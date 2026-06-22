import { Ban, Gauge } from "lucide-react";
import {
  MetricTile,
  OpportunityRow,
  SectionHeader,
  formatDateTime,
  riskTone,
} from "@/components/dashboard/ui";
import { OperationalControls } from "@/components/operational-controls";
import { getDashboardState } from "@/lib/dashboard/get-dashboard-state";
import { money, signedMoney } from "@/lib/dashboard/format";

export default async function OverviewPage() {
  const state = await getDashboardState();
  const topSignals = state.signals.slice(0, 4);

  return (
    <section className="section-band page-band">
      <SectionHeader
        icon={<Gauge size={18} aria-hidden="true" />}
        title="Overview"
        subtitle={`Data timestamp ${formatDateTime(state.data.generatedAt)}`}
      />
      <div className="metric-grid">
        <MetricTile
          label="Paper Equity"
          value={money(state.paper.equityUsd)}
          sub={`${signedMoney(state.paper.dailyPnlUsd)} today`}
          tone={state.paper.dailyPnlUsd >= 0 ? "good" : "bad"}
        />
        <MetricTile
          label="Risk State"
          value={state.risk.state}
          sub={`${state.risk.score.toFixed(1)} / 100 risk score`}
          tone={riskTone(state.risk.state)}
        />
        <MetricTile
          label="Top Opportunity"
          value={topSignals[0]?.opportunityScore.toFixed(1) ?? "0"}
          sub={topSignals[0]?.assetPair ?? "No active signal"}
          tone="info"
        />
        <MetricTile
          label="Backtest Return"
          value={`${state.backtest.totalReturnPct.toFixed(2)}%`}
          sub={`${state.backtest.sharpe.toFixed(2)} Sharpe`}
          tone={state.backtest.totalReturnPct >= 0 ? "good" : "bad"}
        />
      </div>

      <div className="overview-grid">
        <div className="panel">
          <h3>Top Opportunities</h3>
          <div className="opportunity-list">
            {topSignals.map((signal) => (
              <OpportunityRow key={signal.id} signal={signal} />
            ))}
          </div>
        </div>
        <div className="panel risk-panel">
          <h3>Market Risk State</h3>
          <p className="risk-explanation">{state.risk.explanation}</p>
          <div className="restriction-list">
            {state.risk.tradingRestrictions.slice(0, 4).map((restriction) => (
              <div key={restriction.code} className="restriction-row">
                <Ban size={14} aria-hidden="true" />
                <span>{restriction.description}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
      <OperationalControls />
    </section>
  );
}