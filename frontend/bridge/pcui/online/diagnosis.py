"""Bounded diagnosis reasoning; verification cards come only from the host tools."""
from __future__ import annotations

import json
import re
import time

from ..team_assist import TeamAssist, MAX_TOOL_CALLS, CALC_MODES, CALC_TAILWIND, NEED_TYPE_KEYS, roles
from ..team_evidence import read_evidence
from . import calc_scenario, prose_gate
from .prose_gate import LANGUAGE_NAMES

MAX_ROUNDS = 3
PLAIN_MAX_TOKENS = 2200
MAX_INPUT_BYTES = 120_000
# Receipt and analysis IDs belong in the JSON fields, never in the prose a visitor reads.
_RECEIPT_ID = re.compile(r"(?<![A-Za-z0-9])(?:[vt]1\.[0-9a-f]{24}\.[0-9a-f]{64}|[0-9a-f]{64})(?![A-Za-z0-9])")
_EMPTY_RECEIPT = re.compile(r"[（(]\s*(?:复验回执|调校回执|検証の記録|調整の記録|the test receipt|the tuning receipt)?"
                            r"\s*[:：]?\s*[）)]")


def _strip_receipts(text: str) -> str:
    return re.sub(r"[ 	]{2,}", " ", _EMPTY_RECEIPT.sub("", _RECEIPT_ID.sub("", text))).strip()


def _tool(name, description, properties, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": list(required), "additionalProperties": False}}}


_REF = {"type": "object", "properties": {
    "scope": {"type": "string", "enum": ["team_floor"]},
    "analysisId": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
    "opponent": {"type": "string", "minLength": 1, "maxLength": 100}},
    "required": ["scope", "analysisId", "opponent"], "additionalProperties": False}
_CELL_REF = {**_REF, "properties": {**_REF["properties"],
    "scope": {"type": "string", "enum": ["member_cell"]},
    "variantId": {"type": "string", "minLength": 1, "maxLength": 500},
    "memberSlot": {"type": "integer", "minimum": 0, "maximum": 5}},
    "required": ["scope", "analysisId", "opponent", "variantId", "memberSlot"]}

_OPPONENT_SET = {"type": "object", "properties": {
    "nature": {"type": "string"},
    "sps": {"type": "object", "description": "SP per stat hp/atk/def/spa/spd/spe, 0-32; replaces the whole spread"},
    "item": {"type": "string", "description": "item, or none"},
    "ability": {"type": "string"},
    "boosts": {"type": "object", "description": "stat stages atk/def/spa/spd/spe, -6..6"},
    "status": {"type": "string", "enum": list(calc_scenario.STATUSES)}}, "additionalProperties": False}
_CALC_FIELD = {"type": "object", "properties": {
    "weather": {"type": "string", "enum": list(calc_scenario.WEATHERS)},
    "terrain": {"type": "string", "enum": list(calc_scenario.TERRAINS)},
    **{key: {"type": "boolean"} for key in ("reflect", "light_screen", "aurora_veil", "helping_hand")}},
    "additionalProperties": False}
_TYPES = {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "string"}}

