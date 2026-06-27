import sqlite3
import os

DB_NAME = "bot_state.db"


def _connect():
    return sqlite3.connect(DB_NAME, timeout=10)


def init_db():
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS open_trades (
            symbol TEXT PRIMARY KEY,
            entry_price REAL,
            current_price REAL,
            highest_price REAL,
            amount_base REAL,
            amount_eur REAL,
            entry_fee_eur REAL DEFAULT 0,
            adopted INTEGER DEFAULT 0,
            time TEXT
        )
    """)
    # Tabella stato persistente (circuit breaker, statistiche, fee totali)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()

    # Migrazione: aggiunge colonne mancanti su DB preesistenti
    for col, decl in (("entry_fee_eur", "REAL DEFAULT 0"),
                      ("adopted", "INTEGER DEFAULT 0")):
        try:
            cursor.execute(f"ALTER TABLE open_trades ADD COLUMN {col} {decl}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # colonna gia' presente

    conn.close()


def save_trade(symbol, trade_data):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO open_trades
        (symbol, entry_price, current_price, highest_price, amount_base,
         amount_eur, entry_fee_eur, adopted, time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        symbol,
        trade_data['entry_price'],
        trade_data.get('current_price', trade_data['entry_price']),
        trade_data.get('highest_price', trade_data['entry_price']),
        trade_data['amount_base'],
        trade_data['amount_eur'],
        trade_data.get('entry_fee_eur', 0.0),
        1 if trade_data.get('adopted') else 0,
        trade_data['time'],
    ))
    conn.commit()
    conn.close()


def load_trades():
    if not os.path.exists(DB_NAME):
        init_db()
        return {}

    init_db()  # garantisce schema/colonne aggiornate
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol, entry_price, current_price, highest_price, amount_base,
               amount_eur, entry_fee_eur, adopted, time
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
            "pnl_pct": 0.0,
            "time": r[8],
        }
    return trades


def remove_trade(symbol):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM open_trades WHERE symbol=?", (symbol,))
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------
# Stato persistente (chiave/valore) — sopravvive ai riavvii del worker
# ----------------------------------------------------------------------
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
