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
- `spreadStationary` and `adfTestStatistic` on mean-reversion signals

## Implemented MVP Signals

- Spot/perp basis
- Funding-rate carry
- Cross-exchange premium/discount
- BTC/ETH ratio regime, gated by an ADF stationarity check before paper eligibility
- ETH/SOL pair spread z-score, gated by an ADF stationarity check before paper eligibility
- Stablecoin depeg watchlist
- Volatility regime filter

## Adding a Signal

1. Add a pure function that accepts normalized `MarketDataBundle` data.
2. Return `RelativeValueSignal` with a plain-English explanation.
3. Include conservative eligibility rules.
4. Add a unit test that covers signal generation and edge cases.
5. Keep live eligibility false until backtesting, paper trading, and manual approvals explicitly support the strategy.
6. For mean-reversion signals, reject non-stationary spreads before allowing paper trading.
