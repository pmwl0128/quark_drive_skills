"""Request-bound diagnosis evidence; never infer a user's missing battle configuration.

Grades and damage stay owned by the team skill. These helpers only bind, qualify or
withhold its facts at the Web boundary, including before an optional model sees them.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from .paths import SKILLS_ROOT

CHANGE_FIELDS = ("moves", "item", "ability", "nature", "spread", "species")
DEFAULT_CHANGES = CHANGE_FIELDS[:-1]


class DiagnosisInputError(ValueError):
    """A parsed team cannot satisfy the request's member coordinates."""


def diagnosis_intent(body: dict) -> dict:
    goal = body.get("goal", "")
    slots = body.get("lockedSlots", [])
    allowed = body.get("allowedChanges", list(DEFAULT_CHANGES))
    if not isinstance(goal, str) or len(goal) > 500:
        raise ValueError("invalid diagnosis goal")
    if not isinstance(slots, list) or len(slots) > 6 or any(
            type(s) is not int or s < 0 or s > 5 for s in slots) or len(set(slots)) != len(slots):
        raise ValueError("invalid locked slots")
    if not isinstance(allowed, list) or len(allowed) > len(CHANGE_FIELDS) or any(
            not isinstance(value, str) or value not in CHANGE_FIELDS for value in allowed) \
            or len(set(allowed)) != len(allowed):
        raise ValueError("invalid allowed changes")
    return {"goal": goal.strip(), "lockedSlots": sorted(slots), "allowedChanges": allowed}


def data_snapshot(pool) -> dict:
    # Reuse the existing data-generation check and resident-worker invalidation. Include
    # the derived battery too: it can be rebuilt without changing the source team files.
    from .online.qa import _modal_snapshot

    identity = _modal_snapshot(pool)
    current = json.loads((SKILLS_ROOT / "pokemon-champions-meta/data/current.json")
                        .read_text(encoding="utf-8"))["current"]
    if not all(isinstance(current.get(k), str) and current[k] for k in ("season", "rule")):
        raise RuntimeError("missing current data authority")
    cache_dir = SKILLS_ROOT / "pokemon-champions-team/data/opponent_cache"
    files = [(p.name, p.stat().st_mtime_ns, p.stat().st_size)
             for p in sorted(cache_dir.glob("*.json"))]
    key = json.dumps([identity, current, files], sort_keys=True).encode("utf-8")
    return {"id": hashlib.sha256(key).hexdigest(),
            "season": current["season"], "rule": current["rule"]}


def input_requirements(team: dict) -> list[dict]:
    members = []
    for slot, member in enumerate(team.get("pokemon") or []):
        missing = []
        level = member.get("completeness")
        if level not in (None, "observed_full_set", "extracted_set"):
            missing.append("trusted_set")
        if not member.get("moves"):
            missing.append("moves")
        for key in ("ability", "nature"):
            if not member.get(key):
                missing.append(key)
        # An explicit allocation can omit zero-valued stats; no allocation is unknown.
        # Item null, in contrast, is the team-json representation of no held item.
        if not isinstance(member.get("spread"), dict) or not member["spread"]:
            missing.append("spread")
        members.append({"slot": slot, "species": member["species"],
                        "completeness": level, "missingFields": missing})
    return members


def bind_report(report: dict, snapshot: dict, intent: dict, requirements: list[dict],
                variants: list[dict]) -> dict:
    """Bind all three evidence levels to the exact input and data generation."""
    intent = {"allowedChanges": list(DEFAULT_CHANGES), **intent}
    key = json.dumps([report["team"], snapshot, intent], sort_keys=True,
                     ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    analysis_id = hashlib.sha256(key).hexdigest()
    ready = all(not m["missingFields"] for m in requirements)
    report["analysis"] = {"id": analysis_id, "status": "ready" if ready else "needs_input",
                          "format": report["team"]["format"],
                          "pickSize": 4 if report["team"]["format"] == "double" else 3,
                          "snapshot": snapshot, **intent, "members": requirements}
    if not ready:
        # Even if an older worker returns assumed cells, none are user evidence.
        report.pop("checks", None)
        report["offense"]["hardGaps"] = []
        report["offense"]["gapsConfirmed"] = False
        report["roles"]["coverage"] = [r for r in report["roles"]["coverage"] if r["present"]]
        report["roles"]["coverageConfirmed"] = False
        unknown_speed = {m["species"] for m in requirements if any(
            f in m["missingFields"] for f in ("spread", "nature", "trusted_set"))}
        for key in ("order", "orderUnderTrickRoom", "members"):
            report["speed"][key] = [r for r in report["speed"][key]
                                    if r["species"] not in unknown_speed]
        return report

    def ref(scope: str, opponent: str, **coordinates) -> dict:
        return {"scope": scope, "analysisId": analysis_id, "opponent": opponent, **coordinates}

    slots = {m["species"]: m["slot"] for m in requirements}
    by_opponent = {r["opponent"]: r for r in variants}
    for row in (report.get("checks") or {}).get("byOpponent") or []:
        opponent = row["opponent"]
        row["ref"] = ref("team_floor", opponent)
        row["variants"] = [
            {"ref": ref("opponent_variant", opponent, variantId=v["variant_id"]),
             "grade": v.get("grade")}
            for v in by_opponent.get(opponent, {}).get("variants") or [] if v.get("variant_id")]
        for cell in row["cells"]:
            cell["ref"] = ref("member_cell", opponent, variantId=cell["variantId"],
                              memberSlot=slots[cell["member"]])
    return report


def qualified_damage(offense: dict | None) -> dict | None:
    """A model never receives an unqualified KO verdict beside survival/sequence caveats."""
    if not offense:
        return None
    result = deepcopy(offense)
    restricted = bool(result.get("koCaveats") or result.get("disguiseAdjusted"))
    exact = result.get("koExact") is True and not restricted
    result["koExact"] = exact
    result["qualification"] = "exact_single_hit" if exact else "raw_damage_only"
    if not exact:
        # Keep the calculation's numeric damage and its complete caveats together. A
        # raw OHKO or static N-hit phrase must not masquerade as an actual sequence.
        for key in ("ko", "koChance", "koPossible", "koGuaranteed"):
            result.pop(key, None)
    return result


def read_evidence(report: dict, reference: dict) -> dict:
    """Read only a server-bound coordinate; a member cell can never return a team floor.

    This is the read seam for the subsequent restricted tool layer, not a recalculation
    or a means of upgrading an arbitrary model-supplied grade into verified evidence.
    """
    analysis = report.get("analysis") or {}
    if analysis.get("status") != "ready" or reference.get("analysisId") != analysis.get("id"):
        raise ValueError("evidence belongs to a different analysis")
    for row in (report.get("checks") or {}).get("byOpponent") or []:
        if reference == row.get("ref"):
            return deepcopy({k: v for k, v in row.items() if k not in ("cells", "variants")})
        for variant in row.get("variants") or []:
            if reference == variant["ref"]:
                return deepcopy(variant)
        for cell in row.get("cells") or []:
            if reference == cell.get("ref"):
                return {**deepcopy(cell), "offense": qualified_damage(cell.get("offense")),
                        "incoming": qualified_damage(cell.get("incoming")),
                        "memberBuild": deepcopy(report["team"]["pokemon"][reference["memberSlot"]])}
    raise ValueError("unknown evidence reference")
