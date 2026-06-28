import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import type {
  AlertDelivery,
  AlertEvent,
  AlertRouterState,
} from "@/lib/alerts/types";
import type {
  AuditEvent,
  BacktestReport,
  DataQualityReport,
  LiveTradeAttempt,
  MarketDataBundle,
  MarketRiskState,
  PaperPortfolio,
  RelativeValueSignal,
  SystemTrustVerdict,
} from "@/lib/domain/types";
import type { KillSwitchState } from "@/lib/kill-switch/kill-switch";

export interface ValorLocalState {
  lastRefreshAt?: string;
  data?: MarketDataBundle;
  dataQuality?: DataQualityReport;
  signals?: RelativeValueSignal[];
  risk?: MarketRiskState;
  backtest?: BacktestReport;
  paper?: PaperPortfolio;
  systemTrust?: SystemTrustVerdict;
  liveTradeAttempts: LiveTradeAttempt[];
  alertEvents: AlertEvent[];
  alertDeliveries: AlertDelivery[];
  alertRouterState: AlertRouterState;
  auditEvents: AuditEvent[];
  killSwitch?: KillSwitchState;
  schedulerStatus: SchedulerStatus;
  actionLog: Array<{
    id: string;
    timestamp: string;
    action: string;
    status: "ok" | "error" | "dry_run";
    message: string;
  }>;
}

export interface SchedulerStatus {
  running: boolean;
  activeRunId?: string;
  cycleCount: number;
  consecutiveErrors: number;
  lastRunStartedAt?: string;
  lastRunFinishedAt?: string;
  lastHeartbeatAt?: string;
  lastSkippedAt?: string;
  staleRunDetectedAt?: string;
  staleRunCount?: number;
  lastSuccessAt?: string;
  lastErrorAt?: string;
  lastDurationMs?: number;
  lastMessage: string;
  lastDataQualityStatus?: DataQualityReport["status"];
  lastPaperTrades?: number;
  lastAlertCount?: number;
}

export interface StateStore {
  read(): ValorLocalState;
  update(mutator: (state: ValorLocalState) => ValorLocalState): ValorLocalState;
  appendAction(input: {
    action: string;
    status: "ok" | "error" | "dry_run";
    message: string;
    timestamp?: string;
  }): ValorLocalState;
  write(state: ValorLocalState): void;
}

export const INITIAL_STATE: ValorLocalState = {
  alertEvents: [],
  alertDeliveries: [],
  liveTradeAttempts: [],
  alertRouterState: {
    lastSentByFingerprint: {},
    acknowledgedAlertIds: [],
  },
  auditEvents: [],
  schedulerStatus: {
    running: false,
    cycleCount: 0,
    consecutiveErrors: 0,
    lastMessage: "Scheduler has not run yet.",
  },
  actionLog: [],
};

export class LocalStateStore implements StateStore {
  constructor(
    private readonly path = process.env.VALOR_STATE_PATH ?? ".valor/state.json",
  ) {}

  read(): ValorLocalState {
    try {
      return {
        ...INITIAL_STATE,
        ...(JSON.parse(readFileSync(this.path, "utf8")) as Partial<ValorLocalState>),
      };
    } catch {
      return INITIAL_STATE;
    }
  }

  update(mutator: (state: ValorLocalState) => ValorLocalState): ValorLocalState {
    const next = mutator(this.read());
    this.write(next);
    return next;
  }

  appendAction(input: {
    action: string;
    status: "ok" | "error" | "dry_run";
    message: string;
    timestamp?: string;
  }): ValorLocalState {
    return this.update((state) => ({
      ...state,
      actionLog: [
        createActionLogEntry(input),
        ...state.actionLog,
      ].slice(0, 100),
    }));
  }

  write(state: ValorLocalState) {
    mkdirSync(dirname(this.path), { recursive: true });
    writeFileSync(this.path, JSON.stringify(state, null, 2));
  }
}

export function createActionLogEntry(input: {
  action: string;
  status: "ok" | "error" | "dry_run";
  message: string;
  timestamp?: string;
}): ValorLocalState["actionLog"][number] {
  return {
    id: `action:${Date.now()}:${Math.random().toString(16).slice(2)}`,
    timestamp: input.timestamp ?? new Date().toISOString(),
    action: input.action,
    status: input.status,
    message: input.message,
  };
}

export function emptyPaperPortfolio(): PaperPortfolio {
  return {
    cashUsd: 100_000,
    equityUsd: 100_000,
    dailyPnlUsd: 0,
    weeklyPnlUsd: 0,
    positions: [],
    trades: [],
    rejectedSignals: [],
    riskLimits: {
      maxPositionUsd: 12_500,
      maxPortfolioNotionalPct: 0.5,
      maxSignalRiskScore: 70,
      minLiquidityScore: 45,
      allowWhenRiskState: ["Green", "Yellow", "Red"],
      maxHoldingHours: 72,
    },
  };
}
