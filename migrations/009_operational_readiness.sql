-- ADR-024: operational readiness and audit trail.
-- Provides an SLO-style gate before scaling the operation beyond MVP.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS operational_readiness_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  checked_at TEXT NOT NULL,
  status TEXT NOT NULL,
  blocker_count INTEGER NOT NULL DEFAULT 0,
  warning_count INTEGER NOT NULL DEFAULT 0,
  summary_json TEXT NOT NULL DEFAULT '{}',
  issues_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_operational_readiness_status
  ON operational_readiness_snapshots(status, checked_at);

CREATE TABLE IF NOT EXISTS audit_log (
  audit_id TEXT PRIMARY KEY,
  actor_type TEXT NOT NULL,
  actor_id TEXT,
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT,
  client_id TEXT,
  source TEXT,
  occurred_at TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_log_entity
  ON audit_log(entity_type, entity_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_client
  ON audit_log(client_id, occurred_at);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('009_operational_readiness');
