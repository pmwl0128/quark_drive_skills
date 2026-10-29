"""Small per-FORM reverse indexes for QA. Full matrices are read at build time only.

Grades and portfolio floors come from the team skill, never from a second grading formula.
Runtime reads one bounded shard and filters ranks; it does not import/load the matrix.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .. import mappers
from ..paths import META_DATA

SCHEMA = 1
KO_SCHEMA = 1
MAX_SHARD_BYTES = 256_000
MAX_RESULTS = 12
MAX_RESULT_BYTES = 10_000


def conclusions(doc: dict) -> list[str]:
    """Sentence facts for small models: keep each candidate's item and its blockers together.
    Builds that read identically (same candidate, item, grade and blockers) form one sentence with
    their count, so two indistinguishable lines never look like a contradiction."""
    lines = []
    target = doc["pokemon"]
    groups: dict[tuple, list[dict]] = {}
    for row in doc["rows"] + doc["conditionalRows"]:
        key = (row["name"], row.get("item"), row["grade"], tuple(str(i) for i in row.get("blockedBy") or []),
               tuple((a["grade"], str(a["targetItem"])) for a in row.get("answers") or []))
        groups.setdefault(key, []).append(row)
    for rows in groups.values():
        row = rows[0]
        own = f"{row['name']} holding {row.get('item') or 'no item'}"
        if len(rows) > 1:
            own += f" ({len(rows)} observed builds; the example is one of them)"
        if row["grade"] == "C0":
            answers = "; ".join(f"{a['grade']} against {target} holding {a['targetItem'] or 'no item'}"
                                for a in row["answers"])
            blocks = ", ".join(str(i or "no item") for i in row["blockedBy"])
            text = f"{own}: {answers}. THIS candidate has C0 against {target} holding {blocks}; overall floor C0."
        else:
            text = f"{own}: {row['grade']} floor across all retained builds of {target}."
        text += (f" Example ONLY against {target} holding {row.get('exampleTargetItem') or 'no item'}: "
                 f"{row.get('move')} {row['damagePercent'][0]}–{row['damagePercent'][1]}%, {row.get('ko')}.")
        if any(r.get("contested") for r in rows):
            text += " Mechanic caveats apply."
        lines.append(text)
    return lines


def build_shards(cache: dict) -> dict[str, dict]:
    """Called with the already-loaded offline cache by build_projection, including native fallback."""
    mod = mappers._oppcache_mod()
    sets = cache.get("sets") or {}
    matrix = cache.get("check_matrix") or {}
    ranks = {r.get("variant_id") or r.get("species"): r.get("rank") for r in cache.get("species") or []}
    groups: dict[str, list[str]] = {}
    for key, st in sets.items():
        form = st.get("run_form") or st.get("species")
        if form:
            groups.setdefault(form, []).append(key)
    built = cache.get("built_for") or {}
    authority = json.loads((META_DATA / "current.json").read_text(encoding="utf-8"))
    usage_clock = (authority.get("seasons", {}).get(built.get("season")) or {}).get("updated_at")
    out = {}
    for form, targets in groups.items():
        rows, gaps = [], []
        for ai, st in sets.items():
            rank = ranks.get(ai)
            if not isinstance(rank, int):
                continue
            cells = [{"opponent": form, "opponent_variant": dj,
                      "opponent_is_modal": bool(sets[dj].get("is_modal")),
                      "opponent_coverage": sets[dj].get("coverage"),
                      "check": matrix.get(ai, {}).get(dj)} for dj in targets]
            summary = mod.checks.coverage_summary([{"member": ai, "cells": cells}])["by_opponent"][0]
            if not summary["calculation_complete"]:
                gaps.append({"rank": rank, "name": st.get("run_form") or st.get("species")})
                continue
            grade = summary["observed_floor"]["grade"]
            answered = [dj for dj in targets if matrix[ai][dj]["grade"] in ("C2", "C1")]
            if not answered:
                continue
            # A sash build can make every observed floor C0. Keep useful conditional answers
            # visible, with their blockers, without promoting their floor or calling them safe.
            witness = (summary["observed_floor"]["witness_variant_ids"][0]
                       if grade in ("C2", "C1") else answered[0])
            offense = ((cache.get("matrix") or {}).get(ai, {}).get(witness) or {}).get("offense") or {}
            pair = matrix[ai][witness]
            rows.append({"rank": rank, "name": st.get("run_form") or st.get("species"),
                         "item": st.get("item"), "ability": st.get("ability"),
                         "grade": grade, "targetCoverage": summary["coverage"]["represented"],
                         "exampleTargetItem": sets[witness].get("item"), "move": offense.get("move"),
                         **({"answers": [{"targetItem": sets[dj].get("item"),
                                          "grade": matrix[ai][dj]["grade"]} for dj in answered],
                             "blockedBy": [sets[dj].get("item") for dj in targets if dj not in answered]}
                            if grade == "C0" else {}),
                         "damagePercent": [offense.get("min_percent"), offense.get("max_percent")],
                         "ko": offense.get("ko"),
                         "contested": any(c.get("contested") for dj in targets
                                          if (c := matrix.get(ai, {}).get(dj))),
                         "caveats": pair.get("caveats") or []})
        rows.sort(key=lambda r: (("C2", "C1", "C0").index(r["grade"]), r["rank"], r["name"], r.get("item") or ""))
        out[form] = {"schema": SCHEMA, "pokemon": form, "format": built.get("format"),
                     "coverageSpecies": sets[targets[0]].get("species"),
                     "season": built.get("season"), "rule": built.get("rule"),
                     "builtAt": built.get("built_at"), "sourceFingerprints": built.get("source_fingerprints"),
                     "usageClock": usage_clock,
                     "teamEvidenceExpiresAt": built.get("team_evidence_expires_at"),
                     "targetBuilds": [{"item": sets[k].get("item"), "ability": sets[k].get("ability"),
                                       "coverage": sets[k].get("coverage")} for k in targets],
                     "rows": rows, "missing": gaps}
    return out


def _ko_row(rank: int, st: dict, cell: dict | None) -> dict | None:
    """One OHKO/2HKO cell of the static matrix, with its KO caveats; anything slower is not indexed."""
    off = (cell or {}).get("offense") or {}
    chance = off.get("ko_chance") or {}
    if chance.get("n") not in (1, 2):
        return None
    caveats = sorted({str(c["code"]) for c in off.get("ko_caveats") or [] if c.get("code")})
    if off.get("disguise_adjusted"):
        caveats.append("disguise")
    return {"rank": rank, "name": st.get("run_form") or st.get("species"), "item": st.get("item"),
            "move": off.get("move"), "pct": [off.get("min_percent"), off.get("max_percent")],
            "n": chance["n"], "sure": bool(chance.get("guaranteed")), "chancePct": chance.get("chance_pct"),
            **({"caveats": caveats} if caveats else {})}


def build_ko_shards(cache: dict) -> dict[str, dict]:
    """Per-FORM KO index over the same cache as the counters: the form's most common retained build
    against every ranked build (attacking) and every ranked build against it (defending). Only OHKO and
    2HKO cells are kept; `fieldRanks` lets a reader count how many builds a top-N cut compared."""
    sets = cache.get("sets") or {}
    matrix = cache.get("matrix") or {}
    ranks = {r.get("variant_id") or r.get("species"): r.get("rank") for r in cache.get("species") or []}
    groups: dict[str, list[str]] = {}
    for key, st in sets.items():
        form = st.get("run_form") or st.get("species")
        if form:
            groups.setdefault(form, []).append(key)
    built = cache.get("built_for") or {}
    authority = json.loads((META_DATA / "current.json").read_text(encoding="utf-8"))
    usage_clock = (authority.get("seasons", {}).get(built.get("season")) or {}).get("updated_at")
    field = [(key, rank) for key in sets if isinstance(rank := ranks.get(key), int)]
    out = {}
    for form, keys in groups.items():
        ref = max(keys, key=lambda k: (sets[k].get("coverage") or 0, k))
        attacking = [row for key, rank in field
                     if (row := _ko_row(rank, sets[key], (matrix.get(ref) or {}).get(key)))]
        defending = [row for key, rank in field
                     if (row := _ko_row(rank, sets[key], (matrix.get(key) or {}).get(ref)))]
        st = sets[ref]
        out[form] = {"schema": KO_SCHEMA, "pokemon": form, "format": built.get("format"),
                     "season": built.get("season"), "rule": built.get("rule"),
                     "builtAt": built.get("built_at"), "usageClock": usage_clock,
                     "teamEvidenceExpiresAt": built.get("team_evidence_expires_at"),
                     "referenceBuild": {"item": st.get("item"), "ability": st.get("ability"),
                                        "nature": st.get("nature"), "moves": st.get("moves"),
                                        "sps": mappers._map_sps(st.get("sps")), "coverage": st.get("coverage")},
                     "otherBuilds": [{"item": sets[k].get("item"), "ability": sets[k].get("ability"),
                                      "coverage": sets[k].get("coverage")} for k in keys if k != ref],
                     "fieldRanks": sorted(rank for _key, rank in field),
                     "attacking": attacking, "defending": defending}
    return out


def _load_shard(projection: Path | None, folder: str, schema: int, fmt: str, name: str) -> dict:
    """One per-form shard of the current environment (its native twin once handover evidence expired),
    or {"error": ...} when it is missing or belongs to another environment."""
    if projection is None:
        return {"error": "the matchup query index is not installed"}
    path = projection / "qa" / f"{folder}_{fmt}" / f"{mappers.slugify(name)}.json"
    if not path.is_file():
        return {"pokemon": name, "error": "no retained reference build for this exact form; do not substitute a Mega"}

    def read(p: Path) -> dict:
        if p.stat().st_size > MAX_SHARD_BYTES:
            raise ValueError("QA matchup shard exceeds its read budget")
        doc = json.loads(p.read_text(encoding="utf-8"))
        if doc.get("schema") != schema:
            raise ValueError("unsupported QA matchup index")
        return doc

    doc = read(path)
    expiry = doc.get("teamEvidenceExpiresAt")
    if expiry and datetime.now(timezone.utc) >= datetime.fromisoformat(expiry.replace("Z", "+00:00")):
        native = path.with_name(path.stem + ".native.json")
        if not native.is_file():
            return {"error": "reference team evidence expired"}
        doc = read(native)
    authority = json.loads((META_DATA / "current.json").read_text(encoding="utf-8"))
    current = authority["current"]
    usage_clock = (authority.get("seasons", {}).get(current["season"]) or {}).get("updated_at")
    if any(doc.get(k) != v for k, v in (("pokemon", name), ("format", fmt),
                                      ("season", current["season"]), ("rule", current["rule"]),
                                      ("usageClock", usage_clock))):
        return {"error": "matchup query index does not match the current environment"}
    return doc


def read_ko(projection: Path | None, fmt: str, name: str, direction: str, top_n: int) -> dict:
    """The KO index of one form against the top-N field: builds with the same name, move and KO class
    are one row (their items listed), OHKO before 2HKO, guaranteed before chance."""
    doc = _load_shard(projection, "ko", KO_SCHEMA, fmt, name)
    if doc.get("error"):
        return doc
    groups: dict[tuple, dict] = {}
    for row in doc[direction]:
        if not 1 <= row["rank"] <= top_n:
            continue
        # A caveat (Focus Sash, Multiscale ...) belongs to its own builds: never merged into a clean row.
        key = (row["name"], row["move"], row["n"], row["sure"], tuple(row.get("caveats") or ()))
        group = groups.get(key)
        if group is None:
            groups[key] = {**row, "items": [row["item"] or "no item"], "pct": list(row["pct"]), "builds": 1}
            groups[key].pop("item")
            continue
        group["builds"] += 1
        if (row["item"] or "no item") not in group["items"]:
            group["items"].append(row["item"] or "no item")
        group["pct"] = [min(group["pct"][0], row["pct"][0]), max(group["pct"][1], row["pct"][1])]
    rows = sorted(groups.values(), key=lambda r: (r["n"], not r["sure"], r["rank"], r["name"]))
    for row in rows:
        row["ko"] = f"{'guaranteed' if row.pop('sure') else str(row.get('chancePct')) + '% chance to'} " \
                    f"{'OHKO' if row.pop('n') == 1 else '2HKO'}"
        if row.get("caveats"):
            row["ko"] += " by damage alone; its caveats can prevent it"
        row.pop("chancePct", None)
    ohko = [r for r in rows if "OHKO" in r["ko"]]
    two = [r for r in rows if "2HKO" in r["ko"]]
    target = doc["pokemon"]
    result = {"pokemon": target, "format": fmt, "direction": direction, "topN": top_n,
              "season": doc["season"], "rule": doc["rule"], "confidence": "low",
              "referenceBuild": doc["referenceBuild"], "otherBuilds": doc["otherBuilds"],
              "comparedBuilds": sum(1 for r in doc["fieldRanks"] if r <= top_n),
              "ohko": ohko, "twoHko": two, "omittedOhko": 0, "omittedTwoHko": 0,
              "note": (f"Static single-hit reference over observed builds of the top {top_n} usage Pokemon. "
                       + (f"Rows are builds that {target}'s reference build (its most common retained build) "
                          "OHKOs or 2HKOs with its best move; items are the TARGETS' items."
                          if direction == "attacking" else
                          f"Rows are builds whose best move OHKOs or 2HKOs {target}'s reference build; items "
                          "are the ATTACKERS' items. Its other builds (otherBuilds) can differ, e.g. Focus Sash.")
                       + " builds counts the observed builds a row stands for (abilities can differ); pct is "
                         "the damage range in percent of the target's HP across them. "
                         "caveats (focus_sash, sturdy, multiscale, disguise ...) qualify a KO. Builds not listed "
                         "need 3+ hits. Not a win rate; speed, switching and partners are not modelled; doubles "
                         "is an isolated pair. For a stated build or one exact pair use calc_damage.")}
    while len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_RESULT_BYTES:
        if result["twoHko"]:
            result["twoHko"].pop()
            result["omittedTwoHko"] += 1
        elif result["ohko"]:
            result["ohko"].pop()
            result["omittedOhko"] += 1
        else:
            raise ValueError("QA KO metadata exceeds its result budget")
    return result


def read_counters(projection: Path | None, fmt: str, name: str, top_n: int) -> dict:
    doc = _load_shard(projection, "matchup", SCHEMA, fmt, name)
    if doc.get("error"):
        return doc
    selected = [r for r in doc["rows"] if 1 <= r["rank"] <= top_n]
    rows = [r for r in selected if r["grade"] != "C0"]
    conditional = [r for r in selected if r["grade"] == "C0"]
    result = {"pokemon": name, "format": fmt, "season": doc["season"], "rule": doc["rule"],
            "builtAt": doc["builtAt"], "topN": top_n, "confidence": "low",
            "coverageSpecies": doc.get("coverageSpecies", name),
            "targetBuilds": doc["targetBuilds"], "matchedBuilds": len(rows),
            "rows": rows[:MAX_RESULTS], "omittedBuilds": max(0, len(rows) - MAX_RESULTS),
            "conditionalBuilds": len(conditional), "conditionalRows": conditional[:6],
            "omittedConditionalBuilds": max(0, len(conditional) - 6),
            "missingBuilds": sum(1 for r in doc["missing"] if 1 <= r["rank"] <= top_n),
            "note": "C2=safe switch-in then static 1v1; C1=even-footing 1v1 only. Grades are floors across "
                    "all retained builds OF THIS EXACT FORM. Candidate rows are individual observed builds, "
                    "not every build of that species. Coverage is observed sample share, not win rate. "
                    "conditionalRows have C0 floors: answers.targetItem/blockedBy are OPPONENT items; "
                    "row.item is the CANDIDATE's item. Answer ONLY listed C1/C2 matchups, and name "
                    "the C0 blockers. They are NOT all-build counters. Contested rows have mechanic caveats. "
                    "Doubles is an isolated pair reference."}
    # Drop whole rows before serialization. A byte-tail cut would erase blocker caveats or
    # deliver invalid JSON to the small model just when the broadest query needs them most.
    while len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_RESULT_BYTES:
        if result["conditionalRows"]:
            result["conditionalRows"].pop()
            result["omittedConditionalBuilds"] += 1
        elif result["rows"]:
            result["rows"].pop()
            result["omittedBuilds"] += 1
        else:
            raise ValueError("QA matchup metadata exceeds its result budget")
    return result
