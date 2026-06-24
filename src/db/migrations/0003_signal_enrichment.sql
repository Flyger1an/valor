-- Enrich relative_value_signals with the locked Evolver contract fields.
-- Nullable so existing rows migrate cleanly; the engine populates them going forward.
ALTER TABLE relative_value_signals ADD COLUMN zscore REAL;
ALTER TABLE relative_value_signals ADD COLUMN spread_value REAL;
ALTER TABLE relative_value_signals ADD COLUMN expected_convergence_hours REAL;
