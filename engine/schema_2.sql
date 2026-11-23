-- Module tables (checkup, cleaner, dns, bench, recipes, events, receipts).
-- Migration 2. Same conventions as schema.sql.

CREATE TABLE IF NOT EXISTS checks (
  id            TEXT PRIMARY KEY,
  module        TEXT NOT NULL,
  category      TEXT NOT NULL,
  title         TEXT NOT NULL,
  plain         TEXT NOT NULL,
  severity      TEXT NOT NULL,
  fix_tweaks    TEXT,
  auto_safe     INTEGER NOT NULL DEFAULT 0,
  weight        REAL NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS check_runs (
  id                TEXT PRIMARY KEY,
  started_at        INTEGER NOT NULL,
  finished_at       INTEGER,
  trigger           TEXT NOT NULL,
  score_speed       INTEGER, score_security INTEGER,
  score_privacy     INTEGER, score_reliability INTEGER,
  score_total       INTEGER,
  checks_run        INTEGER
);

CREATE TABLE IF NOT EXISTS findings (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES check_runs(id),
  check_id      TEXT NOT NULL,
  category      TEXT NOT NULL,
  severity      TEXT NOT NULL,
  title         TEXT NOT NULL,
  plain         TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  impact        TEXT,
  auto_safe     INTEGER NOT NULL,
  fix_tweaks    TEXT,
  state         TEXT NOT NULL DEFAULT 'new',
  first_seen    INTEGER NOT NULL,
  last_seen     INTEGER NOT NULL,
  tx_id         TEXT
);
CREATE INDEX IF NOT EXISTS idx_findings_state ON findings(state, severity);

CREATE TABLE IF NOT EXISTS events (
  id           TEXT PRIMARY KEY,
  at           INTEGER NOT NULL,
  kind         TEXT NOT NULL,
  source       TEXT NOT NULL,
  severity     TEXT NOT NULL,
  narrative    TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  tx_id        TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at DESC);

CREATE TABLE IF NOT EXISTS clean_rules (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  class       TEXT NOT NULL,
  risk        TEXT NOT NULL,
  patterns    TEXT NOT NULL,
  exempt      TEXT,
  note        TEXT,
  enabled     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS clean_runs (
  id            TEXT PRIMARY KEY,
  tx_id         TEXT,
  started_at    INTEGER NOT NULL,
  finished_at   INTEGER,
  bytes_found   INTEGER NOT NULL DEFAULT 0,
  bytes_moved   INTEGER NOT NULL DEFAULT 0,
  files         INTEGER NOT NULL DEFAULT 0,
  rule_ids      TEXT,
  receipt_json  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vault_items (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES clean_runs(id),
  rule_id       TEXT,
  original_path TEXT NOT NULL,
  vault_path    TEXT NOT NULL,
  size          INTEGER NOT NULL,
  sha256        TEXT NOT NULL,
  moved_at      INTEGER NOT NULL,
  restored_at   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_vault_run ON vault_items(run_id);

CREATE TABLE IF NOT EXISTS benches (
  id            TEXT PRIMARY KEY,
  at            INTEGER NOT NULL,
  duration_ms   INTEGER NOT NULL,
  score         REAL,
  detail_json   TEXT NOT NULL,
  env_json      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recipes (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  enabled        INTEGER NOT NULL DEFAULT 1,
  trigger_json   TEXT NOT NULL,
  actions_json   TEXT NOT NULL,
  created_at     INTEGER NOT NULL,
  last_run_at    INTEGER,
  run_count      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dns_state (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dns_counters (
  domain      TEXT PRIMARY KEY,
  app         TEXT NOT NULL,
  blocked     INTEGER NOT NULL DEFAULT 0,
  last_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dns_allow (
  domain TEXT NOT NULL,
  app    TEXT NOT NULL DEFAULT '*',
  PRIMARY KEY (domain, app)
);

CREATE TABLE IF NOT EXISTS receipts (
  id           TEXT PRIMARY KEY,
  at           INTEGER NOT NULL,
  kind         TEXT NOT NULL,
  title        TEXT NOT NULL,
  summary      TEXT NOT NULL,
  detail_json  TEXT NOT NULL,
  tx_id        TEXT,
  undone_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_receipts_at ON receipts(at DESC);

CREATE TABLE IF NOT EXISTS sens_profiles (
  game_id    TEXT PRIMARY KEY,
  cm360      REAL,
  dpi        INTEGER NOT NULL DEFAULT 800,
  locked     INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
