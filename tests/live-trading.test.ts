import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { evaluateDataQuality } from "@/lib/data/quality";
import { evaluateLiveTradeRequest, readLiveTradingSettings } from "@/lib/live/live-trading";
import { evaluateMarketRisk } from "@/lib/risk/risk-engine";
import { evaluateSystemTrust } from "@/lib/risk/system-trust";
import { emptyPaperPortfolio } from "@/lib/state/local-store";
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

  it("includes system trust as a live-trading blocker", () => {
    const [signal] = generateRelativeValueSignals(sampleMarketData);
    const dataQuality = evaluateDataQuality(sampleMarketData, {
      connectorId: "sample-fixtures",
      connectorLabel: "Deterministic sample market bundle",
      mode: "sample",
      assessedAt: sampleMarketData.generatedAt,
    });
    const systemTrust = evaluateSystemTrust({
      dataQuality,
      risk: evaluateMarketRisk(sampleMarketData),
      paper: emptyPaperPortfolio(),
      now: new Date(sampleMarketData.generatedAt),
    });
    const evaluation = evaluateLiveTradeRequest({
      signal,
      requestedNotionalUsd: 100,
      settings: {
        ...readLiveTradingSettings({
          LIVE_TRADING_ENABLED: "true",
          LIVE_KILL_SWITCH: "false",
        }),
        manualConfirmationRequired: false,
      },
      manualConfirmation: true,
      currentDailyPnlUsd: 0,
      systemTrust,
    });

    expect(evaluation.allowed).toBe(false);
    expect(evaluation.reasons.some((reason) => reason.includes("System trust"))).toBe(true);
  });
});
