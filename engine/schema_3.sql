-- Migration 3: gaming, installer autopsy, desktop studio, consent.
-- Same conventions as schema.sql / schema_2.sql.

CREATE TABLE IF NOT EXISTS games (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  engine        TEXT,
  exe           TEXT,
  yaw           REAL,
  notes         TEXT
);

CREATE TABLE IF NOT EXISTS game_sessions (
  id            TEXT PRIMARY KEY,
  game_id       TEXT NOT NULL,
  started_at    INTEGER NOT NULL,
  ended_at      INTEGER,
  duration_s    REAL,
  frames_json   TEXT,
  metrics_json  TEXT NOT NULL,
  bottleneck    TEXT,
  grade         TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_game ON game_sessions(game_id, started_at DESC);

CREATE TABLE IF NOT EXISTS game_profiles (
  game_id       TEXT PRIMARY KEY,
  settings_json TEXT NOT NULL,
  applied_tx    TEXT,
  updated_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS autopsies (
  id          TEXT PRIMARY KEY,
  installer   TEXT NOT NULL,
  started_at  INTEGER NOT NULL,
  ended_at    INTEGER,
  state       TEXT NOT NULL DEFAULT 'watching',   -- watching|kept|erased
  tx_id       TEXT,
  summary     TEXT
);

CREATE TABLE IF NOT EXISTS autopsy_items (
  id           TEXT PRIMARY KEY,
  autopsy_id   TEXT NOT NULL REFERENCES autopsies(id),
  class        TEXT NOT NULL,                     -- file|registry|service|task|startup
  locator      TEXT NOT NULL,
  size         INTEGER NOT NULL DEFAULT 0,
  critical     INTEGER NOT NULL DEFAULT 0,        -- never auto-erased
  action       TEXT NOT NULL DEFAULT 'pending',   -- pending|erased|kept|failed
  error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_autopsy_items ON autopsy_items(autopsy_id, class);

CREATE TABLE IF NOT EXISTS desktop_themes (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  doc_json    TEXT NOT NULL,
  cost_milli  INTEGER NOT NULL DEFAULT 0,          -- estimated GPU cost, 1/1000 %
  applied_at  INTEGER
);

CREATE TABLE IF NOT EXISTS desktop_state (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
