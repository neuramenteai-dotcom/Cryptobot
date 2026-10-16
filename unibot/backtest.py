"""Replay storico della strategia crypto sulle candele 5m di Coinbase.

Usa le stesse funzioni di strategy.py del bot (stesso codice, non una copia).
Confronta due set di regole:
  - "vecchie": nessun blocco in panico, nessun tetto al pump, nessun filtro di
    liquidita' ne' di estensione (il comportamento del bot Render).
  - "nuove": le regole di Unibot.

Limiti onesti: esecuzione a candela (apertura della successiva + slippage),
stop controllati sul minimo della candela in modo pessimistico, nessuna
profondita' di book, simboli = quelli listati OGGI (survivorship bias),
ogni simbolo e' indipendente (nessun limite di portafoglio).
"""
import datetime
import time
import types
from collections import Counter

import requests

from . import settings as S
from .indicators import sma
from .models import Candidate
from .strategy import crypto_entry, exit_reason
from .universe import retry

TF_MS = 5 * 60 * 1000
BARS_24H = 288


def make_cfg(**over):
    d = {k: getattr(S, k) for k in dir(S) if k.isupper()}
    d.update(over)
    return types.SimpleNamespace(**d)


RULES = {
    "vecchie": dict(gate=False, cfg=dict(CRYPTO_MIN_QUOTE_VOLUME=0.0, CRYPTO_MAX_PCT=1e9, CRYPTO_MAX_EXTENSION=1e9)),
    "nuove": dict(gate=True, cfg={}),
}


def _retry(fn, tries=5, delay=3.0):
    return retry(fn, tries, delay)


def fetch_history(ex, symbol, start_ms, end_ms, timeframe="5m"):
    """Candele paginando con `since` (300 per richiesta). Una pagina persa NON viene ignorata
    in silenzio: dopo i tentativi il simbolo viene segnalato come incompleto."""
    out, since = {}, start_ms
    while since < end_ms:
        try:
            rows = _retry(lambda: ex.fetch_ohlcv(symbol, timeframe, since=since, limit=300))
        except Exception:
            return []  # storico incompleto: meglio escluderlo che testare su un buco
        if not rows:
            break
        for r in rows:
            if r[0] < end_ms:
                out[r[0]] = r
        last = rows[-1][0]
        if last < since:
            break
        since = last + TF_MS
        if len(rows) < 300 and last + TF_MS >= end_ms:
            break
    return [out[k] for k in sorted(out)]


def fng_history(limit=400):
    """Fear&Greed giornaliero (UTC): {'YYYY-MM-DD': valore}."""
    try:
        data = _retry(lambda: requests.get("https://api.alternative.me/fng/?limit=%d&format=json" % limit,
                                           timeout=20).json()["data"])
    except Exception:
        return {}  # senza storico F&G il blocco in panico non si puo' valutare: run() lo segnala
    out = {}
    for d in data:
        day = datetime.datetime.fromtimestamp(int(d["timestamp"]), datetime.timezone.utc).strftime("%Y-%m-%d")
        out[day] = int(d["value"])
    return out


def pick_symbols(ex, n, quote="USDC"):
    """n simboli distribuiti sulla classifica per volume (alti e medi), non solo i piu' liquidi."""
    tickers = _retry(ex.fetch_tickers)
    rows = []
    for sym, t in tickers.items():
        if "/" not in sym:
            continue
        base, q = sym.split("/", 1)
        if q != quote or base in S.CRYPTO_EXCLUDED_BASES:
            continue
        vq = t.get("quoteVolume") or (t.get("baseVolume") or 0) * (t.get("last") or 0)
        if vq and vq > 20000:
            rows.append((vq, sym))
    rows.sort(reverse=True)
    if len(rows) <= n:
        return [s for _, s in rows]
    step = len(rows) / float(n)
    return [rows[int(i * step)][1] for i in range(n)]


