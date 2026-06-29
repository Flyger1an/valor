import type {
  EdgeScoreboard,
  EdgeScoreboardRow,
  EdgeScoreboardStatus,
  PaperPortfolio,
  PaperTrade,
  RelativeValueSignal,
  SignalKind,
} from "@/lib/domain/types";
import { round } from "@/lib/utils/math";

const SIGNAL_KINDS: SignalKind[] = [
  "spot_perp_basis",
  "funding_carry",
  "cross_exchange_premium",
  "btc_eth_ratio",
  "stablecoin_depeg",
  "pair_spread_zscore",
  "volatility_regime",
];

interface MutableRow {
  kind: SignalKind;
  generatedCount: number;
  paperEligibleCount: number;
  openPositionCount: number;
  activeNotionalUsd: number;
  ledgerEventCount: number;
  filledCount: number;
  heldCount: number;
  closedCount: number;
  rejectedCount: number;
  expectedEdgeSum: number;
  signalRiskSum: number;
  markPnlUsd: number;
  realizedPnlUsd: number;
  fundingUsd: number;
  winningClosedCount: number;
  holdingHoursSum: number;
  edgeDecaySum: number;
}

export function buildEdgeScoreboard(input: {
  signals: RelativeValueSignal[];
  paper: PaperPortfolio;
  updatedAt?: string;
}): EdgeScoreboard {
  const signalById = new Map(input.signals.map((signal) => [signal.id, signal]));
  const rows = new Map<SignalKind, MutableRow>();

  for (const signal of input.signals) {
    const row = getRow(rows, signal.kind);
    row.generatedCount += 1;
    row.expectedEdgeSum += signal.expectedEdgeBps;
    row.signalRiskSum += signal.riskScore;
    if (signal.eligibleForPaperTrading) row.paperEligibleCount += 1;
  }

  for (const position of input.paper.positions) {
    const kind =
      position.signalKind ??
      signalById.get(position.signalId)?.kind ??
      kindFromSignalId(position.signalId);
    if (!kind) continue;

    const row = getRow(rows, kind);
    row.openPositionCount += 1;
    row.activeNotionalUsd += position.notionalUsd;
    row.markPnlUsd += position.markPnlUsd;
    row.fundingUsd += position.fundingAccruedUsd ?? 0;
    row.holdingHoursSum += position.holdingHours ?? 0;
    row.edgeDecaySum += Math.max(
      0,
      position.entryEdgeBps - (position.currentEdgeBps ?? position.entryEdgeBps),
    );
  }

  for (const trade of [...input.paper.trades, ...input.paper.rejectedSignals]) {
    const kind = signalById.get(trade.signalId)?.kind ?? kindFromSignalId(trade.signalId);
    if (!kind) continue;

    applyTrade(getRow(rows, kind), trade);
  }

  const finalizedRows = [...rows.values()]
    .map(finalizeRow)
    .sort((a, b) => {
      if (a.status === "underperforming" && b.status !== "underperforming") {
        return -1;
      }
      if (b.status === "underperforming" && a.status !== "underperforming") {
        return 1;
      }
      return b.totalPnlUsd - a.totalPnlUsd || b.generatedCount - a.generatedCount;
    });

  return {
    updatedAt: input.updatedAt ?? new Date().toISOString(),
    rows: finalizedRows,
    totals: {
      generatedCount: sum(finalizedRows, "generatedCount"),
      paperEligibleCount: sum(finalizedRows, "paperEligibleCount"),
      openPositionCount: sum(finalizedRows, "openPositionCount"),
      ledgerEventCount: sum(finalizedRows, "ledgerEventCount"),
      totalPnlUsd: round(sum(finalizedRows, "totalPnlUsd"), 2),
      realizedPnlUsd: round(sum(finalizedRows, "realizedPnlUsd"), 2),
      markPnlUsd: round(sum(finalizedRows, "markPnlUsd"), 2),
      underperformingCount: finalizedRows.filter(
        (row) => row.status === "underperforming",
      ).length,
    },
  };
}

function getRow(rows: Map<SignalKind, MutableRow>, kind: SignalKind): MutableRow {
  const existing = rows.get(kind);
  if (existing) return existing;

  const row: MutableRow = {
    kind,
    generatedCount: 0,
    paperEligibleCount: 0,
    openPositionCount: 0,
    activeNotionalUsd: 0,
    ledgerEventCount: 0,
    filledCount: 0,
    heldCount: 0,
    closedCount: 0,
    rejectedCount: 0,
    expectedEdgeSum: 0,
    signalRiskSum: 0,
    markPnlUsd: 0,
    realizedPnlUsd: 0,
    fundingUsd: 0,
    winningClosedCount: 0,
    holdingHoursSum: 0,
    edgeDecaySum: 0,
  };
  rows.set(kind, row);
  return row;
}

