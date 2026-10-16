"""Quale media mobile (da 1 a 200 giorni) funziona meglio, e dove c'e' spazio sopra il 10% l'anno?

Regola: in posizione (lunga, senza leva) il giorno dopo se la chiusura di oggi e' sopra la media delle N chiusure PRECEDENTI
(N=1: oggi sopra ieri), altrimenti in liquidita' a rendimento 0. Rendimento da t a t+1, costo per ogni variazione di peso.
Costi di un lato: ETF/indici 0,1% (ottimistico, commissione zero) e 0,3% (prudente); crypto 0,35% e 0,6%.

Onesta': scegliere la N migliore guardando tutto il periodo e' un inganno (con 200 prove, qualcuna va bene per caso). Per questo la N si sceglie
solo sulla PRIMA meta' dei dati (in campione) e si guarda come va nella SECONDA (fuori campione). Si riporta anche la quota di asset che superano il 10%
in entrambe le meta'. Limiti: sopravvivenza (monete/ETF ancora quotati oggi), prezzi Yahoo non ufficiali, indici di prezzo senza dividendi, tasse escluse.
"""
import numpy as np

CRYPTO_COST = (0.0035, 0.006)
ETF_COST = (0.001, 0.003)
THRESH = 0.10
NS = np.arange(1, 201)

ETFS = ["SPY", "QQQ", "IWM", "DIA", "MDY", "XLK", "XLF", "XLE", "XLV", "XLU", "XLI", "XLP", "XLY", "XLB",
        "EFA", "EEM", "EWJ", "EWG", "EWU", "EWZ", "INDA", "FXI", "EWY", "EWT", "EWW", "EZA", "VGK", "EWC", "EWA",
        "AGG", "TLT", "IEF", "HYG", "LQD", "GLD", "SLV", "USO", "DBC", "VNQ"]
INDICES = ["^N225", "^GDAXI", "^FTSE", "FTSEMIB.MI", "^HSI", "^NSEI", "^BVSP", "^GSPC", "^NDX"]


def prior_mean_signals(px, ns=NS):
    """Matrice giorni x N di segnali booleani: chiusura > media delle N chiusure precedenti. Prima di N giorni di storia = 0."""
    px = np.asarray(px, dtype=float)
    c = np.concatenate([[0.0], np.cumsum(px)])
    T = len(px)
    sig = np.zeros((T, len(ns)), dtype=bool)
    for j, n in enumerate(ns):
        if n >= T:
            continue
        t = np.arange(n, T)
        mean = (c[t] - c[t - n]) / n
        sig[n:, j] = px[n:] > mean
    return sig


def backtest(px, sig, cost, ppy):
    """Tutte le N insieme: segnale del giorno t guadagna il rendimento t->t+1. Ritorna metriche per colonna."""
    px = np.asarray(px, dtype=float)
    R = px[1:] / px[:-1] - 1.0
    W = sig[:-1].astype(float)
    prev = np.vstack([np.zeros((1, W.shape[1])), W[:-1]])
    turn = np.abs(W - prev)
    eq = np.cumprod((1.0 - cost * turn) * (1.0 + W * R[:, None]), axis=0)
    eq = np.vstack([np.ones((1, W.shape[1])), eq])
    years = max(len(R) / float(ppy), 1e-9)
    cagr = np.where(eq[-1] > 0, eq[-1] ** (1.0 / years) - 1.0, -1.0)
    peak = np.maximum.accumulate(eq, axis=0)
    mdd = (eq / peak - 1.0).min(axis=0)
    cycles = turn.sum(axis=0) / 2.0 / years
    return {"cagr": cagr, "mdd": mdd, "cycles_y": cycles, "invested": W.mean(axis=0)}


def buy_hold(px, ppy):
    px = np.asarray(px, dtype=float)
    years = max((len(px) - 1) / float(ppy), 1e-9)
    peak = np.maximum.accumulate(px)
    return {"cagr": (px[-1] / px[0]) ** (1.0 / years) - 1.0, "mdd": float((px / peak - 1.0).min())}


def analyze_asset(px, cost, ppy, split):
    """Metriche per N su tutto, prima meta' e seconda meta' (split = indice di taglio). B&H nelle tre finestre."""
    px = np.asarray(px, dtype=float)
    sig = prior_mean_signals(px)
    out = {}
    for lbl, (a, b) in (("full", (0, len(px))), ("h1", (0, split)), ("h2", (split, len(px)))):
        seg = px[a:b]
        out[lbl] = backtest(seg, sig[a:b], cost, ppy)   # segnale calcolato sulla storia completa fino a t: nessuna informazione futura
        out[lbl]["bh"] = buy_hold(seg, ppy)
    return out


