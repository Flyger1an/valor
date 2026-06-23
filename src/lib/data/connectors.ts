import { sampleMarketData } from "@/lib/data/sample-market-data";
import type {
  Asset,
  ChainFeeSnapshot,
  MarketDataBundle,
  MarketSnapshot,
  StablecoinSnapshot,
  Venue,
} from "@/lib/domain/types";

export interface MarketDataConnector {
  id: string;
  label: string;
  needsApiKey: boolean;
  fetchLatest(): Promise<MarketDataBundle>;
}

export class SampleMarketDataConnector implements MarketDataConnector {
  id = "sample-fixtures";
  label = "Deterministic sample market bundle";
  needsApiKey = false;

  async fetchLatest(): Promise<MarketDataBundle> {
    const generatedAt = new Date().toISOString();
    return {
      ...sampleMarketData,
      generatedAt,
      markets: sampleMarketData.markets.map((market) => ({
        ...market,
        timestamp: generatedAt,
      })),
      stablecoins: sampleMarketData.stablecoins.map((row) => ({
        ...row,
        timestamp: generatedAt,
      })),
      exchangeHealth: sampleMarketData.exchangeHealth.map((row) => ({
        ...row,
        updatedAt: generatedAt,
      })),
      chainFees: sampleMarketData.chainFees.map((row) => ({
        ...row,
        timestamp: generatedAt,
      })),
      advisories: sampleMarketData.advisories.map((row) => ({
        ...row,
        publishedAt: generatedAt,
      })),
      etfProxies: sampleMarketData.etfProxies.map((row) => ({
        ...row,
        timestamp: generatedAt,
      })),
    };
  }
}

export class CoinGeckoSpotConnector implements MarketDataConnector {
  id = "coingecko-public";
  label = "CoinGecko public spot price adapter";
  needsApiKey = false;

  async fetchLatest(): Promise<MarketDataBundle> {
    const ids = ["bitcoin", "ethereum", "solana"];
    const response = await fetch(
      `https://api.coingecko.com/api/v3/simple/price?ids=${ids.join(
        ",",
      )}&vs_currencies=usd&include_24hr_change=true`,
      { next: { revalidate: 60 } },
    );

    if (!response.ok) {
      throw new Error(`CoinGecko request failed: ${response.status}`);
    }

    const body = (await response.json()) as Record<
      string,
      { usd: number; usd_24h_change: number }
    >;

    const generatedAt = new Date().toISOString();
    const markets: MarketSnapshot[] = [
      mapCoinGeckoSpot("coinbase", "BTC", body.bitcoin, generatedAt),
      mapCoinGeckoSpot("coinbase", "ETH", body.ethereum, generatedAt),
      mapCoinGeckoSpot("coinbase", "SOL", body.solana, generatedAt),
    ];

    return {
      ...sampleMarketData,
      generatedAt,
      markets: [
        ...markets,
        ...sampleMarketData.markets.filter(
          (market) => market.instrumentType === "perp",
        ),
      ],
    };
  }
}

export class BinanceMarketConnector implements MarketDataConnector {
  id = "binance-public";
  label = "Binance public spot + perp adapter";
  needsApiKey = false;

  async fetchLatest(): Promise<MarketDataBundle> {
    const generatedAt = new Date().toISOString();
    const [spotMarkets, perpMarkets] = await Promise.all([
      fetchBinanceSpotMarkets(generatedAt),
      fetchBinancePerpMarkets(generatedAt),
    ]);

    return {
      ...sampleMarketData,
      generatedAt,
      markets: [...spotMarkets, ...perpMarkets],
    };
  }
}

export class PublicCryptoMarketConnector implements MarketDataConnector {
  id = "public-crypto-live";
  label = "Live public crypto APIs with fixture fallback";
  needsApiKey = false;

  async fetchLatest(): Promise<MarketDataBundle> {
    try {
      const generatedAt = new Date().toISOString();
      const [spotMarkets, perpMarkets, stablecoins, chainFees] =
        await Promise.all([
          fetchSpotMarkets(generatedAt),
          fetchPerpMarkets(generatedAt),
          fetchStablecoins(generatedAt),
          fetchChainFees(generatedAt),
        ]);

      const markets = [...spotMarkets, ...perpMarkets];
      if (markets.length < 4) {
        throw new Error("Insufficient live public market data returned.");
      }

      return {
        ...sampleMarketData,
        generatedAt,
        markets,
        stablecoins,
        chainFees,
      };
    } catch (error) {
      return {
        ...sampleMarketData,
        generatedAt: new Date().toISOString(),
        advisories: [
          ...sampleMarketData.advisories,
          {
            id: `public-ingest-fallback-${Date.now()}`,
            severity: "medium",
            source: "public data connector",
            title: "Public data ingest fallback",
            summary: `Live public data ingest failed and fixture fallback was used: ${
              error instanceof Error ? error.message : "unknown error"
            }`,
            affectedVenues: ["manual"],
            affectedAssets: ["BTC", "ETH"],
            publishedAt: new Date().toISOString(),
          },
        ],
      };
    }
  }
}

