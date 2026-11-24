# Safety Engine — Full Access, Zero Fear

> The product's core promise: **you will never be stuck.** Full access is fine; unrecoverable state is not. This doc specifies the six layers, the data model, and what happens when things go wrong.

## Threat model (what we're actually defending against)

| # | Threat | Likelihood | Damage | Primary defense |
|---|---|---|---|---|
| 1 | User applies a bad tweak | Medium | Low–high | Preflight simulation + auto-revert confirm |
| 2 | A tweak is fine alone, bad combined | Medium | High | Combination rules + serialize risky applies |
| 3 | Windows Update re-enables something → drift | **Very high** | Low | Drift repair (weekly hash check) |
| 4 | Crash/power loss mid-apply | Low | High | Two-phase commit + boot guard |
| 5 | A driver/AV fights us | Medium | Medium | Boot guard escalation + safe mode |
| 6 | Imported profile/command is malicious | Low | High | Signature + permission diff |
| 7 | Anti-cheat flags a kernel component | Medium | Medium–high | T2 auto-unload, matrix, badge |
| 8 | Our own bug | Medium | High | Canary rings, chaos testing, kill switch |
| 9 | User panics | Medium | None if fast | Panic key |
| 10 | Total unrecoverability | Very low | Catastrophic | Offline recovery playbook |

---

## Layer 0 — Guardrails

**Immutable protect list** (cannot be overridden, not even by T3): boot configuration (`bcdedit` values), storage and network drivers, Defender core, activation components, `System32` critical paths, the volume that hosts the OS, and our own service's startup entries (so the app can survive to fix itself).

**Combination rules** — evaluated against the *projected* state, not the current one:
```yaml
# every risky pair is declared once, centrally
- when: ["service:BITS.disabled", "update.auto.disabled"]
  verdict: allow
  warn: "Windows Update will need a manual re-enable before updates work"
- when: ["power.core_parking.disabled", "driver.t2.loaded"]
  verdict: refuse
  reason: "Stacking parking + driver DPC tuning can hang some laptops; apply one at a time"
```

**Irreversible-feeling actions** require typing the action name (not a generic "type YES") and always create a **fresh golden snapshot first**, even if the action is protected elsewhere.

---

## Layer 1 — Preflight simulation

Every tweak is declarative and side-effect-checked *before* execution:

```json
{
  "id": "svc:DiagTrack.disable",
  "tier": "T1",
  "preconditions": ["service:DiagTrack exists", "os.build >= 22621"],
  "effects": [{ "kind": "service.start_type",
                "target": "DiagTrack",
                "from": "\\current", "to": "manual" }],
  "dependents": ["app:FeedbackHub", "svc:WpnService(user)"],
  "revert": { "kind": "service.start_type", "target": "DiagTrack", "to": "\\captured" },
  "blast_radius": "low",
  "needs_reboot": false,
  "measure": { "metric": "boot.services_start_ms", "expect": "no change" }
}
```

Simulation = apply `effects` to a virtual state graph, run the same predicates, and surface:
- **conflicts** (two tweaks touching the same value),
- **dependents** that would lose functionality,
- **reboot requirements** and what needs to happen in what order,
- **predictions** (expected measurable change, from the knowledge base).

If simulation can't be confident, the action is blocked with an explanation — **"we don't know"** is a valid engine answer.

---

## Layer 2 — Journal (append-only, checksummed)

```sql
CREATE TABLE tweaks (            -- the catalog (shipped data)
  id TEXT PRIMARY KEY, tier TEXT, title TEXT, effects_json TEXT,
  revert_json TEXT, risk TEXT, needs_reboot INTEGER, knowledge_key TEXT
);

CREATE TABLE journal (           -- what actually happened (append-only)
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  tx_id TEXT NOT NULL,           -- transaction id (a session = many tweaks)
  tweak_id TEXT NOT NULL,
  at_utc TEXT NOT NULL,
  prestate_json TEXT NOT NULL,   -- exact captured values
  poststate_json TEXT,
  result TEXT,                   -- applied | verified | failed | reverted | drifted
  error TEXT,
  checksum TEXT NOT NULL,        -- over prestate+effects+at_utc (tamper-evident)
  profile TEXT, session TEXT     -- who/why: profile name, gaming session id
);

CREATE TABLE drift (             -- reverted by the outside world
  seq INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, expected TEXT,
  actual TEXT, detected_at TEXT, actor_guess TEXT,   -- e.g. "Windows Update"
  repaired_at TEXT
);

CREATE TABLE blame (             -- correlations, not accusations
  seq INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT,   -- bugcheck | hang | slow-boot
  at_utc TEXT, suspects_json TEXT, confidence REAL
);
```

- **Nothing is applied unrecorded.** Even a single registry value write goes through `journal`.
- Journal is append-only; "reverting" writes new entries (so history is never rewritten).
- Checksummed per row and chained (row hash includes previous hash) → tampering is detectable.

---

## Layer 3 — Golden snapshots (fast, small, not System Restore)

**Included:** managed registry keys (services, power, shell, policies, our own keys), service start types, scheduled task states, power scheme + hidden power settings, shell config (taskbar/Explorer/terminal aliases), driver list + versions, startup items, firewall rules we own, our profile.