TOOLS = [
    _tool("team_evidence", "Read the current actual team's witness cells for one supplied team-floor reference. "
          "The team floor and member cells are different evidence levels. Damage caveats qualify all KO claims.",
          {"ref": _REF}, ["ref"]),
    _tool("opponent_builds", "Read EVERY retained observed build of one report opponent (team_evidence shows at "
          "most two witnesses), with the grade this team's best member has against each build.",
          {"ref": _REF}, ["ref"]),
    _tool("member_calc", "Calculate with one member's ACTUAL set against a named opponent, which uses its "
          "most-used set of the named form unless opponentSet states parts: attack = the member's move into the "
          "opponent, defend = the opponent's move into the member (one hit's damage and KO chance), speed = both "
          "final Speed values. Works for opponents outside the report's top-K. Static, no turns.",
          {"slot": {"type": "integer", "minimum": 0, "maximum": 5},
           "opponent": {"type": "string", "minLength": 1, "maxLength": 100},
           "mode": {"type": "string", "enum": list(CALC_MODES)},
           "move": {"type": "string", "maxLength": 100, "description": "attack: the member's move; defend: the "
                                                                       "opponent's move"},
           "opponentSet": _OPPONENT_SET, "field": _CALC_FIELD,
           "tailwind": {"type": "string", "enum": list(CALC_TAILWIND), "description": "speed mode only"}},
          ["slot", "opponent", "mode"]),
    _tool("member_options", "Read canonical dex legal moves and joint representative sets for one exact form. "
          "These are options, not the user's own set and not a recommended change.",
          {"pokemon": {"type": "string", "minLength": 1, "maxLength": 100}}, ["pokemon"]),
    _tool("replacement_candidates", "Candidates to REPLACE one unlocked member: usage-pool Pokemon that meet "
          "EVERY need criterion (resist / offense_type / coverage_move_type types, role, min_speed), avoiding items "
          "the other members hold, each with a full joint set to try with test_change. Options, not a ranking.",
          {"slot": {"type": "integer", "minimum": 0, "maximum": 5},
           "need": {"type": "object", "properties": {
               **{key: _TYPES for key in NEED_TYPE_KEYS},
               "role": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "string", "enum": roles()}},
               "min_speed": {"type": "integer", "minimum": 1, "maximum": 400,
                             "description": "reachable final Speed with a fastest build"}},
               "additionalProperties": False}}, ["slot", "need"]),
    _tool("selection_options", "Enumerate this registered team's legal selection sizes and one-active-Mega routes. "
          "No winner ranking, lead-sequence proof or battle win rates.", {}),
    _tool("tune_member", "Calculate native SP thresholds for ONE unlocked member against 1-3 exact observed "
          "member-cell refs. The referenced cell supplies the move for survive/ohko/2hko and the joint "
          "opponent set. For outspeed OMIT probability and hits. For damage probability is optional. The "
          "host permits no opponent overrides, donor selection or team edits. Defaults explicitly disable "
          "Trick Room; single opponent Tailwind and double Stealth Rock are unsupported. Other defaults disable "
          "hazards/Tailwind/screens; provide only conditions actually requested. Damage thresholds are raw "
          "damage, not proven KO/survival/turn outcomes. Returns a host tuningId. If proposing a complete "
          "SP/nature change, include that tuningId in test_change to recheck thresholds and team tradeoffs.",
          {"slot": {"type": "integer", "minimum": 0, "maximum": 5},
           "targets": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "object",
             "properties": {"ref": _CELL_REF, "kind": {"type": "string", "enum": ["outspeed", "survive", "ohko", "2hko"]},
               "probability": {"type": "string", "enum": ["guaranteed", "near_guaranteed", "likely", "three_quarters", "half", "any"]},
               "hits": {"type": "integer", "enum": [1, 2]},
               "conditions": {"type": "object", "properties": {
                   **{key: {"type": "boolean"} for key in ("stealth_rock", "tailwind", "opponent_tailwind")},
                   "trickroom": {"type": "boolean", "enum": [False]},
                   "spikes": {"type": "integer", "minimum": 0, "maximum": 3},
                   "weather": {"type": "string", "minLength": 1, "maxLength": 30},
                   "terrain": {"type": "string", "minLength": 1, "maxLength": 30},
                   "screens": {"type": ["boolean", "string"]}}, "additionalProperties": False}},
             "required": ["ref", "kind"], "additionalProperties": False}}}, ["slot", "targets"]),
    _tool("test_change", "Test ONE small proposal on an immutable COPY of this actual team. "
          "Respect analysis.allowedChanges and lockedSlots. Returns host-issued verificationId, legality, "
          "member-cell differences, TEAM floor gains and losses. One test per diagnosis. "
          "No library edit is applied. A legal proposal can be worse or leave team floors unchanged. "
          "spread: the listed stats replace the current values and omitted stats keep theirs (give 0 "
          "to clear one); the total must stay within 66 SP. A species change replaces the member: give its "
          "complete set, and its spread is taken as complete.",
          {"tuningId": {"type": "string", "pattern": "^t1\\.[a-f0-9]{24}\\.[a-f0-9]{64}$"},
           "changes": {"type": "array", "minItems": 1, "maxItems": 2, "items": {
              "type": "object", "properties": {"slot": {"type": "integer", "minimum": 0, "maximum": 5},
              "set": {"type": "object", "properties": {
                  "species": {"type": "string", "maxLength": 100},
                  "moves": {"type": "array", "minItems": 1, "maxItems": 4, "items": {"type": "string"}},
                  "item": {"type": ["string", "null"]}, "ability": {"type": "string"},
                  "nature": {"type": "string"}, "spread": {"type": "object", "properties": {
                      key: {"type": "integer", "minimum": 0, "maximum": 999}
                      for key in ("hp", "atk", "def", "spa", "spd", "spe")}, "additionalProperties": False}},
                  "additionalProperties": False}}, "required": ["slot", "set"], "additionalProperties": False}}}, ["changes"]),
]

