#!/usr/bin/env python3
"""Backward-compatible entrypoint: python main.py scan|run|doctor"""

from arbitrage.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
