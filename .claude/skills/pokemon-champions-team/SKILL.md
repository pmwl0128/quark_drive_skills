---
name: pokemon-champions-team
description: Pokémon Champions single/double team building, legality validation, and diagnostics. Use for producing, completing, changing, reviewing, or evaluating a team; do not trigger from isolated words like core/style/include. Examples include "帮我组一队", "配一套双打", "围绕X做一队", "这队怎么改", "这队怎么选"; "build me a team", "team around X", "rework this team", "review/evaluate my team"; "チームを組んで", "ダブルの構築を作って", "X入りで組んで", "この構築を直して", "選出を見て". The mandatory UEP flow uses context-audit, intake, frame, slate-evaluate, checkpoint, and answer-audit receipt gates before presenting build results. Also use to parse or validate teams, diagnose coverage/speed/role gaps, analyze 6v6 selection, compare matchups, tune SP, retrieve real-team evidence, scan metagame OHKO/2HKO tables, compare metagame speed lines (环境速度线 / speed ladder / 環境の素早さ), query type effectiveness and current rules, or query observed answers to a Pokémon (谁能应对X / who counters X / Xへの対策).
---

# Pokémon Champions Team

Build, validate, diagnose, tune, and review Pokémon Champions teams. This skill is an orchestrator:
its scripts emit deterministic facts, legality verdicts, and auditable evidence; the model makes and
explains strategic trade-offs. Never invent a composite team-strength score or an objective "best"
team.

Use the sibling skills as factual authorities:

- `$pokemon-champions-dex`: names, roster, forms, types, stats, abilities, learnsets, and items.
- `$pokemon-champions-meta`: current/historical usage, common configurations, partners and competitive
  Top Meta (`meta_query.py topmeta`; high-level wording uses `--scope high`).
- `$ncp-damage-calculator`: exact damage, KO/survival, and speed calculations.

## Task routing

Read `references/assisted_workflow.md` for every whole-team request. It owns the UEP gates and
evidence requirements; the table below selects the applicable path.

| Task | Entry and evidence |
|---|---|
| New build or complete a partial team | `context-audit` → `intake --next` (until done; re-audit changed context) → ranking and joint evidence → `frame` → model candidates / `slate-init` → `slate-evaluate` → `checkpoint` → `draft-init` / `answer-audit` |
| Diagnose an existing team | `context-audit` → `review`; one completeness-gated battery supplies diagnosis, selection and matchup facts. `review-claims` verifies scoped claims. No new-build intake/frame for a read-only diagnosis. |
| Rework, tune, or evaluate candidates | Audit context, diagnose the original when present, ground changes in relevant joint evidence, and run `tune` for explicit adjustment benchmarks. Slate the resulting proposals and audit the answer. A new-build frame is required only when building/completing a team. |
| Single damage/speed fact | Use the sibling calculator; no whole-team build flow. |
| Type effectiveness or current rule fact | `type-matchup` and `rules-facts` return fixed type-chart and reviewed rule facts. These factual queries do not enter the whole-team UEP flow. |
| Speed against the metagame | `speed-ladder <species> --game-format single\|double`; optional stated set and conditions. This factual query does not enter the whole-team UEP flow. |
| Who answers a Pokémon | `counters <species> --game-format single\|double --top-n 30`; retained build facts, no whole-team UEP. Keep conditional C0 blockers and missing evidence in the answer. |
| Metagame OHKO/2HKO table | `ko-scan <species> --game-format single\|double`; both directions by default, explicit reference build and qualification groups. Use the calculator for a stated set or one exact pair. |
| Stated scenario on common builds | `scenario scenario.json`; both combatants use exact-form retained builds with explicit set and field overrides. Read its source and conditions before citing a KO. |
| SP threshold on common builds | `threshold threshold.json`; minimum legal SP for a stated KO, survival or speed goal, with nature/item lanes, closest feasible result and optional secondary SP maximization. |
| Retrieve observed evidence or Top Meta | `repset`, `observed`, `search` / `show`, `landscape`, `topmeta`; keep samples and in-game usage distinct. |

