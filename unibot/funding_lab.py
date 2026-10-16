"""Test del funding crypto come segnale (dati pubblici gratuiti dei perpetui Binance).

Domanda: le monete con funding piu' ALTO (posizioni long affollate) rendono meno delle altre nei giorni
successivi, e quelle con funding piu' BASSO rendono di piu'? Se si', il funding e' un filtro utile anche per
chi opera solo in spot (evita le monete "affollate" e sceglie le meno affollate).

Cautele incorporate:
- il funding di un istante e' noto a quell'istante, ma si entra al prezzo del periodo SUCCESSIVO (ritardo di
  8 ore): nessuna informazione dal futuro;
- confronto trasversale (le monete tra loro, nello stesso momento): non dipende dal rialzo o ribasso generale;
- osservazioni NON sovrapposte (una ogni H periodi), con due sfasamenti diversi per verificare la stabilita';
- le prime 90 giornate di ogni moneta sono escluse (le nuove quotazioni hanno funding e prezzi anomali);
- risultati riportati anche sulle due meta' del periodo e al netto dei costi.
Limiti: solo monete quotate oggi (survivorship), prezzi dei perpetui usati come proxy dello spot,
alcune monete non sono su Coinbase, ~9 confronti simultanei (qualche t vicino a 2 puo' essere rumore).
"""
import json
import math
import os
import time

from . import settings as S
from .universe import retry

FAPI = "https://fapi.binance.com"
STEP_MS = 8 * 3600 * 1000
PER_DAY = 3
HORIZONS = {"1 giorno": 3, "3 giorni": 9, "7 giorni": 21}
WARMUP = 90 * PER_DAY
ROLL = 21                     # media del funding su 7 giorni
CRYPTO_BASES = ("BTC ETH SOL XRP BNB DOGE ADA AVAX LINK DOT LTC BCH TRX POL ATOM NEAR APT ARB OP SUI INJ TIA UNI "
                "AAVE FIL ETC XLM HBAR ICP RENDER FET GRT IMX ALGO VET SAND MANA AXS SHIB PEPE WIF ONDO ENA TON SEI "
                "STX RUNE MKR LDO CRV SNX COMP GALA CHZ APE DYDX EGLD FLOW KAVA ZEC XTZ EOS NEO").split()


def normalize_base(base):
    """'1000SHIB' -> 'SHIB'."""
    b = str(base)
    while b and b[0].isdigit():
        b = b[1:]
    return b


def list_symbols(http=None):
    """Perpetui USDT di vere crypto (elenco chiuso: Binance quota anche azioni, ETF e oro tokenizzati)."""
    if http is None:
        import requests as http
    r = retry(lambda: http.get(FAPI + "/fapi/v1/exchangeInfo", timeout=60), 5, 3.0)
    out = {}
    for s in r.json().get("symbols", []):
        if s.get("contractType") != "PERPETUAL" or s.get("quoteAsset") != "USDT" or s.get("status") != "TRADING":
            continue
        base = normalize_base(s.get("baseAsset"))
        if base in CRYPTO_BASES and base not in out:
            out[base] = s["symbol"]
    return out


def _page(http, path, params):
    def go():
        r = http.get(FAPI + path, params=params, timeout=30)
        if r.status_code != 200:
            raise RuntimeError("HTTP %s su %s" % (r.status_code, path))
        return r.json()
    return retry(go, 5, 3.0)


def fetch_funding(sym, start_ms, end_ms, http=None, pause=0.15):
    if http is None:
        import requests as http
    out, since = [], start_ms
    while since < end_ms:
        rows = _page(http, "/fapi/v1/fundingRate", {"symbol": sym, "limit": 1000, "startTime": since, "endTime": end_ms})
        if not rows:
            break
        out += [(int(x["fundingTime"]), float(x["fundingRate"])) for x in rows]
        if len(rows) < 1000:
            break
        since = int(rows[-1]["fundingTime"]) + 1
        time.sleep(pause)
    return out


def fetch_opens(sym, start_ms, end_ms, http=None, pause=0.15):
    """Prezzi di apertura delle candele a 8 ore (allineate agli istanti di funding)."""
    if http is None:
        import requests as http
    out, since = [], start_ms
    while since < end_ms:
        rows = _page(http, "/fapi/v1/klines", {"symbol": sym, "interval": "8h", "limit": 1000,
                                               "startTime": since, "endTime": end_ms})
        if not rows:
            break
        out += [(int(x[0]), float(x[1])) for x in rows]
        if len(rows) < 1000:
            break
        since = int(rows[-1][0]) + 1
        time.sleep(pause)
    return out