**Excluded (by design):** user files, Windows components, full registry (huge), anything we never touch.

| Property | Target |
|---|---|
| Capture | ≤ 1.5 s (warm), ≤ 6 MB compressed |
| Restore | ≤ 8 s, no reboot unless a driver/service is involved |
| Retention | hourly × 24, daily × 14, weekly × 8 + named manual snapshots |
| Verification | post-restore hash check of every managed value |

Snapshots are content-addressed (dedup means 100 snapshots cost barely more than one). They support **diffing**: *"what changed between Tuesday and now?"* — the Windows time machine.

---

## Layer 4 — Boot guard (the layer that saves you when nothing else can)

```
pco-service boot
   ├─ write BOOT_STARTED marker (monotonic id + timestamp)
   ├─ if last shutdown was unexpected → session marked SUSPECT
   ├─ apply startup policy: any tweak flagged `armed` must be confirmed
   └─ heartbeat at T+45s, T+90s, T+180s  ("desktop healthy")

Recovery task (independent, protected):
   if BOOT_STARTED exists and no healthy heartbeat within 6 min:
       1. restore last golden snapshot (config-only)
       2. mark the last applied transaction as SUSPECTED, auto-suspend it
       3. re-enable our own services/tasks if they were disabled
       4. write a recovery report visible on next login
   if BOOT_STARTED exists and bugcheck was recorded:
       1. auto-suspend the last applied tweak(s) from the last hour
       2. restore snapshot only if the "armed" tweak is suspected
       3. report with a one-click "roll everything back" button
```

**Escalation ladder** (each rung is automatic, reported, and reversible):
1. Auto-revert the suspect transaction.
2. Restore the last golden snapshot.
3. Boot into **Safe Mode with our service** (config-only restore; no T2 driver).
4. Recovery console from the installer (offline registry from a backup hive, service disabling).
5. Documented manual playbook (`RECOVERY.md` shipped with the installer and printed on the recovery USB).

---

## Layer 5 — Panic key

`Ctrl + Alt + P` (global, works even when the UI is closed):
1. Stop the active session (gaming or maintenance).
2. Unload the T2 driver.
3. Restore the last golden snapshot.
4. Write a receipt with exactly what was undone.
5. Take the app out of autostart until the user re-arms it (so a crash loop can't re-break the machine).

Guarantees: panic never needs network, never needs the UI, never deletes user data, and **always completes** — it is the one code path that must have no dependencies on anything risky.

---

## Failure scenarios → automatic behavior

| Scenario | Detection | Automatic response | User sees |
|---|---|---|---|
| Crash during apply | Two-phase commit marker | Roll forward from prestate on next boot | "Last change was interrupted; finished safely" |
| Power loss during apply | Unexpected shutdown + armed tx | Roll back the transaction | Recovery report |
| BSOD after tweak | Bugcheck + journal window | Suspend suspect, restore snapshot if armed | Blame card with revert button |
| Boot > 6 min | Heartbeat timeout | Snapshot restore | Recovery report on login |
| App hangs forever | Watchdog (engine-side) | Restart helper, resume from journal | "Nim restarted; nothing lost" |
| Driver unload fails | Unload timeout | Mark driver "stuck", disable it for next boot | Warning + reboot prompt |
| Windows Update drifts tweaks | Weekly hash check | Report + offer re-apply (never silent) | Drift card with diff |
| Anti-cheat present | Process/service detect | Unload T2, disable session, badge | "T2 paused for this game" |
| Malicious import | Signature missing + permission diff | Block, show exact permissions requested | Reject screen with detail |
| Disk full during snapshot | Preflight space check | Abort before writing; keep last good | "Need 200 MB to snapshot" |
| AV quarantines our driver | Service health check | Degrade to T1 gracefully | "Some features need a driver rebuild" |
| Total failure | User calls panic | Rung 1–2 immediately | Full rollback report |

---

## Testing strategy (how we earn the promise)

1. **VM matrix**: Win11 24H2 / 25H2 / 26H2, clean + dirty, Home/Pro, HDD/SSD/NVMe, iGPU/dGPU, laptop + desktop. Every release, automated.
2. **Chaos monkey for tweaks**: a harness applies random valid tweak sequences and randomly kills the process/power mid-apply, then asserts the machine's state always matches a journal-consistent state.
3. **Revert drills**: for every shipped tweak, an automated test applies it, verifies the effect, reverts, and asserts a byte-identical prestate (or a documented exception).
4. **Canary rings**: 5% of volunteers get new tweaks first; auto-rollback on crash-rate regression.
5. **"Break it Friday"**: a monthly session where the team deliberately tries to brick a VM using only the app's UI, and the playbook is updated.

---

## Recovery playbook (shipped, printable, offline)

1. Freeze: `Ctrl+Alt+P`. If no keyboard, hard reboot.
2. At login, "PC Optimizer Recovery" runs automatically; accept the restore.
3. If it doesn't appear: run `PcoRecover.exe --last-good` from the install folder or the recovery USB.
4. Offline from another PC: the recovery USB script loads the backup hive and re-enables services.
5. Last resort: the exact commands are printed in the playbook, and every receipt is plain text you can read without us.

The playbook is also a page in the app, permanently, with the USB builder built in.
