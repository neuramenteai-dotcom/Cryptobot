"""Test di Unibot senza rete: python -m unibot.test_unibot"""
import datetime
import math
import json
import os
import shutil
import sys
import tempfile
import types

from . import settings as S
from .engine import Bot
from .models import Candidate, Position
from .paper import PaperBroker
from .signals import Signals
from .store import Store
from .strategy import crypto_entry, stock_entry, exit_reason
from .universe import FmpClient, StockUniverse, market_state, ETF_RE

FAILS = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", name, detail if not cond else "")
    if not cond:
        FAILS.append(name)


def make_cfg(**over):
    d = {k: getattr(S, k) for k in dir(S) if k.isupper()}
    d.update(over)
    return types.SimpleNamespace(**d)


def ny(y, m, d, hh, mm):
    from zoneinfo import ZoneInfo
    return datetime.datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("America/New_York")).timestamp()


GOOD_CLOSES = [100 + 0.05 * i + 0.5 * math.sin(i / 2.0 + 7 * 0.5) for i in range(100)]
REGIME_OK = {"block": False, "mult": 1.0, "label": "ottimismo", "fg": 60}


class FakeCrypto:
    def __init__(self):
        self.cands, self.px, self.cl, self.tickers = [], {}, {}, {}

    def scan(self):
        return list(self.cands)

    def price(self, s):
        return self.px.get(s)

    def closes(self, s):
        return self.cl.get(s)

    def usdc_eur(self):
        return 0.9


class FakeStocks:
    def __init__(self):
        self.cands, self.px, self.tapes, self.prices, self.fmp = [], {}, {}, {}, None
        self.ctx = {"open": True, "day": "2026-10-16", "can_enter": True, "flatten": False,
                    "reasons": [], "stale": False, "spy_pct": 0.1}

    def scan(self):
        return list(self.cands)

    def context(self, now):
        return dict(self.ctx)

    def price(self, s):
        return self.px.get(s)

    def tape_prices(self, s):
        return list(self.tapes.get(s, []))


class FakeIntel:
    def __init__(self, fg=60, available=True):
        self.fg, self.available = fg, available

    def get_regime(self):
        if not self.available:
            return {"available": False}
        return {"available": True, "fear_greed": self.fg, "risk_multiplier": 1.0, "regime": "test"}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def new_bot(tmp, crypto=None, stocks=None, intel=None, signals=None, clock=None, **cfg_over):
    cfg = make_cfg(ENABLE_CRYPTO=crypto is not None, ENABLE_STOCKS=stocks is not None, **cfg_over)
    bot = Bot(Store(os.path.join(tmp, "t.db")), crypto=crypto, stocks=stocks, intel=intel,
              signals=signals, clock=clock or Clock(ny(2026, 10, 16, 10, 30)), cfg=cfg, log=lambda m: None)
    return bot, cfg


def cand(sym="AAA/USDC", price=105.15, pct=5.0, vol=1e6, cls="crypto", quote="USDC"):
    return Candidate(sym, cls, quote, price, pct, vol)


def test_exit_rules():
    check("trailing: dal picco", exit_reason(100, 110, 108, 0, 0.015, 0) == "TRAILING STOP")
    check("stop loss: mai salito", exit_reason(100, 100, 98, 0, 0.015, 0) == "STOP LOSS")
    check("nessuna uscita in range", exit_reason(100, 101, 100.5, 0, 0.015, 0) is None)
    check("time stop", exit_reason(100, 101, 100.5, 90000, 0.015, 86400) == "TIME STOP")
    check("trend reversal solo sopra breakeven",
          exit_reason(100, 103, 102, 0, 0.05, 0, sma_val=103, breakeven=0.016) == "TREND REVERSAL")
    check("niente reversal sotto breakeven",
          exit_reason(100, 101, 100.5, 0, 0.05, 0, sma_val=103, breakeven=0.016) is None)
    check("flatten ha la precedenza", exit_reason(100, 101, 100.5, 0, 0.05, 0, flatten=True) == "EOD FLATTEN")


def test_paper_math():
    cfg = make_cfg(CRYPTO_FEE_RATE=0.006, CRYPTO_SLIPPAGE_BPS=5)
    b = PaperBroker(cfg)
    f = b.buy("crypto", 100.0, 100.0)
    check("buy: fee 0.6%", abs(f["fee"] - 0.6) < 1e-9)
    check("buy: qty al netto di fee e slippage", abs(f["qty"] - 99.4 / (100.0 * 1.0005)) < 1e-9)
    s = b.sell("crypto", 100.0, f["qty"])
    expected = f["qty"] * 100.0 * 0.9995 * (1 - 0.006)
    check("sell: proventi netti", abs(s["proceeds"] - expected) < 1e-9)
    check("round trip a prezzo fermo = perdita ~1.3%", 0.011 < 1 - s["proceeds"] / 100.0 < 0.015)
    st = b.buy("stock", 10.0, 50.0)
    check("azioni: nessuna commissione", st["fee"] == 0.0)
    check("azioni penny: slippage maggiore di quelle >= 5$",
          b.buy("stock", 3.0, 50.0)["avg_price"] / 3.0 > b.buy("stock", 20.0, 50.0)["avg_price"] / 20.0)


def test_crypto_rules():
    cfg = make_cfg()
    check("ok su serie valida", crypto_entry(cand(price=GOOD_CLOSES[-1]), GOOD_CLOSES, REGIME_OK, cfg).ok)
    check("blocco in panico", crypto_entry(cand(), GOOD_CLOSES, {"block": True, "label": "panico"}, cfg).reasons[0].startswith("regime"))
    check("pct fuori range", crypto_entry(cand(pct=40), GOOD_CLOSES, REGIME_OK, cfg).reasons == ["pct_fuori_range"])
    check("volume basso", crypto_entry(cand(vol=100), GOOD_CLOSES, REGIME_OK, cfg).reasons == ["volume_basso"])
    check("storico insufficiente", crypto_entry(cand(), GOOD_CLOSES[:20], REGIME_OK, cfg).reasons == ["storico_insufficiente"])
    check("sotto SMA", crypto_entry(cand(price=90), GOOD_CLOSES, REGIME_OK, cfg).reasons == ["sotto_sma"])
    check("troppo esteso", crypto_entry(cand(price=115), GOOD_CLOSES, REGIME_OK, cfg).reasons == ["troppo_esteso"])


def test_stock_rules():
    cfg = make_cfg()
    ctx = {"can_enter": True, "reasons": []}
    tape = [10.0, 10.1, 10.2, 10.3, 10.4]
    c = cand("XYZ", 10.5, 8.0, 0, "stock", "USD")
    check("azione ok", stock_entry(c, tape, ctx, cfg).ok)
    check("finestra chiusa", not stock_entry(c, tape, {"can_enter": False, "reasons": ["chiuso"]}, cfg).ok)
    check("tape insufficiente", stock_entry(c, tape[:2], ctx, cfg).reasons == ["tape_insufficiente"])
    check("pct troppo alta (parabolica)", stock_entry(cand("XYZ", 10.5, 55.0, 0, "stock", "USD"), tape, ctx, cfg).reasons == ["pct_fuori_range"])
    check("prezzo penny escluso", stock_entry(cand("XYZ", 1.0, 8.0, 0, "stock", "USD"), tape, ctx, cfg).reasons == ["prezzo_basso"])
    check("tape in calo", stock_entry(c, [10.0, 10.5, 10.4, 10.45, 10.3], ctx, cfg).reasons == ["tape_in_calo"] or
          stock_entry(c, [10.0, 10.5, 10.4, 10.45, 10.3], ctx, cfg).reasons == ["sotto_media_tape"])
    check("regex ETF a leva", bool(ETF_RE.search("ProShares UltraPro QQQ")) and not ETF_RE.search("Acme Robotics Inc"))


def test_market_state():
    open_ts = ny(2026, 10, 16, 10, 0)   # venerdi
    st = market_state(open_ts)
    check("borsa aperta alle 10:00 NY", st["open"] and abs(st["since_open"] - 30) < 0.1 and abs(st["to_close"] - 360) < 0.1)
    check("chiusa alle 9:00 NY", not market_state(ny(2026, 10, 16, 9, 0))["open"])
    check("chiusa alle 16:00 NY", not market_state(ny(2026, 10, 16, 16, 0))["open"])
    check("chiusa di sabato", not market_state(ny(2026, 10, 17, 11, 0))["open"])


class FakeResp:
    def __init__(self, status, data):
        self.status_code, self._d = status, data

    def json(self):
        return self._d


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.n = routes, 0

    def get(self, url, params=None, timeout=0):
        self.n += 1
        for key, (st, data) in self.routes.items():
            if url.endswith(key):
                return FakeResp(st, data)
        return FakeResp(404, [])


