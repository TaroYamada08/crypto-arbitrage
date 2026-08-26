from __future__ import annotations

import asyncio

from arbitrage.config import AppConfig, CrossPairConfig
from arbitrage.exchanges import ExchangeHub
from arbitrage.logger import get_logger
from arbitrage.models import Opportunity, OrderBook, PlannedOrder, bps
from arbitrage.orderbook import apply_taker_fee, buy_with_quote, sell_base

log = get_logger()


def simulate_spatial(
    buy_book: OrderBook,
    sell_book: OrderBook,
    buy_ex: str,
    buy_sym: str,
    sell_ex: str,
    sell_sym: str,
    base: str,
    quote: str,
    buy_fee: float,
    sell_fee: float,
    min_notional_usd: float,
    max_notional_usd: float,
    min_profit_bps: float,
    buffer_bps: float,
    quote_balance: float | None = None,
    base_balance: float | None = None,
) -> Opportunity | None:
    mid = buy_book.mid() or sell_book.mid()
    if not mid:
        return None
    hi_base = max_notional_usd / mid
    lo_base = min_notional_usd / mid
    if quote_balance is not None:
        ask = buy_book.best_ask.price if buy_book.best_ask else mid
        if quote_balance <= 0:
            return None
        hi_base = min(hi_base, quote_balance / (ask * (1 + buy_fee)))
    if base_balance is not None:
        if base_balance <= 0:
            return None
        hi_base = min(hi_base, base_balance)
    if hi_base < lo_base:
        return None

    def trial(base_amount: float) -> Opportunity | None:
        quote_needed = 0.0
        remaining = base_amount
        worst_buy = 0.0
        for level in buy_book.asks:
            take = min(remaining, level.amount)
            quote_needed += take * level.price
            remaining -= take
            worst_buy = level.price
            if remaining <= 1e-12:
                break
        if remaining > base_amount * 0.005:
            return None
        buy_fill = buy_with_quote(buy_book.asks, quote_needed)
        base_got = apply_taker_fee(buy_fill.base_filled, buy_fee)
        sell_fill = sell_base(sell_book.bids, base_got)
        if not sell_fill.fully_filled:
            return None
        quote_got = apply_taker_fee(sell_fill.quote_filled, sell_fee)
        quote_got *= 1.0 - buffer_bps / 10_000.0
        net = quote_got - quote_needed
        net_bps = bps(net / quote_needed) if quote_needed else 0.0
        if net_bps < min_profit_bps:
            return None
        notional = quote_needed if quote in {"USDT", "USDC", "USD"} else quote_needed * mid
        return Opportunity(
            strategy="cross_exchange",
            venue=f"{buy_ex}->{sell_ex}",
            description=f"buy {buy_sym}@{buy_ex} / sell {sell_sym}@{sell_ex}",
            start_currency=quote,
            start_amount=quote_needed,
            end_amount=quote_got,
            net_profit=net,
            net_profit_bps=net_bps,
            notional_usd=notional,
            orders=[
                PlannedOrder(
                    exchange=buy_ex,
                    symbol=buy_sym,
                    side="buy",
                    amount=buy_fill.base_filled,
                    price=buy_fill.worst_price or worst_buy,
                    from_currency=quote,
                    to_currency=base,
                    from_amount=quote_needed,
                    to_amount=base_got,
                ),
                PlannedOrder(
                    exchange=sell_ex,
                    symbol=sell_sym,
                    side="sell",
                    amount=sell_fill.base_filled,
                    price=sell_fill.worst_price,
                    from_currency=base,
                    to_currency=quote,
                    from_amount=base_got,
                    to_amount=quote_got,
                ),
            ],
            extra={"base": base, "quote": quote},
        )

    best = trial(lo_base)
    if best is None:
        return None
    lo, hi = lo_base, hi_base
    for _ in range(16):
        mid_amt = (lo + hi) / 2.0
        candidate = trial(mid_amt)
        if candidate:
            best = candidate
            lo = mid_amt
        else:
            hi = mid_amt
    return best


