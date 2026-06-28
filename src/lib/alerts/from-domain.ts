import type { AlertEvent } from "@/lib/alerts/types";
import type {
  EdgeScoreboard,
  MarketRiskState,
  PaperPortfolio,
  RelativeValueSignal,
  RiskAlert,
  SystemTrustIssue,
  SystemTrustVerdict,
} from "@/lib/domain/types";
import { severityFromRiskState } from "@/lib/alerts/severity-policy";

export function riskAlertToAlertEvent(alert: RiskAlert): AlertEvent {
  const severity = mapRiskAlertSeverity(alert.severity);
  return {
    id: `alert:${alert.id}`,
    severity,
    title: alert.title,
    message: alert.explanation,
    source: alert.source,
    scope: {},
    createdAt: alert.timestamp,
    fingerprint: `${alert.category}:${alert.title}`,
    tradingImpact:
      severity === "CRITICAL" || severity === "BLACK"
        ? "Block new live trades for affected venue/asset."
        : "Mark for review.",
    metadata: {
      category: alert.category,
      riskSeverity: alert.severity,
    },
  };
}

export function signalToTradeableAlert(signal: RelativeValueSignal): AlertEvent {
  return {
    id: `alert:signal:${signal.id}`,
    severity: signal.eligibleForPaperTrading ? "TRADEABLE" : "WATCH",
    title: `${signal.assetPair} ${signal.kind.replaceAll("_", " ")}`,
    message: `${signal.explanation} Expected edge ${signal.expectedEdgeBps} bps; risk ${signal.riskScore}; liquidity ${signal.liquidityScore}; assumed fees/slippage are strategy configured and must be verified before manual review.`,
    source: "relative-value-signal-engine",
    scope: { pair: signal.assetPair, venue: signal.venue },
    createdAt: signal.timestamp,
    fingerprint: `signal:${signal.kind}:${signal.assetPair}:${signal.venue}`,
    tradingImpact: signal.eligibleForPaperTrading
      ? "Paper trading allowed under local limits; live trading requires all guards."
      : "No trading action.",
    metadata: {
      expectedEdgeBps: signal.expectedEdgeBps,
      riskScore: signal.riskScore,
      liquidityScore: signal.liquidityScore,
      opportunityScore: signal.opportunityScore,
    },
  };
}

export function dailyDigestAlert(input: {
  risk: MarketRiskState;
  paper: PaperPortfolio;
  signalCount: number;
}): AlertEvent {
  return {
    id: `alert:digest:${input.risk.updatedAt}`,
    severity: severityFromRiskState(input.risk.state),
    title: "Daily system digest",
    message: `${input.signalCount} signals generated. Paper PnL ${input.paper.dailyPnlUsd.toFixed(
      2,
    )}. Market risk state ${input.risk.state}: ${input.risk.explanation}`,
    source: "system-digest",
    scope: {},
    createdAt: input.risk.updatedAt,
    fingerprint: `digest:${input.risk.updatedAt.slice(0, 10)}`,
    tradingImpact:
      input.risk.state === "Black"
        ? "Global live halt remains active."
        : "No direct trading impact.",
    metadata: {
      riskState: input.risk.state,
      signalCount: input.signalCount,
      paperDailyPnlUsd: input.paper.dailyPnlUsd,
    },
  };
}

