#!/usr/bin/env python
"""Derived CHECK-GRADE classification over `matchup` cell facts (design §5 L2 派生视图).

FACTS-ONLY. A cell's check is an ORDINAL LABEL + the booleans that produced it + evidence_ids +
disclosed caveats. It is NEVER a scalar score and is NEVER summed across members/opponents into a
team "check score" (design §1/§16 — the no-composite red line). A consumer may count grades as a
distribution, but any cross-team/cross-opponent RANKING is the AI's / front-end's judgment, never a
skill-emitted number.

Model (frozen rubric — the audited turn-budget engagement, not a static threshold):
  * The engagement is a 1v1 with the opponent already on field. We evaluate TWO lines:
      - same_field : both already out.        incoming = kill_hits - (we_act_first ? 1 : 0)
      - switch_in  : we pivot in on its move.  incoming = 1 + kill_hits - (we_act_first ? 1 : 0)
    `incoming` = how many enemy hits we must absorb before our KO lands.
  * grade is the one atomic build-pair axis and is 3-way:
      C2 = switch_in line safe (survive the switch hit + budget) AND we kill  -> "switch-in check"
           (NOT "counter": the engine models no status/recovery/PP/boosts/hazards/multi-turn, so a
           repeatable-wall claim would over-reach).
      C1 = only same_field safe (revenge; cannot pivot in).
      C0 = neither.
    C1 sub-modes (clean_revenge / speed_trade / bulk_revenge / priority_candidate) are UNORDERED
    explanatory descriptors, NOT a strength ladder (they are different failure modes).
  * safe(line) is THREE-VALUED, not a boolean:
      exact_true  : incoming <= 1 — survival rests only on the exact OHKO fact.
      approx_true : incoming >= 2, no survival-worsening ko_caveat — static N-hit approximation.
      uncertain   : incoming >= 2 AND ncp flagged a ko_caveat that could make survival WORSE than the
                    static floor (`understates`/`unclear` direction — Multiscale / Weak Armor / attacker
                    ramp / stat-change ...) — the "repeat hit 1" assumption is unreliable. A caveat that
                    only makes the enemy KO HARDER (`overstates` — Stamina / one-time-survive) does NOT
                    downgrade (we are at-least-as-safe as approx_true).
      false       : the opponent can KO us within the budget (pessimistic: uses ko_possible).
    Hard C2/C1 require safe in {exact_true, approx_true}; `uncertain` never hardens a grade.
    The multi-hit integrity gate REUSES ncp's own ko_caveats detection (design §14 "DETECTED, not
    modelled") rather than a hand-kept unsafe-ability list.
  * Each cell is one named member build × one retained observed opponent build. Its item, ability,
    moves, spread and real speed are evaluated together; uncertainty lives in sibling cells rather
    than a hidden synthetic fast lane. A species roll-up may take an observed floor only after every
    retained build has its own atomic grade and must name the floor's witness build IDs.
  * resolvability — an ORTHOGONAL multi-turn qualifier, NOT part of the ordinal grade. The grade is a
    single-FRAME fact; a positive read (C2 wall / no-KO stalemate / slow grind) silently assumes the
    opponent neither sets up nor heals. When that is DETECTED false the read is `contested`, unless our
    member negates it — Unaware (纯朴) ignores the opponent's stat changes (hard), Haze/Taunt/phaze
    answer at a tempo cost (soft). An applicable accuracy-based OHKO route on either side also marks
    the read `contested` without changing C2/C1/C0. DETECTED not modelled (design §14): no boosted-state
    damage or OHKO probability sequence is run.

Pure functions only: this module consumes already-computed matchup cell facts and calls no
dex/meta/ncp sibling. All damage/speed arithmetic happened upstream in `matchup`.
"""
from __future__ import annotations

from typing import Any

from battle_effects import (INTIMIDATE_BACKFIRE_ABILITIES, INTIMIDATE_BLOCKING_ITEMS,
                            INTIMIDATE_NO_RELIEF_ABILITIES, INTIMIDATE_REFLECT_ABILITIES,
                            ko_hits_from_pct)

# Abilities that NULLIFY an incoming priority move (the holder is never hit first by our priority).
_PRIORITY_BLOCK_ABILITIES = {"queenly majesty", "dazzling", "armor tail"}
# Priority moves that only go first IF the target attacks — never hardens a grade (soft candidate only).
_CONDITIONAL_PRIORITY = {"sucker punch", "upper hand"}
# Physical moves whose damage does not use the attacker's Attack stat: Attack-stat-independent (Body
# Press = Defense, Foul Play = the target's Attack) AND fixed/level/counter damage (Seismic Toss, Night
# Shade, Counter, Metal Burst, Endeavor, Super Fang, Final Gambit ...). Intimidate's -1 Atk cannot scale
# any of these, so the switch-in rescale must skip them (a x2/3 here is arithmetically wrong).
_INTIMIDATE_ATTACK_INDEPENDENT = {
    "body press", "foul play", "seismic toss", "night shade", "counter", "mirror coat", "metal burst",
    "endeavor", "super fang", "final gambit", "psywave", "dragon rage", "sonic boom", "bide", "guardian of alola",
}


def _priority_acts_first(move: str | None, ability_blocks: bool, opp_has_priority: bool) -> bool:
    """Shared gate for whether OUR priority move actually strikes first: the foe's ability doesn't block
    it, the foe doesn't itself carry priority (over-claim guard), and the move isn't conditional (Sucker
    Punch fails if the target doesn't attack). Single source so the `priority_first` and `priority_lane`
    guards can never drift apart when a new blocker is added."""
    return (not ability_blocks and not opp_has_priority
            and str(move or "").casefold() not in _CONDITIONAL_PRIORITY)


