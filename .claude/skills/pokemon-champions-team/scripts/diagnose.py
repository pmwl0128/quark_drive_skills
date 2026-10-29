#!/usr/bin/env python
"""Team diagnostics (M2). Accepts partial teams (1-6). Each result carries
evidence + confidence + reason. The model decides what to do;
this only reports objective facts. No team-strength score.

Implemented: defense (type-matchup coverage + weakness concentration),
offense (coverage by reliability), speed (team landscape + control inventory, in context),
roles (objective functional signals + compression — never a prescriptive role label/score).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from team_io import Team  # noqa: E402
from typechart import (  # noqa: E402
    TYPES, effectiveness, effectiveness_for_member, berry_for_attack, ate_skin_type,
)
from cliffs import (  # noqa: E402
    champ_speed, defensive_headroom, SPEED_ITEM_MULT, WEATHER_SPEED_ABILITIES,
    TERRAIN_SPEED_ABILITIES,
)
import completeness  # noqa: E402
import team_i18n as i18n  # noqa: E402
import handover  # noqa: E402
import repset  # noqa: E402
import rules  # noqa: E402

WEAK_CONCENTRATION_MIN = 2          # >=2 members weak to a type is worth surfacing
HIGH_SEVERITY_SHARE = 0.5           # >=50% of the team weak == high severity


def _certain_ability(member_ability: str | None, dex_abilities: list | None) -> tuple[str | None, bool]:
    """The ability to use for capability claims: the user's DECLARED ability, or — only when the
    species has exactly ONE legal ability — that sole ability. Otherwise (None, True) = unknown.

    The dex returns the LEGAL ability list, not a usage default, so picking abilities[0] would
    fabricate capabilities the set may not actually have (weather-speed, Intimidate, Levitate
    immunity, Regenerator, ...) and pollute roles' compression. When the ability is ambiguous
    (2+ legal) and undeclared we keep it unknown rather than guess (audit 2026-06-22 / P2-a)."""
    if member_ability:
        return member_ability, False
    ab = [a for a in (dex_abilities or []) if a]
    if len(ab) == 1:
        return ab[0], False
    return None, True


def _fmt_mult(m: float) -> str:
    return f"{m:g}x"


def diagnose_defense(team: Team, facts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Per-attack-type resist/weak/immune tallies + weakness concentration.

    facts: species -> dex fact dict (from dexlink.lookup_pokemon).
    Members whose dex facts are missing/not-found are skipped (validate covers legality);
    they are listed under `skipped` so the report is honest about partial coverage.
    """
    members: list[tuple[str, list[str], str | None, str | None, bool]] = []
    skipped: list[str] = []
    for m in team.pokemon:
        f = facts.get(m.species)
        if not f or not f.get("found"):
            skipped.append(m.species or "(blank)")
            continue
        types = list(f.get("types", []))
        ability, ability_unknown = _certain_ability(m.ability, f.get("abilities"))
        members.append((m.species, types, ability, m.item, ability_unknown))

    by_attack: dict[str, dict[str, Any]] = {}
    for atk in TYPES:
        weak, resist, immune = [], [], []
        neutral = 0
        for name, types, ability, _item, _ in members:
            e = effectiveness_for_member(types, ability, atk)
            if e == 0:
                immune.append(name)
            elif e > 1:
                weak.append(name)
            elif e < 1:
                resist.append(name)
            else:
                neutral += 1
        by_attack[atk] = {"weak": weak, "resist": resist, "immune": immune, "neutral_count": neutral}

    per_member: dict[str, dict[str, list[str]]] = {}
    unknown_ability: list[str] = []
    for name, types, ability, item, is_unknown in members:
        w, r, im = [], [], []
        for atk in TYPES:
            e = effectiveness_for_member(types, ability, atk)
            if e == 0:
                im.append(atk)
            elif e > 1:
                label = f"{atk} {_fmt_mult(e)}"
                berry = berry_for_attack(item, atk)
                if berry:
                    label += f" ({berry}: first hit halved)"
                w.append(label)
            elif e < 1:
                r.append(atk)
        per_member[name] = {"weak": w, "resist": r, "immune": im}
        if is_unknown:
            unknown_ability.append(name)

    size = len(members)
    concentration = []
    for atk, d in by_attack.items():
        wc = len(d["weak"])
        if wc >= WEAK_CONCENTRATION_MIN:
            share = round(wc / size, 2) if size else 0.0
            concentration.append({
                "type": atk, "weak_count": wc, "weak_members": d["weak"],
                "share": share, "severity": "high" if share >= HIGH_SEVERITY_SHARE else "medium",
            })
    concentration.sort(key=lambda x: (-x["weak_count"], x["type"]))

    notes = [
        "Modeled: type chart; immunity abilities (Levitate/absorbs/Sap Sipper/Earth Eater); "
        "damage-factor abilities (Thick Fat/Heatproof/Water Bubble/Purifying Salt/Fluffy); "
        "Filter/Solid Rock (super-effective x0.75); resist berries (annotated, one-time). "
        "NOT modeled — need a physical/special, HP, or move-property axis, so use ncp for exact numbers: "
        "Fur Coat/Ice Scales (phys/spec halving), Multiscale/Marvel Scale (HP/status), "
        "Bulletproof/Soundproof/Overcoat (move-class immunity).",
    ]
    if unknown_ability:
        notes.append(
            "Ability unspecified (2+ legal abilities) for: " + ", ".join(unknown_ability)
            + " — ability-based immunities/resists (e.g. Levitate) NOT applied; specify each "
            "member's ability for exact immunities."
        )
    if skipped:
        notes.append("Skipped (no dex facts): " + ", ".join(skipped) + ".")

    assumptions = [
        "type chart assumed standard Gen6+ (Champions not verified to differ)",
        "type-layer only: phys/spec halving, HP/status, and move-class immunity NOT modelled — use ncp",
    ]
    if unknown_ability:
        assumptions.append("ability left unknown where unspecified + ambiguous (no ability mods applied): "
                           + ", ".join(unknown_ability))

    return {
        "kind": "defense",
        "team_size": size,
        "partial": size < 6,
        "weakness_concentration": concentration,
        "by_attack_type": by_attack,
        "per_member": per_member,
        "skipped": skipped,
        # An unspecified ability on a 2+-ability member means ability immunities/resists (Levitate,
        # Water Absorb, ...) could NOT be applied, so the reported weaknesses may be wrong (a Levitate
        # Rotom shown weak to Ground). That genuinely lowers trust — so confidence drops to medium, it
        # is not hardcoded high while the reason already says ability-unspecified (audit 2026-06-28).
        "confidence": "medium" if unknown_ability else "high",
        "confidence_reason": "ability-unspecified" if unknown_ability else None,
        "assumptions": assumptions,
        "evidence": {
            "facts": [{"source": "dex", "ref": "types + abilities"}],
            "assumptions": assumptions,
            "table": "builtin 18x18 type chart + ability immunity/factor mods",
        },
        "notes": notes,
    }


def _report_heading(title_key: str, d: dict[str, Any], *, reason: bool = False) -> str:
    count_key = "team_count_partial" if d["partial"] else "team_count"
    confidence = i18n.t(f"confidence_{d['confidence']}")
    suffix = ""
    if reason and d.get("confidence_reason"):
        why = i18n.t(f"confidence_reason_{d['confidence_reason']}")
        suffix = i18n.t("confidence_reason_suffix", reason=why)
    cjk = i18n.lang() in ("zh", "ja")
    return (f"# {i18n.t(title_key)}{'' if cjk else ' '}{i18n.t(count_key, n=d['team_size'])} — "
            f"{i18n.t('confidence')}{'：' if cjk else ': '}{confidence}{suffix}")


def _join(values: list[str] | tuple[str, ...]) -> str:
    return ("、" if i18n.lang() in ("zh", "ja") else ", ").join(values)


def _paren(value: str) -> str:
    return f"（{value}）" if i18n.lang() in ("zh", "ja") else f"({value})"


def _colon() -> str:
    return "：" if i18n.lang() in ("zh", "ja") else ": "


def _semicolon() -> str:
    return "；" if i18n.lang() in ("zh", "ja") else "; "


def _period() -> str:
    return "。" if i18n.lang() in ("zh", "ja") else "."


def _completeness_label(value: str) -> str:
    key = f"completeness_{value}"
    rendered = i18n.t(key)
    return value if rendered == key else rendered


def format_defense_md(d: dict[str, Any]) -> str:
    lines = [_report_heading("def_title", d, reason=True)]
    conc = d["weakness_concentration"]
    if conc:
        lines.append(f"\n## {i18n.t('def_weak_conc')}")
        for c in conc:
            severity = i18n.t(f"def_severity_{c['severity']}")
            lines.append(f"- **{c['type']}**{_colon()}{c['weak_count']}/{d['team_size']} {i18n.t('def_weak')} "
                         f"{_paren(severity)} — {_join(c['weak_members'])}")
    else:
        lines.append(f"\n{i18n.t('def_spread')}")
    lines.append(f"\n## {i18n.t('per_member')}")
    for name, pm in d["per_member"].items():
        weak = _join(pm["weak"]) or "—"
        immune = _join(pm["immune"])
        lines.append(f"- **{name}**{_colon()}{i18n.t('def_weak')} {weak}" +
                     (f"{_semicolon()}{i18n.t('def_immune')} {immune}" if immune else ""))
    if d["notes"]:
        lines.append(f"\n## {i18n.t('notes')}")
        lines += [f"- {n}" for n in d["notes"]]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Offense: attacking coverage by reliability, with STAB as the core axis.
# --------------------------------------------------------------------------- #

def _norm_type(t: str | None) -> str | None:
    if not t:
        return None
    return t[:1].upper() + t[1:].lower()


