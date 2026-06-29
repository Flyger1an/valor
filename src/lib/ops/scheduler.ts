import { createAuditTrail } from "@/lib/audit/audit-log";
import { sendAlertNow } from "@/lib/ops/alert-delivery";
import { refreshAndPersistMarketState } from "@/lib/ops/recompute";
import { stepPaperPortfolio } from "@/lib/paper/paper-broker";
import { evaluateSystemTrust } from "@/lib/risk/system-trust";
import type { SchedulerStatus, StateStore } from "@/lib/state/local-store";
import { getStateStore } from "@/lib/state/store-factory";

let cycleRunning = false;
const DEFAULT_SCHEDULER_STALE_AFTER_MS = 15 * 60_000;

export interface SchedulerCycleResult {
  status: "success" | "error" | "skipped";
  startedAt: string;
  finishedAt: string;
  durationMs: number;
  message: string;
  runId?: string;
  staleRunRecovered?: boolean;
  dataQualityStatus?: string;
  paperTrades?: number;
  alerts?: number;
  deliveries?: number;
}

export interface SchedulerCycleOptions {
  store?: StateStore;
  now?: Date;
  sendAlerts?: boolean;
  alertLimit?: number;
  staleAfterMs?: number;
  runId?: string;
  refresh?: typeof refreshAndPersistMarketState;
}

export interface SchedulerLeaseHealth {
  state: "idle" | "active" | "stale";
  staleAfterMs: number;
  ageMs?: number;
  activeRunId?: string;
  lastHeartbeatAt?: string;
}

export async function runSchedulerCycle(
  options: SchedulerCycleOptions = {},
): Promise<SchedulerCycleResult> {
  const store = options.store ?? getStateStore();
  const now = options.now ?? new Date();
  const startedAt = now.toISOString();
  const staleAfterMs = options.staleAfterMs ?? DEFAULT_SCHEDULER_STALE_AFTER_MS;
  const runId = options.runId ?? createSchedulerRunId(startedAt);

  if (cycleRunning) {
    return recordSchedulerSkip({
      store,
      startedAt,
      message: "Scheduler cycle skipped because another cycle is running in this process.",
      now,
    });
  }

  const currentStatus = store.read().schedulerStatus;
  const lease = schedulerLeaseHealth(currentStatus, now, staleAfterMs);
  if (lease.state === "active") {
    return recordSchedulerSkip({
      store,
      startedAt,
      message: `Scheduler cycle skipped because run ${lease.activeRunId ?? "unknown"} is still heartbeating.`,
      now,
    });
  }

  cycleRunning = true;
  const staleRunRecovered = lease.state === "stale";
  store.update((state) => ({
    ...state,
    schedulerStatus: {
      ...state.schedulerStatus,
      running: true,
      activeRunId: runId,
      lastRunStartedAt: startedAt,
      lastHeartbeatAt: startedAt,
      staleRunDetectedAt: staleRunRecovered
        ? startedAt
        : state.schedulerStatus.staleRunDetectedAt,
      staleRunCount:
        (state.schedulerStatus.staleRunCount ?? 0) + (staleRunRecovered ? 1 : 0),
      lastMessage: staleRunRecovered
        ? `Scheduler recovered stale run ${lease.activeRunId ?? "unknown"} and started ${runId}.`
        : `Scheduler cycle ${runId} started.`,
    },
  }));

  try {
    const refresh = options.refresh ?? refreshAndPersistMarketState;
    const result = await refresh({ store, assessedAt: now });
    touchSchedulerHeartbeat(store, runId);
    const previousPaper = store.read().paper;
    const previousTradeIds = new Set(
      previousPaper?.trades.map((trade) => trade.id) ?? [],
    );
    const paper = stepPaperPortfolio({
      signals: result.computed.signals,
      risk: result.computed.risk,
      previousPortfolio: previousPaper,
      dataQuality: result.dataQuality,
      systemTrust: result.computed.systemTrust,
      marketData: result.data,
      now,
    });
    let deliveries = 0;

    const auditEvents = createAuditTrail({
      data: result.data,
      signals: result.computed.signals,
      risk: result.computed.risk,
      backtest: result.computed.backtest,
      paper,
    });

    store.update((state) => ({
      ...state,
      paper,
      systemTrust: result.computed.systemTrust,
      auditEvents,
    }));
    touchSchedulerHeartbeat(store, runId);

    if (options.sendAlerts) {
      const alerts = result.computed.alertEvents.slice(0, options.alertLimit ?? 3);
      for (const alert of alerts) {
        const sent = await sendAlertNow(alert, { store });
        deliveries += sent.deliveries.length;
        touchSchedulerHeartbeat(store, runId);
      }
    }

    const finishedAt = new Date().toISOString();
    const durationMs = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
    const cyclePaperTrades = paper.trades.filter(
      (trade) => !previousTradeIds.has(trade.id),
    ).length;
    const message = `Scheduler refreshed ${result.data.markets.length} markets, stored ${result.computed.signals.length} signals, recorded ${cyclePaperTrades} paper ledger event(s), tracked ${result.computed.alertEvents.length} alert event(s), applied ${result.computed.edgePolicy.blockedSignalCount} edge-policy block(s), and system trust is ${result.computed.systemTrust.status}.`;
    const current = store.read();
    const nextSchedulerStatus: SchedulerStatus = {
      ...current.schedulerStatus,
      running: false,
      activeRunId: undefined,
      cycleCount: current.schedulerStatus.cycleCount + 1,
      consecutiveErrors: 0,
      lastRunStartedAt: startedAt,
      lastRunFinishedAt: finishedAt,
      lastHeartbeatAt: finishedAt,
      lastSuccessAt: finishedAt,
      lastDurationMs: durationMs,
      lastMessage: message,
      lastDataQualityStatus: result.dataQuality.status,
      lastPaperTrades: cyclePaperTrades,
      lastAlertCount: result.computed.alertEvents.length,
    };

    store.update((state) => ({
      ...state,
      schedulerStatus: nextSchedulerStatus,
      systemTrust: evaluateSystemTrust({
        dataQuality: result.dataQuality,
        risk: result.computed.risk,
        schedulerStatus: nextSchedulerStatus,
        alertDeliveries: state.alertDeliveries,
        killSwitch: state.killSwitch,
        paper,
        now: new Date(finishedAt),
      }),
    }));
    store.appendAction({
      action: "scheduler.cycle",
      status: options.sendAlerts ? "ok" : "dry_run",
      message,
      timestamp: finishedAt,
    });

    return {
      status: "success",
      startedAt,
      finishedAt,
      durationMs,
      message,
      runId,
      staleRunRecovered,
      dataQualityStatus: result.dataQuality.status,
      paperTrades: cyclePaperTrades,
      alerts: result.computed.alertEvents.length,
      deliveries,
    };
  } catch (error) {
    const finishedAt = new Date().toISOString();
    const durationMs = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
    const message = `Scheduler cycle failed: ${
      error instanceof Error ? error.message : "unknown error"
    }`;
    const current = store.read();
    const nextSchedulerStatus: SchedulerStatus = {
      ...current.schedulerStatus,
      running: false,
      activeRunId: undefined,
      cycleCount: current.schedulerStatus.cycleCount + 1,
      consecutiveErrors: current.schedulerStatus.consecutiveErrors + 1,
      lastRunStartedAt: startedAt,
      lastRunFinishedAt: finishedAt,
      lastHeartbeatAt: finishedAt,
      lastErrorAt: finishedAt,
      lastDurationMs: durationMs,
      lastMessage: message,
    };

    store.update((state) => ({
      ...state,
      schedulerStatus: nextSchedulerStatus,
      systemTrust: evaluateSystemTrust({
        dataQuality: state.dataQuality,
        risk: state.risk,
        schedulerStatus: nextSchedulerStatus,
        alertDeliveries: state.alertDeliveries,
        killSwitch: state.killSwitch,
        paper: state.paper,
        now: new Date(finishedAt),
      }),
    }));
    store.appendAction({
      action: "scheduler.cycle",
      status: "error",
      message,
      timestamp: finishedAt,
    });

    return {
      status: "error",
      startedAt,
      finishedAt,
      durationMs,
      message,
      runId,
      staleRunRecovered,
    };
  } finally {
    cycleRunning = false;
  }
}

