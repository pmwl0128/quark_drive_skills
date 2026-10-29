"""CLI views of the team skill's type chart and registration rules."""
from __future__ import annotations

import re
from typing import Any, Callable

import environment
import rules
import selection
import typechart

_TYPE_SUFFIX = re.compile(r"(属性|系|タイプ|\s*type)$", re.IGNORECASE)
_LIMITATIONS = ["attacker_abilities", "move_exceptions", "resist_berries", "weather",
                "move_contact_effects"]


class FactInputError(ValueError):
    def __init__(self, query: str, code: str, message: str, **details: Any):
        super().__init__(message)
        self.result = {"ok": False, "query": query,
                       "error": {"code": code, "message": message, **details}}


def _resolve(raw: str, kind: str, resolve_fn: Callable,
             corrections: list[dict[str, Any]]) -> str:
    text = _TYPE_SUFFIX.sub("", raw.strip()).strip() if kind == "type" else raw.strip()
    if not text:
        raise FactInputError(raw, "bad_input", f"{kind} name must not be empty")
    row = (resolve_fn([text], kind) or {}).get(text) or {}
    if not row.get("ok") or not row.get("canonical"):
        error = row.get("error") or {}
        raise FactInputError(raw, error.get("code", "not_found"),
                             error.get("message") or f"unknown {kind}: {raw}",
                             **({"suggestions": row["suggestions"]} if row.get("suggestions") else {}))
    canonical = row["canonical"]
    if kind == "type" and canonical not in typechart.TYPES:
        raise FactInputError(raw, "authority_mismatch", "dex type is absent from the type chart")
    if row.get("match_type") == "fuzzy":
        corrections.append({"query": raw, "canonical": canonical, "kind": kind,
                            "match_type": "fuzzy", "score": row.get("score"),
                            "distance": row.get("distance")})
    return canonical


def _grouped(table: dict[str, float]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for attack, multiplier in sorted(table.items(), key=lambda row: (-row[1], row[0])):
        if multiplier != 1:
            grouped.setdefault(f"{multiplier:g}x", []).append(attack)
    return grouped


def type_matchup(*, attack_type: str | None = None, pokemon: str | None = None,
                 types: list[str] | None = None, ability: str | None = None,
                 resolve_fn: Callable, pokemon_fn: Callable) -> dict[str, Any]:
    for label, value in (("attack_type", attack_type), ("pokemon", pokemon), ("ability", ability)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise FactInputError(label, "bad_input", f"{label} must be a nonempty name")
    if pokemon and types is not None:
        raise FactInputError(pokemon, "bad_input", "use pokemon or types, not both")
    if types is not None and (not isinstance(types, list) or not 1 <= len(types) <= 2
                              or any(not isinstance(t, str) for t in types)):
        raise FactInputError("types", "bad_input", "types must contain one or two names")
    if not attack_type and not pokemon and types is None:
        raise FactInputError("type-matchup", "bad_input", "give attack_type, pokemon or types")
    if ability and not (pokemon or types is not None):
        raise FactInputError(ability, "bad_input", "a defending ability needs pokemon or types")
    corrections: list[dict[str, Any]] = []
    attack = _resolve(attack_type, "type", resolve_fn, corrections) if attack_type else None
    defender: str | None = None
    natural_abilities: list[str] = []
    if pokemon:
        defender = _resolve(pokemon, "pokemon", resolve_fn, corrections)
        fact = (pokemon_fn([defender]) or {}).get(defender) or {}
        if not fact.get("found"):
            raise FactInputError(pokemon, "not_found", "Pokemon has no battle-dex facts")
        defender_types = list(fact.get("types") or [])
        natural_abilities = list(fact.get("abilities") or [])
        if not 1 <= len(defender_types) <= 2 or any(t not in typechart.TYPES for t in defender_types):
            raise FactInputError(pokemon, "authority_mismatch", "Pokemon types are absent from the type chart")
    else:
        defender_types = [_resolve(t, "type", resolve_fn, corrections) for t in (types or [])]
        if len(defender_types) != len(set(defender_types)):
            raise FactInputError("/".join(types or []), "bad_input", "duplicate defending types")
        if defender_types:
            defender = "/".join(defender_types)
    applied = _resolve(ability, "ability", resolve_fn, corrections) if ability else None
    if applied is None and len(natural_abilities) == 1:
        applied = natural_abilities[0]
    out: dict[str, Any] = {"kind": "type-matchup", "defender": defender,
                           "types": defender_types, "attack_type": attack,
                           "basis": "fixed_type_chart_with_defender_ability",
                           "excluded": _LIMITATIONS}
    if not defender_types:
        out.update({"single_type_defenders": True,
                    "super_effective_on": [t for t in typechart.TYPES if typechart.effectiveness(attack, [t]) > 1],
                    "resisted_by": [t for t in typechart.TYPES if 0 < typechart.effectiveness(attack, [t]) < 1],
                    "no_effect_on": [t for t in typechart.TYPES if typechart.effectiveness(attack, [t]) == 0]})
    else:
        plain = {t: typechart.effectiveness(t, defender_types) for t in typechart.TYPES}
        table = ({t: typechart.effectiveness_for_member(defender_types, applied, t)
                  for t in typechart.TYPES} if applied else plain)
        if natural_abilities:
            out["natural_abilities"] = natural_abilities
        if applied:
            out["applied_ability"] = applied
            if ability and natural_abilities and applied not in natural_abilities:
                out["ability_assumption"] = "hypothetical_for_this_pokemon"
        elif natural_abilities:
            effects = []
            for candidate in natural_abilities:
                changed = {t: typechart.effectiveness_for_member(defender_types, candidate, t)
                           for t in typechart.TYPES
                           if (not attack or t == attack)
                           and typechart.effectiveness_for_member(defender_types, candidate, t) != plain[t]}
                if changed:
                    effects.append({"ability": candidate, "changes": changed})
            if effects:
                out["ability_effects"] = effects
            if len(natural_abilities) > 1:
                out["ability_unspecified"] = True
        if attack:
            conditional = any(attack in row["changes"] for row in out.get("ability_effects", []))
            out.update({"base_multiplier": plain[attack],
                        "multiplier": None if conditional else table[attack]})
        else:
            out.update({"table": _grouped(table), "neutral_omitted": True,
                        "table_basis": "type_only" if out.get("ability_unspecified") else "applied_ability_or_type_only"})
    if corrections:
        out["name_resolution"] = corrections
    return out


def rules_facts() -> dict[str, Any]:
    season, rule = environment.CURRENT_SEASON, environment.CURRENT_RULE
    rs = rules.get_ruleset(season, rule)
    return {"kind": "rules-facts", "season": season, "rule": rule,
            "team_size": {"min": rs.team_min, "max": rs.team_max},
            "pick_per_battle": dict(selection.PICK_SIZE),
            "sp_per_stat_max": rs.sp_per_stat_cap, "sp_total_max": rs.sp_total_cap,
            "species_clause": rs.species_clause, "item_clause": rs.item_clause,
            "moves_per_pokemon": rs.moves_per_pokemon,
            "mega_evolutions_per_battle": selection.MAX_ACTIVE_MEGAS,
            "multiple_registered_mega_stones_allowed": True,
            "seasons": [{"season": s, "rule": r, "rules_reviewed": rules.is_reviewed(r)}
                        for s, r in rules.SEASON_RULE.items()],
            "source": "reviewed_team_rules_and_selection"}
