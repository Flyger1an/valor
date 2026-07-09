import type { buildDashboardState } from "@/lib/dashboard/build-dashboard";

type DashboardState = Awaited<ReturnType<typeof buildDashboardState>>;

export type OperatorEvidenceDecision =
  | "no_go"
  | "watchlist_review"
  | "candidate_review";

export interface OperatorEvidenceBlocker {
  source:
    | "readiness"
    | "runbook"
    | "system_trust"
    | "execution"
    | "external_evidence";
  code: string;
  severity: "info" | "warning" | "critical";
  message: string;
  evidence: string;
}

export interface OperatorEvidencePacket {
  id: string;
  title: string;
  generatedAt: string;
  decision: OperatorEvidenceDecision;
  summary: string;
  guardrail: string;
  readiness: {
    status: DashboardState["tinyLiveReadiness"]["status"];
    summary: string;
    candidate: string;
    evidenceDays: number;
    minimumEvidenceDays: number;
    closedTradeCount: number;
    minimumClosedTradesPerFamily: number;
    memoConclusion: string;
    requiredNextEvidence: string;
  };
  controls: {
    dataQuality: string;
    systemTrust: string;
    runbook: string;
    executionReconciliation: string;
    killSwitchActive: boolean;
    scheduler: string;
  };
  evidence: {
    paper: string;
    edgeScoreboard: string;
    evolverSoak: string;
    topSignalFamilies: string[];
    backtest: string;
  };
  blockers: OperatorEvidenceBlocker[];
  nextActions: string[];
  attestations: string[];
}

const GUARDRAIL =
  "This packet is operator review evidence only. It is not live execution approval, order sizing advice, compliance clearance, custody authorization, or permission to bypass deterministic Valor controls.";

export function buildOperatorEvidencePacket(
  state: DashboardState,
): OperatorEvidencePacket {
  const readiness = state.tinyLiveReadiness;
  const generatedAt = readiness.generatedAt;
  const decision = decisionFromReadiness(readiness.status);
  const blockers = collectBlockers(state);
  const nextActions = collectNextActions(state);

  return {
    id: `operator-evidence:${generatedAt}`,
    title: "Valor v0.2 Operator Evidence Packet",
    generatedAt,
    decision,
    summary: `${decisionLabel(decision)}: ${readiness.summary} System trust is ${state.systemTrust.status}; runbook is ${state.operationalRunbook.status}; dry-run reconciliation is ${state.executionReconciliation.status}.`,
    guardrail: GUARDRAIL,
    readiness: {
      status: readiness.status,
      summary: readiness.summary,
      candidate: formatCandidate(readiness.candidate),
      evidenceDays: readiness.evidenceDays,
      minimumEvidenceDays: readiness.minimums.evidenceDays,
      closedTradeCount: readiness.closedTradeCount,
      minimumClosedTradesPerFamily: readiness.minimums.closedTradesPerFamily,
      memoConclusion: readiness.memo.conclusion,
      requiredNextEvidence: readiness.memo.requiredNextEvidence,
    },
    controls: {
      dataQuality: `${state.dataQuality.status}: ${state.dataQuality.summary}`,
      systemTrust: `${state.systemTrust.status}: ${state.systemTrust.summary}`,
      runbook: `${state.operationalRunbook.status}: ${state.operationalRunbook.summary}`,
      executionReconciliation: `${state.executionReconciliation.status}: ${state.executionReconciliation.issueCount} issue(s), ${state.executionReconciliation.criticalIssueCount} critical.`,
      killSwitchActive: Boolean(state.killSwitch?.active),
      scheduler: `${state.schedulerStatus.running ? "running" : "stopped"}; ${state.schedulerStatus.consecutiveErrors} consecutive error(s); stale recoveries ${state.schedulerStatus.staleRunCount ?? 0}; heartbeat ${state.schedulerStatus.lastHeartbeatAt ?? "n/a"}; ${state.schedulerStatus.lastMessage ?? "no scheduler message"}`,
    },
    evidence: {
      paper: `${state.paper.positions.length} open position(s), ${state.paper.trades.length} ledger event(s), daily PnL ${formatUsd(state.paper.dailyPnlUsd)}, weekly PnL ${formatUsd(state.paper.weeklyPnlUsd)}.`,
      edgeScoreboard: `${state.edgeScoreboard.rows.length} signal family row(s), ${state.edgeScoreboard.totals.ledgerEventCount} ledger event(s), total PnL ${formatUsd(state.edgeScoreboard.totals.totalPnlUsd)}.`,
      evolverSoak: formatEvolverEvidence(state.evolverEvidence),
      topSignalFamilies: state.edgeScoreboard.rows
        .slice(0, 6)
        .map(formatScoreboardRow),
      backtest: `${state.backtest.strategyName}: return ${state.backtest.totalReturnPct}%, max drawdown ${state.backtest.maxDrawdownPct}%, Sharpe ${state.backtest.sharpe}, win rate ${state.backtest.winRatePct}%.`,
    },
    blockers,
    nextActions,
    attestations: [
      "No live exchange executor is implemented in Valor v0.2.",
      "Sample, fixture-backed, fallback, stale, or otherwise degraded evidence cannot support tiny-live advancement.",
      "LLM analyst output remains commentary only; deterministic controls remain authoritative.",
      "Any future live-trading review requires separate venue, legal, tax, custody, and compliance review.",
    ],
  };
}

