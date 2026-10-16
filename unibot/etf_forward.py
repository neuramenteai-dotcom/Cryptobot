"""Test in avanti (paper) di UNA regola fissa su ETF, registrata prima di guardare i risultati.

Regola (non si cambia durante il test): 50% SPY + 50% QQQ; ciascuna meta' e' investita solo se il prezzo di chiusura
e' sopra la propria media a 200 giorni, altrimenti resta in liquidita' (rendimento 0). Decisione e rendimento alla chiusura:
il segnale del giorno t usa la chiusura di t e guadagna da t a t+1 (con un ordine 'alla chiusura' e' realistico, con un po' di ottimismo).
Costo 0,30% per lato (prudenziale). Confronto: acquista-e-tieni SPY e QQQ dallo stesso giorno.

Lo stato e' solo la data di partenza (unibot_data/etf_forward.json): il resto si ricalcola dai prezzi, quindi lanciare il comando
quando si vuole, anche a intervalli irregolari, da' sempre lo stesso risultato. Nessun ordine reale.
"""
import datetime
import json
import os

import numpy as np

from . import signal_lab as L
from . import trend_lab as T

RULE = {"assets": ["SPY", "QQQ"], "sma": 200, "cost": 0.003, "name": "ETF: 50% SPY + 50% QQQ, ciascuno sopra SMA200", "ppy": 252}
CRYPTO_RULE = {"assets": ["BTC", "ETH"], "sma": 100, "cost": 0.006, "name": "CRYPTO: 50% BTC + 50% ETH, ciascuno sopra SMA100 (costo 0,6%)", "ppy": 365}
CRYPTO50_RULE = {"assets": ["BTC", "ETH"], "sma": 50, "cost": 0.006, "name": "CRYPTO: 50% BTC + 50% ETH, ciascuno sopra SMA50 (costo 0,6%)", "ppy": 365}
RULES = {"etf": RULE, "crypto": CRYPTO_RULE, "crypto50": CRYPTO50_RULE}
STATE = "unibot_data/etf_forward.json"


def load_start(path=STATE, today=None):
    """Data di partenza fissata alla prima esecuzione (poi non cambia). In cloud: UNIBOT_FORWARD_START (AAAA-MM-GG)."""
    env = os.getenv("UNIBOT_FORWARD_START", "").strip()
    if env:
        datetime.date.fromisoformat(env)  # ValueError se malformata: meglio fermarsi che partire da una data sbagliata
        return env
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)["start"]
    start = (today or datetime.date.today()).isoformat()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"start": start, "rule": RULE}, f)
    return start


def load_crypto(exchange=None):
    """Chiusure giornaliere (UTC) BTC/ETH da Coinbase, ultime 300; la candela del giorno in corso e' esclusa."""
    import ccxt
    ex = exchange or ccxt.coinbase({"enableRateLimit": True})
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    series = {}
    for a in CRYPTO_RULE["assets"]:
        rows = L_retry(lambda a=a: ex.fetch_ohlcv(a + "/USDC", "1d", limit=300))
        d = {}
        for ts, o, h, l, c, v in rows:
            day = datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc).date().isoformat()
            if day < today and c:
                d[day] = float(c)
        series[a] = d
    return series


def L_retry(fn, tries=4):
    import time
    for i in range(tries):
        try:
            return fn()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))


def compute(dates, P, start, rule=RULE):
    """P: colonne = rule['assets']. Ritorna stato e curve dal giorno `start` (incluso) in poi."""
    S = T.sma(P, rule["sma"])
    sig = (P > S) & np.isfinite(S)
    W = sig.astype(float) / len(rule["assets"])
    i0 = next((i for i, d in enumerate(dates) if d >= start), None)
    if i0 is None or i0 >= len(dates) - 1:
        return {"days": 0, "state": {a: bool(sig[-1, j]) for j, a in enumerate(rule["assets"])}, "last": dates[-1]}
    eq, trades, _ = T.run_weights(W[i0:], P[i0:], rule["cost"])
    bench = {a: float(P[-1, j] / P[i0, j] - 1) for j, a in enumerate(rule["assets"])}
    return {"days": len(dates) - 1 - i0, "start_used": dates[i0], "last": dates[-1], "strategy": float(eq[-1] - 1),
            "mdd": T.metrics(eq, trades, 1.0, len(eq) - 1, ppy=rule["ppy"])["mdd"], "trades": trades, "bench": bench,
            "state": {a: bool(sig[-1, j]) for j, a in enumerate(rule["assets"])}}


def _report(key, rule, series, start):
    dates = sorted(set.intersection(*[set(d) for d in series.values()]))
    P = np.array([[series[a][d] for a in rule["assets"]] for d in dates], dtype=float)
    r = compute(dates, P, start, rule)
    out = ["--- %s | partenza %s ---" % (rule["name"], start)]
    out.append("stato (ultima chiusura %s): %s" % (r["last"], ", ".join("%s %s" % (a, "IN POSIZIONE" if v else "IN CASSA") for a, v in r["state"].items())))
    if not r["days"]:
        out.append("nessun giorno completo dopo la partenza: riesegui dopo la prossima chiusura.")
    else:
        out.append("giorni: %d | strategia %+.2f%% (calo max %.1f%%, %d operazioni) | acquista-e-tieni: %s" % (
            r["days"], r["strategy"] * 100, r["mdd"] * 100, r["trades"],
            ", ".join("%s %+.2f%%" % (a, v * 100) for a, v in r["bench"].items())))
    return "\n".join(out)


