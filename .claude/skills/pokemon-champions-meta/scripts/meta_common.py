from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

# Query-only helpers for the offline metagame cache.
# This module is intentionally read-only: it resolves paths, loads cached
# structured data, and normalizes names. It contains no remote endpoints or
# refresh logic. Cache (re)generation lives outside the skill.

SKILL_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = SKILL_DIR / "data"
CACHE_DIR = DATA_DIR
CURRENT_PATH = DATA_DIR / "current.json"

SEASON_RULE = {
    "M-1": "M-A",
    "M-2": "M-A",
    "M-3": "M-B",
    "M-4": "M-B",
    "M-5": "M-B",
    "M-6": "M-C",
    "M-7": "M-C",
    "M-8": "M-C",
}


class EnvironmentResolutionError(ValueError):
    """The requested season/rule tuple is unknown or contradicts the registered environment."""

PANEL_MAP = {
    "moves": "moves",
    "move": "moves",
    "招式": "moves",
    "桸宒": "moves",
    "items": "items",
    "item": "items",
    "道具": "items",
    "耋撿": "items",
    "abilities": "abilities",
    "ability": "abilities",
    "特性": "abilities",
    "杻俶": "abilities",
    "natures": "natures",
    "nature": "natures",
    "性格": "natures",
    "俶跡": "natures",
    "partners": "partners",
    "partner": "partners",
    "搭档": "partners",
    "勦衭": "partners",
    "spreads": "spreads",
    "spread": "spreads",
    "努力值": "spreads",
    "能力点": "spreads",
}


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def ranking_path(season: str, fmt: str) -> Path:
    return CACHE_DIR / f"ranking_{season}_{fmt}.json"


def details_path(season: str, fmt: str) -> Path:
    return CACHE_DIR / f"details_{season}_{fmt}.json"


def ko_path(season: str, fmt: str) -> Path:
    """KO-facts file. A separate family from ranking/details: different upstream, different snapshot
    clock, and rows whose KO objects are species-level rather than form-level."""
    return CACHE_DIR / f"ko_{season}_{fmt}.json"


def count_cjk(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text or ""))


SUSPICIOUS_CN_CHARS = set(
    "瑈琍竤篽狦癨簑碉臦疨礙礥瞊蒩玦幢"
    "桸宒耋撿杻俶俶跡勦衭"
)


def maybe_repair_cn(text: str | None) -> str:
    if not text:
        return ""
    text = str(text)
    if any(ch in SUSPICIOUS_CN_CHARS for ch in text):
        for source, target in (("gbk", "big5"), ("gbk", "cp950"), ("gb18030", "big5"), ("gb18030", "cp950")):
            try:
                repaired = text.encode(source).decode(target)
            except Exception:
                continue
            if repaired != text and count_cjk(repaired) >= count_cjk(text):
                return repaired
    return text


def normalize(text: str) -> str:
    # NFKC folds full-width <-> half-width (JP sources vary, e.g. １０ vs 10) so names match regardless
    # of how they were typed. Mirrors champdex.normalize (the dex is the naming authority) — the extra
    # ·・ separators are meta-specific and harmless to fold out.
    text = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"[\s\-_'’().:：/\\\[\]{}·・]+", "", text.strip().lower())


def norm_panel(panel: str) -> str:
    return PANEL_MAP.get(panel, PANEL_MAP.get(maybe_repair_cn(panel), panel.lower()))


def current_state() -> dict[str, Any]:
    state = load_json(CURRENT_PATH, {})
    if not isinstance(state, dict) or not isinstance(state.get("current"), dict):
        raise RuntimeError(f"current environment authority is missing or invalid: {CURRENT_PATH}")
    seasons = state.setdefault("seasons", {})
    if not isinstance(seasons, dict):
        raise RuntimeError(f"current environment season registry is invalid: {CURRENT_PATH}")
    for sid, srule in SEASON_RULE.items():
        entry = seasons.setdefault(sid, {"rule": srule})
        if not isinstance(entry, dict) or entry.get("rule") not in (None, srule):
            raise RuntimeError(
                f"season {sid!r} conflicts with registered rule {srule!r}: {CURRENT_PATH}")
        entry.setdefault("rule", srule)
    cur = state["current"]
    season = cur.get("season")
    rule = cur.get("rule")
    if not season or not rule:
        raise RuntimeError(f"current season/rule is incomplete: {CURRENT_PATH}")
    mapped = SEASON_RULE.get(season)
    if mapped is None or mapped != rule:
        raise RuntimeError(
            f"current environment {season}/{rule} is not an exact SEASON_RULE registration")
    state["current"] = {"season": season, "rule": rule}
    state["seasons"].setdefault(season, {"rule": rule})
    return state


def resolve_season_rule(season: str | None = None, rule: str | None = None) -> tuple[str, str]:
    state = current_state()
    current = state.get("current", {})
    seasons = state.get("seasons", {})
    if season:
        mapped = SEASON_RULE.get(season)
        if mapped is None:
            raise EnvironmentResolutionError(f"season {season!r} is not registered in SEASON_RULE")
        recorded = seasons.get(season, {}).get("rule")
        if recorded and recorded != mapped:
            raise EnvironmentResolutionError(
                f"season {season!r} record conflicts with SEASON_RULE: {recorded!r} != {mapped!r}")
        if rule and rule != mapped:
            raise EnvironmentResolutionError(f"season {season!r} belongs to {mapped!r}, not {rule!r}")
        resolved_rule = mapped
        return season, resolved_rule
    if rule:
        if rule not in set(SEASON_RULE.values()):
            raise EnvironmentResolutionError(f"rule {rule!r} is not registered in SEASON_RULE")
        current_season = current.get("season")
        if seasons.get(current_season, {}).get("rule") == rule or current.get("rule") == rule:
            return current_season, rule
        # Only the code-reviewed registry may choose a season. Extra manifest rows are historical
        # metadata, not permission to route a query or updater into an unregistered partition.
        matches = [sid for sid, mapped in SEASON_RULE.items() if mapped == rule]
        if matches:
            return matches[-1], rule
        raise EnvironmentResolutionError(f"no registered season is mapped to rule {rule!r}")
    return str(current["season"]), str(current["rule"])
