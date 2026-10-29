#!/usr/bin/env python
"""slate-evaluate (UEP P5): the candidate fact-matrix gate — 2–5 AI-assembled candidates go through
ONE uniform pressure test, so "compose it, then just write the answer" has no cheap path.

Capability chain (§A.B): the slate must carry the `audit_receipt` of a context-audit run on EXACTLY
this context — the fingerprint is recomputed here and a mismatch refuses (honest boundary: this
proves the audit ran on this context, not that its gaps were acted on). The output carries a
`slate_receipt` the answer-audit (P6) will require in turn.

Funnel (§A.B — cost is a skip incentive): the CHEAP stage runs for every candidate (structure
contract, legality, constraint satisfaction, structural profile, objective gap flags); the
EXPENSIVE battery (matchup vs the meta top-K) runs only for survivors. An eliminated candidate
carries its reasons and never bills the battery.

Output discipline (pinned by tests):
- INPUT ORDER preserved; no aggregate column, no winner, no score — a comparison grid the reader
  may sort by any column.
- flags are OBJECTIVE gaps (an archetype may deliberately accept one), never defects.
- Evidence addressability (勘误④a): every quantitative extreme carries
  `evidence_id = "ncp:{fmt}:{attacker}|{defender}|{move}"` — re-runnable coordinates (one ncp calc
  with the same resolved sets), the hook P6's claim↔recompute check binds to.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from canonhash import content_hash
from mega_facts import assess_registration, mega_plan_from_profile
from frame import frame_fingerprint as _frame_fingerprint  # P4.5 chain: recompute the frame receipt
import slate_comparison
from team_views import coverage as compact_coverage


def evidence_id(fmt: str, attacker: str, defender: str, move: str) -> str:
    return f"ncp:{fmt}:{attacker}|{defender}|{move}"


EVIDENCE_ID_GRAMMAR = ("ncp:{format}:{attacker}|{defender}|{move} — recompute by resolving the "
                       "defender/attacker sets as matchup does and running that single ncp calc; "
                       "spd:{format}:{member}|{opponent} — recompute both effective speeds as a "
                       "matchup cell does (registered set vs modal set). Both are re-runnable "
                       "COORDINATES, not stored ids — spd never appears in this output (speed facts "
                       "are aggregate counts here) but is how a draft addresses an outspeed claim.")


def parse_evidence_id(eid: Any) -> dict[str, Any] | None:
    """Parse an evidence_id back into its re-runnable coordinates. The grammar and its parser live
    HERE and only here — the answer-audit imports this, never re-defines it (勘误④a的同款双源防线)."""
    if not isinstance(eid, str):
        return None
    head, _, tail = eid.partition(":")
    fmt, sep, rest = tail.partition(":")
    if not sep or not fmt:
        return None
    parts = rest.split("|")
    if head == "ncp" and len(parts) == 3 and all(parts):
        return {"kind": "damage", "format": fmt,
                "attacker": parts[0], "defender": parts[1], "move": parts[2]}
    if head == "spd" and len(parts) == 2 and all(parts):
        return {"kind": "speed", "format": fmt, "member": parts[0], "opponent": parts[1]}
    return None


# content_hash lives in canonhash (imported above) so frame/slate/answer-audit share ONE hasher; it
# stays importable as slate.content_hash for the answer-audit bait-and-switch check.


def slate_fingerprint(audit_fp: str | None, teams: list, survivors: list[int], battery_fmt: str,
                      matchup_risks: dict[int, Any], frame_fp: str | None = None,
                      candidate_facts: dict[Any, Any] | None = None) -> str:
    """The slate_receipt fingerprint construction, extracted so the answer-audit RECOMPUTES the same
    thing from (slate input, saved slate output) instead of re-deriving its own — binding audit fp +
    team content + survivors + the battery facts (a different top-K or edited matchup_risk must
    re-fingerprint, external audit 2026-07-02) + the frame receipt (P4.5: a swapped/edited frame the
    core-bearer binding checked against must re-fingerprint; None on a non-build slate, back-compat)."""
    risk_digest = {str(i): content_hash(matchup_risks.get(i), 12) for i in survivors}
    body: dict[str, Any] = {"audit": audit_fp,
                            "teams": [content_hash(t, 16) for t in teams],
                            "survivors": survivors, "format": battery_fmt, "risk": risk_digest}
    if frame_fp is not None:                     # omit when absent so pre-frame receipts still reproduce
        body["frame"] = frame_fp
    if candidate_facts is not None:
        body["candidate_facts"] = {
            str(i): content_hash(candidate_facts.get(i), 12) for i in survivors}
        if "_slate" in candidate_facts:
            body["candidate_set_facts"] = content_hash(candidate_facts["_slate"], 12)
        if "_comparison" in candidate_facts:
            body["comparison"] = content_hash(candidate_facts["_comparison"], 16)
    return content_hash(body, 24)


def saved_slate_fingerprint(slate_input: dict, slate_output: dict) -> str:
    """Re-bind saved facts using the same fields for draft transport and the terminal answer gate."""
    receipt = slate_output.get("slate_receipt") or {}
    candidates = slate_output.get("candidates")
    candidates = candidates if isinstance(candidates, list) else []
    survivors = [i for i in (slate_output.get("survivors") or []) if isinstance(i, int)]

    def candidate(i):
        c = candidates[i] if 0 <= i < len(candidates) else None
        return c if isinstance(c, dict) else {}

    facts = None
    if receipt.get("candidate_facts_bound"):
        facts = {i: {k: candidate(i).get(k) for k in
                     ("mega_plan", "mega_registration_assessment", "selection")} for i in survivors}
        facts["_slate"] = slate_output.get("mega_registration_slate")
        if receipt.get("constraint_facts_bound"):
            for i in survivors:
                facts[i].update({k: candidate(i).get(k) for k in ("constraint_satisfaction", "frame_binding")})
        if receipt.get("comparison_facts_bound"):
            facts["_comparison"] = slate_output.get("comparison")
            for i in survivors:
                facts[i]["comparison_facts"] = candidate(i).get("comparison_facts")
                facts[i]["library_overlap"] = candidate(i).get("library_overlap")
    return slate_fingerprint(receipt.get("audit_fingerprint"), slate_input["teams"], survivors,
                             slate_output.get("format"), {i: candidate(i).get("matchup_risk") for i in survivors},
                             receipt.get("frame_fingerprint"), facts)


def slate_shape_error(slate: Any) -> dict[str, Any] | None:
    """The bad-slate refusal, SHARED by the pure gate and the CLI wrapper: the wrapper must run this
    before touching any slate field (a top-level JSON array crashed `.get` in the shell while the
    pure function's refusal sat one call later; external audit 2026-07-02)."""
    if not isinstance(slate, dict) or not isinstance(slate.get("teams"), list) or not slate["teams"]:
        return {"code": "bad_slate",
                "reason": "slate must be {context, audit_receipt, teams:[team-json, ...]}"}
    return None


def _matchup_digest(mres: dict[str, Any], fmt: str) -> dict[str, Any]:
    """Counting facts from a matchup grid: who we can KO / who can KO us / speed pairs — each
    extreme carries its evidence_id. Counts, never a score."""
    we_g: dict[str, list] = {}
    we_p: dict[str, list] = {}
    them_g: dict[str, list] = {}
    we_faster = pairs = 0
    for row in mres.get("members") or []:
        me = row.get("member")
        # ``member`` remains the registered/base name used to bind the row back to team-json.
        # Evidence coordinates name the actual run form because that is whose stats and ability
        # produced the fact (notably base + Mega Stone registrations).
        calc_me = row.get("member_run_form") or me
        for c in row.get("cells") or []:
            opp = c.get("opponent")
            off = c.get("offense") or {}
            # A fact without a move has no re-runnable coordinates — never emit a broken "…|None"
            # evidence address (self-audit 2026-07-02).
            #
            # `opponent` is a SPECIES, but a ranked species expands into one cell per real build, so
            # the evidence_id alone cannot say WHICH build produced the fact. Carry the variant here
            # exactly as the defensive bucket below already does: an M-5 Kingambit splits into five
            # (item, ability) archetypes and Mega Garchomp's Earthquake OHKOs only the 15%-coverage
            # Focus Sash one, so a recompute that re-resolved "Kingambit" to its modal build read the
            # same coordinate as a 2HKO and called an honest claim fabricated (audit 2026-08-06).
            # `opponent_is_modal` rides along for the same reason it does defensively: a fact about a
            # minority build must not read as an equal-weight fact about the species.
            variant = {k: c[k] for k in ("opponent_variant", "opponent_is_modal") if k in c}
            if off.get("ko_guaranteed") == 1 and off.get("move"):
                we_g.setdefault(opp, []).append(
                    {"member": me, "move": off["move"],
                     "evidence_id": evidence_id(fmt, calc_me, opp, off["move"]), **variant})
            elif off.get("ko_possible") == 1 and off.get("move"):
                we_p.setdefault(opp, []).append(
                    {"member": me, "move": off["move"],
                     "evidence_id": evidence_id(fmt, calc_me, opp, off["move"]), **variant})
            worst = (c.get("defense_damage") or {}).get("worst") or {}
            if worst.get("ko_guaranteed") == 1 and worst.get("move"):
                them_g.setdefault(opp, []).append(
                    {"member": me, "move": worst["move"],
                     "evidence_id": evidence_id(fmt, opp, calc_me, worst["move"]),
                     "meta_position": c.get("usage_rank"),
                     "opponent_variant": c.get("opponent_variant"),
                     "opponent_is_modal": c.get("opponent_is_modal")})
            faster = (c.get("speed") or {}).get("faster")
            if faster in ("member", "opponent", "tie"):
                pairs += 1
                if faster == "member":
                    we_faster += 1
    return {
        "top_k": mres.get("top_k") or 0,
        "opponent_scope": mres.get("opponent_scope"),
        "opponents_we_ohko_guaranteed": {k: v for k, v in sorted(we_g.items())},
        "opponents_we_ohko_possible": {k: v for k, v in sorted(we_p.items())},
        "opponents_with_guaranteed_ohko_on_us": {k: v for k, v in sorted(them_g.items())},
        "speed_pairs_member_faster": we_faster,
        "speed_pairs_total": pairs,
        "check_coverage": _check_digest(mres.get("check_coverage")),
        "confidence": mres.get("confidence"),
        "note": "counting facts over the matchup grid (retained observed builds — see matchup for the "
                "full band/caveats); each extreme carries a re-runnable evidence_id.",
    }


def _check_digest(cov: dict[str, Any] | None) -> dict[str, Any] | None:
    """The derived CHECK-grade coverage as SLATE facts: the grade-count distribution + the explicit
    hole/contested lists (opponents with no safe switch-in check / whose positive read setup-recovery
    undermines). Objective labels + counts, NEVER a score and NEVER a cross-candidate ranking — the AI
    reads them as gaps an archetype may accept (design §1/§17)."""
    if not isinstance(cov, dict) or not cov.get("by_opponent"):
        return None
    contested = []
    for row in cov.get("by_opponent", []):
        variants = [{"variant_id": v.get("variant_id"), "by": v.get("contested_by") or []}
                    for v in row.get("variants") or [] if v.get("contested_by")]
        if variants:
            contested.append({"opponent": row["opponent"], "variants": variants})
    return {
        "grade_distribution": cov.get("grade_distribution"),
        "by_opponent": compact_coverage(cov).get("by_opponent"),
        # pass the hole rows through wholesale — they are already a small facts-only label table, so a
        # projection here would silently drop any field the roll-up gains later (e.g. the §17 wall_no_ko
        # refinement) and quietly coarsen matchup_risk with no error.
        "holes": cov.get("holes") or [],
        "contested": contested,
        "note": "observed floor C1/C0 = a HOLE; `contested` names concrete retained variants whose "
                "positive read is undermined by DETECTED setup/recovery. Ordinal labels, no score.",
    }


_LOW_PREVALENCE_ABSENT_ROLE_FLAGS = {("single", "hazard_control")}


def _flags(profile: dict[str, Any], fmt: str | None = None) -> list[dict[str, Any]]:
    """Objective structural gaps from the shared profile vector — facts an archetype may accept."""
    flags: list[dict[str, Any]] = []
    off = profile.get("offense") or {}
    if off.get("hard_gap_defending_types"):
        flags.append({"aspect": "offense", "kind": "hard_gap",
                      "types": off["hard_gap_defending_types"],
                      "confirmed": off.get("gaps_confirmed")})
    for conc in (profile.get("defense") or {}).get("weakness_concentration") or []:
        flags.append({"aspect": "defense", "kind": "weakness_concentration",
                      "type": conc["type"], "weak_count": conc["weak_count"]})
    if (profile.get("speed_control_mode") or {}).get("modes") == ["none"]:
        flags.append({"aspect": "speed", "kind": "no_speed_control"})
    absent = [t for t, rc in (profile.get("role_composition") or {}).items()
              if rc.get("count") == 0
              and ((fmt, t) not in _LOW_PREVALENCE_ABSENT_ROLE_FLAGS)]
    if absent:
        flags.append({"aspect": "roles", "kind": "not_detected", "tags": sorted(absent)})
    return flags


def _avoid_items_check(ctx: dict[str, Any], team_items: list[str]) -> dict[str, Any] | None:
    """avoid_items is a HARD user constraint on held items — a candidate carrying one is eliminated
    exactly like an avoided species (external integration audit 2026-07-03: observed excluded by
    item while slate silently passed the same context — the two gates must agree)."""
    wanted = [i for i in (ctx.get("avoid_items") or []) if i]
    if not wanted:
        return None
    held = set(team_items)
    hits = sorted(i for i in wanted if i in held)
    return {"satisfied": not hits, "violations": hits}


def _constraints(ctx: dict[str, Any], team_species: list[str],
                 team_items: list[str], *, identity_complete: bool = True,
                 identity_basis: str = "registered_species") -> dict[str, Any]:
    """Set-level constraint facts vs the (canonicalized) context. owned/owned_only legality is
    validate's verdict (base-species-aware there); these are the pure membership checks.

    Membership uses exact actual forms supplied by the caller. A base holding its own stone counts
    as that Mega; ordinary, X, Y and Z remain distinct. Reported names retain the context spelling."""
    forms = set(team_species)
    locked_missing = sorted(n for n in (ctx.get("locked") or []) if n not in forms)
    avoid_hits = sorted(n for n in (ctx.get("avoid_species") or ctx.get("avoid") or [])
                        if n in forms)
    prefer = [n for n in (ctx.get("prefer") or []) if n]
    prefer_present = sorted(n for n in prefer if n in forms)
    # Soft preferences expose exact membership facts without eliminating candidates.
    soft = [n for n in (ctx.get("avoid_soft") or []) if n]
    # Mirror avoid's coverage: avoid_soft may name a species OR a held item (schema.md / FIELD_STATUS),
    # so echo BOTH kinds present on the team as a fact — a soft-avoided item was previously invisible in
    # the matrix while the field doc claimed species/items parity (audit 2026-07-06).
    held_items = set(team_items)
    soft_present = sorted(n for n in soft if n in forms or n in held_items)
    return {
        "identity_basis": identity_basis, "identity_complete": identity_complete,
        "locked": {"satisfied": not locked_missing and identity_complete, "missing": locked_missing},
        "avoid": {"satisfied": not avoid_hits and identity_complete, "violations": avoid_hits},
        "avoid_items": _avoid_items_check(ctx, team_items),
        "avoid_soft": ({"present": soft_present} if soft else None),
        "prefer": {"present": prefer_present,
                   "absent": sorted(n for n in prefer if n not in forms)},
        "owned_only": {"delegated_to": "validate (base-species-aware)"} if ctx.get("owned_only") else None,
    }


def verify_frame_output(frame_output: Any, audit_fp: str | None,
                        battery_fmt: str) -> dict[str, Any]:
    """Verify a saved frame output before its skeletons are trusted for the binding. Returns
    {ok:True, skeletons_by_id} or {ok:False, refused:{code,reason}}. Tamper-evident: the frame_receipt
    fingerprint is recomputed from the skeletons (an edited cluster — widened to sneak an ungrounded set
    past the binding — will not reproduce it), and its audit fingerprint must continue THIS chain."""
    if isinstance(frame_output, dict) and frame_output.get("view") == "summary":
        return {"ok": False, "refused": {"code": "summary_frame_output",
                "reason": "frame summary is display-only; use the full artifact saved by --out"}}
    if not isinstance(frame_output, dict) or not isinstance(frame_output.get("skeletons"), list):
        return {"ok": False, "refused": {"code": "bad_frame_output",
                "reason": "--frame-output must be a saved `frame` output {skeletons, frame_receipt, ...}"}}
    receipt = frame_output.get("frame_receipt")
    provided = receipt.get("fingerprint") if isinstance(receipt, dict) else None
    if not provided:
        return {"ok": False, "refused": {"code": "missing_frame_receipt",
                "reason": "the frame output carries no frame_receipt — re-run `team.py frame`"}}
    if receipt.get("identity_basis") != "dex_battle_form":
        return {"ok": False, "refused": {"code": "receipt_upgrade_required",
                "reason": "saved frame predates exact battle-form grounding; re-run frame and slate-evaluate"}}
    fmt = frame_output.get("format")
    if fmt != battery_fmt:
        return {"ok": False, "refused": {"code": "frame_format_mismatch",
                "reason": f"frame output is {fmt!r} but this slate's battery is {battery_fmt!r} — "
                          "the two metagames are never mixed"}}
    expected = _frame_fingerprint(receipt.get("audit_fingerprint"), frame_output["skeletons"],
                                  frame_output.get("anchor"), fmt)
    if expected != provided:
        return {"ok": False, "refused": {"code": "frame_output_inconsistent",
                "reason": "the frame output does not reproduce its own receipt fingerprint — it was "
                          "edited after the run (or paired with a different frame). Re-run `frame`.",
                "expected_fingerprint": expected, "provided_fingerprint": provided}}
    if receipt.get("audit_fingerprint") != audit_fp:
        return {"ok": False, "refused": {"code": "frame_audit_mismatch",
                "reason": "the frame was built against a different context than this slate — its "
                          "audit fingerprint does not match. Re-run `frame` on this context.",
                "expected_fingerprint": audit_fp,
                "provided_fingerprint": receipt.get("audit_fingerprint")}}
    by_id = {s.get("frame_id"): s for s in frame_output["skeletons"] if isinstance(s, dict)}
    return {"ok": True, "skeletons_by_id": by_id, "frame_fingerprint": provided}


def _pair_in_clusters(item: Any, ability: Any, clusters: list[dict[str, Any]]) -> bool:
    """A member's (item, ability) is grounded iff it matches some repset (item,ability) cluster — the
    archetype signature. moves/nature/spread are set_guidance (soft), never part of this check."""
    return any(c.get("item") == item and c.get("ability") == ability
               for c in (clusters or []) if isinstance(c, dict))


def frame_binding(team_c: dict[str, Any], binding: Any, skeletons_by_id: dict[str, Any],
                  repset_fn: Callable[[str], list[dict]] | None) -> tuple[dict[str, Any], list[str]]:
    """Bind ONE candidate against the frame it declares. Returns (binding_info, red_reasons) — red
    reasons feed the cheap-stage elimination (design §19.10: an ungrounded core-bearer with no
    declared basis does not bill the expensive battery). Per-candidate contract is minimal — a single
    `frame_id` (core-bearers are then DERIVED as members ∈ that frame's core_candidates at the CORE
    tier; the frame's `recurring-partner` tier binds softly and is reported under
    `recurring_partners`), plus an optional `off_meta`/`deviations` escape."""
    members = {m.get("species"): m for m in team_c.get("pokemon", []) if m.get("species")}
    b = binding if isinstance(binding, dict) else {}
    frame_id = b.get("frame_id")
    acked = set(b.get("off_meta") or []) | {d.get("species") for d in (b.get("deviations") or [])
                                            if isinstance(d, dict)}
    reasons_by_sp = {d.get("species"): d.get("reason") for d in (b.get("deviations") or [])
                     if isinstance(d, dict)}
    info: dict[str, Any] = {"frame_id": frame_id, "core_bearers": [], "recurring_partners": [],
                            "deviations": [], "advisories": []}
    red: list[str] = []
    if b.get("off_meta_build"):
        info["off_meta_build"] = True                # a declared off-meta build: no core-bearer binding
        return info, red
    if not frame_id:
        info["unbound"] = True
        return info, ["frame binding active but this candidate declares no frame_id — add the frame_id "
                      "it builds on (from the frame output), or off_meta_build:true for a deliberate "
                      "off-meta build (frame_bindings[i] describes teams[i])"]
    skel = skeletons_by_id.get(frame_id)
    if not skel:
        info["unknown_frame_id"] = True
        return info, [f"frame_id {frame_id!r} is not present in the verified frame output — use one "
                      "of the frame output's frame_id values, or off_meta_build:true for a deliberate "
                      "off-meta build"]
    core = {cc.get("species"): cc for cc in (skel.get("core_candidates") or [])
            if isinstance(cc, dict)}
    for sp, m in members.items():
        item, ability = m.get("item"), m.get("ability")
        # Both sides use the battle-form view; another form cannot borrow this core's grounding.
        core_key = sp if sp in core else None
        if core_key is not None:
            tier = core[core_key].get("role")
            # A `recurring-partner` candidate (frame's 0.3-0.5 support band) is NOT the group's
            # backbone: it is offered WITH its real repset grounding so the set is available, but it
            # was never a commitment to bind against. Leaving its clusters is reported (and must be
            # disclosed at answer-audit) and never eliminates — taxing that band at the core rate
            # would price a substitution above going with the crowd, which is the behaviour frame
            # exists to counteract.
            # A fallback skeleton (frame's ladder for an anchor no real team carries) was never
            # observed WITH the anchor, so none of its lent members is a commitment either.
            soft = tier == "recurring-partner" or (tier != "anchor" and bool(skel.get("fallback")))
            info["recurring_partners" if soft else "core_bearers"].append(sp)
            clusters = ((core[core_key].get("grounding") or {}).get("clusters")) or []
            if clusters and not _pair_in_clusters(item, ability, clusters):
                ack = sp in acked
                info["deviations"].append({
                    "species": sp, "declared": {"item": item, "ability": ability},
                    "grounded_clusters": clusters, "acknowledged": ack, "tier": tier,
                    "reason": reasons_by_sp.get(sp),
                    "severity": "yellow" if (ack or soft) else "red"})
                if not (ack or soft):
                    red.append(f"{sp}: item={item!r}/ability={ability!r} is outside this core "
                               f"candidate's repset clusters {clusters} and no deviation rationale / "
                               "off_meta was declared (core-bearer red deviation)")
        elif repset_fn and item is not None and sp not in acked:
            # Off-frame / substitute member: advisory own-repset check (never eliminates — the AI may
            # substitute a core member, but a substitute wants its OWN repset basis; §19.10 correction 3).
            own = repset_fn(sp)
            if own and not _pair_in_clusters(item, ability, own):
                info["advisories"].append({
                    "species": sp, "declared": {"item": item, "ability": ability},
                    "own_clusters": own,
                    "note": "off-frame member's (item,ability) is outside its OWN repset clusters — "
                            "bind a basis or declare off_meta; disclosed, never eliminated"})
    return info, red


def _declared_mega_deviation(binding: Any) -> tuple[bool, Any]:
    """Return whether frame_bindings[i].mega_deviation carries a substantive declared basis."""
    b = binding if isinstance(binding, dict) else {}
    raw = b.get("mega_deviation")
    if isinstance(raw, str):
        return bool(raw.strip()), raw
    if isinstance(raw, dict):
        reason = raw.get("reason")
        return isinstance(reason, str) and bool(reason.strip()), raw
    return False, raw


def _mega_reference(skeleton: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(skeleton, dict):
        return None, None
    facts = (skeleton or {}).get("observed_facts") or {}
    ref = facts.get("mega_registration_reference")
    if isinstance(ref, dict) and isinstance(ref.get("distribution"), dict):
        return ref["distribution"], ref.get("basis")
    # Back-compat for a verified frame produced before mega_registration_reference existed.
    legacy = facts.get("observed_mega_slots")
    return ((legacy, "frame_group_legacy") if isinstance(legacy, dict) else (None, None))


def _mega_slate_summary(ctx: dict[str, Any], candidates: list[dict[str, Any]],
                        survivors: list[int]) -> dict[str, Any]:
    """Candidate-set coverage of observed modal registration lanes — a fact, never a winner."""
    posture = ctx.get("mega_posture") or "environment"
    if posture != "environment":
        return {"status": "explicit_user_posture", "mega_posture": posture,
                "assessed_survivors": survivors, "modal_lane_indices": []}
    if ctx.get("meta_conformance") == "off_meta":
        return {"status": "off_meta_view", "mega_posture": posture,
                "assessed_survivors": survivors, "modal_lane_indices": []}
    assessed = [i for i in survivors
                if (candidates[i].get("mega_registration_assessment") or {}).get("reliable")
                and (candidates[i].get("mega_registration_assessment") or {}).get("source")
                == "observed_distribution"]
    modal = [i for i in assessed
             if (candidates[i].get("mega_registration_assessment") or {}).get("relation") == "modal"]
    if not assessed:
        status = "unassessed"
    else:
        status = "covered" if modal else "missing_modal_lane"
    return {
        "status": status,
        "mega_posture": posture,
        "assessed_survivors": assessed,
        "modal_lane_indices": modal,
        "note": "whether at least one survivor is modal relative to its own bound frame's observed "
                "Mega-registration distribution; "
                "a descriptive conformance gate, never a strength ranking",
    }


def evaluate_slate(slate: dict[str, Any], *,
                   recompute_receipt_fn: Callable[[dict], str],
                   check_team_fn: Callable[[dict], list],
                   validate_fn: Callable[[dict], dict],
                   profile_fn: Callable[[dict], dict],
                   matchup_fn: Callable[[dict], dict | None],
                   selection_fn: Callable[[dict, dict | None, str | None], dict] | None = None,
                   canon_team_fn: Callable[[dict], dict] | None = None,
                   battle_team_fn: Callable[[dict], dict] | None = None,
                   canon_binding_fn: Callable | None = None,
                   constraints_ctx: dict[str, Any] | None = None,
                   fmt: str | None = None,
                   library_copy_fn: Callable[[dict], dict | None] | None = None,
                   frame_output: dict[str, Any] | None = None,
                   repset_fn: Callable[[str], list[dict]] | None = None) -> dict[str, Any]:
    """The gate. Pure given the injected stages; the CLI wires the real siblings.

    `canon_team_fn` canonicalizes a candidate ONCE — its result feeds species extraction, validate,
    profile AND the battery (one dex round-trip per candidate, and constraint membership compares
    canonical names, symmetric with the team side — self-audit 2026-07-02: three separate re-canons
    plus raw-name set ops falsely eliminated alias-named locks). `constraints_ctx` is the
    CANONICALIZED context view for the membership checks; the RAW slate context stays the receipt's
    binding target (the fingerprint is over the context as written).

    Refusals (`refused` set, CLI exits 2): slate not a dict / no teams; audit_receipt not the full
    receipt object (a bare fingerprint string is the predictable mispass — the context-audit md
    summary shows only the fingerprint; external session 2026-07-07); missing audit_receipt;
    receipt fingerprint not matching a recompute over THIS context (the chain's whole point)."""
    shape_err = slate_shape_error(slate)
    if shape_err:
        return {"kind": "slate-evaluate", "refused": shape_err}
    ctx = slate.get("context") if isinstance(slate.get("context"), dict) else {}
    receipt = slate.get("audit_receipt")
    if receipt is not None and not isinstance(receipt, dict):
        return {"kind": "slate-evaluate",
                "refused": {"code": "bad_audit_receipt",
                            "reason": "audit_receipt must be the FULL receipt object from "
                                      "context-audit ({kind, fingerprint, audited_at, ...}), not a "
                                      "bare fingerprint string — copy the audit_receipt object from "
                                      "context-audit's --format json output."}}
    provided = (receipt or {}).get("fingerprint")
    if not provided:
        return {"kind": "slate-evaluate",
                "refused": {"code": "missing_audit_receipt",
                            "reason": "run `context-audit` on this context first and pass its "
                                      "audit_receipt — the slate gate consumes the chain's first link."}}
    expected = recompute_receipt_fn(ctx)
    if provided != expected:
        return {"kind": "slate-evaluate",
                "refused": {"code": "audit_receipt_mismatch",
                            "reason": "the audit_receipt does not match a context-audit recompute of "
                                      "THIS context — the context changed since it was audited (or the "
                                      "receipt belongs to another). Re-run context-audit.",
                            "expected_fingerprint": expected, "provided_fingerprint": provided}}

    cctx = constraints_ctx if constraints_ctx is not None else ctx
    battery_fmt = fmt or ctx.get("format") or "single"

    # --- P4.5 frame binding (build flows): --frame-output activates it. Verify the frame receipt
    # (tamper-evident + chain continuity) BEFORE trusting its skeletons; a broken chain refuses the
    # whole run (the chain's point), a per-candidate binding problem only eliminates that candidate. ---
    skeletons_by_id: dict[str, Any] = {}
    frame_fp: str | None = None
    frame_active = frame_output is not None
    if ctx.get("frame_required") is True and not frame_active:
        return {"kind": "slate-evaluate",
                "refused": {"code": "missing_frame_output",
                            "reason": "this build-flow context declares frame_required:true, so "
                                      "slate-evaluate must be run with --frame-output from team.py "
                                      "frame. Either pass the saved frame output, or remove "
                                      "frame_required only for a deliberate non-build slate."}}
    if frame_active:
        fver = verify_frame_output(frame_output, provided, battery_fmt)
        if not fver["ok"]:
            return {"kind": "slate-evaluate", "refused": fver["refused"]}
        skeletons_by_id = fver["skeletons_by_id"]
        frame_fp = fver["frame_fingerprint"]
    bindings = slate.get("frame_bindings")
    bindings = bindings if isinstance(bindings, list) else []

    candidates: list[dict[str, Any]] = []
    canon_teams: dict[int, dict[str, Any]] = {}
    battle_teams: dict[int, dict[str, Any]] = {}
    survivors: list[int] = []
    for i, team in enumerate(slate["teams"]):
        entry: dict[str, Any] = {"index": i}
        contract_errors = check_team_fn(team) if isinstance(team, dict) else [
            {"code": "E_TYPE", "path": "", "message": "candidate is not a team-json object",
             "severity": "error"}]
        entry["contract_errors"] = contract_errors
        fatal = any(e.get("severity") == "error" for e in contract_errors)
        if fatal:
            entry["eliminated"] = {"stage": "contract",
                                   "reasons": ["candidate failed the team-json contract"]}
            candidates.append(entry)
            continue
        res = canon_team_fn(team) if canon_team_fn else team
        team_c, name_flags = res if isinstance(res, tuple) else (res, [])
        if name_flags:
            # fuzzy species corrections are NEVER silent (same contract as every other operator's
            # name_resolution; external audit 2026-07-02: slate dropped the flags).
            entry["name_resolution"] = name_flags
        canon_teams[i] = team_c
        species = [m.get("species") for m in team_c.get("pokemon", []) if m.get("species")]
        items = [m.get("item") for m in team_c.get("pokemon", []) if m.get("item")]
        entry["team_size"] = len(species)
        entry["legality"] = validate_fn(team_c)
        battle_team = battle_team_fn(team_c) if battle_team_fn else team_c
        battle_teams[i] = battle_team
        battle_members = battle_team.get("pokemon") or []
        complete = not any(m.get("_identity_unknown") or not m.get("species") for m in battle_members)
        entry["constraint_satisfaction"] = _constraints(
            cctx, [m.get("species") for m in battle_members if m.get("species")], items,
            identity_complete=complete, identity_basis="dex_battle_form" if battle_team_fn else "registered_species")
        if library_copy_fn:
            # Library guardrail (§A.D), the DETECT half: a candidate that IS a stored team (or the
            # same six species) is surfaced as a fact here; the answer-audit makes recommending a
            # verbatim copy a violation.
            overlap = library_copy_fn(team_c)
            if overlap:
                entry["library_overlap"] = overlap
        entry["structural_profile"] = profile_fn(team_c)
        entry["mega_plan"] = mega_plan_from_profile(entry["structural_profile"])
        entry["flags"] = _flags(entry["structural_profile"], battery_fmt)
        inferred = [m.get("species") for m in team_c.get("pokemon", [])
                    if m.get("completeness") == "inferred_set"]
        if inferred:
            entry["warnings"] = [{
                "code": "inferred_set_profile_degraded", "members": inferred,
                "message": "inferred_set marks guessed source facts: moves and roles are not "
                           "authoritative, so structural gaps are unconfirmed. For a fully specified "
                           "AI-authored proposal, omit completeness (or use null); keep inferred_set "
                           "for genuinely unknown opponent/source sets. Damage and legality checks "
                           "still inspect the supplied fields.",
            }]
        reasons = []
        tfmt = (team_c.get("format") or "").lower()
        if tfmt and tfmt != battery_fmt:
            # Metagames are never mixed: a candidate declaring another format must not be silently
            # profiled as one game and battery-tested as the other (external audit 2026-07-02).
            reasons.append(f"declares format {tfmt!r} but this slate's battery is {battery_fmt!r} — "
                           "metagames are never mixed")
        if entry["legality"].get("status") == "invalid":
            reasons.append("legality: invalid (see legality.errors)")
        if entry["constraint_satisfaction"]["locked"]["missing"]:
            reasons.append("locked members missing: "
                           + ", ".join(entry["constraint_satisfaction"]["locked"]["missing"]))
        if not complete:
            reasons.append("battle-form identity could not be proved")
        if entry["constraint_satisfaction"]["avoid"]["violations"]:
            reasons.append("avoided species present: "
                           + ", ".join(entry["constraint_satisfaction"]["avoid"]["violations"]))
        ai_check = entry["constraint_satisfaction"].get("avoid_items")
        if ai_check and not ai_check["satisfied"]:
            reasons.append("avoided items held: " + ", ".join(ai_check["violations"]))
        binding = bindings[i] if i < len(bindings) else None
        if canon_binding_fn and isinstance(binding, dict):
            binding, flags, errors = canon_binding_fn(binding)
            if flags:
                entry["binding_name_resolution"] = flags
            if errors:
                entry["binding_name_errors"] = errors
                reasons.append("frame binding contains unresolved or ambiguous species names")
        bound_skeleton: dict[str, Any] | None = None
        if frame_active:
            # P4.5: bind this candidate against the frame it declares. A RED core-bearer deviation
            # (ungrounded set, no rationale/off_meta) eliminates in the funnel (design §19.10 correction:
            # 严重时淘汰); yellow deviations + off-frame advisories survive and are surfaced for the
            # answer-audit disclosure check.
            binfo, red = frame_binding(battle_team, binding,
                                       skeletons_by_id, repset_fn)
            entry["frame_binding"] = binfo
            reasons += red
            bound_skeleton = skeletons_by_id.get(binfo.get("frame_id"))

        deviation_declared, deviation_payload = _declared_mega_deviation(binding)
        distribution, reference_basis = _mega_reference(bound_skeleton)
        effective_conformance = ("off_meta" if isinstance(binding, dict) and binding.get("off_meta_build")
                                 else ctx.get("meta_conformance"))
        assessment = assess_registration(
            entry["mega_plan"]["registered_mega_count"], distribution,
            mega_posture=ctx.get("mega_posture"), meta_conformance=effective_conformance,
            deviation_declared=deviation_declared)
        assessment["reference_basis"] = reference_basis
        if deviation_payload is not None:
            assessment["declared_deviation"] = deviation_payload
        entry["mega_registration_assessment"] = assessment
        if assessment.get("hard_conflict"):
            if assessment.get("source") == "user_posture":
                reasons.append(
                    f"registered Mega count {assessment['registered_mega_count']} conflicts with explicit "
                    f"mega_posture={assessment.get('mega_posture')!r}")
            else:
                reasons.append(
                    f"registered Mega bucket {assessment['bucket']!r} is an observed "
                    f"{assessment.get('relation')} lane (share={assessment.get('observed_share')}) for "
                    "the bound frame and no frame_bindings[i].mega_deviation reason was declared")
        if reasons:
            # Funnel: a definitive cheap-stage failure never bills the expensive battery.
            entry["eliminated"] = {"stage": "cheap", "reasons": reasons}
        else:
            survivors.append(i)
        candidates.append(entry)

    for i in survivors:
        mres = matchup_fn(canon_teams[i])
        candidates[i]["matchup_risk"] = (_matchup_digest(mres, battery_fmt) if mres
                                         else {"skipped": "matchup battery unavailable (ncp/meta down)"})
        if selection_fn is not None:
            candidates[i]["selection"] = selection_fn(
                canon_teams[i], mres, (candidates[i].get("legality") or {}).get("status"))

    mega_registration_slate = _mega_slate_summary(ctx, candidates, survivors)
    for c in candidates:
        c["comparison_facts"] = slate_comparison.candidate_facts(
            c, battle_teams.get(c["index"], {}), cctx.get("goals") or [])
    comparison = slate_comparison.compare(candidates, survivors)

    grid = []
    for c in candidates:
        cs = c.get("constraint_satisfaction") or {}
        mr = c.get("matchup_risk") or {}
        # When the battery was skipped (ncp/meta down) the KO counts are UNKNOWN, not zero — emit None
        # so sorting the grid by a KO column can't rank a skipped candidate as a genuine 0-KO team,
        # matching speed_pairs_member_faster which is already None here (audit 2026-07-06).
        battery_skipped = "skipped" in mr
        grid.append({
            "index": c["index"],
            "eliminated": bool(c.get("eliminated")),
            "legality": (c.get("legality") or {}).get("status"),
            "locked_ok": (cs.get("locked") or {}).get("satisfied"),
            "avoid_ok": (cs.get("avoid") or {}).get("satisfied"),
            "prefer_present": len((cs.get("prefer") or {}).get("present") or []),
            "registered_mega_count": (c.get("mega_plan") or {}).get("registered_mega_count"),
            "mega_registration_relation": (c.get("mega_registration_assessment") or {}).get("relation"),
            "hard_gap_types": len(next((f["types"] for f in c.get("flags") or []
                                        if f["kind"] == "hard_gap"), [])),
            "weakness_concentrations": sum(1 for f in c.get("flags") or []
                                           if f["kind"] == "weakness_concentration"),
            "we_ohko_guaranteed": None if battery_skipped else len(mr.get("opponents_we_ohko_guaranteed") or {}),
            "guaranteed_ohko_on_us": None if battery_skipped else len(mr.get("opponents_with_guaranteed_ohko_on_us") or {}),
            "speed_pairs_member_faster": mr.get("speed_pairs_member_faster"),
        })

    # The receipt binds the WHOLE evaluation, battery facts included: the same slate evaluated with
    # a different top-K (or a changed battery result) must re-fingerprint, or P6 could bind a claim
    # to a receipt whose matchup facts it never saw (external audit 2026-07-02).
    candidate_facts = {
        i: {"mega_plan": candidates[i].get("mega_plan"),
            "mega_registration_assessment": candidates[i].get("mega_registration_assessment"),
            "selection": candidates[i].get("selection")}
        for i in survivors}
    candidate_facts["_slate"] = mega_registration_slate
    candidate_facts["_comparison"] = comparison
    for i in survivors:
        candidate_facts[i].update(constraint_satisfaction=candidates[i].get("constraint_satisfaction"),
                                  frame_binding=candidates[i].get("frame_binding"),
                                  comparison_facts=candidates[i].get("comparison_facts"),
                                  library_overlap=candidates[i].get("library_overlap"))
    fingerprint = slate_fingerprint(provided, slate["teams"], survivors, battery_fmt,
                                    {i: candidates[i].get("matchup_risk") for i in survivors},
                                    frame_fp, candidate_facts)
    return {
        "kind": "slate-evaluate",
        "format": battery_fmt,
        "frame_bound": frame_active,
        "candidate_count": len(candidates),
        "survivors": survivors,
        "candidates": candidates,
        "grid": grid,
        "mega_registration_slate": mega_registration_slate,
        "comparison": comparison,
        "evidence_id_grammar": EVIDENCE_ID_GRAMMAR,
        "slate_receipt": {
            "kind": "slate_receipt",
            "fingerprint": fingerprint,   # audit fp + team hashes + survivors + format + battery digest (+ frame fp)
            "audited_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "audit_fingerprint": provided,
            "frame_fingerprint": frame_fp,   # P4.5 chain link (None on a non-build slate); answer-audit re-binds it
            "candidate_facts_bound": True,
            "constraint_facts_bound": True,
            "comparison_facts_bound": True,
            "format": battery_fmt,
            "candidates": len(candidates), "survivors": len(survivors),
        },
        "notes": [
            "INPUT ORDER preserved; no aggregate column, no winner — sort the grid by whichever "
            "column matters to the user's intent and argue the trade-offs per candidate.",
            "flags are OBJECTIVE gaps, not defects: an archetype may deliberately accept one (a "
            "Trick Room team accepts slow speed_pairs) — eliminating on a flag is the READER's "
            "judgment, only legality/constraint failures eliminate mechanically.",
            "the expensive battery ran ONLY for survivors (funnel) — eliminated candidates carry "
            "their reasons and no matchup_risk.",
            "quantitative extremes carry evidence_id (" + EVIDENCE_ID_GRAMMAR + "); the answer-audit "
            "binds claims to these.",
            "Mega registration is compared with the verified frame's observed distribution. Reliable "
            "minority/rare lanes need frame_bindings[i].mega_deviation under the proven view; the slate "
            "also reports whether survivors cover an observed modal lane. This is conformance evidence, "
            "not a legality rule or strength score.",
        ] + ([
            "FRAME-BOUND (build flow): each candidate's core-bearer sets were checked against the "
            "frame's repset clusters. A RED core-bearer deviation (ungrounded set, no rationale/"
            "off_meta) eliminated the candidate; YELLOW deviations + off-frame advisories are in "
            "candidates[].frame_binding and MUST be disclosed at answer-audit. set_guidance "
            "(moves/nature/spread) is NOT checked here — only (item,ability) cluster membership.",
        ] if frame_active else []),
    }


def project_slate(result: dict[str, Any], view: str = "full") -> dict[str, Any]:
    """Compact the P5 slate output for a DISPLAY consumer (the local web panel's SlateCard).

    view='summary' drops the two payload-heavy surfaces the panel never reads — the top-level `grid`
    (the full per-cell battery) and each candidate's per-opponent evidence lists inside `matchup_risk`
    (opponents_we_ohko_*/opponents_with_guaranteed_ohko_on_us and check_coverage.holes[].variants) —
    while KEEPING survivors, each candidate's legality/flags/mega/structural facts, the
    check_coverage.grade_distribution the card renders, mega_registration_slate, and slate_receipt.
    A 3 MB+ full slate exceeds the bridge's 2 MiB body gate; the summary lands well under it.

    DISPLAY ONLY: the receipt is preserved so the panel can show it, but answer-audit must receive the
    FULL output (not this) — its claim recompute re-hashes the untrimmed matchup_risk. view='full'
    (default) returns the result unchanged, so nothing that omits the flag is affected."""
    if view != "summary" or not isinstance(result, dict):
        return result
    out = dict(result)
    out.pop("grid", None)
    trimmed: list[Any] = []
    for cand in out.get("candidates", []) or []:
        if not isinstance(cand, dict):
            trimmed.append(cand)
            continue
        cand = dict(cand)
        mr = cand.get("matchup_risk")
        if isinstance(mr, dict):
            mr = {k: v for k, v in mr.items()
                  if k not in ("opponents_we_ohko_guaranteed", "opponents_we_ohko_possible",
                               "opponents_with_guaranteed_ohko_on_us")}
            cc = mr.get("check_coverage")
            if isinstance(cc, dict):
                from team_views import coverage
                cc = coverage(cc)
                # Existing Console cards read the representative aliases; floor remains separate.
                cc["holes"] = [{**h, "grade": (h.get("representative") or {}).get("grade"),
                                "best_by": (h.get("representative") or {}).get("best_by")}
                               for h in cc.get("holes") or []]
                mr["check_coverage"] = cc
            cand["matchup_risk"] = mr
        sel = cand.get("selection")
        if isinstance(sel, dict) and isinstance(sel.get("combos"), list):
            sel = dict(sel)
            sel["combos_count"] = len(sel["combos"])
            sel.pop("combos", None)      # 6-pick-N lineup enumeration (the summary's real bulk) — the
            cand["selection"] = sel      # panel renders none of it; keep the count + mega_routes/notes
        trimmed.append(cand)
    out["candidates"] = trimmed
    out["view"] = "summary"
    out["projection"] = {"display_only": True, "omitted": [
        {"path": "/grid", "count": len(result.get("grid") or [])},
        {"path": "/candidates/*/matchup_risk/opponents_*", "replacement": "full artifact carries per-opponent damage evidence"},
        {"path": "/candidates/*/matchup_risk/check_coverage/*/variants", "replacement": "variant_count and exceptional variant_qualifiers; observed floors unchanged"},
        {"path": "/candidates/*/selection/combos", "replacement": "combos_count and Mega routes"}]}
    return out
