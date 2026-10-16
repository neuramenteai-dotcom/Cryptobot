"""Universi di mercato: crypto (Coinbase via ccxt) e azioni USA (radar FMP).

Ogni universo espone la stessa interfaccia minima usata dall'engine:
scan() -> [Candidate], price(symbol) -> float|None, e i dati per gli indicatori.
"""
import datetime
import re
import time
from zoneinfo import ZoneInfo

import requests

from . import settings as S
from .models import Candidate

NY = ZoneInfo("America/New_York")


def retry(fn, tries=3, delay=2.0):
    """Riprova con attesa crescente: la rete puo' cadere per pochi secondi (DNS)."""
    last = None
    for k in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - errori di rete di librerie diverse
            last = e
            if k < tries - 1:
                time.sleep(delay * (k + 1))
    raise last

# Stessa regex del radar Chrome: esclude ETF a leva e simili
ETF_RE = re.compile(
    r"\b\d+(\.\d+)?x\b|\betf\b|\bbull\b|\bbear\b|daily target|leverage shares|graniteshares|"
    r"direxion|proshares|defiance|tradr|leveraged|ultrapro|invesco|ishares|trust\b", re.I)


def market_state(ts):
    """Stato della borsa USA (NY, lun-ven 9:30-16:00). Le festivita' non sono modellate:
    li' interviene il rilevatore di dati fermi dell'universo azioni."""
    dt = datetime.datetime.fromtimestamp(ts, NY)
    day = dt.strftime("%Y-%m-%d")
    closed = {"open": False, "day": day, "since_open": None, "to_close": None}
    if dt.weekday() >= 5:
        return closed
    mins = dt.hour * 60 + dt.minute + dt.second / 60.0
    open_m, close_m = 9 * 60 + 30, 16 * 60
    if not (open_m <= mins < close_m):
        return closed
    return {"open": True, "day": day, "since_open": mins - open_m, "to_close": close_m - mins}


# ----------------------------------------------------------------------
# Crypto
# ----------------------------------------------------------------------
class CryptoUniverse:
    def __init__(self, exchange, cfg=S, clock=time.time, retry_delay=2.0):
        self.ex = exchange
        self.cfg = cfg
        self.clock = clock
        self.retry_delay = retry_delay
        self.tickers = {}
        self._candles = {}      # symbol -> (ts, closes)
        self.errors = 0

    def scan(self):
        cfg = self.cfg
        self.tickers = retry(self.ex.fetch_tickers, 3, self.retry_delay)
        out = []
        for sym, t in self.tickers.items():
            if "/" not in sym:
                continue
            base, quote = sym.split("/", 1)
            if quote not in cfg.CRYPTO_QUOTES or base in cfg.CRYPTO_EXCLUDED_BASES:
                continue
            price, pct = t.get("last"), t.get("percentage")
            if not price or pct is None:
                continue
            vq = t.get("quoteVolume") or (t.get("baseVolume") or 0) * price
            if not (cfg.CRYPTO_MIN_PCT <= pct <= cfg.CRYPTO_MAX_PCT) or vq < cfg.CRYPTO_MIN_QUOTE_VOLUME:
                continue
            out.append(Candidate(sym, "crypto", quote, float(price), float(pct), float(vq)))
        out.sort(key=lambda c: c.change_pct, reverse=True)
        return out[:cfg.CRYPTO_TOP_N]

    def price(self, symbol):
        t = self.tickers.get(symbol) or {}
        return t.get("last")

    def usdc_eur(self):
        for sym in ("USDC/EUR",):
            t = self.tickers.get(sym)
            if t and t.get("last"):
                return float(t["last"])
        return None

    def closes(self, symbol):
        """Chiusure recenti con cache (riduce le chiamate e il rate-limit di Coinbase)."""
        now = self.clock()
        cached = self._candles.get(symbol)
        if cached and now - cached[0] < self.cfg.CRYPTO_CANDLE_TTL:
            return cached[1]
        try:
            rows = self.ex.fetch_ohlcv(symbol, self.cfg.CRYPTO_TIMEFRAME, limit=100)
            closes = [float(r[4]) for r in rows] if rows else None
        except Exception:
            self.errors += 1
            closes = None
        self._candles[symbol] = (now, closes)
        return closes