def test_fmp_budget():
    http = FakeHttp({"biggest-gainers": (200, [{"symbol": "A"}]), "quote": (402, {})})
    clock = Clock(ny(2026, 10, 16, 10, 0))
    f = FmpClient(key="k", budget=3, http=http, clock=clock)
    check("movers ok", f.movers("biggest-gainers") == [{"symbol": "A"}])
    check("402 blocca quel titolo su quell'endpoint", f.quote("A") is None and ("quote", "A") in f.blocked)
    f.quote("A")
    check("endpoint a pagamento non ritentato", http.n == 2, str(http.n))
    f.movers("biggest-gainers")
    check("budget esaurito -> None", f.movers("biggest-gainers") == [] and f.remaining() == 0)
    clock.t += 86400 * 1.5
    check("budget si azzera il giorno dopo", f.remaining() == 3)
    check("senza chiave nessuna chiamata", FmpClient(key="", http=http).movers("x") == [])


class SymbolHttp:
    """402 solo per il titolo 'BAD' (non coperto dal piano), 200 per gli altri."""

    def __init__(self):
        self.n = 0

    def get(self, url, params=None, timeout=0):
        self.n += 1
        if params.get("symbol") == "BAD":
            return FakeResp(402, {})
        return FakeResp(200, [{"symbol": params.get("symbol"), "price": 1.0}])


def test_fmp_block_scope():
    http = SymbolHttp()
    f = FmpClient(key="k", budget=50, http=http, clock=Clock(ny(2026, 10, 16, 10, 0)))
    check("402 su un titolo: quel titolo e' escluso", f.quote("BAD") is None and f.last_error == "blocked")
    n = http.n
    f.quote("BAD")
    check("titolo non coperto non viene richiesto di nuovo", http.n == n)
    check("un titolo coperto funziona ancora dopo il 402 di un altro", f.quote("GOOD") is not None)


def test_stock_tradable():
    http = SymbolHttp()
    clock = Clock(ny(2026, 10, 16, 10, 30))
    u = StockUniverse(FmpClient(key="k", budget=50, http=http, clock=clock), make_cfg(), clock)
    check("tradable: titolo non coperto -> False", u.tradable("BAD") is False)
    check("tradable: titolo coperto -> True e prezzo nel tape", u.tradable("GOOD") is True and u.tape_prices("GOOD") == [1.0])
    n = http.n
    u.tradable("GOOD")
    u.tradable("BAD")
    check("tradable: risposta memorizzata, nessuna chiamata in piu'", http.n == n)

    # nel motore: un titolo senza prezzo FMP non viene aperto, il successivo si'
    class Picky(FakeStocks):
        def tradable(self, s):
            return s != "NOPE"

    st = Picky()
    st.cands = [cand("NOPE", 10.5, 9.0, 0, "stock", "USD"), cand("YEP", 10.5, 8.0, 0, "stock", "USD")]
    tmp = tempfile.mkdtemp(prefix="unibot_")
    try:
        for s in ("NOPE", "YEP"):
            st.tapes[s] = [10.0, 10.1, 10.2, 10.3, 10.4]
        bot, _ = new_bot(tmp, stocks=st, STOCK_START_USD=150)
        bot.cycle()
        check("motore: titolo senza prezzo FMP scartato, il successivo aperto",
              "NOPE" not in bot.positions and "YEP" in bot.positions and bot.rejects.get("senza_prezzo_fmp") == 1,
              str(bot.rejects))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_stock_universe():
    gain = [
        {"symbol": "OKAY", "name": "Okay Corp", "price": 12.0, "changesPercentage": 9.0, "exchange": "NASDAQ"},
        {"symbol": "LEVX", "name": "ProShares UltraPro QQQ", "price": 40.0, "changesPercentage": 9.0, "exchange": "NASDAQ"},
        {"symbol": "PENY", "name": "Penny Inc", "price": 0.5, "changesPercentage": 60.0, "exchange": "NASDAQ"},
        {"symbol": "OTCX", "name": "Otc Inc", "price": 8.0, "changesPercentage": 9.0, "exchange": "OTC"},
        {"symbol": "ILLQ", "name": "Illiquid Inc", "price": 7.0, "changesPercentage": 9.0, "exchange": "NYSE"},
        {"symbol": "PUMP", "name": "Pump Inc", "price": 9.0, "changesPercentage": 80.0, "exchange": "NYSE"},
    ]
    act = [{"symbol": "OKAY", "price": 12.0}, {"symbol": "LEVX", "price": 40.0}]
    http = FakeHttp({"biggest-gainers": (200, gain), "most-actives": (200, act), "quote": (200, [{"volume": 100}])})
    clock = Clock(ny(2026, 10, 16, 10, 30))
    fmp = FmpClient(key="k", budget=100, http=http, clock=clock)
    u = StockUniverse(fmp, make_cfg(), clock)
    out = [c.symbol for c in u.scan()]
    check("radar: solo il titolo valido passa", out == ["OKAY"], str(out))
    check("radar: tape popolato", u.tape_prices("OKAY") == [12.0])
    u.scan()
    check("radar: dati identici -> fermi (stale)", u.stale)
    ctx = u.context(clock())
    check("contesto: niente ingressi con dati fermi", not ctx["can_enter"] and "dati_fermi" in ctx["reasons"])
    u2 = StockUniverse(fmp, make_cfg(), Clock(ny(2026, 10, 16, 10, 30)))
    u2.stale = False
    ctx2 = u2.context(ny(2026, 10, 16, 9, 40))
    check("contesto: niente ingressi nei primi 15 min", "apertura" in ctx2["reasons"] or not ctx2["open"])
    ctx3 = u2.context(ny(2026, 10, 16, 15, 55))
    check("contesto: flatten vicino alla chiusura", ctx3["flatten"])


def test_engine_crypto_flow(tmp):
    cr = FakeCrypto()
    cr.cands = [cand(price=GOOD_CLOSES[-1])]
    cr.cl["AAA/USDC"] = GOOD_CLOSES
    cr.px["AAA/USDC"] = GOOD_CLOSES[-1]
    bot, cfg = new_bot(tmp, crypto=cr, intel=FakeIntel(60), CRYPTO_START_USDC=100, CRYPTO_START_EUR=0)
    s = bot.cycle()
    check("ingresso aperto", s["entries"] == 1 and "AAA/USDC" in bot.positions)
    check("wallet USDC ridotto", bot.wallets["USDC"] < 100)
    cr.cands = []
    cr.px["AAA/USDC"] = 110.0
    bot.cycle()
    check("nessuna uscita mentre sale", "AAA/USDC" in bot.positions and bot.positions["AAA/USDC"].highest >= 110.0)
    cr.px["AAA/USDC"] = 108.0
    s = bot.cycle()
    check("trailing stop in profitto", s["exits"] == 1 and "AAA/USDC" not in bot.positions)
    st = bot.store.stats()
    check("trade archiviato come vinto", st["total"] == 1 and st["wins"] == 1 and st["net_eur"] > 0, str(st))
    check("commissioni registrate", st["fees_eur"] > 0)
    check("lordo = netto + fee", abs(st["gross_eur"] - (st["net_eur"] + st["fees_eur"])) < 1e-9)


def test_engine_panic_block(tmp):
    cr = FakeCrypto()
    cr.cands = [cand(price=GOOD_CLOSES[-1])]
    cr.cl["AAA/USDC"] = GOOD_CLOSES
    cr.px["AAA/USDC"] = GOOD_CLOSES[-1]
    bot, _ = new_bot(tmp, crypto=cr, intel=FakeIntel(15))
    s = bot.cycle()
    check("panico: nessun ingresso", s["entries"] == 0 and not bot.positions)
    check("panico: motivo contato", bot.rejects.get("regime_panico", 0) == 1)
    def sub(name):
        d = tmp + "_" + name
        os.makedirs(d, exist_ok=True)
        return d

    bot2, _ = new_bot(sub("b"), crypto=cr, intel=FakeIntel(available=False))
    s2 = bot2.cycle()
    check("regime ignoto e senza cache: nessun ingresso (fail-closed)",
          s2["entries"] == 0 and bot2.rejects.get("regime_sconosciuto", 0) == 1)

    bot3, _ = new_bot(sub("c"), crypto=cr, intel=FakeIntel(available=False), REGIME_UNKNOWN_BLOCK=False)
    s3 = bot3.cycle()
    check("policy permissiva esplicita: entra con regime ignoto", s3["entries"] == 1 and bot3.last_regime["label"] == "unknown")

    bot4, _ = new_bot(sub("d"), crypto=cr, intel=FakeIntel(15))
    bot4.cycle()
    bot4.intel = FakeIntel(available=False)          # la fonte cade dopo un panico noto
    bot4.cycle()
    check("fonte caduta: usa l'ultimo regime noto (cache) e resta bloccato",
          bot4.last_regime["block"] and "(cache)" in bot4.last_regime["label"] and not bot4.positions, str(bot4.last_regime))

    bot5, _ = new_bot(sub("e"), crypto=cr, intel=FakeIntel(60))
    bot5.cycle()
    cr5 = bot5.crypto
    cr5.cands = []
    bot5.intel = FakeIntel(available=False)
    bot5.cycle()
    check("fonte caduta dopo regime buono: cache non blocca", not bot5.last_regime["block"] and "(cache)" in bot5.last_regime["label"])

    bot6, _ = new_bot(sub("f"), crypto=cr, intel=FakeIntel(60))
    bot6.cycle()
    bot6.intel = FakeIntel(available=False)
    bot6.clock.t += 40 * 3600                         # cache scaduta (36 h)
    reg6 = bot6._crypto_regime()
    check("cache scaduta: torna ignoto e blocca", reg6["label"] == "unknown" and reg6["block"], str(reg6))
    for n in "bcdef":
        shutil.rmtree(tmp + "_" + n, ignore_errors=True)


