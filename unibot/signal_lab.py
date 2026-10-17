"""Banco di prova per segnali datati (trade del Congresso, insider, canali, ecc.).

Domanda a cui risponde: "se avessi comprato DOPO la pubblicazione del segnale, come
sarebbe andata rispetto al mercato (SPY) a 1, 5 e 20 giorni di borsa?"

Regole per evitare di ingannarsi da soli:
- il segnale diventa noto alla data di PUBBLICAZIONE, non a quella dell'operazione
  (per il Congresso i due giorni possono distare settimane). Se manca la data di
  pubblicazione si applica un ritardo prudente (lag_days) e il report lo segnala;
- si entra alla chiusura del primo giorno di borsa SUCCESSIVO alla data nota;
- i rendimenti sono confrontati con SPY (alpha), non in assoluto;
- con meno di 30 segnali per orizzonte il risultato e' dichiarato non conclusivo.

Fonti accettate: export CSV/JSON di Quiver Quant (colonne Ticker, ReportDate,
TransactionDate, Transaction, Representative...), dati FMP, o liste manuali.
"""
import bisect
import csv
import datetime
import json
import math
import os
import re
import time

from . import settings as S

HORIZONS = (1, 5, 20)
MIN_SAMPLE = 30

_ALIASES = {
    "symbol": ("symbol", "ticker"),
    "event": ("transactiondate", "tradedate", "eventdate", "traded"),
    "side": ("side", "transaction", "type", "transactiontype"),
    "source": ("source", "representative", "senator", "politician", "name", "insider"),
}
# Solo colonne che indicano DAVVERO quando il segnale e' diventato pubblico.
# Una generica colonna "date" puo' essere la data dell'operazione: NON viene usata.
_KNOWN_KEYS = ("knowndate", "reportdate", "disclosuredate", "filingdate", "filed", "published", "publisheddate")


def _key(k):
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def _norm_date(v):
    """Data -> 'YYYY-MM-DD'. Formati: ISO (anche con ora) e MM/DD/YYYY (stile USA)."""
    if not v:
        return None
    s = str(v).strip()
    for text, fmt in ((s[:10], "%Y-%m-%d"), (s, "%m/%d/%Y")):
        try:
            return datetime.datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _norm_side(v):
    s = str(v or "").strip().lower()
    if s.startswith(("purchase", "buy", "bought")) or s == "p":
        return "buy"
    if s.startswith(("sale", "sell", "sold")) or s == "s":
        return "sell"
    return None


def normalize(rows, default_source="import", lag_days=30):
    """Righe grezze -> segnali {symbol, known_date, event_date, side, source, assumed_lag}."""
    out = []
    for raw in rows:
        low = {_key(k): v for k, v in raw.items()}

        def pick(name):
            for a in _ALIASES[name]:
                if low.get(a) not in (None, ""):
                    return low[a]
            return None

        symbol = str(pick("symbol") or "").upper().strip()
        side = _norm_side(pick("side"))
        if not symbol or not side or symbol in ("-", "N/A"):
            continue
        event = _norm_date(pick("event"))
        known = next((d for d in (_norm_date(low.get(k)) for k in _KNOWN_KEYS) if d), None)
        assumed = False
        if known is None:
            base = event or _norm_date(low.get("date"))
            if base is None:
                continue
            known = (datetime.datetime.strptime(base, "%Y-%m-%d") + datetime.timedelta(days=lag_days)).strftime("%Y-%m-%d")
            assumed = True
        out.append({"symbol": symbol, "known_date": known, "event_date": event, "side": side,
                    "source": str(pick("source") or default_source)[:60], "assumed_lag": assumed})
    return out


def load_signals(path, default_source=None, lag_days=30):
    src = default_source or os.path.splitext(os.path.basename(path))[0]
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        if path.lower().endswith(".json"):
            data = json.load(f)
            rows = data if isinstance(data, list) else data.get("data", [])
        else:
            rows = list(csv.DictReader(f))
    return normalize(rows, src, lag_days)


QUIVER_BULK_URL = "https://api.quiverquant.com/beta/bulk/congresstrading"


