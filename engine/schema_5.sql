-- Migration 5: battery/thermals, workspaces, drivers, armed-change confirmations.

CREATE TABLE IF NOT EXISTS battery_samples (
  id          TEXT PRIMARY KEY,
  at          INTEGER NOT NULL,
  design_mwh  INTEGER NOT NULL,
  full_mwh    INTEGER NOT NULL,
  now_mwh     INTEGER NOT NULL,
  rate_mw     INTEGER NOT NULL,
  percent     INTEGER NOT NULL,
  plugged     INTEGER NOT NULL,
  cycles      INTEGER,
  temp_c      REAL
);
CREATE INDEX IF NOT EXISTS idx_battery_at ON battery_samples(at DESC);

CREATE TABLE IF NOT EXISTS thermal_events (
  id            TEXT PRIMARY KEY,
  at            INTEGER NOT NULL,
  temp_c        REAL NOT NULL,
  clock_before  REAL NOT NULL,
  clock_after   REAL NOT NULL,
  drop_pct      REAL NOT NULL,
  reason        TEXT
);

CREATE TABLE IF NOT EXISTS workspaces (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  doc_json    TEXT NOT NULL,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS drivers (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL,
  version     TEXT NOT NULL,
  date        INTEGER NOT NULL DEFAULT 0,
  vendor      TEXT,
  signed      INTEGER NOT NULL DEFAULT 1,
  seen_at     INTEGER NOT NULL,
  blocked_at  INTEGER
);

CREATE TABLE IF NOT EXISTS driver_reports (
  id          TEXT PRIMARY KEY,
  version_key TEXT NOT NULL,
  at          INTEGER NOT NULL,
  outcome     TEXT NOT NULL,
  detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_driver_reports ON driver_reports(version_key);

CREATE TABLE IF NOT EXISTS driver_installs (
  id              TEXT PRIMARY KEY,
  driver_id       TEXT NOT NULL,
  at              INTEGER NOT NULL,
  version_before  TEXT,
  snapshot_id     TEXT
);

CREATE TABLE IF NOT EXISTS driver_blocks (
  driver_id   TEXT NOT NULL,
  version     TEXT NOT NULL,
  reason      TEXT NOT NULL,
  blocked_at  INTEGER NOT NULL,
  PRIMARY KEY (driver_id, version)
);

-- armed changes awaiting a "keep it" decision, and what happened to them
CREATE TABLE IF NOT EXISTS armed_changes (
  id            TEXT PRIMARY KEY,
  tx_id         TEXT NOT NULL,
  armed_at      INTEGER NOT NULL,
  window_ms     INTEGER NOT NULL,
  outcome       TEXT NOT NULL DEFAULT 'pending',   -- pending|confirmed|reverted
  decided_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_armed_pending ON armed_changes(outcome, armed_at);

CREATE TABLE IF NOT EXISTS heartbeats (
  id  TEXT PRIMARY KEY,
  at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_heartbeats_at ON heartbeats(at DESC);
