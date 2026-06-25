import os
from dotenv import load_dotenv
import ccxt

load_dotenv()

def test_connections():
    print("=== TEST CONNESSIONE COINBASE E RADAR ===\n")
    
    cb_key = os.getenv("COINBASE_API_KEY", "")
    cb_secret = os.getenv("COINBASE_API_SECRET", "")
    
    if not cb_key or not cb_secret:
        print("[ERRORE] Chiavi Coinbase mancanti nel file .env")
        return
        
    try:
        print("Test 1: Autenticazione...")
        exchange = ccxt.coinbase({
            'apiKey': cb_key,
            'secret': cb_secret,
            'enableRateLimit': True,
        })
        balance = exchange.fetch_balance()
        total_eur = balance['total'].get('EUR', 0)
        print(f"[OK] Autenticazione riuscita. Bilancio: {total_eur} EUR\n")
        
        print("Test 2: Coinbase Native Radar (Ricerca Volatilità)...")
        tickers = exchange.fetch_tickers()
        gainers = 0
        for symbol, ticker in tickers.items():
            pct = ticker.get('percentage') or 0
            if ("/EUR" in symbol or "/USD" in symbol) and pct > 2.0:
                gainers += 1
                
        print(f"[OK] Radar funzionante! Letti {len(tickers)} asset. Trovate {gainers} monete in forte rialzo (>2%).")
        
    except ccxt.AuthenticationError:
        print("[ERRORE] Coinbase: Autenticazione fallita (Chiavi errate o revocate)")
    except Exception as e:
        print(f"[ERRORE] Generico: {e}")

if __name__ == "__main__":
    test_connections()
