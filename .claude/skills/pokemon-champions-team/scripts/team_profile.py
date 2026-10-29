#!/usr/bin/env python
"""Structural profile (UEP S): ONE archetype-agnostic structural vector for any 1–6 team.

The shared vocabulary for landscape aggregation (P4), slate comparison (P5) and observed-team
overlap (P7): all three must count the SAME structural signals or their numbers can't be compared.

Discipline (plan §S — the banned-vocabulary test pins it):
- FACTS ONLY, all countable/explainable: contributors, counts, bucket distributions.
- NO archetype labels ("balance"/"stall"/"hyper offense" never appear as output values or keys) and
  NO similarity/fit/score scalars. An archetype is something a READER may see when comparing this
  vector against the landscape's real distributions — it is never named here.
- PURE AGGREGATION over the existing detectors (diagnose_defense/offense/speed/roles,
  cliffs.champ_speed, selection's Mega-form resolution). This module invents NO new detection; if a
  signal is missing, add it to the diagnose layer where every consumer gets it.

Injectable (dex_fn/move_fn/item_fn) like the diagnose operators, so it is unit-testable offline.
"""
from __future__ import annotations

from typing import Any, Callable

from cliffs import champ_speed, SP_CAP
from diagnose import diagnose_defense, diagnose_offense, diagnose_speed, diagnose_roles
from selection import _mega_form_for
from team_io import team_from_dict
import completeness

# Max-speed-line bucket boundaries (a mechanical, tunable knob — objective dims, not quality tiers).
# Champions final-speed lines at max investment mostly land 100–200; <100 = the Trick-Room-leaning tail.
SPEED_BUCKET_EDGES = (100, 120, 140, 160, 180)


def _bucket(speed: int) -> str:
    prev = None
    for edge in SPEED_BUCKET_EDGES:
        if speed < edge:
            return f"{prev}-{edge - 1}" if prev is not None else f"<{edge}"
        prev = edge
    return f">={SPEED_BUCKET_EDGES[-1]}"


def _control_mode(move: str) -> str:
    key = (move or "").strip().lower()
    if key == "tailwind":
        return "tailwind"
    if key == "trick room":
        return "trickroom"
    return "soft"                       # Icy Wind / Thunder Wave / Sticky Web / ... (foe-side slowdown)