SYSTEM = """DIAGNOSIS_TOOL_WORKFLOW
You explain a Pokemon Champions player's actual team using only supplied project facts.
team.format and team.pickSize are authoritative. Never mix singles and doubles. A C0 floor
is the weakest retained observed build, not a proof that every plan loses. Static member
pairs do not simulate turns, switching, partners, move accuracy, battle wins or full selection plans.
Read the host-prefetched evidence and use bounded tools for the user's goal. Any specific
configuration change you propose MUST be checked with test_change before you discuss it as
tested. Respect allowedChanges and lockedSlots. Call member_options when a new move or
member needs grounding. Do not force an edit if none is supported. New move/ability/item
mechanics and type-effectiveness claims must come from tools; do not invent them from game memory.
If an ability or move effect is not supplied, omit the causal theory. Read English AND
Japanese effect descriptions when supplied. Weather and terrain are distinct fields.

The server alone produces change cards and their gains/losses. Never manufacture a
verificationId or turn a member-cell gain into a TEAM floor gain. A legal edit is not
automatically better. Negative or mixed results are useful: explain the losses clearly.
No files, argv, external lookups or library writes are available.
For an SP threshold or reallocation question use tune_member, not arithmetic from memory.
Its thresholds are NOT an optimal spread and do not prove an actual KO or battle outcome.
Damage thresholds remain raw even if the native card calls the requested tier 'ohko'.
Donors expose capacity and opportunity cost, not an instruction to take SP from one stat.
Any protected capacity applies only to targets in that one tune call. It never proves
preservation of the original team's other benchmarks. For outspeed omit probability and hits.
A proposed SP/nature edit derived from tuning MUST pass test_change with its host tuningId:
the server rechecks those exact targets on the candidate as well as team gains and losses.
If there is no successful change test, explain only threshold facts; no edit was applied.
For a follow-up, currentQuestion is the new request about the SAME original team and
constraints. Read evidence may be reused. previousVerifiedChanges are past tests on that
original team's copies, not edits already adopted and not tests performed this turn.
Do not change locks or permissions from a follow-up question. A new proposal still needs
its own test. Prior prose is not evidence; use the supplied structured host history.
If a proposed test is rejected for argument shape or permissions, inspect its actual diff,
correct it within the stated permissions and retry while tool budget remains. Unchanged
fields may be omitted from set; supplying a full set never grants extra editing permissions.

goalTargets maps the Pokemon named in the goal or question to report rows (opponents) and
team slots (members); use those rows and slots, not a similarly named Pokemon.
opponent_builds lists every observed build of one report opponent. For a damage, KO or Speed
question about a specific member and opponent (including opponents outside the report), call
member_calc instead of estimating. When species changes are allowed and the goal needs a new
member, replacement_candidates lists Pokemon meeting a structured need, most with a complete set:
pass one such set unchanged to test_change (do not re-read it with member_options) before discussing it.
When you have finished with tools, reply with the single word DONE. The server writes the
user-facing explanation from the host facts in a separate step; do not draft it here.
"""

