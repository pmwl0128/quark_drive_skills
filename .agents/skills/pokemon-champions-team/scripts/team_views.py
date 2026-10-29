"""Display projections only: retain qualifications and disclose every omitted evidence surface."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

COMMANDS = frozenset({"diagnose", "select", "oppmatrix", "topmeta", "frame", "tune", "intake", "counters", "ko-scan"})
LIMITED_COMMANDS = frozenset({"oppmatrix", "topmeta", "counters", "ko-scan"})


def coverage(value: dict) -> dict:
    """Keep species floors, witnesses, completeness and exceptional variant qualifications."""
    out = dict(value)
    for key in ("holes", "by_opponent", "contested"):
        if not isinstance(out.get(key), list):
            continue
        rows = []
        for source in out[key]:
            row = dict(source)
            variants = row.pop("variants", None)
            if isinstance(variants, list):
                row["variant_count"] = len(variants)
                row["variant_qualifiers"] = [
                    {k: v for k, v in variant.items()
                     if k in {"variant_id", "grade", "c0_kind", "wall_no_ko_by", "contested_by"}}
                    for variant in variants if any(variant.get(k) for k in
                                                  ("c0_kind", "wall_no_ko_by", "contested_by"))]
            rows.append(row)
        out[key] = rows
    return out


def project(value: Any, command: str, limit: int = 6) -> Any:
    if not isinstance(value, dict) or value.get("ok") is False or value.get("refused"):
        return value
    out = deepcopy(value)
    omitted = []

    def drop(obj, key, path):
        if key in obj:
            old = obj.pop(key)
            omitted.append({"path": path + "/" + key,
                            "count": len(old) if isinstance(old, (list, dict)) else 1})

    def cap(obj, key, path):
        old = obj.get(key)
        if isinstance(old, (list, dict)) and len(old) > limit:
            obj[key] = old[:limit] if isinstance(old, list) else dict(list(old.items())[:limit])
            omitted.append({"path": path + "/" + key, "count": len(old) - limit,
                            "retained": limit, "total": len(old)})

    def compact_coverage(obj, key, path):
        if isinstance(obj.get(key), dict):
            original = obj[key]
            obj[key] = coverage(original)
            # Holes repeat complete by_opponent rows. Keep one qualified row and an exact pointer.
            opponents = original.get("by_opponent") or []
            for subset in ("holes", "contested"):
                for i, row in enumerate(original.get(subset) or []):
                    index = next((j for j, candidate in enumerate(opponents) if candidate == row), None)
                    if index is not None:
                        obj[key][subset][i] = {"opponent": row.get("opponent"),
                                               "details_ref": f"{path}/{key}/by_opponent/{index}"}
            omitted.append({"path": path + "/" + key + "/{holes,by_opponent,contested}/*/variants",
                            "replacement": "variant_count + exceptional variant_qualifiers; species floors unchanged"})
            omitted.append({"path": path + "/" + key + "/{holes,contested}",
                            "replacement": "identical by_opponent rows use details_ref JSON Pointers into this summary"})

    if command == "ko-scan":
        drop(out.get("built_for", {}), "calculation_authority", "/built_for")
        for mode, panel in out.get("panels", {}).items():
            path = "/panels/" + mode
            cap(panel, "missing", path)
            drop(panel, "outside_scan", path)
            for i, group in enumerate(panel["groups"]):
                cap(group, "rows", path + f"/groups/{i}")
                for j, row in enumerate(group["rows"]):
                    # Group qualifiers are exact, so removing their identical per-row copy is lossless.
                    for key in group["qualifications"]:
                        drop(row["damage"], key, path + f"/groups/{i}/rows/{j}/damage")
        omitted.append({"path": "/panels/*/groups/*/rows/*/damage", "replacement": "mechanical qualifications remain in each row's containing group"})
    elif command == "counters":
        drop(out.get("built_for", {}), "calculation_authority", "/built_for")
        drop(out.get("coverage", {}), "unanswered_variant_ids", "/coverage")
        for group in ("rows", "conditional_rows", "incomplete_rows"):
            cap(out, group, "")
            for i, row in enumerate(out.get(group) or []):
                for key in ("answers", "blocked_by"):
                    for j, pair in enumerate(row[key]):
                        # Keep every target build and qualifier in a displayed row, including C0 blockers.
                        check = pair.get("check") or {}
                        pair["check"] = {k: v for k, v in check.items() if k in {
                            "grade", "c1_mode", "c0_kind", "lines", "resolvability", "caveats", "caveat_details",
                            "intimidate_lane", "priority_lane"}}
                        omitted.append({"path": f"/{group}/{i}/{key}/{j}/check", "replacement": "grade, lines and all caveats/soft lanes retained"})
    elif command == "diagnose":
        check = out.get("check_coverage")
        if isinstance(check, dict):
            drop(check, "members", "/check_coverage")
            compact_coverage(check, "check_coverage", "/check_coverage")
    elif command == "select":
        for i, combo in enumerate(out.get("combos") or []):
            compact_coverage(combo, "check_coverage", f"/combos/{i}")
    elif command == "frame":
        for i, skeleton in enumerate(out.get("skeletons") or []):
            drop(skeleton, "structural_profile", f"/skeletons/{i}")
            for j, core in enumerate(skeleton.get("core_candidates") or []):
                # primary/cluster grounding remains intact; set_guidance repeats the primary set.
                drop(core, "set_guidance", f"/skeletons/{i}/core_candidates/{j}")
    elif command == "topmeta":
        cap(out, "ranking", "")
        species = out.get("species") or {}
        if len(species) > limit:
            # The counts table is name-sorted; displayed panels must belong to the displayed ranks.
            ordered = [r["name"] for r in out.get("ranking") or [] if r.get("name") in species]
            ordered.extend(name for name in species if name not in ordered)
            out["species"] = {name: species[name] for name in ordered[:limit]}
            omitted.append({"path": "/species", "count": len(species) - limit,
                            "retained": limit, "total": len(species)})
        for name, species in (out.get("species") or {}).items():
            escaped = name.replace("~", "~0").replace("/", "~1")
            for panel, facts in (species.get("panels") or {}).items():
                if isinstance(facts, dict):
                    cap(facts, "entries", f"/species/{escaped}/panels/{panel}")
    elif command == "tune":
        for i, card in enumerate(out.get("cards") or []):
            # Requested result, KO tiers, assumptions, funding and nature branches stay complete.
            drop(card, "probability_lanes", f"/cards/{i}")
    elif command == "oppmatrix":
        # Raw and CHECK grids are alternative views. Do not send both copies of the same square.
        if out.get("kind") != "opponent-build-checks":
            drop(out, "check_matrix", "")
        cap(out, "summaries", "")
        for key, facts in (out.get("summaries") or {}).items():
            out["summaries"][key] = coverage(facts)
        for table in ("matrix", "row"):
            cap(out, table, "")
            if table == "matrix":
                for key, row in (out.get(table) or {}).items():
                    if isinstance(row, dict) and len(row) > limit:
                        out[table][key] = dict(list(row.items())[:limit])
                        omitted.append({"path": "/matrix/" + key.replace("~", "~0").replace("/", "~1"),
                                        "count": len(row) - limit, "retained": limit, "total": len(row)})
        # Set catalog retains complete concrete configurations for every displayed pair and witness.
        for key, facts in (out.get("summaries") or {}).items():
            omitted.append({"path": "/summaries/" + key.replace("~", "~0").replace("/", "~1") + "/variants",
                            "replacement": "species floors and exceptional variant qualifications"})
    elif command == "intake" and out.get("mode") != "onboarding_step":
        from intake import ONBOARDING_GROUPS, _STEP_NOTES
        covered = {q["id"] for q in out.get("already_answered") or []}
        questions = {q["id"]: q for q in out.get("questions") or []}
        pending = [qid for _, ids in ONBOARDING_GROUPS for qid in ids if qid not in covered]
        group, ids = next(((name, [qid for qid in ids if qid in pending])
                           for name, ids in ONBOARDING_GROUPS if any(qid in pending for qid in ids)), (None, []))
        batch = [questions[qid] for qid in ids]
        for q in batch:
            for n, option in enumerate(q.get("options") or [], 1):
                option["n"] = n
        for key in ("questions", "guided_walk", "resolver_vocabulary"):
            drop(out, key, "")
        out["notes"] = list(_STEP_NOTES)
        out.update(mode="onboarding_step", group=group, batch=batch, done=not pending,
                   answered=[], unknown_answered=[], remaining_after=len(pending) - len(ids))
        out["next_step"] = "Apply each answer's maps_to; pass resolved question ids with intake --next --answered."
    out["view"] = "summary"
    out["projection"] = {"display_only": True, "omitted": omitted, "limit": limit,
                         "detail": "Tables retain source order, without ranking candidates. Read the full --out artifact for omitted evidence; summary cannot replace a receipt input."}
    return out
