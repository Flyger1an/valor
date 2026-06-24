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

/** Per-side paper execution fee, in basis points (taker-like). */
export const FEE_BPS = 8;

/** Starting paper cash for a fresh book. */
export const STARTING_CASH_USD = 100_000;

export function simulatePaperPortfolio(input: {
  signals: RelativeValueSignal[];
  risk: MarketRiskState;
  startingCashUsd?: number;
  limits?: Partial<PaperRiskLimits>;
}): PaperPortfolio {
  const startingCashUsd = input.startingCashUsd ?? STARTING_CASH_USD;
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

      const feesUsd = round(notionalUsd * (FEE_BPS / 10_000), 2);
      // Pure unrealized mark-to-market; fees live in the cash ledger, not the mark.
      const markPnlUsd = round(
        notionalUsd * (signal.expectedEdgeBps / 10_000) * signal.confidence,
        2,
      );

      positions.push({
        id: `paper-pos-${signal.id}`,
        signalId: signal.id,
        assetPair: signal.assetPair,
        venue: signal.venue,
        direction: signal.direction,
        notionalUsd: round(notionalUsd, 2),
        entryEdgeBps: signal.expectedEdgeBps,
        markPnlUsd,
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
        feesUsd,
        status: "filled",
        reason: "Signal passed paper-trading risk limits.",
      });

      usedNotional += notionalUsd;
    });

  const openMarkPnl = positions.reduce((sum, position) => sum + position.markPnlUsd, 0);
  const feesPaidUsd = round(trades.reduce((sum, trade) => sum + trade.feesUsd, 0), 2);
  const cashUsd = round(startingCashUsd - feesPaidUsd, 2);
  const equityUsd = round(cashUsd + openMarkPnl, 2);

  return {
    cashUsd,
    equityUsd,
    realizedPnlUsd: 0,
    feesPaidUsd,
    dailyPnlUsd: round(equityUsd - startingCashUsd, 2),
    weeklyPnlUsd: round((equityUsd - startingCashUsd) * 2.4, 2),
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
