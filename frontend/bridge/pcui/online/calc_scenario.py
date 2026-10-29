"""Deterministic battle scenarios for the QA calc tools (frontend/design.md §7.2).

A question such as "how many Attack SP does a Shell-Smash-passed Garchomp need for Poison Jab to OHKO a max
physically bulky Primarina" needs three things a small model does badly by itself: an exact battle state
(natures, SP, items, stat stages, field) layered on the most-used sets, Champions legality of the named
items/abilities/moves (the calc engine also knows items this game does not have), and a sweep over SP that
finds the cliff exactly instead of sampling it. The model only translates the wording into a scenario; this
module validates it, builds the calc payloads and solves the threshold in one tool call.

Goals are judged from the 16 damage rolls: one hit (OHKO / survive) and the 16x16 roll pairs of two hits
(2HKO / survive two). Residual damage, recovery, entry hazards and multi-turn changes are not modelled.
"""
from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any, Callable

SPREAD_KEYS = ("hp", "atk", "def", "spa", "spd", "spe")
BOOST_KEYS = ("atk", "def", "spa", "spd", "spe")
SP_MAX, SP_TOTAL = 32, 66
STATUSES = ("Healthy", "Burned", "Paralyzed", "Poisoned", "Badly Poisoned", "Asleep", "Frozen")
WEATHERS = {"rain": "Rain", "sun": "Sun", "sand": "Sand", "snow": "Snow"}
TERRAINS = {"electric": "Electric", "grassy": "Grassy", "psychic": "Psychic", "misty": "Misty"}
SCREENS = ("reflect", "light_screen", "aurora_veil")
PLUS_NATURE = {"atk": "Adamant", "spa": "Modest", "def": "Bold", "spd": "Calm", "spe": "Jolly"}
NEUTRAL_NATURE = "Serious"
SOLVE_FOR = ("attacker.atk", "attacker.spa", "attacker.spe", "defender.hp", "defender.def",
             "defender.spd", "defender.spe", "defender.bulk")
GOALS = ("ohko", "2hko", "survive", "survive_2", "outspeed")
COMPARE_ITEMS_MAX = 3
NO_ITEM = {"", "none", "no item", "无", "无道具", "なし", "持ち物なし"}
# Held items that change only Speed. A question that states a build's nature or SP without naming one
# describes that build without it (最速 = +Spe nature and 32 Spe SP, not a Choice Scarf).
SPEED_ITEMS = frozenset({"Choice Scarf"})
# Abilities that set weather on entry (or on Mega Evolution). The calc applies only stated weather.
WEATHER_ABILITIES = {"Drought": "sun", "Drizzle": "rain", "Sand Stream": "sand", "Snow Warning": "snow"}


class ScenarioError(ValueError):
    """A scenario the model can fix (unknown or illegal name, bad SP, contradictory goal); the message is
    returned to it as the tool result."""


class NotInGame(ScenarioError):
    """The dex refused a stated entity for this game (an item, move or ability Champions does not have,
    or a Mega without its own stone). That refusal is itself the grounded answer."""


@lru_cache(maxsize=1)
def nature_table() -> dict[str, tuple[str, str | None, str | None]]:
    """Every dex nature name and alias, lower-cased -> (canonical, raised stat, lowered stat); a neutral
    nature raises and lowers nothing."""
    from .. import mappers
    conn = mappers._conn()
    rows = {}
    for canonical, up, down in conn.execute("select canonical, up_stat, down_stat from natures"):
        up, down = (up or None), (down or None)
        rows[str(canonical)] = (str(canonical), None if up == down else up, None if up == down else down)
    out = {name.lower(): row for name, row in rows.items()}
    for alias, canonical in conn.execute("select alias, canonical from aliases where kind = 'nature'"):
        if alias and canonical in rows:
            out.setdefault(str(alias).strip().lower(), rows[canonical])
    return out


