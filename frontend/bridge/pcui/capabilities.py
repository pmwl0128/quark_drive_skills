"""Capabilities handshake assembly (frontend/design.md §3): one pass at startup over the four
skills' `schema` (+ team `vocab`) output AND their shipped data trees — a fingerprint must
identify the actually-installed contract *and data version* (a meta refresh or dex rebuild
with an unchanged contract still changes the fingerprint; external audit 2026-07-13). The
environment comes from the meta skill's current.json."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from . import __version__
from .paths import META_DATA, SKILLS_ROOT
from .worker import WorkerPool

# Must track frontend/protocol WEB_PROTOCOL_VERSION — bumped 1 -> 2 for the breaking
# ItemPanelEntryDto.key change, then 2 -> 3 for OppCacheDto.sets going REQUIRED -> optional so lean
# projections may omit it (a pre-3 SPA can't parse every v3 projection; external audit 2026-07-14),
# then 3 -> 4 because the default KO page requires the new lean `view=ko` / `oppko_<format>.json`
# resource before lazily loading full details, and 4 -> 5 for lazy per-Pokemon panel usage trends.
# The version is folded into the
# local deploymentId below so a DTO/mapper release is a new deployment identity even when skill data
# is unchanged.
# Version 6 requires clients to stop consuming transitional team evidence at expiry.
# Version 7 adds the lightweight opponent-set catalog used by calculator defaults and build previews.
# Version 8 requires environment.sourceFingerprints, the data identity that derived views were built from.
# Version 9 adds per-species matchup detail shards and the local details view.
WEB_PROTOCOL_VERSION = "9"

# Ids derived from the installed skill snapshot. A deployed release carries a `release-<hash>`
# id instead, bound to the whole payload rather than to the skill data alone.
_LOCAL_ID_PREFIX = "local-"

# What THIS bridge actually serves (llm.* are online-only; team.uep gates the local UEP
# session panel — sessions/artifacts/SSE plus its lazy chunk).
LOCAL_CAPABILITIES = [
    "dex.query", "meta.rank", "meta.detail", "meta.trend",
    "calc.damage", "calc.speedline",
    "team.validate", "team.matchup", "team.tune", "team.uep", "team.code",
]

# Shipped data trees per fingerprint id (calc's "data" is the vendored engine+tables).
_DATA_TREES: dict[str, Path] = {
    "dex": SKILLS_ROOT / "pokemon-champions-dex/data",
    "meta": SKILLS_ROOT / "pokemon-champions-meta/data",
    "calc": SKILLS_ROOT / "ncp-damage-calculator/scripts/script_res",
    "team": SKILLS_ROOT / "pokemon-champions-team/data",
}

# calc's runtime is script_res (data tables) PLUS the two JS wrappers that drive them (buildPokemon /
# calculate / speedOne + the I/O contract). A wrapper BEHAVIOR change that leaves the `schema` output
# untouched must STILL change calc identity — otherwise a projection built on new wrapper logic shares a
# deploymentId with a SPA that ported the old wrapper into its worker (external audit 2026-07-14). Their
# content is folded into the calc fingerprint alongside script_res.
_CALC_WRAPPERS = [
    SKILLS_ROOT / "ncp-damage-calculator/scripts/ncp-calc-api.js",
    SKILLS_ROOT / "ncp-damage-calculator/scripts/ncp-speedline-api.js",
]


def _fingerprint(*docs: object) -> str:
    payload = json.dumps(docs, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


# SQLite writes transient sidecars (-wal / -shm, and -journal in rollback mode) NEXT TO the DB,
# inside the hashed data tree. A WAL-mode DB mutates them on mere READS (and checkpoint-on-close
# rewrites the -wal), so hashing them made the deployment identity DRIFT the moment any process
# queried the dex — projection built at identity A, first served query flipped it to B, and the
# atomic-binding guard then refused to render (external report 2026-07-14). They are not part of
# the shipped data VERSION (the .sqlite file is); exclude them so identity is stable across reads.
_TRANSIENT_SUFFIXES = ("-wal", "-shm", "-journal")


def _data_digest(tree: Path) -> str:
    """Content hash over a shipped data tree (relative path + bytes, sorted), excluding transient
    SQLite sidecars. ~40MB across all four skills, computed once per process on the first
    capabilities request."""
    h = hashlib.sha256()
    if tree.is_dir():
        # Path ordering follows the host flavour (WindowsPath compares case-insensitively while
        # PosixPath does not). The shipped trees contain mixed-case names such as damage_MASTER.js
        # and M-4_double.json, so relying on Path.__lt__ made identical bytes produce a different
        # deploymentId on the Windows projection builder and Linux VPS. Sort the wire-format key
        # explicitly: a POSIX relative-path string has the same order on every host.
        for p in sorted(tree.rglob("*"), key=lambda item: item.relative_to(tree).as_posix()):
            if p.is_file() and not p.name.endswith(_TRANSIENT_SUFFIXES):
                h.update(p.relative_to(tree).as_posix().encode("utf-8"))
                h.update(b"\0")
                h.update(p.read_bytes())
    return h.hexdigest()


def _files_digest(paths: list[Path]) -> str:
    """Content hash over an explicit file list (name + bytes, name-sorted) — for the calc wrappers,
    which live one level up from the hashed script_res tree."""
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda x: x.name):
        h.update(p.name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()


def calc_engine_digest() -> str:
    """SHA-256 over the vendored calc engine sources (script_res/*.js + the two JS wrappers), basename-
    sorted and newline-normalized, each entry `name\\0content` joined by \\0. MUST byte-match the SPA's
    build-time __ENGINE_DIGEST__ (frontend/web/vite.config.ts calcEngineDigest), so the online runtime binds
    the engine it embedded in its worker to the projection it serves — a projection rebuilt on a newer
    engine than the SPA carries is then caught at the handshake, not silently mis-computed (external
    audit 2026-07-14). Distinct from the calc FINGERPRINT (which also folds in the schema + is truncated
    for the deployment id); this is the raw-source binding the browser can reproduce. Hashes RAW BYTES
    (not decoded text): some data files carry non-UTF-8 bytes and JS vs Python replace them differently
    on decode, so a text hash wouldn't agree — normalize CRLF->LF at the byte level, hash `name\\0bytes\\0`."""
    script_res = SKILLS_ROOT / "ncp-damage-calculator/scripts/script_res"
    entries = sorted(list(script_res.glob("*.js")) + _CALC_WRAPPERS, key=lambda p: p.name)
    h = hashlib.sha256()
    for p in entries:
        h.update(p.name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
        h.update(b"\0")
    return h.hexdigest()


def source_fingerprints(season: str, rule: str) -> dict[str, dict[str, str]]:
    """Per-format identity of the mutable data the derived views come from: the season's meta panels
    and the real-team partitions feeding the rule. These are the opponent cache's own validity hashes
    (`oppcache.source_fingerprints`), so a cache that loads at all was built from exactly these inputs.
    Unlike deploymentId they ignore code, so a consumer can tell "same data, new release" apart from
    "the data moved"."""
    from . import mappers
    oppcache = mappers._oppcache_mod()
    out = {}
    for fmt in ("single", "double"):
        prints = oppcache.source_fingerprints(fmt, rule, season)
        out[fmt] = {"meta": prints["meta"], "teamLibrary": prints["team_library"]}
    return out


def assemble(pool: WorkerPool, deployment_id: str | None = None,
             ui_build_id: str | None = None, report: dict | None = None) -> dict:
    """`report`, when given, receives `projectionStale` for the local Console's status card."""
    data = {k: _data_digest(v) for k, v in _DATA_TREES.items()}
    fingerprints = {
        "dex": _fingerprint(pool.request_json("dex", ["schema"]), data["dex"]),
        "meta": _fingerprint(pool.request_json("meta", ["schema"]), data["meta"]),
        "calc": _fingerprint(pool.request_json("calc", ["schema"]),
                             pool.request_json("speedline", ["schema"]), data["calc"],
                             _files_digest(_CALC_WRAPPERS)),
        "team": _fingerprint(pool.request_json("team", ["schema"]),
                             pool.request_json("team", ["vocab", "--format", "json"]),
                             data["team"]),
    }
    # The "deployment" IS the installed skill snapshot: derive its id from the combined fingerprints
    # AND the wire-protocol version, so a breaking DTO/mapper change (which bumps
    # WEB_PROTOCOL_VERSION) is a new deployment identity even when skill data is unchanged (external
    # audit 2026-07-14: DTO version was previously absent from this fingerprint).
    derived = _LOCAL_ID_PREFIX + _fingerprint(fingerprints, WEB_PROTOCOL_VERSION)[:12]
    if deployment_id is None:
        deployment_id = derived
    elif deployment_id.startswith(_LOCAL_ID_PREFIX) and deployment_id != derived:
        # A supplied id comes from the projection manifest, and the SPA's drift guard compares that
        # same manifest against what this handshake returns — so adopting it in silence made that
        # comparison check the manifest against itself. `pcui serve` defaults `--projection` to the
        # dev checkout's own build, which `update.py refresh` routinely leaves behind, and that is
        # exactly the case the guard was for.
        #
        # Only `local-` ids are comparable: `build_release.py` rebinds a deployed projection to its
        # `release-<payload hash>` identity, which is a different preimage and would differ here
        # forever. Report rather than refuse — a deployed bridge that will not answer its own
        # handshake is worse than one serving a named mismatch.
        if report is not None:
            report["projectionStale"] = True
        print(f"WARNING: serving projection {deployment_id} against skill data that fingerprints "
              f"as {derived}. The static pages are stale; rebuild the projection "
              f"(frontend/web/scripts/build_projection.py).", file=sys.stderr, flush=True)
    current = json.loads((META_DATA / "current.json").read_text(encoding="utf-8"))
    season = current["current"]["season"]
    env = {"season": season, "rule": current["current"]["rule"]}
    as_of = (current.get("seasons", {}).get(season, {}).get("updated_at") or "")[:10]
    if as_of:
        env["asOf"] = as_of
    env["sourceFingerprints"] = source_fingerprints(season, env["rule"])
    return {
        "webProtocolVersion": WEB_PROTOCOL_VERSION,
        "deploymentId": deployment_id,
        # Real build id once the wheel embeds the SPA dist; until then the bridge version.
        "uiBuildId": ui_build_id or f"pcui-{__version__}",
        "skillFingerprints": fingerprints,
        "environment": env,
        "capabilities": LOCAL_CAPABILITIES,
    }
