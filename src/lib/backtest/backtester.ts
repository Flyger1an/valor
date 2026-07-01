import type {
  BacktestReport,
  BacktestTrade,
  EquityPoint,
  HistoricalPoint,
} from "@/lib/domain/types";
import {
  annualizeDailySharpe,
  clamp,
  pctChange,
  round,
  sortinoRatio,
} from "@/lib/utils/math";

export interface BasisBacktestConfig {
  strategyName: string;
  startingCashUsd: number;
  maxPositionPct: number;
  entryEdgeBps: number;
  exitEdgeBps: number;
  feeBps: number;
  slippageBps: number;
}

const DEFAULT_CONFIG: BasisBacktestConfig = {
  strategyName: "BTC spot/perp carry sandbox",
  startingCashUsd: 100_000,
  maxPositionPct: 0.28,
  entryEdgeBps: 55,
  exitEdgeBps: 18,
  feeBps: 4,
  slippageBps: 3,
};

export function runBasisCarryBacktest(
  history: HistoricalPoint[],
  config: Partial<BasisBacktestConfig> = {},
): BacktestReport {
  if (history.length < 2) {
    throw new Error("At least two historical points are required for backtesting.");
  }

  const cfg = { ...DEFAULT_CONFIG, ...config };
  let cash = cfg.startingCashUsd;
  let positionNotional = 0;
  let entryBasisBps = 0;
  let peakEquity = cfg.startingCashUsd;
  let turnoverUsd = 0;
  let totalFeesUsd = 0;
  let realizedPnlUsd = 0;
  let activeDays = 0;

  const trades: BacktestTrade[] = [];
  const equityCurve: EquityPoint[] = [
    { timestamp: history[0].timestamp, equity: cash, drawdownPct: 0 },
  ];
  const dailyReturns: number[] = [];

  for (let i = 1; i < history.length; i += 1) {
    const previous = history[i - 1];
    const current = history[i];
    const basisBps =
      ((current.perpPrice - current.spotPrice) / current.spotPrice) * 10_000;
    const fundingSevenDayBps = current.fundingRate8h * 10_000 * 21;
    const totalEdgeBps = basisBps + fundingSevenDayBps;

    if (positionNotional > 0) {
      activeDays += 1;
      const spotReturn = pctChange(current.spotPrice, previous.spotPrice);
      const perpReturn = pctChange(current.perpPrice, previous.perpPrice);
      const convergencePnl = positionNotional * (spotReturn - perpReturn);
      const fundingPnl = positionNotional * current.fundingRate8h * 3;
      const dayPnl = convergencePnl + fundingPnl;
      cash += dayPnl;
      realizedPnlUsd += dayPnl;
    }

    const shouldEnter = positionNotional === 0 && totalEdgeBps >= cfg.entryEdgeBps;
    const shouldExit =
      positionNotional > 0 &&
      (totalEdgeBps <= cfg.exitEdgeBps || basisBps < entryBasisBps * 0.35);

    if (shouldEnter) {
      positionNotional = cash * cfg.maxPositionPct;
      entryBasisBps = basisBps;
      const feesUsd = positionNotional * (cfg.feeBps / 10_000) * 2;
      const slippageUsd = positionNotional * (cfg.slippageBps / 10_000) * 2;
      cash -= feesUsd + slippageUsd;
      turnoverUsd += positionNotional * 2;
      totalFeesUsd += feesUsd;
      trades.push({
        id: `bt-enter-${current.timestamp}`,
        timestamp: current.timestamp,
        action: "enter",
        side: "basis_short_perp",
        notionalUsd: round(positionNotional, 2),
        feesUsd: round(feesUsd, 2),
        slippageUsd: round(slippageUsd, 2),
        fundingUsd: 0,
        realizedPnlUsd: 0,
        reason: `Entered when estimated carry edge reached ${round(
          totalEdgeBps,
          1,
        )} bps.`,
      });
    } else if (shouldExit) {
      const feesUsd = positionNotional * (cfg.feeBps / 10_000) * 2;
      const slippageUsd = positionNotional * (cfg.slippageBps / 10_000) * 2;
      cash -= feesUsd + slippageUsd;
      turnoverUsd += positionNotional * 2;
      totalFeesUsd += feesUsd;
      trades.push({
        id: `bt-exit-${current.timestamp}`,
        timestamp: current.timestamp,
        action: "exit",
        side: "flat",
        notionalUsd: round(positionNotional, 2),
        feesUsd: round(feesUsd, 2),
        slippageUsd: round(slippageUsd, 2),
        fundingUsd: 0,
        realizedPnlUsd: round(realizedPnlUsd, 2),
        reason: `Exited after basis compressed to ${round(basisBps, 1)} bps.`,
      });
      positionNotional = 0;
      entryBasisBps = 0;
      realizedPnlUsd = 0;
    }

    const priorEquity = equityCurve[equityCurve.length - 1].equity;
    const equity = cash;
    peakEquity = Math.max(peakEquity, equity);
    const drawdownPct = peakEquity === 0 ? 0 : ((peakEquity - equity) / peakEquity) * 100;
    equityCurve.push({
      timestamp: current.timestamp,
      equity: round(equity, 2),
      drawdownPct: round(drawdownPct, 2),
    });
    dailyReturns.push(pctChange(equity, priorEquity));
  }

  const winningTrades = trades.filter((trade) => trade.realizedPnlUsd > 0).length;
  const exitTrades = trades.filter((trade) => trade.action === "exit").length;
  const endingEquityUsd = equityCurve[equityCurve.length - 1].equity;
  const sortino = sortinoRatio(dailyReturns);

  return {
    strategyName: cfg.strategyName,
    startedAt: history[0].timestamp,
    endedAt: history[history.length - 1].timestamp,
    startingCashUsd: cfg.startingCashUsd,
    endingEquityUsd,
    totalReturnPct: round(
      ((endingEquityUsd - cfg.startingCashUsd) / cfg.startingCashUsd) * 100,
      2,
    ),
    maxDrawdownPct: round(
      Math.max(...equityCurve.map((point) => point.drawdownPct)),
      2,
    ),
    sharpe: round(annualizeDailySharpe(dailyReturns), 2),
    sortino: sortino === null ? null : round(sortino, 2),
    winRatePct: exitTrades === 0 ? 0 : round((winningTrades / exitTrades) * 100, 1),
    exposureAvgPct: round((activeDays / (history.length - 1)) * cfg.maxPositionPct * 100, 1),
    turnoverUsd: round(turnoverUsd, 2),
    totalFeesUsd: round(totalFeesUsd, 2),
    trades,
    equityCurve,
    assumptions: [
      `Entry when basis plus 7-day projected funding is at least ${cfg.entryEdgeBps} bps.`,
      `Exit when edge falls below ${cfg.exitEdgeBps} bps or basis compresses by 65%.`,
      `${cfg.feeBps} bps fee and ${cfg.slippageBps} bps slippage are charged on both legs.`,
      `Position size is capped at ${round(cfg.maxPositionPct * 100, 1)}% of equity with no leverage.`,
    ],
  };
}

export function maxBacktestPositionUsd(
  equityUsd: number,
  maxPositionPct: number,
): number {
  return round(equityUsd * clamp(maxPositionPct, 0, 1), 2);
}
