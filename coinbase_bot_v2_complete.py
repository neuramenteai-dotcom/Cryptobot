# COINBASE TRADING BOT v2.0 - VERSIONE MIGLIORATA
# Autore: AI Assistant | Data: 2026-06-26

# =============================================================================
# FILE 1: config.py
# =============================================================================

import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()

@dataclass
class TradingConfig:
    TRADE_MODE: str = os.getenv("TRADE_MODE", "SIMULATION")
    BUDGET_EUR: float = float(os.getenv("BUDGET_EUR", "1000"))
    BASE_RISK_PER_TRADE: float = 0.02
    MAX_POSITIONS: int = 3
    MAX_DAILY_DRAWDOWN: float = 0.10
    ENTRY_TIMEFRAME: str = "5m"
    CONFIRM_TIMEFRAME: str = "15m"
    TREND_TIMEFRAME: str = "1h"
    BIAS_TIMEFRAME: str = "4h"
    EMA_FAST: int = 9
    EMA_SLOW: int = 21
    EMA_TREND: int = 50
    RSI_PERIOD: int = 14
    ATR_PERIOD: int = 14
    ADX_PERIOD: int = 14
    ADX_THRESHOLD: float = 25
    MACD_FAST: int = 12
    MACD_SLOW: int = 26
    MACD_SIGNAL: int = 9
    STOP_LOSS_ATR_MULTIPLIER: float = 2.0
    TRAILING_STOP_ATR_MULTIPLIER: float = 1.5
    TAKE_PROFIT_RATIO: float = 2.0
    MIN_DAILY_CHANGE: float = 0.03
    MIN_VOLUME_24H: float = 1000000
    MAX_SPREAD_PCT: float = 0.005
    BLOCKED_ASSETS: List[str] = field(default_factory=lambda: ["BTC", "ETH"])
    CB_MAX_CONSECUTIVE_LOSSES: int = 3
    CB_COOLDOWN_MINUTES: int = 30
    CB_DRAWDOWN_TRIGGER: float = 0.05

@dataclass
class CoinbaseConfig:
    API_KEY: str = os.getenv("COINBASE_API_KEY", "")
    API_SECRET: str = os.getenv("COINBASE_API_SECRET", "")
    PASSPHRASE: str = os.getenv("COINBASE_PASSPHRASE", "")
    SANDBOX: bool = os.getenv("COINBASE_SANDBOX", "true").lower() == "true"
    RATE_LIMIT_REQUESTS: int = 10000
    RATE_LIMIT_WINDOW: int = 3600

@dataclass
class TelegramConfig:
    ENABLED: bool = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
    BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

TRADING_CONFIG = TradingConfig()
COINBASE_CONFIG = CoinbaseConfig()
TELEGRAM_CONFIG = TelegramConfig()

# =============================================================================
# FILE 2: market/indicators.py
# =============================================================================

import numpy as np
import pandas as pd
from typing import Tuple

def calculate_ema(prices, period):
    return pd.Series(prices).ewm(span=period, adjust=False).mean().values

def calculate_sma(prices, period):
    return pd.Series(prices).rolling(window=period).mean().values

