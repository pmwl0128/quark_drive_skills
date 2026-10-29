"""Adapt the browser's input-only roster/records to a portable calculator case.

Browser storage has no meta snapshot. The caller must supply the prior explicitly;
importing a record never claims that today's prior was used for an older result.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from inference_case import CaseInputError, _field, _name, _object, _only, init_case, load_case

DEX = Path(__file__).resolve().parents[2] / "pokemon-champions-dex/scripts/champdex.py"
SIDE_FLAGS = {"reflect", "light_screen", "aurora_veil", "friend_guard", "helping_hand", "battery",
              "power_spot", "steely_spirit", "charge", "tailwind", "stealth_rock"}
DOUBLE_FLAGS = {"friend_guard", "helping_hand", "battery", "power_spot", "steely_spirit"}
MON_FIELDS = {"uid", "slug", "ability", "item", "nature", "sps", "boosts", "status", "moves",
              "curHP", "abilityOn", "autoSig", "buildRef", "pinned"}


def web_field(raw: dict) -> dict:
    row = _object(raw, "web.field")
    _only(row, {"format", "weather", "terrain", "gravity", "foresight", "switchInDrops", "trickRoom", "sides"}, "web.field")
    if row.get("format") not in ("single", "double"):
        raise CaseInputError("web.field.format", "expected a recorded single or double format")
    for key in ("gravity", "foresight", "switchInDrops", "trickRoom"):
        if key in row and type(row[key]) is not bool:
            raise CaseInputError(f"web.field.{key}", "expected a boolean")
    sides = _object(row.get("sides"), "web.field.sides")
    _only(sides, {"a", "b"}, "web.field.sides")
    out = {k: row[k] for k in ("format", "weather", "terrain", "gravity", "foresight") if k in row}
    for source, target in (("a", "mineSide"), ("b", "foeSide")):
        flags = _object(sides.get(source), f"web.field.sides.{source}")
        _only(flags, SIDE_FLAGS, f"web.field.sides.{source}")
        if any(type(v) is not bool for v in flags.values()):
            raise CaseInputError("web.field.sides", "side flags must be booleans")
        # Identical to the browser's liveFlags: dormant doubles flags do not affect singles.
        out[target] = {k: v for k, v in flags.items() if v and (row["format"] == "double" or k not in DOUBLE_FLAGS)}
    return _field(out)


class WebReader:
    def __init__(self):
        self.facts: dict[str, dict] = {}

    def pokemon(self, name: str) -> dict:
        if name not in self.facts:
            try:
                proc = subprocess.run([sys.executable, str(DEX), "batch", "pokemon", name,
                                       "--strict", "--format", "json"], capture_output=True,
                                      encoding="utf-8", timeout=30)
                rows = json.loads(proc.stdout)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                raise CaseInputError("web.slug", "sibling dex is unavailable", "engine_unavailable") from exc
            if proc.returncode or not isinstance(rows, list) or len(rows) != 1 or rows[0].get("error"):
                raise CaseInputError("web.slug", "unknown recorded Pokemon", "not_found")
            self.facts[name] = rows[0]
        return self.facts[name]

    def mon(self, raw: dict, *, foe: bool = False) -> dict:
        row = _object(raw, "web.mon")
        _only(row, MON_FIELDS, "web.mon")
        fact = self.pokemon(_name(row.get("slug"), "web.mon.slug"))
        for key in ("ability", "item", "nature", "status"):
            if key in row and not isinstance(row[key], str):
                raise CaseInputError(f"web.mon.{key}", "expected a string")
        item = row.get("item", "")
        if not fact["is_mega"]:
            mega = next((m for m in fact.get("mega_forms", []) if m["required_item"] == item), None)
            if mega:
                fact = self.pokemon(mega["name"])
        state = {"boosts": deepcopy(row.get("boosts", {})), "status": row.get("status") or "Healthy"}
        if row.get("abilityOn") is not None:
            state["abilityOn"] = row["abilityOn"]
        if row.get("curHP") is not None:
            hp = row["curHP"]
            if type(hp) is not int:
                raise CaseInputError("web.mon.curHP", "expected integer HP or null")
            sps = _object(row.get("sps", {}), "web.mon.sps")
            hp_sp = sps.get("hp", 0)
            if type(hp_sp) is not int or not 0 <= hp_sp <= 32:
                raise CaseInputError("web.mon.sps.hp", "invalid HP SP")
            # The browser caps current HP to the effective build, even after an SP edit.
            maximum = 1 if fact["stats"]["hp"] == 1 else fact["stats"]["hp"] + hp_sp + 75
            state["curHP"] = max(1, min(maximum, hp))
        if foe:
            return {"name": fact["name"], **state}
        moves = row.get("moves")
        if not isinstance(moves, list) or len(moves) > 4 or any(not isinstance(m, str) for m in moves):
            raise CaseInputError("web.mon.moves", "expected at most four browser move slots")
        ability = row.get("ability", "")
        if fact["is_mega"] and ability not in fact["abilities"]:
            ability = fact["abilities"][0]
        return {"name": fact["name"], "moves": [m for m in moves if m],
                "item": item,
                "ability": ability, "nature": row.get("nature", ""), "sps": deepcopy(row.get("sps", {})), **state}


def import_web(raw: dict, prior: dict, foe_id: str | None = None) -> tuple[dict, dict]:
    source = _object(raw, "web")
    _only(source, {"schema", "roster", "records"}, "web")
    if source.get("schema") != "quark_drive_web_inference.v1":
        raise CaseInputError("web.schema", "expected quark_drive_web_inference.v1")
    roster, records = _object(source.get("roster"), "web.roster"), _object(source.get("records"), "web.records")
    teams = _object(roster.get("teams"), "web.roster.teams")
    for side in ("a", "b"):
        if not isinstance(teams.get(side), list) or not 1 <= len(teams[side]) <= 6:
            raise CaseInputError("web.roster.teams", "expected one to six slots per side")
        for mon in teams[side]:
            _object(mon, "web.roster.member")
    if foe_id is None:
        active = _object(roster.get("active"), "web.roster.active").get("b")
        if type(active) is not int or not 0 <= active < len(teams["b"]):
            raise CaseInputError("web.roster.active.b", "opponent index is out of range")
        foe_id = teams["b"][active].get("uid")
    _name(foe_id, "web.foe_id")
    foes = [m for m in teams["b"] if m.get("uid") == foe_id]
    if len(foes) != 1:
        raise CaseInputError("web.foe_id", "expected one matching opponent roster slot")
    record = _object(records.get(foe_id), "web.record")
    _only(record, {"known", "observations"}, "web.record")
    reader = WebReader()
    ours = [m for m in teams["a"] if m.get("slug")]
    ids = [_name(m.get("uid"), "web.mine.uid") for m in ours]
    if len(set(ids)) != len(ids):
        raise CaseInputError("web.mine.uid", "duplicate roster member ids")
    field = web_field(roster.get("field"))
    case = init_case({"format": field["format"], "foe": {"name": reader.mon(foes[0], foe=True)["name"]},
                      "mine_team": [reader.mon(m) for m in ours], "known": record.get("known", {}), "prior": prior})
    hits = record.get("observations")
    if not isinstance(hits, list):
        raise CaseInputError("web.observations", "expected a list")
    for raw_hit in hits:
        h = _object(raw_hit, "web.observation")
        _only(h, {"id", "kind", "foeId", "mineId", "move", "before", "after", "crit", "singleTarget",
                  "field", "mineBoosts", "foeBoosts", "mineStatus", "foeStatus", "enabled",
                  "mineSnapshot", "foeSnapshot"}, "web.observation")
        if h.get("foeId") != foe_id:
            raise CaseInputError("web.observation.foeId", "record belongs to another opponent", "authority_mismatch")
        for key, uid in (("mineSnapshot", h.get("mineId")), ("foeSnapshot", foe_id)):
            snap = _object(h.get(key), f"web.observation.{key}")
            if snap.get("uid") != uid:
                raise CaseInputError(f"web.observation.{key}", "snapshot identity differs from the recorded member", "authority_mismatch")
        mine_id = _name(h.get("mineId"), "web.observation.mineId")
        if mine_id not in ids:
            ids.append(mine_id)  # A removed roster member still has an authoritative frozen build.
        snapshots = []
        for prefix in ("mine", "foe"):
            snapshot = {**h[f"{prefix}Snapshot"], "boosts": h.get(f"{prefix}Boosts", {}),
                        "status": h.get(f"{prefix}Status", "")}
            snapshots.append(reader.mon(snapshot, foe=prefix == "foe"))
        mine, foe = snapshots
        case["observations"].append({
            **{k: h[k] for k in ("id", "kind", "move", "before", "after") if k in h},
            "mine": ids.index(mine_id), "crit": h.get("crit", False), "single_target": h.get("singleTarget", False),
            "enabled": h.get("enabled", True), "field": web_field(h.get("field")),
            "mine_snapshot": mine, "foe_snapshot": foe,
            "mine_state": {k: mine[k] for k in ("boosts", "status", "abilityOn", "curHP") if k in mine},
            "foe_state": {k: foe[k] for k in ("boosts", "status", "abilityOn", "curHP") if k in foe},
        })
    case = load_case(case)
    return case, {"schema": "ncp_inference_web_import.v1", "foe_id": foe_id, "mine_ids": ids,
                  "observations": len(hits), "prior_basis": "caller_supplied_snapshot",
                  "notes": ["Browser storage contains no prior; verify its snapshot before comparing old results.",
                            "Switch-in drops are disabled for recorded stages; Trick Room does not change hit damage."]}
