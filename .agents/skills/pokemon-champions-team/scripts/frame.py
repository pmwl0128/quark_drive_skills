#!/usr/bin/env python
"""Assembly front-door (UEP P4.5): hand the AI a DATA-GROUNDED skeleton before it assembles, so the
one uncovered creative step — `[assemble candidates]` — cannot be hand-waved from the training prior.

Why this exists (the observed failure, design §19.2.C):
A capable AI's dominant failure is NOT an illegal team (validate/diagnose catch those) — it is
MODE-COLLAPSE at assembly: it consults the anchor's repset, then hand-writes the other five members
from its prior (a real session gave Crabominable @ Life Orb while 9/9 real teams run @ Crabominite).
landscape EXPOSES the real structural distribution but the AI can read it and ignore it — exposure is
passive. `frame` makes grounding LOAD-BEARING: it extracts, from real joint teams (repset primary),
the recurring core + its real (item,ability) sets, and the slate gate later binds each candidate's
core-bearer sets against that repset evidence (design §19.10, the third anti-mode-collapse leg after
① the convergence scaffold and ② landscape exposure).

What it is NOT (hard boundaries — same as every library operator, pinned by tests):
- NOT a team retriever. A skeleton is an AGGREGATE (co-occurrence within a structural group + per-species
  repset), never a verbatim stored team; it always leaves >= 1 flex slot open. If the AI's finished team
  happens to reproduce a stored one, the EXISTING library guardrail (libsearch.library_copy_index +
  answer-audit observed_provenance) is what fires — frame does not re-solve netdecking.
- NO composite score, NO winner, NO "best archetype". Skeletons carry counts/share/confidence and are
  ordered by PREVALENCE (a view ordering under the meta_conformance knob, exactly like landscape.order),
  never scored. The banned-vocabulary guard (best_*/recommended_*/*_score/strength/fit) walks the tree.
- Meta is AUXILIARY only (design §19.3 two populations): grounded sets come ONLY from repset (real
  joint). A member with no repset is left ungrounded (set null) + honest disclosure — frame NEVER
  stitches a joint set out of meta item/move/nature marginals.
- Structural facts are FACTS: `structural_profile` reports the group's speed_control modes / role norms
  through the shared profile vocabulary; it never names a team type. `frame_id` is a mechanical hash,
  not a team-name template.

Anti-mediocrity (design §19.10, the subtle one): frame emits EVERY structural group clearing MIN_SAMPLE,
ordered by the knob (proven=common-first / off_meta=rare-first over the SAME facts); a display cap only
trims the generic popular tail, and any group carrying a constraint member (anchor/prefer/locked) is
retained regardless — so an expressive / off-meta anchor's low-support-but-real frame is never
suppressed (that suppression would re-manufacture the very mode-collapse this operator exists to break).

Tier 1 (current): a shallow partition by ONE structural signature + repset attachment — mostly
orchestration of landscape/repset/team_profile. The signature is FORMAT-SPECIFIC because the two
metagames commit to structure on different axes:
- doubles keys on the DEFINING speed-control modes (Trick Room / Tailwind / weather-ability / scarf);
- singles keys on the hazard / setup / recovery / pivot commitments, because speed control does not
  separate singles teams at all (the overwhelming majority carry no defining mode, so a speed-control
  key collapses the pool into one undifferentiated bucket whose co-occurrence clears no support bar —
  a zero-skeleton frame, which is the one output this operator exists to prevent).
A row whose structure is NOT OBSERVABLE (species-only library records: no declared moves, items or
abilities) carries no signature in either format and is grouped as such — "we could not read its
structure" is a different fact from "it carries none", and grouping the two together is what makes a
data gap look like an archetype.
Tier 2 (profile-vector clustering) lands only if Tier-1 groups prove to blend distinct archetypes.

Fallback ladder (an anchor NO current-rule real team carries — an empty anchor pool). Zero skeletons
there left the online wizard nothing to assemble on. The anchor now gets skeletons computed over the
evidence pool its fallback-table recipe names (`fallback_frames.py`, rebuilt every data refresh):
  - history   — older-rule teams built around the anchor itself (rules only ever add species, moves
                and items, so those teams stay valid evidence as they are);
  - partners  — current teams carrying the anchor's specific usage partners together; those partners
                become the backbone and the anchor joins them;
  - stand_in  — the frames of a data-backed species doing the same job, the anchor in its place;
and, only if the table has nothing, whole-library frames lent to the anchor. Every fallback skeleton
is marked `fallback`, carries low confidence and binds softly — the slate reports a departure from it
and never eliminates on one, because nothing in it was observed with the anchor under this rule.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable

import completeness
import team_profile
import team_i18n as i18n
from canonhash import content_hash as _content_hash
from landscape import THIN_BAR, _matches, _team_species, _filter_label
import query_identity
from mega_facts import OBSERVED_NORM_MIN_SAMPLE, mega_slot_distribution
from repset import MIN_SAMPLE, representative_sets_from_teams

# A species must co-occur in at least this share of a structural GROUP's teams to be a core candidate
# (the recurring backbone), AND clear MIN_SAMPLE in absolute count. Tunable; 0.5 = "a majority of the
# archetype's real teams run it".
CORE_SUPPORT = 0.5
# The SECOND tier. A species between PARTNER_SUPPORT and CORE_SUPPORT recurs in the group without
# being its backbone: too frequent to present as an arbitrary filler, not frequent enough to bind a
# candidate against. It is emitted as a core candidate with role `recurring-partner` so it gets the
# same REAL repset grounding (the whole value: the AI is handed the right (item,ability) joint), but
# the slate binding only REPORTS a deviation on it — it never eliminates. A single 0.5 bar sent this
# whole band to observed_fillers, which carries no grounded set, so the band's real joint evidence
# was reachable only by a second manual repset call the assembly step does not make.
PARTNER_SUPPORT = 0.3
# Cap core candidates so a skeleton always leaves >= 1 open flex slot (never a full-6 skeleton — that
# would be a verbatim-team by the back door; the library guardrail is the netdeck backstop, not frame).
MAX_CORE = 5
# Display cap for the generic (no-anchor) case where the whole library partitions into many groups.
# Anchor frames are never capped below constraint-relevant retention (see module docstring).
MAX_FRAMES = 8
# How many whole-library skeletons the last fallback rung lends an anchor with no data of its own.
GENERIC_FALLBACK_FRAMES = 3


def frame_fingerprint(audit_fp: str | None, skeletons: list[dict[str, Any]],
                      anchor: list[str] | None, fmt: str) -> str:
    """The frame_receipt fingerprint: audit fp (chain continuity) + a content digest of the emitted
    skeletons + anchor + format. Extracted so the slate gate RECOMPUTES the same construction from the
    saved frame output (tamper-evident: an edited skeleton — e.g. a widened core cluster to sneak an
    ungrounded set past the binding — will not reproduce this fingerprint)."""
    return _content_hash({"audit": audit_fp, "identity_basis": "dex_battle_form",
                          "skeletons": [_content_hash(s, 16) for s in skeletons],
                          "anchor": sorted(anchor or []), "format": fmt}, 24)


def make_receipt(audit_fp: str | None, skeletons: list[dict[str, Any]],
                 anchor: list[str] | None, fmt: str) -> dict[str, Any]:
    """The frame_receipt the slate gate consumes (extends the chain: audit -> frame -> slate). The
    fingerprint is tamper-evident (recomputed by slate from the saved frame output)."""
    return {
        "kind": "frame_receipt",
        "identity_basis": "dex_battle_form",
        "fingerprint": frame_fingerprint(audit_fp, skeletons, anchor, fmt),
        "framed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "audit_fingerprint": audit_fp,
        "format": fmt,
    }


# The archetype-DEFINING speed-control axes. `soft` (Icy Wind / Thunder Wave foe-side slowdown) is
# deliberately excluded from the group KEY — it is supplementary, carried by many teams across every
# archetype, so keying on it fragments the pool into combinatorial noise (soft+tailwind, soft+trickroom…)
# without separating real archetypes. It is still reported in each frame's structural_profile.
_DEFINING_MODES = ("trickroom", "tailwind", "ability", "scarf")


def _mode_signature(profile: dict[str, Any]) -> tuple[str, ...]:
    """The DOUBLES structural GROUP key: the team's DEFINING speed-control modes (Trick Room /
    Tailwind / weather-ability / scarf), sorted; ('none',) when it carries none of them. Tier-1
    shallow partition — the full profile vector is the Tier-2 clustering key if these groups prove to
    blend archetypes (e.g. sun vs rain both fold to 'ability' here; that is the documented Tier-1
    boundary)."""
    modes = set((profile.get("speed_control_mode") or {}).get("modes") or [])
    defining = tuple(sorted(m for m in modes if m in _DEFINING_MODES))
    return defining or ("none",)


# The SINGLES defining axes: (signature name, role_composition tag, member count that counts as a
# commitment). Speed control is NOT one of them — it is reported in structural_profile like `soft` is
# for doubles, but in singles it is carried by a small minority and cannot key the partition.
#
# The count bars are the tuning surface, and they are not all 1 because presence alone is not a
# commitment for every axis:
#   hazard_set  >= 1  setting hazards at all is the commitment (a team either invests a slot or not);
#   setup       >= 2  one setup move is ordinary on a member that is otherwise not a wincon; two or
#                     more is the team being BUILT around setup;
#   recovery    >= 2  Leftovers/Sitrus count as recovery via item coverage, so >= 1 is near-universal
#                     and separates nothing; >= 2 is the genuinely attrition-leaning build;
#   pivot       >= 1  a pivot move is a deliberate momentum slot, rarely incidental.
_SINGLE_AXES: tuple[tuple[str, str, int], ...] = (
    ("hazard", "hazard_set", 1),
    ("setup", "setup", 2),
    ("recovery", "recovery", 2),
    ("pivot", "pivot", 1),
)
# The signature of a row whose structure could not be READ (see the module docstring). Kept distinct
# from ('none',), which is the positive fact "structure was read and it commits to no defining axis".
_UNPROFILED = ("unprofiled",)


def _structure_observed(team: dict[str, Any]) -> bool:
    """Whether this row's structural signature can be READ at all. Both Tier-1 keys are move-driven
    (Trick Room / Tailwind; hazard / setup / recovery / pivot), so a species-only library record — a
    ladder roster published as six names with no set — supports NO signature: its every role count is
    0 and its only speed-control mode is 'none' because the source never published the set, not
    because the team commits to nothing. diagnose already refuses to report a phantom gap for such a
    member; the partition must not invent one either.

    The bar is the same authority diagnose uses (`completeness.moveset_authoritative`), not "did any
    signal leak through": a bare row of a single-ability species still yields that ability's coverage,
    and reading a structural commitment out of THAT would put a data gap in a real archetype's bucket.
    """
    return any(completeness.moveset_authoritative(m.get("completeness"),
                                                  has_moves=bool(m.get("moves")))
               for m in (team.get("pokemon") or []))


def _group_signature(team: dict[str, Any], profile: dict[str, Any], fmt: str) -> tuple[str, ...]:
    """The Tier-1 structural GROUP key for one team, by format (module docstring)."""
    if not _structure_observed(team):
        return _UNPROFILED
    if fmt == "single":
        rc = profile.get("role_composition") or {}
        sig = tuple(name for name, tag, bar in _SINGLE_AXES
                    if (rc.get(tag) or {}).get("count", 0) >= bar)
        return sig or ("none",)
    return _mode_signature(profile)


def _signature_axis(sig: tuple[str, ...], fmt: str) -> str:
    """What the group key MEANS, so a reader (and the md/web renderers) never has to infer it from
    the format or mistake a role signature for a speed-control one."""
    if sig == ("__all__",):
        return "unpartitioned_pool"
    if sig == _UNPROFILED:
        return "structure_not_observed"
    return "role_signature" if fmt == "single" else "speed_control"


def _cooccurrence(teams: list[dict[str, Any]], exclude: set[str]) -> dict[str, int]:
    """{species: # teams in the group that carry it}, excluding the anchor species (which is core by
    construction). Presence per team (deduped), the same yardstick landscape's observed_cores uses."""
    co: Counter = Counter()
    for t in teams:
        for sp in _team_species(t):
            if sp and sp not in exclude:
                co[sp] += 1
    return dict(co)


