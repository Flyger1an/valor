import { LineChart } from "lucide-react";
import { BacktestPanel } from "@/components/dashboard/panels/backtest-panel";
import { SectionHeader } from "@/components/dashboard/ui";
import { getDashboardState } from "@/lib/dashboard/get-dashboard-state";

export default async function BacktestsPage() {
  const state = await getDashboardState();

  return (
    <section className="section-band page-band">
      <SectionHeader
        icon={<LineChart size={18} aria-hidden="true" />}
        title="Backtests"
        subtitle={`${state.backtest.strategyName} from ${state.backtest.startedAt} to ${state.backtest.endedAt}`}
      />
      <BacktestPanel backtest={state.backtest} />
    </section>
  );
}