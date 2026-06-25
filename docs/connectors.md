# Data Sources and Connectors

Connectors implement `MarketDataConnector` in `src/lib/data/connectors.ts`.

```ts
export interface MarketDataConnector {
  id: string;
  label: string;
  needsApiKey: boolean;
  fetchLatest(): Promise<MarketDataBundle>;
}
```

## Current Connectors

`getDefaultConnector()` routes on `ENABLE_PUBLIC_MARKET_FETCH`:

- `PublicCryptoMarketConnector` — **the default** (used when the var is unset or any value other than the three below). Live OKX primary + Binance fallback for spot/perp/funding/open-interest, CoinGecko for stablecoin pegs, mempool.space + Etherscan for chain fees. Falls back to fixtures per-field on error (provenance recorded in the bundle's lineage).
- `SampleMarketDataConnector` (`=false`): deterministic local fixture bundle, no network.
- `CoinGeckoSpotConnector` (`=coingecko`): public spot price for BTC, ETH, SOL only. Order-book depth is **synthetic** — bid/ask sizes are hardcoded to 0 and the spread is fabricated.
- `BinanceMarketConnector` (`=binance`): live Binance spot + perp.

**Always fixtures (no connector fetches them live):** exchange health/withdrawals/reserves, security advisories / news / RSS, and ETF proxies.

## Adding an Exchange Connector

1. Create a class that implements `MarketDataConnector`.
2. Normalize venue data into `MarketSnapshot`, `StablecoinSnapshot`, `ExchangeHealthSignal`, or `SecurityAdvisory`.
3. Keep API keys in environment variables.
4. Add connector-specific data limitations to this document.
5. Add tests for mapping, stale timestamps, missing books, and failed requests.

## Manual CSV Import

`parseMarketCsv` in `src/lib/data/csv-import.ts` supports fallback imports. Required columns are `timestamp`, `venue`, `base`, `quote`, `instrumentType`, `price`, and `volume24hUsd`.

## Data Limitations

Free APIs commonly have stale order-book depth, incomplete open-interest coverage, inconsistent symbol naming, and rate limits. Treat generated signals as research outputs until venue-level reconciliation, transfer-route checks, and execution-quality logs exist.
