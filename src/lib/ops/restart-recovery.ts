import type { DeploymentHealthReport } from "@/lib/ops/deployment-health";
import type { ValorLocalState } from "@/lib/state/local-store";

export type RestartRecoveryStatus = "passed" | "degraded" | "failed";

export interface RestartRecoveryCheck {
  id: string;
  status: RestartRecoveryStatus;
  summary: string;
}

export interface RestartRecoveryReport {
  id: string;
  generatedAt: string;
  status: RestartRecoveryStatus;
  summary: string;
  checks: RestartRecoveryCheck[];
}

export function buildRestartRecoveryReport(input: {
  before: ValorLocalState;
  after: ValorLocalState;
  health: DeploymentHealthReport;
  generatedAt?: string;
}): RestartRecoveryReport {
  const generatedAt = input.generatedAt ?? new Date().toISOString();
  const checks = [
    dataSnapshotCheck(input.before, input.after),
    dataQualityCheck(input.before, input.after),
    signalSnapshotCheck(input.before, input.after),
    paperLedgerCheck(input.before, input.after),
    schedulerStatusCheck(input.before, input.after),
    evidenceTrailCheck(input.before, input.after),
    healthCheck(input.health),
  ];
  const status = worstStatus(checks.map((check) => check.status));
  const issueCount = checks.filter((check) => check.status !== "passed").length;

  return {
    id: `restart-recovery:${generatedAt}`,
    generatedAt,
    status,
    summary:
      status === "passed"
        ? "Restart recovery preserved the current evidence loop state."
        : `${issueCount} restart recovery check(s) need attention before droplet confidence improves.`,
    checks,
  };
}

function dataSnapshotCheck(
  before: ValorLocalState,
  after: ValorLocalState,
): RestartRecoveryCheck {
  if (!before.data || !after.data) {
    return {
      id: "market-data",
      status: "failed",
      summary: "Market data snapshot is missing before or after restart.",
    };
  }

  const passed = before.data.generatedAt === after.data.generatedAt;
  return {
    id: "market-data",
    status: passed ? "passed" : "failed",
    summary: passed
      ? `Market data snapshot survived restart: ${after.data.generatedAt}.`
      : `Market data generatedAt changed from ${before.data.generatedAt} to ${after.data.generatedAt}.`,
  };
}

function dataQualityCheck(
  before: ValorLocalState,
  after: ValorLocalState,
): RestartRecoveryCheck {
  if (!before.dataQuality || !after.dataQuality) {
    return {
      id: "data-quality",
      status: "failed",
      summary: "Data-quality report is missing before or after restart.",
    };
  }

  const passed =
    before.dataQuality.generatedAt === after.dataQuality.generatedAt &&
    before.dataQuality.status === after.dataQuality.status;
  return {
    id: "data-quality",
    status: passed ? "passed" : "failed",
    summary: passed
      ? `Data-quality report survived restart with status ${after.dataQuality.status}.`
      : "Data-quality report changed across restart.",
  };
}

function signalSnapshotCheck(
  before: ValorLocalState,
  after: ValorLocalState,
): RestartRecoveryCheck {
  const beforeCount = before.signals?.length ?? 0;
  const afterCount = after.signals?.length ?? 0;

  return {
    id: "signals",
    status: beforeCount > 0 && beforeCount === afterCount ? "passed" : "failed",
    summary: `${afterCount} signal(s) restored after restart; before restart had ${beforeCount}.`,
  };
}

function paperLedgerCheck(
  before: ValorLocalState,
  after: ValorLocalState,
): RestartRecoveryCheck {
  if (!before.paper || !after.paper) {
    return {
      id: "paper-ledger",
      status: "failed",
      summary: "Paper ledger is missing before or after restart.",
    };
  }

  const passed =
    before.paper.trades.length === after.paper.trades.length &&
    before.paper.positions.length === after.paper.positions.length &&
    before.paper.rejectedSignals.length === after.paper.rejectedSignals.length &&
    Number.isFinite(after.paper.cashUsd) &&
    Number.isFinite(after.paper.equityUsd);

  return {
    id: "paper-ledger",
    status: passed ? "passed" : "failed",
    summary: `${after.paper.positions.length} position(s), ${after.paper.trades.length} trade event(s), ${after.paper.rejectedSignals.length} rejected signal(s) restored after restart.`,
  };
}

function schedulerStatusCheck(
  before: ValorLocalState,
  after: ValorLocalState,
): RestartRecoveryCheck {
  const passed =
    before.schedulerStatus.cycleCount === after.schedulerStatus.cycleCount &&
    after.schedulerStatus.running === false;

  return {
    id: "scheduler-status",
    status: passed ? "passed" : "degraded",
    summary: passed
      ? `Scheduler status restored at cycle ${after.schedulerStatus.cycleCount}.`
      : "Scheduler status changed across restart or still reports running.",
  };
}

function evidenceTrailCheck(
  before: ValorLocalState,
  after: ValorLocalState,
): RestartRecoveryCheck {
  const beforeCount = before.auditEvents.length + before.actionLog.length;
  const afterCount = after.auditEvents.length + after.actionLog.length;
  const passed = beforeCount > 0 && beforeCount === afterCount;

  return {
    id: "evidence-trail",
    status: passed ? "passed" : "degraded",
    summary: `${afterCount} evidence event(s) restored after restart; before restart had ${beforeCount}.`,
  };
}

function healthCheck(health: DeploymentHealthReport): RestartRecoveryCheck {
  return {
    id: "deployment-health",
    status: health.status === "blocked" ? "failed" : "passed",
    summary: `Post-restart deployment health is ${health.status}: ${health.summary}`,
  };
}

function worstStatus(statuses: RestartRecoveryStatus[]): RestartRecoveryStatus {
  if (statuses.includes("failed")) return "failed";
  if (statuses.includes("degraded")) return "degraded";
  return "passed";
}
