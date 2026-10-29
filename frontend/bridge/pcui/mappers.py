"""Raw skill output -> Web DTO mappers (the backend mapper, frontend/design.md §3).

Graduated from frontend/protocol/scripts/map_dto.py; the protocol package's golden DTO
fixtures are THIS module's regression suite (frontend/bridge/tests/test_mappers.py re-derives
every fixture and compares byte-identically).

Rules: DTOs are camelCase; `name` is ALWAYS the canonical English join key — meta panels
arrive zh/ja-named and resolve through the dex (the only naming authority); a panel name
the dex cannot resolve is a bug and raises rather than shipping a broken join key.
"""
from __future__ import annotations

import json
import re
import sys
import threading

from .paths import SKILLS_ROOT

_DEX_SCRIPTS = SKILLS_ROOT / "pokemon-champions-dex/scripts"
if str(_DEX_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_DEX_SCRIPTS))
import champdex as _dex  # noqa: E402

# The team skill's scripts hold the pure opponent-cache reader (oppcache.py) + check grader
# (checks.py). The KO/check page reuses THEM (canonical logic) rather than re-deriving grades —
# so the team scripts dir joins the path the same way the dex one does. Imported lazily (only the
# matchup endpoint needs it) so an unrelated skill layout change never breaks bridge startup.
_TEAM_SCRIPTS = SKILLS_ROOT / "pokemon-champions-team/scripts"


class MappingError(RuntimeError):
    pass


# THREAD-LOCAL connection cache: sqlite3 connections are thread-affine by default, and the
# server invokes mappers through asyncio.to_thread (a pool of changing threads) — one shared
# cached connection raises ProgrammingError on the second thread (external audit 2026-07-13).
# Read-only DB, bounded pool: one connection per pool thread is cheap and safe.
_tls = threading.local()


def _conn():
    conn = getattr(_tls, "conn", None)
    if conn is None:
        conn = _dex.conn()
        _tls.conn = conn
    return conn


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")


def variant_key(variant_id: str) -> str:
    """The skill already emits a lossless canonical id; keep it unchanged across every layer."""
    return variant_id


def opponent_key(value: str) -> str:
    return variant_key(value) if value.startswith("variant:") else slugify(value)


def type_name(t: str | None) -> str | None:
    """Canonical Title-case type/category (common.ts enums). The dex tables already ship
    Title-case, but the meta details store keeps them lowercase and the meta CLI only
    Title-cases on emit — so a mapper fed store rows directly (build_projection.py) would
    otherwise ship enum-violating lowercase. `.capitalize()` is idempotent for the single-
    word type and category vocabularies, so normalizing here is safe on every path."""
    return t.capitalize() if t else t


def named(en: str, zh: str | None, ja: str | None) -> dict:
    out: dict = {"name": en}
    if zh:
        out["nameZh"] = zh
    if ja:
        out["nameJa"] = ja
    return out


def resolve_en(kind: str, name: str) -> str:
    canon = _dex.resolve(_conn(), kind, name)
    if not canon:
        raise MappingError(f"dex cannot resolve {kind} name {name!r}")
    return canon


# --- dex ---------------------------------------------------------------------------------

def _species_ref(name: str) -> dict:
    """Trilingual dex reference + slug for a canonical species name (the opponent-cache matrix key).
    An unknown name stays English-only (never fails the grid)."""
    row = _conn().execute(
        "select display_name, display_name_ja from pokemon where canonical=?", (name,),
    ).fetchone()
    out = named(name, row["display_name"] if row else None,
                row["display_name_ja"] if row else None)
    out["slug"] = slugify(name)
    return out


def _ability_ref(name: str) -> dict:
    """Trilingual ability reference straight from the dex abilities table (all 201 carry
    zh+ja; an unknown name stays English-only rather than failing a whole card)."""
    row = _conn().execute(
        "select display_name, display_name_ja from abilities where canonical=?", (name,),
    ).fetchone()
    return named(name, row["display_name"] if row else None,
                 row["display_name_ja"] if row else None)


def map_pokemon(raw: dict) -> dict:
    slug = slugify(raw["name"])
    out = {
        "key": f"pokemon:{slug}",
        **named(raw["name"], raw.get("display_name"), raw.get("display_name_ja")),
        "slug": slug,
        "nationalDex": raw["national_dex_no"],
        "types": raw["types"],
        "stats": raw["stats"],
        "abilities": [_ability_ref(a) for a in raw["abilities"]],
        "isMega": bool(raw["is_mega"]),
    }
    if raw.get("base_species"):
        out["baseSpecies"] = raw["base_species"]
    if "required_item" in raw:
        out["requiredItem"] = raw["required_item"]
    if raw.get("mega_forms"):
        out["megaForms"] = [{"name": m["name"], "requiredItem": m.get("required_item")}
                            for m in raw["mega_forms"]]
    return out


def _effects(raw: dict) -> dict:
    """Trilingual effect texts (effects_seed.py). Present-only (omit, never null): an
    empty zh move effect is 52poke's "no secondary effect" convention."""
    out = {}
    for src, dst in (("effect_en", "effect"), ("effect_zh", "effectZh"),
                     ("effect_ja", "effectJa")):
        if raw.get(src):
            out[dst] = raw[src]
    return out


def map_move(raw: dict) -> dict:
    return {
        **named(raw["name"], raw.get("display_name"), raw.get("display_name_ja")),
        "type": type_name(raw["type"]),
        "category": type_name(raw["category"]),
        "power": raw.get("power"),
        "accuracy": raw.get("accuracy"),
        "priority": raw.get("priority", 0),
        "pp": raw.get("pp"),
        **_effects(raw),
    }


def map_item(raw: dict) -> dict:
    return {
        "key": f"item:{slugify(raw['name'])}",
        **named(raw["name"], raw.get("display_name"), raw.get("display_name_ja")),
        "requiredBy": raw.get("required_by", []),
        **_effects(raw),
    }


def map_ability(raw: dict) -> dict:
    return {
        **named(raw["name"], raw.get("display_name"), raw.get("display_name_ja")),
        **_effects(raw),
    }


def map_nature(raw: dict) -> dict:
    return {
        **named(raw["name"], raw.get("display_name"), raw.get("display_name_ja")),
        "upStat": raw.get("up_stat"),
        "downStat": raw.get("down_stat"),
    }


def map_resolve_entry(raw: dict) -> dict:
    out = {"query": raw["query"], "ok": raw["ok"], "kind": raw.get("kind", "pokemon"),
           "matchType": raw.get("match_type")}
    for src, dst in (("canonical", "canonical"), ("display_name", "displayName"),
                     ("display_name_ja", "displayNameJa"), ("score", "score"),
                     ("is_mega", "isMega"), ("base_species", "baseSpecies"),
                     ("required_item", "requiredItem")):
        if src in raw and raw[src] is not None:
            out[dst] = raw[src]
    # The dex reports candidates as {canonical, distance, score}; the DTO carries the names only,
    # in the dex's order (an ambiguous qualifier refusal puts the qualified form first).
    if raw.get("suggestions"):
        out["suggestions"] = [s.get("canonical") if isinstance(s, dict) else s
                              for s in raw["suggestions"]]
    return out


# --- meta --------------------------------------------------------------------------------

