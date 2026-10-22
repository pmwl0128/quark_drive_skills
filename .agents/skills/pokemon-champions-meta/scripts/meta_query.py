#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import io
import re
import sys
import zipfile
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from meta_common import (
    CACHE_DIR,
    EnvironmentResolutionError,
    details_path,
    ko_path,
    load_json,
    maybe_repair_cn,
    norm_panel,
    normalize,
    ranking_path,
    resolve_season_rule,
)

# Output-text i18n (md labels / error prose only; canonical JSON is never localized). Self-contained,
# unique module name so it can't collide with the sibling skills' own *_i18n modules on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import meta_i18n as i18n  # noqa: E402

# The sibling dex skill is the naming authority. Rather than re-read its sqlite and reimplement name
# resolution, meta reuses champdex IN-PROCESS so resolution (incl. the conservative fuzzy fallback, now
# the dex default) lives in ONE place. Absent dex
# degrades to identity (names returned unchanged). Resolved relative to the installed skills root.
_DEX_SCRIPTS = Path(__file__).resolve().parents[2] / "pokemon-champions-dex" / "scripts"
try:
    if str(_DEX_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_DEX_SCRIPTS))
    import champdex as _dex  # type: ignore
except Exception:                       # dex skill not installed alongside; degrade gracefully
    _dex = None

# panel name -> dex kind for display resolution (the dex is the naming authority for every kind,
# natures included now that the dex carries a natures table).
_KIND_BY_PANEL = {"moves": "move", "items": "item", "abilities": "ability", "partners": "pokemon",
                  "natures": "nature"}
_DEX_TABLE = {"pokemon": "pokemon", "move": "moves", "ability": "abilities", "item": "items"}

_DEX_CONN: Any = None


def _dex_conn() -> Any:
    """One cached read-only connection to the dex DB (the DB is read-only; safe for the session)."""
    global _DEX_CONN
    if _dex is None:
        return None
    if _DEX_CONN is None:
        try:
            _DEX_CONN = _dex.conn()
        except SystemExit:              # dex DB file missing
            return None
    return _DEX_CONN

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


# Cached so a resident worker (worker.py / _serve.py) parses each panel file once per session
# instead of once per request. Callers treat the result as read-only. A one-shot process loads
# it once anyway, so the cache is harmless there.
@lru_cache(maxsize=None)
def load_ranking(season: str, fmt: str) -> list[dict[str, Any]]:
    return load_json(ranking_path(season, fmt), {"rows": []}).get("rows", [])


@lru_cache(maxsize=None)
def load_details(season: str, fmt: str) -> list[dict[str, Any]]:
    return load_json(details_path(season, fmt), {"rows": []}).get("rows", [])


@lru_cache(maxsize=None)
def load_ko(season: str, fmt: str) -> dict[str, Any]:
    """The whole KO file, not just its rows: `coverage` is part of the answer. It states which tiers
    were collected, so a caller never has to read an absent panel as "no KO moves exist"."""
    return load_json(ko_path(season, fmt), {})


def dex_resolve(name: str) -> tuple[set[str], dict[str, Any] | None]:
    """Resolve `name` through the dex (fuzzy by default) and return (aliases, resolution).

    `aliases` is every dex alias (any language) that shares a canonical with the resolved name, used to
    match a user query against panel rows stored in mixed languages. `resolution` is the dex fuzzy block
    — {match_type, score, distance, from} — but ONLY when the hit was a typo correction; an exact hit (or
    no dex) returns None, because there is nothing to flag. Surfacing this keeps meta honest with the dex
    rule that a fuzzy correction is never silent."""
    aliases = {name}
    resolution: dict[str, Any] | None = None
    c = _dex_conn()
    if c is None:
        return aliases, resolution
    try:
        rr = _dex.resolve_rich(c, "pokemon", name)        # dex default: fuzzy-tolerant
        canon = rr.get("canonical")
        if not canon:
            return aliases, resolution
        aliases.add(canon)
        for row in c.execute("select alias from aliases where kind='pokemon' and canonical=?", (canon,)):
            if row["alias"]:
                aliases.add(str(row["alias"]))
        if rr.get("match_type") == "fuzzy":
            resolution = {"match_type": "fuzzy", "score": rr.get("score"),
                          "distance": rr.get("distance"), "from": rr.get("query_norm")}
    except Exception:
        pass
    return aliases, resolution


def dex_aliases(name: str) -> set[str]:
    """Back-compat thin wrapper: just the alias set (drops the resolution block)."""
    return dex_resolve(name)[0]


def dex_alias_norms_rich(text: str, kinds: tuple[str, ...]) -> tuple[set[str], dict[str, Any] | None]:
    """Like `dex_alias_norms` but ALSO returns the dex fuzzy block for the first kind that resolved the
    query as a TYPO correction — {kind, resolved (canonical), match_type, score, distance, from} — or
    None when every match was exact/alias (or no dex). `search` uses this to keep a fuzzy correction
    non-silent (the dex rule: an auto-correction is never silent), the way `detail`/`compare` do."""
    out = {normalize(text)}
    resolution: dict[str, Any] | None = None
    c = _dex_conn()
    if c is None or not text:
        return out, resolution
    for kind in kinds:
        try:
            rr = _dex.resolve_rich(c, kind, text)        # fuzzy-tolerant, like every other dex path
            canon = rr.get("canonical")
            if not canon:
                continue
            out.add(normalize(canon))
            for row in c.execute("select alias from aliases where kind=? and canonical=?", (kind, canon)):
                if row["alias"]:
                    out.add(normalize(str(row["alias"])))
            if resolution is None and rr.get("match_type") == "fuzzy":
                resolution = {"kind": kind, "resolved": canon, "match_type": "fuzzy",
                              "score": rr.get("score"), "distance": rr.get("distance"),
                              "from": rr.get("query_norm")}
        except Exception:
            continue
    return out, resolution


def dex_alias_norms(text: str, kinds: tuple[str, ...]) -> set[str]:
    """Normalized aliases of `text` across the given dex kinds, so a search --name in ANY language (or
    the canonical English the contract tells callers to use) matches a panel row stored only in Chinese
    + Japanese. The shipped move/item/ability panels carry no English, so without this an English query
    silently returns 0 (design promise: name lookups resolve through the dex, all three languages).
    Always includes the literal so a no-dex / unknown query still matches its own spelling."""
    return dex_alias_norms_rich(text, kinds)[0]


def dex_canonical_type(text: str) -> str:
    """The canonical (English) type for a type query in any language (飞行 / ひこう / Flying), so the
    --type filter matches the English type stored in the data. Falls back to the literal (English types
    pass through unchanged; an unknown value stays itself and simply matches nothing)."""
    c = _dex_conn()
    if c is None or not text:
        return text
    try:
        return _dex.resolve(c, "type", text) or text
    except Exception:
        return text


@lru_cache(maxsize=None)
def _dex_display(kind: str, text: str, lang: str = "zh") -> str | None:
    """Authoritative display for a name of a given dex kind via the dex (fuzzy-tolerant), or None when
    the dex is unavailable or the name doesn't resolve. `lang`: zh -> display_name, ja -> display_name_ja
    (falls back to the canonical English so a ja workbook never silently shows Chinese), en -> the
    English canonical (the join key itself)."""
    c = _dex_conn()
    if c is None or not text:
        return None
    try:
        rr = _dex.resolve_rich(c, kind, text)             # dex default: fuzzy-tolerant
        canon = rr.get("canonical")
        if not canon:
            return None
        if lang == "en":
            return canon
        if lang == "ja":
            row = c.execute(f"select display_name_ja from {_DEX_TABLE[kind]} where canonical=?", (canon,)).fetchone()
            return (row["display_name_ja"] if row and row["display_name_ja"] else None) or canon
        row = c.execute(f"select display_name from {_DEX_TABLE[kind]} where canonical=?", (canon,)).fetchone()
        return row["display_name"] if row and row["display_name"] else None
    except Exception:
        return None


# Garbled-CN re-decode chain (mojibake from mixed source encodings) \u2014 tried only if the dex can't
# resolve the name as-is, since a re-decoded byte string sometimes matches a real alias.
_REDECODE = (("gbk", "big5"), ("gbk", "cp950"), ("gb18030", "big5"), ("gb18030", "cp950"),
             ("big5", "gbk"), ("cp950", "gbk"), ("big5", "gb18030"), ("cp950", "gb18030"))


def repair_display_name(name: Any, kind: str | None = None, lang: str = "zh") -> str:
    """Best display for a scraped name in `lang` (zh/ja/en). Asks the dex (the naming authority) for the
    canonical display of the given kind, fuzzy-tolerant; `kind` is None for entries with no dex entity,
    which are returned as-is. Falls back to the source text when the dex can't resolve it."""
    text = maybe_repair_cn(str(name or ""))
    if not text or not kind:
        return text
    disp = _dex_display(kind, text, lang)
    if disp:
        return disp
    for source, target in _REDECODE:
        try:
            candidate = text.encode(source).decode(target)
        except Exception:
            continue
        disp = _dex_display(kind, candidate, lang)
        if disp:
            return disp
    return text


def _flag_resolution(detail: dict[str, Any], name: str,
                     resolution: dict[str, Any] | None) -> dict[str, Any]:
    """Attach the typo-correction metadata to a COPY of the matched panel (the panel is shared lru-cache
    state — never mutate it). No-op when the query resolved exactly. Surfaces `query` (raw input),
    `resolved_name` (English canonical of the matched panel), and `name_resolution` (the dex fuzzy block)
    so a caller can distinguish an exact lookup from an auto-corrected typo."""
    if not resolution:
        return detail
    resolved = detail.get("pokemon_en") or detail.get("slug") or ""
    return {**detail, "query": name, "resolved_name": resolved,
            "name_resolution": {**resolution, "resolved": resolved}}


