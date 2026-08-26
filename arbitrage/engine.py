from __future__ import annotations

import asyncio
from time import strftime

from rich.live import Live
from rich.table import Table

from arbitrage.config import AppConfig
from arbitrage.exchanges import ExchangeHub
from arbitrage.execution import LiveExecutor, PaperExecutor
from arbitrage.logger import CONSOLE, get_logger
from arbitrage.models import Opportunity, TradeResult
from arbitrage.risk import RiskManager
from arbitrage.settings import live_env_enabled
from arbitrage.strategies.basis import BasisStrategy
from arbitrage.strategies.cross_exchange import CrossExchangeStrategy
from arbitrage.strategies.triangular import TriangularStrategy

log = get_logger()


def _wanted(name: str, selected: str) -> bool:
    return selected in {"all", name}


class Engine:
    def __init__(self, cfg: AppConfig, live: bool = False, strategy: str = "all"):
        self.cfg = cfg
        self.strategy = strategy
        self.live_requested = live
        self.paper = self._resolve_paper(live)
        self.hub = ExchangeHub(cfg)
        self.risk = RiskManager(
            max_notional_usd=cfg.risk.max_notional_usd,
            max_daily_loss_usd=cfg.risk.max_daily_loss_usd,
            max_trades_per_hour=cfg.risk.max_trades_per_hour,
            stale_book_ms=cfg.risk.stale_book_ms,
            max_book_skew_ms=cfg.risk.max_book_skew_ms,
        )
        self.paper_exec = PaperExecutor(cfg)
        self.live_exec = LiveExecutor(cfg, self.hub)
        self.triangular = TriangularStrategy(cfg, self.hub)
        self.cross = CrossExchangeStrategy(cfg, self.hub)
        self.basis = BasisStrategy(cfg, self.hub)
        self.last_opps: list[Opportunity] = []
        self.last_trade: TradeResult | None = None
        self.cycles = 0
        self._stop = asyncio.Event()

    def _resolve_paper(self, live: bool) -> bool:
        if not live:
            return True
        if self.cfg.paper:
            log.warning("--live ignored because config.yaml has paper: true")
            return True
        if not live_env_enabled():
            log.warning("--live ignored because LIVE_TRADING=1 is not set")
            return True
        return False

    async def warmup(self) -> None:
        if _wanted("triangular", self.strategy) and self.cfg.triangular.enabled:
            await self.triangular.warmup()
        if _wanted("cross", self.strategy) and self.cfg.cross_exchange.enabled:
            await self.cross.warmup()
        if _wanted("basis", self.strategy) and self.cfg.basis.enabled:
            await self.basis.warmup()

    async def scan_once(self) -> list[Opportunity]:
        opps: list[Opportunity] = []
        paper_balances = self.paper_exec.snapshot() if self.paper else None

        if _wanted("triangular", self.strategy) and self.cfg.triangular.enabled:
            tri_bal = None if self.paper else await self.hub.fetch_balance_totals(self.cfg.triangular.exchange)
            if self.paper:
                tri_bal = None  # unconstrained scan; execution still checks the wallet
            opps.extend(await self.triangular.scan(tri_bal))

        if _wanted("cross", self.strategy) and self.cfg.cross_exchange.enabled:
            cross_bal = paper_balances
            if not self.paper:
                cross_bal = {}
                names = set()
                for pair in self.cfg.cross_exchange.pairs:
                    names.add(pair.exchange_a)
                    names.add(pair.exchange_b)
                for name in names:
                    cross_bal[name] = await self.hub.fetch_balance_totals(name)
            opps.extend(await self.cross.scan(cross_bal if not self.paper else None))

        if _wanted("basis", self.strategy) and self.cfg.basis.enabled:
            opps.extend(await self.basis.scan())

        opps.sort(key=lambda o: o.net_profit_bps, reverse=True)
        self.last_opps = opps
        return opps

    def _may_execute(self, opp: Opportunity) -> bool:
        if opp.strategy == "triangular" and not self.cfg.triangular.execute:
            return False
        if opp.strategy == "cross_exchange" and not self.cfg.cross_exchange.execute:
            return False
        if opp.strategy == "basis" and not self.cfg.basis.execute:
            return False
        return True

    async def maybe_execute(self, opps: list[Opportunity], execute: bool) -> TradeResult | None:
        if not execute or not opps:
            return None
        for opp in opps:
            if not self._may_execute(opp):
                continue
            min_bps = {
                "triangular": self.cfg.triangular.min_profit_bps,
                "cross_exchange": self.cfg.cross_exchange.min_profit_bps,
                "basis": self.cfg.basis.min_basis_bps,
            }.get(opp.strategy, 0.0)
            decision = self.risk.allow(opp, min_bps)
            if not decision.allowed:
                log.debug("risk blocked %s: %s", opp.strategy, decision.reason)
                continue
            executor = self.paper_exec if self.paper else self.live_exec
            result = await executor.execute(opp)
            self.last_trade = result
            if result.ok:
                self.risk.record_trade(result.pnl)
            else:
                log.warning("execution failed: %s", result.message)
            return result
        return None

    def render(self) -> Table:
        mode = "PAPER" if self.paper else "LIVE"
        table = Table(
            title=f"crypto-arbitrage  {mode}  cycle={self.cycles}  pnl={self.risk.realized_pnl:.4f}  {strftime('%H:%M:%S')}",
            expand=True,
        )
        table.add_column("strategy", style="cyan", no_wrap=True)
        table.add_column("venue")
        table.add_column("route")
        table.add_column("bps", justify="right")
        table.add_column("profit", justify="right")
        table.add_column("notional", justify="right")
        if not self.last_opps:
            table.add_row("-", "-", "no edge this pass", "-", "-", "-")
            return table
        for opp in self.last_opps[:8]:
            table.add_row(
                opp.strategy,
                opp.venue,
                opp.description[:72],
                f"{opp.net_profit_bps:.2f}",
                f"{opp.net_profit:.6f} {opp.start_currency}",
                f"${opp.notional_usd:.2f}",
            )
        return table

    async def run(self, execute: bool, once: bool = False) -> None:
        await self.warmup()
        interval = max(0.2, self.cfg.execution.poll_interval_ms / 1000.0)
        mode = "PAPER" if self.paper else "[bold red]LIVE[/bold red]"
        CONSOLE.print(f"[bold]Starting engine[/bold] mode={mode} strategy={self.strategy} execute={execute}")
        try:
            with Live(self.render(), console=CONSOLE, refresh_per_second=4) as live:
                while not self._stop.is_set():
                    try:
                        opps = await self.scan_once()
                        self.cycles += 1
                        if opps:
                            best = opps[0]
                            if best.net_profit_bps > 0:
                                log.info(
                                    "best %s %.2f bps on %s",
                                    best.strategy,
                                    best.net_profit_bps,
                                    best.description,
                                )
                        await self.maybe_execute(opps, execute=execute)
                        live.update(self.render())
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        log.exception("scan error: %s", exc)
                    if once:
                        break
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=interval)
                    except asyncio.TimeoutError:
                        pass
        finally:
            await self.hub.close()

    def stop(self) -> None:
        self._stop.set()