def _grounding_for(species: str, fmt: str, group_teams: list[dict[str, Any]],
                   all_teams: list[dict[str, Any]], item: str | None) -> dict[str, Any] | None:
    """Attach the species' REAL repset archetypes as the grounded set + the valid (item,ability)
    clusters the slate binding checks against. Prefer the GROUP-specific archetype (faithful to this
    frame's structure); fall back to the species' library-wide archetype when the group is too thin
    (template-eligible < MIN_SAMPLE), tagged `set_source` for honesty. None when neither clears the bar
    (truly off-meta / thin) — the caller leaves the set ungrounded and discloses it (meta is NEVER
    stitched into a joint set here)."""
    for teams, src in ((group_teams, "group"), (all_teams, "species-overall")):
        reps = representative_sets_from_teams(species, fmt, teams, item=item)
        if reps:
            primary = reps[0]
            clusters = [r["cluster"] for r in reps if r.get("cluster")]
            if not clusters and primary.get("item") is not None:  # fragmented fallback: single global modal
                clusters = [{"item": primary.get("item"), "ability": primary.get("ability")}]
            ref = f"repset:{species}:{fmt}" + (f":{src}" if src != "group" else "")
            return {
                "primary": {k: primary.get(k) for k in
                            ("item", "ability", "nature", "moves", "sps", "spread_origin",
                             "share", "coverage", "confidence")},
                **({"configuration_basis": primary["configuration_basis"],
                    "registration_modes": primary["registration_modes"],
                    "registration_sample": primary["registration_sample"]}
                   if primary.get("registration_modes") else {}),
                "clusters": clusters,               # the (item,ability) pairs the binding accepts as grounded
                "set_source": src,
                "grounding_ref": ref,
                "fragmented": bool(primary.get("fragmented")),
            }
    return None