def nature_effect(nature: str | None, stat: str) -> int:
    row = nature_table().get(str(nature or "").lower())
    if not row:
        return 0
    return 1 if row[1] == stat else -1 if row[2] == stat else 0


def resolve_strict(pool: Any, name: str, kind: str, timeout: float) -> str | None:
    """Exact dex canonical for a Champions-legal entity, or None: the calc engine accepts names this game
    does not have (Choice Band), so a fuzzy pass-through would compute an impossible set."""
    doc = pool.request_json("dex", ["resolve", name, "--format", "json", "--kind", kind, "--strict"],
                            timeout=timeout)
    for entry in doc if isinstance(doc, list) else []:
        if isinstance(entry, dict) and entry.get("ok") and entry.get("canonical"):
            return str(entry["canonical"])
    return None


def apply_set(pool: Any, combatant: dict, spec: Any, side: str, timeout: float) -> dict:
    """`combatant` (the most-used set) with the explicit parts of `spec` replacing it. `sps` replaces the
    whole spread (an omitted stat is 0); `item` "none" removes the item."""
    if spec in (None, {}):
        return copy.deepcopy(combatant)
    if not isinstance(spec, dict):
        raise ScenarioError(f"{side}_set must be an object")
    out = copy.deepcopy(combatant)
    if "item" in spec:
        raw = str(spec.get("item") or "").strip()
        if raw.lower() in NO_ITEM:
            out.pop("item", None)
        else:
            item = resolve_strict(pool, raw, "item", timeout)
            if item is None:
                raise NotInGame(f"item {raw!r} is not in Pokemon Champions; use another item or none")
            out["item"] = item
    if spec.get("ability"):
        raw = str(spec["ability"]).strip()
        ability = resolve_strict(pool, raw, "ability", timeout)
        if ability is None:
            raise NotInGame(f"ability {raw!r} is not in Pokemon Champions")
        out["ability"] = ability
    if spec.get("nature"):
        row = nature_table().get(str(spec["nature"]).strip().lower())
        if row is None:
            raise ScenarioError(f"unknown nature {spec['nature']!r}")
        out["nature"] = row[0]
    if "sps" in spec:
        out["sps"] = check_sps(spec["sps"], side)
    if "boosts" in spec:
        boosts = spec["boosts"] or {}
        if not isinstance(boosts, dict):
            raise ScenarioError(f"{side}_set.boosts must be an object")
        clean = {}
        for key, value in boosts.items():
            if key not in BOOST_KEYS or not isinstance(value, int) or not -6 <= value <= 6:
                raise ScenarioError(f"{side}_set.boosts: stages are atk/def/spa/spd/spe integers -6..6")
            if value:
                clean[key] = value
        out["boosts"] = clean
    if spec.get("status"):
        status = str(spec["status"]).strip()
        match = next((s for s in STATUSES if s.lower() == status.lower()), None)
        if match is None:
            raise ScenarioError(f"{side}_set.status must be one of {', '.join(STATUSES)}")
        out["status"] = match
    return out


def check_sps(sps: Any, side: str) -> dict[str, int]:
    if not isinstance(sps, dict):
        raise ScenarioError(f"{side}_set.sps must be an object")
    out = {}
    for key, value in sps.items():
        if key not in SPREAD_KEYS or not isinstance(value, int) or not 0 <= value <= SP_MAX:
            raise ScenarioError(f"{side}_set.sps: SP are hp/atk/def/spa/spd/spe integers 0..{SP_MAX}")
        out[key] = value
    if sum(out.values()) > SP_TOTAL:
        raise ScenarioError(f"{side}_set.sps totals {sum(out.values())}; at most {SP_TOTAL} SP in total")
    return out


