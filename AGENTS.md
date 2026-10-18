# AGENTS.md

Optional project-level instructions for agents using Quark Drive Skills for Pokémon Champions in this
distribution. The skills auto-trigger without this file; keep these rules when copying or adapting it.

## Language And Names

- Detect whether the user is writing in Simplified Chinese, Japanese, or English and answer in that
  language unless the user requests another one.
- Pass `--lang zh|ja|en` when requesting human-readable Markdown from dex, meta, or team CLIs. Prefer
  canonical JSON for orchestration; JSON fields, stat keys, types, and English `name` join keys are
  intentionally language-independent.
- Resolve every Pokémon, move, item, ability, and nature through `pokemon-champions-dex`. In Chinese
  answers render `中文名 (English)` on first use; in Japanese render `日本語名 (English)`; in English
  use the canonical English name. Do not translate names from memory.
- Keep technical terms understandable for the user's language. Explain SP as Champions Stat Points,
  not standard-game EVs.

## Current Environment

- Pokémon Champions rules and metagame data are versioned. Read the current season/rule from skill
  output or `pokemon-champions-meta/data/current.json`; never hardcode an older season.
- Meta data is a shipped snapshot. State its season/rule and `as_of`/update stamp when freshness
  matters. If it is stale, tell the user to update the skill package; do not pretend the snapshot is
  live.
- Call skills before relying on memory. Search the web only when the installed skills lack the fact,
  and distinguish web evidence from shipped skill data.

## Skill Routing

- `pokemon-champions-dex`: canonical battle facts, forms, trilingual names, types, stats, abilities,
  learnsets, items, Mega stones, and reverse search. Examples: `X能学Y吗`, `XはYを覚える？`,
  `can X learn Y?`.
- `pokemon-champions-meta`: rankings, usage distributions, common moves/items/abilities/natures,
  partners, SP spreads, format comparisons, and factual snapshot changes. Never stitch independent
  marginals into an asserted joint set.
- `ncp-damage-calculator`: concrete damage, KO/survival, and speed-order facts. A successful calculation
  is not a legality verdict.
- `pokemon-champions-team`: any request whose whole intent is to build, complete, validate, diagnose,
  tune, change, select from, review, or evaluate a team. Examples: `帮我组一队`, `这队怎么改`,
  `チームを組んで`, `この構築を直して`, `build me a team`, `review my team`.

Pure dex/meta/damage questions do not enter the team workflow. The explicit phrases `建队流程`,
`开始建队`, `执行建队流程`, `team building flow`, and `チーム構築フロー` always enter the full team
workflow.

## Team Skill: Mandatory Workflow

Before handling any build, diagnose, evaluate, rework, selection, or tuning request, read the team
skill's `references/assisted_workflow.md`. Its Universal Evaluation Protocol (UEP) is binding.

1. **Commit to the phases.** State which phases will run and track them with the host's planning tool.
   Do not silently skip grounding, slate evaluation, checkpoint, validation, or answer audit.
2. **Normalize user input.** Users may paste Showdown text, JSON, prose, screenshots, or any named file.
   There is no required source format. Resolve names with the dex, then construct canonical `team-json`
   and `build-context`; team scripts never parse conversation text.
3. **Audit intent first.** Run `team.py context-audit --context ctx.json`. Resolve blocking and conflict
   gaps; apply and disclose safe defaults. Preserve the returned `audit_receipt`.
4. **Use the correct intake route.** For an open-ended new build, drive `team.py intake --next` in
   batches until `done:true`. Existing-team analysis skips onboarding and asks only for blocking or
   conflicting information. If `owned_only:true`, populate the resolved `owned` list.
5. **Ground before assembling.** Consult current meta ranking plus `landscape`, `observed`, `search`,
   and `repset`; use doubles `oppmatrix` where relevant. Run `frame` with the audit receipt. Build on
   real joint evidence, never on independently combined usage marginals, and never silently copy a
   stored team.
6. **Evaluate a slate.** Unless the user explicitly requests one final team, assemble 2-5 structurally
   distinct candidates. Set `frame_required:true`, preserve `frame_bindings`, and run
   `slate-evaluate` with the saved frame output. Never present an unslated or eliminated candidate.