def profile(team: dict[str, Any], *, dex_fn: Callable, move_fn: Callable,
            item_fn: Callable) -> dict[str, Any]:
    """The structural vector. `team` is a team-json dict; facts come from the injected siblings."""
    t = team_from_dict(team, retain_registration=True)
    species = [m.species for m in t.pokemon if m.species]
    moves = sorted({mv for m in t.pokemon for mv in (m.moves or []) if mv})
    items = sorted({m.item for m in t.pokemon if m.item})
    item_info = item_fn(items) if items else {}
    candidate_forms = sorted({f for it in item_info.values() for f in (it.get("required_by") or [])})
    registrations = [m._registration.get("species") for m in t.pokemon
                     if m._registration and m._registration.get("species")]
    facts = dex_fn(species + candidate_forms + registrations) or {}
    move_facts = move_fn(moves) if moves else {}

    defense = diagnose_defense(t, facts)
    offense = diagnose_offense(t, facts, move_facts)
    speed = diagnose_speed(t, facts, move_facts)
    roles = diagnose_roles(t, facts)

    # --- speed control: mode -> contributors (multi-valued; "none" only when nothing is carried) ---
    sc = speed.get("speed_control") or {}
    contributors: dict[str, list[dict[str, str]]] = {}
    for cm in sc.get("moves") or []:
        contributors.setdefault(_control_mode(cm["move"]), []).append(
            {"species": cm["species"], "via": cm["move"]})
    for ci in sc.get("items") or []:
        contributors.setdefault("scarf", []).append({"species": ci["species"], "via": ci["item"]})
    # Weather/terrain speed abilities are speed control too (Swift Swim & co.) — kept as their own
    # mode so a rain team's structural signature is not lumped into "soft" foe-side slowdown.
    for ca in sc.get("abilities") or []:
        contributors.setdefault("ability", []).append({"species": ca["species"], "via": ca["ability"]})
    speed_control_mode = {"modes": sorted(contributors) or ["none"], "contributors": contributors}

    # --- speed buckets: each member's MAX-Spe line (max SP + positive nature; items/abilities are
    # situational and reported via speed_control, not folded into the line) ---
    lines = []
    for sp in species:
        base = ((facts.get(sp) or {}).get("stats") or {}).get("spe")
        if base is None:
            continue
        mx = champ_speed(base, SP_CAP, "Jolly")
        lines.append({"species": sp, "base_speed": base, "max_speed": mx, "bucket": _bucket(mx)})
    lines.sort(key=lambda x: (-x["max_speed"], x["species"]))
    buckets: dict[str, list[str]] = {}
    for ln in lines:
        buckets.setdefault(ln["bucket"], []).append(ln["species"])

    # --- role composition: checklist counts with bearers (present/absent stays neutral framing) ---
    role_composition = {
        tag: {"count": len(cov["bearers"]), "bearers": [b["species"] for b in cov["bearers"]]}
        for tag, cov in (roles.get("coverage") or {}).items()}

    # --- offense: the diagnose-owned attack-type inventory (-ate skins + authoritative-moveset gate
    # live THERE, single implementation) + defending-type coverage classes + phys/spec lean counts ---
    by_def = offense.get("by_defense_type") or {}
    lean_counts: dict[str, int] = {}
    for mm in roles.get("members") or []:
        lean = mm["stat_orientation"]["offense_lean"]
        lean_counts[lean] = lean_counts.get(lean, 0) + 1
    attack_types = offense.get("attack_types") or {}
    off_vec = {
        "stab_attack_types": list(attack_types.get("stab") or []),
        "other_attack_types": list(attack_types.get("other") or []),
        "covered_defending_types": sorted(d for d, v in by_def.items() if v.get("class") == "covered"),
        "thin_defending_types": list(offense.get("thin") or []),
        "hard_gap_defending_types": list(offense.get("hard_gaps") or []),
        "gaps_confirmed": offense.get("gaps_confirmed"),
        "lean_counts": lean_counts,
    }

    # --- defense: resist/weak/immune member counts per attacking type (diagnose's clean by-attack
    # lists — never parsed out of the decorated per-member display labels) + concentration facts ---
    by_attack = defense.get("by_attack_type") or {}
    def _counts(key: str) -> dict[str, int]:
        return {atk: len(v[key]) for atk, v in sorted(by_attack.items()) if v.get(key)}
    def_vec = {
        "weak_counts": _counts("weak"),
        "resist_counts": _counts("resist"),
        "immune_counts": _counts("immune"),
        "weakness_concentration": [
            {"type": c["type"], "weak_count": c["weak_count"], "share": c["share"]}
            for c in (defense.get("weakness_concentration") or [])],
    }

    # --- mega usage: who can / would Mega (facts; ONE per battle is select's law, not repeated here) ---
    mega = []
    unknown_mega_members = []
    for m in t.pokemon:
        f = facts.get(m.species) or {}
        form = _mega_form_for(
            {"species": m.species, "item": m.item}, f, item_info, facts)
        if form:
            mega.append({"member": m.species, "form": form})
        # A roster-only base form with an unpublished item is NOT evidence of no Mega.
        # Explicit Mega forms are observed species facts; a known non-Mega-capable form
        # cannot register a Mega regardless of its missing set. Otherwise trust only an
        # authoritative set (including an explicitly empty item on a full observed set).
        registration_known = bool(f.get("found")) and (
            bool(f.get("is_mega"))
            or ("mega_forms" in f and not f["mega_forms"])
            or completeness.set_authoritative(m.completeness, has_moves=bool(m.moves))
        )
        if not registration_known:
            unknown_mega_members.append(m.species)

    return {
        "kind": "profile",
        "format": (t.format or team.get("format")),
        "team_size": len(species),
        "partial": len(species) < 6,
        "speed_control_mode": speed_control_mode,
        "speed_lines": lines,
        "speed_buckets": buckets,
        "role_composition": role_composition,
        "offense": off_vec,
        "defense": def_vec,
        "mega_usage": mega,
        "mega_registration": {
            "count": None if unknown_mega_members else len(mega),
            "unknown_members": unknown_mega_members,
        },
        # Aggregation carries the source operators' honesty markers verbatim.
        "confidences": {k: {"confidence": d.get("confidence"), "reason": d.get("confidence_reason")}
                        for k, d in (("defense", defense), ("offense", offense),
                                     ("speed", speed), ("roles", roles))},
        "notes": [
            "structural FACTS (counts, contributors, bucket distributions) — never an archetype "
            "label and never a similarity/fit score; archetypes may emerge only when a reader "
            "compares this vector against the landscape's real distributions.",
            "max_speed lines are base + max SP + positive nature; situational multipliers (scarf, "
            "weather abilities, Tailwind) are reported under speed_control_mode instead of being "
            "folded into the line.",
            "pure aggregation over diagnose_defense/offense/speed/roles — signals, vocabularies and "
            "completeness handling are theirs (e.g. non-authoritative movesets are not counted).",
        ],
    }
