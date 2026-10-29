#!/usr/bin/env python
"""Portable case validation and CLI for the shared set-inference model."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from ncp_cli_runtime import emit_json

SCHEMA = {
    "skill": "ncp-damage-calculator",
    "cli": "ncp-infer-case.py",
    "commands": {
        "init": "--input <JSON file> [--prior <meta inference-prior JSON file>] [--out <case JSON file>] -> ncp_inference_case.v1; JSON may also come from stdin",
        "import-web": "--input <quark_drive_web_inference.v1 JSON> --prior <matching meta prior> [--foe-id <roster uid>] [--out <case>] -> {case,status,imported}; requires frozen snapshots, preserves disabled hits and per-hit formats",
        "add": "--input <case> --observation <hit JSON> [--out <new case>] -> {case,status,next_step}; freezes the hit and validates engine support before saving",
        "status": "--input <case> [--predict] [--mine INDEX] -> recomputed result, speed bounds and optional per-move posterior readouts/information; input move order preserved, no recommendation",
        "remove": "--input <case> --id <observation id> [--out <new case>] -> {case,status,next_step}",
        "example": "--input <case> -> a surviving example only; never writes a case or team",
        "schema": "this contract",
    },
    "init_input": {
        "format": "single|double",
        "foe": "{name:<dex canonical battle form>}",
        "mine_team": "1..6 current calculator members, each {name,moves:[0..4 names],item?,ability?,nature?,sps?}; incomplete current builds do not block frozen evidence; add still requires a complete frozen member",
        "prior": "meta inference_prior.v1 object; alternatively pass --prior; same foe and format required",
        "candidates": "optional {natures?,items?,abilities?:[canonical names]}; explicit axes replace that axis' prior cut later",
        "known": "optional {item?,ability?} confirmed facts; does not replace the prior snapshot",
    },
    "observation": {"id": "unique nonempty string", "kind": "bulk (our hit, foe integer HP%) | offense (foe hit, our exact HP)",
                    "mine": "zero-based mine_team index", "move": "canonical move name", "before/after": "integer HP readings, before > 0, 0 <= after <= before",
                    "mine_state/foe_state": "optional {boosts,status,abilityOn,curHP}; post-entry state, no implicit Intimidate",
                    "field": "optional {format,weather,terrain,gravity,foresight,mineSide,foeSide}; format defaults to case format; side flags use calculator snake_case names",
                    "crit/single_target/enabled": "optional booleans; enabled defaults true, disabled hits are retained but not evaluated"},
    "web_input": "quark_drive_web_inference.v1 {schema,roster:<pc-calc-roster-v1>,records:<pc-calc-infer-v1>}; foe defaults to roster.active.b; browser storage contains no prior, so --prior is explicit and its snapshot must be checked before claiming parity",
    "prediction_observation": "predictions[].observation: state ready|pending|unavailable; ready carries basis surviving_joint_state_weights, readout, before, readings[{after,probability}], readingEntropyBits, expectedNoiseBits, informationGainBits. Gain = entropy(readout) minus expected random-roll entropy. CLI uses full-HP foe, conditional hit; probabilities sum to 1 within the stated model, not calibrated battle frequencies. possibleAfters remains an initial support set.",
    "projection_frame": "basis:last_matching_observation|current_member_neutral_field|unavailable; unavailable carries reason:incomplete_current_member and missing fields. Frozen evidence still evaluates; new predictions are omitted and speed comparison is incomplete until that current member is complete.",
    "output": "ncp_inference_case.v1 carries frozen inputs and observations only; status is recomputed, never authoritative cached inference",
    "runtime": "add/status/example require quickjs-ng and the sibling dex for canonical facts; no meta runtime dependency",
    "error_shape": {"ok": False, "query": "<field>", "error": {"code": "bad_input", "message": "<str>"}},
}


class CaseInputError(ValueError):
    def __init__(self, query: str, message: str, code: str = "bad_input"):
        super().__init__(message)
        self.query = query
        self.code = code


def _object(value: Any, query: str) -> dict:
    if not isinstance(value, dict):
        raise CaseInputError(query, "expected an object")
    return value


def _name(value: Any, query: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CaseInputError(query, "expected a nonempty canonical name")
    return value


def _only(obj: dict, allowed: set[str], query: str) -> None:
    unknown = set(obj) - allowed
    if unknown:
        raise CaseInputError(query, f"unsupported fields: {', '.join(sorted(unknown))}")


def _member(raw: Any, index: int, *, current: bool = False) -> dict:
    path = f"mine_team[{index}]"
    row = _object(raw, path)
    _only(row, {"name", "moves", "item", "ability", "nature", "sps", "boosts", "status", "abilityOn", "curHP"}, path)
    _name(row.get("name"), f"{path}.name")
    moves = row.get("moves")
    if not isinstance(moves, list) or not (0 if current else 1) <= len(moves) <= 4:
        raise CaseInputError(f"{path}.moves", "expected zero to four move names" if current else "expected one to four move names")
    for j, move in enumerate(moves):
        _name(move, f"{path}.moves[{j}]")
    if len(set(moves)) != len(moves):
        raise CaseInputError(f"{path}.moves", "duplicate moves are not a usable hit source")
    for key in ("item", "ability", "nature", "status"):
        if key in row and (not isinstance(row[key], str) or row[key] != row[key].strip()):
            raise CaseInputError(f"{path}.{key}", "expected a string")
    if row.get("status", "Healthy") not in ("Healthy", "Burned", "Paralyzed", "Poisoned",
                                            "Badly Poisoned", "Asleep", "Frozen"):
        raise CaseInputError(f"{path}.status", "unknown calculator status")
    if "abilityOn" in row and not isinstance(row["abilityOn"], bool):
        raise CaseInputError(f"{path}.abilityOn", "expected a boolean")
    if "curHP" in row and (type(row["curHP"]) is not int or row["curHP"] <= 0):
        raise CaseInputError(f"{path}.curHP", "expected a positive integer HP value")
    sps = _object(row.get("sps", {}), f"{path}.sps")
    _only(sps, {"hp", "atk", "def", "spa", "spd", "spe"}, f"{path}.sps")
    for stat, amount in sps.items():
        if type(amount) is not int or not 0 <= amount <= 32:
            raise CaseInputError(f"{path}.sps.{stat}", "SP must be an integer from 0 to 32")
    if sum(sps.values()) > 66:
        raise CaseInputError(f"{path}.sps", "SP total exceeds 66")
    boosts = _object(row.get("boosts", {}), f"{path}.boosts")
    _only(boosts, {"atk", "def", "spa", "spd", "spe"}, f"{path}.boosts")
    for stat, stage in boosts.items():
        if type(stage) is not int or not -6 <= stage <= 6:
            raise CaseInputError(f"{path}.boosts.{stat}", "stage must be an integer from -6 to 6")
    return row


def _prior(raw: Any, foe: str, fmt: str) -> dict:
    prior = _object(raw, "prior")
    if prior.get("schema") != "inference_prior.v1":
        raise CaseInputError("prior.schema", "expected meta inference_prior.v1")
    if prior.get("pokemon") != foe or prior.get("format") != fmt:
        raise CaseInputError("prior", "prior Pokemon and format must match this case", "authority_mismatch")
    for key in ("season", "rule", "base_species", "updated_at"):
        _name(prior.get(key), f"prior.{key}")
    if prior.get("source") != "in_game_species_detail" or prior.get("panel_limit") != 10 or prior.get("min_percentage") != 1:
        raise CaseInputError("prior", "unsupported inference prior scope", "authority_mismatch")
    panels = _object(prior.get("panels"), "prior.panels")
    for panel in ("natures", "items", "abilities"):
        rows = panels.get(panel)
        if not isinstance(rows, list) or len(rows) > 10:
            raise CaseInputError(f"prior.panels.{panel}", "expected at most ten entries")
        seen = set()
        for index, row in enumerate(rows):
            path = f"prior.panels.{panel}[{index}]"
            entry = _object(row, path)
            name = _name(entry.get("name"), f"{path}.name")
            percentage, rank = entry.get("percentage"), entry.get("rank")
            if name in seen or type(rank) is not int or not 1 <= rank <= 10:
                raise CaseInputError(path, "duplicate name or invalid panel rank")
            if (type(percentage) not in (int, float) or not 1 <= percentage <= 100):
                raise CaseInputError(f"{path}.percentage", "expected source percentage from 1 to 100")
            seen.add(name)
    return prior


def init_case(raw: Any, prior_file: Any = None) -> dict:
    spec = _object(raw, "input")
    _only(spec, {"format", "foe", "mine_team", "prior", "known", "candidates"}, "input")
    fmt = spec.get("format")
    if fmt not in ("single", "double"):
        raise CaseInputError("format", "expected single or double")
    foe = _object(spec.get("foe"), "foe")
    _only(foe, {"name"}, "foe")
    name = _name(foe.get("name"), "foe.name")
    team = spec.get("mine_team")
    if not isinstance(team, list) or not 1 <= len(team) <= 6:
        raise CaseInputError("mine_team", "expected one to six members")
    team = [_member(member, index, current=True) for index, member in enumerate(team)]
    known = _object(spec.get("known", {}), "known")
    _only(known, {"item", "ability"}, "known")
    for key, value in known.items():
        _name(value, f"known.{key}")
    candidates = _object(spec.get("candidates", {}), "candidates")
    _only(candidates, {"natures", "items", "abilities"}, "candidates")
    for axis, names in candidates.items():
        if not isinstance(names, list) or not names:
            raise CaseInputError(f"candidates.{axis}", "expected a nonempty list of names")
        for index, value in enumerate(names):
            _name(value, f"candidates.{axis}[{index}]")
        if len(set(names)) != len(names):
            raise CaseInputError(f"candidates.{axis}", "duplicate candidate name")
    if "prior" in spec and prior_file is not None:
        raise CaseInputError("prior", "give the prior inline or through --prior, not both")
    prior = _prior(spec.get("prior", prior_file), name, fmt)
    return {"schema": "ncp_inference_case.v1", "format": fmt,
            "season": prior["season"], "rule": prior["rule"],
            "foe": foe, "mine_team": team, "known": known, "candidates": candidates,
            "prior": prior, "observations": []}


def _state(raw: Any, path: str) -> dict:
    state = _object(raw, path)
    _only(state, {"boosts", "status", "abilityOn", "curHP"}, path)
    _member({"name": "state", "moves": ["state"], **state}, 0)
    return deepcopy(state)


def _field(raw: Any) -> dict:
    field = _object(raw, "field")
    _only(field, {"format", "weather", "terrain", "gravity", "foresight", "mineSide", "foeSide"}, "field")
    if "format" in field and field["format"] not in ("single", "double"):
        raise CaseInputError("field.format", "expected single or double")
    for flag in ("gravity", "foresight"):
        if flag in field and type(field[flag]) is not bool:
            raise CaseInputError(f"field.{flag}", "expected a boolean")
    if field.get("weather", "") not in ("", "Rain", "Sun", "Sand", "Snow"):
        raise CaseInputError("field.weather", "unsupported weather")
    if field.get("terrain", "") not in ("", "Electric", "Grassy", "Psychic", "Misty"):
        raise CaseInputError("field.terrain", "unsupported terrain")
    for side in ("mineSide", "foeSide"):
        flags = _object(field.get(side, {}), f"field.{side}")
        _only(flags, {"reflect", "light_screen", "aurora_veil", "helping_hand", "battery",
                      "friend_guard", "power_spot", "tailwind", "swamp", "steely_spirit", "charge",
                      "stealth_rock"}, f"field.{side}")
        if any(type(v) is not bool for v in flags.values()):
            raise CaseInputError(f"field.{side}", "side flags must be booleans")
    return deepcopy(field)


def observation(raw: Any, case: dict, *, frozen: bool = False) -> dict:
    row = _object(raw, "observation")
    allowed = {"id", "kind", "mine", "move", "before", "after", "mine_state", "foe_state", "field",
               "crit", "single_target", "enabled"}
    _only(row, allowed | ({"mine_snapshot", "foe_snapshot"} if frozen else set()), "observation")
    _name(row.get("id"), "observation.id")
    _name(row.get("move"), "observation.move")
    if row.get("kind") not in ("bulk", "offense"):
        raise CaseInputError("observation.kind", "expected bulk or offense")
    index = row.get("mine")
    if type(index) is not int or index < 0 or (not frozen and index >= len(case["mine_team"])):
        raise CaseInputError("observation.mine", "member index is out of range")
    before, after = row.get("before"), row.get("after")
    if type(before) is not int or type(after) is not int or not 0 <= after <= before or before <= 0:
        raise CaseInputError("observation", "expected integer HP readings with 0 <= after <= before and before > 0")
    if row["kind"] == "bulk" and before > 100:
        raise CaseInputError("observation.before", "opponent HP is an integer percentage from 1 to 100")
    for flag in ("crit", "single_target", "enabled"):
        if flag in row and type(row[flag]) is not bool:
            raise CaseInputError(f"observation.{flag}", "expected a boolean")
    mine_state = _state(row.get("mine_state", {}), "mine_state")
    foe_state = _state(row.get("foe_state", {}), "foe_state")
    out = {**deepcopy(row), "field": _field(row.get("field", {})),
           "mine_state": mine_state, "foe_state": foe_state}
    if frozen:
        _member(row.get("mine_snapshot"), index)
        foe = _object(row.get("foe_snapshot"), "foe_snapshot")
        _only(foe, {"name", "boosts", "status", "abilityOn", "curHP"}, "foe_snapshot")
        _name(foe.get("name"), "foe_snapshot.name")
        _state({k: v for k, v in foe.items() if k != "name"}, "foe_snapshot")
        if any(row["mine_snapshot"].get(k) != v for k, v in mine_state.items()) or any(
                foe.get(k) != v for k, v in foe_state.items()):
            raise CaseInputError("observation", "snapshot and recorded battle state disagree", "authority_mismatch")
    else:
        out["mine_snapshot"] = {**deepcopy(case["mine_team"][index]), **mine_state}
        out["foe_snapshot"] = {**deepcopy(case["foe"]), **foe_state}
    return out


def load_case(raw: Any) -> dict:
    row = _object(raw, "case")
    _only(row, {"schema", "format", "season", "rule", "foe", "mine_team", "known", "candidates",
                "prior", "observations"}, "case")
    if row.get("schema") != "ncp_inference_case.v1":
        raise CaseInputError("case.schema", "unsupported inference case version")
    case = init_case({k: v for k, v in row.items() if k not in {"schema", "season", "rule", "observations"}})
    if any(case[key] != row.get(key) for key in ("season", "rule")):
        raise CaseInputError("case", "case season/rule must match the stored prior", "authority_mismatch")
    hits = row.get("observations")
    if not isinstance(hits, list):
        raise CaseInputError("observations", "expected a list")
    case["observations"] = [observation(hit, case, frozen=True) for hit in hits]
    if len({hit["id"] for hit in hits}) != len(hits):
        raise CaseInputError("observations", "duplicate observation ids")
    return case


def _read(path: str | None) -> Any:
    text = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    return json.loads(text)


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                                     prefix=f".{path.name}.", suffix=".tmp", delete=False) as temp:
        name = temp.name
        try:
            json.dump(obj, temp, ensure_ascii=False, indent=2)
            temp.write("\n")
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
    try:
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Record and replay an opponent set-inference case")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("schema")
    init = sub.add_parser("init")
    init.add_argument("--input", help="case input JSON file; omit for stdin")
    init.add_argument("--prior", help="meta inference-prior JSON file; omit when input embeds prior")
    init.add_argument("--out", help="atomically save the case JSON")
    web = sub.add_parser("import-web")
    web.add_argument("--input", help="browser storage export; omit for stdin")
    web.add_argument("--prior", required=True, help="matching meta inference-prior JSON")
    web.add_argument("--foe-id", help="opponent roster uid; defaults to active opponent")
    web.add_argument("--out", help="atomically save the canonical case")
    for command in ("add", "status", "remove", "example"):
        child = sub.add_parser(command)
        child.add_argument("--input", help="case JSON file; omit for stdin")
        if command in {"add", "remove"}:
            child.add_argument("--out", help="save a new case, preserving the input")
        if command == "add":
            child.add_argument("--observation", required=True)
        if command == "remove":
            child.add_argument("--id", required=True)
        if command == "status":
            child.add_argument("--predict", action="store_true")
            child.add_argument("--mine", type=int, default=0)
    args = parser.parse_args()
    if args.command == "schema":
        emit_json(SCHEMA)
        return 0
    try:
        out, prior_path = getattr(args, "out", None), getattr(args, "prior", None)
        inputs = (args.input, prior_path, getattr(args, "observation", None))
        if out and Path(out).resolve() in {Path(p).resolve() for p in inputs if p}:
            raise CaseInputError("out", "output may not overwrite an input file")
        if args.command == "init":
            case = init_case(_read(args.input), _read(prior_path) if prior_path else None)
            result = case
        elif args.command == "import-web":
            from inference_web import import_web
            from ncp_inference import InferenceRuntime
            case, imported = import_web(_read(args.input), _read(prior_path), args.foe_id)
            status = InferenceRuntime(case).evaluate()
            result = {"case": case, "status": status, "imported": imported}
        else:
            from ncp_inference import InferenceRuntime, guidance
            case = load_case(_read(args.input))
            if args.command == "add":
                hit = observation(_read(args.observation), case)
                if any(hit["id"] == h["id"] for h in case["observations"]):
                    raise CaseInputError("observation.id", "this id already exists")
                case["observations"].append(hit)
            elif args.command == "remove":
                if not any(h["id"] == args.id for h in case["observations"]):
                    raise CaseInputError("id", "observation id not found")
                case["observations"] = [h for h in case["observations"] if h["id"] != args.id]
            runtime = InferenceRuntime(case)
            status = runtime.evaluate(predict=getattr(args, "predict", False), mine=getattr(args, "mine", 0))
            if args.command == "add":
                rejected = next((r for r in status["unsupported"] if r["id"] == hit["id"]), None)
                if rejected:
                    raise CaseInputError("observation", rejected["reason"], "unsupported_mechanic")
            if args.command == "example":
                result = {"schema": "ncp_inference_example.v1", "example": status["mostLikely"],
                          "state": status["state"], "basis": "surviving_model_state_not_confirmed_set",
                          "next_step": guidance(status)}
            elif args.command == "status":
                result = status
            else:
                result = {"case": case, "status": status, "next_step": guidance(status)}
        if out:
            _write(Path(out), case)
    except (CaseInputError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        query = exc.query if isinstance(exc, CaseInputError) else "input"
        code = exc.code if isinstance(exc, CaseInputError) else "bad_input"
        emit_json({"ok": False, "query": query, "error": {"code": code, "message": str(exc)}})
        return 1
    emit_json(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