7. **Honor the checkpoint.** Run `checkpoint` after every build slate. Surface `pause:true` unless the
   user already delegated final convergence or explicitly requested no pause.
8. **Use the right calculations.** Team-level meta matchup claims require the full `matchup` top-K
   table. Doubles six-member evaluation requires `select` because the battle selection is 4-of-6.
   Adjustment intent such as "make X survive/outspeed/OHKO Y" requires `team.py tune` with structured
   benchmarks; isolated as-is questions may use the NCP calculator directly.
9. **Validate every presented team.** Run `validate` with context when available. Treat
   `valid/invalid/unknown` distinctly; never certify legality from incomplete data or a successful
   damage calculation.
10. **Audit the final answer.** Initialize with `draft-init`, fill all substantive fields, then run
    `answer-audit` against the original slate and saved slate output. Fix violations and present only a
    passing draft. Passing verifies structure and recomputable claims, not strategic perfection.

The receipt chain is `context-audit -> frame -> slate-evaluate -> answer-audit`. Save the exact JSON
artifacts so later gates can rebind them. Use `team.py session` for multiple operators on one team.

## Local Visual Interface

A companion browser front end turns the skills' facts and calculations into a browsable workspace. It
is optional — the four CLIs fully cover every workflow — and it never replaces the UEP flow the agent
drives. When the user asks to set up, download, or start the local front end (phrases like "帮我下载并启动前端",
"フロントエンドをダウンロードして起動して", "download and start the local UI"), do the full flow yourself instead of
handing the user a command list.

Recommended path — the prebuilt bundle (no Node.js needed):

1. Fetch `Quark_Drive_Web.zip` from the [`data-latest` release](https://github.com/pmwl0128/quark_drive_skills/releases/tag/data-latest).
2. Extract it into the project directory (or a sibling folder the user designates). Verify the extracted folder contains `dist/`, `projection/`, `bridge/`, `skills/`, `requirements-runtime.txt`, and a platform start script.
3. Install the locked runtime deps with `python -m pip install -r requirements-runtime.txt` (Python 3.10+; no Node.js).
4. Launch the platform start script — `start-local.ps1` on Windows, `start-local.sh` on Linux/macOS (mark it executable first). Do not pass `--port`; read `~/.pokemon-champions/pc.env` for `PCUI_LOCAL_PORT` if the user has set one.
5. Surface the full URL printed by the launcher, including its `#bootstrap=...` fragment — a bare `/` is rejected by the local auth check. Tell the user to open that complete URL once. Each link signs in once; for another browser, or after a restart disconnected an open page, run `python <bundle>/bridge/pcui open --no-browser` and surface the new link instead of restarting.
6. When the user wants you to work on sessions created in the local UI, install the companion skill once with `python <bundle>/bridge/pcui install-agent --yes` (add `--target codex` for Codex) — only at the user's request. It teaches the session workflow: a handoff naming a Quark Drive local session id means read that session through `pcui` and submit each UEP gate artifact back so the page shows progress live.

From a source checkout (when the user wants to build or modify the SPA), run
`frontend/build-ui.ps1` / `frontend/build-ui.sh`, then the matching `frontend/start-ui.*`;
this path needs Node.js 22 and `frontend/requirements-ui.txt`.

The local UI is a verification and browsing aid. It does not produce new facts the agent did not already
compute through the skills, and UEP session/artifact state under `~/.pokemon-champions/` is a mirror
of the agent's own receipts — do not present a UI-rendered team as if it came from a source the agent
did not audit. For the online edition, point the user at the URL in the README; it shares no local state.

## Recommendation Boundaries

- Tools emit facts, legality results, evidence, and multiple explicit views. They never produce a
  composite strength score or an objective "best team". The agent may recommend transparently by
  explaining assumptions, trade-offs, confidence, opportunity costs, and alternatives.
- Keep singles and doubles evidence separate. Keep historical regulations labeled and out of the
  current evidence pool unless explicitly requested.
- Performance tags and usage are evidence, not proof of strength. Exact adoption of an observed team
  requires the provenance and adoption-review disclosures enforced by `answer-audit`.
- Quantitative prose claims must cite operator evidence IDs and reproduce the actual damage, survival,
  KO, or speed result. Surface low-confidence and standard-set assumptions.
