"""Regole di ingresso e uscita. Funzioni pure: stesso codice per il bot live
(paper) e per il backtest, cosi' quello che si testa e' quello che gira.
"""
import math
from .indicators import sma, rsi_last, macd_bullish
from .models import Decision


def crypto_entry(c, closes, regime, cfg):
    """Decide se entrare long su una crypto. `closes` = ultime chiusure (piu' recente per ultima)."""
    if regime.get("block"):
        return Decision(False, ["regime:" + str(regime.get("label", "block"))])
    if not (cfg.CRYPTO_MIN_PCT <= c.change_pct <= cfg.CRYPTO_MAX_PCT):
        return Decision(False, ["pct_fuori_range"])
    if c.volume_quote < cfg.CRYPTO_MIN_QUOTE_VOLUME:
        return Decision(False, ["volume_basso"])
    if not closes or len(closes) < 40:
        return Decision(False, ["storico_insufficiente"])

    ma = sma(closes, 10)
    if ma is None or c.price <= ma:
        return Decision(False, ["sotto_sma"])
    if (c.price / ma - 1) > cfg.CRYPTO_MAX_EXTENSION:
        return Decision(False, ["troppo_esteso"])
    rsi = rsi_last(closes)
    if rsi is None or rsi >= 70:
        return Decision(False, ["rsi_alto"])
    macd = macd_bullish(closes)
    if not macd:
        return Decision(False, ["macd_non_rialzista"])

    score = (0.5 * min(c.change_pct / 10.0, 1.0)
             + 0.3 * (1.0 - rsi / 100.0)
             + 0.2 * min(math.log10(max(c.volume_quote, 1.0)) / 7.0, 1.0))
    return Decision(True, ["ok"], round(score, 4))


def stock_entry(c, tape, ctx, cfg):
    """Ingresso su un'azione. `tape` = prezzi campionati dal bot (piu' recente per ultimo).
    Il piano FMP gratuito non ha candele intraday: il bot costruisce il proprio 'tape'."""
    if not ctx.get("can_enter"):
        return Decision(False, ["finestra:" + ",".join(ctx.get("reasons", ["chiusa"]))])
    if not (cfg.STOCK_MIN_PCT <= c.change_pct <= cfg.STOCK_MAX_PCT):
        return Decision(False, ["pct_fuori_range"])
    if c.price < cfg.STOCK_MIN_PRICE:
        return Decision(False, ["prezzo_basso"])
    n = cfg.STOCK_TAPE_MIN_SAMPLES
    if not tape or len(tape) < n:
        return Decision(False, ["tape_insufficiente"])
    ma = sum(tape[-n:]) / n
    if c.price <= ma:
        return Decision(False, ["sotto_media_tape"])
    if len(tape) >= 2 and tape[-1] < tape[-2]:
        return Decision(False, ["tape_in_calo"])
    score = 0.6 * min(c.change_pct / 15.0, 1.0) + 0.4 * min((c.price / ma - 1) * 20, 1.0)
    return Decision(True, ["ok"], round(score, 4))


def exit_reason(entry_price, highest, price, held_seconds, trail_pct, max_hold_seconds,
                sma_val=None, breakeven=0.0, flatten=False):
    """Motivo di uscita (stringa) oppure None. Ordine: flatten, trailing, tempo, trend."""
    if flatten:
        return "EOD FLATTEN"
    hi = max(highest, price)
    if price <= hi * (1 - trail_pct):
        return "STOP LOSS" if hi <= entry_price else "TRAILING STOP"
    if max_hold_seconds and held_seconds >= max_hold_seconds:
        return "TIME STOP"
    pnl = (price - entry_price) / entry_price
    if sma_val is not None and price < sma_val and pnl > breakeven:
        return "TREND REVERSAL"
    return None
