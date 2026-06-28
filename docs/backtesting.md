# Backtesting Assumptions

The MVP backtester is a basis-carry simulation in `src/lib/backtest/backtester.ts`.

## Accounting

- Starts with cash.
- Enters a spot/perp basis position when estimated carry exceeds the entry threshold.
- Exits when edge compresses below the exit threshold or basis mean-reverts materially.
- Charges fee and slippage on both legs.
- Adds funding payments daily while the position is active.
- Computes max drawdown, Sharpe, Sortino, win rate, exposure, turnover, and report assumptions.

## Limitations

- Uses daily sample history.
- Does not model borrow constraints, partial fills, liquidation queues, tax lots, venue downtime, or transfer latency.
- Does not use intraday order-book replay.
- Reported performance is for engineering validation, not investment claims.
