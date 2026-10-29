# Pokémon Champions Meta Cache Schema

This reference documents the bundled cache files and their stored rows. For the public CLI contract,
run `python scripts/meta_query.py schema`; its executable self-description and actual output are
authoritative. Stored rows may contain source-oriented names or casing that the CLI normalizes at the
boundary.

Runtime data root: `data/`. This is prebuilt, read-only data.

`topmeta` additionally reads the sibling team skill's `data/top_meta/<rule>_<format>.json` through its
pure comparison code. The table schema stays owned by team `references/schema.md` §9; meta does not
copy, rebuild or reinterpret its counting units. Public query behavior and missing-dependency/form
granularity handling are documented in `api.md` and executable `schema`.

## Files

- `ranking_<season>_<format>.json`
- `details_<season>_<format>.json`
- `ko_<season>_<format>.json` (KO axis; its own snapshot clock)
- `report_<season>_<format>.json`
- `trend_<season>_<format>.json` (rolling refresh history for retained seasons; older seasons may have no file)
- `usage_trend_<season>_<format>.json` (rolling per-panel history: percentages, plus teammate RANKS)
- `ko_trend_<season>_<format>.json` (the KO axis's own rolling history, on the KO snapshot clock)
- `current.json`

`format` is `single` or `double`.

`trend` stores `periods[{date,updated_at}]`, `names{slug:{zh,ja,en}}`, and
`ranks{slug:[rank|null]}`. `usage_trend` stores the same period dates and
`pokemon{slug:{moves|items|abilities|natures:{source_name:[percentage|null]}}}`.
Each series is aligned to the stored periods; a missing or mismatched file cannot be
substituted with another season. `report` describes the latest snapshot change only,
not the whole rolling trend. The CLI `trend`/`movers` views expose the distinction.

## Ranking Row

```json
{
  "rank": 1,
  "pokemon": "烈咬陆鲨",
  "slug": "garchomp",
  "pokemon_en": "Garchomp",
  "pokemon_ja": "ガブリアス",
  "format": "single",
  "season": "M-4",
  "rule": "M-B"
}
```

## Detail Row

```json
{
  "rank": 1,
  "pokemon": "烈咬陆鲨",
  "slug": "garchomp",
  "pokedex_no": 445,
  "format": "double",
  "season": "M-4",
  "rule": "M-B",
  "panels": {
    "moves": [{"rank": 1, "name": "龙爪", "name_ja": "ドラゴンクロー", "percentage": 89.5, "type": "dragon", "category": "physical", "power": 80, "accuracy": 100}],
    "items": [{"rank": 1, "name": "讲究围巾", "percentage": 14.8}],
    "abilities": [{"rank": 1, "name": "粗糙皮肤", "percentage": 99.8}],
    "natures": [{"rank": 1, "name": "爽朗", "percentage": 66.6}],
    "partners": [{"rank": 1, "name": "风妖精", "slug": "whimsicott", "percentage": null}],
    "spreads": [{"rank": 1, "hp": 2, "atk": 32, "def": 0, "spa": 0, "spd": 0, "spe": 32, "percentage": 56.0}]
  }
}
```

Panel keys are normalized to English: `moves`, `items`, `abilities`, `natures`, `partners`, `spreads`.

A row carries an optional `detail_updated` ISO stamp only when the source provides a real
per-pokemon update time; the file-level `updated_at` is the refresh stamp for every row.

## KO File

A second data axis, stored separately from `details`, answering "who knocks out whom". The default
complete refresh collects usage, KO objects and KO move shares from one bulk snapshot per format.
Backups and explicitly retained captures can advance on separate clocks, so quote each file's own
`updated_at`.

An explicitly requested same-rule fallback has `coverage.reused_from` with the donor `season`,
`rule`, `format`, `updated_at` and `snapshot_id`. The file's `season` is the query context; the
KO relationships and KO move shares remain donor-season measurements. `updated_at`, `snapshot`
and move-share capture clocks remain unchanged. Subjects are joined to the target ranking by
canonical name within each format; missing subjects remain unavailable. Borrowed captures do
not create target-season KO trend points. A fresh KO capture replaces this fallback marker.

For a confirmed zero-record direction, the default update retains the previous same-rule,
same-format capture by canonical form. If the previous capture is also empty, use the nearest
nonempty capture within the same rule. The row's `panel_reused_from` maps each retained panel to
`{season, rule, format, updated_at, snapshot_id, reason: "current_zero_records"}`. Other nonzero
directions stay current. `coverage.rows_with_retained_ko` counts affected rows and
`move_share_capture: mixed_snapshot_bulk_exports` omits a file-wide move-share snapshot ID.
Only native panels belong to the file's current snapshot; quote the retained panel's own clock.
Retained panels do not produce current-snapshot KO trend observations.

```json
{
  "version": 1,
  "season": "M-6", "rule": "M-C", "format": "double",
  "updated_at": "2026-09-15T01:46:15+00:00",
  "snapshot": {"id": 3107, "taken_at": "2026-09-15T01:46:15+00:00", "verified": true},
  "coverage": {
    "opponents": "ranked",
    "opponent_identity": "national_dex",
    "opponent_depth": 30,
    "opponent_form_collapsed": 688,
    "move_share": "absent",
    "move_depth": null,
    "rows": 262,
    "rows_with_move_share": 0
  },
  "rows": [
    {
      "rank": 2, "pokemon": "大狃拉", "slug": "sneasler",
      "pokemon_en": "Sneasler", "pokemon_ja": "オオニューラ", "pokedex_no": 903,
      "panels": {
        "ko_targets": [{"rank": 1, "key": "727", "name": "炽焰咆哮虎", "name_ja": "ガオガエン", "name_en": "Incineroar"}],
        "koed_by":    [{"rank": 1, "key": "373", "name": "暴飞龙", "name_ja": "ボーマンダ", "name_en": "Salamence"}],
        "ko_moves": null,
        "koed_by_moves": null
      }
    }
  ]
}
```

`ko_targets` is whom this Pokémon knocks out (source: "Most KOs"); `koed_by` is who knocks it out
("Most KO'd By"). Four rules govern reading this file:

- **Ordering, not share.** Object entries deliberately have NO `percentage` field — the source
  exposes an ordering in this contract. Raw upstream counts are not a percentage or a win rate.
- **`null` means not collected.** `ko_moves` / `koed_by_moves` are a reserved tier. While
  `coverage.move_share` is `absent` they are `null`; an empty list would mean the source reported
  none. When the tier ships, `move_share` becomes `ranked_pct` and the panels hold move entries in
  the same shape as `details`' `moves` panel (with `percentage`).
- **Move-share capture provenance.** `move_share_captured_at` timestamps that tier.
  `move_share_capture: independent_bulk` also carries `move_share_agreement`; historical single
  captures may retain the legacy `same_snapshot_detail_pages` marker. The default bulk collection uses
  `same_snapshot_bulk_export` for both formats; `move_share_snapshot_id` must equal the file's
  verified snapshot. Same-snapshot captures carry no cross-capture agreement score.
- **Objects are species-level.** `key` is the national dex number and there is no form: the source
  drops it. The names come from the dex at whatever precision that number allows: the base-form row
  when there is one, the single form when the dex ships the species as exactly one (#670 can only
  mean Floette-Eternal), and otherwise the BARE species name in every language (南瓜怪人 /
  Gourgeist). Only that last case is ambiguous, and only it carries `form_rep` — the form the dex
  points that bare name at — for consumers that need one form to draw or link to. `name_en` is the
  species-level join key, so it equals `form_rep`'s species, not `form_rep`.
- **A repeat is flagged, not removed.** The same `key` can appear twice in one list because the
  underlying rows are per-form; both entries are kept (each is a separate observation) and both carry
  `"form_collapsed": true`. `coverage.opponent_form_collapsed` counts them file-wide.

Row subjects ARE form-level and identified by the dex canonical (`pokemon_en`), which is how this
file joins `ranking`/`details`. `slug` is copied from usage when available, otherwise derived from
the same dex canonical naming rule.

## Current State

`current.json` stores the default context and season/rule mapping. This is an illustrative historical
shape; always read the bundled file for the active values:

```json
{
  "current": {"season": "M-4", "rule": "M-B"},
  "seasons": {
    "M-1": {"rule": "M-A", "label": "Pokémon Champions M-1 / Regulation M-A"},
    "M-2": {"rule": "M-A", "label": "Pokémon Champions M-2 / Regulation M-A"},
    "M-3": {"rule": "M-B", "label": "Pokémon Champions M-3 / Regulation M-B"},
    "M-4": {"rule": "M-B", "label": "Pokémon Champions M-4 / Regulation M-B",
            "updated_at": "2026-08-05T00:00:00+00:00",
            "activated_at": "2026-07-09T11:20:31+00:00"}
  }
}
```

A season entry's timestamps answer different questions and are not interchangeable. `updated_at` is
the UPSTREAM data stamp — when the snapshot this file describes was aggregated — and moves with every
refresh; `created_at` (when present) records when the entry was first written. `activated_at` is when
this checkout first made the season current: written once, never rewritten by a later refresh of the
same season. Only `activated_at` anchors the seven-day cross-regulation team handover window, so a
stale or future upstream stamp can neither shorten nor extend it. Any of the three may be absent —
entries that became current before a field existed simply do not carry it, which is why the bundled
file's older seasons have no `activated_at`.

Queries with no `--season` or `--rule` resolve through `current`. Same-rule historical seasons remain
queryable while their files are retained. On a rule rollover, the update pipeline prunes stale-rule
meta snapshots; entries can remain in `current.json` as known season/rule mappings without implying
that their ranking/detail files still ship.
