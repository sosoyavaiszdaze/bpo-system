-- ADR-019 Phase B3: decision trace ledger.

CREATE TABLE IF NOT EXISTS decision_traces (
  trace_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  evaluation_date TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_decision_traces_client_date
  ON decision_traces(client_id, evaluation_date);
CREATE INDEX IF NOT EXISTS idx_decision_traces_rule
  ON decision_traces(rule_id, evaluation_date);
CREATE INDEX IF NOT EXISTS idx_decision_traces_stage_status
  ON decision_traces(stage, status);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('003_decision_traces');
