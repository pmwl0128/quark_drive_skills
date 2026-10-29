"""Project complete observed answer builds from the existing reverse CHECK facts."""
from __future__ import annotations

from copy import deepcopy

import counters
from stat_keys import canonical_spread


def attach(output, need, cache, fmt, *, excluded_items=(), build_matches=None):
    """Intersect multiple targets on ONE exact candidate variant, never on species marginals."""
    targets = need["answer"] if isinstance(need["answer"], list) else [need["answer"]]
    queries = [counters.query(cache, name, fmt, counters.TOP_N_MAX) for name in dict.fromkeys(targets)]
    index = [{r["candidate"]["variant_id"]: r for r in q["rows"]} for q in queries]
    forbidden = set(excluded_items)
    by_species = {}
    omitted = {"incomplete_observed_set": 0, "excluded_item": 0, "other_need": 0}
    for key, row in index[0].items():
        if not all(key in table for table in index[1:]):
            continue
        own = row["candidate"]
        # Cached reference SP may be a disclosed meta fallback. It is useful for counters but is
        # not an observed full configuration to transport into a candidate unchanged.
        if (own.get("source") != "real-team" or own.get("real_team_backed") is not True
                or not all(own.get(k) for k in ("ability", "nature", "moves", "sps")) or own.get("item") is None):
            omitted["incomplete_observed_set"] += 1
            continue
        if own["item"] in forbidden:
            omitted["excluded_item"] += 1
            continue
        member = {"species": own["species"], **{k: deepcopy(own[k]) for k in ("item", "ability", "nature", "moves")},
                  "spread": canonical_spread(own["sps"])}
        matched = build_matches(member) if build_matches is not None else {}
        if matched is None:
            omitted["other_need"] += 1
            continue
        facts = [{"target": q["pokemon"], "selector": q["selector"],
                  **{k: deepcopy(table[key][k]) for k in ("grade", "coverage", "observed_floor", "answers", "blocked_by", "missing", "contested")}}
                 for q, table in zip(queries, index)]
        by_species.setdefault(own["species"], []).append({**deepcopy(own), "team_member": member, "addresses": matched,
            "observed_complete": True, "answer_facts": facts})
    candidates = []
    for candidate in output["candidates"]:
        builds = by_species.get(candidate["species"], [])
        if builds:
            candidates.append({**candidate, "builds": builds,
                               "addresses": {"answer": targets}})
    output["candidates"] = candidates
    output["candidate_count"] = len(candidates)
    names = {c["species"] for c in candidates}
    output["views"] = {view: [name for name in values if name in names] for view, values in output["views"].items()}
    output["answer_scope"] = {"format": fmt, "targets": [{"selector": q["selector"], "pokemon": q["pokemon"],
        "target_builds": q["target_builds"], "coverage": q["coverage"]} for q in queries],
        "built_for": deepcopy(cache.get("built_for")), "omitted": omitted,
        "condition": "Neutral cached static CHECK, all retained target builds, one concrete observed candidate variant; "
                     "C1 is even footing, C2 includes a safe switch. Doubles remains an isolated pair. "
                     "Read all caveats/contested facts; no win rate or guaranteed battle outcome is implied."}
    output["notes"].append("answer transports complete real-team joint configurations only. Meta SP fallbacks and "
                           "unknown spreads are omitted. Existing team items and avoid_items are excluded at build "
                           "level; no item is silently replaced. Other need predicates must hold on the same build.")
    output.pop("avoid_items_not_enforced", None)
    output["confidence"], output["confidence_reason"] = "low", "vs-observed-build"
    return output