def diagnose_offense(team: Team, facts: dict[str, dict[str, Any]],
                     move_facts: dict[str, dict[str, Any]],
                     variance_averse: bool = False) -> dict[str, Any]:
    """Which defending types the team can hit super-effectively, classified by reliability.

    STAB is the decisive axis (NOT a 1.5x number): a STAB super-effective source is reliable
    firepower; only-non-STAB coverage is thin (move slots + the attacker's stats/ability/item
    all have to line up). Actual damage also depends on factors not modeled here (Adaptability /
    -ate / Technician, Choice items / Life Orb / type gems, base stats) — use ncp for KO numbers.
    """
    # Collect each member's attacking (physical/special) moves with type + STAB flag.
    attackers: list[tuple[str, set[str], list[dict[str, Any]]]] = []
    skipped: list[str] = []
    incomplete_members: list[dict[str, str]] = []
    unknown_moves: list[str] = []
    ate_used: list[str] = []
    # "Luck lines" (variance_tolerance §19.5): the team's ACTUAL moves that can miss — accuracy < 100
    # straight from the dex (None = no accuracy roll = always hits). A fact surfaced regardless of the
    # knob; `variance_tolerance=averse` only FLAGS it. Damaging AND status (a 60% Hypnosis is a luck
    # line too); only over authoritative movesets (an inferred set would fabricate a phantom line).
    luck_lines: list[dict[str, Any]] = []
    for m in team.pokemon:
        f = facts.get(m.species)
        if not f or not f.get("found"):
            skipped.append(m.species or "(blank)")
            continue
        # A member whose moveset isn't authoritative (species-only / inferred) tells us nothing
        # about coverage — counting it would turn "unknown moves" into a phantom "hard gap".
        if not completeness.moveset_authoritative(m.completeness, has_moves=bool(m.moves)):
            incomplete_members.append({
                "species": m.species or "(blank)",
                "completeness": completeness.effective_level(m.completeness, has_moves=bool(m.moves)),
            })
            continue
        ptypes = {_norm_type(t) for t in f.get("types", [])}
        # Ability gates the -ate skin re-typing; use it only when certain (declared or sole legal
        # ability), else None — picking abilities[0] could fake a Pixilate skin (audit 2026-06-22).
        ability, _ability_unknown = _certain_ability(m.ability, f.get("abilities"))
        atks: list[dict[str, Any]] = []
        for mv in m.moves:
            mf = move_facts.get(mv)
            if not mf or not mf.get("found"):
                unknown_moves.append(mv)
                continue
            acc = mf.get("accuracy")
            if acc is not None and acc < 100:
                luck_lines.append({"species": m.species, "move": mv, "accuracy": acc,
                                   "category": mf.get("category")})
            if (mf.get("category") or "").lower() not in ("physical", "special"):
                continue  # status move
            mtype = _norm_type(mf.get("type"))
            if not mtype:
                continue
            # -ate skins re-type Normal damaging moves and grant STAB (e.g. Sylveon's
            # Hyper Voice is Fairy, not Normal) — without this, coverage/STAB are misjudged.
            skin = ate_skin_type(ability, mtype)
            ate = bool(skin)
            if skin:
                mtype = skin
                if m.species not in ate_used:
                    ate_used.append(m.species)
            atks.append({"move": mv, "type": mtype, "stab": mtype in ptypes, "ate": ate})
        attackers.append((m.species, ptypes, atks))

    by_def: dict[str, dict[str, Any]] = {}
    hard_gaps, thin, centralized = [], [], []
    for D in TYPES:
        sources = []
        for mon, _pt, atks in attackers:
            for a in atks:
                if effectiveness(a["type"], [D]) >= 2:
                    sources.append({"mon": mon, "move": a["move"], "type": a["type"],
                                    "stab": a["stab"], "ate": a.get("ate", False)})
        has_stab = any(s["stab"] for s in sources)
        # "centralized" means a single MEMBER carries this coverage, so losing that one member loses
        # the type — it is about who bears the coverage, not how many moves do. Count distinct
        # bearers, not source moves: one Garchomp with both Earthquake and Bulldoze is still a single
        # point of failure for Ground coverage, not redundant coverage (audit 2026-06-21).
        bearers = {s["mon"] for s in sources}
        if not sources:
            cls = "hard_gap"
            hard_gaps.append(D)
        elif len(bearers) == 1:
            cls = "centralized"
            mon = next(iter(bearers))
            centralized.append({"type": D, "mon": mon, "stab": has_stab})
        elif has_stab:
            cls = "covered"
        else:
            cls = "thin"
            thin.append(D)
        by_def[D] = {"class": cls, "has_stab": has_stab, "sources": sources}

    notes = [
        "STAB is treated as a reliability signal, not a 1.5x number: a STAB super-effective source "
        "is reliable firepower; only-non-STAB coverage is 'thin'.",
        "Firepower factors NOT modeled (use ncp for KO numbers): abilities (Adaptability / -ate skins / "
        "Technician / Sheer Force), items (Choice / Life Orb / type-boost / gems), and base stats.",
        "Coverage is judged from the team's ACTUAL moves (team-json), not the full learnset.",
    ]
    if ate_used:
        notes.append("-ate skin ability re-typed Normal moves (counted as the new type + STAB) for: "
                     + ", ".join(ate_used) + ".")
    if unknown_moves:
        notes.append("Moves not found in dex (ignored): " + ", ".join(sorted(set(unknown_moves))) + ".")
    if skipped:
        notes.append("Skipped (no dex facts): " + ", ".join(skipped) + ".")

    # The team's ATTACK-type inventory — the dual of by_defense_type's defending view: which typed
    # damaging attacks the team actually carries, split by STAB. Kept HERE (not re-derived by
    # consumers) so -ate re-typing and the authoritative-moveset gate above have exactly one
    # implementation (self-audit 2026-07-02: profile's re-walk missed both).
    stab_attack_types = sorted({a["type"] for _, _, atks in attackers for a in atks if a["stab"]})
    other_attack_types = sorted({a["type"] for _, _, atks in attackers for a in atks
                                 if not a["stab"]} - set(stab_attack_types))

    # Gaps are only trustworthy if every counted member has an authoritative moveset; otherwise a
    # reported "hard gap" may just be a member whose moves we don't know.
    gaps_confirmed = not incomplete_members
    confidence = "medium" if gaps_confirmed else "low"
    confidence_reason = "firepower-factors-not-modeled" if gaps_confirmed else "incomplete-movesets"
    if incomplete_members:
        names = ", ".join(f"{m['species']} ({m['completeness']})" for m in incomplete_members)
        notes.append("Coverage gaps are NOT confirmed: these members have no authoritative moveset, "
                     "so their offense is unknown (not counted): " + names + ".")

    assumptions = [
        "STAB treated as a reliability signal, not a 1.5x number",
        "firepower factors NOT modelled (Adaptability/-ate/Technician, Choice/Life Orb/gems, base stats) — use ncp",
        "coverage judged from the team's actual moves, not the full learnset",
        "a move counts as coverage the turn it FIRES: turn costs are not modelled (charge moves like "
        "Solar Beam / Electro Shot spend a turn charging unless their weather is up; recharge moves "
        "lose the next turn)",
    ]
    if not gaps_confirmed:
        assumptions.append("gaps unconfirmed: members with non-authoritative movesets are not counted")
    if ate_used:
        assumptions.append("-ate skin re-typed Normal moves (new type + STAB) for: " + ", ".join(ate_used))
    if luck_lines:
        notes.append(("FLAGGED (variance_tolerance=averse) — " if variance_averse else "")
                     + "luck lines (accuracy < 100%, can miss): "
                     + ", ".join(f"{l['species']} {l['move']} {l['accuracy']}%" for l in luck_lines)
                     + ". A fact (dex accuracy), not a verdict — weigh it against what each move buys.")

    return {
        "kind": "offense",
        "team_size": len(attackers),
        "partial": len(attackers) < 6,
        "hard_gaps": hard_gaps,
        "thin": thin,
        "centralized": centralized,
        "by_defense_type": by_def,
        "attack_types": {"stab": stab_attack_types, "other": other_attack_types},
        "luck_lines": {"moves": luck_lines, "count": len(luck_lines),
                       "flagged": bool(variance_averse and luck_lines)},
        "gaps_confirmed": gaps_confirmed,
        "incomplete_members": incomplete_members,
        "confidence": confidence,
        "confidence_reason": confidence_reason,
        "assumptions": assumptions,
        "evidence": {
            "facts": [{"source": "dex", "ref": "pokemon types + move type/category"}],
            "assumptions": assumptions,
            "table": "builtin type chart; STAB = move type in attacker's types",
        },
        "notes": notes,
    }


def _src_str(s: dict[str, Any]) -> str:
    tag = "STAB" if s["stab"] else "non-STAB"
    if s.get("ate"):
        tag += ", -ate"
    return i18n.t("off_source", mon=s["mon"], move=s["move"], tag=tag)


def format_offense_md(d: dict[str, Any]) -> str:
    # Every type below is a DEFENDING type the team is (or isn't) able to hit super-effectively.
    lines = [_report_heading("off_title", d, reason=True),
             f"_{i18n.t('off_intro')}_"]
    if d.get("incomplete_members"):
        names = _join([f"{m['species']}{_paren(_completeness_label(m['completeness']))}"
                       for m in d["incomplete_members"]])
        lines.append(f"\n> ⚠️ {i18n.t('off_gaps_unconfirmed_warn')}{_colon()}{names}{_period()}")
    if d["hard_gaps"]:
        head = i18n.t('off_hard_gaps') if d.get("gaps_confirmed", True) else i18n.t('off_possible_gaps')
        lines.append(f"\n## {head} — {i18n.t('off_gaps_suffix')}")
        lines.append("- " + _join(d["hard_gaps"]))
    if d["thin"]:
        lines.append(f"\n## {i18n.t('off_thin')}")
        for D in d["thin"]:
            srcs = _join([_src_str(s) for s in d["by_defense_type"][D]["sources"]])
            lines.append(i18n.t("off_vs_types", type=D, sources=srcs))
    if d["centralized"]:
        lines.append(f"\n## {i18n.t('off_centralized')}")
        for c in d["centralized"]:
            tag = "STAB" if c["stab"] else "non-STAB"
            lines.append(i18n.t("off_vs_types_only", type=c["type"], mon=c["mon"], tag=tag))
    covered = [D for D, v in d["by_defense_type"].items() if v["class"] == "covered"]
    if covered:
        lines.append(f"\n## {i18n.t('off_covered')} " + _join(covered))
    luck = d.get("luck_lines") or {}
    if luck.get("moves"):
        flag = " ⚑" if luck.get("flagged") else ""
        lines.append(f"\n## {i18n.t('off_luck_lines')}{flag}")
        for l in luck["moves"]:
            accuracy = f"{l['accuracy']}%"
            lines.append(f"- {l['species']} — {l['move']} {_paren(accuracy)}")
    lines.append(f"\n## {i18n.t('notes')}")
    lines += [f"- {n}" for n in d["notes"]]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Speed: the team's own speed landscape + control inventory, contextualized.
