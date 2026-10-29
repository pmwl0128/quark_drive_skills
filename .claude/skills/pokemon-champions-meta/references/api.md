# Pokémon Champions Meta API

This skill is read-only: it queries the prebuilt data under `data/` and performs no network access.
Run `python scripts/meta_query.py schema` for the authoritative command, flag, field, and error
contract. This file provides usage guidance and export behavior rather than a second machine schema.

## Query

All query commands accept optional `--season` and `--rule`. If neither is passed, the command uses the current season/rule from `data/current.json`. If only `--rule` is passed, it resolves to the current season if the rule matches, otherwise the latest recorded season for that rule.

### Competitive Top Meta

```bash
python scripts/meta_query.py topmeta --format double --scope high --pokemon 烈咬陆鲨
python scripts/meta_query.py topmeta --format single --scope rule --limit 10
python scripts/meta_query.py topmeta --format double --scope season --pokemon Garchomp --view full
```

`topmeta` is JSON-only, compact by default. `--limit N` is a positive display limit for summary tables
and panels (default 6); it preserves population ranks. `--view full` returns untruncated evidence and
rejects a display limit. `--pokemon` (alias `--species`) or positional names accept trilingual dex
names. `queries[]` aligns to supplied names, including fuzzy corrections and misses.

The result is `top_meta.v1`: population, scope, partitions, sample, per-panel denominators and tiers,
thresholds, and `compared_with` with its own clock/window. High scope also carries criteria and
composition. Season scope only reads the published season; a missing historical table is
`not_found`, never replaced by the current season. Rule/high compare with the current in-game
snapshot only within the same rule. `projection.omitted` identifies every truncated table/panel.

The sibling team installation supplies the published table and pure comparison/projection code.
Absent installation returns `engine_unavailable` (exit 1); a missing table returns `not_found`
(exit 0). Neither path updates data or substitutes a different population. `data_granularity` is
`species_aggregate`: Mega-only distributions cannot be reconstructed from these marginals.
Such a query carries `queries[].error.code:unsupported_mechanic` and exits 1; any other requested
species' facts remain visible. `available_reference` names a broader population, not an answer to
the requested Mega-only question.

Ranking:

```bash
python scripts/meta_query.py ranking --format double --limit 20
python scripts/meta_query.py ranking --format double --season M-4 --rule M-B --limit 20
```

Detail:

```bash
python scripts/meta_query.py detail --format single --pokemon 雷丘
python scripts/meta_query.py detail --format double --pokemon garchomp --panel moves --output json
```

Inference prior for a calculator case:

```bash
python scripts/meta_query.py inference-prior --format double --pokemon "Mega Garchomp Z"
```

`inference-prior` exports the base species' in-game detail panels, keeping only the first ten
entries in each nature/item/ability panel with usage at least 1%. Names are canonical English;
percentages remain source percentages, not normalized probabilities. The response includes the
season, rule, format and detail snapshot clock. Mega queries name the requested form and the
base species whose panels supplied the prior. These are species-level marginals, not joint build
frequencies or a Mega-only distribution. The calculator case must apply form-specific legal
abilities, fixed Mega stone, confirmed facts and its empty-panel fallback itself.

Search by panel entry:

```bash
python scripts/meta_query.py search --format double --panel moves --name 近身战
python scripts/meta_query.py search --format single --panel items --name 气势披带
python scripts/meta_query.py search --format both --panel moves --type ground --category physical --min-usage 20
```

Search options can be combined. `--name` may be repeated and all name terms must match one of the entry fields. `--type` and `--category` filter move metadata when the cache provides it.

Who uses an entry, format comparison, and stored changes:

```bash
python scripts/meta_query.py who-uses --kind item --entry "Choice Scarf" --format double
python scripts/meta_query.py compare --pokemon Garchomp --output json
python scripts/meta_query.py trend --pokemon Garchomp --format single
python scripts/meta_query.py movers --format double
python scripts/meta_query.py report --format double
```

`who-uses` returns the usage top 60 separately from lower-ranked Pokemon, ordered by each
Pokemon's own panel percentage. That percentage is not the share of all teams. Results below
1% are counted but not listed; `--limit` controls the top-60 display count. `compare` keeps
the existing top-name lists and adds each format's own `panel_shares` and `updated_at`.
Partner percentages may be unknown (`null`); do not interpret that as zero.

