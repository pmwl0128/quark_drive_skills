"""Bidirectional OHKO/2HKO facts over exact cached builds, with intact mechanical qualifications."""
from __future__ import annotations

from copy import deepcopy
import json
import math

from counters import QueryError, TOP_N_DEFAULT, TOP_N_MAX, _build
import oppcache
import team_i18n as i18n

DIRECTIONS = ("both", "attacking", "defending")


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _classify(offense):
    """Read the calculator verdict, never recompute KO from percentages or static hit buckets."""
    if not isinstance(offense, dict) or not offense.get("move"):
        return None, "no_cached_offense"
    lo, hi = offense.get("min_percent"), offense.get("max_percent")
    if not (_number(lo) and _number(hi) and 0 <= lo <= hi):
        return None, "invalid_damage_range"
    chance = offense.get("ko_chance")
    if not isinstance(chance, dict) or type(chance.get("n")) is not int or chance["n"] < 1:
        return None, "no_classifiable_ko_verdict"
    if type(chance.get("guaranteed")) is not bool:
        return None, "invalid_ko_certainty"
    pct = chance.get("chance_pct")
    if pct is not None and (not _number(pct) or not 0 <= pct <= 100):
        return None, "invalid_ko_probability"
    caveats = offense.get("ko_caveats", [])
    if not isinstance(caveats, list) or any(not isinstance(c, dict) for c in caveats):
        return None, "invalid_ko_caveats"
    if "disguise_adjusted" in offense and not isinstance(offense["disguise_adjusted"], dict):
        return None, "invalid_ko_caveats"
    if "hits_range" in offense:
        span = offense["hits_range"]
        if not isinstance(span, (list, tuple)) or len(span) != 2 or any(type(n) is not int for n in span) or not 1 <= span[0] <= span[1]:
            return None, "invalid_hit_range"
    # Preserve entire qualifiers, not just their code: direction, cause and exemptions can differ.
    qualifications = {k: deepcopy(offense[k]) for k in ("ko_caveats", "disguise_adjusted", "hits_range") if offense.get(k)}
    return {"hits": chance["n"], "calculator_guaranteed": chance["guaranteed"],
            "conditional": bool(qualifications), "qualifications": qualifications}, None


