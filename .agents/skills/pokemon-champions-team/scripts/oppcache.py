#!/usr/bin/env python
"""Opponent observed-build matchup CACHE (M5 step 2).

A precomputed grid of how retained observed builds of the meta top-K interact — attacker i's hardest
move vs defender j's concrete build, plus their actual speed line, for every ordered pair. It is a
fast REFERENCE, NOT the user's own matchup: a real team is always matched
LIVE via matchup.py against its ACTUAL sets. So the whole cache is stamped `low` confidence
(reason=`vs-observed-build`) — every cell is a retained-build fact, never the user's real board.

This module holds NO battle data. It (a) READS the shipped cache JSON and (b) provides the PURE
`build_matrix` the dev builder (`dev/update/team/cache.py`) calls with injected sibling functions —
the fact-shaping (KO buckets, speed line) lives here so it ships and is unit-tested in the skill,
while the dev side only fetches + writes.

Design discipline (mirrors matchup.py / repset.py):
- FACTS ONLY. No matchup score, no ranking of species, no "best" anything.
- ATTACKER ROWS ONLY FOR REAL-TEAM-BACKED SPECIES: a meta-only species has no
  REAL co-occurring 4-move set (meta gives independent marginals — stitching them is the forbidden
  marginal-stitch trap), so it cannot be an attacker. It CAN be a defender (being hit needs only its bulk:
  ability/item/nature/spread, which the meta modal supplies). This is the literal "build a cell only
  for species clearing MIN_SAMPLE" — the MIN_SAMPLE gate sits in repset and decides who is an attacker.
- Single/double NEVER mixed (one cache file per format, keyed by RULE — the opponent universe is
  the current usage ranking and the sets come from the whole rule pool, so nothing in the content
  is per-season).
- Time-validity follows the environment: source-content fingerprints bind the matrix to its meta and
  same-rule team snapshots. A mismatch fails loudly and requires an offline rebuild.
"""
from __future__ import annotations

import hashlib
import json
import os
from math import ceil
from pathlib import Path
from typing import Any, Callable

import checks
import handover
import repset
import team_i18n as i18n

# Disguise (Mimikyu) breaks on the first damaging hit: it blocks that hit ENTIRELY (0 dmg) and Mimikyu
# loses 1/8 max HP, dropping to this fraction (Busted form, rest of battle). The effective KO is then
# 1 blocked turn + the turns to remove this remaining HP at the move's per-turn band.
_DISGUISE_REMAINING_PCT = 87.5

SCRIPTS = Path(__file__).resolve().parent
DEFAULT_CACHE = SCRIPTS.parent / "data" / "opponent_cache"   # ships WITH a snapshot (tracked)

CONFIDENCE = "low"                 # every cell is retained-build vs retained-build, not a real board
CONFIDENCE_REASON = "vs-observed-build"

# Worst-first ordering for per-variant readings. C0 (no answer) sorts ahead of C1/C2 so the build a
# reader must actually prepare for is the first entry.
_GRADE_ORDER = {"C0": 0, "C1": 1, "C2": 2}
CACHE_SCHEMA_VERSION = 2

# The module that turns a check/matchup/tune question into a calc request. Hashed into the cache's
# calculation authority, relative to the skills root so producer and verifier name it identically.
CALLER_REL = "pokemon-champions-team/scripts/ncplink.py"


class StaleCacheError(RuntimeError):
    """The matrix no longer matches the local meta/team source snapshots."""


def _fingerprint(paths: list[Path]) -> str:
    """Portable content hash; Git checkout line-ending policy must not invalidate a cache."""
    h = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.name):
        h.update(path.name.encode("utf-8"))
        h.update(b"\0")
        if path.exists():
            h.update(path.read_bytes().replace(b"\r\n", b"\n"))
        else:
            h.update(b"<missing>")
        h.update(b"\0")
    return h.hexdigest()


def team_seasons_for_rule(fmt: str, rule: str) -> list[str]:
    """Non-empty shipped partitions that currently contribute to a rule-scoped team pool.

    Prefer the shipped index so a newly created partition invalidates the cache before a consumer has
    to load the full library. Fall back to the rows for custom/test data directories without an index.
    """
    index_path = repset.data_dir() / "index.json"
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(index, dict):
                seasons = {
                    str(entry.get("season"))
                    for entry in index.values()
                    if isinstance(entry, dict)
                    and entry.get("format") == fmt
                    and entry.get("rule") == rule
                    and int(entry.get("count") or 0) > 0
                    and entry.get("season")
                }
                seasons.update(p for p in handover.source_partitions(repset.data_dir(), fmt, rule)
                               if not p.endswith("-events"))
                return sorted(seasons)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return repset.seasons_in(repset.load_teams_for_rule(fmt, rule))


