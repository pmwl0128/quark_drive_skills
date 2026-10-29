#!/usr/bin/env python
"""Bridge to the sibling pokemon-champions-dex skill.

This skill holds no battle data. Legality facts (roster, types, abilities, learnsets,
Mega items) are read at query time from the sibling dex skill via its public CLI
(`champdex.py ... --format json`), so we depend on its interface, not its internals.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import worker

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SKILL_DIR.parent
DEX_CLI = SKILLS_ROOT / "pokemon-champions-dex" / "scripts" / "champdex.py"


class DexUnavailable(RuntimeError):
    pass


def _run(args: list[str]) -> Any:
    if not DEX_CLI.exists():
        raise DexUnavailable(f"sibling dex skill not found at {DEX_CLI}")
    argv = [*args, "--format", "json"]
    if worker.session_active():             # perf: reuse a resident dex worker within a session
        try:
            code, out = worker.run_python("dex", DEX_CLI, argv)
        except worker.WorkerError:
            pass                            # fall back to a one-shot subprocess (results identical)
        else:
            # A non-zero exit is the CLI's own answer, not a broken worker — raise it here instead
            # of re-running the same failing call as a subprocess just to reach the same raise.
            if code != 0:
                raise DexUnavailable(out.strip() or "champdex.py failed")
            try:
                return json.loads(out)
            except json.JSONDecodeError:
                pass                        # unparseable stdout: let the one-shot path decide
    proc = subprocess.run(
        [sys.executable, str(DEX_CLI), *argv],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        raise DexUnavailable(proc.stderr.strip() or "champdex.py failed")
    return json.loads(proc.stdout)


def resolve_names(names: list[str], kind: str = "pokemon", *, fuzzy: bool = True) -> dict[str, dict[str, Any]]:
    """Bridge to the dex `resolve` name-normalizer (batch + fuzzy + Mega-compose + nicknames). Returns
    query -> the dex resolve record verbatim ({query, ok, canonical, display_name, match_type, score,
    +is_mega/base_species/required_item for pokemon, suggestions? on miss}). resolve is 1:1 ordered;
    the mapping is keyed BY QUERY, so a duplicate query name collapses to one entry (every team-side
    caller resolves a de-duplicated set — context_audit/libsearch build `sorted(set(...))` first — so
    this never loses a record in practice; the dex CLI itself keeps the strict positional 1:1 list).
    This is how the team side normalizes USER-typed condition values (species/move/item/ability/nature)
    for `search` and type facts without re-implementing dex's resolution rules (DRY)."""
    if not names:
        return {}
    args = ["resolve", *names, "--kind", kind]
    if not fuzzy:
        args.append("--strict")
    rows = _run(args)
    if isinstance(rows, dict):
        rows = [rows]
    return {name: row for name, row in zip(names, rows)}


def official_names() -> list[dict[str, Any]]:
    return _run(["names"])


def lookup_pokemon(names: list[str], *, fuzzy: bool = False) -> dict[str, dict[str, Any]]:
    """Resolve each name to dex facts. Returns query-name -> fact dict.

    Fact dict keys: found, name (canonical), display_name, types, stats, abilities,
    moves (cached learnset), is_mega, base_species, required_item, mega_forms.

    With `fuzzy=True` the dex applies its conservative typo fallback: a confident-unique
    hit carries a `resolution` block ({match_type,score,distance,from}); a miss may carry `suggestions`
    (did-you-mean) and, on an ambiguous tie, resolves to NO name (found False) — never a guess. Used at
    runtime for USER-typed species. The dex resolves fuzzily by default, so this bridge passes --strict
    when fuzzy=False: team canonicalizes species once (fuzzy+flagged) at load, then treats everything
    downstream as exact facts — a silent later correction would mask a real problem.
    """
    if not names:
        return {}
    args = ["batch", "pokemon", *names]
    if not fuzzy:
        args.append("--strict")
    rows = _run(args)
    if isinstance(rows, dict):
        rows = [rows]
    out: dict[str, dict[str, Any]] = {}
    for name, row in zip(names, rows):
        if row.get("error"):
            out[name] = {"found": False, "query": name}
            if row.get("suggestions"):
                out[name]["suggestions"] = row["suggestions"]
        else:
            out[name] = {
                "found": True,
                "name": row.get("name"),
                "display_name": row.get("display_name"),
                "types": row.get("types", []),
                "stats": row.get("stats", {}),
                "abilities": row.get("abilities", []),
                "moves": row.get("moves", []),
                "is_mega": row.get("is_mega", False),
                "base_species": row.get("base_species"),
                "required_item": row.get("required_item"),
                # The dex owns the stone -> Mega host rule; base_species is only a Species Clause
                # grouping key and cannot answer it (mega.py).
                "mega_forms": row.get("mega_forms") or [],
            }
            if row.get("resolution"):
                out[name]["resolution"] = row["resolution"]
    return out


def canonicalize_species(team: Any, *, fuzzy: bool = True) -> list[dict[str, Any]]:
    """Rewrite each member's species to dex canonical IN PLACE, fuzzy-tolerant, and return
    the typo corrections as [{from,to,match_type,score,distance}].

    Auto-corrects a confident-unique typo (garchmp -> Garchomp) and flags it; an exact-alias
    normalization (rotom-wash -> Rotom-Wash) is applied silently (no flag). Ambiguous / unresolved
    species are left verbatim so `validate` still flags them (with did-you-mean suggestions) — the
    correction never guesses. Returns [] if the dex is unavailable (caller degrades honestly)."""
    members = [m for m in getattr(team, "pokemon", []) if getattr(m, "species", "")]
    if not members:
        return []
    facts = lookup_pokemon([m.species for m in members], fuzzy=fuzzy)
    flags: list[dict[str, Any]] = []
    for m in members:
        f = facts.get(m.species) or {}
        if not f.get("found") or not f.get("name") or f["name"] == m.species:
            continue
        if f.get("resolution"):                       # fuzzy typo -> visible flag
            r = f["resolution"]
            flags.append({"from": m.species, "to": f["name"], "match_type": r.get("match_type"),
                          "score": r.get("score"), "distance": r.get("distance")})
        m.species = f["name"]                          # exact-alias or fuzzy -> canonical downstream
    return flags


