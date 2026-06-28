import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { sampleMarketData } from "@/lib/data/sample-market-data";
import { evaluateDataQuality } from "@/lib/data/quality";
import { computeFromData } from "@/lib/ops/recompute";
import { runSchedulerSoak } from "@/lib/ops/scheduler-soak";
import { LocalStateStore } from "@/lib/state/local-store";

let dir: string | null = null;

afterEach(() => {
  if (dir) rmSync(dir, { recursive: true, force: true });
  dir = null;
});

describe("scheduler soak harness", () => {
  it("runs repeated scheduler cycles and reports pass/fail evidence", async () => {
    const store = tempStore();
    const dataQuality = evaluateDataQuality(sampleMarketData, {
      connectorId: "sample-fixtures",
      connectorLabel: "Deterministic sample market bundle",
      mode: "sample",
      assessedAt: sampleMarketData.generatedAt,
    });
    const computed = computeFromData(sampleMarketData, dataQuality);
    const report = await runSchedulerSoak({
      store,
      cycles: 3,
      now: new Date(sampleMarketData.generatedAt),
      schedulerOptions: {
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
      },
    });
    const state = store.read();

    expect(report.status).toBe("passed");
    expect(report.cycleCount).toBe(3);
    expect(report.successCount).toBe(3);
    expect(report.failureCount).toBe(0);
    expect(report.finalHealth.status).toBe("ok");
    expect(state.schedulerStatus.cycleCount).toBe(3);
    expect(state.schedulerStatus.running).toBe(false);
    expect(state.paper?.trades.length).toBeGreaterThan(0);
  });

  it("fails when any scheduler cycle fails", async () => {
    const store = tempStore();
    const report = await runSchedulerSoak({
      store,
      cycles: 2,
      now: new Date(sampleMarketData.generatedAt),
      schedulerOptions: {
        refresh: async () => {
          throw new Error("connector unavailable");
        },
      },
    });

    expect(report.status).toBe("failed");
    expect(report.successCount).toBe(0);
    expect(report.failures.some((failure) => failure.code === "cycle-error")).toBe(true);
  });
});

function tempStore(): LocalStateStore {
  dir = mkdtempSync(join(tmpdir(), "valor-soak-"));
  return new LocalStateStore(join(dir, "state.json"));
}
