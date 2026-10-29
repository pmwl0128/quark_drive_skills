"""Public NCP speedline transport, independent of the damage-cache caller identity."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from ncplink import NcpUnavailable
import worker

SCRIPTS = Path(__file__).resolve().parents[2] / "ncp-damage-calculator/scripts"
PY = SCRIPTS / "ncp-speedline-api.py"
JS = SCRIPTS / "ncp-speedline-api.js"


def speed_batch(requests: list[dict]) -> list[dict]:
    """Return one result/error per speed point, reusing a resident worker during sessions."""
    if not requests:
        return []
    payload = json.dumps(requests)
    result, last_error = None, "no speedline runtime available"
    if worker.session_active():
        try:
            _, stdout = worker.run_python("ncp-speed", PY, ["batch"], payload)
            result = json.loads(stdout)
        except (worker.WorkerError, json.JSONDecodeError):
            pass
    if result is None:
        for argv, entry in (([sys.executable, str(PY), "batch"], PY), (["node", str(JS), "batch"], JS)):
            if not entry.is_file():
                continue
            try:
                proc = subprocess.run(argv, input=payload, capture_output=True, text=True, encoding="utf-8")
                result = json.loads(proc.stdout)
                break
            except (OSError, json.JSONDecodeError) as exc:
                last_error = str(exc)
    if result is None:
        raise NcpUnavailable(last_error)
    if not isinstance(result, list) or len(result) != len(requests) or any(not isinstance(r, dict) for r in result):
        raise NcpUnavailable("speedline batch did not return one result per input")
    return result
