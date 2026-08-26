from arbitrage.models import Cycle, Leg
from arbitrage.strategies.cycles import build_cycles, estimate_from_tickers, simulate_cycle, size_cycle
from tests.helpers import deep_book


def _markets():
    def m(base, quote):
        return {"spot": True, "active": True, "base": base, "quote": quote, "type": "spot"}

    return {
        "BTC/USDT": m("BTC", "USDT"),
        "ETH/BTC": m("ETH", "BTC"),
        "ETH/USDT": m("ETH", "USDT"),
    }


def _usdt_btc_eth_cycle() -> Cycle:
    return Cycle(
        start_currency="USDT",
        currencies=("USDT", "BTC", "ETH"),
        exchange="binance",
        legs=[
            Leg("BTC/USDT", "buy", "USDT", "BTC", "binance"),
            Leg("ETH/BTC", "buy", "BTC", "ETH", "binance"),
            Leg("ETH/USDT", "sell", "ETH", "USDT", "binance"),
        ],
    )


def test_build_cycles_finds_triangle():
    volume = {s: 10_000_000 for s in _markets()}
    cycles = build_cycles(_markets(), "binance", ["USDT", "BTC", "ETH"], ["USDT"], volume, 1_000_000, 20)
    routes = {" -> ".join([c.start_currency] + [leg.to_currency for leg in c.legs]) for c in cycles}
    assert any(r.startswith("USDT -> BTC -> ETH") for r in routes)
    assert any(r.startswith("USDT -> ETH -> BTC") for r in routes)


def test_profitable_triangle_after_fees():
    cycle = _usdt_btc_eth_cycle()
    books = {
        "BTC/USDT": deep_book("BTC/USDT", bid=99.5, ask=100.0, size=50),
        "ETH/BTC": deep_book("ETH/BTC", bid=0.49, ask=0.50, size=50),
        "ETH/USDT": deep_book("ETH/USDT", bid=60.0, ask=60.5, size=50),
    }
    fees = {s: 0.001 for s in books}
    tickers = {s: {"last": 1} for s in books}
    tickers["BTC/USDT"] = {"last": 100, "ask": 100, "bid": 99.5}
    opp = simulate_cycle(cycle, books, fees, 100, slippage_buffer_bps=3, tickers=tickers, min_levels=3)
    assert opp is not None
    assert opp.end_amount > 100
    assert opp.net_profit_bps > 1000
    assert len(opp.orders) == 3


def test_unprofitable_triangle_rejected_by_sizer():
    cycle = _usdt_btc_eth_cycle()
    books = {
        "BTC/USDT": deep_book("BTC/USDT", bid=99.9, ask=100.0, size=50),
        "ETH/BTC": deep_book("ETH/BTC", bid=0.01, ask=0.01001, size=50),
        "ETH/USDT": deep_book("ETH/USDT", bid=1.0, ask=1.001, size=50),
    }
    fees = {s: 0.001 for s in books}
    tickers = {"BTC/USDT": {"last": 100, "ask": 100, "bid": 99.9}}
    opp = size_cycle(
        cycle,
        books,
        fees,
        tickers,
        min_notional_usd=15,
        max_notional_usd=100,
        min_profit_bps=6,
        slippage_buffer_bps=3,
        min_levels=3,
    )
    assert opp is None


def test_ticker_estimate_positive_on_synthetic_edge():
    cycle = _usdt_btc_eth_cycle()
    tickers = {
        "BTC/USDT": {"ask": 100, "bid": 99},
        "ETH/BTC": {"ask": 0.5, "bid": 0.49},
        "ETH/USDT": {"ask": 61, "bid": 60},
    }
    fees = {s: 0.001 for s in tickers}
    est = estimate_from_tickers(cycle, tickers, fees, buffer_bps=3)
    assert est is not None and est > 0
