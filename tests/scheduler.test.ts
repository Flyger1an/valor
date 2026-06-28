import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { evaluateDataQuality } from "@/lib/data/quality";
import {
  runSchedulerCycle,
  schedulerConfigFromEnv,
  schedulerLeaseHealth,
} from "@/lib/ops/scheduler";
import { computeFromData } from "@/lib/ops/recompute";
import { LocalStateStore } from "@/lib/state/local-store";

let dir: string | null = null;

afterEach(() => {
  if (dir) rmSync(dir, { recursive: true, force: true });
  dir = null;
});

describe("scheduler evidence loop", () => {
  it("runs refresh, stores paper preview, and records scheduler status", async () => {
    const store = tempStore();
    const dataQuality = evaluateDataQuality(sampleMarketData, {
      connectorId: "sample-fixtures",
      connectorLabel: "Deterministic sample market bundle",
      mode: "sample",
      assessedAt: sampleMarketData.generatedAt,
    });
    const computed = computeFromData(sampleMarketData, dataQuality);
    const result = await runSchedulerCycle({
      store,
      now: new Date(sampleMarketData.generatedAt),
      refresh: async ({ store: cycleStore }) => {
        cycleStore!.update((state) => ({
          ...state,
          lastRefreshAt: sampleMarketData.generatedAt,
          data: sampleMarketData,
          dataQuality,
          signals: computed.signals,
          risk: computed.risk,
          backtest: computed.backtest,
          alertEvents: computed.alertEvents,
        }));

        return {
          connector: {
            id: "sample-fixtures",
            label: "Deterministic sample market bundle",
            mode: "sample" as const,
            needsApiKey: false,
            fetchLatest: async () => sampleMarketData,
          },
          data: sampleMarketData,
          dataQuality,
          computed,
          state: cycleStore!.read(),
        };
      },
    });
    const state = store.read();

    expect(result.status).toBe("success");
    expect(state.paper?.trades.length).toBe(computed.paperPreview.trades.length);
    expect(state.auditEvents.length).toBeGreaterThan(0);
    expect(state.schedulerStatus.running).toBe(false);
    expect(state.schedulerStatus.activeRunId).toBeUndefined();
    expect(state.schedulerStatus.cycleCount).toBe(1);
    expect(state.schedulerStatus.consecutiveErrors).toBe(0);
    expect(state.schedulerStatus.lastHeartbeatAt).toBeDefined();
    expect(state.schedulerStatus.lastDataQualityStatus).toBe("healthy");
    expect(state.schedulerStatus.staleRunCount ?? 0).toBe(0);
    expect(state.actionLog.some((entry) => entry.action === "scheduler.cycle")).toBe(true);
  });

  it("skips a fresh persisted scheduler lease", async () => {
    const store = tempStore();
    const now = new Date("2026-06-27T12:00:00.000Z");
    let refreshCalled = false;

    store.update((state) => ({
      ...state,
      schedulerStatus: {
        ...state.schedulerStatus,
        running: true,
        activeRunId: "scheduler:fresh",
        lastRunStartedAt: "2026-06-27T11:59:50.000Z",
        lastHeartbeatAt: "2026-06-27T11:59:55.000Z",
        lastMessage: "Scheduler cycle scheduler:fresh started.",
      },
    }));

    const result = await runSchedulerCycle({
      store,
      now,
      staleAfterMs: 10_000,
      refresh: async () => {
        refreshCalled = true;
        throw new Error("should not refresh");
      },
    });
    const state = store.read();

    expect(result.status).toBe("skipped");
    expect(refreshCalled).toBe(false);
    expect(state.schedulerStatus.running).toBe(true);
    expect(state.schedulerStatus.activeRunId).toBe("scheduler:fresh");
    expect(state.schedulerStatus.lastSkippedAt).toBe(now.toISOString());
    expect(state.actionLog[0].message).toContain("still heartbeating");
  });

  it("recovers and records a stale persisted scheduler lease", async () => {
    const store = tempStore();
    const now = new Date("2026-06-27T12:00:00.000Z");
    const dataQuality = evaluateDataQuality(sampleMarketData, {
      connectorId: "sample-fixtures",
      connectorLabel: "Deterministic sample market bundle",
      mode: "sample",
      assessedAt: sampleMarketData.generatedAt,
    });
    const computed = computeFromData(sampleMarketData, dataQuality);

    store.update((state) => ({
      ...state,
      schedulerStatus: {
        ...state.schedulerStatus,
        running: true,
        activeRunId: "scheduler:stale",
        lastRunStartedAt: "2026-06-27T11:30:00.000Z",
        lastHeartbeatAt: "2026-06-27T11:30:10.000Z",
        lastMessage: "Scheduler cycle scheduler:stale started.",
      },
    }));

    const before = schedulerLeaseHealth(
      store.read().schedulerStatus,
      now,
      60_000,
    );
    const result = await runSchedulerCycle({
      store,
      now,
      staleAfterMs: 60_000,
      runId: "scheduler:recovered",
      refresh: async ({ store: cycleStore }) => {
        cycleStore!.update((state) => ({
          ...state,
          lastRefreshAt: sampleMarketData.generatedAt,
          data: sampleMarketData,
          dataQuality,
          signals: computed.signals,
          risk: computed.risk,
          backtest: computed.backtest,
          alertEvents: computed.alertEvents,
        }));

        return {
          connector: {
            id: "sample-fixtures",
            label: "Deterministic sample market bundle",
            mode: "sample" as const,
            needsApiKey: false,
            fetchLatest: async () => sampleMarketData,
          },
          data: sampleMarketData,
          dataQuality,
          computed,
          state: cycleStore!.read(),
        };
      },
    });
    const state = store.read();

    expect(before.state).toBe("stale");
    expect(result.status).toBe("success");
    expect(result.staleRunRecovered).toBe(true);
    expect(state.schedulerStatus.running).toBe(false);
    expect(state.schedulerStatus.activeRunId).toBeUndefined();
    expect(state.schedulerStatus.staleRunCount).toBe(1);
    expect(state.schedulerStatus.staleRunDetectedAt).toBe(now.toISOString());
    expect(state.schedulerStatus.lastSuccessAt).toBeDefined();
  });

  it("records failed cycles without throwing", async () => {
    const store = tempStore();
    const result = await runSchedulerCycle({
      store,
      now: new Date("2026-06-27T12:00:00.000Z"),
      refresh: async () => {
        throw new Error("connector unavailable");
      },
    });
    const state = store.read();

    expect(result.status).toBe("error");
    expect(result.message).toContain("connector unavailable");
    expect(state.schedulerStatus.running).toBe(false);
    expect(state.schedulerStatus.cycleCount).toBe(1);
    expect(state.schedulerStatus.consecutiveErrors).toBe(1);
    expect(state.schedulerStatus.lastErrorAt).toBeDefined();
    expect(state.actionLog[0].status).toBe("error");
  });

  it("reads interval and alert settings from env", () => {
    const config = schedulerConfigFromEnv({
      SCHEDULER_INTERVAL_MS: "120000",
      SCHEDULER_SEND_ALERTS: "true",
      SCHEDULER_ALERT_LIMIT: "5",
      SCHEDULER_RUN_ON_START: "false",
      SCHEDULER_STALE_AFTER_MS: "450000",
    });

    expect(config.intervalMs).toBe(120000);
    expect(config.sendAlerts).toBe(true);
    expect(config.alertLimit).toBe(5);
    expect(config.runOnStart).toBe(false);
    expect(config.staleAfterMs).toBe(450000);
  });
});

function tempStore(): LocalStateStore {
  dir = mkdtempSync(join(tmpdir(), "valor-scheduler-"));
  return new LocalStateStore(join(dir, "state.json"));
}
