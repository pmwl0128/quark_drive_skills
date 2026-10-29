"""Reference-bound inputs and disclosure-safe projections for native tune cards."""
from __future__ import annotations

from copy import deepcopy

from . import mappers
from .online.builder import _op_result, _team_session, strip_provenance
from .team_evidence import read_evidence

MAX_TARGETS = 3  # Initial resource envelope; recalibrate with the complete workflows.
KINDS = ("outspeed", "survive", "ohko", "2hko")
PROBABILITIES = ("guaranteed", "near_guaranteed", "likely", "three_quarters", "half", "any")
RESULTS = ("already", "cliff", "infeasible", "unreachable", "n/a", "skipped")
STATS = ("hp", "atk", "def", "spa", "spd", "spe")
WEATHER = {"rain": "rain", "sun": "sun", "sand": "sand", "sandstorm": "sand", "snow": "snow", "hail": "snow",
           "harsh sun": "harsh sun", "heavy rain": "heavy rain", "strong winds": "strong winds"}
TERRAINS = ("electric", "grassy", "psychic", "misty")


class TuningInputError(ValueError):
    pass


def _conditions(kind: str, supplied, fmt: str) -> dict:
    speed = kind == "outspeed"
    defaults = ({"tailwind": False, "opponent_tailwind": False, "trickroom": False} if speed else
                {"stealth_rock": False, "spikes": 0, "screens": False})
    if not isinstance(supplied, dict) or set(supplied) - (set(defaults) | {"weather", "terrain"}):
        raise TuningInputError("bad_tool_input")
    normalized = dict(supplied)
    for key, value in supplied.items():
        if key in ("weather", "terrain"):
            if not isinstance(value, str) or not 1 <= len(value) <= 30:
                raise TuningInputError("bad_tool_input")
            token = value.strip().lower().replace("_", " ").replace("-", " ")
            if key == "terrain":
                token = token.removesuffix(" terrain").strip()
            canonical = WEATHER.get(token) if key == "weather" else token if token in TERRAINS else None
            if canonical is None:
                raise TuningInputError("unsupported_tuning_condition")
            normalized[key] = canonical
        elif key == "spikes":
            if type(value) is not int or not 0 <= value <= 3:
                raise TuningInputError("bad_tool_input")
        elif key == "screens":
            if type(value) is not bool and value not in ("reflect", "light_screen", "aurora_veil"):
                raise TuningInputError("bad_tool_input")
        elif type(value) is not bool:
            raise TuningInputError("bad_tool_input")
    if normalized.get("trickroom") or (fmt == "single" and normalized.get("opponent_tailwind")) \
            or (fmt == "double" and normalized.get("stealth_rock")):
        # Native TR targets a conservative floor rather than this exact joint
        # set; the other two fields are silently ignored in those formats.
        raise TuningInputError("unsupported_tuning_condition")
    return {**defaults, **normalized}


def validate_request(report: dict, args: dict) -> tuple[int, list]:
    if set(args) != {"slot", "targets"} or type(args["slot"]) is not int \
            or not 0 <= args["slot"] < len(report["team"]["pokemon"]):
        raise TuningInputError("bad_tool_input")
    slot, targets = args["slot"], args["targets"]
    if not isinstance(targets, list) or not 1 <= len(targets) <= MAX_TARGETS:
        raise TuningInputError("bad_tool_input")
    if slot in report["analysis"]["lockedSlots"]:
        raise TuningInputError("locked_member")
    if "spread" not in report["analysis"]["allowedChanges"]:
        raise TuningInputError("change_not_allowed")
    if report["legality"]["status"] != "valid":
        raise TuningInputError("team_not_valid")
    return slot, targets