SUMMARY_SYSTEM = """DIAGNOSIS_TOOL_WORKFLOW — FINAL EVIDENCE SUMMARY
Write a short explanation of the supplied host facts in the requested language.
This is the final reporting stage; tools are closed. Do not introduce new recommendations,
new entities, type-matchup theories, move/ability mechanisms, leads or turn sequences.
Describe only what the verified comparison DID and DID NOT establish. A member-cell
gain is not a team-floor gain. If the card has team losses, say that adopting it has a cost;
if the targeted opponent's TEAM floor did not improve, state that explicitly. A positive
test is static evidence, never proof of battle strength. No tests means no change recommendation.
An absent tested change is NOT an absence of read evidence: readEvidence records the
actual witness facts and selection enumeration. Describe those facts without inventing turns.
readEvidence can also hold opponent_builds (each observed build of one opponent with this team's
grade against it), member_calculation (one exact pairing: one hit's damage and KO chance, or two
Speed values; give the KO chance in words, never percentages) and replacement_candidates (options
that met the stated need; untested unless a verified change tests one). A candidate's structure and a
tested card's structureChanges are the static team diff of that swap: shared weaknesses, coverage
gaps, the two speeds and roles lost or gained. They are facts of the swap, not a verdict; a team-floor
change still comes only from teamFloorChanges.
When comparisonPerformed is false, no before/after comparison occurred. Do not say that
team floors improved, stayed unchanged, or failed to improve, and do not discuss adopting
an edit. Describe the CURRENT observed configurations and state that no edit was tested.
Speed and survival facts apply to the supplied specific observed build, not every possible set.
Tuning readings establish speed or RAW DAMAGE thresholds under their declared conditions.
An outspeed threshold compares Speed values only. Move priority and action order are not
verified; never translate the Speed comparison into proof of moving or attacking first.
They do not establish a KO, survival sequence, optimal spread or team improvement. Read
allThresholdsMet only as a predicate for those raw thresholds. A met threshold and a
team-floor gain are distinct. An unfunded reallocation (a tuning envelope needing more SP than
is free) is not an adoptable spread; a TESTED card with valid legality is a complete, legal spread,
so never call it unfunded.
Tuning never proves preservation of the original team's other benchmarks. Do not describe
donor capacity as a safe change, or select a donor when no tested candidate establishes it.
Answer currentQuestion when present. previousVerifiedChanges are earlier host tests on
the same original team, and may explain a question about that result. Attribute those
results to the earlier test; do not say this turn tested or applied them. New edits may
be recommended only from verifiedChanges, not from a change the question merely suggests.
The server renders exact numbers, grades, configurations and losses in cards; do not
restate percentages or internal tool/field names such as test_change, tune_member and
comparisonPerformed in prose.
No casual conclusions
such as 'no stable answer', 'must choose one Mega registration', or inferred type weaknesses.
Return ONLY JSON: {"overview":"one short paragraph on the evidence and its limits",
"plan":"", "priorities":[{"text":"a concise tradeoff grounded on the host card",
"verificationId":null,"evidenceRefs":[]}]}.
Use at most two priorities. IDs go only in the JSON fields, never in prose text. Copy a tested
card's verificationId exactly into its priority's verificationId field when explaining
it. evidenceRefs must be coordinate objects copied from the host or []. English canonical
entity names, interface-language prose. At most 300 Chinese characters / 150 English words.
In Chinese say 整队观测下限 for team floor, 成员格 for member cell; never 地板 or 筛选状态.
Name a grade code (C0/C1/C2) only as the supplied facts state it. goalTargets maps the user's
own words to the opponents and members they asked about; call those by their English canonical
names (the pokemon field), like every other entity.
"""

NUDGE = """No proposal has been checked yet, and the {source} asks about this team. Within
allowedChanges {allowed} and lockedSlots {locked}: if it names a specific edit, call test_change
with exactly that edit now, even if you doubt it is legal, because the host legality check is the
answer. If it asks for an SP or Speed threshold or a reallocation, call tune_member first and then
test_change with its tuningId. Otherwise test ONE concrete edit grounded in the evidence. Reply
DONE without tools only if it asks for no change at all."""


