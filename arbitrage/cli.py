from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from arbitrage import __version__
from arbitrage.engine import Engine
from arbitrage.exchanges import ExchangeHub
from arbitrage.logger import CONSOLE, setup_logging
from arbitrage.settings import live_env_enabled, load_app_config


def _windows_loop_policy() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-c", "--config", default="config.yaml", help="Path to YAML config")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Attempt live trading (also requires paper: false and LIVE_TRADING=1)",
    )
    parser.add_argument(
        "--strategy",
        choices=["all", "triangular", "cross", "basis"],
        default="all",
    )
    parser.add_argument("--once", action="store_true", help="Single scan pass then exit")


def build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    _common_flags(shared)
    parser = argparse.ArgumentParser(
        prog="arbitrage",
        description="Modern crypto arbitrage scanner (triangular, cross-exchange, basis).",
        parents=[shared],
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("scan", parents=[shared], help="Detect opportunities only (default)")
    sub.add_parser("run", parents=[shared], help="Detect and execute (paper unless live locks are open)")
    sub.add_parser("doctor", parents=[shared], help="Check config, Python, and exchange connectivity")
    return parser


async def _doctor(config_path: str) -> int:
    cfg = load_app_config(config_path)
    CONSOLE.print(f"[bold]crypto-arbitrage[/bold] v{__version__}")
    CONSOLE.print(f"Python {sys.version.split()[0]}")
    CONSOLE.print(f"paper={cfg.paper}  LIVE_TRADING={live_env_enabled()}")
    CONSOLE.print(f"enabled exchanges: {', '.join(cfg.enabled_exchanges()) or '(none)'}")
    hub = ExchangeHub(cfg)
    try:
        for name in cfg.enabled_exchanges():
            try:
                line = await hub.ping(name)
                CONSOLE.print(f"[green]ok[/green] {line}")
            except Exception as exc:
                CONSOLE.print(f"[red]fail[/red] {name}: {exc}")
                return 1
    finally:
        await hub.close()
    CONSOLE.print("[green]doctor passed[/green]")
    return 0


async def _run(args: argparse.Namespace) -> int:
    cfg = load_app_config(args.config)
    execute = args.command == "run"
    engine = Engine(cfg, live=args.live, strategy=args.strategy)
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGINT", "SIGTERM"):
        try:
            import signal

            loop.add_signal_handler(getattr(signal, sig_name), engine.stop)
        except (NotImplementedError, RuntimeError):
            pass
    await engine.run(execute=execute, once=args.once)
    return 0


def main(argv: list[str] | None = None) -> int:
    _windows_loop_policy()
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(verbose=args.verbose)
    if not args.command:
        args.command = "scan"
    if args.live and args.command != "run":
        CONSOLE.print("[yellow]--live only applies to the run command[/yellow]")
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path).resolve()
    os.chdir(Path(__file__).resolve().parent.parent)
    try:
        if args.command == "doctor":
            return asyncio.run(_doctor(str(config_path)))
        args.config = str(config_path)
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        CONSOLE.print("\nstopped")
        return 0
    except FileNotFoundError as exc:
        CONSOLE.print(f"[red]{exc}[/red]")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