def map_ranking(raw: dict) -> dict:
    def row(r: dict) -> dict:
        dex = _conn().execute(
            "select types_json from pokemon where canonical=?", (r["name"],),
        ).fetchone()
        if dex is None:
            raise MappingError(f"dex cannot resolve ranking Pokemon {r['name']!r}")
        return {
            "rank": r["rank"], "slug": r["slug"],
            # `slug` is the UPSTREAM meta id: it routes and keys the detail cache, and it drifts
            # with the source (lycanroc-midday -> lycanroc, meowstic-f -> meowstic-female). Ship
            # the asset-pack key derived from the canonical English name instead — the same single
            # slug authority item panels use — so the sprite join never rides on that spelling.
            "key": f"pokemon:{slugify(r['name'])}",
            # Ranking cards need the types, but loading all 189 full dex cards to recover them adds
            # an otherwise unrelated first-screen request. Keep the authoritative dex join here.
            "types": json.loads(dex["types_json"] or "[]"),
            **named(r["name"], r.get("name_zh"), r.get("name_ja")),
        }

    out = {
        "season": raw["season"], "rule": raw["rule"], "format": raw["format"],
        "rows": [row(r) for r in raw["rows"]],
    }
    if raw.get("updated_at"):                 # optional field: omit when absent, never null
        out["updatedAt"] = raw["updated_at"]
    return out


def _panel_entry(kind: str, e: dict) -> dict:
    en = resolve_en(kind, e.get("name_ja") or e["name"])
    row = {"rank": e["rank"], **named(en, e.get("name"), e.get("name_ja")),
           "percentage": e.get("percentage")}
    if kind == "item":
        # Ship the asset-pack key from the mapper (the single slug authority) so the UI never
        # re-derives it with a second, drift-prone slugify.
        row["key"] = f"item:{slugify(en)}"
    return row


def map_detail(raw: dict) -> dict:
    p = raw["panels"]
    moves = []
    for e in p["moves"]:
        row = _panel_entry("move", e)
        row.update({"type": type_name(e["type"]), "category": type_name(e["category"]),
                    "power": e.get("power")})
        moves.append(row)
    partners = []
    for e in p["partners"]:
        # A teammate row is species-level (its identity is the national dex number). `form_rep` is
        # the dex's representative form for a species it ships only as split forms — the handle the
        # sprite and the link need. The printed name stays whatever the panel carries, so the row
        # says "Lycanroc" while pointing at the form the dex means by that word.
        en = e.get("form_rep") or resolve_en("pokemon", e.get("name_ja") or e["name"])
        row = {"rank": e["rank"], **named(en, e.get("name"), e.get("name_ja")),
               "slug": slugify(en)}
        if str(e.get("key", "")).isdigit():
            row["nationalDex"] = int(e["key"])
        partners.append(row)
    spreads = [{"rank": e["rank"],
                "spread": {k: v for k, v in e.items()
                           if k in ("hp", "atk", "def", "spa", "spd", "spe") and v},
                "percentage": e.get("percentage")} for e in p["spreads"]]
    return {
        "rank": raw.get("rank"),
        "slug": raw["slug"],
        "key": f"pokemon:{slugify(raw['pokemon_en'])}",
        **named(raw["pokemon_en"], raw.get("pokemon"), raw.get("pokemon_ja")),
        "format": raw["format"], "season": raw["season"], "rule": raw["rule"],
        "panels": {
            "moves": moves,
            "items": [_panel_entry("item", e) for e in p["items"]],
            "abilities": [_panel_entry("ability", e) for e in p["abilities"]],
            "natures": [_panel_entry("nature", e) for e in p["natures"]],
            "partners": partners,
            "spreads": spreads,
        },
    }


# The shipped skill data keeps 30 per direction; the page shows 10. The projection is a cropped
# public DTO, not a mirror of the skill store (frontend/design.md §7.1), so the cut happens HERE —
# shipping 30 to render 10 would triple this file family for nothing. Raising the page depth means
# raising this constant, in one place, on both the bridge and the projection path.
KO_PANEL_LIMIT = 10


def _ko_entry(e: dict, usage_rank: dict[str, int]) -> dict:
    """One KO opponent row. The source gives a national dex number and no form, so the identity IS
    that number and `name` is species-level. NO percentage field is emitted — the source publishes
    an ordering, and a null pct reads as a measured zero.

    `slug`/`key` name ONE form, because a sprite and a link have to: the species itself when the dex
    names it exactly, otherwise `form_rep`, the form the dex points that bare species name at. The
    usage rank follows the same handle rather than being looked up separately, so the row's sprite,
    its rank and where it leads are three statements about the same pokemon."""
    en = e.get("name_en") or ""
    form = e.get("form_rep") or en
    row: dict = {
        "rank": e["rank"],
        **named(en, e.get("name"), e.get("name_ja")),
        "nationalDex": int(e["key"]),
        "usageRank": usage_rank.get(en, usage_rank.get(form)),
    }
    if form:
        row["slug"] = slugify(form)
        row["key"] = f"pokemon:{slugify(form)}"
    if e.get("form_collapsed"):
        row["formCollapsed"] = True
    return row


def _ko_move_panel(entries: list | None) -> list | None:
    """`None` stays `None`: the reserved tier was NOT collected, which is a different fact from
    the source reporting no KO moves. Only a real list is mapped."""
    if entries is None:
        return None
    out = []
    for e in entries[:KO_PANEL_LIMIT]:
        row = _panel_entry("move", e)
        row.update({"type": type_name(e.get("type")), "category": type_name(e.get("category")),
                    "power": e.get("power")})
        out.append(row)
    return out


def map_ko(doc: dict, row: dict, usage_rank: dict[str, int]) -> dict:
    """One shipped KO row -> MetaKoDto. `doc` is the file (season/rule/format/coverage/updated_at),
    `row` the pokemon's entry, `usage_rank` an English-canonical -> rank map for the SAME format."""
    cov = doc.get("coverage") or {}
    p = row.get("panels") or {}
    en = row["pokemon_en"]
    out = {
        "rank": row.get("rank"),
        "slug": row.get("slug") or slugify(en),
        "key": f"pokemon:{slugify(en)}",
        **named(en, row.get("pokemon"), row.get("pokemon_ja")),
        "format": doc["format"], "season": doc["season"], "rule": doc["rule"],
        "updatedAt": doc["updated_at"],
        "coverage": {
            "opponents": cov.get("opponents", "ranked"),
            "opponentIdentity": cov.get("opponent_identity", "national_dex"),
            "opponentDepth": cov.get("opponent_depth", 0),
            "moveShare": cov.get("move_share", "absent"),
            "moveDepth": cov.get("move_depth"),
            "rows": cov.get("rows", 0),
            "rowsWithMoveShare": cov.get("rows_with_move_share", 0),
        },
        "panels": {
            "koTargets": [_ko_entry(e, usage_rank)
                          for e in (p.get("ko_targets") or [])[:KO_PANEL_LIMIT]],
            "koedBy": [_ko_entry(e, usage_rank)
                       for e in (p.get("koed_by") or [])[:KO_PANEL_LIMIT]],
            "koMoves": _ko_move_panel(p.get("ko_moves")),
            "koedByMoves": _ko_move_panel(p.get("koed_by_moves")),
        },
    }
    if cov.get("opponent_form_collapsed") is not None:
        out["coverage"]["opponentFormCollapsed"] = cov["opponent_form_collapsed"]
    # A merged move tier carries its own capture time and cross-capture agreement; both are absent
    # while the tier is uncollected, so they are mapped only when the store actually has them.
    if cov.get("move_share_captured_at"):
        out["coverage"]["moveShareCapturedAt"] = cov["move_share_captured_at"]
    if cov.get("move_share_agreement"):
        out["coverage"]["moveShareAgreement"] = cov["move_share_agreement"]
    if cov.get("reused_from"):
        donor = cov["reused_from"]
        out["coverage"]["reusedFrom"] = {
            "season": donor["season"], "rule": donor["rule"], "format": donor["format"],
            "updatedAt": donor["updated_at"],
        }
        if donor.get("snapshot_id") is not None:
            out["coverage"]["reusedFrom"]["snapshotId"] = int(donor["snapshot_id"])
    if row.get("panel_reused_from"):
        panel_names = {"ko_targets": "koTargets", "koed_by": "koedBy",
                       "ko_moves": "koMoves", "koed_by_moves": "koedByMoves"}
        out["coverage"]["panelReusedFrom"] = {
            panel_names[panel]: {"season": donor["season"], "rule": donor["rule"],
                                 "format": donor["format"], "updatedAt": donor["updated_at"],
                                 "snapshotId": int(donor["snapshot_id"])}
            for panel, donor in row["panel_reused_from"].items()}
    snapshot = (doc.get("snapshot") or {}).get("id")
    if snapshot is not None:
        out["snapshotId"] = int(snapshot)
    return out


