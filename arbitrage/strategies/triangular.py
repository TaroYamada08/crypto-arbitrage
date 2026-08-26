from __future__ import annotations

import asyncio

from arbitrage.config import AppConfig
from arbitrage.exchanges import ExchangeHub
from arbitrage.logger import get_logger
from arbitrage.models import Cycle, Opportunity, OrderBook
from arbitrage.strategies.cycles import build_cycles, estimate_from_tickers, size_cycle

log = get_logger()


class TriangularStrategy:
    def __init__(self, cfg: AppConfig, hub: ExchangeHub):
        self.cfg = cfg
        self.hub = hub
        self._cycles: list[Cycle] = []

    async def warmup(self) -> None:
        tcfg = self.cfg.triangular
        markets = await self.hub.load_markets(tcfg.exchange)
        tickers = await self.hub.fetch_spot_tickers(tcfg.exchange)
        volume = {}
        for symbol, ticker in tickers.items():
            quote_vol = ticker.get("quoteVolume")
            last = ticker.get("last") or ticker.get("close")
            base_vol = ticker.get("baseVolume")
            if quote_vol:
                volume[symbol] = float(quote_vol)
            elif last and base_vol:
                volume[symbol] = float(last) * float(base_vol)
            else:
                volume[symbol] = 0.0
        self._cycles = build_cycles(
            markets=markets,
            exchange=tcfg.exchange,
            quote_assets=tcfg.quote_assets,
            start_currencies=tcfg.start_currencies,
            volume_usd=volume,
            min_volume_usd=tcfg.min_24h_volume_usd,
            max_cycles=tcfg.max_cycles,
        )
        log.info("triangular: tracking %s cycles on %s", len(self._cycles), tcfg.exchange)

    async def scan(self, balances: dict[str, float] | None = None) -> list[Opportunity]:
        tcfg = self.cfg.triangular
        if not tcfg.enabled or not self._cycles:
            return []
        exchange = tcfg.exchange
        tickers = await self.hub.fetch_spot_tickers(exchange)
        if not tickers:
            return []
        fees = {leg.symbol: self.hub.taker_fee(exchange, leg.symbol) for cycle in self._cycles for leg in cycle.legs}
        buffer = self.cfg.risk.slippage_buffer_bps
        candidates: list[Cycle] = []
        for cycle in self._cycles:
            est = estimate_from_tickers(cycle, tickers, fees, buffer)
            if est is not None and est >= tcfg.min_profit_bps * 0.5:
                candidates.append(cycle)
        if not candidates:
            return []

        symbols = {leg.symbol for cycle in candidates for leg in cycle.legs}
        books = await self._fetch_books(exchange, symbols)
        opps: list[Opportunity] = []
        for cycle in candidates:
            needed = {leg.symbol for leg in cycle.legs}
            if any(sym not in books for sym in needed):
                continue
            ages = [books[sym].age_ms() for sym in needed]
            if any(age > self.cfg.risk.stale_book_ms for age in ages):
                continue
            opp = size_cycle(
                cycle=cycle,
                books=books,
                fees=fees,
                tickers=tickers,
                min_notional_usd=tcfg.min_notional_usd,
                max_notional_usd=self.cfg.risk.max_notional_usd,
                min_profit_bps=tcfg.min_profit_bps,
                slippage_buffer_bps=buffer,
                min_levels=self.cfg.risk.min_book_levels,
                balances=balances,
                parallel=self.cfg.execution.style == "parallel",
            )
            if opp:
                opps.append(opp)
        opps.sort(key=lambda o: o.net_profit_bps, reverse=True)
        return opps

    async def _fetch_books(self, exchange: str, symbols: set[str]) -> dict[str, OrderBook]:
        sem = asyncio.Semaphore(6)

        async def one(symbol: str) -> tuple[str, OrderBook | None]:
            async with sem:
                book = await self.hub.fetch_order_book(exchange, symbol, self.cfg.execution.book_depth)
                return symbol, book

        pairs = await asyncio.gather(*[one(s) for s in symbols])
        return {symbol: book for symbol, book in pairs if book is not None}