def canonicalize_fields(team: Any, *, fuzzy: bool = True) -> list[dict[str, Any]]:
    """Normalize authored item/ability/nature/moves through the public resolver; flag every typo.

    Unresolved/ambiguous values remain verbatim for strict validation. Stored evidence is never
    passed through this cleanup: callers own the mutable Team copy and preserve receipt inputs.
    """
    members = list(getattr(team, "pokemon", []))
    flags = []
    batches = {}
    for field, kind in (("item", "item"), ("ability", "ability"), ("nature", "nature"), ("moves", "move")):
        values = sorted({value for member in members
                         for value in (getattr(member, field, []) if field == "moves"
                                       else [getattr(member, field, None)]) if isinstance(value, str) and value})
        batches[field] = resolve_names(values, kind, fuzzy=fuzzy)
    for field, records in batches.items():
        for index, member in enumerate(members):
            original = getattr(member, field, []) if field == "moves" else [getattr(member, field, None)]
            normalized = []
            for position, value in enumerate(original):
                rec = records.get(value) or {}
                canonical = rec.get("canonical") if rec.get("ok") else None
                normalized.append(canonical or value)
                if canonical and rec.get("match_type") == "fuzzy":
                    flags.append({"field": f"pokemon[{index}].{field}" + (f"[{position}]" if field == "moves" else ""),
                                  "from": value, "to": canonical, "match_type": "fuzzy",
                                  "score": rec.get("score"), "distance": rec.get("distance")})
            setattr(member, field, normalized if field == "moves" else normalized[0])
    return flags


def lookup_items(names: list[str]) -> dict[str, dict[str, Any]]:
    """Resolve held items against the Champions item pool (dex is the authority).

    Returns query-name -> {found, name, display_name, required_by}. `found` False means the
    item is not in the Champions item pool (illegal to hold). `required_by` lists the Mega
    forms that need this item (non-empty => the item is a Mega stone), so callers can map a
    base species + stone to its Mega form without a second lookup.
    """
    if not names:
        return {}
    # Strict: item legality / Mega-stone mapping are facts — a silent fuzzy correction here would pass an
    # illegal or misspelled item, or feed the calculator the wrong stone. (dex defaults to fuzzy now.)
    rows = _run(["batch", "item", *names, "--strict"])
    if isinstance(rows, dict):
        rows = [rows]
    out: dict[str, dict[str, Any]] = {}
    for name, row in zip(names, rows):
        out[name] = {
            "found": not row.get("error"),
            "name": row.get("name"),
            "display_name": row.get("display_name"),
            "required_by": row.get("required_by", []),
        }
    return out


def lookup_abilities(names: list[str]) -> dict[str, dict[str, Any]]:
    """Resolve ability names (any of the dex's trilingual aliases) to the English canonical.

    Returns query-name -> {found, name}. Used to translate the meta skill's JP/zh ability
    names into the English names the ncp calculator expects.
    """
    if not names:
        return {}
    # Strict: these ability names feed the damage calculator; a silent fuzzy mis-translation would
    # corrupt a damage fact. (dex defaults to fuzzy now.)
    rows = _run(["batch", "ability", *names, "--strict"])
    if isinstance(rows, dict):
        rows = [rows]
    out: dict[str, dict[str, Any]] = {}
    for name, row in zip(names, rows):
        out[name] = {"found": not row.get("error"), "name": row.get("name")}
    return out


def lookup_moves(names: list[str]) -> dict[str, dict[str, Any]]:
    """Resolve moves to dex facts. Returns query-name -> {found, name, type, category, power,
    accuracy, priority, is_ohko}. accuracy is int|None straight from the dex (None = no accuracy roll:
    must-hit moves and no-check status moves); is_ohko is the authoritative mechanical flag rather
    than an accuracy/power heuristic.

    Used by offense diagnosis to tell attacking moves (physical/special) from status, get each move's
    type, and judge STAB; `priority` (signed speed-priority stage, 0 == normal) feeds the speed
    landscape so a slower member carrying a priority attacker is surfaced.
    """
    if not names:
        return {}
    # Strict: move type/category/priority drive offense diagnosis facts; no silent correction. (dex
    # defaults to fuzzy now.)
    rows = _run(["batch", "move", *names, "--strict"])
    if isinstance(rows, dict):
        rows = [rows]
    out: dict[str, dict[str, Any]] = {}
    for name, row in zip(names, rows):
        found = not row.get("error")
        out[name] = {
            "found": found,
            "name": row.get("name"),
            "type": row.get("type"),
            "category": row.get("category"),
            "power": row.get("power"),
            "accuracy": row.get("accuracy"),   # int|None — the dex emits ONE numeric surface
            "priority": row.get("priority"),
            # TRI-STATE: True/False from the resolved move, None when the move did NOT resolve
            # (strict-dex miss). Never ASSERT "not OHKO" for an unresolved move — the consumer
            # keys off `is True` for exclusion, so None (unknown) leaves the move where it was
            # rather than falsely certifying it as a normal deterministic KO.
            "is_ohko": (row.get("is_ohko") is True) if found else None,
        }
    return out


if __name__ == "__main__":  # tiny smoke test
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(lookup_pokemon(sys.argv[1:] or ["Garchomp"]), ensure_ascii=False, indent=2))
