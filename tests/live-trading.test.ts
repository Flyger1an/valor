import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { evaluateLiveTradeRequest, readLiveTradingSettings } from "@/lib/live/live-trading";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";

describe("live trading guardrails", () => {
  it("is disabled by default and requires explicit unlocks", () => {
    const settings = readLiveTradingSettings({});

    expect(settings.enabled).toBe(false);
    expect(settings.killSwitchActive).toBe(true);
    expect(settings.maxLeverage).toBe(1);
  });

  it("blocks live trade attempts even for attractive signals", () => {
    const [signal] = generateRelativeValueSignals(sampleMarketData);
    const evaluation = evaluateLiveTradeRequest({
      signal,
      requestedNotionalUsd: 100,
      settings: readLiveTradingSettings({}),
      manualConfirmation: false,
      currentDailyPnlUsd: 0,
    });

    expect(evaluation.allowed).toBe(false);
    expect(evaluation.reasons).toContain("ENABLE_LIVE_TRADING is not true.");
    expect(evaluation.reasons).toContain("Live kill switch is active.");
  });
});
