import os
from dotenv import load_dotenv

load_dotenv()


def _flag(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# Coinbase API (Advanced Trade)
COINBASE_API_KEY = os.getenv("COINBASE_API_KEY", "")
COINBASE_API_SECRET = os.getenv("COINBASE_API_SECRET", "")

# Financial Modeling Prep API
FMP_API_KEY = os.getenv("FMP_API_KEY", "")

# Trading Settings
TRADE_MODE = os.getenv("TRADE_MODE", "LIVE").strip().upper()
BUDGET = float(os.getenv("BUDGET", "150.0"))

# --- Commissioni ---
# Fee taker di partenza (per lato). Il bot AUTO-CALIBRA questo valore dalle fee
# reali dei fill (vedi auto-calibration nell'engine): se Coinbase One azzera le
# commissioni su Advanced Trade, la fee misurata scende e il breakeven con essa.
FEE_RATE = float(os.getenv("FEE_RATE", "0.006"))
ROUND_TRIP_FEE = FEE_RATE * 2
MIN_NET_PROFIT_PCT = float(os.getenv("MIN_NET_PROFIT_PCT", "0.004"))
BREAKEVEN_PCT = ROUND_TRIP_FEE + MIN_NET_PROFIT_PCT  # fallback statico iniziale

# --- Coinbase One ---
# Abbonamento (€5/mese) e franchigia mensile di trading senza commissioni.
COINBASE_ONE = _flag("COINBASE_ONE", True)
COINBASE_ONE_MONTHLY_COST = float(os.getenv("COINBASE_ONE_MONTHLY_COST", "5.0"))
FREE_FEE_ALLOWANCE = float(os.getenv("FREE_FEE_ALLOWANCE", "500.0"))  # EUR/mese a fee 0
# Fine della prova gratuita (da schermata: 4 lug 2026). Dopo questa data parte
# l'addebito di €5/mese: la dashboard mostra il countdown e un verdetto.
COINBASE_ONE_TRIAL_END = os.getenv("COINBASE_ONE_TRIAL_END", "2026-07-01")

# Scalping Parameters
TIMEFRAME = "5m"
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.015"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.02"))
MIN_GAINER_PCT = float(os.getenv("MIN_GAINER_PCT", "2.0"))

# --- Universo mercati (multi-quote) ---
# Non solo EUR: USDC su Coinbase ha ~556 mercati vs 35 di EUR.
ENABLED_QUOTES = [q.strip().upper() for q in os.getenv("ENABLED_QUOTES", "EUR,USDC").split(",") if q.strip()]
# Stablecoin trattate come ~1 EUR (USDC/USDT/USD). Conversione raffinata a runtime.
STABLE_QUOTES = {"USDC", "USDT", "USD", "DAI"}

# --- Pulizia dust (vende posizioni residue piccole per liberare liquidita') ---
DUST_CLEANUP_ENABLED = _flag("DUST_CLEANUP_ENABLED", True)
DUST_MAX_EUR = float(os.getenv("DUST_MAX_EUR", "5.0"))        # considera dust sotto questo valore
DUST_MIN_SELLABLE_EUR = float(os.getenv("DUST_MIN_SELLABLE_EUR", "1.0"))  # sotto e' invendibile (min Coinbase)

# --- Rilevamento nuove listing (per il "balzo iniziale") ---
NEW_LISTING_ENABLED = _flag("NEW_LISTING_ENABLED", True)
NEW_LISTING_TRADE_EUR = float(os.getenv("NEW_LISTING_TRADE_EUR", "6.0"))  # size fissa piccola, alto rischio
NEW_LISTING_MAX_AGE_CYCLES = int(os.getenv("NEW_LISTING_MAX_AGE_CYCLES", "60"))  # quanti cicli resta "nuova"

# --- FMP radar (momentum/news) ---
FMP_ENABLED = _flag("FMP_ENABLED", True)
# Le news vere richiedono un piano FMP a pagamento (free = 402). Se attivo, il
# bot prova l'endpoint news; altrimenti usa solo momentum/quote (gratis).
FMP_NEWS_ENABLED = _flag("FMP_NEWS_ENABLED", False)

# Asset da non liquidare/vendere mai (HODL)
BLOCKED_ASSETS = [a.strip().upper() for a in os.getenv(
    "BLOCKED_ASSETS", "BTC,AIOZ,EUR,USDC,USDT,EURC").split(",") if a.strip()]
