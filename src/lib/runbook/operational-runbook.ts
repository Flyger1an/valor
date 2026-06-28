import type { AlertDelivery } from "@/lib/alerts/types";
import type {
  DataQualityReport,
  ExecutionReconciliationReport,
  OperationalRunbookReport,
  OperationalRunbookStep,
  PaperPortfolio,
  SystemTrustVerdict,
} from "@/lib/domain/types";
import type { KillSwitchState } from "@/lib/kill-switch/kill-switch";
import type { SchedulerStatus } from "@/lib/state/local-store";

export function evaluateOperationalRunbook(input: {
  dataQuality?: DataQualityReport;
  systemTrust: SystemTrustVerdict;
  schedulerStatus: SchedulerStatus;
  alertDeliveries: AlertDelivery[];
  paper: PaperPortfolio;
  executionReconciliation: ExecutionReconciliationReport;
  killSwitch?: KillSwitchState;
  now?: Date;
}): OperationalRunbookReport {
  const generatedAt = (input.now ?? new Date()).toISOString();
  const steps: OperationalRunbookStep[] = [];

  addStopResumeSteps(steps, input);
  addDataSteps(steps, input.dataQuality);
  addAlertSteps(steps, input.alertDeliveries);
  addSchedulerSteps(steps, input.schedulerStatus);
  addPaperDriftSteps(steps, input.systemTrust, input.paper);
  addExecutionSteps(steps, input.executionReconciliation);

  if (steps.length === 0) {
    steps.push({
      id: "ready-paper-ops",
      area: "stop_resume",
      title: "Ready: paper operations can continue",
      status: "ready",
      severity: "info",
      trigger: "No active runbook conditions.",
      action:
        "Continue scheduled refreshes, paper ledger updates, alert previews, and dry-run evidence collection.",
      verification:
        "System trust remains trusted or caution-only, scheduler errors stay at zero, and reconciliation stays clean.",
      evidence: input.systemTrust.summary,
      blocksPaperTrading: false,
      blocksLiveTrading: input.systemTrust.blocksLiveTrading,
    });
  }

  const blockedCount = steps.filter((step) => step.status === "blocked").length;
  const actionRequiredCount = steps.filter(
    (step) => step.status === "action_required",
  ).length;
  const criticalStepCount = steps.filter(
    (step) => step.severity === "critical",
  ).length;
  const status =
    blockedCount > 0 || criticalStepCount > 0
      ? "blocked"
      : actionRequiredCount > 0
        ? "attention"
        : "ready";

  return {
    id: `operational-runbook:${generatedAt}`,
    generatedAt,
    status,
    summary: summaryFor(status, steps),
    stepCount: steps.length,
    actionRequiredCount,
    blockedCount,
    criticalStepCount,
    steps,
  };
}

function addStopResumeSteps(
  steps: OperationalRunbookStep[],
  input: {
    systemTrust: SystemTrustVerdict;
    killSwitch?: KillSwitchState;
  },
) {
  if (input.killSwitch?.active) {
    steps.push({
      id: "stop-kill-switch",
      area: "stop_resume",
      title: "Stop: persisted kill switch is active",
      status: "blocked",
      severity: "critical",
      trigger: input.killSwitch.reason,
      action:
        "Keep paper entries and live attempts blocked. Investigate the halt reason, document operator findings, and only request resume after the cause is understood.",
      verification:
        "Kill switch is inactive, dashboard reset is complete, and system trust no longer reports a kill-switch issue.",
      evidence: `Activated by ${input.killSwitch.activatedBy ?? "unknown"} at ${
        input.killSwitch.activatedAt ?? "unknown time"
      }.`,
      blocksPaperTrading: true,
      blocksLiveTrading: true,
    });
    return;
  }

  if (input.systemTrust.blocksPaperTrading) {
    steps.push({
      id: "stop-system-trust",
      area: "stop_resume",
      title: "Stop: system trust blocks paper entries",
      status: "blocked",
      severity: "critical",
      trigger: input.systemTrust.summary,
      action:
        "Do not open new paper or dry-run intents. Resolve critical trust issues before resuming automated paper entries.",
      verification:
        "System Trust status is no longer blocked and `blocksPaperTrading` is false.",
      evidence: summarizeTrustIssues(input.systemTrust),
      blocksPaperTrading: true,
      blocksLiveTrading: true,
    });
  } else if (input.systemTrust.blocksLiveTrading) {
    steps.push({
      id: "resume-live-gate",
      area: "stop_resume",
      title: "Resume gate: live trading remains blocked",
      status: "action_required",
      severity: "warning",
      trigger: input.systemTrust.summary,
      action:
        "Keep work in paper and dry-run mode. Before any future live-readiness review, replace fixture-backed or degraded inputs with trusted live data and re-run the evidence checks.",
      verification:
        "System Trust reports live trading allowed and no fixture-backed data issue remains.",
      evidence: summarizeTrustIssues(input.systemTrust),
      blocksPaperTrading: false,
      blocksLiveTrading: true,
    });
  }
}

