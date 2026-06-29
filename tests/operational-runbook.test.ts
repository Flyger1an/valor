import { describe, expect, it } from "vitest";
import type { AlertDelivery } from "@/lib/alerts/types";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import type {
  DataQualityReport,
  MarketRiskState,
  RelativeValueSignal,
} from "@/lib/domain/types";
import {
  executeDryRunOrderIntent,
  reconcileDryRunAttempts,
} from "@/lib/execution/dry-run-executor";
import { readLiveTradingSettings } from "@/lib/live/live-trading";
import { evaluateSystemTrust } from "@/lib/risk/system-trust";
import { evaluateOperationalRunbook } from "@/lib/runbook/operational-runbook";
import { generateRelativeValueSignals } from "@/lib/signals/relative-value";
import { emptyPaperPortfolio } from "@/lib/state/local-store";

const now = new Date(sampleMarketData.generatedAt);

describe("operational runbook", () => {
  it("returns a ready baseline when no operator action is active", () => {
    const paper = emptyPaperPortfolio();
    const dataQuality = healthyDataQuality();
    const systemTrust = evaluateSystemTrust({
      dataQuality,
      risk: healthyRisk(),
      paper,
      now,
    });
    const runbook = evaluateOperationalRunbook({
      dataQuality,
      systemTrust,
      schedulerStatus: schedulerOk(),
      alertDeliveries: [],
      paper,
      executionReconciliation: reconcileDryRunAttempts([], now),
      now,
    });

    expect(runbook.status).toBe("ready");
    expect(runbook.steps).toHaveLength(1);
    expect(runbook.steps[0].id).toBe("ready-paper-ops");
  });

  it("emits a stop procedure when the kill switch is active", () => {
    const paper = emptyPaperPortfolio();
    const dataQuality = healthyDataQuality();
    const killSwitch = {
      active: true,
      reason: "operator halt",
      activatedBy: "test",
      activatedAt: sampleMarketData.generatedAt,
      dashboardResetRequired: true,
    };
    const systemTrust = evaluateSystemTrust({
      dataQuality,
      risk: healthyRisk(),
      killSwitch,
      paper,
      now,
    });

    const runbook = evaluateOperationalRunbook({
      dataQuality,
      systemTrust,
      schedulerStatus: schedulerOk(),
      alertDeliveries: [],
      paper,
      executionReconciliation: reconcileDryRunAttempts([], now),
      killSwitch,
      now,
    });

    expect(runbook.status).toBe("blocked");
    expect(runbook.steps.some((step) => step.id === "stop-kill-switch")).toBe(true);
  });

  it("emits alert recovery when a provider delivery fails", () => {
    const paper = emptyPaperPortfolio();
    const dataQuality = healthyDataQuality();
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
    const systemTrust = evaluateSystemTrust({
      dataQuality,
      risk: healthyRisk(),
      alertDeliveries: [failedDelivery],
      paper,
      now,
    });

    const runbook = evaluateOperationalRunbook({
      dataQuality,
      systemTrust,
      schedulerStatus: schedulerOk(),
      alertDeliveries: [failedDelivery],
      paper,
      executionReconciliation: reconcileDryRunAttempts([], now),
      now,
    });

    expect(runbook.status).toBe("blocked");
    expect(runbook.steps.some((step) => step.id === "failed-alert-delivery")).toBe(true);
  });

  it("emits execution drift when reconciliation is blocked", () => {
    const paper = emptyPaperPortfolio();
    const dataQuality = healthyDataQuality();
    const [signal] = generateRelativeValueSignals(sampleMarketData);
    const liveEligibleSignal: RelativeValueSignal = {
      ...signal,
      eligibleForLiveTrading: true,
    };
    const attempt = executeDryRunOrderIntent({
      signal: liveEligibleSignal,
      requestedNotionalUsd: 100,
      settings: readLiveTradingSettings({
        LIVE_TRADING_ENABLED: "true",
        LIVE_KILL_SWITCH: "false",
      }),
      manualConfirmation: true,
      currentDailyPnlUsd: 0,
      now,
    });
    const brokenReconciliation = reconcileDryRunAttempts(
      [{ ...attempt, fills: [] }],
      now,
    );
    const systemTrust = evaluateSystemTrust({
      dataQuality,
      risk: healthyRisk(),
      paper,
      now,
    });

    const runbook = evaluateOperationalRunbook({
      dataQuality,
      systemTrust,
      schedulerStatus: schedulerOk(),
      alertDeliveries: [],
      paper,
      executionReconciliation: brokenReconciliation,
      now,
    });

    expect(runbook.status).toBe("blocked");
    expect(runbook.steps.some((step) => step.id === "execution-reconciliation")).toBe(true);
  });
});

function healthyDataQuality(): DataQualityReport {
  return {
    connectorId: "trusted-test",
    connectorLabel: "Trusted test connector",
    mode: "coingecko",
    status: "healthy",
    generatedAt: sampleMarketData.generatedAt,
    assessedAt: sampleMarketData.generatedAt,
    dataAgeMinutes: 0,
    marketCount: sampleMarketData.markets.length,
    issueCount: 0,
    criticalIssueCount: 0,
    fallbackUsed: false,
    fixtureBacked: false,
    blocksPaperTrading: false,
    summary: "Trusted test data is healthy.",
    issues: [],
  };
}

function schedulerOk() {
  return {
    running: false,
    cycleCount: 1,
    consecutiveErrors: 0,
    lastMessage: "Scheduler ok.",
  };
}

function healthyRisk(): MarketRiskState {
  return {
    state: "Green",
    score: 5,
    explanation: "No active risk alerts. Paper trading can run under normal local limits.",
    activeAlerts: [],
    tradingRestrictions: [],
    updatedAt: sampleMarketData.generatedAt,
  };
}
