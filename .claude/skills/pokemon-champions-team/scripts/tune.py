#!/usr/bin/env python
"""tune operator: explicit SP benchmark frontiers.

Threat targets come only from `build-context.benchmarks`. Each card preserves the requested
benchmark as its primary objective, reports factual adjacent lanes (probability, allocation,
conditions, nature, or target-set ceiling), and exposes the 66-SP funding trade-off. It never
auto-selects threats, combines unrelated objectives into a score, or emits a single "optimal spread".

All external lookups are injected (damage_fn / move_fn / dex_fn / meta_fn) so the logic is
unit-testable without the sibling skills; defaults wire to ncplink + dexlink + metalink.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cliffs import (  # noqa: E402
    SP_CAP, champ_speed, solve_outspeed, solve_min_sp, survival_prob, survival_prob_hits,
    meets_target, ko_roll,
    candidate_natures, weather_speed_mult, effective_speed,
)
from typechart import effectiveness  # noqa: E402
from rules import get_ruleset  # noqa: E402
from diagnose import _stat_orientation  # noqa: E402  (reuse the base-stat offense lean)
from mega import (  # noqa: E402
    effective_form_ability, effective_member_from_maps, mega_form_from_maps,
)
from battle_effects import INTIMIDATE_BLOCKING_ITEMS, INTIMIDATE_NO_RELIEF_ABILITIES  # noqa: E402
import completeness  # noqa: E402
import team_i18n as i18n  # noqa: E402
from stat_keys import canonical_spread, engine_spread, ENGINE_STATS  # noqa: E402

SP_TOTAL_CAP = get_ruleset().sp_total_cap   # centralized registration constant (rules.py)
SPREAD_TO_SPS = ENGINE_STATS
SPS_TO_SPREAD = {v: k for k, v in SPREAD_TO_SPS.items()}
DEF_SPS = {"physical": "df", "special": "sd"}        # which defensive stat a move pressures
DEF_SPREAD = {"physical": "def", "special": "spd"}   # the same stat as a team-json spread key
# Special-category moves that deal damage against the PHYSICAL defense (Def), not SpD. Category alone
# picks the wrong defensive stat for these, so survival tuning would invest SpD and never move the
# cliff (audit 2026-06-24). The ncp calc already models the damage correctly; this only steers which
# stat the SP search varies.
PHYS_DEF_SPECIAL_MOVES = {"Psyshock", "Psystrike", "Secret Sword"}
# Moves whose damage depends on the defender's current/max HP (or is otherwise not flat per hit), so
# the HP lane's "incoming damage is HP-independent" reuse is invalid — adding HP doesn't create a
# normal survival cliff against them (audit 2026-06-24). Fixed-damage moves (Night Shade/Seismic Toss)
# are HP-independent and stay fine; only HP-proportional ones are excluded here.
# Damage scales with the DEFENDER's HP (so adding HP doesn't form a normal survival cliff). NOTE:
# Final Gambit is NOT here — it deals the ATTACKER's current HP, independent of the defender's, so the
# defender's HP lane is valid (audit 2026-06-24). Endeavor stays: it sets the target to the attacker's
# HP, i.e. damage = defender_HP - attacker_HP, which does scale with the defender's HP.
HP_DEPENDENT_MOVES = {"Super Fang", "Ruination", "Nature's Madness", "Guardian of Alola", "Endeavor"}
OFF_SPS = {"physical": "at", "special": "sa"}
OFF_SPREAD = {"physical": "atk", "special": "spa"}    # offensive stat as a team-json spread key
OFF_NATURE = {"physical": "Adamant", "special": "Modest"}
KILL_HITS = {"ohko": 1, "2hko": 2}                    # benchmark kind -> hits the KO needs
BOOST_KEYS = ("atk", "def", "spa", "spd", "spe")       # stat stages a member_state may set
NATURE_LANE_MIN_SAVINGS = 1   # a meta-grounded improvement is a fact; opportunity cost stays explicit

# --- Mega form + phase-specific ability judgments (design §9) ------------------------------
# Damage and post-evolution Speed use the effective Mega form and Mega ability. Switch-in effects
# use the pre-Mega form and base ability. Effects that can occur on either side of evolution (for
# example Intimidate) explicitly inspect both phases; this is never a blanket ability union.
SPECIAL_BASE_ABILITY_USAGE_FLOOR = 20.0

# Back-compatible public name; the shared constant mirrors the calculator's Intimidate handler.
INTIMIDATE_IMMUNE_ABILITIES = INTIMIDATE_NO_RELIEF_ABILITIES

def _ability_key(ability: Any) -> str:
    return str(ability or "").strip().casefold()


def _memoized_batch_lookup(fn: Callable | None) -> Callable | None:
    """Cache a sibling batch lookup for the duration of one tune() call."""
    if fn is None:
        return None
    cache: dict[Any, dict[str, Any]] = {}

    def wrapped(names: list[str]) -> dict[str, Any]:
        req = [n for n in (names or []) if n is not None]
        missing = [n for n in req if n not in cache]
        if missing:
            got = fn(missing) or {}
            for n in missing:
                cache[n] = got.get(n, {}) or {}
        return {n: cache.get(n, {}) for n in req}

    return wrapped


def _memoized_meta_lookup(fn: Callable | None) -> Callable | None:
    if fn is None:
        return None
    cache: dict[tuple[str | None, str | None], dict[str, Any] | None] = {}

    def wrapped(species: str | None, fmt: str | None) -> dict[str, Any] | None:
        key = (species, fmt)
        if key not in cache:
            cache[key] = fn(species, fmt)
        return cache[key]

    return wrapped


def _dex_facts(name: str | None, dex_fn: Callable) -> dict[str, Any]:
    if not name:
        return {}
    return (dex_fn([name]) or {}).get(name, {}) or {}


def _base_form_name(name: str | None, dex_fn: Callable,
                    facts: dict[str, Any] | None = None) -> str | None:
    """Return the pre-Mega/base species for a dex-canonical name when the dex exposes it."""
    if not name:
        return None
    facts = facts if facts is not None else _dex_facts(name, dex_fn)
    if facts.get("is_mega") and facts.get("base_species"):
        return facts.get("base_species")
    return name


def _base_form_facts(name: str | None, dex_fn: Callable,
                     facts: dict[str, Any] | None = None) -> tuple[str | None, dict[str, Any]]:
    base = _base_form_name(name, dex_fn, facts)
    if not base:
        return None, {}
    if base == name and facts is not None:
        return base, facts
    bfacts = _dex_facts(base, dex_fn)
    return base, bfacts or (facts or {})


def _norm_field_token(value: Any, mapping: dict[str, str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    key = raw.lower().replace("_", " ").replace("-", " ")
    key = key.removesuffix(" terrain").strip()
    return mapping.get(key) or raw[:1].upper() + raw[1:]


def _norm_weather(value: Any) -> str | None:
    return _norm_field_token(value, {
        "rain": "Rain", "sun": "Sun", "sand": "Sand", "sandstorm": "Sand",
        "snow": "Snow", "hail": "Snow", "harsh sun": "Harsh Sun",
        "heavy rain": "Heavy Rain", "strong winds": "Strong Winds",
    })


def _norm_terrain(value: Any) -> str | None:
    return _norm_field_token(value, {
        "electric": "Electric", "grassy": "Grassy", "psychic": "Psychic", "misty": "Misty",
    })


def _mega_form_name(name: str | None, item: str | None, dex_fn: Callable,
                    item_fn: Callable | None, *, facts: dict[str, Any] | None = None) -> str | None:
    """The Mega form `name` becomes while holding `item` (a Mega stone), or None. A species already
    given AS a Mega resolves to itself. Otherwise the item's `required_by` mapping (item_fn) is matched
    to the base species — never a guessed 'Mega X' string (X/Y forms need the exact stone). `facts` may
    pass the already-fetched dex facts for `name` to save a lookup. A single required_by candidate is a
    fallback ONLY while its base_species is unknown — a known, mismatched base means the stone belongs
    to a DIFFERENT species (a wrong-stone member must not resolve into someone else's Mega)."""
    if not name:
        return None
    if facts is None:
        facts = _dex_facts(name, dex_fn)
    if not (item and item_fn):
        return name if facts.get("is_mega") else None
    item_info = item_fn([item]) or {}
    forms = ((item_info.get(item, {}) or {}).get("required_by") or [])
    form_facts = {f: _dex_facts(f, dex_fn) for f in forms}
    return mega_form_from_maps(name, item, facts, item_info, form_facts)


def _form_record(name: str | None, item: str | None, ability: str | None, dex_fn: Callable,
                 item_fn: Callable | None, *, run_form: str | None = None,
                 base_ability: str | None = None) -> dict[str, Any]:
    """Resolve the effective calc form plus the switch-in/base facts.

    This is the single tune-side Mega/run_form resolver. Members and opponent sets differ only in
    whether a `run_form` is present and whether a caller supplied a known pre-Mega `base_ability`; the
    shape consumed downstream is identical.
    """
    facts = _dex_facts(name, dex_fn)
    if facts.get("is_mega"):
        base_name, base_facts = _base_form_facts(name, dex_fn, facts)
        mega_abilities = list(facts.get("abilities") or [])
        base_abilities = set(base_facts.get("abilities") or [])
        mega_ability = ability if ability in mega_abilities else (
            mega_abilities[0] if mega_abilities else ability)
        resolved_base_ability = base_ability or (ability if ability in base_abilities else None)
        return {"name": name, "ability": mega_ability, "base_ability": resolved_base_ability,
                "mega_ability": mega_ability, "is_mega": True, "facts": facts,
                "base_facts": base_facts or facts, "base_name": base_name or name}

    if run_form and run_form != name:
        rfacts = _dex_facts(run_form, dex_fn)
        base_name, base_facts = _base_form_facts(run_form, dex_fn, rfacts)
        run_abilities = list(rfacts.get("abilities") or [])
        base_abilities = set(base_facts.get("abilities") or [])
        # Real-team/meta run_form sets usually carry the actually-run ability. Preserve it if present;
        # otherwise fall back to the run form's first ability.
        resolved = effective_form_ability(ability, rfacts)
        resolved_base_ability = base_ability or (ability if ability in base_abilities else None)
        return {"name": run_form, "ability": resolved, "base_ability": resolved_base_ability,
                "mega_ability": resolved if rfacts.get("is_mega") else None,
                "is_mega": bool(rfacts.get("is_mega")), "facts": rfacts,
                "base_facts": base_facts or rfacts, "base_name": base_name or name}

    base_facts = facts
    mform = _mega_form_name(name, item, dex_fn, item_fn, facts=base_facts)
    if mform and mform != name:
        mfacts = _dex_facts(mform, dex_fn)
        actor = effective_member_from_maps(
            {"species": name, "item": item, "ability": ability},
            {name: base_facts, mform: mfacts},
            {item: {"required_by": [mform]}} if item else {},
        )
        mega_ability = actor.get("ability")
        return {"name": mform, "ability": mega_ability,
                "base_ability": base_ability or ability, "mega_ability": mega_ability,
                "is_mega": True, "facts": mfacts, "base_facts": base_facts,
                "base_name": name}
    return {"name": name, "ability": ability, "base_ability": base_ability or ability,
            "mega_ability": None, "is_mega": False, "facts": base_facts, "base_facts": base_facts,
            "base_name": name}


def _effective_form(member: dict[str, Any], dex_fn: Callable,
                    item_fn: Callable | None) -> dict[str, Any]:
    """Resolve a member to the FORM used in the damage/speed math: the MEGA form when it holds a
    matching stone (Mega stats + Mega ability), else the base form. Returns
    {name, ability, base_ability, mega_ability, is_mega, facts, base_facts} — `name`/`ability` feed the
    ncp calc, `base_ability` is the team-json/pre-Mega ability (for switch-in special checks), `facts`
    are the resolved form's dex facts (types/stats), and `base_facts` are the PRE-Mega form's (entry
    hazards resolve at switch-in, before Mega Evolution, so hazard typing reads the base form)."""
    return _form_record(member.get("species"), member.get("item"), member.get("ability"),
                        dex_fn, item_fn, base_ability=member.get("base_ability"))


def _calc_member(member: dict[str, Any], eff: dict[str, Any]) -> dict[str, Any]:
    """The member dict with species/ability overwritten to the effective (Mega) form for the ncp calc;
    spread/item/nature/moves stay as authored."""
    return {**member, "species": eff["name"], "ability": eff["ability"]}


def _special_ability_present(specials: set[str], resolved_ability: str | None,
                             base_ability: str | None, base_pct: float | None,
                             *, base_gated: bool) -> bool:
    """Whether any ability in `specials` is present in a phase where the effect can apply. The
    resolved (in-calc, post-Mega for a stone holder) ability counts UNCONDITIONALLY — it IS the set the
    damage math runs, so gating it would contradict the calc on the same card. The pre-Mega base
    ability counts unconditionally when declared/observed (an explicit set, a real-team joint set, or
    the member's own team-json: `base_gated=False`), and only at >= the usage floor when it comes from
    a meta marginal (`base_gated=True`, `base_pct` its usage %)."""
    special_keys = {_ability_key(a) for a in specials}
    if _ability_key(resolved_ability) in special_keys:
        return True
    if _ability_key(base_ability) in special_keys:
        if not base_gated:
            return True
        if (base_pct or 0.0) >= SPECIAL_BASE_ABILITY_USAGE_FLOOR:
            return True
    return False


def _invested_stats(spread: dict[str, Any] | None) -> set[str]:
    """Spread (non-HP) stats that carry SP — the 'in use' set for the nature in-use filter (§16.8)."""
    return {k for k, v in (spread or {}).items() if k in ("atk", "def", "spa", "spd", "spe") and v}


def _reallocation_plan(delta: int, target_stat: str | set[str],
                       base_sps: dict[str, int]) -> dict[str, Any] | None:
    """Describe how a target-stat increase fits the 66-SP registration budget.

    A legal full spread is not a dead end: it can fund a cliff by moving SP from another invested
    stat. Donors are candidates, not an automatic spread recommendation; every donor carries an
    opportunity cost that the caller must compare with its other explicit benchmarks.
    """
    spent = sum(base_sps.values())
    unused = max(0, SP_TOTAL_CAP - spent)
    required = max(0, delta - unused)
    if required == 0:
        return None
    target_stats = {target_stat} if isinstance(target_stat, str) else set(target_stat)
    donors = [
        {"stat": SPS_TO_SPREAD.get(stat, stat), "current_sp": amount,
         "opportunity_cost": f"reducing {SPS_TO_SPREAD.get(stat, stat)} may give up another benchmark"}
        for stat, amount in base_sps.items()
        if stat not in target_stats and amount > 0
    ]
    donors.sort(key=lambda row: (-row["current_sp"], row["stat"]))
    return {
        "required_sp": required,
        "available_sp": sum(row["current_sp"] for row in donors),
        "unused_sp": unused,
        "candidate_donors": donors,
        "note": "candidate donors are not an automatic spread; re-check every affected benchmark",
    }


def _outcome(min_total: int | None, cur: int, base_sps: dict[str, int],
             target_stat: str) -> dict[str, Any]:
    """Normalise a solved min-SP into a budget-aware cliff verdict.

    The target-stat minimum is a one-dimensional fact. Budget funding is reported separately:
    unused SP first, then an explicit reallocation envelope over other invested stats.
    """
    if min_total is None:
        return {"result": "unreachable", "delta_sp": SP_CAP + 1}
    delta = max(0, min_total - cur)
    if delta == 0:
        return {"result": "already", "delta_sp": 0, "need_total": min_total}
    plan = _reallocation_plan(delta, target_stat, base_sps)
    out = {"result": "cliff", "delta_sp": delta, "need_total": min_total}
    if plan is not None:
        out["reallocation"] = plan
        if plan["required_sp"] > plan["available_sp"]:
            out["result"] = "infeasible"
    return out


def _nature_lanes(target_spread_stat: str, current_nature: str | None, cur: int,
                  base_sps: dict[str, int], baseline: dict[str, Any], *, solve_min_fn: Callable,
                  invested: set[str], offense_lean: str | None, meta_natures: set[str]) -> dict[str, Any]:
    """Build the §16.8 nature-lane attachment for one cliff. `solve_min_fn(nature)->min_total|None`
    re-solves the EXISTING 1-D SP cliff under a different nature (no new search dimension). Only
    `propose` lanes are solved; a lane is kept when it unlocks an unreachable/infeasible baseline
    or saves any positive SP. Returns {alternatives, notes, unlock} —
    NEVER a recommended (nature, SP); the caller attaches it as a sub-field, out of the head ranking.
    `meta_natures` is the REALITY GATE (natures real players run on this species, ~2%+): empty -> no
    lanes at all (an off-role nature nobody runs is not a real option)."""
    if not meta_natures:
        return {"alternatives": [], "notes": [], "unlock": False}
    lanes = candidate_natures(target_spread_stat, current_nature, invested_stats=invested,
                              offense_lean=offense_lean, meta_natures=meta_natures)
    base_cost = baseline["delta_sp"]
    base_reachable = baseline["result"] in ("cliff", "already")
    kept: list[dict[str, Any]] = []
    notes: list[str] = []
    unlock = False
    for L in lanes:
        if L["status"] != "propose":          # summarize / locked -> a compact note, not a solved lane
            notes.append(f"{L['nature']} ({L['reason']}) — not auto-proposed")
            continue
        oc = _outcome(solve_min_fn(L["nature"]), cur, base_sps,
                      SPREAD_TO_SPS[target_spread_stat])
        lane_reachable = oc["result"] in ("cliff", "already")
        saves = base_cost - oc["delta_sp"]
        is_unlock = (not base_reachable) and lane_reachable
        if not (is_unlock or saves >= NATURE_LANE_MIN_SAVINGS):
            continue                           # no real impact -> drop (anti nature-creep)
        unlock = unlock or is_unlock
        kept.append({"nature": L["nature"], **oc, "saves_sp": saves, "unlock": is_unlock,
                     "plus_stat": L["plus_stat"], "penalty_stat": L["penalty_stat"],
                     "meta_pct": L.get("meta_pct"),   # carry the real usage % through to the lane (audit 2026-06-24)
                     "opportunity_cost": (f"-10% {L['penalty_stat']} (a whole-spread, single-slot commitment)"
                                          if L["penalty_stat"] else "neutral nature"),
                     "reason": L["reason"]})
    return {"alternatives": kept, "notes": notes, "unlock": unlock}


def _sps_from_spread(spread: dict[str, int] | None) -> dict[str, int]:
    out = {v: 0 for v in SPREAD_TO_SPS.values()}
    for k, v in canonical_spread(spread or {}).items():
        if k in SPREAD_TO_SPS:
            out[SPREAD_TO_SPS[k]] = int(v or 0)
    return out


def _explicit_set(config: dict[str, Any]) -> dict[str, Any]:
    config = dict(config)
    if isinstance(config.get("sps"), dict):
        config["sps"] = engine_spread(config["sps"])
    elif isinstance(config.get("spread"), dict):
        config["sps"] = _sps_from_spread(config["spread"])
    return config


def _member_ncp(member: dict[str, Any], sps: dict[str, int]) -> dict[str, Any]:
    out = {"name": member.get("species"), "ability": member.get("ability"),
           "item": member.get("item"), "nature": member.get("nature") or "Hardy", "sps": sps}
    # A survive benchmark's explicit `member_state` rides on its calc member (see `_survive_card`).
    # No other card attaches one, so outspeed and kill cliffs are never solved under a stated state.
    out.update(member.get("battle_state") or {})
    return out


def _state_assumption(state: dict[str, Any]) -> str:
    parts = ([f"status {state['status']}"] if state.get("status") else []) + [
        f"{k} {v:+d}" for k, v in (state.get("boosts") or {}).items()]
    return ("our member is solved in the stated battle state (" + ", ".join(parts) + "); "
            "the required SP holds only while that state does")


def _member_state(b: dict[str, Any]) -> dict[str, Any]:
    """The benchmark's explicit in-battle state for OUR member, in the calc's input vocabulary:
    `status` (calc status name) and `boosts` (canonical stat keys, -6..6). Neutral values are dropped
    so an all-default state and an absent one solve — and read back — identically."""
    raw = b.get("member_state")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    status = raw.get("status")
    if isinstance(status, str) and status and status != "Healthy":
        out["status"] = status
    boosts = raw.get("boosts") if isinstance(raw.get("boosts"), dict) else {}
    stages = {k: int(v) for k, v in boosts.items()
              if k in BOOST_KEYS and isinstance(v, int) and not isinstance(v, bool) and v}
    if stages:
        out["boosts"] = stages
    return out


def _attacker_ncp(species: str, category: str, attacker_set: dict[str, Any] | None,
                  meta_set: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve the threatening attacker and return (ncp_attacker, provenance).

    Precedence: an explicit caller `attacker_set` (user/benchmark) wins; otherwise the meta MODAL
    set (real ability/item/nature/spread — this is what stops Huge Power being silently dropped);
    otherwise a synthetic max-offense attacker that models NO ability/item, flagged low-confidence
    so a falsely "survivable" verdict is never presented as solid (audit 2026-06-21)."""
    if attacker_set:
        aset = _explicit_set(attacker_set)
        return ({"name": species, **aset},
                {"source": "explicit", "confidence": "high",
                 "note": "attacker set supplied by the caller"})
    if meta_set:
        base_ability = meta_set.get("base_ability") or (
            (((meta_set.get("set") or {}).get("ability") or {}).get("name"))
            if isinstance(meta_set.get("set"), dict) else None)
        source = meta_set.get("source") or "meta"
        return ({"name": species, "ability": meta_set.get("ability"), "item": meta_set.get("item"),
                 "nature": meta_set.get("nature") or "Hardy", "sps": meta_set.get("sps") or {},
                 "run_form": meta_set.get("run_form"), "base_ability": base_ability},
                {"source": source, "confidence": meta_set.get("confidence", "medium"),
                 "note": meta_set.get("note"), "prevalence": meta_set.get("prevalence")})
    off = OFF_SPS.get(category, "at")
    return ({"name": species, "ability": None, "item": None,
             "nature": OFF_NATURE.get(category, "Hardy"), "sps": {off: SP_CAP}},
            {"source": "synthetic", "confidence": "low",
             "note": "no meta set for this attacker; synthetic max-offense, abilities/items NOT "
                     "modelled — a real set (e.g. Huge Power) may hit far harder"})


def _variant_speed_lines(species: str, fmt: str | None, archetypes_fn: Callable | None,
                         dex_fn: Callable, item_fn: Callable | None,
                         conds: dict[str, Any]) -> list[dict[str, Any]]:
    """Each REAL build of `species` as its own speed line, with the share of that species' teams it
    accounts for.

    The primary card line uses the explicit or resolved set. These rows add alternative observed
    peaks without flattening them: "outspeed the Scarf build" and "outspeed the Mega build" are
    different investments with their own prevalence.

    Each build's item/nature/spread are its own, so no Scarf-vs-Mega guard is needed here: a stone
    holder simply never carries Scarf in real data. That is the same impossible-set problem the
    caller's `not target_is_mega` heuristic exists to dodge, dissolved by using real builds.
    """
    if archetypes_fn is None:
        return []
    try:
        arches = archetypes_fn(species, fmt) or []
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for a in arches:
        item = a.get("item")
        # A stone resolves the build to its Mega form: different base Speed AND a different ability.
        form = _form_record(species, item, a.get("ability"), dex_fn, item_fn)
        base = ((form.get("facts") or {}).get("stats") or {}).get("spe")
        if base is None:
            continue
        sps = a.get("sps") or {}
        spe_sp = int(sps.get("sp") or sps.get("spe") or 0)
        speed = effective_speed(base, spe_sp, a.get("nature"), item=item,
                                ability=form.get("ability"), weather=conds.get("weather"),
                                terrain=conds.get("terrain"))
        if speed is None:
            continue
        if conds.get("opponent_tailwind"):
            speed *= 2
        out.append({
            "clusters": [{"item": item, "ability": a.get("ability")}],
            "coverage": a.get("coverage"),
            "confidence": a.get("confidence"),
            "form": form.get("name"),
            "occupies_mega_slot": bool(form.get("is_mega")),
            "nature": a.get("nature"), "speed_sp": spe_sp, "speed": speed,
        })
    # Builds that land on the SAME speed are one line to clear, not several: two Mega archetypes
    # differing only in their pre-Mega ability fight at identical Speed, and listing them twice
    # invites reading one threshold as two. Merge them and SUM the coverage — the share of the
    # species sitting at that speed is the number that matters when deciding how much SP to spend.
    merged: dict[tuple[Any, int], dict[str, Any]] = {}
    for line in out:
        key = (line["form"], line["speed"])
        if key in merged:
            prev = merged[key]
            prev["clusters"].extend(line["clusters"])
            if prev.get("coverage") is not None and line.get("coverage") is not None:
                prev["coverage"] = round(prev["coverage"] + line["coverage"], 4)
        else:
            merged[key] = line
    return sorted(merged.values(), key=lambda ln: -ln["speed"])   # fastest real build first


def _meta_set(meta_fn: Callable | None, species: str, fmt: str | None,
              attacker_set: dict[str, Any] | None,
              dex_fn: Callable | None = None) -> dict[str, Any] | None:
    """Fetch the meta modal set, defensively: an explicit set skips it, and a meta outage
    falls through to the synthetic attacker rather than crashing the whole tune call."""
    if attacker_set or not meta_fn:
        return None
    names = [species]  # explicit forms cannot borrow another form's modal configuration
    for name in names:
        try:
            got = meta_fn(name, fmt)
        except Exception:
            got = None
        if got:
            return got
    return None


def _mega_resolve_attacker(attacker: dict[str, Any], dex_fn: Callable,
                           item_fn: Callable | None) -> dict[str, Any]:
    """Mega-resolve a meta attacker (§9): a stone item swaps it to its Mega form NAME + Mega
    ability (so Mega Staraptor hits as Contrary + Mega stats, Tough Claws boosts contact moves, etc.),
    keeping `base_ability` for the switch-in special check. Returns {**attacker, name, ability,
    base_ability, mega_ability}."""
    rec = _form_record(attacker.get("name"), attacker.get("item"), attacker.get("ability"),
                       dex_fn, item_fn, run_form=attacker.get("run_form"),
                       base_ability=attacker.get("base_ability"))
    return {**attacker, **rec}


def _meta_ability_pct(meta_set: dict[str, Any] | None) -> float | None:
    return ((((meta_set or {}).get("set") or {}).get("ability")) or {}).get("pct")


def _meta_ability_distribution(meta_set: dict[str, Any] | None) -> list[dict[str, Any]]:
    set_block = ((meta_set or {}).get("set") or {})
    if not isinstance(set_block, dict):
        return []
    rows: list[dict[str, Any]] = []
    for row in (set_block.get("abilities") or set_block.get("ability_distribution") or []):
        if isinstance(row, dict) and row.get("name"):
            rows.append({"name": row.get("name"), "pct": row.get("pct")})
    top = set_block.get("ability") or {}
    if isinstance(top, dict) and top.get("name"):
        key = _ability_key(top.get("name"))
        if all(_ability_key(r.get("name")) != key for r in rows):
            rows.append({"name": top.get("name"), "pct": top.get("pct")})
    return rows


def _meta_special_ability_pct(meta_set: dict[str, Any] | None, specials: set[str]) -> float | None:
    special_keys = {_ability_key(a) for a in specials}
    pcts: list[float] = []
    for row in _meta_ability_distribution(meta_set):
        if _ability_key(row.get("name")) not in special_keys:
            continue
        try:
            pcts.append(float(row.get("pct") or 0.0))
        except (TypeError, ValueError):
            pcts.append(0.0)
    return max(pcts) if pcts else None


def _base_ability_gated(prov: dict[str, Any], meta_set: dict[str, Any] | None) -> bool:
    # A real-team set is joint only for the form actually stored.  Doubles base+stone rows now carry
    # an explicit `base_ability`; singles often stores the Mega form itself, so its pre-Mega ability
    # still falls back to the independent meta marginal in `set.ability`.  Gate that fallback even
    # though the rest of the set is real-team-backed; otherwise a 5% base ability is treated as an
    # observed certainty merely because the Mega-form item/nature/moves are joint.
    if prov.get("source") == "explicit":
        return False
    return not bool((meta_set or {}).get("base_ability"))


def _hazard_abilities_for_modeled_set(actor: dict[str, Any], meta_set: dict[str, Any] | None,
                                      prov: dict[str, Any]) -> tuple[str, ...]:
    """Abilities active when entry hazards resolve.

    Hazards happen before Mega Evolution. A Mega-only Levitate/Magic Guard cannot retroactively
    cancel chip; conversely a declared pre-Mega immunity still applies even when the run form loses it.
    """
    base_ability = actor.get("base_ability")
    base_pct = _meta_ability_pct(meta_set)
    base_gated = _base_ability_gated(prov, meta_set)
    if actor.get("is_mega"):
        entry = (base_ability if base_ability and
                 (not base_gated or (base_pct or 0.0) >= SPECIAL_BASE_ABILITY_USAGE_FLOOR)
                 else None)
    else:
        entry = actor.get("ability")
    return (entry,) if entry else ()


def _attacker_intimidatable(attacker: dict[str, Any], base_pct: float | None,
                            base_special_pct: float | None = None,
                            *, base_gated: bool) -> bool:
    """Whether OUR Intimidate lowers this (physical) attacker's Attack: true unless an Intimidate-immune
    ability is present on the MODELLED set (§9) — the resolved (post-Mega) ability unconditionally (it
    is the very ability the damage calc runs), the pre-Mega base ability ungated for an explicit /
    real-team set and at >= the usage floor for a meta marginal."""
    if _special_ability_present(
        INTIMIDATE_IMMUNE_ABILITIES, attacker.get("ability"), attacker.get("base_ability"),
        base_pct, base_gated=base_gated):
        return False
    if str(attacker.get("item") or "").casefold() in INTIMIDATE_BLOCKING_ITEMS:
        return False
    # When the attacker is a pure meta marginal and the modal ability is not the immune one, keep a
    # distribution-level guard for meaningful rank-2 abilities (e.g. Defiant 45%). Real-team joint sets
    # are observed sets, so their actual ability above remains authoritative.
    if base_gated and (base_special_pct or 0.0) >= SPECIAL_BASE_ABILITY_USAGE_FLOOR:
        return False
    return True


def _member_completeness(member: dict[str, Any]) -> tuple[str, str | None]:
    """(confidence cap, note) for tuning off this member's set. Tuning off a set we don't fully
    know (unknown ability/nature/spread) is a guess, so cap confidence accordingly (audit point 8)."""
    has_moves = bool(member.get("moves"))
    lvl = member.get("completeness")
    if completeness.set_authoritative(lvl, has_moves=has_moves):
        # SP is judged separately from the set fields: a TAGGED authoritative member without a spread
        # has an UNKNOWN spread, and `_sps_from_spread` will read it as all-0 — a guess about its real
        # allocation, so drop to low and say so
        # explicitly (audit 2026-07-02). An untagged member is user-authored (absent = real 0 SP).
        if not completeness.spread_authoritative(lvl, has_spread=bool(member.get("spread"))):
            eff = completeness.effective_level(lvl, has_moves=has_moves)
            return "low", (f"member spread unavailable in source (completeness={eff}) — SP assumed 0; "
                           "cliffs are measured from an assumed-0 baseline")
        # Authoritative -> tune off it directly, but earn only its confidence floor: an observed full set
        # is high, a validated-but-reconstructed `extracted_set` is medium. Never claim high for
        # reverse-engineered data, and no "assumed" caveat (it's real, just lower-confidence) — audit 2026-07-01.
        return completeness.confidence_floor(lvl, has_moves=has_moves), None
    eff = completeness.effective_level(lvl, has_moves=has_moves)
    return (completeness.confidence_floor(lvl, has_moves=has_moves),
            f"member set not fully known (completeness={eff}); its ability/nature/spread may be assumed")


def _sr_chip(types: list[str], hp: int, *, abilities: tuple[str, ...] | set[str] = ()) -> int:
    """Stealth Rock chip = 1/8 * Rock effectiveness of max HP (type-based), floored. Entry hazards
    resolve at SWITCH-IN — before Mega Evolution — so `types` must be the PRE-Mega form's and
    `abilities` must contain only abilities active at that phase: any Magic Guard present -> 0 chip.
    Magic Guard is the only SR-negating ability modelled for now (Heavy-Duty Boots / Air Balloon-vs-SR
    are a deferred increment)."""
    if any((a or "").lower() == "magic guard" for a in abilities):
        return 0
    mult = effectiveness("Rock", [t for t in types if t])
    return int(hp * (1 / 8) * mult)


def _spikes_layers(conds: dict[str, Any]) -> int:
    raw = conds.get("spikes")
    if raw is True:
        return 1
    if raw is False or raw is None:
        return 0
    if isinstance(raw, int):
        return max(0, min(3, raw))
    return 0


def _spikes_chip(types: list[str], hp: int, layers: int, *,
                 abilities: tuple[str, ...] | set[str] = (), item: str | None = None) -> int:
    """Spikes chip on a grounded target. Same switch-in (pre-Mega) basis as _sr_chip: `types` are the
    PRE-Mega form's and `abilities` are those active before evolution."""
    if layers <= 0:
        return 0
    ability_ns = {(a or "").lower() for a in abilities}
    item_n = (item or "").lower()
    type_n = {t.lower() for t in types if t}
    if ability_ns & {"levitate", "eelevate", "magic guard"} or item_n in {"heavy-duty boots", "air balloon"}:
        return 0
    if "flying" in type_n:
        return 0
    if layers == 1:
        return max(1, hp // 8)
    if layers == 2:
        return hp // 6
    return hp // 4


def _screen_side(scr: Any, category: str) -> dict[str, bool]:
    """Map a `screens` condition to ncp's defenderSide flags. A bare truthy value picks the
    screen that blocks this category (Reflect vs physical, Light Screen vs special); an explicit
    string ('reflect' / 'light_screen' / 'aurora_veil') is honoured as given."""
    s = (scr if isinstance(scr, str) else "").replace("_", "").replace(" ", "").lower()
    if s in ("auroraveil", "veil"):
        return {"auroraVeil": True}
    if s == "reflect":
        return {"reflect": True}
    if s == "lightscreen":
        return {"lightScreen": True}
    return {"reflect": True} if category == "physical" else {"lightScreen": True}


def _damage_field(conds: dict[str, Any], category: str, fmt: str | None = None, *,
                  use_sr: bool | None = None, spikes: int | None = None) -> dict[str, Any]:
    """Translate explicit benchmark conditions into the ncp field schema (audit 2026-06-21).

    `fmt` sets `field.format` so the engine applies the DOUBLES spread-move reduction (0.75x on
    Earthquake / Rock Slide / Heat Wave ...) — omitting it silently ran every doubles calc in Singles,
    over-stating spread damage and flipping survival verdicts (re-audit 2026-07-07). `use_sr`/`spikes`
    override the raw-conds hazard read so the caller passes the GATED values (Stealth Rock is singles +
    team-carries + Magic-Guard-exempt; Spikes stays default-off) — pass None to fall back to conds.

    Conditions that the engine needs go here: weather (a named string), screens (defender-side
    Reflect / Light Screen / Aurora Veil), Stealth Rock, and Spikes. Entry hazards are residual chip, not a damage
    modifier — it leaves the raw `damage` rolls and `defenderHP` untouched (those are read by the
    static cliffs, which apply hazards themselves via `_sr_chip`/`_spikes_chip`/`eff_hp`) and only feeds the engine's
    recovery-aware `ko_chance`, so the 4d engine KO% accounts for the same hazard chip the static cliff does
    instead of silently reporting a no-SR number (audit 2026-06-27). It rides the DEFENDER side in
    both cliffs: the chipped Pokemon is always the ncp `defender` (survive: our member; kill: the
    target), and ncp reads SR from the side (`defenderSide.stealthRock` -> handlerSide.isSR), never
    a top-level field key. Tailwind / Trick Room don't change damage, so they live in the speed cliff.
    Conditions are applied only when the benchmark sets them explicitly or a documented team-carries
    lane supplies them; format alone never silently turns a condition on or off."""
    field: dict[str, Any] = {}
    if fmt:
        field["format"] = fmt
    weather = _norm_weather(conds.get("weather"))
    if weather:
        field["weather"] = weather
    terrain = _norm_terrain(conds.get("terrain"))
    if terrain:
        field["terrain"] = terrain
    def_side: dict[str, Any] = {}
    scr = conds.get("screens")
    if scr:
        def_side.update(_screen_side(scr, category))
    sr = bool(conds.get("stealth_rock")) if use_sr is None else use_sr
    if sr:
        def_side["stealthRock"] = True
    layers = _spikes_layers(conds) if spikes is None else spikes
    if layers:
        def_side["spikes"] = layers
    if def_side:
        field["defenderSide"] = def_side
    return field


def _survive_min_sp(member: dict[str, Any], nature: str, attacker: dict[str, Any], move: str,
                    field: dict[str, Any], dstat: str, base_sps: dict[str, int], target: str,
                    defender_types: list[str], use_sr: bool, spikes: int, *, damage_fn: Callable,
                    damage_batch_fn: Callable | None, hits: int = 1,
                    precomputed: dict | None = None,
                    hazard_abilities: tuple[str, ...] | set[str] = ()) -> tuple[int | None, dict]:
    """Min `dstat` SP for `member` UNDER `nature` to survive `hits` hits of `attacker` at the target
    probability; returns (min_total, rolls_cache). The defender's nature scales its Def/SpD so each
    nature re-batches; the incoming damage is monotonic in `dstat` SP, so one batch [0..cap] feeds the
    binary search. This is the per-nature kernel the baseline card and every §16.8 nature lane share.

    `hits` is the DEFENSE mirror of the kill cliff's multi-hit band (§9). Two-hit survival enumerates
    the exact independent 16x16 damage-roll distribution, with entry-hazard chip applied ONCE. It
    still ignores between-hit recovery and state changes; those are surfaced as assumptions. Damage
    rolls do NOT depend on `hits`, so the survive-1 rolls_cache can be passed back in as `precomputed`
    for the survive-2 solve — same (attacker, field, nature), no second batch call.

    `defender_types`/`hazard_abilities` feed ONLY the hazard chip and must describe the PRE-Mega
    (switch-in) form and ability. The damage side reads the effective Mega form from `member`."""
    m = member if nature == (member.get("nature") or "Hardy") else {**member, "nature": nature}

    def _defender(total: int):
        sps = dict(base_sps); sps[dstat] = total
        return _member_ncp(m, sps)

    rolls_cache: dict[int, tuple[list[int], int]] = precomputed if precomputed is not None else {}
    if precomputed is None and damage_batch_fn is not None:
        reqs = [{"attacker": attacker, "defender": _defender(t), "move": move, "field": field}
                for t in range(SP_CAP + 1)]
        for t, r in enumerate(damage_batch_fn(reqs) or []):
            r = r or {}
            hp = int(r.get("defenderHP") or 0)
            # A batch entry with no HP is a calculator error, not a real 0-HP defender; skipping it
            # lets predicate() fall back to a live probe instead of mis-reading "doesn't survive".
            if hp:
                rolls_cache[t] = (list(r.get("damage", [])), hp)

    def predicate(total: int) -> bool:
        if total in rolls_cache:
            rolls, hp = rolls_cache[total]
        else:
            rolls, hp = damage_fn(attacker, _defender(total), move, field)
        if not hp:
            return False
        hazard_chip = (_sr_chip(defender_types, hp, abilities=hazard_abilities) if use_sr else 0) \
            + _spikes_chip(defender_types, hp, spikes, abilities=hazard_abilities, item=m.get("item"))
        eff_hp = hp - hazard_chip
        return meets_target(survival_prob_hits(rolls, eff_hp, hits), target)

    return solve_min_sp(predicate, cap=SP_CAP), rolls_cache


def _survive_card(member: dict[str, Any], b: dict[str, Any], fmt: str, *,
                  damage_fn: Callable, move_fn: Callable, dex_fn: Callable,
                  meta_fn: Callable | None = None, meta_natures: set[str] | None = None,
                  locked: bool = False, damage_batch_fn: Callable | None = None,
                  item_fn: Callable | None = None,
                  team_flags: dict[str, bool] | None = None,
                  effective: dict[str, Any] | None = None) -> dict[str, Any]:
    team_flags = team_flags or {}
    move = b.get("move")
    mi = (move_fn([move]) or {}).get(move, {}) if move else {}
    cat = (mi.get("category") or "").lower()
    # Resolve the DEFENDER to its Mega form when it holds a stone (§9): Mega stats/typing/ability
    # (so Mega Delphox's Levitate makes Ground a 0, not an unreachable survival cliff).
    eff = effective or _effective_form(member, dex_fn, item_fn)
    # A stated battle state (a status, stat stages) is a condition of THIS benchmark, not part of the
    # build: it rides on the calc member so every lane below — Def/SpD, HP, mixed, probability,
    # nature, Intimidate — is solved under the same state, and it is echoed on the card.
    state = _member_state(b)
    calc_member = {**_calc_member(member, eff), **({"battle_state": state} if state else {})}
    member_label = eff["name"]
    if cat not in ("physical", "special"):
        return {"aspect": "defense", "kind": "survive", "member": member_label, "vs": b.get("vs"),
                "move": move, "result": "skipped",
                "note": "move missing/non-damaging; cannot solve a survival cliff"}

    dstat, dspread = DEF_SPS[cat], DEF_SPREAD[cat]
    if move in PHYS_DEF_SPECIAL_MOVES:           # special move, but it hits Def — pressure Def, not SpD
        dstat, dspread = "df", "def"
    target = b.get("probability", "guaranteed")
    conds = b.get("conditions") or {}
    # Stealth Rock chipping US needs the OPPONENT's SR (our team-carries gate is for the KILL card),
    # so on the survive side it is an EXPLICIT opt-in and SINGLES-only (re-audit 2026-07-07); a Magic
    # Guard defender takes no chip (handled inside _sr_chip). Spikes stays default-off (conds-driven).
    sr_requested = conds.get("stealth_rock") is True
    sr_ignored = sr_requested and fmt != "single"
    use_sr = sr_requested and fmt == "single"
    spikes = _spikes_layers(conds)
    # field.format makes the engine apply the DOUBLES spread-move reduction (0.75x) — the fix at the
    # Format must reach the damage engine so doubles spread moves receive their spread modifier.
    field = _damage_field(conds, cat, fmt, use_sr=use_sr, spikes=spikes)

    explicit_opponent = b.get("opponent_set") or b.get("attacker_set")
    meta_set = _meta_set(meta_fn, b["vs"], fmt, explicit_opponent, dex_fn=dex_fn)
    attacker0, prov = _attacker_ncp(b["vs"], cat, explicit_opponent, meta_set)
    attacker = _mega_resolve_attacker(attacker0, dex_fn, item_fn)   # Mega Staraptor -> Contrary + Mega stats
    atk_base_pct = _meta_ability_pct(meta_set)
    atk_base_special_pct = _meta_special_ability_pct(meta_set, INTIMIDATE_IMMUNE_ABILITIES)
    # The §9 usage gate applies only to a pre-Mega base ability that is a META MARGINAL; an explicit
    # attacker_set or a real-team joint set is declared/observed and counts ungated.
    atk_base_gated = _base_ability_gated(prov, meta_set)
    mc_conf, mc_note = _member_completeness(member)
    base_sps = _sps_from_spread(member.get("spread"))
    cur = base_sps[dstat]
    # Hazards resolve at SWITCH-IN (pre-Mega): chip typing and immunity both use the BASE phase.
    # A Mega-only Magic Guard/Levitate cannot retroactively cancel entry damage.
    defender_types = list(eff["base_facts"].get("types") or [])
    hazard_abilities = ((eff["base_ability"],) if eff["is_mega"] and eff.get("base_ability")
                        else (eff["ability"],) if eff.get("ability") else ())

    # Only `dstat` (Def or SpD, never HP) varies across the SP search, so the incoming damage is
    # monotonic in SP and one batch [0..cap] feeds the search (the kernel handles batch-vs-live).
    cur_nature = member.get("nature") or "Hardy"
    # Solve both one- and two-hit survival lines. The benchmark's optional `hits` selects the primary
    # line; damage rolls don't depend on that count, so the one-hit cache feeds the two-hit and HP
    # lanes unchanged.
    tiers: dict[int, dict[str, Any]] = {}
    rolls_cache: dict[int, tuple[list[int], int]] = {}
    for h in (1, 2):
        mt, rc = _survive_min_sp(calc_member, cur_nature, attacker, move, field, dstat, base_sps,
                                 target, defender_types, use_sr, spikes, damage_fn=damage_fn,
                                 damage_batch_fn=damage_batch_fn, hits=h,
                                 precomputed=rolls_cache if h == 2 else None,
                                 hazard_abilities=hazard_abilities)
        if h == 1:
            rolls_cache = rc
        t = {"hits": h, "label": "survive 1 hit" if h == 1 else "survive 2 hits",
             **_outcome(mt, cur, base_sps, dstat)}
        if t["result"] == "already" and mt is not None:      # slack = pullable SP while still surviving
            t["slack_sp"] = cur - mt
        tiers[h] = t
    # The browser durability workbench may explicitly request one- or two-hit survival. Keep one hit
    # as the compatibility default; when two is named, make that tier the headline instead of
    # silently solving a different objective. The two-hit tier uses the documented independent-roll
    # model and surfaces the effects it cannot carry across hits.
    survive_tiers = [tiers[1], tiers[2]]
    requested_hits = b.get("hits", 1)
    headline = tiers[requested_hits]
    head_hits = headline["hits"]
    probability_lanes: list[dict[str, Any]] = []
    for lane_target in ("guaranteed", "near_guaranteed", "likely", "three_quarters", "half", "any"):
        lane_min, _ = _survive_min_sp(
            calc_member, cur_nature, attacker, move, field, dstat, base_sps,
            lane_target, defender_types, use_sr, spikes,
            damage_fn=damage_fn, damage_batch_fn=damage_batch_fn, hits=head_hits,
            precomputed=rolls_cache, hazard_abilities=hazard_abilities,
        )
        probability_lanes.append({
            "probability": lane_target,
            "primary": lane_target == target,
            "stat": dspread,
            "scope": "defense_axis",
            **_outcome(lane_min, cur, base_sps, dstat),
        })

    # HP lane (audit retro 2026-06-22): HP is often the cheaper survival lever and scales BOTH
    # defenses, yet the cliff above tunes only one defensive stat — so a cliff reachable via HP was
    # mis-reported as "unreachable". Incoming damage does NOT depend on the defender's HP, so the
    # rolls at the current Def/SpD are reused unchanged: the HP lane costs ZERO extra ncp calls; only
    # the survival threshold (max HP) moves with HP SP. Reported as an alternative fact, never as the
    # chosen lever (the model picks; competing lanes share the same 66 SP budget).
    cur_hp_sp = base_sps["hp"]
    if cur in rolls_cache:
        cur_rolls, cur_hp = rolls_cache[cur]
    else:                                  # current Def/SpD defender (base_sps[dstat] == cur already)
        cur_rolls, cur_hp = damage_fn(attacker, _member_ncp(calc_member, base_sps), move, field)
    hp_lane: dict[str, Any] | None = None
    if move in HP_DEPENDENT_MOVES:
        # The HP-lane reuse assumes incoming damage is independent of the defender's HP; that's false
        # for HP-proportional moves, so don't offer a misleading HP lane (audit 2026-06-24).
        hp_lane = {"stat": "hp", "result": "n/a",
                   "note": f"{move} damage scales with HP — adding HP doesn't form a survival cliff"}
    elif cur_hp and cur_rolls is not None:
        hp0 = cur_hp - cur_hp_sp                        # max HP at 0 HP SP (Champions: 1 SP = +1 HP)

        def _hp_pred(hp_sp: int) -> bool:
            mhp = hp0 + hp_sp
            hazard_chip = (_sr_chip(defender_types, mhp, abilities=hazard_abilities) if use_sr
                           else 0) + _spikes_chip(defender_types, mhp, spikes,
                                                  abilities=hazard_abilities, item=calc_member.get("item"))
            eff_hp = mhp - hazard_chip
            return meets_target(survival_prob_hits(cur_rolls, eff_hp, head_hits), target)

        min_hp = solve_min_sp(_hp_pred, cap=SP_CAP)
        if min_hp is None:
            hp_lane = {"stat": "hp", "result": "unreachable", "delta_sp": SP_CAP + 1}
        else:
            hp_lane = {"stat": "hp", **_outcome(min_hp, cur_hp_sp, base_sps, "hp")}

    # Bounded two-stat survival frontier. Damage is already known for each Def/SpD point; HP only
    # changes the threshold, so enumerating HP x relevant defense adds no calculator work on the
    # production batch path. This catches real mixed cliffs that both one-dimensional lanes miss.
    combined_lane: dict[str, Any] | None = None
    if move not in HP_DEPENDENT_MOVES and cur_hp:
        hp0 = cur_hp - cur_hp_sp
        best: tuple[int, int, int] | None = None       # total delta, hp total, defense total
        for defense_sp in range(cur, SP_CAP + 1):
            cached = rolls_cache.get(defense_sp)
            if cached is None:
                probe_sps = dict(base_sps)
                probe_sps[dstat] = defense_sp
                cached = damage_fn(
                    attacker, _member_ncp(calc_member, probe_sps), move, field
                )
                rolls_cache[defense_sp] = cached
            defense_rolls, _probe_hp = cached
            for hp_sp in range(cur_hp_sp, SP_CAP + 1):
                mhp = hp0 + hp_sp
                hazard_chip = (
                    _sr_chip(defender_types, mhp, abilities=hazard_abilities) if use_sr else 0
                ) + _spikes_chip(
                    defender_types, mhp, spikes, abilities=hazard_abilities,
                    item=calc_member.get("item"),
                )
                if meets_target(
                        survival_prob_hits(defense_rolls, mhp - hazard_chip, head_hits), target):
                    cand = ((defense_sp - cur) + (hp_sp - cur_hp_sp), hp_sp, defense_sp)
                    if best is None or cand < best:
                        best = cand
                    break
        if best is not None and best[1] > cur_hp_sp and best[2] > cur:
            delta, need_hp, need_defense = best
            combined_lane = {
                "stat": f"hp+{dspread}", "result": "already" if delta == 0 else "cliff",
                "delta_sp": delta,
                "allocation": {
                    "hp": {"current": cur_hp_sp, "need_total": need_hp,
                           "delta_sp": need_hp - cur_hp_sp},
                    dspread: {"current": cur, "need_total": need_defense,
                              "delta_sp": need_defense - cur},
                },
            }
            plan = _reallocation_plan(delta, {"hp", dstat}, base_sps)
            if plan:
                combined_lane["reallocation"] = plan

    # Intimidate lane (§9, doubles only): when our team fields an Intimidate provider AND the
    # physical attacker is not Intimidate-immune (Contrary/Defiant/Clear Body/Mirror Armor on either
    # form), model BOTH 0 and 1 Intimidate (capped at 1) — the -1 Atk relief cliff sits beside the raw
    # cliff so the user sees the range they actually play at, not a false "unreachable".
    intimidate_lane: dict[str, Any] | None = None
    if (cat == "physical" and team_flags.get("intimidate") and headline["result"] != "already"
            and _attacker_intimidatable(attacker, atk_base_pct, atk_base_special_pct,
                                        base_gated=atk_base_gated)):
        boosts = dict(attacker.get("boosts") or {})
        boosts["atk"] = max(-6, min(6, int(boosts.get("atk") or 0) - 1))
        intim_attacker = {**attacker, "boosts": boosts}
        intim_min, _ic = _survive_min_sp(calc_member, cur_nature, intim_attacker, move, field, dstat,
                                         base_sps, target, defender_types, use_sr, spikes,
                                         damage_fn=damage_fn, damage_batch_fn=damage_batch_fn, hits=head_hits,
                                         hazard_abilities=hazard_abilities)
        intimidate_lane = {"stages": 1, **_outcome(intim_min, cur, base_sps, dstat)}

    def _hp_suffix() -> str:
        if not hp_lane:
            return ""
        r = hp_lane["result"]
        if r == "n/a":
            return f" | HP lane n/a ({hp_lane.get('note', '')})"
        if r == "already":
            return " | HP lane: already met"
        if r == "unreachable":
            return " | HP lane also can't reach within the cap"
        return f" | HP lane: +{hp_lane['delta_sp']} HP SP (to {hp_lane['need_total']}) [{r}]"

    def _combined_suffix() -> str:
        if not combined_lane:
            return ""
        alloc = combined_lane["allocation"]
        return (f" | mixed HP+{dspread} lane: +{combined_lane['delta_sp']} SP "
                f"(HP {alloc['hp']['need_total']}, {dspread} {alloc[dspread]['need_total']})")

    allocation_lanes: list[dict[str, Any]] = [
        {"lane": dspread, **headline},
    ]
    if hp_lane and hp_lane.get("result") in ("already", "cliff"):
        allocation_lanes.append({"lane": "hp", **hp_lane})
    if combined_lane:
        allocation_lanes.append({"lane": "mixed", **combined_lane})
    reachable_allocations = [
        lane for lane in allocation_lanes if lane.get("result") in ("already", "cliff")
    ]
    selected = min(
        reachable_allocations,
        key=lambda lane: (int(lane.get("delta_sp") or 0),
                          {"already": 0, "cliff": 1}.get(lane.get("result"), 2),
                          {dspread: 0, "hp": 1, "mixed": 2}.get(lane.get("lane"), 3)),
    ) if reachable_allocations else {"lane": dspread, **headline}

    card: dict[str, Any] = {
        "aspect": "defense", "kind": "survive", "member": member_label, "vs": b.get("vs"), "move": move,
        "category": cat, "stat": selected.get("stat") or selected.get("lane") or dspread,
        "probability": target,
        "stealth_rock": use_sr, "spikes": spikes,
        "survive_tiers": survive_tiers, "hits": head_hits,
        "probability_lanes": probability_lanes,
        # Confidence = the more cautious of the attacker-set confidence and the tuned member's own
        # set completeness (tuning off an unknown spread/ability is a guess).
        "confidence": completeness.min_confidence(prov.get("confidence", "medium"), mc_conf),
        "attacker": {"source": prov.get("source"), "ability": attacker.get("ability"),
                     "item": attacker.get("item"), "nature": attacker.get("nature"),
                     "sps": attacker.get("sps"), "prevalence": prov.get("prevalence")},
        "hp_lane": hp_lane,
        "combined_lane": combined_lane,
        "allocation_lanes": allocation_lanes,
        "selected_lane": selected.get("lane"),
        "assumptions": [f"attacker = {prov.get('source')} set",
                        f"requested survive benchmark is the {requested_hits}-hit primary"]
        + ([_state_assumption(state)] if state else [])
        + (["2-hit survival enumerates the independent 16x16 damage rolls and applies entry-hazard "
            "chip once; it ignores between-hit recovery (Sitrus/Leftovers), ability shifts "
            "(Stamina/Multiscale), and field changes — those effects can flip the result."]
           if head_hits == 2 else [])
        # The meta attacker stitches independent ability/item/nature marginals onto a real spread row;
        # surface that caveat in assumptions too (matchup already does), not only in evidence.note.
        + (["attacker fields are independent meta marginals — exact ability+item+nature combo may not co-occur"]
           if prov.get("source") == "meta" else [])
        + (["Stealth Rock chip modelled via the type table (opponent SR, singles; chip resolves at "
            "switch-in, so it reads the PRE-Mega typing/abilities)"] if use_sr else [])
        + (["Stealth Rock was explicitly requested but ignored: this tune path only models entry "
            "hazards in singles, so no Stealth Rock chip was applied"] if sr_ignored else [])
        + ([f"{spikes} layer(s) of Spikes chip on grounded targets modelled (pre-Mega form)"] if spikes else [])
        + (["the main cliff assumes 0 Intimidate (conservative); the intimidate lane is the -1 Atk relief"]
           if intimidate_lane else [])
        + ([f"attacker Mega form modelled ({attacker.get('name')}; Mega stats/ability)"]
           if attacker.get("mega_ability") else [])
        + (["Mega form modelled (Mega stats/typing/ability); switch-in abilities also read the base form"]
           if eff["is_mega"] else [])
        + ([mc_note] if mc_note else []),
        "evidence": {"facts": [{"source": "ncp", "ref": "damage rolls"},
                               {"source": "dex", "ref": "defender types/stats"},
                               {"source": "meta", "ref": "attacker modal set"}],
                     "note": (prov.get("note") or "attacker set") + (f" | {mc_note}" if mc_note else "")},
    }
    if state:
        card["member_state"] = state
    if sr_ignored:
        card["stealth_rock_requested"] = True
        card["stealth_rock_ignored"] = "doubles_not_modelled"
    if intimidate_lane is not None:
        card["intimidate"] = intimidate_lane

    def _haz_suffix() -> str:
        return (" after Stealth Rock" if use_sr else "") \
            + (f" after {spikes} layer(s) of Spikes" if spikes else "")

    def _stier_phrase(t: dict[str, Any]) -> str:
        r, lab = t["result"], t["label"]
        if r == "already":
            return f"already {lab} ({target}{_haz_suffix()})"
        if r == "unreachable":
            return f"can't {lab} even at {SP_CAP} {dspread} SP"
        if r == "infeasible":
            return f"{lab} needs +{t['delta_sp']} {dspread} SP (to {t['need_total']}) — no legal donor capacity"
        funding = t.get("reallocation")
        suffix = (f", reallocating {funding['required_sp']} SP from other invested stats"
                  if funding else "")
        return f"{lab} at +{t['delta_sp']} {dspread} SP (to {t['need_total']}{suffix})"

    card.update(result=selected["result"], delta_sp=selected["delta_sp"])
    if selected.get("need_total") is not None:
        card["need_total"] = selected["need_total"]
    if selected.get("allocation") is not None:
        card["allocation"] = selected["allocation"]
    if selected.get("slack_sp") is not None:
        card["slack_sp"] = selected["slack_sp"]
    if selected.get("reallocation") is not None:
        card["reallocation"] = selected["reallocation"]
    card["note"] = (" | ".join(_stier_phrase(t) for t in survive_tiers)
                    + _hp_suffix() + _combined_suffix())

    # Stealth Rock 0/1 dual (§9): when our team-benchmark puts opponent SR on us (opt-in, singles),
    # also solve the headline tier WITHOUT the chip so both hazard states are visible — you may or may not
    # be on SR. Anchored to the headline hits only (NOT crossed with the other tier — §9 no-cross rule).
    if use_sr:
        # The damage rolls don't depend on the chip, so the survive-1 rolls_cache is reused — the
        # no-SR lane costs zero extra engine calls.
        no_sr_min, _ = _survive_min_sp(calc_member, cur_nature, attacker, move, field, dstat, base_sps,
                                       target, defender_types, False, spikes, damage_fn=damage_fn,
                                       damage_batch_fn=damage_batch_fn, hits=head_hits,
                                       precomputed=rolls_cache, hazard_abilities=hazard_abilities)
        card["stealth_rock_lane"] = {
            "stealth_rock": False, **_outcome(no_sr_min, cur, base_sps, dstat)
        }

    # §16.8 nature lanes: re-solve THIS cliff under candidate natures (reuses the 1-D kernel, no new
    # search dimension) and attach the impactful ones (unlock / save >= floor) as an OPTIONAL sub-field.
    # NEVER enters the head ranking — `nature` is a whole-spread single-slot commitment the model owns.
    # A `locked` member (user declared it won't change) gets no lanes at all.
    if headline["result"] in ("cliff", "infeasible", "unreachable") and not locked:
        m_stats = eff["facts"].get("stats") or {}
        lanes = _nature_lanes(
            dspread, cur_nature, cur, base_sps,
            {"result": headline["result"], "delta_sp": headline["delta_sp"]},
            solve_min_fn=lambda nat: _survive_min_sp(calc_member, nat, attacker, move, field, dstat, base_sps,
                                                     target, defender_types, use_sr, spikes, damage_fn=damage_fn,
                                                     damage_batch_fn=damage_batch_fn, hits=head_hits,
                                                     hazard_abilities=hazard_abilities)[0],
            invested=_invested_stats(member.get("spread")),
            offense_lean=_stat_orientation(m_stats).get("offense_lean"),
            meta_natures=meta_natures or set())
        if lanes["alternatives"]:
            card["nature_alternatives"] = lanes["alternatives"]
        if lanes["notes"]:
            card["nature_notes"] = lanes["notes"]
        if lanes["unlock"]:
            card["nature_unlock"] = True
    return card


def _raw_speed(vs: Any) -> int | None:
    """A benchmark `vs` may be a raw target Speed instead of a species name (schema.md §7)."""
    if isinstance(vs, bool):
        return None
    if isinstance(vs, (int, float)):
        return int(vs)
    if isinstance(vs, str) and vs.strip().lstrip("+").isdigit():
        return int(vs.strip())
    return None


def _outspeed_card(member: dict[str, Any], b: dict[str, Any], fmt: str, *,
                   damage_fn: Callable, move_fn: Callable, dex_fn: Callable,
                   meta_fn: Callable | None = None, meta_natures: set[str] | None = None,
                   locked: bool = False, item_fn: Callable | None = None,
                   team_flags: dict[str, bool] | None = None,
                   archetypes_fn: Callable | None = None,
                   effective: dict[str, Any] | None = None) -> dict[str, Any]:
    team_flags = team_flags or {}
    raw_target = _raw_speed(b["vs"])
    eff = effective or _effective_form(member, dex_fn, item_fn)         # my Mega form (Mega base Speed + ability)
    member_label = eff["name"]
    my_base = (eff["facts"].get("stats") or {}).get("spe")
    my_ability = eff["ability"]
    calc_member = _calc_member(member, eff)
    if my_base is None:
        return {"aspect": "speed", "kind": "outspeed", "member": member_label, "vs": b.get("vs"),
                "result": "skipped", "note": "missing base speed for self"}
    facts = dex_fn([b["vs"]]) or {} if raw_target is None else {}

    conds = b.get("conditions") or {}
    trickroom = bool(conds.get("trickroom"))             # opt-in; default off (§9)

    # Named targets use the same resolved real/modal set authority as damage. The old theoretical
    # max-speed line remains a ceiling lane; it no longer overwrites the actual benchmark coordinate.
    explicit_target = b.get("opponent_set")
    d_meta = None if raw_target is not None else _meta_set(
        meta_fn, b["vs"], fmt, explicit_target, dex_fn=dex_fn
    )
    target_set = explicit_target or d_meta
    speed_basis = "raw" if raw_target is not None else (
        "explicit" if explicit_target else
        str((d_meta or {}).get("source") or "resolved") if d_meta else
        "theoretical_max"
    )
    tr_floor_base: int | None = None
    target_is_mega = False
    ceiling_speed: int | None = None
    ceiling_label: str | None = None
    if raw_target is not None:
        tgt_speed, tgt_label = raw_target, f"{raw_target} Speed"
    else:
        tfacts = facts.get(b["vs"], {}) or {}
        tgt_base = (tfacts.get("stats") or {}).get("spe")
        tgt_name = b["vs"]
        target_item = (target_set or {}).get("item") if isinstance(target_set, dict) else None
        if tfacts.get("is_mega"):
            target_is_mega = True
            _base_name, _base_facts = _base_form_facts(tgt_name, dex_fn, tfacts)
            base_spe = (_base_facts.get("stats") or {}).get("spe")
            _floor_candidates = [x for x in (tgt_base, base_spe) if x is not None]
            if _floor_candidates:
                tr_floor_base = min(_floor_candidates)
        # Mega-resolve the target. Only the CURRENT run-form ability modifies current Speed; unlike
        # switch-in effects, a pre-Mega weather ability that was replaced cannot keep doubling Speed.
        tgt_form = (target_set or {}).get("run_form") if isinstance(target_set, dict) else None
        if not tgt_form:
            tgt_form = _mega_form_name(tgt_name, target_item, dex_fn, item_fn, facts=tfacts)
        target_ability = (target_set or {}).get("ability") if isinstance(target_set, dict) else None
        if tgt_form and tgt_form != tgt_name:
            tform_facts = _dex_facts(tgt_form, dex_fn)
            t_spe = (tform_facts.get("stats") or {}).get("spe")
            if t_spe is not None:
                # Trick Room's floor keeps the SLOWEST plausible form (a Mega can be slower: Abomasnow).
                tr_floor_base = min(x for x in (tr_floor_base, tgt_base, t_spe) if x is not None)
                tgt_base = t_spe
                tgt_name = tgt_form
                target_is_mega = bool(tform_facts.get("is_mega"))
                target_ability = effective_form_ability(target_ability, tform_facts)
        if tgt_base is None:
            return {"aspect": "speed", "kind": "outspeed", "member": member_label, "vs": b.get("vs"),
                    "result": "skipped", "note": "missing base speed for target"}
        if tr_floor_base is None:
            tr_floor_base = tgt_base
        if target_set:
            target_sps = engine_spread(target_set.get("sps") or {})
            if not target_sps and isinstance(target_set.get("spread"), dict):
                target_sps = _sps_from_spread(target_set.get("spread"))
            target_nature = target_set.get("nature") or "Hardy"
            tgt_speed = effective_speed(
                tgt_base, int(target_sps.get("sp") or 0), target_nature,
                item=target_item, ability=target_ability,
                weather=conds.get("weather"), terrain=conds.get("terrain"),
            )
            tgt_label = f"{speed_basis} set {tgt_name}"
            ceiling_speed = effective_speed(
                tgt_base, SP_CAP, "Jolly", item=target_item, ability=target_ability,
                weather=conds.get("weather"), terrain=conds.get("terrain"),
            )
            ceiling_label = f"theoretical max of the resolved {tgt_name} form/set"
        else:
            # No set authority: retain the conservative synthetic maximum and disclose the fallback.
            # Pick the current-form ability that MAXIMISES the weather/terrain Speed boost, so a
            # non-first weather ability (e.g. Swift Swim listed second) still doubles the synthetic
            # max under its weather — the pre-refactor max-over-all-abilities conservatism.
            target_ability = max(
                (tfacts.get("abilities") or [None]),
                key=lambda a: weather_speed_mult(a, conds.get("weather"), conds.get("terrain")),
            )
            tgt_speed = effective_speed(
                tgt_base, SP_CAP, "Jolly", ability=target_ability,
                weather=conds.get("weather"), terrain=conds.get("terrain"),
            )
            tgt_label = f"theoretical max-speed {tgt_name}"
            ceiling_speed, ceiling_label = tgt_speed, tgt_label
    # Opponent Tailwind (x2): modelled but OPT-IN (default off) — only when the benchmark asks, and
    # doubles only (§9). It doubles the target's Speed, including raw-Speed targets.
    if conds.get("opponent_tailwind") and fmt == "double":
        tgt_speed *= 2
        if ceiling_speed is not None:
            ceiling_speed *= 2
        tgt_label += " under their Tailwind (x2)"
        if ceiling_label:
            ceiling_label += " under their Tailwind (x2)"
    my_nature = member.get("nature")
    base_sps = _sps_from_spread(member.get("spread"))
    cur_sp = base_sps["sp"]
    mc_conf, mc_note = _member_completeness(member)
    # My own weather-speed ability (Swift Swim / Chlorophyll / ...) doubles my Speed when its weather is
    # up (both formats). Folded into self_mult so the cliff is solved off EFFECTIVE Speed, not bare.
    wmult = weather_speed_mult(my_ability, conds.get("weather"), conds.get("terrain"))
    _trig = conds.get("weather") or conds.get("terrain")
    # My Tailwind (x2) comes in two shapes (§9 + schema.md): an EXPLICIT `conditions.tailwind: true`
    # is the user declaring Tailwind IS up — the request always wins, so the MAIN line is solved at x2
    # (no lane needed; this is the pre-refactor behaviour the schema documents). Otherwise the
    # DOUBLES + team-carries gate models it as a DUAL: the raw cliff is the main line, the x2 cliff a
    # lane (both always shown) — you may or may not have Tailwind up.
    tailwind_specified = "tailwind" in conds
    explicit_tailwind = conds.get("tailwind") is True
    my_tailwind = (not tailwind_specified) and team_flags.get("tailwind") and not trickroom

    if trickroom:
        # Trick Room inverts the order: the SLOWER mon acts first, so the goal is to UNDER-speed — and
        # the conservative bound flips with it. To guarantee moving first we must under-speed the
        # target's FLOOR line (0 SP, -Speed nature, slowest form), not the fast line the outspeed goal
        # uses (vs the fast line "already under-speeds" would be the OPTIMISTIC extreme). Scarf /
        # weather / Tailwind only make the target faster (easier to under-speed), so none apply here.
        # Speed SP is a lever to PULL (a -Speed nature under-speeds harder), not add.
        if raw_target is not None or target_set:
            tr_target, tr_label = tgt_speed, tgt_label
        else:
            tr_target = champ_speed(tr_floor_base, 0, "Brave")
            tr_label = f"floor-speed {b['vs']} (0 SP, -Speed nature, slowest form)"
        my_now = effective_speed(my_base, cur_sp, my_nature, item=member.get("item"),
                                 ability=my_ability, weather=conds.get("weather"),
                                 terrain=conds.get("terrain"), tailwind=explicit_tailwind)
        under = my_now is not None and my_now < tr_target
        tw_tr_note = " under my Tailwind (x2, explicit condition)" if explicit_tailwind else ""
        tr_note = (f"already under-speeds {tr_label} ({my_now} < {tr_target}) — moves first under Trick Room"
                   if under else
                   f"currently {my_now} >= {tr_target}: PULL Speed SP or take a -Speed nature to under-speed "
                   f"{tr_label} under Trick Room")
        return {
            "aspect": "speed", "kind": "outspeed", "member": member_label, "vs": b.get("vs"),
            "trickroom": True, "tailwind": explicit_tailwind,
            "tailwind_mode": "explicit-main" if explicit_tailwind else "none",
            "tailwind_main": explicit_tailwind, "tailwind_lane_available": False,
            "confidence": mc_conf,
            "result": "already" if under else "unreachable",
            "delta_sp": 0 if under else SP_CAP + 1,
            "needs": None if under else "lower_speed",
            "note": tr_note + tw_tr_note,
            "assumptions": ["Trick Room: UNDER-speeding is the goal (slower acts first)",
                            "target = floor line (0 SP, -Speed nature, slowest form) — the conservative "
                            "bound for under-speeding; the fast line would be optimistic here"]
            + (["my Tailwind applied to the current-speed check (x2 Speed — explicit benchmark condition)"]
               if explicit_tailwind else [])
            + (["Mega form modelled"] if eff["is_mega"] else []) + ([mc_note] if mc_note else []),
            "evidence": {"facts": [{"source": "dex", "ref": "base speeds"},
                                   {"source": "builtin", "ref": "Champions speed formula"}],
                         "note": tr_note + tw_tr_note},
        }

    self_mult = (wmult if wmult else 1) * (2 if explicit_tailwind else 1)
    sol = solve_outspeed(my_base, my_nature, tgt_speed, cap=SP_CAP, self_mult=self_mult,
                         item=member.get("item"))
    sol_tw = solve_outspeed(my_base, my_nature, tgt_speed, cap=SP_CAP, self_mult=self_mult * 2,
                            item=member.get("item")) if my_tailwind else None
    tw_note = (f" under {_trig} ({my_ability} x{wmult} Speed)" if wmult != 1 else "") \
        + (" under my Tailwind (x2, explicit condition)" if explicit_tailwind else "")

    # What other observed builds of the target cost to outspeed. The primary line remains the explicit
    # or resolved set; these disclose alternative speed peaks without flattening them into one target.
    variant_lines = []
    if raw_target is None and b.get("vs"):
        for line in _variant_speed_lines(str(b["vs"]), fmt, archetypes_fn, dex_fn, item_fn, conds):
            need = None
            for sp in range(0, SP_CAP + 1):
                got = effective_speed(my_base, sp, my_nature, item=member.get("item"),
                                      ability=my_ability, weather=conds.get("weather"),
                                      terrain=conds.get("terrain"), tailwind=explicit_tailwind)
                if got is not None and got > line["speed"]:
                    need = sp
                    break
            variant_lines.append({**line, "sp_to_outspeed": need,
                                  "already": need is not None and need <= cur_sp})

    ceiling_lane = None
    if ceiling_speed is not None:
        ceiling_sol = solve_outspeed(
            my_base, my_nature, ceiling_speed, cap=SP_CAP, self_mult=self_mult,
            item=member.get("item"),
        )
        ceiling_min = ceiling_sol.get("sp") if ceiling_sol else None
        ceiling_lane = {
            "basis": "theoretical_max", "target_speed": ceiling_speed,
            "label": ceiling_label, **_outcome(ceiling_min, cur_sp, base_sps, "sp"),
        }
        if ceiling_sol and ceiling_sol.get("result") == "tie-only":
            ceiling_lane["result"] = "tie-only"

    card: dict[str, Any] = {
        "aspect": "speed", "kind": "outspeed", "member": member_label, "vs": b.get("vs"),
        "stat": "spe",
        "speed_basis": speed_basis, "target_speed": tgt_speed,
        **({"ceiling_lane": ceiling_lane} if ceiling_lane else {}),
        **({"variant_lines": variant_lines} if variant_lines else {}),
        "tailwind": bool(explicit_tailwind or my_tailwind),
        "tailwind_mode": ("explicit-main" if explicit_tailwind else "team-lane" if my_tailwind else "none"),
        "tailwind_main": explicit_tailwind,
        "tailwind_lane_available": bool(my_tailwind),
        "confidence": mc_conf,
        "assumptions": [f"primary target = {speed_basis} speed set",
                        "main line is SP-only on the current nature; bounded nature lanes are attached "
                        "separately (not a joint nature x SP search)"]
        + (["my Tailwind applied to the MAIN line (x2 Speed — explicit benchmark condition)"]
           if explicit_tailwind else [])
        + (["my Tailwind lane shown as x2 Speed (doubles, team carries Tailwind) — both raw and Tailwind lines"]
           if my_tailwind else [])
        + ([f"my {my_ability} applied (x{wmult} Speed under {_trig})"] if wmult != 1 else [])
        + (["Mega form modelled (Mega base Speed + ability)"] if eff["is_mega"] else [])
        + ([mc_note] if mc_note else []),
        "evidence": {"facts": [{"source": "dex", "ref": "base speeds"},
                               {"source": "builtin", "ref": "Champions speed formula"}],
                     "note": f"vs {tgt_label}" + tw_note
                             + "; theoretical maximum is a separate ceiling lane"
                             + (f" | {mc_note}" if mc_note else "")},
    }
    if my_tailwind:
        # Both lines are ALWAYS presented (§9): even when the x2 line can't reach either, the lane says
        # "unreachable" instead of silently vanishing (the reader must see that Tailwind doesn't save it).
        tw_result = sol_tw.get("result") if sol_tw else None
        _tw_min = sol_tw["sp"] if sol_tw and tw_result in ("outspeed", "tie-only") else None
        lane = {"self_mult": 2,
                "tie_only": bool(tw_result == "tie-only"),
                **_outcome(_tw_min, cur_sp, base_sps, "sp")}
        if tw_result == "tie-only":
            lane["result"] = "tie-only"
        card["tailwind_lane"] = lane
    if sol is None:
        card.update(result="unreachable", delta_sp=SP_CAP + 1,
                    note=f"cannot outspeed {tgt_label} ({tgt_speed}){tw_note} even at {SP_CAP} Speed SP")
    else:
        delta = max(0, sol["sp"] - cur_sp)
        budget = _outcome(sol["sp"], cur_sp, base_sps, "sp")
        # Join with a KO check: a speed cliff only matters if moving first flips an outcome.
        ko_impact, ko_note = "not_evaluated", (
            "speed only — couple a move to judge if moving first flips the KO"
        )
        move = b.get("move")
        if move and raw_target is not None:
            ko_note = "speed only — a raw-speed target has no species to run a KO check against"
        if move and raw_target is None:
            mi = (move_fn([move]) or {}).get(move, {})
            cat = (mi.get("category") or "").lower()
            if cat in ("physical", "special"):
                sps = _sps_from_spread(member.get("spread"))
                # Model the opponent with its meta MODAL set (real bulk/ability/item), not a 0-investment
                # naked defender — a naked target turned every fast hit into a false "guaranteed OHKO"
                # (audit retro 2026-06-22). No meta set -> fall back to naked and flag the optimism.
                # Defender is Mega-resolved so a stone-holding target is judged on its Mega bulk/typing.
                if target_set:
                    target_actor = dict(target_set)
                    if "sps" not in target_actor and isinstance(target_actor.get("spread"), dict):
                        target_actor["sps"] = _sps_from_spread(target_actor.get("spread"))
                    defender = _mega_resolve_attacker(
                        {"name": b["vs"], **target_actor,
                         "nature": target_actor.get("nature") or "Hardy",
                         "sps": target_actor.get("sps") or {}},
                        dex_fn, item_fn)
                    d_basis = ("vs explicit defender" if explicit_target
                               else "vs resolved modal defender")
                else:
                    defender = {"name": b["vs"], "nature": "Hardy", "sps": {}}
                    d_basis = "vs a 0-investment defender (no meta set) — bulkier real sets may survive"
                # I attack as my Mega form (calc_member); doubles applies the 0.75x spread reduction. (SR
                # rides only the engine ko_chance, not these static rolls, so it isn't wired into this join.)
                rolls, hp = damage_fn(_member_ncp(calc_member, sps), defender, move,
                                      _damage_field(conds, cat, fmt))
                if hp and rolls and min(rolls) >= hp:
                    ko_impact, ko_note = "guaranteed_ohko", (
                        f"moving first guarantees the OHKO with {move} — decisive ({d_basis})"
                    )
                elif hp and rolls and max(rolls) >= hp:
                    ko_impact, ko_note = "possible_ohko", (
                        f"moving first can OHKO with {move} (roll-dependent; {d_basis})"
                    )
                else:
                    ko_impact, ko_note = "no_ohko", (
                        f"outspeeding doesn't secure a KO with {move} — matchup stays unclear ({d_basis})"
                    )

        card["ko_impact"] = ko_impact
        if budget["result"] == "infeasible":
            verb = "tie" if sol["result"] == "tie-only" else "outspeed"
            card.update(result="infeasible", delta_sp=delta, need_total=sol["sp"],
                        note=f"+{delta} Speed SP (to {sol['sp']}) to {verb} {tgt_label} "
                             f"({tgt_speed}) has no legal donor capacity; {ko_note}")
        elif sol["result"] == "tie-only":
            card.update(result="tie-only", delta_sp=delta,
                        note=f"can only tie {tgt_label} ({tgt_speed}){tw_note}; {ko_note}")
        elif delta == 0:
            card.update(result="already", delta_sp=0, need_total=sol["sp"],
                        note=f"already outspeeds {tgt_label} ({tgt_speed}){tw_note}; {ko_note}")
        else:
            funding = budget.get("reallocation")
            funding_note = (f", reallocating {funding['required_sp']} SP from other invested stats"
                            if funding else "")
            card.update(result="cliff", delta_sp=delta, need_total=sol["sp"],
                        note=f"+{delta} Speed SP (to {sol['sp']}) outspeeds {tgt_label} "
                             f"({tgt_speed}){tw_note}{funding_note}; {ko_note}")
        if budget.get("reallocation") is not None:
            card["reallocation"] = budget["reallocation"]

    # §16.8 nature lanes for the Speed cliff (closed-form, near-free): re-solve under candidate natures,
    # attach the impactful ones. A -Speed (Trick Room/weather) member yields no auto speed-nature lane
    # (candidate_natures locks them) yet its EXPLICIT benchmark above is always computed. Locked -> none.
    if card["result"] in ("cliff", "infeasible", "unreachable", "tie-only") and not locked:
        m_stats = eff["facts"].get("stats") or {}

        def _spd_min(nat: str) -> int | None:
            s = solve_outspeed(my_base, nat, tgt_speed, cap=SP_CAP, self_mult=self_mult, item=member.get("item"))
            return s["sp"] if (s and s["result"] == "outspeed") else None

        lanes = _nature_lanes("spe", my_nature, cur_sp, base_sps,
                              {"result": card["result"], "delta_sp": card["delta_sp"]},
                              solve_min_fn=_spd_min, invested=_invested_stats(member.get("spread")),
                              offense_lean=_stat_orientation(m_stats).get("offense_lean"),
                              meta_natures=meta_natures or set())
        if lanes["alternatives"]:
            card["nature_alternatives"] = lanes["alternatives"]
        if lanes["notes"]:
            card["nature_notes"] = lanes["notes"]
        if lanes["unlock"]:
            card["nature_unlock"] = True
    return card


def _engine_ko_meets(kc: dict[str, Any], hits: int, target: str) -> bool:
    """Whether the recovery-aware engine verdict meets an N-hit probability target."""
    try:
        n = int(kc.get("n"))
    except (TypeError, ValueError):
        return False
    if n < hits:
        return True
    if n > hits:
        return False
    chance = float(kc.get("chance_pct") or (100.0 if kc.get("guaranteed") else 0.0))
    threshold = {"guaranteed": 100.0, "likely": 81.25, "any": 0.000001}.get(target, 100.0)
    return bool(kc.get("guaranteed")) if target == "guaranteed" else chance >= threshold


def _kill_min_sp(member: dict[str, Any], nature: str, defender: dict[str, Any], move: str,
                 field: dict[str, Any], off_stat: str, base_sps: dict[str, int], eff_hp: int,
                 hits: int, target: str, *, damage_fn: Callable,
                 damage_batch_fn: Callable | None,
                 precomputed: dict | None = None,
                 precomputed_kochance: dict | None = None) -> tuple[int | None, dict, dict]:
    """Min `off_stat` (Atk/SpD... Atk or SpA) SP for `member` UNDER `nature` to KO `defender` (the
    mirror of _survive_min_sp: vary the ATTACKER's offensive stat, damage is monotonic in it, one
    batch [0..cap] feeds the search). For multi-turn targets, engine `ko_chance` is authoritative when
    available; otherwise the predicate falls back to repeating the selected first-hit roll.

    Returns (min_sp, rolls_cache, kochance_cache). The rolls don't depend on `eff_hp`/`hits`, so a
    prior tier's cache can be reused. Recovery-aware KO chances remain field-specific and therefore
    must be recomputed when a hazard lane changes the field."""
    m = member if nature == (member.get("nature") or "Hardy") else {**member, "nature": nature}

    def _attacker(total: int):
        sps = dict(base_sps); sps[off_stat] = total
        return _member_ncp(m, sps)

    rolls_cache: dict[int, list[int]] = precomputed if precomputed is not None else {}
    kochance_cache: dict[int, dict] = precomputed_kochance or {}
    if damage_batch_fn is not None and (
        precomputed is None or (hits >= 2 and not kochance_cache)
    ):
        reqs = [{"attacker": _attacker(t), "defender": defender, "move": move, "field": field}
                for t in range(SP_CAP + 1)]
        for t, r in enumerate(damage_batch_fn(reqs) or []):
            dmg = list((r or {}).get("damage", []))
            if dmg:
                rolls_cache[t] = dmg
            kc = (r or {}).get("ko_chance")
            if kc:
                kochance_cache[t] = kc

    def predicate(total: int) -> bool:
        if hits >= 2 and kochance_cache:
            kc = kochance_cache.get(total)
            if kc:
                return _engine_ko_meets(kc, hits, target)
            # No engine verdict at this SP point (a cache hole): fall back to the static
            # repeated-hit roll instead of returning False, so a hole can't break the
            # monotonicity solve_min_sp's bisection assumes.
        rolls = rolls_cache.get(total)
        if rolls is None:
            rolls, _hp = damage_fn(_attacker(total), defender, move, field)
        return bool(rolls) and ko_roll(rolls, target) * hits >= eff_hp

    return solve_min_sp(predicate, cap=SP_CAP), rolls_cache, kochance_cache


def _kill_card(member: dict[str, Any], b: dict[str, Any], fmt: str, *,
               damage_fn: Callable, move_fn: Callable, dex_fn: Callable,
               meta_fn: Callable | None = None, meta_natures: set[str] | None = None,
               locked: bool = False, damage_batch_fn: Callable | None = None,
               item_fn: Callable | None = None,
               team_flags: dict[str, bool] | None = None,
               effective: dict[str, Any] | None = None) -> dict[str, Any]:
    """Kill cliff against the requested target set, with the benchmark's declared KO tier primary.

    The adjacent OHKO/2HKO threshold is contextual only. Recovery-aware engine KO results are
    authoritative for multi-turn targets when available; the static repeated-hit predicate is a
    disclosed fallback.
    """
    team_flags = team_flags or {}
    kind = b.get("kind")
    move = b.get("move")
    mi = (move_fn([move]) or {}).get(move, {}) if move else {}
    cat = (mi.get("category") or "").lower()
    eff = effective or _effective_form(member, dex_fn, item_fn)          # OUR attacker's Mega form (Mega stats/ability)
    calc_member = _calc_member(member, eff)
    member_label = eff["name"]
    base = {"aspect": "offense", "kind": kind, "member": member_label, "vs": b.get("vs"), "move": move}
    if cat not in ("physical", "special"):
        return {**base, "result": "skipped", "note": "move missing/non-damaging; cannot solve a kill cliff"}

    off_stat, off_spread = OFF_SPS[cat], OFF_SPREAD[cat]
    target = b.get("probability", "guaranteed")
    conds = b.get("conditions") or {}
    # OUR Stealth Rock chips the TARGET (helps the KO). An EXPLICIT benchmark condition wins (schema:
    # "the user's request always wins" — `stealth_rock: false` turns the chip OFF even when the team
    # carries SR, `true` asserts the rocks are up); absent, the default is the team-carries gate. Either
    # way it is a SINGLES model (doubles entry hazards are marginal — a capability boundary, not a
    # default). A Magic Guard target is exempt inside _sr_chip. Spikes stays default-off (conds-driven).
    # field.format applies the doubles spread-move reduction to our own move (a spread KO move deals
    # 0.75x too).
    sr_cond = conds.get("stealth_rock")
    sr_ignored = sr_cond is True and fmt != "single"
    use_sr = (bool(sr_cond) if sr_cond is not None else bool(team_flags.get("stealth_rock"))) \
        and fmt == "single"
    spikes = _spikes_layers(conds)
    field = _damage_field(conds, cat, fmt, use_sr=use_sr, spikes=spikes)

    explicit_defender = b.get("opponent_set")
    d_meta = _meta_set(meta_fn, b["vs"], fmt, explicit_defender, dex_fn=dex_fn)
    if explicit_defender:
        dset = _explicit_set(explicit_defender)
        defender0 = {"name": b["vs"], **dset}
        d_prov = {"source": "explicit", "confidence": "high",
                  "note": "opponent set supplied by the benchmark", "prevalence": None}
    elif d_meta:
        base_ability = d_meta.get("base_ability") or (
            (((d_meta.get("set") or {}).get("ability") or {}).get("name"))
            if isinstance(d_meta.get("set"), dict) else None)
        defender0 = {"name": b["vs"], "ability": d_meta.get("ability"), "item": d_meta.get("item"),
                     "nature": d_meta.get("nature") or "Hardy", "sps": d_meta.get("sps") or {},
                     "run_form": d_meta.get("run_form"), "base_ability": base_ability}
        d_prov = {"source": d_meta.get("source") or "meta", "confidence": d_meta.get("confidence", "medium"),
                  "note": d_meta.get("note"), "prevalence": d_meta.get("prevalence")}
    else:
        defender0 = {"name": b["vs"], "nature": "Hardy", "sps": {}}
        d_prov = {"source": "synthetic", "confidence": "low",
                  "note": "no meta set for the target; 0-investment defender — a real bulky set survives more"}
    defender = _mega_resolve_attacker(defender0, dex_fn, item_fn)     # target's Mega form (bulk/ability)

    mc_conf, mc_note = _member_completeness(member)
    base_sps = _sps_from_spread(member.get("spread"))
    cur = base_sps[off_stat]
    cur_nature = member.get("nature") or "Hardy"
    # Hazards chip the target at SWITCH-IN (pre-Mega): typing = the BASE species' (a Charizard-X
    # target takes SR on Fire/Flying, not Fire/Dragon), and immunity uses only the base-phase ability.
    target_base_name = defender.get("base_name") or _base_form_name(b["vs"], dex_fn)
    target_types = list((_dex_facts(target_base_name, dex_fn).get("types")) or [])
    d_haz_abilities = _hazard_abilities_for_modeled_set(defender, d_meta, d_prov)

    # Target HP is fixed (nature changes the ATTACKER, not the target); entry hazards chip the
    # target before we hit, which HELPS the KO (the mirror of survive's hazards, which chipped US).
    _probe, target_hp = damage_fn(_member_ncp(calc_member, base_sps), defender, move, field)
    if not target_hp:
        return {**base, "result": "skipped", "note": "could not resolve the target's HP"}
    spikes_chip = _spikes_chip(target_types, target_hp, spikes, abilities=d_haz_abilities,
                               item=defender.get("item"))
    hazard_chip = (_sr_chip(target_types, target_hp, abilities=d_haz_abilities) if use_sr else 0) \
        + spikes_chip
    eff_hp = target_hp - hazard_chip

    # Solve BOTH KO tiers (OHKO, guaranteed 2HKO) at the requested probability.
    ko_min: dict[int, int | None] = {}
    tiers: dict[int, dict[str, Any]] = {}
    rolls_cache: dict[int, list[int]] = {}
    engine_koc: dict[int, dict] = {}
    for h in (1, 2):
        # The rolls don't depend on `hits`, so tier 2 reuses tier 1's batch (mirror of the survive side).
        mt, _c, koc = _kill_min_sp(
            calc_member, cur_nature, defender, move, field, off_stat, base_sps,
            eff_hp, h, target, damage_fn=damage_fn, damage_batch_fn=damage_batch_fn,
            precomputed=rolls_cache if h == 2 else None,
            precomputed_kochance=engine_koc if h == 2 else None,
        )
        ko_min[h] = mt
        if h == 1:
            rolls_cache, engine_koc = _c, koc
        t = {"hits": h, "label": "OHKO" if h == 1 else "2HKO", "ko_exact": h == 1,
             "ko_basis": ("engine_recovery_aware" if h >= 2 and engine_koc else
                          "exact_single_hit" if h == 1 else "static_repeat_fallback"),
             **_outcome(mt, cur, base_sps, off_stat)}
        if t["result"] == "already" and mt is not None:        # slack = pullable SP while still securing it
            t["slack_sp"] = cur - mt
        # The engine ko_chance rides the batch result (per SP point), which tier 2 reuses from tier 1 —
        # read it from the h=1 capture so both tiers stay annotated.
        eng = engine_koc.get(mt) if mt is not None else None
        if eng:
            t["engine_ko_chance"] = eng
        tiers[h] = t
    ko_tiers = [tiers[1], tiers[2]]
    requested_hits = KILL_HITS[kind]
    headline = tiers[requested_hits]
    hits, label, min_total = headline["hits"], headline["label"], ko_min[headline["hits"]]
    probability_lanes: list[dict[str, Any]] = []
    for lane_target in ("guaranteed", "likely", "any"):
        lane_min, _rc, lane_koc = _kill_min_sp(
            calc_member, cur_nature, defender, move, field, off_stat, base_sps,
            eff_hp, hits, lane_target, damage_fn=damage_fn,
            damage_batch_fn=damage_batch_fn, precomputed=rolls_cache,
            precomputed_kochance=engine_koc,
        )
        probability_lanes.append({
            "probability": lane_target,
            "primary": lane_target == target,
            "stat": off_spread,
            "scope": "offense_axis",
            "ko_basis": ("engine_recovery_aware" if hits >= 2 and (lane_koc or engine_koc)
                         else "exact_single_hit" if hits == 1 else "static_repeat_fallback"),
            **_outcome(lane_min, cur, base_sps, off_stat),
        })

    card: dict[str, Any] = {
        **base, "category": cat, "stat": off_spread, "hits": hits, "probability": target,
        "stealth_rock": use_sr, "spikes": spikes, "ko_tiers": ko_tiers,
        "probability_lanes": probability_lanes,
        "ko_basis": headline["ko_basis"],
        # Only an OHKO is exact. A 2HKO predicate is `ko_roll * 2 >= hp` — static: it repeats the first
        # hit and does NOT model between-turn recovery (Sitrus/Leftovers), ability shifts (Draco Meteor /
        # Stamina / Multiscale), recoil or field changes, so it can claim a KO that a berry denies (audit
        # 2026-06-24). Surfaced as a flag + assumption, not silently sold as a guaranteed 2HKO.
        "ko_exact": hits == 1,
        "confidence": completeness.min_confidence(d_prov.get("confidence", "medium"), mc_conf),
        "defender": {"source": d_prov.get("source"), "ability": defender.get("ability"),
                     "item": defender.get("item"), "nature": defender.get("nature"),
                     "sps": defender.get("sps"), "prevalence": d_prov.get("prevalence")},
        "assumptions": [f"target = {d_prov.get('source')} defensive set",
                        f"requested {label} is the primary target; the adjacent KO tier is contextual"]
        + (["STATIC 2HKO fallback: repeats hit 1; ignores between-turn recovery (Sitrus/Leftovers), "
            "ability shifts (Draco Meteor / Stamina / Multiscale), recoil & field changes — a berry/heal "
            "can deny it. Only OHKO is exact."] if tiers[2]["ko_basis"] == "static_repeat_fallback"
           else ["2HKO uses the engine's recovery-aware KO chance; OHKO remains the exact single-hit tier."])
        + (["defender fields are independent meta marginals — exact ability+item+nature combo may not co-occur"]
           if d_prov.get("source") == "meta" else [])
        + (["our Stealth Rock chip on the target modelled (singles; explicit condition wins, else "
            "team-carries; chip resolves at switch-in — pre-Mega typing/abilities)"] if use_sr else [])
        + (["Stealth Rock was explicitly requested but ignored: this tune path only models entry "
            "hazards in singles, so no Stealth Rock chip was applied"] if sr_ignored else [])
        + ([f"{spikes} layer(s) of Spikes chip on grounded targets modelled (pre-Mega form)"] if spikes else [])
        + (["target Mega form modelled (Mega stats/typing/ability)"] if defender.get("mega_ability") else [])
        + (["Mega form modelled for our attacker"] if eff["is_mega"] else [])
        + ([mc_note] if mc_note else []),
        "evidence": {"facts": [{"source": "ncp", "ref": "damage rolls"},
                               {"source": "dex", "ref": "target types/stats"},
                               {"source": "meta", "ref": "target modal defensive set"}],
                     "note": (d_prov.get("note") or "target set") + (f" | {mc_note}" if mc_note else "")},
    }
    if sr_ignored:
        card["stealth_rock_requested"] = True
        card["stealth_rock_ignored"] = "doubles_not_modelled"

    def _hazard_suffix() -> str:
        return (" after Stealth Rock" if use_sr else "") + (f" after {spikes} layer(s) of Spikes" if spikes else "")

    def _tier_phrase(t: dict[str, Any]) -> str:
        r, lab = t["result"], t["label"]
        if r == "already":
            return f"already {lab}s {b['vs']} ({target}{_hazard_suffix()})"
        if r == "unreachable":
            return f"cannot {lab} even at {SP_CAP} {off_spread} SP"
        if r == "infeasible":
            return f"{lab} needs +{t['delta_sp']} {off_spread} SP (to {t['need_total']}) — no legal donor capacity"
        funding = t.get("reallocation")
        suffix = (f", reallocating {funding['required_sp']} SP from other invested stats"
                  if funding else "")
        return f"{lab} at +{t['delta_sp']} {off_spread} SP (to {t['need_total']}{suffix})"

    card.update(result=headline["result"], delta_sp=headline["delta_sp"])
    if headline.get("need_total") is not None:
        card["need_total"] = headline["need_total"]
    if headline.get("slack_sp") is not None:
        card["slack_sp"] = headline["slack_sp"]
    if headline.get("reallocation") is not None:
        card["reallocation"] = headline["reallocation"]
    card["note"] = " | ".join(_tier_phrase(t) for t in ko_tiers)

    # Stealth Rock 0/1 dual (§9, mirror of the survive side): when the SR chip is in the KO math, also
    # solve the headline tier WITHOUT it — the rocks may not be up when the KO matters, and a KO that
    # exists only after the chip must be visible as such. Anchored to the headline hits (no-cross rule);
    # only the SR chip flips (Spikes stays as conditioned). Rolls don't depend on the chip, so the
    # tier-1 rolls_cache is reused — zero extra engine calls.
    if use_sr:
        no_sr_field = _damage_field(conds, cat, fmt, use_sr=False, spikes=spikes)
        no_sr_min, _rc, _kc = _kill_min_sp(
            calc_member, cur_nature, defender, move, no_sr_field, off_stat,
            base_sps, target_hp - spikes_chip, hits, target,
            damage_fn=damage_fn, damage_batch_fn=damage_batch_fn,
            precomputed=rolls_cache if hits == 1 else None,
        )
        card["stealth_rock_lane"] = {
            "stealth_rock": False, **_outcome(no_sr_min, cur, base_sps, off_stat)
        }

    # Recovery-aware engine verdict at the headline tier's SP.
    engine_ko = headline.get("engine_ko_chance")
    if engine_ko:
        card["engine_ko_chance"] = engine_ko
    if engine_ko and hits >= 2 and not engine_ko.get("guaranteed"):
        card["assumptions"].append(
            f"engine recovery-aware KO% at {min_total} {off_spread} SP = {engine_ko.get('chance_pct')}% "
            f"(n={engine_ko.get('n')}); this meets the requested {target} threshold but is not guaranteed.")

    # §16.8 nature lanes re-solve the HEADLINE tier's cliff under candidate natures.
    if card["result"] in ("cliff", "infeasible", "unreachable") and not locked:
        m_stats = eff["facts"].get("stats") or {}
        lanes = _nature_lanes(
            off_spread, cur_nature, cur, base_sps,
            {"result": card["result"], "delta_sp": card["delta_sp"]},
            solve_min_fn=lambda nat: _kill_min_sp(calc_member, nat, defender, move, field, off_stat, base_sps,
                                                  eff_hp, hits, target, damage_fn=damage_fn,
                                                  damage_batch_fn=damage_batch_fn)[0],
            invested=_invested_stats(member.get("spread")),
            offense_lean=_stat_orientation(m_stats).get("offense_lean"),
            meta_natures=meta_natures or set())
        if lanes["alternatives"]:
            card["nature_alternatives"] = lanes["alternatives"]
        if lanes["notes"]:
            card["nature_notes"] = lanes["notes"]
        if lanes["unlock"]:
            card["nature_unlock"] = True
    return card


def _team_provides_intimidate(team: dict[str, Any], dex_fn: Callable,
                              item_fn: Callable | None,
                              eff_by_member: dict[int, dict[str, Any]] | None = None) -> bool:
    """Does our team carry an Intimidate provider? Checks BOTH each member's declared (base) ability
    AND its Mega ability (a base-Intimidate mon that Megas away still intimidates at switch-in; a base
    mon that Megas INTO Intimidate re-triggers it on Mega Evolution) — §9 both-sides rule."""
    for m in team.get("pokemon", []):
        if _ability_key(m.get("ability")) == "intimidate":
            return True
        eff = (eff_by_member or {}).get(id(m)) or _effective_form(m, dex_fn, item_fn)
        if _ability_key(eff.get("mega_ability")) == "intimidate":
            return True
    return False


def _team_has_move(team: dict[str, Any], move: str) -> bool:
    """Does any team member carry `move` (e.g. Tailwind / Stealth Rock) — the team-carries gate for a
    field effect we can only rely on when we can actually set it up."""
    return any(move in (m.get("moves") or []) for m in team.get("pokemon", []))


def _apply_benchmark_protections(cards: list[dict[str, Any]],
                                 members: dict[str, dict[str, Any]]) -> None:
    """Annotate reallocation donors with floors from other already-met explicit benchmarks.

    This is constraint reporting, not optimization: it says how much of each current investment can
    move without breaking a benchmark the same tune request already proved. The user may still accept
    that trade; the facts layer never silently deletes the donor.
    """
    floors: dict[str, dict[str, dict[str, Any]]] = {}
    for card in cards:
        member = members.get(card.get("member"))
        stat = card.get("stat")
        need = card.get("need_total")
        if not member or card.get("result") != "already" or stat not in SPREAD_TO_SPS \
                or not isinstance(need, int):
            continue
        species = member.get("species")
        slot = floors.setdefault(species, {}).setdefault(
            stat, {"min_sp": 0, "benchmark_indices": []}
        )
        slot["min_sp"] = max(slot["min_sp"], need)
        slot["benchmark_indices"].append(card.get("benchmark_index"))

    def annotate(node: Any, protected: dict[str, dict[str, Any]]) -> None:
        if isinstance(node, list):
            for value in node:
                annotate(value, protected)
            return
        if not isinstance(node, dict):
            return
        plan = node.get("reallocation")
        if isinstance(plan, dict):
            available = 0
            for donor in plan.get("candidate_donors") or []:
                floor = protected.get(donor.get("stat")) or {}
                protected_sp = min(int(donor.get("current_sp") or 0),
                                   int(floor.get("min_sp") or 0))
                donor["protected_sp"] = protected_sp
                donor["available_without_breaking"] = max(
                    0, int(donor.get("current_sp") or 0) - protected_sp
                )
                donor["protected_by_benchmarks"] = [
                    i for i in (floor.get("benchmark_indices") or []) if i is not None
                ]
                available += donor["available_without_breaking"]
            required = int(plan.get("required_sp") or 0)
            plan["available_without_breaking"] = available
            plan["preserves_existing_benchmarks"] = available >= required
            plan["protected_shortfall_sp"] = max(0, required - available)
        for value in node.values():
            if value is not plan:
                annotate(value, protected)

    for card in cards:
        member = members.get(card.get("member"))
        if member:
            annotate(card, floors.get(member.get("species"), {}))


def tune(team: dict[str, Any], benchmarks: list[dict[str, Any]], *, fmt: str | None = None,
         damage_fn: Callable, move_fn: Callable, dex_fn: Callable,
         meta_fn: Callable | None = None, nature_dist_fn: Callable | None = None,
         locked: list[str] | None = None, damage_batch_fn: Callable | None = None,
         item_fn: Callable | None = None,
         archetypes_fn: Callable | None = None) -> dict[str, Any]:
    fmt_battle = str(fmt or team.get("format") or "single").lower()
    if fmt_battle not in {"single", "double"}:
        fmt_battle = "single"
    dex_fn = _memoized_batch_lookup(dex_fn) or dex_fn
    move_fn = _memoized_batch_lookup(move_fn) or move_fn
    item_fn = _memoized_batch_lookup(item_fn) if item_fn else None
    meta_fn = _memoized_meta_lookup(meta_fn)
    members = {}
    member_effective: dict[int, dict[str, Any]] = {}

    for m in team.get("pokemon", []):
        eff = _effective_form(m, dex_fn, item_fn)
        member_effective[id(m)] = eff
        actual = eff.get("name")
        # Select and lock actual forms only. Registration/base aliases remain solely for provenance.
        if actual in members:
            members[actual] = None  # malformed duplicate form: never silently choose a slot
        else:
            members[actual] = m
    # Team-level field gates (computed once): our Intimidate provider and our Tailwind / Stealth Rock
    # setters. A field effect is only modelled when we can actually put it up (re-audit 2026-07-07).
    team_flags = {
        "intimidate": fmt_battle == "double" and _team_provides_intimidate(
            team, dex_fn, item_fn, eff_by_member=member_effective),
        "tailwind": fmt_battle == "double" and _team_has_move(team, "Tailwind"),
        "stealth_rock": fmt_battle == "single" and _team_has_move(team, "Stealth Rock"),
    }
    locked_set = set(locked or [])
    locked_members = {id(m) for name, m in members.items() if m is not None and name in locked_set}
    nat_cache: dict[str, dict[str, float]] = {}

    def _member_natures(member: dict[str, Any]) -> dict[str, float]:
        """The member species' meta natures -> usage % (best-effort) — the §16.8 reality gate AND the
        source of each lane's `meta_pct`. Keep the DICT (don't collapse to a set): candidate_natures
        uses it as a membership gate (`n in dist`) but also reads the % so a 2%-run lane isn't shown as
        equal to a 60%-run one (audit 2026-06-24 — the old set() dropped meta_pct)."""
        species = member.get("species")
        names = [(member_effective.get(id(member)) or {}).get("name") or species]
        key = "|".join(n for n in names if n)
        if key not in nat_cache:
            try:
                # Keep the fn's return AS-IS: nature_distribution gives {EN: pct} (carries meta_pct);
                # don't collapse to a set (that dropped the %). candidate_natures handles dict-or-set.
                dist = {}
                if nature_dist_fn:
                    for n in names:
                        dist = nature_dist_fn(n, fmt_battle) or {}
                        if dist:
                            break
                nat_cache[key] = dist
            except Exception:
                nat_cache[key] = {}
        return nat_cache[key]

    cards: list[dict[str, Any]] = []
    notes: list[str] = []
    any_lane = False
    seen_kill_benchmarks: set[str] = set()
    for benchmark_index, b in enumerate(benchmarks):
        sp = b.get("member")
        member = members.get(sp)
        if not member:
            cards.append({"aspect": "identity", "kind": b.get("kind"), "member": sp, "vs": b.get("vs"),
                          "benchmark_index": benchmark_index, "result": "skipped",
                          "reason": "member_not_found_or_ambiguous",
                          "note": "benchmark member must identify exactly one actual battle form on this team"})
            continue
        kind = b.get("kind")
        m_locked, mnat = id(member) in locked_members, _member_natures(member)
        previous_card_count = len(cards)
        if kind == "survive":
            cards.append(_survive_card(member, b, fmt_battle, damage_fn=damage_fn, move_fn=move_fn,
                                       dex_fn=dex_fn, meta_fn=meta_fn, meta_natures=mnat,
                                       locked=m_locked, damage_batch_fn=damage_batch_fn,
                                       item_fn=item_fn, team_flags=team_flags,
                                       effective=member_effective.get(id(member))))
        elif kind == "outspeed":
            cards.append(_outspeed_card(member, b, fmt_battle, damage_fn=damage_fn, move_fn=move_fn,
                                        dex_fn=dex_fn, meta_fn=meta_fn, meta_natures=mnat, locked=m_locked,
                                        item_fn=item_fn, team_flags=team_flags,
                                        archetypes_fn=archetypes_fn,
                                        effective=member_effective.get(id(member))))
        elif kind in KILL_HITS:                # ohko / 2hko
            kill_key = json.dumps(b, sort_keys=True, ensure_ascii=False, default=str)
            if kill_key in seen_kill_benchmarks:
                notes.append(f"duplicate kill benchmark for member '{sp}' vs '{b.get('vs')}' "
                             f"move '{b.get('move')}' skipped; one card already shows OHKO and 2HKO tiers")
                continue
            seen_kill_benchmarks.add(kill_key)
            cards.append(_kill_card(member, b, fmt_battle, damage_fn=damage_fn, move_fn=move_fn,
                                    dex_fn=dex_fn, meta_fn=meta_fn, meta_natures=mnat,
                                    locked=m_locked, damage_batch_fn=damage_batch_fn,
                                    item_fn=item_fn, team_flags=team_flags,
                                    effective=member_effective.get(id(member))))
        else:
            notes.append(f"benchmark kind '{kind}' not supported (survive/outspeed/ohko/2hko)")
        if len(cards) > previous_card_count:
            if member.get("species") != sp:
                cards[-1]["registration_member"] = member.get("species")
            cards[-1]["member"] = sp
            cards[-1]["benchmark_index"] = benchmark_index
            any_lane = any_lane or bool(cards[-1].get("nature_alternatives"))

    _apply_benchmark_protections(cards, members)
    notes.append("Cliff cards preserve explicit benchmark order; the facts layer assigns no composite score. "
                 "Pick which cliffs to fund from unused SP or by "
                 "reallocating other investments — candidate donors expose capacity and opportunity cost, but "
                 "the tool does not choose a donor or an optimal spread; competing cliffs share the 66 SP budget.")
    if any_lane:
        notes.append("`nature_alternatives` on a card are NATURE LANES: the same cliff solved "
                     "under a different nature, kept whenever a meta-observed nature saves SP or unlocks "
                     "an unreachable cliff. "
                     "They are opportunity-cost facts (the penalty stat is shown), NEVER ranked or recommended — "
                     "a nature change is a whole-spread, single-slot commitment that is yours to make.")
    return {"kind": "tune", "format": fmt_battle, "cards": cards, "notes": notes}


def format_tune_md(d: dict[str, Any]) -> str:
    lines = [f"# {i18n.t('tn_title', fmt=d['format'])}"]
    if not d["cards"]:
        lines.append("\n" + i18n.t("tn_no_cards"))
    for c in d["cards"]:
        who = f" {c['member']}" if c.get("member") else ""
        head = f"- **{c['aspect']}/{c['kind']}**{who} vs {c.get('vs')}"
        if c.get("move"):
            head += f" ({c['move']})"
        if c.get("nature_unlock"):
            head += "  " + i18n.t("tn_nature_unlock_tag")
        lines.append(head + f" — {c.get('result')}: {c.get('note', '')}")
        ek = c.get("engine_ko_chance")
        if ek:
            verdict = (i18n.t("tn_guaranteed") if ek.get("guaranteed")
                       else i18n.t("tn_not_guaranteed", pct=ek.get('chance_pct')))
            note = ("  — " + i18n.t("tn_recovery_note")
                    if not ek.get("guaranteed") and c.get("hits", 1) >= 2 else "")
            lines.append(f"    · {i18n.t('tn_engine_ko')}: {verdict}{note}")
        realloc = c.get("reallocation")
        if realloc:
            donors = ", ".join(
                f"{d.get('stat')} {d.get('current_sp')} SP"
                for d in realloc.get("candidate_donors", [])
            )
            lines.append(
                f"    · reallocation: move {realloc.get('required_sp')} SP from other invested "
                f"stats; candidate donors: {donors or 'none'} (re-check affected benchmarks)"
            )

        # Secondary lanes (0/1 duals, anchored to the headline tier — never crossed with the other tier).
        def _lane_verdict(L: dict[str, Any]) -> str:
            r = L.get("result")
            if r == "already":
                return f"{r}"
            if r in ("cliff", "infeasible"):
                need = i18n.t("tn_need_to", total=L["need_total"]) if L.get("need_total") is not None else ""
                return f"{r} (+{L.get('delta_sp')} SP{need})"
            return f"{r}"
        for key, lane in (("tn_intimidate_lane", c.get("intimidate")),
                          ("tn_tailwind_lane", c.get("tailwind_lane")),
                          ("tn_sr_lane", c.get("stealth_rock_lane"))):
            if lane:
                lines.append(f"    · {i18n.t(key)}: {_lane_verdict(lane)}")
        for alt in c.get("nature_alternatives", []):
            tag = i18n.t("tn_unlocks") if alt.get("unlock") else i18n.t("tn_saves_sp", n=alt.get('saves_sp'))
            need = i18n.t("tn_need_to", total=alt['need_total']) if alt.get("need_total") is not None else ""
            lines.append(f"    · {i18n.t('tn_nature_lane')} **{alt['nature']}**: {alt['result']} (+{alt.get('delta_sp')} SP{need}; "
                         f"{tag}) — {i18n.t('tn_opportunity_cost')}: {alt['opportunity_cost']}")
        for n in c.get("nature_notes", []):
            lines.append(f"    · {i18n.t('tn_nature_note')}: {n}")
    if d.get("notes"):
        lines.append("\n## " + i18n.t("notes"))
        lines += [f"- {n}" for n in d["notes"]]
    return "\n".join(lines)