# Facet -> where its values live. `partners` and the two KO panels are pokemon facets (their values
# are English species canonicals); the rest are entity facets read off the usage detail panels.
FACET_PANELS = (("items", "item"), ("moves", "move"), ("abilities", "ability"),
                ("natures", "nature"))


def build_facets(details: list[dict], ko_rows: list[dict]) -> dict:
    """Inverted index: facet -> English canonical value -> the slugs carrying it.

    Built ONCE per format because the per-pokemon detail files are lazy: without this a filter rail
    would have to fetch all 262 of them to answer "which mons run Choice Scarf". Values are English
    canonicals, the cross-skill join key; the UI localizes them through the dex at render time."""
    out: dict[str, dict[str, list[str]]] = {k: {} for k, _ in FACET_PANELS}
    out["partners"] = {}
    out["koTargets"] = {}
    out["koedBy"] = {}

    def add(facet: str, value: str, slug: str) -> None:
        if not value or not slug:
            return
        bucket = out[facet].setdefault(value, [])
        if slug not in bucket:
            bucket.append(slug)

    for row in details:
        slug = row.get("slug") or ""
        panels = row.get("panels") or {}
        for facet, kind in FACET_PANELS:
            for e in panels.get(facet) or []:
                try:
                    add(facet, resolve_en(kind, e.get("name_ja") or e["name"]), slug)
                except MappingError:
                    continue                    # a value the dex snapshot cannot resolve is skipped
        for e in panels.get("partners") or []:
            try:
                add("partners", resolve_en("pokemon", e.get("name_ja") or e["name"]), slug)
            except MappingError:
                continue
    ko_by_slug = {r.get("slug") or slugify(r["pokemon_en"]): r for r in ko_rows}
    for slug, row in ko_by_slug.items():
        panels = row.get("panels") or {}
        for facet, key in (("koTargets", "ko_targets"), ("koedBy", "koed_by")):
            for e in panels.get(key) or []:
                add(facet, e.get("name_en") or "", slug)
    return out


def map_trend(raw: dict) -> dict:
    periods = [p["date"] for p in raw["periods"]]
    series = []
    for s in raw["series"]:
        slug = s["slug"]
        names = raw.get("names", {}).get(slug, {})
        series.append({
            "slug": slug,
            **named(names.get("en") or slug, names.get("zh"), names.get("ja")),
            "ranks": raw["ranks"].get(slug, []),
        })
    return {"season": raw["season"], "rule": raw["rule"], "format": raw["format"],
            "periods": periods, "series": series}


def _species_rank_series(store_panel: dict) -> list[dict]:
    """Species-level history (teammates, KO opponents) keyed by national dex number.

    A key that is not a bare number comes from a retired source convention (a form-indexed teammate
    id, say). It is dropped rather than guessed at: it cannot be placed in the dex, and it cannot
    join the current panel either, whose rows are keyed by the number alone."""
    out = []
    for key, values in store_panel.items():
        if not str(key).isdigit():
            continue
        out.append({"nationalDex": int(key),
                    "ranks": [None if v is None else int(v) for v in values]})
    return out


def map_ko_trend(raw: dict, slug: str) -> dict:
    """Map one Pokemon out of the KO trend store (its own clock, hence its own document).

    Opponent panels stay RANKS keyed by national dex number — the identity those rows actually have.
    Move panels are percentages keyed by the source's Japanese move name, resolved to the English
    canonical here exactly as the usage trend does."""
    pokemon = raw.get("pokemon", {}).get(slug)
    if pokemon is None:
        raise MappingError(f"ko trend not found: {slug}")
    panels = {
        "koTargets": _species_rank_series(pokemon.get("ko_targets", {})),
        "koedBy": _species_rank_series(pokemon.get("koed_by", {})),
    }
    for dto_key, store_key in (("koMoves", "ko_moves"), ("koedByMoves", "koed_by_moves")):
        panels[dto_key] = [
            {"name": resolve_en("move", source_name), "values": values}
            for source_name, values in pokemon.get(store_key, {}).items()
        ]
    try:
        return {
            "season": raw["season"], "rule": raw["rule"], "format": raw["format"],
            "slug": slug, "periods": [p["date"] for p in raw["periods"]],
            "panels": panels,
        }
    except (KeyError, TypeError) as exc:
        raise MappingError(f"malformed ko trend store metadata: {exc}") from exc


def map_usage_trend(raw: dict, slug: str) -> dict:
    """Map one Pokemon out of the compact historical usage store.

    The store keeps Japanese source keys so snapshots remain stable across localization
    updates. The Web DTO uses canonical English keys to join the current detail rows.
    """
    pokemon = raw.get("pokemon", {}).get(slug)
    if pokemon is None:
        raise MappingError(f"usage trend not found: {slug}")

    panels: dict[str, list[dict]] = {}
    for panel, kind in (("moves", "move"), ("items", "item"),
                        ("abilities", "ability"), ("natures", "nature")):
        panels[panel] = [
            {"name": resolve_en(kind, source_name), "values": values}
            for source_name, values in pokemon.get(panel, {}).items()
        ]

    panels["partners"] = _species_rank_series(pokemon.get("partners", {}))

    stat_keys = ("hp", "atk", "def", "spa", "spd", "spe")
    spreads = []
    for source_key, values in pokemon.get("spreads", {}).items():
        parts = source_key.split("/")
        if len(parts) != len(stat_keys):
            raise MappingError(f"invalid usage spread key: {source_key}")
        try:
            spread = {key: int(value) for key, value in zip(stat_keys, parts) if int(value)}
        except (TypeError, ValueError) as exc:
            raise MappingError(f"invalid usage spread key: {source_key}") from exc
        spreads.append({"spread": spread, "values": values})
    panels["spreads"] = spreads

    # A partial/older store may be missing the top-level metadata or a period's date. Surface it
    # as a MappingError (the endpoint's handled failure) instead of an opaque KeyError/TypeError 500.
    try:
        return {
            "season": raw["season"], "rule": raw["rule"], "format": raw["format"],
            "slug": slug, "periods": [p["date"] for p in raw["periods"]],
            "panels": panels,
        }
    except (KeyError, TypeError) as exc:
        raise MappingError(f"malformed usage trend store metadata: {exc}") from exc


# --- calc --------------------------------------------------------------------------------

