import { describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { evaluateDataQuality } from "@/lib/data/quality";
import type { LiveTradingSettings } from "@/lib/domain/types";
import { buildDeploymentHealthReport } from "@/lib/ops/deployment-health";
import { evaluateMarketRisk } from "@/lib/risk/risk-engine";
import { evaluateSystemTrust } from "@/lib/risk/system-trust";
import { emptyPaperPortfolio, INITIAL_STATE } from "@/lib/state/local-store";

describe("deployment health", () => {
  it("reports ready when the paper-mode deployment evidence is coherent", () => {
    const state = readyState();
    const report = buildDeploymentHealthReport({
      state,
      liveSettings: safeLiveSettings(),
      now: new Date(sampleMarketData.generatedAt),
      schedulerStaleAfterMs: 60_000,
    });

    expect(report.ready).toBe(true);
    expect(report.status).toBe("ok");
    expect(report.checks.every((check) => check.status === "ok")).toBe(true);
  });

  it("degrades without mutating when evidence has not been produced yet", () => {
    const report = buildDeploymentHealthReport({
      state: { ...INITIAL_STATE },
      liveSettings: safeLiveSettings(),
      now: new Date(sampleMarketData.generatedAt),
      schedulerStaleAfterMs: 60_000,
    });

    expect(report.ready).toBe(true);
    expect(report.status).toBe("degraded");
    expect(report.checks.some((check) => check.id === "market-data" && check.status === "degraded")).toBe(true);
  });

  it("flags stale scheduler leases as degraded", () => {
    const state = readyState();
    state.schedulerStatus = {
      ...state.schedulerStatus,
      running: true,
      activeRunId: "scheduler:stale",
      lastHeartbeatAt: "2026-06-22T16:00:00.000Z",
    };

    const report = buildDeploymentHealthReport({
      state,
      liveSettings: safeLiveSettings(),
      now: new Date("2026-06-22T17:00:00.000Z"),
      schedulerStaleAfterMs: 60_000,
    });

    expect(report.status).toBe("degraded");
    expect(report.checks.find((check) => check.id === "scheduler-lease")?.status).toBe("degraded");
  });

  it("blocks unsafe live guardrail settings for v0.2 deployment", () => {
    const report = buildDeploymentHealthReport({
      state: readyState(),
      liveSettings: {
        ...safeLiveSettings(),
        enabled: true,
        dryRun: false,
      },
      now: new Date(sampleMarketData.generatedAt),
      schedulerStaleAfterMs: 60_000,
    });

    expect(report.ready).toBe(false);
    expect(report.status).toBe("blocked");
    expect(report.checks.find((check) => check.id === "live-guardrails")?.summary).toContain("live trading env flag");
  });
});

function readyState() {
  const dataQuality = evaluateDataQuality(sampleMarketData, {
    connectorId: "sample-fixtures",
    connectorLabel: "Deterministic sample market bundle",
    mode: "sample",
    assessedAt: sampleMarketData.generatedAt,
  });
  const risk = evaluateMarketRisk(sampleMarketData);
  const paper = emptyPaperPortfolio();
  const schedulerStatus = {
    ...INITIAL_STATE.schedulerStatus,
    cycleCount: 4,
    lastSuccessAt: sampleMarketData.generatedAt,
    lastHeartbeatAt: sampleMarketData.generatedAt,
    lastMessage: "Scheduler ok.",
  };
  const systemTrust = evaluateSystemTrust({
    dataQuality,
    risk,
    schedulerStatus,
    paper,
    now: new Date(sampleMarketData.generatedAt),
  });

  return {
    ...INITIAL_STATE,
    data: sampleMarketData,
    dataQuality,
    risk,
    paper,
    schedulerStatus,
    systemTrust,
    auditEvents: [
      {
        id: "audit-health-fixture",
        timestamp: sampleMarketData.generatedAt,
        actor: "system",
        action: "health.fixture",
        summary: "Health fixture.",
        metadata: {},
      },
    ],
    actionLog: [
      {
        id: "action-health-fixture",
        timestamp: sampleMarketData.generatedAt,
        action: "health.fixture",
        status: "ok" as const,
        message: "Health fixture.",
      },
    ],
  };
}

function safeLiveSettings(): LiveTradingSettings {
  return {
    enabled: false,
    dryRun: true,
    manualConfirmationRequired: true,
    killSwitchActive: true,
    maxTradeUsd: 250,
    dailyLossLimitUsd: 100,
    maxLeverage: 1,
    venueAllowlist: ["coinbase", "kraken"],
    assetAllowlist: ["BTC", "ETH", "USDC", "USD"],
  };
}
