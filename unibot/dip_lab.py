"""Comprare basso e vendere vicino al massimo funziona? Test su Bitcoin (dal 2014) ed Ethereum/BNB/XRP/Solana (dal 2017-2020).

Regola 'compra il ribasso': si compra quando il prezzo scende piu' di `in` sotto il massimo dei 365 giorni precedenti e si vende quando
risale a meno di `out` sotto quel massimo (il 'picco' non si conosce in anticipo: si vende quando il ribasso e' in gran parte recuperato).
Decisione alla chiusura del giorno t, rendimento da t a t+1, costo 0,6% a lato. Riferimenti: acquista-e-tieni e trend sopra la media a 200 giorni.
Cautele: poche entrate indipendenti (3 cicli per Bitcoin), parametri provati in 4 coppie (non scelti guardando il risultato di una sola),
Yahoo e' una fonte non ufficiale, sopravvivenza (solo monete ancora quotate), tasse escluse.
"""
import numpy as np
import pandas as pd

from . import trend_lab as T

PARAMS = [(0.30, 0.10), (0.40, 0.15), (0.50, 0.20), (0.60, 0.30)]
COST = 0.006
HIGH_WINDOW = 365


def drawdown(px, window=HIGH_WINDOW):
    """Distanza dal massimo dei `window` giorni fino a oggi compreso (<= 0)."""
    hi = pd.Series(px).rolling(window, min_periods=window).max().to_numpy()
    return px / hi - 1.0


def dip_weights(px, enter, leave, window=HIGH_WINDOW):
    """1 = in posizione. Isteresi: entra se dd < -enter, esce se dd > -leave; prima di `window` giorni di storia resta in cassa."""
    dd = drawdown(px, window)
    w = np.zeros(len(px))
    on = False
    for t in range(len(px)):
        if np.isfinite(dd[t]):
            if not on and dd[t] < -enter:
                on = True
            elif on and dd[t] > -leave:
                on = False
        w[t] = 1.0 if on else 0.0
    return w


def trend_w(px, n=200):
    s = pd.Series(px).rolling(n, min_periods=n).mean().to_numpy()
    return ((px > s) & np.isfinite(s)).astype(float)


def trades_of(w, px):
    """Elenco dei cicli compra-vendita: (data indice entrata, indice uscita, rendimento lordo)."""
    out = []
    start = None
    for t in range(len(w)):
        if w[t] > 0 and start is None:
            start = t
        if (w[t] == 0 or t == len(w) - 1) and start is not None:
            end = t
            out.append((start, end, px[end] / px[start] - 1))
            start = None
    return out


def evaluate(px, dates, cost=COST):
    px = np.asarray(px, dtype=float)
    P = px[:, None]
    mid = len(px) // 2
    periods = {"completo": (0, len(px)), "1a meta'": (0, mid), "2a meta'": (mid, len(px))}
    strategies = {"acquista e tieni": np.ones(len(px)), "trend sopra SMA200": trend_w(px)}
    for enter, leave in PARAMS:
        strategies["compra a -%d%% / vendi a -%d%%" % (enter * 100, leave * 100)] = dip_weights(px, enter, leave)
    res = {}
    for name, w in strategies.items():
        res[name] = {}
        for pn, (a, b) in periods.items():
            eq, tr, inv = T.run_weights(w[a:b, None], P[a:b], cost)
            m = T.metrics(eq, tr, inv, b - a)
            m["n_trades"] = len(trades_of(w[a:b], px[a:b]))
            res[name][pn] = m
        res[name]["_trades"] = trades_of(w, px)
    return res, {pn: (dates[a], dates[b - 1]) for pn, (a, b) in periods.items()}


def position_now(px, dates):
    """Dove siamo oggi nel ciclo: distanza dal massimo a 365 giorni, dalla SMA200, percentile del prezzo negli ultimi 4 anni."""
    px = np.asarray(px, dtype=float)
    dd = drawdown(px)[-1]
    sma = pd.Series(px).rolling(200).mean().iloc[-1]
    win = px[-4 * 365:] if len(px) >= 4 * 365 else px
    pct = float((win <= px[-1]).mean())
    hi_idx = int(np.argmax(px[-HIGH_WINDOW:]))
    return {"date": dates[-1], "price": float(px[-1]), "dd365": float(dd), "vs_sma200": float(px[-1] / sma - 1),
            "pct4y": pct, "days_since_high": int(HIGH_WINDOW - 1 - hi_idx)}


