#!/usr/bin/env python
"""L3 candidate retrieval — `fill`.

Fill a STRUCTURED gap with a candidate POOL, presented in MULTIPLE EXPLICIT ranking VIEWS. The hard
rule: the skill NEVER emits a composite strength score or a single "best pick". It returns
objective facts + several orderings; the AI weighs and chooses.

Pool = the meta usage list (the viable species), filtered to those that ADDRESS the `need` and respect
owned/avoid/already-on-team. Each candidate carries WHY it matches (resist multiplier / STAB type /
coverage move of a type / role moves it learns / reachable Speed) + the per-view positions:
  - usage              : meta usage rank (the published "most-used" order)
  - co_occurrence      : real-team library teams pairing it with the LOCKED core (synergy fact)
  - tournament_sample  : real-team library appearances (how attested it is)
  - owned              : whether it's in the owned roster (and an owned-only view)

Everything is injectable (dex_fn / ranking_fn / repset_fn) for hermetic tests. No network, no state.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cliffs import champ_speed, SP_CAP  # noqa: E402
from typechart import effectiveness, effectiveness_for_member  # noqa: E402
from diagnose import _ROLE_MOVES, _ROLE_LABELS  # noqa: E402  (reuse the functional-move taxonomy)
import repset  # noqa: E402
import team_i18n as i18n  # noqa: E402
import query_identity
import teamid
from stat_keys import canonical_spread
from mega import form_base_species

POOL_K = 60                       # how deep into the usage ranking we draw the candidate pool from


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _as_list(x: Any) -> list[str]:
    if x is None:
        return []
    return [x] if isinstance(x, str) else list(x)


def _resist_detail(types: list[str], abilities: list[str], atk_type: str) -> dict[str, Any] | None:
    """How (and whether) this species resists `atk_type`, with the BASIS disclosed (audit 2026-06-24):
      - basis="type"    : the type chart already resists (always-on, stable);
      - basis="ability" : only a specific ability gets it under 1.0 (conditional on running THAT ability).
    Returns None if it doesn't resist by any means. We report the basis + which ability so an
    ability-only resist isn't misread as a stable one."""
    type_mult = effectiveness(atk_type, types)
    best, via = type_mult, None
    for ab in (abilities or []):
        m = effectiveness_for_member(types, ab, atk_type)
        if m < best:
            best, via = m, ab
    if best >= 1.0:
        return None
    if type_mult < 1.0:                                  # stable: typing alone resists
        return {"mult": type_mult, "basis": "type", "ability": None}
    return {"mult": best, "basis": "ability", "ability": via}   # only via this ability


def _addresses(need: dict[str, Any], fact: dict[str, Any],
               coverage: dict[str, list[str]] | None = None) -> dict[str, Any] | None:
    """How this candidate addresses each requested need criterion, or None if it fails ANY (AND).
    Objective facts only — never a score. `coverage` (when supplied) maps a dex move-type -> the
    candidate's learnset DAMAGING moves of that type, the bridge that makes `coverage_move_type` real."""
    types = fact.get("types") or []
    abilities = fact.get("abilities") or []
    learn = {_norm(m) for m in (fact.get("moves") or [])}
    base_spe = (fact.get("stats") or {}).get("spe")
    out: dict[str, Any] = {}

    for t in _as_list(need.get("resist")):                       # wants a resist/immunity to type t
        detail = _resist_detail(types, abilities, t)
        if detail is None:
            return None
        out.setdefault("resist", {})[t] = detail

    for t in _as_list(need.get("offense_type")):                 # wants a STAB type t (typing proxy)
        if t not in types:
            return None
        out.setdefault("offense_type", {})[t] = i18n.Msg("fill_stab_proxy")

    # coverage_move_type: wants an ACTUAL damaging move of type t in the learnset (the real coverage
    # check the STAB proxy can't do — e.g. a non-Ice mon that learns Ice Beam). `coverage` is resolved
    # via the dex move->type bridge in `fill()`; matched case-insensitively against the requested type.
    for t in _as_list(need.get("coverage_move_type")):
        hit = next(((ctype, mvs) for ctype, mvs in (coverage or {}).items()
                    if ctype.lower() == t.lower() and mvs), None)
        if hit is None:
            return None
        ctype, mvs = hit
        # STAB vs non-STAB is the value distinction the bare move list hides: a STAB hit is worth FAR
        # more than the nominal 1.5x over a non-STAB coverage move (a core dimension — never
        # collapsed to 1.5x), since non-STAB coverage costs a moveslot and only helps vs specific targets.
        # We surface the FACT (stab bool); the AI weighs it (the tool never scores).
        out.setdefault("coverage_move_type", {})[ctype] = {
            "moves": sorted(mvs),
            "stab": any(ctype.lower() == ot.lower() for ot in types),
        }

    for role in _as_list(need.get("role")):                      # wants a functional role
        hits = sorted(m for m in (_ROLE_MOVES.get(role) or set()) if m in learn)
        if not hits:
            return None
        out.setdefault("role", {})[role] = {"label": _ROLE_LABELS.get(role, role), "moves": hits}

    ms = need.get("min_speed")
    if ms is not None:
        if base_spe is None:
            return None
        reach = champ_speed(base_spe, SP_CAP, "Jolly")           # max-Spe +nature line
        if reach < int(ms):
            return None
        out["min_speed"] = {"target": int(ms), "max_reachable": reach}

    return out