def map_damage(raw: dict) -> dict:
    out = {
        "description": raw["description"],
        "damage": raw["damage"],
        "min": raw["min"], "max": raw["max"],
        "minPercent": raw["minPercent"], "maxPercent": raw["maxPercent"],
        "defenderHP": raw["defenderHP"],
        "hits": raw["hits"], "hitsRange": raw["hits_range"],
        "koChance": None, "category": raw["category"],
        "move": raw["move"], "attacker": raw["attacker"], "defender": raw["defender"],
    }
    # Optional-not-nullable in the DTO, and absent from results produced by an older engine
    # snapshot — mirror the browser twin (calc-engine/index.ts mapDamage) exactly.
    if raw.get("is_spread") is not None:
        out["isSpread"] = raw["is_spread"]
    for src, dst in (("min_env", "minEnv"), ("max_env", "maxEnv"),
                     ("min_env_percent", "minEnvPercent"), ("max_env_percent", "maxEnvPercent")):
        if raw.get(src) is not None:
            out[dst] = raw[src]
    if raw.get("ko_chance"):
        k = raw["ko_chance"]
        # `n` can be None (parseKoChance yields n=None for a non-standard KO string); KoChanceDto.n is
        # optional-NOT-nullable, so a None sinks the SPA's Zod parse of the whole (batched) result — skip
        # it when absent OR None, exactly as the browser twin (calc-engine/index.ts mapDamage) does.
        out["koChance"] = {kk: k[sk] for sk, kk in
                           (("text", "text"), ("n", "n"), ("guaranteed", "guaranteed"),
                            ("chance_pct", "chancePct"))
                           if sk in k and not (sk == "n" and k[sk] is None)}
    if raw.get("ko_caveats"):
        out["koCaveats"] = [{"code": c["code"], "direction": c["direction"],
                             **({"cause": c["cause"]} if c.get("cause") else {})}
                            for c in raw["ko_caveats"]]
    return out


def map_speedline(raw: dict) -> dict:
    return {k: raw[k] for k in ("name", "types", "baseSpeed", "nature", "speedSPs",
                                "speedIV", "speedBoost", "rawSpeed", "boostedSpeed",
                                "finalSpeed")}


# --- learnset (straight from the dex DB — static data, no CLI hop needed) -----------------

def learners_index() -> dict:
    """English move canonical -> the slugs that learn it, for the dex browse filter.

    Built in ONE query rather than per Pokemon: the shipped learnsets are lazy per slug, so a
    "who learns this" filter assembled client-side would cost one request per roster entry. Slugs
    come from the same `slugify` authority `map_pokemon` uses, so the index joins straight onto the
    browse cards without a second spelling."""
    out: dict[str, list[str]] = {}
    for row in _conn().execute("select move, pokemon from learnsets order by move, pokemon"):
        out.setdefault(str(row["move"]), []).append(slugify(str(row["pokemon"])))
    return {"moves": out}


def learnset_dto(canonical: str) -> dict:
    """LearnsetDto for one pokemon: learnable moves joined with the move catalog, trilingual,
    sorted by canonical name (the UI re-sorts/filters as it likes)."""
    rows = _conn().execute(
        "select m.canonical, m.display_name, m.display_name_ja, m.type, m.category, m.power, "
        "m.accuracy from learnsets l join moves m on m.canonical = l.move "
        "where l.pokemon = ? order by m.canonical", (canonical,),
    ).fetchall()
    moves = []
    for r in rows:
        entry = named(r["canonical"], r["display_name"], r["display_name_ja"])
        try:
            power: int | None = int(r["power"])
        except (TypeError, ValueError):
            power = None
        try:
            accuracy: float | None = float(r["accuracy"])
        except (TypeError, ValueError):
            accuracy = None
        entry.update({"type": type_name(r["type"]), "category": type_name(r["category"]),
                      "power": power, "accuracy": accuracy})
        moves.append(entry)
    return {"slug": slugify(canonical), "moves": moves}


# --- opponent matchup cache (M5) — KO matrix + derived check view ------------------------
# The team skill owns the fact-shaping: oppcache.py reads the shipped cache and derives the
# C2/C1/C0 check grid (checks.py). The bridge reuses those pure functions (never re-implements a
# grade) and only maps their output to camelCase DTOs — the same "map, never recompute" discipline
# as map_detail. The team scripts dir is added to sys.path lazily on the first matchup request.

_SP_NCP_TO_SMOGON = {"hp": "hp", "at": "atk", "df": "def", "sa": "spa", "sd": "spd", "sp": "spe"}


def _oppcache_mod():
    if str(_TEAM_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_TEAM_SCRIPTS))
    import oppcache  # noqa: E402
    return oppcache


def load_oppcache(fmt: str, rule: str) -> dict | None:
    """The shipped opponent cache for (rule, format), or None when it has not been built."""
    return _oppcache_mod().load_cache(fmt, rule)


def derive_oppcheck_grid(cache: dict) -> dict:
    """The lean variant x variant grade grid the UI renders (see oppcache.derive_check_grid)."""
    return _oppcache_mod().derive_check_grid(cache)


def _team_evidence_window(cache: dict) -> dict:
    built = cache.get("built_for") or {}
    expiry = built.get("team_evidence_expires_at")
    if built.get("handover_receipt") and not expiry:
        raise ValueError("team handover cache is missing its evidence expiry")
    return {"teamEvidenceExpiresAt": expiry} if expiry else {}


def map_oppcheck_grid(cache: dict, derived: dict) -> dict:
    """Encode the grid's keys; everything else is already minimal.

    Damage/speed are deliberately NOT here.  The checks page can render the representative grid
    from this compact document and lazy-load the much larger KO matrix only when a cell is opened.
    """
    grid = {}
    for ai, row in (derived.get("grid") or {}).items():
        grid[opponent_key(ai)] = {
            opponent_key(dj): {
                "grade": c.get("grade"),
                **({"c0Kind": c["c0_kind"]} if c.get("c0_kind") else {}),
                **({"contested": True} if c.get("contested") else {}),
            } for dj, c in row.items()}
    return {
        **_team_evidence_window(cache),
        "format": derived.get("format") or (cache.get("built_for") or {}).get("format") or "single",
        "confidence": derived.get("confidence") or "",
        "confidenceReason": derived.get("confidence_reason") or "",
        "species": _species_columns(cache.get("species") or [], cache.get("sets")),
        "grid": grid,
    }


def _map_offense(off: dict | None) -> dict | None:
    if not off:
        return None
    out = {
        "move": off["move"],
        "minPercent": off["min_percent"], "maxPercent": off["max_percent"],
        "koPossible": off.get("ko_possible"), "koGuaranteed": off.get("ko_guaranteed"),
        "ko": off.get("ko") or "", "koExact": bool(off.get("ko_exact")),
        "category": type_name(off.get("category")), "koChance": None,
    }
    kc = off.get("ko_chance")
    if kc:
        out["koChance"] = {dst: kc[src] for src, dst in
                           (("text", "text"), ("n", "n"), ("guaranteed", "guaranteed"),
                            ("chance_pct", "chancePct")) if src in kc}
    cav = off.get("ko_caveats")
    if cav:
        out["koCaveats"] = [{"code": c["code"], "direction": c["direction"],
                             **({"cause": c["cause"]} if c.get("cause") else {})} for c in cav]
    da = off.get("disguise_adjusted")
    if da:
        out["disguiseAdjusted"] = {"effectiveKoPossible": da.get("effective_ko_possible"),
                                   "effectiveKoGuaranteed": da.get("effective_ko_guaranteed"),
                                   "note": da.get("note") or ""}
    return out


