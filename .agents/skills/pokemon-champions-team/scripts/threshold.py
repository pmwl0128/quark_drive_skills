"""Budget-valid SP threshold lanes over a stated, dex-checked battle scenario."""
from __future__ import annotations

from copy import deepcopy
from itertools import product

import cliffs
import counters
import matchup
import ncplink
import scenario
import speedlink
from rules import get_ruleset
from tune import PHYS_DEF_SPECIAL_MOVES


GOALS = frozenset({"ohko", "2hko", "survive", "survive_2", "outspeed"})
SOLVE_FOR = frozenset({"attacker.atk", "attacker.def", "attacker.spa", "attacker.spe", "defender.hp",
                      "defender.def", "defender.spd", "defender.spe", "defender.bulk"})
PLUS_NATURE = {"atk": "Adamant", "spa": "Modest", "def": "Bold", "spd": "Calm", "spe": "Jolly"}
EXTRAS = frozenset({"solve_for", "goal", "probability", "compare_items", "maximize", "speed_field", "switch_in_drops"})
SPEED_FIELD = frozenset({"attacker_tailwind", "defender_tailwind", "trick_room"})
BATCH_SIZE = 256


def _fail(message, query="threshold", code="bad_input"):
    raise counters.QueryError(message, query=query, code=code)


def _validate(spec):
    if not isinstance(spec, dict):
        _fail("threshold input must be a JSON object")
    solve_for, goal = spec.get("solve_for"), spec.get("goal")
    if not isinstance(solve_for, str) or solve_for not in SOLVE_FOR or not isinstance(goal, str) or goal not in GOALS:
        _fail("choose a supported solve_for and goal", "solve_for")
    side, stat = solve_for.split(".")
    if goal == "outspeed":
        if stat != "spe":
            _fail("outspeed must solve a spe coordinate", "solve_for")
    elif goal in ("ohko", "2hko"):
        if side != "attacker" or stat not in ("atk", "def", "spa"):
            _fail("KO goals must solve the attacker's relevant damage stat", "solve_for")
    elif side != "defender" or stat not in ("hp", "def", "spd", "bulk"):
        _fail("survival goals must solve defender.hp, def, spd or bulk", "solve_for")
    probability = spec.get("probability", "guaranteed")
    if probability not in ("guaranteed", "any") or (goal == "outspeed" and probability != "guaranteed"):
        _fail("probability must be guaranteed or any; outspeed is deterministic", "probability")
    extra = spec.get("compare_items", [])
    if not isinstance(extra, list) or len(extra) > 3 or any(v is not None and not isinstance(v, str) for v in extra):
        _fail("compare_items accepts at most three item names or null", "compare_items")
    maximize = spec.get("maximize")
    if maximize is not None:
        if stat == "bulk" or not isinstance(maximize, str) or "." not in maximize:
            _fail("maximize needs a distinct SP stat on the solved side; bulk has no secondary search", "maximize")
        max_side, max_stat = maximize.split(".", 1)
        if max_side != side or max_stat not in scenario.STATS or max_stat == stat:
            _fail("maximize must name a different stat on the solved side", "maximize")
    sf = spec.get("speed_field", {})
    if not isinstance(sf, dict) or set(sf) - SPEED_FIELD or any(type(v) is not bool for v in sf.values()):
        _fail("speed_field accepts boolean attacker_tailwind, defender_tailwind and trick_room", "speed_field")
    if type(spec.get("switch_in_drops", True)) is not bool:
        _fail("switch_in_drops must be boolean", "switch_in_drops")
    if set(spec) - (set(EXTRAS) | {"attacker", "defender", "move", "format", "attacker_set", "defender_set", "field"}):
        _fail("unsupported threshold field")
    return side, stat, goal, probability, extra, maximize, sf


def _natures(current, stat):
    if stat == "hp":
        return [current]
    result, effects = [], set()
    for nature in (current, PLUS_NATURE.get(stat), "Serious"):
        if nature and cliffs.nature_mod(nature, stat) not in effects:
            effects.add(cliffs.nature_mod(nature, stat))
            result.append(nature)
    return result


def _item_actors(cache, spec, side, base_actor, compared):
    values = [base_actor.get("item")]
    for raw in compared:
        item = scenario._resolve(raw, "item", "compare_items") if raw not in (None, "") else None
        if item not in values:
            values.append(item)
    result = []
    for item in values:
        if item == base_actor.get("item"):
            result.append(deepcopy(base_actor))
            continue
        override = {**(spec.get(side + "_set") or {}), "item": item}
        actor, _ = scenario._combatant(cache, spec[side], override, side)
        if actor["name"] != base_actor["name"]:
            _fail("a compared item changes battle form; query that form separately", "compare_items")
        result.append(actor)
    return result