def field_payload(fmt: str, field: Any) -> dict:
    """The calc engine's field object from the tool's flat field options."""
    if field in (None, {}):
        return {"format": fmt}
    if not isinstance(field, dict):
        raise ScenarioError("field must be an object")
    out: dict[str, Any] = {"format": fmt}
    weather = str(field.get("weather") or "").strip().lower()
    if weather:
        if weather not in WEATHERS:
            raise ScenarioError(f"field.weather must be one of {', '.join(WEATHERS)}")
        out["weather"] = WEATHERS[weather]
    terrain = str(field.get("terrain") or "").strip().lower()
    if terrain:
        if terrain not in TERRAINS:
            raise ScenarioError(f"field.terrain must be one of {', '.join(TERRAINS)}")
        out["terrain"] = TERRAINS[terrain]
    defender_side = {k: True for k in SCREENS if field.get(k)}
    if defender_side:
        out["defenderSide"] = defender_side
    if field.get("helping_hand"):
        out["attackerSide"] = {"helping_hand": True}
    return out


def calc_payload(attacker: dict, defender: dict, move: str, field: dict, crit: bool = False) -> dict:
    attacker = {k: v for k, v in attacker.items() if not k.startswith("_")}
    defender = {k: v for k, v in defender.items() if not k.startswith("_")}
    if crit:
        attacker["moves"] = [{"name": move, "isCrit": True}]
    return {"attacker": attacker, "defender": defender, "move": move, "field": field}


def one_hit(doc: dict) -> tuple[list[int], int]:
    rolls = [int(d) for d in doc.get("damage") or [0]]
    return rolls, int(doc.get("defenderHP") or 0)


def goal_chance(doc: dict, goal: str) -> float:
    """Chance (0..1) that the goal holds for this calc, from the damage rolls."""
    rolls, hp = one_hit(doc)
    if hp <= 0:
        return 0.0
    if goal in ("ohko", "survive"):
        ko = sum(r >= hp for r in rolls) / len(rolls)
    else:
        ko = sum(a + b >= hp for a in rolls for b in rolls) / (len(rolls) ** 2)
    return ko if goal in ("ohko", "2hko") else 1.0 - ko


def met(chance: float, probability: str) -> bool:
    return chance >= 1.0 if probability == "guaranteed" else chance > 0.0


def summarize(doc: dict, goal: str) -> dict:
    """One SP point's result; the chance is named for what it measures, so a KO chance is never read as a
    survival chance."""
    key = "survive_chance_pct" if goal.startswith("survive") else "ko_chance_pct"
    return {"damage": f"{doc.get('min')}-{doc.get('max')}", "hp": doc.get("defenderHP"),
            "pct": f"{doc.get('minPercent')}-{doc.get('maxPercent')}%",
            key: round(100 * goal_chance(doc, goal), 2)}


GOAL_WORDS = {"ohko": "OHKO", "2hko": "2HKO", "survive": "survive one hit", "survive_2": "survive two hits"}


def _lane_name(lane: dict) -> str:
    return f"{lane.get('nature') or 'its nature'}, {lane.get('item') or 'no item'}"


