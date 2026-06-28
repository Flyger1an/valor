import { real, sqliteTable, text, integer } from "drizzle-orm/sqlite-core";

export const marketSnapshots = sqliteTable("market_snapshots", {
  id: text("id").primaryKey(),
  venue: text("venue").notNull(),
  base: text("base").notNull(),
  quote: text("quote").notNull(),
  instrumentType: text("instrument_type").notNull(),
  price: real("price").notNull(),
  markPrice: real("mark_price"),
  indexPrice: real("index_price"),
  fundingRate8h: real("funding_rate_8h"),
  openInterestUsd: real("open_interest_usd"),
  volume24hUsd: real("volume_24h_usd").notNull(),
  volatility30d: real("volatility_30d").notNull(),
  change24hPct: real("change_24h_pct").notNull(),
  spreadBps: real("spread_bps").notNull(),
  bidDepthUsd: real("bid_depth_usd").notNull(),
  askDepthUsd: real("ask_depth_usd").notNull(),
  timestamp: text("timestamp").notNull(),
});

export const relativeValueSignals = sqliteTable("relative_value_signals", {
  id: text("id").primaryKey(),
  kind: text("kind").notNull(),
  assetPair: text("asset_pair").notNull(),
  venue: text("venue").notNull(),
  direction: text("direction").notNull(),
  confidence: real("confidence").notNull(),
  expectedEdgeBps: real("expected_edge_bps").notNull(),
  riskScore: real("risk_score").notNull(),
  liquidityScore: real("liquidity_score").notNull(),
  opportunityScore: real("opportunity_score").notNull(),
  explanation: text("explanation").notNull(),
  eligibleForPaperTrading: integer("eligible_for_paper_trading", {
    mode: "boolean",
  }).notNull(),
  eligibleForLiveTrading: integer("eligible_for_live_trading", {
    mode: "boolean",
  }).notNull(),
  timestamp: text("timestamp").notNull(),
});

export const riskAlerts = sqliteTable("risk_alerts", {
  id: text("id").primaryKey(),
  severity: text("severity").notNull(),
  category: text("category").notNull(),
  title: text("title").notNull(),
  explanation: text("explanation").notNull(),
  source: text("source").notNull(),
  restrictionsJson: text("restrictions_json").notNull(),
  timestamp: text("timestamp").notNull(),
});

export const riskStates = sqliteTable("risk_states", {
  id: text("id").primaryKey(),
  state: text("state").notNull(),
  score: real("score").notNull(),
  explanation: text("explanation").notNull(),
  restrictionsJson: text("restrictions_json").notNull(),
  updatedAt: text("updated_at").notNull(),
});

export const backtestRuns = sqliteTable("backtest_runs", {
  id: text("id").primaryKey(),
  strategyName: text("strategy_name").notNull(),
  startedAt: text("started_at").notNull(),
  endedAt: text("ended_at").notNull(),
  startingCashUsd: real("starting_cash_usd").notNull(),
  endingEquityUsd: real("ending_equity_usd").notNull(),
  totalReturnPct: real("total_return_pct").notNull(),
  maxDrawdownPct: real("max_drawdown_pct").notNull(),
  sharpe: real("sharpe").notNull(),
  sortino: real("sortino").notNull(),
  winRatePct: real("win_rate_pct").notNull(),
  exposureAvgPct: real("exposure_avg_pct").notNull(),
  turnoverUsd: real("turnover_usd").notNull(),
  totalFeesUsd: real("total_fees_usd").notNull(),
  reportJson: text("report_json").notNull(),
  createdAt: text("created_at").notNull(),
});

export const paperTrades = sqliteTable("paper_trades", {
  id: text("id").primaryKey(),
  signalId: text("signal_id").notNull(),
  timestamp: text("timestamp").notNull(),
  assetPair: text("asset_pair").notNull(),
  venue: text("venue").notNull(),
  direction: text("direction").notNull(),
  notionalUsd: real("notional_usd").notNull(),
  feesUsd: real("fees_usd").notNull(),
  status: text("status").notNull(),
  reason: text("reason").notNull(),
});

export const liveTradeAttempts = sqliteTable("live_trade_attempts", {
  id: text("id").primaryKey(),
  signalId: text("signal_id").notNull(),
  requestedNotionalUsd: real("requested_notional_usd").notNull(),
  allowed: integer("allowed", { mode: "boolean" }).notNull(),
  dryRun: integer("dry_run", { mode: "boolean" }).notNull(),
  reasonsJson: text("reasons_json").notNull(),
  createdAt: text("created_at").notNull(),
});

