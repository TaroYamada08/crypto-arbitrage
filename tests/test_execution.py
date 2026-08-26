import asyncio
from pathlib import Path

from arbitrage.config import AppConfig
from arbitrage.execution import PaperExecutor
from arbitrage.models import Opportunity, PlannedOrder
from arbitrage.settings import load_app_config


def test_load_default_config():
    cfg = load_app_config("config.yaml")
    assert cfg.paper is True
    assert cfg.triangular.exchange == "binance"
    assert cfg.basis.execute is False


def test_paper_executor_updates_wallet(tmp_path: Path):
    cfg = AppConfig()
    executor = PaperExecutor(cfg, state_path=tmp_path / "paper.json")
    executor.balances = {"binance": {"USDT": 1000, "ETH": 0}}
    opp = Opportunity(
        strategy="cross_exchange",
        venue="binance",
        description="test",
        start_currency="USDT",
        start_amount=300,
        end_amount=303,
        net_profit=3,
        net_profit_bps=100,
        notional_usd=300,
        orders=[
            PlannedOrder("binance", "ETH/USDT", "buy", 0.1, 3000, "USDT", "ETH", 300, 0.1),
        ],
    )
    result = asyncio.run(executor.execute(opp))
    assert result.ok
    assert executor.balances["binance"]["USDT"] == 700
    assert executor.balances["binance"]["ETH"] == 0.1


def test_paper_executor_rejects_shortfall(tmp_path: Path):
    cfg = AppConfig()
    executor = PaperExecutor(cfg, state_path=tmp_path / "paper.json")
    executor.balances = {"binance": {"USDT": 10}}
    opp = Opportunity(
        strategy="triangular",
        venue="binance",
        description="test",
        start_currency="USDT",
        start_amount=100,
        end_amount=101,
        net_profit=1,
        net_profit_bps=10,
        notional_usd=100,
        orders=[
            PlannedOrder("binance", "BTC/USDT", "buy", 0.001, 100000, "USDT", "BTC", 100, 0.001),
        ],
    )
    result = asyncio.run(executor.execute(opp))
    assert not result.ok
    assert executor.balances["binance"]["USDT"] == 10
