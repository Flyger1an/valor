CREATE TABLE IF NOT EXISTS market_snapshots (
  id TEXT PRIMARY KEY,
  venue TEXT NOT NULL,
  base TEXT NOT NULL,
  quote TEXT NOT NULL,
  instrument_type TEXT NOT NULL,
  price REAL NOT NULL,
  mark_price REAL,
  index_price REAL,
  funding_rate_8h REAL,
  open_interest_usd REAL,
  volume_24h_usd REAL NOT NULL,
  volatility_30d REAL NOT NULL,
  change_24h_pct REAL NOT NULL,
  spread_bps REAL NOT NULL,
  bid_depth_usd REAL NOT NULL,
  ask_depth_usd REAL NOT NULL,
  timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relative_value_signals (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  asset_pair TEXT NOT NULL,
  venue TEXT NOT NULL,
  direction TEXT NOT NULL,
  confidence REAL NOT NULL,
  expected_edge_bps REAL NOT NULL,
  risk_score REAL NOT NULL,
  liquidity_score REAL NOT NULL,
  opportunity_score REAL NOT NULL,
  explanation TEXT NOT NULL,
  eligible_for_paper_trading INTEGER NOT NULL,
  eligible_for_live_trading INTEGER NOT NULL,
  timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_alerts (
  id TEXT PRIMARY KEY,
  severity TEXT NOT NULL,
  category TEXT NOT NULL,
  title TEXT NOT NULL,
  explanation TEXT NOT NULL,
  source TEXT NOT NULL,
  restrictions_json TEXT NOT NULL,
  timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_states (
  id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  score REAL NOT NULL,
  explanation TEXT NOT NULL,
  restrictions_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_runs (
  id TEXT PRIMARY KEY,
  strategy_name TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT NOT NULL,
  starting_cash_usd REAL NOT NULL,
  ending_equity_usd REAL NOT NULL,
  total_return_pct REAL NOT NULL,
  max_drawdown_pct REAL NOT NULL,
  sharpe REAL NOT NULL,
  sortino REAL NOT NULL,
  win_rate_pct REAL NOT NULL,
  exposure_avg_pct REAL NOT NULL,
  turnover_usd REAL NOT NULL,
  total_fees_usd REAL NOT NULL,
  report_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_trades (
  id TEXT PRIMARY KEY,
  signal_id TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  asset_pair TEXT NOT NULL,
  venue TEXT NOT NULL,
  direction TEXT NOT NULL,
  notional_usd REAL NOT NULL,
  fees_usd REAL NOT NULL,
  status TEXT NOT NULL,
  reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS live_trade_attempts (
  id TEXT PRIMARY KEY,
  signal_id TEXT NOT NULL,
  requested_notional_usd REAL NOT NULL,
  allowed INTEGER NOT NULL,
  dry_run INTEGER NOT NULL,
  reasons_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings_changes (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL,
  previous_value TEXT,
  next_value TEXT NOT NULL,
  changed_by TEXT NOT NULL,
  changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY,
  timestamp TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  summary TEXT NOT NULL,
  metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_events (
  id TEXT PRIMARY KEY,
  severity TEXT NOT NULL,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  source TEXT NOT NULL,
  scope_json TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  trading_impact TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  acknowledged_at TEXT
);

CREATE TABLE IF NOT EXISTS alert_deliveries (
  id TEXT PRIMARY KEY,
  alert_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  provider TEXT NOT NULL,
  status TEXT NOT NULL,
  attempted_at TEXT NOT NULL,
  destination TEXT NOT NULL,
  redacted_message TEXT NOT NULL,
  error TEXT
);

CREATE TABLE IF NOT EXISTS kill_switch_states (
  id TEXT PRIMARY KEY,
  active INTEGER NOT NULL,
  reason TEXT NOT NULL,
  activated_at TEXT,
  activated_by TEXT,
  reset_requested_at TEXT,
  dashboard_reset_required INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_analyst_runs (
  id TEXT PRIMARY KEY,
  mode TEXT NOT NULL,
  model TEXT,
  question_redacted TEXT NOT NULL,
  answer_redacted TEXT NOT NULL,
  citations_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_snapshots_pair_time
  ON market_snapshots (base, quote, instrument_type, timestamp);

CREATE INDEX IF NOT EXISTS idx_signals_time_score
  ON relative_value_signals (timestamp, opportunity_score);

CREATE INDEX IF NOT EXISTS idx_risk_alerts_time_severity
  ON risk_alerts (timestamp, severity);

CREATE INDEX IF NOT EXISTS idx_audit_events_time_action
  ON audit_events (timestamp, action);

CREATE INDEX IF NOT EXISTS idx_alert_events_fingerprint_created
  ON alert_events (fingerprint, created_at);

CREATE INDEX IF NOT EXISTS idx_alert_deliveries_status_time
  ON alert_deliveries (status, attempted_at);

CREATE INDEX IF NOT EXISTS idx_llm_analyst_runs_time
  ON llm_analyst_runs (created_at);