`trend` reads the stored rolling refresh history of the requested season. A null rank means
unranked at that update, and null panel usage means the entry was not listed. Mega requests
are explicitly mapped to their base species in this species-level history. The panel shifts
cover the first and last stored updates, not necessarily the whole season; a missing history
returns `not_found` rather than silently using another season. `movers` uses that same window
for the current top 30; `last_update` reports counts from the latest update only. These
views are JSON-only. `movers.omitted` reports rows beyond the display limit. They never
treat refresh dates as a Top Meta trend.

For structure of observed real teams, run the sibling team skill's
`python scripts/team.py landscape --game-format double --format json` from its own directory.
It reports speed control, structural signals, Mega registration and common pairs for one
format/rule, not the in-game usage population. It has no high-performance sample filter.

KO axis:

```bash
python scripts/meta_query.py ko --format double --pokemon 大狃拉
python scripts/meta_query.py ko --format single --pokemon Salamence --panel koed_by --output json
python scripts/meta_query.py ko --format double --pokemon 大狃拉 暴飞龙 --output json
python scripts/meta_query.py ko --format single
```

`--panel` accepts `ko_targets` (alias `beats`), `koed_by` (alias `counters`), `ko_moves` and
`koed_by_moves`. With no `--pokemon` the command returns this snapshot's `coverage` alone — read it
first to learn whether the move-share tier is collected, because an uncollected panel is `null`, not
an empty list. Every answer repeats `coverage` and the KO file's own `updated_at`, which is a
DIFFERENT snapshot clock from `ranking`/`details`.

A season whose KO snapshot was never collected is a graceful `not_found` (exit 0) naming the gap —
it is a data gap, not an empty metagame. An unknown `--panel` is `bad_input` (exit 1).

Compare single and double:

```bash
python scripts/meta_query.py compare --pokemon 雷丘
```

Export Excel:

```bash
# ALWAYS three single-language workbooks, written to the current directory:
#   M-4_YYYYMMDD_zh.xlsx  M-4_YYYYMMDD_ja.xlsx  M-4_YYYYMMDD_en.xlsx
python scripts/meta_query.py export-excel --season M-4 --rule M-B

# Pick the output dir; --report-dir overrides where the report is read from.
python scripts/meta_query.py export-excel --output-dir teams/
```

`export-excel` reads only local data and is a **distribution artifact**: it always writes
**three standalone single-language workbooks** (zh / ja / en), independent of `--lang` / the
env var / `DEFAULT_LANG`. Each workbook is self-contained, with four worksheets in its own
language:

- a lookup sheet (`检索` / `検索` / `Lookup`) first — select one Pokémon to see its Singles and
  Doubles panels plus the current factual changes for that Pokémon;
- two data sheets (`单打`/`双打`, `シングル`/`ダブル`, or `Singles`/`Doubles`) — one row per
  Pokémon. Names and every panel cell are rendered in that file's language via the dex (the
  naming authority): the en file carries no Chinese, the zh/ja files add an English
  cross-reference column. Columns (zh) are `排名`, `中文名`, `英文名`, `招式`, `道具`, `特性`,
  `性格`, `队友`, `SP 分配`; ja/en use the translated headers (ja keeps `日本語名` + `英語名`, en
  is `Name` only). Panel entries are ranked multiline cell text; SP spreads use `H/A/B/C/D/S`.
- an update-report sheet (`更新报告` / `更新レポート` / `Update Report`) — the single and double
  factual change tables plus their tier reference. Reports are read from
  `data/report_<season>_<format>.json` by default.

The files go to the current working directory (the user's project), never into the skill. The
default season/rule is read from `data/current.json`. Before writing, generated report workbooks in
the output directory whose names match `<M-season>_<YYYYMMDD>_{zh,ja,en}.xlsx` are deleted, so stale
triplets do not survive beside the current export.

## Output Formats

`--output md` is default. Use `--output json` for structured downstream use.
`export-excel` always writes the three `.xlsx` files and prints a JSON summary
(`{files:[{lang, output_file, sheets, single_rows, double_rows}], reports_embedded}`).