def aggregate(assets, ns=NS):
    """assets: lista di dict analyze_asset. Per ogni N: mediane fra asset e quota di asset sopra soglia in entrambe le meta'."""
    def stack(lbl, key):
        return np.vstack([a[lbl][key] for a in assets])
    agg = {}
    for lbl in ("full", "h1", "h2"):
        agg[lbl + "_cagr_med"] = np.median(stack(lbl, "cagr"), axis=0)
        agg[lbl + "_mdd_med"] = np.median(stack(lbl, "mdd"), axis=0)
        agg[lbl + "_bh_cagr_med"] = float(np.median([a[lbl]["bh"]["cagr"] for a in assets]))
        agg[lbl + "_bh_mdd_med"] = float(np.median([a[lbl]["bh"]["mdd"] for a in assets]))
    both = (stack("h1", "cagr") > THRESH) & (stack("h2", "cagr") > THRESH)
    agg["share_both"] = both.mean(axis=0)
    agg["share_h2"] = (stack("h2", "cagr") > THRESH).mean(axis=0)
    agg["cycles_y"] = np.median(stack("full", "cycles_y"), axis=0)
    agg["n_assets"] = len(assets)
    return agg


def best_n_in_sample(agg, ns=NS):
    j = int(np.argmax(agg["h1_cagr_med"]))
    return int(ns[j]), j


def winners(names, assets, j, min_both=True):
    """Asset con CAGR netto sopra soglia in entrambe le meta' per la colonna j, con il confronto col compra-e-tieni."""
    rows = []
    for nm, a in zip(names, assets):
        c1, c2, cf = a["h1"]["cagr"][j], a["h2"]["cagr"][j], a["full"]["cagr"][j]
        if (c1 > THRESH and c2 > THRESH) or (not min_both and c2 > THRESH):
            rows.append({"name": nm, "h1": float(c1), "h2": float(c2), "full": float(cf), "mdd": float(a["full"]["mdd"][j]),
                         "bh": float(a["full"]["bh"]["cagr"]), "bh_mdd": float(a["full"]["bh"]["mdd"]), "cycles_y": float(a["full"]["cycles_y"][j])})
    return sorted(rows, key=lambda r: -r["full"])


def load_crypto_short(cache_dir="unibot_data"):
    from . import trend_lab as T
    syms, dates, P = T.load_daily(cache_dir)
    out = {}
    for j, s in enumerate(syms):
        col = P[:, j]
        ok = np.isfinite(col)
        if ok.sum() < 600:
            continue
        idx = np.flatnonzero(ok)
        seg = col[idx[0]: idx[-1] + 1]
        if np.isnan(seg).any():
            continue
        out[s[:-4] if s.endswith("USDT") else s] = (dates[idx[0]: idx[-1] + 1], seg)
    return out


def load_yahoo(symbols, cache_dir, min_days=1500):
    from . import signal_lab as L
    y = L.YahooHistory(cache_dir=cache_dir, start="2003-01-01")
    out, missing = {}, []
    for s in symbols:
        d = y.get(s)
        if not d:
            missing.append(s)
            continue
        d = dict(d)
        dates = sorted(d)
        if len(dates) < min_days:
            missing.append(s)
            continue
        out[s] = (dates, np.array([d[k] for k in dates], dtype=float))
    return out, missing


def split_index(dates, split_date=None):
    if split_date:
        for i, d in enumerate(dates):
            if d >= split_date:
                return max(60, min(i, len(dates) - 60))
    return len(dates) // 2


def run_group(data, cost, ppy, split_date=None):
    names, assets = [], []
    for nm, (dates, px) in data.items():
        if len(px) < 450:
            continue
        names.append(nm)
        assets.append(analyze_asset(px, cost, ppy, split_index(dates, split_date)))
    return names, assets


KEY_N = (1, 2, 3, 5, 7, 10, 15, 20, 30, 50, 75, 100, 150, 200)


