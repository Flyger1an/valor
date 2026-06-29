import type { buildDashboardState } from "@/lib/dashboard/build-dashboard";
import type { RagDocument } from "@/lib/llm/types";

type DashboardState = Awaited<ReturnType<typeof buildDashboardState>>;

export function buildAnalystCorpus(state: DashboardState): RagDocument[] {
  const riskDocs: RagDocument[] = [
    {
      id: "risk:state",
      title: `Market risk state ${state.risk.state}`,
      kind: "risk",
      timestamp: state.risk.updatedAt,
      content: `${state.risk.state} score ${state.risk.score}. ${state.risk.explanation}. Restrictions: ${state.risk.tradingRestrictions
        .map((restriction) => restriction.description)
        .join(" ")}`,
    },
    ...state.risk.activeAlerts.map((alert) => ({
      id: `risk:${alert.id}`,
      title: alert.title,
      kind: "risk" as const,
      timestamp: alert.timestamp,
      content: `${alert.severity} ${alert.category}. ${alert.explanation}. Source ${alert.source}. Restrictions: ${alert.restrictions
        .map((restriction) => restriction.description)
        .join(" ")}`,
    })),
    {
      id: "risk:system-trust",
      title: `System trust ${state.systemTrust.status}`,
      kind: "risk",
      timestamp: state.systemTrust.generatedAt,
      content: `System trust status ${state.systemTrust.status}. Paper blocked ${state.systemTrust.blocksPaperTrading}. Live blocked ${state.systemTrust.blocksLiveTrading}. ${state.systemTrust.summary}. Issues: ${state.systemTrust.issues
        .map(
          (issue) =>
            `${issue.severity} ${issue.code}: ${issue.message} Paper blocked ${issue.blocksPaperTrading}. Live blocked ${issue.blocksLiveTrading}.`,
        )
        .join(" ")}`,
    },
  ];

  const signalDocs = state.signals.map((signal) => ({
    id: `signal:${signal.id}`,
    title: `${signal.assetPair} ${signal.kind}`,
    kind: "signal" as const,
    timestamp: signal.timestamp,
    content: `${signal.assetPair} on ${signal.venue}. Direction ${signal.direction}. Expected edge ${signal.expectedEdgeBps} bps. Confidence ${signal.confidence}. Risk ${signal.riskScore}. Liquidity ${signal.liquidityScore}. Paper eligible ${signal.eligibleForPaperTrading}. Live eligible ${signal.eligibleForLiveTrading}. ${signal.explanation}`,
  }));

  const backtestDoc: RagDocument = {
    id: "backtest:latest",
    title: state.backtest.strategyName,
    kind: "backtest",
    timestamp: state.backtest.endedAt,
    content: `Backtest from ${state.backtest.startedAt} to ${state.backtest.endedAt}. Return ${state.backtest.totalReturnPct}%. Max drawdown ${state.backtest.maxDrawdownPct}%. Sharpe ${state.backtest.sharpe}. Sortino ${state.backtest.sortino}. Win rate ${state.backtest.winRatePct}%. Turnover ${state.backtest.turnoverUsd}. Assumptions: ${state.backtest.assumptions.join(" ")}`,
  };

  const paperDoc: RagDocument = {
    id: "paper:portfolio",
    title: "Paper trading portfolio",
    kind: "paper",
    timestamp: state.data.generatedAt,
    content: `Paper portfolio has ${state.paper.positions.length} positions and ${state.paper.trades.length} ledger events. Daily PnL ${state.paper.dailyPnlUsd}. Weekly PnL ${state.paper.weeklyPnlUsd}. Rejected signals ${state.paper.rejectedSignals.length}. Risk limits: max position ${state.paper.riskLimits.maxPositionUsd}, max signal risk ${state.paper.riskLimits.maxSignalRiskScore}, min liquidity ${state.paper.riskLimits.minLiquidityScore}, max holding hours ${state.paper.riskLimits.maxHoldingHours ?? 72}. Full balances are redacted from LLM context.`,
  };

  const scoreboardDoc: RagDocument = {
    id: "scoreboard:edge",
    title: "Edge scoreboard",
    kind: "scoreboard",
    timestamp: state.edgeScoreboard.updatedAt,
    content: `Edge scoreboard tracks ${state.edgeScoreboard.rows.length} signal families with ${state.edgeScoreboard.totals.ledgerEventCount} paper ledger events and total PnL ${state.edgeScoreboard.totals.totalPnlUsd}. Underperforming families: ${state.edgeScoreboard.rows
      .filter((row) => row.status === "underperforming")
      .map((row) => row.kind)
      .join(", ") || "none"}. Family summaries: ${state.edgeScoreboard.rows
      .map(
        (row) =>
          `${row.kind} status ${row.status}, generated ${row.generatedCount}, accepted ${row.filledCount}, rejected ${row.rejectedCount}, closed ${row.closedCount}, win rate ${row.winRatePct}%, total PnL ${row.totalPnlUsd}, avg edge ${row.averageExpectedEdgeBps} bps, avg decay ${row.averageEdgeDecayBps} bps.`,
      )
      .join(" ")}`,
  };

  const runbookDoc: RagDocument = {
    id: "ops:runbook",
    title: `Operational runbook ${state.operationalRunbook.status}`,
    kind: "ops",
    timestamp: state.operationalRunbook.generatedAt,
    content: `Operational runbook status ${state.operationalRunbook.status}. ${state.operationalRunbook.summary}. Action required ${state.operationalRunbook.actionRequiredCount}. Blocked ${state.operationalRunbook.blockedCount}. Critical ${state.operationalRunbook.criticalStepCount}. Steps: ${state.operationalRunbook.steps
      .slice(0, 6)
      .map(
        (step) =>
          `${step.title} status ${step.status}, severity ${step.severity}, area ${step.area}. Trigger: ${step.trigger}. Action: ${step.action}. Verification: ${step.verification}. Evidence: ${step.evidence}. Blocks paper ${step.blocksPaperTrading}. Blocks live ${step.blocksLiveTrading}.`,
      )
      .join(" ")}`,
  };

  const readiness = state.tinyLiveReadiness;
  const readinessDoc: RagDocument = {
    id: "ops:readiness",
    title: `Tiny-live readiness ${readiness.status}`,
    kind: "ops",
    timestamp: readiness.generatedAt,
    content: `Tiny-live readiness status ${readiness.status}. ${readiness.summary}. ${readinessStatusPhrase(readiness.status)}. Evidence days ${readiness.evidenceDays}, minimum ${readiness.minimums.evidenceDays}. Closed trades ${readiness.closedTradeCount}. Blockers ${readiness.blockerCount}, critical ${readiness.criticalBlockerCount}. Candidate: ${formatReadinessCandidate(readiness.candidate)}. Memo conclusion: ${readiness.memo.conclusion}. Evidence window: ${readiness.memo.evidenceWindow}. Required next evidence: ${readiness.memo.requiredNextEvidence}. Blockers: ${readiness.blockers
      .slice(0, 8)
      .map(
        (blocker) =>
          `${blocker.severity} ${blocker.code}: ${blocker.message} Evidence: ${blocker.evidence}.`,
      )
      .join(" ")}`,
  };

  return [
    ...riskDocs,
    ...signalDocs,
    backtestDoc,
    paperDoc,
    scoreboardDoc,
    runbookDoc,
    readinessDoc,
  ];
}

