# Assisted team building — Universal Evaluation Protocol (UEP)

Use this protocol for whole-team build, completion, diagnosis, evaluation, rework and tuning.
The tools return facts and certify deliverability; the model chooses structures, candidate count,
trade-offs and when to converge. Do not turn a prevalence table into a strength score.

At entry, state the applicable phases and track them with the host's checklist when available.
Use the project's current data rather than a remembered environment. Explain strategic trade-offs
to the user; do not ask them to certify the model's structural or strength assumptions.

## Route the request

| Request | Required evidence and gates |
|---|---|
| New build or complete a partial team | Audit intent, complete intake, ground structures and joint sets, run frame, assemble candidates, slate, checkpoint, and audit the answer. |
| Read-only diagnosis or selection | Audit intent; use review for completeness-gated validation, diagnosis, selection and matchup. Verify scoped claims with review-claims. Finish with known facts; do not invent candidates, intake or frame. |
| Rework/tune an existing team | Audit intent and diagnose the original before changing it. Ground affected sets, run tune for adjustment benchmarks, slate the proposals and audit the answer. No new-build intake/frame unless completing or rebuilding a team. |
| Evaluate supplied candidates | Audit their context, slate them and audit any recommendations. No invented new-build onboarding. |
| Isolated dex/meta/damage/speed fact | Use the relevant sibling skill. No whole-team build chain. |

An explicit `建队流程` trigger enters the complete guided build path unconditionally.
Its authoritative aliases are 开始建队 / 执行建队流程, English "team building flow",
and Japanese 「チーム構築フロー」. Supplied prose pre-answers the corresponding dimensions.

Otherwise route by the whole request: producing/completing/changing/reviewing a Champions team
triggers the appropriate row above. An isolated word such as "core", "style", "include" or a field
name does not. Existing-team analysis is a team-skill request, but not automatically a new build.

## Audit intent and ask only for needed input

1. Normalize user names through the dex; construct team-json and build-context from their input.
   Users may supply prose, text, JSON, a screenshot description or a file. Do not ask them to
   preformat the data. Set `owned` and `owned_only:true` only when the owned pool is a constraint.
2. Run `context-audit [team.json] --context context.json --format json` before assembly or changes.
   Resolve blocking/conflicting gaps; apply and disclose safe defaults. An expressive request
   missing its anchor is blocking because guessing would erase the stated intent.
3. For a new build, use `intake --next --context context.json --answered <resolved ids>` until
   `done:true`. Keep the answered IDs, including dimensions resolved by the original request or
   answer shape. Unknown IDs are errors. `--onboarding` is an overview, not the batch driver.
   Do not dump the entire catalog at the user. Existing-team work uses only the relevant gap menu.
4. Re-audit whenever answers change the context. Frame and slate must consume the receipt for
   the final context, not the initial incomplete version.

Use `intake` wording/options for gaps joined by `triggers_on`; each batch has 1–3 related questions.
Blocking input and conflicts need targeted questions. Cheap, reversible defaults can be disclosed.
No benchmark means no targeted tuning; it does not create a question by itself.

Read `schema.md` §1 for canonical SP, authored completeness and Mega representation. A specified
AI proposal uses empty provenance and omitted/null completeness; guessed source facts retain
`inferred_set` and cannot establish authoritative moves or roles.

For illegal abilities, missing/wrong Mega stones, out-of-pool items or Item Clause conflicts,
`suggest-fixes team.json --context context.json` offers explicit changes against the original
input. Read every conflicting holder's options; no holder automatically wins. Select and disclose
edits on a copy, then rerun validate and affected slate/frame/answer gates. The helper preserves
registered Mega identities and does not judge item suitability or make the candidate deliverable.

## Ground before assembly

For a new build/completion, read the current format's ranking and `landscape`, then use
`repset`, `observed` or `search/show` for real co-occurring sets and structures.
Marginal move/item/ability usage is not a joint recipe. Use a scoped
`oppmatrix <attacker> --game-format double [--vs <defender>]` for a reference matchup when needed;
the entire matrix requires explicit `--all` and is not default grounding.

