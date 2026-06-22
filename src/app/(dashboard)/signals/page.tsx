import { Radar } from "lucide-react";
import { SectionHeader } from "@/components/dashboard/ui";
import { SignalsTable } from "@/components/signals-table";
import { getDashboardState } from "@/lib/dashboard/get-dashboard-state";

export default async function SignalsPage() {
  const state = await getDashboardState();

  return (
    <section className="section-band page-band">
      <SectionHeader
        icon={<Radar size={18} aria-hidden="true" />}
        title="Signals"
        subtitle={`${state.signals.length} generated; ${state.signals.filter((signal) => signal.eligibleForPaperTrading).length} paper-eligible; live eligibility intentionally disabled`}
      />
      <SignalsTable signals={state.signals} />
    </section>
  );
}