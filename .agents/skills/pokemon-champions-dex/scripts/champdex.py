#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import operator
import re
import sqlite3
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

# Output-text catalog (md labels / error prose only; JSON stays canonical). Unique module name so the
# cross-skill `import champdex` from meta/team never collides with their own i18n table.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dex_i18n as i18n  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SKILL_DIR = Path(__file__).resolve().parents[1]
DB_PATH = SKILL_DIR / "data" / "champions_dex.sqlite"

STAT_ALIASES = {
    "hp": "hp",
    "at": "at", "atk": "at", "attack": "at", "攻击": "at", "物攻": "at",
    "df": "df", "def": "df", "defense": "df", "防御": "df", "物防": "df",
    "sa": "sa", "spa": "sa", "spatk": "sa", "特攻": "sa",
    "sd": "sd", "spd": "sd", "spdef": "sd", "特防": "sd",
    "sp": "sp", "spe": "sp", "speed": "sp", "速度": "sp",
}
OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "=": operator.eq, "==": operator.eq}

# Canonical external contract (dev/conventions.md): smogon stat keys + int power + a
# uniform error shape. The dex DB stores NCP-native short keys; we translate only at CLI emit time.
SHORT_TO_SMOGON = {"hp": "hp", "at": "atk", "df": "def", "sa": "spa", "sd": "spd", "sp": "spe"}


def canonical_stats(raw: dict[str, Any]) -> dict[str, Any]:
    """DB short stat keys (at/df/sa/sd/sp) -> canonical smogon keys (atk/def/spa/spd/spe)."""
    return {smogon: raw[short] for short, smogon in SHORT_TO_SMOGON.items() if short in raw}


def official_names(c: sqlite3.Connection) -> list[dict[str, Any]]:
    """Official display vocabulary only; aliases and combat data stay in their own APIs."""
    result = []
    for kind, table in (("pokemon", "pokemon"), ("move", "moves"), ("ability", "abilities"),
                        ("item", "items"), ("nature", "natures")):
        for row in c.execute(f"select canonical from {table} order by canonical"):
            canonical = row["canonical"]
            zh, ja = _display_name(c, kind, canonical)
            result.append({"kind": kind, "name": canonical, "name_zh": zh, "name_ja": ja})
    return result


