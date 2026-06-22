import type {
  MarketRiskState,
  PaperPortfolio,
  PaperPosition,
  PaperRiskLimits,
  PaperTrade,
  RelativeValueSignal,
  RiskState,
} from "@/lib/domain/types";
import { round } from "@/lib/utils/math";

export const DEFAULT_PAPER_LIMITS: PaperRiskLimits = {
  maxPositionUsd: 12_500,
  maxPortfolioNotionalPct: 0.5,
  maxSignalRiskScore: 70,
  minLiquidityScore: 45,
  allowWhenRiskState: ["Green", "Yellow", "Red"],
};

export function simulatePaperPortfolio(input: {
  signals: RelativeValueSignal[];
  risk: MarketRiskState;
  startingCashUsd?: number;
  limits?: Partial<PaperRiskLimits>;
}): PaperPortfolio {
  const startingCashUsd = input.startingCashUsd ?? 100_000;
  const limits = { ...DEFAULT_PAPER_LIMITS, ...input.limits };
  const positions: PaperPosition[] = [];
  const trades: PaperTrade[] = [];
  const rejectedSignals: PaperTrade[] = [];
  let usedNotional = 0;

  input.signals
    .filter((signal) => signal.expectedEdgeBps > 0)
    .slice(0, 8)
    .forEach((signal) => {
      const rejection = rejectionReason(signal, input.risk.state, limits, usedNotional, startingCashUsd);
      const notionalUsd = Math.min(
        limits.maxPositionUsd,
        startingCashUsd * 0.08,
        (signal.opportunityScore / 100) * limits.maxPositionUsd,
      );
      const timestamp = signal.timestamp;

      if (rejection) {
        rejectedSignals.push({
          id: `paper-reject-${signal.id}`,
          signalId: signal.id,
          timestamp,
          assetPair: signal.assetPair,
          venue: signal.venue,
          direction: signal.direction,
          notionalUsd: round(Math.max(notionalUsd, 0), 2),
          feesUsd: 0,
          status: "rejected",
          reason: rejection,
        });
        return;
      }

      const feesUsd = notionalUsd * 0.0008;
      const markPnlUsd = notionalUsd * (signal.expectedEdgeBps / 10_000) * signal.confidence;

      positions.push({
        id: `paper-pos-${signal.id}`,
        signalId: signal.id,
        assetPair: signal.assetPair,
        venue: signal.venue,
        direction: signal.direction,
        notionalUsd: round(notionalUsd, 2),
        entryEdgeBps: signal.expectedEdgeBps,
        markPnlUsd: round(markPnlUsd - feesUsd, 2),
        openedAt: timestamp,
      });

      trades.push({
        id: `paper-fill-${signal.id}`,
        signalId: signal.id,
        timestamp,
        assetPair: signal.assetPair,
        venue: signal.venue,
        direction: signal.direction,
        notionalUsd: round(notionalUsd, 2),
        feesUsd: round(feesUsd, 2),
        status: "filled",
        reason: "Signal passed paper-trading risk limits.",
      });

      usedNotional += notionalUsd;
    });

  const markPnl = positions.reduce((sum, position) => sum + position.markPnlUsd, 0);
  const totalFees = trades.reduce((sum, trade) => sum + trade.feesUsd, 0);

  return {
    cashUsd: round(startingCashUsd - totalFees, 2),
    equityUsd: round(startingCashUsd + markPnl - totalFees, 2),
    dailyPnlUsd: round(markPnl - totalFees, 2),
    weeklyPnlUsd: round((markPnl - totalFees) * 2.4, 2),
    positions,
    trades,
    rejectedSignals,
    riskLimits: limits,
  };
}

function rejectionReason(
  signal: RelativeValueSignal,
  riskState: RiskState,
  limits: PaperRiskLimits,
  usedNotional: number,
  startingCashUsd: number,
): string | null {
  if (!limits.allowWhenRiskState.includes(riskState)) {
    return `Market risk state ${riskState} blocks new paper trades.`;
  }

  if (!signal.eligibleForPaperTrading) {
    return "Signal is marked ineligible for paper trading.";
  }

  if (signal.riskScore > limits.maxSignalRiskScore) {
    return `Signal risk score ${signal.riskScore} exceeds limit ${limits.maxSignalRiskScore}.`;
  }

  if (signal.liquidityScore < limits.minLiquidityScore) {
    return `Liquidity score ${signal.liquidityScore} is below limit ${limits.minLiquidityScore}.`;
  }

  if (usedNotional >= startingCashUsd * limits.maxPortfolioNotionalPct) {
    return "Portfolio notional limit reached.";
  }

  return null;
}
