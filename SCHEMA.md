# SCHEMA — the full data model

> Every module, every table, every document format. This is the single source of truth for storage. Versioned, local-only, append-only where it matters, and small enough to live on any machine.

---

## 0. Principles

| # | Rule | Why |
|---|---|---|
| 1 | **One local SQLite database** (`pco.db`), WAL mode, single writer (engine only) | No server, no cloud, no locks |
| 2 | **UI never touches the DB** — it asks the engine over an ACL'd pipe | One writer, one migration path, one integrity checker |
| 3 | **Append-only where truth matters** (`journal`, `events`, `dns_events`) | History is never rewritten |
| 4 | **Checksum chain** on journal rows | Tamper-evident, exportable as an audit |
| 5 | **Content-addressed blobs** for anything big (clips, screenshots, assets, backups) — stored as files, referenced by SHA-256 | DB stays fast |
| 6 | **Typed JSON columns** for anything variable; every JSON column has a schema name + version | Migratable forever |
| 7 | **Timestamps are `INTEGER` unix ms (UTC)**, IDs are **ULIDs** (sortable, collision-free) | Sorting and joins are cheap |
| 8 | **Retention is declared per table** (§13) — never unbounded growth | Predictable size |
| 9 | **Nothing leaves the device**: no columns for accounts, emails, or remote IDs | Provable privacy |
| 10 | **Migrations are forward-only, checksummed, backed up before applying** (§14) | Upgrades can't corrupt history |

---

## 1. Core conventions

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA temp_store   = MEMORY;
PRAGMA mmap_size    = 268435456;   -- 256 MB
```

Entity kinds (used as discriminator columns): `'run'|'service'|'task'|'uwp'|'driver'|'extension'`, tiers `T0..T3`, risk `'none'|'low'|'medium'|'high'|'cosmetic'`, result `'applied'|'verified'|'failed'|'reverted'|'drifted'|'suspended'`.

---

## 2. Meta & migrations

```sql
CREATE TABLE meta (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);  -- schema_version, app_version, install_id, db_created_at, last_vacuum

CREATE TABLE migrations (
  version     INTEGER PRIMARY KEY,
  applied_at  INTEGER NOT NULL,
  checksum    TEXT NOT NULL,        -- sha256 of the migration SQL
  duration_ms INTEGER NOT NULL
);
```

---

## 3. Settings, profiles, actions

```sql
CREATE TABLE settings (
  scope      TEXT NOT NULL,          -- 'device'|'user'|'app'|'module'
  key        TEXT NOT NULL,
  value_json TEXT NOT NULL,
  schema     TEXT NOT NULL,          -- 'settings.v1'
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (scope, key)
);

CREATE TABLE profiles (
  id          TEXT PRIMARY KEY,      -- ULID
  name        TEXT NOT NULL,
  kind        TEXT NOT NULL,         -- 'config'|'gaming'|'power'|'theme'|'sens'|'recipe_set'
  version     INTEGER NOT NULL DEFAULT 1,
  parent_id   TEXT REFERENCES profiles(id),
  data_json   TEXT NOT NULL,         -- validated against a document schema
  schema      TEXT NOT NULL,
  signature   TEXT,                  -- optional Ed25519 for imported profiles
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);

CREATE TABLE actions (                -- the palette / Personal CMD catalog
  id           TEXT PRIMARY KEY,     -- 'game.start', 'clean.safe', 'user.：morning'
  module       TEXT NOT NULL,
  title        TEXT NOT NULL,
  keywords     TEXT,
  icon         TEXT,
  tier         TEXT NOT NULL,        -- T0..T3
  executor     TEXT NOT NULL,        -- 'engine'|'shell'|'app'|'web'|'script'|'recipe'
  payload_json TEXT NOT NULL,        -- 'action.v1'
  perms_json   TEXT,                 -- declared permissions (diffed before import)
  source       TEXT NOT NULL,        -- 'builtin'|'user'|'recipe'|'plugin'
  risk         TEXT NOT NULL,
  needs_confirm INTEGER NOT NULL DEFAULT 0,
  created_at   INTEGER NOT NULL
);

CREATE TABLE action_runs (
  id          TEXT PRIMARY KEY,
  action_id   TEXT NOT NULL REFERENCES actions(id),
  at          INTEGER NOT NULL,
  args_json   TEXT,
  result      TEXT NOT NULL,
  output_ref  TEXT,                  -- blob
  tx_id       TEXT,
  duration_ms INTEGER
);

