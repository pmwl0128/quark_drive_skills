#!/usr/bin/env python
"""context-audit (UEP P2): the front gate — turn "did the AI understand the request?" into
pre-build queryable FACTS.

It audits only the STRUCTURED intent (the build-context the AI translated the conversation into,
plus an optional team-json); it never reads natural language, so a mis-transcription upstream is
out of its reach by design (the answer-audit's disclosure check + the user's reaction cover that).

Output discipline (plan §模糊请求 — surface + verify, never compel):
- facts + THREE-level gap classification only; the audit NEVER decides ask-vs-default for the AI:
    blocking      = no safe default AND a wrong guess wastes the whole build (or erases intent)
    safe_default  = a defensible default exists; applied defaults must be DISCLOSED in the answer.
                    `info_value: high` marks "defaultable, but asking buys a lot" (posture knobs)
    conflict      = the context contradicts itself / the team / the dex (one targeted question)
- `field_status` is THE single source for "which build-context field is mechanically consumed by
  what" (勘误④b): the CLI `schema` NOTE derives from it, and a contract test pins it against
  contracts.CONTEXT_FIELDS so a new field cannot ship unclassified.
- `audit_receipt` starts the capability chain (§A.B): slate-evaluate will require it. Honest
  boundary: a receipt proves this audit RAN on this exact context, not that the AI obeyed it.

Pure/injectable: the dex resolve bridge is passed in, so the classification is unit-testable
offline. No battle data lives here.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from query_identity import corrections

import team_i18n as i18n
from typechart import TYPES
from diagnose import _ROLE_MOVES, _ROLE_LABELS   # the functional-role taxonomy fill matches need.role against
from libsearch import base_key          # Mega/base fold — ONE membership semantics with slate/search/observed

_TYPE_SET = {t.lower() for t in TYPES}

# --------------------------------------------------------------------------- #
# field_status taxonomy — THE single source (schema NOTE derives from this; drift-guarded by test).
# status: enforced_by_skill = an operator mechanically consumes/filters on it;
#         ai_side_only      = loaded + validated + disclosed, but honoring it is the composing AI's
#                             job (no operator filters on it);
#         unused            = accepted by the contract but consumed by nothing (must stay empty —
#                             a field in this state is the "looks supported, actually lost" bug).
# --------------------------------------------------------------------------- #
FIELD_STATUS: dict[str, dict[str, str]] = {
    "season":        {"status": "enforced_by_skill", "consumer": "environment stamp (every operator)"},
    "rule":          {"status": "enforced_by_skill", "consumer": "environment stamp (every operator)"},
    "format":        {"status": "enforced_by_skill", "consumer": "battle format for every operator (metagames never mixed)"},
    "owned":         {"status": "enforced_by_skill", "consumer": "validate owned_only roster check; fill pool"},
    "owned_only":    {"status": "enforced_by_skill", "consumer": "validate + fill hard pool restriction"},
    "locked":        {"status": "enforced_by_skill", "consumer": "fill exclusion; tune nature-lane lock"},
    "avoid":         {"status": "enforced_by_skill", "consumer": "split by dex kind into avoid_species/avoid_items on load"},
    "avoid_species": {"status": "enforced_by_skill", "consumer": "fill candidate filter"},
    "avoid_items":   {"status": "enforced_by_skill", "consumer": "observed excludes teams holding one; slate eliminates candidates holding one; fill candidates are species-level (item n/a — echoes avoid_items_not_enforced)"},
    "keep_mega":     {"status": "enforced_by_skill", "consumer": "select marks the matching mega_option user_keep + echoes matched:false"},
    "mega_posture":  {"status": "enforced_by_skill", "consumer": "slate-evaluate checks candidate registered-Mega count against explicit none/single/multi intent; environment uses the frame's observed registration distribution"},
    "benchmarks":    {"status": "enforced_by_skill", "consumer": "tune SP cliffs (the viability floor — height is the user's)"},
    "goals":         {"status": "enforced_by_skill", "consumer": "slate appends answer targets to its battery and reports exact-form inclusion and observed CHECK floors; descriptive, never an elimination rule"},
    "need":          {"status": "enforced_by_skill", "consumer": "fill gap spec"},
    "replace":       {"status": "enforced_by_skill", "consumer": "replace impact diff"},
    "direct_final":   {"status": "enforced_by_skill", "consumer": "checkpoint suppresses the pause contract when the user explicitly delegated final convergence"},
    "skip_checkpoint": {"status": "enforced_by_skill", "consumer": "checkpoint suppresses the pause contract when the user explicitly requested no mid-build pause"},
    "checkpoint_required": {"status": "enforced_by_skill", "consumer": "checkpoint asks for intermediate candidate, confidence or adoption choices only when the user requests that steering"},
    "frame_required": {"status": "enforced_by_skill", "consumer": "slate-evaluate refuses a build-flow slate without --frame-output; answer-audit backstops missing frame receipts"},
    "meta_conformance": {"status": "enforced_by_skill", "consumer": "landscape observed_cores + fill candidate VIEW order; slate disables observed-registration deviation gates for off_meta — never a score"},
    "variance_tolerance": {"status": "enforced_by_skill", "consumer": "diagnose luck_lines fact (sub-100%-accuracy damaging moves from dex accuracy) — surfaced either way; averse FLAGS them, never a score"},
    "style_lean": {"status": "ai_side_only", "consumer": "the AI reads the axis-2 structural FACTS (profile role_composition / offense lean / landscape norms) through the user's stated posture (offense=主动进攻 / balance=平衡轮换 / defense=稳健防守); no operator filters or orders by it — the skill never labels a team"},
    "wants":         {"status": "ai_side_only", "consumer": "free-form tactic intent; honored when composing"},
    "prefer":        {"status": "ai_side_only", "consumer": "soft species preference (anchor's soft half); dex-canonicalized on load"},
    "avoid_soft":    {"status": "ai_side_only", "consumer": "SOFT exclude (species/items/anything) — prefer's mirror: honored when composing; best-effort canonicalized, NOT name-audited and never a mechanical filter (hard exclusion is `avoid`)"},
    "exclude_tactics": {"status": "ai_side_only", "consumer": "enumerated negative tactics; honored when composing / choosing fill views"},
}

# Fields derived by the loader, not user input (present in FIELD_STATUS for honesty, absent from
# contracts.CONTEXT_FIELDS — the drift test accounts for exactly this set).
DERIVED_FIELDS = frozenset({"avoid_species", "avoid_items"})

# Trigger-token vocabulary — the machine joint between this audit's GAPS and the intake catalog's
# `triggers_on` (the audit says WHAT is missing, the catalog says HOW to ask it). Tokens are
# "{level}:{field-or-kind}" with list indices stripped (benchmarks[0].vs -> benchmarks.vs) and
# '/' folded to '_'. "blocking:anchor_expressive" is the ESCALATED alias of safe_default:anchor —
# the audit cannot read the conversation, so whether a request is expressive stays the AI's call
# (escalates_to: blocking_if_expressive marks it). Single source; intake's tokens are pinned ⊆ this;
# Every askable token has a catalog question (bidirectional closure — conflict:* joins TEMPLATE
# questions whose {placeholders} fill from the gap's own fields; sole exemption:
# safe_default:season_rule, the env-stamp default nobody should be asked about).
TRIGGER_VOCABULARY = frozenset({
    "blocking:format", "blocking:owned", "blocking:benchmarks.vs",
    "blocking:anchor_expressive",
    "safe_default:season_rule", "safe_default:anchor", "safe_default:benchmarks",
    "safe_default:meta_conformance", "safe_default:style_lean",
    "conflict:locked_not_owned", "conflict:prefer_and_avoid", "conflict:locked_and_avoid",
    "conflict:benchmark_member_not_in_team", "conflict:replace_member_not_in_team",
    "conflict:need_unsolvable", "conflict:keep_mega_not_in_pool",
    "conflict:keep_mega_with_none_posture",
    "conflict:ambiguous_name", "conflict:unresolved_name",
})


_INDEX_RE = re.compile(r"\[\d+\]")           # benchmarks[0].vs -> benchmarks.vs


def _trigger_token(level: str, key: str) -> str:
    # NOTE: no backslash inside an f-string expression (PEP 701 is 3.12+; the skill floor is 3.10).
    token = level + ":" + _INDEX_RE.sub("", key).replace("/", "_")
    if token not in TRIGGER_VOCABULARY:
        # Emitter-controlled strings only (field/kind literals) — an off-vocabulary token is a DEV
        # error (new gap emitter without a vocabulary entry), so fail loudly at emit time instead
        # of silently orphaning the gap from every intake question (enforce-in-code, not prose).
        raise ValueError(f"gap trigger {token!r} is not in TRIGGER_VOCABULARY - "
                         "extend the vocabulary (and, if askable, the intake catalog)")
    return token


def field_status_note() -> str:
    """The compact CONSUMED/AI-SIDE sentence the CLI `schema` embeds — generated, never hand-written
    (single source; a hand copy drifted once: keep_mega/avoid were mis-stated; audit 2026-07-02)."""
    enforced = sorted(k for k, v in FIELD_STATUS.items() if v["status"] == "enforced_by_skill")
    ai_side = sorted(k for k, v in FIELD_STATUS.items() if v["status"] == "ai_side_only")
    return (f"CONSUMED (enforced_by_skill): {', '.join(enforced)}. "
            f"AI-SIDE (loaded+validated, honored by the composing AI, not mechanically filtered): "
            f"{', '.join(ai_side)}. Per-field consumers: run `vocab` or `context-audit` (field_status).")


def vocab() -> dict[str, Any]:
    """The queryable vocabulary + consumption surface (design §17: 公开 role/tactic 词表 + 各字段是否被消费).

    Publishes, as ONE facts-only object: the enumerated word-lists the typed build-context knobs draw
    from (so an AI / front-end composes a VALID context without guessing), plus `field_status` — which
    field is mechanically consumed by what. This makes "is this field consumed, and how" a first-class
    fact instead of buried schema prose. `roles` (need.role) and `exclude_tactics` are DIFFERENT
    abstraction layers (move-function taxonomy vs playstyle enum), not one shared vocab."""
    import contracts   # local import: keep module-load order decoupled (contracts is a low-level leaf)
    return {
        "roles": {k: _ROLE_LABELS.get(k, k) for k in sorted(_ROLE_MOVES)},   # need.role functional taxonomy
        "exclude_tactics": sorted(contracts.EXCLUDE_TACTICS),                 # negative-tactic enum (playstyle)
        "style_lean": sorted(contracts.STYLE_LEAN),                           # posture knob (AI-side lens)
        "meta_conformance": sorted(contracts.META_CONFORMANCE),              # view-order knob
        "mega_posture": sorted(contracts.MEGA_POSTURE),                      # registration intent / observed default
        "variance_tolerance": sorted(contracts.VARIANCE_TOLERANCE),          # luck-line flag knob
        "field_status": {k: dict(v) for k, v in FIELD_STATUS.items()},
        "derived_fields": sorted(DERIVED_FIELDS),
        "notes": [
            "roles = the need.role taxonomy (fill matches a candidate's learnset against these keys).",
            "exclude_tactics is a SEPARATE playstyle enum, not the role taxonomy — different abstraction.",
            "field_status: enforced_by_skill = an operator mechanically consumes/filters/orders on it; "
            "ai_side_only = loaded+validated+disclosed but honored by the composing AI (no operator filters). "
            "No field may sit in `unused` — that state is the 'looks supported, silently lost' bug.",
        ],
    }


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #

def _rec(records: dict[str, dict], name: str) -> dict:
    return records.get(name) or {}


def _safe(ctx: Any) -> dict[str, Any]:
    """A type-sane view of the raw context for classification. The audit must never crash on the
    inputs it exists to report (contracts records the type errors; the audit still classifies what
    IS well-typed): non-dict context -> {}, list fields keep only their string items, keep_mega only
    a string, owned_only only a literal bool, benchmarks only its dict entries, replace only a dict."""
    if not isinstance(ctx, dict):
        ctx = {}                        # still build the full typed skeleton below
    out = dict(ctx)
    for key in ("season", "rule", "format"):
        out[key] = ctx.get(key) if isinstance(ctx.get(key), str) else None
    for key in ("owned", "locked", "prefer", "avoid", "avoid_soft", "wants", "exclude_tactics"):
        v = ctx.get(key)
        out[key] = [n for n in v if isinstance(n, str)] if isinstance(v, list) else []
    out["keep_mega"] = ctx.get("keep_mega") if isinstance(ctx.get("keep_mega"), str) else None
    for key in ("meta_conformance", "style_lean", "mega_posture"):
        # posture knobs classify as str-or-absent: a garbage-typed value must not suppress the
        # gray-zone safe_default nor launder into constraints_not_enforced as a "stated" posture
        # (self-audit 2026-07-03); contracts reports the type/enum error in the same output.
        out[key] = ctx.get(key) if isinstance(ctx.get(key), str) else None
    out["owned_only"] = ctx.get("owned_only") is True
    out["need"] = ctx.get("need") if isinstance(ctx.get("need"), dict) else {}
    v = ctx.get("benchmarks")
    out["benchmarks"] = [b for b in v if isinstance(b, dict)] if isinstance(v, list) else []
    out["replace"] = ctx.get("replace") if isinstance(ctx.get("replace"), dict) else {}
    return out


def _collect_names(ctx: dict[str, Any], avoid_species_side: list[str]) -> list[str]:
    """Every species-referencing name in the (sanitized) context, for ONE batched resolve. `avoid`
    contributes only its species side — its item side is classified separately and must not be
    misreported as an unresolved species (that would re-introduce the item-masquerades-as-species
    bug this changeset kills in the loader/fill; audit 2026-07-02)."""
    names: list[str] = list(ctx["owned"]) + list(ctx["locked"]) + list(ctx["prefer"])
    names += avoid_species_side
    if ctx["keep_mega"]:
        names.append(ctx["keep_mega"])
    for b in ctx["benchmarks"]:
        if isinstance(b.get("member"), str):
            names.append(b["member"])
        if isinstance(b.get("vs"), str) and not (b.get("kind") == "outspeed"
                                                and b["vs"].strip().lstrip("+").isdigit()):
            names.append(b["vs"])
    if isinstance(ctx["replace"].get("member"), str):
        names.append(ctx["replace"]["member"])
    return names


def _canon(records: dict[str, dict], name: Any) -> Any:
    """Canonical form of a context name when the resolve succeeded, else the verbatim value."""
    if not isinstance(name, str):
        return name
    r = _rec(records, name)
    return r.get("canonical") if r.get("ok") and r.get("canonical") else name


def context_fingerprint(raw_ctx: Any, env: dict[str, Any] | None) -> str:
    """The audit_receipt fingerprint: the INTENT CONTENT (raw context, as written) + env — NOT the
    optional team passed at audit time (it informs conflict checks but must not fork the chain:
    slate-evaluate recomputes team-less, so a team-inclusive audit would otherwise never validate;
    self-audit 2026-07-02). Extracted so chain consumers (P5 gate, P6 answer-audit) recompute the
    SAME construction instead of forking their own — content-only, so no dex round-trip is needed."""
    return hashlib.sha256(json.dumps(
        {"ctx": raw_ctx, "env": {k: (env or {}).get(k) for k in ("season", "rule")}},
        sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:24]


def audit(ctx: dict[str, Any], team: dict[str, Any] | None, *,
          resolve_fn: Callable[[list[str]], dict[str, dict]],
          item_fn: Callable[[list[str]], dict[str, dict]] | None = None,
          normalize_fn: Callable | None = None,
          battle_team_fn: Callable | None = None,
          contract_errors: list[dict] | None = None,
          env: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify the structured intent into facts + three-level gaps. Pure given resolve_fn/item_fn.

    `resolve_fn(names) -> {query: resolve-record}` is the dex resolve bridge (pokemon kind);
    `item_fn(names) -> {query: item-record}` classifies `avoid` entries by dex kind (mirrors the
    loader's split — an avoid-ITEM is a legitimate constraint, never an unresolved species);
    `team` is the parsed team-json dict (or None); `contract_errors` are the executable-contract
    findings on the RAW inputs (embedded as facts — the audit reports, it does not refuse; badly
    TYPED fields are likewise reported by contracts and skipped by classification, never crashed on);
    `env` is the resolved environment stamp."""
    contract_errors = contract_errors or []
    raw_ctx = ctx                         # the receipt fingerprints the INPUT as written, pre-sanitize
    nested_flags, nested_errors = [], []
    if normalize_fn:
        ctx, nested_flags, nested_errors = normalize_fn(ctx if isinstance(ctx, dict) else {})
    ctx = _safe(ctx)
    if team and battle_team_fn:
        team = battle_team_fn(team)
    team_members = [m.get("species") for m in ((team or {}).get("pokemon") or [])
                    if isinstance(m, dict) and m.get("species")]
    # avoid: item side first (same split as the loader; item hits are facts, not species misses).
    item_facts = item_fn(list(ctx["avoid"])) if (item_fn and ctx["avoid"]) else {}
    avoid_items = [f["name"] for n in ctx["avoid"]
                   if (f := item_facts.get(n) or {}).get("found") and f.get("name")]
    avoid_rest = [n for n in ctx["avoid"]
                  if not ((item_facts.get(n) or {}).get("found") and (item_facts.get(n) or {}).get("name"))]
    names = _collect_names(ctx, avoid_rest)
    unique_names = sorted(set(names))
    records = resolve_fn(unique_names) if unique_names else {}

    # --- name resolution facts (P1 canonicalization made these silent-safe; the audit SURFACES them:
    # ambiguity/typo = a targeted single question, per 模糊请求 §conflict) -----------------------------
    unresolved: list[dict[str, Any]] = [
        {**e, "name_kind": e.get("kind"),
         "kind": "ambiguous_name" if e.get("match_type") == "ambiguous" else "unresolved_name"}
        for e in nested_errors]
    for n in unique_names:
        r = _rec(records, n)
        if not r or (r.get("ok") and r.get("canonical")) or any(e["query"] == n for e in unresolved):
            continue
        unresolved.append({
            "query": n,
            "match_type": r.get("match_type"),
            "suggestions": r.get("suggestions") or [],
            "kind": "ambiguous_name" if r.get("match_type") == "ambiguous" else "unresolved_name",
        })

    owned = [_canon(records, n) for n in ctx["owned"]]
    locked = [_canon(records, n) for n in ctx["locked"]]
    prefer = [_canon(records, n) for n in ctx["prefer"]]
    avoid = [_canon(records, n) for n in avoid_rest]           # the species side (items are facts above)

    # --- blocking: no safe default AND a wrong guess wastes the build --------------------------------
    missing_required: list[dict[str, Any]] = []
    fmt = ctx.get("format") or (team or {}).get("format")
    if not fmt:
        missing_required.append({
            "field": "format",
            "reason": "single vs double is not declared by the context or the team and cannot be "
                      "defaulted — the metagames are never mixed, and every downstream fact depends on it."})
    if ctx.get("owned_only") and not (ctx.get("owned") or []):
        missing_required.append({
            "field": "owned",
            "reason": "owned_only restricts the pool to the owned roster, but no roster was given — "
                      "every fill/build decision would be a guess."})
    for i, b in enumerate(ctx["benchmarks"]):
        vs = b.get("vs")
        if isinstance(vs, str):
            r = _rec(records, vs)
            if not r:
                # An EMPTY record means dex was unavailable (not that the name is bogus) — mirror the
                # name-resolution loop above, which skips empty records. Reporting "does not resolve in
                # the dex" here would be dishonest degradation: dex-down != name-unresolvable (audit
                # 2026-07-06). When dex is up, a genuine miss still carries ok=False and IS reported.
                continue
            if not (r.get("ok") and r.get("canonical")):
                missing_required.append({
                    "field": f"benchmarks[{i}].vs",
                    "reason": f"benchmark target {vs!r} does not resolve in the dex — the SP cliff "
                              "would be tuned against nothing."})

    # --- conflicts: the context contradicts itself / the team ---------------------------------------
    conflicts: list[dict[str, Any]] = []
    if ctx["owned_only"] and owned:
        # Ownership is base-species-aware, matching validate's owned_only rule: a locked Mega form is
        # owned when its BASE is owned — the two gates must never hand out opposite verdicts on the
        # same context (audit 2026-07-02).
        owned_set = set(owned)
        def _owned(name: str) -> bool:
            base = _rec(records, name).get("base_species")
            return name in owned_set or _canon(records, name) in owned_set or (base in owned_set if base else False)
        missing = sorted({_canon(records, n) for n in ctx["locked"] if not _owned(n)})
        if missing:
            conflicts.append({"kind": "locked_not_owned", "members": missing,
                              "detail": "owned_only is set but these locked members are not in the owned roster."})
    overlap = sorted(set(prefer) & set(avoid))
    if overlap:
        conflicts.append({"kind": "prefer_and_avoid", "members": overlap,
                          "detail": "the same species is both preferred and avoided."})
    lock_avoid = sorted(set(locked) & set(avoid))
    if lock_avoid:
        conflicts.append({"kind": "locked_and_avoid", "members": lock_avoid,
                          "detail": "the same species is both locked and avoided."})
    if team_members:
        for i, b in enumerate(ctx["benchmarks"]):
            member = b.get("member")
            if isinstance(member, str) and _canon(records, member) not in team_members:
                conflicts.append({"kind": "benchmark_member_not_in_team", "path": f"benchmarks[{i}]",
                                  "member": member,
                                  "detail": "the benchmark tunes a member that is not on the team."})
        rep_member = ctx["replace"].get("member")
        if isinstance(rep_member, str) and _canon(records, rep_member) not in team_members:
            conflicts.append({"kind": "replace_member_not_in_team", "member": rep_member,
                              "detail": "replace removes a member that is not on the team."})
    # need solvability — VOCABULARY level (mechanical, no data needed): an off-vocabulary type/role
    # token would make fill silently match nothing (fill looks tokens up and gets empty sets), so it
    # is a conflict to surface now, not an empty result to puzzle over later (external audit
    # 2026-07-02). Numeric reachability (min_speed vs what actually exists) needs dex/meta data and
    # deliberately stays with fill's empty-result disclosure.
    def _tokens(v: Any) -> list[str]:
        if isinstance(v, str):
            return [v]
        return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []
    for key in ("resist", "offense_type", "coverage_move_type"):
        bad_types = [t for t in _tokens(ctx["need"].get(key)) if t.lower() not in _TYPE_SET]
        if bad_types:
            conflicts.append({"kind": "need_unsolvable", "path": f"need.{key}", "tokens": bad_types,
                              "detail": "unknown type token(s) — fill would silently match nothing; "
                                        "use the canonical English type names."})
    bad_roles = [t for t in _tokens(ctx["need"].get("role")) if t not in _ROLE_MOVES]
    if bad_roles:
        conflicts.append({"kind": "need_unsolvable", "path": "need.role", "tokens": bad_roles,
                          "detail": "unknown role token(s) — fill would silently match nothing; "
                                    f"allowed: {', '.join(sorted(_ROLE_MOVES))}."})

    keep = ctx["keep_mega"]
    if keep and ctx.get("mega_posture") == "none":
        conflicts.append({
            "kind": "keep_mega_with_none_posture",
            "member": keep,
            "detail": "keep_mega requests a Mega option while mega_posture='none' requires no "
                      "registered Mega options."})
    if keep:
        r = _rec(records, keep)
        keep_canon = _canon(records, keep)
        base = r.get("base_species") if r.get("ok") else None
        pool = team_members or (owned if ctx["owned_only"] else
                                sorted(set(owned) | set(locked) | set(prefer)))
        # BASE-folded membership, BOTH directions: keep=base vs a pool Mega form must match just
        # like keep=Mega vs a pool base (2026-07-03 system test: keep_mega 耿鬼 against locked
        # 超级耿鬼 falsely reported keep_mega_not_in_pool).
        pool_bases = {base_key(n) for n in pool}
        keep_keys = {base_key(keep_canon)} | ({base_key(base)} if base else set())
        if pool and not (keep_keys & pool_bases):
            # POOL-MEMBERSHIP check only — whether the member can actually Mega (stone/form) is
            # verified mechanically by `select` (user_keep / matched:false); the audit does not
            # duplicate that deeper rule, it only catches a keep that names nothing at all.
            conflicts.append({"kind": "keep_mega_not_in_pool", "member": keep,
                              "detail": "keep_mega names no team member (nor a base species of one) "
                                        "in the declared team/pool (pool membership only — actual "
                                        "Mega capability is select's verdict)."})

    # --- safe defaults (+ the posture gray zone: defaultable but info-rich) --------------------------
    safe_defaults: list[dict[str, Any]] = []
    if not ctx.get("season") or not ctx.get("rule"):
        cur = env or {}
        safe_defaults.append({"field": "season/rule",
                              "default": f"current base ({cur.get('season')}/{cur.get('rule')})",
                              "source": "environment", "info_value": "low"})
    anchor_via = [k for k in ("locked", "prefer") if ctx[k]]
    if not anchor_via:
        safe_defaults.append({
            "field": "anchor", "default": "none (build from the environment only)",
            "source": "absence of locked/prefer", "info_value": "high",
            # Machine-readable escalation (P6 consumes it): whether the request IS expressive is the
            # AI's read of the conversation — not derivable here — but when it is, this default is
            # not safe (it erases the intent) and must be treated as blocking.
            "escalates_to": "blocking_if_expressive",
            "note": "for an EXPRESSIVE request a missing anchor is BLOCKING (defaulting it erases "
                    "the intent) — whether the request is expressive is the AI's read of the "
                    "conversation, not derivable from structured intent."})
    if not ctx["benchmarks"]:
        safe_defaults.append({
            "field": "benchmarks", "default": "no explicit viability floor (validate/diagnose only)",
            "source": "absence of benchmarks", "info_value": "high",
            "note": "the floor's height is the user's call — asking buys a lot when the posture is unknown."})
    if not ctx.get("style_lean"):
        safe_defaults.append({
            "field": "style_lean", "default": "none (read the profile facts without a posture lens)",
            "source": "absence of style_lean", "info_value": "high",
            "note": "the structural-posture knob (offense=主动进攻 / balance=平衡轮换 / "
                    "defense=稳健防守) — cheap to default, but for a build request the answer "
                    "changes a lot with it; one plain-language question buys a lot."})
    if not ctx.get("meta_conformance"):
        safe_defaults.append({
            "field": "meta_conformance", "default": "proven (common-first landscape views)",
            "source": "absence of meta_conformance", "info_value": "high",
            "note": "the proven↔off-meta posture knob — cheap to default, but for a user who wants "
                    "something deliberately off the beaten path the default hides the rare-first view."})

    # --- assemble -------------------------------------------------------------------------------------
    present = [k for k in FIELD_STATUS if ctx.get(k)]
    not_enforced = [k for k in present if FIELD_STATUS[k]["status"] == "ai_side_only"]
    # Each gap carries its trigger TOKEN — join it against the intake catalog's `triggers_on` to
    # pick the wording/options for whatever you decide to ask (the deciding stays yours).
    gaps = ([{"level": "blocking", "trigger": _trigger_token("blocking", m["field"]), **m}
             for m in missing_required]
            + [{"level": "conflict", "trigger": _trigger_token("conflict", c["kind"]), **c}
               for c in conflicts]
            + [{"level": "conflict", "trigger": _trigger_token("conflict", u["kind"]), **u}
               for u in unresolved]
            + [{"level": "safe_default", "trigger": _trigger_token("safe_default", s["field"]), **s}
               for s in safe_defaults])
    fingerprint = context_fingerprint(raw_ctx, env)
    return {
        "kind": "context-audit",
        "field_status": FIELD_STATUS,
        "constraints_not_enforced": not_enforced,
        "anchor": {"present": bool(anchor_via), "via": anchor_via},
        "avoid_items_recognized": avoid_items,     # legit item constraints (mirrors the loader split)
        "contract_errors": contract_errors,
        "missing_required": missing_required,
        "conflicts": conflicts,
        "unresolved_names": unresolved,
        "name_resolution": corrections({("pokemon", name): record for name, record in records.items()}) + nested_flags,
        "safe_defaults": safe_defaults,
        "gaps": gaps,
        "audit_receipt": {
            "kind": "audit_receipt",
            "fingerprint": fingerprint,               # content-only (stable across re-runs)
            "audited_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "blocking": len(missing_required), "conflicts": len(conflicts) + len(unresolved),
        },
        "notes": [
            "each gap carries a `trigger` token — run `team.py intake [--game-format ...]` and join "
        "it against the catalog's triggers_on for ready-made wording/options/field mappings. "
        "STEADY-STATE refinement (a build-context already exists): pick <=3 questions PER ROUND for "
        "the gaps you decide to ask, THEN re-audit and iterate — <=3 is a per-round batch, NOT a cap "
        "on total questions. A NEW open-ended build (its intent not yet elicited — per build task, "
        "not per session) instead completes the base set ONCE: `team.py intake --onboarding "
        "[--context ctx.json]` returns the guided_walk to ask in "
        "full (design §19.6, two-mode intake). conflict:* triggers join TEMPLATE questions — fill "
        "their {placeholders} from this gap's own fields (members/member/query/suggestions/tokens/path).",
        "facts + gap levels only — the audit never decides ask-vs-default (surface + verify, "
            "not compel): blocking -> ask targeted; safe_default -> apply + DISCLOSE the assumption "
            "(info_value=high marks 'asking buys a lot'); conflict -> one precise question.",
            "this audits the STRUCTURED intent only; whether it faithfully transcribes the "
            "conversation is not checkable here (disclosure + the user's reaction cover that).",
            "the receipt proves this audit ran on exactly this context (content fingerprint) — it "
            "does not prove the gaps were acted on.",
        ],
    }


