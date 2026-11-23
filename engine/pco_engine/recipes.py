"""Recipes — "when X happens, do Y".

Deliberately capped: trigger + conditions + up to 10 actions, no loops, no
variables. Every action is an existing engine action, so every recipe run is a
Guardian transaction with a receipt and one undo.

This is the first PC automation that can always be un-done, which is the only
reason it belongs in this product.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .ids import new_id

TRIGGERS = {
    "game_launch", "app_launch", "app_exit", "on_ac", "on_battery", "idle",
    "schedule", "disk_threshold", "temp_threshold", "network_change",
    "install_detected", "boot", "manual",
}
MAX_ACTIONS = 10


class RecipeError(ValueError):
    pass


@dataclass
class Spec:
    id: str
    name: str
    trigger: dict[str, Any]
    actions: list[dict[str, Any]]
    conditions: list[dict[str, Any]] | None = None
    enabled: bool = True


class Recipes:
    def __init__(self, conn, clock, *, run_action: Callable[[str, dict[str, Any]], Any]) -> None:
        self.conn = conn
        self.clock = clock
        self.run_action = run_action      # supplied by the service

    # ------------------------------------------------------------------ crud
    def add(self, spec: Spec) -> str:
        self._validate(spec)
        import json
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO recipes(id,name,enabled,trigger_json,actions_json,"
                "created_at) VALUES(?,?,?,?,?,?)",
                (spec.id, spec.name, int(spec.enabled), json.dumps(spec.trigger),
                 json.dumps(spec.actions), self.clock()))
        return spec.id

    def list(self) -> list[dict[str, Any]]:
        import json
        rows = self.conn.execute(
            "SELECT id,name,enabled,trigger_json,actions_json,last_run_at,run_count "
            "FROM recipes ORDER BY name").fetchall()
        return [{"id": r[0], "name": r[1], "enabled": bool(r[2]),
                 "trigger": json.loads(r[3]), "actions": json.loads(r[4]),
                 "last_run_at": r[5], "run_count": r[6]} for r in rows]

    def set_enabled(self, recipe_id: str, enabled: bool) -> None:
        with self.conn:
            self.conn.execute("UPDATE recipes SET enabled=? WHERE id=?",
                              (int(enabled), recipe_id))

    def delete(self, recipe_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM recipes WHERE id=?", (recipe_id,))

    # --------------------------------------------------------------- validate
    def _validate(self, spec: Spec) -> None:
        if not spec.id or not spec.name:
            raise RecipeError("recipe needs an id and a name")
        kind = spec.trigger.get("kind")
        if kind not in TRIGGERS:
            raise RecipeError(f"unknown trigger kind: {kind!r}")
        if not spec.actions:
            raise RecipeError("recipe has no actions")
        if len(spec.actions) > MAX_ACTIONS:
            raise RecipeError(f"recipes are capped at {MAX_ACTIONS} actions")
        for a in spec.actions:
            if "action" not in a:
                raise RecipeError(f"action entry needs an 'action' key: {a!r}")
            if a["action"] in {"recipe.run", "recipe.loop"}:
                raise RecipeError("recipes cannot call other recipes (no loops)")

    # -------------------------------------------------------------------- fire
    def fire(self, trigger_kind: str, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run every enabled recipe whose trigger matches. Returns run reports."""
        context = context or {}
        reports = []
        for r in self.list():
            if not r["enabled"] or r["trigger"].get("kind") != trigger_kind:
                continue
            if not self._matches(r["trigger"], context):
                continue
            reports.append(self._run(r, context))
        return reports

    def _matches(self, trigger: dict[str, Any], context: dict[str, Any]) -> bool:
        match = trigger.get("match") or {}
        for key, want in match.items():
            have = context.get(key)
            if isinstance(want, list):
                if have not in want:
                    return False
            elif isinstance(want, str) and isinstance(have, str):
                if want.lower() not in have.lower():
                    return False
            elif have != want:
                return False
        return True

    def _run(self, recipe: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        import json
        started = self.clock()
        log: list[dict[str, Any]] = []
        ok = True
        for action in recipe["actions"]:
            name = action["action"]
            try:
                result = self.run_action(name, {k: v for k, v in action.items() if k != "action"})
                log.append({"action": name, "ok": True, "result": result})
            except Exception as exc:  # noqa: BLE001 - a bad action must not kill the trigger
                ok = False
                log.append({"action": name, "ok": False, "error": str(exc)})
        with self.conn:
            self.conn.execute(
                "UPDATE recipes SET last_run_at=?, run_count=run_count+1 WHERE id=?",
                (started, recipe["id"]))
            self.conn.execute(
                "INSERT INTO receipts(id,at,kind,title,summary,detail_json) VALUES(?,?,?,?,?,?)",
                (new_id(), started, "recipe", f"Recipe · {recipe['name']}",
                 ("ran" if ok else "ran with errors"),
                 json.dumps({"recipe": recipe["id"], "log": log})))
        return {"recipe": recipe["id"], "name": recipe["name"], "ok": ok, "log": log}
