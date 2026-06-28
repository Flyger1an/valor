import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, isAbsolute, join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import type { AlertDelivery, AlertEvent } from "@/lib/alerts/types";
import type {
  AuditEvent,
  DataQualityReport,
  LiveTradeAttempt,
  MarketDataBundle,
  MarketRiskState,
  PaperPortfolio,
  RelativeValueSignal,
} from "@/lib/domain/types";
import type { KillSwitchState } from "@/lib/kill-switch/kill-switch";
import {
  createActionLogEntry,
  INITIAL_STATE,
  type StateStore,
  type ValorLocalState,
} from "@/lib/state/local-store";

const SNAPSHOT_KEY = "valor-local-state";
const MIGRATIONS = ["0001_initial.sql", "0002_durable_state.sql"];

export class SqliteStateStore implements StateStore {
  private db: DatabaseSync | null = null;

  constructor(private readonly path = defaultSqlitePath(process.env)) {}

  read(): ValorLocalState {
    const db = this.getDb();
    const row = db
      .prepare("SELECT value_json FROM app_state WHERE key = ?")
      .get(SNAPSHOT_KEY) as { value_json: string } | undefined;

    if (!row) return INITIAL_STATE;

    try {
      return {
        ...INITIAL_STATE,
        ...(JSON.parse(row.value_json) as Partial<ValorLocalState>),
      };
    } catch {
      return INITIAL_STATE;
    }
  }

  update(mutator: (state: ValorLocalState) => ValorLocalState): ValorLocalState {
    const next = mutator(this.read());
    this.write(next);
    return next;
  }

  appendAction(input: {
    action: string;
    status: "ok" | "error" | "dry_run";
    message: string;
    timestamp?: string;
  }): ValorLocalState {
    return this.update((state) => ({
      ...state,
      actionLog: [createActionLogEntry(input), ...state.actionLog].slice(0, 100),
    }));
  }

  write(state: ValorLocalState) {
    const db = this.getDb();
    const now = new Date().toISOString();

    db.exec("BEGIN IMMEDIATE");
    try {
      this.writeSnapshot(db, state, now);
      if (state.data) this.writeMarketData(db, state.data);
      if (state.dataQuality) this.writeDataQuality(db, state.dataQuality);
      if (state.signals) this.writeSignals(db, state.signals);
      if (state.risk) this.writeRisk(db, state.risk);
      if (state.backtest) this.writeBacktest(db, state.backtest, now);
      if (state.paper) this.writePaper(db, state.paper);
      this.writeLiveTradeAttempts(db, state.liveTradeAttempts);
      this.writeAlertEvents(db, state.alertEvents);
      this.writeAlertDeliveries(db, state.alertDeliveries);
      this.writeAuditEvents(db, state.auditEvents);
      this.writeActionLog(db, state.actionLog);
      if (state.killSwitch) this.writeKillSwitch(db, state.killSwitch, now);
      db.exec("COMMIT");
    } catch (error) {
      db.exec("ROLLBACK");
      throw error;
    }
  }

  close() {
    this.db?.close();
    this.db = null;
  }

  private getDb(): DatabaseSync {
    if (!this.db) {
      mkdirSync(dirname(this.path), { recursive: true });
      this.db = new DatabaseSync(this.path);
      this.applyMigrations(this.db);
    }
    return this.db;
  }

  private applyMigrations(db: DatabaseSync) {
    for (const migration of MIGRATIONS) {
      const path = join(process.cwd(), "src", "db", "migrations", migration);
      if (existsSync(path)) db.exec(readFileSync(path, "utf8"));
    }
    this.ensureSignalEnrichmentColumns(db);
  }

  private ensureSignalEnrichmentColumns(db: DatabaseSync) {
    const rows = db.prepare("PRAGMA table_info(relative_value_signals)").all() as Array<{
      name: string;
    }>;
    const existing = new Set(rows.map((row) => row.name));
    const columns = [
      ["zscore", "REAL"],
      ["spread_value", "REAL"],
      ["expected_convergence_hours", "REAL"],
    ] as const;

    for (const [name, type] of columns) {
      if (!existing.has(name)) {
        db.exec(`ALTER TABLE relative_value_signals ADD COLUMN ${name} ${type}`);
      }
    }
  }

  private writeSnapshot(db: DatabaseSync, state: ValorLocalState, updatedAt: string) {
    db.prepare(
      `INSERT OR REPLACE INTO app_state (key, value_json, updated_at)
       VALUES (?, ?, ?)`,
    ).run(SNAPSHOT_KEY, JSON.stringify(state), updatedAt);
  }

