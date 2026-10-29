#!/usr/bin/env python
"""Environment structural landscape (UEP P4): how REAL successful teams are structurally laid out.

The anti-mediocrity data leg (§A.C ②): instead of guessing "what a team usually looks like" from
training priors, the AI reads the real library's structural DISTRIBUTIONS — speed-control modes,
functional-role norms, observed co-occurring cores — and builds its OWN team against them.

Discipline (plan §P4 + §A.D, pinned by tests):
- COUNTS + sample + share only; never a strength ordering, never a best/recommended anything.
  Field naming guard: observed_* / sample_count / source_kind; best_* / recommended_* / *_score
  are banned (test walks the key tree).
- The library takes part ONLY as aggregation/co-occurrence — no whole team is ever echoed here
  (that is `search`/`show`'s AI-facing lane, with its own disclosure).
- Same yardstick as every other structural consumer: each team is projected through `profile`
  (UEP S), so landscape norms and a candidate's profile are directly comparable.
- Single/double never mixed: the caller loads ONE format's library (repset.load_teams).
- `order` is a VIEW ordering (meta_conformance knob: proven=common-first, off_meta=rare-first over
  the SAME facts) — it reorders, it never scores.

The meta partner_pairs fallback planned for a thin library is NOT wired: both formats currently
exceed the thin bar (勘误①), so it would be dead code — a thin library is flagged `thin: true`
honestly instead (the fallback lands with the singles data track if the need returns).
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Any, Callable

import team_profile
import team_i18n as i18n
from mega_facts import mega_slot_distribution
from repset import MIN_SAMPLE
import query_identity
import topmeta

# Below this many observed teams the distributions are too sparse to read as norms — flagged, never
# silently served as solid (mirrors repset's per-species honesty, at library scale).
THIN_BAR = 30
MAX_CORES = 20
# Opt-in full offense/defense aggregation (`aspects=`): the profile vectors are already computed
# per team, so these cost only the aggregation — they are a VIEW opt-in (output size), not a
# compute gate.
ASPECTS = ("offense", "defense")


def high_sample(teams: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select published joint records by the shared performance bar, without inventing identities.

    The caller supplies native current-rule rows only. Public rows cannot reproduce Top Meta's
    private cross-partition player dedup, so the counting unit remains a shipped library record.
    """
    selected = []
    composition = Counter({tier: 0 for tier in topmeta.HIGH_TIERS})
    for team in teams:
        tier = topmeta.high_tier((team.get("provenance") or {}).get("performance"))
        if tier is not None:
            selected.append(team)
            composition[tier] += 1
    return selected, {
        "scope": "high", "sample_unit": "shipped_team_record",
        "criteria": topmeta.high_criteria(), "composition": dict(composition),
        "examined_records": len(teams), "eligible_records": len(selected),
        "excluded_records": len(teams) - len(selected),
        "cross_partition_identity_dedup": False, "handover_included": False,
        "same_counting_units_as_topmeta": False,
    }


def _team_species(team: dict[str, Any]) -> list[str]:
    return sorted({m.get("species") for m in team.get("pokemon", []) if m.get("species")})


def _matches(team: dict[str, Any], flt: dict[str, Any]) -> bool:
    """Exact battle-form membership, optionally qualified by a held item. Project both sides first."""
    return any(m.get("species") == flt["species"]
               and (not flt.get("item") or m.get("item") == flt["item"])
               for m in team.get("pokemon", []))


def _filter_label(flt: dict[str, Any]) -> str:
    return f"{flt['species']} @ {flt['item']}" if flt.get("item") else flt["species"]


