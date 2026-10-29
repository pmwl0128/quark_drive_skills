"""Calculate a stated battle scenario over one retained, exact-form common build per side."""
from __future__ import annotations

from copy import deepcopy

import counters
import dexlink
import matchup
import ncplink
import oppcache
from stat_keys import canonical_spread, canonical_spreads


STATS = frozenset({"hp", "atk", "def", "spa", "spd", "spe"})
BOOSTS = STATS - {"hp"}
STATUSES = frozenset({"Healthy", "Burned", "Paralyzed", "Poisoned", "Badly Poisoned", "Asleep", "Frozen"})
WEATHER = {"rain": "Rain", "sun": "Sun", "sand": "Sand", "snow": "Snow"}
TERRAIN = {"electric": "Electric", "grassy": "Grassy", "psychic": "Psychic", "misty": "Misty"}
SET_FIELDS = frozenset({"item", "ability", "nature", "sps", "boosts", "status"})
FIELD_FLAGS = frozenset({"reflect", "light_screen", "aurora_veil", "helping_hand"})


def _fail(message, path, code="bad_input"):
    raise counters.QueryError(message, code=code, query=path)


def _resolve(raw, kind, path):
    if not isinstance(raw, str) or not raw.strip():
        _fail("a name is required", path)
    row = dexlink.resolve_names([raw], kind, fuzzy=False).get(raw) or {}
    if not row.get("ok") or not row.get("canonical"):
        _fail(f"{kind} does not resolve exactly in the Champions dex", path, "not_found")
    return row["canonical"]


def _spread(raw, path):
    if not isinstance(raw, dict):
        _fail("sps must be an object", path)
    try:
        values = canonical_spread(raw)
    except ValueError as exc:
        _fail(str(exc), path)
    if any(k not in STATS or type(v) is not int or not 0 <= v <= 32 for k, v in values.items()):
        _fail("sps must use hp/atk/def/spa/spd/spe with integer values 0..32", path)
    if sum(values.values()) > 66:
        _fail("sps total exceeds 66", path)
    return {k: values.get(k, 0) for k in ("hp", "atk", "def", "spa", "spd", "spe")}


def _field(raw, fmt):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - ({"weather", "terrain"} | FIELD_FLAGS):
        _fail("field accepts weather, terrain, screens and helping_hand", "field")
    result = {"format": fmt}
    for key, allowed in (("weather", WEATHER), ("terrain", TERRAIN)):
        value = raw.get(key)
        if value is not None:
            if not isinstance(value, str) or value.lower() not in allowed:
                _fail(f"field.{key} is not a supported condition", f"field.{key}")
            result[key] = allowed[value.lower()]
    for key in FIELD_FLAGS:
        if key in raw and type(raw[key]) is not bool:
            _fail(f"field.{key} must be boolean", f"field.{key}")
    screens = {k: True for k in ("reflect", "light_screen", "aurora_veil") if raw.get(k)}
    if screens:
        result["defenderSide"] = screens
    if raw.get("helping_hand"):
        result["attackerSide"] = {"helping_hand": True}
    return result


def _combatant(cache, selector, raw_set, side):
    canonical = _resolve(selector, "pokemon", side) if not selector.startswith("variant:") else selector
    sets = cache.get("sets") or {}
    keys = oppcache.selector_keys(cache, canonical, list(sets))
    if not keys:
        _fail("no retained build for this exact form; provide an in-scope form or variant", side, "not_found")
    key = max(keys, key=lambda k: (sets[k].get("coverage") or 0, k))
    base = sets[key]
    form = base.get("run_form") or base.get("species")
    if not form or not isinstance(raw_set, dict) or set(raw_set) - SET_FIELDS:
        _fail(f"{side}_set must contain only item, ability, nature, sps, boosts, status", f"{side}_set")
    names = [form]
    if form.startswith("Mega "):
        names.append(base.get("species"))
    facts = dexlink.lookup_pokemon(list(dict.fromkeys(names)))
    own = facts.get(form) or {}
    if not own.get("found"):
        _fail("battle form is missing from the Champions dex", side, "authority_mismatch")
    if not base.get("sps") and "sps" not in raw_set:
        _fail("common build has no known SP spread; state a complete sps replacement", f"{side}_set.sps")
    actor = {"name": form, "item": base.get("item"), "ability": base.get("ability"),
             "nature": base.get("nature"), "sps": _spread(base.get("sps") or {}, f"{side}.source.sps")}
    if "item" in raw_set:
        item = raw_set["item"]
        if item is not None and item != "":
            actor["item"] = _resolve(item, "item", f"{side}_set.item")
            info = dexlink.lookup_items([actor["item"]]).get(actor["item"]) or {}
            if not info.get("found"):
                _fail("item is not in Champions", f"{side}_set.item", "unknown_item")
        else:
            actor["item"] = None
    required = own.get("required_item") if own.get("is_mega") else None
    if required and actor["item"] != required:
        _fail(f"{form} requires {required}", f"{side}_set.item")
    if not required and actor["item"]:
        info = dexlink.lookup_items([actor["item"]]).get(actor["item"]) or {}
        hosted = next((m.get("name") for m in own.get("mega_forms") or []
                       if m.get("required_item") == actor["item"]), None)
        if hosted:
            form = hosted
            own = dexlink.lookup_pokemon([form]).get(form) or {}
            if not own.get("found"):
                _fail("Mega form is missing from the Champions dex", side, "authority_mismatch")
            actor["name"] = form
            if "ability" not in raw_set:
                actor["ability"] = (own.get("abilities") or [None])[0]
        elif info.get("required_by"):
            _fail("the stated Mega Stone does not belong to this form", f"{side}_set.item")
    if "ability" in raw_set:
        actor["ability"] = _resolve(raw_set["ability"], "ability", f"{side}_set.ability")
    if actor["ability"] not in (own.get("abilities") or []):
        _fail("ability is not legal on the battle form", f"{side}_set.ability", "unknown_ability")
    if "nature" in raw_set:
        actor["nature"] = _resolve(raw_set["nature"], "nature", f"{side}_set.nature")
    if not actor["nature"]:
        _fail("common build has no known nature; state one", f"{side}_set.nature")
    if "sps" in raw_set:
        actor["sps"] = _spread(raw_set["sps"], f"{side}_set.sps")
    if "boosts" in raw_set:
        boosts = raw_set["boosts"]
        if not isinstance(boosts, dict) or any(k not in BOOSTS or type(v) is not int or not -6 <= v <= 6
                                               for k, v in boosts.items()):
            _fail("boosts must use atk/def/spa/spd/spe stages -6..6", f"{side}_set.boosts")
        actor["boosts"] = boosts
    if "status" in raw_set:
        status = raw_set["status"]
        if not isinstance(status, str) or status not in STATUSES:
            _fail("unsupported calculator status", f"{side}_set.status")
        actor["status"] = status
    return actor, {"variant_id": key, "source": canonical_spreads(deepcopy(base)),
                   "overrides": canonical_spreads(deepcopy(raw_set))}


