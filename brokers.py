"""Astrazione Broker — interfaccia unica per piu' mercati/asset class.

Obiettivo: rendere l'engine indipendente dal broker, cosi' la stessa strategia
puo' girare su crypto (Coinbase, oggi) E su azioni (Alpaca, domani) senza
riscrivere la logica di trading.

Stato:
- BaseBroker: interfaccia comune (contratto).
- CoinbaseBroker: ADATTATORE sul CoinbaseExecutor esistente (crypto). Pronto.
- AlpacaBroker: scheletro per azioni USA + crypto via API REST Alpaca
  (REST pubblica, paper trading gratuito, azioni a 0 commissioni). Da completare
  e testare prima di operare in reale: vedi NOTE nei metodi.

eToro NON e' incluso di proposito: non espone un'API pubblica retail per
piazzare ordini programmaticamente (solo CopyTrader/Smart Portfolios o API
partner/istituzionale). Per le azioni il percorso e' Alpaca o Interactive Brokers.
"""
import os
import time
import requests


class BaseBroker:
    """Contratto che ogni broker deve implementare per essere usato dall'engine."""
    name = "base"
    asset_class = "generic"

    def get_balance(self, quote="EUR"):
        raise NotImplementedError

    def fetch_tickers(self):
        raise NotImplementedError

    def get_ohlcv(self, symbol, timeframe="5m", limit=100):
        raise NotImplementedError

    def market_buy(self, symbol, amount_quote, ref_price=None):
        """Ritorna fill normalizzato: {filled_base, cost_eur, fee_eur, avg_price} o None."""
        raise NotImplementedError

    def market_sell(self, symbol, amount_base, ref_price=None):
        """Ritorna fill normalizzato: {filled_base, proceeds_eur, fee_eur, avg_price} o None."""
        raise NotImplementedError


class CoinbaseBroker(BaseBroker):
    """Adattatore sul CoinbaseExecutor esistente (crypto, multi-quote EUR/USDC)."""
    name = "coinbase"
    asset_class = "crypto"

    def __init__(self, executor=None):
        if executor is None:
            from coinbase_executor import CoinbaseExecutor
            executor = CoinbaseExecutor()
        self.executor = executor

    def get_balance(self, quote="EUR"):
        bal = self.executor.exchange.fetch_balance()
        return float(bal.get('total', {}).get(quote, 0) or 0)

    def fetch_tickers(self):
        return self.executor.exchange.fetch_tickers()

    def get_ohlcv(self, symbol, timeframe="5m", limit=100):
        return self.executor.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

    def market_buy(self, symbol, amount_quote, ref_price=None):
        return self.executor.execute_market_buy(symbol, amount_quote, ref_price=ref_price)

    def market_sell(self, symbol, amount_base, ref_price=None):
        return self.executor.execute_market_sell(symbol, amount_base, ref_price=ref_price)


