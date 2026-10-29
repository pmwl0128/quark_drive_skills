"""Reverse CHECK facts from the shipped opponent cache; no ranking or new battle formula."""
from __future__ import annotations

from copy import deepcopy

import checks
import oppcache
import team_i18n as i18n

TOP_N_DEFAULT = 30
TOP_N_MAX = 60


class QueryError(ValueError):
    def __init__(self, message, *, code="bad_input", query="counters"):
        super().__init__(message)
        self.code, self.query = code, query

    def result(self):
        return {"ok": False, "query": self.query, "error": {"code": self.code, "message": str(self)}}


def prepare(species, fmt, top_n, resolve_fn):
    if fmt not in ("single", "double"):
        raise QueryError("--game-format single|double is required", code="unknown_format", query="game_format")
    top_n = TOP_N_DEFAULT if top_n is None else top_n
    if type(top_n) is not int or not 1 <= top_n <= TOP_N_MAX:
        raise QueryError(f"top_n must be an integer in 1..{TOP_N_MAX}", query="top_n")
    if not isinstance(species, str) or not species.strip():
        raise QueryError("a species name or exact variant id is required", query="species")
    if species.startswith("variant:"):
        return species, top_n, []
    rec = (resolve_fn([species], "pokemon", fuzzy=True) or {}).get(species) or {}
    if not rec.get("ok") or not rec.get("canonical"):
        raise QueryError("species must resolve unambiguously", code="unknown_pokemon", query="species")
    flags = ([{"path": "species", "kind": "pokemon", "from": species, "to": rec["canonical"],
               "match_type": "fuzzy", "score": rec.get("score"), "distance": rec.get("distance")}]
             if rec.get("match_type") == "fuzzy" else [])
    return rec["canonical"], top_n, flags


def _build(key, value, rank=None):
    return {"variant_id": key, "species": value.get("run_form") or value.get("species"),
            "pool_source": {"species": value.get("species"), "usage_rank": rank},
            **{k: deepcopy(value.get(k)) for k in (
                "item", "ability", "base_ability", "nature", "moves", "sps", "source", "note", "confidence",
                "real_team_backed", "coverage", "represented_coverage", "unrepresented_coverage",
                "variant_sample", "variant_count", "speed_profile")}}


def _pair(cache, ai, dj, fmt, rank):
    """Explain an existing grade with its original facts. Missing facts cannot become safe survival."""
    if any(cache["sets"][key].get("real_team_backed") is not True for key in (ai, dj)):
        return {"target_variant": dj, "grade": None, "reason": "no_observed_attacking_build"}
    compact = (cache["check_matrix"].get(ai) or {}).get(dj)
    if not isinstance(compact, dict) or compact.get("grade") not in ("C0", "C1", "C2"):
        return {"target_variant": dj, "grade": None, "reason": "missing_check"}
    matrix = cache.get("matrix") or {}
    fwd, rev = (matrix.get(ai) or {}).get(dj), (matrix.get(dj) or {}).get(ai)
    if (not isinstance(fwd, dict) or not isinstance(rev, dict) or "error" in fwd or "error" in rev
            or "offense" not in fwd or "offense" not in rev or "speed" not in fwd):
        return {"target_variant": dj, "grade": None, "reason": "missing_pair_evidence"}
    synth = oppcache._synth_cell(cache, ai, dj, rank)
    if synth is None:
        return {"target_variant": dj, "grade": None, "reason": "missing_pair_evidence"}
    explained = checks.build_check(synth, ai, cache["sets"][dj], fmt, member_set=cache["sets"][ai])
    if oppcache._compact_pair_check(explained) != compact:
        raise oppcache.StaleCacheError("cached CHECK disagrees with its damage evidence/current rubric; rebuild it")
    return {"target_variant": dj, "grade": compact["grade"], "check": explained,
            "offense": deepcopy(fwd.get("offense")), "incoming": deepcopy(rev.get("offense")),
            "speed": deepcopy(fwd.get("speed")), "ohko_moves": list(fwd.get("ohko_moves") or []),
            "incoming_ohko_moves": list(rev.get("ohko_moves") or [])}