class CrossExchangeStrategy:
    """
    Inventory-based spatial arb: buy on the cheap venue, sell on the expensive one.
    No on-trade withdrawals. Capital is assumed pre-positioned on both books.
    """

    def __init__(self, cfg: AppConfig, hub: ExchangeHub):
        self.cfg = cfg
        self.hub = hub

    async def warmup(self) -> None:
        names = set()
        for pair in self.cfg.cross_exchange.pairs:
            names.add(pair.exchange_a)
            names.add(pair.exchange_b)
        for name in names:
            await self.hub.load_markets(name)
        log.info("cross-exchange: watching %s pairs", len(self.cfg.cross_exchange.pairs))

    async def scan(self, balances: dict[str, dict[str, float]] | None = None) -> list[Opportunity]:
        ccfg = self.cfg.cross_exchange
        if not ccfg.enabled:
            return []
        results: list[Opportunity] = []
        for pair in ccfg.pairs:
            for opp in await self._scan_pair(pair, balances):
                results.append(opp)
        results.sort(key=lambda o: o.net_profit_bps, reverse=True)
        return results

    async def _scan_pair(
        self,
        pair: CrossPairConfig,
        balances: dict[str, dict[str, float]] | None,
    ) -> list[Opportunity]:
        book_a, book_b = await asyncio.gather(
            self.hub.fetch_order_book(pair.exchange_a, pair.symbol_a, self.cfg.execution.book_depth),
            self.hub.fetch_order_book(pair.exchange_b, pair.symbol_b, self.cfg.execution.book_depth),
        )
        if not book_a or not book_b:
            return []
        ages = [book_a.age_ms(), book_b.age_ms()]
        if any(age > self.cfg.risk.stale_book_ms for age in ages):
            return []
        if abs(ages[0] - ages[1]) > self.cfg.risk.max_book_skew_ms:
            return []
        if len(book_a.asks) < self.cfg.risk.min_book_levels or len(book_b.asks) < self.cfg.risk.min_book_levels:
            return []

        fee_a = self.hub.taker_fee(pair.exchange_a, pair.symbol_a)
        fee_b = self.hub.taker_fee(pair.exchange_b, pair.symbol_b)
        buffer = self.cfg.risk.slippage_buffer_bps
        opps = []
        # Buy A (take asks), sell B (hit bids)
        first = self._evaluate(pair, book_a, book_b, fee_a, fee_b, buy_left=True, balances=balances, buffer=buffer)
        # Buy B, sell A
        second = self._evaluate(pair, book_b, book_a, fee_b, fee_a, buy_left=False, balances=balances, buffer=buffer)
        for opp in (first, second):
            if opp and opp.net_profit_bps >= self.cfg.cross_exchange.min_profit_bps:
                opps.append(opp)
        return opps

    def _evaluate(
        self,
        pair: CrossPairConfig,
        buy_book,
        sell_book,
        buy_fee: float,
        sell_fee: float,
        buy_left: bool,
        balances: dict[str, dict[str, float]] | None,
        buffer: float,
    ) -> Opportunity | None:
        if buy_left:
            buy_ex, buy_sym = pair.exchange_a, pair.symbol_a
            sell_ex, sell_sym = pair.exchange_b, pair.symbol_b
        else:
            buy_ex, buy_sym = pair.exchange_b, pair.symbol_b
            sell_ex, sell_sym = pair.exchange_a, pair.symbol_a

        try:
            buy_market = self.hub.market(buy_ex, buy_sym)
            sell_market = self.hub.market(sell_ex, sell_sym)
        except Exception:
            return None
        base = buy_market["base"]
        quote = buy_market["quote"]
        if sell_market["base"] != base:
            return None

        quote_bal = base_bal = None
        if balances:
            quote_bal = balances.get(buy_ex, {}).get(quote, 0.0)
            base_bal = balances.get(sell_ex, {}).get(base, 0.0)

        return simulate_spatial(
            buy_book=buy_book,
            sell_book=sell_book,
            buy_ex=buy_ex,
            buy_sym=buy_sym,
            sell_ex=sell_ex,
            sell_sym=sell_sym,
            base=base,
            quote=quote,
            buy_fee=buy_fee,
            sell_fee=sell_fee,
            min_notional_usd=self.cfg.cross_exchange.min_notional_usd,
            max_notional_usd=self.cfg.risk.max_notional_usd,
            min_profit_bps=self.cfg.cross_exchange.min_profit_bps,
            buffer_bps=buffer,
            quote_balance=quote_bal,
            base_balance=base_bal,
        )
