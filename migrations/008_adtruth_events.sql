-- ADR-023: AdTruth behavioral event evidence store.
-- Stores normalized LP/SDK style events for fraud review, CV preservation,
-- and campaign/adset/ad/placement attribution.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS adtruth_events (
  event_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  session_id TEXT,
  visitor_id TEXT,
  event_name TEXT,
  event_at TEXT,
  source TEXT,
  medium TEXT,
  campaign TEXT,
  campaign_id TEXT,
  adset_id TEXT,
  ad_id TEXT,
  placement TEXT,
  gclid TEXT,
  fbclid TEXT,
  fraud_probability REAL NOT NULL,
  fraud_band TEXT NOT NULL,
  signals_json TEXT NOT NULL DEFAULT '[]',
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_adtruth_events_client_time
  ON adtruth_events(client_id, event_at);
CREATE INDEX IF NOT EXISTS idx_adtruth_events_band
  ON adtruth_events(client_id, fraud_band, event_at);
CREATE INDEX IF NOT EXISTS idx_adtruth_events_campaign
  ON adtruth_events(client_id, campaign_id, adset_id, ad_id, placement);

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('008_adtruth_events');
