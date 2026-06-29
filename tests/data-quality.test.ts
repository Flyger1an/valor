import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { evaluateDataQuality } from "@/lib/data/quality";
import type { MarketDataBundle, MarketSnapshot } from "@/lib/domain/types";

describe("data quality evaluator", () => {
  it("keeps old sample fixtures inspectable without blocking paper mode", () => {
    const report = evaluateDataQuality(sampleMarketData, {
      connectorId: "sample-fixtures",
      connectorLabel: "Deterministic sample market bundle",
      mode: "sample",
      assessedAt: "2026-06-27T12:00:00.000Z",
    });

    expect(report.status).toBe("healthy");
    expect(report.fixtureBacked).toBe(true);
    expect(report.blocksPaperTrading).toBe(false);
    expect(report.issues.some((issue) => issue.code === "sample-fixture-age")).toBe(true);
  });

  it("blocks paper entries when public market snapshots are stale", () => {
    const data = withMarkets(
      sampleMarketData.markets.map((market) => ({
        ...market,
        timestamp: "2026-06-27T11:30:00.000Z",
      })),
      "2026-06-27T12:00:00.000Z",
    );
    const report = evaluateDataQuality(data, {
      connectorId: "public-crypto-live",
      connectorLabel: "Live public crypto APIs with fixture fallback",
      mode: "public",
      assessedAt: "2026-06-27T12:00:00.000Z",
    });

    expect(report.status).toBe("blocked");
    expect(report.blocksPaperTrading).toBe(true);
    expect(report.criticalIssueCount).toBeGreaterThan(0);
    expect(report.issues.some((issue) => issue.code === "stale-market")).toBe(true);
  });

  it("blocks crossed books even in sample mode", () => {
    const [first, ...rest] = sampleMarketData.markets;
    const data = withMarkets([
      {
        ...first,
        orderBook: {
          ...first.orderBook,
          bid: first.orderBook.ask + 1,
          ask: first.orderBook.bid - 1,
        },
      },
      ...rest,
    ]);
    const report = evaluateDataQuality(data, {
      connectorId: "sample-fixtures",
      connectorLabel: "Deterministic sample market bundle",
      mode: "sample",
      assessedAt: sampleMarketData.generatedAt,
    });

    expect(report.status).toBe("blocked");
    expect(report.blocksPaperTrading).toBe(true);
    expect(report.issues.some((issue) => issue.code === "crossed-book")).toBe(true);
  });

  it("treats public fixture fallback as blocking", () => {
    const data: MarketDataBundle = {
      ...sampleMarketData,
      generatedAt: "2026-06-27T12:00:00.000Z",
      advisories: [
        ...sampleMarketData.advisories,
        {
          id: "public-ingest-fallback-test",
          severity: "medium",
          source: "public data connector",
          title: "Public data ingest fallback",
          summary: "Live public data ingest failed and fixture fallback was used.",
          affectedVenues: ["manual"],
          affectedAssets: ["BTC"],
          publishedAt: "2026-06-27T12:00:00.000Z",
        },
      ],
    };
    const report = evaluateDataQuality(data, {
      connectorId: "public-crypto-live",
      connectorLabel: "Live public crypto APIs with fixture fallback",
      mode: "public",
      assessedAt: "2026-06-27T12:00:00.000Z",
    });

    expect(report.fallbackUsed).toBe(true);
    expect(report.status).toBe("blocked");
    expect(report.issues.some((issue) => issue.code === "fixture-fallback")).toBe(true);
  });
});

function withMarkets(
  markets: MarketSnapshot[],
  generatedAt = sampleMarketData.generatedAt,
): MarketDataBundle {
  return {
    ...sampleMarketData,
    generatedAt,
    markets,
  };
}