#
# Complementary to `matchup` (which compares each member against meta opponents): speed diagnose
# stays team-internal and needs no meta/ncp — Champions Speed is the integer-exact closed form
# (cliffs.champ_speed, verified against ncp), so the raw landscape is computed, not estimated.
# It reports HOW field conditions reshape that landscape and WHAT speed control the team itself
# carries, all as objective facts — never a "fast enough" score.
# --------------------------------------------------------------------------- #

# Speed modifiers are owned by cliffs.py (single source of truth, shared with matchup) so the two
# operators can never disagree on a Pokemon's Speed again (audit retro 2026-06-22).
# Weather AND terrain field-speed abilities (cliffs split them into two lanes; diagnose must recognize
# both or it silently disagrees with matchup/tune on the same Pokemon — e.g. Surge Surfer).
_WEATHER_SPEED_ABILITIES = {**WEATHER_SPEED_ABILITIES, **TERRAIN_SPEED_ABILITIES}
_SPEED_ITEMS = SPEED_ITEM_MULT
# Abilities that boost Speed under a non-weather trigger (annotated, not folded into the number).
_OTHER_SPEED_ABILITIES = {
    "Unburden": ("after its item is consumed", 2.0),
    "Protosynthesis": ("in sun / on Booster Energy, only if Speed is its highest stat", 1.5),
    "Quark Drive": ("on Electric Terrain / Booster Energy, only if Speed is its highest stat", 1.5),
}
# Move names that are speed control (the move being in the moveset is the fact we report).
_SPEED_CONTROL_MOVES = {
    "tailwind": "doubles your side's Speed", "trick room": "reverses the turn order (slower acts first)",
    "icy wind": "lowers foes' Speed", "electroweb": "lowers foes' Speed", "bulldoze": "lowers foes' Speed",
    "rock tomb": "lowers foe Speed", "low sweep": "lowers foe Speed", "scary face": "sharply lowers foe Speed",
    "string shot": "lowers foes' Speed", "cotton spore": "sharply lowers foes' Speed",
    "thunder wave": "paralysis (halves Speed)", "glare": "paralysis (halves Speed)",
    "nuzzle": "paralysis (halves Speed)", "sticky web": "lowers grounded switch-ins' Speed",
    "quash": "moves a foe last", "after you": "moves an ally next",
}
# A priority ATTACKING move lets a member strike before a faster foe, so the neutral base-Speed
# landscape can read "slower" yet the member still hits first. The priority STAGE is an authoritative
# dex field (signed priority stage int; 0 == normal) — we never hand-maintain it here.
# We surface the FACT (the damaging move is in the set + its dex stage); we do NOT predict turn order —
# that also depends on the foe's own priority, ability-granted priority (Prankster/Gale Wings), and field.
# Excluded because their high stage doesn't mean reliable strike-first damage:
#   Fake Out  (击掌奇袭, +3): first turn out only, a flinch utility that doesn't aim to KO.
#   Upper Hand (快手还击, +3): only triggers when the foe is itself about to use a priority move.
_PRIORITY_ATTACK_EXCLUDE = {"fake out", "upper hand"}

# Moves whose priority stage is CONDITIONAL on a field state the TEAM ITSELF can put up. The dex
# stores the unconditional stage (Grassy Glide is priority 0 there) and the calculator raises it only
# under the matching field (damage_MASTER.checkConditionalPriority), so a flat reading is wrong in
# both directions: counted always, a level-1 role ticks for a team with no terrain at all (37 members
# of the pool learn Grassy Glide and most cannot set the terrain); counted never, the Grassy Surge
# teams that actually run it read as having no priority finisher. Gate it on the team's own source.
# Abilities that grant priority (Gale Wings, Prankster) stay out on purpose: the grant depends on the
# member carrying a move of the right type, which the role pass reads by NAME and cannot verify.
#   move key -> (field condition the team must supply, the stage it then reaches)
_CONDITIONAL_PRIORITY: dict[str, tuple[str, int]] = {"grassy glide": ("grassy_terrain", 1)}
_FIELD_CONDITION_MOVES = {"grassy_terrain": {"grassy terrain"}}
_FIELD_CONDITION_ABILITIES = {"grassy_terrain": {"Grassy Surge", "Seed Sower"}}


