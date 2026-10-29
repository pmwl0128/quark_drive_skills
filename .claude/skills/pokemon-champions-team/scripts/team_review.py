"""One completeness-gated battery for diagnosis, selection and explicit copy edits."""
from __future__ import annotations

from copy import deepcopy

from canonhash import content_hash
from stat_keys import canonical_spreads
import team_i18n as i18n
import team_views
from matchup import project_matchup
from replace_impact import _defense_diff, _offense_diff, _roles_diff, _speed_diff


CHANGE_FIELDS = {"species", "item", "ability", "moves", "nature", "spread"}


def requirements(team: dict) -> list[dict]:
    result = []
    for slot, member in enumerate(team.get("pokemon") or []):
        missing = [key for key in ("species", "moves", "ability", "nature") if not member.get(key)]
        if not isinstance(member.get("spread"), dict) or not member["spread"]:
            missing.append("spread")
        if member.get("completeness") not in (None, "", "observed_full_set", "extracted_set"):
            missing.append("trusted_set")
        result.append({"slot": slot, "species": member.get("species"), "missing_fields": missing})
    return result


def seal(result: dict) -> dict:
    out = i18n.jsonify(canonical_spreads(result))
    out["receipt"] = {"kind": out["kind"] + "_receipt", "fingerprint": content_hash(out, 32)}
    return out


def verify(result: dict) -> bool:
    if not isinstance(result, dict) or result.get("view") == "summary":
        return False
    receipt = result.get("receipt")
    return (isinstance(receipt, dict) and receipt.get("kind") == result.get("kind", "") + "_receipt"
            and receipt.get("fingerprint") == content_hash({k: v for k, v in result.items() if k != "receipt"}, 32))


def check_focus(focus: dict) -> None:
    if not isinstance(focus, dict) or set(focus) - {"opponents", "members"}:
        raise ValueError("focus accepts opponents:[species] and members:[species|zero-based slot]")
    for key in ("opponents", "members"):
        values = focus.get(key, [])
        if not isinstance(values, list) or any(not (isinstance(v, str) and v.strip()) and
                not (key == "members" and type(v) is int and 0 <= v < 6) for v in values):
            raise ValueError("invalid focus targets")


def evidence(report: dict) -> list[dict]:
    """Three semantic coordinates, scoped to one full review by its outer receipt."""
    match = report.get("matchup") or {}
    entries = []
    for row in (match.get("check_coverage") or {}).get("by_opponent") or []:
        ref = {"scope": "team_floor", "opponent": row["opponent"]}
        entries.append({"ref": ref, "fact": deepcopy(row)})
        for variant in row.get("variants") or []:
            entries.append({"ref": {**ref, "scope": "opponent_variant", "variant_id": variant["variant_id"]},
                "fact": {"opponent": row["opponent"], "variant": deepcopy(variant),
                         "set": deepcopy((match.get("opponent_sets") or {}).get(variant["variant_id"])),
                         "coverage": deepcopy(row.get("coverage")),
                         "calculation_complete": row.get("calculation_complete")}})
    for member in match.get("members") or []:
        for cell in member.get("cells") or []:
            if cell.get("opponent_variant"):
                entries.append({"ref": {"scope": "member_cell", "opponent": cell["opponent"],
                    "variant_id": cell["opponent_variant"], "member_slot": member["source_index"]},
                    "fact": {"member": member["member"], "member_run_form": member.get("member_run_form"),
                             "cell": deepcopy(cell)}})
    return entries


def targets(report: dict, focus: dict) -> dict:
    check_focus(focus)
    members = report["team"].get("pokemon") or []
    match = report.get("matchup") or {}
    grid = {r["source_index"]: r for r in match.get("members") or []}
    entries = evidence(report)
    mapped = []
    for target in focus.get("members") or []:
        slots = [i for i, member in enumerate(members) if target == i or target in
                 (member.get("species"), grid.get(i, {}).get("member_run_form"))]
        mapped.append({"target": target, "slots": slots,
                       "status": "matched" if len(slots) == 1 else "ambiguous" if slots else "not_on_team"})
    return {"members": mapped, "opponents": [{"target": name,
        "status": "mapped" if any(e["ref"]["opponent"] == name for e in entries) else "unavailable",
        "refs": [e["ref"] for e in entries if e["ref"]["opponent"] == name]}
        for name in focus.get("opponents") or []]}