CREATE TABLE hotkeys (
  key         TEXT PRIMARY KEY,      -- normalized, e.g. 'ctrl+alt+p'
  action_id   TEXT NOT NULL REFERENCES actions(id),
  scope       TEXT NOT NULL,         -- 'global'|'app'|'game'
  conflict_with TEXT
);
```

---

## 4. Guardian — the safety core

```sql
CREATE TABLE tweaks (                              -- the catalog (shipped data)
  id             TEXT PRIMARY KEY,                 -- 'display.set_refresh'
  module         TEXT NOT NULL,
  title          TEXT NOT NULL,
  plain          TEXT NOT NULL,                    -- one-line explanation
  tier           TEXT NOT NULL,
  risk           TEXT NOT NULL,
  needs_reboot   INTEGER NOT NULL DEFAULT 0,
  preconditions  TEXT,                             -- 'expr.v1'
  effects_json   TEXT NOT NULL,                    -- 'effect.v1'[]
  revert_json    TEXT,                             -- null = derive from captured prestate
  measure_json   TEXT,                             -- what to verify after applying
  blast_radius   TEXT,
  knowledge_key  TEXT,
  deprecated     INTEGER NOT NULL DEFAULT 0,
  catalog_version INTEGER NOT NULL
);

CREATE TABLE transactions (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,          -- 'single'|'batch'|'session'|'recipe'|'theme'|'profile'
  started_at    INTEGER NOT NULL,
  finished_at   INTEGER,
  state         TEXT NOT NULL,          -- 'open'|'armed'|'committed'|'reverted'|'failed'
  trigger_json  TEXT,                   -- what caused it (user, recipe, boot guard…)
  source        TEXT NOT NULL,          -- 'ui'|'palette'|'recipe'|'cli'|'bootguard'|'plugin'
  profile_id    TEXT REFERENCES profiles(id),
  armed_until   INTEGER,                -- auto-revert deadline
  parent_tx     TEXT REFERENCES transactions(id)
);

CREATE TABLE journal (                  -- APPEND-ONLY, checksum-chained
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  tx_id        TEXT NOT NULL REFERENCES transactions(id),
  tweak_id     TEXT NOT NULL,
  at           INTEGER NOT NULL,
  prestate_json   TEXT NOT NULL,
  poststate_json  TEXT,
  result       TEXT NOT NULL,
  error        TEXT,
  checksum     TEXT NOT NULL,           -- sha256(prev_checksum || seq || tx || tweak || at || pre)
  prev_checksum TEXT
);
CREATE INDEX idx_journal_at ON journal(at DESC);
CREATE INDEX idx_journal_tx ON journal(tx_id);

CREATE TABLE snapshots (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,          -- 'golden'|'auto'|'manual'|'pre_risky'
  at            INTEGER NOT NULL,
  size_bytes    INTEGER NOT NULL,
  manifest_ref  TEXT NOT NULL,          -- blob: content-addressed manifest
  parent_id     TEXT REFERENCES snapshots(id),
  note          TEXT,
  verified_at   INTEGER
);

CREATE TABLE drift (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  key          TEXT NOT NULL,
  expected_json TEXT NOT NULL,
  actual_json   TEXT NOT NULL,
  detected_at   INTEGER NOT NULL,
  actor_guess   TEXT,                   -- 'WindowsUpdate'|'Store'|'Vendor'
  repaired_at   INTEGER,
  repair_tx     TEXT
);

CREATE TABLE blame (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  event_kind   TEXT NOT NULL,           -- 'bugcheck'|'hang'|'slow_boot'|'driver_crash'
  event_at     INTEGER NOT NULL,
  suspects_json TEXT NOT NULL,          -- [{tweak, confidence, evidence}]
  confidence   REAL NOT NULL,
  narrative    TEXT NOT NULL
);

CREATE TABLE boot_events (
  id                TEXT PRIMARY KEY,
  at                INTEGER NOT NULL,
  phases_json       TEXT NOT NULL,      -- kernel/services/logon/shell/idle ms
  healthy_at_ms     INTEGER,
  boot_guard_action TEXT,               -- null|'reverted_tx'|'restored_snapshot'
  unexpected_shutdown INTEGER NOT NULL DEFAULT 0
);
```

---

## 5. Checkup, events, metrics

```sql
CREATE TABLE checks (                    -- the catalog
  id            TEXT PRIMARY KEY,        -- 'display.refresh.below_max'
  module        TEXT NOT NULL,
  category      TEXT NOT NULL,           -- 'performance'|'storage'|'security'|'privacy'|'reliability'
  title         TEXT NOT NULL,
  plain         TEXT NOT NULL,
  severity      TEXT NOT NULL,
  evidence_spec TEXT,                    -- 'evidence.v1'
  fix_tweak_id  TEXT REFERENCES tweaks(id),
  auto_safe     INTEGER NOT NULL DEFAULT 0,
  weight        REAL NOT NULL DEFAULT 1
);

