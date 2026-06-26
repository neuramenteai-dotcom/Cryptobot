import ccxt
from config import COINBASE_API_KEY, COINBASE_API_SECRET, TRADE_MODE
from utils import retry_with_backoff, calculate_rsi, calculate_macd

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

    @retry_with_backoff(max_retries=3)
    def get_indicators(self, symbol, timeframe='5m', period=100):
        """Scarica le candele e calcola RSI e MACD."""
        try:
            candles = self.exchange.fetch_ohlcv(symbol, timeframe, limit=period)
            if not candles or len(candles) < period:
                return None, None
                
            closes = [candle[4] for candle in candles]
            
            rsi_series = calculate_rsi(closes)
            macd_line, signal_line, histogram = calculate_macd(closes)
            
            if not rsi_series or not macd_line:
                return None, None
                
            current_rsi = rsi_series[-1]
            
            # Check se macd e' bullish (macd_line > signal_line e histogram > 0)
            # Dobbiamo assicurarci che non ci siano None alla fine
            macd_bullish = False
            if macd_line[-1] is not None and signal_line[-1] is not None and histogram[-1] is not None:
                macd_bullish = macd_line[-1] > signal_line[-1] and histogram[-1] > 0
                
            return current_rsi, macd_bullish
            
        except Exception as e:
            print(f"Errore Indicators per {symbol}: {e}")
            return None, None