def format_audit_md(d: dict[str, Any]) -> str:
    lines = [i18n.t('ctx_audit_header', n_gaps=len(d.get("gaps") or [])), ""]

    def section(label: str, rows: list[str]) -> None:
        lines.append(f"## {label}")
        lines.extend([f"- {r}" for r in rows] or [f"- {i18n.t('ctx_audit_clean')}"])
        lines.append("")

    section(i18n.t('ctx_audit_contract_errors'),
            [f"[{e.get('severity', 'error')}] {e.get('code')} `{e.get('path')}` — {e.get('message')}"
             for e in d.get("contract_errors") or []])
    section(i18n.t('ctx_audit_blocking'),
            [f"`{m['field']}` — {m['reason']}" for m in d.get("missing_required") or []])
    def _sugg(s: Any) -> str:
        # dex resolve suggestions are records ({canonical, display_name, ...}); tolerate bare strings.
        return s if isinstance(s, str) else (s.get("canonical") or s.get("display_name")
                                             or s.get("name") or str(s))

    section(i18n.t('ctx_audit_conflicts'),
            [f"{c['kind']}: {c.get('members') or c.get('member') or c.get('path')} — {c['detail']}"
             for c in d.get("conflicts") or []]
            + [f"{u['kind']}: `{u['query']}`"
               + (f" → {', '.join(_sugg(s) for s in u['suggestions'])}" if u.get("suggestions") else "")
               for u in d.get("unresolved_names") or []])
    section(i18n.t('ctx_audit_defaults'),
            [f"`{s['field']}` → {s['default']} (info_value={s['info_value']})"
             + (f" — {s['note']}" if s.get("note") else "") for s in d.get("safe_defaults") or []])
    section(i18n.t('ctx_audit_not_enforced'),
            [f"`{k}` — {FIELD_STATUS[k]['consumer']}" for k in d.get("constraints_not_enforced") or []])
    r = d.get("audit_receipt") or {}
    # The hint is load-bearing: this line shows ONLY the fingerprint, and an orchestrator reading md
    # pasted exactly that string as the slate's audit_receipt (external session 2026-07-07).
    lines.append(f"_{i18n.t('ctx_audit_receipt')}: `{r.get('fingerprint')}` @ {r.get('audited_at')}"
                 f" — {i18n.t('ctx_audit_receipt_hint')}_")
    return "\n".join(lines)
