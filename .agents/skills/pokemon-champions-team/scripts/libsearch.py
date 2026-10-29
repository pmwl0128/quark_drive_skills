"""Real-team sample-library SEARCH — the AI-facing query API over data/teams/<season>_<format>.jsonl.

Answers "does the library contain a team matching these conditions, and what is its exact config?"
without the caller hand-writing a jsonl scan. The query is a list of member SLOTS; each slot is a
conjunction of constraints (species / move(s) / item / ability / nature) and every slot must be
satisfied by a DISTINCT team member. So a query like

    [ {"species": "耿鬼"},
      {"item": "剧毒宝珠", "move": "灭亡之歌"},     # a member holding that item AND knowing that move
      {"ability": "降雨"} ]

means "a team with (some Gengar) AND (a member holding Toxic Orb that knows Perish Song) AND (a member
with Drizzle)", the three matched to three different members.

Facts-only (design §8 / repset boundaries): results are observed configs + counts + a content id. No
synthetic strength score. Condition values are normalized through the dex `resolve` bridge (Mega-compose
+ nicknames + fuzzy), so users type any language / colloquial name. Queries use exact battle forms:
a base holding its dex-declared Mega stone matches that Mega, while X/Y/Z and ordinary forms remain
distinct. Frozen composition/copy grouping below has its own explicit family semantics.
"""
from __future__ import annotations

import re
from typing import Any

import dexlink
import repset
import teamid
import query_identity

_MEGA_PREFIX = re.compile(r"^mega\s+", re.IGNORECASE)
_MEGA_XY_SUFFIX = re.compile(r"\s+[xy]$", re.IGNORECASE)

# A slot constraint kind -> the dex `resolve --kind` to normalize its value with.
_KINDS = {"species": "pokemon", "move": "move", "moves": "move", "item": "item",
          "ability": "ability", "nature": "nature"}

# Only move/moves take a list (a member knowing several moves); species/item/ability/nature are
# single-valued. A scalar field given a list, or a field name that isn't in _KINDS at all, is a caller
# mistake (typo / wrong shape) — rejected as bad_input, NEVER silently dropped. Silent dropping turned
# a typo'd `{"speceis":...}` into an empty slot that matched the whole library, and a scalar given a
# list into "only the first value counts" (audit 2026-07-01).
_LIST_FIELDS = {"move", "moves"}


def base_key(species: str) -> str:
    """Fold a canonical species to its base for order/Mega-insensitive matching: `Mega Gengar` and
    `Gengar` -> `gengar`, `Mega Charizard X` -> `charizard`. Trailing X/Y is stripped ONLY for a Mega
    form (a base species never legitimately ends in ' X'/' Y')."""
    n = (species or "").strip()
    if _MEGA_PREFIX.match(n):
        n = _MEGA_XY_SUFFIX.sub("", _MEGA_PREFIX.sub("", n))
    return n.lower()


