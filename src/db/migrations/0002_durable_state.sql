CREATE TABLE IF NOT EXISTS app_state (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS data_quality_reports (
  id TEXT PRIMARY KEY,
  connector_id TEXT NOT NULL,
  connector_label TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  assessed_at TEXT NOT NULL,
  data_age_minutes REAL NOT NULL,
  market_count INTEGER NOT NULL,
  issue_count INTEGER NOT NULL,
  critical_issue_count INTEGER NOT NULL,
  fallback_used INTEGER NOT NULL,
  fixture_backed INTEGER NOT NULL,
  blocks_paper_trading INTEGER NOT NULL,
  summary TEXT NOT NULL,
  issues_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_positions (
  id TEXT PRIMARY KEY,
  signal_id TEXT NOT NULL,
  asset_pair TEXT NOT NULL,
  venue TEXT NOT NULL,
  direction TEXT NOT NULL,
  notional_usd REAL NOT NULL,
  entry_edge_bps REAL NOT NULL,
  mark_pnl_usd REAL NOT NULL,
  opened_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_log (
  id TEXT PRIMARY KEY,
  timestamp TEXT NOT NULL,
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_data_quality_reports_time_status
  ON data_quality_reports (assessed_at, status);

CREATE INDEX IF NOT EXISTS idx_paper_positions_signal
  ON paper_positions (signal_id);

CREATE INDEX IF NOT EXISTS idx_action_log_time_action
  ON action_log (timestamp, action);
