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
    content: `Paper portfolio has ${state.paper.positions.length} positions and ${state.paper.trades.length} filled trades. Daily PnL ${state.paper.dailyPnlUsd}. Weekly PnL ${state.paper.weeklyPnlUsd}. Rejected signals ${state.paper.rejectedSignals.length}. Risk limits: max position ${state.paper.riskLimits.maxPositionUsd}, max signal risk ${state.paper.riskLimits.maxSignalRiskScore}, min liquidity ${state.paper.riskLimits.minLiquidityScore}. Full balances are redacted from LLM context.`,
  };

  return [...riskDocs, ...signalDocs, backtestDoc, paperDoc];
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
