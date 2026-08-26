# Crypto Arbitrage

Python 3 trading bot that scans for **triangular**, **cross-exchange (inventory)**, and **spot/perp basis** edges. Paper trading is the default. Live trading is triple-locked.

This is a rewrite of the 2017 Python 2 Bittrex bot. Exchange adapters are gone; [CCXT](https://github.com/ccxt/ccxt) provides unified markets, order books, and order placement.

## What changed

The old bot fired three innermost limit orders and hoped they filled. This version:

- Uses **full order-book depth** (VWAP), not just the top level
- Subtracts **taker fees + a slippage buffer** before calling anything an opportunity
- **Binary-searches size** so larger clips that walk the book still clear the profit floor
- Pre-filters triangles from a single `fetch_tickers` call, then hydrates books only for candidates
- Treats cross-exchange arb as **pre-positioned inventory** (no on-trade withdrawals)
- Adds a **cash-and-carry / funding scanner** (execution off unless you enable it)
- Refuses live orders unless `paper: false`, `--live`, and `LIVE_TRADING=1` are all set
- Cancels a sequential live path with a best-effort unwind if a later leg fails

Retail HFT this is not. Fees, latency, and inventory still eat most prints. Use it as a scanner first.

## Strategies

| Mode | Idea |
| --- | --- |
| `triangular` | Same-exchange 3-leg cycle (e.g. USDT → BTC → ETH → USDT). Needs inventory of all three assets if `execution.style` is `parallel`. |
| `cross` | Buy the cheap venue, sell the rich one. Capital stays on both exchanges; rebalance later off-bot. |
| `basis` | Spot vs USDT-perp: rich perp → cash-and-carry signal; also surfaces funding APR. Scan-only by default. |

## Setup

Python **3.11+** (developed on 3.13).

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

API keys are optional for scanning public books. Add them to `.env` only if you will fetch private balances or place live orders.

## Usage

```bash
python -m arbitrage doctor
python -m arbitrage scan
python -m arbitrage scan --strategy triangular --once
python -m arbitrage run
python -m arbitrage run --strategy cross
```

`main.py` is a thin wrapper around the same CLI.

Live trading (real money):

1. Set `paper: false` in `config.yaml`
2. Set `LIVE_TRADING=1` in `.env`
3. Run `python -m arbitrage run --live`

IOC limit orders are used by default (`execution.order_type: ioc`) at the worst book price the simulator consumed.

## Config

See `config.yaml`. Useful knobs:

- `triangular.min_profit_bps` — net edge after fees and the slippage buffer
- `triangular.max_cycles` / `min_24h_volume_usd` — how many triangles to track
- `risk.max_notional_usd` / `max_daily_loss_usd` / `max_trades_per_hour`
- `risk.stale_book_ms` / `max_book_skew_ms` — drop cross-venue prints from mismatched clocks
- `basis.execute` — keep `false` unless you accept futures liquidation risk

Paper fills update `data/paper_state.json`. Logs go to `data/arbitrage.log`.

## Tests

```bash
pytest
```

## Difficulties that still apply

1. **Fees dominate.** A 6–10 bps floor is optimistic on 10 bps taker venues; VIP fees or maker flow change the math.
2. **Latency.** A REST poll cannot beat colocated takers. Opportunities you see are often already gone.
3. **Inventory.** Parallel triangular and spatial arb both need coins on the right books. Transfers are slow and are not done in the hot path.
4. **Partial fills.** Sequential live mode tries to unwind; it can still leave you with residual risk.

## Disclaimer

Educational software. You can lose money, including more than the intended notional when futures are enabled. No warranty.

Original project © 2017 Kelvin Au (MIT).
