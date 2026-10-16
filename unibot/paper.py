"""Broker simulato (paper). Applica commissioni e slippage per classe di asset.

I fill simulati sono SEMPRE piu' ottimistici della realta' (niente profondita' di
book, niente rifiuti, niente halt): trattare i risultati come un limite superiore.
"""


class PaperBroker:
    def __init__(self, cfg):
        self.cfg = cfg

    def _slip(self, c_class, price):
        cfg = self.cfg
        if c_class == "crypto":
            return cfg.CRYPTO_SLIPPAGE_BPS / 1e4
        bps = cfg.STOCK_PENNY_SLIPPAGE_BPS if price < cfg.STOCK_PENNY_PRICE else cfg.STOCK_SLIPPAGE_BPS
        return bps / 1e4

    def _fee_rate(self, c_class):
        return self.cfg.CRYPTO_FEE_RATE if c_class == "crypto" else 0.0  # azioni USA: 0 commissioni

    def buy(self, asset_class, price, notional):
        """notional = valuta quote che esce dal wallet. Ritorna qty, prezzo medio, fee."""
        fee = notional * self._fee_rate(asset_class)
        fill_px = price * (1 + self._slip(asset_class, price))
        qty = (notional - fee) / fill_px
        return {"qty": qty, "avg_price": fill_px, "fee": fee, "cost": notional}

    def sell(self, asset_class, price, qty):
        fill_px = price * (1 - self._slip(asset_class, price))
        gross = qty * fill_px
        fee = gross * self._fee_rate(asset_class)
        return {"avg_price": fill_px, "fee": fee, "proceeds": gross - fee}