Normalize user names through the dex and construct the JSON inputs yourself. Read
`references/schema.md` §1 for authored-set completeness, Mega representation and canonical SP keys.
Use actual battle-form names in tune benchmarks, replacement selectors and frame deviations:
`Mega Charizard X` selects a Charizard holding Charizardite X; `Charizard` selects its ordinary form.
Nested names resolve through the public dex; fuzzy corrections are disclosed and unknown names
must be fixed before running the operation. Original inputs remain unchanged for receipt binding.
Save full gate outputs unchanged: receipts bind the context, frame and candidates. Never present an
invalid, eliminated or unaudited proposal. `checkpoint` normally lets candidate comparisons proceed;
it asks for an intermediate choice when requested (`checkpoint_required:true`) or when modal
Mega-registration coverage needs a repair or an explicit posture. Missing benchmarks are disclosed,
not asked for automatically.

`slate-init` transports explicit candidates, selected frame primary sets, bindings and resolved
intake IDs into a directly usable slate. `draft-init` fills derivable onboarding facts and offers
receipt-bound `claim_candidates`; neither helper chooses the team or writes its strategic rationale.
The complete input/round-trip example is in `references/schema.md` §8.

Use `build-context.goals:[{kind:answer|include,species:...}]` for named opponent/member targets.
Slate includes answer targets outside its top-K and returns receipt-bound `comparison` facts:
CHECK floors, holes, candidate/core overlaps and library overlap, without ranking candidates.
Read coverage and incomplete-calculation qualifications. If cores overlap over half or cannot be
compared, fill `draft.exploration.reason`; library adoption also discloses whether other structures
were considered. Team matchup claims use `team_claims` with complete saved report rows.
Use `localize team.json` for official trilingual member/set names. Optional
`draft.text_check:{lang:zh|ja|en,text:...}` checks the final text's known names, member presence,
percentage claims and writing script; it does not certify prose semantics. See schema §8.

For alternatives, use frame's `--draw N --seed INT --seen-teams shown.json` or
`observed --order diversity`. `fill` accepts `need.answer` to retrieve complete observed builds
answering named opponents, with static CHECK conditions; the off-meta view also includes
library-backed long-tail species. See `references/schema.md` §8 for evidence and identity limits.

The explicit keyword `建队流程` and its aliases enter the complete guided path unconditionally.

## Contract Discovery

Do not guess payload shapes. The CLI exposes its commands and structured contracts:

```bash
python scripts/team.py schema
python scripts/team.py vocab
```

`schema` describes command inputs, `team-json`, context specs, session specs, and answer drafts.
`vocab` describes enumerated intent fields and which fields are enforced by scripts versus interpreted
by the orchestration layer. Read `references/schema.md` for durable domain and cache contracts.

## Core Commands

An explicitly supplied ten-character public team code is a team input. Resolve it once with
`python scripts/team.py code <code> --format json`, then use its `team` in the existing workflow.
Never guess or enumerate codes, fetch while typing, or retry a refusal automatically. `--cache-only`
makes no network request. A query does not establish format, season or rule: preserve nulls until
the caller supplies the context. Query failure must not become a fabricated or species-only team.
Network setup, conservative shared limits and error contracts: `references/team-codes.md`.
The resolved team still goes through the normal validation/build/diagnostic gates.

Parse and validate:

```bash
python scripts/team.py parse team.txt --game-format double --format json
python scripts/team.py validate team.json --context ctx.json --format json
python scripts/team.py suggest-fixes team.json --context ctx.json --out suggestions.json
python scripts/team.py diagnose team.json --aspect all --format json
```

`suggest-fixes` returns explicit ability/item choices and every side of an Item Clause conflict,
without editing the team. Choose and disclose changes on a copy, then rerun validation and the
affected UEP gates. Item options are a verified convenience shortlist, not a suitability ranking;
occupied/avoided options are marked unavailable. See `references/schema.md` §16.

Selection, matchup, and tuning:

