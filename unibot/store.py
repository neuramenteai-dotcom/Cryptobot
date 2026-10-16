"""Persistenza SQLite di Unibot: posizioni aperte, trade chiusi, stato (meta)."""
import json
import sqlite3
from contextlib import closing

from . import settings as S
from .models import Position

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (symbol TEXT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT, asset_class TEXT, quote TEXT,
    entry_price REAL, exit_price REAL, qty REAL, cost REAL, proceeds REAL,
    pnl_quote REAL, pnl_eur REAL, fees_eur REAL,
    reason TEXT, score REAL, regime TEXT, signal TEXT,
    opened_ts REAL, closed_ts REAL
);
"""

_TRADE_COLS = ("symbol", "asset_class", "quote", "entry_price", "exit_price", "qty", "cost",
               "proceeds", "pnl_quote", "pnl_eur", "fees_eur", "reason", "score", "regime",
               "signal", "opened_ts", "closed_ts")


class Store:
    def __init__(self, path=None):
        self.path = path or S.DB_PATH
        with closing(self._conn()) as c:
            c.executescript(_SCHEMA)
            c.commit()

    def _conn(self):
        return sqlite3.connect(self.path, timeout=10)

    # --- posizioni ---
    def save_position(self, pos):
        with closing(self._conn()) as c:
            c.execute("INSERT OR REPLACE INTO positions (symbol, data) VALUES (?, ?)",
                      (pos.symbol, json.dumps(pos.to_dict())))
            c.commit()

    def delete_position(self, symbol):
        with closing(self._conn()) as c:
            c.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
            c.commit()

    def load_positions(self):
        with closing(self._conn()) as c:
            rows = c.execute("SELECT data FROM positions").fetchall()
        return [Position.from_dict(json.loads(r[0])) for r in rows]

    # --- trade chiusi ---
    def add_trade(self, t):
        vals = [t.get(k) for k in _TRADE_COLS]
        with closing(self._conn()) as c:
            c.execute("INSERT INTO trades (%s) VALUES (%s)" % (",".join(_TRADE_COLS),
                                                                 ",".join("?" * len(_TRADE_COLS))), vals)
            c.commit()

    def recent_trades(self, limit=20):
        with closing(self._conn()) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self):
        out = {"total": 0, "wins": 0, "net_eur": 0.0, "fees_eur": 0.0, "gross_eur": 0.0,
               "by_class": {}, "by_reason": {}}
        with closing(self._conn()) as c:
            row = c.execute("SELECT COUNT(*), COALESCE(SUM(pnl_eur>=0),0), COALESCE(SUM(pnl_eur),0), "
                            "COALESCE(SUM(fees_eur),0) FROM trades").fetchone()
            out["total"], out["wins"], out["net_eur"], out["fees_eur"] = row[0], int(row[1]), row[2], row[3]
            out["gross_eur"] = out["net_eur"] + out["fees_eur"]
            for cls, n, w, net, fees in c.execute(
                    "SELECT asset_class, COUNT(*), SUM(pnl_eur>=0), SUM(pnl_eur), SUM(fees_eur) "
                    "FROM trades GROUP BY asset_class"):
                out["by_class"][cls] = {"n": n, "wins": int(w or 0), "net_eur": net or 0.0, "fees_eur": fees or 0.0}
            for reason, n, net in c.execute(
                    "SELECT reason, COUNT(*), SUM(pnl_eur) FROM trades GROUP BY reason ORDER BY COUNT(*) DESC"):
                out["by_reason"][reason] = {"n": n, "net_eur": net or 0.0}
        return out

    # --- meta ---
    def set_meta(self, key, value):
        with closing(self._conn()) as c:
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))
            c.commit()

    def get_meta(self, key, default=None):
        with closing(self._conn()) as c:
            row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except ValueError:
            return default
