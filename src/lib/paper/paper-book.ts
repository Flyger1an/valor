import type {
  MarketRiskState,
  PaperPortfolio,
  PaperPosition,
  PaperTrade,
  RelativeValueSignal,
} from "@/lib/domain/types";
import {
  DEFAULT_PAPER_LIMITS,
  FEE_BPS,
  STARTING_CASH_USD,
} from "@/lib/paper/paper-broker";
import { round } from "@/lib/utils/math";

const EXIT_EDGE_BPS = 18;

export interface PaperBookUpdate {
  portfolio: PaperPortfolio;
  equityHistory: PaperEquityPoint[];
  opened: number;
  closed: number;
  marked: number;
}

export interface PaperEquityPoint {
  timestamp: string;
  equity: number;
  cashUsd: number;
  openPositions: number;
  realizedPnlUsd: number;
}

/** A fresh, fully-zeroed paper ledger. */
export function emptyLedgerPortfolio(): PaperPortfolio {
  return {
    cashUsd: STARTING_CASH_USD,
    equityUsd: STARTING_CASH_USD,
    realizedPnlUsd: 0,
    feesPaidUsd: 0,
    dailyPnlUsd: 0,
    weeklyPnlUsd: 0,
    positions: [],
    trades: [],
    rejectedSignals: [],
    riskLimits: DEFAULT_PAPER_LIMITS,
  };
}

/**
 * Advance the persistent paper book by one refresh cycle.
 *
 * The book is an explicit cash ledger, not a reconstruction:
 *   cash  = starting cash + realized PnL − fees paid
 *   equity = cash + unrealized mark-to-market of open positions
 *
 * Fees are charged exactly once (open and close); position marks are pure
 * unrealized PnL with no fees mixed in; and all ids are derived from
 * (signalId/positionId, timestamp) so the same inputs always produce the same
 * output — re-running a cycle is idempotent.
 */