```bash
python scripts/team.py review team.json --context ctx.json --out review-full.json
python scripts/team.py try team.json --changes changes.json --out trial-full.json
python scripts/team.py diagnose team.json --focus targets.json --format json --out focused-review.json
python scripts/team.py review-claims claims.json --fail-on-verdict
python scripts/team.py select team.json --context ctx.json --format json
python scripts/team.py matchup team.json --top-k 20 --context ctx.json --format json
python scripts/team.py matchup actual-sets.json --top-k 60 --matchup-view summary --format json --out matchup-full.json
python scripts/team.py tune team.json --context ctx.json --format json
python scripts/team.py fill team.json --context need.json --slot 1 --format json --out replacements.json
python scripts/team.py review team.json --opponents opponents.json --conditions field.json --out scoped-review.json
python scripts/team.py select team.json --opponents opponents.json --conditions field.json --format json
python scripts/team.py tune cell-spec.json --from-cell --out cell-tuning.json
python scripts/team.py type-matchup --attack-type 地面 --pokemon Rotom-Wash --format json
python scripts/team.py type-matchup --types Steel Fairy --ability Levitate --format json
python scripts/team.py rules-facts --format json
python scripts/team.py speed-ladder "Mega Gengar" --game-format double --limit 20 --format json
python scripts/team.py speed-ladder Garchomp --game-format double --set speed-set.json --conditions speed-field.json --opponent-ranges --format json --out speeds.json
python scripts/team.py counters Garchomp --game-format single --top-n 30 --view summary --format json --out counters-full.json
python scripts/team.py ko-scan "Mega Garchomp Z" --game-format double --top-n 30 --view summary --format json --out ko-full.json
python scripts/team.py scenario scenario.json --out scenario-result.json
python scripts/team.py threshold threshold.json --out threshold-result.json
```

`review` and `try` default to compact stdout while `--out` and session references keep the full
receipt-bound result. Missing moves, ability, nature, SP or trusted source evidence returns
`needs_input` without team CHECK/selection claims. `try` accepts explicit `{slot,set}` changes;
it edits a copy, replaces whole fields, and clears the old set when species changes. Read the
structural differences and both qualified floors; the tool does not choose a winner.
`--focus` maps `{members:[name|slot],opponents:[name]}` to slots and evidence coordinates, adding
named opponents beyond top-K. For claims, copy a full report's `evidence_index` reference and
complete fact into `review-claims`; it recomputes team floors, opponent variants and member cells
without mixing their scopes. A successful check does not replace proposal UEP gates. See schema §17.

`fill --slot N` releases that zero-based member's species and item before retrieval. It returns
complete observed replacement configurations, excludes retained/avoided items, and attaches
`changes` ready for `try`, with structural differences and actual set Speed. Empty results disclose
omitted evidence; do not invent missing SP or treat structural changes as matchup improvements.

`--opponents` supplies 1–6 species-only entries (observed variants) or complete legal sets (stated
scenarios); partial set overrides are rejected. The roster replaces usage top-K. `--conditions`
accepts weather, terrain and boolean trick_room. Damage uses the field in both directions, while
trick_room changes equal-priority action order. This remains a static reference. See schema §18.

For `tune --from-cell`, construct `{report:<full review>,ref:<member_cell ref>,goal,solve_for,
compare_items?,maximize?,probability?}`. The tool verifies and recomputes the cell, binds exact
sets/actual forms and field, then returns threshold lanes and explicit trial `changes`. Read each
trial's `available` and qualifications before passing it to `try`. Unreachable lanes keep their
closest percentages; no lane is selected automatically. See schema §19.

`speed-ladder` reads the complete current-rule team pool. Its reference is the most frequent known
speed configuration of each actual form, with exact-form meta marginals as a disclosed fallback.
Pass a stated nature/SP/item/ability through `--set`; an omitted item means no held item, except a
Mega's required stone. Read `schema.speed_ladder_spec` or `references/schema.md` §10 for field inputs,
sample thresholds and limitations. “Reliably/usually” describe the observed sample, not all players.

`counters` reads the current opponent cache and derives one floor per concrete candidate build.
`--top-n` selects candidate usage ranks (1–60), not a number of recommendations. C2/C1 rows cover
all retained builds of the exact target form; `conditional_rows` keep their C0 floor and name every
answered build and blocker. No complete floor is claimed for `incomplete_rows`. This is a low-confidence
static reference, not a win rate or a universal counter. See `references/schema.md` §11.