def conclusions(solution: dict) -> list[str]:
    """One plain English line per lane, built from the numbers, for the model to restate rather than reread
    the arrays (the 10-25 eval: a small model swapped KO and survival chances and mixed up SP points)."""
    goal, sure = solution["goal"], solution.get("probability") == "guaranteed"
    lines = []
    for lane in solution["lanes"]:
        name = _lane_name(lane)
        if goal == "outspeed":
            target = solution["target"]
            if lane["min_sp"] is None:
                lines.append(f"{name}: no Spe SP outspeeds {target['name']} ({target['speed']}); 32 SP reaches "
                             f"{lane['speed_at_32']}.")
            else:
                tie = f"; {lane['tie_sp']} SP only ties" if lane.get("tie_sp") is not None else ""
                lines.append(f"{name}: {lane['min_sp']} Spe SP ({lane['speed_at_min']}) outspeeds {target['name']} "
                             f"({target['speed']}){tie}.")
            continue
        words = GOAL_WORDS[goal]
        if solution["solve_for"] == "defender.bulk":
            best = lane["at_max"]
            if lane.get("min_total_sp") is None:
                lines.append(f"{name}: no HP/{solution['stat']} SP split {'guarantees to ' if sure else 'can '}{words}; "
                             f"at 32/32 the survival chance is {best['survive_chance_pct']}% ({best['damage']} vs "
                             f"{best['hp']} HP).")
            else:
                lines.append(f"{name}: the fewest SP is {lane['min_total_sp']} ({lane['hp']} HP + "
                             f"{lane[solution['stat']]} {solution['stat']}) to {'surely ' if sure else ''}{words}.")
            continue
        stat = solution["solve_for"].split(".")[1]
        top, chance_key = lane["at_32"], next(k for k in lane["at_32"] if k.endswith("_chance_pct"))
        if top["damage"] == "0-0":
            lines.append(f"{name}: {solution['move']} deals no damage to the target at any SP (it is immune), so "
                         f"no {stat} SP can {words}." if goal in ("ohko", "2hko") else
                         f"{name}: {solution['move']} deals no damage (the target is immune), so it always survives.")
            continue
        if lane["min_sp"] is None:
            first = lane.get("first_sp_with_any_ko_chance")
            extra = f"; the first chance appears at {first} SP" if first is not None else ""
            lines.append(f"{name}: no {stat} SP {'guarantees the' if sure else 'gives any'} {words}; at 32 SP the "
                         f"{chance_key.replace('_pct', '').replace('_', ' ')} is {top[chance_key]}% ({top['damage']} vs "
                         f"{top['hp']} HP){extra}.")
        else:
            at = lane.get("at_min") or (lane["at_0"] if lane["min_sp"] == 0 else top)
            lines.append(f"{name}: {lane['min_sp']} {stat} SP {'guarantees the' if sure else 'gives a chance to'} "
                         f"{words} ({at['damage']} vs {at['hp']} HP).")
    return lines


def lanes_for(side_set: dict, stat: str, compare_items: list[str | None]) -> list[tuple[str | None, str | None]]:
    """(nature, item) lanes: the set's own nature, the nature raising the solved stat and a neutral one
    (deduplicated by their effect on that stat), each with the set's item and every compared item."""
    natures, effects = [], set()
    own = side_set.get("nature")
    candidates = [own] + ([PLUS_NATURE[stat], NEUTRAL_NATURE] if stat in PLUS_NATURE else [])
    for nature in candidates:
        effect = nature_effect(nature, stat) if stat in PLUS_NATURE else 0
        if nature is None and stat in PLUS_NATURE:
            continue
        if effect not in effects:
            effects.add(effect)
            natures.append(nature)
    items = [side_set.get("item")] + [i for i in compare_items if i != side_set.get("item")]
    return [(n, i) for n in (natures or [own]) for i in items]


