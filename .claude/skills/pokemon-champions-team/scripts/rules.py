#!/usr/bin/env python
"""Champions registration rule constants, centralized.

These are the registration-period constants the validator (and tune's budget check) enforce.
Centralizing them here — instead of hardcoding in validate_team / tune — keeps those modules in
agreement. Season/source registration also belongs to the updater. Explicit unknown seasons or
rules fail closed: a previous regulation is never evidence that a new one is compatible.

`_RULES` ACCUMULATES; a rollover ADDS the new regulation and never replaces the old one. The
real-team library keeps its rows forever and stays collectible/queryable under their original
regulation (design §10). Explicit additive predecessors are validated against the current rule,
preserving original provenance; lack of other historical authority may still yield unknown. A
regulation whose registration contract was never reviewed (M-A) is deliberately ABSENT rather than
approximated: `is_reviewed` lets callers that only need honest provenance (historical backfill)
degrade to "cannot certify" instead of inventing caps a reviewer never signed off on.

This is a small, stable policy table, not battle data, so holding it here does not violate the
"no duplicate data" rule (same rationale as the type chart living in typechart.py).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuleSet:
    rule: str
    sp_per_stat_cap: int      # max SP on a single stat
    sp_total_cap: int         # max SP across all six stats
    team_min: int             # registered team size lower bound
    team_max: int             # registered team size upper bound
    species_clause: bool      # each base species at most once
    item_clause: bool         # each held item at most once
    moves_per_pokemon: int = 4  # max moves on a single member (and they must be distinct)


# Reviewed M-B ruleset. This is the single source for its per-stat SP cap: `cliffs.SP_CAP`
# derives from `get_ruleset().sp_per_stat_cap` rather than re-declaring 32 (audit 2026-06-21).
_M_B = RuleSet(rule="M-B", sp_per_stat_cap=32, sp_total_cap=66,
               team_min=3, team_max=6, species_clause=True, item_clause=True,
               moves_per_pokemon=4)

# Reviewed regulations only. M-A (seasons M-1/M-2) predates this table and its exact registration
# contract was never reviewed, so it stays out: its partitions remain readable and refreshable, but
# nothing may claim to have certified an M-A team's legality.
# M-C is an additive roster update: registration limits are unchanged. This does not claim that
# upstream data is available, and does not switch meta/current.json.
_M_C = RuleSet(rule="M-C", sp_per_stat_cap=32, sp_total_cap=66,
               team_min=3, team_max=6, species_clause=True, item_clause=True,
               moves_per_pokemon=4)
_RULES: dict[str, RuleSet] = {"M-B": _M_B, "M-C": _M_C}
ADDITIVE_PREDECESSORS = {"M-C": ("M-A", "M-B")}
_META_CURRENT = (Path(__file__).resolve().parents[2]
                 / "pokemon-champions-meta" / "data" / "current.json")


class UnsupportedRulesetError(ValueError):
    """The requested season/rule has no reviewed registration contract."""


def is_reviewed(rule: str | None) -> bool:
    """True when `rule` has a reviewed registration contract in this table.

    THE predicate for "may a legality verdict be issued for this regulation". A caller that must
    certify legality (validate, tune, any rollover target) requires True and otherwise fails closed;
    a caller that only records historical provenance uses this to report `unknown` instead of
    treating the missing contract as a validator crash.
    """
    return bool(rule) and str(rule).upper() in _RULES


def current_rule() -> str:
    try:
        data = json.loads(_META_CURRENT.read_text(encoding="utf-8"))
        rule = (data.get("current") or {}).get("rule")
    except Exception as exc:
        raise UnsupportedRulesetError(
            f"cannot read current regulation authority {_META_CURRENT}: {exc}") from exc
    if not isinstance(rule, str) or not rule:
        raise UnsupportedRulesetError(f"current regulation is missing in {_META_CURRENT}")
    return rule.upper()


def get_ruleset(season: str | None = None, rule: str | None = None) -> RuleSet:
    """Return a reviewed ruleset; reject unknown or contradictory explicit context.

    Calls without context resolve the sibling meta manifest, so module-level calculation caps follow
    the active reviewed regulation instead of remaining fixed to the previous one.
    """
    season_rule = rule_for_season(season)
    explicit_rule = (rule or "").upper() or None
    if season and season_rule is None:
        raise UnsupportedRulesetError(f"season {season!r} has no registered regulation")
    if season_rule and explicit_rule and season_rule != explicit_rule:
        raise UnsupportedRulesetError(
            f"season {season!r} belongs to {season_rule!r}, not {explicit_rule!r}")
    resolved = explicit_rule or season_rule or current_rule()
    if resolved not in _RULES:
        raise UnsupportedRulesetError(
            f"registration rules for {resolved!r} are not reviewed; refusing compatibility fallback")
    return _RULES[resolved]


# season -> its regulation. THE one-place edit for a new season: repset.load_teams_for_rule and the
# environment season->rule fallback both read this instead of re-declaring their own copy (audit 2026-07-08).
SEASON_RULE: dict[str, str] = {
    "M-1": "M-A",
    "M-2": "M-A",
    "M-3": "M-B",
    "M-4": "M-B",
    "M-5": "M-B",
    "M-6": "M-C",
    "M-7": "M-C",
    "M-8": "M-C",
}


def rule_for_season(season: str | None) -> str | None:
    """The regulation a season ran under from the static registration table, or None when unmapped."""
    return SEASON_RULE.get(season) if season else None


# The canonical smogon-style SP stat keys (contracts.STAT_KEYS aliases this). Scope note: this is
# the KEY SET only — the smogon->ncp key MAPPINGS (metalink/repset/tune/matchup *_TO_SPS) and the
# parse-order list (team_io.STATS) are separate, deliberately local tables.
SPREAD_STAT_KEYS = frozenset({"hp", "atk", "def", "spa", "spd", "spe"})


def legal_spread(spread: dict[str, Any] | None, rs: RuleSet | None = None) -> bool:
    """True when an SP spread respects the ruleset caps (each stat within the per-stat cap, total
    within the budget). THE shared predicate — consumers must call this rather than re-deriving
    32/66 locally, or the legality claim drifts from the rules source (audit 2026-07-02).

    Strict on SHAPE, not just values: every key must be a known stat and every value a real int
    (bools excluded) — an unknown key or a stringly value makes the WHOLE spread illegal, because a
    consumer (repset) would otherwise drop the unknown key from the emitted line yet still count the
    row as a legal sample. This predicate gates dirty source data, so it must never raise."""
    rs = rs or get_ruleset()
    total = 0
    for k, v in (spread or {}).items():
        if (k not in SPREAD_STAT_KEYS or isinstance(v, bool) or not isinstance(v, int)
                or not 0 <= v <= rs.sp_per_stat_cap):
            return False
        total += v
    return total <= rs.sp_total_cap