export function getDefaultConnector(): MarketDataConnector {
  if (process.env.ENABLE_PUBLIC_MARKET_FETCH === "false") {
    return new SampleMarketDataConnector();
  }

  if (process.env.ENABLE_PUBLIC_MARKET_FETCH === "coingecko") {
    return new CoinGeckoSpotConnector();
  }

  if (process.env.ENABLE_PUBLIC_MARKET_FETCH === "binance") {
    return new BinanceMarketConnector();
  }

  return new PublicCryptoMarketConnector();
}

function mapCoinGeckoSpot(
  venue: Venue,
  base: Asset,
  price: { usd: number; usd_24h_change: number },
  timestamp: string,
): MarketSnapshot {
  return {
    id: `${venue}-${base}-USD-spot-live`,
    venue,
    base,
    quote: "USD",
    instrumentType: "spot",
    price: price.usd,
    volume24hUsd: 0,
    volatility30d: 0.5,
    change24hPct: price.usd_24h_change,
    timestamp,
    orderBook: {
      bid: price.usd * 0.9999,
      ask: price.usd * 1.0001,
      bidDepthUsd: 0,
      askDepthUsd: 0,
      spreadBps: 2,
    },
  };
}

async function fetchSpotMarkets(timestamp: string): Promise<MarketSnapshot[]> {
  try {
    return await fetchOkxSpotMarkets(timestamp);
  } catch {
    return fetchBinanceSpotMarkets(timestamp);
  }
}

async function fetchPerpMarkets(timestamp: string): Promise<MarketSnapshot[]> {
  try {
    return await fetchOkxPerpMarkets(timestamp);
  } catch {
    return fetchBinancePerpMarkets(timestamp);
  }
}

async function fetchOkxSpotMarkets(timestamp: string): Promise<MarketSnapshot[]> {
  const instruments: Array<{ instId: string; base: Asset }> = [
    { instId: "BTC-USDT", base: "BTC" },
    { instId: "ETH-USDT", base: "ETH" },
    { instId: "SOL-USDT", base: "SOL" },
  ];

  return Promise.all(
    instruments.map(async ({ instId, base }) => {
      const [ticker, book] = await Promise.all([
        fetchOkx<OkxTicker>(
          `https://www.okx.com/api/v5/market/ticker?instId=${instId}`,
        ),
        fetchOkx<OkxBook>(
          `https://www.okx.com/api/v5/market/books?instId=${instId}&sz=1`,
        ),
      ]);
      const row = ticker.data[0];
      const bookRow = book.data[0];
      const price = Number(row.last);
      const bid = Number(bookRow.bids[0][0]);
      const ask = Number(bookRow.asks[0][0]);
      const bidSize = Number(bookRow.bids[0][1]);
      const askSize = Number(bookRow.asks[0][1]);
      const open24h = Number(row.open24h);

      return {
        id: `okx-${base}-USD-spot-live`,
        venue: "okx",
        base,
        quote: "USD",
        instrumentType: "spot",
        price,
        volume24hUsd: Number(row.volCcy24h) * price,
        volatility30d: volatilityProxy(((price - open24h) / open24h) * 100),
        change24hPct: ((price - open24h) / open24h) * 100,
        timestamp,
        orderBook: {
          bid,
          ask,
          bidDepthUsd: bidSize * bid,
          askDepthUsd: askSize * ask,
          spreadBps: ((ask - bid) / price) * 10_000,
        },
      } satisfies MarketSnapshot;
    }),
  );
}

