import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { advancePaperBook } from "@/lib/paper/paper-book";
import { evaluateMarketRisk } from "@/lib/risk/risk-engine";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";

describe("advancePaperBook", () => {
  it("opens eligible positions and records equity history", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const risk = evaluateMarketRisk(sampleMarketData);
    const first = advancePaperBook({
      signals,
      risk,
      timestamp: sampleMarketData.generatedAt,
    });

    expect(first.portfolio.positions.length).toBeGreaterThan(0);
    expect(first.equityHistory).toHaveLength(1);
    expect(first.opened).toBeGreaterThan(0);

    const second = advancePaperBook({
      previous: first.portfolio,
      signals,
      risk,
      timestamp: new Date(Date.parse(sampleMarketData.generatedAt) + 60_000).toISOString(),
      equityHistory: first.equityHistory,
    });

    expect(second.equityHistory).toHaveLength(2);
    expect(second.marked).toBeGreaterThan(0);
  });
});