def run(path=STATE, yahoo=None, today=None, crypto_series=None):
    start = load_start(path, today)
    y = yahoo or L.YahooHistory(cache_dir="unibot_data/etf_prices", start="2024-01-01")
    etf = {}
    for a in RULE["assets"]:
        s = y.get(a)
        if not s:
            raise RuntimeError("prezzi non disponibili per %s: riprova" % a)
        etf[a] = dict(s)
    parts = ["=== TEST IN AVANTI (paper, nessun ordine reale) ===", _report("etf", RULE, etf, start)]
    try:
        cs = crypto_series or load_crypto()
        parts.append(_report("crypto", CRYPTO_RULE, cs, start))
        parts.append(_report("crypto50", CRYPTO50_RULE, cs, start))
    except Exception as e:  # noqa: BLE001
        parts.append("--- CRYPTO: dati non raggiungibili (%s): riprova ---" % type(e).__name__)
    parts.append("Verdetto non prima di 4-8 settimane e di almeno un cambio di segnale: prima e' rumore. "
                 "Il bot intraday (crypto/azioni) non e' incluso: il backtest non ha mostrato vantaggio e non e' in esecuzione.")
    return "\n".join(parts)


def state_changes(prev, new):
    """Confronta {regola: {asset: bool}} fra due esecuzioni: elenco dei segnali cambiati."""
    out = []
    for key, assets in new.items():
        for a, v in assets.items():
            old = (prev or {}).get(key, {}).get(a)
            if old is not None and old != v:
                out.append("%s %s: %s -> %s" % (key.upper(), a, "IN POSIZIONE" if old else "IN CASSA", "IN POSIZIONE" if v else "IN CASSA"))
    return out


def current_states(series_by_key):
    out = {}
    for key, series in series_by_key.items():
        rule = RULES[key]
        dates = sorted(set.intersection(*[set(d) for d in series.values()]))
        P = np.array([[series[a][d] for a in rule["assets"]] for d in dates], dtype=float)
        S = T.sma(P, rule["sma"])
        out[key] = {a: bool(P[-1, j] > S[-1, j]) if np.isfinite(S[-1, j]) else False for j, a in enumerate(rule["assets"])}
    return out


def write_cloud_report(out_dir, report, states, today=None):
    """Scrive latest.md, history/<data>.md e state.json in out_dir; ritorna i cambi di segnale rispetto a state.json precedente."""
    today = (today or datetime.date.today()).isoformat()
    os.makedirs(os.path.join(out_dir, "history"), exist_ok=True)
    sp = os.path.join(out_dir, "state.json")
    prev = None
    if os.path.exists(sp):
        with open(sp, "r", encoding="utf-8") as f:
            prev = json.load(f)
    changes = state_changes(prev, states)
    text = report + ("\n\nSEGNALE CAMBIATO: " + "; ".join(changes) if changes else "\n\nNessun cambio di segnale dall'ultima esecuzione.")
    for fn in ("latest.md", os.path.join("history", today + ".md")):
        with open(os.path.join(out_dir, fn), "w", encoding="utf-8") as f:
            f.write(text + "\n")
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(states, f)
    return text, changes


def run_cloud(out_dir, yahoo=None, crypto_series=None, today=None):
    """Una esecuzione non presidiata: calcola, scrive i file in out_dir, ritorna il testo."""
    start = load_start(os.path.join(out_dir, "start.json"), today)
    y = yahoo or L.YahooHistory(cache_dir="unibot_data/etf_prices", start="2024-01-01")
    etf = {}
    for a in RULE["assets"]:
        s = y.get(a)
        if not s:
            raise RuntimeError("prezzi non disponibili per %s" % a)
        etf[a] = dict(s)
    series = {"etf": etf}
    parts = ["=== TEST IN AVANTI (paper, nessun ordine reale) ===", _report("etf", RULE, etf, start)]
    try:
        cs = crypto_series or load_crypto()
        series["crypto"] = cs
        series["crypto50"] = cs
        parts.append(_report("crypto", CRYPTO_RULE, cs, start))
        parts.append(_report("crypto50", CRYPTO50_RULE, cs, start))
    except Exception as e:  # noqa: BLE001
        parts.append("--- CRYPTO: dati non raggiungibili (%s) ---" % type(e).__name__)
    text, _ = write_cloud_report(out_dir, "\n".join(parts), current_states(series), today)
    return text


def main_cli(out_dir=None):
    try:
        print(run_cloud(out_dir) if out_dir else run())
        return 0
    except Exception as e:  # noqa: BLE001
        print("etf-forward non eseguito: %s" % e)
        return 2
