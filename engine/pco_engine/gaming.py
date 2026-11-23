"""Gaming — watch the frame times, then say the one useful thing.

Most "game boosters" lie: they close background apps and claim a win the player
cannot measure. This module does the opposite. It reads *actual gameplay* frame
times, computes what the player feels (1% low, stutter count), and names the
bottleneck that would actually move those numbers. If nothing would move them,
it says so.

The classifier is a decision tree over utilisation evidence, and every branch is
tested. On Windows the readings come from PresentMon + ETW + PDH; here they come
from a pluggable `Capture` so the logic is provable on any OS.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from .ids import new_id

# --------------------------------------------------------------------- catalog
@dataclass(frozen=True)
class Game:
    id: str
    name: str
    engine: str
    exe: str
    yaw: float | None = None
    notes: str = ""


GAMES: dict[str, Game] = {
    g.id: g for g in [
        Game("cs2", "Counter-Strike 2", "Source 2", "cs2.exe", 0.022),
        Game("valorant", "VALORANT", "Unreal", "VALORANT-Win64-Shipping.exe", 0.07),
        Game("apex", "Apex Legends", "Source", "r5apex.exe", 0.022),
        Game("fortnite", "Fortnite", "Unreal", "FortniteClient-Win64-Shipping.exe", 0.005555,
             notes="ADS is non-linear; only hipfire converts exactly."),
        Game("overwatch2", "Overwatch 2", "Blizzard", "Overwatch.exe", 0.0066),
        Game("cod", "Call of Duty", "IW", "cod.exe", 0.0066),
        Game("pubg", "PUBG", "Unreal", "TslGame.exe", 0.02),
        Game("tarkov", "Escape from Tarkov", "Unity", "EscapeFromTarkov.exe", 0.125),
        Game("rust", "Rust", "Unity", "RustClient.exe", 0.1125),
        Game("dota2", "Dota 2", "Source 2", "dota2.exe", 0.022),
        Game("minecraft", "Minecraft", "Java", "javaw.exe", None,
             notes="Sensitivity is a percent slider, not a multiplier."),
        Game("gta5", "GTA V", "RAGE", "GTA5.exe", None),
    ]
}

# --------------------------------------------------------------- frame metrics
@dataclass
class FrameStats:
    frames: int
    fps_avg: float
    fps_p1: float           # 1% low — what the player feels in a fight
    ft_median_ms: float
    ft_p99_ms: float
    stutter_pct: float      # share of frames slower than 2× the median
    consistency: float      # 0..100

    def as_dict(self) -> dict[str, Any]:
        return {
            "frames": self.frames, "fps_avg": round(self.fps_avg, 1),
            "fps_p1": round(self.fps_p1, 1), "ft_median_ms": round(self.ft_median_ms, 2),
            "ft_p99_ms": round(self.ft_p99_ms, 2), "stutter_pct": round(self.stutter_pct, 2),
            "consistency": round(self.consistency, 1),
        }


def analyse_frames(frame_ms: Sequence[float]) -> FrameStats:
    """Frame times → what the player actually experiences.

    Average FPS is the least interesting number on the screen. A 200 fps average
    with a 40 fps 1% low feels worse than a flat 90, and this function is written
    so the UI can say exactly that.
    """
    xs = [x for x in frame_ms if x > 0]
    if len(xs) < 10:
        raise ValueError("need at least 10 frames to say anything honest")
    ordered = sorted(xs)
    median = statistics.median(ordered)
    p99 = _percentile(ordered, 0.99)
    # 1% low: the average fps of the slowest 1% of frames
    slow_n = max(1, len(ordered) // 100)
    slow = ordered[-slow_n:]
    fps_p1 = 1000.0 / statistics.fmean(slow)
    fps_avg = 1000.0 / statistics.fmean(ordered)
    stutters = sum(1 for x in ordered if x > 2 * median)
    # consistency: how tight the distribution is around the median (IQR based)
    q1 = _percentile(ordered, 0.25)
    q3 = _percentile(ordered, 0.75)
    spread = (q3 - q1) / median
    consistency = max(0.0, min(100.0, 100.0 * (1.0 - spread)))
    return FrameStats(frames=len(xs), fps_avg=fps_avg, fps_p1=fps_p1,
                      ft_median_ms=median, ft_p99_ms=p99,
                      stutter_pct=100.0 * stutters / len(ordered),
                      consistency=consistency)


def _percentile(sorted_xs: Sequence[float], q: float) -> float:
    if not sorted_xs:
        raise ValueError("empty")
    i = min(len(sorted_xs) - 1, max(0, int(round(q * (len(sorted_xs) - 1)))))
    return float(sorted_xs[i])


# ----------------------------------------------------------------- bottleneck
@dataclass
class Readings:
    """One sample of system evidence during gameplay."""
    gpu_util: float = 0.0           # %
    cpu_util: float = 0.0           # overall %
    cpu_busiest_thread: float = 0.0  # %
    vram_used_mb: int = 0
    vram_total_mb: int = 0
    ram_used_mb: int = 0
    ram_total_mb: int = 0
    gpu_temp_c: float | None = None
    cpu_temp_c: float | None = None
    disk_queue: float = 0.0
    network_ms: float | None = None
    pagefile_hits: int = 0

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Bottleneck:
    key: str                  # gpu|cpu|vram|ram|thermal|storage|network|balanced
    confidence: float         # 0..1
    headline: str
    evidence: dict[str, Any]
    advice: list[str] = field(default_factory=list)
    tweaks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "confidence": round(self.confidence, 2),
                "headline": self.headline, "evidence": self.evidence,
                "advice": self.advice, "tweaks": self.tweaks}


def classify(r: Readings) -> Bottleneck:
    """Name the limiter, or admit there isn't one.

    Order matters: a thermal limit masquerades as a GPU limit, and VRAM
    exhaustion masquerades as a CPU limit (the driver thrashes). Both are checked
    before the naive utilisation comparison.
    """
    vram_pct = (100.0 * r.vram_used_mb / r.vram_total_mb) if r.vram_total_mb else 0.0
    ram_pct = (100.0 * r.ram_used_mb / r.ram_total_mb) if r.ram_total_mb else 0.0

    if (r.gpu_temp_c is not None and r.gpu_temp_c >= 84) or \
       (r.cpu_temp_c is not None and r.cpu_temp_c >= 95):
        hot = "GPU" if (r.gpu_temp_c or 0) >= 84 else "CPU"
        temp = r.gpu_temp_c if hot == "GPU" else r.cpu_temp_c
        return Bottleneck(
            "thermal", 0.85,
            f"Your {hot} is thermally limiting at {temp:.0f} °C.",
            {"gpu_temp_c": r.gpu_temp_c, "cpu_temp_c": r.cpu_temp_c},
            [f"Fix {hot} cooling before buying anything: dust, fan curve, or repaste.",
             "A cooler running at the same clocks is free performance.",
             "Check that the case has positive pressure and the intake is not blocked."])

    if vram_pct >= 97:
        return Bottleneck(
            "vram", 0.9,
            f"VRAM is exhausted ({r.vram_used_mb} of {r.vram_total_mb} MB).",
            {"vram_pct": round(vram_pct, 1)},
            ["Drop texture quality one step — it is the only setting that frees VRAM.",
             "Disable ray tracing: it costs VRAM before it costs GPU time.",
             "Close the browser: hardware-accelerated video keeps VRAM resident."],
            ["gaming.profile.textures.down"])

    if ram_pct >= 92:
        return Bottleneck(
            "ram", 0.8,
            f"RAM pressure is starving the game ({ram_pct:.0f}% used).",
            {"ram_pct": round(ram_pct, 1), "pagefile_hits": r.pagefile_hits},
            ["Close the top three memory consumers before launching.",
             "A pagefile on an SSD is fine; a pagefile on a full HDD is not.",
             "16 GB is the floor for modern titles, 32 GB is comfortable."])

    if r.disk_queue >= 40:
        return Bottleneck(
            "storage", 0.7,
            "The disk is queueing hard — assets are arriving late.",
            {"disk_queue": r.disk_queue},
            ["Make sure the game is on the NVMe, not a spinning disk or a USB drive.",
             "Keep at least 15% of the SSD free or writes slow down sharply.",
             "Run a drive health check: a failing SSD stutters before it dies."])

    if r.gpu_util >= 95 and r.cpu_busiest_thread < 85:
        return Bottleneck(
            "gpu", 0.9,
            f"The GPU is saturated at {r.gpu_util:.0f}% — this is a graphics limit.",
            {"gpu_util": r.gpu_util, "cpu_busiest_thread": r.cpu_busiest_thread},
            ["You are already using the GPU fully. Settings changes trade pixels for frames.",
             "Upscaling (DLSS/FSR/XeSS) is the largest single lever at this limit.",
             "Shadows and reflections are the cheapest 20% of frames to reclaim.",
             "A faster GPU is the only thing that raises the ceiling."],
            ["gaming.profile.upscaling.on"])

    if r.cpu_busiest_thread >= 90 and r.gpu_util <= 80:
        return Bottleneck(
            "cpu", 0.85,
            f"One CPU thread is pegged at {r.cpu_busiest_thread:.0f}% while the GPU idles at {r.gpu_util:.0f}%.",
            {"cpu_busiest_thread": r.cpu_busiest_thread, "gpu_util": r.gpu_util},
            ["This is a simulation limit: it is usually draw calls, not cores.",
             "Lower settings that scale with draws: view distance, crowd/character density, shadows.",
             "Enable Resizable BAR and check the game's own CPU-heavy options.",
             "Closing background apps can genuinely help here — scheduler contention is real."],
            ["power.scheme.high", "startup.defer.onedrive"])

    return Bottleneck(
        "balanced", 0.5,
        "No single component is saturated. The game is running as intended.",
        {"gpu_util": r.gpu_util, "cpu_busiest_thread": r.cpu_busiest_thread},
        ["Nothing to fix. Optimisers that promise more here are selling noise.",
         "If it feels bad anyway, the problem is frame pacing, not average frame rate."])


# ------------------------------------------------------------------- profiles
@dataclass
class Setting:
    key: str
    label: str
    value: str
    why: str
    fps_gain_pct: float = 0.0
    quality_cost: str = "low"


def suggest_settings(game_id: str, bottleneck: Bottleneck) -> list[Setting]:
    """Settings that address *this* bottleneck, ordered by frames per unit of ugly."""
    g = GAMES.get(game_id)
    base: list[Setting] = []
    if bottleneck.key == "gpu":
        base = [
            Setting("upscaling", "Upscaling", "Quality", "Largest lever when the GPU is saturated", 22),
            Setting("shadows", "Shadow quality", "Medium", "Cheapest large win at a GPU limit", 8, "medium"),
            Setting("reflections", "Screen-space reflections", "Off", "Very expensive, subtly visible", 7, "medium"),
            Setting("volumetrics", "Volumetric fog", "Low", "Often invisible during play", 6, "low"),
            Setting("anti_aliasing", "Anti-aliasing", "TAA (not MSAA)", "MSAA is 4× the cost for a similar result", 5),
        ]
    elif bottleneck.key == "cpu":
        base = [
            Setting("view_distance", "View distance", "Medium", "Draw calls are the limit here", 12, "medium"),
            Setting("crowd_density", "Character/crowd density", "Medium", "Directly scales simulation cost", 9, "medium"),
            Setting("shadows", "Shadow quality", "Low", "Also costs CPU draws, not just GPU", 6),
            Setting("ray_tracing", "Ray tracing", "Off", "Hits the CPU too, not only the GPU", 10, "high"),
            Setting("physics", "Physics detail", "Low", "Runs on the main thread", 4),
        ]
    elif bottleneck.key == "vram":
        base = [
            Setting("textures", "Texture quality", "High (not Ultra)", "The only setting that frees VRAM", 0, "medium"),
            Setting("ray_tracing", "Ray tracing", "Off", "Costs VRAM before GPU time", 10, "high"),
            Setting("streaming", "Texture streaming", "On", "Keeps VRAM under the cliff", 0),
        ]
    elif bottleneck.key == "thermal":
        base = [
            Setting("frame_cap", "Frame cap", "3 below refresh", "Less heat, more consistent clocks", 0),
            Setting("power_limit", "GPU power limit", "-10%", "Loses ~3% frames, drops 8–12 °C", -3),
        ]
    else:
        base = [
            Setting("frame_cap", "Frame cap", "At refresh rate", "Consistency beats headroom", 0),
            Setting("vsync", "V-Sync", "Off + frame cap", "Removes tearing without the input lag", 0),
        ]
    if g and g.id in {"cs2", "valorant", "apex", "overwatch2"}:
        base.append(Setting("nvidia_reflex", "Low-latency mode", "On + Boost",
                            "Cuts the input-to-photon pipeline", 0))
    return base


# -------------------------------------------------------------------- capture
Capture = Callable[[str, float], tuple[list[float], Readings]]


def simulated_capture(game_id: str, seconds: float = 45.0) -> tuple[list[float], Readings]:
    """A believable gameplay sample, so the whole pipeline is demonstrable.

    Deterministic per game id: the same game always yields the same session in the
    demo, which is what makes screenshots and tests reproducible.
    """
    rng = random.Random(f"pco:{game_id}")
    target_fps = {"cs2": 240, "valorant": 300, "apex": 144, "fortnite": 120,
                  "overwatch2": 200, "cod": 130, "pubg": 110, "tarkov": 75,
                  "rust": 90, "dota2": 180, "minecraft": 240, "gta5": 95}.get(game_id, 120)
    n = int(seconds * target_fps)
    frames = []
    base_ms = 1000.0 / target_fps
    for i in range(n):
        jitter = rng.gauss(0, base_ms * 0.05)
        spike = rng.random() < 0.012          # occasional shader/streaming hitch
        ms = base_ms + jitter + (rng.uniform(4, 18) * base_ms / 10 if spike else 0)
        frames.append(max(0.5, ms))
    if game_id in {"tarkov", "gta5"}:        # these titles stutter for the demo's sake
        for i in range(0, n, max(1, n // 12)):
            frames[i] *= rng.uniform(2.2, 3.6)

    branch = rng.random()
    if game_id in {"tarkov", "gta5"}:
        readings = Readings(gpu_util=72, cpu_util=48, cpu_busiest_thread=94,
                            vram_used_mb=7600, vram_total_mb=8192, ram_used_mb=14200,
                            ram_total_mb=16384, gpu_temp_c=71, cpu_temp_c=68, disk_queue=22)
    elif game_id in {"cs2", "valorant", "dota2"}:
        readings = Readings(gpu_util=99, cpu_util=42, cpu_busiest_thread=71,
                            vram_used_mb=4100, vram_total_mb=8192, ram_used_mb=9800,
                            ram_total_mb=32768, gpu_temp_c=68, cpu_temp_c=62, disk_queue=4)
    elif game_id in {"fortnite", "cod"}:
        readings = Readings(gpu_util=99, cpu_util=55, cpu_busiest_thread=88,
                            vram_used_mb=8100, vram_total_mb=8192, ram_used_mb=15600,
                            ram_total_mb=16384, gpu_temp_c=76, cpu_temp_c=74, disk_queue=11)
    elif game_id == "minecraft":
        readings = Readings(gpu_util=61, cpu_util=52, cpu_busiest_thread=97,
                            vram_used_mb=2600, vram_total_mb=8192, ram_used_mb=11200,
                            ram_total_mb=16384, gpu_temp_c=63, cpu_temp_c=70, disk_queue=3)
    else:
        readings = Readings(gpu_util=87, cpu_util=51, cpu_busiest_thread=78,
                            vram_used_mb=5200, vram_total_mb=8192, ram_used_mb=12400,
                            ram_total_mb=16384, gpu_temp_c=72, cpu_temp_c=69, disk_queue=8)
    return frames, readings


def grade(stats: FrameStats) -> str:
    """One letter, and the user can ask why."""
    if stats.fps_p1 >= 120 and stats.stutter_pct < 0.5:
        return "S"
    if stats.fps_p1 >= 90 and stats.stutter_pct < 1.0:
        return "A"
    if stats.fps_p1 >= 60 and stats.stutter_pct < 2.0:
        return "B"
    if stats.fps_p1 >= 45:
        return "C"
    return "D"


# --------------------------------------------------------------------- service
class Gaming:
    def __init__(self, conn, clock, *, capture: Capture | None = None) -> None:
        self.conn = conn
        self.clock = clock
        self._capture = capture
        self._seed()

    def _seed(self) -> None:
        with self.conn:
            for g in GAMES.values():
                self.conn.execute(
                    "INSERT OR REPLACE INTO games(id,name,engine,exe,yaw,notes) "
                    "VALUES(?,?,?,?,?,?)",
                    (g.id, g.name, g.engine, g.exe, g.yaw, g.notes))

    # ------------------------------------------------------------------ library
    def games(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id,name,engine,exe,yaw,notes FROM games ORDER BY name").fetchall()
        played = {r[0]: r[1] for r in self.conn.execute(
            "SELECT game_id, COUNT(*) FROM game_sessions GROUP BY game_id").fetchall()}
        return [{**dict(r), "sessions": played.get(r["id"], 0)} for r in rows]

    def get_game(self, game_id: str) -> Game:
        g = GAMES.get(game_id)
        if g is None:
            raise KeyError(f"unknown game {game_id!r}")
        return g

    # ------------------------------------------------------------------ session
    def capture(self, game_id: str, *, seconds: float = 45.0) -> dict[str, Any]:
        """Watch gameplay and return a *receipt* for what was observed."""
        self.get_game(game_id)
        capture = self._capture or simulated_capture
        frames, readings = capture(game_id, seconds)
        stats = analyse_frames(frames)
        bottleneck = classify(readings)
        grade_letter = grade(stats)
        session_id = new_id()
        started = self.clock()
        with self.conn:
            self.conn.execute(
                "INSERT INTO game_sessions(id,game_id,started_at,ended_at,duration_s,"
                "frames_json,metrics_json,bottleneck,grade) VALUES(?,?,?,?,?,?,?,?,?)",
                (session_id, game_id, started, started + int(seconds * 1000), seconds,
                 ",".join(f"{x:.3f}" for x in frames[:20000]),
                 _json({"stats": stats.as_dict(), "readings": readings.as_dict(),
                        "bottleneck": bottleneck.as_dict()}),
                 bottleneck.key, grade_letter))
        return {"session_id": session_id, "game_id": game_id,
                "game": self.get_game(game_id).name,
                "stats": stats.as_dict(), "readings": readings.as_dict(),
                "bottleneck": bottleneck.as_dict(), "grade": grade_letter,
                "seconds": seconds,
                "verdict": self._verdict(stats, bottleneck)}

    @staticmethod
    def _verdict(stats: FrameStats, b: Bottleneck) -> str:
        if stats.stutter_pct >= 2.0:
            return (f"{stats.fps_avg:.0f} fps average, but {stats.stutter_pct:.1f}% of frames "
                    f"are stutters — that is what you are feeling, not the average.")
        if stats.fps_p1 < stats.fps_avg * 0.6:
            return (f"The 1% low is {stats.fps_p1:.0f} against a {stats.fps_avg:.0f} average: "
                    f"frame pacing is uneven.")
        return f"Smooth: {stats.fps_avg:.0f} fps average, {stats.fps_p1:.0f} fps 1% low."

    def sessions(self, limit: int = 20, game_id: str | None = None) -> list[dict[str, Any]]:
        if game_id:
            rows = self.conn.execute(
                "SELECT id,game_id,started_at,duration_s,metrics_json,bottleneck,grade "
                "FROM game_sessions WHERE game_id=? ORDER BY started_at DESC LIMIT ?",
                (game_id, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id,game_id,started_at,duration_s,metrics_json,bottleneck,grade "
                "FROM game_sessions ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            m = _load(r[4])
            out.append({"id": r[0], "game_id": r[1], "at": r[2],
                        "duration_s": r[3], "stats": m.get("stats", {}),
                        "bottleneck": r[5], "grade": r[6],
                        "game": GAMES[r[1]].name if r[1] in GAMES else r[1]})
        return out

    # ------------------------------------------------------------------ suggest
    def plan(self, game_id: str, session_id: str | None = None) -> dict[str, Any]:
        """Turn a session into settings advice + concrete catalog tweaks."""
        if session_id:
            row = self.conn.execute(
                "SELECT metrics_json FROM game_sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown session {session_id}")
            metrics = _load(row[0])
            bottleneck = Bottleneck(**{k: metrics["bottleneck"][k] for k in
                                       ("key", "confidence", "headline", "evidence",
                                        "advice", "tweaks")})
        else:
            frames, readings = (self._capture or simulated_capture)(game_id, 45.0)
            bottleneck = classify(readings)
        settings = suggest_settings(game_id, bottleneck)
        return {"game_id": game_id, "game": self.get_game(game_id).name,
                "bottleneck": bottleneck.as_dict(),
                "settings": [s.__dict__ for s in settings],
                "tweaks": bottleneck.tweaks,
                "expected": (f"up to {max([s.fps_gain_pct for s in settings] or [0]):.0f}% "
                             f"from the single largest change")}

    # ------------------------------------------------------------------ profile
    def save_profile(self, game_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        with self.conn:
            self.conn.execute(
                "INSERT INTO game_profiles(game_id,settings_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(game_id) DO UPDATE SET settings_json=excluded.settings_json, "
                "updated_at=excluded.updated_at",
                (game_id, _json(settings), self.clock()))
        return {"game_id": game_id, "settings": settings}

    def profile(self, game_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT settings_json,updated_at FROM game_profiles WHERE game_id=?",
            (game_id,)).fetchone()
        if row is None:
            return None
        return {"game_id": game_id, "settings": _load(row[0]), "updated_at": row[1]}

    def export_profile(self, game_id: str) -> dict[str, Any]:
        """A portable document, no account, no server — the privacy rule forbids
        an upload, so sharing is a file the user moves themselves."""
        prof = self.profile(game_id) or {"game_id": game_id, "settings": {}}
        return {"format": "pco.gameprofile.v1", "created_at": self.clock(), **prof}


def _json(value: Any) -> str:
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _load(text: str) -> Any:
    import json
    return json.loads(text)
