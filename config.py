import os
from dotenv import load_dotenv

load_dotenv()

# Coinbase API (Advanced Trade)
COINBASE_API_KEY = os.getenv("COINBASE_API_KEY", "")
COINBASE_API_SECRET = os.getenv("COINBASE_API_SECRET", "")

# Financial Modeling Prep API
FMP_API_KEY = os.getenv("FMP_API_KEY", "")

# Trading Settings
TRADE_MODE = os.getenv("TRADE_MODE", "LIVE") # Costringiamo il LIVE come default
BUDGET = float(os.getenv("BUDGET", "150.0")) # Valore di default 150 euro

# Scalping Parameters
TIMEFRAME = "1m" # 1 minuto per scalping
STOP_LOSS_PCT = 0.01 # Stop loss base (utilizzato come failsafe, il trailing è più intelligente)
TAKE_PROFIT_PCT = 0.02 # +2%
BLOCKED_ASSETS = ['BTC', 'AIOZ', 'EUR', 'USDC'] # Asset da non liquidare mai
