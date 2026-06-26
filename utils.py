import time
import functools

def retry_with_backoff(max_retries=3, initial_delay=2, backoff_factor=2):
    """
    Decorator for exponential backoff.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    print(f"[RETRY] {func.__name__} failed (attempt {attempt+1}/{max_retries}): {e}")
                    if attempt == max_retries - 1:
                        print(f"[RETRY] Max retries reached for {func.__name__}.")
                        raise
                    time.sleep(delay)
                    delay *= backoff_factor
            return None
        return wrapper
    return decorator

def calculate_ema(prices, period):
    if not prices or len(prices) < period:
        return []
    
    k = 2 / (period + 1)
    emas = [sum(prices[:period]) / period] # SMA come primo valore
    
    for price in prices[period:]:
        emas.append((price - emas[-1]) * k + emas[-1])
        
    # Ritorniamo una lista lunga quanto i prezzi, riempiendo l'inizio con None
    return [None] * (len(prices) - len(emas)) + emas

def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal:
        return None, None, None
        
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    
    # Allineiamo tagliando i None iniziali di ema_slow
    valid_start = len(prices) - len([x for x in ema_slow if x is not None])
    
    macd_line = []
    for i in range(len(prices)):
        if ema_fast[i] is None or ema_slow[i] is None:
            macd_line.append(None)
        else:
            macd_line.append(ema_fast[i] - ema_slow[i])
            
    # Calcoliamo la signal line sul macd_line
    valid_macd = [x for x in macd_line if x is not None]
    if len(valid_macd) < signal:
        return None, None, None
        
    signal_line_valid = calculate_ema(valid_macd, signal)
    signal_line = [None] * (len(prices) - len(signal_line_valid)) + signal_line_valid
    
    histogram = []
    for i in range(len(prices)):
        if macd_line[i] is None or signal_line[i] is None:
            histogram.append(None)
        else:
            histogram.append(macd_line[i] - signal_line[i])
            
    return macd_line, signal_line, histogram

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
        
    gains = []
    losses = []
    
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
            
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    rsis = [None] * period
    if avg_loss == 0:
        rsis.append(100)
    else:
        rs = avg_gain / avg_loss
        rsis.append(100 - (100 / (1 + rs)))
        
    for i in range(period, len(prices) - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        if avg_loss == 0:
            rsis.append(100)
        else:
            rs = avg_gain / avg_loss
            rsis.append(100 - (100 / (1 + rs)))
            
    return rsis