CREATE TABLE check_runs (
  id           TEXT PRIMARY KEY,
  started_at   INTEGER NOT NULL,
  finished_at  INTEGER,
  trigger      TEXT NOT NULL,            -- 'user'|'weekly'|'sentinel'|'boot'
  score_speed  INTEGER, score_security INTEGER,
  score_privacy INTEGER, score_reliability INTEGER,
  score_total  INTEGER,
  checks_run   INTEGER
);

CREATE TABLE findings (
  id          TEXT PRIMARY KEY,
  run_id      TEXT NOT NULL REFERENCES check_runs(id),
  check_id    TEXT NOT NULL REFERENCES checks(id),
  category    TEXT NOT NULL,
  severity    TEXT NOT NULL,
  title       TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  impact      TEXT,
  auto_safe   INTEGER NOT NULL,
  state       TEXT NOT NULL,             -- 'new'|'review'|'planned'|'fixed'|'dismissed'|'snoozed'
  first_seen  INTEGER NOT NULL,
  last_seen   INTEGER NOT NULL,
  tx_id       TEXT,
  trend_json  TEXT                       -- small rolling series per finding
);
CREATE INDEX idx_findings_state ON findings(state, severity);

CREATE TABLE events (                    -- Sentinel / change feed (append-only)
  id         TEXT PRIMARY KEY,
  at         INTEGER NOT NULL,
  kind       TEXT NOT NULL,              -- 'install'|'uninstall'|'driver'|'autostart'|'dns'|
                                         -- 'thermal'|'crash'|'update'|'space'|'boot'|'network'
  source     TEXT NOT NULL,
  severity   TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  tx_id      TEXT,
  narrative  TEXT
);
CREATE INDEX idx_events_at ON events(at DESC);

