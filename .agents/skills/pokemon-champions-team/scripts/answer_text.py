"""Optional bounded text checks against official dex names and the audited structured draft."""
from __future__ import annotations

import json
import re
import unicodedata

import exploration


def _team_coverage_percentages(draft: dict, output: dict) -> set[float]:
    """Only complete, bound coverage claims for recommended candidates supply percentages."""
    recommended = {row["slate_index"] for row in draft.get("recommended") or []
                   if isinstance(row, dict) and type(row.get("slate_index")) is int}
    values = set()
    for claim in draft.get("team_claims") or []:
        if exploration.team_claim_violations({"team_claims": [claim]}, output, recommended):
            continue
        coverage = claim["expect"].get("coverage") or {}
        if not isinstance(coverage, dict):
            continue
        for key in ("represented", "calculated", "unrepresented"):
            ratio = coverage.get(key)
            if type(ratio) in (int, float) and 0 <= ratio <= 1:
                values.add(round(ratio * 100, 1))
    return values


def localize(team: dict, resolve_fn) -> dict:
    kinds = {"species": "pokemon", "item": "item", "ability": "ability", "nature": "nature", "moves": "move"}
    members = team.get("pokemon") or []
    names = {}
    for field in kinds:
        values = set()
        for member in members:
            if field == "moves":
                values.update(member.get(field) or [])
            else:
                value = member.get(field) or (member.get("name") if field == "species" else None)
                if value:
                    values.add(value)
        names[field] = sorted(values)
    resolved = {field: resolve_fn(values, kinds[field], fuzzy=False) for field, values in names.items() if values}
    errors = []

    def display(field, name, slot):
        if not name:
            return None
        row = resolved.get(field, {}).get(name) or {}
        if not row.get("ok"):
            errors.append({"slot": slot, "field": field, "query": name})
            return {"query": name, "unresolved": True}
        return {"name": row["canonical"], "name_zh": row.get("display_name"), "name_ja": row.get("display_name_ja")}

    result = []
    for slot, member in enumerate(members):
        row = {"slot": slot}
        for field in kinds:
            if field == "moves":
                row[field] = [display(field, n, slot) for n in member.get(field) or []]
            else:
                row[field] = display(field, member.get(field) or (member.get("name") if field == "species" else None), slot)
        result.append(row)
    return {"kind": "localize", "members": result, "unresolved": errors,
            "note": "Official names from dex; null means no supplied/available name. Original team unchanged."}


def check(draft: dict, source: dict, output: dict, vocabulary: list[dict]) -> dict:
    spec = draft.get("text_check")
    if not isinstance(spec, dict) or not isinstance(spec.get("text"), str) or not spec["text"].strip() or spec.get("lang") not in ("zh", "ja", "en"):
        return {"violations": [{"code": "bad_text_check", "where": "text_check",
                  "detail": "text_check requires nonempty text and lang:zh|ja|en"}]}
    text, lang = unicodedata.normalize("NFKC", spec["text"]), spec["lang"]
    vocab = {}
    for row in vocabulary:
        for key in ("name", "name_zh", "name_ja"):
            if row.get(key):
                vocab.setdefault(unicodedata.normalize("NFKC", row[key]).casefold(), []).append(row)
    # Longest names first so a Mega name never becomes a base-species hit. ASCII word boundaries
    # prevent e.g. Protect in protection. CJK official-name homonyms remain a disclosed limitation.
    def token(name):
        return (r"(?<![A-Za-z0-9])" if name[0].isascii() and name[0].isalnum() else "") + re.escape(name) + (r"(?![A-Za-z0-9])" if name[-1].isascii() and name[-1].isalnum() else "")
    pattern = re.compile("|".join(token(n) for n in sorted(vocab, key=lambda n: (-len(n), n))), re.I)
    authority = json.dumps({"context": source.get("context"), "teams": source.get("teams"),
                            "candidates": output.get("candidates")}, ensure_ascii=False)
    authority = unicodedata.normalize("NFKC", authority)
    allowed = {r["name"] for m in pattern.finditer(authority) for r in vocab[m.group().casefold()]}
    matches = list(pattern.finditer(text))
    seen = {r["name"] for m in matches for r in vocab[m.group().casefold()]}
    violations, review_required = [], []
    def issue(code, detail):
        violations.append({"code": code, "where": "text_check.text", "detail": detail})
    extra = seen - allowed
    if extra:
        # A substring is not a factual assertion: e.g. Chinese "immune" is also an
        # ability name, and discussing an absent move is a legitimate comparison.
        review_required.append({"code": "text_entities_unbound", "where": "text_check.text",
            "detail": "Review mentions not present in supplied facts (may be homonyms or comparisons): "
                      + ", ".join(sorted(extra))})
    required = {m.get("species") or m.get("name") for r in draft.get("recommended") or []
                for m in (r.get("team") or {}).get("pokemon") or []}
    # Source normalization can contain aliases; match the original member through the same official vocabulary.
    missing = [name for name in sorted(required - {None})
               if not any(row["name"] in seen for row in vocab.get(name.casefold(), []))]
    if missing:
        issue("text_member_names_missing", "Use a full official name for each recommended member: " + ", ".join(missing))
    percentages = {round(float(v), 1) for c in draft.get("claims") or [] if isinstance(c, dict) and isinstance(c.get("expect"), dict)
                   for k, v in (c.get("expect") or {}).items() if k in ("min_percent", "max_percent")
                   and type(v) in (int, float)}
    percentages.update(_team_coverage_percentages(draft, output))
    unsupported = [m.group() for m in re.finditer(r"(?<![\d.])-?\d+(?:\.\d+)?\s*%", text)
                   if round(float(m.group().replace("%", "").strip()), 1) not in percentages]
    if unsupported:
        issue("text_percentages_unbound", "Percentages need a numerical claim: " + ", ".join(unsupported))
    prose = pattern.sub("", text)
    han = bool(re.search(r"[\u3400-\u9fff]", prose))
    kana = bool(re.search(r"[\u3040-\u30ff]", prose))
    if (lang == "en" and (han or kana)) or (lang == "zh" and (not han or kana)) or (lang == "ja" and not kana):
        issue("text_language", "Prose does not match the requested script/language: " + lang)
    return {"violations": violations, "review_required": review_required, "detected_entities": sorted(seen),
            "note": "Bounded dictionary, member-presence, percentage and script checks only; not semantic verification. "
                    "Unbound mentions, unknown invented names and CJK homonyms need human/model review. "
                    "A matching percentage is not proof that its sentence states the claim correctly."}
