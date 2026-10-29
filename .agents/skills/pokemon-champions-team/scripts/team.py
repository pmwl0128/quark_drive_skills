#!/usr/bin/env python
"""Pokemon Champions team CLI (M1).

Commands:
  parse     <file>            Parse team-json or Showdown text -> canonical team-json.
  validate  <file>            Validate a team against Champions rules (uses sibling dex skill).
  diagnose  <file>            [M2] team diagnostics: defense/offense/speed/roles (--aspect; default all).
  select    <file>            [M2/M3] 6v6 selection matrix / candidate fill.

Options:
  --format md|json            md (default) = readable report; json = programmatic.
  --context <file>            build-context JSON (intent/constraints; schema.md §7). Used by validate
                              for owned_only checks; consumed more by M2/M3.

Use --format json for programmatic output.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from team_io import load_team, load_context, team_from_dict, context_from_dict  # noqa: E402
import context_audit  # noqa: E402  (UEP P2: front gate + field_status single source)
import landscape  # noqa: E402  (UEP P4: structural distributions over the real library)
import landscape_cache  # noqa: E402
import team_views  # noqa: E402
import query_identity  # noqa: E402
import nested_names  # noqa: E402
import speed_ladder  # noqa: E402
import counters  # noqa: E402
import ko_scan  # noqa: E402
import scenario  # noqa: E402
import threshold  # noqa: E402
import team_facts  # noqa: E402
import assembly  # noqa: E402
import diversity  # noqa: E402
import corrections  # noqa: E402
from landscape import landscape_from_teams as run_landscape  # noqa: E402
import frame  # noqa: E402  (UEP P4.5: assembly front-door — grounded skeletons before assembly)
from slate import evaluate_slate as run_slate, slate_shape_error, project_slate  # noqa: E402  (UEP P5 gate)
from rules import get_ruleset  # noqa: E402  (registration hard caps surfaced in `schema`)
import rules  # noqa: E402
import answer_audit  # noqa: E402  (UEP P6: the back gate — draft checklist + claim recompute)
import checkpoint as checkpoint_mod  # noqa: E402  (post-slate, pre-tune pause contract)
import team_profile  # noqa: E402  (UEP S: the shared structural vector, slate's cheap stage)
from validate_team import validate, format_report  # noqa: E402
from dexlink import lookup_pokemon, lookup_moves, canonicalize_species, canonicalize_fields, resolve_names, DexUnavailable  # noqa: E402
from matchup import (  # noqa: E402  (P6)
    effective_member, member_actor, set_actor, pair_speed, dmg_fact, run_form_name,
)
from diagnose import (  # noqa: E402
    diagnose_defense, format_defense_md, diagnose_offense, format_offense_md,
    diagnose_speed, format_speed_md, diagnose_roles, format_roles_md,
)
from tune import tune as run_tune, format_tune_md  # noqa: E402
from ncplink import damage_vs, damage_batch, NcpUnavailable, NcpInputError  # noqa: E402
from speedlink import speed_batch  # noqa: E402
from metalink import (  # noqa: E402
    nature_distribution, usage_top_k, MetaUnavailable,   # opponent SETS now resolve via sources (M4)
)
from selection import select as run_select, format_selection_md  # noqa: E402
from matchup import (  # noqa: E402
    matchup as run_matchup, format_matchup_md, format_check_coverage_md, project_matchup,
    normalize_top_k, MATCHUP_TOP_K_MIN, MATCHUP_TOP_K_MAX, MATCHUP_TOP_K_DEFAULT,
)
from fill import fill as run_fill, format_fill_md  # noqa: E402  (M3: L3 candidate retrieval)
from replace_impact import replace_impact as run_replace, format_replace_impact_md  # noqa: E402  (M3)
from dexlink import lookup_items  # noqa: E402
import sources  # noqa: E402  (M4: real-team joint set ⊕ meta spread resolver)
import repset  # noqa: E402  (real-team library for fill's co-occurrence / sample views)
import libsearch  # noqa: E402  (sample-library search: combined-condition query + content-id fetch)
import observed  # noqa: E402  (UEP P7: observed-team retrieval, AI-facing evidence)
import intake as intake_catalog_mod  # noqa: E402  (UEP P3: static question catalog)
import oppcache  # noqa: E402  (M5 step 2: opponent observed-build matchup cache)
import metalink  # noqa: E402
import topmeta  # noqa: E402  (Top Meta: the library on the in-game fields + the symmetric comparison)
import contracts  # noqa: E402
import environment  # noqa: E402
import worker  # noqa: E402
import team_i18n as i18n  # noqa: E402
from stat_keys import canonical_spreads  # noqa: E402
from team_output import emit_json, output_options, output_path, protect_inputs, OutputError  # noqa: E402
from session_inputs import resolve_refs, materialize_inputs, result_failed, FILE_FIELDS  # noqa: E402


def _gate_status(out: dict) -> dict:
    """Put the actionable verdict first without changing receipt-bound facts."""
    kind = out.get("kind")
    if out.get("refused"):
        verdict, next_step = "refused", out["refused"].get("reason", "fix the input and rerun")
    elif kind == "context-audit":
        blocked = (out.get("missing_required") or out.get("conflicts") or out.get("unresolved_names")
                   or any(e.get("severity") == "error" for e in out.get("contract_errors", [])))
        verdict = "needs_input" if blocked else "ready"
        next_step = "resolve blocking gaps and rerun context-audit" if blocked else "ground candidates; use this audit_receipt in frame"
    elif kind == "frame":
        verdict = "ready" if out.get("skeletons") else "no_frames"
        next_step = ("assemble candidates from the grounded sets; preserve frame_receipt" if out.get("skeletons")
                     else "inspect the data scope and grounding gaps before assembling")
    elif kind == "slate-evaluate":
        verdict = "survivors" if out.get("survivors") else "no_survivors"
        next_step = "run checkpoint, then draft-init and answer-audit with the full output" if out.get("survivors") else "revise candidates using elimination reasons and rerun slate-evaluate"
    elif kind == "checkpoint":
        verdict = "pause" if out.get("pause") else "continue"
        next_step = "ask questions_to_ask before continuing" if out.get("pause") else "present the comparison; disclose caveats and any tuning not run"
    else:
        verdict = "pass" if out.get("pass") is True else "fail"
        next_step = "present the audited draft" if out.get("pass") is True else "fix violations and rerun answer-audit"
    return {"verdict": verdict, "next_step": next_step, **out}


def _emit_gate(out: dict, fail_on_verdict: bool = False, view: str = "full") -> int:
    out = _gate_status(out)
    emit_json(out, view=view, projector=project_slate if out.get("kind") == "slate-evaluate" else None)
    if out.get("refused"):
        return 2
    failed = out["verdict"] in {"needs_input", "no_frames", "no_survivors", "fail", "pause"}
    return 1 if fail_on_verdict and failed else 0


def _load_team(path: str):
    """load_team + fuzzy species canonicalization. Returns (team, name_flags).

    A USER-typed species typo (garchmp -> Garchomp) is auto-corrected here, BEFORE validate/diagnose,
    so every command operates on canonical names and validate stays strict. The corrections
    (name_flags) are surfaced in output, never silent. Ambiguous/unresolved names are left as-is for
    validate to flag (with suggestions). Dex down -> no correction (honest degradation)."""
    loaded = load_team(path)
    flags = []
    try:
        flags += canonicalize_species(loaded)
        flags += canonicalize_fields(loaded)
    except DexUnavailable:
        pass
    return loaded, flags


def _dex_resolve(names: list[str]) -> dict:
    """Fuzzy pokemon resolve with the standard degrade (dex down -> {}). ONE bridge for every gate:
    the P2 front gate and the P6 back gate recompute the SAME context audit through it — a second
    copy drifting apart would make the two gates disagree about the same context (self-audit
    2026-07-03)."""
    try:
        return resolve_names(names, "pokemon", fuzzy=True)
    except DexUnavailable:
        return {}


def _dex_items(names: list[str]) -> dict:
    try:
        return lookup_items(names)
    except DexUnavailable:
        return {}


def _canon_team_json(team: dict) -> tuple:
    """ONE canonicalization for a candidate team dict -> (canonical dict, name_resolution flags):
    feeds species extraction, validate, profile AND the battery/claim recompute — shared by the P5
    and P6 gates so a fuzzy-correction policy change can never split them.

    Item/ability/nature/move fields normalize through the same public resolver. Approximate readings
    return flags; unresolved fields stay verbatim for strict validation. Original receipt inputs
    are never mutated."""
    t = team_from_dict(team)
    flags = []
    try:
        flags += canonicalize_species(t)
        flags += canonicalize_fields(t)
    except DexUnavailable:
        pass
    return t.to_dict(), flags


def _battle_team_view(team: dict) -> dict:
    projected, _ = query_identity.battle_view([team])
    return projected[0]


def _dex_fact_fns(teams: list[dict], extra_species: set[str] | None = None) -> tuple:
    """The library-page prefetch ritual, ONE owner (landscape and observed both profile stored
    teams through it): sweep species/moves/items across the teams, resolve Mega forms via
    item.required_by, and return the three dict-backed fns team_profile/landscape consume — three
    sibling batches total, zero per-team subprocess. Raises DexUnavailable; the CALLER picks the
    policy (landscape refuses, observed degrades to signal-less rows)."""
    species: set[str] = set(extra_species or ())
    moves: set[str] = set()
    items: set[str] = set()
    for t in teams:
        for m in t.get("pokemon", []):
            if m.get("species"):
                species.add(m["species"])
            moves.update(mv for mv in (m.get("moves") or []) if mv)
            if m.get("item"):
                items.add(m["item"])
    item_info = lookup_items(sorted(items)) if items else {}
    forms = {f for it in item_info.values() for f in (it.get("required_by") or [])}
    facts = lookup_pokemon(sorted(species | forms)) if species else {}
    move_facts = lookup_moves(sorted(moves)) if moves else {}
    return (lambda names: {n: facts.get(n, {"found": False}) for n in names},
            lambda names: {n: move_facts.get(n, {}) for n in names},
            lambda names: {n: item_info.get(n, {"found": False, "required_by": []}) for n in names})


def _library_query_preamble(context_path: str | None, game_format: str, season: str | None,
                            refuse_key: str) -> tuple:
    """Shared landscape/observed front door: contract-gate the build-context (a malformed context
    is refused, never half-used — external audit 2026-07-02), load it canonicalized, resolve the
    explicit season and the context-vs-CLI format mismatch warning.
    Returns (context, season, warns, rc); rc != 0 means the refusal was already printed."""
    if context_path:
        contract_errs = _check_contracts(None, context_path)
        _emit_contract_errs(contract_errs, "json")
        if contracts.fatal(contract_errs):
            print(i18n.t(refuse_key), file=sys.stderr)
            return None, season, [], 2
    context = _load_context(context_path) if context_path else None
    warns: list = []
    if context and context.format and context.format != game_format:
        warns.append(i18n.Msg('team_context_format_mismatch', ctx=context.format, cli=game_format))
    return context, season, warns, 0


def _library_scope(fmt: str, season: str | None = None, context=None) -> dict[str, Any]:
    """Real-team data scope for library consumers.

    `--season` is exact: it reads that season's file because the user asked for a labeled partition.
    Without an explicit season, current-rule data is consumed as one rule pool: sibling seasons under
    that rule contribute together while other rules stay out. A context that explicitly targets an
    old, non-current rule keeps the old exact-season behavior when it has a season label.
    """
    if season:
        return {"kind": "season", "season": season, "rule": environment.rule_for_season(season)}
    ctx_season = getattr(context, "season", None) if context else None
    ctx_rule = (getattr(context, "rule", None) if context else None) or environment.rule_for_season(ctx_season)
    if ctx_season and ctx_rule and ctx_rule != environment.CURRENT_RULE:
        return {"kind": "season", "season": ctx_season, "rule": ctx_rule}
    return {"kind": "rule", "rule": ctx_rule or environment.CURRENT_RULE}


def _scope_teams(fmt: str, scope: dict[str, Any], fresh: bool = False) -> list[dict[str, Any]]:
    if scope.get("kind") == "rule":
        loader = repset.load_teams_for_rule if fresh else repset.cached_teams_for_rule
        return loader(fmt, scope["rule"])
    loader = repset.load_teams if fresh else repset.cached_teams
    return loader(fmt, scope.get("season"))


def _scope_seasons(teams: list[dict[str, Any]], scope: dict[str, Any]) -> list[str]:
    seasons = repset.seasons_in(teams)
    if seasons:
        return seasons
    return [scope["season"]] if scope.get("kind") == "season" and scope.get("season") else []


def _scope_stamp(scope: dict[str, Any], teams: list[dict[str, Any]] | None = None,
                 context=None) -> tuple[dict, list[str]]:
    ctx_season = getattr(context, "season", None) if context else None
    ctx_rule = getattr(context, "rule", None) if context else None
    if scope.get("kind") == "rule":
        seasons = _scope_seasons(teams or [], scope)
        return environment.resolve(ctx_season, ctx_rule, data_rule=scope.get("rule"), data_seasons=seasons)
    return environment.resolve(ctx_season, ctx_rule,
                               data_season=scope.get("season"), data_rule=scope.get("rule"))


def _scope_query(scope: dict[str, Any], teams: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if scope.get("kind") == "rule":
        return {"season": None, "rule": scope.get("rule"),
                "data_seasons": _scope_seasons(teams or [], scope)}
    return {"season": scope.get("season"), "rule": scope.get("rule")}


def _library_copy_fn(fmt_battle: str, season: str | None = None, rule: str | None = None, *, comparison: bool = False):
    """The library-guardrail detector both gates share: verbatim joint-set / same-composition
    matches against the CURRENT partition. Returns None when the library is empty (nothing to
    match). Recomputed per run — overlap facts sit outside the receipt digest, so consumers must
    never trust a saved copy (transparency gate, user ruling 2026-07-03)."""
    lib = repset.load_teams_for_rule(fmt_battle, rule) if rule else repset.load_teams(fmt_battle, season)
    if not lib:
        return None
    index = libsearch.library_copy_index(lib)
    def lookup(team_c):
        result = libsearch.find_library_copies(team_c, index)
        if comparison:
            result = {**(result or {"verbatim_ids": [], "same_composition": 0}),
                      "composition_overlap": libsearch.library_composition_overlap(team_c, index)}
        return result
    return lookup


def _validate_json(team_c: dict, context) -> dict:
    """validate() as the JSON verdict both machine gates consume (errors pre-jsonified — the P5 and
    P6 copies had already drifted on that within one commit; self-audit 2026-07-03)."""
    r = validate(team_from_dict(team_c), context)
    return {"status": r.status, "valid": r.valid, "confidence": r.confidence,
            "errors": i18n.jsonify(list(r.errors)),
            # Unknown is a first-class legality verdict, not an empty-error mystery.  Preserve the
            # same skipped-check reasons and warnings exposed by the standalone validate command so
            # slate/checkpoint consumers can explain why certification was incomplete.
            "warnings": i18n.jsonify(list(r.warnings)),
            "skipped": i18n.jsonify(list(r.skipped)),
            "messages": r.message_details()}


def _emit_name_flags(flags: list[dict]) -> None:
    """Surface auto-corrected species typos to stderr (visible in every output mode, json stdout
    stays clean). validate/diagnose ALSO embed them in their json under `name_resolution`."""
    for line in _name_flag_lines(flags):
        print(line, file=sys.stderr)


def _name_flag_lines(flags: list[dict]) -> list[str]:
    """Human-facing did-you-mean lines for auto-corrected species typos (non-silent)."""
    return [i18n.t('team_name_corrected', frm=f["from"], to=f["to"],
                   distance=f.get("distance"), score=f.get("score")) for f in flags]


def _canon_map(names: list[str]) -> dict[str, str]:
    """query -> canonical species for every resolvable name, via the dex `resolve` bridge — batch +
    fuzzy + MEGA-COMPOSE + nicknames, ONE subprocess round-trip for any number of names — so a
    Chinese / alias / lightly-typo'd / `Mega喷火龙Y`-style entry resolves to the SAME canonical the
    team members are normalized to, including keep_mega's compositional Mega names.
    Unresolved/ambiguous names are simply absent (callers keep them verbatim: validate still flags
    them, a literal filter never over-matches, and resolve refuses to guess an ambiguous Mega); dex
    down -> empty map (honest degradation)."""
    names = [n for n in names if n]
    if not names:
        return {}
    try:
        recs = resolve_names(names, "pokemon", fuzzy=True)
    except DexUnavailable:
        return {}
    _emit_name_flags(query_identity.corrections({("pokemon", name): rec for name, rec in recs.items()}))
    return {n: r["canonical"] for n, r in recs.items() if r.get("ok") and r.get("canonical")}


def _normalize_nested(value: dict, surface: str):
    normalized, flags, errors = nested_names.normalize(value, surface, resolve_names)
    _emit_name_flags(flags)
    return normalized, flags, errors


def _nested_input(value: dict, surface: str) -> tuple[dict | None, list[dict]]:
    try:
        normalized, flags, errors = _normalize_nested(value, surface)
    except DexUnavailable as exc:
        emit_json({"ok": False, "query": surface, "error": {"code": "engine_unavailable", "message": str(exc)}})
        return None, []
    if errors:
        emit_json({"ok": False, "query": surface, "error": {
            "code": "bad_input", "message": "nested names must resolve before this operation",
            "details": {"schema": "nested_name_errors_v1", "entries": errors}}})
        return None, flags
    return normalized, flags


def _load_context(path: str):
    """load_context + `_canonicalize_context` (see there)."""
    return _canonicalize_context(load_context(path))


def _canonicalize_context(context):
    """Dex canonicalization of a BuildContext's species-referencing fields, so build-context hard
    constraints resolve symmetrically with the team's own members (`_load_team`).

    `avoid` is first split by dex kind (ONE strict item lookup — no fuzzy hijacking of a species
    typo): an item entry ("讲究围巾") used to masquerade as an unresolvable species and silently
    filter nothing. The branches are complementary — an item row without a canonical name falls
    through to the species side rather than vanishing from both buckets. Then owned/locked/prefer/
    keep_mega + the species side of avoid canonicalize in ONE batched resolve call (per-list wiring
    cost 5 subprocess round-trips per context load; audit 2026-07-02). The combined `avoid` stays
    populated for back-compat. Tactic/free-form fields (wants/exclude_tactics) are untouched — not
    species."""
    item_facts: dict = {}
    if context.avoid:
        try:
            item_facts = lookup_items(list(context.avoid))
        except DexUnavailable:
            pass                                       # dex down -> every entry stays species-side
    avoid_rest: list[str] = []
    context.avoid_items = []
    for n in context.avoid:
        f = item_facts.get(n) or {}
        if f.get("found") and f.get("name"):
            context.avoid_items.append(f["name"])
        else:
            avoid_rest.append(n)
    # keep_mega is a NAME; contracts refuses non-strings (E_TYPE), but guard here too — this loader
    # must never feed a non-string into subprocess argv (audit 2026-07-02: a legacy boolean crashed
    # every context-loading command).
    keep = [context.keep_mega] if isinstance(context.keep_mega, str) and context.keep_mega else []
    canon = _canon_map(context.owned + context.locked + context.prefer + context.avoid_soft
                       + avoid_rest + keep)
    context.owned = [canon.get(n, n) for n in context.owned]
    context.locked = [canon.get(n, n) for n in context.locked]
    context.prefer = [canon.get(n, n) for n in context.prefer]
    # best-effort: species entries canonicalize, item/free-text entries stay verbatim (soft — never
    # a mechanical filter, so no item split is needed here).
    context.avoid_soft = [canon.get(n, n) for n in context.avoid_soft]
    context.avoid_species = [canon.get(n, n) for n in avoid_rest]
    context.avoid = context.avoid_species + context.avoid_items
    if keep:
        context.keep_mega = canon.get(context.keep_mega, context.keep_mega)
    return context


def _stamp(context, team=None) -> tuple[dict, list[str]]:
    """Environment stamp (+ mismatch warnings) for the current base; attached to every output.

    season/rule are taken from the build-context first, then the team-json itself (a team that
    declares its own season/rule must not be silently ignored — audit 2026-06-21), then default.
    When BOTH declare them and disagree, the context wins (it is the intent layer), but the team's
    overridden declaration is reported, never silently dropped (audit 2026-06-21).
    """
    ctx_season = getattr(context, "season", None) if context else None
    ctx_rule = getattr(context, "rule", None) if context else None
    team_season = getattr(team, "season", None) if team else None
    team_rule = getattr(team, "rule", None) if team else None
    stamp, warnings = environment.resolve(ctx_season or team_season, ctx_rule or team_rule)
    if ctx_season and team_season and ctx_season != team_season:
        warnings.append(i18n.Msg('team_season_mismatch', team_season=repr(team_season),
                               ctx_season=repr(ctx_season)))
    if ctx_rule and team_rule and ctx_rule != team_rule:
        warnings.append(i18n.Msg('team_rule_mismatch', team_rule=repr(team_rule),
                               ctx_rule=repr(ctx_rule)))
    return stamp, warnings


def _make_sets_fn(season: str | None = None, rule: str | None = None):
    """Memoized opponent-set batch resolver for one command run. Keyed by (name, fmt_) so a cross-format
    session (single-vs-double compare) never serves a single-format set for a double lookup."""
    sets_cache: dict[tuple[str, str | None], dict | None] = {}

    def sets_fn(names: list[str], fmt_: str | None) -> dict[str, dict | None]:
        missing = [n for n in names if (n, fmt_) not in sets_cache]
        if missing:
            resolved = sources.resolve_opponent_sets(missing, fmt_, season=season, rule=rule)
            sets_cache.update({(n, fmt_): v for n, v in resolved.items()})
        return {n: sets_cache.get((n, fmt_)) for n in names}

    return sets_fn


def _make_form_sets_fn(rule: str):
    """Tune queries one actual form; derive its joint sets before applying the cluster budget."""
    pools, cached = {}, {}

    def variants(species: str, fmt: str):
        key = (species, fmt)
        if key not in cached:
            if fmt not in pools:
                pools[fmt], _ = query_identity.battle_view(repset.cached_teams_for_rule(fmt, rule))
            arches = {}
            def rep_fn(name, game_format, **kwargs):
                rows = repset.representative_sets_from_teams(name, game_format, pools[fmt]) or []
                arches.update({sources.variant_id(name, a.get("item"), a.get("ability")): a for a in rows})
                return rows
            rows = sources.resolve_opponent_variants(species, fmt, rule=rule, repset_fn=rep_fn)
            for row in rows:
                arch = arches.get(row.get("variant_id")) or {}
                for field in ("configuration_basis", "registration_modes", "registration_sample"):
                    if field in arch:
                        row[field] = arch[field]
                modes = arch.get("registration_modes") or []
                abilities = {m.get("ability") for m in modes if m.get("species") != species}
                if len(abilities) == 1 and None not in abilities and all(m.get("species") != species for m in modes):
                    row["base_ability"] = next(iter(abilities))
                if modes:
                    row["note"] = (row.get("note") or "") + " | " + query_identity.registration_note(arch)
            if not rows:
                fallback = metalink.canonical_attacker_set(species, fmt)
                if fallback:
                    identity = query_identity.Forms([{"species": species, "item": fallback.get("item")}])
                    if identity({"species": species, "item": fallback.get("item")}) == species:
                        rows = [fallback]
            cached[key] = rows
        return cached[key]
    return variants


def _make_variants_fn(season: str | None = None, rule: str | None = None):
    """Memoized per-species build resolver: every real (item,ability) build as a FULL opponent set.

    This is what turns a ranked species into one matrix column per build, so a cell names a concrete
    set on both sides instead of a species standing in for all of its builds."""
    cache: dict[tuple[str, str | None], list] = {}

    def variants_fn(species: str, fmt_: str | None) -> list:
        key = (species, fmt_)
        if key not in cache:
            try:
                cache[key] = sources.resolve_opponent_variants(
                    species, fmt_ or "single", season=season, rule=rule)
            except Exception:
                cache[key] = []
        return cache[key]

    return variants_fn


def _make_damage_fn(base_fn=damage_batch):
    """Content-keyed ncp damage-batch memo for ONE command run. A multi-battery caller (slate grades
    many candidate teams that share most of their members) otherwise re-sends byte-identical
    `{attacker, defender, move, field}` calcs per candidate; this issues each unique calc at most once.
    Mirrors `_make_sets_fn`'s per-run memo. Damage is a pure function of (actors, move, field) —
    season/rule independent and the actors already pin species/set — so the key is exact, never stale,
    and cross-format-safe. Variant-expanded batteries repeat many shared-member calculations, so this
    is where that absolute cost is recovered.

    Preserves damage_batch's index contract: returns a list aligned 1:1 with `requests`. A calc the
    underlying batch could not run (short/empty return) reads back as None for that slot — the same
    degradation matchup already handles (`results[idx] or {}` / a None offense result)."""
    cache: dict[str, dict[str, Any]] = {}

    def _key(r: dict[str, Any]) -> str:
        return json.dumps({"attacker": r["attacker"], "defender": r["defender"],
                           "move": r["move"], "field": r.get("field") or {}},
                          sort_keys=True, ensure_ascii=False)

    def damage_fn(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keys = [_key(r) for r in requests]
        misses: dict[str, dict[str, Any]] = {}       # unique-by-key: dedup WITHIN this batch too, so a
        for r, k in zip(requests, keys):             # duplicated calc is sent at most once per run
            if k not in cache and k not in misses:
                misses[k] = r
        if misses:
            fresh = base_fn(list(misses.values()))   # base_fn returns results 1:1 with its input;
            for k, res in zip(misses.keys(), fresh):  # a short/empty return just leaves those keys uncached
                cache[k] = res
        return [cache.get(k) for k in keys]          # 1:1 with `requests`; uncached slot -> None

    return damage_fn


def _check_contracts(team_path: str | None, context_path: str | None = None) -> list:
    """Run the executable I/O contract on the raw inputs (team and/or context) as written,
    before any normalization. Returns ContractErrors (team first, then context); [] when clean
    or when the team is non-JSON Showdown text. A malformed JSON file (syntax error, or a JSON
    array where an object is required) yields a FATAL error here so the caller refuses instead of
    crashing in the loader or misreading an array as Showdown text (audit 2026-06-21)."""
    raw_team, errs = _raw_json(team_path)
    if raw_team is not None:
        errs = errs + contracts.check_team(raw_team)
    if context_path:
        raw_ctx, ctx_errs = _raw_json(context_path, json_only=True)
        errs += ctx_errs
        if raw_ctx is not None:
            errs += contracts.check_context(raw_ctx)
    return errs


def _emit_contract_errs(errs: list, fmt: str) -> None:
    """Print contract errors (stderr for md, so they don't corrupt a JSON stdout payload)."""
    if errs:
        print(contracts.format_errors(errs), file=sys.stderr)


def _env_header(stamp: dict, warnings: list[str]) -> str:
    a = stamp.get("as_of") or {}
    line = (f"_{i18n.t('team_env')}: {stamp['season']} / {stamp['rule']} — "
            f"{i18n.t('team_env_dex')} {a.get('dex_built_at') or '?'}, "
            f"{i18n.t('team_env_meta')} {a.get('meta_updated_at') or '?'}_")
    ds = stamp.get("data_season")
    data_seasons = stamp.get("data_seasons")
    if data_seasons:
        ds_label = ", ".join(data_seasons) if data_seasons else "none"
        line += (f"\n_{i18n.t('team_data_partition')}: {ds_label} / "
                 f"{stamp.get('data_rule') or 'rule n/a'} (real-team library)_")
    elif ds is not None and ds != stamp["season"]:
        # The real-team data was served from a historical PARTITION, not the current base — label it
        # so the result is never read as current-season data (audit 2026-06-26).
        line += (f"\n_{i18n.t('team_data_partition')}: {ds} / "
                 f"{stamp.get('data_rule') or 'rule n/a'} (real-team library)_")
    if warnings:
        line += "\n" + "\n".join(f"> ⚠️ {w}" for w in warnings)
    return line


def _raw_json(path: str | None, *, json_only: bool = False) -> tuple[Any, list]:
    """Parse the raw input for the executable contract (run before normalization).

    Returns (parsed, errors):
      - (obj, [])      input parsed as JSON — hand `obj` (any JSON type) to the contract, which
                       checks it really is the right shape;
      - (None, [])     non-JSON text — a team may be a Showdown paste, so let the loader read it;
      - (None, [err])  the input is malformed where it must be JSON: a `{`/`[`-leading file that
                       fails to parse, or (json_only) any non-JSON build-context. We emit a FATAL
                       contract error so the caller refuses, instead of letting load_team()/
                       load_context() throw or silently parsing a JSON array as Showdown text
                       (audit 2026-06-21).
    `json_only` is for the build-context, which has no Showdown form: non-JSON text is malformed there.
    """
    if not path:
        return None, []
    try:
        text = Path(path).read_text(encoding="utf-8").lstrip()
    except OSError as e:
        return None, [contracts.ContractError(
            contracts.Code.TYPE, "", i18n.Msg('ct_input_unreadable', path=path, e=e))]
    if not text.startswith(("{", "[")):
        if json_only:
            return None, [contracts.ContractError(
                contracts.Code.TYPE, "", "build-context must be a JSON object")]
        return None, []                       # Showdown paste — the loader handles it
    try:
        return json.loads(text), []
    except json.JSONDecodeError as e:
        return None, [contracts.ContractError(
            contracts.Code.TYPE, "",
            f"input is not valid JSON ({e.msg} at line {e.lineno} column {e.colno})")]


def cmd_parse(path: str, fmt: str, game_format: str | None = None) -> int:
    if game_format not in (None, "single", "double"):
        print("parse needs --game-format single|double", file=sys.stderr)
        return 2
    raw, errs = _raw_json(path)
    if raw is not None:
        errs = errs + contracts.check_team(raw)
    if errs:
        print(contracts.format_errors(errs), file=sys.stderr)
    if contracts.fatal(errs):
        return 1
    team, name_flags = _load_team(path)
    _emit_name_flags(name_flags)
    if game_format and team.format and game_format != team.format:
        print("parse format conflicts with the input team; use the same --game-format", file=sys.stderr)
        return 2
    team.format = game_format or team.format
    if fmt == "json":
        emit_json(team.to_dict())
    else:
        d = team.to_dict()
        print(i18n.t('team_parse_header', fmt=d.get('format') or 'format?', n=len(d['pokemon'])))
        for m in d["pokemon"]:
            mv = ", ".join(m.get("moves") or [])
            print(f"- {m['species']} @ {m.get('item') or '-'} | {m.get('ability') or '-'} | {mv}")
    return 0


def cmd_suggest_fixes(path: str, context_path: str | None) -> int:
    raw, errors = _raw_json(path, json_only=True)
    errors += _check_contracts(path, context_path)
    if raw is None or contracts.fatal(errors):
        emit_json({"ok": False, "query": "suggest-fixes", "error": {
            "code": "bad_input", "message": "suggest-fixes requires valid team-json and context shapes"},
            "contract": [e.to_dict() for e in errors]})
        return 1
    try:
        candidate = team_from_dict(raw)
        canonicalize_species(candidate, fuzzy=False)
        canonicalize_fields(candidate, fuzzy=False)
        context = _load_context(context_path) if context_path else None
        out = corrections.suggest(raw, candidate, context)
        stamp, warnings = _stamp(context, candidate)
        out.update(environment=stamp, warnings=warnings)
        emit_json(out)
        return 0  # the suggestions query succeeded; original legality is a separate field
    except (DexUnavailable, ValueError) as exc:
        emit_json({"ok": False, "query": "suggest-fixes", "error": {
            "code": "engine_unavailable" if isinstance(exc, DexUnavailable) else "bad_input", "message": str(exc)}})
        return 1


def cmd_validate(path: str, fmt: str, context_path: str | None) -> int:
    # Contract check first: a malformed shape is more fundamental than legality. On a FATAL
    # contract error we must NOT call load_team — malformed input can make the loader throw
    # (audit 2026-06-21); report the contract failure and stop here instead.
    contract_errs = _check_contracts(path, context_path)
    if contracts.fatal(contract_errs):
        if fmt == "json":
            emit_json({"status": "invalid", "next_step": "fix contract errors and rerun validate", "valid": False, "confidence": "high",
                              "errors": [], "warnings": [], "skipped": [],
                              "contract": [e.to_dict() for e in contract_errs]})
        else:
            print(i18n.t('team_gate_status', verdict="invalid", next_step="fix contract errors and rerun validate"))
            print(contracts.format_errors(contract_errs))
            print("\n" + i18n.t('team_refuse_validate'))
        return 1
    team, name_flags = _load_team(path)
    _emit_name_flags(name_flags)
    context = _load_context(context_path) if context_path else None
    result = validate(team, context)
    stamp, env_warn = _stamp(context, team)
    next_step = ("continue with evaluated facts" if result.valid
                 else "resolve legality errors or skipped checks and rerun validate")
    if fmt == "json":
        out = {"status": result.status, "next_step": next_step, **result.to_dict()}
        out["contract"] = [e.to_dict() for e in contract_errs]
        out["environment"] = stamp
        if env_warn:
            out["warnings"] = out.get("warnings", []) + env_warn
        if name_flags:
            out["name_resolution"] = name_flags     # fuzzy species auto-corrections
        emit_json(out)
    else:
        print(i18n.t('team_gate_status', verdict=result.status, next_step=next_step))
        print(_env_header(stamp, env_warn))
        if contract_errs:
            print(contracts.format_errors(contract_errs))
            print()
        print(format_report(result))
    return 0 if result.valid else 1


def _opponent_rows(opponents: dict, fmt: str) -> list[dict]:
    import team_review
    from canonhash import content_hash
    errors = contracts.check_team(opponents)
    if contracts.fatal(errors) or not 1 <= len(opponents.get("pokemon") or []) <= 6:
        raise ValueError("opponents must be team-json with 1..6 members")
    if opponents.get("format") and opponents["format"] != fmt:
        raise ValueError("opponent format differs from the requested battle format")
    result = validate(team_from_dict(opponents))
    errors = [i18n.Msg(k, **kw).en() for k, kw in result._errors if k != "val_team_size"]
    if errors or result._skipped:
        raise ValueError("opponents are not verified legal: " + "; ".join(errors + result.skipped))
    facts = lookup_pokemon([m["species"] for m in opponents["pokemon"]])
    items = lookup_items(sorted({m["item"] for m in opponents["pokemon"] if m.get("item")}))
    forms = sorted({form for item in items.values() for form in item.get("required_by") or []})
    if forms:
        facts.update(lookup_pokemon(forms))
    rows = []
    for member in opponents["pokemon"]:
        row = {"pokemon_en": member["species"], "rank": None}
        if any(k in member for k in ("item", "ability", "nature", "moves", "spread")):
            missing = team_review.requirements({"pokemon": [member]})[0]["missing_fields"]
            if missing:
                raise ValueError(f"partial opponent set for {member['species']}: provide {', '.join(missing)} or species only")
            effective = effective_member(member, facts, items)
            actor = member_actor(effective)
            row["stated_set"] = {"variant_id": "stated:" + content_hash(effective, 24),
                "species": member["species"], "run_form": effective["species"],
                **{k: actor[k] for k in ("ability", "item", "nature", "sps")}, "moves": member["moves"],
                "coverage": 1.0, "coverage_basis": "stated_configuration", "source": "stated",
                "confidence": "stated", "note": "Explicit hypothetical set; coverage is this stated scenario, not observed sample mass."}
        rows.append(row)
    return rows


def _load_battle_scope(opponents_path, conditions_path):
    import battle_scope
    opponents = None
    if opponents_path:
        raw, errors = _raw_json(opponents_path, json_only=True)
        if raw is None or contracts.fatal(contracts.check_team(raw)):
            raise ValueError("opponents requires a valid team-json object")
        opponents, _ = _canon_team_json(raw)
        # Canonicalization supplies defaults. Keep omitted fields omitted so a bare species and
        # an explicit (possibly empty) partial set cannot collapse into the same scenario.
        for supplied, member in zip(raw["pokemon"], opponents["pokemon"]):
            for key in ("item", "ability", "nature", "moves", "spread"):
                if key not in supplied:
                    member.pop(key, None)
    field, _ = _raw_json(conditions_path, json_only=True) if conditions_path else ({}, [])
    if conditions_path and field is None:
        raise ValueError("conditions requires a JSON object")
    return {"opponents": opponents, "conditions": battle_scope.conditions(field)}


def _run_check_battery(team: Team, context, top_k: int, scope: dict | None = None) -> tuple[dict[str, Any], str]:
    """The ONE matchup battery both --with-check consumers (diagnose, select) need: a single ~top-K
    opponent ncp run + its check_coverage / members grid. Extracted so the two share ONE setup path (they
    were byte-for-byte twins). Raises the sibling-unavailable errors for each caller to disclose as a note.
    Returns (matchup result, battle format)."""
    top_k = normalize_top_k(top_k)
    fmt_battle = (context.format if context and context.format else None) or team.format
    stamp, _ = _stamp(context, team)
    rule = stamp.get("rule")
    scope = scope or {}
    rows = _opponent_rows(scope["opponents"], fmt_battle) if scope.get("opponents") else usage_top_k(fmt_battle, top_k)
    mres = run_matchup(team.to_dict(), rows, fmt=fmt_battle, dex_fn=lookup_pokemon,
                       sets_fn=_make_sets_fn(rule=rule), damage_fn=damage_batch, move_fn=lookup_moves,
                       variants_fn=_make_variants_fn(rule=rule), item_fn=lookup_items,
                       conditions=scope.get("conditions"))
    mres["opponent_scope"] = "stated_roster" if scope.get("opponents") else "usage_top_k"
    return mres, fmt_battle


def _diagnose_check_coverage(team: Team, context, top_k: int = 8) -> dict[str, object]:
    """Opt-in (--with-check) ncp-grounded CHECK coverage for the defense picture. `diagnose_defense`
    stays ncp-free (type-layer); this runs the matchup battery ONLY when asked and returns its
    check_coverage roll-up (wrapped for rendering). A note — never a crash — when a sibling is down;
    the type-layer defense facts stand on their own."""
    try:
        mres, fmt_battle = _run_check_battery(team, context, top_k)
        cov = mres.get("check_coverage")
        if not cov:
            return {"unavailable": i18n.Msg('chk_cov_none')}
        return {"format": fmt_battle, "top_k": top_k, "confidence": mres.get("confidence"),
                "check_coverage": cov,
                # the per-member cells behind the roll-up (offense/incoming/speed/check per
                # opponent) — consumers that render a clickable detail need the same facts
                # the matchup grid exposes; the roll-up alone cannot answer "why C0?"
                "members": mres.get("members"),
                "note": "opt-in ncp-grounded complement to the type-layer defense above (--with-check)"}
    except (NcpUnavailable, MetaUnavailable, DexUnavailable) as e:
        return {"unavailable": i18n.Msg('chk_cov_skipped', kind=type(e).__name__, e=e)}


def _select_check_grid(team: Team, context, top_k: int = 8, scope: dict | None = None):
    """Opt-in (--with-check) member x meta top-K CHECK grid for `select` to roll up PER LINEUP.

    Runs the matchup battery ONCE and returns its `members` grid (rows carrying each member's per-
    opponent `check`); selection then restricts that grid to each lineup — a pure set operation, no
    extra ncp per combo. Returns (grid, note) on success, (None, note) when a sibling is down — a
    disclosed note, never a crash: the enumeration facts stand on their own."""
    try:
        mres, fmt_battle = _run_check_battery(team, context, top_k, scope)
        grid = mres.get("members")
        if not grid or mres.get("check_coverage") is None:
            return None, {"unavailable": i18n.Msg('chk_cov_lineup_none')}
        return grid, {"format": fmt_battle, "top_k": mres.get("top_k"),
                      "conditions": (scope or {}).get("conditions"), "opponent_scope": mres.get("opponent_scope"),
                      "confidence": mres.get("confidence"),
                      "confidence_reason": mres.get("confidence_reason")}
    except (NcpUnavailable, MetaUnavailable, DexUnavailable) as e:
        return None, {"unavailable": i18n.Msg('chk_cov_lineup_skipped', kind=type(e).__name__, e=e)}


def _diagnostic_facts(team, context, aspect: str = "all") -> dict:
    facts = lookup_pokemon([m.species for m in team.pokemon if m.species])
    held = sorted({m.item for m in team.pokemon if m.item})
    if held:
        items = lookup_items(held)
        forms = sorted({form for item in items.values() for form in item.get("required_by") or []})
        if forms:
            facts.update(lookup_pokemon(forms))
    moves = lookup_moves(sorted({move for member in team.pokemon for move in member.moves})) if aspect in ("all", "offense", "speed") else {}
    result = {}
    if aspect in ("all", "defense"):
        result["defense"] = diagnose_defense(team, facts)
    if aspect in ("all", "offense"):
        result["offense"] = diagnose_offense(team, facts, moves, variance_averse=bool(context and context.variance_tolerance == "averse"))
    if aspect in ("all", "speed"):
        result["speed"] = diagnose_speed(team, facts, moves)
    if aspect in ("all", "roles"):
        result["roles"] = diagnose_roles(team, facts, rule=context.rule if context else None)
    return result


def cmd_diagnose(path: str, fmt: str, aspect: str, context_path: str | None = None,
                 with_check: bool = False, top_k: int = 8, focus_path: str | None = None) -> int:
    if focus_path:
        if fmt != "json" or aspect != "all":
            emit_json({"ok": False, "error": {"code": "bad_input",
                      "message": "diagnose --focus returns a coordinated review; use --format json and --aspect all"}})
            return 2
        return cmd_review(path, context_path, top_k=top_k, focus_path=focus_path)
    contract_errs = _check_contracts(path, context_path)
    _emit_contract_errs(contract_errs, fmt)
    if contracts.fatal(contract_errs):
        print(i18n.t('team_refuse_diagnose'), file=sys.stderr)
        return 2
    team, name_flags = _load_team(path)
    _emit_name_flags(name_flags)
    # variance_tolerance=averse flags the team's luck lines (offense). The optional context carries it;
    # no context = no flag (the luck-line FACT is surfaced regardless).
    context = _load_context(context_path) if context_path else None
    try:
        out = _diagnostic_facts(team, context, aspect)
    except DexUnavailable as e:
        print(i18n.t('team_dex_unavailable', e=e), file=sys.stderr)
        return 2
    if with_check and aspect in ("defense", "all"):
        out["check_coverage"] = _diagnose_check_coverage(team, context, top_k)
    stamp, env_warn = _stamp(None, team)
    out["environment"] = stamp
    if env_warn:
        out["warnings"] = env_warn
    if name_flags:
        out["name_resolution"] = name_flags         # fuzzy species auto-corrections
    if fmt == "json":
        emit_json(canonical_spreads(out))
    else:
        print(_env_header(stamp, env_warn) + "\n")
        blocks = []
        if "defense" in out:
            blocks.append(format_defense_md(out["defense"]))
        if isinstance(out.get("check_coverage"), dict) and out["check_coverage"].get("check_coverage"):
            blocks.append(format_check_coverage_md(out["check_coverage"]))
        elif isinstance(out.get("check_coverage"), dict):
            blocks.append("_" + i18n.t('chk_cov_label') + " "
                          + str(out["check_coverage"].get("unavailable") or i18n.t('chk_cov_na')) + "_")
        if "offense" in out:
            blocks.append(format_offense_md(out["offense"]))
        if "speed" in out:
            blocks.append(format_speed_md(out["speed"]))
        if "roles" in out:
            blocks.append(format_roles_md(out["roles"]))
        print("\n\n".join(blocks))
    return 0


def cmd_speed_ladder(species, game_format, config, conditions, limit, opponent_ranges, fmt):
    """One rule pool and one numerical batch for a complete speed comparison."""
    def obj(value, path):
        if value is None or isinstance(value, dict):
            return value
        if not isinstance(value, str):
            raise speed_ladder.InputError(path, "expected a JSON object or its file path")
        try:
            raw = sys.stdin.read() if value == "-" else (
                value if value.lstrip().startswith("{") else Path(value).read_text(encoding="utf-8"))
            result = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise speed_ladder.InputError(path, str(exc)) from exc
        if not isinstance(result, dict):
            raise speed_ladder.InputError(path, "expected a JSON object")
        return result
    try:
        if game_format not in ("single", "double"):
            raise speed_ladder.InputError("game_format", "--game-format single|double is required", code="unknown_format")
        limit = 20 if limit is None else limit
        if type(limit) is not int or limit < 1:
            raise speed_ladder.InputError("limit", "limit must be a positive integer")
        if config == "-" and conditions == "-":
            raise speed_ladder.InputError("conditions", "stdin can supply only one JSON object")
        config, conditions = obj(config, "set"), obj(conditions, "conditions")
        prepared = speed_ladder.prepare(species, config, conditions, resolve_names)
        stamp, warnings = _stamp(None, None)
        out = speed_ladder.ladder(species, game_format,
            repset.cached_teams_for_rule(game_format, stamp["rule"]), usage_top_k(game_format, limit),
            config=config, conditions=conditions, resolve_fn=resolve_names,
            dex_fn=lookup_pokemon, item_fn=lookup_items, meta_fn=metalink.canonical_attacker_sets,
            speed_fn=speed_batch, opponent_ranges=opponent_ranges, prepared=prepared)
    except speed_ladder.InputError as exc:
        emit_json(exc.result())
        return 0 if exc.code == "not_found" else 1
    except (DexUnavailable, MetaUnavailable, NcpUnavailable, OSError, RuntimeError) as exc:
        emit_json({"ok": False, "query": "speed-ladder", "error": {"code": "engine_unavailable", "message": str(exc)}})
        return 1
    out["environment"], out["query"]["limit"] = stamp, limit
    if warnings:
        out["warnings"] = warnings
    _emit_name_flags(out["name_resolution"])
    if fmt == "json":
        emit_json(out)
    else:
        print(speed_ladder.format_md(out))
    return 0


def cmd_tune(path: str, fmt: str, context_path: str | None) -> int:
    if not context_path:
        print(i18n.t('team_tune_needs_context'), file=sys.stderr)
        return 2
    # Contract-check BOTH inputs (the team is fed to the damage/speed math, not just the context).
    contract_errs = _check_contracts(path, context_path)
    _emit_contract_errs(contract_errs, fmt)
    if contracts.fatal(contract_errs):
        print(i18n.t('team_refuse_tune'), file=sys.stderr)
        return 2
    team, name_flags = _load_team(path)
    _emit_name_flags(name_flags)
    context = _load_context(context_path)
    if not context.benchmarks:
        print(i18n.t('team_no_benchmarks'), file=sys.stderr)
        return 2
    normalized, nested_flags = _nested_input({"benchmarks": context.benchmarks}, "context")
    if normalized is None:
        return 2
    context.benchmarks = normalized["benchmarks"]
    stamp, env_warn = _stamp(context, team)
    rule_scope = stamp.get("rule")
    # tune's defender/attacker set resolves through the M4 resolver too (real-team joint ⊕ meta spread),
    # so survive/kill cliffs tune against a real co-occurring set when the library has it (handoff §5.1).
    fmt_battle = context.format or team.format
    try:
        form_sets = _make_form_sets_fn(rule_scope)

        def meta_fn(species: str, fmt_: str | None) -> dict | None:
            rows = form_sets(species, fmt_ or fmt_battle)
            return rows[0] if rows else None

        out = run_tune(team.to_dict(), context.benchmarks, fmt=fmt_battle,
                       damage_fn=damage_vs, move_fn=lookup_moves, dex_fn=lookup_pokemon,
                       meta_fn=meta_fn, nature_dist_fn=nature_distribution,
                       locked=context.locked, damage_batch_fn=damage_batch, item_fn=lookup_items,
                       # The same retained observed builds as matchup/slate: one speed line per build,
                       # so Scarf and Mega configurations remain independently actionable.
                       archetypes_fn=form_sets)
    except NcpInputError as e:
        # The calculator ran but rejected a benchmark input (e.g. an off-roster Pokemon or unknown
        # move) — a parameter problem, NOT the skill being down. Say so, don't mislabel it.
        print(i18n.t('team_ncp_rejected_benchmark', e=e), file=sys.stderr)
        return 2
    except (DexUnavailable, NcpUnavailable) as e:
        print(i18n.t('team_sibling_unavailable', e=e), file=sys.stderr)
        return 2
    out["environment"] = stamp
    if name_flags or nested_flags:
        out["name_resolution"] = name_flags + nested_flags
    if env_warn:
        out["warnings"] = (out.get("warnings") or []) + env_warn
    if fmt == "json":
        emit_json(canonical_spreads(out))
    else:
        print(_env_header(stamp, env_warn) + "\n")
        print(format_tune_md(canonical_spreads(out)))
    return 0


def _review_evaluator(context_raw: dict, context, stamp: dict, fmt: str, top_k: int, focus: dict,
                      scope: dict | None = None):
    import team_review
    from slate_comparison import target_rows
    rows = None
    sets_fn, variants_fn = _make_sets_fn(rule=stamp.get("rule")), _make_variants_fn(rule=stamp.get("rule"))
    damage_fn = _make_damage_fn()
    scope = scope or {}

    def battery(value):
        nonlocal rows
        try:
            if rows is None:
                goals = [{"kind": "answer", "species": name} for name in focus.get("opponents") or []]
                base = _opponent_rows(scope["opponents"], fmt) if scope.get("opponents") else usage_top_k(fmt, top_k)
                rows = target_rows(base, goals)
            result = run_matchup(value, rows, fmt=fmt, dex_fn=lookup_pokemon, move_fn=lookup_moves,
                                 sets_fn=sets_fn, variants_fn=variants_fn, item_fn=lookup_items,
                                 damage_fn=damage_fn, conditions=scope.get("conditions"))
            result["opponent_scope"] = "stated_roster" if scope.get("opponents") else "usage_top_k"
            return result
        except (NcpUnavailable, MetaUnavailable, DexUnavailable) as exc:
            return {"unavailable": str(exc)}

    def selection(value, match, legality):
        return run_select(value, fmt=fmt, dex_fn=lookup_pokemon, item_fn=lookup_items,
                          legality_status=legality, keep_mega=context.keep_mega if context else None,
                          check_grid=match.get("members"), check_context={key: match.get(key) for key in
                          ("confidence", "confidence_reason", "top_k", "conditions", "opponent_scope")})

    def evaluate(value):
        result = team_review.review(value, context=context_raw or {}, environment=stamp,
            validate_fn=lambda t: _validate_json(t, context),
            diagnose_fn=lambda t: _diagnostic_facts(team_from_dict(t), context),
            matchup_fn=battery, select_fn=selection)
        result.update(top_k_requested=top_k, focus=focus, battle_scope=scope)
        result["targets"] = team_review.targets(result, focus)
        result["evidence_index"] = team_review.evidence(result)
        return result
    return evaluate


def _recompute_review(report):
    import team_review
    context_raw = report["context"]
    errors = contracts.check_team(report["team"]) + contracts.check_context(context_raw)
    if contracts.fatal(errors):
        raise ValueError("invalid report inputs")
    team_review.check_focus(report.get("focus", {}))
    context = _canonicalize_context(context_from_dict(context_raw))
    stamp, _ = _stamp(context, team_from_dict(report["team"]))
    evaluate = _review_evaluator(context_raw, context, stamp, context.format or report["team"].get("format"),
                                normalize_top_k(report["top_k_requested"]), report.get("focus", {}), report.get("battle_scope"))
    return i18n.jsonify(canonical_spreads(evaluate(report["team"])))


def cmd_review_claims(path: str, fail_on_verdict: bool = False) -> int:
    import team_review
    raw, errors = _raw_json(path, json_only=True)

    try:
        result = team_review.audit_claims(raw, recompute_fn=_recompute_review)
    except (ValueError, TypeError, KeyError, DexUnavailable, MetaUnavailable, NcpUnavailable) as exc:
        result = {"kind": "review-claims", "pass": False,
                  "violations": [{"code": "recompute_unavailable", "detail": str(exc)}], "recomputed": 0}
    emit_json(result)
    return 1 if fail_on_verdict and not result["pass"] else 0


def cmd_tune_cell(path: str) -> int:
    import tune_cell
    raw, _ = _raw_json(path, json_only=True)
    try:
        result = tune_cell.query(raw, recompute_fn=_recompute_review, dex_fn=lookup_pokemon,
                                 item_fn=lookup_items, threshold_fn=threshold.query,
                                 validate_fn=lambda t: _validate_json(t, None))
    except (ValueError, TypeError, KeyError, DexUnavailable, NcpUnavailable, counters.QueryError) as exc:
        emit_json({"ok": False, "query": "tune.from_cell", "error": {"code": "bad_input", "message": str(exc)}})
        return 2
    emit_json(canonical_spreads(result))
    return 0


def cmd_review(path: str, context_path: str | None = None, *, top_k: int = 8,
               changes_path: str | None = None, trial: bool = False,
               view: str = "summary", fail_on_verdict: bool = False, focus_path: str | None = None,
               opponents_path: str | None = None, conditions_path: str | None = None) -> int:
    import team_review
    kind = "try" if trial else "review"

    def bad(message):
        emit_json({"ok": False, "query": kind, "error": {"code": "bad_input", "message": str(message)}})
        return 2

    errors = _check_contracts(path, context_path)
    if contracts.fatal(errors):
        return bad("; ".join(str(e.message) for e in errors))
    top_k = normalize_top_k(top_k)
    team, flags = _load_team(path)
    context_raw, _ = _raw_json(context_path, json_only=True) if context_path else ({}, [])
    context = _load_context(context_path) if context_path else None
    fmt = (context.format if context else None) or team.format
    if fmt not in {"single", "double"}:
        return bad("set team.format or context.format to single|double")
    if team.format and context and context.format and team.format != context.format:
        return bad("team and context battle formats disagree")
    stamp, warnings = _stamp(context, team)
    source = team.to_dict()
    try:
        scope = _load_battle_scope(opponents_path, conditions_path)
        if scope.get("opponents"):
            _opponent_rows(scope["opponents"], fmt)
    except (ValueError, TypeError, DexUnavailable) as exc:
        return bad(exc)
    focus, _ = _raw_json(focus_path, json_only=True) if focus_path else ({}, [])
    try:
        team_review.check_focus(focus)
    except ValueError as exc:
        return bad(exc)
    focus, focus_flags = _nested_input(focus, "focus")
    if focus is None:
        return 2
    flags += focus_flags
    candidate, changes = None, None
    if trial:
        raw, errs = _raw_json(changes_path, json_only=True)
        if raw is None:
            return bad("try requires --changes with a JSON list of {slot,set}")
        try:
            tentative, _ = team_review.apply_changes(source, raw)
            errs = contracts.check_team(tentative)
            if contracts.fatal(errs):
                return bad("; ".join(str(e.message) for e in errs))
            normalized, nested_flags = _nested_input({"changes": raw}, "changes")
            if normalized is None:
                return 2
            flags += nested_flags
            candidate, changes = team_review.apply_changes(source, normalized["changes"])
            candidate, candidate_flags = _canon_team_json(candidate)
            flags += candidate_flags
            for row in changes:
                row["after"] = candidate["pokemon"][row["slot"]]
        except (ValueError, TypeError) as exc:
            return bad(exc)

    # Shared closures freeze the opponent pool and reuse damage across both copies.
    evaluate = _review_evaluator(context_raw or {}, context, stamp, fmt, top_k, focus, scope)
    try:
        before = evaluate(source)
        if trial:
            after = evaluate(candidate)
            result = {"kind": "try", "status": after["status"], "changes": changes,
                      "candidate_team": candidate, "before": team_review.seal(before),
                      "after": team_review.seal(after), "comparison": team_review.compare(before, after, changes),
                      "note": "Explicit copy edits only; original inputs remain untouched. "
                              "Trial evaluation does not replace the UEP proposal/answer gate."}
        else:
            result = before
    except (DexUnavailable, MetaUnavailable, NcpUnavailable) as exc:
        emit_json({"ok": False, "query": kind, "error": {"code": "engine_unavailable", "message": str(exc)}})
        return 2
    result["name_resolution"] = flags
    result["warnings"] = warnings
    result["top_k_requested"] = top_k
    result = team_review.seal(result)
    emit_json(result, view=view, projector=team_review.project)
    return 1 if fail_on_verdict and result["status"] != "ready" else 0


def cmd_select(path: str, fmt: str, context_path: str | None,
               with_check: bool = False, top_k: int = 8, opponents_path: str | None = None,
               conditions_path: str | None = None) -> int:
    contract_errs = _check_contracts(path, context_path)
    _emit_contract_errs(contract_errs, fmt)
    if contracts.fatal(contract_errs):
        print(i18n.t('team_refuse_select'), file=sys.stderr)
        return 2
    team, name_flags = _load_team(path)
    _emit_name_flags(name_flags)
    context = _load_context(context_path) if context_path else None
    check_context = None
    try:
        scope = _load_battle_scope(opponents_path, conditions_path)
    except (ValueError, TypeError, DexUnavailable) as exc:
        emit_json({"ok": False, "error": {"code": "bad_input", "message": str(exc)}})
        return 2
    with_check = with_check or bool(opponents_path or conditions_path)
    try:
        # Selection enumerates picks of the REGISTERED team; it must not present them as "legal"
        # without a legality check. Validate first and attach the verdict so an invalid/unknown
        # team is flagged, not silently enumerated as if legal (audit 2026-06-21).
        legality = validate(team, context)
        # Opt-in per-lineup CHECK coverage (--with-check): one matchup battery, rolled up per combo as
        # OBJECTIVE facts (never a lineup ranking). Default off keeps `select` ncp-free (design §17).
        check_grid = None
        if with_check:
            check_grid, check_context = _select_check_grid(team, context, top_k, scope)
        out = run_select(team.to_dict(), fmt=(context.format if context else None),
                         dex_fn=lookup_pokemon, item_fn=lookup_items,
                          legality_status=legality.status,
                          keep_mega=(context.keep_mega if context else None),
                          check_grid=check_grid, check_context=check_context)
    except (DexUnavailable, ValueError) as e:
        print(i18n.t('team_sibling_dex_unavailable', e=e), file=sys.stderr)
        return 2
    stamp, env_warn = _stamp(context, team)
    out["environment"] = stamp
    out["legality"] = {"status": legality.status, "valid": legality.valid,
                       "confidence": legality.confidence, "errors": legality.errors}
    warns = list(env_warn)
    if legality.status != "valid":
        warns.append(f"registered team legality is '{legality.status}' (not certified valid) — "
                     "these pick subsets assume a legal team; resolve validation first.")
    if with_check and check_grid is None and check_context and check_context.get("unavailable"):
        warns.append(check_context["unavailable"])      # requested, but a sibling was down
    if warns:
        out["warnings"] = (out.get("warnings") or []) + warns
    if fmt == "json":
        emit_json(canonical_spreads(out))
    else:
        print(_env_header(stamp, env_warn) + "\n")
        if legality.status != "valid":
            print("> ⚠️ " + i18n.t("team_select_legality_warn", status=legality.status) + "\n")
        print(format_selection_md(out))
    return 0


def cmd_matchup(path: str, fmt: str, context_path: str | None, top_k: int,
                as_checks: bool = False, view: str = "full", opponents_path: str | None = None,
                conditions_path: str | None = None) -> int:
    top_k = normalize_top_k(top_k)
    contract_errs = _check_contracts(path, context_path)
    _emit_contract_errs(contract_errs, fmt)
    if contracts.fatal(contract_errs):
        print(i18n.t('team_refuse_matchup'), file=sys.stderr)
        return 2
    team, name_flags = _load_team(path)
    _emit_name_flags(name_flags)
    context = _load_context(context_path) if context_path else None
    fmt_battle = (context.format if context and context.format else None) or team.format
    stamp, env_warn = _stamp(context, team)
    rule_scope = stamp.get("rule")
    try:
        scope = _load_battle_scope(opponents_path, conditions_path)
        rows = _opponent_rows(scope["opponents"], fmt_battle) if scope.get("opponents") else usage_top_k(fmt_battle, top_k)
    except (ValueError, TypeError) as exc:
        emit_json({"ok": False, "error": {"code": "bad_input", "message": str(exc)}})
        return 2
    except MetaUnavailable as e:
        print(i18n.t('team_meta_unavailable_matchup', e=e), file=sys.stderr)
        return 2
    # Opponent sets resolve through the M4 resolver: the real-team JOINT set (ability/item/nature/moves)
    # ⊕ the meta modal SPREAD, in ONE batched meta call (handoff §5.1). With an empty real-team library
    # this is byte-identical to the bare meta path. Memoized so the no-ncp fallback reuses it.
    sets_fn = _make_sets_fn(rule=rule_scope)
    try:
        out = run_matchup(team.to_dict(), rows, fmt=fmt_battle, dex_fn=lookup_pokemon,
                          sets_fn=sets_fn, damage_fn=damage_batch, move_fn=lookup_moves,
                          item_fn=lookup_items, conditions=scope.get("conditions"),
                          # One column per real build (see _make_variants_fn).
                          variants_fn=_make_variants_fn(rule=rule_scope))
    except NcpUnavailable as e:
        out = run_matchup(team.to_dict(), rows, fmt=fmt_battle, dex_fn=lookup_pokemon,
                          sets_fn=sets_fn, damage_fn=None, move_fn=lookup_moves,
                          item_fn=lookup_items, conditions=scope.get("conditions"),
                          variants_fn=_make_variants_fn(rule=rule_scope))
        out["warnings"] = (out.get("warnings") or []) + [i18n.Msg('team_ncp_unavailable_damage_skipped', e=e)]
    except MetaUnavailable as e:
        print(i18n.t('team_sibling_meta_unavailable', e=e), file=sys.stderr)
        return 2
    except DexUnavailable as e:
        print(i18n.t('team_sibling_dex_unavailable', e=e), file=sys.stderr)
        return 2
    out["environment"] = stamp                          # stamp/env_warn computed above (before resolving)
    out["opponent_scope"] = "stated_roster" if scope.get("opponents") else "usage_top_k"
    if env_warn:
        out["warnings"] = (out.get("warnings") or []) + env_warn
    if fmt == "json":
        emit_json(canonical_spreads(out), view=view, projector=project_matchup)
    else:
        print(_env_header(stamp, env_warn) + "\n")
        print(format_check_coverage_md(out) if as_checks else format_matchup_md(out))
    return 0


def cmd_replace(path: str, fmt: str, context_path: str | None) -> int:
    """L3 replace-impact (M3): objective before/after diff of swapping one member for a candidate.
    No 'better/worse' verdict."""
    contract_errs = _check_contracts(path, context_path)
    _emit_contract_errs(contract_errs, fmt)
    if contracts.fatal(contract_errs):
        print(i18n.t('team_refuse_replace'), file=sys.stderr)
        return 2
    team, name_flags = _load_team(path)
    _emit_name_flags(name_flags)
    context = _load_context(context_path) if context_path else None
    spec = context.replace if context else {}
    if not spec or not spec.get("member") or not spec.get("with"):
        print(i18n.t('team_replace_needs_context'), file=sys.stderr)
        return 2
    normalized, nested_flags = _nested_input({"replace": spec}, "context")
    if normalized is None:
        return 2
    spec = normalized["replace"]
    stamp, env_warn = _stamp(context, team)

    def _validate_after(after_dict: dict) -> dict:
        # Legality bottom-line: run the real validator on the prospective after-team (iron rule —
        # never present a diff on an illegal team as clean fact).
        r = validate(team_from_dict(after_dict), context)
        return {"status": r.status, "errors": r.errors_msg(), "warnings": r.warnings_msg()}

    try:
        out = run_replace(team.to_dict(), spec["member"], spec["with"],
                          dex_fn=lookup_pokemon, move_fn=lookup_moves, item_fn=lookup_items,
                          validate_fn=_validate_after)
    except DexUnavailable as e:
        print(i18n.t('team_sibling_dex_unavailable', e=e), file=sys.stderr)
        return 2
    out["environment"] = stamp
    if name_flags or nested_flags:
        out["name_resolution"] = name_flags + nested_flags
    if env_warn:
        out["warnings"] = (out.get("warnings") or []) + env_warn
    if fmt == "json":
        emit_json(canonical_spreads(out))
    else:
        print(_env_header(stamp, env_warn) + "\n")
        print(format_replace_impact_md(out))
    return 0


def cmd_fill(path: str, fmt: str, context_path: str | None, trial_slot: int | None = None) -> int:
    """L3 candidate retrieval (M3): given a structured `need` (build-context), return the candidate
    pool in multiple explicit views. No composite score / single best pick."""
    contract_errs = _check_contracts(path, context_path)
    _emit_contract_errs(contract_errs, fmt)
    if contracts.fatal(contract_errs):
        print(i18n.t('team_refuse_fill'), file=sys.stderr)
        return 2
    team, name_flags = _load_team(path)
    _emit_name_flags(name_flags)
    context = _load_context(context_path) if context_path else None
    if not context or not context.need:
        print(i18n.t('team_fill_needs_context'), file=sys.stderr)
        return 2
    if context.need.get("answer"):
        normalized, _ = _nested_input({"need": context.need}, "context")
        if normalized is None:
            return 2
        context.need = normalized["need"]
    fmt_battle = (context.format if context.format else None) or team.format
    stamp, env_warn = _stamp(context, team)
    rule_scope = stamp.get("rule")
    try:
        answer_cache = None
        if context.need.get("answer"):
            if rule_scope != environment.CURRENT_RULE:
                raise counters.QueryError("answer uses only the current validated opponent cache", query="fill.answer")
            answer_cache = oppcache.load_cache(fmt_battle, rule_scope)
            if answer_cache is None:
                raise counters.QueryError("no current opponent cache for this format", code="not_found", query="fill.answer")
        retained = team.to_dict()
        if trial_slot is not None:
            if type(trial_slot) is not int or not 0 <= trial_slot < len(retained["pokemon"]):
                raise ValueError("slot must identify an existing zero-based member")
            retained["pokemon"] = [m for i, m in enumerate(retained["pokemon"]) if i != trial_slot]
        out = run_fill(retained, context.need, fmt=fmt_battle,
                       dex_fn=lookup_pokemon, ranking_fn=usage_top_k, repset_fn=repset.load_teams,
                       move_fn=lookup_moves, item_fn=lookup_items,
                       rule=rule_scope, owned=context.owned, owned_only=context.owned_only,
                       avoid=context.avoid_species, locked=context.locked,
                       avoid_items=context.avoid_items,
                       meta_conformance=context.meta_conformance, answer_cache=answer_cache)
        if trial_slot is not None:
            import fill_trials
            import team_review
            def structural_review(value):
                return team_review.review(value, context={"format": fmt_battle}, environment=stamp,
                    validate_fn=lambda t: _validate_json(t, context),
                    diagnose_fn=lambda t: _diagnostic_facts(team_from_dict(t), context),
                    matchup_fn=lambda t: {"unavailable": "structural-only candidate query"},
                    select_fn=lambda *args: None)
            out = fill_trials.attach(out, team.to_dict(), trial_slot, dex_fn=lookup_pokemon,
                                     move_fn=lookup_moves, review_fn=structural_review)
    except (counters.QueryError, oppcache.StaleCacheError, ValueError) as exc:
        emit_json({"ok": False, "query": "fill.answer", "error": {"code": "bad_input", "message": str(exc)}})
        return 2
    except MetaUnavailable as e:
        print(i18n.t('team_meta_unavailable_fill', e=e), file=sys.stderr)
        return 2
    except DexUnavailable as e:
        print(i18n.t('team_sibling_dex_unavailable', e=e), file=sys.stderr)
        return 2
    out["environment"] = stamp
    if env_warn:
        out["warnings"] = (out.get("warnings") or []) + env_warn
    if fmt == "json":
        emit_json(canonical_spreads(out))
    else:
        print(_env_header(stamp, env_warn) + "\n")
        print(format_fill_md(out))
    return 0


def cmd_context_audit(team_path: str | None, fmt: str, context_path: str | None,
                      fail_on_verdict: bool = False) -> int:
    """UEP P2 front gate: audit the STRUCTURED intent (build-context, + optional team) into pre-build
    facts + three-level gaps + the audit receipt (capability-chain start). Unlike the operators, the
    audit REPORTS contract errors as facts instead of refusing on them — it IS the checker; only an
    unparseable input file refuses."""
    if not context_path:
        print(i18n.t('team_ctx_audit_needs_context'), file=sys.stderr)
        return 2
    raw_ctx, errs = _raw_json(context_path, json_only=True)
    if raw_ctx is None:
        _emit_contract_errs(errs, fmt)
        return 2
    contract_errs = contracts.check_context(raw_ctx)
    team_obj = None
    team_dict = None
    if team_path:
        raw_team, terrs = _raw_json(team_path)
        if raw_team is None and terrs:                 # malformed JSON (Showdown text is fine)
            _emit_contract_errs(terrs, fmt)
            return 2
        team_contract_errs = []
        if raw_team is not None:
            team_contract_errs = contracts.check_team(raw_team)
            contract_errs = contract_errs + team_contract_errs
        if not contracts.fatal(team_contract_errs):
            team_obj, name_flags = _load_team(team_path)
            _emit_name_flags(name_flags)
            team_dict = team_obj.to_dict()
    # Stamp from a MINIMAL sane view: the raw context may be arbitrarily mis-typed (that is exactly
    # what the audit reports), so never feed it wholesale into context_from_dict — a list-valued
    # `need`/`replace` or a non-dict top level would crash the auditor on the inputs it exists for
    # (self-audit 2026-07-02).
    rd = raw_ctx if isinstance(raw_ctx, dict) else {}
    stamp_ctx = context_from_dict({k: rd.get(k) for k in ("season", "rule", "format")
                                   if isinstance(rd.get(k), str)})
    stamp, env_warn = _stamp(stamp_ctx, team_obj)

    try:
        out = context_audit.audit(raw_ctx, team_dict, resolve_fn=_dex_resolve, item_fn=_dex_items,
                                  normalize_fn=lambda c: nested_names.normalize(c, "context", resolve_names),
                                  battle_team_fn=lambda t: query_identity.battle_view([t])[0][0],
                                  contract_errors=[e.to_dict() for e in contract_errs], env=stamp)
    except DexUnavailable as exc:
        emit_json({"ok": False, "error": {"code": "engine_unavailable", "message": str(exc)}})
        return 2
    _emit_name_flags(out.get("name_resolution") or [])
    out["environment"] = stamp
    if env_warn:
        out["warnings"] = env_warn
    if fmt == "json":
        return _emit_gate(out, fail_on_verdict)
    else:
        status = _gate_status(out)
        print(i18n.t('team_gate_status', verdict=status['verdict'], next_step=status['next_step']))
        print(_env_header(stamp, env_warn) + "\n")
        print(context_audit.format_audit_md(out))
    return 1 if fail_on_verdict and status["verdict"] == "needs_input" else 0


def cmd_landscape(fmt: str, game_format: str | None, season: str | None, context_path: str | None,
                  species_filter: list[str] | None, max_cores: int | None,
                  aspects: list[str] | None = None, no_cache: bool = False,
                  population_scope: str | None = None) -> int:
    """UEP P4: structural distributions over one format's real-team library, aggregated through the
    shared `profile` vocabulary. Counts + share + sample only — never a ranking or a recommended
    core. The whole library's dex facts are prefetched in THREE sibling batches (species/moves/
    items), so profiling thousands of teams costs no per-team subprocess. `aspects` opts into the
    full offense/defense presence distributions (a view opt-in — the profiles are computed anyway)."""
    # Enum-gate BOTH entry doors (argparse choices covers the CLI, the session spec arrives
    # stringly) — an off-vocabulary aspect is refused, never silently dropped. Validated BEFORE the
    # game_format check so the session path rejects a bad aspect independently of a missing format,
    # matching argparse's format-independent `choices=` (audit 2026-07-03).
    if population_scope not in (None, "high"):
        emit_json({"ok": False, "query": "landscape", "error": {
            "code": "bad_input", "message": "landscape accepts --scope high only; omit it for the ordinary library scope"}})
        return 1
    aspects = [a for a in (aspects or []) if a]
    bad_aspects = sorted(set(aspects) - set(landscape.ASPECTS))
    if bad_aspects:
        print(i18n.t('team_landscape_bad_aspect', bad=", ".join(bad_aspects),
                     allowed=", ".join(landscape.ASPECTS)), file=sys.stderr)
        return 2
    if game_format not in ("single", "double"):
        # Enum-gate BOTH entry doors like `aspects` above and `cmd_intake`: argparse `choices=` covers
        # the CLI, but the session spec arrives stringly — an off-vocabulary game_format ("weird",
        # "Single", "singles") must be a loud refusal, never a silent rc=0 empty landscape that the
        # orchestrator misreads as "this metagame has no teams" (audit 2026-07-06).
        print(i18n.t('team_landscape_needs_format'), file=sys.stderr)
        return 2
    if max_cores is not None and (type(max_cores) is not int or max_cores < 0):
        print("landscape --limit must be a non-negative integer", file=sys.stderr)
        return 2
    # The operator gate other cmds run: a malformed / off-enum context is refused, never silently
    # half-used (external audit 2026-07-02: meta_conformance:"bogus" quietly fell back to
    # common_first and a top-level array crashed the loader).
    context, season, warns, rc = _library_query_preamble(
        context_path, game_format, season, 'team_refuse_landscape')
    if rc:
        return rc
    raw_filter = list(species_filter or [])
    order = "common_first"
    if context:
        raw_filter += [s for s in (context.locked + context.prefer) if s]
        if context.meta_conformance == "off_meta":
            order = "rare_first"
    # Format-aware filter resolution (repset's rule): a doubles Mega is stored as BASE species +
    # stone, so 'Mega Charizard Y' must filter as {species: Charizard, item: Charizardite Y} — the
    # canonical Mega name matches zero doubles teams (external audit 2026-07-02).
    filter_members: list[dict] = []
    seen_filters: set[tuple] = set()
    for name in raw_filter:
        resolved, item_filter, fwarns = _canonicalize_repset_species(name, game_format)
        warns += fwarns
        key = (resolved, item_filter)
        if key not in seen_filters:
            seen_filters.add(key)
            filter_members.append({"species": resolved, "item": item_filter})
    scope = _library_scope(game_format, season, context)
    if population_scope == "high" and (season or scope.get("kind") != "rule"
                                        or scope.get("rule") != environment.CURRENT_RULE):
        emit_json({"ok": False, "query": "landscape", "error": {
            "code": "bad_input", "message": "landscape --scope high describes native current-rule records; an explicit season or another rule conflicts"}})
        return 1

    def authority():
        if population_scope == "high":
            with repset.handover.native_only():
                return landscape_cache.signature(repset.data_dir(), game_format, scope["rule"])
        return landscape_cache.signature(repset.data_dir(), game_format,
                                         scope.get("rule") or environment.CURRENT_RULE)

    population = None
    try:
        snapshot = authority()
        if population_scope == "high":
            with repset.handover.native_only():
                teams = _scope_teams(game_format, scope, True)
            teams, population = landscape.high_sample(teams)
        else:
            teams = _scope_teams(game_format, scope, True)
        if authority() != snapshot:
            raise landscape_cache.AuthorityChanged("landscape sources changed while loading teams; rerun the query")
    except (landscape_cache.AuthorityChanged, OSError) as exc:
        emit_json({"ok": False, "query": "landscape", "error": {"code": "authority_mismatch", "message": str(exc)}})
        return 1
    options = {"format": game_format, "scope": scope, "filter_members": filter_members, "order": order,
               "max_cores": max_cores if max_cores is not None else landscape.MAX_CORES, "aspects": aspects}
    if population is not None:
        options["population"] = population

    def compute():
        dex_fn, move_fn, item_fn = _dex_fact_fns(teams, {f["species"] for f in filter_members})
        result = run_landscape(teams, fmt=game_format, dex_fn=dex_fn, move_fn=move_fn, item_fn=item_fn,
                               filter_members=filter_members, order=order, max_cores=options["max_cores"], aspects=aspects)
        if population is not None:
            result["population"] = population
            result["notes"].append(
                "High-performance STRUCTURAL sample: native current-rule shipped records passing "
                "Top Meta's performance criteria. Counts are library records, NOT unique players "
                "or Top Meta counting units: public rows lack cross-partition identity and may omit "
                "qualifying achievements from other source observations. Unknown performance is "
                "excluded; no fallback to the ordinary library or cross-rule handover. Structural "
                "distributions use all eligible records; species filters narrow observed_cores only.")
        return result

    try:
        out, cache_info = landscape_cache.evaluate(
            teams, options, compute, enabled=not no_cache,
            authority=authority, expected=snapshot)
    except (landscape_cache.AuthorityChanged, OSError) as exc:
        emit_json({"ok": False, "query": "landscape", "error": {"code": "authority_mismatch", "message": str(exc)}})
        return 1
    except DexUnavailable as e:
        print(i18n.t('team_sibling_dex_unavailable', e=e), file=sys.stderr)
        return 2
    out["cache"] = cache_info
    # Historical-season provenance mirrors repset (audit 2026-06-26): the library partition IS the
    # data's provenance, so resolve with data_season= — the _stamp(context,...) path would warn
    # "computed against current base" about data that genuinely came from that partition.
    stamp, env_warn = _scope_stamp(scope, teams, context)
    out["environment"] = stamp
    out["query"] = {"game_format": game_format, **_scope_query(scope, teams),
                    "filter": (out.get("core_basis") or {}).get("filter") or [], "order": order,
                    "aspects": aspects}
    if population_scope is not None:
        out["query"]["scope"] = population_scope
    if env_warn or warns:
        out["warnings"] = list(env_warn) + warns
    if fmt == "json":
        emit_json(out)
    else:
        print(_env_header(stamp, list(env_warn) + warns) + "\n")
        print(landscape.format_landscape_md(out))
    return 0


def _fallback_fn(game_format: str, rule: str | None, current_teams: list[dict]):
    """frame's fallback-ladder input: the fallback table's recipe for an anchor no current-rule team
    carries, resolved into the evidence pool frame computes over. Older-rule partitions load through
    the same process cache as the rule pool. No table for this rule (not built yet, or a historical
    scope) -> None, and frame falls to its generic rung."""
    import fallback_frames

    def resolve(filter_members: list[dict]) -> dict | None:
        table = fallback_frames.load_table(game_format, rule) if rule else None
        if not table or len(filter_members) != 1:
            return None
        f = filter_members[0]
        entries = []
        for key, recipe in (table.get("anchors") or {}).items():
            species, sep, item = key.partition("|")
            if sep and species:
                entries.append(({"species": species, "item": item or None}, recipe))
        identities = query_identity.Forms([f] + [entry for entry, _ in entries])
        wanted = identities(f)
        matches = [recipe for entry, recipe in entries
                   if wanted is not None and identities(entry) == wanted]
        # Legacy table keys are species|item. Only equivalent actual forms may share a recipe;
        # conflicting recipes remain unavailable rather than choosing an unrelated evidence pool.
        recipe = matches[0] if matches and all(r == matches[0] for r in matches) else None
        if not recipe:
            return None
        pool, _ = query_identity.battle_view(current_teams)
        resolved = fallback_frames.resolve(
            recipe, fmt=game_format, rule=rule, current_teams=pool,
            load_season=lambda season: repset.cached_teams(game_format, season))
        if resolved and resolved["source"] == "history":
            # frame's fact tables are prefetched over the species/moves/items the current library
            # uses; an older pool can carry ones it does not, which would otherwise profile blind.
            try:
                resolved["fact_fns"] = _dex_fact_fns(resolved["pool"], {f["species"]})
            except DexUnavailable:
                return None
        return resolved
    return resolve


def _seen_teams(path: str | None, fmt: str | None) -> list[list[str]]:
    if path is None:
        return []
    value, errors = _raw_json(path, json_only=True)
    if value is None:
        raise ValueError("cannot read seen-teams JSON")
    teams = value.get("teams") if isinstance(value, dict) and "teams" in value else value
    teams = teams if isinstance(teams, list) else [teams]
    canonical = []
    for raw in teams:
        if contracts.fatal(contracts.check_team(raw)):
            raise ValueError("seen-teams must contain team-json objects, singly or in a list/teams wrapper")
        if raw.get("format") not in (None, fmt):
            raise ValueError("seen-teams battle format differs from this query")
        doc, flags = _canon_team_json(raw)
        _emit_name_flags(flags)
        canonical.append(doc)
    forms = query_identity.Forms(query_identity.members(canonical))
    result = []
    for doc in canonical:
        row = []
        for member in doc["pokemon"]:
            if not (forms.facts.get(member["species"]) or {}).get("found"):
                raise ValueError("seen-teams contains an unresolved species")
            if member.get("item") and not (forms.items.get(member["item"]) or {}).get("found"):
                raise ValueError("seen-teams contains an unresolved item")
            form = forms(member)
            if not form:
                raise ValueError("seen-teams has an unproved battle-form identity")
            row.append(form)
        result.append(row)
    return result


def cmd_frame(fmt: str, game_format: str | None, season: str | None, context_path: str | None,
              audit_receipt_path: str | None, draw: int | None = None, seed: int | None = None,
              seen_teams_path: str | None = None) -> int:
    """UEP P4.5 assembly front-door: emit DATA-GROUNDED skeletons (repset-backed core candidates +
    structural facts) the AI assembles ON, so `[assemble]` is not hand-waved from the training prior.
    Consumes context-audit's audit_receipt (the chain link — recomputed here, mismatch refuses) and
    emits a frame_receipt the slate gate binds each candidate's core-bearer sets against."""
    try:
        diversity.validate(draw, seed, seen=seen_teams_path is not None)
        shown = _seen_teams(seen_teams_path, game_format)
    except (ValueError, DexUnavailable) as exc:
        emit_json({"kind": "frame", "refused": {"code": "bad_diversity_input", "reason": str(exc)}})
        return 2
    if game_format not in ("single", "double"):
        print(i18n.t('team_frame_needs_format'), file=sys.stderr)
        return 2
    if not context_path:
        print(i18n.t('team_ctx_audit_needs_context'), file=sys.stderr)
        return 2
    # Raw context (as written) is the fingerprint's binding target; the canonicalized one drives anchor
    # resolution + the order knob — exactly the split slate uses for its receipt recompute.
    raw_ctx, rerrs = _raw_json(context_path, json_only=True)
    if raw_ctx is None:
        _emit_contract_errs(rerrs, fmt)
        return 2
    context, season, warns, rc = _library_query_preamble(
        context_path, game_format, season, 'team_refuse_frame')
    if rc:
        return rc
    # Chain link: consume context-audit's receipt (a full context-audit output OR a bare receipt object)
    # and recompute its fingerprint over THIS context — proves the audit ran on exactly this intent.
    if not audit_receipt_path:
        print(i18n.t('team_frame_bad_audit_receipt'), file=sys.stderr)
        return 2
    raw_receipt, aerrs = _raw_json(audit_receipt_path, json_only=True)
    if raw_receipt is None:
        _emit_contract_errs(aerrs, fmt)
        return 2
    receipt = (raw_receipt.get("audit_receipt")
               if isinstance(raw_receipt, dict) and isinstance(raw_receipt.get("audit_receipt"), dict)
               else raw_receipt)
    provided = receipt.get("fingerprint") if isinstance(receipt, dict) else None
    if not provided:
        print(i18n.t('team_frame_bad_audit_receipt'), file=sys.stderr)
        return 2
    stamp, env_warn0 = _stamp(context, None)
    expected = context_audit.context_fingerprint(raw_ctx, stamp)
    if provided != expected:
        out = {"kind": "frame", "refused": {"code": "audit_receipt_mismatch",
               "expected_fingerprint": expected, "provided_fingerprint": provided}}
        _emit_gate(out)
        print(i18n.t('team_frame_audit_mismatch'), file=sys.stderr)
        return 2
    # Anchor = locked + prefer (exact actual forms); order = meta_conformance knob.
    raw_filter = [s for s in ((context.locked + context.prefer) if context else []) if s]
    order = "rare_first" if (context and context.meta_conformance == "off_meta") else "common_first"
    filter_members: list[dict] = []
    seen_filters: set[tuple] = set()
    for name in raw_filter:
        resolved, item_filter, fwarns = _canonicalize_repset_species(name, game_format)
        warns += fwarns
        key = (resolved, item_filter)
        if key not in seen_filters:
            seen_filters.add(key)
            filter_members.append({"species": resolved, "item": item_filter})
    scope = _library_scope(game_format, season, context)
    teams = _scope_teams(game_format, scope)
    try:
        dex_fn, move_fn, item_fn = _dex_fact_fns(teams, {f["species"] for f in filter_members})
    except DexUnavailable as e:
        print(i18n.t('team_sibling_dex_unavailable', e=e), file=sys.stderr)
        return 2
    out = frame.frame_from_teams(teams, fmt=game_format, dex_fn=dex_fn, move_fn=move_fn,
                                 item_fn=item_fn, filter_members=filter_members, order=order,
                                 fallback_fn=_fallback_fn(game_format, scope.get("rule"), teams))
    out = canonical_spreads(out)
    out["frame_receipt"] = frame.make_receipt(provided, out["skeletons"], out.get("anchor"), game_format)
    if draw is not None:
        from mega import form_base_species
        names = {c["species"] for s in out["skeletons"] for c in s.get("core_candidates") or [] if c.get("species")}
        names.update(n for row in shown for n in row)
        facts = lookup_pokemon(sorted(names))
        out["draw"] = diversity.draw_frames(out["skeletons"], draw, seed, shown,
            group_fn=lambda name: form_base_species(name, facts.get(name)) or name)
    # Historical-provenance stamp mirrors landscape/repset: the library partition IS the data's season.
    stamp2, env_warn = _scope_stamp(scope, teams, context)
    out["environment"] = stamp2
    if env_warn or warns:
        out["warnings"] = list(env_warn) + warns
    if fmt == "json":
        return _emit_gate(out)
    else:
        status = _gate_status(out)
        print(i18n.t('team_gate_status', verdict=status['verdict'], next_step=status['next_step']))
        print(_env_header(stamp2, list(env_warn) + warns) + "\n")
        print(frame.format_frame_md(out))
    return 0


def cmd_intake(game_format: str | None, onboarding: bool = False, context_path: str | None = None,
               step: bool = False, answered: list[str] | None = None) -> int:
    """UEP P3: emit the question catalog (JSON only, ai-orchestration-facing). Three surfaces
    (design §19.6): the default MENU (full catalog for steady-state <=3-per-round gap refinement,
    which questions = context-audit gaps + the AI's judgment); --onboarding, the new-build guided
    walk OVERVIEW (the base set to complete ONCE, minus what --context already answers); and --next,
    the batch DRIVER that returns the next 1-3 related questions (numbered) to actually ask the walk
    one step at a time (loop with --answered). --game-format only narrows format-tagged options (singles
    has no Tailwind option, doubles no hazards option). Language-invariant: text/label/example ship
    zh/ja/en."""
    if game_format not in (None, "single", "double"):
        # the session path has no argparse choices guard: {"op":"intake","format":"json"} used to
        # produce a silently mis-filtered catalog (external audit 2026-07-03) — refuse instead.
        print(i18n.t('team_intake_bad_format', got=game_format), file=sys.stderr)
        return 2
    context = None
    if context_path:
        # presence-based covered-detection only (never parses free text); read the raw dict like
        # context-audit does, so a mis-typed context is reported rather than crashing the loader.
        raw_ctx, errs = _raw_json(context_path, json_only=True)
        if raw_ctx is None:
            _emit_contract_errs(errs, "json")
            return 2
        context = raw_ctx if isinstance(raw_ctx, dict) else None
    if step:
        out = intake_catalog_mod.next_batch(context, game_format=game_format, answered=answered)
    else:
        out = intake_catalog_mod.intake_catalog(game_format, context=context, onboarding=onboarding)
    stamp, env_warn = environment.resolve()
    out["environment"] = stamp
    if env_warn:
        out["warnings"] = list(env_warn)
    emit_json(out)
    if step and out.get("unknown_answered"):
        # `answered` is the walk's termination state — a silently dropped typo would re-ask a resolved
        # dimension and spin the loop, so fail LOUD (the unknown ids are also in the JSON above).
        print(i18n.t('team_intake_unknown_answered', ids=", ".join(out["unknown_answered"])),
              file=sys.stderr)
        return 2
    return 0


def cmd_observed(fmt: str, game_format: str | None, season: str | None, context_path: str | None,
                 species_filter: list[str] | None, order: str | None, limit: int | None,
                 seed: int | None = None, seen_teams_path: str | None = None) -> int:
    """UEP P7: observed-team retrieval — the AI-facing evidence table over the real-team library.
    Constraints ride the build-context (locked/prefer/owned/avoid, dex-canonicalized) plus ad-hoc
    --species; rows carry explicit overlap counts, the provenance evidence tier, performance fact
    tags and structural signals (profile vocabulary, computed for the returned page only).
    JSON only (AI-facing evidence, like slate/answer-audit)."""
    try:
        diversity.validate(seed=seed, seen=seen_teams_path is not None, order=order)
        shown = _seen_teams(seen_teams_path, game_format)
    except (ValueError, DexUnavailable) as exc:
        emit_json({"ok": False, "query": "observed", "error": {"code": "bad_diversity_input", "message": str(exc)}})
        return 2
    if game_format not in ("single", "double"):
        # Same enum-gate as cmd_landscape/cmd_intake: the session path has no argparse `choices=`, so
        # an off-vocabulary game_format must refuse loudly, not return a silent rc=0 empty page that
        # reads as "no observed teams" (audit 2026-07-06).
        print(i18n.t('team_observed_needs_format'), file=sys.stderr)
        return 2
    if limit is not None and limit < 0:
        print(i18n.t('team_observed_bad_limit', limit=limit), file=sys.stderr)
        return 2
    context, season, warns, rc = _library_query_preamble(
        context_path, game_format, season, 'team_refuse_observed')
    if rc:
        return rc
    constraints: dict[str, Any] = {}
    if context:
        constraints = {"locked": context.locked, "prefer": context.prefer,
                       "owned": context.owned, "avoid_species": context.avoid_species,
                       "avoid_items": context.avoid_items, "owned_only": context.owned_only}
    name_flags = []
    if species_filter:
        # Resolution failures are NEVER silent (landscape parity): an unresolved alias or a dex
        # outage must read as a name problem, not as "the library has no such species"
        # (self-audit 2026-07-03).
        names = [s for s in species_filter if s]
        try:
            recs = resolve_names(names, "pokemon", fuzzy=True)
            canon = {q: r["canonical"] for q, r in recs.items()
                     if r.get("ok") and r.get("canonical")}
            name_flags = query_identity.corrections({("pokemon", q): r for q, r in recs.items()})
            for miss in [n for n in names if n not in canon]:
                warns.append(i18n.Msg('team_dex_unresolved_library', species=repr(miss)))
        except DexUnavailable:
            canon = {}
            warns.append(i18n.Msg('team_dex_down_query_library'))
        constraints["species"] = [canon.get(s, s) for s in names]
    scope = _library_scope(game_format, season, context)
    teams = _scope_teams(game_format, scope)
    if not teams:
        # an empty partition usually means a season typo — a well-formed zero-row table with no
        # signal would read as "no evidence exists" (self-audit 2026-07-03).
        warns.append(i18n.Msg('team_observed_empty_partition',
                              season=scope.get("season") or ",".join(_scope_seasons(teams, scope)) or scope.get("rule"),
                              fmt=game_format))

    def profile_batch_fn(page: list[dict]) -> list[dict]:
        try:
            dex_fn, move_fn, item_fn = _dex_fact_fns(page)
        except DexUnavailable:
            return []                                  # honest degrade: rows ship without signals
        return [team_profile.profile(t, dex_fn=dex_fn, move_fn=move_fn, item_fn=item_fn)
                for t in page]

    extra = [{"species": name} for key in ("locked", "prefer", "species", "avoid_species")
             for name in constraints.get(key) or []]
    try:
        identities = query_identity.Forms(query_identity.members(teams, extra))
    except DexUnavailable as exc:
        emit_json({"ok": False, "query": "observed", "error": {"code": "engine_unavailable", "message": str(exc)}})
        return 1
    if any(not (identities.facts.get(m["species"]) or {}).get("found") for m in extra):
        emit_json({"ok": False, "query": "observed", "error": {"code": "unknown_pokemon",
                   "message": "one or more battle-form constraints could not be resolved; no retrieval was performed"}})
        return 1
    out = observed.retrieve(teams, fmt=game_format, constraints=constraints,
                            profile_batch_fn=profile_batch_fn, order=order,
                            limit=(limit if limit is not None else 10), identity_fn=identities, shown=shown, seed=seed)
    out["identity_gaps"] = identities.gaps
    if name_flags:
        out["name_resolution"] = name_flags
    stamp, env_warn = _scope_stamp(scope, teams, context)
    out["environment"] = stamp
    out.update(_scope_query(scope, teams))
    if env_warn or warns:
        out["warnings"] = list(env_warn) + warns
    emit_json(out)
    return 0


def _topmeta_ingame(game_format: str) -> dict:
    """The in-game snapshot through the meta CLI (ranking + every detail row in one batch), panel
    names resolved through the dex in one strict batch per kind."""
    rc, raw = metalink._meta_run(["ranking", "--format", game_format, "--limit", "1000",
                                  "--output", "json"])
    if rc != 0 or not raw.strip():
        raise MetaUnavailable("meta ranking query failed")
    ranking = json.loads(raw)
    names = [r["pokemon_en"] for r in ranking.get("rows") or [] if r.get("pokemon_en")]
    details = [d for d in metalink._details_batch(names, game_format).values() if d]

    def resolve_batch(kind: str, values: list[str]) -> dict[str, str | None]:
        rows = resolve_names(values, kind, fuzzy=False) if values else {}
        return {v: (r.get("canonical") if r.get("ok") else None) for v, r in rows.items()}

    return topmeta.ingame_from_meta(ranking, details, resolve_batch)


def cmd_topmeta(game_format: str | None, scope: str | None, species: list[str] | None) -> int:
    """Top Meta: one scope of the shipped table joined with the in-game snapshot (JSON only)."""
    scope = scope or "season"
    if game_format not in ("single", "double") or scope not in topmeta.SCOPES:
        emit_json({"ok": False, "query": "topmeta", "error": {
            "code": "bad_input", "message": "topmeta needs --game-format single|double and "
                                            "--scope season|rule|high"}})
        return 2
    rule = environment.CURRENT_RULE
    table = topmeta.load_table(game_format, rule)
    if table is None:
        emit_json({"ok": False, "query": "topmeta", "error": {
            "code": "not_found",
            "message": f"no Top Meta table for {rule}/{game_format}"}})
        return 0
    try:
        ingame = _topmeta_ingame(game_format)
    except (MetaUnavailable, ValueError) as exc:
        emit_json({"ok": False, "query": "topmeta", "error": {
            "code": "engine_unavailable", "message": str(exc)}})
        return 1
    records = None
    if species:
        resolved = resolve_names(list(dict.fromkeys(species)), "pokemon", fuzzy=True)
        records = [{"query": name, **(resolved.get(name) or {})} for name in species]
    wanted, queries = topmeta.resolve_queries(table, scope, records, ingame)
    out = topmeta.compare(table, scope, ingame, species=wanted)
    out["data_granularity"] = "species_aggregate"
    if records is not None:
        out["queries"] = queries
    out["environment"], _ = environment.resolve(data_rule=rule, data_seasons=out["partitions"])
    emit_json(out)
    return 1 if any((q.get("error") or {}).get("code") == "unsupported_mechanic" for q in queries) else 0


def cmd_type_matchup(attack_type: str | None, pokemon: str | None,
                     types: list[str] | None, ability: str | None) -> int:
    try:
        result = team_facts.type_matchup(attack_type=attack_type, pokemon=pokemon, types=types,
                                         ability=ability, resolve_fn=resolve_names,
                                         pokemon_fn=lookup_pokemon)
    except team_facts.FactInputError as exc:
        emit_json(exc.result)
        return 0 if exc.result["error"]["code"] == "not_found" else 1
    except DexUnavailable as exc:
        emit_json({"ok": False, "query": "type-matchup",
                   "error": {"code": "engine_unavailable", "message": str(exc)}})
        return 1
    emit_json(result)
    return 0


def cmd_rules_facts() -> int:
    try:
        result = team_facts.rules_facts()
    except (rules.UnsupportedRulesetError, RuntimeError) as exc:
        emit_json({"ok": False, "query": "rules-facts",
                   "error": {"code": "authority_mismatch", "message": str(exc)}})
        return 1
    emit_json(result)
    return 0


def cmd_slate_init(path: str, context_path: str | None, audit_path: str | None,
                   frame_path: str | None = None, intake_path: str | None = None) -> int:
    if not context_path or not audit_path:
        emit_json({"kind": "slate-init", "refused": {"code": "missing_inputs",
                  "reason": "slate-init requires candidates, --context and --audit-receipt"}})
        return 2
    values = []
    for source in (path, context_path, audit_path, frame_path, intake_path):
        if not source:
            values.append(None)
            continue
        value, errors = _raw_json(source, json_only=True)
        if value is None:
            _emit_contract_errs(errors, "json")
            return 2
        values.append(value)
    spec, raw_ctx, audit, frame_out, intake_out = values
    errors = contracts.check_context(raw_ctx)
    if contracts.fatal(errors):
        _emit_contract_errs(errors, "json")
        return 2
    context = _canonicalize_context(context_from_dict(raw_ctx))
    stamp, _ = _stamp(context, None)
    receipt = audit.get("audit_receipt", audit) if isinstance(audit, dict) else audit
    try:
        result = assembly.assemble(spec, raw_ctx, receipt, context_audit.context_fingerprint(raw_ctx, stamp),
                                   frame_out, intake_out)
    except assembly.AssemblyError as exc:
        emit_json({"kind": "slate-init", "refused": {"code": exc.code, "reason": str(exc)}})
        return 2
    emit_json(result)
    return 0


def cmd_slate_evaluate(path: str, fmt: str, top_k: int, frame_output_path: str | None = None,
                       view: str = "full", fail_on_verdict: bool = False) -> int:
    """UEP P5: the candidate fact-matrix gate. `path` = slate.json {context, audit_receipt,
    teams:[team-json…], frame_bindings?}. Consumes the context-audit receipt (recomputed here, mismatch
    refuses); runs the cheap funnel for every candidate and the matchup battery for survivors only;
    emits the order-preserving no-winner grid + slate_receipt for the answer-audit. `--frame-output`
    activates the P4.5 build-flow binding (core-bearer sets vs their repset clusters)."""
    if view not in ("full", "summary"):
        print(i18n.t("team_slate_bad_view", got=view), file=sys.stderr)
        return 2
    top_k = normalize_top_k(top_k)
    raw, errs = _raw_json(path, json_only=True)
    if raw is None:
        _emit_contract_errs(errs, fmt)
        return 2
    # Shape gate FIRST (shared predicate, single source) — the wrapper must not touch slate fields
    # before it: a top-level array crashed `.get` here while the pure function's refusal sat one
    # call later (external audit 2026-07-02).
    shape_err = slate_shape_error(raw)
    if shape_err:
        _emit_gate({"kind": "slate-evaluate", "refused": shape_err})
        print(i18n.t('team_slate_refused', code=shape_err["code"]), file=sys.stderr)
        return 2
    ctx_raw = raw.get("context") if isinstance(raw.get("context"), dict) else {}
    ctx_errs = contracts.check_context(ctx_raw)
    _emit_contract_errs(ctx_errs, fmt)
    if contracts.fatal(ctx_errs):
        print(i18n.t('team_refuse_slate'), file=sys.stderr)
        return 2
    context = _canonicalize_context(context_from_dict(ctx_raw))
    stamp, env_warn = _stamp(context, None)

    def recompute_receipt_fn(ctx: dict) -> str:
        # The P2 fingerprint is content-only (context + env) — recompute via the imported
        # construction, no dex round-trip needed for the gate check.
        return context_audit.context_fingerprint(ctx, stamp)

    def check_team_fn(team: dict) -> list:
        return [e.to_dict() for e in contracts.check_team(team)]

    def validate_fn(team_c: dict) -> dict:
        return _validate_json(team_c, context)

    def profile_fn(team_c: dict) -> dict:
        return team_profile.profile(team_c, dex_fn=lookup_pokemon,
                                    move_fn=lookup_moves, item_fn=lookup_items)

    # Constraint membership compares CANONICAL names on both sides (the raw slate context stays the
    # receipt's binding target); the canonicalized BuildContext supplies the split avoid_species too.
    normalized_goals, goal_flags = _nested_input({"goals": context.goals}, "context")
    if normalized_goals is None:
        return 2
    constraints_ctx = {"locked": context.locked, "avoid_species": context.avoid_species,
                       "avoid_items": context.avoid_items, "avoid_soft": context.avoid_soft,
                       "prefer": context.prefer, "owned_only": context.owned_only,
                       "goals": normalized_goals["goals"]}
    fmt_battle = context.format or next(
        (t.get("format") for t in raw.get("teams", []) if isinstance(t, dict) and t.get("format")),
        None)
    if not fmt_battle:
        # Metagames are never mixed — a slate that declares no battle format anywhere must not be
        # silently evaluated against the singles top-K (self-audit 2026-07-02).
        print(i18n.t('team_slate_needs_battle_format'), file=sys.stderr)
        return 2
    rule_scope = stamp.get("rule")
    rows_cache: list = []                          # the top-K ranking is loop-invariant: fetch once
    sets_fn = _make_sets_fn(rule=rule_scope)
    variants_fn = _make_variants_fn(rule=rule_scope)
    damage_fn = _make_damage_fn()                   # per-run calc memo: candidates share most members,
                                                    # so a shared (attacker,defender,move) is calc'd once

    def matchup_fn(team_c: dict) -> dict | None:
        try:
            if not rows_cache:
                from slate_comparison import target_rows
                rows_cache.extend(target_rows(usage_top_k(fmt_battle, top_k), constraints_ctx["goals"]))
            result = run_matchup(team_c, rows_cache, fmt=fmt_battle, dex_fn=lookup_pokemon,
                               sets_fn=sets_fn, damage_fn=damage_fn, move_fn=lookup_moves,
                               variants_fn=variants_fn, item_fn=lookup_items)
            result["opponent_scope"] = {
                "ranking_top_k_requested": top_k,
                "explicit_answer_targets": [g["species"] for g in constraints_ctx["goals"] if g["kind"] == "answer"],
                "additional_unranked_targets": [r["pokemon_en"] for r in rows_cache if r.get("rank") is None],
                "note": "Battery count includes the union of ranking rows and explicit answer targets; no usage rank is invented."}
            return result
        except (NcpUnavailable, MetaUnavailable, DexUnavailable):
            return None

    def selection_fn(team_c: dict, matchup_result: dict | None,
                     legality_status: str | None) -> dict:
        """Reuse the survivor's already-paid matchup grid for 6-pick-N lineup facts.

        This adds no second ncp battery: selection restricts the existing member rows to each lineup.
        The selection operator remains facts-only and neutral-order; it now travels inside the slate
        receipt instead of being an optional afterthought outside the build gate.
        """
        mres = matchup_result if isinstance(matchup_result, dict) else {}
        check_grid = mres.get("members") if isinstance(mres.get("members"), list) else None
        check_context = ({"confidence": mres.get("confidence"),
                          "confidence_reason": mres.get("confidence_reason"),
                          "top_k": mres.get("top_k")}
                         if check_grid is not None else None)
        return run_select(
            team_c, fmt=fmt_battle, dex_fn=lookup_pokemon, item_fn=lookup_items,
            legality_status=legality_status, keep_mega=context.keep_mega,
            check_grid=check_grid, check_context=check_context)

    # P4.5 frame binding (only when --frame-output is passed): load the saved frame + a best-effort
    # own-repset lookup for the off-frame advisory (never eliminates; core-bearers use the frame's own
    # tamper-bound clusters, so they need no recompute).
    frame_output = None
    repset_fn = None
    if frame_output_path:
        frame_output, ferrs = _raw_json(frame_output_path, json_only=True)
        if frame_output is None:
            _emit_contract_errs(ferrs, fmt)
            return 2

        _repset_fn_cache: dict[str, list[dict]] = {}
        _repset_pool: list[dict] | None = None

        def repset_fn(species: str) -> list[dict]:
            nonlocal _repset_pool
            if species in _repset_fn_cache:            # candidates share filler species — cache per run
                return _repset_fn_cache[species]
            resolved, item_filter, _ = _canonicalize_repset_species(species, fmt_battle)
            try:
                if _repset_pool is None:
                    teams = repset.cached_teams_for_rule(fmt_battle, rule_scope)
                    _repset_pool, _ = query_identity.battle_view(teams)
                reps = repset.representative_sets_from_teams(resolved, fmt_battle, _repset_pool, item=item_filter)
            except (OSError, json.JSONDecodeError):    # narrow: a code bug MUST propagate, not masquerade
                return []                              # as an empty library (same discipline as sources._rep_for)
            out_cl: list[dict] = []
            for r in reps:
                cl = r.get("cluster")
                if cl:
                    out_cl.append(cl)
                elif r.get("item") is not None or r.get("ability") is not None:
                    out_cl.append({"item": r.get("item"), "ability": r.get("ability")})
            _repset_fn_cache[species] = out_cl
            return out_cl

    try:
        out = run_slate(raw, recompute_receipt_fn=recompute_receipt_fn, check_team_fn=check_team_fn,
                        validate_fn=validate_fn, profile_fn=profile_fn, matchup_fn=matchup_fn,
                        selection_fn=selection_fn,
                        canon_team_fn=_canon_team_json, battle_team_fn=_battle_team_view,
                        canon_binding_fn=lambda b: _normalize_nested(b, "binding"),
                        constraints_ctx=constraints_ctx,
                        fmt=fmt_battle,
                        library_copy_fn=_library_copy_fn(fmt_battle, rule=rule_scope, comparison=True),
                        frame_output=frame_output, repset_fn=repset_fn)
    except DexUnavailable as e:
        print(i18n.t('team_sibling_dex_unavailable', e=e), file=sys.stderr)
        return 2
    if out.get("refused"):
        _emit_gate(out)
        print(i18n.t('team_slate_refused', code=out["refused"]["code"]), file=sys.stderr)
        return 2
    out["environment"] = stamp
    if goal_flags:
        out["goal_name_resolution"] = goal_flags
    if env_warn:
        out["warnings"] = env_warn
    # DISPLAY projection only — the FULL output must still be fed to answer-audit (it re-hashes the
    # untrimmed matchup_risk). `view=summary` keeps the slate under the bridge's 2 MiB artifact gate.
    return _emit_gate(out, fail_on_verdict, view)


def cmd_localize(path: str) -> int:
    import answer_text
    raw, errors = _raw_json(path, json_only=True)
    if raw is None:
        _emit_contract_errs(errors, "json")
        return 2
    errors = contracts.check_team(raw)
    if contracts.fatal(errors):
        _emit_contract_errs(errors, "json")
        return 2
    out = answer_text.localize(team_from_dict(raw).to_dict(), resolve_names)
    emit_json(out)
    return 1 if out["unresolved"] else 0


def cmd_answer_audit(draft_path: str, fmt: str, slate_path: str | None,
                     slate_out_path: str | None, fail_on_verdict: bool = False) -> int:
    """UEP P6: the back gate. `draft_path` = the structured answer draft; `--slate` = the ORIGINAL
    slate.json; `--slate-output` = the SAVED slate-evaluate output. The receipt chain is re-bound by
    recomputed fingerprints (draft <-> output <-> slate teams <-> context); the structural checklist
    and the claim<->recompute layer run in one pass. JSON only (AI-facing). Violations are a VERDICT
    (exit 0); only unusable inputs / a broken chain refuse (exit 2)."""
    if not slate_path or not slate_out_path:
        print(i18n.t('team_answer_needs_inputs'), file=sys.stderr)
        return 2
    draft, errs = _raw_json(draft_path, json_only=True)
    if draft is None:
        _emit_contract_errs(errs, fmt)
        return 2
    slate_raw, errs = _raw_json(slate_path, json_only=True)
    if slate_raw is None:
        _emit_contract_errs(errs, fmt)
        return 2
    slate_out, errs = _raw_json(slate_out_path, json_only=True)
    if slate_out is None:
        _emit_contract_errs(errs, fmt)
        return 2
    ctx_raw = slate_raw.get("context") if isinstance(slate_raw, dict) \
        and isinstance(slate_raw.get("context"), dict) else {}
    ctx_errs = contracts.check_context(ctx_raw)
    _emit_contract_errs(ctx_errs, fmt)
    if contracts.fatal(ctx_errs):
        print(i18n.t('team_refuse_answer'), file=sys.stderr)
        return 2
    context = _canonicalize_context(context_from_dict(ctx_raw))
    stamp, env_warn = _stamp(context, None)

    def context_audit_fn(ctx: dict) -> dict:
        # Full P2 recompute: the fingerprint re-binds the chain AND the blocking gaps feed the
        # disclosure check (same degraded behavior as cmd_context_audit when the dex is down —
        # the shared bridges guarantee the two gates cannot disagree).
        return context_audit.audit(ctx, None, resolve_fn=_dex_resolve, item_fn=_dex_items, env=stamp,
                                   normalize_fn=lambda c: nested_names.normalize(c, "context", resolve_names))

    fmt_b = (slate_out.get("format") if isinstance(slate_out, dict) else None) \
        or context.format or "single"
    rule_scope = stamp.get("rule")

    def recompute_fn(bindings: list) -> list:
        """Re-run each claim's coordinates through the SAME construction the battery used:
        member side = the recommended team's registered set (matchup.member_actor); the other
        side = the resolved modal set (sources + matchup.set_actor/run_form_name); damage in ONE
        ncp batch, speed via matchup.pair_speed (no modal set -> neutral 0-SP basis, matchup
        parity). Sibling outage -> None for all (a disclosed note)."""
        try:
            need: set[str] = set()
            for b in bindings:
                if b["kind"] == "damage":
                    if not b.get("attacker_member"):
                        need.add(b["attacker"])
                    if not b.get("defender_member"):
                        need.add(b["defender"])
                else:
                    need.add(b["opponent"])
            sets_map = sources.resolve_opponent_sets(sorted(need), fmt_b, rule=rule_scope) if need else {}
            # A ranked species expands into one battery cell per observed (item, ability) archetype,
            # so when the binding names the variant the battery actually ran, resolve THAT build
            # instead of the species' modal one — otherwise the recompute answers a different
            # question than the claim (audit 2026-08-06). Falls back to the modal set when the id no
            # longer resolves (a library refresh can retire an archetype); it never invents one.
            variant_sets: dict[str, dict] = {}
            wanted = {(b[side], b[f"{kind}_variant"])
                      for b in bindings if b["kind"] == "damage"
                      for side, kind in (("attacker", "attacker"), ("defender", "defender"))
                      if b.get(f"{kind}_variant")}
            for species, vid in sorted(wanted):
                for v in sources.resolve_opponent_variants(species, fmt_b, rule=rule_scope) or []:
                    if v.get("variant_id") == vid:
                        variant_sets[vid] = v
                        break

            def opponent_set(binding: dict, side: str) -> dict | None:
                vid = binding.get(f"{side}_variant")
                return variant_sets.get(vid) or sets_map.get(binding[side])
            bound_members = [
                member for b in bindings
                for member in (b.get("attacker_member"), b.get("defender_member"),
                               b.get("member_member"))
                if isinstance(member, dict)
            ]
            member_names = {m["species"] for m in bound_members if m.get("species")}
            speed_opp_names = {
                run_form_name(b["opponent"], sets_map.get(b["opponent"]))
                for b in bindings if b["kind"] == "speed"
            }
            facts = lookup_pokemon(sorted(member_names | speed_opp_names)) \
                if member_names or speed_opp_names else {}
            held_items = sorted({m.get("item") for m in bound_members if m.get("item")})
            item_info = (lookup_items(held_items) or {}) if held_items else {}
            form_names = {
                form for info in item_info.values()
                for form in ((info or {}).get("required_by") or [])
            }
            missing_forms = sorted(form_names - set(facts))
            if missing_forms:
                facts.update(lookup_pokemon(missing_forms) or {})

            def effective(member: dict[str, Any]) -> dict[str, Any]:
                return effective_member(member, facts, item_info)

            def base_spe(name: str) -> int | None:
                return ((facts.get(name) or {}).get("stats") or {}).get("spe")

            out: list = [None] * len(bindings)
            requests: list[dict] = []
            pending: list[int] = []
            for bi, b in enumerate(bindings):
                if b["kind"] == "damage":
                    atk_m, def_m = b.get("attacker_member"), b.get("defender_member")
                    atk_set = None if atk_m else opponent_set(b, "attacker")
                    def_set = None if def_m else opponent_set(b, "defender")
                    if atk_m is None and atk_set is None:
                        out[bi] = {"computed": False,
                                   "reason": f"no resolvable modal set for attacker {b['attacker']!r}"}
                    elif def_m is None and def_set is None:
                        out[bi] = {"computed": False,
                                   "reason": f"no resolvable modal set for defender {b['defender']!r}"}
                    else:
                        pending.append(bi)
                        atk_effective = effective(atk_m) if atk_m else None
                        def_effective = effective(def_m) if def_m else None
                        requests.append({
                            "attacker": member_actor(atk_effective) if atk_effective
                            else set_actor(b["attacker"], atk_set),
                            "defender": member_actor(def_effective) if def_effective
                            else set_actor(b["defender"], def_set),
                            "move": b["move"]})
                else:
                    member_effective = effective(b["member_member"])
                    m_base = base_spe(member_effective["species"])
                    opp_set = sets_map.get(b["opponent"])
                    o_base = base_spe(run_form_name(b["opponent"], opp_set))
                    if m_base is None or o_base is None:
                        missing = b["member"] if m_base is None else b["opponent"]
                        out[bi] = {"computed": False,
                                   "reason": f"dex has no base Speed for {missing!r}"}
                    else:
                        out[bi] = {"computed": True,
                                   **pair_speed(member_effective, m_base, opp_set, o_base)}
            results = damage_batch(requests) if requests else []
            for bi, r in zip(pending, results):
                r = r or {}
                if r.get("error") or r.get("maxPercent") is None:
                    out[bi] = {"computed": False,
                               "reason": str(r.get("error")
                                             or "no damage result (status move or unknown inputs)")}
                else:
                    out[bi] = {"computed": True, **dmg_fact(bindings[bi]["move"], r)}
            return out
        except (NcpUnavailable, MetaUnavailable, DexUnavailable):
            return [None] * len(bindings)

    def text_check_fn(draft, source, output):
        import answer_text
        from dexlink import official_names
        return answer_text.check(draft, source, output, official_names())

    try:
        out = answer_audit.audit_answer(draft, slate_raw, slate_out,
                                        context_audit_fn=context_audit_fn,
                                        validate_fn=lambda tc: _validate_json(tc, context),
                                        canon_team_fn=_canon_team_json, recompute_fn=recompute_fn,
                                        canon_disclosures_fn=lambda d: _normalize_nested(d, "draft"),
                                        library_copy_fn=_library_copy_fn(fmt_b, rule=rule_scope),
                                        text_check_fn=text_check_fn)
    except DexUnavailable as e:
        print(i18n.t('team_sibling_dex_unavailable', e=e), file=sys.stderr)
        return 2
    if out.get("refused"):
        _emit_gate(out)
        print(i18n.t('team_answer_refused', code=out["refused"]["code"]), file=sys.stderr)
        return 2
    out["environment"] = stamp
    if env_warn:
        out["warnings"] = env_warn
    return _emit_gate(out, fail_on_verdict)


def cmd_draft_init(fmt: str, slate_path: str | None, slate_out_path: str | None,
                   recommended: list[int] | int | None = None) -> int:
    """Generate a structured answer-draft skeleton from the saved slate chain.

    This is a FORM helper only: it copies the receipt, selected slated teams, and required per-survivor
    scaffold keys so the AI fills substance instead of hand-reconstructing the P6 JSON shape.
    """
    if not slate_path or not slate_out_path:
        out = {"kind": "draft-init",
               "refused": {"code": "missing_inputs",
                           "reason": "draft-init requires --slate <slate.json> and --slate-output "
                                     "<saved slate-evaluate output.json>"}}
        emit_json(out)
        return 2
    slate_raw, errs = _raw_json(slate_path, json_only=True)
    if slate_raw is None:
        _emit_contract_errs(errs, fmt)
        return 2
    shape_err = slate_shape_error(slate_raw)
    if shape_err:
        emit_json({"kind": "draft-init", "refused": shape_err})
        return 2
    slate_out, errs = _raw_json(slate_out_path, json_only=True)
    if slate_out is None:
        _emit_contract_errs(errs, fmt)
        return 2
    if not isinstance(slate_out, dict):
        emit_json({
            "kind": "draft-init",
            "refused": {"code": "bad_slate_output",
                        "reason": "slate-output must be the saved slate-evaluate JSON object"}})
        return 2
    receipt = slate_out.get("slate_receipt")
    if not isinstance(receipt, dict) or not receipt.get("fingerprint"):
        emit_json({
            "kind": "draft-init",
            "refused": {"code": "missing_slate_receipt",
                        "reason": "slate-output has no slate_receipt; run slate-evaluate first"}})
        return 2
    try:
        assembly.verify_draft_source(slate_raw, slate_out)
    except assembly.AssemblyError as exc:
        emit_json({"kind": "draft-init", "refused": {"code": exc.code, "reason": str(exc)}})
        return 2
    survivors = [i for i in (slate_out.get("survivors") or []) if isinstance(i, int)]
    if not survivors:
        emit_json({
            "kind": "draft-init",
            "refused": {"code": "no_survivors",
                        "reason": "slate-output has no survivors to recommend; revise and re-slate"}})
        return 2
    if (recommended is not None and not (type(recommended) is int or
            (isinstance(recommended, list) and recommended and all(type(i) is int for i in recommended)))):
        emit_json({"kind": "draft-init", "refused": {"code": "bad_recommended",
                  "reason": "recommended must contain survivor integer indices"}})
        return 2
    if isinstance(recommended, int):
        rec_list: list[int] | None = [recommended]
    elif isinstance(recommended, list):
        rec_list = [i for i in recommended if isinstance(i, int)]
    else:
        rec_list = None
    selected = list(dict.fromkeys(rec_list or survivors[:3]))
    teams = slate_raw.get("teams") or []
    bad = [i for i in selected if i not in survivors or i < 0 or i >= len(teams)]
    if bad:
        emit_json({
            "kind": "draft-init",
            "refused": {"code": "recommended_not_survivor",
                        "reason": "recommended indices must be slate survivors",
                        "bad_indices": bad, "survivors": survivors}})
        return 2

    ctx = slate_raw.get("context") if isinstance(slate_raw.get("context"), dict) else {}
    out_env = slate_out.get("environment") if isinstance(slate_out.get("environment"), dict) else {}
    environment = {
        "season": out_env.get("season") or ctx.get("season") or "",
        "rule": out_env.get("rule") or ctx.get("rule") or "",
    }
    candidates = slate_out.get("candidates") if isinstance(slate_out.get("candidates"), list) else []

    def _candidate(idx: int) -> dict[str, Any]:
        c = candidates[idx] if 0 <= idx < len(candidates) else {}
        return c if isinstance(c, dict) else {}

    recommended_entries: list[dict[str, Any]] = []
    for idx in selected:
        rec: dict[str, Any] = {"slate_index": idx, "team": teams[idx], "tradeoffs": []}
        cand = _candidate(idx)
        mega_plan = cand.get("mega_plan") if isinstance(cand.get("mega_plan"), dict) else {}
        if (mega_plan.get("registered_mega_count") or 0) > 1:
            rec["mega_registration_rationale"] = {
                "primary": "", "alternative_plan": "", "opportunity_cost": ""}
        mega_assessment = (cand.get("mega_registration_assessment")
                           if isinstance(cand.get("mega_registration_assessment"), dict) else {})
        if mega_assessment.get("requires_deviation_ack"):
            rec["mega_registration_deviation"] = {
                "reason": "", "evidence": "", "opportunity_cost": ""}
        if cand.get("library_overlap"):
            rec["observed_provenance"] = ""
            overlap = cand.get("library_overlap") if isinstance(cand.get("library_overlap"), dict) else {}
            if overlap.get("verbatim_ids"):
                rec["adoption_review"] = {
                    "status": "",
                    "other_structures": {"considered": None, "reason": ""},
                    "checked_modification_types": [],
                    "attempted_changes": [],
                    "reason": "",
                    "evidence": "",
                }
        recommended_entries.append(rec)

    try:
        onboarding = assembly.onboarding_summary(ctx, slate_raw.get("onboarding")) if ctx.get("frame_required") is True else None
    except assembly.AssemblyError as exc:
        emit_json({"kind": "draft-init", "refused": {"code": exc.code, "reason": str(exc)}})
        return 2
    draft = {
        "environment": environment,
        "context_summary": "",
        "single_team_requested": False,
        "alternatives_omitted_reason": "",
        "assumptions": [],
        "confidence_notes": [],
        "tuning_summary": {"status": "not_run", "reason": "" if ctx.get("benchmarks")
                           else "No explicit tuning benchmarks were supplied."},
        "recommended": recommended_entries,
        "frame_deviations": [],
        "onboarding_summary": onboarding,
        "convergence_rationale": {
            str(i): {"worst_matchup": "", "accepted_by_constraint": "", "opportunity_cost": ""}
            for i in survivors
        },
        "claims": [],
        "team_claims": [],
        "slate_receipt": receipt,
    }
    import exploration
    if exploration.concerns(slate_out):
        draft["exploration"] = {"reason": ""}
    out = {"kind": "draft-init", "draft": draft,
           "exploration_facts": exploration.concerns(slate_out),
           "claim_candidates": assembly.claim_candidates(slate_out, selected),
           "notes": ["Complete substantive draft fields and resolve any incomplete onboarding before answer-audit. "
                     "Claim candidates are receipt-bound static calculator facts, conditional on the stated "
                     "opponent variant; they are not battle outcomes. Copy only facts used in the answer into "
                     "draft.claims. Speed aggregates do not create individual speed claims. "
                     "The helper does not supply convergence judgment."]}
    emit_json(out)
    return 0


def cmd_checkpoint(slate_out_path: str, fmt: str, slate_path: str | None,
                   fail_on_verdict: bool = False) -> int:
    """Post-slate, pre-tune pause contract for AI orchestration. JSON only."""
    if not slate_path:
        print(i18n.t('team_checkpoint_needs_inputs'), file=sys.stderr)
        return 2
    slate_out, errs = _raw_json(slate_out_path, json_only=True)
    if slate_out is None:
        _emit_contract_errs(errs, fmt)
        return 2
    slate_raw, errs = _raw_json(slate_path, json_only=True)
    if slate_raw is None:
        _emit_contract_errs(errs, fmt)
        return 2
    out = checkpoint_mod.build_checkpoint(slate_raw, slate_out)
    ctx_raw = slate_raw.get("context") if isinstance(slate_raw, dict) and isinstance(slate_raw.get("context"), dict) else {}
    ctx_errs = contracts.check_context(ctx_raw)          # guard context_from_dict like every other command
    _emit_contract_errs(ctx_errs, fmt)
    if contracts.fatal(ctx_errs):
        print(i18n.t('team_refuse_checkpoint'), file=sys.stderr)
        return 2
    stamp, env_warn = _stamp(context_from_dict(ctx_raw) if ctx_raw else None, None)
    out["environment"] = slate_out.get("environment") if isinstance(slate_out, dict) else stamp
    if not out.get("environment"):
        out["environment"] = stamp
    if env_warn:
        out["warnings"] = (out.get("warnings") or []) + env_warn
    return _emit_gate(out, fail_on_verdict)


def _as_list(v):
    """Session-spec list coercion: a bare string must not be iterated into characters
    (external audit 2026-07-02) — one rule, one place."""
    return [v] if isinstance(v, str) else v


def _dispatch_session_op(cmd: dict) -> int:
    """Run one operator inside a session, always as json. Returns its exit code."""
    op = cmd.get("op")
    op = op.replace("_", "-") if isinstance(op, str) else op
    if op not in COMMAND_OPTIONS:
        raise ValueError(f"unknown session op: {op!r}")
    allowed = (COMMAND_OPTIONS[op] - {"matchup_view", "slate_view", "next_batch"}) | {"op"}
    file_ops = {"review", "try", "review-claims", "localize", "parse", "validate", "suggest-fixes", "diagnose", "tune", "scenario", "threshold", "select", "matchup", "fill", "replace",
                "context-audit", "slate-init", "slate-evaluate", "checkpoint", "answer-audit"}
    allowed |= {"file"} if op in file_ops else set()
    allowed |= {"format"} if "game_format" in COMMAND_OPTIONS[op] else set()
    allowed |= {"species"} if op in {"repset", "speed-ladder", "counters", "ko-scan"} else set()
    allowed |= {"view"} if op in {"matchup", "slate-evaluate"} else set()
    allowed |= {"max_cores"} if op == "landscape" else set()
    allowed |= {"team_id", "id"} if op == "show" else set()
    allowed |= {"next"} if op == "intake" else set()
    bad = sorted(set(cmd) - allowed)
    if bad:
        raise ValueError(f"{op} does not consume session fields: {', '.join(bad)}; see team.py schema")
    for field in ("fail_on_verdict", "no_cache", "opponent_ranges", "from_cell"):
        if field in cmd and not isinstance(cmd[field], bool):
            raise ValueError(f"{field} must be a boolean")
    if op in {"diagnose", "select"} and "top_k" in cmd and not any(cmd.get(k) for k in ("with_check", "focus", "opponents", "conditions")):
        raise ValueError("top_k needs with_check:true for diagnose/select")
    if op == "intake" and "answered" in cmd and not cmd.get("next"):
        raise ValueError("answered requires intake next:true")
    f = cmd.get("file")
    ctx = cmd.get("context")
    strict = cmd.get("fail_on_verdict") is True
    if op in ("review", "try"):
        return cmd_review(f, ctx, top_k=normalize_top_k(cmd.get("top_k")), changes_path=cmd.get("changes"),
                          trial=op == "try", view=cmd.get("view", "summary"), fail_on_verdict=strict,
                          focus_path=cmd.get("focus"), opponents_path=cmd.get("opponents"), conditions_path=cmd.get("conditions"))
    if op == "review-claims":
        return cmd_review_claims(f, strict)
    if op == "localize":
        return cmd_localize(f)
    if op in ("context-audit", "context_audit"):
        return cmd_context_audit(f, "json", ctx, strict)
    if op == "frame":
        return cmd_frame("json", cmd.get("game_format") or cmd.get("format"), cmd.get("season"),
                         ctx, cmd.get("audit_receipt"), cmd.get("draw"), cmd.get("seed"), cmd.get("seen_teams"))
    if op in ("slate-evaluate", "slate_evaluate"):
        return cmd_slate_evaluate(f, "json", normalize_top_k(cmd.get("top_k")), cmd.get("frame_output"),
                                  view=cmd.get("view", "full"), fail_on_verdict=strict)
    if op == "slate-init":
        return cmd_slate_init(f, cmd.get("context"), cmd.get("audit_receipt"),
                              cmd.get("frame_output"), cmd.get("intake_output"))
    if op == "checkpoint":
        return cmd_checkpoint(f, "json", cmd.get("slate"), strict)
    if op in ("answer-audit", "answer_audit"):
        return cmd_answer_audit(f, "json", cmd.get("slate"), cmd.get("slate_output"), strict)
    if op in ("draft-init", "draft_init"):
        return cmd_draft_init("json", cmd.get("slate"), cmd.get("slate_output"),
                              _as_list(cmd.get("recommended")))
    if op == "intake":
        return cmd_intake(cmd.get("game_format") or cmd.get("format"),
                          onboarding=bool(cmd.get("onboarding")), context_path=cmd.get("context"),
                          step=bool(cmd.get("next")), answered=_as_list(cmd.get("answered")))
    if op == "vocab":
        return cmd_vocab("json")
    if op == "observed":
        lim = cmd.get("limit")
        return cmd_observed("json", cmd.get("game_format") or cmd.get("format"), cmd.get("season"),
                            ctx, _as_list(cmd.get("species")), cmd.get("order"),
                            int(lim) if lim is not None else None, cmd.get("seed"), cmd.get("seen_teams"))
    if op == "landscape":
        if "max_cores" in cmd and "limit" in cmd and cmd["max_cores"] != cmd["limit"]:
            raise ValueError("conflicting landscape max_cores and limit; use one")
        mc = cmd.get("max_cores", cmd.get("limit"))
        return cmd_landscape("json", cmd.get("game_format") or cmd.get("format"),
                             cmd.get("season"), ctx, _as_list(cmd.get("species")),
                             mc,
                             _as_list(cmd.get("aspects")), no_cache=cmd.get("no_cache") is True,
                             population_scope=cmd.get("scope"))
    if op == "parse":
        return cmd_parse(f, "json", cmd.get("game_format") or cmd.get("format"))
    if op == "validate":
        return cmd_validate(f, "json", ctx)
    if op == "suggest-fixes":
        return cmd_suggest_fixes(f, ctx)
    if op == "diagnose":
        return cmd_diagnose(f, "json", cmd.get("aspect", "all"), ctx,
                            with_check=bool(cmd.get("with_check")),
                            top_k=normalize_top_k(cmd.get("top_k")), focus_path=cmd.get("focus"))
    if op == "tune":
        if cmd.get("from_cell"):
            if ctx:
                raise ValueError("tune from_cell reads context from its bound report; omit context")
            return cmd_tune_cell(f)
        return cmd_tune(f, "json", ctx)
    if op == "type-matchup":
        return cmd_type_matchup(cmd.get("attack_type"), cmd.get("pokemon"),
                                cmd.get("types"), cmd.get("ability"))
    if op == "rules-facts":
        return cmd_rules_facts()
    if op == "speed-ladder":
        return cmd_speed_ladder(cmd.get("species"), cmd.get("game_format") or cmd.get("format"),
                                cmd.get("set"), cmd.get("conditions"), cmd.get("limit"),
                                cmd.get("opponent_ranges", False), "json")
    if op == "counters":
        return cmd_counters(cmd.get("species"), cmd.get("game_format") or cmd.get("format"), cmd.get("top_n"), "json")
    if op == "ko-scan":
        return cmd_ko_scan(cmd.get("species"), cmd.get("game_format") or cmd.get("format"),
                           cmd.get("top_n"), cmd.get("direction", "both"), "json")
    if op == "scenario":
        return cmd_scenario(f)
    if op == "threshold":
        return cmd_threshold(f)
    if op == "select":
        return cmd_select(f, "json", ctx, with_check=bool(cmd.get("with_check")),
                          top_k=normalize_top_k(cmd.get("top_k")), opponents_path=cmd.get("opponents"),
                          conditions_path=cmd.get("conditions"))
    if op == "matchup":
        return cmd_matchup(f, "json", ctx, normalize_top_k(cmd.get("top_k")),
                           view=cmd.get("view", "full"), as_checks=bool(cmd.get("as_checks")),
                           opponents_path=cmd.get("opponents"), conditions_path=cmd.get("conditions"))
    if op == "fill":
        return cmd_fill(f, "json", ctx, cmd.get("slot"))
    if op == "replace":
        return cmd_replace(f, "json", ctx)
    # L4 library / cache reads — no team file; the NL flows that chain build+review routinely need
    # these in the SAME batch (e.g. search a sample library, then show one, then matchup against it),
    # so they run in-session too (the sibling skills stay resident). game_format accepts `game_format`
    # or `format`; a query may be a JSON string OR an inline object/list (auto-serialized for cmd_search).
    if op == "repset":
        return cmd_repset(cmd.get("species"), cmd.get("game_format") or cmd.get("format"),
                          cmd.get("season"), int(cmd.get("max_clusters", 3)), "json")
    if op == "oppmatrix":
        return cmd_oppmatrix(cmd.get("species"), cmd.get("game_format") or cmd.get("format"),
                             cmd.get("season"), cmd.get("vs"), "json",
                             as_checks=bool(cmd.get("as_checks")), all_species=cmd.get("all") is True)
    if op == "topmeta":
        return cmd_topmeta(cmd.get("game_format") or cmd.get("format"), cmd.get("scope"),
                           _as_list(cmd.get("species")))
    if op == "search":
        q = cmd.get("query")
        if isinstance(q, (dict, list)):
            q = json.dumps(q, ensure_ascii=False)
        return cmd_search(q, _as_list(cmd.get("species")), _as_list(cmd.get("has_move")),
                          _as_list(cmd.get("has_item")), _as_list(cmd.get("has_ability")),
                          cmd.get("game_format") or cmd.get("format"),
                          cmd.get("season"), cmd.get("limit"), bool(cmd.get("collapse", False)), "json",
                          where=cmd.get("where"))
    if op == "show":
        return cmd_show(cmd.get("team_id") or cmd.get("id"), cmd.get("game_format") or cmd.get("format"),
                        cmd.get("season"), "json")
    raise ValueError(f"unknown session op: {op!r}")


def cmd_session(spec_path: str | None, spec_json: str | None = None, fail_on_verdict: bool = False) -> int:
    """Run operators in one process with inline inputs and prior full-result references.

    `spec_path` is a JSON list of commands, e.g.
      [{"op":"validate","file":"team.json","context":"ctx.json"},
       {"op":"matchup","file":"team.json","top_k":8,"view":"summary"}]
    File/inline JSON arrays and --spec stdin use the same dispatch. Each item emits {op,rc,result}
    or {op,error}; failed/missing/forward references never dispatch a dependent operation.
    Full results supply references and saved artifacts even when stdout projects summaries."""
    import io
    import tempfile
    from contextlib import redirect_stdout
    try:
        if spec_json is not None:
            text = sys.stdin.read() if spec_json == "-" else spec_json
        elif spec_path and spec_path.lstrip().startswith(("[", "{")):
            text = spec_path
        elif spec_path:
            text = Path(spec_path).read_text(encoding="utf-8")
        else:
            raise ValueError("session requires a spec.json file or --spec '<JSON array>' (use --spec - for stdin)")
        spec = json.loads(text)
    except (OSError, ValueError) as e:
        print(i18n.t('team_session_unreadable', e=e), file=sys.stderr)
        return 2
    if not isinstance(spec, list):
        print(i18n.t('team_session_not_list'), file=sys.stderr)
        return 2
    results: list[dict] = []
    full_results: list[dict] = []
    aggregate_out = output_path()
    all_inputs = [cmd[field] for cmd in spec if isinstance(cmd, dict) for field in FILE_FIELDS
                  if isinstance(cmd.get(field), str) and "\n" not in cmd[field]]
    if spec_json is None and spec_path and not spec_path.lstrip().startswith(("[", "{")):
        all_inputs.append(spec_path)
    try:
        protect_inputs(aggregate_out, all_inputs)
    except ValueError as exc:
        raise OutputError(str(exc)) from exc
    destinations = {Path(aggregate_out).resolve()} if aggregate_out else set()
    with worker.session(), tempfile.TemporaryDirectory(prefix="quark-team-session-") as directory:
        for index, cmd in enumerate(spec):
            if not isinstance(cmd, dict):
                error = {"op": None, "error": i18n.Msg('team_session_cmd_not_object', type=type(cmd).__name__)}
                results.append(error)
                full_results.append(error)
                continue
            op = cmd.get("op")
            buf = io.StringIO()
            try:
                resolved = resolve_refs(cmd, full_results)
                out = resolved.pop("out", None)
                if out is not None and (not isinstance(out, str) or not out):
                    raise ValueError("session out must be a nonempty file path")
                if out:
                    destination = Path(out).resolve()
                    if destination in destinations:
                        raise ValueError("session output paths must be distinct, including the aggregate --out")
                    destinations.add(destination)
                protect_inputs(out, all_inputs + [resolved[f] for f in FILE_FIELDS
                                                 if isinstance(resolved.get(f), str) and "\n" not in resolved[f]])
                ready = materialize_inputs(resolved, Path(directory), index)
                command = str(op).replace("_", "-")
                view = ready.pop("view", None) if command in team_views.COMMANDS else ready.get("view")
                limit = ready.pop("summary_limit", 6)
                if view is not None and (command not in VIEW_COMMANDS or view not in {"full", "summary"}):
                    raise ValueError("view must be full|summary on a supported operator; see schema")
                if type(limit) is not int or limit < 1:
                    raise ValueError("summary_limit must be a positive integer")
                if command == "intake" and view == "summary" and not (ready.get("onboarding") or ready.get("next")):
                    raise ValueError("intake summary requires onboarding:true or next:true")
                if "summary_limit" in resolved and (command not in team_views.LIMITED_COMMANDS or view != "summary"):
                    raise ValueError("summary_limit requires view:summary on a supported operator")
                with output_options(out, command=command, view=view, limit=limit) as state, redirect_stdout(buf):
                    rc = _dispatch_session_op(ready)
                payload = buf.getvalue().strip()
                displayed = json.loads(payload) if payload else None
                results.append({"op": op, "rc": rc, "result": displayed})
                full_results.append({"op": op, "rc": rc,
                                     "result": state.full if state.emitted else displayed})
            except Exception as e:  # noqa: BLE001
                error = {"op": op, "error": str(e)}
                results.append(error)
                full_results.append(error)
    emit_json(full_results, projector=lambda value, view: results)
    failed = any(item.get("error") or item.get("rc", 1) != 0 or result_failed(item.get("result"))
                 for item in full_results)
    return 1 if fail_on_verdict and failed else 0


def _canonicalize_repset_species(species: str, fmt: str) -> tuple[str, str | None, list[str]]:
    """Resolve a requested battle form without broadening it to a storage convention."""
    warns = []
    try:
        rec = resolve_names([species], "pokemon", fuzzy=True).get(species, {})
    except DexUnavailable:
        warns.append(i18n.Msg('team_dex_down_query_library'))
        return species, None, warns
    if not (rec.get("ok") and rec.get("canonical")):
        warns.append(i18n.Msg('team_dex_unresolved_library', species=repr(species)))
        return species, None, warns
    if rec.get("match_type") == "fuzzy":
        warns.append(i18n.Msg('team_name_corrected', frm=species, to=rec["canonical"],
                             distance=rec.get("distance"), score=rec.get("score")))
    return rec["canonical"], None, warns


def _format_repset_md(species: str, fmt: str, archetypes: list[dict], resolved: str | None = None,
                      item_filter: str | None = None) -> str:
    """Human-readable archetype report. Objective FACTS only — coverage/share/sample are provenance,
    NEVER a strength score, and archetypes are listed by real-team prevalence, not 'best'."""
    title = species
    if resolved and resolved != species:
        flt = f" @ {item_filter}" if item_filter else ""
        title = f"{species} → {resolved}{flt}"
    if not archetypes:
        return i18n.t('team_repset_none', title=title, fmt=fmt)
    lines = [f"### {title} — {i18n.t('team_repset_archetypes_title', fmt=fmt)}",
             "_" + i18n.t('team_repset_subtitle') + "_\n"]
    if item_filter:
        # The pool is the item-filtered subpool (doubles-Mega isolation), NOT the whole base species.
        # Spell that out so `covers ... of teams` below is read against the filtered pool, never as
        # base-species coverage (audit 2026-06-26).
        total = archetypes[0].get("species_sample_total")
        pool = archetypes[0].get("species_sample")
        if total is not None and pool is not None:
            lines.append("_" + i18n.t('team_repset_pool_filtered', item_filter=item_filter,
                                       pool=pool, total=total, resolved=resolved) + "_\n")
    for i, a in enumerate(archetypes, 1):
        cl = a.get("cluster")
        if cl:
            head = f"**#{i}** item=`{cl['item']}` / ability=`{cl['ability']}`"
            cov = a.get("coverage")
            # coverage is denominated on the (item-filtered) species pool, so name THAT pool size
            # (`species_sample`), not the cluster size (`sample`) — else "covers 0.6 of the 3 teams" when
            # the pool is 5 (audit 2026-06-26: matches the 9856e9d denominator-honesty fix).
            pool_n = a.get("species_sample", a["sample"])
            pool_word = (i18n.t('team_repset_pool_word_filtered', pool_n=pool_n, item_filter=item_filter)
                         if item_filter else i18n.t('team_repset_pool_word'))
            cov_s = f", {i18n.t('team_repset_covers', cov=cov)}{pool_word}" if cov is not None else ""
        else:
            # Fragmented fallback: no dominant (item,ability) archetype. Do NOT claim a cluster
            # coverage — the global modal is on share=count/sample of the pool, not 100% (audit).
            head = f"**#{i}** {i18n.t('team_repset_fragmented', sample=a['sample'])}"
            cov_s = ""
        lines.append(f"{head} — {a['nature']} · {i18n.t('team_repset_modal_set')} {a['count']}/{a['sample']} "
                     f"({i18n.t('team_repset_share')} {a['share']}{cov_s}) · "
                     f"{i18n.t('confidence')} **{a['confidence']}**")
        lines.append(f"  - {i18n.t('team_repset_moves')}: {', '.join(a['moves']) or '—'}")
        if note := query_identity.registration_note(a):
            lines.append(f"  - {note}")
        sps = a.get("sps")
        if sps:
            # The spread's OWN sample stats: only some modal-set teams carry a spread, so its
            # count/sample/confidence differ from the set's (a 4/29 spread must not read as the set's
            # high — audit 2026-07-02). Confidence sits on the investment SHAPE (the direction real
            # players converge on); the line shown is the modal real line within it, and a second
            # shape big enough to matter is disclosed (bimodality).
            sc, ss, scf = a.get("spread_count"), a.get("spread_sample"), a.get("spread_confidence")
            shape, shc = a.get("spread_shape"), a.get("spread_shape_count")
            if shape is not None and shc:
                basis = f"; shape {repset.shape_label(shape)} {shc}/{ss}, {scf}; line {sc}/{shc}"
            elif sc:
                basis = f"; spread {sc}/{ss}, {scf}"
            else:
                basis = ""
            ru = a.get("spread_shape_runner_up")
            if ru:
                basis += f"; 2nd shape {repset.shape_label(ru['shape'])} {ru['count']}/{ss}"
            lines.append(f"  - {i18n.t('team_repset_sp_real', sps=sps)}  _[spread_origin=real-team{basis}]_")
        else:
            lines.append(f"  - {i18n.t('team_repset_sp_none')}")
    return "\n".join(lines)


def cmd_repset(species: str, fmt: str | None, season: str | None, max_clusters: int,
               out_fmt: str) -> int:
    """Query the real-team library for a species' up-to-N (item,ability) archetypes.

    Facts only: every set carries cluster/coverage/share/sample/spread_origin/confidence and is NEVER a
    strength score. Format is REQUIRED (no single/double default — never mix metagames); omitted season
    reads the current rule pool (an empty library yields an empty result, not an error). The raw species is
    canonicalized via dex first, then matched against the actual battle-form library view."""
    if not fmt:
        print(i18n.t('team_repset_needs_format'), file=sys.stderr)
        return 2
    if max_clusters < 1:
        print(i18n.t('team_repset_max_clusters'), file=sys.stderr)
        return 2
    scope = _library_scope(fmt, season, None)
    try:
        resolved, item_filter, name_warns = _canonicalize_repset_species(species, fmt)
        teams = _scope_teams(fmt, scope)
        teams, identities = query_identity.battle_view(teams, [{"species": resolved}])
        archetypes = repset.representative_sets_from_teams(
            resolved, fmt, teams, max_clusters=max_clusters, item=item_filter)
        archetypes = canonical_spreads(archetypes)
    except Exception as e:
        print(i18n.t('team_repset_failed', e=e), file=sys.stderr)
        return 2
    # repset reads real-team data (exact partition for --season, same-rule pool by default), so stamp the
    # actual data season/rule provenance instead of mislabeling it as the current base.
    stamp, env_warn = _scope_stamp(scope, teams, None)
    warns = list(name_warns) + list(env_warn)
    if scope.get("kind") == "rule":
        partition_count = repset.partition_count_for_rule(fmt, scope["rule"])
        partition_empty = (partition_count == 0) if partition_count is not None else (not teams)
    else:
        partition_count = repset.partition_count(fmt, scope.get("season"))
        partition_empty = (partition_count == 0) if partition_count is not None else (not teams)
    if not archetypes and partition_empty:
        # empty result + empty partition = a season typo, not "this species has no real-team data";
        # mirror observed's guard (zero rows != no evidence) so the AI doesn't wrongly fall back to meta
        # when it actually queried a non-existent season (audit 2026-07-06).
        warns.append(i18n.Msg('team_observed_empty_partition',
                              season=scope.get("season") or scope.get("rule"), fmt=fmt))
    payload = {
        "query": {"species": species, "resolved": resolved, "item_filter": item_filter,
                  "format": fmt, **_scope_query(scope, teams), "max_clusters": max_clusters},
        "archetypes": archetypes,
        "identity_basis": "dex_battle_form", "identity_gaps": identities.gaps,
        "environment": stamp,
        "note": "real-team (item,ability)-clustered archetypes by prevalence; facts only, no strength "
                "score; empty when the library is too thin (resolve via meta fallback).",
    }
    if warns:
        payload["warnings"] = warns
    if out_fmt == "json":
        emit_json(payload)
    else:
        print(_env_header(stamp, warns) + "\n")
        print(_format_repset_md(species, fmt, archetypes, resolved, item_filter))
    return 0


def cmd_counters(species, game_format, top_n, fmt):
    return _cmd_cached_facts("counters", counters, species, game_format, top_n, fmt)


def cmd_ko_scan(species, game_format, top_n, direction, fmt):
    return _cmd_cached_facts("ko-scan", ko_scan, species, game_format, top_n, fmt, direction=direction)


def cmd_scenario(path):
    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(spec, dict):
            raise counters.QueryError("scenario input must be a JSON object", query="scenario")
        fmt = spec.get("format")
        if fmt not in ("single", "double"):
            raise counters.QueryError("format must be single or double", code="unknown_format", query="format")
        cache = oppcache.load_cache(fmt, environment.CURRENT_RULE)
        if cache is None:
            raise counters.QueryError("no current opponent cache for this format", code="not_found", query="scenario")
        if (cache.get("built_for") or {}).get("rule") != environment.CURRENT_RULE:
            raise oppcache.StaleCacheError("opponent cache does not match the current rule")
        out = scenario.query(spec, cache)
        out["environment"], warns = environment.resolve(data_rule=(cache.get("built_for") or {}).get("data_rule"),
                                                         data_seasons=(cache.get("built_for") or {}).get("data_seasons"))
        if warns:
            out["warnings"] = warns
        emit_json(out)
        return 0
    except counters.QueryError as exc:
        emit_json(exc.result())
        return 0 if exc.code == "not_found" else 1
    except oppcache.StaleCacheError as exc:
        emit_json({"ok": False, "query": "scenario", "error": {"code": "authority_mismatch", "message": str(exc)}})
    except (OSError, json.JSONDecodeError) as exc:
        emit_json({"ok": False, "query": "scenario", "error": {"code": "bad_input", "message": str(exc)}})
    except (DexUnavailable, NcpUnavailable, NcpInputError) as exc:
        emit_json({"ok": False, "query": "scenario", "error": {"code": "engine_unavailable", "message": str(exc)}})
    return 1


def cmd_threshold(path):
    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(spec, dict):
            raise counters.QueryError("threshold input must be a JSON object", query="threshold")
        fmt = spec.get("format")
        if fmt not in ("single", "double"):
            raise counters.QueryError("format must be single or double", code="unknown_format", query="format")
        cache = oppcache.load_cache(fmt, environment.CURRENT_RULE)
        if cache is None:
            raise counters.QueryError("no current opponent cache for this format", code="not_found", query="threshold")
        if (cache.get("built_for") or {}).get("rule") != environment.CURRENT_RULE:
            raise oppcache.StaleCacheError("opponent cache does not match the current rule")
        out = threshold.query(spec, cache)
        built = cache.get("built_for") or {}
        out["environment"], warns = environment.resolve(data_rule=built.get("data_rule"),
                                                         data_seasons=built.get("data_seasons"))
        if warns:
            out["warnings"] = warns
        emit_json(out)
        return 0
    except counters.QueryError as exc:
        emit_json(exc.result())
        return 0 if exc.code == "not_found" else 1
    except oppcache.StaleCacheError as exc:
        emit_json({"ok": False, "query": "threshold", "error": {"code": "authority_mismatch", "message": str(exc)}})
    except (OSError, json.JSONDecodeError) as exc:
        emit_json({"ok": False, "query": "threshold", "error": {"code": "bad_input", "message": str(exc)}})
    except (DexUnavailable, NcpUnavailable, NcpInputError) as exc:
        emit_json({"ok": False, "query": "threshold", "error": {"code": "engine_unavailable", "message": str(exc)}})
    return 1


def _cmd_cached_facts(command, operator, species, game_format, top_n, fmt, **options):
    try:
        if command == "ko-scan" and options.get("direction") not in ko_scan.DIRECTIONS:
            raise counters.QueryError("direction must be both, attacking or defending", query="direction")
        canonical, top_n, flags = counters.prepare(species, game_format, top_n, resolve_names)
        cache = oppcache.load_cache(game_format, environment.CURRENT_RULE)
        if cache is None:
            raise counters.QueryError("no opponent cache for the current rule and format", code="not_found", query=command)
        if (cache.get("built_for") or {}).get("rule") != environment.CURRENT_RULE:
            raise oppcache.StaleCacheError("opponent cache does not match the current rule")
        out = operator.query(cache, canonical, game_format, top_n, **options)
    except counters.QueryError as exc:
        emit_json(exc.result())
        return 0 if exc.code == "not_found" else 1
    except oppcache.StaleCacheError as exc:
        emit_json({"ok": False, "query": command, "error": {"code": "authority_mismatch", "message": str(exc)}})
        return 1
    except (DexUnavailable, OSError, ValueError, KeyError, TypeError) as exc:
        emit_json({"ok": False, "query": command, "error": {"code": "engine_unavailable", "message": str(exc)}})
        return 1
    built = cache.get("built_for") or {}
    out["environment"], warns = environment.resolve(data_rule=built.get("data_rule"), data_seasons=built.get("data_seasons"))
    out["name_resolution"] = flags
    if warns:
        out["warnings"] = warns
    _emit_name_flags(flags)
    if fmt == "json":
        emit_json(canonical_spreads(out))
    else:
        print(operator.format_md(out))
    return 0


def cmd_oppmatrix(species: str | None, fmt: str | None, season: str | None,
                  vs: str | None, out_fmt: str, as_checks: bool = False, all_species: bool = False) -> int:
    """Read the precomputed opponent observed-build matchup cache (M5 step 2). FACTS ONLY —
    a retained-build reference grid, every cell `low` confidence (reason=vs-observed-build); it is
    NOT your team (match a real team LIVE via `matchup`). Format is REQUIRED (metagames never mixed);
    season defaults to the current base. Explicit `--all` prints the whole matrix; `species` restricts
    that attacker's row; with `--vs` only the one ordered (attacker -> defender) cell. The cache is
    built by the dev pipeline (`update.py team-cache`); a missing file is reported, not an error."""
    if not fmt:
        print(i18n.t('team_oppmatrix_needs_format'), file=sys.stderr)
        return 2
    if (not species and not all_species) or (vs and not species) or (all_species and (species or vs)):
        print("oppmatrix requires an attacker: team.py oppmatrix Garchomp --game-format double "
              "[--vs Charizard]. Use --all explicitly for the full matrix (tens of MB); "
              "do not combine --all with an attacker or --vs.", file=sys.stderr)
        return 2
    season = season or environment.CURRENT_SEASON
    # The cache is keyed by RULE. `--season` stays accepted (it is how users think about the
    # environment) but resolves to its regulation first: seasons sharing a rule share one matrix,
    # so `--season M-3` and `--season M-4` under M-B correctly read the same file.
    rule = environment.rule_for_season(season) or environment.CURRENT_RULE
    cache = oppcache.load_cache(fmt, rule)
    built_for = (cache or {}).get("built_for") or {}
    stamp, env_warn = environment.resolve(
        season, None,
        data_rule=built_for.get("data_rule"),
        data_seasons=built_for.get("data_seasons"),
    )
    if cache is None:
        msg = i18n.Msg('team_oppmatrix_no_cache', rule=rule, fmt=fmt)
        if out_fmt == "json":
            emit_json(canonical_spreads({"ok": False, "query": {"format": fmt, "season": season,
                                                                  "rule": rule},
                              "environment": stamp,
                              "error": {"code": "no_cache", "message": msg}}))
        else:
            print(_env_header(stamp, env_warn) + "\n\n" + msg)
        return 0
    # Canonicalize aliases / Mega display names. The check view intentionally keeps a species query
    # as a species so it expands to every retained build; the raw KO view still resolves a plain name
    # to its representative row for its compact historical CLI behavior.
    warns: list[str] = list(env_warn)
    canonical_atk = _canonicalize_oppmatrix_name(species, warns) if species else None
    canonical_dfd = _canonicalize_oppmatrix_name(vs, warns) if vs else None
    atk = canonical_atk if as_checks else (_resolve_cache_key(cache, canonical_atk)
                                           if canonical_atk else None)
    dfd = canonical_dfd if as_checks else (_resolve_cache_key(cache, canonical_dfd)
                                           if canonical_dfd else None)
    if not as_checks and ((canonical_atk and atk is None) or (canonical_dfd and dfd is None)):
        missing = canonical_atk if atk is None else canonical_dfd
        if out_fmt == "json":
            emit_json({"ok": False, "query": "oppmatrix", "environment": stamp,
                       **({"warnings": warns} if warns else {}),
                       "error": {"code": "not_found", "message": f"no retained build for the exact battle form {missing}"}})
        else:
            print(f"no retained build for the exact battle form {missing}")
        return 0
    if dfd and not atk:
        print(i18n.t('team_oppmatrix_vs_needs_attacker'), file=sys.stderr)
        return 2
    if as_checks:
        # Derived check-grade view over the retained observed-build grid (design §17). A REFERENCE grid,
        # not your team; `species` restricts the row and `--vs` restricts its opponent.
        derived = oppcache.derive_checks(cache, atk, dfd)
        if out_fmt == "json":
            query = {"species": species, "resolved_attacker": atk,
                     "vs": vs, "resolved_defender": dfd, "format": fmt,
                     "season": season, "as_checks": True}
            payload = {"query": query,
                       "built_for": cache.get("built_for"), "environment": stamp, **derived}
            if warns:
                payload["warnings"] = warns
            emit_json(canonical_spreads(payload))
        else:
            print(_env_header(stamp, warns) + "\n")
            blocks = []
            for attacker_key, coverage in (derived.get("summaries") or {}).items():
                blocks.append(f"## {attacker_key}\n\n" + format_check_coverage_md(
                    {**derived, "check_coverage": coverage}))
            print("\n\n".join(blocks) if blocks else format_check_coverage_md(
                {**derived, "check_coverage": None}))
        return 0
    if out_fmt == "json":
        if atk and dfd:
            payload: dict = {"attacker": atk, "defender": dfd, "cell": oppcache.cell(cache, atk, dfd)}
        elif atk:
            payload = {"attacker": atk, "row": oppcache.attacker_row(cache, atk)}
        else:
            payload = cache
        payload = {"query": {"species": species, "resolved_attacker": atk, "resolved_defender": dfd,
                             "format": fmt, "season": season},
                   "built_for": cache.get("built_for"), "confidence": cache.get("confidence"),
                   "confidence_reason": cache.get("confidence_reason"), "environment": stamp, **payload}
        if warns:
            payload["warnings"] = warns
        emit_json(canonical_spreads(payload))
    else:
        print(_env_header(stamp, warns) + "\n")
        print(oppcache.format_oppcache_md(canonical_spreads(cache), attacker=atk, defender=dfd))
    return 0


def _canonicalize_oppmatrix_name(name: str, warns: list[str]) -> str:
    """Resolve a raw alias / Mega display name to the cache's key (the meta canonical English name).
    On a dex miss, keep the raw name (the lookup just won't match — reported, not fatal)."""
    if name.startswith("variant:"):
        return name
    try:
        info = lookup_pokemon([name], fuzzy=True).get(name, {})
    except DexUnavailable:
        warns.append(i18n.Msg('team_dex_down_query_cache'))
        return name
    if info.get("found"):
        if info.get("resolution"):
            rec = info["resolution"]
            warns.append(i18n.Msg('team_name_corrected', frm=name, to=info.get("name"),
                                 distance=rec.get("distance"), score=rec.get("score")))
        return info.get("name") or name
    warns.append(i18n.Msg('team_dex_unresolved_cache', name=repr(name)))
    return name


def _resolve_cache_key(cache: dict, canonical: str) -> str:
    """Map a dex-canonical name to the matrix key it actually lives under.

    The variant-expanded matrix is keyed by `variant_id` (`<species>#<item>|<ability>`), so a plain
    species query resolves to that species' MODAL build — the one a reader means by default. Passing
    a `variant_id` verbatim still wins outright, which is how you ask for a specific build.

    A Mega is ranked by meta under its BASE name (e.g. 'Staraptor') while the row's ``run_form`` is
    the Mega ('Mega Staraptor'), so a 'Mega Staraptor' query — itself a valid dex canonical — must
    reverse-resolve to the build that actually runs as it."""
    if canonical is None:
        return canonical
    sets = cache.get("sets") or {}
    keys = oppcache.selector_keys(cache, canonical, list(sets))

    def prevalence(key):
        config = sets[key] or {}
        def number(field):
            value = config.get(field)
            return value if isinstance(value, (int, float)) else -1
        return bool(config.get("is_modal")), number("coverage"), number("variant_count")

    return max(keys, key=prevalence, default=None)


def _build_search_query(query_arg: str | None, species: list[str] | None, has_move: list[str] | None,
                        has_item: list[str] | None, has_ability: list[str] | None,
                        fmt: str | None, season: str | None, limit: int | None, collapse: bool) -> dict:
    """Assemble the search query. `--query <json>` is the authoritative, fully-general form (per-member
    conjunctions); the convenience flags build one slot per value for the common cases."""
    if query_arg:
        parsed = json.loads(query_arg)   # JSONDecodeError (a ValueError) on malformed json → bad_input
        if isinstance(parsed, list):
            q = {"members": parsed}
        elif isinstance(parsed, dict):
            q = dict(parsed)
        else:
            raise ValueError(f"--query must be a JSON object {{members:[...]}} or a list of slots, "
                             f"got {type(parsed).__name__}")
    else:
        members = [{"species": s} for s in (species or [])]
        members += [{"move": m} for m in (has_move or [])]
        members += [{"item": it} for it in (has_item or [])]
        members += [{"ability": ab} for ab in (has_ability or [])]
        q = {"members": members}
    if fmt:
        q["game_format"] = fmt
    if season:
        q["season"] = season
    if limit is not None:
        q["limit"] = limit
    if collapse:
        q["collapse"] = True
    return q


def _format_member_line(m: dict) -> str:
    bits = [b for b in [m.get("item"), m.get("ability"), m.get("nature")] if b]
    head = f"  - {m.get('species')}" + (f" @ {' · '.join(bits)}" if bits else "")
    moves = ", ".join(m.get("moves") or []) or "—"
    sp = f"  [{'/'.join(f'{k}{v}' for k, v in (m.get('spread') or {}).items())}]" if m.get("spread") else ""
    return f"{head}{sp}\n      {moves}"


def _format_search_md(out: dict) -> str:
    lines = [f"### {i18n.t('team_search_title', fmt=out['game_format'])}",
             i18n.t('team_search_summary', match=out['match_count'], scanned=out['scanned'])]
    if out.get("unresolved_conditions"):
        u = ", ".join(f"{c['kind']}:{c['value']}" for c in out["unresolved_conditions"])
        lines.append(i18n.t('team_search_unresolved', u=u))
    if out.get("collapsed"):
        for g in out.get("groups", []):
            lines.append(f"\n**{' + '.join(g['species_set'])}** — {i18n.t('team_search_variants', n=g['variant_count'])}")
            for v in g["variants"]:
                lines.append(f"  · `{v['id']}`")
    else:
        for t in out.get("teams", []):
            perf = t.get("performance") or {}
            tag = f" ({perf.get('kind')}#{perf.get('placing')})" if perf.get("placing") else ""
            lines.append(f"\n`{t['id']}`{tag}")
            for m in t["pokemon"]:
                lines.append(_format_member_line(m))
    return "\n".join(lines)


# The sample library is AI-facing RAW observed teams. Per the library guardrail (design §17: the library
# participates only via aggregation / co-occurrence, and whole real teams are NOT echoed to end users),
# search/show tag their output so an AI treats it as retrieval-to-summarize, not a user-facing card. The
# user-facing aggregate is `repset` (archetype clusters). This is a signal, not an enforcement — the skill
# is the facts layer; honoring the guardrail is the AI bridge's job.
_OBSERVED_TEAM_DISCLOSURE = {
    "class": "observed_real_team",
    "audience": "ai_facing",
    "note": "Raw observed tournament team(s), facts only. Summarize / aggregate before presenting to an "
            "end user — do NOT echo whole real teams verbatim (library guardrail). For a user-facing "
            "aggregate use `repset` (archetype clusters).",
}


def cmd_search(query_arg, species, has_move, has_item, has_ability, fmt, season, limit, collapse,
               out_fmt: str, where=None) -> int:
    """Search the real-team library by any combination of per-member conditions (facts only).
    `where` (a boolean AND/OR/NOT JSON query, or '-' for stdin) takes precedence over the slot/flag form."""
    # Default to the CURRENT RULE pool across its season partitions. `--season M-x` remains an exact
    # partition query and `--season all` is the explicit all-regulations scan.
    cross_season = (season == "all")
    resolved_season = None if cross_season else season
    resolved_rule = None if (cross_season or resolved_season) else environment.CURRENT_RULE
    try:
        if where is not None:
            ast = json.loads(sys.stdin.read()) if where == "-" else json.loads(where)
            out = libsearch.search_where(ast, game_format=fmt, season=resolved_season, rule=resolved_rule,
                                         limit=limit, collapse=collapse)
        else:
            q = _build_search_query(query_arg, species, has_move, has_item, has_ability, fmt, resolved_season, limit, collapse)
            out = libsearch.search(q, season=resolved_season, rule=resolved_rule, collapse=collapse)
    except ValueError as e:
        # Malformed --query JSON (json.JSONDecodeError is a ValueError) or a bad query shape/format is a
        # genuine bad REQUEST (§3): emit the uniform error on stdout, exit 1 — so an AI caller parsing the
        # JSON gets a `bad_input` code, not an unparseable traceback (matches cmd_show's error discipline).
        if out_fmt == "json":
            emit_json({
                "ok": False, "query": where if where is not None else (query_arg or "(flags)"),
                "error": {"code": "bad_input", "message": str(e)}})
        else:
            print(i18n.t('team_search_bad_input', e=e))
        return 1
    except DexUnavailable as e:
        print(i18n.t('team_dex_unavailable', e=e), file=sys.stderr)
        return 2
    if cross_season:
        out["environment"] = {"cross_season": True,
                              "note": "all seasons incl. old regulations; each team's own `season` labels it"}
        env_warn: list = []
        stamp = None
    else:
        if resolved_rule:
            stamp, env_warn = environment.resolve(data_rule=resolved_rule,
                                                  data_seasons=out.get("data_seasons") or [])
        else:
            stamp, env_warn = environment.resolve(data_season=resolved_season,
                                                  data_rule=environment.rule_for_season(resolved_season))
        out["environment"] = stamp
        if env_warn:
            out["warnings"] = (out.get("warnings") or []) + list(env_warn)
        if out.get("scanned") == 0:
            # nothing scanned = the season partition is empty (usually a season typo), NOT "no team
            # matches your query"; mirror observed/repset so a mistyped --season is not read as evidence
            # of absence (zero rows != no evidence — audit 2026-07-06).
            out["warnings"] = (out.get("warnings") or []) + [
                i18n.Msg('team_observed_empty_partition',
                         season=resolved_season or resolved_rule, fmt=(fmt or "all"))]
    out["disclosure"] = _OBSERVED_TEAM_DISCLOSURE
    if out_fmt == "json":
        emit_json(out)
    else:
        header = (i18n.t('team_search_cross_season') if cross_season else _env_header(stamp, env_warn))
        print(header + "\n")
        print(_format_search_md(out))
        print("\n_" + i18n.t('team_observed_disclosure') + "_")
    return 0


def cmd_show(team_id: str, fmt: str | None, season: str | None, out_fmt: str) -> int:
    """Fetch one team's full config by the content id returned from `search`."""
    team = libsearch.get_team(team_id, game_format=fmt, season=season)
    if team is None:
        # A graceful lookup-miss (§3): uniform error shape on stdout for json, localized prose for md,
        # exit 0 — matching `oppmatrix`'s no-cache path, not a hard failure.
        if out_fmt == "json":
            emit_json({
                "ok": False, "query": team_id,
                "error": {"code": "not_found", "message": f"no team with id {team_id}"}})
        else:
            print(i18n.t('team_show_not_found', id=team_id))
        return 0
    team["disclosure"] = _OBSERVED_TEAM_DISCLOSURE
    if out_fmt == "json":
        emit_json(team)
    else:
        perf = team.get("performance") or {}
        tag = f" ({perf.get('kind')} #{perf.get('placing')})" if perf.get("placing") else ""
        print(f"### `{team['id']}` · {team['season']} {team['format']}{tag}")
        for m in team["pokemon"]:
            print(_format_member_line(m))
        print("\n_" + i18n.t('team_observed_disclosure') + "_")
    return 0


# Machine-readable I/O contract (dev/conventions.md), emitted by `schema` so an AI caller
# can learn the team CLI's commands + shapes without reading source. Pinned by the team contract test.
TEAM_SCHEMA = {
    "skill": "pokemon-champions-team",
    "contract": "references/schema.md",
    "input": "team-json or Showdown-text file; member: {species|name, ability, item, nature, "
             "spread:{hp,atk,def,spa,spd,spe}, moves:[str], tera, completeness}",
    "stat_keys": ["hp", "atk", "def", "spa", "spd", "spe"],
    "output_artifacts": {
        "out": "JSON commands accept --out <file>: atomically save the full result from the same computation; input paths are protected",
        "views": "--view full|summary affects stdout only; review/try default summary, other supported commands default full. Summary reports cannot certify gates. Legacy --matchup-view/--slate-view accepted; conflicting flags refuse",
        "summary_limit": "topmeta/oppmatrix --summary-limit N (positive, default 6) requires --view summary; caps displayed tables/panels in source order, never reranks population entries; projection.omitted discloses omissions",
        "landscape_cache": "landscape caches structural facts by source content, implementation, query and live handover validity; --no-cache bypasses local reads/writes; cache:{status,fingerprint}; authority changes refuse mixed results",
        "session": "per-op out saves the full operation result; session --out saves full result envelopes; stdout preserves selected per-op views",
        "sp": "public configuration SP keys are hp/atk/def/spa/spd/spe; legacy short-key caches remain readable; original receipt-bound input objects stay unchanged",
        "names": "authored species/item/ability/nature/moves normalize on a working copy through the public dex resolver; fuzzy corrections are disclosed; unresolved values stay for strict validation",
    },
    "ruleset": (lambda rs: {
        "rule": rs.rule,
        "sp_per_stat_cap": rs.sp_per_stat_cap,
        "sp_total_cap": rs.sp_total_cap,
        "team_size": [rs.team_min, rs.team_max],
        "species_clause": rs.species_clause,
        "item_clause": rs.item_clause,
        "moves_per_pokemon": rs.moves_per_pokemon,
        "_note": "Champions registration HARD CAPS that `validate` enforces — build spreads/teams to "
                 "these BEFORE validating. SP is NOT the EV system: each stat's SP <= sp_per_stat_cap "
                 "and the six-stat total <= sp_total_cap (so 32/32 in two stats + 2 = the 66 budget, "
                 "never 252/508). item_clause=each held item at most once across the team; "
                 "species_clause=each base species at most once; team_size=[min,max] registered.",
    })(get_ruleset()),
    "review_spec": {
        "input": "review team.json [--context ctx.json] [--focus targets.json] [--top-k 8]; JSON only",
        "readiness": "invalid|needs_input|unknown|ready|partial|calculation_unavailable; missing moves/ability/nature/SP or untrusted source sets never run CHECK or select",
        "trial": "try team.json --changes changes.json; nonempty [{slot:zero-based int,set:{species?,item?,ability?,moves?,nature?,spread?}}]; distinct slots; whole fields replace; different species clears prior set; no source mutation",
        "focus": "{opponents:[canonical-or-alias species],members:[species|zero-based slot]}; named opponents supplement top-K without fabricated ranks; absent/ambiguous members stay explicit; diagnose --focus uses the coordinated review contract (JSON, aspect all)",
        "evidence": "full review.evidence_index:[{ref:{scope:team_floor|opponent_variant|member_cell,opponent,variant_id?,member_slot?},fact:complete qualified object}]; summary keeps refs only",
        "claims": "review-claims claims.json accepts {report:full review,claims:[{claim:str,ref:exact reference,expect:complete fact}]}; verifies receipt and recomputes one battery for all three scopes; environment drift/missing evidence refuse",
        "gates": "--fail-on-verdict returns 1 for non-ready reviews or failed claims; default returns the report for repair. Incomplete reports may feed try but cannot certify claims; try.before/after are independently sealed reviews. Does not replace UEP slate/answer-audit",
    },
    "battle_scope_spec": {
        "commands": "review, try, select, matchup accept --opponents team.json and --conditions field.json; session uses inline opponents/conditions",
        "opponents": "1..6 members; each is species-only (retained observed variants) or a complete legal set (stated configuration). Partial overrides refuse; omitted item or null means none on a complete set. Format must agree with the requested battle",
        "conditions": "weather:rain|sun|sand|snow; terrain:electric|grassy|psychic|misty; names are case-insensitive and output uses Title Case; trick_room:boolean; unknown fields refuse",
        "scope": "specified roster replaces usage top-K; stated set replaces observed variants, coverage=1 is scenario coverage, never usage. Damage is recalculated in both directions; trick_room changes acts_first, not numeric faster. Static equal-priority reference, not full battle simulation",
    },
    "fill_trial_spec": {
        "input": "fill team.json --context need.json --slot N; N is an existing zero-based slot, removed before retrieval to release its species and item",
        "output": "complete observed joint builds only; team_member, changes:[{slot,set}], structure_diff, legality and set_speed; missing/meta-filled SP and occupied/avoided items excluded; all need fields checked on that actual build",
        "next": "choose explicit changes and pass to try; structural difference does not imply matchup improvement; no automatic selection or per-candidate matchup battery",
    },
    "tune_cell_spec": {
        "input": "tune spec.json --from-cell; JSON only; spec:{report:full sealed review,ref:member_cell reference,goal,solve_for,probability?,compare_items?,maximize?}",
        "binding": "recomputes the cell, binds actual battle forms and both exact configurations, move and weather/terrain/trick_room; solve_for must address the team member",
        "output": "source_check, benchmark, bound_sets, threshold nature/item lanes and trials; unreachable lanes retain closest damage percentages. trials include explicit changes, legality and available; only available trials may be adopted, then try and UEP gates",
    },
    "scenario_spec": {
        "input": "JSON object: attacker, defender, move, format:single|double; optional attacker_set, defender_set, field",
        "set_overrides": "item, ability, nature, sps:{hp,atk,def,spa,spd,spe} (replaces whole spread), boosts:{atk,def,spa,spd,spe}, status",
        "field": "weather:rain|sun|sand|snow; terrain:electric|grassy|psychic|misty; reflect, light_screen, aurora_veil, helping_hand booleans",
        "source": "current-rule opponent cache; highest-coverage retained exact-form build or exact variant; no outside-cache fallback",
        "legality": "strict Champions dex item, ability, nature, learnset and Mega stone; SP 0..32 per stat and <=66 total",
        "output": "actual actors, source builds and overrides, applied field/conditions, rolls, HP, KO chance and caveats",
    },
    "threshold_spec": {
        "input": "scenario fields plus solve_for, goal, probability, compare_items, maximize and speed_field",
        "solve_for": sorted(threshold.SOLVE_FOR), "goal": sorted(threshold.GOALS),
        "probability": ["guaranteed", "any"],
        "compare_items": "up to three additional Champions items or null; each legal, same-form item gets a separate lane",
        "maximize": "optional distinct SP stat on the solved side, e.g. attacker.spe after an OHKO requirement; not available with defender.bulk",
        "budget": "every evaluated spread uses current rule per-stat and total SP caps, fixing other stats",
        "result": "per nature/item lane: minimum feasible SP or null, nearest goal probability/speed when unreachable, optional maximizing allocation, full witness and qualifications",
    },
    "ko_scan_spec": {
        "top_n": {"min": 1, "max": counters.TOP_N_MAX, "default": counters.TOP_N_DEFAULT,
                  "meaning": "opponent usage-rank cutoff, not output count"},
        "direction": {"choices": list(ko_scan.DIRECTIONS), "default": "both"},
        "reference": "highest retained coverage of the exact battle form (variant-id breaks ties), or an exact variant selector; other builds disclosed",
        "authority": "cached NCP ko_chance; never infer 2HKO from static damage buckets",
        "groups": "hits 1/2, calculator_guaranteed and complete mechanical qualifications; separate concrete build rows",
        "missing": "absent/unclassifiable evidence remains unknown, never proof of survival",
        "views": "summary caps rows within every qualification group, preserving group metadata/counts; out saves full evidence",
    },
    "counters_spec": {
        "top_n": {"min": 1, "max": counters.TOP_N_MAX, "default": counters.TOP_N_DEFAULT,
                  "meaning": "candidate usage-rank cutoff, not output count"},
        "identity": "exact run_form, or one exact variant id; never expand an ordinary form into a Mega",
        "grade_authority": "opponent cache check_matrix, explained by existing checks.build_check; mismatch refuses",
        "rows": "one candidate build, all retained target builds; full C2/C1 observed floor only when calculation_complete",
        "conditional_rows": "floor remains C0; answers and blocked_by retain every named target variant and its evidence",
        "incomplete_rows": "grade and observed_floor null; calculated_subset_floor never establishes a complete answer",
        "coverage": "sample mass within coverage_basis; parent usage rank does not imply form usage",
        "views": "summary limits whole rows in usage order; all answer/blocker qualifiers of retained rows survive; out saves full result",
    },
    "correction_spec": {
        "schema": "team-corrections.v1",
        "input": "team-json only; --context optional; strict dex alias normalization is reported, no fuzzy repairs",
        "output": "input_fingerprint (original JSON content hash), applied:false, validation, name_normalization, suggestions, gaps, item_shortlist, environment",
        "suggestions": "ability_illegal|mega_item|item_pool: options; ineffective_mega_stone: advisory options; item_clause: all holders with their options and minimum_changes",
        "option": "{changes:[{path,member_index,field,from,present,to}],available,blocked_by_members?,blocked_by_avoid?}; paths address original pokemon/decklist slots; absent fields need add semantics, not replace",
        "scope": "dex-verified alphabetical convenience item shortlist plus null (no held item); not exhaustive or ranked. Preserve explicit Mega/base+own-stone identity. Unknown facts leave gaps, never guessed options",
        "adoption": "Host chooses and discloses changes on a copy, checks original hash and from/present preconditions, then reruns validate and affected UEP gates; options from separate issues/holders can conflict",
        "exit": "0 for a successful suggestions query even if original validation is invalid; malformed inputs or unavailable dex return canonical error/exit 1",
    },
    "speed_ladder_spec": {
        "set": {"optional_fields": sorted(speed_ladder.SET_FIELDS),
                "spread": "canonical SP keys; legacy aliases accepted; omitted stats in an explicit spread are 0",
                "item": "omitted from a stated set means no held item; a Mega defaults to its required stone",
                "boosts": "spe only, integer -6..6", "status": sorted(contracts.MEMBER_STATUSES)},
        "conditions": {"weather": "Sun|Rain|Sand|Snow|Harsh Sun|Heavy Rain|Strong Winds",
                       "terrain": "Electric|Grassy|Psychic|Misty", "tailwind": "none|mine|opponents|both",
                       "trickroom": "boolean; reverse order at equal priority, not speed values"},
        "population": "current rule pool, all seasons/event partitions accepted by the existing reader; no raw-team limit",
        "reference": "most frequent complete observed speed configuration per actual form; exact-form meta marginals only when absent",
        "limit": "positive integer, default 20 ranked parent species; dex-declared forms may expand the output row count",
        "shares": {"min_sample": speed_ladder.MIN_SAMPLE, "reliable": speed_ladder.RELIABLE,
                   "usual": speed_ladder.USUAL, "usually_ties": speed_ladder.USUAL_TIE,
                   "basis": "opponent reference against asked form's eligible observed sample; failures suppress labels"},
    },
    "commands": {
        "review": "<team.json> [--context ctx.json] [--focus targets.json] [--top-k 8] [--opponents roster.json --conditions field.json] [--view summary|full --out full.json] -> completeness-gated validate/diagnose/matchup/select from one battery; see review_spec and battle_scope_spec",
        "try": "<team.json> --changes changes.json [review options] -> explicit copy, before/after full reviews, replace structural differences and qualified opponent floors; see review_spec",
        "review-claims": "<claims.json> [--fail-on-verdict] -> verify a full review and recompute its scoped claims; see review_spec",
        "localize": "<team.json> -> official trilingual member/move/item/ability/nature labels from dex; unresolved inputs remain explicit; also available in session",
        "type-matchup": "[--attack-type TYPE] [--pokemon NAME | --types TYPE [TYPE]] [--ability NAME] -> JSON type effectiveness with defender ability modifiers; ambiguous natural abilities remain conditional",
        "rules-facts": "-> JSON current reviewed registration rules and pick sizes from the meta authority",
        "threshold": "<threshold.json> -> legal SP cliffs for OHKO/2HKO/survival/outspeed; nature and item lanes, closest feasible result, optional maximize",
        "scenario": "<scenario.json> -> exact-form retained common builds plus stated set/field overrides; current-rule cache, strict dex legality and NCP damage",
        "ko-scan": "<species|variant-id> --game-format single|double [--top-n 30] [--direction both|attacking|defending] [--view summary --out full.json] -> cached exact-form OHKO/2HKO reference with qualification groups and explicit missing evidence",
        "counters": "<species|variant-id> --game-format single|double [--top-n 30] [--view summary --summary-limit 6 --out full.json] -> reverse CHECK by individual candidate build; exact target form, complete C2/C1 floors, conditional C0 answers/blockers and missing evidence; usage order, no score",
        "speed-ladder": "<species> --game-format single|double [--set JSON-or-file] [--conditions JSON-or-file] [--limit N] [--opponent-ranges] -> exact-form reference speeds and complete rule-pool observed distributions; one calculator batch; no team cap",
        "code": "<public-code> [--cache-only] -> {schema:team-code.v1,code,team,cache}; explicit bounded network lookup, unknown format/season/rule stay null",
        "code-status": "-> private operator configuration/dependency/circuit status; never contains identity or tokens",
        "parse": "<file> [--game-format single|double] -> {schema_version, format, season, rule, pokemon:[member], provenance}; format conflicts refuse",
        "validate": "<file> [--context] -> {status: valid|invalid|unknown, valid, confidence, errors, warnings}",
        "suggest-fixes": "<team.json> [--context ctx.json] -> JSON team-corrections.v1: original validation, strict alias normalization, explicit ability/Mega/item choices and every Item Clause holder. Never applies edits; choose on a copy and rerun validate/UEP. See correction_spec.",
        "diagnose": "<file> [--aspect defense|offense|speed|roles|all] [--context] "
                    f"[--with-check [--top-k N ({MATCHUP_TOP_K_MIN}..{MATCHUP_TOP_K_MAX})]] -> per-aspect "
                    "objective signals + evidence. offense carries luck_lines (the team's <100%-accuracy moves, "
                    "dex accuracy); context variance_tolerance=averse FLAGS them (surfaced either way, never a "
                    "score). --with-check adds ncp-grounded check_coverage as a complement to the type-layer "
                    "defense (default off keeps diagnose ncp-free)",
        "select": f"<file> [--with-check [--top-k N ({MATCHUP_TOP_K_MIN}..{MATCHUP_TOP_K_MAX})]] [--opponents roster.json --conditions field.json] -> "
                   "6->3 (single) / ->4 (double) objective facts "
                   "(no strength score); --with-check adds per-lineup CHECK coverage vs meta top-K "
                   "(opt-in, ncp-grounded, per-opponent facts — lineups NOT ranked). Each lineup "
                   "enumerates zero-or-one active Mega states; mega_routes counts each registered "
                   "option's exclusive/shared lineup availability without selecting a preferred route; see battle_scope_spec",
        "matchup": "<file> [--top-k N] [--opponents roster.json --conditions field.json] [--as-checks] [--matchup-view full|summary] -> 1..N actual "
                   "member configurations x meta top-K speed/type/damage battery; top_k is caller-selected "
                   f"within {MATCHUP_TOP_K_MIN}..{MATCHUP_TOP_K_MAX} (default {MATCHUP_TOP_K_DEFAULT}). "
                   "each cell carries a derived CHECK grade (C2/C1/C0), + a check_coverage roll-up "
                   "(holes = opponents with no safe switch-in check). JSON full keeps the complete evidence "
                   "surface; summary keeps stable source/target/cell ids + KO/incoming/speed/atomic checks. "
                   "--as-checks renders the map; see battle_scope_spec",
        "tune": "<file> --context <benchmarks.json> -> SP cliff cards (multi-view, never a single best); "
                "or <cell-spec.json> --from-cell -> verified member-cell threshold lanes and explicit try edits",
        "fill": "<file> --context <need.json> -> candidate pool for a structured gap "
                "(need: resist/offense_type/role/min_speed/coverage_move_type/answer) in multiple explicit views "
                "(usage/off_meta/co_occurrence/tournament_sample/owned; no score). context meta_conformance "
                "picks the DEFAULT order (proven/unset=common-first, off_meta=rare-first). Off-meta also includes "
                "real-team-backed species outside the usage pool, with sample/witness ids. answer:name|[name] "
                "intersects counters on one exact variant and returns complete observed builds with team_member "
                "and answer_facts. Meta SP fallbacks/unknowns are omitted, occupied/avoided items excluded. "
                "Read answer_scope for retained target builds and static CHECK qualifications. "
                "--slot N releases the existing zero-based slot and returns complete legal replacement builds "
                "with changes for try, concrete Speed and neutral structural differences; see fill_trial_spec.",
        "replace": "<file> --context <{replace:{member,with}}> -> objective before/after diff of a "
                   "swap (defense/offense/speed/roles); never a better/worse verdict",
        "repset": "<species> --game-format single|double [--season] [--max-clusters N] -> up to N "
                  "real-team (item,ability) archetypes by prevalence, each with cluster/coverage/share/"
                  "sample/spread_origin/confidence; facts only, no strength score; [] when too thin. "
                  "Default reads the current rule pool across its eligible season partitions; "
                  "--season is an exact season partition query.",
        "oppmatrix": "<species> (or --species <species>, or explicit --all) --game-format single|double [--vs <defender>] [--season] [--as-checks] -> "
                     "precomputed observed-build matchup matrix over meta top-K (offense band/KO + speed "
                     "line per ordered pair); attacker rows only for real-team-backed species; EVERY cell "
                     "low confidence (vs-observed-build); a reference grid, NOT your team (match live). "
                     "Name selectors use exact cached run_form, with modal selection within that form; "
                     "exact variant ids select their own builds. Missing raw form returns not_found. "
                     "--as-checks derives the C2/C1/C0 grid (attacker x attacker reference view)",
        "topmeta": "--game-format single|double [--scope season|rule|high] [--species A B] -> JSON only. Top "
                   "Meta: the real-team library counted on the in-game metagame's fields (species "
                   "presence, moves/items/abilities/natures/spreads, partners) over dedup units (one "
                   "player's distinct composition / distinct build), joined with the in-game snapshot. "
                   "Every entry is ONE record carrying both sides: count/share + ingame "
                   "{kind: pct|bound|absent|rank|unlisted} + top_minus_ingame (null below "
                   f"{topmeta.COUNT_TIER_BELOW} units, a lower bound when delta_is_floor) + within_noise "
                   "(Wilson 95%, a muting aid, not significance) + count-tier markers "
                   "outside_ingame_top / unseen_in_top. Panel `tier` count|low|reliable decides "
                   "presentation, never existence. The rule scope spans seasons and compares with the "
                   "CURRENT in-game snapshot (compared_with.window: current_snapshot — the "
                   "two sides cover different time ranges; say so). A VIEW of the team library, not "
                   "a second metagame authority; no score field.",
        "search":"--game-format single|double [--season|--season all] [--limit N] [--collapse] + EITHER --query "
                  "'<json>' OR convenience flags (--species A B / --has-move / --has-item / --has-ability). "
                  "Query = list of member SLOTS; each slot is a conjunction {species?, move(s)?, item?, "
                  "ability?, nature?} and every slot must match a DISTINCT member. Values are dex-resolved "
                  "(Mega-compose + nicknames + fuzzy); exact battle forms match, including base+own-stone "
                  "equivalence to its Mega. Ordinary/X/Y/Z forms remain distinct. Unknown leaves retain "
                  "unknown truth under NOT. name_resolution and identity_gaps disclose corrections/gaps. "
                  "-> {game_format, season, slots, unresolved_conditions, scanned, match_count, "
                  "teams:[{id, pokemon:[member], performance, fetched_at}] | (with --collapse) groups:"
                  "[{species_set, variant_count, variants:[team]}], environment, disclosure}. Facts "
                  "only, no strength score. `disclosure` marks the rows AI-facing raw observed teams: "
                  "summarize/aggregate before showing an end user, do NOT echo whole teams verbatim (library "
                  "guardrail); use `repset` for a user-facing aggregate. Defaults to the current rule pool "
                  "(same-rule seasons such as M-3+M-4 may both contribute; old regulations are excluded); "
                  "`--season M-x` reads exactly that partition; `--season all` scans every stored season. "
                  "OR `--where '<json>'`: a boolean "
                  "AND/OR/NOT query over leaf SLOTS — a node with an and/or/not key is a combinator, else it "
                  "is a leaf slot {species/move/item/ability/nature}; a leaf means the team HAS a member "
                  "matching it (EXISTENCE, not distinct members — use --query members for distinct). '-' reads "
                  "stdin; malformed -> bad_input. Same output shape + an echoed `where`.",
        "show": "<team-id> [--game-format] [--season] -> the full facts-only config for one team by the "
                "content id from `search` (id namespaces season+format, so it self-locates), + a `disclosure` "
                "marker (AI-facing raw observed team — aggregate before showing users). id is read-time "
                "(teamid.compute_id_v1); a library refresh that changes a team's content changes its id.",
        "context-audit": "[team-file] --context <ctx.json> -> UEP P2 front gate: field_status (which "
                         "field is mechanically consumed by what — the single source), anchor fact, "
                         "contract_errors (reported, not refused), three-level gaps (blocking must-ask / "
                         "safe_default apply+disclose with info_value / conflict+ambiguity one targeted "
                         "question), unresolved_names, and audit_receipt (capability-chain start; content "
                         "fingerprint — proves the audit ran, not that gaps were acted on). Facts only: "
                         "it never decides ask-vs-default for you.",
        "frame": "--game-format single|double --context <ctx.json> --audit-receipt <context-audit "
                 "output.json> [--draw N --seed INT --seen-teams teams.json] -> UEP P4.5 assembly front-door "
                 "(JSON+md). draw adds a seeded weighted sample of frame ids with low core overlap; it leaves "
                 "the full skeleton array and receipt intact. Shown cores use Species Clause grouping; "
                 "repeat_unavoidable discloses fallback when every remaining core repeats. Consumes context-audit's "
                 "audit_receipt (chain link — recomputed, mismatch refuses) and emits DATA-GROUNDED "
                 "skeletons you assemble ON (so [assemble] is not built from the training prior): "
                 "each skeleton = {frame_id (mechanical hash), group_signature{axis,key} (the Tier-1 "
                 "key actually used: speed_control in doubles, role_signature (hazard/setup/recovery/"
                 "pivot) in singles, structure_not_observed for species-only library rows whose "
                 "signature cannot be read at all), structural_profile (speed_control "
                 "modes / role norms — FACTS, never a team-type label), prevalence, core_candidates "
                 "[each with its REAL repset (item,ability) clusters + set_guidance (moves/nature/sps "
                 "= reference, NOT a lock) + within_group_share/pool_share, in TWO tiers by role: "
                 "'core-partner' is the backbone and slate RED-eliminates an off-cluster one; "
                 "'recurring-partner' recurs below that bar, arrives equally grounded, and slate only "
                 "REPORTS a deviation on it (yellow, disclose at answer-audit, never eliminated) — "
                 "either way this is the frame-GROUP view, NARROWER than a standalone "
                 "`repset <species>`], core_tiers{anchor,core,recurring_partner}, "
                 "flex_slots{open_count,substitutable_count}, "
                  "observed_facts (mega_registration_reference selects the frame-group distribution "
                  "when reliable, otherwise the whole frame pool; it is a descriptive registration "
                  "prior, not a reserved second-Mega slot; "
                 "fillers describe how real teams vary — not a to-fill list)}. Anchor = context "
                 "locked+prefer; ordered by prevalence under meta_conformance (an ANCHOR build shows "
                 "ALL frames so an off-meta one is never dropped). Grounded sets come ONLY from repset "
                 "(real joint); grounding=null means thin/off-meta (build it yourself + disclose, "
                 "never stitch from meta). An EMPTY skeleton (no tier cleared the support bar) is "
                 "never confidence:medium — it reports low + no-recurring-backbone, and no_backbone "
                 "says the same for the run: ground every member through repset/search and disclose "
                 "that the frame supplied no roster. Emits frame_receipt the slate binds against. "
                 "FACTS only, no strength score, no best-archetype.",
        "slate-init": "<assembly.json> --context context.json --audit-receipt audit.json "
                      "[--frame-output full-frame.json] [--intake-output last-intake-step.json] -> directly usable "
                      "slate {context,audit_receipt,teams,frame_bindings?,onboarding,assembly}. "
                      "Input {candidates:[{team:<unchanged team-json> OR members:[member-json|{from_frame:<exact "
                      "battle-form species>}], frame_id?, off_meta?, deviations?, mega_deviation?, off_meta_build?}]}. "
                      "Explicit frame references copy that frame's joint primary set into canonical team-json; "
                      "missing fields remain null, never defaulted. Frame/audit receipts are checked. The helper "
                      "neither selects candidates nor acknowledges deviations. Intake carries explicit answered ids "
                      "only for the matching final context; unresolved dimensions remain incomplete. "
                      "Evaluate this output with the same --frame-output; assembly itself is not certification.",
        "slate-evaluate": f"<slate.json> [--top-k N ({MATCHUP_TOP_K_MIN}..{MATCHUP_TOP_K_MAX})] [--slate-view full|summary] "
                          "[--frame-output <saved frame output.json>] -> UEP "
                          "P5 candidate fact-matrix gate (JSON only, AI-facing). Input {context:"
                          "<build-context>, audit_receipt:<the FULL "
                          "receipt OBJECT {kind:'audit_receipt', fingerprint, audited_at, ...} "
                          "copied from context-audit's --format json output on the SAME context — a "
                          "bare fingerprint string refuses (bad_audit_receipt); the fingerprint is "
                          "recomputed here, mismatch refuses>, teams:[team-json, ...], frame_bindings?:"
                           "[{frame_id, off_meta?:[species], deviations?:[{species,reason}], "
                           "mega_deviation?:str|{reason,...}, "
                           "off_meta_build?:bool}]}. Funnel: "
                          "cheap stage for every candidate "
                          "(contract + validate + constraint_satisfaction + structural_profile + "
                           "objective flags + Mega-registration relation); reliable observed "
                           "minority/rare registration lanes require mega_deviation under the proven "
                           "view; explicit mega_posture is a hard count constraint. The survivor set "
                           "must retain an observed modal registration lane unless posture/off_meta "
                           "explicitly says otherwise. Matchup-vs-top-K battery for SURVIVORS only. "
                          "--frame-output activates the P4.5 BUILD-FLOW binding: frame_bindings[i] "
                          "declares which frame teams[i] builds on; each core-bearer (a member at the "
                          "CORE tier of that frame's core_candidates) must carry an (item,ability) in "
                          "its repset clusters, else a frame_binding deviation — RED (ungrounded + no "
                          "rationale/off_meta) ELIMINATES in the funnel, YELLOW (acknowledged via "
                          "off_meta/deviations) survives + must be disclosed at answer-audit. A member "
                          "matching a 'recurring-partner' candidate binds SOFTLY: an off-cluster set "
                          "is always YELLOW (reported + owed a disclosure, never eliminated), because "
                          "that tier is offered, not committed to. Off-frame members "
                          "get advisory-only checks (set_guidance is never checked — only item+"
                          "ability). A broken frame_receipt refuses the run. Output "
                          "preserves input order: per-candidate facts (incl. library_overlap — verbatim/"
                          "same-composition matches vs the stored library, a FACT not an "
                           "elimination; frame_binding and neutral 6-pick-N selection facts when bound) "
                           "+ a sortable grid (NO aggregate, "
                          "NO winner) + slate_receipt (for the future answer-audit). Quantitative "
                          "extremes carry evidence_id 'ncp:{fmt}:{attacker}|{defender}|{move}' "
                          "(re-runnable coordinates).",
        "checkpoint": "<saved slate-evaluate output.json> --slate <slate.json> -> post-slate, "
                      "pre-tune pause contract (JSON only, consumer: ai-orchestration). Reports "
                      "candidate_frames, open_decisions, questions_to_ask, and pause:true|false. "
                      "Run after EVERY build-flow slate-evaluate and before tune/final answer; if "
                      "pause:true, pause unless the slate context explicitly set direct_final:true "
                       "or skip_checkpoint:true. Slate survivors already carry neutral 6-pick-N "
                       "selection facts; checkpoint does not pause merely because multiple Mega "
                       "options are registered. Candidate/confidence/provenance choices only pause "
                       "when checkpoint_required:true; missing benchmarks are not a question. It "
                       "still pauses when the candidate set misses the bound frame's observed modal Mega-registration lane.",
        "answer-audit": "<draft.json> --slate <slate.json> --slate-output <saved slate-evaluate "
                        "output.json> -> UEP P6 back gate (JSON only, AI-facing). Refuses (exit 2) on "
                        "a broken receipt chain: the draft's slate_receipt must equal the saved "
                        "output's, the output must reproduce its own fingerprint from the slate "
                        "input, and the audit fingerprint must recompute from the slate's context. "
                        "Then reports pass/violations: structural checklist (recommended re-validated "
                        "+ hash-matched to a slated SURVIVOR, >=1 tradeoff each, single-team "
                        "declared, convergence_rationale filled for every survivor, blocking gaps "
                         "and low-confidence facts disclosed, observed-registration deviations carry "
                         "reason/evidence/opportunity_cost, a slate or recommended set missing every "
                         "modal registration lane cannot pass, banned absolute-strength wordlist) + "
                        "library guardrail: a recommended team whose joint set IS a stored "
                        "observed team needs observed_provenance on its entry (adoption is "
                        "legitimate; silence is the violation) + "
                        "claim<->recompute (every claims[] entry carries an evidence_id under the "
                        "slate grammar; its expect numbers are recomputed via the same set "
                        "construction the battery used — fabricated numbers are violations, sibling "
                        "outages are disclosed notes). See context_specs.draft_spec.",
        "draft-init": "--slate <slate.json> --slate-output <saved slate-evaluate output.json> "
                      "Rebinds full saved facts, carries slate-init onboarding without guessing answers, "
                      "and supplies optional claim_candidates (exact variant and numeric expect; not auto-added "
                      "to draft.claims). "
                      "[--recommended I J] -> JSON skeleton for answer-audit: copies environment, "
                      "slate_receipt, selected survivor teams, convergence_rationale stubs for every "
                      "survivor, tuning_summary stub, and claim/deviation placeholders. Form helper "
                      "only; the AI must fill the substantive empty fields before answer-audit.",
        "landscape": "--game-format single|double [--season] [--context] [--species A B] [--limit N] "
                     "[--aspects offense defense] [--scope high] "
                     "-> UEP P4 structural distributions over the real-team library, aggregated "
                     "through the shared profile vocabulary: speed_control_modes / structural_signals "
                     "/ role_composition_norms (count+share+sample) + observed_cores (co-occurrence "
                     f"counts, support >= {repset.MIN_SAMPLE}; --species or context locked/prefer "
                     "narrows to co-members). --aspects opts into offense_norms/defense_norms: "
                     "team-level PRESENCE distributions per type (stab/covered/thin/hard-gap attack "
                     "coverage; resist/immune presence + stacked weaknesses) — layout facts, never "
                     "an adequacy target. meta_conformance=off_meta flips the core view rare-first "
                     f"(an ordering, never a score). thin flag below {landscape.THIN_BAR} teams. "
                     "Counts only — no best core, no recommendation. Default reads the current rule pool; "
                     "--season reads an exact season partition. --scope high selects native current-rule "
                     "published records by Top Meta performance criteria; population discloses record "
                     "counts and exclusions, not Top Meta identity units. No season/other-rule override.",
        "observed": "--game-format single|double [--season] [--context <build-context>] [--species A B] "
                    "[--order overlap|tier|recent|diversity] [--limit N] [--seed INT --seen-teams teams.json] "
                    "-> UEP P7 observed-team retrieval (JSON "
                    "only, consumer: ai-facing-evidence). diversity keeps constraint overlap first, then "
                    "minimizes maximum actual-form member overlap with shown/selected teams; already-shown "
                    "and duplicate rosters excluded. Seed randomizes only ties, otherwise tier/recency/id "
                    "break ties. seen-teams takes one team-json, an ordered list, or {teams:[...]}. "
                    "FACT rows over the real-team library: explicit "
                    "constraint overlap (locked/prefer/--species hit counts by exact dex battle form, "
                    "base+own-stone equivalent to its Mega), evidence_tier (provenance-kind ladder tournament > community > "
                    "ladder + declared sink unknown — categories, never a score), raw performance tags, "
                    "owned_coverage, recency, and "
                    "structural_signals (profile vocabulary) for the returned page. avoid_species excludes "
                    "mechanically by exact battle form and exact held item (avoid_items). Unknown forms "
                    "cannot prove an avoid constraint; excluded_by_identity counts those rows separately. "
                    "owned_coverage is an inventory fact with base-species grouping; owned_only is NOT a "
                    "filter here and is echoed under constraints_not_enforced. The thin flag measures "
                    "the post-exclusion pool; ordering keys are "
                    "overlap/tier/recent ONLY. Retrieval, not "
                    "generation: rows are stored teams (fetch one via show <id>); decompose into facts to "
                    "build YOUR OWN team — never echo a row to the user as the answer. Default reads the "
                    "current rule pool; --season reads an exact season partition.",
        "intake": "[--game-format single|double] [--onboarding|--next [--context ctx.json] "
                  "[--answered id...]] -> UEP P3 question catalog (JSON only, consumer: ai-orchestration). "
                  "18 entries (9 base questions tier=onboarding, each with answer_mode "
                  "choice|choice_or_text|text + 9 conflict templates tier=conflict) with trilingual "
                  "text/labels, options -> typed build-context/draft fields (maps_to), free-form "
                  "resolvers + examples + a no-op fallback, info_value, triggers_on (context-audit gap "
                  "vocabulary), skippable/must_confirm, defaults. THREE SURFACES (design §19.6): "
                  "default MENU for steady-state refinement — pick <=3 PER ROUND for the audit's gaps, "
                  "re-audit + iterate (<=3 is a batch size, NOT a total cap); --onboarding for a NEW "
                  "open-ended build (per build task) — guided_walk = the base set to COMPLETE once, "
                  "minus what --context answers (already_answered); --next to actually ASK it, "
                  "returning the next 1-3 related questions under key `batch` (with group/remaining_after; "
                  "NOTE: --onboarding lists the overview under `questions`, --next uses `batch`) one step "
                  "at a time — loop with --answered <resolved ids so far> (holds asked ids AND dims the "
                  "request resolved via draft/answer-shape; unknown ids -> rc2) until done:true. "
                  "--game-format narrows format-tagged options (no Tailwind in singles, no hazards).",
        "session": "<spec.json> OR --spec <inline JSON array|-> [--out full.json] [--fail-on-verdict] -> batched ops in one process; inline file/context/receipt values and backward {$ref:'/0/result/...'} references use complete prior results even when stdout is summary",
        "vocab": "-> queryable vocabulary + field-consumption surface (no file): {roles (need.role "
                  "taxonomy), exclude_tactics, style_lean, meta_conformance, mega_posture, variance_tolerance, "
                 "field_status (which build-context field is mechanically consumed by what — the single "
                 "source shared with context-audit), derived_fields}. Facts only.",
        "schema": "this contract",
    },
    "context_specs": {
        "_doc": "The JSON shapes for the --context build-context and the session spec, so an AI can "
                "construct them WITHOUT reading source. Unknown fields warn (W_UNKNOWN_FIELD); names are "
                "dex-resolved. tune/fill/replace/select all take --context <this JSON file>.",
        # The consumed/AI-side split is GENERATED from context_audit.FIELD_STATUS (the single source,
        # 勘误④b) — a hand-written copy drifted once. Species fields are dex-canonicalized on load.
        "build_context": "{season, rule, format:single|double, owned:[species], owned_only:bool, "
                         "locked:[species], avoid:[species and/or items — split by dex kind on load], "
                         "avoid_soft:[soft excludes — prefer's mirror, never mechanically filtered], "
                          "prefer:[species], keep_mega:<species-or-Mega-form>, "
                          "mega_posture:environment|none|single|multi (default environment consumes "
                          "the frame's reliable observed registration distribution; explicit other "
                          "values hard-constrain the registered-Mega count), wants, "
                          "exclude_tactics:[str], meta_conformance:proven|off_meta (fill+landscape view order; "
                          "off_meta disables observed-registration deviation gates), "
                         "style_lean:offense|balance|defense (posture lens, AI-side), "
                         "variance_tolerance:averse|tolerant (averse flags diagnose luck_lines), need:<need>, "
                         "benchmarks:[<benchmark>], goals:[{kind:answer|include,species:<Pokemon>}], replace:<replace>, direct_final:bool, "
                         "skip_checkpoint:bool, checkpoint_required:bool (user-requested intermediate choice), frame_required:bool}. " + context_audit.field_status_note()
                         + " Enumerated vocab (roles/tactics/knobs) + field_status: run `vocab`.",
        "need": "fill <team.json> --context {\"need\": {resist:[Type], offense_type:[Type], role:[str], "
                "min_speed:int, coverage_move_type:[Type], answer:name|[name]}} — criteria combine with AND. "
                "answer uses same-variant static CHECK facts against all retained target builds; build-level "
                "addresses carry actual ability/move matches; min_speed remains a theoretical species ceiling.",
        "benchmark": "tune --context {\"benchmarks\": [{member:<species-in-team>, "
                     "kind:survive|outspeed|ohko|2hko, vs:<species> (or a raw Speed int for outspeed), "
                     "move:<name> (required for survive/ohko/2hko), conditions:{weather,terrain,"
                     "stealth_rock:bool,spikes:true|false|0|1|2|3,tailwind:bool,"
                     "opponent_tailwind:bool,trickroom:bool,screens,...}, "
                     "probability:guaranteed|likely|any, "
                     "opponent_set:{ability,item,nature,sps|spread,moves} "
                     "(attacker_set is a survive-only compatibility alias)}]} "
                     "— benchmark-ordered SP frontier cards; no composite score.",
        "replace": "replace --context {\"replace\": {member:<species-in-team>, with:<member-object>}} "
                   "— objective before/after diff of one swap.",
        "draft_spec": "answer-audit <draft.json> = {environment:{season,rule}, context_summary, "
                      "single_team_requested?:bool, alternatives_omitted_reason?:str, "
                      "onboarding_summary?:{status:completed, answered:[onboarding question ids], "
                      "note:str} (REQUIRED for build-flow contexts with frame_required:true; a "
                      "request for one team resolves answer_shape only and does not skip the walk), "
                      "tuning_summary:{status:tuned|proposed|not_run|not_applicable, "
                      "benchmarks_considered?:list, set_adjustments?:list, "
                      "nature_spread_item_notes?:list, reason?:str, notes?:list} (REQUIRED; "
                      "use not_run/not_applicable with reason when no targeted tuning was run), "
                      "request_expressive?:bool (an expressive request escalates the anchor default "
                      "to blocking), assumptions:[str | {gap:<blocking field>, note:str} — the "
                      "structured form is language-independent and preferred for non-English "
                      "drafts], confidence_notes:[str — a low-confidence candidate must be NAMED "
                      "(one of its species, or 'candidate <i>')], "
                      "recommended:[{slate_index:int (into the slated teams), team:<the team-json "
                      "presented — must hash-equal the slated one>, tradeoffs:[str], "
                       "mega_registration_rationale?:{primary,alternative_plan,opportunity_cost} "
                      "(REQUIRED when the slate says this recommended candidate registers multiple "
                      "Mega options; primary may be a canonical string or {member/form/option/...} "
                       "object naming one of them), "
                       "mega_registration_deviation?:{reason,evidence,opportunity_cost} "
                       "(REQUIRED when this survivor's mega_registration_assessment says "
                       "requires_deviation_ack — an observed minority/rare registration lane), "
                      "replacement_rationale?:{out,in,reason,benefit,cost,evidence}|list "
                      "(REQUIRED by the workflow when a previously user-visible/checkpointed "
                      "frame was changed; answer-audit validates completeness when present), "
                      "observed_provenance?: str|object (REQUIRED when the team verbatim-matches a "
                      "stored observed team — the overlap fact + why it fits; language-free), "
                      "adoption_review?:{status:accepted_as_is|modified_candidate_considered|"
                      "no_reasonable_change_found, checked_modification_types:[moves|item|spread|"
                      "nature|member], attempted_changes?:[{kind,reason}], reason, evidence} "
                      "(REQUIRED only when the exact joint set is a stored observed team; the gate "
                      "requires a reasoned review, not a forced change)}] — entries "
                      "must be DISTINCT teams (duplicate content = violation; one distinct team "
                      "requires single_team_requested: true, boolean, or "
                      "alternatives_omitted_reason), "
                      "frame_deviations?:[str | {species, note}] (REQUIRED disclosure when a "
                      "frame-bound slate flagged a YELLOW deviation / off-frame advisory on a "
                      "recommended SURVIVOR — name the species; a filled-check, not a veto), "
                      "convergence_rationale:{<survivor index>:{worst_matchup:str, "
                      "accepted_by_constraint:str|bool, opportunity_cost:str}} (EVERY battery "
                      "survivor, recommended or passed over), claims:[{claim:str, evidence_id:"
                      "'ncp:{fmt}:{attacker}|{defender}|{move}' or 'spd:{fmt}:{member}|{opponent}' "
                      "— species EXACTLY as canonicalized in the slate output; copy damage ids "
                      "verbatim (the audit re-binds their member/modal direction from the saved "
                      "output); the spd opponent side is ALWAYS the modal set, "
                      "expect:object (nonempty subset) {ko_guaranteed:number|null,ko_possible:number|null,min_percent:number,max_percent:number} or "
                      "{faster:member|opponent|tie,member:number,opponent:number}, team?:int (required when the same "
                      "evidence coordinates match multiple recommended teams), direction?:we|them, "
                      "opponent_variant?:str, opponent_is_modal?:bool (damage qualifiers must match saved cells; "
                      "ambiguous coordinates require exact build/direction)}], "
                      "exploration?:{reason:str} (required for over-half/unknown bound core overlap), "
                      "team_claims?:[{claim:str,evidence_ref:<saved candidate by_opponent JSON Pointer>,expect:<complete row>}], "
                      "text_check?:{text:str,lang:zh|ja|en} (optional bounded official-entity, member-name, percentage and script checks), "
                      "slate_receipt:<from the saved slate-evaluate output>}.",
        "session_spec": "session <spec.json> = [{op, ...}] -> [{op, rc, result}]. Team ops "
                        "(parse|validate|suggest-fixes|diagnose|tune|select|matchup|fill|replace|context-audit): "
                        "file:<team> (optional for context-audit), "
                        "context:<context-file>, aspect?, top_k?, with_check? (diagnose/select). "
                        "L4 library/cache reads (no team file): "
                        "parse{file,game_format?}, repset{species, game_format, season?, max_clusters?}, oppmatrix{species (or all:true), "
                        "game_format, season?, vs?, as_checks?}, topmeta{game_format,scope?:season|rule|high,species?}, search{query|species|has_move|has_item|has_ability, "
                        "game_format, season?, limit?, collapse?}, show{team_id, game_format?, season?}, "
                        "landscape{game_format, season?, context?, species?, max_cores?, aspects?, no_cache?:bool, scope?:high (native current-rule record sample, not Top Meta identity units)}, "
                        "observed{game_format, season?, context?, species?, order?, limit?, seed?, seen_teams?}, "
                        "frame{game_format,context,audit_receipt,season?,draw?,seed?,seen_teams?}; "
                        "seen_teams is a file field accepting inline JSON/references. "
                        "intake{game_format?, onboarding?:bool, next?:bool, context?:<ctx.json>, "
                        "answered?:[base-question id...]}, vocab{}, "
                        f"slate-evaluate{{file:<slate.json>, top_k?, view?:full|summary (summary = compact panel projection)}}, matchup top_k range="
                        f"{MATCHUP_TOP_K_MIN}..{MATCHUP_TOP_K_MAX}; matchup may set view=full|summary, "
                        "checkpoint{file:<saved slate-evaluate output>, slate:<slate.json>} "
                        "(build flows should include this op after slate-evaluate; inspect pause), "
                        "answer-audit{file:<draft.json>, slate:<slate.json>, slate_output:<saved output>}, "
                        "draft-init{slate:<slate.json>, slate_output:<saved output>, recommended?:[int]}. "
                        "slate-init{file:<assembly.json>, context, audit_receipt, frame_output?, intake_output?} "
                        "emits a directly usable slate; all these file fields also accept inline JSON/references. "
                        "game_format accepts `format` too; search `query` may be an inline object/list. "
                        "context-audit/slate-evaluate/checkpoint/answer-audit may set fail_on_verdict:true "
                        "(rc=1 for negative verdict, rc=2 for unusable input). File/context/receipt values "
                        "may be inline JSON; {$ref:'/0/result/...'} is a backward-only JSON Pointer into full prior envelopes. "
                        "Missing/failed/forward dependencies refuse the dependent op; independent ops continue. "
                        "Any op may set out:<file> to save full JSON. Session --out saves the full result array; "
                        "diagnose/select/oppmatrix/topmeta/frame/tune/intake/matchup/slate-evaluate accept view:full|summary; "
                        "topmeta/oppmatrix additionally accept summary_limit:N with view:summary. "
                        "--fail-on-verdict returns 1 if any op failed, refused or returned pass:false. Unknown op fields refuse.",
    },
    "note": "member identity accepts `name` or the Showdown `species` (normalized to species). validate's "
            "three-state result is a domain result, NOT the uniform error shape (conventions §3).",
}


def _matchup_top_k(s: str) -> int:
    """argparse adapter for the shared 1..60 matchup-battery scope."""
    try:
        return normalize_top_k(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def _positive_int(s: str) -> int:
    """argparse type for counts that must be >= 1 (e.g. repset --max-clusters)."""
    v = int(s)
    if v < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1 (got {v})")
    return v


def cmd_vocab(fmt: str) -> int:
    """Publish the queryable vocabulary + field-consumption surface (design §17: 公开 role/tactic 词表 +
    各字段是否被消费). The enumerated word-lists the typed build-context knobs draw from, plus
    field_status (which field is mechanically consumed by what). Facts only; no team/context needed."""
    v = context_audit.vocab()
    if fmt == "json":
        emit_json(v)
        return 0
    lines = [f"# {i18n.t('vocab_title')}", "", f"## {i18n.t('vocab_roles')} (need.role)"]
    lines += [f"- `{k}` — {label}" for k, label in v["roles"].items()]
    for key in ("exclude_tactics", "style_lean", "meta_conformance", "variance_tolerance"):
        lines.append(f"\n## {key}")
        lines.append("- " + ", ".join(v[key]))
    lines.append(f"\n## {i18n.t('vocab_field_status')}")
    for k, meta in v["field_status"].items():
        lines.append(f"- `{k}` [{meta['status']}] — {meta['consumer']}")
    lines += ["", *(f"> {n}" for n in v["notes"])]
    print("\n".join(lines))
    return 0


COMMAND_OPTIONS = {
    "review": {"context", "top_k", "focus", "opponents", "conditions", "view", "fail_on_verdict"},
    "try": {"context", "top_k", "focus", "opponents", "conditions", "changes", "view", "fail_on_verdict"},
    "review-claims": {"fail_on_verdict"},
    "type-matchup": {"attack_type", "pokemon", "types", "ability"},
    "rules-facts": set(),
    "threshold": set(),
    "scenario": set(),
    "ko-scan": {"game_format", "top_n", "direction"},
    "counters": {"game_format", "top_n"},
    "speed-ladder": {"game_format", "set", "conditions", "limit", "opponent_ranges"},
    "parse": {"game_format"}, "validate": {"context"}, "suggest-fixes": {"context"},
    "diagnose": {"context", "aspect", "with_check", "top_k", "focus"},
    "tune": {"context", "from_cell"}, "select": {"context", "with_check", "top_k", "opponents", "conditions"},
    "matchup": {"context", "top_k", "as_checks", "matchup_view", "opponents", "conditions"},
    "fill": {"context", "slot"}, "replace": {"context"},
    "context-audit": {"context", "fail_on_verdict"},
    "frame": {"context", "game_format", "season", "audit_receipt", "draw", "seed", "seen_teams"},
    "slate-evaluate": {"top_k", "frame_output", "slate_view", "fail_on_verdict"},
    "slate-init": {"context", "audit_receipt", "frame_output", "intake_output"},
    "checkpoint": {"slate", "fail_on_verdict"},
    "answer-audit": {"slate", "slate_output", "fail_on_verdict"},
    "draft-init": {"slate", "slate_output", "recommended"},
    "landscape": {"context", "game_format", "season", "species", "limit", "aspects", "no_cache", "scope"},
    "observed": {"context", "game_format", "season", "species", "limit", "order", "seed", "seen_teams"},
    "intake": {"context", "game_format", "onboarding", "next_batch", "answered"},
    "repset": {"game_format", "season", "max_clusters"},
    "oppmatrix": {"game_format", "season", "species", "vs", "as_checks", "all"},
    "topmeta": {"game_format", "scope", "species"},
    "search": {"query", "where", "species", "has_move", "has_item", "has_ability",
               "game_format", "season", "limit", "collapse"},
    "show": {"game_format", "season"}, "session": {"spec", "fail_on_verdict"}, "schema": set(), "vocab": set(),
    "code": {"cache_only"}, "code-status": set(), "localize": set(),
}
VIEW_COMMANDS = team_views.COMMANDS | {"matchup", "slate-evaluate", "review", "try"}
for _command in VIEW_COMMANDS:
    COMMAND_OPTIONS[_command].add("view")
for _command in team_views.LIMITED_COMMANDS:
    COMMAND_OPTIONS[_command].add("summary_limit")


def main() -> int:
    p = argparse.ArgumentParser(description=i18n.t('team_cli_description'), allow_abbrev=False)
    p.add_argument("--lang", choices=list(i18n.LANGS),
                   help="output language for md labels/errors (default: env POKEMON_CHAMPIONS_LANG or en)")
    p.add_argument("command",
                 choices=["review", "try", "review-claims", "localize", "parse", "validate", "suggest-fixes", "diagnose", "tune", "type-matchup", "rules-facts", "speed-ladder", "counters", "ko-scan", "scenario", "threshold", "select", "matchup", "fill", "replace",
                            "context-audit", "frame", "slate-init", "slate-evaluate", "checkpoint", "answer-audit", "draft-init", "landscape", "observed",
                            "intake", "repset", "oppmatrix", "topmeta", "search", "show", "session", "schema", "vocab", "code", "code-status"])
    p.add_argument("--cache-only", action="store_true", help="code: use a fresh shared cache result without a network request")
    p.add_argument("--all", action="store_true", help="oppmatrix: explicitly request the entire large matrix")
    p.add_argument("--fail-on-verdict", action="store_true",
                   help="gates: return exit 1 for a negative verdict; unusable input still returns 2")
    p.add_argument("--out", help="save the FULL canonical JSON artifact once; stdout may use a compact view")
    p.add_argument("--view", choices=["full", "summary"], help="JSON display projection; --out always saves full evidence")
    p.add_argument("--summary-limit", type=_positive_int, default=6,
                   help="summary only: table/panel display limit (default 6), without changing calculations or population ranks")
    p.add_argument("--spec", help="session: inline JSON array, or - to read it from stdin")
    p.add_argument("--no-cache", action="store_true", help="landscape: recompute structural facts without reading/writing its local cache")
    p.add_argument("file", nargs="?",
                   help="team-json/Showdown file; for `repset` it is the SPECIES name, for `oppmatrix` "
                   "the (optional) attacker species, for `show` the team id")
    p.add_argument("--format", choices=["md", "json"], default="md")
    p.add_argument("--context", help="build-context JSON file (intent/constraints)")
    p.add_argument("--changes", help="try: JSON list of {slot,set}; each field including spread replaces its prior value")
    p.add_argument("--focus", help="review/try/diagnose: JSON {opponents:[species],members:[species|zero-based slot]}")
    p.add_argument("--slot", type=int, help="fill: zero-based replacement slot; return complete trial configurations and structural differences")
    p.add_argument("--opponents", help="matchup/select/review/try: team-json roster of 1..6 opponents; species-only or complete stated sets")
    p.add_argument("--from-cell", action="store_true", help="tune: input is {report,ref,goal,solve_for,...}; derive threshold lanes and explicit trial changes")
    p.add_argument("--attack-type", help="type-matchup: attacking type in English, Chinese or Japanese")
    p.add_argument("--pokemon", help="type-matchup: defending Pokemon; use instead of --types")
    p.add_argument("--types", nargs="+", help="type-matchup: one or two defending types")
    p.add_argument("--ability", help="type-matchup: stated defending ability")
    p.add_argument("--set", help="speed-ladder: stated configuration as JSON or file; an omitted item does not inherit Choice Scarf")
    p.add_argument("--conditions", help="matchup/select/review/try: field JSON file (weather, terrain, trick_room); speed-ladder: JSON or file with weather, terrain, tailwind side and trickroom")
    p.add_argument("--opponent-ranges", action="store_true", help="speed-ladder: include each opponent form's complete observed speed distribution")
    p.add_argument("--top-n", type=_positive_int, help="counters/ko-scan: usage-rank scope, 1..60 (default 30); not a result count")
    p.add_argument("--direction", choices=ko_scan.DIRECTIONS, default="both", help="ko-scan: attacking, defending or both (default)")
    p.add_argument("--aspect", choices=["defense", "offense", "speed", "roles", "all"], default="all",
                   help="diagnose: which aspect(s) to run")
    p.add_argument("--aspects", nargs="+", choices=list(landscape.ASPECTS), default=None,
                   help="landscape: opt into the full offense/defense presence distributions "
                        "(view opt-in; e.g. --aspects offense defense)")
    p.add_argument("--top-k", type=_matchup_top_k, default=MATCHUP_TOP_K_DEFAULT,
                   help=f"matchup battery scope, freely chosen per consumer "
                        f"({MATCHUP_TOP_K_MIN}..{MATCHUP_TOP_K_MAX}; default {MATCHUP_TOP_K_DEFAULT})")
    p.add_argument("--matchup-view", choices=["full", "summary"], default="full",
                   help="matchup JSON projection: full evidence surface or compact grid summary")
    p.add_argument("--slate-view", dest="slate_view", choices=["full", "summary"], default="full",
                   help="slate-evaluate JSON projection: full evidence surface, or a compact summary "
                        "that drops the per-cell grid + per-opponent evidence lists (for the local UI "
                        "panel / artifact put). Feed the FULL output to answer-audit; summary is display-only")
    p.add_argument("--as-checks", dest="as_checks", action="store_true",
                   help="matchup: render the derived CHECK coverage map (per-opponent best grade + "
                        "holes) instead of the verbose per-cell dump (JSON always carries both). "
                        "oppmatrix: derive the retained observed-build CHECK grid (build x build "
                        "reference view, low/vs-observed-build) instead of the raw damage matrix")
    p.add_argument("--with-check", dest="with_check", action="store_true",
                   help="diagnose/select: opt into ncp-grounded CHECK coverage (runs the matchup battery; "
                        "default off keeps them ncp-free). diagnose: complements the type-layer defense; "
                        "select: adds per-lineup coverage vs meta top-K (per-opponent facts, no ranking)")
    p.add_argument("--game-format", choices=["single", "double"],
                   help="repset/oppmatrix/landscape/observed: which metagame to read (never mixed; landscape and observed REQUIRE it)")
    p.add_argument("--vs", help="oppmatrix: defender species for a single (attacker -> defender) cell")
    p.add_argument("--scope", choices=list(topmeta.SCOPES),
                   help="topmeta: season (the current season, compared with its in-game snapshot), "
                        "rule (every current-rule partition, compared with the current in-game "
                        "snapshot — different time ranges) or high (the rule scope filtered to "
                        "high-performance teams, compared like rule). Default season. "
                        "landscape: high only, native current-rule joint records; omit for ordinary library scope")
    p.add_argument("--slate", help="answer-audit: the ORIGINAL slate.json the candidates were slated with")
    p.add_argument("--slate-output", dest="slate_output",
                   help="answer-audit: the SAVED slate-evaluate output JSON (its slate_receipt is re-bound)")
    p.add_argument("--intake-output", help="slate-init: full intake --next output for the final context; carries resolved ids")
    p.add_argument("--draw", type=int, help="frame: number of weighted, low-overlap frame ids to draw")
    p.add_argument("--seed", type=int, help="reproducible frame draw or observed diversity tie order")
    p.add_argument("--seen-teams", help="frame draw/observed diversity: previously shown team-json, list or teams wrapper")
    p.add_argument("--recommended", nargs="*", type=int,
                   help="draft-init: survivor slate indices to include as recommended entries; default "
                        "is the first up to three survivors in slate order")
    p.add_argument("--audit-receipt", dest="audit_receipt",
                   help="frame: context-audit's output JSON (or a bare audit_receipt object) — the chain "
                        "link recomputed to prove the audit ran on THIS context")
    p.add_argument("--frame-output", dest="frame_output",
                   help="slate-evaluate: the SAVED frame output JSON — activates the build-flow frame "
                        "binding (core-bearer sets checked against their repset clusters)")
    p.add_argument("--season", help="repset/oppmatrix/search/landscape/observed: exact season partition to read. "
                   "Omit it for the current rule pool (same-rule seasons may be merged; old regulations "
                   "excluded). For `search`, `--season all` opts into the cross-season library.")
    p.add_argument("--max-clusters", type=_positive_int, default=3,
                   help="repset: max (item,ability) archetypes to surface (default 3; must be >= 1). "
                        "Values above the cache's retained-build cap expand the long tail for "
                        "debug/exploration only.")
    p.add_argument("--query", help="search: a JSON query (authoritative form). Either a list of member "
                   "slots or {members:[...], game_format, season, limit, collapse}. Each slot is a "
                   'conjunction, e.g. \'[{"species":"耿鬼"},{"item":"剧毒宝珠","move":"灭亡之歌"}]\'.')
    p.add_argument("--where", help="search: a boolean JSON query — AND/OR/NOT over leaf SLOTS. A leaf is a "
                   "slot conjunction {species/move/item/ability/nature}; a node with an and/or/not key is a "
                   'combinator. A leaf means "the team HAS a member matching this slot" (EXISTENCE — not '
                   "distinct members; use --query members when you need N distinct). '-' reads stdin. "
                   'e.g. \'{"and":[{"or":[{"species":"耿鬼"},{"species":"雪妖女"}]},{"ability":"降雨"}]}\'')
    p.add_argument("--species", nargs="*", help="search: species that must ALL be present (one slot each; "
                   "Mega/base-normalized). Convenience shortcut for the pure-composition case. "
                   "observed: OVERLAP-COUNTED constraint members — rows are NOT filtered by them "
                   "(zero-hit rows still return; read overlap.count).")
    p.add_argument("--has-move", nargs="*", help="search: each move must be known by some (distinct) member")
    p.add_argument("--has-item", nargs="*", help="search: each item must be held by some (distinct) member")
    p.add_argument("--has-ability", nargs="*", help="search: each ability must be present on some member")
    p.add_argument("--limit", type=int, help="search/observed: cap the number of teams/groups returned")
    p.add_argument("--order", choices=list(observed.ORDERS),
                   help="observed: ordering key (default: overlap when constraints exist, else tier). "
                        "Evidence tier / recency / explicit overlap only — never a strength ranking")
    p.add_argument("--collapse", action="store_true",
                   help="search: group matches by base-species set (archetype), listing variants per group")
    p.add_argument("--onboarding", action="store_true",
                   help="intake: return the new-build guided walk (the base question set to complete "
                        "once, minus what --context already answers) instead of the full menu — the "
                        "onboarding flow for an open-ended build (per build task), not the <=3-per-round refinement")
    p.add_argument("--next", dest="next_batch", action="store_true",
                   help="intake: return the NEXT batch of 1-3 related walk questions (numbered menu) "
                        "to ask the onboarding walk one step at a time; loop with --answered until done")
    p.add_argument("--answered", nargs="*",
                   help="intake --next: base-question ids already RESOLVED (REQUIRED for the loop to "
                        "terminate). Holds ids you have asked this walk AND dimensions the initial "
                        "request resolved via the draft/answer-shape that --context cannot show "
                        "(e.g. answer_shape when the user said 'just one team'). Unknown ids -> rc=2.")
    ns = p.parse_args()
    i18n.set_lang(getattr(ns, "lang", None))   # must run BEFORE any command produces output
    supplied = {p._option_string_actions[token.split("=", 1)[0]].dest
                for token in sys.argv[1:] if token.split("=", 1)[0] in p._option_string_actions}
    unused = supplied - COMMAND_OPTIONS[ns.command] - {"lang", "format", "out"}
    if unused:
        p.error(f"{ns.command} does not consume: " + ", ".join("--" + k.replace("_", "-") for k in sorted(unused))
                + "; see team.py schema for supported options")
    json_only = {"review", "try", "review-claims", "localize", "type-matchup", "rules-facts", "suggest-fixes", "scenario", "threshold", "topmeta", "observed", "intake", "slate-init", "slate-evaluate", "checkpoint",
                 "answer-audit", "draft-init", "session", "schema", "code", "code-status"}
    if ns.command == "tune" and ns.from_cell:
        json_only.add("tune")
    if ns.command in json_only and (("format" in supplied and ns.format != "json")
                                   or (ns.command == "topmeta" and "lang" in supplied)):
        print(f"{ns.command} emits canonical JSON only; --lang does not translate JSON and --format md "
              "cannot select Markdown. Use --format json.", file=sys.stderr)
    no_file = {"type-matchup", "rules-facts", "topmeta", "frame", "landscape", "observed", "intake", "draft-init", "schema", "vocab", "code-status", "search"}
    if ns.file and ns.command in no_file:
        p.error(f"{ns.command} does not take a positional file/species; see team.py schema")
    if ns.command == "oppmatrix" and ns.species is not None:
        if ns.file or len(ns.species) != 1:
            p.error("oppmatrix accepts one attacker: use oppmatrix Garchomp or --species Garchomp")
        ns.file = ns.species[0]
    if ns.command in {"diagnose", "select"} and "top_k" in supplied and not any((ns.with_check, ns.focus, ns.opponents, ns.conditions)):
        p.error("--top-k needs --with-check for diagnose/select; otherwise no matchup battery runs")
    if ns.command == "intake" and ns.answered is not None and not ns.next_batch:
        p.error("--answered requires intake --next")
    if ns.command == "session" and ns.file and ns.spec is not None:
        p.error("session accepts a spec file OR --spec, not both")
    if ns.out is not None and not ns.out:
        p.error("--out must be a nonempty file path")
    if ns.out and ns.format != "json" and ns.command not in json_only:
        p.error("--out saves a JSON artifact; add --format json")
    if ns.view and ns.format != "json" and ns.command not in json_only:
        p.error("--view selects a JSON projection; add --format json")
    legacy_view = "matchup_view" if ns.command == "matchup" else "slate_view"
    if ns.view and legacy_view in supplied and ns.view != getattr(ns, legacy_view):
        p.error("conflicting --view and legacy view flags; use one projection")
    if "summary_limit" in supplied and ns.view != "summary":
        p.error("--summary-limit requires --view summary")
    if ns.command == "intake" and ns.view == "summary" and not (ns.onboarding or ns.next_batch):
        p.error("intake summary requires --onboarding or --next")
    inputs = [getattr(ns, field) for field in FILE_FIELDS - {"file"} if getattr(ns, field, None)
              and not (field in {"set", "conditions"} and getattr(ns, field).lstrip().startswith("{"))]
    if (ns.file and ns.command not in {"repset", "speed-ladder", "counters", "ko-scan", "oppmatrix", "show", "code"}
            and not (ns.command == "session" and ns.file.lstrip().startswith(("[", "{")))):
        inputs.append(ns.file)
    try:
        protect_inputs(ns.out, inputs)
    except (ValueError, OSError) as exc:
        p.error(str(exc))
    try:
        with output_options(ns.out, command=ns.command, view=ns.view, limit=ns.summary_limit):
            return _dispatch_cli(ns, p)
    except OutputError as exc:
        emit_json({"ok": False, "query": "out", "error": {"code": "bad_input", "message": str(exc)}})
        return 1


def _dispatch_cli(ns, p) -> int:
    if ns.command in {"code", "code-status"}:
        import team_codes
        try:
            result = (team_codes.status() if ns.command == "code-status" else
                      team_codes.lookup(ns.file or "", network=not ns.cache_only))
        except team_codes.TeamCodeError as exc:
            emit_json(exc.result())
            return 0 if exc.code == "not_found" else 1
        emit_json(result)
        return 0
    if ns.command == "schema":
        emit_json(TEAM_SCHEMA)
        return 0
    if ns.command == "vocab":
        return cmd_vocab(ns.format)
    # oppmatrix species / search query are optional; context-audit audits a bare context (team
    # optional); landscape reads the library, no team file at all; vocab/schema take no file.
    if not ns.file and ns.command not in ("type-matchup", "rules-facts", "oppmatrix", "topmeta", "search", "context-audit", "frame", "landscape", "observed", "intake", "vocab", "draft-init", "session"):
        target = {"repset": "species (for command 'repset')",
                  "show": "team id (for command 'show')",
                  "fill": "team file (for command 'fill'; pass a partial or full team-json, plus --context containing need)",
                  "replace": "team file (for command 'replace'; pass the current team-json, plus --context containing replace)",
                  "tune": "team file (for command 'tune'; pass the current team-json, plus --context containing benchmarks)"}.get(
            ns.command, "file")
        p.error(f"the following arguments are required: {target} (for command '{ns.command}')")
    if ns.command == "parse":
        return cmd_parse(ns.file, ns.format, ns.game_format)
    if ns.command == "validate":
        return cmd_validate(ns.file, ns.format, ns.context)
    if ns.command == "suggest-fixes":
        return cmd_suggest_fixes(ns.file, ns.context)
    if ns.command == "diagnose":
        return cmd_diagnose(ns.file, ns.format, ns.aspect, ns.context, with_check=ns.with_check,
                            top_k=ns.top_k, focus_path=ns.focus)
    if ns.command == "tune":
        if ns.from_cell:
            if ns.context:
                p.error("tune --from-cell reads its bound report context; omit --context")
            return cmd_tune_cell(ns.file)
        return cmd_tune(ns.file, ns.format, ns.context)
    if ns.command == "type-matchup":
        return cmd_type_matchup(ns.attack_type, ns.pokemon, ns.types, ns.ability)
    if ns.command == "rules-facts":
        return cmd_rules_facts()
    if ns.command == "speed-ladder":
        return cmd_speed_ladder(ns.file, ns.game_format, ns.set, ns.conditions, ns.limit, ns.opponent_ranges, ns.format)
    if ns.command == "counters":
        return cmd_counters(ns.file, ns.game_format, ns.top_n, ns.format)
    if ns.command == "ko-scan":
        return cmd_ko_scan(ns.file, ns.game_format, ns.top_n, ns.direction, ns.format)
    if ns.command == "scenario":
        return cmd_scenario(ns.file)
    if ns.command == "threshold":
        return cmd_threshold(ns.file)
    if ns.command == "localize":
        return cmd_localize(ns.file)
    if ns.command in ("review", "try"):
        return cmd_review(ns.file, ns.context, top_k=ns.top_k, changes_path=ns.changes,
                          trial=ns.command == "try", view=ns.view or "summary", fail_on_verdict=ns.fail_on_verdict,
                          focus_path=ns.focus, opponents_path=ns.opponents, conditions_path=ns.conditions)
    if ns.command == "review-claims":
        return cmd_review_claims(ns.file, ns.fail_on_verdict)
    if ns.command == "select":
        return cmd_select(ns.file, ns.format, ns.context, with_check=ns.with_check, top_k=ns.top_k,
                          opponents_path=ns.opponents, conditions_path=ns.conditions)
    if ns.command == "matchup":
        return cmd_matchup(ns.file, ns.format, ns.context, ns.top_k, as_checks=ns.as_checks,
                           view=ns.matchup_view, opponents_path=ns.opponents, conditions_path=ns.conditions)
    if ns.command == "fill":
        return cmd_fill(ns.file, ns.format, ns.context, ns.slot)
    if ns.command == "context-audit":
        return cmd_context_audit(ns.file, ns.format, ns.context, ns.fail_on_verdict)
    if ns.command == "frame":
        return cmd_frame(ns.format, ns.game_format, ns.season, ns.context, ns.audit_receipt,
                         ns.draw, ns.seed, ns.seen_teams)
    if ns.command == "slate-evaluate":
        return cmd_slate_evaluate(ns.file, ns.format, ns.top_k, ns.frame_output, view=ns.slate_view,
                                  fail_on_verdict=ns.fail_on_verdict)
    if ns.command == "slate-init":
        return cmd_slate_init(ns.file, ns.context, ns.audit_receipt, ns.frame_output, ns.intake_output)
    if ns.command == "checkpoint":
        return cmd_checkpoint(ns.file, ns.format, ns.slate, ns.fail_on_verdict)
    if ns.command == "answer-audit":
        return cmd_answer_audit(ns.file, ns.format, ns.slate, ns.slate_output, ns.fail_on_verdict)
    if ns.command == "draft-init":
        return cmd_draft_init(ns.format, ns.slate, ns.slate_output, ns.recommended)
    if ns.command == "landscape":
        return cmd_landscape(ns.format, ns.game_format, ns.season, ns.context, ns.species, ns.limit,
                             ns.aspects, no_cache=ns.no_cache, population_scope=ns.scope)
    if ns.command == "observed":
        return cmd_observed(ns.format, ns.game_format, ns.season, ns.context, ns.species, ns.order, ns.limit,
                            ns.seed, ns.seen_teams)
    if ns.command == "intake":
        return cmd_intake(ns.game_format, ns.onboarding, ns.context, ns.next_batch, ns.answered)
    if ns.command == "replace":
        return cmd_replace(ns.file, ns.format, ns.context)
    if ns.command == "repset":
        return cmd_repset(ns.file, ns.game_format, ns.season, ns.max_clusters, ns.format)
    if ns.command == "oppmatrix":
        return cmd_oppmatrix(ns.file, ns.game_format, ns.season, ns.vs, ns.format,
                             as_checks=ns.as_checks, all_species=ns.all)
    if ns.command == "topmeta":
        return cmd_topmeta(ns.game_format, ns.scope, ns.species)
    if ns.command == "search":
        return cmd_search(ns.query, ns.species, ns.has_move, ns.has_item, ns.has_ability,
                          ns.game_format, ns.season, ns.limit, ns.collapse, ns.format, where=ns.where)
    if ns.command == "show":
        return cmd_show(ns.file, ns.game_format, ns.season, ns.format)
    if ns.command == "session":
        return cmd_session(ns.file, ns.spec, ns.fail_on_verdict)
    return 2


if __name__ == "__main__":
    # Every operator makes several sibling calls internally (dex facts, meta rows, ncp batteries),
    # and outside a session each one is a fresh interpreter+data load. Holding the siblings resident
    # for the life of the command is worth ~5x on a real operator (measured: `matchup --top-k 8` on a
    # 4-member doubles team, 18.1s -> 3.6s, byte-identical output). Sessions nest, so the `session`
    # batch op keeps managing its own; and the bridges still fall back to a one-shot subprocess on any
    # worker error, so this is a pure performance path.
    with worker.session():
        raise SystemExit(main())
