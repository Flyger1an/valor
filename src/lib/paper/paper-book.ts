import type {
  MarketRiskState,
  PaperPortfolio,
  PaperPosition,
  PaperTrade,
  RelativeValueSignal,
} from "@/lib/domain/types";
import {
  DEFAULT_PAPER_LIMITS,
  simulatePaperPortfolio,
} from "@/lib/paper/paper-broker";
import { round } from "@/lib/utils/math";

const EXIT_EDGE_BPS = 18;
const FEE_BPS = 8;

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

export function advancePaperBook(input: {
  previous?: PaperPortfolio;
  signals: RelativeValueSignal[];
  risk: MarketRiskState;
  timestamp: string;
  equityHistory?: PaperEquityPoint[];
}): PaperBookUpdate {
  const base =
    input.previous ??
    simulatePaperPortfolio({
      signals: [],
      risk: input.risk,
      limits: DEFAULT_PAPER_LIMITS,
    });

  const signalMap = new Map(input.signals.map((signal) => [signal.id, signal]));
  const positions: PaperPosition[] = [];
  const trades: PaperTrade[] = [...base.trades];
  const rejectedSignals: PaperTrade[] = [...base.rejectedSignals];
  let realizedPnlUsd = estimateRealizedPnl(base);
  let usedNotional = 0;
  let closed = 0;
  let marked = 0;

  for (const position of base.positions) {
    const signal = signalMap.get(position.signalId);
    const shouldClose =
      !signal ||
      signal.direction === "watch_only" ||
      signal.direction === "risk_off" ||
      signal.expectedEdgeBps < EXIT_EDGE_BPS ||
      !DEFAULT_PAPER_LIMITS.allowWhenRiskState.includes(input.risk.state);

    if (shouldClose) {
      const exitPnl = position.markPnlUsd;
      const feesUsd = position.notionalUsd * (FEE_BPS / 10_000);
      realizedPnlUsd += exitPnl - feesUsd;
      closed += 1;
      trades.push({
        id: `paper-close-${position.id}-${Date.now()}`,
        signalId: position.signalId,
        timestamp: input.timestamp,
        assetPair: position.assetPair,
        venue: position.venue,
        direction: position.direction,
        notionalUsd: position.notionalUsd,
        feesUsd: round(feesUsd, 2),
        status: "filled",
        reason: signal
          ? `Closed: edge ${signal.expectedEdgeBps.toFixed(1)} bps / risk ${input.risk.state}.`
          : "Closed: signal no longer active.",
      });
      continue;
    }

    const feesUsd = position.notionalUsd * (FEE_BPS / 10_000);
    const edgeDelta = signal.expectedEdgeBps - position.entryEdgeBps;
    const markPnlUsd = round(
      position.notionalUsd * (edgeDelta / 10_000) * signal.confidence - feesUsd * 0.25,
      2,
    );

    positions.push({
      ...position,
      markPnlUsd,
    });
    marked += 1;
    usedNotional += position.notionalUsd;
  }

  const heldSignalIds = new Set(positions.map((position) => position.signalId));
  let opened = 0;

  for (const signal of input.signals.filter((row) => row.eligibleForPaperTrading)) {
    if (heldSignalIds.has(signal.id)) continue;
    if (usedNotional >= base.cashUsd * DEFAULT_PAPER_LIMITS.maxPortfolioNotionalPct) {
      rejectedSignals.push(rejectTrade(signal, "Portfolio notional limit reached.", input.timestamp));
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
        base.cashUsd * 0.08,
        (signal.opportunityScore / 100) * DEFAULT_PAPER_LIMITS.maxPositionUsd,
      ),
      2,
    );
    const feesUsd = round(notionalUsd * (FEE_BPS / 10_000), 2);

    positions.push({
      id: `paper-pos-${signal.id}`,
      signalId: signal.id,
      assetPair: signal.assetPair,
      venue: signal.venue,
      direction: signal.direction,
      notionalUsd,
      entryEdgeBps: signal.expectedEdgeBps,
      markPnlUsd: round(-feesUsd, 2),
      openedAt: input.timestamp,
    });
    trades.push({
      id: `paper-open-${signal.id}-${Date.now()}`,
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

  const openMarkPnl = positions.reduce((sum, position) => sum + position.markPnlUsd, 0);
  const totalFees = trades
    .filter((trade) => trade.timestamp === input.timestamp)
    .reduce((sum, trade) => sum + trade.feesUsd, 0);
  const equityUsd = round(base.cashUsd + realizedPnlUsd + openMarkPnl - totalFees, 2);
  const equityHistory = [
    ...(input.equityHistory ?? []),
    {
      timestamp: input.timestamp,
      equity: equityUsd,
      cashUsd: base.cashUsd,
      openPositions: positions.length,
      realizedPnlUsd: round(realizedPnlUsd, 2),
    },
  ].slice(-240);

  const previousEquity = input.equityHistory?.at(-1)?.equity ?? base.equityUsd;
  const dayWindow = equityHistory.slice(-48);
  const weekWindow = equityHistory.slice(-336);

  return {
    portfolio: {
      cashUsd: base.cashUsd,
      equityUsd,
      dailyPnlUsd: round(equityUsd - previousEquity, 2),
      weeklyPnlUsd: round(
        equityUsd - (weekWindow[0]?.equity ?? equityUsd),
        2,
      ),
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
    id: `paper-reject-${signal.id}-${Date.now()}`,
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

function estimateRealizedPnl(portfolio: PaperPortfolio): number {
  return round(portfolio.equityUsd - portfolio.cashUsd - positionMarkTotal(portfolio), 2);
}

function positionMarkTotal(portfolio: PaperPortfolio): number {
  return portfolio.positions.reduce((sum, position) => sum + position.markPnlUsd, 0);
}