def _skeleton(sig: tuple[str, ...], group: list[tuple[dict, dict]], *, fmt: str,
              pool_size: int, pool_cooccur: dict[str, int],
              pool_mega_distribution: dict[str, Any],
              anchor_members: list[dict[str, Any]], all_teams: list[dict[str, Any]],
              meta_fn: Callable[[str], dict | None] | None) -> dict[str, Any]:
    """Build one grounded skeleton over a structural group. `group` = [(team, profile), ...]; `sig` is
    the group's speed_control signature (or ('__all__',) for the unpartitioned single frame)."""
    g_teams = [t for t, _ in group]
    g_profs = [p for _, p in group]
    n = len(g_teams)
    anchor_species = [m["species"] for m in anchor_members]
    anchor_item = {m["species"]: m.get("item") for m in anchor_members}
    cooccur = _cooccurrence(g_teams, set(anchor_species))

    # --- structural_profile: the group's structure through the shared profile vocabulary (FACTS) ----
    mode_presence: Counter = Counter()
    role_presence: Counter = Counter()
    role_hists: dict[str, Counter] = {}
    for p in g_profs:
        for mode in (p.get("speed_control_mode") or {}).get("modes") or []:
            mode_presence[mode] += 1
        for tag, rc in (p.get("role_composition") or {}).items():
            if rc.get("count", 0) > 0:
                role_presence[tag] += 1
            role_hists.setdefault(tag, Counter())[rc.get("count", 0)] += 1
    structural_profile = {
        "speed_control_modes": {m: {"count": c, "share": round(c / n, 3)}
                                for m, c in sorted(mode_presence.items(), key=lambda kv: (-kv[1], kv[0]))},
        "structural_signals": {t: {"count": c, "share": round(c / n, 3)}
                               for t, c in sorted(role_presence.items(), key=lambda kv: (-kv[1], kv[0]))},
        "role_composition_norms": {tag: {str(k): v for k, v in sorted(h.items())}
                                   for tag, h in sorted(role_hists.items())},
    }

    # --- core candidates, TIERED: anchor first, then the within-group backbone (share >=
    # CORE_SUPPORT), then the recurring-partner band (PARTNER_SUPPORT <= share < CORE_SUPPORT). Both
    # derived tiers are ordered by prevalence and both get real repset grounding; only the tier NAME
    # differs, and it is what the slate binding reads to decide report-vs-eliminate. ---------------
    ranked = sorted(((sp, c) for sp, c in cooccur.items() if c >= MIN_SAMPLE),
                    key=lambda kv: (-kv[1], kv[0]))
    core_list: list[tuple[str, str, str | None]] = [   # (species, role, item_filter)
        (sp, "anchor", anchor_item.get(sp)) for sp in anchor_species]
    for role, low, high in (("core-partner", CORE_SUPPORT, None),
                            ("recurring-partner", PARTNER_SUPPORT, CORE_SUPPORT)):
        for sp, c in ranked:
            if len(core_list) >= MAX_CORE:
                break
            share = c / n
            if share >= low and (high is None or share < high):
                core_list.append((sp, role, None))

    core_candidates = []
    for sp, role, item in core_list:
        c = cooccur.get(sp, n if role == "anchor" else 0)
        grounding = _grounding_for(sp, fmt, g_teams, all_teams, item)
        prim = (grounding or {}).get("primary") or {}
        core_candidates.append({
            "species": sp,
            "role": role,
            "within_group_share": round(c / n, 3),
            "within_group_count": c,
            # Two explicit views (glue vs archetype-defining), NEVER folded into a distinctiveness score:
            # a member high on both is glue; high within-group but low pool-wide is what defines this frame.
            # The anchor is in every pool team by construction (pool_cooccur excludes it), so its pool_share
            # is 1.0 — not the group/pool fallback that would shadow prevalence.share.
            "pool_share": 1.0 if role == "anchor" else (
                round(pool_cooccur.get(sp, c) / pool_size, 3) if pool_size else None),
            "grounding": grounding,
            "set_guidance": ({"moves": prim.get("moves"), "nature": prim.get("nature"),
                              "sps": prim.get("sps")} if grounding else None),
            "confidence": prim.get("confidence") if grounding else "low",
            "meta_auxiliary": (meta_fn(sp) if meta_fn else None),
        })

    # --- observed facts (DESCRIPTIVE — "how real teams in this group vary", NOT a to-fill checklist;
    # observed_mega_slots is a fact, not a reserved second-Mega slot) --------------------------------
    core_species = {sp for sp, _, _ in core_list}
    fillers = sorted(((sp, c) for sp, c in cooccur.items()
                      if c >= MIN_SAMPLE and sp not in core_species),
                     key=lambda kv: (-kv[1], kv[0]))
    group_mega_distribution = mega_slot_distribution(g_profs)
    use_group_mega = group_mega_distribution["sample_count"] >= OBSERVED_NORM_MIN_SAMPLE
    mega_reference_distribution = (group_mega_distribution if use_group_mega
                                   else pool_mega_distribution)
    observed_facts = {
        "observed_mega_slots": group_mega_distribution,
        # The local structural group is the first authority.  A thin group falls back to the whole
        # frame pool (same anchor/format) instead of asking the AI to infer a norm from anecdotes.
        # This object lives inside the fingerprinted skeleton, so slate can trust it as a gate input.
        "mega_registration_reference": {
            "basis": "frame_group" if use_group_mega else "frame_pool",
            "distribution": mega_reference_distribution,
            "confidence": ("medium" if mega_reference_distribution["sample_count"]
                            >= OBSERVED_NORM_MIN_SAMPLE else "low"),
        },
        "observed_fillers": [{"species": sp, "count": c, "within_group_share": round(c / n, 3)}
                             for sp, c in fillers],
        "role_composition_norms": structural_profile["role_composition_norms"],
    }

    tiers = Counter(role for _, role, _ in core_list)
    n_core, n_partner = tiers.get("core-partner", 0), tiers.get("recurring-partner", 0)
    structure_observed = sig != _UNPROFILED
    thin = n < THIN_BAR
    # An EMPTY BACKBONE is never `medium`. A skeleton whose co-occurrence clears no support bar states
    # a real structural distribution and nothing to build on; labelling that "medium" told the reader
    # the six open slots were a considered result rather than an absence of evidence. The anchor does
    # not count — it is core by construction, not an observation about the group.
    if thin:
        conf, reason = "low", "thin-observed-sample"
    elif not structure_observed:
        conf, reason = "low", "structure-not-observed"
    elif not (n_core or n_partner):
        conf, reason = "low", "no-recurring-backbone"
    elif not n_core:
        conf, reason = "low", "recurring-partners-only"
    else:
        conf, reason = "medium", "observed-sample"
    skeleton = {
        "group_signature": {"axis": _signature_axis(sig, fmt), "key": list(sig)},
        "structural_profile": structural_profile,
        "prevalence": {"count": n, "share": round(n / pool_size, 3) if pool_size else 0.0},
        "core_candidates": core_candidates,
        "core_tiers": {"anchor": tiers.get("anchor", 0), "core": n_core,
                       "recurring_partner": n_partner},
        # open_count = slots this skeleton says nothing about. substitutable_count = the
        # recurring-partner slots it DOES fill but does not bind, so the reader can see how much of a
        # "full" skeleton is actually soft.
        "flex_slots": {"open_count": max(0, 6 - len(core_candidates)),
                       "substitutable_count": n_partner},
        "observed_facts": observed_facts,
        "thin": thin,
        "confidence": conf,
        "confidence_reason": reason,
        "notes": [
            "core_candidates = within-group co-occurrence + each member's REAL repset (item,ability) "
            "joint, in TWO tiers: role 'core-partner' (share >= %.2f, the group's backbone — a "
            "substitute needs its OWN repset basis or an off_meta declaration, which the slate "
            "binding checks) and role 'recurring-partner' (%.2f–%.2f — recurs without being the "
            "backbone; substituting one costs NO declared basis). An aggregate over the group, NOT a "
            "verbatim team and NOT a mandated roster." % (CORE_SUPPORT, PARTNER_SUPPORT, CORE_SUPPORT),
            "set_guidance (moves/nature/sps) is a REFERENCE, not a hard lock; only a core-bearer's "
            "(item,ability) leaving its repset clusters WITHOUT an acknowledged deviation is a red flag.",
            "observed_facts is DESCRIPTIVE — how real teams in this group vary at the open slots; "
            "observed_mega_slots is a fact about registered Mega counts, NOT a reserved second-Mega "
            "slot. Second Mega / coverage / utility are the AI's independent calls (flex_slots).",
        ],
    }
    if not (n_core or n_partner):
        skeleton["notes"].append(
            "EMPTY BACKBONE: no species recurs in >= %.2f of this group's teams, so there is NOTHING "
            "here to assemble on — this skeleton is a structural distribution plus open slots, and "
            "its confidence is low for exactly that reason (an empty core is never 'medium'). Do not "
            "read the open slots as freedom: ground every member you pick through repset/search and "
            "say in the answer that the frame supplied no backbone." % PARTNER_SUPPORT)
    if not structure_observed:
        skeleton["notes"].append(
            "STRUCTURE NOT OBSERVED: these library rows are species-only records (the source "
            "published the six species, not their moves/items/abilities), so NO structural signature "
            "could be read from them. They are pooled as one co-occurrence group, which is NOT an "
            "archetype — their co-occurrence is real evidence, their structure is unknown, and "
            "'unknown' is not the same fact as 'commits to nothing'.")
    # frame_id is a mechanical content hash (NOT a team-name template): structure + core species + fmt.
    skeleton["frame_id"] = "f" + _content_hash(
        {"sig": list(sig), "core": sorted(core_species), "fmt": fmt}, 10)
    # id first for readability in md/json without reordering the dict build above.
    return {"frame_id": skeleton.pop("frame_id"), **skeleton}


