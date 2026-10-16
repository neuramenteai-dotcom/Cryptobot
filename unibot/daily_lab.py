"""Le variazioni giornaliere si possono sfruttare? Rendimento del giorno DOPO una forte salita/discesa (chiusura-chiusura).

Per ogni asset: media del giorno dopo, condizionata al movimento di oggi (> +soglia, < -soglia), contro la media di tutti i giorni;
poi la regola 'segui' (compra dopo un rialzo) e 'contrario' (compra dopo un calo) al netto dei costi, in due periodi.
Segnale noto alla chiusura di t, rendimento da t a t+1: nessun look-ahead (ottimistico solo se non si riesce a eseguire alla chiusura).
"""
import numpy as np

from . import trend_lab as T


def cond_stats(r, thr):
    """r[t] = rendimento da t-1 a t. Ritorna statistiche del rendimento del giorno dopo."""
    nxt = r[1:]
    today = r[:-1]
    out = {"all": float(nxt.mean()), "n": len(nxt)}
    for name, mask in (("up", today > thr), ("down", today < -thr)):
        sel = nxt[mask]
        out[name] = {"n": int(mask.sum()), "mean": float(sel.mean()) if len(sel) else 0.0,
                     "hit": float((sel > 0).mean()) if len(sel) else 0.0,
                     "t": float(sel.mean() / (sel.std(ddof=1) / np.sqrt(len(sel)))) if len(sel) > 2 and sel.std(ddof=1) > 0 else 0.0}
    return out


def rule_equity(r, thr, mode, cost):
    """Posizione lunga per il giorno dopo se il movimento di oggi supera la soglia (mode 'segui': > +thr, 'contrario': < -thr)."""
    pos = (r[:-1] > thr) if mode == "segui" else (r[:-1] < -thr)
    pos = pos.astype(float)
    prev = np.concatenate([[0.0], pos[:-1]])
    turn = np.abs(pos - prev)
    gross = pos * r[1:]
    net = (1 - cost * turn) * (1 + gross)
    eq = np.concatenate([[1.0], np.cumprod(net)])
    return eq, int(pos.sum()), float(np.nansum(gross))


def run(series, thresholds, costs, split_frac=0.5):
    lines = []
    for name, (px, cost_levels) in series.items():
        r = px[1:] / px[:-1] - 1
        half = int(len(r) * split_frac)
        for thr in thresholds:
            cs = cond_stats(r, thr)
            lines.append("%-5s soglia %.1f%% | media giorno dopo: tutti %+.3f%% | dopo rialzo %+.3f%% (n=%d, t=%.1f) | dopo calo %+.3f%% (n=%d, t=%.1f)" % (
                name, thr * 100, cs["all"] * 100, cs["up"]["mean"] * 100, cs["up"]["n"], cs["up"]["t"],
                cs["down"]["mean"] * 100, cs["down"]["n"], cs["down"]["t"]))
            for mode in ("segui", "contrario"):
                parts = []
                for lbl, rr in (("1a meta'", r[:half]), ("2a meta'", r[half:])):
                    eq, days, _ = rule_equity(rr, thr, mode, cost_levels)
                    parts.append("%s %+6.1f%% in mercato %d gg" % (lbl, (eq[-1] - 1) * 100, days))
                lines.append("        %-9s dopo costi %.2f%%/lato: %s" % (mode, cost_levels * 100, " | ".join(parts)))
    return "\n".join(lines)
