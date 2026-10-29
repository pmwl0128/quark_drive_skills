---
name: pokemon-champions-meta
description: Offline-first Pokémon Champions metagame queries for current/historical seasons and single/double formats, including usage, common configurations, partners, trends and competitive Top Meta. Use for "环境/使用率/常见配置/高端局/上位", "usage/ranking/common sets/high ladder/top cut/tournament", or "環境/採用率/よくある型/上位帯/大会". High-level wording routes to topmeta --scope high. Do not trigger from an isolated item, ability or nature without metagame context. Battle dex facts belong to the dex skill; exact damage and speed arithmetic belong to the calculator.
---

# Pokémon Champions Meta

Query the bundled Pokémon Champions metagame snapshot for rankings, common configurations, partners,
format comparisons, and factual changes between snapshots. This skill answers "what is popular and
how is it used?"; canonical battle facts remain the responsibility of `$pokemon-champions-dex`.

## Workflow

1. Use the current season/rule unless the user explicitly requests a historical context.
2. Use `ranking` for the in-game environment overview and `detail` for one Pokémon's panels.
   Use `topmeta` for the competitive real-team population and its differences from in-game usage.
   High-level wording always uses `--scope high`: 高端、高分段、高水平、天梯顶端、上位、比赛、大赛;
   high-end, high ladder, top ladder, high-rated, top cut, tournament;
   上位帯、上位勢、高レート、大会、入賞. Never substitute the whole ladder for this population.
3. Use `who-uses` to find which Pokemon run a move/item/ability/nature, and `compare` for
   single-vs-double ranks and per-Pokemon panel shares. `search` handles more general filters.
4. Use `ko` for the knock-out axis — whom a Pokémon knocks out and who knocks it out.
5. Use `trend` for one Pokemon's stored rank/panel history, `movers` for this season's top-30
   risers/fallers, and `report` for changes since the latest snapshot. Use `export-excel` only
   when the user requests workbooks.
   For team-level format structure (speed control, signals, Mega slots and common pairs), use
   the sibling team skill's `landscape --game-format <single|double> --format json`; it reads
   real joint teams, not in-game usage marginals. Add `--scope high` for high-level structure;
   its sample counts qualifying published records, not Top Meta's player-deduplicated units.
6. Keep usage marginals factual: common moves/items/partners are not a guaranteed joint set.
7. When asked to update only meta, run `python dev/update/update.py meta` from the repository root.
   The default flow collects full single and double usage, KO objects and both KO move-share panels
   together. Do not ask for separate KO or move collection. Use `refresh` when real teams also need
   updating; developer source and backup operations are documented in `dev/update/README.md`.

## Commands

Query cached rankings:

```bash
# Uses the current season/rule by default.
python scripts/meta_query.py ranking --format single --limit 20
python scripts/meta_query.py ranking --format double --limit 20
python scripts/meta_query.py ranking --format double --season M-4 --rule M-B --limit 20
```

Query Pokémon details:

```bash
python scripts/meta_query.py detail --format single --pokemon 雷丘
python scripts/meta_query.py detail --format double --pokemon garchomp
python scripts/meta_query.py inference-prior --format double --pokemon "Mega Garchomp Z"
```

Reverse-search panel entries or compare formats:

```bash
python scripts/meta_query.py search --format double --panel moves --name 地震
python scripts/meta_query.py search --format both --panel moves --type ground --category physical --min-usage 20
python scripts/meta_query.py search --format single --panel moves --where '{"and":[{"type":"Fire"},{"or":[{"category":"Special"},{"usage":">=20"}]}]}'
python scripts/meta_query.py compare --pokemon 雷丘
python scripts/meta_query.py who-uses --kind item --entry 讲究围巾 --format double
python scripts/meta_query.py trend --pokemon 烈咬陆鲨 --format single
python scripts/meta_query.py movers --format double
python scripts/meta_query.py report --format both
```

