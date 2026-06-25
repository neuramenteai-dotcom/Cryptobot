import time
import datetime
from config import TRADE_MODE, BUDGET, STOP_LOSS_PCT, TAKE_PROFIT_PCT
from fmp_radar import get_crypto_gainers
from coinbase_executor import CoinbaseExecutor

LOG_FILE = "bot_log.txt"

def log_msg(msg):
    """Stampa a schermo e salva nel file di log per farti vedere cosa succede."""
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(full_msg + "\n")

def start_bot(duration_hours=1):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("=== LOG BOT DI TRADING (COINBASE) ===\n")
        
    log_msg(f"Avvio Bot in Modalità: {TRADE_MODE}")
    log_msg(f"Budget Base: €{BUDGET} | Stop-Loss: -{STOP_LOSS_PCT*100}% | Take-Profit: +{TAKE_PROFIT_PCT*100}%")
    log_msg(f"Il bot si spegnerà automaticamente tra {duration_hours} ora/e.\n")
    
    executor = CoinbaseExecutor()
    current_balance = executor.get_balance() if TRADE_MODE == "LIVE" else BUDGET
    log_msg(f"Bilancio Operativo: €{current_balance:.2f}\n")
    
    open_positions = {}
    
    end_time = datetime.datetime.now() + datetime.timedelta(hours=duration_hours)
    
    try:
        while datetime.datetime.now() < end_time:
            log_msg("Scansione Mercato in corso...")
            
            # 1. Trova le monete in pump (Gainers) dal nostro Radar Coinbase
            gainers = get_crypto_gainers(min_pct_change=3.0)
            
            # Recuperiamo i prezzi correnti globali per aggiornare le posizioni
            try:
                tickers = executor.exchange.fetch_tickers()
            except Exception as e:
                log_msg(f"Errore connessione Coinbase: {e}")
                time.sleep(30)
                continue
                
            # 2. Gestione Posizioni Aperte (SELL)
            symbols_to_close = []
            for sym, pos in open_positions.items():
                if sym in tickers:
                    current_price = tickers[sym].get('last', 0)
                    if current_price == 0: continue
                    
                    entry_price = pos['entry_price']
                    pnl_pct = (current_price - entry_price) / entry_price
                    
                    # Controllo Take Profit o Stop Loss
                    if pnl_pct >= TAKE_PROFIT_PCT:
                        profit_eur = pos['amount_eur'] * pnl_pct
                        log_msg(f"[TAKE PROFIT] su {sym}! Vendita a ${current_price:.4f} (Guadagno: €{profit_eur:.2f})")
                        symbols_to_close.append((sym, profit_eur))
                    elif pnl_pct <= -STOP_LOSS_PCT:
                        loss_eur = pos['amount_eur'] * abs(pnl_pct)
                        log_msg(f"[STOP LOSS] su {sym}! Vendita a ${current_price:.4f} (Perdita: €{loss_eur:.2f})")
                        symbols_to_close.append((sym, -loss_eur))
                    else:
                        log_msg(f"  Monitoraggio {sym} | PnL Attuale: {pnl_pct*100:.2f}%")
                        
            for sym, pnl in symbols_to_close:
                pos = open_positions.pop(sym)
                executor.execute_market_sell(sym, pos["amount_base"])
                current_balance += (pos["amount_eur"] + pnl)
                log_msg(f"  Bilancio Aggiornato: €{current_balance:.2f}")

            # 3. Gestione Nuove Entrate (BUY)
            if gainers and len(open_positions) < 3: # Max 3 operazioni aperte in contemporanea
                for gainer in gainers:
                    symbol = gainer['symbol']
                    price = gainer['price']
                    
                    # Se non l'abbiamo già comprato
                    if symbol not in open_positions:
                        trade_amount = 20.0 # Puntata fissa di 20€ per mitigare i rischi
                        
                        if current_balance >= trade_amount:
                            log_msg(f"[SEGNALE RADAR] {symbol} in rialzo (+{gainer.get('changesPercentage', 0):.2f}%). Compro!")
                            order = executor.execute_market_buy(symbol, trade_amount)
                            
                            open_positions[symbol] = {
                                "entry_price": price,
                                "amount_base": trade_amount / price,
                                "amount_eur": trade_amount
                            }
                            current_balance -= trade_amount
                            break # Apriamo max 1 nuova posizione a ciclo
                        else:
                            log_msg("  Fondi insufficienti per un nuovo trade.")
                            
            log_msg("Pausa di 60 secondi...\n")
            time.sleep(60)
            
    except KeyboardInterrupt:
        log_msg("\nArresto manuale del Bot.")
        
    # Chiusura Forzata a fine tempo (per calcolare il bilancio)
    log_msg(f"\n--- FINE DELLA SESSIONE (1 ORA) ---")
    log_msg(f"Chiudo eventuali posizioni rimaste aperte...")
    for sym, pos in open_positions.items():
        if sym in tickers:
            pnl_pct = (tickers[sym]['last'] - pos['entry_price']) / pos['entry_price']
            pnl_eur = pos['amount_eur'] * pnl_pct
            current_balance += (pos['amount_eur'] + pnl_eur)
            
    log_msg(f"BILANCIO FINALE STIMATO: €{current_balance:.2f}")

if __name__ == "__main__":
    start_bot(duration_hours=1)
