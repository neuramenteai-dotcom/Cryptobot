"""Settori crypto: dove e' andata la crescita negli ultimi ~4 anni e la leadership di un settore si ripete?

Dati: prezzi giornalieri dei 60 perpetui gia' scaricati dal funding lab (2023-01 -> oggi), raggruppati in settori a mano.
Cautele: solo monete ancora quotate oggi (survivorship: gonfia i settori che hanno avuto molti fallimenti, es. L1 e gaming);
settori assegnati col senno di poi; pochi settori e poche monete per settore (rumore alto); 3 anni e mezzo = un solo ciclo.
Test di persistenza: ogni ~mese si tiene il settore con il miglior rendimento a 90 giorni (peso uguale fra le sue monete),
decisione all'apertura del giorno (come trend_lab), costo di un lato 0,6%.
Flussi: valore bloccato (TVL) per blockchain da DefiLlama, variazione a 90 giorni e a 1 anno: dove arriva capitale.
"""
import numpy as np
import pandas as pd

from . import trend_lab as T

SECTORS = {
    "Blockchain L1 grandi": ["ETH", "SOL", "ADA", "AVAX", "DOT", "NEAR", "ATOM", "TRX", "ICP", "HBAR", "ETC", "EGLD", "ALGO", "XTZ", "NEO", "VET", "KAVA", "FLOW"],
    "Blockchain L1 nuove": ["APT", "SUI", "SEI", "TIA"],
    "Layer 2": ["ARB", "OP", "POL", "IMX", "STX"],
    "DeFi": ["UNI", "AAVE", "COMP", "CRV", "SNX", "LDO", "DYDX", "INJ", "RUNE", "ENA", "ONDO"],
    "AI e dati": ["FET", "RENDER", "GRT", "FIL"],
    "Meme": ["DOGE", "1000PEPE", "1000SHIB", "WIF"],
    "Gaming e metaverso": ["SAND", "MANA", "AXS", "GALA", "APE", "CHZ"],
    "Pagamenti e store of value": ["XRP", "LTC", "BCH", "XLM", "ZEC"],
}
LOOKBACK = 90
EVERY = 30
CHAINS = ["Ethereum", "Solana", "Tron", "BSC", "Base", "Arbitrum", "Sui", "Avalanche", "Hyperliquid L1", "Aptos", "Optimism", "Polygon", "Sei", "TON", "Near"]


def _cols(syms):
    return {(s[:-4] if s.endswith("USDT") else s): i for i, s in enumerate(syms)}


def sector_matrix(P, syms):
    """Peso uguale fra le monete disponibili di ogni settore. Ritorna (nomi, matrice giorni x settori di rendimenti giornalieri, membri)."""
    cols = _cols(syms)
    names, rets, members = [], [], {}
    R = np.where(np.isfinite(P[1:]) & np.isfinite(P[:-1]), P[1:] / P[:-1] - 1, np.nan)
    for name, coins in SECTORS.items():
        idx = [cols[c] for c in coins if c in cols]
        if len(idx) < 3:
            continue
        names.append(name)
        members[name] = [c for c in coins if c in cols]
        rets.append(np.nanmean(R[:, idx], axis=1))
    return names, np.column_stack(rets), members


def yearly_table(names, S, dates):
    """Rendimento per anno solare (composto) di ogni settore; l'ultimo anno e' parziale."""
    d = pd.DataFrame(S, columns=names, index=pd.to_datetime(dates[1:]))
    return (1 + d).groupby(d.index.year).prod() - 1


def leader_weights(P, S, syms, k=1, lookback=LOOKBACK, every=EVERY):
    """Pesi sulle monete: ogni `every` giorni si tengono i `k` settori con il miglior rendimento a `lookback` giorni."""
    cols = _cols(syms)
    names = list(SECTORS)
    avail = [n for n in names if sum(c in cols for c in SECTORS[n]) >= 3]
    cum = np.vstack([np.ones(S.shape[1]), np.cumprod(1 + np.nan_to_num(S), axis=0)])   # indice settoriale, un valore per giorno di P
    W = np.zeros(P.shape)
    cur = np.zeros(P.shape[1])
    for t in range(P.shape[0]):
        if t >= lookback and (t - lookback) % every == 0:
            mom = cum[t] / cum[t - lookback] - 1
            top = np.argsort(-mom)[:k]
            cur = np.zeros(P.shape[1])
            for j in top:
                idx = [cols[c] for c in SECTORS[avail[j]] if c in cols and np.isfinite(P[t, cols[c]])]
                for i in idx:
                    cur[i] += 1.0 / (k * len(idx))
        W[t] = cur
    return W


def _spearman(a, b):
    """Correlazione di rango senza scipy (i pareggi sono trascurabili su rendimenti continui)."""
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def persistence(S, lookback=LOOKBACK, horizon=EVERY):
    """Correlazione di rango (Spearman) fra rendimento passato a `lookback` giorni e futuro a `horizon`, sui settori, a campioni non sovrapposti."""
    cum = np.vstack([np.ones(S.shape[1]), np.cumprod(1 + np.nan_to_num(S), axis=0)])
    corrs = []
    for t in range(lookback, cum.shape[0] - horizon, horizon):
        past = cum[t] / cum[t - lookback] - 1
        fut = cum[t + horizon] / cum[t] - 1
        corrs.append(_spearman(past, fut))
    c = np.array([x for x in corrs if np.isfinite(x)])
    t = float(c.mean() / (c.std(ddof=1) / np.sqrt(len(c)))) if len(c) > 2 and c.std(ddof=1) > 0 else 0.0
    return {"mean": float(c.mean()) if len(c) else 0.0, "n": len(c), "t": t, "pos": float((c > 0).mean()) if len(c) else 0.0}