def team_partitions_for_rule(fmt: str, rule: str) -> list[str]:
    """Non-empty physical partitions contributing to a rule pool, including rule-level events."""
    index_path = repset.data_dir() / "index.json"
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(index, dict):
                partitions = {
                    str(entry.get("partition") or entry.get("season"))
                    for entry in index.values()
                    if isinstance(entry, dict)
                    and entry.get("format") == fmt
                    and entry.get("rule") == rule
                    and int(entry.get("count") or 0) > 0
                    and (entry.get("partition") or entry.get("season"))
                }
                partitions.update(handover.source_partitions(repset.data_dir(), fmt, rule))
                return sorted(partitions)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    partitions = set()
    for path in repset.data_dir().glob(f"*_{fmt}.jsonl"):
        rows = repset._read_jsonl(path)
        if any(team.get("rule") == rule for team in rows):
            partitions.add(path.stem.rsplit("_", 1)[0])
    partitions.update(handover.source_partitions(repset.data_dir(), fmt, rule))
    return sorted(partitions)


def calculation_authority() -> dict:
    """Bind derived team calculations to the current dex, installed JS engine, and call convention.

    The engine hashes alone are not enough: what a cached cell MEANS also depends on how this skill
    ASKS for it. Turning the calc's switch-in Attack drops off keeps every frame history-free and
    moves every physical number without touching one engine byte, so the calc boundary module is
    hashed alongside the engine. Any edit there invalidates the cache, which is the safe direction:
    that file is small, rarely touched, and is the only place the request convention lives.
    """
    root = SCRIPTS.parent.parent
    ncp = root / "ncp-damage-calculator/scripts"
    engine_files = [ncp / "ncp-calc-api.js", ncp / "ncp-speedline-api.js",
                    *(ncp / "script_res").rglob("*.js")]
    return {"dex": handover.binary_sha256(handover.dex_path(repset.data_dir())),
            "ncp": {p.relative_to(root).as_posix(): handover.file_sha256(p)
                    for p in sorted(engine_files)},
            "caller": {CALLER_REL: handover.file_sha256(root / CALLER_REL)}}


def source_fingerprints(fmt: str, rule: str, season: str,
                        data_seasons: list[str] | None = None,
                        data_partitions: list[str] | None = None) -> dict[str, str]:
    """Hashes of every mutable data source that shapes this cache.

    Meta ranking and detail panels are season-scoped; real-team evidence is rule-scoped and therefore
    includes every non-empty shipped partition currently contributing to that rule. The hashes make a
    data refresh invalidate the old matrix loudly instead of letting a newer local snapshot silently
    consume stale calculations.
    """
    skills_root = SCRIPTS.parent.parent
    meta_data = skills_root / "pokemon-champions-meta" / "data"
    meta_paths = [meta_data / f"ranking_{season}_{fmt}.json",
                  meta_data / f"details_{season}_{fmt}.json"]
    contributing = sorted(set(data_partitions or team_partitions_for_rule(fmt, rule)))
    team_paths = [repset.data_dir() / f"{partition}_{fmt}.jsonl" for partition in contributing]
    if handover.load_active_receipt(repset.data_dir(), rule):
        team_paths.append(handover.receipt_path(repset.data_dir()))
    return {"meta": _fingerprint(meta_paths), "team_library": _fingerprint(team_paths)}


# --- read side ---------------------------------------------------------------

def cache_dir() -> Path:
    """The opponent-cache dir (CHAMP_OPPCACHE overrides, for the dev builder + tests)."""
    return Path(os.environ.get("CHAMP_OPPCACHE", DEFAULT_CACHE))


def cache_path(fmt: str, rule: str) -> Path:
    return cache_dir() / f"{rule}_{fmt}.json"