def _anchor_entry(member: dict[str, Any], fmt: str, all_teams: list[dict[str, Any]],
                  meta_fn: Callable[[str], dict | None] | None) -> dict[str, Any]:
    """A fallback skeleton's anchor slot. Nothing co-occurs with it (that is why we are here), so the
    share fields are empty facts rather than invented ones; its own exact-form repset is still tried
    against the wider evidence pool."""
    sp = member["species"]
    grounding = _grounding_for(sp, fmt, [], all_teams, member.get("item"))
    prim = (grounding or {}).get("primary") or {}
    return {
        "species": sp, "role": "anchor",
        "within_group_share": None, "within_group_count": 0, "pool_share": None,
        "grounding": grounding,
        "set_guidance": ({"moves": prim.get("moves"), "nature": prim.get("nature"),
                          "sps": prim.get("sps")} if grounding else None),
        "confidence": prim.get("confidence") if grounding else "low",
        "meta_auxiliary": (meta_fn(sp) if meta_fn else None),
    }


def _no_mega_reference() -> dict[str, Any]:
    """Fallback skeletons never gate the Mega registration count: an older rule or another species'
    registrations are not an observed norm for this anchor under this rule."""
    empty = mega_slot_distribution([])
    return {"basis": "none", "distribution": empty, "confidence": "low"}


