#!/usr/bin/env python
"""Fallback frame table — where a frame comes from for an anchor that NO current-rule real team
carries (design §19.10, the fallback ladder).

The table is built once per data refresh (`dev/update/team/fallback.py`) and read by `frame`. It
stores a RECIPE per anchor, not a skeleton: which evidence pool the frame is computed over. `frame`
then materializes the skeletons with its ordinary machinery, so the table can never drift from the
current skeleton shape and every set it hands over is still grounded the ordinary way.

Three sources, tried in this order at build time:
  1. history   — older-rule partitions of the same format that DO carry the anchor. Teams built
                 around the anchor itself: the highest-fidelity structure available (rules only
                 ever add species, moves and items, so those teams stay valid as they are).
  2. partners  — the anchor's published usage partners, filtered to the ones that say something
                 about THIS anchor (not partners of everyone, or partners it pulls far above their
                 own usage rank), and only when real current teams carry those partners together.
                 The partner list selects the pool; the pool is real joint teams.
  3. stand_in  — a data-backed species doing the same job (explicit predicate hits: shared types,
                 attacking lean, speed band, field-setting ability, functional moves — counts, never
                 a similarity scalar). Its frames are lent to the anchor.

Boundaries (same as every library operator): no score, no winner; the table picks a POOL, never a
team; nothing here is a stored team handed to anyone.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable

import environment
import repset

SCHEMA_VERSION = 1
SOURCES = ("history", "partners", "stand_in")
# An older-rule pool this small is anecdote, not structure.
HISTORY_MIN_TEAMS = 3
# A partner listed with more than this share of all species is everyone's partner — no information.
PARTNER_SHARED_SHARE = 0.25
# ...unless the anchor pulls it this far above its own usage rank (overall rank / list position).
PARTNER_LIFT = 3.0
# Current teams that must carry the chosen partners together for them to define a pool.
PARTNER_MIN_TEAMS = 3
# A stand-in must itself be well attested, or it lends nothing.
STAND_IN_MIN_TEAMS = 10
# Functional capabilities that separate species (setup/recovery/protect are near-universal).
STAND_IN_ROLES = ("hazard_set", "hazard_control", "pivot", "screens", "redirection",
                  "weather_rewrite", "anti_setup", "priority_attack", "trick_room")
FIELD_ABILITIES = {"Drought", "Drizzle", "Sand Stream", "Snow Warning", "Electric Surge",
                   "Grassy Surge", "Psychic Surge", "Misty Surge", "Chlorophyll", "Swift Swim",
                   "Sand Rush", "Slush Rush", "Surge Surfer", "Solar Power", "Rain Dish"}


def table_path(fmt: str, rule: str) -> Path:
    return repset.data_dir().parent / "fallback_frames" / f"{rule}_{fmt}.json"


def anchor_key(species: str, item: str | None = None) -> str:
    """Table key for one frame filter member (species + the doubles-Mega stone isolation)."""
    return f"{species}|{item or ''}"


def load_table(fmt: str, rule: str) -> dict[str, Any] | None:
    path = table_path(fmt, rule)
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(table, dict) or table.get("schema_version") != SCHEMA_VERSION:
        return None
    built = table.get("built_for") or {}
    if built.get("format") != fmt or built.get("rule") != rule:
        return None
    return table


# -- selection (pure) --------------------------------------------------------------------------

def team_species(team: dict[str, Any]) -> set[str]:
    return {m.get("species") for m in team.get("pokemon") or [] if m.get("species")}


def specific_partners(partners: list[str], lists: dict[str, list[str]],
                      overall_rank: dict[str, int]) -> list[str]:
    """The anchor's partners that describe THIS anchor, in the published order. A partner on more
    than PARTNER_SHARED_SHARE of all species' lists is dropped unless the anchor lists it at least
    PARTNER_LIFT times above its own usage rank (a top-5 species listed first by an obscure one is
    still no news; a rank-60 species listed third is)."""
    n = max(1, len(lists))
    listed_by = Counter(p for plist in lists.values() for p in set(plist))
    unranked = len(overall_rank) + 1
    out = []
    for position, p in enumerate(partners, 1):
        shared = listed_by.get(p, 0) / n > PARTNER_SHARED_SHARE
        lifted = overall_rank.get(p, unranked) / position >= PARTNER_LIFT
        if (not shared or lifted) and p not in out:
            out.append(p)
    return out


def _teams_by_species(team_sets: list[set[str]]) -> dict[str, set[int]]:
    index: dict[str, set[int]] = {}
    for coordinate, species in enumerate(team_sets):
        for name in species:
            index.setdefault(name, set()).add(coordinate)
    return index


def partner_core(partners: list[str], team_sets: list[set[str]]) -> tuple[list[str], int]:
    return _partner_core(partners, _teams_by_species(team_sets))


def _partner_core(partners: list[str], team_index: dict[str, set[int]]) -> tuple[list[str], int]:
    """The largest group of the specific partners (three, else two) that real current teams carry
    together at least PARTNER_MIN_TEAMS times; ties go to the earlier-listed group. ([], 0) if none."""
    best: tuple[list[str], int] = ([], 0)
    for size in (3, 2):
        for i, a in enumerate(partners):
            for j in range(i + 1, len(partners)):
                rest = [partners[k] for k in range(j + 1, len(partners))] if size == 3 else [None]
                for c in rest:
                    group = [a, partners[j]] + ([c] if c else [])
                    pool = [team_index.get(name, set()) for name in group]
                    n = len(set.intersection(*pool))
                    if n >= PARTNER_MIN_TEAMS and n > best[1]:
                        best = (group, n)
        if best[0]:
            return best
    return best


def capabilities(fact: dict[str, Any], role_moves: dict[str, set[str]]) -> dict[str, Any]:
    """The explicit predicates a stand-in is matched on, from dex facts only."""
    stats = fact.get("stats") or {}
    moves = {str(m).lower() for m in fact.get("moves") or []}
    roles = {r for r in STAND_IN_ROLES if r != "trick_room" and moves & role_moves.get(r, set())}
    if "trick room" in moves:
        roles.add("trick_room")
    atk, spa, spe = stats.get("atk", 0), stats.get("spa", 0), stats.get("spe", 0)
    return {
        "types": set(fact.get("types") or []),
        "lean": "physical" if atk >= spa + 10 else "special" if spa >= atk + 10 else "mixed",
        "speed_band": "slow" if spe < 60 else "mid" if spe < 90 else "fast" if spe < 110 else "very_fast",
        "roles": roles,
        "field": {str(a) for a in fact.get("abilities") or []} & FIELD_ABILITIES,
    }


def predicate_hits(a: dict[str, Any], b: dict[str, Any]) -> dict[str, int]:
    return {"types": len(a["types"] & b["types"]), "lean": int(a["lean"] == b["lean"]),
            "speed_band": int(a["speed_band"] == b["speed_band"]),
            "roles": len(a["roles"] & b["roles"]), "field": len(a["field"] & b["field"])}


def stand_in(anchor_caps: dict[str, Any], candidates: dict[str, dict[str, Any]],
             team_counts: dict[str, int]) -> tuple[str | None, dict[str, int]]:
    """The attested species sharing the most predicates with the anchor. Ordered by the hit counts
    themselves (types and field abilities first — they decide what a slot IS — then attacking lean,
    speed band and functional moves), then by how attested the candidate is. A candidate sharing
    neither a type nor a field ability is not doing the same job and is never picked."""
    best, best_key, best_hits = None, None, {}
    for name, caps in candidates.items():
        if team_counts.get(name, 0) < STAND_IN_MIN_TEAMS:
            continue
        hits = predicate_hits(anchor_caps, caps)
        if not (hits["types"] or hits["field"]):
            continue
        key = (hits["types"] + hits["field"], hits["lean"] + hits["speed_band"], hits["roles"],
               team_counts.get(name, 0), name)
        if best_key is None or key > best_key:
            best, best_key, best_hits = name, key, hits
    return best, best_hits


def _presence_counts(teams: list[dict[str, Any]]) -> Counter:
    counts = Counter()
    for team in teams:
        present = set()
        for member in team.get("pokemon", []):
            name = member.get("species")
            item = member.get("item")
            present.add((name, None))
            if item:
                present.add((name, item))
        counts.update(present)
    return counts


def build_table(anchors: Iterable[tuple[str, str | None]], *, fmt: str, rule: str, season: str | None,
                current_teams: list[dict[str, Any]], history_teams: dict[str, list[dict[str, Any]]],
                partners_of: Callable[[str], list[str]], partner_lists: dict[str, list[str]],
                overall_rank: dict[str, int],
                anchor_caps: Callable[[str, str | None], dict[str, Any] | None],
                candidate_caps: dict[str, dict[str, Any]],
                matches: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None) -> dict[str, Any]:
    """One format's table. `anchors` = (species, item-filter) frame filter members; only anchors with
    no current-rule team get a recipe. `history_teams` = {season: older-rule teams}."""
    team_index = _teams_by_species([team_species(t) for t in current_teams])
    team_counts = {name: len(teams) for name, teams in team_index.items()}
    if matches is None:
        current_presence = _presence_counts(current_teams)
        history_presence = {part: _presence_counts(rows) for part, rows in history_teams.items()}
    recipes: dict[str, Any] = {}
    sources: Counter = Counter()
    for species, item in anchors:
        if matches is None:
            key = (species, item or None)
            if current_presence[key]:
                continue
            seasons = {part: counts[key] for part, counts in history_presence.items() if counts[key]}
        else:
            f = {"species": species, "item": item}
            if any(matches(t, f) for t in current_teams):
                continue
            seasons = {part: sum(1 for t in rows if matches(t, f)) for part, rows in history_teams.items()}
            seasons = {part: count for part, count in seasons.items() if count}
        recipe = None
        if sum(seasons.values()) >= HISTORY_MIN_TEAMS:
            recipe = {"source": "history", "seasons": sorted(seasons), "teams": sum(seasons.values())}
        if recipe is None:
            specific = specific_partners(partners_of(species), partner_lists, overall_rank)
            group, n = _partner_core(specific, team_index)
            if group:
                recipe = {"source": "partners", "partners": group, "teams": n}
        if recipe is None:
            caps = anchor_caps(species, item)
            if caps:
                name, hits = stand_in(caps, candidate_caps, team_counts)
                if name:
                    recipe = {"source": "stand_in", "species": name, "hits": hits,
                              "teams": team_counts[name]}
        if recipe:
            recipes[anchor_key(species, item)] = recipe
            sources[recipe["source"]] += 1
        else:
            sources["none"] += 1
    return {
        "kind": "fallback_frames",
        "schema_version": SCHEMA_VERSION,
        "built_for": {"format": fmt, "rule": rule, "season": season},
        "counts": dict(sorted(sources.items())),
        "anchors": dict(sorted(recipes.items())),
    }


# -- read side: recipe -> the pool `frame` materializes -------------------------------------------

def resolve(recipe: dict[str, Any], *, fmt: str, rule: str, current_teams: list[dict[str, Any]],
            load_season: Callable[[str], list[dict[str, Any]]]) -> dict[str, Any] | None:
    """Turn one recipe into the evidence pool `frame` needs. None when the pool has since emptied
    (the table is rebuilt every refresh, but a caller must not trust it blindly)."""
    source = recipe.get("source")
    if source == "history":
        pool = [t for s in recipe.get("seasons") or [] for t in load_season(s)
                if (t.get("rule") or environment.rule_for_season(s)) != rule]
        return {"source": source, "pool": pool} if pool else None
    if source == "partners":
        group = [p for p in recipe.get("partners") or [] if p]
        pool = [t for t in current_teams if all(p in team_species(t) for p in group)]
        return {"source": source, "pool": pool, "partners": group} if len(group) >= 2 and pool else None
    if source == "stand_in":
        name = recipe.get("species")
        pool = [t for t in current_teams if name in team_species(t)]
        return {"source": source, "pool": pool, "stand_in": name} if pool else None
    return None
