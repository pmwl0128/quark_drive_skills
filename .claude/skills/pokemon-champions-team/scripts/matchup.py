#!/usr/bin/env python
"""Matchup-vs-meta-top-K (M2 L2) — OBJECTIVE FACTS ONLY.

For each of the team's members, report objective matchup facts against the meta's most-used
Pokemon: who outspeeds whom, how the opponent's STAB types hit us (type level), and how hard our
member's own moves hit each retained observed build (damage, via ncp). It assigns NO matchup score,
ranks NO member or opponent, and names NO "best" anything — it lays out facts the AI
reasons over.

Discipline / boundary (mirrors metalink.py):
  - the opponent LIST is the meta USAGE ranking — a published fact ("most-used"), not a synthesized
    threat assessment. It is NOT the marginal-mode threat auto-discovery that this skill
    forbids for tune's SP optimization: here every opponent is named, and we only *report* facts,
    never optimize a spread against them.
  - each ranked species expands into retained observed (item, ability) builds from the rule-scoped
    real-team library. Coverage is sample share, not strength; omitted sample mass stays explicit.
  - our side uses the registered team's ACTUAL set (the moves/nature/SP as brought).

Speed is the integer-exact Champions closed form (cliffs.champ_speed). Type effectiveness is the
built-in chart (typechart). Damage is the sibling ncp calculator, batched in one subprocess
(ncplink.damage_batch) so a members x opponents matrix is a single node call. All external lookups
are injected (dex_fn / set_fn / damage_fn) so the logic is unit-testable offline.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from cliffs import champ_speed, effective_speed, solve_outspeed, SPEED_ITEM_MULT, SP_CAP
from metalink import SPREAD_TO_SPS as _SPREAD_TO_SPS
from typechart import effectiveness_for_member
from rules import get_ruleset
from battle_effects import ko_hits_from_pct
from mega import effective_member_from_maps
import checks
import team_i18n as i18n
import battle_scope

SP_TOTAL_CAP = get_ruleset().sp_total_cap          # 66 SP across all stats (the real spend ceiling)

# Public matchup-battery scope. Callers choose their own Top-K within this shared contract: a quick
# diagnose/select view can stay small, while a deliberate full-field audit can request 60. Sixty is
# also the shipped opponent-cache partition boundary, so values above it do not have a consistent
# environment/reference meaning across formats.
MATCHUP_TOP_K_MIN = 1
MATCHUP_TOP_K_MAX = 60
MATCHUP_TOP_K_DEFAULT = 8


def normalize_top_k(value: Any, *, default: int = MATCHUP_TOP_K_DEFAULT) -> int:
    """Return a validated matchup Top-K integer.

    One owner for CLI, session, diagnose/select and slate boundaries. ``bool`` is rejected even
    though it subclasses ``int``: ``true`` must not silently become a Top-1 battery.
    """
    if value is None:
        value = default
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"top_k must be an integer in {MATCHUP_TOP_K_MIN}..{MATCHUP_TOP_K_MAX}")
    try:
        out = int(value)
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"top_k must be an integer in {MATCHUP_TOP_K_MIN}..{MATCHUP_TOP_K_MAX}"
        ) from e
    if isinstance(value, bool) or not MATCHUP_TOP_K_MIN <= out <= MATCHUP_TOP_K_MAX:
        raise ValueError(f"top_k must be in {MATCHUP_TOP_K_MIN}..{MATCHUP_TOP_K_MAX}; got {value!r}")
    return out


def _stable_id(prefix: str, body: Any, index: int) -> str:
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{index}:{digest}"

# Any KO that needs 2+ turns is a STATIC approximation: it repeats the first hit's damage and does NOT
# model what actually happens between turns. Only OHKO is exact (audit 2026-06-24 — the calc's raw
# damage band can't be a real "guaranteed 2HKO").
_NHKO_CAVEAT = ("static N-hit approximation — repeats the first hit's damage; ignores between-turn "
                "recovery (Sitrus / Leftovers), attacker/defender ability shifts (Draco Meteor / "
                "Stamina / Multiscale), recoil, status and field changes. Only OHKO is exact.")

def _to_sps(spread: dict[str, Any] | None) -> dict[str, int]:
    if not spread:
        return {}
    return {_SPREAD_TO_SPS[k]: int(v or 0) for k, v in spread.items() if k in _SPREAD_TO_SPS}


def _hko(n: int | None) -> str | None:
    return None if n is None else {1: "OHKO", 2: "2HKO", 3: "3HKO"}.get(n, f"{n}HKO")


def _ko_buckets(min_percent: float | None, max_percent: float | None) -> tuple[int | None, int | None]:
    """(possible, guaranteed) KO-hit counts from the damage band. `possible` uses the best roll (high
    roll, fewest hits); `guaranteed` uses the worst roll (low roll still KOs). Per-hit-independent
    approximation — ignores between-hit Leftovers/Life-Orb recoil (same simplification as the calc).
    Shares the ceil(100/pct) arithmetic with the checks Intimidate rescale via battle_effects."""
    return ko_hits_from_pct(max_percent), ko_hits_from_pct(min_percent)


def _is_static_nhko(possible: int | None, guaranteed: int | None) -> bool:
    """True when the KO needs 2+ turns (so it's a static multi-turn approximation, not exact)."""
    worst = guaranteed if guaranteed is not None else possible
    return bool(worst and worst >= 2)


def _ko_label(possible: int | None, guaranteed: int | None) -> str | None:
    """'2HKO' when the band agrees, else 'possible 2HKO / guaranteed 3HKO' (honest about roll spread).
    A KO needing 2+ turns is tagged '(static approx)' — it isn't an engine-grade probability (audit
    2026-06-24); only OHKO is exact."""
    if possible is None:
        return None
    base = _hko(possible) if possible == guaranteed else (
        f"possible {_hko(possible)} / guaranteed {_hko(guaranteed)}")
    return base + (" (static approx)" if _is_static_nhko(possible, guaranteed) else "")


def member_actor(m: dict[str, Any]) -> dict[str, Any]:
    """A team member's calculation set as an ncp actor.

    Callers must pass :func:`effective_member`'s result when the registered member may hold a Mega
    Stone. Keeping form resolution outside this small serializer lets damage and speed share the
    same effective set without teaching the NCP adapter about dex item mappings.
    """
    return {"name": m["species"], "ability": m.get("ability"), "item": m.get("item"),
            "nature": m.get("nature"), "sps": _to_sps(m.get("spread"))}


def effective_member(member: dict[str, Any], facts: dict[str, dict[str, Any]],
                     item_info: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return the member's actual calculation form while preserving the registered input.

    Team JSON registers a Mega as base species + stone + pre-Mega ability. The calculator needs the
    stone's authoritative ``required_by`` form and that form's ability. Both the matchup battery and
    answer-audit call this helper so a saved evidence coordinate always rebuilds the same actor.
    """
    return effective_member_from_maps(member, facts, item_info)


def run_form_name(name: str, s: dict[str, Any] | None) -> str:
    """The form a resolved opponent actually RUNS as (a singles Mega is ranked under the base name
    but battles as 'Mega X') — the single rule the battery AND the answer-audit's recompute share
    for dex fact lookups on the opponent side.

    Falls back to the set's own species before the passed key: with builds expanded, that key is a
    variant id (`Garchomp#Choice Scarf|Rough Skin`), which is not a name the dex or the calculator
    can resolve — every damage request for a non-Mega build silently returned nothing."""
    s = s or {}
    return s.get("run_form") or s.get("species") or name


def set_actor(name: str, s: dict[str, Any] | None) -> dict[str, Any]:
    """A resolved opponent's MODAL set as an ncp actor (run_form-aware: a singles Mega is ranked
    under the base name but runs as 'Mega X')."""
    s = s or {}
    return {"name": run_form_name(name, s), "ability": s.get("ability"), "item": s.get("item"),
            "nature": s.get("nature"), "sps": s.get("sps") or {}}


def pair_speed(member: dict[str, Any], member_base: int | None,
               opp_set: dict[str, Any] | None, opp_base: int | None, *, conditions: dict | None = None) -> dict[str, Any]:
    """The speed half of ONE matchup cell — member's effective Speed (registered set, always-on item)
    vs the opponent build's effective Speed. Faster spreads and Choice Scarf sets are separate build
    columns in a variant-expanded grid, not hidden synthetic lanes inside this pair. Extracted as the
    re-runnable pair the spd:{fmt}:{member}|{opponent} evidence coordinates recompute."""
    my_spe_sp = int((member.get("spread") or {}).get("spe") or 0)
    field = battle_scope.conditions(conditions)
    my_speed = effective_speed(member_base, my_spe_sp, member.get("nature"),
                               item=member.get("item"), ability=member.get("ability"),
                               weather=field.get("weather"), terrain=field.get("terrain"))
    s = opp_set
    o_nat = (s or {}).get("nature")
    o_spe_sp = int(((s or {}).get("sps") or {}).get("sp") or 0)
    o_item = (s or {}).get("item")
    o_speed = effective_speed(opp_base, o_spe_sp, o_nat, item=o_item, ability=(s or {}).get("ability"),
                             weather=field.get("weather"), terrain=field.get("terrain")) if opp_base is not None else None
    faster = None
    if my_speed is not None and o_speed is not None:
        faster = "member" if my_speed > o_speed else "opponent" if my_speed < o_speed else "tie"
    my_scarf = member_base is not None and member.get("item") in SPEED_ITEM_MULT
    order = {"member": "opponent", "opponent": "member"}.get(faster, faster) if field.get("trick_room") else faster
    return {"member": my_speed, "opponent": o_speed, "faster": faster,
            **({"acts_first": order, "conditions": field} if field else {}),
            "member_item_applied": member.get("item") if my_scarf else None,
            "opponent_item_applied": o_item if (o_item in SPEED_ITEM_MULT) else None,
            "opponent_speed_basis": "modal" if s else "neutral 0-SP"}


def _threat_moves(opp_set: dict | None, excluded: set[str] | None = None) -> list[dict[str, Any]]:
    """them->us threat surface: the moves this BUILD actually runs.

    A resolved build is a real joint set — those four moves are what it brings. The broad meta
    surface (every move the SPECIES runs at >=15%) belongs to a species-level reading: it graded
    Archaludon's Dark Pulse / Thunderbolt / Aura Sphere / Mirror Coat against us even though the
    build on the field carries none of them. When the set is meta-only there is no joint list, so
    the broad surface remains the honest fallback there.
    """
    if not opp_set:
        return []
    rows = opp_set.get("moves") or opp_set.get("threat_moves") or []
    if rows and isinstance(rows[0], str):          # joint sets are plain names; normalise the shape
        rows = [{"name": name, "pct": None} for name in rows]
    if not excluded:
        return rows
    return [row for row in rows if row.get("name") not in excluded]


def dmg_fact(move: str, r: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """One damage fact from an ncp result: roll band + possible/guaranteed KO buckets (both rolls),
    NOT a single max-roll headline. `extra` carries direction-specific fields (e.g. usage_pct).
    Public: the single source of the damage-fact shape — oppcache reuses it, and the answer-audit's
    claim recompute must produce the SAME keys the battery produced."""
    mn, mx = r.get("minPercent"), r.get("maxPercent")    # CENTRAL (expected-hit) band, for context
    hits, hr = r.get("hits"), r.get("hits_range")
    env_lo, env_hi = r.get("min_env_percent"), r.get("max_env_percent")
    span: dict[str, Any] = {}
    # Variable multi-hit: use the calc's TRUE damage envelope (computed from real per-hit damage — the
    # cumulative escalating sums for Triple Axel, lo/hi x roll for uniform moves), NOT a linear ratio
    # of the central band (which was wrong for escalating-BP moves; audit 2026-06-24). KO buckets then
    # span hit-count variance honestly; the central band is kept for reference.
    if (isinstance(hr, (list, tuple)) and len(hr) == 2 and hr[1] > hr[0]
            and env_lo is not None and env_hi is not None):
        span = {"hits": hits, "hits_range": [hr[0], hr[1]],
                "central_percent": [mn, mx]}
        mn, mx = env_lo, env_hi
    elif isinstance(hits, int) and hits > 1:
        span = {"hits": hits}                       # fixed multi-hit: count only, band already total
    poss, guar = _ko_buckets(mn, mx)
    fact = {"move": move, "min_percent": mn, "max_percent": mx,
            "ko_possible": poss, "ko_guaranteed": guar, "ko": _ko_label(poss, guar),
            "ko_exact": poss == 1 and guar == 1,        # only a clean OHKO is engine-exact
            **span, **extra}
    if r.get("category"):                               # Physical/Special — lets checks tell whether a
        fact["category"] = r["category"]                # +Def caveat (Stamina) is even relevant to THIS hit
    # The engine's recovery-aware KO verdict (models Sitrus/Leftovers/hazards) — authoritative for the
    # KO QUESTION on N>=2, where the static band can't see between-turn heals (audit 2026-06-24). The
    # static band/buckets stay as the damage RANGE; ko_chance is the real verdict.
    ko_chance = r.get("ko_chance")
    if ko_chance:
        fact["ko_chance"] = ko_chance
    # Static-KO reliability flags from the calc (DETECTED, not modelled): effects that make the static
    # multi-turn KO unreliable + which DIRECTION it's off (self/target stat-change, Stamina, Multiscale,
    # Sash/Disguise, Knock Off, speed-BP, ...). The AI reader uses these to chain explicit-state
    # snapshots or caveat, rather than the tool pretending to simulate (audit 2026-06-24).
    ko_caveats = r.get("ko_caveats")
    if ko_caveats:
        fact["ko_caveats"] = ko_caveats
    if _is_static_nhko(poss, guar):
        fact["ko_caveat"] = _NHKO_CAVEAT + (" See ko_chance for the recovery-aware verdict."
                                            if ko_chance else "")
    return fact


def best_offense(moves: list[str], result_fn: Callable[[str], dict[str, Any] | None],
                 dmg_fact_fn: Callable[..., dict[str, Any]] = dmg_fact) -> dict[str, Any] | None:
    """Hardest-hitting move by max roll from a precomputed damage result lookup."""
    best_r = best_mv = None
    for mv in moves:
        r = result_fn(mv) or {}
        if r.get("error") or r.get("maxPercent") is None:
            continue
        if best_r is None or r["maxPercent"] > best_r["maxPercent"]:
            best_r, best_mv = r, mv
    return dmg_fact_fn(best_mv, best_r) if best_r else None


def _speed_coverage(my_speed: int | None, my_base: int | None, my_spe_sp: int, nature: str | None,
                    item: str | None, opp_lines: list[dict[str, Any]],
                    total_sp: int = 0) -> dict[str, Any] | None:
    """Speed coverage over observed build variants, equal-weighted across opponent species.

    Within a species, observed variants are weighted by their sample coverage. There is no synthetic
    max-Speed/Scarf lane: those are real variant rows when observed, and unrepresented sample share is
    reported by the matchup portfolio instead of being invented here.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for line in opp_lines:
        if line.get("speed") is not None:
            groups.setdefault(str(line.get("species")), []).append(line)
    if my_speed is None or my_base is None or not groups:
        return None
    representative = []
    observed_scores = []
    for lines in groups.values():
        modal = next((line for line in lines if line.get("is_modal")), lines[0])
        representative.append(modal["speed"])
        weighted = [(line, float(line["coverage"])) for line in lines
                    if isinstance(line.get("coverage"), (int, float))]
        if weighted and sum(w for _, w in weighted) > 0:
            denom = sum(w for _, w in weighted)
            observed_scores.append(sum(w for line, w in weighted if my_speed > line["speed"]) / denom)
        else:
            observed_scores.append(1.0 if my_speed > modal["speed"] else 0.0)
    of = len(representative)
    out = sum(1 for speed in representative if my_speed > speed)
    ties = sum(1 for speed in representative if my_speed == speed)
    other_sp = max(0, total_sp - my_spe_sp)            # SP spent outside Speed (fixed while Speed varies)
    jumps: list[dict[str, Any]] = []
    for tgt in sorted({s for s in representative if s >= my_speed})[:3]:
        sol = solve_outspeed(my_base, nature, tgt, item=item)
        if sol and sol["result"] == "outspeed":
            new_speed = sol["achieved"]
            total_after = other_sp + sol["sp"]
            jumps.append({"clears": tgt, "speed_sp": sol["sp"],
                          "delta_sp": max(0, sol["sp"] - my_spe_sp), "achieved": new_speed,
                          "outspeeds_after": sum(1 for s in representative if new_speed > s),
                          "total_sp_after": total_after,
                          "feasible": total_after <= SP_TOTAL_CAP})
    return {"outspeeds": out, "ties": ties, "of": of, "percent": round(100.0 * out / of, 1),
            "observed_variant_percent": round(100.0 * sum(observed_scores) / of, 1),
            "weighting": "equal-weight across top-K opponents (meta has no per-species usage %)",
            "anchors": {"speed_0_sp": champ_speed(my_base, 0, nature),
                        "speed_max_sp": champ_speed(my_base, SP_CAP, nature)},
            "next_jumps": jumps,
            "note": "Representative speed coverage plus observed-variant coverage. Species are "
                    "equal-weighted; variants within a species use observed sample shares. No synthetic "
                    "fast lane. next_jumps targets representative speed clusters within the 66 SP budget."}


def matchup(team: dict[str, Any], top_k: list[dict[str, Any]], *, fmt: str | None = None,
            dex_fn: Callable, sets_fn: Callable,
            damage_fn: Callable | None = None, move_fn: Callable | None = None,
            variants_fn: Callable | None = None,
            item_fn: Callable | None = None, conditions: dict | None = None) -> dict[str, Any]:
    """Build the member x top-K matchup fact grid.

    `top_k` is a list of meta ranking rows ({rank, pokemon_en, ...}); the opponent list is that
    usage ranking, untouched. Injected, all batched to one call each: `dex_fn(names)->facts`,
    `sets_fn(species_list,fmt)->{species: representative set|None}` for the no-library fallback, and
    `damage_fn(requests)->results` (ncplink.damage_batch; None to skip damage).

    `variants_fn` expands each ranked species into observed representative builds before any damage
    request is created. Each returned cell therefore has one unambiguous build-pair meaning.
    """
    fmt = (fmt or team.get("format") or "single").lower()
    field = battle_scope.conditions(conditions)
    damage_field = {k: v for k, v in field.items() if k != "trick_room"}
    members = [m for m in team.get("pokemon", []) if m.get("species")]

    # Opponents: keep the ranking order; resolve each to a canonical species via its English name.
    # With `variants_fn` each ranked species EXPANDS into one entry per real build, so a cell is a
    # concrete build on both sides rather than a species standing in for all of its builds. `name`
    # stays the key everything downstream indexes by (now a variant id); `species` carries the
    # display/ranking identity, so several rows can share one Pokemon.
    opponents: list[dict[str, Any]] = []
    preset_sets: dict[str, dict[str, Any]] = {}
    for row in top_k:
        name = row.get("pokemon_en") or row.get("pokemon") or row.get("slug")
        if not name:
            continue
        builds = [row["stated_set"]] if row.get("stated_set") else []
        if not builds and variants_fn is not None:
            try:
                builds = variants_fn(name, fmt) or []
            except Exception:
                builds = []
        if not builds:
            opponents.append({"rank": row.get("rank"), "name": name, "species": name})
            continue
        for v in builds:
            vid = v.get("variant_id") or name
            opponents.append({"rank": row.get("rank"), "name": vid, "species": name,
                              "is_modal": bool(v.get("is_modal"))})
            preset_sets[vid] = v

    # Two independent fetches: the dex facts (types + base Speed for members + opponents) and the
    # opponents' fallback representative sets. Neither depends on the other, so run them concurrently — they are
    # separate sibling processes (dex vs meta+dex) and overlap while each waits on its subprocess.
    names = sorted({m["species"] for m in members} | {o["species"] for o in opponents})
    # Only species WITHOUT a resolved build still need the modal resolver.
    opp_names = [o["name"] for o in opponents if o["name"] not in preset_sets]
    import concurrent.futures as _cf
    with _cf.ThreadPoolExecutor(max_workers=2) as _ex:
        facts_fut = _ex.submit(dex_fn, names)
        sets_fut = _ex.submit(sets_fn, opp_names, fmt)
        facts = facts_fut.result() or {}
        opp_sets = sets_fut.result() or {}
    opp_sets.update(preset_sets)          # resolved builds win; the resolver only fills the gaps

    # Resolve each registered member to the form that actually enters the calculation. Team-json often
    # represents a registered Mega as BASE species + its stone; using the registered name directly
    # silently priced base stats/types/ability. The dex item mapping is authoritative (important for
    # X/Y forms and for rejecting a foreign stone), and the original species remains the public row key.
    item_info: dict[str, dict[str, Any]] = {}
    if item_fn is not None:
        held_items = sorted({m.get("item") for m in members if m.get("item")})
        item_info = item_fn(held_items) or {} if held_items else {}
        form_names = sorted({form for info in item_info.values()
                             for form in ((info or {}).get("required_by") or [])})
        missing_forms = [form for form in form_names if form not in facts]
        if missing_forms:
            facts.update(dex_fn(missing_forms) or {})

    effective_members = [effective_member(member, facts, item_info) for member in members]

    # Move priorities (signed): our KO move's priority lets a SLOW member still act first (checks reads
    # it into we_act_first); the opponents' threat-move priorities gate that upgrade OFF when the foe
    # also carries priority (don't over-claim "we're first"). One batched dex call over every move seen.
    move_prio: dict[str, int] = {}
    ohko_moves: set[str] = set()
    if move_fn is not None:
        all_moves = {mv for m in members for mv in (m.get("moves") or [])}
        all_moves |= {mvrow["name"] for o in opponents for mvrow in _threat_moves(opp_sets.get(o["name"]))}
        if all_moves:
            for name, mf in (move_fn(sorted(all_moves)) or {}).items():
                p = (mf or {}).get("priority")
                if isinstance(p, int):
                    move_prio[name] = p
                if (mf or {}).get("is_ohko") is True:
                    ohko_moves.add(name)

    # A singles Mega is ranked under the BASE name but RUN as 'Mega X' (the resolver's `run_form`): use
    # the run form's dex facts (Mega stats/types differ) and ncp name, or speed/type/damage would use the
    # base form (audit 2026-06-25). One extra dex call only when such an opponent is present.
    # Falls back to the SPECIES, not the key: a non-Mega build's set has no `run_form`, and letting
    # the variant id stand in meant every dex lookup (base Speed, types) missed and came back empty.
    run_forms = {o["name"]: (run_form_name(o["name"], opp_sets.get(o["name"]))
                             if (opp_sets.get(o["name"]) or {}).get("run_form")
                             else o.get("species") or o["name"])
                 for o in opponents}
    extra = sorted({rf for rf in run_forms.values() if rf not in facts})
    if extra:
        facts.update(dex_fn(extra) or {})

    def runform(on: str) -> str:
        return run_forms.get(on, on)

    def base_spe(sp: str) -> int | None:
        return ((facts.get(sp, {}) or {}).get("stats") or {}).get("spe")

    def types_of(sp: str) -> list[str]:
        return list((facts.get(sp, {}) or {}).get("types") or [])

    # --- damage, both directions, batched in ONE ncp call ---------------------------------------
    # we->them key: (member, opponent, our move); them->us key: (opponent, member, their move).
    # Status moves come back 0 and drop out. Both directions need the concrete opponent build: offense
    # for the defender basis, defense for the attacker + its carried moves. With no set there is no
    # basis (a bare neutral 0-SP stand-in inflates damage and invents a KO), so skip the opponent.
    # Index by request-local source/target positions, NOT species names. A formal 1..N actual-set
    # battery may compare two builds of the same species; name-keyed indices made the latter silently
    # overwrite the former. Legal registered teams still have unique species, but the lower operator
    # must not rely on that higher-level clause.
    dmg_index: dict[tuple[int, int, str], int] = {}   # (source, target, move)
    def_index: dict[tuple[int, int, str], int] = {}
    requests: list[dict[str, Any]] = []
    if damage_fn is not None:
        for mi, m in enumerate(effective_members):
            mine = member_actor(m)
            for oi, o in enumerate(opponents):
                s = opp_sets.get(o["name"])
                if not s:
                    continue
                actor = set_actor(o["name"], s)
                for mv in (m.get("moves") or []):                  # we -> them
                    dmg_index[(mi, oi, mv)] = len(requests)
                    requests.append({"attacker": mine, "defender": actor, "move": mv,
                                     **({"field": damage_field} if damage_field else {})})
                for mvrow in _threat_moves(s):                     # them -> us
                    def_index[(mi, oi, mvrow["name"])] = len(requests)
                    requests.append({"attacker": actor, "defender": mine, "move": mvrow["name"],
                                     **({"field": damage_field} if damage_field else {})})
    results = damage_fn(requests) if (damage_fn is not None and requests) else []

    def best_member_offense(member: dict[str, Any], member_index: int,
                            opponent_index: int) -> dict[str, Any] | None:
        """Our hardest-hitting move (by max roll) into this exact opponent build."""
        def result_for(mv: str) -> dict[str, Any] | None:
            idx = dmg_index.get((member_index, opponent_index, mv))
            if idx is None or idx >= len(results):
                return None
            return results[idx]

        moves = [mv for mv in (member.get("moves") or []) if mv not in ohko_moves]
        off = best_offense(moves, result_for)
        if off and off.get("move") in move_prio:
            off["priority"] = move_prio[off["move"]]      # our KO move's speed-priority stage (signed)
        # the best PRIORITY move's KO — may be weaker than `off` but strikes first (a revenge lane the
        # neutral frame's hardest-hit selection would otherwise hide). Attached only when it differs.
        prio_moves = [mv for mv in moves if move_prio.get(mv, 0) > 0]
        if off is not None and prio_moves:
            po = best_offense(prio_moves, result_for)
            if po and po["move"] != off.get("move"):
                po["priority"] = move_prio.get(po["move"], 0)
                off["priority_offense"] = po
        return off

    def incoming(member: dict[str, Any], member_index: int, opponent_index: int,
                 opp_set: dict | None) -> dict[str, Any] | None:
        """Damage for every carried move of this exact opponent build; `worst` is max roll."""
        if not opp_set:
            return None
        facts = []
        for mvrow in _threat_moves(opp_set, ohko_moves):
            idx = def_index.get((member_index, opponent_index, mvrow["name"]))
            if idx is None or idx >= len(results):
                continue
            r = results[idx] or {}
            if r.get("error") or r.get("maxPercent") is None:
                continue
            fact = dmg_fact(mvrow["name"], r, usage_pct=mvrow.get("pct"))
            if mvrow["name"] in move_prio:
                fact["priority"] = move_prio[mvrow["name"]]
            facts.append(fact)
        if not facts:
            return None
        facts.sort(key=lambda f: f["max_percent"], reverse=True)
        worst = dict(facts[0])
        priority_facts = [f for f in facts if (f.get("priority") or 0) > 0]
        if priority_facts and priority_facts[0]["move"] != worst.get("move"):
            worst["priority_offense"] = dict(priority_facts[0])
        return {"moves": facts, "worst": worst}

    def member_ohko(member: dict[str, Any], member_index: int, opponent_index: int) -> list[str]:
        """Usable OHKO routes. They stay outside normal offense; checks only mark them uncertain."""
        usable = []
        for mv in member.get("moves") or []:
            if mv not in ohko_moves:
                continue
            idx = dmg_index.get((member_index, opponent_index, mv))
            r = results[idx] if idx is not None and idx < len(results) else None
            if r and not r.get("error") and (r.get("maxPercent") or 0) > 0:
                usable.append(mv)
        return usable

    def incoming_ohko(member: dict[str, Any], member_index: int, opponent_index: int,
                      opp_set: dict | None) -> list[str]:
        usable = []
        for mvrow in _threat_moves(opp_set):
            mv = mvrow["name"]
            if mv not in ohko_moves:
                continue
            idx = def_index.get((member_index, opponent_index, mv))
            r = results[idx] if idx is not None and idx < len(results) else None
            if r and not r.get("error") and (r.get("maxPercent") or 0) > 0:
                usable.append(mv)
        return usable

    # --- assemble per (member, opponent) ---------------------------------------------------------
    any_modal = False
    grid: list[dict[str, Any]] = []
    for mi, m in enumerate(members):
        sp = m["species"]
        calc_member = effective_members[mi]
        calc_sp = calc_member["species"]
        source_id = _stable_id("source", m, mi)
        my_types = types_of(calc_sp)
        my_base = base_spe(calc_sp)
        my_spe_sp = int((m.get("spread") or {}).get("spe") or 0)
        # Effective Speed (Choice Scarf etc. applied) — the number that actually decides who moves
        # first. Weather/Tailwind aren't part of a matchup cell, so they stay off here; Choice Scarf
        # is always-on and MUST be counted (audit retro 2026-06-22).
        my_speed = effective_speed(my_base, my_spe_sp, calc_member.get("nature"),
                                   item=calc_member.get("item"), ability=calc_member.get("ability"),
                                   weather=field.get("weather"), terrain=field.get("terrain"))
        cells: list[dict[str, Any]] = []
        for oi, o in enumerate(opponents):
            on = o["name"]
            target_id = _stable_id("meta-target", {"format": fmt, **o}, oi)
            s = opp_sets.get(on)
            if s:
                any_modal = True
            # Speed: opponent uses this build's nature + Spe SP + item (so Choice Scarf is counted);
            # if no set exists, neutral 0-SP is flagged. One shared pair construction —
            # pair_speed — is the same function the answer-audit's spd recompute runs.
            o_base = base_spe(runform(on))
            # Defense (them -> us): opponent STAB types vs our member types, type level only.
            opp_types = types_of(runform(on))
            def_pairs = [(t, effectiveness_for_member(my_types, calc_member.get("ability"), t))
                         for t in opp_types]
            worst = max((e for _, e in def_pairs), default=None)
            cell = {
                "cell_id": f"{source_id}|{target_id}",
                "target_id": target_id,
                "opponent": o.get("species") or on, "usage_rank": o["rank"],
                # Several cells can share one `opponent`: the ranked species expands into one cell
                # per real build, and this is which build the numbers belong to.
                **({"opponent_variant": on, "opponent_is_modal": bool(o.get("is_modal"))}
                   if o.get("species") and o["species"] != on else {}),
                "opponent_coverage": (s or {}).get("coverage"),
                "opponent_cluster": (s or {}).get("cluster"),
                **({"opponent_source": s.get("source"), "opponent_coverage_basis": s.get("coverage_basis")}
                   if s and s.get("source") == "stated" else {}),
                "opponent_run_form": (runform(on)
                                      if runform(on) not in (on, o.get("species")) else None),
                "set_confidence": (s or {}).get("confidence") if s else None,
                "set_note": (s or {}).get("note") if s else "no meta set — speed uses neutral 0-SP, no damage",
                "speed": pair_speed(calc_member, my_base, s, o_base, conditions=field),
                "defense_type": {"opponent_stab_types": opp_types,
                                 "max_effectiveness_vs_member": worst,
                                 "by_type": [{"type": t, "x": e} for t, e in def_pairs]},
                "offense": best_member_offense(calc_member, mi, oi) if damage_fn is not None else None,
                "defense_damage": incoming(calc_member, mi, oi, s) if damage_fn is not None else None,
                "ohko_moves": member_ohko(calc_member, mi, oi) if damage_fn is not None else [],
                "incoming_ohko_moves": incoming_ohko(calc_member, mi, oi, s) if damage_fn is not None else [],
                # does THIS opponent carry a priority move? gates our priority-first upgrade off (don't
                # claim we act first when the foe also has priority) — checks reads it.
                "opponent_has_priority": any(move_prio.get(mvrow["name"], 0) > 0
                                             for mvrow in _threat_moves(s)),
            }
            # One atomic grade for this exact member set × observed opponent variant.
            cell["check"] = (checks.build_check(cell, calc_sp, s, fmt, member_set=calc_member)
                             if damage_fn is not None else None)
            battle_scope.annotate(cell["check"], field)
            # The REVERSE reading: can the OPPONENT answer us. Same grader over the same cell with
            # the two sides exchanged — our KO becomes its incoming, its threat becomes our offense,
            # and the speed pair inverts. Both facts are already computed here, so this costs no
            # extra calc; without it "swap sides" in the UI had nothing to show.
            if damage_fn is not None and cell.get("defense_damage"):
                rev_speed = dict(cell["speed"])
                rev_speed["member"], rev_speed["opponent"] = (cell["speed"].get("opponent"),
                                                             cell["speed"].get("member"))
                rev_speed["faster"] = {"member": "opponent", "opponent": "member"}.get(
                    cell["speed"].get("faster"), cell["speed"].get("faster"))
                if "acts_first" in rev_speed:
                    rev_speed["acts_first"] = {"member": "opponent", "opponent": "member"}.get(
                        cell["speed"]["acts_first"], cell["speed"]["acts_first"])
                member_stab_pairs = [
                    (t, effectiveness_for_member(opp_types, (s or {}).get("ability"), t))
                    for t in my_types
                ]
                rev_cell = {
                    "opponent": calc_sp, "usage_rank": None, "speed": rev_speed,
                    "defense_type": {
                        "opponent_stab_types": my_types,
                        "max_effectiveness_vs_member": max(
                            (e for _, e in member_stab_pairs), default=None),
                        "by_type": [{"type": t, "x": e} for t, e in member_stab_pairs],
                    },
                    "offense": (cell.get("defense_damage") or {}).get("worst"),
                    "defense_damage": {"worst": cell.get("offense"),
                                       "moves": [cell["offense"]] if cell.get("offense") else []},
                    "ohko_moves": cell.get("incoming_ohko_moves"),
                    "incoming_ohko_moves": cell.get("ohko_moves"),
                    "opponent_has_priority": any(
                        move_prio.get(mv, 0) > 0 for mv in (calc_member.get("moves") or [])),
                }
                rev = checks.build_check(rev_cell, on, calc_member, fmt, member_set=s)
                cell["reverse_check"] = battle_scope.annotate(rev, field)
            cells.append(cell)
        # Coverage counts ONLY opponents whose speed comes from a real meta set; a no-set opponent's
        # speed is unknown and its neutral-0-SP stand-in must not count as 'outspept' (audit 2026-06-24).
        opp_lines = [{"species": c["opponent"], "speed": c["speed"]["opponent"],
                      "coverage": c.get("opponent_coverage"),
                      "is_modal": c.get("opponent_is_modal", True)}
                     for c in cells if c["speed"]["opponent_speed_basis"] == "modal"]
        total_sp = sum(int(v or 0) for v in (m.get("spread") or {}).values())
        coverage = _speed_coverage(my_speed, my_base, my_spe_sp, calc_member.get("nature"),
                                   calc_member.get("item"),
                                   opp_lines, total_sp=total_sp)
        if field:
            coverage = None  # Neutral-field SP jump solver does not solve stated-field turn order.
        grid.append({"source_id": source_id, "source_index": mi,
                     "member": sp,
                     "member_run_form": calc_sp if calc_sp != sp else None,
                     "base_speed": my_base, "speed": my_speed, "types": my_types,
                     "speed_coverage": coverage, "cells": cells})

    ranked_species = len({o.get("species") or o["name"] for o in opponents})
    notes = [
        f"{fmt}: each member vs {len(opponents)} retained builds across the top {ranked_species} "
        "most-used Pokemon (meta usage ranking). "
        "Objective facts only — no matchup score, no ranking of members/opponents, no best pick.",
        "Opponent LIST = meta usage ranking (a published fact: 'most-used', not 'biggest threat'). "
        "Each species expands into retained observed (item, ability) builds from the rule-scoped "
        "real-team sample; coverage and omitted sample mass are provenance, not strength.",
        "Speed is integer-exact (Champions closed form) with always-on Choice Scarf applied on BOTH "
        "sides (our registered item, the observed build's item); our side uses the registered set and "
        "the opponent its retained nature + Spe SP (neutral 0-SP only when no set resolves). Weather-speed "
        "abilities and Tailwind are field-dependent and NOT applied in a matchup cell.",
        "damage = the turn the move FIRES, no field up; turn costs are not modelled (a charge move "
        "like Solar Beam / Electro Shot spends a turn charging unless its weather is up — and Solar "
        "Beam halves in rain/sand/hail), so a cell's max damage can overstate one-turn output. For "
        "field-explicit numbers run ncp with `field` set.",
        "`speed_coverage` (§16.5): EQUAL-WEIGHT across top-K species and sample-coverage-weighted across "
        "retained builds within a species (no-set opponents excluded), + the cheapest Spe-SP jumps to "
        "the next representative "
        "clusters, each checked vs the 66 SP total budget (feasible flag) — marginal arms-race cost made "
        "explicit, not a creep recommendation. Full usage-weighted field model is a later increment.",
        "offense (we->them) = our member's own moves vs one observed build; defense_damage "
        "(them->us) = EVERY damaging move carried by that build vs our member; `worst` = hardest max "
        "roll. Both directions give the full roll band plus "
        "ko_possible (best roll) AND ko_guaranteed (worst roll still KOs). ONLY OHKO is exact: any 2+ "
        "turn KO is a STATIC approximation (`ko_caveat`/`ko_exact`) — it repeats hit 1 and ignores "
        "between-turn recovery / ability / field shifts (audit 2026-06-24).",
        "Accuracy-based one-hit-KO moves are excluded from deterministic offense/incoming facts and "
        "never upgrade C2/C1/C0, but an applicable route marks the derived check as contested "
        "(dex is_ohko authority; no probability simulation).",
        "Observed representative builds preserve joint move/item/ability co-occurrence; defense_type "
        "(STAB type effectiveness) is the no-set fallback and is always present. STILL DEFERRED: lead/back, "
        "doubles spread/partner, and speed-control (tailwind/trick-room) are not modelled.",
    ]
    if damage_fn is None:
        notes.append("damage skipped (ncp unavailable): offense and defense_damage omitted, "
                     "defense is type-level only.")
    if field:
        notes = [n for n in notes if not n.startswith(("Speed is", "damage =", "`speed_coverage`"))]
        notes = [n.replace("STILL DEFERRED: lead/back, doubles spread/partner, and speed-control (tailwind/trick-room) are not modelled.",
                           "Lead/back, doubles partners and changes to field duration are not simulated.") for n in notes]
        notes.append("Stated weather/terrain applies to damage and field-triggered Speed abilities. "
                     "Trick Room changes acts_first within equal priority, not the faster speed label. "
                     "Neutral-field SP jump suggestions are omitted. Weather/terrain setters are not automatically activated.")
    if any(row.get("stated_set") for row in top_k) or all(row.get("rank") is None for row in top_k):
        notes = [n for n in notes if "meta usage ranking" not in n]
        notes.append("Explicit opponent roster; stated sets replace retained builds for that slot. Bare species use "
                     "retained observed configurations. Stated-set coverage=1 describes this hypothesis only, not observed usage.")

    if damage_fn is not None:
        notes.append(
            "`check` (per cell) + `check_coverage` (roll-up) are a DERIVED classification over these "
            "same facts (checks.py): C2=safe switch-in check / C1=same-field revenge only / C0=none; "
            "an ordinal LABEL with predicates + a turn budget, NEVER a score and NEVER summed into a "
            "team 'check score'. Each cell grades one concrete observed build. The species-level "
            "observed_floor is derived afterward and names the build variants that witness it; observed "
            "sample coverage and calculation completeness are reported separately.")

    confidence = "low" if not any_modal else "medium"
    returned_calcs = min(len(results), len(requests)) if isinstance(results, list) else 0
    failed_calcs = sum(
        1 for r in (results[:len(requests)] if isinstance(results, list) else [])
        if not isinstance(r, dict) or bool(r.get("error"))
    ) + max(0, len(requests) - returned_calcs)
    cells_total = len(members) * len(opponents)
    cells_with_offense = sum(
        1 for row in grid for cell in row.get("cells", []) if cell.get("offense") is not None)
    cells_with_check = sum(
        1 for row in grid for cell in row.get("cells", []) if cell.get("check") is not None)
    # `top_k` stays the requested opponent SPECIES count — the scope the caller asked for. With
    # builds expanded there are more columns than species, and reporting that as top_k would make
    # the response contradict the request.
    return {"kind": "matchup", "format": fmt, "top_k": ranked_species,
            "source_count": len(members), "target_count": len(opponents), "view": "full",
            **({"conditions": field} if field else {}),
            "members": grid,
            "opponent_sets": opp_sets,
            "check_coverage": checks.coverage_summary(grid) if damage_fn is not None else None,
            "calculation": {"requested": len(requests), "returned": returned_calcs,
                            "failed": failed_calcs},
            "coverage": {"cells_total": cells_total, "cells_with_offense": cells_with_offense,
                         "cells_with_check": cells_with_check,
                         "complete": (cells_total > 0 and cells_with_check == cells_total
                                      and failed_calcs == 0)},
            "notes": notes, "confidence": confidence,
            "confidence_reason": "vs-observed-build",
            "evidence": {"facts": [{"source": "meta", "ref": "usage ranking (opponent list)"},
                                   {"source": "team-library", "ref": "retained observed builds per opponent"},
                                   {"source": "dex", "ref": "types / base Speed"},
                                   {"source": "ncp", "ref": "we->them and them->us max damage"}],
                         "assumptions": ["opponent runs one of the retained observed builds",
                                         "KO buckets give both possible (best roll) and guaranteed (worst roll)",
                                         "unrepresented sample mass is not calculated"]}}


def _compact_check(check: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(check, dict):
        return None
    resolvability = check.get("resolvability") or {}
    return {
        "grade": check.get("grade"),
        "c1_mode": check.get("c1_mode"),
        "c0_kind": check.get("c0_kind"),
        "resolvability": resolvability.get("verdict"),
        "contested": resolvability.get("verdict") == "contested",
        "caveat_codes": [row.get("code") for row in (check.get("caveat_details") or [])
                          if isinstance(row, dict) and row.get("code")],
        **{key: check[key] for key in ("field_conditions", "c2_basis", "condition_profile", "vs_set", "evaluated_surface",
                                      "caveats", "caveat_details", "intimidate_lane", "priority_lane") if key in check},
    }


def _compact_damage(fact: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(fact, dict):
        return None
    # `ko_chance` rides along because it is the RECOVERY-AWARE verdict, and the grade is computed
    # from it: a defender holding Leftovers turns a static 2HKO into an 87.5% chance, so a client
    # left with only the static rolls would label that cell "guaranteed 2HKO" while the check grid
    # called the same kill uncertain — two tables contradicting each other over one number.
    keys = ("move", "min_percent", "max_percent", "ko_possible", "ko_guaranteed", "ko",
            "ko_exact", "ko_chance", "category", "priority", "hits", "hits_range", "central_percent",
            "ko_caveat", "ko_caveats", "disguise_adjusted")
    return {key: fact.get(key) for key in keys if key in fact}


def project_matchup(result: dict[str, Any], view: str = "full") -> dict[str, Any]:
    """Project one computed battery without changing its calculations.

    ``full`` is the durable operator/evidence surface used by diagnose/select/slate. ``summary`` is a
    wire-friendly grid: it keeps stable cell ids, KO headlines, worst incoming damage, speed and the
    derived grade, while dropping broad move lists and line-by-line predicates.
    A caller that needs those details can request the full projection for the same input.
    """
    if view not in {"full", "summary"}:
        raise ValueError(f"matchup view must be 'full' or 'summary'; got {view!r}")
    if view == "full":
        return result

    members: list[dict[str, Any]] = []
    for row in result.get("members") or []:
        cells = []
        for cell in row.get("cells") or []:
            incoming = cell.get("defense_damage") or {}
            cells.append({
                "cell_id": cell.get("cell_id"), "target_id": cell.get("target_id"),
                "opponent": cell.get("opponent"), "usage_rank": cell.get("usage_rank"),
                "opponent_run_form": cell.get("opponent_run_form"),
                "opponent_coverage": cell.get("opponent_coverage"),
                "opponent_cluster": cell.get("opponent_cluster"),
                **{k: cell[k] for k in ("opponent_source", "opponent_coverage_basis") if k in cell},
                "set_confidence": cell.get("set_confidence"),
                "speed": cell.get("speed"),
                "defense_type": {
                    "opponent_stab_types": (cell.get("defense_type") or {}).get("opponent_stab_types"),
                    "max_effectiveness_vs_member":
                        (cell.get("defense_type") or {}).get("max_effectiveness_vs_member"),
                },
                "offense": _compact_damage(cell.get("offense")),
                "incoming": _compact_damage(incoming.get("worst")),
                "check": _compact_check(cell.get("check")),
                "reverse_check": _compact_check(cell.get("reverse_check")),
                **({"opponent_variant": cell["opponent_variant"]}
                   if cell.get("opponent_variant") else {}),
                **({"opponent_is_modal": cell["opponent_is_modal"]}
                   if cell.get("opponent_is_modal") is not None else {}),
            })
        members.append({
            "source_id": row.get("source_id"), "source_index": row.get("source_index"),
            "member": row.get("member"), "member_run_form": row.get("member_run_form"),
            "base_speed": row.get("base_speed"),
            "speed": row.get("speed"), "types": row.get("types"),
            "speed_coverage": row.get("speed_coverage"), "cells": cells,
        })

    return {**{k: v for k, v in result.items() if k != "opponent_sets"}, "view": "summary", "members": members,
            "notes": [*(result.get("notes") or []), "summary projection: request view=full for broad incoming move surfaces and "
                      "complete CHECK predicates."]}


def _x(mult: float | None) -> str:
    if mult is None:
        return "?"
    return f"{mult:g}x"


def format_matchup_md(d: dict[str, Any]) -> str:
    lines = ["# " + i18n.t('mu_header', top_k=d['top_k'], fmt=d['format'],
                           conf=d['confidence'], confidence=i18n.t('confidence'))]
    for mrow in d["members"]:
        spe = f"{mrow['speed']}" if mrow.get("speed") is not None else "?"
        lines.append(f"\n## {mrow['member']}  " + i18n.t(
            'mu_member_header', base=mrow.get('base_speed'), spe=spe,
            types='/'.join(mrow.get('types') or []) or '?'))
        cov = mrow.get("speed_coverage")
        if cov:
            # `of` lives on the coverage object, not each jump (the old `j['of']` raised KeyError and
            # crashed the whole Markdown render; audit 2026-06-24). Infeasible jumps (over the 66 SP
            # total budget) are flagged, not shown as plain advice.
            jt = "; ".join(
                f"+{j['delta_sp']} Spe SP → "
                + i18n.t('mu_clears', clears=j['clears'])
                + f" ({j['outspeeds_after']}/{cov['of']})"
                + ("" if j.get("feasible", True)
                   else i18n.t('mu_infeasible', total=j.get('total_sp_after')))
                for j in cov.get("next_jumps", []))
            if jt:
                tail = i18n.t('mu_next_jumps', jt=jt)
            elif cov["outspeeds"] == cov["of"]:
                tail = i18n.t('mu_already_clears')
            else:
                tail = i18n.t('mu_no_jump')
            lines.append("- " + i18n.t('mu_speed_field', out=cov['outspeeds'], of=cov['of'],
                                       percent=cov['percent'])
                         + (i18n.t('mu_ties', ties=cov['ties']) if cov.get("ties") else "")
                         + tail)
        for c in mrow["cells"]:
            sp = c["speed"]
            arrow = {"member": i18n.t('mu_cmp_outspeeds'),
                     "opponent": i18n.t('mu_cmp_slower'),
                     "tie": i18n.t('mu_cmp_tie')}.get(sp["faster"], i18n.t('mu_cmp_unknown'))
            off = c.get("offense")
            off_txt = (i18n.t('mu_we_them', move=off['move'],
                              min=off['min_percent'], max=off['max_percent'])
                       + (f" ({off['ko']})" if off.get("ko") else "")) if off else ""
            dd = c.get("defense_damage")
            if dd:
                w = dd["worst"]
                used = (i18n.t('mu_used', pct=f"{w['usage_pct']:.0f}")
                        if w.get("usage_pct") is not None else "")
                more = (i18n.t('mu_more', n=len(dd['moves']) - 1)
                        if len(dd["moves"]) > 1 else "")
                dd_txt = (i18n.t('mu_them_us', move=w['move'],
                                 min=w['min_percent'], max=w['max_percent'])
                          + (f" ({w['ko']}{used})" if w.get("ko") else "") + more)
            else:
                dd_txt = ""
            dt = c["defense_type"]
            def_txt = i18n.t('mu_their_stab',
                             types=('/'.join(dt['opponent_stab_types']) or '—'),
                             x=_x(dt['max_effectiveness_vs_member']))
            rank = f"#{c['usage_rank']}" if c.get("usage_rank") else ""
            lines.append(f"- vs **{c['opponent']}** {rank}: {arrow} "
                         f"({sp['member']} vs {sp['opponent']}){off_txt}{dd_txt}{def_txt}")
    if d.get("notes"):
        lines.append("\n## " + i18n.t('notes'))
        lines += [f"- {n}" for n in d["notes"]]
    return "\n".join(lines)


def format_check_coverage_md(d: dict[str, Any]) -> str:
    """Observed species floors with concrete witness variants; no score or opponent ranking."""
    cov = d.get("check_coverage")
    head = "# " + i18n.t('chkcov_title', fmt=d['format'], top_k=d['top_k'], conf=d['confidence'])
    if not cov:
        return head + "\n\n_" + i18n.t('chkcov_none') + "_"
    dist = cov.get("grade_distribution") or {}
    lines = [head,
             "\n_" + i18n.t('chkcov_legend', c2=dist.get('C2', 0), c1=dist.get('C1', 0),
                            c0=dist.get('C0', 0)) + "_",
             "\n" + i18n.t('chkcov_header'),
             "|---:|---|:--:|---|---|---|---|"]
    for r in cov.get("by_opponent", []):
        rank = r.get("usage_rank")
        floor = (r.get("observed_floor") or {}).get("grade")
        witnesses = (r.get("observed_floor") or {}).get("witness_variant_ids") or []
        rep = r.get("representative") or {}
        coverage = r.get("coverage") or {}
        lines.append(
            f"| {rank if rank is not None else ''} | {r['opponent']} | **{floor or '—'}** | "
            f"{', '.join(witnesses) or '—'} | {rep.get('grade') or '—'} | "
            f"{coverage.get('represented') if coverage.get('represented') is not None else '—'} | "
            f"{'yes' if r.get('calculation_complete') else 'no'} |")
    holes = cov.get("holes") or []
    lines.append("\n## " + i18n.t('chkcov_holes_title'))
    if holes:
        for h in holes:
            floor = (h.get("observed_floor") or {}).get("grade")
            witnesses = (h.get("observed_floor") or {}).get("witness_variant_ids") or []
            lines.append(f"- **{h['opponent']}** (#{h.get('usage_rank')}): "
                         + i18n.t('chkcov_hole_line', floor=floor,
                                  witnesses=', '.join(witnesses) or '—'))
    else:
        lines.append("- " + i18n.t('chkcov_holes_none'))
    if cov.get("note"):
        lines.append("\n> " + cov["note"])
    return "\n".join(lines)
