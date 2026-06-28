import type { AlertDelivery } from "@/lib/alerts/types";
import type {
  DataQualityReport,
  MarketRiskState,
  PaperPortfolio,
  SystemTrustIssue,
  SystemTrustVerdict,
} from "@/lib/domain/types";
import type { KillSwitchState } from "@/lib/kill-switch/kill-switch";
import type { SchedulerStatus } from "@/lib/state/local-store";

const SCHEDULER_ERROR_BLOCK_THRESHOLD = 3;

export function evaluateSystemTrust(input: {
  dataQuality?: DataQualityReport;
  risk?: MarketRiskState;
  schedulerStatus?: SchedulerStatus;
  alertDeliveries?: AlertDelivery[];
  killSwitch?: KillSwitchState;
  paper?: PaperPortfolio;
  now?: Date;
}): SystemTrustVerdict {
  const issues: SystemTrustIssue[] = [];
  const generatedAt = (input.now ?? new Date()).toISOString();

  issues.push(...dataQualityIssues(input.dataQuality));
  issues.push(...riskIssues(input.risk));
  issues.push(...killSwitchIssues(input.killSwitch));
  issues.push(...schedulerIssues(input.schedulerStatus));
  issues.push(...alertDeliveryIssues(input.alertDeliveries ?? []));
  issues.push(...paperLedgerIssues(input.paper));

  const blocksPaperTrading = issues.some((issue) => issue.blocksPaperTrading);
  const blocksLiveTrading = issues.some((issue) => issue.blocksLiveTrading);
  const criticalIssueCount = issues.filter(
    (issue) => issue.severity === "critical",
  ).length;
  const warningIssueCount = issues.filter(
    (issue) => issue.severity === "warning",
  ).length;
  const status =
    blocksPaperTrading || criticalIssueCount > 0
      ? "blocked"
      : warningIssueCount > 0 || blocksLiveTrading
        ? "caution"
        : "trusted";

  return {
    status,
    generatedAt,
    summary: summaryFor(status, issues, blocksPaperTrading, blocksLiveTrading),
    blocksPaperTrading,
    blocksLiveTrading,
    issueCount: issues.length,
    criticalIssueCount,
    issues,
  };
}

export function paperTrustBlockReason(verdict?: SystemTrustVerdict): string | null {
  if (!verdict?.blocksPaperTrading) return null;

  const blocking = verdict.issues
    .filter((issue) => issue.blocksPaperTrading)
    .map((issue) => `${issue.code}: ${issue.message}`);

  return `System trust ${verdict.status} blocks new paper trades: ${
    blocking.length ? blocking.join(" ") : verdict.summary
  }`;
}

function dataQualityIssues(
  report: DataQualityReport | undefined,
): SystemTrustIssue[] {
  if (!report) {
    return [
      issue({
        code: "missing-data-quality",
        severity: "critical",
        scope: "data",
        message: "No data-quality report is available.",
        blocksPaperTrading: true,
        blocksLiveTrading: true,
      }),
    ];
  }

  const issues: SystemTrustIssue[] = [];
  if (report.blocksPaperTrading) {
    issues.push(
      issue({
        code: "data-quality-blocked",
        severity: "critical",
        scope: "data",
        message: report.summary,
        blocksPaperTrading: true,
        blocksLiveTrading: true,
      }),
    );
  } else if (report.status === "degraded") {
    issues.push(
      issue({
        code: "data-quality-degraded",
        severity: "warning",
        scope: "data",
        message: report.summary,
        blocksPaperTrading: false,
        blocksLiveTrading: true,
      }),
    );
  }

  if (report.fixtureBacked) {
    issues.push(
      issue({
        code: "fixture-backed-data",
        severity: report.mode === "sample" ? "info" : "critical",
        scope: "data",
        message:
          report.mode === "sample"
            ? "Sample fixtures are inspectable for paper research but cannot support live execution."
            : "Connector fallback uses fixture-backed data.",
        blocksPaperTrading: report.mode !== "sample",
        blocksLiveTrading: true,
      }),
    );
  }

  return issues;
}

function riskIssues(risk: MarketRiskState | undefined): SystemTrustIssue[] {
  if (!risk) {
    return [
      issue({
        code: "missing-risk-state",
        severity: "critical",
        scope: "risk",
        message: "No market risk state is available.",
        blocksPaperTrading: true,
        blocksLiveTrading: true,
      }),
    ];
  }

  if (risk.state === "Black") {
    return [
      issue({
        code: "black-risk-state",
        severity: "critical",
        scope: "risk",
        message: risk.explanation,
        blocksPaperTrading: true,
        blocksLiveTrading: true,
      }),
    ];
  }

  if (risk.state === "Red") {
    return [
      issue({
        code: "red-risk-state",
        severity: "warning",
        scope: "risk",
        message: risk.explanation,
        blocksPaperTrading: false,
        blocksLiveTrading: true,
      }),
    ];
  }

  return [];
}

