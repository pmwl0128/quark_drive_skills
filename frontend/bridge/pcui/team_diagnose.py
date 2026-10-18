"""Shared deterministic diagnosis: preserve parsed builds before mapping report facts."""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from . import mappers
from .online.builder import canonicalize_members
from .online.teamtext import extract_species, normalize_team_text, resolve_species

DIAGNOSE_TEXT_MAX = 8_000
DIAGNOSE_TIMEOUT = 150.0
DIAGNOSE_TOP_K = 30


def validate_team(pool, team: dict) -> dict:
    """The legality facts a diagnosis report pairs with an already-diagnosed team."""
    with tempfile.TemporaryDirectory(prefix="pcui-validate-") as tmp:
        team_json = Path(tmp) / "team.json"
        team_json.write_text(json.dumps(team, ensure_ascii=False), encoding="utf-8")
        spec = Path(tmp) / "spec.json"
        spec.write_text(json.dumps([{"op": "validate", "file": str(team_json)}]), encoding="utf-8")
        rows = pool.request_json("team", ["session", str(spec)], None, DIAGNOSE_TIMEOUT)
    row = rows[0] if isinstance(rows, list) and rows else None
    if not isinstance(row, dict) or not isinstance(row.get("result"), dict):
        raise RuntimeError("validate failed")
    return row["result"]


def run_team_diagnose(pool, text: str, fmt: str) -> dict:
    """Parse structured/share text, then fill genuine gaps with observed species only."""
    norm = normalize_team_text(text)
    with tempfile.TemporaryDirectory(prefix="pcui-diag-") as tmp:
        tmp_dir = Path(tmp)
        team_file = tmp_dir / "team.txt"
        team_file.write_text(norm, encoding="utf-8")
        spec = tmp_dir / "spec.json"
        spec.write_text(json.dumps([{"op": "parse", "file": str(team_file)}]), encoding="utf-8")
        out = pool.request_json("team", ["session", str(spec)], None, DIAGNOSE_TIMEOUT)
        entry = out[0] if isinstance(out, list) and out and isinstance(out[0], dict) else {}
        parsed = entry.get("result")
        members = [m for m in ((parsed or {}).get("pokemon") or [])
                   if isinstance(m, dict) and m.get("species")] \
            if entry.get("rc") == 0 and isinstance(parsed, dict) else []
        if members:
            canonicalize_members(pool, members, time.monotonic() + 60)
            resolved = set(resolve_species(pool, [m["species"] for m in members]))
            members = [m for m in members if m["species"] in resolved]
        if len(members) < 6:
            have = {m.get("species") for m in members}
            for name in extract_species(pool, text):
                if len(members) >= 6:
                    break
                if name not in have:
                    members.append({"species": name, "item": None, "ability": None,
                                    "moves": [], "nature": None, "spread": None, "tera": None,
                                    "completeness": "observed_species_only"})
                    have.add(name)
        if not members:
            raise ValueError("unparseable")
        team = {"schema_version": 1, "format": fmt, "season": None, "rule": None,
                "pokemon": members[:6], "provenance": None}
        team_json = tmp_dir / "team.json"
        team_json.write_text(json.dumps(team, ensure_ascii=False), encoding="utf-8")
        spec2 = tmp_dir / "spec2.json"
        spec2.write_text(json.dumps([
            {"op": "validate", "file": str(team_json)},
            {"op": "diagnose", "file": str(team_json), "aspect": "all",
             "top_k": DIAGNOSE_TOP_K, "with_check": True},
        ]), encoding="utf-8")
        reports = pool.request_json("team", ["session", str(spec2)], None, DIAGNOSE_TIMEOUT)
    if not isinstance(reports, list) or len(reports) != 2 or any(
            not isinstance(row, dict) or not isinstance(row.get("result"), dict) for row in reports):
        raise RuntimeError("diagnose session failed")
    return mappers.map_diagnose_report(team, reports[0]["result"], reports[1]["result"])
