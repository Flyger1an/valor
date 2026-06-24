import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import type { PairSpreadPoint } from "@/lib/domain/types";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";
import { mean, meanReversionHalfLifeHours, zScore } from "@/lib/utils/math";

describe("signal enrichment (zscore / spreadValue / convergence)", () => {
  it("populates the locked contract fields on every emitted signal", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    expect(signals.length).toBeGreaterThan(0);
    for (const s of signals) {
      expect(Number.isFinite(s.zscore!)).toBe(true);
      expect(Number.isFinite(s.spreadValue!)).toBe(true);
      expect(s.expectedConvergenceHours!).toBeGreaterThan(0);
    }
  });

  it("surfaces a REAL z-score for the ratio signal (not a default)", () => {
    // Craft a clearly-dislocated BTC/ETH ratio history.
    const ratios = [17.8, 18.2, 17.9, 18.1, 18.0, 17.95, 18.05, 18.0, 17.9, 19.6];
    const history: PairSpreadPoint[] = ratios.map((r, i) => ({
      timestamp: `2026-06-${10 + i}T00:00:00Z`,
      firstPrice: r,
      secondPrice: 1,
    }));
    const signals = generateRelativeValueSignals({
      ...sampleMarketData,
      btcEthRatioHistory: history,
    });
    const btcEth = signals.find((s) => s.kind === "btc_eth_ratio");
    expect(btcEth).toBeDefined();

    const current = ratios[ratios.length - 1];
    const sample = ratios.slice(0, -1);
    expect(btcEth!.zscore!).toBeCloseTo(zScore(current, sample), 2);
    expect(btcEth!.spreadValue!).toBeCloseTo((current - mean(sample)) / mean(sample), 4);
    expect(btcEth!.expectedConvergenceHours!).toBeGreaterThan(0);
  });
});

describe("meanReversionHalfLifeHours", () => {
  it("recovers ~1 period (24h) for a known AR(1) decay (φ=0.5)", () => {
    const series = Array.from({ length: 14 }, (_, t) => 10 * 0.5 ** t); // φ=0.5
    const h = meanReversionHalfLifeHours(series, 24, 999);
    // ≈24h; allow finite-sample mean bias (regressor uses full-series mean).
    expect(h).toBeGreaterThan(18);
    expect(h).toBeLessThan(30);
  });

  it("falls back when the series is too short", () => {
    expect(meanReversionHalfLifeHours([1, 2, 3], 24, 42)).toBe(42);
  });

  it("falls back for an explosive (φ>1) series", () => {
    const explosive = Array.from({ length: 14 }, (_, t) => 1.5 ** t); // φ≈1.5
    expect(meanReversionHalfLifeHours(explosive, 24, 42)).toBe(42);
  });
});
