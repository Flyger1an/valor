import { EquityChart } from "@/components/equity-chart";
import type { BacktestReport } from "@/lib/domain/types";
import { money } from "@/lib/dashboard/format";

export function BacktestPanel({ backtest }: { backtest: BacktestReport }) {
  const metrics = [
    ["Ending Equity", money(backtest.endingEquityUsd)],
    ["Max Drawdown", `${backtest.maxDrawdownPct.toFixed(2)}%`],
    ["Sortino", backtest.sortino.toFixed(2)],
    ["Win Rate", `${backtest.winRatePct.toFixed(1)}%`],
    ["Exposure", `${backtest.exposureAvgPct.toFixed(1)}%`],
    ["Turnover", money(backtest.turnoverUsd)],
  ];

  return (
    <div className="backtest-grid">
      <div className="chart-panel">
        <EquityChart points={backtest.equityCurve} />
      </div>
      <div className="report-panel">
        <div className="mini-metrics">
          {metrics.map(([label, value]) => (
            <div key={label} className="mini-metric">
              <span>{label}</span>
              <strong>{value}</strong>
            </div>
          ))}
        </div>
        <div className="assumptions">
          {backtest.assumptions.map((assumption) => (
            <p key={assumption}>{assumption}</p>
          ))}
        </div>
      </div>
    </div>
  );
}