`ko-scan` uses the highest-coverage retained build of the exact named form; an exact variant id selects
another build. `--direction attacking|defending|both` defaults to both. Read the reference, direction
and **entire qualification group** before stating a KO: `calculator_guaranteed` does not override
Sash, Disguise, variable hit counts or other caveats. Missing records are not survival evidence.
The KO verdict comes from cached `ko_chance`, not static percentage buckets. See §12 of the schema.

`scenario` reads a JSON object with `attacker`, `defender`, `move`, `format`, optional `attacker_set`,
`defender_set` and `field`. It keeps the source build visible, validates stated overrides against the
Champions dex, and calculates one stated encounter. The current cache covers retained top-ranked builds;
an absent form is an explicit miss. See `references/schema.md` §13 for the fields and limits.

`threshold` extends that scenario with `solve_for`, `goal`, and optional `probability`,
`compare_items`, `maximize`, and `speed_field`. Each nature/item lane enforces the 66-SP budget;
unreachable goals return the closest feasible fact. For two-hit goals, the recovery-aware calculator
verdict decides the threshold. Read the selected lane's caveats before stating a battle guarantee.
See `references/schema.md` §14.

Run several operators in one process when they belong to the same request:

```bash
python scripts/team.py session session.json
```

The session spec is a JSON array of operations such as `validate`, `diagnose`, `select`, `matchup`,
and `tune`. Use the shape emitted by `schema`. It accepts inline JSON inputs and backward
`{"$ref":"/0/result/..."}` pointers to prior full results. `session --spec -` reads the array from stdin;
`--out session-full.json` preserves complete results. See `references/examples.md` for minimal shapes.

## Build Gates

Audit intent and, for new builds, drive intake:

```bash
python scripts/team.py context-audit --context ctx.json --format json
python scripts/team.py intake --onboarding --context ctx.json --format json
python scripts/team.py intake --next --context ctx.json --answered format,purpose --format json
```

Ground and evaluate candidates:

```bash
python scripts/team.py landscape --game-format double --context ctx.json --format json
python scripts/team.py observed --game-format double --context ctx.json --format json
python scripts/team.py frame --game-format double --context ctx.json --audit-receipt audit.json --format json
python scripts/team.py slate-evaluate slate.json --frame-output frame.json --format json
python scripts/team.py checkpoint slate-output.json --slate slate.json --format json
```

Audit the answer:

```bash
python scripts/team.py draft-init --slate slate.json --slate-output slate-output.json --format json
python scripts/team.py answer-audit draft.json --slate slate.json --slate-output slate-output.json --format json
```

For build flows, set `frame_required:true` in the context/slate and preserve matching receipt files.
`slate-evaluate` eliminates invalid or ungrounded candidates and emits the evidence IDs used by the
final answer. `answer-audit` certifies structure and recomputable claims, not strategic quality.
Gates start with `verdict` and `next_step`. Add `--fail-on-verdict` to `context-audit`,
`slate-evaluate`, `checkpoint` or `answer-audit` when process exit status must reflect a negative
verdict; session also supports this flag for its batch. Summary slate output (`--slate-view summary`) is display-only; answer-audit needs the full
saved result. Unsupported command options are rejected instead of ignored.
Use `--view summary --out full.json` for JSON `diagnose`, `select`, `oppmatrix`, `topmeta`, `frame`,
`tune`, `intake`, `matchup` and `slate-evaluate`. The summary discloses omitted evidence; saved files
and session references retain full results from the same computation. CHECK floors, missing coverage
and qualifications remain visible. `topmeta`/`oppmatrix` accept `--summary-limit N` (default 6) for
table display, preserving source order and population ranks. Summary frame/slate cannot certify gates.
`intake --onboarding --view summary` returns only the next numbered batch; carry resolved ids through
`intake --next --answered ...`. Saved artifacts keep original receipt-bound inputs.

## Real-Team Evidence

The bundled sample library is factual evidence, not a leaderboard:

```bash
python scripts/team.py repset Garchomp --game-format double --format json
python scripts/team.py search --game-format double --species Gengar Froslass --format json
python scripts/team.py show <team-id> --game-format double --format json
python scripts/team.py oppmatrix Garchomp --game-format double --vs Charizard --format json
python scripts/team.py topmeta --game-format double --scope season --species Garchomp
```