def load_cache(fmt: str, rule: str, *, native: bool = False) -> dict[str, Any] | None:
    """The cached matrix for a (rule, format), or None when it has not been built/shipped.

    Keyed by RULE, not season: neither half of the content is per-season. The opponent universe is
    the CURRENT usage ranking (whatever the meta ranks right now), and the sets come from the whole
    rule pool (`cached_teams_for_rule`). Season-keyed files implied a per-season matrix that was
    never built — the retention policy already expires these by rule, and rebuilding a non-current
    season only re-stamped the current ranking under an older name."""
    if native and not handover._NATIVE_ONLY.get():
        with handover.native_only():
            return load_cache(fmt, rule, native=True)
    p = cache_path(fmt, rule)
    if native:
        p = p.with_name(f"{rule}_{fmt}.native.json")
    if not p.exists():
        return None
    cache = json.loads(p.read_text(encoding="utf-8"))
    built_for = cache.get("built_for") or {}
    if built_for.get("calculation_authority") is not None and built_for["calculation_authority"] != calculation_authority():
        raise StaleCacheError("opponent cache uses an older dex/calculator; rebuild it")
    if native and built_for.get("handover_receipt"):
        raise StaleCacheError("native team cache cannot contain handover evidence")
    if not native and built_for.get("handover_receipt") and not handover.load_active_receipt(
            repset.data_dir(), rule):
        return load_cache(fmt, rule, native=True)
    expected = built_for.get("source_fingerprints")
    season = built_for.get("season")
    if isinstance(expected, dict) and isinstance(season, str):
        recorded_seasons = sorted(set(built_for.get("data_seasons") or []))
        actual_seasons = team_seasons_for_rule(fmt, rule)
        recorded_partitions = sorted(set(built_for.get("data_partitions") or recorded_seasons))
        actual_partitions = team_partitions_for_rule(fmt, rule)
        if recorded_seasons != actual_seasons or recorded_partitions != actual_partitions:
            raise StaleCacheError(
                f"opponent cache {rule}/{fmt} is stale for the local team partitions; rebuild it")
        actual = source_fingerprints(fmt, rule, season, actual_seasons,
                                     data_partitions=actual_partitions)
        if expected != actual:
            raise StaleCacheError(
                f"opponent cache {rule}/{fmt} is stale for the local meta/team snapshots; rebuild it")
    if cache.get("unavailable"):
        return None
    if built_for.get("cache_schema_version") not in (None, CACHE_SCHEMA_VERSION):
        raise StaleCacheError(
            f"opponent cache {rule}/{fmt} uses unsupported schema "
            f"{built_for.get('cache_schema_version')!r}; rebuild it")
    return cache


def attacker_row(cache: dict[str, Any], attacker: str) -> dict[str, Any] | None:
    """The attacker's row (its concrete set + every defender cell), or None if it has no row (a
    meta-only species is never an attacker — see module docstring)."""
    cell_row = (cache.get("matrix") or {}).get(attacker)
    if cell_row is None:
        return None
    return {"attacker": attacker, "set": (cache.get("sets") or {}).get(attacker),
            "cells": cell_row}


def cell(cache: dict[str, Any], attacker: str, defender: str) -> dict[str, Any] | None:
    """One ordered (attacker -> defender) cell, or None when the pair is not in the matrix."""
    return ((cache.get("matrix") or {}).get(attacker) or {}).get(defender)


# --- pure builder (shared by the dev builder; injectable for tests) ----------

def _ncp(species: str, s: dict[str, Any]) -> dict[str, Any]:
    """An ncp pokemon dict from a resolved build. The ncp NAME (stats/types basis) is the form
    actually RUN: for a singles Mega the matrix key is the meta base name ('Staraptor') but `run_form`
    is 'Mega Staraptor', so the calc must use the Mega's stats (audit 2026-06-25)."""
    return {"name": s.get("run_form") or s.get("species") or species,
            "ability": s.get("ability"), "item": s.get("item"),
            "nature": s.get("nature"), "sps": s.get("sps") or {}}


def _disguise_adjust(off: dict[str, Any]) -> dict[str, Any]:
    """Effective KO vs a DISGUISED Mimikyu. Disguise BLOCKS the first damaging move entirely (0 dmg) and
    breaks, costing Mimikyu 1/8 max HP (→ ~87.5% HP, Busted form). So a naive "+1 hit" is WRONG: a hit
    big enough that the 1/8 chip + one real hit already KOs needs FEWER turns than nominal+1 (e.g. a 90%
    move is a real 2-turn KO, not 3), and a weak move can land on the nominal count. The honest model is
    `1 blocked turn + ceil(remaining 87.5% / per-turn damage band)`.

    NARROW labelling exception keyed on ability=Disguise (Mimikyu's whole value), computed from the
    nominal band at the cache layer — NOT a general recompute of one-time-survive effects into the calc,
    which the engine deliberately rejects (Sash/Sturdy/... would all follow → re-simulating the engine).
    Assumes Mimikyu starts disguised (true at the start of an engagement — the standard-set cell case).
    The raw `ko`/`ko_chance` ignore Disguise and OVERSTATE; read this vs Mimikyu instead."""
    def turns(p: Any) -> int | None:                 # 1 blocked turn + turns to remove the remaining HP
        return (1 + ceil(_DISGUISE_REMAINING_PCT / p)) if (p and p > 0) else None
    return {
        "effective_ko_possible": turns(off.get("max_percent")),      # best roll (fewest turns)
        "effective_ko_guaranteed": turns(off.get("min_percent")),    # worst roll (most turns)
        "note": "Disguise BLOCKS the first damaging move entirely (0 dmg) and breaks — Mimikyu loses "
                "1/8 max HP (→ ~87.5% HP, Busted form rest of battle). Effective KO = 1 blocked turn + "
                "turns to remove the remaining 87.5% at the band above. NOT nominal+1 (a hard hit + the "
                "1/8 chip can KO sooner). The raw ko/ko_chance ignore Disguise and overstate.",
    }