def _lend(skeletons: list[dict[str, Any]], anchor_members: list[dict[str, Any]], *, fmt: str, source: str,
          all_teams: list[dict[str, Any]], meta_fn: Callable[[str], dict | None] | None,
          drop: set[str] = frozenset(),
          wider: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Turn skeletons computed over a fallback pool into the anchor's: the anchor takes the first core
    slots, `drop` (a stand-in) leaves, a pool-defining partner becomes backbone, and the rest is
    trimmed so a flex slot stays open. A member the (small) pool could not ground — species-only
    rows publish no set — is grounded from `wider`, the whole current library."""
    anchors = [_anchor_entry(m, fmt, all_teams, meta_fn) for m in anchor_members]
    names = {a["species"] for a in anchors}
    out = []
    for sk in skeletons:
        lent = []
        for cc in sk.get("core_candidates") or []:
            sp = cc.get("species")
            if not sp or sp in names or sp in drop:
                continue
            cc = dict(cc)
            if cc.get("role") == "anchor":            # a partner that defined the pool: its backbone
                cc["role"] = "core-partner"
            if not cc.get("grounding") and wider:
                grounding = _grounding_for(sp, fmt, [], wider, None)
                if grounding:
                    prim = grounding.get("primary") or {}
                    cc.update({"grounding": grounding,
                               "set_guidance": {"moves": prim.get("moves"), "nature": prim.get("nature"),
                                                "sps": prim.get("sps")},
                               "confidence": prim.get("confidence")})
            lent.append(cc)
        lent = lent[:max(0, MAX_CORE - len(anchors))]
        core_candidates = anchors + lent
        tiers = Counter(c.get("role") for c in lent)
        new = {k: v for k, v in sk.items() if k != "frame_id"}
        facts = dict(new.get("observed_facts") or {})
        facts["mega_registration_reference"] = _no_mega_reference()
        new.update({
            "fallback": source,
            "core_candidates": core_candidates,
            "core_tiers": {"anchor": len(anchors), "core": tiers.get("core-partner", 0),
                           "recurring_partner": tiers.get("recurring-partner", 0)},
            "flex_slots": {"open_count": max(0, 6 - len(core_candidates)),
                           "substitutable_count": len(lent)},
            "observed_facts": facts,
            "confidence": "low",
            "confidence_reason": f"{source.replace('_', '-')}-fallback",
        })
        frame_id = "f" + _content_hash(
            {"sig": (sk.get("group_signature") or {}).get("key"), "fallback": source,
             "core": sorted(c["species"] for c in core_candidates), "fmt": fmt}, 10)
        out.append({"frame_id": frame_id, **new})
    # Two pool frames can collapse onto the same lent roster once trimmed; keep the first of each.
    seen: set[str] = set()
    return [sk for sk in out if not (sk["frame_id"] in seen or seen.add(sk["frame_id"]))]


def _fallback_skeletons(resolved: dict[str, Any], anchor_members: list[dict[str, Any]],
                        filter_members: list[dict[str, Any]], *, fmt: str,
                        teams: list[dict[str, Any]], dex_fn: Callable, move_fn: Callable,
                        item_fn: Callable, order: str,
                        meta_fn: Callable[[str], dict | None] | None) -> list[dict[str, Any]]:
    """Materialize one resolved fallback recipe (`fallback_frames.resolve`) into skeletons."""
    source, pool = resolved.get("source"), resolved.get("pool") or []
    # A pool outside the current library brings its own prefetched fact tables (team.py).
    dex_fn, move_fn, item_fn = resolved.get("fact_fns") or (dex_fn, move_fn, item_fn)
    pool, _ = query_identity.battle_view(pool, filter_members, dex_fn=dex_fn, item_fn=item_fn)
    common = dict(fmt=fmt, dex_fn=dex_fn, move_fn=move_fn, item_fn=item_fn, order=order,
                  meta_fn=meta_fn)
    if source == "history":
        sub = frame_from_teams(pool, filter_members=filter_members, **common)
        return _lend(sub["skeletons"], anchor_members, fmt=fmt, source=source, all_teams=pool,
                     meta_fn=meta_fn, wider=teams)
    if source == "partners":
        sub = frame_from_teams(pool, filter_members=[{"species": p, "item": None}
                                                     for p in resolved.get("partners") or []],
                               **common)
        return _lend(sub["skeletons"], anchor_members, fmt=fmt, source=source, all_teams=teams,
                     meta_fn=meta_fn, wider=teams)
    if source == "stand_in":
        stand = resolved.get("stand_in")
        sub = frame_from_teams(pool, filter_members=[{"species": stand, "item": None}], **common)
        return _lend(sub["skeletons"], anchor_members, fmt=fmt, source=source, all_teams=teams,
                     meta_fn=meta_fn, drop={stand}, wider=teams)
    return []


def frame_from_teams(teams: list[dict[str, Any]], *, fmt: str,
                     dex_fn: Callable, move_fn: Callable, item_fn: Callable,
                     filter_members: list[dict[str, Any]] | None = None,
                     order: str = "common_first", max_frames: int = MAX_FRAMES,
                     meta_fn: Callable[[str], dict | None] | None = None,
                     fallback_fn: Callable[[list[dict[str, Any]]], dict | None] | None = None,
                     ) -> dict[str, Any]:
    """Build grounded skeletons over `teams` (one format's real library). Pure given the injected fact
    tables + optional `meta_fn` (auxiliary marginals only). `filter_members` ({species, item?} — item =
    optional held-item isolation) narrows to the exact actual-form anchor pool; empty = the generic
    (whole-library) build. `order` = the meta_conformance knob (common_first | rare_first).
    `fallback_fn(filter_members)` -> the resolved fallback-table recipe ({source, pool, ...}) or
    None; read only when the anchor pool is empty (the fallback ladder, module docstring)."""
    teams, identities = query_identity.battle_view(teams, filter_members, dex_fn=dex_fn, item_fn=item_fn)
    filter_members = [{**f, "species": identities(f) or "\0UNRESOLVED_FORM"}
                      for f in (filter_members or []) if f.get("species")]
    anchor_labels = [_filter_label(f) for f in filter_members]
    anchor_species = {f["species"] for f in filter_members}

    structural_teams = list(teams)
    pool = ([t for t in structural_teams if all(_matches(t, f) for f in filter_members)]
            if filter_members else structural_teams)
    pool_size = len(pool)
    profiles = [team_profile.profile(t, dex_fn=dex_fn, move_fn=move_fn, item_fn=item_fn) for t in pool]
    pool_mega_distribution = mega_slot_distribution(profiles)
    pool_cooccur = _cooccurrence(pool, anchor_species)

    # Partition by the format's Tier-1 structural signature; a group qualifies at MIN_SAMPLE. >=2
    # qualifying groups -> partition; else ONE unpartitioned frame over the whole pool (honest: a pool
    # that does not separate structurally is one frame, not fabricated sub-archetypes).
    groups: dict[tuple, list[tuple[dict, dict]]] = {}
    for t, p in zip(pool, profiles):
        groups.setdefault(_group_signature(t, p, fmt), []).append((t, p))
    qual = {sig: g for sig, g in groups.items() if len(g) >= MIN_SAMPLE}
    partitioned = len(qual) >= 2
    residual = sum(len(g) for sig, g in groups.items() if sig not in qual) if partitioned else 0

    if partitioned:
        frame_groups = list(qual.items())
    elif pool:
        frame_groups = [(("__all__",), list(zip(pool, profiles)))]
    else:
        frame_groups = []

    # Anti-mediocrity ordering (design §19.10): order ALL groups by prevalence under the knob; the
    # display cap only trims the generic popular/rare tail and NEVER a group carrying a constraint
    # member — in an anchor build the anchor is in every group, so nothing is trimmed (an off-meta
    # anchor's rare frame survives). frames_total vs frames_shown is disclosed.
    # The structure-not-observed pool sorts LAST whichever way the knob points. That is a CATEGORY
    # split, not a ranking: it is not a structural frame at all (see _structure_observed), so it does
    # not belong anywhere in an ordering of structural frames — and letting its raw prevalence put it
    # first would spend the display cap on the one group that describes no structure.
    frame_groups.sort(key=lambda kv: (kv[0] == _UNPROFILED,
                                      *((len(kv[1]), sorted(kv[0])) if order == "rare_first"
                                        else (-len(kv[1]), sorted(kv[0])))))
    frames_total = len(frame_groups)
    anchor_members = [{"species": f["species"], "item": f.get("item")} for f in filter_members]
    if filter_members:
        shown = frame_groups                                   # every anchor frame is constraint-relevant
    else:
        shown = frame_groups[:max_frames]

    skeletons = [_skeleton(sig, g, fmt=fmt, pool_size=pool_size, pool_cooccur=pool_cooccur,
                           pool_mega_distribution=pool_mega_distribution,
                           anchor_members=anchor_members, all_teams=teams, meta_fn=meta_fn)
                 for sig, g in shown]

    # Fallback ladder: an anchor no current-rule team carries still gets skeletons.
    fallback: dict[str, Any] | None = None
    if filter_members and not skeletons:
        resolved = fallback_fn(filter_members) if fallback_fn else None
        if resolved:
            skeletons = _fallback_skeletons(resolved, anchor_members, filter_members, fmt=fmt,
                                            teams=teams, dex_fn=dex_fn, move_fn=move_fn,
                                            item_fn=item_fn, order=order, meta_fn=meta_fn)
            if skeletons:
                fallback = {"tier": resolved["source"], "reason": "anchor_pool_empty"}
        if not skeletons:
            generic = frame_from_teams(teams, fmt=fmt, dex_fn=dex_fn, move_fn=move_fn, item_fn=item_fn,
                                       order=order, max_frames=GENERIC_FALLBACK_FRAMES,
                                       meta_fn=meta_fn)
            skeletons = _lend(generic["skeletons"], anchor_members, fmt=fmt,
                              source="generic_library", all_teams=teams, meta_fn=meta_fn)
            if skeletons:
                fallback = {"tier": "generic_library", "reason": "anchor_pool_empty"}

    thin = pool_size < THIN_BAR
    meta_fallback = pool_size == 0 or not any(
        cc.get("grounding") for s in skeletons for cc in s["core_candidates"])
    # Every shown skeleton empty of a derived backbone is a frame-level fact, not a per-group quirk:
    # the pool does not carry a recurring structure this partition can see.
    no_backbone = bool(skeletons) and not fallback and all(
        not (s["core_tiers"]["core"] or s["core_tiers"]["recurring_partner"]) for s in skeletons)
    notes = [
        "frame HANDS the AI a data-grounded starting skeleton so `[assemble]` is not built from the "
        "training prior; it is a scaffold, NOT a retriever — build YOUR team on it, deviate with a "
        "declared basis. The slate gate binds each candidate's core-bearer sets against this evidence.",
        "structural skeletons are ordered by PREVALENCE (a view under the meta_conformance knob: common_first / "
        "rare_first) — never a strength ranking; frames_total is every group above support, frames_shown "
        "the displayed subset (an anchor build shows them ALL so an off-meta frame is never dropped).",
        "grounded sets come ONLY from repset (real joint teams); a core candidate with grounding=null "
        "has no real joint set (thin/off-meta) — build it yourself and disclose, NEVER stitch one from "
        "meta marginals. meta_auxiliary (when present) is commonness context only.",
    ]
    if thin:
        notes.append(f"THIN anchor pool ({pool_size} < {THIN_BAR} teams): low CONFIDENCE = thin "
                     "EVIDENCE, not a weak team — read the skeleton as anecdotal, ground per-member "
                     "via repset/search before relying on it.")
    if meta_fallback:
        notes.append("META_FALLBACK: no real joint grammar for this anchor (empty pool or no core "
                     "candidate cleared repset) — this is the data-gated boundary (design §19.8), "
                     "stated honestly; assemble from meta + dex facts and disclose the low confidence.")
    if fallback:
        notes.append(
            f"FALLBACK LADDER ({fallback['tier']}): no current-rule team carries the anchor, so these "
            "skeletons come from the fallback table — history = older-rule teams built around the "
            "anchor, partners = current teams carrying its specific usage partners, stand_in = the "
            "frames of a species doing the same job, generic_library = whole-library frames. None "
            "was observed with the anchor under this rule: they bind softly (reported, never "
            "eliminating).")
    if no_backbone:
        notes.append(f"NO BACKBONE in any shown frame: no species recurs in >= {PARTNER_SUPPORT:.2f} "
                     "of any structural group, so this run hands you structural facts and open slots "
                     "but no grounded starting roster. Frame confidence is low for that reason; "
                     "ground each member through repset/search and disclose that the frame was empty "
                     "— do NOT fill the slots from the training prior, which is the exact failure "
                     "this operator exists to block.")
    transitioned = sum(1 for t in pool if t.get("target_rule"))
    if transitioned:
        notes.append(
            f"HANDOVER: {transitioned}/{pool_size} structural rows retain their evidence_rule and "
            "were revalidated for the target rule; both evidence pools contribute until the "
            "receipt expires, regardless of native sample count.")
    if residual:
        notes.append(f"{residual} pool team(s) fell in structural groups below MIN_SAMPLE (no frame "
                     "emitted for them) — not silently merged into a shown frame.")
    return {
        "kind": "frame",
        "identity_basis": "dex_battle_form", "identity_gaps": identities.gaps,
        "format": fmt,
        "anchor": anchor_labels or None,
        "order": order,
        "pool_size": pool_size,
        "partitioned": partitioned,
        "residual_teams": residual,
        "frames_total": frames_total,
        "frames_shown": len(skeletons),
        "skeletons": skeletons,
        "fallback": fallback,
        "thin": thin,
        "meta_fallback": meta_fallback,
        "no_backbone": no_backbone,
        "handover": ({"rows": transitioned,
                       "evidence_rules": sorted({t.get("evidence_rule") for t in pool
                                                 if t.get("evidence_rule")}),
                       "target_rules": sorted({t.get("target_rule") for t in pool
                                               if t.get("target_rule")}),
                       "expires_at": sorted({t.get("expires_at") for t in pool
                                             if t.get("expires_at")})}
                      if transitioned else None),
        "confidence": "low" if (thin or no_backbone) else "medium",
        "confidence_reason": ((fallback["tier"].replace("_", "-") + "-fallback") if fallback
                              else "thin-observed-sample" if thin
                              else "no-recurring-backbone" if no_backbone else "observed-sample"),
        "notes": notes,
    }


def format_frame_md(d: dict[str, Any]) -> str:
    """Human-readable skeleton report. FACTS only — prevalence/share/counts are provenance, never a
    strength score; core candidates are listed by real-team prevalence, not 'best'."""
    anchor = ", ".join(d.get("anchor") or []) or i18n.t('frame_no_anchor')
    lines = [i18n.t('frame_header', anchor=anchor, fmt=d["format"], n=d["pool_size"],
                    conf=d["confidence"]),
             "_" + i18n.t('frame_subtitle', total=d["frames_total"], shown=d["frames_shown"],
                          order=d["order"]) + "_"]
    if d.get("thin"):
        lines.append("> ⚠️ " + i18n.t('frame_thin', n=d["pool_size"], bar=THIN_BAR))
    if d.get("meta_fallback"):
        lines.append("> ⚠️ " + i18n.t('frame_meta_fallback'))
    if d.get("fallback"):
        lines.append("> ⚠️ " + i18n.t('frame_fallback_' + d["fallback"]["tier"]))
    if d.get("no_backbone"):
        lines.append("> ⚠️ " + i18n.t('frame_no_backbone', bar=f"{PARTNER_SUPPORT:.2f}"))
    for i, s in enumerate(d.get("skeletons") or [], 1):
        # The GROUP KEY, not the group's mode inventory: the key is format-specific (speed control in
        # doubles, role commitments in singles), so printing the speed-control modes for a singles
        # frame would name an axis the partition did not use.
        gsig = s.get("group_signature") or {}
        key = (i18n.t('frame_unpartitioned') if gsig.get("axis") == "unpartitioned_pool"
               else ", ".join(gsig.get("key") or []) or ", ".join(
                   s["structural_profile"]["speed_control_modes"].keys()) or "—")
        prev = s["prevalence"]
        lines.append(f"\n## #{i} `{s['frame_id']}` — {i18n.t('frame_structure')}: {key} "
                     f"({gsig.get('axis') or 'speed_control'}) "
                     f"· {i18n.t('frame_prevalence')} {prev['count']} ({prev['share']}) "
                     f"· {i18n.t('confidence')} **{s['confidence']}**")
        if gsig.get("axis") == "structure_not_observed":
            lines.append("> ⚠️ " + i18n.t('frame_structure_unobserved'))
        tiers = s.get("core_tiers") or {}
        lines.append(f"**{i18n.t('frame_core')}** ({i18n.t('frame_open_slots')}: "
                     f"{s['flex_slots']['open_count']}"
                     + (f"; {i18n.t('frame_substitutable')}: {tiers.get('recurring_partner')}"
                        if tiers.get("recurring_partner") else "") + ")")
        if s.get("fallback"):
            lines.append("> ⚠️ " + i18n.t('frame_fallback_' + s["fallback"]))
        elif not (tiers.get("core") or tiers.get("recurring_partner")):
            lines.append("> ⚠️ " + i18n.t('frame_empty_backbone', bar=f"{PARTNER_SUPPORT:.2f}"))
        for cc in s["core_candidates"]:
            g = cc.get("grounding")
            if g:
                prim = g["primary"]
                gset = f"@ `{prim.get('item')}` · {prim.get('ability')} · {prim.get('nature')}"
                src = "" if g.get("set_source") == "group" else f" _[{g.get('set_source')}]_"
                moves = ", ".join(prim.get("moves") or []) or "—"
                sps = prim.get("sps")
                sp_s = f" · SP {sps}" if sps else ""
                lines.append(f"- **{cc['species']}** ({cc['role']}, "
                             f"{i18n.t('frame_within_group')} {cc['within_group_share']}) {gset}{src}")
                lines.append(f"    - {i18n.t('frame_set_guidance')}: {moves}{sp_s} · "
                             f"{i18n.t('confidence')} {cc['confidence']}")
                if note := query_identity.registration_note(g):
                    lines.append(f"    - {note}")
            else:
                lines.append(f"- **{cc['species']}** ({cc['role']}, "
                             f"{i18n.t('frame_within_group')} {cc['within_group_share']}) — "
                             f"⚠️ {i18n.t('frame_ungrounded')}")
        of = s["observed_facts"]
        mega = of.get("observed_mega_slots") or {}
        lines.append(f"_{i18n.t('frame_observed_mega')}: avg "
                     f"{mega.get('average_registered_mega_slots')}_ "
                     f"({i18n.t('frame_mega_is_fact')})")
        fillers = of.get("observed_fillers") or []
        if fillers:
            fs = ", ".join(f"{f['species']} ({f['within_group_share']})" for f in fillers[:8])
            lines.append(f"_{i18n.t('frame_observed_fillers')}: {fs}_")
    if d.get("notes"):
        lines.append(f"\n## {i18n.t('notes')}")
        lines += [f"- {n}" for n in d["notes"]]
    return "\n".join(lines)
