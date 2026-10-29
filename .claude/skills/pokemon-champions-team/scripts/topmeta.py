#!/usr/bin/env python
"""Top Meta: the real-team library re-aggregated onto the in-game metagame's own fields, and the
symmetric comparison between the two populations.

The in-game metagame (the meta skill) describes the WHOLE ranked ladder. The real-team library is a
different population — mostly tournament entries in doubles, upper-ladder and community builds in
singles. This module answers "what does that population run, field by field" and "where does it
differ from the in-game numbers". It is a VIEW of the team library, never a second metagame
authority: it never feeds repset/frame (they already read the library's joint sets) and agreeing
with them is not corroboration.

Two halves, both pure:

- `build_table` turns de-identified counting units into the shipped table. Units are deduplicated
  on the dev side, where player identity exists (dev/update/team/topmeta.py): a team unit is one
  player's distinct six-species composition, a set unit one player's distinct full build. The table
  keeps every count; no publication threshold exists at this layer.
- `compare` joins one scope of the table with the in-game snapshot at query time, so a meta-only
  refresh never stales the shipped table. Each panel entry is ONE record carrying both sides; a
  consumer picks a perspective when rendering (in-game page vs Top Meta page), so the deviation is
  computed exactly once. `top_minus_ingame` is the Top Meta share minus the in-game share; the other
  perspective is its negation.

Sample size decides presentation, never existence (roadmap §10.3): below COUNT_TIER_BELOW units an
entry is a count (no numeric deviation, only two existence markers); from there to RELIABLE_N it is a
low-sample share; beyond that a share. The rule scope compares with the current in-game snapshot and
is labelled `window: current_snapshot`, because the two sides cover different time ranges. The
interval used for muting is Wilson 95% — a muting device, not a significance claim, because the units
are still a self-selected population.

No score field exists anywhere in this output, and none can be assembled from it.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

SCRIPTS = Path(__file__).resolve().parent
DEFAULT_DIR = SCRIPTS.parent / "data" / "top_meta"

SCHEMA_VERSION = 1
OUTPUT_SCHEMA = "top_meta.v1"
SCOPES = ("season", "rule", "high")
# The high-performance slice (scope "high"; 高端 / 上位 / top level): the rule scope's teams whose RAW
# performance facts pass a fixed bar inside their own source kind. It is a filter, never a weight:
# performance is not normalized across kinds, no unit counts more than once, and the slice reports how
# many units each kind contributed. Tournament and ladder bars stay separate facts.
HIGH_PLACING = 8                 # a top-8 finish ...
HIGH_MIN_FIELD = 16              # ... in an event of at least 16 entrants (smaller events have no cut)
HIGH_LADDER_RANK = 100           # a final ladder rank in the top 100 ...
HIGH_LADDER_RATING = 2000        # ... or a ladder rating of at least 2000
HIGH_TIERS = ("tournament_top8", "ladder_top")
VALUE_PANELS = ("moves", "items", "abilities", "natures", "spreads")
PANELS = VALUE_PANELS + ("partners",)
STAT_KEYS = ("hp", "atk", "def", "spa", "spd", "spe")

# Numeric comparisons start at five units; the reliable-distribution gate remains thirty.
COUNT_TIER_BELOW = 5
RELIABLE_N = 30
# A count-tier entry absent from the Top Meta sample is flagged "unseen" only when the in-game share
# makes zero observations in n draws this improbable.
UNSEEN_P = 0.05
WILSON_Z = 1.959963984540054
INGAME_KIND = {"moves": "move", "items": "item", "abilities": "ability", "natures": "nature"}


def table_dir() -> Path:
    """The shipped table dir (CHAMP_TOPMETA overrides, for the dev builder and tests)."""
    return Path(os.environ.get("CHAMP_TOPMETA", DEFAULT_DIR))


def table_path(fmt: str, rule: str) -> Path:
    return table_dir() / f"{rule}_{fmt}.json"


def load_table(fmt: str, rule: str) -> dict[str, Any] | None:
    path = table_path(fmt, rule)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def spread_key(spread: Mapping[str, Any] | None) -> str | None:
    """Fixed-order SP key `hp/atk/def/spa/spd/spe`; None when no spread was observed."""
    if not spread or not any(spread.get(k) for k in STAT_KEYS):
        return None
    return "/".join(str(int(spread.get(k) or 0)) for k in STAT_KEYS)


def spread_of(key: str) -> dict[str, int]:
    return {k: int(v) for k, v in zip(STAT_KEYS, key.split("/"))}


def high_tier(performance: Mapping[str, Any] | None) -> str | None:
    """The high-performance bar a team's raw performance facts pass, or None (unknown never passes)."""
    perf = performance or {}
    if perf.get("kind") == "tournament":
        placing, field = perf.get("placing"), perf.get("field_size")
        if isinstance(placing, int) and isinstance(field, int) and placing <= HIGH_PLACING \
                and field >= HIGH_MIN_FIELD:
            return "tournament_top8"
    if perf.get("kind") == "ladder":
        rank, rating = perf.get("placing"), perf.get("rating")
        if (isinstance(rank, int) and rank <= HIGH_LADDER_RANK) \
                or (isinstance(rating, (int, float)) and rating >= HIGH_LADDER_RATING):
            return "ladder_top"
    return None


