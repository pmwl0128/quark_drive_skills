#!/usr/bin/env python
"""answer-audit (UEP P6): the back gate — the receipt chain's LAST link, run on a structured draft
of the answer BEFORE it reaches the user. The audit never reads prose; it reads the draft skeleton
the AI fills (context_summary / recommended+tradeoffs / convergence_rationale / assumptions /
confidence_notes / claims / slate_receipt) plus the ORIGINAL slate input and the SAVED slate output.

Two layers (both here — form first, substance right behind):
- FORM (structural checklist): environment stamped; every recommended candidate re-validates, maps
  by content hash to a slated team and was a battery SURVIVOR; >=1 trade-off each; a single-team
  answer is declared; the convergence scaffold (worst_matchup / accepted_by_constraint /
  opportunity_cost) is filled for EVERY survivor — recommended or passed over; blocking gaps that
  existed at slating are disclosed as assumptions; multi-Mega recommendations and declared
  replacements carry their rationales; low-confidence slate facts are disclosed; no banned
  absolute-strength wording (bounded wordlist — a tripwire, not NLP).
- SUBSTANCE (claim <-> recompute): every quantitative claim carries an evidence_id under the grammar
  OWNED by slate.py (imported — never re-defined here, 勘误④a), and its structured `expect` numbers
  are recomputed through the SAME set construction the battery used (matchup.member_actor/set_actor/
  pair_speed/dmg_fact). A fabricated number is a violation; a sibling outage is a disclosed note
  (honest degrade), never a silent pass of the number.

Receipt chain (refusals, not violations): the draft's slate_receipt must equal the saved output's;
the output must reproduce its own fingerprint from the slate input (edits after the run refuse);
the output's audit fingerprint must match a context-audit recompute of the slate's context.

Honest boundary (pinned by tests + notes): PASS = the form is complete and the quantitative claims
reproduce. It does NOT mean the trade-offs are right — that judgment was never auditable here.
"""
from __future__ import annotations

from typing import Any, Callable

import intake
import slate as slate_mod
import exploration

# Bounded absolute-strength wordlist (plan P6: 有界词表) — a TRIPWIRE over the draft's prose fields,
# not language understanding. English terms are matched lowercase; CJK terms match as-is (str.lower
# is a no-op on kana/kanji). The tripwire must cover EVERY language the skill can answer in — a
# ja-only draft claiming 最強/無敵 would otherwise sail past the zh+en list, since ja 最強 (強) is a
# different string from zh 最强 (强) (audit 2026-07-06).
BANNED_CLAIM_TERMS = (
    "最强", "最优解", "评分最高", "绝对无解", "客观最优", "必胜",            # zh (simplified)
    "最強", "最適解", "無敵", "必勝", "客観的に最適", "勝ち確定", "最高評価",   # ja
    "strongest team", "objectively best", "objectively optimal", "unbeatable",
    "highest-rated", "guaranteed win",                                    # en
)

# The structured numbers a claim's `expect` may pin, per evidence kind — exactly the keys the
# recompute produces (matchup.dmg_fact / matchup.pair_speed), never free-form.
DAMAGE_EXPECT_KEYS = frozenset({"ko_guaranteed", "ko_possible", "min_percent", "max_percent"})
SPEED_EXPECT_KEYS = frozenset({"faster", "member", "opponent"})
_PCT_TOLERANCE = 0.1                      # damage percents may be transcribed rounded to one decimal
_SCAFFOLD_FIELDS = ("worst_matchup", "accepted_by_constraint", "opportunity_cost")
_TUNING_STATUSES = frozenset({"tuned", "proposed", "not_run", "not_applicable"})
_ADOPTION_STATUSES = frozenset({
    "accepted_as_is",
    "modified_candidate_considered",
    "no_reasonable_change_found",
})
_ADOPTION_MOD_TYPES = frozenset({"moves", "item", "spread", "nature", "member"})


def _refuse(code: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"kind": "answer-audit", "refused": {"code": code, "reason": reason, **extra}}


def _filled(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _nonempty(v: Any) -> bool:
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, list):
        return any(_nonempty(x) for x in v)
    if isinstance(v, dict):
        return any(_nonempty(x) for x in v.values())
    return v is not None and not isinstance(v, bool)


def _prose_strings(draft: dict[str, Any]) -> list[tuple[str, str]]:
    """(path, text) for every prose field the banned-wordlist tripwire scans. Team jsons are NOT
    scanned (move/item names are game vocabulary, not claims)."""
    out: list[tuple[str, str]] = []

    def add(path: str, v: Any) -> None:
        if isinstance(v, str) and v:
            out.append((path, v))
        elif isinstance(v, list):
            for j, x in enumerate(v):
                add(f"{path}[{j}]", x)
        elif isinstance(v, dict):
            for k, x in v.items():
                add(f"{path}.{k}", x)

    for key in ("context_summary", "assumptions", "confidence_notes",
                "alternatives_omitted_reason", "convergence_rationale", "tuning_summary",
                "onboarding_summary"):
        add(key, draft.get(key))
    for i, rec in enumerate(draft.get("recommended") or []):
        if isinstance(rec, dict):
            add(f"recommended[{i}].tradeoffs", rec.get("tradeoffs"))
            add(f"recommended[{i}].mega_registration_rationale",
                rec.get("mega_registration_rationale"))
            add(f"recommended[{i}].replacement_rationale",
                rec.get("replacement_rationale"))
            add(f"recommended[{i}].adoption_review", rec.get("adoption_review"))
    for i, c in enumerate(draft.get("claims") or []):
        if isinstance(c, dict):
            add(f"claims[{i}].claim", c.get("claim"))
    return out


def _tuning_summary_violations(draft: dict[str, Any]) -> list[dict[str, Any]]:
    ts = draft.get("tuning_summary")
    if not isinstance(ts, dict):
        return [{"code": "missing_tuning_summary", "where": "tuning_summary",
                 "detail": "draft must disclose set tuning: status, benchmark facts considered, "
                           "and nature/SP/item decisions; use status='not_run' or "
                           "'not_applicable' with a reason when no tuning was run"}]
    status = ts.get("status")
    if status not in _TUNING_STATUSES:
        return [{"code": "bad_tuning_summary", "where": "tuning_summary.status",
                 "detail": "status must be one of " + ", ".join(sorted(_TUNING_STATUSES))}]
    has_bench = _nonempty(ts.get("benchmarks_considered")) or _nonempty(ts.get("benchmarks"))
    has_sets = (_nonempty(ts.get("set_adjustments")) or _nonempty(ts.get("set_choices"))
                or _nonempty(ts.get("nature_spread_item_notes")))
    has_reason = _nonempty(ts.get("reason")) or _nonempty(ts.get("notes"))
    if status in ("tuned", "proposed") and not (has_bench and has_sets):
        return [{"code": "incomplete_tuning_summary", "where": "tuning_summary",
                 "detail": "status tuned/proposed requires non-empty benchmark facts and concrete "
                           "nature/SP/item/set decisions"}]
    if status == "not_run" and not has_reason:
        return [{"code": "incomplete_tuning_summary", "where": "tuning_summary",
                 "detail": "status not_run requires a reason or note, so absent tuning is visible"}]
    if status == "not_applicable" and not has_reason:
        return [{"code": "incomplete_tuning_summary", "where": "tuning_summary",
                 "detail": "status not_applicable requires a reason"}]
    return []


