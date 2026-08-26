from __future__ import annotations

from collections import defaultdict

from arbitrage.models import Cycle, Leg, Opportunity, OrderBook, PlannedOrder, bps
from arbitrage.orderbook import convert_leg


def build_cycles(
    markets: dict,
    exchange: str,
    quote_assets: list[str],
    start_currencies: list[str],
    volume_usd: dict[str, float],
    min_volume_usd: float,
    max_cycles: int,
) -> list[Cycle]:
    graph: dict[str, list[tuple[str, Leg]]] = defaultdict(list)
    quotes = set(quote_assets)

    for symbol, market in markets.items():
        if not market.get("spot") or not market.get("active"):
            continue
        if market.get("type") and market["type"] not in ("spot",):
            continue
        base = market.get("base")
        quote = market.get("quote")
        if not base or not quote:
            continue
        if volume_usd.get(symbol, 0) < min_volume_usd:
            continue
        if quote not in quotes and base not in quotes:
            continue
        graph[quote].append((base, Leg(symbol, "buy", quote, base, exchange)))
        graph[base].append((quote, Leg(symbol, "sell", base, quote, exchange)))

    preferred = list(start_currencies)
    cycles: list[Cycle] = []
    seen: set[tuple] = set()

    starts = [c for c in preferred if c in graph] or list(graph.keys())[:8]
    for start in starts:
        for mid1, leg1 in graph.get(start, []):
            if mid1 == start:
                continue
            for mid2, leg2 in graph.get(mid1, []):
                if mid2 in (start, mid1):
                    continue
                for end, leg3 in graph.get(mid2, []):
                    if end != start:
                        continue
                    key = (leg1.symbol, leg1.side, leg2.symbol, leg2.side, leg3.symbol, leg3.side)
                    if key in seen:
                        continue
                    seen.add(key)
                    cycles.append(
                        Cycle(
                            start_currency=start,
                            currencies=(start, mid1, mid2),
                            legs=[leg1, leg2, leg3],
                            exchange=exchange,
                        )
                    )

    def score(cycle: Cycle) -> float:
        vols = [volume_usd.get(leg.symbol, 0.0) for leg in cycle.legs]
        return min(vols) if vols else 0.0

    cycles.sort(key=score, reverse=True)
    return cycles[:max_cycles]


def estimate_from_tickers(cycle: Cycle, tickers: dict, fees: dict[str, float], buffer_bps: float) -> float | None:
    """Top-of-book product using ticker bid/ask. Returns net bps or None."""
    qty = 1.0
    for leg in cycle.legs:
        ticker = tickers.get(leg.symbol) or {}
        fee = fees.get(leg.symbol, 0.001)
        if leg.side == "buy":
            ask = ticker.get("ask")
            if not ask:
                return None
            qty = (qty / float(ask)) * (1.0 - fee)
        else:
            bid = ticker.get("bid")
            if not bid:
                return None
            qty = (qty * float(bid)) * (1.0 - fee)
    return bps(qty - 1.0) - buffer_bps


def usd_price(currency: str, tickers: dict) -> float | None:
    if currency in {"USDT", "USDC", "BUSD", "USD", "DAI"}:
        return 1.0
    for symbol in (f"{currency}/USDT", f"{currency}/USD", f"{currency}/USDC"):
        ticker = tickers.get(symbol) or {}
        last = ticker.get("last") or ticker.get("close") or ticker.get("bid")
        if last:
            return float(last)
    return None


def simulate_cycle(
    cycle: Cycle,
    books: dict[str, OrderBook],
    fees: dict[str, float],
    start_amount: float,
    slippage_buffer_bps: float,
    tickers: dict,
    min_levels: int,
) -> Opportunity | None:
    qty = start_amount
    fee_paid = 0.0
    orders: list[PlannedOrder] = []

    for leg in cycle.legs:
        book = books.get(leg.symbol)
        if book is None or len(book.asks) < min_levels or len(book.bids) < min_levels:
            return None
        fee = fees.get(leg.symbol, 0.001)
        fill, received = convert_leg(leg.side, book, qty, fee)
        if not fill.fully_filled or received <= 0:
            return None
        if leg.side == "buy":
            fee_paid += fill.base_filled * fee * (book.mid() or fill.worst_price)
            base_amount = fill.base_filled
        else:
            fee_paid += fill.quote_filled * fee
            base_amount = fill.base_filled
        orders.append(
            PlannedOrder(
                exchange=cycle.exchange,
                symbol=leg.symbol,
                side=leg.side,
                amount=base_amount,
                price=fill.worst_price,
                from_currency=leg.from_currency,
                to_currency=leg.to_currency,
                from_amount=qty,
                to_amount=received,
            )
        )
        qty = received

    end_amount = qty
    px = usd_price(cycle.start_currency, tickers) or 0.0
    notional = start_amount * px if px else 0.0
    gross = end_amount - start_amount
    buffered = end_amount * (1.0 - slippage_buffer_bps / 10_000.0)
    net = buffered - start_amount
    net_bps = bps(net / start_amount) if start_amount else 0.0
    return Opportunity(
        strategy="triangular",
        venue=cycle.exchange,
        description=cycle.label,
        start_currency=cycle.start_currency,
        start_amount=start_amount,
        end_amount=end_amount,
        net_profit=net,
        net_profit_bps=net_bps,
        notional_usd=notional,
        orders=orders,
        fee_paid=fee_paid,
        extra={"gross": gross, "buffered_end": buffered},
    )


def size_cycle(
    cycle: Cycle,
    books: dict[str, OrderBook],
    fees: dict[str, float],
    tickers: dict,
    min_notional_usd: float,
    max_notional_usd: float,
    min_profit_bps: float,
    slippage_buffer_bps: float,
    min_levels: int,
    balances: dict[str, float] | None = None,
    parallel: bool = True,
) -> Opportunity | None:
    px = usd_price(cycle.start_currency, tickers)
    if not px:
        return None
    lo = min_notional_usd / px
    hi = max_notional_usd / px
    if balances:
        held = float(balances.get(cycle.start_currency, 0.0))
        if held <= 0:
            return None
        hi = min(hi, held)
    if hi < lo:
        return None

    def trial(amount: float) -> Opportunity | None:
        opp = simulate_cycle(cycle, books, fees, amount, slippage_buffer_bps, tickers, min_levels)
        if opp is None:
            return None
        if parallel and balances:
            for order in opp.orders:
                if balances.get(order.from_currency, 0.0) < order.from_amount * 0.999:
                    return None
        if opp.net_profit_bps < min_profit_bps:
            return None
        return opp

    best = trial(lo)
    if best is None:
        return None
    for _ in range(18):
        mid = (lo + hi) / 2.0
        candidate = trial(mid)
        if candidate:
            best = candidate
            lo = mid
        else:
            hi = mid
    return best