class AlpacaBroker(BaseBroker):
    """Broker per AZIONI USA (+crypto) via API REST Alpaca.

    SCHELETRO: implementa account/posizioni/quote in lettura; gli ordini sono
    abbozzati e vanno testati in PAPER prima del reale. Richiede le env:
      ALPACA_API_KEY, ALPACA_API_SECRET, ALPACA_PAPER (true/false).
    """
    name = "alpaca"
    asset_class = "stocks"

    def __init__(self):
        self.key = os.getenv("ALPACA_API_KEY", "")
        self.secret = os.getenv("ALPACA_API_SECRET", "")
        paper = os.getenv("ALPACA_PAPER", "true").strip().lower() in ("1", "true", "yes", "on")
        self.base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        self.data = "https://data.alpaca.markets"

    @property
    def enabled(self):
        return bool(self.key and self.secret)

    def _headers(self):
        return {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}

    def get_account(self):
        r = requests.get(f"{self.base}/v2/account", headers=self._headers(), timeout=15)
        return r.json() if r.status_code == 200 else None

    def get_balance(self, quote="USD"):
        acc = self.get_account()
        # 'cash' = liquidita' disponibile; 'buying_power' include la leva margine
        return float(acc.get("cash", 0)) if acc else 0.0

    def get_positions(self):
        r = requests.get(f"{self.base}/v2/positions", headers=self._headers(), timeout=15)
        return r.json() if r.status_code == 200 else []

    def is_crypto(self, symbol):
        return "/" in symbol  # Alpaca: crypto = "BTC/USD", azioni = "AAPL"

    def get_ohlcv(self, symbol, timeframe="5Min", limit=100):
        """Candele OHLCV normalizzate nel formato ccxt [ts,o,h,l,c,v].
        Stocks: /v2/stocks/{sym}/bars (feed IEX gratuito); crypto: /v1beta3/crypto."""
        try:
            if self.is_crypto(symbol):
                r = requests.get(f"{self.data}/v1beta3/crypto/us/bars",
                                 headers=self._headers(),
                                 params={"symbols": symbol, "timeframe": timeframe, "limit": limit}, timeout=15)
                bars = (r.json().get("bars", {}) or {}).get(symbol, []) if r.status_code == 200 else []
            else:
                r = requests.get(f"{self.data}/v2/stocks/{symbol}/bars",
                                 headers=self._headers(),
                                 params={"timeframe": timeframe, "limit": limit, "feed": "iex"}, timeout=15)
                bars = r.json().get("bars", []) if r.status_code == 200 else []
            return [[b.get("t"), b.get("o"), b.get("h"), b.get("l"), b.get("c"), b.get("v")] for b in bars]
        except Exception:
            return []

    def _poll_order(self, order_id, timeout=20):
        """Gli ordini market Alpaca sono ASINCRONI: la POST torna subito con
        filled_qty=0. Si fa polling finche' lo stato e' 'filled' (o terminale)."""
        for _ in range(timeout):
            try:
                r = requests.get(f"{self.base}/v2/orders/{order_id}", headers=self._headers(), timeout=10)
                if r.status_code == 200:
                    o = r.json()
                    if o.get("status") in ("filled", "canceled", "rejected", "expired"):
                        return o
            except Exception:
                pass
            time.sleep(1)
        return None

    def _submit(self, order):
        r = requests.post(f"{self.base}/v2/orders", headers=self._headers(), json=order, timeout=15)
        if r.status_code not in (200, 201):
            return None
        oid = r.json().get("id")
        return self._poll_order(oid) if oid else None

    def market_buy(self, symbol, amount_quote, ref_price=None):
        tif = "gtc" if self.is_crypto(symbol) else "day"
        o = self._submit({"symbol": symbol, "notional": round(amount_quote, 2),
                          "side": "buy", "type": "market", "time_in_force": tif})
        if not o or o.get("status") != "filled":
            return None
        price = float(o.get("filled_avg_price") or ref_price or 0)
        qty = float(o.get("filled_qty") or 0)
        # azioni USA: 0 commissioni; crypto Alpaca: ~0.15-0.25% (qui stimato a 0, misurato in seguito)
        return {"filled_base": qty, "cost_eur": amount_quote, "fee_eur": 0.0, "avg_price": price}

    def market_sell(self, symbol, amount_base, ref_price=None):
        tif = "gtc" if self.is_crypto(symbol) else "day"
        o = self._submit({"symbol": symbol, "qty": amount_base,
                          "side": "sell", "type": "market", "time_in_force": tif})
        if not o or o.get("status") != "filled":
            return None
        price = float(o.get("filled_avg_price") or ref_price or 0)
        qty = float(o.get("filled_qty") or amount_base)
        return {"filled_base": qty, "proceeds_eur": qty * price, "fee_eur": 0.0, "avg_price": price}

    def ping(self):
        """Self-test: verifica credenziali e ritorna lo stato account."""
        acc = self.get_account()
        if not acc:
            return {"ok": False, "error": "401 o credenziali mancanti"}
        return {"ok": True, "status": acc.get("status"), "cash": acc.get("cash"),
                "buying_power": acc.get("buying_power"), "currency": acc.get("currency")}


def get_broker(name="coinbase", **kwargs):
    """Factory: ritorna l'istanza broker richiesta."""
    name = (name or "coinbase").lower()
    if name == "coinbase":
        return CoinbaseBroker(**kwargs)
    if name == "alpaca":
        return AlpacaBroker()
    raise ValueError(f"Broker non supportato: {name}")
