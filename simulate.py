import requests
import datetime
from config import BUDGET, STOP_LOSS_PCT, TAKE_PROFIT_PCT

# Configurazioni
PAIRS = ['BTC-USD', 'ETH-USD', 'SOL-USD']
GRANULARITY = 60 # Candele da 1 minuto (60 secondi)
SIMULATION_HOURS = 2

def fetch_historical_data(product_id, start, end):
    """Scarica dati storici pubblici da Coinbase."""
    url = f"https://api.exchange.coinbase.com/products/{product_id}/candles"
    params = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "granularity": GRANULARITY
    }
    response = requests.get(url, params=params)
    if response.status_code == 200:
        data = response.json()
        if not data:
            return []
        # [timestamp, price_low, price_high, price_open, price_close, volume]
        # Sort in ascending time order (Coinbase returns descending)
        data.reverse()
        formatted_data = []
        for row in data:
            formatted_data.append({
                'timestamp': datetime.datetime.fromtimestamp(row[0]),
                'low': float(row[1]),
                'high': float(row[2]),
                'open': float(row[3]),
                'close': float(row[4]),
                'volume': float(row[5])
            })
        return formatted_data
    else:
        print(f"Errore Coinbase API ({product_id}): {response.status_code}")
        return []

def run_scalping_simulation():
    print(f"--- Avvio Simulazione Scalping ({SIMULATION_HOURS} Ore) ---")
    print(f"Budget Iniziale: €{BUDGET}")
    print(f"Stop-Loss: {STOP_LOSS_PCT*100}% | Take-Profit: {TAKE_PROFIT_PCT*100}%")
    
    end_time = datetime.datetime.utcnow()
    start_time = end_time - datetime.timedelta(hours=SIMULATION_HOURS)
    
    print(f"Recupero dati da {start_time.strftime('%H:%M:%S')} a {end_time.strftime('%H:%M:%S')} (UTC)...")
    
    total_trades = 0
    winning_trades = 0
    losing_trades = 0
    current_balance = BUDGET
    
    for pair in PAIRS:
        df = fetch_historical_data(pair, start_time, end_time)
        if not df:
            continue
            
        print(f"\nAnalisi {pair} ({len(df)} candele trovate)...")
        
        in_position = False
        entry_price = 0.0
        trade_amount = 20.0 # Usiamo €20 per trade simulato
        
        for i in range(5, len(df)):
            current_candle = df[i]
            prev_candle = df[i-1]
            
            # Calcolo variazione % e Media Volume (5 candele precedenti)
            pct_change = (current_candle['close'] - prev_candle['close']) / prev_candle['close']
            vol_ma = sum(c['volume'] for c in df[i-5:i]) / 5
            
            # Segnale di Acquisto (Trend in salita e volume alto)
            if not in_position and pct_change > 0.001 and current_candle['volume'] > vol_ma:
                in_position = True
                entry_price = current_candle['close']
                # print(f"BUY {pair} a {entry_price}")
                
            # Gestione Posizione (Take Profit o Stop Loss)
            elif in_position:
                current_price = current_candle['close']
                pnl_pct = (current_price - entry_price) / entry_price
                
                if pnl_pct >= TAKE_PROFIT_PCT:
                    # Take Profit
                    profit = trade_amount * pnl_pct
                    current_balance += profit
                    total_trades += 1
                    winning_trades += 1
                    in_position = False
                    # print(f"SELL {pair} a {current_price} (PROFITTO: €{profit:.2f})")
                    
                elif pnl_pct <= -STOP_LOSS_PCT:
                    # Stop Loss
                    loss = trade_amount * abs(pnl_pct)
                    current_balance -= loss
                    total_trades += 1
                    losing_trades += 1
                    in_position = False
                    # print(f"SELL {pair} a {current_price} (PERDITA: €{loss:.2f})")
                    
    # Report Finale
    print("\n=================================")
    print("      REPORT FINALE (2 ORE)      ")
    print("=================================")
    print(f"Operazioni Totali  : {total_trades}")
    if total_trades > 0:
        win_rate = (winning_trades / total_trades) * 100
        print(f"Win Rate           : {win_rate:.1f}% ({winning_trades} Vinte, {losing_trades} Perse)")
    print(f"Budget Finale      : €{current_balance:.2f}")
    profit_netto = current_balance - BUDGET
    print(f"Profitto Netto     : €{profit_netto:.2f} ({profit_netto/BUDGET*100:.2f}%)")
    print("=================================")

if __name__ == "__main__":
    run_scalping_simulation()
