#!/usr/bin/env python
"""Selection matrix (M2 core) — OBJECTIVE FACTS ONLY.

Champions is bring-6 / pick-3 (singles) or pick-4 (doubles), and only ONE member may Mega Evolve
per battle. This operator enumerates the legal pick subsets and reports objective facts for each —
who's in it, which member(s) could Mega (and into what), the base-Speed ordering, and the types
present — so the AI can reason about the 6vN choice. It assigns NO strength score and names NO single
best pick; it does not rank combos, only lists them in a stable, neutral order.

Optional CHECK coverage comes from the supplied matchup battery, including its opponent scope and
field conditions. Lead/back constraints, partner synergy and full turn sequences remain outside it.

External lookups are injected (dex_fn / item_fn) so the logic is unit-testable offline.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any, Callable

import checks
import team_i18n as i18n
from mega import mega_form_from_maps

PICK_SIZE = {"single": 3, "double": 4}
MAX_ACTIVE_MEGAS = 1


def _combo_check_coverage(members: list[str], grid: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Per-lineup CHECK coverage, as OBJECTIVE per-opponent facts — NEVER a lineup score/ranking.

    `grid` is a matchup `members` grid (rows carrying `member` + `cells` with a derived `check`). We
    restrict it to THIS lineup's members and reuse `checks.coverage_summary` — a pure set operation over
    the already-computed per-member grades (ZERO extra ncp). What we surface per lineup is the same
    facts the whole-team coverage surfaces: the grade DISTRIBUTION (a count), witnessed observed floors,
    and the HOLES (opponents no lineup member switch-in checks). We deliberately do NOT emit any lineup
    aggregate or sort lineups by coverage — coverage-maximization is exactly the hidden optimizer the
    facts-only red line forbids (design §1/§16). The AI reads these per-lineup facts; picking a lineup
    stays its judgment, made against the disclosed holes, not a skill-emitted best."""
    wanted = set(members)                         # hoisted: was rebuilt once per grid row
    rows = [r for r in grid if r.get("member") in wanted]
    if not rows:
        return None
    cov = checks.coverage_summary(rows)
    return {
        "grade_distribution": cov.get("grade_distribution"),   # a COUNT for THIS lineup, never a score
        "by_opponent": cov.get("by_opponent"),                 # explicit witnessed observed floors
        "holes": cov.get("holes"),                             # opponents with no safe switch-in check
        "note": "per-lineup coverage FACTS (strongest member per retained variant, then witnessed "
                "observed floor per opponent); "
                "a per-opponent label table + counts, NEVER a lineup score and lineups are NOT ranked "
                "by it — the hole list is the actionable fact, the pick stays your judgment (design §1).",
    }


def _mega_form_for(member: dict[str, Any], own_fact: dict[str, Any],
                   item_info: dict[str, dict], form_facts: dict[str, dict]) -> str | None:
    """The Mega form this member would become, or None. Given-as-Mega members resolve to themselves;
    otherwise a held Mega stone whose form's base matches the member's species resolves the form."""
    return mega_form_from_maps(member.get("species"), member.get("item"), own_fact, item_info, form_facts)


