"""Report testuale (ASCII) dello stato di Unibot."""
import datetime


def _fmt_ts(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%d/%m %H:%M") if ts else "-"


def build_report(snap):
    st = snap["stats"]
    n = st["total"]
    lines = []
    add = lines.append
    add("=== UNIBOT - report (PAPER, nessun ordine reale) ===")
    w = snap["wallets"]
    add("Equity stimata: %.2f EUR | cassa: %s" % (
        snap["equity_eur"], ", ".join("%s %.2f" % (k, v) for k, v in w.items() if v)))
    add("PnL oggi: %+.2f EUR | circuit breaker: %s (perdite di fila: %d)" % (
        snap["day_pnl_eur"], "ATTIVO" if snap["circuit_active"] else "no", snap["consecutive_losses"]))
    reg = snap["regime"]
    add("Regime crypto: %s | Fear&Greed: %s | ingressi bloccati: %s" % (
        reg.get("label"), reg.get("fg"), "SI" if reg.get("block") else "no"))
    ctx = snap.get("stock_ctx") or {}
    if ctx:
        add("Azioni USA: borsa %s | ingressi consentiti: %s%s | SPY %s%%" % (
            "aperta" if ctx.get("open") else "chiusa", "si" if ctx.get("can_enter") else "no",
            (" (" + ",".join(ctx.get("reasons", [])) + ")") if ctx.get("reasons") else "",
            ("%.2f" % ctx["spy_pct"]) if ctx.get("spy_pct") is not None else "n/d"))
    if snap.get("fmp_remaining") is not None:
        add("Chiamate FMP rimaste oggi: %d" % snap["fmp_remaining"])

    add("")
    add("Posizioni aperte (%d):" % len(snap["positions"]))
    for p in snap["positions"]:
        add("  %-14s %-6s entrata %.4f ora %.4f  %+.2f%%%s" % (
            p["symbol"], p["class"], p["entry"], p["last"], p["pnl_pct"],
            ("  [segnale: %s]" % p["signal"]) if p["signal"] else ""))
    if not snap["positions"]:
        add("  nessuna")

    add("")
    if n:
        add("Trade chiusi: %d | vinti %d (%.1f%%) | netto %+.2f EUR | fee %.2f EUR | lordo %+.2f EUR" % (
            n, st["wins"], st["wins"] / n * 100, st["net_eur"], st["fees_eur"], st["gross_eur"]))
        for cls, d in st["by_class"].items():
            add("  %-6s n=%d vinti=%d netto %+.2f EUR fee %.2f EUR" % (cls, d["n"], d["wins"], d["net_eur"], d["fees_eur"]))
        add("  motivi di uscita: " + ", ".join(
            "%s %d (%+.2f)" % (r, d["n"], d["net_eur"]) for r, d in st["by_reason"].items()))
    else:
        add("Trade chiusi: 0 (campione vuoto: nessuna conclusione possibile)")
    add("Campione piccolo = poca affidabilita': servono decine di trade prima di giudicare.")

    add("")
    rej = sorted(snap["rejects"].items(), key=lambda kv: kv[1], reverse=True)[:8]
    add("Perche' non entra (conteggio scarti): " + (", ".join("%s %d" % kv for kv in rej) if rej else "n/d"))

    if snap["signals_active"]:
        add("")
        add("Segnali esterni attivi (NON verificati): " + ", ".join(
            "%s[%s,%s]" % (s["symbol"], s["source"], s["side"]) for s in snap["signals_active"]))
        if snap["signals_unmatched"]:
            add("  non abbinati al mercato osservato: " + ", ".join(s["symbol"] for s in snap["signals_unmatched"]))

    if snap["recent"]:
        add("")
        add("Ultimi trade:")
        for t in snap["recent"][:6]:
            add("  %s %-14s %-14s %+.2f EUR" % (_fmt_ts(t["closed_ts"]), t["symbol"], t["reason"], t["pnl_eur"]))
    return "\n".join(lines)
