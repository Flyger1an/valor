import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { evaluateDataQuality } from "@/lib/data/quality";
import type { AlertDelivery } from "@/lib/alerts/types";
import { evaluateMarketRisk } from "@/lib/risk/risk-engine";
import { evaluateSystemTrust } from "@/lib/risk/system-trust";
import { emptyPaperPortfolio } from "@/lib/state/local-store";

describe("system trust", () => {
  it("allows paper mode but blocks live execution on sample fixture data", () => {
    const dataQuality = sampleDataQuality();
    const risk = evaluateMarketRisk(sampleMarketData);
    const verdict = evaluateSystemTrust({
      dataQuality,
      risk,
      paper: emptyPaperPortfolio(),
      now: new Date(sampleMarketData.generatedAt),
    });

    expect(verdict.status).toBe("caution");
    expect(verdict.blocksPaperTrading).toBe(false);
    expect(verdict.blocksLiveTrading).toBe(true);
    expect(verdict.issues.some((issue) => issue.code === "fixture-backed-data")).toBe(true);
  });

  it("blocks new paper entries when the persisted kill switch is active", () => {
    const verdict = evaluateSystemTrust({
      dataQuality: sampleDataQuality(),
      risk: evaluateMarketRisk(sampleMarketData),
      killSwitch: {
        active: true,
        reason: "manual halt",
        activatedBy: "test",
        activatedAt: sampleMarketData.generatedAt,
        dashboardResetRequired: true,
      },
      paper: emptyPaperPortfolio(),
      now: new Date(sampleMarketData.generatedAt),
    });

    expect(verdict.status).toBe("blocked");
    expect(verdict.blocksPaperTrading).toBe(true);
    expect(verdict.issues.some((issue) => issue.code === "kill-switch-active")).toBe(true);
  });

  it("blocks on repeated scheduler failures", () => {
    const verdict = evaluateSystemTrust({
      dataQuality: sampleDataQuality(),
      risk: evaluateMarketRisk(sampleMarketData),
      schedulerStatus: {
        running: false,
        cycleCount: 4,
        consecutiveErrors: 3,
        lastMessage: "connector unavailable",
      },
      paper: emptyPaperPortfolio(),
      now: new Date(sampleMarketData.generatedAt),
    });

    expect(verdict.blocksPaperTrading).toBe(true);
    expect(verdict.issues.some((issue) => issue.code === "scheduler-consecutive-errors")).toBe(true);
  });

  it("blocks when alert delivery has failed", () => {
    const failedDelivery: AlertDelivery = {
      id: "delivery:test",
      alertId: "alert:test",
      channel: "telegram",
      provider: "telegram",
      status: "failed",
      attemptedAt: sampleMarketData.generatedAt,
      destination: "redacted",
      redactedMessage: "test",
      error: "provider rejected request",
    };
    const verdict = evaluateSystemTrust({
      dataQuality: sampleDataQuality(),
      risk: evaluateMarketRisk(sampleMarketData),
      alertDeliveries: [failedDelivery],
      paper: emptyPaperPortfolio(),
      now: new Date(sampleMarketData.generatedAt),
    });

    expect(verdict.blocksPaperTrading).toBe(true);
    expect(verdict.issues.some((issue) => issue.code === "alert-delivery-failed")).toBe(true);
  });

  it("blocks paper ledger drift beyond configured notional limits", () => {
    const paper = emptyPaperPortfolio();
    paper.positions = [
      {
        id: "paper-pos-drift",
        signalId: "signal-drift",
        signalKind: "spot_perp_basis",
        assetPair: "BTC/USD",
        venue: "test",
        direction: "long_spot_short_perp",
        notionalUsd: 90_000,
        entryEdgeBps: 50,
        markPnlUsd: 0,
        openedAt: sampleMarketData.generatedAt,
      },
    ];
    const verdict = evaluateSystemTrust({
      dataQuality: sampleDataQuality(),
      risk: evaluateMarketRisk(sampleMarketData),
      paper,
      now: new Date(sampleMarketData.generatedAt),
    });

    expect(verdict.blocksPaperTrading).toBe(true);
    expect(verdict.issues.some((issue) => issue.code === "paper-ledger-notional-drift")).toBe(true);
  });
});

function sampleDataQuality() {
  return evaluateDataQuality(sampleMarketData, {
    connectorId: "sample-fixtures",
    connectorLabel: "Deterministic sample market bundle",
    mode: "sample",
    assessedAt: sampleMarketData.generatedAt,
  });
}