def load_symbol(sym, start_ms, end_ms, cache_dir, http=None):
    path = os.path.join(cache_dir, "funding", sym + ".json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 86400:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return {"funding": [tuple(x) for x in d["funding"]], "open": [tuple(x) for x in d["open"]]}
    d = {"funding": fetch_funding(sym, start_ms, end_ms, http), "open": fetch_opens(sym, start_ms, end_ms, http)}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f)
    return d


def build_panel(series, t0, t1, warmup=WARMUP):
    """Matrici (tempo x moneta) allineate alla griglia di 8 ore: funding F e prezzo di apertura P."""
    import numpy as np
    t0 = (t0 // STEP_MS) * STEP_MS
    n = int((t1 - t0) // STEP_MS) + 1
    syms = sorted(series)
    F = np.full((n, len(syms)), np.nan)
    P = np.full((n, len(syms)), np.nan)
    for j, s in enumerate(syms):
        for ts, rate in series[s]["funding"]:
            i = int(round((ts - t0) / STEP_MS))
            if 0 <= i < n:
                F[i, j] = rate
        for ts, op in series[s]["open"]:
            i = int(round((ts - t0) / STEP_MS))
            if 0 <= i < n and op > 0:
                P[i, j] = op
        first = np.flatnonzero(~np.isnan(F[:, j]))
        if len(first):
            F[: first[0] + warmup, j] = np.nan      # nuove quotazioni: periodo di assestamento escluso
    return syms, F, P


def rolling_mean(F, window=ROLL, min_periods=15):
    import pandas as pd
    return pd.DataFrame(F).rolling(window, min_periods=min_periods).mean().to_numpy()


def stat(x):
    x = [v for v in x if v == v]
    n = len(x)
    if n < 3:
        return {"n": n}
    m = sum(x) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in x) / (n - 1))
    return {"n": n, "mean": m, "t": (m / (sd / math.sqrt(n))) if sd > 0 else 0.0,
            "hit": sum(1 for v in x if v > 0) / n}


