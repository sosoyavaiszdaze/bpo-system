-- ADR-018 Phase B1: operational platform minimum schema.
-- SQLite first, PostgreSQL-compatible shape later.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS clients (
  client_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  vertical TEXT,
  ec_platform TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  owner_user_id TEXT,
  chatwork_room_id TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS data_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  source TEXT NOT NULL,
  snapshot_date TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  error_json TEXT,
  UNIQUE(client_id, source, snapshot_date)
);

CREATE TABLE IF NOT EXISTS rule_evaluations (
  evaluation_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  evaluation_date TEXT NOT NULL,
  status TEXT NOT NULL,
  severity TEXT,
  priority_score REAL,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  data_sources_json TEXT NOT NULL DEFAULT '[]',
  decision_trace_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS operational_cases (
  case_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  severity TEXT,
  owner_type TEXT NOT NULL DEFAULT 'client',
  source_evaluation_id TEXT,
  first_detected_at TEXT NOT NULL,
  first_detected_date TEXT,
  last_detected_at TEXT,
  last_detected_date TEXT,
  notified_at TEXT,
  resolved_at TEXT,
  resolved_date TEXT,
  closed_at TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_operational_cases_client_status
  ON operational_cases(client_id, status);
CREATE INDEX IF NOT EXISTS idx_operational_cases_rule
  ON operational_cases(rule_id);
CREATE INDEX IF NOT EXISTS idx_operational_cases_resolved_date
  ON operational_cases(resolved_date);

CREATE TABLE IF NOT EXISTS case_events (
  event_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  actor_id TEXT,
  event_at TEXT NOT NULL,
  message TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_case_events_case
  ON case_events(case_id, event_at);
CREATE INDEX IF NOT EXISTS idx_case_events_client_type
  ON case_events(client_id, event_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_case_events_dedupe
  ON case_events(case_id, event_type, event_at, actor_type, IFNULL(actor_id, ''));

CREATE TABLE IF NOT EXISTS action_items (
  action_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  title TEXT NOT NULL,
  owner_type TEXT NOT NULL,
  status TEXT NOT NULL,
  due_date TEXT,
  completed_at TEXT,
  evidence_url TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_action_items_client_status
  ON action_items(client_id, status);

CREATE TABLE IF NOT EXISTS outcome_measurements (
  outcome_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  baseline_start TEXT,
  baseline_end TEXT,
  measurement_start TEXT,
  measurement_end TEXT,
  baseline_value REAL,
  measured_value REAL,
  change_pct REAL,
  estimated_value_yen REAL,
  confidence TEXT,
  notes TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_outcomes_client_metric
  ON outcome_measurements(client_id, metric);

CREATE TABLE IF NOT EXISTS rule_feedback (
  feedback_id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  case_id TEXT,
  feedback_type TEXT NOT NULL,
  outcome_score REAL,
  comment TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rule_feedback_rule_type
  ON rule_feedback(rule_id, feedback_type);

CREATE TABLE IF NOT EXISTS job_runs (
  job_run_id TEXT PRIMARY KEY,
  job_name TEXT NOT NULL,
  client_id TEXT,
  scheduled_at TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  error_json TEXT NOT NULL DEFAULT '[]',
  metrics_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_job_runs_client_started
  ON job_runs(client_id, started_at);
CREATE INDEX IF NOT EXISTS idx_job_runs_status
  ON job_runs(status);

CREATE TABLE IF NOT EXISTS chatwork_sent (
  idempotency_key TEXT PRIMARY KEY,
  client_id TEXT,
  room_id TEXT,
  message_id TEXT,
  sent_at TEXT,
  body_hash TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS client_responses (
  response_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  case_id TEXT,
  answer_code TEXT,
  answer_label TEXT,
  status TEXT NOT NULL,
  raw_message TEXT,
  chatwork_message_id TEXT,
  answered_at TEXT,
  source TEXT,
  expires_at TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(client_id, rule_id, chatwork_message_id)
);

CREATE INDEX IF NOT EXISTS idx_client_responses_client_rule
  ON client_responses(client_id, rule_id);
CREATE INDEX IF NOT EXISTS idx_client_responses_status
  ON client_responses(status);

CREATE TABLE IF NOT EXISTS notification_messages (
  notification_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  case_id TEXT,
  channel TEXT NOT NULL,
  destination_id TEXT,
  message_id TEXT,
  idempotency_key TEXT,
  status TEXT NOT NULL,
  sent_at TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_notification_messages_client
  ON notification_messages(client_id, sent_at);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('001_operational_platform');
