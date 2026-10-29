#!/usr/bin/env python
"""Executable I/O contracts for the team skill.

The skill passes loose dicts between modules and to/from the sibling skills, which is how the
field-drift bugs got in (raw-speed `vs`, NCP `screens` vs `defenderSide`, condition keys). This
module is the single, *executable* source of truth for those shapes: enumerated field values,
stable error codes, and validators that return structured `ContractError`s instead of silently
accepting malformed input. It is enforced at the CLI boundary (team.py) so internal callers stay
lenient and unit-testable.

stdlib-only by design: a skill must run under a bare `python` via subprocess, so no pydantic /
third-party deps (same constraint as the rest of scripts/). The validators ARE the schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import team_i18n as i18n
from rules import SPREAD_STAT_KEYS, get_ruleset
from stat_keys import canonical_spread

# --- versioning -------------------------------------------------------------
SCHEMA_VERSION = 1                      # the version this skill emits
SUPPORTED_SCHEMA_VERSIONS = {1}        # versions it accepts as input

# --- enumerated field values (the contract) ---------------------------------
FORMATS = {"single", "double"}
COMPLETENESS = {"observed_full_set", "observed_species_only", "extracted_set", "inferred_set"}
BENCHMARK_KINDS = {"survive", "outspeed", "ohko", "2hko"}
PROBABILITIES = {"guaranteed", "near_guaranteed", "likely", "three_quarters", "half", "any"}
CONFIDENCE = {"high", "medium", "low"}
CONDITION_KEYS = {"stealth_rock", "spikes", "tailwind", "opponent_tailwind", "trickroom",
                  "weather", "terrain", "screens"}
SCREEN_VALUES = {"reflect", "light_screen", "aurora_veil"}    # plus bare boolean true
# A survive benchmark's `member_state`: OUR member's stated battle state, in the calc's input
# vocabulary (conventions §2: statuses are the calc's capitalised English; stages use canonical keys).
MEMBER_STATE_FIELDS = {"status", "boosts"}
MEMBER_STATUSES = {"Healthy", "Burned", "Paralyzed", "Poisoned", "Badly Poisoned", "Asleep", "Frozen"}
BOOST_STAT_KEYS = {"atk", "def", "spa", "spd", "spe"}
STAT_KEYS = SPREAD_STAT_KEYS            # single source for the SP stat keys: rules.py
# Negative tactic tokens for build-context.exclude_tactics — the playstyle a user does NOT want (the
# assisted-build flow's "不想用受队/空间/天气…"). An enumerated vocab (unlike free-form `wants`) so a typo'd
# tactic is caught, not silently ignored. AI-side intent today: the assistant honors it when composing
# and when choosing which fill views to request; no operator mechanically filters on it yet.
EXCLUDE_TACTICS = {"stall", "trickroom", "weather", "tailwind", "screens", "pivot", "setup", "hazards"}
TEAM_FIELDS = {"schema_version", "format", "season", "rule", "pokemon", "decklist", "provenance"}
MEMBER_FIELDS = {"species", "name", "item", "ability", "moves", "attacks", "nature",
                 "spread", "tera", "completeness"}
CONTEXT_FIELDS = {"season", "rule", "format", "locked", "owned_only", "owned", "wants",
                  "keep_mega", "mega_posture", "avoid", "avoid_soft", "prefer", "exclude_tactics", "meta_conformance", "style_lean",
                  "variance_tolerance", "benchmarks", "need", "replace", "direct_final", "skip_checkpoint",
                  "frame_required", "checkpoint_required", "goals"}
# The posture knob's values (proven = common-first views, off_meta = rare-first). A VIEW ordering
# consumed by landscape; never a score.
META_CONFORMANCE = {"proven", "off_meta"}
# Registration posture.  ``environment`` consumes the observed frame distribution; the other values
# are explicit user composition constraints.  This is separate from keep_mega (which names an option).
MEGA_POSTURE = {"environment", "none", "single", "multi"}
# The structural-posture trichotomy in professional battle vocabulary (主动进攻/平衡轮换/稳健防守).
# A USER intent lens over the emergent profile vector — the skill itself never labels a team.
STYLE_LEAN = {"offense", "balance", "defense"}
# The variance knob ("稳" 的次要成分, §19.5): averse = the user wants fewer luck lines, so diagnose
# FLAGS the team's sub-100%-accuracy damaging moves; tolerant = fine with them. A disclosure flag over
# dex accuracy facts — diagnose surfaces the luck-line list either way, the knob only flags it. Never a score.
VARIANCE_TOLERANCE = {"averse", "tolerant"}
BENCHMARK_FIELDS = {
    "member", "kind", "vs", "move", "hits", "conditions", "probability",
    "attacker_set", "opponent_set", "member_state",
}


# --- error model ------------------------------------------------------------
class Code:
    SCHEMA_VERSION = "E_SCHEMA_VERSION"   # unsupported schema_version
    TYPE = "E_TYPE"                       # wrong JSON type for a field
    ENUM = "E_ENUM"                       # value outside the allowed set
    MISSING = "E_MISSING"                 # required field absent/empty
    RANGE = "E_RANGE"                     # numeric out of range
    UNKNOWN_FIELD = "W_UNKNOWN_FIELD"     # field not in the contract (warning)
    SUSPECT = "W_SUSPECT"                 # legal-shaped but likely wrong (warning)


@dataclass
class ContractError:
    code: str
    path: str
    message: str
    severity: str = "error"              # "error" | "warning"

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "path": self.path, "message": self.message, "severity": self.severity}


def fatal(errors: list[ContractError]) -> bool:
    return any(e.severity == "error" for e in errors)


# --- small helpers ----------------------------------------------------------
def _is_str(v: Any) -> bool:
    return isinstance(v, str)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _err(out: list[ContractError], code: str, path: str, msg: str) -> None:
    out.append(ContractError(code, path, msg))


def _warn(out: list[ContractError], code: str, path: str, msg: str) -> None:
    out.append(ContractError(code, path, msg, severity="warning"))


def _check_version(d: dict, out: list[ContractError]) -> None:
    v = d.get("schema_version", SCHEMA_VERSION)
    if not _is_int(v) or v not in SUPPORTED_SCHEMA_VERSIONS:
        _err(out, Code.SCHEMA_VERSION, "schema_version",
             i18n.Msg('ct_schema_version', v=v, supported=sorted(SUPPORTED_SCHEMA_VERSIONS)))


def _check_unknown(d: dict, allowed: set[str], path: str, out: list[ContractError]) -> None:
    for k in d:
        if k not in allowed:
            _warn(out, Code.UNKNOWN_FIELD, f"{path}.{k}" if path else k, f"unknown field `{k}`")


# --- public validators ------------------------------------------------------
def check_team(d: Any) -> list[ContractError]:
    """Validate a team-json dict against the contract. Returns [] when clean."""
    out: list[ContractError] = []
    if not isinstance(d, dict):
        _err(out, Code.TYPE, "", i18n.Msg('ct_team_object'))
        return out
    _check_version(d, out)
    _check_unknown(d, TEAM_FIELDS, "", out)
    # Enum fields: membership tests hash the value, so a non-string (list/dict) must be caught by
    # the type gate FIRST — `["double"] in FORMATS` raises TypeError, and the checker's whole job is
    # to REPORT bad input, never crash on it. EVERY set-membership enum in this module carries the
    # same gate (external audit 2026-07-03 found completeness/kind/probability/confidence unfixed
    # after the first pass — patch the CLASS, not the named instances).
    fmt = d.get("format")
    if fmt is not None and (not _is_str(fmt) or fmt not in FORMATS):
        _err(out, Code.ENUM, "format", i18n.Msg('ct_format_enum', allowed=sorted(FORMATS), got=fmt))
    members = d.get("pokemon", d.get("decklist"))
    if not isinstance(members, list) or not members:
        _err(out, Code.MISSING, "pokemon", i18n.Msg('ct_pokemon_list'))
        return out
    for i, m in enumerate(members):
        _check_member(m, f"pokemon[{i}]", out)
    return out


def _check_member(m: Any, path: str, out: list[ContractError]) -> None:
    if not isinstance(m, dict):
        _err(out, Code.TYPE, path, i18n.Msg('ct_member_object'))
        return
    _check_unknown(m, MEMBER_FIELDS, path, out)
    species = m.get("species") or m.get("name")
    if not _is_str(species) or not species.strip():
        _err(out, Code.MISSING, f"{path}.species", i18n.Msg('ct_member_species'))
    for key in ("item", "ability", "nature"):
        if m.get(key) is not None and not _is_str(m[key]):
            _err(out, Code.TYPE, f"{path}.{key}", i18n.Msg('ct_string_or_null', key=key))
    moves = m.get("moves", m.get("attacks"))
    if moves is not None:
        if not isinstance(moves, list) or any(not _is_str(x) for x in moves):
            _err(out, Code.TYPE, f"{path}.moves", i18n.Msg('ct_moves_list'))
    sp = m.get("spread")
    if sp is not None:
        if not isinstance(sp, dict):
            _err(out, Code.TYPE, f"{path}.spread", i18n.Msg('ct_spread_object'))
        else:
            try:
                sp = canonical_spread(sp)
            except ValueError as exc:
                _err(out, Code.TYPE, f"{path}.spread", str(exc))
            for k, v in sp.items():
                if k not in STAT_KEYS:
                    _warn(out, Code.UNKNOWN_FIELD, f"{path}.spread.{k}", i18n.Msg('ct_unknown_stat', k=k))
                elif not _is_int(v):
                    # A wrong TYPE (e.g. "32", 32.0, true) is a type error, not a range error: every
                    # other field in this contract reports a type mismatch as E_TYPE, and an LLM/API
                    # caller auto-repairs a type fault differently from a bound fault (audit 2026-06-28).
                    _err(out, Code.TYPE, f"{path}.spread.{k}", i18n.Msg('ct_sp_int'))
                elif v < 0:
                    _err(out, Code.RANGE, f"{path}.spread.{k}", i18n.Msg('ct_sp_nonneg'))
    comp = m.get("completeness")
    if comp is not None and (not _is_str(comp) or comp not in COMPLETENESS):
        _err(out, Code.ENUM, f"{path}.completeness", i18n.Msg('ct_completeness_enum', allowed=sorted(COMPLETENESS)))
    if m.get("tera"):
        _warn(out, Code.SUSPECT, f"{path}.tera", i18n.Msg('ct_tera_null'))


def check_context(d: Any) -> list[ContractError]:
    """Validate a build-context dict (including its benchmarks)."""
    out: list[ContractError] = []
    if not isinstance(d, dict):
        _err(out, Code.TYPE, "", i18n.Msg('ct_context_object'))
        return out
    _check_unknown(d, CONTEXT_FIELDS, "", out)
    fmt = d.get("format")
    if fmt is not None and (not _is_str(fmt) or fmt not in FORMATS):
        _err(out, Code.ENUM, "format", i18n.Msg('ct_format_enum', allowed=sorted(FORMATS), got=fmt))
    if d.get("owned_only") is not None and not isinstance(d.get("owned_only"), bool):
        _err(out, Code.TYPE, "owned_only", i18n.Msg('ct_owned_only_bool'))
    for key in ("direct_final", "skip_checkpoint", "frame_required", "checkpoint_required"):
        if d.get(key) is not None and not isinstance(d.get(key), bool):
            _err(out, Code.TYPE, key, i18n.Msg('ct_bool', k=key))
    # keep_mega is a NAME (species / Mega form) — it gets dex-canonicalized on load and fed to the
    # resolver, so a non-string (e.g. a legacy boolean) must be refused here, not crash in subprocess
    # argv (audit 2026-07-02).
    if d.get("keep_mega") is not None and not _is_str(d.get("keep_mega")):
        _err(out, Code.TYPE, "keep_mega", i18n.Msg('ct_keep_mega_str'))
    for key in ("locked", "owned", "wants", "avoid", "avoid_soft", "prefer"):
        if d.get(key) is not None and (not isinstance(d[key], list) or any(not _is_str(x) for x in d[key])):
            _err(out, Code.TYPE, key, i18n.Msg('ct_list_of_strings', key=key))
    mc = d.get("meta_conformance")
    if mc is not None and (not _is_str(mc) or mc not in META_CONFORMANCE):
        _err(out, Code.ENUM, "meta_conformance",
             i18n.Msg('ct_meta_conformance_enum', got=mc, allowed=sorted(META_CONFORMANCE)))
    mp = d.get("mega_posture")
    if mp is not None and (not _is_str(mp) or mp not in MEGA_POSTURE):
        _err(out, Code.ENUM, "mega_posture",
             i18n.Msg('ct_mega_posture_enum', got=mp, allowed=sorted(MEGA_POSTURE)))
    sl = d.get("style_lean")
    if sl is not None and (not _is_str(sl) or sl not in STYLE_LEAN):
        _err(out, Code.ENUM, "style_lean",
             i18n.Msg('ct_style_lean_enum', got=sl, allowed=sorted(STYLE_LEAN)))
    vt = d.get("variance_tolerance")
    if vt is not None and (not _is_str(vt) or vt not in VARIANCE_TOLERANCE):
        _err(out, Code.ENUM, "variance_tolerance",
             i18n.Msg('ct_variance_tolerance_enum', got=vt, allowed=sorted(VARIANCE_TOLERANCE)))
    xt = d.get("exclude_tactics")
    if xt is not None:
        if not isinstance(xt, list) or any(not _is_str(x) for x in xt):
            _err(out, Code.TYPE, "exclude_tactics", i18n.Msg('ct_list_of_strings', key="exclude_tactics"))
        else:
            for tok in xt:
                if tok not in EXCLUDE_TACTICS:
                    _err(out, Code.ENUM, "exclude_tactics",
                         i18n.Msg('ct_exclude_tactics_enum', tok=tok, allowed=sorted(EXCLUDE_TACTICS)))
    benches = d.get("benchmarks")
    goals = d.get("goals")
    if goals is not None:
        if not isinstance(goals, list):
            _err(out, Code.TYPE, "goals", "goals must be a list of {kind:answer|include, species}")
        else:
            for i, goal in enumerate(goals):
                path = f"goals[{i}]"
                if not isinstance(goal, dict):
                    _err(out, Code.TYPE, path, "goal must be {kind:answer|include, species}")
                    continue
                for key in set(goal) - {"kind", "species"}:
                    _err(out, Code.UNKNOWN_FIELD, f"{path}.{key}", "unsupported goal field")
                if goal.get("kind") not in ("answer", "include"):
                    _err(out, Code.ENUM, path + ".kind", "goal kind must be answer or include")
                if not _is_str(goal.get("species")) or not goal["species"].strip():
                    _err(out, Code.TYPE, path + ".species", "goal species must be a nonempty Pokemon name")
    if benches is not None:
        if not isinstance(benches, list):
            _err(out, Code.TYPE, "benchmarks", i18n.Msg('ct_benchmarks_list'))
        else:
            for i, b in enumerate(benches):
                out.extend(check_benchmark(b, f"benchmarks[{i}]"))
    if d.get("need") is not None:
        out.extend(check_need(d["need"]))
    if d.get("replace") is not None:
        out.extend(check_replace(d["replace"]))
    return out


REPLACE_FIELDS = {"member", "with"}


def check_replace(r: Any, path: str = "replace") -> list[ContractError]:
    """Validate the L3 replace-impact spec: {member: <species to remove>, with: <a team-json member>}."""
    out: list[ContractError] = []
    if not isinstance(r, dict):
        _err(out, Code.TYPE, path, i18n.Msg('ct_replace_object'))
        return out
    _check_unknown(r, REPLACE_FIELDS, path, out)
    if not _is_str(r.get("member")) or not r["member"].strip():
        _err(out, Code.MISSING, f"{path}.member", i18n.Msg('ct_replace_member_req'))
    w = r.get("with")
    if w is None:
        _err(out, Code.MISSING, f"{path}.with", i18n.Msg('ct_replace_with_req'))
    elif isinstance(w, str):
        # Common shorthand mistake: a bare name string. Say exactly what shape is expected rather than
        # the generic "member must be a JSON object" (audit 2026-06-28).
        _err(out, Code.TYPE, f"{path}.with", i18n.Msg('ct_replace_with_shape'))
    else:
        _check_member(w, f"{path}.with", out)          # the candidate must be a valid team-json member
    return out


NEED_FIELDS = {"resist", "offense_type", "role", "min_speed", "coverage_move_type", "answer"}


def check_need(n: Any, path: str = "need") -> list[ContractError]:
    """Validate the L3 `fill` gap spec. Keeps fill a safe CLI/front-end entry: a bad
    `min_speed` (e.g. "fast") would otherwise ValueError mid-run (audit 2026-06-24)."""
    out: list[ContractError] = []
    if not isinstance(n, dict):
        _err(out, Code.TYPE, path, i18n.Msg('ct_need_object'))
        return out
    _check_unknown(n, NEED_FIELDS, path, out)
    for key in ("resist", "offense_type", "role", "coverage_move_type"):
        v = n.get(key)
        if v is not None and not (_is_str(v) or (isinstance(v, list) and all(_is_str(x) for x in v))):
            _err(out, Code.TYPE, f"{path}.{key}", i18n.Msg('ct_string_or_list', key=key))
    ms = n.get("min_speed")
    if "answer" in n:
        value = n["answer"]
        if not ((isinstance(value, str) and value.strip()) or
                (isinstance(value, list) and value and all(isinstance(x, str) and x.strip() for x in value))):
            _err(out, Code.TYPE, f"{path}.answer", "answer must be a nonempty Pokemon name/variant id or list")
    if ms is not None and not _is_int(ms):
        _err(out, Code.TYPE, f"{path}.min_speed", i18n.Msg('ct_min_speed_int'))
    return out


def _check_member_state(state: Any, kind: Any, path: str, out: list[ContractError]) -> None:
    """`member_state` is a survive-only condition: our member's status and stat stages while it takes
    the hit. Speed and kill cliffs are not solved under a stated state, so it is refused there rather
    than silently dropped."""
    if state is None:
        return
    if not isinstance(state, dict):
        _err(out, Code.TYPE, path, "member_state must be an object")
        return
    if kind != "survive":
        _err(out, Code.RANGE, path, "member_state is only valid for survive")
    _check_unknown(state, MEMBER_STATE_FIELDS, path, out)
    status = state.get("status")
    if status is not None and (not _is_str(status) or status not in MEMBER_STATUSES):
        _err(out, Code.ENUM, f"{path}.status",
             f"member_state.status must be one of {sorted(MEMBER_STATUSES)}")
    boosts = state.get("boosts")
    if boosts is None:
        return
    if not isinstance(boosts, dict):
        _err(out, Code.TYPE, f"{path}.boosts", "member_state.boosts must be an object")
        return
    for key, value in boosts.items():
        if key not in BOOST_STAT_KEYS:
            _err(out, Code.ENUM, f"{path}.boosts.{key}",
                 f"member_state.boosts keys must be among {sorted(BOOST_STAT_KEYS)}")
        elif not _is_int(value) or not -6 <= value <= 6:
            _err(out, Code.RANGE, f"{path}.boosts.{key}", "a stat stage must be an integer -6..6")


def check_benchmark(b: Any, path: str = "benchmark") -> list[ContractError]:
    """Validate one tune benchmark. Encodes the `vs`/`conditions`/`screens` contract that drifted."""
    out: list[ContractError] = []
    if not isinstance(b, dict):
        _err(out, Code.TYPE, path, i18n.Msg('ct_benchmark_object'))
        return out
    _check_unknown(b, BENCHMARK_FIELDS, path, out)
    if not _is_str(b.get("member")) or not b["member"].strip():
        _err(out, Code.MISSING, f"{path}.member", i18n.Msg('ct_benchmark_member'))
    kind = b.get("kind")
    if not _is_str(kind) or kind not in BENCHMARK_KINDS:
        _err(out, Code.ENUM, f"{path}.kind", i18n.Msg('ct_kind_enum', allowed=sorted(BENCHMARK_KINDS), got=kind))
    # `vs`: a species name, or a raw Speed number for outspeed only.
    vs = b.get("vs")
    vs_is_num = _is_int(vs) or isinstance(vs, float)
    if vs is None or (not _is_str(vs) and not vs_is_num):
        _err(out, Code.TYPE, f"{path}.vs", i18n.Msg('ct_vs_type'))
    elif vs_is_num and kind != "outspeed":
        _err(out, Code.TYPE, f"{path}.vs", i18n.Msg('ct_vs_outspeed_only', kind=kind))
    if kind in ("survive", "ohko", "2hko") and not _is_str(b.get("move")):
        _err(out, Code.MISSING, f"{path}.move", i18n.Msg('ct_kind_needs_move', kind=kind))
    hits = b.get("hits")
    if hits is not None and (not _is_int(hits) or hits not in (1, 2) or kind != "survive"):
        _err(out, Code.RANGE, f"{path}.hits", "hits must be 1 or 2 and is only valid for survive")
    prob = b.get("probability")
    if prob is not None and (not _is_str(prob) or prob not in PROBABILITIES):
        _err(out, Code.ENUM, f"{path}.probability", i18n.Msg('ct_probability_enum', allowed=sorted(PROBABILITIES)))
    for set_field in ("attacker_set", "opponent_set"):
        if b.get(set_field) is not None and not isinstance(b.get(set_field), dict):
            _err(out, Code.TYPE, f"{path}.{set_field}", f"{set_field} must be an object")
        elif isinstance(b.get(set_field), dict):
            # The target's identity comes only from `vs`; arbitrary calc fields must not override it.
            allowed = {"ability", "item", "nature", "spread", "sps", "moves", "boosts", "status"}
            for key in b[set_field].keys() - allowed:
                _err(out, Code.TYPE, f"{path}.{set_field}.{key}",
                     "unsupported opponent-set field; identity is supplied only by vs")
            for field in ("ability", "item", "nature"):
                if b[set_field].get(field) is not None and not _is_str(b[set_field][field]):
                    _err(out, Code.TYPE, f"{path}.{set_field}.{field}", f"{field} must be a name or null")
            if "moves" in b[set_field] and (not isinstance(b[set_field]["moves"], list)
                                             or any(not _is_str(m) for m in b[set_field]["moves"])):
                _err(out, Code.TYPE, f"{path}.{set_field}.moves", "moves must be a list of names")
            if "boosts" in b[set_field] or "status" in b[set_field]:
                if kind == "outspeed":
                    _err(out, Code.TYPE, f"{path}.{set_field}", "outspeed does not model opponent boosts/status")
                state = {k: b[set_field][k] for k in ("boosts", "status") if k in b[set_field]}
                _check_member_state(state, "survive", f"{path}.{set_field}", out)
            normalized = {}
            for field in ("spread", "sps"):
                raw = b[set_field].get(field)
                if raw is None:
                    continue
                where = f"{path}.{set_field}.{field}"
                if not isinstance(raw, dict):
                    _err(out, Code.TYPE, where, "SP allocation must be an object")
                    continue
                try:
                    spread = canonical_spread(raw)
                except ValueError as exc:
                    _err(out, Code.TYPE, where, str(exc))
                    continue
                normalized[field] = spread
                rs = get_ruleset()
                for key, value in spread.items():
                    if key not in STAT_KEYS:
                        _err(out, Code.ENUM, f"{where}.{key}", "unknown SP stat; use hp/atk/def/spa/spd/spe")
                    elif not _is_int(value):
                        _err(out, Code.TYPE, f"{where}.{key}", "SP must be an integer")
                    elif not 0 <= value <= rs.sp_per_stat_cap:
                        _err(out, Code.RANGE, f"{where}.{key}", f"SP must be 0..{rs.sp_per_stat_cap}")
                if all(_is_int(v) for v in spread.values()) and sum(spread.values()) > rs.sp_total_cap:
                    _err(out, Code.RANGE, where, f"SP total exceeds {rs.sp_total_cap}")
            if len(normalized) == 2 and any(normalized["spread"].get(k, 0) != normalized["sps"].get(k, 0)
                                            for k in set(normalized["spread"]) | set(normalized["sps"])):
                _err(out, Code.TYPE, f"{path}.{set_field}", "spread and sps disagree; supply one allocation")
    _check_member_state(b.get("member_state"), kind, f"{path}.member_state", out)
    conds = b.get("conditions")
    if conds is not None:
        if not isinstance(conds, dict):
            _err(out, Code.TYPE, f"{path}.conditions", i18n.Msg('ct_conditions_object'))
        else:
            for k, v in conds.items():
                if k not in CONDITION_KEYS:
                    _err(out, Code.ENUM, f"{path}.conditions.{k}",
                         i18n.Msg('ct_unknown_condition', k=k, allowed=sorted(CONDITION_KEYS)))
                elif k == "weather" and not (_is_str(v) or isinstance(v, bool)):
                    _err(out, Code.TYPE, f"{path}.conditions.weather", i18n.Msg('ct_weather_type'))
                elif k == "terrain" and not _is_str(v):
                    _err(out, Code.TYPE, f"{path}.conditions.terrain", i18n.Msg('ct_terrain_type'))
                elif k == "screens" and not (isinstance(v, bool) or (_is_str(v) and v in SCREEN_VALUES)):
                    _err(out, Code.ENUM, f"{path}.conditions.screens",
                         i18n.Msg('ct_screens_enum', allowed=sorted(SCREEN_VALUES)))
                elif k in ("stealth_rock", "tailwind", "opponent_tailwind", "trickroom") and not isinstance(v, bool):
                    _err(out, Code.TYPE, f"{path}.conditions.{k}", i18n.Msg('ct_bool', k=k))
                elif k == "spikes" and not (
                    isinstance(v, bool) or (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 3)
                ):
                    _err(out, Code.TYPE, f"{path}.conditions.spikes", i18n.Msg('ct_spikes_type'))
            if _is_str(kind):
                speed_only = {"tailwind", "opponent_tailwind", "trickroom"}
                damage_only = {"stealth_rock", "spikes", "screens"}
                ignored = (speed_only if kind in ("survive", "ohko", "2hko") else
                           damage_only if kind == "outspeed" else set()) & set(conds)
                for k in sorted(ignored):
                    _warn(out, Code.SUSPECT, f"{path}.conditions.{k}",
                          i18n.Msg('ct_condition_ignored_for_kind', k=k, kind=kind))
    return out


def check_evidence(e: Any, path: str = "evidence") -> list[ContractError]:
    """Light validation for an output-side evidence block (facts + note + optional confidence)."""
    out: list[ContractError] = []
    if not isinstance(e, dict):
        _err(out, Code.TYPE, path, i18n.Msg('ct_evidence_object'))
        return out
    if "facts" in e and not isinstance(e["facts"], list):
        _err(out, Code.TYPE, f"{path}.facts", i18n.Msg('ct_facts_list'))
    conf = e.get("confidence")
    if conf is not None and (not _is_str(conf) or conf not in CONFIDENCE):
        _err(out, Code.ENUM, f"{path}.confidence", i18n.Msg('ct_confidence_enum', allowed=sorted(CONFIDENCE)))
    return out


def format_errors(errors: list[ContractError]) -> str:
    if not errors:
        return "Contract: OK"
    lines = ["# " + i18n.Msg('ct_header')]
    for e in errors:
        tag = "ERROR" if e.severity == "error" else "warn "
        lines.append(f"- [{tag}] {e.code} @ {e.path or '(root)'}: {e.message}")
    return "\n".join(lines)