export function schedulerConfigFromEnv(env: NodeJS.ProcessEnv = process.env) {
  const intervalMs = numberFromEnv(env.SCHEDULER_INTERVAL_MS, 5 * 60_000);
  return {
    intervalMs,
    sendAlerts: env.SCHEDULER_SEND_ALERTS === "true",
    alertLimit: numberFromEnv(env.SCHEDULER_ALERT_LIMIT, 3),
    runOnStart: env.SCHEDULER_RUN_ON_START !== "false",
    staleAfterMs: numberFromEnv(
      env.SCHEDULER_STALE_AFTER_MS,
      Math.max(intervalMs * 3, DEFAULT_SCHEDULER_STALE_AFTER_MS),
    ),
  };
}

export function schedulerLeaseHealth(
  status: SchedulerStatus,
  now: Date,
  staleAfterMs: number,
): SchedulerLeaseHealth {
  if (!status.running) {
    return {
      state: "idle",
      staleAfterMs,
      activeRunId: status.activeRunId,
      lastHeartbeatAt: status.lastHeartbeatAt,
    };
  }

  const heartbeat = status.lastHeartbeatAt ?? status.lastRunStartedAt;
  const heartbeatMs = heartbeat ? new Date(heartbeat).getTime() : Number.NaN;
  const ageMs = Number.isFinite(heartbeatMs)
    ? now.getTime() - heartbeatMs
    : undefined;

  return {
    state: ageMs !== undefined && ageMs <= staleAfterMs ? "active" : "stale",
    staleAfterMs,
    ageMs,
    activeRunId: status.activeRunId,
    lastHeartbeatAt: heartbeat,
  };
}

export function touchSchedulerHeartbeat(
  store: StateStore,
  runId: string,
  now = new Date(),
): string {
  const lastHeartbeatAt = now.toISOString();

  store.update((state) => {
    if (state.schedulerStatus.activeRunId !== runId) return state;

    return {
      ...state,
      schedulerStatus: {
        ...state.schedulerStatus,
        lastHeartbeatAt,
      },
    };
  });

  return lastHeartbeatAt;
}

function recordSchedulerSkip(input: {
  store: StateStore;
  startedAt: string;
  message: string;
  now: Date;
}): SchedulerCycleResult {
  const finishedAt = input.now.toISOString();
  input.store.update((state) => ({
    ...state,
    schedulerStatus: {
      ...state.schedulerStatus,
      lastSkippedAt: finishedAt,
      lastMessage: input.message,
    },
  }));
  input.store.appendAction({
    action: "scheduler.cycle",
    status: "dry_run",
    message: input.message,
    timestamp: finishedAt,
  });

  return {
    status: "skipped",
    startedAt: input.startedAt,
    finishedAt,
    durationMs: 0,
    message: input.message,
  };
}

function createSchedulerRunId(startedAt: string): string {
  return `scheduler:${startedAt}:${Math.random().toString(16).slice(2, 10)}`;
}

function numberFromEnv(value: string | undefined, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}