export function systemTrustToAlertEvents(verdict: SystemTrustVerdict): AlertEvent[] {
  if (verdict.status === "trusted" || verdict.issueCount === 0) return [];

  const surfacedIssues = verdict.issues.filter(
    (issue) =>
      issue.blocksPaperTrading ||
      issue.blocksLiveTrading ||
      issue.severity !== "info",
  );
  if (surfacedIssues.length === 0) return [];

  const leadIssues = surfacedIssues
    .slice(0, 3)
    .map(formatSystemTrustIssue)
    .join(" ");
  const remaining =
    surfacedIssues.length > 3
      ? ` ${surfacedIssues.length - 3} more issue(s).`
      : "";
  const issueFingerprint = surfacedIssues
    .map((issue) => issue.code)
    .sort()
    .join("|");

  return [
    {
      id: `alert:system-trust:${verdict.status}:${verdict.generatedAt}`,
      severity: systemTrustAlertSeverity(verdict),
      title: `System trust ${verdict.status}`,
      message: `${verdict.summary} ${leadIssues}${remaining}`.trim(),
      source: "system-trust-gate",
      scope: {},
      createdAt: verdict.generatedAt,
      fingerprint: `system-trust:${verdict.status}:${issueFingerprint}`,
      tradingImpact: systemTrustTradingImpact(verdict),
      metadata: {
        status: verdict.status,
        blocksPaperTrading: verdict.blocksPaperTrading,
        blocksLiveTrading: verdict.blocksLiveTrading,
        issueCount: verdict.issueCount,
        criticalIssueCount: verdict.criticalIssueCount,
        surfacedIssueCount: surfacedIssues.length,
      },
    },
  ];
}

export function edgeScoreboardToAlertEvents(
  scoreboard: EdgeScoreboard,
): AlertEvent[] {
  return scoreboard.rows
    .filter((row) => row.status === "underperforming")
    .map<AlertEvent>((row) => ({
      id: `alert:edge-scoreboard:${row.kind}:${scoreboard.updatedAt}`,
      severity: "WATCH",
      title: `${formatSignalKind(row.kind)} underperforming`,
      message: `${formatSignalKind(row.kind)} has ${formatUsd(
        row.totalPnlUsd,
      )} net paper PnL across ${row.closedCount} closed trade(s), ${row.winRatePct.toFixed(
        1,
      )}% win rate, and ${row.acceptanceRatePct.toFixed(
        1,
      )}% acceptance. ${row.recommendation}`,
      source: "edge-scoreboard",
      scope: {},
      createdAt: scoreboard.updatedAt,
      fingerprint: `edge-underperforming:${row.kind}`,
      tradingImpact:
        "Signal family is marked watch-only by edge policy; new paper entries are blocked until evidence improves.",
      metadata: {
        kind: row.kind,
        totalPnlUsd: row.totalPnlUsd,
        realizedPnlUsd: row.realizedPnlUsd,
        markPnlUsd: row.markPnlUsd,
        closedCount: row.closedCount,
        winRatePct: row.winRatePct,
        paperEligibleCount: row.paperEligibleCount,
        openPositionCount: row.openPositionCount,
      },
    }));
}

function mapRiskAlertSeverity(
  severity: RiskAlert["severity"],
): AlertEvent["severity"] {
  if (severity === "critical") return "BLACK";
  if (severity === "high") return "CRITICAL";
  if (severity === "medium") return "WATCH";
  return "INFO";
}

function systemTrustAlertSeverity(verdict: SystemTrustVerdict): AlertEvent["severity"] {
  if (
    verdict.issues.some(
      (issue) =>
        issue.code === "black-risk-state" || issue.code === "kill-switch-active",
    )
  ) {
    return "BLACK";
  }
  if (verdict.blocksPaperTrading || verdict.criticalIssueCount > 0) {
    return "CRITICAL";
  }
  return "WATCH";
}

function systemTrustTradingImpact(verdict: SystemTrustVerdict): string {
  if (verdict.blocksPaperTrading) {
    return "Blocks new paper entries and all live attempts.";
  }
  if (verdict.blocksLiveTrading) {
    return "Paper research can continue; live trading remains blocked.";
  }
  return "Review required before increasing trading privileges.";
}

function formatSystemTrustIssue(issue: SystemTrustIssue): string {
  const blocks = [
    issue.blocksPaperTrading ? "paper" : "",
    issue.blocksLiveTrading ? "live" : "",
  ]
    .filter(Boolean)
    .join("/");
  const suffix = blocks ? ` Blocks ${blocks}.` : "";
  return `${issue.code}: ${issue.message}${suffix}`;
}

function formatSignalKind(kind: string): string {
  return kind.replaceAll("_", " ");
}

function formatUsd(value: number): string {
  const sign = value < 0 ? "-" : "";
  return `${sign}$${Math.abs(value).toFixed(2)}`;
}
