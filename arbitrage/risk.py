from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from time import time

from arbitrage.models import Opportunity


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""


@dataclass
class RiskManager:
    max_notional_usd: float
    max_daily_loss_usd: float
    max_trades_per_hour: int
    stale_book_ms: int
    max_book_skew_ms: int
    min_profit_bps: float = 0.0
    realized_pnl: float = 0.0
    _trade_times: deque[float] = field(default_factory=deque)
    halted: bool = False
    halt_reason: str = ""

    def halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason

    def record_trade(self, pnl: float) -> None:
        self.realized_pnl += pnl
        self._trade_times.append(time())
        self._trim()
        if self.realized_pnl <= -abs(self.max_daily_loss_usd):
            self.halt(f"daily loss limit hit ({self.realized_pnl:.4f} USD)")

    def _trim(self) -> None:
        cutoff = time() - 3600
        while self._trade_times and self._trade_times[0] < cutoff:
            self._trade_times.popleft()

    def allow(self, opp: Opportunity, min_profit_bps: float | None = None) -> RiskDecision:
        if self.halted:
            return RiskDecision(False, self.halt_reason)
        threshold = self.min_profit_bps if min_profit_bps is None else min_profit_bps
        if opp.notional_usd > self.max_notional_usd + 1e-9:
            return RiskDecision(False, f"notional {opp.notional_usd:.2f} exceeds cap {self.max_notional_usd:.2f}")
        if opp.net_profit_bps < threshold:
            return RiskDecision(False, f"profit {opp.net_profit_bps:.2f} bps below {threshold:.2f}")
        if opp.start_amount <= 0 or opp.end_amount <= 0:
            return RiskDecision(False, "non-positive amounts")
        self._trim()
        if len(self._trade_times) >= self.max_trades_per_hour:
            return RiskDecision(False, "hourly trade cap reached")
        return RiskDecision(True)

    def books_fresh(self, ages_ms: list[float]) -> RiskDecision:
        if any(age > self.stale_book_ms for age in ages_ms):
            return RiskDecision(False, f"stale book (ages_ms={['%.0f' % a for a in ages_ms]})")
        if len(ages_ms) >= 2 and (max(ages_ms) - min(ages_ms)) > self.max_book_skew_ms:
            return RiskDecision(False, "book timestamps too skewed")
        return RiskDecision(True)
