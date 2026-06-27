import time
import sys
from coinbase_executor import CoinbaseExecutor
from bot_engine import bot_instance
import database

def verify():
    print("=== INIZIO VERIFICA LIVE (5 ITERAZIONI) ===")
    executor = CoinbaseExecutor()
    
    for i in range(1, 6):
        print(f"\n--- Iterazione {i}/5 ---")
        try:
            # Check API Key
            from config import COINBASE_API_KEY
            if not COINBASE_API_KEY:
                print("ATTENZIONE: Nessuna API KEY configurata. Impossibile verificare su Coinbase reale.")
                break
                
            balance = executor.get_balance()
            print(f"Bilancio EUR attuale: €{balance:.2f}")
            
            # Fetch all balances to find open positions (assets > 0)
            balances = executor.exchange.fetch_balance()
            assets = {k: v for k, v in balances.get('total', {}).items() if v > 0}
            print(f"Asset posseduti su Coinbase: {assets}")
            
            # Check open orders
            try:
                orders = executor.exchange.fetch_open_orders()
                print(f"Ordini aperti (pendenti): {len(orders)}")
                for o in orders:
                    print(f"  - {o['symbol']} | {o['side']} | {o['amount']} @ {o['price']}")
            except Exception as e:
                print(f"Impossibile fetchare open orders: {e}")
                
            # Check local DB positions
            db_trades = database.load_trades()
            print(f"Posizioni aperte nel Database locale: {list(db_trades.keys())}")
            
        except Exception as e:
            print(f"Errore durante l'iterazione {i}: {e}")
            
        if i < 5:
            print("Attesa 15 secondi prima della prossima verifica...")
            time.sleep(15)
            
    print("\n=== VERIFICA COMPLETATA ===")

if __name__ == "__main__":
    verify()