def fill(team: dict[str, Any], need: dict[str, Any], *, fmt: str | None = None,
         dex_fn: Callable[[list[str]], dict[str, dict]],
         ranking_fn: Callable[[str | None, int], list[dict]],
         repset_fn: Callable[..., list[dict]] | None = None,
         move_fn: Callable[[list[str]], dict[str, dict]] | None = None,
         item_fn: Callable | None = None,
         season: str | None = None, rule: str | None = None,
         owned: list[str] | None = None, owned_only: bool = False,
         avoid: list[str] | None = None, locked: list[str] | None = None,
         avoid_items: list[str] | None = None,
         meta_conformance: str | None = None,
         answer_cache: dict | None = None,
         defer_min_speed: bool = False,
         pool_k: int = POOL_K) -> dict[str, Any]:
    """Build the candidate pool for `need` and present it in multiple explicit views (no score)."""
    fmt = (fmt or team.get("format") or "single").lower()
    if not need:
        return {"kind": "fill", "format": fmt, "need": need, "candidates": [], "views": {},
                "notes": ["no `need` given — nothing to fill"], "confidence": "low"}

    # Slot replacements check full observed sets in fill_trials, including held-item speed.
    # A species' unmodified speed ceiling must not discard those sets before that check.
    match_need = {k: v for k, v in need.items() if not (defer_min_speed and k == "min_speed")}

    avoid_n = {_norm(a) for a in (avoid or [])}
    owned_n = {_norm(o) for o in (owned or [])}

    # `owned_only` is a HARD constraint (project rules): with no owned roster we CANNOT fill
    # from it, so return empty + a warning rather than silently dropping the restriction (audit
    # 2026-06-24 — the old `and owned_n` short-circuit degraded owned_only to an unrestricted pool).
    if owned_only and not owned_n:
        return {"kind": "fill", "format": fmt, "need": need, "candidates": [], "views": {},
                "pool_considered": 0, "candidate_count": 0,
                "notes": ["owned_only is set but no owned roster was provided — cannot fill from owned. "
                          "The AI must resolve the user's owned Pokemon (input in any format) into "
                          "build-context.owned."],
                "confidence": "low", "confidence_reason": "owned-roster-missing"}

    # Usage ranking — for ranks (a view/fact). When owned_only, the candidate POOL is the OWNED roster
    # itself (not meta∩owned): an owned, gap-filling mon outside the usage top-K must NOT vanish (audit
    # 2026-06-24). Otherwise the pool is the usage top-K (the viable field).
    rows = ranking_fn(fmt, pool_k) or []
    rank_of: dict[str, Any] = {}
    for row in rows:
        nm = row.get("pokemon_en") or row.get("pokemon") or row.get("slug")
        if nm:
            rank_of.setdefault(nm, row.get("rank"))

    pool = list(owned) if owned_only else [r.get("pokemon_en") or r.get("pokemon") or r.get("slug")
                                           for r in rows]
    teams = []
    if repset_fn is not None:
        try:
            teams = (repset.load_teams_for_rule(fmt, rule) if rule and repset_fn is repset.load_teams
                     else repset_fn(fmt, season=season)) or []
        except (OSError, ValueError):
            teams = []
    elif rule or season:
        try:
            teams = repset.load_teams_for_rule(fmt, rule) if rule else repset.load_teams(fmt, season=season)
        except (OSError, ValueError):
            teams = []
    if meta_conformance == "off_meta" and not owned_only:
        # The library supplies evidence for the tail; never fabricate a pool entry from dex alone.
        pool = list(dict.fromkeys([*pool, *(m["species"] for t in teams for m in t.get("pokemon") or []
                                          if m.get("species"))]))
    raw_teams = teams  # Witness ids bind published registration facts, before battle-form projection.
    view, identities = query_identity.battle_view(
        [team, *teams], [{"species": n} for n in [*pool, *(owned or []), *(locked or [])] if n],
        dex_fn=dex_fn, item_fn=item_fn or (lambda _: {}))
    battle_team, teams = view[0], view[1:]
    facts = identities.facts
    origin = {nm: nm for nm in pool if nm}
    if owned_only:
        for nm in list(origin):
            base = form_base_species(nm, facts.get(nm))
            if base:
                origin.setdefault(base, nm)
        missing = sorted(set(origin) - facts.keys())
        if missing:
            facts.update(dex_fn(missing) or {})
    # A usage/owned pool entry supplies its dex-declared Mega options. Each option is a separate
    # factual candidate; the parent's aggregate usage rank is never relabelled as a Mega rank.
    for nm in list(origin):
        for form in (facts.get(nm) or {}).get("mega_forms") or []:
            if form.get("name"):
                origin.setdefault(form["name"], origin[nm])
    missing = sorted(set(origin) - facts.keys())
    if missing:
        facts.update(dex_fn(missing) or {})
    def group(nm):
        return _norm(form_base_species(nm, facts.get(nm)) or nm)
    on_team = {group(m.get("species")) for m in team.get("pokemon", []) if m.get("species")}
    owned_groups = {group(nm) for nm in owned or []}
    names = [nm for nm in origin if group(nm) not in on_team and _norm(nm) not in avoid_n]
    core = [s for s in (locked or [m.get("species") for m in battle_team.get("pokemon", [])]) if s]

    # coverage_move_type bridge: resolve each candidate's learnset move NAMES to dex (type, category)
    # ONCE (one batch lookup over the union), then keep only DAMAGING moves -> {dex_type: [moves]} per
    # species. This is what lets the need match a real coverage move, not just the candidate's STAB.
    wants_coverage = bool(_as_list(need.get("coverage_move_type")))
    cov_by_species: dict[str, dict[str, list[str]]] = {}
    if wants_coverage:
        if move_fn is None:
            return {"kind": "fill", "format": fmt, "need": need, "candidates": [], "views": {},
                    "pool_considered": len(names), "candidate_count": 0,
                    "notes": ["coverage_move_type needs the dex move->type bridge (move_fn). cmd_fill "
                              "wires it; an injected fill() must pass move_fn to use this criterion."],
                    "confidence": "low", "confidence_reason": "move-bridge-missing"}
        all_moves = sorted({mv for nm in names for mv in ((facts.get(nm) or {}).get("moves") or [])})
        move_facts = move_fn(all_moves) or {}
        for nm in names:
            cov: dict[str, list[str]] = {}
            for mv in ((facts.get(nm) or {}).get("moves") or []):
                mf = move_facts.get(mv) or {}
                if mf.get("category") in ("Physical", "Special") and mf.get("type"):
                    cov.setdefault(mf["type"], []).append(mv)
            cov_by_species[_norm(nm)] = cov

    core_n = {_norm(c) for c in core}

    def _counts(species: str) -> tuple[int, int]:
        sample = co = 0
        sp = _norm(species)
        for t in teams:
            members = {_norm(m.get("species")) for m in t.get("pokemon", []) if m.get("species")}
            if sp in members:
                sample += 1
                if core_n and core_n <= members:           # contains the WHOLE locked core too
                    co += 1
        return sample, co

    # Cluster only within the selected actual form, including its deterministic ability projection.
    _arches_by_species: dict[str, list[dict[str, Any]]] = {}
    if teams:
        for _nm in names:
            try:
                _arches = repset.representative_sets_from_teams(_nm, fmt, teams) or []
                _arches_by_species[_nm] = _arches
            except Exception:
                _arches_by_species[_nm] = []
                continue

    def _builds(species: str) -> list[dict[str, Any]]:
        """The candidate's REAL (item,ability) builds, from the same team pool `_counts` scans.

        A candidate is not a species, it is a species running a particular build: recommending
        "add Staraptor" is not actionable when its Scarf build (Speed 250, no Mega slot) and its
        stone build (Speed 178, spends the team's Mega slot) are different answers to different
        problems. Reads the pool already in memory, so this costs no extra IO and no damage calc —
        whether a build actually CLOSES a given hole still needs a matchup battery and is
        deliberately not claimed here.
        """
        if not teams:
            return []
        arches = _arches_by_species.get(species, [])
        out = []
        for a in arches:
            item = a.get("item")
            mega_form = species if (facts.get(species) or {}).get("is_mega") else None
            spe_sp = int(canonical_spread(a.get("sps") or {}).get("spe") or 0)
            # The build's OWN speed, not the species' theoretical ceiling: a 0-Speed bulky build and
            # a max-Speed Scarf build of one species sit nowhere near each other.
            b_base = (((facts.get(species) or {}).get("stats") or {}).get("spe"))
            out.append({
                "item": item, "ability": a.get("ability"), "nature": a.get("nature"),
                "moves": a.get("moves"), "spread_origin": a.get("spread_origin"),
                "sps": a.get("sps"), "coverage": a.get("coverage"),
                "confidence": a.get("confidence"),
                **{k: a[k] for k in ("configuration_basis", "registration_modes", "registration_sample") if k in a},
                "speed": champ_speed(b_base, spe_sp, a.get("nature")) if b_base is not None else None,
                # Spends the team-level Mega resource (few stones registered, one evolution per
                # battle), so this build is not a free pick the way an item swap is. The
                # registration slate stays the authority on whether the lane is conformant.
                "occupies_mega_slot": bool(mega_form),
                **({"run_form": mega_form} if mega_form else {}),
            })
        out.sort(key=lambda b: -(b.get("coverage") or 0))
        return out

    candidates: list[dict[str, Any]] = []
    for nm in names:
        fact = facts.get(nm) or {}
        if not fact or (not fact.get("found", True) and "types" not in fact):
            continue
        addresses = _addresses(match_need, fact, cov_by_species.get(_norm(nm)))
        if addresses is None:
            continue
        sample, co = _counts(nm)
        base_spe = (fact.get("stats") or {}).get("spe")
        candidates.append({
            "species": nm,
            "types": fact.get("types") or [],
            "max_speed": champ_speed(base_spe, SP_CAP, "Jolly") if base_spe is not None else None,
            "addresses": addresses,
            "usage_rank": rank_of.get(nm),
            "pool_source": {"species": origin[nm], "usage_rank": rank_of.get(origin[nm]),
                            "form_expanded": origin[nm] != nm},
            "co_occurrence": co,
            "tournament_sample": sample,
            "owned": group(nm) in owned_groups if owned_groups else None,
            **({"builds": builds} if (builds := _builds(nm)) else {}),
        })
        if rank_of.get(origin[nm]) is None and sample:
            witnesses = [teamid.compute_id(raw) for raw, t in zip(raw_teams, teams)
                         if any(_norm(m.get("species")) == _norm(nm) for m in t.get("pokemon") or [])]
            candidates[-1]["tail_evidence"] = {"sample": sample, "team_ids": witnesses[:3],
                                              "truncated": len(witnesses) > 3,
                                              "basis": "real-team appearances, not usage rank or competitive strength"}

    # Explicit, SEPARATE orderings — never merged into one score. usage = common-first; off_meta =
    # rare-first (a null usage_rank is the MOST off-meta and sorts first) — the meta_conformance knob
    # picks which is the DEFAULT list order, mirroring landscape's observed_cores. An ordering, never a score.
    def _usage_key(c):    return (c["usage_rank"] is None, c["usage_rank"] or 1e9)
    def _off_meta_key(c): return (c["usage_rank"] is not None, -(c["usage_rank"] or 0))
    by_usage = [c["species"] for c in sorted(candidates, key=_usage_key)]
    by_off_meta = [c["species"] for c in sorted(candidates, key=_off_meta_key)]
    by_cooc = [c["species"] for c in sorted(candidates, key=lambda c: -c["co_occurrence"]) if c["co_occurrence"]]
    by_sample = [c["species"] for c in sorted(candidates, key=lambda c: -c["tournament_sample"]) if c["tournament_sample"]]
    owned_view = [c["species"] for c in candidates if c.get("owned")]

    off_meta = (meta_conformance == "off_meta")
    candidates.sort(key=(_off_meta_key if off_meta else _usage_key))   # default order mirrors the posture knob (a VIEW, not advice)

    notes = [
        "Candidate pool = usage/owned species plus their dex-declared Mega options, filtered to the need. "
        "Counts, sets and avoid constraints use exact actual forms; on-team and owned checks use Species Clause groups.",
        "Multiple EXPLICIT views (usage / off_meta / co_occurrence / tournament_sample / owned) — NO "
        "composite score and NO single best pick. Default list order follows meta_conformance "
        "(proven/unset=usage common-first, off_meta=rare-first) — an ordering fact, not a ranking.",
        "offense_type is a STAB/typing proxy (the candidate's OWN type). coverage_move_type is the real "
        "check: it lists the actual learnset DAMAGING moves of the wanted type (dex move->type bridge), "
        "so a non-STAB coverage move (e.g. Ice Beam on a non-Ice mon) counts — each match flags `stab`. "
        "STAB is worth FAR more than the nominal 1.5x over non-STAB coverage (a core dimension, never "
        "collapsed to 1.5x): non-STAB coverage costs a moveslot and only helps vs specific targets. "
        "Fact only — weigh STAB vs non-STAB accordingly. resist discloses its basis: 'type' (stable) "
        "vs 'ability' (only if it runs that ability).",
    ]
    if off_meta:
        notes.append("meta_conformance=off_meta: default order is rare-first (least-used viable first; "
                     "candidates outside the usage pool sort first). The `usage` view still gives common-first.")
    if owned_only:
        notes.append("owned_only: the pool IS the owned roster (filtered to the need); usage rank is "
                     "just a fact-label and may be null for off-meta owned mons.")
    if not teams:
        notes.append("co_occurrence / tournament_sample are 0 (no real-team library for this format/season).")
    if avoid_items:
        # Disclosure lives in the OPERATOR (not the CLI shell) so every caller surfaces it. An
        # avoid entry recognized as an ITEM is enforced by observed (exclusion) and slate
        # (elimination); fill's candidates are SPECIES, so item avoidance is structurally n/a
        # HERE — the composing AI honors it when writing sets, and the run must SAY so.
        notes.append("avoid ITEMS are not applicable to fill's SPECIES-level candidates (observed "
                     "excludes / slate eliminates teams holding them) — honor them when composing "
                     f"sets for these candidates: {', '.join(avoid_items)}")

    confidence = "low" if not facts else "medium"
    out_extra = {"avoid_items_not_enforced": avoid_items} if avoid_items else {}
    output = {
        "kind": "fill", "format": fmt, "need": need,
        "identity_basis": "dex_battle_form", "identity_gaps": identities.gaps,
        "pool_considered": len(names), "candidate_count": len(candidates),
        "candidates": candidates,
        "views": {"usage": by_usage, "off_meta": by_off_meta, "co_occurrence": by_cooc,
                  "tournament_sample": by_sample, "owned": owned_view},
        "notes": notes,
        **out_extra,
        "confidence": confidence, "confidence_reason": "vs-usage-pool",
        "evidence": {"facts": [{"source": "meta", "ref": "usage ranking (candidate pool + usage view)"},
                               {"source": "dex", "ref": "types / stats / abilities / learnset (need match)"},
                               {"source": "real-team", "ref": "co-occurrence + sample (when library present)"}]},
    }
    if need.get("answer"):
        import fill_answers
        if answer_cache is None:
            raise ValueError("answer requires the current validated opponent cache")
        other = {k: v for k, v in match_need.items() if k != "answer"}
        def build_matches(member):
            fact = {**(facts.get(member["species"]) or {}), "abilities": [member["ability"]], "moves": member["moves"]}
            coverage = {}
            if other.get("coverage_move_type") and move_fn is not None:
                for name, move in (move_fn(member["moves"]) or {}).items():
                    if move.get("category") in ("Physical", "Special") and move.get("type"):
                        coverage.setdefault(move["type"], []).append(name)
            return _addresses(other, fact, coverage)
        output["notes"] = [n for n in output["notes"] if not n.startswith("avoid ITEMS")]
        return fill_answers.attach(output, need, answer_cache, fmt,
            excluded_items=[*(avoid_items or []), *(m.get("item") for m in team.get("pokemon", []) if m.get("item"))],
            build_matches=build_matches)
    return output


