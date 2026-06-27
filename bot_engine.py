import json
import time
import datetime
import threading
from config import (
    TRADE_MODE, BUDGET, STOP_LOSS_PCT, MIN_NET_PROFIT_PCT, FEE_RATE, MIN_GAINER_PCT,
    BLOCKED_ASSETS, ENABLED_QUOTES, STABLE_QUOTES,
    COINBASE_ONE, COINBASE_ONE_MONTHLY_COST, FREE_FEE_ALLOWANCE, COINBASE_ONE_TRIAL_END,
    DUST_CLEANUP_ENABLED, DUST_MAX_EUR, DUST_MIN_SELLABLE_EUR,
    NEW_LISTING_ENABLED, NEW_LISTING_TRADE_EUR, NEW_LISTING_MAX_AGE_CYCLES,
    FMP_ENABLED,
)
from coinbase_executor import CoinbaseExecutor
from fmp_radar import FmpRadar
import database

COOLDOWN_MINUTES = 30
CIRCUIT_BREAKER_MINUTES = 30
MAX_OPEN_POSITIONS = 10
MIN_ORDER = 5.0  # taglio minimo per ordine (in valuta quote)


class TradingBot:
    def __init__(self):
        self.executor = CoinbaseExecutor()
        self.fmp = FmpRadar()
        self.running = False
        self.logs = []
        self.lock = threading.RLock()

        database.init_db()
        self.open_positions = database.load_trades()
        self.sold_coins = {}
        self.blocked_assets = BLOCKED_ASSETS

        # Balance multi-quote e tassi di conversione in EUR (per reporting)
        self.balances = {q: 0.0 for q in set(ENABLED_QUOTES) | STABLE_QUOTES | {"EUR"}}
        self.eur_rates = {q: (1.0 if q == "EUR" else 0.92) for q in self.balances}

        # New listing tracking
        self.known_markets = set()
        self.new_listings = {}  # symbol -> cicli rimanenti come "nuova"

        self._load_state()
        self._init_balances()

    # ------------------------------------------------------------------
    # Stato persistente
    # ------------------------------------------------------------------
    def _load_state(self):
        def _f(key, default):
            try:
                return float(database.get_meta(key, default))
            except (TypeError, ValueError):
                return float(default)

        self.consecutive_losses = int(_f('consecutive_losses', 0))
        cb = database.get_meta('circuit_breaker_until', '')
        try:
            self.circuit_breaker_until = float(cb) if cb else None
        except (TypeError, ValueError):
            self.circuit_breaker_until = None
        self.total_profit = _f('total_profit', 0.0)
        self.total_fees = _f('total_fees', 0.0)
        self.win_rate = {'wins': int(_f('wins', 0)), 'losses': int(_f('losses', 0))}

        # Fee auto-calibration
        self.measured_fee_total = _f('measured_fee_total', 0.0)
        self.measured_notional_total = _f('measured_notional_total', 0.0)

        # Volume mensile Coinbase One
        self.monthly_volume = _f('monthly_volume', 0.0)
        self.volume_month = database.get_meta('volume_month', '') or self._month_key()

        # Known markets (per new-listing detection)
        try:
            self.known_markets = database.load_known_markets()
        except Exception:
            self.known_markets = set()

    def _persist_state(self):
        database.set_meta('consecutive_losses', self.consecutive_losses)
        database.set_meta('circuit_breaker_until', self.circuit_breaker_until or '')
        database.set_meta('total_profit', self.total_profit)
        database.set_meta('total_fees', self.total_fees)
        database.set_meta('wins', self.win_rate['wins'])
        database.set_meta('losses', self.win_rate['losses'])
        database.set_meta('measured_fee_total', self.measured_fee_total)
        database.set_meta('measured_notional_total', self.measured_notional_total)
        database.set_meta('monthly_volume', self.monthly_volume)
        database.set_meta('volume_month', self.volume_month)

    @staticmethod
    def _month_key():
        return datetime.datetime.now().strftime('%Y-%m')

    # ------------------------------------------------------------------
    # Fee / volume helpers
    # ------------------------------------------------------------------
    def effective_fee_rate(self):
        """Fee per lato MISURATA dai fill reali; fallback al valore di config."""
        if self.measured_notional_total > 0:
            return self.measured_fee_total / self.measured_notional_total
        return FEE_RATE

    def breakeven_pct(self):
        """Guadagno lordo minimo per uscire in profitto netto (dinamico)."""
        return 2 * self.effective_fee_rate() + MIN_NET_PROFIT_PCT

    def _record_fill(self, notional_eur, fee_eur):
        """Aggiorna fee misurata e volume mensile (entrambi in EUR-equiv)."""
        self._roll_month()
        self.measured_fee_total += fee_eur
        self.measured_notional_total += notional_eur
        self.monthly_volume += notional_eur

    def _roll_month(self):
        mk = self._month_key()
        if mk != self.volume_month:
            self.volume_month = mk
            self.monthly_volume = 0.0

    def free_allowance_left(self):
        self._roll_month()
        if not COINBASE_ONE:
            return 0.0
        return max(FREE_FEE_ALLOWANCE - self.monthly_volume, 0.0)

    # ------------------------------------------------------------------
    # Balance multi-quote
    # ------------------------------------------------------------------
    def _init_balances(self):
        if TRADE_MODE != "LIVE":
            self.balances = {q: 0.0 for q in self.balances}
            self.balances["EUR"] = BUDGET
            self.balances["USDC"] = BUDGET
            return
        self._refresh_balances()

    def _refresh_balances(self):
        if TRADE_MODE != "LIVE":
            return
        try:
            free = self.executor.exchange.fetch_balance().get('free', {})
            for q in self.balances:
                self.balances[q] = float(free.get(q, 0) or 0)
        except Exception as e:
            self.log_msg(f"Errore refresh balances: {e}")

    def _update_eur_rates(self, tickers):
        rate = None
        for sym in ('USDC/EUR', 'USDT/EUR'):
            t = tickers.get(sym)
            if t and t.get('last'):
                rate = t['last']
                break
        if rate is None:
            t = tickers.get('EUR/USDC') or tickers.get('EUR/USD')
            if t and t.get('last'):
                rate = 1.0 / t['last']
        if rate:
            for q in ('USDC', 'USDT', 'USD', 'DAI'):
                if q in self.eur_rates:
                    self.eur_rates[q] = rate

    def _to_eur(self, amount, quote):
        return amount * self.eur_rates.get(quote, 0.92 if quote != "EUR" else 1.0)

    def total_balance_eur(self):
        return sum(self._to_eur(v, q) for q, v in self.balances.items())

    def log_msg(self, msg):
        timestamp = time.strftime('%H:%M:%S')
        full_msg = f"[{timestamp}] {msg}"
        print(full_msg, flush=True)
        with self.lock:
            self.logs.insert(0, full_msg)
            if len(self.logs) > 100:
                self.logs.pop()

    # ------------------------------------------------------------------
    # Snapshot dashboard
    # ------------------------------------------------------------------
    def get_status_snapshot(self):
        with self.lock:
            cb_active = bool(self.circuit_breaker_until and time.time() < self.circuit_breaker_until)
            cb_minutes = int((self.circuit_breaker_until - time.time()) / 60) if cb_active else 0
            eff_fee = self.effective_fee_rate()
            net_profit_after_sub = self.total_profit - (COINBASE_ONE_MONTHLY_COST if COINBASE_ONE else 0)
            trial_days_left = None
            try:
                end = datetime.datetime.strptime(COINBASE_ONE_TRIAL_END, "%Y-%m-%d").date()
                trial_days_left = (end - datetime.date.today()).days
            except Exception:
                trial_days_left = None
            return {
                "running": self.running,
                "mode": TRADE_MODE,
                "balance": round(self.total_balance_eur(), 2),
                "balances": {q: round(v, 4) for q, v in self.balances.items() if v > 0.0001},
                "total_profit": round(self.total_profit, 2),
                "net_after_subscription": round(net_profit_after_sub, 2),
                "total_fees": round(self.total_fees, 2),
                "win_rate": dict(self.win_rate),
                "fee_rate_pct": round(eff_fee * 100, 4),
                "fee_measured": self.measured_notional_total > 0,
                "breakeven_pct": round(self.breakeven_pct() * 100, 4),
                "coinbase_one": COINBASE_ONE,
                "monthly_volume": round(self.monthly_volume, 2),
                "free_allowance_left": round(self.free_allowance_left(), 2),
                "subscription_cost": COINBASE_ONE_MONTHLY_COST if COINBASE_ONE else 0,
                "trial_end": COINBASE_ONE_TRIAL_END,
                "trial_days_left": trial_days_left,
                "enabled_quotes": ENABLED_QUOTES,
                "new_listings": list(self.new_listings.keys()),
                "circuit_breaker_active": cb_active,
                "circuit_breaker_minutes": cb_minutes,
                "open_positions": {k: dict(v) for k, v in self.open_positions.items()},
                "logs": list(self.logs[:20]),
            }

    # ------------------------------------------------------------------
    # Dust cleanup
    # ------------------------------------------------------------------
    def cleanup_dust(self):
        if not DUST_CLEANUP_ENABLED or TRADE_MODE != "LIVE":
            return
        self.log_msg("[DUST] Pulizia portafoglio in corso...")
        try:
            bal = self.executor.exchange.fetch_balance()
            tickers = self.executor.exchange.fetch_tickers()
            self._update_eur_rates(tickers)
            held = {sym.split('/')[0] for sym in self.open_positions}
            free = bal.get('free', {})
            recovered = 0.0
            for asset, amount in free.items():
                if not amount or amount <= 0:
                    continue
                if asset in self.blocked_assets or asset in held or asset in STABLE_QUOTES:
                    continue
                market, value_eur = self._best_market_value(asset, amount, tickers)
                if market is None or value_eur <= 0:
                    continue
                if value_eur >= DUST_MAX_EUR:
                    continue  # non e' dust, lascia stare
                if value_eur < DUST_MIN_SELLABLE_EUR:
                    self.log_msg(f"[DUST] {asset} (€{value_eur:.2f}) sotto il minimo vendibile: bloccato, salto.")
                    continue
                self.log_msg(f"[DUST] Vendo {asset} su {market} (~€{value_eur:.2f})...")
                fill = self.executor.execute_market_sell(market, amount, ref_price=tickers[market].get('last'))
                if fill:
                    recovered += value_eur
                    time.sleep(1)
            if recovered > 0:
                self._refresh_balances()
                self.log_msg(f"[DUST] Recuperati ~€{recovered:.2f} di liquidita'.")
            else:
                self.log_msg("[DUST] Nessun dust vendibile trovato.")
        except Exception as e:
            self.log_msg(f"[DUST] Errore pulizia: {e}")

    def _best_market_value(self, asset, amount, tickers):
        """Trova il miglior mercato per vendere un asset e il suo valore in EUR."""
        for q in ('EUR',) + tuple(q for q in ENABLED_QUOTES if q != 'EUR') + ('USDC', 'USD', 'USDT'):
            sym = f"{asset}/{q}"
            t = tickers.get(sym)
            if t and t.get('last'):
                return sym, self._to_eur(amount * t['last'], q)
        return None, 0.0

    # ------------------------------------------------------------------
    # New listing detection
    # ------------------------------------------------------------------
    def _detect_new_listings(self, current_symbols):
        current = set(current_symbols)
        if not self.known_markets:
            self.known_markets = current
            database.save_known_markets(current)
            return
        new = current - self.known_markets
        if new:
            self.known_markets |= new
            database.save_known_markets(new)
            for s in new:
                self.new_listings[s] = NEW_LISTING_MAX_AGE_CYCLES
                self.log_msg(f"[NEW LISTING] Rilevato nuovo mercato: {s}")
        # invecchia i flag
        for s in list(self.new_listings):
            self.new_listings[s] -= 1
            if self.new_listings[s] <= 0 or s not in current:
                self.new_listings.pop(s, None)

    # ------------------------------------------------------------------
    # Chiusura verificata (fee-aware, multi-quote)
    # ------------------------------------------------------------------
    def _close_position(self, sym, reason):
        with self.lock:
            pos = self.open_positions.get(sym)
        if pos is None:
            return False
        quote = pos.get('quote', 'EUR')

        fill = self.executor.execute_market_sell(sym, pos["amount_base"], ref_price=pos.get('current_price'))
        if fill is None:
            self.log_msg(f"[ERRORE] Vendita {sym} fallita. Posizione mantenuta.")
            return False

        proceeds = fill['proceeds_eur']      # in valuta quote
        exit_fee = fill['fee_eur']           # in valuta quote
        
        amount_quote = pos.get('amount_quote', pos.get('amount_eur', 0.0))
        net_pnl_q = proceeds - amount_quote
        net_pnl_eur = self._to_eur(net_pnl_q, quote)
        
        entry_fee = pos.get('entry_fee_eur', 0.0)
        trade_fee_eur = self._to_eur(exit_fee + entry_fee, quote)

        with self.lock:
            self.open_positions.pop(sym, None)
            self.balances[quote] = self.balances.get(quote, 0.0) + proceeds
            self.total_profit += net_pnl_eur
            self.total_fees += trade_fee_eur
            self._record_fill(self._to_eur(proceeds, quote), self._to_eur(exit_fee, quote))

            if net_pnl_eur >= 0:
                self.win_rate['wins'] += 1
                self.consecutive_losses = 0
                tag = "PROFITTO"
            else:
                self.win_rate['losses'] += 1
                self.consecutive_losses += 1
                self.sold_coins[sym] = datetime.datetime.now()
                tag = "PERDITA"
                if self.consecutive_losses >= 3:
                    self.circuit_breaker_until = time.time() + (CIRCUIT_BREAKER_MINUTES * 60)
                    self.log_msg("[CIRCUIT BREAKER] 3 perdite consecutive. Pausa di emergenza attivata.")
                    database.log_circuit_breaker(f"3 perdite consecutive. Stop attivato per {sym}", self.consecutive_losses)
            self._persist_state()

        database.archive_trade(
            symbol=sym,
            entry_price=pos.get('entry_price', 0),
            exit_price=fill.get('avg_price', pos.get('current_price', 0)),
            highest_price=pos.get('highest_price'),
            amount_base=pos['amount_base'],
            amount_quote=amount_quote,
            pnl_eur=net_pnl_eur,
            entry_fee=entry_fee,
            exit_fee=exit_fee,
            fee_currency=quote,
            quote=quote,
            close_reason=reason,
            opened_at=pos.get('opened_at', pos.get('time', ''))
        )
        self.log_msg(f"[{reason}] {sym} chiusa -> {tag} NETTO €{net_pnl_eur:+.2f} "
                     f"(fee €{trade_fee_eur:.2f}). Tot EUR: €{self.total_balance_eur():.2f}")
        return True

    # ------------------------------------------------------------------
    # Ciclo principale
    # ------------------------------------------------------------------
    def loop(self):
        self.log_msg(f"Avvio Bot in Modalita': {TRADE_MODE} | Quote: {','.join(ENABLED_QUOTES)}")
        self.log_msg(f"Fee misurata/lato: {self.effective_fee_rate()*100:.3f}% | "
                     f"Breakeven: {self.breakeven_pct()*100:.3f}% | "
                     f"Coinbase One: {'ON' if COINBASE_ONE else 'OFF'} "
                     f"(franchigia residua €{self.free_allowance_left():.0f})")
        self.log_msg(f"Asset Protetti: {', '.join(self.blocked_assets)}")

        self.cleanup_dust()
        self.sync_portfolio()

        while self.running:
            try:
                if self.circuit_breaker_until and time.time() < self.circuit_breaker_until:
                    minutes_left = int((self.circuit_breaker_until - time.time()) / 60)
                    self.log_msg(f"[CIRCUIT BREAKER] In pausa. Ripresa tra {minutes_left} min.")
                    self._wait(60)
                    continue
                elif self.circuit_breaker_until and time.time() >= self.circuit_breaker_until:
                    self.log_msg("[CIRCUIT BREAKER] Pausa terminata. Ripresa operativita'.")
                    with self.lock:
                        self.circuit_breaker_until = None
                        self.consecutive_losses = 0
                        self._persist_state()

                self.log_msg("Scansione Mercato in corso...")
                try:
                    tickers = self.executor.exchange.fetch_tickers()
                except Exception as e:
                    self.log_msg(f"Errore connessione Coinbase: {e}")
                    self._wait(10)
                    continue

                self._update_eur_rates(tickers)
                self._refresh_balances()
                self._detect_new_listings(tickers.keys())

                gainers = self._scan_gainers(tickers)
                if not gainers:
                    self.log_msg(f"Nessun gainer (>+{MIN_GAINER_PCT}%) sui quote {','.join(ENABLED_QUOTES)}. Attesa...")
                else:
                    self.log_msg(f"Trovati {len(gainers)} candidati. Valutazione...")

                self._manage_open_positions(tickers)

                with self.lock:
                    now = datetime.datetime.now()
                    self.sold_coins = {k: v for k, v in self.sold_coins.items()
                                       if (now - v).total_seconds() < COOLDOWN_MINUTES * 60}

                self._manage_new_entries(gainers)

            except Exception as e:
                self.log_msg(f"Errore nel ciclo principale: {e}")

            self._wait(60)

    def _scan_gainers(self, tickers):
        gainers = []
        for sym, ticker in tickers.items():
            if '/' not in sym:
                continue
            base, quote = sym.split('/', 1)
            is_new = sym in self.new_listings and NEW_LISTING_ENABLED
            if quote not in ENABLED_QUOTES and not is_new:
                continue
            price = ticker.get('last', 0)
            pct_change = ticker.get('percentage', 0)
            volume = ticker.get('baseVolume', 0)
            if not price:
                continue
            # Le nuove listing entrano anche senza storico/momentum consolidato
            if is_new:
                gainers.append({"symbol": sym, "base": base, "quote": quote, "price": price,
                                "changesPercentage": pct_change or 0, "volume": volume or 0, "is_new": True})
                continue
            if pct_change and volume and volume > 100000 and pct_change >= MIN_GAINER_PCT:
                gainers.append({"symbol": sym, "base": base, "quote": quote, "price": price,
                                "changesPercentage": pct_change, "volume": volume, "is_new": False})
        # nuove listing in cima, poi per momentum
        gainers.sort(key=lambda x: (x["is_new"], x["changesPercentage"]), reverse=True)
        return gainers[:15]  # Limitiamo ai top 15 per evitare rate-limit su fetch_ohlcv

    def _wait(self, seconds):
        for _ in range(seconds):
            if not self.running:
                break
            time.sleep(1)

    def sync_portfolio(self):
        if TRADE_MODE != "LIVE":
            return
        self.log_msg("Sincronizzazione Portafoglio...")
        try:
            balances = self.executor.exchange.fetch_balance()
            tickers = self.executor.exchange.fetch_tickers()
            self._update_eur_rates(tickers)
            for asset, amount in balances.get('total', {}).items():
                if amount <= 0 or asset in self.blocked_assets or asset in STABLE_QUOTES:
                    continue
                market, value_eur = self._best_market_value(asset, amount, tickers)
                if market is None or market in self.open_positions or value_eur < DUST_MAX_EUR:
                    continue  # il dust sotto soglia e' gestito da cleanup_dust
                quote = market.split('/')[1]
                price = tickers[market].get('last', 0)
                if not price:
                    continue
                self.log_msg(f"[SYNC] Adottato {amount} {asset} su {market} (~€{value_eur:.2f}). Solo uscite in profitto.")
                trade_data = {
                    "entry_price": price, "current_price": price, "highest_price": price,
                    "amount_base": amount, "amount_eur": amount * price, "entry_fee_eur": 0.0,
                    "quote": quote, "adopted": True, "new_listing": False,
                    "pnl_pct": 0.0, "time": time.strftime('%H:%M:%S'),
                }
                with self.lock:
                    self.open_positions[market] = trade_data
                database.save_trade(market, trade_data)
        except Exception as e:
            self.log_msg(f"Errore sincronizzazione: {e}")

    def _manage_open_positions(self, tickers):
        with self.lock:
            symbols = list(self.open_positions.keys())

        to_close = []
        for sym in symbols:
            with self.lock:
                pos = self.open_positions.get(sym)
            if pos is None or sym not in tickers:
                continue
            current_price = tickers[sym].get('last', 0)
            if not current_price:
                continue

            entry_price = pos['entry_price']
            highest_price = pos.get('highest_price', entry_price)
            adopted = pos.get('adopted', False)

            if current_price > highest_price:
                highest_price = current_price
                pos['highest_price'] = highest_price
                database.save_trade(sym, pos)

            pnl_pct = (current_price - entry_price) / entry_price
            pos['current_price'] = current_price
            pos['pnl_pct'] = pnl_pct

            sma = self.executor.get_sma(sym)  # puo' essere None (es. nuove listing)
            trailing_stop_price = highest_price * (1 - STOP_LOSS_PCT)

            if current_price < trailing_stop_price:
                if pnl_pct > 0:
                    to_close.append((sym, "TRAILING STOP"))
                elif adopted:
                    self.log_msg(f"  [HOLD-ADOPTED] {sym} sotto trailing, attendo profitto")
                else:
                    to_close.append((sym, "STOP LOSS"))
            elif sma is not None and current_price < sma and pnl_pct > self.breakeven_pct():
                to_close.append((sym, "TREND REVERSAL"))
            else:
                self.log_msg(f"  [HOLD] {sym} | PnL: {pnl_pct*100:.2f}% | Max: {highest_price:.4f} | Stop: {trailing_stop_price:.4f}")

        for sym, reason in to_close:
            self._close_position(sym, reason)

    def _manage_new_entries(self, gainers):
        with self.lock:
            open_count = len(self.open_positions)
        if not gainers or open_count >= MAX_OPEN_POSITIONS:
            return

        for gainer in gainers:
            symbol = gainer['symbol']
            base = gainer['base']
            quote = gainer['quote']
            price = gainer['price']
            is_new = gainer.get('is_new', False)

            if base in self.blocked_assets:
                continue
            with self.lock:
                if symbol in self.sold_coins or symbol in self.open_positions:
                    continue
                if len(self.open_positions) >= MAX_OPEN_POSITIONS:
                    break

            avail = self.balances.get(quote, 0.0)

            if is_new:
                # Nuove listing: size piccola fissa, niente requisito SMA/indicatori (no storico)
                trade_amount = min(NEW_LISTING_TRADE_EUR, avail)
                if avail < MIN_ORDER:
                    continue
                if not self._fmp_ok(base):
                    self.log_msg(f"[FILTRO] {symbol} (nuova) scartata: sentiment FMP negativo")
                    continue
                self.log_msg(f"[NEW LISTING] {symbol} acquisto speculativo €{trade_amount:.2f} ({quote})...")
                if self._execute_entry(symbol, base, quote, price, trade_amount, is_new=True):
                    break
                continue

            # 1. Verifica preliminare liquidita' (risparmia chiamate API e previene rate-limit)
            base_amount = max(MIN_ORDER, avail * 0.05)
            multiplier = 1.0
            if gainer.get('changesPercentage', 0) > 15.0:
                multiplier += 0.5
            if gainer.get('volume', 0) > 20000000:
                multiplier += 0.5
            trade_amount = min(base_amount * multiplier, max(MIN_ORDER, avail * 0.15))

            if avail < trade_amount or avail < MIN_ORDER:
                # Nessun log per evitare spam inutile per monete senza fondi sufficienti
                continue

            # 2. Candidato normale: SMA + RSI/MACD + FMP (solo se ci sono fondi)
            sma = self.executor.get_sma(symbol)
            if sma is None or price <= sma:
                sma_val = f"{sma:.4f}" if sma is not None else "N/A"
                self.log_msg(f"[FILTRO] {symbol} scartato: prezzo (${price:.4f}) sotto SMA ({sma_val})")
                continue

            rsi, macd_bullish = self.executor.get_indicators(symbol)
            if rsi is None:
                continue
            if rsi >= 70:
                self.log_msg(f"[FILTRO] {symbol} scartato: RSI alto ({rsi:.1f})")
                continue
            if not macd_bullish:
                self.log_msg(f"[FILTRO] {symbol} scartato: MACD non rialzista")
                continue
            if not self._fmp_ok(base):
                self.log_msg(f"[FILTRO] {symbol} scartato: sentiment FMP negativo")
                continue

            self.log_msg(f"[SEGNALE] {symbol} (SMA ok, RSI {rsi:.1f}, MACD ok). Acquisto €{trade_amount:.2f} ({quote})...")
            if self._execute_entry(symbol, base, quote, price, trade_amount, is_new=False):
                break

    def _fmp_ok(self, base):
        """Soft filter FMP: blocca solo se il sentiment news e' chiaramente negativo."""
        if not FMP_ENABLED:
            return True
        try:
            sig = self.fmp.get_signal(base)
            sent = sig.get('news_sentiment')
            if sent is not None and sent < -0.5:
                return False
        except Exception:
            pass
        return True

    def _execute_entry(self, symbol, base, quote, price, trade_amount, is_new):
        fill = self.executor.execute_market_buy(symbol, trade_amount, ref_price=price)
        if fill is None:
            self.log_msg(f"[ERRORE] Ordine fallito per {symbol}.")
            return False
        filled_base = fill['filled_base']
        cost = fill['cost_eur']        # in valuta quote
        entry_fee = fill['fee_eur']
        entry_price = fill['avg_price'] or price
        if not filled_base or not entry_price:
            self.log_msg(f"[ERRORE] Fill non valido per {symbol}.")
            return False

        trade_data = {
            "entry_price": entry_price, "current_price": entry_price, "highest_price": entry_price,
            "amount_base": filled_base, "amount_eur": cost, "amount_quote": cost, "amount_eur_equiv": self._to_eur(cost, quote),
            "entry_fee_eur": entry_fee, "fee_currency": quote,
            "quote": quote, "adopted": False, "new_listing": is_new,
            "pnl_pct": 0.0, "time": time.strftime('%H:%M:%S'), "opened_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        with self.lock:
            self.open_positions[symbol] = trade_data
            self.balances[quote] = self.balances.get(quote, 0.0) - cost
            self._record_fill(self._to_eur(cost, quote), self._to_eur(entry_fee, quote))
            self.total_fees += self._to_eur(entry_fee, quote)
            self._persist_state()
        database.save_trade(symbol, trade_data)
        self.log_msg(f"Comprato {symbol}: {filled_base:.6f} @ {entry_price:.4f} {quote} (fee {entry_fee:.4f})")
        return True

    def start(self):
        if hasattr(self, 'thread') and self.thread.is_alive():
            return
        self.running = True
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.log_msg("Spegnimento bot richiesto...")


bot_instance = TradingBot()
