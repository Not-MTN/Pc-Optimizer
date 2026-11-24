"""Sens Lab — one true sensitivity, expressed in every game.

The idea: a player's aim is one physical quantity — how far the mouse travels for
a 360° turn, `cm/360` — and games merely quote it in different units. Store the
physical value, derive each game's number, and the problem "what's my Valorant
sens if I play CS2 at 1.6?" stops existing.

  cm/360 = (360 × 2.54) / (DPI × sens × yaw)

Yaw is the engine's degrees-per-count-per-sensitivity-unit. Values below are the
published community constants; they are data, so a patch can update them without
touching logic. Games with non-linear ADS/scope curves (R6, Battlefield, Fortnite
ADS) are marked `linear=False` and are excluded from exact conversion on purpose —
pretending otherwise is how converters get people killed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ids import new_id

# --------------------------------------------------------------- game constants
YAW: dict[str, float] = {
    "cs2": 0.022,          # Source engine
    "csgo": 0.022,
    "valorant": 0.07,
    "overwatch2": 0.0066,
    "cod": 0.0066,         # MW-era and later
    "fortnite": 0.005555,  # X-axis only
    "apex": 0.022,
    "tf2": 0.022,
    "quake": 0.022,
    "pubg": 0.02,
    "tarkov": 0.125,
    "rust": 0.1125,
}

# id -> (display name, engine family, exact conversion?)
GAMES: dict[str, tuple[str, str, bool]] = {
    "cs2": ("Counter-Strike 2", "Source", True),
    "csgo": ("CS:GO", "Source", True),
    "valorant": ("VALORANT", "Unreal", True),
    "overwatch2": ("Overwatch 2", "Blizzard", True),
    "cod": ("Call of Duty", "IW", True),
    "fortnite": ("Fortnite", "Unreal", True),
    "apex": ("Apex Legends", "Source", True),
    "tf2": ("Team Fortress 2", "Source", True),
    "quake": ("Quake Champions", "id Tech", True),
    "pubg": ("PUBG", "Unreal", True),
    "tarkov": ("Escape from Tarkov", "Unity", True),
    "rust": ("Rust", "Unity", True),
    "r6s": ("Rainbow Six Siege", "AnvilNext", False),
    "battlefield": ("Battlefield", "Frostbite", False),
}


@dataclass
class Profile:
    cm360: float
    dpi: int = 800
    game: str = "cs2"
    locked: bool = False
    updated_at: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"cm360": round(self.cm360, 3), "dpi": self.dpi, "game": self.game,
                "locked": self.locked, "updated_at": self.updated_at}


def cm_per_360(dpi: int, sens: float, yaw: float) -> float:
    if dpi <= 0 or sens <= 0 or yaw <= 0:
        raise ValueError("dpi, sens and yaw must all be positive")
    return (360.0 * 2.54) / (dpi * sens * yaw)


def sens_for(cm360: float, dpi: int, yaw: float) -> float:
    if cm360 <= 0 or dpi <= 0 or yaw <= 0:
        raise ValueError("cm360, dpi and yaw must all be positive")
    return (360.0 * 2.54) / (dpi * cm360 * yaw)


def edpi(dpi: int, sens: float) -> float:
    """Only meaningful *within* one game — the UI must say so."""
    return dpi * sens


def turn_space_cm(cm360: float, pad_width_cm: float, *, usable: float = 0.8) -> float:
    """How many 360° turns fit in the usable part of a mousepad."""
    return (pad_width_cm * usable) / cm360


class SensLab:
    def __init__(self, conn, clock) -> None:
        self.conn = conn
        self.clock = clock

    # ------------------------------------------------------------------ profile
    def get(self, game_id: str = "main") -> Profile:
        row = self.conn.execute(
            "SELECT cm360,dpi,locked,updated_at FROM sens_profiles WHERE game_id=?",
            (game_id,)).fetchone()
        if row is None:
            return Profile(cm360=40.0, dpi=800, game=game_id,
                           updated_at=self.clock())
        return Profile(cm360=row[0] or 40.0, dpi=row[1] or 800, game=game_id,
                       locked=bool(row[2]), updated_at=row[3])

    def save(self, *, cm360: float | None = None, dpi: int | None = None,
             game: str = "main", locked: bool | None = None) -> Profile:
        cur = self.get(game)
        prof = Profile(cm360=cm360 if cm360 is not None else cur.cm360,
                       dpi=dpi if dpi is not None else cur.dpi,
                       game=game,
                       locked=locked if locked is not None else cur.locked,
                       updated_at=self.clock())
        if prof.cm360 <= 0 or prof.dpi <= 0:
            raise ValueError("cm360 and dpi must be positive")
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO sens_profiles(game_id,cm360,dpi,locked,updated_at) "
                "VALUES(?,?,?,?,?)",
                (game, prof.cm360, prof.dpi, int(prof.locked), prof.updated_at))
        return prof

    # --------------------------------------------------------------- conversion
    def from_game(self, game: str, sens: float, dpi: int) -> Profile:
        """'I play CS2 at 1.6 / 800 DPI' → store the physical value it implies."""
        if game not in YAW:
            raise ValueError(f"no yaw constant for {game!r}")
        # "I play CS2 at 1.6" describes *the player*, not the game: it updates the
        # single shared profile that every row of the table is derived from.
        return self.save(cm360=cm_per_360(dpi, sens, YAW[game]), dpi=dpi)

    def table(self, *, dpi: int | None = None, cm360: float | None = None) -> list[dict[str, Any]]:
        prof = self.get()
        dpi = dpi or prof.dpi
        cm = cm360 or prof.cm360
        out = []
        for gid, (name, engine, linear) in GAMES.items():
            yaw = YAW.get(gid)
            if not linear or yaw is None:
                out.append({"id": gid, "name": name, "engine": engine,
                            "cm360": round(cm, 3), "sens": None,
                            "exact": False,
                            "note": "non-linear ADS — convert the hipfire value only"})
                continue
            s = sens_for(cm, dpi, yaw)
            out.append({"id": gid, "name": name, "engine": engine,
                        "cm360": round(cm, 3), "sens": round(s, 4),
                        "edpi": round(edpi(dpi, s), 2), "yaw": yaw, "exact": True})
        return out

    # ---------------------------------------------------------- PSA test helper
    def psa_start(self, *, lo: float | None = None, hi: float | None = None) -> dict[str, Any]:
        """Two concrete candidates per round; the winner's half becomes the next
        range. Six or seven rounds resolve to under a centimetre."""
        prof = self.get()
        cm = prof.cm360
        lo = lo if lo is not None else max(5.0, cm * 0.6)
        hi = hi if hi is not None else min(120.0, cm * 1.5)
        return _psa_round(lo, hi, rounds=0)

    def psa_step(self, state: dict[str, Any], better: str) -> dict[str, Any]:
        """better ∈ {'a','b'} (also accepts 'left'/'right'): which candidate won.

        Rule: the winner keeps its half of the range. That halves the search space
        every round regardless of which side wins.
        """
        mid = (float(state["lo"]) + float(state["hi"])) / 2
        if better in {"left", "a"}:
            lo, hi = float(state["lo"]), mid
        elif better in {"right", "b"}:
            lo, hi = mid, float(state["hi"])
        else:
            raise ValueError("answer must be 'a' or 'b'")
        rounds = int(state.get("rounds", 0)) + 1
        out = _psa_round(lo, hi, rounds=rounds)
        out["done"] = out["width"] < 1.0 or rounds >= 7
        if out["done"]:
            locked = round((out["lo"] + out["hi"]) / 2, 2)
            self.save(cm360=locked)
            out["locked_cm360"] = locked
            out["value"] = locked
            out["advice"] = ("Locked. Give it two weeks before changing anything — "
                             "consistency beats constant tweaking.")
        return out

    def reset(self) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM sens_profiles")


def _psa_round(lo: float, hi: float, *, rounds: int) -> dict[str, Any]:
    """Candidates sit at the middle of each half, so the winning half is exactly
    half the range: every answer halves the search space."""
    width = hi - lo
    a, b = lo + width / 4, lo + 3 * width / 4
    return {"lo": round(lo, 3), "hi": round(hi, 3), "a": round(a, 3), "b": round(b, 3),
            "value": round((a + b) / 2, 3), "rounds": rounds,
            "width": round(width, 3), "done": False,
            "statement": _statement((a + b) / 2)}


def _statement(cm360: float) -> str:
    if cm360 < 22:
        return "Very fast: flicks win, tracking suffers."
    if cm360 < 32:
        return "Fast: aggressive duellist range."
    if cm360 < 45:
        return "Balanced: covers most tactical and tracking games."
    if cm360 < 60:
        return "Controlled: precise, needs sweeping arm movement."
    return "Slow: very stable, but check your pad — a 180° may not fit."
