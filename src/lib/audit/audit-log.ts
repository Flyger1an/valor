import type {
  AuditEvent,
  BacktestReport,
  MarketDataBundle,
  MarketRiskState,
  PaperPortfolio,
  RelativeValueSignal,
} from "@/lib/domain/types";

export function createAuditTrail(input: {
  data: MarketDataBundle;
  signals: RelativeValueSignal[];
  risk: MarketRiskState;
  backtest: BacktestReport;
  paper: PaperPortfolio;
}): AuditEvent[] {
  const { data, signals, risk, backtest, paper } = input;
  const events: AuditEvent[] = [
    {
      id: "audit-data-refresh",
      timestamp: data.generatedAt,
      actor: "system",
      action: "data.refresh",
      summary: `Refreshed ${data.markets.length} market snapshots, ${data.stablecoins.length} stablecoin marks, and ${data.advisories.length} advisories.`,
      metadata: {
        markets: data.markets.length,
        stablecoins: data.stablecoins.length,
        advisories: data.advisories.length,
      },
    },
    {
      id: "audit-signals-generated",
      timestamp: data.generatedAt,
      actor: "system",
      action: "signal.generated",
      summary: `Generated ${signals.length} relative-value signals.`,
      metadata: {
        signals: signals.length,
        eligibleForPaper: signals.filter((signal) => signal.eligibleForPaperTrading)
          .length,
      },
    },
    {
      id: "audit-backtest-run",
      timestamp: data.generatedAt,
      actor: "system",
      action: "backtest.run",
      summary: `${backtest.strategyName} ended at $${backtest.endingEquityUsd.toLocaleString()} with ${backtest.maxDrawdownPct.toFixed(2)}% max drawdown.`,
      metadata: {
        totalReturnPct: backtest.totalReturnPct,
        maxDrawdownPct: backtest.maxDrawdownPct,
        trades: backtest.trades.length,
      },
    },
  ];

  risk.activeAlerts.forEach((alert) => {
    events.push({
      id: `audit-alert-${alert.id}`,
      timestamp: alert.timestamp,
      actor: "system",
      action: "risk.alert",
      summary: alert.title,
      metadata: {
        severity: alert.severity,
        category: alert.category,
      },
    });
  });

  paper.trades.forEach((trade) => {
    events.push({
      id: `audit-paper-${trade.id}`,
      timestamp: trade.timestamp,
      actor: "paper_broker",
      action: "paper.trade",
      summary: `${trade.status.toUpperCase()} ${trade.assetPair} ${trade.direction} for $${trade.notionalUsd.toLocaleString()}.`,
      metadata: {
        signalId: trade.signalId,
        status: trade.status,
        feesUsd: trade.feesUsd,
      },
    });
  });

  if (risk.state === "Red" || risk.state === "Black") {
    events.push({
      id: "audit-kill-switch-policy",
      timestamp: risk.updatedAt,
      actor: "live_guard",
      action: "kill_switch.activated",
      summary: `Risk state ${risk.state} activates live trading block policy.`,
      metadata: {
        riskState: risk.state,
        restrictions: risk.tradingRestrictions.length,
      },
    });
  }

  return events.sort((a, b) => b.timestamp.localeCompare(a.timestamp));
}
