"""Evidence/disclosure floor, never a prescribed composition or a forced candidate count."""
from __future__ import annotations


def concerns(output: dict) -> list[dict]:
    result = []
    for pair in (output.get("comparison") or {}).get("pairs") or []:
        fraction = (pair.get("core_overlap") or {}).get("fraction_of_smaller")
        if fraction is None or fraction > 0.5:
            result.append({"left": pair["left"], "right": pair["right"],
                           "kind": "unknown_core" if fraction is None else "core_overlap_over_half",
                           "fraction_of_smaller": fraction})
    return result


def violations(draft: dict, output: dict) -> list[dict]:
    facts = concerns(output)
    disclosure = draft.get("exploration")
    if facts and not (isinstance(disclosure, dict) and isinstance(disclosure.get("reason"), str)
                      and disclosure["reason"].strip()):
        return [{"code": "exploration_disclosure_missing", "where": "exploration.reason",
                 "detail": "Explain why the evaluated candidates share over half a bound core or lack comparable "
                           "bound cores (for example user scope, pool limits or deliberate local variants). "
                           "No additional candidate is forced.", "facts": facts}]
    return []


def team_claim_violations(draft: dict, output: dict, recommended: set[int]) -> list[dict]:
    claims = draft.get("team_claims", [])
    if not isinstance(claims, list):
        return [{"code": "bad_team_claims", "where": "team_claims", "detail": "team_claims must be a list"}]
    refs = {}
    for c in output.get("candidates") or []:
        if c["index"] not in recommended:
            continue
        for i, row in enumerate(((c.get("matchup_risk") or {}).get("check_coverage") or {}).get("by_opponent") or []):
            refs[f"/candidates/{c['index']}/matchup_risk/check_coverage/by_opponent/{i}"] = row
    violations = []
    for i, claim in enumerate(claims):
        claim = claim if isinstance(claim, dict) else {}
        ref = claim.get("evidence_ref")
        row = refs.get(ref) if isinstance(ref, str) else None
        if row is None or claim.get("expect") != row or not isinstance(claim.get("claim"), str) or not claim["claim"].strip():
            violations.append({"code": "team_claim_unbound", "where": f"team_claims[{i}]",
                "detail": "A team matchup claim requires a recommended candidate's saved by_opponent evidence_ref, "
                          "the complete row in expect (including qualifications), and nonempty claim text."})
    return violations
