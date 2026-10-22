# Pokémon Champions Team Public Data Contracts

This reference documents durable domain objects and the bundled facts-only caches needed by an
installed skill. It intentionally excludes source adapters, refresh procedures, tests, and private
development history. Run `python scripts/team.py schema` for the authoritative live command and
payload contract; run `python scripts/team.py vocab` for enumerated intent fields and their consumers.

## Contents

- [`team-json`](#1-team-json-the-canonical-team-structure)
- [`provenance`](#2-provenance-real-team-fact-tags--never-a-strength-score)
- [Shipped library layout](#3-shipped-library-layout)
- [Representative sets](#4-representative-sets)
- [Opponent matchup cache](#5-opponent-observed-build-matchup-cache)
- [`build-context`](#6-build-context-intent-layer)
- [Evidence](#7-evidence-output-side-lightweight)
- [Frame](#8-frame-assembly-front-door)
- [Top Meta](#9-top-meta-the-library-on-the-metagames-fields)
- [Public team codes](team-codes.md): canonical six-member lookup and bounded local batch interface.

## 1. `team-json` (the canonical team structure)

One team. Used for both validation input and as the unit stored in the sample library.

```json
{
  "schema_version": 1,
  "format": "single",                 // "single" | "double"
  "season": "M-4",                    // nullable for a hand-built team
  "rule": "M-B",                      // nullable for a hand-built team
  "pokemon": [
    {
      "species": "Staraptor",         // dex canonical English name (Mega forms: "Mega Staraptor")
      "item": "Staraptite",           // null if none
      "ability": "Intimidate",
      "moves": ["Close Combat", "Dual Wingbeat", "Protect", "Roost"],
      "nature": "Jolly",              // null if unknown
      "spread": {"hp":0,"atk":32,"def":0,"spa":0,"spd":0,"spe":32},  // SP (Champions), null if unknown
      "tera": null,                   // Champions has no Terastal; kept null for forward-compat
      "completeness": "observed_full_set"  // observed_full_set | observed_species_only | extracted_set | inferred_set
    }
  ],
  "provenance": null                  // null for hand-built; object below for collected real teams
}
```

Field notes:
- Collected ladder rows carry `season`. A competition not owned by a ladder season ships with
  `season: null` and an explicit `rule`; its physical `<rule>-events_<format>.jsonl` partition records
  the event provenance axis without exposing event identity. Full event metadata exists only in the
  private updater build-state because event identity combined with placing can identify a source row.
- `species` / `item` / `ability` / `moves` use **dex canonical English** so validation and cross-checks
  against `$pokemon-champions-dex` are exact. Raw importer identifiers are normalized here before
  anything ships.
- `spread` is **SP** (Stat Points), not EVs: each stat 0–32, total ≤66. `nature` applies the ±10%.
- `tera` exists only so importers can round-trip foreign formats; Champions never uses it.
- `completeness` separates the evidence lanes. `observed_species_only` means the slot is only a
  presence/co-occurrence fact (useful for core/sample views); it is **not** a set template. A member is
  eligible for `repset` / opponent resolver / opponent cache when it is tagged `observed_full_set`
  (owner-observed — confidence floor high) **or** `extracted_set` (a validated community
  reconstruction — usable as a template/tune target but confidence-capped **medium**, never presented
  as an owner's own export) **and** actually carries `species + ability + nature + moves`.
  `inferred_set` (guessed) never qualifies.
  `spread` is optional for template eligibility because some real sources expose a joint set but no SP
  spread; those consumers fall back to meta spread instead of inventing one — and a tagged member
  with NO spread has an UNKNOWN spread (tune reads it as assumed-0, low confidence, disclosed),
  while spread-carrying members feed the shape-aggregated spread stats
  (`spread_shape*`/`spread_count`/`spread_sample`/`spread_confidence`).
  `item` is held to a **team-level** rule: a member with no item is still
  template-eligible when its team carries items elsewhere (a deliberate no-item build — Acrobatics /
  Unburden — must not be silently dropped), but a team where **every** member is itemless is treated as
  an items-not-captured extraction, so none of its members are template-eligible.

## 2. `provenance` (real-team fact tags — never a strength score)

Attached when a team is collected from a real source. Hand-built teams have `provenance: null`.
The shipped skill keeps only facts that are safe to show and query.

```json
{
  "fetched_at": "2026-06-19T00:00:00Z",
  "performance": {
    "kind": "tournament",           // "tournament" | "community" | "ladder"; consumed by
                                    // the ordered evidence-tier view
    "record": {"wins": 6, "losses": 1, "ties": 0},
    "placing": 1,
    "field_size": 37,               // entrants (evidence weight), else null
    "rating": null                  // ladder rating e.g. 2422.626 (singles), else null
  }
}
```

The shipped `*.jsonl` is a facts-only projection. It contains no source identifier, URL, person,
event title, or other source-locating field. It keeps `fetched_at` plus the whitelisted
`provenance.performance` facts (`kind`, `record`, `placing`, `field_size`, `rating`, and `votes`).
`index.json` carries aggregate counts and environment labels only.

**Rules for `performance`:**
- It is **fact**, surfaced as context ("6-1 in a 37-player M-B event", "ladder rating 2422"),
  **not** normalized into a single number.
- **Never merge across formats**: singles ladder `rating` and doubles tournament `record` are
  different metagames and different confounders. Keep them separate; if ranking, use an *ordered
  evidence tier*, not a blended score.

## 3. Shipped Library Layout

```text
data/teams/
  index.json
  <season>_<format>.jsonl
  <rule>-events_<format>.jsonl
  handover.json                         # optional, temporary cross-rule validation receipt
```

- A season JSONL represents one `(season, format)` partition. A non-empty event JSONL represents the
  rule-scoped event pool for `(rule, format)`; its rows have `season:null` plus distinct event ids.
- Filter and label evidence by `season`, `rule`, `format`, and `performance`.
- Runtime consumers may merge partitions sharing the same rule; old regulations remain explicitly
  labeled and are not treated as current.
- Exact-season reads use only `<season>_<format>.jsonl`; rule-pool reads also include the matching
  `<rule>-events_<format>.jsonl`. Empty event scopes do not create files or index entries.
- `handover.json` never changes those rows or their labels. While its target is current and its
  maximum seven-day window is open, the runtime revalidates its target, ruleset, source partitions,
  target dex bytes, validator code, and immutable local activation timestamp. It deep-copies only
  receipt-approved rows with in-memory `evidence_rule`, `target_rule`, `handover_reason`, and
  `expires_at` annotations. Any mismatch fails closed.

## 4. Representative Sets

`team.py repset <species> --game-format single|double` returns up to three real-team
`(item, ability)` archetypes. Every emitted set carries `cluster`, `coverage`, `share`,
`species_sample`, `species_sample_total`, `item_filter`, `spread_origin`, and `confidence`.

The **live `repset` shape** (one archetype object, ncp-key spread): `species`, `format`, `source:
"real-team"`, `ability`/`item`/`nature`/`moves` (the co-occurring joint set), `cluster:
{item,ability}` (the cluster key; `null` + `fragmented:true` on the no-dominant-archetype fallback),
`sps` (real co-occurring SP spread in ncp keys, or `null` when the source has none → use meta spread),
`spread_origin: "real-team" | null`, `count`/`sample` (modal-set count within the cluster / cluster
size), `share` (count/sample within cluster), `coverage` (cluster size / **the queried pool** —
`species_sample`; **`null` on the fragmented fallback** — it is a whole-pool entry, not a cluster
covering 100%, so the honest figure is `share`), `species_sample` (the queried pool size: the
**item-filtered subpool** when `item_filter` is set, e.g. a doubles Mega isolated by its stone — NOT the
whole base species), `species_sample_total` (the whole-species count, == `species_sample` unless an
item filter narrows the pool), `item_filter` (the item the pool was filtered to, or `null`),
`confidence` (sample-size ∧ modal-share folded). When `item_filter` is set, read `coverage`/
`species_sample` against the filtered pool, never as whole-species coverage.

Throughout an active team handover, validated prior-rule and native members contribute together.
Such a set carries `evidence_rules`, `target_rule`, and `handover_expires_at`. Native sample counts
do not shorten the 168-hour window; at expiry only target-rule team evidence remains.
Dex, meta and calculator data always belong to the current rule.

> **CLI canonicalization:** `team.py repset <species>` resolves the raw species via
> dex first (Chinese/Japanese aliases → canonical English) so the library's English keys match. Mega
> resolution is **format-aware**: the doubles source stores a Mega as base + stone (CLI queries the base
> with an `item_filter = required_item`), the singles source stores the `Mega X` species directly (used
> as-is). The output `query` block echoes `{species, resolved, item_filter}` for transparency.
>
> **Real-team provenance**: `repset --season <old>` reads that exact season partition and records
> `data_season`/`data_rule`; omitting `--season` reads the current-rule pool and records
> `data_rule` + `data_seasons`. A historical partition does NOT emit the "computed against the current
> base" warning (that warning is for the current-only dex/meta bases). `data_rule` is sourced from
> `index.json` / the pinned season-rule map and is `null` only for genuinely unmapped future labels.

Per meta member, one to three representative sets are derived from real teams (joint sets), with meta
usage used only where the output explicitly identifies a fallback such as SP fill.
Only template-eligible members enter these counts: `observed_full_set` OR `extracted_set` (validated
community reconstruction — participates in `sample`, but an all-extracted archetype is
confidence-capped **medium** via the completeness floor) plus real `species/ability/item/nature/moves`
fields. Presence-only rows, `inferred_set` rows, and mislabeled rows with missing set fields remain
available to presence/co-occurrence consumers but do not inflate `sample`, do not trigger singles Mega
run-form remapping, and do not create opponent-cache attacker rows.

## 5. Opponent Observed-Build Matchup Cache

`team.py oppmatrix [species] --game-format single|double [--vs defender] [--as-checks]` reads a
precomputed observed-build reference grid over the meta top-K from
`data/opponent_cache/<rule>_<format>.json` (keyed by rule: the universe is the current usage
ranking and the sets span the whole rule pool, so seasons sharing a rule share one matrix).

The grid is **variant-expanded**: one row/column per retained real (item, ability) cluster, keyed by
the lossless canonical form `variant:<pct-species>|item=<pct-item>|ability=<pct-ability>`. A species
that runs both a Mega stone and a Choice item
is two different opponents (different typing, stats, ability, speed tier), so each gets its own row.
Every ordered pair is computed, including two builds of one species AND the mirror (a build against
itself). Each set carries `is_modal` (the highest-coverage default), `coverage` (sample share), a
`speed_profile`, and represented/unrepresented sample coverage. Cluster admission requires both
absolute sample support and relative coverage; a global per-species ceiling bounds calculation and
presentation cost.

`--as-checks` preserves these build axes. Every cell has one atomic `grade` for exactly the two builds
it names; there is no hidden modal/headline/strict or synthetic fast lane. A plain species selector
expands to all retained variants, while an exact `variant_id` selects one. The species portfolio first
keeps the strongest team/member answer for each variant, then exposes `observed_floor` as the weakest
of those answers together with `witness_variant_ids`. `coverage.represented` and
`calculation_complete` are separate facts: an observed floor never claims to cover omitted sample mass.

The **live cache shape** — the matrix is keyed by `variant_id`:

```json
{
  "kind": "opponent-cache",
  "built_for": {"season": "M-4", "rule": "M-B", "format": "single",
                "data_rule": "M-B", "data_seasons": ["M-3", "M-4"],
                "data_partitions": ["M-3", "M-4", "M-B-events"],
                "built_at": "...", "top_k": 60, "variant_count": 201,
                "cache_schema_version": 2,
                "handover_receipt": false,
                "source_fingerprints": {"meta": "sha256...", "team_library": "sha256..."}},
  "species": [{"rank": 7, "species": "Staraptor",
               "variant_id": "variant:Staraptor|item=Staraptite|ability=Intimidate",
               "real_team_backed": true,
               "set_source": "real-team", "set_confidence": "low",
               "run_form": "Mega Staraptor"}],     // run_form: present when the form actually run differs
                                                    // from the meta key (singles Mega ranked under base)
  "sets": {"variant:Staraptor|item=Staraptite|ability=Intimidate": {
                        "species": "Staraptor", "run_form": "Mega Staraptor",
                        "ability": "...", "item": "...", "nature": "...",
                        "moves": ["..."],          // the real joint set — null for defender-only species
                        "sps": {"hp": 32, "df": 25}, "source": "real-team",
                        "coverage": 0.72, "represented_coverage": 0.91,
                        "unrepresented_coverage": 0.09,
                        "speed_profile": {"sample": 20, "min_spe_sp": 0,
                                          "max_spe_sp": 32, "natures": ["Jolly"],
                                          "heterogeneous": true},
                        "confidence": "low", "real_team_backed": true, "note": "..."}},
  "matrix": {                                       // matrix[attacker][defender] = ordered-pair cell
    "Garchomp": {
      "Mimikyu": {
        "offense": {"move": "Earthquake", "min_percent": 64.4, "max_percent": 77.3,
                    "ko_possible": 2, "ko_guaranteed": 2, "ko": "2HKO (static approx)",
                    "ko_exact": false, "ko_chance": {...}, "ko_caveats": [...],
                    "disguise_adjusted": {           // ONLY when the defender's ability is Disguise:
                      "effective_ko_possible": 3,    // 1 blocked turn + ceil(remaining 87.5% / per-turn
                      "effective_ko_guaranteed": 3,  // band) — NOT nominal+1 (the 1/8 break-chip can KO
                      "note": "..."}},               // sooner). Labelling exception, not an engine
                                                     // recompute; read this vs Mimikyu.
        "speed": {"attacker": 122, "defender": 148, "faster": "defender"}
      }
    }
  },
  "check_matrix": {                              // precomputed offline; no first-request derivation
    "variant:...": {"variant:...": {"grade": "C1", "c0_kind": null,
                                      "contested": false}}
  },
  "confidence": "low", "confidence_reason": "vs-observed-build", // EVERY cell, stated once at top
  "notes": ["..."]
}
```

- `built_for.top_k` is the ranked-species scope; `built_for.variant_count` is the number of expanded
  build rows/columns. Multiple variants of one species never inflate `top_k`.
- One file per `(rule, format)` cache target; `built_for.season` is provenance for the current ranking
  snapshot and does not select the file. The two metagames are never mixed. Built from the meta
  usage ranking (the opponent universe), the same-rule real-team library pool, dex facts, and one batched
  ncp call. The shipped single and double reference grids both cover the Top 60; per-row confidence
  still reflects the formats' different real-team density.
- **Singles Mega names are resolved to the form actually run** by the shared form resolver: meta ranks
  a singles Mega under the BASE name but the
  library stores `Mega X`, so the resolved set keeps `species` = the meta label and adds `run_form` =
  the real Mega; the calc/speed/types use `run_form`. Fixed in the resolver, so **`matchup` gets the
  same correction** (its cells carry `opponent_run_form`).
- **Attacker rows exist ONLY for real-team-backed species** with a real co-occurring four-move set
  meeting the configured minimum sample size. A meta-only species has no real joint move set — meta marginals can't be stitched into
  one — so it appears as a **defender only** (`real_team_backed:false`, `moves:null`).
  It therefore appears only as a defender.
- Source fingerprints cover the season-scoped meta ranking/details and every same-rule team-library
  partition, including the rule event partition. An active handover adds its approved source
  partitions and receipt to that fingerprint and sets `built_for.handover_receipt:true` and
  `built_for.team_evidence_expires_at`. At expiry readers select the companion
  `<rule>_<format>.native.json`; `unavailable:true` means native samples cannot supply a matrix.
  `built_for.calculation_authority` binds current dex bytes, all installed calculator JS files,
  and the hash of the module this skill asks the calculator through: the same engine answers a
  different question once that request convention changes.
  A source or calculation mismatch refuses the cache and requires rebuild;
  a refreshed local data snapshot is
  never silently paired with older calculations.
- `offense` reuses the live `matchup` damage fact (full roll band + possible/guaranteed KO buckets;
  only a damage-based one-turn KO is exact, 2+ turn KO is a static approximation flagged
  `ko_caveat`). `speed` is the modal line.
- Accuracy-based one-hit-KO moves are excluded from the static KO baseline and never upgrade a derived
  check's C2/C1/C0. An applicable route only marks the read `contested`, like setup/recovery; the static
  operator does not simulate its probability. Detection uses the dex `is_ohko` mechanic flag rather
  than a power/accuracy heuristic.
- **Cells are ALWAYS `low` confidence** (`vs-observed-build`): a reference grid of retained observed builds, NOT the
  user's team. The user's own matchup is always computed LIVE via `team.py matchup` on actual sets.
- `--as-checks` reads the offline-precomputed attacker-build×attacker-build C2/C1/C0 reference grid:
  the forward cell is our offense and the reverse cell is incoming. Meta-only defenders have no
  reverse attacker row and are excluded. A species query expands; an exact build key narrows.

The live actual-set battery is a separate L2 contract: `team.py matchup` accepts any non-empty
collection of 1–N member objects using the same member shape as `team-json`, and evaluates each actual
configuration against a caller-selected meta Top-K in the shared range `1..60`. It does not certify
that the collection is a legal registered team; call `validate` for that higher-level question.
Every source row and target cell carries request-stable `source_id` / `target_id` / `cell_id`, so two
configurations of the same species remain distinct. JSON callers may request `full` (complete evidence)
or `summary` (KO/incoming/speed/atomic CHECK facts); both are projections of the same calculation.

## 6. build-context (Intent Layer)

The AI translates the conversation's intent into this structured object and passes it with `team-json`.
It is **structured constraints, not natural language** — the skill never parses free text.

```json
{
  "season": "M-4", "rule": "M-B", "format": "double",
  "locked": ["Garchomp"],                 // members the user will not change
  "owned_only": true,                     // restrict to the owned roster
  "owned": ["Garchomp", "Whimsicott"],    // owned species (canonical or dex-resolvable); the AI gathers
                                          // these from the user's input in ANY format and resolves them
                                          // via the dex — there is no required input file
  "wants": ["tailwind"],                  // desired tactics: weather/trickroom/tailwind/...
  "keep_mega": "Garchomp",                // a Mega to preserve, if any
  "mega_posture": "environment",           // environment | none | single | multi. Default environment:
                                          //   use a reliable frame-observed registration distribution;
                                          //   the other values are hard registered-Mega-count constraints
  "avoid": [],                            // species/items the user wants excluded
  "prefer": ["Mimikyu"],                  // SOFT includes — "想尽量带". The AI tries to honor these but
                                          //   may drop one (with a stated reason) when it does not fit;
                                          //   contrast `locked` (HARD, never changed). dex-resolvable names
  "avoid_soft": ["Gengar"],               // SOFT excludes — "尽量别用", prefer's mirror: honored when
                                          //   composing (slate echoes hits as a fact, never eliminates);
                                          //   contrast `avoid` (HARD: observed excludes / slate eliminates)
  "exclude_tactics": ["stall",            // tactic tokens the user does NOT want (the assisted flow's "不想用受队/
                      "trickroom"],       //   空间/天气…"). Enumerated vocab: stall | trickroom | weather |
                                          //   tailwind | screens | pivot | setup | hazards. AI-side intent
                                          //   (honored in composition + which fill views to request); no
                                          //   operator mechanically filters on it yet
  "meta_conformance": "proven",           // posture knob proven | off_meta: flips the landscape
                                          //   observed_cores VIEW to rare-first (an ordering, never a
                                          //   score) and disables slate's observed-registration
                                          //   deviation gate for deliberate off-meta builds
  "style_lean": "defense",                // posture knob offense | balance | defense (主动进攻/平衡轮换/
                                          //   稳健防守) — the USER's stated structural posture, a lens the
                                          //   AI reads profile/landscape FACTS through. AI-side intent: no
                                          //   operator filters or orders by it; the skill never labels a team
  "need": {                               // L3 `fill` gap spec; AI translates a
    "resist": "Water",                    //   diagnosed gap. Keys (AND-combined): resist (type|[types]),
    "offense_type": "Fire",               //   offense_type (type|[types], STAB/typing proxy = own type),
    "coverage_move_type": "Ice",          //   coverage_move_type (type|[types]; REAL learnset damaging
                                          //     move of that type via dex move->type bridge; each match
                                          //     flags stab — non-STAB coverage is worth far less than STAB),
    "role": "speed_control",              //   role (pivot/hazard_set/hazard_control/speed_control/...),
    "min_speed": 120                      //   min_speed (int, max-Spe +nature line must reach it)
  },
  "replace": {                            // L3 `replace`-impact: objective before/
    "member": "Rotom-Wash",               //   after diff of swapping a member for a CONCRETE candidate
    "with": {"species": "Incineroar", "ability": "Intimidate", "item": "Sitrus Berry",
             "nature": "Careful", "spread": {"hp": 32, "spd": 16},
             "moves": ["Flare Blitz", "Parting Shot", "Fake Out", "Knock Off"]}
  },
  "benchmarks": [                         // SP fine-tuning targets; AI translates intent
    {"member": "Incineroar", "kind": "survive",  "vs": "Garchomp", "move": "Earthquake",
     "conditions": {"stealth_rock": false, "spikes": 0}, "probability": "guaranteed",
     "opponent_set": {"ability": "Rough Skin", "item": "Life Orb", "nature": "Jolly",
                      "spread": {"atk": 32, "spe": 32}},
     "member_state": {"status": "Burned", "boosts": {"def": 1}}},
    {"member": "Garchomp",   "kind": "outspeed", "vs": "Dragapult", "conditions": {"tailwind": false},
     "opponent_set": {"nature": "Timid", "spread": {"spe": 20}}}
    // kind: survive | outspeed | ohko | 2hko ; vs = canonical species (or a raw Speed number for outspeed);
    // conditions are applied when set explicitly here; survive may set hits: 1 | 2 and member_state;
    // probability: guaranteed | near_guaranteed | likely | three_quarters | half | any
  ],
  "frame_required": true,                 // build-flow backstop: slate refuses without --frame-output,
                                          //   answer-audit requires a frame fingerprint in the receipt chain
  "direct_final": false,                  // checkpoint: user explicitly delegated final convergence
  "skip_checkpoint": false                // checkpoint: user explicitly requested no mid-build pause
}
```

`benchmarks` is the entry point for the `tune` operator. Each is a declarative
**cliff target**; `tune` reports the minimum SP to cross it (or the slack if already past), never a
single "optimal spread". A full 66-SP spread does not make an otherwise reachable cliff infeasible:
the card keeps the target-stat `delta_sp` and adds a `reallocation` envelope with the amount that
must move, available capacity, and invested-stat donor candidates. Donors are opportunity-cost facts,
not an automatic instruction; floors proven by other already-met benchmarks are annotated as protected
capacity. Cards preserve benchmark input order and have no cross-benchmark score.

`kind`, `probability`, `conditions`, and `opponent_set` define the primary request. `opponent_set`
accepts the opponent's ability/item/nature plus `spread` or NCP-keyed `sps`; it is used consistently
for damage and speed. When absent, Tune resolves a real joint set first, then a disclosed meta/synthetic
fallback. Survival cards compare HP, relevant Def/SpD, and bounded mixed allocations for the same
objective; `probability_lanes` are explicitly scoped to their single stat axis. Speed cards use the
resolved set as the main target and keep the max-SP positive-nature target in `ceiling_lane`. Adjacent
survival/KO tiers and probability lanes are context, not silent replacements for the requested target.
For `survive`, `hits: 2` enumerates the independent 16×16 damage-roll pairs and applies entry-hazard
chip once. Between-hit recovery, ability/stat changes, and field changes remain explicit assumptions
rather than being silently approximated.

`member_state` (survive only) states OUR member's battle state while it takes the hit: `status` in the
calc's vocabulary (`Healthy | Burned | Paralyzed | Poisoned | Badly Poisoned | Asleep | Frozen`) and
`boosts` as canonical stat keys (`atk/def/spa/spd/spe`, integers -6..6). It is a condition of that
benchmark, not part of the build: every lane on the card (defense axis, HP, mixed, probability, nature,
Intimidate) is solved under it, the card echoes the non-neutral part as `member_state`, and an
assumption line says the required SP holds only while that state does. It is what status-sensitive
defence needs — Marvel Scale, Hex / Venoshock / Wake-Up Slap into a statused target, Foul Play off our
Attack stage, Gyro Ball off our Speed. Outspeed and kill benchmarks refuse it rather than drop it.

`conditions` keys
(stealth_rock / spikes / tailwind / opponent_tailwind /
trickroom / weather / terrain / screens) are **applied only when explicitly set here** — the user's
request always wins over any default gate. Two field effects also have TEAM-CARRIES defaults when the condition is absent
(explicit values still override them): my Tailwind (doubles + a team Tailwind setter -> a raw/x2 dual
lane on outspeed cards) and my Stealth Rock on kill cards (singles + a team SR setter -> the chip is
applied, with a no-SR dual lane). Notes:
- `weather` takes a named string (e.g. `"rain"`); `screens` takes `true` (the calc picks Reflect vs
  a physical hit, Light Screen vs a special one) or an explicit `"reflect" | "light_screen" | "aurora_veil"`.
- `stealth_rock` is a SINGLES model on both sides (doubles entry hazards are marginal — a capability
  boundary, so an explicit `true` in doubles is dropped, not honoured). Survive side: opponent rocks
  chip US (opt-in via this key). Kill side: OUR rocks chip the target — explicit value wins, else the
  team-carries default; when the chip is on, a `stealth_rock_lane` carries the no-chip line.
- `spikes` takes `true` (one layer) or an explicit layer count `0 | 1 | 2 | 3`; it means Spikes only,
  not Toxic Spikes or Sticky Web.
- `terrain` takes a named string (e.g. `"electric"`); it triggers a terrain-keyed Speed ability in an
  `outspeed` cliff (Surge Surfer x2 under Electric Terrain), the terrain analogue of `weather`.
- `tailwind: true` declares MY Tailwind is up: the outspeed MAIN line is solved at x2 Speed (any
  format — the user's assertion wins). When absent, doubles + a team Tailwind setter yields the
  raw/x2 dual (`tailwind_lane`) instead. `opponent_tailwind: true` doubles the TARGET's Speed
  (doubles only; default off).
- `trickroom: true` inverts the speed objective: the cliff reports whether the member already
  UNDER-speeds the target's floor line (0 SP, -Speed nature, slowest form — the conservative bound
  for moving first under Trick Room); Speed SP becomes a lever to pull, not add.

> **Executable contract:** this schema is enforced in code by `scripts/contracts.py`
> (stdlib-only, no pydantic), not just documented here. It validates team-json, build-context, and
> benchmarks — enumerated `format`/`kind`/`probability`/`completeness`/`conditions`/`screens`/`exclude_tactics`, the
> `vs` raw-Speed-only-for-outspeed rule, and `schema_version` (supported: 1) — returning coded
> `ContractError`s (`E_TYPE`/`E_ENUM`/`E_MISSING`/`E_RANGE`/`E_SCHEMA_VERSION`/`W_*`). `team.py`
> runs it at the CLI boundary: `validate` reports contract errors alongside legality, and `tune`
> refuses a malformed build-context.

## 7. Evidence (Output-Side, Lightweight)

Every diagnose / candidate / key-calc result carries an `evidence` block so the AI's claims
("why this", "handles whom", "outspeeds whom") are traceable. This is NOT champions-data's claims-json
protocol (no forced AI output format, no retry loop) — it is the skill attaching its sources to its own output.

```json
{
  "result": "...",                        // the fact/verdict
  "confidence": "low",                    // high | medium | low
  "confidence_reason": "vs-observed-build", // small-sample | sp-inferred | cache | heuristic-role | vs-observed-build | null
  "evidence": {
    "facts": [{"source": "dex", "ref": "Garchomp.types", "value": ["Dragon", "Ground"]}],
    "calc": {"source": "ncp", "inputs": {"attacker": "...", "defender": "...", "move": "..."}, "result": "..."}
  }
}
```

## 8. Frame (Assembly Front Door)

The `frame` operator emits data-grounded skeletons for the AI to assemble on. Authoritative shapes
come from `team.py schema` (`commands.frame`, `commands.slate-evaluate`, and
`context_specs.draft_spec`); the essentials are:

```json
{
  "kind": "frame", "format": "double", "anchor": ["Maushold"], "order": "common_first",
  "pool_size": 223, "partitioned": true, "frames_total": 11, "frames_shown": 11,
  "thin": false, "meta_fallback": false, "no_backbone": false,
  "handover": null,
  "fallback": null,     // {tier, reason} only when NO current-rule team carries the anchor (below)
  "skeletons": [{
    "frame_id": "fa1a799e1ab",                 // MECHANICAL hash, never a team-name/type label
    "group_signature": {"axis": "speed_control", "key": ["tailwind"]},  // the Tier-1 key ACTUALLY used
    // axis: "speed_control" (doubles) | "role_signature" (singles: hazard/setup/recovery/pivot)
    //     | "structure_not_observed" (species-only rows: structure was never published, so no
    //       signature can be read — real co-occurrence, unknown structure, NOT an archetype)
    //     | "unpartitioned_pool" (the pool did not separate structurally)
    "structural_profile": {"speed_control_modes": {...}, "structural_signals": {...},
                           "role_composition_norms": {...}},   // profile FACTS, no archetype label
    "prevalence": {"count": 48, "share": 0.215},               // a VIEW ordering, never a score
    "core_candidates": [{
      "species": "Maushold", "role": "anchor",  // or "core-partner" / "recurring-partner"
      "within_group_share": 1.0, "pool_share": 1.0,   // two views (glue vs archetype-defining), no composite
      "grounding": {"primary": {"item": "...", "ability": "...", "nature": "...", "moves": [...],
                                "sps": {...}, "share": ..., "confidence": ...},
                    "clusters": [{"item": "...", "ability": "..."}],   // the (item,ability) the slate binding accepts
                    "set_source": "group|species-overall", "grounding_ref": "repset:Maushold:double"},
      "set_guidance": {"moves": [...], "nature": "...", "sps": {...}},   // REFERENCE, not a hard lock
      "confidence": "medium"
      // grounding=null (+ set_guidance=null) when no real joint set exists (thin/off-meta) — build it
      // yourself + disclose; NEVER stitch a joint set from meta marginals
    }],
    "core_tiers": {"anchor": 1, "core": 2, "recurring_partner": 1},   // counts per role, all facts
    "flex_slots": {"open_count": 2,            // 2nd Mega / coverage / utility are YOURS (>=1 always open)
                   "substitutable_count": 1},  // recurring-partner slots: filled, but not binding
    "observed_facts": {"observed_mega_slots": {...}, // sample_count counts known registrations only;
                       // unknown_sample_count + sample_count = total_sample_count.
                       // profile.mega_registration.count is null when a Mega-capable member's
                       // set is unpublished/inferred; explicit Mega species remain observed.
                       "mega_registration_reference": { // group distribution when sample>=30,
                         "basis": "frame_group|frame_pool", // else whole-pool fallback
                         "distribution": {...}, "confidence": "medium|low"
                       },                                // descriptive prior, NOT a reserved Mega slot
                       "observed_fillers": [...],      // how real teams vary here — not a to-fill list
                       "role_composition_norms": {...}}
  }],
  "frame_receipt": {"kind": "frame_receipt", "fingerprint": "...", "audit_fingerprint": "...", "framed_at": "..."}
}
```

- Ordering is PREVALENCE under the `meta_conformance` knob (common_first / rare_first); an ANCHOR
  build shows every frame meeting the minimum sample threshold (`frames_shown == frames_total`) so an off-meta frame is
  never dropped. The `structure_not_observed` pool always sorts LAST — a category split, not a
  ranking: it is not a structural frame. `frame_receipt` extends the chain (audit → frame →
  slate); its fingerprint is TAMPER-EVIDENT (slate recomputes it from the saved skeletons).
- The two candidate TIERS carry different weight. `core-partner` (within-group share >=
  `CORE_SUPPORT`) is the backbone the slate binding enforces. `recurring-partner`
  (`PARTNER_SUPPORT`..`CORE_SUPPORT`) recurs below that bar and arrives equally grounded, but leaving
  its clusters is only REPORTED (yellow, owed a disclosure, never eliminated) — the tier is offered,
  not committed to, and pricing a substitution there at the core rate would cost more than going with
  the crowd.
- An EMPTY skeleton is never `confidence: medium`. With no tier above the support bar it reports
  `low` + `no-recurring-backbone`, and `no_backbone: true` says the same for the whole run: the
  library carries no recurring structure here, so ground every member through repset/search and
  disclose that the frame supplied no roster.
- Build-flow contexts/slates should carry `frame_required:true`. With that flag, `slate-evaluate`
  refuses a missing `--frame-output`, and `answer-audit` reports a violation if the saved slate output
  has no `frame_fingerprint` in its receipt chain.
- During the entire active week, `handover` reports evidence rules, contributing row count and expiry
  for validated prior-rule structural rows, regardless of the native team count. At expiry structure
  becomes target-rule-only and `handover` is `null`.
- An anchor that NO current-rule team carries still gets skeletons. `data/fallback_frames/<rule>_<format>.json`
  (rebuilt with the opponent cache) stores one RECIPE per such anchor — `{"source": "history",
  "seasons": [...]}` (older-rule teams built around it), `{"source": "partners", "partners": [...]}`
  (current teams carrying its specific usage partners together) or `{"source": "stand_in",
  "species": "...", "hits": {...}}` (a data-backed species doing the same job, matched on explicit
  predicate counts) — and `frame` computes the skeletons over that pool with its ordinary machinery.
  With no recipe it lends whole-library frames (`generic_library`). Every such skeleton carries
  `fallback: <source>`, `confidence: low`, `confidence_reason: <source>-fallback`, a Mega reference
  with `basis: "none"` (never gates), and binds SOFTLY: departing from any of its members' clusters
  is reported, never eliminated. The top-level `fallback` names the source; `meta_fallback` stays
  `true` (the anchor still has no current joint grammar of its own).

**The two fields the AI PRODUCES for the binding:**
- `slate.frame_bindings[i]` (aligned with `teams[i]`): `{frame_id, off_meta?:[species], deviations?:
  [{species, reason}], mega_deviation?:str|{reason,...}, off_meta_build?:bool}`. `frame_id` declares which skeleton the candidate builds
  on; a core-bearer (member ∈ that frame's `core_candidates` at the `core-partner`/`anchor` tier)
  whose `(item,ability)` is outside its repset `clusters` is a deviation — RED (no
  `off_meta`/`deviations` ack) eliminates in the funnel, YELLOW (acknowledged) survives. A member
  matching a `recurring-partner` candidate is always YELLOW (reported under
  `frame_binding.recurring_partners`, never eliminated). `off_meta_build:true` = a deliberate off-meta build (no core-bearer
  binding). Only `(item,ability)` is checked — `set_guidance` is soft. Independently,
  `mega_deviation` acknowledges a deliberate Mega-registration count outside a reliable frame's
  common lanes; without it, an observed minority/rare lane is eliminated under `proven`.
- `draft.frame_deviations`: `[str | {species, note}]` — REQUIRED disclosure when a frame-bound slate
  flagged a YELLOW deviation / off-frame advisory on a recommended SURVIVOR (name the species). A
  filled-check at answer-audit, never a veto.

For the final answer shape, initialize the draft with
`team.py draft-init --slate slate.json --slate-output slate_out.json [--recommended I J]`, then fill
the substantive empty fields before `answer-audit`. The helper copies selected survivor teams and
the saved `slate_receipt`; it does not choose winners or write the convergence judgment. A recommended
survivor whose `mega_registration_assessment.requires_deviation_ack` is true needs
`mega_registration_deviation:{reason,evidence,opportunity_cost}`. The slate itself must also contain at
least one survivor that is modal relative to its own bound frame unless the context explicitly selects
`mega_posture` or `meta_conformance:off_meta`; the final `recommended[]` set must retain at least one of
those modal survivors. Samples below 30 remain visible but are not load-bearing;
in reliable samples, share >=20% is common, at least 5% but below 20% is minority, and <5% is rare (modal is tracked
separately and may tie). These labels describe observations; they do not score team strength.

## 9. Top Meta (The Library On The Metagame's Fields)

`data/top_meta/<rule>_<format>.json` counts the current rule's real-team library on the in-game
metagame's own fields; `team.py topmeta` joins one scope with the in-game snapshot at query time. It
is a VIEW of the library, never a second metagame authority: `repset`/`frame` do not consume it, and
agreeing with them is not corroboration (same data). Display names: 竞技环境 / 上位環境 / Top Meta.

Counting units are decided on the dev side, where player identity exists, and shipped de-identified:

- **team unit** — one player's distinct six-species composition (the same six in several events,
  seasons or the event partition count once; swapping a member is a new unit);
- **set unit** — one player's distinct full build of one species (any changed field is a new unit;
  the same build seen with and without SP is one unit, kept with its SP);
- rows without player identity: byte-identical full six-member teams collapse into one team unit;
  their members never merge across different teams. Distinct players on one team stay separate
  (rentals and netdecks are real adoption);
- a Mega form counts as its base species (the stone stays in `items`); an ability that is not one of
  the base species' own is the Mega's run-form ability and counts as unknown.

Table shape (storage; consume through `topmeta`):

```json
{"schema_version": 1, "format": "double", "rule": "M-C", "season": "M-7",
 "built_for": {"season": "M-7", "rule": "M-C", "format": "double", "built_at": "...",
               "data_partitions": ["M-6", "M-7", "M-C-events"],
               "source_fingerprints": {"team_library": "<sha256>", "dex": "<sha256>"}},
 "base_species": {"Aegislash-Blade": "Aegislash"},
 "scopes": {
   "season": {"season": "M-7", "partitions": ["M-7"], "teams": 1764, "sets": 10261, "species": {}},
   "rule": {"partitions": ["M-6", "M-7", "M-C-events"], "teams": 9076, "sets": 50888, "species": {}}}}
```

A species entry is `{teams, sets, lanes: {observed_full_set, extracted_set}, panels}`; each of
`moves|items|abilities|natures|spreads|partners` is `{n, counts: {key: count}}` with its OWN `n` (the
units whose field is known — a missing field is never a zero). Spread keys are `hp/atk/def/spa/spd/spe`.
The season scope never reads event partitions; neither scope reads a handover predecessor. No count
is cropped and no publication threshold exists at this layer.

`topmeta` output (`schema: top_meta.v1`) carries `sample {teams, sets}`, `compared_with`,
`identity_folds` (library-only forms folded into the in-game ranking's identity through their dex
base), a `ranking`, and per-species panels. Every panel entry is ONE record with both sides:

| Field | Meaning |
|---|---|
| `count`, `share` | Top Meta units carrying the entry, and `count / n` |
| `ingame` | `{kind:"pct", value}`; `{kind:"bound", below}` = outside the in-game list, an UPPER bound (never 0); `{kind:"absent"}`; partners `{kind:"rank", value}` / `{kind:"unlisted"}` |
| `top_minus_ingame` | Top Meta share minus in-game share; the in-game perspective is its negation. `delta_is_floor:true` when derived from a bound |
| `within_noise` | the in-game value lies inside the Wilson 95% interval of the Top Meta share — a muting aid, not a significance claim |
| `marker` | count tier only: `outside_ingame_top` (seen here, absent from the in-game list) / `unseen_in_top` (count 0 while `(1-p)^n < 0.05`) |

Panel `tier` is `count` (n < 5, no numeric difference), `low` (5–29) or `reliable` (≥ 30); it decides
presentation, never existence. Ranking rows carry `rank` (competition ranking by team units), `teams`,
`share`, `tier`, `ingame_rank`, `rank_delta` (= `ingame_rank - rank`, null in the count tier) and
`within_noise` (the in-game rank falls inside the rank range implied by the share's Wilson interval).
The season scope compares only with the same season's snapshot (`compared_with.window: same_season`).
The rule scope spans seasons whose in-game snapshots cannot be pooled, so it compares with the current
in-game snapshot (`window: current_snapshot`): the two sides cover different time ranges, and a consumer
must say so. No field is a score.
