"""Market Intelligence — aggrega segnali di mercato da piu' fonti per dare al
bot un "contesto" oltre al puro momentum tecnico.

Fonti attuali:
- Crypto Fear & Greed Index (alternative.me) — GRATUITO, nessuna chiave.
  Misura il sentiment aggregato del mercato crypto (0=panico, 100=euforia).
- FMP (momentum + news/sentiment) via FmpRadar — momentum gratis, news a pagamento.

Architettura estensibile: aggiungere una fonte = aggiungere un metodo che
ritorna un punteggio normalizzato e includerlo in get_regime()/get_asset_signal().
Tutte le chiamate sono resilienti (cache + mai sollevano eccezioni) cosi' una
fonte offline non blocca mai il trading.
"""
import time
import requests
from fmp_radar import FmpRadar
from config import GEOPOLITICAL_RISK_ENABLED, CRYPTOPANIC_API_KEY

_FNG_URL = "https://api.alternative.me/fng/?limit=1"
_GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"
_CACHE_TTL = 600       # 10 minuti (Fear&Greed, sentiment)
_GEO_TTL = 1800        # 30 minuti (GDELT e' rate-limited a 1 req/5s)

# Termini ACUTI di crisi (non semplice "regolamentazione", che e' sempre presente):
# eventi che davvero muovono i mercati. Usato come rilevatore di PICCHI.
_RISK_QUERY = '(crypto OR bitcoin) (hack OR exploit OR collapse OR sanctions OR crackdown OR "emergency" OR seized)'
_GEO_NORM = 60.0  # denominatore: in tempi normali questi termini sono pochi; un picco li satura


