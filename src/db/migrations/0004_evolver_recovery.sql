CREATE TABLE IF NOT EXISTS evolver_recovery_snapshots (
  id TEXT PRIMARY KEY,
  generated_at TEXT NOT NULL,
  source_label TEXT NOT NULL,
  evidence_status TEXT NOT NULL,
  recovery_status TEXT NOT NULL,
  required_pnl_recovery_usd REAL NOT NULL,
  additional_evidence_days INTEGER NOT NULL,
  additional_closed_trades INTEGER NOT NULL,
  win_rate_gap_pct REAL NOT NULL,
  convergence_rate_gap_pct REAL NOT NULL,
  confidence_haircut_pct REAL,
  gap_score REAL NOT NULL,
  bench_candidates_json TEXT NOT NULL,
  action_codes_json TEXT NOT NULL,
  signature TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evolver_recovery_snapshots_generated_at
  ON evolver_recovery_snapshots (generated_at DESC);