def audit_claims(spec: dict, *, recompute_fn) -> dict:
    """Re-run the source report once, then check all scoped facts without upgrading a scope."""
    def refused(code):
        return {"kind": "review-claims", "pass": False, "violations": [{"code": code}], "recomputed": 0}
    if not isinstance(spec, dict) or not verify(spec.get("report")):
        return refused("invalid_full_review_receipt")
    report, claims = spec["report"], spec.get("claims")
    if report.get("kind") != "review" or report.get("status") not in {"ready", "partial"}:
        return refused("review_not_calculated")
    if not isinstance(claims, list) or not claims:
        return refused("claims_required")
    saved = {content_hash(e["ref"], 32): e for e in evidence(report)}
    bound, violations = [], []
    for i, claim in enumerate(claims):
        claim = claim if isinstance(claim, dict) else {}
        entry = saved.get(content_hash(claim.get("ref"), 32))
        if (not entry or claim.get("expect") != entry["fact"] or
                not isinstance(claim.get("claim"), str) or not claim["claim"].strip()):
            violations.append({"code": "unbound_claim", "claim": i})
        else:
            bound.append((i, entry))
    if violations:
        return {**refused("unbound_claim"), "violations": violations}
    fresh = recompute_fn(report)
    if fresh.get("environment") != report.get("environment"):
        return refused("environment_changed")
    current = {content_hash(e["ref"], 32): e for e in evidence(fresh)}
    for i, entry in bound:
        if current.get(content_hash(entry["ref"], 32)) != entry:
            violations.append({"code": "fact_did_not_reproduce", "claim": i})
    return {"kind": "review-claims", "pass": not violations, "violations": violations,
            "recomputed": len(bound), "report_fingerprint": report["receipt"]["fingerprint"],
            "note": "Exact scoped facts and qualifications reproduced; free-text interpretation is not certified. "
                    "A member cell is not a team floor. Proposal delivery still requires answer-audit."}


def review(team: dict, *, context: dict, environment: dict, validate_fn, diagnose_fn,
           matchup_fn, select_fn) -> dict:
    team = deepcopy(team)
    needed = requirements(team)
    missing = any(row["missing_fields"] for row in needed) or not team.get("pokemon")
    fmt = context.get("format") or team.get("format")
    legality = validate_fn(team)
    diagnostic = diagnose_fn(team)
    if missing:
        # Type facts can still be useful. Set-dependent absence and numeric speed are not confirmed.
        diagnostic = deepcopy(diagnostic)
        if any(set(row["missing_fields"]) & {"moves", "trusted_set"} for row in needed):
            diagnostic["offense"] = {"skipped": "moveset evidence incomplete; no confirmed coverage gaps"}
            diagnostic["roles"] = {"skipped": "moveset evidence incomplete; no confirmed absent roles"}
        if any(set(row["missing_fields"]) & {"spread", "nature", "ability", "trusted_set"} for row in needed):
            diagnostic["speed"] = {"skipped": "speed configuration incomplete; no assumed zero-SP speeds"}
    status = ("invalid" if legality.get("status") == "invalid" else "needs_input" if missing or not fmt
              else "unknown" if legality.get("status") != "valid" else "ready")
    matchup = selection = None
    if status == "ready":
        matchup = matchup_fn(team)
        if not matchup or matchup.get("unavailable"):
            status = "calculation_unavailable"
        elif not (matchup.get("coverage") or {}).get("complete"):
            status = "partial"
        if matchup and not matchup.get("unavailable"):
            selection = select_fn(team, matchup, legality.get("status"))
    return {"kind": "review", "status": status, "team": team, "context": deepcopy(context),
            "environment": deepcopy(environment), "requirements": needed,
            "missing_format": not bool(fmt), "legality": legality, "diagnose": diagnostic,
            "matchup": matchup, "selection": selection,
            "next_step": "use static facts with their qualifications" if status == "ready" else
                         "resolve missing inputs, legality or calculation evidence before making whole-team claims",
            "note": "One battery supplies all CHECK facts. Legality and input completeness are separate. "
                    "Static checks are not battle outcomes; proposed changes still require the UEP slate/answer chain."}


