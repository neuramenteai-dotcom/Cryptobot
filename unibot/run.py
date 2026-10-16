"""Comandi di Unibot (solo paper).

  python -m unibot.run --once                 un ciclo + report
  python -m unibot.run --loop [--interval 60] ciclo continuo
  python -m unibot.run --report               report dallo stato salvato
  python -m unibot.run --add-signal SOL --source nome [--note ..] [--side buy|avoid] [--ttl 24]
  python -m unibot.run --backtest [--start 2026-06-28] [--days 10] [--symbols 40]
  python -m unibot.run --signal-lab FILE      testa un file di segnali datati (JSON/CSV)
  python -m unibot.run --reset --yes          cancella lo stato locale
"""
import argparse
import datetime
import os
import sys
import time

from . import settings as S
from .report import build_report
from .store import Store


def _make_bot():
    import ccxt
    from .engine import Bot
    from .signals import Signals
    from .universe import CryptoUniverse, FmpClient, StockUniverse
    crypto = stocks = intel = None
    if S.ENABLE_CRYPTO:
        crypto = CryptoUniverse(ccxt.coinbase({"enableRateLimit": True}))  # dati pubblici: nessuna chiave
        try:
            from market_intel import MarketIntel
            intel = MarketIntel()
        except Exception as e:
            print("market intel non disponibile (%s): regime 'unknown'" % e)
    if S.ENABLE_STOCKS:
        stocks = StockUniverse(FmpClient())
        if not S.FMP_API_KEY:
            print("FMP_API_KEY mancante: le azioni non verranno valutate")
    return Bot(Store(), crypto=crypto, stocks=stocks, intel=intel, signals=Signals())


def _report_only():
    from .engine import Bot
    from .signals import Signals
    bot = Bot(Store(), signals=Signals(), log=lambda m: None)
    return build_report(bot.snapshot())


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="unibot", description="Bot unificato crypto + azioni USA (paper)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--add-signal", metavar="SIMBOLO")
    ap.add_argument("--source", default="manuale")
    ap.add_argument("--note", default="")
    ap.add_argument("--side", default="buy", choices=["buy", "avoid"])
    ap.add_argument("--ttl", type=float, default=None)
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--start", default=None)
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--symbols", type=int, default=40)
    ap.add_argument("--signal-lab", metavar="FILE")
    ap.add_argument("--insider-lab", action="store_true", help="testa gli acquisti degli insider (SEC Form 4)")
    ap.add_argument("--funding-lab", action="store_true", help="testa il funding crypto (perpetui Binance, dati pubblici)")
    ap.add_argument("--trend-lab", action="store_true", help="testa il seguimento del trend a bassa rotazione (usa i dati del funding lab)")
    ap.add_argument("--etf-lab", action="store_true", help="prestazioni di 9 ETF dal 2005 e regole semplici a bassa rotazione")
    ap.add_argument("--etf-forward", action="store_true", help="test in avanti (paper) della regola fissa trend SPY+QQQ")
    ap.add_argument("--quarters", default=None, help="es. 2025q1,2025q2 (default: 2025q1..2026q3)")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)

    if a.add_signal:
        from .signals import Signals
        item = Signals().add(a.add_signal, a.source, a.note, a.side, a.ttl)
        print("segnale registrato (non verificato, passa comunque da tutti i filtri):", item)
        return 0
    if a.reset:
        if not a.yes:
            print("aggiungi --yes per cancellare lo stato locale (%s)" % S.DB_PATH)
            return 1
        if os.path.exists(S.DB_PATH):
            os.remove(S.DB_PATH)
        print("stato locale cancellato")
        return 0
    if a.report:
        print(_report_only())
        return 0
    if a.backtest:
        import ccxt
        from . import backtest
        end = datetime.datetime.now(datetime.timezone.utc)
        if a.start:
            start = datetime.datetime.strptime(a.start, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
            end = min(end, start + datetime.timedelta(days=a.days))
        else:
            start = end - datetime.timedelta(days=a.days)
        s_ms, e_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        try:
            out = backtest.run(ccxt.coinbase({"enableRateLimit": True}), s_ms, e_ms, a.symbols)
        except Exception as e:  # rete assente anche dopo i tentativi
            print("backtest non eseguito: dati di mercato non raggiungibili (%s). Riprova tra poco." % type(e).__name__)
            return 2
        print(backtest.format_results(out, s_ms, e_ms))
        return 0
    if a.signal_lab:
        from . import signal_lab
        return signal_lab.main_cli(a.signal_lab)
    if a.insider_lab:
        from . import insider_lab
        return insider_lab.main_cli(a.quarters)
    if a.funding_lab:
        from . import funding_lab
        return funding_lab.main_cli(a.start)
    if a.trend_lab:
        from . import trend_lab
        return trend_lab.main_cli()
    if a.etf_forward:
        from . import etf_forward
        return etf_forward.main_cli()
    if a.etf_lab:
        from . import etf_lab
        return etf_lab.main_cli()
    if a.once or a.loop:
        bot = _make_bot()
        if a.once:
            bot.cycle()
            print(build_report(bot.snapshot()))
            return 0
        try:
            bot.run_forever(a.interval)
        except KeyboardInterrupt:
            bot.stop_event.set()
            print("\nfermato")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
