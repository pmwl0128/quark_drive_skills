#!/usr/bin/env python
"""team-json + Showdown-text parsing/serialization (M1.1).

Accepts two inputs and normalizes both to the canonical team-json shape
(see references/schema.md):
  - team-json  : our own structure (a dict with a "pokemon" list).
  - Showdown   : the common paste format, blank-line separated members.

Showdown EV lines are read into `spread` as Champions SP (1 SP = +1 stat); Champions
has no IVs/EVs, so any "IVs" line is ignored. This module does no legality checks.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from stat_keys import canonical_spread

STATS = ["hp", "atk", "def", "spa", "spd", "spe"]
_EV_TOKEN = {"hp": "hp", "atk": "atk", "at": "atk", "def": "def", "df": "def",
             "spa": "spa", "spatk": "spa", "spd": "spd", "spdef": "spd",
             "spe": "spe", "spd.": "spd"}


@dataclass
class TeamMember:
    species: str
    item: str | None = None
    ability: str | None = None
    moves: list[str] = field(default_factory=list)
    nature: str | None = None
    spread: dict[str, int] | None = None
    tera: str | None = None
    completeness: str | None = None    # observed_full_set | observed_species_only | extracted_set | inferred_set
    # Internal battle-view provenance; never trusted by the public parser or serialized as team input.
    _registration: dict[str, Any] | None = field(default=None, repr=False, compare=False)


@dataclass
class Team:
    pokemon: list[TeamMember]
    format: str | None = None          # "single" | "double"
    season: str | None = None
    rule: str | None = None
    provenance: dict[str, Any] | None = None
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "format": self.format,
            "season": self.season,
            "rule": self.rule,
            "pokemon": [{k: v for k, v in asdict(m).items() if k != "_registration"} for m in self.pokemon],
            "provenance": self.provenance,
        }


# --------------------------------------------------------------------------- #
# team-json
# --------------------------------------------------------------------------- #

def _member_from_dict(d: dict[str, Any]) -> TeamMember:
    return TeamMember(
        species=str(d.get("species") or d.get("name") or "").strip(),
        item=d.get("item"),
        ability=d.get("ability"),
        moves=list(d.get("moves") or d.get("attacks") or []),
        nature=d.get("nature"),
        spread=canonical_spread(d["spread"]) if isinstance(d.get("spread"), dict) else d.get("spread"),
        tera=d.get("tera"),
        completeness=d.get("completeness"),
    )


def team_from_dict(d: dict[str, Any], *, retain_registration: bool = False) -> Team:
    rows = d.get("pokemon") or d.get("decklist") or []
    members = [_member_from_dict(m) for m in rows]
    if retain_registration:
        for member, row in zip(members, rows):
            if isinstance(row.get("_registration"), dict):
                member._registration = dict(row["_registration"])
    return Team(
        pokemon=members,
        format=d.get("format"),
        season=d.get("season"),
        rule=d.get("rule"),
        provenance=d.get("provenance"),
        schema_version=int(d.get("schema_version", 1)),
    )


# --------------------------------------------------------------------------- #
# Showdown text
# --------------------------------------------------------------------------- #

def _parse_spread(line: str) -> dict[str, int]:
    spread = {s: 0 for s in STATS}
    for amount, label in re.findall(r"(\d+)\s+([A-Za-z.]+)", line):
        key = _EV_TOKEN.get(label.lower().rstrip("."))
        if key:
            spread[key] = int(amount)
    return spread


def _parse_member_block(block: str) -> TeamMember | None:
    lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
    if not lines:
        return None
    m = TeamMember(species="")
    head = lines[0]
    if "@" in head:
        name, item = head.split("@", 1)
        m.species, m.item = name.strip(), item.strip()
    else:
        m.species = head.strip()
    # Showdown header forms: "Species", "Nickname (Species)", optionally "... (M)/(F)".
    # Strip the gender marker first, then if a "(Species)" suffix remains it is the real
    # species and the leading text is just a nickname (dex can't resolve "Nick (Garchomp)").
    m.species = re.sub(r"\s*\((?:M|F)\)\s*$", "", m.species).strip()
    nick = re.match(r"^.+?\s*\(([^()]+)\)\s*$", m.species)
    if nick:
        m.species = nick.group(1).strip()
    for ln in lines[1:]:
        low = ln.lower()
        if low.startswith("ability:"):
            m.ability = ln.split(":", 1)[1].strip()
        elif low.startswith("nature:"):
            # Champions community exports often use the field-style `Nature: Timid`
            # alongside `SP:` instead of Showdown's `Timid Nature`. Accept both forms.
            m.nature = ln.split(":", 1)[1].strip()
        elif low.startswith("evs:") or low.startswith("sp:"):
            m.spread = _parse_spread(ln.split(":", 1)[1])
        elif low.startswith("ivs:") or low.startswith("level:") or low.startswith("tera type:"):
            continue
        elif low.endswith("nature"):
            m.nature = ln.rsplit(" ", 1)[0].strip()
        elif ln.lstrip().startswith("-"):
            mv = ln.lstrip()[1:].strip()
            if mv:
                m.moves.append(mv)
    return m if m.species else None


def team_from_showdown(text: str) -> Team:
    blocks = re.split(r"\n\s*\n", text.strip())
    members = [mm for mm in (_parse_member_block(b) for b in blocks) if mm]
    return Team(pokemon=members)


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #

def parse_text(text: str) -> Team:
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return team_from_dict(json.loads(text))
    return team_from_showdown(text)


def load_team(path: str | Path) -> Team:
    p = Path(path)
    return parse_text(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# build-context (intent layer — schema.md §7). AI translates conversation intent
# into this structured object; the skill never parses free text.
# --------------------------------------------------------------------------- #

@dataclass
class BuildContext:
    season: str | None = None
    rule: str | None = None
    format: str | None = None
    locked: list[str] = field(default_factory=list)      # members the user will not change
    owned_only: bool = False                             # restrict to owned roster
    owned: list[str] = field(default_factory=list)       # owned species the AI gathered from the user's
    #                                                      input (any format) and resolved via the dex —
    #                                                      canonical or dex-resolvable names; no fixed file
    wants: list[str] = field(default_factory=list)       # tactics: weather/trickroom/tailwind/...
    keep_mega: str | None = None                         # species/form whose Mega the user wants kept
    mega_posture: str | None = None                      # environment|none|single|multi registration intent
    avoid: list[str] = field(default_factory=list)       # raw input: species AND/OR items, mixed
    prefer: list[str] = field(default_factory=list)      # soft species preference (anchor's soft half);
    #                                                      AI-side intent — no operator filters on it yet
    exclude_tactics: list[str] = field(default_factory=list)  # enumerated negative tactics (see
    #                                                      contracts.EXCLUDE_TACTICS); AI-side intent
    meta_conformance: str | None = None                  # posture knob proven|off_meta — a VIEW
    style_lean: str | None = None                        # posture knob offense|balance|defense — a USER lens over profile facts, never a team label
    variance_tolerance: str | None = None                # posture knob averse|tolerant — diagnose FLAGS luck lines when averse; a disclosure flag over dex accuracy, never a score
    avoid_soft: list[str] = field(default_factory=list)   # SOFT exclude — prefer's mirror: honored when composing, no operator filters
    #                                                      ordering for landscape cores, never a score
    # Derived: `avoid` split by dex kind so an item entry ("Choice Scarf") stops masquerading as an
    # unresolvable species. The CLI loader (_load_context) refines these via the dex; the dex-free
    # constructor defaults avoid_species to the RAW avoid list so a consumer that filters on it
    # (fill) never silently loses the constraint (an item entry then just never matches a species).
    avoid_species: list[str] = field(default_factory=list)
    avoid_items: list[str] = field(default_factory=list)
    benchmarks: list[dict[str, Any]] = field(default_factory=list)   # SP-tuning targets (schema §7)
    goals: list[dict[str, Any]] = field(default_factory=list)        # slate reporting targets
    need: dict[str, Any] = field(default_factory=dict)               # L3 fill gap spec:
    #   {resist: type|[types], offense_type: type|[types], coverage_move_type: type|[types],
    #    role: name|[names], min_speed: int, answer: species|[species]}
    replace: dict[str, Any] = field(default_factory=dict)            # L3 replace-impact:
    #   {member: "<species to remove>", with: <full team-json member: species + set>}
    direct_final: bool = False                  # checkpoint: user explicitly delegated final convergence
    skip_checkpoint: bool = False               # checkpoint: explicit orchestration override
    checkpoint_required: bool = False           # user requested an intermediate choice before continuing
    frame_required: bool = False                # build-flow slate must carry a frame receipt/binding


def context_from_dict(d: dict[str, Any]) -> BuildContext:
    return BuildContext(
        season=d.get("season"), rule=d.get("rule"), format=d.get("format"),
        locked=list(d.get("locked") or []),
        owned_only=bool(d.get("owned_only", False)),
        owned=list(d.get("owned") or []),
        wants=list(d.get("wants") or []),
        keep_mega=d.get("keep_mega"),
        mega_posture=d.get("mega_posture"),
        avoid=list(d.get("avoid") or []),
        avoid_species=list(d.get("avoid") or []),   # dex-free default; _load_context refines the split
        prefer=list(d.get("prefer") or []),
        exclude_tactics=list(d.get("exclude_tactics") or []),
        meta_conformance=d.get("meta_conformance"),
        style_lean=d.get("style_lean"),
        variance_tolerance=d.get("variance_tolerance"),
        avoid_soft=[s for s in (d.get("avoid_soft") or []) if isinstance(s, str)],
        benchmarks=list(d.get("benchmarks") or []),
        goals=[dict(g) for g in (d.get("goals") or [])],
        need=dict(d.get("need") or {}),
        replace=dict(d.get("replace") or {}),
        direct_final=d.get("direct_final") is True,
        skip_checkpoint=d.get("skip_checkpoint") is True,
        checkpoint_required=d.get("checkpoint_required") is True,
        frame_required=d.get("frame_required") is True,
    )


def load_context(path: str | Path) -> BuildContext:
    return context_from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
