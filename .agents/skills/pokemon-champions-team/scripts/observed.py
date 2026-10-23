#!/usr/bin/env python
"""observed-team retrieval (UEP P7, route (b)): the AI-FACING evidence source over the real-team
library — teams satisfying / partially satisfying the user's constraints, laid out as FACT rows:
explicit-predicate constraint overlap (hit counts, never a similarity scalar), provenance evidence
tier, recency, raw performance fact tags, and structural signals through the shared profile
vocabulary.

Hard boundary (§A.D, pinned by tests):
- RETRIEVAL, never generation — every row IS a stored team (id-addressable via `show`); nothing is
  synthesized or blended.
- Ordering is ONLY evidence tier / recency / constraint overlap. No strength ranking exists here,
  and none can be assembled from these columns (counts + categories only).
- The output marks `consumer: ai-facing-evidence`: decompose rows into structural facts and
  trade-offs to build YOUR OWN team; never echo a stored team to the user as "the answer"
  (library guardrail — no netdecking).
"""
from __future__ import annotations

from typing import Any, Callable

import teamid
from landscape import THIN_BAR                      # ONE honesty bar for the library operators
from libsearch import base_key, team_view

# Ordered provenance-KIND ladder — design §12's evidence-tier ORDERING as categories, never a
# score. The finer §12 rungs (official top cut vs major vs minor event) are not derivable from
# current provenance (no event metadata ships), so placing/record/field_size stay RAW fact tags on
# each row for the reader to weigh transparently. A kind outside the ladder maps to the DECLARED
# sink category "unknown" (sorts last). Published configurations without asserted performance
# use that category; a data test rejects unreviewed kinds on supplied performance facts.
OBSERVED_TIERS = ("tournament", "community", "ladder")
ORDERS = ("overlap", "tier", "recent")


def evidence_tier(team: dict[str, Any]) -> str:
    kind = ((team.get("provenance") or {}).get("performance") or {}).get("kind")
    return kind if kind in OBSERVED_TIERS else "unknown"


def _tier_index(tier: str) -> int:
    return OBSERVED_TIERS.index(tier) if tier in OBSERVED_TIERS else len(OBSERVED_TIERS)


def _bases(names: list[str] | None) -> dict[str, list[str]]:
    """{base_key: [constraint names]} for a group. Base-insensitive matching cannot tell Mega X
    from Y, so two names folding to one base BOTH count as hit when the base is present — the
    honest reading, and `of` stays the number of names the user actually gave (a dict keyed by
    base silently collapsed 'Mega Charizard X'+'Y' to of:1 with the wrong form reported;
    self-audit 2026-07-03)."""
    out: dict[str, list[str]] = {}
    for n in names or []:
        if n:
            bucket = out.setdefault(base_key(n), [])
            if n not in bucket:                  # dedupe EXACT repeats (a name given twice must not
                bucket.append(n)                 # inflate count/of); distinct names — Mega X vs Y —
    return out                                    # fold to one base yet BOTH stay, counted honestly