def map_oppko_grid(cache: dict) -> dict:
    """Map only the facts the KO overview paints.

    The full cache is intentionally separate: speed, moves, caveats and processed sets are useful
    only after a user opens a cell, while eagerly shipping them made the default KO page parse a
    7–14 MB document before it could render.
    """
    bf = cache.get("built_for") or {}
    grid: dict = {}
    for ai, cells in (cache.get("matrix") or {}).items():
        row: dict = {}
        for dj, cell in cells.items():
            offense = (cell or {}).get("offense")
            chance = (offense or {}).get("ko_chance") or {}
            row[opponent_key(dj)] = (None if offense is None else [
                offense.get("min_percent"), offense.get("max_percent"),
                offense.get("ko_possible"), offense.get("ko_guaranteed"),
                chance.get("n"), chance.get("guaranteed"), chance.get("chance_pct"),
            ])
        grid[opponent_key(ai)] = row
    return {
        **_team_evidence_window(cache),
        "format": bf.get("format") or "single",
        "confidence": cache.get("confidence") or "",
        "confidenceReason": cache.get("confidence_reason") or "",
        "species": _species_columns(cache.get("species") or [], cache.get("sets")),
        "grid": grid,
    }


def _map_opp_speed(sp: dict | None) -> dict:
    sp = sp or {}
    return {"attacker": sp.get("attacker"), "defender": sp.get("defender"),
            "faster": sp.get("faster")}


def _map_sps(sps: dict | None) -> dict | None:
    if not sps:
        return None
    out: dict = {}
    for k, v in sps.items():
        kk = _SP_NCP_TO_SMOGON.get(k, k)
        if kk in ("hp", "atk", "def", "spa", "spd", "spe") and isinstance(v, int):
            out[kk] = v
    return out or None


def _map_opp_set(s: dict | None) -> dict:
    s = s or {}
    out = {"species": s.get("species") or "", "runForm": s.get("run_form"),
           "ability": s.get("ability"), "item": s.get("item"), "nature": s.get("nature"),
           "moves": s.get("moves"), "sps": _map_sps(s.get("sps")),
           "source": s.get("source"), "confidence": s.get("confidence"),
           "realTeamBacked": bool(s.get("real_team_backed")), "note": s.get("note")}
    if s.get("variant_id"):
        out["isModal"] = bool(s.get("is_modal"))
        out["coverage"] = s.get("coverage")
        out["sampleCount"] = s.get("variant_sample")
        out["representativeCount"] = s.get("variant_count")
        out["clusterBasis"] = s.get("cluster_basis")
        out["representedCoverage"] = s.get("represented_coverage")
        out["unrepresentedCoverage"] = s.get("unrepresented_coverage")
        profile = s.get("speed_profile")
        if isinstance(profile, dict):
            out["speedProfile"] = {
                "sample": profile.get("sample"),
                "minSpeSp": profile.get("min_spe_sp"),
                "maxSpeSp": profile.get("max_spe_sp"),
                "natures": profile.get("natures") or [],
                "heterogeneous": bool(profile.get("heterogeneous")),
            }
        if s.get("base_ability"):     # pre-Mega ability — fires on switch-in, before Mega evolution
            out["baseAbility"] = s["base_ability"]
    return out


def _species_row(r: dict) -> dict:
    name = r.get("species") or ""
    row = {"rank": r.get("rank"), **_species_ref(name),
           "realTeamBacked": bool(r.get("real_team_backed")),
           "setSource": r.get("set_source"), "setConfidence": r.get("set_confidence")}
    if r.get("run_form"):
        row["runForm"] = r["run_form"]
    return row


def map_oppdetails(dto: dict, slug: str) -> dict | None:
    """Slice a mapped cache by species, preserving every build and both damage directions.

    This is the common boundary for the local endpoint and static projection. A defender-only
    build has no outgoing row but still retains its incoming cells; variant ids are never parsed.
    """
    species = next((row for row in dto["species"] if row["slug"] == slug), None)
    if species is None:
        return None
    keys = {variant["key"] for variant in species.get("variants", [])} or {slug}
    matrix = {}
    for attacker, row in dto["matrix"].items():
        cells = row if attacker in keys else {key: cell for key, cell in row.items() if key in keys}
        if cells:
            matrix[attacker] = cells
    return {**({"teamEvidenceExpiresAt": dto["teamEvidenceExpiresAt"]}
               if "teamEvidenceExpiresAt" in dto else {}),
            "format": dto["format"], "matrix": matrix}


def map_oppcache(cache: dict) -> dict:
    """Map the cache to its wire DTO.

    The grid is keyed by VARIANT (one row/column per real build), so the wire key losslessly encodes
    `variant_id`, not of the species — several rows share a species and a species-keyed map would
    collapse them onto each other. `species` groups those variants back up for the UI: each entry
    lists its builds with `isModal` (what to show by default) and `coverage` (how common it is), so
    the default view is one column per Pokémon and switching a build is a pure re-index of data the
    client already holds."""
    bf = cache.get("built_for") or {}
    species_rows = cache.get("species") or []
    sets_in = cache.get("sets") or {}

    def _key(row_or_id: Any) -> str:
        """Wire key for a matrix row: encoded variant id when expanded, species slug otherwise."""
        value = str(row_or_id)
        return opponent_key(value)

    out_sets = {_key(k): _map_opp_set(s) for k, s in sets_in.items()}
    out_matrix: dict = {}
    for ai, cells in (cache.get("matrix") or {}).items():
        out_matrix[_key(ai)] = {
            _key(dj): {"offense": _map_offense((c or {}).get("offense")),
                       "speed": _map_opp_speed((c or {}).get("speed"))}
            for dj, c in cells.items()}

    # Group variant rows under their species, preserving the cache's stable order.
    grouped: dict[str, dict] = {}
    for r in species_rows:
        name = r.get("species") or ""
        vid = r.get("variant_id")
        entry = grouped.get(name)
        if entry is None:
            entry = grouped[name] = {**_species_row(r), "variants": []}
        # The species row inherits the MODAL build's provenance, so a collapsed view reads the
        # default build rather than whichever variant happened to sort first.
        if vid and r.get("is_modal"):
            entry.update({k: v for k, v in _species_row(r).items() if k != "variants"})
        if vid:
            s = sets_in.get(vid) or {}
            entry["variants"].append({
                "key": _key(vid), "item": s.get("item"), "ability": s.get("ability"),
                "isModal": bool(r.get("is_modal")), "coverage": r.get("coverage"),
                **({"runForm": s["run_form"]} if s.get("run_form") else {}),
                **({"baseAbility": s["base_ability"]} if s.get("base_ability") else {}),
            })
        else:                       # legacy species-keyed cache: one implicit variant
            entry["variants"].append({"key": _key(name), "item": None, "ability": None,
                                      "isModal": True, "coverage": None})
    # Modal first, then by coverage: the UI labels builds A/B/C BY POSITION, so an unordered list
    # would make "A" mean nothing (Garchomp's 67.8% build sorted behind its 8.9% one).
    for entry in grouped.values():
        vs = entry.get("variants")
        if vs:
            vs.sort(key=lambda v: (not v.get("isModal"), -(v.get("coverage") or 0)))
    return {
        **_team_evidence_window(cache),
        "season": bf.get("season") or "", "rule": bf.get("rule") or "",
        "format": bf.get("format") or "single",
        "builtAt": bf.get("built_at") or "", "topK": bf.get("top_k") or len(grouped),
        "variantCount": bf.get("variant_count") or len(species_rows),
        "confidence": cache.get("confidence") or "",
        "confidenceReason": cache.get("confidence_reason") or "",
        "species": list(grouped.values()),
        "sets": out_sets, "matrix": out_matrix,
    }