def _collect_values(members_q: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Every raw condition value per kind, so we can resolve each kind in ONE batched dex call."""
    by_kind: dict[str, set[str]] = {}
    for slot in members_q:
        for field, val in slot.items():
            kind = _KINDS.get(field)
            if not kind:
                continue
            vals = val if isinstance(val, list) else [val]
            by_kind.setdefault(kind, set()).update(str(v) for v in vals)
    return by_kind


def _resolve_all(members_q: list[dict[str, Any]]) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, str]]]:
    """Resolve all condition values via the dex bridge. Returns ((kind,raw)->resolve_record, unresolved).
    Unresolved values are surfaced (not silently dropped) so a typo'd condition reads as a bad keyword,
    never as a genuine 'no such team'."""
    resolved: dict[tuple[str, str], dict[str, Any]] = {}
    unresolved: list[dict[str, str]] = []
    for kind, vals in _collect_values(members_q).items():
        recs = dexlink.resolve_names(sorted(vals), kind, fuzzy=True)
        for raw, rec in recs.items():
            resolved[(kind, raw)] = rec
            if not rec.get("ok"):
                unresolved.append({"kind": kind, "value": raw})
    return resolved, unresolved


def _build_slots(members_q: list[dict[str, Any]],
                 resolved: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn each raw slot into a resolved matcher: species/moves/scalars use canonical names.
    Unresolved values carry unknown truth, including beneath NOT, rather than proving absence."""
    slots: list[dict[str, Any]] = []
    for slot in members_q:
        m: dict[str, Any] = {}
        for field, val in slot.items():
            kind = _KINDS.get(field)
            if not kind:
                continue
            vals = val if isinstance(val, list) else [val]
            canon = []
            for v in vals:
                rec = resolved.get((kind, str(v)))
                canon.append(rec["canonical"] if rec and rec.get("ok") else f"\0UNRESOLVED:{v}")
                if not rec or not rec.get("ok"):
                    m["_unresolved"] = True
            if field == "species":
                m["species"] = canon[0]
            elif field in ("move", "moves"):
                m.setdefault("moves", []).extend(canon)
            else:
                m[field] = canon[0]
        slots.append(m)
    return slots


def _slot_matches(slot: dict[str, Any], member: dict[str, Any], identity=None) -> bool | None:
    if slot.get("_unresolved"):
        return None
    if "species" in slot:
        form = identity or (lambda m: m.get("species"))
        requested, actual = form(slot), form(member)
        if requested is None or actual is None:
            return None
        if requested != actual:
            return False
    if "item" in slot and member.get("item") != slot["item"]:
        return False
    if "ability" in slot and member.get("ability") != slot["ability"]:
        return False
    if "nature" in slot and member.get("nature") != slot["nature"]:
        return False
    member_moves = set(member.get("moves") or [])
    for mv in slot.get("moves", []):
        if mv not in member_moves:
            return False
    return True


def _team_satisfies(slots: list[dict[str, Any]], members: list[dict[str, Any]], identity=None) -> bool:
    """True iff every slot maps to a DISTINCT member (bipartite matching, backtracking; slot count <=6)."""
    used = [False] * len(members)

    def bt(i: int) -> bool:
        if i == len(slots):
            return True
        for j, mem in enumerate(members):
            if not used[j] and _slot_matches(slots[i], mem, identity):
                used[j] = True
                if bt(i + 1):
                    return True
                used[j] = False
        return False

    return bt(0)


def _member_view(m: dict[str, Any]) -> dict[str, Any]:
    return {"species": m.get("species"), "item": m.get("item"), "ability": m.get("ability"),
            "nature": m.get("nature"), "moves": m.get("moves") or [], "spread": m.get("spread"),
            "tera": m.get("tera"), "completeness": m.get("completeness")}


def team_view(team: dict[str, Any]) -> dict[str, Any]:
    """The library row as the AI-facing fact view (id + members + performance tag + recency).
    Public: search, show AND the P7 observed retrieval all emit THIS one view — the library
    never grows two row shapes."""
    prov = team.get("provenance") or {}
    return {"id": teamid.compute_id(team), "season": team.get("season"), "rule": team.get("rule"),
            "format": team.get("format"),
            "pokemon": [_member_view(m) for m in team.get("pokemon", [])],
            "performance": prov.get("performance"), "fetched_at": prov.get("fetched_at")}


def joint_set_signature(team: dict[str, Any]) -> tuple:
    """Order-independent signature over a team's JOINT sets: (base-folded species, item, ability,
    nature, sorted moves) per member — the same member facts as teamid._member_fingerprint MINUS
    the spread (pinned by test). Spread is EXCLUDED on purpose — a tweaked spread must not dodge
    the verbatim-copy check; species are BASE-folded and text fields casefolded so a
    representation change (doubles 'Mega X' vs base+stone, letter case) does not dodge it either
    (self-audit 2026-07-03). Residual boundary: the P5/P6 gates feed dex-canonicalized teams
    (_canon_team_json canonicalizes species AND items) — an untranslated alias only survives
    outside the audited chain."""
    def fold(s):
        return (s or "").casefold()
    return tuple(sorted(
        (base_key(m.get("species") or ""), fold(m.get("item")), fold(m.get("ability")),
         fold(m.get("nature")), tuple(sorted(fold(mv) for mv in (m.get("moves") or []) if mv)))
        for m in team.get("pokemon", []) if m.get("species")))


def composition_key(team: dict[str, Any]) -> tuple:
    """Base-folded species multiset — 'the same six' regardless of sets/forms."""
    return tuple(sorted(base_key(m.get("species") or "")
                        for m in team.get("pokemon", []) if m.get("species")))


def library_copy_index(teams: list[dict[str, Any]]) -> dict[str, dict]:
    """Precomputed lookup for the library guardrail (§A.D): joint-set signature -> stored team ids
    (verbatim copies) and composition key -> count (same six species). Built once per operator run;
    the P5 slate surfaces hits as FACTS and the P6 answer-audit requires a verbatim RECOMMENDATION
    to carry observed_provenance — the gate is TRANSPARENCY, not prohibition (user ruling
    2026-07-03: adoption is legitimate as the library saturates the good-team space; SILENCE is
    the violation). Origin: an external integration audit showed stripped search rows sailing
    through the whole chain unflagged."""
    sig: dict[tuple, list[str]] = {}
    comp: dict[tuple, int] = {}
    for t in teams:
        sig.setdefault(joint_set_signature(t), []).append(teamid.compute_id(t))
        comp[composition_key(t)] = comp.get(composition_key(t), 0) + 1
    return {"signatures": sig, "compositions": comp}


def find_library_copies(team: dict[str, Any], index: dict[str, dict]) -> dict[str, Any] | None:
    """{verbatim_ids, same_composition} when the team matches stored teams; None when clean."""
    ids = sorted(set(index["signatures"].get(joint_set_signature(team)) or []))
    comp = index["compositions"].get(composition_key(team), 0)
    if not ids and not comp:
        return None
    return {"verbatim_ids": list(ids), "same_composition": comp}


def library_composition_overlap(team: dict[str, Any], index: dict[str, dict]) -> dict[str, Any]:
    """Distribution of shared registration species; composition overlap is not set identity."""
    from collections import Counter
    members = Counter(composition_key(team))
    counts: dict[int, int] = {}
    for composition, number in index["compositions"].items():
        shared = sum((members & Counter(composition)).values())
        counts[shared] = counts.get(shared, 0) + number
    return {"basis": "registration_species_base_folded", "candidate_size": sum(members.values()),
            "library_team_count": sum(counts.values()),
            "shared_member_counts": [{"shared": shared, "teams": counts[shared]} for shared in sorted(counts)],
            "max_shared": max(counts) if counts else None,
            "note": "Registration overlap folds forms; it is separate from verbatim joint-set identity."}


_BOOL_KEYS = {"and", "or", "not"}


def _validate_slot(slot: dict[str, Any], label: str) -> None:
    """A slot must constrain >=1 known field (species/move/moves/item/ability/nature; only move/moves
    take a list). Shared by `search` (bipartite `members`) and `search_where` (boolean leaves)."""
    if not slot:
        raise ValueError(
            f"{label} is empty — a slot must constrain something (an empty slot matches every "
            "member, collapsing the search to the whole library); give it at least one of "
            "species, move/moves, item, ability, nature, or drop the slot to browse the partition")
    for field, val in slot.items():
        if field not in _KINDS:
            raise ValueError(
                f"{label} has unknown field {field!r} (typo?); a slot's fields are "
                "species, move/moves, item, ability, nature")
        if field not in _LIST_FIELDS and isinstance(val, list):
            raise ValueError(
                f"{label} field {field!r} takes a single value, not a list "
                f"(only move/moves accept a list of moves); got {val!r}")


def search(query: dict[str, Any], *, game_format: str | None = None, season: str | None = None,
           rule: str | None = None, limit: int | None = None, collapse: bool = False) -> dict[str, Any]:
    """Run a library search. `query` = {members:[slot,...]} (or a bare list of slots). Returns
    {game_format, season, slots, unresolved_conditions, scanned, match_count, collapsed?, teams|groups}."""
    if isinstance(query, list):
        query = {"members": query}
    if not isinstance(query, dict):
        raise ValueError("search query must be a JSON object {members:[...]} or a list of slots")
    members_q = query.get("members")
    if members_q is None:
        members_q = []
    if not isinstance(members_q, list) or not all(isinstance(s, dict) for s in members_q):
        raise ValueError('search query.members must be a list of slot objects, '
                         'e.g. [{"species":"Incineroar"}, {"move":"Perish Song"}]')
    # NOTE: an EMPTY members list (no conditions) is the legitimate "browse the whole partition" query
    # (season-defaulted, like repset without a species) — allowed. An empty SLOT {} is different: the
    # caller wrote a slot object and gave it no constraint, so it silently matches EVERY member and
    # collapses the search to the whole library — the same failure the typo'd-field guard closes, via
    # the other door (audit 2026-07-05). Reject the empty slot, not the empty list.
    for i, slot in enumerate(members_q):
        _validate_slot(slot, f"slot #{i}")
    fmt = game_format or query.get("game_format")
    if fmt not in ("single", "double"):
        raise ValueError("search needs game_format single|double (flag --game-format or query.game_format)")
    season = season or query.get("season")
    if limit is None:
        limit = query.get("limit")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 0):
        raise ValueError(f"search limit must be a non-negative integer, got {limit!r}")
    collapse = collapse or bool(query.get("collapse"))

    resolved, unresolved = _resolve_all(members_q)
    slots = _build_slots(members_q, resolved)
    teams = repset.load_teams_for_rule(fmt, rule) if rule else repset.load_teams(fmt, season)
    identity = query_identity.Forms(query_identity.members(teams, slots))
    matches = [t for t in teams if _team_satisfies(slots, t.get("pokemon", []), identity)]

    out: dict[str, Any] = {
        "game_format": fmt, "season": season, "rule": rule,
        "data_seasons": repset.seasons_in(teams) if rule else None,
        "slots": [{k: v for k, v in s.items() if not k.startswith("_")} for s in slots],
        "unresolved_conditions": unresolved,
        "name_resolution": query_identity.corrections(resolved),
        "identity_basis": "dex_battle_form", "identity_gaps": identity.gaps,
        "scanned": len(teams), "match_count": len(matches),
    }
    if collapse:
        groups: dict[tuple, dict[str, Any]] = {}
        for t in matches:
            key = tuple(sorted(base_key(m.get("species", "")) for m in t.get("pokemon", [])))
            g = groups.setdefault(key, {"species_set": list(key), "variant_count": 0, "variants": []})
            g["variant_count"] += 1
            g["variants"].append(team_view(t))
        group_list = sorted(groups.values(), key=lambda g: -g["variant_count"])
        if limit is not None:                          # limit==0 means "count only, no bodies"
            group_list = group_list[:limit]
        out["collapsed"] = True
        out["group_count"] = len(groups)
        out["groups"] = group_list
    else:
        views = [team_view(t) for t in matches]
        if limit is not None:                          # limit==0 means "count only, no bodies"
            views = views[:limit]
        out["teams"] = views
    return out