def calculate_rsi(prices, period=14):
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gains = pd.Series(gains).ewm(alpha=1/period, adjust=False).mean().values
    avg_losses = pd.Series(losses).ewm(alpha=1/period, adjust=False).mean().values
    rs = avg_gains / (avg_losses + 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return np.concatenate([[50], rsi])

def calculate_atr(high, low, close, period=14):
    tr1 = high[1:] - low[1:]
    tr2 = np.abs(high[1:] - close[:-1])
    tr3 = np.abs(low[1:] - close[:-1])
    tr = np.maximum(np.maximum(tr1, tr2), tr3)
    atr = pd.Series(tr).ewm(span=period, adjust=False).mean().values
    return np.concatenate([[tr[0]], atr])

def calculate_adx(high, low, close, period=14):
    tr1 = high[1:] - low[1:]
    tr2 = np.abs(high[1:] - close[:-1])
    tr3 = np.abs(low[1:] - close[:-1])
    tr = np.maximum(np.maximum(tr1, tr2), tr3)
    plus_dm = np.where((high[1:] - high[:-1]) > (low[:-1] - low[1:]),
                       np.maximum(high[1:] - high[:-1], 0), 0)
    minus_dm = np.where((low[:-1] - low[1:]) > (high[1:] - high[:-1]),
                        np.maximum(low[:-1] - low[1:], 0), 0)
    atr = pd.Series(tr).ewm(span=period, adjust=False).mean().values
    plus_di = 100 * pd.Series(plus_dm).ewm(span=period, adjust=False).mean().values / (atr + 1e-10)
    minus_di = 100 * pd.Series(minus_dm).ewm(span=period, adjust=False).mean().values / (atr + 1e-10)
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    adx = pd.Series(dx).ewm(span=period, adjust=False).mean().values
    return (np.concatenate([[20], adx]),
            np.concatenate([[0], plus_di]),
            np.concatenate([[0], minus_di]))

def calculate_macd(prices, fast=12, slow=26, signal=9):
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line

def calculate_obv(close, volume):
    obv = np.zeros_like(close)
    obv[0] = volume[0]
    for i in range(1, len(close)):
        if close[i] > close[i-1]:
            obv[i] = obv[i-1] + volume[i]
        elif close[i] < close[i-1]:
            obv[i] = obv[i-1] - volume[i]
        else:
            obv[i] = obv[i-1]
    return obv

def calculate_vwap(high, low, close, volume):
    tp = (high + low + close) / 3
    return np.cumsum(tp * volume) / (np.cumsum(volume) + 1e-10)

def detect_rsi_divergence(prices, rsi, lookback=5):
    if len(prices) < lookback * 2 or len(rsi) < lookback * 2:
        return 'none'
    rp = prices[-lookback:]
    rr = rsi[-lookback:]
    pp = prices[-lookback*2:-lookback]
    pr = rsi[-lookback*2:-lookback]
    if np.min(rp) < np.min(pp) and np.min(rr) > np.min(pr):
        return 'bullish'
    if np.max(rp) > np.max(pp) and np.max(rr) < np.max(pr):
        return 'bearish'
    return 'none'

# =============================================================================
# FILE 3: market/candlestick_patterns.py
# =============================================================================

import numpy as np

def analyze_candlestick_pattern(ohlcv):
    if len(ohlcv) < 2:
        return {"pattern": "none", "signal": "neutral"}

    curr = ohlcv[-1]
    prev = ohlcv[-2]
    o, h, l, c = curr[1], curr[2], curr[3], curr[4]
    po, ph, pl, pc = prev[1], prev[2], prev[3], prev[4]

    patterns = []
    signal = "neutral"

    # Bullish Engulfing
    if pc < po and c > o and o <= pc and c >= po:
        patterns.append("bullish_engulfing")
        signal = "bullish"

    # Hammer
    body = abs(c - o)
    lower_shadow = min(o, c) - l
    upper_shadow = h - max(o, c)
    if lower_shadow > 2 * body and upper_shadow < body and body > 0:
        patterns.append("hammer")
        signal = "bullish"

    # Shooting Star
    if upper_shadow > 2 * body and lower_shadow < body and body > 0:
        patterns.append("shooting_star")
        signal = "bearish"

    # Doji
    if body < (h - l) * 0.1 and (h - l) > 0:
        patterns.append("doji")

    return {"pattern": patterns[0] if patterns else "none", "signal": signal, "all_patterns": patterns}

# =============================================================================
# FILE 4: core/risk_manager.py
# =============================================================================

import numpy as np
from dataclasses import dataclass
from typing import List, Dict
from datetime import datetime, timedelta

@dataclass
class TradeRecord:
    symbol: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl_pct: float
    pnl_eur: float
    timestamp: datetime
    exit_reason: str

class RiskManager:
    def __init__(self, config):
        self.config = config
        self.trade_history = []
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self.daily_start_balance = 0.0
        self.last_reset = datetime.now()
        self.circuit_breaker_active = False
        self.circuit_breaker_until = None

    def reset_daily_stats(self):
        self.daily_pnl = 0.0
        self.last_reset = datetime.now()
        self.consecutive_losses = 0

    def check_circuit_breaker(self):
        if self.circuit_breaker_active:
            if datetime.now() >= self.circuit_breaker_until:
                self.circuit_breaker_active = False
                return False
            return True
        return False

    def update_after_trade(self, trade, current_balance):
        self.trade_history.append(trade)
        if trade.pnl_pct < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        self.daily_pnl += trade.pnl_eur
        if self.consecutive_losses >= self.config.CB_MAX_CONSECUTIVE_LOSSES:
            self._activate_circuit_breaker()
        if self.daily_start_balance > 0:
            drawdown = abs(self.daily_pnl) / self.daily_start_balance
            if drawdown >= self.config.MAX_DAILY_DRAWDOWN:
                self._activate_circuit_breaker()

    def _activate_circuit_breaker(self):
        self.circuit_breaker_active = True
        self.circuit_breaker_until = datetime.now() + timedelta(minutes=self.config.CB_COOLDOWN_MINUTES)

    def calculate_position_size(self, capital, entry_price, stop_loss_price, atr, volatility_regime='normal'):
        risk_amount = capital * self.config.BASE_RISK_PER_TRADE
        if volatility_regime == 'high':
            risk_amount *= 0.5
        elif volatility_regime == 'low':
            risk_amount *= 1.2
        price_risk = abs(entry_price - stop_loss_price)
        if price_risk <= 0:
            return 0.0
        quantity = risk_amount / price_risk
        max_quantity = (capital * 0.15) / entry_price
        return min(quantity, max_quantity)

    def calculate_dynamic_stop_loss(self, entry_price, atr, direction='long'):
        stop_distance = atr * self.config.STOP_LOSS_ATR_MULTIPLIER
        return entry_price - stop_distance if direction == 'long' else entry_price + stop_distance

    def calculate_take_profit(self, entry_price, stop_loss_price, direction='long'):
        risk = abs(entry_price - stop_loss_price)
        reward = risk * self.config.TAKE_PROFIT_RATIO
        return entry_price + reward if direction == 'long' else entry_price - reward

    def calculate_trailing_stop(self, highest_price, atr, direction='long'):
        trail_distance = atr * self.config.TRAILING_STOP_ATR_MULTIPLIER
        return highest_price - trail_distance if direction == 'long' else highest_price + trail_distance

    def get_volatility_regime(self, atr, current_price):
        atr_pct = (atr / current_price) * 100
        if atr_pct < 1.0:
            return 'low'
        elif atr_pct > 3.0:
            return 'high'
        return 'normal'

    def get_performance_metrics(self):
        if not self.trade_history:
            return {}
        wins = [t for t in self.trade_history if t.pnl_pct > 0]
        losses = [t for t in self.trade_history if t.pnl_pct <= 0]
        total = len(self.trade_history)
        win_rate = len(wins) / total if total > 0 else 0
        avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0
        avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0
        pf = (sum(t.pnl_eur for t in wins) / abs(sum(t.pnl_eur for t in losses))) if losses else float('inf')
        return {
            'total_trades': total,
            'win_rate': win_rate,
            'avg_win_pct': avg_win,
            'avg_loss_pct': avg_loss,
            'profit_factor': pf,
            'total_pnl_eur': sum(t.pnl_eur for t in self.trade_history),
            'consecutive_losses': self.consecutive_losses,
            'circuit_breaker_active': self.circuit_breaker_active
        }

# =============================================================================
# FILE 5: core/strategy_engine.py
# =============================================================================

import numpy as np
from typing import Dict, List
from dataclasses import dataclass
from market.indicators import *
from market.candlestick_patterns import analyze_candlestick_pattern

@dataclass
class Signal:
    symbol: str
    direction: str
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    atr: float
    reasons: List[str]
    timestamp: str

class StrategyEngine:
    def __init__(self, config):
        self.config = config

    def analyze(self, ohlcv_5m, ohlcv_15m, ohlcv_1h, ohlcv_4h):
        close_5m = ohlcv_5m[:, 4]
        high_5m = ohlcv_5m[:, 2]
        low_5m = ohlcv_5m[:, 3]
        volume_5m = ohlcv_5m[:, 5]
        close_1h = ohlcv_1h[:, 4]
        high_1h = ohlcv_1h[:, 2]
        low_1h = ohlcv_1h[:, 3]
        volume_1h = ohlcv_1h[:, 5]
        close_4h = ohlcv_4h[:, 4]

        reasons = []
        score = 0.0
        max_score = 0.0

        # === 1. FILTRO TREND (4H) ===
        ema_50_4h = calculate_ema(close_4h, self.config.EMA_TREND)
        price_above_ema50 = close_4h[-1] > ema_50_4h[-1]
        if price_above_ema50:
            score += 1.0
            reasons.append("Trend rialzista su 4H (EMA50)")
        else:
            reasons.append("Trend non confermato su 4H")
        max_score += 1.0

        # === 2. MOMENTUM (1H) ===
        rsi_1h = calculate_rsi(close_1h, self.config.RSI_PERIOD)
        macd_line, signal_line, histogram = calculate_macd(
            close_1h, self.config.MACD_FAST, self.config.MACD_SLOW, self.config.MACD_SIGNAL
        )
        rsi_current = rsi_1h[-1]
        if 50 < rsi_current < 70:
            score += 1.0
            reasons.append(f"RSI(14) 1H = {rsi_current:.1f} - Momentum positivo")
        elif rsi_current >= 70:
            reasons.append(f"RSI = {rsi_current:.1f} - Possibile ipercomprato")
        else:
            reasons.append(f"RSI = {rsi_current:.1f} - Momentum debole")
        max_score += 1.0

        macd_bullish = macd_line[-1] > signal_line[-1] and histogram[-1] > 0
        if macd_bullish:
            score += 1.0
            reasons.append("MACD 1H bullish")
        else:
            reasons.append("MACD 1H non bullish")
        max_score += 1.0

        divergence = detect_rsi_divergence(close_1h, rsi_1h)
        if divergence == 'bullish':
            score += 0.5
            reasons.append("Divergenza RSI rialzista")
        max_score += 0.5

        # === 3. ENTRY TIMING (5m) ===
        ema_9_5m = calculate_ema(close_5m, self.config.EMA_FAST)
        ema_21_5m = calculate_ema(close_5m, self.config.EMA_SLOW)
        ema_crossover = ema_9_5m[-1] > ema_21_5m[-1] and ema_9_5m[-2] <= ema_21_5m[-2]
        ema_aligned = ema_9_5m[-1] > ema_21_5m[-1]

        if ema_crossover:
            score += 1.5
            reasons.append("EMA(9/21) 5m Crossover rialzista")
        elif ema_aligned:
            score += 0.5
            reasons.append("EMA(9) sopra EMA(21) 5m")
        else:
            reasons.append("EMA non allineato su 5m")
        max_score += 1.5

        candle_analysis = analyze_candlestick_pattern(ohlcv_5m)
        if candle_analysis["signal"] == "bullish":
            score += 0.5
            reasons.append(f"Pattern candela: {candle_analysis['pattern']}")
        max_score += 0.5

        # === 4. VOLUME CONFIRMATION ===
        obv_5m = calculate_obv(close_5m, volume_5m)
        obv_trend = obv_5m[-1] > obv_5m[-5]
        avg_volume = np.mean(volume_5m[-20:])
        volume_spike = volume_5m[-1] > avg_volume * 1.5

        if obv_trend and volume_spike:
            score += 1.5
            reasons.append("OBV crescente + Volume spike 1.5x")
        elif obv_trend:
            score += 0.5
            reasons.append("OBV crescente")
        else:
            reasons.append("Volume debole")
        max_score += 1.5

        # === 5. VOLATILITA (ATR) ===
        atr_5m = calculate_atr(high_5m, low_5m, close_5m, self.config.ATR_PERIOD)
        current_atr = atr_5m[-1]
        adx_1h, plus_di, minus_di = calculate_adx(high_1h, low_1h, close_1h, self.config.ADX_PERIOD)
        strong_trend = adx_1h[-1] > self.config.ADX_THRESHOLD

        if strong_trend:
            score += 1.0
            reasons.append(f"ADX = {adx_1h[-1]:.1f} - Trend forte")
        else:
            reasons.append(f"ADX = {adx_1h[-1]:.1f} - Trend debole")
        max_score += 1.0

        confidence = score / max_score if max_score > 0 else 0

        if confidence >= 0.7 and price_above_ema50 and ema_aligned:
            entry = close_5m[-1]
            stop = entry - (current_atr * self.config.STOP_LOSS_ATR_MULTIPLIER)
            tp = entry + (current_atr * self.config.STOP_LOSS_ATR_MULTIPLIER * self.config.TAKE_PROFIT_RATIO)
            return Signal("", 'long', confidence, entry, stop, tp, current_atr, reasons, "")

        return Signal("", 'neutral', confidence, close_5m[-1], 0, 0, current_atr, reasons, "")

# =============================================================================
# FILE 6: core/bot_engine.py
# =============================================================================

import time
import logging
from datetime import datetime
from typing import Dict, List
from config import TRADING_CONFIG
from core.risk_manager import RiskManager, TradeRecord
from core.strategy_engine import StrategyEngine, Signal

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TradingBot:
    def __init__(self):
        self.config = TRADING_CONFIG
        self.risk_manager = RiskManager(self.config)
        self.strategy = StrategyEngine(self.config)
        self.positions = {}
        self.running = False

    def loop(self):
        self.running = True
        logger.info("Bot avviato in modalita %s", self.config.TRADE_MODE)
        while self.running:
            try:
                if self.risk_manager.check_circuit_breaker():
                    logger.warning("Circuit breaker attivo. Attesa...")
                    time.sleep(60)
                    continue
                if datetime.now().hour == 0 and datetime.now().minute < 5:
                    self.risk_manager.reset_daily_stats()
                candidates = self._scan_market()
                self._manage_positions()
                if len(self.positions) < self.config.MAX_POSITIONS:
                    for symbol in candidates:
                        if symbol in self.positions:
                            continue
                        signal = self._analyze_symbol(symbol)
                        if signal.direction == 'long' and signal.confidence >= 0.7:
                            self._execute_entry(signal)
                metrics = self.risk_manager.get_performance_metrics()
                if metrics:
                    logger.info("WR %.1f%% | PF %.2f | PnL EUR %.2f",
                              metrics['win_rate']*100, metrics['profit_factor'], metrics['total_pnl_eur'])
                time.sleep(60)
            except Exception as e:
                logger.error("Errore: %s", str(e))
                time.sleep(30)

    def _scan_market(self):
        return []

    def _analyze_symbol(self, symbol):
        pass

    def _execute_entry(self, signal):
        pass

    def _manage_positions(self):
        for symbol, position in list(self.positions.items()):
            current_price = self._get_current_price(symbol)
            if current_price > position['highest_price']:
                position['highest_price'] = current_price
                new_stop = self.risk_manager.calculate_trailing_stop(position['highest_price'], position['atr'])
                position['stop_loss'] = max(position['stop_loss'], new_stop)
            if current_price <= position['stop_loss']:
                self._execute_exit(symbol, current_price, 'stop_loss')
                continue
            if current_price >= position['take_profit']:
                self._execute_exit(symbol, current_price, 'take_profit')
                continue

    def _execute_exit(self, symbol, price, reason):
        position = self.positions[symbol]
        pnl_pct = (price - position['entry_price']) / position['entry_price']
        pnl_eur = pnl_pct * position['invested_eur']
        trade = TradeRecord(symbol, position['entry_price'], price, position['quantity'],
                           pnl_pct, pnl_eur, datetime.now(), reason)
        self.risk_manager.update_after_trade(trade, self._get_balance())
        del self.positions[symbol]
        logger.info("CHIUSURA %s @ %.4f | PnL: %.2f%% (EUR %.2f) | %s",
                   symbol, price, pnl_pct*100, pnl_eur, reason)

    def _get_current_price(self, symbol):
        pass

    def _get_balance(self):
        pass

    def stop(self):
        self.running = False
        logger.info("Bot fermato")

# =============================================================================
# FILE 7: notifications/telegram_bot.py
# =============================================================================

import requests
import logging
from config import TELEGRAM_CONFIG

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self):
        self.enabled = TELEGRAM_CONFIG.ENABLED
        self.token = TELEGRAM_CONFIG.BOT_TOKEN
        self.chat_id = TELEGRAM_CONFIG.CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}"

    def send_message(self, message):
        if not self.enabled:
            return
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {"chat_id": self.chat_id, "text": message, "parse_mode": "Markdown"}
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.error("Errore Telegram: %s", response.text)
        except Exception as e:
            logger.error("Errore Telegram: %s", str(e))

    def notify_trade_entry(self, symbol, price, quantity, stop, take_profit, confidence):
        msg = f"NUOVO TRADE\nSimbolo: {symbol}\nPrezzo: EUR {price:.4f}\nQuantita: {quantity:.6f}\nStop: EUR {stop:.4f}\nTP: EUR {take_profit:.4f}\nConfidenza: {confidence*100:.1f}%"
        self.send_message(msg)

    def notify_trade_exit(self, symbol, exit_price, pnl_pct, pnl_eur, reason):
        emoji = "PROFITTO" if pnl_pct > 0 else "PERDITA"
        msg = f"TRADE CHIUSO - {emoji}\nSimbolo: {symbol}\nUscita: EUR {exit_price:.4f}\nPnL: {pnl_pct*100:.2f}% (EUR {pnl_eur:.2f})\nMotivo: {reason}"
        self.send_message(msg)

    def notify_circuit_breaker(self, consecutive_losses, cooldown_until):
        msg = f"CIRCUIT BREAKER ATTIVATO\nPerdite consecutive: {consecutive_losses}\nRipresa: {cooldown_until}"
        self.send_message(msg)

    def notify_daily_report(self, metrics):
        msg = f"REPORT GIORNALIERO\nTrades: {metrics.get('total_trades', 0)}\nWR: {metrics.get('win_rate', 0)*100:.1f}%\nPF: {metrics.get('profit_factor', 0):.2f}\nPnL: EUR {metrics.get('total_pnl_eur', 0):.2f}"
        self.send_message(msg)

