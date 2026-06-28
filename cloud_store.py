"""Persistenza cloud su Supabase (Postgres) via REST/PostgREST.

Scopo: lo storico dei trade sopravvive ai riavvii del DB SQLite effimero di
Render. Best-effort e non bloccante: se Supabase non e' configurato o non
risponde, il bot continua a funzionare normalmente (SQLite resta la fonte locale).
"""
import requests
from config import SUPABASE_URL, SUPABASE_KEY

_TABLE = "bot_trade_history"
_TIMEOUT = 8


def enabled():
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _headers(extra=None):
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def archive_trade(payload):
    """Inserisce un trade chiuso su Supabase. Ritorna True se ok. Mai solleva."""
    if not enabled():
        return False
    try:
        r = requests.post(f"{SUPABASE_URL}/rest/v1/{_TABLE}",
                          headers=_headers({"Prefer": "return=minimal"}),
                          json=payload, timeout=_TIMEOUT)
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


def fetch_all(limit=2000):
    """Scarica i trade archiviati (piu' recenti prima). Lista vuota in errore."""
    if not enabled():
        return []
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/{_TABLE}",
            headers=_headers(),
            params={"select": "*", "order": "id.desc", "limit": str(limit)},
            timeout=_TIMEOUT)
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def compute_stats():
    """Statistiche aggregate dai trade persistiti su Supabase."""
    rows = fetch_all()
    total = len(rows)
    if total == 0:
        return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0,
                "realized_pnl_eur": 0, "total_fees_eur": 0, "source": "supabase"}
    pnl = sum((r.get("pnl_eur") or 0) for r in rows)
    fees = sum((r.get("entry_fee") or 0) + (r.get("exit_fee") or 0) for r in rows)
    wins = sum(1 for r in rows if (r.get("pnl_eur") or 0) >= 0)
    return {
        "total_trades": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate_pct": round(wins / total * 100, 1),
        "realized_pnl_eur": round(pnl, 2),
        "total_fees_eur": round(fees, 2),
        "gross_pnl_eur": round(pnl + fees, 2),
        "source": "supabase",
    }