def _walk_where(node: Any, leaves: list[dict[str, Any]]) -> None:
    """Validate a team boolean-query node and collect its leaf SLOTS in order. A node is either a
    boolean {and|or:[...]} / {not:node} (the bool key must be the ONLY key) or a leaf slot — a
    conjunction of slot fields (species/move/item/ability/nature, possibly several at once, which is
    what disambiguates a multi-field leaf from a single-key bool node)."""
    if not isinstance(node, dict) or not node:
        raise ValueError("each query node must be an object: a slot {species/move/item/ability/nature}, "
                         "or {and|or:[...]} / {not:node}")
    combos = [k for k in node if str(k).lower() in _BOOL_KEYS]
    if combos:
        if len(node) != 1:
            raise ValueError("a boolean node (and/or/not) must be its only key — do not mix it with slot fields")
        k = str(combos[0]).lower()
        val = node[combos[0]]
        if k in {"and", "or"}:
            if not isinstance(val, list) or not val:
                raise ValueError(f'"{k}" takes a non-empty list of sub-queries')
            for ch in val:
                _walk_where(ch, leaves)
        else:
            _walk_where(val, leaves)
    else:
        _validate_slot(node, "leaf slot")
        leaves.append(node)


def _compile_where(node: Any, matcher_of: dict[int, dict[str, Any]], identity=None):
    """Compile a validated AST into a team->bool predicate. A leaf is EXISTENCE: the team has >=1 member
    matching that slot (matcher looked up by the leaf's identity)."""
    combos = [k for k in node if str(k).lower() in _BOOL_KEYS]
    if combos:
        k = str(combos[0]).lower()
        val = node[combos[0]]
        if k == "and":
            preds = [_compile_where(ch, matcher_of, identity) for ch in val]
            return lambda team: _known_all([p(team) for p in preds])
        if k == "or":
            preds = [_compile_where(ch, matcher_of, identity) for ch in val]
            return lambda team: _known_any([p(team) for p in preds])
        sub = _compile_where(val, matcher_of, identity)
        return lambda team: None if (value := sub(team)) is None else not value
    matcher = matcher_of[id(node)]
    return lambda team: _known_any([_slot_matches(matcher, m, identity) for m in team.get("pokemon", [])])