async function fetchOkxPerpMarkets(timestamp: string): Promise<MarketSnapshot[]> {
  const instruments: Array<{ instId: string; base: Asset }> = [
    { instId: "BTC-USDT-SWAP", base: "BTC" },
    { instId: "ETH-USDT-SWAP", base: "ETH" },
    { instId: "SOL-USDT-SWAP", base: "SOL" },
  ];

  return Promise.all(
    instruments.map(async ({ instId, base }) => {
      const [ticker, book, funding, openInterest] = await Promise.all([
        fetchOkx<OkxTicker>(
          `https://www.okx.com/api/v5/market/ticker?instId=${instId}`,
        ),
        fetchOkx<OkxBook>(
          `https://www.okx.com/api/v5/market/books?instId=${instId}&sz=1`,
        ),
        fetchOkx<{ fundingRate: string }>(
          `https://www.okx.com/api/v5/public/funding-rate?instId=${instId}`,
        ),
        fetchOkx<{ oiUsd: string }>(
          `https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId=${instId}`,
        ),
      ]);
      const row = ticker.data[0];
      const bookRow = book.data[0];
      const price = Number(row.last);
      const bid = Number(bookRow.bids[0][0]);
      const ask = Number(bookRow.asks[0][0]);
      const bidSize = Number(bookRow.bids[0][1]);
      const askSize = Number(bookRow.asks[0][1]);
      const open24h = Number(row.open24h);

      return {
        id: `okx-${base}-USD-perp-live`,
        venue: "okx",
        base,
        quote: "USD",
        instrumentType: "perp",
        price,
        markPrice: price,
        indexPrice: Number(row.open24h),
        fundingRate8h: Number(funding.data[0].fundingRate),
        openInterestUsd: Number(openInterest.data[0].oiUsd),
        volume24hUsd: Number(row.volCcy24h) * price,
        volatility30d: volatilityProxy(((price - open24h) / open24h) * 100),
        change24hPct: ((price - open24h) / open24h) * 100,
        timestamp,
        orderBook: {
          bid,
          ask,
          bidDepthUsd: bidSize * bid,
          askDepthUsd: askSize * ask,
          spreadBps: ((ask - bid) / price) * 10_000,
        },
      } satisfies MarketSnapshot;
    }),
  );
}

async function fetchBinanceSpotMarkets(
  timestamp: string,
): Promise<MarketSnapshot[]> {
  const symbols: Array<{ symbol: string; base: Asset }> = [
    { symbol: "BTCUSDT", base: "BTC" },
    { symbol: "ETHUSDT", base: "ETH" },
    { symbol: "SOLUSDT", base: "SOL" },
  ];

  return Promise.all(
    symbols.map(async ({ symbol, base }) => {
      const [ticker, book] = await Promise.all([
        fetchJson<{
          lastPrice: string;
          volume: string;
          quoteVolume: string;
          priceChangePercent: string;
        }>(`https://api.binance.com/api/v3/ticker/24hr?symbol=${symbol}`),
        fetchJson<{ bidPrice: string; askPrice: string }>(
          `https://api.binance.com/api/v3/ticker/bookTicker?symbol=${symbol}`,
        ),
      ]);
      const price = Number(ticker.lastPrice);
      const bid = Number(book.bidPrice);
      const ask = Number(book.askPrice);
      const quoteVolume = Number(ticker.quoteVolume);

      return {
        id: `binance-${base}-USD-spot-live`,
        venue: "binance",
        base,
        quote: "USD",
        instrumentType: "spot",
        price,
        volume24hUsd: quoteVolume,
        volatility30d: volatilityProxy(Number(ticker.priceChangePercent)),
        change24hPct: Number(ticker.priceChangePercent),
        timestamp,
        orderBook: {
          bid,
          ask,
          bidDepthUsd: Math.max(quoteVolume * 0.006, 2_000_000),
          askDepthUsd: Math.max(quoteVolume * 0.006, 2_000_000),
          spreadBps: ((ask - bid) / price) * 10_000,
        },
      } satisfies MarketSnapshot;
    }),
  );
}