export function advancePaperBook(input: {
  previous?: PaperPortfolio;
  signals: RelativeValueSignal[];
  risk: MarketRiskState;
  timestamp: string;
  equityHistory?: PaperEquityPoint[];
}): PaperBookUpdate {
  const base = input.previous ?? emptyLedgerPortfolio();
  const signalMap = new Map(input.signals.map((signal) => [signal.id, signal]));

  // Explicit ledger carried forward from the previous cycle. Coalesce the
  // ledger fields so books persisted before they existed upgrade cleanly
  // instead of poisoning the math with undefined -> NaN.
  let cashUsd = base.cashUsd ?? STARTING_CASH_USD;
  let realizedPnlUsd = base.realizedPnlUsd ?? 0;
  let feesPaidUsd = base.feesPaidUsd ?? 0;

  const positions: PaperPosition[] = [];
  const trades: PaperTrade[] = [...base.trades];
  const rejectedSignals: PaperTrade[] = [...base.rejectedSignals];
  let opened = 0;
  let closed = 0;
  let marked = 0;

  // 1) Mark or close existing positions.
  for (const position of base.positions) {
    const signal = signalMap.get(position.signalId);
    const shouldClose =
      !signal ||
      signal.direction === "watch_only" ||
      signal.direction === "risk_off" ||
      signal.expectedEdgeBps < EXIT_EDGE_BPS ||
      !DEFAULT_PAPER_LIMITS.allowWhenRiskState.includes(input.risk.state);

    if (shouldClose) {
      const feesUsd = round(position.notionalUsd * (FEE_BPS / 10_000), 2);
      // Realize the last mark, pay the close fee, settle both into cash.
      realizedPnlUsd = round(realizedPnlUsd + position.markPnlUsd, 2);
      feesPaidUsd = round(feesPaidUsd + feesUsd, 2);
      cashUsd = round(cashUsd + position.markPnlUsd - feesUsd, 2);
      closed += 1;
      trades.push({
        id: `paper-close:${position.id}:${input.timestamp}`,
        signalId: position.signalId,
        timestamp: input.timestamp,
        assetPair: position.assetPair,
        venue: position.venue,
        direction: position.direction,
        notionalUsd: position.notionalUsd,
        feesUsd,
        status: "filled",
        reason: signal
          ? `Closed: edge ${signal.expectedEdgeBps.toFixed(1)} bps / risk ${input.risk.state}.`
          : "Closed: signal no longer active.",
      });
      continue;
    }

    // Re-mark to the current edge: pure unrealized PnL, no cash movement.
    const edgeDelta = signal.expectedEdgeBps - position.entryEdgeBps;
    const markPnlUsd = round(
      position.notionalUsd * (edgeDelta / 10_000) * signal.confidence,
      2,
    );
    positions.push({ ...position, markPnlUsd });
    marked += 1;
  }

  // 2) Open new eligible positions, sized against current cash.
  const heldSignalIds = new Set(positions.map((position) => position.signalId));
  let usedNotional = positions.reduce(
    (sum, position) => sum + position.notionalUsd,
    0,
  );
  const notionalCap = cashUsd * DEFAULT_PAPER_LIMITS.maxPortfolioNotionalPct;

  for (const signal of input.signals.filter((row) => row.eligibleForPaperTrading)) {
    if (heldSignalIds.has(signal.id)) continue;

    if (usedNotional >= notionalCap) {
      rejectedSignals.push(
        rejectTrade(signal, "Portfolio notional limit reached.", input.timestamp),
      );
      continue;
    }
    if (signal.riskScore > DEFAULT_PAPER_LIMITS.maxSignalRiskScore) {
      rejectedSignals.push(
        rejectTrade(
          signal,
          `Signal risk score ${signal.riskScore} exceeds limit.`,
          input.timestamp,
        ),
      );
      continue;
    }
    if (signal.liquidityScore < DEFAULT_PAPER_LIMITS.minLiquidityScore) {
      rejectedSignals.push(
        rejectTrade(
          signal,
          `Liquidity score ${signal.liquidityScore} below limit.`,
          input.timestamp,
        ),
      );
      continue;
    }

    const notionalUsd = round(
      Math.min(
        DEFAULT_PAPER_LIMITS.maxPositionUsd,
        cashUsd * 0.08,
        (signal.opportunityScore / 100) * DEFAULT_PAPER_LIMITS.maxPositionUsd,
      ),
      2,
    );
    const feesUsd = round(notionalUsd * (FEE_BPS / 10_000), 2);

    // Pay the open fee from cash; the new position starts flat (mark 0).
    cashUsd = round(cashUsd - feesUsd, 2);
    feesPaidUsd = round(feesPaidUsd + feesUsd, 2);

    positions.push({
      id: `paper-pos-${signal.id}`,
      signalId: signal.id,
      assetPair: signal.assetPair,
      venue: signal.venue,
      direction: signal.direction,
      notionalUsd,
      entryEdgeBps: signal.expectedEdgeBps,
      markPnlUsd: 0,
      openedAt: input.timestamp,
    });
    trades.push({
      id: `paper-open:${signal.id}:${input.timestamp}`,
      signalId: signal.id,
      timestamp: input.timestamp,
      assetPair: signal.assetPair,
      venue: signal.venue,
      direction: signal.direction,
      notionalUsd,
      feesUsd,
      status: "filled",
      reason: "Opened on eligible relative-value signal.",
    });
    usedNotional += notionalUsd;
    opened += 1;
  }

  // 3) Equity = cash + unrealized marks of open positions.
  const openMarkPnl = positions.reduce(
    (sum, position) => sum + position.markPnlUsd,
    0,
  );
  const equityUsd = round(cashUsd + openMarkPnl, 2);

  const equityHistory = [
    ...(input.equityHistory ?? []),
    {
      timestamp: input.timestamp,
      equity: equityUsd,
      cashUsd,
      openPositions: positions.length,
      realizedPnlUsd,
    },
  ].slice(-240);

  const previousEquity = input.equityHistory?.at(-1)?.equity ?? base.equityUsd;
  // NOTE: window sizes assume ~30-min refresh cycles (48/day, 336/week).
  const weekWindow = equityHistory.slice(-336);

  return {
    portfolio: {
      cashUsd,
      equityUsd,
      realizedPnlUsd,
      feesPaidUsd,
      dailyPnlUsd: round(equityUsd - previousEquity, 2),
      weeklyPnlUsd: round(equityUsd - (weekWindow[0]?.equity ?? equityUsd), 2),
      positions,
      trades: trades.slice(0, 200),
      rejectedSignals: rejectedSignals.slice(0, 100),
      riskLimits: DEFAULT_PAPER_LIMITS,
    },
    equityHistory,
    opened,
    closed,
    marked,
  };
}

function rejectTrade(
  signal: RelativeValueSignal,
  reason: string,
  timestamp: string,
): PaperTrade {
  return {
    id: `paper-reject:${signal.id}:${timestamp}`,
    signalId: signal.id,
    timestamp,
    assetPair: signal.assetPair,
    venue: signal.venue,
    direction: signal.direction,
    notionalUsd: 0,
    feesUsd: 0,
    status: "rejected",
    reason,
  };
}
