from __future__ import annotations

from arbitrage.models import BookLevel, Fill, OrderBook

_EPS = 1e-12


def _levels_from_raw(raw: list) -> list[BookLevel]:
    levels: list[BookLevel] = []
    for item in raw:
        if not item or len(item) < 2:
            continue
        price, amount = float(item[0]), float(item[1])
        if price > 0 and amount > 0:
            levels.append(BookLevel(price=price, amount=amount))
    return levels


def parse_ccxt_order_book(exchange: str, symbol: str, raw: dict) -> OrderBook:
    ts = raw.get("timestamp") or 0
    if not ts:
        import time

        ts = time.time() * 1000
    return OrderBook(
        exchange=exchange,
        symbol=symbol,
        bids=_levels_from_raw(raw.get("bids") or []),
        asks=_levels_from_raw(raw.get("asks") or []),
        timestamp_ms=float(ts),
    )


def buy_with_quote(asks: list[BookLevel], quote_amount: float) -> Fill:
    """Spend `quote_amount` taking asks. Returns base received and quote spent."""
    remaining = quote_amount
    base_filled = 0.0
    quote_spent = 0.0
    worst = 0.0
    levels_used = 0
    for level in asks:
        if remaining <= _EPS:
            break
        level_cost = level.price * level.amount
        take_cost = min(remaining, level_cost)
        take_base = take_cost / level.price
        base_filled += take_base
        quote_spent += take_cost
        remaining -= take_cost
        worst = level.price
        levels_used += 1
    fully = remaining <= max(_EPS, quote_amount * 0.005)
    return Fill(
        base_filled=base_filled,
        quote_filled=quote_spent,
        worst_price=worst,
        fully_filled=fully and base_filled > 0,
        levels_used=levels_used,
    )


def sell_base(bids: list[BookLevel], base_amount: float) -> Fill:
    """Sell `base_amount` hitting bids. Returns quote received."""
    remaining = base_amount
    base_sold = 0.0
    quote_got = 0.0
    worst = 0.0
    levels_used = 0
    for level in bids:
        if remaining <= _EPS:
            break
        take = min(remaining, level.amount)
        base_sold += take
        quote_got += take * level.price
        remaining -= take
        worst = level.price
        levels_used += 1
    fully = remaining <= max(_EPS, base_amount * 0.005)
    return Fill(
        base_filled=base_sold,
        quote_filled=quote_got,
        worst_price=worst,
        fully_filled=fully and base_sold > 0,
        levels_used=levels_used,
    )


def apply_taker_fee(amount: float, fee_rate: float) -> float:
    return amount * (1.0 - fee_rate)


def convert_leg(
    side: str,
    book: OrderBook,
    from_amount: float,
    fee_rate: float,
) -> tuple[Fill, float]:
    """
    Convert `from_amount` of the current asset through one market.
    Buy: from = quote, to = base (take asks).
    Sell: from = base, to = quote (hit bids).
    Fee is charged on the asset received (typical taker).
    """
    if side == "buy":
        fill = buy_with_quote(book.asks, from_amount)
        received = apply_taker_fee(fill.base_filled, fee_rate)
    elif side == "sell":
        fill = sell_base(book.bids, from_amount)
        received = apply_taker_fee(fill.quote_filled, fee_rate)
    else:
        raise ValueError(f"Unknown side {side}")
    return fill, received