def format_report(name, res, spans):
    lines = ["--- %s | %s -> %s (costo %.1f%% a lato) ---" % (name, spans["completo"][0], spans["completo"][1], COST * 100),
             "%-30s | %-34s | %-34s | %-34s" % ("strategia", "COMPLETO rend./calo max/Sharpe/in mercato", "1a META' (%s..)" % spans["1a meta'"][0][:4], "2a META' (%s..)" % spans["2a meta'"][0][:4])]
    for sname, byp in res.items():
        cells = []
        for pn in ("completo", "1a meta'", "2a meta'"):
            m = byp[pn]
            cells.append("%+8.0f%% %5.0f%% %5.2f %3.0f%% %2dop" % (m["total"] * 100, m["mdd"] * 100, m["sharpe"], m["invested"] * 100, m["n_trades"]))
        lines.append("%-30s | %-34s | %-34s | %-34s" % (sname, cells[0], cells[1], cells[2]))
    return "\n".join(lines)


def load_long(cache_dir="unibot_data/crypto_long", symbols=("BTC-USD", "ETH-USD", "BNB-USD", "XRP-USD", "SOL-USD")):
    from . import signal_lab as L
    y = L.YahooHistory(cache_dir=cache_dir, start="2014-01-01")
    out = {}
    for s in symbols:
        d = y.get(s)
        if not d:
            raise RuntimeError("prezzi non disponibili per %s (rete o fonte): riprova" % s)
        d = dict(d)
        dates = sorted(d)
        out[s.split("-")[0]] = (dates, np.array([d[k] for k in dates], dtype=float))
    return out


def run(cache_dir="unibot_data/crypto_long"):
    data = load_long(cache_dir)
    parts = ["=== COMPRA BASSO / VENDI VICINO AL PICCO: test su %d anni di crypto ===" % (len(data["BTC"][0]) // 365)]
    for sym in ("BTC", "ETH", "BNB", "XRP", "SOL"):
        dates, px = data[sym]
        res, spans = evaluate(px, dates)
        parts.append(format_report(sym, res, spans))
    parts.append("Lettura: 'rend.' e' il rendimento totale del periodo, 'op' il numero di cicli compra-vendita. Poche operazioni = pochi campioni: nessun risultato e' statistico.")
    return "\n".join(parts)


def main_cli():
    try:
        print(run())
        return 0
    except Exception as e:  # noqa: BLE001
        print("dip-lab non eseguito: %s" % e)
        return 2


SCAN_N = (5, 10, 20, 50, 100, 200)
SCAN_COSTS = (0.0, 0.0035, 0.006)


def sma_scan(px, ns=SCAN_N, costs=SCAN_COSTS):
    """Trend sopra la media a N giorni: rendimento totale e calo massimo a diversi costi, operazioni all'anno, tempo in mercato, meta' per meta'."""
    px = np.asarray(px, dtype=float)
    P = px[:, None]
    mid = len(px) // 2
    out = {}
    for n in ns:
        w = trend_w(px, n)
        row = {"n": n}
        for cost in costs:
            eq, tr, inv = T.run_weights(w[:, None], P, cost)
            m = T.metrics(eq, tr, inv, len(px))
            row["total@%g" % cost] = m["total"]
            row["mdd@%g" % cost] = m["mdd"]
            row["sharpe@%g" % cost] = m["sharpe"]
            row["trades_y"] = m["trades_y"] / 2.0   # run_weights conta ingressi e uscite: cicli = meta'
            row["invested"] = m["invested"]
        for lbl, (a, b) in (("h1", (0, mid)), ("h2", (mid, len(px)))):
            eq, tr, inv = T.run_weights(w[a:b, None], P[a:b], costs[-1])
            row["total_" + lbl] = T.metrics(eq, tr, inv, b - a)["total"]
        out[n] = row
    return out


def format_scan(name, scan, costs=SCAN_COSTS):
    lines = ["--- %s ---" % name,
             "%-8s | %-12s %-12s %-18s | %-8s %-8s | %-10s %-10s" % ("media", "senza costi", "costo 0,35%", "costo 0,6% (rend./calo)", "op/anno", "in merc.", "1a meta'", "2a meta'")]
    for n, r in scan.items():
        lines.append("SMA %-4d | %+10.0f%% %+10.0f%% %+9.0f%% %6.0f%% | %6.0f %7.0f%% | %+9.0f%% %+9.0f%%" % (
            n, r["total@0"] * 100, r["total@0.0035"] * 100, r["total@0.006"] * 100, r["mdd@0.006"] * 100, r["trades_y"], r["invested"] * 100, r["total_h1"] * 100, r["total_h2"] * 100))
    return "\n".join(lines)


def run_scan(cache_dir="unibot_data/crypto_long"):
    data = load_long(cache_dir)
    parts = ["=== TREND A BREVE (media a 5-200 giorni), chiusura giornaliera, solo posizioni lunghe ==="]
    for sym in ("BTC", "ETH", "BNB", "XRP", "SOL"):
        dates, px = data[sym]
        parts.append(format_scan("%s %s -> %s" % (sym, dates[0], dates[-1]), sma_scan(px)))
    parts.append("Colonne: rendimento totale a tre livelli di costo di un lato; 'op/anno' = cicli compra-vendita all'anno; ultime due colonne a costo 0,6%.")
    return "\n".join(parts)