def test_circuit_breaker(tmp):
    cr = FakeCrypto()
    bot, cfg = new_bot(tmp, crypto=cr, intel=FakeIntel(60), CRYPTO_START_USDC=100)
    now = bot.clock()
    for i in range(3):
        sym = "L%d/USDC" % i
        bot.positions[sym] = Position(sym, "crypto", "USDC", 100.0, 0.05, 5.0, 0.03, 100.0, now, "x", 100.0)
        bot._close(bot.positions[sym], 95.0, "STOP LOSS", now)
    check("3 perdite -> circuit breaker", bot.circuit_until > now and bot._entries_blocked(now) == "circuit_breaker")
    check("cooldown sul simbolo perso", bot.cooldown.get("L0/USDC", 0) > now)
    bot.clock.t += 31 * 60
    check("circuit breaker scade", bot._entries_blocked(bot.clock()) is None)


def test_daily_loss_limit(tmp):
    bot, cfg = new_bot(tmp, crypto=FakeCrypto(), intel=FakeIntel(60), CRYPTO_START_USDC=100, CRYPTO_START_EUR=0)
    bot._roll_day(bot.clock())
    bot.day_pnl = -0.5 * bot.day_start_equity
    check("limite perdita giornaliera blocca gli ingressi", bot._entries_blocked(bot.clock()) == "limite_giornaliero")


def test_stock_flatten_and_old_positions(tmp):
    st = FakeStocks()
    bot, cfg = new_bot(tmp, stocks=st, STOCK_START_USD=150)
    now = bot.clock()
    bot.positions["OLD"] = Position("OLD", "stock", "USD", 10.0, 1.0, 10.0, 0.0, 10.2, now - 86400, "2026-10-15", 10.1)
    st.px["OLD"] = 10.1
    bot.cycle()
    check("posizione di ieri chiusa all'apertura (no overnight)", "OLD" not in bot.positions)
    st.ctx["flatten"] = True
    bot.positions["NEW"] = Position("NEW", "stock", "USD", 10.0, 1.0, 10.0, 0.0, 10.2, now, "2026-10-16", 10.1)
    st.px["NEW"] = 10.2
    bot._last_stock_check = 0
    bot.cycle()
    check("flatten a fine giornata", "NEW" not in bot.positions)
    reasons = {t["reason"] for t in bot.store.recent_trades(5)}
    check("motivo EOD FLATTEN registrato", "EOD FLATTEN" in reasons)


def test_stock_entry_flow(tmp):
    st = FakeStocks()
    st.cands = [cand("XYZ", 10.5, 8.0, 0, "stock", "USD")]
    st.tapes["XYZ"] = [10.0, 10.1, 10.2, 10.3, 10.4]
    bot, cfg = new_bot(tmp, stocks=st, STOCK_START_USD=150)
    s = bot.cycle()
    check("azione: ingresso aperto con wallet USD", s["entries"] == 1 and bot.wallets["USD"] < 150)
    check("azione: commissioni zero, solo slippage", bot.positions["XYZ"].entry_fee == 0.0)


def test_signals(tmp):
    path = os.path.join(tmp, "sig.json")
    clock = Clock(1_000_000.0)
    sg = Signals(path, clock)
    sg.add("aaa", "canale-test", "nota", "buy", ttl_hours=1)
    sg.add("XAUUSD", "canale-test")
    sg.add("BBB", "canale-test", side="avoid")
    check("lookup per base", sg.lookup("AAA/USDC")["source"] == "canale-test")
    check("segnale non nel mercato -> non abbinato", [s["symbol"] for s in sg.unmatched({"AAA/USDC", "BBB/USDC"})] == ["XAUUSD"])
    clock.t += 3601
    check("segnale scaduto", sg.lookup("AAA/USDC") is None or sg.lookup("AAA/USDC")["symbol"] != "AAA")

    clock2 = Clock(ny(2026, 10, 16, 10, 30))
    sg2 = Signals(os.path.join(tmp, "sig2.json"), clock2)
    sg2.add("BBB", "fonte", side="avoid")
    sg2.add("CCC", "fonte", side="buy")
    cr = FakeCrypto()
    for sym in ("BBB/USDC", "CCC/USDC", "DDD/USDC"):
        cr.cands.append(cand(sym, GOOD_CLOSES[-1], 5.0))
        cr.cl[sym] = GOOD_CLOSES
        cr.px[sym] = GOOD_CLOSES[-1]
    bot, _ = new_bot(tmp + "_s", crypto=cr, intel=FakeIntel(60), signals=sg2, clock=clock2) if os.makedirs(tmp + "_s", exist_ok=True) is None else (None, None)
    bot.cycle()
    check("segnale avoid blocca l'ingresso", "BBB/USDC" not in bot.positions)
    check("segnale buy ha priorita'", "CCC/USDC" in bot.positions and bot.positions["CCC/USDC"].signal == "fonte")
    shutil.rmtree(tmp + "_s", ignore_errors=True)


def test_persistence(tmp):
    cr = FakeCrypto()
    cr.cands = [cand(price=GOOD_CLOSES[-1])]
    cr.cl["AAA/USDC"] = GOOD_CLOSES
    cr.px["AAA/USDC"] = GOOD_CLOSES[-1]
    bot, cfg = new_bot(tmp, crypto=cr, intel=FakeIntel(60))
    bot.cycle()
    wallets = dict(bot.wallets)
    bot2 = Bot(Store(os.path.join(tmp, "t.db")), crypto=cr, intel=FakeIntel(60), clock=bot.clock, cfg=cfg, log=lambda m: None)
    check("riavvio: posizioni ripristinate", "AAA/USDC" in bot2.positions)
    check("riavvio: wallet ripristinato", bot2.wallets == wallets)


class FakePrices:
    def __init__(self, series):
        self.series = series

    def get(self, symbol, since):
        return self.series.get(symbol)


def weekday_series(start, n, fn):
    """n giorni di borsa (lun-ven) da `start`; fn(i) = chiusura del giorno i."""
    day, out = datetime.date.fromisoformat(start), []
    while len(out) < n:
        if day.weekday() < 5:
            out.append((day.isoformat(), fn(len(out))))
        day += datetime.timedelta(days=1)
    return out


def test_backtest_replay():
    from . import backtest
    from .models import Decision
    cfg = make_cfg(CRYPTO_TRAIL_PCT=0.015)
    n, base = 400, 1_760_000_000_000

    def flat(i, px):
        return [base + i * 300000, px, px, px, px, 1000.0]

    candles = [flat(i, 100.0) for i in range(n)]
    for j in range(289, 300):
        candles[j] = flat(j, 110.0)                       # gap: la candela dopo il segnale apre a 110
    candles[300] = [candles[300][0], 110.0, 110.0, 100.0, 100.0, 1000.0]   # crollo intracandela

    calls = {"n": 0}
    orig = backtest.crypto_entry

    def once(c, closes, regime, cfg_):
        calls["n"] += 1
        return Decision(True, ["ok"], 1.0) if calls["n"] == 1 else Decision(False, ["x"])

    backtest.crypto_entry = once
    try:
        tr = backtest.replay_symbol("X/USDC", candles, {}, {"gate": False}, 0.01, 0.0, cfg)
    finally:
        backtest.crypto_entry = orig
    expected = 0.99 * 0.99 * (110 * (1 - 0.015)) / 110 - 1
    check("backtest: un solo trade, uscita per stop", len(tr) == 1 and tr[0]["reason"] == "STOP LOSS", str(tr))
    check("backtest: entra all'apertura della candela DOPO il segnale e applica fee e stop",
          abs(tr[0]["ret"] - expected) < 1e-9, "ret=%s atteso=%s" % (tr[0]["ret"], expected))

    days = {datetime.datetime.fromtimestamp(c[0] / 1000, datetime.timezone.utc).strftime("%Y-%m-%d") for c in candles}
    panic = {d: 10 for d in days}

    def respects_regime(c, closes, regime, cfg_):
        return Decision(not regime.get("block"), ["ok"], 1.0)

    backtest.crypto_entry = respects_regime
    try:
        blocked = backtest.replay_symbol("X/USDC", candles, panic, {"gate": True}, 0.0, 0.0, cfg)
        open_ = backtest.replay_symbol("X/USDC", candles, panic, {"gate": False}, 0.0, 0.0, cfg)
    finally:
        backtest.crypto_entry = orig
    check("backtest: blocco in panico elimina gli ingressi", len(blocked) == 0 and len(open_) >= 1, "%d/%d" % (len(blocked), len(open_)))
    s = backtest.summarize([{"ret": 0.02, "reason": "TRAILING STOP", "hold_h": 1.0},
                            {"ret": -0.01, "reason": "STOP LOSS", "hold_h": 3.0}])
    check("backtest: riepilogo (win%, media, profit factor)",
          s["n"] == 2 and abs(s["win_pct"] - 50.0) < 1e-9 and abs(s["mean_pct"] - 0.5) < 1e-9 and abs(s["pf"] - 2.0) < 1e-9, str(s))