def canonical_power(value: Any) -> Any:
    """Move base power as an int (the DB stores it as a string); None when the move has no power."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def not_found(name: str, code: str = "not_found") -> dict[str, Any]:
    return {"ok": False, "query": name, "error": {"code": code, "message": f"not found: {name}"}}


class BadInput(Exception):
    """A malformed CLI request (unknown condition field, bad stat expression, invalid batch kind).
    Carried up to main() and emitted as the §3 error shape so a `--format json` caller can PARSE the
    failure, instead of a bare `SystemExit` string that crashes the JSON stream (conventions §3)."""


def normalize(text: str) -> str:
    # NFKC folds full-width <-> half-width (Japanese sources vary, e.g. １０ vs 10),
    # so Japanese aliases match regardless of how the query is typed.
    text = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"[\s\-_'’().:：/\\\[\]{}]+", "", text.strip().lower())


def ensure_db() -> None:
    if DB_PATH.exists():
        return
    raise SystemExit(
        f"dex database not found at {DB_PATH}. This skill ships a prebuilt, "
        "read-only database; reinstall the skill to restore it."
    )


def conn() -> sqlite3.Connection:
    ensure_db()
    # The shipped dex is immutable runtime data.  ``mode=ro`` alone can still create WAL/SHM
    # sidecars for a WAL-mode database, which fails when production mounts the release tree
    # read-only (systemd ProtectSystem=strict).  ``immutable=1`` forbids those writes as well.
    c = sqlite3.connect(f"{DB_PATH.as_uri()}?mode=ro&immutable=1", uri=True)
    c.execute("pragma query_only = on")
    c.row_factory = sqlite3.Row
    return c


@lru_cache(maxsize=8)
def _query_aliases(data_dir: Path) -> dict[tuple[str, str], str]:
    """Small lookup-only additions to the installed alias table, maintained by dex alone.

    Keys may be any language. An ambiguous alias is omitted; membership is checked against the
    installed entity table by resolve, so this library never adds a Pokemon or a battle fact.
    """
    path = data_dir / "query_aliases.json"
    if not path.is_file():
        return {}
    groups: dict[tuple[str, str], set[str]] = {}
    for kind, rows in json.loads(path.read_text(encoding="utf-8")).items():
        for canonical, aliases in rows.items():
            for alias in aliases:
                groups.setdefault((kind, normalize(alias)), set()).add(canonical)
    return {key: next(iter(values)) for key, values in groups.items() if len(values) == 1}


def resolve(c: sqlite3.Connection, kind: str, text: str) -> str | None:
    norm = normalize(text)
    if kind == "type":
        for canonical, labels in i18n.TYPE_NAMES.items():
            if norm in {normalize(canonical), *(normalize(label) for label in labels)}:
                return canonical
    row = c.execute(
        "select canonical from aliases where kind=? and normalized=? limit 1",
        (kind, norm),
    ).fetchone()
    if row:
        return row["canonical"]
    table = {"pokemon": "pokemon", "move": "moves", "ability": "abilities", "item": "items",
             "nature": "natures"}.get(kind)
    if table:
        text = _query_aliases(DB_PATH.parent).get((kind, norm), text)
        row = c.execute(f"select canonical from {table} where lower(canonical)=lower(?) limit 1", (text,)).fetchone()
        if row:
            return row["canonical"]
    return None


# --- Fuzzy resolution (DEFAULT third tier) ----------------------------------------------------------
# dex is the naming authority, so resolution is fuzzy-tolerant BY DEFAULT: any caller that asks dex to
# resolve a name gets the typo fallback for free, and a caller that needs a miss to stay a miss opts out
# with `--strict` (validation, integrity checks). A wrong fuzzy hit silently mis-resolves a typo to the
# WRONG entity (a different Pokemon/item looks legal), so this layer is conservative: tight
# per-script/per-length max edit distance, Han disabled in v1 (a 1-char diff in a short Chinese name is
# usually a different species), and a hard ambiguity gate that REFUSES rather than guessing when two
# candidates tie. Fuzzy never fires when exact succeeds (exact short-circuits first).
_FUZZY_POOL_CACHE: dict[tuple[str, str], list[tuple[str, str]]] = {}


def _dominant_script(s: str) -> str:
    """Classify a normalized query by its single dominant script; mixed -> 'mixed' (fuzzy disabled)."""
    han = kana = latin = False
    for ch in s:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF:
            han = True
        elif 0x3040 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF:
            kana = True
        elif "a" <= ch <= "z" or "0" <= ch <= "9":
            latin = True
    if sum((han, kana, latin)) != 1:
        return "mixed"
    return "han" if han else ("kana" if kana else "latin")


def _fuzzy_max_distance(script: str, length: int) -> int:
    """Allowed edit distance by script x length. 0 == fuzzy disabled for this query."""
    if script == "latin":
        if length < 4:
            return 0
        return 1 if length <= 5 else 2     # hard cap 2 — never let a long name drift to a neighbour
    if script == "kana":
        return 1 if length >= 4 else 0     # dakuten/長音 typos are 1 char; tighter than latin
    return 0                                # han / mixed / other: disabled in v1 (too short, too risky)


def _capped_levenshtein(a: str, b: str, max_d: int) -> int | None:
    """True Levenshtein distance, or None when it exceeds max_d. Full-row DP with a row-min cutoff
    (NOT a diagonal-band DP — band edge init is error-prone and can yield false positives that match
    unrelated words). Verified row-for-row against an unbounded reference over 200k random cases."""
    la, lb = len(a), len(b)
    if abs(la - lb) > max_d:
        return None
    if la == 0:
        return lb if lb <= max_d else None
    if lb == 0:
        return la if la <= max_d else None
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        row_min = i
        ai = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ai == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if cur[j] < row_min:
                row_min = cur[j]
        if row_min > max_d:
            return None
        prev = cur
    return prev[lb] if prev[lb] <= max_d else None


def _fuzzy_pool(c: sqlite3.Connection, kind: str) -> list[tuple[str, str]]:
    """Cached (normalized_alias, canonical) candidate pool for one kind, loaded once per (db, kind).
    The DB is read-only, so a process-lifetime cache is safe; the resident worker reuses it."""
    db = (c.execute("pragma database_list").fetchone() or ("", "", ""))[2] or ""
    key = (db, kind)
    pool = _FUZZY_POOL_CACHE.get(key)
    if pool is None:
        pool = [(r[0], r[1]) for r in
                c.execute("select normalized, canonical from aliases where kind=?", (kind,))]
        _FUZZY_POOL_CACHE[key] = pool
    return pool


# Split-gender forms (Indeedee / Indeedee-F, ...) differ in stats, abilities and learnsets, so a query
# that names one gender must never fuzz onto the other's form: 'イエッサン♀' is distance 1 from the bare
# (male) 'イエッサン' and nowhere near 'イエッサン（メスのすがた）'. The pairs (`X` / `X-F`) and the
# qualifier suffixes come from the alias pool itself; these are the user-typed spellings it lacks.
_GENDER_SYMBOLS = {"♀": "F", "♂": "M"}            # matched anywhere in the query
_TYPED_GENDER_SUFFIXES = {"m": "M", "male": "M"}


def _qualified_sibling(pool: list[tuple[str, str]], canonical: str, norm_text: str) -> str | None:
    """The other-gender form when `canonical` is half of a split-gender pair and the query carries a
    qualifier naming that other gender; None otherwise. Suffix qualifiers are the dex's own: each form
    alias minus the shortest bare base alias it extends ('indeedeef' -> 'f', 'イエッサンオスのすがた' ->
    'オスのすがた'), matched longest first so 'female' never reads as 'male'."""
    names: dict[str, set[str]] = {}
    for norm, cn in pool:
        names.setdefault(cn, set()).add(norm)
    if canonical.endswith("-F") and canonical[:-2] in names:
        gender, sibling = "F", canonical[:-2]
    elif canonical + "-F" in names:
        gender, sibling = "M", canonical + "-F"
    else:
        return None
    suffixes = dict(_TYPED_GENDER_SUFFIXES)
    for base in (cn[:-2] for cn in names if cn.endswith("-F") and cn[:-2] in names):
        for form, g in ((base + "-F", "F"), (base, "M")):
            for alias in names[form]:
                stems = [s for s in names[base] if s != alias and alias.startswith(s)]
                if stems:
                    suffixes[alias[len(min(stems, key=len)):]] = g
    said = {g for sym, g in _GENDER_SYMBOLS.items() if sym in norm_text}
    for sfx, g in sorted(suffixes.items(), key=lambda kv: -len(kv[0])):
        if norm_text.endswith(sfx):
            said.add(g)
            break
    return sibling if said - {gender} else None


def _fuzzy_match(c: sqlite3.Connection, kind: str, norm_text: str) -> dict[str, Any] | None:
    """Best fuzzy canonical for a normalized query, or None. Refuses (canonical=None, ambiguous=True)
    when >=2 distinct canonicals tie at the best distance — never guesses between them — and when the
    best hit is a split-gender form the query's own gender qualifier contradicts (the qualified form
    leads the alternatives)."""
    if not norm_text:
        return None
    max_d = _fuzzy_max_distance(_dominant_script(norm_text), len(norm_text))
    if max_d == 0:
        return None
    length = len(norm_text)
    best: dict[str, int] = {}
    for cand_norm, canonical in _fuzzy_pool(c, kind):
        if abs(len(cand_norm) - length) > max_d:
            continue
        d = _capped_levenshtein(norm_text, cand_norm, max_d)
        if d is None:
            continue
        if canonical not in best or d < best[canonical]:
            best[canonical] = d
    if not best:
        return None
    ranked = sorted(best.items(), key=lambda kv: (kv[1], kv[0]))
    best_canon, best_d = ranked[0]
    score = lambda d: round(1 - d / length, 3)  # noqa: E731
    alts = [{"canonical": cn, "distance": d, "score": score(d)} for cn, d in ranked[:3]]
    if len(ranked) >= 2 and ranked[1][1] == best_d:
        return {"canonical": None, "match_type": "fuzzy", "score": score(best_d),
                "distance": best_d, "ambiguous": True, "alternatives": alts}
    sibling = _qualified_sibling(_fuzzy_pool(c, kind), best_canon, norm_text) if kind == "pokemon" else None
    if sibling:
        lead = next((a for a in alts if a["canonical"] == sibling), {"canonical": sibling})
        return {"canonical": None, "match_type": "fuzzy", "score": score(best_d), "distance": best_d,
                "ambiguous": True, "alternatives": [lead, *(a for a in alts if a is not lead)]}
    return {"canonical": best_canon, "match_type": "fuzzy", "score": score(best_d),
            "distance": best_d, "alternatives": alts}


def resolve_rich(c: sqlite3.Connection, kind: str, text: str, *, fuzzy: bool = True) -> dict[str, Any]:
    """Unified resolution result. Exact short-circuits (fuzzy never fires on a hit). Fuzzy is ON by
    default (dex owns resolution quality); pass fuzzy=False for strict callers (validation/integrity).
    With fuzzy=False this is just `resolve()` wrapped in metadata; bare `resolve()` is always exact."""
    canon = resolve(c, kind, text)
    norm = normalize(text)
    if canon:
        return {"canonical": canon, "match_type": "exact", "score": 1.0,
                "distance": 0, "query_norm": norm, "alternatives": []}
    if fuzzy:
        fm = _fuzzy_match(c, kind, norm)
        if fm is not None:
            fm["query_norm"] = norm
            fm.setdefault("alternatives", [])
            return fm
    return {"canonical": None, "match_type": None, "score": 0.0,
            "distance": None, "query_norm": norm, "alternatives": []}


# --- Mega compositional resolution ------------------------------------------------------------------
# Users write a Mega as an AFFIX + a base name in ANY language: "Mega耿鬼", "耿鬼Mega", "メガゲンガー",
# "超级耿鬼". Only the fully-spelled aliases ("Mega Gengar", "超级耿鬼") sit in the alias table; the mixed
# forms (English "Mega" + Chinese base, etc.) do not, so exact/fuzzy both miss. This layer strips the
# Mega affix, resolves the BASE species in any language, then maps it to that base's Mega form + stone.
# It is compositional (not a fuzzy guess), so it runs BEFORE the fuzzy tier. X/Y megas disambiguate on a
# leading/trailing X/Y/Z token (NFKC includes full-width). A plain Mega form is literal when present;
# otherwise a multi-Mega base with no disambiguator refuses (ambiguous), never guesses.
_MEGA_AFFIXES = ("mega", "超级", "メガ", "めが")  # normalized (lowercase, NFKC); checked as prefix or suffix


def _strip_mega_affix(norm: str) -> str | None:
    """Remainder after removing a leading/trailing Mega affix, or None when no affix is present."""
    for aff in _MEGA_AFFIXES:
        if norm.startswith(aff) and len(norm) > len(aff):
            return norm[len(aff):]
        if norm.endswith(aff) and len(norm) > len(aff):
            return norm[: -len(aff)]
    return None


def mega_compose(c: sqlite3.Connection, text: str) -> dict[str, Any] | None:
    """Resolve an affix+base Mega name to the canonical Mega form. Returns
    {canonical, required_item, match_type:'mega_compose'} on success, {ambiguous:True, candidates:[...]}
    when the base has only lettered Megas and no qualifier, or None for a non-composable name."""
    norm = normalize(text)
    base_norm = _strip_mega_affix(norm)
    explicit_mega = base_norm is not None
    base_norm = base_norm if explicit_mega else norm
    variant = None
    base = resolve(c, "pokemon", base_norm) if explicit_mega else None
    if base is None:
        # No whole-name hit: an X/Y/Z is a Mega disambiguator, never a
        # letter of the base name — strip it and retry, so 'メガDelphox' (base itself ends in x) is
        # not mis-split into a dead 'delpho' stem and lost (audit 2026-07-05).
        for v in ("x", "y", "z"):
            if base_norm.endswith(v) and len(base_norm) > 1:
                variant, base_norm = v.upper(), base_norm[:-1]
                break
            if base_norm.startswith(v) and len(base_norm) > 1:
                variant, base_norm = v.upper(), base_norm[1:]
                break
        if variant is None:
            return None
        base_norm = _strip_mega_affix(base_norm) or base_norm
        base = resolve(c, "pokemon", base_norm)
        if base is None:
            return None
    row = c.execute("select base_species, is_mega from pokemon where canonical=?", (base,)).fetchone()
    if row is None:
        return None
    base_species = row["base_species"] or base
    megas = c.execute(
        "select canonical, required_item from pokemon where is_mega=1 and base_species=? order by canonical",
        (base_species,),
    ).fetchall()
    if not megas:
        return None                            # base has no Mega form
    if variant:
        for m in megas:
            if normalize(m["canonical"]).endswith(variant.lower()):
                return {"canonical": m["canonical"], "required_item": m["required_item"], "match_type": "mega_compose"}
        # A stated qualifier is never discarded, even for a species with only one other Mega.
        return {"ambiguous": True, "candidates": [m["canonical"] for m in megas],
                "note": f"{base_species} has no Mega {variant} form; suggestions are alternative forms, "
                        "not equivalent names for the stated variant"}
    plain = next((m for m in megas if m["canonical"] == f"Mega {base_species}"), None)
    if plain:
        return {"canonical": plain["canonical"], "required_item": plain["required_item"], "match_type": "mega_compose"}
    if len(megas) == 1:
        return {"canonical": megas[0]["canonical"], "required_item": megas[0]["required_item"], "match_type": "mega_compose"}
    return {"ambiguous": True, "candidates": [m["canonical"] for m in megas]}


def _display_name(c: sqlite3.Connection, kind: str, canonical: str) -> tuple[str | None, str | None]:
    """(display_name zh, display_name_ja) for a resolved canonical, or (None, None)."""
    if kind == "type":
        return i18n.TYPE_NAMES.get(canonical, (None, None))
    table = {"pokemon": "pokemon", "move": "moves", "ability": "abilities", "item": "items",
             "nature": "natures"}.get(kind)
    if not table:
        return None, None
    row = c.execute(f"select display_name, display_name_ja from {table} where canonical=? limit 1", (canonical,)).fetchone()
    return (row["display_name"], row["display_name_ja"]) if row else (None, None)


_MD_CONN: sqlite3.Connection | None = None


def _md_entity(kind: str, canonical: str | None) -> str:
    """Localized Markdown display value; JSON continues to carry canonical English only."""
    if not canonical or i18n.lang() == "en" or _MD_CONN is None:
        return canonical or ""
    zh, ja = _display_name(_MD_CONN, kind, canonical)
    return (zh if i18n.lang() == "zh" else ja) or canonical


def _md_head(data: dict[str, Any]) -> str:
    if i18n.lang() == "zh":
        return data.get("display_name") or data.get("name") or ""
    if i18n.lang() == "ja":
        return data.get("display_name_ja") or data.get("name") or ""
    return data.get("name") or ""


def _md_stats(stats: dict[str, Any]) -> str:
    return " / ".join(f"{i18n.value('stat', k)} {v}" for k, v in stats.items())


def _md_join(values: Any) -> str:
    return i18n.list_sep().join(str(v) for v in values)


def _md_value(value: Any) -> str:
    return "—" if value is None or value == "" else str(value)


def _md_effect(data: dict[str, Any]) -> str:
    """Effect prose in the selected UI language, with canonical English fallback."""
    preferred = data.get(f"effect_{i18n.lang()}")
    return preferred if preferred not in (None, "") else (data.get("effect_en") or "")


def _possible_kinds(c: sqlite3.Connection, text: str, *, exclude_kind: str) -> list[dict[str, Any]]:
    """Exact cross-kind hints for a miss, e.g. `pokemon Crabominite` -> item Crabominite.

    This is deliberately exact-only, not fuzzy: `possible_kinds` means the query is a real entity under
    another kind, while typo alternatives stay under `suggestions`.
    """
    out: list[dict[str, Any]] = []
    for kind in ("pokemon", "move", "ability", "item", "nature"):
        if kind == exclude_kind:
            continue
        canonical = resolve(c, kind, text)
        if not canonical:
            continue
        zh, ja = _display_name(c, kind, canonical)
        entry: dict[str, Any] = {"kind": kind, "canonical": canonical,
                                 "display_name": zh, "display_name_ja": ja}
        if kind == "item":
            holders = [r["canonical"] for r in c.execute(
                "select canonical from pokemon where required_item=? order by canonical", (canonical,))]
            if holders:
                entry["required_by"] = holders
        elif kind == "pokemon":
            row = c.execute(
                "select is_mega, base_species, required_item from pokemon where canonical=?", (canonical,)
            ).fetchone()
            if row:
                entry["is_mega"] = bool(row["is_mega"])
                entry["base_species"] = row["base_species"]
                entry["required_item"] = row["required_item"]
        out.append(entry)
    return out


def resolve_name(c: sqlite3.Connection, kind: str, text: str, *, fuzzy: bool = True) -> dict[str, Any]:
    """Normalize ONE free-form user name to a canonical entity — the AI-facing name-cleaning primitive.
    Resolution order: exact alias/canonical -> Mega compose (pokemon only) -> fuzzy typo fallback. Always
    returns a 1:1 record: on success {query, ok:True, kind, canonical, display_name(_ja), match_type,
    score, +is_mega/base_species/required_item for pokemon}; on a miss {query, ok:False, kind,
    canonical:None, match_type, suggestions?}. `match_type` is exact|mega_compose|fuzzy (success) or
    ambiguous|None (miss). Guarantees a miss is NEVER dropped (the batch/resolve cardinality contract)."""
    def _hit(canonical: str, match_type: str, score: float, distance: int = 0,
             required_item: str | None = None) -> dict[str, Any]:
        zh, ja = _display_name(c, kind, canonical)
        out: dict[str, Any] = {"query": text, "ok": True, "kind": kind, "canonical": canonical,
                               "display_name": zh, "display_name_ja": ja, "match_type": match_type, "score": score}
        if kind == "pokemon":
            row = c.execute("select is_mega, base_species, required_item from pokemon where canonical=?", (canonical,)).fetchone()
            if row is not None:
                out["is_mega"] = bool(row["is_mega"])
                out["base_species"] = row["base_species"]
                out["required_item"] = required_item if required_item is not None else row["required_item"]
        if match_type == "fuzzy":
            out["distance"] = distance
        return out

    norm = normalize(text)
    canon = resolve(c, kind, text)
    if canon:
        return _hit(canon, "exact", 1.0)
    if kind == "pokemon":
        mc = mega_compose(c, text)
        if mc and mc.get("canonical"):
            return _hit(mc["canonical"], "mega_compose", 1.0, required_item=mc.get("required_item"))
        if mc and mc.get("ambiguous"):
            return {"query": text, "ok": False, "kind": kind, "canonical": None, "match_type": "ambiguous",
                    "error": {"code": "ambiguous", "message": f"ambiguous Mega: {text}"},
                    **({"note": mc["note"]} if mc.get("note") else {}),
                    "suggestions": [{"canonical": x} for x in mc["candidates"]]}
    if fuzzy:
        fm = _fuzzy_match(c, kind, norm)
        if fm is not None and fm.get("canonical"):
            return _hit(fm["canonical"], "fuzzy", fm.get("score", 0.0), distance=fm.get("distance", 0) or 0)
    miss = {"query": text, "ok": False, "kind": kind, "canonical": None, "match_type": None,
            "error": {"code": "not_found", "message": f"not found: {text}"}}
    pk = _possible_kinds(c, text, exclude_kind=kind)
    if pk:
        miss["possible_kinds"] = pk
    if fuzzy:
        fm = _fuzzy_match(c, kind, norm)      # ambiguous ties carry did-you-mean candidates
        if fm is not None and fm.get("alternatives"):
            miss["suggestions"] = fm["alternatives"]
            if fm.get("ambiguous"):
                miss["match_type"] = "ambiguous"
                miss["error"] = {"code": "ambiguous", "message": f"ambiguous name: {text}"}
    return miss


def mega_forms_for(c: sqlite3.Connection, canonical: str,
                   base_species: str | None) -> list[dict[str, Any]]:
    """The Mega forms this ROSTER FORM may evolve into (holding the listed stone).

    `base_species` is the Species Clause GROUPING KEY, not a host pointer, so a Mega form's key
    cannot be matched against a member's name blindly: Raichu and Raichu-Alola share the key
    "Raichu", yet only Raichu itself holds Raichuite X/Y. The strict name match is therefore the
    rule — except for the one Champions species whose grouping key is not a roster form at all
    (Mega Floette keys under "Floette" while the roster only has Floette-Eternal). There the
    group's single non-Mega form is unambiguously the host, and refusing to say so is what left a
    legal Floette-Eternal + Floettite registration counted as no Mega at all.
    """
    def rows_for(host: str) -> list[dict[str, Any]]:
        return [{"name": r["canonical"], "required_item": r["required_item"]}
                for r in c.execute(
                    "select canonical, required_item from pokemon "
                    "where is_mega=1 and base_species=? order by canonical", (host,))]

    forms = rows_for(canonical)
    if forms or not base_species or base_species == canonical:
        return forms
    if c.execute("select 1 from pokemon where canonical=?", (base_species,)).fetchone():
        return []                       # the key IS a roster form: only that form is the host
    hosts = [r["canonical"] for r in c.execute(
        "select canonical from pokemon where is_mega=0 and base_species=?", (base_species,))]
    return rows_for(base_species) if hosts == [canonical] else []


def row_to_pokemon(row: sqlite3.Row, c: sqlite3.Connection, include_moves: bool = False) -> dict[str, Any]:
    data = {
        "name": row["canonical"],
        "display_name": row["display_name"],
        "display_name_ja": row["display_name_ja"],
        "national_dex_no": row["national_dex_no"],
        "types": json.loads(row["types_json"] or "[]"),
        "stats": canonical_stats(json.loads(row["stats_json"] or "{}")),
        "abilities": json.loads(row["abilities_json"] or "[]"),
        "is_mega": bool(row["is_mega"]),
        "base_species": row["base_species"],
        "required_item": row["required_item"],
    }
    if not data["is_mega"]:
        megas = mega_forms_for(c, row["canonical"], row["base_species"])
        if megas:
            data["mega_forms"] = megas
    if include_moves:
        moves = [r["move"] for r in c.execute("select move from learnsets where pokemon=? order by move", (row["canonical"],))]
        data["moves"] = moves
    return data


def get_one(c: sqlite3.Connection, kind: str, name: str, fuzzy: bool = True) -> dict[str, Any]:
    rr = resolve_name(c, kind, name, fuzzy=fuzzy)
    canonical = rr["canonical"]
    if canonical is None:
        return {**not_found(name), **{key: rr[key] for key in
                ("error", "possible_kinds", "suggestions", "note") if key in rr}}

    if kind == "pokemon":
        row = c.execute("select * from pokemon where canonical=?", (canonical,)).fetchone()
        entity = row_to_pokemon(row, c, include_moves=True) if row else None
    elif kind == "move":
        row = c.execute("select * from moves where canonical=?", (canonical,)).fetchone()
        if row:
            raw = json.loads(row["raw_json"] or "{}")
            users = [r["pokemon"] for r in c.execute("select pokemon from learnsets where move=? order by pokemon", (canonical,))]
            entity = {
                "name": row["canonical"], "display_name": row["display_name"],
                "display_name_ja": row["display_name_ja"], "type": row["type"],
                "category": row["category"], "power": canonical_power(row["power"]),
                # same numeric surface as find-move's _move_row: int or None, never raw text —
                # the move/batch endpoints emitted '70' while find-move emitted 70 (self-audit
                # 2026-07-03); pp likewise below.
                "accuracy": canonical_power(row["accuracy"]),
                "pp": canonical_power(row["pp"]), "priority": row["priority"],
                # Trilingual effect texts (effects_seed.py sources; None on a pre-effects DB
                # so an old data file degrades instead of crashing the CLI).
                **_effects(row),
                # Mechanical flag from the vendored NCP move catalog. Accuracy alone is not a safe
                # proxy: ordinary 30%-accuracy attacks and OHKO moves have different semantics.
                "is_ohko": raw.get("isOHKO") is True, "known_users": users,
            }
        else:
            entity = None
    elif kind == "ability":
        row = c.execute("select * from abilities where canonical=?", (canonical,)).fetchone()
        if row:
            users = []
            for p in c.execute("select * from pokemon"):
                abilities = json.loads(p["abilities_json"] or "[]")
                if canonical in abilities:
                    users.append(p["canonical"])
            entity = {"name": row["canonical"], "display_name": row["display_name"],
                      "display_name_ja": row["display_name_ja"], **_effects(row),
                      "known_users": users}
        else:
            entity = None
    elif kind == "item":
        row = c.execute("select * from items where canonical=?", (canonical,)).fetchone()
        if row:
            holders = [r["canonical"] for r in c.execute("select canonical from pokemon where required_item=? order by canonical", (canonical,))]
            entity = {"name": row["canonical"], "display_name": row["display_name"],
                      "display_name_ja": row["display_name_ja"], **_effects(row),
                      "required_by": holders}
        else:
            entity = None
    elif kind == "nature":
        row = c.execute("select * from natures where canonical=?", (canonical,)).fetchone()
        if row:
            entity = {"name": row["canonical"], "display_name": row["display_name"],
                      "display_name_ja": row["display_name_ja"],
                      "up_stat": row["up_stat"], "down_stat": row["down_stat"]}
        else:
            entity = None
    else:
        raise ValueError(f"unknown kind: {kind}")

    if entity is None:
        nf = not_found(name)
        pk = _possible_kinds(c, name, exclude_kind=kind)
        if pk:
            nf["possible_kinds"] = pk
        return nf
    if rr["match_type"] == "fuzzy":
        # Tag the correction so callers can record provenance and cap confidence (never silent).
        entity["resolution"] = {"match_type": "fuzzy", "score": rr["score"],
                                "distance": rr["distance"], "from": normalize(name)}
    return entity


def parse_conditions(tokens: list[str]) -> list[tuple[str, str]]:
    if len(tokens) % 2 != 0:
        raise BadInput("find/reverse needs field value pairs, e.g. find move 近身战 type 飞行")
    return [(tokens[i].lower(), tokens[i + 1]) for i in range(0, len(tokens), 2)]


def stat_ok(stats: dict[str, Any], expr: str) -> bool:
    m = re.match(r"^([A-Za-z\u4e00-\u9fff]+)\s*(>=|<=|==|=|>|<)\s*(\d+)$", expr.strip())
    if not m:
        raise BadInput(f"bad stat expression: {expr}")
    stat = STAT_ALIASES.get(normalize(m.group(1)))
    if not stat:
        raise BadInput(f"unknown stat: {m.group(1)}")
    value = int(stats.get(stat, 0))
    return OPS[m.group(2)](value, int(m.group(3)))


def _pokemon_leaf(c: sqlite3.Connection, field: str, value: Any):
    """Build a per-row predicate for ONE pokemon condition, shared by the AND-shorthand `find` and the
    boolean `find_where`. Returns (pred(row)->bool, resolved:(field,value), unresolved:dict|None,
    learnset_empty:bool). Names are exact-resolved (an unresolved value filters literally and is
    SURFACED, never silently matched on raw text — audit 2026-06-28). `learns_move_where` takes a MOVE
    sub-AST: pokemon that learn ANY move matching it."""
    f = field.lower()
    if f in {"learns_move_where", "learns_where"}:
        msink = _new_sink()
        mpred = _compile_where(value, lambda fl, v: _move_leaf(c, fl, v), msink)
        move_names = {m["canonical"] for m in c.execute("select * from moves") if mpred(m)}
        users = ({r["pokemon"] for r in c.execute("select pokemon, move from learnsets")
                  if r["move"] in move_names} if move_names else set())
        return ((lambda r: r["canonical"] in users), ("learns_move_where", f"{len(move_names)} moves"),
                (msink["unresolved"][0] if msink["unresolved"] else None), (not move_names))
    if f in {"move", "moves", "learns", "会"}:
        move = resolve(c, "move", str(value))
        unres = None if move is not None else {"field": "move", "value": str(value)}
        move = move if move is not None else str(value)
        users = {r["pokemon"] for r in c.execute("select pokemon from learnsets where move=?", (move,))}
        return (lambda r: r["canonical"] in users), ("move", move), unres, (not users)
    if f in {"ability", "特性"}:
        ab = resolve(c, "ability", str(value))
        unres = None if ab is not None else {"field": "ability", "value": str(value)}
        ab = ab if ab is not None else str(value)
        return (lambda r: ab in json.loads(r["abilities_json"] or "[]")), ("ability", ab), unres, False
    if f in {"type", "属性"}:
        typ = resolve(c, "type", str(value))
        unres = None if typ is not None else {"field": "type", "value": str(value)}
        typ = typ if typ is not None else str(value)
        return (lambda r: typ in json.loads(r["types_json"] or "[]")), ("type", typ), unres, False
    if f in {"pokemon", "name", "宝可梦"}:
        n = normalize(str(value))
        matched = {a["canonical"] for a in c.execute(
            "select canonical from aliases where kind='pokemon' and normalized like ?", (f"%{n}%",))}
        return ((lambda r: r["canonical"] in matched or n in normalize(r["canonical"])
                 or n in normalize(r["display_name"])), ("name", str(value)), None, False)
    if f in {"mega", "超级"}:
        want = normalize(str(value)) not in {"false", "no", "0", "否", "不是"}
        return (lambda r: bool(r["is_mega"]) == want), ("mega", str(want)), None, False
    if f in {"stat", "stats", "能力"}:
        stat_ok({}, str(value))     # eager: a bad expr/stat raises BadInput now, not mid-scan
        return ((lambda r: stat_ok(json.loads(r["stats_json"] or "{}"), str(value))),
                ("stat", str(value)), None, False)
    if f in {"item", "道具"}:
        it = resolve(c, "item", str(value))
        unres = None if it is not None else {"field": "item", "value": str(value)}
        it = it if it is not None else str(value)
        return (lambda r: r["required_item"] == it), ("item", it), unres, False
    raise BadInput(f"unknown condition field: {field}; expected one of "
                   "move/ability/type/pokemon/item/mega/stat/name/learns_move_where")


def find(c: sqlite3.Connection, conditions: list[tuple[str, str]]) -> dict[str, Any]:
    rows = list(c.execute("select * from pokemon order by canonical"))
    resolved_conditions, unresolved, learnset_warning = [], [], False
    for field, value in conditions:
        pred, res, unres, ls_empty = _pokemon_leaf(c, field, value)
        rows = [r for r in rows if pred(r)]
        resolved_conditions.append(res)
        if unres:
            unresolved.append(unres)
        if ls_empty:
            learnset_warning = True
    return {
        "conditions": resolved_conditions,
        "count": len(rows),
        "learnset_warning": learnset_warning,
        "unresolved_conditions": unresolved,
        "results": [row_to_pokemon(r, c, include_moves=False) for r in rows],
    }


_CATEGORY_ALIASES = {"physical": "Physical", "phys": "Physical", "物理": "Physical",
                     "special": "Special", "spec": "Special", "特殊": "Special",
                     "status": "Status", "变化": "Status", "變化": "Status"}


def _norm_category(value: str) -> str | None:
    return _CATEGORY_ALIASES.get(normalize(value))


def _num_cmp(raw: Any, expr: str, field: str) -> bool:
    """Compare a move's numeric attribute (power/priority/accuracy/pp) to `expr` like `>=90`, `<40`,
    `2`, `==0`. An empty/None attribute (a status move's power) never satisfies a numeric test."""
    m = re.match(r"^\s*(>=|<=|==|=|>|<)?\s*(-?\d+)\s*$", str(expr))
    if not m:
        raise BadInput(f"bad {field} expression: {expr!r} (use e.g. {field}>=90 or a bare number)")
    if raw in (None, ""):
        return False
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return False
    return OPS[m.group(1) or "=="](v, int(m.group(2)))


def _effects(row: sqlite3.Row) -> dict[str, Any]:
    """Trilingual effect columns (effects_seed.py). Tolerates a pre-effects DB file."""
    keys = row.keys()
    return {f"effect_{lang}": (row[f"effect_{lang}"] if f"effect_{lang}" in keys else None)
            for lang in ("zh", "en", "ja")}


def _move_row(row: sqlite3.Row) -> dict[str, Any]:
    return {"name": row["canonical"], "display_name": row["display_name"],
            "display_name_ja": row["display_name_ja"], "type": row["type"], "category": row["category"],
            "power": canonical_power(row["power"]), "accuracy": canonical_power(row["accuracy"]),
            "pp": canonical_power(row["pp"]), "priority": row["priority"], **_effects(row)}


def _move_leaf(c: sqlite3.Connection, field: str, value: Any):
    """Per-row predicate for ONE move condition, shared by the `find-move` shorthand and
    `find_move_where`. Returns (pred, resolved:(field,value), unresolved:dict|None, False). Numeric
    fields (power/priority/accuracy/pp) take a comparison expr (`>=90`, `<40`, `2`) or a bare int;
    type/category are exact (an unresolved value is surfaced, not matched on raw text)."""
    f = field.lower()
    if f in {"type", "属性"}:
        typ = resolve(c, "type", str(value))
        unres = None if typ is not None else {"field": "type", "value": str(value)}
        typ = typ if typ is not None else str(value)
        return (lambda r: (r["type"] or "") == typ), ("type", typ), unres, False
    if f in {"category", "cat", "分类"}:
        cat = _norm_category(str(value))
        if cat is None:
            return (lambda r: False), ("category", str(value)), {"field": "category", "value": str(value)}, False
        return (lambda r: (r["category"] or "") == cat), ("category", cat), None, False
    if f in {"power", "威力", "bp"}:
        _num_cmp(None, value, "power")      # eager: a bad expr raises BadInput now, not mid-scan
        return (lambda r: _num_cmp(r["power"], value, "power")), ("power", str(value)), None, False
    if f in {"priority", "先制", "优先度"}:
        _num_cmp(None, value, "priority")
        return (lambda r: _num_cmp(r["priority"], value, "priority")), ("priority", str(value)), None, False
    if f in {"accuracy", "命中", "acc"}:
        _num_cmp(None, value, "accuracy")
        return (lambda r: _num_cmp(r["accuracy"], value, "accuracy")), ("accuracy", str(value)), None, False
    if f == "pp":
        _num_cmp(None, value, "pp")
        return (lambda r: _num_cmp(r["pp"], value, "pp")), ("pp", str(value)), None, False
    if f in {"name", "move", "名称"}:
        n = normalize(str(value))
        return ((lambda r: n in normalize(r["canonical"]) or n in normalize(r["display_name"] or "")),
                ("name", str(value)), None, False)
    raise BadInput(f"unknown move condition field: {field}; expected "
                   "type/category/power/priority/accuracy/pp/name")


def find_move(c: sqlite3.Connection, conditions: list[tuple[str, str]]) -> dict[str, Any]:
    """Reverse-search the MOVES table (the pokemon-only `find` cannot): filter by type / category /
    power / priority / accuracy / pp / name (ANDed)."""
    rows = list(c.execute("select * from moves order by canonical"))
    resolved, unresolved = [], []
    for field, value in conditions:
        pred, res, unres, _ = _move_leaf(c, field, value)
        rows = [r for r in rows if pred(r)]
        resolved.append(res)
        if unres:
            unresolved.append(unres)
    return {"conditions": resolved, "count": len(rows),
            "unresolved_conditions": unresolved, "results": [_move_row(r) for r in rows]}


def _new_sink() -> dict[str, Any]:
    return {"resolved": [], "unresolved": [], "learnset_empty": False}


def _compile_where(node: Any, leaf_fn, sink: dict[str, Any]):
    """Compile a boolean query AST into a per-row predicate. A node is a SINGLE-key object: `and`/`or`
    -> a non-empty list of sub-nodes; `not` -> one sub-node; anything else is a leaf `{<field>:<value>}`
    built by `leaf_fn(field, value)`. Resolved / unresolved leaves + learnset-empty accumulate in
    `sink` (so the caller reports the same unresolved-keyword and empty-learnset signals as the
    shorthand). Depth is bounded by the AST the caller passes; no cycles are possible in JSON."""
    if not isinstance(node, dict) or len(node) != 1:
        raise BadInput('each query node must be a single-key object: {"and"|"or":[...]}, {"not":{...}}, '
                       'or a leaf {"<field>":"<value>"}')
    (key, val), = node.items()
    k = key.lower()
    if k in {"and", "or"}:
        if not isinstance(val, list) or not val:
            raise BadInput(f'"{k}" takes a non-empty list of sub-queries')
        preds = [_compile_where(child, leaf_fn, sink) for child in val]
        return (lambda r: all(p(r) for p in preds)) if k == "and" else (lambda r: any(p(r) for p in preds))
    if k == "not":
        sub = _compile_where(val, leaf_fn, sink)
        return lambda r: not sub(r)
    pred, res, unres, ls_empty = leaf_fn(key, val)
    sink["resolved"].append(res)
    if unres:
        sink["unresolved"].append(unres)
    if ls_empty:
        sink["learnset_empty"] = True
    return pred


def find_where(c: sqlite3.Connection, ast: Any) -> dict[str, Any]:
    """Boolean pokemon search: AND/OR/NOT over the SAME leaves as `find`, plus `learns_move_where`.
    Output mirrors `find` (+ an echoed `where`) so the md renderer and callers are unchanged."""
    sink = _new_sink()
    pred = _compile_where(ast, lambda f, v: _pokemon_leaf(c, f, v), sink)
    rows = [r for r in c.execute("select * from pokemon order by canonical") if pred(r)]
    return {"where": ast, "conditions": sink["resolved"], "count": len(rows),
            "learnset_warning": sink["learnset_empty"], "unresolved_conditions": sink["unresolved"],
            "results": [row_to_pokemon(r, c, include_moves=False) for r in rows]}


def find_move_where(c: sqlite3.Connection, ast: Any) -> dict[str, Any]:
    """Boolean moves-table search: AND/OR/NOT over the SAME leaves as `find-move`."""
    sink = _new_sink()
    pred = _compile_where(ast, lambda f, v: _move_leaf(c, f, v), sink)
    rows = [r for r in c.execute("select * from moves order by canonical") if pred(r)]
    return {"where": ast, "conditions": sink["resolved"], "count": len(rows),
            "unresolved_conditions": sink["unresolved"], "results": [_move_row(r) for r in rows]}


def _resolution_md(data: dict[str, Any]) -> list[str]:
    """The fuzzy-correction provenance line for an entity panel — empty for an exact hit. Surfacing it in
    md keeps the 'a correction is never silent' promise at the DEFAULT (md) layer, not only in json."""
    r = data.get("resolution")
    if not r:
        return []
    detail = (f"{i18n.t('fuzzy')}, {i18n.t('distance')} {r.get('distance')}, "
              f"{i18n.t('score')} {r.get('score')}")
    return [f"- {i18n.t('resolved')}: `{r.get('from', '')}` → {data.get('name')}"
            f"{i18n.parens(detail)}"]


def format_md(data: Any) -> str:
    if isinstance(data, list):
        return "\n\n".join(format_md(x) for x in data)
    if "results" in data:
        lines = [f"{i18n.t('conditions')}: {_md_join(f'{k}={v}' for k, v in data['conditions'])}",
                 f"{i18n.t('count')}: {data['count']}"]
        if data.get("unresolved_conditions"):
            # find/reverse are exact-only: an unresolved keyword filtered on raw text, so Count may be 0
            # because the FILTER is invalid, not because nothing matched — say which (audit 2026-06-28).
            bad = _md_join(f"{c['field']}={c['value']}" for c in data["unresolved_conditions"])
            lines.append(i18n.t("note_unrecognized", bad=bad))
        if data.get("learnset_warning"):
            lines.append(i18n.t("note_no_learnset"))
        for p in data["results"]:
            if "category" in p:                       # a move row (find-move), not a pokemon row
                extra = "".join([f" | {i18n.t('power')} {p['power']}" if p.get("power") else "",
                                 f" | {i18n.t('priority')} {p['priority']:+d}" if p.get("priority") else ""])
                lines.append(f"- {_md_head(p)} | {i18n.value('type', p['type'])} "
                             f"{i18n.value('category', p['category'])}{extra}")
            else:
                lines.append(f"- {_md_head(p)} | {'/'.join(i18n.value('type', x) for x in p['types'])} "
                             f"| {i18n.t('stats')} {_md_stats(p['stats'])} | {i18n.t('abilities')} "
                             f"{_md_join(_md_entity('ability', x) for x in p['abilities'])}"
                             + (f" | {i18n.t('required_item')} {_md_entity('item', p['required_item'])}"
                                if p.get("required_item") else ""))
        return "\n".join(lines)
    if data.get("error"):
        err = data["error"]
        code = err.get("code") if isinstance(err, dict) else ""
        msg = (i18n.t("error_ambiguous" if code == "ambiguous" else "error_not_found",
                      query=data.get("query", "")) if code in ("ambiguous", "not_found")
               else err.get("message") if isinstance(err, dict) else err)
        # Batch error items carry their input position (contract §3); keep that anchor in md so a caller
        # can align the failure to the Nth argument. Did-you-mean suggestions (ambiguous tie / near miss)
        # are surfaced too, so a miss is never silent at the md layer (audit 2026-06-28).
        prefix = f"[{data['index']}] " if "index" in data else ""
        line = f"{prefix}{data.get('query')}: {msg}"
        sugg = data.get("suggestions")
        if sugg:
            cands = _md_join(f"{s.get('canonical')}" + (f" ({i18n.t('distance')} {s['distance']})"
                                                        if s.get("distance") is not None else "")
                             for s in sugg)
            line += f" — {i18n.t('did_you_mean')}: {cands}"
        pk = data.get("possible_kinds") or []
        if pk:
            hints = []
            for h in pk:
                extra = ""
                if h.get("required_by"):
                    extra = " → " + _md_join(h["required_by"])
                hints.append(f"{h.get('kind')}:{h.get('canonical')}{extra}")
            line += f" — {i18n.t('possible_kinds')}: " + _md_join(hints)
        return line
    if "types" in data:
        lines = [
            f"## {_md_head(data)}",
            f"- {i18n.t('types')}: {'/'.join(i18n.value('type', x) for x in data['types'])}",
            f"- {i18n.t('stats')}: {_md_stats(data['stats'])}",
            f"- {i18n.t('abilities')}: {_md_join(_md_entity('ability', x) for x in data['abilities'])}",
            f"- {i18n.t('mega')}: {i18n.t('yes' if data['is_mega'] else 'no')}",
        ]
        if data.get("mega_forms"):
            forms = _md_join(
                f"{_md_entity('pokemon', m['name'])}{i18n.parens(_md_entity('item', m['required_item']))}"
                if m.get("required_item") else _md_entity('pokemon', m["name"])
                for m in data["mega_forms"]
            )
            lines.append(f"- {i18n.t('mega_forms')}: {forms}")
        if data.get("required_item"):
            lines.append(f"- {i18n.t('required_item')}: {_md_entity('item', data['required_item'])}")
        if data.get("moves") is not None:
            lines.append(f"- {i18n.t('cached_moves')}{i18n.parens(len(data['moves']))}: "
                         f"{_md_join(_md_entity('move', x) for x in data['moves']) if data['moves'] else i18n.t('none_cached')}")
        lines += _resolution_md(data)
        return "\n".join(lines)
    if "known_users" in data:
        # Priority shown only when non-zero (0 == normal); signed so -7 Trick Room / +2 Extreme Speed read right.
        prio = data.get("priority")
        prio_line = [f"- {i18n.t('priority')}: {prio:+d}"] if isinstance(prio, int) and prio != 0 else []
        effect = _md_effect(data)
        effect_line = [f"- {i18n.t('effect')}: {effect}"] if effect else []
        return "\n".join([
            f"## {_md_head(data)}",
            *(f"- {i18n.t(k)}: {i18n.value(k, data[k]) if k in ('type', 'category') else _md_value(data[k])}"
              for k in ["type", "category", "power", "accuracy", "pp"] if k in data),
            *prio_line,
            *effect_line,
            f"- {i18n.t('known_users')}{i18n.parens(len(data['known_users']))}: "
            f"{_md_join(_md_entity('pokemon', x) for x in data['known_users']) if data['known_users'] else i18n.t('none_cached')}",
            *_resolution_md(data),
        ])
    if "required_by" in data:
        effect = _md_effect(data)
        return "\n".join([
            f"## {_md_head(data)}",
            *([f"- {i18n.t('effect')}: {effect}"] if effect else []),
            f"- {i18n.t('required_by')}: {_md_join(_md_entity('pokemon', x) for x in data['required_by']) if data['required_by'] else i18n.t('none')}",
            *_resolution_md(data),
        ])
    if "up_stat" in data:        # nature
        up, down = data.get("up_stat"), data.get("down_stat")
        effect = f"{i18n.value('stat', up)}↑ / {i18n.value('stat', down)}↓" if up and down else i18n.t("none")
        return "\n".join([
            f"## {_md_head(data)}",
            f"- {i18n.t('nature_effect')}: {effect}",
            *_resolution_md(data),
        ])
    return json.dumps(data, ensure_ascii=False, indent=2)


def emit(data: Any, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(format_md(data))


def emit_error(query: str, code: str, message: str, fmt: str) -> None:
    """A request-level failure: the §3 error shape on stdout for json callers (so they can parse it),
    a plain one-liner on stderr for md callers."""
    if fmt == "json":
        emit({"ok": False, "query": query, "error": {"code": code, "message": message}}, "json")
    else:
        display = i18n.t("error_bad_input") if code == "bad_input" and i18n.lang() != "en" else message
        print(f"{code}: {display}", file=sys.stderr)


# Machine-readable I/O contract (dev/conventions.md), emitted by the `schema` command so an
# AI caller can learn the exact format without reading source. Pinned by the dex contract test.
SCHEMA = {
    "skill": "pokemon-champions-dex",
    "contract": "dev/conventions.md",
    "stat_keys": ["hp", "atk", "def", "spa", "spd", "spe"],
    "error_shape": {"ok": False, "query": "<input>", "error": {"code": "not_found", "message": "<str>"},
                    "possible_kinds?": [{"kind": "item", "canonical": "<name>"}]},
    "commands": {
        "pokemon|move|ability|item|nature": "<name ...> [--format md|json] [--strict]; >1 name -> list; a miss -> error_shape",
        "batch": "<kind> <name ...> [--strict]; always a list; misses inline as error_shape. NOTE kind is "
                 "the FIRST positional — for plain name-cleaning prefer `resolve` (no kind-first foot-gun).",
        "resolve": "<name ...> [--kind pokemon|move|ability|item|nature|type] [--strict]; the AI-facing name "
                   "normalizer. Batch + fuzzy-by-default + Mega-compose (\"Mega耿鬼\"/\"超级耿鬼\"/\"メガ…\" and "
                   "colloquial nicknames and X/Y/Z qualifiers, full-width included, all resolve). A stated "
                   "qualifier never falls back to a different form. ALWAYS a 1:1 list (never drops/merges an input). Each "
                   "item: {query, ok, kind, canonical, display_name, display_name_ja, match_type: "
                   "exact|mega_compose|fuzzy|ambiguous|null, score, +is_mega/base_species/required_item for "
                   "pokemon, suggestions? on miss/ambiguous}.",
        "find|reverse": "<field> <value> ...; ANDed -> {conditions, count, learnset_warning, unresolved_conditions:[{field,value}], results:[pokemon]} (exact only, never fuzzy; unresolved_conditions lists condition keywords that are not a known alias — they filter literally, so a 0 count there is the keyword, not the data). OR `--where '<json>'` for a boolean AND/OR/NOT query (see where_query).",
        "find-move": "<field> <value> ...; reverse-search the MOVES table -> {conditions, count, unresolved_conditions, results:[move]}. Fields: type, category (Physical|Special|Status), power|priority|accuracy|pp (comparison expr like >=90/<40/2), name (substring). e.g. `find-move type Fire category Special power >=90` or `find-move priority >=1`. Also accepts `--where '<json>'` (see where_query).",
        "schema": "this contract",
        "names": "no arguments; JSON list of official {kind,name,name_zh,name_ja} for pokemon/move/ability/item/nature; no aliases or combat data",
    },
    "where_query": {
        "flag": "find / find-move `--where '<json>'` (or '-' to read the JSON from stdin); overrides the "
                "positional shorthand. Malformed query -> bad_input error_shape (exit 1); a valid 0-match "
                "query is a normal empty result (exit 0). Output = the shorthand shape + an echoed `where`.",
        "grammar": "a node is a SINGLE-key object: {\"and\":[node,...]} | {\"or\":[node,...]} | {\"not\":node} "
                   "| a leaf {\"<field>\":<value>}. Nest freely. Unresolved leaf values surface in "
                   "unresolved_conditions (filtered literally, never silently matched).",
        "pokemon_leaves": "type / ability / move / mega (bool) / stat (expr e.g. \"spe>=100\") / item / name; "
                          "plus learns_move_where:<move-query> — pokemon that learn ANY move matching the sub-query.",
        "move_leaves": "type / category / power / priority / accuracy / pp (expr like >=90 or a bare int) / name.",
        "example": "{\"and\":[{\"learns_move_where\":{\"and\":[{\"type\":\"Ice\"},{\"category\":\"Special\"}]}},"
                   "{\"not\":{\"mega\":true}}]}",
    },
    "fuzzy": {
        "default": "pokemon/move/ability/item/batch resolve with a conservative typo fallback BY DEFAULT "
                   "(dex owns naming); exact short-circuits, so a correct name is unaffected. Never fires on a hit.",
        "strict": "--strict disables fuzzy fallback while retaining deterministic Mega composition and "
                  "ambiguity refusal on entity/batch/resolve queries. find/reverse are always exact.",
        "on_hit": "entity gains `resolution: {match_type:'fuzzy', score:float, distance:int, from:'<normalized query>'}`",
        "on_miss": "error_shape may gain `suggestions:[{canonical,distance?,score?}]` (a qualifier-gate lead has no distance); ambiguous ties and qualifier conflicts set error.code='ambiguous' and resolve to NO canonical. Exact cross-kind hits are reported as `possible_kinds` (e.g. pokemon miss that is really an item).",
        "discipline": "ambiguity (>=2 canonicals tie at best distance) -> refuse, never guess; a gender qualifier in the query (♀/♂, -F/-M, female/male, (メス)/(オス)(のすがた), ...) that contradicts a split-gender fuzzy hit (Indeedee vs Indeedee-F) -> refuse as ambiguous with the qualified form first; Han disabled, Kana<=1, Latin<=2 edit distance, length-gated",
    },
    "shapes": {
        "pokemon": {"name": "English canonical", "display_name": "str (zh)", "display_name_ja": "str|null",
                    "national_dex_no": "int|null", "types": ["Type"],
                    "stats": "{hp,atk,def,spa,spd,spe: int}", "abilities": ["str"], "is_mega": "bool",
                    "base_species": "str", "required_item": "str|null",
                    "mega_forms": [{"name": "Mega form", "required_item": "item"}],
                    "moves": ["str (pokemon/batch only)"]},
        "move": {"name": "str", "display_name": "str (zh)", "display_name_ja": "str|null",
                 "type": "Type (Title-case)", "category": "Physical|Special|Status",
                 "power": "int|null", "accuracy": "int|null",
                 "priority": "int (signed speed-priority stage; 0 == normal)", "is_ohko": "bool",
                 "known_users": ["str"]},
        "ability": {"name": "str", "display_name": "str (zh)", "display_name_ja": "str|null", "known_users": ["str"]},
        "item": {"name": "str", "display_name": "str (zh)", "display_name_ja": "str|null", "required_by": ["str"]},
        "nature": {"name": "str (English canonical)", "display_name": "str (zh)", "display_name_ja": "str|null",
                   "up_stat": "smogon stat key|null (raised; null for the 5 neutral natures)",
                   "down_stat": "smogon stat key|null (lowered)"},
    },
}


def _run_where(c: sqlite3.Connection, ns, where_fn, shorthand_fn):
    """Dispatch a find / find-move: `--where <json>` (boolean AST; '-' = stdin) else the positional
    field-value shorthand. On any bad request emits the §3 error shape and returns None (caller ->
    exit 1). A valid query that matches nothing returns a normal 0-count result (exit 0)."""
    if ns.where is not None:
        try:
            ast = json.loads(sys.stdin.read() if ns.where == "-" else ns.where)
        except json.JSONDecodeError as e:
            emit_error(ns.where, "bad_input", f"--where must be valid JSON: {e}", ns.format)
            return None
        try:
            return where_fn(c, ast)
        except BadInput as e:
            emit_error(ns.where, "bad_input", str(e), ns.format)
            return None
    try:
        return shorthand_fn(c, parse_conditions(ns.args))
    except BadInput as e:
        emit_error(" ".join(ns.args), "bad_input", str(e), ns.format)
        return None


def main() -> int:
    global _MD_CONN
    parser = argparse.ArgumentParser(description="Query the offline Pokémon Champions dex.")
    parser.add_argument("command", choices=["pokemon", "move", "ability", "item", "nature", "batch", "resolve", "find", "find-move", "reverse", "schema", "names"])
    parser.add_argument("args", nargs="*")
    parser.add_argument("--format", choices=["md", "json"], default="md")
    parser.add_argument("--kind", choices=["pokemon", "move", "ability", "item", "nature", "type"], default="pokemon",
                        help="resolve: which entity kind the names are (default: pokemon)")
    parser.add_argument("--lang", choices=list(i18n.LANGS),
                        help="language for human-readable md labels / notes (default: "
                             "POKEMON_CHAMPIONS_LANG env, else en). JSON output stays canonical.")
    parser.add_argument("--strict", action="store_true",
                        help="disable the fuzzy fallback (exact only) for pokemon/move/ability/item/batch; "
                             "use when a miss must stay a miss (validation, integrity checks)")
    # Deprecated: fuzzy is now the default, so --fuzzy is a harmless no-op kept for back-compat.
    parser.add_argument("--fuzzy", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--where", help="find / find-move: a boolean JSON query. AND/OR/NOT over leaf "
                        'conditions {"<field>":<value>}; pokemon leaves also take '
                        '{"learns_move_where":<move-query>}. Overrides the positional shorthand; '
                        "'-' reads the JSON from stdin. "
                        'e.g. \'{"and":[{"type":"Dragon"},{"not":{"mega":true}}]}\'')
    ns = parser.parse_args()
    i18n.set_lang(ns.lang)
    fuzzy = not ns.strict
    if ns.command == "schema":
        print(json.dumps(SCHEMA, ensure_ascii=False, indent=2))
        return 0
    c = conn()
    _MD_CONN = c
    try:
        if ns.command == "names":
            if ns.args or ns.where:
                emit_error("names", "bad_input", "names accepts no names or filters", ns.format)
                return 1
            print(json.dumps(official_names(c), ensure_ascii=False, indent=2))
            return 0
        if ns.command == "batch":
            if len(ns.args) < 2:
                emit_error(" ".join(ns.args), "bad_input",
                           "batch needs a KIND then names, e.g. batch pokemon 姆克鹰 巨金怪 "
                           "(kind is the FIRST positional; for plain name-cleaning use `resolve`)", ns.format)
                return 1
            kind, names = ns.args[0], ns.args[1:]
            if kind not in {"pokemon", "move", "ability", "item", "nature"}:
                emit_error(kind, "bad_input",
                           f"batch: unknown kind '{kind}'; expected pokemon|move|ability|item|nature as the "
                           f"FIRST positional (did you mean `resolve {kind} …`, which takes bare names?)", ns.format)
                return 1
            data = []
            for i, n in enumerate(names):
                row = get_one(c, kind, n, fuzzy=fuzzy)
                if isinstance(row, dict) and row.get("ok") is False:
                    row["index"] = i      # batch error items carry their position (contract §3)
                data.append(row)
        elif ns.command == "resolve":
            # AI-facing name normalizer: batch, fuzzy-by-default, Mega-compose. ALWAYS a 1:1 list
            # (every input yields exactly one record, hit or miss) so a caller never has to guess a
            # kind-first positional (`batch`) or lose an input silently.
            if not ns.args:
                # No names is a bad REQUEST (§3), not a fatal crash: emit the uniform error shape so a
                # JSON caller parses `bad_input` instead of a bare SystemExit string (matches `batch`).
                emit_error("", "bad_input",
                           "resolve needs one or more names, e.g. resolve Mega耿鬼 牛蛙君 --kind pokemon",
                           ns.format)
                return 1
            data = [resolve_name(c, ns.kind, n, fuzzy=fuzzy) for n in ns.args]
        elif ns.command in {"pokemon", "move", "ability", "item", "nature"}:
            data = [get_one(c, ns.command, n, fuzzy=fuzzy) for n in ns.args]
            if len(data) == 1:
                data = data[0]
        elif ns.command == "find-move":
            data = _run_where(c, ns, find_move_where, find_move)
            if data is None:
                return 1
        else:
            data = _run_where(c, ns, find_where, find)
            if data is None:
                return 1
        emit(data, ns.format)
    finally:
        _MD_CONN = None
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