def _mega_by_base(facts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Mega forms keyed by the base species that can hold their stone (team.py augments `facts`)."""
    return {ff["base_species"]: ff for ff in facts.values()
            if isinstance(ff, dict) and ff.get("found") and ff.get("is_mega")
            and ff.get("base_species")}


def _ability_phases(member: Any, facts: dict[str, dict[str, Any]],
                    mega_by_base: dict[str, dict[str, Any]]) -> tuple[str | None, str | None, bool]:
    """Retain entry and Mega abilities even when retrieval has already projected the battle form."""
    f = facts.get(member.species) or {}
    base, unknown = _certain_ability(member.ability, f.get("abilities"))
    registration = getattr(member, "_registration", None) or {}
    source = facts.get(registration.get("species")) or {}
    if (f.get("is_mega") and registration.get("species") == f.get("base_species")
            and member.item and member.item == f.get("required_item") and source.get("found")):
        entry = registration.get("ability")
        if entry is None or entry in (source.get("abilities") or []):
            entry, entry_unknown = _certain_ability(entry, source.get("abilities"))
            return entry, base, entry_unknown
    mf = mega_by_base.get(member.species)
    mega = None
    if mf and member.item and mf.get("required_item") == member.item:
        abilities = [a for a in (mf.get("abilities") or []) if a]
        mega = abilities[0] if len(abilities) == 1 else None
    return base, mega, unknown


def _battle_abilities(member: Any, facts: dict[str, dict[str, Any]],
                      mega_by_base: dict[str, dict[str, Any]]) -> set[str]:
    base, mega, _unknown = _ability_phases(member, facts, mega_by_base)
    return {a for a in (base, mega) if a}


def team_field_conditions(team: Team, facts: dict[str, dict[str, Any]]) -> set[str]:
    """Field states the team can put up itself — the gate for a conditional move effect."""
    mega_by_base = _mega_by_base(facts)
    conditions: set[str] = set()
    for m in team.pokemon:
        moves = {(mv or "").strip().lower() for mv in (m.moves or [])}
        abilities = _battle_abilities(m, facts, mega_by_base)
        for condition, names in _FIELD_CONDITION_MOVES.items():
            if moves & names:
                conditions.add(condition)
        for condition, names in _FIELD_CONDITION_ABILITIES.items():
            if abilities & names:
                conditions.add(condition)
    return conditions


def diagnose_speed(team: Team, facts: dict[str, dict[str, Any]],
                   move_facts: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """The team's Speed landscape (neutral closed-form), plus how conditions reshape it and what
    speed control the team carries. Members with no dex facts are skipped (listed honestly).

    Speed uses each member's actual Spe SP + nature when given; when a member declares no spread or
    no nature, neutral (0 Spe SP / neutral nature) is assumed and the member is flagged — the report
    stays honest rather than inventing a spread.

    move_facts (query-name -> dex move dict incl. signed `priority`) is optional: when supplied, each
    member's damaging positive-priority moves are surfaced so a slower member that can still strike
    first is not misread. Without it, priority_moves is simply empty.
    """
    move_facts = move_facts or {}
    field_conditions = team_field_conditions(team, facts)
    members: list[dict[str, Any]] = []
    skipped: list[str] = []
    assumed: list[str] = []
    unknown_ability: list[str] = []
    for m in team.pokemon:
        f = facts.get(m.species)
        if not f or not f.get("found"):
            skipped.append(m.species or "(blank)")
            continue
        base = ((f.get("stats") or {}).get("spe"))
        spread = m.spread or {}
        spe_sp = int(spread.get("spe") or 0)
        sp_known = "spe" in spread
        nature_known = m.nature is not None
        if not sp_known or not nature_known:
            assumed.append(m.species)
        speed = champ_speed(base, spe_sp, m.nature) if base is not None else None

        ability, ability_unknown = _certain_ability(m.ability, f.get("abilities"))
        if ability_unknown:
            unknown_ability.append(m.species)
        modifiers: list[dict[str, Any]] = []
        scarf_speed = None
        if m.item in _SPEED_ITEMS and speed is not None:
            mult = _SPEED_ITEMS[m.item]
            scarf_speed = int(speed * mult)
            modifiers.append({"source": m.item, "kind": "item", "mult": mult,
                              "trigger": "always (locked into one move)", "speed": scarf_speed})
        if ability in _WEATHER_SPEED_ABILITIES:
            trig, mult = _WEATHER_SPEED_ABILITIES[ability]
            modifiers.append({"source": ability, "kind": "ability", "mult": mult,
                              "trigger": "/".join(sorted(trig)),   # trig is a token SET (JSON needs a str)
                              "speed": int(speed * mult) if speed is not None else None})
        if ability in _OTHER_SPEED_ABILITIES:
            trig, mult = _OTHER_SPEED_ABILITIES[ability]
            modifiers.append({"source": ability, "kind": "ability", "mult": mult, "trigger": trig,
                              "speed": int(speed * mult) if speed is not None else None})

        # Damaging moves with a positive dex priority stage (excluding the two too-conditional ones).
        priority_moves: list[dict[str, Any]] = []
        for mv in (m.moves or []):
            key = (mv or "").strip().lower()
            mf = move_facts.get(mv) or {}
            pr = mf.get("priority")
            cat = (mf.get("category") or "").lower()
            if (isinstance(pr, int) and pr > 0 and cat in ("physical", "special")
                    and key not in _PRIORITY_ATTACK_EXCLUDE):
                priority_moves.append({"move": mv, "priority": pr})
            elif key in _CONDITIONAL_PRIORITY and cat in ("physical", "special"):
                condition, stage = _CONDITIONAL_PRIORITY[key]
                if condition in field_conditions:
                    priority_moves.append({"move": mv, "priority": stage, "condition": condition})
        priority_moves.sort(key=lambda p: (-p["priority"], p["move"]))

        members.append({
            "species": m.species, "base_speed": base, "speed": speed,
            "spe_sp": spe_sp, "nature": m.nature,
            "assumed_neutral": (not sp_known or not nature_known),
            "ability": ability, "ability_unknown": ability_unknown,
            "modifiers": modifiers,
            "priority_moves": priority_moves,
        })

    # Neutral landscape ordering (fastest first); members with unknown base Speed sort last.
    ranked = [x for x in members if x["speed"] is not None]
    ranked.sort(key=lambda x: (-x["speed"], x["species"]))
    order = [{"species": x["species"], "speed": x["speed"]} for x in ranked]
    order_under_trick_room = [{"species": x["species"], "speed": x["speed"]}
                              for x in sorted(ranked, key=lambda x: (x["speed"], x["species"]))]

    # Speed-control inventory carried by the team (objective: move in moveset / dex ability / item).
    control_moves, control_abilities, control_items = [], [], []
    present_species = {x["species"] for x in members}
    for m in team.pokemon:
        if m.species not in present_species:
            continue
        for mv in (m.moves or []):
            key = (mv or "").strip().lower()
            if key in _SPEED_CONTROL_MOVES:
                control_moves.append({"species": m.species, "move": mv,
                                      "effect": _SPEED_CONTROL_MOVES[key]})
    for x in members:
        ab = x["ability"]
        if ab in _WEATHER_SPEED_ABILITIES:
            trig, mult = _WEATHER_SPEED_ABILITIES[ab]
            control_abilities.append({"species": x["species"], "ability": ab,
                                      "effect": f"x{mult:g} Speed in {'/'.join(sorted(trig))}"})
        elif ab in _OTHER_SPEED_ABILITIES:
            trig, mult = _OTHER_SPEED_ABILITIES[ab]
            control_abilities.append({"species": x["species"], "ability": ab,
                                      "effect": f"x{mult:g} Speed {trig}"})
        if any(md["kind"] == "item" for md in x["modifiers"]):
            control_items.append({"species": x["species"], "item": next(
                md["source"] for md in x["modifiers"] if md["kind"] == "item")})

    notes = [
        "Speed is the integer-exact Champions closed form (base + Spe SP + nature), verified against ncp.",
        "Tailwind doubles your whole side's Speed (relative order within the side is unchanged); "
        "Trick Room reverses turn order so the SLOWER Pokemon moves first; paralysis halves Speed.",
        "Per-opponent speed comparisons (who outspeeds which meta threat) are the `matchup` operator's "
        "job — this is the team-internal landscape + the speed control you carry.",
    ]
    if any(x["priority_moves"] for x in members):
        notes.append("Priority attacking moves (dex priority stage) let a member strike "
                     "BEFORE a faster foe — the base-Speed order above does not reflect this. Some are still "
                     "conditional (Sucker Punch only if the foe attacks). A move carrying `condition` reaches "
                     "that stage only under a field state THIS team can set (Grassy Glide on Grassy Terrain); "
                     "turn order also depends on the foe's own priority and on ability-granted priority "
                     "(Prankster/Gale Wings/Triage), which is not modeled here. Fake Out (+3, first turn "
                     "only) and Upper Hand (+3, only vs a foe's priority move) are excluded as too situational.")
    if assumed:
        notes.append("Assumed neutral (0 Spe SP / neutral nature) where unspecified: "
                     + ", ".join(sorted(set(assumed))) + " — give each member's spread + nature for exact Speed.")
    if unknown_ability:
        notes.append("Ability unspecified (2+ legal abilities) for: " + ", ".join(sorted(set(unknown_ability)))
                     + " — weather-speed / other ability speed signals are NOT reported for them "
                     "(dex lists legal abilities, not a usage default); specify the ability.")
    if skipped:
        notes.append("Skipped (no dex facts): " + ", ".join(skipped) + ".")

    assumptions = [
        "Speed = Champions closed form (base + Spe SP + nature); exact, no ncp needed",
        "field modifiers (Scarf/weather abilities/Tailwind/Trick Room) are annotated, not folded into one number",
    ]
    if assumed:
        assumptions.append("neutral 0-Spe-SP / neutral nature assumed where unspecified: "
                           + ", ".join(sorted(set(assumed))))

    # Below high when the base number is itself assumed (spread/nature inferred) OR when an unspecified
    # 2+-ability member could hide a conditional speed mod (Swift Swim / Unburden / Protosynthesis), so
    # the landscape may understate a member's speed. Reason is a single controlled token (evidence.
    # CONFIDENCE_REASONS), so when both apply we surface the spread/nature cause (it moves the base
    # number directly) and keep the ability cause in the notes (audit 2026-06-28).
    if assumed:
        confidence, confidence_reason = "medium", "spread-or-nature-inferred"
    elif unknown_ability:
        confidence, confidence_reason = "medium", "ability-unspecified"
    else:
        confidence, confidence_reason = "high", None
    return {
        "kind": "speed",
        "team_size": len(members),
        "partial": len(members) < 6,
        "order": order,
        "order_under_trick_room": order_under_trick_room,
        "members": members,
        "speed_control": {"moves": control_moves, "abilities": control_abilities, "items": control_items},
        "skipped": skipped,
        "confidence": confidence,
        "confidence_reason": confidence_reason,
        "assumptions": assumptions,
        "evidence": {
            "facts": [{"source": "dex", "ref": "base Speed + abilities + types"}],
            "inputs": [{"source": "team-json", "ref": "Spe SP + nature + item + moves"}],
            "assumptions": assumptions,
            "table": "builtin Champions Speed closed form (cliffs.champ_speed)",
        },
        "notes": notes,
    }


_SPEED_EFFECT_I18N = {
    "doubles your side's Speed": "spd_effect_double_side",
    "reverses the turn order (slower acts first)": "spd_effect_trick_room",
    "lowers foes' Speed": "spd_effect_lower_foes",
    "lowers foe Speed": "spd_effect_lower_foe",
    "sharply lowers foe Speed": "spd_effect_sharply_lower_foe",
    "sharply lowers foes' Speed": "spd_effect_sharply_lower_foes",
    "paralysis (halves Speed)": "spd_effect_paralysis",
    "lowers grounded switch-ins' Speed": "spd_effect_sticky_web",
    "moves a foe last": "spd_effect_quash",
    "moves an ally next": "spd_effect_after_you",
}
_OTHER_SPEED_TRIGGER_I18N = {
    "Unburden": "spd_trigger_unburden",
    "Protosynthesis": "spd_trigger_protosynthesis",
    "Quark Drive": "spd_trigger_quark_drive",
}


def _speed_trigger(source: str, raw: str) -> str:
    if source == "Choice Scarf":
        return i18n.t("spd_trigger_scarf")
    if source in _WEATHER_SPEED_ABILITIES:
        trigger_tokens, _ = _WEATHER_SPEED_ABILITIES[source]
        translated = [i18n.t(f"spd_trigger_{token}") for token in sorted(trigger_tokens)]
        return "/".join(dict.fromkeys(translated))
    if source in _OTHER_SPEED_TRIGGER_I18N:
        return i18n.t(_OTHER_SPEED_TRIGGER_I18N[source])
    return raw


def _speed_modifier_md(md: dict[str, Any]) -> str:
    trigger = _speed_trigger(md["source"], md["trigger"])
    key = "spd_modifier" if md.get("speed") is not None else "spd_modifier_no_value"
    return i18n.t(key, source=md["source"], speed=md.get("speed"), trigger=trigger)


def _speed_control_effect(c: dict[str, Any], kind: str) -> str:
    if kind == "move":
        key = _SPEED_EFFECT_I18N.get(c["effect"])
        return i18n.t(key) if key else c["effect"]
    source = c["ability"]
    if source in _WEATHER_SPEED_ABILITIES:
        _triggers, mult = _WEATHER_SPEED_ABILITIES[source]
        return i18n.t("spd_effect_ability", mult=f"{mult:g}", trigger=_speed_trigger(source, ""))
    key = _OTHER_SPEED_TRIGGER_I18N.get(source)
    if key:
        _trigger, mult = _OTHER_SPEED_ABILITIES[source]
        return i18n.t("spd_effect_ability", mult=f"{mult:g}", trigger=i18n.t(key))
    return c.get("effect", "")


def format_speed_md(d: dict[str, Any]) -> str:
    lines = [_report_heading("spd_title", d, reason=True)]
    if d["order"]:
        lines.append(f"\n## {i18n.t('spd_order')}")
        for i, e in enumerate(d["order"], 1):
            member = next(m for m in d["members"] if m["species"] == e["species"])
            tag = f" *{i18n.t('spd_assumed_neutral')}*" if member["assumed_neutral"] else ""
            mods = "".join(_speed_modifier_md(md) for md in member["modifiers"])
            lines.append(f"{i}. **{e['species']}** {e['speed']}{tag}{mods}")
    unknown = [m["species"] for m in d["members"] if m["speed"] is None]
    if unknown:
        lines.append(f"- {i18n.t('spd_base_unknown')} " + _join(unknown))
    prio = [m for m in d["members"] if m.get("priority_moves")]
    if prio:
        lines.append(f"\n## {i18n.t('spd_priority')}")
        for m in prio:
            moves = _join([f"{pm['move']} (+{pm['priority']})" for pm in m["priority_moves"]])
            lines.append(f"- **{m['species']}**{_colon()}{moves}")
    sc = d["speed_control"]
    if sc["moves"] or sc["abilities"] or sc["items"]:
        lines.append(f"\n## {i18n.t('spd_control')}")
        for c in sc["moves"]:
            lines.append(f"- {c['species']}{_colon()}**{c['move']}** — {_speed_control_effect(c, 'move')}")
        for c in sc["abilities"]:
            lines.append(f"- {c['species']}{_colon()}**{c['ability']}** — {_speed_control_effect(c, 'ability')}")
        for c in sc["items"]:
            lines.append(f"- {c['species']}{_colon()}**{c['item']}**")
    else:
        lines.append(f"\n## {i18n.t('spd_control')}\n- {i18n.t('spd_control_none')}")
    lines.append(f"\n## {i18n.t('notes')}")
    lines += [f"- {n}" for n in d["notes"]]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Roles: OBJECTIVE functional signals only.
#
# Pinning a "role label" on a Pokemon is subjective, error-prone, and
# slips into a strength judgment. So this operator NEVER says "X is a wall" or "you are
# missing a pivot, add one". It reports verifiable signals — functional moves carried, base-stat
# orientation, item/ability signals — and a neutral present/not-detected checklist of functional
# categories. The model decides actual roles and what (if anything) to change. No score, no
# prescription. Confidence is 'low' (reason heuristic-role): these are signals, not assignments.
# --------------------------------------------------------------------------- #

# Functional move groups, keyed by lowercase move name. A move may appear in two groups
# (e.g. Sticky Web is both a hazard and speed control) — both signals are reported.
_ROLE_MOVES: dict[str, set[str]] = {
    "setup": {
        "swords dance", "dragon dance", "nasty plot", "calm mind", "bulk up", "quiver dance",
        "shell smash", "work up", "coil", "hone claws", "agility", "rock polish", "autotomize",
        "tail glow", "growth", "shift gear", "victory dance", "clangorous soul", "no retreat",
        "belly drum", "geomancy", "cosmic power", "iron defense", "acid armor", "curse",
        "tidy up", "acupressure", "torch song", "electro shot", "meteor beam", "psyshield bash",
        "aura wheel", "coaching", "decorate", "howl", "aqua step",
    },
    "pivot": {"u-turn", "volt switch", "flip turn", "teleport", "parting shot", "baton pass",
              "memento", "healing wish", "shed tail", "chilly reception"},
    # HARD setup answers (maintainer meta call 2026-07-16): phazing, boost-clearing, and the
    # boost-copy/ignore abilities below (_ABILITY_COVERAGE). On 36% of real singles teams —
    # a core singles need: a team can win without setup of its own, but without anti-setup
    # it lacks a whole answer class. SOFT pressure (Toxic/Yawn forcing boosters out) stays
    # under `status` — it is not a hard answer and must not make this box tick.
    "anti_setup": {"whirlwind", "roar", "dragon tail", "circle throw", "haze", "clear smog",
                   "topsy-turvy"},
    "hazard_set": {"stealth rock", "spikes", "toxic spikes", "sticky web",
                   "stone axe", "ceaseless edge"},
    "hazard_control": {"rapid spin", "defog", "mortal spin", "tidy up", "court change"},
    "recovery": {
        "recover", "roost", "synthesis", "moonlight", "morning sun", "slack off", "soft-boiled",
        "milk drink", "rest", "wish", "shore up", "strength sap", "life dew", "jungle healing",
        "lunar blessing", "heal order", "purify", "healing wish",
    },
    "speed_control": {
        "tailwind", "trick room", "icy wind", "electroweb", "thunder wave", "glare", "nuzzle",
        "sticky web", "scary face", "bulldoze", "rock tomb", "low sweep", "cotton spore",
        "string shot", "quash", "after you", "flame charge", "trailblaze", "rapid spin",
        "aqua step", "drum beating",
    },
    # Terrain is its own axis: weather answers to weather_rewrite, and until this existed a team
    # built on a terrain answered to nothing at all. Removal belongs here for the same reason Defog
    # belongs to hazard_control — controlling the field includes taking it away. The removal side is
    # the dex's own "解除场地 / removes any terrain" set, which is exactly these three moves.
    "terrain_control": {"grassy terrain", "psychic terrain", "electric terrain", "misty terrain",
                        "steel roller", "ice spinner", "defog"},
    "redirection": {"follow me", "rage powder", "spotlight", "ally switch"},
    "screens": {"reflect", "light screen", "aurora veil"},
    "status": {
        "will-o-wisp", "toxic", "thunder wave", "glare", "spore", "sleep powder", "stun spore",
        "yawn", "zap cannon", "nuzzle", "mortal spin",
    },
    # Plan-interruption tools, split out of `status` (survey 2026-07-16: carried by ~36%
    # of M-B singles / ~33% of doubles — an answer class of its own, and `status` mixing
    # ailments with Taunt blurred both).
    # Octolock / Jaw Lock join as trapping: denying the switch is plan interruption in the same
    # sense as Encore, and Octolock additionally drains Def/SpD every turn. Jaw Lock locks the USER
    # too — still a deliberate disruption tool, so it counts, and the cost is the pilot's to weigh.
    "disruption": {"taunt", "encore", "disable", "quash", "imprison", "torment",
                   "trick", "knock off", "perish song", "destiny bond", "psychic noise",
                   "throat chop", "switcheroo", "final gambit", "octolock", "jaw lock"},
    # Opening pressure (doubles staple: 58% of real doubles teams; singles ~8% — there it
    # is a per-mon tactic, not a team slot, so the singles checklist omits it entirely).
    "fake_out": {"fake out"},
    # Damage-pressure reduction on the OPPONENT (Intimidate joins via ability coverage):
    # 64% of real doubles teams, ~28% singles.
    "damage_mitigation": {
        "snarl", "will-o-wisp", "parting shot", "eerie impulse", "growl", "charm",
        "feather dance", "tearful look", "knock off", "breaking swipe", "skitter smack",
        "trop kick", "chilling water", "struggle bug", "mystical fire", "spirit break",
        "memento", "noble roar",
    },
    # Priority ATTACKS — the endgame-finisher asset (carried by ~75% singles / ~69%
    # doubles, stable across rules). NOT simply priority>0 damaging moves: utility
    # priority is excluded — Fake Out (opening flinch), Feint (protect-breaking), Upper
    # Hand (fires only against the foe's own priority — a counter, not a finisher).
    # First Impression stays: a real 90BP kill line, resettable by switching.
    # Entries in _CONDITIONAL_PRIORITY (Grassy Glide) are listed here but only tag a member whose
    # TEAM supplies the field state — see the tagging loop in diagnose_roles.
    "priority_attack": {
        "accelerock", "aqua jet", "bullet punch", "extreme speed",
        "first impression", "grassy glide", "ice shard", "jet punch", "mach punch",
        "quick attack", "shadow sneak", "sucker punch", "vacuum wave", "water shuriken",
    },
    # Weather REWRITE capability (maintainer call 2026-07-16): most setters on real teams
    # are weather-INDEPENDENT (single 35%/double 32% of all teams carry a setter with no
    # weather-benefit ability in the team) — the setter's value is overwriting the
    # opponent's weather. Setter ABILITIES join via ability coverage.
    "weather_rewrite": {"rain dance", "sunny day", "sandstorm", "snowscape", "hail",
                        "chilly reception"},
    "protect": {
        "protect", "detect", "spiky shield", "king's shield", "baneful bunker", "silk trap",
        "burning bulwark",
    },
    # Doubles partner semantics — moves that BUFF or ENABLE an ALLY (not afflict a foe): the
    # doubles-specific support signal (§16.3). Some also live in recovery/speed_control (a move may be
    # in two groups). A signal only — never a "this mon is the support" label.
    # Milk Drink heals the USER OR AN ALLY, so it is both recovery and ally support (a move may be
    # in two groups). Revival Blessing brings a fainted TEAMMATE back — the strongest ally-directed
    # effect in the pool, and support is where it belongs; it is not recovery for anyone on the field.
    "partner_support": {
        "helping hand", "coaching", "decorate", "aromatic mist", "gear up", "magnetic flux",
        "heal pulse", "pollen puff", "life dew", "jungle healing", "after you",
        "acupressure", "baton pass", "psych up", "instruct", "gravity", "feint",
        "milk drink", "revival blessing",
    },
    # Side-wide protection (doubles): guards the WHOLE side for a turn, distinct from single self-protect.
    "side_protect": {"wide guard", "quick guard", "mat block"},
    # AOE / spread damage (doubles level-1 role): every damaging move that hits multiple
    # targets -- both "hits both foes" and "hits all-except-self" ranges (authoritative
    # move-category lists, damaging entries only, then validated present in the M-B dex). A move may
    # also live in speed_control / damage_mitigation (Icy Wind, Snarl, Struggle Bug, Bulldoze)
    # -- both signals report. Doubles-only: a singles team never needs spread by definition.
    "spread": {
        "air cutter", "blizzard", "boomburst", "breaking swipe", "brutal swing", "bulldoze",
        "burning jealousy", "clanging scales", "dazzling gleam", "discharge", "earthquake",
        "electroweb", "eruption", "explosion", "heat wave", "hyper voice", "icy wind",
        "lava plume", "make it rain", "matcha gotcha", "misty explosion", "mortal spin",
        "muddy water", "overdrive", "parabolic charge", "petal blizzard", "rock slide",
        "self-destruct", "sludge wave", "snarl", "sparkling aria", "struggle bug", "surf",
        "water spout",
    },
}
_ROLE_LABELS = {
    "setup": "setup", "anti_setup": "anti-setup", "pivot": "pivot",
    "hazard_set": "hazard setter",
    "hazard_control": "hazard control", "recovery": "recovery", "speed_control": "speed control",
    "redirection": "redirection (doubles)", "screens": "screens", "status": "status/utility",
    "protect": "protect", "partner_support": "partner support (doubles)",
    "side_protect": "side protection (doubles)",
    "disruption": "disruption", "fake_out": "Fake Out pressure (doubles)",
    "damage_mitigation": "damage mitigation", "priority_attack": "priority attack",
    "weather_rewrite": "weather rewrite", "spread": "spread damage (doubles)",
    "terrain_control": "terrain control",
}
# Item signals (objective: the item is declared). Mega stones are intentionally omitted — Mega is
# handled by validate/selection, not a role signal.
_ITEM_SIGNALS = {
    "Choice Band": "choice-locked physical", "Choice Specs": "choice-locked special",
    "Choice Scarf": "choice-locked + x1.5 Speed", "Assault Vest": "special bulk (no status moves)",
    "Eviolite": "bulk (not-fully-evolved)", "Focus Sash": "survives one hit from full HP",
    "Life Orb": "extra power (recoil)", "Leftovers": "passive recovery",
    "Black Sludge": "passive recovery (Poison)", "Sitrus Berry": "one-time recovery",
    "Rocky Helmet": "contact chip", "Mental Herb": "anti-Taunt/Encore (one-time)",
    "Safety Goggles": "weather/powder immunity",
    # M-C made terrain a real axis: four seeds, an extender, and two new surge abilities.
    "Grassy Seed": "Def +1 on Grassy Terrain (one-time)",
    "Electric Seed": "Def +1 on Electric Terrain (one-time)",
    "Psychic Seed": "SpD +1 on Psychic Terrain (one-time)",
    "Misty Seed": "SpD +1 on Misty Terrain (one-time)",
    "Terrain Extender": "terrain lasts 8 turns instead of 5",
    "Air Balloon": "Ground immunity until hit",
    "Eject Button": "switches out when hit (one-time)",
    "Red Card": "forces the attacker out (one-time)",
    "Leek": "crit rate +2 (Farfetch'd line)",
    "Normal Gem": "x1.3 Normal move (one-time)",
    "Binding Band": "binding damage 1/6 instead of 1/8",
}
# Ability signals well-understood as team functions (objective: declared / dex ability).
_ABILITY_SIGNALS = {
    "Intimidate": "lowers foe Attack on entry (support)", "Regenerator": "heals on switch (longevity pivot)",
    "Prankster": "priority status", "Drizzle": "rain setter", "Drought": "sun setter",
    "Sand Stream": "sandstorm setter", "Snow Warning": "snow setter",
    "Electric Surge": "Electric Terrain setter", "Grassy Surge": "Grassy Terrain setter",
    "Misty Surge": "Misty Terrain setter", "Psychic Surge": "Psychic Terrain setter",
    "Seed Sower": "Grassy Terrain setter (on being hit)",
    "Levitate": "Ground immunity (defensive)", "Eelevate": "Ground immunity (defensive)",
    "Magic Bounce": "reflects status/hazards",
    "Unaware": "ignores stat changes (wall)", "Multiscale": "halves damage at full HP (wall)",
    "Imposter": "transforms into the foe incl. boosts (setup answer)",
    "Guard Dog": "immune to Intimidate (Attack +1 instead); cannot be forced out",
    "Rattled": "Speed +1 when hit by Dark/Ghost/Bug or intimidated",
    "Punk Rock": "x1.3 sound moves, halves incoming sound damage",
    "Stakeout": "x2 power against a foe switching in",
    "Steely Spirit": "x1.5 Steel moves for itself AND its ally",
    "Thermal Exchange": "Attack +1 when hit by Fire; cannot be burned",
    "Liquid Ooze": "drain moves damage the drainer instead of healing it",
    "Grass Pelt": "Defense x1.5 on Grassy Terrain",
    "Aura Guard": "halves damage from contact moves",
    "Emergency Exit": "forced back to the party below half HP",
    "Run Away": "switches out through trapping effects",
    "Libero": "becomes the type of the move it uses (once per entry)",
}
_ITEM_SIGNAL_I18N = {
    "Choice Band": "role_signal_choice_band", "Choice Specs": "role_signal_choice_specs",
    "Choice Scarf": "role_signal_choice_scarf", "Assault Vest": "role_signal_assault_vest",
    "Eviolite": "role_signal_eviolite", "Focus Sash": "role_signal_focus_sash",
    "Life Orb": "role_signal_life_orb", "Leftovers": "role_signal_passive_recovery",
    "Black Sludge": "role_signal_passive_recovery_poison", "Sitrus Berry": "role_signal_one_time_recovery",
    "Rocky Helmet": "role_signal_contact_chip", "Mental Herb": "role_signal_mental_herb",
    "Safety Goggles": "role_signal_safety_goggles",
    "Grassy Seed": "role_signal_grassy_seed", "Electric Seed": "role_signal_electric_seed",
    "Psychic Seed": "role_signal_psychic_seed", "Misty Seed": "role_signal_misty_seed",
    "Terrain Extender": "role_signal_terrain_extender", "Air Balloon": "role_signal_air_balloon",
    "Eject Button": "role_signal_eject_button", "Red Card": "role_signal_red_card",
    "Leek": "role_signal_leek", "Normal Gem": "role_signal_normal_gem",
    "Binding Band": "role_signal_binding_band",
}
_ABILITY_SIGNAL_I18N = {
    "Intimidate": "role_signal_intimidate", "Regenerator": "role_signal_regenerator",
    "Prankster": "role_signal_prankster", "Drizzle": "role_signal_rain_setter",
    "Drought": "role_signal_sun_setter", "Sand Stream": "role_signal_sand_setter",
    "Snow Warning": "role_signal_snow_setter", "Electric Surge": "role_signal_electric_terrain",
    "Grassy Surge": "role_signal_grassy_terrain", "Misty Surge": "role_signal_misty_terrain",
    "Psychic Surge": "role_signal_psychic_terrain", "Seed Sower": "role_signal_seed_sower",
    "Levitate": "role_signal_ground_immunity",
    "Eelevate": "role_signal_ground_immunity", "Magic Bounce": "role_signal_magic_bounce",
    "Unaware": "role_signal_unaware", "Multiscale": "role_signal_multiscale",
    "Imposter": "role_signal_imposter",
    "Guard Dog": "role_signal_guard_dog", "Rattled": "role_signal_rattled",
    "Punk Rock": "role_signal_punk_rock", "Stakeout": "role_signal_stakeout",
    "Steely Spirit": "role_signal_steely_spirit", "Thermal Exchange": "role_signal_thermal_exchange",
    "Liquid Ooze": "role_signal_liquid_ooze", "Grass Pelt": "role_signal_grass_pelt",
    "Aura Guard": "role_signal_aura_guard", "Emergency Exit": "role_signal_emergency_exit",
    "Run Away": "role_signal_run_away", "Libero": "role_signal_libero",
}
# Items / abilities that ALSO fulfil a checklist category, so team coverage agrees with the
# per-member item/ability signals instead of contradicting them ("Leftovers — passive recovery"
# while "recovery: not detected"). Only clean, unambiguous mappings (audit 2026-06-22 / P2-b).
_ITEM_COVERAGE = {
    "Choice Scarf": {"speed_control"},
    "Leftovers": {"recovery"}, "Black Sludge": {"recovery"}, "Sitrus Berry": {"recovery"},
    # Eject Button is a real switch tool (the member leaves the field on being hit), so it belongs
    # with the pivot moves. Red Card forces the ATTACKER out, which clears its boosts off the field
    # exactly as phazing does — the hard setup answer anti_setup is about, carried by an item.
    "Eject Button": {"pivot"}, "Red Card": {"anti_setup"},
}
# Field-speed abilities (weather + terrain, from the shared cliffs maps) all count as speed
# control; Unaware/Imposter are HARD setup answers by ability (see anti_setup above).
_ABILITY_COVERAGE = {ab: {"speed_control"} for ab in _WEATHER_SPEED_ABILITIES}
_ABILITY_COVERAGE.update({"Unaware": {"anti_setup"}, "Imposter": {"anti_setup"}})
_ABILITY_COVERAGE.update({"Intimidate": {"damage_mitigation"}})
_ABILITY_COVERAGE.update({ab: {"weather_rewrite"}
                          for ab in ("Drizzle", "Drought", "Sand Stream", "Snow Warning")})
_ABILITY_COVERAGE.update({ab: {"terrain_control"}
                          for ab in ("Grassy Surge", "Psychic Surge", "Electric Surge",
                                     "Seed Sower")})
# Additional ability->role coverage (maintainer meta call 2026-07-16). Multi-role abilities
# carry a multi-element set; base+Mega union is handled by diagnose_roles via ability_tag_via.
_ABILITY_COVERAGE.update({
    "Speed Boost": {"speed_control"}, "Prankster": {"speed_control"},
    "Hospitality": {"recovery", "partner_support"}, "Regenerator": {"recovery"},
    "Shadow Tag": {"disruption"}, "Magic Bounce": {"disruption"},
    "Armor Tail": {"disruption", "side_protect"},
    "Queenly Majesty": {"disruption", "side_protect"},
    "Spicy Spray": {"status"},
    "Moxie": {"setup"}, "Moody": {"setup"}, "Contrary": {"setup"},
    "Telepathy": {"partner_support"},
    # Lightning Rod redirects single-target Electric moves away from allies (a doubles
    # ally-protection lane); Flash Fire is NOT here — it only grants the HOLDER Fire immunity
    # and self-boost, it neither guards nor draws attacks aimed at partners (audit 2026-07-16).
    "Lightning Rod": {"side_protect"},
    "Toxic Debris": {"hazard_set"},
    # Steely Spirit boosts the ALLY's Steel moves as well as its own — an ally-directed buff, the
    # same lane as Hospitality/Telepathy rather than a personal damage trait.
    "Steely Spirit": {"partner_support"},
})
# Canonical display order for the coverage checklist (required tier first, then optional,
# each in this order). Roles not in a format's tier map are NOT_CHECKED (omitted).
_ROLE_ORDER = ["speed_control", "priority_attack", "anti_setup", "protect", "fake_out",
               "spread", "damage_mitigation", "redirection", "hazard_set", "hazard_control",
               "pivot", "recovery", "screens", "disruption", "weather_rewrite",
               "terrain_control", "status", "setup", "partner_support", "side_protect"]
# Per-format THREE-LEVEL ATTENTION calibration (maintainer meta call, grounded in the M-B
# real-team library carry rates AND functional necessity). The tiers are ATTENTION levels for
# a MISSING role, NOT "required vs optional":
#   required  (LEVEL 1 需重点注意) -> a gap worth focused attention: flagged (not_detected),
#                                     the online AI reading MAY raise it.
#   optional  (LEVEL 2 有条件注意) -> conditional: shown as a neutral note, never a hard problem.
#   <omitted> (LEVEL 3 无需注意)   -> not applicable to this format, never listed.
# (wire values stay required/optional so consumers/protocol are unchanged; the levels are the
#  user-facing framing.)
_ROLE_TIERS = {
    "single": {
        # level 1 gained hazard_set (maintainer call): 57% carry, a real tempo/chip axis.
        "required": {"speed_control", "anti_setup", "priority_attack", "hazard_set"},
        "optional": {"pivot", "recovery", "screens", "disruption", "damage_mitigation",
                     "weather_rewrite", "terrain_control", "status", "setup"},
        # level 3 (omitted): hazard_control, protect (doubles mechanic), redirection,
        # fake_out, partner_support / side_protect, spread (a doubles-only AOE role).
    },
    "double": {
        # level 1 gained priority_attack / fake_out / weather_rewrite / spread (maintainer call).
        "required": {"speed_control", "protect", "damage_mitigation", "priority_attack",
                     "fake_out", "weather_rewrite", "spread"},
        "optional": {"redirection", "pivot", "recovery", "screens", "disruption",
                     "terrain_control", "status", "setup", "partner_support", "side_protect"},
        # level 3 (omitted): hazard_set / hazard_control (0% in doubles), anti_setup (~5%).
    },
}
_ROLE_CALIBRATION_RULE = "M-B"


def _role_calibration(effective_rule: str) -> dict[str, Any]:
    """Whether the role-attention study applies to `effective_rule`, and on what basis.

    The carry rates were measured on real M-B teams. A regulation opens with far too few real teams to
    re-measure anything, so for as long as the cross-rule handover window is open — the same seven-day
    clock every other predecessor-evidence view runs on — the predecessor's rates carry over and the
    status says exactly that, instead of claiming a review that has not happened. When the window
    closes the calibration reverts to unreviewed and every absence goes back to neutral.
    """
    if effective_rule == _ROLE_CALIBRATION_RULE:
        return {"rule": effective_rule, "status": "reviewed"}
    try:
        receipt = handover.load_active_receipt(repset.data_dir(), effective_rule)
    except Exception:                    # noqa: BLE001 — a broken receipt is not this view's call
        # The opponent-cache loader is the authority on a malformed receipt and fails loudly there.
        # Here the safe reading is simply "no carry-over": it costs a warning tier, not correctness.
        receipt = None
    if receipt and receipt.get("evidence_rule") == _ROLE_CALIBRATION_RULE:
        return {"rule": effective_rule, "status": "carried_over",
                "measured_on": _ROLE_CALIBRATION_RULE,
                "expires_at": receipt.get("expires_at")}
    return {"rule": effective_rule, "status": "unreviewed"}
# Reason text for LEVEL-2 absences (the AI reads it; the panel shows a generic note).
_OPTIONAL_REASONS = {
    "single": {
        "damage_mitigation": "carried by ~28% of real M-B singles teams",
        "disruption": "carried by ~36% of real M-B singles teams",
        "weather_rewrite": "~42% of real M-B singles teams carry a setter; weather also "
                           "expires on its own — an asset, not a hole",
        "screens": "carried by ~16% of real M-B singles teams",
    },
    "double": {
        "disruption": "carried by ~33% of real M-B doubles teams",
        "screens": "carried by ~30% of real M-B doubles teams",
    },
}


def _stat_orientation(stats: dict[str, Any]) -> dict[str, Any]:
    """Base-stat offensive lean + bulk tier. A signal, NOT a role label."""
    at = stats.get("atk") or 0
    sa = stats.get("spa") or 0
    if at and sa and at >= sa * 1.15:
        lean = "physical"
    elif at and sa and sa >= at * 1.15:
        lean = "special"
    elif at or sa:
        lean = "mixed"
    else:
        lean = "unknown"
    return {"offense_lean": lean, "atk": at, "spa": sa, "bulk": defensive_headroom(stats)}


def diagnose_roles(team: Team, facts: dict[str, dict[str, Any]], *,
                   rule: str | None = None) -> dict[str, Any]:
    """Objective functional signals per member + a neutral team coverage checklist + compression.

    Move signals come from move NAMES (no dex move lookup needed); stat orientation + bulk come from
    dex base stats; item/ability signals from the declared set. Members whose moveset is not
    authoritative still get stat/item/ability signals, but their move signals are flagged unknown so
    the coverage checklist does not report a phantom gap (mirrors offense's gaps_confirmed).
    """
    members: list[dict[str, Any]] = []
    skipped: list[str] = []
    incomplete_members: list[dict[str, str]] = []
    # A Mega-stone holder battles as its Mega form, whose sole ability is what actually fires
    # (team.py augments `facts` with those forms). Keyed by base species; used only when the
    # held item IS that form's Mega stone — Froslass+Froslassite -> Mega Froslass / Snow
    # Warning, a real snow setter the declared Cursed Body hides.
    mega_by_base = _mega_by_base(facts)
    field_conditions = team_field_conditions(team, facts)
    for m in team.pokemon:
        f = facts.get(m.species)
        if not f or not f.get("found"):
            skipped.append(m.species or "(blank)")
            continue
        moves_authoritative = completeness.moveset_authoritative(
            m.completeness, has_moves=bool(m.moves))
        tags: dict[str, list[str]] = {}
        if moves_authoritative:
            for mv in (m.moves or []):
                key = (mv or "").strip().lower()
                for tag, names in _ROLE_MOVES.items():
                    if key not in names:
                        continue
                    # A conditional priority move is only a priority finisher while the team can put
                    # up the field state it needs; without it, it is an ordinary attack.
                    if tag == "priority_attack" and key in _CONDITIONAL_PRIORITY:
                        if _CONDITIONAL_PRIORITY[key][0] not in field_conditions:
                            continue
                    tags.setdefault(tag, []).append(mv)
        else:
            incomplete_members.append({
                "species": m.species or "(blank)",
                "completeness": completeness.effective_level(m.completeness, has_moves=bool(m.moves)),
            })
        base_ability, mega_ability, ability_unknown = _ability_phases(m, facts, mega_by_base)
        # A Mega-stone holder plays its BASE ability first (turn 0 — Intimidate fires on
        # entry) and its MEGA ability after evolving: BOTH grant capabilities, so UNION their
        # coverage rather than replacing (Salamence keeps Intimidate mitigation AND gains
        # Aerilate; Froslass keeps nothing pre-Mega but gains Snow Warning / weather rewrite).
        item_signal = _ITEM_SIGNALS.get(m.item) if m.item else None
        base_ability_signal = _ABILITY_SIGNALS.get(base_ability) if base_ability else None
        mega_ability_signal = _ABILITY_SIGNALS.get(mega_ability) if mega_ability else None
        # Functional categories this member contributes to via its item / ability (not just moves),
        # so the coverage checklist agrees with the per-member signals shown (audit 2026-06-22 / P2-b).
        item_tags = _ITEM_COVERAGE.get(m.item, set()) if m.item else set()
        # tag -> the specific ability that grants it, so a bearer names Snow Warning (Mega) vs
        # the base ability. Base wins ties (it acts first in the battle).
        ability_tag_via: dict[str, str] = {}
        for ab in (base_ability, mega_ability):
            if ab:
                for tg in _ABILITY_COVERAGE.get(ab, set()):
                    ability_tag_via.setdefault(tg, ab)
        ability_tags = set(ability_tag_via)
        # Distinct functional signals this member carries (for compression counting).
        signal_tags = set(tags)
        if item_signal:
            signal_tags.add("item:" + (m.item or ""))
        for ab, sig in ((base_ability, base_ability_signal), (mega_ability, mega_ability_signal)):
            if sig:
                signal_tags.add("ability:" + ab)
        members.append({
            "species": m.species,
            "stat_orientation": _stat_orientation(f.get("stats") or {}),
            "move_signals": {t: tags[t] for t in tags},
            "moves_authoritative": moves_authoritative,
            "item": m.item, "item_signal": item_signal, "item_tags": sorted(item_tags),
            "ability": base_ability, "mega_ability": mega_ability,
            "ability_unknown": ability_unknown,
            "ability_signal": base_ability_signal, "mega_ability_signal": mega_ability_signal,
            "ability_tag_via": ability_tag_via, "ability_tags": sorted(ability_tags),
            "signal_count": len(signal_tags),
        })

    # Team functional coverage: present (with bearers) vs not-detected. Bearers come from moves AND
    # the item/ability contributors, so the checklist agrees with the per-member signals (a member
    # holding Leftovers now counts under recovery). Each bearer is tagged with its source. Neutral
    # framing — a not-detected category is an objective absence, NOT a prescription to add one.
    # Doubles also checklists the partner-support + side-protection categories (singles don't run them,
    # so they'd be phantom 'not detected' gaps there — §16.3 doubles aspect).
    fmt = (team.format or "").lower()
    effective_rule = (rule or team.rule or rules.current_rule()).upper()
    calibration = _role_calibration(effective_rule)
    calibrated = calibration["status"] in ("reviewed", "carried_over")
    base_tiers = _ROLE_TIERS.get(fmt, _ROLE_TIERS["single"])
    # The objective detectors remain useful after a regulation change, but an old carry-rate study may
    # not decide which absences deserve a warning. Until the target rule is reviewed, retain the same
    # format-applicable coverage surface and make every absence neutral.
    tiers = (base_tiers if calibrated else
             {"required": set(), "optional": base_tiers["required"] | base_tiers["optional"]})
    req, opt = tiers["required"], tiers["optional"]
    # required tier first, then optional — each in canonical order. NOT_CHECKED roles (in
    # neither set for this format) are simply never listed.
    checklist = ([t for t in _ROLE_ORDER if t in req]
                 + [t for t in _ROLE_ORDER if t in opt])
    reasons = _OPTIONAL_REASONS.get(fmt, {}) if calibrated else {}
    coverage: dict[str, dict[str, Any]] = {}
    for tag in checklist:
        bearers: list[dict[str, str]] = []
        for mm in members:
            srcs = []
            if tag in mm["move_signals"]:
                srcs.extend(f"move:{move}" for move in mm["move_signals"][tag])
            if tag in mm["item_tags"]:
                srcs.append(f"item:{mm['item']}")
            if tag in mm["ability_tags"]:
                srcs.append(f"ability:{mm['ability_tag_via'][tag]}")
            if srcs:
                bearers.append({"species": mm["species"], "via": ", ".join(srcs)})
        tier = "required" if tag in req else "optional"
        entry: dict[str, Any] = {"label": _ROLE_LABELS[tag], "present": bool(bearers),
                                 "bearers": bearers, "expectation": tier}
        if tier == "optional" and tag in reasons:
            entry["expectation_reason"] = reasons[tag]
        coverage[tag] = entry
    # not_detected = the REQUIRED absences (the real gaps). Optional absences are normal and
    # stay only in `coverage` (shown as a neutral note, never a flagged hole).
    not_detected = [coverage[t]["label"] for t in checklist
                    if not coverage[t]["present"] and coverage[t]["expectation"] == "required"]

    # Compression: members carrying multiple distinct functional signals (objective count).
    compression = sorted(
        [{"species": mm["species"], "signal_count": mm["signal_count"],
          "signals": (sorted(mm["move_signals"]) + ([f"item:{mm['item']}"] if mm["item_signal"] else [])
                      + [f"ability:{ab}" for ab, sig in
                         ((mm["ability"], mm["ability_signal"]),
                          (mm["mega_ability"], mm["mega_ability_signal"])) if sig])}
         for mm in members if mm["signal_count"] >= 2],
        key=lambda x: (-x["signal_count"], x["species"]))

    coverage_confirmed = not incomplete_members
    notes = [
        "These are OBJECTIVE signals (functional moves carried, base-stat orientation, item/ability "
        "signals), NOT role assignments — the model decides actual roles and what to change.",
        "A 'not detected' category is a neutral fact (this functional move/ability/item isn't on the "
        "team), NOT a recommendation to add it.",
        "Stat orientation is a base-stat lean, not a build: a physical-leaning species can still run a "
        "special set. Doubles roles also depend on the partner (a lone set can be ambiguous).",
    ]
    if incomplete_members:
        names = ", ".join(f"{m['species']} ({m['completeness']})" for m in incomplete_members)
        notes.append("Move signals NOT counted for members without an authoritative moveset (coverage "
                     "'not detected' may be incomplete): " + names + ".")
    if skipped:
        notes.append("Skipped (no dex facts): " + ", ".join(skipped) + ".")
    if not calibrated:
        notes.append(
            f"Role-attention calibration has not been reviewed for {effective_rule}; objective "
            "coverage is shown, but no absence is promoted to a required warning.")
    elif calibration["status"] == "carried_over":
        notes.append(
            f"Role-attention calibration was measured on real {calibration['measured_on']} teams and "
            f"carries over to {effective_rule} only while the handover window is open (until "
            f"{calibration['expires_at']}); after that every absence returns to neutral.")

    assumptions = [
        "role signals are heuristic (move-name / base-stat / item / ability derived), not role labels",
        "stat orientation is a base-stat lean, not the member's actual physical/special split",
    ]
    if not coverage_confirmed:
        assumptions.append("coverage gaps unconfirmed: members with non-authoritative movesets not counted")

    return {
        "kind": "roles",
        "team_size": len(members),
        "partial": len(members) < 6,
        "members": members,
        "coverage": coverage,
        "attention_calibration": calibration,
        "not_detected": not_detected,
        "compression": compression,
        "coverage_confirmed": coverage_confirmed,
        "incomplete_members": incomplete_members,
        "skipped": skipped,
        "confidence": "low",
        "confidence_reason": "heuristic-role",
        "assumptions": assumptions,
        "evidence": {
            "facts": [{"source": "dex", "ref": "base stats + abilities"}],
            "inputs": [{"source": "team-json", "ref": "moves + item + ability"}],
            "assumptions": assumptions,
            "table": "builtin functional move taxonomy + item/ability signal map",
        },
        "notes": notes,
    }


def _role_label(tag: str) -> str:
    key = f"role_label_{tag}"
    rendered = i18n.t(key)
    return _ROLE_LABELS.get(tag, tag) if rendered == key else rendered


def _role_via(raw: str) -> str:
    parts: list[str] = []
    for part in raw.split(", "):
        if part == "move":
            parts.append(i18n.t("role_via_move"))
        elif part.startswith("move:"):
            parts.append(i18n.t("role_via_move_name", name=part.split(":", 1)[1]))
        elif part.startswith("item:"):
            parts.append(i18n.t("role_via_item", name=part.split(":", 1)[1]))
        elif part.startswith("ability:"):
            parts.append(i18n.t("role_via_ability", name=part.split(":", 1)[1]))
        else:
            parts.append(part)
    return _join(parts)


def _compression_signal(raw: str) -> str:
    if raw.startswith("item:"):
        return i18n.t("role_via_item", name=raw.split(":", 1)[1])
    if raw.startswith("ability:"):
        return i18n.t("role_via_ability", name=raw.split(":", 1)[1])
    return _role_label(raw)


def format_roles_md(d: dict[str, Any]) -> str:
    lines = [_report_heading("role_title", d, reason=True),
             f"_{i18n.t('role_intro')}_"]
    if d.get("incomplete_members"):
        names = _join([f"{m['species']}{_paren(_completeness_label(m['completeness']))}"
                       for m in d["incomplete_members"]])
        lines.append(f"\n> ⚠️ {i18n.t('role_moves_uncounted_warn')}{_colon()}{names}{_period()}")
    lines.append(f"\n## {i18n.t('per_member')}")
    for mm in d["members"]:
        so = mm["stat_orientation"]
        sig = []
        for tag, mvs in mm["move_signals"].items():
            sig.append(f"{_role_label(tag)}{_paren(_join(mvs))}")
        if mm["item_signal"]:
            text = i18n.t(_ITEM_SIGNAL_I18N.get(mm["item"], "role_signal_unknown"))
            sig.append(f"{i18n.t('role_item')}{_colon()}{mm['item']} — {text if text != 'role_signal_unknown' else mm['item_signal']}")
        if mm["ability_signal"]:
            text = i18n.t(_ABILITY_SIGNAL_I18N.get(mm["ability"], "role_signal_unknown"))
            sig.append(f"{i18n.t('role_ability')}{_colon()}{mm['ability']} — {text if text != 'role_signal_unknown' else mm['ability_signal']}")
        if mm.get("ability_unknown"):
            sig.append(i18n.t('role_ability_unspecified'))
        sig_str = ("；" if i18n.lang() in ("zh", "ja") else "; ").join(sig) if sig else i18n.t('role_no_signals')
        lines.append(i18n.t("role_member_orientation", species=mm["species"],
                            lean=i18n.t(f"role_lean_{so['offense_lean']}"),
                            bulk=i18n.t(f"role_bulk_{so['bulk']}"), signals=sig_str))
    lines.append(f"\n## {i18n.t('role_coverage')}")
    # Iterate the coverage dict itself (insertion-ordered: singles checklist, then the doubles-only
    # partner_support / side_protect when present) so the doubles categories actually render here — not
    # the singles-only _CHECKLIST, which silently dropped them from the MD section (audit 2026-06-27).
    for tag in d["coverage"]:
        c = d["coverage"][tag]
        if c["present"]:
            bearers = _join([f"{b['species']}{_paren(_role_via(b['via']))}" for b in c["bearers"]])
            lines.append(f"- [x] {_role_label(tag)}{_colon()}{bearers}")
        elif c.get("expectation") == "optional":
            # a normal absence in this format — labelled so, never a flagged gap
            lines.append(f"- [ ] {_role_label(tag)}{_colon()}{i18n.t('role_not_detected')} "
                         f"{_paren(i18n.t('role_situational'))}")
        else:
            lines.append(f"- [ ] {_role_label(tag)}{_colon()}{i18n.t('role_not_detected')}")
    if d["compression"]:
        lines.append(f"\n## {i18n.t('role_compression')}")
        for c in d["compression"]:
            signals = _join([_compression_signal(s) for s in c["signals"]])
            lines.append(f"- **{c['species']}** {_paren(str(c['signal_count']))}{_colon()}{signals}")
    lines.append(f"\n## {i18n.t('notes')}")
    lines += [f"- {n}" for n in d["notes"]]
    return "\n".join(lines)