# ----------------------------------------------------------------------
# Azioni USA (FMP)
# ----------------------------------------------------------------------
class FmpClient:
    BASE = "https://financialmodelingprep.com/stable/"

    def __init__(self, key=None, budget=None, http=None, clock=time.time, cfg=S):
        self.key = cfg.FMP_API_KEY if key is None else key
        self.budget = cfg.FMP_DAILY_BUDGET if budget is None else budget
        self.http = http or requests
        self.clock = clock
        self._day = None
        self.calls = 0
        self.last_error = None    # None | 'budget' | 'blocked' | 'network' | 'http_NNN' | 'parse' | 'api'
        self.blocked = set()      # (endpoint, simbolo) non inclusi nel piano (HTTP 402): non ritentare.
        # Il 402 puo' valere per UN titolo (es. AZO non coperto) senza riguardare l'endpoint intero.

    def _roll(self):
        day = datetime.datetime.fromtimestamp(self.clock(), datetime.timezone.utc).strftime("%Y-%m-%d")
        if day != self._day:
            self._day, self.calls = day, 0

    def remaining(self):
        self._roll()
        return max(self.budget - self.calls, 0)

    def get(self, path, **params):
        self.last_error = None
        block_key = (path, params.get("symbol"))
        if not self.key or block_key in self.blocked:
            self.last_error = "blocked"
            return None
        self._roll()
        if self.calls >= self.budget:
            self.last_error = "budget"
            return None
        self.calls += 1
        try:
            r = self.http.get(self.BASE + path, params=dict(params, apikey=self.key), timeout=15)
        except Exception:
            self.last_error = "network"
            return None
        if r.status_code == 402:
            self.blocked.add(block_key)
            self.last_error = "blocked"
            return None
        if r.status_code != 200:
            self.last_error = "http_%d" % r.status_code
            return None
        try:
            data = r.json()
        except ValueError:
            self.last_error = "parse"
            return None
        if isinstance(data, dict) and "Error Message" in data:
            self.last_error = "api"
            return None
        return data

    def movers(self, kind):
        data = self.get(kind)
        return data if isinstance(data, list) else []

    def quote(self, symbol):
        data = self.get("quote", symbol=symbol)
        return data[0] if isinstance(data, list) and data else None


