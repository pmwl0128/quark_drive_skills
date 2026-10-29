#!/usr/bin/env python
"""Representative-set reader over the real-team library (data/teams/<season>_<format>.jsonl).

Real teams are REAL JOINT objects: each member's ability/item/nature/moves co-occur, which the meta
usage panels (independent marginals) cannot give — so this is the higher-accuracy source for those
fields and the cure for the marginal-stitch problem. When a format partition has no usable rows, every
function returns None/[] and the resolver falls back to meta: the system stays stable with no real data.

Boundaries:
- FACTS ONLY. `count`/`sample`/`share` and the performance tags (placing/record/rating) are
  provenance, never a synthetic strength score.
- NEVER merged across single/double (different metagames) — keyed by format, loaded per format.
- The SP/spread dimension is carried only when the raw row exposes a real Champions SP spread, so
  `sps` is the modal LEGAL spread AMONG the teams running the modal set (it co-occurs — not a
  marginal stitch). Rows without a spread yield `sps: None` and the resolver falls back to the meta
  spread. Never invent a spread.
- The spread has its OWN sample (`spread_count`/`spread_sample`/`spread_confidence`): only some of the
  modal-set teams carry a spread, so the spread's confidence is folded from THAT thinner sample — it
  never inherits the set's. All present, legal spreads are treated uniformly regardless of source;
  illegal lines (per-stat/total cap violations — e.g. an EV-format paste) are never counted.
- Spread aggregation is per investment SHAPE, not per exact line: exact SP lines fragment naturally
  (players fine-tune ±few points; defensive lines are per-calc bespoke while offensive lines
  standardize, so an exact-modal pick is also systematically biased toward offense). The dominant
  shape cluster carries the confidence share axis; the emitted `sps` is the modal EXACT line WITHIN
  that cluster — always a real observed line, NEVER a per-stat average/synthesis (that would be a
  fabricated spread nobody runs, poisoned by bimodality). A second shape big enough to matter is
  disclosed (`spread_shape_runner_up`), mirroring how (item, ability) clustering guards bimodality.

This module holds NO battle data — only the reader/aggregator over the dev-built library.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import completeness
import evidence
import handover
import rules
from mega import base_of_form_name

SCRIPTS = Path(__file__).resolve().parent
DEFAULT_DATA = SCRIPTS.parent / "data" / "teams"   # ships WITH a snapshot (tracked); maintainer refreshes via dev/update/team
_SEASON_RULE = rules.SEASON_RULE          # single source (rules.py); do not re-declare a local copy

# --------------------------------------------------------------------------- #
# Sample-sufficiency criteria (the M5 trigger, resolved 2026-06-25).
#
# The bar is PER SPECIES (and per archetype cluster), not a single per-format gate: the doubles
# library (2879 teams, 96 species at >=10 occurrences) and the singles library (44 teams, only 5
# species at >=10) have wildly different per-species density, so a global "is the format ready" switch
# would either starve singles or trust noise. Instead every representative set is emitted only when its
# own sample clears MIN_SAMPLE, and confidence is folded from BOTH sample size and modal share — a
# thin or fragmented species reads `low`, never silently masquerading as solid. The future cache layer
# (M5 step 2) builds a cell ONLY for species clearing MIN_SAMPLE and stamps every cell
# `low` (reason=vs-observed-build) regardless, so this same per-species bar governs cache admission.
# --------------------------------------------------------------------------- #
# A species/archetype seen in fewer than this many real teams is too thin to trust as
# "representative" — the resolver falls back to meta below it. Tunable; deliberately conservative for
# thin early-season samples.
MIN_SAMPLE = 3
# A cluster also needs material support within its species pool. The reviewed M-B evidence showed that
# the old fixed top-3 ceiling retained only ~87-88% of Top-60 observations, while a 3% share floor and
# five-row ceiling retains ~89% singles / ~92% doubles without admitting the sub-percent noise that a
# fixed count of three permits in the much larger doubles sample. Keep admission and capacity separate:
# MIN_SAMPLE rejects tiny absolute samples; MIN_CLUSTER_COVERAGE rejects tiny relative tails.
MIN_CLUSTER_COVERAGE = 0.03
MAX_CLUSTERS = 5
# Sample-size -> confidence (crude tiers, honest about small samples; not a Wilson interval because
# we are choosing a modal set, not estimating a win rate).
_CONF_TIERS = ((10, "high"), (5, "medium"), (MIN_SAMPLE, "low"))

from metalink import SPREAD_TO_SPS as _SPS_KEYS


def data_dir() -> Path:
    """The real-team library dir (CHAMP_TEAM_DATA overrides, matching the dev pipeline)."""
    return Path(os.environ.get("CHAMP_TEAM_DATA", DEFAULT_DATA))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    teams: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            teams.append(json.loads(line))
    return teams


def load_teams(fmt: str, season: str | None = None) -> list[dict[str, Any]]:
    """All stored teams for a format (optionally one season). [] when the library is absent/empty.

    season=None reads every season's file for the format; callers that must not mix regulations should
    use load_teams_for_rule or pass an explicit season."""
    d = data_dir()
    if not d.exists():
        return []
    pattern = f"{season}_{fmt}.jsonl" if season else f"*_{fmt}.jsonl"
    teams: list[dict[str, Any]] = []
    for jf in sorted(d.glob(pattern)):
        teams.extend(_read_jsonl(jf))
    return teams


# Keyed on the data dir too: CHAMP_TEAM_DATA can differ within one process (tests point it at fixtures),
# so a cache that ignored it would serve one dir's result for another.
_TEAM_CACHE: dict[tuple[str, str, str | None], list[dict[str, Any]]] = {}
# (data_dir, format, rule) -> (validity token, pool). See `cached_teams_for_rule` for the token.
_RULE_TEAM_CACHE: dict[tuple[str, str, str],
                       tuple[tuple[Any, ...], list[dict[str, Any]]]] = {}


def cached_teams(fmt: str, season: str | None = None) -> list[dict[str, Any]]:
    """Process-local cache for shipped real-team partitions."""
    key = (str(data_dir()), fmt, season)
    if key not in _TEAM_CACHE:
        _TEAM_CACHE[key] = load_teams(fmt, season)
    return _TEAM_CACHE[key]


def load_teams_for_rule(fmt: str, rule: str) -> list[dict[str, Any]]:
    """All stored teams for a format whose row provenance says `rule`.

    Files stay season-partitioned (`M-3_double.jsonl`, `M-4_double.jsonl`, ...), but default build
    consumption is rule-scoped: sibling seasons under the same rule contribute to one real-team
    evidence pool, while rows from other rules stay out. Filtering on the row's own `rule` keeps the
    storage label authoritative and survives future same-rule season additions without another
    hardcoded filename list.
    """
    d = data_dir()
    if not d.exists() or not rule:
        return []
    teams: list[dict[str, Any]] = []
    for jf in sorted(d.glob(f"*_{fmt}.jsonl")):
        season, _ = jf.stem.rsplit("_", 1)
        file_rule = _SEASON_RULE.get(season)
        teams.extend(t for t in _read_jsonl(jf) if (t.get("rule") or file_rule) == rule)
    # Source rows retain their old `rule`; only receipt-approved rows receive an in-memory target
    # annotation. Invalid or stale receipts fail loudly instead of silently widening the pool.
    teams.extend(handover.load_teams(d, fmt, rule, _read_jsonl))
    return teams


def cached_teams_for_rule(fmt: str, rule: str) -> list[dict[str, Any]]:
    """Process-local cache for a rule-scoped real-team pool.

    Keyed on a validity TOKEN, not just the pool identity: a long-lived bridge can cross the hard
    handover expiry while running, and a dev refresh can rewrite a partition underneath it. The token
    covers both (plus every authority the receipt is bound to), so the entry is reused only while it
    would still be rebuilt identically — and is discarded the moment the window closes. Rebuilding
    unconditionally instead, which is what a receipt used to force, cost a full re-parse and re-hash
    of the whole library on EVERY per-species call (audit 2026-09-08)."""
    team_dir = data_dir()
    key = (str(team_dir), fmt, rule)
    token = handover.pool_token(team_dir, fmt, rule)
    cached = _RULE_TEAM_CACHE.get(key)
    if cached is not None and cached[0] == token:
        return cached[1]
    teams = load_teams_for_rule(fmt, rule)
    _RULE_TEAM_CACHE[key] = (token, teams)
    return teams


def clear_cached_teams_for_rule(fmt: str, rule: str) -> None:
    """Force the next rule-pool read to use the current on-disk partitions."""
    _RULE_TEAM_CACHE.pop((str(data_dir()), fmt, rule), None)


def seasons_in(teams: list[dict[str, Any]]) -> list[str]:
    """Sorted season labels present in a loaded team pool."""
    return sorted({str(t.get("season")) for t in teams if t.get("season")})


def partition_count(fmt: str, season: str | None = None) -> int | None:
    """Fast row count from data/teams/index.json when available; None means fall back to load_teams."""
    if not season:
        return None
    p = data_dir() / "index.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    row = d.get(f"{season}_{fmt}") if isinstance(d, dict) else None
    try:
        return int(row.get("count")) if isinstance(row, dict) and row.get("count") is not None else None
    except (TypeError, ValueError):
        return None


def partition_count_for_rule(fmt: str, rule: str) -> int | None:
    """Fast-ish row count for a rule pool when index.json records rules; None means fall back to load."""
    p = data_dir() / "index.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(d, dict):
        return None
    total = 0
    saw = False
    for row in d.values():
        if not isinstance(row, dict) or row.get("format") != fmt or row.get("rule") != rule:
            continue
        try:
            total += int(row.get("count") or 0)
            saw = True
        except (TypeError, ValueError):
            return None
    return total if saw else None


def _confidence(sample: int, share: float = 1.0) -> str:
    """Confidence from BOTH the sample size AND the modal share. A heavily-fragmented modal — e.g. 10
    distinct sets where the top one is only 1/10 (share 0.1) — is NOT representative even with a large
    sample, so it can't read 'high' on sample count alone (audit 2026-06-24). Take the lower of the
    sample tier and the share tier."""
    s_tier = "low"
    for thr, lvl in _CONF_TIERS:
        if sample >= thr:
            s_tier = lvl
            break
    sh_tier = "high" if share >= 0.5 else "medium" if share >= 0.34 else "low"
    return evidence.min_confidence(s_tier, sh_tier)


def _cap_by_completeness(conf: str, members: list[dict[str, Any]]) -> str:
    """Lower `conf` to what the members' completeness allows. An archetype is only as trustworthy as the
    sets that form it: a single owner-observed / untagged member running the exact set floors it 'high',
    but an all-`extracted_set` (validated but reverse-engineered) modal is capped 'medium'. Members are
    template-eligible (moves present), so the floor is high for observed/untagged and medium for
    extracted. Empty -> unchanged."""
    if not members:
        return conf
    cap = max((completeness.confidence_floor(m.get("completeness"), has_moves=bool(m.get("moves")))
               for m in members), key=evidence.confidence_rank)
    return evidence.floor_confidence(conf, cap)


# A stat carrying at least this much SP is a MAJOR investment — the spread-shape key. Evaluated on the
# real library (2026-07-02): the dominant-shape share is stable under 12/16/20, so this is not a
# fragile knob; 16 = half the per-stat cap.
_SHAPE_MAJOR_SP = 16


def _spread_shape(spread: dict[str, int]) -> frozenset:
    """The spread's investment SHAPE: the stats carrying a major (>= _SHAPE_MAJOR_SP) SP investment.
    Exact SP lines fragment naturally (fine-tuning, per-calc bespoke defensive lines) while the
    DIRECTION of investment is what real players converge on (doubles Garchomp: exact modal 20/34 vs
    shape 34/34; singles M-2 Garchomp 2/11 vs 11/11) — so the shape is the aggregation level for the
    spread's confidence."""
    return frozenset(k for k, v in spread.items() if k in _SPS_KEYS and int(v or 0) >= _SHAPE_MAJOR_SP)


