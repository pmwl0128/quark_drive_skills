#!/usr/bin/env python
"""L3 replace-impact diff — `replace_impact`.

Swap ONE member for a candidate and report the OBJECTIVE before/after diff across the four diagnose
aspects — never a "better/worse" verdict or a score. It REUSES diagnose: run each aspect
on the before- and after-team and diff the structured outputs.

The candidate is a CONCRETE member (species + ability/item/nature/spread/moves), not a bare species:
offense and roles depend on its moveset, so a bare species can't be diffed there. The AI supplies the
set it wants to try (its own pick, a meta modal, or the resolver). Everything injectable for tests.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import team_io  # noqa: E402
import team_i18n as i18n  # noqa: E402
import query_identity
from diagnose import (  # noqa: E402
    diagnose_defense, diagnose_offense, diagnose_speed, diagnose_roles,
)


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _conc_map(d: dict[str, Any]) -> dict[str, tuple[int, list[str]]]:
    return {c["type"]: (c["weak_count"], c.get("weak_members", []))
            for c in d.get("weakness_concentration", [])}


def _defense_diff(b: dict[str, Any], a: dict[str, Any]) -> dict[str, Any]:
    """Shared-weakness concentration shifts (the real defensive risk) — types whose weak-member count
    went up / down across the swap."""
    mb, ma = _conc_map(b), _conc_map(a)
    worsened, eased = [], []
    for t in sorted(set(mb) | set(ma)):
        cb = mb.get(t, (0, []))[0]
        ca, members_a = ma.get(t, (0, []))
        if ca > cb:
            worsened.append({"type": t, "from": cb, "to": ca, "weak_members": members_a})
        elif ca < cb:
            eased.append({"type": t, "from": cb, "to": ca, "weak_members": members_a})
    return {"shared_weakness_worsened": sorted(worsened, key=lambda x: -x["to"]),
            "shared_weakness_eased": sorted(eased, key=lambda x: -x["from"])}


def _offense_diff(b: dict[str, Any], a: dict[str, Any]) -> dict[str, Any]:
    """Coverage shifts across ALL reliability tiers (audit 2026-06-24): not just hard_gaps but also
    `thin` (only non-STAB coverage) and `centralized` (single bearer = a point of failure) — a
    covered->thin/centralized slide is a real trade-off a hard_gaps-only diff would hide."""
    def types(d: dict, k: str) -> set[str]:
        return {c["type"] if isinstance(c, dict) else c for c in (d.get(k) or [])}
    hb, ha = types(b, "hard_gaps"), types(a, "hard_gaps")
    tb, ta = types(b, "thin"), types(a, "thin")
    cb, ca = types(b, "centralized"), types(a, "centralized")
    return {"hard_gaps_added": sorted(ha - hb), "hard_gaps_removed": sorted(hb - ha),
            "thin_added": sorted(ta - tb), "thin_removed": sorted(tb - ta),
            "centralized_added": sorted(ca - cb), "centralized_removed": sorted(cb - ca),
            "gaps_confirmed": a.get("gaps_confirmed")}


def _speed_diff(b: dict[str, Any], a: dict[str, Any], out_sp: str, in_sp: str) -> dict[str, Any]:
    sb = {m["species"]: m.get("speed") for m in b.get("members", [])}
    sa = {m["species"]: m.get("speed") for m in a.get("members", [])}
    out_speed, in_speed = sb.get(out_sp), sa.get(in_sp)
    order = a.get("order", [])
    pos = next((i + 1 for i, x in enumerate(order) if _norm(x["species"]) == _norm(in_sp)), None)
    return {"out": {"species": out_sp, "speed": out_speed},
            "in": {"species": in_sp, "speed": in_speed},
            "delta": (in_speed - out_speed) if (in_speed is not None and out_speed is not None) else None,
            "in_team_speed_rank": pos, "team_size": a.get("team_size")}


def _roles_diff(b: dict[str, Any], a: dict[str, Any]) -> dict[str, Any]:
    """Team role-coverage shifts: roles wholly lost / gained, and roles that lost redundancy (a role
    still present but down to fewer bearers — losing the only / a backup carrier)."""
    cb, ca = b.get("coverage", {}), a.get("coverage", {})
    lost, req_lost, gained, thinner, thicker = [], [], [], [], []
    for tag in sorted(set(cb) | set(ca)):
        pb, pa = cb.get(tag, {}), ca.get(tag, {})
        label = pa.get("label") or pb.get("label") or tag
        # tier-aware: a LEVEL-1 (required) role wholly lost is a focused-attention gap, ranked
        # ahead of level-2 losses. The role's tier is identical before/after (same format).
        required = (pa.get("expectation") or pb.get("expectation") or "optional") == "required"
        if pb.get("present") and not pa.get("present"):
            (req_lost if required else lost).append(label)
        elif not pb.get("present") and pa.get("present"):
            gained.append(label)
        elif pb.get("present") and pa.get("present"):
            nb, na = len(pb.get("bearers") or []), len(pa.get("bearers") or [])
            if na < nb:
                thinner.append({"role": label, "bearers": f"{nb}->{na}", "required": required})
            elif na > nb:
                thicker.append({"role": label, "bearers": f"{nb}->{na}"})
    # roles_lost keeps EVERY lost label (level-1 first) for backward-compatible consumers;
    # roles_lost_required isolates the level-1 subset for prominent rendering.
    return {"roles_lost": req_lost + lost, "roles_lost_required": req_lost,
            "roles_gained": gained,
            "redundancy_reduced": thinner, "redundancy_increased": thicker}


def replace_impact(team_dict: dict[str, Any], out_species: str, candidate: dict[str, Any], *,
                   dex_fn: Callable[[list[str]], dict[str, dict]],
                   move_fn: Callable[[list[str]], dict[str, dict]],
                   item_fn: Callable | None = None,
                   validate_fn: Callable[[dict], dict] | None = None) -> dict[str, Any]:
    members = team_dict.get("pokemon", [])
    if not candidate or not candidate.get("species"):
        return {"kind": "replace_impact", "result": "skipped",
                "note": "candidate must be a concrete member with at least a `species`"}
    identities = query_identity.Forms(members + [candidate, {"species": out_species}],
                                      dex_fn=dex_fn, item_fn=item_fn or (lambda _: {}))
    indices = [i for i, m in enumerate(members) if identities(m) == out_species]
    if len(indices) != 1 or identities(candidate) is None:
        return {"kind": "replace_impact", "result": "skipped", "identity_basis": "dex_battle_form",
                "identity_gaps": identities.gaps,
                "note": f"`{out_species}` must identify exactly one actual form on the team and the candidate form must be known"}
    index = indices[0]
    cand_species = identities(candidate)
    has_moves = bool(candidate.get("moves"))
    after_dict = {**team_dict,
                  "pokemon": [dict(candidate) if i == index else m for i, m in enumerate(members)]}
    battle_before, battle_after = identities.project([team_dict, after_dict])
    before = team_io.team_from_dict(battle_before, retain_registration=True)
    after = team_io.team_from_dict(battle_after, retain_registration=True)

    species = sorted({m.species for m in before.pokemon if m.species}
                     | {m.species for m in after.pokemon if m.species})
    registrations = [m._registration.get("species") for t in (before, after) for m in t.pokemon
                     if m._registration and m._registration.get("species")]
    facts = dex_fn(species + registrations) or {}
    moves = sorted({mv for t in (before, after) for m in t.pokemon for mv in m.moves})
    move_facts = move_fn(moves) or {}

    # Legality bottom-line: never present a diff on an ILLEGAL after-team as clean fact (the "legality
    # 靠代码兜底" iron rule). validate_fn runs the real validator on the after-team; an invalid result
    # is surfaced and caps confidence (audit 2026-06-24). Injectable / optional for hermetic tests.
    legality = None
    if validate_fn is not None:
        try:
            legality = validate_fn(after_dict)
        except Exception:
            legality = None

    defense = _defense_diff(diagnose_defense(before, facts), diagnose_defense(after, facts))
    speed = _speed_diff(diagnose_speed(before, facts, move_facts),
                        diagnose_speed(after, facts, move_facts), out_species, cand_species)
    # offense/roles depend on the candidate's MOVES — a bare species would make them meaningless (the
    # team looks like it lost all the candidate slot's coverage), so DON'T present them as fact: skip
    # with an explicit marker rather than emit a misleading empty diff (audit 2026-06-24).
    if has_moves:
        o_a = diagnose_offense(after, facts, move_facts)
        offense = _offense_diff(diagnose_offense(before, facts, move_facts), o_a)
        roles = _roles_diff(diagnose_roles(before, facts), diagnose_roles(after, facts))
        offense_unconfirmed = o_a.get("gaps_confirmed") is False
    else:
        offense = roles = {"skipped": "candidate has no moveset — offense/roles depend on its moves "
                                       "and were NOT diffed (would be meaningless)"}
        offense_unconfirmed = False

    # Confidence is DERIVED, not fixed: illegal after-team / missing moveset / unconfirmed coverage all
    # drop it to low with the reason (audit 2026-06-24 — the old fixed 'medium' hid these).
    reasons: list[str] = []
    if identities.gaps:
        reasons.append("battle-form identity or ability is incomplete (see identity_gaps)")
    if legality and legality.get("status") == "invalid":
        reasons.append("after-team is ILLEGAL (see legality.errors)")
    if not has_moves:
        reasons.append("candidate has no moveset (offense/roles not diffed)")
    if offense_unconfirmed:
        reasons.append("offense gaps unconfirmed (a member's moveset is non-authoritative)")
    confidence = "low" if reasons else "medium"

    notes = [
        "Objective before/after diff of ONE swap — facts only, NOT a 'better/worse' verdict or score. "
        "The AI weighs the trade.",
        "offense/roles reflect the candidate's GIVEN set (its moves); a different set changes them.",
    ]
    if legality and legality.get("status") == "invalid":
        notes.append("⚠️ the after-team does NOT pass validate — this diff is on an illegal team: "
                     + "; ".join(legality.get("errors") or []))
    if not has_moves:
        notes.append("⚠️ candidate moveset not given — only defense/speed (type/stat based) are diffed.")

    return {
        "kind": "replace_impact", "out": out_species, "in": cand_species,
        "identity_basis": "dex_battle_form", "identity_gaps": identities.gaps,
        "registration": {"out": dict(members[index]), "in": dict(candidate)},
        "legality": legality,
        "defense": defense, "offense": offense, "speed": speed, "roles": roles,
        "notes": notes,
        "confidence": confidence, "confidence_reason": "; ".join(reasons) or "vs-given-candidate-set",
        "evidence": {
            "facts": [{"source": "dex", "ref": "types / stats / abilities / learnset (both teams)"},
                      {"source": "builtin", "ref": "18x18 type chart + role-move taxonomy"}],
            "inputs": [{"source": "team-json", "ref": "before-team + candidate set"}],
            "method": "diff = diagnose(after) − diagnose(before), per aspect",
            "aspects_diffed": ["defense", "speed"] + (["offense", "roles"] if has_moves else []),
            "legality_checked": legality is not None,
        },
    }


def format_replace_impact_md(d: dict[str, Any]) -> str:
    if d.get("result") == "skipped":
        return f"# {i18n.t('rep_skip_header')}\n- {d.get('note')}"
    lines = [f"# {i18n.t('rep_main_header', out=d['out'], in_=d['in'])}"]
    leg = d.get("legality")
    if leg and leg.get("status") == "invalid":
        lines.append(f"\n> {i18n.t('rep_illegal')}{'; '.join(leg.get('errors') or [])}")
    elif leg and leg.get("status") == "unknown":
        lines.append(f"\n> {i18n.t('rep_legality_unknown')}")
    dd = d["defense"]
    if dd["shared_weakness_worsened"]:
        lines.append(f"\n## {i18n.t('rep_defense_worsened')}")
        for w in dd["shared_weakness_worsened"]:
            lines.append(f"- {w['type']}: {w['from']}→{w['to']} {i18n.t('rep_weak')} ({', '.join(w['weak_members'])})")
    if dd["shared_weakness_eased"]:
        lines.append(f"\n## {i18n.t('rep_defense_eased')}")
        for w in dd["shared_weakness_eased"]:
            lines.append(f"- {w['type']}: {w['from']}→{w['to']} {i18n.t('rep_weak')}")
    od = d["offense"]
    if od.get("skipped"):
        lines.append(f"\n## {i18n.t('rep_offense_roles')}\n- {i18n.t('rep_skipped')} {od['skipped']}")
    elif any(od.get(k) for k in ("hard_gaps_added", "hard_gaps_removed", "thin_added", "thin_removed",
                                 "centralized_added", "centralized_removed")):
        lines.append(f"\n## {i18n.t('rep_offense_coverage')}")
        for label_key, key in [("rep_gaps_removed", "hard_gaps_removed"),
                           ("rep_gaps_added", "hard_gaps_added"),
                           ("rep_now_thin", "thin_added"),
                           ("rep_no_longer_thin", "thin_removed"),
                           ("rep_now_centralized", "centralized_added"),
                           ("rep_no_longer_centralized", "centralized_removed")]:
            if od.get(key):
                lines.append(f"- {i18n.t(label_key)}: {', '.join(od[key])}")
    sp = d["speed"]
    lines.append(f"\n## {i18n.t('rep_speed')}\n- {sp['out']['species']} ({sp['out']['speed']}) → "
                 f"{sp['in']['species']} ({sp['in']['speed']})"
                 + (f", Δ{sp['delta']:+d}" if sp.get("delta") is not None else "")
                 + (f"; #{sp['in_team_speed_rank']}/{sp['team_size']} {i18n.t('rep_on_the_team')}" if sp.get("in_team_speed_rank") else ""))
    rd = d["roles"]
    if not rd.get("skipped") and (rd["roles_lost"] or rd["roles_gained"]
                                  or rd["redundancy_reduced"] or rd.get("redundancy_increased")):
        lines.append(f"\n## {i18n.t('rep_roles')}")
        req_lost = rd.get("roles_lost_required") or []
        opt_lost = [l for l in rd["roles_lost"] if l not in set(req_lost)]
        if req_lost:
            lines.append(f"- ⚠ {i18n.t('rep_role_lost_req')}: {', '.join(req_lost)}")
        if opt_lost:
            lines.append(f"- {i18n.t('rep_role_lost')}: {', '.join(opt_lost)}")
        if rd["roles_gained"]:
            lines.append(f"- {i18n.t('rep_role_gained')}: {', '.join(rd['roles_gained'])}")
        for t in rd["redundancy_reduced"]:
            lines.append(f"- {i18n.t('rep_role_thinner')}: {t['role']} {i18n.t('rep_bearers')} {t['bearers']}")
        for t in rd.get("redundancy_increased", []):
            lines.append(f"- {i18n.t('rep_role_more_redundant')}: {t['role']} {i18n.t('rep_bearers')} {t['bearers']}")
    lines += ["", *(f"> {n}" for n in d.get("notes", []))]
    return "\n".join(lines)
