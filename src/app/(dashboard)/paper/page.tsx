import { CircleDollarSign } from "lucide-react";
import { EquityChart } from "@/components/equity-chart";
import { PaperPanel } from "@/components/dashboard/panels/paper-panel";
import { SectionHeader } from "@/components/dashboard/ui";
import { getDashboardState } from "@/lib/dashboard/get-dashboard-state";

export default async function PaperPage() {
  const state = await getDashboardState();
  const equityCurve = state.equityHistory.map((point) => ({
    timestamp: point.timestamp,
    equity: point.equity,
    drawdownPct: 0,
  }));

  return (
    <section className="section-band page-band">
      <SectionHeader
        icon={<CircleDollarSign size={18} aria-hidden="true" />}
        title="Paper Trading"
        subtitle={`Persistent book across refresh cycles · ${state.paper.positions.length} open · ${state.paper.trades.length} lifetime fills`}
      />
      <div className="panel chart-panel paper-equity-panel">
        <h3>Paper Equity Path</h3>
        {equityCurve.length > 1 ? (
          <EquityChart points={equityCurve} />
        ) : (
          <p className="muted">
            Equity history builds as refresh cycles mark and open positions.
          </p>
        )}
      </div>
      <PaperPanel paper={state.paper} />
    </section>
  );
}