class MarketIntel:
    def __init__(self):
        self.fmp = FmpRadar()
        self._fng_cache = None      # (timestamp, dict)
        self._geo_cache = None      # (timestamp, dict)
        self._news_cache = {}       # base -> (timestamp, float|None)

    # ------------------------------------------------------------------
    # Fear & Greed Index (gratuito)
    # ------------------------------------------------------------------
    def get_fear_greed(self):
        """Ritorna {'value': int 0-100, 'label': str} oppure None se non disponibile."""
        now = time.time()
        if self._fng_cache and (now - self._fng_cache[0]) < _CACHE_TTL:
            return self._fng_cache[1]
        try:
            r = requests.get(_FNG_URL, timeout=10)
            if r.status_code != 200:
                return None
            d = (r.json().get("data") or [{}])[0]
            val = int(d.get("value"))
            out = {"value": val, "label": d.get("value_classification", "")}
            self._fng_cache = (now, out)
            return out
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Rischio geopolitico/regolatorio (GDELT, gratuito keyless)
    # ------------------------------------------------------------------
    def get_geopolitical_risk(self):
        """Stima il 'rumore' di rischio sui mercati crypto dalle news globali
        (ban, regolamentazioni, hack, sanzioni, guerra). Ritorna
        {'risk': 0.0-1.0, 'articles': int, 'samples': [..], 'available': bool}.
        Heuristica: piu' copertura su questi temi -> piu' rischio percepito.
        Cache 30 min (GDELT limita a 1 richiesta/5s)."""
        if not GEOPOLITICAL_RISK_ENABLED:
            return {"risk": 0.0, "articles": 0, "samples": [], "available": False}
        now = time.time()
        if self._geo_cache and (now - self._geo_cache[0]) < _GEO_TTL:
            return self._geo_cache[1]
        out = {"risk": 0.0, "articles": 0, "samples": [], "available": False}
        try:
            r = requests.get(_GDELT_URL, timeout=12, params={
                "query": _RISK_QUERY, "mode": "artlist", "maxrecords": 75,
                "format": "json", "timespan": "1d", "sort": "datedesc"})
            if r.status_code == 200:
                arts = r.json().get("articles", []) or []
                out = {
                    "risk": min(len(arts) / _GEO_NORM, 1.0),
                    "articles": len(arts),
                    "samples": [a.get("title", "")[:90] for a in arts[:3]],
                    "available": True,
                }
                self._geo_cache = (now, out)
        except Exception:
            pass
        return out

    # ------------------------------------------------------------------
    # Sentiment news per-asset (CryptoPanic, chiave gratuita)
    # ------------------------------------------------------------------
    def get_news_sentiment(self, base):
        """Sentiment news per un asset da CryptoPanic. >0 bullish, <0 bearish,
        None se non disponibile (manca la chiave o nessuna news)."""
        if not CRYPTOPANIC_API_KEY:
            return None
        now = time.time()
        cached = self._news_cache.get(base)
        if cached and (now - cached[0]) < _CACHE_TTL:
            return cached[1]
        sentiment = None
        try:
            r = requests.get(_CRYPTOPANIC_URL, timeout=10, params={
                "auth_token": CRYPTOPANIC_API_KEY, "currencies": base, "public": "true"})
            if r.status_code == 200:
                posts = r.json().get("results", []) or []
                score = 0
                for p in posts[:20]:
                    v = p.get("votes", {}) or {}
                    score += (v.get("positive", 0) - v.get("negative", 0))
                    if str(p.get("kind")) == "news":
                        title = (p.get("title") or "").lower()
                        if any(w in title for w in ("hack", "ban", "lawsuit", "crash", "dump")):
                            score -= 1
                sentiment = score / max(len(posts[:20]), 1) if posts else 0.0
        except Exception:
            sentiment = None
        self._news_cache[base] = (now, sentiment)
        return sentiment

    # ------------------------------------------------------------------
    # Regime di mercato -> moltiplicatore di rischio per il position sizing
    # ------------------------------------------------------------------
    def get_regime(self):
        """Sintetizza un 'regime' di mercato e un moltiplicatore di rischio
        (0.0-1.0) che il bot puo' usare per scalare la size delle posizioni.

        Logica (crypto, contrarian sui due estremi):
        - Extreme Greed (>=80): mercato surriscaldato -> riduci rischio.
        - Greed (60-79): normale, rischio pieno.
        - Neutral (40-59): rischio pieno.
        - Fear (25-39): cautela moderata.
        - Extreme Fear (<25): possibile 'falling knife' -> rischio ridotto.
        """
        fng = self.get_fear_greed()
        if fng is None:
            return {"available": False, "risk_multiplier": 1.0,
                    "regime": "unknown", "fear_greed": None, "fear_greed_label": ""}

        v = fng["value"]
        if v >= 80:
            regime, mult = "euforia (surriscaldato)", 0.5
        elif v >= 60:
            regime, mult = "ottimismo", 1.0
        elif v >= 40:
            regime, mult = "neutro", 1.0
        elif v >= 25:
            regime, mult = "paura", 0.75
        else:
            regime, mult = "panico (falling knife)", 0.5

        # Rischio geopolitico: se alto, riduce ulteriormente la size
        geo = self.get_geopolitical_risk()
        geo_risk = geo.get("risk", 0.0) if geo.get("available") else 0.0
        if geo_risk >= 0.8:
            mult *= 0.5
            regime += " + alto rischio news"
        elif geo_risk >= 0.5:
            mult *= 0.75
            regime += " + rischio news"

        return {
            "available": True,
            "risk_multiplier": round(mult, 3),
            "regime": regime,
            "fear_greed": v,
            "fear_greed_label": fng["label"],
            "geopolitical_risk": round(geo_risk, 2),
            "geo_samples": geo.get("samples", []),
        }

    # ------------------------------------------------------------------
    # Segnale per singolo asset (delega a FMP)
    # ------------------------------------------------------------------
    def get_asset_signal(self, base):
        """Segnale aggregato per un asset: momentum FMP + sentiment news
        (FMP se a pagamento, altrimenti CryptoPanic gratuito).
        {'fmp_change', 'news_sentiment', 'available'}."""
        try:
            sig = self.fmp.get_signal(base)
        except Exception:
            sig = {"fmp_change": None, "news_sentiment": None, "available": False}
        # Se FMP non da' sentiment (news a pagamento), prova CryptoPanic gratuito
        if sig.get("news_sentiment") is None:
            cp = self.get_news_sentiment(base)
            if cp is not None:
                sig["news_sentiment"] = cp
                sig["available"] = True
        return sig
