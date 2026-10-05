import { loadExperimentStatus } from "@/lib/trading/experiment-status";

const usd = (n: number | undefined) => n === undefined ? "Unverified" : new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", maximumFractionDigits: Math.abs(n) > 0 && Math.abs(n) < .01 ? 4 : 2,
}).format(n);
const names = { baseline: "Baseline", kelly: "Kelly shadow", henry: "Henry chaos" };

export function ExperimentComparison() {
  const report = loadExperimentStatus();
  return <section className="panel" style={{ marginTop: 24 }} aria-labelledby="experiment-title">
    <h2 id="experiment-title">Three fictional bankrolls</h2>
    <p>Baseline sizing, conservative Kelly sizing, and Henry’s all-cash concentration share one opportunity stream.
      Each begins with $500 at the same forward start. No refills, transfers, broker orders, or additional model calls.</p>
    {report.status === "unconfigured" || report.status === "unavailable" ?
      <p className="muted">{report.status === "unconfigured" ? "No experiment snapshot is connected. The comparison starts after the local experiment is initialized." :
        "The experiment snapshot is unavailable or invalid. No comparative performance can be shown."}</p> : <>
      <p><strong>Virtual simulation only</strong> · Start {report.epoch} · End {report.end}</p>
      <p>Paper candidates: {report.symbols?.join(", ")}. Every entry remains subject to cost, freshness and risk checks.</p>
      <p className="muted">Updated {report.timestamp}. Market source: {report.source}.
        {report.source === "test_fixture" && " These are synthetic test fixtures, not observed market performance."}</p>
      <p>Snapshot: <strong>{report.captureFresh ? "Current" : "Stale"}</strong> ·
        Portfolio valuation: <strong>{report.marksFresh ? "Known" : "Stale"}</strong> · {report.frames ?? "Unknown"} observations</p>
      {report.quoteStatus && <p>{report.quoteStatus.map(q => `${q.symbol}: ${q.fresh ? "fresh" : "stale / missing"}`).join(" · ")}</p>}
      {!report.captureFresh && <p className="bad-text">The snapshot is stale. Reconnect the private snapshot mirror to confirm current state.</p>}
      {!report.marksFresh && <p className="bad-text">Some provider quotes are stale. They remain ineligible for execution; a recent snapshot does not refresh a quote.</p>}
      {report.halt && <p className="bad-text">Experiment stopped: {report.halt}. History and balances are retained.</p>}
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", minWidth: 850, textAlign: "left", borderCollapse: "collapse" }}>
          <caption style={{ textAlign: "left", padding: "12px 0" }}>Net marked results after modeled trading fees; no profit ranking.</caption>
          <thead><tr>{["Book", "Equity", "Net P&L", "Realized / unrealized", "Fees", "Exposure", "Max drawdown", "Closed / skipped"].map(label =>
            <th key={label} scope="col" style={{ padding: 8 }}>{label}</th>)}</tr></thead>
          <tbody>{report.books?.map(book => <tr key={book.name}>
            <th scope="row" style={{ padding: 8 }}>{names[book.name]}</th>
            <td style={{ padding: 8 }}>{usd(book.equity)}</td><td style={{ padding: 8 }}>{usd(book.netPnl)}</td>
            <td style={{ padding: 8 }}>{usd(book.realizedPnl)} / {usd(book.unrealizedPnl)}</td>
            <td style={{ padding: 8 }}>{usd(book.fees)}</td><td style={{ padding: 8 }}>{usd(book.exposure)}</td>
            <td style={{ padding: 8 }}>{(book.drawdown * 100).toFixed(2)}%</td>
            <td style={{ padding: 8 }}>{book.closedTrades} / {book.skips}</td>
          </tr>)}</tbody>
        </table>
      </div>
      <p>Kelly evidence: {report.evidenceBlocks} / 30 usable synchronized daily blocks. Missing or negative evidence means cash.</p>
      {report.evidenceQuality && <div aria-label="Forward evidence coverage">
        <p>Evidence policy: {report.evidencePolicy}. Day {report.evidenceQuality.day}: {report.evidenceQuality.completeSoFar ?
          "Eligible so far; awaiting the complete day and settlement." : "Excluded so far: " + report.evidenceQuality.invalidReasons.join("; ")} </p>
        <p>First full day for this version: {report.evidenceQuality.firstFullDay ?? "Unverified"}.
          {" "}{report.evidenceQuality.legacyBlocks} earlier blocks retained separately; {report.evidenceQuality.missingDays} missing full days.</p>
        <p>{report.evidenceQuality.observations} observations today · all candidate quotes fresh in {report.evidenceQuality.freshPairFraction === undefined ?
          "unknown" : (100 * report.evidenceQuality.freshPairFraction).toFixed(1) + "%"} ·
          {" "}{report.evidenceQuality.gapsOver60} gaps over 60 seconds.</p>
        <ul>{report.evidenceQuality.staleByAsset.map(s => <li key={s.symbol}>{s.symbol}: {s.count} stale observations,
          {" "}{s.heldCount} while Baseline held inventory; maximum quote age {s.maximumAge.toFixed(1)} seconds.</li>)}</ul>
        <p>Baseline valuation at snapshot: {report.evidenceQuality.valuationKnown ? "Known under the evidence rules" : "Missing a fresh held-inventory mark"}.
          Coverage is not evidence of profitable trading.</p>
      </div>}
      <p>Shared operating expense since the comparison began: estimated {usd(report.sharedOperatingEstimate)};
        verified actual {usd(report.actualSharedCost)}. One third is attributed to each book for comparison;
        it is counted once across the experiment and does not change trading cash.</p>
      {report.books?.map(book => <details key={book.name} style={{ padding: "12px 0" }}>
        <summary>{names[book.name]} · {book.halt || (book.dailyHalt ? "Daily loss pause" : "Active policy")} · {book.policyVersion}</summary>
        <p>Available cash {usd(book.cash)} · Fee escrow {usd(book.feeEscrow)} · Turnover {usd(book.turnover)} ·
          P&L after allocated operating estimate {usd(book.afterOperatingEstimate)}.</p>
        <p>{book.provisionalFees ? "Some modeled fees await virtual settlement." : "No pending modeled fees."} These are never actual broker fee receipts.</p>
        {book.name === "henry" && <p>One position can consume almost the entire fictional bankroll. There is no 20% drawdown halt and no replenishment.</p>}
        {book.sizing.map(s => <p key={s.symbol}>{s.symbol}: raw recommendation {usd(s.raw)}, fractional {usd(s.fractional)},
          capped {usd(s.capped)}. {s.reason}.</p>)}
        {!!book.skipReasons.length && <p>Skip reasons: {book.skipReasons.join("; ")}.</p>}
        {!!book.safetyEvents.length && <p>Recent safety events: {book.safetyEvents.join("; ")}.</p>}
      </details>)}
      <p className="muted">Orders use later observed quotes, adverse slippage, and a declared volume-based liquidity model.
        Approvals are hypothetical rules, not reused AI or broker approvals. Greater profit at greater exposure does not establish better skill or live execution quality.</p>
    </>}
  </section>;
}