def select(team: dict[str, Any], *, fmt: str | None = None,
           dex_fn: Callable, item_fn: Callable,
           legality_status: str | None = None,
           keep_mega: str | None = None,
           check_grid: list[dict[str, Any]] | None = None,
           check_context: dict[str, Any] | None = None) -> dict[str, Any]:
    members = [m for m in team.get("pokemon", []) if m.get("species")]
    fmt = (fmt or team.get("format") or "single").lower()
    pick = PICK_SIZE.get(fmt, 3)

    species = [m["species"] for m in members]
    items = sorted({m["item"] for m in members if m.get("item")})
    item_info = item_fn(items) if items else {}
    # Candidate Mega forms referenced by held stones, looked up so we can match form -> base species.
    candidate_forms = sorted({f for it in item_info.values() for f in (it.get("required_by") or [])})
    facts = dex_fn(species + candidate_forms) or {}

    # Per-member objective attributes computed once. When a member can Mega Evolve, the Mega form's
    # OWN types/speed are looked up too: a base form carrying a stone keeps its base type/speed until
    # it Megas, but Mega Evolution can change both (e.g. Mega Charizard X: Fire/Flying -> Fire/Dragon),
    # so reporting only base data would be a factual error (audit 2026-06-21). Base (as-brought) values
    # drive speed_order/types_present; the Mega delta is surfaced per mega_option.
    attrs: dict[str, dict[str, Any]] = {}
    for m in members:
        sp = m["species"]
        fact = facts.get(sp, {}) or {}
        mega_form = _mega_form_for(m, fact, item_info, facts)
        mfact = (facts.get(mega_form, {}) or {}) if mega_form else {}
        attrs[sp] = {
            "base_speed": (fact.get("stats") or {}).get("spe"),
            "types": list(fact.get("types") or []),
            "mega_form": mega_form,
            "mega_types": list(mfact.get("types") or []),
            "mega_base_speed": (mfact.get("stats") or {}).get("spe"),
        }

    idxs = list(range(len(members)))
    groups = list(combinations(idxs, pick)) if len(members) > pick else [tuple(idxs)]

    combos: list[dict[str, Any]] = []
    any_mega_changes = False
    for g in groups:
        sel = [members[i]["species"] for i in g]
        mega_options = []
        for s in sel:
            a = attrs[s]
            if not a["mega_form"]:
                continue
            opt = {"member": s, "form": a["mega_form"]}
            # The user's declared keep (build-context.keep_mega, dex-canonicalized upstream) — an
            # objective highlight so the declaration is visibly honored instead of silently ignored
            # (audit 2026-07-02: the field was accepted but consumed by nothing). It matches either
            # the member species or the Mega form name; it is a marker, NEVER a recommendation.
            if keep_mega and keep_mega in (s, a["mega_form"]):
                opt["user_keep"] = True
            # Surface the objective Mega delta only when the form actually differs from the base.
            if a["mega_types"] and a["mega_types"] != a["types"]:
                opt["form_types"] = a["mega_types"]
                any_mega_changes = True
            if a["mega_base_speed"] is not None and a["mega_base_speed"] != a["base_speed"]:
                opt["form_base_speed"] = a["mega_base_speed"]
                any_mega_changes = True
            mega_options.append(opt)
        speed_order = sorted(
            [{"member": s, "base_speed": attrs[s]["base_speed"]} for s in sel],
            key=lambda x: (-(x["base_speed"] or -1), x["member"]),
        )
        types_present = sorted({t for s in sel for t in attrs[s]["types"]})
        entry = {
            "members": sel,
            "mega_options": mega_options,
            "multiple_mega_brought": len(mega_options) > 1,   # legal to bring; only one may Mega in battle
            # Explicit battle-state enumeration closes the most common conceptual error: a lineup with
            # two registered stones does NOT have two active Mega forms.  These states are membership
            # facts only; matchup/check rows are not yet recomputed per active form (note below).
            "mega_activation_states": (
                [{"active_member": None, "active_form": None}]
                + [{"active_member": o["member"], "active_form": o["form"]}
                   for o in mega_options]
            ),
            "speed_order": speed_order,        # as-brought (pre-Mega) base Speed
            "types_present": types_present,    # as-brought (pre-Mega) types
        }
        # Opt-in CHECK coverage per lineup (only when the caller ran the matchup battery and passed the
        # grid). Objective per-opponent facts, reusing the whole-team roll-up restricted to this lineup —
        # never a lineup score; lineups stay in neutral name order below (design §1/§16).
        if check_grid is not None:
            cc = _combo_check_coverage(sel, check_grid)
            if cc is not None:
                entry["check_coverage"] = cc
        combos.append(entry)
    # Stable, neutral ordering (by member names) — explicitly NOT a quality ranking.
    combos.sort(key=lambda c: c["members"])
    for index, combo in enumerate(combos):
        combo["lineup_index"] = index

    # Per-option route availability across the actual 6-pick-N subsets.  This is the structural fact
    # that flat six-member evaluation hides: a second registered Mega can own exclusive lineups or be
    # brought alongside another option for preview-time choice.  Counts only; no route is ranked.
    registered_options: list[dict[str, Any]] = []
    seen_options: set[tuple[str, str]] = set()
    for combo in combos:
        for opt in combo["mega_options"]:
            key = (opt["member"], opt["form"])
            if key not in seen_options:
                seen_options.add(key)
                registered_options.append({"member": key[0], "form": key[1]})
    mega_routes = []
    for opt in registered_options:
        lineups = [c for c in combos if any(
            o["member"] == opt["member"] and o["form"] == opt["form"]
            for o in c["mega_options"])]
        exclusive = [c["lineup_index"] for c in lineups if len(c["mega_options"]) == 1]
        shared = [c["lineup_index"] for c in lineups if len(c["mega_options"]) > 1]
        mega_routes.append({
            **opt,
            "lineup_count": len(lineups),
            "exclusive_lineup_indices": exclusive,
            "shared_multi_mega_lineup_indices": shared,
        })

    # The legality note depends on whether a caller already ran `validate` and passed the verdict
    # in: the CLI does (so claiming "run validate first" would contradict the attached legality —
    # audit 2026-06-21); the bare library call does not, so it keeps the run-validate-first advice.
    if legality_status is None:
        legality_note = ("Legality is NOT re-checked by this operator: it enumerates pick subsets of the "
                         "registered team and assumes it already passed `validate`. Run validate first — "
                         "selection does not certify legality.")
    else:
        legality_note = (f"Registered-team legality was checked: status={legality_status!r} (see `legality`). "
                         "These are pick subsets of that team; selection itself certifies no legality.")
    notes = [
        f"{fmt}: bring {len(members)}, pick {pick}. Objective facts per pick subset — no strength score, "
        "no single best pick; combos are listed in a neutral order, not ranked.",
        legality_note,
        "Only ONE member may Mega Evolve per battle: a combo carrying multiple Mega stones is legal to "
        "bring (multiple_mega_brought=true), but you Mega at most one once in battle.",
        "mega_activation_states enumerates that one-active-Mega choice, and mega_routes counts each "
        "registered option's exclusive/shared lineup availability. These are structural route facts: "
        "check_coverage is still lineup-level and is NOT recomputed per active Mega state.",
        "Lead/back constraints, doubles partner synergy and full turn sequences are not modelled. "
        "speed_order uses base Speed; supplied CHECK coverage inherits the matchup battery's "
        "opponent scope and field conditions.",
    ]
    if check_grid is not None:
        notes.append(
            "each combo carries `check_coverage` from the supplied matchup battery (opt-in, ncp-grounded): per-opponent "
            "the witnessed observed floor after taking the strongest member per retained build + the "
            "HOLES (opponents no lineup member "
            "switch-in checks) + a grade COUNT. These are per-lineup FACTS — combos are still listed in a "
            "neutral name order, never ranked by coverage, and no 'best lineup' is emitted (design §1/§16). "
            "The coverage inherits the matchup battery's confidence (vs-observed-build), not selection's own.")
        if check_context and check_context.get("conditions"):
            notes.append(
                "CHECK coverage incorporates the stated field conditions in check_coverage_context. "
                "When specified, Trick Room changes equal-priority action order, not numeric Speed. "
                "speed_order remains pre-Mega base Speed, not a field-adjusted action order.")
    if any_mega_changes:
        notes.insert(3, "speed_order and types_present are the as-brought (pre-Mega) values; if a member Mega Evolves, "
                     "its post-Mega type/Speed are given on its mega_option (form_types / form_base_speed when they differ).")
    if len(members) <= pick:
        notes.append(f"team has {len(members)} <= pick {pick}; the whole team is the only selection.")

    # keep_mega echo: the declaration must be visibly honored or visibly inapplicable — never
    # silently dropped. `matched` False = it names no member/Mega-form of this team (a conflict the
    # AI should surface back to the user; context-audit will classify it later).
    keep_echo = None
    if keep_mega:
        matched = any(o.get("user_keep") for c in combos for o in c["mega_options"])
        keep_echo = {"requested": keep_mega, "matched": matched}
        if not matched:
            notes.append(f"keep_mega={keep_mega!r} matches no member / Mega form of this team — "
                         "declaration could not be applied; check the name or the team.")

    # With --with-check the output embeds ncp-grounded per-combo check_coverage (whose own lower,
    # vs-observed-build confidence lives in check_coverage_context — never selection's `high`), so the
    # evidence must not still claim "no matchup". Top-level confidence stays high: it qualifies ONLY the
    # dex enumeration facts, not the check coverage.
    assumptions = (["objective enumeration + opt-in ncp-grounded per-combo check_coverage "
                    "(its own confidence in check_coverage_context); combos never ranked"]
                   if check_grid is not None else
                   ["objective enumeration only; no matchup, no ranking"])
    out = {"kind": "selection", "format": fmt, "pick_size": pick,
           "bring_size": len(members), "combos": combos, "notes": notes,
           "mega_routes": mega_routes,
           "keep_mega": keep_echo,
           "legality_checked": legality_status,
           "confidence": "high", "confidence_reason": None,
           "evidence": {"facts": [{"source": "dex", "ref": "types / base speed / Mega form+stone"}],
                        "assumptions": assumptions}}
    if check_grid is not None and check_context:
        out["check_coverage_context"] = check_context   # coverage confidence is not selection confidence
    return out