async function fetchBinancePerpMarkets(
  timestamp: string,
): Promise<MarketSnapshot[]> {
  const symbols: Array<{ symbol: string; base: Asset }> = [
    { symbol: "BTCUSDT", base: "BTC" },
    { symbol: "ETHUSDT", base: "ETH" },
    { symbol: "SOLUSDT", base: "SOL" },
  ];

  return Promise.all(
    symbols.map(async ({ symbol, base }) => {
      const [premium, ticker, book, openInterest] = await Promise.all([
        fetchJson<{
          markPrice: string;
          indexPrice: string;
          lastFundingRate: string;
        }>(`https://fapi.binance.com/fapi/v1/premiumIndex?symbol=${symbol}`),
        fetchJson<{
          lastPrice: string;
          quoteVolume: string;
          priceChangePercent: string;
        }>(`https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=${symbol}`),
        fetchJson<{ bidPrice: string; askPrice: string }>(
          `https://fapi.binance.com/fapi/v1/ticker/bookTicker?symbol=${symbol}`,
        ),
        fetchJson<{ openInterest: string }>(
          `https://fapi.binance.com/fapi/v1/openInterest?symbol=${symbol}`,
        ),
      ]);
      const price = Number(ticker.lastPrice);
      const bid = Number(book.bidPrice);
      const ask = Number(book.askPrice);
      const quoteVolume = Number(ticker.quoteVolume);

      return {
        id: `binance-${base}-USD-perp-live`,
        venue: "binance",
        base,
        quote: "USD",
        instrumentType: "perp",
        price,
        markPrice: Number(premium.markPrice),
        indexPrice: Number(premium.indexPrice),
        fundingRate8h: Number(premium.lastFundingRate),
        openInterestUsd: Number(openInterest.openInterest) * price,
        volume24hUsd: quoteVolume,
        volatility30d: volatilityProxy(Number(ticker.priceChangePercent)),
        change24hPct: Number(ticker.priceChangePercent),
        timestamp,
        orderBook: {
          bid,
          ask,
          bidDepthUsd: Math.max(quoteVolume * 0.004, 2_000_000),
          askDepthUsd: Math.max(quoteVolume * 0.004, 2_000_000),
          spreadBps: ((ask - bid) / price) * 10_000,
        },
      } satisfies MarketSnapshot;
    }),
  );
}

async function fetchStablecoins(
  timestamp: string,
): Promise<StablecoinSnapshot[]> {
  const body = await fetchJson<
    Record<string, { usd: number; usd_24h_vol?: number }>
  >(
    "https://api.coingecko.com/api/v3/simple/price?ids=tether,usd-coin,dai&vs_currencies=usd&include_24hr_vol=true",
  );
  const rows: Array<{
    asset: StablecoinSnapshot["asset"];
    id: "tether" | "usd-coin" | "dai";
    venue: Venue;
  }> = [
    { asset: "USDT", id: "tether", venue: "binance" },
    { asset: "USDC", id: "usd-coin", venue: "coinbase" },
    { asset: "DAI", id: "dai", venue: "uniswap" },
  ];

  return rows.map(({ asset, id, venue }) => ({
    asset,
    venue,
    priceUsd: body[id].usd,
    liquidityUsd: body[id].usd_24h_vol ?? 0,
    pegDeviationBps: (body[id].usd - 1) * 10_000,
    timestamp,
  }));
}

async function fetchChainFees(timestamp: string): Promise<ChainFeeSnapshot[]> {
  const [btcFees, ethGas] = await Promise.allSettled([
    fetchJson<{ fastestFee: number; halfHourFee: number }>(
      "https://mempool.space/api/v1/fees/recommended",
    ),
    fetchJson<{ result: { ProposeGasPrice: string } }>(
      "https://api.etherscan.io/api?module=gastracker&action=gasoracle",
    ),
  ]);

  return [
    {
      chain: "bitcoin",
      feeMetric: "sat/vB next block",
      value:
        btcFees.status === "fulfilled"
          ? btcFees.value.fastestFee
          : sampleMarketData.chainFees[0].value,
      normalRangeHigh: 80,
      timestamp,
    },
    {
      chain: "ethereum",
      feeMetric: "gwei proposed gas",
      value: validNumber(
        ethGas.status === "fulfilled"
          ? Number(ethGas.value.result?.ProposeGasPrice)
          : undefined,
        sampleMarketData.chainFees[1].value,
      ),
      normalRangeHigh: 55,
      timestamp,
    },
  ];
}

async function fetchJson<T>(url: string): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8_000);
  try {
    const response = await fetch(url, {
      signal: controller.signal,
      headers: {
        accept: "application/json",
        "user-agent": "Valor-Risk-Intel/0.1",
      },
    });
    if (!response.ok) throw new Error(`${url} returned ${response.status}`);
    return (await response.json()) as T;
  } finally {
    clearTimeout(timeout);
  }
}

interface OkxTicker {
  last: string;
  askPx: string;
  bidPx: string;
  open24h: string;
  volCcy24h: string;
}

interface OkxBook {
  asks: Array<[string, string, string, string]>;
  bids: Array<[string, string, string, string]>;
}

async function fetchOkx<T>(
  url: string,
): Promise<{ code: string; data: T[] }> {
  const body = await fetchJson<{ code: string; data?: T[] }>(url);
  if (body.code !== "0" || !Array.isArray(body.data) || body.data.length === 0) {
    throw new Error(`OKX request returned empty/non-zero response for ${url}`);
  }
  return { code: body.code, data: body.data };
}

function volatilityProxy(change24hPct: number): number {
  return Math.min(1.2, Math.max(0.25, (Math.abs(change24hPct) / 100) * Math.sqrt(30)));
}

function validNumber(value: number | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}
