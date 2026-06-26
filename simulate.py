import requests
import datetime
from config import BUDGET, STOP_LOSS_PCT

# Configurazioni
PAIRS = ['ETH-USD', 'SOL-USD', 'AVAX-USD', 'LINK-USD', 'DOT-USD'] # Esclusi BTC e AIOZ
GRANULARITY = 300 # Candele da 5 minuti (300 secondi)
SIMULATION_HOURS = 24
SMA_PERIOD = 10

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
    print(f"--- Avvio Simulazione Trend Following Media Mobile ({SIMULATION_HOURS} Ore) ---")
    print(f"Budget Iniziale: €{BUDGET}")
    print(f"Stop-Loss: {STOP_LOSS_PCT*100}% | SMA: {SMA_PERIOD} periodi su {GRANULARITY//60} min")
    
    end_time = datetime.datetime.now(datetime.timezone.utc)
    start_time = end_time - datetime.timedelta(hours=SIMULATION_HOURS)
    
    total_trades = 0
    winning_trades = 0
    losing_trades = 0
    current_balance = BUDGET
    
    for pair in PAIRS:
        df = fetch_historical_data(pair, start_time, end_time)
        if not df or len(df) < SMA_PERIOD:
            continue
            
        print(f"\nAnalisi {pair} ({len(df)} candele trovate)...")
        
        in_position = False
        entry_price = 0.0
        trade_amount = 20.0
        cooldown_until = 0  # Indice della candela fino alla quale non operare
        
        for i in range(SMA_PERIOD, len(df)):
            current_candle = df[i]
            prev_candle = df[i-1]
            
            # Calcolo SMA (ultimi SMA_PERIOD prezzi di chiusura)
            sma_closes = [c['close'] for c in df[i-SMA_PERIOD:i]]
            sma = sum(sma_closes) / len(sma_closes)
            
            pct_change = (current_candle['close'] - prev_candle['close']) / prev_candle['close']
            vol_ma = sum(c['volume'] for c in df[i-5:i]) / 5
            
            current_price = current_candle['close']
            
            # Segnale Acquisto: Forte rialzo E Prezzo Sopra Media Mobile E non in cooldown
            if not in_position and i >= cooldown_until and pct_change > 0.001 and current_candle['volume'] > vol_ma and current_price > sma:
                in_position = True
                entry_price = current_price
                # print(f"BUY {pair} a {entry_price}")
                
            # Gestione Posizione: Cavalcare l'onda
            elif in_position:
                pnl_pct = (current_price - entry_price) / entry_price
                
                # Trend Reversal (Incrocio ribassista della SMA)
                if current_price < sma:
                    pnl = trade_amount * pnl_pct
                    current_balance += pnl
                    total_trades += 1
                    if pnl > 0:
                        winning_trades += 1
                    else:
                        losing_trades += 1
                        # Imposta cooldown di 30 minuti (6 candele da 5 minuti)
                        cooldown_until = i + 6
                    in_position = False
                    
                # Hard Stop Loss (Failsafe contro i crolli verticali)
                elif pnl_pct <= -STOP_LOSS_PCT:
                    loss = trade_amount * abs(pnl_pct)
                    current_balance -= loss
                    total_trades += 1
                    losing_trades += 1
                    in_position = False
                    # Imposta cooldown di 30 minuti (6 candele da 5 minuti)
                    cooldown_until = i + 6
                    # print(f"STOP LOSS {pair} a {current_price} (-€{loss:.2f})")
                    
    print("\n=================================")
    print(f"   REPORT FINALE ({SIMULATION_HOURS} ORE STORICHE)   ")
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