function killSwitchIssues(
  killSwitch: KillSwitchState | undefined,
): SystemTrustIssue[] {
  if (!killSwitch?.active) return [];

  return [
    issue({
      code: "kill-switch-active",
      severity: "critical",
      scope: "kill-switch",
      message: killSwitch.reason,
      blocksPaperTrading: true,
      blocksLiveTrading: true,
    }),
  ];
}

function schedulerIssues(
  schedulerStatus: SchedulerStatus | undefined,
): SystemTrustIssue[] {
  if (!schedulerStatus) return [];

  if (schedulerStatus.consecutiveErrors >= SCHEDULER_ERROR_BLOCK_THRESHOLD) {
    return [
      issue({
        code: "scheduler-consecutive-errors",
        severity: "critical",
        scope: "scheduler",
        message: `${schedulerStatus.consecutiveErrors} consecutive scheduler error(s): ${schedulerStatus.lastMessage}`,
        blocksPaperTrading: true,
        blocksLiveTrading: true,
      }),
    ];
  }

  if (schedulerStatus.consecutiveErrors > 0) {
    return [
      issue({
        code: "scheduler-error-warning",
        severity: "warning",
        scope: "scheduler",
        message: `${schedulerStatus.consecutiveErrors} consecutive scheduler error(s): ${schedulerStatus.lastMessage}`,
        blocksPaperTrading: false,
        blocksLiveTrading: true,
      }),
    ];
  }

  return [];
}

function alertDeliveryIssues(deliveries: AlertDelivery[]): SystemTrustIssue[] {
  const latestFailure = deliveries.find((delivery) => delivery.status === "failed");
  if (!latestFailure) return [];

  return [
    issue({
      code: "alert-delivery-failed",
      severity: "critical",
      scope: `alert:${latestFailure.alertId}`,
      message:
        latestFailure.error ??
        `${latestFailure.channel} delivery to ${latestFailure.destination} failed.`,
      blocksPaperTrading: true,
      blocksLiveTrading: true,
    }),
  ];
}

function paperLedgerIssues(paper: PaperPortfolio | undefined): SystemTrustIssue[] {
  if (!paper) return [];

  const issues: SystemTrustIssue[] = [];
  if (!Number.isFinite(paper.cashUsd) || !Number.isFinite(paper.equityUsd)) {
    issues.push(
      issue({
        code: "paper-ledger-invalid-equity",
        severity: "critical",
        scope: "paper",
        message: "Paper ledger cash or equity is not finite.",
        blocksPaperTrading: true,
        blocksLiveTrading: true,
      }),
    );
  }

  const notional = paper.positions.reduce(
    (sum, position) => sum + position.notionalUsd,
    0,
  );
  const maxNotional =
    Math.max(paper.equityUsd, 0) * paper.riskLimits.maxPortfolioNotionalPct;
  if (notional > maxNotional + 0.01) {
    issues.push(
      issue({
        code: "paper-ledger-notional-drift",
        severity: "critical",
        scope: "paper",
        message: `Open paper notional $${notional.toFixed(
          2,
        )} exceeds configured cap $${maxNotional.toFixed(2)}.`,
        blocksPaperTrading: true,
        blocksLiveTrading: true,
      }),
    );
  }

  const uniqueSignalIds = new Set(paper.positions.map((position) => position.signalId));
  if (uniqueSignalIds.size !== paper.positions.length) {
    issues.push(
      issue({
        code: "paper-ledger-duplicate-position",
        severity: "critical",
        scope: "paper",
        message: "Paper ledger contains duplicate open positions for a signal.",
        blocksPaperTrading: true,
        blocksLiveTrading: true,
      }),
    );
  }

  return issues;
}

function issue(input: SystemTrustIssue): SystemTrustIssue {
  return input;
}

function summaryFor(
  status: SystemTrustVerdict["status"],
  issues: SystemTrustIssue[],
  blocksPaperTrading: boolean,
  blocksLiveTrading: boolean,
): string {
  if (blocksPaperTrading) {
    return `${issues.filter((issue) => issue.blocksPaperTrading).length} system-trust issue(s) block new paper entries.`;
  }

  if (blocksLiveTrading) {
    return "System trust allows paper mode but blocks live execution.";
  }

  if (status === "caution") {
    return `${issues.length} non-critical system-trust issue(s) require review.`;
  }

  return "System trust allows paper research under current local limits.";
}
