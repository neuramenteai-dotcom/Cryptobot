"""Dati per la dashboard di Unibot: raccoglie i risultati dei laboratori in tabelle JSON (una riga per record).

Uso: python -m unibot.run --dashboard-data  -> scrive unibot_data/dashboard/*.json
I file si caricano poi nella dashboard (snapshot: si rigenerano e si ricaricano quando si vuole aggiornarla).
Nessun dato personale: solo prezzi di mercato pubblici e risultati dei test.
"""
import datetime
import json
import os

import numpy as np
import pandas as pd

from . import dip_lab, etf_forward, etf_lab, sector_lab
from . import trend_lab as T

OUT = "unibot_data/dashboard"


def weekly_prices(data, coins=("BTC", "ETH")):
    """Una riga a settimana per moneta: chiusura, media a 200 giorni, distanza dal massimo a 365 giorni."""
    rows = []
    for c in coins:
        dates, px = data[c]
        sma = pd.Series(px).rolling(200, min_periods=200).mean().to_numpy()
        dd = dip_lab.drawdown(px)
        for i in range(len(px) - 1, -1, -7):
            rows.append({"date": dates[i], "coin": c, "price": round(float(px[i]), 4),
                         "sma200": round(float(sma[i]), 4) if np.isfinite(sma[i]) else None,
                         "dd365": round(float(dd[i]), 4) if np.isfinite(dd[i]) else None})
    rows.sort(key=lambda r: (r["coin"], r["date"]))
    return rows


def cycle_rows(data):
    rows = []
    for c, (dates, px) in data.items():
        p = dip_lab.position_now(px, dates)
        sma = pd.Series(px).rolling(200).mean().iloc[-1]
        rows.append({"coin": c, "date": p["date"], "price": round(p["price"], 4), "dd365": round(p["dd365"], 4),
                     "vs_sma200": round(p["vs_sma200"], 4), "pct4y": round(p["pct4y"], 4), "days_since_high": p["days_since_high"],
                     "trend": "IN POSIZIONE" if px[-1] > sma else "IN CASSA"})
    return rows


def dip_rows(data):
    rows = []
    for c, (dates, px) in data.items():
        res, spans = dip_lab.evaluate(px, dates)
        for strat, byp in res.items():
            for pn, m in byp.items():
                if pn == "_trades":
                    continue
                rows.append({"id": "%s|%s|%s" % (c, strat, pn), "coin": c, "strategy": strat, "period": pn,
                             "from": spans[pn][0], "to": spans[pn][1], "total": round(m["total"], 4), "mdd": round(m["mdd"], 4),
                             "sharpe": round(m["sharpe"], 3), "invested": round(m["invested"], 4), "trades": m["n_trades"]})
    return rows


def sector_rows(cache_dir="unibot_data"):
    syms, dates, P = T.load_daily(cache_dir)
    names, S, members = sector_lab.sector_matrix(P, syms)
    yt = sector_lab.yearly_table(names, S, dates)
    rows = []
    for n in names:
        r = {"sector": n, "coins": len(members[n])}
        for y in yt.index:
            r["y%d" % y] = round(float(yt.loc[y, n]), 4)
        rows.append(r)
    cols = {(s[:-4] if s.endswith("USDT") else s): i for i, s in enumerate(syms)}
    btc = P[:, cols["BTC"]]
    r = {"sector": "Bitcoin (riferimento)", "coins": 1}
    for y in yt.index:
        m = [i for i, d in enumerate(dates) if d.startswith(str(y))]
        r["y%d" % y] = round(float(btc[m[-1]] / btc[m[0]] - 1), 4)
    rows.append(r)
    return rows


def etf_rows():
    syms, dates, P = etf_lab.load_prices()
    out = []
    for r in etf_lab.vs_cash_rows(syms, dates, P):
        out.append({"id": "%s|%d" % (r["strategy"], r["years"]), **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}})
    perf = etf_lab.performance_table(syms, dates, P)
    etf = [{"etf": s, "name": etf_lab.ETFS[s], **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in perf[s].items() if k in ("y1", "y3", "y5", "y10", "cagr", "mdd", "sharpe") or k in ("1y", "3y", "5y", "10y")}} for s in syms]
    return out, etf


def forward_rows(start=None):
    from . import signal_lab as L
    start = start or os.getenv("UNIBOT_FORWARD_START") or "2026-10-16"
    y = L.YahooHistory(cache_dir="unibot_data/etf_prices", start="2024-01-01")
    series = {"etf": {a: dict(y.get(a)) for a in etf_forward.RULE["assets"]}, "crypto": etf_forward.load_crypto()}
    rows = []
    for key, rule in etf_forward.RULES.items():
        s = series[key]
        dates = sorted(set.intersection(*[set(d) for d in s.values()]))
        P = np.array([[s[a][d] for a in rule["assets"]] for d in dates], dtype=float)
        r = etf_forward.compute(dates, P, start, rule)
        for a, on in r["state"].items():
            rows.append({"id": key + "|" + a, "test": rule["name"], "asset": a, "state": "IN POSIZIONE" if on else "IN CASSA",
                         "start": start, "last": r["last"], "days": r["days"],
                         "strategy": r.get("strategy"), "mdd": r.get("mdd"), "bench": (r.get("bench") or {}).get(a)})
    return rows


def build(out_dir=OUT):
    os.makedirs(out_dir, exist_ok=True)
    data = dip_lab.load_long()
    etf_cash, etf_perf = etf_rows()
    tables = {"prezzi_settimanali": weekly_prices(data), "ciclo_oggi": cycle_rows(data), "compra_basso": dip_rows(data),
              "settori_anno": sector_rows(), "etf_vs_deposito": etf_cash, "etf_prestazioni": etf_perf, "test_in_avanti": forward_rows()}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    for name, rows in tables.items():
        with open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
    return {k: len(v) for k, v in tables.items()}, now


def main_cli():
    try:
        sizes, now = build()
        print("dashboard: tabelle scritte in %s (%s)" % (OUT, now))
        for k, n in sizes.items():
            print("  %-20s %5d righe" % (k, n))
        return 0
    except Exception as e:  # noqa: BLE001
        print("dashboard-data non eseguito: %s" % e)
        return 2
