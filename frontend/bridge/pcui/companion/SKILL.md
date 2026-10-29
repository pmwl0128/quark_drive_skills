---
name: pokemon-champions-ui-session
description: Work on a Quark Drive Console (local Pokémon Champions UI) session — read its request, format, attached team and artifacts through the pcui CLI, run the normal UEP team-building flow with each gate's artifact submitted as it runs, and wait for the reader's answers in the panel so the local web page shows progress live. Trigger when the user asks to handle a Quark Drive local session (including an exact session id), continue one after deciding or answering in the panel, or wants their work shown in the local Console. Chinese examples include "处理 Quark Drive 本地会话 <id>", "继续处理会话 <id>", "处理当前 Pokémon UI 会话", "看看 UI 会话的决策". English examples include "work on Quark Drive local session <id>", "continue session <id>", "work on the current Pokémon UI session". Japanese examples include "Quark Driveのローカルセッション <id> を処理して", "セッション <id> を続けて". Requires the pokemon-champions-team skill (and its sibling data skills) — this skill only adds the session I/O, never replaces the UEP discipline.
---

# Quark Drive Console Session

The local web panel (`pcui serve`, shown as Quark Drive Console) and you collaborate through ONE
shared session store: the panel is the session's eyes and hands, you are the decision participant.
This skill adds exactly one thing to your normal workflow — session I/O through `pcui`. The
team-building discipline itself is unchanged: follow `pokemon-champions-team`'s assisted workflow
(UEP) exactly as you always do.

## CLI

The pcui command for this machine (works from any directory): `{{PCUI_CMD}}`
(bridge directory: `{{BRIDGE_DIR}}`)

```
{{PCUI_CMD}} session show <S>                           # meta (intent, format, team) + heads + ledger
{{PCUI_CMD}} run <op> --session <S> [--file f.json] [--context ctx.json] [--args '<json>'] [--out out.json]
                                                        # run one team operator in the daemon AND
                                                        # submit its gate artifacts to the session
{{PCUI_CMD}} session wait <S> [--for decision|intake-answer] [--timeout 1800]
                                                        # block until the reader answers in the panel;
                                                        # prints that artifact (rc 3 on timeout)
{{PCUI_CMD}} artifact get --session <S> --kind <kind>    # latest artifact of that kind (verbatim)
{{PCUI_CMD}} artifact put --session <S> --kind <kind> --revision <R> --file out.json
                                                        # for what no operator produces; R is the
                                                        # session revision read BEFORE doing the work
{{PCUI_CMD}} session create --intent "<request>" [--format double] [--kind build] [--team team.json]
{{PCUI_CMD}} open --no-browser [--session <S>]          # a fresh one-time link to the panel
```

**MCP instead of the CLI**: when the `quark-drive` MCP tools are available (`{{PCUI_CMD}} mcp`;
`{{PCUI_CMD}} mcp --print-config` shows how the user registers it), use them — same daemon, same
semantics: `session_list`, `session_show`, `session_create`, `artifact_get`, `artifact_put` (pass the
`revision` you read; a conflict comes back as is, so re-read and decide), `run_operator` (with
`session` and `output_path`, exactly like `run --session --out`), `session_wait` (returns `status:
timeout` to call again) and `open_link`. Everything below applies to both.

Typed deterministic tools also expose `team_review`, `team_try`, `review_claims`, `slate_init`,
`team_fill`, `team_tune_cell`, `team_localize`, `team_counters`, `team_ko_scan`, `team_speed_ladder`,
`team_scenario` and `team_threshold`. Use their schemas instead of guessing generic arguments.
File inputs are inline JSON, never local paths. `output_path` on review/try saves the full report
from that run; Console receives a display summary. Copy the saved full report into subsequent
claim/tuning inputs. Typed tools use the same daemon session and UEP gates as `run_operator`.

`inference_prior` and `inference_init/add/status/remove/example/import_web/schema` expose portable
calculator cases. Every call supplies the current case and returns new JSON; there is no hidden
case state, automatic team write or confirmed opponent set. Prior/observations are inline objects;
keep frozen source evidence and the calculator's uncertainty. These query tools do not append a
session gate. A surviving example is a hypothesis, not a recommendation or factual identification.

Canceling `session_wait` stops that wait. Canceling a `run_operator` reply does not retract work
already sent to the daemon: it may still complete and commit. Read `session_show` before retrying.

**Always pass the exact session id on every command.** `session use` is a shared, human-only CLI
convenience; another assistant or terminal can change it. Do not use it or rely on environment
variables persisting between agent shell calls. Give `run` an `--out` file whenever the
output may be large (frame, slate-evaluate, diagnose): stdout then carries a one-line summary that
lists the artifacts it submitted, and the file holds the FULL result body, directly consumable by
the next gate (without an `{op, rc, result}` envelope). A failed output-file save may follow a
successful gate commit: read the returned completion/artifact details before deciding to rerun.

## Gate artifacts: `run --session` submits them

Run every UEP gate operator through `run <op> --session <S>`. The daemon then submits, in ledger order:

