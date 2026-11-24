#!/usr/bin/env python
"""Audited, temporary cross-regulation access to real-team evidence.

The receipt never relabels or copies source rows. It binds accepted row digests to the original
partitions, the exact reviewed target ruleset, the target meta activation timestamp, and a hard
expiry. A malformed or stale receipt fails closed while its target is current.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import rules

SCHEMA_VERSION = 1
HARD_MAX_DURATION = timedelta(days=7)
_NATIVE_ONLY = ContextVar("team_native_only", default=False)


@contextmanager
def native_only():
    """Build/read the target team's own evidence without changing any source or authority file."""
    token = _NATIVE_ONLY.set(True)
    try:
        yield
    finally:
        _NATIVE_ONLY.reset(token)
_VALIDATOR_FILES = (
    "dexlink.py", "handover.py", "mega.py", "rules.py", "team_io.py", "validate_team.py",
)

# Re-proving a receipt costs a dex read, six validator reads, and a full re-hash + re-parse of every
# source partition — ~2s for a production doubles library. Callers ask per species, so paying it on
# every call made the whole transition week roughly 4x slower than steady state. Memoize on a cheap
# (path, mtime_ns, size) signature of exactly the files the proof reads: any edit to a bound input
# changes the signature and forces the full proof again, and the expiry is re-checked on every call
# regardless, so a cached pre-expiry answer can still never outlive the window.
#
# Scope: a PROCESS-LOCAL read accelerator, never the authority. Content hashing still happens where a
# result is committed to — the cache builder re-fingerprints real bytes before installing, and the
# release gate re-proves the whole receipt from the Git commit. The memo therefore trades only the
# in-session detection of an edit that preserved both mtime_ns and size, which no refresh produces.
#
# Both are keyed by IDENTITY and hold the signature beside the value, so a changed input replaces
# its entry instead of adding one. A long-lived bridge sees a new signature after every data refresh,
# and an accepted-row list is tens of thousands of dicts — versioned keys would retain every
# generation for the life of the process.
_AUTHORITY_CACHE: dict[tuple[str, str],
                       tuple[tuple[Any, ...], dict[str, Any] | None, datetime | None]] = {}
_ROWS_CACHE: dict[tuple[str, str, str], tuple[tuple[Any, ...], list[dict[str, Any]]]] = {}