# =============================================================================
# FILE 8: data/database.py
# =============================================================================

import sqlite3
import json
from datetime import datetime
from typing import List, Dict

class TradeDatabase:
    def __init__(self, db_path="trades.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL, entry_price REAL NOT NULL,
                    exit_price REAL, quantity REAL NOT NULL,
                    pnl_pct REAL, pnl_eur REAL,
                    entry_time TIMESTAMP NOT NULL, exit_time TIMESTAMP,
                    exit_reason TEXT, confidence REAL,
                    strategy_signals TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY, trades_count INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
                    total_pnl_eur REAL DEFAULT 0, max_drawdown REAL DEFAULT 0
                )
            """)

    def record_trade_entry(self, symbol, entry_price, quantity, confidence, signals):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO trades (symbol, entry_price, quantity, entry_time, confidence, strategy_signals)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (symbol, entry_price, quantity, datetime.now(), confidence, json.dumps(signals)))

    def record_trade_exit(self, trade_id, exit_price, pnl_pct, pnl_eur, exit_reason):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE trades SET exit_price=?, pnl_pct=?, pnl_eur=?, exit_time=?, exit_reason=?
                WHERE id=?
            """, (exit_price, pnl_pct, pnl_eur, datetime.now(), exit_reason, trade_id))

    def get_trade_history(self, limit=100):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM trades ORDER BY entry_time DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_performance_summary(self, days=30):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(f"""
                SELECT COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl_eur > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl_eur <= 0 THEN 1 ELSE 0 END) as losses,
                    SUM(pnl_eur) as total_pnl, AVG(pnl_pct) as avg_pnl_pct,
                    MAX(pnl_eur) as best_trade, MIN(pnl_eur) as worst_trade
                FROM trades WHERE entry_time >= date('now', '-{days} days')
            """)
            row = cursor.fetchone()
            return {
                'total_trades': row[0] or 0, 'wins': row[1] or 0, 'losses': row[2] or 0,
                'total_pnl_eur': row[3] or 0, 'avg_pnl_pct': row[4] or 0,
                'best_trade': row[5] or 0, 'worst_trade': row[6] or 0
            }

