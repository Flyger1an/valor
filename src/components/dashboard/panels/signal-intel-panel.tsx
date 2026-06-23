import type { DashboardState } from "@/lib/dashboard/get-dashboard-state";

export function DataProvenanceStrip({
  provenance,
}: {
  provenance: DashboardState["dataProvenance"];
}) {
  return (
    <div className="provenance-strip">
      <div>
        <strong>Data lineage</strong>
        <p>{provenance.summary}</p>
      </div>
      <div className="provenance-metrics">
        <span>{provenance.liveMarketCount} live</span>
        <span>{provenance.fixtureMarketCount} fixture</span>
        <span>{provenance.liveSharePct}% live share</span>
        {provenance.hasFallbackAdvisory ? (
          <span className="warn-pill">fallback advisory active</span>
        ) : null}
      </div>
    </div>
  );
}

export function SignalJournalPanel({
  journal,
}: {
  journal: DashboardState["signalJournal"];
}) {
  if (journal.length === 0) {
    return <p className="muted">No journal entries yet. Run a refresh cycle.</p>;
  }

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Pair</th>
            <th>Kind</th>
            <th>Sightings</th>
            <th>Edge</th>
            <th>Δ Edge</th>
            <th>Opp</th>
            <th>Paper</th>
          </tr>
        </thead>
        <tbody>
          {journal.map((entry) => (
            <tr key={entry.id}>
              <td className="mono strong">{entry.assetPair}</td>
              <td>{entry.kind.replaceAll("_", " ")}</td>
              <td>{entry.sightings}</td>
              <td>{entry.expectedEdgeBps.toFixed(1)} bps</td>
              <td className={entry.edgeDeltaBps >= 0 ? "good-text" : "bad-text"}>
                {entry.edgeDeltaBps >= 0 ? "+" : ""}
                {entry.edgeDeltaBps.toFixed(1)} bps
              </td>
              <td>{entry.opportunityScore.toFixed(1)}</td>
              <td>{entry.eligibleForPaperTrading ? "yes" : "no"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}