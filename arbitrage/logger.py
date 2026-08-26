from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

CONSOLE = Console()
ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data"
LOG_FILE = LOG_DIR / "arbitrage.log"


def setup_logging(verbose: bool = False) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    logger = logging.getLogger("arbitrage")
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    rich_handler = RichHandler(console=CONSOLE, show_path=False, rich_tracebacks=True, markup=True)
    rich_handler.setLevel(level)
    logger.addHandler(rich_handler)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)
    return logger


def get_logger() -> logging.Logger:
    logger = logging.getLogger("arbitrage")
    if not logger.handlers:
        return setup_logging()
    return logger
