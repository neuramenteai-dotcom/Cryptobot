"""Prestazioni degli ETF e test di regole semplici a bassa rotazione (dal 2004, rendimento totale).

Usa i prezzi giornalieri rettificati (dividendi inclusi) da Yahoo Finance: fonte NON ufficiale, ok per ricerca
personale. Gli ETF scelti sono quotati negli USA perche' hanno la storia piu' lunga: un risparmiatore italiano
di norma non puo' comprarli e usa equivalenti UCITS sugli stessi indici (da verificare sulla piattaforma).

Cautele: periodo 'di scelta' (fino al 2014) e 'di verifica' (dal 2015) separati; costo di un lato 0,10% (spread) e 0,30%
(prudenziale, include una quota di conversione valuta); la liquidita' non rende nulla; tasse escluse.
"""
import numpy as np

from . import signal_lab as L
from . import trend_lab as T

ETFS = {"SPY": "S&P 500 (USA)", "QQQ": "Nasdaq 100", "IWM": "USA small cap", "EFA": "Paesi sviluppati ex-USA",
        "EEM": "Paesi emergenti", "AGG": "Obbligazioni USA", "TLT": "Obbligazioni USA lunghe",
        "GLD": "Oro", "VNQ": "Immobiliare USA"}
PPY = 252
SPLIT = "2015-01-01"
COSTS = {"0,10% a lato": 0.001, "0,30% a lato": 0.003}


def load_prices(cache_dir="unibot_data/etf_prices", start="2003-01-01", yahoo=None):
    """Matrice (giorni x ETF) di prezzi rettificati, allineata sulle date comuni."""
    y = yahoo or L.YahooHistory(cache_dir=cache_dir, start=start)
    series = {}
    for sym in ETFS:
        s = y.get(sym)
        if not s:
            raise RuntimeError("prezzi non disponibili per %s (rete o fonte): riprova" % sym)
        series[sym] = dict(s)
    dates = sorted(set.intersection(*[set(d) for d in series.values()]))
    syms = list(ETFS)
    P = np.array([[series[s][d] for s in syms] for d in dates], dtype=float)
    return syms, dates, P


def annualized(P_col, dates, years):
    """Rendimento annualizzato sugli ultimi `years` anni (None se la storia e' piu' corta)."""
    n = int(round(years * PPY))
    if len(P_col) <= n:
        return None
    return (P_col[-1] / P_col[-1 - n]) ** (1.0 / years) - 1


def worst_calendar_year(P_col, dates):
    by_year = {}
    for d, p in zip(dates, P_col):
        y = d[:4]
        by_year.setdefault(y, [p, p])[1] = p
    rets = {y: v[1] / v[0] - 1 for y, v in by_year.items() if y not in (dates[0][:4], dates[-1][:4])}
    return min(rets.items(), key=lambda kv: kv[1]) if rets else (None, 0.0)


def performance_table(syms, dates, P):
    rows = {}
    for j, s in enumerate(syms):
        col = P[:, j]
        eq = col / col[0]
        m = T.metrics(eq, 0, 1.0, len(eq) - 1, ppy=PPY)
        wy, wr = worst_calendar_year(col, dates)
        rows[s] = {"1y": annualized(col, dates, 1), "3y": annualized(col, dates, 3), "5y": annualized(col, dates, 5),
                   "10y": annualized(col, dates, 10), "cagr": m["cagr"], "mdd": m["mdd"], "sharpe": m["sharpe"],
                   "worst_year": wy, "worst": wr}
    return rows


def rotation_weights(P, top=3, lookback=126, every=21, sma_n=200):
    """Ogni ~mese: tiene i `top` ETF col miglior rendimento a 6 mesi, ma solo se sopra la media a 200 giorni."""
    S = T.sma(P, sma_n)
    W = np.zeros(P.shape)
    cur = np.zeros(P.shape[1])
    for t in range(P.shape[0]):
        if t >= max(lookback, sma_n) and (t - max(lookback, sma_n)) % every == 0:
            ret = P[t] / P[t - lookback] - 1
            ok = np.isfinite(ret) & (P[t] > S[t])
            cur = np.zeros(P.shape[1])
            if ok.any():
                idx = np.flatnonzero(ok)[np.argsort(-ret[ok])[:top]]
                cur[idx] = 1.0 / top
        W[t] = cur
    return W


def fixed_mix(P, weights, every=21):
    W = np.zeros(P.shape)
    cur = np.zeros(P.shape[1])
    for t in range(P.shape[0]):
        if t % every == 0:
            cur = np.array(weights, dtype=float)
        W[t] = cur
    return W


def strategies(syms, P):
    c = {s: i for i, s in enumerate(syms)}
    out = {}
    for asset in ("SPY", "QQQ", "EFA"):
        for n in (100, 200):
            out["trend %s SMA%d" % (asset, n)] = T.trend_weights(P, [c[asset]], n)
    out["rotazione top3 (6m, SMA200)"] = rotation_weights(P)
    bench = {}
    w = np.zeros(P.shape)
    w[:, c["SPY"]] = 1.0
    bench["acquista-e-tieni SPY"] = w
    mix = [0.0] * len(syms)
    mix[c["SPY"]], mix[c["AGG"]] = 0.6, 0.4
    bench["60% SPY / 40% AGG (mensile)"] = fixed_mix(P, mix)
    bench["9 ETF equipesati (mensile)"] = fixed_mix(P, [1.0 / len(syms)] * len(syms))
    return out, bench