# =============================================================================
# FILE 9: execution/coinbase_client.py
# =============================================================================

import ccxt
import time
import logging
from typing import Dict, List
from config import COINBASE_CONFIG

logger = logging.getLogger(__name__)

class CoinbaseClient:
    def __init__(self):
        self.config = COINBASE_CONFIG
        self.exchange = ccxt.coinbase({
            'apiKey': self.config.API_KEY, 'secret': self.config.API_SECRET,
            'password': self.config.PASSPHRASE, 'sandbox': self.config.SANDBOX,
            'enableRateLimit': True, 'options': {'defaultType': 'spot'}
        })
        self.request_count = 0
        self.window_start = time.time()

    def _check_rate_limit(self):
        now = time.time()
        if now - self.window_start > self.config.RATE_LIMIT_WINDOW:
            self.request_count = 0
            self.window_start = now
        if self.request_count >= self.config.RATE_LIMIT_REQUESTS:
            sleep_time = self.window_start + self.config.RATE_LIMIT_WINDOW - now
            if sleep_time > 0:
                logger.warning("Rate limit. Attesa %.0f secondi", sleep_time)
                time.sleep(sleep_time)
                self.request_count = 0
                self.window_start = time.time()
        self.request_count += 1

    def _retry_request(self, func, max_retries=3, backoff=2):
        for attempt in range(max_retries):
            try:
                self._check_rate_limit()
                return func()
            except ccxt.NetworkError as e:
                if attempt == max_retries - 1:
                    raise
                wait = backoff ** attempt
                logger.warning("Network error, retry in %ds: %s", wait, str(e))
                time.sleep(wait)
            except ccxt.ExchangeError as e:
                if "rate_limit" in str(e).lower():
                    time.sleep(60)
                    continue
                raise

    def fetch_ohlcv(self, symbol, timeframe='5m', limit=100):
        return self._retry_request(lambda: self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit))

    def fetch_ticker(self, symbol):
        return self._retry_request(lambda: self.exchange.fetch_ticker(symbol))

    def fetch_balance(self):
        return self._retry_request(lambda: self.exchange.fetch_balance())

    def create_market_buy_order(self, symbol, amount):
        return self._retry_request(lambda: self.exchange.create_market_buy_order(symbol, amount))

    def create_market_sell_order(self, symbol, amount):
        return self._retry_request(lambda: self.exchange.create_market_sell_order(symbol, amount))

    def fetch_tickers(self):
        return self._retry_request(lambda: self.exchange.fetch_tickers())

# =============================================================================
# FILE 10: requirements.txt
# =============================================================================

ccxt>=4.2.0
pandas>=2.0.0
numpy>=1.24.0
python-dotenv>=1.0.0
Flask>=3.0.0
gunicorn>=21.0.0
requests>=2.31.0
pytest>=7.4.0