def _mega_label(o: dict[str, Any]) -> str:
    delta = []
    if o.get("form_types"):
        delta.append("type→" + "/".join(o["form_types"]))
    if o.get("form_base_speed") is not None:
        delta.append(f"Spe→{o['form_base_speed']}")
    keep = " [user keep]" if o.get("user_keep") else ""
    return f"{o['member']}→{o['form']}" + (f" ({', '.join(delta)})" if delta else "") + keep


def format_selection_md(d: dict[str, Any]) -> str:
    status = d.get("legality_checked")
    leg = (f"legality checked: {status}" if status else "legality assumed; run `validate` separately")
    lines = ["# " + i18n.t('sel_header', format=d['format'], bring=d['bring_size'],
                            pick=d['pick_size'], count=len(d['combos']), leg=leg)]
    for c in d["combos"]:
        head = " + ".join(c["members"])
        mega = (i18n.t('sel_mega_prefix') + " " + ", ".join(_mega_label(o) for o in c["mega_options"])
                if c["mega_options"] else i18n.t('sel_no_mega'))
        if c["multiple_mega_brought"]:
            mega += i18n.t('sel_multi_mega')
        spe = ", ".join(f"{s['member']} {s['base_speed']}" for s in c["speed_order"])
        lines.append(f"- **{head}**{mega}\n  - {i18n.t('sel_speed_order')} {spe}"
                     f"\n  - {i18n.t('sel_types')} {', '.join(c['types_present'])}")
        cc = c.get("check_coverage")
        if cc:
            dist = cc.get("grade_distribution") or {}
            holes = cc.get("holes") or []
            hole_txt = (", ".join(h["opponent"] for h in holes) if holes
                        else i18n.t('sel_chk_holes_none'))
            lines.append(f"  - {i18n.t('sel_chk_label')} "
                         f"C2:{dist.get('C2',0)} C1:{dist.get('C1',0)} C0:{dist.get('C0',0)}; "
                         f"{i18n.t('sel_chk_holes')} {hole_txt}")
    if d.get("notes"):
        lines.append("\n## " + i18n.t('notes'))
        lines += [f"- {n}" for n in d["notes"]]
    return "\n".join(lines)
