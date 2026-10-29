"""Calculator/dex adapters for the shared web inference model; no Python inference algorithm."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from inference_case import CaseInputError
from ncp_engine import Engine, EngineUnavailable, INFERENCE_CORE

SCRIPTS = Path(__file__).resolve().parent
DEX = SCRIPTS.parents[1] / "pokemon-champions-dex" / "scripts" / "champdex.py"
STATS = ("hp", "atk", "def", "spa", "spd", "spe")
SHORT = dict(zip(STATS, ("hp", "at", "df", "sa", "sd", "sp")))


def missing_configuration(raw: dict) -> list[str]:
    return [k for k in ("item", "ability", "nature", "sps", "moves")
            if k not in raw or raw[k] is None or k in ("ability", "nature", "moves") and not raw[k]]


def guidance(status: dict) -> dict:
    if status.get("impossible"):
        code = "check_reading"
        messages = ("核对无效读数及相邻可行值，删除错误记录后重新录入。",
                    "Check invalid readings against their neighboring feasible values; remove and record corrections.",
                    "無効な読み取り値と近くの実現可能値を確認し、誤った記録を削除して再入力してください。")
    elif status.get("state") == "conflict":
        code = "resolve_conflict"
        messages = ("各条观测单独可行，但合起来没有解；核对场况和冻结配置，逐条删除以定位冲突。",
                    "Individually possible hits conflict jointly; check frozen states and remove records to locate the conflict.",
                    "各記録は単独で可能ですが同時には成立しません。保存された状態を確認し、記録を削除して矛盾を調べてください。")
    elif status.get("unsupported") or status.get("state") == "pending":
        code = "review_unavailable_evidence"
        messages = ("仍有未纳入的观测或未完成的计算；先核对原因，当前范围只解释已接受的记录。",
                    "Review omitted observations or pending calculations; current ranges explain accepted records only.",
                    "未対応の観測や計算待ちがあります。理由を確認してください。現在の範囲は採用された記録のみを説明します。")
    elif status.get("count") == 1:
        code = "single_model_state"
        messages = ("当前候选范围只剩一个伤害状态，可查看示例；速度投入及范围外配置仍未确认，是否结束由目标决定。",
                    "One damage state remains in this candidate scope. Inspect the example; Speed investment and out-of-scope builds remain unconfirmed.",
                    "現在の候補範囲ではダメージ状態が一つです。構成例を確認できますが、素早さ配分と範囲外の型は未確定です。")
    elif "predictions" not in status:
        code = "compare_observations"
        messages = ("可用 status --predict 比较当前成员招式的读数分布和预期信息量；按目标决定继续观察或结束。",
                    "Use status --predict to compare readouts and expected information for this member's moves, then decide whether more evidence is needed.",
                    "status --predict で現在の技の読み取り分布と期待情報量を比較し、追加観測が必要か判断してください。")
    elif not status["predictions"] or any(p["observation"]["state"] != "ready" for p in status["predictions"]):
        code = "observation_information_unavailable"
        messages = ("当前招式的信息量尚不能完整计算；检查略过原因，或选择另一成员和场况。不能据此判定已收敛。",
                    "Information is not available for every current move. Check omitted reasons or another member/frame; this does not establish convergence.",
                    "現在の技の情報量をすべて計算できません。理由を確認するか別の味方・場況を選んでください。収束したとは限りません。")
    elif all(p["observation"]["informationGainBits"] <= 1e-9 for p in status["predictions"]):
        code = "no_distinguishing_observation"
        messages = ("当前已支持招式在这份场况下无法进一步区分候选；可换成员或场况，或保留不确定性后结束，不表示实战配置已确认。",
                    "Supported moves in this frame do not distinguish the remaining states. Try another member/frame or finish with uncertainty; the true build is not confirmed.",
                    "この場況で対応済みの技では候補を区別できません。味方や場況を変えるか、不確実性を残して終了できます。実際の型は未確定です。")
    else:
        code = "compare_observations"
        messages = ("可按读数分布与 informationGainBits 比较哪些命中更能区分候选；它不衡量招式强弱、命中率或对战风险，选择由宿主决定。",
                    "Compare readout distributions and informationGainBits to distinguish candidates; these do not measure move strength, accuracy or battle risk. The host chooses.",
                    "読み取り分布と informationGainBits で候補の区別しやすさを比較できます。技の強さ・命中率・対戦リスクは評価せず、選択はホストが行います。")
    return {"code": code, "messages": dict(zip(("zh", "en", "ja"), messages)),
            "options": [{"id": 1, "command": "add"}, {"id": 2, "command": "remove"},
                        {"id": 3, "command": "example"}, {"id": 4, "command": "status --predict --mine INDEX"}]}


class InferenceRuntime:
    def __init__(self, case: dict):
        self.case = deepcopy(case)
        try:
            self.engine = Engine(with_speed=True, with_inference=True)
        except EngineUnavailable as exc:
            raise CaseInputError("runtime", "Install quickjs-ng to run inference cases", "engine_unavailable") from exc
        self.facts: dict[str, dict] = {}
        self.calc_cache: dict[str, dict] = {}
        names = {case["foe"]["name"], *(m["name"] for m in case["mine_team"]),
                 *(h["mine_snapshot"]["name"] for h in case["observations"])}
        self.pokemon = self.lookup("pokemon", sorted(names))
        self.foe = self.pokemon[case["foe"]["name"]]
        engine_stats = json.loads(self.engine.ctx.eval(
            "JSON.stringify(__CTX.pokedex[" + json.dumps(self.foe["name"]) + "]?.bs ?? null)"))
        if not engine_stats or any(engine_stats.get(SHORT[s]) != self.foe["stats"][s] for s in STATS):
            raise CaseInputError("foe", "dex and calculator battle-form stats disagree", "authority_mismatch")
        self.space = self.build_space()

    def lookup(self, kind: str, names: list[str]) -> dict:
        names = list(dict.fromkeys(n for n in names if n))
        missing = [n for n in names if f"{kind}:{n}" not in self.facts]
        if missing:
            try:
                proc = subprocess.run([sys.executable, str(DEX), "batch", kind, *missing,
                                       "--strict", "--format", "json"], capture_output=True,
                                      encoding="utf-8", timeout=30)
                rows = json.loads(proc.stdout)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                raise CaseInputError(kind, "sibling dex is unavailable", "engine_unavailable") from exc
            if not isinstance(rows, list) or len(rows) != len(missing):
                raise CaseInputError(kind, "dex batch returned incomplete facts", "engine_unavailable")
            for name, row in zip(missing, rows):
                if row.get("error") or row.get("name") != name:
                    raise CaseInputError(kind, f"expected a canonical {kind} name: {name}", "not_found")
                self.facts[f"{kind}:{name}"] = row
        return {n: self.facts[f"{kind}:{n}"] for n in names}

    def build_space(self) -> dict:
        case, foe = self.case, self.foe
        candidates, known = case["candidates"], case["known"]
        panels = case["prior"]["panels"]
        rows = {}
        for axis in ("natures", "items", "abilities"):
            rows[axis] = ([{"name": n, "prior": 1 / len(candidates[axis])} for n in candidates[axis]]
                          if axis in candidates else
                          [{"name": r["name"], "prior": r["percentage"] / 100} for r in panels[axis]])
        if not rows["natures"]:
            # Use the calculator's nature vocabulary, with dex as the multiplier authority.
            names = json.loads(self.engine.ctx.eval("JSON.stringify(Object.keys(__CTX.NATURES))"))
            rows["natures"] = [{"name": n, "prior": 1 / len(names)} for n in names]
        natures = self.lookup("nature", [r["name"] for r in rows["natures"]])
        self.natures = natures
        for row in rows["natures"]:
            fact = natures[row["name"]]
            row["mult"] = {s: 1.1 if fact.get("up_stat") == s else 0.9 if fact.get("down_stat") == s else 1
                           for s in ("atk", "def", "spa", "spd")}
        if foe["is_mega"]:
            fixed = {"item": foe["required_item"], "ability": foe["abilities"][0]}
            for field, axis in (("item", "items"), ("ability", "abilities")):
                if field in known and known[field] != fixed[field] or axis in candidates and candidates[axis] != [fixed[field]]:
                    raise CaseInputError(field, "confirmed/candidate value conflicts with the fixed Mega form")
                rows[axis] = [{"name": fixed[field], "prior": 1}]
        else:
            for field, axis in (("item", "items"), ("ability", "abilities")):
                if field in known:
                    rows[axis] = [{"name": known[field], "prior": 1}]
            items = self.lookup("item", [r["name"] for r in rows["items"]])
            stones = [r for r in rows["items"] if items[r["name"]].get("required_by")]
            if stones and ("items" in candidates or "item" in known):
                raise CaseInputError("items", "a confirmed Mega stone needs its explicit battle form")
            rows["items"] = [r for r in rows["items"] if not items[r["name"]].get("required_by")]
            invalid = [r for r in rows["abilities"] if r["name"] not in foe["abilities"]]
            if invalid and ("abilities" in candidates or "ability" in known):
                raise CaseInputError("abilities", "ability is not legal for this battle form")
            rows["abilities"] = [r for r in rows["abilities"] if r["name"] in foe["abilities"]]
            if not rows["items"]:
                rows["items"] = [{"name": "", "prior": 1}]
            if not rows["abilities"]:
                rows["abilities"] = [{"name": n, "prior": 1 / len(foe["abilities"])} for n in foe["abilities"]]
        return {"base": {s: foe["stats"][s] for s in STATS if s != "spe"}, **rows}

    def model(self, request: dict) -> dict:
        return self.engine.run_inference(json.dumps(request))

    def member(self, raw: dict) -> dict:
        fact = self.pokemon[raw["name"]]
        missing = missing_configuration(raw)
        if missing:
            raise CaseInputError("mine_team", "known configuration is missing: " + ", ".join(missing))
        if raw["ability"] not in fact["abilities"]:
            raise CaseInputError("mine_team.ability", "ability is not legal for the recorded battle form")
        self.lookup("nature", [raw["nature"]])
        items = self.lookup("item", [raw["item"]])
        if fact["is_mega"] and raw["item"] != fact["required_item"]:
            raise CaseInputError("mine_team.item", "Mega form must hold its required stone")
        if raw["item"] and items[raw["item"]].get("required_by") and not fact["is_mega"]:
            raise CaseInputError("mine_team.name", "use the explicit battle form when holding a Mega stone")
        self.lookup("move", raw["moves"])
        if any(move not in fact["moves"] for move in raw["moves"]):
            raise CaseInputError("mine_team.moves", "recorded member cannot learn a supplied move")
        return deepcopy(raw)

    def hp(self, member: dict) -> int:
        return json.loads(self.engine.ctx.eval(
            "JSON.stringify(buildPokemon(" + json.dumps(member) + ", __CTX).maxHP)"))

    def survives(self, attacker: dict, defender: dict, move: str) -> bool:
        return self.model({"op": "survival", "attackerAbility": attacker.get("ability", ""),
                           "attackerAbilityOn": attacker.get("abilityOn", True),
                           "defenderItem": defender.get("item", ""), "defenderAbility": defender.get("ability", ""),
                           "defenderAbilityOn": defender.get("abilityOn", True), "move": move})["stopAtOne"]

    def hit(self, record: dict) -> tuple[dict | None, str | None]:
        if record["foe_snapshot"]["name"] != self.foe["name"]:
            return None, "recorded opponent battle form differs from the case"
        mine = self.member(record["mine_snapshot"])
        if record["kind"] == "offense" and record["before"] > self.hp(mine):
            raise CaseInputError("observation.before", "our HP reading exceeds the frozen member's maximum HP")
        move = record["move"]
        move_fact = self.lookup("move", [move])[move]
        source = mine["name"] if record["kind"] == "bulk" else self.foe["name"]
        if move not in self.pokemon[source]["moves"]:
            raise CaseInputError("observation.move", "the attacker cannot learn this move")
        use = self.model({"op": "classify", "kind": record["kind"], "move": move, "category": move_fact["category"]})
        if "key" not in use:
            return None, use["reason"]
        key = use["key"]
        frame_issue = self.model({"op": "frame-support", "frame": {
            "kind": record["kind"],
            "foeAbilities": [a["name"] for a in self.space["abilities"]],
            "foeBaseHP": self.foe["stats"]["hp"], "foeCurrentHP": record["foe_snapshot"].get("curHP")}})["reason"]
        if frame_issue:
            return None, frame_issue
        hit = {"id": record["id"], "kind": record["kind"], "key": key,
               "before": record["before"], "after": record["after"], "rolls": {}, "stopAtOne": {}}
        mults = sorted({n["mult"][key] for n in self.space["natures"]})
        for ability in self.space["abilities"]:
            for item in self.space["items"]:
                foe = {**record["foe_snapshot"], "ability": ability["name"], "item": item["name"]}
                attacker, defender = (mine, foe) if record["kind"] == "bulk" else (foe, mine)
                hit["stopAtOne"][json.dumps([item["name"], ability["name"]], separators=(",", ":"))] = (
                    self.survives(attacker, defender, move) and
                    (record["kind"] == "bulk" or record["before"] == self.hp(mine)))
                for mult in mults:
                    nature = next(n["name"] for n in self.space["natures"] if n["mult"][key] == mult)
                    for sp in range(33):
                        target = {**foe, "nature": nature, "sps": {key: sp}}
                        known = deepcopy(mine)
                        if record["kind"] == "bulk":
                            target.pop("curHP", None)  # The recorded percent, not an editable build's HP, owns this hit.
                            if record["before"] < 100:
                                target["curHP"] = max(1, self.hp(target) - 1)
                        if record["kind"] == "offense":
                            known["curHP"] = record["before"]
                        attacker, defender = (known, target) if record["kind"] == "bulk" else (target, known)
                        move_input = {"name": move, "isCrit": record.get("crit", False)}
                        if record.get("single_target"):
                            move_input["isSpread"] = False
                        attacker = {**attacker, "moves": [move_input]}
                        field = record["field"]
                        attack_side, defend_side = (("mineSide", "foeSide") if record["kind"] == "bulk"
                                                    else ("foeSide", "mineSide"))
                        request = {"attacker": attacker, "defender": defender, "move": move, "switch_in_drops": False,
                                   "field": {"format": field.get("format", self.case["format"]), "weather": field.get("weather", ""),
                                             "terrain": field.get("terrain", ""),
                                             "gravity": field.get("gravity", False), "foresight": field.get("foresight", False),
                                             "attackerSide": field.get(attack_side, {}), "defenderSide": field.get(defend_side, {})}}
                        cache_key = json.dumps(request, sort_keys=True)
                        if cache_key not in self.calc_cache:
                            self.calc_cache[cache_key] = self.engine.run_calc("one", cache_key)["output"]
                        calc = self.calc_cache[cache_key]
                        if "error" in calc:
                            return None, "calculator refused the recorded frame: " + calc["error"]["message"]
                        if calc.get("hits", 1) != 1 or calc.get("hits_range", [1, 1]) != [1, 1]:
                            return None, "multi-hit observations are not supported"
                        parts = ([ability["name"], mult, sp, item["name"]] if record["kind"] == "bulk"
                                 else [item["name"], ability["name"], mult, sp])
                        hit["rolls"][json.dumps(parts, separators=(",", ":"))] = calc["damage"]
        return hit, None

    def speed(self, status: dict, mine: dict, foe_state: dict, field: dict) -> dict:
        probes, readings = [], []
        def add(mon: dict, side: str) -> int:
            # Case spreads are explicit: omitted stats mean zero, not speed CLI's default 32.
            value = self.engine.run_speed("one", json.dumps({**mon, "sps": {"spe": 0, **mon.get("sps", {})}, "field": {
                "weather": field.get("weather", ""), "terrain": field.get("terrain", ""),
                "side": field.get(side, {})}}))["output"]
            readings.append(value.get("finalSpeed") if "error" not in value else None)
            return len(readings) - 1
        mine_index = add(mine, "mineSide")
        I, A = len(self.space["items"]), len(self.space["abilities"])
        for n, nature in enumerate(self.space["natures"]):
            for i, item in enumerate(self.space["items"]):
                for a, ability in enumerate(self.space["abilities"]):
                    cap = (status.get("speedCaps") or [])[((n * I + i) * A + a)]
                    if cap < 0:
                        continue
                    base = {**foe_state, "name": self.foe["name"], "nature": nature["name"],
                            "ability": ability["name"], "item": item["name"]}
                    lo = add({**base, "sps": {"spe": 0}}, "foeSide")
                    hi = add({**base, "sps": {"spe": cap}}, "foeSide")
                    probes.append({"nature": nature["name"], "lo": lo, "hi": hi, "spHi": cap})
        return self.model({"op": "speed", "plan": {"mine": mine_index, "probes": probes}, "speeds": readings})

    def evaluate(self, *, predict: bool = False, mine: int = 0) -> dict:
        if not 0 <= mine < len(self.case["mine_team"]):
            raise CaseInputError("mine", "member index is out of range")
        current = self.case["mine_team"][mine]
        missing = missing_configuration(current)
        selected = deepcopy(current) if missing else self.member(current)
        hits, unsupported, disabled = [], [], []
        for record in self.case["observations"]:
            if not record.get("enabled", True):
                disabled.append(record["id"])
                continue
            hit, reason = self.hit(record)
            if reason:
                unsupported.append({"id": record["id"], "reason": reason})
            else:
                hits.append(hit)
        predictions, omitted = [], []
        last = None if missing else next((h for h in reversed(self.case["observations"])
                     if h.get("enabled", True) and h["mine"] == mine
                     and h["mine_snapshot"]["name"] == selected["name"]
                     and h["foe_snapshot"]["name"] == self.foe["name"]), None)
        field, foe_state = (last["field"], last["foe_state"]) if last else ({}, {})
        if last:
            selected = self.member(last["mine_snapshot"])
        if predict and missing:
            omitted.append({"reason": "incomplete_current_member", "missing": missing})
        if predict and not missing:
            for move in selected["moves"]:
                record = {"id": move, "kind": "bulk", "move": move, "before": 100, "after": 100,
                          "mine_snapshot": selected, "foe_snapshot": {**self.case["foe"], **foe_state}, "field": field}
                hit, reason = self.hit(record)
                if reason:
                    omitted.append({"move": move, "reason": reason})
                else:
                    predictions.append({"id": move, "hit": hit, "ourHP": self.hp(selected)})
        status = self.model({"op": "evaluate", "space": self.space, "hits": hits,
                             **({"predictions": predictions} if predict else {})})
        status.update(schema="ncp_inference_status.v1", space=self.space, unsupported=unsupported, disabled=disabled,
                      case_sha256=hashlib.sha256(json.dumps(self.case, sort_keys=True).encode()).hexdigest(),
                      model_sha256=hashlib.sha256(INFERENCE_CORE.read_bytes()).hexdigest(),
                      season=self.case["season"], rule=self.case["rule"], format=self.case["format"],
                      predictions_omitted=omitted,
                      projection_frame={"basis": "unavailable" if missing else
                                                 "last_matching_observation" if last else "current_member_neutral_field",
                                        **({"reason": "incomplete_current_member", "missing": missing} if missing else {}),
                                        "observation_id": last["id"] if last else None,
                                        "mine": selected, "foe_state": foe_state, "field": field},
                      assumptions=["Candidate percentages are priors, not confirmation of the true set.",
                                   "SP ranges use the shared 0..32 per-stat and 66 total budget model.",
                                   "Speed ranges are bounds, not every integer within them is attainable.",
                                   "Prediction damage ranges use surviving states; possibleAfters uses the initial candidate range.",
                                   "Observation probabilities are conditional on a landed hit against a full-HP foe in projection_frame, with no intervening effects.",
                                   "Information uses the model's five damage-axis joint weights, not calibrated battle probabilities; unobserved HP intervals have feasibility weights only."])
        status["speed"] = (self.speed(status, selected, foe_state, field) if status["state"] == "ready" and not missing
                           else {"mine": None, "ranges": [], "incomplete": True})
        status["next_step"] = guidance(status)
        return status