function applyTrade(row: MutableRow, trade: PaperTrade) {
  row.ledgerEventCount += 1;

  if (trade.status === "filled") row.filledCount += 1;
  if (trade.status === "held") row.heldCount += 1;
  if (trade.status === "closed") {
    row.closedCount += 1;
    if ((trade.realizedPnlUsd ?? 0) > 0) row.winningClosedCount += 1;
    row.fundingUsd += trade.fundingUsd ?? 0;
  }
  if (trade.status === "rejected") row.rejectedCount += 1;

  row.realizedPnlUsd += trade.realizedPnlUsd ?? 0;
}

function finalizeRow(row: MutableRow): EdgeScoreboardRow {
  const closedCount = row.closedCount;
  const totalDecisions = row.filledCount + row.rejectedCount;
  const totalPnlUsd = row.realizedPnlUsd + row.markPnlUsd;
  const status = statusFor(row, totalPnlUsd);

  return {
    kind: row.kind,
    generatedCount: row.generatedCount,
    paperEligibleCount: row.paperEligibleCount,
    openPositionCount: row.openPositionCount,
    activeNotionalUsd: round(row.activeNotionalUsd, 2),
    ledgerEventCount: row.ledgerEventCount,
    filledCount: row.filledCount,
    heldCount: row.heldCount,
    closedCount,
    rejectedCount: row.rejectedCount,
    averageExpectedEdgeBps:
      row.generatedCount === 0
        ? 0
        : round(row.expectedEdgeSum / row.generatedCount, 2),
    averageSignalRiskScore:
      row.generatedCount === 0 ? 0 : round(row.signalRiskSum / row.generatedCount, 1),
    markPnlUsd: round(row.markPnlUsd, 2),
    realizedPnlUsd: round(row.realizedPnlUsd, 2),
    totalPnlUsd: round(totalPnlUsd, 2),
    fundingUsd: round(row.fundingUsd, 2),
    winRatePct:
      closedCount === 0 ? 0 : round((row.winningClosedCount / closedCount) * 100, 1),
    acceptanceRatePct:
      totalDecisions === 0 ? 0 : round((row.filledCount / totalDecisions) * 100, 1),
    averageHoldingHours:
      row.openPositionCount === 0
        ? 0
        : round(row.holdingHoursSum / row.openPositionCount, 2),
    averageEdgeDecayBps:
      row.openPositionCount === 0 ? 0 : round(row.edgeDecaySum / row.openPositionCount, 2),
    status,
    recommendation: recommendationFor(status, row, totalPnlUsd),
  };
}

function statusFor(row: MutableRow, totalPnlUsd: number): EdgeScoreboardStatus {
  const evidenceCount = row.filledCount + row.closedCount;
  if (evidenceCount < 3 && row.closedCount === 0) return "insufficient";
  if (row.closedCount > 0 && totalPnlUsd < 0) return "underperforming";
  if (row.closedCount > 0 && totalPnlUsd >= 0 && row.winningClosedCount / row.closedCount >= 0.5) {
    return "proving";
  }
  return "watch";
}

function recommendationFor(
  status: EdgeScoreboardStatus,
  row: MutableRow,
  totalPnlUsd: number,
): string {
  if (status === "underperforming") {
    return "Mark watch-only until the paper ledger shows positive net outcomes after costs.";
  }
  if (status === "proving") {
    return "Keep in paper rotation and keep collecting out-of-sample evidence.";
  }
  if (status === "watch") {
    return totalPnlUsd >= 0
      ? "Continue paper observation; closed-trade evidence is not deep enough yet."
      : "Reduce confidence until more closed paper outcomes arrive.";
  }
  if (row.paperEligibleCount === 0) {
    return "No paper-eligible signals in the current refresh.";
  }
  return "Insufficient paper ledger evidence; collect more cycles before trusting this family.";
}

function kindFromSignalId(signalId: string): SignalKind | null {
  const candidate = signalId.split(":")[0] as SignalKind | undefined;
  return candidate && SIGNAL_KINDS.includes(candidate) ? candidate : null;
}

function sum<T extends keyof EdgeScoreboardRow>(
  rows: EdgeScoreboardRow[],
  key: T,
): number {
  return rows.reduce((total, row) => {
    const value = row[key];
    return typeof value === "number" ? total + value : total;
  }, 0);
}
