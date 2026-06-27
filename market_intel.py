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

_FNG_URL = "https://api.alternative.me/fng/?limit=1"
_CACHE_TTL = 600  # 10 minuti


class MarketIntel:
    def __init__(self):
        self.fmp = FmpRadar()
        self._fng_cache = None      # (timestamp, dict)

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

        return {
            "available": True,
            "risk_multiplier": mult,
            "regime": regime,
            "fear_greed": v,
            "fear_greed_label": fng["label"],
        }

    # ------------------------------------------------------------------
    # Segnale per singolo asset (delega a FMP)
    # ------------------------------------------------------------------
    def get_asset_signal(self, base):
        """{'fmp_change', 'news_sentiment', 'available'} per un asset specifico."""
        try:
            return self.fmp.get_signal(base)
        except Exception:
            return {"fmp_change": None, "news_sentiment": None, "available": False}
