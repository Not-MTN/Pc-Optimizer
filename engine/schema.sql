-- PC Optimizer — Guardian core schema (v1)
-- Subset of SCHEMA.md that the safety engine needs. Other modules add their
-- own tables on their own migration numbers.

CREATE TABLE IF NOT EXISTS meta (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS migrations (
  version     INTEGER PRIMARY KEY,
  applied_at  INTEGER NOT NULL,
  checksum    TEXT NOT NULL,
  duration_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  scope      TEXT NOT NULL,
  key        TEXT NOT NULL,
  value_json TEXT NOT NULL,
  schema     TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (scope, key)
);

CREATE TABLE IF NOT EXISTS tweaks (
  id             TEXT PRIMARY KEY,
  module         TEXT NOT NULL,
  title          TEXT NOT NULL,
  plain          TEXT NOT NULL,
  tier           TEXT NOT NULL,
  risk           TEXT NOT NULL,
  needs_reboot   INTEGER NOT NULL DEFAULT 0,
  preconditions  TEXT,
  effects_json   TEXT NOT NULL,
  revert_json    TEXT,
  measure_json   TEXT,
  blast_radius   TEXT,
  knowledge_key  TEXT,
  source_file    TEXT,
  catalog_version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,
  started_at    INTEGER NOT NULL,
  finished_at   INTEGER,
  state         TEXT NOT NULL,
  trigger_json  TEXT,
  source        TEXT NOT NULL,
  profile_id    TEXT,
  armed_until   INTEGER,
  parent_tx     TEXT REFERENCES transactions(id),
  note          TEXT
);

CREATE TABLE IF NOT EXISTS journal (
  seq           INTEGER PRIMARY KEY,
  tx_id         TEXT NOT NULL REFERENCES transactions(id),
  tweak_id      TEXT NOT NULL,
  phase         TEXT NOT NULL CHECK (phase IN
                  ('begin','applied','verified','failed','reverted','suspended','confirmed')),
  at            INTEGER NOT NULL,
  prestate_json TEXT NOT NULL,
  poststate_json TEXT,
  error         TEXT,
  checksum      TEXT NOT NULL,
  prev_checksum TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_journal_tx ON journal(tx_id, seq);
CREATE INDEX IF NOT EXISTS idx_journal_at ON journal(at DESC);

-- Append-only enforcement at the storage layer, not just in code.
CREATE TRIGGER IF NOT EXISTS journal_no_update
BEFORE UPDATE ON journal
BEGIN
  SELECT RAISE(ABORT, 'journal is append-only');
END;

CREATE TRIGGER IF NOT EXISTS journal_no_delete
BEFORE DELETE ON journal
BEGIN
  SELECT RAISE(ABORT, 'journal is append-only');
END;

CREATE TABLE IF NOT EXISTS snapshots (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,
  at            INTEGER NOT NULL,
  size_bytes    INTEGER NOT NULL,
  manifest_hash TEXT NOT NULL,
  parent_id     TEXT REFERENCES snapshots(id),
  note          TEXT,
  verified_at   INTEGER
);

-- Projection: the current *expected* value of every address we manage.
-- Rebuildable from the journal at any time (see Engine.rebuild_managed()).
CREATE TABLE IF NOT EXISTS managed (
  address       TEXT PRIMARY KEY,
  expected_json TEXT NOT NULL,
  applied_by    TEXT NOT NULL,
  updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS drift (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  address      TEXT NOT NULL,
  expected_json TEXT NOT NULL,
  actual_json   TEXT NOT NULL,
  detected_at   INTEGER NOT NULL,
  actor_guess   TEXT,
  repaired_at   INTEGER,
  repair_tx     TEXT
);

CREATE TABLE IF NOT EXISTS boot_events (
  id                TEXT PRIMARY KEY,
  at                INTEGER NOT NULL,
  healthy_at_ms     INTEGER,
  boot_guard_action TEXT,
  unexpected_shutdown INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS blame (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  event_kind TEXT NOT NULL,
  event_at   INTEGER NOT NULL,
  suspects_json TEXT NOT NULL,
  confidence REAL NOT NULL,
  narrative  TEXT NOT NULL
);
