# NCP Damage Calculator API Reference

This reference explains supported inputs and common examples. The executable contracts are
authoritative: run `python scripts/ncp-calc-api.py schema` and
`python scripts/ncp-speedline-api.py schema` before constructing unfamiliar payloads. (Install the
preferred runtime with `python -m pip install -r requirements.txt`. The Python CLIs host the vendored
NCP JavaScript in-process via `quickjs-ng` — no Node needed; without it the
same Python entries automatically delegate to the Node CLIs `node scripts/ncp-calc-api.js`, whose
calculation/query results are identical. Each `schema` truthfully names its own runtime entry.)

## Contents

- [Commands](#commands)
- [Pokémon Object](#pokémon-object)
- [Field Object](#field-object)
- [Damage Example](#example-mega-staraptor-vs-archaludon)
- [Damage Output](#output)
- [Speed-Line API](#speed-line-api)
- [Inference Case](#inference-case)
- [Attribution](#attribution)

## Commands

### Inference Case

`ncp-infer-case.py init` creates a portable input case for the set-inference workflow. First obtain
`inference_prior.v1` from the meta skill's `inference-prior` command for the same opponent form and
format. Supply a JSON object with `format`, `foe:{name}`, one to six `mine_team` members (canonical
English names and their current calculator configuration), and either an inline `prior` or a
`--prior` JSON file. `known:{item?,ability?}` records confirmed facts; optional `candidates` lists
explicit nature, item or ability choices. `--out` atomically saves the
case without replacing an input file. The case stores inputs and an empty `observations` array;
this command does not calculate a build or claim a verdict. Run `ncp-infer-case.py schema` for the
machine contract. Case evaluation requires quickjs-ng and the sibling dex; the prior is read from
the saved case, so no meta process or data access is required after export.

```powershell
python scripts\ncp-infer-case.py init --input case-input.json --prior meta-prior.json --out case.json
python scripts\ncp-infer-case.py status --input case.json
python scripts\ncp-infer-case.py add --input case.json --observation hit.json --out case-with-hit.json
python scripts\ncp-infer-case.py status --input case-with-hit.json --predict --mine 0
python scripts\ncp-infer-case.py example --input case-with-hit.json
python scripts\ncp-infer-case.py remove --input case-with-hit.json --id hit-1 --out corrected-case.json
```

The prior is a species-level in-game marginal, including Mega Stone and non-Mega ability entries
where present. Evaluation applies form legality, fixed stones and confirmed facts
before treating entries as candidates.

`hit.json` records one hit, for example:

```json
{"id":"hit-1","kind":"bulk","mine":0,"move":"Earthquake","before":100,"after":60,
 "mine_state":{"boosts":{"atk":-1}},"foe_state":{},"field":{"weather":"Rain"},
 "crit":false,"single_target":false}
```

`bulk` reads the opponent's integer HP percentage; `offense` reads our exact HP. State overrides
and field side flags are frozen with both combatants when adding the observation; stages already
include switch-in effects. Our member must have explicit item (empty means none), ability, nature
and SP (an explicit empty object means zero SP). Use actual Mega battle-form names and their stones.
The same runtime checks apply on replay. Unknown moves, unsupported multi-hit frames and malformed
readings refuse `add` before saving anything. Files are immutable inputs: save a new path, then pass
that case in the next turn. `add`/`remove` stdout contains `{case,status,next_step}`, while `--out`
stores only the portable case. `status` and `example` never write files.

Each hit may set `field.format` independently of the case's current format. Fields also accept
`gravity`, `foresight` and calculator side flags, including `charge` and `steely_spirit`.
`curHP` preserves HP-dependent abilities; `enabled:false` retains a hit without using it as evidence.

The status reports `accepted`, `impossible` with neighboring feasible readings, `unsupported`, `disabled`, joint
`count`, relative `natureShares`, stat ranges, remaining fractions and speed bounds. An individually
impossible reading is retained for correction but excluded from inference; individually possible
yet conflicting observations remain in the intersection and produce `state:conflict`. Correct a
record by removing its id and adding a replacement. Frozen hits survive changes to the current
team; changed opponent forms are explicitly skipped. A returned example is one surviving model
configuration, not a confirmed set or an observed tournament build.

Speed and optional predictions disclose `projection_frame`: the latest matching observation's
frozen frame, or the current member under neutral conditions if none exists. `--mine` selects that
member. Prediction damage bounds use surviving states; `possibleAfters` lists readings possible
under the initial candidates, not posterior probabilities or a move recommendation. Shared model
and case hashes identify each evaluation. The runtime provides numbered next-step commands with
Chinese, English and Japanese messages; `init` preserves the input-only case contract, followed by
`status` for initial ranges and guidance.

Current members may retain an empty move list or unfinished ability/nature fields while frozen
observations remain complete. These edits do not change historical inference. For an incomplete
selected member, `projection_frame.basis:unavailable` carries `reason:incomplete_current_member`
and `missing`; `--predict` returns no new predictions and explains the omission in
`predictions_omitted`, while the speed comparison is marked incomplete. No frozen configuration
is substituted for the unfinished current build. Recording a new hit still requires a complete
frozen configuration.

With `--predict`, each move also returns `observation`. A `ready` result contains sorted
`readings:[{after,probability}]`, `readingEntropyBits`, `expectedNoiseBits` and `informationGainBits`.
The probabilities sum to one under the **remaining joint-state weights**, marginalizing every
compatible HP/Atk/Def/SpA/SpD allocation under the 66-SP budget. Repeated damage rolls keep their
multiplicity, and KO or one-HP survival is applied before grouping readouts. Information gain is
readout entropy minus expected random-roll entropy, so a wide random damage band with identical
readout distributions across candidates has zero gain. This is an observation fact, not a ranking
of moves, their accuracy, battle risk or usefulness. Moves retain the input order.

These are model-conditional probabilities, not calibrated battle frequencies. Nature/item/ability
priors and prior matching-roll weights retain the existing model's assumptions; Speed allocations
do not multiply the five damage-axis measure. CLI comparisons assume a landed hit against a
full-HP foe in the disclosed `projection_frame`, before any recovery, recoil or other intervening
effect. They do not predict the next real turn unconditionally. A partial opponent-HP percentage
identifies several exact HPs: existing observations use feasibility over that interval, without
assuming a probability law for its exact HP. The shared distribution API therefore reports
`partial_hp_probability_unspecified` for partial-HP bulk predictions. Missing rolls or zero total
model weight also report unavailability rather than renormalizing an incomplete result.

Next-step guidance distinguishes invalid/conflicting evidence, omitted or pending observations,
available information, no distinguishing supported move in this frame, and a single remaining
damage state. None confirms the true full build or forces the user to continue. Use
`status --predict --mine INDEX` to compare another member. The host decides whether the user's
question is answered.

Klutz disables Focus Sash in the survival censor. For an incoming hit, when the opponent's recorded
exact HP puts Blaze/Torrent/Overgrow/Swarm on different sides of its trigger across possible HP SP,
the factorized model cannot express that extra dependency: it skips the whole record with
`hp_trigger_hidden_hp` (and refuses a new `add`) instead of excluding the ability or a real build.

#### Browser transfer

The browser keeps `pc-calc-roster-v1` and `pc-calc-infer-v1` in the current tab's session storage.
Export their raw values together; do not copy computed percentages or replace frozen snapshots
with the current roster. For example, run this in that tab's developer console to download the
input-only export:

```javascript
const exported = {
  schema: "quark_drive_web_inference.v1",
  roster: JSON.parse(sessionStorage.getItem("pc-calc-roster-v1")),
  records: JSON.parse(sessionStorage.getItem("pc-calc-infer-v1")),
};
const url = URL.createObjectURL(new Blob([JSON.stringify(exported, null, 2)], {type: "application/json"}));
const link = document.createElement("a");
link.href = url;
link.download = "web-inference.json";
link.click();
setTimeout(() => URL.revokeObjectURL(url), 1000);
```

```powershell
python scripts\ncp-infer-case.py import-web --input web-inference.json --prior meta-prior.json --out case.json
python scripts\ncp-infer-case.py status --input case.json
```

The selected opponent defaults to `roster.active.b`; `--foe-id` selects another uid on side B.
The prior must name the effective battle form and the current format. Browser storage does **not**
contain the meta snapshot: supply its matching exported prior when reproducing an earlier result.
Using a newer prior is a new evaluation, not proof of parity. The import receipt records this boundary.

Import preserves frozen builds even when our original roster member was removed, disabled records,
per-hit single/double format, both sides' conditions, current HP and ability switches. Base species
holding their own Mega Stone are resolved through dex to the battle form, as in the browser.
Empty move slots are dropped; unfinished current configurations are preserved for later editing.
Incomplete frozen configurations, missing snapshots, ambiguous identities,
unknown field flags and malformed readings fail before writing an output. It never fabricates a
missing historical snapshot from today's roster. Unsupported mechanics and changed opponent forms
remain explicit in `status.unsupported`; impossible readings and conflicts keep the ordinary case
semantics. Recorded stages already include entry drops. Browser-only Trick Room changes move order,
not hit damage, and is not a speed-order conclusion in this case format.

The browser continues to store its own UI inputs; the portable CLI case is a separate canonical
format. Import is one-way. Model counts, ranges and weights are comparable under identical priors,
known facts, engine/dex data and frozen hits. The webpage can choose an observed common set as its
display example, whereas CLI `example` returns a surviving model state; these are different display
choices. CLI predictions retain their disclosed `projection_frame`, rather than following live
browser roster edits.

Damage calculation:

```powershell
python scripts\ncp-calc-api.py --input calc.json
```

Batch calculation:

```powershell
python scripts\ncp-calc-api.py batch --input batch.json
```

`batch.json` is an array of the same objects accepted by one calculation.

Name resolution (align a dex-canonical or typed name to the exact NCP pokedex key — case/space/hyphen-insensitive, with did-you-mean candidates on a miss; never hand-scan the data files):

```powershell
'["garchomp","Rotom Wash","Mega Charizard X"]' | python scripts\ncp-calc-api.py resolve
```

Returns a 1:1 list `[{query, ok, name, match: exact|normalized}]`, or `{ok:false, error, suggestions}` on a miss; feed the returned `name` back to `one`/`batch`. Both CLIs also emit their full contract via `python scripts\ncp-calc-api.py schema` (and `ncp-speedline-api.py schema`).

Speed-line calculation:

```powershell
python scripts\ncp-speedline-api.py --input speed.json
```

Speed-line batch:

```powershell
python scripts\ncp-speedline-api.py batch --input speed-batch.json
```

Speed-line table:

```powershell
python scripts\ncp-speedline-api.py table --input speed-table.json
```

## Pokémon Object

Required:

- `name`: English NCP species/form name, e.g. `Mega Metagross`, `Mega Staraptor`, `Archaludon`.
- `moves`: one to four move names. The calculation uses `move` from the top-level input.

Recommended:

- `ability`: exact English ability name.
- `item`: exact English item name.
- `nature`: English nature name.
- `sps`: Champions stat points object with the canonical smogon keys `hp`, `atk`, `def`, `spa`, `spd`, `spe` (`spe` = Speed, `spa` = Special Attack). The legacy short keys `at/df/sa/sd/sp` are still accepted but not recommended.

Optional:

- `boosts`: stat stage object: `atk`, `def`, `spa`, `spd`, `spe` (legacy `at/df/sa/sd/sp` also accepted).
- `status`: `Healthy`, `Burned`, `Poisoned`, etc.
- `curHP`: current HP as raw integer.
- `tera`: boolean.
- `teraType`: type name.
- `abilityOn`: whether a conditionally-activated ability has already triggered. Most abilities default
  to active. The ones whose trigger a single damage frame cannot observe default to inactive, matching
  the upstream calculator's unchecked toggles: Flash Fire, Plus, Minus, Trace, Stakeout, Sand Spit,
  Battle Bond, Electromorphosis, Wind Power and Seed Sower. Pass `true` to model them as active.

## On-Field State

Before the damage frame is computed the wrapper resolves the state both Pokémon bring onto the field,
so these do not have to be pre-baked into `boosts`, `item`, `ability` or the field:

- copied and suppressed abilities (Trace, Neutralizing Gas, Klutz),
- type changes from Mimicry, Forecast and Terastallization,
- weather suppression from Air Lock and Cloud Nine,
- terrain seeds, Protosynthesis and Quark Drive, Intrepid Sword and Dauntless Shield, Wind Rider,
  Download, Embody Aspect and Battle Bond,
- Infiltrator ignoring the defender's screens,
- Heavy Metal, Light Metal and Float Stone feeding the weight-based base powers,
- the item, field and status modifiers on Speed that the speed-ratio moves read.

The two switch-in effects that move the **other** side's stat stages, Intimidate and Supersweet Syrup,
are also resolved. Set the top-level `switch_in_drops` to `false` when the caller already encodes that
drop in `boosts`, otherwise it is counted twice. Nothing else on the list is switchable.

## Field Object

Supported common options:

```json
{
  "weather": "Rain",
  "terrain": "Electric",
  "attackerSide": {"helping_hand": true, "tailwind": true},
  "defenderSide": {"reflect": true, "light_screen": true, "aurora_veil": true, "stealth_rock": true, "spikes": 1}
}
```

Side-option keys are canonically snake_case. The wrapper accepts legacy camelCase spellings for
compatibility, but new callers should emit the canonical form shown above.

Weather examples: `Rain`, `Sun`, `Sand`, `Snow`, or empty string.

Terrain examples: `Electric`, `Grassy`, `Psychic`, `Misty`, or empty string.

## Example: Mega Staraptor vs Archaludon

```json
{
  "attacker": {
    "name": "Mega Staraptor",
    "ability": "Contrary",
    "item": "Staraptorite",
    "nature": "Jolly",
    "sps": {"hp": 2, "atk": 32, "def": 0, "spa": 0, "spd": 0, "spe": 32},
    "moves": ["Close Combat"]
  },
  "defender": {
    "name": "Archaludon",
    "ability": "Stamina",
    "item": "Sitrus Berry",
    "nature": "Modest",
    "sps": {"hp": 2, "atk": 0, "def": 0, "spa": 32, "spd": 0, "spe": 32},
    "moves": ["Draco Meteor"]
  },
  "move": "Close Combat",
  "field": {}
}
```

## Output

```json
{
  "description": "32 Atk Mega Staraptor Close Combat vs. 2 HP / 0 Def Archaludon",
  "damage": [174, 176, "..."],
  "damagePercent": [104.2, 105.4, "..."],
  "min": 174,
  "max": 206,
  "minPercent": 104.2,
  "maxPercent": 123.4,
  "defenderHP": 167,
  "move": "Close Combat",
  "attacker": "Mega Staraptor",
  "defender": "Archaludon"
}
```

## Speed-Line API

Single input:

```json
{
  "name": "Mega Staraptor",
  "nature": "Jolly",
  "sps": {"spe": 32},
  "ivs": {"spe": 31},
  "boosts": {"spe": 0},
  "ability": "Contrary",
  "item": "",
  "status": "Healthy",
  "field": {"weather": "", "terrain": "", "tailwind": false}
}
```

Defaults match common speedline usage: `nature = Timid`, `sps.spe = 32`, `ivs.spe = 31`, no item, no ability, no field modifier. `Timid` and `Jolly` produce the same speed modifier; choose the nature that matches the actual set. Pass `ability` explicitly when a speed ability should apply, or set `useDefaultAbility: true` only when you intentionally want the NCP default ability inserted.

Single output:

```json
{
  "name": "Mega Staraptor",
  "types": ["Fighting", "Flying"],
  "baseSpeed": 110,
  "nature": "Timid",
  "speedSPs": 32,
  "speedIV": 31,
  "speedBoost": 0,
  "rawSpeed": 178,
  "boostedSpeed": 178,
  "finalSpeed": 178,
  "ability": "Contrary",
  "item": "",
  "status": "Healthy",
  "field": {"weather": "", "terrain": "", "tailwind": false, "swamp": false}
}
```

Table input:

```json
{
  "defaults": {"nature": "Timid", "sps": {"spe": 32}},
  "field": {"weather": "Sand"},
  "filters": {"type": "Ground", "speedMin": 200},
  "sort": "desc",
  "limit": 20
}
```

Supported table filters:

- `type` or `types`: require one or more exact English types.
- `mega`: `true` or `false`.
- `baseSpeedMin`, `baseSpeedMax`.
- `speedMin`, `speedMax`.

Speed modifiers are computed through the same bundled NCP functions used by the damage wrapper: stat stages, Choice Scarf, Iron Ball-style speed-halving items, Quick Feet, Slow Start, Chlorophyll, Swift Swim, Sand Rush, Slush Rush, Surge Surfer, Unburden, Tailwind, swamp, and paralysis.

The speedline implementation follows the Champions speedline convention for the active rule data.

## Attribution

The bundled formula/data files under `scripts/script_res/` are the NCP VGC Damage Calculator core. See `references/upstream-LICENSE` for the license and contributor attribution.
