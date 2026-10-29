"""The CLI and MCP share one operator-file contract: files contain the consumable result body."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def full_result_args(op: str, args: dict) -> dict:
    """A saved result is an evidence input; request its full body before projecting the display."""
    result = dict(args)
    if op in {"review", "try", "counters", "ko-scan", "slate-evaluate", "matchup", "diagnose"}:
        result["view"] = "full"
    return result


def execution_failed(row: Any) -> bool:
    if not isinstance(row, dict) or row.get("error") is not None or row.get("result") is None:
        return True
    result = row["result"]
    return (row.get("rc", 0) >= 2 or
            isinstance(result, dict) and (result.get("error") is not None or bool(result.get("refused"))))


def save_result(row: Any, target: str) -> dict:
    result = row.get("result") if isinstance(row, dict) else None
    text = json.dumps(result if result is not None else row, ensure_ascii=False, indent=2)
    path = Path(target).expanduser()
    path.write_text(text, encoding="utf-8")
    return {"op": row.get("op") if isinstance(row, dict) else None,
            "rc": row.get("rc") if isinstance(row, dict) else None,
            "out": str(path.resolve()), "bytes": len(text.encode("utf-8")),
            **({"artifacts": row["artifacts"]} if isinstance(row, dict) and row.get("artifacts") else {})}