def format_fill_md(d: dict[str, Any]) -> str:
    need = ", ".join(f"{k}={v}" for k, v in (d.get("need") or {}).items()) or "(none)"
    lines = ["# " + i18n.t("fill_header", need=need, fmt=d["format"])]
    if not d.get("candidates"):
        lines.append("\n_" + i18n.t("fill_no_candidates") + "_")
        return "\n".join(lines + ["", *(f"- {n}" for n in d.get("notes", []))])
    lines.append("\n" + i18n.t("fill_summary", n=d["candidate_count"], pool=d.get("pool_considered")))
    for c in d["candidates"]:
        bits = []
        a = c["addresses"]
        if a.get("resist"):
            bits.append(i18n.t("fill_resists") + " " + ", ".join(
                f"{t} {d['mult']:g}x ({i18n.t('fill_via') + ' ' + d['ability'] if d['basis'] == 'ability' else i18n.t('fill_typing')})"
                for t, d in a["resist"].items()))
        if a.get("offense_type"):
            bits.append("STAB " + "/".join(a["offense_type"]))
        if a.get("coverage_move_type"):
            bits.append(i18n.t("fill_coverage") + " " + ", ".join(
                f"{t} {'STAB' if d['stab'] else 'non-STAB'} ({', '.join(d['moves'])})"
                for t, d in a["coverage_move_type"].items()))
        if a.get("role"):
            bits.append("; ".join(f"{v['label']} ({', '.join(v['moves'])})" for v in a["role"].values()))
        if a.get("min_speed"):
            bits.append(f"max Spe {a['min_speed']['max_reachable']} ≥ {a['min_speed']['target']}")
        own = " [" + i18n.t("fill_owned") + "]" if c.get("owned") else ""
        lines.append(f"- **{c['species']}** ({'/'.join(c['types']) or '?'}){own}: " + "; ".join(bits)
                     + f"  — {i18n.t('fill_usage')} #{c['usage_rank']}, {i18n.t('fill_cooc')} {c['co_occurrence']}, {i18n.t('fill_sample')} {c['tournament_sample']}")
    lines += ["", i18n.t("fill_views") + ":"]
    for v, order in d["views"].items():
        if order:
            lines.append(f"- {v}: {', '.join(order[:8])}")
    lines += ["", *(f"> {n}" for n in d.get("notes", []))]
    return "\n".join(lines)
