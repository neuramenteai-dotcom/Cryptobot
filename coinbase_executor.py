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
        if not COINBASE_API_KEY:
            print(f"[MOCK] Eseguito BUY Market di {symbol} per €{amount_eur}")
            return True
            
        try:
            # Formattazione simbolo da FMP (es. SOLUSD) a ccxt (SOL/USD o SOL/EUR)
            ccxt_symbol = symbol.replace("USD", "/EUR") # Assumiamo scambi in Euro
            
            # Coinbase richiede spesso l'ammontare in base (es. quanti SOL).
            # Dobbiamo calcolarlo in base al prezzo attuale.
            ticker = self.exchange.fetch_ticker(ccxt_symbol)
            price = ticker['last']
            amount_base = amount_eur / price
            
            order = self.exchange.create_market_buy_order(ccxt_symbol, amount_base)
            print(f"Ordino eseguito su Coinbase: BUY {amount_base} {ccxt_symbol}")
            return order
        except Exception as e:
            print(f"Errore BUY Coinbase ({symbol}): {e}")
            return None

    def execute_market_sell(self, symbol, amount_base):
        """Esegue un ordine di vendita a mercato."""
        if not COINBASE_API_KEY:
            print(f"[MOCK] Eseguito SELL Market di {symbol} per {amount_base} coin")
            return True
            
        try:
            ccxt_symbol = symbol.replace("USD", "/EUR")
            order = self.exchange.create_market_sell_order(ccxt_symbol, amount_base)
            print(f"Ordino eseguito su Coinbase: SELL {amount_base} {ccxt_symbol}")
            return order
        except Exception as e:
            print(f"Errore SELL Coinbase ({symbol}): {e}")
            return None