def fetch_tvl(chain, get=None):
    """Variazione del valore bloccato (TVL) di una blockchain a 90 giorni e a 1 anno, da DefiLlama."""
    import requests
    g = get or (lambda url: requests.get(url, timeout=25).json())
    rows = g("https://api.llama.fi/v2/historicalChainTvl/" + chain.replace(" ", "%20"))
    if not rows or len(rows) < 400:
        return None
    last = rows[-1]["tvl"]
    return {"tvl": last, "d90": last / rows[-91]["tvl"] - 1 if rows[-91]["tvl"] else None,
            "y1": last / rows[-366]["tvl"] - 1 if rows[-366]["tvl"] else None}


def run(cache_dir="unibot_data", with_flows=True):
    syms, dates, P = T.load_daily(cache_dir)
    names, S, members = sector_matrix(P, syms)
    yt = yearly_table(names, S, dates)
    out = ["=== SETTORI CRYPTO | %d monete, %s -> %s ===" % (len(syms), dates[0], dates[-1]), ""]
    out.append("RENDIMENTO PER ANNO (paniere a peso uguale; %s parziale):" % yt.index[-1])
    out.append("%-28s %s" % ("settore (monete)", " ".join("%8s" % y for y in yt.index)))
    for n in sorted(names, key=lambda n: -yt[n].sum()):
        out.append("%-28s %s" % ("%s (%d)" % (n, len(members[n])), " ".join("%+7.0f%%" % (v * 100) for v in yt[n])))
    cols = _cols(syms)
    btc = P[:, cols["BTC"]]
    yb = {}
    for y in yt.index:
        m = [i for i, d in enumerate(dates) if d.startswith(str(y))]
        yb[y] = btc[m[-1]] / btc[m[0]] - 1
    out.append("%-28s %s" % ("(riferimento) BTC", " ".join("%+7.0f%%" % (yb[y] * 100) for y in yt.index)))
    p = persistence(S)
    out += ["", "LA LEADERSHIP SI RIPETE? (settore migliore a 90 giorni -> rendimento relativo nei 30 successivi, correlazione di rango fra settori)",
            "  media %+.2f su %d campioni non sovrapposti, t=%.1f, positiva in %.0f%% dei casi (0 = nessuna ripetizione, 1 = perfetta)" % (p["mean"], p["n"], p["t"], p["pos"] * 100)]
    split = dates.index(T.SPLIT) if T.SPLIT in dates else len(dates) // 2
    periods = {"completo": (0, len(dates)), "scelta": (0, split), "verifica": (split, len(dates))}
    strat = {"segui il settore leader (k=1)": leader_weights(P, S, syms, 1), "segui i 2 settori leader": leader_weights(P, S, syms, 2),
             "BTC compra e tieni": np.where(np.arange(P.shape[1]) == cols["BTC"], 1.0, 0.0)[None, :].repeat(P.shape[0], 0),
             "tutte le 60 monete equipesate": T.equal_weight_weights(P, every=30)}
    out += ["", "STRATEGIA 'SEGUI IL SETTORE LEADER' (costo 0,6% a lato): rendimento totale / calo massimo / Sharpe"]
    out.append("%-34s | %-26s | %-26s | %-26s" % ("strategia", "COMPLETO", "SCELTA (<2025)", "VERIFICA (>=2025)"))
    for name, W in strat.items():
        cells = []
        for pn, (a, b) in periods.items():
            eq, tr, inv = T.run_weights(W[a:b], P[a:b], 0.006)
            m = T.metrics(eq, tr, inv, b - a)
            cells.append("%+7.0f%% %5.0f%% %5.2f" % (m["total"] * 100, m["mdd"] * 100, m["sharpe"]))
        out.append("%-34s | %-26s | %-26s | %-26s" % (name, cells[0], cells[1], cells[2]))
    if with_flows:
        out += ["", "DOVE ARRIVA CAPITALE (valore bloccato nei protocolli, DefiLlama): variazione a 90 giorni e a 1 anno"]
        rows = []
        for ch in CHAINS:
            try:
                r = fetch_tvl(ch)
            except Exception:  # noqa: BLE001
                r = None
            if r and r["d90"] is not None and r["y1"] is not None and r["tvl"] > 5e7:
                rows.append((ch, r))
        for ch, r in sorted(rows, key=lambda x: -x[1]["y1"]):
            out.append("  %-16s TVL %6.2f mld$ | 90 gg %+6.0f%% | 1 anno %+6.0f%%" % (ch, r["tvl"] / 1e9, r["d90"] * 100, r["y1"] * 100))
        if not rows:
            out.append("  dati DefiLlama non raggiungibili ora: riprova")
    out += ["", "Limiti: sopravvivenza (solo monete vive oggi), settori scelti col senno di poi, un solo ciclo (2023-2026), il TVL e' in dollari e sale anche solo perche' sale il prezzo dei token."]
    return "\n".join(out)


def main_cli():
    try:
        print(run())
        return 0
    except Exception as e:  # noqa: BLE001
        print("sector-lab non eseguito: %s (serve prima --funding-lab per scaricare i prezzi)" % e)
        return 2