export function retrieveDocuments(
  query: string,
  documents: RagDocument[],
  topK = 6,
): RagDocument[] {
  const queryTokens = tokenize(query);

  return documents
    .map((document) => ({
      document,
      score:
        overlapScore(queryTokens, tokenize(`${document.title} ${document.content}`)) +
        freshnessBoost(document.timestamp),
    }))
    .sort((a, b) => b.score - a.score)
    .slice(0, topK)
    .map((entry) => entry.document);
}

export function formatRagContext(documents: RagDocument[], maxChars: number): string {
  let output = "";

  for (const document of documents) {
    const next = `\n[SOURCE ${document.id}] ${document.title}\n${document.content}\n`;
    if ((output + next).length > maxChars) break;
    output += next;
  }

  return output.trim();
}

function tokenize(input: string): Set<string> {
  return new Set(
    input
      .toLowerCase()
      .split(/[^a-z0-9.]+/)
      .filter((token) => token.length > 2),
  );
}

function overlapScore(queryTokens: Set<string>, documentTokens: Set<string>): number {
  let score = 0;
  for (const token of queryTokens) {
    if (documentTokens.has(token)) score += token.length > 5 ? 2 : 1;
  }
  return score;
}

function freshnessBoost(timestamp?: string): number {
  if (!timestamp) return 0;
  return 0.1;
}

function formatReadinessCandidate(
  candidate: DashboardState["tinyLiveReadiness"]["candidate"],
): string {
  if (!candidate) return "none";
  return `${candidate.kind} status ${candidate.status}, closed ${candidate.closedCount}, total PnL ${candidate.totalPnlUsd}, win rate ${candidate.winRatePct}%, acceptance rate ${candidate.acceptanceRatePct}%, average expected edge ${candidate.averageExpectedEdgeBps} bps. Recommendation: ${candidate.recommendation}`;
}

function readinessStatusPhrase(
  status: DashboardState["tinyLiveReadiness"]["status"],
): string {
  if (status === "candidate_review") {
    return "Candidate review means human review only, not live execution approval.";
  }
  if (status === "watchlist") {
    return "Watchlist means evidence may be forming, but a readiness upgrade is still blocked.";
  }
  return "No go means do not advance toward tiny-live review.";
}