def landscape_from_teams(teams: list[dict[str, Any]], *, fmt: str,
                         dex_fn: Callable, move_fn: Callable, item_fn: Callable,
                         filter_members: list[dict[str, Any]] | None = None,
                         order: str = "common_first",
                         max_cores: int = MAX_CORES,
                         aspects: list[str] | None = None) -> dict[str, Any]:
    """Aggregate structural distributions over `teams` (one format's real library). Pure given the
    injected fact tables; `filter_members` ({species, item?} entries) narrows observed_cores to teams
    matching ALL actual forms. `aspects` opts into the full
    offense/defense presence distributions (see ASPECTS)."""
    aspects = [a for a in (aspects or []) if a]
    bad = sorted(set(aspects) - set(ASPECTS))
    if bad:
        raise ValueError(f"unknown landscape aspect(s): {', '.join(bad)}; "
                         f"allowed: {', '.join(ASPECTS)}")
    teams, identities = query_identity.battle_view(teams, filter_members, dex_fn=dex_fn, item_fn=item_fn)
    filter_members = [{**f, "species": identities(f) or "\0UNRESOLVED_FORM"} for f in filter_members or []]
    sample = len(teams)
    profiles = [team_profile.profile(t, dex_fn=dex_fn, move_fn=move_fn, item_fn=item_fn)
                for t in teams]
    mega_slots = mega_slot_distribution(profiles)

    # --- speed-control mode distribution (team-level presence; a team can carry several modes) ----
    mode_counts: Counter = Counter()
    for p in profiles:
        for mode in p["speed_control_mode"]["modes"]:
            mode_counts[mode] += 1
    speed_control_modes = {
        mode: {"count": c, "share": round(c / sample, 3) if sample else 0.0}
        for mode, c in sorted(mode_counts.items(), key=lambda kv: (-kv[1], kv[0]))}

    # --- structural signals: per-tag presence frequency (the checklist vocabulary, via profile) ----
    tag_presence: Counter = Counter()
    tag_hists: dict[str, Counter] = {}
    for p in profiles:
        for tag, rc in p["role_composition"].items():
            if rc["count"] > 0:
                tag_presence[tag] += 1
            tag_hists.setdefault(tag, Counter())[rc["count"]] += 1
    structural_signals = {
        tag: {"count": c, "share": round(c / sample, 3) if sample else 0.0}
        for tag, c in sorted(tag_presence.items(), key=lambda kv: (-kv[1], kv[0]))}
    role_composition_norms = {
        tag: {str(n): c for n, c in sorted(hist.items())}
        for tag, hist in sorted(tag_hists.items())}

    # --- aspects= opt-in: offense/defense presence distributions (a team counts ONCE per type it
    # has the property for — same profile vocabulary, so a candidate's own vector reads directly
    # against these) -------------------------------------------------------------------------------
    def _presence(counts: Counter) -> dict[str, dict[str, Any]]:
        return {t: {"count": c, "share": round(c / sample, 3) if sample else 0.0}
                for t, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))}

    aspect_norms: dict[str, Any] = {}
    if "offense" in aspects:
        off: dict[str, Counter] = {k: Counter() for k in (
            "stab_attack_types", "other_attack_types", "covered_defending_types",
            "thin_defending_types", "hard_gap_defending_types")}
        for p in profiles:
            for key, cnt in off.items():
                for tp in (p["offense"].get(key) or []):
                    cnt[tp] += 1
        aspect_norms["offense_norms"] = {k: _presence(c) for k, c in off.items()}
    if "defense" in aspects:
        resist: Counter = Counter()
        immune: Counter = Counter()
        stacked: Counter = Counter()
        for p in profiles:
            d = p["defense"]
            for tp, n in (d.get("resist_counts") or {}).items():
                if n:
                    resist[tp] += 1
            for tp, n in (d.get("immune_counts") or {}).items():
                if n:
                    immune[tp] += 1
            for c in d.get("weakness_concentration") or []:
                stacked[c["type"]] += 1
        aspect_norms["defense_norms"] = {"resist_presence": _presence(resist),
                                         "immune_presence": _presence(immune),
                                         "stacked_weakness_types": _presence(stacked)}

    # --- observed cores: co-occurrence counts (pairs; or co-members of the filter set) ------------
    filter_members = [f for f in (filter_members or []) if f.get("species")]
    if filter_members:
        pool = [t for t in teams if all(_matches(t, f) for f in filter_members)]
        fspecies = {f["species"] for f in filter_members}
        co: Counter = Counter()
        for t in pool:
            for sp in _team_species(t):
                if sp not in fspecies:
                    co[sp] += 1
        entries = [{"members": [sp], "count": c,
                    "share": round(c / len(pool), 3) if pool else 0.0}
                   for sp, c in co.items() if c >= MIN_SAMPLE]
        core_basis = {"filter": [_filter_label(f) for f in filter_members],
                      "teams_with_filter": len(pool)}
    else:
        pairs: Counter = Counter()
        for t in teams:
            for a, b in combinations(_team_species(t), 2):
                pairs[(a, b)] += 1
        entries = [{"members": list(k), "count": c,
                    "share": round(c / sample, 3) if sample else 0.0}
                   for k, c in pairs.items() if c >= MIN_SAMPLE]
        core_basis = {"filter": None, "teams_with_filter": None}
    # Two explicit sorts (self-audit 2026-07-02: a sign-flip combined with reverse= cancelled out,
    # making rare_first a silent no-op — and reverse also inverted the alphabetical tiebreak).
    if order == "rare_first":
        entries.sort(key=lambda e: (e["count"], e["members"]))
    else:
        entries.sort(key=lambda e: (-e["count"], e["members"]))
    cores_total = len(entries)                    # cores above support BEFORE the display cap
    observed_cores = entries[:max_cores]

    thin = sample < THIN_BAR
    notes = [
        "distributions over OBSERVED real teams (counts + share + sample), aggregated through the "
        "shared `profile` vocabulary — read them as 'how the environment is laid out', never as a "
        "strength ranking, a best core, or a recommended archetype.",
        "observed_cores = co-occurrence counts (support >= "
        f"{MIN_SAMPLE}); `order` (common_first | rare_first via the meta_conformance knob) selects "
        "WHICH end of the count distribution the top-N view surfaces — a view ordering by explicit "
        "count, never a score. When cores_total > the display cap the two orders show DIFFERENT ends "
        "by design (the off-meta knob wants the rare tail); cores_total is the full count above support.",
        "the library participates as aggregation only — no whole team is echoed here; use "
        "`search`/`show` (AI-facing, disclosure-marked) to decompose an individual team.",
    ]
    if thin:
        notes.append(f"THIN library ({sample} < {THIN_BAR} teams): distributions are sparse — read "
                     "with caution; the meta partner_pairs fallback is not wired (deliberate, see "
                     "module docstring).")
    if aspect_norms:
        notes.append("offense_norms / defense_norms (aspects=) are team-level PRESENCE "
                     "distributions over the same profile vocabulary: a team counts once per type "
                     "it has the property for; stacked_weakness_types uses diagnose's concentration "
                     "bar (>=2 members weak to the type). How real teams are laid out — never an "
                     "adequacy target or a coverage requirement.")
    if sample and not observed_cores:
        # An empty cores list on a non-empty partition means NO pair reaches the support bar — say
        # so explicitly, or the reader misreads it as "this metagame has no cores" (2026-07-03
        # system test: the 116-team singles partition returns zero cores and gave no signal why).
        notes.append(f"observed_cores is EMPTY because no co-occurring pair reaches support >= "
                     f"{MIN_SAMPLE} in this {sample}-team partition (co-occurrence too dispersed) — "
                     "NOT because the metagame lacks cores; ground per-species via repset/search "
                     "instead.")
    return {
        "kind": "landscape",
        "identity_basis": "dex_battle_form", "identity_gaps": identities.gaps,
        "format": fmt,
        "sample_count": sample,
        "source_kind": "observed_real_teams",
        "thin": thin,
        "order": order,
        "aspects": aspects,
        "speed_control_modes": speed_control_modes,
        "mega_slot_distribution": mega_slots,
        "structural_signals": structural_signals,
        "role_composition_norms": role_composition_norms,
        **aspect_norms,
        "observed_cores": observed_cores,
        "cores_total": cores_total,
        "core_basis": core_basis,
        "confidence": "low" if thin else "medium",
        "confidence_reason": "thin-observed-sample" if thin else "observed-sample",
        "notes": notes,
    }


