import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";

describe("relative-value signal engine", () => {
  it("generates multiple signal families from sample data", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const kinds = new Set(signals.map((signal) => signal.kind));

    expect(signals.length).toBeGreaterThanOrEqual(6);
    expect(kinds.has("spot_perp_basis")).toBe(true);
    expect(kinds.has("funding_carry")).toBe(true);
    expect(kinds.has("cross_exchange_premium")).toBe(true);
    expect(kinds.has("stablecoin_depeg")).toBe(true);
  });

  it("marks live trading ineligible regardless of paper signal quality", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);
    const paperEligible = signals.find((signal) => signal.eligibleForPaperTrading);

    expect(paperEligible).toBeDefined();
    expect(signals.every((signal) => signal.eligibleForLiveTrading === false)).toBe(true);
  });

  it("prioritizes opportunity score with risk and liquidity adjustments", () => {
    const signals = generateRelativeValueSignals(sampleMarketData);

    expect(signals[0].opportunityScore).toBeGreaterThanOrEqual(
      signals[signals.length - 1].opportunityScore,
    );
    expect(signals[0].liquidityScore).toBeGreaterThan(0);
  });
});
