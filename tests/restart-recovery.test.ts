import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createAuditTrail } from "@/lib/audit/audit-log";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { evaluateDataQuality } from "@/lib/data/quality";
import type { LiveTradingSettings } from "@/lib/domain/types";
import { buildDeploymentHealthReport } from "@/lib/ops/deployment-health";
import { computeFromData } from "@/lib/ops/recompute";
import { buildRestartRecoveryReport } from "@/lib/ops/restart-recovery";
import type { ValorLocalState } from "@/lib/state/local-store";
import { SqliteStateStore } from "@/lib/state/sqlite-store";

let dir: string | null = null;

afterEach(() => {
  if (dir) rmSync(dir, { recursive: true, force: true });
  dir = null;
});

describe("restart recovery", () => {
  it("reports that SQLite state survives a close and reopen cycle", () => {
    const path = tempDbPath();
    const before = buildRestartState();
    const first = new SqliteStateStore(path);

    first.write(before);
    first.close();

    const second = new SqliteStateStore(path);
    const after = second.read();
    second.close();

    const health = buildDeploymentHealthReport({
      state: after,
      liveSettings: safeLiveSettings(),
      now: new Date(sampleMarketData.generatedAt),
      schedulerStaleAfterMs: 60_000,
    });
    const report = buildRestartRecoveryReport({
      before,
      after,
      health,
      generatedAt: sampleMarketData.generatedAt,
    });

    expect(report.status).toBe("passed");
    expect(report.checks.every((check) => check.status === "passed")).toBe(true);
    expect(report.summary).toContain("preserved");
  });

  it("fails when restored paper evidence is missing", () => {
    const before = buildRestartState();
    const after = {
      ...before,
      paper: undefined,
    };
    const health = buildDeploymentHealthReport({
      state: after,
      liveSettings: safeLiveSettings(),
      now: new Date(sampleMarketData.generatedAt),
      schedulerStaleAfterMs: 60_000,
    });
    const report = buildRestartRecoveryReport({
      before,
      after,
      health,
      generatedAt: sampleMarketData.generatedAt,
    });

    expect(report.status).toBe("failed");
    expect(report.checks.find((check) => check.id === "paper-ledger")?.status).toBe("failed");
  });
});

function buildRestartState(): ValorLocalState {
  const dataQuality = evaluateDataQuality(sampleMarketData, {
    connectorId: "sample-fixtures",
    connectorLabel: "Deterministic sample market bundle",
    mode: "sample",
    assessedAt: sampleMarketData.generatedAt,
  });
  const computed = computeFromData(sampleMarketData, dataQuality);
  const auditEvents = createAuditTrail({
    data: sampleMarketData,
    signals: computed.signals,
    risk: computed.risk,
    backtest: computed.backtest,
    paper: computed.paperPreview,
  });

  return {
    lastRefreshAt: sampleMarketData.generatedAt,
    data: sampleMarketData,
    dataQuality,
    signals: computed.signals,
    risk: computed.risk,
    backtest: computed.backtest,
    paper: computed.paperPreview,
    systemTrust: computed.systemTrust,
    liveTradeAttempts: [],
    alertEvents: computed.alertEvents,
    alertDeliveries: [],
    alertRouterState: {
      lastSentByFingerprint: {},
      acknowledgedAlertIds: [],
    },
    auditEvents,
    schedulerStatus: {
      running: false,
      cycleCount: 2,
      consecutiveErrors: 0,
      lastSuccessAt: sampleMarketData.generatedAt,
      lastHeartbeatAt: sampleMarketData.generatedAt,
      lastMessage: "Scheduler ok.",
    },
    actionLog: [
      {
        id: "action-restart-fixture",
        timestamp: sampleMarketData.generatedAt,
        action: "restart.fixture",
        status: "ok",
        message: "Restart fixture.",
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

function tempDbPath(): string {
  dir = mkdtempSync(join(tmpdir(), "valor-restart-"));
  return join(dir, "valor.sqlite");
}