def format_landscape_md(d: dict[str, Any]) -> str:
    lines = [i18n.t('land_header', fmt=d["format"], n=d["sample_count"], conf=d["confidence"])]
    population = d.get("population") or {}
    if population.get("scope") == "high":
        criteria = population["criteria"]
        lines.append(i18n.t('land_high_sample', n=d["sample_count"],
                            total=population["examined_records"],
                            placing=criteria["tournament_top8"]["placing_at_most"],
                            field=criteria["tournament_top8"]["field_size_at_least"],
                            rank=criteria["ladder_top"]["rank_at_most"],
                            rating=criteria["ladder_top"]["or_rating_at_least"]))
    if d.get("thin"):
        lines.append("> ⚠️ " + i18n.t('land_thin', n=d["sample_count"], bar=THIN_BAR))
    lines.append(f"\n## {i18n.t('land_modes')}")
    lines += [f"- {m}: {v['count']} ({v['share']})" for m, v in d["speed_control_modes"].items()]
    mega = d.get("mega_slot_distribution") or {}
    if mega:
        lines.append(f"\n## {i18n.t('land_mega_slots')}")
        buckets = mega.get("buckets") or {}
        lines += [f"- {b}: {v['count']} ({v['share']})" for b, v in buckets.items()]
        lines.append(f"- average_registered_mega_slots: {mega.get('average_registered_mega_slots')}")
    lines.append(f"\n## {i18n.t('land_signals')}")
    lines += [f"- {t}: {v['count']} ({v['share']})" for t, v in d["structural_signals"].items()]
    for sec_key, out_key in (("land_offense", "offense_norms"), ("land_defense", "defense_norms")):
        if d.get(out_key):
            lines.append(f"\n## {i18n.t(sec_key)}")
            for group, dist in d[out_key].items():
                vals = ", ".join(f"{t} {v['count']} ({v['share']})" for t, v in dist.items())
                lines.append(f"- {group}: {vals or '—'}")
    lines.append(f"\n## {i18n.t('land_cores', order=d['order'])}")
    basis = d.get("core_basis") or {}
    if basis.get("filter"):
        lines.append("_" + i18n.t('land_filter', filt=", ".join(basis["filter"]),
                                  n=basis["teams_with_filter"]) + "_")
    lines += [f"- {' + '.join(c['members'])}: {c['count']} ({c['share']})"
              for c in d["observed_cores"]] or [f"- {i18n.t('ctx_audit_clean')}"]
    if d.get("notes"):
        lines.append(f"\n## {i18n.t('notes')}")
        lines += [f"- {n}" for n in d["notes"]]
    return "\n".join(lines)
