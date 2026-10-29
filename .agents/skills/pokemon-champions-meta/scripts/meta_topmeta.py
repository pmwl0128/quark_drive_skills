"""Read-only adapter to the team skill's published counts and pure comparison/projection code."""
from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

TEAM = Path(__file__).resolve().parents[2] / "pokemon-champions-team"


class Unavailable(RuntimeError):
    pass


@lru_cache(maxsize=2)
def module(name: str):
    path = TEAM / "scripts" / f"{name}.py"
    if not path.is_file():
        raise Unavailable("pokemon-champions-team is not installed alongside meta; Top Meta needs its published table and query modules")
    spec = importlib.util.spec_from_file_location(f"quark_meta_team_{name}", path)
    if spec is None or spec.loader is None:
        raise Unavailable(f"cannot load team {name} query module")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def query(fmt: str, season: str, rule: str, scope: str, records: list[dict] | None,
          ingame: Callable[[Any], dict], *, view: str = "summary", limit: int = 6) -> dict:
    facts = module("topmeta")
    table = facts.load_table(fmt, rule)
    if table is None:
        return {"ok": False, "query": "topmeta", "error": {"code": "not_found",
                "message": f"no published Top Meta table for {rule}/{fmt}"}}
    if table.get("schema_version") != facts.SCHEMA_VERSION or table.get("format") != fmt or table.get("rule") != rule:
        raise ValueError("published Top Meta table identity/schema does not match this query")
    doc = (table.get("scopes") or {}).get(scope)
    if not isinstance(doc, dict):
        return {"ok": False, "query": "topmeta", "error": {"code": "not_found",
                "message": f"published Top Meta table has no {scope} scope"}}
    if scope == "season" and doc.get("season", table.get("season")) != season:
        return {"ok": False, "query": "topmeta", "error": {"code": "not_found",
                "message": f"published season scope is not {season}; no historical table is inferred"}}
    snapshot = ingame(facts)
    wanted, queries = facts.resolve_queries(table, scope, records, snapshot)
    result = facts.compare(table, scope, snapshot, species=wanted)
    if records is not None:
        result["queries"] = queries
    result["data_granularity"] = "species_aggregate"
    if view == "summary":
        result = module("team_views").project(result, "topmeta", limit)
        result["projection"]["detail"] = "Use --view full for untruncated evidence; tables retain source order and population ranks."
    return result