Run `frame --game-format ... --context context.json --audit-receipt audit.json --format json`.
For alternative structures, add `--draw N --seed INT` and optionally `--seen-teams shown.json`.
Read `draw.frame_ids` as a weighted low-overlap exploration view; full skeletons and their receipt
remain the gate input. `observed --order diversity` offers distinct roster evidence with optional
seed/shown teams. Under `meta_conformance:off_meta`, fill also includes library-backed long-tail
species with sample/witness IDs. These are exploration views; assess each actual joint set yourself.
Set `frame_required:true` in build contexts before their final audit. Inspect the grounded
skeletons and their actual set guidance; explore alternatives when the evidence supports them.
The model decides how many candidates to develop and which structures fit the request.

- `core-partner` and anchor bearers bind to the frame's real (item, ability) clusters.
  A departure needs an explicit acknowledgement and reason; an unacknowledged departure
  eliminates the candidate. Moves/nature/SP guidance is soft, not a hard lock.
- `recurring-partner` is a softer observed association: deviations are reported, not eliminated.
  Substitutes still need their own joint-set basis or an explicit off-meta explanation.
- Empty backbones, species-only structure and fallback frames mean limited evidence.
  Disclose that limit and ground chosen members individually; an open slot is not evidence.
- Under the environment Mega posture, reliable observed registration distributions create
  disclosure/comparison obligations. Deliberate minority/rare lanes need `mega_deviation`;
  retain a modal lane or explicitly change the posture. `none|single|multi` are user composition
  constraints. Read the complete frame/binding rules in `schema.md` §8.

For targeted rework, use the original diagnosis and relevant joint sets instead of repeating
an unrelated global build interview. Look for an answer already on the team before replacing
a member, and retain the user's locked anchor.

Use `review` for the original and `try --changes` for explicit copy edits. Missing configuration
returns `needs_input`; obtain the missing user facts or author and disclose a new proposal before
making numeric claims. Different species clears the old set. The trial reports neutral structural
differences and comparable observed floors; its result still goes through slate and answer-audit.
Use `--focus` to bind named members/opponents to slots/report rows. Preserve full reports through
`--out` or session references. `review-claims` verifies the saved receipt and recomputes all claims
against the same input; copy the entire scoped fact with qualifications from `evidence_index`.
A member cell cannot stand for a team floor, and a successful check does not certify prose meaning.

To retrieve a concrete answer to a named opponent, set `need.answer` (a name or list) for `fill`.
It returns observed complete `builds[].team_member` and the same build's static CHECK facts against
every retained target build. Keep the attached coverage, caveats and contested conditions. Missing
joint SP or meta SP fallbacks are omitted from this transport; an empty result means no supported
complete answer in this scope. It does not prove that the opponent has no answer elsewhere.

## Evaluate the actual proposals

Validate every team before presenting it. Never present an invalid, eliminated or unslated proposal.
Use `slate-init` to assemble the chosen candidates with the final context/audit, full frame output
and latest `intake --next` result. It preserves explicit teams, or copies explicitly selected frame
primary sets into team-json; unknown fields stay unknown. It carries resolved intake IDs and aligned
bindings without choosing structures or inventing deviation acknowledgements. See `schema.md` §8
for the assembly input. Its output is the original slate input to save unchanged.
Run `slate-evaluate` with the original context, matching audit receipt, candidates and, for builds,
the frame output and one aligned binding per candidate.

The cheap funnel precedes the matchup battery. Survivors carry per-candidate facts and neutral
selection routes; tools preserve input order and do not choose a winner. Compare candidates using
the user's goals, accepted holes and opportunity costs. One recommendation needs the requested
single-team flag or a substantive reason for omitting alternatives; repeating the same team
does not count as distinct candidates.

Encode named opponents and desired members as `context.goals` (schema §6). Read slate's
receipt-bound `comparison`, including partial/unknown evidence and explicit goal rows.
`exploration_facts` from draft-init identifies over-half or unknown bound-core overlap; explain
exceptions in `draft.exploration.reason`. This is disclosure, not a fixed candidate-count rule.

- A six-member registration is a preview toolbox: singles brings 3, doubles brings 4.
  Read the attached select facts before discussing lineup roles or Mega routes.
  Several registered Mega options still allow only one active Mega in a battle.
- Team-level matchup claims require the full `matchup` table in both directions,
  not cherry-picked calculator calls or aggregate counts alone.
  For the slate's integrated battery, bind the complete saved opponent coverage row in
  `draft.team_claims` with its `evidence_ref` and `expect`; keep all qualifications.
- Adjustment intent such as "make it survive/outspeed/OHKO X" belongs in
  `benchmarks[]` and requires `tune`. Bare calculator calls answer as-is facts and spot checks;
  they do not establish a tuned spread.
