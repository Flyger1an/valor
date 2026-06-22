import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { evaluateMarketRisk } from "@/lib/risk/risk-engine";

describe("risk engine", () => {
  it("produces a unified risk state and restrictions", () => {
    const risk = evaluateMarketRisk(sampleMarketData);

    expect(["Green", "Yellow", "Red", "Black"]).toContain(risk.state);
    expect(risk.activeAlerts.length).toBeGreaterThan(0);
    expect(risk.tradingRestrictions.length).toBeGreaterThan(0);
  });

  it("flags stablecoin peg deviations", () => {
    const risk = evaluateMarketRisk(sampleMarketData);

    expect(
      risk.activeAlerts.some((alert) => alert.category === "stablecoin"),
    ).toBe(true);
  });

  it("does not classify the sample research environment as Black", () => {
    const risk = evaluateMarketRisk(sampleMarketData);

    expect(risk.state).not.toBe("Black");
  });
});
