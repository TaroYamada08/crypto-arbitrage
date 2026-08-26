from __future__ import annotations

from arbitrage.config import AppConfig
from arbitrage.exchanges import ExchangeHub
from arbitrage.logger import get_logger
from arbitrage.models import Opportunity, PlannedOrder, bps

log = get_logger()


def _swap_symbol(spot: str) -> str:
    if ":" in spot:
        return spot
    return f"{spot}:USDT"


def carry_nets(
    spot_px: float,
    perp_px: float,
    funding_rate: float,
    spot_fee: float,
    perp_fee: float,
    buffer_bps: float,
    funding_days: float = 1.0,
) -> dict[str, float]:
    """
    Cash-and-carry = long spot, short perp.
    Reverse = short spot, long perp.
    Funding rate > 0 means longs pay shorts (shorts receive).
    """
    basis_bps = bps((perp_px - spot_px) / spot_px)
    fee_bps = (spot_fee + perp_fee) * 10_000.0 + buffer_bps
    short_funding_bps = funding_rate * 3.0 * funding_days * 10_000.0
    carry_net = basis_bps + short_funding_bps - fee_bps
    reverse_net = -basis_bps - short_funding_bps - fee_bps
    return {
        "basis_bps": basis_bps,
        "fee_bps": fee_bps,
        "funding_apr_pct": funding_rate * 3.0 * 365.0 * 100.0,
        "carry_net_bps": carry_net,
        "reverse_net_bps": reverse_net,
    }


class BasisStrategy:
    """
    Cash-and-carry scanner: long spot, short perp when perp trades rich,
    or the reverse when perp trades cheap. Execution is off by default.
    """

    def __init__(self, cfg: AppConfig, hub: ExchangeHub):
        self.cfg = cfg
        self.hub = hub

    async def warmup(self) -> None:
        if self.cfg.basis.enabled:
            await self.hub.load_markets(self.cfg.basis.exchange)
            log.info("basis: watching %s on %s", self.cfg.basis.symbols, self.cfg.basis.exchange)

    async def scan(self) -> list[Opportunity]:
        bcfg = self.cfg.basis
        if not bcfg.enabled:
            return []
        opps: list[Opportunity] = []
        for spot_symbol in bcfg.symbols:
            opp = await self._scan_symbol(spot_symbol)
            if opp:
                opps.append(opp)
        opps.sort(key=lambda o: o.net_profit_bps, reverse=True)
        return opps

    async def _scan_symbol(self, spot_symbol: str) -> Opportunity | None:
        name = self.cfg.basis.exchange
        swap_symbol = _swap_symbol(spot_symbol)
        spot = await self.hub.fetch_ticker(name, spot_symbol)
        perp = await self.hub.fetch_ticker(name, swap_symbol)
        if not spot or not perp:
            return None
        spot_px = float(spot.get("last") or spot.get("close") or 0)
        perp_px = float(perp.get("last") or perp.get("close") or 0)
        if spot_px <= 0 or perp_px <= 0:
            return None
        funding = await self.hub.fetch_funding_rate(name, swap_symbol)
        funding_rate = float((funding or {}).get("fundingRate") or 0)
        metrics = carry_nets(
            spot_px,
            perp_px,
            funding_rate,
            self.hub.taker_fee(name, spot_symbol),
            self.hub.taker_fee(name, swap_symbol),
            self.cfg.risk.slippage_buffer_bps,
        )
        carry = metrics["carry_net_bps"]
        reverse = metrics["reverse_net_bps"]
        min_bps = self.cfg.basis.min_basis_bps
        if carry >= min_bps and carry >= reverse:
            direction = "cash_and_carry"
            net_bps = carry
        elif reverse >= min_bps:
            direction = "reverse_cash_and_carry"
            net_bps = reverse
        else:
            return None

        notional = min(self.cfg.risk.max_notional_usd, 250)
        amount = notional / spot_px
        desc = (
            f"{direction} {spot_symbol} spot={spot_px:.4f} perp={perp_px:.4f} "
            f"basis={metrics['basis_bps']:.1f}bps funding_apr={metrics['funding_apr_pct']:.1f}%"
        )
        side_spot = "buy" if direction == "cash_and_carry" else "sell"
        side_perp = "sell" if side_spot == "buy" else "buy"
        quote = spot_symbol.split("/")[1] if "/" in spot_symbol else "USDT"
        base = spot_symbol.split("/")[0]
        return Opportunity(
            strategy="basis",
            venue=name,
            description=desc,
            start_currency=quote,
            start_amount=notional,
            end_amount=notional * (1 + net_bps / 10_000.0),
            net_profit=notional * net_bps / 10_000.0,
            net_profit_bps=net_bps,
            notional_usd=notional,
            orders=[
                PlannedOrder(
                    exchange=name,
                    symbol=spot_symbol,
                    side=side_spot,
                    amount=amount,
                    price=spot_px,
                    from_currency=quote,
                    to_currency=base,
                    from_amount=notional,
                    to_amount=amount,
                ),
                PlannedOrder(
                    exchange=name,
                    symbol=swap_symbol,
                    side=side_perp,
                    amount=amount,
                    price=perp_px,
                    from_currency=base,
                    to_currency=quote,
                    from_amount=amount,
                    to_amount=notional,
                ),
            ],
            extra={**metrics, "direction": direction},
        )
