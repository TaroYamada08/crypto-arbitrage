from __future__ import annotations

import asyncio
from typing import Any

import ccxt.async_support as ccxt

from arbitrage.config import AppConfig
from arbitrage.logger import get_logger
from arbitrage.models import OrderBook
from arbitrage.orderbook import parse_ccxt_order_book
from arbitrage.settings import credentials_for

log = get_logger()


class ExchangeError(RuntimeError):
    pass


class ExchangeHub:
    """Thin async CCXT wrapper. Public data works without API keys."""

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._clients: dict[str, ccxt.Exchange] = {}
        self._markets_ready: set[str] = set()
        self._lock = asyncio.Lock()

    async def client(self, name: str) -> ccxt.Exchange:
        name = name.lower()
        async with self._lock:
            if name in self._clients:
                return self._clients[name]
            if not hasattr(ccxt, name):
                raise ExchangeError(f"Unknown exchange '{name}' (not in CCXT)")
            spec = self.cfg.exchanges.get(name)
            options: dict[str, Any] = {
                "enableRateLimit": True,
                "timeout": 15000,
                "options": {"defaultType": "spot"},
            }
            options.update(credentials_for(self.cfg, name))
            exchange: ccxt.Exchange = getattr(ccxt, name)(options)
            if spec and spec.sandbox and hasattr(exchange, "set_sandbox_mode"):
                exchange.set_sandbox_mode(True)
            self._clients[name] = exchange
            return exchange

    async def load_markets(self, name: str) -> dict:
        exchange = await self.client(name)
        markets = await exchange.load_markets()
        self._markets_ready.add(name)
        return markets

    def taker_fee(self, name: str, symbol: str, default: float = 0.001) -> float:
        client = self._clients.get(name)
        if not client:
            return default
        market = client.markets.get(symbol) or {}
        fee = market.get("taker")
        if fee is None:
            fee = client.fees.get("trading", {}).get("taker")
        try:
            return float(fee) if fee is not None else default
        except (TypeError, ValueError):
            return default

    def market(self, name: str, symbol: str) -> dict:
        client = self._clients[name]
        if symbol not in client.markets:
            raise ExchangeError(f"{name} has no market {symbol}")
        return client.markets[symbol]

    async def fetch_spot_tickers(self, name: str) -> dict:
        exchange = await self.client(name)
        if name not in self._markets_ready:
            await self.load_markets(name)
        try:
            return await exchange.fetch_tickers()
        except Exception as exc:
            log.warning("fetch_tickers failed on %s: %s", name, exc)
            return {}

    async def fetch_order_book(self, name: str, symbol: str, depth: int) -> OrderBook | None:
        exchange = await self.client(name)
        try:
            raw = await exchange.fetch_order_book(symbol, limit=depth)
            return parse_ccxt_order_book(name, symbol, raw)
        except Exception as exc:
            log.debug("order book %s %s failed: %s", name, symbol, exc)
            return None

    async def fetch_balance_totals(self, name: str) -> dict[str, float]:
        exchange = await self.client(name)
        if not exchange.apiKey:
            return {}
        try:
            bal = await exchange.fetch_balance()
        except Exception as exc:
            log.warning("balance fetch failed on %s: %s", name, exc)
            return {}
        totals = bal.get("total") or {}
        return {k: float(v) for k, v in totals.items() if v}

    async def fetch_ticker(self, name: str, symbol: str, default_type: str | None = None) -> dict | None:
        exchange = await self.client(name)
        params = {}
        if default_type:
            params["type"] = default_type
        elif ":" in symbol:
            params["type"] = "swap"
        try:
            return await exchange.fetch_ticker(symbol, params)
        except Exception as exc:
            log.debug("ticker %s %s failed: %s", name, symbol, exc)
            return None

    async def fetch_funding_rate(self, name: str, symbol: str) -> dict | None:
        exchange = await self.client(name)
        try:
            if exchange.has.get("fetchFundingRate"):
                return await exchange.fetch_funding_rate(symbol)
        except Exception as exc:
            log.debug("funding rate %s %s failed: %s", name, symbol, exc)
        return None

    async def create_order(
        self,
        name: str,
        symbol: str,
        side: str,
        amount: float,
        price: float | None,
        order_type: str,
    ) -> dict:
        exchange = await self.client(name)
        amount = float(exchange.amount_to_precision(symbol, amount))
        params: dict[str, Any] = {}
        ccxt_type = "market"
        ccxt_price = None
        if order_type == "market":
            ccxt_type = "market"
        elif order_type == "ioc":
            ccxt_type = "limit"
            ccxt_price = float(exchange.price_to_precision(symbol, price))
            params["timeInForce"] = "IOC"
        else:
            ccxt_type = "limit"
            ccxt_price = float(exchange.price_to_precision(symbol, price))
        return await exchange.create_order(symbol, ccxt_type, side, amount, ccxt_price, params)

    async def ping(self, name: str) -> str:
        exchange = await self.client(name)
        await self.load_markets(name)
        spot = [s for s, m in exchange.markets.items() if m.get("spot") and m.get("active")]
        keyed = "yes" if exchange.apiKey else "no"
        return f"{name}: {len(spot)} spot markets, api_key={keyed}"

    async def close(self) -> None:
        for client in self._clients.values():
            try:
                await client.close()
            except Exception:
                pass
        self._clients.clear()
        self._markets_ready.clear()
