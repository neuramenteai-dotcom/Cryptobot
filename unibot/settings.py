"""Impostazioni di Unibot, tutte sovrascrivibili da variabili d'ambiente.

SICUREZZA: Unibot e' SOLO paper trading. Non esiste un percorso di codice che
invii ordini reali: il broker e' simulato. Per collegare un broker reale in
futuro serve scrivere un adattatore (vedi brokers.py) e una decisione esplicita.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # dotenv opzionale
    pass


def _f(name, default):
    return float(os.getenv(name, default))


def _i(name, default):
    return int(float(os.getenv(name, default)))


def _b(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _l(name, default):
    return [x.strip().upper() for x in os.getenv(name, default).split(",") if x.strip()]


PAPER_ONLY = True  # costante di progetto: nessun ordine reale

# --- percorsi ---
DB_PATH = os.getenv("UNIBOT_DB", "unibot_state.db")
SIGNALS_PATH = os.getenv("UNIBOT_SIGNALS", "unibot_signals.json")
CLOUD_MIRROR = _b("UNIBOT_CLOUD_MIRROR", False)  # copia i trade chiusi su Supabase

# --- classi di asset attive ---
ENABLE_CRYPTO = _b("UNIBOT_CRYPTO", True)
ENABLE_STOCKS = _b("UNIBOT_STOCKS", True)

# --- capitale paper di partenza ---
CRYPTO_START_EUR = _f("UNIBOT_CRYPTO_START_EUR", 50)
CRYPTO_START_USDC = _f("UNIBOT_CRYPTO_START_USDC", 100)
STOCK_START_USD = _f("UNIBOT_STOCK_START_USD", 150)

# --- rischio comune ---
POSITION_PCT = _f("POSITION_PCT", 0.05)          # size base, % dell'equity della valuta
MAX_POSITION_PCT = _f("MAX_POSITION_PCT", 0.15)  # tetto per singola posizione
MIN_ORDER = _f("MIN_ORDER", 5.0)
COOLDOWN_MINUTES = _i("COOLDOWN_MINUTES", 30)
CIRCUIT_LOSSES = _i("CIRCUIT_LOSSES", 3)
CIRCUIT_MINUTES = _i("CIRCUIT_MINUTES", 30)
DAILY_LOSS_LIMIT_PCT = _f("DAILY_LOSS_LIMIT_PCT", 0.03)
MIN_NET_PROFIT_PCT = _f("MIN_NET_PROFIT_PCT", 0.004)
ENTRIES_PER_CYCLE = _i("ENTRIES_PER_CYCLE", 1)

# --- crypto ---
CRYPTO_QUOTES = _l("CRYPTO_QUOTES", "EUR,USDC")
CRYPTO_EXCLUDED_BASES = _l("CRYPTO_EXCLUDED_BASES", "USDT,USDC,EURC,DAI,PYUSD,EUR,USD")
CRYPTO_MIN_PCT = _f("CRYPTO_MIN_PCT", 2.0)
CRYPTO_MAX_PCT = _f("CRYPTO_MAX_PCT", 25.0)             # oltre: pump gia' esaurito
CRYPTO_MIN_QUOTE_VOLUME = _f("CRYPTO_MIN_QUOTE_VOLUME", 50000)  # volume 24h in valuta quote
CRYPTO_MAX_EXTENSION = _f("CRYPTO_MAX_EXTENSION", 0.03)  # max distanza sopra la SMA10
CRYPTO_TOP_N = _i("CRYPTO_TOP_N", 15)
CRYPTO_TIMEFRAME = os.getenv("CRYPTO_TIMEFRAME", "5m")
CRYPTO_FEE_RATE = _f("CRYPTO_FEE_RATE", 0.005)           # per lato (taker): livello "Intro" Coinbase, letto dal conto il 2026-10-16
CRYPTO_SLIPPAGE_BPS = _f("CRYPTO_SLIPPAGE_BPS", 5)
CRYPTO_TRAIL_PCT = _f("CRYPTO_TRAIL_PCT", 0.015)
CRYPTO_MAX_HOLD_HOURS = _f("CRYPTO_MAX_HOLD_HOURS", 24)
CRYPTO_MAX_POSITIONS = _i("CRYPTO_MAX_POSITIONS", 5)
CRYPTO_CANDLE_TTL = _i("CRYPTO_CANDLE_TTL", 120)         # cache candele (anti rate-limit)
PANIC_BLOCK_BELOW = _i("PANIC_BLOCK_BELOW", 25)          # Fear&Greed <= soglia: nessun ingresso
REGIME_UNKNOWN_MULT = _f("REGIME_UNKNOWN_MULT", 0.5)     # size se il regime non e' noto (solo se non si blocca)
REGIME_UNKNOWN_BLOCK = _b("REGIME_UNKNOWN_BLOCK", True)  # senza dato sul regime: nessun ingresso (fail-closed)
REGIME_CACHE_HOURS = _f("REGIME_CACHE_HOURS", 36)        # ultimo Fear&Greed valido riusabile (e' un dato giornaliero)

# --- azioni USA (radar FMP) ---
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FMP_DAILY_BUDGET = _i("FMP_DAILY_BUDGET", 240)           # il piano gratuito ha ~250 chiamate/giorno
FMP_EXIT_RESERVE = _i("FMP_EXIT_RESERVE", 30)            # chiamate tenute per gestire le uscite
STOCK_EXCHANGES = _l("STOCK_EXCHANGES", "NASDAQ,NYSE,AMEX")
STOCK_MIN_PCT = _f("STOCK_MIN_PCT", 5.0)
STOCK_MAX_PCT = _f("STOCK_MAX_PCT", 40.0)
STOCK_MIN_PRICE = _f("STOCK_MIN_PRICE", 2.0)
STOCK_MIN_VOLUME = _f("STOCK_MIN_VOLUME", 500000)        # azioni scambiate (se il quote e' disponibile)
STOCK_TAPE_MIN_SAMPLES = _i("STOCK_TAPE_MIN_SAMPLES", 4)  # campioni prima di valutare l'ingresso
STOCK_SCAN_SECONDS = _i("STOCK_SCAN_SECONDS", 900)
STOCK_POS_CHECK_SECONDS = _i("STOCK_POS_CHECK_SECONDS", 300)
STOCK_TRAIL_PCT = _f("STOCK_TRAIL_PCT", 0.03)
STOCK_MAX_POSITIONS = _i("STOCK_MAX_POSITIONS", 3)
STOCK_ENTRY_AFTER_OPEN_MIN = _i("STOCK_ENTRY_AFTER_OPEN_MIN", 15)
STOCK_ENTRY_BEFORE_CLOSE_MIN = _i("STOCK_ENTRY_BEFORE_CLOSE_MIN", 60)
STOCK_FLATTEN_BEFORE_CLOSE_MIN = _i("STOCK_FLATTEN_BEFORE_CLOSE_MIN", 10)
STOCK_SPY_MIN_PCT = _f("STOCK_SPY_MIN_PCT", -1.0)        # SPY sotto questa soglia: niente ingressi
STOCK_SLIPPAGE_BPS = _f("STOCK_SLIPPAGE_BPS", 10)
STOCK_PENNY_PRICE = _f("STOCK_PENNY_PRICE", 5.0)
STOCK_PENNY_SLIPPAGE_BPS = _f("STOCK_PENNY_SLIPPAGE_BPS", 40)

# --- segnali esterni (non verificati) ---
SIGNAL_BONUS = _f("SIGNAL_BONUS", 0.5)
SIGNAL_TTL_HOURS = _f("SIGNAL_TTL_HOURS", 24)
