from dataclasses import dataclass, field, asdict


@dataclass
class Candidate:
    symbol: str
    asset_class: str          # 'crypto' | 'stock'
    quote: str                # EUR | USDC | USD
    price: float
    change_pct: float
    volume_quote: float = 0.0
    score: float = 0.0
    meta: dict = field(default_factory=dict)


@dataclass
class Decision:
    ok: bool
    reasons: list
    score: float = 0.0


@dataclass
class Position:
    symbol: str
    asset_class: str
    quote: str
    entry_price: float
    qty: float
    cost: float               # valuta quote spesa (commissioni incluse)
    entry_fee: float
    highest: float
    opened_ts: float
    opened_day: str = ""      # giorno di apertura (fuso NY per le azioni)
    last_price: float = 0.0
    score: float = 0.0
    regime: str = ""
    signal: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)
