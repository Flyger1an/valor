import { Radar } from "lucide-react";
import { BasisChart } from "@/components/basis-chart";
import {
  DataProvenanceStrip,
  SignalJournalPanel,
} from "@/components/dashboard/panels/signal-intel-panel";
import { SectionHeader } from "@/components/dashboard/ui";
import { SignalsTable } from "@/components/signals-table";
import { getDashboardState } from "@/lib/dashboard/get-dashboard-state";

export default async function SignalsPage() {
  const state = await getDashboardState();
  const persistent = state.signalJournal.filter((entry) => entry.sightings >= 2).length;

  return (
    <section className="section-band page-band">
      <SectionHeader
        icon={<Radar size={18} aria-hidden="true" />}
        title="Signals"
        subtitle={`${state.signals.length} generated; ${state.signals.filter((signal) => signal.eligibleForPaperTrading).length} paper-eligible; ${persistent} persistent journal entries`}
      />
      <DataProvenanceStrip provenance={state.dataProvenance} />
      <div className="intel-grid">
        <div className="panel">
          <h3>Signal Journal</h3>
          <p className="muted">
            Edge deltas tracked across refresh cycles. Sightings &gt;= 2 means the
            engine keeps seeing the same dislocation.
          </p>
          <SignalJournalPanel journal={state.signalJournal} />
        </div>
        <div className="panel chart-panel">
          <h3>Basis History</h3>
          <BasisChart history={state.data.backtestHistory} />
        </div>
      </div>
      <SignalsTable signals={state.signals} />
    </section>
  );
}