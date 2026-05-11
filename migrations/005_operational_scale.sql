-- ADR-020: operational scale foundation.
-- Adds DB-owned ledgers for client connections, secret references, case
-- transitions, rule operations metadata, outcome rollups, and self-monitoring.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS client_connections (
  connection_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  connection_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'unknown',
  required INTEGER NOT NULL DEFAULT 0,
  strongly_recommended INTEGER NOT NULL DEFAULT 0,
  last_checked_at TEXT,
  last_success_at TEXT,
  last_error TEXT,
  config_ref TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(client_id, provider, connection_type)
);

CREATE INDEX IF NOT EXISTS idx_client_connections_client_status
  ON client_connections(client_id, status);

CREATE TABLE IF NOT EXISTS secret_references (
  secret_ref_id TEXT PRIMARY KEY,
  client_id TEXT,
  provider TEXT NOT NULL,
  env_name TEXT NOT NULL,
  purpose TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'referenced',
  required INTEGER NOT NULL DEFAULT 1,
  last_verified_at TEXT,
  rotation_due_at TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(client_id, provider, env_name, purpose)
);

CREATE INDEX IF NOT EXISTS idx_secret_references_client_provider
  ON secret_references(client_id, provider);

CREATE TABLE IF NOT EXISTS rule_registry_operations (
  rule_id TEXT PRIMARY KEY,
  lifecycle TEXT NOT NULL DEFAULT 'active',
  owner TEXT,
  required_data_sources_json TEXT NOT NULL DEFAULT '[]',
  duplicate_group TEXT,
  prerequisite_rule_ids_json TEXT NOT NULL DEFAULT '[]',
  conflict_rule_ids_json TEXT NOT NULL DEFAULT '[]',
  rule_family TEXT,
  false_positive_rate REAL,
  win_rate REAL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  synced_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rule_registry_ops_lifecycle
  ON rule_registry_operations(lifecycle);

CREATE TABLE IF NOT EXISTS case_transitions (
  transition_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  actor_id TEXT,
  reason TEXT,
  transitioned_at TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(case_id, to_status, transitioned_at, actor_type, actor_id)
);

CREATE INDEX IF NOT EXISTS idx_case_transitions_case
  ON case_transitions(case_id, transitioned_at);

CREATE TABLE IF NOT EXISTS system_incidents (
  incident_id TEXT PRIMARY KEY,
  client_id TEXT,
  severity TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  component TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  resolved_at TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(client_id, component, title, status)
);

CREATE INDEX IF NOT EXISTS idx_system_incidents_status
  ON system_incidents(status, severity);

CREATE TABLE IF NOT EXISTS health_checks (
  health_check_id TEXT PRIMARY KEY,
  client_id TEXT,
  component TEXT NOT NULL,
  status TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  latency_ms REAL,
  detail TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_health_checks_client_component
  ON health_checks(client_id, component, checked_at);

CREATE TABLE IF NOT EXISTS rule_outcome_rollups (
  rule_id TEXT PRIMARY KEY,
  cases_count INTEGER NOT NULL DEFAULT 0,
  measured_count INTEGER NOT NULL DEFAULT 0,
  improved_count INTEGER NOT NULL DEFAULT 0,
  worsened_count INTEGER NOT NULL DEFAULT 0,
  unknown_count INTEGER NOT NULL DEFAULT 0,
  avg_change_pct REAL,
  estimated_value_yen REAL NOT NULL DEFAULT 0,
  win_rate REAL,
  last_measured_at TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('005_operational_scale');
