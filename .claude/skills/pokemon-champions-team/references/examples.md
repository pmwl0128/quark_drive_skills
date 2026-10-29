# Minimal inputs and dependent sessions

Run commands from the installed skill directory. Paths below are caller-owned files, relative to
the current working directory. Create candidate/context inputs from the user's request first;
the examples demonstrate transport and shapes, not strategic choices.

## Inline inputs and prior results

Save this JSON array as `session.json`, or pass its JSON through stdin with
`python scripts/team.py session --spec - --out session-full.json --fail-on-verdict`:

```json
[
  {"op":"parse","file":"candidate.json","game_format":"double"},
  {"op":"validate","file":{"$ref":"/0/result"},"context":{"format":"double"}}
]
```

`file`, `context`, `audit_receipt`, `frame_output`, `slate`, and `slate_output` accept inline JSON
objects/arrays as well as paths. An inline multiline `file` string is parsed as team text.
References are JSON Pointers into the prior result envelopes: `/0/result/pokemon/0` selects
the first parsed member. Escape `/` as `~1` and `~` as `~0` in a key. Forward/missing references,
and references to failed operations, refuse the dependent operation. Unrelated operations can run;
read every `rc/error`, or use the session-level `--fail-on-verdict`.

## Slate and frame bindings

Assume the earlier operations in this session are 0=`context-audit`, 1=`frame`, and 2=`parse`.
The audited context is exactly `{"format":"double","frame_required":true}`.
After intake/grounding and the model's frame/candidate choice, a slate operation has this shape:

```json
{
  "op":"slate-evaluate",
  "file":{
    "context":{"format":"double","frame_required":true},
    "audit_receipt":{"$ref":"/0/result/audit_receipt"},
    "teams":[{"$ref":"/2/result"}],
    "frame_bindings":[{"frame_id":{"$ref":"/1/result/skeletons/0/frame_id"}}]
  },
  "frame_output":{"$ref":"/1/result"},
  "view":"summary",
  "out":"slate-full.json"
}
```

Skeleton index `0` is illustrative: inspect the frames and choose the applicable one. Add candidates
and aligned bindings as needed. A binding can additionally carry `deviations:[{species,reason}]`,
`off_meta:[species]`, or `mega_deviation` when the corresponding evidence obligation applies
(`schema.md` §8). Use the final audited context, including actual constraints and answered intent.

One computation saves the full result to `out` and renders its summary to stdout. Later session
references still read the full result. The aggregate `--out` likewise saves complete operation
results, including when stdout contains compact per-operation views. Output destinations must be
distinct and must not overwrite input files.

## Draft shape and final audit

Use `draft-init` with the original slate and its full result to obtain the actual required fields:

```bash
python scripts/team.py draft-init --slate slate.json --slate-output slate-full.json --out draft-scaffold.json
```

The helper returns `{kind,draft,notes}`. Fill `draft`, then pass that object as the answer-audit
`file`. Its core fields look like this (text values below describe what to fill):

```json
{
  "environment":{"season":"copy from the evaluated environment","rule":"copy from the evaluated environment"},
  "context_summary":"State the user's actual constraints.",
  "single_team_requested":true,
  "assumptions":[],
  "confidence_notes":["Name the candidate and describe any limited evidence."],
  "tuning_summary":{"status":"not_run","reason":"No tuning benchmarks were requested."},
  "recommended":[{"slate_index":0,"team":{"$ref":"/2/result"},"tradeoffs":["State an accepted trade-off."]}],
  "convergence_rationale":{"0":{"worst_matchup":"Name the evidenced limitation.","accepted_by_constraint":"Explain why it is acceptable.","opportunity_cost":"Explain what the choice gives up."}},
  "claims":[],
  "slate_receipt":{"$ref":"/3/result/slate_receipt"}
}
```

The references above apply only when the draft is inline in that session. A plain draft file must
contain the resolved values. Copy the exact slated team; do not normalize or edit it after slating.
Set the single-team flag from the request, or explain omitted alternatives. For builds, fill the
additional onboarding/frame/Mega/provenance fields emitted by `draft-init` when applicable.
Populate quantitative claims only from actual evidence IDs and values; `expect` is an object such
as `{"ko_guaranteed":1}` or `{"faster":"member"}`, not free text.

```bash
python scripts/team.py answer-audit draft.json --slate slate.json --slate-output slate-full.json --fail-on-verdict
```

Summary stdout is display-only. Always give answer-audit the saved full slate result.

## Compact fact views

```bash
python scripts/team.py diagnose team.json --with-check --top-k 20 --format json --view summary --out diagnosis-full.json
python scripts/team.py select team.json --with-check --format json --view summary --out selection-full.json
python scripts/team.py topmeta --game-format double --scope high --view summary --summary-limit 10 --out high-full.json
python scripts/team.py oppmatrix Garchomp --game-format double --as-checks --format json --view summary --out checks-full.json
python scripts/team.py intake --game-format double --onboarding --view summary
python scripts/team.py landscape --game-format double --format json
```

In a session, use `view:"summary"` and optional `summary_limit:10` for topmeta/oppmatrix.
Read `projection.omitted` before making claims about the displayed subset. A full file remains
available without recomputing. Landscape reuses only matching structural facts; add `--no-cache`
to compare a fresh calculation. Cache hits do not change sample scope or confidence.
