"""Fact-QA orchestration (frontend/design.md §7.2): one question -> tool-grounded one-shot
answer. The system prompt + tool definitions are a byte-stable request prefix (upstream
auto-caching); at most QA_MAX_ROUNDS tool rounds over a read-only dex/meta tool surface
(the §13 v1 cut), every tool result clipped server-side, then one forced final call with
tool_choice=none. No conversation history — each question is independent.

The answer contract mirrors agent prose (design §6): entities are named by ENGLISH
CANONICAL, so the SPA's prose renderer localizes them with dex authority instead of
trusting model translations."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from collections import Counter, OrderedDict
from functools import lru_cache
from typing import Any, Callable

from .provider import ChatResult, LlmProvider, LlmUnavailable

QA_QUESTION_MAX_CHARS = 400     # mirrors frontend/protocol llm.ts
QA_MAX_ROUNDS = 3               # tool rounds; one extra forced-final call follows
QA_MAX_CALLS_PER_ROUND = 4
QA_TOOL_RESULT_CHARS = 3500     # per tool result, measured in UTF-8 BYTES (see clip_json)
QA_MAX_ANSWER_TOKENS = 900
QA_LLM_TIMEOUT = 40.0
QA_TOOL_TIMEOUT = 30.0          # per skill-tool (worker) call; also clamped by remaining deadline
QA_REPSET_TIMEOUT = 60.0        # repset is a one-shot team.py subprocess (~2s warm), not a worker
QA_DEADLINE_SECONDS = 90.0      # wall clock for the whole pipeline (§7.2 hard caps)
# Hard per-request token ceiling (§7.4): once crossed we stop spending on tool rounds and force
# the final synthesis, so one pathological question cannot burn the daily budget far past the
# reservation (the reserve stays the empirically-calibrated 12k; this caps the tail).
QA_MAX_REQUEST_TOKENS = 40_000

DEX_KINDS = ("pokemon", "move", "item", "ability", "nature")

# repset/skeleton "sps" shorthand -> team-json/calc spread keys (shared with the builder).
SPS_TO_SPREAD = {"hp": "hp", "at": "atk", "df": "def", "sa": "spa", "sd": "spd", "sp": "spe"}


def spread_from_sps(sps: Any) -> dict:
    out: dict[str, int] = {}
    if isinstance(sps, dict):
        for k, v in sps.items():
            key = SPS_TO_SPREAD.get(str(k), str(k))
            if key in ("hp", "atk", "def", "spa", "spd", "spe"):
                try:
                    out[key] = max(0, min(32, int(v or 0)))
                except (TypeError, ValueError):
                    pass
    return out


class QaFailed(RuntimeError):
    """The pipeline ran but produced no usable answer (model kept calling tools past the
    forced-final round, empty content, deadline). Carries the tokens burned so the caller
    can still settle the budget truthfully. Maps to HTTP 502."""

    def __init__(self, message: str, tokens: int = 0):
        super().__init__(message)
        self.tokens = tokens


SYSTEM_PROMPT = """\
You are the fact-lookup assistant of a Pokemon Champions data site.

Rules:
- If the user supplies a public team code, use team_code_lookup to retrieve its actual
  members and configurations before answering about that team. Accept only ONE explicitly
  supplied code per question. Never invent, enumerate, repair or retry codes; if retrieval
  fails, explain that the team could not be loaded. Codes do not establish format or season.
- Answer ONE factual question about Pokemon Champions: battle-dex facts (types, stats,
  abilities, moves, items, natures), the current metagame (usage ranking, common
  moves/items/abilities/natures/partners/SP spreads, representative real-team sets), or a
  concrete damage / speed comparison.
- You MUST call at least one tool before stating ANY Pokemon Champions fact — never answer
  a factual question from memory. Ground every factual claim in tool output; never invent
  numbers, sets, or rankings. If you answer WITHOUT calling any tool, your reply must be a
  brief out-of-scope / cannot-answer decline only (that is the ONLY ungrounded reply allowed).
  If the tools cannot answer, say so plainly.
- Pass entity names to tools AS THE USER WROTE THEM (any language) — the tools resolve
  zh/ja/en themselves. Do NOT translate names into English yourself: idioms like
  "Alolan Ninetales" miss the database (the canonical is "Ninetales-Alola").
- A not_found result IS the answer for that entity, not a spelling to work around: the tools
  already accept every language and minor typos. Say the Pokemon is not in the current battle
  dex (not available under this ruleset) or has no usage data this season, as the result's
  note states — never retry it under other spellings, languages or unrelated searches.
- An ambiguous result means the name needs clarification. Show its candidates and ask which
  form the user meant; do not claim that the Pokemon is unavailable or has no usage data.
- For "can X OHKO/2HKO Y", "how much does X's move do to Y" or "is X faster than Y",
  call calc_damage / calc_speed — NEVER estimate damage or speed from memory. Both sides
  default to their most-used competitive set (a Mega form uses its real-team representative
  set; a set holding its own Mega Stone is computed as that Mega form, with the Mega's ability,
  so the tool may name "Mega X" for an asked "X"); ALWAYS disclose that assumption briefly when
  you state the result, and mention the
  assumed items when they matter. calc_damage/calc_speed resolve the sets INTERNALLY — they
  do not need a prior detail/repset lookup, and they still work when such a lookup missed:
  always TRY the calc tool before declining. If a tool ultimately fails, decline plainly —
  never substitute numbers from memory.
- For "what outspeeds X" / "which pokemon are faster than X" / "is X fast enough for the meta",
  call speed_ladder ONCE — it returns X plus the ranked field already sorted and split into
  outspeedsMe / ties / slowerThanMe. Do NOT loop calc_speed over the ranking: that runs out of
  tool rounds before you have enough of the field, and the answer ends up incomplete.