def _map_check(chk: dict | None) -> dict | None:
    if not chk:
        return None
    verdict = (chk.get("resolvability") or {}).get("verdict")
    details = []
    for detail in chk.get("caveat_details") or []:
        if not isinstance(detail, dict) or not isinstance(detail.get("code"), str):
            continue
        params = detail.get("params") if isinstance(detail.get("params"), dict) else {}
        details.append({"code": detail["code"],
                        "params": {str(k): v for k, v in params.items()
                                   if v is None or isinstance(v, (str, int, float, bool))}})
    return {
        "grade": chk.get("grade"),
        "c1Mode": chk.get("c1_mode"),
        "c0Kind": chk.get("c0_kind"),
        "resolvability": verdict if verdict in ("clean", "contested") else "clean",
        "caveats": chk.get("caveats") or [],
        "caveatDetails": details,
    }


def _bounded_share(value):
    """Keep source-rounded probability shares inside the public DTO's [0, 1] domain."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, value))
    return value


def _species_columns(species_rows: list[dict], sets: dict | None = None) -> list[dict]:
    """One entry per species (preferring its modal row) WITH its build list, so a grid keyed by
    variant is still presented as one row/column per Pokemon and switched build-by-build.

    Builds are ordered modal-first (coverage desc), which is what makes a positional label stable:
    the UI names them A/B/C by index rather than by item, because an item name is long, differs per
    language, and says nothing about how common the build is.
    """
    sets = sets or {}
    out: dict[str, dict] = {}
    variants: dict[str, list[dict]] = {}
    for r in species_rows:
        name = r.get("species") or ""
        if name not in out or r.get("is_modal"):
            out[name] = _species_row(r)
        vid = r.get("variant_id")
        if vid:
            st = sets.get(vid) or {}
            variants.setdefault(name, []).append({
                "key": variant_key(vid), "isModal": bool(r.get("is_modal")),
                "coverage": r.get("coverage"),
                "item": st.get("item"), "ability": st.get("ability"),
                **({"runForm": st["run_form"]} if st.get("run_form") else {}),
                **({"baseAbility": st["base_ability"]} if st.get("base_ability") else {}),
            })
    for name, row in out.items():
        vs = variants.get(name)
        if vs:
            vs.sort(key=lambda v: (not v["isModal"], -(v.get("coverage") or 0)))
            row["variants"] = vs
    return list(out.values())


def map_oppsets(cache: dict) -> dict:
    """Map only the retained real-build catalog, without the damage/check matrices.

    The calculator needs concrete joint configurations as defaults, and matchup headers need them
    for hover detail. Mapping the full matrix for those two reads costs megabytes and substantial
    CPU for data neither surface consumes.
    """
    bf = cache.get("built_for") or {}
    sets_in = cache.get("sets") or {}
    return {
        **_team_evidence_window(cache),
        "format": bf.get("format") or "single",
        "species": _species_columns(cache.get("species") or [], sets_in),
        "sets": {opponent_key(str(key)): _map_opp_set(value)
                 for key, value in sets_in.items()},
    }


# --- team diagnose report (online deterministic surface, design §7.5) ---------------------

def _str_list(v) -> list:
    """Errors/warnings arrive as localized-at-the-boundary strings; anything structured is
    serialized rather than dropped (facts must not vanish in the mapper)."""
    import json as _json
    return [x if isinstance(x, str) else _json.dumps(x, ensure_ascii=False)
            for x in (v or [])]


def _incomplete_members(rows) -> list:
    """The members whose moveset isn't authoritative (offense/roles skip them, so a reported
    gap may be a phantom). Each carries its effective completeness tier for the UI caveat."""
    return [{"species": r.get("species") or "", "completeness": str(r.get("completeness") or "")}
            for r in (rows or []) if isinstance(r, dict) and r.get("species")]


def map_diagnose_report(team: dict, validate_raw: dict, diagnose_raw: dict) -> dict:
    """validate + diagnose(all) -> DiagnoseReportDto: the disclosure-safe per-aspect facts
    the diagnose page renders. Species names stay English canonical (the SPA localizes)."""
    defense = diagnose_raw.get("defense") or {}
    bat = defense.get("by_attack_type") or {}
    by_attack = [{"type": t,
                  "weak": list(e.get("weak") or []),
                  "resist": list(e.get("resist") or []),
                  "immune": list(e.get("immune") or []),
                  "neutralCount": int(e.get("neutral_count") or 0)}
                 for t, e in (bat.items() if isinstance(bat, dict) else [])
                 if isinstance(e, dict)]
    offense = diagnose_raw.get("offense") or {}
    attack_types = offense.get("attack_types") or {}
    speed = diagnose_raw.get("speed") or {}

    def order(rows) -> list:
        return [{"species": r.get("species") or "", "speed": r.get("speed")}
                for r in (rows or []) if isinstance(r, dict) and r.get("species")]

    members = [{"species": m.get("species") or "",
                "baseSpeed": m.get("base_speed"), "speed": m.get("speed"),
                "speSp": m.get("spe_sp"), "nature": m.get("nature"),
                "assumedNeutral": m.get("assumed_neutral")}
               for m in (speed.get("members") or [])
               if isinstance(m, dict) and m.get("species")]
    roles = diagnose_raw.get("roles") or {}
    coverage = [{"key": k, "label": str(e.get("label") or k),
                 "present": bool(e.get("present")),
                 # The skill keeps required|optional for CLI compatibility. Do not expose those
                 # prescriptive words to UI/LLM consumers: these are attention levels, not proof
                 # that every team requires a role. NOT_CHECKED roles are simply omitted.
                 "expectation": ("situational" if e.get("expectation") == "optional"
                                 else "priority"),
                 **({"expectationReason": str(e["expectation_reason"])}
                    if e.get("expectation_reason") else {}),
                 "bearers": [{"species": b.get("species") or "", "via": b.get("via")}
                             for b in (e.get("bearers") or []) if isinstance(b, dict)]}
                for k, e in (roles.get("coverage") or {}).items() if isinstance(e, dict)]
    # A regulation that is too young to re-measure carry rates borrows its predecessor's study for
    # the handover window. The reason strings already name that rule; this says it is borrowed and
    # when it lapses, so a consumer is not left reading a stale attention level as a current one.
    calibration_raw = roles.get("attention_calibration")
    calibration = None
    if isinstance(calibration_raw, dict) and calibration_raw.get("status"):
        calibration = {"rule": str(calibration_raw.get("rule") or ""),
                       "status": str(calibration_raw["status"])}
        if calibration_raw.get("measured_on"):
            calibration["measuredOn"] = str(calibration_raw["measured_on"])
        if calibration_raw.get("expires_at"):
            calibration["expiresAt"] = str(calibration_raw["expires_at"])
    role_members = [{"species": m.get("species") or "",
                     "signals": [str(s) for s in (m.get("signals") or [])]}
                    for m in (roles.get("compression") or [])
                    if isinstance(m, dict) and m.get("species")]
    cc_raw = diagnose_raw.get("check_coverage") or {}
    # Per-opponent member cells (the same facts the matchup grid exposes), keyed by the
    # opponent species so each roll-up row can open a clickable detail.
    cells_by_opp: dict = {}
    for mrow in cc_raw.get("members") or []:
        if not isinstance(mrow, dict):
            continue
        member = mrow.get("member") or ""
        for c in mrow.get("cells") or []:
            if not isinstance(c, dict) or not c.get("opponent"):
                continue
            sp = c.get("speed") or {}
            dd = c.get("defense_damage") or {}
            cells_by_opp.setdefault(c["opponent"], []).append({
                "member": member,
                "variantId": c.get("opponent_variant") or c.get("opponent"),
                "coverage": _bounded_share(c.get("opponent_coverage")),
                "offense": _map_offense(c.get("offense")),
                "incoming": _map_offense(dd.get("worst")),
                "speed": {"member": sp.get("member"), "opponent": sp.get("opponent"),
                          "faster": sp.get("faster")},
                "check": _map_check(c.get("check")),
            })
    by_opp = []
    for o in ((cc_raw.get("check_coverage") or {}).get("by_opponent") or []):
        if not isinstance(o, dict) or not o.get("opponent"):
            continue
        witnesses = [str(s) for s in
                     ((o.get("observed_floor") or {}).get("witness_variant_ids") or [])]
        witness_set = set(witnesses)
        floor_by = []
        for variant in o.get("variants") or []:
            if not isinstance(variant, dict) or variant.get("variant_id") not in witness_set:
                continue
            for member in variant.get("best_by") or []:
                if member not in floor_by:
                    floor_by.append(member)
        by_opp.append({
            "opponent": o.get("opponent") or "",
            "usageRank": o.get("usage_rank"),
            "observedFloor": (o.get("observed_floor") or {}).get("grade"),
            "witnessVariantIds": witnesses,
            "floorBy": floor_by,
            "representativeGrade": (o.get("representative") or {}).get("grade"),
            "representedCoverage": _bounded_share(
                (o.get("coverage") or {}).get("represented")),
            "calculationComplete": bool(o.get("calculation_complete")),
            "cells": cells_by_opp.get(o.get("opponent"), []),
        })
    checks = {"topK": int(cc_raw.get("top_k") or 0) or len(by_opp),
              "confidence": str(cc_raw.get("confidence") or ""),
              "byOpponent": by_opp} if by_opp else None
    issues = []
    for msg in validate_raw.get("messages") or []:
        if not isinstance(msg, dict) or not isinstance(msg.get("code"), str):
            continue
        severity = msg.get("severity")
        if severity not in ("error", "warning", "skipped"):
            continue
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        issues.append({"severity": severity, "code": msg["code"],
                       "params": {str(k): v for k, v in params.items()
                                  if v is None or isinstance(v, (str, int, float, bool))}})
    return {
        "team": team,
        "legality": {
            "status": str(validate_raw.get("status") or "unknown"),
            "confidence": validate_raw.get("confidence"),
            "errors": _str_list(validate_raw.get("errors")),
            "warnings": _str_list(validate_raw.get("warnings")),
            "issues": issues,
        },
        "defense": {"byAttackType": by_attack},
        "offense": {
            "hardGaps": [str(t) for t in (offense.get("hard_gaps") or [])],
            "stabTypes": [str(t) for t in (attack_types.get("stab") or [])],
            "otherTypes": [str(t) for t in (attack_types.get("other") or [])],
            # Fidelity with the skill's ⚠️ banner: a listed hard gap is only trustworthy when
            # every counted member has an authoritative moveset. When some don't, their offense
            # is UNKNOWN (not counted) and a gap may be a phantom — the SPA must say so instead
            # of presenting an unconfirmed gap as fact (audit 2026-07-16).
            "gapsConfirmed": bool(offense.get("gaps_confirmed", True)),
            "incompleteMembers": _incomplete_members(offense.get("incomplete_members")),
        },
        "speed": {"order": order(speed.get("order")),
                  "orderUnderTrickRoom": order(speed.get("order_under_trick_room")),
                  "members": members},
        "roles": {"coverage": coverage, "members": role_members,
                  # Same caveat for the role checklist: a "not detected" role is only reliable
                  # when every member's moveset is authoritative — otherwise a move signal may
                  # simply be unknown, not absent (mirrors the skill's ⚠️ role banner).
                  "coverageConfirmed": bool(roles.get("coverage_confirmed", True)),
                  "incompleteMembers": _incomplete_members(roles.get("incomplete_members")),
                  **({"attentionCalibration": calibration} if calibration else {})},
        **({"checks": checks} if checks else {}),
    }


# --- Top Meta (竞技环境 / 上位環境) ---------------------------------------------------------
# The team skill owns the counting and the comparison (scripts/topmeta.py); these mappers only crop
# and name. Every entry is ONE record carrying both sides, so the in-game page and the Top Meta page
# read the same deviation from opposite perspectives — nothing here recomputes it.

# The page shows ten rows per panel; the in-game page additionally needs the record for every row it
# lists itself, so those survive the crop too.
TOP_PANEL_LIMIT = 10
# Web boundary, not a sample threshold: a partner row coming from fewer distinct teams than this is
# close to naming one team's line-up, which the projection must not carry (frontend/design.md §7.1).
TOP_PARTNER_MIN_TEAMS = 3


def map_landscape(raw: dict) -> dict:
    """The team skill's format landscape (structure distributions over the observed real teams of the
    rule), cropped to what a reader asks about. Aggregates only: cores already carry the skill's support
    floor, which equals the projection's partner-row privacy boundary."""
    env = raw.get("environment") or {}
    cores = [{"members": list(c["members"]), "count": int(c["count"]), "share": c["share"]}
             for c in raw.get("observed_cores") or [] if int(c.get("count") or 0) >= TOP_PARTNER_MIN_TEAMS]
    mega = raw.get("mega_slot_distribution") or {}
    return {"format": raw["format"], "rule": env.get("data_rule") or env.get("rule"),
            "season": env.get("season"), "dataSeasons": list(env.get("data_seasons") or []),
            "sample": int(raw["sample_count"]), "thin": bool(raw.get("thin")),
            "speedControlModes": {k: dict(v) for k, v in (raw.get("speed_control_modes") or {}).items()},
            "structuralSignals": {k: dict(v) for k, v in (raw.get("structural_signals") or {}).items()},
            "megaSlots": {"buckets": {k: dict(v) for k, v in (mega.get("buckets") or {}).items()},
                          "average": mega.get("average_registered_mega_slots"),
                          "unknown": mega.get("unknown_sample_count")},
            "cores": cores, "coresTotal": raw.get("cores_total")}