function addDataSteps(
  steps: OperationalRunbookStep[],
  dataQuality: DataQualityReport | undefined,
) {
  if (!dataQuality) {
    steps.push({
      id: "failed-data-missing-report",
      area: "data",
      title: "Failed data: missing quality report",
      status: "blocked",
      severity: "critical",
      trigger: "No data-quality report is available.",
      action:
        "Run a data refresh and inspect connector configuration before allowing new paper entries.",
      verification: "A fresh data-quality report exists and is not blocked.",
      evidence: "dataQuality is undefined.",
      blocksPaperTrading: true,
      blocksLiveTrading: true,
    });
    return;
  }

  if (dataQuality.blocksPaperTrading || dataQuality.status === "blocked") {
    steps.push({
      id: "failed-data-blocked",
      area: "data",
      title: "Failed data: paper entries blocked",
      status: "blocked",
      severity: "critical",
      trigger: dataQuality.summary,
      action:
        "Pause new paper entries, refresh connector inputs, and inspect critical data-quality issues before retrying.",
      verification:
        "Data quality status returns healthy or degraded without `blocksPaperTrading`.",
      evidence: summarizeDataIssues(dataQuality),
      blocksPaperTrading: true,
      blocksLiveTrading: true,
    });
  } else if (dataQuality.status === "degraded" || dataQuality.fallbackUsed) {
    steps.push({
      id: "failed-data-degraded",
      area: "data",
      title: "Data caution: degraded or fallback inputs",
      status: "action_required",
      severity: "warning",
      trigger: dataQuality.summary,
      action:
        "Keep live-readiness blocked, compare fallback fields against the source connector, and refresh before trusting new signal evidence.",
      verification: "Data quality status is healthy and fallback usage is false.",
      evidence: summarizeDataIssues(dataQuality),
      blocksPaperTrading: false,
      blocksLiveTrading: true,
    });
  }
}

function addAlertSteps(
  steps: OperationalRunbookStep[],
  deliveries: AlertDelivery[],
) {
  const latestFailure = deliveries.find((delivery) => delivery.status === "failed");
  if (!latestFailure) return;

  steps.push({
    id: "failed-alert-delivery",
    area: "alerts",
    title: "Failed alert: delivery provider rejected a message",
    status: "blocked",
    severity: "critical",
    trigger:
      latestFailure.error ??
      `${latestFailure.channel} delivery to ${latestFailure.destination} failed.`,
    action:
      "Keep new execution intents blocked, verify Telegram/SMS credentials and destination allowlists, then send a dry-run alert before resuming.",
    verification:
      "Latest alert deliveries are sent, dry-run, queued, or suppressed for expected policy reasons; no failed delivery remains newest.",
    evidence: `${latestFailure.provider}:${latestFailure.channel} ${latestFailure.alertId} at ${latestFailure.attemptedAt}.`,
    blocksPaperTrading: true,
    blocksLiveTrading: true,
  });
}

function addSchedulerSteps(
  steps: OperationalRunbookStep[],
  scheduler: SchedulerStatus,
) {
  if (scheduler.consecutiveErrors <= 0) return;

  const critical = scheduler.consecutiveErrors >= 3;
  steps.push({
    id: critical ? "scheduler-stop" : "scheduler-retry",
    area: "scheduler",
    title: critical
      ? "Scheduler stop: repeated cycle failures"
      : "Scheduler caution: latest cycle failed",
    status: critical ? "blocked" : "action_required",
    severity: critical ? "critical" : "warning",
    trigger: `${scheduler.consecutiveErrors} consecutive scheduler error(s): ${scheduler.lastMessage}`,
    action: critical
      ? "Stop relying on unattended cycles. Inspect connector, persistence, and alert logs before restarting the scheduler."
      : "Inspect the latest scheduler message and run a manual refresh to confirm the failure has cleared.",
    verification: "Scheduler consecutive error count returns to zero after a successful cycle.",
    evidence: scheduler.lastErrorAt
      ? `Last error at ${scheduler.lastErrorAt}.`
      : "No last error timestamp recorded.",
    blocksPaperTrading: critical,
    blocksLiveTrading: true,
  });
}