def _speed_input(actor, field, tailwind):
    result = {k: actor[k] for k in ("name", "nature", "item", "ability", "sps") if actor.get(k) is not None}
    for k in ("boosts", "status"):
        if actor.get(k):
            result[k] = actor[k]
    result["field"] = {k: field[k] for k in ("weather", "terrain") if k in field}
    if tailwind:
        result["field"]["tailwind"] = True
    return result


def _batch_damage(requests):
    out = []
    for start in range(0, len(requests), BATCH_SIZE):
        rows = ncplink._run(requests[start:start + BATCH_SIZE], command="batch")
        if not isinstance(rows, list) or len(rows) != min(BATCH_SIZE, len(requests) - start):
            _fail("calculator returned an incomplete threshold batch", code="engine_unavailable")
        if any(not isinstance(r, dict) or r.get("error") for r in rows):
            _fail("calculator rejected a threshold candidate", code="engine_unavailable")
        out.extend(rows)
    return out


def _ko2_status(doc, goal, probability):
    chance = doc.get("ko_chance") or {}
    n, guaranteed, pct = chance.get("n"), chance.get("guaranteed"), chance.get("chance_pct")
    if type(n) is not int or type(guaranteed) is not bool or n < 1:
        return None
    if goal == "2hko":
        return (guaranteed and n <= 2) if probability == "guaranteed" else (n <= 2 and (pct is None or pct > 0))
    if probability == "guaranteed":
        return n > 2
    if n > 2 or (n == 2 and not guaranteed):
        return True
    if n <= 2 and guaranteed:
        return False
    return None


def _damage_reading(doc, goal, probability, move):
    rolls, hp = doc.get("damage"), doc.get("defenderHP")
    if not isinstance(rolls, list) or not rolls or any(type(r) is not int or r < 0 for r in rolls) or type(hp) is not int or hp <= 0:
        _fail("calculator omitted valid rolls or HP", code="engine_unavailable")
    if goal in ("ohko", "survive"):
        ko = sum(r >= hp for r in rolls) / len(rolls)
    else:
        ko = sum(a + b >= hp for a, b in product(rolls, repeat=2)) / (len(rolls) ** 2)
    static = ko if goal in ("ohko", "2hko") else 1 - ko
    static_met = static == 1 if probability == "guaranteed" else static > 0
    engine = _ko2_status(doc, goal, probability) if goal in ("2hko", "survive_2") else None
    met = static_met if goal in ("ohko", "survive") else engine is True
    chance = doc.get("ko_chance") or {}
    pct = chance.get("chance_pct")
    score, basis = static, "static_rolls"
    if goal in ("2hko", "survive_2") and type(chance.get("n")) is int and type(pct) in (int, float):
        if chance["n"] > 2:
            score = 0 if goal == "2hko" else 1
            basis = "engine_ko_chance"
        elif chance["n"] == 2:
            score = pct / 100 if goal == "2hko" else 1 - pct / 100
            basis = "engine_ko_chance"
    return {"met": met, "static_goal_chance_pct": round(static * 100, 2),
            "closest_goal_chance_pct": round(score * 100, 2),
            "closest_metric_basis": basis, "engine_goal_met": engine,
            "damage": matchup.dmg_fact(move, doc), "defender_hp": hp,
            "conditional": bool(doc.get("ko_caveats") or doc.get("hits_range")),
            "mechanical_qualifications": {k: deepcopy(doc[k]) for k in ("ko_caveats", "hits_range") if doc.get(k)}}


def _points(actor, stat, maximize, rules):
    spread = actor["sps"]
    fixed = sum(v for k, v in spread.items() if k != stat and k != maximize)
    secondary = [None] if maximize is None else range(rules.sp_per_stat_cap + 1)
    return [(p, m) for m in secondary for p in range(rules.sp_per_stat_cap + 1)
            if fixed + p + (0 if maximize is None else m) <= rules.sp_total_cap]


