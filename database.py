import sqlite3
import os

DB_NAME = "bot_state.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS open_trades (
            symbol TEXT PRIMARY KEY,
            entry_price REAL,
            current_price REAL,
            highest_price REAL,
            amount_base REAL,
            amount_eur REAL,
            time TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_trade(symbol, trade_data):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO open_trades 
        (symbol, entry_price, current_price, highest_price, amount_base, amount_eur, time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        symbol,
        trade_data['entry_price'],
        trade_data.get('current_price', trade_data['entry_price']),
        trade_data.get('highest_price', trade_data['entry_price']),
        trade_data['amount_base'],
        trade_data['amount_eur'],
        trade_data['time']
    ))
    conn.commit()
    conn.close()

def load_trades():
    if not os.path.exists(DB_NAME):
        init_db()
        return {}
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, entry_price, current_price, highest_price, amount_base, amount_eur, time FROM open_trades")
    rows = cursor.fetchall()
    
    trades = {}
    for r in rows:
        trades[r[0]] = {
            "entry_price": r[1],
            "current_price": r[2],
            "highest_price": r[3],
            "amount_base": r[4],
            "amount_eur": r[5],
            "time": r[6]
        }
    conn.close()
    return trades

def remove_trade(symbol):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM open_trades WHERE symbol=?", (symbol,))
    conn.commit()
    conn.close()
