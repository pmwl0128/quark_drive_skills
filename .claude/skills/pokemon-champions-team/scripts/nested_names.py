"""Resolve typed name leaves in working copies, never prose or receipt-bound source objects."""
from __future__ import annotations

from copy import deepcopy
from typing import Callable


def normalize(value: dict, surface: str, resolve_fn: Callable) -> tuple[dict, list[dict], list[dict]]:
    out = deepcopy(value)
    slots = []

    def rows(key):
        v = out.get(key)
        return v if isinstance(v, list) else []

    def leaf(obj, key, kind, path):
        if isinstance(obj, (dict, list)):
            raw = obj.get(key) if isinstance(obj, dict) else obj[key]
            if isinstance(raw, str) and raw.strip():
                slots.append((obj, key, kind, raw, path))

    def member(obj, path, *, species=True):
        if not isinstance(obj, dict):
            return
        if species:
            leaf(obj, "species", "pokemon", path + ".species")
        for key in ("item", "ability", "nature"):
            leaf(obj, key, key, path + "." + key)
        if isinstance(obj.get("moves"), list):
            for i in range(len(obj["moves"])):
                leaf(obj["moves"], i, "move", f"{path}.moves[{i}]")

    if surface == "context":
        for i, goal in enumerate(rows("goals")):
            if isinstance(goal, dict):
                leaf(goal, "species", "pokemon", f"goals[{i}].species")
        need = out.get("need")
        if isinstance(need, dict):
            answer = need.get("answer")
            if isinstance(answer, list):
                for i, name in enumerate(answer):
                    if isinstance(name, str) and not name.startswith("variant:"):
                        leaf(answer, i, "pokemon", f"need.answer[{i}]")
            elif isinstance(answer, str) and not answer.startswith("variant:"):
                leaf(need, "answer", "pokemon", "need.answer")
        for i, b in enumerate(rows("benchmarks")):
            if not isinstance(b, dict):
                continue
            path = f"benchmarks[{i}]"
            leaf(b, "member", "pokemon", path + ".member")
            # Numeric speed targets are coordinates, not dex names.
            vs = b.get("vs")
            numeric_speed = b.get("kind") == "outspeed" and isinstance(vs, str) and vs.strip().lstrip("+").isdigit()
            if not numeric_speed:
                leaf(b, "vs", "pokemon", path + ".vs")
            leaf(b, "move", "move", path + ".move")
            for key in ("opponent_set", "attacker_set"):
                member(b.get(key), path + "." + key, species=False)
        repl = out.get("replace")
        if isinstance(repl, dict):
            leaf(repl, "member", "pokemon", "replace.member")
            member(repl.get("with"), "replace.with")
    elif surface == "focus":
        for key in ("members", "opponents"):
            for i in range(len(rows(key))):
                leaf(out[key], i, "pokemon", f"{key}[{i}]")
    elif surface == "changes":
        for i, change in enumerate(rows("changes")):
            if isinstance(change, dict):
                member(change.get("set"), f"changes[{i}].set")
    elif surface == "binding":
        if isinstance(out.get("off_meta"), list):
            for i in range(len(out["off_meta"])):
                leaf(out["off_meta"], i, "pokemon", f"off_meta[{i}]")
        for i, row in enumerate(rows("deviations")):
            if isinstance(row, dict):
                leaf(row, "species", "pokemon", f"deviations[{i}].species")
    elif surface == "draft":
        for i, row in enumerate(rows("frame_deviations")):
            if isinstance(row, dict):
                leaf(row, "species", "pokemon", f"frame_deviations[{i}].species")
    else:
        raise ValueError(f"unknown name surface: {surface}")

    # Finish every batch before changing even the copy: an interrupted resolver cannot leave a
    # partially normalized input with an incomplete correction ledger.
    resolved = {}
    for kind in sorted({slot[2] for slot in slots}):
        names = sorted({raw for _, _, k, raw, _ in slots if k == kind})
        resolved[kind] = resolve_fn(names, kind, fuzzy=True)
    flags, errors = [], []
    for obj, key, kind, raw, path in slots:
        rec = (resolved.get(kind) or {}).get(raw) or {}
        if rec.get("ok") and rec.get("canonical"):
            obj[key] = rec["canonical"]
            if rec.get("match_type") == "fuzzy":
                flags.append({"kind": kind, "path": path, "from": raw, "to": rec["canonical"],
                              "match_type": "fuzzy", "score": rec.get("score"), "distance": rec.get("distance")})
        else:
            errors.append({"path": path, "query": raw, "kind": kind,
                           "match_type": rec.get("match_type"), "suggestions": rec.get("suggestions") or []})
    return out, flags, errors
