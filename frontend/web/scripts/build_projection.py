#!/usr/bin/env python
"""Build the static data projection the SPA consumes (frontend/design.md §7.1).

Reuses the bridge's mapper + resident workers so projection DTOs are BY CONSTRUCTION the
same shapes the live bridge serves, and derives the deployment id from the same
capabilities assembly — projection and API can never disagree about identity.

Output (default frontend/web/public/projection/, gitignored):
    manifest.json                     {webProtocolVersion, deploymentId, calcEngineDigest, generatedAt, environment}
    capabilities.json                 online handshake (static caps + explicitly enabled API caps)
    dex/cards.json                    all PokemonCardDto (browse index + card lookup)
    dex/{moves,abilities,natures,items}.json  complete trilingual vocabularies
    dex/learners.json                 LearnersDto (move -> slugs; browse filter index)
    meta/ranking_{single,double}.json RankingDto
    meta/detail_{single,double}/<slug>.json   MetaDetailDto (lazy per-pokemon)
    meta/trend_{single,double}.json   TrendDto
    meta/usage_trend_{single,double}/<slug>.json   UsageTrendDto (lazy per-pokemon)
    meta/ko_{single,double}/<slug>.json       MetaKoDto (lazy per-pokemon; own snapshot clock)
    meta/ko_trend_{single,double}/<slug>.json MetaKoTrendDto (lazy; the KO axis's own history)
    meta/facets_{single,double}.json          MetaFacetsDto (filter-rail inverted index)
    meta/top_ranking_{single,double}_{season,rule,high}.json  TopMetaRankingDto (Top Meta, per scope)
    meta/top_detail_{single,double}/<slug>.json          TopMetaDetailDto (lazy; every scope)
    assets/                           image pack (full audit manifest + compact runtime index + files)
    matchup/details_{single,double}/<slug>.json   species builds + reverse damage cells (lazy)
    qa/{matchup,ko}_{single,double}/<form>.json   server-side QA reverse indexes (counters, OHKO/2HKO)
    meta/landscape_{single,double}.json  format structure over the rule's real teams (server-side QA)

    python frontend/web/scripts/build_projection.py [--out DIR] [--ui-build-id ID]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "frontend" / "bridge"))

from pcui import mappers  # noqa: E402
from pcui.capabilities import WEB_PROTOCOL_VERSION, assemble, calc_engine_digest  # noqa: E402
from pcui.paths import META_DATA, SKILLS_ROOT  # noqa: E402
from pcui.worker import WorkerPool  # noqa: E402
from pcui.online import qa_matchups  # noqa: E402

PACK = PROJECT_ROOT / "frontend" / "assets" / "pack"
# Beyond browsing, the static projection also carries:
#  - calc.damage / calc.speedline: computed CLIENT-SIDE in the SPA's calc-engine Web Worker (the
#    vendored NCP engine ported to the browser — frontend/design.md §7.1).
#  - team.matchup: the current-rule opponent cache is shipped STATIC DATA (standard-vs-standard KO grid + derived
#    C2/C1/C0 checks — no user team, no compute), so it exports like ranking/detail and the online
#    adapter reads it directly, same as the bridge maps it live.
# Precise tune (team.tune) is deployment-optional: a static-only site degrades to the client-side
# quick tune, while online-serve exposes the authoritative operator behind workload limits.
ONLINE_CAPABILITIES = ["dex.query", "meta.rank", "meta.detail", "meta.trend",
                       "calc.damage", "calc.speedline", "team.matchup"]
# Deployment-optional capabilities (--capability): advertised ONLY when the release really
# deploys the matching backend next to the static site (llm.qa = `pcui online-serve`,
# design §7.2; team.validate = the deterministic diagnose endpoint, §7.5 — needs the full
# four-skill install). Capabilities gate front-end visibility, not access (§3) — the
# backend enforces its own limits regardless.
OPTIONAL_CAPABILITIES = ("llm.qa", "llm.builder", "team.validate", "team.tune")


def write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n",
                    encoding="utf-8", newline="\n")


def strip_matchup_provenance(doc: dict) -> dict:
    """Keep processed sets while removing real-team attribution from every public view."""
    for st in (doc.get("sets") or {}).values():
        if isinstance(st, dict):
            for field in ("source", "confidence", "note", "realTeamBacked"):
                st.pop(field, None)
    for row in doc.get("species", []):
        if isinstance(row, dict):
            row.pop("setSource", None)
            row.pop("setConfidence", None)
    return doc


def runtime_asset_index(manifest: dict) -> dict:
    """Project the audited asset manifest down to fields the browser resolver actually reads.

    The full manifest remains beside it as the integrity/provenance authority. Shipping that same
    600+ KiB document through the first image request made every repeated hash, dimension and
    provenance string part of the ranking page's critical path even though the UI uses only paths
    and match tiers.
    """
    return {
        "schema_version": "1",
        "assets": [{
            "key": asset["key"],
            "match": asset["match"],
            "files": {role: file["path"] for role, file in asset["files"].items()},
        } for asset in manifest["assets"]],
    }


def write_matchup_details(out: Path, fmt: str, dto: dict, *, native: bool = False,
                          slugs: set[str] | None = None) -> None:
    """Emit bounded detail reads; native misses explicitly refuse expired handover evidence."""
    species_slugs = {row["slug"] for row in dto.get("species", [])} | (slugs or set())
    for slug in sorted(species_slugs):
        detail = mappers.map_oppdetails(dto, slug) if dto.get("species") else None
        suffix = ".native" if native else ""
        write(out / "matchup" / f"details_{fmt}" / f"{slug}{suffix}.json",
              detail if detail is not None else {"available": False})


def write_qa_matchups(out: Path, fmt: str, cache: dict, *, native: bool = False) -> None:
    suffix = ".native" if native else ""
    for folder, shards in (("matchup", qa_matchups.build_shards(cache)), ("ko", qa_matchups.build_ko_shards(cache))):
        for form, doc in shards.items():
            path = out / "qa" / f"{folder}_{fmt}" / f"{mappers.slugify(form)}{suffix}.json"
            write(path, doc)
            if path.stat().st_size > qa_matchups.MAX_SHARD_BYTES:
                raise RuntimeError(f"QA {folder} shard exceeds its read budget: {path.name}")


def load_usage_trend_store(path: Path, detail_slugs: set[str], fmt: str) -> dict:
    """Load the protocol-v5 usage store and prove every current detail has a lazy resource.

    The SPA treats usage trends as part of the v5 projection contract.  Silently skipping a missing
    store (or a current Pokemon absent from it) would still advertise a compatible deployment and
    then 404 only when the user opened a panel preview.
    """
    if not path.is_file():
        raise RuntimeError(f"required {fmt} usage-trend store is missing: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    pokemon = raw.get("pokemon")
    if not isinstance(pokemon, dict):
        raise RuntimeError(f"invalid {fmt} usage-trend store: pokemon must be an object")
    missing = sorted(detail_slugs - set(pokemon))
    if missing:
        preview = ", ".join(missing[:8])
        suffix = " ..." if len(missing) > 8 else ""
        raise RuntimeError(
            f"{fmt} usage-trend store misses {len(missing)} current detail row(s): "
            f"{preview}{suffix}"
        )
    return raw


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(PROJECT_ROOT / "frontend/web/public/projection"))
    ap.add_argument("--ui-build-id", default=None)
    ap.add_argument(
        "--generated-at",
        default=None,
        help="ISO-8601 timestamp for reproducible release builds; defaults to the current UTC time",
    )
    ap.add_argument("--capability", action="append", default=[],
                    choices=list(OPTIONAL_CAPABILITIES), metavar="CAP",
                    help="advertise a deployment-optional capability (repeatable): "
                         f"{', '.join(OPTIONAL_CAPABILITIES)}")
    ns = ap.parse_args()
    out = Path(ns.out)
    # Capabilities gate FRONT-END VISIBILITY, so omitting a flag silently removes working UI
    # (no `llm.qa`/`llm.builder` => the 问答/建队 entries vanish from a runtime that can serve
    # them). A bare rebuild dropping what the previous projection advertised is the easiest way
    # to "break" a local frontend, so say so loudly instead of leaving it to the diff.
    dropped = [c for c in OPTIONAL_CAPABILITIES if c not in ns.capability]
    if dropped:
        flags = " ".join(f"--capability {c}" for c in OPTIONAL_CAPABILITIES)
        print(f"NOTE: not advertising {', '.join(dropped)} — the SPA will hide those features.\n"
              f"      A full local projection (what dev/deploy/build_release.py ships) wants:\n"
              f"      python frontend/web/scripts/build_projection.py {flags}")
    # Check the asset pack up front: copytree(PACK) runs last, so a missing pack used to crash
    # AFTER all JSON was written, leaving a partial projection (no assets/) that `pcui serve`
    # auto-mounts and silently serves as all-placeholder images. Fail before touching `out`.
    if not PACK.is_dir():
        raise SystemExit(f"asset pack not found at {PACK} — run frontend/assets/build_pack.py "
                         "first (refusing to build a projection without images)")
    subprocess.run([sys.executable, str(PROJECT_ROOT / "frontend/assets/build_pack.py"), "validate"],
                   cwd=PROJECT_ROOT, check=True)
    # Build into a sibling staging dir and swap at the end. Wiping `out` up front left the
    # projection ABSENT for the whole build (minutes): a `pcui serve` / `online-serve` already
    # running keeps serving that path, and the SPA treats a 404 on assets/manifest.json as a
    # definitive "no pack shipped" and caches an EMPTY asset index for the rest of the page
    # session — every image not yet resolved turns into permanent placeholder art. The swap
    # keeps that window down to two renames.
    final = out
    staging = final.parent / f".{final.name}.building-{os.getpid()}"
    # Sweep the trees a killed run left behind. Each is named for its own pid, so only the current
    # run's own name was ever cleaned and a crashed build's partial copy sat next to the real
    # projection until someone noticed it. Builds are not run concurrently against one output tree
    # (the swap below already assumes exclusive use of `final`), so any other pid's tree is dead.
    for stale in final.parent.glob(f".{final.name}.building-*"):
        shutil.rmtree(stale, ignore_errors=True)
    for stale in final.parent.glob(f".{final.name}.previous-*"):
        shutil.rmtree(stale, ignore_errors=True)
    if staging.exists():
        shutil.rmtree(staging)
    staging.parent.mkdir(parents=True, exist_ok=True)
    out = staging                      # every write below lands in staging

    pool = WorkerPool()
    try:
        # identity comes from the SAME assembly the bridge serves (atomic binding, §7.1)
        caps = assemble(pool, deployment_id=None, ui_build_id=ns.ui_build_id)
        caps["capabilities"] = ONLINE_CAPABILITIES + [c for c in OPTIONAL_CAPABILITIES
                                                      if c in ns.capability]
        write(out / "capabilities.json", caps)
        generated_at = ns.generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Accept the common trailing-Z spelling while keeping one canonical UTC representation in
        # the projection.  Release automation passes the frozen commit timestamp here, so rebuilding
        # the same commit does not change bytes merely because wall-clock time advanced.
        if generated_at.endswith("Z"):
            generated_at = generated_at[:-1] + "+00:00"
        write(out / "manifest.json", {
            "webProtocolVersion": WEB_PROTOCOL_VERSION,
            "deploymentId": caps["deploymentId"],
            # The engine sources THIS projection was built from — the SPA compares it to its own
            # build-time __ENGINE_DIGEST__ at boot, so a projection rebuilt on a newer engine than the
            # deployed SPA carries is caught instead of silently mis-computing (audit 2026-07-14).
            "calcEngineDigest": calc_engine_digest(),
            "generatedAt": generated_at,
            "environment": caps["environment"],
        })

        # dex cards: every roster entry through the resident dex worker + the real mapper.
        # Browse order: dex number, then base-species group with the base form FIRST and its
        # Megas right after (a plain canonical sort put "Mega Raichu X" before "Raichu").
        import sqlite3
        con = sqlite3.connect(SKILLS_ROOT / "pokemon-champions-dex/data/champions_dex.sqlite")
        roster = con.execute(
            "select canonical, national_dex_no, base_species, is_mega from pokemon "
            "order by national_dex_no, coalesce(base_species, canonical), is_mega, canonical"
        ).fetchall()
        cards = []
        learnset_count = 0
        all_moves: dict[str, dict] = {}
        for name, _no, _base, _mega in roster:
            raw = pool.request_json("dex", ["pokemon", name, "--format", "json"])
            card = mappers.map_pokemon(raw)
            cards.append(card)
            ls = mappers.learnset_dto(name)
            write(out / "dex" / "learnset" / f"{card['slug']}.json", ls)
            learnset_count += len(ls["moves"])
            # Global move vocab, aggregated from the learnsets (name -> trilingual + type +
            # category). Feeds the SPA's prose entity rendering: agent prose references
            # entities by English canonical, the panel localizes with authority — never a
            # hand-written translation.
            for mv in ls["moves"]:
                all_moves.setdefault(mv["name"], mv)
        write(out / "dex" / "cards.json", {"cards": cards})
        # Move -> learners, for the browse filter. One lazy document, because the per-Pokemon
        # learnsets above are lazy: "who learns Fake Out" would otherwise cost a fetch per entry.
        write(out / "dex" / "learners.json", mappers.learners_index())
        # The global move vocabulary is built from the moves TABLE (not the learnset
        # aggregate): the dex browse tab needs every move's numbers + trilingual effect
        # texts once, while learnset rows stay lean (they repeat per pokemon page).
        def _num(v):
            try:
                return int(v) if v not in (None, "", "None", "—") else None
            except ValueError:
                return None
        move_rows = con.execute(
            "select canonical, display_name, display_name_ja, type, category, power, "
            "accuracy, pp, priority, effect_zh, effect_en, effect_ja from moves "
            "where canonical != '(No Move)' order by canonical").fetchall()
        vocab_moves = []
        for r in move_rows:
            entry = {**mappers.named(r[0], r[1], r[2]),
                     "type": mappers.type_name(r[3]), "category": mappers.type_name(r[4]),
                     "power": _num(r[5]), "accuracy": _num(r[6]), "pp": _num(r[7]),
                     "priority": r[8] or 0}
            for val, key in ((r[10], "effect"), (r[9], "effectZh"), (r[11], "effectJa")):
                if val:
                    entry[key] = val
            vocab_moves.append(entry)
        write(out / "dex" / "moves.json", {"moves": vocab_moves})
        print(f"  dex: {len(cards)} cards, {learnset_count} learnset rows, "
              f"{len(all_moves)} moves")

        # Shared entity vocabularies are complete tables, not just the abilities encountered on
        # the current roster.  Tune/AI prose may mention an ability outside the selected six and
        # must still localize it at the presentation boundary.
        abilities = []
        for r in con.execute("select canonical, display_name, display_name_ja, effect_zh, "
                             "effect_en, effect_ja from abilities order by canonical"):
            entry = mappers.named(r[0], r[1], r[2])
            for val, key in ((r[4], "effect"), (r[3], "effectZh"), (r[5], "effectJa")):
                if val:
                    entry[key] = val
            abilities.append(entry)
        write(out / "dex" / "abilities.json", {"abilities": abilities})

        # calculator vocab: natures (26) + items (148), trilingual, tiny static files
        natures = [mappers.map_nature({
            "name": r[0], "display_name": r[1], "display_name_ja": r[2],
            "up_stat": r[3], "down_stat": r[4]})
            for r in con.execute("select canonical, display_name, display_name_ja, up_stat, "
                                 "down_stat from natures order by canonical")]
        write(out / "dex" / "natures.json", {"natures": natures})
        required_by: dict[str, list[str]] = {}
        for r in con.execute("select canonical, required_item from pokemon "
                             "where required_item is not null and required_item != ''"):
            required_by.setdefault(r[1], []).append(r[0])
        items = []
        for r in con.execute("select canonical, display_name, display_name_ja, effect_zh, "
                             "effect_en, effect_ja, category from items order by canonical"):
            entry = {**mappers.named(r[0], r[1], r[2]), "key": f"item:{mappers.slugify(r[0])}"}
            # Mega-stone holders: the calc tabs key their auto-Mega switch on this
            # (its absence silently disabled the feature — user report 2026-07-16).
            if r[0] in required_by:
                entry["requiredBy"] = sorted(required_by[r[0]])
            if r[6]:
                entry["category"] = r[6]     # 52poke's section taxonomy (dex items.category)
            for val, key in ((r[4], "effect"), (r[3], "effectZh"), (r[5], "effectJa")):
                if val:
                    entry[key] = val
            items.append(entry)
        write(out / "dex" / "items.json", {"items": items})
        con.close()
        print(f"  dex vocab: {len(abilities)} abilities, {len(natures)} natures, "
              f"{len(items)} items")

        _current = json.loads((META_DATA / "current.json").read_text(encoding="utf-8"))["current"]
        season = _current["season"]                  # meta snapshots are per-season
        rule = _current["rule"]                      # the opponent cache is per-RULE
        for fmt in ("single", "double"):
            raw = pool.request_json("meta", ["ranking", "--format", fmt, "--limit", "300",
                                             "--output", "json"])
            write(out / "meta" / f"ranking_{fmt}.json", mappers.map_ranking(raw))
            details = json.loads(
                (META_DATA / f"details_{season}_{fmt}.json").read_text(encoding="utf-8"))
            for row in details["rows"]:
                dto = mappers.map_detail(row)
                write(out / "meta" / f"detail_{fmt}" / f"{dto['slug']}.json", dto)
            trend = json.loads(
                (META_DATA / f"trend_{season}_{fmt}.json").read_text(encoding="utf-8"))
            write(out / "meta" / f"trend_{fmt}.json", mappers.map_trend(trend))
            usage_path = META_DATA / f"usage_trend_{season}_{fmt}.json"
            detail_slugs = {str(row.get("slug") or "") for row in details["rows"]}
            usage = load_usage_trend_store(usage_path, detail_slugs - {""}, fmt)
            usage_count = 0
            for slug in usage["pokemon"]:
                write(out / "meta" / f"usage_trend_{fmt}" / f"{slug}.json",
                      mappers.map_usage_trend(usage, slug))
                usage_count += 1
            # KO axis — its own file family, lazy per slug like the details above: the panel sits
            # below the fold, and folding it into detail_*.json would make every page load carry it.
            ko_path = META_DATA / f"ko_{season}_{fmt}.json"
            ko_rows: list = []
            ko_count = 0
            if ko_path.exists():
                ko_doc = json.loads(ko_path.read_text(encoding="utf-8"))
                ko_rows = ko_doc.get("rows") or []
                # `raw` is the CLI ranking, whose rows carry the canonical English join key `name`.
                usage_rank = {r["name"]: r["rank"] for r in raw.get("rows") or [] if r.get("name")}
                for row in ko_rows:
                    dto = mappers.map_ko(ko_doc, row, usage_rank)
                    write(out / "meta" / f"ko_{fmt}" / f"{dto['slug']}.json", dto)
                    ko_count += 1
            # KO history rides with the KO axis, not with the usage trend: same data, same clock.
            # Absent store = absent panel history, which the page renders as "not collected yet"
            # rather than as a flat line.
            ko_trend_path = META_DATA / f"ko_trend_{season}_{fmt}.json"
            ko_trend_count = 0
            if ko_trend_path.exists():
                ko_trend = json.loads(ko_trend_path.read_text(encoding="utf-8"))
                for slug in ko_trend.get("pokemon") or {}:
                    write(out / "meta" / f"ko_trend_{fmt}" / f"{slug}.json",
                          mappers.map_ko_trend(ko_trend, slug))
                    ko_trend_count += 1
            # Filter-rail index. One file per format because the detail files are lazy: a facet
            # filter over the roster would otherwise cost one fetch per entry to answer one question.
            write(out / "meta" / f"facets_{fmt}.json",
                  {"season": season, "rule": rule, "format": fmt,
                   "facets": mappers.build_facets(details["rows"], ko_rows)})
            # Top Meta (竞技环境 / 上位環境): the team skill's comparison, cropped and named by the
            # same mappers the bridge serves live. One detail file per Pokemon carries BOTH scopes,
            # so switching scope on the page costs no request; the in-game page reads the same file
            # for its comparison column.
            top = mappers.top_meta_docs(fmt, META_DATA, season, rule)
            if top is None:
                raise RuntimeError(f"required Top Meta table is missing: {rule}/{fmt}")
            for scope in ("season", "rule", "high"):
                if top.get(scope) is not None:
                    write(out / "meta" / f"top_ranking_{fmt}_{scope}.json",
                          mappers.map_top_ranking(top[scope], top["slug_of"]))
            for top_slug, top_name in top["by_slug"].items():
                write(out / "meta" / f"top_detail_{fmt}" / f"{top_slug}.json",
                      mappers.map_top_detail(top["season"], top["rule"], top_name, top["slug_of"], top["high"]))
            # Format structure (speed-control modes, structural roles, Mega slots, common cores) over the
            # rule's observed real teams. The skill reads the whole library (~35 s per format), so it is
            # computed once here and read by server-side QA, never per request.
            landscape = pool.request_json("team", ["landscape", "--game-format", fmt, "--format", "json"],
                                          timeout=600)
            if not isinstance(landscape, dict) or landscape.get("kind") != "landscape":
                raise RuntimeError(f"team landscape failed for {fmt}")
            write(out / "meta" / f"landscape_{fmt}.json", mappers.map_landscape(landscape))
            print(f"  meta[{fmt}]: ranking + {len(details['rows'])} details + trend"
                  f" + {usage_count} usage trends + {ko_count} ko"
                  f" + {ko_trend_count} ko trends + facets + top meta ({len(top['by_slug'])})")

            # opponent matchup cache (M5): the SAME shipped static cache the bridge maps live — a
            # standard-vs-standard KO grid + derived C2/C1/C0 checks, keyed only by (rule, format).
            # Export both views so the online adapter reads them directly (no user team, no compute);
            # A release advertises team.matchup, so both formats must carry a usable cache.
            cache = mappers.load_oppcache(fmt, rule)
            if cache is None:
                raise RuntimeError(f"required opponent cache is missing: {rule}/{fmt}")
            if (not cache.get("species") or not cache.get("sets") or not cache.get("matrix")
                    or any(not cells for cells in cache["matrix"].values())):
                raise RuntimeError(f"required opponent cache is empty or unusable: {rule}/{fmt}")
            else:
                # §7.1 boundary (maintainer-revised 2026-07-16): the public site must not
                # SHOW/SEARCH/AGGREGATE real teams directly, but PROCESSED per-species sets —
                # the cell inputs the detail panel renders and hands to the calculator — ARE
                # public. Ship them WITHOUT provenance labels (source/confidence/note name the
                # real-team library; the set itself carries no attribution on the site).
                oppcache = mappers.map_oppcache(cache)
                # The LEAN variant x variant grade grid: the page renders build pairs, and the rich
                # per-species view repeated the KO matrix's damage/speed in every cell (~5x larger
                # for facts the client already holds).
                oppchecks = mappers.map_oppcheck_grid(cache, mappers.derive_oppcheck_grid(cache))
                # KO is the default view, but its cells need only a small verdict summary. Keep the
                # full matrix for legacy consumers; result cards read bounded species shards.
                oppko = mappers.map_oppko_grid(cache)
                # Calculator defaults and matchup build previews need only the processed real-build
                # catalog. Shipping that slice separately avoids parsing the 15–17 MB matrix just
                # to fill one Pokemon or show one hover card.
                oppsets = mappers.map_oppsets(cache)
                # Every public view can carry the processed set catalog. Apply one privacy
                # boundary to all of them, including the lightweight calculator catalog.
                for doc in (oppcache, oppko, oppchecks, oppsets):
                    strip_matchup_provenance(doc)
                write(out / "matchup" / f"oppcache_{fmt}.json", oppcache)
                write(out / "matchup" / f"oppko_{fmt}.json", oppko)
                write(out / "matchup" / f"oppchecks_{fmt}.json", oppchecks)
                write(out / "matchup" / f"oppsets_{fmt}.json", oppsets)
                write_matchup_details(out, fmt, oppcache)
                write_qa_matchups(out, fmt, cache)
                if cache.get("built_for", {}).get("handover_receipt"):
                    native = mappers._oppcache_mod().load_cache(fmt, rule, native=True)
                    native_views = ({"oppcache": mappers.map_oppcache(native),
                                     "oppko": mappers.map_oppko_grid(native),
                                     "oppsets": mappers.map_oppsets(native),
                                     "oppchecks": mappers.map_oppcheck_grid(
                                         native, mappers.derive_oppcheck_grid(native))}
                                    if native is not None else
                                    {name: {"available": False} for name in ("oppcache", "oppko", "oppchecks", "oppsets")})
                    if native is not None:
                        write_qa_matchups(out, fmt, native, native=True)
                    for name, doc in native_views.items():
                        strip_matchup_provenance(doc)
                        write(out / "matchup" / f"{name}_{fmt}.native.json", doc)
                    write_matchup_details(out, fmt, native_views["oppcache"], native=True,
                                          slugs={row["slug"] for row in oppcache["species"]})
                print(f"  matchup[{fmt}]: lean KO + check grids + set catalog + detail shards; full cache "
                      "(provenance stripped — public)")
    finally:
        pool.shutdown()

    shutil.copytree(PACK, out / "assets")
    asset_manifest = json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))
    write(out / "assets" / "runtime.json", runtime_asset_index(asset_manifest))
    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())

    # Swap staging into place. Directory os.replace needs an absent destination on Windows, so
    # step the old tree aside first and put it back if the install itself fails.
    previous = final.parent / f".{final.name}.previous-{os.getpid()}"
    if previous.exists():
        shutil.rmtree(previous)
    stepped_aside = False
    if final.exists():
        os.replace(final, previous)
        stepped_aside = True
    try:
        os.replace(out, final)
    except OSError:
        if stepped_aside:
            os.replace(previous, final)
        raise
    if stepped_aside:
        shutil.rmtree(previous, ignore_errors=True)

    print(f"projection: {total / 1048576:.2f} MiB -> {final} (deployment {caps['deploymentId']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