def _topmeta_mod():
    if str(_TEAM_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_TEAM_SCRIPTS))
    import topmeta  # noqa: E402
    return topmeta


# The dex tables a Top Meta entry may name; the table name is interpolated, so it is whitelisted.
_TOP_TABLES = frozenset({"moves", "items", "abilities", "natures", "pokemon"})


def _dex_row(table: str, name: str):
    if table not in _TOP_TABLES:
        raise KeyError(table)
    return _conn().execute(f"select * from {table} where canonical=?", (name,)).fetchone()


def _top_named(name: str, row) -> dict:
    return named(name, row["display_name"] if row else None, row["display_name_ja"] if row else None)


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _top_ingame(ingame: dict | None) -> dict | None:
    if ingame is None:
        return None
    kind = ingame["kind"]
    if kind in ("pct", "rank"):
        return {"kind": kind, "value": ingame["value"]}
    if kind == "bound":
        return {"kind": "bound", "below": ingame["below"]}
    return {"kind": kind}


def _top_value_entry(panel: str, e: dict) -> dict:
    if panel == "spreads":
        row: dict = {"spread": {k: v for k, v in e["spread"].items() if v}}
    else:
        dex = _dex_row(panel, e["name"])
        row = _top_named(e["name"], dex)
        if panel == "moves":
            row.update({"type": type_name(dex["type"]) if dex else None,
                        "category": type_name(dex["category"]) if dex else None,
                        "power": _int_or_none(dex["power"]) if dex else None})
        elif panel == "items":
            row["key"] = f"item:{slugify(e['name'])}"
    row.update({"count": e["count"], "share": e["share"], "ingame": _top_ingame(e["ingame"]),
                "delta": e["top_minus_ingame"], "withinNoise": e["within_noise"],
                "marker": e["marker"]})
    if e.get("delta_is_floor"):
        row["deltaIsFloor"] = True
    return row