def _summary_messages(executor: TeamAssist, lang: str) -> list[dict]:
    language = LANGUAGE_NAMES.get(lang, "English")
    return [{"role": "system", "content": (
                f"{SUMMARY_SYSTEM}Write the prose in {language}. Keep every Pokemon, move, item, ability "
                "and nature name in its English canonical form exactly as the facts spell it: the page "
                "localizes names, and a translated name can name the wrong Pokemon.\n")},
            {"role": "user", "content": json.dumps(_closing_payload(executor, lang), ensure_ascii=False)}]


def _needs_action(executor: TeamAssist) -> bool:
    """A goal (or a follow-up that opted into a new test) asks about this team, the permissions allow
    an edit, and neither a test nor a tuning was even attempted: one host nudge, never a loop."""
    if any(t["tool"] in ("test_change", "tune_member") for t in executor.trace):
        return False
    if len(executor.trace) > MAX_TOOL_CALLS - 2:
        return False
    if executor.question:
        return executor.allow_test and executor.can_test
    return bool(executor.analysis.get("goal")) and (executor.can_test or executor.can_tune)


def _closing_payload(executor: TeamAssist, lang: str) -> dict:
    rows = (executor.report.get("checks") or {}).get("byOpponent") or []
    return {"lang": lang, "format": executor.analysis["format"], "pickSize": executor.analysis["pickSize"],
            "goal": executor.analysis["goal"], "lockedSlots": executor.analysis["lockedSlots"],
            "goalTargets": executor.targets,
            "currentQuestion": executor.question, "previousQuestions": executor.previous_questions,
            "allowedChanges": executor.analysis.get("allowedChanges"),
            "actualTeam": [{"species": m["species"]} for m in executor.team["pokemon"]],
            "comparisonPerformed": any(card["status"] == "tested" for card in executor.actions + executor.previous_actions),
            "currentTurnComparisonPerformed": any(card["status"] == "tested" for card in executor.actions),
            "teamFloors": [{"ref": r["ref"], "grade": r.get("observedFloor"),
                            "complete": r.get("calculationComplete")} for r in rows],
            "verifiedChanges": [executor.card_for_model(card) for card in executor.actions],
            "previousVerifiedChanges": [executor.card_for_model(card) for card in executor.previous_actions],
            "readEvidence": executor.read_summaries(),
            "toolStatus": executor.trace,
            "limits": ["static observed configurations only", "no battle/turn/lead verification",
                       "no composite strength or win-rate conclusion"]}


def _validate_note_ref(executor: TeamAssist, ref, verification) -> None:
    try:
        read_evidence(executor.report, ref)
        return
    except (ValueError, TypeError, AttributeError):
        pass
    # A linked, host-generated comparison also owns references to its AFTER report.
    # Never accept an arbitrary new analysis ID or another card's coordinates.
    for card in executor.actions + executor.previous_actions:
        if card["verificationId"] != verification:
            continue
        for row in card["teamFloorChanges"] + card["memberCellChanges"]:
            if isinstance(ref, dict) and ref in (row.get("beforeRef"), row.get("afterRef")):
                return
    raise ValueError("reference is outside this report and linked comparison")


