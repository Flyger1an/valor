import type {
  DataQualityReport,
  EdgeScoreboard,
  EdgeScoreboardRow,
  EvolverEvidenceReport,
  ExecutionReconciliationReport,
  OperationalRunbookReport,
  PaperPortfolio,
  SignalKind,
  SystemTrustVerdict,
  TinyLiveReadinessBlocker,
  TinyLiveReadinessCandidate,
  TinyLiveReadinessReport,
} from "@/lib/domain/types";
import { benchedSignalKindsFromEvidence } from "@/lib/evidence/evolver-watchdog";

const MIN_EVIDENCE_DAYS = 21;
const MIN_CLOSED_TRADES_PER_FAMILY = 10;
const MIN_WIN_RATE_PCT = 55;
const MIN_TOTAL_PNL_USD = 1;

export function evaluateTinyLiveReadiness(input: {
  dataQuality: DataQualityReport;
  systemTrust: SystemTrustVerdict;
  edgeScoreboard: EdgeScoreboard;
  paper: PaperPortfolio;
  executionReconciliation: ExecutionReconciliationReport;
  operationalRunbook: OperationalRunbookReport;
  evolverEvidence?: EvolverEvidenceReport;
  now?: Date;
}): TinyLiveReadinessReport {
  const generatedAt = (input.now ?? new Date()).toISOString();
  const evidence = evidenceWindow(input.paper);
  const benchedSignalKinds = benchedSignalKindsFromEvidence(
    input.evolverEvidence,
  );
  const benchedCandidateRows = qualifiedBenchedRows(
    input.edgeScoreboard,
    benchedSignalKinds,
  );
  const candidateRow = bestCandidateRow(
    input.edgeScoreboard,
    benchedSignalKinds,
  );
  const blockers: TinyLiveReadinessBlocker[] = [];

  addTrustBlockers(blockers, input.dataQuality, input.systemTrust);
  addEvidenceBlockers(blockers, evidence.days, candidateRow);
  addBenchGuardBlockers(blockers, benchedCandidateRows);
  addControlBlockers(
    blockers,
    input.executionReconciliation,
    input.operationalRunbook,
  );
  addEvolverEvidenceBlockers(blockers, input.evolverEvidence);

  const candidate = candidateRow ? candidateFromRow(candidateRow) : undefined;
  const criticalBlockerCount = blockers.filter(
    (blocker) => blocker.severity === "critical",
  ).length;
  const status =
    criticalBlockerCount > 0 || !candidate
      ? "no_go"
      : blockers.some((blocker) => blocker.severity === "warning")
        ? "watchlist"
        : "candidate_review";

  return {
    id: `tiny-live-readiness:${generatedAt}`,
    generatedAt,
    status,
    summary: summaryFor(status, candidate, blockers),
    candidate,
    evidenceDays: evidence.days,
    closedTradeCount: closedTradeCount(input.paper),
    blockerCount: blockers.length,
    criticalBlockerCount,
    blockers,
    minimums: {
      evidenceDays: MIN_EVIDENCE_DAYS,
      closedTradesPerFamily: MIN_CLOSED_TRADES_PER_FAMILY,
      winRatePct: MIN_WIN_RATE_PCT,
      totalPnlUsd: MIN_TOTAL_PNL_USD,
    },
    memo: {
      conclusion: memoConclusion(status, candidate),
      evidenceWindow: evidence.label,
      requiredNextEvidence: requiredNextEvidence(status, blockers),
    },
  };
}

function addEvolverEvidenceBlockers(
  blockers: TinyLiveReadinessBlocker[],
  evolverEvidence: EvolverEvidenceReport | undefined,
) {
  if (!evolverEvidence?.configured) return;

  if (evolverEvidence.status === "empty") {
    blockers.push({
      code: "evolver-evidence-empty",
      severity: "warning",
      message: "Configured Evolver evidence import has no usable soak records.",
      evidence: evolverEvidence.summary,
    });
    return;
  }

  for (const issue of evolverEvidence.issues) {
    if (issue.severity === "info") continue;
    blockers.push({
      code: issue.code,
      severity: issue.severity,
      message: issue.message,
      evidence: issue.evidence,
    });
  }
}

