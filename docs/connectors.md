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

- `SampleMarketDataConnector`: deterministic local fixture bundle.
- `CoinGeckoSpotConnector`: public spot price adapter for BTC, ETH, and SOL. It is disabled unless `ENABLE_PUBLIC_MARKET_FETCH=true`.

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
