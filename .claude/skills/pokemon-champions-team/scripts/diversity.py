"""Explicit overlap views; never a team-strength metric or a recommendation."""
from __future__ import annotations

import random
import secrets


def validate(draw=None, seed=None, *, seen=False, order=None):
    if draw is not None and (type(draw) is not int or draw < 1):
        raise ValueError("draw must be a positive integer")
    if seed is not None and type(seed) is not int:
        raise ValueError("seed must be an integer")
    if draw is None and order != "diversity" and (seed is not None or seen):
        raise ValueError("seed/seen-teams require frame --draw or observed --order diversity")


def draw_frames(skeletons, count, seed=None, shown=(), group_fn=lambda name: name):
    """Keep the full receipt-bound skeleton array intact; return only a selected-id view."""
    validate(count, seed)
    seed = secrets.randbits(64) if seed is None else seed
    rng = random.Random(seed)
    cores = {s["frame_id"]: {group_fn(c["species"]) for c in s.get("core_candidates") or []
                           if c.get("species")} for s in skeletons}
    seen = [{group_fn(n) for n in row} for row in shown]
    def repeated(core):
        return bool(core) and any(len(core & team) >= max(1, len(core) - 1) for team in seen)
    remaining, drawn, steps = list(skeletons), [], []
    for _ in range(min(count, len(remaining))):
        fresh = [s for s in remaining if not repeated(cores[s["frame_id"]])]
        pool = fresh or remaining
        shared = {s["frame_id"]: max((len(cores[s["frame_id"]] & cores[o["frame_id"]]) for o in drawn), default=0)
                  for s in pool}
        least = min(shared.values())
        pool = [s for s in pool if shared[s["frame_id"]] == least]
        weights = [max(0, (s.get("prevalence") or {}).get("count") or 0) for s in pool]
        pick = rng.choices(pool, weights=weights if any(weights) else None, k=1)[0]
        steps.append({"frame_id": pick["frame_id"], "max_shared_core": least,
                      "prevalence_count": (pick.get("prevalence") or {}).get("count"),
                      "repeats_shown_core": repeated(cores[pick["frame_id"]]),
                      "repeat_unavoidable": bool(seen and not fresh)})
        drawn.append(pick)
        remaining.remove(pick)
    return {"seed": seed, "requested": count, "returned": len(drawn),
            "frame_ids": [s["frame_id"] for s in drawn], "steps": steps,
            "identity_basis": "species_clause_group", "shown_teams": len(seen),
            "note": "Prefer cores unlike shown teams, then minimize maximum core overlap with earlier draws; "
                    "sample tied frames by observed team count. Empty/zero-count pools use uniform ties. "
                    "This view neither alters the full frame receipt nor measures strength."}


def diverse_page(rows, limit, shown=(), seed=None):
    """Greedy minimum maximum roster overlap, with constraint overlap ahead of evidence tie-breaks.

    rows arrive in stable tier/recency/id order. `_roster` uses proven actual forms. Exact already-shown
    rosters and duplicate rosters are excluded; the original stored rows and ids are retained.
    """
    seen = [set(r) for r in shown]
    previous = set(map(frozenset, seen))
    remaining, excluded = [], 0
    for row in rows:
        if frozenset(row["_roster"]) in previous:
            excluded += 1
        else:
            remaining.append(row)
    if seed is not None:
        random.Random(seed).shuffle(remaining)
    page = []
    wanted = len(remaining) if limit is None else limit
    while remaining and len(page) < wanted:
        overlap = {id(row): max((len(row["_roster"] & other) for other in seen), default=0) for row in remaining}
        pick = min(remaining, key=lambda row: (-row["overlap"]["count"], overlap[id(row)]))
        page.append({**pick, "diversity": {"max_shared_members": overlap[id(pick)], "against_rosters": len(seen)}})
        roster = pick["_roster"]
        seen.append(roster)
        remaining = [row for row in remaining if row["_roster"] != roster]
    return page, excluded
