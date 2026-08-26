from arbitrage.strategies.cross_exchange import simulate_spatial
from tests.helpers import deep_book


def test_spatial_arb_buy_cheap_sell_rich():
    buy = deep_book("ETH/USDT", bid=2990, ask=3000, size=10, exchange="binance")
    sell = deep_book("ETH/USDT", bid=3030, ask=3040, size=10, exchange="kraken")
    opp = simulate_spatial(
        buy_book=buy,
        sell_book=sell,
        buy_ex="binance",
        buy_sym="ETH/USDT",
        sell_ex="kraken",
        sell_sym="ETH/USDT",
        base="ETH",
        quote="USDT",
        buy_fee=0.001,
        sell_fee=0.001,
        min_notional_usd=20,
        max_notional_usd=250,
        min_profit_bps=8,
        buffer_bps=3,
    )
    assert opp is not None
    assert opp.net_profit > 0
    assert opp.orders[0].side == "buy"
    assert opp.orders[1].side == "sell"
    assert opp.orders[0].exchange == "binance"


def test_spatial_arb_absent_when_books_aligned():
    buy = deep_book("ETH/USDT", bid=2999, ask=3000, size=10, exchange="binance")
    sell = deep_book("ETH/USDT", bid=2998, ask=2999, size=10, exchange="kraken")
    opp = simulate_spatial(
        buy_book=buy,
        sell_book=sell,
        buy_ex="binance",
        buy_sym="ETH/USDT",
        sell_ex="kraken",
        sell_sym="ETH/USDT",
        base="ETH",
        quote="USDT",
        buy_fee=0.001,
        sell_fee=0.001,
        min_notional_usd=20,
        max_notional_usd=250,
        min_profit_bps=8,
        buffer_bps=3,
    )
    assert opp is None
