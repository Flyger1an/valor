import { fetchJson } from "@/lib/data/http";
import type { Asset, MarketSnapshot, PairSpreadPoint } from "@/lib/domain/types";

export type RelativeValueHistorySource = "live-klines" | "fixture";

export interface RelativeValueHistory {
  btcEthRatioHistory: PairSpreadPoint[];
  ethSolSpreadHistory: PairSpreadPoint[];
  source: RelativeValueHistorySource;
}

export interface ClosePoint {
  timestamp: string;
  close: number;
}

const HISTORY_DAYS = 90;
// The z-score signal generators require >= 6 historical points; keep margin.
const MIN_HISTORY_POINTS = 8;

/**
 * Build the relative-value z-score histories (BTC/ETH ratio, ETH/SOL spread)
 * from real exchange klines instead of fixtures, then append a live "current"
 * point so the z-score's latest observation reflects the live spot price.
 *
 * Never throws: on any failure (network, geo-block, thin data) it returns the
 * supplied fixture histories and reports `source: "fixture"` so lineage stays
 * honest end to end.
 */
export async function buildRelativeValueHistory(input: {
  markets: MarketSnapshot[];
  generatedAt: string;
  fixture: {
    btcEthRatioHistory: PairSpreadPoint[];
    ethSolSpreadHistory: PairSpreadPoint[];
  };
  fetchCloses?: (base: Asset) => Promise<ClosePoint[]>;
}): Promise<RelativeValueHistory> {
  const fetchCloses = input.fetchCloses ?? fetchDailyCloses;

  try {
    const [btc, eth, sol] = await Promise.all([
      fetchCloses("BTC"),
      fetchCloses("ETH"),
      fetchCloses("SOL"),
    ]);

    const btcEthRatioHistory = withLivePoint(
      buildPairHistory(btc, eth),
      spotPrice(input.markets, "BTC"),
      spotPrice(input.markets, "ETH"),
      input.generatedAt,
    );
    const ethSolSpreadHistory = withLivePoint(
      buildPairHistory(eth, sol),
      spotPrice(input.markets, "ETH"),
      spotPrice(input.markets, "SOL"),
      input.generatedAt,
    );

    if (
      btcEthRatioHistory.length < MIN_HISTORY_POINTS ||
      ethSolSpreadHistory.length < MIN_HISTORY_POINTS
    ) {
      throw new Error("Insufficient live kline history returned.");
    }

    return { btcEthRatioHistory, ethSolSpreadHistory, source: "live-klines" };
  } catch {
    return {
      btcEthRatioHistory: input.fixture.btcEthRatioHistory,
      ethSolSpreadHistory: input.fixture.ethSolSpreadHistory,
      source: "fixture",
    };
  }
}

/**
 * Daily closes, oldest-first, with the same OKX-primary / Binance-fallback
 * hedge the live market connector uses (Binance is geo-blocked in some
 * regions, including the US).
 */
async function fetchDailyCloses(base: Asset): Promise<ClosePoint[]> {
  try {
    return await fetchOkxDailyCloses(base);
  } catch {
    return await fetchBinanceDailyCloses(base);
  }
}

interface OkxCandleResponse {
  code: string;
  // [ts, open, high, low, close, ...], newest-first.
  data: string[][];
}

async function fetchOkxDailyCloses(base: Asset): Promise<ClosePoint[]> {
  // 1Dutc aligns candle opens to 00:00 UTC, matching Binance day boundaries.
  const body = await fetchJson<OkxCandleResponse>(
    `https://www.okx.com/api/v5/market/candles?instId=${base}-USDT&bar=1Dutc&limit=${HISTORY_DAYS}`,
  );
  if (body.code !== "0" || !Array.isArray(body.data) || body.data.length === 0) {
    throw new Error(`OKX candles empty/non-zero for ${base}`);
  }
  return body.data
    .map((row) => ({
      timestamp: new Date(Number(row[0])).toISOString(),
      close: Number(row[4]),
    }))
    .reverse();
}

// Binance kline rows are positional arrays: [openTime, open, high, low, close, ...].
type Kline = [number, string, string, string, string, ...unknown[]];

async function fetchBinanceDailyCloses(base: Asset): Promise<ClosePoint[]> {
  const klines = await fetchJson<Kline[]>(
    `https://api.binance.com/api/v3/klines?symbol=${base}USDT&interval=1d&limit=${HISTORY_DAYS}`,
  );
  // Binance returns oldest-first already.
  return klines.map((row) => ({
    timestamp: new Date(row[0]).toISOString(),
    close: Number(row[4]),
  }));
}

/** Align two close series by candle timestamp into firstPrice/secondPrice points. */
function buildPairHistory(
  first: ClosePoint[],
  second: ClosePoint[],
): PairSpreadPoint[] {
  const secondByTs = new Map(second.map((point) => [point.timestamp, point.close]));
  return first
    .map((point) => {
      const secondClose = secondByTs.get(point.timestamp);
      if (
        secondClose === undefined ||
        !Number.isFinite(point.close) ||
        !Number.isFinite(secondClose) ||
        secondClose === 0
      ) {
        return null;
      }
      return {
        timestamp: point.timestamp,
        firstPrice: point.close,
        secondPrice: secondClose,
      };
    })
    .filter((point): point is PairSpreadPoint => point !== null);
}

function withLivePoint(
  history: PairSpreadPoint[],
  firstPrice: number | undefined,
  secondPrice: number | undefined,
  timestamp: string,
): PairSpreadPoint[] {
  if (
    firstPrice === undefined ||
    secondPrice === undefined ||
    !Number.isFinite(firstPrice) ||
    !Number.isFinite(secondPrice) ||
    secondPrice === 0
  ) {
    return history;
  }
  return [...history, { timestamp, firstPrice, secondPrice }];
}

/** Most-liquid live spot price for an asset, used as the live "current" point. */
function spotPrice(markets: MarketSnapshot[], base: Asset): number | undefined {
  const spots = markets.filter(
    (market) => market.instrumentType === "spot" && market.base === base,
  );
  if (spots.length === 0) return undefined;
  return spots.sort((a, b) => b.volume24hUsd - a.volume24hUsd)[0].price;
}