def query(cache, selector, fmt, top_n=TOP_N_DEFAULT, direction="both"):
    if direction not in DIRECTIONS:
        raise QueryError("direction must be both, attacking or defending", query="direction")
    if fmt not in ("single", "double") or type(top_n) is not int or not 1 <= top_n <= TOP_N_MAX:
        raise QueryError("invalid format or top_n", query="ko-scan")
    built = cache.get("built_for") or {}
    if built.get("format") != fmt:
        raise oppcache.StaleCacheError("opponent cache format does not match the query")
    sets, matrix = cache.get("sets") or {}, cache.get("matrix")
    if not isinstance(matrix, dict):
        raise oppcache.StaleCacheError("opponent cache has no damage matrix; rebuild it")
    own_keys = oppcache.selector_keys(cache, selector, list(sets))
    if not own_keys:
        raise QueryError("no retained build for this exact battle form or variant", code="not_found", query="species")
    forms = {sets[k].get("run_form") or sets[k].get("species") for k in own_keys}
    if len(forms) != 1 or not next(iter(forms)):
        raise oppcache.StaleCacheError("reference cache identities are inconsistent")
    if any(sets[k].get("coverage") is not None and
           (not _number(sets[k]["coverage"]) or not 0 <= sets[k]["coverage"] <= 1) for k in own_keys):
        raise oppcache.StaleCacheError("reference coverage is invalid")
    # Match the online reference selection; exact variant selectors opt into a different retained set.
    reference = max(own_keys, key=lambda k: (sets[k].get("coverage") or 0, k))
    ranks = {(r.get("variant_id") or r.get("species")): r.get("rank") for r in cache.get("species") or []}
    field = [k for k in sets if type(ranks.get(k)) is int and 1 <= ranks[k] <= top_n]
    field.sort(key=lambda k: ranks[k])
    panels = {}
    for mode in (("attacking", "defending") if direction == "both" else (direction,)):
        groups, missing, outside = {}, [], []
        for other in field:
            ai, dj = (reference, other) if mode == "attacking" else (other, reference)
            coordinate = {"opponent_variant": other, "attacker_variant": ai, "defender_variant": dj}
            cell = (matrix.get(ai) or {}).get(dj)
            if sets[ai].get("real_team_backed") is not True:
                reason = "no_observed_attacking_build"
            elif not isinstance(cell, dict) or "error" in cell:
                reason = "missing_damage_cell"
            else:
                reason = None
            if reason:
                missing.append({**coordinate, "reason": reason})
                continue
            off = cell.get("offense")
            classified, reason = _classify(off)
            if reason:
                missing.append({**coordinate, "reason": reason, "damage": deepcopy(off)})
                continue
            if classified["hits"] not in (1, 2):
                outside.append({**coordinate, "ko_chance": deepcopy(off["ko_chance"]),
                                "reason": "calculator_verdict_above_two"})
                continue
            identity = json.dumps(classified, sort_keys=True, ensure_ascii=False)
            group = groups.setdefault(identity, {**classified, "rows": []})
            group["rows"].append({"opponent": _build(other, sets[other], ranks[other]),
                                  "attacker_variant": ai, "defender_variant": dj,
                                  "damage": deepcopy(off), "speed": deepcopy(cell.get("speed")),
                                  "ohko_moves": list(cell.get("ohko_moves") or [])})
        rows = list(groups.values())  # first occurrence in usage order; no strength ordering
        for group in rows:
            group["builds"] = len(group["rows"])
        matched = sum(g["builds"] for g in rows)
        panels[mode] = {"groups": rows, "missing": missing, "outside_scan": outside,
                        "coverage": {"compared_builds": len(field), "matched_builds": matched,
                                     "outside_scan_builds": len(outside), "missing_builds": len(missing),
                                     "calculation_complete": not missing}}
    return {"kind": "ko-scan", "pokemon": next(iter(forms)), "selector": selector,
            "format": fmt, "top_n": top_n, "direction": direction,
            "confidence": "low", "confidence_reason": "vs-observed-build", "built_for": deepcopy(built),
            "reference_build": _build(reference, sets[reference], ranks.get(reference)),
            "reference_selection": "exact_variant" if selector == reference else "highest_retained_coverage",
            "other_builds": [_build(k, sets[k], ranks.get(k)) for k in own_keys if k != reference],
            "panels": panels,
            "coverage": {"cache_top_k": built.get("top_k"), "requested_top_n": top_n,
                         "scope_complete": type(built.get("top_k")) is int and built["top_k"] >= top_n,
                         "unranked_variant_ids": [k for k in sets if type(ranks.get(k)) is not int or ranks[k] < 1]},
            "notes": [
                "One exact-form reference build is compared with retained top-N builds. Its other builds can give different results; use an exact variant id to select one.",
                "Attacking rows name DEFENDERS; defending rows name ATTACKERS. Item, ability and SP belong to that row's opponent build; no builds or moves are merged.",
                "Groups retain the cached ko_chance n and guaranteed fields. calculator_guaranteed applies only to the calculator reference, never an unqualified battle guarantee.",
                "KO caveats, Disguise adjustments and variable hit counts remain separate groups. They can prevent or change the indicated KO even when calculator_guaranteed is true.",
                "For variable multi-hit moves, the damage range is the full hit-count envelope; ko_chance can describe the central band. Read hits_range and central_percent together.",
                "The cache holds each attacker's hardest move by max damage, not every possible move. Missing/unclassifiable records are unknown, not proof of survival; outside_scan means the calculator reported more than two hits.",
                "This is a cached neutral static reference; recovery-aware ko_chance takes precedence over static damage buckets. Accuracy, switching, evolving multi-turn states and partners are not simulated; doubles is an isolated pair.",
                "Coverage/sample shares and usage ranks belong to the cache species pools, not form usage or win rates. Source/note disclose meta SP fallbacks. No cache write or data refresh occurs.",
            ]}


def format_md(out):
    own = out["reference_build"]
    lines = ["# " + i18n.t("ko_scan_title") + ": " + out["pokemon"],
             f"{own['species']} @ {own['item'] or '-'} / {own['ability']} / {own['nature']}", ""]
    for mode, panel in out["panels"].items():
        lines.append("## " + i18n.t("ko_scan_" + mode))
        for group in panel["groups"]:
            label = "OHKO" if group["hits"] == 1 else "2HKO"
            certainty = i18n.t("ko_scan_guaranteed" if group["calculator_guaranteed"] else "ko_scan_chance")
            qualification = i18n.t("ko_scan_conditional") if group["conditional"] else ""
            lines.append(f"### {label}: {certainty} {qualification}")
            if group["qualifications"]:
                lines.append(json.dumps(group["qualifications"], ensure_ascii=False))
            for row in group["rows"]:
                st, off = row["opponent"], row["damage"]
                verdict = off["ko_chance"].get("text") or json.dumps(off["ko_chance"], ensure_ascii=False)
                lines.append(f"- {st['species']} @ {st['item'] or '-'} / {st['ability']}: "
                             f"{off['move']} {off['min_percent']}-{off['max_percent']}%; {verdict}")
        lines.append(i18n.t("ko_scan_missing", n=panel["coverage"]["missing_builds"]))
        lines.append(i18n.t("ko_scan_outside", n=panel["coverage"]["outside_scan_builds"]))
        lines.append("")
    lines.extend("> " + note for note in out["notes"])
    return "\n".join(lines)
