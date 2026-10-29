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
- [Metagame speed ladder](#10-metagame-speed-ladder)
- [Reverse observed answers](#11-reverse-observed-answers)
- [Reference KO scan](#12-reference-ko-scan)
- [Stated scenario](#13-stated-scenario)
- [SP threshold](#14-sp-threshold)
- [Public team codes](team-codes.md): canonical six-member lookup and bounded local batch interface.
- [Type and rules facts](#15-type-and-rules-facts)
- [Configuration correction suggestions](#16-configuration-correction-suggestions)

## 1. `team-json` (the canonical team structure)

Authored member species/item/ability/nature/move names are normalized on a mutable copy through the
dex public exact/Mega-compose/fuzzy resolver. Fuzzy corrections identify from/to, score/distance and
the member field where applicable; JSON validate/diagnose carry `name_resolution`, and the CLI
also discloses corrections on stderr. Unresolved values stay verbatim for strict validation. Stored
library evidence and original slate/draft receipt inputs are not rewritten by this cleanup.

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
- All authored SP uses `hp/atk/def/spa/spd/spe`. Team input also accepts legacy
  `at/df/sa/sd/sp` (respectively `atk/def/spa/spd/spe`); conflicting aliases refuse. In particular,
  old `sp` means **Speed**, while canonical `spd` means Special Defense. Public set guidance,
  matchup/tune/check payloads and cache-query views use canonical keys. Tune accepts either key
  family in explicit opponent/attacker `spread` or `sps`, validates the allocation, and rejects
  conflicting aliases or disagreeing `spread`/`sps`. Legacy cache bytes stay unchanged.
  Original slate/draft inputs are preserved verbatim for receipt rebinding.
- A fully specified AI-authored proposal has `provenance:null` and omitted/null `completeness`.
  This treats the supplied configuration as the scenario to evaluate, without claiming a real-team
  observation. Do not tag it `inferred_set`: that marks guessed source data and makes moves/roles
  non-authoritative. Keep source tags when working from an observed/extracted record; validation
  never turns an authored proposal into `extracted_set`.
- For Mega proposals, either use the canonical Mega form with its own required stone and ability,
  or use the ordinary form holding that stone. The latter accepts the base entry phase or Mega
  run-form ability. Reachability uses the dex's exact host/stone mapping, not Species Clause
  grouping: regional siblings cannot borrow Mega abilities. A foreign stone is legal to hold
  but cannot activate Mega Evolution on that host.
- Missing SP is reported as a warning; write six explicit zeroes for an intentional zero-SP set.
  `parse --game-format single|double` preserves the intended battle format and refuses a conflict
  with a format already present in the input.
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

`search`/`observed` retrieval and `repset`/`landscape`/`frame` views use
`identity_basis:dex_battle_form`: a base with its own stone and
that direct Mega representation match; other ordinary/Mega/X/Y/Z forms stay separate. Search slots
echo canonical `species`, not a folded grouping key. Fuzzy query corrections appear under
`name_resolution`; unresolved names and missing battle-form facts remain under
`unresolved_conditions` / `identity_gaps`. Unknown leaves stay unknown through AND/OR/NOT, so NOT
cannot turn a missing authority into proven absence. Observed hard-avoid queries separately count
unprovable rows as `excluded_by_identity`; its inventory `owned_coverage` still groups base species.
Source team views and frozen ids remain the original registered records.
Context species corrections are disclosed in `context-audit.name_resolution` and on CLI stderr;
its receipt continues to fingerprint the raw context rather than a corrected replacement.

`fill` counts and builds use the same actual-form view; the pool expands dex-declared Mega options.
`usage_rank` belongs only to an exact ranked species; `pool_source` names the parent pool entry and
its rank, never a fabricated form usage. Owned coverage and on-team duplicate exclusion use Species
Clause groups. `replace.member` selects exactly one actual form; output `registration` keeps the
before/after input members beside the form-based diff. Unknown or duplicate selectors are skipped.

For `oppmatrix`, a species name selects exact cached `run_form` (or `species` when no run form is
declared). Raw output selects a modal retained build within that form; `--as-checks` expands its
retained variants. Exact variant ids remain addressable. A raw query with no matching form returns
`not_found`, never the full matrix. Environment-wide CHECK portfolios remain labeled as observed
species portfolios; their variant catalog supplies the distinct run forms.

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

The **live `repset` shape** (one archetype object, canonical SP spread): `species`, `format`, `source:
"real-team"`, `ability`/`item`/`nature`/`moves` (the co-occurring joint set), `cluster:
{item,ability}` (the cluster key; `null` + `fragmented:true` on the no-dominant-archetype fallback),
`sps` (real co-occurring SP spread in canonical keys, or `null` when the source has none → use meta spread),
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
> resolution keeps the exact requested actual form in both formats. A disposable dex-backed library
> view maps base + own stone to that Mega, with a uniquely determined Mega ability when the observed
> base ability differs. Ordinary anchors never promote to a library Mega. The output `query` block
> echoes `{species, resolved, item_filter:null}` for transparency. A derived archetype carries
> `configuration_basis:"battle_form"`, `registration_sample` (the modal joint-set count), and
> `registration_modes:[{species,ability,count}]` (observed registration facts, counts sum to that
> sample). These fields describe a deterministic projection, not a newly observed post-Mega ability.
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

`goals:[{kind:"answer"|"include",species:<dex-resolvable Pokemon name>}]` describes reporting
targets. Names resolve by exact battle form (base plus its own Mega stone is that Mega).
`answer` adds the opponent to the slate battery even outside top-K and reports each candidate's
observed CHECK floor, witnesses, sample coverage and calculation completeness. `include` reports
actual-form presence. Neither goal eliminates a candidate or means a battle win; use `locked`
for mandatory membership and `benchmarks` for exact tune constraints. Unknown names refuse before
calculation. The original context remains unchanged for fingerprint binding.

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
    "min_speed": 120,                     //   min_speed (int, max-Spe +nature line must reach it)
    "answer": "Dragonite"                 //   exact opponent name/variant id or list; same candidate
                                          //   build must answer every retained build of every target
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
  "skip_checkpoint": false,               // checkpoint: user explicitly requested no mid-build pause
  "checkpoint_required": false            // user requested an intermediate steering choice
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
accepts the opponent's ability/item/nature plus `spread` or `sps` (canonical or legacy keys); it is used consistently
for damage and speed. When absent, Tune resolves a real joint set first, then a disclosed meta/synthetic
fallback. Survival cards compare HP, relevant Def/SpD, and bounded mixed allocations for the same
objective; `probability_lanes` are explicitly scoped to their single stat axis. Speed cards use the
resolved set as the main target and keep the max-SP positive-nature target in `ceiling_lane`. Adjacent
survival/KO tiers and probability lanes are context, not silent replacements for the requested target.
For `survive`, `hits: 2` enumerates the independent 16×16 damage-roll pairs and applies entry-hazard
chip once. Between-hit recovery, ability/stat changes, and field changes remain explicit assumptions
rather than being silently approximated.

Benchmark `member` and locks select actual forms: a registered Garchomp with Garchompite is selected
by `Mega Garchomp`, not `Garchomp`. A missing or duplicate selector yields a positional `skipped` card
with `benchmark_index` and `reason:member_not_found_or_ambiguous`; resolved cards retain
`registration_member` when different. Implicit opponent sets and nature distributions never borrow
another form's sample. Explicit base-plus-own-stone targets still resolve to their actual form;
`vs` is the only identity field. Unknown opponent-set fields (including `name`, `species`, `run_form`)
are fatal contract errors. Optional `boosts`/`status` apply only to damage benchmarks; speed refuses
them because its solver does not model that opponent state.

Nested benchmark/replacement species, moves, items, abilities and natures use typed public dex
resolution. Numeric outspeed targets (including integer strings) remain numeric coordinates.
Fuzzy corrections carry `path` in `name_resolution`; unresolved names refuse before calculation.
`context-audit` reports those names as conflicts and uses actual forms for member membership.
Only working copies are normalized; raw inputs and free text remain intact for receipt binding.
The same rule covers `frame_bindings[].off_meta`, `deviations[].species` and structured
`draft.frame_deviations[].species`. Structured declarations match exactly, so a Mega declaration
cannot acknowledge its ordinary form or another X/Y/Z form.

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

JSON commands accept `--out <file>` to atomically save the full canonical result before a display
projection. `review` and `try` default to summary; existing commands retain their full default.
`--view full|summary` supports review, try, diagnose, select, oppmatrix, topmeta, frame, tune, intake,
matchup and slate-evaluate. Legacy `--matchup-view` and
`--slate-view` remain accepted; conflicting flags refuse. Summary is not an audit artifact; a summary
frame or slate explicitly refuses when supplied as gate evidence.

New summaries carry `view:summary` and `projection {display_only, omitted, limit, detail}`. Each omitted
entry identifies a source path and count or replacement. diagnose omits the member grid; select keeps
every lineup; both retain representative grades, observed floors and witness ids, calculation
completeness, missing mass and exceptional variant qualifications. frame keeps grounded primary sets,
clusters, sample/confidence and obligations, omitting duplicate guidance and structural profiles. tune
keeps the requested target/result, KO tiers, conditions and funding; adjacent probability lanes are
available in the full artifact. intake overview summary returns the next numbered onboarding batch,
with trilingual labels, maps_to and progress bookkeeping; `--next --answered` continues the walk.
For intake, summary requires `--onboarding` or `--next`; the ordinary refinement menu remains full.
Repeated holes/contested rows may carry `details_ref`, a JSON Pointer to an identical `by_opponent`
row within that summary; the target retains the complete compact qualifications.

topmeta/oppmatrix accept `--summary-limit N` (positive integer, default 6; requires `--view summary`),
or per-session `summary_limit`. It limits displayed tables/panels in source order, never calculations
or population ranks; totals and omitted counts disclose the selection. Concrete set catalogs,
population/threshold/sample and confidence remain. Other operators reject this option.
Input files and receipt-bound original objects are never overwritten or normalized by saving.
Sessions can supply JSON values inline in file/context/receipt fields and use backward JSON Pointer
references to prior complete `{op,rc,result}` entries. Session `out` saves one full operation result;
CLI `--out` saves the complete result array. See `examples.md` for shapes and failure semantics.

`landscape` uses a disposable cache bound to team contents, dex/current authority, implementation,
format/scope, anchor filters, order, aspects, core limit and live handover validity. Its `cache` metadata
is `{status:hit|miss|disabled|unavailable|not_stored, fingerprint}`. `--no-cache` (session `no_cache:true`)
bypasses reads/writes. A corrupt or inaccessible cache recomputes; a source change during a query
refuses with `authority_mismatch`. Loaded code/dex/current changes require restarting the process.
In a checkout, files live in `.cache/team-landscape`; installed skills use
`~/.pokemon-champions/cache/landscape`. `CHAMP_LANDSCAPE_CACHE` can select a local cache directory.

`landscape --scope high` (session `scope:"high"`) applies `topmeta.high_tier` to native current-rule
published joint records, with no handover or fallback. Omit `scope` for the existing library scope;
other explicit scope values, `--season` together with high, or a context targeting another rule refuse.
`population` carries `scope`, `sample_unit:"shipped_team_record"`, `criteria`, `composition` by eligibility
bar, `examined_records`, `eligible_records`, `excluded_records`, `cross_partition_identity_dedup:false`,
`handover_included:false` and `same_counting_units_as_topmeta:false`. `sample_count` equals eligible
records. Public rows have no player identity and can omit qualifying achievements present in another
private source observation; neither the denominator nor shares reproduce Top Meta's counting units.
Missing performance is excluded. An empty qualified sample stays empty/low-confidence. Filters retain
their existing meaning: only `observed_cores` uses the member filter; structural distributions cover
the whole qualified sample. Actual Mega forms, aspects and common/rare ordering are unchanged.
Population facts join the cache key; source edits invalidate both the membership and counts.

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
  "frame_receipt": {"kind": "frame_receipt", "identity_basis": "dex_battle_form", "fingerprint": "...", "audit_fingerprint": "...", "framed_at": "..."}
}
```

- Ordering is PREVALENCE under the `meta_conformance` knob (common_first / rare_first); an ANCHOR
  build shows every frame meeting the minimum sample threshold (`frames_shown == frames_total`) so an off-meta frame is
  never dropped. The `structure_not_observed` pool always sorts LAST — a category split, not a
  ranking: it is not a structural frame. `frame_receipt` extends the chain (audit → frame →
  slate); its fingerprint binds the battle-form basis and saved skeletons. Grounding preserves the
  primary representative's `configuration_basis` / `registration_modes` / `registration_sample` when
  projected; `(species,ability)` and accepted clusters use the actual form. Legacy frames without
  that basis require a new `frame` run (`receipt_upgrade_required`).
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

Slate `constraint_satisfaction` reports `identity_basis` and `identity_complete`. Locked, avoided,
preferred and soft-avoided species use exact actual forms; owned coverage and Species Clause retain
their separate grouping rules. Unproved battle-form identities fail the cheap stage before matchup.
The `slate_receipt` carries `constraint_facts_bound:true`; its candidate digest includes both
`constraint_satisfaction` and `frame_binding`. Answer-audit refuses edits to these saved facts and
requires re-slating for legacy receipts lacking this binding.

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

**Exploration views and concrete answers:**

`frame ... --draw N [--seed INT] [--seen-teams shown.json]` adds `draw` with the actual seed,
ordered `frame_ids` and per-draw shared-core counts. It first prefers cores unlike shown teams,
then minimizes maximum core overlap with earlier draws, and samples tied frames by observed
`prevalence.count`. All-zero pools use uniform ties. Draw count is caller-selected; no fixed
candidate count is imposed. When all remaining cores repeat, the view discloses
`repeat_unavoidable:true`. The entire `skeletons` array stays intact and receipt-bound; the view
is neither a replacement receipt nor a certification of structural variety or quality.

`observed --order diversity [--seed INT] [--seen-teams shown.json]` keeps explicit constraint
overlap first, then minimizes maximum shared members with shown/selected rosters. Already-shown
rosters and duplicate rosters are excluded even if configurations differ. Ties use ordinary
tier/recency/id order, or seeded random order when supplied. Each row exposes its overlap facts
and original team id; provenance, performance and constraints retain their existing meanings.
Counts-only (`--limit 0`) returns no bodies. `seen-teams` accepts one team-json, a list, or
`{teams:[team-json,...]}` and is protected as an input file; inline JSON and backward references
work in session. Names/items must resolve and formats must match. Observed uses exact battle
forms (base plus own stone equals its Mega); frame core avoidance groups by Species Clause so
switching only the Mega form does not count as a different core.

`fill ... --context ctx.json` with `need.answer:name|[name]` intersects the current counters
evidence on one exact variant id. Every retained target build must have a complete C1/C2 CHECK
for that same candidate build; complementary variants cannot be combined. Unknown forms, stale
cache and unavailable evidence refuse or remain visibly absent. `answer_scope` retains the target
builds, cache identity, coverage and omission counts. Each returned `builds[]` includes the original
variant/source/SP provenance, a ready-to-use `team_member`, `observed_complete:true`, and
`answer_facts` with the full checks/caveats/contested flags. Only real-team joint configurations
including observed SP are transported: meta SP fallbacks and unknown fields are omitted. The
existing team's items and avoided items are excluded at build level without replacement. Other
need predicates must hold on the same build, whose `addresses` lists the actual matching ability
or moves; `min_speed` retains its existing theoretical species-ceiling meaning. Neutral static
CHECK and isolated doubles pairs are conditional reference facts, not a win-rate claim.

With `meta_conformance:off_meta`, the ordinary fill pool also includes actual-form species
present in the scoped real-team library outside the in-game usage pool. Such candidates have
null usage rank, actual sample/co-occurrence counts, and up to three `tail_evidence.team_ids`
for `show` (with a truncation flag). Missing joint builds stay missing; a library appearance
does not make a strong or complete configuration. Owned-only remains a hard pool restriction.

**Assemble the chosen candidates:**

```bash
python scripts/team.py slate-init assembly.json --context context.json --audit-receipt audit.json --frame-output frame.json --intake-output intake-last.json --out slate.json
python scripts/team.py slate-evaluate slate.json --frame-output frame.json --format json --out slate-full.json
python scripts/team.py checkpoint slate-full.json --slate slate.json --format json
python scripts/team.py draft-init --slate slate.json --slate-output slate-full.json --out draft.json
```

`assembly.json` is `{candidates:[...]}`. Each candidate supplies either `team:<team-json>` unchanged
or an ordered `members:[member-json | {from_frame:<canonical battle-form species>}]` list. It also
supplies the desired binding fields above (`frame_id`, `off_meta`, `deviations`, `mega_deviation`,
`off_meta_build`). A frame-bound candidate must explicitly name a frame or declare an off-meta
build; the helper never chooses or acknowledges a departure. The `from_frame` object has no other
fields: it copies that exact species' `grounding.primary` item/ability/nature/moves and maps `sps`
to `spread`. It does not join marginal usage, choose a different cluster, restore a guessed base
ability or fill missing SP with zero. Mix those references with authored members, or supply an
entire authored team when adjusting sets. Unknown/ungrounded references refuse.

The result itself is a usable slate with `kind:slate-init`, `context`, `audit_receipt`, `teams`,
optional aligned `frame_bindings`, `onboarding`, and `assembly` metadata. Newly composed teams use
the context's format/season/rule and empty provenance. They are proposals, not an observed whole
team; `assembly.frame_sources` retains the grounding coordinate and battle-form/registration
attribution, while `missing_fields` lists unknown copied fields. Authored team objects retain their
original bytes at the JSON-value level, including legacy SP keys, for subsequent receipt binding.
Assembly checks input shapes and frame/audit continuity; legality and deliverability remain the
existing slate and answer gates. Always pass the same full frame output to `slate-evaluate`.

`--intake-output` accepts the full latest `intake --next` output. Its `context_fingerprint` must
match the final context; refresh the cheap intake step after a context edit. Only explicit `answered`
IDs are carried, and covered dimensions are recomputed. An unanswered displayed batch or a saved
`done:true` alone cannot complete onboarding. Without this input the helper uses only context
coverage. `draft-init` computes `onboarding_summary.status:completed|incomplete`, retains explicit
answered IDs and describes context coverage; it never fabricates user responses. `session` accepts
inline/reference values for `file`, `context`, `audit_receipt`, `frame_output` and `intake_output`,
so these deterministic transport steps can share the existing session command with the gates.

For the final answer shape, initialize the draft with
`team.py draft-init --slate slate.json --slate-output slate_out.json [--recommended I J]`, then fill
the substantive empty fields before `answer-audit`. The helper refuses summary, outdated or mismatched
slate receipts, copies selected survivor teams, and returns optional `claim_candidates` beside `draft`.
These are numerical OHKO facts from receipt-bound matchup buckets, with `team`, `evidence_id`, numeric
`expect`, `direction:we|them`, and the original `opponent_variant`/`opponent_is_modal` qualifiers. They preserve direction and
input order; speed aggregate counts cannot supply per-encounter speed values. `draft.claims` remains
empty until the host chooses facts used in its answer; answer-audit recomputes those chosen facts.

### Candidate comparison and delivery checks

`slate-evaluate.comparison` preserves candidate order. `candidates[]` reports hole counts
(`null` when no calculated CHECK floor exists), incomplete-opponent counts, library overlap and goals.
`pairs[]` reports shared actual battle forms, bound-core intersection and per-opponent floor
comparisons with JSON Pointers into full candidate facts. Core overlap is intersection size divided
by the smaller bound core; no bound core means unknown, never zero. Library composition overlap
separately uses registration species (base-folded) and supplies a count distribution, not a
configuration identity or a strength ranking. Joint-set copy detection keeps its existing semantics.
All comparison and goal facts, including library overlap, are bound by `comparison_facts_bound`;
old slate receipts must be regenerated. Summary is still display-only.

When any survivor pair has core overlap over half or unknown cores, answer-audit requires
`exploration:{reason:<nonempty explanation>}`. A constrained pool, one requested structure or
deliberate local variants may explain the exception; the gate never forces another candidate.
For verbatim library adoption, `recommended[].adoption_review.other_structures` requires
`{considered:bool,reason:str}`. This is a disclosure, not proof of the model's private reasoning.
`draft-init` exposes `exploration_facts` and leaves needed explanation fields empty.

Whole-team matchup statements use `team_claims:[{claim:str,evidence_ref:str,expect:object}]`.
The reference is `/candidates/I/matchup_risk/check_coverage/by_opponent/J` from a recommended
candidate's full saved slate. `expect` must equal the entire row, preserving all qualifications.
This verifies a query receipt and its saved facts; independent calculator re-execution remains
the numerical `claims[]` path. Arbitrary prose is not inferred into claims automatically.

`localize team.json` (or session `{op:"localize",file:<team>}`) returns `members[]`, each with a
zero-based slot and species/item/ability/nature/moves labels `{name,name_zh,name_ja}` from the
public dex resolver. It never fills unspecified facts or rewrites the input. Unresolved entries
remain visible and exit 1. Copy these official labels into final tables.

Optional `text_check:{text:str,lang:"zh"|"ja"|"en"}` in the answer draft enables bounded text checks:
missing official member names (including a misspelled member label), percentages without a matching
numerical claim, and incompatible writing scripts. Official dex mentions absent from supplied
context/team/candidate facts appear in `text_check.review_required`, not blocking violations:
dictionary matches cannot distinguish a factual claim from a homonym or a comparison of absent moves.
Review those mentions against evidence before delivery. It accepts official translations as well as canonical English.
Percentage matching uses one decimal; the numerical claims still go through their existing
recompute checks. Coverage percentages may also come from `coverage.represented`, `calculated` and
`unrepresented` in a complete, receipt-bound `team_claims[].expect` row for a recommended candidate;
these ratios are converted to percentages. Unbound, changed or unrecommended rows do not supply values.
The report states the limits: invented non-member entities outside the dictionary,
CJK homonyms and sentence-level meaning require model/human review. It never rewrites the text.
Damage qualifiers must match a receipt-bound cell for the selected team and coordinates. When
multiple builds or attack directions share the same coordinates, supply the exact variant and
direction; the gate rejects ambiguity or a mismatched qualifier instead of choosing the first cell.
No benchmarks fills only the factual `not_run` tuning explanation. The helper does not choose winners
or write the convergence judgment. A recommended
survivor whose `mega_registration_assessment.requires_deviation_ack` is true needs
`mega_registration_deviation:{reason,evidence,opportunity_cost}`. The slate itself must also contain at
least one survivor that is modal relative to its own bound frame unless the context explicitly selects
`mega_posture` or `meta_conformance:off_meta`; the final `recommended[]` set must retain at least one of
those modal survivors. Samples below 30 remain visible but are not load-bearing;
in reliable samples, share >=20% is common, at least 5% but below 20% is minority, and <5% is rare (modal is tracked
separately and may tie). These labels describe observations; they do not score team strength.

## 9. Top Meta (The Library On The Metagame's Fields)

The meta skill exposes the primary standalone `meta_query.py topmeta` query; `team.py topmeta`
remains compatible for team sessions. Both use this module's pure comparison. Name filters carry
aligned `queries[]` with canonical/match metadata and per-query errors; they never map a requested
Mega to base counts silently. The published table's `data_granularity` is `species_aggregate`, so a
Mega-only distribution is explicitly `unsupported_mechanic` (exit 1), with an optional
`available_reference` naming the broader species population. Comparison is disabled across rules.

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
   "rule": {"partitions": ["M-6", "M-7", "M-C-events"], "teams": 9076, "sets": 50888, "species": {}},
   "high": {"partitions": ["M-6", "M-7", "M-C-events"], "teams": 1694, "sets": 9651, "species": {},
            "criteria": {"tournament_top8": {"placing_at_most": 8, "field_size_at_least": 16},
                         "ladder_top": {"rank_at_most": 100, "or_rating_at_least": 2000}},
            "composition": {"tournament_top8": 1618, "ladder_top": 76}}}}
```

A species entry is `{teams, sets, lanes: {observed_full_set, extracted_set}, panels}`; each of
`moves|items|abilities|natures|spreads|partners` is `{n, counts: {key: count}}` with its OWN `n` (the
units whose field is known — a missing field is never a zero). Spread keys are `hp/atk/def/spa/spd/spe`.
The season scope never reads event partitions; no scope reads a handover predecessor. No count
is cropped and no publication threshold exists at this layer.

The `high` scope (高端 / 上位 / high-level) is the rule scope filtered on RAW performance facts, each
judged inside its own source kind (`topmeta.high_tier`): a tournament row needs `placing <= 8` in an event
with `field_size >= 16`; a ladder row needs a final rank `<= 100` or a rating `>= 2000`; anything else,
including a missing fact, does not qualify. It is a filter, never a weight, and it uses the same identity
dedup, so one player's six counts once however many cuts it made. `composition` counts the team units
each bar contributed (a unit can meet both). A table built without it has no `high` scope, and
`topmeta --scope high` then refuses instead of answering from another scope.

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
must say so; the `high` scope compares the same way and also carries `criteria` and `composition`. No
field is a score.

## 10. Metagame Speed Ladder

`team.py speed-ladder <species> --game-format single|double [--limit 20] [--set JSON-or-file]`
`[--conditions JSON-or-file] [--opponent-ranges] [--format md|json] [--out full.json]`

This factual query does not require a team or UEP receipts. Format is required. The current meta
ranking supplies the parent pool; `limit` is a positive number of ranked parents, not a raw-team
limit. Their dex-declared Mega forms become separate rows when a usable configuration exists.
`pool_source.usage_rank` belongs to the named parent, never to an expanded form. The queried ordinary
form is never promoted to a Mega; a stated own stone explicitly changes the battle form.

The current-rule reader loads all accepted season/event partitions and any receipt-approved
handover rows. `coverage.teams_read`, `data_seasons`, `team_limit:null`, `identity_gaps` and
`unavailable` disclose the population and missing configurations. Every numerical point is priced
by one deduplicated public NCP speedline batch at level 50 / IV 31. No source data is rewritten.

`--set` accepts item, ability, nature, `spread` or `sps`, `boosts:{spe:-6..6}` and calculator `status`.
SP keys use the canonical contract (legacy aliases remain readable); conflicting aliases, invalid
totals, unknown fields and unsupported coordinates refuse. Name resolution is public dex with
disclosed fuzzy corrections. A stated set inherits missing nature/SP/ability from its exact-form
reference when available, otherwise neutral nature / 0 Spe SP / a unique dex ability, all disclosed
in `asked.basis`. Its omitted item means no held item, including no implicit Choice Scarf; a direct
Mega instead receives its required stone and rejects another item. An explicit partial spread has
0 in omitted stats. `vs`/`name` overrides are not accepted inside the set.

Example files:

```json
{"nature":"Jolly","spread":{"spe":32},"ability":"Rough Skin"}
```

```json
{"weather":"Rain","terrain":"Electric","tailwind":"mine","trickroom":false}
```

Conditions accept `weather:Sun|Rain|Sand|Snow|Harsh Sun|Heavy Rain|Strong Winds`,
`terrain:Electric|Grassy|Psychic|Misty`, `tailwind:none|mine|opponents|both`, and boolean `trickroom`.
Weather and terrain names are case-insensitive. The same conditions price the reference sets and
each observed configuration, with Tailwind applied only to its stated side. A weather-setting
ability does not silently set the field. Trick Room reverses order at equal move priority; speed
values and ties stay unchanged. Activation-dependent abilities/items use the calculator's baseline
state; the result does not simulate item consumption or earlier turns.

`asked.configuration` and each row's `configuration` are actual-form speed coordinates. Their
reference is the most frequent known joint speed configuration (item, ability, nature, Spe SP) in
the full observed pool. `basis` carries count, sample, confidence and registered-form/ability modes.
Only when no eligible joint exists may exact-form `meta_marginals` supply a reference; that combination
need not co-occur. Missing configuration is explicit, never a synthetic max-speed opponent.

`observed` keeps `matched_rows`, `eligible_rows`, `completeness_counts`, excluded counts by reason,
`computed_rows`, `failed_rows`, numerical `distribution:[[speed,count],...]` and its range. Only
`observed_full_set` / `extracted_set` rows with known speed coordinates qualify; stored names use
strict resolution, never fuzzy repair. A missing Spe SP is not 0. An itemless record is usable only
when another member establishes that the source recorded held items. Extracted observations remain
identified. All percentages refer to eligible observations, not players or in-game usage.

At least 5 successfully priced observations are needed for share labels. A single failed eligible
calculation suppresses labels rather than shrinking the denominator; failure inputs and errors are
retained. For each opponent reference, `comparison.counts` records how many asked-form observations
it precedes, ties, or follows. Labels use ≥90% for `reliably_first/last`, ≥60% for `usually_first/last`,
and ≥50% for `usually_ties` (checked first); otherwise `depends_on_build`. With a stated asked set,
comparison is exact (`stated_set`), not a population share. Insufficient observations use
`reference_only`. `--opponent-ranges` additionally prices every opponent form's own joint distribution;
these are observed configurations, not combinations of independent range endpoints.

Rows sort by speed in move order; `groups` lists `opponent_first`, `ties`, `asked_first`. This is a
numeric ordering, not a strength score. Session uses `op:"speed-ladder"`, scalar `species`,
`game_format`, optional inline/file `set` and `conditions`, `limit` and boolean `opponent_ranges`.
Backward result references and `--out` use the normal full-artifact contract.

## 11. Reverse observed answers

`team.py counters <species|variant-id> --game-format single|double [--top-n 30]`
`[--format json --view summary --summary-limit 6 --out full.json]`

This reads the current rule's opponent cache, resolves names through the public dex (fuzzy corrections
are disclosed), and selects the exact `run_form`, falling back to `species` only when the cache has no
run form. An ordinary form never selects its Mega variants; an exact variant id narrows to that one build.
No data is refreshed or written. Missing exact targets/cache return `not_found`; stale source/calculator
authority or a CHECK inconsistent with its original evidence returns `authority_mismatch` and exit 1.

`top_n` is a candidate **usage-rank cutoff**, integer 1–60, default 30. It does not limit output rows
or select stronger candidates. Source usage order is retained within each result group; a Mega's
`candidate.pool_source.usage_rank` belongs to its named cache pool, not a separately measured form rank.
`coverage.scope_complete` reports whether the installed cache spans the requested rank scope.

- `target_builds`: every selected retained build, with canonical identity, item, ability, nature, moves,
  SP, sample/coverage and source assumptions. Candidate builds carry the same fields. SP fallback to
  meta remains explicit in `source/note`; variation is disclosed in `speed_profile`.
- `rows`: one candidate build with a complete C2/C1 observed floor across every target build. C2 means
  a safe switch-in then static 1v1; C1 means an even-footing answer only.
- `conditional_rows`: the floor remains C0. `answers` gives its C1/C2 target pairs; `blocked_by` gives
  every C0 pair. Different configurations of one candidate species are never combined into a stronger
  row. An answered pair is not an all-build claim.
- `incomplete_rows`: `grade` and `observed_floor` are null; missing targets remain in `missing` and in
  the coverage denominator. `calculated_subset_floor` names only calculated witnesses and cannot
  establish a complete answer. Meta-only targets/candidates have no invented incoming attack.
- Each pair names `target_variant` and retains forward `offense`, reverse `incoming`, `speed`, OHKO
  routes and the existing CHECK explanation (`lines`, certainty, safety, caveats, resolvability and
  soft Intimidate/priority lanes). The existing grade engine supplies these facts; no second formula.
- `coverage` counts complete, conditional, incomplete and unanswered candidate builds. Complete C0
  builds with no C1/C2 pair are counted and listed by id, not presented as useful answers.

`coverage_basis` names the cache species pools underlying target sample shares. Shares are not
renormalized after exact-form filtering, and are neither form usage nor win probabilities. The retained
tail remains unknown. Conditions are those of the cached neutral reference; doubles is an isolated pair.
Same-field/switch-in turn budgets do not model a full battle, repeated setup/healing or partner support.

Full output is the default. Summary caps whole rows per group, retaining source order and omission
counts. **Every answer and blocker of a displayed row remains**, with KO and CHECK caveats; only
repeated explanation fields and non-answer id lists are omitted. `--out` always stores the full result
from that same query. Session uses `op:"counters"`, scalar `species`, `game_format` (or `format`),
`top_n`, and the normal view/output/backward-reference fields.

## 12. Reference KO scan

`team.py ko-scan <species|variant-id> --game-format single|double [--top-n 30]`
`[--direction both|attacking|defending] [--format json --view summary --out full.json]`

The current opponent cache supplies the facts. The species selector resolves through dex and matches
the exact `run_form` (or `species` when no run form exists). `reference_build` is the highest-coverage
retained configuration of that form, with the greater variant id breaking ties; `other_builds` lists
the remaining exact-form configurations. An explicit variant id chooses that one build. Neither an
ordinary query nor reference selection borrows a Mega form. Source, SP fallback, sample shares and
pool rank stay attached to the full build; this reference is not the user's authored set.

`top_n` selects opponent usage ranks 1–60 (default 30), not result count. `direction` defaults to
`both`; `panels.attacking` lists defender builds hit by the reference, while `panels.defending` lists
attacker builds hitting it. Every row carries both `attacker_variant` and `defender_variant`, its
`opponent` build, complete cached `damage` fact, speed context and accuracy-based `ohko_moves` routes.
These routes do not enter the damage-based OHKO/2HKO verdict.

Each panel's `groups` partitions rows by:

- `hits`: the cached `ko_chance.n` (1 or 2);
- `calculator_guaranteed`: the cached boolean, **only within the calculator's reference assumptions**;
- `qualifications`: complete `ko_caveats`, `disguise_adjusted` and `hits_range`, when present.

`conditional:true` means a mechanical qualification applies even if the calculator reports a
guarantee. Effects with the same code but different details/directions remain separate. Every row's
`ko_chance.chance_pct` remains attached to that configuration; probabilities are never merged.
Variable hit-count envelopes and `central_percent` remain distinguishable: `ko_chance` can apply to
the central band. Generic static/multi-turn caveats remain in the row's damage fact. These are not
unconditional battle guarantees or win rates.

The recovery-aware `ko_chance` controls membership; `ko_guaranteed`/`ko_possible` percentage buckets
are context, never a fallback verdict. `outside_scan` contains supported calculator verdicts above
two hits. `missing` retains absent cells, absent observed attackers, invalid or unclassifiable facts
with explicit reasons; no omission proves survival. A defender-only meta reference can still be
hit, but cannot acquire an invented attack row. Coverage counts satisfy:

`compared_builds = matched_builds + outside_scan_builds + missing_builds`.

The cache stores the hardest move by maximum damage, not every move. Conditions are the cached
neutral reference; no full battle, partner, accuracy or evolving multi-turn simulation occurs.
Top-level `coverage.scope_complete` reports cache rank coverage. In-group rows retain usage order;
groups follow their first occurrence, with no strength ranking.

Summary limits displayed rows **within every qualification group**, leaving each group's qualifiers
and full `builds` count intact. Identical row qualifiers move to the enclosing group; they can be
reconstructed exactly. Missing-row truncation and omitted outside-scan records are counted. `--out`
saves the full result from the same query. Session accepts `op:"ko-scan"` (or `ko_scan`), scalar
`species`, `game_format`/`format`, `top_n`, `direction`, views, outputs and backward references.
Errors and cache-authority checks follow `counters`; malformed directions are rejected before I/O.

## 13. Stated scenario

`team.py scenario scenario.json [--out result.json]` accepts a JSON object:

```json
{"attacker":"Garchomp","defender":"Hippowdon","move":"Earthquake","format":"single","attacker_set":{"nature":"Adamant","sps":{"atk":32,"spe":32},"boosts":{"atk":2}},"field":{"weather":"sand","reflect":true}}
```

The attacker and defender may be exact variant IDs. Each name otherwise resolves through the dex and
selects the highest-coverage retained build of that **exact battle form** in the current-rule opponent
cache. The cache's Top-K is the scope, and an absent form/variant returns `not_found`; no ordinary or
Mega form is borrowed. `basis` preserves both selected configurations and explicit overrides. An
override is a hypothetical set, not an observed joint configuration or usage statistic.

`attacker_set` and `defender_set` may replace `item`, `ability`, `nature`, `sps`, `boosts`, or `status`.
`sps` replaces the entire spread; omitted stats become zero. Canonical keys are
`hp/atk/def/spa/spd/spe`, each 0–32 and total at most 66. `boosts` uses
`atk/def/spa/spd/spe` with integer stages -6..6. Status uses the calculator's exact names:
`Healthy`, `Burned`, `Paralyzed`, `Poisoned`, `Badly Poisoned`, `Asleep`, `Frozen`.
Item, ability, nature, move, learnset and Mega Stone/form are checked against the current Champions
dex. Unknown or illegal stated facts fail before calculation. A source without known SP requires an
explicit spread.

`field` accepts `weather:rain|sun|sand|snow`, `terrain:electric|grassy|psychic|misty`,
boolean `reflect`, `light_screen`, `aurora_veil`, and `helping_hand`. The exact applied field and
`conditions` are returned; weather is not inferred from a setter's ability. The single encounter
uses the calculator's on-entry stat changes. The result retains all damage rolls, HP,
recovery-aware `ko_chance`, mechanical caveats and source coverage. A KO is a conditional calculator
fact, not a win rate. Session accepts `{"op":"scenario","file":{...}}` and saves the same full
result with per-op `out`.

## 14. SP threshold

`team.py threshold threshold.json [--out result.json]` extends the scenario object from §13:

```json
{"attacker":"Garchomp","defender":"Hippowdon","move":"Earthquake","format":"single","attacker_set":{"item":"Life Orb","sps":{"atk":0,"spe":0}},"solve_for":"attacker.atk","goal":"ohko","probability":"guaranteed","compare_items":[null],"maximize":"attacker.spe"}
```

`solve_for` accepts `attacker.atk|def|spa|spe`, `defender.hp|def|spd|spe`, or `defender.bulk`.
`goal` is `ohko`, `2hko`, `survive`, `survive_2`, or `outspeed`. KO goals solve the attacker's
relevant damage stat (`attacker.def` for Body Press); Foul Play's target-Attack dependency is
explicitly unsupported. Survival goals solve the defender; outspeed solves either side's Speed.
Psyshock, Psystrike and Secret Sword direct the `defender.bulk` search to physical Defense.
Speed questions may omit `move`. `probability` is `guaranteed` (default) or `any`; outspeed is
deterministic. `speed_field` accepts boolean `attacker_tailwind`, `defender_tailwind`, `trick_room`.
Weather and terrain come from the inherited `field` and are passed to both damage and Speed calculations.

The solved stat ranges from 0 to the current rule's per-stat cap. All other SP stay fixed, and every
evaluated allocation must satisfy the total budget. `defender.bulk` searches legal HP + relevant Defense
combinations and minimizes their sum. Nature lanes show the stated nature, a positive nature for the
solved stat, and a neutral nature when these have distinct effects. `compare_items` adds at most three
legal, same-form held items (or `null` for none); a Mega Stone that changes form must be queried as that
form separately. No item or nature is silently adopted as a recommendation.

Each lane returns `min_sp` (or bulk's `min_total_sp`), its witness allocation, the zero/maximum feasible
points, and `closest` when no legal allocation meets the goal. Damage witnesses carry the static roll
percentage, recovery-aware `ko_chance`, qualification flags and `closest_metric_basis`; speed witnesses
carry both final Speeds. Two-hit thresholds require the calculator's recovery-aware verdict rather than
static roll pairs alone. If that verdict is unavailable, the two-hit goal is not certified. A static
one-hit threshold with Sash, Disguise or another caveat is conditional, not an unqualified battle
guarantee. `maximize` may name another SP coordinate on the solved side (for example `attacker.spe`);
`maximized` then gives the greatest legal investment in that coordinate that still meets the goal.
For a Speed secondary coordinate it also returns the resulting final Speed.
The bulk joint search does not take a third maximization coordinate. Every result names its source
build, actual field, SP budget and caveats. Session accepts `{"op":"threshold","file":{...}}`.

## 15. Type and rules facts

`team.py type-matchup [--attack-type TYPE] [--pokemon NAME | --types TYPE [TYPE]]
[--ability NAME] --format json` returns fixed type effectiveness. A lone attacking type lists
single-type defenders. With a defender, `base_multiplier` is the type-only result and `multiplier`
includes a stated ability or the only natural ability. When several natural abilities are possible,
`ability_unspecified` is true; relevant `ability_effects` are conditional and `multiplier` is null
where they differ. Without an attack type, `table` omits neutral entries; `table_basis=type_only`
marks an unspecified ability. A stated ability outside a species' natural abilities is labeled
`ability_assumption=hypothetical_for_this_pokemon`. The result excludes attacker abilities,
move-specific exceptions, resist berries, weather and contact effects. Damage needs the calculator.
Names resolve through the sibling dex; unknown names and malformed type sets return structured errors.

`team.py rules-facts --format json` reads the current season and rule from the meta authority,
registration caps from the reviewed ruleset and pick/Mega limits from selection. Historical seasons
without reviewed registration rules have `rules_reviewed:false`; they do not inherit current caps.
Both commands also accept session operations `type-matchup`/`rules-facts` (underscores accepted).

## 16. Configuration correction suggestions

`team.py suggest-fixes team.json [--context ctx.json] [--out suggestions.json]` is a read-only
JSON helper; session accepts `{op:"suggest-fixes",file:<team-json>,context?:<build-context>}`.
It uses strict dex aliases, exposes their changes under `name_normalization`, and leaves unknown
or misspelled names unresolved. It never rewrites the source or applies a suggestion.

The `team-corrections.v1` output has `input_fingerprint` (24-character content hash of the original
JSON), `applied:false`, original `validation`, `suggestions`, `gaps`, and the verified `item_shortlist`.
Each suggestion names its zero-based `members` and a code:

- `ability_illegal`: every legal ability for the specified host/stone; no first-ability default.
- `mega_item`: the explicitly named Mega's required stone, including current holders blocking it.
- `item_pool`: alternatives for an item outside the dex pool.
- `ineffective_mega_stone`: an advisory about a foreign stone, which is legal to hold but cannot
  activate on this species. It does not make validation invalid.
- `item_clause`: every holder and its options, with `minimum_changes = holders - 1`; no keeper is
  chosen. For three or more holders, changing one member may leave the conflict unresolved.

Options carry `changes:[{path,member_index,field,from,present,to}]`, `available` and, for items,
`blocked_by_members` / `blocked_by_avoid`. Paths address original `pokemon` or `decklist` indices.
Check the original fingerprint and the `from`/`present` preconditions before adopting; a missing
field requires addition rather than replacement. `to:null` means no held item. Item options use
an alphabetical convenience shortlist verified by dex, not the complete pool or a suitability
ranking. Required stones can be shown as unavailable until another holder or a context ban is
resolved. Explicit Mega and base+own-stone registrations are preserved: redesigning them into
ordinary forms belongs to the host. Missing facts produce gaps or an unavailable query, never
invented options. Other validation failures stay in `validation` and need separate attention.

Options on different holders/issues are independent and may collide when combined. The host
chooses changes on a copy and discloses them, then reruns validate and every affected UEP gate;
this output is not a legality certificate, frame acknowledgement or automatic repair receipt.
Successful queries exit 0 even when the original team is invalid; malformed inputs or unavailable
dex return a canonical error with exit 1.

## 17. Coordinated reviews, copy trials and scoped claims

`review team.json [--context ctx.json] [--focus targets.json] [--top-k 8]` returns JSON with
`team`, `context`, `environment`, `requirements`, `legality`, `diagnose`, `matchup`, `selection`
and a content-bound `receipt`. Complete, valid input runs one battery; selection reuses that grid.
`status` is `invalid`, `needs_input`, `unknown`, `ready`, `partial` or `calculation_unavailable`.
Missing moves/ability/nature/SP or an inferred/species-only source set prevents CHECK and selection.
Type facts remain available; unconfirmed moveset gaps and numeric speeds are withheld. Item null
means no item, not a missing field. `validate` separately warns when SP is absent.

`try team.json --changes changes.json` takes a nonempty list of
`{slot:zero-based int,set:{species?,item?,ability?,moves?,nature?,spread?}}`. Slots must be distinct.
Each supplied field replaces its old value, including the entire spread; a different species clears
the previous set. Source files are untouched, proposal provenance is cleared, and partial edits do
not turn an inferred source into a trusted observation. Output contains `candidate_team`, a change
ledger, separately sealed `before`/`after` reviews and `comparison`: neutral structural differences
and per-opponent floors. Floors are comparable only with complete calculations and identical variant
pools. No score, winner, candidate-count limit or iteration limit is imposed.

`focus` is `{opponents:[species],members:[species|zero-based slot]}`. Names resolve through dex.
Named opponents supplement top-K without fabricated ranks. `targets` maps members to exact slots
and opponents to report coordinates; missing/ambiguous targets remain explicit. The compatibility
entry `diagnose --focus targets.json --format json` returns this coordinated review contract and
requires `aspect:all`. Without focus, diagnose keeps its existing structural-only behavior.

The full report's `evidence_index` contains `{ref,fact}` with three reference scopes:

- `team_floor`: `{scope,opponent}`; the whole qualified observed-floor row.
- `opponent_variant`: `{scope,opponent,variant_id}`; one retained configuration, its result and coverage.
- `member_cell`: `{scope,opponent,variant_id,member_slot}`; one member's full cell and actual form.

`review-claims claims.json` accepts `{report:<full review>,claims:[{claim:str,ref:<exact ref>,
expect:<complete fact>}]}`. It checks the full receipt, binds each exact scope and reruns one battery
for all claims. Missing/stale facts or a changed environment fail. A member result cannot supply a
team-floor expectation. The claim text itself is not semantically certified. Trial `before` or
`after` can be supplied as the report. Summary reports are refused; `--out` and session references
preserve full reports from the original run. Summary retains evidence refs but omits repeated facts.

Default exit 0 lets incomplete reviews feed explicit repairs. `--fail-on-verdict` returns 1 for a
non-ready review/trial or failed claim check. Incomplete reports cannot certify claims. These helpers
do not replace context-audit or the proposal slate/checkpoint/answer-audit chain.

## 18. Replacement trials and battle scope

`fill team.json --context need.json --slot N --format json` removes the existing zero-based slot
before retrieval, releasing its species and held item. The other members and context constraints
remain in force. Every returned `candidates[].builds[]` is a complete observed joint configuration
with `team_member`, `changes:[{slot,set}]`, `structure_diff`, `legality`, and actual `set_speed`.
The concrete set must meet all requested needs. Missing or meta-filled SP, retained/avoided items,
and invalid whole-team replacements are excluded; `trial_scope.omitted` counts reasons. A structural
difference is not a calculated improvement. Select a build explicitly and pass its `changes` to
`try`; retrieval does not calculate a battery or choose a replacement. Without `--slot`, the
existing fill contract is unchanged. Session uses `slot:N`.

`review`, `try`, `matchup`, and `select` accept `--opponents opponents.json` and
`--conditions field.json` (inline `opponents` / `conditions` in session). Opponents use team-json
with 1–6 members and an optional matching format. Each member is either species-only, using retained
observed configurations, or a complete legal set with moves, ability, nature and explicit SP.
An omitted/empty/null item on a complete set means no held item. Partial set overrides are rejected;
explicit item alone, including null, is a partial set. A complete set replaces the species' observed
variants with `stated:<content-hash>`. Its `coverage:1` describes this scenario, not sample usage.
The specified roster replaces usage top-K; full matchup includes exact `opponent_sets` for binding.

Conditions accept only `weather:rain|sun|sand|snow`, `terrain:electric|grassy|psychic|misty`, and
boolean `trick_room`. Weather/terrain names are case-insensitive; output uses Title Case and can
be passed directly to another CLI, session or typed MCP call. Unknown fields or incorrect types fail. Damage is recalculated in both
directions with the stated weather/terrain. Numeric `speed.faster` keeps its direction;
`speed.acts_first` includes trick_room at equal priority and feeds CHECK. Speed ties stay ties.
This is a static pair/turn-budget model with disclosed limitations, not a full battle simulation.
No alternate field-specific opponent cache is created. Select automatically enables CHECK for a
specified roster. With non-neutral conditions, neutral-field SP jump suggestions are withheld.

## 19. Member-cell tuning

`tune cell-spec.json --from-cell [--out result.json]` emits JSON. Session accepts
`{op:"tune",from_cell:true,file:<cell-spec>}`. The spec is:

```json
{"report":"<full sealed review object>","ref":{"scope":"member_cell","opponent":"Dragonite","variant_id":"<exact id>","member_slot":0},"goal":"outspeed","solve_for":"attacker.spe","compare_items":["Choice Scarf"],"maximize":"attacker.hp"}
```

`report` is an object, not the illustrative string above; copy `ref` from its `evidence_index`.
The source report must pass receipt verification and the cell must reproduce under current data.
Both exact sets, the member's actual battle form/ability, the damaging move in the requested
direction and weather/terrain/trick_room are bound before calling threshold (§14). No ordinary-form
substitution is allowed. `solve_for` must address the member: attacker for attack/outspeed, defender
for survive/survive_2. `goal`, `probability`, `compare_items`, `maximize` follow §14.

Output contains `source_check`, `benchmark`, `bound_sets`, full `threshold` lanes and `trials`.
Unreachable lanes preserve `closest` facts, including `damage.min_percent/max_percent` where
damage is available. Each reachable at_min/maximized point offers explicit nature/item/spread
`changes`, whole-team `legality`, and `available`; a conflicting item can make a point unavailable.
Unchanged points need no trial. No original set is mutated, no lane is chosen, and meeting one
benchmark does not establish whole-team improvement. Pass an available trial to `try`, inspect
both reviews, and finish the applicable proposal UEP gates.
