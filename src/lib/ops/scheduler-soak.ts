import { readLiveTradingSettings } from "@/lib/live/live-trading";
import { buildDeploymentHealthReport } from "@/lib/ops/deployment-health";
import {
  runSchedulerCycle,
  schedulerConfigFromEnv,
  type SchedulerCycleOptions,
  type SchedulerCycleResult,
} from "@/lib/ops/scheduler";
import type { StateStore } from "@/lib/state/local-store";
import { getStateStore } from "@/lib/state/store-factory";

export type SchedulerSoakStatus = "passed" | "failed";

export interface SchedulerSoakFailure {
  cycle: number;
  code: string;
  message: string;
}

export interface SchedulerSoakReport {
  id: string;
  status: SchedulerSoakStatus;
  startedAt: string;
  finishedAt: string;
  cycleCount: number;
  successCount: number;
  failureCount: number;
  results: SchedulerCycleResult[];
  failures: SchedulerSoakFailure[];
  finalHealth: ReturnType<typeof buildDeploymentHealthReport>;
}

export async function runSchedulerSoak(input: {
  cycles?: number;
  store?: StateStore;
  now?: Date;
  cycleSpacingMs?: number;
  schedulerOptions?: Omit<SchedulerCycleOptions, "store" | "now">;
} = {}): Promise<SchedulerSoakReport> {
  const cycles = Math.max(1, Math.floor(input.cycles ?? 3));
  const store = input.store ?? getStateStore();
  const startedAt = (input.now ?? new Date()).toISOString();
  const results: SchedulerCycleResult[] = [];
  const failures: SchedulerSoakFailure[] = [];

  for (let index = 0; index < cycles; index += 1) {
    const now = new Date(
      (input.now?.getTime() ?? Date.now()) + index * (input.cycleSpacingMs ?? 60_000),
    );
    const result = await runSchedulerCycle({
      ...input.schedulerOptions,
      store,
      now,
    });
    results.push(result);

    if (result.status !== "success") {
      failures.push({
        cycle: index + 1,
        code: `cycle-${result.status}`,
        message: result.message,
      });
      continue;
    }

    const state = store.read();
    if (state.schedulerStatus.running || state.schedulerStatus.activeRunId) {
      failures.push({
        cycle: index + 1,
        code: "scheduler-left-running",
        message: "Scheduler status remained running after a completed cycle.",
      });
    }
    if (!state.paper) {
      failures.push({
        cycle: index + 1,
        code: "missing-paper-ledger",
        message: "No paper ledger was persisted after scheduler cycle.",
      });
    }
    if (state.schedulerStatus.consecutiveErrors > 0) {
      failures.push({
        cycle: index + 1,
        code: "scheduler-error-streak",
        message: `${state.schedulerStatus.consecutiveErrors} consecutive scheduler error(s) remain.`,
      });
    }
  }

  const finishedAt = new Date().toISOString();
  const schedulerConfig = schedulerConfigFromEnv();
  const finalHealth = buildDeploymentHealthReport({
    state: store.read(),
    liveSettings: readLiveTradingSettings(),
    schedulerStaleAfterMs: schedulerConfig.staleAfterMs,
  });

  if (finalHealth.status === "blocked") {
    failures.push({
      cycle: cycles,
      code: "deployment-health-blocked",
      message: finalHealth.summary,
    });
  }

  return {
    id: `scheduler-soak:${startedAt}`,
    status: failures.length === 0 ? "passed" : "failed",
    startedAt,
    finishedAt,
    cycleCount: cycles,
    successCount: results.filter((result) => result.status === "success").length,
    failureCount: failures.length,
    results,
    failures,
    finalHealth,
  };
}
