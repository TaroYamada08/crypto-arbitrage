from arbitrage.models import BookLevel, OrderBook


def make_book(symbol: str, bids: list[tuple[float, float]], asks: list[tuple[float, float]], exchange: str = "binance") -> OrderBook:
    return OrderBook(
        exchange=exchange,
        symbol=symbol,
        bids=[BookLevel(price=p, amount=a) for p, a in bids],
        asks=[BookLevel(price=p, amount=a) for p, a in asks],
    )


def deep_book(symbol: str, bid: float, ask: float, size: float = 1_000.0, levels: int = 5, exchange: str = "binance") -> OrderBook:
    bids = [(bid - i * 0.01, size) for i in range(levels)]
    asks = [(ask + i * 0.01, size) for i in range(levels)]
    return make_book(symbol, bids, asks, exchange=exchange)
