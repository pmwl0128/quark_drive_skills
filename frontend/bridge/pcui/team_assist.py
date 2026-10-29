"""Bounded tools over one immutable team and data generation.

No model-supplied paths, argv or library writes. Heavy work is injected by the server
and uses the same deterministic semaphore/executor as ordinary team diagnosis.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import time
import unicodedata
from typing import Callable

from . import mappers
from . import assist_tuning
from .online.builder import _op_result, _team_session, canonicalize_members, strip_provenance
from .online.qa import _resolve_query, spread_from_sps
from .paths import SKILLS_ROOT
from .team_evidence import (CHANGE_FIELDS, DEFAULT_CHANGES, bind_report, data_snapshot,
                            input_requirements, read_evidence)

TOOLS = ("team_evidence", "opponent_builds", "member_calc", "member_options", "replacement_candidates",
         "selection_options", "tune_member", "test_change")
MAX_TOOL_CALLS = 6
MAX_CHANGE_TESTS = 1
CALC_MODES = ("attack", "defend", "speed")
CALC_TAILWIND = ("member", "opponent", "both")
NEED_TYPE_KEYS = ("resist", "offense_type", "coverage_move_type")
REPLACEMENT_CANDIDATES = 6


def structure_diff(raw: dict) -> dict:
    """The team skill's `replace` diff of one swap, compact: shared weaknesses, coverage gaps, the two
    members' speeds and the roles lost or gained. Facts of the swap, never a better/worse verdict."""
    defense, offense, speed, role = (raw.get(k) or {} for k in ("defense", "offense", "speed", "roles"))
    weak = lambda rows: [{"type": r["type"], "from": r["from"], "to": r["to"]} for r in rows or []]
    speeds = None
    if (speed.get("out") or {}).get("speed") is not None and (speed.get("in") or {}).get("speed") is not None:
        speeds = {"out": speed["out"]["speed"], "in": speed["in"]["speed"], "delta": speed.get("delta")}
    return {"out": raw.get("out"), "in": raw.get("in"),
            "sharedWeaknessWorsened": weak(defense.get("shared_weakness_worsened")),
            "sharedWeaknessEased": weak(defense.get("shared_weakness_eased")),
            **{key: list(offense.get(src) or []) for key, src in (
                ("hardGapsAdded", "hard_gaps_added"), ("hardGapsRemoved", "hard_gaps_removed"),
                ("thinAdded", "thin_added"), ("thinRemoved", "thin_removed"))},
            "speed": speeds, "rolesLost": list(role.get("roles_lost") or []),
            "rolesLostRequired": list(role.get("roles_lost_required") or []),
            "rolesGained": list(role.get("roles_gained") or [])}


def _replace_op(team: dict, out_species: str, member: dict) -> dict:
    keep = ("species", "moves", "item", "ability", "nature", "spread")
    return {"op": "replace", "file": team, "context": {"format": team["format"], "replace": {
        "member": out_species, "with": {k: member.get(k) for k in keep if member.get(k) is not None}}}}


def _structures(entries: list) -> list[dict | None]:
    """Each session row's diff, or None for a row the skill could not diff (the swap is still reported)."""
    out = []
    for entry in entries:
        try:
            out.append(structure_diff(_op_result(entry)))
        except Exception:
            out.append(None)
    return out


def roles() -> list[str]:
    """The team skill's functional-role taxonomy, which `fill` matches a role need against."""
    from .online.qa_facts import _team_module
    return sorted(_team_module("diagnose")._ROLE_MOVES)


class AssistToolError(ValueError):
    """`detail` reaches the model only (which name the dex refused); the public trace keeps the code."""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _grade(cell: dict | None) -> str | None:
    return (cell.get("check") or {}).get("grade") if cell else None


def _direction(before, after) -> str:
    order = {"C0": 0, "C1": 1, "C2": 2}
    if before not in order or after not in order:
        return "unknown"
    return "gain" if order[after] > order[before] else "loss"


def _fold(text: str) -> str:
    """Full-width forms to their plain characters (メガリザードンＹ = メガリザードンY) and ASCII lower-cased,
    one character for one, so match offsets stay those of the original text."""
    out = []
    for c in text:
        plain = unicodedata.normalize("NFKC", c)
        c = plain if len(plain) == 1 else c
        out.append(c.lower() if c.isascii() else c)
    return "".join(out)


@lru_cache(maxsize=1)
def _pokemon_names() -> tuple[re.Pattern, dict[str, tuple[str, str]]]:
    """Every dex Pokemon name, alias and lookup nickname -> (canonical, identity). The identity is the
    base species for a Mega (the same Pokemon before Mega Evolution) and the canonical for every other
    form (Rotom-Wash is not Rotom-Heat). One-character CJK aliases (喷) are left out: inside free text
    they match unrelated words."""
    conn = mappers._conn()
    base = {row[0]: (row[1] or row[0]) if row[2] else row[0]
            for row in conn.execute("select canonical, base_species, is_mega from pokemon")}
    names: dict[str, tuple[str, str]] = {}

    def add(name, canonical):
        if not name or canonical not in base or len(name) < (3 if name.isascii() else 2):
            return
        names.setdefault(_fold(name), (canonical, base[canonical]))

    for canonical, zh, ja in conn.execute("select canonical, display_name, display_name_ja from pokemon"):
        for name in (canonical, zh, ja):
            add(name, canonical)
    for alias, canonical in conn.execute("select alias, canonical from aliases where kind = 'pokemon'"):
        add(alias, canonical)
    nicknames = SKILLS_ROOT / "pokemon-champions-dex/data/query_aliases.json"
    if nicknames.is_file():
        for canonical, aliases in json.loads(nicknames.read_text(encoding="utf-8")).get("pokemon", {}).items():
            for alias in aliases:
                add(alias, canonical)
    parts = [re.escape(n) if not n.isascii() else rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])"
             for n in sorted(names, key=len, reverse=True)]
    return re.compile("|".join(parts)), names