class HandoverError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def row_sha256(row: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(row)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def binary_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validator_sha256() -> str:
    """Bind the receipt to the exact code that interpreted target-rule legality."""
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in _VALIDATOR_FILES:
        path = root / name
        if not path.is_file():
            raise HandoverError(f"handover validator authority is missing: {path}")
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()


def dex_path(team_dir: Path) -> Path:
    override = os.environ.get("CHAMP_DEX_DB")
    if override:
        return Path(override)
    skill_dir = team_dir.parents[1]
    return skill_dir.parent / "pokemon-champions-dex" / "data" / "champions_dex.sqlite"


def dex_sha256(team_dir: Path) -> str:
    path = dex_path(team_dir)
    if not path.is_file():
        raise HandoverError(f"handover dex authority is missing: {path}")
    return binary_sha256(path)


def ruleset_sha256(rule: str) -> str:
    return hashlib.sha256(canonical_bytes(asdict(rules.get_ruleset(rule=rule)))).hexdigest()


def receipt_path(team_dir: Path) -> Path:
    return team_dir / "handover.json"


def _meta_current_path(team_dir: Path) -> Path:
    override = os.environ.get("CHAMP_META_CURRENT")
    if override:
        return Path(override)
    skill_dir = team_dir.parents[1]
    return skill_dir.parent / "pokemon-champions-meta" / "data" / "current.json"


def _parse_time(value: Any, field: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise HandoverError(f"handover {field} is not an ISO timestamp: {value!r}") from exc
    if stamp.tzinfo is None:
        raise HandoverError(f"handover {field} must carry a timezone")
    return stamp.astimezone(timezone.utc)


def _now() -> datetime:
    override = os.environ.get("CHAMP_HANDOVER_NOW")
    return _parse_time(override, "test-now") if override else datetime.now(timezone.utc)


def _stat_signature(paths: list[Path]) -> tuple[Any, ...]:
    """Cheap change detector for a fixed set of local files (missing files included explicitly)."""
    signature: list[Any] = []
    for path in paths:
        try:
            info = path.stat()
            signature.append((str(path), info.st_mtime_ns, info.st_size))
        except OSError:
            signature.append((str(path), None, None))
    return tuple(signature)


def _authority_signature(team_dir: Path) -> tuple[Any, ...]:
    """Signature of every file `load_active_receipt` proves the receipt against."""
    root = Path(__file__).resolve().parent
    return _stat_signature([
        receipt_path(team_dir), _meta_current_path(team_dir), dex_path(team_dir),
        *(root / name for name in _VALIDATOR_FILES),
    ])


def pool_token(team_dir: Path, fmt: str, target_rule: str) -> tuple[Any, ...]:
    """A cheap token that changes whenever a rule pool's contents would change or expire.

    THE invalidation key for a process-local rule-pool cache. It covers the receipt's own bound
    authorities, every shipped partition file for the format (so a dev refresh writing a partition
    invalidates the pool without an explicit clear), and whether the window is still open — so the
    cached pool flips exactly once, at the hard expiry, without re-proving the receipt each call.
    """
    return (target_rule, _authority_signature(team_dir),
            _stat_signature(sorted(team_dir.glob(f"*_{fmt}.jsonl"))),
            load_active_receipt(team_dir, target_rule) is not None)


def load_active_receipt(team_dir: Path, target_rule: str) -> dict[str, Any] | None:
    if _NATIVE_ONLY.get():
        return None
    key = (str(team_dir.resolve()), target_rule)
    signature = _authority_signature(team_dir)
    cached = _AUTHORITY_CACHE.get(key)
    if cached is not None and cached[0] == signature:
        _, receipt, expires = cached
        # The window is the one thing that moves without any file changing, so it is re-checked here
        # rather than trusted from the memo.
        if expires is None or _now() < expires:
            return receipt
        return None
    receipt = _load_active_receipt_uncached(team_dir, target_rule)
    _AUTHORITY_CACHE[key] = (
        signature, receipt, _parse_time(receipt["expires_at"], "expires_at") if receipt else None)
    return receipt


def _load_active_receipt_uncached(team_dir: Path, target_rule: str) -> dict[str, Any] | None:
    path = receipt_path(team_dir)
    if not path.is_file():
        return None
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        current_doc = json.loads(_meta_current_path(team_dir).read_text(encoding="utf-8"))
    except Exception as exc:
        raise HandoverError(f"cannot read handover authority: {exc}") from exc
    current = current_doc.get("current") or {}
    if current.get("rule") != target_rule:
        return None
    # A receipt for a previous target is inert. This lets the next rollover remain usable even if the
    # old tracked receipt has not yet been pruned, while still consuming zero cross-rule evidence.
    if (receipt.get("target_rule") != current.get("rule")
            or receipt.get("target_season") != current.get("season")):
        return None
    if receipt.get("schema_version") != SCHEMA_VERSION:
        raise HandoverError(f"unsupported handover schema {receipt.get('schema_version')!r}")
    evidence_rule = receipt.get("evidence_rule")
    if not isinstance(evidence_rule, str) or not evidence_rule or evidence_rule == target_rule:
        raise HandoverError("handover evidence and target rules must differ")
    if not receipt.get("reason"):
        raise HandoverError("handover reason is missing")
    activated = _parse_time(receipt.get("activated_at"), "activated_at")
    expires = _parse_time(receipt.get("expires_at"), "expires_at")
    authority_activation = _parse_time(
        ((current_doc.get("seasons") or {}).get(current.get("season")) or {}).get("activated_at"),
        "current season activated_at",
    )
    if activated != authority_activation:
        raise HandoverError("handover activation does not match the current season authority")
    if expires <= activated:
        raise HandoverError("handover expiry must be after activation")
    if expires - activated > HARD_MAX_DURATION:
        raise HandoverError("handover duration exceeds the seven-day hard limit")
    now = _now()
    if now < activated:
        raise HandoverError("handover activation is in the future")
    if now >= expires:
        return None
    # Authority bindings matter only while the receipt can authorize old evidence. Once expired, the
    # function has already returned None and later code changes cannot resurrect or poison it.
    if receipt.get("ruleset_sha256") != ruleset_sha256(target_rule):
        raise HandoverError("handover target ruleset changed; rebuild the legality receipt")
    if receipt.get("validator_sha256") != validator_sha256():
        raise HandoverError("handover validator code changed; rebuild the legality receipt")
    if receipt.get("dex_sha256") != dex_sha256(team_dir):
        raise HandoverError("handover dex authority changed; rebuild the legality receipt")
    return receipt


def source_partitions(team_dir: Path, fmt: str, target_rule: str) -> list[str]:
    receipt = load_active_receipt(team_dir, target_rule)
    if not receipt:
        return []
    sources = ((receipt.get("formats") or {}).get(fmt) or {}).get("source_partitions")
    if not isinstance(sources, list) or not sources:
        raise HandoverError(f"handover has no source partitions for {fmt}")
    names: list[str] = []
    for source in sources:
        name = source.get("file") if isinstance(source, dict) else None
        if (not isinstance(name, str)
                or not re.fullmatch(rf"(?:M-\d+|M-[A-Z]+-(?:events|unassigned))_{re.escape(fmt)}\.jsonl", name)):
            raise HandoverError(f"handover has an unsafe source partition name for {fmt}")
        names.append(name)
    if len(names) != len(set(names)):
        raise HandoverError(f"handover repeats a source partition for {fmt}")
    return sorted({Path(name).stem.rsplit("_", 1)[0] for name in names})


def load_teams(team_dir: Path, fmt: str, target_rule: str,
               read_jsonl) -> list[dict[str, Any]]:
    receipt = load_active_receipt(team_dir, target_rule)
    if not receipt:
        return []
    # Same memo rationale as the receipt itself: the accepted rows are a pure function of the bound
    # authorities plus the partition files, and `load_active_receipt` above already re-checked the
    # window. `read_jsonl` is the library's own reader in every caller, so it is not part of the key.
    key = (str(team_dir.resolve()), fmt, target_rule)
    signature = (_authority_signature(team_dir),
                 _stat_signature(sorted(team_dir.glob(f"*_{fmt}.jsonl"))))
    cached = _ROWS_CACHE.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    rows = _load_teams_uncached(team_dir, fmt, target_rule, read_jsonl, receipt)
    _ROWS_CACHE[key] = (signature, rows)
    return rows


def _load_teams_uncached(team_dir: Path, fmt: str, target_rule: str,
                         read_jsonl, receipt: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_rule = receipt.get("evidence_rule")
    block = (receipt.get("formats") or {}).get(fmt)
    if not isinstance(block, dict):
        raise HandoverError(f"handover has no {fmt!r} validation block")
    accepted_list = block.get("accepted_row_sha256")
    if not isinstance(accepted_list, list) or not accepted_list:
        raise HandoverError(f"handover accepted no {fmt} rows")
    if any(not isinstance(value, str) or len(value) != 64
           or any(char not in "0123456789abcdef" for char in value)
           for value in accepted_list):
        raise HandoverError(f"handover has invalid accepted-row hashes for {fmt}")
    accepted = Counter(accepted_list)
    if block.get("accepted_rows") != sum(accepted.values()):
        raise HandoverError(f"handover has a contradictory accepted-row count for {fmt}")
    sources = block.get("source_partitions")
    if not isinstance(sources, list) or not sources:
        raise HandoverError(f"handover has no source partitions for {fmt}")
    rows: list[dict[str, Any]] = []
    seen: Counter[str] = Counter()
    seen_sources: set[str] = set()
    candidate_rows = 0
    for source in sources:
        if not isinstance(source, dict):
            raise HandoverError(f"handover has a malformed source partition for {fmt}")
        name = source.get("file")
        if (not isinstance(name, str)
                or not re.fullmatch(rf"(?:M-\d+|M-[A-Z]+-(?:events|unassigned))_{re.escape(fmt)}\.jsonl", name)):
            raise HandoverError(f"handover has an unsafe source partition name for {fmt}")
        if name in seen_sources:
            raise HandoverError(f"handover repeats a source partition for {fmt}: {name}")
        seen_sources.add(name)
        path = (team_dir / name).resolve()
        try:
            path.relative_to(team_dir.resolve())
        except ValueError as exc:
            raise HandoverError("handover source partition escapes the team data directory") from exc
        if not path.is_file() or file_sha256(path) != source.get("sha256"):
            raise HandoverError(f"handover source partition changed or is missing: {path.name}")
        source_rows = 0
        for raw in read_jsonl(path):
            if raw.get("rule") == evidence_rule:
                source_rows += 1
            digest = row_sha256(raw)
            if digest not in accepted:
                continue
            seen[digest] += 1
            if seen[digest] > accepted[digest]:
                raise HandoverError(
                    f"handover source partitions contain excess accepted-row occurrences for {fmt}")
            if raw.get("rule") != evidence_rule or raw.get("format") != fmt:
                raise HandoverError(f"accepted handover row has contradictory labels in {path.name}")
            row = copy.deepcopy(raw)
            row["evidence_rule"] = evidence_rule
            row["target_rule"] = target_rule
            row["handover_reason"] = receipt["reason"]
            row["expires_at"] = receipt["expires_at"]
            rows.append(row)
        if source.get("rows") != source_rows:
            raise HandoverError(
                f"handover source partition has a contradictory row count: {path.name}")
        candidate_rows += source_rows
    if block.get("candidate_rows") != candidate_rows:
        raise HandoverError(f"handover has a contradictory candidate-row count for {fmt}")
    if accepted != seen:
        missing = sum((accepted - seen).values())
        raise HandoverError(f"handover receipt references {missing} missing accepted row occurrences")
    return rows
