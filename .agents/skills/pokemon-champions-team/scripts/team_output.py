"""Team JSON emission: save the complete artifact, then render its optional display view."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
from typing import Any, Callable

import team_i18n as i18n


@dataclass
class Output:
    out: str | None = None
    command: str | None = None
    view: str | None = None
    limit: int = 6
    full: Any = None
    emitted: bool = False


_output: ContextVar[Output | None] = ContextVar("team_output", default=None)


class OutputError(OSError):
    pass


@contextmanager
def output_options(out: str | None = None, *, command: str | None = None,
                   view: str | None = None, limit: int = 6):
    state = Output(out=out, command=command, view=view, limit=limit)
    token = _output.set(state)
    try:
        yield state
    finally:
        _output.reset(token)


def output_path() -> str | None:
    state = _output.get()
    return state.out if state is not None else None


def write_json(path: str, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=target.parent, prefix=f".{target.name}.",
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def protect_inputs(out: str | None, inputs: list[str]) -> None:
    if out and Path(out).resolve() in {Path(p).resolve() for p in inputs if isinstance(p, str)}:
        raise ValueError("--out must differ from every input file; preserve the original input and receipts")


def emit_json(value: Any, *, view: str = "full",
              projector: Callable[[Any, str], Any] | None = None) -> None:
    value = i18n.jsonify(value)
    state = _output.get()
    if state is not None:
        if state.emitted:
            raise ValueError("an operator must emit exactly one JSON result")
        if state.out:
            try:
                write_json(state.out, value)
            except OSError as exc:
                raise OutputError(f"cannot write --out: {exc}") from exc
        state.full, state.emitted = value, True
    view = state.view if state is not None and state.view is not None else view
    if projector is not None:
        display = projector(value, view)
    elif state is not None and view == "summary":
        from team_views import project
        display = project(value, state.command, state.limit)
    else:
        display = value
    print(json.dumps(display, ensure_ascii=False, indent=2))