def goal_targets(report: dict, texts: list[str]) -> dict:
    """The Pokemon a goal or question names, mapped onto this report: opponents to their team-floor
    rows (a Mega to its species row) and members to their slots. The model then reads the row the
    user meant instead of the first C0 row or a similarly named Pokemon."""
    pattern, names = _pokemon_names()
    rows = {r["opponent"]: r for r in (report.get("checks") or {}).get("byOpponent") or []}
    member_slots: dict[str, int] = {}
    for slot, member in enumerate(report["team"]["pokemon"]):
        found = names.get(_fold(member["species"]))
        member_slots.setdefault(found[1] if found else member["species"], slot)
    opponents, members, mirrors = [], [], []
    for text in texts:
        for match in pattern.finditer(_fold(text or "")):
            canonical, species = names[match.group(0)]
            mention = text[match.start():match.end()]
            row = rows.get(canonical) or rows.get(species)
            entry = row and {"mention": mention, "pokemon": canonical, "reportOpponent": row["opponent"],
                             "teamFloor": {"ref": row["ref"], "grade": row.get("observedFloor")}}
            if species in member_slots:
                if not any(m["slot"] == member_slots[species] for m in members):
                    members.append({"mention": mention, "pokemon": canonical, "slot": member_slots[species]})
                if entry:
                    mirrors.append(entry)
            elif entry and not any(o["reportOpponent"] == row["opponent"] for o in opponents):
                opponents.append(entry)
    # A member's own species is an opponent only when nothing else is (a mirror question).
    return {"opponents": (opponents or mirrors)[:3], "members": members[:3]}


def compare_reports(before: dict, after: dict) -> dict:
    """Compare the skill's grades at their original level; never create a new grade."""
    old_rows = {r["opponent"]: r for r in (before.get("checks") or {}).get("byOpponent") or []}
    new_rows = {r["opponent"]: r for r in (after.get("checks") or {}).get("byOpponent") or []}
    floors, cells, unpaired = [], [], []
    comparable = unchanged = 0
    old_slots = {m["species"]: i for i, m in enumerate(before["team"]["pokemon"])}
    new_slots = {m["species"]: i for i, m in enumerate(after["team"]["pokemon"])}
    for opponent in sorted(old_rows.keys() | new_rows.keys()):
        old, new = old_rows.get(opponent), new_rows.get(opponent)
        old_variants = {v["ref"]["variantId"] for v in (old or {}).get("variants") or []}
        new_variants = {v["ref"]["variantId"] for v in (new or {}).get("variants") or []}
        if not old or not new or not old.get("calculationComplete") or not new.get("calculationComplete") \
                or not old_variants or old_variants != new_variants \
                or old.get("observedFloor") not in ("C0", "C1", "C2") \
                or new.get("observedFloor") not in ("C0", "C1", "C2"):
            unpaired.append(opponent)
        else:
            comparable += 1
            if old["observedFloor"] != new["observedFloor"]:
                floors.append({"opponent": opponent, "before": old["observedFloor"],
                               "after": new["observedFloor"], "beforeRef": old["ref"],
                               "afterRef": new["ref"],
                               "direction": _direction(old["observedFloor"], new["observedFloor"])})
            else:
                unchanged += 1
        old_cells = {(old_slots[c["member"]], c["variantId"]): c for c in (old or {}).get("cells") or []}
        new_cells = {(new_slots[c["member"]], c["variantId"]): c for c in (new or {}).get("cells") or []}
        for key in sorted(old_cells.keys() | new_cells.keys()):
            a, b = old_cells.get(key), new_cells.get(key)
            if _grade(a) == _grade(b) and (a or {}).get("check") == (b or {}).get("check"):
                continue
            cells.append({"opponent": opponent, "memberSlot": key[0], "variantId": key[1],
                          "beforeMember": a["member"] if a else None,
                          "afterMember": b["member"] if b else None,
                          "before": _grade(a), "after": _grade(b),
                          "beforeRef": a.get("ref") if a else None,
                          "afterRef": b.get("ref") if b else None,
                          "direction": (_direction(_grade(a), _grade(b))
                                        if _grade(a) != _grade(b) else "qualification"),
                          "beforeContested": ((a or {}).get("check") or {}).get("resolvability") == "contested",
                          "afterContested": ((b or {}).get("check") or {}).get("resolvability") == "contested"})
    return {"teamFloorChanges": floors, "memberCellChanges": cells,
            "coverage": {"topK": (before.get("checks") or {}).get("topK") or 0,
                         "comparableOpponents": comparable, "unchangedTeamFloors": unchanged,
                         "uncomparedOpponents": unpaired},
            "hasTeamGains": any(r["direction"] == "gain" for r in floors),
            "hasTeamLosses": any(r["direction"] == "loss" for r in floors)}


