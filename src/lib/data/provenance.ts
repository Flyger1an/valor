import type { MarketDataBundle } from "@/lib/domain/types";

export interface DataProvenance {
  connectorId: string;
  connectorLabel: string;
  liveMarketCount: number;
  fixtureMarketCount: number;
  liveSharePct: number;
  hasFallbackAdvisory: boolean;
  summary: string;
}

export function buildDataProvenance(
  data: MarketDataBundle,
  connector: { id: string; label: string },
): DataProvenance {
  const liveMarketCount = data.markets.filter((market) => market.id.includes("-live")).length;
  const fixtureMarketCount = data.markets.length - liveMarketCount;
  const liveSharePct =
    data.markets.length === 0
      ? 0
      : round((liveMarketCount / data.markets.length) * 100, 1);
  const hasFallbackAdvisory = data.advisories.some((advisory) =>
    advisory.title.toLowerCase().includes("fallback"),
  );

  const summary =
    liveMarketCount === 0
      ? "Fixture-only bundle. No live venue prices in this refresh."
      : fixtureMarketCount === 0
        ? `All ${liveMarketCount} markets sourced live via ${connector.label}.`
        : `${liveMarketCount} live / ${fixtureMarketCount} fixture markets (${liveSharePct}% live).`;

  return {
    connectorId: connector.id,
    connectorLabel: connector.label,
    liveMarketCount,
    fixtureMarketCount,
    liveSharePct,
    hasFallbackAdvisory,
    summary,
  };
}

function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}