def _damage_lane(actors, side, stat, maximize, move, field, goal, probability, switch_in_drops):
    rules = get_ruleset()
    actor = actors[side]
    points = _points(actor, stat, maximize, rules)
    baseline_secondary = actor["sps"].get(maximize) if maximize else None
    requests, allocations = [], []
    for p, m in points:
        varied = deepcopy(actor)
        varied["sps"][stat] = p
        if maximize:
            varied["sps"][maximize] = m
        pair = {**actors, side: varied}
        requests.append({"attacker": pair["attacker"], "defender": pair["defender"],
                         "move": move, "field": field, "switch_in_drops": switch_in_drops})
        allocations.append(varied["sps"])
    docs = _batch_damage(requests)
    readings = [_damage_reading(doc, goal, probability, move) for doc in docs]
    rows = [{"primary_sp": p, "secondary_sp": m, "allocation": allocation, **reading}
            for (p, m), allocation, reading in zip(points, allocations, readings)]
    base = [r for r in rows if maximize is None or r["secondary_sp"] == baseline_secondary]
    base.sort(key=lambda r: r["primary_sp"])
    if not base:
        _fail("fixed SP investment leaves no legal point for the solved stat", "solve_for")
    first = next((r for r in base if r["met"]), None)
    closest = max(base, key=lambda r: (r["closest_goal_chance_pct"], r["primary_sp"]))
    optimized = max((r for r in rows if r["met"]),
                    key=lambda r: (r["secondary_sp"], -r["primary_sp"]), default=None) if maximize else None
    return {"min_sp": first["primary_sp"] if first else None,
            "at_zero": next((r for r in base if r["primary_sp"] == 0), None),
            "at_cap": base[-1], "at_min": first, "closest": closest if first is None else None,
            "maximized": optimized, "feasible_points": len(rows), "primary_cap": base[-1]["primary_sp"]}


def _bulk_lane(actors, stat, move, field, goal, probability, switch_in_drops):
    rules = get_ruleset()
    defender = actors["defender"]
    fixed = sum(v for k, v in defender["sps"].items() if k not in ("hp", stat))
    points = [(hp, defense) for hp in range(rules.sp_per_stat_cap + 1)
              for defense in range(rules.sp_per_stat_cap + 1)
              if fixed + hp + defense <= rules.sp_total_cap]
    requests = []
    for hp, defense in points:
        varied = deepcopy(defender)
        varied["sps"].update(hp=hp, **{stat: defense})
        requests.append({"attacker": actors["attacker"], "defender": varied,
                         "move": move, "field": field, "switch_in_drops": switch_in_drops})
    docs = _batch_damage(requests)
    rows = []
    for (hp, defense), doc in zip(points, docs):
        allocation = {**defender["sps"], "hp": hp, stat: defense}
        rows.append({"hp_sp": hp, "defense_sp": defense, "total_sp": hp + defense,
                     "allocation": allocation, **_damage_reading(doc, goal, probability, move)})
    rows.sort(key=lambda r: (r["total_sp"], -r["hp_sp"]))
    first = next((r for r in rows if r["met"]), None)
    closest = max(rows, key=lambda r: (r["closest_goal_chance_pct"], r["total_sp"]))
    return {"min_total_sp": first["total_sp"] if first else None,
            "defense_stat": stat, "at_min": first, "at_cap": max(rows, key=lambda r: r["total_sp"]),
            "closest": closest if first is None else None, "feasible_points": len(rows)}


def _speed_lane(actors, side, stat, maximize, field, speed_field):
    rules = get_ruleset()
    other = "defender" if side == "attacker" else "attacker"
    tail = lambda s: speed_field.get(s + "_tailwind") is True
    target_doc = speedlink.speed_batch([_speed_input(actors[other], field, tail(other))])[0]
    target = target_doc.get("finalSpeed")
    if type(target) is not int:
        _fail("target speed could not be computed", code="engine_unavailable")
    points = _points(actors[side], stat, maximize, rules)
    requests, allocations = [], []
    for p, m in points:
        actor = deepcopy(actors[side])
        actor["sps"][stat] = p
        if maximize:
            actor["sps"][maximize] = m
        requests.append(_speed_input(actor, field, tail(side)))
        allocations.append(actor["sps"])
    docs = speedlink.speed_batch(requests)
    rows = []
    for (p, m), allocation, doc in zip(points, allocations, docs):
        speed = doc.get("finalSpeed")
        if type(speed) is not int:
            _fail("a speed candidate could not be computed", code="engine_unavailable")
        met = speed < target if speed_field.get("trick_room") else speed > target
        rows.append({"primary_sp": p, "secondary_sp": m, "allocation": allocation,
                     "speed": speed, "target_speed": target, "met": met})
    baseline_secondary = actors[side]["sps"].get(maximize) if maximize else None
    base = [r for r in rows if maximize is None or r["secondary_sp"] == baseline_secondary]
    base.sort(key=lambda r: r["primary_sp"])
    if not base:
        _fail("fixed SP investment leaves no legal speed point", "solve_for")
    first = next((r for r in base if r["met"]), None)
    closest = min(base, key=lambda r: (r["speed"], -r["primary_sp"])) if speed_field.get("trick_room") else max(
        base, key=lambda r: (r["speed"], r["primary_sp"]))
    optimized = max((r for r in rows if r["met"]),
                    key=lambda r: (r["secondary_sp"], -r["primary_sp"]), default=None) if maximize else None
    return {"min_sp": first["primary_sp"] if first else None,
            "at_zero": next((r for r in base if r["primary_sp"] == 0), None),
            "at_cap": base[-1], "at_min": first, "closest": closest if first is None else None,
            "maximized": optimized, "feasible_points": len(rows), "primary_cap": base[-1]["primary_sp"]}