Query the KO axis (a separate data family with its own upstream snapshot):

```bash
python scripts/meta_query.py ko --format double --pokemon 大狃拉
python scripts/meta_query.py ko --format single --pokemon Salamence --panel koed_by
python scripts/meta_query.py ko --format single            # coverage only: what this snapshot has
```

`ko_targets` (alias `beats`) is whom the Pokémon knocks out; `koed_by` (alias `counters`) is who
knocks it out. Both are an **ordering only** — the source publishes no percentage, so none is shown.
Both are **species-level**: opponents are keyed by national dex number with no form, and a species
that appears twice in one list is flagged rather than de-duplicated. `ko_moves` / `koed_by_moves` are
a reserved tier: while `coverage.move_share` is `absent` they are `null`, meaning NOT COLLECTED — not
"no KO moves". Never present a KO list as a matchup win rate; it counts knock-outs, not games.

Names resolve through the sibling dex in Chinese, English, or Japanese. Fuzzy corrections are always
disclosed: structured detail/compare/ko results carry resolution metadata, while `search` reports a
correction on stderr without contaminating JSON stdout.
The public dex resolver accepts compositional Mega names such as `Mega耿鬼` and `メガゲンガー`;
explicit forms never fall back to a base row when the snapshot lacks that form.

## Competitive Top Meta

```bash
python scripts/meta_query.py topmeta --format double --scope high --pokemon 烈咬陆鲨
python scripts/meta_query.py topmeta --format single --scope rule --limit 10
python scripts/meta_query.py topmeta --format double --scope season --pokemon Garchomp --view full
```

The default is compact canonical JSON (`--view summary`, display limit 6). `--view full` returns
untruncated data. Always name the population, scope, sample and comparison window. `high` states its
separate tournament/ladder criteria and composition; it is a filter, not a strength score.
Read `queries[]` and `projection.omitted` before making claims. An unrepresented or ambiguous query
stays visible. Published counts aggregate Megas into species: a Mega-only question reports the
granularity gap and a possible base-name reference, without presenting that aggregate as Mega data.

This command has a read-only dependency on the sibling `$pokemon-champions-team` published
`data/top_meta/` tables and its pure comparison/projection modules. It does not load the team
orchestrator, read the real-team library, rebuild data or call a network service. Missing team
installation is explicitly unavailable; other meta commands continue to work without it.
The data remains the same library evidence used by repset/frame, so agreement is not corroboration.

Use JSON for downstream processing:

```bash
python scripts/meta_query.py detail --format double --pokemon garchomp --output json
```

## Contract And Output

Treat the executable schema as the authority for commands, flags, fields, boolean query grammar, and
errors:

```bash
python scripts/meta_query.py schema
```

Canonical JSON is language-independent. Human-readable output language precedence is `--lang`, then
`POKEMON_CHAMPIONS_LANG`, then `en`. Read `references/api.md` for detailed command usage and
`references/schema.md` only when the bundled cache layout matters.

## Excel Export

`export-excel` always writes three standalone single-language workbooks (`zh`, `ja`, `en`) to the
chosen output directory, independently of `--lang`:

```bash
python scripts/meta_query.py export-excel --season M-4 --rule M-B
```

This is the only command requiring `openpyxl`; install `requirements.txt` when export is needed. All
query commands use the Python standard library.

## Boundaries

- The shipped snapshot is read-only and queried offline; its date and season/rule stamp define freshness.
- Do not infer legality or learnsets from usage data.
- Do not combine move, item, ability, nature, partner, or spread marginals into an asserted joint set.
- A change report records factual differences between snapshots, not an interpretation of the metagame.
- Usage and KO normally share a complete snapshot; backups or retained KO can have different clocks.
  Quote each one's own `updated_at` and never treat a KO ordering as a win rate, a counter verdict,
  or a damage result. For zero-record directions retained from history, quote each panel's donor
  clock; feedback evidence and upstream reports use English.