- A replacement of an already shown candidate needs its reason, benefit, cost and evidence.
  Re-slate changed members/sets; old evidence IDs do not certify a later edit.

Use `session` for related operators. It accepts inline inputs and backward JSON Pointer references
to earlier full results, including when their stdout view is summary.
Use `--view summary --out full.json` for diagnose/select/oppmatrix/topmeta/frame/tune/intake and
matchup/slate-evaluate. Display summaries retain conditions, CHECK floors and missing evidence;
full artifacts and session references come from that same computation. `intake --onboarding` with
summary shows only the next numbered batch; continue with `--next --answered` and updated context.
Save original candidate inputs unchanged: do not normalize them
after the receipt has bound them. See `examples.md` for transport and object examples.

## Checkpoint and targeted tuning

Run `checkpoint` after a build-flow slate and before tuning or final delivery.
Candidate comparisons, confidence caveats and observed overlap are normally answer disclosures;
they do not force an extra choice. Set `checkpoint_required:true` when the user wants an
intermediate choice before continuing. Missing modal Mega-registration coverage needs a repair
or an explicit posture. `direct_final` / `skip_checkpoint` suppress a pause, not the final audit.

If benchmarks exist, run the applicable tuning and re-evaluate any changes. Otherwise disclose
`tuning_summary.status:not_run`; do not ask for unsolicited tuning targets.
Explain nature penalties, SP funding/reallocation and any unmodelled conditions from the facts.

## Prepare and audit the answer

Build final tables from `localize team.json` so official names come from dex. Optionally pass
the exact proposed final text as `draft.text_check:{text,lang}` to answer-audit. Its dictionary,
percentage and script checks supplement, but do not replace, checking the meaning of each sentence.
When adopting a verbatim library team, disclose `adoption_review.other_structures:{considered,reason}`.

Initialize with `draft-init --slate slate.json --slate-output full-slate.json --format json`.
It rebinds the full saved facts, fills onboarding from the carried resolved IDs/current context,
and offers `claim_candidates` with numeric expectations and exact opponent variants. Copy only
claims used in the answer into `draft.claims`; aggregate speed counts do not establish individual
speed claims. Incomplete onboarding remains incomplete. Fill the substantive fields; the helper
does not supply strategic judgment.
Run `answer-audit ... --fail-on-verdict` and present only `pass:true`.
A domain failure can otherwise exit 0, so always inspect the verdict. A summary slate is
display-only and is explicitly refused by answer-audit.

The original context/candidates and full receipts must form the same chain:
`context-audit -> [build frame] -> slate-evaluate -> answer-audit`.
The audit recomputes fingerprints, validates recommended teams and rechecks bound numerical claims.
A saved result is evidence only for its original inputs.

Before delivery, cover these obligations:

- Each recommendation copies its exact slated team and includes trade-offs.
  Explain worst matchup, accepted constraint and opportunity cost for every survivor,
  including candidates omitted from the final recommendations.
- Disclose answered-over gaps and defaults in `assumptions`; structured
  `{gap:<field>,note:...}` entries work across languages. Name low-confidence candidates
  in `confidence_notes`, not merely a generic caveat.
- Build drafts need completed `onboarding_summary` and acknowledged frame deviations.
  Multi-Mega and observed minority/rare registration lanes need their respective rationales.
  `schema` and `draft-init` expose the exact current fields.
- Observed-team adoption is allowed with `observed_provenance`; an exact joint-set copy
  additionally needs `adoption_review`: modification types considered, reason/evidence and
  any attempted fork. Keeping the observed team is allowed; silent adoption is not.
- Quantitative damage/speed claims need an evidence ID copied from the slate plus a nonempty
  `expect` object. Damage fields are numeric hit counts/null (`ko_guaranteed/ko_possible`)
  and numeric percentages (`min_percent/max_percent`).
  Speed fields are numeric `member/opponent` and
  `faster:"member"|"opponent"|"tie"`. Do not substitute prose, strings or fractions.
  Keep opponent variant identity; the `spd` evidence opponent is the modal set.
- Disclose calculations that could not be recomputed. Static damage/CHECK facts do not prove
  a turn sequence, a win rate or strategic superiority.

Finally use dex-provided official display names in the answer, keep single/double and historical
evidence separate, and explain assumptions, trade-offs and omitted alternatives. Receipts certify
facts and deliverability; they do not certify the quality of strategic judgment.