export function formatOperatorEvidenceMarkdown(
  packet: OperatorEvidencePacket,
): string {
  const lines = [
    `# ${packet.title}`,
    "",
    `Generated: ${packet.generatedAt}`,
    `Decision: ${packet.decision}`,
    "",
    packet.guardrail,
    "",
    "## Summary",
    "",
    packet.summary,
    "",
    "## Readiness",
    "",
    `- Status: ${packet.readiness.status}`,
    `- Summary: ${packet.readiness.summary}`,
    `- Candidate: ${packet.readiness.candidate}`,
    `- Evidence window: ${packet.readiness.evidenceDays} day(s), minimum ${packet.readiness.minimumEvidenceDays}`,
    `- Closed trades: ${packet.readiness.closedTradeCount}, minimum ${packet.readiness.minimumClosedTradesPerFamily} per candidate family`,
    `- Memo conclusion: ${packet.readiness.memoConclusion}`,
    `- Required next evidence: ${packet.readiness.requiredNextEvidence}`,
    "",
    "## Controls",
    "",
    `- Data quality: ${packet.controls.dataQuality}`,
    `- System trust: ${packet.controls.systemTrust}`,
    `- Operational runbook: ${packet.controls.runbook}`,
    `- Dry-run reconciliation: ${packet.controls.executionReconciliation}`,
    `- Kill switch active: ${packet.controls.killSwitchActive}`,
    `- Scheduler: ${packet.controls.scheduler}`,
    "",
    "## Evidence",
    "",
    `- Paper ledger: ${packet.evidence.paper}`,
    `- Edge scoreboard: ${packet.evidence.edgeScoreboard}`,
    `- Evolver imported soak: ${packet.evidence.evolverSoak}`,
    `- Backtest: ${packet.evidence.backtest}`,
    "",
    "### Top Signal Families",
    "",
    ...markdownList(packet.evidence.topSignalFamilies, "No signal-family rows yet."),
    "",
    "## Blockers",
    "",
    ...markdownList(
      packet.blockers.map(
        (blocker) =>
          `[${blocker.source}] ${blocker.severity} ${blocker.code}: ${blocker.message} Evidence: ${blocker.evidence}`,
      ),
      "No active blockers in this packet.",
    ),
    "",
    "## Next Actions",
    "",
    ...markdownNumberedList(packet.nextActions, "Continue paper-mode evidence collection."),
    "",
    "## Attestations",
    "",
    ...markdownList(packet.attestations, "No attestations recorded."),
    "",
  ];

  return lines.join("\n");
}

function collectBlockers(state: DashboardState): OperatorEvidenceBlocker[] {
  const readinessBlockers: OperatorEvidenceBlocker[] =
    state.tinyLiveReadiness.blockers.map((blocker) => ({
      source: "readiness" as const,
      code: blocker.code,
      severity: blocker.severity,
      message: blocker.message,
      evidence: blocker.evidence,
    }));
  const runbookBlockers: OperatorEvidenceBlocker[] = state.operationalRunbook.steps
    .filter((step) => step.status !== "ready")
    .map((step) => ({
      source: "runbook" as const,
      code: step.id,
      severity: step.severity,
      message: step.title,
      evidence: `${step.trigger} Action: ${step.action}`,
    }));
  const trustBlockers: OperatorEvidenceBlocker[] = state.systemTrust.issues.map((issue) => ({
    source: "system_trust" as const,
    code: issue.code,
    severity: issue.severity,
    message: issue.message,
    evidence: `Paper blocked ${issue.blocksPaperTrading}. Live blocked ${issue.blocksLiveTrading}.`,
  }));
  const executionBlockers: OperatorEvidenceBlocker[] =
    state.executionReconciliation.issues.map((issue) => ({
      source: "execution" as const,
      code: issue.code,
      severity: issue.severity,
      message: issue.message,
      evidence: issue.scope,
    }));
  const externalEvidenceBlockers: OperatorEvidenceBlocker[] =
    state.evolverEvidence.issues.map((issue) => ({
      source: "external_evidence" as const,
      code: issue.code,
      severity: issue.severity,
      message: issue.message,
      evidence: issue.evidence,
    }));

  return [
    ...readinessBlockers,
    ...runbookBlockers,
    ...trustBlockers,
    ...executionBlockers,
    ...externalEvidenceBlockers,
  ].slice(0, 24);
}

