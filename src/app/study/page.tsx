import Link from "next/link";
import { Activity } from "lucide-react";
import { MetricTile, SectionHeader } from "@/components/dashboard/ui";
import { requireBrowserSession } from "@/lib/auth/page-session";
import { loadTradingRuntimeStatus } from "@/lib/trading/runtime-status";

export const dynamic = "force-dynamic";
const usd = (n: number | undefined) => n === undefined ? "Unavailable" : new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", maximumFractionDigits: n !== 0 && Math.abs(n) < 0.01 ? 4 : 2,
}).format(n);

export default async function StudyPage() {
  await requireBrowserSession();
  const report = loadTradingRuntimeStatus();
  return <main style={{ maxWidth: 1150, margin: "0 auto", padding: "36px 24px" }}>
    <Link href="/" className="muted">← Valor dashboard</Link>
    <section className="section-band" style={{ marginTop: 24 }}>
      <SectionHeader icon={<Activity size={20} aria-hidden="true" />} title="Account growth study"
        subtitle="Execution ledger • 90-day study begins with the first live fill" />
      <p><strong>{report.mode?.toUpperCase() ?? "AWAITING RUNTIME"}</strong> · {report.status}
        {report.timestamp && <> · Updated {new Date(report.timestamp).toLocaleString("en-US", { timeZone: "America/Chicago" })} Chicago</>}</p>
      {report.status === "unconfigured" || report.status === "unavailable" ?
        <p className="muted">The study ledger is not connected. No account performance is available yet.</p> : <>
        <div className="metric-grid">
          <MetricTile label="Account equity" value={usd(report.equity)} sub={`Starting capital ${usd(report.startingCash)}`} tone="neutral" />
          <MetricTile label="Realized trading P&L" value={usd(report.realizedPnl)} sub={report.mode === "paper" ? "After simulated trading fees" : report.feesProvisional ? "Includes fees awaiting broker postings" : "After recorded execution fees"} tone={(report.realizedPnl ?? 0) >= 0 ? "good" : "bad"} />
          <MetricTile label="Estimated operating costs" value={usd(report.operatingCosts)} sub={`Hosting and model use · models ${usd(report.modelCost)}`} tone="neutral" />
          <MetricTile label="P&L after operating costs" value={usd(report.afterOperatingCosts)} sub="Runtime-period estimate; excludes tax and invoice adjustments" tone={report.afterOperatingCosts === undefined ? "neutral" : report.afterOperatingCosts >= 0 ? "good" : "bad"} />
        </div>
        <div className="panel" style={{ marginTop: 24 }}>
          <h2>Runtime controls</h2>
          <p>Live study clock: {report.studyStarted ? `${report.studyStarted} to ${report.studyEnd}` : "Not started — awaiting the first live fill"}</p>
          <p>Trading halt: <strong>{report.halt || "None recorded"}</strong></p>
          <p>Entry pause: {report.entryPause || "None recorded"}</p>
          {report.entrySessionOpen !== undefined && <p>Entry window: {report.entrySessionOpen ? "Open" : "Closed — weekdays 13:00–21:00 UTC; protection remains active"}</p>}
          <p>Supervisor: {report.supervisor}</p>
          <p>Model connection: {report.modelConnection}{report.modelConnection === "blocked" && " — credentials need attention; entries remain paused"}</p>
          <p>Active strategy: <code>{report.activeStrategy}</code></p>
          <p>Learning: {report.learning}</p>
          <p>{report.positionCount} positions · {report.pendingOrderCount} pending orders · {usd(report.cash)} cash</p>
          <p>Unrealized trading P&L: {usd(report.unrealizedPnl)}</p>
          {report.status === "stale" && <p className="bad-text">The latest ledger snapshot is stale. These figures do not confirm current account state.</p>}
        </div>
        <div className="panel" style={{ marginTop: 24 }}>
          <h2>News in decisions</h2>
          <p>Feed status at snapshot: <strong>{report.newsStatus}</strong>{report.newsCheckedAt && <> · Checked {new Date(report.newsCheckedAt).toLocaleString("en-US", {timeZone: "America/Chicago"})} Chicago</>}</p>
          <p className="muted">Crypto headlines from Alpaca/Benzinga and official Fed monetary-policy releases. Both reviewers assess news before discretionary trades. Missing crypto coverage pauses new entries; missing Fed coverage requires an explicit uncertainty assessment. Protective exits continue.</p>
          {!!report.newsHeadlines?.length && <ul>{report.newsHeadlines.map(item => <li key={item.url}>
            <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}</a> <span className="muted">({item.source === "alpaca_crypto" ? "Alpaca / Benzinga" : item.source === "federal_reserve" ? "Federal Reserve" : item.source})</span>
          </li>)}</ul>}
          <p className="muted">Coverage is limited to the retrieved headlines, with possible provider delay. A quiet feed is not a news-based veto.</p>
        </div>
        {report.mode === "demo" && <div className="panel" style={{ marginTop: 24 }}>
          <h2>Broker validation</h2>
          <p>Order drill: <strong>{report.demoDrill}</strong>. Engineering orders are excluded from the strategy sample.</p>
          <p>Forward evidence: {report.strategyTrades ?? 0} / 30 completed strategy trades · {report.activeSessions ?? 0} / 10 active sessions.</p>
          <p>Fee posting days: {report.feePostingDays ?? 0} / 3 · {report.feesProvisional ? "Fee estimates remain provisional" : "No outstanding fee estimates"}.</p>
          <p>Broker protection: {report.protectionComplete ? "Confirmed at the snapshot time" : "Needs verification"}.</p>
          <p className="muted">These checks do not activate live trading or establish profitability. Broker stop-limit orders can remain unfilled during price gaps.</p>
        </div>}
        <p className="muted">{report.mode === "paper" ? "Paper fills are simulated. They do not establish live execution quality or profitability." :
          report.mode === "demo" ? "Broker-demo orders use simulated funds." : "Live results require broker reconciliation."}</p>
      </>}
      <p className="muted">Starting rules: $25 maximum position, $15 planned trade loss, $50 daily loss, 1× leverage. Position caps rise in steps when capital doubles and shrink during drawdowns. Price gaps and outages can exceed planned losses.</p>
    </section>
  </main>;
}