def prepare(report: dict, catalog: dict, args: dict) -> tuple[int, list, list]:
    slot, targets = validate_request(report, args)
    member = report["team"]["pokemon"][slot]
    normalized, benchmarks = [], []
    for target in targets:
        if not isinstance(target, dict) or not {"ref", "kind"} <= set(target) \
                or set(target) - {"ref", "kind", "probability", "hits", "conditions"}:
            raise TuningInputError("bad_tool_input")
        kind, ref = target["kind"], target["ref"]
        if kind not in KINDS or not isinstance(ref, dict) or ref.get("scope") != "member_cell" \
                or ref.get("memberSlot") != slot or type(ref.get("memberSlot")) is not int:
            raise TuningInputError("bad_evidence_ref")
        try:
            cell = read_evidence(report, ref)
        except ValueError:
            raise TuningInputError("bad_evidence_ref") from None
        build = catalog.get(mappers.opponent_key(ref["variantId"]))
        if not isinstance(build, dict):
            raise TuningInputError("catalog_unavailable")
        spread = build.get("sps")
        if not isinstance(spread, dict) or not spread or set(spread)-set(STATS) \
                or any(type(n) is not int or not 0 <= n <= 32 for n in spread.values()) \
                or sum(spread.values()) > 66 or any(not isinstance(build.get(k), str) or not build[k]
                                                  for k in ("species", "ability", "nature")) \
                or "item" not in build or (build["item"] is not None and not isinstance(build["item"], str)):
            raise TuningInputError("opponent_set_incomplete")
        species = build.get("runForm") or build["species"]
        conditions = _conditions(kind, target.get("conditions", {}), report["team"]["format"])
        benchmark = {"member": member["species"], "kind": kind, "vs": species, "conditions": conditions,
                     "opponent_set": {"ability": build["ability"], "item": build["item"],
                                      "nature": build["nature"], "spread": deepcopy(spread)}}
        damage = None
        if kind != "outspeed":
            damage = cell.get("incoming" if kind == "survive" else "offense") or {}
            move = damage.get("move")
            moves = build.get("moves") if kind == "survive" else member.get("moves")
            if not isinstance(move, str) or move not in (moves or []):
                raise TuningInputError("benchmark_unavailable")
            benchmark["move"] = move
            probability = target.get("probability", "guaranteed")
            if probability not in PROBABILITIES:
                raise TuningInputError("bad_tool_input")
            benchmark["probability"] = probability
        elif "probability" in target:
            raise TuningInputError("bad_tool_input")
        if "hits" in target:
            if kind != "survive" or type(target["hits"]) is not int or target["hits"] not in (1, 2):
                raise TuningInputError("bad_tool_input")
            benchmark["hits"] = target["hits"]
        if benchmark in benchmarks:
            raise TuningInputError("bad_tool_input")
        benchmarks.append(benchmark)
        normalized.append({"ref": deepcopy(ref), "kind": kind, "conditions": conditions,
                           "opponentBuild": {"species": species, **deepcopy(benchmark["opponent_set"]),
                                             "moves": deepcopy(build.get("moves") or [])},
                           **{k: benchmark[k] for k in ("move", "probability", "hits") if k in benchmark},
                           "sourceDamageCaveats": deepcopy((damage or {}).get("koCaveats") or []),
                           "scope": "speed_threshold" if kind == "outspeed" else "raw_damage_threshold"})
    return slot, normalized, benchmarks


def calculate(pool, team, benchmarks, deadline, *, allow_nature: bool) -> dict:
    context = {"benchmarks": benchmarks,
               "locked": [] if allow_nature else [b["member"] for b in benchmarks]}
    raw = _op_result(_team_session(pool, [{"op": "tune", "file": team, "context": context}], deadline)[0])
    cards = raw.get("cards")
    if "error" in raw or raw.get("kind") != "tune" or raw.get("format") != team["format"] \
            or not isinstance(cards, list) or len(cards) != len(benchmarks):
        raise TuningInputError("calculation_unavailable")
    for i, (card, benchmark) in enumerate(zip(cards, benchmarks)):
        if not isinstance(card, dict) or type(card.get("benchmark_index")) is not int or card["benchmark_index"] != i \
                or card.get("kind") != benchmark["kind"] or card.get("vs") != benchmark["vs"] \
                or card.get("member") != benchmark["member"] \
                or ("move" in benchmark and card.get("move") != benchmark["move"]) \
                or card.get("result") not in RESULTS or not isinstance(card.get("note"), str):
            raise TuningInputError("calculation_unavailable")
    public = strip_provenance(raw)
    # The public artifact scrubber removes free-form `note` fields. Keep the
    # native DTO's required text slots empty; this surface renders scoped facts,
    # never the native prose's unqualified KO statements or source labels.
    for card in public["cards"]:
        card["note"] = ""
        if card.get("reallocation"):
            card["reallocation"]["note"] = ""
    return public


def thresholds(result: dict) -> list[dict]:
    """Never feed raw KO prose, ko_exact or unqualified KO chances to the model."""
    keys = ("member", "vs", "kind", "stat", "probability", "need_total", "delta_sp", "slack_sp",
            "allocation", "speed_basis", "target_speed", "confidence")
    output = []
    for card in result["cards"]:
        row = {"benchmarkIndex": card["benchmark_index"], "thresholdStatus": card["result"],
               **{key: deepcopy(card[key]) for key in keys if key in card},
               "scope": "speed_threshold" if card["kind"] == "outspeed" else "raw_damage_threshold"}
        if card["result"] not in ("already", "cliff", "infeasible"):
            # Native unreachable lanes use sentinel deltas such as 33; those
            # are not an actionable SP requirement.
            for key in ("need_total", "delta_sp", "slack_sp", "allocation"):
                row.pop(key, None)
        if card.get("reallocation"):
            envelope = card["reallocation"]
            row["reallocation"] = {key: deepcopy(envelope[key]) for key in
                ("required_sp", "available_sp", "unused_sp", "candidate_donors") if key in envelope}
            row["reallocation"]["protectionScope"] = "only_targets_in_this_tune_call"
            row["reallocation"]["otherOriginalBenchmarksPreserved"] = "unproven"
        if card.get("nature_alternatives"):
            row["natureAlternatives"] = [{key: deepcopy(lane[key]) for key in
                ("nature", "result", "stat", "need_total", "delta_sp", "allocation", "plus_stat", "penalty_stat", "saves_sp", "unlock")
                if key in lane} for lane in card["nature_alternatives"]]
        output.append(row)
    return output


def for_model(receipt: dict) -> dict:
    return {"tuningId": receipt["tuningId"], "analysisId": receipt["analysisId"],
            "memberSlot": receipt["memberSlot"], "targets": deepcopy(receipt["targets"]),
            "thresholds": thresholds(receipt["result"]),
            "applied": False, "limits": ["no_auto_reallocation", "no_team_gain_or_battle_outcome_proof",
                                         "no_move_priority_or_action_order_proof"]}