def apply_changes(team: dict, changes: list) -> tuple[dict, list[dict]]:
    if not isinstance(changes, list) or not changes:
        raise ValueError("changes must be a nonempty list of {slot,set}")
    out = deepcopy(team)
    members = out.get("pokemon") or []
    visited, ledger = set(), []
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"slot", "set"}:
            raise ValueError("each change must contain exactly slot and set")
        slot, patch = change["slot"], change["set"]
        if type(slot) is not int or not 0 <= slot < len(members) or slot in visited:
            raise ValueError("change slots must be distinct valid zero-based member indices")
        if not isinstance(patch, dict) or not patch or set(patch) - CHANGE_FIELDS:
            raise ValueError("set accepts only species/item/ability/moves/nature/spread")
        visited.add(slot)
        before = deepcopy(members[slot])
        replacing = "species" in patch and patch["species"] != before.get("species")
        candidate = {} if replacing else deepcopy(before)
        # A spread is one explicit allocation, not a hidden incremental adjustment.
        candidate.update(deepcopy(patch))
        if any(candidate.get(key) != before.get(key) for key in CHANGE_FIELDS):
            candidate["completeness"] = (before.get("completeness") if not replacing and
                before.get("completeness") in ("inferred_set", "observed_species_only") else None)
            members[slot] = candidate
            ledger.append({"slot": slot, "before": before, "after": deepcopy(candidate),
                           "replaced_species": replacing})
    if not ledger:
        raise ValueError("changes do not change any member configuration")
    # Original provenance is retained in the before-report and ledger, never claimed for a new proposal.
    out["provenance"] = []
    return out, ledger


def compare(before: dict, after: dict, changes: list[dict]) -> dict:
    b, a = before["diagnose"], after["diagnose"]
    structural = {}
    for aspect, fn in (("defense", _defense_diff), ("offense", _offense_diff), ("roles", _roles_diff)):
        left, right = b.get(aspect) or {}, a.get(aspect) or {}
        structural[aspect] = ({"skipped": "one side lacks confirmed structural evidence"}
                              if left.get("skipped") or right.get("skipped") else fn(left, right))
    structural["speed"] = ({"skipped": "one side lacks speed evidence"} if
        (b.get("speed") or {}).get("skipped") or (a.get("speed") or {}).get("skipped") else
        [_speed_diff(b.get("speed") or {}, a.get("speed") or {}, row["before"]["species"], row["after"]["species"])
         for row in changes])
    left = ((before.get("matchup") or {}).get("check_coverage") or {}).get("by_opponent") or []
    right = ((after.get("matchup") or {}).get("check_coverage") or {}).get("by_opponent") or []
    lb, ra = {r["opponent"]: r for r in left}, {r["opponent"]: r for r in right}
    floors = []
    for name in dict.fromkeys([*lb, *ra]):
        old, new = lb.get(name), ra.get(name)
        # No cross-pool comparison: identical concrete retained variants and full calculations required.
        comparable = bool(old and new and old.get("calculation_complete") and new.get("calculation_complete")
            and {v["variant_id"] for v in old.get("variants") or []} == {v["variant_id"] for v in new.get("variants") or []})
        floors.append({"opponent": name, "before": deepcopy(old), "after": deepcopy(new),
                       "comparable": comparable,
                       "same_floor": old["observed_floor"]["grade"] == new["observed_floor"]["grade"] if comparable else None})
    return {"structure": structural, "opponent_floors": floors,
            "comparable_opponents": sum(row["comparable"] for row in floors),
            "note": "Net before/after structural differences reuse replace diagnostics. No score or winner; "
                    "partial calculations or different variant pools are not comparable."}


def project(result: dict, view: str = "summary") -> dict:
    if view == "full" or result.get("error"):
        return result
    out = deepcopy(result)
    if out.get("kind") == "try":
        out["before"] = project(out["before"], "summary")
        out["after"] = project(out["after"], "summary")
        for row in out["comparison"]["opponent_floors"]:
            for side in ("before", "after"):
                if row.get(side):
                    row[side] = team_views.coverage({"by_opponent": [row[side]]})["by_opponent"][0]
    else:
        # References remain discoverable without repeating the full grid in compact output.
        out["evidence_index"] = [{"ref": entry["ref"]} for entry in out.get("evidence_index") or []]
        out["diagnose"] = team_views.project(out["diagnose"], "diagnose")
        if out.get("selection"):
            selection = out["selection"]
            selection["combos_count"] = len(selection.pop("combos", []))
        if out.get("matchup") and not out["matchup"].get("unavailable"):
            out["matchup"] = project_matchup(out["matchup"], "summary")
    out["view"] = "summary"
    out["projection"] = {"display_only": True, "omitted": ["selection.combos", "detailed matchup predicates"],
                         "note": "Full --out artifact and session references retain all evidence and the receipt."}
    return out