- speed_ladder compares most-used sets (finalSpeed, s, outspeedsMe/ties/slowerThanMe). Use
  speedRange for "X's speed range" (X's observed real-team builds). For "reliably/稳定 faster" use
  each row's rel and p: "reliably faster" means that opponent's most-used set outspeeds at least
  90% of X's observed builds — say so with the percentage; never call "usually faster" or
  "depends on build" reliable. rel "modal only" means X has no observed distribution: compare
  the most-used sets and say so. Set opponent_ranges=true ONLY when the user explicitly asks about
  the opponents' own speed ranges or build variance.
- For "which pokemon can learn X (and Y)" / "who learns X" / "which X-type pokemon have ability
  Y" questions, call dex_find ONCE with every condition together — it searches the whole
  battle dex, which is exactly the set of Pokemon available under the current ruleset. Report
  the count and the names compactly; mention Mega forms as such. Copy every name EXACTLY as the
  tool lists it, one canonical name per entry: forms stay separate entries ("Gourgeist-Average,
  Gourgeist-Large"), never merged into a group like "Gourgeist (Average/Large forms)" and never
  shortened to the bare species — the site can only localize the exact canonical name.
- repset_lookup gives the representative REAL-TEAM joint set (ability+item+nature+moves+SP
  played together) — prefer it over stitching independent usage modes when the question is
  "how is X actually built".
- Stat allocations in Champions are SP (0–32 per stat), not conventional EVs. Call them SP
  in every language; never describe a Spe SP allocation as "速度努力值" or "速度努力".
- Answer in the language given by the [lang=..] tag. Be concise: a short paragraph,
  at most ~120 words, no headings or bullet lists unless listing is the answer.
- PLAIN TEXT only — no markdown (**bold**, headers, tables); the site renders entities
  itself and markdown markers show up as literal noise.
- Name every Pokemon, move, item, ability and nature by its ENGLISH CANONICAL name exactly as the
  tools return it — the site localizes names itself; hand-translated names break that. Write each
  name ONCE: never add a translation in parentheses ("Leftovers (吃剩的东西)"), the page already
  shows the localized name.
- Never name the tools or quote their field names (repset_lookup, meta_detail, archetypes,
  panels, …) in the answer; describe the data in plain words ("real-team sets", "usage data").
- This box only does factual lookups: for team building or anything off-topic, briefly say
  it is out of scope and point to the site's builder/calculator pages.
"""

TOOLS: list[dict] = [
    {"type": "function", "function": {
        "name": "team_code_lookup",
        "description": "Read the complete six-member team for a ten-character public code explicitly supplied by the user. Never invent codes. Format, season and rule are unknown; do not infer them from the result.",
        "parameters": {"type": "object", "properties": {"code": {"type": "string"}},
                       "required": ["code"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "dex_lookup",
        "description": "Look up one canonical fact sheet from the battle dex: a pokemon "
                       "(types/base stats/abilities), a move, an item, an ability or a "
                       "nature. Accepts Chinese / Japanese / English names.",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": list(DEX_KINDS)},
            "name": {"type": "string", "description": "entity name in any language"},
        }, "required": ["kind", "name"]},
    }},
    {"type": "function", "function": {
        "name": "dex_find",
        "description": "Reverse search of the battle dex: every pokemon (forms and Megas "
                       "included) matching ALL the given conditions — learns every listed "
                       "move, has the listed types / ability, meets the base-stat bounds. The "
                       "dex holds exactly the Pokemon available under the current ruleset. Use "
                       "it for 'which pokemon can learn X and Y' / 'who learns X'. Quote the "
                       "returned names verbatim, one per entry — never merge forms.",
        "parameters": {"type": "object", "properties": {
            "moves": {"type": "array", "items": {"type": "string"}, "maxItems": 4,
                      "description": "moves the pokemon must all learn (any language)"},
            "types": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
            "ability": {"type": "string"},
            "stats": {"type": "array", "items": {"type": "string"}, "maxItems": 3,
                      "description": "base-stat bounds such as 'spe>=100' or 'hp<=60'"},
        }},
    }},
    {"type": "function", "function": {
        "name": "meta_ranking",
        "description": "Current-season usage ranking (top N) for singles or doubles.",
        "parameters": {"type": "object", "properties": {
            "format": {"type": "string", "enum": ["single", "double"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        }, "required": ["format"]},
    }},
    {"type": "function", "function": {
        "name": "meta_detail",
        "description": "Current-season usage detail for ONE pokemon: common moves, items, "
                       "abilities, natures, partners and SP spreads, for singles or doubles.",
        "parameters": {"type": "object", "properties": {
            "format": {"type": "string", "enum": ["single", "double"]},
            "pokemon": {"type": "string", "description": "pokemon name in any language"},
        }, "required": ["format", "pokemon"]},
    }},
    {"type": "function", "function": {
        "name": "repset_lookup",
        "description": "Representative REAL-TEAM joint set(s) for one pokemon: ability, item, "
                       "nature, moves and SP spread as actually played together — more "
                       "faithful than stitching independent usage modes. Accepts Mega forms.",
        "parameters": {"type": "object", "properties": {
            "format": {"type": "string", "enum": ["single", "double"]},
            "pokemon": {"type": "string", "description": "pokemon name in any language"},
        }, "required": ["format", "pokemon"]},
    }},
    {"type": "function", "function": {
        "name": "speed_ladder",
        "description": "Speed ladder for ONE pokemon against the current metagame: its own final "
                       "speed plus the most-used sets of the top-N ranked pokemon, sorted fastest "
                       "first, marking which outspeed it. Also X's observed real-team speedRange, and "
                       "per opponent the share of X's observed builds its most-used set outspeeds "
                       "(p, rel: 'reliably faster' = at least 90%). Use this for 'what outspeeds X' "
                       "/ 'is X fast enough' / 'X's speed range' / 'who is reliably faster' — ONE "
                       "call covers the whole field, never loop calc_speed over the ranking. The "
                       "result's note defines every field.",
        "parameters": {"type": "object", "properties": {
            "pokemon": {"type": "string", "description": "pokemon name in any language"},
            "format": {"type": "string", "enum": ["single", "double"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 30,
                      "description": "how many ranked opponents to include (default 20)"},
            "opponent_ranges": {"type": "boolean",
                                "description": "true ONLY when the user explicitly asks about the "
                                               "opponents' own speed ranges or build variance; "
                                               "adds each opponent's real-team range and is "
                                               "slower. Default false."},
        }, "required": ["pokemon", "format"]},
    }},
    {"type": "function", "function": {
        "name": "calc_damage",
        "description": "Compute REAL damage of one attack: damage %, KO chance, and the "
                       "assumed sets. Both sides default to their most-used competitive set "
                       "(usage modes; Mega forms use their real-team representative set); a set "
                       "holding its own Mega Stone is computed as that Mega form with its ability. "
                       "Optional item overrides answer what-if-holding-X questions.",
        "parameters": {"type": "object", "properties": {
            "attacker": {"type": "string"},
            "defender": {"type": "string"},
            "move": {"type": "string", "description": "the attacking move (any language)"},
            "format": {"type": "string", "enum": ["single", "double"]},
            "attacker_item": {"type": "string", "description": "optional item override"},
            "defender_item": {"type": "string", "description": "optional item override"},
        }, "required": ["attacker", "defender", "move"]},
    }},
    {"type": "function", "function": {
        "name": "calc_speed",
        "description": "Compare the final speed of two pokemon under their most-used sets "
                       "(nature, Spe SP, item, ability). Returns both numbers and who is "
                       "faster.",
        "parameters": {"type": "object", "properties": {
            "pokemon_a": {"type": "string"},
            "pokemon_b": {"type": "string"},
            "format": {"type": "string", "enum": ["single", "double"]},
        }, "required": ["pokemon_a", "pokemon_b"]},
    }},
]


def prune(node: Any, list_cap: int = 12, str_cap: int = 400) -> Any:
    """Structural trim that KEEPS the JSON shape (lists capped with an omission marker,
    long strings ellipsized). Model-facing; also used by the builder wizard, whose prompt
    payloads must stay valid JSON (clip_json's byte-level tail cut may not)."""
    if isinstance(node, dict):
        return {k: prune(v, list_cap, str_cap) for k, v in node.items()}
    if isinstance(node, list):
        out = [prune(v, list_cap, str_cap) for v in node[:list_cap]]
        if len(node) > list_cap:
            out.append(f"…{len(node) - list_cap} more omitted")
        return out
    if isinstance(node, str) and len(node) > str_cap:
        return node[:str_cap] + "…"
    return node


def _utf8_clip(s: str, cap: int) -> str:
    """Cut a string to at most `cap` UTF-8 bytes without splitting a codepoint."""
    b = s.encode("utf-8")
    if len(b) <= cap:
        return s
    return b[:cap].decode("utf-8", "ignore")


def clip_json(doc: Any, cap: int = QA_TOOL_RESULT_CHARS) -> str:
    """Serialize a tool result under a UTF-8 BYTE budget: structural prune first (lists/
    strings), hard cut as the last resort — a clipped tail is model-facing, not a wire DTO.
    Bytes, not characters: a CJK-heavy result must not reach the model at ~3x the cap."""
    s = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    if len(s.encode("utf-8")) <= cap:
        return s
    s = json.dumps(prune(doc), ensure_ascii=False, separators=(",", ":"))
    return s if len(s.encode("utf-8")) <= cap else _utf8_clip(s, cap) + "…(truncated)"


def _clean_name(raw: Any) -> str | None:
    name = str(raw or "").strip()[:100]
    if not name or name.startswith("-"):
        return None
    # DeepSeek's tool-argument generator occasionally DOUBLES a name verbatim (seen live:
    # "キュウコン（アローラのすがた）キュウコン（アローラのすがた）"), which then misses every
    # lookup and reads as "no data". No real entity name is its own first half repeated —
    # fold the duplication instead of failing the question.
    half = len(name) // 2
    if len(name) >= 6 and len(name) % 2 == 0 and name[:half] == name[half:]:
        name = name[:half]
    return name


# DeepSeek occasionally writes its tool-call DSL INTO the content instead of the structured
# tool_calls field (seen live: an answer body of <｜DSML｜invoke ...> markup). Such a reply
# is machine noise, never an answer — fail it (502, quota refunded) instead of showing it.
_TOOL_MARKUP = re.compile(r"<[|｜]{1,2}|DSML|tool[_▁]calls|<tool_call")


# The forced-final turn and its one correction. Plain user turns at the END of the transcript, so
# the byte-stable system + tools prefix stays cacheable.
FINAL_TURN = ("[No tools remain for this question.] Write the final answer now, in plain text, "
              "from the tool results above. If they do not answer the question, say plainly "
              "what could not be found. Do not call or write out any tool call.")
FINAL_RETRY = ("That was a tool call, and no tools are available any more. Reply with the final "
               "answer in plain text only, using the tool results you already have.")


def _reject_tool_markup(content: str, tokens: int) -> str:
    if _TOOL_MARKUP.search(content):
        raise QaFailed("model leaked tool-call markup into the answer", tokens)
    return content


# Model-facing words the answer must not repeat (design §2.1: no internal identifiers in UI text).
# The prompt forbids them; this rewrite is the guarantee. Tool names are machine-shaped in every
# language; the English data words below can only be an echo inside zh/ja prose.
_TOOL_TERMS = {
    "repset_lookup": {"zh": "真实队伍数据", "en": "real-team data", "ja": "実戦構築データ"},
    "meta_detail": {"zh": "使用率数据", "en": "usage data", "ja": "使用率データ"},
    "meta_ranking": {"zh": "使用率排名", "en": "usage ranking", "ja": "使用率ランキング"},
    "dex_lookup": {"zh": "图鉴", "en": "the dex", "ja": "図鑑"},
    "dex_find": {"zh": "图鉴检索", "en": "a dex search", "ja": "図鑑検索"},
    "speed_ladder": {"zh": "速度线", "en": "the speed ladder", "ja": "素早さライン"},
    "calc_damage": {"zh": "伤害计算", "en": "a damage calc", "ja": "ダメージ計算"},
    "calc_speed": {"zh": "速度比较", "en": "a speed comparison", "ja": "素早さ比較"},
    "repset": {"zh": "真实队伍数据", "en": "real-team data", "ja": "実戦構築データ"},
}
_CJK_ECHO_TERMS = {
    "Champions battle dex": {"zh": "对战图鉴", "ja": "対戦図鑑"},
    "battle dex": {"zh": "对战图鉴", "ja": "対戦図鑑"},
    "usage modes": {"zh": "各项最高使用率", "ja": "各項目の最多採用"},
}
_LANG_TAG = re.compile(r"\[lang=[a-z]{2}\]\s*")
_CJK = re.compile("[　-ヿ一-鿿＀-￯]")


_GLOSS = re.compile(r"(?P<sep> ?)(?P<open>[（(])(?P<head>[^（）()，,、；;/]{1,24})"
                    r"(?P<rest>[，,、；;/][^（）()]{0,40})?(?P<close>[）)])")


@lru_cache(maxsize=1)
def _dex_vocabulary() -> tuple[re.Pattern, frozenset[str], dict[str, frozenset[str]]]:
    """Every English canonical (to find a name in prose) and every zh/ja name and alias (to tell
    a translation gloss from an explanation)."""
    from .. import mappers
    conn = mappers._conn()
    canon: set[str] = set()
    local: set[str] = set()
    equivalents: dict[str, set[str]] = {}
    for table in ("pokemon", "moves", "abilities", "items", "natures"):
        for row in conn.execute(f"select canonical, display_name, display_name_ja from {table}"):
            canon.add(row[0])
            local.update(n for n in (row[1], row[2]) if n)
            equivalents.setdefault(row[0], set()).update(n for n in (row[1], row[2]) if n)
    for alias, canonical in conn.execute("select alias, canonical from aliases"):
        if alias and _CJK.search(alias):
            local.add(alias)
            equivalents.setdefault(canonical, set()).add(alias)
    alternation = "|".join(re.escape(n) for n in sorted(canon, key=len, reverse=True) if n)
    return (re.compile(rf"(?<![\w-])(?:{alternation})(?![\w-])"), frozenset(local),
            {name: frozenset(aliases) for name, aliases in equivalents.items()})


def _drop_name_glosses(text: str) -> str:
    """`Leftovers（吃剩的东西）` -> `Leftovers`: the page renders the canonical in the reader's
    language, so a parenthesised dex name after it shows the same item twice (or, when the model
    picked the wrong name, contradicts it). Only a gloss that IS a dex name or alias goes; an
    explanation in the parentheses ("Mega 前") stays, and so does a figure after the name
    (`Adamant（固执，26.9%）` -> `Adamant（26.9%）`)."""
    try:
        names, local, equivalents = _dex_vocabulary()
    except Exception:
        return text
    out: list[str] = []
    pos = 0
    for m in names.finditer(text):
        g = _GLOSS.match(text, m.end())
        if not g or g.group("head").strip() not in local:
            continue
        rest = (g.group("rest") or "")[1:].strip()
        out.append(text[pos:g.start()])
        if rest:
            out.append(f"{g.group('sep')}{g.group('open')}{rest}{g.group('close')}")
        pos = g.end()
    out.append(text[pos:])
    text = "".join(out)
    # Models sometimes reverse the bilingual pair. Only fold equivalent names: a nature
    # after a species ("耿鬼（Timid）") describes its set and must stay.
    localized = "|".join(re.escape(n) for n in sorted(local, key=len, reverse=True) if _CJK.search(n))
    reversed_pair = re.compile(rf"(?P<local>{localized})(?P<gloss>{_GLOSS.pattern})")

    def fold(m: re.Match) -> str:
        canonical = m.group("head").strip()
        if m.group("local") not in equivalents.get(canonical, ()):
            return m.group(0)
        rest = (m.group("rest") or "")[1:].strip()
        return canonical + (f"{m.group('sep')}{m.group('open')}{rest}{m.group('close')}" if rest else "")

    return reversed_pair.sub(fold, text)


def _scrub_answer(text: str, lang: str) -> str:
    """Drop the request's own `[lang=..]` tag and rewrite tool names (and, in zh/ja prose, the
    English data words of the tool results) into the wording the page uses."""
    lang = lang if lang in ("zh", "ja", "en") else "en"
    text = _drop_name_glosses(_LANG_TAG.sub("", text))
    if lang == "zh":
        text = re.sub(r"(\d+)\s*(?:点\s*)?(HP|体力|攻击|防御|特攻|特防|速度)努力(?:值)?",
                      r"\1 \2 SP", text)
    mapping = {token: terms[lang] for token, terms in _TOOL_TERMS.items()}
    if lang != "en":
        mapping.update({token: terms[lang] for token, terms in _CJK_ECHO_TERMS.items()})
    words = {token.lower(): word for token, word in mapping.items()}
    alternation = "|".join(re.escape(t) for t in sorted(mapping, key=len, reverse=True))
    pattern = re.compile(rf"( ?)(?<![\w])({alternation})(?![\w])( ?)", re.IGNORECASE)

    def swap(m: re.Match) -> str:
        # A space the model set around an English word goes with it when the neighbour is CJK.
        before, after = m.string[m.start() - 1:m.start()], m.string[m.end():m.end() + 1]
        word = words[m.group(2).lower()]
        if lang == "en":
            return m.group(1) + word + m.group(3)
        return (("" if _CJK.match(before or "x") or _CJK.match(word[0]) else m.group(1)) + word
                + ("" if _CJK.match(after or "x") or _CJK.match(word[-1]) else m.group(3)))

    return pattern.sub(swap, text).strip()


def _strip_markdown(text: str) -> str:
    """The prompt demands plain text, but chat models still sprinkle markdown out of habit
    and the SPA renders entities itself — stray markers are literal noise. Strip the common
    ones conservatively: paired emphasis, inline code/fences, and leading heading/bullet/
    quote markers. Inline hyphens, '#1'-style ranks and numeric ranges are left untouched
    (only line-leading markers are removed).

    Blank lines are DROPPED, not collapsed: models pad plain-text answers with markdown-style
    paragraph breaks, and the SPA already spaces every line as its own block — so each blank line
    arrives as a second, empty block and the answer reads as if it has holes punched through it."""
    text = text.replace("**", "").replace("__", "").replace("`", "")
    lines = []
    for line in text.split("\n"):
        s = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)   # ATX heading -> its text
        s = re.sub(r"^\s{0,3}[-*+]\s+", "", s)        # bullet marker -> the item text
        s = re.sub(r"^\s{0,3}>\s?", "", s)            # blockquote marker
        if s.strip():
            lines.append(s)
    return "\n".join(lines).strip()


_SPREAD_KEYS = ("hp", "atk", "def", "spa", "spd", "spe")

# Main-series English regional idiom -> the dex's canonical suffix form. Models translate
# user names into "Alolan Ninetales" out of habit; the dex only knows "Ninetales-Alola".
_REGION_FORMS = {"alolan": "Alola", "galarian": "Galar", "hisuian": "Hisui",
                 "paldean": "Paldea"}
_REGION_RE = re.compile(r"^(Alolan|Galarian|Hisuian|Paldean)\s+(.+)$", re.IGNORECASE)


def _resolve_query(pool: Any, name: str, kind: str = "pokemon",
                   timeout: float = 15.0) -> str:
    """Snap one tool-argument name to dex canonical when possible (trilingual fuzzy);
    unresolvable names pass through verbatim so the downstream lookup reports the miss
    honestly. Also tries the regional-idiom rewrite (seen live: 'Alolan Ninetales' missed
    every lookup while 'Ninetales-Alola' and all zh/ja spellings hit)."""
    candidates = [name]
    m = _REGION_RE.match(name)
    if m:
        candidates.append(f"{m.group(2)}-{_REGION_FORMS[m.group(1).lower()]}")
    try:
        doc = pool.request_json(
            "dex", ["resolve", *candidates, "--format", "json", "--kind", kind],
            timeout=timeout)
        for e in doc if isinstance(doc, list) else []:
            if isinstance(e, dict) and e.get("ok") and e.get("canonical"):
                return str(e["canonical"])
    except Exception:
        pass
    return name


def _repset_budget(timeout: float, budget: float | None) -> float:
    """The repset fallback is a COLD one-shot `team.py` subprocess, so it earns more than
    QA_TOOL_TIMEOUT — but never more than the pipeline has left.

    Without the clamp `max(timeout, QA_REPSET_TIMEOUT)` re-inflated an already deadline-bounded
    budget back to a flat 60s, so ONE fallback could reserve two thirds of the 90s wall clock and
    starve every remaining tool call. Speed questions hit this constantly: a Mega form is keyed in
    the meta by its BASE species, so `meta detail` misses and both combatants land here — 2x60s
    against a 90s deadline, which surfaced to the user as an intermittent "answer failed"."""
    want = max(timeout, QA_REPSET_TIMEOUT)
    return max(1.0, min(want, budget) if budget is not None else want)


def _form_abilities(pool: Any, forms: list[str], timeout: float) -> dict[str, list[str]]:
    """Each form's own dex abilities, in one batch call."""
    if not forms:
        return {}
    cards = pool.request_json("dex", ["batch", "pokemon", *forms, "--format", "json"],
                              timeout=timeout)
    return {form: list(card.get("abilities") or [])
            for form, card in zip(forms, cards if isinstance(cards, list) else [])
            if isinstance(card, dict)}


def _resolve_modal(pool: Any, fmt: str, name: str, timeout: float,
                   budget: float | None) -> tuple[dict, str, bool]:
    """Most-used set for one calc combatant: usage modes (mapped detail — English canonical
    everywhere), falling back to the real-team representative set for forms the meta keys
    by base species (Megas), then to a bare default. Returns (combatant, provenance, definitive) —
    the provenance rides into the tool result so the model can DISCLOSE the assumption.

    `definitive` says whether the same data snapshot would give the same answer again, i.e. whether
    `_modal_combatant` may cache it. A lookup that raised (timeout, worker fault) or answered with an
    error other than the meta's `not_found` could have changed the outcome, so whatever followed it
    is served once and not kept: a meta timeout must not pin a real-team set over the usage modes."""
    from .. import mappers
    meta_missed = False
    try:
        doc = pool.request_json("meta", ["detail", "--format", fmt, "--pokemon", name,
                                         "--output", "json"], timeout=timeout)
        if isinstance(doc, dict) and not doc.get("error"):
            dto = mappers.map_detail(doc)
            panels = dto.get("panels") or {}
            out: dict[str, Any] = {"name": dto.get("name") or name}
            for panel, key in (("items", "item"), ("abilities", "ability"),
                               ("natures", "nature")):
                entries = panels.get(panel) or []
                if entries and entries[0].get("name"):
                    out[key] = entries[0]["name"]
            spreads = panels.get("spreads") or []
            spread = (spreads[0] or {}).get("spread") if spreads else None
            if isinstance(spread, dict) and spread:
                # Usage spreads are complete allocations stored sparsely: an omitted axis is zero.
                # Passing a sparse set to the speed engine would replace its missing Spe with 32.
                out["sps"] = {k: int(spread.get(k, 0)) for k in _SPREAD_KEYS
                              if isinstance(spread.get(k, 0), (int, float))}
            return out, "most-used set (usage modes)", True
        meta_missed = _not_found(doc)
    except Exception:
        pass
    try:
        doc = pool.request_json("team", ["repset", name, "--game-format", fmt,
                                         "--format", "json"],
                                timeout=_repset_budget(timeout, budget))
        answered = (isinstance(doc, dict) and not doc.get("error") and doc.get("ok") is not False
                    and isinstance(doc.get("archetypes"), list))
        arch = doc["archetypes"] if answered else []
        if arch and isinstance(arch[0], dict):
            a = arch[0]
            out = {"name": a.get("species") or name}
            for k in ("item", "ability", "nature"):
                if a.get(k):
                    out[k] = a[k]
            sps = spread_from_sps(a.get("sps"))
            if sps:
                out["sps"] = sps
            item_filter = (doc.get("query") or {}).get("item_filter")
            if item_filter and a.get("item") == item_filter:
                # The doubles library stores a Mega as its base species + stone, so repset answers
                # an asked Mega from the base species' rows narrowed to that stone: the archetype
                # names the BASE and records the pre-Mega ability. The combatant is still the form
                # that was asked for, with its own ability — base Gengar runs 150 where the same set
                # as Mega Gengar runs 170.
                out["name"] = name
                own = _form_abilities(pool, [name], timeout).get(name) or []
                if own and out.get("ability") not in own:
                    out["ability"] = own[0]
            return out, "real-team representative set", meta_missed
        if answered:
            # Both sources answered and neither knows this pokemon in this format: outside the
            # tracked ranking AND no real-team sample. That is a fact of the snapshot (the same
            # question gets the same answer until the data changes), not a failure — cacheable when
            # the meta's miss was a clean not_found too.
            return {"name": name}, "no usage data — default neutral set", meta_missed
    except Exception:
        pass
    return {"name": name}, "no usage data — default neutral set", False


# Process-local cache of `_resolve_modal` results and of speed_ladder's real-team speed ranges. A
# speed ladder resolves the asked pokemon plus up to 30 ranked ones, and a singles + doubles question
# two such ladders (~42 sets) inside the 90s QA deadline; a Mega costs a cold team.py subprocess each
# time, the ranges a batched one. Sized for both formats x the top-30 field, a modal set and a range
# each, plus the asked pokemon and calc_damage/calc_speed combatants, with headroom. Keyed by the data
# snapshot (below), the format and the resolved English canonical, so a refresh or deploy can never
# serve an answer from older data. QA runs on several llm-lane threads at once, hence the lock; two
# threads missing on the same key both resolve it, which is harmless (same answer).
MODAL_CACHE_MAX = 320
_MODAL_CACHE: OrderedDict[tuple, Any] = OrderedDict()
_MODAL_LOCK = threading.Lock()
_MODAL_SNAPSHOT: dict[str, str | None] = {"seen": None}
# Everything a modal set is computed from lives at the top level of these directories: the meta's
# current.json and season panels, the real-team library (repset honours CHAMP_TEAM_DATA), and the dex
# (names, Mega forms, abilities).
_META_DATA_REL = "pokemon-champions-meta/data"
_TEAM_DATA_REL = "pokemon-champions-team/data/teams"
_DEX_DATA_REL = "pokemon-champions-dex/data"


def _snapshot_identity() -> str:
    """Identity of the skill data a modal set comes from: the RESOLVED skills root plus the name,
    mtime and size of every file in the data directories. A deployed host reaches its skills through
    `current -> releases/<deploymentId>`, so a release switch changes the root even before the restart
    that follows it; a refresh in a dev checkout rewrites the files in place. A stat sweep, not a content
    hash: ~50 entries, about a millisecond, once per tool call. Any doubt reads as a different snapshot
    (a miss), never as the same one."""
    from ..paths import SKILLS_ROOT
    dirs = (SKILLS_ROOT / _META_DATA_REL,
            os.environ.get("CHAMP_TEAM_DATA") or SKILLS_ROOT / _TEAM_DATA_REL,
            SKILLS_ROOT / _DEX_DATA_REL)
    h = hashlib.sha256(os.path.realpath(SKILLS_ROOT).encode("utf-8"))
    for d in dirs:
        stats: list[tuple[str, int, int]] = []
        try:
            with os.scandir(d) as entries:
                for e in entries:
                    if e.is_file():
                        st = e.stat()
                        stats.append((e.name, st.st_mtime_ns, st.st_size))
        except OSError:
            stats = [("<unreadable>", 0, 0)]
        h.update(f"\0{os.path.realpath(d)}\0{sorted(stats)!r}".encode("utf-8"))
    return h.hexdigest()[:16]


def _modal_snapshot(pool: Any) -> str:
    """The current data identity. On a change every cached set is dropped AND the pool's idle
    resident workers are recycled: the meta worker parses each panel file once per process
    (meta_query's lru_cache), so after an in-place refresh it keeps answering from the old panels,
    and this cache would pin that stale answer under the NEW identity long after the worker is
    reaped. (A deployed host never gets here: every release switch restarts the process.)"""
    snapshot = _snapshot_identity()
    with _MODAL_LOCK:
        previous = _MODAL_SNAPSHOT["seen"]
        if previous != snapshot:
            _MODAL_CACHE.clear()
            _MODAL_SNAPSHOT["seen"] = snapshot
    if previous is not None and previous != snapshot and hasattr(pool, "reap_idle"):
        pool.reap_idle(0.0)
    return snapshot


def _modal_combatant(pool: Any, fmt: str, name: str, timeout: float,
                     budget: float | None = None,
                     snapshot: str | None = None) -> tuple[dict, str]:
    """`_resolve_modal` through the snapshot-keyed cache. A caller resolving a whole field passes the
    `snapshot` it took once. Only definitive answers are kept; every caller gets its own copy of the
    combatant (calc_damage writes item overrides into it)."""
    key = (snapshot or _modal_snapshot(pool), fmt, name)
    hit = _cache_get(key)
    if hit is None:
        combatant, provenance, definitive = _resolve_modal(pool, fmt, name, timeout, budget)
        if not definitive:
            return combatant, provenance
        hit = _cache_put(key, (combatant, provenance))
    return copy.deepcopy(hit[0]), hit[1]


def _cache_get(key: tuple) -> Any:
    with _MODAL_LOCK:
        hit = _MODAL_CACHE.get(key)
        if hit is not None:
            _MODAL_CACHE.move_to_end(key)
    return hit


def _cache_put(key: tuple, value: Any) -> Any:
    """Keep a DEFINITIVE answer (callers decide that) and evict the least recently used past the bound.
    Values are never handed out for mutation: callers copy, or only read."""
    with _MODAL_LOCK:
        _MODAL_CACHE[key] = value
        _MODAL_CACHE.move_to_end(key)
        while len(_MODAL_CACHE) > MODAL_CACHE_MAX:
            _MODAL_CACHE.popitem(last=False)
    return value


def _dex_cards(pool: Any, names: Any, timeout: float) -> dict[str, dict]:
    """Dex pokemon cards for `names`, one batch call; a name the dex refuses is simply absent."""
    names = sorted({n for n in names if n})
    if not names:
        return {}
    cards = pool.request_json("dex", ["batch", "pokemon", *names, "--format", "json"],
                              timeout=timeout)
    return {n: c for n, c in zip(names, cards if isinstance(cards, list) else [])
            if isinstance(c, dict) and not c.get("error") and c.get("ok") is not False}


def _stones(card: dict | None) -> dict[str, str]:
    """Mega Stone -> the Mega form this roster card turns into (its dex `mega_forms`)."""
    return {str(f["required_item"]): str(f["name"]) for f in (card or {}).get("mega_forms") or []
            if isinstance(f, dict) and f.get("name") and f.get("required_item")}


def _battle_forms(pool: Any, sides: list[tuple[dict, str]], timeout: float,
                  cards: dict[str, dict] | None = None) -> list[tuple[dict, str]]:
    """Each speed combatant in the form it battles as. The ranking and the usage modes key a Mega by
    its base species, so the modal Garchomp is `Garchomp @ Garchompite Z` — and the speed engine
    prices exactly the species it is given: 154 as base Garchomp, where the battle runs at Mega
    Garchomp Z's 203. Same rule as the team skill (mega.py): the roster card's dex `mega_forms`
    decides stone -> form (X/Y/Z stones apart, Floette-Eternal's Floettite included), and a pre-Mega
    ability gives way to the form's own. A dex failure raises: silently pricing the base form is the
    wrong answer this exists to prevent. `cards` may carry cards the caller already holds."""
    holders = {c["name"] for c, _ in sides if c.get("item")}
    if not holders:
        return sides
    cards = dict(cards or {})
    cards.update(_dex_cards(pool, holders - set(cards), timeout))
    forms = {(h, stone): form for h in holders for stone, form in _stones(cards.get(h)).items()}
    used = {forms[(c["name"], c["item"])] for c, _ in sides if (c["name"], c.get("item")) in forms}
    abilities = {f: list(cards[f].get("abilities") or []) for f in used if f in cards}
    abilities.update(_form_abilities(pool, sorted(used - set(abilities)), timeout))
    out = []
    for combatant, src in sides:
        form = forms.get((combatant["name"], combatant.get("item")))
        if form:
            combatant = {**combatant, "name": form}
            own = abilities.get(form) or []
            if own and combatant.get("ability") not in own:
                combatant["ability"] = own[0]
        out.append((combatant, src))
    return out


# --- real-team speed data (speed_ladder) --------------------------------------------------------
# The ladder compares most-used set with most-used set. What one modal set cannot say is the ASKED
# pokemon's speed range, or how dependably an opponent's most-used set beats it: that needs the builds
# real teams play. The team skill exposes joint builds (nature + Spe SP + item on one row) only through
# `search`, which returns whole raw teams, so that read is for the asked pokemon alone (one team.py
# run). An opponent is then "reliably faster" by SHARE: its most-used set outspeeds at least
# SPEED_RELIABLE of those observed builds. Ranging every opponent the same way would read whole raw
# teams for the field (~110 MB / ~37 s for a doubles top 20).
#
# Opponents' OWN ranges are opt-in (`opponent_ranges`) and come from `repset` archetypes instead: each
# (item, ability) cluster's speed_profile gives its lowest and highest Spe SP and the natures seen,
# marginal within the cluster, read as an ENVELOPE (lowest SP with its slowest nature .. highest with
# its fastest), never narrower than the builds really played. One more team session (~1.5 s singles,
# ~5 s doubles cold on a dev box), which is why it is not the default.
SPEED_RANGE_MIN_ROWS = 5         # observed rows with a Spe SP below which no distribution is stated
SPEED_RANGE_CLUSTERS = 6         # archetypes read per library form (repset's own default is 3)
SPEED_JOINT_TEAM_LIMIT = 1500    # raw teams read for the asked pokemon (a partial read says so)
# A team.py read here may use at most this, and at most half of what the QA deadline has left: the
# modal ladder must still reach the model with time to answer, with or without the real-team data.
SPEED_RANGE_TIMEOUT = 30.0
SPEED_RELIABLE = 0.90            # share of the asked pokemon's observed builds: "reliably" faster/slower
SPEED_USUAL = 0.60               # ... "usually" faster/slower
SPEED_USUAL_TIE = 0.50           # share that ties: "usually ties"
_SPEED_NATURE = {-1: "Brave", 0: "Serious", 1: "Timid"}    # stand-ins for -Spe / neutral / +Spe


@lru_cache(maxsize=1)
def _nature_speed_effects() -> dict[str, tuple[str, int]]:
    """Every dex nature name and alias, lower-cased -> (canonical, Speed effect -1/0/+1). Real-team rows
    carry natures as their source typed them ('timid', 'Modesto', 'mid', 've'); only an exact dex name
    or alias counts and the rest are dropped and counted — reading 'mid' as Mild (neutral) or Timid
    (+Spe) would be a guess that moves a range edge."""
    from .. import mappers
    conn = mappers._conn()
    effect = {str(r[0]): (1 if r[1] == "spe" else -1 if r[2] == "spe" else 0)
              for r in conn.execute("select canonical, up_stat, down_stat from natures")}
    out = {name.lower(): (name, e) for name, e in effect.items()}
    for alias, canonical in conn.execute(
            "select alias, canonical from aliases where kind = 'nature'"):
        if alias and canonical in effect:
            out.setdefault(str(alias).strip().lower(), (canonical, effect[canonical]))
    return out


def _speed_share(dist: list[list[int]], s: int) -> tuple[str, list[int]]:
    """An opponent's most-used-set speed `s` against the asked pokemon's observed builds
    ([[speed, rows]...]): its label and [% of those builds it outspeeds, % it ties]."""
    n = sum(k for _v, k in dist)
    below = sum(k for v, k in dist if v < s)
    tie = sum(k for v, k in dist if v == s)
    above = n - below - tie
    if tie >= SPEED_USUAL_TIE * n:
        label = "usually ties"
    elif below >= SPEED_RELIABLE * n:
        label = "reliably faster"
    elif below >= SPEED_USUAL * n:
        label = "usually faster"
    elif above >= SPEED_RELIABLE * n:
        label = "reliably slower"
    elif above >= SPEED_USUAL * n:
        label = "usually slower"
    else:
        label = "depends on build"
    return label, [round(100 * below / n), round(100 * tie / n)]


def _range_relation(r: list | None, mine: list | None) -> str:
    """Opt-in range comparison: an opponent's [slowest, fastest] against the asked pokemon's."""
    if not r or not mine:
        return "no range"
    if r[0] > mine[1]:
        return "whole range faster"
    if r[1] < mine[0]:
        return "whole range slower"
    if r[0] == r[1] == mine[0] == mine[1]:
        return "always ties"
    if r[0] == mine[1]:
        return "faster or tie"
    if r[1] == mine[0]:
        return "slower or tie"
    return "ranges overlap"


def _team_session(pool: Any, ops: list[dict], timeout: float) -> list:
    """Run team ops in ONE team.py process: the library load and sibling skills are paid once."""
    with tempfile.TemporaryDirectory(prefix="pcweb-qa-") as tmp:
        spec = os.path.join(tmp, "session.json")
        with open(spec, "w", encoding="utf-8") as f:
            json.dump(ops, f, ensure_ascii=False)
        out = pool.request_json("team", ["session", spec], None, timeout)
    return out if isinstance(out, list) else []


def _op_result(entry: Any, key: str) -> dict | None:
    doc = entry.get("result") if isinstance(entry, dict) and entry.get("rc") == 0 else None
    return doc if isinstance(doc, dict) and not doc.get("error") and isinstance(
        doc.get(key), list) else None


def _envelope(species: str, results: dict, cards: dict, effects: dict) -> dict | None:
    """Every real-team archetype of `species` across its library forms, each in its battle form, as
    envelope points. Singles files a registered Mega under its own name, so those pools are added;
    doubles stores base + stone, which repset answers by narrowing the base pool (`item_filter`), so
    those rows are already in it. None when any read failed (not definitive)."""
    stones = _stones(cards.get(species))
    clusters, pool_rows = [], 0
    for query in [species, *stones.values()]:
        doc = _op_result(results.get(("repset", query)), "archetypes")
        if doc is None:
            return None
        if query != species and (doc.get("query") or {}).get("item_filter"):
            continue
        arches = [a for a in doc["archetypes"] if isinstance(a, dict)]
        pool_rows += int(arches[0].get("species_sample") or 0) if arches else 0
        for a in arches:
            prof = a.get("speed_profile") or {}
            lo, hi, n = prof.get("min_spe_sp"), prof.get("max_spe_sp"), prof.get("sample")
            if not (isinstance(lo, int) and isinstance(hi, int) and isinstance(n, int) and n > 0):
                continue
            item = (a.get("cluster") or {}).get("item") or None
            form = query if query != species else stones.get(item or "", species)
            hits = [effects.get(str(raw).strip().lower()) for raw in prof.get("natures") or []]
            classes = {h[1] for h in hits if h} or {-1, 1}     # no usable nature: both extremes
            clusters.append({"form": form, "n": n, "unresolved": hits.count(None),
                             "rows": float(a.get("coverage") or 0) * int(a.get("species_sample") or 0),
                             "lo": (form, _SPEED_NATURE[min(classes)], lo, item),
                             "hi": (form, _SPEED_NATURE[max(classes)], hi, item)})
    return {"clusters": clusters, "pool": pool_rows}


def _observed(doc: Any, form: str, base: str, cards: dict, effects: dict) -> dict | None:
    """The asked form's real-team rows that record a Spe SP, as (nature, spe SP, item) -> rows, from a
    `team search` answer. None for anything but a well-formed answer."""
    if not (isinstance(doc, dict) and not doc.get("error") and doc.get("ok") is not False
            and isinstance(doc.get("teams"), list)):
        return None
    combos: Counter = Counter()
    unresolved = 0
    for team in doc["teams"]:
        for m in (team.get("pokemon") or []) if isinstance(team, dict) else []:
            sp, item = (m.get("species"), m.get("item") or None) if isinstance(m, dict) else (None, None)
            if sp not in (form, base):
                continue
            row_form = sp if (cards.get(sp) or {}).get("is_mega") else _stones(
                cards.get(sp)).get(item or "", sp)
            spe = (m.get("spread") or {}).get("spe")
            if row_form != form or not isinstance(spe, int):
                continue
            hit = effects.get(str(m.get("nature") or "").strip().lower())
            if hit is None:
                unresolved += 1
            else:
                combos[(hit[0], spe, item)] += 1
    return {"combos": combos, "unresolved": unresolved, "read": len(doc["teams"]),
            "matched": max(len(doc["teams"]), int(doc.get("match_count") or 0))}


def _speed_points(pool: Any, points: dict[tuple, int], timeout: float) -> list:
    """Final speed of every (form, nature, spe SP, item) point, one speedline batch; None per failure."""
    if not points:
        return []
    items = [{"name": form, "nature": nature, "sps": {"spe": spe}, **({"item": item} if item else {})}
             for (form, nature, spe, item) in points]
    try:
        doc = pool.request_json("speedline", ["batch"], json.dumps(items, ensure_ascii=False),
                                timeout=timeout)
    except Exception:
        return [None] * len(points)
    doc = doc if isinstance(doc, list) else []
    return [r.get("finalSpeed") if isinstance(r, dict) and not r.get("error") else None
            for r in doc] + [None] * (len(points) - len(doc))


def _envelope_record(clusters: list[dict], pool_rows: int, speeds: list,
                     points: dict) -> dict | None:
    n = sum(c["n"] for c in clusters)
    rec: dict[str, Any] = {"n": n}
    if pool_rows:
        rec["cov"] = round(100 * sum(c["rows"] for c in clusters) / pool_rows)
    if sum(c["unresolved"] for c in clusters):
        rec["unresolved"] = sum(c["unresolved"] for c in clusters)
    if n < SPEED_RANGE_MIN_ROWS:
        return rec
    lows = [speeds[points[c["lo"]]] for c in clusters]
    highs = [speeds[points[c["hi"]]] for c in clusters]
    if None in lows or None in highs:
        return None
    rec["r"] = [min(lows), max(highs)]
    return rec


def _asked_distribution(pool: Any, fmt: str, form: str, cards: dict, timeout: float,
                        read_timeout: float, snapshot: str) -> dict | None:
    """The asked form's observed real-team speeds through the snapshot cache: one `team search` (rows
    of that form only, the stone narrowing a Mega's read) and one speedline batch over its distinct
    builds. Record {n: rows with a Spe SP, read/matched: teams, unresolved?, dist?: [[speed, rows]...]
    ascending, present from SPEED_RANGE_MIN_ROWS rows}. None when a read failed; nothing is cached."""
    key = (snapshot, fmt, "speed-dist-of", form)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    card = cards.get(form) or {}
    base = str(card.get("base_species") or form) if card.get("is_mega") else form
    stone = card.get("required_item") if card.get("is_mega") else None
    query = json.dumps([{"species": form, **({"item": stone} if stone else {})}], ensure_ascii=False)
    try:
        doc = pool.request_json("team", ["search", "--query", query, "--game-format", fmt, "--limit",
                                         str(SPEED_JOINT_TEAM_LIMIT), "--format", "json"],
                                None, read_timeout)
    except Exception:
        return None
    observed = _observed(doc, form, base, cards, _nature_speed_effects())
    if observed is None:
        return None
    combos = observed["combos"]
    rec: dict[str, Any] = {"n": sum(combos.values()), "read": observed["read"],
                           "matched": observed["matched"]}
    if observed["unresolved"]:
        rec["unresolved"] = observed["unresolved"]
    if rec["n"] >= SPEED_RANGE_MIN_ROWS:
        points = {(form, *combo): i for i, combo in enumerate(combos)}
        speeds = _speed_points(pool, points, timeout)
        by_speed: Counter = Counter()
        for combo, k in combos.items():
            by_speed[speeds[points[(form, *combo)]]] += k
        if None in by_speed:
            return None
        rec["dist"] = sorted([s, k] for s, k in by_speed.items())
    return _cache_put(key, rec)


def _field_ranges(pool: Any, fmt: str, field: list[str], cards: dict, timeout: float,
                  session_timeout: float, snapshot: str) -> dict[str, dict | None]:
    """Opt-in: {field species: envelope record} through the snapshot cache. Misses cost ONE team session
    (repset per library form of every missing species) and ONE speedline batch; a failed read caches
    nothing. Record {r?: [slowest, fastest], n: rows with a Spe SP, cov?: % of the species' real-team
    rows in the archetypes used, unresolved?}."""
    found = {sp: _cache_get((snapshot, fmt, "speed-range", sp)) for sp in field}
    missing = [sp for sp in field if found[sp] is None]
    if not missing:
        return found
    ops: list[dict] = []
    keys: list[tuple[str, str]] = []
    for sp in missing:
        for q in [sp, *_stones(cards.get(sp)).values()]:
            if ("repset", q) not in keys:
                keys.append(("repset", q))
                ops.append({"op": "repset", "species": q, "game_format": fmt,
                            "max_clusters": SPEED_RANGE_CLUSTERS})
    try:
        results = dict(zip(keys, _team_session(pool, ops, session_timeout)))
    except Exception:
        return found
    effects = _nature_speed_effects()
    envelopes = {sp: _envelope(sp, results, cards, effects) for sp in missing}
    points: dict[tuple, int] = {}
    for env in envelopes.values():
        for c in (env or {}).get("clusters", []):
            points.setdefault(c["lo"], len(points))
            points.setdefault(c["hi"], len(points))
    speeds = _speed_points(pool, points, timeout)
    for sp, env in envelopes.items():
        rec = _envelope_record(env["clusters"], env["pool"], speeds, points) if env else None
        if rec is not None:
            found[sp] = _cache_put((snapshot, fmt, "speed-range", sp), rec)
    return found


def _ladder_note(name: str, has_dist: bool, opponent_ranges: bool) -> str:
    """What every field of the ladder means — the model reads this, so it must be exact."""
    parts = ["s = final speed of an opponent's most-used set (set = nature, Spe SP, item; a Mega Stone "
             "holder as its Mega form, once Mega Evolved); finalSpeed/set = mine. outspeedsMe/ties/"
             "slowerThanMe compare the most-used sets; a tie is NOT faster."]
    if has_dist:
        rel, usual, tie = (f"{SPEED_RELIABLE:.0%}", f"{SPEED_USUAL:.0%}", f"{SPEED_USUAL_TIE:.0%}")
        parts.append(
            f"speedRange = {name}'s final speeds over its observed real-team builds (rows recording "
            f"nature + Spe SP + item; common = [speed, rows]). p = [% of those {name} builds this s "
            f"outspeeds, % it ties]; the rest outspeed it. rel: 'reliably faster' = this most-used set "
            f"outspeeds >={rel} of the observed {name} builds; 'usually faster' >={usual}; 'reliably "
            f"slower'/'usually slower' = >={rel}/>={usual} of them outspeed it; 'usually ties' = "
            f">={tie} tie; otherwise 'depends on build'. Opponents' own builds vary"
            + (" and are ranged in r, separately." if opponent_ranges
               else " and are not ranged unless opponent_ranges is set."))
    else:
        parts.append("rel = 'modal only': with no observed distribution of mine (speedRange says why), "
                     "only the most-used sets are compared.")
    if opponent_ranges:
        parts.append("r = an opponent's [slowest, fastest] over its real-team builds: per item/ability "
                     "archetype, its lowest Spe SP with its slowest nature up to its highest with its "
                     "fastest, each in its battle form, so possibly wider than any team plays; s comes "
                     "from usage data and can sit outside r; cov = % of its real-team rows those "
                     "archetypes hold. rr compares r with speedRange: whole range faster/slower, "
                     "faster or tie, slower or tie, ranges overlap, no range.")
    parts.append("Not modelled: Tailwind, Trick Room, weather/terrain/Unburden speed abilities, "
                 "paralysis, stat boosts.")
    return " ".join(parts)


def _set_text(nature: Any, spe: Any, item: Any) -> str:
    return " ".join(str(x) for x in (nature or "neutral", spe or 0, item) if x is not None)


def _ladder_doc(fmt: str, rows: list[tuple[str, dict, str]], asked: dict | None,
                ranges: dict[str, dict | None], opponent_ranges: bool = False) -> dict:
    """The model-facing ladder: the asked pokemon's most-used set and observed speed distribution, then
    the field split by most-used-set speed, each row labelled by the share of the asked pokemon's
    observed builds it outspeeds (and, opted in, its own range). Past the tool-result budget the
    slowest rows keep only name and speed (`moreSlower`)."""
    _sp, mine, mine_src = rows[0]
    dist = (asked or {}).get("dist")
    mine_r = [dist[0][0], dist[-1][0]] if dist else None
    if dist:
        speed_range: dict[str, Any] = {
            "min": mine_r[0], "max": mine_r[1], "rows": asked["n"],
            "common": [[v, k] for v, k in sorted(dist, key=lambda e: (-e[1], -e[0]))[:3]]}
        if asked["read"] < asked["matched"]:
            speed_range["teams"] = (f"first {asked['read']} of {asked['matched']} matching teams "
                                    f"read (older season first)")
        if asked.get("unresolved"):
            speed_range["droppedNatures"] = asked["unresolved"]
    elif asked is not None:
        speed_range = {"none": f"only {asked['n']} real-team rows of {mine['n']} record a Spe SP "
                               f"(a distribution needs {SPEED_RANGE_MIN_ROWS})"}
    else:
        speed_range = {"none": "real-team speed data unavailable for this answer"}
    field = []
    for sp, row, src in rows[1:]:
        if row.get("s") is None:
            continue
        row = dict(row)
        if dist:
            row["rel"], row["p"] = _speed_share(dist, row["s"])
        else:
            row["rel"] = "modal only"
        if opponent_ranges:
            rng = ranges.get(sp) or {}
            if rng.get("r"):
                row["r"] = rng["r"]
            if rng.get("cov") is not None:
                row["cov"] = rng["cov"]
            row["rr"] = _range_relation(rng.get("r"), mine_r)
        if src != "most-used set (usage modes)":
            row["b"] = "real-team" if src.startswith("real-team") else "default"
        field.append(row)
    field.sort(key=lambda r: -r["s"])
    base = mine.get("s")
    out: dict[str, Any] = {
        "pokemon": mine["n"], "format": fmt, "finalSpeed": base, "set": mine["set"],
        "basis": mine_src, "speedRange": speed_range,
        "note": _ladder_note(mine["n"], bool(dist), opponent_ranges),
        "outspeedsMe": [r for r in field if base is not None and r["s"] > base],
        "ties": [r for r in field if base is not None and r["s"] == base],
        "slowerThanMe": [r for r in field if base is not None and r["s"] < base],
    }

    def fits() -> bool:
        return len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8")) <= QA_TOOL_RESULT_CHARS

    if not fits():
        out["note"] += (" moreSlower = further members slower at s, as 'name s'; their detail is "
                        "dropped for space.")
        out["moreSlower"] = []
        while not fits() and len(out["slowerThanMe"]) > 3:
            r = out["slowerThanMe"].pop()
            out["moreSlower"].insert(0, f"{r['n']} {r['s']}")
    return out


_STAT_BOUND = re.compile(r"(hp|atk|def|spa|spd|spe)(>=|<=|>|<|=)(\d{1,3})")
# A full list is handed to the model only while it stays well inside the answer budget
# (QA_MAX_ANSWER_TOKENS): an English canonical name costs one to three tokens, so sixty names plus
# the sentence around them is a few hundred. Past that the model gets the count and a sample, and is
# told to say so — a longer list would be cut off mid-name by the token cap, not finished.
DEX_FIND_FULL_LIST_MAX = 60
DEX_FIND_SAMPLE = 40
DEX_FIND_MEGA_SAMPLE = 10


def _dex_find(pool: Any, args: dict, timeout: float) -> tuple[tuple[str, str, dict] | None, str]:
    """Reverse search: all conditions at once, one dex call. Names are resolved first (typo
    tolerant); `find` itself is exact. The answer is a name list, which the generic clip would cut
    at twelve entries — so it is built compactly here, base forms and Mega forms apart."""
    def names(value: Any, cap: int) -> list[str]:
        items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
        return [n for n in (_clean_name(item) for item in items[:cap]) if n]

    moves = [_resolve_query(pool, move, "move", timeout) for move in names(args.get("moves"), 4)]
    abilities = [_resolve_query(pool, a, "ability", timeout) for a in names(args.get("ability"), 1)]
    types = names(args.get("types"), 2)
    stats = [str(bound).replace(" ", "").lower() for bound in (args.get("stats") or [])[:3]
             if isinstance(bound, str)]
    if any(not _STAT_BOUND.fullmatch(bound) for bound in stats):
        return None, json.dumps({"error": "stats must look like 'spe>=100'"})
    conditions = ([("move", m) for m in moves] + [("type", t) for t in types]
                  + [("ability", a) for a in abilities] + [("stat", b) for b in stats])
    if not conditions:
        return None, json.dumps({"error": "dex_find needs at least one condition"})
    argv = ["find"]
    for field, value in conditions:
        argv += [field, value]
    doc = pool.request_json("dex", [*argv, "--format", "json"], timeout=timeout)
    results = doc.get("results") if isinstance(doc, dict) else None
    label = " + ".join(value for _field, value in conditions)
    trace = ("dex.find", f"dex find {label}", {"conditions": label})
    if not isinstance(results, list):
        return trace, clip_json(doc)
    found = [r for r in results if isinstance(r, dict) and r.get("name")]
    base = [r["name"] for r in found if not r.get("is_mega")]
    mega = [r["name"] for r in found if r.get("is_mega")]
    out: dict[str, Any] = {"conditions": [f"{f} {v}" for f, v in conditions], "count": len(found)}
    if len(found) <= DEX_FIND_FULL_LIST_MAX:
        out.update({"pokemon": base, "megaForms": mega})
    else:
        out.update({"pokemonSample": base[:DEX_FIND_SAMPLE], "megaFormsSample": mega[:DEX_FIND_MEGA_SAMPLE],
                    "note": f"{len(found)} matches are too many to list in one answer: give the total, "
                            "name a few examples from the sample, and suggest narrowing the search "
                            "by type, ability or a base-stat bound."})
    unresolved = doc.get("unresolved_conditions") if isinstance(doc, dict) else None
    if unresolved:
        out["unresolved"] = unresolved
    return trace, json.dumps(out, ensure_ascii=False)


# Usage results reach the model with ENGLISH CANONICAL names only, in a compact shape. A zh/ja
# name in a tool result comes back in the answer as "Sitrus Berry (文柚果)", and the page — which
# localizes every canonical itself (design §2.1) — then shows the same item twice. Compact also
# means every panel fits the byte budget: the raw detail is ~6 kB, so the generic clip silently
# dropped partners and SP spreads, and the generic list cap cut a top-50 ranking to 12 rows.
DETAIL_DEPTH = {"moves": 10, "items": 8, "abilities": 3, "natures": 5, "partners": 10,
                "spreads": 5}
_LOCAL_NAME_KEYS = frozenset(("display_name", "display_name_ja", "name_zh", "name_ja",
                              "nameZh", "nameJa"))


def _english_only(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _english_only(v) for k, v in node.items() if k not in _LOCAL_NAME_KEYS}
    if isinstance(node, list):
        return [_english_only(v) for v in node]
    return node


def _compact_detail(raw: Any) -> Any:
    from .. import mappers
    if not isinstance(raw, dict) or raw.get("error") or "panels" not in raw:
        return raw
    try:
        dto = mappers.map_detail(raw)
    except Exception:
        return _english_only(raw)
    panels = dto.get("panels") or {}
    out: dict[str, Any] = {"pokemon": dto.get("name"), "format": dto.get("format"),
                           "season": dto.get("season"), "rule": dto.get("rule"),
                           "rank": dto.get("rank")}
    for panel, depth in DETAIL_DEPTH.items():
        rows = (panels.get(panel) or [])[:depth]
        if panel == "partners":
            out[panel] = [r.get("name") for r in rows]
        elif panel == "spreads":
            out[panel] = [{"sps": r.get("spread"), "pct": r.get("percentage")} for r in rows]
        else:
            out[panel] = [{"name": r.get("name"), "pct": r.get("percentage")} for r in rows]
    out["partners_order"] = "most frequent teammate first; no share is published"
    return out


def _compact_ranking(raw: Any) -> Any:
    if not isinstance(raw, dict) or not isinstance(raw.get("rows"), list):
        return raw
    return {"season": raw.get("season"), "rule": raw.get("rule"), "format": raw.get("format"),
            "rows": [{"rank": r.get("rank"), "name": r.get("pokemon_en") or r.get("name")}
                     for r in raw["rows"] if isinstance(r, dict)]}


# A miss must END the search for that entity. Without saying why it missed, models spend every
# remaining tool round re-asking under other spellings and languages (seen live: Flutter Mane in
# en/zh/ja, a Mega form under three names) and reach the forced-final round still wanting a tool.
NOT_IN_DEX = ("not in the Champions battle dex, so not available under the current ruleset; the "
              "lookup already accepts zh/ja/en names and minor typos, so other spellings will "
              "not find it")
NO_USAGE = ("in the battle dex but has no usage data in this format this season (outside the "
            "tracked ranking)")


def _not_found(doc: Any) -> bool:
    return (isinstance(doc, dict) and doc.get("ok") is False
            and (doc.get("error") or {}).get("code") == "not_found")


def _meta_detail(pool: Any, fmt: str, name: str, timeout: float) -> Any:
    """Usage detail, or a miss that says which kind of miss it is. The meta keys a Mega form by
    its BASE species (whose page carries the Mega-stone sets), so a Mega query is answered from
    the base species' page and says so."""
    doc = pool.request_json("meta", ["detail", "--format", fmt, "--pokemon", name,
                                     "--output", "json"], timeout=timeout)
    if not _not_found(doc):
        return _compact_detail(doc)
    try:
        card = pool.request_json("dex", ["pokemon", name, "--format", "json"], timeout=timeout)
    except Exception:
        return doc
    if not isinstance(card, dict):
        return doc
    if _not_found(card):
        return {"note": NOT_IN_DEX, **doc}
    if card.get("error") or card.get("ok") is False:
        # A refused name is not an identified species with missing usage. Preserve the dex's
        # ambiguity and candidates instead of teaching the model a false absence of data.
        return _english_only(card)
    base = card.get("base_species")
    if card.get("is_mega") and isinstance(base, str) and base and base != name:
        base_doc = pool.request_json("meta", ["detail", "--format", fmt, "--pokemon", base,
                                              "--output", "json"], timeout=timeout)
        if isinstance(base_doc, dict) and not base_doc.get("error"):
            return {"note": f"the metagame records {name} under its base species {base}: this "
                            f"usage data is {base}'s, Mega-stone sets included",
                    **_compact_detail(base_doc)}
    return {"note": NO_USAGE, **doc}


def _run_tool(pool: Any, name: str, args: dict,
              timeout: float = QA_TOOL_TIMEOUT,
              budget: float | None = None, supplied_codes: frozenset[str] = frozenset(),
              consume_team_code: Callable[[], None] | None = None) -> tuple[tuple[str, str, dict] | None, str]:
    """Execute one whitelisted tool call. Returns ((tool_id, label, params) or None, content).
    Bad arguments and CLI error shapes become CONTENT (the model reads the miss and says
    so); only infrastructure faults bubble up via WorkerError -> content too. `timeout` is
    the deadline-bounded budget for the worker call (design §7.2); `budget` is the pipeline's
    whole remaining wall clock, which bounds the longer-running repset fallback."""
    timeout = max(1.0, timeout)
    try:
        if name == "team_code_lookup":
            from ..team_code import skill
            service = skill()
            try:
                code = service.normalize_code(args.get("code"))
                if len(supplied_codes) != 1 or code not in supplied_codes:
                    return None, json.dumps({"error": "only query a public code explicitly supplied by the user"})
                if consume_team_code is not None:
                    consume_team_code()
                doc = service.lookup(code, network=(budget is not None and budget >= 80))
                return ("team.code", "public team " + code, {"code": code}), clip_json(doc)
            except service.TeamCodeError as exc:
                return None, json.dumps({"error": {"code": exc.code, "message": str(exc)}})
        if name == "dex_lookup":
            kind = args.get("kind")
            q = _clean_name(args.get("name"))
            if kind not in DEX_KINDS or q is None:
                return None, json.dumps({"error": "bad dex_lookup arguments"})
            q = _resolve_query(pool, q, kind, timeout)
            doc = pool.request_json("dex", [kind, q, "--format", "json"], timeout=timeout)
            if kind == "pokemon" and _not_found(doc):
                doc = {"note": NOT_IN_DEX, **doc}
            return (f"dex.{kind}", f"dex {kind} {q}", {"kind": kind, "name": q}), \
                clip_json(_english_only(doc))
        if name == "dex_find":
            return _dex_find(pool, args, timeout)
        if name == "meta_ranking":
            fmt = args.get("format")
            if fmt not in ("single", "double"):
                return None, json.dumps({"error": "bad meta_ranking arguments"})
            limit = max(1, min(int(args.get("limit") or 30), 50))
            doc = pool.request_json("meta", ["ranking", "--format", fmt, "--limit",
                                             str(limit), "--output", "json"], timeout=timeout)
            return ("meta.ranking", f"meta ranking {fmt} top{limit}",
                    {"format": fmt, "limit": limit}), \
                clip_json(_compact_ranking(doc), cap=QA_TOOL_RESULT_CHARS * 2)
        if name == "meta_detail":
            fmt = args.get("format")
            q = _clean_name(args.get("pokemon"))
            if fmt not in ("single", "double") or q is None:
                return None, json.dumps({"error": "bad meta_detail arguments"})
            q = _resolve_query(pool, q, "pokemon", timeout)
            doc = _meta_detail(pool, fmt, q, timeout)
            return ("meta.detail", f"meta detail {fmt} {q}",
                    {"format": fmt, "pokemon": q}), clip_json(doc)
        if name == "repset_lookup":
            fmt = args.get("format") or "single"
            q = _clean_name(args.get("pokemon"))
            if fmt not in ("single", "double") or q is None:
                return None, json.dumps({"error": "bad repset_lookup arguments"})
            q = _resolve_query(pool, q, "pokemon", timeout)
            doc = pool.request_json("team", ["repset", q, "--game-format", fmt,
                                             "--format", "json"],
                                    timeout=_repset_budget(timeout, budget))
            arches = []
            for a in (doc.get("archetypes") or [])[:2] if isinstance(doc, dict) else []:
                if isinstance(a, dict):
                    arches.append({"species": a.get("species"), "ability": a.get("ability"),
                                   "item": a.get("item"), "nature": a.get("nature"),
                                   "moves": a.get("moves"),
                                   "sps": spread_from_sps(a.get("sps")),
                                   "share": a.get("share"),
                                   "confidence": a.get("confidence")})
            out: dict[str, Any] = {"resolved": (doc.get("query") or {}).get("resolved")
                                   if isinstance(doc, dict) else None,
                                   "format": fmt, "archetypes": arches}
            if not arches:
                out["note"] = "no real-team sample for this pokemon in this format"
            return ("team.repset", f"repset {fmt} {q}",
                    {"format": fmt, "pokemon": q}), clip_json(out)
        if name == "speed_ladder":
            fmt = args.get("format") or "single"
            who = _clean_name(args.get("pokemon"))
            if fmt not in ("single", "double") or who is None:
                return None, json.dumps({"error": "bad speed_ladder arguments"})
            try:
                limit = max(1, min(30, int(args.get("limit") or 20)))
            except (TypeError, ValueError):
                limit = 20
            opponent_ranges = args.get("opponent_ranges") in (True, "true")
            from .. import mappers      # local import, same as the other calc branches: `mappers`
            #                                is function-local here, so using it before this line
            #                                is an UnboundLocalError, not a NameError.
            who = _resolve_query(pool, who, "pokemon", timeout)
            rank_doc = pool.request_json(
                "meta", ["ranking", "--format", fmt, "--limit", str(limit), "--output", "json"],
                timeout=timeout)
            names = [r.get("pokemon_en") or r.get("pokemon") or r.get("slug")
                     for r in ((rank_doc or {}).get("rows") or [])]
            names = [n for n in names if n]
            # `who` first so its row is unambiguous even when it is itself in the ranking.
            ordered = [who] + [n for n in names if n != who]
            # ONE resolution pass + ONE speedline batch for the whole field: looping calc_speed over
            # a ranking is what made this class of question run out of tool rounds before it had
            # enough of the field to answer.
            started = time.monotonic()
            snapshot = _modal_snapshot(pool)
            modal = [_modal_combatant(pool, fmt, n, timeout, budget, snapshot) for n in ordered]
            # Two dex batches serve the battle forms, their abilities and the range reads: every name,
            # then the Mega forms those can become and an asked Mega's base species.
            cards = _dex_cards(pool, set(ordered) | {c["name"] for c, _ in modal}, timeout)
            more = {f for c in cards.values() for f in _stones(c).values()}
            more |= {str(c["base_species"]) for c in cards.values()
                     if c.get("is_mega") and c.get("base_species")}
            cards.update(_dex_cards(pool, more - set(cards), timeout))
            sides = _battle_forms(pool, modal, timeout, cards)
            # Asked as "Mega Gengar", the ranked "Gengar @ Gengarite" is the same pokemon, not an
            # opponent that ties with it.
            entries = [(ordered[0], sides[0])] + [
                (sp, s) for sp, s in zip(ordered[1:], sides[1:]) if s[0]["name"] != sides[0][0]["name"]]
            sides = [s for _sp, s in entries]
            items = []
            for combatant, _src in sides:
                it: dict[str, Any] = {"name": combatant["name"]}
                if combatant.get("nature"):
                    it["nature"] = combatant["nature"]
                spe = (combatant.get("sps") or {}).get("spe")
                if spe is not None:
                    it["sps"] = {"spe": spe}
                for k in ("item", "ability"):
                    if combatant.get(k):
                        it[k] = combatant[k]
                items.append(it)
            doc = pool.request_json("speedline", ["batch"],
                                    json.dumps(items, ensure_ascii=False),
                                    timeout=max(timeout, 45.0) if budget is None
                                    else max(1.0, min(max(timeout, 45.0), budget)))
            rows = []
            for i, ((sp, (combatant, src)), raw) in enumerate(
                    zip(entries, doc if isinstance(doc, list) else [])):
                if isinstance(raw, dict) and not raw.get("error"):
                    m = mappers.map_speedline(raw)
                    rows.append((sp, {"n": m["name"], "s": m["finalSpeed"],
                                      "set": _set_text(m["nature"], m["speedSPs"],
                                                       combatant.get("item"))}, src))
                elif i == 0:
                    break       # without the asked row, rows[0] would be an opponent posing as it
            if not rows:
                return ("speed_ladder", f"speed ladder: {who} ({fmt})", {"pokemon": who}),                     json.dumps({"error": "no speed could be computed"})
            def read_timeout() -> float:
                return SPEED_RANGE_TIMEOUT if budget is None else min(
                    SPEED_RANGE_TIMEOUT, (budget - (time.monotonic() - started)) / 2)

            asked = None if read_timeout() < 5 else _asked_distribution(
                pool, fmt, sides[0][0]["name"], cards, timeout, read_timeout(), snapshot)
            ranges: dict[str, dict | None] = {}
            if opponent_ranges and read_timeout() >= 5:
                ranges = _field_ranges(pool, fmt, [sp for sp, _r, _s in rows[1:]], cards, timeout,
                                       read_timeout(), snapshot)
            out = _ladder_doc(fmt, rows, asked, ranges, opponent_ranges)
            params = {"pokemon": out["pokemon"], "format": fmt, "limit": limit}
            if opponent_ranges:
                params["opponent_ranges"] = True
            return ("speed_ladder", f"speed ladder: {out['pokemon']} ({fmt})", params), \
                clip_json(out)
        if name == "calc_damage":
            from .. import mappers
            fmt = args.get("format") or "single"
            atk_n = _clean_name(args.get("attacker"))
            dfd_n = _clean_name(args.get("defender"))
            mv = _clean_name(args.get("move"))
            if fmt not in ("single", "double") or None in (atk_n, dfd_n, mv):
                return None, json.dumps({"error": "bad calc_damage arguments"})
            atk_n = _resolve_query(pool, atk_n, "pokemon", timeout)
            dfd_n = _resolve_query(pool, dfd_n, "pokemon", timeout)
            mv = _resolve_query(pool, mv, "move", timeout)
            snapshot = _modal_snapshot(pool)
            atk, src_a = _modal_combatant(pool, fmt, atk_n, timeout, budget, snapshot)
            dfd, src_d = _modal_combatant(pool, fmt, dfd_n, timeout, budget, snapshot)
            for override, side in ((args.get("attacker_item"), atk),
                                   (args.get("defender_item"), dfd)):
                item = _clean_name(override) if override else None
                if item:
                    side["item"] = _resolve_query(pool, item, "item", timeout)
                    side["_item_overridden"] = True
            # After the overrides: the item a side finally holds decides its battle form (a Specs
            # override keeps Gengar base; a stone override makes the Mega).
            (atk, src_a), (dfd, src_d) = _battle_forms(pool, [(atk, src_a), (dfd, src_d)], timeout)
            payload = {"attacker": {k: v for k, v in atk.items() if not k.startswith("_")},
                       "defender": {k: v for k, v in dfd.items() if not k.startswith("_")},
                       "move": mv, "field": {"format": fmt}}
            doc = pool.request_json("calc", ["one"],
                                    json.dumps(payload, ensure_ascii=False), timeout=timeout)
            label = ("calc.damage", f"calc {atk['name']} {mv} vs {dfd['name']}",
                     {"attacker": atk["name"], "move": mv, "defender": dfd["name"],
                      "format": fmt})
            if isinstance(doc, dict) and doc.get("error"):
                return label, clip_json(doc)   # unknown move/mon: an honest miss
            mapped = mappers.map_damage(doc)
            result = {"description": mapped.get("description"),
                      "koChance": mapped.get("koChance"),
                      "minPercent": mapped.get("minPercent"),
                      "maxPercent": mapped.get("maxPercent"),
                      "hits": mapped.get("hits"),
                      "assumedSets": {
                          "attacker": {**payload["attacker"], "basis": src_a},
                          "defender": {**payload["defender"], "basis": src_d}}}
            return label, clip_json(result)
        if name == "calc_speed":
            from .. import mappers
            fmt = args.get("format") or "single"
            a_n = _clean_name(args.get("pokemon_a"))
            b_n = _clean_name(args.get("pokemon_b"))
            if fmt not in ("single", "double") or None in (a_n, b_n):
                return None, json.dumps({"error": "bad calc_speed arguments"})
            a_n = _resolve_query(pool, a_n, "pokemon", timeout)
            b_n = _resolve_query(pool, b_n, "pokemon", timeout)
            snapshot = _modal_snapshot(pool)
            sides = _battle_forms(pool, [_modal_combatant(pool, fmt, n, timeout, budget, snapshot)
                                         for n in (a_n, b_n)], timeout)
            items = []
            for combatant, _src in sides:
                item: dict[str, Any] = {"name": combatant["name"]}
                if combatant.get("nature"):
                    item["nature"] = combatant["nature"]
                spe = (combatant.get("sps") or {}).get("spe")
                if spe is not None:
                    item["sps"] = {"spe": spe}
                for k in ("item", "ability"):
                    if combatant.get(k):
                        item[k] = combatant[k]
                items.append(item)
            doc = pool.request_json("speedline", ["batch"],
                                    json.dumps(items, ensure_ascii=False), timeout=timeout)
            rows = []
            for (combatant, src), raw in zip(sides, doc if isinstance(doc, list) else []):
                if isinstance(raw, dict) and not raw.get("error"):
                    m = mappers.map_speedline(raw)
                    rows.append({"name": m["name"], "finalSpeed": m["finalSpeed"],
                                 "nature": m["nature"], "speSp": m["speedSPs"],
                                 "item": combatant.get("item"), "basis": src})
                else:
                    rows.append({"name": combatant["name"],
                                 "error": "speed could not be computed"})
            speeds = [r.get("finalSpeed") for r in rows]
            faster = None
            if len(speeds) == 2 and all(isinstance(s, (int, float)) for s in speeds):
                faster = ("tie" if speeds[0] == speeds[1]
                          else rows[0]["name"] if speeds[0] > speeds[1] else rows[1]["name"])
            return ("calc.speed", f"speed {a_n} vs {b_n}",
                    {"a": a_n, "b": b_n, "format": fmt}), \
                clip_json({"combatants": rows, "faster": faster})
        return None, json.dumps({"error": f"unknown tool {name!r}"})
    except Exception as e:  # WorkerError / JSON decode — the model can still answer honestly
        return None, json.dumps({"error": f"tool failed: {type(e).__name__}"})


DIAGNOSE_EXPLAIN_PROMPT = """\
You are the team analyst of a Pokemon Champions data site. You receive ONE team-diagnose
report as JSON facts: legality, weaknesses by attack type, offensive gaps, speed order,
role coverage, and per-opponent check grades against the top meta.

FIELD GUIDE for `checks.byOpponent` — read these EXACTLY as defined, never guess:
- observedFloor: the weakest team answer across every RETAINED OBSERVED build of that
  opponent. C2 = someone can switch in safely and win the 1v1; C1 = someone can beat it
  on even footing but cannot switch in; C0 = neither. This is an observed portfolio floor,
  NOT a fabricated max-speed or broad-move worst case.
- witnessVariantIds: the exact retained builds where observedFloor occurs. floorBy names
  the team members providing the strongest answer on those witness builds.
- representativeGrade: the answer against the highest-coverage retained build. It is useful
  context but never replaces a weaker observedFloor.
- representedCoverage: share of the real-team sample covered by retained builds. This is
  provenance, not probability of winning or a strength score. calculationComplete=false means
  at least one retained build lacks a calculation; do not claim a complete floor.
- A grade C1/C0 despite big damage numbers usually means the kill is not CERTAIN (e.g.
  the opponent's common Focus Sash, move accuracy, damage rolls) or my member cannot
  survive the return hit — respect the grade; do not overrule it with damage intuition.

Metagame calls (maintainer-set):
- Coverage entries carry a per-format `expectation` ATTENTION LEVEL, not a universal team
  requirement. `priority` means inspect an absence closely and raise it only when the other
  report facts show a concrete consequence. `situational` means it matters only to plans that
  call for it; a missing situational entry is normal and is filtered from this payload. If a
  role is not in the coverage list, do not invent a gap. In singles, anti-setup is a
  high-priority signal to inspect
  (hard answers to stat-boosting sweepers: phazing, Haze-class moves, Unaware/Imposter);
  Toxic/Yawn under `status` are only soft, partial substitutes.
- Coverage classes, named by the human label the payload prints: priority attack = endgame
  priority finishers (priority-attention in singles; situational in doubles, where Fake Out +
  speed control substitute); weather rewrite = the ability to overwrite the opponent's weather —
  a Mega form's ability counts (a Froslassite Froslass battles as Mega Froslass / Snow Warning,
  a real setter); disruption = Taunt/Encore class; damage mitigation = Intimidate/burn class
  (priority-attention in doubles); Fake Out = doubles opening pressure; terrain control = setting,
  overwriting or removing a terrain.
- Mega plan reading: two registered Mega stones are normal. If the report does not state a
  default Mega, do NOT invent one or infer what either form "solves" from model memory; say
  only that the battle selection must be made explicit when that ambiguity matters.
- A coverage entry with present=true means the team HAS that role — never call it missing,
  lacking, or "no hard answer": the bearers list names who provides it. Real teams do not
  double-invest in a role: never recommend ADDING a role that present=true already covers.
  If the single bearer looks exploitable, say the role rests on that one member and name
  the risk — that is a robustness note, not a missing role.

Write a SHORT interpretation (about 120 words, plain text, no markdown):
- the 2-3 most consequential problems in this report and WHY they matter,
- for each, what kind of change would address it (which member or slot to reconsider),
- grounded ONLY in the report facts — never invent numbers, matchups or members; when you
  cite a check grade, use observedFloor and name the opponent; name witness builds only when useful.
- the `team` list is the COMPLETE roster of MY team — advise ONLY those members. Every other
  Pokemon anywhere in the data (above all `checks.byOpponent[].opponent`) is an OPPONENT, never
  mine: never tell it to carry a move/item, never treat it as if it were on my team.
Name every Pokemon, move, item, ability and nature by its ENGLISH CANONICAL name exactly as
the report does (the site localizes names itself). Answer in the language given by [lang=..].

OUTPUT VOCABULARY (hard rule). Every field name, key path and enum value above describes the INPUT
JSON. They are internal identifiers and must NEVER appear in the answer: not observedFloor,
representativeGrade, witnessVariantIds, floorBy, representedCoverage, calculationComplete,
hardGaps, bearers, roles.coverage, present=true/false, nor any other snake_case, camelCase or
dotted token. Say what the field MEANS in the answer's language, or use the human label the
payload already carries. In a non-English answer the ONLY English permitted is a canonical entity
name (Pokemon / move / item / ability / nature), a type name, and the C2 / C1 / C0 grade codes the
site's own table prints — every other word must be written in the answer's language.
"""

DIAGNOSE_EXPLAIN_THINKING_PROFILE = """\

Thinking-mode output profile — this OVERRIDES the earlier 120-word/2-3-problem length and shape
only; every factual, roster and vocabulary rule above stays mandatory. The reader chose the long
reading: give them a real analyst's briefing, not a template.
- Length: about 700-1200 Chinese characters, 350-550 English words, or 850-1400 Japanese
  characters. Organise it as you judge best in 3-6 plain-text paragraphs. A paragraph may open with
  a short label of your own followed by a colon, and priorities may be a numbered list ("1. ...").
  No markdown syntax: no **, #, tables or "-" bullets.
- Explain how the team actually plays: its structure, likely leads and back lines, how members
  cover each other, and the win routes the supplied sets point to. You MAY use standard battle
  mechanics of the moves, abilities and items the team already carries (what Intimidate, Trick
  Room, Choice items, a weather or terrain does) to explain that plan.
- Connect 2-4 consequential risks across report sections. For an opponent row, use what the row
  states — the weakest answer found, which of my members answer it best, the grade against the
  representative build, how much of the real-team sample the observed builds cover, whether every
  build was calculated — in words, never by field name. The payload has no damage cells: never
  state or imply numbers it does not carry (damage, turns-to-KO, speed tiers, probabilities).
- Close with 2-4 priorities: what to preserve, what to select or lead into which opponents, and
  which CURRENT slot or role most deserves a second look and why.
- This is an evidence briefing, NOT a rebuild: never name a new Pokemon, move, item, ability,
  spread or type-based counter that the report does not contain — there is no legality authority
  for it. Describe the needed function and the current slot instead.
- Pokemon Champions has team selection but no ban phase. Say select/lead/preserve/position;
  never tell the user to ban an opponent.
- Entity spelling is non-negotiable even in Chinese/Japanese prose: write `Pelipper 的 Drizzle`,
  never translate Pelipper or Drizzle. The UI performs localization after generation.
"""

DIAGNOSE_EXPLAIN_THINKING_USER_CONTRACT = """\
FINAL OUTPUT CONTRACT: a thorough, specific reading in the thinking-mode length range, 3-6
plain-text paragraphs in whatever order serves this team; short paragraph labels and a numbered
priority list are fine, markdown is not. Mechanics of the team's own moves, abilities and items may
explain the plan. Never introduce or recommend a named entity absent from [report], never state a
number the report does not carry, never let an internal identifier (field name, key path,
snake_case or camelCase token, present=true) reach the prose, and never mention a ban phase. Use
English canonical entity spellings even inside Chinese or Japanese prose.
"""

DIAGNOSE_EXPLAIN_INPUT_BYTES = 28_000
DIAGNOSE_EXPLAIN_MAX_TOKENS = 650
# The thinking reading is two to three times the quick one, and reasoning tokens count against the
# same cap, so both the cap and the wall clock grow with it (the NDJSON heartbeat keeps the CDN open).
DIAGNOSE_EXPLAIN_THINKING_MAX_TOKENS = 9000
DIAGNOSE_EXPLAIN_THINKING_TIMEOUT = 150.0


# ---------------------------------------------------------------------------------------- #
# Public vocabulary for the diagnose reading.
#
# design §2.1: a localized answer keeps ENTITY names in English canonical and writes everything
# else in the interface language. A DTO field name, a role key, a JSON path or an enum value is
# an internal identifier — it is neither an entity nor a word of the answer's language, so it
# must never reach the prose. Two enforcement points share the tables below, because a prompt
# rule alone is not a guarantee: the PAYLOAD hands the model the same wording the page prints
# (an identifier it never sees, it cannot echo), and a POST-PASS rewrites whatever still leaks.
#
# Display authority for the role rows is frontend/web/src/pages/assist/messages.ts `diag.cov.*` — the reading sits
# on the same page as that checklist, so the two must name a role identically. A bridge test pins
# the key set to that file so a new role cannot be added on one side only.
# ---------------------------------------------------------------------------------------- #
_ROLE_TERMS: dict[str, dict[str, str]] = {
    "speed_control": {"zh": "速度控制", "ja": "素早さコントロール", "en": "speed control"},
    "priority_attack": {"zh": "先制攻击", "ja": "先制技", "en": "priority attack"},
    "anti_setup": {"zh": "反强化", "ja": "積み対策", "en": "anti-setup"},
    "protect": {"zh": "守住", "ja": "まもる", "en": "Protect"},
    "fake_out": {"zh": "击掌奇袭", "ja": "ねこだまし", "en": "Fake Out"},
    "spread": {"zh": "范围输出", "ja": "全体攻撃", "en": "spread damage"},
    "damage_mitigation": {"zh": "火力削弱", "ja": "火力削減", "en": "damage mitigation"},
    "redirection": {"zh": "攻击引导", "ja": "攻撃誘導", "en": "redirection"},
    "hazard_set": {"zh": "出钉", "ja": "設置技役", "en": "hazard setter"},
    "hazard_control": {"zh": "除钉", "ja": "設置技対策", "en": "hazard control"},
    "pivot": {"zh": "中转", "ja": "対面操作", "en": "pivot"},
    "recovery": {"zh": "回复", "ja": "回復", "en": "recovery"},
    "screens": {"zh": "墙类减伤", "ja": "壁", "en": "screens"},
    "disruption": {"zh": "干扰", "ja": "妨害", "en": "disruption"},
    "weather_rewrite": {"zh": "天气改写", "ja": "天候書き換え", "en": "weather rewrite"},
    "terrain_control": {"zh": "场地控制", "ja": "フィールド管理", "en": "terrain control"},
    "status": {"zh": "异常状态", "ja": "状態異常", "en": "status pressure"},
    "setup": {"zh": "强化手段", "ja": "積み技", "en": "setup"},
    "partner_support": {"zh": "同伴支援", "ja": "味方支援", "en": "partner support"},
    "side_protect": {"zh": "全体防护", "ja": "全体技対策", "en": "side protection"},
}
# Report fields the reading is expected to cite. The wording matches the page's own column
# headers so the prose and the table under it read as one document.
_FIELD_TERMS: dict[str, dict[str, str]] = {
    "observedFloor": {"zh": "观测下界", "ja": "観測下限", "en": "observed floor"},
    "witnessVariantIds": {"zh": "对应的实战配置", "ja": "該当する実型", "en": "witness builds"},
    "witness": {"zh": "对应的实战配置", "ja": "該当する実型"},
    "witnesses": {"zh": "对应的实战配置", "ja": "該当する実型"},
    "floorBy": {"zh": "提供应对的成员", "ja": "対応を担うメンバー",
                "en": "the members supplying that answer"},
    "representativeGrade": {"zh": "代表配置的应对等级", "ja": "代表型に対する評価",
                            "en": "the representative build's grade"},
    "representedCoverage": {"zh": "真实队伍样本覆盖率", "ja": "実チームサンプルの網羅率",
                            "en": "sample coverage"},
    "calculationComplete": {"zh": "计算完整性", "ja": "計算の完全性",
                            "en": "calculation completeness"},
    "usageRank": {"zh": "使用率排名", "ja": "使用率順位", "en": "usage rank"},
    "hardGaps": {"zh": "属性克制盲点", "ja": "範囲の盲点", "en": "coverage blind spots"},
    "stabTypes": {"zh": "本系打点", "ja": "タイプ一致の打点", "en": "STAB coverage"},
    "otherTypes": {"zh": "非本系打点", "ja": "タイプ不一致の打点", "en": "off-STAB coverage"},
    "byAttackType": {"zh": "按攻击属性的防守弱点", "ja": "攻撃タイプ別の弱点",
                     "en": "the weakness rows"},
    "byOpponent": {"zh": "逐对手应对等级", "ja": "相手ごとの評価", "en": "the per-opponent grades"},
    "orderUnderTrickRoom": {"zh": "戏法空间下的速度顺序", "ja": "トリックルーム下の素早さ順",
                            "en": "the Trick Room speed order"},
    "assumedNeutral": {"zh": "按中性性格假定", "ja": "無補正として仮定", "en": "assumed neutral"},
    "incompleteMembers": {"zh": "配置未知的成员", "ja": "型が不明なメンバー",
                          "en": "the members with an unknown set"},
    "coverageConfirmed": {"zh": "功能覆盖已确认", "ja": "機能カバーの確認状況",
                          "en": "coverage confirmed"},
    "gapsConfirmed": {"zh": "盲点已确认", "ja": "盲点の確認状況", "en": "gaps confirmed"},
    "attentionCalibration": {"zh": "注意等级校准", "ja": "注目度の較正",
                             "en": "the attention calibration"},
    "expectationReason": {"zh": "注意等级依据", "ja": "注目度の根拠", "en": "the attention reason"},
    "bearers": {"zh": "承担该功能的成员", "ja": "その機能を担うメンバー",
                "en": "the members providing it"},
    "topK": {"zh": "统计的对手数量", "ja": "対象とした相手数", "en": "the opponent count"},
}
# Section paths. The bare one-word sections are CJK-only entries: `defense` or `checks` alone is
# an ordinary English word that an English answer may legitimately use, while in zh/ja prose it
# can only be an echoed key.
_SECTION_TERMS: dict[str, dict[str, str]] = {
    "roles.coverage": {"zh": "功能覆盖", "ja": "機能カバー", "en": "the coverage checklist"},
    "offense.hardGaps": {"zh": "属性克制盲点", "ja": "範囲の盲点", "en": "the coverage blind spots"},
    "checks.byOpponent": {"zh": "逐对手应对等级", "ja": "相手ごとの評価",
                          "en": "the per-opponent grades"},
    "defense.byAttackType": {"zh": "防守弱点", "ja": "防御面の弱点", "en": "the weakness rows"},
    "speed.order": {"zh": "速度顺序", "ja": "素早さ順", "en": "the speed order"},
    "roles": {"zh": "功能覆盖", "ja": "機能カバー"},
    "coverage": {"zh": "功能覆盖", "ja": "機能カバー"},
    "checks": {"zh": "对位应对等级", "ja": "対面評価"},
    "offense": {"zh": "进攻打点", "ja": "攻撃範囲"},
    "defense": {"zh": "防守弱点", "ja": "防御面の弱点"},
    "legality": {"zh": "合法性", "ja": "ルール適合"},
}
# Attention levels: non-prescriptive public wording, never the wire enum.
_ATTENTION_TERMS: dict[str, dict[str, str]] = {
    "required": {"zh": "重点关注", "ja": "要確認", "en": "worth a close look"},
    "priority": {"zh": "重点关注", "ja": "要確認", "en": "worth a close look"},
    "optional": {"zh": "按需关注", "ja": "必要に応じて", "en": "situational"},
    "situational": {"zh": "按需关注", "ja": "必要に応じて"},
}
_PRESENT_TERMS: dict[bool, dict[str, str]] = {
    True: {"zh": "已具备", "ja": "あり", "en": "present"},
    False: {"zh": "未检测到", "ja": "検出なし", "en": "not detected"},
}
# `via` / member-signal prefixes: the entity name after the colon stays English canonical.
_VIA_TERMS: dict[str, dict[str, str]] = {
    "move": {"zh": "招式", "ja": "技", "en": "move"},
    "item": {"zh": "道具", "ja": "持ち物", "en": "item"},
    "ability": {"zh": "特性", "ja": "特性", "en": "ability"},
}


def _term(table: dict[str, dict[str, str]], token: str, lang: str) -> str | None:
    return (table.get(token) or {}).get(lang)


def _localize_via(raw: Any, lang: str) -> Any:
    """`ability:Grassy Surge` -> `特性：Grassy Surge`. The prefix is an identifier, the name is
    an entity: localize the first, never the second."""
    if not isinstance(raw, str) or not raw:
        return raw
    out: list[str] = []
    for part in raw.split(", "):
        kind, sep, name = part.partition(":")
        label = _term(_VIA_TERMS, kind, lang)
        if label is None:
            out.append(part)
        elif sep and name:
            out.append(f"{label}: {name}" if lang == "en" else f"{label}：{name}")
        else:
            out.append(label)
    return ", ".join(out)


def _localize_signal(raw: Any, lang: str) -> Any:
    if not isinstance(raw, str):
        return raw
    if ":" in raw:
        return _localize_via(raw, lang)
    return _term(_ROLE_TERMS, raw, lang) or raw


def _is_machine_token(token: str) -> bool:
    """True for tokens no natural sentence produces: snake_case, camelCase, a dotted path."""
    return "_" in token or "." in token or any(c.isupper() for c in token)


def _diagnose_explain_projection(report: dict, lang: str = "en") -> dict:
    """Compact model-facing report that keeps every opponent roll-up but drops the
    6-member damage cells. The deterministic API/UI still receives the complete grid.

    With top-30 checks the cells dominate the payload (~170 KB). Keep the deterministic
    roll-ups for all 30. The prose layer must not reverse-engineer a grade from selected cells:
    partial evidence encouraged confident but incorrect causal explanations in real calls.
    """
    trimmed = {k: report.get(k) for k in
               ("legality", "defense", "offense", "speed", "roles", "checks")}
    roles = trimmed.get("roles")
    if isinstance(roles, dict) and isinstance(roles.get("coverage"), list):
        coverage = []
        for entry in roles["coverage"]:
            if not isinstance(entry, dict):
                continue
            # SITUATIONAL absences are normal in the format and must never surface as problems.
            # Removing them from the payload is surer than asking the prose layer to ignore them.
            if not entry.get("present") and entry.get("expectation") == "situational":
                continue
            key = str(entry.get("key") or "")
            projected = {k: v for k, v in entry.items() if k != "key"}
            projected["label"] = _term(_ROLE_TERMS, key, lang) or entry.get("label") or key
            projected["bearers"] = [
                {**b, "via": _localize_via(b.get("via"), lang)} if isinstance(b, dict) else b
                for b in (entry.get("bearers") or [])]
            coverage.append(projected)
        members = [{**m, "signals": [_localize_signal(sig, lang) for sig in (m.get("signals") or [])]}
                   if isinstance(m, dict) else m
                   for m in (roles.get("members") or [])]
        trimmed["roles"] = {**roles, "coverage": coverage, "members": members}
    checks = trimmed.get("checks")
    if isinstance(checks, dict):
        source_rows = [row for row in (checks.get("byOpponent") or [])
                       if isinstance(row, dict)]

        rows = []
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            projected = {k: row.get(k) for k in
                         ("opponent", "usageRank", "observedFloor", "witnessVariantIds",
                          "floorBy", "representativeGrade", "representedCoverage",
                          "calculationComplete")}
            rows.append(projected)
        trimmed["checks"] = {
            "topK": checks.get("topK"),
            "confidence": checks.get("confidence"),
            "byOpponent": rows,
        }
    return trimmed


def _scrub_internal_tokens(text: str, lang: str) -> str:
    """Last-resort guard: rewrite every internal identifier the model echoed into the public
    wording the page uses.

    The payload no longer carries role keys and the prompt forbids field names outright, but an
    upstream model can still copy an identifier out of the field guide. A reading that says
    "observedFloor 为 C0" or "speed_control 只系于…" is reporting the JSON, not the team, so the
    rewrite is deterministic rather than advisory. English answers keep ordinary words such as
    `defense` or `setup` and only lose the machine-shaped tokens; in zh/ja prose an English word
    from these tables can only be an echo, so all of them are rewritten.
    """
    lang = lang if lang in ("zh", "ja", "en") else "en"
    for flag, terms in _PRESENT_TERMS.items():
        word = terms.get(lang)
        if word:
            text = re.sub(rf"`?\bpresent\s*=\s*{str(flag).lower()}\b`?", word, text,
                          flags=re.IGNORECASE)
    mapping: dict[str, str] = {}
    for table in (_SECTION_TERMS, _FIELD_TERMS, _ROLE_TERMS, _ATTENTION_TERMS):
        for token, terms in table.items():
            word = terms.get(lang)
            if word and (lang != "en" or _is_machine_token(token)):
                mapping[token] = word
    if not mapping:
        return text
    # Longest first so `roles.coverage` wins over `roles`; the trailing lookahead independently
    # stops a prefix from matching inside a longer path. Case-sensitive on purpose: the move
    # `Protect` and the role key `protect` differ only by case.
    alternation = "|".join(re.escape(t) for t in sorted(mapping, key=len, reverse=True))
    pattern = re.compile(rf"`?(?<![\w.])({alternation})(?![\w.])`?")
    return pattern.sub(lambda m: mapping[m.group(1)], text)


def explain_diagnose(provider: LlmProvider, report: dict, lang: str) -> tuple[str, int]:
    """One tool-less LLM call turning a diagnose report's facts into a short reading
    (the local agent's interpretation layer, ported as an OPT-IN online add-on). Returns
    (text, tokens burned); raises LlmUnavailable / QaFailed like the QA pipeline."""
    trimmed = _diagnose_explain_projection(report, lang)
    # State the complete user team explicitly and FIRST. The richer thinking profile needs
    # the actual sets to explain a plan rather than emitting a generic coverage summary; this
    # is the visitor's own submitted data, not private metagame evidence.
    team = [{k: m.get(k) for k in
             ("species", "item", "ability", "nature", "moves", "spread")}
            for m in ((report.get("team") or {}).get("pokemon") or [])
            if isinstance(m, dict) and m.get("species")]
    trimmed = {"team": team, **trimmed}
    thinking = getattr(provider, "thinking_enabled", False)
    report_json = clip_json(trimmed, cap=DIAGNOSE_EXPLAIN_INPUT_BYTES)
    user_content = f"[lang={lang}] [report]{report_json}\n[/report]"
    if thinking:
        # Repeat the high-risk constraints AFTER the data: long system prompts are easy for a
        # reasoning model to dilute while drafting a lengthy answer; the final user contract is
        # the closest instruction to the completion.
        user_content += "\n" + DIAGNOSE_EXPLAIN_THINKING_USER_CONTRACT
    messages = [
        {"role": "system", "content": DIAGNOSE_EXPLAIN_PROMPT
         + (DIAGNOSE_EXPLAIN_THINKING_PROFILE if thinking else "")},
        {"role": "user", "content": user_content},
    ]
    max_tokens = (DIAGNOSE_EXPLAIN_THINKING_MAX_TOKENS
                  if thinking
                  else DIAGNOSE_EXPLAIN_MAX_TOKENS)
    timeout = (DIAGNOSE_EXPLAIN_THINKING_TIMEOUT
               if thinking
               else QA_LLM_TIMEOUT)
    result = provider.chat(messages, [], max_tokens=max_tokens,
                           tool_choice="none", timeout=timeout)
    tokens = result.prompt_tokens + result.completion_tokens
    text = _strip_markdown(str(result.message.get("content") or "").strip())
    text = _scrub_internal_tokens(text, lang)
    if not text:
        raise QaFailed("empty diagnose explanation", tokens)
    _reject_tool_markup(text, tokens)
    return text, tokens


def answer(pool: Any, provider: LlmProvider, question: str, lang: str,
           on_progress: Callable[[dict], None] | None = None,
           consume_team_code: Callable[[], None] | None = None) -> dict:
    """Run the full pipeline. Returns {"answer", "toolTrace", "grounded", "tokens"} plus the
    metric split the §9 benchmark consumes ("promptTokens"/"completionTokens"/"cacheHitTokens"/
    "llmCalls"/"toolBytes"); raises QaFailed (tokens attached) when the model never produces
    usable content. `grounded` is True only when a tool actually ran — an ungrounded reply is a
    refusal/decline, never a Champions fact (design §7.2).

    `on_progress`, when given, receives {"toolTrace", "round"} as the run advances: when each
    model round starts and after each lookup it ran. It carries only what the final payload
    discloses anyway, so a page can show the lookups as they happen.

    Two hard caps make §7.2/§7.4 real, not aspirational: the wall-clock deadline is threaded
    into EVERY provider/worker call (so no single call runs past it) and re-checked between
    tools; and once cumulative tokens cross QA_MAX_REQUEST_TOKENS the loop stops spending on
    tool rounds and forces the closing synthesis."""
    from ..team_code import skill as team_code_skill
    supplied_codes = frozenset(code.upper() for code in team_code_skill().explicit_codes(question))
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"[lang={lang}] {question}"},
    ]
    trace: list[dict] = []
    tokens = 0
    metrics = {"promptTokens": 0, "completionTokens": 0, "cacheHitTokens": 0,
               "llmCalls": 0, "toolBytes": 0}
    deadline = time.monotonic() + QA_DEADLINE_SECONDS
    answered: set[str] = set()      # name + canonical arguments of every executed call
    final_turn = corrected = False
    round_no = 0

    def report() -> None:
        if on_progress is None:
            return
        try:
            on_progress({"toolTrace": [dict(entry) for entry in trace], "round": round_no})
        except Exception:   # a progress listener must never cost the answer
            pass

    while True:
        rem = deadline - time.monotonic()
        if rem <= 0:
            raise QaFailed("qa deadline exceeded", tokens)
        report()
        # Force the closing synthesis on the last allowed round OR once the token ceiling is
        # crossed — no further tool spend, one final tool_choice=none call to answer.
        final = round_no >= QA_MAX_ROUNDS or tokens >= QA_MAX_REQUEST_TOKENS
        if final and not final_turn:
            # The forced-final call carries no tool definitions (MiMo honours only auto), yet the
            # transcript is full of tool calls; told nothing, the model writes its next call out
            # as text (seen live: <tool_call><function=meta_detail>…). Say the tools are gone.
            messages.append({"role": "user", "content": FINAL_TURN})
            final_turn = True
        try:
            result: ChatResult = provider.chat(
                messages, TOOLS, max_tokens=QA_MAX_ANSWER_TOKENS,
                tool_choice="none" if final else "auto",
                timeout=min(QA_LLM_TIMEOUT, rem))
        except LlmUnavailable as e:
            e.tokens = tokens        # earlier rounds already cost money — settle them truthfully
            raise
        tokens += result.prompt_tokens + result.completion_tokens
        metrics["promptTokens"] += result.prompt_tokens
        metrics["completionTokens"] += result.completion_tokens
        metrics["cacheHitTokens"] += result.cache_hit_tokens
        metrics["llmCalls"] += 1
        msg = result.message
        calls = [] if final else msg.get("tool_calls") or []
        if not calls:
            content = _strip_markdown(str(msg.get("content") or "").strip())
            reaching = bool(msg.get("tool_calls")) or bool(_TOOL_MARKUP.search(content))
            if final and reaching and not corrected and deadline - time.monotonic() > 5:
                # Still reaching for a tool: show it the attempt and ask once more for prose.
                corrected = True
                messages.append({"role": "assistant", "content": content or "(tool call)"})
                messages.append({"role": "user", "content": FINAL_RETRY})
                continue
            if not content:
                raise QaFailed("model kept requesting tools past the forced-final round"
                               if reaching else "model returned an empty answer", tokens)
            _reject_tool_markup(content, tokens)
            return {"answer": _scrub_answer(content, lang), "toolTrace": trace,
                    "grounded": bool(trace), "tokens": tokens, **metrics}
        # Every tool_call id MUST get a tool message back (API contract), including the
        # ones past the per-round cap or the deadline — those get an error instead of execution.
        messages.append(msg)
        for i, tc in enumerate(calls):
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
                if not isinstance(args, dict):
                    args = {}
            except ValueError:
                args = {}
            key = f"{fn.get('name')}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
            if i >= QA_MAX_CALLS_PER_ROUND:
                content = json.dumps({"error": "per-round tool budget exceeded"})
            elif key in answered:
                # A repeat cannot add facts; running it again only spends a round.
                content = json.dumps({"note": "this exact call was already answered above; "
                                              "use that result"})
            elif (rem := deadline - time.monotonic()) <= 1:
                content = json.dumps({"error": "deadline exceeded"})
            else:
                answered.add(key)
                traced, content = _run_tool(pool, str(fn.get("name") or ""), args,
                                            timeout=min(QA_TOOL_TIMEOUT, rem), budget=rem,
                                            supplied_codes=supplied_codes,consume_team_code=consume_team_code)
                metrics["toolBytes"] += len(content.encode("utf-8"))
                if traced is not None:
                    trace.append({"tool": traced[0], "label": traced[1], "params": traced[2]})
                    report()
            messages.append({"role": "tool", "tool_call_id": str(tc.get("id") or ""),
                             "content": content})
        round_no += 1
