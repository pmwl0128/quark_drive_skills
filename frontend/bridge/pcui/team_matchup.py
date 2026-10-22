"""Shared request boundary for actual-set matchup batteries.

Both bridges accept the same small public shape (structured team-json or free-form team text),
canonicalize names through the resident dex worker, and invoke the team skill with a controlled
temporary file.  Keeping this here avoids the local and online UIs silently using different
parsers or matchup parameters.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .online import builder
from .online.teamtext import extract_species, normalize_team_text, resolve_species

MATCHUP_TOP_K_MIN = 1
MATCHUP_TOP_K_MAX = 60
MATCHUP_SOURCE_MAX = 6     # one team's worth of builds
MATCHUP_TEXT_MAX = 16_000
MATCHUP_TIMEOUT = 240.0


class MatchupInputError(ValueError):
    """Client-fixable actual-matchup input error."""


SP_STAT_CAP = 32
SP_TOTAL_CAP = 66


def _spread(raw: Any, species: str) -> dict[str, int]:
    spread_raw = raw if isinstance(raw, dict) else {}
    spread: dict[str, int] = {}
    for key in ("hp", "atk", "def", "spa", "spd", "spe"):
        value = spread_raw.get(key) or 0
        if isinstance(value, bool):
            raise MatchupInputError(f"{species} {key} SP must be an integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise MatchupInputError(f"{species} {key} SP must be an integer") from None
        if isinstance(value, float) and not value.is_integer():
            raise MatchupInputError(f"{species} {key} SP must be an integer")
        if not 0 <= parsed <= SP_STAT_CAP:
            raise MatchupInputError(
                f"{species} {key} SP must be between 0 and {SP_STAT_CAP}")
        spread[key] = parsed
    total = sum(spread.values())
    if total > SP_TOTAL_CAP:
        raise MatchupInputError(f"{species} SP total {total} exceeds {SP_TOTAL_CAP}")
    return spread


def _member(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("species"), str):
        return None
    species = raw["species"].strip()
    if not species:
        return None
    moves = [str(v).strip() for v in (raw.get("moves") or [])
             if isinstance(v, str) and v.strip()][:4]
    spread = _spread(raw.get("spread"), species)
    return {
        "species": species,
        "item": raw.get("item").strip() if isinstance(raw.get("item"), str) else None,
        "ability": raw.get("ability").strip() if isinstance(raw.get("ability"), str) else None,
        "nature": raw.get("nature").strip() if isinstance(raw.get("nature"), str) else None,
        "moves": moves,
        "spread": spread,
        "tera": None,
        "completeness": str(raw.get("completeness") or "extracted_set"),
    }


def _base_species_of(species: str) -> str | None:
    """The base species a 'Mega X' / 'Mega X Y' name derives from, or None for a plain name."""
    if not species.startswith("Mega "):
        return None
    rest = species[5:]
    if rest.endswith((" X", " Y", " Z")):
        rest = rest[:-2]
    return rest or None


def _canonicalize(pool: Any, members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    builder.canonicalize_members(pool, members, time.monotonic() + 60)
    resolved = set(resolve_species(pool, [m["species"] for m in members]))
    return [m for m in members if m["species"] in resolved]


def _parse_text(pool: Any, text: str, tmp_dir: Path) -> list[dict[str, Any]]:
    team_file = tmp_dir / "input.txt"
    team_file.write_text(normalize_team_text(text), encoding="utf-8")
    spec = tmp_dir / "parse.json"
    spec.write_text(json.dumps([{"op": "parse", "file": str(team_file)}]), encoding="utf-8")
    out = pool.request_json("team", ["session", str(spec)], None, MATCHUP_TIMEOUT)
    entry = out[0] if isinstance(out, list) and out else {}
    parsed = entry.get("result") if isinstance(entry, dict) else None
    members = [_member(m) for m in ((parsed or {}).get("pokemon") or [])] \
        if entry.get("rc") == 0 else []
    clean = [m for m in members if m is not None]
    clean = _canonicalize(pool, clean)
    # Bare lists and compact prose may not produce full parser blocks. Preserve every parsed set,
    # then fill still-missing species as honest species-only rows; the UI marks incomplete inputs.
    #
    # "Already present" must include the BASE species of anything parsed as a Mega form: a member
    # written 'Mega Raichu Y' also makes the scanner see 'Raichu', and a plain name-equality check
    # let that through as a THIRD member — an item-less, move-less ghost row that computed no damage
    # at all and read as "this Pokemon can't do anything".
    have = {m["species"] for m in clean}
    have_families = {_base_species_of(name) or name for name in have}
    for name in extract_species(pool, text):
        if len(clean) >= MATCHUP_SOURCE_MAX:
            break
        family = _base_species_of(name) or name
        if name not in have and family not in have_families:
            clean.append({"species": name, "item": None, "ability": None, "nature": None,
                          "moves": [], "spread": {k: 0 for k in
                                                   ("hp", "atk", "def", "spa", "spd", "spe")},
                          "tera": None, "completeness": "observed_species_only"})
            have.add(name)
            have_families.add(family)
    return clean


def run_actual_matchup(pool: Any, body: dict[str, Any],
                       workload_fn: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    fmt = body.get("format")
    if fmt not in ("single", "double"):
        raise MatchupInputError("format must be single or double")
    try:
        top_k = int(body.get("topK", 30))
    except (TypeError, ValueError):
        raise MatchupInputError("topK must be an integer") from None
    if not MATCHUP_TOP_K_MIN <= top_k <= MATCHUP_TOP_K_MAX:
        raise MatchupInputError("topK must be between 1 and 60")

    text = body.get("text")
    raw_team = body.get("team")
    if text is not None and raw_team is not None:
        raise MatchupInputError("provide text or team, not both")
    if isinstance(text, str) and len(text) > MATCHUP_TEXT_MAX:
        raise MatchupInputError(f"text exceeds {MATCHUP_TEXT_MAX} characters")
    if isinstance(text, str):
        from .team_code import resolve_text
        text = resolve_text(text, fmt)

    with tempfile.TemporaryDirectory(prefix="pcweb-matchup-") as tmp:
        tmp_dir = Path(tmp)
        if isinstance(text, str) and text.strip():
            members = _parse_text(pool, text, tmp_dir)
        elif isinstance(raw_team, dict):
            raw_members = raw_team.get("pokemon")
            if not isinstance(raw_members, list):
                raise MatchupInputError("team.pokemon must be an array")
            members = [m for m in (_member(raw) for raw in raw_members) if m is not None]
            members = _canonicalize(pool, members)
        else:
            raise MatchupInputError("team or text is required")

        if not members:
            raise MatchupInputError("no Pokemon could be resolved")
        if len(members) > MATCHUP_SOURCE_MAX:
            raise MatchupInputError(f"at most {MATCHUP_SOURCE_MAX} source sets are allowed")
        # Online deployments inject a cheap quota callback here, after parsing/canonicalization but
        # before the expensive team-skill session. Local callers leave it unset and retain the full
        # operator range. The callback meters work; it never narrows the legal 6 x top-60 shape.
        if workload_fn is not None:
            workload_fn(len(members), top_k)
        team = {"schema_version": 1, "format": fmt, "season": None, "rule": None,
                "pokemon": members, "provenance": None}
        team_path = tmp_dir / "team.json"
        team_path.write_text(json.dumps(team, ensure_ascii=False), encoding="utf-8")
        spec = tmp_dir / "matchup.json"
        spec.write_text(json.dumps([{"op": "matchup", "file": str(team_path),
                                     "top_k": top_k, "view": "summary"}],
                                   ensure_ascii=False), encoding="utf-8")
        out = pool.request_json("team", ["session", str(spec)], None, MATCHUP_TIMEOUT)
    entry = out[0] if isinstance(out, list) and out else None
    result = entry.get("result") if isinstance(entry, dict) and entry.get("rc") == 0 else None
    if not isinstance(result, dict):
        message = entry.get("stderr") if isinstance(entry, dict) else "matchup session failed"
        raise RuntimeError(str(message or "matchup session failed"))
    # The normalized source is returned separately so a text import can immediately become an
    # editable manual draft without asking the client to reverse-engineer calculation rows.
    return {"team": team, "result": result}