function addBenchGuardBlockers(
  blockers: TinyLiveReadinessBlocker[],
  benchedCandidateRows: EdgeScoreboardRow[],
) {
  if (benchedCandidateRows.length === 0) return;
  blockers.push({
    code: "evolver-bench-guard-active",
    severity: "critical",
    message:
      "Imported Evolver recovery plan benches a v0.2 signal family that otherwise qualifies for candidate review.",
    evidence: benchedCandidateRows
      .map(
        (row) =>
          `${row.kind}: ${row.closedCount} close(s), ${row.winRatePct.toFixed(
            1,
          )}% win rate, $${row.totalPnlUsd.toFixed(2)} total PnL`,
      )
      .join("; "),
  });
}

function addTrustBlockers(
  blockers: TinyLiveReadinessBlocker[],
  dataQuality: DataQualityReport,
  systemTrust: SystemTrustVerdict,
) {
  if (dataQuality.fixtureBacked || dataQuality.fallbackUsed || dataQuality.mode === "sample") {
    blockers.push({
      code: "untrusted-data-source",
      severity: "critical",
      message: "Tiny-live review requires trusted non-fixture data.",
      evidence: `${dataQuality.connectorLabel} mode ${dataQuality.mode}; fallback ${dataQuality.fallbackUsed}; fixture ${dataQuality.fixtureBacked}.`,
    });
  }

  if (dataQuality.status !== "healthy") {
    blockers.push({
      code: "data-quality-not-healthy",
      severity: dataQuality.blocksPaperTrading ? "critical" : "warning",
      message: "Data quality must be healthy before readiness can advance.",
      evidence: dataQuality.summary,
    });
  }

  if (systemTrust.status !== "trusted" || systemTrust.blocksLiveTrading) {
    blockers.push({
      code: "system-trust-not-live-ready",
      severity: systemTrust.blocksPaperTrading ? "critical" : "warning",
      message: "System Trust must be trusted and live-unblocked for candidate review.",
      evidence: systemTrust.summary,
    });
  }
}

function addEvidenceBlockers(
  blockers: TinyLiveReadinessBlocker[],
  evidenceDays: number,
  candidate: EdgeScoreboardRow | undefined,
) {
  if (evidenceDays < MIN_EVIDENCE_DAYS) {
    blockers.push({
      code: "insufficient-evidence-window",
      severity: "critical",
      message: "Tiny-live review requires several weeks of continuous paper evidence.",
      evidence: `${evidenceDays} evidence day(s), minimum ${MIN_EVIDENCE_DAYS}.`,
    });
  }

  if (!candidate) {
    blockers.push({
      code: "no-proving-signal-family",
      severity: "critical",
      message: "No signal family has enough positive closed paper evidence.",
      evidence: "No edge-scoreboard row satisfies closed count, positive PnL, and win-rate thresholds.",
    });
    return;
  }

  if (candidate.closedCount < MIN_CLOSED_TRADES_PER_FAMILY) {
    blockers.push({
      code: "insufficient-family-closed-trades",
      severity: "critical",
      message: "Candidate family needs more closed paper trades.",
      evidence: `${candidate.kind} has ${candidate.closedCount} closed trade(s), minimum ${MIN_CLOSED_TRADES_PER_FAMILY}.`,
    });
  }

  if (candidate.totalPnlUsd < MIN_TOTAL_PNL_USD) {
    blockers.push({
      code: "candidate-pnl-not-positive",
      severity: "critical",
      message: "Candidate family must be net positive after modeled costs.",
      evidence: `${candidate.kind} total PnL $${candidate.totalPnlUsd.toFixed(2)}.`,
    });
  }

  if (candidate.winRatePct < MIN_WIN_RATE_PCT) {
    blockers.push({
      code: "candidate-win-rate-low",
      severity: "warning",
      message: "Candidate family win rate is below readiness threshold.",
      evidence: `${candidate.kind} win rate ${candidate.winRatePct.toFixed(1)}%, minimum ${MIN_WIN_RATE_PCT}%.`,
    });
  }
}

function addControlBlockers(
  blockers: TinyLiveReadinessBlocker[],
  executionReconciliation: ExecutionReconciliationReport,
  operationalRunbook: OperationalRunbookReport,
) {
  if (executionReconciliation.status !== "clean") {
    blockers.push({
      code: "execution-reconciliation-not-clean",
      severity: executionReconciliation.status === "blocked" ? "critical" : "warning",
      message: "Dry-run execution reconciliation must be clean.",
      evidence: `${executionReconciliation.issueCount} issue(s), status ${executionReconciliation.status}.`,
    });
  }

  if (operationalRunbook.status !== "ready") {
    blockers.push({
      code: "runbook-not-ready",
      severity: operationalRunbook.status === "blocked" ? "critical" : "warning",
      message: "Operational runbook must be ready before candidate review.",
      evidence: operationalRunbook.summary,
    });
  }
}

