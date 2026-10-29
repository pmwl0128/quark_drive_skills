"""Turn one verified member cell into threshold lanes and explicit copy-trial changes."""
from copy import deepcopy

import matchup
import team_review
from stat_keys import canonical_spread


def query(spec, *, recompute_fn, dex_fn, item_fn, threshold_fn, validate_fn):
    fields = {"report", "ref", "goal", "solve_for", "probability", "compare_items", "maximize"}
    if not isinstance(spec, dict) or set(spec) - fields or not team_review.verify(spec.get("report")):
        raise ValueError("tune --from-cell requires a complete sealed review and a member-cell reference")
    report, ref = spec["report"], spec.get("ref")
    if not isinstance(ref, dict) or ref.get("scope") != "member_cell":
        raise ValueError("choose a member_cell ref from the full review evidence_index")
    entry = next((e for e in team_review.evidence(report) if e["ref"] == ref), None)
    if not entry:
        raise ValueError("member-cell reference is not in the report")
    checked = team_review.audit_claims({"report": report, "claims": [
        {"ref": ref, "expect": entry["fact"], "claim": "source cell for explicit tuning"}]}, recompute_fn=recompute_fn)
    if not checked["pass"]:
        raise ValueError("source cell did not reproduce: " + str(checked["violations"]))
    slot = ref["member_slot"]
    member = report["team"]["pokemon"][slot]
    cell = entry["fact"]["cell"]
    opponent = deepcopy(report["matchup"]["opponent_sets"][ref["variant_id"]])
    facts = dex_fn([member["species"], entry["fact"].get("member_run_form") or member["species"]])
    items = item_fn([member["item"]]) if member.get("item") else {}
    effective = matchup.effective_member(member, facts, items)
    actor = matchup.member_actor(effective)
    own = {"species": effective["species"], **{k: actor[k] for k in ("item", "ability", "nature", "sps")},
           "moves": member["moves"], "source": "stated-review-member", "coverage": 1.0}
    own_key, opp_key = "variant:review-member", "variant:review-opponent"
    fmt = report["matchup"]["format"]
    cache = {"built_for": {"format": fmt, "top_k": 1}, "sets": {own_key: own, opp_key: opponent}}
    survival = spec.get("goal") in {"survive", "survive_2"}
    side = "defender" if survival else "attacker"
    if not isinstance(spec.get("solve_for"), str) or not spec["solve_for"].startswith(side + "."):
        raise ValueError("solve_for must tune the referenced team member, not its opponent")
    fact = ((cell.get("defense_damage") or {}).get("worst") if survival else cell.get("offense")) or {}
    conditions = (report.get("battle_scope") or {}).get("conditions") or {}
    benchmark = {k: deepcopy(spec[k]) for k in ("goal", "solve_for", "probability", "compare_items", "maximize") if k in spec}
    benchmark.update(format=fmt, attacker=opp_key if survival else own_key, defender=own_key if survival else opp_key,
                     field={k: v for k, v in conditions.items() if k != "trick_room"},
                     speed_field={"trick_room": conditions.get("trick_room", False)})
    if spec.get("goal") != "outspeed":
        if not fact.get("move"):
            raise ValueError("this cell has no calculated damaging move in the requested direction")
        benchmark["move"] = fact["move"]
    result = threshold_fn(benchmark, cache)
    trials = []
    for index, lane in enumerate(result["lanes"]):
        for basis in ("at_min", "maximized"):
            point = lane.get(basis)
            if not point or not point.get("met"):
                continue
            patch = {"nature": lane["nature"], "item": lane.get("item"),
                     "spread": canonical_spread(point["allocation"])}
            changes = [{"slot": slot, "set": patch}]
            try:
                changed, _ = team_review.apply_changes(report["team"], changes)
            except ValueError:
                continue
            legality = validate_fn(changed)
            trials.append({"lane": index, "basis": basis, "changes": changes,
                           "legality": legality, "available": legality.get("status") == "valid"})
    return {"kind": "tune-cell", "source": {"report_fingerprint": report["receipt"]["fingerprint"], "ref": ref},
            "source_check": checked, "benchmark": benchmark, "bound_sets": cache["sets"], "threshold": result,
            "trials": trials, "note": "Nature/item lanes and closest unreachable percentages reuse threshold. "
            "Each available change meets this one benchmark; pass it to try for whole-team trade-offs and then UEP. "
            "No lane is selected and the original team is unchanged."}