def _known_any(values):
    return True if True in values else None if None in values else False


def _known_all(values):
    return False if False in values else None if None in values else True


def search_where(ast: Any, *, game_format: str | None = None, season: str | None = None,
                 rule: str | None = None,
                 limit: int | None = None, collapse: bool = False) -> dict[str, Any]:
    """Boolean team-library search: AND/OR/NOT over leaf SLOTS. A leaf is EXISTENCE — "the team has a
    member matching this slot". Unlike the bipartite `members` form, boolean leaves do NOT claim DISTINCT
    members (OR/NOT have no bipartite reading); when you need N distinct members use `search`'s `members`.
    Same output shape as `search`, plus an echoed `where`."""
    if game_format not in ("single", "double"):
        raise ValueError("search needs game_format single|double (flag --game-format or query.game_format)")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 0):
        raise ValueError(f"search limit must be a non-negative integer, got {limit!r}")
    leaves: list[dict[str, Any]] = []
    _walk_where(ast, leaves)
    resolved, unresolved = _resolve_all(leaves)
    matchers = _build_slots(leaves, resolved)
    matcher_of = {id(leaf): matchers[i] for i, leaf in enumerate(leaves)}
    teams = repset.load_teams_for_rule(game_format, rule) if rule else repset.load_teams(game_format, season)
    identity = query_identity.Forms(query_identity.members(teams, matchers))
    pred = _compile_where(ast, matcher_of, identity)
    matches = [t for t in teams if pred(t)]
    out: dict[str, Any] = {
        "game_format": game_format, "season": season, "rule": rule,
        "data_seasons": repset.seasons_in(teams) if rule else None, "where": ast,
        "unresolved_conditions": unresolved, "scanned": len(teams), "match_count": len(matches),
        "name_resolution": query_identity.corrections(resolved),
        "identity_basis": "dex_battle_form", "identity_gaps": identity.gaps,
    }
    if collapse:
        groups: dict[tuple, dict[str, Any]] = {}
        for t in matches:
            key = tuple(sorted(base_key(m.get("species", "")) for m in t.get("pokemon", [])))
            g = groups.setdefault(key, {"species_set": list(key), "variant_count": 0, "variants": []})
            g["variant_count"] += 1
            g["variants"].append(team_view(t))
        group_list = sorted(groups.values(), key=lambda g: -g["variant_count"])
        if limit is not None:
            group_list = group_list[:limit]
        out.update(collapsed=True, group_count=len(groups), groups=group_list)
    else:
        views = [team_view(t) for t in matches]
        if limit is not None:
            views = views[:limit]
        out["teams"] = views
    return out


def get_team(team_id: str, *, game_format: str | None = None, season: str | None = None) -> dict[str, Any] | None:
    """Fetch one team by its content id (from `search`). id namespaces season+format, so with no
    game_format/season this scans every library file and still resolves uniquely. None if not found."""
    for fmt in ([game_format] if game_format else ["single", "double"]):
        for t in repset.load_teams(fmt, season):
            if teamid.compute_id(t) == team_id:
                return team_view(t)
    return None
