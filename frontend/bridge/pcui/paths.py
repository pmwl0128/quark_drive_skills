"""Skill CLI locations + the bridge's per-user data directory."""
from __future__ import annotations

import os
from pathlib import Path

# Repo layout: frontend/bridge/pcui/paths.py -> project root three levels up. An installed wheel
# overrides via PCUI_SKILLS_ROOT; the dev tree just works.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SKILLS_ROOT = Path(os.environ.get("PCUI_SKILLS_ROOT", PROJECT_ROOT / ".agents" / "skills"))

CLIS: dict[str, Path] = {
    "dex": SKILLS_ROOT / "pokemon-champions-dex/scripts/champdex.py",
    "meta": SKILLS_ROOT / "pokemon-champions-meta/scripts/meta_query.py",
    "calc": SKILLS_ROOT / "ncp-damage-calculator/scripts/ncp-calc-api.py",
    "speedline": SKILLS_ROOT / "ncp-damage-calculator/scripts/ncp-speedline-api.py",
    "team": SKILLS_ROOT / "pokemon-champions-team/scripts/team.py",
    "inference": SKILLS_ROOT / "ncp-damage-calculator/scripts/ncp-infer-case.py",
}
# Query CLIs get resident workers; team deliberately does NOT (frontend/design.md §4.1 🚩:
# out-of-session team calls cold-start sibling skills anyway — batch through `session`).
WORKER_CLIS = ("dex", "meta", "calc", "speedline")

META_DATA = SKILLS_ROOT / "pokemon-champions-meta/data"


def data_dir() -> Path:
    d = Path(os.environ.get("PCUI_DATA_DIR", Path.home() / ".pokemon-champions"))
    d.mkdir(parents=True, exist_ok=True)
    return d
