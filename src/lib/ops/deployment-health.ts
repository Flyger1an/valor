import type { LiveTradingSettings } from "@/lib/domain/types";
import { schedulerLeaseHealth } from "@/lib/ops/scheduler";
import type { ValorLocalState } from "@/lib/state/local-store";

export type DeploymentHealthStatus = "ok" | "degraded" | "blocked";

export interface DeploymentHealthCheck {
  id: string;
  status: DeploymentHealthStatus;
  summary: string;
}

export interface DeploymentHealthReport {
  id: string;
  generatedAt: string;
  status: DeploymentHealthStatus;
  ready: boolean;
  summary: string;
  checks: DeploymentHealthCheck[];
}

export function buildDeploymentHealthReport(input: {
  state: ValorLocalState;
  liveSettings: LiveTradingSettings;
  now?: Date;
  schedulerStaleAfterMs: number;
}): DeploymentHealthReport {
  const now = input.now ?? new Date();
  const generatedAt = now.toISOString();
  const checks = [
    marketDataCheck(input.state),
    dataQualityCheck(input.state),
    schedulerLeaseCheck(input.state, now, input.schedulerStaleAfterMs),
    schedulerErrorCheck(input.state),
    paperLedgerCheck(input.state),
    systemTrustCheck(input.state),
    evidenceTrailCheck(input.state),
    liveGuardrailCheck(input.liveSettings),
  ];
  const status = worstStatus(checks.map((check) => check.status));
  const attentionCount = checks.filter((check) => check.status !== "ok").length;

  return {
    id: `deployment-health:${generatedAt}`,
    generatedAt,
    status,
    ready: status !== "blocked",
    summary:
      status === "ok"
        ? "Deployment health checks are clean for paper-mode operation."
        : `${attentionCount} deployment health check(s) need attention before unattended confidence improves.`,
    checks,
  };
}

function marketDataCheck(state: ValorLocalState): DeploymentHealthCheck {
  if (!state.data) {
    return {
      id: "market-data",
      status: "degraded",
      summary: "No market-data snapshot is persisted yet.",
    };
  }

  return {
    id: "market-data",
    status: "ok",
    summary: `${state.data.markets.length} market snapshot(s), generated ${state.data.generatedAt}.`,
  };
}

function dataQualityCheck(state: ValorLocalState): DeploymentHealthCheck {
  if (!state.dataQuality) {
    return {
      id: "data-quality",
      status: "degraded",
      summary: "No data-quality report is persisted yet.",
    };
  }

  if (state.dataQuality.blocksPaperTrading) {
    return {
      id: "data-quality",
      status: "blocked",
      summary: state.dataQuality.summary,
    };
  }

  return {
    id: "data-quality",
    status: state.dataQuality.status === "healthy" ? "ok" : "degraded",
    summary: `${state.dataQuality.status}: ${state.dataQuality.summary}`,
  };
}

function schedulerLeaseCheck(
  state: ValorLocalState,
  now: Date,
  staleAfterMs: number,
): DeploymentHealthCheck {
  const lease = schedulerLeaseHealth(state.schedulerStatus, now, staleAfterMs);
  if (lease.state === "stale") {
    return {
      id: "scheduler-lease",
      status: "degraded",
      summary: `Scheduler run ${lease.activeRunId ?? "unknown"} has stale heartbeat ${lease.lastHeartbeatAt ?? "unknown"}.`,
    };
  }

  return {
    id: "scheduler-lease",
    status: "ok",
    summary:
      lease.state === "active"
        ? `Scheduler run ${lease.activeRunId ?? "unknown"} is heartbeating.`
        : "No active scheduler lease is persisted.",
  };
}

function schedulerErrorCheck(state: ValorLocalState): DeploymentHealthCheck {
  if (state.schedulerStatus.consecutiveErrors >= 3) {
    return {
      id: "scheduler-errors",
      status: "blocked",
      summary: `${state.schedulerStatus.consecutiveErrors} consecutive scheduler error(s): ${state.schedulerStatus.lastMessage}`,
    };
  }

  if (state.schedulerStatus.consecutiveErrors > 0) {
    return {
      id: "scheduler-errors",
      status: "degraded",
      summary: `${state.schedulerStatus.consecutiveErrors} consecutive scheduler error(s): ${state.schedulerStatus.lastMessage}`,
    };
  }

  return {
    id: "scheduler-errors",
    status: "ok",
    summary: "No consecutive scheduler errors are recorded.",
  };
}

function paperLedgerCheck(state: ValorLocalState): DeploymentHealthCheck {
  if (!state.paper) {
    return {
      id: "paper-ledger",
      status: "degraded",
      summary: "No paper ledger is persisted yet.",
    };
  }

  if (!Number.isFinite(state.paper.cashUsd) || !Number.isFinite(state.paper.equityUsd)) {
    return {
      id: "paper-ledger",
      status: "blocked",
      summary: "Paper ledger cash or equity is not finite.",
    };
  }

  return {
    id: "paper-ledger",
    status: "ok",
    summary: `${state.paper.positions.length} open position(s), ${state.paper.trades.length} ledger event(s).`,
  };
}

function systemTrustCheck(state: ValorLocalState): DeploymentHealthCheck {
  if (!state.systemTrust) {
    return {
      id: "system-trust",
      status: "degraded",
      summary: "No system-trust verdict is persisted yet.",
    };
  }

  return {
    id: "system-trust",
    status: state.systemTrust.status === "blocked" ? "blocked" : "ok",
    summary: `${state.systemTrust.status}: ${state.systemTrust.summary}`,
  };
}

function evidenceTrailCheck(state: ValorLocalState): DeploymentHealthCheck {
  const eventCount = state.auditEvents.length + state.actionLog.length;
  if (eventCount === 0) {
    return {
      id: "evidence-trail",
      status: "degraded",
      summary: "No audit or action-log evidence is persisted yet.",
    };
  }

  return {
    id: "evidence-trail",
    status: "ok",
    summary: `${state.auditEvents.length} audit event(s), ${state.actionLog.length} action-log event(s).`,
  };
}

function liveGuardrailCheck(
  settings: LiveTradingSettings,
): DeploymentHealthCheck {
  const issues = [
    settings.enabled ? "live trading env flag is enabled" : "",
    settings.dryRun ? "" : "dry-run mode is disabled",
    settings.manualConfirmationRequired ? "" : "manual confirmation is disabled",
    settings.killSwitchActive ? "" : "live kill switch is inactive",
    settings.maxLeverage <= 1 ? "" : "max leverage is above 1x",
  ].filter(Boolean);

  if (issues.length > 0) {
    return {
      id: "live-guardrails",
      status: "blocked",
      summary: `Unsafe v0.2 live guardrail setting(s): ${issues.join(", ")}.`,
    };
  }

  return {
    id: "live-guardrails",
    status: "ok",
    summary: "Live execution remains disabled, dry-run, manually confirmed, kill-switched, and 1x capped.",
  };
}

function worstStatus(statuses: DeploymentHealthStatus[]): DeploymentHealthStatus {
  if (statuses.includes("blocked")) return "blocked";
  if (statuses.includes("degraded")) return "degraded";
  return "ok";
}