def build_matrix(sets: dict[str, dict[str, Any]], dex_facts: dict[str, dict[str, Any]],
                 attackers: set[str] | list[str], rows: list[dict[str, Any]], *,
                 fmt: str, season: str, rule: str, built_at: str,
                 damage_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
                 move_facts: dict[str, dict[str, Any]] | None = None,
                 dmg_fact_fn: Callable[..., dict[str, Any]] | None = None,
                 speed_fn: Callable[..., int | None] | None = None) -> dict[str, Any]:
    """Build the observed-build matchup matrix. PURE given its injected sibling functions.

    `sets`     = {build key: resolved set} (ability/item/
                 nature/sps for every defender, plus a real `moves` list for real-team-backed species).
    `dex_facts`= {species: {stats:{spe}, types}} for the speed line.
    `attackers`= the species that get an OFFENSE row — exactly the real-team-backed ones (repset
                 returned a set, i.e. sample >= MIN_SAMPLE). Meta-only species appear as defenders only.
    `rows`     = the meta usage ranking rows ({rank, species}), in order — fixes the species order and
                 carries the usage rank as a provenance fact.
    Cells carry the attacker's hardest move (by max roll) vs the defender + the actual speed line. Every
    cell is `low` confidence (reason=`vs-observed-build`); the flag sits once at the top level."""
    from matchup import dmg_fact as _default_dmg_fact, best_offense as _best_offense
    from cliffs import effective_speed as _default_speed
    dmg_fact_fn = dmg_fact_fn or _default_dmg_fact
    speed_fn = speed_fn or _default_speed
    attackers = set(attackers)
    # Accuracy-based one-hit-KO moves do not belong in a deterministic static damage/KO baseline.
    # The dex mechanical flag is authoritative; never guess from power=1 or accuracy=30.
    ohko_moves = {name for name, fact in (move_facts or {}).items()
                  if (fact or {}).get("is_ohko") is True}

    # The matrix key is the VARIANT id when rows carry one, else the species name (the meta-only /
    # legacy shape). Everything below indexes by this key, so one species can hold several rows and
    # columns without any other logic changing.
    def _key(r: dict[str, Any]) -> str:
        return r.get("variant_id") or r.get("species") or r.get("pokemon_en") or ""

    ordered = [r for r in rows if _key(r) in sets]
    # Stable-key row order: `rows` arrives rank-ordered, but the cache is rewritten on every refresh
    # and a rank shuffle would relocate whole species/sets/matrix blocks in the file, exploding its
    # git diff. Sort by the key instead — rank stays as a per-row FACT in `species`, and every reader
    # indexes by key, so only the usage-rank NUMBERS change between refreshes. Variant ids embed the
    # species name, so a species' variants also stay adjacent.
    ordered.sort(key=_key)
    keys = [_key(r) for r in ordered]

    def base_spe(sp: str) -> int | None:
        return ((dex_facts.get(sp, {}) or {}).get("stats") or {}).get("spe")

    def spd(sp: str) -> int | None:
        s = sets.get(sp) or {}
        return speed_fn(base_spe(sp), int((s.get("sps") or {}).get("sp") or 0),
                        s.get("nature"), item=s.get("item"))

    speeds = {k: spd(k) for k in keys}

    # ONE batched ncp call for the whole matrix: every (attacker-move, defender) request, indexed.
    index: dict[tuple[str, str, str], int] = {}
    requests: list[dict[str, Any]] = []
    for ai in keys:
        if ai not in attackers:
            continue
        atk = _ncp(ai, sets[ai])
        for dj in keys:
            dfd = _ncp(dj, sets[dj])
            for mv in (sets[ai].get("moves") or []):
                if not mv:
                    continue
                index[(ai, dj, mv)] = len(requests)
                requests.append({"attacker": atk, "defender": dfd, "move": mv})
    results = damage_fn(requests) if requests else []

    def best_offense(ai: str, dj: str) -> dict[str, Any] | None:
        def result_for(mv: str) -> dict[str, Any] | None:
            idx = index.get((ai, dj, mv))
            if idx is None or idx >= len(results):
                return None
            return results[idx]

        eligible = [mv for mv in (sets[ai].get("moves") or []) if mv not in ohko_moves]
        return _best_offense(eligible, result_for, dmg_fact_fn)

    def usable_ohko(ai: str, dj: str) -> list[str]:
        """Applicable OHKO routes, kept only as a contested-check qualifier."""
        usable = []
        for mv in sets[ai].get("moves") or []:
            if mv not in ohko_moves:
                continue
            idx = index.get((ai, dj, mv))
            r = results[idx] if idx is not None and idx < len(results) else None
            if r and not r.get("error") and (r.get("maxPercent") or 0) > 0:
                usable.append(mv)
        return usable

    matrix: dict[str, dict[str, Any]] = {}
    for ai in keys:
        if ai not in attackers:
            continue
        cells: dict[str, Any] = {}
        for dj in keys:
            # The MIRROR (dj == ai) is computed too. It is a real matchup — two players bring the
            # same build — and "how many hits does this need to KO its own twin" is a direct read on
            # the build's bulk-vs-power balance. The old skip was inherited from the species-keyed
            # grid, where the diagonal really was self-vs-self; with variant rows it also wrongly
            # implied the two builds of one species could not be compared.
            a_spe, d_spe = speeds.get(ai), speeds.get(dj)
            faster = None
            if a_spe is not None and d_spe is not None:
                faster = "attacker" if a_spe > d_spe else "defender" if a_spe < d_spe else "tie"
            off = best_offense(ai, dj)
            # Disguise backdoor: a Mimikyu defender eats the first hit, so annotate the effective KO
            # (boundary: label it, don't recompute the whole engine — see _disguise_adjust).
            if off and (sets.get(dj) or {}).get("ability") == "Disguise":
                off["disguise_adjusted"] = _disguise_adjust(off)
            cell_out = {
                "offense": off,
                "speed": {"attacker": a_spe, "defender": d_spe, "faster": faster},
            }
            routes = usable_ohko(ai, dj)
            if routes:                                  # sparse qualifier; don't bloat every cache cell with []
                cell_out["ohko_moves"] = routes
            cells[dj] = cell_out
        matrix[ai] = cells

    sets_out = {k: _set_provenance(k, sets[k], k in attackers) for k in keys}

    def _row(r: dict[str, Any]) -> dict[str, Any]:
        k = _key(r)
        s = sets.get(k) or {}
        out = {"rank": r.get("rank"), "species": r.get("species") or s.get("species") or k,
               "real_team_backed": k in attackers,
               "set_source": s.get("source"), "set_confidence": s.get("confidence")}
        if s.get("variant_id"):
            # `species` stays the usage-ranking label (several rows share it); `variant_id` is the
            # matrix key. Readers group by species and default to the is_modal row.
            out["variant_id"] = s["variant_id"]
            out["is_modal"] = bool(s.get("is_modal"))
            out["coverage"] = s.get("coverage")
        if s.get("run_form"):                  # transparent: ranked under the base, run as the Mega
            out["run_form"] = s["run_form"]
        return out

    species_rows = [_row(r) for r in ordered]
    species_count = len({row["species"] for row in species_rows})
    out = {
        "kind": "opponent-cache",
        # `rule` is the CACHE KEY (what the file is named by and what the retention policy expires
        # on); `season` is provenance only — the season whose usage ranking supplied this opponent
        # universe at build time. Readers must not treat `season` as a selector.
        "built_for": {"rule": rule, "season": season, "format": fmt,
                      "built_at": built_at, "top_k": species_count,
                      "variant_count": len(keys)},
        "species": species_rows,
        "sets": sets_out,
        "matrix": matrix,
        "confidence": CONFIDENCE,
        "confidence_reason": CONFIDENCE_REASON,
        "notes": [
            f"{fmt}: observed-build matchup grid over the meta top-{species_count} "
            f"species ({len(keys)} build variants). Objective facts only — no matchup score, "
            "no ranking, no best pick. "
            "This is a REFERENCE grid, not your team: match your real team LIVE.",
            "ATTACKER rows exist ONLY for real-team-backed species (a real co-occurring 4-move set, "
            "sample >= MIN_SAMPLE). A meta-only species has no real joint move set (meta marginals "
            "can't be stitched into one), so it appears as a DEFENDER only.",
            "offense = the attacker's hardest move (by max roll) vs the defender's concrete set: full "
            "roll band + ko_possible (best roll) / ko_guaranteed (worst roll). ONLY OHKO is exact; any "
            "2+ turn KO is a static approximation (see ko_caveat). speed compares the two named "
            "build variants with their own spreads/items (Choice Scarf applied), and faster = "
            "attacker/defender/tie for that exact pair. A faster spread or Scarf set is a separate "
            "observed build column, never a synthetic lane inside the pair.",
            "Accuracy-based one-hit-KO moves are excluded from offense and never upgrade C2/C1/C0. "
            "An applicable route only marks the derived check as contested (dex is_ohko authority; "
            "no probability simulation).",
            f"EVERY cell is {CONFIDENCE} confidence (reason={CONFIDENCE_REASON}): these are retained "
            "representative builds, not the opponents' actual registered builds nor yours.",
            "Source-content fingerprints bind the cache to its meta and same-rule team snapshots; "
            "a mismatch requires an offline rebuild.",
        ],
    }
    # Grade each pair once during the offline rebuild. The compact matrix makes CLI/bridge/frontend
    # reads a direct slice instead of repeated Python classification work.
    out["check_matrix"] = _compute_check_matrix(out)
    return out