def _onboarding_summary_violations(draft: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Build flows must prove the initial guided walk was resolved. This is a form gate over the
    structured draft: context coverage + declared answered ids must cover the onboarding base set.
    It does not decide the user's answers; it prevents silently jumping from a vague build request to
    a final slate without accounting for the front-door questions."""
    if ctx.get("frame_required") is not True:
        return []
    summary = draft.get("onboarding_summary")
    if not isinstance(summary, dict):
        return [{"code": "missing_onboarding_summary", "where": "onboarding_summary",
                 "detail": "build-flow drafts must declare how the initial intake walk was resolved: "
                           "{status:'completed', answered:[onboarding ids], note}. A request for one "
                           "team only resolves answer_shape; it does not skip the walk."}]
    status = summary.get("status")
    if status != "completed":
        return [{"code": "bad_onboarding_summary", "where": "onboarding_summary.status",
                 "detail": "build-flow onboarding_summary.status must be 'completed' once the "
                           "guided walk has been asked or pruned by supplied context"}]
    answered = summary.get("answered")
    if not isinstance(answered, list):
        return [{"code": "bad_onboarding_summary", "where": "onboarding_summary.answered",
                 "detail": "answered must list resolved onboarding question ids (asked ids plus "
                           "pre-resolved draft-only ids such as answer_shape)"}]
    answered_ids = {x for x in answered if isinstance(x, str)}
    unknown = sorted(answered_ids - set(intake.ONBOARDING_IDS))
    if unknown:
        return [{"code": "bad_onboarding_summary", "where": "onboarding_summary.answered",
                 "detail": "unknown onboarding ids: " + ", ".join(unknown)}]
    covered = set(intake.covered_dimensions(ctx))
    unresolved = [qid for qid in intake.ONBOARDING_IDS if qid not in covered and qid not in answered_ids]
    if unresolved:
        return [{"code": "incomplete_onboarding_summary", "where": "onboarding_summary",
                 "detail": "these onboarding dimensions were neither covered by context nor listed "
                           "as answered: " + ", ".join(unresolved)}]
    if not _nonempty(summary.get("note")) and not _nonempty(summary.get("notes")):
        return [{"code": "incomplete_onboarding_summary", "where": "onboarding_summary",
                 "detail": "status completed requires a note summarizing whether the walk was asked "
                           "or pruned by user-supplied context"}]
    return []


def _adoption_review_violations(rec: dict[str, Any], overlap: dict[str, Any],
                                where: str) -> list[dict[str, Any]]:
    """Exact stored-team adoption is allowed, but a verbatim answer needs an explicit review of
    whether a reasonable fork was attempted. This is only for exact joint-set copies; changed moves,
    item, spread, nature, or members no longer produce verbatim_ids and do not trigger this gate."""
    if not overlap.get("verbatim_ids"):
        return []
    review = rec.get("adoption_review")
    if not isinstance(review, dict):
        return [{"code": "library_adoption_review_missing", "where": where,
                 "detail": "this recommended team exactly matches a stored observed team; add "
                           "adoption_review showing what kind of changes were considered. The gate "
                           "does not require a change, only a reasoned accept-or-fork review."}]
    violations: list[dict[str, Any]] = []
    status = review.get("status")
    other = review.get("other_structures")
    if not (isinstance(other, dict) and type(other.get("considered")) is bool
            and _filled(other.get("reason"))):
        violations.append({"code": "adoption_exploration_missing", "where": f"{where}.adoption_review.other_structures",
                           "detail": "Disclose whether other structures were considered, with considered:bool and reason:str. "
                                     "Keeping the observed team is allowed."})
    if status not in _ADOPTION_STATUSES:
        violations.append({"code": "bad_adoption_review", "where": f"{where}.adoption_review.status",
                           "detail": "status must be one of "
                                     + ", ".join(sorted(_ADOPTION_STATUSES))})
    checked = review.get("checked_modification_types")
    if not isinstance(checked, list) or not checked:
        violations.append({"code": "incomplete_adoption_review",
                           "where": f"{where}.adoption_review.checked_modification_types",
                           "detail": "list at least one considered modification type: "
                                     + ", ".join(sorted(_ADOPTION_MOD_TYPES))})
    else:
        bad = sorted(str(x) for x in checked if x not in _ADOPTION_MOD_TYPES)
        if bad:
            violations.append({"code": "bad_adoption_review",
                               "where": f"{where}.adoption_review.checked_modification_types",
                               "detail": "unknown modification type(s): " + ", ".join(map(str, bad))})
    for field in ("reason", "evidence"):
        if not _nonempty(review.get(field)):
            violations.append({"code": "incomplete_adoption_review",
                               "where": f"{where}.adoption_review.{field}",
                               "detail": f"adoption_review requires non-empty {field}"})
    attempts = review.get("attempted_changes")
    if attempts is not None:
        if not isinstance(attempts, list):
            violations.append({"code": "bad_adoption_review",
                               "where": f"{where}.adoption_review.attempted_changes",
                               "detail": "attempted_changes must be a list when present"})
        else:
            for j, attempt in enumerate(attempts):
                awhere = f"{where}.adoption_review.attempted_changes[{j}]"
                if not isinstance(attempt, dict):
                    violations.append({"code": "bad_adoption_review", "where": awhere,
                                       "detail": "each attempted change must be an object"})
                    continue
                if attempt.get("kind") not in _ADOPTION_MOD_TYPES:
                    violations.append({"code": "bad_adoption_review", "where": f"{awhere}.kind",
                                       "detail": "kind must be one of "
                                                 + ", ".join(sorted(_ADOPTION_MOD_TYPES))})
                if not _nonempty(attempt.get("reason")):
                    violations.append({"code": "incomplete_adoption_review",
                                       "where": f"{awhere}.reason",
                                       "detail": "each attempted change needs a reason / trade-off"})
    return violations


def _mega_options(c: dict[str, Any]) -> list[str]:
    plan = c.get("mega_plan") if isinstance(c.get("mega_plan"), dict) else {}
    out: list[str] = []
    for opt in plan.get("mega_options") or []:
        if not isinstance(opt, dict):
            continue
        for key in ("member", "form"):
            v = opt.get(key)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
    return out


def _mega_rationale_violations(rec: dict[str, Any], cand: dict[str, Any],
                               where: str) -> list[dict[str, Any]]:
    plan = cand.get("mega_plan") if isinstance(cand.get("mega_plan"), dict) else {}
    if (plan.get("registered_mega_count") or 0) <= 1:
        return []
    rat = rec.get("mega_registration_rationale")
    if not isinstance(rat, dict):
        return [{"code": "missing_mega_registration_rationale", "where": where,
                 "detail": "a recommended candidate with multiple registered Mega options must "
                           "explain the intended primary Mega, when the other Mega option is used, "
                           "and the opportunity cost"}]
    missing = [k for k in ("primary", "alternative_plan", "opportunity_cost")
               if not _nonempty(rat.get(k))]
    if missing:
        return [{"code": "incomplete_mega_registration_rationale",
                 "where": f"{where}.mega_registration_rationale",
                 "detail": "missing " + ", ".join(missing)}]
    raw_primary = rat.get("primary")
    primary_texts: list[str] = []
    if isinstance(raw_primary, dict):
        for key in ("member", "form", "option", "species", "name"):
            val = raw_primary.get(key)
            if isinstance(val, str) and val.strip():
                primary_texts.append(val.strip())
        if not primary_texts:                          # no recognized key — fall back to ALL string values
            primary_texts = [v.strip() for v in raw_primary.values()   # so a wrong primary can't slip
                             if isinstance(v, str) and v.strip()]       # through under a non-whitelisted key
    elif isinstance(raw_primary, str):
        primary_texts.append(raw_primary.strip())
    primary = " ".join(primary_texts).lower()
    options = _mega_options(cand)
    # Free-form zh/ja prose cannot be checked by English-canonical substring matching. Use the
    # structured primary object for language-independent verification; keep strict matching for pure
    # ASCII prose where the user is expected to name the canonical option.
    enforce_text_match = bool(primary_texts) and all(text.isascii() for text in primary_texts)
    if options and enforce_text_match and not any(opt.lower() in primary for opt in options):
        return [{"code": "mega_registration_primary_unmatched",
                 "where": f"{where}.mega_registration_rationale.primary",
                 "detail": "primary must name one of this candidate's registered Mega options: "
                           + ", ".join(options)}]
    return []


def _mega_deviation_violations(rec: dict[str, Any], cand: dict[str, Any],
                               where: str) -> list[dict[str, Any]]:
    assessment = (cand.get("mega_registration_assessment")
                  if isinstance(cand.get("mega_registration_assessment"), dict) else {})
    if not assessment.get("requires_deviation_ack"):
        return []
    dev = rec.get("mega_registration_deviation")
    if not isinstance(dev, dict):
        return [{"code": "missing_mega_registration_deviation", "where": where,
                 "detail": "this survivor uses an observed minority/rare Mega-registration lane; "
                           "disclose {reason,evidence,opportunity_cost} instead of presenting the "
                           "deviation as the frame's normal registration shape"}]
    missing = [key for key in ("reason", "evidence", "opportunity_cost")
               if not _nonempty(dev.get(key))]
    if missing:
        return [{"code": "incomplete_mega_registration_deviation",
                 "where": f"{where}.mega_registration_deviation",
                 "detail": "missing " + ", ".join(missing)}]
    return []


def _replacement_rationale_violations(rec: dict[str, Any], where: str) -> list[dict[str, Any]]:
    entries = rec.get("replacement_rationale")
    if entries is None:
        return []
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return [{"code": "bad_replacement_rationale", "where": f"{where}.replacement_rationale",
                 "detail": "replacement_rationale must be an object or a list of objects"}]
    violations: list[dict[str, Any]] = []
    for j, entry in enumerate(entries):
        ewhere = f"{where}.replacement_rationale[{j}]"
        if not isinstance(entry, dict):
            violations.append({"code": "bad_replacement_rationale", "where": ewhere,
                               "detail": "each replacement rationale must be an object"})
            continue
        missing = [k for k in ("out", "in", "reason", "benefit", "cost", "evidence")
                   if not _nonempty(entry.get(k))]
        if missing:
            violations.append({"code": "incomplete_replacement_rationale", "where": ewhere,
                               "detail": "post-checkpoint or previously user-visible member "
                                         "replacements must disclose " + ", ".join(missing)
                                         + "; evidence should cite a replace diff, slate grid, or "
                                           "equivalent before/after facts"})
    return violations


def audit_answer(draft: Any, slate_input: Any, slate_output: Any, *,
                 context_audit_fn: Callable[[dict], dict],
                 validate_fn: Callable[[dict], dict],
                 canon_team_fn: Callable[[dict], Any] | None = None,
                 canon_disclosures_fn: Callable | None = None,
                 recompute_fn: Callable[[list[dict]], list] | None = None,
                 library_copy_fn: Callable[[dict], dict | None] | None = None,
                 text_check_fn: Callable | None = None) -> dict[str, Any]:
    """The gate. Pure given the injected stages; the CLI wires the real siblings.

    `context_audit_fn(ctx)` -> a context-audit dict (fingerprint + blocking gaps + safe defaults);
    `validate_fn(team_c)` -> validate verdict; `canon_team_fn(team)` -> team_c or (team_c, flags)
    (ONE canonicalization per recommended team — feeds validate + claim binding);
    `recompute_fn(bindings)` -> per-binding fact dict / {"computed": False, reason} / None (=sibling
    down, disclosed as a note). Output: pass + violations(with code/where/detail) — a verdict, not a
    refusal; only a broken input/receipt chain refuses (CLI exits 2)."""
    if isinstance(draft, dict) and draft.get("kind") == "draft-init" \
            and isinstance(draft.get("draft"), dict):
        draft = draft["draft"]
    if not isinstance(draft, dict) or not isinstance(draft.get("recommended"), list) \
            or not draft["recommended"]:
        return _refuse("bad_draft",
                       "draft must be {environment, context_summary, recommended:[{slate_index, "
                       "team, tradeoffs}], convergence_rationale, assumptions, confidence_notes, "
                       "claims, slate_receipt, ...} with at least one recommended entry")
    shape_err = slate_mod.slate_shape_error(slate_input)
    if shape_err:
        return _refuse("bad_slate", "the ORIGINAL slate input is required to re-bind the receipt: "
                       + shape_err["reason"])
    out_receipt = (slate_output or {}).get("slate_receipt") if isinstance(slate_output, dict) else None
    if not isinstance(out_receipt, dict) or not out_receipt.get("fingerprint"):
        return _refuse("bad_slate_output",
                       "the SAVED slate-evaluate output (with its slate_receipt) is required — the "
                       "audit re-reads the battery facts and re-binds their fingerprint")
    if slate_output.get("view") == "summary":
        return _refuse("summary_slate_output",
                       "answer-audit requires the FULL saved slate-evaluate output; summary is "
                       "display-only and omits receipt-bound facts. Re-run team.py slate-evaluate "
                       "slate.json --slate-view full --format json and save that output unchanged.")
    provided = (draft.get("slate_receipt") or {}).get("fingerprint") \
        if isinstance(draft.get("slate_receipt"), dict) else None
    if not provided:
        return _refuse("missing_slate_receipt",
                       "run slate-evaluate and carry its slate_receipt in the draft — the answer "
                       "gate consumes the chain's second link")
    if provided != out_receipt["fingerprint"]:
        return _refuse("slate_receipt_mismatch",
                       "the draft's slate_receipt does not match the saved slate output — the draft "
                       "was written against a different evaluation. Re-run slate-evaluate.",
                       expected_fingerprint=out_receipt["fingerprint"], provided_fingerprint=provided)
    candidates = slate_output.get("candidates")
    candidates = candidates if isinstance(candidates, list) else []
    survivors = [i for i in (slate_output.get("survivors") or []) if isinstance(i, int)]
    battery_fmt = slate_output.get("format")

    def _cand(i: int) -> dict[str, Any]:
        # The saved output is a user-supplied file: a type-level tamper of a candidate entry must
        # land in the inconsistency refusal below, never an AttributeError before it (self-audit).
        c = candidates[i] if 0 <= i < len(candidates) else None
        return c if isinstance(c, dict) else {}

    recomputed = slate_mod.saved_slate_fingerprint(slate_input, slate_output)
    if recomputed != out_receipt["fingerprint"]:
        return _refuse("slate_output_inconsistent",
                       "the slate output does not reproduce its own receipt fingerprint from this "
                       "slate input — the output was edited after the run, or it is paired with a "
                       "different slate.json",
                       expected_fingerprint=recomputed, provided_fingerprint=out_receipt["fingerprint"])
    if not out_receipt.get("constraint_facts_bound"):
        return _refuse("receipt_upgrade_required", "saved slate does not bind battle-form constraints and frame binding facts; re-run slate-evaluate and answer-audit")
    if not out_receipt.get("comparison_facts_bound"):
        return _refuse("receipt_upgrade_required", "saved slate does not bind comparison and goal facts; re-run slate-evaluate")
    # A frame-bound (build-flow) slate that reproduces its fingerprint WITHOUT candidate_facts_bound
    # predates the Mega-registration / 6-pick-N selection gates (399fcf2): its receipt binds neither,
    # so the mega_registration_slate / deviation checks below would silently no-op on the pre-gate
    # receipt and grandfather it to PASS.  Refuse — the terminal gate re-runs against the current
    # chain instead of certifying an answer whose registration/selection facts were never bound.
    if out_receipt.get("frame_fingerprint") and not out_receipt.get("candidate_facts_bound"):
        return _refuse("receipt_upgrade_required",
                       "this build-flow slate output predates the Mega-registration / selection "
                       "gates (frame-bound receipt without candidate_facts_bound). Re-run "
                       "team.py slate-evaluate --frame-output so the receipt binds the current "
                       "candidate facts, then re-audit.")
    ctx = slate_input.get("context") if isinstance(slate_input.get("context"), dict) else {}
    ctx_audit = context_audit_fn(ctx) or {}
    audit_fp = (ctx_audit.get("audit_receipt") or {}).get("fingerprint")
    if audit_fp != out_receipt.get("audit_fingerprint"):
        return _refuse("audit_receipt_mismatch",
                       "the slate receipt's audit fingerprint does not match a context-audit "
                       "recompute of the slate's context — the context (or the environment) changed "
                       "since slating. Re-run the chain from context-audit.",
                       expected_fingerprint=audit_fp,
                       provided_fingerprint=out_receipt.get("audit_fingerprint"))

    violations: list[dict[str, Any]] = []
    notes: list[str] = []
    violations.extend(exploration.violations(draft, slate_output))
    name_resolution: dict[str, list] = {}
    if ctx.get("frame_required") is True and not out_receipt.get("frame_fingerprint"):
        violations.append({"code": "missing_frame_receipt",
                           "where": "slate_receipt.frame_fingerprint",
                           "detail": "the slate context declares frame_required:true, but the saved "
                                     "slate output has no frame fingerprint. Re-run team.py frame, then "
                                     "slate-evaluate with --frame-output before drafting the answer."})
    violations.extend(_onboarding_summary_violations(draft, ctx))
    mega_slate = (slate_output.get("mega_registration_slate")
                  if isinstance(slate_output.get("mega_registration_slate"), dict) else {})
    if mega_slate.get("status") == "missing_modal_lane":
        violations.append({
            "code": "missing_modal_mega_registration_lane",
            "where": "mega_registration_slate",
            "detail": "no survivor is modal relative to its bound frame's observed Mega-registration "
                      "distribution. Re-slate with at least one modal lane, or encode the "
                      "user's explicit none/single/multi mega_posture in build-context and rerun the chain.",
        })
    if not library_copy_fn:
        # The transparency gate must never fail SILENT: without a library to check against, a
        # verbatim copy would pass indistinguishably from "checked and clean" (self-audit
        # 2026-07-03) — same honest-degrade contract as the claim-recompute outage note.
        notes.append("library copy check SKIPPED (library unavailable/empty for this partition) — "
                     "verbatim-adoption transparency was NOT verified this run.")

    # --- recommended entries: identity, distinctness, survivor-ship, legality, trade-offs --------
    canon_rec: list[tuple[int | None, dict[str, Any]]] = []
    revalidated: dict[int, dict[str, Any]] = {}      # slate_index -> fresh validate verdict (A-1)
    seen_teams: dict[str, int] = {}                  # content hash -> first recommended index
    for r_i, rec in enumerate(draft["recommended"]):
        where = f"recommended[{r_i}]"
        rec = rec if isinstance(rec, dict) else {}
        idx, team = rec.get("slate_index"), rec.get("team")
        if not (isinstance(idx, int) and 0 <= idx < len(slate_input["teams"])) \
                or not isinstance(team, dict):
            violations.append({"code": "recommended_not_slated", "where": where,
                               "detail": "each recommended entry needs slate_index (into the slated "
                                         "teams) + the team-json it presents — never present a "
                                         "candidate that was never slated"})
            canon_rec.append((None, {}))
            continue
        team_h = slate_mod.content_hash(team)
        if team_h in seen_teams:
            # Distinctness is CONTENT-level (same index twice, or the same team slated at two
            # indexes): repeating one team must not impersonate the default 2-3 distinct
            # candidates nor dodge the single-team declaration (external audit 2026-07-03).
            violations.append({"code": "recommended_duplicate", "where": where,
                               "detail": f"identical to recommended[{seen_teams[team_h]}] — the "
                                         "default answer is 2-3 DISTINCT candidates; repeating one "
                                         "team does not satisfy it"})
        else:
            seen_teams[team_h] = r_i
        if team_h != slate_mod.content_hash(slate_input["teams"][idx]):
            violations.append({"code": "recommended_team_differs_from_slated", "where": where,
                               "detail": "the presented team is NOT the team that was slated at this "
                                         "index — any post-slate edit must be re-slated (the battery "
                                         "facts no longer describe this team)"})
        if idx not in survivors:
            # Survivorship authority = the fingerprint-BOUND survivors list, never the loose
            # per-candidate `eliminated` field: that field sits outside the receipt digest, so a
            # one-key edit of the saved output would clear it without re-fingerprinting (self-audit
            # 2026-07-03 — the check must read what the chain actually binds).
            elim = _cand(idx).get("eliminated")
            reasons = (elim.get("reasons") if isinstance(elim, dict) else None) or []
            violations.append({"code": "recommended_was_eliminated", "where": where,
                               "detail": "; ".join(str(r) for r in reasons)
                                         or "not a battery survivor (see the slate output)"})
        res = canon_team_fn(team) if canon_team_fn else team
        team_c, flags = res if isinstance(res, tuple) else (res, [])
        if flags:
            name_resolution[where] = list(flags)
        canon_rec.append((idx, team_c))
        verdict = validate_fn(team_c) or {}
        revalidated[idx] = verdict                    # answer-time legality authority (A-1)
        if verdict.get("status") == "invalid":
            violations.append({"code": "recommended_invalid", "where": where,
                               "detail": "re-validate at answer time returned INVALID: "
                                         + "; ".join(str(e) for e in (verdict.get("errors") or [])[:3])})
        elif verdict.get("status") == "unknown":
            notes.append(f"{where}: re-validate returned UNKNOWN (dex degraded) — legality was not "
                         "re-confirmed at answer time.")
        if library_copy_fn:
            # Library guardrail (§A.D), transparency-gated (user ruling 2026-07-03): ADOPTING an
            # observed build is legitimate — often the best answer as the library saturates the
            # good-team space — but presenting one AS YOUR OWN SYNTHESIS is not. A verbatim
            # joint-set match therefore requires the entry to carry `observed_provenance`
            # (structured, language-independent); silence is the violation, not the overlap.
            # RECOMPUTED here (not read from the saved slate output): library_overlap is outside
            # the receipt digest, so a stripped output must not dodge the check.
            overlap = library_copy_fn(team_c)
            if overlap and overlap.get("verbatim_ids"):
                prov = rec.get("observed_provenance")
                if not (isinstance(prov, str) and prov.strip())                         and not (isinstance(prov, dict) and prov):
                    violations.append({
                        "code": "library_copy_undisclosed", "where": where,
                        "detail": "this exact joint set IS a stored observed team ("
                                  + ", ".join(overlap["verbatim_ids"][:3])
                                  + ") — adopting it is fine, presenting it silently as your own "
                                    "synthesis is not: add observed_provenance (the overlap fact + "
                                    "why the facts fit this intent)."})
                violations.extend(_adoption_review_violations(rec, overlap, where))
            elif overlap and overlap.get("same_composition"):
                notes.append(f"{where}: same six species as {overlap['same_composition']} observed "
                             "team(s) (sets differ) — composition convergence, no disclosure "
                             "required.")
        tradeoffs = rec.get("tradeoffs")
        if not (isinstance(tradeoffs, list)
                and any(isinstance(x, str) and x.strip() for x in tradeoffs)):
            violations.append({"code": "missing_tradeoffs", "where": where,
                               "detail": "every recommended candidate carries at least one honest "
                                         "trade-off — a recommendation without a cost is a crowned "
                                         "winner"})
        violations.extend(_mega_rationale_violations(rec, _cand(idx), where))
        violations.extend(_mega_deviation_violations(rec, _cand(idx), where))
        violations.extend(_replacement_rationale_violations(rec, where))

    # Carrying a modal lane in the slate is not enough if the final answer silently drops it.  Under
    # the environment posture the recommended set retains at least one survivor that was modal relative
    # to its own frame; an intentional single/none/multi or off-meta answer must be encoded upstream and
    # re-slated so the receipt states that posture instead of laundering the choice at prose time.
    modal_indices = {i for i in (mega_slate.get("modal_lane_indices") or []) if isinstance(i, int)}
    recommended_indices = {i for i, _ in canon_rec if isinstance(i, int) and i in survivors}
    violations.extend(exploration.team_claim_violations(draft, slate_output, recommended_indices))
    if mega_slate.get("status") == "covered" and modal_indices \
            and not (recommended_indices & modal_indices):
        violations.append({
            "code": "recommended_omits_modal_mega_registration_lane",
            "where": "recommended",
            "detail": "the slate carried a candidate that is modal relative to its bound frame, but "
                      "the recommended set omits every such lane. Include at least one, or encode an "
                      "explicit mega_posture/off_meta intent upstream and rerun the receipt chain.",
        })

    # One team = one DISTINCT team (duplicates collapse), and the declaration is the TYPED contract
    # `single_team_requested: true` — a truthy string like "yes"/"false" is not a declaration
    # (external audit 2026-07-03).
    distinct = len(seen_teams) if seen_teams else len(draft["recommended"])
    if distinct == 1 and draft.get("single_team_requested") is not True \
            and not (isinstance(draft.get("alternatives_omitted_reason"), str)
                     and draft["alternatives_omitted_reason"].strip()):
        violations.append({"code": "single_team_undeclared", "where": "recommended",
                           "detail": "one distinct team only: declare single_team_requested: true "
                                     "(boolean true — the draft contract is typed; a string does "
                                     "not count) because the user explicitly asked for one, OR "
                                     "give alternatives_omitted_reason"})

    # --- environment stamp ------------------------------------------------------------------------
    env = draft.get("environment") if isinstance(draft.get("environment"), dict) else {}
    out_env = slate_output.get("environment") if isinstance(slate_output.get("environment"), dict) else {}
    if not env.get("season"):
        violations.append({"code": "missing_environment", "where": "environment",
                           "detail": "the draft must stamp the environment it answers in "
                                     "(season at minimum)"})
    elif out_env.get("season") and env.get("season") != out_env.get("season"):
        # Informational cross-check on the CLI-appended environment stamp (which is NOT in the slate
        # fingerprint). The AUTHORITATIVE season binding is the audit_fingerprint — it digests the
        # resolved env season/rule and is re-verified by the audit_receipt recompute upstream — so a
        # forged stamp here cannot slip a wrong-season answer past the chain; this only adds a friendlier
        # season-mismatch note (audit 2026-07-06).
        violations.append({"code": "environment_mismatch", "where": "environment.season",
                           "detail": f"draft says {env.get('season')!r} but the slate ran under "
                                     f"{out_env.get('season')!r}"})

    violations.extend(_tuning_summary_violations(draft))

    # --- convergence scaffold: filled for EVERY battery survivor (discipline injection — the
    # checker only checks FILLED, never whether the reasoning is good; mechanically-eliminated
    # candidates already carry machine reasons and are exempt) --------------------------------------
    cr = draft.get("convergence_rationale") if isinstance(draft.get("convergence_rationale"), dict) else {}
    for i in survivors:
        entry = cr.get(str(i))                       # JSON object keys are always strings
        entry = entry if isinstance(entry, dict) else {}
        # worst_matchup / opportunity_cost are inherently textual; accepted_by_constraint is a
        # yes/no judgment, so a bare bool (INCLUDING False — "not accepted" is honest content)
        # counts as filled there and only there.
        missing = [f for f in _SCAFFOLD_FIELDS
                   if not (_filled(entry.get(f))
                           or (f == "accepted_by_constraint" and isinstance(entry.get(f), bool)))]
        if missing:
            violations.append({"code": "missing_convergence_rationale",
                               "where": f"convergence_rationale[{i}]",
                               "detail": "every battery survivor (recommended OR passed over) needs "
                                         + ", ".join(missing)})

    # --- blocking gaps at slating must be disclosed as assumptions ---------------------------------
    # Disclosure entries are strings OR structured {"gap": <field>, "note": ...} objects. The
    # structured form is checked by FIELD equality and is language-independent — a zh/ja draft's
    # prose never contains the English field token, so bare-substring matching alone would
    # false-violate every non-English draft (self-audit 2026-07-03). String fallback tolerates
    # singular/plural ("benchmark ..." discloses field benchmarks[0].vs).
    disclosures: list[str] = []
    gap_tags: set[str] = set()
    for entry in list(draft.get("assumptions") or []) + list(draft.get("confidence_notes") or []):
        if isinstance(entry, str):
            disclosures.append(entry)
        elif isinstance(entry, dict):
            if entry.get("gap"):
                gap_tags.add(str(entry["gap"]).lower())
            disclosures.extend(v for v in entry.values() if isinstance(v, str))
    blocking = list(ctx_audit.get("missing_required") or [])
    if draft.get("request_expressive"):
        # The P2 escalation hook: an anchor-less EXPRESSIVE request makes the anchor default
        # blocking (defaulting it erases the intent) — the draft declares expressiveness, P6 enforces.
        blocking += [{"field": sd.get("field"), "reason": sd.get("note")}
                     for sd in ctx_audit.get("safe_defaults") or []
                     if sd.get("escalates_to") == "blocking_if_expressive"]
    lowered = " ".join(disclosures).lower()
    for gap in blocking:
        field = str(gap.get("field") or "")
        token = (field.split("[")[0].split(".")[0] or field).lower()
        disclosed = (field.lower() in gap_tags or token in gap_tags
                     or (token and (token in lowered or token.rstrip("s") in lowered)))
        if not disclosed:
            violations.append({"code": "blocking_gap_undisclosed", "where": f"context:{field}",
                               "detail": "a blocking gap was present at slating; answering anyway "
                                         "requires an assumptions/confidence_notes entry naming it — "
                                         f"a string containing {token or field!r}, or the "
                                         f"language-independent form {{\"gap\": {field!r}, "
                                         "\"note\": ...}"})

    # --- P4.5: a recommended team's frame YELLOW deviations / off-frame advisories must be disclosed
    # (design §19.10, answer-audit's LIGHT duty — read slate's saved flags, a filled-check, NEVER a
    # second frame run or a repset recompute; the heavy verification lived at slate). Red deviations
    # already eliminated the candidate, so a survivor only carries acknowledged/advisory ones — the
    # user must still be told the off-meta choice was made. `frame_deviations` (draft field) joins the
    # assumptions/confidence_notes disclosure pool; naming the species anywhere in them clears it. ------
    fd_pool = list(disclosures)
    disclosure_view = draft
    if canon_disclosures_fn:
        disclosure_view, flags, errors = canon_disclosures_fn(draft)
        if flags:
            name_resolution["frame_deviations"] = flags
        for error in errors:
            violations.append({"code": "unresolved_disclosure_name", "where": error["path"],
                               "detail": "frame deviation species must resolve unambiguously", "resolution": error})
    for entry in list(disclosure_view.get("frame_deviations") or []):
        if isinstance(entry, str):
            fd_pool.append(entry)
        elif isinstance(entry, dict):
            if entry.get("species"):
                gap_tags.add(str(entry["species"]).lower())
            else:
                fd_pool.extend(v for v in entry.values() if isinstance(v, str))
    fd_lowered = " ".join(fd_pool).lower()
    rec_idxs = sorted({r.get("slate_index") for r in draft["recommended"]
                       if isinstance(r, dict) and isinstance(r.get("slate_index"), int)})
    for idx in rec_idxs:
        if idx not in survivors:
            continue                                  # an eliminated recommendation is already flagged
        fb = _cand(idx).get("frame_binding") if isinstance(_cand(idx).get("frame_binding"), dict) else {}
        flagged = [d.get("species") for d in (fb.get("deviations") or []) if isinstance(d, dict)]
        flagged += [a.get("species") for a in (fb.get("advisories") or []) if isinstance(a, dict)]
        for sp in {s for s in flagged if s}:
            if not (str(sp).lower() in gap_tags or str(sp).lower() in fd_lowered):
                violations.append({"code": "frame_deviation_undisclosed",
                                   "where": f"recommended(slate_index={idx})",
                                   "detail": f"the frame binding flagged {sp} as an off-meta / "
                                             "off-frame pick on this recommended team; disclose it "
                                             "(name the species in frame_deviations / assumptions / "
                                             "confidence_notes) so the user sees the choice was "
                                             "deliberate — the gate is transparency, not a veto"})

    # --- low-confidence slate facts must be disclosed, PER candidate: the note must NAME the
    # candidate (one of its species, or "candidate <i>") — one generic note about anything must not
    # silence the check for every low-confidence recommendation (self-audit 2026-07-03).
    conf_texts = [s.lower() for s in (draft.get("confidence_notes") or [])
                  if isinstance(s, str) and s.strip()]
    for r_i, (idx, team_c) in enumerate(canon_rec):
        if idx is None:
            continue
        c = _cand(idx)
        # Legality confidence is read from answer-audit's OWN fresh re-validate (revalidated[idx]),
        # NOT the saved slate output: legality is not part of the slate fingerprint, so trusting the
        # stored value let a one-key edit ("low"->"high") silence this disclosure check without
        # breaking the receipt (audit 2026-07-06). matchup_risk.confidence IS fingerprint-bound, so it
        # still reads from the bound candidate.
        fresh_legality_low = (revalidated.get(idx) or {}).get("confidence") == "low"
        if not (fresh_legality_low
                or (c.get("matchup_risk") or {}).get("confidence") == "low"):
            continue
        species = [m.get("species", "").lower() for m in team_c.get("pokemon", []) if m.get("species")]
        named = any(f"candidate {idx}" in t or any(sp and sp in t for sp in species)
                    for t in conf_texts)
        if not named:
            violations.append({"code": "low_confidence_undisclosed", "where": f"recommended[{r_i}]",
                               "detail": "the slate marked this candidate's facts low-confidence; "
                                         "a confidence_notes entry must name it (a species on it, "
                                         f"or 'candidate {idx}')"})

    # --- banned absolute-strength wording (bounded tripwire) ---------------------------------------
    for path, text in _prose_strings(draft):
        low_text = text.lower()
        for term in BANNED_CLAIM_TERMS:
            if term in low_text:
                violations.append({"code": "banned_claim", "where": path,
                                   "detail": f"banned absolute-strength wording {term!r} — facts + "
                                             "trade-offs, never a crowned winner"})

    # --- claims: bind to the grammar, then recompute -------------------------------------------------
    # Direction authority: an evidence_id COPIED from the saved slate output tells us which side of
    # the original battery cell was the MEMBER and which the modal opponent. Without it, a species
    # shared by the team and the meta top-K (an Incineroar/Garchomp staple) would rebind the modal
    # side to the registered set and fail a claim copied verbatim from the battery's own receipt
    # (self-audit 2026-07-03). Hand-authored ids not present in the output fall back to
    # membership-based binding.
    eid_index: dict[str, list[dict[str, Any]]] = {}
    for i in survivors:
        mr = _cand(i).get("matchup_risk") or {}
        for bucket, direction in (("opponents_we_ohko_guaranteed", "we"),
                                  ("opponents_we_ohko_possible", "we"),
                                  ("opponents_with_guaranteed_ohko_on_us", "them")):
            bucket_rows = mr.get(bucket) if isinstance(mr.get(bucket), dict) else {}
            for rows in bucket_rows.values():
                for row in rows if isinstance(rows, list) else []:
                    if isinstance(row, dict) and row.get("evidence_id"):
                        # `opponent_variant` names the exact observed build the battery ran this cell
                        # against. Without it the recompute re-resolves the species to its MODAL
                        # build, which for a multi-archetype species is a different opponent and a
                        # different number — an honest claim then reads as fabricated (audit
                        # 2026-08-06). It is battery-supplied binding data, exactly like `member`
                        # and `direction`; the calc itself is still re-run independently below.
                        eid_index.setdefault(row["evidence_id"], []).append(
                            {"candidate": i, "member": row.get("member"), "direction": direction,
                             "opponent_variant": row.get("opponent_variant"),
                             "opponent_is_modal": row.get("opponent_is_modal")})

    bindings: list[dict[str, Any]] = []
    meta: list[tuple[str, str, dict]] = []
    pool = [(idx, tc) for idx, tc in canon_rec if idx is not None]
    for c_i, c in enumerate(draft.get("claims") or []):
        where = f"claims[{c_i}]"
        c = c if isinstance(c, dict) else {}
        eid = c.get("evidence_id")
        if not eid:
            violations.append({"code": "claim_unbound", "where": where,
                               "detail": "every quantitative claim carries the evidence_id that "
                                         "produced it (" + slate_mod.EVIDENCE_ID_GRAMMAR + ")"})
            continue
        parsed = slate_mod.parse_evidence_id(eid)
        if not parsed:
            violations.append({"code": "claim_evidence_unparseable", "where": where,
                               "detail": f"{eid!r} does not parse under the grammar: "
                                         + slate_mod.EVIDENCE_ID_GRAMMAR})
            continue
        if battery_fmt and parsed["format"] != battery_fmt:
            violations.append({"code": "claim_format_mismatch", "where": where,
                               "detail": f"claim addresses format {parsed['format']!r} but this "
                                         f"answer's battery ran {battery_fmt!r} — metagames are "
                                         "never mixed"})
            continue
        allowed = DAMAGE_EXPECT_KEYS if parsed["kind"] == "damage" else SPEED_EXPECT_KEYS
        expect = c.get("expect")
        if not isinstance(expect, dict) or not expect or not set(expect) <= set(allowed):
            violations.append({"code": "claim_expect_invalid", "where": where,
                               "detail": "expect must be a non-empty subset of "
                                         f"{sorted(allowed)} — the structured numbers the recompute "
                                         "checks against"})
            continue
        team_filter = c.get("team") if isinstance(c.get("team"), int) else None
        cpool = [(idx, tc) for idx, tc in pool if team_filter is None or idx == team_filter]

        def member_matches(name: str) -> list[tuple[int, dict[str, Any]]]:
            out: list[tuple[int, dict[str, Any]]] = []
            for idx, tc in cpool:
                for m in tc.get("pokemon", []):
                    if m.get("species") == name:
                        out.append((idx, m))
            return out

        def member_of(name: str) -> tuple[dict[str, Any] | None, bool]:
            matches = member_matches(name)
            if team_filter is None and len({idx for idx, _ in matches}) > 1:
                return None, True
            return (matches[0][1], False) if matches else (None, False)

        def ambiguous_claim(member: str) -> dict[str, Any]:
            return {"code": "claim_team_ambiguous", "where": where,
                    "detail": f"evidence_id {eid!r} matches member {member!r} on multiple "
                              "recommended teams; add claims[].team with the slate_index to bind "
                              "which presented team's set is being recomputed"}

        if parsed["kind"] == "damage":
            qualifiers = {k: c[k] for k in ("direction", "opponent_variant", "opponent_is_modal") if k in c}
            if ("direction" in qualifiers and qualifiers["direction"] not in ("we", "them")
                    or "opponent_variant" in qualifiers and not
                    (isinstance(qualifiers["opponent_variant"], str) and qualifiers["opponent_variant"].strip())
                    or "opponent_is_modal" in qualifiers and type(qualifiers["opponent_is_modal"]) is not bool):
                violations.append({"code": "claim_binding_invalid", "where": where,
                                   "detail": "damage claims accept direction:we|them, a nonempty opponent_variant, "
                                             "and boolean opponent_is_modal"})
                continue
            indexed = [r for r in (eid_index.get(eid) or [])
                       if any(idx == r.get("candidate") for idx, _ in cpool)]
            if eid in eid_index and not indexed:
                violations.append({"code": "claim_subject_not_recommended", "where": where,
                                   "detail": f"the saved battery has {eid!r}, but not for the "
                                             "recommended team selected by claims[].team"})
                continue
            if indexed and team_filter is None and len({r.get("candidate") for r in indexed}) > 1:
                violations.append(ambiguous_claim(str(indexed[0].get("member") or "")))
                continue
            # Match explicit qualifiers against saved facts, never substitute the first variant.
            # Guaranteed/possible buckets may repeat the same cell; those are one binding.
            indexed = [r for r in indexed if all(r.get(k) == v for k, v in qualifiers.items())]
            if qualifiers and not indexed:
                violations.append({"code": "claim_evidence_mismatch", "where": where,
                                   "detail": "the stated opponent build/direction does not match a saved battery cell "
                                             "for this evidence_id and team"})
                continue
            identities = {(r["candidate"], r["member"], r["direction"], r["opponent_variant"]) for r in indexed}
            if len(identities) > 1:
                violations.append({"code": "claim_evidence_ambiguous", "where": where,
                                   "detail": "multiple saved cells share these coordinates; specify opponent_variant "
                                             "and direction:we|them to bind one cell"})
                continue
            known = indexed[0] if indexed else None
            if known:
                mm, ambiguous = member_of(known.get("member") or "")
                if ambiguous:
                    violations.append(ambiguous_claim(str(known.get("member") or "")))
                    continue
                if mm is None:
                    violations.append({"code": "claim_subject_not_recommended", "where": where,
                                       "detail": f"the battery ran this cell with member "
                                                 f"{known.get('member')!r}, which is not on any "
                                                 "recommended team"})
                    continue
                am, dm = (mm, None) if known["direction"] == "we" else (None, mm)
            else:
                am, amb_a = member_of(parsed["attacker"])
                dm, amb_d = member_of(parsed["defender"])
                if amb_a or amb_d:
                    violations.append(ambiguous_claim(parsed["attacker"] if amb_a else parsed["defender"]))
                    continue
                if am is None and dm is None:
                    violations.append({"code": "claim_subject_not_recommended", "where": where,
                                       "detail": "neither side of the coordinates is on a recommended "
                                                 "team — claims bind YOUR presented teams to the field"})
                    continue
            # The opponent side is whichever side is NOT one of our bound members, so that is where
            # the battery's observed-build id applies.
            opponent_variant = (known or {}).get("opponent_variant")
            bindings.append({"kind": "damage", "format": parsed["format"],
                             "attacker": parsed["attacker"], "defender": parsed["defender"],
                             "move": parsed["move"],
                             "attacker_member": am, "defender_member": dm,
                             **({"defender_variant": opponent_variant} if opponent_variant and dm is None
                                else {}),
                             **({"attacker_variant": opponent_variant} if opponent_variant and am is None
                                else {})})
        else:
            mm, ambiguous = member_of(parsed["member"])
            if ambiguous:
                violations.append(ambiguous_claim(parsed["member"]))
                continue
            if mm is None:
                violations.append({"code": "claim_subject_not_recommended", "where": where,
                                   "detail": "the member side of a spd claim must be on a "
                                             "recommended team"})
                continue
            # The opponent side of a spd pair is ALWAYS the modal set — that is what the grammar
            # defines ("registered set vs modal set"); binding a same-species teammate here would
            # silently change the claim's meaning (self-audit 2026-07-03).
            bindings.append({"kind": "speed", "format": parsed["format"],
                             "member": parsed["member"], "opponent": parsed["opponent"],
                             "member_member": mm})
        meta.append((where, eid, expect))

    results = list(recompute_fn(bindings)) if (recompute_fn and bindings) else [None] * len(bindings)
    skipped = recomputed_n = 0
    for (where, eid, expect), fact in zip(meta, results):
        if fact is None:
            skipped += 1
            continue
        if not fact.get("computed", True):
            violations.append({"code": "claim_recompute_failed", "where": where,
                               "detail": f"{eid}: coordinates did not reproduce — "
                                         + str(fact.get("reason") or "no result")})
            continue
        recomputed_n += 1
        for k, want in expect.items():
            got = fact.get(k)
            if _num(want) is not None and _num(got) is not None:
                tol = _PCT_TOLERANCE if k in ("min_percent", "max_percent") else 0.0
                ok = abs(_num(want) - _num(got)) <= tol
            else:
                ok = want == got
            if not ok:
                violations.append({"code": "claim_numbers_differ", "where": where,
                                   "detail": f"{eid}: draft says {k}={want!r} but the recompute "
                                             f"says {got!r}"})
    if skipped:
        notes.append(f"{skipped} claim(s) NOT recomputed (calc/meta/dex sibling unavailable) — "
                     "recompute skipped honestly; those numbers rest on the draft's word and "
                     "should be presented with that caveat.")

    text_result = None
    if "text_check" in draft:
        if text_check_fn is None:
            violations.append({"code": "text_check_unavailable", "where": "text_check",
                               "detail": "requested text checking requires the official dex vocabulary"})
        else:
            text_draft = {**draft, "recommended": [{**rec, "team": team_c}
                          for rec, (_, team_c) in zip(draft["recommended"], canon_rec)]}
            text_result = text_check_fn(text_draft, slate_input, slate_output)
            violations.extend(text_result.get("violations") or [])

    out = {
        "kind": "answer-audit",
        "pass": not violations,
        "violations": violations,
        "checked": {"recommended": len(draft["recommended"]), "survivors": len(survivors),
                    "claims": len(draft.get("claims") or []), "claims_recomputed": recomputed_n,
                    "blocking_gaps_at_slating": len(blocking)},
        "slate_receipt_fingerprint": provided,
        "notes": notes + [
            "PASS = the answer's FORM is complete and its quantitative claims reproduce under "
            "recompute — NOT that the trade-offs are right; that judgment was never auditable "
            "here and stays the reader's.",
            "receipt chain verified end-to-end: draft <-> saved slate output <-> slate input teams "
            "<-> context, all re-bound by recomputed fingerprints — a stale or foreign receipt "
            "refuses instead of passing.",
        ],
    }
    if name_resolution:
        out["name_resolution"] = name_resolution
    if text_result is not None:
        out["text_check"] = text_result
    return out
