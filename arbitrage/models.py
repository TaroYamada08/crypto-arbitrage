from __future__ import annotations

from dataclasses import dataclass, field
from time import time


def bps(value: float) -> float:
    return value * 10_000.0


def from_bps(value: float) -> float:
    return value / 10_000.0


@dataclass(frozen=True)
class BookLevel:
    price: float
    amount: float


@dataclass
class OrderBook:
    exchange: str
    symbol: str
    bids: list[BookLevel]
    asks: list[BookLevel]
    timestamp_ms: float = field(default_factory=lambda: time() * 1000)

    @property
    def best_bid(self) -> BookLevel | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> BookLevel | None:
        return self.asks[0] if self.asks else None

    def mid(self) -> float | None:
        if not self.best_bid or not self.best_ask:
            return None
        return (self.best_bid.price + self.best_ask.price) / 2.0

    def age_ms(self, now_ms: float | None = None) -> float:
        now = time() * 1000 if now_ms is None else now_ms
        return now - self.timestamp_ms


@dataclass(frozen=True)
class Leg:
    symbol: str
    side: str
    from_currency: str
    to_currency: str
    exchange: str = ""


@dataclass
class Cycle:
    start_currency: str
    currencies: tuple[str, str, str]
    legs: list[Leg]
    exchange: str = ""

    @property
    def label(self) -> str:
        route = " -> ".join([self.start_currency, self.legs[0].to_currency, self.legs[1].to_currency, self.start_currency])
        return f"{self.exchange} {route}"


@dataclass
class Fill:
    base_filled: float
    quote_filled: float
    worst_price: float
    fully_filled: bool
    levels_used: int


@dataclass
class PlannedOrder:
    exchange: str
    symbol: str
    side: str
    amount: float
    price: float
    from_currency: str
    to_currency: str
    from_amount: float
    to_amount: float


@dataclass
class Opportunity:
    strategy: str
    venue: str
    description: str
    start_currency: str
    start_amount: float
    end_amount: float
    net_profit: float
    net_profit_bps: float
    notional_usd: float
    orders: list[PlannedOrder]
    fee_paid: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def profitable(self) -> bool:
        return self.net_profit > 0 and self.net_profit_bps > 0


@dataclass
class TradeResult:
    ok: bool
    opportunity: Opportunity
    message: str
    pnl: float = 0.0
    paper: bool = True
    fills: list[dict] = field(default_factory=list)
