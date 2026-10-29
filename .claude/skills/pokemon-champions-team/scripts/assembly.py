"""Explicit candidate transport for UEP; no candidate selection, repair or strategic judgment."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import contracts
import intake
import slate
from canonhash import content_hash


class AssemblyError(ValueError):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code


def _require(ok: bool, code: str, reason: str) -> None:
    if not ok:
        raise AssemblyError(code, reason)


def onboarding(context: dict, saved: Any = None) -> dict:
    """Carry explicit resolved ids, re-evaluate context coverage, never mark a displayed batch answered."""
    answered = []
    if saved is not None:
        _require(isinstance(saved, dict) and saved.get("kind") == "intake"
                 and saved.get("mode") == "onboarding_step", "bad_intake_output",
                 "use the full saved intake --next result")
        _require(saved.get("context_fingerprint") == content_hash(context, 24), "intake_context_mismatch",
                 "run intake --next on the final context before carrying its answered ids")
        _require(saved.get("game_format") in (None, context.get("format")), "intake_format_mismatch",
                 "intake and context must use the same battle format")
        answered = saved.get("answered")
        _require(isinstance(answered, list) and all(isinstance(q, str) for q in answered)
                 and not saved.get("unknown_answered"), "bad_answered", "intake answered must contain known ids")
    progress = intake.next_batch(context, context.get("format"), answered)
    _require(not progress["unknown_answered"], "bad_answered", "intake answered contains unknown ids")
    return {"context_fingerprint": content_hash(context, 24), "answered": progress["answered"],
            "covered": [q["id"] for q in progress["already_answered"]], "done": progress["done"]}


def onboarding_summary(context: dict, carried: Any) -> dict:
    if carried is not None:
        _require(isinstance(carried, dict), "bad_onboarding", "slate onboarding must be an object")
        saved = {**carried, "kind": "intake", "mode": "onboarding_step"}
    else:
        saved = None
    progress = onboarding(context, saved)
    return {"status": "completed" if progress["done"] else "incomplete",
            "answered": progress["answered"],
            "note": "Explicitly resolved intake ids: " + (", ".join(progress["answered"]) or "none")
                    + "; dimensions covered by the current context: " + (", ".join(progress["covered"]) or "none") + "."}


_BINDING_FIELDS = {"frame_id", "off_meta", "deviations", "mega_deviation", "off_meta_build"}


def assemble(spec: Any, context: dict, receipt: dict, expected_audit: str,
             frame_output: Any = None, intake_output: Any = None) -> dict:
    _require(isinstance(spec, dict) and set(spec) == {"candidates"}
             and isinstance(spec["candidates"], list) and bool(spec["candidates"]),
             "bad_assembly", "assembly must be {candidates:[{team|members, frame_id?, ...binding fields}]}")
    _require(isinstance(receipt, dict) and receipt.get("fingerprint") == expected_audit,
             "audit_receipt_mismatch", "audit receipt does not match the final context; run context-audit again")
    _require(context.get("format") in ("single", "double"), "missing_format", "context must specify single or double")
    _require(not context.get("frame_required") or frame_output is not None,
             "missing_frame_output", "a build-flow assembly requires the full saved frame output")
    skeletons = {}
    if frame_output is not None:
        verified = slate.verify_frame_output(frame_output, expected_audit, context["format"])
        if not verified["ok"]:
            raise AssemblyError(verified["refused"]["code"], verified["refused"]["reason"])
        skeletons = verified["skeletons_by_id"]
    teams, bindings, sources, missing = [], [], [], []
    for index, candidate in enumerate(spec["candidates"]):
        _require(isinstance(candidate, dict) and not (set(candidate) - (_BINDING_FIELDS | {"team", "members"}))
                 and (("team" in candidate) != ("members" in candidate)), "bad_candidate",
                 f"candidate {index} must contain exactly one of team or members and supported binding fields")
        binding = {k: deepcopy(v) for k, v in candidate.items() if k in _BINDING_FIELDS}
        frame_id = binding.get("frame_id")
        _require(frame_id is None or isinstance(frame_id, str), "bad_frame_id", "frame_id must be a string")
        _require(not binding or frame_output is not None, "missing_frame_output", "bindings require the saved frame output")
        if frame_output is not None:
            _require(frame_id in skeletons or (binding.get("off_meta_build") is True and frame_id is None),
                     "unknown_frame", f"candidate {index} must explicitly select a known frame_id or off_meta_build:true")
        if "team" in candidate:
            team = deepcopy(candidate["team"])
        else:
            members = candidate["members"]
            _require(isinstance(members, list) and bool(members), "bad_members", "members must be a nonempty ordered list")
            team = {k: deepcopy(context[k]) for k in ("format", "season", "rule") if context.get(k)}
            team.update(pokemon=[], provenance=[])
            for slot, member in enumerate(members):
                _require(isinstance(member, dict), "bad_member", "members must contain member objects or {from_frame:species}")
                if "from_frame" not in member:
                    team["pokemon"].append(deepcopy(member))
                    continue
                _require(set(member) == {"from_frame"} and isinstance(member["from_frame"], str),
                         "bad_frame_member", "a frame reference must be exactly {from_frame:<canonical battle-form species>}")
                matches = [m for m in skeletons.get(frame_id, {}).get("core_candidates", [])
                           if m.get("species") == member["from_frame"]]
                _require(len(matches) == 1, "unknown_frame_member", f"no unique frame member {member['from_frame']!r}")
                grounding = matches[0].get("grounding") or {}
                primary = grounding.get("primary")
                _require(isinstance(primary, dict), "ungrounded_frame_member",
                         f"{member['from_frame']} has no joint primary set; supply an authored member")
                converted = {"species": member["from_frame"],
                             **{k: deepcopy(primary.get(k)) for k in ("item", "ability", "nature", "moves")},
                             "spread": deepcopy(primary.get("sps"))}
                team["pokemon"].append(converted)
                sources.append({"team": index, "slot": slot, "frame_id": frame_id,
                                "species": converted["species"], "grounding_ref": grounding.get("grounding_ref"),
                                **{k: deepcopy(grounding[k]) for k in ("configuration_basis", "registration_modes",
                                                                     "registration_sample") if k in grounding}})
                for key in ("item", "ability", "nature", "moves", "spread"):
                    if converted[key] is None or (key == "moves" and not converted[key]):
                        missing.append({"team": index, "slot": slot, "field": key})
        errors = contracts.check_team(team)
        _require(not contracts.fatal(errors), "bad_team", f"candidate {index} fails the team-json contract: "
                 + "; ".join(str(e.to_dict()) for e in errors if e.severity == "error"))
        _require(team.get("format") in (None, context["format"]), "team_format_mismatch",
                 f"candidate {index} uses a different battle format")
        teams.append(team)
        bindings.append(binding)
    return {"kind": "slate-init", "context": deepcopy(context), "audit_receipt": deepcopy(receipt),
            "teams": teams, **({"frame_bindings": bindings} if frame_output is not None else {}),
            "onboarding": onboarding(context, intake_output),
            "assembly": {"frame_sources": sources, "missing_fields": missing,
                         "note": "Selected primary sets are proposal inputs, not an observed whole team. "
                                 "No missing field, deviation acknowledgement or strategic choice is invented. "
                                 "Run slate-evaluate with the same full frame output."}}


def verify_draft_source(source: dict, output: dict) -> None:
    _require(output.get("view") != "summary", "summary_slate_output", "draft-init requires the full saved slate output")
    candidates, survivors = output.get("candidates"), output.get("survivors")
    _require(isinstance(candidates, list) and len(candidates) == len(source["teams"])
             and all(isinstance(c, dict) for c in candidates) and isinstance(survivors, list)
             and all(type(i) is int and 0 <= i < len(candidates) for i in survivors)
             and len(set(survivors)) == len(survivors), "bad_slate_output", "invalid candidate/survivor shape")
    receipt, audit = output["slate_receipt"], source.get("audit_receipt")
    _require(receipt.get("candidate_facts_bound") is True and receipt.get("constraint_facts_bound") is True
             and receipt.get("comparison_facts_bound") is True,
             "receipt_upgrade_required", "re-run slate-evaluate to bind all candidate facts before generating a draft")
    _require(isinstance(audit, dict) and receipt.get("audit_fingerprint") == audit.get("fingerprint")
             and slate.saved_slate_fingerprint(source, output) == receipt["fingerprint"],
             "slate_output_inconsistent", "saved facts do not match the original slate and receipt; re-run slate-evaluate")


def claim_candidates(output: dict, selected: list[int]) -> list[dict]:
    """Only receipt-bound numerical facts; speed aggregate counts cannot create a speed claim."""
    result, seen = [], set()
    buckets = (("opponents_we_ohko_guaranteed", "ko_guaranteed", "we"),
               ("opponents_we_ohko_possible", "ko_possible", "we"),
               ("opponents_with_guaranteed_ohko_on_us", "ko_guaranteed", "them"))
    for index in selected:
        risk = output["candidates"][index].get("matchup_risk") or {}
        for bucket, key, direction in buckets:
            for rows in (risk.get(bucket) or {}).values():
                for row in rows:
                    eid = row.get("evidence_id")
                    parsed = slate.parse_evidence_id(eid)
                    if not parsed or parsed["kind"] != "damage":
                        continue
                    claim = {"team": index, "evidence_id": eid, "direction": direction, "expect": {key: 1},
                             **{k: row[k] for k in ("opponent_variant", "opponent_is_modal") if row.get(k) is not None},
                             "claim": f"In the evaluated static matchup, {parsed['attacker']} using {parsed['move']} "
                                      f"into {parsed['defender']} has calculator {key}=1 for the stated opponent build."}
                    identity = content_hash(claim, 24)
                    if identity not in seen:
                        result.append(claim)
                        seen.add(identity)
    return result
