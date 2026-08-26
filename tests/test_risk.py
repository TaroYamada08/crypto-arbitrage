from arbitrage.models import Opportunity
from arbitrage.risk import RiskManager


def _opp(notional=100.0, bps=10.0, profit=1.0) -> Opportunity:
    return Opportunity(
        strategy="triangular",
        venue="binance",
        description="test",
        start_currency="USDT",
        start_amount=notional,
        end_amount=notional + profit,
        net_profit=profit,
        net_profit_bps=bps,
        notional_usd=notional,
        orders=[],
    )


def test_risk_allows_good_trade():
    rm = RiskManager(250, 25, 30, 2500, 800)
    assert rm.allow(_opp()).allowed


def test_risk_blocks_oversize():
    rm = RiskManager(250, 25, 30, 2500, 800)
    decision = rm.allow(_opp(notional=500))
    assert not decision.allowed
    assert "notional" in decision.reason


def test_daily_loss_halt():
    rm = RiskManager(250, 5, 30, 2500, 800)
    rm.record_trade(-6)
    assert rm.halted
    assert not rm.allow(_opp()).allowed


def test_stale_and_skewed_books():
    rm = RiskManager(250, 25, 30, stale_book_ms=2500, max_book_skew_ms=800)
    stale = rm.books_fresh([4000])
    assert not stale.allowed
    skew = rm.books_fresh([100, 1200])
    assert not skew.allowed
    ok = rm.books_fresh([100, 200])
    assert ok.allowed
