"""Motore di Unibot: un solo ciclo gestisce crypto e azioni USA in paper trading.

Ordine di ogni ciclo: aggiorna i dati -> gestisci le uscite -> valuta gli ingressi.
Le uscite vengono sempre prima degli ingressi e non sono mai bloccate da
circuit breaker o limiti giornalieri (quelli bloccano solo i nuovi ingressi).
"""
import collections
import datetime
import threading
import time

from . import settings as S
from .indicators import sma
from .models import Position
from .paper import PaperBroker
from .strategy import crypto_entry, stock_entry, exit_reason
from .universe import market_state


def _utc_day(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")


class Bot:
    def __init__(self, store, crypto=None, stocks=None, intel=None, signals=None,
                 broker=None, clock=time.time, cfg=S, log=print):
        self.cfg = cfg
        self.store = store
        self.crypto = crypto
        self.stocks = stocks
        self.intel = intel
        self.signals = signals
        self.broker = broker or PaperBroker(cfg)
        self.clock = clock
        self._log_fn = log
        self.lock = threading.RLock()
        self.logs = []
        self.rejects = collections.Counter(store.get_meta("rejects", {}) or {})
        self.fx = {"EUR": 1.0, "USDC": 0.86, "USD": 0.86}
        self.positions = {p.symbol: p for p in store.load_positions()}
        self.wallets = store.get_meta("wallets") or self._initial_wallets()
        self.consecutive_losses = int(store.get_meta("consecutive_losses", 0) or 0)
        self.circuit_until = float(store.get_meta("circuit_until", 0) or 0)
        self.cooldown = {}
        self.day_key = store.get_meta("day_key", "")
        self.day_pnl = float(store.get_meta("day_pnl", 0.0) or 0.0)
        self.day_start_equity = float(store.get_meta("day_start_equity", 0.0) or 0.0)
        self._last_stock_scan = 0.0
        self._last_stock_check = 0.0
        self.last_regime = {"block": False, "mult": 1.0, "label": "n/d", "fg": None}
        self.last_stock_ctx = {}
        self.stop_event = threading.Event()

    # ------------------------------------------------------------------
    def _initial_wallets(self):
        c = self.cfg
        return {"EUR": c.CRYPTO_START_EUR if c.ENABLE_CRYPTO else 0.0,
                "USDC": c.CRYPTO_START_USDC if c.ENABLE_CRYPTO else 0.0,
                "USD": c.STOCK_START_USD if c.ENABLE_STOCKS else 0.0}

    def log(self, msg):
        line = "[%s] %s" % (time.strftime("%H:%M:%S", time.localtime(self.clock())), msg)
        self.logs.insert(0, line)
        del self.logs[200:]
        try:
            self._log_fn(line)
        except Exception:
            pass

    def _equity_in(self, quote):
        total = self.wallets.get(quote, 0.0)
        for p in self.positions.values():
            if p.quote == quote:
                total += p.qty * (p.last_price or p.entry_price)
        return total

    def equity_eur(self):
        return sum(self._equity_in(q) * self.fx.get(q, 1.0) for q in self.wallets)

    # ------------------------------------------------------------------
    # Ciclo
    # ------------------------------------------------------------------
    def cycle(self):
        with self.lock:
            now = self.clock()
            self._roll_day(now)
            summary = {"ts": now, "entries": 0, "exits": 0}
            if self.cfg.ENABLE_CRYPTO and self.crypto is not None:
                self._cycle_crypto(now, summary)
            if self.cfg.ENABLE_STOCKS and self.stocks is not None:
                self._cycle_stocks(now, summary)
            self._persist()
            return summary

    def _roll_day(self, now):
        key = _utc_day(now)
        if key != self.day_key:
            self.day_key, self.day_pnl = key, 0.0
            self.day_start_equity = self.equity_eur()

    def _entries_blocked(self, now):
        if now < self.circuit_until:
            return "circuit_breaker"
        limit = self.cfg.DAILY_LOSS_LIMIT_PCT * self.day_start_equity
        if self.day_start_equity > 0 and self.day_pnl <= -limit:
            return "limite_giornaliero"
        return None

    def _crypto_regime(self):
        """Regime di mercato crypto. Se la fonte e' momentaneamente irraggiungibile si usa
        l'ultimo valore valido (cache); senza nessun dato il bot NON entra (fail-closed):
        operare alla cieca nel panico e' proprio cio' che ha prodotto 1 vinto su 35."""
        cfg = self.cfg
        if self.intel is None:
            return {"block": False, "mult": 1.0, "label": "no_intel", "fg": None}
        try:
            r = self.intel.get_regime()
        except Exception:
            r = {"available": False}
        now = self.clock()
        fg = r.get("fear_greed")
        if r.get("available") and fg is not None:
            reg = {"block": fg <= cfg.PANIC_BLOCK_BELOW, "mult": float(r.get("risk_multiplier", 1.0)),
                   "label": r.get("regime", ""), "fg": fg}
            self.store.set_meta("last_regime", {"fg": fg, "mult": reg["mult"], "label": reg["label"], "ts": now})
            return reg
        cached = self.store.get_meta("last_regime")
        if cached and now - cached.get("ts", 0) < cfg.REGIME_CACHE_HOURS * 3600:
            return {"block": cached["fg"] <= cfg.PANIC_BLOCK_BELOW, "mult": cached["mult"],
                    "label": "%s (cache)" % cached["label"], "fg": cached["fg"]}
        return {"block": bool(cfg.REGIME_UNKNOWN_BLOCK), "mult": cfg.REGIME_UNKNOWN_MULT,
                "label": "unknown", "fg": None}

    def _cycle_crypto(self, now, summary):
        try:
            cands = self.crypto.scan()
        except Exception as e:
            self.log("crypto: errore scan (%s)" % e)
            return
        rate = getattr(self.crypto, "usdc_eur", lambda: None)()
        if rate:
            self.fx["USDC"] = self.fx["USD"] = rate
        self._manage("crypto", now, summary, None)
        regime = self._crypto_regime()
        self.last_regime = regime
        self._enter("crypto", cands, now, summary, regime, None)

    def _cycle_stocks(self, now, summary):
        cfg = self.cfg
        ms = market_state(now)
        do_scan = ms["open"] and now - self._last_stock_scan >= cfg.STOCK_SCAN_SECONDS
        cands = None
        if do_scan:
            try:
                cands = self.stocks.scan()
            except Exception as e:
                self.log("azioni: errore scan (%s)" % e)
            self._last_stock_scan = now
        ctx = self.stocks.context(now)
        self.last_stock_ctx = ctx
        if ctx["open"] and (do_scan or now - self._last_stock_check >= cfg.STOCK_POS_CHECK_SECONDS):
            self._manage("stock", now, summary, ctx)
            self._last_stock_check = now
        if cands is not None:
            self._enter("stock", cands, now, summary, None, ctx)

    # ------------------------------------------------------------------
    # Uscite
    # ------------------------------------------------------------------
    def _manage(self, cls, now, summary, ctx):
        cfg = self.cfg
        uni = self.crypto if cls == "crypto" else self.stocks
        for pos in [p for p in self.positions.values() if p.asset_class == cls]:
            price = uni.price(pos.symbol)
            if not price:
                self.log("%s: prezzo non disponibile, salto" % pos.symbol)
                continue
            pos.last_price = float(price)
            pos.highest = max(pos.highest, float(price))
            flatten = False
            if cls == "crypto":
                trail = cfg.CRYPTO_TRAIL_PCT
                max_hold = cfg.CRYPTO_MAX_HOLD_HOURS * 3600
                closes = uni.closes(pos.symbol)
                sma_val = sma(closes, 10) if closes else None
                breakeven = 2 * (cfg.CRYPTO_FEE_RATE + cfg.CRYPTO_SLIPPAGE_BPS / 1e4) + cfg.MIN_NET_PROFIT_PCT
            else:
                trail, max_hold = cfg.STOCK_TRAIL_PCT, 0
                tape = uni.tape_prices(pos.symbol)
                sma_val = sum(tape[-5:]) / len(tape[-5:]) if len(tape) >= 5 else None
                slip = (cfg.STOCK_PENNY_SLIPPAGE_BPS if pos.entry_price < cfg.STOCK_PENNY_PRICE
                        else cfg.STOCK_SLIPPAGE_BPS) / 1e4
                breakeven = 2 * slip + cfg.MIN_NET_PROFIT_PCT
                flatten = bool(ctx and (ctx["flatten"] or (ctx["open"] and pos.opened_day != ctx["day"])))
            reason = exit_reason(pos.entry_price, pos.highest, pos.last_price, now - pos.opened_ts,
                                 trail, max_hold, sma_val, breakeven, flatten)
            if reason:
                self._close(pos, pos.last_price, reason, now)
                summary["exits"] += 1
            else:
                self.store.save_position(pos)

    def _close(self, pos, price, reason, now):
        fill = self.broker.sell(pos.asset_class, price, pos.qty)
        q = pos.quote
        self.wallets[q] = self.wallets.get(q, 0.0) + fill["proceeds"]
        pnl_q = fill["proceeds"] - pos.cost
        fx = self.fx.get(q, 1.0)
        pnl_eur = pnl_q * fx
        fees_eur = (pos.entry_fee + fill["fee"]) * fx
        trade = {"symbol": pos.symbol, "asset_class": pos.asset_class, "quote": q,
                 "entry_price": pos.entry_price, "exit_price": fill["avg_price"], "qty": pos.qty,
                 "cost": pos.cost, "proceeds": fill["proceeds"], "pnl_quote": pnl_q,
                 "pnl_eur": pnl_eur, "fees_eur": fees_eur, "reason": reason, "score": pos.score,
                 "regime": pos.regime, "signal": pos.signal, "opened_ts": pos.opened_ts, "closed_ts": now}
        self.store.add_trade(trade)
        self.store.delete_position(pos.symbol)
        self.positions.pop(pos.symbol, None)
        self._mirror(trade, pos, fill)
        self.day_pnl += pnl_eur
        if pnl_eur < 0:
            self.consecutive_losses += 1
            self.cooldown[pos.symbol] = now + self.cfg.COOLDOWN_MINUTES * 60
            if self.consecutive_losses >= self.cfg.CIRCUIT_LOSSES:
                self.circuit_until = now + self.cfg.CIRCUIT_MINUTES * 60
                self.log("CIRCUIT BREAKER: %d perdite di fila, ingressi sospesi %d min"
                         % (self.consecutive_losses, self.cfg.CIRCUIT_MINUTES))
        else:
            self.consecutive_losses = 0
        self.log("%s chiusa [%s]: netto %+.2f EUR (fee %.2f EUR)" % (pos.symbol, reason, pnl_eur, fees_eur))

    def _mirror(self, trade, pos, fill):
        if not self.cfg.CLOUD_MIRROR:
            return
        try:
            import cloud_store
            cloud_store.archive_trade({
                "symbol": pos.symbol, "entry_price": pos.entry_price, "exit_price": fill["avg_price"],
                "pnl_eur": trade["pnl_eur"], "entry_fee": pos.entry_fee, "exit_fee": fill["fee"],
                "fee_currency": pos.quote, "quote": pos.quote, "close_reason": trade["reason"],
                "opened_at": str(pos.opened_ts), "source": "unibot-" + pos.asset_class})
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Ingressi
    # ------------------------------------------------------------------
    def _enter(self, cls, cands, now, summary, regime, ctx):
        cfg = self.cfg
        blocked = self._entries_blocked(now)
        if blocked:
            self.rejects[blocked] += len(cands)
            return
        max_pos = cfg.CRYPTO_MAX_POSITIONS if cls == "crypto" else cfg.STOCK_MAX_POSITIONS
        if sum(1 for p in self.positions.values() if p.asset_class == cls) >= max_pos:
            self.rejects["max_posizioni"] += len(cands)
            return
        if cls == "crypto" and regime["block"]:
            self.rejects["regime_sconosciuto" if regime["label"] == "unknown" else "regime_panico"] += len(cands)
            return
        if cls == "stock" and not ctx["can_enter"]:
            self.rejects["finestra:" + ",".join(ctx["reasons"])] += len(cands)
            return

        ok = []
        for c in cands:
            if c.symbol in self.positions:
                continue
            if now < self.cooldown.get(c.symbol, 0):
                self.rejects["cooldown"] += 1
                continue
            sig = self.signals.lookup(c.symbol) if self.signals else None
            if sig and sig["side"] == "avoid":
                self.rejects["segnale_avoid"] += 1
                continue
            if cls == "crypto":
                dec = crypto_entry(c, self.crypto.closes(c.symbol), regime, cfg)
            else:
                dec = stock_entry(c, self.stocks.tape_prices(c.symbol), ctx, cfg)
            if not dec.ok:
                self.rejects[dec.reasons[0]] += 1
                continue
            bonus = cfg.SIGNAL_BONUS if sig else 0.0
            ok.append((dec.score + bonus, c, sig))

        ok.sort(key=lambda t: t[0], reverse=True)
        opened = 0
        for score, c, sig in ok:
            if opened >= cfg.ENTRIES_PER_CYCLE:
                break
            if cls == "stock" and hasattr(self.stocks, "tradable") and not self.stocks.tradable(c.symbol):
                self.rejects["senza_prezzo_fmp"] += 1      # non gestibile dopo l'ingresso: non si apre
                continue
            if self._open(c, score, regime, ctx, sig, now):
                opened += 1
                summary["entries"] += 1

    def _open(self, c, score, regime, ctx, sig, now):
        cfg = self.cfg
        q = c.quote
        free = self.wallets.get(q, 0.0)
        eq = self._equity_in(q)
        mult = regime["mult"] if regime else 1.0
        notional = max(cfg.MIN_ORDER, eq * cfg.POSITION_PCT * mult)
        notional = min(notional, max(cfg.MIN_ORDER, eq * cfg.MAX_POSITION_PCT), free)
        if notional < cfg.MIN_ORDER:
            self.rejects["liquidita_insufficiente"] += 1
            return False
        fill = self.broker.buy(c.asset_class, c.price, notional)
        day = ctx["day"] if ctx else _utc_day(now)
        pos = Position(symbol=c.symbol, asset_class=c.asset_class, quote=q,
                       entry_price=fill["avg_price"], qty=fill["qty"], cost=fill["cost"],
                       entry_fee=fill["fee"], highest=fill["avg_price"], opened_ts=now, opened_day=day,
                       last_price=c.price, score=score,
                       regime=(regime["label"] if regime else "azioni"),
                       signal=(sig["source"] if sig else ""))
        self.wallets[q] = free - fill["cost"]
        self.positions[c.symbol] = pos
        self.store.save_position(pos)
        self.log("APERTA %s (%s) %.2f %s @ %.4f, score %.2f%s" % (
            c.symbol, c.asset_class, notional, q, fill["avg_price"], score,
            (", segnale: " + sig["source"]) if sig else ""))
        return True

    # ------------------------------------------------------------------
    def _persist(self):
        s = self.store
        s.set_meta("wallets", self.wallets)
        s.set_meta("consecutive_losses", self.consecutive_losses)
        s.set_meta("circuit_until", self.circuit_until)
        s.set_meta("day_key", self.day_key)
        s.set_meta("day_pnl", self.day_pnl)
        s.set_meta("day_start_equity", self.day_start_equity)
        s.set_meta("rejects", dict(self.rejects))

    def snapshot(self):
        with self.lock:
            now = self.clock()
            seen = set()
            if self.crypto is not None:
                seen |= set(getattr(self.crypto, "tickers", {}).keys())
            if self.stocks is not None:
                seen |= set(getattr(self.stocks, "prices", {}).keys())
            positions = []
            for p in self.positions.values():
                last = p.last_price or p.entry_price
                positions.append({"symbol": p.symbol, "class": p.asset_class, "quote": p.quote,
                                  "entry": p.entry_price, "last": last,
                                  "pnl_pct": (last / p.entry_price - 1) * 100, "signal": p.signal})
            fmp = getattr(self.stocks, "fmp", None)
            return {
                "equity_eur": self.equity_eur(), "wallets": dict(self.wallets), "positions": positions,
                "stats": self.store.stats(), "rejects": dict(self.rejects),
                "regime": self.last_regime, "stock_ctx": self.last_stock_ctx,
                "circuit_active": now < self.circuit_until, "consecutive_losses": self.consecutive_losses,
                "day_pnl_eur": self.day_pnl,
                "fmp_remaining": fmp.remaining() if fmp else None,
                "signals_active": self.signals.active() if self.signals else [],
                "signals_unmatched": self.signals.unmatched(seen) if self.signals else [],
                "recent": self.store.recent_trades(8), "logs": self.logs[:15],
            }

    def run_forever(self, interval=60):
        self.log("Unibot avviato (PAPER). Crypto: %s | Azioni: %s | intervallo %ds" % (
            self.cfg.ENABLE_CRYPTO, self.cfg.ENABLE_STOCKS, interval))
        while not self.stop_event.is_set():
            try:
                self.cycle()
            except Exception as e:  # un ciclo fallito non ferma il bot
                self.log("errore ciclo: %s" % e)
            self.stop_event.wait(interval)