  private writeMarketData(db: DatabaseSync, data: MarketDataBundle) {
    const statement = db.prepare(
      `INSERT OR REPLACE INTO market_snapshots (
        id, venue, base, quote, instrument_type, price, mark_price, index_price,
        funding_rate_8h, open_interest_usd, volume_24h_usd, volatility_30d,
        change_24h_pct, spread_bps, bid_depth_usd, ask_depth_usd, timestamp
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    );

    for (const market of data.markets) {
      statement.run(
        market.id,
        market.venue,
        market.base,
        market.quote,
        market.instrumentType,
        market.price,
        market.markPrice ?? null,
        market.indexPrice ?? null,
        market.fundingRate8h ?? null,
        market.openInterestUsd ?? null,
        market.volume24hUsd,
        market.volatility30d,
        market.change24hPct,
        market.orderBook.spreadBps,
        market.orderBook.bidDepthUsd,
        market.orderBook.askDepthUsd,
        market.timestamp,
      );
    }
  }

  private writeDataQuality(db: DatabaseSync, report: DataQualityReport) {
    db.prepare(
      `INSERT OR REPLACE INTO data_quality_reports (
        id, connector_id, connector_label, mode, status, generated_at, assessed_at,
        data_age_minutes, market_count, issue_count, critical_issue_count,
        fallback_used, fixture_backed, blocks_paper_trading, summary, issues_json
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).run(
      `data-quality:${report.generatedAt}:${report.connectorId}`,
      report.connectorId,
      report.connectorLabel,
      report.mode,
      report.status,
      report.generatedAt,
      report.assessedAt,
      report.dataAgeMinutes,
      report.marketCount,
      report.issueCount,
      report.criticalIssueCount,
      report.fallbackUsed ? 1 : 0,
      report.fixtureBacked ? 1 : 0,
      report.blocksPaperTrading ? 1 : 0,
      report.summary,
      JSON.stringify(report.issues),
    );
  }

  private writeSignals(db: DatabaseSync, signals: RelativeValueSignal[]) {
    const statement = db.prepare(
      `INSERT OR REPLACE INTO relative_value_signals (
        id, kind, asset_pair, venue, direction, confidence, expected_edge_bps,
        risk_score, liquidity_score, opportunity_score, explanation,
        eligible_for_paper_trading, eligible_for_live_trading, timestamp,
        zscore, spread_value, expected_convergence_hours
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    );

    for (const signal of signals) {
      statement.run(
        signal.id,
        signal.kind,
        signal.assetPair,
        signal.venue,
        signal.direction,
        signal.confidence,
        signal.expectedEdgeBps,
        signal.riskScore,
        signal.liquidityScore,
        signal.opportunityScore,
        signal.explanation,
        signal.eligibleForPaperTrading ? 1 : 0,
        signal.eligibleForLiveTrading ? 1 : 0,
        signal.timestamp,
        signal.zscore ?? null,
        signal.spreadValue ?? null,
        signal.expectedConvergenceHours ?? null,
      );
    }
  }

  private writeRisk(db: DatabaseSync, risk: MarketRiskState) {
    db.prepare(
      `INSERT OR REPLACE INTO risk_states (
        id, state, score, explanation, restrictions_json, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?)`,
    ).run(
      "latest",
      risk.state,
      risk.score,
      risk.explanation,
      JSON.stringify(risk.tradingRestrictions),
      risk.updatedAt,
    );

    const statement = db.prepare(
      `INSERT OR REPLACE INTO risk_alerts (
        id, severity, category, title, explanation, source, restrictions_json, timestamp
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    );

    for (const alert of risk.activeAlerts) {
      statement.run(
        alert.id,
        alert.severity,
        alert.category,
        alert.title,
        alert.explanation,
        alert.source,
        JSON.stringify(alert.restrictions),
        alert.timestamp,
      );
    }
  }

  private writeBacktest(
    db: DatabaseSync,
    backtest: ValorLocalState["backtest"],
    createdAt: string,
  ) {
    if (!backtest) return;

    db.prepare(
      `INSERT OR REPLACE INTO backtest_runs (
        id, strategy_name, started_at, ended_at, starting_cash_usd,
        ending_equity_usd, total_return_pct, max_drawdown_pct, sharpe, sortino,
        win_rate_pct, exposure_avg_pct, turnover_usd, total_fees_usd,
        report_json, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).run(
      `backtest:${backtest.strategyName}:${backtest.startedAt}:${backtest.endedAt}`,
      backtest.strategyName,
      backtest.startedAt,
      backtest.endedAt,
      backtest.startingCashUsd,
      backtest.endingEquityUsd,
      backtest.totalReturnPct,
      backtest.maxDrawdownPct,
      backtest.sharpe,
      backtest.sortino,
      backtest.winRatePct,
      backtest.exposureAvgPct,
      backtest.turnoverUsd,
      backtest.totalFeesUsd,
      JSON.stringify(backtest),
      createdAt,
    );
  }

  private writePaper(db: DatabaseSync, paper: PaperPortfolio) {
    db.exec("DELETE FROM paper_positions");

    const positionStatement = db.prepare(
      `INSERT OR REPLACE INTO paper_positions (
        id, signal_id, asset_pair, venue, direction, notional_usd,
        entry_edge_bps, mark_pnl_usd, opened_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    );

    for (const position of paper.positions) {
      positionStatement.run(
        position.id,
        position.signalId,
        position.assetPair,
        position.venue,
        position.direction,
        position.notionalUsd,
        position.entryEdgeBps,
        position.markPnlUsd,
        position.openedAt,
      );
    }

    const tradeStatement = db.prepare(
      `INSERT OR REPLACE INTO paper_trades (
        id, signal_id, timestamp, asset_pair, venue, direction, notional_usd,
        fees_usd, status, reason
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    );

    for (const trade of [...paper.trades, ...paper.rejectedSignals]) {
      tradeStatement.run(
        trade.id,
        trade.signalId,
        trade.timestamp,
        trade.assetPair,
        trade.venue,
        trade.direction,
        trade.notionalUsd,
        trade.feesUsd,
        trade.status,
        trade.reason,
      );
    }
  }

  private writeAlertEvents(db: DatabaseSync, alerts: AlertEvent[]) {
    const statement = db.prepare(
      `INSERT OR REPLACE INTO alert_events (
        id, severity, title, message, source, scope_json, fingerprint,
        trading_impact, metadata_json, created_at, acknowledged_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    );

    for (const alert of alerts) {
      statement.run(
        alert.id,
        alert.severity,
        alert.title,
        alert.message,
        alert.source,
        JSON.stringify(alert.scope),
        alert.fingerprint,
        alert.tradingImpact,
        JSON.stringify(alert.metadata),
        alert.createdAt,
        alert.acknowledgedAt ?? null,
      );
    }
  }

  private writeLiveTradeAttempts(db: DatabaseSync, attempts: LiveTradeAttempt[]) {
    const statement = db.prepare(
      `INSERT OR REPLACE INTO live_trade_attempts (
        id, signal_id, requested_notional_usd, allowed, dry_run, reasons_json,
        created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?)`,
    );

    for (const attempt of attempts) {
      statement.run(
        attempt.id,
        attempt.signalId,
        attempt.requestedNotionalUsd,
        attempt.allowed ? 1 : 0,
        attempt.dryRun ? 1 : 0,
        JSON.stringify(attempt.reasons),
        attempt.createdAt,
      );
    }
  }

  private writeAlertDeliveries(db: DatabaseSync, deliveries: AlertDelivery[]) {
    const statement = db.prepare(
      `INSERT OR REPLACE INTO alert_deliveries (
        id, alert_id, channel, provider, status, attempted_at, destination,
        redacted_message, error
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    );

    for (const delivery of deliveries) {
      statement.run(
        delivery.id,
        delivery.alertId,
        delivery.channel,
        delivery.provider,
        delivery.status,
        delivery.attemptedAt,
        delivery.destination,
        delivery.redactedMessage,
        delivery.error ?? null,
      );
    }
  }

  private writeAuditEvents(db: DatabaseSync, events: AuditEvent[]) {
    const statement = db.prepare(
      `INSERT OR REPLACE INTO audit_events (
        id, timestamp, actor, action, summary, metadata_json
      ) VALUES (?, ?, ?, ?, ?, ?)`,
    );

    for (const event of events) {
      statement.run(
        event.id,
        event.timestamp,
        event.actor,
        event.action,
        event.summary,
        JSON.stringify(event.metadata),
      );
    }
  }

  private writeActionLog(db: DatabaseSync, entries: ValorLocalState["actionLog"]) {
    const statement = db.prepare(
      `INSERT OR REPLACE INTO action_log (
        id, timestamp, action, status, message
      ) VALUES (?, ?, ?, ?, ?)`,
    );

    for (const entry of entries) {
      statement.run(
        entry.id,
        entry.timestamp,
        entry.action,
        entry.status,
        entry.message,
      );
    }
  }

  private writeKillSwitch(
    db: DatabaseSync,
    killSwitch: KillSwitchState,
    updatedAt: string,
  ) {
    db.prepare(
      `INSERT OR REPLACE INTO kill_switch_states (
        id, active, reason, activated_at, activated_by, reset_requested_at,
        dashboard_reset_required, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    ).run(
      "current",
      killSwitch.active ? 1 : 0,
      killSwitch.reason,
      killSwitch.activatedAt ?? null,
      killSwitch.activatedBy ?? null,
      killSwitch.resetRequestedAt ?? null,
      killSwitch.dashboardResetRequired ? 1 : 0,
      updatedAt,
    );
  }
}

export function sqlitePathFromDatabaseUrl(
  databaseUrl: string | undefined,
): string | null {
  if (!databaseUrl) return ".valor/valor.sqlite";

  if (
    databaseUrl.startsWith("postgres://") ||
    databaseUrl.startsWith("postgresql://")
  ) {
    return null;
  }

  if (databaseUrl.startsWith("file:")) {
    return normalizePath(databaseUrl.slice("file:".length));
  }

  if (databaseUrl.startsWith("sqlite:")) {
    return normalizePath(databaseUrl.slice("sqlite:".length));
  }

  if (/\.(db|sqlite|sqlite3)$/i.test(databaseUrl)) {
    return normalizePath(databaseUrl);
  }

  return null;
}

function defaultSqlitePath(env: NodeJS.ProcessEnv): string {
  return sqlitePathFromDatabaseUrl(env.DATABASE_URL) ?? ".valor/valor.sqlite";
}

function normalizePath(path: string): string {
  const clean = path.replace(/^\/\//, "");
  if (isAbsolute(clean)) return clean;
  return clean || ".valor/valor.sqlite";
}