def _synth_cell(cache: dict[str, Any], ai: str, dj: str, rank: int | None) -> dict[str, Any] | None:
    """Map an oppmatrix (ai -> dj) pair into the matchup-cell shape `checks.build_check` consumes.

    The matrix stores ONE direction per cell (ai's hardest move vs dj). A check needs BOTH: ai's KO on
    dj (the forward cell's offense) AND dj's hit on ai (the incoming). The incoming is exactly the
    REVERSE cell's offense — dj's hardest move vs ai — which exists only because dj is itself an attacker
    (real-team-backed). So a derived check is buildable ONLY for attacker x attacker pairs; a meta-only
    defender can retain its forward cell in the Top-K view but cannot be graded as an incoming.

    The incoming surface is that single hardest reverse move. The whole grid stays
    `low`/vs-observed-build (retained observed builds, not the user's actual team)."""
    sets = cache.get("sets") or {}
    fwd = cell(cache, ai, dj) or {}
    rev = cell(cache, dj, ai) or {}
    offense = fwd.get("offense")
    incoming = rev.get("offense")
    if (offense is None and incoming is None
            and not fwd.get("ohko_moves") and not rev.get("ohko_moves")):
        return None                                  # neither side lands a damaging move on record
    sp = fwd.get("speed") or {}
    # remap the matrix's attacker/defender speed keys to the member/opponent keys build_check reads.
    faster = {"attacker": "member", "defender": "opponent", "tie": "tie"}.get(sp.get("faster"))
    speed = {"member": sp.get("attacker"), "opponent": sp.get("defender"), "faster": faster}
    synth = {
        "opponent": dj, "usage_rank": rank,
        "speed": speed,
        "defense_type": {"max_effectiveness_vs_member": None},   # oppcache has no type layer -> bulk basis
        "offense": offense,
        "defense_damage": ({"moves": [incoming], "worst": incoming} if incoming else None),
        "ohko_moves": fwd.get("ohko_moves") or [],
        "incoming_ohko_moves": rev.get("ohko_moves") or [],
        "opponent_has_priority": None,               # UNTRACKED in the cache — None (unknown), not a
                                                     # fabricated False (which would falsely license a
                                                     # priority_first upgrade once the cache tracks it).
    }
    checks.assert_cell_contract(synth)               # loud-fail if build_check's cell contract grows a key
    return synth