def load_quiver_api(token, lag_days=30, http=None, page_size=500, max_pages=400, retry_delay=3.0):
    """Storico completo dei trade del Congresso dall'endpoint ufficiale 'bulk' di Quiver.

    Basato sulla documentazione ufficiale (GET /beta/bulk/congresstrading, Bearer token, parametri
    page/page_size; campi Filed = data di pubblicazione, Traded = data dell'operazione). Richiede un
    abbonamento API; il token va nella variabile QUIVER_API_TOKEN (mai in chat). NON ancora provato
    con un token reale: se la risposta non e' una lista JSON o il token e' rifiutato, solleva un errore chiaro.
    """
    if http is None:
        import requests as http
    rows, page = [], 1
    while page <= max_pages:
        data = None
        for attempt in range(4):
            try:
                r = http.get(QUIVER_BULK_URL, headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
                             params={"page": page, "page_size": page_size, "version": "V2"}, timeout=60)
            except Exception:  # noqa: BLE001 - rete
                time.sleep(retry_delay * (attempt + 1))
                continue
            if r.status_code in (401, 403):
                raise RuntimeError("token Quiver rifiutato (HTTP %d): controlla il token e che il piano includa l'API"
                                   % r.status_code)
            if r.status_code == 429:
                time.sleep(retry_delay * 3 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise RuntimeError("Quiver ha risposto HTTP %d" % r.status_code)
            data = r.json()
            break
        if data is None:
            raise RuntimeError("Quiver non raggiungibile alla pagina %d" % page)
        if not isinstance(data, list):
            raise RuntimeError("risposta Quiver inattesa (non e' una lista)")
        rows.extend(data)
        if len(data) < page_size:
            break
        page += 1
    return normalize(rows, "quiver-congress", lag_days)


class PriceHistory:
    """Chiusure giornaliere da FMP (piano gratuito) con cache su disco e tetto di chiamate."""

    def __init__(self, fmp, cache_dir="unibot_prices", max_calls=150, clock=time.time, retry_delay=2.0):
        self.fmp, self.cache_dir, self.max_calls, self.clock = fmp, cache_dir, max_calls, clock
        self.retry_delay = retry_delay
        self.calls = 0
        self.failed = set()           # titoli davvero senza dati (risposta vuota o a pagamento)
        self.network_failed = set()   # titoli non scaricati per errori di rete: da riprovare
        os.makedirs(cache_dir, exist_ok=True)

    def _path(self, symbol):
        return os.path.join(self.cache_dir, re.sub(r"[^A-Za-z0-9_.-]", "_", symbol) + ".json")

    def get(self, symbol, since):
        """Lista [(data, chiusura)] crescente, oppure None se non disponibile."""
        path = self._path(symbol)
        if os.path.exists(path) and self.clock() - os.path.getmtime(path) < 86400:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return [tuple(x) for x in json.load(f)]
            except (ValueError, OSError):
                pass
        if symbol in self.failed or self.calls >= self.max_calls:
            return None
        today = datetime.datetime.fromtimestamp(self.clock(), datetime.timezone.utc).strftime("%Y-%m-%d")
        data = None
        for attempt in range(3):              # un errore di rete non e' "nessun dato": si ritenta
            self.calls += 1
            data = self.fmp.get("historical-price-eod/full", symbol=symbol, **{"from": since, "to": today})
            if data is not None or getattr(self.fmp, "last_error", None) != "network":
                break
            time.sleep(self.retry_delay * (attempt + 1))
        if not isinstance(data, list) or not data:
            if getattr(self.fmp, "last_error", None) == "network":
                self.network_failed.add(symbol)
            else:
                self.failed.add(symbol)
            return None
        self.network_failed.discard(symbol)
        series = sorted({(r["date"], float(r.get("close") or r.get("price"))) for r in data
                         if r.get("date") and (r.get("close") or r.get("price"))})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(series, f)
        return series


def _stats(vals):
    n = len(vals)
    if n == 0:
        return {"n": 0}
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1)) if n > 1 else 0.0
    ordered = sorted(vals)
    median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    return {"n": n, "mean": mean, "median": median, "sd": sd, "hit": sum(1 for v in vals if v > 0) / n,
            "t": (mean / (sd / math.sqrt(n))) if sd > 0 else 0.0}