def cross_section(F7, P, H, delay=1, offset=0, min_syms=12):
    """Per ogni istante (uno ogni H periodi): rendimento medio del quintile a funding piu' basso (q1), di
    quello a funding piu' alto (q5) e dell'intero universo (u). Entrata al prezzo del periodo i+delay."""
    import numpy as np
    rows = []
    for i in range(offset, P.shape[0] - 1 - delay - H, H):
        f, entry, exit_ = F7[i], P[i + delay], P[i + delay + H]
        valid = ~np.isnan(f) & ~np.isnan(entry) & ~np.isnan(exit_)
        if valid.sum() < min_syms:
            continue
        ret = exit_[valid] / entry[valid] - 1
        order = np.argsort(f[valid])
        q = max(2, len(order) // 5)
        rows.append((i, ret[order[:q]].mean(), ret[order[-q:]].mean(), ret.mean()))
    return np.array(rows).reshape(-1, 4)


def market_timing(F7, P, H, delay=1, offset=0, z_window=270):
    """Il funding medio dell'intero mercato prevede il rendimento dell'universo? (z rispetto agli ultimi 90 giorni)."""
    import numpy as np
    import pandas as pd
    m = pd.Series(np.nanmean(F7, axis=1))
    z = ((m - m.rolling(z_window, min_periods=90).mean()) / m.rolling(z_window, min_periods=90).std()).to_numpy()
    buckets = {"alto (z>1)": [], "medio": [], "basso (z<-1)": []}
    for i in range(offset, P.shape[0] - 1 - delay - H, H):
        if z[i] != z[i]:
            continue
        entry, exit_ = P[i + delay], P[i + delay + H]
        valid = ~np.isnan(entry) & ~np.isnan(exit_)
        if valid.sum() < 12:
            continue
        r = float((exit_[valid] / entry[valid] - 1).mean())
        buckets["alto (z>1)" if z[i] > 1 else "basso (z<-1)" if z[i] < -1 else "medio"].append(r)
    return buckets


def analyze(syms, F, P, cost=None):
    cost = (2 * S.CRYPTO_FEE_RATE + 2 * S.CRYPTO_SLIPPAGE_BPS / 1e4) if cost is None else cost
    F7 = rolling_mean(F)
    out = {"symbols": syms, "cost": cost, "horizons": {}, "market": {}}
    for name, H in HORIZONS.items():
        res = {}
        for tag, off in (("A", 0), ("B", H // 2)):
            cs = cross_section(F7, P, H, offset=off)
            if len(cs) < 5:
                res[tag] = {"n": len(cs)}
                continue
            q1, q5, u = cs[:, 1], cs[:, 2], cs[:, 3]
            half = len(cs) // 2
            res[tag] = {
                "n": len(cs),
                "q1_minus_u": stat(q1 - u), "q5_minus_u": stat(q5 - u), "spread": stat(q1 - q5),
                "q1_net": stat(q1 - cost), "u": stat(u),
                "spread_h1": stat((q1 - q5)[:half]), "spread_h2": stat((q1 - q5)[half:]),
            }
        out["horizons"][name] = res
        out["market"][name] = {k: stat(v) for k, v in market_timing(F7, P, H).items()}
    return out


def _fmt(s, scale=100.0):
    if not s.get("n") or "mean" not in s:
        return "   n/d"
    return "%+6.2f%% (t %5.2f, n %d)" % (s["mean"] * scale, s["t"], s["n"])


def format_report(res, start, end):
    L = ["=== FUNDING LAB (perpetui Binance, dati pubblici) | %s -> %s | %d monete ===" % (start, end, len(res["symbols"]))]
    L.append("Q1 = quintile a funding PIU' BASSO, Q5 = PIU' ALTO, U = media dell'universo. Entrata 8 ore dopo il segnale. "
             "Costo di giro ipotizzato %.2f%%." % (res["cost"] * 100))
    for name, byoff in res["horizons"].items():
        L.append("")
        L.append("Orizzonte %s" % name)
        for tag in ("A", "B"):
            r = byoff.get(tag, {})
            if "q1_minus_u" not in r:
                L.append("  [%s] campione insufficiente (n %s)" % (tag, r.get("n", 0)))
                continue
            L.append("  [%s] Q1 - U   : %s" % (tag, _fmt(r["q1_minus_u"])))
            L.append("      Q5 - U   : %s" % _fmt(r["q5_minus_u"]))
            L.append("      Q1 - Q5  : %s | prima meta' %s | seconda %s" % (
                _fmt(r["spread"]), _fmt(r["spread_h1"]), _fmt(r["spread_h2"])))
            L.append("      Q1 netto costi (assoluto): %s | universo: %s" % (_fmt(r["q1_net"]), _fmt(r["u"])))
        mk = res["market"].get(name, {})
        L.append("  Funding medio di mercato -> rendimento dell'universo: " + " | ".join(
            "%s %s" % (k, _fmt(v)) for k, v in mk.items()))
    L.append("")
    L.append("Come leggerlo: un filtro utile ha Q1-Q5 positivo con |t| >= 2 in ENTRAMBI gli sfasamenti [A] e [B], "
             "in entrambe le meta' del periodo, e un Q1 netto dei costi positivo. Con ~9 confronti simultanei, "
             "uno o due t vicini a 2 possono essere rumore.")
    return "\n".join(L)


def run(start="2023-01-01", cache_dir="unibot_data", log=print):
    import datetime
    t0 = int(datetime.datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    t1 = int(time.time() * 1000)
    log("elenco perpetui Binance...")
    mapping = list_symbols()
    log("crypto trovate: %d (%s)" % (len(mapping), ", ".join(sorted(mapping))))
    series = {}
    for k, (base, sym) in enumerate(sorted(mapping.items()), 1):
        try:
            series[base] = load_symbol(sym, t0, t1, cache_dir)
            log("  %2d/%d %-6s funding %5d | candele %5d" % (k, len(mapping), base,
                                                           len(series[base]["funding"]), len(series[base]["open"])))
        except Exception as e:  # noqa: BLE001
            log("  %2d/%d %-6s SALTATO (%s)" % (k, len(mapping), base, str(e)[:60]))
    syms, F, P = build_panel(series, t0, t1)
    log("analisi su %d monete, %d periodi..." % (len(syms), F.shape[0]))
    return format_report(analyze(syms, F, P), start, datetime.datetime.now().strftime("%Y-%m-%d"))


def main_cli(start=None):
    try:
        print(run(start or "2023-01-01"))
        return 0
    except Exception as e:  # noqa: BLE001
        print("funding-lab non eseguito: %s" % e)
        return 2
