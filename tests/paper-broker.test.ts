import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import type { MarketRiskState } from "@/lib/domain/types";
import { simulatePaperPortfolio } from "@/lib/paper/paper-broker";
import { evaluateMarketRisk } from "@/lib/risk/risk-engine";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";

describe("paper broker", () => {
  it("simulates trades from eligible signals under sample risk limits", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const risk = evaluateMarketRisk(sampleMarketData);
    const portfolio = simulatePaperPortfolio({ signals, risk });

    expect(portfolio.trades.length).toBeGreaterThan(0);
    expect(portfolio.positions.length).toBe(portfolio.trades.length);
    expect(portfolio.equityUsd).toBeGreaterThan(0);
  });

  it("blocks new paper trades in Black risk state", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const risk: MarketRiskState = {
      ...evaluateMarketRisk(sampleMarketData),
      state: "Black",
    };
    const portfolio = simulatePaperPortfolio({ signals, risk });

    expect(portfolio.trades).toHaveLength(0);
    expect(portfolio.rejectedSignals.length).toBeGreaterThan(0);
  });
});
