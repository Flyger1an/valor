import { describe, expect, it } from "vitest";
import { runBasisCarryBacktest } from "@/lib/backtest/backtester";
import { sampleMarketData } from "@/lib/data/sample-market-data";

describe("basis carry backtester", () => {
  it("runs a report with accounting metrics", () => {
    const report = runBasisCarryBacktest(sampleMarketData.backtestHistory);

    expect(report.strategyName).toContain("BTC");
    expect(report.equityCurve.length).toBe(sampleMarketData.backtestHistory.length);
    expect(report.trades.length).toBeGreaterThan(0);
    expect(report.totalFeesUsd).toBeGreaterThan(0);
    expect(Number.isFinite(report.sharpe)).toBe(true);
  });

  it("charges fees and slippage on entry", () => {
    const report = runBasisCarryBacktest(sampleMarketData.backtestHistory, {
      entryEdgeBps: 1,
    });
    const entry = report.trades.find((trade) => trade.action === "enter");

    expect(entry).toBeDefined();
    expect(entry?.feesUsd).toBeGreaterThan(0);
    expect(entry?.slippageUsd).toBeGreaterThan(0);
  });
});