def query(cache, selector, fmt, top_n=TOP_N_DEFAULT):
    """One concrete candidate build at a time. Never combine different builds into a stronger floor."""
    if fmt not in ("single", "double") or type(top_n) is not int or not 1 <= top_n <= TOP_N_MAX:
        raise QueryError("invalid format or top_n")
    built = cache.get("built_for") or {}
    if built.get("format") != fmt:
        raise oppcache.StaleCacheError("opponent cache format does not match the query")
    if not isinstance(cache.get("check_matrix"), dict):
        raise oppcache.StaleCacheError("opponent cache has no check_matrix; rebuild it")
    sets = cache.get("sets") or {}
    targets = oppcache.selector_keys(cache, selector, list(sets))
    if not targets:
        raise QueryError("no retained build for this exact battle form or variant", code="not_found", query="species")
    forms = {sets[k].get("run_form") or sets[k].get("species") for k in targets}
    if len(forms) != 1 or not next(iter(forms)):
        raise oppcache.StaleCacheError("target cache identities are inconsistent")
    target = next(iter(forms))
    ranks = {(r.get("variant_id") or r.get("species")): r.get("rank") for r in cache.get("species") or []}
    selected = [key for key in sets if type(ranks.get(key)) is int and 1 <= ranks[key] <= top_n]
    selected.sort(key=lambda key: ranks[key])  # usage order; ties retain cache order, never grade ordering
    rows, conditional, incomplete, unanswered = [], [], [], []
    for ai in selected:
        pairs = [_pair(cache, ai, dj, fmt, ranks.get(dj)) for dj in targets]
        cells = [{"opponent": target, "opponent_variant": p["target_variant"],
                  "opponent_is_modal": bool(sets[p["target_variant"]].get("is_modal")),
                  "opponent_coverage": sets[p["target_variant"]].get("coverage"),
                  "check": p.get("check")} for p in pairs]
        summary = checks.coverage_summary([{"member": ai, "cells": cells}])["by_opponent"][0]
        complete = summary["calculation_complete"]
        floor = summary["observed_floor"]["grade"] if complete else None
        row = {"candidate": _build(ai, sets[ai], ranks[ai]), "grade": floor,
               "calculation_complete": complete, "coverage": summary["coverage"],
               "observed_floor": summary["observed_floor"] if complete else None,
               "calculated_subset_floor": summary["observed_floor"] if not complete else None,
               "answers": [p for p in pairs if p["grade"] in ("C1", "C2")],
               "blocked_by": [p for p in pairs if p["grade"] == "C0"],
               "missing": [p for p in pairs if p["grade"] is None],
               "contested": any((p.get("check", {}).get("resolvability") or {}).get("verdict") == "contested" for p in pairs)}
        if not complete:
            incomplete.append(row)
        elif floor in ("C1", "C2"):
            rows.append(row)
        elif row["answers"]:
            conditional.append(row)
        else:
            # No answer exists on this retained surface. Keep the build id for transparent accounting.
            unanswered.append(ai)
    return {"kind": "counters", "pokemon": target, "format": fmt, "selector": selector, "top_n": top_n,
            "confidence": "low", "confidence_reason": "vs-observed-build",
            "built_for": deepcopy(built),
            "target_builds": [_build(k, sets[k], ranks.get(k)) for k in targets],
            "coverage_basis": sorted({sets[k].get("species") for k in targets}),
            "rows": rows, "conditional_rows": conditional, "incomplete_rows": incomplete,
            "coverage": {"cache_top_k": built.get("top_k"), "requested_top_n": top_n,
                         "scope_complete": type(built.get("top_k")) is int and built["top_k"] >= top_n,
                         "candidate_builds": len(selected), "complete_answers": len(rows),
                         "conditional_answers": len(conditional), "incomplete_builds": len(incomplete),
                         "unanswered_builds": len(unanswered), "unanswered_variant_ids": unanswered,
                         "unranked_variant_ids": [k for k in sets if type(ranks.get(k)) is not int or ranks[k] < 1]},
            "notes": [
                "Each row is ONE concrete candidate build, never all builds of its species. Usage order is not a strength ranking.",
                "C2: safe switch-in then static 1v1; C1: even-footing only. Grades cover retained builds of the exact target form.",
                "Conditional rows keep floor C0: answer only the named target builds and retain every C0 blocker. Missing checks cannot establish a full floor.",
                "Coverage is retained sample mass within coverage_basis (the cache species pool), not form usage or a win rate; excluded tail remains unknown.",
                "The cache supplies neutral reference conditions and each side's hardest move; doubles is an isolated pair. Priority, setup, recovery and KO caveats stay qualified.",
                "Observed joint builds can contain disclosed meta SP fallback; source/note/speed_profile identify assumptions. No new damage calculation or cache write occurs.",
            ]}


def format_md(out):
    targets = {r["variant_id"]: r for r in out["target_builds"]}
    lines = ["# " + i18n.t("counters_title") + ": " + out["pokemon"], ""]
    for group in ("rows", "conditional_rows", "incomplete_rows"):
        lines.append("## " + i18n.t("counters_" + group))
        for row in out[group]:
            own = row["candidate"]
            lines.append(f"- {own['species']} @ {own['item'] or '-'} / {own['ability']}: {row['grade'] or '?'}")
            for key in ("answers", "blocked_by", "missing"):
                if row[key]:
                    details = [f"{targets[p['target_variant']]['item'] or '-'} / {targets[p['target_variant']]['ability']}: {p['grade'] or '?'}" for p in row[key]]
                    lines.append("  - " + i18n.t("counters_" + key) + ": " + "; ".join(details))
            if row["contested"]:
                lines.append("  - " + i18n.t("counters_contested"))
        lines.append("")
    lines.extend("> " + note for note in out["notes"])
    return "\n".join(lines)