`topmeta` (Top Meta / 竞技环境 / 上位環境) counts the library on the metagame's own fields and returns
each entry with both its count and the in-game value. Use it for "what do top-level teams run" and
"how does that differ from the in-game data"; name the population and the sample size in the answer,
and never treat agreement with `repset`/`frame` as corroboration — it is the same library. `--scope
high` is the high-performance slice (a top-8 finish in an event of 16+, or a ladder top-100 rank /
2000+ rating): use it for every high-level wording — 高端、高分段、高水平、天梯顶端、上位、比赛、大赛,
high-end, high ladder, top ladder, high-rated, top cut, tournament, 上位帯、上位勢、高レート、大会、入賞.
The primary standalone query entry is the meta skill's `topmeta`; this team command remains available
for existing sessions. Both reuse the same comparison and report `data_granularity:species_aggregate`.
Read `queries[]`: unresolved names and unavailable Mega-only distributions remain explicit, without
substituting a base-species distribution. Approximate names retain their matching metadata.

Use `observed` for constraint-shaped retrieval and `search`/`show` for exact library inspection.
Scope `oppmatrix` to an attacker; the entire matrix requires an explicit `--all` and can exceed
tens of MB. For high-level team structure, use `landscape --game-format double --scope high --format json`
(or `single`). This applies Top Meta's performance criteria to native current-rule joint records.
Report `population.sample_unit` and `sample_count`: these are published library records, not Top Meta's
player-deduplicated units. Unknown achievements are excluded, with no broader-population fallback.
The ordinary landscape remains the default; `--species` narrows cores, not all structural distributions.
`landscape` caches structural facts by source content, implementation, options and handover validity.
Use `--no-cache` to recompute; an unavailable local cache never substitutes for missing source data.
Adopting an observed team is allowed only with the provenance and adoption-review disclosures required
by `answer-audit`. Never silently reproduce a stored team.

## Output And Boundaries

Authored member species, items, abilities, natures and moves accept trilingual dex names. The CLI
normalizes a working copy through the public resolver; every fuzzy correction is disclosed, and
unresolved/ambiguous values remain for strict validation. Original inputs and receipt-bound objects
stay intact.

`search`, `observed`, `repset`, `landscape`, `frame` and slate species constraints match the actual
battle form: a base with its own dex-declared Mega stone
equals that Mega; ordinary forms and Mega X/Y/Z remain distinct. `search` with both species and its
stone is one conjunctive member condition. Unknown form evidence stays unknown beneath boolean
NOT. Read `identity_gaps` and `unresolved_conditions`; zero matches need not prove absence.
`observed` excludes rows with unresolved forms when an avoid constraint cannot be proved, counting
them under `excluded_by_identity`. Its `owned_coverage` is a separate inventory count by base species.
`oppmatrix` name selectors read the cached `run_form`; an exact variant id selects its own build.
Frame core grounding uses the same actual species/ability projection; representative sets preserve
observed registration values in `registration_modes`. An ordinary anchor never auto-selects a Mega
because the library only contains that form. Re-run legacy frame/slate artifacts when a gate reports
`receipt_upgrade_required`.

- Use canonical English names in structured JSON; render localized names in the final user-facing
  response through the dex.
- Pass `--lang zh|ja|en` for human-readable CLI output. JSON remains canonical and language-independent.
- Keep single and double evidence separate. Keep historical regulations explicitly labeled.
- A six-member roster is a preview toolbox: singles selects 3-of-6 and doubles 4-of-6. Do not judge it
  as a flat six-member battle lineup; only zero or one brought Mega option can be active per battle.
- Expose assumptions, confidence, evidence, trade-offs, and omitted alternatives.
- Do not infer strength from usage, sample performance tags, or provenance tier alone.

## References

- `references/assisted_workflow.md`: mandatory routing, guided intake, grounding, gates, and final audit.
- `references/schema.md`: `team-json`, provenance, representative-set/cache, build-context, evidence,
  frame and Top Meta contracts.
- `references/examples.md`: inline sessions, slate/frame bindings, draft fields and saved full results.
