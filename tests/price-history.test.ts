import { describe, expect, it } from "vitest";
import {
  buildRelativeValueHistory,
  type ClosePoint,
} from "@/lib/data/price-history";
import type { Asset, MarketSnapshot, PairSpreadPoint } from "@/lib/domain/types";

function closes(prices: number[]): ClosePoint[] {
  return prices.map((close, index) => ({
    timestamp: new Date(Date.UTC(2024, 0, index + 1)).toISOString(),
    close,
  }));
}

function spot(base: Asset, price: number): MarketSnapshot {
  return {
    id: `live-${base}`,
    venue: "binance",
    base,
    quote: "USD",
    instrumentType: "spot",
    price,
    volume24hUsd: 1_000_000,
    volatility30d: 0.5,
    change24hPct: 0,
    timestamp: "2024-02-01T00:00:00.000Z",
    orderBook: { bid: price, ask: price, bidDepthUsd: 1, askDepthUsd: 1, spreadBps: 1 },
  };
}

const FIXTURE = {
  btcEthRatioHistory: [
    { timestamp: "fixture", firstPrice: 1, secondPrice: 1 },
  ] as PairSpreadPoint[],
  ethSolSpreadHistory: [
    { timestamp: "fixture", firstPrice: 1, secondPrice: 1 },
  ] as PairSpreadPoint[],
};

const LIVE_MARKETS = [spot("BTC", 70_000), spot("ETH", 3_500), spot("SOL", 160)];

describe("buildRelativeValueHistory", () => {
  it("builds live histories from klines and appends a live current point", async () => {
    const series: Record<string, number[]> = {
      BTC: [60_000, 61_000, 62_000, 63_000, 64_000, 65_000, 66_000, 67_000, 68_000, 69_000],
      ETH: [3_000, 3_050, 3_100, 3_150, 3_200, 3_250, 3_300, 3_350, 3_400, 3_450],
      SOL: [140, 142, 144, 146, 148, 150, 152, 154, 156, 158],
    };
    const generatedAt = "2024-02-01T00:00:00.000Z";

    const result = await buildRelativeValueHistory({
      generatedAt,
      markets: LIVE_MARKETS,
      fixture: FIXTURE,
      fetchCloses: async (base) => closes(series[base]),
    });

    expect(result.source).toBe("live-klines");
    // 10 aligned daily closes + 1 appended live "current" point.
    expect(result.btcEthRatioHistory).toHaveLength(11);
    expect(result.ethSolSpreadHistory).toHaveLength(11);

    const lastRatio = result.btcEthRatioHistory.at(-1)!;
    expect(lastRatio.timestamp).toBe(generatedAt);
    expect(lastRatio.firstPrice).toBe(70_000);
    expect(lastRatio.secondPrice).toBe(3_500);
  });

  it("falls back to fixtures honestly when the kline fetch fails", async () => {
    const result = await buildRelativeValueHistory({
      generatedAt: "2024-02-01T00:00:00.000Z",
      markets: LIVE_MARKETS,
      fixture: FIXTURE,
      fetchCloses: async () => {
        throw new Error("network down");
      },
    });

    expect(result.source).toBe("fixture");
    expect(result.btcEthRatioHistory).toBe(FIXTURE.btcEthRatioHistory);
    expect(result.ethSolSpreadHistory).toBe(FIXTURE.ethSolSpreadHistory);
  });

  it("falls back when too few aligned points are returned", async () => {
    const result = await buildRelativeValueHistory({
      generatedAt: "2024-02-01T00:00:00.000Z",
      markets: LIVE_MARKETS,
      fixture: FIXTURE,
      fetchCloses: async () => closes([100, 101, 102]),
    });

    expect(result.source).toBe("fixture");
  });
});
