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

RULE = {"assets": ["SPY", "QQQ"], "sma": 200, "cost": 0.003}
STATE = "unibot_data/etf_forward.json"


def load_start(path=STATE, today=None):
    """Data di partenza fissata alla prima esecuzione (poi non cambia)."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)["start"]
    start = (today or datetime.date.today()).isoformat()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"start": start, "rule": RULE}, f)
    return start


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
            "mdd": T.metrics(eq, trades, 1.0, len(eq) - 1, ppy=252)["mdd"], "trades": trades, "bench": bench,
            "state": {a: bool(sig[-1, j]) for j, a in enumerate(rule["assets"])}}


def run(path=STATE, yahoo=None, today=None):
    start = load_start(path, today)
    y = yahoo or L.YahooHistory(cache_dir="unibot_data/etf_prices", start="2024-01-01")
    series = {}
    for a in RULE["assets"]:
        s = y.get(a)
        if not s:
            raise RuntimeError("prezzi non disponibili per %s: riprova" % a)
        series[a] = dict(s)
    dates = sorted(set.intersection(*[set(d) for d in series.values()]))
    P = np.array([[series[a][d] for a in RULE["assets"]] for d in dates], dtype=float)
    r = compute(dates, P, start)
    out = ["=== ETF FORWARD (paper) | regola: 50%% SPY + 50%% QQQ, ciascuno solo sopra SMA%d | partenza %s ===" % (RULE["sma"], start)]
    out.append("stato oggi (%s): %s" % (r["last"], ", ".join("%s %s" % (a, "IN POSIZIONE" if v else "IN CASSA") for a, v in r["state"].items())))
    if not r["days"]:
        out.append("nessun giorno di borsa completo dopo la partenza: riesegui dopo la prossima chiusura.")
    else:
        out.append("giorni di borsa: %d | strategia %+.2f%% (calo max %.1f%%, %d operazioni) | acquista-e-tieni: %s" % (
            r["days"], r["strategy"] * 100, r["mdd"] * 100, r["trades"],
            ", ".join("%s %+.2f%%" % (a, v * 100) for a, v in r["bench"].items())))
        out.append("Verdetto non prima di 4-8 settimane e di almeno un cambio di segnale: prima e' rumore.")
    return "\n".join(out)


def main_cli():
    try:
        print(run())
        return 0
    except Exception as e:  # noqa: BLE001
        print("etf-forward non eseguito: %s" % e)
        return 2