function addPaperDriftSteps(
  steps: OperationalRunbookStep[],
  systemTrust: SystemTrustVerdict,
  paper: PaperPortfolio,
) {
  const driftIssues = systemTrust.issues.filter((issue) =>
    issue.code.startsWith("paper-ledger-"),
  );
  if (driftIssues.length > 0) {
    steps.push({
      id: "position-drift-review",
      area: "paper",
      title: "Position drift: paper ledger requires review",
      status: "blocked",
      severity: "critical",
      trigger: driftIssues.map((issue) => issue.message).join(" "),
      action:
        "Do not open new paper entries. Inspect positions, duplicate signal ids, equity values, and configured notional caps before resuming.",
      verification:
        "System Trust has no paper-ledger issues and open notional is back under configured caps.",
      evidence: `${paper.positions.length} open position(s); ${paper.trades.length} trade event(s).`,
      blocksPaperTrading: true,
      blocksLiveTrading: true,
    });
    return;
  }

  if (paper.positions.length > 0 && systemTrust.blocksPaperTrading) {
    steps.push({
      id: "position-stop-review",
      area: "paper",
      title: "Position review: trust block with open paper exposure",
      status: "action_required",
      severity: "warning",
      trigger: systemTrust.summary,
      action:
        "Let the paper broker close positions through data, risk, edge-decay, edge-policy, and holding-time rules; review any manual intervention separately.",
      verification: "Open paper positions fall to zero or system trust clears.",
      evidence: `${paper.positions.length} open position(s) remain while trust blocks entries.`,
      blocksPaperTrading: true,
      blocksLiveTrading: true,
    });
  }
}

function addExecutionSteps(
  steps: OperationalRunbookStep[],
  reconciliation: ExecutionReconciliationReport,
) {
  if (reconciliation.status === "clean") return;

  steps.push({
    id: "execution-reconciliation",
    area: "execution",
    title:
      reconciliation.status === "blocked"
        ? "Execution drift: dry-run ledger inconsistent"
        : "Execution caution: dry-run ledger needs attention",
    status: reconciliation.status === "blocked" ? "blocked" : "action_required",
    severity: reconciliation.status === "blocked" ? "critical" : "warning",
    trigger: `${reconciliation.issueCount} reconciliation issue(s), ${reconciliation.criticalIssueCount} critical.`,
    action:
      "Pause new dry-run intents, inspect attempt/fill records, and correct the state snapshot before using execution evidence.",
    verification: "Dry-run execution reconciliation returns clean.",
    evidence:
      reconciliation.issues
        .slice(0, 3)
        .map((issue) => `${issue.code}: ${issue.message}`)
        .join(" ") || "No issue detail recorded.",
    blocksPaperTrading: false,
    blocksLiveTrading: true,
  });
}

function summarizeTrustIssues(verdict: SystemTrustVerdict): string {
  return (
    verdict.issues
      .slice(0, 3)
      .map((issue) => `${issue.code}: ${issue.message}`)
      .join(" ") || verdict.summary
  );
}

function summarizeDataIssues(report: DataQualityReport): string {
  return (
    report.issues
      .slice(0, 3)
      .map((issue) => `${issue.code}: ${issue.message}`)
      .join(" ") || `${report.connectorLabel} ${report.status}`
  );
}

function summaryFor(
  status: OperationalRunbookReport["status"],
  steps: OperationalRunbookStep[],
): string {
  if (status === "blocked") {
    return `${steps.length} active runbook step(s); at least one stop condition blocks paper or live progression.`;
  }
  if (status === "attention") {
    return `${steps.length} active runbook step(s); operator review is required before any readiness upgrade.`;
  }
  return "No active operator action is required for paper-mode operation.";
}
