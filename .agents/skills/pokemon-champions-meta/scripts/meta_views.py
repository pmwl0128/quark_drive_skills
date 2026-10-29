"""Small factual views over the published in-game meta snapshots."""
from __future__ import annotations

from typing import Any

from meta_common import normalize


def require_identity(doc: dict[str, Any], season: str, rule: str, fmt: str) -> None:
    if (doc.get("season"), doc.get("rule"), doc.get("format")) != (season, rule, fmt):
        raise ValueError("meta view file identity does not match the requested environment")


def who_uses(rows: list[dict[str, Any]], aliases: set[str], *, limit: int = 15,
             low_limit: int = 5) -> dict[str, Any]:
    hits = [r for r in rows if any(r.get(key) and normalize(r[key]) in aliases
                                   for key in ("name", "name_ja", "key"))]
    visible = sorted((r for r in hits if (r.get("percentage") or 0) >= 1),
                     key=lambda r: (-(r.get("percentage") or 0), r.get("pokemon_rank") or 9999))
    def compact(r: dict[str, Any]) -> dict[str, Any]:
        return {"name": r.get("pokemon_en") or r.get("pokemon"), "name_zh": r.get("pokemon"),
                "slug": r.get("slug"), "usage_rank": r.get("pokemon_rank"),
                "percentage": r.get("percentage")}
    top = [r for r in visible if (r.get("pokemon_rank") or 9999) <= 60]
    low = [r for r in visible if (r.get("pokemon_rank") or 9999) > 60]
    return {"pokemon": [compact(r) for r in top[:limit]],
            "low_usage_pokemon": [compact(r) for r in low[:low_limit]],
            "matched": len(hits), "omitted_below_1pct": len(hits) - len(visible),
            "omitted_rows": max(0, len(top) - limit) + max(0, len(low) - low_limit),
            "percentage_basis": "share_within_each_pokemon", "top_usage_rank": 60}


def trend(doc: dict[str, Any], usage: dict[str, Any] | None, slug: str,
          name: str) -> dict[str, Any]:
    periods = doc.get("periods") or []
    dates = [p["date"] for p in periods]
    ranks = (doc.get("ranks") or {}).get(slug)
    if ranks is not None and len(ranks) != len(dates):
        raise ValueError("rank trend length does not match its periods")
    out: dict[str, Any] = {"name": name, "slug": slug, "season": doc["season"],
                           "rule": doc["rule"], "format": doc["format"],
                           "generated_at": doc.get("generated_at"),
                           "rank_history": [[date, rank] for date, rank in zip(dates, ranks)] if ranks is not None else None,
                           "rank_history_note": "null rank means unranked at that update"}
    out["usage_available"] = usage is not None
    if usage is not None:
        if [p["date"] for p in usage.get("periods") or []] != dates:
            raise ValueError("usage trend periods do not match rank trend periods")
        panels = (usage.get("pokemon") or {}).get(slug) or {}
        shifts = {}
        for panel in ("moves", "items", "abilities", "natures"):
            rows = []
            for entry, values in (panels.get(panel) or {}).items():
                if len(values) != len(dates):
                    raise ValueError("usage trend values do not match its periods")
                if any(value is not None for value in values):
                    rows.append({"name": entry, "first": values[0], "latest": values[-1]})
            rows.sort(key=lambda row: (-(row["latest"] if row["latest"] is not None else -1), row["name"]))
            shifts[panel] = rows[:5]
        out["usage_shifts"] = shifts
        out["percentage_basis"] = "share_within_this_pokemon"
        out["usage_shift_note"] = "first/latest are the first and last stored updates; null means not listed"
    return out


def movers(doc: dict[str, Any], report: dict[str, Any] | None, *, limit: int = 6) -> dict[str, Any]:
    periods = doc.get("periods") or []
    dates = [p["date"] for p in periods]
    top_n = int(doc.get("top_n") or 30)
    changes: list[dict[str, Any]] = []
    entered: list[dict[str, Any]] = []
    for slug, ranks in (doc.get("ranks") or {}).items():
        if len(ranks) != len(dates):
            raise ValueError("rank trend length does not match its periods")
        if not ranks or ranks[-1] is None or ranks[-1] > top_n:
            continue
        first = next((rank for rank in ranks if rank is not None), None)
        start = ranks[0] if ranks[0] is not None else first
        if start is None:
            continue
        name = ((doc.get("names") or {}).get(slug) or {}).get("en") or slug
        was_outside = ranks[0] is None or ranks[0] > top_n
        row = {"name": name, "slug": slug, "from": start, "to": ranks[-1],
               "change": start - ranks[-1], "entered_top": was_outside}
        changes.append(row)
        if was_outside:
            entered.append({k: row[k] for k in ("name", "slug", "from", "to")})
    risers = sorted((r for r in changes if r["change"] > 0), key=lambda r: (-r["change"], r["to"]))
    fallers = sorted((r for r in changes if r["change"] < 0), key=lambda r: (r["change"], r["to"]))
    entered.sort(key=lambda r: r["to"])
    out: dict[str, Any] = {"season": doc["season"], "rule": doc["rule"], "format": doc["format"],
                           "generated_at": doc.get("generated_at"), "top_n": top_n,
                           "since": dates[0] if dates else None, "latest": dates[-1] if dates else None,
                           "risers": risers[:limit], "fallers": fallers[:limit],
                           "new_in_top": entered[:limit],
                           "omitted": {"risers": max(0, len(risers) - limit),
                                       "fallers": max(0, len(fallers) - limit),
                                       "new_in_top": max(0, len(entered) - limit)},
                           "window": "first_to_latest_stored_update"}
    if report is not None:
        out["last_update"] = {"at": (report.get("current") or {}).get("ranking_updated_at"),
                              "counts": {key: len(report.get(key) or []) for key in
                                         ("rank_moves", "new_entries", "dropped", "config_changes")}}
    return out
