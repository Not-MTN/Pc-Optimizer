-- Migration 4: startup surgeon, palette, space samples, peripherals, events.

CREATE TABLE IF NOT EXISTS startup_items (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  class       TEXT NOT NULL,
  locator     TEXT NOT NULL,
  enabled     INTEGER NOT NULL DEFAULT 1,
  phase       TEXT,
  cost_ms     INTEGER,                 -- NULL means unmeasured, never zero
  seen_at     INTEGER NOT NULL,
  removed_at  INTEGER,
  relapsed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_startup_locator ON startup_items(locator);

CREATE TABLE IF NOT EXISTS palette_commands (
  id           TEXT PRIMARY KEY,
  title        TEXT NOT NULL,
  verb         TEXT NOT NULL UNIQUE,
  "group"      TEXT NOT NULL,
  executor     TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  dangerous    INTEGER NOT NULL DEFAULT 0,
  timeout_s    INTEGER NOT NULL DEFAULT 0,
  created_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS space_samples (
  id           TEXT PRIMARY KEY,
  at           INTEGER NOT NULL,
  folders_json TEXT NOT NULL,
  free_bytes   INTEGER NOT NULL,
  total_bytes  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_space_at ON space_samples(at DESC);

CREATE TABLE IF NOT EXISTS peripheral_tests (
  id          TEXT PRIMARY KEY,
  device      TEXT NOT NULL,
  kind        TEXT NOT NULL,          -- mouse|keyboard|monitor|audio|polling
  at          INTEGER NOT NULL,
  verdict     TEXT NOT NULL,
  detail_json TEXT NOT NULL
);
