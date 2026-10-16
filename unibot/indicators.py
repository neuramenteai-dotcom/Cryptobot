"""Indicatori tecnici: riusa le implementazioni gia' presenti in utils.py."""
from utils import calculate_rsi, calculate_macd


def sma(closes, n):
    if not closes or len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def rsi_last(closes, period=14):
    series = calculate_rsi(closes, period)
    if not series:
        return None
    return series[-1]


def macd_bullish(closes):
    """True/False se calcolabile, None se i dati sono insufficienti."""
    macd_line, signal_line, hist = calculate_macd(closes)
    if not macd_line:
        return None
    if macd_line[-1] is None or signal_line[-1] is None or hist[-1] is None:
        return False
    return macd_line[-1] > signal_line[-1] and hist[-1] > 0
