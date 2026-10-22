---
name: pokemon-champions-team
description: Pokémon Champions single/double team building, legality validation, and team diagnostics. Use when the whole request is about producing, completing, changing, reviewing, or evaluating a Pokémon Champions team; do not trigger from isolated generic words like core/style/include. Chinese examples include "帮我组一队", "配一套双打", "带X组一队", "围绕X做一队", "这队怎么改", "这队怎么选". English examples include "build me a team", "make a doubles team", "team around X", "include X in the team", "rework this team", "review/evaluate my team". Japanese examples include "チームを組んで", "ダブルの構築を作って", "X入りで組んで", "Xを軸にした構築", "この構築を直して", "選出を見て". The mandatory UEP flow uses context-audit, intake, frame, slate-evaluate, checkpoint, and answer-audit receipt gates before presenting build results. Also use to parse or validate a team, diagnose coverage/speed/role gaps, analyze 6v6 selection, compare matchups, fine-tune SP spreads, and retrieve factual real-team evidence.
---

# Pokémon Champions Team

Build, validate, diagnose, tune, and review Pokémon Champions teams. This skill is an orchestrator:
its scripts emit deterministic facts, legality verdicts, and auditable evidence; the model makes and
explains strategic trade-offs. Never invent a composite team-strength score or an objective "best"
team.

Use the sibling skills as factual authorities:

- `$pokemon-champions-dex`: names, roster, forms, types, stats, abilities, learnsets, and items.
- `$pokemon-champions-meta`: current or historical usage, common configurations, and partners.
- `$ncp-damage-calculator`: exact damage, KO/survival, and speed calculations.

## Mandatory Protocol

Read `references/assisted_workflow.md` before every build, diagnose, evaluate, rework, or tune request.
It is the authoritative reasoning and routing protocol. The non-negotiable sequence is:

1. Normalize every user-supplied name through the dex, then translate free-form intent into
   `team-json` and `build-context`. The scripts do not parse conversation text.
2. Run `context-audit` before building or changing a team. Resolve blocking/conflicting gaps and
   disclose any safe defaults. Keep its `audit_receipt`.
3. For an open-ended new build, drive `intake --next` until the guided walk reports `done:true`.
   Existing-team analysis does not require onboarding; ask only for blocking/conflicting gaps.
4. Ground a build in current ranking plus real joint evidence (`landscape`, `observed`, `search`, and
   `repset`). Run `frame` with the audit receipt and build on its grounded skeletons. Do not construct
   joint sets by stitching together independent usage marginals.
5. Assemble 2-5 candidates unless the user explicitly requests one. Run `slate-evaluate` with the
   original context, audit receipt, frame output, and frame bindings. Never present an unslated or
   eliminated candidate.
6. Run `checkpoint` after every build-flow slate. Surface a required pause unless the user explicitly
   delegated final convergence or asked not to pause.
7. Run targeted `tune` only when adjustment intent or explicit benchmarks exist. Bare NCP calls answer
   as-is fact questions; they do not replace cliff detection for SP tuning.
8. Initialize the response with `draft-init`, fill its substantive fields, and run `answer-audit` with
   the original slate and saved slate output. Fix violations and present only a passing draft.
9. Validate every team before presenting it. Slate survivors carry `select` facts for the actual
   6-pick-3 (single) / 6-pick-4 (double) object; inspect them before making lineup or Mega-route claims.
   Team-level matchup claims require the full `matchup` table, not hand-picked calculations.

At entry, state the phases that will run and track them with the host's available plan/checklist tool.
If a phase is later dropped, disclose why. The receipt chain is
`context-audit -> frame -> slate-evaluate -> answer-audit`; save every JSON artifact unchanged.

The explicit keyword `建队流程` and its aliases listed in `references/assisted_workflow.md` enter the
full guided flow unconditionally.

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
python scripts/team.py parse team.txt --format json
python scripts/team.py validate team.json --context ctx.json --format json
python scripts/team.py diagnose team.json --aspect all --format json
```

Selection, matchup, and tuning:

```bash
python scripts/team.py select team.json --context ctx.json --format json
python scripts/team.py matchup team.json --top-k 20 --context ctx.json --format json
python scripts/team.py matchup actual-sets.json --top-k 60 --matchup-view summary --format json
python scripts/team.py tune team.json --context ctx.json --format json
```

Run several operators in one process when they belong to the same request:

```bash
python scripts/team.py session session.json
```

The session spec is a JSON array of operations such as `validate`, `diagnose`, `select`, `matchup`,
and `tune`. Use the shape emitted by `schema`.

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

## Real-Team Evidence

The bundled sample library is factual evidence, not a leaderboard:

```bash
python scripts/team.py repset Garchomp --game-format double --format json
python scripts/team.py search --game-format double --species Gengar Froslass --format json
python scripts/team.py show <team-id> --game-format double --format json
python scripts/team.py oppmatrix --game-format double --species Garchomp --vs Charizard --format json
python scripts/team.py topmeta --game-format double --scope season --species Garchomp
```

`topmeta` (Top Meta / 竞技环境 / 上位環境) counts the library on the metagame's own fields and returns
each entry with both its count and the in-game value. Use it for "what do top-level teams run" and
"how does that differ from the in-game data"; name the population and the sample size in the answer,
and never treat agreement with `repset`/`frame` as corroboration — it is the same library.

Use `observed` for constraint-shaped retrieval and `search`/`show` for exact library inspection.
Adopting an observed team is allowed only with the provenance and adoption-review disclosures required
by `answer-audit`. Never silently reproduce a stored team.

## Output And Boundaries

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
