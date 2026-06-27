import time
import ccxt
from config import COINBASE_API_KEY, COINBASE_API_SECRET, TRADE_MODE, FEE_RATE
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
            return 100.0  # Valore mockato

        try:
            balance = self.exchange.fetch_balance()
            return balance['total'].get('EUR', 0)
        except Exception as e:
            print(f"Errore recupero bilancio: {e}")
            return 0.0

    @retry_with_backoff(max_retries=3)
    def get_tickers(self):
        return self.exchange.fetch_tickers()

    # ------------------------------------------------------------------
    # Normalizzazione fill reali (filled base, costo in EUR, fee in EUR)
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_fee_eur(order):
        """Estrae le commissioni pagate in EUR da un ordine ccxt."""
        try:
            fee = order.get('fee') or {}
            if fee and fee.get('cost') is not None:
                return abs(float(fee['cost']))
            total = 0.0
            for f in (order.get('fees') or []):
                if f and f.get('cost') is not None:
                    total += abs(float(f['cost']))
            return total
        except Exception:
            return 0.0

    def _fetch_filled_order(self, order, symbol):
        """Ricarica l'ordine per ottenere i fill effettivi (filled/cost/average)."""
        order_id = order.get('id') if isinstance(order, dict) else None
        if not order_id:
            return order
        # Coinbase puo' non popolare i fill nella risposta immediata: ricarichiamo.
        for _ in range(3):
            try:
                fetched = self.exchange.fetch_order(order_id, symbol)
                if fetched and fetched.get('filled'):
                    return fetched
            except Exception:
                pass
            time.sleep(1)
        return order

    @retry_with_backoff(max_retries=3)
    def execute_market_buy(self, symbol, amount_eur, ref_price=None):
        """Acquisto a mercato. Ritorna un fill normalizzato:
        {'id', 'filled_base', 'cost_eur' (EUR lordi spesi incl. fee),
         'fee_eur', 'avg_price'} oppure None in caso di errore.
        """
        if TRADE_MODE == "SIMULATION" or not COINBASE_API_KEY:
            price = ref_price or 1.0
            fee = amount_eur * FEE_RATE
            net = max(amount_eur - fee, 0.0)
            return {
                'id': 'sim_buy',
                'filled_base': net / price if price else 0.0,
                'cost_eur': amount_eur,
                'fee_eur': fee,
                'avg_price': price,
            }

        try:
            order = self.exchange.create_market_buy_order(symbol, amount_eur)
            order = self._fetch_filled_order(order, symbol)

            filled_base = float(order.get('filled') or 0.0)
            cost = order.get('cost')
            fee_eur = self._extract_fee_eur(order)
            avg_price = order.get('average') or ref_price

            if filled_base and avg_price is None:
                # Ricava avg dal costo
                avg_price = (float(cost) / filled_base) if cost else ref_price

            if not filled_base:
                # Fallback stima se i fill non sono disponibili
                price = avg_price or ref_price or 0.0
                fee_eur = fee_eur or (amount_eur * FEE_RATE)
                filled_base = (amount_eur - fee_eur) / price if price else 0.0
                cost = amount_eur - fee_eur

            cost_val = float(cost) if cost is not None else (amount_eur - fee_eur)
            gross_eur = cost_val + fee_eur  # totale EUR effettivamente usciti

            print(f"Ordine BUY eseguito: {symbol} | base={filled_base} | "
                  f"speso=€{gross_eur:.2f} | fee=€{fee_eur:.4f}", flush=True)
            return {
                'id': order.get('id'),
                'filled_base': filled_base,
                'cost_eur': gross_eur,
                'fee_eur': fee_eur,
                'avg_price': avg_price or (gross_eur / filled_base if filled_base else 0.0),
            }
        except Exception as e:
            print(f"Errore BUY Coinbase ({symbol}): {e}", flush=True)
            return None

    @retry_with_backoff(max_retries=3)
    def execute_market_sell(self, symbol, amount_base, ref_price=None):
        """Vendita a mercato. Ritorna un fill normalizzato:
        {'id', 'filled_base', 'proceeds_eur' (EUR netti incassati dopo fee),
         'fee_eur', 'avg_price'} oppure None in caso di errore.
        """
        if TRADE_MODE == "SIMULATION" or not COINBASE_API_KEY:
            price = ref_price or 1.0
            gross = amount_base * price
            fee = gross * FEE_RATE
            return {
                'id': 'sim_sell',
                'filled_base': amount_base,
                'proceeds_eur': max(gross - fee, 0.0),
                'fee_eur': fee,
                'avg_price': price,
            }

        try:
            order = self.exchange.create_market_sell_order(symbol, amount_base)
            order = self._fetch_filled_order(order, symbol)

            filled_base = float(order.get('filled') or amount_base)
            cost = order.get('cost')  # EUR lordi ricavati (base * prezzo)
            fee_eur = self._extract_fee_eur(order)
            avg_price = order.get('average') or ref_price

            if cost is None:
                price = avg_price or ref_price or 0.0
                cost = filled_base * price
            cost_val = float(cost)
            fee_eur = fee_eur or (cost_val * FEE_RATE)
            proceeds = max(cost_val - fee_eur, 0.0)

            print(f"Ordine SELL eseguito: {symbol} | base={filled_base} | "
                  f"incassato=€{proceeds:.2f} | fee=€{fee_eur:.4f}", flush=True)
            return {
                'id': order.get('id'),
                'filled_base': filled_base,
                'proceeds_eur': proceeds,
                'fee_eur': fee_eur,
                'avg_price': avg_price or (cost_val / filled_base if filled_base else 0.0),
            }
        except Exception as e:
            print(f"Errore SELL Coinbase ({symbol}): {e}", flush=True)
            return None

    @retry_with_backoff(max_retries=3)
    def get_sma(self, symbol, timeframe='5m', period=10):
        """Calcola la Simple Moving Average (SMA) dalle candele (OHLCV)."""
        try:
            candles = self.exchange.fetch_ohlcv(symbol, timeframe, limit=period)
            if not candles or len(candles) < period:
                return None
            closes = [candle[4] for candle in candles]
            return sum(closes) / len(closes)
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
            macd_bullish = False
            if (macd_line[-1] is not None and signal_line[-1] is not None
                    and histogram[-1] is not None):
                macd_bullish = macd_line[-1] > signal_line[-1] and histogram[-1] > 0

            return current_rsi, macd_bullish
        except Exception as e:
            print(f"Errore Indicators per {symbol}: {e}")
            return None, None
