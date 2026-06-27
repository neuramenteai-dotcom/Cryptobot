import os
from dotenv import load_dotenv

load_dotenv()

# Coinbase API (Advanced Trade)
COINBASE_API_KEY = os.getenv("COINBASE_API_KEY", "")
COINBASE_API_SECRET = os.getenv("COINBASE_API_SECRET", "")

# Financial Modeling Prep API
FMP_API_KEY = os.getenv("FMP_API_KEY", "")

# Trading Settings
# Modalita': "LIVE" (denaro reale) o "SIMULATION" (mock, nessun ordine reale).
# Letto dall'ambiente; default LIVE per mantenere il comportamento di deploy esistente.
TRADE_MODE = os.getenv("TRADE_MODE", "LIVE").strip().upper()
BUDGET = float(os.getenv("BUDGET", "150.0"))  # Valore di default 150 euro

# --- Commissioni (CRITICO per il profitto reale) ---
# Fee taker di Coinbase Advanced Trade per lato (ordini a mercato = taker).
# Default conservativo 0.6%. Round-trip (entrata + uscita) ~= 2 x FEE_RATE.
FEE_RATE = float(os.getenv("FEE_RATE", "0.006"))
ROUND_TRIP_FEE = FEE_RATE * 2

# Margine di profitto NETTO minimo che vogliamo incassare oltre alle fee
# prima di considerare una chiusura "in profitto". (0.4% di default)
MIN_NET_PROFIT_PCT = float(os.getenv("MIN_NET_PROFIT_PCT", "0.004"))

# Soglia di breakeven lordo: sotto questo guadagno lordo l'operazione e' in
# perdita netta a causa delle commissioni. Usata per non vendere "in finto profitto".
BREAKEVEN_PCT = ROUND_TRIP_FEE + MIN_NET_PROFIT_PCT

# Scalping Parameters
TIMEFRAME = "5m"  # timeframe usato per SMA/indicatori
# Stop loss / trailing: distanza dal picco massimo prima di uscire.
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.015"))  # 1.5% trailing dal picco
# Take profit target di riferimento (deve coprire abbondantemente le fee).
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.02"))  # +2%

# Soglia minima di crescita giornaliera per considerare un asset "gainer".
MIN_GAINER_PCT = float(os.getenv("MIN_GAINER_PCT", "2.0"))

BLOCKED_ASSETS = ['BTC', 'AIOZ', 'EUR', 'USDC']  # Asset da non liquidare mai