# --------------------------------------------------------------------------------------------------
# engagement primitives
# --------------------------------------------------------------------------------------------------
def _kill_hits(offense: dict[str, Any] | None) -> tuple[int | None, bool]:
    """Our KO turn count plus whether the kill is a hard fact.

    Static `ko_guaranteed` remains the fallback. A recovery-aware engine verdict supersedes it when
    it proves the threshold later, or disproves certainty at that same/later threshold. Relevant
    dynamic-distribution caveats that can overstate the KO also make the kill uncertain.
    """
    if not offense:
        return None, False
    g = offense.get("ko_guaranteed")
    p = offense.get("ko_possible")
    hits = g if isinstance(g, int) and g > 0 else None
    certain = hits is not None

    chance = offense.get("ko_chance")
    if isinstance(chance, dict) and isinstance(chance.get("n"), int) and chance["n"] > 0:
        n = chance["n"]
        if chance.get("guaranteed") is True:
            # OHKO remains exact unless a detected one-time-survive caveat says otherwise. For N>=2,
            # the engine's recovery-aware threshold is the more faithful turn count.
            if hits != 1:
                hits, certain = n, True
        elif hits is None or (hits != 1 and n >= hits):
            # The engine found only a chance at the static guarantee (or later): that threshold is not
            # a hard kill. Keep the earliest engine-supported turn count as soft information.
            hits, certain = n, False

    if hits is None and isinstance(p, int) and p > 0:
        hits, certain = p, False
    if hits is None:
        return None, False

    if certain:
        cav = _relevant_ko_caveats(offense)
        if hits == 1:
            # A true 1-hit KO faints the target before a PER-HIT +Def effect (Stamina / Weak Armor) can
            # matter, so those caveats are inert here — mirror `_survive`'s required>=2 boundary. A
            # one-time-survive caveat (Sturdy / Sash / Disguise / Multiscale) DOES survive the OHKO and
            # must still block certainty.
            cav = [c for c in cav if c.get("code") not in _PHYS_DEF_CAVEATS]
        blocking = [c for c in cav
                    if str(c.get("direction") or "unclear").casefold() != "understates"]
        if blocking:
            certain = False
    return hits, certain


