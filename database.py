import sqlite3
import os
import json
import datetime

DB_NAME = "bot_state.db"


def _connect():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=1")
    cursor.execute("PRAGMA cache_size=-8000")
    cursor.execute("PRAGMA temp_store=MEMORY")
    return conn


def init_db():
    conn = _connect()
    cursor = conn.cursor()
    
    # known_markets
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS known_markets (
            symbol TEXT PRIMARY KEY,
            base_currency TEXT NOT NULL,
            quote_currency TEXT NOT NULL,
            first_seen TEXT DEFAULT (datetime('now'))
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_km_quote ON known_markets(quote_currency)")
    
    # open_trades
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS open_trades (
            symbol TEXT PRIMARY KEY,
            entry_price REAL,
            current_price REAL,
            highest_price REAL,
            amount_base REAL,
            amount_eur REAL,
            amount_quote REAL,
            amount_eur_equiv REAL,
            entry_fee_eur REAL DEFAULT 0,
            estimated_exit_fee REAL DEFAULT 0,
            fee_currency TEXT DEFAULT 'EUR',
            quote TEXT DEFAULT 'EUR',
            adopted INTEGER DEFAULT 0,
            new_listing INTEGER DEFAULT 0,
            time TEXT,
            opened_at TEXT,
            updated_at TEXT
        )
    """)
    
    # trade_history
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            highest_price REAL,
            amount_base REAL NOT NULL,
            amount_quote REAL NOT NULL,
            pnl_eur REAL NOT NULL,
            entry_fee REAL DEFAULT 0,
            exit_fee REAL DEFAULT 0,
            fee_currency TEXT DEFAULT 'EUR',
            quote TEXT DEFAULT 'EUR',
            close_reason TEXT,
            opened_at TEXT NOT NULL,
            closed_at TEXT NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_th_symbol ON trade_history(symbol)")
    
    # circuit_breaker_log
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS circuit_breaker_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            triggered_at TEXT NOT NULL,
            reason TEXT NOT NULL,
            consecutive_losses INTEGER DEFAULT 0,
            resumed_at TEXT
        )
    """)

    # bot_meta
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    conn.commit()
    
    # Migrazione vecchie colonne
    for col, decl in (
        ("amount_quote", "REAL"),
        ("amount_eur_equiv", "REAL"),
        ("estimated_exit_fee", "REAL DEFAULT 0"),
        ("fee_currency", "TEXT DEFAULT 'EUR'"),
        ("opened_at", "TEXT"),
        ("updated_at", "TEXT"),
        ("quote", "TEXT DEFAULT 'EUR'"),
        ("entry_fee_eur", "REAL DEFAULT 0"),
        ("adopted", "INTEGER DEFAULT 0"),
        ("new_listing", "INTEGER DEFAULT 0"),
        ("time", "TEXT")
    ):
        try:
            cursor.execute(f"ALTER TABLE open_trades ADD COLUMN {col} {decl}")
            conn.commit()
        except sqlite3.OperationalError:
            pass

    _migrate_v2(cursor, conn)
    conn.close()


def _migrate_v2(cursor, conn):
    cursor.execute("SELECT value FROM bot_meta WHERE key='known_markets'")
    row = cursor.fetchone()
    if row and row[0]:
        try:
            markets = json.loads(row[0])
            for sym in markets:
                parts = sym.split('/')
                base = parts[0]
                quote = parts[1] if len(parts) > 1 else 'EUR'
                cursor.execute("INSERT OR IGNORE INTO known_markets (symbol, base_currency, quote_currency) VALUES (?, ?, ?)", (sym, base, quote))
            cursor.execute("DELETE FROM bot_meta WHERE key='known_markets'")
            conn.commit()
        except:
            pass

    try:
        cursor.execute("UPDATE open_trades SET amount_quote = amount_eur WHERE amount_quote IS NULL AND amount_eur IS NOT NULL")
        cursor.execute("UPDATE open_trades SET amount_eur_equiv = amount_eur WHERE amount_eur_equiv IS NULL AND amount_eur IS NOT NULL")
        
        today_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT')
        cursor.execute("UPDATE open_trades SET opened_at = ? || time || 'Z' WHERE opened_at IS NULL AND time IS NOT NULL", (today_str,))
        conn.commit()
    except Exception:
        pass


