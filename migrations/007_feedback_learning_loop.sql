-- ADR-022: Track/Learn feedback loop.
-- Separates "client answered" from "work was executed" and stores learned
-- rule-level priors that can feed future Diagnose/Prioritize decisions.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS case_executions (
  execution_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  execution_status TEXT NOT NULL,
  evidence_source TEXT NOT NULL,
  evidence_quality TEXT NOT NULL DEFAULT 'low',
  actor_type TEXT,
  actor_id TEXT NOT NULL DEFAULT '',
  executed_at TEXT,
  verified_at TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(case_id, execution_status, evidence_source, actor_id)
);

CREATE INDEX IF NOT EXISTS idx_case_executions_case
  ON case_executions(case_id, execution_status);
CREATE INDEX IF NOT EXISTS idx_case_executions_rule
  ON case_executions(rule_id, verified_at);
CREATE INDEX IF NOT EXISTS idx_case_executions_client
  ON case_executions(client_id, executed_at);

CREATE TABLE IF NOT EXISTS rule_learning_stats (
  rule_id TEXT PRIMARY KEY,
  cases_count INTEGER NOT NULL DEFAULT 0,
  execution_count INTEGER NOT NULL DEFAULT 0,
  execution_rate REAL,
  measured_count INTEGER NOT NULL DEFAULT 0,
  improved_count INTEGER NOT NULL DEFAULT 0,
  worsened_count INTEGER NOT NULL DEFAULT 0,
  unknown_count INTEGER NOT NULL DEFAULT 0,
  false_positive_count INTEGER NOT NULL DEFAULT 0,
  false_positive_rate REAL,
  win_rate REAL,
  avg_change_pct REAL,
  estimated_value_yen REAL NOT NULL DEFAULT 0,
  priority_adjustment REAL NOT NULL DEFAULT 0,
  confidence TEXT NOT NULL DEFAULT 'low',
  recommendation TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  last_learned_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rule_learning_priority
  ON rule_learning_stats(priority_adjustment, win_rate);

CREATE TABLE IF NOT EXISTS rule_learning_events (
  learning_event_id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  source TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_rule_learning_events_rule
  ON rule_learning_events(rule_id, created_at);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('007_feedback_learning_loop');