class YResp:
    def __init__(self, status, data=None):
        self.status_code, self._d = status, data

    def json(self):
        return self._d


class YHttp:
    """Risposte a copione: ogni get() consuma la prossima; l'ultima si ripete."""

    def __init__(self, script):
        self.script, self.n = list(script), 0

    def get(self, url, params=None, headers=None, timeout=0):
        self.n += 1
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


def _chart(stamps, adj):
    return {"chart": {"result": [{"timestamp": stamps, "indicators": {
        "quote": [{"close": [1.0] * len(stamps)}], "adjclose": [{"adjclose": adj}]}}]}}


def test_yahoo_history(tmp):
    from .signal_lab import YahooHistory
    ts = [int(datetime.datetime(2026, 10, d, 13, 30, tzinfo=datetime.timezone.utc).timestamp()) for d in (1, 2, 5)]
    ok = YResp(200, _chart(ts, [10.0, None, 12.0]))
    y = YahooHistory(cache_dir=os.path.join(tmp, "y1"), http=YHttp([ok]), retry_delay=0, pause=0)
    s = y.get("AAA")
    check("yahoo: serie con date UTC, valori nulli scartati", s == [("2026-10-01", 10.0), ("2026-10-05", 12.0)], str(s))
    h2 = YHttp([ok])
    y2 = YahooHistory(cache_dir=os.path.join(tmp, "y1"), http=h2, retry_delay=0, pause=0)
    check("yahoo: seconda lettura dalla cache su disco", y2.get("AAA") == s and h2.n == 0)

    y3 = YahooHistory(cache_dir=os.path.join(tmp, "y3"), http=YHttp([YResp(404, {})]), retry_delay=0, pause=0)
    check("yahoo: 404 = titolo senza dati (non errore di rete)", y3.get("DEAD") is None and "DEAD" in y3.failed
          and "DEAD" not in y3.network_failed)
    y4 = YahooHistory(cache_dir=os.path.join(tmp, "y4"), http=YHttp([YResp(429)]), retry_delay=0, pause=0)
    check("yahoo: 429 persistente = errore di rete da riprovare", y4.get("BUSY") is None
          and "BUSY" in y4.network_failed and "BUSY" not in y4.failed)
    y5 = YahooHistory(cache_dir=os.path.join(tmp, "y5"), http=YHttp([ConnectionError("dns"), ok]), retry_delay=0, pause=0)
    check("yahoo: errore di rete transitorio ritentato", y5.get("AAA") is not None and not y5.network_failed)


def _synthetic_panel(edge, seed, T=1500, S=30):
    """Funding persistente per moneta + rendimenti casuali; con edge>0 il funding ALTO prevede rendimenti bassi."""
    import numpy as np
    rng = np.random.default_rng(seed)
    a = rng.normal(0, 1e-4, S)
    F = a + rng.normal(0, 2e-5, (T, S))
    from .funding_lab import rolling_mean
    F7 = rolling_mean(F)
    zs = (F7 - np.nanmean(F7, axis=1, keepdims=True)) / np.nanstd(F7, axis=1, keepdims=True)
    r = rng.normal(0, 0.01, (T, S))
    for t in range(2, T):
        if not np.isnan(zs[t - 2]).any():
            r[t] -= edge * zs[t - 2]
    P = 100 * np.cumprod(1 + r, axis=0)
    return F, F7, P