def query(spec, cache):
    side, stat, goal, probability, compared, maximize, speed_field = _validate(spec)
    switch_in_drops = spec.get("switch_in_drops", True)
    base_spec = {k: v for k, v in spec.items() if k not in EXTRAS}
    prepared = scenario.prepare(base_spec, cache, require_move=goal != "outspeed")
    actors, basis, move, field = (prepared[k] for k in ("actors", "basis", "move", "field"))
    if stat == "bulk":
        category = prepared["move_fact"].get("category")
        stat = "def" if category == "Physical" or move in PHYS_DEF_SPECIAL_MOVES else "spd"
    if goal != "outspeed":
        category = prepared["move_fact"].get("category")
        if move == "Foul Play":
            _fail("Foul Play uses the target's Attack, not the user's SP; this coordinate is unsupported", "solve_for",
                  "unsupported_mechanic")
        expected = "def" if move == "Body Press" else "atk" if category == "Physical" else "spa"
        if side == "attacker" and stat != expected:
            _fail(f"this move uses attacker.{expected}, not attacker.{stat}", "solve_for")
    calc_field = deepcopy(field)
    for s in ("attacker", "defender"):
        if speed_field.get(s + "_tailwind"):
            calc_field.setdefault(s + "Side", {})["tailwind"] = True
    item_actors = _item_actors(cache, base_spec, side, actors[side], compared)
    lanes = []
    for item_actor in item_actors:
        for nature in _natures(item_actor["nature"], stat):
            pair = deepcopy(actors)
            pair[side] = deepcopy(item_actor)
            pair[side]["nature"] = nature
            secondary = maximize.split(".")[1] if maximize else None
            if goal == "outspeed":
                lane = _speed_lane(pair, side, stat, secondary, field, speed_field)
            elif spec["solve_for"] == "defender.bulk":
                lane = _bulk_lane(pair, stat, move, calc_field, goal, probability, switch_in_drops)
            else:
                lane = _damage_lane(pair, side, stat, secondary, move, calc_field, goal, probability, switch_in_drops)
            if secondary == "spe" and lane.get("maximized") and goal != "outspeed":
                optimized = deepcopy(pair[side])
                optimized["sps"] = lane["maximized"]["allocation"]
                speed = speedlink.speed_batch([_speed_input(
                    optimized, field, speed_field.get(side + "_tailwind") is True)])[0].get("finalSpeed")
                if type(speed) is not int:
                    _fail("maximized Speed could not be computed", code="engine_unavailable")
                lane["maximized"]["final_speed"] = speed
            lane.update(nature=nature, item=pair[side].get("item"),
                        nature_effect=cliffs.nature_mod(nature, stat))
            lanes.append(lane)
    return {"kind": "threshold", "format": prepared["format"], "solve_for": spec["solve_for"],
            "goal": goal, "probability": probability, "maximize": maximize,
            "move": move, "attacker": actors["attacker"], "defender": actors["defender"],
            "basis": basis, "field": calc_field, "speed_field": speed_field,
            "conditions": {"weather": field.get("weather") or "none", "terrain": field.get("terrain") or "none",
                           "attacker_tailwind": bool(speed_field.get("attacker_tailwind")),
                           "defender_tailwind": bool(speed_field.get("defender_tailwind")),
                           "trick_room": bool(speed_field.get("trick_room")), "switch_in_drops": switch_in_drops},
            "lanes": lanes, "confidence": "low", "confidence_reason": "retained-common-build-with-stated-overrides",
            "coverage": {"cache_top_k": (cache.get("built_for") or {}).get("top_k"), "reference_only": True},
            "notes": ["All tested allocations obey the current rule's per-stat and total SP caps; other SP coordinates stay fixed unless maximize names one.",
                      "Damage percentages from rolls are static. For two-hit goals, the calculator's recovery-aware ko_chance must also support the threshold; unknown verdicts never certify it.",
                      "Mechanical qualifications can still prevent a battle guarantee. Accuracy, switching and evolving multi-turn states are not simulated."]}
