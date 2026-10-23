"""Small public request boundary shared by imports, diagnosis, matchup and QA."""
from __future__ import annotations

import json
import re
import sys
from fastapi import HTTPException
from .paths import SKILLS_ROOT


def skill():
    scripts = str(SKILLS_ROOT / "pokemon-champions-team/scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import team_codes
    return team_codes


def code_in_text(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text or "").upper()
    if not re.fullmatch(r"[A-Z0-9]{10}", compact):
        return None
    if compact.isalpha():
        skill()  # Make the skill's public dex adapter available.
        import dexlink
        if dexlink.resolve_names([text.strip()], "pokemon", fuzzy=False).get(text.strip(), {}).get("ok"):
            return None
    return compact


def resolve(body: dict, consume=None) -> dict:
    if not isinstance(body, dict) or set(body) - {"code", "format"} or body.get("format") not in ("single", "double"):
        raise HTTPException(400, {"error": {"code": "bad_input", "message": "code and format are required"}})
    service = skill()
    try:
        code = service.normalize_code(body.get("code"))
        if consume is not None:
            consume()
        try:
            result = service.lookup(code, network=False)
        except service.TeamCodeError as exc:
            if exc.code != "cache_miss":
                raise
            result = service.lookup(code)
        # Format is explicitly chosen by the importing caller; no season/rule is guessed.
        return {**result, "team": {**result["team"], "format": body["format"]}}
    except service.TeamCodeError as exc:
        status = {"bad_input": 400, "not_found": 404, "busy": 429, "rate_limited": 429}.get(exc.code, 503)
        # Public bodies omit operator state, quotas and exact backoff settings.
        raise HTTPException(status, {"error": {"code": exc.code, "message": str(exc)}}) from None


def resolve_text(text: str, fmt: str, consume=None) -> str:
    code = code_in_text(text)
    return json.dumps(resolve({"code": code, "format": fmt}, consume)["team"], ensure_ascii=False) if code else text
