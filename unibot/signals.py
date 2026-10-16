"""Casella dei segnali esterni (NON verificati).

Un segnale e' un suggerimento inserito dall'utente (es. da un canale Telegram o
da una pagina social). Regole di sicurezza:
- non apre mai posizioni da solo: il titolo deve comunque passare tutti i filtri
  e i limiti di rischio della strategia;
- al massimo alza la priorita' (bonus di punteggio) o, con side='avoid', blocca l'ingresso;
- il contenuto e' trattato come dato, mai come istruzione.
"""
import json
import os
import time

from . import settings as S


class Signals:
    def __init__(self, path=None, clock=time.time):
        self.path = path or S.SIGNALS_PATH
        self.clock = clock

    def _load(self):
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (ValueError, OSError):
            return []

    def _save(self, items):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

    def add(self, symbol, source, note="", side="buy", ttl_hours=None):
        side = side if side in ("buy", "avoid") else "buy"
        ttl = S.SIGNAL_TTL_HOURS if ttl_hours is None else ttl_hours
        now = self.clock()
        items = [s for s in self._load() if s.get("expires", 0) > now]
        item = {"symbol": symbol.upper().strip(), "source": source.strip()[:60], "note": note.strip()[:200],
                "side": side, "added": now, "expires": now + ttl * 3600}
        items.append(item)
        self._save(items)
        return item

    def active(self):
        now = self.clock()
        return [s for s in self._load() if s.get("expires", 0) > now]

    def lookup(self, symbol):
        """Segnale attivo per un simbolo ('SOL/USDC' o 'NVDA'): prima corrispondenza esatta, poi sulla base."""
        base = symbol.split("/")[0].upper()
        sym = symbol.upper()
        for s in self.active():
            if s["symbol"] in (sym, base):
                return s
        return None

    def unmatched(self, seen_symbols):
        """Segnali attivi che non corrispondono a nessun simbolo visto nel mercato osservato."""
        seen = set()
        for s in seen_symbols:
            seen.add(s.upper())
            seen.add(s.split("/")[0].upper())
        return [s for s in self.active() if s["symbol"] not in seen]
