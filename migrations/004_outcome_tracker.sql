-- ADR-018 B2: Outcome Tracker query indexes.

PRAGMA foreign_keys = ON;

CREATE INDEX IF NOT EXISTS idx_outcomes_case
  ON outcome_measurements(case_id);

CREATE INDEX IF NOT EXISTS idx_outcomes_client_created
  ON outcome_measurements(client_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_outcomes_case_metric_window
  ON outcome_measurements(
    case_id,
    metric,
    IFNULL(measurement_start, ''),
    IFNULL(measurement_end, '')
  );

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('004_outcome_tracker');
