"""Canonical SP keys at the public team boundary; legacy short keys are input-only."""
from __future__ import annotations

from typing import Any

LEGACY_STATS = {"at": "atk", "df": "def", "sa": "spa", "sd": "spd", "sp": "spe"}
ENGINE_STATS = {"hp": "hp", **{canonical: legacy for legacy, canonical in LEGACY_STATS.items()}}


def canonical_spread(spread: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in spread.items():
        canonical = LEGACY_STATS.get(key, key)
        if canonical in out and (type(out[canonical]) is not type(value) or out[canonical] != value):
            raise ValueError(f"conflicting SP aliases for {canonical}: use hp/atk/def/spa/spd/spe")
        out[canonical] = value
    return out


def canonical_spreads(value: Any) -> Any:
    """Copy a public set/frame view without changing unrelated dictionary keys."""
    if isinstance(value, list):
        return [canonical_spreads(v) for v in value]
    if isinstance(value, dict):
        return {k: canonical_spread(v) if k in {"sps", "spread"} and isinstance(v, dict)
                else canonical_spreads(v) for k, v in value.items()}
    return value


def engine_spread(spread: dict[str, Any]) -> dict[str, Any]:
    """Accept either public/legacy keys once, before the internal SP search touches them."""
    return {ENGINE_STATS.get(k, k): v for k, v in canonical_spread(spread).items()}
