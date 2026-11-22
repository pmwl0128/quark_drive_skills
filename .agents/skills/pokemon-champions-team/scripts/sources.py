#!/usr/bin/env python
"""Provenance-aware opponent-set resolver: real-team JOINT set first, meta marginals as fallback.

The accuracy ranking is: a REAL team's joint {ability,item,nature,moves} beats meta's independent
marginals (no §10 stitch) and can be skill-segmented. Some real-team rows carry NO spread, so for
those the SP/spread still comes from meta; rows that DO expose a spread supply a real co-occurring
spread. So the resolved opponent set is a per-field MERGE:

    ability / item / nature / moves  <- real-team representative set (when sample is sufficient)
    sps (spread)                     <- real-team modal spread when the raw row carries one (co-occurs
                                        with the set, but its
                                        confidence folds the spread's OWN thinner sample); else meta
                                        modal
    fallback (no real-team data)     <- meta modal set as-is (today's behaviour)
    fallback (no meta either)        <- None (caller's synthetic max-offense path handles it)

Every result carries its per-field provenance + confidence; this never emits a strength score, and
single/double are never mixed. Real-team reads are either an exact season partition or a same-rule pool.

Consumers (matchup / tune) inject `resolve_opponent_set` instead of calling metalink directly, so
they upgrade automatically once the real-team library is populated — with an empty library the
result is byte-identical to the meta path, which is exactly how the no-real-data case stays stable.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repset  # noqa: E402
import evidence  # noqa: E402
from mega import base_of_form_name  # noqa: E402
from metalink import canonical_attacker_set, canonical_attacker_sets  # noqa: E402


# --- Mega run-form resolution ------------------------------------------------------------------
# The library stores a registered Mega in EITHER of two shapes, and both must resolve to the form
# that actually battles:
#   1. as the Mega species itself ('Mega Staraptor')  -- repset.dominant_form_from_teams handles it
#   2. as the BASE species holding its stone ('Staraptor' + 'Staraptite')  -- resolved here
# Shape 2 is how every DOUBLES partition is written (M-4 doubles: 2283 rows across 67 Mega forms,
# zero explicit 'Mega X' rows), so before this resolver every doubles Mega was priced with base-form
# stats, types and ability -- e.g. Staraptor stayed Normal/Flying 120 Atk instead of Fighting/Flying
# 140 Atk, turning a guaranteed OHKO on Kingambit into a coin-flip. The 2026-06-25 audit fixed only
# shape 1 (singles), which is why the doubles half of the matrix kept reading wrong.
_MEGA_MEMO: dict[tuple[str, str], str | None] = {}


def _default_item_fn(names: list[str]) -> dict[str, dict[str, Any]]:
    from dexlink import lookup_items          # local: keeps sources importable without the dex
    return lookup_items(names)


def _default_dex_fn(names: list[str]) -> dict[str, dict[str, Any]]:
    from dexlink import lookup_pokemon
    return lookup_pokemon(names)


def mega_run_form(species: str, item: str | None, *,
                  item_fn: Callable[[list[str]], dict] | None = None) -> str | None:
    """The Mega form `species` battles as while holding `item`, or None.

    The dex `required_by` mapping is authoritative (it distinguishes X/Y stones and rejects a stone
    belonging to another species). Memoized per (species, item) — an opponent battery resolves the
    same handful of stones repeatedly."""
    if not species or not item:
        return None
    key = (species, item)
    if key in _MEGA_MEMO:
        return _MEGA_MEMO[key]
    fn = item_fn or _default_item_fn
    form = None
    try:
        info = (fn([item]) or {}).get(item) or {}
        for candidate in info.get("required_by") or []:
            if base_of_form_name(candidate) == species:
                form = candidate
                break
    except Exception:
        form = None                            # dex unavailable -> no remap, never a guess
    _MEGA_MEMO[key] = form
    return form


def mega_ability(form: str, *, dex_fn: Callable[[list[str]], dict] | None = None) -> str | None:
    """The Mega form's own ability. A real-team row records the ability the mon holds BEFORE it Mega
    evolves (Staraptor's Intimidate), which is illegal on the Mega and would price the wrong
    ability-driven effects — the form's dex ability replaces it once the run form is known."""
    try:
        fn = dex_fn or _default_dex_fn
        abilities = ((fn([form]) or {}).get(form) or {}).get("abilities") or []
        first = abilities[0] if abilities else None
        return first.get("name") if isinstance(first, dict) else first
    except Exception:
        return None


def _cap(conf: str, ceiling: str) -> str:
    """The lower of two confidence levels."""
    return evidence.floor_confidence(conf, ceiling)


def _merge_set(species: str, rep: dict | None, meta: dict | None,
               run_form: str | None = None) -> dict[str, Any] | None:
    """Per-field merge: real-team JOINT {ability,item,nature,moves} + a SPREAD whose origin depends on
    whether the representative row carries one. A real co-occurring spread (rep['sps']) is used as-is;
    otherwise the spread is the meta modal (a marginal stitched on). Shared by the single and batch
    resolvers. With no real-team data -> meta as-is (byte-identical to the
    pre-resolver behaviour, so the system stays stable on an empty library)."""
    if not rep:
        return meta

    rep_sps = rep.get("sps") or {}
    if rep_sps:
        # The spread is part of the SAME real joint set (it co-occurs with the modal ability/item/
        # nature/moves), so a real spread is ALWAYS used when one exists — never swapped for a meta
        # marginal, no threshold fallback; all present, legal spreads are treated uniformly. But it has
        # its OWN, usually much thinner sample (repset's spread_count/spread_sample — only some of the
        # modal-set teams carry a spread, and exact SP lines fragment heavily), so the merged confidence
        # folds repset's spread_confidence: a 4/29 spread can't ride the set's 336/594 high (audit
        # 2026-07-02). The set-field confidence stays visible in provenance.set_fields.
        sps = rep_sps
        spread_conf = rep.get("spread_confidence") or rep["confidence"]
        conf = _cap(rep["confidence"], spread_conf)
        spread_origin = rep.get("spread_origin") or "real-team"
        source = "real-team"
        sc, ss = rep.get("spread_count"), rep.get("spread_sample")
        shape, shc = rep.get("spread_shape"), rep.get("spread_shape_count")
        if shape is not None and shc:
            # Two-level counts: the shape cluster (the confidence claim — "invests in this direction")
            # and the exact representative line within it.
            basis = (f"shape {repset.shape_label(shape)} {shc}/{ss} spread-carrying teams, "
                     f"exact line {sc}/{shc}")
        elif sc:
            basis = f"{sc}/{ss} spread-carrying teams of the set"
        else:
            basis = f"{rep['count']}/{rep['sample']}"
        spread_prov = f"real-team modal joint (co-occurs with the set; {basis}; {spread_conf})"
        spread_note = (" | spread from the same real-team joint set (co-occurs; confidence folds the "
                       "spread's own shape sample)")
    else:
        # No real spread: the rep fields are a real joint object, but the SPREAD is
        # only a meta MARGINAL stitched on — not verified to co-occur with that real set. So cap the
        # whole set's confidence at `medium` when the spread is meta-derived (a high-sample real core
        # doesn't make the stitched spread certain; audit 2026-06-24). No spread at all -> low.
        sps = (meta or {}).get("sps") or {}
        has_meta_spread = bool(sps)
        conf = _cap(rep["confidence"], "medium") if has_meta_spread else "low"
        spread_conf = None
        spread_origin = "usage" if has_meta_spread else None
        source = "real-team+meta-spread" if has_meta_spread else "real-team (no spread)"
        spread_prov = ("meta modal (marginal — not verified to co-occur)" if has_meta_spread
                       else "unavailable (no meta spread)")
        spread_note = (" | spread from meta modal (marginal stitch — confidence capped)" if has_meta_spread
                       else " | no meta spread available — spread unknown")
    # them->us THREAT surface must stay BROAD: the real-team JOINT set is ONE build, so using its 4
    # moves as the threat list would MISS moves other variants run (research 2026-06-24 — real-team
    # marginals ~= meta's, but a single joint set under-covers the threat space; defense stays
    # conservative). Keep the meta >=15% damaging surface and union in any real-team joint move not
    # already there. `moves` = what the modal set RUNS (joint); `threat_moves` = what it can HIT with.
    meta_moves = [m for m in ((meta or {}).get("moves") or []) if isinstance(m, dict)]
    meta_move_names = {m.get("name") for m in meta_moves}
    threat_moves = list(meta_moves) + [{"name": mv, "pct": None}
                                       for mv in (rep["moves"] or []) if mv not in meta_move_names]
    # A run form implies the Mega's OWN ability: a base+stone library row records what the mon holds
    # before it Mega evolves (Staraptor's Intimidate), which is illegal on the Mega and would price
    # the wrong ability-driven effects. Only override when the dex actually answers.
    effective_form = run_form if (run_form and run_form != species) else None
    # Doubles registers a Mega as BASE species + stone, so the real-team row's ability is the
    # pre-Mega ability that actually applies on switch-in.  Once `ability` below is rewritten to the
    # Mega form's battle ability, preserve that joint base ability explicitly.  Singles commonly
    # stores the Mega form itself; in that shape the row ability is already the run-form ability and
    # must not be mislabeled as a pre-Mega fact.
    rep_form = rep.get("_form") or rep.get("species")
    base_ability = rep.get("ability") if effective_form and rep_form == species else None
    ability = rep["ability"]
    if effective_form:
        ability = mega_ability(effective_form) or ability
    out = {
        "species": species,
        # The form actually RUN, when it differs from the meta label. Two library shapes reach this:
        # singles rank a Mega under the base name while storing 'Mega Staraptor' (audit 2026-06-25),
        # and doubles store the base species holding its stone (resolved via `mega_run_form`).
        # Consumers must use this form's stats/types. None when there is no remap.
        "run_form": effective_form,
        "ability": ability, "item": rep["item"], "nature": rep["nature"],
        "moves": rep["moves"],                        # the real co-occurring set (what it RUNS)
        "threat_moves": threat_moves,                 # broad meta surface U joint (what it can HIT with)
        "sps": sps,                                   # real co-occurring spread or meta modal
        "confidence": conf,
        # Pass the meta modal marginals through so downstream (tune's anti-Intimidate / Choice Scarf
        # checks) can read the ability usage % and item ranks even when the merged set is real-team.
        "set": (meta or {}).get("set"),
        "choice_scarf": (meta or {}).get("choice_scarf"),
        "prevalence": (meta or {}).get("prevalence", 0.5),
        "prevalence_basis": (meta or {}).get("prevalence_basis", "real-team set (no meta usage)"),
        "source": source,
        "provenance": {
            "set_fields": f"real-team modal joint {rep['count']}/{rep['sample']} "
                          f"(share {rep.get('share')}, {rep['confidence']})",
            "spread": spread_prov,
            "spread_origin": spread_origin,
            # The spread's OWN sample confidence (real-team spreads only; None for a meta stitch —
            # the medium cap on `confidence` already carries that case).
            "spread_confidence": spread_conf,
        },
        "note": rep["note"] + spread_note,
    }
    if base_ability:
        out["base_ability"] = base_ability
    return out


def _rep_for(species: str, fmt: str, season: str | None, rule: str | None,
             repset_fn: Callable[..., dict | None] | None,
             teams: list[dict[str, Any]] | None = None) -> tuple[dict | None, str]:
    """(representative set, run_form). On the real path the meta name is first resolved to the form it is
    actually run as (`repset.dominant_form`) — a singles Mega is ranked under the base name but stored as
    'Mega X', so a base name like 'Staraptor' must query 'Mega Staraptor' or it falsely reads as having
    no real data (audit 2026-06-25). An injected repset_fn (tests) controls its own scoping/naming, so
    no remap is applied there. Returns run_form == species when there is no remap."""
    # season=None propagated to repset reads EVERY season's file (cross-regulation pollution). The guard
    # sits on the real-library path only; an injected repset_fn controls its own scoping (audit 2026-06-23).
    if repset_fn is None:
        if not (season or rule):
            raise ValueError("an explicit season is required for the real-team library "
                             "unless a rule pool is supplied (season=None would read every season -> "
                             "cross-regulation pollution)")
        try:
            if teams is None:
                teams = repset.cached_teams_for_rule(fmt, rule) if rule else repset.cached_teams(fmt, season)
            # The LIBRARY KEY and the BATTLE FORM are different questions and must not be conflated:
            # the set is fetched under the name the partition actually stores, then the stone the
            # fetched set holds decides the form it runs as. Remapping before the fetch (the obvious
            # shortcut) would query 'Mega Staraptor' against a doubles partition that only has
            # 'Staraptor' rows and read back as "no real data".
            lib_key = repset.dominant_form_from_teams(species, fmt, teams)
            rep = repset.representative_set_from_teams(lib_key, fmt, teams)
            run_form = lib_key
            if lib_key == species and rep:
                run_form = mega_run_form(species, rep.get("item")) or species
            return rep, run_form
        except (OSError, json.JSONDecodeError):
            return None, species
    else:
        run_form = species
        fn = repset_fn
    # NARROW catch: an absent library already returns []/None without raising (repset.load_teams), so the
    # only expected throws here are a corrupt/unreadable library file — fall back to meta for those. A
    # code bug (KeyError/TypeError/...) MUST propagate, not masquerade as an empty library (audit 2026-06-25).
    try:
        return fn(run_form, fmt, season=season), run_form
    except (OSError, json.JSONDecodeError):
        return None, run_form


def resolve_opponent_set(species: str, fmt: str | None, *, season: str | None = None,
                         rule: str | None = None,
                         repset_fn: Callable[..., dict | None] | None = None,
                         meta_fn: Callable[..., dict | None] = canonical_attacker_set
                         ) -> dict[str, Any] | None:
    """Resolve ONE opponent's set: real-team joint set ⊕ meta spread. repset_fn/meta_fn injectable for
    tests. Returns None only when BOTH sources are empty."""
    if not fmt:
        raise ValueError("resolve_opponent_set requires an explicit format (no single/double default)")
    rep, run_form = _rep_for(species, fmt, season, rule, repset_fn)
    try:
        meta = meta_fn(species, fmt)              # meta ranks under the META name (the base for a Mega)
    except Exception:
        meta = None
    return _merge_set(species, rep, meta, run_form=run_form)


def variant_id(species: str, item: str | None, ability: str | None) -> str:
    """Lossless stable key for one (item, ability) archetype.

    Each component is percent-encoded independently, so spaces inside a field can never collide with
    structural separators (the old slug boundary mapped ``A B|C`` and ``A|B C`` to the same key).
    """
    enc = lambda value: quote(value or "", safe="")
    return f"variant:{enc(species)}|item={enc(item)}|ability={enc(ability)}"


def _variant_rep(arch: dict[str, Any]) -> dict[str, Any]:
    """An (item,ability) archetype in the `rep` shape `_merge_set` consumes (it needs a `note`)."""
    cl = arch.get("cluster") or {}
    note = (f"real-team archetype {cl.get('item') or '-'}/{cl.get('ability') or '-'}: "
            f"{arch.get('count')}/{arch.get('sample')} teams"
            + (" (fragmented — no cluster cleared the sample floor)" if arch.get("fragmented") else ""))
    return {**arch, "note": note}


def resolve_opponent_variants(species: str, fmt: str, *, season: str | None = None,
                              rule: str | None = None,
                              repset_fn: Callable[..., list | None] | None = None,
                              meta: dict[str, Any] | None = None,
                              meta_fn: Callable[..., dict | None] = canonical_attacker_set,
                              teams: list[dict[str, Any]] | None = None,
                              ) -> list[dict[str, Any]]:
    """Every real-team (item,ability) archetype of `species`, each merged into a FULL opponent set.

    This is the plural sibling of `resolve_opponent_set`. The single-modal resolver can only describe
    one build, but a species that genuinely splits — doubles Staraptor runs Staraptite (88% coverage)
    AND Choice Scarf (8%) — has two materially different opponents behind one name: only the first
    Mega-evolves, so they differ in typing, stats, ability and speed tier. Collapsing them to the
    modal made the Scarf build invisible.

    Each variant resolves its OWN `run_form`, because the archetype's ITEM is what decides
    Mega-vs-base. `is_modal` marks the highest-coverage variant (what a caller shows by default) and
    `coverage` is that archetype's share of the species' team pool — surfaced so a 2%-coverage row is
    never read as an equal-weight fact next to an 88% one.

    Returns [] when the species has no real-team read at all (below MIN_SAMPLE); callers fall back to
    `resolve_opponent_set` for the meta-only path.
    """
    if not fmt:
        raise ValueError("resolve_opponent_variants requires an explicit format")
    if repset_fn is not None:
        arches = repset_fn(species, fmt, season=season) or []
    else:
        if not (season or rule):
            raise ValueError("an explicit season or rule is required for the real-team library")
        try:
            if teams is None:
                teams = repset.cached_teams_for_rule(fmt, rule) if rule else repset.cached_teams(fmt, season)
            # Collect across EVERY library form of the species, not just its dominant one.
            # Clustering is per species-NAME, and the singles partition files a registered Mega under
            # its own name ('Mega Greninja'), so querying only the dominant form made the other half
            # of the species invisible: singles Greninja is ~36 base / ~38 Mega, and only the Mega
            # was ever shown. Doubles stores base+stone, where (item,ability) already separates them.
            arches = []
            for form in repset.library_forms(species, teams) or [species]:
                for a in repset.representative_sets_from_teams(form, fmt, teams) or []:
                    arches.append({**a, "_form": form})
            # Coverage arrives denominated on each FORM's own pool, which would read as
            # "100% of Mega Greninja" next to "100% of Greninja". Re-denominate on the combined pool
            # so the shares are comparable and sum to the species.
            #
            # The numerator is the CLUSTER's size, recovered as coverage x that form's sample.
            # `count` is a different number — the exact joint set within the cluster — and using it
            # collapsed every share to a few percent AND inverted the ranking: singles Charizard is
            # 35 Mega-Y vs 17 Mega-X, but their exact-joint counts are 4 and 5, so X sorted ahead
            # of a build twice its size.
            total = 0
            for form in {a["_form"] for a in arches}:
                first = next(a for a in arches if a["_form"] == form)
                total += int(first.get("species_sample") or 0)
            if total > 0:
                for a in arches:
                    cov, samp = a.get("coverage"), a.get("species_sample")
                    if isinstance(cov, (int, float)) and isinstance(samp, int):
                        a["coverage"] = round(cov * samp / total, 4)
        except (OSError, json.JSONDecodeError):
            return []
    if not arches:
        return []
    # Form-local clustering can yield up to MAX_CLUSTERS for EACH form. Once their coverage shares
    # have the common base+Mega denominator, apply the materiality floor and the global ceiling once.
    # This is both the public evidence boundary and the calculation budget boundary.
    arches = [a for a in arches
              if a.get("coverage") is None
              or float(a.get("coverage") or 0) >= repset.MIN_CLUSTER_COVERAGE]
    arches.sort(key=lambda a: (-(a.get("coverage") or 0),
                               str((a.get("cluster") or {}).get("item") or ""),
                               str((a.get("cluster") or {}).get("ability") or "")))
    arches = arches[:repset.MAX_CLUSTERS]
    if not arches:
        return []
    if meta is None:
        try:
            meta = meta_fn(species, fmt)
        except Exception:
            meta = None

    # Archetype shares are rounded independently. Clamp their aggregate to the probability domain
    # so a 100.01% rounding sum cannot leak into CLI or web contracts as 1.0001.
    represented = round(min(1.0, max(
        0.0, sum(float(a.get("coverage") or 0) for a in arches)
    )), 4)
    out: list[dict[str, Any]] = []
    for arch in arches:
        run_form = mega_run_form(species, arch.get("item")) or species
        merged = _merge_set(species, _variant_rep(arch), meta, run_form=run_form)
        if not merged:
            continue
        merged["variant_id"] = variant_id(species, arch.get("item"), arch.get("ability"))
        # The PRE-Mega ability, kept whenever it differs from the battle ability. Two archetypes can
        # share a stone yet differ only here (Staraptite|Intimidate vs Staraptite|Reckless both fight
        # as Contrary) — and that is NOT a duplicate row: the mon enters in its base form, so
        # Intimidate still fires on switch-in before it Mega evolves. Damage is priced on the Mega's
        # ability; this field is what lets a reader tell the two apart.
        if run_form != species and arch.get("ability") != merged.get("ability"):
            merged["base_ability"] = arch.get("ability")
        merged["coverage"] = arch.get("coverage")
        merged["variant_sample"] = arch.get("sample")
        merged["variant_count"] = arch.get("count")
        merged["fragmented"] = bool(arch.get("fragmented"))
        merged["cluster"] = arch.get("cluster")
        merged["cluster_basis"] = "item_ability"
        merged["represented_coverage"] = represented
        merged["unrepresented_coverage"] = (round(max(0.0, 1.0 - represented), 4)
                                               if any(a.get("coverage") is not None for a in arches)
                                               else None)
        merged["speed_profile"] = arch.get("speed_profile")
        out.append(merged)
    # Highest coverage is the default row. `sorted` is stable, so repset's own ordering breaks ties.
    out.sort(key=lambda v: -(v.get("coverage") or 0))
    for i, v in enumerate(out):
        v["is_modal"] = (i == 0)
    return out


def resolve_opponent_sets(species_list: list[str], fmt: str | None, *, season: str | None = None,
                          rule: str | None = None,
                          repset_fn: Callable[..., dict | None] | None = None,
                          meta_batch_fn: Callable[..., dict] = canonical_attacker_sets,
                          teams_for_species: Callable[[str], list[dict[str, Any]]] | None = None,
                          ) -> dict[str, dict | None]:
    """BATCH resolver (matchup's injection point): one batched meta call for ALL species + a per-species
    real-team read, merged per field. Same result as calling resolve_opponent_set per species, but the
    meta side is a single sibling call instead of N (the prerequisite for wiring matchup without
    degrading to N subprocesses — handoff §5.1). Empty real-team library -> byte-identical to the meta
    batch path."""
    if not fmt:
        raise ValueError("resolve_opponent_sets requires an explicit format (no single/double default)")
    names = list(species_list)
    try:
        metas = meta_batch_fn(names, fmt) or {}
    except Exception:
        metas = {}
    def _resolve(sp: str) -> dict | None:
        teams = teams_for_species(sp) if teams_for_species is not None else None
        rep, run_form = _rep_for(sp, fmt, season, rule, repset_fn, teams)
        return _merge_set(sp, rep, metas.get(sp), run_form=run_form)
    return {sp: _resolve(sp) for sp in names}
