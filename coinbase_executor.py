import ccxt
from config import COINBASE_API_KEY, COINBASE_API_SECRET, TRADE_MODE
from utils import retry_with_backoff

class CoinbaseExecutor:
    def __init__(self):
        self.exchange = ccxt.coinbase({
            'apiKey': COINBASE_API_KEY,
            'secret': COINBASE_API_SECRET,
            'enableRateLimit': True,
            'options': {
                'createMarketBuyOrderRequiresPrice': False
            }
        })
        
        # In simulazione blocchiamo gli ordini, ma l'API resta in lettura live
        if TRADE_MODE == "SIMULATION":
            print("Coinbase Executor inizializzato in SIMULAZIONE (No ordini reali)")
        else:
            print("Coinbase Executor inizializzato in LIVE Mode! ATTENZIONE.")

    @retry_with_backoff(max_retries=3)
    def get_balance(self):
        if not COINBASE_API_KEY:
            return 100.0 # Valore mockato
            
        try:
            balance = self.exchange.fetch_balance()
            return balance['total'].get('EUR', 0)
        except Exception as e:
            print(f"Errore recupero bilancio: {e}")
            return 0.0

    @retry_with_backoff(max_retries=3)
    def get_tickers(self):
        return self.exchange.fetch_tickers()

    @retry_with_backoff(max_retries=3)
    def execute_market_buy(self, symbol, amount_eur):
        """Esegue un ordine di acquisto a mercato."""
        if TRADE_MODE == "SIMULATION":
            print(f"[SIMULATION] BUY MOCK di {symbol} per €{amount_eur}")
            return {'id': 'sim_buy', 'status': 'closed'}
            
        if not COINBASE_API_KEY:
            print(f"[MOCK] Eseguito BUY Market di {symbol} per €{amount_eur}")
            return {'id': 'sim_buy_no_key'}
            
        try:
            # Per i Market Buy su Coinbase Advanced Trade, passando createMarketBuyOrderRequiresPrice=False
            # l'ammontare è direttamente il costo nella valuta quote (es. EUR).
            order = self.exchange.create_market_buy_order(symbol, amount_eur)
            print(f"Ordino eseguito su Coinbase: BUY €{amount_eur} di {symbol}", flush=True)
            return order
        except Exception as e:
            print(f"Errore BUY Coinbase ({symbol}): {e}", flush=True)
            return None

    @retry_with_backoff(max_retries=3)
    def execute_market_sell(self, symbol, amount_base):
        """Esegue un ordine di vendita a mercato."""
        if TRADE_MODE == "SIMULATION":
            print(f"[SIMULATION] SELL MOCK di {symbol} per {amount_base} coin")
            return {'id': 'sim_sell', 'status': 'closed'}
            
        if not COINBASE_API_KEY:
            print(f"[MOCK] Eseguito SELL Market di {symbol} per {amount_base} coin")
            return {'id': 'sim_sell_no_key'}
            
        try:
            order = self.exchange.create_market_sell_order(symbol, amount_base)
            print(f"Ordino eseguito su Coinbase: SELL {amount_base} {symbol}", flush=True)
            return order
        except Exception as e:
            print(f"Errore SELL Coinbase ({symbol}): {e}", flush=True)
            return None

    @retry_with_backoff(max_retries=3)
    def get_sma(self, symbol, timeframe='5m', period=10):
        """Calcola la Simple Moving Average (SMA) scaricando le candele (OHLCV) da Coinbase."""
        try:
            # ccxt fetch_ohlcv returns: [timestamp, open, high, low, close, volume]
            candles = self.exchange.fetch_ohlcv(symbol, timeframe, limit=period)
            
            if not candles or len(candles) < period:
                return None
                
            # Estraiamo solo i prezzi di chiusura (index 4)
            closes = [candle[4] for candle in candles]
            sma = sum(closes) / len(closes)
            return sma
        except Exception as e:
            print(f"Errore SMA per {symbol}: {e}")
            return None