def _modal_spread(members: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Representative REAL spread among `members` + its sample stats, or None when none carry a legal
    spread. Called only on the teams running the modal joint set, so the spread co-occurs with that
    set (not a marginal).

    Aggregation is per SHAPE (`_spread_shape`), then the modal EXACT line WITHIN the dominant shape
    cluster is emitted — always a real observed line, never a per-stat average (a synthesis nobody
    runs, poisoned by bimodality). Exact-modal picking alone is also systematically biased toward
    offensive lines (they standardize, e.g. 2/32/32, while defensive lines are per-calc bespoke), so
    the shape cluster decides the direction first. Members with no spread or an illegal one are
    skipped — `sample` is the spread-carrying subset, the honest denominator for the
    spread's confidence (share axis = shape coverage). A runner-up shape clearing MIN_SAMPLE is
    surfaced for bimodality disclosure. Legality = the shared `rules.legal_spread` (sources store the
    paste's stat line verbatim, so an EV-format 0–252 or corrupt line must never become the modal
    Champions spread — only spreads that exist AND are legal count)."""
    pairs = [(m, m["spread"]) for m in members if m.get("spread") and rules.legal_spread(m["spread"])]
    if not pairs:
        return None
    shapes = Counter(_spread_shape(s) for _, s in pairs)
    top_shape, shape_cnt = shapes.most_common(1)[0]
    cluster = [(m, s) for m, s in pairs if _spread_shape(s) == top_shape]
    top, cnt = Counter(tuple(sorted(s.items())) for _, s in cluster).most_common(1)[0]
    sps = {_SPS_KEYS[k]: int(v) for k, v in top if k in _SPS_KEYS}
    if not sps:
        return None
    runner = next(((sh, c) for sh, c in shapes.most_common()[1:] if c >= MIN_SAMPLE), None)
    return {
        "sps": sps,
        "count": cnt,                                  # exact-line count (within the dominant shape)
        "sample": len(pairs),                          # spread-carrying members (legal spreads only)
        "shape": sorted(top_shape),                    # dominant investment shape (majors)
        "shape_count": shape_cnt,
        "runner_up": ({"shape": sorted(runner[0]), "count": runner[1]} if runner else None),
        "carriers": [m for m, _ in cluster],           # dominant-shape carriers (completeness basis)
    }


def shape_label(shape: list[str]) -> str:
    """The ONE rendering of an investment shape ("def+hp"; empty = "flat") — shared by the repset
    note, the resolver provenance (sources) and the team MD, so the same shape never prints
    differently across the three surfaces."""
    return "+".join(shape) or "flat"


def _joint_key(m: dict[str, Any]) -> tuple:
    """A member's full REAL JOINT identity: ability+item+nature+sorted moves co-occur (no stitch)."""
    return (m.get("ability"), m.get("item"), m.get("nature"),
            tuple(sorted(mv for mv in (m.get("moves") or []) if mv)))


def _team_has_item(team: dict[str, Any]) -> bool:
    """True if ANY member of `team` carries an item — i.e. the source actually captured item data for
    this team. Used to tell an INTENTIONAL itemless set (a member with no item on a team that DID record
    items elsewhere — e.g. an Acrobatics/Unburden user) from a team whose items were simply never
    scraped (every member itemless)."""
    return any(m.get("item") for m in team.get("pokemon", []))


def _template_eligible_member(m: dict[str, Any], *, team_has_item: bool) -> bool:
    """True when a member has enough observed fields to build a representative set template.

    `completeness` is a source-level claim; this also checks the actual fields so mislabeled or
    species-only rows cannot inflate samples or emit empty/None modal sets. Spread is intentionally
    optional because some rows have real joint ability/item/nature/moves but no SP spread.

    `item` is held to a FINER rule than a bare non-null check (audit 2026-06-26): a member with no item
    is still a complete set when its team carries items elsewhere (a deliberate no-item build like
    Acrobatics Talonflame — losing those silently under-counts a real archetype), but when the WHOLE team
    is itemless the items were never captured, so its members are not template-eligible. `team_has_item`
    carries that team-level signal from `_team_has_item`.
    """
    moves = [mv for mv in (m.get("moves") or []) if mv]
    return (
        completeness.template_eligible(m.get("completeness"))   # observed_full_set OR validated extracted_set
        and bool(m.get("species"))
        and bool(m.get("ability"))
        and bool(m.get("nature"))
        and bool(moves)
        and (bool(m.get("item")) or team_has_item)
    )


def library_forms(species: str, teams: list[dict[str, Any]]) -> list[str]:
    """Every library species-name that IS `species`, including its Mega forms.

    The two partitions store a registered Mega differently: doubles as base species + stone (one
    name), singles as 'Mega X' under its own name. Archetype clustering works inside ONE name, so on
    the singles shape a species' base builds and its Mega builds live in separate pools and a caller
    asking about "Greninja" could only ever see one of them. Returns the names in the order
    [base, Mega...], base first, and only those actually present in `teams`.
    """
    present: set[str] = set()
    for t in teams:
        for m in t.get("pokemon", []):
            sp = m.get("species")
            if isinstance(sp, str) and sp:
                present.add(sp)
    forms = [species] if species in present else []
    forms += sorted(sp for sp in present
                    if sp != species and sp.startswith("Mega ") and base_of_form_name(sp) == species)
    return forms


def _members_of(species: str, teams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    native: list[dict[str, Any]] = []
    transitioned: list[dict[str, Any]] = []
    for t in teams:
        thi = _team_has_item(t)
        bucket = transitioned if t.get("target_rule") else native
        for member in t.get("pokemon", []):
            if member.get("species") != species or not _template_eligible_member(member, team_has_item=thi):
                continue
            stamped = dict(member)
            stamped["_evidence_rule"] = t.get("evidence_rule") or t.get("rule")
            stamped["_target_rule"] = t.get("target_rule") or t.get("rule")
            if t.get("expires_at"):
                stamped["_handover_expires_at"] = t["expires_at"]
            bucket.append(stamped)
    # The receipt's clock is the only cutover. Native sample count must not shorten the week.
    return [*native, *transitioned]


def _build_modal_set(species: str, fmt: str, members: list[dict[str, Any]]) -> dict[str, Any]:
    """Modal joint set over `members`, with share/confidence denominated by len(members). Reused by the
    global-modal path (members = ALL of the species) and the per-cluster path (members = one cluster),
    so a cluster's `share`/`confidence` reflect the archetype's own density. Assumes members non-empty."""
    sample = len(members)
    counts = Counter(_joint_key(m) for m in members)
    top_key, cnt = counts.most_common(1)[0]
    ability, item, nature, moves = top_key
    # Spread is taken ONLY over the teams running this exact set, so it co-occurs with it (real joint).
    winners = [m for m in members if _joint_key(m) == top_key]
    ms = _modal_spread(winners)
    # The spread earns its OWN confidence from its OWN sample: only the winners carrying a legal spread
    # count, and its share axis is the dominant SHAPE's coverage (exact lines fragment; the direction is
    # the consensus), capped by the shape carriers' completeness. It never inherits the set's high
    # (audit 2026-07-02). The claim `spread_confidence` backs is therefore "invests in this direction,
    # shown as a real representative line", not "runs exactly this line" — `spread_count` carries the
    # exact-line figure.
    if ms:
        sps = ms["sps"]
        spread_conf = _cap_by_completeness(
            _confidence(ms["sample"], ms["shape_count"] / ms["sample"]), ms["carriers"])
        ru = ms["runner_up"]
        spread_note = (f"spread shape {shape_label(ms['shape'])} covers {ms['shape_count']}/"
                       f"{ms['sample']} of the spread-carrying set ({spread_conf}); sps = the modal "
                       f"real line within it ({ms['count']}/{ms['shape_count']})"
                       + (f"; second shape {shape_label(ru['shape'])} {ru['count']}/{ms['sample']}"
                          if ru else "") + ".")
    else:
        sps, spread_conf = None, None
        spread_note = "spread not in source."
    # Cap the sample/share confidence by how much we trust the sets that FORM this archetype: an
    # owner-observed (or untagged legacy) member running the exact set lifts it to its floor 'high', but
    # an all-`extracted_set` modal is community-reconstructed -> capped 'medium', so a big reverse-
    # engineered pool can't read 'high' on count alone (audit 2026-07-01). observed_full_set data (all
    # current sources) floors 'high', so this is a no-op for the existing library.
    conf = _confidence(sample, cnt / sample)
    conf = _cap_by_completeness(conf, winners)
    expiries = sorted({m.get("_handover_expires_at") for m in members
                       if m.get("_handover_expires_at")})
    result = {
        "species": species, "source": "real-team", "format": fmt,
        "ability": ability, "item": item, "nature": nature, "moves": list(moves),
        "sps": sps,                               # real co-occurring LEGAL spread, or None
        "spread_origin": "real-team" if sps else None,
        # The spread's OWN sample stats — its confidence basis, distinct from count/sample (the set's).
        # spread_count = teams running exactly `sps`; spread_shape_count = teams in its shape cluster.
        "spread_count": ms["count"] if ms else 0,
        "spread_sample": ms["sample"] if ms else 0,
        "spread_shape": ms["shape"] if ms else None,
        "spread_shape_count": ms["shape_count"] if ms else 0,
        "spread_shape_runner_up": ms["runner_up"] if ms else None,
        "spread_confidence": spread_conf,
        "count": cnt, "sample": sample, "share": round(cnt / sample, 3),
        "confidence": conf,
        "note": (f"real-team modal joint set: {cnt}/{sample} teams ({species}, {fmt}); "
                 f"ability/item/nature/moves co-occur (no marginal stitch). {spread_note}"),
    }
    if any(isinstance(m.get("_registration"), dict) for m in winners):
        registrations = Counter(((m.get("_registration") or m).get("species"),
                                 (m.get("_registration") or m).get("ability")) for m in winners)
        result["configuration_basis"] = "battle_form"
        result["registration_sample"] = cnt
        result["registration_modes"] = [{"species": sp, "ability": ability, "count": count}
                                        for (sp, ability), count in sorted(registrations.items(), key=lambda kv: str(kv[0]))]
        result["note"] += " Species/ability use the deterministic battle-form projection; registration_modes preserve the observed values."
    if expiries:
        evidence_rules = sorted({m.get("_evidence_rule") for m in members if m.get("_evidence_rule")})
        target_rules = sorted({m.get("_target_rule") for m in members if m.get("_target_rule")})
        result.update({
            "evidence_rules": evidence_rules,
            "target_rule": target_rules[0] if len(target_rules) == 1 else None,
            "handover_expires_at": expiries[0] if len(expiries) == 1 else None,
        })
    return result


def representative_set_from_teams(species: str, fmt: str, teams: list[dict[str, Any]], *,
                                  min_sample: int = MIN_SAMPLE) -> dict[str, Any] | None:
    """The most-common REAL JOINT set ({ability,item,nature,moves}) actually run for `species`.

    Returns None when fewer than `min_sample` real occurrences exist (too thin to trust). Pure /
    injectable for tests. `sps` is the modal SP spread among the teams running this exact set when the
    raw row exposes one, else None — see module docstring. This is the SINGLE global modal (the
    opponent-set resolver's choice); for the up-to-3 archetype split use
    `representative_sets_from_teams`."""
    members = _members_of(species, teams)
    if len(members) < min_sample:
        return None
    return _build_modal_set(species, fmt, members)


def representative_sets_from_teams(species: str, fmt: str, teams: list[dict[str, Any]], *,
                                   min_sample: int = MIN_SAMPLE,
                                   max_clusters: int = MAX_CLUSTERS,
                                   item: str | None = None) -> list[dict[str, Any]]:
    """Up to `max_clusters` REAL archetypes for `species`, split by (item, ability) cluster.
    A species commonly runs genuinely distinct builds (e.g. Choice Scarf vs Assault Vest);
    one global modal would hide that bimodality. Each archetype = the modal joint set WITHIN its
    (item, ability) cluster, ranked by cluster size; only clusters with >= `min_sample` teams qualify.

    `item` (optional) restricts to members holding that item before clustering — used to resolve a Mega
    in the doubles library, which stores it as base species + stone (so item=required_item isolates the
    Mega's archetypes; audit 2026-06-25). `max_clusters` must be >= 1; <1 yields [] (no archetype
    requested), never the fragmented fallback.

    Per-set fields beyond the global-modal shape: `cluster` ({item,ability}); `coverage` (the cluster's
    share of the QUERIED pool); `species_sample` (that pool's size); `species_sample_total` (the
    whole-species count, == `species_sample` unless an `item` filter narrows the pool); `item_filter`
    (the item the pool was filtered to, or None). When `item` is set, `coverage`/`species_sample` are
    denominated on the filtered subpool (e.g. a doubles Mega isolated by its stone), NOT the whole
    species — so consumers must read coverage against `pool_label`/`item_filter`, never as base-species
    coverage (audit 2026-06-26). `share`/`confidence` are denominated WITHIN the cluster. Returns [] when
    the species itself is below `min_sample`. When no single (item,ability)
    cluster reaches the bar (a fragmented species that still clears `min_sample` overall), falls back to
    a single global modal entry flagged `fragmented` (cluster=None, coverage=None) — honest degrade,
    never fabrication (§10 trap ④)."""
    if max_clusters < 1:
        return []
    all_members = _members_of(species, teams)
    species_total = len(all_members)          # the WHOLE-species count, before any item filter
    if item is not None:
        members = [m for m in all_members if m.get("item") == item]
    else:
        members = all_members
    sample = len(members)                     # the QUERIED pool: the item-filtered subpool when an item
    if sample < min_sample:                   # filter is active (doubles-Mega isolation), else == species_total
        return []
    # When an item filter narrows the pool, `coverage`/`species_sample` are denominated on THAT subpool,
    # not the whole species — so a doubles-Mega query ("Mega Charizard Y" -> Charizard @ Charizardite Y)
    # must label its denominator as the filtered pool, never as base Charizard's full real teams. The
    # honest figure is carried in the note (`pool_label`) plus `item_filter`/`species_sample_total`
    # so a consumer/cache can never read the cluster coverage as a base-species coverage (audit 2026-06-26).
    pool_label = f"{species} @ {item}" if item is not None else species
    clusters: dict[tuple, list[dict[str, Any]]] = {}
    for m in members:
        clusters.setdefault((m.get("item"), m.get("ability")), []).append(m)
    ranked = sorted(clusters.items(),
                    key=lambda kv: (-len(kv[1]), str(kv[0][0] or ""), str(kv[0][1] or "")))
    out: list[dict[str, Any]] = []
    for (c_item, ability), cms in ranked:
        if len(out) >= max_clusters:      # checked BEFORE building so max_clusters is an exact ceiling
            break
        coverage = len(cms) / sample
        if len(cms) < min_sample or coverage < MIN_CLUSTER_COVERAGE:
            continue
        s = _build_modal_set(species, fmt, cms)
        s["cluster"] = {"item": c_item, "ability": ability}
        s["coverage"] = round(coverage, 4)
        speed_rows = [(m.get("spread") or {}).get("spe") for m in cms
                      if isinstance((m.get("spread") or {}).get("spe"), int)]
        speed_natures = sorted({str(m.get("nature")) for m in cms if m.get("nature")})
        s["speed_profile"] = {
            "sample": len(speed_rows),
            "min_spe_sp": min(speed_rows) if speed_rows else None,
            "max_spe_sp": max(speed_rows) if speed_rows else None,
            "natures": speed_natures,
            "heterogeneous": (len(set(speed_rows)) > 1 or len(speed_natures) > 1),
        }
        s["species_sample"] = sample                  # the queried pool (filtered subpool when item-filtered)
        s["species_sample_total"] = species_total     # the whole-species count (== species_sample if no filter)
        s["item_filter"] = item
        s["note"] = (f"real-team archetype item={c_item!r}/ability={ability!r}: covers {len(cms)}/{sample}"
                     f" of {pool_label} real teams ({fmt}); modal joint set {s['count']}/{len(cms)} within"
                     f" it (co-occur). {'spread co-occurs.' if s['sps'] else 'spread not in source.'}")
        out.append(s)
    if not out:
        g = _build_modal_set(species, fmt, members)
        g["cluster"] = None
        # NOT a cluster -> cluster `coverage` is undefined (None). Reusing 1.0 read as "this set is on
        # 100% of teams", but it is only on count/sample of them (audit 2026-06-25). `share`
        # (count/sample) carries the honest figure; MD/consumers render fragmented distinctly.
        g["coverage"] = None
        g["species_sample"] = sample
        g["species_sample_total"] = species_total
        g["item_filter"] = item
        g["fragmented"] = True
        g["note"] = (f"real-team: no single item/ability archetype reaches the sample bar ({sample} "
                     f"teams across {len(clusters)} item/ability combos, {pool_label}, {fmt}); global modal"
                     f" shown — treat as fragmented. " + g["note"])
        out = [g]
    return out


def representative_set(species: str, fmt: str, *, season: str | None = None,
                       min_sample: int = MIN_SAMPLE) -> dict[str, Any] | None:
    """Load the format's library (one season when given) and return the modal joint set, or None."""
    return representative_set_from_teams(
        species, fmt, cached_teams(fmt, season), min_sample=min_sample)


def representative_sets(species: str, fmt: str, *, season: str | None = None,
                        min_sample: int = MIN_SAMPLE,
                        max_clusters: int = MAX_CLUSTERS,
                        item: str | None = None) -> list[dict[str, Any]]:
    """Load the format's library (one season when given) and return up to `max_clusters` archetypes."""
    return representative_sets_from_teams(
        species, fmt, cached_teams(fmt, season), min_sample=min_sample, max_clusters=max_clusters,
        item=item)


def dominant_form_from_teams(species: str, fmt: str, teams: list[dict[str, Any]], *,
                             min_sample: int = MIN_SAMPLE) -> str:
    """The library form a meta BASE name is actually RUN as. Pure / injectable for tests.

    Some library partitions store a Mega as the `Mega X` species, while the meta usage ranking can rank
    it under the BASE name — so a base name like 'Staraptor' would miss real teams stored under
    'Mega Staraptor'. Return the base name UNLESS a `Mega <base>` form is real-team-backed
    (>= min_sample) AND at least as common as the base — then that Mega is the form the opponent runs.
    Partitions that store Mega via base species + stone have no `Mega X` key and naturally return the
    base."""
    counts: Counter = Counter()
    for t in teams:
        thi = _team_has_item(t)
        for m in t.get("pokemon", []):
            if _template_eligible_member(m, team_has_item=thi):
                counts[m.get("species")] += 1
    base_n = counts.get(species, 0)
    megas = [(sp, c) for sp, c in counts.items()
             if isinstance(sp, str) and sp.startswith("Mega ") and base_of_form_name(sp) == species]
    if megas:
        best_form, best_c = max(megas, key=lambda kv: kv[1])
        if best_c >= min_sample and best_c >= base_n:
            return best_form
    return species


def dominant_form(species: str, fmt: str, *, season: str | None = None,
                  min_sample: int = MIN_SAMPLE,
                  teams: list[dict[str, Any]] | None = None) -> str:
    """Load the format's library (or use the passed `teams`) and resolve a meta base name to the form it
    is actually run as (see `dominant_form_from_teams`)."""
    teams = cached_teams(fmt, season) if teams is None else teams
    return dominant_form_from_teams(species, fmt, teams, min_sample=min_sample)