class StockUniverse:
    def __init__(self, fmp, cfg=S, clock=time.time):
        self.fmp = fmp
        self.cfg = cfg
        self.clock = clock
        self.tape = {}            # symbol -> prezzi campionati (piu' recente per ultimo)
        self.prices = {}          # symbol -> (ts, price)
        self.stale = False
        self._sig = None
        self._spy = (0.0, None)   # (ts, variazione %)
        self._tradable = {}       # (giorno, simbolo) -> bool

    def _push(self, symbol, price):
        t = self.tape.setdefault(symbol, [])
        t.append(float(price))
        del t[:-60]
        self.prices[symbol] = (self.clock(), float(price))

    def tape_prices(self, symbol):
        return list(self.tape.get(symbol, []))

    def scan(self):
        cfg = self.cfg
        gainers = self.fmp.movers("biggest-gainers")
        actives = self.fmp.movers("most-actives")

        sig = tuple((r.get("symbol"), r.get("price")) for r in gainers[:5])
        self.stale = (not gainers) or (sig == self._sig)
        self._sig = sig

        seen = {}
        for r in gainers + actives:
            if r.get("symbol") and r.get("price"):
                seen[r["symbol"]] = r["price"]
        for sym, price in seen.items():
            self._push(sym, price)

        active_syms = {r.get("symbol") for r in actives}
        out, extra_quotes = [], 0
        for r in gainers:
            sym, price, pct = r.get("symbol"), r.get("price"), r.get("changesPercentage")
            if not sym or not price or pct is None:
                continue
            if r.get("exchange") not in cfg.STOCK_EXCHANGES or ETF_RE.search(r.get("name") or ""):
                continue
            if price < cfg.STOCK_MIN_PRICE or not (cfg.STOCK_MIN_PCT <= pct <= cfg.STOCK_MAX_PCT):
                continue
            liquid = sym in active_syms
            if not liquid and extra_quotes < 2 and self.fmp.remaining() > cfg.FMP_EXIT_RESERVE:
                q = self.fmp.quote(sym)
                extra_quotes += 1
                liquid = bool(q) and (q.get("volume") or 0) >= cfg.STOCK_MIN_VOLUME
            if not liquid:
                continue
            out.append(Candidate(sym, "stock", "USD", float(price), float(pct), 0.0,
                                 meta={"name": r.get("name"), "exchange": r.get("exchange")}))
        out.sort(key=lambda c: c.change_pct, reverse=True)
        return out[:10]

    def tradable(self, symbol):
        """True se FMP fornisce il prezzo del titolo. Serve a non aprire posizioni che poi non si
        potrebbero gestire (stop/uscita) perche' il titolo non e' coperto dal piano gratuito."""
        key = (market_state(self.clock())["day"], symbol)
        if key in self._tradable:
            return self._tradable[key]
        q = self.fmp.quote(symbol)
        if q and q.get("price"):
            self._push(symbol, q["price"])
            self._tradable[key] = True
            return True
        if self.fmp.last_error not in ("network", "budget"):   # errori passeggeri: non memorizzare
            self._tradable[key] = False
        return False

    def price(self, symbol):
        """Prezzo recente: dall'ultimo scan se fresco, altrimenti una chiamata quote (a budget)."""
        cached = self.prices.get(symbol)
        if cached and self.clock() - cached[0] <= self.cfg.STOCK_POS_CHECK_SECONDS:
            return cached[1]
        q = self.fmp.quote(symbol)
        if q and q.get("price"):
            self._push(symbol, q["price"])
            return float(q["price"])
        return cached[1] if cached else None

    def _spy_pct(self, now):
        ts, pct = self._spy
        if pct is not None and now - ts < 900:
            return pct
        q = self.fmp.quote("SPY")
        if q:
            val = q.get("changePercentage")
            if val is None:
                val = q.get("changesPercentage")
            if val is not None:
                self._spy = (now, float(val))
                return float(val)
        return None

    def context(self, now):
        cfg = self.cfg
        ms = market_state(now)
        reasons = []
        can = ms["open"]
        if not ms["open"]:
            reasons.append("chiuso")
        else:
            if ms["since_open"] < cfg.STOCK_ENTRY_AFTER_OPEN_MIN:
                can = False
                reasons.append("apertura")
            if ms["to_close"] < cfg.STOCK_ENTRY_BEFORE_CLOSE_MIN:
                can = False
                reasons.append("chiusura")
        if ms["open"] and self.stale:
            can = False
            reasons.append("dati_fermi")
        if self.fmp.remaining() < cfg.FMP_EXIT_RESERVE:
            can = False
            reasons.append("budget_fmp")
        spy = None
        if ms["open"] and can:
            spy = self._spy_pct(now)
            if spy is None:
                can = False
                reasons.append("spy_nd")
            elif spy < cfg.STOCK_SPY_MIN_PCT:
                can = False
                reasons.append("spy_in_calo")
        flatten = ms["open"] and ms["to_close"] <= cfg.STOCK_FLATTEN_BEFORE_CLOSE_MIN
        return {"open": ms["open"], "day": ms["day"], "can_enter": can, "flatten": bool(flatten),
                "reasons": reasons, "stale": self.stale, "spy_pct": spy}