def _top_value_panel(panel: str, p: dict) -> dict:
    observed = [e for e in p["entries"] if e["count"] > 0]
    keep = {id(e) for e in observed[:TOP_PANEL_LIMIT]}
    keep |= {id(e) for e in p["entries"] if (e.get("ingame") or {}).get("kind") == "pct"}
    return {"n": p["n"], "tier": p["tier"],
            "entries": [_top_value_entry(panel, e) for e in p["entries"] if id(e) in keep]}


def _top_partner_panel(p: dict, slug_of) -> dict:
    entries = []
    for e in p["entries"]:
        listed_ingame = (e.get("ingame") or {}).get("kind") == "rank"
        if 0 < e["count"] < TOP_PARTNER_MIN_TEAMS:
            continue
        if e["count"] == 0 and not listed_ingame:
            continue
        if e["count"] > 0 and (e["rank"] or 0) > TOP_PANEL_LIMIT and not listed_ingame:
            continue
        entries.append({**_top_named(e["name"], _dex_row("pokemon", e["name"])), "slug": slug_of(e["name"]),
                        "key": f"pokemon:{slugify(e['name'])}",
                        "count": e["count"], "share": e["share"], "rank": e["rank"],
                        "ingame": _top_ingame(e["ingame"]), "rankDelta": e["rank_delta"]})
    return {"n": p["n"], "tier": p["tier"], "entries": entries}


def _top_header(doc: dict) -> dict:
    out = {"format": doc["format"], "rule": doc["rule"], "scope": doc["scope"],
           "season": doc.get("season"), "partitions": list(doc.get("partitions") or []),
           "sample": {"teams": doc["sample"]["teams"], "sets": doc["sample"]["sets"]},
           "comparedWith": None}
    cw = doc.get("compared_with")
    if cw:
        out["comparedWith"] = {"season": cw["season"], "window": cw.get("window", "same_season")}
        if cw.get("updated_at"):
            out["comparedWith"]["updatedAt"] = cw["updated_at"]
    if doc.get("built_at"):
        out["builtAt"] = doc["built_at"]
    if doc.get("criteria"):
        # The high slice: the raw-fact bar and how many team units each source kind contributed.
        out["criteria"] = {"tournamentTop8": {"placingAtMost": doc["criteria"]["tournament_top8"]["placing_at_most"],
                                              "fieldSizeAtLeast": doc["criteria"]["tournament_top8"]["field_size_at_least"]},
                           "ladderTop": {"rankAtMost": doc["criteria"]["ladder_top"]["rank_at_most"],
                                         "orRatingAtLeast": doc["criteria"]["ladder_top"]["or_rating_at_least"]}}
        out["composition"] = {"tournamentTop8": int((doc.get("composition") or {}).get("tournament_top8") or 0),
                              "ladderTop": int((doc.get("composition") or {}).get("ladder_top") or 0)}
    return out


def map_top_ranking(doc: dict, slug_of) -> dict:
    """One scope's Top Meta ranking. `slug_of` gives the in-game meta slug where the species is
    ranked in-game (so both views route to the same detail page), else the dex slug."""
    rows = []
    for r in doc["ranking"]:
        dex = _dex_row("pokemon", r["name"])
        rows.append({
            "rank": r["rank"], "slug": slug_of(r["name"]), "key": f"pokemon:{slugify(r['name'])}",
            "types": json.loads(dex["types_json"] or "[]") if dex else [],
            **_top_named(r["name"], dex),
            "teams": r["teams"], "share": r["share"], "tier": r["tier"],
            "ingameRank": r["ingame_rank"], "rankDelta": r["rank_delta"],
            "withinNoise": r["within_noise"],
        })
    return {**_top_header(doc), "rows": rows}


def _top_scope(doc: dict | None, name: str, slug_of) -> dict | None:
    if doc is None or name not in doc["species"]:
        return None
    s = doc["species"][name]
    rank = next((r for r in doc["ranking"] if r["name"] == name), None)
    panels = {panel: _top_value_panel(panel, s["panels"][panel])
              for panel in ("moves", "items", "abilities", "natures", "spreads")}
    panels["partners"] = _top_partner_panel(s["panels"]["partners"], slug_of)
    return {
        **_top_header(doc),
        "rank": rank["rank"] if rank else None, "tier": rank["tier"] if rank else None,
        "share": rank["share"] if rank else None,
        "rankDelta": rank["rank_delta"] if rank else None,
        "withinNoise": rank["within_noise"] if rank else None,
        "ingameRank": s["ingame_rank"], "teams": s["teams"], "sets": s["sets"],
        "lanes": dict(s["lanes"]), "panels": panels,
    }


def map_top_detail(season_doc: dict, rule_doc: dict, name: str, slug_of, high_doc: dict | None = None) -> dict:
    """One Pokemon's Top Meta panels for EVERY scope in one document: switching scope on the page
    costs no request, and the in-game page reads the season scope's records for its comparison."""
    dex = _dex_row("pokemon", name)
    return {
        "slug": slug_of(name), "key": f"pokemon:{slugify(name)}",
        **_top_named(name, dex),
        "format": season_doc["format"], "rule": season_doc["rule"], "season": season_doc.get("season"),
        "scopes": {"season": _top_scope(season_doc, name, slug_of),
                   "rule": _top_scope(rule_doc, name, slug_of),
                   **({"high": _top_scope(high_doc, name, slug_of)} if high_doc is not None else {})},
    }


def top_meta_docs(fmt: str, meta_data, season: str, rule: str) -> dict | None:
    """Both scopes compared through the team skill, plus the slug lookup. None when no table ships.

    The in-game side is read from the shipped meta files (ranking + details of the CURRENT season)
    and its zh/ja names resolved through the dex — the same single naming authority map_detail uses."""
    tm = _topmeta_mod()
    table = tm.load_table(fmt, rule)
    if table is None:
        return None
    ranking = json.loads((meta_data / f"ranking_{season}_{fmt}.json").read_text(encoding="utf-8"))
    details = json.loads((meta_data / f"details_{season}_{fmt}.json").read_text(encoding="utf-8"))

    def resolve_batch(kind: str, names: list[str]) -> dict:
        return {n: resolve_en(kind, n) for n in names}

    ingame = tm.ingame_from_meta(ranking, details.get("rows") or [], resolve_batch)
    slugs = ingame["slugs"]

    def slug_of(name: str) -> str:
        return slugs.get(name) or slugify(name)

    season_doc = tm.compare(table, "season", ingame)
    rule_doc = tm.compare(table, "rule", ingame)
    high_doc = tm.compare(table, "high", ingame) if "high" in table["scopes"] else None
    names = sorted(set(season_doc["species"]) | set(rule_doc["species"]))
    return {"season": season_doc, "rule": rule_doc, "high": high_doc, "slug_of": slug_of,
            "by_slug": {slug_of(n): n for n in names}}
