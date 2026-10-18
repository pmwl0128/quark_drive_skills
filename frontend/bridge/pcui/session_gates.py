"""Which team operator runs are UEP gate artifacts (frontend/design.md §6).

`pcui run <op> --session S` lets the daemon submit them as it runs the operator, so the assistant
does one step per gate instead of saving each output and putting it again. Inputs are stored as the
exact bytes the operator read (the bridge materializes inline JSON into a file); outputs as the
operator's JSON. The panel's slate is the summary view, while the assistant still receives the full
output, which answer-audit needs."""
from __future__ import annotations

import json
import sys

from .mappers import _TEAM_SCRIPTS

# op -> artifacts it produces, in ledger order: ("input", field) is an inline file the op read,
# ("output", None) its result.
GATES: dict[str, tuple[tuple[str, str, str | None], ...]] = {
    "context-audit": (("context", "input", "context"), ("audit", "output", None)),
    "frame": (("frame", "output", None),),
    "slate-evaluate": (("slate", "output", None),),
    "checkpoint": (("checkpoint", "output", None),),
    "answer-audit": (("draft", "input", "file"), ("answer-audit", "output", None)),
    "intake": (("intake", "output", None),),
    "tune": (("tune-result", "output", None),),
    # The raw diagnosis does not carry the team, which the panel's report needs beside it.
    "diagnose": (("team", "input", "file"), ("diagnose", "output", None)),
}
_ALIASES = {"context_audit": "context-audit", "slate_evaluate": "slate-evaluate",
            "answer_audit": "answer-audit"}


def gate_name(op: dict) -> str | None:
    name = op.get("op")
    name = _ALIASES.get(name, name)
    if name not in GATES:
        return None
    # Only the batch driver is a question set the panel can answer; the catalog and the onboarding
    # overview are the assistant's own references.
    if name == "intake" and not op.get("next"):
        return None
    return name


def _slate_summary(result):
    if str(_TEAM_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_TEAM_SCRIPTS))
    import slate  # noqa: E402  (the team skill's own projection, so the two can never drift)
    return slate.project_slate(result, "summary")


def gate_artifacts(op: dict, inputs: dict[str, bytes], row: dict) -> list[tuple[str, bytes]]:
    """The (kind, payload) pairs one finished operator row contributes; none for a crash or an
    operator that printed nothing. A refusing or failing operator still reports JSON (a failed
    answer-audit is exactly what the panel must show), so the exit code does not gate this."""
    name = gate_name(op)
    if name is None or not isinstance(row, dict) or row.get("error") is not None:
        return []
    result = row.get("result")
    if not isinstance(result, (dict, list)):
        return []
    out: list[tuple[str, bytes]] = []
    for kind, source, field in GATES[name]:
        if source == "input":
            if field in inputs:
                out.append((kind, inputs[field]))
            continue
        value = _slate_summary(result) if name == "slate-evaluate" else result
        out.append((kind, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")))
    return out