function collectNextActions(state: DashboardState): string[] {
  const actions = [
    state.tinyLiveReadiness.memo.requiredNextEvidence,
    evolverEvidenceAction(state),
    ...state.operationalRunbook.steps
      .filter((step) => step.status !== "ready")
      .slice(0, 5)
      .map((step) => `${step.title}: ${step.action}`),
  ];

  return uniqueNonEmpty(actions).slice(0, 8);
}

function formatEvolverEvidence(
  report: DashboardState["evolverEvidence"],
): string {
  if (!report.configured) return report.summary;
  const shadow = report.shadow
    ? `${report.shadow.closedTradeCount} closed shadow trade(s), ${formatUsd(
        report.shadow.reportedPnlUsd ??
          report.shadow.approximatedClosedPnlUsd,
      )} shadow PnL, ${report.shadow.winRatePct.toFixed(1)}% win rate`
    : "no shadow book";

  return `${report.status}: ${report.evidenceDays} imported day(s), ${shadow}, ${report.totalResearchCycles} research cycle(s), ${report.surfacedCandidateCount} surfaced candidate(s).`;
}

function evolverEvidenceAction(state: DashboardState): string {
  const report = state.evolverEvidence;
  if (!report.configured) {
    return "Configure VALOR_EVOLVER_EVIDENCE_DIR on the v0.2 deployment to import live Evolver soak ledgers into readiness review.";
  }
  if (report.status === "blocked") {
    return "Keep tiny-live blocked until imported Evolver shadow/calibration evidence recovers or the failed family is removed from promotion consideration.";
  }
  if (report.status === "watch") {
    return "Review imported Evolver soak warnings before any candidate-review memo.";
  }
  return "";
}

function decisionFromReadiness(
  status: DashboardState["tinyLiveReadiness"]["status"],
): OperatorEvidenceDecision {
  if (status === "candidate_review") return "candidate_review";
  if (status === "watchlist") return "watchlist_review";
  return "no_go";
}

function decisionLabel(decision: OperatorEvidenceDecision): string {
  if (decision === "candidate_review") return "Candidate review";
  if (decision === "watchlist_review") return "Watchlist review";
  return "No-go";
}

function formatCandidate(
  candidate: DashboardState["tinyLiveReadiness"]["candidate"],
): string {
  if (!candidate) return "none";
  return `${candidate.kind} (${candidate.status}), ${candidate.closedCount} closed trade(s), ${formatUsd(candidate.totalPnlUsd)} total PnL, ${candidate.winRatePct}% win rate.`;
}

function formatScoreboardRow(
  row: DashboardState["edgeScoreboard"]["rows"][number],
): string {
  return `${row.kind}: ${row.status}, generated ${row.generatedCount}, accepted ${row.filledCount}, rejected ${row.rejectedCount}, closed ${row.closedCount}, win rate ${row.winRatePct}%, PnL ${formatUsd(row.totalPnlUsd)}, recommendation ${row.recommendation}`;
}

function formatUsd(value: number): string {
  return `$${value.toFixed(2)}`;
}

function markdownList(values: string[], emptyValue: string): string[] {
  if (values.length === 0) return [`- ${emptyValue}`];
  return values.map((value) => `- ${compact(value)}`);
}

function markdownNumberedList(values: string[], emptyValue: string): string[] {
  if (values.length === 0) return [`1. ${emptyValue}`];
  return values.map((value, index) => `${index + 1}. ${compact(value)}`);
}

function uniqueNonEmpty(values: string[]): string[] {
  return Array.from(
    new Set(values.map(compact).filter((value) => value.length > 0)),
  );
}

function compact(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}
