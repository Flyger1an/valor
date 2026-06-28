import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import type { PairSpreadPoint } from "@/lib/domain/types";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";
import { augmentedDickeyFullerTest } from "@/lib/utils/math";

describe("augmentedDickeyFullerTest", () => {
  it("rejects a unit root on a stationary AR(1) series", () => {
    const adf = augmentedDickeyFullerTest(ar1Series(0.55, 80), 0.05);

    expect(adf.isStationary).toBe(true);
    expect(adf.testStatistic).toBeLessThan(0);
  });

  it("does not reject a unit root on a drifting series", () => {
    const adf = augmentedDickeyFullerTest(unitRootWalk(80), 0.05);

    expect(adf.isStationary).toBe(false);
  });

  it("returns non-stationary when history is too short", () => {
    const adf = augmentedDickeyFullerTest([1, 1.01, 0.99, 1.02], 0.05);

    expect(adf.isStationary).toBe(false);
    expect(adf.confidence).toBe("none");
  });
});

describe("mean-reversion signal stationarity gate", () => {
  it("blocks paper trading on a dislocated but non-stationary spread", () => {
    const ratios = unitRootWalk(40).map((value) => value / 5);
    ratios[ratios.length - 1] = ratios[ratios.length - 2] + 2.5;

    const signals = generateRelativeValueSignals({
      ...sampleMarketData,
      btcEthRatioHistory: historyFromRatios(ratios),
    });
    const btcEth = signals.find((signal) => signal.kind === "btc_eth_ratio");

    expect(btcEth).toBeDefined();
    expect(btcEth?.spreadStationary).toBe(false);
    expect(btcEth?.adfTestStatistic).toBeTypeOf("number");
    expect(btcEth?.direction).toBe("watch_only");
    expect(btcEth?.eligibleForPaperTrading).toBe(false);
    expect(btcEth?.explanation).toMatch(/unit root/i);
  });

  it("allows paper trading when stationarity passes and z-score exceeds threshold", () => {
    const ratios = ar1Series(0.55, 40).map((value) => 18 + value * 0.02);
    ratios[ratios.length - 1] =
      mean(ratios.slice(0, -1)) + 3 * standardDeviation(ratios.slice(0, -1));

    const signals = generateRelativeValueSignals({
      ...sampleMarketData,
      btcEthRatioHistory: historyFromRatios(ratios),
    });
    const btcEth = signals.find((signal) => signal.kind === "btc_eth_ratio");

    expect(btcEth).toBeDefined();
    expect(btcEth?.spreadStationary).toBe(true);
    expect(btcEth?.direction).not.toBe("watch_only");
    expect(btcEth?.eligibleForPaperTrading).toBe(true);
  });

  it("marks stationarity evidence on fixture mean-reversion signals", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const meanReversionSignals = signals.filter(
      (signal) =>
        signal.kind === "btc_eth_ratio" ||
        signal.kind === "pair_spread_zscore",
    );

    expect(meanReversionSignals).toHaveLength(2);
    for (const signal of meanReversionSignals) {
      expect(signal.spreadStationary).toBeTypeOf("boolean");
      expect(signal.adfTestStatistic).toBeTypeOf("number");
    }
  });
});

function ar1Series(phi: number, length: number, noise = 0.05): number[] {
  const series = [1];
  for (let i = 1; i < length; i++) {
    const shock = noise * Math.sin(i * 1.7) * Math.cos(i * 0.9);
    series.push(phi * series[i - 1] + shock);
  }
  return series;
}

function unitRootWalk(length: number): number[] {
  const series = [100];
  for (let i = 1; i < length; i++) {
    series.push(series[i - 1] + 0.35 + Math.sin(i * 0.7) * 0.05);
  }
  return series;
}

function historyFromRatios(ratios: number[]): PairSpreadPoint[] {
  return ratios.map((ratio, i) => ({
    timestamp: `2026-01-${String(i + 1).padStart(2, "0")}T00:00:00Z`,
    firstPrice: ratio,
    secondPrice: 1,
  }));
}

function mean(values: number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function standardDeviation(values: number[]): number {
  const avg = mean(values);
  const variance =
    values.reduce((sum, value) => sum + (value - avg) ** 2, 0) /
    Math.max(1, values.length - 1);
  return Math.sqrt(variance);
}
