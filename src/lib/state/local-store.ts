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
  MarketDataBundle,
  MarketRiskState,
  PaperPortfolio,
  RelativeValueSignal,
} from "@/lib/domain/types";
import type { KillSwitchState } from "@/lib/kill-switch/kill-switch";
import {
  loadValorStateFromSqlite,
  persistValorStateToSqlite,
} from "@/lib/state/sqlite-persistence";

export interface ValorLocalState {
  lastRefreshAt?: string;
  data?: MarketDataBundle;
  signals?: RelativeValueSignal[];
  risk?: MarketRiskState;
  backtest?: BacktestReport;
  paper?: PaperPortfolio;
  alertEvents: AlertEvent[];
  alertDeliveries: AlertDelivery[];
  alertRouterState: AlertRouterState;
  auditEvents: AuditEvent[];
  killSwitch?: KillSwitchState;
  actionLog: Array<{
    id: string;
    timestamp: string;
    action: string;
    status: "ok" | "error" | "dry_run";
    message: string;
  }>;
}

const INITIAL_STATE: ValorLocalState = {
  alertEvents: [],
  alertDeliveries: [],
  alertRouterState: {
    lastSentByFingerprint: {},
    acknowledgedAlertIds: [],
  },
  auditEvents: [],
  actionLog: [],
};

export class LocalStateStore {
  constructor(
    private readonly path = process.env.VALOR_STATE_PATH ?? ".valor/state.json",
  ) {}

  read(): ValorLocalState {
    const fromSqlite = loadValorStateFromSqlite();
    if (fromSqlite) return { ...INITIAL_STATE, ...fromSqlite };

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
        {
          id: `action:${Date.now()}:${Math.random().toString(16).slice(2)}`,
          timestamp: input.timestamp ?? new Date().toISOString(),
          action: input.action,
          status: input.status,
          message: input.message,
        },
        ...state.actionLog,
      ].slice(0, 100),
    }));
  }

  write(state: ValorLocalState) {
    persistValorStateToSqlite(state);
    mkdirSync(dirname(this.path), { recursive: true });
    writeFileSync(this.path, JSON.stringify(state, null, 2));
  }
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
    },
  };
}