def retrieve(teams: list[dict[str, Any]], *, fmt: str,
             constraints: dict[str, Any] | None = None,
             profile_batch_fn: Callable[[list[dict]], list[dict]] | None = None,
             order: str | None = None, limit: int | None = 10) -> dict[str, Any]:
    """The retrieval. Pure given `profile_batch_fn` (profiles the RETURNED page only — structural
    signals are a view on the page, not a scan of the library).

    `constraints` (canonical names): locked / prefer / species (ad-hoc members) count into the
    explicit overlap; avoid_species (base-folded) AND avoid_items (exact held item) EXCLUDE
    mechanically (hard user constraints — excluded rows are counted, never silently dropped);
    owned yields owned_coverage as a fact and NEVER excludes; owned_only is NOT a filter here
    either — it is echoed under constraints_not_enforced (partially-owned teams are still
    evidence to learn from; external audit 2026-07-03: a consumed-looking echo with no
    enforcement is a silent lie). `limit=0` = counts only, no bodies (search parity); None =
    unlimited (pure-API callers only — the CLI defaults to a 10-row page)."""
    if limit is not None and limit < 0:
        raise ValueError(f"limit must be >= 0 (0 = counts only), got {limit}")
    c = constraints or {}
    groups = {name: _bases(c.get(name)) for name in ("locked", "prefer", "species")
              if c.get(name)}
    avoid = set(_bases(c.get("avoid_species")))
    avoid_items = {i for i in (c.get("avoid_items") or []) if i}
    owned = set(_bases(c.get("owned")))
    requested_order = order
    if order not in ORDERS:
        order = "overlap" if groups else "tier"     # same default whether omitted or invalid

    excluded_by_avoid = 0
    scored: list[dict[str, Any]] = []
    for t in teams:
        members = [m for m in t.get("pokemon", []) if m.get("species")]
        member_bases = {base_key(m.get("species", "")) for m in members}
        if (avoid & member_bases) or (avoid_items
                                      and any(m.get("item") in avoid_items for m in members)):
            excluded_by_avoid += 1
            continue
        overlap: dict[str, Any] = {}
        hit_bases: set[str] = set()
        for gname, gbases in groups.items():
            matched = set(gbases) & member_bases
            present = sorted(n for b in matched for n in gbases[b])
            overlap[gname] = {"present": present, "count": len(present),
                              "of": sum(len(v) for v in gbases.values())}
            hit_bases |= matched
        # The ordering total counts DISTINCT species hit across all groups — a name listed in both
        # locked and --species must not outweigh a genuine second-constraint hit (self-audit
        # 2026-07-03). Per-group counts above keep the sum decomposable.
        overlap["count"] = len(hit_bases)
        rec: dict[str, Any] = {
            "team": t,
            "id": teamid.compute_id(t),             # computed ONCE (sort tiebreak + page row)
            "evidence_tier": evidence_tier(t),
            "fetched_at": (t.get("provenance") or {}).get("fetched_at"),
            "overlap": overlap,
        }
        if owned:
            rec["owned_coverage"] = {"owned_members": len(member_bases & owned),
                                     "of": len(member_bases)}
        scored.append(rec)

    if limit == 0:
        page: list[dict[str, Any]] = []             # counts-only: no ordering work, no profiles
    else:
        # Deterministic multi-pass ordering (stable sorts, LAST key is primary): id -> recency
        # desc, then tier + the chosen primary (recent IS recency-primary, so no further pass).
        # Explicit branches per key, never a sign-flip trick (the rare_first inversion lesson).
        scored.sort(key=lambda r: r["id"])
        scored.sort(key=lambda r: r.get("fetched_at") or "", reverse=True)
        if order != "recent":
            scored.sort(key=lambda r: _tier_index(r["evidence_tier"]))
            if order == "overlap":
                scored.sort(key=lambda r: r["overlap"]["count"], reverse=True)
        page = scored[:limit] if limit is not None else scored

    profiles = (profile_batch_fn([r["team"] for r in page]) or []) if profile_batch_fn else []
    rows = []
    for i, r in enumerate(page):
        row = team_view(r["team"])
        row["evidence_tier"] = r["evidence_tier"]
        row["overlap"] = r["overlap"]
        if "owned_coverage" in r:
            row["owned_coverage"] = r["owned_coverage"]
        p = profiles[i] if i < len(profiles) and isinstance(profiles[i], dict) else None
        if p:
            row["structural_signals"] = {
                "speed_control_modes": (p.get("speed_control_mode") or {}).get("modes"),
                "roles": {tag: rc.get("count")
                          for tag, rc in (p.get("role_composition") or {}).items()},
                "mega_candidates": [m.get("member") for m in (p.get("mega_usage") or [])],
            }
        rows.append(row)

    # The honesty bar measures the pool the reader actually retrieves from — AFTER the avoid
    # exclusion, not the raw partition (a heavy avoid list can thin a healthy partition below the
    # bar; self-audit 2026-07-03).
    pool = len(scored)
    thin = pool < THIN_BAR
    notes = [
        "RETRIEVAL, not generation: every row is a stored real team (fetch its full config via "
        "`show <id>`); nothing here is synthesized.",
        "ordering = " + order + " (allowed: overlap | tier | recent) — evidence tier is the "
        "provenance-kind ladder " + " > ".join(OBSERVED_TIERS) + " (categories, never a score; "
        "an unrecognized/missing kind is the declared sink 'unknown' and sorts last); overlap is "
        "an explicit constraint-member hit count (top-level count = DISTINCT species hit; "
        "per-group breakdowns keep it decomposable). None of these is a strength ranking.",
        "performance is a RAW fact tag with provenance (record/placing/field_size as published) — "
        "finer §12 rungs (official top cut vs minor event) are not derivable from shipped "
        "provenance, so no such distinction is encoded.",
        "structural_signals is a three-aspect VIEW (speed control / roles / mega) of the profile "
        "vocabulary, computed for this page only — NOT the full structural picture; decompose "
        "interesting rows via `show <id>` + diagnose/profile before drawing offense/defense "
        "conclusions.",
        "species matching is base-folded (Mega ≡ base, X ≡ Y — search parity); landscape's core "
        "filter is item-aware instead (Mega X ≠ Y). For form-precise retrieval, use `search` "
        "with an item slot.",
    ]
    if requested_order is not None and requested_order not in ORDERS:
        notes.append(f"requested order {requested_order!r} is not one of {list(ORDERS)} — "
                     f"fell back to the default ({order}).")
    if thin:
        notes.append(f"THIN evidence pool ({pool} of {len(teams)} scanned survive the avoid "
                     f"exclusion; bar {THIN_BAR}): sparse evidence — read rows as anecdotes, not "
                     "a distribution. (The meta partner-graph 'near core' fallback remains "
                     "deliberately unwired while the main partitions are full.)")
    not_enforced = ["owned_only"] if c.get("owned_only") else []
    if not_enforced:
        notes.append("owned_only is NOT a filter in this operator (constraints_not_enforced): "
                     "partially-owned teams stay decomposable evidence — read owned_coverage and "
                     "judge; validate/fill are where owned_only mechanically binds.")
    return {
        "kind": "observed-team-retrieval",
        "consumer": "ai-facing-evidence",
        "game_format": fmt,
        "constraints_view": {
            **{k: c.get(k)
               for k in ("locked", "prefer", "species", "avoid_species", "avoid_items", "owned_only")
               if c.get(k)},
            # owned can be a 100+ roster: echo the COUNT (per-row owned_coverage carries the facts)
            **({"owned": {"count": len(c.get("owned") or [])}} if c.get("owned") else {}),
        },
        # Everything echoed above without a not_enforced tag IS mechanically applied here (avoid =
        # species base-fold AND exact held item); a field this operator does not act on must say so.
        "constraints_not_enforced": not_enforced,
        "scanned": len(teams),
        "excluded_by_avoid": excluded_by_avoid,
        "pool_after_avoid": pool,
        "with_overlap": sum(1 for r in scored if r["overlap"]["count"] > 0),
        "returned": len(rows),
        "thin": thin,
        "ordering": {"key": order, "allowed": list(ORDERS)},
        "evidence_tiers": list(OBSERVED_TIERS) + ["unknown"],
        "teams": rows,
        "disclosure": "AI-facing raw observed teams (library guardrail): decompose into structural "
                      "facts / trade-offs to build YOUR OWN team; do NOT echo a stored team "
                      "verbatim to the user as the answer, and this table is NOT a user-facing "
                      "recommendation slate.",
        "notes": notes,
    }