def _worst_incoming(moves: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The hardest-hitting incoming move (highest max roll) — what a SAFE switch-in must survive."""
    facts = [m for m in (moves or []) if m.get("max_percent") is not None]
    if not facts:
        return None
    return max(facts, key=lambda f: f["max_percent"])


def incoming_hits_required(line: str, kill_hits: int | None, we_act_first: bool) -> int | None:
    """Enemy hits we must absorb before our KO lands (the audited turn budget).
    same_field: kill_hits - (first?1:0) ; switch_in: 1 + kill_hits - (first?1:0). Floors at 0."""
    if kill_hits is None:
        return None
    base = kill_hits - (1 if we_act_first else 0)
    if line == "switch_in":
        base += 1
    return max(0, base)


# ncp caveat codes whose effect is a +Def buff that keys off DEFENSE and so ONLY bites a PHYSICAL hit
# (Stamina = +1 Def per physical hit; Weak Armor = -1 Def per physical hit). Against a SPECIAL move the
# defender's Def change is irrelevant, so such a caveat is spurious and must NOT block a hard grade —
# the Stamina/Iron-Defense category lesson: a +Def effect does not touch a special attacker (design §14).
_PHYS_DEF_CAVEATS = {"defender_stamina", "defender_weak_armor"}


def _relevant_ko_caveats(worst: dict[str, Any] | None) -> list[dict[str, Any]]:
    """ncp's ko_caveats for THIS incoming move, minus the ones its damage category makes inert. A +Def
    caveat cannot affect a Special hit, so dropping it stops a false `uncertain` (the category fix)."""
    cav = (worst or {}).get("ko_caveats") or []
    if str((worst or {}).get("category") or "").casefold() == "special":
        cav = [c for c in cav if c.get("code") not in _PHYS_DEF_CAVEATS]
    return cav


def _survive(required: int | None, worst: dict[str, Any] | None) -> str:
    """Three-valued survival verdict over the worst incoming move (design: exact/approx/uncertain/
    false). Safety is PESSIMISTIC — it uses ko_possible (a roll that KILLS within budget = not safe),
    and it prefers the recovery-aware ko_chance verdict for the N>=2 window when the calc supplies it."""
    if required is None:
        return "false"                                   # we cannot even guarantee a kill -> not safe
    if required == 0:
        return "exact_true"                              # we KO before the enemy acts
    if worst is None:
        return "exact_true"                              # no damaging incoming move on this surface
    # Recovery-aware verdict first (models Sitrus/Leftovers/hazards) when present, for the N>=2 window.
    chance = worst.get("ko_chance")
    enemy_ko = worst.get("ko_possible")                  # pessimistic: best enemy roll
    if required >= 2 and isinstance(chance, dict) and isinstance(chance.get("n"), int):
        static_poss = enemy_ko                           # the envelope-widened static bucket
        enemy_ko = chance["n"]                            # dynamic threshold, not min(static, dynamic)
        # ko_chance is the recovery-aware verdict on the CENTRAL (expected-hit) band; for a VARIABLE
        # multi-hit move ko_possible carries the TRUE damage envelope (most hits x best roll -> fewest
        # KO turns) the central verdict can miss. Keep the envelope as a pessimistic floor so a variable
        # multi-hit foe is not credited a slower KO than its own envelope allows (ncp API contract: widen
        # KO over the envelope, not the central band).
        if worst.get("hits_range") and isinstance(static_poss, int) and static_poss < enemy_ko:
            enemy_ko = static_poss
    if isinstance(enemy_ko, int) and enemy_ko <= required:
        return "false"                                   # enemy can KO within the budget
    if required <= 1:
        return "exact_true"                              # surviving exactly one hit rests on the exact OHKO fact
    # required >= 2: static N-hit approximation. A DETECTED category-relevant caveat makes the "repeat
    # hit 1" assumption unreliable -> uncertain — BUT only when it could make survival WORSE than this
    # static floor. A caveat whose direction only makes the enemy KO HARDER (`overstates` — defender
    # Stamina, one-time-survive) leaves us at-least-as-safe as approx_true, so it must NOT downgrade to
    # uncertain (mirror of `_kill_hits`, whose favorable direction is the opposite `understates`); an
    # `understates` (Weak Armor / Multiscale / attacker ramp) or `unclear`/absent direction still gates.
    blocking = [c for c in _relevant_ko_caveats(worst)
                if str(c.get("direction") or "unclear").casefold() != "overstates"]
    if blocking:
        return "uncertain"
    return "approx_true"


_SAFE_HARD = {"exact_true", "approx_true"}


def line_result(line: str, offense: dict[str, Any] | None,
                incoming_moves: list[dict[str, Any]] | None, we_act_first: bool) -> dict[str, Any]:
    """One engagement line's outcome: the turn budget, the three-valued safety verdict, and whether
    we actually secure the kill. `passes` = safe (hard) AND we secure a GUARANTEED kill — a best-roll
    (ko_possible-only) kill is pessimistically NOT a hard grade (mirrors survival's ko_possible floor);
    it is surfaced separately as `kill_uncertain` so the info is not lost, never hardening C1/C2."""
    kill, kill_certain = _kill_hits(offense)
    req = incoming_hits_required(line, kill, we_act_first)
    worst = _worst_incoming(incoming_moves)
    safe = _survive(req, worst)
    rel = _relevant_ko_caveats(worst)
    return {
        "line": line,
        "safe": safe,
        "incoming_hits_required": req,
        "our_kill_hits": kill,
        "kill_certain": kill_certain,
        "kill_uncertain": kill is not None and not kill_certain,   # best-roll kill only (not guaranteed)
        "we_act_first": we_act_first,
        "integrity_gate": ("blocked:" + ",".join(
            sorted({str(c.get("cause") or c.get("code") or "caveat") for c in rel}))
            ) if (safe == "uncertain" and rel) else None,
        "passes": safe in _SAFE_HARD and kill is not None and kill_certain,
    }


def grade_over_surface(offense: dict[str, Any] | None, incoming_moves: list[dict[str, Any]] | None,
                       we_act_first: bool, resist_stab: bool) -> dict[str, Any]:
    """Grade ONE (surface, speed) scenario. Returns primary grade (C2/C1/C0), both line results, and
    the UNORDERED descriptors (c1_mode / c2_basis) + predicates."""
    same = line_result("same_field", offense, incoming_moves, we_act_first)
    switch = line_result("switch_in", offense, incoming_moves, we_act_first)
    kill, _ = _kill_hits(offense)
    if switch["passes"]:
        grade = "C2"
    elif same["passes"]:
        grade = "C1"
    else:
        grade = "C0"
    c1_mode = c2_basis = None
    if grade == "C1":
        if we_act_first and kill == 1:
            c1_mode = "clean_revenge"
        elif we_act_first and kill == 2:
            c1_mode = "speed_trade"
        elif not we_act_first:
            c1_mode = "bulk_revenge"
        else:
            c1_mode = "same_field"
    elif grade == "C2":
        c2_basis = "type_advantaged" if resist_stab else "bulk"
    return {
        "grade": grade, "c1_mode": c1_mode, "c2_basis": c2_basis,
        "lines": {"same_field": same, "switch_in": switch},
        "predicates": {"we_act_first": we_act_first, "resist_stab": resist_stab,
                       "type_advantaged": resist_stab},
    }


_GRADE_RANK = {"C0": 0, "C1": 1, "C2": 2}


# --------------------------------------------------------------------------------------------------
# cell -> check object
# --------------------------------------------------------------------------------------------------
def _broad_incoming(cell: dict[str, Any]) -> list[dict[str, Any]]:
    """All damaging moves carried by this concrete opponent build."""
    return (cell.get("defense_damage") or {}).get("moves") or []


def _eid(fmt: str, attacker: str, defender: str, move: str | None) -> str | None:
    return f"ncp:{fmt}:{attacker}|{defender}|{move}" if move else None


# The top-level cell keys build_check consumes. A consumer that SYNTHESISES a cell (oppcache._synth_cell;
# matchup builds them inline) must supply every one — even as None — so a future key added to build_check's
# contract fails LOUDLY in the derived views instead of silently reading as None (a quietly weaker grade).
REQUIRED_CELL_KEYS = frozenset({
    "offense", "defense_damage", "opponent", "speed", "defense_type", "opponent_has_priority",
    "ohko_moves", "incoming_ohko_moves",
})


def assert_cell_contract(cell: dict[str, Any]) -> None:
    """Raise if a synthesised cell omits a key build_check reads (present-with-None is fine). Turns a
    silently-degraded derived grade into a loud failure the moment build_check's contract grows a key."""
    missing = REQUIRED_CELL_KEYS - set(cell)
    if missing:
        raise KeyError(f"synthesised check cell missing required build_check keys: {sorted(missing)}")


def build_check(cell: dict[str, Any], member: str, opp_set: dict[str, Any] | None,
                fmt: str, member_set: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Grade one concrete member-build × opponent-build pair.

    Build uncertainty is represented by other matrix cells, never by hidden archetype or synthetic
    fast lanes inside this cell. Returns None when neither direction has damage evidence.
    """
    if (cell.get("offense") is None and cell.get("defense_damage") is None
            and not cell.get("ohko_moves") and not cell.get("incoming_ohko_moves")):
        return None
    opp = cell.get("opponent")
    speed = cell.get("speed") or {}
    offense = cell.get("offense")
    resist_stab = (cell.get("defense_type") or {}).get("max_effectiveness_vs_member")
    # < 1, not <= 1: a NEUTRAL (1.0x) matchup is not "resisting" the opponent's best type — only a
    # genuinely reduced multiplier is. Counting 1.0x as resist mislabels a neutral C2 as type_advantaged.
    resist_stab = (resist_stab is not None and resist_stab < 1)

    # Priority: our KO move's speed-priority stage lets a SLOW member still act first — but only when the
    # foe can't block it (Queenly Majesty / Dazzling / Armor Tail) AND doesn't itself carry priority, and
    # the move isn't conditional (Sucker Punch fails if the target doesn't attack). It only ever UPGRADES
    # who-acts-first (never claims we're slower). A conditional/blocked case stays a soft candidate.
    our_prio = (offense or {}).get("priority") or 0
    opp_ability = str((opp_set or {}).get("ability") or "").casefold()
    prio_move = str((offense or {}).get("move") or "").casefold()
    ability_blocks = opp_ability in _PRIORITY_BLOCK_ABILITIES
    opp_has_priority = bool(cell.get("opponent_has_priority"))
    priority_first = our_prio > 0 and _priority_acts_first(prio_move, ability_blocks, opp_has_priority)
    priority_kill_candidate = our_prio > 0 and not ability_blocks
    # A NEGATIVE-priority KO move (our best-damage move is Avalanche / Focus Punch ...) strikes in a late
    # bracket, so raw Speed must NOT claim we act first. Priority only ever UPGRADES who-acts-first
    # (design §17 "只上不下"); a negative bracket just blocks the Speed-based first read.
    speed_first_ok = our_prio >= 0

    # This pair names one concrete build on each side. The broad incoming list is the complete move
    # surface carried by that named representative set; no second modal/strict reading exists.
    acts_first = speed.get("acts_first", speed.get("faster"))
    we_first = (acts_first == "member" and speed_first_ok) or priority_first
    graded = grade_over_surface(offense, _broad_incoming(cell), we_first, resist_stab)

    # C0 sub-kind (ORTHOGONAL to the ordinal — it never re-ranks C0): a defensive STALEMATE (we can't
    # reliably close, but the opponent can't 2HKO us either — a wall that just can't finish) vs an
    # outright LOSS. The wall test uses a fixed switch-in defensive budget of 2 (survive the switch hit
    # + one follow-up), INDEPENDENT of our kill, so a walled-but-can't-KO cell no longer collapses into
    # the kill-tied _survive (design §17 — resolves the C0-loss vs C0-wall ambiguity).
    # C0 already means NO winning line (we never cleanly close — a fast certain kill that we survive
    # would have passed to C1/C2). So the split is purely defensive: do they break us in the near term?
    # A slow guaranteed kill (7HKO) is NOT "closing" and must not disqualify the wall.
    worst_broad = _worst_incoming(_broad_incoming(cell))
    c0_kind = None
    if graded["grade"] == "C0":
        c0_kind = "wall_no_ko" if _survive(2, worst_broad) in _SAFE_HARD else "loss"

    resolvability = _resolvability(
        offense, graded, cell, opp_set, member_set, c0_kind, priority_first=priority_first)
    our_ohko = list(cell.get("ohko_moves") or [])
    incoming_ohko = list(cell.get("incoming_ohko_moves") or [])
    if our_ohko or incoming_ohko:
        # Like setup/recovery, OHKO is a real but non-deterministic route: disclose uncertainty without
        # changing the stable ordinal. The positive calc result already filtered obvious immunities.
        parts = []
        if our_ohko:
            parts.append("our probabilistic OHKO route: " + ", ".join(our_ohko))
        if incoming_ohko:
            parts.append("opponent probabilistic OHKO threat: " + ", ".join(incoming_ohko))
        ohko_note = "; ".join(parts) + "; stable C grade unchanged"
        # Preserve ANY prior note, not just a prior CONTESTED one — a "clear"/soft note (e.g. an
        # Unaware/Haze negation the OHKO route is orthogonal to) must not be discarded when OHKO
        # flips the verdict to contested.
        previous = str(resolvability.get("note") or "")
        resolvability = {**resolvability, "verdict": "contested", "active": True,
                         "applies_to": resolvability.get("applies_to") or "ohko_uncertainty",
                         "note": (previous + "; " + ohko_note if previous else ohko_note)}
    intimidate_lane = _intimidate_switch_in(cell, offense, opp_set, member_set, we_first)

    # priority revenge lane (soft): our best PRIORITY move striking first — a weaker move than our
    # hardest hit, but it can revenge a fast frail threat the neutral best-damage grade misses. Grade it
    # with we_act_first forced True (gated like priority_first); informational, never hardens the grade.
    prio_off = (offense or {}).get("priority_offense")
    priority_lane = None
    if prio_off and _priority_acts_first(prio_off.get("move"), ability_blocks, opp_has_priority):
        pg = grade_over_surface(prio_off, _broad_incoming(cell), True, resist_stab)
        priority_lane = {"move": prio_off.get("move"), "priority": prio_off.get("priority"),
                         "grade": pg["grade"],
                         "switch_in_passes": pg["lines"]["switch_in"]["passes"],
                         "same_field_passes": pg["lines"]["same_field"]["passes"],
                         "note": "best PRIORITY move striking first; SOFT lane — never hardens the grade"}

    off_move = (offense or {}).get("move")
    evidence = [e for e in (
        _eid(fmt, member, opp, off_move),
        _eid(fmt, opp, member, (worst_broad or {}).get("move")),
        f"spd:{fmt}:{member}|{opp}",
    ) if e]

    caveats: list[str] = []
    caveat_details: list[dict[str, Any]] = []

    def add_caveat(code: str, text: str, **params: Any) -> None:
        """Keep legacy English prose and add language-invariant presentation coordinates."""
        caveats.append(text)
        caveat_details.append({"code": code, "params": {
            k: (v if v is None or isinstance(v, (str, int, float, bool)) else str(v))
            for k, v in params.items()
        }})

    for scen in (graded,):
        sw = scen["lines"]["switch_in"]
        if sw["safe"] == "approx_true":
            surface = "pair"
            add_caveat(
                "static_switch_approx",
                f"{surface}: switch_in incoming={sw['incoming_hits_required']} static approx "
                "(only OHKO exact)",
                surface=surface, incoming_hits=sw["incoming_hits_required"],
            )
        if sw["integrity_gate"]:
            add_caveat("switch_survival_uncertain",
                       f"switch_in survival uncertain — {sw['integrity_gate']}",
                       gate=sw["integrity_gate"])
    if our_prio > 0 and not priority_first and acts_first != "member":
        reason = ("ability_block" if ability_blocks else
                  "conditional" if prio_move in _CONDITIONAL_PRIORITY else "opponent_priority")
        why = ("blocked by opponent " + opp_ability if reason == "ability_block"
               else "conditional (only if the foe attacks)" if reason == "conditional"
               else "opponent also carries priority")
        add_caveat("priority_not_first",
                   f"our priority KO move is not counted as acting-first — {why}",
                   reason=reason, ability=opp_ability if reason == "ability_block" else None)
    if resolvability["verdict"] == "contested":
        add_caveat("multi_turn_contested", "multi-turn: " + resolvability["note"],
                   applies_to=resolvability.get("applies_to"))

    return {
        "grade": graded["grade"],
        "c1_mode": graded["c1_mode"],
        "c2_basis": graded["c2_basis"],
        "lines": graded["lines"],
        "predicates": {**graded["predicates"],
                       "priority_first": priority_first,
                       "priority_kill_candidate": priority_kill_candidate},
        "condition_profile": "neutral",
        "c0_kind": c0_kind,                              # loss | wall_no_ko | null (only meaningful on C0)
        "resolvability": resolvability,
        "intimidate_lane": intimidate_lane,              # soft -1 Atk switch-in read (null unless we have it)
        "priority_lane": priority_lane,                  # soft: best priority move striking first (null if none)
        "vs_set": {"set_confidence": (opp_set or {}).get("confidence"),
                   "source": (opp_set or {}).get("note")},
        "evaluated_surface": {"floor": "matchup-default",
                              "note": "does not enumerate rare coverage moves below the usage floor"},
        "evidence_ids": evidence,
        "caveats": caveats,
        "caveat_details": caveat_details,
    }


# --------------------------------------------------------------------------------------------------
# multi-turn resolvability gate (design §17) — a FACTS-ONLY qualifier, ORTHOGONAL to C2/C1/C0.
# The ordinal grade is a single-FRAME KO/survival fact; a POSITIVE multi-turn read (a C2 switch-in
# wall, a no-KO stalemate, a slow grind-down) silently assumes the opponent neither sets up nor heals.
# When that assumption is DETECTED false (opponent carries setup/recovery on its evaluated moves) the
# read is `contested` — UNLESS our member structurally negates it: Unaware (纯朴) ignores the
# opponent's stat changes entirely (setup can't break the wall; Iron Defense + Body Press can't either,
# because we ignore the attacker's Def boost), while Haze/Clear Smog/phaze/Taunt/Encore answer it at a
# tempo cost. DETECTED, not modelled (design §14): NO boosted-state damage is recomputed — we surface
# the capability and who negates it, never a simulated trajectory.
# --------------------------------------------------------------------------------------------------
_OFFENSE_SETUP = {                      # boosts an ATTACKING stat -> escalating offense THROUGH a wall
    "swords dance", "dragon dance", "nasty plot", "calm mind", "quiver dance", "bulk up", "work up",
    "tail glow", "growth", "shell smash", "victory dance", "clangorous soul", "no retreat", "coil",
    "howl", "meditate", "sharpen", "fillet away", "belly drum", "geomancy", "take heart",
    "curse", "hone claws", "tidy up",   # +Atk setup (Curse also +Def, Tidy Up also +Spe — both listed
                                        # in the defensive/speed tables too)
    "torch song", "meteor beam",        # damage-AND-boost moves that ramp SpA each use
}
_SPEED_SETUP = {                        # can invalidate a check that relies on moving first
    "agility", "autotomize", "clangorous soul", "dragon dance", "fillet away", "flame charge",
    "geomancy", "no retreat", "quiver dance", "rapid spin", "rock polish", "scale shot",
    "shell smash", "shift gear", "tidy up", "trailblaze", "victory dance",
}
_DEFENSE_SETUP = {                      # boosts a DEFENSIVE stat — inert alone; a threat only paired with
    "iron defense", "acid armor", "cotton guard", "barrier", "amnesia", "cosmic power", "stockpile",
    "defense curl", "harden", "stuff cheeks", "shelter", "curse",    # ...a defensive-stat->offense
                                                                     # converter (Curse feeds Body Press)
}
_DEF_TO_OFFENSE = {"body press", "stored power", "power trip"}   # turns a defensive/any boost into damage
_UNAWARE_BP_BYPASS = {"stored power", "power trip"}  # boosts still raise these moves' base power
_RECOVERY = {                           # opponent out-sustains our chip -> a slow-KO claim is unreliable
    "recover", "roost", "synthesis", "moonlight", "morning sun", "slack off", "soft-boiled",
    "milk drink", "rest", "wish", "shore up", "strength sap", "life dew", "jungle healing",
    "lunar blessing", "heal order", "purify",
}
# our anti-setup ANSWERS (moves): haze/clear smog reset or strip boosts; phazing forces the boosted mon
# out; taunt blocks future setup AND recovery; encore locks a non-attacking setup move. A tempo/timing
# cost -> softens `contested` (disclosed), never silently erases it.
_ANTI_SETUP_MOVES = {"haze", "clear smog", "roar", "whirlwind", "dragon tail", "circle throw",
                     "taunt", "encore"}
_ANTI_RECOVERY_MOVES = {"taunt", "roar", "whirlwind", "dragon tail", "circle throw"}  # block/skip the heal
_ANTI_SETUP_ABILITY = {"unaware"}       # 纯朴: ignores the opponent's stat changes entirely (both ways)


def _resolvability(offense: dict[str, Any] | None, graded: dict[str, Any], cell: dict[str, Any],
                   opp_set: dict[str, Any] | None, member_set: dict[str, Any] | None,
                   c0_kind: str | None = None, *, priority_first: bool = False) -> dict[str, Any]:
    """Qualify the cell's POSITIVE multi-turn read (C2 wall / stalemate / slow grind) as clean vs
    contested by DETECTING opponent setup/recovery and OUR negation of it. Facts only — no boosted
    damage is recomputed; a `contested` verdict names the capability and who (if anyone) answers it."""
    # opponent capability over the SAME move names the grade already saw (modal joint U broad surface).
    opp_moves = {m.lower() for m in ((opp_set or {}).get("moves") or []) if isinstance(m, str)}
    opp_moves |= {(f.get("move") or "").lower() for f in _broad_incoming(cell)}
    off_setup = sorted(opp_moves & _OFFENSE_SETUP)
    speed_setup = sorted(opp_moves & _SPEED_SETUP)
    def_setup = sorted(opp_moves & _DEFENSE_SETUP)
    converters = sorted(opp_moves & _DEF_TO_OFFENSE)
    recovery = sorted(opp_moves & _RECOVERY)
    defense_conversion = bool(def_setup) and bool(converters)
    speed_escalation = (bool(speed_setup) and graded["predicates"].get("we_act_first")
                        and not priority_first)
    # Stored Power / Power Trip scale with the TOTAL boost count (Speed boosts included), which Unaware
    # does not ignore — so an Agility (speed-only) + Stored Power line still escalates through a wall.
    bp_bypass = bool((set(off_setup) | set(def_setup) | set(speed_setup)) and (opp_moves & _UNAWARE_BP_BYPASS))
    offense_escalation = bool(off_setup) or defense_conversion or speed_escalation or bp_bypass

    # which positive/neutral read (if any) is at risk. Qualified claims: a C2 switch-in wall, a slow
    # (>=2-hit) grind we survive, or a defensive STALEMATE (`c0_kind == wall_no_ko` — we can't close but
    # they can't 2HKO us). A plain loss and a clean fast (OHKO) revenge carry no multi-turn claim.
    kill, _ = _kill_hits(offense)
    sw, sf = graded["lines"]["switch_in"], graded["lines"]["same_field"]
    we_survive = sf["safe"] in _SAFE_HARD or sw["safe"] in _SAFE_HARD
    if graded["grade"] == "C2":
        claim = "switch_in_wall"
    elif kill is not None and kill >= 2 and we_survive:
        claim = "slow_grind"
    elif c0_kind == "wall_no_ko":
        claim = "stalemate"
    else:
        claim = None
    if claim is None:
        return {"verdict": "clean", "applies_to": None, "active": False,
                "opponent": {}, "our_negation": {}, "note": "no positive multi-turn claim to qualify"}

    # recovery bites only a claim that ASSERTS a KO we must grind for (a fast OHKO or a pure stalemate
    # is unaffected by the opponent healing).
    recovery_threat = (bool(recovery) and claim in ("switch_in_wall", "slow_grind")
                       and kill is not None and kill >= 2)
    if not (offense_escalation or recovery_threat):
        note = ("opponent recovery detected but it does not threaten a stalemate (a mutual non-KO draw)"
                if recovery and claim == "stalemate"
                else "no setup/recovery that threatens this read on the opponent's evaluated moves")
        return {"verdict": "clean", "applies_to": claim, "active": True,
                "opponent": ({"recovery": recovery} if recovery else {}), "our_negation": {}, "note": note}

    # our negation. Unaware HARD-answers every stat-boost escalation leg (it ignores the boosts); moves
    # SOFT-answer at a tempo cost. Unaware does NOT stop recovery (only Taunt/phaze do).
    our_ability = ((member_set or {}).get("ability") or "").lower()
    our_moves = {m.lower() for m in ((member_set or {}).get("moves") or []) if isinstance(m, str)}
    hard = our_ability in _ANTI_SETUP_ABILITY
    setup_answers = sorted(our_moves & _ANTI_SETUP_MOVES)
    recov_answers = sorted(our_moves & _ANTI_RECOVERY_MOVES)
    # Unaware clears ordinary attacking-stat escalation and Body Press's Defense-stage scaling, but it
    # does not ignore Speed for turn order or the boost-count base-power scaling of Stored Power/Power Trip.
    setup_unnegated = offense_escalation and (not hard or speed_escalation or bp_bypass)
    recovery_unnegated = recovery_threat and not recov_answers

    reasons: list[str] = []
    if offense_escalation:
        reasons.append("opponent_offense_setup" if off_setup else
                       "opponent_def_setup+converter" if defense_conversion else
                       "opponent_speed_setup")
        if speed_escalation:
            reasons.append("speed_setup_can_flip_turn_order")
        if bp_bypass:
            reasons.append("stored_power_or_power_trip_scales_through_unaware")
    if recovery_threat:
        reasons.append("opponent_recovery")

    our_neg: dict[str, Any] = {}
    if hard and offense_escalation:
        our_neg["ability"] = ("Unaware (partial: Speed order and Stored Power/Power Trip base power "
                              "are not ignored)" if (speed_escalation or bp_bypass) else "Unaware")
    answers = sorted(set(setup_answers) | set(recov_answers))
    if answers:
        our_neg["moves"] = answers

    contested = setup_unnegated or recovery_unnegated
    if not contested:
        verdict = "clean"
        note = ("our Unaware ignores the relevant opponent stat changes — setup cannot break this wall"
                if hard else "our moves answer the detected threat before it snowballs")
    else:
        verdict = "contested"
        note = ("DETECTED opponent " + " + ".join(reasons) + "; the static grade assumes neither — treat "
                "the positive read as contested"
                + (f" (we carry a tempo-cost answer: {', '.join(answers)})" if answers else ""))
    return {
        "verdict": verdict, "applies_to": claim, "active": True,
        "opponent": {k: v for k, v in (("offense_setup", off_setup), ("speed_setup", speed_setup),
                                       ("defense_setup", def_setup),
                                       ("converters", converters), ("recovery", recovery)) if v},
        "our_negation": our_neg, "note": note,
    }


# --------------------------------------------------------------------------------------------------
# Intimidate lane (design §17) — a SOFT `with_own_intimidate` switch-in read, never hardens the grade.
# When OUR member has Intimidate, switching in drops a physical attacker's Attack one stage: damage is
# linear in Attack so a -1 hit is exactly x2/3. We recompute ONLY the switch-in survival over the
# physical incoming (scaled x2/3), gated OFF by an Intimidate-immune foe. Pure arithmetic — no extra ncp.
# --------------------------------------------------------------------------------------------------
def _intimidate_switch_in(cell: dict[str, Any], offense: dict[str, Any] | None,
                          opp_set: dict[str, Any] | None, member_set: dict[str, Any] | None,
                          we_first: bool) -> dict[str, Any] | None:
    """The switch-in survival WITH our Intimidate applied (physical incoming x2/3). None when our member
    has no Intimidate. Informational only: a softer switch-in read, never folded into `grade`."""
    if str((member_set or {}).get("ability") or "").casefold() != "intimidate":
        return None
    opp_ab = str((opp_set or {}).get("ability") or "").casefold()
    opp_item = str((opp_set or {}).get("item") or "").casefold()
    if opp_ab in INTIMIDATE_NO_RELIEF_ABILITIES:
        if opp_ab in INTIMIDATE_BACKFIRE_ABILITIES:
            why = f"opponent {opp_ab} turns Intimidate into a NET +Attack — the drop backfires, not just blocked"
        elif opp_ab in INTIMIDATE_REFLECT_ABILITIES:
            why = f"opponent {opp_ab} reflects the Intimidate drop back onto our member (the foe keeps full Attack)"
        else:
            why = f"opponent {opp_ab} is immune to the Intimidate Attack drop (net 0, no -1)"
        return {"applicable": False, "reason": why}
    if opp_item in INTIMIDATE_BLOCKING_ITEMS:
        return {"applicable": False, "reason": f"opponent {opp_item} blocks the Intimidate Attack drop"}
    broad = _broad_incoming(cell)
    physical = [f for f in broad if str(f.get("category") or "").casefold() == "physical"
                and str(f.get("move") or "").casefold() not in _INTIMIDATE_ATTACK_INDEPENDENT]
    if opp_ab == "competitive" and any(
            str(f.get("category") or "").casefold() == "special" for f in broad):
        return {"applicable": False,
                "reason": "opponent Competitive raises SpA after Intimidate; mixed threat surface not rescaled"}
    if not physical:
        return {"applicable": False, "reason": "no physical threat on the evaluated surface"}
    multiplier = 1 / 2 if opp_ab == "simple" else 2 / 3
    phys_ids = {id(f) for f in physical}     # identity, not dict-equality: O(1) and no equal-entry collision
    scaled: list[dict[str, Any]] = []
    for f in broad:
        if id(f) not in phys_ids:
            scaled.append(f)
            continue
        g = dict(f)
        for k in ("max_percent", "min_percent"):
            if isinstance(f.get(k), (int, float)):
                g[k] = f[k] * multiplier
        g["ko_possible"], g["ko_guaranteed"] = ko_hits_from_pct(g.get("max_percent")), ko_hits_from_pct(g.get("min_percent"))
        g.pop("ko_chance", None)             # the recovery-aware verdict is stale once we rescale damage
        scaled.append(g)
    line = line_result("switch_in", offense, scaled, we_first)
    return {"applicable": True,
            "switch_in": {"safe": line["safe"], "passes": line["passes"],
                           "incoming_hits_required": line["incoming_hits_required"]},
            "note": f"Attack-based physical incoming rescaled x{multiplier:g} (-1 Atk) on the switch-in; "
                    "SOFT lane — never hardens the grade. APPROXIMATE: scales the already-rounded damage "
                    "percent (not the integer per-hit pipeline) and drops the recovery-aware ko_chance, so "
                    "a band that straddles a KO boundary can read a turn off — informational only"}


# --------------------------------------------------------------------------------------------------
# coverage roll-up (per opponent species, across observed build variants and the team)
# --------------------------------------------------------------------------------------------------
def coverage_summary(members: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive an explainable species portfolio from atomic build-pair checks.

    Each opponent variant first keeps the strongest answer among the supplied members. The species'
    ``observed_floor`` is then the weakest of those per-variant answers and always names its witness
    variant(s). Calculation completeness and observed sample coverage are separate facts.
    """
    per_opp: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for mrow in members:
        who = mrow.get("member")
        for c in mrow.get("cells", []):
            opp = c.get("opponent")
            if not opp:
                continue
            if opp not in per_opp:
                per_opp[opp] = {"opponent": opp, "usage_rank": c.get("usage_rank"), "variants": {}}
                order.append(opp)
            vid = c.get("opponent_variant") or opp
            variants = per_opp[opp]["variants"]
            v = variants.setdefault(vid, {
                "variant_id": vid,
                "coverage": c.get("opponent_coverage"),
                "is_modal": bool(c.get("opponent_is_modal") or vid == opp),
                "grade": None,
                "best_by": [],
                "c0_kind": None,
                "wall_no_ko_by": [],
                "contested_by": [],
            })
            chk = c.get("check")
            if not chk:
                continue
            g = chk["grade"]
            if v["grade"] is None or _GRADE_RANK[g] > _GRADE_RANK[v["grade"]]:
                v["grade"] = g
                v["best_by"] = [who]
                v["c0_kind"] = chk.get("c0_kind") if g == "C0" else None
            elif g == v["grade"]:
                if who not in v["best_by"]:
                    v["best_by"].append(who)
                if g == "C0" and chk.get("c0_kind") == "loss":
                    v["c0_kind"] = "loss"
            if g == "C0" and chk.get("c0_kind") == "wall_no_ko" and who not in v["wall_no_ko_by"]:
                v["wall_no_ko_by"].append(who)
            res = chk.get("resolvability") or {}
            if res.get("verdict") == "contested" and who not in v["contested_by"]:
                v["contested_by"].append(who)

    rows: list[dict[str, Any]] = []
    dist: dict[str, int] = {"C2": 0, "C1": 0, "C0": 0}
    for opp in order:
        base = per_opp[opp]
        variants = list(base["variants"].values())
        variants.sort(key=lambda v: (not v["is_modal"], -(v["coverage"] or 0), v["variant_id"]))
        calculated = [v for v in variants if v["grade"] is not None]
        floor = (min((v["grade"] for v in calculated), key=lambda g: _GRADE_RANK[g])
                 if calculated else None)
        witnesses = [v["variant_id"] for v in calculated if v["grade"] == floor]
        representative = next((v for v in variants if v["is_modal"]), variants[0] if variants else None)
        numeric = [float(v["coverage"]) for v in variants if isinstance(v.get("coverage"), (int, float))]
        # Retained variant shares are rounded independently by the evidence source. Their sum can
        # therefore land a hair outside the probability domain (for example 1.0001), even though
        # the underlying sample covers exactly 100%. Keep every aggregate share in [0, 1]; public
        # consumers validate that invariant and should not lose the whole diagnosis to source
        # rounding noise.
        represented = (round(min(1.0, max(0.0, sum(numeric))), 4)
                       if numeric else None)
        calculated_coverage = (round(min(1.0, max(0.0, sum(
                                           float(v["coverage"]) for v in calculated
                                           if isinstance(v.get("coverage"), (int, float))
                                       ))), 4)
                               if numeric else None)
        row = {
            "opponent": opp,
            "usage_rank": base["usage_rank"],
            "representative": ({"variant_id": representative["variant_id"],
                                "grade": representative["grade"],
                                "best_by": representative["best_by"]}
                               if representative else None),
            "observed_floor": {"grade": floor, "witness_variant_ids": witnesses},
            "variants": variants,
            "coverage": {
                "represented": represented,
                "calculated": calculated_coverage,
                "unrepresented": round(max(0.0, 1.0 - represented), 4)
                                 if represented is not None else None,
            },
            "calculation_complete": bool(variants) and len(calculated) == len(variants),
        }
        rows.append(row)
        if floor:
            dist[floor] += 1
    holes = [r for r in rows
             if r["observed_floor"]["grade"] is not None
             and _GRADE_RANK[r["observed_floor"]["grade"]] <= 1]
    return {
        "by_opponent": rows,
        "holes": holes,
        "grade_distribution": dist,
        "note": ("Each variant keeps the strongest member answer; observed_floor is the weakest of "
                 "those answers and names the witness variants. Coverage is observed sample share; "
                 "calculation_complete is a separate fact. C grades remain ordinal labels, never a score."),
    }