def solve(run_calcs: Callable[[list[dict]], list[dict]], run_speeds: Callable[[list[dict]], list[dict]],
          attacker: dict, defender: dict, move: str, field: dict, *, solve_for: str, goal: str,
          probability: str = "guaranteed", compare_items: list[str | None] = (), crit: bool = False) -> dict:
    """The fewest SP on `solve_for` that reaches `goal`, per nature/item lane, with the result at 0 and 32 SP
    so a cliff that cannot be reached still says how close the best spread gets."""
    if solve_for not in SOLVE_FOR or goal not in GOALS or probability not in ("guaranteed", "any"):
        raise ScenarioError(f"solve_for must be one of {', '.join(SOLVE_FOR)}; goal one of {', '.join(GOALS)}; "
                            "probability guaranteed or any")
    side, stat = solve_for.split(".")
    if (goal == "outspeed") != (stat == "spe"):
        raise ScenarioError("outspeed is solved on a spe stat, and a spe stat only for outspeed")
    if goal in ("ohko", "2hko") and side != "attacker" or goal.startswith("survive") and side != "defender":
        raise ScenarioError("KO goals are solved on the attacker, survive goals on the defender")
    sets = {"attacker": attacker, "defender": defender}
    if goal == "outspeed":
        return _solve_speed(run_speeds, sets, side, compare_items)
    probe = run_calcs([calc_payload(attacker, defender, move, field, crit)])[0]
    if probe.get("error"):
        raise ScenarioError(str(probe["error"]))
    if stat == "bulk":
        stat = "def" if probe.get("category") == "Physical" else "spd"
        return _solve_bulk(run_calcs, sets, move, field, crit, goal, probability, stat, compare_items, probe)
    lanes = []
    for nature, item in lanes_for(sets[side], stat, list(compare_items)):
        trial = copy.deepcopy(sets[side])
        if nature:
            trial["nature"] = nature
        if item:
            trial["item"] = item
        else:
            trial.pop("item", None)
        payloads = []
        for sp in range(SP_MAX + 1):
            point = copy.deepcopy(trial)
            point["sps"] = {**(trial.get("sps") or {}), stat: sp}
            pair = {**sets, side: point}
            payloads.append(calc_payload(pair["attacker"], pair["defender"], move, field, crit))
        docs = run_calcs(payloads)
        chances = [goal_chance(d, goal) for d in docs]
        reached = next((sp for sp, c in enumerate(chances) if met(c, probability)), None)
        lane = {"nature": trial.get("nature"), "item": trial.get("item"), "min_sp": reached,
                "at_0": summarize(docs[0], goal), "at_32": summarize(docs[-1], goal)}
        if reached is not None and reached not in (0, SP_MAX):
            lane["at_min"] = summarize(docs[reached], goal)
        if probability == "guaranteed" and goal in ("ohko", "2hko"):
            lane["first_sp_with_any_ko_chance"] = next((sp for sp, c in enumerate(chances) if c > 0), None)
        lanes.append(lane)
    return _finish({"solve_for": solve_for, "goal": goal, "probability": probability, "move": move,
                    "category": probe.get("category"), "lanes": lanes,
                    "note": f"min_sp = fewest SP on {stat} (0-32) for the goal; null = not reachable even at 32. "
                            "Other stats keep the stated set. Residual damage and hazards not modelled."})


def _finish(solution: dict) -> dict:
    return {"conclusion": conclusions(solution), **solution}


