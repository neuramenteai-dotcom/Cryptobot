import time
import datetime
import threading
from config import TRADE_MODE, BUDGET, STOP_LOSS_PCT, TAKE_PROFIT_PCT
from fmp_radar import get_crypto_gainers
from coinbase_executor import CoinbaseExecutor

COOLDOWN_MINUTES = 30

class TradingBot:
    def __init__(self):
        self.executor = CoinbaseExecutor()
        self.running = False
        self.logs = []
        self.open_positions = {}
        self.sold_coins = {}
        self.current_balance = self.executor.get_balance() if TRADE_MODE == "LIVE" else BUDGET
        self.win_rate = {"wins": 0, "losses": 0}
        self.total_profit = 0.0
        
        # Le monete da NON VENDERE MAI (HODL)
        self.blocked_assets = ['BTC', 'AIOZ', 'EUR', 'USDC']
        
    def log_msg(self, msg):
        timestamp = time.strftime('%H:%M:%S')
        full_msg = f"[{timestamp}] {msg}"
        print(full_msg, flush=True)
        self.logs.insert(0, full_msg) # Inseriamo in testa per la web UI
        # Mantieni massimo 100 log
        if len(self.logs) > 100:
            self.logs.pop()

    def free_up_liquidity(self, required_amount):
        """Vende monete non bloccate se i fondi EUR scarseggiano."""
        if TRADE_MODE == "SIMULATION":
            return # In simulazione i 150€ sono fittizi
            
        self.log_msg("Ricerca liquidità nel portafoglio...")
        try:
            balances = self.executor.exchange.fetch_balance()
            free_balances = balances['free']
            
            for asset, amount in free_balances.items():
                if amount > 0 and asset not in self.blocked_assets:
                    # Trovata una moneta liquidabile
                    self.log_msg(f"Liquidazione automatica di {amount} {asset}...")
                    try:
                        self.executor.execute_market_sell(f"{asset}/EUR", amount)
                        # Ricarichiamo il bilancio
                        time.sleep(2)
                        new_balance = self.executor.get_balance()
                        self.current_balance = new_balance
                        self.log_msg(f"Liquidazione completata. Nuovo bilancio: €{self.current_balance:.2f}")
                        if self.current_balance >= required_amount:
                            return
                    except Exception as e:
                        self.log_msg(f"Errore liquidazione {asset}: {e}")
        except Exception as e:
            self.log_msg(f"Errore controllo portafoglio: {e}")

    def loop(self):
        self.log_msg(f"Avvio Bot in Modalità: {TRADE_MODE}")
        self.log_msg(f"Budget: €{BUDGET} | SL: -{STOP_LOSS_PCT*100}% | TP: +{TAKE_PROFIT_PCT*100}%")
        self.log_msg(f"Asset Protetti (Non verranno venduti): {', '.join(self.blocked_assets)}")
        
        while self.running:
            try:
                self.log_msg("Scansione Mercato in corso...")
                gainers = get_crypto_gainers(min_pct_change=3.0)
                
                try:
                    tickers = self.executor.exchange.fetch_tickers()
                except Exception as e:
                    self.log_msg(f"Errore connessione Coinbase: {e}")
                    time.sleep(10)
                    continue
                    
                # 1. Gestione Posizioni Aperte (SELL)
                symbols_to_close = []
                for sym, pos in self.open_positions.items():
                    if sym in tickers:
                        current_price = tickers[sym].get('last', 0)
                        if current_price == 0: continue
                        
                        entry_price = pos['entry_price']
                        pnl_pct = (current_price - entry_price) / entry_price
                        pos['current_price'] = current_price
                        pos['pnl_pct'] = pnl_pct
                        
                        # Calcoliamo l'SMA per Trend Following
                        sma = self.executor.get_sma(sym)
                        if sma is None: continue
                        
                        if current_price < sma:
                            # Il trend si è invertito, chiudiamo la posizione
                            if pnl_pct > 0:
                                profit_eur = pos['amount_eur'] * pnl_pct
                                self.log_msg(f"[TREND REVERSAL] {sym} sceso sotto SMA. Incasso Profitto: +€{profit_eur:.2f}")
                                symbols_to_close.append((sym, profit_eur, 'win'))
                            else:
                                loss_eur = pos['amount_eur'] * abs(pnl_pct)
                                self.log_msg(f"[TREND REVERSAL] {sym} sceso sotto SMA. Vendo in Perdita: -€{loss_eur:.2f}")
                                symbols_to_close.append((sym, -loss_eur, 'loss'))
                        elif pnl_pct <= -STOP_LOSS_PCT:
                            # Failsafe Hard Stop Loss
                            loss_eur = pos['amount_eur'] * abs(pnl_pct)
                            self.log_msg(f"[HARD STOP LOSS] {sym} a ${current_price:.4f} (-€{loss_eur:.2f})")
                            symbols_to_close.append((sym, -loss_eur, 'loss'))
                        else:
                            self.log_msg(f"  [HODL] {sym} sopra SMA (${sma:.4f}) | PnL: {pnl_pct*100:.2f}%")
                            
                for sym, pnl, result_type in symbols_to_close:
                    pos = self.open_positions.pop(sym)
                    self.executor.execute_market_sell(sym, pos["amount_base"])
                    self.current_balance += (pos["amount_eur"] + pnl)
                    self.total_profit += pnl
                    if result_type == 'win': 
                        self.win_rate['wins'] += 1
                    else: 
                        self.win_rate['losses'] += 1
                        self.sold_coins[sym] = datetime.datetime.now()
                        self.log_msg(f"  ⚠️ {sym} in cooldown per {COOLDOWN_MINUTES} minuti")
                    self.log_msg(f"Bilancio Aggiornato: €{self.current_balance:.2f}")

                # Pulisci il cooldown Expired
                self.sold_coins = {k: v for k, v in self.sold_coins.items() if (datetime.datetime.now() - v).total_seconds() < COOLDOWN_MINUTES * 60}

                # 2. Gestione Nuove Entrate (BUY)
                if gainers and len(self.open_positions) < 10:
                    for gainer in gainers:
                        symbol = gainer['symbol']
                        price = gainer['price']
                        
                        # Filtro HODL assets
                        if 'BTC' in symbol or 'AIOZ' in symbol:
                            continue
                            
                        # Skip se in cooldown
                        if symbol in self.sold_coins:
                            continue
                        
                        if symbol not in self.open_positions:
                            # CHECK TREND
                            sma = self.executor.get_sma(symbol)
                            if sma is None or price <= sma:
                                # Il trend non è confermato
                                continue
                                
                            # BASE SIZING: 5% del capitale, ma almeno 5€ per rispettare i minimi di Coinbase
                            base_amount = max(5.0, self.current_balance * 0.05)
                            
                            # RISK MANAGEMENT (Weighting & Momentum)
                            multiplier = 1.0
                            pct_change = gainer.get('changesPercentage', 0)
                            volume = gainer.get('volume', 0)
                            
                            # Se sta spingendo forte al rialzo (> 15% giornaliero), rischiamo di più per cavalcare
                            if pct_change > 15.0:
                                multiplier += 0.5 # +50% di budget
                            
                            # Se è una moneta "pesante" (Altissima liquidità, es > 20 Milioni), è più sicura
                            if volume > 20000000:
                                multiplier += 0.5 # +50% di budget
                                
                            trade_amount = base_amount * multiplier
                            
                            # Hard cap per sicurezza: non investire mai più del 15% in una singola moneta
                            max_allowed = max(5.0, self.current_balance * 0.15)
                            if trade_amount > max_allowed:
                                trade_amount = max_allowed
                            
                            if self.current_balance < trade_amount:
                                self.free_up_liquidity(trade_amount)
                                
                            if self.current_balance >= trade_amount:
                                self.log_msg(f"[SEGNALE] {symbol} in rialzo (+{pct_change:.2f}%). Vol: {volume:,.0f} | Peso Moltiplicatore: {multiplier}x | Investo: €{trade_amount:.2f}")
                                order = self.executor.execute_market_buy(symbol, trade_amount)
                                
                                if order is not None:
                                    self.open_positions[symbol] = {
                                        "entry_price": price,
                                        "current_price": price,
                                        "amount_base": trade_amount / price,
                                        "amount_eur": trade_amount,
                                        "pnl_pct": 0.0,
                                        "time": time.strftime('%H:%M:%S')
                                    }
                                    self.current_balance -= trade_amount
                                else:
                                    self.log_msg(f"[ERRORE] Ordine fallito per {symbol}. Transazione annullata.")
                                break
                                
            except Exception as e:
                self.log_msg(f"Errore nel ciclo principale: {e}")
                
            # Attesa non bloccante per permettere alla web UI di leggere lo stato fluido
            for _ in range(60):
                if not self.running: break
                time.sleep(1)

    def start(self):
        if not self.running:
            self.running = True
            threading.Thread(target=self.loop, daemon=True).start()
            
    def stop(self):
        self.running = False
        self.log_msg("Spegnimento bot richiesto...")

# Istanza globale per la Web UI
bot_instance = TradingBot()
