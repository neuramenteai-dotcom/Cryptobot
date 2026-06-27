import time
import datetime
import threading
from config import (
    TRADE_MODE, BUDGET, STOP_LOSS_PCT, TAKE_PROFIT_PCT, BLOCKED_ASSETS,
    FEE_RATE, ROUND_TRIP_FEE, BREAKEVEN_PCT, MIN_GAINER_PCT,
)
from coinbase_executor import CoinbaseExecutor
import database

COOLDOWN_MINUTES = 30
CIRCUIT_BREAKER_MINUTES = 30
MAX_OPEN_POSITIONS = 10


class TradingBot:
    def __init__(self):
        self.executor = CoinbaseExecutor()
        self.running = False
        self.logs = []

        # Lock per proteggere lo stato condiviso dal thread del bot e dalla web UI
        self.lock = threading.RLock()

        # Inizializza o carica le posizioni salvate dal Database SQLite
        database.init_db()
        self.open_positions = database.load_trades()
        self.sold_coins = {}

        # Le monete da NON VENDERE MAI (HODL)
        self.blocked_assets = BLOCKED_ASSETS

        # --- Stato persistente (sopravvive ai riavvii del worker) ---
        self._load_state()

        # Calcolo approssimativo per ripristinare il balance operativo
        used_balance = sum(pos['amount_eur'] for pos in self.open_positions.values())
        live_balance = self.executor.get_balance() if TRADE_MODE == "LIVE" else BUDGET
        self.current_balance = max(live_balance - used_balance, 0.0)

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
        self.win_rate = {
            'wins': int(_f('wins', 0)),
            'losses': int(_f('losses', 0)),
        }

    def _persist_state(self):
        database.set_meta('consecutive_losses', self.consecutive_losses)
        database.set_meta('circuit_breaker_until', self.circuit_breaker_until or '')
        database.set_meta('total_profit', self.total_profit)
        database.set_meta('total_fees', self.total_fees)
        database.set_meta('wins', self.win_rate['wins'])
        database.set_meta('losses', self.win_rate['losses'])

    def log_msg(self, msg):
        timestamp = time.strftime('%H:%M:%S')
        full_msg = f"[{timestamp}] {msg}"
        print(full_msg, flush=True)
        with self.lock:
            self.logs.insert(0, full_msg)
            if len(self.logs) > 100:
                self.logs.pop()

    # ------------------------------------------------------------------
    # Snapshot thread-safe per la dashboard
    # ------------------------------------------------------------------
    def get_status_snapshot(self):
        with self.lock:
            cb_active = bool(self.circuit_breaker_until and time.time() < self.circuit_breaker_until)
            cb_minutes = 0
            if cb_active:
                cb_minutes = int((self.circuit_breaker_until - time.time()) / 60)
            return {
                "running": self.running,
                "mode": TRADE_MODE,
                "balance": round(self.current_balance, 2),
                "total_profit": round(self.total_profit, 2),
                "total_fees": round(self.total_fees, 2),
                "win_rate": dict(self.win_rate),
                "fee_rate_pct": round(FEE_RATE * 100, 3),
                "breakeven_pct": round(BREAKEVEN_PCT * 100, 3),
                "circuit_breaker_active": cb_active,
                "circuit_breaker_minutes": cb_minutes,
                # copia profonda leggera delle posizioni
                "open_positions": {k: dict(v) for k, v in self.open_positions.items()},
                "logs": list(self.logs[:20]),
            }

    # ------------------------------------------------------------------
    # Liquidita'
    # ------------------------------------------------------------------
    def free_up_liquidity(self, required_amount):
        """Vende monete non bloccate se i fondi EUR scarseggiano.
        NON tocca gli asset che corrispondono a posizioni aperte gestite dal bot."""
        if TRADE_MODE == "SIMULATION":
            return

        self.log_msg("Ricerca liquidita' nel portafoglio...")
        try:
            held_bases = {sym.split('/')[0] for sym in self.open_positions}
            balances = self.executor.exchange.fetch_balance()
            free_balances = balances['free']

            for asset, amount in free_balances.items():
                if amount <= 0:
                    continue
                if asset in self.blocked_assets or asset in held_bases:
                    continue  # mai liquidare HODL o posizioni aperte del bot

                self.log_msg(f"Liquidazione automatica di {amount} {asset}...")
                fill = self.executor.execute_market_sell(f"{asset}/EUR", amount)
                if fill is None:
                    self.log_msg(f"Liquidazione {asset} fallita, proseguo.")
                    continue
                time.sleep(2)
                with self.lock:
                    self.current_balance = self.executor.get_balance()
                self.log_msg(f"Liquidazione completata. Nuovo bilancio: €{self.current_balance:.2f}")
                if self.current_balance >= required_amount:
                    return
        except Exception as e:
            self.log_msg(f"Errore controllo portafoglio: {e}")

    def sync_portfolio(self):
        """Scansiona il portafoglio Coinbase e adotta le monete orfane.
        Le posizioni adottate sono marcate (adopted=True): non conosciamo il
        prezzo di carico reale, quindi NON applichiamo lo stop-loss su di esse,
        le chiudiamo solo in profitto netto."""
        if TRADE_MODE == "SIMULATION":
            return
        self.log_msg("Sincronizzazione Portafoglio in corso...")
        try:
            balances = self.executor.exchange.fetch_balance()
            tickers = self.executor.exchange.fetch_tickers()

            for asset, amount in balances.get('total', {}).items():
                if amount <= 0 or asset in self.blocked_assets:
                    continue
                symbol = f"{asset}/EUR"
                if symbol in self.open_positions or symbol not in tickers:
                    continue

                current_price = tickers[symbol].get('last', 0)
                if not current_price:
                    continue
                amount_eur = amount * current_price
                if amount_eur < 2.0:  # ignora la polvere
                    continue

                self.log_msg(f"[SYNC] Trovato {amount} {asset} (€{amount_eur:.2f}). "
                             f"Adottato (solo uscite in profitto).")
                trade_data = {
                    "entry_price": current_price,
                    "current_price": current_price,
                    "highest_price": current_price,
                    "amount_base": amount,
                    "amount_eur": amount_eur,
                    "entry_fee_eur": 0.0,
                    "adopted": True,
                    "pnl_pct": 0.0,
                    "time": time.strftime('%H:%M:%S'),
                }
                with self.lock:
                    self.open_positions[symbol] = trade_data
                database.save_trade(symbol, trade_data)
        except Exception as e:
            self.log_msg(f"Errore durante la sincronizzazione: {e}")

    # ------------------------------------------------------------------
    # Esecuzione chiusura verificata (fee-aware)
    # ------------------------------------------------------------------
    def _close_position(self, sym, reason):
        """Esegue la vendita, verifica il fill e aggiorna stato/bilancio con i
        valori REALI (al netto delle commissioni). Ritorna True se chiusa."""
        with self.lock:
            pos = self.open_positions.get(sym)
        if pos is None:
            return False

        fill = self.executor.execute_market_sell(
            sym, pos["amount_base"], ref_price=pos.get('current_price'))
        if fill is None:
            self.log_msg(f"[ERRORE] Vendita {sym} fallita. Posizione mantenuta.")
            return False

        proceeds = fill['proceeds_eur']
        exit_fee = fill['fee_eur']
        net_pnl = proceeds - pos['amount_eur']  # profitto reale al netto di TUTTE le fee
        total_trade_fees = exit_fee + pos.get('entry_fee_eur', 0.0)

        with self.lock:
            self.open_positions.pop(sym, None)
            self.current_balance += proceeds
            self.total_profit += net_pnl
            self.total_fees += total_trade_fees

            if net_pnl >= 0:
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
            self._persist_state()

        database.remove_trade(sym)
        self.log_msg(f"[{reason}] {sym} chiusa -> {tag} NETTO €{net_pnl:+.2f} "
                     f"(fee €{total_trade_fees:.2f}). Bilancio: €{self.current_balance:.2f}")
        return True

    # ------------------------------------------------------------------
    # Ciclo principale
    # ------------------------------------------------------------------
    def loop(self):
        self.log_msg(f"Avvio Bot in Modalita': {TRADE_MODE}")
        self.log_msg(f"Budget: €{BUDGET} | Trailing: -{STOP_LOSS_PCT*100:.1f}% | "
                     f"Fee/lato: {FEE_RATE*100:.2f}% | Breakeven: {BREAKEVEN_PCT*100:.2f}%")
        self.log_msg(f"Asset Protetti: {', '.join(self.blocked_assets)}")

        self.sync_portfolio()

        while self.running:
            try:
                # Circuit Breaker
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

                gainers = []
                for sym, ticker in tickers.items():
                    if "/EUR" not in sym:
                        continue
                    price = ticker.get('last', 0)
                    pct_change = ticker.get('percentage', 0)
                    volume = ticker.get('baseVolume', 0)
                    if price and pct_change and volume > 100000 and pct_change >= MIN_GAINER_PCT:
                        gainers.append({"symbol": sym, "price": price,
                                        "changesPercentage": pct_change, "volume": volume})
                gainers.sort(key=lambda x: x["changesPercentage"], reverse=True)

                if not gainers:
                    self.log_msg(f"Nessun asset con +{MIN_GAINER_PCT}% trovato. Attesa...")
                else:
                    self.log_msg(f"Trovati {len(gainers)} asset in crescita. Valutazione...")

                # 1. Gestione Posizioni Aperte (SELL) -------------------
                self._manage_open_positions(tickers)

                # Pulizia cooldown scaduti
                with self.lock:
                    now = datetime.datetime.now()
                    self.sold_coins = {k: v for k, v in self.sold_coins.items()
                                       if (now - v).total_seconds() < COOLDOWN_MINUTES * 60}

                # 2. Gestione Nuove Entrate (BUY) -----------------------
                self._manage_new_entries(gainers)

            except Exception as e:
                self.log_msg(f"Errore nel ciclo principale: {e}")

            self._wait(60)

    def _wait(self, seconds):
        for _ in range(seconds):
            if not self.running:
                break
            time.sleep(1)

    def _manage_open_positions(self, tickers):
        with self.lock:
            symbols = list(self.open_positions.keys())

        to_close = []  # (symbol, reason)
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

            sma = self.executor.get_sma(sym)
            if sma is None:
                continue

            trailing_stop_price = highest_price * (1 - STOP_LOSS_PCT)

            if current_price < trailing_stop_price:
                if pnl_pct > 0:
                    # Uscita dal picco con guadagno lordo: chiudiamo (il netto e' calcolato al fill)
                    to_close.append((sym, "TRAILING STOP"))
                elif adopted:
                    # Non conosciamo il carico reale: non forziamo una perdita
                    self.log_msg(f"  [HOLD-ADOPTED] {sym} sotto trailing ma in attesa di profitto")
                else:
                    to_close.append((sym, "STOP LOSS"))
            elif current_price < sma and pnl_pct > BREAKEVEN_PCT:
                # Trend rotto MA solo se il guadagno lordo copre le fee + margine
                to_close.append((sym, "TREND REVERSAL"))
            else:
                self.log_msg(f"  [HOLD] {sym} | PnL lordo: {pnl_pct*100:.2f}% | "
                             f"Max: {highest_price:.4f} | Stop: {trailing_stop_price:.4f}")

        for sym, reason in to_close:
            self._close_position(sym, reason)

    def _manage_new_entries(self, gainers):
        with self.lock:
            open_count = len(self.open_positions)
        if not gainers or open_count >= MAX_OPEN_POSITIONS:
            return

        for gainer in gainers:
            symbol = gainer['symbol']
            price = gainer['price']
            base = symbol.split('/')[0]

            if base in self.blocked_assets:
                continue
            with self.lock:
                if symbol in self.sold_coins or symbol in self.open_positions:
                    continue
                if len(self.open_positions) >= MAX_OPEN_POSITIONS:
                    break

            # CHECK TREND (SMA)
            sma = self.executor.get_sma(symbol)
            if sma is None or price <= sma:
                sma_val = f"{sma:.4f}" if sma is not None else "N/A"
                self.log_msg(f"[FILTRO] {symbol} scartato: Prezzo (${price:.4f}) sotto SMA ({sma_val})")
                continue

            # POSITION SIZING (fee-aware): base 5% del capitale, minimo 5€
            base_amount = max(5.0, self.current_balance * 0.05)
            multiplier = 1.0
            pct_change = gainer.get('changesPercentage', 0)
            volume = gainer.get('volume', 0)
            if pct_change > 15.0:
                multiplier += 0.5
            if volume > 20000000:
                multiplier += 0.5
            trade_amount = base_amount * multiplier

            max_allowed = max(5.0, self.current_balance * 0.15)
            trade_amount = min(trade_amount, max_allowed)

            if self.current_balance < trade_amount:
                self.free_up_liquidity(trade_amount)

            # RSI + MACD
            rsi, macd_bullish = self.executor.get_indicators(symbol)
            if rsi is None:
                continue
            if rsi >= 70:
                self.log_msg(f"[FILTRO] {symbol} scartato: RSI alto ({rsi:.1f} - Ipercomprato)")
                continue
            if not macd_bullish:
                self.log_msg(f"[FILTRO] {symbol} scartato: MACD non rialzista")
                continue

            if self.current_balance < trade_amount:
                self.log_msg(f"[SKIP] {symbol}: liquidita' insufficiente (€{self.current_balance:.2f})")
                continue

            self.log_msg(f"[SEGNALE] {symbol} (SMA ok, RSI {rsi:.1f}, MACD ok). Acquisto €{trade_amount:.2f}...")
            fill = self.executor.execute_market_buy(symbol, trade_amount, ref_price=price)
            if fill is None:
                self.log_msg(f"[ERRORE] Ordine fallito per {symbol}. Annullato.")
                break

            filled_base = fill['filled_base']
            cost_eur = fill['cost_eur']      # EUR lordi spesi (incl. fee)
            entry_fee = fill['fee_eur']
            entry_price = fill['avg_price'] or price
            if not filled_base or not entry_price:
                self.log_msg(f"[ERRORE] Fill non valido per {symbol}. Annullato.")
                break

            trade_data = {
                "entry_price": entry_price,
                "current_price": entry_price,
                "highest_price": entry_price,
                "amount_base": filled_base,
                "amount_eur": cost_eur,
                "entry_fee_eur": entry_fee,
                "adopted": False,
                "pnl_pct": 0.0,
                "time": time.strftime('%H:%M:%S'),
            }
            with self.lock:
                self.open_positions[symbol] = trade_data
                self.current_balance -= cost_eur
            database.save_trade(symbol, trade_data)
            self.log_msg(f"Comprato {symbol}: {filled_base:.6f} @ €{entry_price:.4f} "
                         f"(fee €{entry_fee:.2f}). Bilancio: €{self.current_balance:.2f}")
            break  # una entrata per ciclo

    def start(self):
        if hasattr(self, 'thread') and self.thread.is_alive():
            return
        self.running = True
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.log_msg("Spegnimento bot richiesto...")


# Istanza globale per la Web UI
bot_instance = TradingBot()
