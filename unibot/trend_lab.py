"""Test del seguimento del trend a bassa rotazione (crypto, solo posizioni lunghe in spot).

Strategie, tutte decise sul prezzo di APERTURA del giorno (noto a quell'istante) e tenute fino al giorno dopo:
  - trend su singola moneta: in posizione se il prezzo e' sopra la media mobile a N giorni, altrimenti liquidita';
  - trend su paniere: ogni moneta sopra la propria media pesa 1/n, le altre restano in liquidita';
  - momentum trasversale: ogni 7 giorni si tengono le K monete con il miglior rendimento a 28 giorni.
Riferimenti: acquista-e-tieni su BTC e paniere equipesato ribilanciato ogni settimana.

Cautele: costi di giro applicati a ogni variazione dei pesi; due livelli di costo (taker e maker);
risultati separati per periodo (2023-24 'di scelta', 2025-26 'di verifica'): i parametri non vanno scelti
guardando la verifica. Limiti: monete quotate oggi (survivorship: favorisce soprattutto paniere e momentum),
prezzi dei perpetui come proxy dello spot, nessun impatto di mercato, tasse escluse.
"""
import datetime
import json
import math
import os

import numpy as np
import pandas as pd

DAY_MS = 86_400_000
COSTS = {"taker (0,6%)": 0.006, "maker (0,35%)": 0.0035}   # costo di UN lato: commissione + slippage
SPLIT = "2025-01-01"


def load_daily(cache_dir="unibot_data"):
    """Prezzi di apertura delle 00:00 UTC per moneta, dai dati a 8 ore gia' scaricati dal funding lab."""
    folder = os.path.join(cache_dir, "funding")
    series = {}
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(folder, fn), "r", encoding="utf-8") as f:
            d = json.load(f)
        pts = {int(ts) // DAY_MS: float(op) for ts, op in d["open"] if int(ts) % DAY_MS == 0 and op > 0}
        if len(pts) > 400:
            series[fn[:-5]] = pts
    days = sorted({k for pts in series.values() for k in pts})
    grid = list(range(days[0], days[-1] + 1))
    syms = sorted(series)
    P = np.full((len(grid), len(syms)), np.nan)
    for j, s in enumerate(syms):
        for k, v in series[s].items():
            P[k - grid[0], j] = v
    dates = [_iso(g) for g in grid]
    return syms, dates, P


def _iso(day_number):
    return (datetime.date(1970, 1, 1) + datetime.timedelta(days=int(day_number))).isoformat()


def sma(P, n):
    return pd.DataFrame(P).rolling(n, min_periods=n).mean().to_numpy()


def run_weights(W, P, cost):
    """Curva del capitale dato il peso W[t] deciso all'apertura del giorno t, tenuto fino a t+1.
    Costo = cost * variazione assoluta dei pesi. Ritorna (equity, trades, tempo_in_mercato)."""
    T = P.shape[0]
    R = np.where(np.isfinite(P[1:]) & np.isfinite(P[:-1]), P[1:] / P[:-1] - 1, 0.0)
    W = np.nan_to_num(W[:-1])
    prev = np.zeros(W.shape[1])
    eq = [1.0]
    trades = 0
    for t in range(T - 1):
        turn = float(np.abs(W[t] - prev).sum())
        trades += int((np.abs(W[t] - prev) > 1e-9).sum())
        eq.append(eq[-1] * (1.0 - cost * turn) * (1.0 + float((W[t] * R[t]).sum())))   # costo pagato prima di entrare
        prev = W[t]
    invested = float((W.sum(axis=1) > 1e-9).mean())
    return np.array(eq), trades, invested


def trend_weights(P, cols, n, basket=False):
    S = sma(P, n)
    sig = (P > S) & np.isfinite(P) & np.isfinite(S)
    W = np.zeros(P.shape)
    if not basket:
        for c in cols:
            W[:, c] = sig[:, c].astype(float)
        return W
    avail = np.isfinite(P).sum(axis=1).clip(min=1)
    for c in cols:
        W[:, c] = sig[:, c] / avail
    return W


def momentum_weights(P, k=5, lookback=28, every=7):
    W = np.zeros(P.shape)
    cur = np.zeros(P.shape[1])
    for t in range(P.shape[0]):
        if t >= lookback and (t - lookback) % every == 0:
            ret = P[t] / P[t - lookback] - 1
            ok = np.isfinite(ret) & np.isfinite(P[t])
            cur = np.zeros(P.shape[1])
            if ok.sum() >= k:
                idx = np.flatnonzero(ok)[np.argsort(-ret[ok])[:k]]
                cur[idx] = 1.0 / k
        W[t] = cur
    return W


def equal_weight_weights(P, every=7):
    W = np.zeros(P.shape)
    cur = np.zeros(P.shape[1])
    for t in range(P.shape[0]):
        if t % every == 0:
            ok = np.isfinite(P[t])
            cur = np.zeros(P.shape[1])
            if ok.any():
                cur[ok] = 1.0 / ok.sum()
        W[t] = cur
    return W


def metrics(eq, trades, invested, days, ppy=365):
    r = np.diff(eq) / eq[:-1]
    total = eq[-1] / eq[0] - 1
    years = max(days / float(ppy), 1e-9)
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1 if eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq)
    mdd = float(((eq - peak) / peak).min())
    sd = r.std(ddof=1) if len(r) > 2 else 0.0
    sharpe = float(r.mean() / sd * math.sqrt(ppy)) if sd > 0 else 0.0
    return {"total": total, "cagr": cagr, "mdd": mdd, "sharpe": sharpe, "trades_y": trades / years, "invested": invested}