def replay_symbol(sym, candles, fng, rules, fee, slip, cfg):
    n = len(candles)
    if n < BARS_24H + 60:
        return []
    closes = [c[4] for c in candles]
    qv = [closes[i] * candles[i][5] for i in range(n)]
    pre = [0.0]
    for v in qv:
        pre.append(pre[-1] + v)

    trades, pos, cooldown_until = [], None, -1
    i = BARS_24H
    while i < n - 1:
        c = candles[i]
        if pos is None:
            if i >= cooldown_until:
                pct = (closes[i] / closes[i - BARS_24H] - 1) * 100
                vq = pre[i + 1] - pre[i + 1 - BARS_24H]
                day = datetime.datetime.fromtimestamp(c[0] / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
                fg = fng.get(day)
                regime = {"block": bool(rules["gate"] and fg is not None and fg <= cfg.PANIC_BLOCK_BELOW),
                          "label": "panico"}
                cand = Candidate(sym, "crypto", "USDC", closes[i], pct, vq)
                dec = crypto_entry(cand, closes[max(0, i - 99):i + 1], regime, cfg)
                if dec.ok:
                    entry_px = candles[i + 1][1] * (1 + slip)
                    pos = {"entry": entry_px, "hi": entry_px, "ts": candles[i + 1][0]}
                    i += 1
                    continue
            i += 1
            continue

        low, high, close, ts = c[3], c[2], c[4], c[0]
        trail_px = pos["hi"] * (1 - cfg.CRYPTO_TRAIL_PCT)
        exit_px, reason = None, None
        if low <= trail_px:                      # stop controllato prima (pessimistico)
            exit_px = min(trail_px, c[1])
            reason = "STOP LOSS" if pos["hi"] <= pos["entry"] else "TRAILING STOP"
        else:
            pos["hi"] = max(pos["hi"], high)
            held = (ts - pos["ts"]) / 1000.0
            sma_val = sma(closes[max(0, i - 9):i + 1], 10)
            breakeven = 2 * (fee + slip) + cfg.MIN_NET_PROFIT_PCT
            reason = exit_reason(pos["entry"], pos["hi"], close, held, cfg.CRYPTO_TRAIL_PCT,
                                 cfg.CRYPTO_MAX_HOLD_HOURS * 3600, sma_val, breakeven)
            if reason:
                exit_px = close
        if reason:
            net_exit = exit_px * (1 - slip)
            ret = (1 - fee) * (1 - fee) * net_exit / pos["entry"] - 1
            trades.append({"symbol": sym, "ret": ret, "reason": reason,
                           "hold_h": (ts - pos["ts"]) / 3.6e6})
            if ret < 0:
                cooldown_until = i + S.COOLDOWN_MINUTES // 5
            pos = None
        i += 1
    return trades


def summarize(trades):
    n = len(trades)
    if not n:
        return {"n": 0}
    rets = [t["ret"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [-r for r in rets if r <= 0]
    return {
        "n": n, "win_pct": 100.0 * len(wins) / n, "mean_pct": 100.0 * sum(rets) / n,
        "sum_pct": 100.0 * sum(rets),
        "pf": (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else float("inf"),
        "hold_h": sum(t["hold_h"] for t in trades) / n,
        "reasons": dict(Counter(t["reason"] for t in trades)),
    }


def run(ex, start_ms, end_ms, n_symbols=40, quote="USDC", log=print):
    log("selezione simboli...")
    symbols = pick_symbols(ex, n_symbols, quote)
    log("scarico storico di %d simboli (%s)..." % (len(symbols), quote))
    history = {}
    for k, sym in enumerate(symbols, 1):
        h = fetch_history(ex, sym, start_ms - BARS_24H * TF_MS, end_ms)
        if len(h) >= BARS_24H + 60:
            history[sym] = h
        log("  %2d/%d %-14s %d candele" % (k, len(symbols), sym, len(h)))
    fng = fng_history()
    results = {}
    for name, spec in RULES.items():
        for label, fee, slip in (("con fee %.1f%%" % (S.CRYPTO_FEE_RATE * 100), S.CRYPTO_FEE_RATE, S.CRYPTO_SLIPPAGE_BPS / 1e4),
                                 ("lordo (fee 0)", 0.0, 0.0)):
            cfg = make_cfg(**spec["cfg"])
            trades = []
            for sym, candles in history.items():
                trades += replay_symbol(sym, candles, fng, spec, fee, slip, cfg)
            results[(name, label)] = summarize(trades)
    in_window = sorted(d for d in fng if start_ms / 1000 - 86400 <= datetime.datetime.strptime(d, "%Y-%m-%d").replace(
        tzinfo=datetime.timezone.utc).timestamp() <= end_ms / 1000)
    return {"symbols": list(history), "results": results, "fng": fng, "fng_days": in_window,
            "requested": len(symbols)}


def format_results(out, start_ms, end_ms):
    a = datetime.datetime.fromtimestamp(start_ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
    b = datetime.datetime.fromtimestamp(end_ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
    lines = ["=== BACKTEST crypto %s -> %s | %d simboli con storico completo (su %d richiesti) ===" % (
        a, b, len(out["symbols"]), out.get("requested", len(out["symbols"])))]
    days = out.get("fng_days", [])
    panic = [d for d in days if out["fng"][d] <= S.PANIC_BLOCK_BELOW]
    if days:
        lines.append("Fear&Greed: %d giorni con dato, di cui %d in panico (<= %d): il blocco poteva scattare solo in quei giorni." % (
            len(days), len(panic), S.PANIC_BLOCK_BELOW))
    else:
        lines.append("ATTENZIONE: nessun dato Fear&Greed nella finestra: il blocco in panico NON e' stato valutato.")
    lines.append("%-8s %-14s %5s %7s %9s %9s %6s %6s  %s" % (
        "regole", "scenario", "n", "win%", "media%", "somma%", "PF", "ore", "uscite"))
    for (name, label), r in out["results"].items():
        if not r["n"]:
            lines.append("%-8s %-14s %5d  (nessun trade)" % (name, label, 0))
            continue
        lines.append("%-8s %-14s %5d %6.1f%% %+8.3f%% %+8.2f%% %6.2f %6.1f  %s" % (
            name, label, r["n"], r["win_pct"], r["mean_pct"], r["sum_pct"],
            r["pf"] if r["pf"] != float("inf") else 99.0, r["hold_h"],
            ",".join("%s:%d" % kv for kv in r["reasons"].items())))
    lines.append("Limiti: survivorship bias, fill ottimistici, simboli indipendenti, campione finito.")
    return "\n".join(lines)