def save_trade(symbol, trade_data):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO open_trades
        (symbol, entry_price, current_price, highest_price, amount_base,
         amount_eur, amount_quote, amount_eur_equiv, entry_fee_eur, estimated_exit_fee, fee_currency, adopted, quote, new_listing, time, opened_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        symbol,
        trade_data.get('entry_price', 0),
        trade_data.get('current_price', trade_data.get('entry_price', 0)),
        trade_data.get('highest_price', trade_data.get('entry_price', 0)),
        trade_data.get('amount_base', 0),
        trade_data.get('amount_eur', 0),
        trade_data.get('amount_quote', trade_data.get('amount_eur', 0)),
        trade_data.get('amount_eur_equiv', trade_data.get('amount_eur', 0)),
        trade_data.get('entry_fee_eur', 0.0),
        trade_data.get('estimated_exit_fee', 0.0),
        trade_data.get('fee_currency', 'EUR'),
        1 if trade_data.get('adopted') else 0,
        trade_data.get('quote', 'EUR'),
        1 if trade_data.get('new_listing') else 0,
        trade_data.get('time', ''),
        trade_data.get('opened_at', datetime.datetime.now(datetime.timezone.utc).isoformat()),
        datetime.datetime.now(datetime.timezone.utc).isoformat()
    ))
    conn.commit()
    conn.close()


def load_trades():
    if not os.path.exists(DB_NAME):
        init_db()
        return {}
    init_db()
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol, entry_price, current_price, highest_price, amount_base,
               amount_eur, entry_fee_eur, adopted, quote, new_listing, time,
               amount_quote, amount_eur_equiv, estimated_exit_fee, fee_currency, opened_at, updated_at
        FROM open_trades
    """)
    rows = cursor.fetchall()
    conn.close()

    trades = {}
    for r in rows:
        trades[r[0]] = {
            "entry_price": r[1],
            "current_price": r[2],
            "highest_price": r[3],
            "amount_base": r[4],
            "amount_eur": r[5],
            "entry_fee_eur": r[6] or 0.0,
            "adopted": bool(r[7]),
            "quote": r[8] or 'EUR',
            "new_listing": bool(r[9]),
            "time": r[10] or '',
            "amount_quote": r[11] if r[11] is not None else r[5],
            "amount_eur_equiv": r[12] if r[12] is not None else r[5],
            "estimated_exit_fee": r[13] or 0.0,
            "fee_currency": r[14] or 'EUR',
            "opened_at": r[15],
            "updated_at": r[16],
            "pnl_pct": 0.0
        }
    return trades


def remove_trade(symbol):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM open_trades WHERE symbol=?", (symbol,))
    conn.commit()
    conn.close()


def archive_trade(symbol, entry_price, exit_price, highest_price, amount_base, amount_quote, pnl_eur, entry_fee, exit_fee, fee_currency, quote, close_reason, opened_at):
    conn = _connect()
    cursor = conn.cursor()
    closed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cursor.execute("""
        INSERT INTO trade_history
        (symbol, entry_price, exit_price, highest_price, amount_base, amount_quote, pnl_eur, entry_fee, exit_fee, fee_currency, quote, close_reason, opened_at, closed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (symbol, entry_price, exit_price, highest_price, amount_base, amount_quote, pnl_eur, entry_fee, exit_fee, fee_currency, quote, close_reason, opened_at, closed_at))
    cursor.execute("DELETE FROM open_trades WHERE symbol=?", (symbol,))
    conn.commit()
    conn.close()

    # Persistenza cloud (Supabase): sopravvive ai riavvii del DB effimero.
    try:
        import cloud_store
        cloud_store.archive_trade({
            "symbol": symbol, "entry_price": entry_price, "exit_price": exit_price,
            "pnl_eur": pnl_eur, "entry_fee": entry_fee, "exit_fee": exit_fee,
            "fee_currency": fee_currency, "quote": quote, "close_reason": close_reason,
            "opened_at": str(opened_at),
        })
    except Exception:
        pass