def high_criteria() -> dict[str, Any]:
    return {"tournament_top8": {"placing_at_most": HIGH_PLACING, "field_size_at_least": HIGH_MIN_FIELD},
            "ladder_top": {"rank_at_most": HIGH_LADDER_RANK, "or_rating_at_least": HIGH_LADDER_RATING}}


def tier(n: int) -> str:
    if n < COUNT_TIER_BELOW:
        return "count"
    return "low" if n < RELIABLE_N else "reliable"


def wilson(k: int, n: int, z: float = WILSON_Z) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials, as fractions."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _share(count: int, n: int) -> float | None:
    return round(100.0 * count / n, 1) if n else None


def _sorted_counts(counts: Mapping[str, int]) -> dict[str, int]:
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


# --- build -------------------------------------------------------------------------------------

def build_scope(team_units: Iterable[Iterable[str]],
                set_units: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate de-identified units into per-species panels.

    `team_units`: each unit is one composition (species names, already folded by the builder).
    `set_units`: each unit is one full build {species, item, ability, nature, moves, spread?,
    completeness}. Every panel stores its own `n` — the units whose value for that field is known —
    so a field missing from a unit never counts as a zero."""
    species: dict[str, dict[str, Any]] = {}

    def entry(name: str) -> dict[str, Any]:
        if name not in species:
            species[name] = {"teams": 0, "sets": 0, "lanes": {},
                             "panels": {p: {"n": 0, "counts": {}} for p in PANELS}}
        return species[name]

    teams = 0
    for unit in team_units:
        members = sorted({s for s in unit if s})
        if not members:
            continue
        teams += 1
        for name in members:
            e = entry(name)
            e["teams"] += 1
            partners = e["panels"]["partners"]
            partners["n"] += 1
            for other in members:
                if other != name:
                    partners["counts"][other] = partners["counts"].get(other, 0) + 1
    sets = 0
    for unit in set_units:
        name = unit.get("species")
        if not name:
            continue
        sets += 1
        e = entry(name)
        e["sets"] += 1
        lane = str(unit.get("completeness") or "unknown")
        e["lanes"][lane] = e["lanes"].get(lane, 0) + 1
        values = {
            "moves": list(dict.fromkeys(m for m in (unit.get("moves") or []) if m)),
            "items": [unit["item"]] if unit.get("item") else [],
            "abilities": [unit["ability"]] if unit.get("ability") else [],
            "natures": [unit["nature"]] if unit.get("nature") else [],
            "spreads": [k] if (k := spread_key(unit.get("spread"))) else [],
        }
        for panel, vals in values.items():
            if not vals:
                continue
            p = e["panels"][panel]
            p["n"] += 1
            for v in vals:
                p["counts"][v] = p["counts"].get(v, 0) + 1
    for e in species.values():
        e["lanes"] = dict(sorted(e["lanes"].items()))
        for p in e["panels"].values():
            p["counts"] = _sorted_counts(p["counts"])
    return {"teams": teams, "sets": sets,
            "species": dict(sorted(species.items(), key=lambda kv: (-kv[1]["teams"], kv[0])))}


def build_table(*, fmt: str, rule: str, season: str, scopes: Mapping[str, Mapping[str, Any]],
                base_species: Mapping[str, str], built_for: Mapping[str, Any]) -> dict[str, Any]:
    """The shipped table. `scopes` maps each of SCOPES to {partitions, team_units, set_units} (the high
    slice also carries `composition`, its team units per HIGH_TIERS bar; a table built without it simply
    has no high scope); `base_species` is the dex base for every folded name whose base differs (the
    query-time join needs it to meet the in-game ranking's identity without calling the dex again)."""
    out_scopes = {}
    for scope in SCOPES:
        if scope == "high" and scope not in scopes:
            continue
        spec = scopes[scope]
        out_scopes[scope] = {"partitions": sorted(spec["partitions"]),
                             **build_scope(spec["team_units"], spec["set_units"])}
    out_scopes["season"]["season"] = season
    if "high" in out_scopes:
        out_scopes["high"].update(criteria=high_criteria(),
                                  composition=dict(scopes["high"].get("composition") or {}))
    return {"schema_version": SCHEMA_VERSION, "format": fmt, "rule": rule, "season": season,
            "built_for": dict(built_for),
            "base_species": dict(sorted((k, v) for k, v in base_species.items() if k != v)),
            "scopes": out_scopes}


# --- in-game side ------------------------------------------------------------------------------

def ingame_from_meta(ranking: Mapping[str, Any], details: Iterable[Mapping[str, Any]],
                     resolve_batch: Callable[[str, list[str]], Mapping[str, str | None]]
                     ) -> dict[str, Any]:
    """The in-game snapshot normalized to English canonical keys.

    `ranking` is the meta ranking document, `details` its detail rows (meta CLI or shipped store:
    same row shape). Panel names arrive zh/ja and resolve through the dex — the only naming
    authority — via `resolve_batch(kind, names) -> {name: canonical}`; an unresolvable name is a data
    bug and raises rather than silently dropping a row from the comparison."""
    rows = [r for r in (ranking.get("rows") or []) if r.get("pokemon_en")]
    ranks = {str(r["pokemon_en"]): int(r["rank"]) for r in rows}
    slugs = {str(r["pokemon_en"]): r.get("slug") for r in rows}
    details = [d for d in details if d and d.get("pokemon_en")]
    pending: dict[str, set[str]] = {kind: set() for kind in (*INGAME_KIND.values(), "pokemon")}

    def source_name(entry: Mapping[str, Any]) -> str:
        return str(entry.get("name_ja") or entry.get("name") or "")

    for d in details:
        panels = d.get("panels") or {}
        for panel, kind in INGAME_KIND.items():
            pending[kind].update(source_name(e) for e in panels.get(panel) or [])
        pending["pokemon"].update(source_name(e) for e in panels.get("partners") or []
                                  if not e.get("form_rep"))
    resolved: dict[str, dict[str, str | None]] = {
        kind: dict(resolve_batch(kind, sorted(n for n in names if n))) for kind, names in pending.items()}

    def en(kind: str, entry: Mapping[str, Any]) -> str:
        if kind == "pokemon" and entry.get("form_rep"):
            return str(entry["form_rep"])
        name = resolved[kind].get(source_name(entry))
        if not name:
            raise ValueError(f"dex cannot resolve {kind} name {source_name(entry)!r}")
        return name

    species: dict[str, dict[str, Any]] = {}
    for d in details:
        panels = d.get("panels") or {}
        out: dict[str, Any] = {}
        for panel, kind in INGAME_KIND.items():
            out[panel] = [(en(kind, e), e.get("percentage")) for e in panels.get(panel) or []]
        out["spreads"] = [(spread_key(e), e.get("percentage")) for e in panels.get("spreads") or []]
        out["partners"] = [en("pokemon", e) for e in panels.get("partners") or []]
        species[str(d["pokemon_en"])] = out
    return {"season": ranking.get("season"), "rule": ranking.get("rule"),
            "updated_at": ranking.get("updated_at"), "ranks": ranks, "slugs": slugs,
            "species": species}


# --- compare -----------------------------------------------------------------------------------

def _fold_scope(scope: Mapping[str, Any], base_species: Mapping[str, str],
                ingame_names: set[str]) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Merge library-only forms into the in-game ranking's identity.

    A form the in-game ranking has no row for (Aegislash-Blade, Palafin-Hero, ...) folds into its dex
    base when the base IS a ranking row; everything else keeps its own name. Counts add safely: the
    species clause keeps two forms of one species out of the same team."""
    def target(name: str) -> str:
        if not ingame_names or name in ingame_names:
            return name
        base = base_species.get(name)
        return base if base and base in ingame_names else name

    merged: dict[str, dict[str, Any]] = {}
    folds: dict[str, str] = {}
    for name, e in (scope.get("species") or {}).items():
        to = target(name)
        if to != name:
            folds[name] = to
        m = merged.setdefault(to, {"teams": 0, "sets": 0, "lanes": {},
                                   "panels": {p: {"n": 0, "counts": {}} for p in PANELS}})
        m["teams"] += e["teams"]
        m["sets"] += e["sets"]
        for lane, c in e.get("lanes", {}).items():
            m["lanes"][lane] = m["lanes"].get(lane, 0) + c
        for panel in PANELS:
            src = e["panels"][panel]
            dst = m["panels"][panel]
            dst["n"] += src["n"]
            for key, c in src["counts"].items():
                key = target(key) if panel == "partners" else key
                dst["counts"][key] = dst["counts"].get(key, 0) + c
    return merged, [{"from": k, "to": v} for k, v in sorted(folds.items())]


def _value_entry(panel: str, key: str, count: int, n: int, level: str,
                 listed: Mapping[str, float | None] | None, floor: float | None) -> dict[str, Any]:
    out: dict[str, Any] = {("spread" if panel == "spreads" else "name"):
                           (spread_of(key) if panel == "spreads" else key),
                           "count": count, "share": _share(count, n)}
    if listed is None:
        out.update(ingame=None, top_minus_ingame=None, within_noise=None, marker=None)
        return out
    value = listed.get(key)
    if value is not None:
        ingame: dict[str, Any] = {"kind": "pct", "value": value}
    elif floor is not None:
        ingame = {"kind": "bound", "below": floor}
    else:
        ingame = {"kind": "absent"}
    out["ingame"] = ingame
    delta = within = marker = None
    floor_delta = False
    if level == "count":
        if count > 0 and ingame["kind"] == "bound":
            marker = "outside_ingame_top"
        elif count == 0 and ingame["kind"] == "pct" and (1 - value / 100.0) ** n < UNSEEN_P:
            marker = "unseen_in_top"
    elif ingame["kind"] in ("pct", "bound"):
        lo, hi = wilson(count, n)
        if ingame["kind"] == "pct":
            delta = round(out["share"] - value, 1)
            within = lo * 100 <= value <= hi * 100
        elif out["share"] > ingame["below"]:
            # In-game only says "below the last listed share", so the difference is a LOWER bound.
            delta = round(out["share"] - ingame["below"], 1)
            floor_delta = True
            within = lo * 100 < ingame["below"]
        else:
            within = True
    out.update(top_minus_ingame=delta, within_noise=within, marker=marker)
    if floor_delta:
        out["delta_is_floor"] = True
    return out


def _value_panel(panel: str, top: Mapping[str, Any],
                 ingame: list[tuple[str | None, float | None]] | None) -> dict[str, Any]:
    n = int(top["n"])
    level = tier(n)
    listed = None
    floor = None
    if ingame is not None:
        listed = {k: v for k, v in ingame if k is not None}
        shares = [v for _, v in ingame if v is not None]
        floor = min(shares) if shares else None
    entries = [_value_entry(panel, key, int(c), n, level, listed, floor)
               for key, c in _sorted_counts(top["counts"]).items()]
    seen = set(top["counts"])
    for key, _ in ingame or []:
        if key is not None and key not in seen:
            seen.add(key)
            entries.append(_value_entry(panel, key, 0, n, level, listed, floor))
    return {"n": n, "tier": level, "entries": entries}


def _competition_ranks(counts: Mapping[str, int]) -> dict[str, int]:
    ranks: dict[str, int] = {}
    previous = None
    for position, (key, c) in enumerate(_sorted_counts(counts).items(), start=1):
        if c != previous:
            rank = position
            previous = c
        ranks[key] = rank
    return ranks


def _partner_panel(top: Mapping[str, Any], ingame: list[str] | None,
                   base_species: Mapping[str, str]) -> dict[str, Any]:
    n = int(top["n"])
    level = tier(n)
    counts = _sorted_counts(top["counts"])
    top_ranks = _competition_ranks(counts)
    in_rank: dict[str, int] = {}
    for i, name in enumerate(ingame or [], start=1):
        in_rank.setdefault(name, i)

    def ingame_rank(name: str) -> int | None:
        hit = in_rank.get(name)
        return hit if hit is not None else in_rank.get(base_species.get(name, ""))

    entries = []
    matched: set[int] = set()
    for name, c in counts.items():
        r = ingame_rank(name) if ingame is not None else None
        if r is not None:
            matched.add(r)
        e: dict[str, Any] = {"name": name, "count": int(c), "share": _share(int(c), n),
                             "rank": top_ranks[name]}
        if ingame is None:
            e.update(ingame=None, rank_delta=None)
        else:
            e["ingame"] = {"kind": "rank", "value": r} if r is not None else {"kind": "unlisted"}
            e["rank_delta"] = (r - top_ranks[name]) if (r is not None and level != "count") else None
        entries.append(e)
    for i, name in enumerate(ingame or [], start=1):
        if i not in matched and in_rank.get(name) == i:
            entries.append({"name": name, "count": 0, "share": _share(0, n), "rank": None,
                            "ingame": {"kind": "rank", "value": i}, "rank_delta": None})
    return {"n": n, "tier": level, "entries": entries}


def _ranking(merged: Mapping[str, Mapping[str, Any]], total: int,
             ingame: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    counts = {name: int(e["teams"]) for name, e in merged.items() if e["teams"] > 0}
    ranks = _competition_ranks(counts)
    shares = {name: c / total for name, c in counts.items()} if total else {}
    ordered = sorted(shares.values(), reverse=True)
    rows = []
    for name, c in _sorted_counts(counts).items():
        level = tier(c)
        row: dict[str, Any] = {"rank": ranks[name], "name": name, "teams": c,
                               "share": _share(c, total), "tier": level}
        if ingame is None:
            row.update(ingame_rank=None, rank_delta=None, within_noise=None)
        else:
            r = ingame["ranks"].get(name)
            row["ingame_rank"] = r
            if r is None or level == "count":
                row.update(rank_delta=None, within_noise=None)
            else:
                lo, hi = wilson(c, total)
                best = 1 + sum(1 for s in ordered if s > hi)
                worst = sum(1 for s in ordered if s >= lo)
                row.update(rank_delta=r - ranks[name], within_noise=best <= r <= worst)
        rows.append(row)
    return rows


def compare(table: Mapping[str, Any], scope: str, ingame: Mapping[str, Any] | None = None, *,
            species: Iterable[str] | None = None) -> dict[str, Any]:
    """One scope of the table, joined with the in-game snapshot.

    `ingame` (from `ingame_from_meta`) is the CURRENT in-game snapshot. The season scope compares with
    it only when it is the same season (`window: same_season`). The rule scope spans seasons whose
    in-game numbers cannot be pooled (snapshots are per season, the species ranking is ordinal), so it
    compares with the current in-game snapshot and says so (`window: current_snapshot`): the two
    sides then cover different time ranges, and every consumer must show that. The high slice is a
    subset of the rule scope and compares the same way. Identity folds to the in-game names whenever a
    snapshot is supplied, so links and joins agree across both views."""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}")
    if scope not in table["scopes"]:
        raise ValueError(f"this Top Meta table has no {scope} scope; rebuild it")
    doc = table["scopes"][scope]
    base_species = table.get("base_species") or {}
    same_rule = ingame is not None and ingame.get("rule") == table.get("rule")
    names = set((ingame or {}).get("ranks") or {}) if same_rule else set()
    merged, folds = _fold_scope(doc, base_species, names)
    same_season = ingame is not None and ingame.get("season") == doc.get("season", table.get("season"))
    comparable = same_rule and (scope != "season" or same_season)
    side = ingame if comparable else None
    wanted = set(species) if species is not None else None
    out_species: dict[str, Any] = {}
    for name, e in merged.items():
        if wanted is not None and name not in wanted:
            continue
        in_species = side["species"].get(name) if side else None
        panels = {}
        for panel in VALUE_PANELS:
            listed = in_species.get(panel) if in_species is not None else None
            panels[panel] = _value_panel(panel, e["panels"][panel], listed)
        panels["partners"] = _partner_panel(
            e["panels"]["partners"], in_species.get("partners") if in_species is not None else None,
            base_species)
        out_species[name] = {"teams": e["teams"], "sets": e["sets"], "lanes": e["lanes"],
                             "ingame_rank": side["ranks"].get(name) if side else None,
                             "panels": panels}
    return {
        "schema": OUTPUT_SCHEMA,
        "population": "top",
        "format": table["format"], "rule": table["rule"],
        "season": doc.get("season", table.get("season")) if scope == "season" else None,
        "scope": scope,
        "partitions": doc.get("partitions", []),
        "sample": {"teams": doc["teams"], "sets": doc["sets"]},
        "built_at": (table.get("built_for") or {}).get("built_at"),
        "compared_with": ({"population": "ingame", "season": side.get("season"),
                           "updated_at": side.get("updated_at"),
                           "window": "same_season" if scope == "season" else "current_snapshot"}
                          if side else None),
        "thresholds": {"count_below": COUNT_TIER_BELOW, "reliable": RELIABLE_N,
                       "unseen_p": UNSEEN_P, "interval": "wilson95"},
        **({"criteria": doc["criteria"], "composition": doc.get("composition") or {}}
           if scope == "high" else {}),
        "identity_folds": [f for f in folds if wanted is None or f["to"] in wanted],
        "ranking": [r for r in _ranking(merged, int(doc["teams"]), side)
                    if wanted is None or r["name"] in wanted],
        "species": out_species,
    }


def resolve_queries(table: Mapping[str, Any], scope: str, records: list[dict] | None,
                    ingame: Mapping[str, Any] | None = None) -> tuple[list[str] | None, list[dict]]:
    """Keep exact requested forms; a species-aggregate table cannot answer a Mega-only question."""
    if records is None:
        return None, []
    same_rule = ingame is not None and ingame.get("rule") == table.get("rule")
    merged, _ = _fold_scope(table["scopes"][scope], table.get("base_species") or {},
                            set((ingame or {}).get("ranks") or {}) if same_rule else set())
    wanted, queries = [], []
    for record in records:
        canonical = record.get("canonical") if record.get("ok") else None
        out = {key: record[key] for key in ("query", "canonical", "match_type", "score", "distance",
                                            "is_mega", "base_species", "required_item", "suggestions") if key in record}
        if not canonical:
            out.update(ok=False, error=record.get("error") or {"code": "not_found", "message": "name did not resolve"})
        elif record.get("is_mega") and canonical not in merged:
            out.update(ok=False, error={"code": "unsupported_mechanic", "message":
                       "published Top Meta counts are species aggregates; a Mega-only distribution cannot be recovered from marginal panels"})
            # base_species can be a Species Clause group (e.g. Floette), not a published roster row.
            if record.get("base_species") in merged:
                out["available_reference"] = {"name": record["base_species"], "granularity": "species_aggregate"}
        elif canonical not in merged:
            out.update(ok=False, error={"code": "not_found", "message": f"no published {scope} counts for this exact form"})
        else:
            wanted.append(canonical)
            out.update(ok=True, name=canonical, granularity="species_aggregate")
        queries.append(out)
    return wanted, queries