def explain(provider, report: dict, lang: str, executor: TeamAssist) -> tuple[str, int]:
    from . import qa
    from .builder import _extract_json

    thinking = bool(getattr(provider, "thinking_enabled", False))
    max_tokens = qa.DIAGNOSE_EXPLAIN_THINKING_MAX_TOKENS if thinking else PLAIN_MAX_TOKENS
    token_limit = 120_000 if thinking else 60_000
    tokens = 0
    metrics = {"promptTokens": 0, "completionTokens": 0, "cacheHitTokens": 0,
               "reasoningTokens": None, "usageUnknown": False, "llmCalls": 0, "calls": [], "durationMs": 0}
    executor.metrics = metrics
    started = time.monotonic()
    payload = qa._diagnose_explain_projection(report, lang)
    # The tool stage is about a concrete goal. Type coverage and role tables stay
    # visible on the page; feeding unrelated negative lists encourages phantom gaps.
    payload = {key: payload.get(key) for key in ("analysis", "legality", "checks")}
    payload["team"] = {"format": executor.analysis["format"], "pickSize": executor.analysis["pickSize"],
                       "pokemon": executor.team["pokemon"]}
    payload["goalTargets"] = executor.targets
    if executor.question:
        payload.update(currentQuestion=executor.question, previousQuestions=executor.previous_questions,
                       previousVerifiedChanges=[executor.card_for_model(card) for card in executor.previous_actions],
                       readEvidence=executor.read_summaries(), newChangeTestAllowed=executor.can_test)
    # Required evidence lookup is host-orchestrated, even if the model would skip tools. It reads the
    # opponent the user named when there is one, so the model never mistakes another row for it.
    rows = (report.get("checks") or {}).get("byOpponent") or []
    if rows:
        named = [t["teamFloor"]["ref"] for t in executor.targets["opponents"]]
        focus = named[0] if named else next((r for r in rows if r.get("observedFloor") == "C0"), rows[0])["ref"]
        payload["initialEvidence"] = executor.run("team_evidence", {"ref": focus})
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(content.encode("utf-8")) > MAX_INPUT_BYTES:
        metrics["failure"] = "input_too_large"
        raise qa.QaFailed("diagnosis input exceeds bounded context", tokens)
    profile = ("Thinking-mode output profile: use 3-6 concise paragraphs across the JSON fields."
               if thinking else "Keep the complete explanation concise, specific and actionable.")
    messages = [{"role": "system", "content": SYSTEM + "\n" + profile},
                {"role": "user", "content": f"[lang={lang}] [report]{content}\n[/report]"}]
    retried = nudged = closing_sent = False
    summary_stage = False
    try:
        for round_no in range(MAX_ROUNDS + 3):
            tool_rounds = MAX_ROUNDS + (1 if nudged else 0)
            final = round_no >= tool_rounds or len(executor.trace) >= MAX_TOOL_CALLS or tokens >= token_limit
            if final and not summary_stage:
                summary_stage = True
                messages = _summary_messages(executor, lang)
                closing_sent = False
            final = final or summary_stage
            if final and not closing_sent:
                # Once per closing context: a correction request must stay the LAST message.
                closing_sent = True
                messages.append({"role": "user", "content": "Tools are now closed. Return the final JSON from established facts. Do not request or print tools."})
            timeout = min(150 if thinking else 70, executor.remaining())
            before_call = time.monotonic()
            try:
                allowed_tools = [tool for tool in TOOLS if
                    (tool["function"]["name"] != "test_change" or executor.can_test) and
                    (tool["function"]["name"] != "tune_member" or executor.can_tune) and
                    (tool["function"]["name"] != "replacement_candidates" or executor.can_replace)]
                result = provider.chat(messages, allowed_tools, max_tokens=max_tokens,
                    tool_choice="none" if final else "auto",
                    timeout=timeout)
            except Exception as exc:
                metrics["usageUnknown"] = True
                metrics["failure"] = "provider_unavailable"
                exc.tokens = tokens
                raise
            spent = result.prompt_tokens + result.completion_tokens
            tokens += spent
            metrics["promptTokens"] += result.prompt_tokens
            metrics["completionTokens"] += result.completion_tokens
            metrics["cacheHitTokens"] += result.cache_hit_tokens
            if getattr(result, "reasoning_tokens", None) is not None:
                metrics["reasoningTokens"] = (metrics["reasoningTokens"] or 0) + result.reasoning_tokens
            metrics["usageUnknown"] |= not getattr(result, "usage_known", True)
            metrics["llmCalls"] += 1
            metrics["calls"].append({"model": str(getattr(result, "model", None) or getattr(provider, "model", "unknown")),
                "thinking": thinking, "promptTokens": result.prompt_tokens,
                "completionTokens": result.completion_tokens, "cacheHitTokens": result.cache_hit_tokens,
                "reasoningTokens": getattr(result, "reasoning_tokens", None),
                "finishReason": getattr(result, "finish_reason", None),
                "usageKnown": getattr(result, "usage_known", True),
                "durationMs": round((time.monotonic() - before_call) * 1000)})
            msg = result.message
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if calls and not final:
                for call in calls:
                    fn = call.get("function") or {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except (TypeError, ValueError):
                        args = None
                    outcome = executor.run(str(fn.get("name") or ""), args)
                    data = json.dumps(outcome, ensure_ascii=False, separators=(",", ":"))
                    available = MAX_INPUT_BYTES - len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) - 3000
                    if len(data.encode("utf-8")) > min(28_000, available):
                        # A bounded refusal stays valid JSON; never tail-cut evidence.
                        data = json.dumps({"error": {"code": "result_too_large"}})
                    messages.append({"role": "tool", "tool_call_id": str(call.get("id") or ""), "content": data})
                continue
            if not summary_stage:
                if not nudged and _needs_action(executor):
                    # A requested edit or threshold was never even attempted: one host nudge.
                    nudged = True
                    messages.append({"role": "user", "content": NUDGE.format(
                        source="current question" if executor.question else "goal",
                        allowed=json.dumps(executor.analysis.get("allowedChanges")),
                        locked=json.dumps(executor.analysis.get("lockedSlots")))})
                    continue
                # Discard the tool-stage draft: it may predate the comparison or
                # contain explanations the tools did not establish.
                summary_stage = True
                messages = _summary_messages(executor, lang)
                closing_sent = False
                continue
            doc = _extract_json(str(msg.get("content") or "")) if not calls else None
            if not isinstance(doc, dict) or not isinstance(doc.get("overview"), str) or not doc["overview"].strip():
                if not retried and executor.remaining() > 1:
                    retried = True
                    messages.append({"role": "user", "content": "The final answer must be valid JSON with overview, plan, priorities. No tools. Correct the output format only."})
                    continue
                metrics["failure"] = ("output_length" if getattr(result, "finish_reason", None) == "length"
                                      else "malformed_output")
                raise qa.QaFailed("diagnosis output is empty or malformed", tokens)
            notes = []
            malformed_refs = False
            for note in (doc.get("priorities") or [])[:3]:
                if not isinstance(note, dict) or not isinstance(note.get("text"), str):
                    continue
                verification = note.get("verificationId")
                if verification is not None and verification not in {a["verificationId"] for a in executor.actions + executor.previous_actions}:
                    continue
                refs = note.get("evidenceRefs") or []
                if not isinstance(refs, list) or len(refs) > 3:
                    malformed_refs = True
                    continue
                try:
                    for ref in refs:
                        _validate_note_ref(executor, ref, verification)
                except (ValueError, TypeError, AttributeError):
                    malformed_refs = True
                    continue
                notes.append({"text": _strip_receipts(qa._scrub_internal_tokens(
                                  qa._scrub_answer(note["text"][:1400], lang), lang)),
                              "verificationId": verification, "evidenceRefs": refs})
            prose = [doc["overview"], *(n["text"] for n in notes)]
            text = "\n\n".join(p.strip() for p in prose if isinstance(p, str) and p.strip())
            # Internal tool/field names are rewritten into page wording below; these are not.
            found = prose_gate.problems(text, lang, json.dumps(_closing_payload(executor, lang), ensure_ascii=False),
                                        language_text=doc["overview"])
            if malformed_refs:
                found.append(("refs", "evidenceRefs must be coordinate objects copied from the report or the "
                                      "comparison linked by verificationId, or []"))
            if found:
                if not retried and executor.remaining() > 1:
                    retried = True
                    messages.append({"role": "user", "content": prose_gate.retry_message(
                        found, "Explain only the supplied outcomes and losses.")})
                    continue
                metrics["failure"] = "language" if [c for c, _m in found] == ["language"] else "evidence_gate"
                raise qa.QaFailed("diagnosis summary exceeded supplied evidence", tokens)
            if qa._TOOL_MARKUP.search(text):
                metrics["failure"] = "malformed_output"
            qa._reject_tool_markup(text, tokens)
            executor.notes = notes
            return _strip_receipts(qa._scrub_internal_tokens(qa._scrub_answer(text[:7000], lang), lang)), tokens
        metrics["failure"] = "rounds_exhausted"
        raise qa.QaFailed("diagnosis tool rounds exhausted", tokens)
    except Exception as exc:
        metrics.setdefault("failure", "deadline" if getattr(exc, "code", None) == "deadline" else "internal")
        exc.tokens = tokens
        raise
    finally:
        metrics["durationMs"] = round((time.monotonic() - started) * 1000)
