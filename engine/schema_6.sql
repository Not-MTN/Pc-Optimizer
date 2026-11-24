-- Migration 6 — desktop customization: saved shell themes.
--
-- A theme is a *named set of choices* (taskbar.align = left, shell.dark_mode =
-- dark). Storing it separately from `desktop_themes` (the Studio's wallpaper and
-- widget budget) is deliberate: those cost GPU time and are governed by a 3%
-- budget; these are registry values and cost nothing. Mixing the two would let a
-- taskbar choice be "refused for being over budget", which is nonsense.

CREATE TABLE IF NOT EXISTS shell_themes (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    doc_json    TEXT NOT NULL,              -- {"taskbar.align": "left", ...}
    created_at  INTEGER NOT NULL,
    applied_at  INTEGER
);

-- Git remotes, read once per refresh. Kept in the database rather than shelled
-- out to on every prompt render: a prompt that spawns a process is a prompt that
-- stutters, and one of this project's constraints is 0% idle CPU.
CREATE TABLE IF NOT EXISTS git_remotes (
    url         TEXT PRIMARY KEY,
    seen_at     INTEGER NOT NULL
);
