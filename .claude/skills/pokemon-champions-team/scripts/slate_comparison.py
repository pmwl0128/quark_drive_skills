"""Order-preserving candidate comparisons. Labels and intersections, never a team score."""
from __future__ import annotations

from copy import deepcopy
from itertools import combinations


def target_rows(rows: list[dict], goals: list[dict]) -> list[dict]:
    """Append explicit answer targets without assigning them an invented usage rank."""
    result = deepcopy(rows)
    present = {r.get("pokemon_en") or r.get("pokemon") or r.get("slug") for r in result}
    for goal in goals:
        name = goal["species"]
        if goal["kind"] == "answer" and name not in present:
            result.append({"pokemon_en": name, "rank": None})
            present.add(name)
    return result


def candidate_facts(candidate: dict, battle_team: dict, goals: list[dict]) -> dict:
    members = battle_team.get("pokemon") or []
    identity_complete = bool(members) and all(m.get("species") and not m.get("_identity_unknown") for m in members)
    forms = sorted({m["species"] for m in members if m.get("species")})
    binding = candidate.get("frame_binding") or {}
    core = sorted(set(binding.get("core_bearers") or []))
    coverage = (candidate.get("matchup_risk") or {}).get("check_coverage") or {}
    rows = coverage.get("by_opponent") or []
    by_name = {row["opponent"]: (index, row) for index, row in enumerate(rows)}
    targets = []
    for goal in goals:
        row = deepcopy(goal)
        if goal["kind"] == "include":
            row.update(present=goal["species"] in forms if identity_complete else None,
                       identity_basis="battle_form")
        else:
            found = by_name.get(goal["species"])
            row.update(status="unavailable", coverage=None, evidence_ref=None)
            if found:
                index, facts = found
                row.update(status="calculated" if facts.get("calculation_complete") else "partial",
                           coverage=deepcopy(facts),
                           evidence_ref=f"/candidates/{candidate['index']}/matchup_risk/check_coverage/by_opponent/{index}")
        targets.append(row)
    return {"battle_forms": forms, "identity_complete": identity_complete,
            "core_bearers": core, "core_basis": "bound_frame" if core else "unavailable",
            "goals": targets}


def compare(candidates: list[dict], survivors: list[int]) -> dict:
    """Keep all candidates; eliminated/uncomputed facts remain unknown, not zero holes."""
    per_candidate = []
    for c in candidates:
        cov = (c.get("matchup_risk") or {}).get("check_coverage")
        rows = (cov or {}).get("by_opponent") or []
        has_floor = any((r.get("observed_floor") or {}).get("grade") is not None for r in rows)
        per_candidate.append({"index": c["index"], "eliminated": bool(c.get("eliminated")),
            "hole_count": len(cov.get("holes") or []) if has_floor else None,
            "assessed_opponents": len(rows),
            "incomplete_opponents": sum(not r.get("calculation_complete") for r in rows),
            "library_overlap": deepcopy(c.get("library_overlap")),
            "goals": deepcopy((c.get("comparison_facts") or {}).get("goals") or [])})
    pairs = []
    for left, right in combinations(survivors, 2):
        a, b = candidates[left], candidates[right]
        af, bf = a.get("comparison_facts") or {}, b.get("comparison_facts") or {}
        core_a, core_b = set(af.get("core_bearers") or []), set(bf.get("core_bearers") or [])
        core_shared = sorted(core_a & core_b)
        rows_a = ((a.get("matchup_risk") or {}).get("check_coverage") or {}).get("by_opponent") or []
        rows_b = ((b.get("matchup_risk") or {}).get("check_coverage") or {}).get("by_opponent") or []
        indexed = {r["opponent"]: (i, r) for i, r in enumerate(rows_b)}
        floors = []
        for i, row in enumerate(rows_a):
            if row["opponent"] not in indexed:
                continue
            j, other = indexed[row["opponent"]]
            floors.append({"opponent": row["opponent"],
                "left": deepcopy(row), "right": deepcopy(other),
                "same_floor": (row["observed_floor"]["grade"] == other["observed_floor"]["grade"]
                               if row.get("observed_floor", {}).get("grade") is not None
                               and other.get("observed_floor", {}).get("grade") is not None else None),
                "evidence_refs": [f"/candidates/{left}/matchup_risk/check_coverage/by_opponent/{i}",
                                  f"/candidates/{right}/matchup_risk/check_coverage/by_opponent/{j}"]})
        pairs.append({"left": left, "right": right,
            "shared_battle_forms": sorted(set(af.get("battle_forms") or []) & set(bf.get("battle_forms") or [])),
            "identity_complete": bool(af.get("identity_complete") and bf.get("identity_complete")),
            "core_overlap": {"shared": core_shared, "left_size": len(core_a), "right_size": len(core_b),
                "fraction_of_smaller": len(core_shared) / min(len(core_a), len(core_b)) if core_a and core_b else None},
            "opponent_floors": floors,
            "left_only_opponents": [r["opponent"] for r in rows_a if r["opponent"] not in indexed],
            "right_only_opponents": [r["opponent"] for r in rows_b if r["opponent"] not in {x["opponent"] for x in rows_a}]})
    return {"candidates": per_candidate, "pairs": pairs,
            "note": "Input order; no ranking or score. Floors cover retained builds only; partial calculations, "
                    "sample coverage and contested variants remain attached. Core overlap is unknown without bound cores. "
                    "Goals are reporting targets, not elimination rules or proof of a battle win."}