class TeamAssist:
    def __init__(self, pool, report: dict, *, projection_dir: Path | None = None,
                 run_heavy: Callable[[Callable], dict] | None = None,
                 deadline: float | None = None, read_cache: dict | None = None,
                 previous_actions: list | None = None, question: str = "", previous_questions: list | None = None,
                 allow_test: bool = True):
        if (report.get("analysis") or {}).get("status") != "ready":
            raise AssistToolError("needs_input")
        self.pool = pool
        self.report = deepcopy(report)
        self.team = self.report["team"]
        self.analysis = self.report["analysis"]
        self.snapshot = self.analysis["snapshot"]
        self.projection_dir = projection_dir
        self.run_heavy = run_heavy or (lambda work: work())
        self.deadline = deadline or time.monotonic() + 300
        self.trace: list[dict] = []
        self.actions: list[dict] = []
        self.tunings: list[dict] = []
        self.metrics: dict = {}
        self.notes: list[dict] = []
        self._cache = {key: deepcopy(value) for key, value in (read_cache or {}).items() if key[0] != "test_change"}
        self.previous_actions = deepcopy(previous_actions or [])
        self.question = question
        self.previous_questions = list(previous_questions or [])
        self.allow_test = allow_test
        self.can_test = allow_test and bool(self.analysis.get("allowedChanges", list(DEFAULT_CHANGES)))
        self.can_tune = "spread" in self.analysis.get("allowedChanges", list(DEFAULT_CHANGES)) \
            and self.report["legality"]["status"] == "valid" \
            and any(i not in self.analysis["lockedSlots"] for i in range(len(self.team["pokemon"])))
        self.can_replace = "species" in self.analysis.get("allowedChanges", list(DEFAULT_CHANGES))             and any(i not in self.analysis["lockedSlots"] for i in range(len(self.team["pokemon"])))
        self.targets = goal_targets(self.report, [question, self.analysis.get("goal") or ""])
        self._tests = 0
        self._secret = secrets.token_bytes(32)
        self._sets: dict | None = None

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise AssistToolError("deadline")
        return remaining

    def check_snapshot(self) -> None:
        self.remaining()
        if data_snapshot(self.pool) != self.snapshot:
            raise AssistToolError("snapshot_changed")

    def run(self, name: str, args: dict) -> dict:
        started = time.monotonic()
        actions_before = len(self.actions)
        tunings_before = len(self.tunings)
        try:
            if name not in TOOLS or not isinstance(args, dict) \
                    or len(json.dumps(args, ensure_ascii=False)) > 14_000:
                raise AssistToolError("bad_tool_input")
            key = (name, _digest(args))
            self.check_snapshot()
            if len(self.trace) >= MAX_TOOL_CALLS:
                raise AssistToolError("tool_limit")
            if key in self._cache:
                cached = self._cache.pop(key)
                self._cache[key] = cached
                if name == "tune_member":
                    self._retain_tuning(cached)
                self.trace.append({"tool": name, "status": "done", "durationMs": round((time.monotonic()-started)*1000),
                                   "cacheHit": True})
                return assist_tuning.for_model(cached) if name == "tune_member" else deepcopy(cached)
            work = lambda: self._dispatch(name, args)
            result = self.run_heavy(work)
            self.check_snapshot()
            self._cache[key] = deepcopy(self.tunings[-1] if name == "tune_member" else result)
            status, code = "done", None
        except (AssistToolError, assist_tuning.TuningInputError) as exc:
            del self.actions[actions_before:]
            del self.tunings[tunings_before:]
            status, code = "error", exc.code if isinstance(exc, AssistToolError) else str(exc)
            detail = getattr(exc, "detail", None)
            result = {"error": {"code": code, **({"detail": detail[:200]} if detail else {})}}
        except Exception:
            del self.actions[actions_before:]
            del self.tunings[tunings_before:]
            # Neither worker diagnostics nor arbitrary model input is public error text.
            status, code = "error", "tool_failed"
            result = {"error": {"code": code}}
        if len(self.trace) < MAX_TOOL_CALLS:
            self.trace.append({"tool": name if name in TOOLS else "unknown", "status": status,
                               "durationMs": round((time.monotonic() - started) * 1000),
                               **({"errorCode": code} if code else {})})
        return result

    def _dispatch(self, name: str, args: dict) -> dict:
        if name == "team_evidence":
            if set(args) != {"ref"} or not isinstance(args["ref"], dict):
                raise AssistToolError("bad_tool_input")
            return self.evidence(args["ref"])
        if name == "member_options":
            if set(args) != {"pokemon"} or not isinstance(args["pokemon"], str) \
                    or not 1 <= len(args["pokemon"]) <= 100 or args["pokemon"].startswith("-"):
                raise AssistToolError("bad_tool_input")
            return self.member_options(args["pokemon"])
        if name == "selection_options":
            if args:
                raise AssistToolError("bad_tool_input")
            if self.report["legality"]["status"] != "valid":
                raise AssistToolError("team_not_valid")
            result = _op_result(_team_session(self.pool, [{"op": "select", "file": self.team}],
                                             self.deadline)[0])
            if "error" in result:
                raise AssistToolError("tool_failed")
            return strip_provenance(result)
        if name == "test_change":
            return self.test_change(args)
        if name == "tune_member":
            return self.tune_member(args)
        if name == "opponent_builds":
            if set(args) != {"ref"} or not isinstance(args["ref"], dict):
                raise AssistToolError("bad_tool_input")
            return self.opponent_builds(args["ref"])
        if name == "member_calc":
            return self.member_calc(args)
        if name == "replacement_candidates":
            return self.replacement_candidates(args)
        raise AssistToolError("bad_tool_input")

    def _catalog(self) -> dict:
        if self._sets is None:
            if self.projection_dir is None:
                return {}
            path = self.projection_dir / "matchup" / f"oppsets_{self.analysis['format']}.json"
            from .capabilities import source_fingerprints
            manifest = json.loads((self.projection_dir / "manifest.json").read_text(encoding="utf-8"))
            environment = manifest.get("environment") or {}
            actual = source_fingerprints(self.snapshot["season"], self.snapshot["rule"])
            if environment.get("season") != self.snapshot["season"] or environment.get("rule") != self.snapshot["rule"] \
                    or (environment.get("sourceFingerprints") or {}).get(self.analysis["format"]) != actual[self.analysis["format"]]:
                raise AssistToolError("catalog_unavailable")
            if path.stat().st_size > 8_000_000:
                raise AssistToolError("catalog_unavailable")
            self._sets = json.loads(path.read_text(encoding="utf-8")).get("sets") or {}
        return self._sets

    def evidence(self, ref: dict) -> dict:
        if ref.get("scope") != "team_floor":
            raise AssistToolError("bad_evidence_ref")
        try:
            floor = read_evidence(self.report, ref)
        except ValueError:
            raise AssistToolError("bad_evidence_ref") from None
        row = next(r for r in self.report["checks"]["byOpponent"] if r["ref"] == ref)
        witness_ids = row["witnessVariantIds"]
        displayed = witness_ids[:2]
        catalog = self._catalog()
        variants = []
        mechanics = []
        for vid in displayed:
            build = catalog.get(mappers.opponent_key(vid))
            if build:
                build = {k: build.get(k) for k in ("species", "runForm", "item", "ability", "nature", "moves", "sps")}
                for kind in ("ability", "item"):
                    value = build.get(kind)
                    if value and not any(m.get("kind") == kind and m.get("name") == value for m in mechanics):
                        raw = self.pool.request_json("dex", [kind, value, "--format", "json"],
                                                     timeout=min(15, self.remaining()))
                        mechanics.append({"kind": kind, **{k: raw[k] for k in
                            ("name", "effect_en", "effect_ja") if k in raw}})
            variants.append({"variantId": vid, "opponentBuild": build,
                             "cells": [read_evidence(self.report, c["ref"]) for c in row["cells"]
                                       if c["variantId"] == vid]})
        return {"teamFloor": floor, "witnesses": variants, "totalWitnesses": len(witness_ids),
                "displayedWitnesses": len(displayed), "mechanics": mechanics,
                "scope": "static_observed_builds_not_turn_simulation"}

    def _floor_row(self, ref: dict) -> dict:
        if ref.get("scope") != "team_floor":
            raise AssistToolError("bad_evidence_ref")
        try:
            read_evidence(self.report, ref)
        except ValueError:
            raise AssistToolError("bad_evidence_ref") from None
        return next(r for r in self.report["checks"]["byOpponent"] if r["ref"] == ref)

    def opponent_builds(self, ref: dict) -> dict:
        """Every retained observed build of one report opponent, with the grade the skill gave this
        team's best member against it (team_evidence shows only two witnesses)."""
        row = self._floor_row(ref)
        catalog = self._catalog()
        builds = []
        for variant in row.get("variants") or []:
            vid = variant["ref"]["variantId"]
            build = catalog.get(mappers.opponent_key(vid))
            builds.append({"variantId": vid, "teamGrade": variant.get("grade"),
                           "isFloorWitness": vid in row["witnessVariantIds"],
                           "build": {k: build.get(k) for k in ("species", "runForm", "item", "ability", "nature",
                                                                "moves", "sps", "coverage", "isModal")}
                           if build else None})
        return {"opponent": row["opponent"], "teamFloor": row.get("observedFloor"), "builds": builds,
                "note": "teamGrade = the best grade any member of this team has against that observed build; the "
                        "team floor is the weakest of them. coverage = that build's share of the opponent's "
                        "observed sample. Static observed builds, not every possible set."}

    def member_calc(self, args: dict) -> dict:
        """One damage or speed calculation with a member's ACTUAL set against a named opponent: its
        most-used set of the named form, with any stated parts on top (the QA calc semantics)."""
        from .online import calc_scenario, qa
        from .team_evidence import qualified_damage
        if set(args) - {"slot", "opponent", "mode", "move", "opponentSet", "field", "tailwind"} \
                or not {"slot", "opponent", "mode"} <= set(args):
            raise AssistToolError("bad_tool_input")
        slot, mode, opponent, move = args["slot"], args["mode"], args["opponent"], args.get("move")
        if type(slot) is not int or not 0 <= slot < len(self.team["pokemon"]) or mode not in CALC_MODES \
                or not isinstance(opponent, str) or not 1 <= len(opponent) <= 100 or opponent.startswith("-") \
                or (mode != "speed" and (not isinstance(move, str) or not 1 <= len(move) <= 100)) \
                or not isinstance(args.get("opponentSet") or {}, dict) or not isinstance(args.get("field") or {}, dict) \
                or args.get("tailwind") not in (None, *CALC_TAILWIND):
            raise AssistToolError("bad_tool_input")
        member = self.team["pokemon"][slot]
        fmt = self.analysis["format"]
        timeout = min(20, self.remaining())
        try:
            opp, basis, _usual, notes = qa._stated_side(self.pool, fmt, opponent, args.get("opponentSet") or None,
                                                       "opponentSet", timeout, self.remaining(), qa._modal_snapshot(self.pool))
            mine = {"name": member["species"], "sps": {k: int((member.get("spread") or {}).get(k) or 0)
                                                       for k in calc_scenario.SPREAD_KEYS},
                    **{k: member[k] for k in ("item", "ability", "nature") if member.get(k)}}
            (mine, _src), (opp, basis) = qa._battle_forms(self.pool, [(mine, "actual member"), (opp, basis)], timeout)
            field = calc_scenario.field_payload(fmt, args.get("field") or None)
        except calc_scenario.NotInGame as exc:
            raise AssistToolError("not_in_game", str(exc)) from None
        except calc_scenario.ScenarioError as exc:
            raise AssistToolError("bad_scenario", str(exc)) from None
        except qa.NameResolutionError:
            raise AssistToolError("bad_scenario", "the dex found this name ambiguous; name the exact form") from None
        public = lambda c: {k: v for k, v in c.items() if not k.startswith("_") and k != "name"}
        result = {"mode": mode, "member": {"slot": slot, "name": mine["name"], "set": public(mine)},
                  "opponent": {"name": opp["name"], "set": public(opp), "basis": basis}}
        if mode == "speed":
            sides = []
            for combatant, own in ((mine, "member"), (opp, "opponent")):
                side = {k: field[k] for k in ("weather", "terrain") if field.get(k)}
                if args.get("tailwind") in (own, "both"):
                    side["tailwind"] = True
                sides.append(qa._speed_input(combatant, side or None))
            docs = self.pool.request_json("speedline", ["batch"], json.dumps(sides, ensure_ascii=False),
                                          timeout=timeout)
            speeds = [d.get("finalSpeed") if isinstance(d, dict) else None for d in docs or []]
            if len(speeds) != 2 or not all(isinstance(s, int) for s in speeds):
                raise AssistToolError("tool_failed")
            result["member"]["finalSpeed"], result["opponent"]["finalSpeed"] = speeds
            result["faster"] = "tie" if speeds[0] == speeds[1] else "member" if speeds[0] > speeds[1] else "opponent"
            result["note"] = ("Speed values only, with the stated weather/terrain and Tailwind; move priority and "
                              "action order are not verified.")
        else:
            resolved = calc_scenario.resolve_strict(self.pool, move, "move", timeout)
            if resolved is None:
                raise AssistToolError("not_in_game", f"move {move!r} is not in Pokemon Champions")
            attacker, defender = (mine, opp) if mode == "attack" else (opp, mine)
            raw = self.pool.request_json("calc", ["one"], json.dumps(
                calc_scenario.calc_payload(attacker, defender, resolved, field), ensure_ascii=False), timeout=timeout)
            if not isinstance(raw, dict) or raw.get("error"):
                raise AssistToolError("tool_failed")
            mapped = mappers.map_damage(raw)
            damage = qualified_damage({k: mapped.get(k) for k in ("minPercent", "maxPercent", "koChance", "koCaveats")
                                       if mapped.get(k) is not None})
            if not damage.get("koCaveats"):
                damage.pop("koExact", None)
                damage.pop("qualification", None)
                if mapped.get("koChance"):
                    damage["koChance"] = mapped["koChance"]
            if mode == "attack" and resolved not in (member.get("moves") or []):
                notes.append(f"{resolved} is not one of this member's moves: a hypothetical move; changing moves "
                             "needs test_change")
            learners = self.pool.request_json("dex", ["find", "move", resolved, "--format", "json"], timeout=timeout)
            if isinstance(learners, dict) and isinstance(learners.get("results"), list) \
                    and attacker["name"] not in {r.get("name") for r in learners["results"] if isinstance(r, dict)}:
                notes.append(f"{attacker['name']} cannot learn {resolved} in Pokemon Champions; computed as asked")
            if (weather := qa._weather_note([attacker, defender], field)):
                notes.append(weather)
            result.update(move=resolved, damage=damage, conditions=qa._conditions({"field": field, "format": fmt}, mapped),
                          note="One hit's damage in percent of the defender's HP and its KO chance, from the static "
                               "calculator; a Mega Stone holder is calculated as its Mega form. No turn sequence.")
        if notes:
            result["notes"] = notes
        return result

    def replacement_candidates(self, args: dict) -> dict:
        """`fill` candidates for one replaceable slot: the usage pool filtered by a structured need, in
        the skill's explicit views, each with a full joint set that a test_change can try as it is."""
        from .online.qa_facts import type_name
        allowed = self.analysis.get("allowedChanges", list(DEFAULT_CHANGES))
        if "species" not in allowed:
            raise AssistToolError("change_not_allowed")
        need = args.get("need")
        if set(args) != {"slot", "need"} or type(args["slot"]) is not int \
                or not 0 <= args["slot"] < len(self.team["pokemon"]) or not isinstance(need, dict) or not need \
                or set(need) - {*NEED_TYPE_KEYS, "role", "min_speed"}:
            raise AssistToolError("bad_tool_input")
        if args["slot"] in self.analysis["lockedSlots"]:
            raise AssistToolError("locked_member")
        clean: dict = {}
        for key in NEED_TYPE_KEYS:
            if key in need:
                values = need[key] if isinstance(need[key], list) else [need[key]]
                types = [type_name(v) for v in values[:3] if isinstance(v, str)]
                if not values or len(types) != len(values) or None in types:
                    raise AssistToolError("bad_tool_input")
                clean[key] = types
        if "role" in need:
            values = need["role"] if isinstance(need["role"], list) else [need["role"]]
            if not 1 <= len(values) <= 2 or any(v not in roles() for v in values):
                raise AssistToolError("bad_tool_input")
            clean["role"] = values
        if "min_speed" in need:
            if type(need["min_speed"]) is not int or not 1 <= need["min_speed"] <= 400:
                raise AssistToolError("bad_tool_input")
            clean["min_speed"] = need["min_speed"]
        fmt = self.analysis["format"]
        rest = deepcopy(self.team)
        removed = rest["pokemon"].pop(args["slot"])
        items = {m["item"] for m in rest["pokemon"] if m.get("item")}
        context = {"format": fmt, "need": clean, "avoid": [removed["species"], *sorted(items)]}
        result = _op_result(_team_session(self.pool, [{"op": "fill", "file": rest, "context": context}],
                                          self.deadline)[0])
        candidates = (result.get("candidates") or [])[:REPLACEMENT_CANDIDATES]
        reps = _team_session(self.pool, [{"op": "repset", "species": c["species"], "game_format": fmt,
                                          "max_clusters": 3} for c in candidates],
                             self.deadline) if candidates else []
        sets = []
        for i, candidate in enumerate(candidates):
            archetypes = (_op_result(reps[i]).get("archetypes") or []) if i < len(reps) else []
            arch = next((a for a in archetypes if isinstance(a, dict) and a.get("item") not in items
                         and a.get("moves")), None)
            sets.append({"species": arch.get("species") or candidate["species"], "moves": arch["moves"],
                         "item": arch.get("item"), "ability": arch.get("ability"), "nature": arch.get("nature"),
                         "spread": spread_from_sps(arch.get("sps"))} if arch else None)
        # One session diffs every offered set against the member it would replace (team skill `replace`).
        offered = [i for i, s in enumerate(sets) if s]
        diffs = dict(zip(offered, _structures(_team_session(self.pool, [
            _replace_op(self.team, removed["species"], sets[i]) for i in offered], self.deadline)))) if offered else {}
        rows = []
        for i, candidate in enumerate(candidates):
            row = {"species": candidate["species"], "types": candidate.get("types"),
                   "addresses": strip_provenance(candidate.get("addresses")), "usageRank": candidate.get("usage_rank"),
                   "maxSpeed": candidate.get("max_speed")}
            if sets[i]:
                row["set"] = sets[i]
            if diffs.get(i):
                row["structure"] = {k: v for k, v in diffs[i].items() if k not in ("out", "in")}
            rows.append(row)
        views = {k: v[:REPLACEMENT_CANDIDATES] for k, v in (result.get("views") or {}).items() if v}
        return {"slot": args["slot"], "replaces": removed["species"], "need": clean, "candidates": rows,
                "candidateCount": result.get("candidate_count"), "views": views,
                "note": "Candidates are usage-pool Pokemon that meet EVERY need criterion, avoiding items already "
                        "held by the other members. Views are orderings (usage, off_meta, co_occurrence, "
                        "tournament_sample), never a score or a best pick. set is the candidate's most common "
                        "joint real-team set without a clashing item. A candidate's set is ready for test_change "
                        "as it is (slot + that set); member_options is needed only for a candidate without one. "
                        "structure = the static team diff of that swap (shared weaknesses, coverage gaps, the two "
                        "speeds, roles lost or gained), facts and not a verdict. Before discussing a candidate as "
                        "an improvement, test it."}

    def member_options(self, query: str) -> dict:
        canonical = _resolve_query(self.pool, query, timeout=min(15, self.remaining()))
        raw = self.pool.request_json("dex", ["pokemon", canonical, "--format", "json"],
                                     timeout=min(20, self.remaining()))
        if not isinstance(raw, dict) or "error" in raw or not raw.get("name"):
            raise AssistToolError("unknown_pokemon")
        reps = _op_result(_team_session(self.pool, [{"op": "repset", "species": raw["name"],
            "game_format": self.analysis["format"], "max_clusters": 3}], self.deadline)[0])
        if "error" in reps:
            raise AssistToolError("tool_failed")
        return {"pokemon": raw["name"], "dex": {k: raw.get(k) for k in
                ("types", "stats", "abilities", "required_item", "moves")},
                "representativeSets": strip_provenance(reps)}

    def test_change(self, args: dict) -> dict:
        if not self.allow_test:
            raise AssistToolError("read_only")
        changes = args.get("changes")
        if set(args)-{"tuningId"} != {"changes"} or not isinstance(changes, list) or not 1 <= len(changes) <= 2:
            raise AssistToolError("bad_tool_input")
        tuning = self._tuning(args["tuningId"]) if "tuningId" in args else None
        if self._tests >= MAX_CHANGE_TESTS:
            raise AssistToolError("change_limit")
        candidate = deepcopy(self.team)
        visited = set()
        allowed = self.analysis.get("allowedChanges", list(DEFAULT_CHANGES))
        for change in changes:
            if not isinstance(change, dict) or set(change) != {"slot", "set"}:
                raise AssistToolError("bad_tool_input")
            slot, patch = change["slot"], change["set"]
            if type(slot) is not int or not 0 <= slot < len(candidate["pokemon"]) or slot in visited \
                    or not isinstance(patch, dict) or not patch or set(patch) - set(CHANGE_FIELDS):
                raise AssistToolError("bad_tool_input")
            visited.add(slot)
            for key, value in patch.items():
                if value is None and key != "item":
                    raise AssistToolError("bad_tool_input")
                if key == "moves":
                    if not isinstance(value, list) or not 1 <= len(value) <= 4 or any(
                            not isinstance(v, str) or not 1 <= len(v) <= 100 or v.startswith("-") for v in value):
                        raise AssistToolError("bad_tool_input")
                elif key == "spread":
                    if not isinstance(value, dict) or not value or set(value) - {"hp", "atk", "def", "spa", "spd", "spe"} \
                            or any(type(v) is not int or not 0 <= v <= 999 for v in value.values()):
                        raise AssistToolError("bad_tool_input")
                elif value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 100 or value.startswith("-")):
                    raise AssistToolError("bad_tool_input")
            patch = deepcopy(patch)
            replaced = "species" in patch and _resolve_query(self.pool, patch["species"], timeout=min(
                15, self.remaining())) != candidate["pokemon"][slot]["species"]
            if "spread" in patch and not replaced:
                # Listed stats replace the current values; an omitted stat keeps its own. A new
                # species' spread is its own complete allocation, never merged into the old member's.
                patch["spread"] = {**(candidate["pokemon"][slot].get("spread") or {}), **patch["spread"]}
            candidate["pokemon"][slot].update(patch)
            candidate["pokemon"][slot]["completeness"] = "extracted_set"
        canonicalize_members(self.pool, candidate["pokemon"], min(self.deadline, time.monotonic() + 40))
        normalized = [{"slot": slot, "species": self.team["pokemon"][slot]["species"],
                       "before": {k: self.team["pokemon"][slot].get(k) for k in CHANGE_FIELDS
                                  if self.team["pokemon"][slot].get(k) != candidate["pokemon"][slot].get(k)},
                       "after": {k: candidate["pokemon"][slot].get(k) for k in CHANGE_FIELDS
                                 if self.team["pokemon"][slot].get(k) != candidate["pokemon"][slot].get(k)}}
                      for slot in sorted(visited)]
        normalized = [change for change in normalized if change["before"]]
        if not normalized:
            raise AssistToolError("no_change")
        if any(change["slot"] in self.analysis["lockedSlots"] for change in normalized):
            raise AssistToolError("locked_member")
        if any(set(change["after"]) - set(allowed) for change in normalized):
            raise AssistToolError("change_not_allowed")
        if not tuning and any(set(change["after"]) & {"spread", "nature"} and
                any(name == "tune_member" and receipt.get("memberSlot") == change["slot"]
                    for (name, _), receipt in self._cache.items()) for change in normalized):
            raise AssistToolError("tuning_link_required")
        if tuning and (len(normalized) != 1 or normalized[0]["slot"] != tuning["memberSlot"]
                       or set(normalized[0]["after"])-{"spread", "nature"}):
            raise AssistToolError("tuning_change_mismatch")
        if any(m["missingFields"] for m in input_requirements(candidate)):
            raise AssistToolError("needs_input")
        self._tests += 1
        self.check_snapshot()
        legality = _op_result(_team_session(self.pool, [{"op": "validate", "file": candidate}],
                                           self.deadline)[0], allow_rc=(0, 1))
        if "error" in legality or legality.get("status") not in ("valid", "invalid", "unknown"):
            raise AssistToolError("tool_failed")
        card = {"analysisId": self.analysis["id"], "changes": normalized,
                "candidateTeam": candidate, "legality": {
                    "status": legality.get("status") or "unknown",
                    "issues": mappers.map_diagnose_report(candidate, legality, {})["legality"]["issues"]},
                "status": "invalid", "teamFloorChanges": [], "memberCellChanges": [],
                "coverage": {"topK": (self.report.get("checks") or {}).get("topK") or 0,
                             "comparableOpponents": 0, "unchangedTeamFloors": 0,
                             "uncomparedOpponents": []}, "hasTeamGains": False, "hasTeamLosses": False,
                "limits": ["static_matchups", "damage_only_changes_not_summarized"]}
        if legality.get("status") == "valid":
            if tuning:
                checked = assist_tuning.calculate(self.pool, candidate, tuning["benchmarks"], self.deadline,
                    allow_nature="nature" in allowed)
                card["tuningCheck"] = {"tuningId": tuning["tuningId"], "targets": deepcopy(tuning["targets"]),
                    "before": deepcopy(tuning["result"]), "after": checked,
                    "allThresholdsMet": all(c["result"] == "already" for c in checked["cards"])}
            # The structural diff of each changed slot rides in the same session as the battery.
            session = _team_session(self.pool, [{"op": "diagnose", "file": candidate, "aspect": "all",
                "with_check": True, "top_k": card["coverage"]["topK"]}] + [
                _replace_op(self.team, change["species"], candidate["pokemon"][change["slot"]])
                for change in normalized], self.deadline)
            raw = _op_result(session[0])
            if "error" in raw or not isinstance(raw.get("check_coverage"), dict):
                raise AssistToolError("calculation_unavailable")
            card["structureChanges"] = [{"slot": change["slot"], **diff} for change, diff in
                                        zip(normalized, _structures(session[1:])) if diff]
            intent = {k: self.analysis[k] for k in ("goal", "lockedSlots", "allowedChanges") if k in self.analysis}
            mapped = mappers.map_diagnose_report(candidate, legality, raw)
            variants = ((raw.get("check_coverage") or {}).get("check_coverage") or {}).get("by_opponent") or []
            after = bind_report(mapped, self.snapshot, intent, input_requirements(candidate), variants)
            card.update(compare_reports(self.report, after), status="tested", afterAnalysisId=after["analysis"]["id"])
        self.check_snapshot()
        # IDs are minted solely from host-verified facts. A model cannot mint a tested card.
        nonce = secrets.token_hex(12)
        signature = hmac.new(self._secret, f"{nonce}:{_digest(card)}".encode(), hashlib.sha256).hexdigest()
        card["verificationId"] = f"v1.{nonce}.{signature}"
        self.actions.append(card)
        return self.card_for_model(card)

    @staticmethod
    def card_for_model(card: dict) -> dict:
        result = {**{k: deepcopy(card[k]) for k in ("verificationId", "analysisId", "status", "changes",
                    "legality", "teamFloorChanges", "coverage", "hasTeamGains", "hasTeamLosses", "limits",
                    "structureChanges") if k in card},
                "memberCellChanges": deepcopy(card["memberCellChanges"][:12]),
                "memberCellChangesTotal": len(card["memberCellChanges"])}
        if card.get("tuningCheck"):
            check = card["tuningCheck"]
            result["tuningCheck"] = {"tuningId": check["tuningId"], "targets": deepcopy(check["targets"]),
                "before": assist_tuning.thresholds(check["before"]), "after": assist_tuning.thresholds(check["after"]),
                "allThresholdsMet": check["allThresholdsMet"]}
        return result

    def public_result(self) -> dict:
        return {"toolTrace": deepcopy(self.trace), "actions": deepcopy(self.actions), "tunings": deepcopy(self.tunings),
                "notes": deepcopy(self.notes), "metrics": deepcopy(self.metrics) or None}

    def export_reads(self) -> dict:
        return {key: deepcopy(value) for key, value in self._cache.items() if key[0] != "test_change"}

    def _retain_tuning(self, receipt: dict) -> None:
        if self.tunings and self.tunings[0]["tuningId"] != receipt["tuningId"]:
            raise AssistToolError("tune_limit")
        if not self.tunings:
            self.tunings.append(deepcopy(receipt))

    def _tuning(self, tuning_id) -> dict:
        if not isinstance(tuning_id, str):
            raise AssistToolError("unknown_tuning")
        for (name, _key), result in self._cache.items():
            if name == "tune_member" and result.get("tuningId") == tuning_id \
                    and result.get("analysisId") == self.analysis["id"]:
                return result
        raise AssistToolError("unknown_tuning")

    def tune_member(self, args: dict) -> dict:
        if self.tunings:
            raise AssistToolError("tune_limit")
        # Validate shape/locks before touching the potentially large opponent catalog.
        assist_tuning.validate_request(self.report, args)
        slot, targets, benchmarks = assist_tuning.prepare(self.report, self._catalog(), args)
        result = assist_tuning.calculate(self.pool, self.team, benchmarks, self.deadline,
            allow_nature="nature" in self.analysis["allowedChanges"])
        receipt = {"analysisId": self.analysis["id"], "memberSlot": slot, "targets": targets,
                   "benchmarks": benchmarks, "result": result}
        nonce = secrets.token_hex(12)
        signature = hmac.new(self._secret, f"{nonce}:{_digest(receipt)}".encode(), hashlib.sha256).hexdigest()
        receipt["tuningId"] = f"t1.{nonce}.{signature}"
        self._retain_tuning(receipt)
        return assist_tuning.for_model(receipt)

    def read_summaries(self) -> list[dict]:
        """Facts actually read, without an unrelated matrix or unqualified KO prose."""
        readings = []
        for (name, _key), result in self._cache.items():
            if name == "team_evidence":
                witnesses = []
                for witness in result.get("witnesses") or []:
                    witnesses.append({"variantId": witness["variantId"],
                        "opponentBuild": witness.get("opponentBuild"),
                        "cells": [{"ref": cell["ref"], "member": cell["member"],
                                   "check": cell.get("check"), "speed": cell.get("speed"),
                                   "damageCaveats": {direction: (cell.get(direction) or {}).get("koCaveats") or []
                                                     for direction in ("offense", "incoming")}}
                                  for cell in witness.get("cells") or []]})
                readings.append({"kind": "read_evidence", "teamFloor": result.get("teamFloor"),
                                 "witnesses": witnesses, "mechanics": result.get("mechanics"),
                                 "totalWitnesses": result.get("totalWitnesses")})
            elif name == "selection_options":
                readings.append({"kind": "selection_enumeration", "facts": {
                    key: result[key] for key in ("format", "pick_size", "registered_mega_options", "mega_routes", "notes")
                    if key in result}, "rankingProduced": False})
            elif name == "tune_member":
                readings.append({"kind": "tuning_thresholds", **assist_tuning.for_model(result)})
            elif name == "opponent_builds":
                readings.append({"kind": "opponent_builds", "opponent": result["opponent"],
                                 "teamFloor": result["teamFloor"],
                                 "builds": [{"teamGrade": b["teamGrade"], **{k: (b["build"] or {}).get(k) for k in
                                             ("runForm", "item", "ability", "nature", "moves", "coverage")}}
                                            for b in result["builds"]]})
            elif name == "member_calc":
                readings.append({"kind": "member_calculation", **{k: result[k] for k in
                                 ("mode", "member", "opponent", "move", "damage", "conditions", "faster", "notes")
                                 if k in result}})
            elif name == "replacement_candidates":
                readings.append({"kind": "replacement_candidates", "replaces": result["replaces"],
                                 "need": result["need"], "candidates": [
                                     {k: c[k] for k in ("species", "usageRank", "set", "structure") if k in c}
                                     for c in result["candidates"]], "rankingProduced": False})
        # Keep the closing model context bounded while the host retains the whole cache.
        # Cache hits update recency, so a follow-up can explicitly reread an older fact.
        return readings[-4:]
