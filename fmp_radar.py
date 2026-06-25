import ccxt
from config import COINBASE_API_KEY, COINBASE_API_SECRET

def get_crypto_gainers(min_price=0.01, min_pct_change=2.0):
    """
    Trova i Gainers direttamente usando Coinbase tramite ccxt.
    Non usiamo più FMP perché richiede abbonamenti premium per i dati Crypto in tempo reale.
    """
    try:
        # Usiamo Coinbase in sola lettura pubblica se non ci sono le chiavi, 
        # altrimenti usiamo le chiavi per evitare rate-limit stringenti.
        if COINBASE_API_KEY:
            exchange = ccxt.coinbase({
                'apiKey': COINBASE_API_KEY,
                'secret': COINBASE_API_SECRET,
                'enableRateLimit': True,
            })
        else:
            exchange = ccxt.coinbase({'enableRateLimit': True})
            
        print("Recupero ticker live da Coinbase...")
        tickers = exchange.fetch_tickers()
        
        filtered_gainers = []
        for symbol, ticker in tickers.items():
            # Filtriamo solo le coin scambiate contro EUR o USD (es. BTC/EUR)
            if not ("/EUR" in symbol or "/USD" in symbol):
                continue
                
            price = ticker.get('last', 0)
            pct_change = ticker.get('percentage', 0)
            
            if price is None: price = 0
            if pct_change is None: pct_change = 0
            
            if price >= min_price and pct_change >= min_pct_change:
                filtered_gainers.append({
                    "symbol": symbol,
                    "price": price,
                    "changesPercentage": pct_change,
                    "volume": ticker.get('baseVolume', 0)
                })
        
        # Ordina per variazione % decrescente
        filtered_gainers.sort(key=lambda x: x["changesPercentage"], reverse=True)
        return filtered_gainers
        
    except Exception as e:
        print(f"Errore recupero Gainers da Coinbase: {e}")
        return []

if __name__ == "__main__":
    # Test
    print("Test Coinbase Radar Engine...")
    gainers = get_crypto_gainers(min_price=0.1, min_pct_change=1.0)
    print(f"Trovati {len(gainers)} gainers.")
    for g in gainers[:5]:
        print(f"{g['symbol']} | Prezzo: ${g['price']} | Var: +{g['changesPercentage']:.2f}%")
