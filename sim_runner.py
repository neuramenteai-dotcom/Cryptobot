"""Runner di SIMULAZIONE per accumulare trade nel DB locale.
Forza TRADE_MODE=SIMULATION PRIMA di importare config: nessun ordine reale,
indipendentemente da cosa dice il .env. Usato per raccogliere un campione di
trade (50-100) e valutare la strategia.
"""
import os
os.environ["TRADE_MODE"] = "SIMULATION"   # hard-forzato: zero soldi reali
os.environ.setdefault("GEOPOLITICAL_RISK_ENABLED", "true")

import time
from config import TRADE_MODE

assert TRADE_MODE == "SIMULATION", f"ATTESO SIMULATION, trovato {TRADE_MODE} — STOP"
print("=== SIM RUNNER avviato in SIMULATION (nessun ordine reale) ===", flush=True)

from bot_engine import bot_instance
bot_instance.start()

try:
    while True:
        time.sleep(10)
except KeyboardInterrupt:
    bot_instance.stop()
    print("SIM RUNNER terminato.")