def _solve_bulk(run_calcs, sets, move, field, crit, goal, probability, stat, compare_items, probe) -> dict:
    """Fewest HP + defence SP for a survive goal: for every HP value a binary search on the defence stat,
    all HP values in one batch per step (survival only grows with either stat)."""
    if not goal.startswith("survive"):
        raise ScenarioError("defender.bulk is solved for survive goals")
    lanes = []
    for nature, item in lanes_for(sets["defender"], stat, list(compare_items)):
        trial = copy.deepcopy(sets["defender"])
        if nature:
            trial["nature"] = nature
        if item:
            trial["item"] = item
        else:
            trial.pop("item", None)

        def payload(hp: int, d: int) -> dict:
            point = copy.deepcopy(trial)
            point["sps"] = {**(trial.get("sps") or {}), "hp": hp, stat: d}
            return calc_payload(sets["attacker"], point, move, field, crit)
        low, high = [0] * (SP_MAX + 1), [SP_MAX + 1] * (SP_MAX + 1)   # high: first known success (33 = none)
        top = run_calcs([payload(hp, SP_MAX) for hp in range(SP_MAX + 1)])
        for hp, doc in enumerate(top):
            if not met(goal_chance(doc, goal), probability):
                low[hp] = SP_MAX + 1
            else:
                high[hp] = SP_MAX
        while True:
            open_hp = [hp for hp in range(SP_MAX + 1) if low[hp] < high[hp]]
            if not open_hp:
                break
            mids = {hp: (low[hp] + high[hp]) // 2 for hp in open_hp}
            docs = run_calcs([payload(hp, mids[hp]) for hp in open_hp])
            for hp, doc in zip(open_hp, docs):
                if met(goal_chance(doc, goal), probability):
                    high[hp] = mids[hp]
                else:
                    low[hp] = mids[hp] + 1
        options = [(hp + high[hp], hp, high[hp]) for hp in range(SP_MAX + 1) if high[hp] <= SP_MAX]
        best = min(options, default=None, key=lambda o: (o[0], -o[1]))
        lane = {"nature": trial.get("nature"), "item": trial.get("item"),
                "at_max": summarize(top[-1], goal)}
        if best:
            lane.update(min_total_sp=best[0], hp=best[1], **{stat: best[2]},
                        min_def_at_hp32=high[SP_MAX] if high[SP_MAX] <= SP_MAX else None)
        else:
            lane["min_total_sp"] = None
        lanes.append(lane)
    return _finish({"solve_for": "defender.bulk", "stat": stat, "goal": goal, "probability": probability,
                    "move": move, "category": probe.get("category"), "lanes": lanes,
                    "note": f"min_total_sp = fewest HP+{stat} SP for the goal (null = unreachable even at 32/32; "
                            "at_max is the 32/32 result). Residual damage and hazards not modelled."})


def _solve_speed(run_speeds, sets, side, compare_items) -> dict:
    other = "defender" if side == "attacker" else "attacker"
    target_doc = run_speeds([speed_item(sets[other])])[0]
    target = target_doc.get("finalSpeed")
    if not isinstance(target, int):
        raise ScenarioError("the other side's speed could not be computed")
    lanes = []
    for nature, item in lanes_for(sets[side], "spe", list(compare_items)):
        trial = copy.deepcopy(sets[side])
        if nature:
            trial["nature"] = nature
        if item:
            trial["item"] = item
        else:
            trial.pop("item", None)
        docs = run_speeds([speed_item({**trial, "sps": {**(trial.get("sps") or {}), "spe": sp}})
                           for sp in range(SP_MAX + 1)])
        speeds = [d.get("finalSpeed") for d in docs]
        reached = next((sp for sp, s in enumerate(speeds) if isinstance(s, int) and s > target), None)
        tie = next((sp for sp, s in enumerate(speeds) if s == target), None)
        lanes.append({"nature": trial.get("nature"), "item": trial.get("item"), "min_sp": reached,
                      "speed_at_min": speeds[reached] if reached is not None else None,
                      "tie_sp": tie, "speed_at_0": speeds[0], "speed_at_32": speeds[-1]})
    return _finish({"solve_for": f"{side}.spe", "goal": "outspeed",
                    "target": {"name": sets[other].get("name"), "speed": target}, "lanes": lanes,
                    "note": "min_sp = fewest Spe SP to move strictly first (null = not reachable at 32); tie_sp = SP "
                            "that only ties. Tailwind, Trick Room, priority and speed abilities not modelled."})


def speed_item(combatant: dict, field: dict | None = None) -> dict:
    out = {"name": combatant["name"]}
    for key in ("nature", "item", "ability"):
        if combatant.get(key):
            out[key] = combatant[key]
    spe = (combatant.get("sps") or {}).get("spe")
    out["sps"] = {"spe": int(spe or 0)}
    if (combatant.get("boosts") or {}).get("spe"):
        out["boosts"] = {"spe": combatant["boosts"]["spe"]}
    if combatant.get("status") and combatant["status"] != "Healthy":
        out["status"] = combatant["status"]
    if field:
        out["field"] = dict(field)
    return out


def drop_unstated_speed_item(combatant: dict, spec: Any, side: str) -> str | None:
    """A stated nature/SP without an item describes that build without a Speed item; returns the note."""
    if not isinstance(spec, dict) or "item" in spec or not (spec.get("nature") or "sps" in spec):
        return None
    if combatant.get("item") not in SPEED_ITEMS:
        return None
    item = combatant.pop("item")
    return (f"{side}: the stated nature/SP describe a build without a Speed item, so its usual {item} "
            "is not assumed; state the item to include it")