def format_group(title, names, assets, agg, cost, note=""):
    lines = ["=== %s | %d asset | costo %.2f%% a lato %s===" % (title, agg["n_assets"], cost * 100, note)]
    lines.append("compra e tieni (mediana): prima meta' %+.1f%% (calo %.0f%%) | seconda meta' %+.1f%% (calo %.0f%%)" % (
        agg["h1_bh_cagr_med"] * 100, agg["h1_bh_mdd_med"] * 100, agg["h2_bh_cagr_med"] * 100, agg["h2_bh_mdd_med"] * 100))
    lines.append("%-6s | %-9s %-9s %-9s | %-8s | %-9s | %-9s %-9s" % ("media N", "1a meta'", "2a meta'", "completo", "calo med", "cicli/anno", "% >10% 2a", "% >10% in entrambe"))
    for n in KEY_N:
        j = n - 1
        lines.append("N=%-4d | %+8.1f%% %+8.1f%% %+8.1f%% | %7.0f%% | %9.0f | %8.0f%% %8.0f%%" % (
            n, agg["h1_cagr_med"][j] * 100, agg["h2_cagr_med"][j] * 100, agg["full_cagr_med"][j] * 100, agg["full_mdd_med"][j] * 100,
            agg["cycles_y"][j], agg["share_h2"][j] * 100, agg["share_both"][j] * 100))
    n_star, j = best_n_in_sample(agg)
    lines.append("N scelta sulla sola 1a meta' (in campione): N=%d -> 1a meta' %+.1f%%, 2a meta' (fuori campione) %+.1f%%, asset sopra il 10%% fuori campione: %.0f%%" % (
        n_star, agg["h1_cagr_med"][j] * 100, agg["h2_cagr_med"][j] * 100, agg["share_h2"][j] * 100))
    w = winners(names, assets, j)
    lines.append("Asset sopra il %.0f%% netto in ENTRAMBE le meta' con N=%d (%d su %d):" % (THRESH * 100, n_star, len(w), agg["n_assets"]))
    for r in w[:15]:
        lines.append("   %-10s 1a %+6.1f%% 2a %+6.1f%% | calo %4.0f%% (compra-e-tieni %+6.1f%%, calo %4.0f%%) | %2.0f cicli/anno" % (
            r["name"], r["h1"] * 100, r["h2"] * 100, r["mdd"] * 100, r["bh"] * 100, r["bh_mdd"] * 100, r["cycles_y"]))
    return "\n".join(lines)


def run(cache="unibot_data"):
    parts = ["=== SCANSIONE MEDIE MOBILI N=1..200 su crypto, ETF e indici (solo posizioni lunghe) ==="]
    short = load_crypto_short(cache)
    long_syms = ("BTC-USD", "ETH-USD", "BNB-USD", "XRP-USD", "SOL-USD")
    from . import dip_lab
    longd = {k: v for k, v in dip_lab.load_long(cache + "/crypto_long", long_syms).items()}
    etf, miss_e = load_yahoo(ETFS, cache + "/etf_prices")
    idx, miss_i = load_yahoo(INDICES, cache + "/etf_prices")
    groups = [("CRYPTO 60 monete (2023-2026)", short, 365, "2025-01-01", CRYPTO_COST),
              ("CRYPTO 5 grandi (dal 2014/2017/2020)", longd, 365, None, CRYPTO_COST),
              ("ETF (%d) azioni, obbligazioni, materie prime" % len(etf), etf, 252, None, ETF_COST),
              ("INDICI di prezzo (%d)" % len(idx), idx, 252, None, ETF_COST)]
    summary = []
    for title, data, ppy, sd, costs in groups:
        for cost in costs:
            names, assets = run_group(data, cost, ppy, sd)
            if not assets:
                continue
            agg = aggregate(assets)
            parts.append(format_group(title, names, assets, agg, cost))
            n_star, j = best_n_in_sample(agg)
            summary.append((title, cost, n_star, agg["h1_cagr_med"][j], agg["h2_cagr_med"][j], agg["share_h2"][j], agg["h2_bh_cagr_med"]))
    parts.append("RIEPILOGO: N scelta in campione -> risultato fuori campione (mediana fra asset)")
    for t, c, n, h1, h2, sh, bh in summary:
        parts.append("  %-48s costo %.2f%%: N=%-3d 1a %+6.1f%% | 2a %+6.1f%% | >10%% nella 2a: %3.0f%% | compra-e-tieni 2a %+6.1f%%" % (t, c * 100, n, h1 * 100, h2 * 100, sh * 100, bh * 100))
    if miss_e or miss_i:
        parts.append("Non disponibili o troppo corti: %s" % ", ".join(miss_e + miss_i))
    return "\n".join(parts)


def main_cli():
    try:
        print(run())
        return 0
    except Exception as e:  # noqa: BLE001
        print("ma-scan non eseguito: %s" % e)
        return 2