export const settingsChanges = sqliteTable("settings_changes", {
  id: text("id").primaryKey(),
  key: text("key").notNull(),
  previousValue: text("previous_value"),
  nextValue: text("next_value").notNull(),
  changedBy: text("changed_by").notNull(),
  changedAt: text("changed_at").notNull(),
});

export const auditEvents = sqliteTable("audit_events", {
  id: text("id").primaryKey(),
  timestamp: text("timestamp").notNull(),
  actor: text("actor").notNull(),
  action: text("action").notNull(),
  summary: text("summary").notNull(),
  metadataJson: text("metadata_json").notNull(),
});

export const alertEvents = sqliteTable("alert_events", {
  id: text("id").primaryKey(),
  severity: text("severity").notNull(),
  title: text("title").notNull(),
  message: text("message").notNull(),
  source: text("source").notNull(),
  scopeJson: text("scope_json").notNull(),
  fingerprint: text("fingerprint").notNull(),
  tradingImpact: text("trading_impact").notNull(),
  metadataJson: text("metadata_json").notNull(),
  createdAt: text("created_at").notNull(),
  acknowledgedAt: text("acknowledged_at"),
});

export const alertDeliveries = sqliteTable("alert_deliveries", {
  id: text("id").primaryKey(),
  alertId: text("alert_id").notNull(),
  channel: text("channel").notNull(),
  provider: text("provider").notNull(),
  status: text("status").notNull(),
  attemptedAt: text("attempted_at").notNull(),
  destination: text("destination").notNull(),
  redactedMessage: text("redacted_message").notNull(),
  error: text("error"),
});

export const killSwitchStates = sqliteTable("kill_switch_states", {
  id: text("id").primaryKey(),
  active: integer("active", { mode: "boolean" }).notNull(),
  reason: text("reason").notNull(),
  activatedAt: text("activated_at"),
  activatedBy: text("activated_by"),
  resetRequestedAt: text("reset_requested_at"),
  dashboardResetRequired: integer("dashboard_reset_required", {
    mode: "boolean",
  }).notNull(),
  updatedAt: text("updated_at").notNull(),
});

export const llmAnalystRuns = sqliteTable("llm_analyst_runs", {
  id: text("id").primaryKey(),
  mode: text("mode").notNull(),
  model: text("model"),
  questionRedacted: text("question_redacted").notNull(),
  answerRedacted: text("answer_redacted").notNull(),
  citationsJson: text("citations_json").notNull(),
  createdAt: text("created_at").notNull(),
});

export const appState = sqliteTable("app_state", {
  key: text("key").primaryKey(),
  valueJson: text("value_json").notNull(),
  updatedAt: text("updated_at").notNull(),
});

export const dataQualityReports = sqliteTable("data_quality_reports", {
  id: text("id").primaryKey(),
  connectorId: text("connector_id").notNull(),
  connectorLabel: text("connector_label").notNull(),
  mode: text("mode").notNull(),
  status: text("status").notNull(),
  generatedAt: text("generated_at").notNull(),
  assessedAt: text("assessed_at").notNull(),
  dataAgeMinutes: real("data_age_minutes").notNull(),
  marketCount: integer("market_count").notNull(),
  issueCount: integer("issue_count").notNull(),
  criticalIssueCount: integer("critical_issue_count").notNull(),
  fallbackUsed: integer("fallback_used", { mode: "boolean" }).notNull(),
  fixtureBacked: integer("fixture_backed", { mode: "boolean" }).notNull(),
  blocksPaperTrading: integer("blocks_paper_trading", {
    mode: "boolean",
  }).notNull(),
  summary: text("summary").notNull(),
  issuesJson: text("issues_json").notNull(),
});

export const paperPositions = sqliteTable("paper_positions", {
  id: text("id").primaryKey(),
  signalId: text("signal_id").notNull(),
  assetPair: text("asset_pair").notNull(),
  venue: text("venue").notNull(),
  direction: text("direction").notNull(),
  notionalUsd: real("notional_usd").notNull(),
  entryEdgeBps: real("entry_edge_bps").notNull(),
  markPnlUsd: real("mark_pnl_usd").notNull(),
  openedAt: text("opened_at").notNull(),
});

export const actionLog = sqliteTable("action_log", {
  id: text("id").primaryKey(),
  timestamp: text("timestamp").notNull(),
  action: text("action").notNull(),
  status: text("status").notNull(),
  message: text("message").notNull(),
});