function bestCandidateRow(
  scoreboard: EdgeScoreboard,
  benchedSignalKinds: SignalKind[] = [],
): EdgeScoreboardRow | undefined {
  const benched = new Set(benchedSignalKinds);
  return scoreboard.rows
    .filter((row) => !benched.has(row.kind) && rowQualifiesForCandidate(row))
    .sort(
      (a, b) =>
        b.totalPnlUsd - a.totalPnlUsd ||
        b.closedCount - a.closedCount ||
        b.winRatePct - a.winRatePct,
    )[0];
}

function qualifiedBenchedRows(
  scoreboard: EdgeScoreboard,
  benchedSignalKinds: SignalKind[],
): EdgeScoreboardRow[] {
  if (benchedSignalKinds.length === 0) return [];
  const benched = new Set(benchedSignalKinds);
  return scoreboard.rows.filter(
    (row) => benched.has(row.kind) && rowQualifiesForCandidate(row),
  );
}

function rowQualifiesForCandidate(row: EdgeScoreboardRow): boolean {
  return (
    row.status === "proving" &&
    row.closedCount >= MIN_CLOSED_TRADES_PER_FAMILY &&
    row.totalPnlUsd >= MIN_TOTAL_PNL_USD &&
    row.winRatePct >= MIN_WIN_RATE_PCT
  );
}

function candidateFromRow(row: EdgeScoreboardRow): TinyLiveReadinessCandidate {
  return {
    kind: row.kind,
    status: row.status,
    closedCount: row.closedCount,
    totalPnlUsd: row.totalPnlUsd,
    winRatePct: row.winRatePct,
    acceptanceRatePct: row.acceptanceRatePct,
    averageExpectedEdgeBps: row.averageExpectedEdgeBps,
    recommendation: row.recommendation,
  };
}

function evidenceWindow(paper: PaperPortfolio): { days: number; label: string } {
  const timestamps = [...paper.trades, ...paper.rejectedSignals]
    .map((trade) => new Date(trade.timestamp).getTime())
    .filter(Number.isFinite);
  if (timestamps.length === 0) {
    return { days: 0, label: "No paper ledger evidence yet." };
  }

  const first = Math.min(...timestamps);
  const last = Math.max(...timestamps);
  const days = Math.max(1, Math.floor((last - first) / 86_400_000) + 1);
  return {
    days,
    label: `${days} day(s), ${new Date(first).toISOString()} to ${new Date(
      last,
    ).toISOString()}.`,
  };
}

function closedTradeCount(paper: PaperPortfolio): number {
  return paper.trades.filter((trade) => trade.status === "closed").length;
}

function summaryFor(
  status: TinyLiveReadinessReport["status"],
  candidate: TinyLiveReadinessCandidate | undefined,
  blockers: TinyLiveReadinessBlocker[],
): string {
  if (status === "candidate_review" && candidate) {
    return `${candidate.kind.replaceAll("_", " ")} has earned human candidate review; no live authorization is implied.`;
  }
  if (status === "watchlist" && candidate) {
    return `${candidate.kind.replaceAll("_", " ")} is on watchlist, but readiness blockers remain.`;
  }
  return `No-go for tiny-live review: ${blockers.length} blocker(s) remain.`;
}

function memoConclusion(
  status: TinyLiveReadinessReport["status"],
  candidate: TinyLiveReadinessCandidate | undefined,
): string {
  if (status === "candidate_review" && candidate) {
    return `${candidate.kind.replaceAll("_", " ")} may be reviewed by a human for future tiny-live consideration. This is not execution approval.`;
  }
  if (status === "watchlist") {
    return "A candidate may be forming, but required evidence or control gates are not fully satisfied.";
  }
  return "Do not advance to tiny-live review.";
}

function requiredNextEvidence(
  status: TinyLiveReadinessReport["status"],
  blockers: TinyLiveReadinessBlocker[],
): string {
  if (status === "candidate_review") {
    return "Write a human go/no-go memo, validate venue/compliance constraints, and keep live execution disabled until v0.3 review.";
  }
  return (
    blockers
      .slice(0, 3)
      .map((blocker) => `${blocker.code}: ${blocker.message}`)
      .join(" ") || "Continue paper evidence collection."
  );
}