def test_funding_helpers():
    try:
        import numpy as np
        import pandas  # noqa: F401
    except ImportError:
        print("SKIP - numpy/pandas assenti")
        return
    from .funding_lab import normalize_base, build_panel, STEP_MS, list_symbols
    check("funding: 1000SHIB -> SHIB", normalize_base("1000SHIB") == "SHIB" and normalize_base("BTC") == "BTC")
    t0 = 1_700_000_000_000 // STEP_MS * STEP_MS
    series = {"AAA": {"funding": [(t0 + k * STEP_MS + 3, 0.0001) for k in range(300)],
                      "open": [(t0 + k * STEP_MS, 10.0 + k) for k in range(300)]}}
    syms, F, P = build_panel(series, t0, t0 + 299 * STEP_MS, warmup=270)
    check("funding: orari con scarto di millisecondi agganciati alla griglia 8h", np.isfinite(F[270, 0]) and np.isfinite(F[299, 0]))
    check("funding: prime 90 giornate della moneta escluse (assestamento)", np.isnan(F[:270, 0]).all())
    check("funding: prezzi allineati alla stessa griglia", abs(P[5, 0] - 15.0) < 1e-9)

    class Http:
        def get(self, url, params=None, timeout=0):
            info = {"symbols": [
                {"contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "TRADING", "baseAsset": "BTC", "symbol": "BTCUSDT"},
                {"contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "TRADING", "baseAsset": "1000SHIB", "symbol": "1000SHIBUSDT"},
                {"contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "TRADING", "baseAsset": "XAU", "symbol": "XAUUSDT"},
                {"contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "TRADING", "baseAsset": "SOXL", "symbol": "SOXLUSDT"},
                {"contractType": "PERPETUAL", "quoteAsset": "USDT", "status": "BREAK", "baseAsset": "ETH", "symbol": "ETHUSDT"},
                {"contractType": "CURRENT_QUARTER", "quoteAsset": "USDT", "status": "TRADING", "baseAsset": "SOL", "symbol": "SOLUSDT_X"}]}
            return YResp(200, info)

    m = list_symbols(Http())
    check("funding: solo vere crypto perpetue in trading (oro, ETF, trimestrali, sospese esclusi)",
          m == {"BTC": "BTCUSDT", "SHIB": "1000SHIBUSDT"}, str(m))


def test_funding_stats():
    try:
        import numpy as np  # noqa: F401
        import pandas  # noqa: F401
    except ImportError:
        print("SKIP - numpy/pandas assenti")
        return
    from .funding_lab import cross_section, stat

    for seed in (1, 2, 3):
        F, F7, P = _synthetic_panel(0.0, seed)
        for off in (0, 1):
            cs = cross_section(F7, P, 3, offset=off)
            s = stat(cs[:, 1] - cs[:, 2])
            check("funding: nessun legame inserito -> nessun segnale (seme %d, sfasamento %d, t %.2f)" % (seed, off, s["t"]),
                  abs(s["t"]) < 3.5)
    F, F7, P = _synthetic_panel(0.002, 5)
    for off in (0, 1):
        cs = cross_section(F7, P, 3, offset=off)
        s = stat(cs[:, 1] - cs[:, 2])
        check("funding: legame inserito (funding alto -> rendimenti bassi) -> rilevato (sfasamento %d, t %.1f)" % (off, s["t"]),
              s["mean"] > 0 and s["t"] > 5)

    # nessuna informazione dal futuro: il rialzo avviene NEL periodo di ritardo (tra P[i0] e P[i0+1])
    import numpy as np
    T, Sn, i0 = 120, 15, 40
    Fc = np.tile(np.linspace(0, 1e-3, Sn), (T, 1))
    Pj = np.full((T, Sn), 100.0)
    Pj[i0 + 1:, :3] = 200.0                      # le 3 monete a funding piu' basso raddoppiano subito dopo il segnale
    cs = cross_section(Fc.copy(), Pj, 3, offset=i0, min_syms=10)
    check("funding: l'entrata e' il periodo DOPO il segnale (il balzo nel ritardo non conta)",
          len(cs) >= 1 and int(cs[0, 0]) == i0 and abs(cs[0, 1]) < 1e-9, str(cs[:1]))


def test_funding_fetch_paging():
    from .funding_lab import fetch_funding, fetch_opens
    funding = [{"fundingTime": 1000 + k, "fundingRate": "0.0001"} for k in range(1005)]
    klines = [[2000 + k, "10", "11", "9", "10", "1"] for k in range(1007)]

    class Http:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None, timeout=0):
            self.calls.append(params["startTime"])
            if "fundingRate" in url:
                rows = [x for x in funding if x["fundingTime"] >= params["startTime"]][:params["limit"]]
            else:
                rows = [x for x in klines if x[0] >= params["startTime"]][:params["limit"]]
            return YResp(200, rows)

    h = Http()
    out = fetch_funding("X", 0, 10 ** 9, h, pause=0)
    check("funding: paginazione completa senza buchi ne' doppioni", len(out) == 1005 and len({t for t, _ in out}) == 1005
          and h.calls == [0, 2000], str(h.calls))
    h2 = Http()
    out2 = fetch_opens("X", 0, 10 ** 9, h2, pause=0)
    check("funding: paginazione candele completa", len(out2) == 1007 and h2.calls == [0, 3000], str(h2.calls))


def test_trend_lab(tmp):
    try:
        import numpy as np
        import pandas  # noqa: F401
    except ImportError:
        print("SKIP - numpy/pandas assenti")
        return
    from . import trend_lab as tl

    # lettura giornaliera: solo le aperture delle 00:00 UTC, dalle candele a 8 ore
    day0 = 20_000
    opens = []
    for d in range(450):
        for k, h in enumerate((0, 8, 16)):
            opens.append([(day0 + d) * tl.DAY_MS + h * 3_600_000, 100.0 + d + k * 0.1])
    os.makedirs(os.path.join(tmp, "funding"), exist_ok=True)
    with open(os.path.join(tmp, "funding", "AAA.json"), "w") as f:
        json.dump({"funding": [], "open": opens}, f)
    syms, dates, P = tl.load_daily(tmp)
    check("trend: una apertura al giorno, quella delle 00:00 UTC",
          syms == ["AAA"] and P.shape == (450, 1) and abs(P[10, 0] - 110.0) < 1e-9, str(P[:3, 0]))

    # contabilita' dei costi
    T = 100
    up = (100 * 1.01 ** np.arange(T)).reshape(-1, 1)
    eq, tr, inv = tl.run_weights(np.ones((T, 1)), up, 0.0)
    check("trend: senza costi il capitale segue il prezzo", abs(eq[-1] - 1.01 ** (T - 1)) < 1e-9)
    eq, tr, inv = tl.run_weights(np.ones((T, 1)), up, 0.01)
    check("trend: entrata iniziale costa un lato", abs(eq[-1] - 0.99 * 1.01 ** (T - 1)) < 1e-9 and tr == 1)
    flat = np.full((T, 1), 100.0)
    W = np.zeros((T, 1))
    W[::2] = 1.0                                   # entra/esce a giorni alterni
    eq, tr, inv = tl.run_weights(W, flat, 0.01)
    check("trend: entrare e uscire di continuo erode il capitale per i costi",
          eq[-1] < 0.55 and tr >= 90, "eq %.3f trades %d" % (eq[-1], tr))

    # nessuna informazione dal futuro: il balzo avviene PRIMA dell'entrata (P[60] e' gia' alto)
    jump = np.full((79, 1), 100.0)                 # fino a prima che la media raggiunga il nuovo livello (poi il segnale esce)
    jump[60:] = 150.0
    Wt = tl.trend_weights(jump, [0], 20)
    eq, tr, inv = tl.run_weights(Wt, jump, 0.01)
    check("trend: il balzo che fa scattare il segnale non viene incassato (entra al prezzo gia' alto)",
          abs(eq[-1] - 0.99) < 1e-9 and Wt[59, 0] == 0.0 and Wt[60, 0] == 1.0, "eq %.4f" % eq[-1])

    # momentum: tiene le K monete migliori sul rendimento a 28 giorni
    rng = np.random.default_rng(3)
    Pm = 100 * np.cumprod(1 + rng.normal(0, 0.005, (120, 8)), axis=0)
    drift = np.array([0.004, 0.003, 0, 0, 0, 0, -0.003, -0.004])
    Pm = Pm * np.exp(np.outer(np.arange(120), drift))
    Wm = tl.momentum_weights(Pm, k=2, lookback=28, every=7)
    held = set(np.flatnonzero(Wm[90] > 0))
    check("trend: momentum sceglie le 2 monete con il miglior rendimento a 28 giorni",
          held == {0, 1} and abs(Wm[90].sum() - 1.0) < 1e-9, str(sorted(held)))
    check("trend: prima del periodo di osservazione non si tiene nulla", Wm[:28].sum() == 0.0)

    rng2 = np.random.default_rng(9)
    Pq = 100 * np.cumprod(1 + rng2.normal(0.001, 0.02, (300, 3)), axis=0)
    res, bench = tl.evaluate(["BTCUSDT", "ETHUSDT", "SOLUSDT"], [str(i) for i in range(300)], Pq, 0.006)
    check("trend: BTC e ETH (nomi BTCUSDT/ETHUSDT) entrano nei risultati e nei riferimenti",
          "trend BTC SMA50" in res and "trend ETH SMA20" in res and "acquista-e-tieni BTC" in bench, str(list(res)[:4]))
    try:
        tl.evaluate(["SOLUSDT", "ADAUSDT"], [str(i) for i in range(300)], Pq[:, :2], 0.006)
        loud = False
    except ValueError:
        loud = True
    check("trend: se manca BTC il test si ferma con un errore (niente risultati parziali in silenzio)", loud)

    m = tl.metrics(np.array([1.0, 1.1, 1.21]), 2, 1.0, 2)
    check("trend: metriche (rendimento totale e nessun calo)", abs(m["total"] - 0.21) < 1e-9 and m["mdd"] == 0.0)


def test_quiver_loader():
    from .signal_lab import load_quiver_api

    def row(t, filed, traded, tx, name="Jane Doe"):
        return {"Ticker": t, "Filed": filed, "Traded": traded, "Transaction": tx, "Name": name}

    pages = [YResp(200, [row("AAA", "2026-09-10T00:00:00Z", "2026-08-01T00:00:00Z", "Purchase"),
                         row("BBB", "2026-09-11T00:00:00Z", "2026-08-02T00:00:00Z", "Sale")]),
             YResp(200, [row("CCC", "2026-09-12T00:00:00Z", "2026-08-03T00:00:00Z", "Purchase")])]
    http = YHttp(pages)
    sigs = load_quiver_api("tok", http=http, page_size=2, retry_delay=0)
    check("quiver: pagine lette fino all'ultima incompleta", http.n == 2 and len(sigs) == 3, "n=%d" % http.n)
    check("quiver: data nota = Filed (pubblicazione), evento = Traded",
          sigs[0]["known_date"] == "2026-09-10" and sigs[0]["event_date"] == "2026-08-01" and not sigs[0]["assumed_lag"])
    check("quiver: Purchase/Sale diventano buy/sell", [s["side"] for s in sigs] == ["buy", "sell", "buy"])
    try:
        load_quiver_api("bad", http=YHttp([YResp(403)]), retry_delay=0)
        refused = False
    except RuntimeError as e:
        refused = "token" in str(e)
    check("quiver: token rifiutato -> errore chiaro", refused)
    try:
        load_quiver_api("tok", http=YHttp([YResp(200, {"detail": "x"})]), retry_delay=0)
        odd = False
    except RuntimeError as e:
        odd = "inattesa" in str(e)
    check("quiver: risposta non-lista -> errore chiaro", odd)


def test_insider_loader(tmp):
    try:
        import pandas  # noqa: F401
    except ImportError:
        print("SKIP - pandas assente")
        return
    import zipfile
    from .insider_lab import load_filings
    sub = ("ACCESSION_NUMBER\tFILING_DATE\tDOCUMENT_TYPE\tISSUERTRADINGSYMBOL\tAFF10B5ONE\tISSUERNAME\n"
           "A1\t15-OCT-2025\t4\tabc\t0\tAbc Inc\n"
           "A2\t16-OCT-2025\t4\tBRK.B\t0\tBerkshire\n"
           "A3\t16-OCT-2025\t3\tTHREE\t0\tForm 3 Co\n"
           "A4\t16-OCT-2025\t4\tNONE\t0\tNo Ticker\n"
           "A5\t16-OCT-2025\t4\tCHEAP\t0\tPenny\n"
           "A6\t16-OCT-2025\t4\tSOLD\t0\tSeller\n")
    trans = ("ACCESSION_NUMBER\tTRANS_DATE\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\tTRANS_ACQUIRED_DISP_CD\n"
             "A1\t13-OCT-2025\tP\t1000\t50\tA\n"
             "A1\t13-OCT-2025\tP\t500\t50\tA\n"
             "A2\t14-OCT-2025\tP\t10000\t30\tA\n"
             "A3\t14-OCT-2025\tP\t1000\t50\tA\n"
             "A4\t14-OCT-2025\tP\t100\t10\tA\n"
             "A5\t14-OCT-2025\tP\t1000\t1.5\tA\n"
             "A6\t14-OCT-2025\tS\t1000\t50\tD\n")
    own = ("ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNER_RELATIONSHIP\n"
           "A1\t111\tOfficer\n"
           "A2\t222\tDirector\n"
           "A2\t333\tTenPercentOwner\n"
           "A5\t444\tOfficer\n")
    path = os.path.join(tmp, "q.zip")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("SUBMISSION.tsv", sub)
        z.writestr("NONDERIV_TRANS.tsv", trans)
        z.writestr("REPORTINGOWNER.tsv", own)
    df = load_filings(path)
    rows = {r.symbol: r for r in df.itertuples()}
    check("insider: solo Form 4, acquisti 'P', prezzo >= 2, simbolo valido",
          set(rows) == {"ABC", "BRK-B"}, str(sorted(rows)))
    check("insider: righe dello stesso modulo sommate", abs(rows["ABC"].value - 75_000) < 1e-6)
    check("insider: data di deposito ISO (mesi in maiuscolo)", rows["ABC"].filing_date == "2025-10-15")
    check("insider: ruolo officer/director riconosciuto", rows["ABC"].role_ok and rows["BRK-B"].role_ok)
    check("insider: insider distinti raccolti", rows["BRK-B"].owners == frozenset({"222", "333"}))


def test_insider_variants():
    try:
        import pandas as pd
    except ImportError:
        print("SKIP - pandas assente")
        return
    from .insider_lab import build_variants

    def rec(sym, date, value, owner, role=True):
        return {"ACCESSION_NUMBER": "%s-%s" % (sym, date), "symbol": sym, "filing_date": date, "trans_date": date,
                "value": value, "owners": frozenset({owner}), "role_ok": role, "plan10b5": False}

    df = pd.DataFrame([
        rec("AAA", "2025-01-02", 60_000, "1"),
        rec("AAA", "2025-01-12", 60_000, "2"),
        rec("AAA", "2025-02-11", 300_000, "1"),
        rec("BBB", "2025-01-05", 1_000_000, "9", role=False),
        rec("CCC", "2025-01-05", 40_000, "5"),
    ])
    v = build_variants(df)
    d1 = [s["known_date"] for s in v["acquisto>=50k"]]
    check("varianti: >=50k una volta ogni 30 giorni per titolo", d1 == ["2025-01-02", "2025-02-11"], str(d1))
    check("varianti: >=250k solo il grande acquisto", [s["known_date"] for s in v["acquisto>=250k"]] == ["2025-02-11"])
    cl = [s["known_date"] for s in v["cluster>=2 insider"]]
    check("varianti: cluster scatta con 2 insider diversi entro 30 giorni", cl == ["2025-01-12", "2025-02-11"], str(cl))
    allsyms = {s["symbol"] for lst in v.values() for s in lst}
    check("varianti: chi non e' officer/director e' escluso; un solo acquisto piccolo non basta",
          "BBB" not in allsyms and "CCC" not in allsyms, str(allsyms))
    check("varianti: i segnali portano la data di DEPOSITO come data nota",
          all(s["assumed_lag"] is False and s["side"] == "buy" for lst in v.values() for s in lst))


def test_welch_placebo():
    from .insider_lab import welch, make_placebo
    check("welch: campioni identici -> differenza 0", welch([1, 2, 3, 4], [1, 2, 3, 4]) == (0.0, 0.0))
    d, t = welch([5, 6, 7, 8], [1, 2, 3, 4])
    check("welch: differenza e t positivi se il primo gruppo e' maggiore", abs(d - 4.0) < 1e-12 and t > 3)
    check("welch: campione troppo piccolo -> None", welch([1], [1, 2]) is None)

    series = weekday_series("2026-01-01", 200, lambda i: 100 * 1.001 ** i)
    prices = FakePrices({"AAA": series})
    sigs = [{"symbol": "AAA", "known_date": "2026-03-02", "side": "buy"},
            {"symbol": "ZZZ", "known_date": "2026-03-02", "side": "buy"}]
    p1 = make_placebo(sigs, prices, draws=3, seed=7)
    p2 = make_placebo(sigs, prices, draws=3, seed=7)
    dates = [d for d, _ in series]
    idx = [dates.index(p["known_date"]) for p in p1]
    check("placebo: 3 date per segnale con prezzi, nessuna per titoli senza prezzi",
          len(p1) == 3 and all(p["symbol"] == "AAA" for p in p1))
    check("placebo: riproducibile con lo stesso seme", [p["known_date"] for p in p1] == [p["known_date"] for p in p2])
    check("placebo: date nel periodo e con 60 giorni di borsa di margine",
          all(dates[i] >= "2026-03-02" and i <= len(dates) - 1 - 60 for i in idx), str(idx))


def test_signal_loader(tmp):
    from .signal_lab import load_signals, normalize
    path = os.path.join(tmp, "q.csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write("Representative,ReportDate,TransactionDate,Ticker,Transaction,Range\n"
                "Jane Doe,2026-09-10,2026-08-01,AAA,Purchase,$1K-$15K\n"
                "John Roe,2026-09-12,2026-09-01,BBB,Sale (Full),$1K-$15K\n"
                "No Side,2026-09-12,2026-09-01,CCC,Exchange,$1K\n")
    sigs = load_signals(path)
    check("lab loader: 'Exchange' scartato, 2 validi", len(sigs) == 2)
    check("lab loader: data nota = ReportDate, non TransactionDate",
          sigs[0]["known_date"] == "2026-09-10" and sigs[0]["event_date"] == "2026-08-01")
    check("lab loader: lato normalizzato", sigs[0]["side"] == "buy" and sigs[1]["side"] == "sell")
    check("lab loader: nessun lag assunto con ReportDate", not any(s["assumed_lag"] for s in sigs))
    s = normalize([{"Ticker": "AAA", "TransactionDate": "2026-08-01", "Transaction": "Purchase"}], lag_days=30)
    check("lab loader: senza pubblicazione -> lag prudente segnalato",
          s[0]["known_date"] == "2026-08-31" and s[0]["assumed_lag"])
    g = normalize([{"Ticker": "A", "Date": "2026-08-01", "Transaction": "Buy"}], lag_days=30)
    check("lab loader: colonna generica 'Date' NON e' la data nota", g[0]["assumed_lag"])


def test_signal_lab_eval():
    from .signal_lab import evaluate, format_report
    steady = weekday_series("2026-06-01", 70, lambda i: 100 * 1.01 ** i)
    bench = weekday_series("2026-06-01", 70, lambda i: 100 * 1.005 ** i)
    prices = FakePrices({"AAA": steady, "SPY": bench})
    res = evaluate([{"symbol": "AAA", "known_date": "2026-07-01", "side": "buy", "assumed_lag": False}], prices)
    a5 = res["stats"][("buy", 5)]
    check("lab: rendimento a 5gg corretto", abs(a5["ret"]["mean"] - (1.01 ** 5 - 1)) < 1e-9, str(a5))
    check("lab: alpha = rendimento - SPY", abs(a5["alpha"]["mean"] - ((1.01 ** 5 - 1) - (1.005 ** 5 - 1))) < 1e-9)

    # salto di prezzo il giorno DOPO la pubblicazione: entrando alla chiusura di quel giorno il rendimento e' ~0
    jump = weekday_series("2026-06-01", 70, lambda i: 100.0 if i <= 21 else 120.0)  # i=21 -> 2026-07-01
    flat = weekday_series("2026-06-01", 70, lambda i: 100.0)
    res2 = evaluate([{"symbol": "JMP", "known_date": "2026-07-01", "side": "buy", "assumed_lag": False}],
                    FakePrices({"JMP": jump, "SPY": flat}))
    r1 = res2["stats"][("buy", 1)]["ret"]["mean"]
    check("lab: niente look-ahead (entra DOPO la data nota)", abs(r1) < 1e-9, "ret=%s" % r1)

    res3 = evaluate([{"symbol": "AAA", "known_date": "2026-12-01", "side": "buy", "assumed_lag": False},
                     {"symbol": "ZZZ", "known_date": "2026-07-01", "side": "buy", "assumed_lag": False}], prices)
    check("lab: segnali futuri e senza prezzi scartati con motivo",
          res3["skipped"]["troppo_recente"] == 1 and res3["skipped"]["senza_prezzi"] == 1, str(res3["skipped"]))
    check("lab: campione piccolo dichiarato non conclusivo", "non conclusivo" in format_report(res))
    check("lab: benchmark mancante -> errore esplicito",
          "benchmark" in evaluate([{"symbol": "AAA", "known_date": "2026-07-01", "side": "buy",
                                    "assumed_lag": False}], FakePrices({"AAA": steady})).get("error", ""))


def test_price_history_cache(tmp):
    from .signal_lab import PriceHistory
    rows = [{"date": "2026-07-02", "close": 120.0}, {"date": "2026-07-01", "close": 100.0}]
    http = FakeHttp({"historical-price-eod/full": (200, rows)})
    clock = Clock(ny(2026, 10, 16, 10, 0))
    fmp = FmpClient(key="k", budget=100, http=http, clock=clock)
    ph = PriceHistory(fmp, cache_dir=os.path.join(tmp, "px"), max_calls=1, clock=clock)
    s = ph.get("AAA", "2026-06-01")
    check("prezzi: serie ordinata crescente", s == [("2026-07-01", 100.0), ("2026-07-02", 120.0)], str(s))
    ph.get("AAA", "2026-06-01")
    check("prezzi: seconda lettura dalla cache (nessuna chiamata)", http.n == 1, str(http.n))
    check("prezzi: tetto di chiamate rispettato", ph.get("BBB", "2026-06-01") is None and http.n == 1)


class FlakyHttp:
    """Solleva un errore di rete per le prime `fails` richieste, poi risponde."""

    def __init__(self, fails, status, data):
        self.fails, self.status, self.data, self.n = fails, status, data, 0

    def get(self, url, params=None, timeout=0):
        self.n += 1
        if self.fails > 0:
            self.fails -= 1
            raise ConnectionError("dns")
        return FakeResp(self.status, self.data)


def test_network_resilience(tmp):
    from .signal_lab import PriceHistory, evaluate
    from .universe import CryptoUniverse
    rows = [{"date": "2026-07-02", "close": 120.0}, {"date": "2026-07-01", "close": 100.0}]
    clock = Clock(ny(2026, 10, 16, 10, 0))

    def ph_for(http, sub):
        return PriceHistory(FmpClient(key="k", budget=100, http=http, clock=clock),
                            cache_dir=os.path.join(tmp, sub), clock=clock, retry_delay=0)

    h1 = FlakyHttp(1, 200, rows)
    p1 = ph_for(h1, "a")
    check("rete: errore transitorio ritentato e recuperato",
          p1.get("AAA", "2026-06-01") is not None and not p1.network_failed and h1.n == 2, "n=%d" % h1.n)

    p2 = ph_for(FlakyHttp(99, 200, rows), "b")
    check("rete: errore persistente = errore di rete, NON 'senza dati'",
          p2.get("AAA", "2026-06-01") is None and "AAA" in p2.network_failed and "AAA" not in p2.failed)

    class NetDown(FakePrices):
        network_failed = {"AAA"}

    bench = weekday_series("2026-06-01", 70, lambda i: 100 * 1.005 ** i)
    res = evaluate([{"symbol": "AAA", "known_date": "2026-07-01", "side": "buy", "assumed_lag": False}],
                   NetDown({"SPY": bench}))
    check("rete: il report separa errore_rete da senza_prezzi",
          res["skipped"]["errore_rete"] == 1 and res["skipped"]["senza_prezzi"] == 0, str(res["skipped"]))

    h3 = FlakyHttp(0, 200, [])
    p3 = ph_for(h3, "c")
    check("rete: risposta vuota vera = senza dati, nessun ritentativo",
          p3.get("AAA", "2026-06-01") is None and "AAA" in p3.failed and h3.n == 1, "n=%d" % h3.n)

    class FlakyEx:
        def __init__(self):
            self.n = 0

        def fetch_tickers(self):
            self.n += 1
            if self.n == 1:
                raise ConnectionError("dns")
            return {"AAA/USDC": {"last": 1.0, "percentage": 5.0, "quoteVolume": 1e6}}

    cu = CryptoUniverse(FlakyEx(), make_cfg(), retry_delay=0)
    check("rete: scan crypto ritenta dopo un errore", [c.symbol for c in cu.scan()] == ["AAA/USDC"])


def test_etf_lab():
    import numpy as np
    from . import etf_lab as E
    n = 600
    up = 100 * np.cumprod(np.full(n, 1.0005))
    P = np.tile(up[:, None], (1, len(E.ETFS)))
    W = E.fixed_mix(P, [1.0] + [0.0] * (len(E.ETFS) - 1))
    check("etf: mix fisso pesa 1 sul primo", abs(W[100, 0] - 1.0) < 1e-12 and W[100, 1:].sum() == 0)
    R = E.rotation_weights(P)
    check("etf: rotazione senza storia = cassa", R[:200].sum() == 0)
    check("etf: rotazione tiene al piu' 3 ETF a peso 1/3", abs(R[300].sum() - 1.0) < 1e-9 and (R[300] > 0).sum() == 3)
    a = E.annualized(P[:, 0], None, 1)
    check("etf: annualizzato 1 anno coerente", abs(a - (1.0005 ** 252 - 1)) < 1e-6)
    check("etf: annualizzato 10 anni su storia corta = None", E.annualized(P[:, 0], None, 10) is None)
    ds = ["2004-06-01", "2004-12-31", "2005-01-03", "2005-12-30", "2006-01-02", "2006-12-29", "2007-01-02", "2007-06-01"]
    col = np.array([100, 100, 100, 70, 70, 90, 90, 95], dtype=float)
    wy, wr = E.worst_calendar_year(col, ds)
    check("etf: anno peggiore = 2005 (-30%), primo e ultimo anno esclusi", wy == "2005" and abs(wr + 0.30) < 1e-9)
    eq, tr, inv = __import__("unibot.trend_lab", fromlist=["x"]).run_weights(W, P, 0.001)
    flat = np.array([1.0] * 300)
    r0 = E.rolling_vs_cash(flat, 1, 0.025)
    check("etf: capitale fermo = sempre peggio del deposito, mai in perdita", r0["below_cash"] == 1.0 and r0["loss"] == 0.0 and r0["worst"] == 0.0)
    gr = np.cumprod(np.full(600, 1.0005))
    r1 = E.rolling_vs_cash(gr, 1, 0.025)
    check("etf: crescita 0,05%/giorno batte il deposito in ogni finestra", r1["below_cash"] == 0.0 and abs(r1["median"] - (1.0005 ** 252 - 1)) < 1e-6)
    check("etf: finestra piu' lunga della storia = None", E.rolling_vs_cash(flat, 5, 0.025) is None)
    check("etf: costo applicato solo all'ingresso", tr == 1 and eq[-1] < up[-1] / up[0] * 1.0)


def test_etf_forward(tmp):
    import numpy as np
    from . import etf_forward as F
    path = os.path.join(tmp, "st.json")
    import datetime as dt
    check("forward: prima esecuzione fissa la partenza", F.load_start(path, dt.date(2026, 10, 16)) == "2026-10-16")
    check("forward: la partenza non cambia dopo", F.load_start(path, dt.date(2027, 1, 1)) == "2026-10-16")
    n = 260
    dates = [(dt.date(2025, 10, 1) + dt.timedelta(days=i)).isoformat() for i in range(n)]
    up = 100 * np.cumprod(np.full(n, 1.001))
    P = np.column_stack([up, up])
    r = F.compute(dates, P, dates[230])
    check("forward: in trend le due meta' sono in posizione", all(r["state"].values()))
    check("forward: giorni e rendimento coerenti", r["days"] == n - 1 - 230 and r["strategy"] > 0.02)
    P2 = P.copy()
    P2[-5:, 1] = 10.0
    r2 = F.compute(dates, P2, dates[230])
    check("forward: crollo sotto la media = QQQ in cassa", r2["state"]["SPY"] and not r2["state"]["QQQ"])
    from . import etf_forward as F2
    rc = F2._report("crypto", F2.CRYPTO_RULE, {"BTC": dict(zip(dates, up)), "ETH": dict(zip(dates, up))}, dates[230])
    check("forward: report crypto con costo e giorni", "SMA100" in rc and "giorni: %d" % (n - 1 - 230) in rc)
    check("forward: partenza dopo l'ultimo dato = 0 giorni", F.compute(dates, P, "2099-01-01")["days"] == 0)


def test_daily_lab():
    import numpy as np
    from . import daily_lab as D
    r = np.array([0.02, -0.01, -0.03, 0.05, 0.0, 0.02])
    cs = D.cond_stats(r, 0.015)
    check("daily: dopo rialzo > soglia (giorni 0,3,5 -> successivi -0.01, 0.0)", cs["up"]["n"] == 2 and abs(cs["up"]["mean"] - (-0.005)) < 1e-12)
    check("daily: dopo calo < -soglia (giorno 2 -> 0.05)", cs["down"]["n"] == 1 and abs(cs["down"]["mean"] - 0.05) < 1e-12)
    eq, days, _ = D.rule_equity(r, 0.015, "contrario", 0.0)
    check("daily: 'contrario' guadagna il +5% del giorno dopo il calo", days == 1 and abs(eq[-1] - 1.05) < 1e-12)
    eq2, _, _ = D.rule_equity(r, 0.015, "contrario", 0.01)
    check("daily: costo ingresso e uscita applicati", abs(eq2[-1] - 1.05 * 0.99 * 0.99) < 1e-12)


def test_forward_cloud(tmp):
    import datetime as dt
    from . import etf_forward as F
    prev = {"etf": {"SPY": True, "QQQ": True}, "crypto": {"BTC": True, "ETH": True}}
    new = {"etf": {"SPY": True, "QQQ": False}, "crypto": {"BTC": True, "ETH": True}}
    check("cloud: rileva il cambio QQQ", F.state_changes(prev, new) == ["ETF QQQ: IN POSIZIONE -> IN CASSA"])
    check("cloud: primo giro senza stato precedente = nessun cambio", F.state_changes(None, new) == [])
    d = os.path.join(tmp, "rep")
    t1, c1 = F.write_cloud_report(d, "R", prev, dt.date(2026, 10, 17))
    check("cloud: primo report scritto, nessun cambio", c1 == [] and os.path.exists(os.path.join(d, "latest.md")) and os.path.exists(os.path.join(d, "history", "2026-10-17.md")))
    t2, c2 = F.write_cloud_report(d, "R", new, dt.date(2026, 10, 18))
    check("cloud: secondo report segnala il cambio nel testo", c2 == ["ETF QQQ: IN POSIZIONE -> IN CASSA"] and "SEGNALE CAMBIATO" in open(os.path.join(d, "latest.md"), encoding="utf-8").read())
    os.environ["UNIBOT_FORWARD_START"] = "2026-10-16"
    try:
        check("cloud: la data di partenza viene dalla variabile d'ambiente", F.load_start(os.path.join(tmp, "x.json")) == "2026-10-16")
        os.environ["UNIBOT_FORWARD_START"] = "boh"
        try:
            F.load_start(os.path.join(tmp, "x.json"))
            check("cloud: data malformata = errore", False)
        except ValueError:
            check("cloud: data malformata = errore", True)
    finally:
        os.environ.pop("UNIBOT_FORWARD_START", None)


def test_sector_lab():
    import numpy as np
    from . import sector_lab as SL
    coins = ["SOL", "ADA", "AVAX", "UNI", "AAVE", "COMP", "BTC"]
    n = 200
    P = np.ones((n, len(coins)))
    for j, c in enumerate(coins):
        P[:, j] = 100 * np.cumprod(np.full(n, 1.0 + (0.002 if c in ("SOL", "ADA", "AVAX") else 0.0005)))
    syms = [c + "USDT" for c in coins]
    names, S, members = SL.sector_matrix(P, syms)
    check("sector: solo settori con almeno 3 monete (L1 grandi e DeFi)", set(names) == {"Blockchain L1 grandi", "DeFi"})
    check("sector: rendimento giornaliero = media delle monete del settore", abs(S[0, names.index("Blockchain L1 grandi")] - 0.002) < 1e-9)
    W = SL.leader_weights(P, S, syms, k=1, lookback=60, every=30)
    check("sector: senza storia = cassa", W[:60].sum() == 0)
    sol, uni = syms.index("SOLUSDT"), syms.index("UNIUSDT")
    check("sector: tiene il settore migliore (L1) a pesi uguali", abs(W[100, sol] - 1 / 3) < 1e-9 and W[100, uni] == 0 and abs(W[100].sum() - 1.0) < 1e-9)
    perfect = np.column_stack([np.full(300, 0.002), np.full(300, 0.001), np.full(300, 0.0)])
    pr = SL.persistence(perfect, lookback=30, horizon=30)
    check("sector: stessa classifica sempre = persistenza 1", abs(pr["mean"] - 1.0) < 1e-9)
    rows = [{"tvl": 100.0 + i} for i in range(500)]
    r = SL.fetch_tvl("Solana", get=lambda url: rows)
    check("sector: variazione TVL a 90 giorni e a 1 anno", abs(r["d90"] - (rows[-1]["tvl"] / rows[-91]["tvl"] - 1)) < 1e-12 and abs(r["y1"] - (rows[-1]["tvl"] / rows[-366]["tvl"] - 1)) < 1e-12)
    check("sector: storia TVL troppo corta = None", SL.fetch_tvl("X", get=lambda url: rows[:100]) is None)


def test_dip_lab():
    import numpy as np
    from . import dip_lab as D
    px = np.concatenate([np.linspace(100, 200, 400), np.linspace(200, 90, 40), np.linspace(90, 190, 60)])
    dd = D.drawdown(px, 365)
    check("dip: nessun valore prima di 365 giorni di storia", not np.isfinite(dd[:364]).any() and np.isfinite(dd[364]))
    check("dip: drawdown = prezzo / massimo - 1", abs(dd[439] - (90.0 / 200.0 - 1)) < 1e-9)
    w = D.dip_weights(px, 0.40, 0.15, 365)
    check("dip: entra quando scende oltre il 40% sotto il massimo", w[:400].sum() == 0 and w[439] == 1.0)
    check("dip: esce quando recupera a meno del 15% sotto il massimo", w[-1] == 0.0 and w[440:].sum() > 0)
    w2 = D.dip_weights(px, 0.40, 0.15, 365)
    tr = D.trades_of(w2, px)
    check("dip: un solo ciclo compra-vendita, in utile", len(tr) == 1 and tr[0][2] > 0)
    pos = D.position_now(px, ["d%d" % i for i in range(len(px))])
    check("dip: posizione attuale coerente (vicino al massimo, percentile alto)", pos["dd365"] > -0.1 and pos["pct4y"] > 0.9)
    res, spans = D.evaluate(px, ["d%d" % i for i in range(len(px))])
    check("dip: acquista e tieni sempre in mercato con 1 operazione", res["acquista e tieni"]["completo"]["n_trades"] == 1)


def test_dashboard_rows():
    import numpy as np
    from . import dashboard as DB
    n = 500
    px = 100 * np.cumprod(np.full(n, 1.001))
    dates = [(__import__("datetime").date(2024, 1, 1) + __import__("datetime").timedelta(days=i)).isoformat() for i in range(n)]
    data = {"BTC": (dates, px), "ETH": (dates, px * 0.5)}
    w = DB.weekly_prices(data)
    check("dash: una riga a settimana per moneta, ordinate", len(w) == 2 * len(range(n - 1, -1, -7)) and w[0]["coin"] == "BTC" and w[0]["date"] < w[1]["date"])
    check("dash: media a 200 giorni assente nei primi 199 giorni", any(r["sma200"] is None for r in w) and w[-1]["sma200"] is not None)
    c = DB.cycle_rows(data)
    check("dash: ciclo_oggi in trend rialzista = IN POSIZIONE", all(r["trend"] == "IN POSIZIONE" for r in c) and abs(c[0]["dd365"]) < 1e-9)
    d = DB.dip_rows(data)
    check("dash: righe compra_basso con id univoci", len({r["id"] for r in d}) == len(d) and {r["period"] for r in d} == {"completo", "1a meta'", "2a meta'"})


def test_sma_scan():
    import numpy as np
    from . import dip_lab as D
    rng = np.random.default_rng(1)
    px = 100 * np.cumprod(1 + rng.normal(0.0, 0.03, 900))
    sc = D.sma_scan(px, ns=(5, 50), costs=(0.0, 0.006))
    check("scan: piu' costo = rendimento minore", sc[5]["total@0.006"] < sc[5]["total@0"] and sc[50]["total@0.006"] < sc[50]["total@0"])
    check("scan: media corta = piu' operazioni all'anno", sc[5]["trades_y"] > sc[50]["trades_y"] > 0)
    flat = D.sma_scan(np.full(400, 100.0), ns=(5,), costs=(0.0, 0.006))
    check("scan: prezzo fermo = nessun segnale e nessun costo", flat[5]["total@0.006"] == 0.0 and flat[5]["trades_y"] == 0.0)
    up = D.sma_scan(100 * np.cumprod(np.full(400, 1.01)), ns=(5,), costs=(0.0, 0.006))
    check("scan: trend costante = una sola entrata", abs(up[5]["trades_y"] - (1 / (400 / 365.0)) / 2.0) < 1e-9)


def main():
    tests_plain = [test_sma_scan, test_dashboard_rows, test_dip_lab, test_sector_lab, test_daily_lab, test_etf_lab, test_exit_rules, test_paper_math, test_crypto_rules, test_stock_rules,
                   test_market_state, test_fmp_budget, test_fmp_block_scope, test_stock_tradable, test_stock_universe,
                   test_signal_lab_eval, test_backtest_replay, test_insider_variants, test_welch_placebo,
                   test_quiver_loader, test_funding_helpers, test_funding_stats, test_funding_fetch_paging]
    tests_tmp = [test_forward_cloud, test_etf_forward, test_engine_crypto_flow, test_engine_panic_block, test_circuit_breaker,
                 test_daily_loss_limit, test_stock_flatten_and_old_positions, test_stock_entry_flow,
                 test_signals, test_persistence, test_signal_loader, test_price_history_cache,
                 test_network_resilience, test_yahoo_history, test_insider_loader, test_trend_lab]
    for t in tests_plain:
        print("==", t.__name__)
        t()
    for t in tests_tmp:
        print("==", t.__name__)
        tmp = tempfile.mkdtemp(prefix="unibot_")
        try:
            t(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("\nRISULTATO:", "TUTTI I TEST PASSATI" if not FAILS else "FALLITI: %s" % FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
