-- ADR-019 Phase B2: DB-backed rule registry.

CREATE TABLE IF NOT EXISTS rule_registry (
  rule_id TEXT PRIMARY KEY,
  canonical_rule_id TEXT NOT NULL,
  name TEXT NOT NULL,
  layer TEXT NOT NULL,
  category TEXT,
  severity TEXT,
  root_cause_group TEXT,
  decision_axis TEXT,
  applies_to_json TEXT NOT NULL DEFAULT '{}',
  prerequisite_json TEXT NOT NULL DEFAULT 'null',
  expected_impact_json TEXT NOT NULL DEFAULT 'null',
  messaging_mapped INTEGER NOT NULL DEFAULT 0,
  customer_visible INTEGER NOT NULL DEFAULT 0,
  source_path TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  synced_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rule_registry_layer
  ON rule_registry(layer);
CREATE INDEX IF NOT EXISTS idx_rule_registry_root_cause
  ON rule_registry(root_cause_group);
CREATE INDEX IF NOT EXISTS idx_rule_registry_messaging
  ON rule_registry(messaging_mapped, customer_visible);

CREATE TABLE IF NOT EXISTS rule_registry_issues (
  issue_id TEXT PRIMARY KEY,
  rule_id TEXT NOT NULL,
  issue_type TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'medium',
  source_path TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  synced_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(rule_id, issue_type)
);

CREATE INDEX IF NOT EXISTS idx_rule_registry_issues_type
  ON rule_registry_issues(issue_type);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('002_rule_registry');
