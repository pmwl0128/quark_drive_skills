"""Transport grounded complete replacement builds; never invent missing source fields."""
from copy import deepcopy

from cliffs import effective_speed
from fill import _addresses
from stat_keys import canonical_spread
import team_review


def attach(output, original, slot, *, dex_fn, move_fn, review_fn):
    candidates = []
    omitted = {"incomplete_source": 0, "need_not_met": 0, "unavailable_item": 0, "invalid_trial": 0}
    occupied = {m.get("item") for i, m in enumerate(original["pokemon"]) if i != slot and m.get("item")}
    forbidden = occupied | set(output.get("avoid_items_not_enforced") or [])
    facts = dex_fn([c["species"] for c in output.get("candidates") or []])
    before = review_fn(original)
    for candidate in output.get("candidates") or []:
        builds = []
        for build in candidate.get("builds") or []:
            member = deepcopy(build.get("team_member"))
            if member is None:
                if build.get("spread_origin") != "real-team" or build.get("item") is None:
                    omitted["incomplete_source"] += 1
                    continue
                member = {"species": candidate["species"],
                    **{k: deepcopy(build.get(k)) for k in ("item", "ability", "nature", "moves")},
                    "spread": canonical_spread(build.get("sps") or {})}
            if any(team_review.requirements({"pokemon": [member]})[0]["missing_fields"]):
                omitted["incomplete_source"] += 1
                continue
            if member.get("item") in forbidden:
                omitted["unavailable_item"] += 1
                continue
            fact = {**(facts.get(member["species"]) or {}), "abilities": [member["ability"]], "moves": member["moves"]}
            need = {k: v for k, v in output["need"].items() if k not in {"answer", "min_speed"}}
            min_speed = output["need"].get("min_speed")
            coverage = {}
            if need.get("coverage_move_type"):
                for name, move in move_fn(member["moves"]).items():
                    if move.get("category") in ("Physical", "Special") and move.get("type"):
                        coverage.setdefault(move["type"], []).append(name)
            matched = _addresses(need, fact, coverage)
            base = (fact.get("stats") or {}).get("spe")
            speed = effective_speed(base, member["spread"].get("spe", 0), member["nature"],
                                    item=member.get("item"), ability=member["ability"]) if base is not None else None
            if matched is None or (min_speed is not None and (speed is None or speed < min_speed)):
                omitted["need_not_met"] += 1
                continue
            if min_speed is not None:
                matched["min_speed"] = {"target": min_speed, "set_speed": speed}
            changes = [{"slot": slot, "set": member}]
            try:
                trial, ledger = team_review.apply_changes(original, changes)
            except ValueError:
                continue
            after = review_fn(trial)
            if after["legality"].get("status") != "valid":
                omitted["invalid_trial"] += 1
                continue
            diff = team_review.compare(before, after, ledger)["structure"]
            builds.append({**build, "team_member": member, "changes": changes,
                           "structure_diff": diff, "legality": after["legality"], "set_speed": speed,
                           "addresses": matched})
        if builds:
            candidates.append({**candidate, "builds": builds})
    names = {c["species"] for c in candidates}
    output.update(candidates=candidates, candidate_count=len(candidates),
                  views={k: [n for n in v if n in names] for k, v in output["views"].items()},
                  trial_scope={"slot": slot, "omitted": omitted,
                    "note": "Complete observed configurations only; retained team items excluded. Structural differences "
                            "are not matchup improvements. Pass explicit changes to try and then the UEP gates."})
    output.pop("avoid_items_not_enforced", None)
    output["notes"] = [n for n in output["notes"] if not n.startswith("avoid ITEMS")]
    return output
