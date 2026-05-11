-- ADR-021: natural-language rule intake.
-- Stores reviewable draft rules generated from operator text. Drafts are not
-- active rules until reviewed and committed into YAML by a human/operator flow.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS rule_change_drafts (
  draft_id TEXT PRIMARY KEY,
  source_text TEXT NOT NULL,
  proposed_rule_id TEXT,
  proposed_yaml TEXT NOT NULL,
  target_family TEXT,
  target_layer TEXT,
  status TEXT NOT NULL DEFAULT 'review_required',
  reviewer_user_id TEXT,
  reviewed_at TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rule_change_drafts_status
  ON rule_change_drafts(status, created_at);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('006_rule_change_drafts');