class YahooHistory:
    """Chiusure giornaliere rettificate (split e dividendi) da Yahoo Finance, con cache su disco.

    ATTENZIONE: endpoint NON ufficiale, senza garanzie e con termini d'uso restrittivi: va bene
    per ricerca personale, non per un servizio. I titoli che hanno cessato le contrattazioni
    spesso non hanno dati: l'esclusione crea un 'survivorship bias' (risultati troppo rosei),
    quindi il chiamante deve misurare quanti titoli mancano.
    """
    URL = "https://query1.finance.yahoo.com/v8/finance/chart/%s"

    def __init__(self, cache_dir="unibot_prices", start="2024-12-01", http=None, clock=time.time,
                 retry_delay=2.0, pause=0.25, max_age=86400):
        self.cache_dir, self.start, self.clock = cache_dir, start, clock
        self.max_age = max_age        # secondi di validita' della cache su disco
        self.http = http
        self.retry_delay, self.pause = retry_delay, pause
        self.failed = set()           # nessun dato (titolo sconosciuto o ritirato)
        self.network_failed = set()   # errori di rete/limiti: da riprovare
        self._mem = {}
        os.makedirs(cache_dir, exist_ok=True)

    def _path(self, symbol):
        return os.path.join(self.cache_dir, "yahoo_" + re.sub(r"[^A-Za-z0-9_.-]", "_", symbol) + ".json")

    def get(self, symbol, since=None):
        if symbol in self._mem:
            return self._mem[symbol]
        series = self._get(symbol)
        if series is not None:
            self._mem[symbol] = series
        return series

    def _get(self, symbol):
        path = self._path(symbol)
        if os.path.exists(path) and self.clock() - os.path.getmtime(path) < self.max_age:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return [tuple(x) for x in json.load(f)] or None
            except (ValueError, OSError):
                pass
        if symbol in self.failed:
            return None
        http = self.http
        if http is None:
            import requests as http
        p1 = int(datetime.datetime.strptime(self.start, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp())
        p2 = int(self.clock()) + 86400
        err = None
        for attempt in range(4):
            try:
                time.sleep(self.pause)
                r = http.get(self.URL % symbol, params={"period1": p1, "period2": p2, "interval": "1d"},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
            except Exception:
                err = "network"
                time.sleep(self.retry_delay * (attempt + 1))
                continue
            if r.status_code == 429:
                err = "network"
                time.sleep(self.retry_delay * 3 * (attempt + 1))
                continue
            if r.status_code == 404:
                self.failed.add(symbol)
                return None
            if r.status_code != 200:
                err = "network"
                time.sleep(self.retry_delay * (attempt + 1))
                continue
            try:
                res = (r.json().get("chart", {}).get("result") or [None])[0]
                ts = res["timestamp"]
                ind = res["indicators"]
                adj = (ind.get("adjclose") or [{}])[0].get("adjclose") or ind["quote"][0]["close"]
            except (ValueError, KeyError, TypeError, IndexError):
                self.failed.add(symbol)
                return None
            series = sorted({(datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%d"), float(c))
                             for t, c in zip(ts, adj) if c})
            if not series:
                self.failed.add(symbol)
                return None
            with open(path, "w", encoding="utf-8") as f:
                json.dump(series, f)
            self.network_failed.discard(symbol)
            return series
        if err == "network":
            self.network_failed.add(symbol)
        return None


def evaluate(signals, prices, horizons=HORIZONS, benchmark="SPY"):
    """Rendimento e alpha vs benchmark dopo ogni segnale, aggregato per lato e orizzonte."""
    since = None
    if signals:
        first = min(s["known_date"] for s in signals)
        since = (datetime.datetime.strptime(first, "%Y-%m-%d") - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
    bench = prices.get(benchmark, since) if signals else None
    skipped = {"senza_prezzi": 0, "errore_rete": 0, "troppo_recente": 0}
    buckets = {}
    assumed = sum(1 for s in signals if s.get("assumed_lag"))
    if bench is None:
        return {"error": "benchmark %s non disponibile" % benchmark, "skipped": skipped,
                "n_signals": len(signals), "assumed_lag": assumed, "stats": {}}
    bdates = [d for d, _ in bench]

    def fwd(series_dates, series_close, entry_date, h):
        i = bisect.bisect_right(series_dates, entry_date)  # primo giorno di borsa successivo
        if i >= len(series_dates) or i + h >= len(series_dates):
            return None
        return series_close[i + h] / series_close[i] - 1

    for s in signals:
        series = prices.get(s["symbol"], since)
        if not series:
            net = s["symbol"] in getattr(prices, "network_failed", ())
            skipped["errore_rete" if net else "senza_prezzi"] += 1
            continue
        dates, closes = [d for d, _ in series], [c for _, c in series]
        if bisect.bisect_right(dates, s["known_date"]) >= len(dates):
            skipped["troppo_recente"] += 1
            continue
        for h in horizons:
            r = fwd(dates, closes, s["known_date"], h)
            b = fwd(bdates, [c for _, c in bench], s["known_date"], h)
            if r is None or b is None:
                continue
            buckets.setdefault((s["side"], h), {"ret": [], "alpha": []})
            buckets[(s["side"], h)]["ret"].append(r)
            buckets[(s["side"], h)]["alpha"].append(r - b)
    stats = {k: {"ret": _stats(v["ret"]), "alpha": _stats(v["alpha"])} for k, v in buckets.items()}
    return {"stats": stats, "skipped": skipped, "n_signals": len(signals), "assumed_lag": assumed,
            "raw": buckets}


def format_report(res, horizons=HORIZONS):
    if res.get("error"):
        return "ERRORE: " + res["error"]
    L = ["=== SIGNAL LAB: %d segnali | scartati: %s ===" % (
        res["n_signals"], ", ".join("%s %d" % kv for kv in res["skipped"].items()))]
    if res["assumed_lag"]:
        L.append("ATTENZIONE: %d segnali senza data di pubblicazione (ritardo prudente applicato)." % res["assumed_lag"])
    if res["skipped"].get("senza_prezzi"):
        L.append("Nota: %d segnali su titoli senza prezzi: sconosciuti a FMP o NON inclusi nel piano gratuito "
                 "(HTTP 402, il piano copre solo alcuni titoli). Non sono errori di rete." % res["skipped"]["senza_prezzi"])
    if res["skipped"].get("errore_rete"):
        L.append("ATTENZIONE: %d segnali non valutati per errori di rete (non per assenza di dati): rilancia, "
                 "la cache evita di riscaricare i titoli gia' letti." % res["skipped"]["errore_rete"])
    L.append("%-5s %4s %5s %9s %9s %7s %6s  %s" % ("lato", "gg", "n", "rend%", "alpha%", "hit%", "t", "giudizio"))
    for side in ("buy", "sell"):
        for h in horizons:
            d = res["stats"].get((side, h))
            if not d or not d["alpha"]["n"]:
                L.append("%-5s %4d %5d  (nessun dato maturo)" % (side, h, 0))
                continue
            a, r = d["alpha"], d["ret"]
            verdict = "non conclusivo (n<%d)" % MIN_SAMPLE if a["n"] < MIN_SAMPLE else (
                "alpha significativo" if abs(a["t"]) >= 2 else "indistinguibile dal caso")
            t_txt = "%6.2f" % a["t"] if a["n"] >= 5 else "   n/d"     # con pochi campioni il t e' privo di senso
            L.append("%-5s %4d %5d %+8.2f%% %+8.2f%% %6.1f%% %s  %s" % (
                side, h, a["n"], r["mean"] * 100, a["mean"] * 100, a["hit"] * 100, t_txt, verdict))
    L.append("Alpha = rendimento meno SPY, entrata alla chiusura del primo giorno di borsa dopo la pubblicazione.")
    L.append("Segnali ravvicinati sullo stesso titolo non sono indipendenti: il t-test e' ottimistico.")
    return "\n".join(L)


def main_cli(path):
    from .universe import FmpClient
    lag = 30
    if path == "quiver-api":
        token = os.getenv("QUIVER_API_TOKEN", "")
        if not token:
            print("QUIVER_API_TOKEN non impostato (metterlo nel file .env, mai in chat).")
            return 1
        signals = load_quiver_api(token, lag)
    else:
        signals = load_signals(path, lag_days=lag)
    if not signals:
        print("nessun segnale valido nel file (servono almeno simbolo, data e lato acquisto/vendita)")
        return 1
    symbols = {s["symbol"] for s in signals}
    print("%d segnali su %d titoli; storico prezzi FMP (gratuito, max %d chiamate, cache su disco)" % (
        len(signals), len(symbols), 150))
    prices = PriceHistory(FmpClient(budget=S.FMP_DAILY_BUDGET))
    res = evaluate(signals, prices)
    print(format_report(res))
    if prices.calls >= prices.max_calls:
        print("Tetto di chiamate raggiunto: rilancia domani, la cache evita di riscaricare i titoli gia' letti.")
    return 0
