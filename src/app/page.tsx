import {
  Activity,
  AlertTriangle,
  Ban,
  BellRing,
  BookOpenCheck,
  Bot,
  CircleDollarSign,
  ClipboardCheck,
  Database,
  FileClock,
  Gauge,
  KeyRound,
  LineChart,
  LockKeyhole,
  Radar,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  TerminalSquare,
} from "lucide-react";
import { AnalystCopilot } from "@/components/analyst-copilot";
import { EquityChart } from "@/components/equity-chart";
import { LogoutButton } from "@/components/logout-button";
import { OperationalControls } from "@/components/operational-controls";
import { SignalsTable } from "@/components/signals-table";
import { requireBrowserSession } from "@/lib/auth/page-session";
import { buildDashboardState } from "@/lib/dashboard/build-dashboard";
import type {
  AuditEvent,
  BacktestReport,
  DataQualityReport,
  EdgeScoreboard,
  EdgeScoreboardStatus,
  LiveTradeAttempt,
  LiveTradingSettings,
  MarketRiskState,
  PaperPortfolio,
  RelativeValueSignal,
  RiskState,
  SystemTrustVerdict,
} from "@/lib/domain/types";

export const dynamic = "force-dynamic";

export default async function Home() {
  await requireBrowserSession();
  const state = await buildDashboardState();
  const topSignals = state.signals.slice(0, 4);

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <TerminalSquare size={22} aria-hidden="true" />
          <div>
            <p className="brand-name">Valor</p>
            <p className="brand-subtitle">Private RV + Risk Intel</p>
          </div>
        </div>
        <nav className="side-nav" aria-label="Dashboard sections">
          <a href="#overview">
            <Gauge size={16} aria-hidden="true" />
            Overview
          </a>
          <a href="#signals">
            <Radar size={16} aria-hidden="true" />
            Signals
          </a>
          <a href="#risk">
            <ShieldAlert size={16} aria-hidden="true" />
            Risk Intel
          </a>
          <a href="#alerts">
            <BellRing size={16} aria-hidden="true" />
            Alerts
          </a>
          <a href="#analyst">
            <Bot size={16} aria-hidden="true" />
            Analyst
          </a>
          <a href="#backtests">
            <LineChart size={16} aria-hidden="true" />
            Backtests
          </a>
          <a href="#paper">
            <CircleDollarSign size={16} aria-hidden="true" />
            Paper Trading
          </a>
          <a href="#scoreboard">
            <LineChart size={16} aria-hidden="true" />
            Edge Scoreboard
          </a>
          <a href="#evolver">
            <Activity size={16} aria-hidden="true" />
            Evolver Soak
          </a>
          <a href="#execution">
            <ClipboardCheck size={16} aria-hidden="true" />
            Dry Run
          </a>
          <a href="#runbook">
            <BookOpenCheck size={16} aria-hidden="true" />
            Runbook
          </a>
          <a href="#readiness">
            <ShieldCheck size={16} aria-hidden="true" />
            Readiness
          </a>
          <a href="#settings">
            <SlidersHorizontal size={16} aria-hidden="true" />
            Settings
          </a>
          <a href="#audit">
            <FileClock size={16} aria-hidden="true" />
            Audit
          </a>
        </nav>
        <div className="connector-box">
          <Database size={16} aria-hidden="true" />
          <div>
            <span>{state.connector.label}</span>
            <strong>{state.connector.needsApiKey ? "Key required" : "Local-ready"}</strong>
          </div>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Local-first research cockpit</p>
            <h1>Crypto Relative-Value + Risk Intelligence</h1>
          </div>
          <div className="topbar-actions">
            <RiskBadge risk={state.risk} />
            <LogoutButton />
          </div>
        </header>

        <section className="section-band" id="overview">
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
            <MetricTile
              label="Data Health"
              value={state.dataQuality.status}
              sub={
                state.dataQuality.blocksPaperTrading
                  ? "Paper entries blocked"
                  : `${state.dataQuality.marketCount} markets checked`
              }
              tone={dataQualityTone(state.dataQuality)}
            />
            <MetricTile
              label="System Trust"
              value={state.systemTrust.status}
              sub={
                state.systemTrust.blocksPaperTrading
                  ? "Paper entries blocked"
                  : state.systemTrust.blocksLiveTrading
                    ? "Live blocked"
                    : "Paper mode allowed"
              }
              tone={systemTrustTone(state.systemTrust)}
            />
          </div>

          <DataQualityPanel report={state.dataQuality} />
          <SystemTrustPanel verdict={state.systemTrust} />

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

        <section className="section-band" id="signals">
          <SectionHeader
            icon={<Radar size={18} aria-hidden="true" />}
            title="Signals"
            subtitle={`${state.signals.length} generated; ${state.signals.filter((signal) => signal.eligibleForPaperTrading).length} paper-eligible; live eligibility intentionally disabled`}
          />
          <SignalsTable signals={state.signals} />
        </section>

        <section className="section-band" id="risk">
          <SectionHeader
            icon={<ShieldAlert size={18} aria-hidden="true" />}
            title="Risk Intel"
            subtitle="Unified alert timeline with active trading restrictions"
          />
          <div className="risk-grid">
            {state.risk.activeAlerts.map((alert) => (
              <article key={alert.id} className={`alert-row severity-${alert.severity}`}>
                <div>
                  <span className="tag">{alert.category}</span>
                  <h3>{alert.title}</h3>
                  <p>{alert.explanation}</p>
                </div>
                <div className="alert-meta">
                  <strong>{alert.severity}</strong>
                  <span>{formatDateTime(alert.timestamp)}</span>
                  <span>{alert.source}</span>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="section-band" id="alerts">
          <SectionHeader
            icon={<BellRing size={18} aria-hidden="true" />}
            title="Alerts"
            subtitle="Severity-based routing with dedupe, quiet hours, SMS fallback, and dry-run delivery previews"
          />
          <AlertsPanel
            alerts={state.alertEvents}
            routingPreview={state.alertRoutingPreview}
            deliveries={state.alertDeliveries}
          />
        </section>

        <section className="section-band" id="analyst">
          <SectionHeader
            icon={<Bot size={18} aria-hidden="true" />}
            title="Analyst"
            subtitle="RAG, structured extraction, and explanation only; deterministic controls remain authoritative"
          />
          <AnalystCopilot
            configured={state.llmStatus.configured}
            model={state.llmStatus.model}
          />
        </section>

        <section className="section-band" id="backtests">
          <SectionHeader
            icon={<LineChart size={18} aria-hidden="true" />}
            title="Backtests"
            subtitle={`${state.backtest.strategyName} from ${state.backtest.startedAt} to ${state.backtest.endedAt}`}
          />
          <BacktestPanel backtest={state.backtest} />
        </section>

        <section className="section-band" id="paper">
          <SectionHeader
            icon={<CircleDollarSign size={18} aria-hidden="true" />}
            title="Paper Trading"
            subtitle="Simulated fills with signal attribution and risk-limit enforcement"
          />
          <PaperPanel paper={state.paper} />
        </section>

        <section className="section-band" id="scoreboard">
          <SectionHeader
            icon={<LineChart size={18} aria-hidden="true" />}
            title="Edge Scoreboard"
            subtitle={`${state.edgeScoreboard.rows.length} signal families; ${state.edgeScoreboard.totals.underperformingCount} underperforming`}
          />
          <EdgeScoreboardPanel scoreboard={state.edgeScoreboard} />
        </section>

        <section className="section-band" id="evolver">
          <SectionHeader
            icon={<Activity size={18} aria-hidden="true" />}
            title="Evolver Soak"
            subtitle={`${state.evolverEvidence.status}; ${state.evolverEvidence.evidenceDays} imported day${state.evolverEvidence.evidenceDays === 1 ? "" : "s"}; ${state.evolverEvidence.totalResearchCycles} research cycles`}
          />
          <EvolverEvidencePanel
            report={state.evolverEvidence}
            watchdog={state.evolverRecoveryWatchdog}
          />
        </section>

        <section className="section-band" id="execution">
          <SectionHeader
            icon={<ClipboardCheck size={18} aria-hidden="true" />}
            title="Dry-Run Execution"
            subtitle={`${state.liveTradeAttempts.length} recorded order intent${state.liveTradeAttempts.length === 1 ? "" : "s"}; reconciliation ${state.executionReconciliation.status}`}
          />
          <DryRunExecutionPanel
            attempts={state.liveTradeAttempts}
            reconciliation={state.executionReconciliation}
          />
        </section>

        <section className="section-band" id="runbook">
          <SectionHeader
            icon={<BookOpenCheck size={18} aria-hidden="true" />}
            title="Runbook"
            subtitle={`${state.operationalRunbook.stepCount} step${state.operationalRunbook.stepCount === 1 ? "" : "s"}; status ${state.operationalRunbook.status}`}
          />
          <OperationalRunbookPanel runbook={state.operationalRunbook} />
        </section>

        <section className="section-band" id="readiness">
          <SectionHeader
            icon={<ShieldCheck size={18} aria-hidden="true" />}
            title="Readiness"
            subtitle={`Tiny-live review status ${state.tinyLiveReadiness.status}; ${state.tinyLiveReadiness.blockerCount} blocker${state.tinyLiveReadiness.blockerCount === 1 ? "" : "s"}`}
          />
          <TinyLiveReadinessPanel report={state.tinyLiveReadiness} />
        </section>

        <section className="section-band" id="settings">
          <SectionHeader
            icon={<SlidersHorizontal size={18} aria-hidden="true" />}
            title="Settings"
            subtitle="API readiness, limits, live execution lockouts, and manual approval posture"
          />
          <SettingsPanel
            live={state.liveSettings}
            paper={state.paper}
            liveReasons={state.liveEvaluation ? state.liveEvaluation.reasons : []}
            connector={state.connector.label}
            llm={state.llmStatus}
            killSwitch={state.killSwitch}
            schedulerStatus={state.schedulerStatus}
          />
        </section>

        <section className="section-band" id="audit">
          <SectionHeader
            icon={<FileClock size={18} aria-hidden="true" />}
            title="Audit"
            subtitle="Important refreshes, generated signals, alerts, backtests, and trade events"
          />
          <AuditPanel events={state.auditEvents} />
          <ActionLog entries={state.actionLog} />
        </section>
      </div>
    </main>
  );
}

function SectionHeader(props: {
  icon: React.ReactNode;
  title: string;
  subtitle: string;
}) {
  return (
    <div className="section-header">
      <div className="section-title">
        {props.icon}
        <h2>{props.title}</h2>
      </div>
      <p>{props.subtitle}</p>
    </div>
  );
}

function RiskBadge({ risk }: { risk: MarketRiskState }) {
  const Icon = risk.state === "Green" ? ShieldCheck : AlertTriangle;
  return (
    <div className={`risk-badge risk-${risk.state.toLowerCase()}`}>
      <Icon size={17} aria-hidden="true" />
      <span>{risk.state}</span>
      <strong>{risk.activeAlerts.length} alerts</strong>
    </div>
  );
}

function DataQualityPanel({ report }: { report: DataQualityReport }) {
  const visibleIssues = report.issues.slice(0, 5);

  return (
    <div className={`panel data-quality-panel quality-${report.status}`}>
      <div>
        <h3>
          <Database size={15} aria-hidden="true" />
          System Health
        </h3>
        <p>{report.summary}</p>
      </div>
      <div className="quality-metrics">
        <LimitRow label="Mode" value={report.mode} />
        <LimitRow label="Age" value={formatDataAge(report.dataAgeMinutes)} />
        <LimitRow label="Markets" value={report.marketCount.toString()} />
        <LimitRow label="Issues" value={report.issueCount.toString()} />
        <LimitRow label="Critical" value={report.criticalIssueCount.toString()} />
        <LimitRow
          label="Paper Entries"
          value={report.blocksPaperTrading ? "Blocked" : "Allowed"}
        />
      </div>
      {visibleIssues.length > 0 ? (
        <div className="quality-issues">
          {visibleIssues.map((issue) => (
            <div
              key={`${issue.code}:${issue.scope}`}
              className={`quality-issue ${issue.severity}`}
            >
              <span>{issue.severity}</span>
              <strong>{issue.code}</strong>
              <p>{issue.message}</p>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function SystemTrustPanel({ verdict }: { verdict: SystemTrustVerdict }) {
  const visibleIssues = verdict.issues.slice(0, 6);

  return (
    <div className={`panel system-trust-panel trust-${verdict.status}`}>
      <div>
        <h3>
          <ShieldCheck size={15} aria-hidden="true" />
          System Trust
        </h3>
        <p>{verdict.summary}</p>
      </div>
      <div className="quality-metrics">
        <LimitRow label="Status" value={verdict.status} />
        <LimitRow
          label="Paper Entries"
          value={verdict.blocksPaperTrading ? "Blocked" : "Allowed"}
        />
        <LimitRow
          label="Live Trading"
          value={verdict.blocksLiveTrading ? "Blocked" : "Allowed"}
        />
        <LimitRow label="Issues" value={verdict.issueCount.toString()} />
        <LimitRow label="Critical" value={verdict.criticalIssueCount.toString()} />
        <LimitRow label="Checked" value={formatDateTime(verdict.generatedAt)} />
      </div>
      {visibleIssues.length > 0 ? (
        <div className="quality-issues">
          {visibleIssues.map((issue) => (
            <div
              key={`${issue.code}:${issue.scope}`}
              className={`quality-issue ${issue.severity}`}
            >
              <span>{issue.severity}</span>
              <strong>{issue.code}</strong>
              <p>{issue.message}</p>
              <span className={`pill ${systemTrustIssueClass(issue)}`}>
                {issue.blocksPaperTrading ? "paper blocked" : "paper allowed"}
              </span>
              <span className={`pill ${issue.blocksLiveTrading ? "blocked" : "ok"}`}>
                {issue.blocksLiveTrading ? "live blocked" : "live allowed"}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function MetricTile(props: {
  label: string;
  value: string;
  sub: string;
  tone: "good" | "bad" | "warn" | "info" | "neutral";
}) {
  return (
    <div className={`metric-tile tone-${props.tone}`}>
      <span>{props.label}</span>
      <strong>{props.value}</strong>
      <small>{props.sub}</small>
    </div>
  );
}

function OpportunityRow({ signal }: { signal: RelativeValueSignal }) {
  return (
    <div className="opportunity-row">
      <div>
        <span className="mono strong">{signal.assetPair}</span>
        <p>{signal.venue}</p>
      </div>
      <div className="score-stack">
        <strong>{signal.opportunityScore.toFixed(1)}</strong>
        <span>{signal.expectedEdgeBps.toFixed(1)} bps</span>
      </div>
    </div>
  );
}

function BacktestPanel({ backtest }: { backtest: BacktestReport }) {
  const metrics = [
    ["Ending Equity", money(backtest.endingEquityUsd)],
    ["Max Drawdown", `${backtest.maxDrawdownPct.toFixed(2)}%`],
    ["Sortino", backtest.sortino === null ? "∞ (no downside)" : backtest.sortino.toFixed(2)],
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

function PaperPanel({ paper }: { paper: PaperPortfolio }) {
  return (
    <div className="paper-grid">
      <div className="panel">
        <h3>Positions</h3>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Pair</th>
                <th>Venue</th>
                <th>Direction</th>
                <th>Notional</th>
                <th>Edge</th>
                <th>Funding</th>
                <th>Hold</th>
                <th>Mark PnL</th>
              </tr>
            </thead>
            <tbody>
              {paper.positions.map((position) => (
                <tr key={position.id}>
                  <td className="mono strong">{position.assetPair}</td>
                  <td>{position.venue}</td>
                  <td>{position.direction.replaceAll("_", " ")}</td>
                  <td>{money(position.notionalUsd)}</td>
                  <td>{(position.currentEdgeBps ?? position.entryEdgeBps).toFixed(1)} bps</td>
                  <td>{signedMoney(position.fundingAccruedUsd ?? 0)}</td>
                  <td>{formatHours(position.holdingHours ?? 0)}</td>
                  <td className={position.markPnlUsd >= 0 ? "good-text" : "bad-text"}>
                    {signedMoney(position.markPnlUsd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="panel">
        <h3>Risk Enforcement</h3>
        <div className="limit-list">
          <LimitRow label="Max Position" value={money(paper.riskLimits.maxPositionUsd)} />
          <LimitRow
            label="Portfolio Notional"
            value={`${(paper.riskLimits.maxPortfolioNotionalPct * 100).toFixed(0)}%`}
          />
          <LimitRow label="Max Signal Risk" value={paper.riskLimits.maxSignalRiskScore.toString()} />
          <LimitRow label="Min Liquidity" value={paper.riskLimits.minLiquidityScore.toString()} />
          <LimitRow
            label="Max Holding"
            value={formatHours(paper.riskLimits.maxHoldingHours ?? 72)}
          />
          <LimitRow label="Rejected Signals" value={paper.rejectedSignals.length.toString()} />
        </div>
      </div>
      <div className="panel full-width">
        <h3>Recent Ledger Events</h3>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Status</th>
                <th>Pair</th>
                <th>Notional</th>
                <th>Mark / Realized</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {paper.trades.slice(0, 10).map((trade) => (
                <tr key={trade.id}>
                  <td>{formatDateTime(trade.timestamp)}</td>
                  <td>
                    <span className="pill">{trade.status}</span>
                  </td>
                  <td className="mono strong">{trade.assetPair}</td>
                  <td>{money(trade.notionalUsd)}</td>
                  <td
                    className={
                      (trade.realizedPnlUsd ?? trade.markPnlUsd ?? 0) >= 0
                        ? "good-text"
                        : "bad-text"
                    }
                  >
                    {signedMoney(trade.realizedPnlUsd ?? trade.markPnlUsd ?? 0)}
                  </td>
                  <td>{trade.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function EdgeScoreboardPanel({ scoreboard }: { scoreboard: EdgeScoreboard }) {
  if (scoreboard.rows.length === 0) {
    return (
      <div className="panel">
        <p className="muted">No signal-family evidence is available yet.</p>
      </div>
    );
  }

  return (
    <div className="scoreboard-grid">
      <div className="panel">
        <h3>Totals</h3>
        <div className="mini-metrics">
          <div className="mini-metric">
            <span>Generated</span>
            <strong>{scoreboard.totals.generatedCount}</strong>
          </div>
          <div className="mini-metric">
            <span>Ledger Events</span>
            <strong>{scoreboard.totals.ledgerEventCount}</strong>
          </div>
          <div className="mini-metric">
            <span>Open Positions</span>
            <strong>{scoreboard.totals.openPositionCount}</strong>
          </div>
          <div className="mini-metric">
            <span>Total PnL</span>
            <strong
              className={
                scoreboard.totals.totalPnlUsd >= 0 ? "good-text" : "bad-text"
              }
            >
              {signedMoney(scoreboard.totals.totalPnlUsd)}
            </strong>
          </div>
        </div>
      </div>
      <div className="panel full-width">
        <h3>Signal Family Evidence</h3>
        <div className="table-wrap">
          <table className="data-table scoreboard-table">
            <thead>
              <tr>
                <th>Family</th>
                <th>Status</th>
                <th>Generated</th>
                <th>Ledger</th>
                <th>Open</th>
                <th>PnL</th>
                <th>Win</th>
                <th>Accept</th>
                <th>Edge</th>
                <th>Decay</th>
                <th>Recommendation</th>
              </tr>
            </thead>
            <tbody>
              {scoreboard.rows.map((row) => (
                <tr key={row.kind}>
                  <td className="mono strong">{labelSignalKind(row.kind)}</td>
                  <td>
                    <span className={`pill ${scoreboardStatusClass(row.status)}`}>
                      {labelScoreboardStatus(row.status)}
                    </span>
                  </td>
                  <td>{row.generatedCount}</td>
                  <td>
                    {row.ledgerEventCount}
                    <span className="muted block">
                      {row.filledCount} fill / {row.closedCount} close /{" "}
                      {row.rejectedCount} reject
                    </span>
                  </td>
                  <td>
                    {row.openPositionCount}
                    <span className="muted block">{money(row.activeNotionalUsd)}</span>
                  </td>
                  <td className={row.totalPnlUsd >= 0 ? "good-text" : "bad-text"}>
                    {signedMoney(row.totalPnlUsd)}
                    <span className="muted block">
                      {signedMoney(row.realizedPnlUsd)} realized
                    </span>
                  </td>
                  <td>{row.winRatePct.toFixed(1)}%</td>
                  <td>{row.acceptanceRatePct.toFixed(1)}%</td>
                  <td>{row.averageExpectedEdgeBps.toFixed(1)} bps</td>
                  <td>{row.averageEdgeDecayBps.toFixed(1)} bps</td>
                  <td>{row.recommendation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function EvolverEvidencePanel({
  report,
  watchdog,
}: {
  report: Awaited<ReturnType<typeof buildDashboardState>>["evolverEvidence"];
  watchdog: Awaited<
    ReturnType<typeof buildDashboardState>
  >["evolverRecoveryWatchdog"];
}) {
  const shadowPnl =
    report.shadow?.reportedPnlUsd ??
    report.shadow?.approximatedClosedPnlUsd ??
    0;
  const calibration = report.calibration;
  const recoveryPlan = report.recoveryPlan;

  return (
    <div className="evolver-grid">
      <div className="panel">
        <h3>
          <Activity size={15} aria-hidden="true" />
          Import Status
        </h3>
        <p className="risk-explanation">{report.summary}</p>
        <div className="mini-metrics">
          <div className="mini-metric">
            <span>Status</span>
            <strong className={evolverEvidenceStatusClass(report.status)}>
              {report.status.replaceAll("_", " ")}
            </strong>
          </div>
          <div className="mini-metric">
            <span>Window</span>
            <strong>{report.evidenceDays}d</strong>
          </div>
          <div className="mini-metric">
            <span>Cycles</span>
            <strong>{report.totalResearchCycles}</strong>
          </div>
          <div className="mini-metric">
            <span>Surfaced</span>
            <strong>{report.surfacedCandidateCount}</strong>
          </div>
        </div>
      </div>
      <div className="panel">
        <h3>Shadow Book</h3>
        {report.shadow ? (
          <div className="status-list">
            <LimitRow label="Closed" value={report.shadow.closedTradeCount.toString()} />
            <LimitRow label="Open" value={report.shadow.openPositionCount.toString()} />
            <LimitRow label="Equity" value={money(report.shadow.equityUsd ?? report.shadow.startingEquityUsd)} />
            <LimitRow label="PnL" value={signedMoney(shadowPnl)} />
            <LimitRow label="Win Rate" value={`${report.shadow.winRatePct.toFixed(1)}%`} />
            <LimitRow
              label="Convergence"
              value={`${report.shadow.convergenceRatePct.toFixed(1)}%`}
            />
          </div>
        ) : (
          <p className="muted">No imported shadow book.</p>
        )}
      </div>
      <div className="panel">
        <h3>Calibration</h3>
        {calibration ? (
          <div className="status-list">
            <LimitRow label="Status" value={calibration.status} />
            <LimitRow label="Sample" value={calibration.sampleSize.toString()} />
            <LimitRow
              label="Stated"
              value={
                calibration.statedConfidenceMean === undefined
                  ? "n/a"
                  : `${(calibration.statedConfidenceMean * 100).toFixed(1)}%`
              }
            />
            <LimitRow
              label="Realized"
              value={
                calibration.realizedConvergenceRate === undefined
                  ? "n/a"
                  : `${(calibration.realizedConvergenceRate * 100).toFixed(1)}%`
              }
            />
            <LimitRow
              label="Scale"
              value={
                calibration.convergenceScale === undefined
                  ? "n/a"
                  : calibration.convergenceScale.toFixed(3)
              }
            />
          </div>
        ) : (
          <p className="muted">No imported calibration file.</p>
        )}
      </div>
      <div className="panel full-width">
        <h3>Path To Unblock</h3>
        <p className="risk-explanation">{recoveryPlan.summary}</p>
        <div className="mini-metrics">
          <div className="mini-metric">
            <span>PnL Recovery</span>
            <strong>{money(recoveryPlan.requiredPnlRecoveryUsd)}</strong>
          </div>
          <div className="mini-metric">
            <span>Days Gap</span>
            <strong>
              {recoveryPlan.additionalEvidenceDays}/{recoveryPlan.minimumEvidenceDays}
            </strong>
          </div>
          <div className="mini-metric">
            <span>Closes Gap</span>
            <strong>
              {recoveryPlan.additionalClosedTrades}/{recoveryPlan.minimumClosedTrades}
            </strong>
          </div>
          <div className="mini-metric">
            <span>Win Gap</span>
            <strong>{percentPointGap(recoveryPlan.winRateGapPct)}</strong>
          </div>
          <div className="mini-metric">
            <span>Convergence Gap</span>
            <strong>{percentPointGap(recoveryPlan.convergenceRateGapPct)}</strong>
          </div>
          <div className="mini-metric">
            <span>Confidence Haircut</span>
            <strong>
              {recoveryPlan.confidenceHaircutPct === undefined
                ? "n/a"
                : `${recoveryPlan.confidenceHaircutPct.toFixed(1)}%`}
            </strong>
          </div>
        </div>
        <div className="status-list">
          <LimitRow
            label="Bench"
            value={
              recoveryPlan.benchCandidates.length
                ? recoveryPlan.benchCandidates.join(", ")
                : "none"
            }
          />
        </div>
        {recoveryPlan.actions.length === 0 ? (
          <p className="muted">No imported-evidence recovery actions.</p>
        ) : (
          <div className="runbook-steps">
            {recoveryPlan.actions.slice(0, 6).map((action) => (
              <article
                key={action.code}
                className={`runbook-step runbook-step-${action.severity}`}
              >
                <div className="runbook-step-header">
                  <div>
                    <span className="tag">{action.severity}</span>
                    <h3>{action.title}</h3>
                  </div>
                </div>
                <div className="runbook-step-body">
                  <RunbookField label="Current" value={action.current} />
                  <RunbookField label="Target" value={action.target} />
                  <RunbookField label="Gap" value={action.gap} />
                  <RunbookField label="Why" value={action.rationale} />
                </div>
              </article>
            ))}
          </div>
        )}
      </div>
      <div className="panel full-width">
        <h3>Recovery Watchdog</h3>
        <p className="risk-explanation">{watchdog.summary}</p>
        <div className="mini-metrics">
          <div className="mini-metric">
            <span>Posture</span>
            <strong className={watchdogPostureClass(watchdog.posture)}>
              {watchdog.posture}
            </strong>
          </div>
          <div className="mini-metric">
            <span>Snapshots</span>
            <strong>{watchdog.snapshotCount}</strong>
          </div>
          <div className="mini-metric">
            <span>Gap Score</span>
            <strong>{watchdog.current?.gapScore.toFixed(1) ?? "n/a"}</strong>
          </div>
          <div className="mini-metric">
            <span>Prior Score</span>
            <strong>{watchdog.previous?.gapScore.toFixed(1) ?? "n/a"}</strong>
          </div>
          <div className="mini-metric">
            <span>Bench Matches</span>
            <strong>{watchdog.benchGuard.matchingSignalKinds.length}</strong>
          </div>
          <div className="mini-metric">
            <span>Bench Guard</span>
            <strong>{watchdog.benchGuard.active ? "active" : "inactive"}</strong>
          </div>
        </div>
        <div className="status-list">
          <LimitRow
            label="Matching Signal Kinds"
            value={
              watchdog.benchGuard.matchingSignalKinds.length
                ? watchdog.benchGuard.matchingSignalKinds.join(", ")
                : "none"
            }
          />
          <LimitRow
            label="Last Snapshot"
            value={
              watchdog.current?.generatedAt
                ? formatDateTime(watchdog.current.generatedAt)
                : "n/a"
            }
          />
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Metric</th>
                <th>Current</th>
                <th>Delta</th>
                <th>Direction</th>
              </tr>
            </thead>
            <tbody>
              {watchdog.metrics.slice(0, 7).map((metric) => (
                <tr key={metric.key}>
                  <td>{metric.label}</td>
                  <td>{formatWatchdogMetric(metric.current, metric.unit)}</td>
                  <td>{formatWatchdogDelta(metric.delta, metric.unit)}</td>
                  <td className={metricDirectionClass(metric.direction)}>
                    {metric.direction}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="panel full-width">
        <h3>Research Loops</h3>
        {report.researchLoops.length === 0 ? (
          <p className="muted">No imported research loops.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Loop</th>
                  <th>Cycles</th>
                  <th>Surfaced</th>
                  <th>Last</th>
                  <th>Families</th>
                </tr>
              </thead>
              <tbody>
                {report.researchLoops.map((loop) => (
                  <tr key={loop.name}>
                    <td className="mono strong">{loop.name}</td>
                    <td>{loop.cycleCount}</td>
                    <td>{loop.surfacedCount}</td>
                    <td>{loop.lastSummary ?? "n/a"}</td>
                    <td>
                      {loop.familyCounts
                        .slice(0, 4)
                        .map((family) => `${family.family}:${family.count}`)
                        .join(" / ") || "n/a"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <div className="panel full-width">
        <h3>Bridge Issues</h3>
        {report.issues.length === 0 ? (
          <p className="muted">No imported-evidence issues.</p>
        ) : (
          <div className="runbook-steps">
            {report.issues.slice(0, 8).map((issue) => (
              <article
                key={issue.code}
                className={`runbook-step runbook-step-${issue.severity}`}
              >
                <div className="runbook-step-header">
                  <div>
                    <span className="tag">{issue.severity}</span>
                    <h3>{issue.code.replaceAll("_", " ")}</h3>
                  </div>
                </div>
                <div className="runbook-step-body">
                  <RunbookField label="Issue" value={issue.message} />
                  <RunbookField label="Evidence" value={issue.evidence} />
                </div>
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function DryRunExecutionPanel({
  attempts,
  reconciliation,
}: {
  attempts: LiveTradeAttempt[];
  reconciliation: Awaited<ReturnType<typeof buildDashboardState>>["executionReconciliation"];
}) {
  if (attempts.length === 0) {
    return (
      <div className="execution-grid">
        <ExecutionReconciliationPanel reconciliation={reconciliation} />
        <div className="panel">
          <p className="muted">No dry-run execution attempts recorded yet.</p>
        </div>
      </div>
    );
  }

  const latest = attempts[0];

  return (
    <div className="execution-grid">
      <div className="panel">
        <h3>
          <ClipboardCheck size={15} aria-hidden="true" />
          Latest Intent
        </h3>
        <div className="mini-metrics">
          <div className="mini-metric">
            <span>Status</span>
            <strong className={latest.allowed ? "good-text" : "bad-text"}>
              {labelExecutionStatus(latest.status)}
            </strong>
          </div>
          <div className="mini-metric">
            <span>Notional</span>
            <strong>{money(latest.requestedNotionalUsd)}</strong>
          </div>
          <div className="mini-metric">
            <span>Fees</span>
            <strong>{money(latest.preview.estimatedFeesUsd)}</strong>
          </div>
          <div className="mini-metric">
            <span>Slippage</span>
            <strong>{money(latest.preview.estimatedSlippageUsd)}</strong>
          </div>
        </div>
      </div>
      <div className="panel">
        <h3>Executor Boundary</h3>
        <div className="status-list">
          <LimitRow label="Mode" value={latest.mode} />
          <StatusBoolean label="Dry Run" value={latest.dryRun} />
          <StatusBoolean label="Allowed" value={latest.allowed} />
          <LimitRow label="Fills" value={latest.fills.length.toString()} />
          <LimitRow label="Audit" value={latest.evaluationAuditLabel} />
        </div>
      </div>
      <ExecutionReconciliationPanel reconciliation={reconciliation} />
      <div className="panel full-width">
        <h3>Order Intent Ledger</h3>
        <div className="table-wrap">
          <table className="data-table execution-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Status</th>
                <th>Signal</th>
                <th>Venue</th>
                <th>Notional</th>
                <th>Estimated Cost</th>
                <th>Guard Reasons</th>
              </tr>
            </thead>
            <tbody>
              {attempts.slice(0, 12).map((attempt) => (
                <tr key={attempt.id}>
                  <td>{formatDateTime(attempt.createdAt)}</td>
                  <td>
                    <span className={`pill ${executionStatusClass(attempt)}`}>
                      {labelExecutionStatus(attempt.status)}
                    </span>
                  </td>
                  <td>
                    <span className="mono strong">{attempt.assetPair}</span>
                    <span className="muted block">{labelSignalKind(attempt.signalKind)}</span>
                  </td>
                  <td>{attempt.venue}</td>
                  <td>{money(attempt.requestedNotionalUsd)}</td>
                  <td>
                    {money(attempt.preview.estimatedTotalCostUsd)}
                    <span className="muted block">
                      {money(attempt.preview.estimatedFeesUsd)} fees /{" "}
                      {money(attempt.preview.estimatedSlippageUsd)} slip
                    </span>
                  </td>
                  <td>
                    {attempt.reasons.length === 0 ? (
                      <span className="good-text">Guardrails passed</span>
                    ) : (
                      attempt.reasons.slice(0, 4).join(" ")
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function ExecutionReconciliationPanel({
  reconciliation,
}: {
  reconciliation: Awaited<
    ReturnType<typeof buildDashboardState>
  >["executionReconciliation"];
}) {
  const visibleIssues = reconciliation.issues.slice(0, 4);

  return (
    <div className="panel">
      <h3>
        <ShieldCheck size={15} aria-hidden="true" />
        Reconciliation
      </h3>
      <div className="mini-metrics">
        <div className="mini-metric">
          <span>Status</span>
          <strong className={reconciliationStatusClass(reconciliation.status)}>
            {reconciliation.status}
          </strong>
        </div>
        <div className="mini-metric">
          <span>Attempts</span>
          <strong>{reconciliation.attemptCount}</strong>
        </div>
        <div className="mini-metric">
          <span>Fills</span>
          <strong>{reconciliation.dryRunFillCount}</strong>
        </div>
        <div className="mini-metric">
          <span>Issues</span>
          <strong>{reconciliation.issueCount}</strong>
        </div>
      </div>
      {visibleIssues.length > 0 ? (
        <div className="blocked-reasons execution-issues">
          {visibleIssues.map((issue) => (
            <span
              key={`${issue.code}:${issue.scope}`}
              className={`execution-issue-${issue.severity}`}
            >
              {issue.code}: {issue.message}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function OperationalRunbookPanel({
  runbook,
}: {
  runbook: Awaited<ReturnType<typeof buildDashboardState>>["operationalRunbook"];
}) {
  return (
    <div className="runbook-grid">
      <div className="panel">
        <h3>
          <BookOpenCheck size={15} aria-hidden="true" />
          Operator State
        </h3>
        <p className="risk-explanation">{runbook.summary}</p>
        <div className="mini-metrics">
          <div className="mini-metric">
            <span>Status</span>
            <strong className={runbookStatusClass(runbook.status)}>
              {runbook.status}
            </strong>
          </div>
          <div className="mini-metric">
            <span>Actions</span>
            <strong>{runbook.actionRequiredCount}</strong>
          </div>
          <div className="mini-metric">
            <span>Blocked</span>
            <strong>{runbook.blockedCount}</strong>
          </div>
          <div className="mini-metric">
            <span>Critical</span>
            <strong>{runbook.criticalStepCount}</strong>
          </div>
        </div>
      </div>
      <div className="panel full-width">
        <h3>Current Procedures</h3>
        <div className="runbook-steps">
          {runbook.steps.slice(0, 10).map((step) => (
            <article
              key={step.id}
              className={`runbook-step runbook-step-${step.severity}`}
            >
              <div className="runbook-step-header">
                <div>
                  <span className="tag">{step.area.replaceAll("_", " ")}</span>
                  <h3>{step.title}</h3>
                </div>
                <span className={`pill ${runbookStepClass(step.status)}`}>
                  {step.status.replaceAll("_", " ")}
                </span>
              </div>
              <div className="runbook-step-body">
                <RunbookField label="Trigger" value={step.trigger} />
                <RunbookField label="Action" value={step.action} />
                <RunbookField label="Verify" value={step.verification} />
                <RunbookField label="Evidence" value={step.evidence} />
              </div>
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}

function TinyLiveReadinessPanel({
  report,
}: {
  report: Awaited<ReturnType<typeof buildDashboardState>>["tinyLiveReadiness"];
}) {
  return (
    <div className="readiness-grid">
      <div className="panel">
        <h3>
          <ShieldCheck size={15} aria-hidden="true" />
          Decision Memo
        </h3>
        <p className="risk-explanation">{report.summary}</p>
        <div className="mini-metrics">
          <div className="mini-metric">
            <span>Status</span>
            <strong className={readinessStatusClass(report.status)}>
              {report.status.replaceAll("_", " ")}
            </strong>
          </div>
          <div className="mini-metric">
            <span>Evidence</span>
            <strong>{report.evidenceDays}d</strong>
          </div>
          <div className="mini-metric">
            <span>Closed</span>
            <strong>{report.closedTradeCount}</strong>
          </div>
          <div className="mini-metric">
            <span>Critical</span>
            <strong>{report.criticalBlockerCount}</strong>
          </div>
        </div>
      </div>
      <div className="panel">
        <h3>Candidate Family</h3>
        {report.candidate ? (
          <div className="status-list">
            <LimitRow label="Family" value={labelSignalKind(report.candidate.kind)} />
            <LimitRow label="Status" value={report.candidate.status} />
            <LimitRow
              label="PnL"
              value={signedMoney(report.candidate.totalPnlUsd)}
            />
            <LimitRow
              label="Closed / Win"
              value={`${report.candidate.closedCount} / ${report.candidate.winRatePct.toFixed(1)}%`}
            />
            <LimitRow
              label="Expected Edge"
              value={`${report.candidate.averageExpectedEdgeBps.toFixed(1)} bps`}
            />
          </div>
        ) : (
          <p className="muted">No signal family has earned candidate review.</p>
        )}
      </div>
      <div className="panel full-width">
        <h3>Go / No-Go Memo</h3>
        <div className="runbook-step-body readiness-memo">
          <RunbookField label="Conclusion" value={report.memo.conclusion} />
          <RunbookField label="Evidence Window" value={report.memo.evidenceWindow} />
          <RunbookField
            label="Required Next Evidence"
            value={report.memo.requiredNextEvidence}
          />
          <RunbookField
            label="Minimums"
            value={`${report.minimums.evidenceDays}d evidence, ${report.minimums.closedTradesPerFamily} closed trades/family, ${report.minimums.winRatePct}% win rate, positive PnL`}
          />
        </div>
      </div>
      <div className="panel full-width">
        <h3>Readiness Blockers</h3>
        {report.blockers.length === 0 ? (
          <p className="muted">No blockers remain. Human review is still required.</p>
        ) : (
          <div className="runbook-steps">
            {report.blockers.slice(0, 10).map((blocker) => (
              <article
                key={blocker.code}
                className={`runbook-step runbook-step-${blocker.severity}`}
              >
                <div className="runbook-step-header">
                  <div>
                    <span className="tag">{blocker.severity}</span>
                    <h3>{blocker.code.replaceAll("_", " ")}</h3>
                  </div>
                </div>
                <div className="runbook-step-body">
                  <RunbookField label="Blocker" value={blocker.message} />
                  <RunbookField label="Evidence" value={blocker.evidence} />
                </div>
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function RunbookField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <p>{value}</p>
    </div>
  );
}

function AlertsPanel(props: {
  alerts: Awaited<ReturnType<typeof buildDashboardState>>["alertEvents"];
  routingPreview: Awaited<ReturnType<typeof buildDashboardState>>["alertRoutingPreview"];
  deliveries: Awaited<ReturnType<typeof buildDashboardState>>["alertDeliveries"];
}) {
  return (
    <div className="alerts-grid">
      <div className="panel">
        <h3>Active Alert Events</h3>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Severity</th>
                <th>Title</th>
                <th>Source</th>
                <th>Impact</th>
              </tr>
            </thead>
            <tbody>
              {props.alerts.slice(0, 10).map((alert) => (
                <tr key={alert.id}>
                  <td>
                    <span className={`pill severity-pill-${alert.severity.toLowerCase()}`}>
                      {alert.severity}
                    </span>
                  </td>
                  <td>
                    <strong>{alert.title}</strong>
                    <span className="muted block">{alert.message}</span>
                  </td>
                  <td>{alert.source}</td>
                  <td>{alert.tradingImpact}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="panel">
        <h3>Routing Preview</h3>
        <div className="delivery-list">
          {props.routingPreview.map((result) => (
            <div key={result.alert.id} className="delivery-row">
              <div>
                <strong>{result.alert.severity}</strong>
                <span>{result.alert.title}</span>
              </div>
              <p>
                {result.suppressed
                  ? result.reasons.join(", ")
                  : result.deliveries
                      .map((delivery) => `${delivery.channel}:${delivery.destination}`)
                      .join(" / ")}
              </p>
            </div>
          ))}
        </div>
      </div>
      <div className="panel full-width">
        <h3>Delivery Log</h3>
        <DeliveryLog deliveries={props.deliveries} />
      </div>
    </div>
  );
}

function SettingsPanel(props: {
  live: LiveTradingSettings;
  paper: PaperPortfolio;
  liveReasons: string[];
  connector: string;
  llm: Awaited<ReturnType<typeof buildDashboardState>>["llmStatus"];
  killSwitch: Awaited<ReturnType<typeof buildDashboardState>>["killSwitch"];
  schedulerStatus: Awaited<ReturnType<typeof buildDashboardState>>["schedulerStatus"];
}) {
  const apiStatuses = [
    ["Exchange OHLCV", props.connector],
    ["Stablecoin Pegs", "CoinGecko live path with fixture fallback"],
    ["News / RSS", "Advisory model active; RSS connector not yet wired"],
    ["CSV Import", "Parser available for manual snapshots"],
  ];

  return (
    <div className="settings-grid">
      <div className="panel">
        <h3>
          <KeyRound size={15} aria-hidden="true" />
          Data Sources
        </h3>
        <div className="status-list">
          {apiStatuses.map(([label, value]) => (
            <div key={label} className="status-row">
              <span>{label}</span>
              <strong>{value}</strong>
            </div>
          ))}
        </div>
      </div>
      <div className="panel">
        <h3>
          <LockKeyhole size={15} aria-hidden="true" />
          Live Guardrails
        </h3>
        <div className="status-list">
          <StatusBoolean label="Enabled" value={props.live.enabled} safeWhenFalse />
          <StatusBoolean label="Dry Run" value={props.live.dryRun} />
          <StatusBoolean label="Manual Approval" value={props.live.manualConfirmationRequired} />
          <StatusBoolean label="Kill Switch Active" value={props.live.killSwitchActive} safeWhenFalse={false} />
          <LimitRow label="Max Trade" value={money(props.live.maxTradeUsd)} />
          <LimitRow label="Daily Loss Limit" value={money(props.live.dailyLossLimitUsd)} />
          <LimitRow label="Max Leverage" value={`${props.live.maxLeverage}x`} />
        </div>
      </div>
      <div className="panel full-width">
        <h3>
          <Bot size={15} aria-hidden="true" />
          LLM API Plug
        </h3>
        <div className="status-list">
          <LimitRow label="Env Flag" value="LLM_API_ENABLED" />
          <LimitRow label="Configured" value={props.llm.configured ? "Yes" : "No"} />
          <LimitRow label="Model" value={props.llm.model} />
          <LimitRow label="Base URL" value={props.llm.baseUrl} />
          <LimitRow label="Authority" value="RAG/extraction/explanation only" />
        </div>
      </div>
      <div className="panel full-width">
        <h3>
          <LockKeyhole size={15} aria-hidden="true" />
          Persisted Kill Switch
        </h3>
        <div className="status-list">
          <StatusBoolean
            label="Active"
            value={props.killSwitch?.active ?? false}
            safeWhenFalse
          />
          <LimitRow label="Reason" value={props.killSwitch?.reason ?? "Not active"} />
          <LimitRow
            label="Activated By"
            value={props.killSwitch?.activatedBy ?? "n/a"}
          />
        </div>
      </div>
      <div className="panel full-width">
        <h3>
          <BookOpenCheck size={15} aria-hidden="true" />
          Live Attempt Evaluation
        </h3>
        <div className="blocked-reasons">
          {props.liveReasons.map((reason) => (
            <span key={reason}>{reason}</span>
          ))}
        </div>
      </div>
      <div className="panel full-width">
        <h3>
          <FileClock size={15} aria-hidden="true" />
          Scheduler
        </h3>
        <div className="status-list">
          <StatusBoolean
            label="Running"
            value={props.schedulerStatus.running}
            safeWhenFalse
          />
          <LimitRow
            label="Cycles"
            value={props.schedulerStatus.cycleCount.toString()}
          />
          <LimitRow
            label="Consecutive Errors"
            value={props.schedulerStatus.consecutiveErrors.toString()}
          />
          <LimitRow
            label="Last Success"
            value={
              props.schedulerStatus.lastSuccessAt
                ? formatDateTime(props.schedulerStatus.lastSuccessAt)
                : "n/a"
            }
          />
          <LimitRow
            label="Last Duration"
            value={
              props.schedulerStatus.lastDurationMs !== undefined
                ? `${props.schedulerStatus.lastDurationMs}ms`
                : "n/a"
            }
          />
          <LimitRow
            label="Data Quality"
            value={props.schedulerStatus.lastDataQualityStatus ?? "n/a"}
          />
          <LimitRow
            label="Last Heartbeat"
            value={
              props.schedulerStatus.lastHeartbeatAt
                ? formatDateTime(props.schedulerStatus.lastHeartbeatAt)
                : "n/a"
            }
          />
          <LimitRow
            label="Last Skipped"
            value={
              props.schedulerStatus.lastSkippedAt
                ? formatDateTime(props.schedulerStatus.lastSkippedAt)
                : "n/a"
            }
          />
          <LimitRow
            label="Stale Recoveries"
            value={(props.schedulerStatus.staleRunCount ?? 0).toString()}
          />
          <LimitRow
            label="Active Run"
            value={props.schedulerStatus.activeRunId ?? "n/a"}
          />
          <LimitRow label="Last Message" value={props.schedulerStatus.lastMessage} />
        </div>
      </div>
    </div>
  );
}

function AuditPanel({ events }: { events: AuditEvent[] }) {
  return (
    <div className="audit-list">
      {events.slice(0, 14).map((event) => (
        <article key={event.id} className="audit-row">
          <div className="audit-icon">
            <Activity size={15} aria-hidden="true" />
          </div>
          <div>
            <span className="mono">{event.action}</span>
            <p>{event.summary}</p>
          </div>
          <time>{formatDateTime(event.timestamp)}</time>
        </article>
      ))}
    </div>
  );
}

function DeliveryLog({
  deliveries,
}: {
  deliveries: Awaited<ReturnType<typeof buildDashboardState>>["alertDeliveries"];
}) {
  if (deliveries.length === 0) {
    return <p className="muted">No delivery attempts recorded yet.</p>;
  }

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Channel</th>
            <th>Status</th>
            <th>Destination</th>
            <th>Alert</th>
          </tr>
        </thead>
        <tbody>
          {deliveries.slice(0, 12).map((delivery) => (
            <tr key={delivery.id}>
              <td>{formatDateTime(delivery.attemptedAt)}</td>
              <td>{delivery.channel}</td>
              <td>
                <span className="pill">{delivery.status}</span>
              </td>
              <td>{delivery.destination}</td>
              <td>{delivery.alertId}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ActionLog({
  entries,
}: {
  entries: Awaited<ReturnType<typeof buildDashboardState>>["actionLog"];
}) {
  if (entries.length === 0) return null;

  return (
    <div className="panel action-log-panel">
      <h3>Action Log</h3>
      <div className="delivery-list">
        {entries.slice(0, 10).map((entry) => (
          <div key={entry.id} className="delivery-row">
            <div>
              <strong>{entry.action}</strong>
              <span>{entry.status}</span>
            </div>
            <p>{entry.message}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function LimitRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="status-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function StatusBoolean(props: {
  label: string;
  value: boolean;
  safeWhenFalse?: boolean;
}) {
  const good = props.safeWhenFalse ? !props.value : props.value;
  return (
    <div className="status-row">
      <span>{props.label}</span>
      <strong className={good ? "good-text" : "bad-text"}>{props.value ? "Yes" : "No"}</strong>
    </div>
  );
}

function money(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

function signedMoney(value: number): string {
  const formatted = money(Math.abs(value));
  return `${value >= 0 ? "+" : "-"}${formatted}`;
}

function percentPointGap(value: number): string {
  return value > 0 ? `+${value.toFixed(1)} pp` : "0.0 pp";
}

function formatWatchdogMetric(
  value: number,
  unit: Awaited<
    ReturnType<typeof buildDashboardState>
  >["evolverRecoveryWatchdog"]["metrics"][number]["unit"],
): string {
  if (unit === "usd") return money(value);
  if (unit === "days") return `${value.toFixed(0)}d`;
  if (unit === "trades") return value.toFixed(0);
  if (unit === "pp") return `${value.toFixed(1)} pp`;
  if (unit === "pct") return `${value.toFixed(1)}%`;
  return value.toFixed(1);
}

function formatWatchdogDelta(
  value: number | undefined,
  unit: Awaited<
    ReturnType<typeof buildDashboardState>
  >["evolverRecoveryWatchdog"]["metrics"][number]["unit"],
): string {
  if (value === undefined) return "n/a";
  const sign = value > 0 ? "+" : "";
  if (unit === "usd") return `${sign}${money(value)}`;
  if (unit === "days") return `${sign}${value.toFixed(0)}d`;
  if (unit === "trades") return `${sign}${value.toFixed(0)}`;
  if (unit === "pp") return `${sign}${value.toFixed(1)} pp`;
  if (unit === "pct") return `${sign}${value.toFixed(1)}%`;
  return `${sign}${value.toFixed(1)}`;
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDataAge(value: number): string {
  return value >= 0 ? `${value.toFixed(1)}m` : "n/a";
}

function formatHours(value: number): string {
  if (value < 1) return `${Math.round(value * 60)}m`;
  return `${value.toFixed(1)}h`;
}

function labelSignalKind(kind: RelativeValueSignal["kind"]): string {
  return kind.replaceAll("_", " ");
}

function labelScoreboardStatus(status: EdgeScoreboardStatus): string {
  return status.replaceAll("_", " ");
}

function scoreboardStatusClass(status: EdgeScoreboardStatus): string {
  if (status === "proving") return "ok";
  if (status === "underperforming") return "blocked";
  if (status === "watch") return "severity-pill-watch";
  return "muted-pill";
}

function labelExecutionStatus(status: LiveTradeAttempt["status"]): string {
  return status.replaceAll("_", " ");
}

function executionStatusClass(attempt: LiveTradeAttempt): string {
  if (attempt.status === "dry_run_recorded") return "ok";
  if (attempt.status === "blocked") return "blocked";
  if (attempt.status === "cancelled") return "severity-pill-watch";
  return "muted-pill";
}

function reconciliationStatusClass(
  status: Awaited<
    ReturnType<typeof buildDashboardState>
  >["executionReconciliation"]["status"],
): string {
  if (status === "clean") return "good-text";
  if (status === "attention") return "severity-pill-watch";
  return "bad-text";
}

function runbookStatusClass(
  status: Awaited<ReturnType<typeof buildDashboardState>>["operationalRunbook"]["status"],
): string {
  if (status === "ready") return "good-text";
  if (status === "attention") return "severity-pill-watch";
  return "bad-text";
}

function runbookStepClass(
  status: Awaited<
    ReturnType<typeof buildDashboardState>
  >["operationalRunbook"]["steps"][number]["status"],
): string {
  if (status === "ready") return "ok";
  if (status === "action_required") return "severity-pill-watch";
  return "blocked";
}

function readinessStatusClass(
  status: Awaited<
    ReturnType<typeof buildDashboardState>
  >["tinyLiveReadiness"]["status"],
): string {
  if (status === "candidate_review") return "good-text";
  if (status === "watchlist") return "severity-pill-watch";
  return "bad-text";
}

function evolverEvidenceStatusClass(
  status: Awaited<
    ReturnType<typeof buildDashboardState>
  >["evolverEvidence"]["status"],
): string {
  if (status === "healthy") return "good-text";
  if (status === "watch") return "severity-pill-watch";
  if (status === "blocked") return "bad-text";
  return "muted";
}

function watchdogPostureClass(
  posture: Awaited<
    ReturnType<typeof buildDashboardState>
  >["evolverRecoveryWatchdog"]["posture"],
): string {
  if (posture === "clear" || posture === "improving") return "good-text";
  if (posture === "flat" || posture === "new") return "severity-pill-watch";
  if (posture === "deteriorating") return "bad-text";
  return "muted";
}

function metricDirectionClass(
  direction: Awaited<
    ReturnType<typeof buildDashboardState>
  >["evolverRecoveryWatchdog"]["metrics"][number]["direction"],
): string {
  if (direction === "improved") return "good-text";
  if (direction === "deteriorated") return "bad-text";
  if (direction === "flat") return "severity-pill-watch";
  return "muted";
}

function riskTone(state: RiskState): "good" | "bad" | "warn" | "info" | "neutral" {
  if (state === "Green") return "good";
  if (state === "Yellow") return "warn";
  if (state === "Red" || state === "Black") return "bad";
  return "neutral";
}

function dataQualityTone(
  report: DataQualityReport,
): "good" | "bad" | "warn" | "info" | "neutral" {
  if (report.status === "blocked") return "bad";
  if (report.status === "degraded") return "warn";
  return report.mode === "sample" ? "info" : "good";
}

function systemTrustTone(
  verdict: SystemTrustVerdict,
): "good" | "bad" | "warn" | "info" | "neutral" {
  if (verdict.status === "blocked") return "bad";
  if (verdict.status === "caution") return "warn";
  return "good";
}

function systemTrustIssueClass(
  issue: SystemTrustVerdict["issues"][number],
): string {
  if (issue.blocksPaperTrading) return "blocked";
  if (issue.severity === "warning") return "severity-pill-watch";
  return "ok";
}