| op | submitted kinds |
|---|---|
| `context-audit` (with `--context`) | `context` (the context it read), `audit` |
| `frame` | `frame` |
| `slate-evaluate` | `slate` — the panel gets the summary view; YOUR output stays full |
| `checkpoint` | `checkpoint` |
| `answer-audit` (with `--file draft.json`) | `draft` (the draft it read), `answer-audit` |
| `intake` with `"next": true` | `intake` (the batch the panel shows as a form) |
| `tune` | `tune-result` |
| `diagnose` (with `--file team.json`) | `team` (the team it read), `diagnose` — the panel shows the report |
| `review` / `diagnose --focus` | `review` display summary; preserve full result for evidence |
| `try` | `trial` display summary; original team stays unchanged |
| `review-claims` | `review-claims` recomputation result |
| `slate-init` | `slate-input` candidate transport; does not certify delivery |

A consecutive rerun of the same gate adds nothing. An intervening draft, answer or other gate makes
it a new ledger application even when the result bytes match. Use `artifact put` only for content no operator
produced. Everything you submit is shown live; the panel also shows which operator is running.

## Workflow

1. **Find the session**: a Console handoff names an exact session id — use that id, not the newest,
   and include it explicitly on every call. `session show <S>` gives `meta.intent` (the request typed in the
   panel), `heads` (latest artifact per kind) and the ledger. New requests may include `meta.format`,
   `meta.requestKind`, `meta.team` (the canonical attached team snapshot), `meta.teamName` and
   `meta.environment`. Read these before intake; use the attached builds for rework/diagnosis/tuning,
   preserving their completeness and the user's constraints. Old intent-only sessions remain valid.
   The task kind is a hint, not permission to skip the normal UEP gates or silently change the team.
2. **Read what exists**: `artifact get` every kind in `heads` that matters — `context`, `decision`
   (the reader's checkpoint answer: `chooseCandidate` + `candidateIndex` into your slate's teams,
   `requestChanges` + note, or `proceed`) and `intake-answer` (below). A handoff that says the reader
   decided or answered in the panel means: read that artifact first and continue from it. A handoff
   that says review failed means: read `answer-audit`, fix what it names, and run answer-audit again.
3. **Work the UEP flow normally** (context-audit → grounding → frame → assemble → slate-evaluate →
   checkpoint → answer-audit), with each gate operator run as `run <op> --session <S>`. Batch related
   lookups (ranking/landscape/repset/validate) into one `session` spec instead of many shell-outs;
   fall back to a raw `team.py` only when the daemon is down, then `artifact put` the gate outputs.
4. **Ask through the panel when the user is there**: run `intake --next` as
   `run intake --session <S> --args '{"next": true, "format": "<f>", "answered": [...]}' --context ctx.json`,
   say in one line that the questions are on the panel, then `session wait <S> --for intake-answer`.
   The reader may still answer in conversation instead; either is a user answer. An `intake-answer`
   is `{answers: [{id, option?, text?, skipped?}], note?}`: apply each chosen option's `maps_to`
   exactly as for a numbered reply, echo a typed `text` back before committing it, and add every
   answered id to `--answered` for the next batch.
5. **Checkpoint pause**: when the checkpoint output says `pause:true` (and the context did not
   encode `direct_final`/`skip_checkpoint`), tell the user to decide in the panel (give the
   `open --no-browser --session <S>` link if they may not have it open), then
   `session wait <S> --for decision`. Hosts that can run a command in the background (Claude Code) should
   do so and continue when it completes; otherwise run it in the foreground. On a timeout (rc 3), stop
   the turn; next time, read `decision` first and continue from it.
6. **Draft prose: narrate in the USER'S language, but name every ENTITY by English
   canonical.** In `tradeoffs`, `assumptions[].note`, `confidence_notes`,
   `convergence_rationale`, `context_summary`: species, moves, items and abilities are
   written EXACTLY as they appear in operator output ("Whimsicott", "Last Respects") —
   never hand-translated. The panel localizes canonical names with dex authority at render time.
7. **Artifact size**: the daemon rejects a body over ~2 MiB on `artifact put`. Only `slate` risks that,
   and `run slate-evaluate --session <S>` already submits the summary view. Keep the FULL slate output for
   your own reasoning and for `answer-audit` (its claim recompute re-hashes the untrimmed
   `matchup_risk`) — pass the full output file as answer-audit's `slate_output`.
8. **Tuning**: SP adjustment intent becomes `build-context.benchmarks[]` and `run tune --session <S>
   --context ctx.json --file team.json` per the team skill; the panel renders its cards. Sessions from
   older panels may carry a `tune-request` (`{note, member?, member_set?, team?, form_benchmarks?}`);
   translate its note the same way and keep any `form_benchmarks` the user already ran.
9. **Conflicts**: a gate commits all its artifacts against the revision it read before running.
   A 409 means the inputs are stale or another run is active: re-read the session and reader's
   answers, decide what is still applicable, then rerun. Never blindly repeat an old write.
   Both MCP `artifact_put` and CLI `artifact put --revision <R>` require the revision read before
   doing the work. On conflict, review the newer artifacts and revise your output as needed;
   do not merely fetch a newer revision and retry the old output. The CLI never chooses a baseline.

When the user started in conversation rather than the panel and wants to follow along there, create
the session yourself (`session create --intent … [--team team.json]`), keep its id explicit, and hand the
user the `open --no-browser --session <S>` link.
