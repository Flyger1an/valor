# Signals

Signals are generated in `src/lib/signals/relative-value.ts` and return a common `RelativeValueSignal` shape:

- `assetPair`
- `venue`
- `direction`
- `confidence`
- `expectedEdgeBps`
- `riskScore`
- `liquidityScore`
- `opportunityScore`
- `explanation`
- `eligibleForPaperTrading`
- `eligibleForLiveTrading`

## Implemented MVP Signals

- Spot/perp basis
- Funding-rate carry
- Cross-exchange premium/discount
- BTC/ETH ratio regime
- ETH/SOL pair spread z-score
- Stablecoin depeg watchlist
- Volatility regime filter

## Edge Is Net of Execution Cost

`expectedEdgeBps` is the edge a taker could realistically capture, not the
headline dislocation. Gross premia are netted against the assumptions in
`src/lib/signals/costs.ts` (per-leg taker fee, plus a transfer/slippage haircut
for cross-venue moves). A cross-exchange premium is still *emitted* whenever the
gross dislocation clears the detection threshold (so it stays visible for
research), but its `expectedEdgeBps`, `opportunityScore`, and paper eligibility
all reflect the net figure. Expect basis/funding edges to read lower than raw
basis + funding, and cross-exchange edges to go negative in calm markets.

## Z-Score History Lineage

The BTC/ETH ratio and ETH/SOL spread z-scores need a price *sample*. In live
mode this is reconstructed from real daily exchange candles (OKX primary,
Binance fallback) in `src/lib/data/price-history.ts`, with the live spot price
appended as the most recent observation. If the candle fetch fails, the engine
falls back to fixture history and records `relativeValueHistorySource:
"fixture"` on the bundle so `DataProvenance` reports it honestly.

## Adding a Signal

1. Add a pure function that accepts normalized `MarketDataBundle` data.
2. Return `RelativeValueSignal` with a plain-English explanation.
3. Include conservative eligibility rules.
4. Add a unit test that covers signal generation and edge cases.
5. Keep live eligibility false until backtesting, paper trading, and manual approvals explicitly support the strategy.
