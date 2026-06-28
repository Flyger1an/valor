# Data Sources and Connectors

Connectors implement `MarketDataConnector` in `src/lib/data/connectors.ts`.

```ts
export interface MarketDataConnector {
  id: string;
  label: string;
  mode: MarketDataMode;
  needsApiKey: boolean;
  fetchLatest(): Promise<MarketDataBundle>;
}
```

## Current Connectors

- `SampleMarketDataConnector`: deterministic local fixture bundle.
- `CoinGeckoSpotConnector`: public spot price adapter for BTC, ETH, and SOL. It is enabled with `ENABLE_PUBLIC_MARKET_FETCH=coingecko`.
- `BinanceMarketConnector`: public Binance spot/perp adapter. It is enabled with `ENABLE_PUBLIC_MARKET_FETCH=binance`.
- `PublicCryptoMarketConnector`: public OKX/Binance/CoinGecko data path with fixture fallback. It is enabled with `ENABLE_PUBLIC_MARKET_FETCH=true`, `public`, or `live`.

When `ENABLE_PUBLIC_MARKET_FETCH` is absent, `false`, `0`, `off`, or `sample`, Valor uses deterministic sample data. Unknown values also fall back to sample mode.

Public live modes rebuild BTC/ETH and ETH/SOL z-score histories from exchange daily candles when possible, then append the live spot point. If that history fetch fails, Valor falls back to fixture histories and records that lineage on the market-data bundle.

## Data Quality

Every refresh now produces a data-quality report with connector mode, bundle age, market count, issue counts, fallback status, and whether new paper entries are blocked. Sample fixtures remain inspectable even with static timestamps. Public-data sessions block new paper entries when snapshots are stale, books are crossed, depth is missing, spreads are extreme, or the connector falls back to fixtures.

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
