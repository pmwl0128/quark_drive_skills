"""Inline session inputs and backward-only JSON Pointer references to complete prior results."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any

FILE_FIELDS = {"file", "context", "audit_receipt", "frame_output", "intake_output", "seen_teams", "slate", "slate_output", "set", "conditions", "changes", "focus", "opponents"}
NEGATIVE_VERDICTS = {"needs_input", "no_frames", "no_survivors", "pause", "fail", "refused"}


def result_failed(result: Any) -> bool:
    return isinstance(result, dict) and bool(
        result.get("refused") or result.get("pass") is False or result.get("ok") is False
        or "error" in result or result.get("verdict") in NEGATIVE_VERDICTS)


def resolve_refs(value: Any, results: list[dict]) -> Any:
    if isinstance(value, list):
        return [resolve_refs(item, results) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" not in value:
        return {key: resolve_refs(item, results) for key, item in value.items()}
    pointer = value.get("$ref")
    if len(value) != 1 or not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError('$ref must be the sole key with a JSON Pointer such as "/0/result/audit_receipt"')
    if re.search(r"~(?![01])", pointer):
        raise ValueError("invalid JSON Pointer escape; use ~0 for ~ and ~1 for /")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    index = parts[0]
    if not index.isascii() or not index.isdigit() or str(int(index)) != index or int(index) >= len(results):
        raise ValueError("$ref must target an already completed operation, never a forward reference")
    source = results[int(index)]
    result = source.get("result")
    if source.get("error") or source.get("rc", 1) != 0 or result_failed(result):
        raise ValueError(f"$ref dependency {index} failed; its output cannot supply an input")
    selected: Any = source
    for part in parts[1:]:
        if isinstance(selected, list):
            if not part.isascii() or not part.isdigit() or str(int(part)) != part or int(part) >= len(selected):
                raise ValueError(f"$ref array index is missing: {pointer}")
            selected = selected[int(part)]
        elif isinstance(selected, dict) and part in selected:
            selected = selected[part]
        else:
            raise ValueError(f"$ref target is missing: {pointer}")
    return deepcopy(selected)


def materialize_inputs(command: dict, directory: Path, index: int) -> dict:
    command = dict(command)
    for field in FILE_FIELDS:
        value = command.get(field)
        if isinstance(value, (dict, list)):
            path = directory / f"{index}-{field}.json"
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            command[field] = str(path)
        elif field == "file" and isinstance(value, str) and "\n" in value:
            path = directory / f"{index}-team.txt"
            path.write_text(value, encoding="utf-8")
            command[field] = str(path)
    if isinstance(command.get("where"), (dict, list)):
        command["where"] = json.dumps(command["where"], ensure_ascii=False)
    return command