def _resolve_one(name: str, ranking: list[dict[str, Any]],
                 details_by_slug: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Resolve one name against already-loaded ranking + details (no file I/O here). A fuzzy (typo) hit
    carries a non-silent `name_resolution` block on the returned copy; an exact hit returns the panel as-is."""
    aliases, resolution = dex_resolve(name)
    needles = {normalize(x) for x in aliases}
    needles.discard("")  # never match on empty fields
    for row in ranking:
        values = [
            row.get("slug", ""),
            row.get("pokemon", ""),
            row.get("alt_name", ""),
            row.get("pokemon_en", ""),
            row.get("pokemon_ja", ""),
        ]
        if any(normalize(v) in needles for v in values):
            return _flag_resolution(details_by_slug.get(row.get("slug")) or row, name, resolution)
    for row in details_by_slug.values():
        values = [row.get("slug", ""), row.get("pokemon", ""), row.get("pokemon_en", ""), row.get("pokemon_ja", "")]
        if any(normalize(v) in needles for v in values):
            return _flag_resolution(row, name, resolution)
    return None


def resolve_many(names: list[str], season: str, fmt: str) -> list[dict[str, Any] | None]:
    """Resolve several names against the season/format panels, loading the data files ONCE.
    Returns a list aligned to `names` (None where a name doesn't resolve)."""
    ranking = load_ranking(season, fmt)
    details_by_slug = {row.get("slug"): row for row in load_details(season, fmt)}
    return [_resolve_one(n, ranking, details_by_slug) for n in names]


def resolve_pokemon(name: str, season: str, fmt: str) -> dict[str, Any] | None:
    return resolve_many([name], season, fmt)[0]


# --- Canonical I/O contract (dev/conventions.md) ----------------------------------------
# Applied to every JSON CLI result via emit(): pokemon rows expose the join key `name` (English) plus
# localized `name_zh`/`name_ja` alongside the existing pokemon_*/slug (kept for back-compat), and move
# type/category are Title-cased to match dex/calc. Panel entries own `name` (the move/ability/item), so
# pokemon-identity keys are added ONLY to pure pokemon rows (slug present, no panel `key`) — never to
# the flattened search rows, where `name` is the matched entry. Data files are left untouched.
_ID_CANON = (("pokemon_en", "name"), ("pokemon", "name_zh"), ("pokemon_ja", "name_ja"))


def canonicalize(obj: Any) -> Any:
    if isinstance(obj, list):
        return [canonicalize(x) for x in obj]
    if isinstance(obj, dict):
        out = {k: (canonicalize(v) if isinstance(v, (dict, list)) else v) for k, v in obj.items()}
        if "slug" in out and "key" not in out:           # a pure pokemon row, not a panel/search row
            for src, dst in _ID_CANON:
                if out.get(src) and dst not in out:
                    out[dst] = out[src]
        for f in ("type", "category"):                   # match dex/calc Title-case
            if isinstance(out.get(f), str) and out[f]:
                out[f] = out[f].capitalize()
        return out
    return obj


def emit(data: Any, output: str) -> None:
    if output == "json":
        print(json.dumps(canonicalize(data), ensure_ascii=False, indent=2))
    else:
        print(data if isinstance(data, str) else to_markdown(data))


def _md_pokemon(row: dict[str, Any]) -> str:
    lang = i18n.lang()
    direct = row.get({"en": "pokemon_en", "ja": "pokemon_ja"}.get(lang, "pokemon"))
    src = direct or row.get("pokemon") or row.get("pokemon_en") or row.get("slug") or ""
    return repair_display_name(src, "pokemon", lang)


def _md_panel_name(entry: dict[str, Any], panel: str) -> str:
    lang = i18n.lang()
    if panel == "spreads":
        return i18n.spread(entry)
    src = (entry.get("name_ja") if lang == "ja" else entry.get("name")) \
        or entry.get("name_ja") or entry.get("key") or ""
    return repair_display_name(src, _KIND_BY_PANEL.get(panel), i18n.lang())


def ranking_md(rows: list[dict[str, Any]]) -> str:
    lines = [f"| {i18n.t('rank')} | {i18n.t('pokemon')} | {i18n.t('slug')} | {i18n.t('en')} |", "|---:|---|---|---|"]
    for row in rows:
        lines.append(f"| {row.get('rank','')} | {_md_pokemon(row)} | {row.get('slug','')} | {row.get('pokemon_en','')} |")
    return "\n".join(lines)


def detail_md(detail: dict[str, Any], panel: str | None = None) -> str:
    heading_context = (f"{i18n.value('format', detail.get('format', ''))} · "
                       f"{detail.get('season')}/{detail.get('rule', '')}")
    lines = [
        f"# {_md_pokemon(detail)}{i18n.parens(heading_context)}",
        "",
        f"- {i18n.t('rank')}: {detail.get('rank','')}",
        f"- {i18n.t('slug')}: {detail.get('slug','')}",
        f"- {i18n.t('en')}: {detail.get('pokemon_en','')}",
    ]
    nr = detail.get("name_resolution")
    if nr:  # non-silent typo correction: tell the reader the query was auto-resolved
        correction = f"{i18n.t('fuzzy')}, {i18n.t('distance')} {nr.get('distance')}"
        lines.append(f"- {i18n.t('resolved')}: `{detail.get('query','')}` → {detail.get('resolved_name','')}"
                     f"{i18n.parens(correction)}")
    lines.append("")
    panels = detail.get("panels", {})
    selected = [norm_panel(panel)] if panel else ["moves", "items", "abilities", "natures", "partners", "spreads"]
    for key in selected:
        entries = panels.get(key, [])
        lines.extend([f"## {i18n.panel_label(key)}", "",
                      f"| {i18n.t('rank')} | {i18n.t('name')} | {i18n.t('usage')} | {i18n.t('extra')} |",
                      "|---:|---|---:|---|"])
        for entry in entries:
            extra = []
            for k in ("type", "category", "power", "accuracy"):
                if entry.get(k) not in ("", None):
                    value = i18n.value(k, entry.get(k)) if k in ("type", "category") else entry.get(k)
                    extra.append(f"{i18n.t(k) if k in ('type', 'category', 'power', 'accuracy') else k}={value}")
            lines.append(
                f"| {entry.get('rank','')} | {_md_panel_name(entry, key)} | "
                f"{percent_text(entry.get('percentage'))} | {i18n.list_sep().join(extra) if extra else '—'} |"
            )
        if not entries:
            lines.append("|  |  |  |  |")
        lines.append("")
    return "\n".join(lines)


def search_md(rows: list[dict[str, Any]]) -> str:
    lines = [f"| {i18n.t('pokemon_rank')} | {i18n.t('pokemon')} | {i18n.t('slug')} | {i18n.t('format')} | "
             f"{i18n.t('panel')} | {i18n.t('entry_rank')} | {i18n.t('name')} | {i18n.t('usage')} |",
             "|---:|---|---|---|---|---:|---|---:|"]
    for row in rows:
        lines.append(
            f"| {row.get('pokemon_rank','')} | {_md_pokemon(row)} | {row.get('slug','')} | {i18n.value('format', row.get('format',''))} | "
            f"{i18n.panel_label(row.get('panel',''))} | {row.get('entry_rank','')} | {_md_panel_name(row, row.get('panel',''))} | {percent_text(row.get('percentage'))} |"
        )
    return "\n".join(lines)


def to_markdown(data: Any) -> str:
    if isinstance(data, list):
        return search_md(data)
    if isinstance(data, dict):
        return detail_md(data)
    return str(data)


VALID_PANELS = {"moves", "items", "abilities", "natures", "partners", "spreads"}


def _emit_meta_error(query: str, code: str, message: str) -> None:
    """§3 error shape, always JSON — a machine parses stdout on a request-level failure."""
    emit({"ok": False, "query": query, "error": {"code": code, "message": message}}, "json")


def _require_data(season: str, fmt: str) -> None:
    """Guard: an unknown season/format (no cache file) is a bad REQUEST, not an empty metagame. Emit
    §3 `unknown_format` + exit 1 instead of silently returning rows:[] that a caller misreads as
    'nothing is used'. `both` passes if EITHER single or double exists."""
    fmts = ["single", "double"] if fmt == "both" else [fmt]
    if not any(ranking_path(season, f).exists() or details_path(season, f).exists() for f in fmts):
        _emit_meta_error(f"{season}/{fmt}", "unknown_format",
                         f"no metagame data for season={season} format={fmt} "
                         "(unknown season/format, or the cache is not built)")
        raise SystemExit(1)


def _require_panel(panel: str | None) -> None:
    """Guard: an unknown --panel would silently filter to []; make it a §3 `bad_input` instead."""
    if panel and norm_panel(panel) not in VALID_PANELS:
        _emit_meta_error(panel, "bad_input",
                         f"unknown panel '{panel}'; expected one of {sorted(VALID_PANELS)}")
        raise SystemExit(1)


def command_ranking(args: argparse.Namespace) -> None:
    args.season, args.rule = resolve_season_rule(args.season, args.rule)
    _require_data(args.season, args.format)
    rows = load_ranking(args.season, args.format)[: args.limit]
    emit({"season": args.season, "rule": args.rule, "format": args.format, "rows": rows} if args.output == "json" else ranking_md(rows), args.output)


def _panel_filtered(detail: dict[str, Any], panel: str | None) -> dict[str, Any]:
    if not panel:
        return detail
    return {**detail, "panels": {norm_panel(panel): detail.get("panels", {}).get(norm_panel(panel), [])}}


def command_detail(args: argparse.Namespace) -> None:
    args.season, args.rule = resolve_season_rule(args.season, args.rule)
    _require_data(args.season, args.format)
    _require_panel(args.panel)
    # De-dup while preserving order: `detail Garchomp --pokemon Garchomp` (positional + flag naming the
    # same mon) must not resolve/print it twice (audit 2026-07-06).
    names = list(dict.fromkeys(list(args.pokemon or []) + list(getattr(args, "pokemon_pos", None) or [])))
    if not names:
        args.parser.error("detail requires --pokemon <name ...> or positional <name ...>")
    resolved = resolve_many(names, args.season, args.format)
    if len(names) == 1:
        # Single name keeps the original scalar shape (object for json, md report).
        detail = resolved[0]
        if not detail:
            # A not-found Pokemon in a VALID season is a graceful miss, not a failure -> exit 0 (§3),
            # consistent with the batch path below. The error `message` stays English-stable (machine
            # contract, paired with the stable `code`); md prints the localized prose to stdout.
            if args.output == "json":
                msg = f"not found: {names[0]} in {args.season} {args.format}"
                emit({"ok": False, "query": names[0], "error": {"code": "not_found", "message": msg}}, "json")
            else:
                print(i18n.t("not_found", query=names[0], context=f"{args.season} {args.format}"))
            return
        emit(_panel_filtered(detail, args.panel) if args.output == "json" else detail_md(detail, args.panel),
             args.output)
        return
    # Batch: resolve every name in ONE invocation (data files loaded once). Aligned to `names`;
    # an unresolved name becomes the uniform error object rather than aborting the whole batch.
    if args.output == "json":
        # Batch errors carry `index` to align with request order (conventions.md error shape; audit
        # 2026-06-24 — only `query` was present before).
        out = [_panel_filtered(d, args.panel) if d
               else {"ok": False, "index": i, "query": n,
                     "error": {"code": "not_found", "message": f"not found: {n} in {args.season} {args.format}"}}
               for i, (n, d) in enumerate(zip(names, resolved))]
        emit(out, "json")
    else:
        blocks = [detail_md(d, args.panel) if d else f"# {n}\n- {i18n.t('nf')}" for n, d in zip(names, resolved)]
        print("\n\n---\n\n".join(blocks))


# --- KO axis --------------------------------------------------------------------------------
# A second data family (ko_<season>_<format>.json) with its own upstream and snapshot clock. Its
# object panels are SPECIES-level (keyed by national dex no) and carry no percentage — the source
# publishes an ordering only — while the move panels are a reserved tier that stays `null` until
# collected. Both facts are restated in every answer's `coverage`, never left to be inferred.
KO_OBJECT_PANELS = ("ko_targets", "koed_by")
KO_MOVE_PANELS = ("ko_moves", "koed_by_moves")
VALID_KO_PANELS = {*KO_OBJECT_PANELS, *KO_MOVE_PANELS}
# Accepted --panel spellings, including the source-facing words a caller is likely to reach for.
_KO_PANEL_ALIASES = {
    "beats": "ko_targets", "ko": "ko_targets", "kos": "ko_targets", "targets": "ko_targets",
    "ko_targets": "ko_targets",
    "counters": "koed_by", "koed": "koed_by", "koed_by": "koed_by",
    "ko_moves": "ko_moves", "komoves": "ko_moves",
    "koed_by_moves": "koed_by_moves", "koedbymoves": "koed_by_moves",
}


def norm_ko_panel(panel: str) -> str:
    return _KO_PANEL_ALIASES.get(str(panel).strip().lower().replace("-", "_"), str(panel))


def _ko_entry_name(entry: dict[str, Any], panel: str) -> str:
    """Display for one KO entry in the active language.

    Object entries carry all three names inline — they are keyed by a dex NUMBER, which the dex
    resolver cannot localize on its own — so the fallback chain ends at `name_en` rather than
    borrowing a form name. Move entries localize through the dex like any other move panel."""
    if panel in KO_MOVE_PANELS:
        return _md_panel_name(entry, "moves")
    order = {"zh": ("name", "name_en", "name_ja"),
             "ja": ("name_ja", "name_en", "name"),
             "en": ("name_en", "name", "name_ja")}[i18n.lang()]
    for key in order:
        if entry.get(key):
            return str(entry[key])
    return str(entry.get("key") or "")


def _ko_row_for(name: str, season: str, fmt: str) -> dict[str, Any] | None:
    """Resolve a query to one KO row, reusing the dex alias set the other commands use."""
    aliases, _resolution = dex_resolve(name)
    needles = {normalize(x) for x in aliases}
    needles.discard("")
    for row in load_ko(season, fmt).get("rows") or []:
        values = [row.get("slug", ""), row.get("pokemon", ""), row.get("pokemon_en", ""),
                  row.get("pokemon_ja", "")]
        if any(normalize(v) in needles for v in values):
            return row
    return None


def ko_md(row: dict[str, Any], meta: dict[str, Any], panel: str | None) -> str:
    coverage = meta.get("coverage") or {}
    heading = (f"{i18n.value('format', meta.get('format', ''))} · "
               f"{meta.get('season')}/{meta.get('rule', '')}")
    lines = [f"# {_md_pokemon(row)}{i18n.parens(heading)}", "",
             f"- {i18n.t('rank')}: {row.get('rank', '')}",
             f"- {i18n.t('ko_snapshot')}: {meta.get('updated_at', '')}",
             f"- {i18n.t('ko_no_pct')}",
             f"- {i18n.t('ko_species_level')}"]
    if coverage.get("reused_from"):
        lines.append(f"- {i18n.t('ko_reused_from', season=coverage['reused_from']['season'])}")
    if coverage.get("move_share") == "absent":
        lines.append(f"- {i18n.t('ko_moves_absent')}")
    lines.append("")
    panels = row.get("panels") or {}
    for key in ([panel] if panel else [*KO_OBJECT_PANELS, *KO_MOVE_PANELS]):
        entries = panels.get(key)
        lines.append(f"## {i18n.panel_label(key)}")
        retained = (row.get("panel_reused_from") or {}).get(key)
        if retained:
            lines.append(i18n.t("ko_zero_retained", season=retained["season"],
                                time=retained["updated_at"]))
        if entries is None:                      # tier not collected — never rendered as "none"
            lines.extend([i18n.t("ko_moves_absent"), ""])
            continue
        if not entries:
            lines.extend([i18n.t("nf"), ""])
            continue
        for entry in entries:
            usage = percent_text(entry.get("percentage"))
            suffix = f" ({usage})" if usage else ""
            # A repeated species in one list is an acknowledged upstream ambiguity, not a duplicated
            # row — say so inline, or the reader reads it as a bug (see `form_collapsed`).
            if entry.get("form_collapsed"):
                suffix += i18n.parens(i18n.t("ko_form_collapsed"))
            lines.append(f"{ranked_prefix(entry)}{_ko_entry_name(entry, key)}{suffix}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def command_ko(args: argparse.Namespace) -> None:
    args.season, args.rule = resolve_season_rule(args.season, args.rule)
    _require_data(args.season, args.format)
    panel = norm_ko_panel(args.panel) if args.panel else None
    if panel and panel not in VALID_KO_PANELS:
        _emit_meta_error(args.panel, "bad_input",
                         f"unknown KO panel '{args.panel}'; expected one of {sorted(VALID_KO_PANELS)}")
        raise SystemExit(1)
    meta = load_ko(args.season, args.format)
    context = f"{args.season} {args.format}"
    if not meta.get("rows"):
        # The season/format is valid but this axis was not collected for it — a DATA GAP, not a bad
        # request. So it is a graceful miss (exit 0) that NAMES the gap, instead of an empty list a
        # caller could read as "this Pokemon knocks nothing out".
        if args.output == "json":
            emit({"ok": False, "query": context, "error": {
                "code": "not_found",
                "message": f"no KO data for {context} (this snapshot was not collected)"}}, "json")
        else:
            print(i18n.t("ko_missing", context=context))
        return
    names = list(dict.fromkeys(list(args.pokemon or [])
                               + list(getattr(args, "pokemon_pos", None) or [])))
    header = {"season": args.season, "rule": args.rule, "format": args.format,
              "updated_at": meta.get("updated_at"), "coverage": meta.get("coverage")}
    if not names:
        # No name asked: answer with coverage. This is what a caller reads BEFORE spending a lookup
        # on a panel that may not be collected in this snapshot.
        if args.output == "json":
            emit({**header, "rows": len(meta.get("rows") or [])}, "json")
            return
        absent = (meta.get("coverage") or {}).get("move_share") == "absent"
        print("\n".join([
            f"# {i18n.t('ko_snapshot')}: {meta.get('updated_at', '')}",
            f"- {i18n.t('format')}: {i18n.value('format', args.format)}",
            f"- {i18n.t('pokemon')}: {len(meta.get('rows') or [])}",
            f"- {i18n.t('ko_no_pct')}",
            f"- {i18n.t('ko_species_level')}",
            *([f"- {i18n.t('ko_reused_from', season=meta['coverage']['reused_from']['season'])}"]
              if (meta.get("coverage") or {}).get("reused_from") else []),
            *([f"- {i18n.t('ko_moves_absent')}"] if absent else []),
        ]))
        return
    resolved = [(n, _ko_row_for(n, args.season, args.format)) for n in names]

    def _shape(row: dict[str, Any]) -> dict[str, Any]:
        if not panel:
            return row
        return {**row, "panels": {panel: (row.get("panels") or {}).get(panel)}}

    def _miss(name: str, index: int | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": False, "query": name, "error": {
            "code": "not_found", "message": f"not found: {name} in {context}"}}
        if index is not None:
            out = {"ok": False, "index": index, **{k: v for k, v in out.items() if k != "ok"}}
        return out

    if len(names) == 1:
        name, row = resolved[0]
        if not row:
            if args.output == "json":
                emit(_miss(name), "json")
            else:
                print(i18n.t("not_found", query=name, context=context))
            return
        emit({**header, **_shape(row)} if args.output == "json" else ko_md(row, meta, panel),
             args.output)
        return
    if args.output == "json":
        emit([{**header, **_shape(row)} if row else _miss(name, i)
              for i, (name, row) in enumerate(resolved)], "json")
        return
    print("\n\n---\n\n".join(ko_md(row, meta, panel) if row else f"# {name}\n- {i18n.t('nf')}"
                            for name, row in resolved))


def iter_panel_rows(season: str, fmt: str, panel: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    panels = [norm_panel(panel)] if panel else ["moves", "items", "abilities", "natures", "partners", "spreads"]
    for detail in load_details(season, fmt):
        for key in panels:
            for entry in detail.get("panels", {}).get(key, []) or []:
                row = {
                    "pokemon_rank": detail.get("rank"),
                    "pokemon": detail.get("pokemon"),
                    "slug": detail.get("slug"),
                    "format": fmt,
                    "season": season,
                    "rule": detail.get("rule", ""),
                    "panel": key,
                    "entry_rank": entry.get("rank"),
                    "name": entry.get("name"),
                    "name_ja": entry.get("name_ja"),
                    "percentage": entry.get("percentage"),
                }
                row.update({k: v for k, v in entry.items() if k not in row})
                rows.append(row)
    return rows


def percent_text(value: Any) -> str:
    if value in ("", None):
        return ""
    try:
        return f"{float(value):g}%"
    except Exception:
        return str(value)


def ranked_prefix(entry: dict[str, Any]) -> str:
    rank = entry.get("rank")
    return f"{rank}. " if rank not in ("", None) else ""


# Nature stat effect (+raised / -lowered) now comes from the dex natures table (the one naming +
# data authority). The single-letter form A/B/C/D/S (atk/def/spa/spd/spe) matches the spread panel.
_SP_LETTER = {"atk": "A", "def": "B", "spa": "C", "spd": "D", "spe": "S"}


def _dex_nature_effect(name: str) -> tuple[str, str] | None:
    """(+stat, -stat) single-letter effect for a nature via the dex, or None (neutral / unresolved)."""
    c = _dex_conn()
    if c is None or not name:
        return None
    try:
        rr = _dex.resolve_rich(c, "nature", name)
        canon = rr.get("canonical")
        if not canon:
            return None
        row = c.execute("select up_stat, down_stat from natures where canonical=?", (canon,)).fetchone()
        if not row or not row["up_stat"] or not row["down_stat"]:
            return None
        return _SP_LETTER.get(row["up_stat"], row["up_stat"]), _SP_LETTER.get(row["down_stat"], row["down_stat"])
    except Exception:
        return None


def nature_effect_text(entry: dict[str, Any], name: str) -> str:
    eff = (_dex_nature_effect(entry.get("name") or "")
           or _dex_nature_effect(entry.get("name_ja") or "")
           or _dex_nature_effect(name))
    if not eff:
        return ""
    up, down = eff
    return f" +{up} -{down}"


def format_named_entry(entry: dict[str, Any], panel: str, lang: str = "zh") -> str:
    src = entry.get("name") or entry.get("name_ja") or entry.get("key") or ""
    kind = _KIND_BY_PANEL.get(panel)
    name = repair_display_name(src, kind, lang)
    # repair_display_name returns the (Chinese) source text verbatim when the dex can't resolve the
    # entry. A handful of real metagame moves are absent from the calc-derived move roster (e.g. the
    # non-calc Poison Sting / どくばり on Qwilfish), so for a non-zh workbook fall back to the panel's
    # own `key` (the source's Japanese spelling) rather than leak Chinese into a ja/en file.
    if kind and name == src and lang in ("ja", "en") and entry.get("key"):
        name = entry["key"]
    if panel == "partners":
        return f"{ranked_prefix(entry)}{name}"
    usage = percent_text(entry.get("percentage"))
    if panel == "natures":
        effect = nature_effect_text(entry, name)       # A/B/C/D/S letters are language-neutral
        suffix = f"{effect} ({usage})" if usage else effect
        return f"{ranked_prefix(entry)}{name}{suffix}"
    suffix = f" ({usage})" if usage else ""
    return f"{ranked_prefix(entry)}{name}{suffix}"


def format_spread_entry(entry: dict[str, Any]) -> str:
    usage = percent_text(entry.get("percentage"))
    values = [
        f"H{entry.get('hp', '')}",
        f"A{entry.get('atk', '')}",
        f"B{entry.get('def', '')}",
        f"C{entry.get('spa', '')}",
        f"D{entry.get('spd', '')}",
        f"S{entry.get('spe', '')}",
    ]
    suffix = f" ({usage})" if usage else ""
    return f"{ranked_prefix(entry)}{'/'.join(values)}{suffix}"   # compact EV notation (fits the split column)


def format_panel(entries: list[dict[str, Any]], panel: str, lang: str = "zh") -> str:
    """One newline-joined line per entry. Every entry already carries a usage-rank prefix ("1. ") via
    `ranked_prefix`, so the data sheets and the lookup panel read as ranked 1..N lists (the lookup
    splits them 1-5 / 6-10 into two columns)."""
    lines: list[str] = []
    for entry in entries or []:
        if panel == "spreads":
            lines.append(format_spread_entry(entry))
        else:
            lines.append(format_named_entry(entry, panel, lang))
    return "\n".join(lines)


def _pokemon_name(detail: dict[str, Any], ranking: dict[str, Any], lang: str) -> str:
    """Pokemon display name in `lang`. The meta rows carry authoritative localized names directly
    (pokemon = zh, pokemon_ja = ja, pokemon_en = en) — these resolve form names (Ninetales-Alola) that
    the dex stores under a longer descriptor, so prefer them and only fall back to the dex / source text."""
    field = {"en": "pokemon_en", "ja": "pokemon_ja"}.get(lang)
    if field:
        direct = detail.get(field) or ranking.get(field)
        if direct:
            return direct
    src = detail.get("pokemon") or ranking.get("pokemon") or detail.get("slug") or ""
    return repair_display_name(src, "pokemon", lang)


def export_human_rows(season: str, fmt: str, lang: str = "zh") -> list[dict[str, str]]:
    """Per-Pokemon rows for the xlsx data sheet, keyed by stable English keys (the sheet builder maps
    them to localized headers). Names and every panel cell are rendered in `lang` via the dex; `en` is
    the always-English cross-reference column (used by zh/ja workbooks, dropped by the en workbook)."""
    rows: list[dict[str, str]] = []
    ranking_by_slug = {row.get("slug"): row for row in load_ranking(season, fmt)}
    details = sorted(load_details(season, fmt), key=lambda row: row.get("rank") or 999999)
    for detail in details:
        ranking = ranking_by_slug.get(detail.get("slug"), {})
        rank = detail.get("rank") or ranking.get("rank")
        name = _pokemon_name(detail, ranking, lang)
        en = detail.get("pokemon_en") or ranking.get("pokemon_en") or ""
        panels = detail.get("panels", {})
        rows.append(
            {
                "rank": rank,
                "slug": detail.get("slug") or ranking.get("slug") or "",
                "name": name,
                "en": en,
                "moves": format_panel(panels.get("moves", []), "moves", lang),
                "items": format_panel(panels.get("items", []), "items", lang),
                "abilities": format_panel(panels.get("abilities", []), "abilities", lang),
                "natures": format_panel(panels.get("natures", []), "natures", lang),
                "partners": format_panel(panels.get("partners", []), "partners", lang),
                "spreads": format_panel(panels.get("spreads", []), "spreads", lang),
            }
        )
    return rows


def default_excel_path(season: str, lang: str, output_dir: Path | None = None) -> Path:
    """One workbook per language: `<season>_<date>_<lang>.xlsx` (zh / ja / en)."""
    export_date = datetime.now().strftime("%Y%m%d")
    return (output_dir or Path.cwd()) / f"{season}_{export_date}_{lang}.xlsx"


_REPORT_XLSX_NAME_RE = re.compile(r"^M-\d+_\d{8}_(zh|ja|en)\.xlsx$")


def cleanup_report_excels(output_dir: Path) -> list[Path]:
    """Delete prior generated report workbooks in `output_dir`, identified strictly by filename.

    The export artifact name is `<season>_<YYYYMMDD>_<zh|ja|en>.xlsx`. Use that exact convention as
    the deletion boundary so stale M-3/M-4 workbook triplets disappear before a fresh export, while
    unrelated spreadsheets in the same directory are left alone.
    """
    removed: list[Path] = []
    for path in sorted(output_dir.glob("*.xlsx")):
        if path.is_file() and _REPORT_XLSX_NAME_RE.fullmatch(path.name):
            path.unlink()
            removed.append(path)
    return removed


def project_root() -> Path:
    return Path(__file__).resolve().parents[4]


# Per-language fonts chosen for DISTRIBUTION robustness — the most universally pre-installed face for
# each language's user base, so the workbook renders without substitution on a clean machine:
#   zh  Microsoft YaHei — the default Simplified-Chinese UI font on every Windows since Vista.
#   ja  Meiryo          — bundled with every Windows since Vista; full kana+kanji coverage.
#   en  Arial           — present (or substituted 1:1) on essentially every OS and Office build.
# (The old "汉仪旗黑-55S" was a commercial font absent on virtually all end-user machines → tofu.)
FONT_BY_LANG = {"zh": "Microsoft YaHei", "ja": "Meiryo", "en": "Arial"}


def _font(lang: str) -> str:
    return FONT_BY_LANG.get(lang, "Arial")


INK = "1F2937"

# Stable per-Pokemon row keys (export_human_rows emits these); ordered the way data columns appear.
# `name` is always column B (the lookup key); `en` is the English cross-reference column, present only
# in the zh/ja workbooks (the en workbook's `name` already IS English, so the cross-ref is dropped).
_ROW_ORDER = ["rank", "name", "en", "moves", "items", "abilities", "natures", "partners", "spreads"]
COL_WIDTH = {"rank": 8, "name": 20, "en": 20, "moves": 26, "items": 24,
             "abilities": 24, "natures": 28, "partners": 24, "spreads": 42}
# Per-language width overrides for the list columns the shared base wraps. openpyxl's width unit (default-
# font '0'-glyphs) UNDER-estimates how wide MS Excel renders these in a CJK font (Meiryo), so an estimated
# width still wraps in real Excel. The ja values are therefore the ones measured by hand IN Excel (column
# dragged until each entry fits); パートナー is deliberately left narrow — its long form-name entries
# (ケンタロス（パルデアのすがた・ウォーターしゅ）) are allowed to wrap rather than blow the column out. zh/en
# columns render at/under their estimate, so those stay estimate-sized. Cosmetic — facts-only report.
COL_WIDTH_LANG = {
    "zh": {"items": 30, "partners": 36},
    "ja": {"en": 13, "moves": 28.44, "items": 34.11, "abilities": 28.78, "partners": 25.66},
    "en": {"moves": 30, "items": 28},
}


def _col_width(key: str, lang: str) -> float:
    return COL_WIDTH_LANG.get(lang, {}).get(key, COL_WIDTH[key])

# Per-language xlsx vocabulary. Each workbook is single-language but still carries BOTH single and
# double data (side by side in the report sheet), so only labels + name rendering + dropdown values
# change per language — never the structure.
XLSX_LANG: dict[str, dict[str, Any]] = {
    "zh": {
        "cross_ref": True,
        "labels": {"rank": "排名", "name": "中文名", "en": "英文名", "moves": "招式", "items": "道具",
                   "abilities": "特性", "natures": "性格", "partners": "队友", "spreads": "SP 分配"},
        "sheets": {"lookup": "检索", "single": "单打", "double": "双打", "report": "更新报告"},
        "search_title": "宝可梦检索（单 / 双 对照）", "search_prompt": "输入中文名 →", "item_col": "项目",
        "report_title": {"single": "单打更新报告", "double": "双打更新报告"},
        "baseline": "对比基线", "current": "当前", "none": "（无）", "yes": "是", "unranked": "榜外",
        "changes": "本期变化", "chg_rank": "名次", "chg_in": "进榜", "chg_out": "跌出", "chg_none": "本期无显著变化",
        "tables": {
            "rank_moves": ("名次显著变动", ["宝可梦", "旧名次", "新名次", "Δ", "档"]),
            "tier_change": ("档位变化", ["宝可梦", "原档", "现档", "名次"]),
            "config": ("配置显著变动", ["宝可梦(名次)", "面板/项目", "旧%", "新%", "Δpp", "档"]),
        },
    },
    "ja": {
        "cross_ref": True,
        "labels": {"rank": "順位", "name": "日本語名", "en": "英語名", "moves": "わざ", "items": "もちもの",
                   "abilities": "とくせい", "natures": "せいかく", "partners": "パートナー", "spreads": "SP配分"},
        "sheets": {"lookup": "検索", "single": "シングル", "double": "ダブル", "report": "更新レポート"},
        "search_title": "ポケモン検索（シングル / ダブル 対照）", "search_prompt": "名前を入力 →", "item_col": "項目",
        "report_title": {"single": "シングル更新レポート", "double": "ダブル更新レポート"},
        "baseline": "比較基準", "current": "現在", "none": "（なし）", "yes": "はい", "unranked": "圏外",
        "changes": "今回の変化", "chg_rank": "順位", "chg_in": "ランクイン", "chg_out": "ランク外", "chg_none": "今回は大きな変化なし",
        "tables": {
            "rank_moves": ("順位の大きな変動", ["ポケモン", "旧順位", "新順位", "Δ", "段"]),
            "tier_change": ("使用率帯の変化", ["ポケモン", "旧帯", "新帯", "順位"]),
            "config": ("構成の大きな変動", ["ポケモン(順位)", "パネル/項目", "旧%", "新%", "Δpp", "段"]),
        },
    },
    "en": {
        "cross_ref": False,
        "labels": {"rank": "Rank", "name": "Name", "en": "English", "moves": "Moves", "items": "Items",
                   "abilities": "Abilities", "natures": "Natures", "partners": "Partners", "spreads": "Spreads"},
        "sheets": {"lookup": "Lookup", "single": "Singles", "double": "Doubles", "report": "Update Report"},
        "search_title": "Pokémon Lookup (Singles / Doubles)", "search_prompt": "Enter name →", "item_col": "Field",
        "report_title": {"single": "Singles Update Report", "double": "Doubles Update Report"},
        "baseline": "Baseline", "current": "Current", "none": "(none)", "yes": "yes", "unranked": "Unranked",
        "changes": "This update", "chg_rank": "Rank", "chg_in": "In", "chg_out": "Out", "chg_none": "No significant change this update",
        "tables": {
            "rank_moves": ("Major rank shifts", ["Pokémon", "Old", "New", "Δ", "Tier"]),
            "tier_change": ("Tier changes", ["Pokémon", "From", "To", "Rank"]),
            "config": ("Major config shifts", ["Pokémon (rank)", "Panel/item", "Old%", "New%", "Δpp", "Tier"]),
        },
    },
}


# Usage-tier reference panel (shipped to the right of the update report, cols P:Y). Verbatim
# maintainer-provided prose per language; the S/A/B/C/D letters + per-tier significance thresholds are
# composed onto each block at render time from the report's own `thresholds` block (so the panel can
# never drift from report_config.json). One merged cell per tier — see _write_tier_panel.
TIER_DEFS: dict[str, dict[str, Any]] = {
    "zh": {
        "intro_title": "使用率档位说明",
        "intro": (
            "本报告根据宝可梦在当前环境中的使用率排名，将其划分为五个档位：1-10、11-30、31-60、61-100、101+。"
            "档位主要用于反映宝可梦在当前 meta 中的出场频率、构筑价值、针对必要性与环境影响力。需要注意的是，"
            "使用率档位并不完全等同于强度排名，部分低使用率宝可梦仍可能在特定构筑、特定对局或特定战术体系中发挥重要作用。"),
        "thr_prefix": "大幅变化判定：", "thr_rank": "名次变动 ≥{r} 位",
        "thr_pp": "，配置变动 ≥{p}pp", "thr_pp_null": "，配置变动仅在名次大幅变动时记录（≥{trig}pp）",
        "tiers": [
            ("S", "1-10", "核心环境宝可梦",
             "该档位代表当前环境中最主流、最具影响力的宝可梦。它们通常具备极高的泛用性、稳定的对局表现，或对队伍构筑具有明显的支撑作用。\n"
             "进入这一档的宝可梦往往是环境定义者。无论是否使用它们，队伍构筑时都必须考虑如何应对这些宝可梦。它们的常见配置、道具、招式与队友组合，通常会直接影响整个环境的攻防节奏。"),
            ("A", "11-30", "主流强势宝可梦",
             "该档位的宝可梦同样具有较高使用率和较强竞技价值，是当前环境中的重要组成部分。相比 1-10 档，它们可能在泛用性、稳定性或构筑适配面上略逊一筹，但依然是常见且可靠的选择。\n"
             "这一档中的宝可梦通常具备明确的队伍职能，例如输出核心、联防轴、速度控制、强化清场、反制特定热门宝可梦等。它们在多数队伍中都有较高的实战价值，也是分析 meta 时需要重点关注的对象。"),
            ("B", "31-60", "常见功能型与体系型宝可梦",
             "该档位的宝可梦使用率中等，通常不属于最主流核心，但在特定体系、特定队伍结构或特定对局中具有稳定价值。\n"
             "这一档的宝可梦往往具有较明确的定位，例如针对热门威胁、补足队伍抗性、提供特定辅助功能，或作为某些战术体系的关键成员。它们未必适合所有队伍，但在合适的构筑中能够发挥较高效率。"),
            ("C", "61-100", "环境边缘可用宝可梦",
             "该档位的宝可梦处于环境边缘位置，使用率相对较低，但仍具有一定实战意义。它们可能受限于泛用性不足、对热门宝可梦表现不稳定、竞争者过强，或需要较高构筑成本才能发挥作用。\n"
             "这一档中的宝可梦通常更依赖队伍支持和玩家理解。它们适合用于特定反制、冷门战术、针对性构筑，或作为出其不意的选择。虽然不是主流选择，但不能简单视为不可用。"),
            ("D", "101+", "冷门与特殊用途宝可梦",
             "该档位代表使用率较低、环境存在感较弱的宝可梦。它们通常不属于当前主流构筑的常规选择，可能存在明显短板，例如数值不足、定位被替代、对主流环境适应性差，或发挥条件较苛刻。\n"
             "不过，低使用率并不等于没有价值。部分宝可梦可能在特定规则、特定队伍、特定对局，或玩家高度熟悉的操作体系中发挥作用。分析这一档时，应重点关注其独特性，而不是仅凭使用率否定其可能性。"),
        ],
    },
    "ja": {
        "intro_title": "使用率帯の定義",
        "intro": (
            "本レポートでは、ポケモンの使用率順位をもとに、1-10位、11-30位、31-60位、61-100位、101位以下の5つの使用率帯に分類する。"
            "各使用率帯は、現在の環境における採用頻度、構築への組み込みやすさ、対策優先度、そして環境全体への影響度を示すための目安である。"
            "ただし、使用率はそのまま強さの序列を意味するものではない。使用率が低いポケモンであっても、特定の構築、特定の対面、あるいは特定のプレイスタイルにおいて高い実戦価値を持つ場合がある。"),
        "thr_prefix": "大きな変動の基準：", "thr_rank": "順位変動 ≥{r}位",
        "thr_pp": "、構成変動 ≥{p}pp", "thr_pp_null": "、構成変動は順位が大きく動いた場合のみ（≥{trig}pp）",
        "tiers": [
            ("S", "1-10位", "環境の中心となるポケモン",
             "この帯に入るポケモンは、現在の環境を代表する存在である。採用率が非常に高く、汎用性、安定感、構築への貢献度のいずれか、または複数において高い水準を持つ。\n"
             "これらのポケモンは、環境そのものを形作る存在になりやすい。採用するかどうかにかかわらず、構築段階で必ず意識すべき対象であり、型、持ち物、技構成、並びの傾向が環境全体の流れに大きく影響する。"),
            ("A", "11-30位", "環境上位の有力ポケモン",
             "この帯のポケモンも、現在の環境で高い存在感を持つ有力な選択肢である。1-10位のポケモンと比べると、汎用性や安定感、採用できる構築の幅でやや差がある場合もあるが、十分に主流と呼べる位置にいる。\n"
             "主な役割としては、攻撃の軸、受けやサイクルの要員、積み展開、素早さ操作、特定の上位ポケモンへの対策枠などが挙げられる。環境を分析するうえで、継続的に確認しておくべき重要な層である。"),
            ("B", "31-60位", "役割が明確な中堅ポケモン",
             "この帯のポケモンは、使用率としては中程度であり、どの構築にも入る汎用枠というよりは、明確な役割を持って採用されることが多い。\n"
             "特定の相手への対策、タイプ補完、補助技によるサポート、特定の展開構築との相性、あるいは構築全体の穴埋めとして価値を発揮する。採用にはある程度の目的意識が必要だが、噛み合う構築では安定した働きが期待できる。"),
            ("C", "61-100位", "環境内に残るピンポイント採用枠",
             "この帯のポケモンは、主流とは言いにくいものの、現在の環境で一定の採用実績を持つ層である。汎用性の低さ、上位ポケモンとの競合、環境上位への不安定さ、構築上の負担などが使用率を抑えている場合が多い。\n"
             "一方で、明確な仮想敵がいる、特定の並びに強い、奇襲性がある、または独自の役割を持つといった理由で採用されることがある。広く安定する選択肢ではないが、環境理解が深い構築では十分に候補になり得る。"),
            ("D", "101位以下", "低使用率・特殊用途のポケモン",
             "101位以下のポケモンは、現在の環境における採用率が低く、一般的な構築では見かける機会が少ない。数値不足、役割の狭さ、上位互換に近い競合の存在、環境上位への不利、または活躍条件の厳しさが主な要因となる。\n"
             "ただし、使用率が低いことは、そのまま実戦価値がないことを意味しない。独自のタイプ、特性、技、奇襲性能、または特定構築との相性によって、限定的ながら明確な役割を持つ場合がある。この帯のポケモンは、使用率そのものよりも「何ができるか」を基準に評価するのが適切である。"),
        ],
    },
    "en": {
        "intro_title": "Usage Tier Definitions",
        "intro": (
            "This report groups Pokémon by usage ranking into five tiers: 1-10, 11-30, 31-60, 61-100, and 101+. "
            "These tiers are intended to describe how prominent each Pokémon is in the current metagame, including "
            "its consistency, splashability, team-building impact, and how urgently it needs to be accounted for in "
            "preparation. Usage ranking should not be read as a strict power ranking. Some Pokémon with lower usage "
            "may still be highly effective in specific archetypes, matchups, or player-dependent strategies."),
        "thr_prefix": "Significance: ", "thr_rank": "rank shift ≥{r}",
        "thr_pp": ", config shift ≥{p}pp", "thr_pp_null": ", config only when rank moves significantly (≥{trig}pp)",
        "tiers": [
            ("S", "1-10", "Metagame-Defining Staples",
             "Pokémon in this range are the central pieces of the current metagame. They are highly consistent, broadly applicable, and frequently shape how teams are built.\n"
             "These Pokémon often define the pace and structure of the format. Whether a team uses them or not, it usually needs a clear plan for handling them. Their common sets, items, moves, and partner choices tend to influence the broader direction of the metagame."),
            ("A", "11-30", "Major Meta Threats",
             "Pokémon in this tier are still firmly established as strong and common choices. Compared with the top 10, they may be slightly less universal, slightly more team-dependent, or easier to replace in certain structures, but they remain major parts of the format.\n"
             "They often serve important competitive roles such as primary attackers, defensive pivots, setup sweepers, speed control options, matchup stabilizers, or checks to popular top-tier threats. These Pokémon should be treated as regular and relevant components of the metagame."),
            ("B", "31-60", "Established Role Players",
             "Pokémon in this range have moderate usage and usually appear as role-specific picks rather than universal staples. They may not define the format, but they have clear value in the right team structure.\n"
             "Many Pokémon in this tier are chosen for specific utility, defensive coverage, offensive pressure, matchup targeting, or synergy with a particular archetype. They are not always easy to fit, but when the team context is correct, they can perform their assigned role reliably."),
            ("C", "61-100", "Fringe Meta Options",
             "Pokémon in this tier sit near the edge of regular metagame usage. They are not common enough to be considered standard picks, but they still have enough practical relevance to be worth tracking.\n"
             "These Pokémon often require more deliberate support, more precise positioning, or a clearer matchup purpose. They may be used to exploit specific trends, punish common team structures, or introduce an unexpected angle into a matchup. They are not broadly reliable, but they are not irrelevant."),
            ("D", "101+", "Low-Usage and Specialist Picks",
             "Pokémon ranked outside the top 100 have limited presence in the current metagame. They are usually held back by low consistency, narrow matchups, heavy team-building costs, competition from stronger alternatives, or difficulty fitting into mainstream structures.\n"
             "That said, low usage does not automatically mean a Pokémon is unviable. Some may offer a unique trait, surprise factor, specific counterplay option, or synergy within a specialized team. These Pokémon should be evaluated primarily by their distinct role rather than their raw usage alone."),
        ],
    },
}


def _tier_threshold_text(lang: str, letter: str, thresholds: dict[str, Any]) -> str:
    """The per-tier significance line, composed from the report's own thresholds so the reference panel
    never drifts from report_config.json. '' when thresholds are unavailable."""
    if not thresholds:
        return ""
    td = TIER_DEFS[lang]
    rmt = (thresholds.get("rank_move_threshold") or {}).get(letter)
    ppt = (thresholds.get("config_pp_threshold") or {}).get(letter)
    trig = thresholds.get("rank_triggered_pp_threshold", 20)
    if rmt is None:
        return ""
    text = td["thr_prefix"] + td["thr_rank"].format(r=rmt)
    text += td["thr_pp"].format(p=ppt) if ppt is not None else td["thr_pp_null"].format(trig=trig)
    return text


def _data_keys(lang: str) -> list[str]:
    """Ordered stable row keys for a language's data sheet (drops the `en` cross-ref when cross_ref off)."""
    cross = XLSX_LANG[lang]["cross_ref"]
    return [k for k in _ROW_ORDER if k != "en" or cross]


# panels whose lists run up to 10 entries — the lookup shows them as 2 columns (1-5 | 6-10), fed by two
# hidden half-columns on each data sheet so the panel fits the screen width instead of one tall stack.
SPLIT_PANELS = ("moves", "items", "natures", "spreads", "partners")


def _data_col_layout(lang: str) -> dict[str, Any]:
    """Column indices for a data sheet: the visible keys, then a HIDDEN changes column, then a HIDDEN
    pair of first/second-half columns per splittable panel. Shared by the data-sheet writer and the
    lookup so both agree on where each value lives."""
    keys = _data_keys(lang)
    n = len(keys)
    halves: dict[str, tuple[int, int]] = {}
    c = n + 2
    for p in SPLIT_PANELS:
        if p in keys:
            halves[p] = (c, c + 1)
            c += 2
    return {"keys": keys, "changes": n + 1, "halves": halves, "last": c - 1}


def _halves(text: str) -> tuple[str, str]:
    """Split a newline-joined list into (first half, second half) — for 10 entries that is 1-5 / 6-10."""
    if not text:
        return "", ""
    lines = text.split("\n")
    h = -(-len(lines) // 2)
    return "\n".join(lines[:h]), "\n".join(lines[h:])


def _style_data_sheet(ws, rows: list[dict[str, str]], lang: str) -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    cfg = XLSX_LANG[lang]
    font = _font(lang)
    layout = _data_col_layout(lang)
    keys = layout["keys"]
    labels = cfg["labels"]
    n_vis = len(keys)
    ws.sheet_view.showGridLines = False
    # header: visible keys, then hidden changes + per-panel half columns (their labels are never shown)
    header = [labels[k] for k in keys] + [cfg["changes"]]
    for p in layout["halves"]:
        header += [f"{labels[p]}·1", f"{labels[p]}·2"]
    ws.append(header)
    for row in rows:
        vals = [row.get(k, "") for k in keys] + [row.get("changes") or cfg["chg_none"]]
        for p in layout["halves"]:
            a, b = _halves(row.get(p, ""))
            vals += [a, b]
        ws.append(vals)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(n_vis)}{ws.max_row}"        # filter the visible columns only
    for idx, key in enumerate(keys, 1):
        ws.column_dimensions[get_column_letter(idx)].width = _col_width(key, lang)
    for idx in range(n_vis + 1, layout["last"] + 1):                          # hide changes + half columns
        ws.column_dimensions[get_column_letter(idx)].hidden = True
    header_fill = PatternFill("solid", fgColor="D9EAF7")
    band_fill = PatternFill("solid", fgColor="F7FBFD")
    rule = Side(style="thin", color="D6E3EA")                                 # internal horizontal rule
    last_row = ws.max_row
    for row_idx, row in enumerate(ws.iter_rows(), 1):
        for cell in row:
            hidden = cell.column > n_vis
            cell.font = Font(name=font, size=10, bold=(row_idx == 1), color=INK)
            # internal rule on every row + the same thin line wrapped around the visible table's outer edge
            cell.border = Border(
                bottom=rule,
                top=rule if row_idx == 1 else None,
                left=rule if cell.column == 1 else None,
                right=rule if cell.column == n_vis else None,
            )
            # hidden helper cells must NOT wrap, else their multi-line text would inflate the data row
            # height; INDEX still reads each one's full value (newlines included) for the lookup sheet.
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=not hidden)
            if row_idx == 1:
                cell.fill = header_fill
            elif row_idx % 2 == 0:
                cell.fill = band_fill
    ws.row_dimensions[1].height = 22


def _load_report(report_dir: Path | None, season: str, fmt: str) -> dict[str, Any] | None:
    if not report_dir:
        return None
    path = report_dir / f"report_{season}_{fmt}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _disp_width(s: str) -> int:
    """Display width: a CJK glyph is ~2 Excel width-units, a Latin glyph ~1."""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in str(s))


def _wrapped_lines(value: Any, col_width: float) -> int:
    """How many wrapped lines `value` needs in a column of `col_width` Excel units (honours explicit
    newlines). Used to size row heights so a wrapped cell shows in full instead of clipping."""
    cap = max(4.0, (col_width or 8) * 0.92)          # usable width per line (slight safety margin)
    lines = 0
    for part in str(value).split("\n"):
        lines += max(1, -(-_disp_width(part) // int(cap)))
    return lines


def _write_report_table(ws, top: int, left: int, title: str, headers: list[str],
                        rows: list[list[Any]], none_label: str, font: str,
                        links: list[str | None] | None = None) -> int:
    """Render one titled table starting at (top,left); return the next free row. Text cells wrap and the
    row height is sized to the tallest cell, so every value shows in full (no clipping). When `links` is
    given, row r's first cell becomes an internal hyperlink to links[r] (styled, not the ugly default)."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    widths = [ws.column_dimensions[get_column_letter(left + j)].width or 8 for j in range(len(headers))]
    tcell = ws.cell(row=top, column=left, value=f"{title} ({len(rows)})")
    tcell.font = Font(name=font, size=11, bold=True, color="0B5394")
    top += 1
    for j, h in enumerate(headers):
        c = ws.cell(row=top, column=left + j, value=h)
        c.font = Font(name=font, size=10, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="6FA8DC")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    top += 1
    if not rows:
        ws.cell(row=top, column=left, value=none_label).font = Font(name=font, size=10, italic=True, color="888888")
        return top + 2
    for r, rowvals in enumerate(rows):
        band = (r % 2 == 1)
        target = links[r] if links and r < len(links) else None
        max_lines = 1
        for j, v in enumerate(rowvals):
            c = ws.cell(row=top + r, column=left + j, value=v)
            linked = (j == 0 and target)
            # a linked name is a tasteful underlined blue (not Excel's default purple-on-visit style);
            # the first two columns hold name / panel·item text (left, wrapped), numeric columns center.
            c.font = Font(name=font, size=10, color="1A6BB5" if linked else INK, underline="single" if linked else None)
            c.alignment = Alignment(horizontal="left" if j <= 1 else "center", vertical="center",
                                    wrap_text=True)
            if linked:
                c.hyperlink = target
            if band:
                c.fill = PatternFill("solid", fgColor="F2F7FB")
            max_lines = max(max_lines, _wrapped_lines(v, widths[j]))
        ws.row_dimensions[top + r].height = max(16.0, max_lines * 15.0 + 3.0)
    return top + len(rows) + 2


def _write_report_block(ws, top: int, left: int, title: str, rep: dict[str, Any], lang: str,
                        name_by_slug: dict[str, str], data_sheet: str = "",
                        row_by_slug: dict[str, int] | None = None) -> int:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    cfg = XLSX_LANG[lang]
    font = _font(lang)
    tbl, labels, none_label, unranked = cfg["tables"], cfg["labels"], cfg["none"], cfg["unranked"]
    row_by_slug = row_by_slug or {}

    def pname(r):                                        # localize the report's Pokemon name by slug
        s = r.get("slug")
        if s and name_by_slug.get(s):
            return name_by_slug[s]
        return repair_display_name(r.get("name") or s or "", "pokemon", lang)

    def jump(r):                                         # internal link to the Pokemon's data-sheet row
        row = row_by_slug.get(r.get("slug"))             # absent for a Pokemon that dropped out of the data
        return f"#'{data_sheet}'!A{row}" if (row and data_sheet) else None

    ws.merge_cells(start_row=top, start_column=left, end_row=top, end_column=left + 6)
    head = ws.cell(row=top, column=left, value=title)
    head.font = Font(name=font, size=13, bold=True, color="FFFFFF")
    head.fill = PatternFill("solid", fgColor="0B5394")
    head.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[top].height = 26               # tall enough for the size-13 title (ja was clipping)
    ws.cell(row=top + 1, column=left,
            value=f"{cfg['baseline']} {rep.get('baseline', {}).get('kind', '?')}　{cfg['current']} {str(rep.get('current', {}).get('details_updated_at', '?'))[:10]}").font = Font(name=font, size=9, italic=True, color="666666")
    top += 3
    rm = [[pname(r), r["old"], r["new"], ("↑" if r["delta"] > 0 else "↓") + str(abs(r["delta"])), r["tier"]] for r in rep.get("rank_moves", [])]
    rm_links = [jump(r) for r in rep.get("rank_moves", [])]
    top = _write_report_table(ws, top, left, *tbl["rank_moves"], rm, none_label, font, rm_links)
    # Tier-change table merges the old new-entries + dropped lists into one "from -> to" view: an entry
    # rises from 榜外/unranked into its current tier; a drop falls from its old tier back to 榜外. New
    # entries link to their data-sheet row; a dropped Pokemon is no longer in the data, so no link.
    tc = ([[pname(r), unranked, r["tier"], r["rank"]] for r in rep.get("new_entries", [])]
          + [[pname(r), r["tier"], unranked, r["old"]] for r in rep.get("dropped", [])])
    tc_links = [jump(r) for r in rep.get("new_entries", [])] + [None] * len(rep.get("dropped", []))
    top = _write_report_table(ws, top, left, *tbl["tier_change"], tc, none_label, font, tc_links)
    # panel + item merged into one column ("Moves/Close Combat") to keep the table narrow. The panel
    # word is localized via the data-column labels; the item via the dex in the workbook's language.
    cc = [[f"{pname(c)}({c['rank']})",
           f"{labels.get(c['panel'], c['panel'])}/{repair_display_name(c['item'], _KIND_BY_PANEL.get(c['panel']), lang)}",
           c["old_pct"], c["new_pct"], f"{c['delta']:+.1f}", c["tier"]]
          for c in rep.get("config_changes", [])]
    top = _write_report_table(ws, top, left, *tbl["config"], cc, none_label, font)
    return top


def _change_summary_by_slug(rep: dict[str, Any], lang: str) -> dict[str, str]:
    """slug -> a localized multiline "what changed this update" summary, so the lookup sheet can refresh
    the SAME report facts for whichever Pokemon is selected. Empty dict when there is no report."""
    cfg = XLSX_LANG[lang]
    out: dict[str, list[str]] = {}

    def add(slug: str, line: str) -> None:
        if slug:
            out.setdefault(slug, []).append(line)

    for r in rep.get("rank_moves", []):
        arrow = "↑" if r["delta"] > 0 else "↓"
        add(r["slug"], f"{cfg['chg_rank']} {r['old']}→{r['new']} ({arrow}{abs(r['delta'])}, {r['tier']})")
    for r in rep.get("new_entries", []):
        add(r["slug"], f"{cfg['chg_in']} → {r['tier']} (#{r['rank']})")
    for r in rep.get("dropped", []):
        add(r["slug"], f"{cfg['chg_out']} {r['tier']} → {cfg['unranked']} (#{r['old']})")
    for c in rep.get("config_changes", []):
        panel = cfg["labels"].get(c["panel"], c["panel"])
        item = repair_display_name(c["item"], _KIND_BY_PANEL.get(c["panel"]), lang)
        add(c["slug"], f"{panel}/{item}  {c['old_pct']}→{c['new_pct']}% ({c['delta']:+.1f})")
    return {s: "\n".join(v) for s, v in out.items()}


def _write_tier_panel(ws, top: int, left: int, right: int, lang: str, thresholds: dict[str, Any]) -> None:
    """Usage-tier reference panel (cols left..right) sitting to the RIGHT of the report blocks. Each
    block (intro + one per S/A/B/C/D tier) is a single merged cell, but merged VERTICALLY across as many
    normal-height rows as its text needs — so it never forces one giant row that would stretch the report
    tables sharing those rows. Existing row heights (set by the report tables) are respected; only rows
    the panel introduces past the tables get a height. Thresholds come from the report so the panel can
    never drift from report_config.json."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    defs = TIER_DEFS[lang]
    font = _font(lang)
    span_w = sum((ws.column_dimensions[get_column_letter(c)].width or 9) for c in range(left, right + 1))
    thin = Border(*(Side(style="thin", color="C9D6DF"),) * 4)
    sep = "  " if lang == "en" else "　"
    default_h = 15.0
    row = top

    def block(text: str, *, fill: str, color: str, size: int, bold: bool, align: str = "top") -> None:
        nonlocal row
        need = (_wrapped_lines(text, span_w) * 15.0) + 6.0     # points the text needs
        start = row
        acc = 0.0
        while acc < need:                                      # span enough rows to fit, respecting
            h = ws.row_dimensions[row].height                 # any height the report tables already set
            if h is None:
                h = default_h
                ws.row_dimensions[row].height = default_h
            acc += h
            row += 1
        ws.merge_cells(start_row=start, start_column=left, end_row=row - 1, end_column=right)
        c = ws.cell(row=start, column=left, value=text)
        c.font = Font(name=font, size=size, bold=bold, color=color)
        c.fill = PatternFill("solid", fgColor=fill)
        c.alignment = Alignment(horizontal="left", vertical=align, wrap_text=True)
        c.border = thin

    block(defs["intro_title"], fill="0B5394", color="FFFFFF", size=13, bold=True, align="center")
    block(defs["intro"], fill="EAF1F8", color=INK, size=10, bold=False)
    row += 1                                                  # one blank spacer row before the tiers
    tier_fills = ("FFF2CC", "FCE5CD", "D9EAD3", "D0E0E3", "EAD1DC")
    for i, (letter, rng, title, body) in enumerate(defs["tiers"]):
        thr = _tier_threshold_text(lang, letter, thresholds)
        header = f"{letter}{sep}{rng}{sep}{title}"
        text = f"{header}\n{body}" + (f"\n{thr}" if thr else "")
        block(text, fill=tier_fills[i % len(tier_fills)], color=INK, size=10, bold=False)
        row += 1                                              # a blank spacer row between tiers


def _build_lookup_sheet(wb, single_rows: list[dict[str, str]], double_rows: list[dict[str, str]],
                        lang: str) -> None:
    """Standalone interactive lookup sheet (placed FIRST). Pick a Pokemon from the dropdown; the panel
    pulls its single + double sets from the data sheets via INDEX/MATCH, and — when the update report has
    something for it — a 本期变化 row at the bottom refreshes the same change facts for that Pokemon."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.utils import get_column_letter

    cfg = XLSX_LANG[lang]
    font = _font(lang)
    sheets = cfg["sheets"]
    sheet_single, sheet_double = sheets["single"], sheets["double"]
    ws = wb.create_sheet(sheets["lookup"])
    ws.sheet_view.showGridLines = False
    thin = Border(*(Side(style="thin", color="C9D6DF"),) * 4)
    rule = Side(style="thin", color="D0DCE6")                # light horizontal rule between panel rows
    # label col A, single band B:G, gap H, double band I:N (each value cell merges across its band; the
    # split panels use two 3-col halves B:D | E:G). 14 wide so a full EV-spread line fits without wrapping.
    for col, w in (("A", 12), ("B", 14), ("C", 14), ("D", 14), ("E", 14), ("F", 14), ("G", 14),
                   ("H", 3), ("I", 14), ("J", 14), ("K", 14), ("L", 14), ("M", 14), ("N", 14), ("O", 14)):
        ws.column_dimensions[col].width = w

    ws.merge_cells("A1:G1")
    title = ws.cell(row=1, column=1, value=cfg["search_title"])
    title.font = Font(name=font, size=14, bold=True, color="0B5394")
    ws.cell(row=2, column=1, value=cfg["search_prompt"]).font = Font(name=font, size=11, bold=True, color=INK)
    ws.merge_cells("B2:G2")
    inp = ws.cell(row=2, column=2, value=(single_rows[0]["name"] if single_rows else ""))
    inp.font = Font(name=font, size=12, bold=True, color="990000")
    inp.fill = PatternFill("solid", fgColor="FFF2CC")
    inp.border = thin
    inp.alignment = Alignment(horizontal="center", vertical="center")

    # dropdown source: union of names in this workbook's language, hidden far-right in column AB (28)
    names = list(dict.fromkeys([r["name"] for r in single_rows] + [r["name"] for r in double_rows]))
    for i, nm in enumerate(names, start=1):
        ws.cell(row=i, column=28, value=nm)
    ws.column_dimensions["AB"].hidden = True
    dv = DataValidation(type="list", formula1=f"'{sheets['lookup']}'!$AB$1:$AB${max(len(names), 1)}", allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(inp)

    # band header
    ws.cell(row=4, column=1, value=cfg["item_col"]).font = Font(name=font, size=11, bold=True, color="FFFFFF")
    ws.cell(row=4, column=1).fill = PatternFill("solid", fgColor="6FA8DC")
    ws.cell(row=4, column=1).alignment = Alignment(horizontal="center", vertical="center")
    for band, lbl in ((2, sheet_single), (9, sheet_double)):
        ws.merge_cells(start_row=4, start_column=band, end_row=4, end_column=band + 5)
        h = ws.cell(row=4, column=band, value=lbl)
        h.font = Font(name=font, size=11, bold=True, color="FFFFFF")
        h.fill = PatternFill("solid", fgColor="6FA8DC")
        h.alignment = Alignment(horizontal="center", vertical="center")

    # 项目 rows pull each field from the matching data-sheet row. The up-to-10-entry panels are shown as
    # TWO columns (1-5 | 6-10) sourced from the hidden half-columns so the panel uses the width and stays
    # on screen; a trailing 本期变化 row pulls the hidden change column so report facts refresh with the
    # selection.
    layout = _data_col_layout(lang)
    keys = layout["keys"]
    col_of = {k: get_column_letter(i) for i, k in enumerate(keys, 1)}
    changes_col = get_column_letter(layout["changes"])
    half_col = {p: (get_column_letter(a), get_column_letter(b)) for p, (a, b) in layout["halves"].items()}
    order = [k for k in ("rank", "en", "moves", "items", "abilities", "natures", "spreads", "partners") if k in col_of]

    # Clean styling: the two columns get a genuine 95%-light tint (Excel formula base*0.05 + 255*0.95) of
    # olive-green (#6B8E23 -> F8F9F4) and red (#C00000 -> FCF2F2), distinct from the blue-grey label column;
    # rank/en/abilities rows are plain white; the change row matches the search box. Panel rows are closed
    # by one continuous horizontal rule, and each set block gets a thin outer frame (same colour as the rule).
    LEFT_TINT, RIGHT_TINT, FULL_FILL, CHG_FILL, LABEL_FILL = "F8F9F4", "FCF2F2", "FFFFFF", "FFF2CC", "E8EEF5"

    def value_cell(r: int, c0: int, c1: int, sheet: str, src_col: str, fill: str) -> None:
        ws.merge_cells(start_row=r, start_column=c0, end_row=r, end_column=c1)
        f = f"=IFERROR(INDEX('{sheet}'!{src_col}:{src_col},MATCH($B$2,'{sheet}'!$B:$B,0)),\"—\")"
        c = ws.cell(row=r, column=c0, value=f)
        c.font = Font(name=font, size=10, color=INK)
        c.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
        c.fill = PatternFill("solid", fgColor=fill)

    def hrule(r: int) -> None:                                 # one continuous bottom line across the row
        for cc in (*range(1, 8), *range(9, 15)):
            ws.cell(row=r, column=cc).border = Border(bottom=rule)

    hrule(4)                                                   # close the band-header row
    specs = []                                                 # (label, key | None for changes, height, split)
    for k in order:
        if k in layout["halves"]:
            specs.append((cfg["labels"][k], k, 86, True))      # 2 columns of 5 entries
        else:
            specs.append((cfg["labels"][k], k, 56 if k == "abilities" else 18, False))
    specs.append((cfg["changes"], None, 72, False))
    for r, (label, key, height, split) in enumerate(specs, start=5):
        lc = ws.cell(row=r, column=1, value=label)
        lc.font = Font(name=font, size=10, bold=True, color=INK)
        lc.fill = PatternFill("solid", fgColor=LABEL_FILL)
        lc.alignment = Alignment(horizontal="center", vertical="top")
        full_fill = CHG_FILL if key is None else FULL_FILL
        for band, sheet in ((2, sheet_single), (9, sheet_double)):
            if split:
                a_col, b_col = half_col[key]
                value_cell(r, band, band + 2, sheet, a_col, LEFT_TINT)      # entries 1-5
                value_cell(r, band + 3, band + 5, sheet, b_col, RIGHT_TINT)  # entries 6-10
            else:
                value_cell(r, band, band + 5, sheet, changes_col if key is None else col_of[key], full_fill)
        ws.row_dimensions[r].height = height
        hrule(r)

    # outer frame around each set block (single B:G, double I:N), same thin line as the internal rules,
    # combined with whatever border each perimeter cell already carries so the bottom rules survive.
    frame = Side(style="thin", color="D0DCE6")
    last = 4 + len(specs)                                       # band header row 4 .. last spec row

    def add_side(r: int, c: int, **sides: Side) -> None:
        b = ws.cell(row=r, column=c).border
        ws.cell(row=r, column=c).border = Border(
            left=sides.get("left", b.left), right=sides.get("right", b.right),
            top=sides.get("top", b.top), bottom=sides.get("bottom", b.bottom))

    for left_col, right_col in ((2, 7), (9, 14)):
        for r in range(4, last + 1):
            add_side(r, left_col, left=frame)
            add_side(r, right_col, right=frame)
        for c in range(left_col, right_col + 1):
            add_side(4, c, top=frame)
            add_side(last, c, bottom=frame)


def _build_report_sheet(wb, season: str, reports: dict[str, Any], lang: str) -> None:
    """Update-report sheet: the single + double report blocks at the TOP (the lookup now lives on its own
    sheet), with the usage-tier reference panel to their right (cols Q:Z, vertically merged so its long
    prose never stretches the report rows)."""
    cfg = XLSX_LANG[lang]
    sheets = cfg["sheets"]
    ws = wb.create_sheet(sheets["report"])
    ws.sheet_view.showGridLines = False
    # column bands: single report A-G (1-7), gap H, double report I-O (9-15), gap P, tier panel Q-Z.
    for col, w in (("A", 22), ("B", 22), ("C", 8), ("D", 8), ("E", 8), ("F", 6), ("G", 9),
                   ("H", 3), ("I", 22), ("J", 22), ("K", 8), ("L", 8), ("M", 8), ("N", 6), ("O", 9),
                   ("P", 3)):
        ws.column_dimensions[col].width = w
    for col in ("Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"):   # tier panel band (10 cols)
        ws.column_dimensions[col].width = 9.5

    # slug -> localized name (report rows carry slug + a zh name; render in this language and resolve form
    # names), and slug -> its row on the data sheet (sorted by rank, header row 1) for jump links.
    name_by_slug: dict[str, str] = {}
    row_by_slug: dict[str, dict[str, int]] = {"single": {}, "double": {}}
    for f in ("single", "double"):
        rk = {r.get("slug"): r for r in load_ranking(season, f)}
        ordered = sorted(load_details(season, f), key=lambda r: r.get("rank") or 999999)
        for i, d in enumerate(ordered, start=2):              # data-sheet row = header(1) + position
            s = d.get("slug")
            if not s:
                continue
            row_by_slug[f][s] = i
            if s not in name_by_slug:
                name_by_slug[s] = _pokemon_name(d, rk.get(s, {}), lang)
    top = 1
    rep_s, rep_d = reports.get("single"), reports.get("double")
    if rep_s:
        _write_report_block(ws, top, 1, cfg["report_title"]["single"], rep_s, lang, name_by_slug,
                            cfg["sheets"]["single"], row_by_slug["single"])
    if rep_d:
        _write_report_block(ws, top, 9, cfg["report_title"]["double"], rep_d, lang, name_by_slug,
                            cfg["sheets"]["double"], row_by_slug["double"])
    thresholds = (rep_s or rep_d or {}).get("thresholds", {})
    _write_tier_panel(ws, top, 17, 26, lang, thresholds)         # Q:Z


def save_workbook(workbook, destination: Path) -> None:
    """Canonical package metadata makes identical report inputs produce identical bytes."""
    workbook.properties.created = datetime(2000, 1, 1)
    buffer = io.BytesIO()
    workbook.save(buffer)
    with zipfile.ZipFile(buffer) as source, zipfile.ZipFile(destination, 'w') as output:
        for name in sorted(source.namelist()):
            data = source.read(name)
            if name == 'docProps/core.xml':
                data = re.sub(rb'(<dcterms:(?:created|modified)[^>]*>).*?(</dcterms:(?:created|modified)>)',
                              rb'\g<1>2000-01-01T00:00:00Z\g<2>', data)
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            output.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def command_export_excel(args: argparse.Namespace) -> None:
    try:
        from openpyxl import Workbook
    except Exception as exc:
        raise SystemExit("export-excel requires openpyxl to be installed") from exc

    args.season, args.rule = resolve_season_rule(args.season, args.rule)
    output_dir = Path(args.output_dir) if args.output_dir else Path.cwd()
    output_dir.mkdir(parents=True, exist_ok=True)
    removed_old_files = cleanup_report_excels(output_dir)
    # The update report ships inside the skill's data dir (report_<season>_<fmt>.json),
    # so it is embedded by default — no --report-dir needed.
    report_dir = Path(args.report_dir) if args.report_dir else details_path(args.season, "single").parent
    reports = {fmt: _load_report(report_dir, args.season, fmt) for fmt in ("single", "double")}

    # The xlsx export is a distribution artifact: ALWAYS write all three single-language workbooks
    # (zh / ja / en), independent of --lang / env / DEFAULT_LANG. Each is self-contained — its own
    # localized data sheets, lookup page (dropdown in that language), and embedded update report.
    files: list[dict[str, Any]] = []
    for lang in ("zh", "ja", "en"):
        rows_by_fmt = {fmt: export_human_rows(args.season, fmt, lang) for fmt in ("single", "double")}
        # attach each Pokemon's report-change summary (per format) so the lookup sheet can refresh it
        for fmt in ("single", "double"):
            chg = _change_summary_by_slug(reports[fmt], lang) if reports.get(fmt) else {}
            for row in rows_by_fmt[fmt]:
                row["changes"] = chg.get(row.get("slug", ""), "")
        wb = Workbook()
        wb.remove(wb.active)
        # sheet order: lookup FIRST, then the two data sheets, then the update report
        _build_lookup_sheet(wb, rows_by_fmt["single"], rows_by_fmt["double"], lang)
        for fmt in ("single", "double"):
            ws = wb.create_sheet(XLSX_LANG[lang]["sheets"][fmt])
            _style_data_sheet(ws, rows_by_fmt[fmt], lang)
        _build_report_sheet(wb, args.season, reports, lang)
        out = default_excel_path(args.season, lang, output_dir)
        save_workbook(wb, out)
        files.append({
            "lang": lang,
            "output_file": str(out),
            "sheets": list(XLSX_LANG[lang]["sheets"].values()),
            "single_rows": len(rows_by_fmt["single"]),
            "double_rows": len(rows_by_fmt["double"]),
        })

    result = {
        "files": files,
        "removed_old_files": [str(p) for p in removed_old_files],
        "reports_embedded": [f for f, r in reports.items() if r],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


_REPORT_TABLES = [("rank_moves", "名次显著变动 / rank moves"), ("new_entries", "新进榜 / new entries"),
                  ("dropped", "掉榜 / dropped"), ("config_changes", "配置显著变动 / config changes")]


def _report_md(season: str, rule: str, reports: dict[str, Any], missing: list[str]) -> str:
    lines = [f"# {season} / {rule} — {i18n.t('report_update')}"]
    for fmt, rep in reports.items():
        lines.append(f"\n## {fmt}  ({i18n.t('report_generated')} {rep.get('generated_at', '?')})")
        for key, label in _REPORT_TABLES:
            lines.append(f"- {label}: {len(rep.get(key) or [])}")
    if missing:
        lines.append(f"\n_{i18n.t('report_missing', fmts=', '.join(missing))}_")
    return "\n".join(lines)


def command_report(args: argparse.Namespace) -> None:
    """Emit the factual update report (`report_<season>_<fmt>.json`) through the CLI — the canonical way
    to read it, so a caller never hand-builds the internal data path. Facts only (significant ranking /
    config change tables vs the previous snapshot); JSON is the source of truth, md is a count summary."""
    args.season, args.rule = resolve_season_rule(args.season, args.rule)
    fmts = ["single", "double"] if args.format == "both" else [args.format]
    reports: dict[str, Any] = {}
    missing: list[str] = []
    for fmt in fmts:
        p = CACHE_DIR / f"report_{args.season}_{fmt}.json"
        if p.exists():
            reports[fmt] = json.loads(p.read_text(encoding="utf-8"))
        else:
            missing.append(fmt)
    if not reports:
        # A report may legitimately not exist yet (never refreshed) — a graceful miss (§3), exit 0.
        _emit_meta_error(f"{args.season}/{args.format}", "not_found",
                         f"no update report for season={args.season} format={args.format}")
        raise SystemExit(0)
    if args.output == "json":
        out = {"season": args.season, "rule": args.rule, "format": args.format, "reports": reports}
        if missing:
            out["missing"] = missing
        emit(out, "json")
    else:
        print(_report_md(args.season, args.rule, reports, missing))


class _MetaWhereError(Exception):
    """A malformed boolean `--where` query (bad node/field/expr) -> §3 bad_input."""


def _meta_hay(row: dict[str, Any]) -> str:
    return normalize(" ".join(str(row.get(k, "")) for k in ("name", "name_ja", "key", "slug", "pokemon")))


def _meta_num_cmp(raw: Any, expr: Any) -> bool:
    """Compare a row's numeric field (percentage / rank) to `expr` like `>=10`, `<40`, `2`. A missing
    field never satisfies a numeric test. A bad expr raises _MetaWhereError (eager-validated with raw=None)."""
    m = re.match(r"^\s*(>=|<=|==|=|>|<)?\s*(-?\d+(?:\.\d+)?)\s*$", str(expr))
    if not m:
        raise _MetaWhereError(f"bad numeric expression: {expr!r} (use e.g. >=10 or a bare number)")
    if raw in (None, ""):
        return False
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return False
    o, t = m.group(1) or "==", float(m.group(2))
    return {">=": v >= t, "<=": v <= t, ">": v > t, "<": v < t, "==": v == t, "=": v == t}[o]


def _meta_leaf(field: str, value: Any, name_kinds):
    """Per-row predicate for ONE meta search leaf. Returns (pred(row)->bool, unresolved:dict|None).
    name/pokemon resolve through the dex alias sets ONCE (cross-language); type via dex canonical;
    usage/rank/entry_rank are numeric exprs on the panel row's percentage / ranks; panel/category exact."""
    f = str(field).lower()
    if f == "name":
        aset, _res = dex_alias_norms_rich(str(value), name_kinds)
        return ((lambda row: any(a in _meta_hay(row) for a in aset)) if aset else (lambda row: False),
                None if aset else {"field": "name", "value": str(value)})
    if f in {"pokemon", "mon"}:
        aset, _res = dex_alias_norms_rich(str(value), ("pokemon",))
        return ((lambda row: any(a in normalize(str(row.get("pokemon", ""))) for a in aset)) if aset
                else (lambda row: False), None if aset else {"field": "pokemon", "value": str(value)})
    if f == "type":
        wanted = dex_canonical_type(str(value))
        wn = normalize(wanted or str(value))
        return (lambda row: normalize(str(row.get("type", ""))) == wn), (None if wanted else {"field": "type", "value": str(value)})
    if f in {"category", "cat"}:
        cv = normalize(str(value))
        return (lambda row: normalize(str(row.get("category", ""))) == cv), None
    if f in {"usage", "percentage"}:
        _meta_num_cmp(None, value)
        return (lambda row: _meta_num_cmp(row.get("percentage"), value)), None
    if f in {"rank", "pokemon_rank"}:
        _meta_num_cmp(None, value)
        return (lambda row: _meta_num_cmp(row.get("pokemon_rank"), value)), None
    if f == "entry_rank":
        _meta_num_cmp(None, value)
        return (lambda row: _meta_num_cmp(row.get("entry_rank"), value)), None
    if f == "panel":
        pv = normalize(str(value))
        return (lambda row: normalize(str(row.get("panel", ""))) == pv), None
    raise _MetaWhereError(f"unknown condition field: {field}; expected "
                          "name/pokemon/type/category/usage/rank/entry_rank/panel")


def _compile_meta_where(node: Any, name_kinds, sink: dict[str, Any]):
    """Compile a boolean query AST into a per-row predicate (same grammar as dex `--where`): a node is a
    SINGLE-key object {and|or:[...]} / {not:node} / a leaf {<field>:<value>}. Unresolved leaves accumulate
    in `sink`."""
    if not isinstance(node, dict) or len(node) != 1:
        raise _MetaWhereError('each query node must be a single-key object: {"and"|"or":[...]}, '
                              '{"not":{...}}, or a leaf {"<field>":<value>}')
    (key, val), = node.items()
    k = str(key).lower()
    if k in {"and", "or"}:
        if not isinstance(val, list) or not val:
            raise _MetaWhereError(f'"{k}" takes a non-empty list of sub-queries')
        preds = [_compile_meta_where(ch, name_kinds, sink) for ch in val]
        return (lambda row: all(p(row) for p in preds)) if k == "and" else (lambda row: any(p(row) for p in preds))
    if k == "not":
        sub = _compile_meta_where(val, name_kinds, sink)
        return lambda row: not sub(row)
    pred, unres = _meta_leaf(key, val, name_kinds)
    if unres:
        sink["unresolved"].append(unres)
    return pred


def _meta_where_rows(args: argparse.Namespace, formats: list[str]) -> list[dict[str, Any]]:
    """The boolean `--where` path of `search`: compile the AST once, scan panel rows, return matches.
    A malformed query emits the §3 bad_input shape and exits; unresolved leaves surface on stderr
    (meta search's JSON is a bare row list, same channel as its name-correction signals)."""
    try:
        ast = json.loads(sys.stdin.read() if args.where == "-" else args.where)
    except json.JSONDecodeError as e:
        _emit_meta_error(args.where, "bad_input", f"--where must be valid JSON: {e}")
        raise SystemExit(1)
    if args.panel:
        p = norm_panel(args.panel)
        name_kinds = (_KIND_BY_PANEL[p],) if p in _KIND_BY_PANEL else ()
    else:
        name_kinds = ("move", "item", "ability", "pokemon", "nature")
    sink: dict[str, Any] = {"unresolved": []}
    try:
        pred = _compile_meta_where(ast, name_kinds, sink)
    except _MetaWhereError as e:
        _emit_meta_error(args.where, "bad_input", str(e))
        raise SystemExit(1)
    rows = [row for fmt in formats for row in iter_panel_rows(args.season, fmt, args.panel) if pred(row)]
    for u in sink["unresolved"]:
        print(f"unresolved where leaf: {u['field']}={u['value']} (filtered literally; 0 here = the keyword, not the data)",
              file=sys.stderr)
    return rows


def command_search(args: argparse.Namespace) -> None:
    args.season, args.rule = resolve_season_rule(args.season, args.rule)
    _require_data(args.season, args.format)
    _require_panel(args.panel)
    formats = ["single", "double"] if args.format == "both" else [args.format]
    results: list[dict[str, Any]] = []
    if args.where is not None:
        # Boolean AND/OR/NOT query path (the AND-only --name/--type/--category/--min-usage shorthand
        # cannot express OR/NOT). Same sort/limit/emit tail as the shorthand below.
        results = _meta_where_rows(args, formats)
        results.sort(key=lambda r: (r.get("format", ""), r.get("pokemon_rank") or 9999, r.get("panel", ""), r.get("entry_rank") or 9999))
        results = results[: args.limit]
        emit(results if args.output == "json" else search_md(results), args.output)
        return
    # Resolve --name through the dex so an English/Chinese/JP query matches a panel row stored in another
    # language (the move/item/ability panels carry no English). The kinds searched come from --panel; a
    # bare search (no panel) covers every entity panel. Each needle expands to its cross-language alias
    # set and matches if ANY alias is present — done once per query, not per row.
    if args.panel:
        p = norm_panel(args.panel)
        name_kinds = (_KIND_BY_PANEL[p],) if p in _KIND_BY_PANEL else ()
    else:
        name_kinds = ("move", "item", "ability", "pokemon", "nature")
    name_alias_sets = []
    for x in args.name:
        aset, resolution = dex_alias_norms_rich(x, name_kinds)
        name_alias_sets.append(aset)
        if resolution:
            # A fuzzy (typo) --name is auto-corrected; keep it NON-SILENT so the caller doesn't read the
            # rows as an exact-query result. search's JSON is a deliberate bare list (no envelope to hold
            # a resolution block), and one query fans out to many rows, so the correction is surfaced on
            # stderr — the same channel the team skill uses for name corrections (_emit_name_flags) — not
            # embedded per-row. The matched rows still carry the canonical name/name_ja themselves.
            print(i18n.t("search_name_corrected", frm=x, to=resolution["resolved"],
                         distance=resolution.get("distance"), score=resolution.get("score")),
                  file=sys.stderr)
    wanted_type = dex_canonical_type(args.type) if args.type else None
    for fmt in formats:
        for row in iter_panel_rows(args.season, fmt, args.panel):
            hay = normalize(" ".join(str(row.get(k, "")) for k in ("name", "name_ja", "key", "slug", "pokemon")))
            if name_alias_sets and not all(any(a in hay for a in aset) for aset in name_alias_sets):
                continue
            if wanted_type and normalize(row.get("type", "")) != normalize(wanted_type):
                continue
            if args.category and normalize(row.get("category", "")) != normalize(args.category):
                continue
            if args.min_usage is not None:
                try:
                    if float(row.get("percentage") or 0) < args.min_usage:
                        continue
                except Exception:
                    continue
            results.append(row)
    results.sort(key=lambda r: (r.get("format", ""), r.get("pokemon_rank") or 9999, r.get("panel", ""), r.get("entry_rank") or 9999))
    results = results[: args.limit]
    # JSON output is a bare list of matched panel rows (consistent with `detail`); the season/rule are
    # query inputs the caller already knows, and each row carries its own format/panel/identity.
    emit(results if args.output == "json" else search_md(results), args.output)


def command_compare(args: argparse.Namespace) -> None:
    args.season, args.rule = resolve_season_rule(args.season, args.rule)
    rows = []
    resolution = None        # captured from whichever format resolves (same query for both)
    resolved_name = None
    for fmt in ("single", "double"):
        detail = resolve_pokemon(args.pokemon, args.season, fmt)
        if not detail:
            rows.append({"format": fmt, "found": False})
            continue
        if detail.get("name_resolution") and resolution is None:
            resolution = detail["name_resolution"]
            resolved_name = detail.get("resolved_name")
        panels = detail.get("panels", {})
        rows.append(
            {
                "format": fmt,
                "found": True,
                "rank": detail.get("rank"),
                "pokemon": detail.get("pokemon"),
                # Thread the English/JP identity so canonicalize emits `name` — the cross-skill JOIN key
                # (every skill must carry it). Without pokemon_en the row had only pokemon/slug, so `name`
                # vanished and a caller couldn't feed compare output to calc/dex (audit 2026-06-28).
                "pokemon_en": detail.get("pokemon_en"),
                "pokemon_ja": detail.get("pokemon_ja"),
                "slug": detail.get("slug"),
                "top_moves": [x.get("name") for x in panels.get("moves", [])[:5]],
                "top_items": [x.get("name") for x in panels.get("items", [])[:5]],
                "top_abilities": [x.get("name") for x in panels.get("abilities", [])[:3]],
                "top_partners": [x.get("name") for x in panels.get("partners", [])[:5]],
            }
        )
    if args.output == "json":
        # Key the per-format results by format ("single"/"double") so callers can address them
        # directly (compared["single"]); season/rule/pokemon stay as sibling metadata.
        out = {"season": args.season, "rule": args.rule, "pokemon": args.pokemon}
        if resolution:  # non-silent typo correction
            out["resolved_name"] = resolved_name
            out["name_resolution"] = resolution
        for row in rows:
            out[row["format"]] = row
        emit(out, args.output)
        return
    lines = [f"# {i18n.t('pokemon')}: {repair_display_name(args.pokemon, 'pokemon', i18n.lang())}", ""]
    if resolution:
        correction = f"{i18n.t('fuzzy')}, {i18n.t('distance')} {resolution.get('distance')}"
        lines.append(f"- {i18n.t('resolved')}: `{args.pokemon}` → {resolved_name}{i18n.parens(correction)}")
        lines.append("")
    for row in rows:
        if not row.get("found"):
            lines.append(f"## {row['format']}\n{i18n.t('nf')}\n")
            continue
        lines.append(f"## {i18n.value('format', row['format'])}")
        lines.append(f"- {i18n.t('rank')}: {row.get('rank')}")
        lines.append(f"- {i18n.t('pokemon')}: {_md_pokemon(row)}{i18n.parens(row.get('slug'))}")
        for panel, field in (("moves", "top_moves"), ("items", "top_items"),
                             ("abilities", "top_abilities"), ("partners", "top_partners")):
            values = [repair_display_name(x, _KIND_BY_PANEL.get(panel), i18n.lang())
                      for x in row.get(field, [])]
            lines.append(f"- {i18n.panel_label(panel)}: {i18n.list_sep().join(values)}")
        lines.append("")
    print("\n".join(lines))


# Machine-readable I/O contract (dev/conventions.md), emitted by `schema`. Pinned by the
# meta contract test so it can't drift from what the CLI actually returns.
META_SCHEMA = {
    "skill": "pokemon-champions-meta",
    "contract": "dev/conventions.md",
    "identity": {"name": "English join key", "name_zh": "中文", "name_ja": "日本語", "slug": "lowercase"},
    "stat_keys": ["hp", "atk", "def", "spa", "spd", "spe"],
    "casing": "move type/category are Title-case",
    "error_shape": {"ok": False, "query": "<input>", "error": {"code": "not_found", "message": "<str>"}},
    "flags": "--format single|double = BATTLE format (compat alias: --game-format, matching team.py); "
             "--output md|json = OUTPUT format. (team.py uses --game-format/--format for the same two "
             "meanings — the alias exists so the asymmetry can't bite cross-skill orchestration.)",
    "commands": {
        "ranking": "--format single|double [--season --rule --limit] -> {season,rule,format,rows:[pokemon row]}",
        "detail": "--format <single|double> [--pokemon] <name ...> [--panel] -> detail(1) | list(>1; misses -> error_shape)",
        "search": "[--format single|double|both (default both)] [--panel --name --type --category "
                  "--min-usage --limit] -> [flattened panel rows]. --type/--category apply to the move "
                  "panel only; unknown --panel -> bad_input. OR `--where '<json>'` for a boolean "
                  "AND/OR/NOT query (see where_query).",
        "compare": "--pokemon <name> -> {single:{found,...},double:{found,...}}  (found:false = not ranked there)",
        "ko": "--format single|double [--pokemon <name ...>] [--panel ko_targets|koed_by|ko_moves|"
              "koed_by_moves] -> {season,rule,format,updated_at,coverage,...row}. The KO axis: who "
              "knocks whom out. Separate file family and separate upstream snapshot from "
              "ranking/details, so `updated_at` here is its own. With no --pokemon it returns "
              "coverage only. A season with no KO snapshot collected is a graceful not_found "
              "(exit 0), NOT an empty result.",
        "report": "--format single|double|both [--season --rule] -> the factual update report "
                  "(rank_moves/new_entries/dropped/config_changes vs the previous snapshot) as "
                  "{season,rule,format,reports:{<fmt>:<report json>}}. The canonical way to read "
                  "report_<season>_<fmt>.json (no hand-built path); default --output json.",
        "export-excel": "[--output-dir/-report-dir] writes THREE single-language workbooks (zh/ja/en), each with localized data + factual-change sheets -> {files:[{lang,output_file,sheets,...}], reports_embedded}",
        "schema": "this contract",
    },
    "where_query": {
        "flag": "search `--where '<json>'` (or '-' for stdin); overrides the AND-only shorthand. Malformed "
                "query -> bad_input error_shape (exit 1); a valid 0-match query is a normal empty list.",
        "grammar": "a node is a SINGLE-key object: {\"and\":[node,...]} | {\"or\":[node,...]} | {\"not\":node} "
                   "| a leaf {\"<field>\":<value>}. Nest freely.",
        "leaves": "name / pokemon (dex cross-language alias match) / type / category / "
                  "usage|rank|entry_rank (numeric expr like >=10 or a bare number) / panel.",
        "output": "same bare panel-row list as the shorthand; unresolved leaves are surfaced on stderr.",
        "example": "{\"and\":[{\"panel\":\"moves\"},{\"type\":\"Fire\"},{\"or\":[{\"category\":\"Special\"},"
                   "{\"usage\":\">=20\"}]}]}",
    },
    "shapes": {
        "ranking_row": "{rank, name, name_zh, name_ja, slug, pokemon, pokemon_en, pokemon_ja}",
        "detail": "{rank, name, name_zh, name_ja, slug, panels:{moves,items,abilities,natures,partners,spreads}}",
        "move_entry": "{rank, name, name_ja, key, percentage:float, type:Title, category:Title, power:int|null}",
        "spread_entry": "{rank, hp,atk,def,spa,spd,spe:int, percentage:float}",
        "ko_row": "{rank, name, name_zh, name_ja, slug, pokedex_no, panels:{ko_targets, koed_by, "
                  "ko_moves, koed_by_moves}, panel_reused_from?:{<panel>:{season,rule,format,"
                  "updated_at,snapshot_id,reason:'current_zero_records'}}}",
        "ko_object_entry": "{rank, key:'<national dex no>', name:zh, name_ja, name_en}. SPECIES-level "
                           "in the public contract, and carries NO percentage. name/name_ja use the "
                           "dex species name; ambiguous split species also carry form_rep.",
        "ko_coverage": "{opponents, opponent_identity:'national_dex', opponent_depth, move_share:"
                       "'absent'|'ranked_pct', move_depth, rows, rows_with_move_share}. A `null` "
                       "ko_moves/koed_by_moves panel means NOT COLLECTED (move_share:'absent'); an "
                       "empty list means confirmed zero with no historical fallback. Optional "
                       "move_share_capture is same_snapshot_bulk_export, mixed_snapshot_bulk_exports "
                       "or independent_bulk (legacy: same_snapshot_detail_pages). A mixed capture "
                       "has rows_with_retained_ko and per-row panel_reused_from instead of a "
                       "file-wide move_share_snapshot_id; each retained panel keeps its donor clock.",
        "name_resolution": "on a TYPO'd query only, detail/compare add {query, resolved_name, name_resolution:{match_type:'fuzzy', score, distance, from}}; an exact query omits all three",
    },
}


def command_schema(args: argparse.Namespace) -> None:
    print(json.dumps(META_SCHEMA, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    # Shared --lang on every subcommand (precedence: flag > POKEMON_CHAMPIONS_LANG env > en). Localizes
    # md labels / error prose only; --output json stays language-invariant.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", choices=list(i18n.LANGS),
                        help="output language for md labels/errors (default: env POKEMON_CHAMPIONS_LANG or en)")
    sub = parser.add_subparsers(dest="command", required=True)
    schema_p = sub.add_parser("schema", parents=[common])
    schema_p.add_argument("--output", choices=["md", "json"], default="json")  # ignored; uniform CLI
    schema_p.set_defaults(func=command_schema)

    ranking = sub.add_parser("ranking", parents=[common])
    # --game-format is a compat ALIAS for --format (battle format), matching team.py's flag name —
    # the asymmetry is a documented high-frequency trap for cross-skill orchestration
    # (conventions.md §2). dest stays "format"; --format remains canonical.
    ranking.add_argument("--format", "--game-format", choices=["single", "double"], required=True)
    ranking.add_argument("--season")
    ranking.add_argument("--rule")
    ranking.add_argument("--limit", type=int, default=20)
    ranking.add_argument("--output", choices=["md", "json"], default="md")
    ranking.set_defaults(func=command_ranking)

    detail = sub.add_parser("detail", parents=[common])
    detail.add_argument("pokemon_pos", nargs="*",
                        help="Pokémon names; convenience alias for --pokemon")
    detail.add_argument("--format", "--game-format", choices=["single", "double"], required=True)
    detail.add_argument("--season")
    detail.add_argument("--rule")
    detail.add_argument("--pokemon", nargs="+",
                        help="one or more names; with >1 name the json output is a list (data files "
                             "loaded once) so callers can fetch many panels in a single process")
    detail.add_argument("--panel")
    detail.add_argument("--output", choices=["md", "json"], default="md")
    detail.set_defaults(func=command_detail, parser=detail)

    search = sub.add_parser("search", parents=[common])
    search.add_argument("--format", "--game-format", choices=["single", "double", "both"], default="both")
    search.add_argument("--season")
    search.add_argument("--rule")
    search.add_argument("--panel")
    search.add_argument("--name", action="append", default=[])
    search.add_argument("--type")
    search.add_argument("--category")
    search.add_argument("--min-usage", type=float)
    search.add_argument("--where", help="a boolean JSON query — AND/OR/NOT over leaf conditions "
                        '{"<field>":<value>} (name/pokemon/type/category/usage/rank/entry_rank/panel); '
                        "overrides the AND-only shorthand; '-' reads the JSON from stdin. "
                        'e.g. \'{"and":[{"panel":"items"},{"or":[{"name":"突击背心"},{"name":"讲究围巾"}]}]}\'')
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--output", choices=["md", "json"], default="md")
    search.set_defaults(func=command_search)

    ko = sub.add_parser("ko", parents=[common])
    ko.add_argument("pokemon_pos", nargs="*", help="Pokemon names; convenience alias for --pokemon")
    ko.add_argument("--format", "--game-format", choices=["single", "double"], required=True)
    ko.add_argument("--season")
    ko.add_argument("--rule")
    ko.add_argument("--pokemon", nargs="+",
                    help="one or more names; with >1 name the json output is a list. With NO name "
                         "the command answers with this snapshot's coverage only.")
    ko.add_argument("--panel",
                    help="ko_targets (alias beats) | koed_by (alias counters) | ko_moves | "
                         "koed_by_moves")
    ko.add_argument("--output", choices=["md", "json"], default="md")
    ko.set_defaults(func=command_ko)

    compare = sub.add_parser("compare", parents=[common])
    compare.add_argument("--season")
    compare.add_argument("--rule")
    compare.add_argument("--pokemon", required=True)
    compare.add_argument("--output", choices=["md", "json"], default="md")
    compare.set_defaults(func=command_compare)

    report = sub.add_parser("report", parents=[common])
    report.add_argument("--format", "--game-format", choices=["single", "double", "both"], default="both")
    report.add_argument("--season")
    report.add_argument("--rule")
    report.add_argument("--output", choices=["md", "json"], default="json")
    report.set_defaults(func=command_report)

    export_excel = sub.add_parser("export-excel", parents=[common])
    export_excel.add_argument("--season")
    export_excel.add_argument("--rule")
    export_excel.add_argument("--output-dir", help="Directory for the three workbooks (<season>_<date>_<zh|ja|en>.xlsx); defaults to the current working directory.")
    export_excel.add_argument("--report-dir", help="Directory holding report_<season>_<fmt>.json to embed in the 更新报告 sheet. Defaults to the skill's own data dir.")
    export_excel.set_defaults(func=command_export_excel)

    args = parser.parse_args()
    i18n.set_lang(getattr(args, "lang", None))
    try:
        args.func(args)
    except EnvironmentResolutionError as exc:
        query = f"{getattr(args, 'season', None) or 'current'}/{getattr(args, 'format', 'both')}"
        if args.command == "report":
            _emit_meta_error(query, "not_found", str(exc))
            return 0
        _emit_meta_error(query, "unknown_format", str(exc))
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        sys.exit(0)
