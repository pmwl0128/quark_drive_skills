"""Fact tools beyond one Pokemon's current-season page (frontend/design.md §7.2): the type chart with
defensive abilities, the current regulation, move search, and the meta views the site already
publishes — who runs an entry, singles vs doubles, rank history, rank movers and Top Meta.

Each tool returns (trace, content) like `qa._run_tool_once` and reads only the shipped skill data
or the public projection. Constants come from their owning skill module (typechart, rules,
selection); nothing here keeps a second copy of a rule or a chart.
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from .. import mappers
from ..paths import META_DATA

META_PANELS = {"move": "moves", "item": "items", "ability": "abilities", "nature": "natures"}
WHO_USES_ROWS = 15
WHO_USES_LOW_ROWS = 5
WHO_USES_TOP_RANK = 60           # the usage pool the team skill's candidate retrieval draws from
WHO_USES_MIN_PCT = 1.0
MOVE_FIND_ROWS = 40
TREND_PANEL_ROWS = 5
MOVERS_ROWS = 6
TOP_META_ROWS = 20
TOP_META_SCOPES = ("season", "rule", "high")
_NUM_EXPR = re.compile(r"^(>=|<=|>|<|=)?\s*-?\d{1,3}$")
_TYPE_SUFFIX = re.compile(r"(属性|系|タイプ|\s*type)$", re.IGNORECASE)


def _team_module(name: str):
    """A pure team-skill module (typechart, rules, selection): the owner of that rule or chart."""
    scripts = str(mappers._TEAM_SCRIPTS)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module(name)


def _authority() -> dict:
    return json.loads((META_DATA / "current.json").read_text(encoding="utf-8"))


def previous_season() -> tuple[str, str] | None:
    """The season before the current one in the registration order, when its data ships."""
    authority = _authority()
    order = list(authority.get("seasons") or {})
    current = authority["current"]["season"]
    if current not in order or order.index(current) == 0:
        return None
    season = order[order.index(current) - 1]
    rule = (authority["seasons"].get(season) or {}).get("rule")
    if not rule or not (META_DATA / f"ranking_{season}_single.json").is_file():
        return None
    return season, rule


def season_args(season: Any) -> tuple[list[str], dict | None]:
    """meta CLI arguments and a disclosure for `season`: "previous" or the current season."""
    if season != "previous":
        return [], None
    prev = previous_season()
    if prev is None:
        raise ValueError("no previous-season data ships")
    current_rule = _authority()["current"]["rule"]
    same = prev[1] == current_rule
    return ["--season", prev[0]], {
        "season": prev[0], "rule": prev[1],
        "note": f"previous season {prev[0]} under rule {prev[1]}"
                + (" (the same rule as the current season)" if same else
                   f", a different rule from the current {current_rule}: its roster and items can differ")}


def type_name(raw: Any) -> str | None:
    """Canonical type from any language ('地面', 'じめん', 'ground', '地面系')."""
    text = _TYPE_SUFFIX.sub("", str(raw or "").strip())
    if not text:
        return None
    types = _team_module("typechart").TYPES
    if text.title() in types:
        return text.title()
    row = mappers._conn().execute("select canonical from aliases where kind = 'type' and normalized = ?",
                                  (text.lower(),)).fetchone()
    if row and row[0] in types:
        return row[0]
    import dex_i18n     # the dex's own zh/ja type labels (mappers puts its scripts on sys.path)
    return next((t for t, labels in dex_i18n.TYPE_NAMES.items() if text in labels and t in types), None)


def _grouped(table: dict[str, float]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for attack, mult in sorted(table.items(), key=lambda kv: -kv[1]):
        if mult != 1:
            out.setdefault(f"{mult:g}x", []).append(attack)
    return out


def type_matchup(pool: Any, args: dict, timeout: float) -> tuple[tuple | None, str]:
    from . import qa, calc_scenario
    chart = _team_module("typechart")
    attack = type_name(args["attack_type"]) if args.get("attack_type") else None
    if args.get("attack_type") and attack is None:
        return None, json.dumps({"error": f"unknown type {args['attack_type']!r}"})
    name = qa._clean_name(args.get("pokemon"))
    abilities: list[str] = []
    if name:
        q = qa._resolve_query(pool, name, "pokemon", timeout)
        card = pool.request_json("dex", ["pokemon", q, "--format", "json"], timeout=timeout)
        if qa._not_found(card):
            return ("dex.pokemon", f"dex pokemon {q}", {"kind": "pokemon", "name": q}), \
                qa.clip_json({"note": qa.NOT_IN_DEX, **card})
        if not isinstance(card, dict) or card.get("error") or card.get("ok") is False:
            return None, qa.clip_json(qa._english_only(card))
        label, types, abilities = card["name"], list(card.get("types") or []), list(card.get("abilities") or [])
    else:
        raw_types = args.get("types") if isinstance(args.get("types"), list) else []
        types = [t for t in (type_name(t) for t in raw_types[:2]) if t]
        if not types and not attack:
            return None, json.dumps({"error": "give pokemon, types or attack_type"})
        if len(types) != len(raw_types[:2]):
            return None, json.dumps({"error": f"unknown type in {raw_types[:2]!r}"})
        label = "/".join(types) if types else attack
    if args.get("ability"):
        ability = calc_scenario.resolve_strict(pool, str(args["ability"]), "ability", timeout)
        if ability is None:
            raise calc_scenario.NotInGame(f"ability {args['ability']!r} is not in Pokemon Champions")
        abilities = [ability]
    note = ("Fixed type chart with the DEFENDER's ability modifiers: type immunities (Levitate, Water Absorb, "
            "Flash Fire, Sap Sipper ...), Thick Fat / Heatproof / Water Bubble / Purifying Salt / Fluffy, and "
            "Filter / Solid Rock (0.75x on super-effective hits). Not applied: attacker abilities (Scrappy, "
            "Mold Breaker, -ate skins), move exceptions (Freeze-Dry, Thousand Arrows), resist berries and "
            "weather. Unlisted attack types are neutral (1x). For damage use calc_damage.")
    out: dict[str, Any] = {"defender": label if types else None, "types": types}
    if not types:
        out = {"attackType": attack,
               "superEffectiveOn": [t for t in chart.TYPES if chart.effectiveness(attack, [t]) > 1],
               "resistedBy": [t for t in chart.TYPES if 0 < chart.effectiveness(attack, [t]) < 1],
               "noEffectOn": [t for t in chart.TYPES if chart.effectiveness(attack, [t]) == 0],
               "note": "single-type defenders; a dual type multiplies both factors. " + note}
    else:
        if abilities:
            out["abilities"] = abilities
        plain = {a: chart.effectiveness(a, types) for a in chart.TYPES}
        applied = abilities[0] if len(abilities) == 1 else None
        table = {a: chart.effectiveness_for_member(types, applied, a) for a in chart.TYPES} if applied else plain
        if attack:
            out.update(attackType=attack, multiplier=table[attack])
        else:
            out["table"] = _grouped(table)
        if applied:
            out["appliedAbility"] = applied
        else:
            effects = []
            for ability in abilities:
                changed = {a: f"{chart.effectiveness_for_member(types, ability, a):g}x" for a in chart.TYPES
                           if chart.effectiveness_for_member(types, ability, a) != plain[a]
                           and (attack is None or a == attack)}
                if changed:
                    effects.append({"ability": ability, "changes": changed})
            if effects:
                out["abilityEffects"] = effects
        out["note"] = note
    return ("dex.types", f"type matchup {label}", {"name": label}), json.dumps(out, ensure_ascii=False)


def rules_facts() -> tuple[tuple, str]:
    rules = _team_module("rules")
    pick = _team_module("selection").PICK_SIZE
    authority = _authority()
    current = authority["current"]
    rs = rules.get_ruleset(current["season"], current["rule"])
    out = {"season": current["season"], "rule": current["rule"],
           "teamSize": {"min": rs.team_min, "max": rs.team_max},
           "pickPerBattle": dict(pick),
           "spPerStatMax": rs.sp_per_stat_cap, "spTotalMax": rs.sp_total_cap,
           "speciesClause": rs.species_clause, "itemClause": rs.item_clause,
           "movesPerPokemon": rs.moves_per_pokemon,
           "megaEvolution": "several Mega Stone holders may be registered and brought; only one Pokemon "
                            "may Mega Evolve per battle",
           "seasons": [{"season": s, "rule": (row or {}).get("rule")}
                       for s, row in (authority.get("seasons") or {}).items()],
           "note": "SP (stat points) replace EVs: each stat 0..spPerStatMax, all six at most spTotalMax. "
                   "Species Clause: one per base species; Item Clause: each held item at most once. The "
                   "Pokemon available are exactly the battle dex (use dex tools)."}
    return ("rules.current", f"rules {current['rule']}", {"rule": current["rule"], "season": current["season"]}), \
        json.dumps(out, ensure_ascii=False)


def move_find(pool: Any, args: dict, timeout: float) -> tuple[tuple | None, str]:
    from . import qa
    leaves, label = [], []
    if args.get("type"):
        t = type_name(args["type"])
        if t is None:
            return None, json.dumps({"error": f"unknown type {args['type']!r}"})
        leaves.append({"type": t})
        label.append(t)
    if args.get("category"):
        if args["category"] not in ("Physical", "Special", "Status"):
            return None, json.dumps({"error": "category is Physical, Special or Status"})
        leaves.append({"category": args["category"]})
        label.append(args["category"])
    for key in ("power", "priority", "accuracy"):
        value = args.get(key)
        if value in (None, ""):
            continue
        text = str(value).replace(" ", "")
        if not _NUM_EXPR.match(text):
            return None, json.dumps({"error": f"{key} must look like '>=90', '<=-1' or '100'"})
        leaves.append({key: text})
        label.append(f"{key}{text if text[0] in '<>=' else '=' + text}")
    learner = qa._clean_name(args.get("learner"))
    learnset = None
    if learner:
        q = qa._resolve_query(pool, learner, "pokemon", timeout)
        card = pool.request_json("dex", ["pokemon", q, "--format", "json"], timeout=timeout)
        if qa._not_found(card):
            return ("dex.pokemon", f"dex pokemon {q}", {"kind": "pokemon", "name": q}), \
                qa.clip_json({"note": qa.NOT_IN_DEX, **card})
        if not isinstance(card, dict) or card.get("error") or card.get("ok") is False:
            return None, qa.clip_json(qa._english_only(card))
        learnset = set(card.get("moves") or [])
        label.append(f"learnable by {card['name']}")
    if not leaves:
        return None, json.dumps({"error": "move_find needs type, category, power, priority or accuracy"})
    where = leaves[0] if len(leaves) == 1 else {"and": leaves}
    doc = pool.request_json("dex", ["find-move", "--where", json.dumps(where), "--format", "json"],
                            timeout=timeout)
    results = doc.get("results") if isinstance(doc, dict) else None
    text = " + ".join(label)
    trace = ("dex.find_move", f"move search {text}", {"conditions": text})
    if not isinstance(results, list):
        return trace, qa.clip_json(doc)
    rows = [r for r in results if isinstance(r, dict) and (learnset is None or r.get("name") in learnset)]
    rows.sort(key=lambda r: (-(r.get("power") or 0), r.get("name") or ""))
    out: dict[str, Any] = {"conditions": label, "count": len(rows), "moves": [
        {k: r.get(k) for k in ("name", "type", "category", "power", "accuracy", "priority")}
        for r in rows[:MOVE_FIND_ROWS]]}
    if len(rows) > MOVE_FIND_ROWS:
        out["note"] = (f"{len(rows)} moves match; the {MOVE_FIND_ROWS} with the highest power are listed. Give "
                       "the total and suggest narrowing by type, category or power.")
    if doc.get("unresolved_conditions"):
        out["unresolved"] = doc["unresolved_conditions"]
    return trace, json.dumps(out, ensure_ascii=False)


# --- meta views ------------------------------------------------------------------------------

def _english_names(pool: Any, fmt: str, timeout: float, season: list[str] | None = None) -> dict[str, str]:
    doc = pool.request_json("meta", ["ranking", "--format", fmt, "--limit", "400", *(season or []),
                                     "--output", "json"], timeout=timeout)
    return {r.get("slug"): r.get("pokemon_en") or r.get("name") for r in (doc or {}).get("rows") or []
            if isinstance(r, dict) and r.get("slug")}


def _species_for_meta(pool: Any, name: str, timeout: float) -> tuple[str, str | None, dict | None]:
    """(meta species, note, miss): the meta keys a Mega by its base species."""
    from . import qa
    q = qa._resolve_query(pool, name, "pokemon", timeout)
    card = pool.request_json("dex", ["pokemon", q, "--format", "json"], timeout=timeout)
    if qa._not_found(card):
        return q, None, {"pokemon": q, "note": qa.NOT_IN_DEX, **card}
    if not isinstance(card, dict) or card.get("error") or card.get("ok") is False:
        return q, None, qa._english_only(card)
    if card.get("is_mega") and card.get("base_species"):
        return str(card["base_species"]), (f"the metagame records {card['name']} under its base species "
                                           f"{card['base_species']}"), None
    return card["name"], None, None


def _who_uses(pool: Any, args: dict, fmt: str, timeout: float) -> tuple[tuple | None, Any]:
    from . import qa
    kind = args.get("entry_kind")
    entry = qa._clean_name(args.get("entry"))
    if kind not in META_PANELS or entry is None:
        return None, {"error": "who_uses needs entry and entry_kind (move, item, ability or nature)"}
    canonical = qa._resolve_query(pool, entry, kind, timeout)
    card = pool.request_json("dex", [kind, canonical, "--format", "json"], timeout=timeout)
    if not isinstance(card, dict) or card.get("error") or card.get("ok") is False or not card.get("name"):
        return (f"dex.{kind}", f"dex {kind} {canonical}", {"kind": kind, "name": canonical}), \
            {"note": f"no {kind} in the Champions battle dex matches this name; it is not in this game", **(
                qa._english_only(card) if isinstance(card, dict) else {})}
    canonical = card["name"]
    names = {card.get("display_name"), card.get("display_name_ja"), canonical}
    rows = pool.request_json("meta", ["search", "--format", fmt, "--panel", META_PANELS[kind], "--name", canonical,
                                      "--limit", "500", "--output", "json"], timeout=timeout)
    english = _english_names(pool, fmt, timeout)
    hits = [r for r in rows if isinstance(r, dict) and (r.get("name") in names or r.get("name_ja") in names)] \
        if isinstance(rows, list) else []
    hits = [{"pokemon": english.get(r.get("slug"), r.get("slug")), "usageRank": r.get("pokemon_rank"),
             "pct": r.get("percentage")} for r in hits]
    listed = sorted((h for h in hits if (h["pct"] or 0) >= WHO_USES_MIN_PCT),
                    key=lambda h: (-(h["pct"] or 0), h["usageRank"] or 999))
    top = [h for h in listed if (h["usageRank"] or 999) <= WHO_USES_TOP_RANK]
    low = [h for h in listed if (h["usageRank"] or 999) > WHO_USES_TOP_RANK]
    out = {"entry": canonical, "kind": kind, "format": fmt, "pokemon": top[:WHO_USES_ROWS],
           "lowUsagePokemon": low[:WHO_USES_LOW_ROWS], "matched": len(hits),
           "omittedBelowMinPct": sum(1 for h in hits if (h["pct"] or 0) < WHO_USES_MIN_PCT),
           "omittedRows": max(0, len(top) - WHO_USES_ROWS) + max(0, len(low) - WHO_USES_LOW_ROWS),
           "note": f"pct = share of that Pokemon's own usage data that runs {canonical} (its {META_PANELS[kind]} "
                   f"panel), not a share of all teams; usageRank = the Pokemon's usage rank. pokemon = the "
                   f"usage top {WHO_USES_TOP_RANK}, sorted by pct; lowUsagePokemon = rarely used Pokemon that "
                   f"often run it. Rows under {WHO_USES_MIN_PCT:g}% are counted, not listed."}
    return ("meta.who_uses", f"who uses {canonical} ({fmt})", {"entry": canonical, "format": fmt}), out


def _formats(pool: Any, name: str, timeout: float) -> tuple[tuple, Any]:
    from . import qa
    q = qa._resolve_query(pool, name, "pokemon", timeout)
    sides = {}
    for fmt in ("single", "double"):
        detail = qa._meta_detail(pool, fmt, q, timeout)
        if isinstance(detail, dict) and "panels" not in detail and detail.get("rank") is not None:
            sides[fmt] = {k: detail.get(k) for k in ("rank", "note") if detail.get(k) is not None}
            for panel in ("moves", "items", "abilities", "natures"):
                sides[fmt][panel] = (detail.get(panel) or [])[:5]
            sides[fmt]["partners"] = (detail.get("partners") or [])[:5]
        else:
            sides[fmt] = {"note": (detail or {}).get("note") or "no usage data in this format",
                          **({"error": detail["error"]} if isinstance(detail, dict) and detail.get("error") else {})}
    out = {"pokemon": q, "single": sides["single"], "double": sides["double"],
           "note": "Current-season usage per format: rank and the top five entries of each panel with their share."}
    return ("meta.formats", f"singles vs doubles {q}", {"pokemon": q}), out


def _projection_json(projection: Path | None, rel: str) -> dict | None:
    path = projection / rel if projection is not None else None
    if path is None or not path.is_file() or path.stat().st_size > 2_000_000:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _trend(pool: Any, name: str, fmt: str, timeout: float, projection: Path | None) -> tuple[tuple, Any]:
    species, note, miss = _species_for_meta(pool, name, timeout)
    trace = ("meta.trend", f"usage trend {species} ({fmt})", {"pokemon": species, "format": fmt})
    if miss:
        return trace, miss
    current = _authority()["current"]
    raw = json.loads((META_DATA / f"trend_{current['season']}_{fmt}.json").read_text(encoding="utf-8"))
    slug = mappers.slugify(species)
    dates = [p["date"] for p in raw.get("periods") or []]
    ranks = (raw.get("ranks") or {}).get(slug)
    out: dict[str, Any] = {"pokemon": species, "format": fmt, "season": current["season"]}
    if note:
        out["formNote"] = note
    if ranks:
        out["rankHistory"] = [[d, r] for d, r in zip(dates, ranks)]
    else:
        out["rankHistory"] = None
        out["rankNote"] = "no rank history: not among the Pokemon this season's trend tracks"
    prev = previous_season()
    if prev:
        doc = pool.request_json("meta", ["ranking", "--format", fmt, "--season", prev[0], "--limit", "400",
                                         "--output", "json"], timeout=timeout)
        row = next((r for r in (doc or {}).get("rows") or [] if r.get("slug") == slug), None)
        out["previousSeason"] = {"season": prev[0], "rule": prev[1], "finalRank": row.get("rank") if row else None}
    usage = _projection_json(projection, f"meta/usage_trend_{fmt}/{slug}.json")
    if usage and usage.get("season") == current["season"]:
        shifts = {}
        for panel in ("moves", "items", "abilities", "natures"):
            rows = []
            for entry in usage.get("panels", {}).get(panel) or []:
                values = [v for v in entry.get("values") or [] if v is not None]
                if values:
                    rows.append({"name": entry["name"], "first": values[0], "latest": entry["values"][-1]})
            rows.sort(key=lambda r: -(r["latest"] or 0))
            shifts[panel] = rows[:TREND_PANEL_ROWS]
        out["usageShifts"] = shifts
        out["periods"] = [usage["periods"][0], usage["periods"][-1]] if usage.get("periods") else None
    out["note"] = ("rankHistory = [date, usage rank] per data update this season (null = unranked then). "
                   "usageShifts = each panel's share at the season's first update (first) and now (latest); "
                   "latest null = no longer listed. Shares are within this Pokemon's usage data.")
    return trace, out


def _movers(fmt: str) -> tuple[tuple, Any]:
    current = _authority()["current"]
    raw = json.loads((META_DATA / f"trend_{current['season']}_{fmt}.json").read_text(encoding="utf-8"))
    dates = [p["date"] for p in raw.get("periods") or []]
    names = raw.get("names") or {}
    top = int(raw.get("top_n") or 30)
    changes, new = [], []
    for slug, ranks in (raw.get("ranks") or {}).items():
        if not ranks or ranks[-1] is None or ranks[-1] > top:
            continue
        first = next((r for r in ranks if r is not None), None)
        name = (names.get(slug) or {}).get("en") or slug
        entered = ranks[0] is None or ranks[0] > top
        if entered:
            new.append({"pokemon": name, "from": ranks[0], "to": ranks[-1]})
        start = ranks[0] if ranks[0] is not None else first
        if start is not None:
            changes.append({"pokemon": name, "from": start, "to": ranks[-1], "change": start - ranks[-1],
                            "enteredTop": entered})
    risers = sorted((c for c in changes if c["change"] > 0), key=lambda c: (-c["change"], c["to"]))[:MOVERS_ROWS]
    fallers = sorted((c for c in changes if c["change"] < 0), key=lambda c: (c["change"], c["to"]))[:MOVERS_ROWS]
    out: dict[str, Any] = {"format": fmt, "season": current["season"], "topN": top,
                           "since": dates[0] if dates else None, "latest": dates[-1] if dates else None,
                           "risers": risers, "fallers": fallers,
                           "newInTop": sorted(new, key=lambda c: c["to"])[:MOVERS_ROWS]}
    report_path = META_DATA / f"report_{current['season']}_{fmt}.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        latest = {k: len(report.get(k) or []) for k in ("rank_moves", "new_entries", "dropped", "config_changes")}
        out["lastUpdate"] = {"at": (report.get("current") or {}).get("ranking_updated_at"), "counts": latest}
    out["note"] = (f"Usage-rank changes among this season's top {top} from its first data update (since) to the "
                   "latest; from = rank then, to = rank now, change = places gained; enteredTop = it was outside "
                   f"the top {top} at the start (false = it was already inside). "
                   "lastUpdate counts the threshold-crossing changes of the latest data update alone.")
    return ("meta.movers", f"rank movers ({fmt})", {"format": fmt}), out


def _real_teams(pool: Any, args: dict, fmt: str, timeout: float, projection: Path | None) -> tuple[tuple, Any]:
    from . import qa
    scope = args.get("scope") if args.get("scope") in TOP_META_SCOPES else "season"
    current = _authority()["current"]
    name = qa._clean_name(args.get("pokemon"))
    population = {"season": "this season", "rule": "the whole rule",
                  "high": "the high-performance slice of the whole rule"}[scope]
    note = ("Top Meta = usage counted from collected real teams (tournament and ladder samples) of "
            f"{population}; share = % of sampled teams. ingameRank / ingame = the official in-game usage; "
            "rankDelta = ingameRank - rank (positive: ranks higher in real teams than in-game); delta = share - "
            "ingame share; withinNoise = the difference is within sampling noise; tier 'count' = too few teams "
            "for a reliable share.")
    if scope == "high":
        note += (" The high slice keeps only teams with a top-8 finish in an event of at least 16 entrants, or a "
                 "ladder top-100 rank or 2000+ rating (criteria); composition = team units per kind. Name it as "
                 "high-performance real teams, never as the whole ladder or the in-game usage.")

    def current_doc(doc: dict | None) -> bool:
        return bool(doc) and doc.get("rule") == current["rule"] \
            and (scope != "season" or doc.get("season") == current["season"])

    def header(block: dict) -> dict:
        return {k: block[k] for k in ("criteria", "composition") if block.get(k)}

    if name is None:
        doc = _projection_json(projection, f"meta/top_ranking_{fmt}_{scope}.json")
        limit = max(1, min(int(args.get("limit") or TOP_META_ROWS), 40))
        trace = ("meta.real_ranking", f"real-team usage top{limit} ({fmt}, {scope})",
                 {"format": fmt, "limit": limit, "scope": scope})
        if not current_doc(doc):
            return trace, {"error": "real-team usage for the current environment is not installed"}
        rows = [{k: r.get(k) for k in ("rank", "name", "share", "teams", "ingameRank", "rankDelta", "withinNoise", "tier")}
                for r in (doc.get("rows") or [])[:limit]]
        return trace, {"format": fmt, "scope": scope, "season": doc.get("season"), "rule": doc["rule"],
                       "sample": doc.get("sample"), **header(doc), "builtAt": doc.get("builtAt"), "rows": rows,
                       "note": note}
    species, form_note, miss = _species_for_meta(pool, name, timeout)
    trace = ("meta.real_teams", f"real-team usage {species} ({fmt}, {scope})",
             {"format": fmt, "pokemon": species, "scope": scope})
    if miss:
        return trace, miss
    doc = _projection_json(projection, f"meta/top_detail_{fmt}/{mappers.slugify(species)}.json")
    if doc is None:
        return trace, {"pokemon": species, "note": "no real-team usage recorded for this Pokemon in this format"}
    if doc.get("season") != current["season"] or doc.get("rule") != current["rule"]:
        return trace, {"error": "real-team usage for the current environment is not installed"}
    block = (doc.get("scopes") or {}).get(scope)
    if not block:
        return trace, {"pokemon": species, "format": fmt, "scope": scope,
                       "note": f"no real team of {population} records this Pokemon"}
    out: dict[str, Any] = {"pokemon": species, "format": fmt, "scope": scope,
                           **{k: block.get(k) for k in ("rank", "share", "teams", "tier", "ingameRank", "rankDelta",
                                                        "withinNoise", "sample")}, **header(block)}
    if form_note:
        out["formNote"] = form_note
    panels = block.get("panels") or {}
    for panel in ("moves", "items", "abilities", "natures"):
        out[panel] = [{"name": e.get("name"), "share": e.get("share"),
                       "ingame": (e.get("ingame") or {}).get("value"), "delta": e.get("delta"),
                       "withinNoise": e.get("withinNoise")}
                      for e in ((panels.get(panel) or {}).get("entries") or [])[:5]]
    out["partners"] = [{"name": e.get("name"), "share": e.get("share")}
                       for e in ((panels.get("partners") or {}).get("entries") or [])[:5]]
    out["spreads"] = [{"sps": e.get("spread"), "share": e.get("share")}
                      for e in ((panels.get("spreads") or {}).get("entries") or [])[:3]]
    out["note"] = note + " Panel shares are within this Pokemon's real-team sets."
    return trace, out


SPEED_MODES = {"tailwind": "Tailwind", "trickroom": "Trick Room", "scarf": "Choice Scarf",
               "ability": "a speed ability (weather or terrain speed abilities, Unburden and similar)",
               "soft": "foe-side slowdown (Icy Wind, Thunder Wave, Electroweb, Sticky Web ...)",
               "none": "no speed control"}
STRUCTURE_SIGNALS = 10
STRUCTURE_CORES = 10


def _structure(fmt: str, projection: Path | None) -> tuple[tuple, Any]:
    trace = ("meta.structure", f"format structure ({fmt})", {"format": fmt})
    doc = _projection_json(projection, f"meta/landscape_{fmt}.json")
    if not doc or doc.get("rule") != _authority()["current"]["rule"]:
        return trace, {"error": "the format structure for the current rule is not installed"}
    out = {"format": fmt, "rule": doc["rule"], "seasons": doc.get("dataSeasons"), "teams": doc["sample"],
           "speedControlModes": {mode: {**row, "meaning": SPEED_MODES.get(mode, mode)}
                                 for mode, row in doc["speedControlModes"].items()},
           "structuralSignals": dict(list(doc["structuralSignals"].items())[:STRUCTURE_SIGNALS]),
           "megaSlots": doc["megaSlots"], "commonCores": doc["cores"][:STRUCTURE_CORES],
           "note": "Distributions over the observed real teams of this rule (tournament and ladder samples, not "
                   "the in-game usage): share = fraction of teams with at least one member providing it; a team "
                   "can use several speed-control modes. structuralSignals = team-level presence of role moves "
                   "(protect, spread, fake_out ...). megaSlots.buckets = how many Mega Stone holders teams "
                   "register (only one may Mega Evolve per battle). commonCores = Pokemon pairs most often on "
                   "one team, by count. Facts about how teams are built, not a strength ranking or a "
                   "recommended archetype."}
    return trace, out


META_VIEWS = ("who_uses", "formats", "trend", "movers", "real_teams", "structure")


def meta_explore(pool: Any, args: dict, timeout: float, projection: Path | None) -> tuple[tuple | None, str]:
    from . import qa
    view = args.get("view")
    fmt = args.get("format") or "single"
    if view not in META_VIEWS or fmt not in ("single", "double"):
        return None, json.dumps({"error": f"view is one of {', '.join(META_VIEWS)}; format single or double"})
    if view == "who_uses":
        trace, out = _who_uses(pool, args, fmt, timeout)
    elif view == "movers":
        trace, out = _movers(fmt)
    elif view == "real_teams":
        trace, out = _real_teams(pool, args, fmt, timeout, projection)
    elif view == "structure":
        trace, out = _structure(fmt, projection)
    else:
        name = qa._clean_name(args.get("pokemon"))
        if name is None:
            return None, json.dumps({"error": f"{view} needs pokemon"})
        trace, out = _formats(pool, name, timeout) if view == "formats" else _trend(pool, name, fmt, timeout, projection)
    return trace, qa.clip_json(out, cap=qa.QA_TOOL_RESULT_CHARS * 2)
