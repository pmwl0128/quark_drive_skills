#!/usr/bin/env python
"""Bridge to the sibling pokemon-champions-meta skill: a canonical (modal) attacker set.

The tune operator's survival cliffs need a *realistic* threatening attacker. Synthesizing a
generic "max offense, no ability, no item" attacker silently drops set-defining firepower:
Huge Power doubles Azumarill's Play Rough, turning a guaranteed KO into a falsely "survivable"
hit. So instead we read the threat species' modal set from the meta usage panels — the most
common ability / item / nature / EV spread, each with its usage % — and translate the JP/zh
names into the English names the ncp calculator expects.

We model "occurrence" honestly rather than worst-casing:
  - the modelled set is the meta MODE (what the threat most often actually runs);
  - each field carries its usage % so the cliff is auditable;
  - confidence comes from how dominant the modal *ability* is (the damage-critical field): a 98%
    ability is reported high-confidence, a split one medium, with the runner-up surfaced so a
    rarer-but-deadlier variant is visible instead of hidden.

Boundary: this builds the attacker SET for an explicitly-named threat. It must NOT be used to
auto-discover the threat LIST (meta top-K) — marginal-mode stitching is too weak a basis for that.
The threat targets stay explicit in build-context.benchmarks until real joint-set
data exists (M4 real teams / M5 representative sets).

No battle facts live here. Meta names are translated through the sibling dex skill; the species'
overall popularity (rank) is read from the meta ranking. Everything external is injectable so the
logic stays unit-testable offline.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import worker
from stat_keys import ENGINE_STATS

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SKILL_DIR.parent
META_CLI = SKILLS_ROOT / "pokemon-champions-meta" / "scripts" / "meta_query.py"

SPREAD_TO_SPS = ENGINE_STATS
ABILITY_DOMINANT = 80.0   # modal ability usage >= this -> the modelled ability is near-certain
MARGINAL_REPRESENTATIVE = 50.0   # a marginal field whose mode is >= this is broadly representative
# them->us threat surface: the opponent's damaging moves a defender should plan around are the ones a
# meaningful share of the species actually runs. Select ALL damaging moves at/above this usage floor
# (a judgment value, not a fixed slot count — a form's common coverage union can be 3..6 moves), with
# a generous cap as a pure compute guard (truncation is noted, never silent).
MODAL_MOVE_USAGE_FLOOR = 15.0    # percent; >=15% of the species runs this move
MODAL_MOVE_CAP = 6
# The opponent-Choice-Scarf ITEM lane is a DIFFERENT axis from the move floor above: a species runs a
# meaningful non-modal Scarf at this usage. Single source shared by tune (speed advice) and matchup's
# check scarf lane so the two operators never disagree about whether a 10-15% Scarf is worth modelling.
OPPONENT_SCARF_USAGE_FLOOR = 10.0
OPPONENT_SCARF_TOP_N = 3


class MetaUnavailable(RuntimeError):
    pass


def _meta_run(argv: list[str]) -> tuple[int, str]:
    """(returncode, stdout) for a meta_query call — via the resident worker in a session, else a
    one-shot subprocess. Worker errors transparently fall back to one-shot (results identical).

    The worker reports the CLI's real exit code, so callers that branch on it (a graceful meta
    `not_found` is exit 0 by conventions.md §3, a genuine failure is exit 1) see the same value on
    both paths. This used to hard-code 0, which made every worker-served call look successful."""
    if worker.session_active():
        try:
            return worker.run_python("meta", META_CLI, argv)
        except worker.WorkerError:
            pass
    proc = subprocess.run(
        [sys.executable, str(META_CLI), *argv],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
    )
    return proc.returncode, proc.stdout


def _detail(species: str, fmt: str) -> dict[str, Any] | None:
    """Run the meta skill's `detail` command; None when the species isn't ranked / has no panel."""
    if not META_CLI.exists():
        raise MetaUnavailable(f"sibling meta skill not found at {META_CLI}")
    rc, out = _meta_run(["detail", "--format", fmt or "single",
                         "--pokemon", species, "--output", "json"])
    if rc != 0 or not out.strip():
        return None
    try:
        d = json.loads(out)
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) and d.get("panels") else None


def usage_top_k(fmt: str | None, k: int, *,
                ranking_fn: Callable[[str, int], list[dict]] | None = None) -> list[dict[str, Any]]:
    """The meta usage ranking's top-`k` rows — a published fact (the most-used species), used as the
    opponent LIST for matchup. NOT the marginal-mode threat auto-discovery this skill forbids:
    this is the raw usage order, and matchup only *reports* facts against each named species.
    Each row carries at least {rank, pokemon_en, slug}. `ranking_fn` is injectable for tests.
    """
    if ranking_fn is not None:
        return ranking_fn(fmt or "single", k)
    if not META_CLI.exists():
        raise MetaUnavailable(f"sibling meta skill not found at {META_CLI}")
    rc, out = _meta_run(["ranking", "--format", fmt or "single",
                         "--limit", str(k), "--output", "json"])
    if rc != 0 or not out.strip():
        raise MetaUnavailable("meta ranking query failed")
    try:
        d = json.loads(out)
    except json.JSONDecodeError as e:
        raise MetaUnavailable(f"meta ranking returned non-JSON: {e}") from e
    return list(d.get("rows", []))[:k]


def _translate(kind: str, ja: str | None, zh: str | None,
               resolver: Callable[[list[str]], dict[str, dict[str, Any]]]) -> str | None:
    """Resolve a meta JP/zh name to the English canonical via the dex; JP first, then zh."""
    for cand in (ja, zh):
        if not cand:
            continue
        row = resolver([cand]).get(cand) or {}
        name = row.get("name") or row.get("canonical")
        if (row.get("found") or row.get("ok")) and name:
            return name
    return None


def _details_batch(species_list: list[str], fmt: str) -> dict[str, dict | None]:
    """Fetch meta detail panels for many species in ONE meta_query call (the panel data file is
    loaded once instead of once per species). Returns {species: detail|None}, None where unranked."""
    if not species_list:
        return {}
    if not META_CLI.exists():
        raise MetaUnavailable(f"sibling meta skill not found at {META_CLI}")
    unique_names = list(dict.fromkeys(species_list))
    rc, out = _meta_run(["detail", "--format", fmt or "single",
                         "--pokemon", *unique_names, "--output", "json"])
    if rc != 0 or not out.strip():
        return {sp: None for sp in species_list}
    try:
        rows = json.loads(out)
    except json.JSONDecodeError:
        return {sp: None for sp in species_list}
    if isinstance(rows, dict):           # single-name shape (shouldn't happen for >1, but be safe)
        rows = [rows]
    by_query: dict[str, dict | None] = {}
    by_canon: dict[str, dict | None] = {}
    for sp, row in zip(unique_names, rows):
        detail = row if isinstance(row, dict) and row.get("panels") else None
        by_query[sp] = detail
        if detail:
            for key in (detail.get("name"), detail.get("pokemon_en")):
                if key:
                    by_canon[str(key)] = detail
    return {sp: by_query.get(sp) or by_canon.get(sp) for sp in species_list}


def _set_name_candidates(d: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """The ability/item/nature ja+en names a detail will translate."""
    panels = d.get("panels", {})
    abilities = panels.get("abilities") or []
    items = panels.get("items") or []
    natures = panels.get("natures") or []
    ab_names = [x for row in abilities for x in (row.get("name_ja"), row.get("name")) if x]
    it_names = [x for row in items for x in (row.get("name_ja"), row.get("name")) if x]
    na_names = [x for row in natures for x in (row.get("name_ja"), row.get("name")) if x]
    return ab_names, it_names, na_names


def nature_distribution(species: str, fmt: str | None, *,
                        detail_fn: Callable[[str, str], dict | None] | None = None,
                        nature_fn: Callable[[list[str]], dict] | None = None,
                        threshold: float = 2.0) -> dict[str, float]:
    """English nature -> usage % for natures `species` ACTUALLY runs at >= `threshold`% in meta (detail
    `natures` panel). This is the REALITY GATE for §16.8 nature lanes: a candidate nature is considered
    only if real players run it (default floor ~2%). Returns a DICT so the lane can carry the real
    usage % (a 2% nature and a 60% nature are NOT equally endorsed — audit 2026-06-24); it still works
    as a membership set (`n in dist`). Empty on a meta miss/unranked species (no lanes can be grounded).
    Injectable for offline tests."""
    d = (detail_fn or _detail)(species, fmt or "single")
    if not d:
        return {}
    if nature_fn is None:
        from dexlink import resolve_names
        nature_fn = lambda names: resolve_names(names, "nature", fuzzy=False)  # noqa: E731
    names = sorted({x for r in (d.get("panels", {}).get("natures") or [])
                    for x in (r.get("name_ja"), r.get("name")) if x})
    nmap = nature_fn(names) if names else {}
    n_res = lambda qs: {n: nmap.get(n, {}) for n in qs}  # noqa: E731
    out: dict[str, float] = {}
    for r in (d.get("panels", {}).get("natures") or []):
        pct = float(r.get("percentage") or 0.0)
        if pct >= threshold:
            en = _translate("nature", r.get("name_ja"), r.get("name"), n_res)
            if en and en not in out:
                out[en] = round(pct, 1)
    return out


def canonical_attacker_set(species: str, fmt: str | None, *,
                           detail_fn: Callable[[str, str], dict | None] | None = None,
                           ability_fn: Callable[[list[str]], dict] | None = None,
                           item_fn: Callable[[list[str]], dict] | None = None,
                           nature_fn: Callable[[list[str]], dict] | None = None,
                           rank_pct: float | None = None) -> dict[str, Any] | None:
    """Build the modal attacker set for `species` from meta, translated to ncp English names.

    Returns None if meta has no detail for the species (caller falls back to a synthetic set and
    flags low confidence). Otherwise returns:
      {ability, item, nature, sps, set:{<field>:{name,pct}}, prevalence, confidence, note}
    `prevalence` blends the species' meta popularity (rank) with how canonical its set is.
    All dex/meta access is injected so this is unit-testable without the sibling skills.
    For many species at once, prefer `canonical_attacker_sets` (one meta + one dex batch each).
    """
    detail_fn = detail_fn or _detail
    d = detail_fn(species, fmt or "single")
    if not d:
        return None
    if ability_fn is None or item_fn is None or nature_fn is None:
        from dexlink import lookup_abilities, lookup_items, resolve_names  # lazy: keep the module import-light
        ability_fn = ability_fn or lookup_abilities
        item_fn = item_fn or lookup_items
        nature_fn = nature_fn or (lambda names: resolve_names(names, "nature", fuzzy=False))
    return _build_set(d, ability_fn=ability_fn, item_fn=item_fn, nature_fn=nature_fn,
                      rank_pct=rank_pct)


def canonical_attacker_sets(species_list: list[str], fmt: str | None, *,
                            details_fn: Callable[[list[str], str], dict] | None = None,
                            ability_fn: Callable[[list[str]], dict] | None = None,
                            item_fn: Callable[[list[str]], dict] | None = None,
                            nature_fn: Callable[[list[str]], dict] | None = None,
                            move_fn: Callable[[list[str]], dict] | None = None,
                            rank_pcts: dict[str, float] | None = None) -> dict[str, dict | None]:
    """Modal attacker sets for many species with batched I/O: ONE meta detail call + ONE
    lookup_abilities + ONE lookup_items for the whole list (vs ~3 subprocesses per species the
    single `canonical_attacker_set` would spend). Returns {species: set|None}. Same per-species
    result as the single function; only the call count differs. Injectable for offline tests."""
    fmt = fmt or "single"
    details = (details_fn or _details_batch)(species_list, fmt)
    if ability_fn is None or item_fn is None or nature_fn is None or move_fn is None:
        from dexlink import lookup_abilities, lookup_items, lookup_moves, resolve_names
        ability_fn = ability_fn or lookup_abilities
        item_fn = item_fn or lookup_items
        nature_fn = nature_fn or (lambda names: resolve_names(names, "nature", fuzzy=False))
        move_fn = move_fn or lookup_moves
    # One translation batch each for every ability / item / nature / move name across all species.
    all_ab: set[str] = set()
    all_it: set[str] = set()
    all_na: set[str] = set()
    all_mv: set[str] = set()
    for sp in species_list:
        d = details.get(sp)
        if not d:
            continue
        a, i, n = _set_name_candidates(d)
        all_ab.update(a)
        all_it.update(i)
        all_na.update(n)
        for r in (d.get("panels", {}).get("moves") or []):   # only the >=floor damaging moves _build_set will use
            if r.get("category") in ("Physical", "Special") and float(r.get("percentage") or 0.0) >= MODAL_MOVE_USAGE_FLOOR:
                all_mv.update(x for x in (r.get("name_ja"), r.get("name")) if x)
    # Ability / item / nature / move translations are independent dex processes — run them concurrently
    # (subprocess.run releases the GIL while waiting, so the spawns overlap).
    import concurrent.futures as _cf
    with _cf.ThreadPoolExecutor(max_workers=4) as _ex:
        ab_fut = _ex.submit(ability_fn, sorted(all_ab)) if all_ab else None
        it_fut = _ex.submit(item_fn, sorted(all_it)) if all_it else None
        na_fut = _ex.submit(nature_fn, sorted(all_na)) if all_na else None
        mv_fut = _ex.submit(move_fn, sorted(all_mv)) if all_mv else None
        ab_map = ab_fut.result() if ab_fut else {}
        it_map = it_fut.result() if it_fut else {}
        na_map = na_fut.result() if na_fut else {}
        mv_map = mv_fut.result() if mv_fut else {}
    ab_res = lambda names: {n: ab_map.get(n, {}) for n in names}   # noqa: E731 (prefetched resolver)
    it_res = lambda names: {n: it_map.get(n, {}) for n in names}   # noqa: E731
    na_res = lambda names: {n: na_map.get(n, {}) for n in names}   # noqa: E731
    mv_res = lambda names: {n: mv_map.get(n, {}) for n in names}   # noqa: E731
    rank_pcts = rank_pcts or {}
    out: dict[str, dict | None] = {}
    for sp in species_list:
        d = details.get(sp)
        out[sp] = _build_set(d, ability_fn=ab_res, item_fn=it_res, nature_fn=na_res, move_fn=mv_res,
                             rank_pct=rank_pcts.get(sp)) if d else None
    return out


def _build_set(d: dict[str, Any], *, ability_fn: Callable[[list[str]], dict],
               item_fn: Callable[[list[str]], dict], nature_fn: Callable[[list[str]], dict],
               move_fn: Callable[[list[str]], dict] | None = None,
               rank_pct: float | None = None) -> dict[str, Any]:
    """Assemble the modal set from one detail dict. `ability_fn`/`item_fn`/`move_fn` resolve names to
    the English canonical (a real dex batch for a single call, or a prefetched-map closure in batch).
    `move_fn` is optional: pass it to surface the top damaging modal moves (for them->us damage)."""
    panels = d.get("panels", {})

    def top(name: str) -> dict[str, Any]:
        rows = panels.get(name) or []
        return rows[0] if rows else {}

    ab, it, na, sp = top("abilities"), top("items"), top("natures"), top("spreads")
    ability = _translate("ability", ab.get("name_ja"), ab.get("name"), ability_fn)
    ability_dist: list[dict[str, Any]] = []
    for row in (panels.get("abilities") or []):
        ab_en = _translate("ability", row.get("name_ja"), row.get("name"), ability_fn)
        if ab_en:
            ability_dist.append({"name": ab_en, "pct": float(row.get("percentage") or 0.0)})
    item = _translate("item", it.get("name_ja"), it.get("name"), item_fn)
    nature = _translate("nature", na.get("name_ja"), na.get("name"), nature_fn)
    # Choice Scarf presence for the outspeed cliff (a scarfed target is 1.5x faster), matched through
    # the dex canonical item name so feed language/key changes do not silently drop the speed modifier.
    # Translate EVERY panel item name in one item_fn call (a dex subprocess in the single path), then
    # scan the prefetched map — not one _translate (= one subprocess) per row.
    panel_items = panels.get("items") or []
    _item_names = sorted({x for row in panel_items for x in (row.get("name_ja"), row.get("name")) if x})
    _item_map = item_fn(_item_names) if _item_names else {}
    _item_res = lambda names: {n: _item_map.get(n, {}) for n in names}   # noqa: E731 (prefetched resolver)
    choice_scarf = None
    for _i, _row in enumerate(panel_items, start=1):
        if _translate("item", _row.get("name_ja"), _row.get("name"), _item_res) == "Choice Scarf":
            choice_scarf = {"rank": _i, "pct": float(_row.get("percentage") or 0.0)}
            break
    sps = {SPREAD_TO_SPS[k]: int(sp.get(k) or 0) for k in SPREAD_TO_SPS if k in sp} if sp else {}
    # Damaging modal moves at/above the usage floor — the them->us threat surface (status deals no
    # damage, so skip it). MARGINAL like ability/item: each move's % is its own usage marginal, NOT a
    # guaranteed joint moveset — the consumer (matchup them->us) carries that caveat. Selection is the
    # usage floor (not a fixed slot count); the cap is a pure compute guard. Built only with a resolver.
    qualifying: list[dict[str, Any]] = []
    if move_fn is not None:
        for r in (panels.get("moves") or []):
            if r.get("category") not in ("Physical", "Special"):
                continue
            pct = float(r.get("percentage") or 0.0)
            if pct < MODAL_MOVE_USAGE_FLOOR:
                continue
            mv = _translate("move", r.get("name_ja"), r.get("name"), move_fn)
            if mv:
                qualifying.append({"name": mv, "pct": pct})
    moves = qualifying[:MODAL_MOVE_CAP]

    ability_pct = float(ab.get("percentage") or 0.0)
    item_pct = float(it.get("percentage") or 0.0)
    nature_pct = float(na.get("percentage") or 0.0)
    # IMPORTANT: ability / item / nature are INDEPENDENT meta marginals (the spread row is the only
    # real joint sub-fact), so stitching their modes can name a combo that never actually co-occurs
    # (the marginal-stitch trap). Be honest about it: 'high' only when the ability is near-certain AND
    # the item/nature modes are themselves broadly representative; otherwise 'medium'.
    marginal_combo = item_pct >= MARGINAL_REPRESENTATIVE and nature_pct >= MARGINAL_REPRESENTATIVE
    confidence = "high" if (ability_pct >= ABILITY_DOMINANT and marginal_combo) else "medium"
    note_bits = [f"meta modal set: {ability or '?'} {ability_pct:.0f}%",
                 f"{item or '?'} {item_pct:.0f}%", f"{nature or '?'} {nature_pct:.0f}%",
                 "fields are independent meta marginals (spread is a real joint EV row) — the exact "
                 "ability+item+nature combo may not co-occur"]
    abilities = panels.get("abilities") or []
    if len(abilities) > 1 and ability_pct < 95.0:
        alt = abilities[1]
        alt_en = _translate("ability", alt.get("name_ja"), alt.get("name"), ability_fn) or alt.get("name")
        note_bits.append(f"also {alt_en} {float(alt.get('percentage') or 0):.0f}% — "
                         "a rarer set may hit differently")
    if len(qualifying) > MODAL_MOVE_CAP:
        note_bits.append(f"{len(qualifying)} damaging moves >={MODAL_MOVE_USAGE_FLOOR:.0f}% usage; "
                         f"them->us uses the top {MODAL_MOVE_CAP} by usage")

    # Popularity prior. Use REAL usage % when available. When it isn't, do NOT fabricate a usage-like
    # number from ordinal rank (`1.0 - (rank-1)*0.01`): rank is not usage — #20 vs #21 isn't necessarily
    # 1% apart — and that fake value fed the cliff score as if it were prevalence (audit 2026-06-24).
    # Fall back to a NEUTRAL weight and flag the basis; the ordinal `rank` stays exposed for the caller.
    rank = d.get("rank")
    if rank_pct is not None:
        prevalence = max(0.2, min(1.0, rank_pct / 100.0))
        prevalence_basis = "usage"
    else:
        prevalence = 0.5
        prevalence_basis = "neutral (no usage %; rank is ordinal only — not a usage proxy)"

    return {
        "ability": ability, "item": item, "nature": nature, "sps": sps, "moves": moves,
        "set": {
            "ability": {"name": ability, "pct": ability_pct},
            "abilities": ability_dist,
            "item": {"name": item, "pct": float(it.get("percentage") or 0.0)},
            "nature": {"name": nature, "pct": float(na.get("percentage") or 0.0)},
            "spread": {"pct": float(sp.get("percentage") or 0.0)},
        },
        "rank": rank, "prevalence": round(prevalence, 3), "prevalence_basis": prevalence_basis,
        "confidence": confidence, "choice_scarf": choice_scarf,
        "note": "; ".join(note_bits),
    }