def prepare(spec, cache, *, require_move=True):
    if not isinstance(spec, dict) or set(spec) - {"attacker", "defender", "move", "format", "attacker_set", "defender_set", "field"}:
        _fail("scenario accepts attacker, defender, move, format, two set overrides and field", "scenario")
    fmt = spec.get("format")
    if fmt not in ("single", "double"):
        _fail("format must be single or double", "format", "unknown_format")
    if (cache.get("built_for") or {}).get("format") != fmt:
        raise oppcache.StaleCacheError("opponent cache format does not match scenario")
    actors, basis = {}, {}
    for side in ("attacker", "defender"):
        name = spec.get(side)
        if not isinstance(name, str) or not name:
            _fail("a species or exact variant is required", side)
        override = spec.get(f"{side}_set", {})
        actors[side], basis[side] = _combatant(cache, name, {} if override is None else override, side)
    move = spec.get("move")
    move_fact = {}
    if require_move or move is not None:
        move = _resolve(move, "move", "move")
        move_fact = dexlink.lookup_moves([move]).get(move) or {}
        if not move_fact.get("found"):
            _fail("move is not in Champions", "move", "unknown_move")
        if move_fact.get("category") not in ("Physical", "Special") or move_fact.get("is_ohko"):
            _fail("scenario requires a damaging, non-OHKO move", "move", "unsupported_mechanic")
        atk_fact = dexlink.lookup_pokemon([actors["attacker"]["name"]]).get(actors["attacker"]["name"]) or {}
        if move not in (atk_fact.get("moves") or []):
            _fail("attacker cannot learn the stated move in Champions", "move", "unknown_move")
    field = _field(spec.get("field"), fmt)
    return {"format": fmt, "actors": actors, "basis": basis, "move": move,
            "move_fact": move_fact, "field": field}


def query(spec, cache):
    prepared = prepare(spec, cache)
    fmt, actors, basis, move, field = (prepared[k] for k in
                                      ("format", "actors", "basis", "move", "field"))
    doc = ncplink._run({"attacker": actors["attacker"], "defender": actors["defender"],
                       "move": move, "field": field, "switch_in_drops": True})
    if not isinstance(doc, dict) or doc.get("error"):
        _fail("calculator could not evaluate this scenario", "scenario", "engine_unavailable")
    if doc.get("minPercent") is None or doc.get("defenderHP") is None:
        _fail("calculator omitted the damage result", "scenario", "engine_unavailable")
    conditions = {"weather": field.get("weather") or "none", "terrain": field.get("terrain") or "none",
                  "screens": sorted((field.get("defenderSide") or {}).keys()),
                  "helping_hand": bool((field.get("attackerSide") or {}).get("helping_hand"))}
    notes = ["Unstated fields come from a retained exact-form common build; overrides describe a hypothetical set, not observed usage.",
             "KO chance is the calculator's static verdict. Read mechanical caveats; accuracy, switching and evolving multi-turn states are not simulated."]
    if "weather" not in field and any(actor.get("ability") in ("Drought", "Drizzle", "Sand Stream", "Snow Warning")
                                      for actor in actors.values()):
        notes.append("A weather-setting ability is present; no weather was applied. State field.weather when it applies.")
    return {"kind": "scenario", "format": fmt, "move": move, "attacker": actors["attacker"],
            "defender": actors["defender"], "basis": basis, "field": field,
            "conditions": conditions,
            "damage": matchup.dmg_fact(move, doc), "rolls": doc.get("damage"),
            "defender_hp": doc["defenderHP"], "description": doc.get("description"),
            "confidence": "low", "confidence_reason": "retained-common-build-with-stated-overrides",
            "coverage": {"cache_top_k": (cache.get("built_for") or {}).get("top_k"),
                         "reference_only": True},
            "notes": notes}