def log_circuit_breaker(reason, consecutive_losses):
    conn = _connect()
    cursor = conn.cursor()
    triggered_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cursor.execute("""
        INSERT INTO circuit_breaker_log (triggered_at, reason, consecutive_losses)
        VALUES (?, ?, ?)
    """, (triggered_at, reason, consecutive_losses))
    conn.commit()
    conn.close()


def load_known_markets():
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("SELECT symbol FROM known_markets")
        rows = cursor.fetchall()
        conn.close()
        return set(r[0] for r in rows)
    except:
        return set()


def save_known_markets(markets_set):
    conn = _connect()
    cursor = conn.cursor()
    for sym in markets_set:
        parts = sym.split('/')
        base = parts[0]
        quote = parts[1] if len(parts) > 1 else 'EUR'
        cursor.execute("INSERT OR IGNORE INTO known_markets (symbol, base_currency, quote_currency) VALUES (?, ?, ?)", (sym, base, quote))
    conn.commit()
    conn.close()


def load_trade_history(limit=50):
    """Ritorna gli ultimi trade chiusi (piu' recenti prima)."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, entry_price, exit_price, pnl_eur, entry_fee, exit_fee,
                   fee_currency, quote, close_reason, opened_at, closed_at
            FROM trade_history ORDER BY id DESC LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        conn.close()
        out = []
        for r in rows:
            out.append({
                "symbol": r[0], "entry_price": r[1], "exit_price": r[2],
                "pnl_eur": r[3], "fees_eur": (r[4] or 0) + (r[5] or 0),
                "fee_currency": r[6], "quote": r[7], "close_reason": r[8],
                "opened_at": r[9], "closed_at": r[10],
            })
        return out
    except Exception:
        return []


def get_trade_stats():
    """Statistiche aggregate sui trade chiusi (per analytics professionale)."""
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*),
                   COALESCE(SUM(pnl_eur), 0),
                   COALESCE(SUM(CASE WHEN pnl_eur >= 0 THEN 1 ELSE 0 END), 0),
                   COALESCE(SUM(entry_fee + exit_fee), 0),
                   COALESCE(AVG(pnl_eur), 0),
                   COALESCE(MAX(pnl_eur), 0),
                   COALESCE(MIN(pnl_eur), 0)
            FROM trade_history
        """)
        total, pnl, wins, fees, avg, best, worst = cursor.fetchone()
        conn.close()
        losses = total - wins
        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round((wins / total * 100), 1) if total else 0,
            "realized_pnl_eur": round(pnl, 2),
            "total_fees_eur": round(fees, 2),
            "avg_pnl_eur": round(avg, 3),
            "best_eur": round(best, 2),
            "worst_eur": round(worst, 2),
        }
    except Exception:
        return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate_pct": 0,
                "realized_pnl_eur": 0, "total_fees_eur": 0, "avg_pnl_eur": 0,
                "best_eur": 0, "worst_eur": 0}


def load_circuit_breaker_log(limit=10):
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("SELECT triggered_at, reason, consecutive_losses FROM circuit_breaker_log ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [{"triggered_at": r[0], "reason": r[1], "consecutive_losses": r[2]} for r in rows]
    except Exception:
        return []


def set_meta(key, value):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO bot_meta (key, value) VALUES (?, ?)",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


def get_meta(key, default=None):
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM bot_meta WHERE key=?", (key,))
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return default
        return row[0]
    except Exception:
        return default