def evaluate(syms, dates, P, cost):
    """Tutte le strategie su tre periodi: completo, di scelta (< SPLIT), di verifica (>= SPLIT)."""
    # i file hanno il nome del contratto (BTCUSDT): si cerca per moneta base
    cols = {s[:-4] if s.endswith("USDT") else s: i for i, s in enumerate(syms)}
    all_cols = list(range(len(syms)))
    for needed in ("BTC", "ETH"):
        if needed not in cols:
            raise ValueError("manca %s nei dati: il confronto con il riferimento non sarebbe valido" % needed)
    strategies = {}
    for n in (20, 50, 100):
        for name in ("BTC", "ETH"):
            if name in cols:
                strategies["trend %s SMA%d" % (name, n)] = trend_weights(P, [cols[name]], n)
        strategies["trend paniere SMA%d" % n] = trend_weights(P, all_cols, n, basket=True)
    strategies["momentum top5 (28g, sett.)"] = momentum_weights(P)
    bench = {}
    for coin in ("BTC", "ETH"):
        w = np.zeros(P.shape)
        w[:, cols[coin]] = 1.0
        bench["acquista-e-tieni %s" % coin] = w
    bench["paniere equipesato (sett.)"] = equal_weight_weights(P)
    split = dates.index(SPLIT) if SPLIT in dates else len(dates) // 2
    periods = {"completo": (0, len(dates)), "scelta": (0, split), "verifica": (split, len(dates))}
    out = {}
    for name, W in list(strategies.items()) + list(bench.items()):
        out[name] = {}
        for pname, (a, b) in periods.items():
            eq, tr, inv = run_weights(W[a:b], P[a:b], cost)
            out[name][pname] = metrics(eq, tr, inv, b - a)
    return out, list(bench)


def format_report(results, bench, dates, cost_label):
    L = ["--- costo di un lato: %s | periodo %s -> %s ---" % (cost_label, dates[0], dates[-1])]
    L.append("%-28s | %-28s | %-28s | %-28s" % ("strategia", "COMPLETO ritorno/maxDD/Sharpe",
                                                "SCELTA (< %s)" % SPLIT, "VERIFICA (>= %s)" % SPLIT))
    for name, byp in results.items():
        cells = []
        for p in ("completo", "scelta", "verifica"):
            m = byp[p]
            cells.append("%+7.0f%% %5.0f%% %5.2f %3.0fop/a" % (m["total"] * 100, m["mdd"] * 100, m["sharpe"], m["trades_y"]))
        L.append("%-28s | %-28s | %-28s | %-28s%s" % (name, cells[0], cells[1], cells[2], "  [riferimento]" if name in bench else ""))
    return "\n".join(L)


def run(cache_dir="unibot_data"):
    syms, dates, P = load_daily(cache_dir)
    parts = ["=== TREND LAB | %d monete, %d giorni (%s -> %s) ===" % (len(syms), len(dates), dates[0], dates[-1])]
    for label, c in COSTS.items():
        res, bench = evaluate(syms, dates, P, c)
        parts.append(format_report(res, bench, dates, label))
    parts.append("Colonne: rendimento totale / massimo calo / Sharpe annualizzato / operazioni all'anno. "
                 "Le strategie in cassa nei cali rinunciano a parte del rialzo: contano rendimento E calo massimo, "
                 "e soprattutto che il risultato REGGA nel periodo di verifica.")
    return "\n".join(parts)


def main_cli():
    try:
        print(run())
        return 0
    except Exception as e:  # noqa: BLE001
        print("trend-lab non eseguito: %s (serve prima --funding-lab per scaricare i prezzi)" % e)
        return 2