def _compact_pair_check(chk: dict[str, Any] | None) -> dict[str, Any] | None:
    if not chk:
        return None
    out = {"grade": chk["grade"]}
    if chk.get("c1_mode"):
        out["c1_mode"] = chk["c1_mode"]
    if chk.get("c0_kind"):
        out["c0_kind"] = chk["c0_kind"]
    if (chk.get("resolvability") or {}).get("verdict") == "contested":
        out["contested"] = True
    return out


def _compute_check_matrix(cache: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Pure compact grade projection, run once by the offline cache builder."""
    fmt = (cache.get("built_for") or {}).get("format") or "single"
    matrix = cache.get("matrix") or {}
    sets = cache.get("sets") or {}
    ranks = {(r.get("variant_id") or r.get("species")): r.get("rank")
             for r in cache.get("species") or []}
    out: dict[str, dict[str, Any]] = {}
    for ai in matrix:
        row: dict[str, Any] = {}
        for dj in matrix:
            synth = _synth_cell(cache, ai, dj, ranks.get(dj))
            if synth is None:
                continue
            compact = _compact_pair_check(
                checks.build_check(synth, ai, sets.get(dj), fmt, member_set=sets.get(ai)))
            if compact:
                row[dj] = compact
        if row:
            out[ai] = row
    return out


def selector_keys(cache: dict[str, Any], query: str | None, keys: list[str]) -> list[str]:
    """Exact build ids or actual run-form selectors; a meta base label never widens a form query."""
    if query is None:
        return keys
    sets = cache.get("sets") or {}
    own = sets.get(query) or {}
    if query in sets and (query.startswith("variant:") or query not in {own.get("species"), own.get("run_form")}):
        return [query] if query in keys else []
    return [key for key in keys if ((sets.get(key) or {}).get("run_form")
                                    or (sets.get(key) or {}).get("species")) == query]


def derive_checks(cache: dict[str, Any], attacker: str | None = None,
                  defender: str | None = None) -> dict[str, Any]:
    """Public observed-build check matrix plus per-attacker species portfolios.

    A species selector expands to all of its variants; an exact variant id selects one. Pair grades are
    read from the offline-precomputed ``check_matrix``. No hidden species floor is folded into a cell.
    """
    fmt = (cache.get("built_for") or {}).get("format") or "single"
    matrix = cache.get("check_matrix")
    if not isinstance(matrix, dict):
        raise ValueError("opponent cache has no precomputed check_matrix; rebuild it")
    sets = cache.get("sets") or {}
    rows = cache.get("species") or []
    meta = {(r.get("variant_id") or r.get("species")): r for r in rows}

    def selected(query: str | None, keys: list[str]) -> list[str]:
        return selector_keys(cache, query, keys)

    attackers = selected(attacker, list(matrix))
    target_order = [r.get("variant_id") or r.get("species") for r in rows]
    targets = selected(defender, [key for key in target_order if key])
    pair_matrix = {ai: {dj: matrix.get(ai, {}).get(dj) for dj in targets
                        if matrix.get(ai, {}).get(dj) is not None}
                   for ai in attackers}
    summaries: dict[str, Any] = {}
    for ai in attackers:
        cells = []
        for dj in targets:
            pair = pair_matrix.get(ai, {}).get(dj)
            row = meta.get(dj) or {}
            target_set = sets.get(dj) or {}
            cells.append({
                "opponent": target_set.get("species") or row.get("species") or dj,
                "opponent_variant": dj,
                "opponent_is_modal": bool(target_set.get("is_modal")),
                "opponent_coverage": target_set.get("coverage"),
                "usage_rank": row.get("rank"),
                "check": ({**pair,
                           "resolvability": {"verdict": "contested" if pair.get("contested") else "clean"}}
                          if pair else None),
            })
        summaries[ai] = checks.coverage_summary([{"member": ai, "cells": cells}])
    species_count = len({(sets.get(key) or {}).get("species") for key in targets})
    selected_keys = set(attackers) | set(targets)
    selected_rows = [r for r in rows
                     if (r.get("variant_id") or r.get("species")) in selected_keys]
    return {
        "kind": "opponent-build-checks", "format": fmt,
        "top_k": species_count, "confidence": cache.get("confidence") or CONFIDENCE,
        "confidence_reason": cache.get("confidence_reason") or CONFIDENCE_REASON,
        "species": selected_rows,
        "variants": {key: sets[key] for key in set(attackers + targets) if key in sets},
        "matrix": pair_matrix,
        "summaries": summaries,
        "notes": [
            "Every matrix cell is one observed representative build pair. Species summaries are derived "
            "portfolios; observed_floor always carries witness variant ids.",
            f"Every grade is {CONFIDENCE} confidence (reason={CONFIDENCE_REASON}): representative-set "
            "reference facts, not the user's registered team. Ordinal labels only; never summed.",
        ],
    }


def _set_provenance(key: str, s: dict[str, Any], real_team_backed: bool) -> dict[str, Any]:
    """The concrete set stored in the cache — the fields a reader needs to interpret a cell. `key`
    is the matrix key (a variant id, or the meta label on the meta-only path); `run_form` is the form
    actually run when it differs (a Mega registered as base+stone) — the calc used the run form's
    stats, so it is surfaced here."""
    out = {
        "species": s.get("species") or key,
        "run_form": s.get("run_form"),
        "ability": s.get("ability"), "item": s.get("item"), "nature": s.get("nature"),
        "moves": s.get("moves") if real_team_backed else None,   # real joint set only for attackers
        "sps": s.get("sps") or None,
        "source": s.get("source"),
        "confidence": s.get("confidence"),
        "real_team_backed": real_team_backed,
        "note": s.get("note"),
    }
    # Variant identity + how much of the species' real teams this build actually accounts for. Without
    # `coverage` a 2%-coverage archetype reads as an equal-weight peer of an 88% one.
    if s.get("variant_id"):
        out["variant_id"] = s["variant_id"]
        out["is_modal"] = bool(s.get("is_modal"))
        out["coverage"] = s.get("coverage")
        out["variant_sample"] = s.get("variant_sample")
        out["variant_count"] = s.get("variant_count")
        out["cluster"] = s.get("cluster")
        out["cluster_basis"] = s.get("cluster_basis")
        out["represented_coverage"] = s.get("represented_coverage")
        out["unrepresented_coverage"] = s.get("unrepresented_coverage")
        out["speed_profile"] = s.get("speed_profile")
        if s.get("base_ability"):        # pre-Mega ability; fires on switch-in before Mega evolution
            out["base_ability"] = s["base_ability"]
    return out


# --- markdown formatter ------------------------------------------------------

def _ko(off: dict[str, Any] | None) -> str:
    if not off:
        return "—"
    txt = f"{off['move']} {off['min_percent']}–{off['max_percent']}%"
    txt += f" ({off['ko']})" if off.get("ko") else ""
    da = off.get("disguise_adjusted")
    if da:
        eff = da.get("effective_ko_guaranteed") or da.get("effective_ko_possible")
        txt += f" [Disguise: ~{eff} hits effective]" if eff else " [Disguise: +1 hit effective]"
    return txt


def format_oppcache_md(cache: dict[str, Any], attacker: str | None = None,
                       defender: str | None = None) -> str:
    bf = cache.get("built_for", {})
    head = i18n.t("opp_matrix_head", fmt=bf.get("format"), top_k=bf.get("top_k"),
                  conf=cache.get("confidence"), reason=cache.get("confidence_reason"))
    lines = [head, i18n.t("opp_built_for", season=bf.get("season"), rule=bf.get("rule"),
                          built_at=bf.get("built_at")) + "\n"]
    matrix = cache.get("matrix") or {}
    sets = cache.get("sets") or {}

    def one_attacker(ai: str) -> list[str]:
        row = matrix.get(ai)
        if row is None:
            s = sets.get(ai) or {}
            why = (i18n.t("opp_why_meta_only") if s else i18n.t("opp_why_not_topk"))
            return [i18n.t("opp_no_attacker_row", ai=ai, why=why)]
        s = sets.get(ai) or {}
        out = [i18n.t("opp_attacker_head", ai=ai, item=s.get("item"), ability=s.get("ability"),
                      nature=s.get("nature"), conf=s.get("confidence"), source=s.get("source")),
               f"  - {i18n.t('opp_moves')}: {', '.join(s.get('moves') or []) or '—'}"]
        items = [(defender, row.get(defender))] if defender else sorted(row.items())
        for dj, c in items:
            if c is None:
                out.append(i18n.t("opp_not_in_matrix", dj=dj))
                continue
            sp = c["speed"]
            arrow = {"attacker": i18n.t("opp_arrow_outspeeds"), "defender": i18n.t("opp_arrow_slower"),
                     "tie": i18n.t("opp_arrow_tie")}.get(sp.get("faster"), i18n.t("opp_arrow_unknown"))
            out.append(i18n.t("opp_vs_line", dj=dj, arrow=arrow, a_spe=sp.get("attacker"),
                              d_spe=sp.get("defender"), ko=_ko(c.get("offense"))))
        return out

    if attacker:
        lines += one_attacker(attacker)
    else:
        for ai in matrix:
            lines += one_attacker(ai)
    if not matrix:
        lines.append(i18n.t("opp_empty_matrix"))
    return "\n".join(lines)


def derive_check_grid(cache: dict[str, Any]) -> dict[str, Any]:
    """The precomputed lean variant × variant grid used by the UI."""
    fmt = (cache.get("built_for") or {}).get("format") or "single"
    grid = cache.get("check_matrix")
    if not isinstance(grid, dict):
        raise ValueError("opponent cache has no precomputed check_matrix; rebuild it")
    return {
        "kind": "opponent-check-grid", "format": fmt,
        "confidence": cache.get("confidence") or CONFIDENCE,
        "confidence_reason": cache.get("confidence_reason") or CONFIDENCE_REASON,
        "grid": grid,
        "note": "C2/C1/C0 per ordered observed-build pair, precomputed during the cache rebuild. "
                "Damage/speed for the pair live in the KO matrix and are not repeated here.",
    }
