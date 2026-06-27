"""Radar FMP v2 — usa la nuova API 'stable' di Financial Modeling Prep.

- Momentum/quote crypto: disponibile sul piano gratuito (endpoint stable/quote).
- News/sentiment: richiede piano FMP a pagamento (free => HTTP 402). Se non
  disponibile, degrada in modo trasparente (ritorna None = neutro).

Il radar e' un segnale SOFT: arricchisce/conferma la selezione, non blocca mai
il trading se FMP e' irraggiungibile.
"""
import time
import requests
from config import FMP_API_KEY, FMP_ENABLED, FMP_NEWS_ENABLED

BASE = "https://financialmodelingprep.com/stable"
_CACHE_TTL = 600  # 10 minuti


class FmpRadar:
    def __init__(self):
        self.enabled = bool(FMP_ENABLED and FMP_API_KEY)
        self._cache = {}             # base -> (timestamp, dict)
        self._news_unavailable = False  # True dopo un 402 (evita richieste inutili)

    def _get(self, path, params=None):
        params = dict(params or {})
        params["apikey"] = FMP_API_KEY
        r = requests.get(f"{BASE}/{path}", params=params, timeout=12)
        if r.status_code == 402:
            return ("PAID", None)
        if r.status_code != 200:
            return ("ERR", None)
        try:
            return ("OK", r.json())
        except ValueError:
            return ("ERR", None)

    def get_signal(self, base):
        """Ritorna {'fmp_change': float|None, 'news_sentiment': float|None,
        'available': bool}. Cache 10 min. Non solleva mai eccezioni."""
        if not self.enabled:
            return {"fmp_change": None, "news_sentiment": None, "available": False}

        now = time.time()
        cached = self._cache.get(base)
        if cached and (now - cached[0]) < _CACHE_TTL:
            return cached[1]

        signal = {"fmp_change": None, "news_sentiment": None, "available": True}
        try:
            status, data = self._get("quote", {"symbol": f"{base}USD"})
            if status == "OK" and isinstance(data, list) and data:
                q = data[0]
                signal["fmp_change"] = q.get("changePercentage") or q.get("changesPercentage")
        except Exception:
            pass

        if FMP_NEWS_ENABLED and not self._news_unavailable:
            try:
                signal["news_sentiment"] = self._news_sentiment(base)
            except Exception:
                signal["news_sentiment"] = None

        self._cache[base] = (now, signal)
        return signal

    def _news_sentiment(self, base):
        """Sentiment grezzo dalle news (richiede FMP a pagamento). None se non disponibile."""
        status, data = self._get("news/crypto", {"symbols": f"{base}USD", "limit": 10})
        if status == "PAID":
            self._news_unavailable = True
            return None
        if status != "OK" or not isinstance(data, list) or not data:
            return None
        pos = ("surge", "rally", "soar", "bull", "gain", "high", "adopt", "partnership", "launch", "approve")
        neg = ("crash", "drop", "plunge", "bear", "loss", "hack", "lawsuit", "ban", "dump", "exploit")
        score = 0
        items = data[:10]
        for art in items:
            title = (art.get("title") or "").lower()
            score += sum(1 for w in pos if w in title)
            score -= sum(1 for w in neg if w in title)
        return score / max(len(items), 1)  # >0 bullish, <0 bearish


# Compat: vecchia firma usata da main.py legacy (non piu' nel path attivo)
def get_crypto_gainers(*args, **kwargs):
    return []
