import ccxt
from config import COINBASE_API_KEY, COINBASE_API_SECRET, TRADE_MODE

class CoinbaseExecutor:
    def __init__(self):
        self.exchange = ccxt.coinbase({
            'apiKey': COINBASE_API_KEY,
            'secret': COINBASE_API_SECRET,
            'enableRateLimit': True,
        })
        
        # In simulazione blocchiamo gli ordini, ma l'API resta in lettura live
        if TRADE_MODE == "SIMULATION":
            print("Coinbase Executor inizializzato in SIMULAZIONE (No ordini reali)")
        else:
            print("Coinbase Executor inizializzato in LIVE Mode! ATTENZIONE.")

    def get_balance(self):
        if not COINBASE_API_KEY:
            return 100.0 # Valore mockato
            
        try:
            balance = self.exchange.fetch_balance()
            return balance['total'].get('EUR', 0)
        except Exception as e:
            print(f"Errore recupero bilancio: {e}")
            return 0.0

    def execute_market_buy(self, symbol, amount_eur):
        """Esegue un ordine di acquisto a mercato."""
        if TRADE_MODE == "SIMULATION":
            print(f"[SIMULATION] BUY MOCK di {symbol} per €{amount_eur}")
            return {'id': 'sim_buy', 'status': 'closed'}
            
        if not COINBASE_API_KEY:
            print(f"[MOCK] Eseguito BUY Market di {symbol} per €{amount_eur}")
            return {'id': 'sim_buy_no_key'}
            
        try:
            # Calcolo amount base in base al prezzo
            ticker = self.exchange.fetch_ticker(symbol)
            price = ticker['last']
            amount_base = amount_eur / price
            
            order = self.exchange.create_market_buy_order(symbol, amount_base)
            print(f"Ordino eseguito su Coinbase: BUY {amount_base} {symbol}")
            return order
        except Exception as e:
            print(f"Errore BUY Coinbase ({symbol}): {e}")
            return None

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
            print(f"Ordino eseguito su Coinbase: SELL {amount_base} {symbol}")
            return order
        except Exception as e:
            print(f"Errore SELL Coinbase ({symbol}): {e}")
            return None

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
