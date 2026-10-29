"""Path-free adapter for portable inference cases; the skill owns all validation and math."""
import asyncio
import json
from pathlib import Path
import tempfile

from fastapi import HTTPException


async def run(pool, body):
    operation = body.get("operation")
    allowed = {"init": {"case"}, "add": {"case", "observation"}, "status": {"case", "predict", "mine"},
               "remove": {"case", "id"}, "example": {"case"}, "schema": set(),
               "import-web": {"case", "prior", "foe_id"}}
    if not isinstance(operation, str) or operation not in allowed or set(body) - allowed[operation] - {"operation"}:
        raise HTTPException(400, "unsupported inference operation or fields")
    if operation != "schema" and not isinstance(body.get("case"), dict):
        raise HTTPException(400, "case must be inline JSON")
    argv = [operation]
    with tempfile.TemporaryDirectory() as tmp:
        for field in ("observation", "prior"):
            if field in allowed[operation]:
                if not isinstance(body.get(field), dict):
                    raise HTTPException(400, f"{field} must be inline JSON")
                path = Path(tmp) / (field + ".json")
                path.write_text(json.dumps(body[field], ensure_ascii=False), encoding="utf-8")
                argv += ["--" + field, str(path)]
        if operation == "remove":
            if not isinstance(body.get("id"), str) or not body["id"]:
                raise HTTPException(400, "id required")
            argv += ["--id=" + body["id"]]
        if "predict" in body:
            if type(body["predict"]) is not bool:
                raise HTTPException(400, "predict must be boolean")
            if body["predict"]:
                argv.append("--predict")
        if "mine" in body:
            if type(body["mine"]) is not int or not 0 <= body["mine"] < 6:
                raise HTTPException(400, "mine must be a zero-based slot")
            argv += ["--mine", str(body["mine"])]
        if "foe_id" in body:
            if not isinstance(body["foe_id"], str) or not body["foe_id"]:
                raise HTTPException(400, "foe_id must be a nonempty string")
            argv.append("--foe-id=" + body["foe_id"])
        return await asyncio.to_thread(pool.request_json, "inference", argv,
            json.dumps(body.get("case"), ensure_ascii=False) if operation != "schema" else None, 600)