CREATE TABLE metric_series (             -- one generic time-series table
  series     TEXT NOT NULL,              -- 'boot.ms'|'space.bytes'|'thermal.cpu.max'|'battery.pct'
  at         INTEGER NOT NULL,
  value      REAL NOT NULL,
  unit       TEXT NOT NULL,
  device_id  TEXT NOT NULL DEFAULT '',   -- '' = device-agnostic series (must be NOT NULL: it is in the PK)
  bucket     INTEGER NOT NULL DEFAULT 0, -- 0=raw, 1=minute, 2=hour, 3=day
  PRIMARY KEY (series, at, device_id, bucket)
) WITHOUT ROWID;
CREATE INDEX idx_series_at ON metric_series(series, at DESC);
```

The bucket column is the retention trick: raw samples live for hours, minute buckets for weeks, hour buckets for a year, day buckets forever. One table, four resolutions.

---

## 6. Cleaner

```sql
CREATE TABLE clean_rules (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  class       TEXT NOT NULL,             -- 'free'|'auto_regen'|'policy'
  risk        TEXT NOT NULL,
  action      TEXT NOT NULL,             -- 'delete_children'|'delete_tree'|'resize'|'dism_component'
  paths_json  TEXT NOT NULL,
  locks_json  TEXT,
  fallback    TEXT,                      -- 'schedule_reboot'|'skip'
  regen_note  TEXT,
  exempt_profiles TEXT,
  version     INTEGER NOT NULL,
  disabled    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE clean_runs (
  id           TEXT PRIMARY KEY,
  tx_id        TEXT NOT NULL REFERENCES transactions(id),
  started_at   INTEGER NOT NULL,
  finished_at  INTEGER,
  bytes_found  INTEGER NOT NULL DEFAULT 0,
  bytes_deleted INTEGER NOT NULL DEFAULT 0,
  files        INTEGER NOT NULL DEFAULT 0,
  elapsed_ms   INTEGER,
  skipped_json TEXT,
  receipt_json TEXT NOT NULL,
  rule_set_version INTEGER
);

CREATE TABLE vault_items (               -- 30-day holding box
  id            TEXT PRIMARY KEY,
  run_id        TEXT REFERENCES clean_runs(id),
  rule_id       TEXT,
  original_path TEXT NOT NULL,
  vault_path    TEXT NOT NULL,
  size          INTEGER NOT NULL,
  sha256        TEXT NOT NULL,
  deleted_at    INTEGER NOT NULL,
  expires_at    INTEGER NOT NULL,
  restored_at   INTEGER,
  verified_at   INTEGER
);
CREATE INDEX idx_vault_expiry ON vault_items(expires_at);

CREATE TABLE dup_groups (
  id          TEXT PRIMARY KEY,
  run_id      TEXT NOT NULL,
  kind        TEXT NOT NULL,             -- 'exact'|'photo'|'video'|'document'
  keeper_id   TEXT,
  bytes_saved INTEGER NOT NULL DEFAULT 0,
  state       TEXT NOT NULL              -- 'open'|'resolved'|'dismissed'
);

CREATE TABLE dup_members (
  group_id TEXT NOT NULL REFERENCES dup_groups(id),
  path     TEXT NOT NULL,
  size     INTEGER NOT NULL,
  hash     TEXT,
  phash    TEXT,
  mtime    INTEGER,
  is_keeper INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (group_id, path)
);

CREATE TABLE disk_paths (
  id       TEXT PRIMARY KEY,
  path     TEXT NOT NULL UNIQUE,
  alias    TEXT,
  excluded INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE auto_rules (                -- "downloads > 30 days → archive"
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  enabled     INTEGER NOT NULL DEFAULT 1,
  condition_json TEXT NOT NULL,          -- 'condition.v1'
  action_json    TEXT NOT NULL,
  last_run    INTEGER,
  run_count   INTEGER NOT NULL DEFAULT 0
);
```

---

## 7. Startup & boot

```sql
CREATE TABLE startup_items (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,            -- 'run'|'service'|'task'|'uwp'|'driver'|'extension'
  name         TEXT NOT NULL,
  path         TEXT,
  args         TEXT,
  signer       TEXT,
  file_hash    TEXT,
  trigger_json TEXT,
  enabled      INTEGER NOT NULL DEFAULT 1,
  defer_seconds INTEGER,
  kb_status    TEXT,                     -- 'safe_to_disable'|'leave_on'|'unknown'
  kb_breaks_json TEXT,
  measured_cost_ms INTEGER,
  last_change_at INTEGER,
  changed_by   TEXT
);

CREATE TABLE startup_insights (          -- re-add watchdog
  id      TEXT PRIMARY KEY,
  item_id TEXT NOT NULL REFERENCES startup_items(id),
  at      INTEGER NOT NULL,
  kind    TEXT NOT NULL,                 -- 'readded'|'removed'|'disabled'|'slowed'
  detail_json TEXT
);

CREATE TABLE boot_phases (
  boot_id   TEXT NOT NULL REFERENCES boot_events(id),
  phase     TEXT NOT NULL,               -- 'kernel'|'services'|'logon'|'shell'|'idle'
  start_ms  INTEGER NOT NULL,
  end_ms    INTEGER NOT NULL,
  items_json TEXT,
  PRIMARY KEY (boot_id, phase)
);
```

---

## 8. Gaming, coach, benchmarks, sensitivity

```sql
CREATE TABLE games (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  exe_hash    TEXT UNIQUE,
  install_path TEXT,
  launchers_json TEXT,
  genre       TEXT,                      -- 'tactical'|'arena'|'battle'|'other'
  anticheat   TEXT,                      -- 'none'|'vanguard'|'eac'|'battleye'|'faceit'
  cover_ref   TEXT,
  yaw         REAL,                      -- degrees per count at sens 1 (null = capture needed)
  yaw_tier    TEXT,                      -- 'verified'|'community'|'captured'
  yaw_source  TEXT,
  yaw_updated INTEGER
);

CREATE TABLE game_settings_snapshots (   -- Settings Vault
  id          TEXT PRIMARY KEY,
  game_id     TEXT NOT NULL REFERENCES games(id),
  at          INTEGER NOT NULL,
  source      TEXT NOT NULL,             -- 'ocr'|'config'|'manual'
  settings_json TEXT NOT NULL,           -- { shadows:"High", textures:"High", … }
  config_backup_ref TEXT,                -- blob (original files, zipped)
  is_baseline INTEGER NOT NULL DEFAULT 0,
  note        TEXT
);

CREATE TABLE game_sessions (
  id           TEXT PRIMARY KEY,
  game_id      TEXT NOT NULL REFERENCES games(id),
  started_at   INTEGER NOT NULL,
  ended_at     INTEGER,
  profile_id   TEXT,
  tx_id        TEXT,
  pre_bench_id TEXT,
  post_bench_id TEXT,
  end_reason   TEXT,                     -- 'user'|'crash'|'idle'|'power_loss'|'anticheat'
  aborted      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE session_buckets (           -- 10s aggregates, not per-frame rows
  session_id TEXT NOT NULL REFERENCES game_sessions(id),
  at         INTEGER NOT NULL,
  fps_p50    REAL, fps_p01 REAL, frame_ms_p95 REAL,
  gpu_busy   REAL, cpu_busy REAL, vram_mb INTEGER,
  gpu_temp   INTEGER, cpu_temp INTEGER, power_w REAL,
  dpc_us_max INTEGER, net_ms REAL,
  drops      INTEGER,
  PRIMARY KEY (session_id, at)
);

CREATE TABLE session_verdicts (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL REFERENCES game_sessions(id),
  bottleneck  TEXT NOT NULL,             -- 'gpu'|'cpu_st'|'vram'|'thermal'|'background'|'pacing'|'storage'|'network'
  evidence_json TEXT NOT NULL,
  confidence  REAL NOT NULL,
  recommendations_json TEXT
);

CREATE TABLE stutter_clips (             -- Stutter Cam
  id         TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES game_sessions(id),
  at         INTEGER NOT NULL,
  duration_ms INTEGER NOT NULL,
  video_ref  TEXT NOT NULL,              -- blob
  metrics_ref TEXT NOT NULL,
  cause_guess TEXT
);

CREATE TABLE recommendations (
  id          TEXT PRIMARY KEY,
  scope       TEXT NOT NULL,             -- 'session'|'game'|'global'
  game_id     TEXT,
  kind        TEXT NOT NULL,             -- 'windows'|'ingame'|'driver'|'power'
  target_key  TEXT NOT NULL,
  current     TEXT, suggested TEXT,
  expected_gain TEXT,                    -- '+9% fps' — must carry basis
  confidence  TEXT NOT NULL,
  basis_json  TEXT NOT NULL,
  applied_tx  TEXT,
  measured_result TEXT
);

CREATE TABLE benches (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,            -- 'cpu'|'gpu'|'disk'|'mem'|'io'|'custom'
  at           INTEGER NOT NULL,
  duration_ms  INTEGER NOT NULL,
  score        REAL,
  sub_scores_json TEXT,
  env_json     TEXT NOT NULL,            -- app version, driver, Windows build, power plan
  stable       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE bench_results (
  bench_id TEXT NOT NULL REFERENCES benches(id),
  metric   TEXT NOT NULL,
  value    REAL NOT NULL,
  unit     TEXT NOT NULL,
  n        INTEGER,
  p50      REAL, p95 REAL, stdev REAL,
  PRIMARY KEY (bench_id, metric)
);

CREATE TABLE sens_profiles (
  id        TEXT PRIMARY KEY,
  game_id   TEXT REFERENCES games(id),   -- null = universal/base profile
  cm360     REAL NOT NULL,
  dpi       INTEGER NOT NULL,
  confidence REAL,
  source    TEXT NOT NULL,               -- 'lab'|'capture'|'manual'|'import'
  genre     TEXT,
  locked    INTEGER NOT NULL DEFAULT 0,  -- sens lock: watch for silent resets
  updated_at INTEGER NOT NULL
);

CREATE TABLE sens_sessions (
  id         TEXT PRIMARY KEY,
  started_at INTEGER NOT NULL,
  ended_at   INTEGER,
  baseline_cm REAL NOT NULL,
  opts_json  TEXT,
  result_cm  REAL,
  confidence REAL,
  spread_cm  REAL
);

CREATE TABLE sens_points (
  session_id TEXT NOT NULL REFERENCES sens_sessions(id),
  cm360      REAL NOT NULL,
  err        REAL NOT NULL,
  overshoot  INTEGER, undershoot INTEGER,
  hits       INTEGER, shots INTEGER,
  track_rms  REAL,
  acq_ms_p50 REAL,
  PRIMARY KEY (session_id, cm360)
);

CREATE TABLE sens_watch (                -- gameplay analysis (opt-in)
  id         TEXT PRIMARY KEY,
  session_id TEXT REFERENCES game_sessions(id),
  game_id    TEXT,
  at         INTEGER NOT NULL,
  overshoot_pct REAL, undershoot_pct REAL,
  pad_cm     REAL, sweep_cm REAL,
  reposition_per_min REAL,
  samples    INTEGER
);

CREATE TABLE aim_progress (
  day      INTEGER NOT NULL,             -- unix day
  game_id  TEXT,
  drill    TEXT NOT NULL,
  score    REAL,
  accuracy REAL,
  note     TEXT,
  PRIMARY KEY (day, game_id, drill)
);
```

---

## 9. Hardware, drivers, apps

```sql
CREATE TABLE devices (
  id            TEXT PRIMARY KEY,
  kind          TEXT NOT NULL,           -- gpu|cpu|ram|disk|monitor|mouse|keyboard|headset|mic|controller|net
  vendor        TEXT, model TEXT, serial TEXT, firmware TEXT, bus TEXT,
  first_seen    INTEGER NOT NULL,
  warranty_until INTEGER
);

CREATE TABLE device_tests (              -- Peripheral Lab
  id          TEXT PRIMARY KEY,
  device_id   TEXT NOT NULL REFERENCES devices(id),
  kind        TEXT NOT NULL,             -- doubleclick|chatter|jitter|uniformity|mic|polling|battery
  at          INTEGER NOT NULL,
  result      TEXT NOT NULL,             -- 'pass'|'warn'|'fail'|'inconclusive'
  score       REAL,
  evidence_ref TEXT,
  detail_json TEXT
);

CREATE TABLE smart_samples (
  device_id  TEXT NOT NULL REFERENCES devices(id),
  at         INTEGER NOT NULL,
  temp_c     REAL, wear_pct REAL, power_on_hours INTEGER,
  reallocated INTEGER, spare_pct REAL, errors INTEGER,
  PRIMARY KEY (device_id, at)
);

CREATE TABLE battery_samples (
  device_id   TEXT NOT NULL REFERENCES devices(id),
  at          INTEGER NOT NULL,
  percent     INTEGER, charging INTEGER,
  capacity_mwh INTEGER, design_mwh INTEGER, cycles INTEGER,
  PRIMARY KEY (device_id, at)
);

CREATE TABLE driver_inventory (
  id          TEXT PRIMARY KEY,
  device_id   TEXT REFERENCES devices(id),
  package     TEXT NOT NULL,
  version     TEXT NOT NULL,
  date        INTEGER,
  signer      TEXT,
  installed_at INTEGER,
  source      TEXT,                      -- 'windows_update'|'vendor'|'manual'|'oem'
  score       TEXT,                      -- 'known_good'|'untested'|'problematic'
  blocklisted  INTEGER NOT NULL DEFAULT 0,
  rolled_back_at INTEGER
);

CREATE TABLE app_inventory (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  publisher   TEXT,
  version     TEXT,
  install_date INTEGER,
  source      TEXT,                      -- 'winget'|'store'|'exe'|'msi'|'portable'
  path        TEXT,
  size_bytes  INTEGER,
  update_state TEXT,                     -- 'current'|'outdated'|'eol'|'unknown'
  cve_hits_json TEXT,
  last_used   INTEGER
);
```

---

## 10. Automation

```sql
CREATE TABLE recipes (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  enabled      INTEGER NOT NULL DEFAULT 1,
  trigger_json TEXT NOT NULL,            -- 'trigger.v1'
  conditions_json TEXT,
  actions_json TEXT NOT NULL,            -- action ids + args, max 10
  from_nl      INTEGER NOT NULL DEFAULT 0,
  created_at   INTEGER NOT NULL,
  last_run_at  INTEGER,
  run_count    INTEGER NOT NULL DEFAULT 0,
  cooldown_sec INTEGER NOT NULL DEFAULT 60,
  requires_confirm INTEGER NOT NULL DEFAULT 0,
  signature    TEXT
);

CREATE TABLE recipe_runs (
  id        TEXT PRIMARY KEY,
  recipe_id TEXT NOT NULL REFERENCES recipes(id),
  at        INTEGER NOT NULL,
  result    TEXT NOT NULL,
  tx_id     TEXT,
  log_json  TEXT,
  duration_ms INTEGER
);
```

**Trigger kinds** (`trigger.v1`): `game_launch`, `app_launch`, `app_exit`, `on_ac`, `on_battery`, `idle`, `schedule`, `disk_threshold`, `temp_threshold`, `network_change`, `install_detected`, `boot`, `manual`.

---

## 11. DNS, sandbox, evidence, desktop, plugins

```sql
CREATE TABLE dns_lists (
  id      TEXT PRIMARY KEY,
  name    TEXT NOT NULL,
  grp     TEXT NOT NULL,                 -- ads|trackers|telemetry|malware|phishing|regional|adult
  version TEXT NOT NULL,
  entries INTEGER NOT NULL,
  source  TEXT,
  enabled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE dns_policy (
  scope   TEXT NOT NULL,                 -- 'global'|'app'
  target  TEXT NOT NULL DEFAULT '*',     -- '*' or app id
  mode    TEXT NOT NULL,                 -- 'off'|'on'|'strict'
  PRIMARY KEY (scope, target)
);

CREATE TABLE dns_events (                -- append-only, aggregated daily
  id     TEXT PRIMARY KEY,
  at     INTEGER NOT NULL,
  domain TEXT NOT NULL,
  app_id TEXT,
  action TEXT NOT NULL,                  -- 'allowed'|'blocked'|'paused'
  list_id TEXT
);
CREATE INDEX idx_dns_at ON dns_events(at DESC);

CREATE TABLE dns_pauses (
  id         TEXT PRIMARY KEY,
  started_at INTEGER NOT NULL,
  ends_at    INTEGER NOT NULL,
  scope      TEXT NOT NULL,              -- 'global' or app id
  reason     TEXT
);

CREATE TABLE dns_breakages (
  id         TEXT PRIMARY KEY,
  at         INTEGER NOT NULL,
  app_id     TEXT,
  symptom    TEXT,
  candidates_json TEXT,
  resolution TEXT                        -- 'allow'|'pause'|'ignored'
);

CREATE TABLE sandbox_sessions (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,             -- 'autopsy'|'windows_sandbox'|'appcontainer'|'vprofile'|'wfp_net'
  target_path TEXT NOT NULL,
  started_at  INTEGER NOT NULL,
  ended_at    INTEGER,
  exit_code   INTEGER,
  verdict     TEXT,                      -- 'kept'|'erased'|'partial'
  blind_spots TEXT,
  report_ref  TEXT
);

CREATE TABLE sandbox_events (
  session_id TEXT NOT NULL REFERENCES sandbox_sessions(id),
  at         INTEGER NOT NULL,
  category   TEXT NOT NULL,              -- file|registry|service|task|driver|net|process
  detail_json TEXT NOT NULL,
  PRIMARY KEY (session_id, at, category)
);

CREATE TABLE evidence (                  -- Evidence Locker
  id         TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,              -- receipt|report|clip|screenshot|diff|bench|audit
  module     TEXT NOT NULL,
  title      TEXT NOT NULL,
  at         INTEGER NOT NULL,
  blob_ref   TEXT NOT NULL,
  sha256     TEXT NOT NULL,
  redaction  TEXT NOT NULL DEFAULT 'none',
  tx_id      TEXT
);
CREATE INDEX idx_evidence_at ON evidence(at DESC);

CREATE TABLE reports (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,            -- weekly|health_pdf|incident|warranty|space
  generated_at INTEGER NOT NULL,
  period_start INTEGER, period_end INTEGER,
  file_ref     TEXT NOT NULL,
  summary_json TEXT
);

CREATE TABLE themes (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  version     INTEGER NOT NULL DEFAULT 1,
  parent_id   TEXT REFERENCES themes(id),
  doc_ref     TEXT NOT NULL,             -- blob: theme.yaml
  cost_profile_json TEXT,                -- measured: gpu%, cpu%
  tier_parts_json TEXT,                  -- which parts are A/B/C tier (§DESKTOP)
  verified_at INTEGER,
  created_at  INTEGER NOT NULL
);

CREATE TABLE theme_assets (
  theme_id TEXT NOT NULL REFERENCES themes(id),
  kind     TEXT NOT NULL,                -- icon|wallpaper|shader|sound|cursor|font
  name     TEXT NOT NULL,
  blob_ref TEXT NOT NULL,
  meta_json TEXT,
  PRIMARY KEY (theme_id, kind, name)
);

CREATE TABLE widget_instances (
  id          TEXT PRIMARY KEY,
  theme_id    TEXT NOT NULL REFERENCES themes(id),
  type        TEXT NOT NULL,             -- analog_clock|cpu_gauge|now_playing|…
  monitor     INTEGER NOT NULL DEFAULT 0,
  x REAL, y REAL, w REAL, h REAL,
  settings_json TEXT,
  refresh_ms  INTEGER NOT NULL DEFAULT 1000
);

CREATE TABLE terminal_sessions (
  id         TEXT PRIMARY KEY,
  shell      TEXT NOT NULL,
  cwd        TEXT,
  tabs_json  TEXT NOT NULL,
  scrollback_ref TEXT,
  at         INTEGER NOT NULL
);

CREATE TABLE snippets (
  id      TEXT PRIMARY KEY,
  title   TEXT NOT NULL,
  body    TEXT NOT NULL,
  shell   TEXT,
  tags    TEXT,
  created_at INTEGER NOT NULL
);

CREATE TABLE plugins (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  version      TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  capabilities_json TEXT NOT NULL,
  signature    TEXT,
  enabled      INTEGER NOT NULL DEFAULT 0,
  sandbox      TEXT NOT NULL DEFAULT 'wasm'
);

CREATE TABLE support_bundles (
  id         TEXT PRIMARY KEY,
  at         INTEGER NOT NULL,
  file_ref   TEXT NOT NULL,
  includes_json TEXT NOT NULL,
  redaction_report TEXT NOT NULL
);
```

---

## 12. Document formats (files, not tables)

### 12.1 Tweak / effect (`effect.v1`)

```json
{
  "id": "display.set_refresh",
  "module": "display",
  "tier": "T1",
  "risk": "low",
  "needs_reboot": false,
  "preconditions": [
    { "kind": "exists", "what": "monitor:primary" },
    { "kind": "gte", "what": "monitor:primary.supported_hz", "value": 165 }
  ],
  "effects": [
    { "kind": "display_mode", "target": "primary", "from": "$capture", "to": { "hz": 165 } }
  ],
  "revert": { "kind": "display_mode", "target": "primary", "to": "$prestate" },
  "measure": { "metric": "display.hz", "expect": 165, "tolerance": 0.5 },
  "blast_radius": "low"
}
```

**Effect kinds** (v1): `registry_value`, `service_start_type`, `scheduled_task_state`, `file_write`, `file_delete`, `dns_policy`, `power_setting`, `display_mode`, `driver_action`, `app_setting`, `theme_apply`, `sens_set`, `recipe_run`, `script`.

### 12.2 Recipe (`recipe.v1`)

```yaml
id: game.rocket.auto
name: Rocket League — auto session
enabled: true
trigger:
  kind: game_launch
  match: { exe: "RocketLeague.exe" }
conditions:
  - { kind: on_ac_power }
  - { kind: not, of: { kind: process_running, name: "OBS64.exe" } }
actions:
  - { action: game.start, args: { profile: rocket-league, measure: true } }
  - { action: dns.set, args: { mode: on, scope: game } }
  - { action: wallpaper.pause }
cooldown_sec: 300
requires_confirm: false
limits: { max_actions: 10, no_loops: true, no_variables: true }
```

### 12.3 Theme (`.pco` zip)

```
theme.yaml            # the document from DESKTOP.md
assets/icons/*.svg
assets/wallpaper/*
assets/sounds/*
manifest.json         # { name, version, tier_parts, hashes[] }
```

### 12.4 Profile (`.pcoprofile`)

```yaml
meta: { name: "MTN dev rig", version: 3, created: 2026-11-23, signature: "ed25519:…" }
sections: [appearance, taskbar, explorer, terminal, shortcuts, sounds]
overrides: { … }
requires: { min_app_version: "1.0", tiers: ["T1"] }
```

### 12.5 Plugin manifest

```json
{
  "id": "com.you.widget.tides",
  "name": "Tides",
  "version": "1.0.0",
  "abi": "wasm32-pco-1",
  "capabilities": ["widget.render", "settings.read:widget", "net.fetch:api.example.com"],
  "entry": "main.wasm",
  "signature": "ed25519:…"
}
```

Capabilities are declared, diffed and shown before enabling; a plugin can never request `T2`/`T3`.

### 12.6 Evidence bundle / health report

```
report.json        # summary, findings, receipts, measurements, evidence index
report.pdf         # rendered, printable
evidence/          # content-addressed copies referenced by sha256
manifest.json      # { generated_at, app_version, redaction: "paths|serials|both|none" }
```

---

## 13. Indexes, retention, size budget

| Table | Retention | Why |
|---|---|---|
| `journal`, `transactions` | **forever** (append-only) | the audit trail is the product |
| `events` | 180 days (roll-ups kept) | change feed stays useful, not huge |
| `metric_series` raw | 6 hours | live graphs only |
| `metric_series` minute | 30 days | trends |
| `metric_series` hour | 2 years | long-term health |
| `metric_series` day | forever | "your PC over a year" |
| `session_buckets` | 90 days | per-game history |
| `stutter_clips` | 30 days (pinned never expires) | clips are big |
| `dns_events` | 7 days raw, 90 days aggregated | privacy + size |
| `evidence` | forever unless user deletes | the locker |
| `vault_items` | 30 days | undo window |
| `benches` | forever (tiny) | baseline history |
| `findings` | 1 year | trends |
| `app_inventory`, `driver_inventory` | forever | lifecycle truth |
| `plugins`, `themes` | forever | user property |

**Size budget after one year of heavy use:** DB ≈ **180–320 MB**; blobs (clips, screenshots, assets, backups) dominate and are user-capped (default 4 GB with a visible counter and one-click prune). Vacuum on idle only.

---

## 14. Migrations, integrity, encryption

- `PRAGMA user_version` + `migrations` table; every migration has a sha256.
- **Backup before migrate** (`pco.db.bak-<version>`), applied inside a single transaction, with a rollback path for the last 3 versions.
- Daily `PRAGMA quick_check` on idle; weekly full `integrity_check`.
- Journal chain verified on every boot; a broken chain is *reported*, never silently repaired.
- **Optional encryption at rest:** SQLCipher, key wrapped with DPAPI (per-user) + optional TPM; evidence blobs encrypted with the same key. Off by default, one switch.
- **Export everything** = a folder of plain JSON/CSV + the blobs. No lock-in, ever.

---

## 15. Privacy guarantees encoded in the schema

| Guarantee | How the schema enforces it |
|---|---|
| No accounts, no emails | there is no such column anywhere |
| No keystrokes | only user-created `snippets` are stored |
| No clipboard history | never captured; `{clipboard}` is read at run time, not stored |
| Window titles | opt-in only, stored in `events.payload_json` with 30-day retention |
| DNS | domain + app + allow/block only; never URLs or page content |
| Games | no account names, no matchmaking IDs, no game memory |
| Support bundle | generated locally, redaction report attached, sent only by the user |

---

## 16. Instance map (where each table lives)

| Process | Reads/writes |
|---|---|
| `pco-service` (SYSTEM) | **all writes**; owns the DB; exposes typed queries |
| `pco-ui` (user) | none — queries only via IPC |
| `pco-bench` (isolated) | writes only `benches`/`bench_results`, via the service |
| `pco-cli` | queries + action dispatch via the service |
| `pco-plugin-host` | sandboxed; no DB access, only declared capability calls |
