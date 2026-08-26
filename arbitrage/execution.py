from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from arbitrage.config import AppConfig
from arbitrage.exchanges import ExchangeHub
from arbitrage.logger import get_logger
from arbitrage.models import Opportunity, TradeResult
from arbitrage.settings import ROOT

log = get_logger()

PAPER_STATE = ROOT / "data" / "paper_state.json"

_SEED = {
    "USDT": 5_000.0,
    "USDC": 2_000.0,
    "USD": 2_000.0,
    "BTC": 0.05,
    "ETH": 1.0,
    "BNB": 8.0,
    "SOL": 25.0,
    "XRP": 2_000.0,
    "DOGE": 8_000.0,
    "ADA": 2_000.0,
    "LINK": 50.0,
    "LTC": 10.0,
}


def seed_balances(exchanges: list[str]) -> dict[str, dict[str, float]]:
    return {name: dict(_SEED) for name in exchanges}


class PaperExecutor:
    def __init__(self, cfg: AppConfig, state_path: Path | None = None):
        self.cfg = cfg
        self.state_path = state_path or PAPER_STATE
        self.balances = self._load()

    def _load(self) -> dict[str, dict[str, float]]:
        if self.state_path.exists():
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            return raw.get("balances") or seed_balances(self.cfg.enabled_exchanges())
        return seed_balances(self.cfg.enabled_exchanges())

    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"balances": self.balances}, indent=2), encoding="utf-8")

    def snapshot(self) -> dict[str, dict[str, float]]:
        return deepcopy(self.balances)

    def flat_for(self, exchange: str) -> dict[str, float]:
        return dict(self.balances.get(exchange, {}))

    async def execute(self, opp: Opportunity) -> TradeResult:
        preview = deepcopy(self.balances)
        try:
            for order in opp.orders:
                wallet = preview.setdefault(order.exchange, {})
                have = wallet.get(order.from_currency, 0.0)
                if have + 1e-9 < order.from_amount:
                    raise RuntimeError(
                        f"paper wallet {order.exchange} lacks {order.from_amount:.8f} {order.from_currency} "
                        f"(have {have:.8f})"
                    )
                wallet[order.from_currency] = have - order.from_amount
                wallet[order.to_currency] = wallet.get(order.to_currency, 0.0) + order.to_amount
        except RuntimeError as exc:
            return TradeResult(False, opp, str(exc), paper=True)

        self.balances = preview
        self.save()
        log.info(
            "PAPER FILL %s | %+0.4f %s (%.2f bps)",
            opp.description,
            opp.net_profit,
            opp.start_currency,
            opp.net_profit_bps,
        )
        return TradeResult(True, opp, "paper fill", pnl=opp.net_profit, paper=True)


class LiveExecutor:
    def __init__(self, cfg: AppConfig, hub: ExchangeHub):
        self.cfg = cfg
        self.hub = hub

    async def execute(self, opp: Opportunity) -> TradeResult:
        if self.cfg.execution.style == "sequential":
            return await self._sequential(opp)
        return await self._parallel(opp)

    async def _parallel(self, opp: Opportunity) -> TradeResult:
        fills = []
        errors = []
        for order in opp.orders:
            try:
                raw = await self.hub.create_order(
                    order.exchange,
                    order.symbol,
                    order.side,
                    order.amount,
                    order.price,
                    self.cfg.execution.order_type,
                )
                fills.append(raw)
            except Exception as exc:
                errors.append(f"{order.symbol} {order.side}: {exc}")
                log.error("live order failed: %s", errors[-1])
        if errors:
            return TradeResult(False, opp, "; ".join(errors), fills=fills, paper=False)
        log.info("LIVE FILL %s | %+0.4f %s", opp.description, opp.net_profit, opp.start_currency)
        return TradeResult(True, opp, "live fill", pnl=opp.net_profit, fills=fills, paper=False)

    async def _sequential(self, opp: Opportunity) -> TradeResult:
        fills = []
        for index, order in enumerate(opp.orders):
            try:
                raw = await self.hub.create_order(
                    order.exchange,
                    order.symbol,
                    order.side,
                    order.amount,
                    order.price,
                    self.cfg.execution.order_type,
                )
                fills.append(raw)
            except Exception as exc:
                message = f"leg {index + 1} failed: {exc}"
                log.error(message)
                await self._unwind(opp, fills)
                return TradeResult(False, opp, message, fills=fills, paper=False)
        return TradeResult(True, opp, "live fill", pnl=opp.net_profit, fills=fills, paper=False)

    async def _unwind(self, opp: Opportunity, fills: list[dict]) -> None:
        """Best-effort reverse of filled prefix. Does not guarantee a round trip."""
        for order, _fill in zip(opp.orders, fills):
            reverse = "sell" if order.side == "buy" else "buy"
            try:
                await self.hub.create_order(
                    order.exchange,
                    order.symbol,
                    reverse,
                    order.amount,
                    order.price,
                    "market",
                )
                log.warning("unwound %s %s", reverse, order.symbol)
            except Exception as exc:
                log.error("unwind failed for %s: %s", order.symbol, exc)
