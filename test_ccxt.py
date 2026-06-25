import ccxt
from config import COINBASE_API_KEY, COINBASE_API_SECRET

exchange = ccxt.coinbase({
    'apiKey': COINBASE_API_KEY,
    'secret': COINBASE_API_SECRET,
    'enableRateLimit': True,
    'options': {
        'createMarketBuyOrderRequiresPrice': False
    }
})

try:
    print("Tentativo di acquisto di 5 USDC (con EUR)...")
    order = exchange.create_market_buy_order('USDC/EUR', 5.0)
    print("Ordine Riuscito!", order)
except Exception as e:
    print("Errore:", e)
