from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ExchangeConfig(BaseModel):
    enabled: bool = True
    sandbox: bool = False
    api_key_env: str | None = None
    secret_env: str | None = None
    password_env: str | None = None


class RiskConfig(BaseModel):
    max_notional_usd: float = 250
    max_daily_loss_usd: float = 25
    max_trades_per_hour: int = 30
    stale_book_ms: int = 2500
    max_book_skew_ms: int = 800
    min_book_levels: int = 3
    slippage_buffer_bps: float = 3.0

    @field_validator(
        "max_notional_usd",
        "max_daily_loss_usd",
        "stale_book_ms",
        "max_book_skew_ms",
        "min_book_levels",
        "slippage_buffer_bps",
    )
    @classmethod
    def _positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("must be positive")
        return value


class ExecutionConfig(BaseModel):
    style: str = "parallel"
    order_type: str = "ioc"
    poll_interval_ms: int = 1500
    book_depth: int = 20

    @field_validator("style")
    @classmethod
    def _style(cls, value: str) -> str:
        value = value.lower()
        if value not in {"parallel", "sequential"}:
            raise ValueError("style must be parallel or sequential")
        return value

    @field_validator("order_type")
    @classmethod
    def _order_type(cls, value: str) -> str:
        value = value.lower()
        if value not in {"ioc", "limit", "market"}:
            raise ValueError("order_type must be ioc, limit, or market")
        return value


class TriangularConfig(BaseModel):
    enabled: bool = True
    execute: bool = True
    exchange: str = "binance"
    start_currencies: list[str] = Field(default_factory=lambda: ["USDT", "USDC", "BTC"])
    quote_assets: list[str] = Field(default_factory=lambda: ["USDT", "USDC", "BTC", "ETH"])
    max_cycles: int = 60
    min_24h_volume_usd: float = 2_000_000
    min_profit_bps: float = 6.0
    min_notional_usd: float = 15


class CrossPairConfig(BaseModel):
    exchange_a: str
    symbol_a: str
    exchange_b: str
    symbol_b: str


class CrossExchangeConfig(BaseModel):
    enabled: bool = True
    execute: bool = True
    pairs: list[CrossPairConfig] = Field(default_factory=list)
    min_profit_bps: float = 8.0
    min_notional_usd: float = 20
    rebalance_threshold: float = 0.35


class BasisConfig(BaseModel):
    enabled: bool = True
    execute: bool = False
    exchange: str = "binance"
    symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    min_basis_bps: float = 12.0
    min_funding_apr_pct: float = 8.0


class AppConfig(BaseModel):
    paper: bool = True
    exchanges: dict[str, ExchangeConfig] = Field(default_factory=dict)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    triangular: TriangularConfig = Field(default_factory=TriangularConfig)
    cross_exchange: CrossExchangeConfig = Field(default_factory=CrossExchangeConfig)
    basis: BasisConfig = Field(default_factory=BasisConfig)

    def enabled_exchanges(self) -> list[str]:
        return [name for name, cfg in self.exchanges.items() if cfg.enabled]
