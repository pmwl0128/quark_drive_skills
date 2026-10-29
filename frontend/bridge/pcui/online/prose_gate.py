"""The output gate shared by every model explanation a visitor reads (diagnosis summary, builder
rationale; frontend/ASSIST_PLAN.md §4.6). It finds what the supplied facts cannot account for:
entity names the facts do not contain, percentage figures, Pokemon names translated out of their
English canonical form (the page localizes names itself), and prose in the wrong language. Callers
send the listed problems back once and otherwise drop the prose; internal field names are rewritten
separately (qa._scrub_internal_tokens)."""
from __future__ import annotations

import re

LANGUAGE_NAMES = {"zh": "Simplified Chinese", "ja": "Japanese", "en": "English"}
PERCENT = re.compile(r"\d+(?:\.\d+)?\s*[%％]")


def problems(text: str, lang: str, authority: str, *, language_text: str | None = None,
             allow_percentages: bool = False) -> list[tuple[str, str]]:
    """(code, instruction) per problem; codes: entities, percentages, translated, language.
    `authority` is the serialized facts the prose may draw on; `language_text` is the part whose
    language is judged (default: the whole text)."""
    from . import qa
    from ..team_assist import _fold, _pokemon_names
    entity_pattern, _, _ = qa._dex_vocabulary()
    allowed = {m.group(0) for m in entity_pattern.finditer(authority)}
    out: list[tuple[str, str]] = []
    extra = {m.group(0) for m in entity_pattern.finditer(text)} - allowed
    if extra:
        # A base name where the facts hold its Mega form usually means that Mega, a different member:
        # say which form to write.
        megas = sorted({f"{name} -> {mega}" for name in extra for mega in allowed if mega.startswith(f"Mega {name}")})
        out.append(("entities", "remove these names, which the supplied facts do not contain: "
                    + ", ".join(sorted(extra)[:8])
                    + (" (where you mean the Mega form the facts name, write it in full: " + ", ".join(megas[:4]) + ")"
                       if megas else "")))
    if not allow_percentages and (percents := PERCENT.findall(text)):
        out.append(("percentages", "remove these percentage values (the cards show the numbers): "
                    + ", ".join(percents[:6])))
    pattern, _names = _pokemon_names()
    translated = sorted({text[m.start():m.end()] for m in pattern.finditer(_fold(text))
                         if not m.group(0).isascii()})
    if translated:
        out.append(("translated", "write every Pokemon, move, item and ability in English canonical form "
                    "exactly as the supplied facts spell them, never translated (a translated name is often "
                    "wrong): " + ", ".join(translated[:8])))
    judged = text if language_text is None else language_text
    if lang in ("zh", "ja") and not qa._CJK.search(judged or ""):
        out.append(("language", f"write every prose field in {LANGUAGE_NAMES[lang]}"))
    return out


def untranslate(text: str) -> str:
    """Pokemon names written in the reader's language put back in English canonical form. The page
    localizes entity names itself, so a caller that only needs the names right rewrites them instead of
    sending them back as a problem."""
    from ..team_assist import _fold, _pokemon_names
    pattern, names = _pokemon_names()
    out: list[str] = []
    end = 0
    for match in pattern.finditer(_fold(text)):
        if match.group(0).isascii():
            continue
        out += [text[end:match.start()], names[match.group(0)][0]]
        end = match.end()
    return "".join(out) + text[end:]


# Sentence ends: CJK and ASCII stops, and a period only before whitespace (not "1.5" or "Mr.Mime").
_SENTENCE_END = re.compile(r"(?<=[。！？!?\n])|(?<=\.)(?=\s)")
SALVAGEABLE = {"entities", "percentages"}


def salvage(text: str, authority: str) -> tuple[str, int]:
    """The text without the sentences that carry an unsupplied entity or a percentage, and how many
    sentences stay. The rest of the prose is grounded, so a caller whose retry still names something
    unsupplied keeps it instead of dropping the whole explanation."""
    parts = [p for p in _SENTENCE_END.split(text) if p.strip()]
    # "en" skips the language judgement, which a single sentence cannot carry.
    kept = [p for p in parts if not {code for code, _m in problems(p, "en", authority)} & SALVAGEABLE]
    return "".join(kept).strip(), len(kept)


def retry_message(found: list[tuple[str, str]], tail: str) -> str:
    return "Correct exactly these problems and return the complete JSON: " \
        + "; ".join(message for _code, message in found) + ". " + tail
