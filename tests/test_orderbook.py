from arbitrage.orderbook import apply_taker_fee, buy_with_quote, sell_base
from tests.helpers import make_book


def test_buy_with_quote_walks_asks():
    book = make_book("BTC/USDT", bids=[(99, 1)], asks=[(100, 1), (101, 2)])
    fill = buy_with_quote(book.asks, 150)
    assert fill.fully_filled
    assert fill.base_filled == 1 + 50 / 101
    assert fill.quote_filled == 150
    assert fill.worst_price == 101
    assert fill.levels_used == 2


def test_sell_base_walks_bids():
    book = make_book("BTC/USDT", bids=[(99, 1), (98, 2)], asks=[(100, 1)])
    fill = sell_base(book.bids, 1.5)
    assert fill.fully_filled
    assert fill.base_filled == 1.5
    assert fill.quote_filled == 99 + 0.5 * 98
    assert fill.worst_price == 98


def test_insufficient_depth_is_not_fully_filled():
    book = make_book("BTC/USDT", bids=[(99, 0.1)], asks=[(100, 0.1)])
    fill = buy_with_quote(book.asks, 50)
    assert not fill.fully_filled
    assert fill.base_filled == 0.1


def test_taker_fee():
    assert apply_taker_fee(100, 0.001) == 99.9
