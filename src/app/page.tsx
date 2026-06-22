import {
  Activity,
  AlertTriangle,
  Ban,
  BellRing,
  BookOpenCheck,
  Bot,
  CircleDollarSign,
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
import { OperationalControls } from "@/components/operational-controls";
import { SignalsTable } from "@/components/signals-table";
import { buildDashboardState } from "@/lib/dashboard/build-dashboard";
import type {
  AuditEvent,
  BacktestReport,
  LiveTradingSettings,
  MarketRiskState,
  PaperPortfolio,
  RelativeValueSignal,
  RiskState,
} from "@/lib/domain/types";

export const dynamic = "force-dynamic";

export default async function Home() {
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
          <RiskBadge risk={state.risk} />
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
          <LimitRow label="Rejected Signals" value={paper.rejectedSignals.length.toString()} />
        </div>
      </div>
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

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function riskTone(state: RiskState): "good" | "bad" | "warn" | "info" | "neutral" {
  if (state === "Green") return "good";
  if (state === "Yellow") return "warn";
  if (state === "Red" || state === "Black") return "bad";
  return "neutral";
}