def evaluate(syms, dates, P, cost):
    strat, bench = strategies(syms, P)
    split = next((i for i, d in enumerate(dates) if d >= SPLIT), len(dates) // 2)
    periods = {"completo": (0, len(dates)), "scelta": (0, split), "verifica": (split, len(dates))}
    res = {}
    for name, W in list(strat.items()) + list(bench.items()):
        res[name] = {}
        for pname, (a, b) in periods.items():
            eq, tr, inv = T.run_weights(W[a:b], P[a:b], cost)
            res[name][pname] = T.metrics(eq, tr, inv, b - a, ppy=PPY)
    return res, list(bench), split


def format_report(syms, dates, P, perf, results_by_cost):
    L_ = ["=== ETF LAB | %d ETF, %d giorni (%s -> %s), rendimento totale ===" % (len(syms), len(dates), dates[0], dates[-1])]
    L_.append("")
    L_.append("PRESTAZIONI (compra e tieni, annualizzate): ultimi 1/3/5/10 anni | dal %s | calo massimo | anno peggiore" % dates[0][:4])
    L_.append("%-5s %-26s %7s %7s %7s %7s | %7s %8s %6s | %s" % ("ETF", "cosa replica", "1 anno", "3 anni", "5 anni", "10 anni",
                                                         "dal " + dates[0][:4], "calo max", "Sharpe", "anno peggiore"))
    f = lambda v: "   n/d" if v is None else "%+6.1f%%" % (v * 100)
    for s in syms:
        r = perf[s]
        L_.append("%-5s %-26s %s %s %s %s | %s %7.0f%% %6.2f | %s %+.0f%%" % (
            s, ETFS[s], f(r["1y"]), f(r["3y"]), f(r["5y"]), f(r["10y"]), f(r["cagr"]), r["mdd"] * 100, r["sharpe"],
            r["worst_year"], r["worst"] * 100))
    for label, (res, bench, split) in results_by_cost.items():
        L_.append("")
        L_.append("REGOLE SEMPLICI - costo %s | 'scelta' fino al %s, 'verifica' dal %s" % (label, dates[split - 1], dates[split]))
        L_.append("%-30s | %-27s | %-27s | %-27s" % ("strategia", "COMPLETO CAGR/maxDD/Sharpe", "SCELTA", "VERIFICA"))
        for name, byp in res.items():
            cells = ["%+5.1f%% %4.0f%% %5.2f" % (byp[p]["cagr"] * 100, byp[p]["mdd"] * 100, byp[p]["sharpe"])
                     for p in ("completo", "scelta", "verifica")]
            L_.append("%-30s | %-27s | %-27s | %-27s%s" % (name, cells[0], cells[1], cells[2],
                                                          "  [riferimento]" if name in bench else ""))
    L_.append("")
    L_.append("Le regole in cassa nei cali riducono il calo massimo ma rinunciano a parte del rialzo: guardare CAGR, calo massimo "
              "e soprattutto che il risultato regga nella 'verifica'. Tasse e conversione valuta reali non sono modellate.")
    return "\n".join(L_)


def run(cache_dir="unibot_data/etf_prices"):
    syms, dates, P = load_prices(cache_dir)
    perf = performance_table(syms, dates, P)
    results = {label: evaluate(syms, dates, P, c) for label, c in COSTS.items()}
    return format_report(syms, dates, P, perf, results)


def main_cli():
    try:
        print(run())
        return 0
    except Exception as e:  # noqa: BLE001
        print("etf-lab non eseguito: %s" % e)
        return 2


def rolling_vs_cash(eq, years, cash_rate=0.025, ppy=PPY):
    """Su tutte le finestre mobili di `years` anni: quota in cui si fa peggio di un deposito, mediana e caso peggiore (rendimento annuo)."""
    n = int(round(years * ppy))
    if len(eq) <= n:
        return None
    ann = (eq[n:] / eq[:-n]) ** (1.0 / years) - 1
    return {"below_cash": float((ann < cash_rate).mean()), "median": float(np.median(ann)), "worst": float(ann.min()),
            "loss": float((ann < 0).mean())}


def vs_cash_report(syms, dates, P, cost=0.003, cash_rate=0.025):
    strat, bench = strategies(syms, P)
    c = {s: i for i, s in enumerate(syms)}
    two = np.zeros(P.shape)
    for a in ("SPY", "QQQ"):
        two[:, c[a]] = T.sma(P, 200)[:, c[a]] < P[:, c[a]]
    two = two * 0.5
    sel = {"S&P 500 compra e tieni": bench["acquista-e-tieni SPY"], "trend S&P 500 SMA200": strat["trend SPY SMA200"],
           "regola in test: 50% SPY+50% QQQ, SMA200": two, "60% SPY / 40% obbligazioni": bench["60% SPY / 40% AGG (mensile)"]}
    out = ["CONFRONTO CON UN DEPOSITO AL %.1f%% annuo, finestre mobili (costo %.2f%% a lato), %s -> %s" % (cash_rate * 100, cost * 100, dates[0], dates[-1]),
           "%-42s %-4s | %-14s %-10s %-10s %-10s" % ("strategia", "anni", "peggio deposito", "in perdita", "mediana", "caso peggiore")]
    for name, W in sel.items():
        eq, _, _ = T.run_weights(W, P, cost)
        for y in (1, 3, 5):
            r = rolling_vs_cash(eq, y, cash_rate)
            out.append("%-42s %-4d | %13.0f%% %9.0f%% %+9.1f%% %+9.1f%%" % (name, y, r["below_cash"] * 100, r["loss"] * 100, r["median"] * 100, r["worst"] * 100))
    return "\n".join(out)
