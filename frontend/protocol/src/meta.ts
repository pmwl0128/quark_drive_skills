/**
 * Meta-facing Web DTOs (rankings, detail panels, rank trend). Mapped from `meta_query.py`
 * JSON and the shipped trend store by the backend mapper. Panel names arrive from the raw
 * source in zh/ja; the mapper resolves the canonical English `name` through the dex — the
 * DTO always carries the English join key (conventions §4).
 */
import { z } from "zod";
import {
  FormatIdSchema, MoveCategorySchema, SpSpreadSchema, TypeNameSchema, namedFields,
} from "./common.ts";

/** `slug` is the UPSTREAM meta id (routes + detail-cache key) and drifts with the source, so it
 * must never be used to find a sprite; `key` is the mapper-derived asset-pack key, same contract
 * as item panels. Optional so a projection built before this field still parses. */
export const RankRowDtoSchema = z.object({
  rank: z.number().int().positive(),
  slug: z.string().min(1),
  key: z.string().regex(/^pokemon:[a-z0-9-]+$/).optional(),
  /** Carried by current mappers so the ranking does not download the whole dex card index merely
   * to paint two badges. Optional only for projections built before this field existed. */
  types: z.array(TypeNameSchema).max(2).optional(),
  ...namedFields,
});
export type RankRowDto = z.infer<typeof RankRowDtoSchema>;

export const RankingDtoSchema = z.object({
  season: z.string().min(1),
  rule: z.string().min(1),
  format: FormatIdSchema,
  updatedAt: z.string().optional(),
  rows: z.array(RankRowDtoSchema),
});
export type RankingDto = z.infer<typeof RankingDtoSchema>;

/** Percentages are 0-100 floats (conventions §2); partners carry NO percentage upstream. */
const panelEntryFields = {
  rank: z.number().int().positive(),
  ...namedFields,
  percentage: z.number().min(0).max(100).nullable(),
} as const;

export const PanelEntryDtoSchema = z.object(panelEntryFields);
export type PanelEntryDto = z.infer<typeof PanelEntryDtoSchema>;

/** Item panel entries additionally carry the asset-pack key derived by the mapper, so the UI
 * never re-slugifies the name to find the sprite. */
export const ItemPanelEntryDtoSchema = z.object({
  ...panelEntryFields,
  key: z.string().regex(/^item:[a-z0-9-]+$/),
});
export type ItemPanelEntryDto = z.infer<typeof ItemPanelEntryDtoSchema>;

export const MovePanelEntryDtoSchema = z.object({
  ...panelEntryFields,
  type: TypeNameSchema,
  category: MoveCategorySchema,
  power: z.number().int().nullable(),
});
export type MovePanelEntryDto = z.infer<typeof MovePanelEntryDtoSchema>;

export const PartnerEntryDtoSchema = z.object({
  rank: z.number().int().positive(),
  ...namedFields,
  slug: z.string().optional(),
  nationalDex: z.number().int().positive().optional(),
});
export type PartnerEntryDto = z.infer<typeof PartnerEntryDtoSchema>;

export const SpreadEntryDtoSchema = z.object({
  rank: z.number().int().positive(),
  spread: SpSpreadSchema,
  percentage: z.number().min(0).max(100).nullable(),
});
export type SpreadEntryDto = z.infer<typeof SpreadEntryDtoSchema>;

export const MetaDetailDtoSchema = z.object({
  rank: z.number().int().positive().nullable(),
  slug: z.string().min(1),
  key: z.string().regex(/^pokemon:[a-z0-9-]+$/).optional(),   // see RankRowDtoSchema
  ...namedFields,
  format: FormatIdSchema,
  season: z.string().min(1),
  rule: z.string().min(1),
  panels: z.object({
    moves: z.array(MovePanelEntryDtoSchema),
    items: z.array(ItemPanelEntryDtoSchema),
    abilities: z.array(PanelEntryDtoSchema),
    natures: z.array(PanelEntryDtoSchema),
    partners: z.array(PartnerEntryDtoSchema),
    spreads: z.array(SpreadEntryDtoSchema),
  }),
});
export type MetaDetailDto = z.infer<typeof MetaDetailDtoSchema>;

/* ── KO axis ──────────────────────────────────────────────────────────────────────────────
 * A separate data family: backups or retained historical panels can use a different clock from
 * usage. Native default bulk panels share their format's snapshot; provenance preserves exceptions.
 * Two rules the shapes below encode rather than document:
 *   - opponent entries carry NO percentage. The source publishes an ORDERING; a nullable pct
 *     field would read as a measured zero to anything that plots it.
 *   - the move panels are `null`, never `[]`, until the tier is collected. `[]` would claim the
 *     source reported no KO moves. `coverage.moveShare` states the same fact at file level.
 */

/** One KO opponent. SPECIES-level: this contract keys these by national dex number with no form, so
 * `nationalDex` is the identity and `name` is a species name. `slug`/`key` name ONE form, because a
 * sprite and a link have to — the species itself where the dex names it exactly, otherwise the form
 * the dex points that bare species name at. `usageRank` pairs the ordering with how often that
 * pokemon is actually played, which is what lets a reader discount a KO position that is really
 * just exposure; null = not in this format's ranking. */
export const KoEntryDtoSchema = z.object({
  rank: z.number().int().positive(),
  ...namedFields,
  nationalDex: z.number().int().positive(),
  slug: z.string().optional(),
  key: z.string().regex(/^pokemon:[a-z0-9-]+$/).optional(),
  usageRank: z.number().int().positive().nullable(),
  /** The same species appears twice in one list because the underlying rows are per-form and the
   * source drops the form. Both entries are kept — de-duplicating would delete an observation. */
  formCollapsed: z.boolean().optional(),
});
export type KoEntryDto = z.infer<typeof KoEntryDtoSchema>;

/** Reserved tier. Same shape as the usage move panel because it IS one: when it ships, these
 * entries carry a real percentage and render with the same in-row bar. */
export const KoMoveEntryDtoSchema = MovePanelEntryDtoSchema;
export type KoMoveEntryDto = z.infer<typeof KoMoveEntryDtoSchema>;

export const KoCoverageDtoSchema = z.object({
  opponents: z.string().min(1),
  opponentIdentity: z.literal("national_dex"),
  opponentDepth: z.number().int().nonnegative(),
  opponentFormCollapsed: z.number().int().nonnegative().optional(),
  moveShare: z.enum(["absent", "ranked_pct"]),
  moveDepth: z.number().int().nonnegative().nullable(),
  rows: z.number().int().nonnegative(),
  rowsWithMoveShare: z.number().int().nonnegative(),
  /** Explicit same-rule donor capture; the DTO season remains the query context. */
  reusedFrom: z.object({
    season: z.string().min(1),
    rule: z.string().min(1),
    format: FormatIdSchema,
    updatedAt: z.string().min(1),
    snapshotId: z.number().int().positive().optional(),
  }).optional(),
  /** Confirmed zero-record directions retain the previous same-rule capture, by panel. */
  panelReusedFrom: z.partialRecord(z.enum(["koTargets", "koedBy", "koMoves", "koedByMoves"]),
    z.object({
      season: z.string().min(1),
      rule: z.string().min(1),
      format: FormatIdSchema,
      updatedAt: z.string().min(1),
      snapshotId: z.number().int().positive(),
    })).optional(),
  /** A filled move tier can come from a DIFFERENT capture than the object lists — which is why it
   * states its own time here instead of letting the file's `updatedAt` stand for both — and carries
   * how far the two captures agreed about each row's KO opponents (0..1, its own denominator).
   * Both absent while `moveShare` is "absent". */
  moveShareCapturedAt: z.string().min(1).optional(),
  moveShareAgreement: z.object({
    compared: z.number().int().nonnegative(),
    mean: z.number().min(0).max(1).nullable(),
    min: z.number().min(0).max(1).nullable(),
  }).optional(),
});
export type KoCoverageDto = z.infer<typeof KoCoverageDtoSchema>;

export const MetaKoDtoSchema = z.object({
  rank: z.number().int().positive().nullable(),
  slug: z.string().min(1),
  key: z.string().regex(/^pokemon:[a-z0-9-]+$/).optional(),
  ...namedFields,
  format: FormatIdSchema,
  season: z.string().min(1),
  rule: z.string().min(1),
  /** This axis's OWN data time — a different snapshot clock from ranking/details. */
  updatedAt: z.string().min(1),
  snapshotId: z.number().int().positive().optional(),
  coverage: KoCoverageDtoSchema,
  panels: z.object({
    koTargets: z.array(KoEntryDtoSchema),
    koedBy: z.array(KoEntryDtoSchema),
    koMoves: z.array(KoMoveEntryDtoSchema).nullable(),
    koedByMoves: z.array(KoMoveEntryDtoSchema).nullable(),
  }),
});
export type MetaKoDto = z.infer<typeof MetaKoDtoSchema>;

/** Inverted index for the filter rail: every facet value -> the slugs that carry it.
 * Shipped as ONE lazy file per format because the per-pokemon detail files are lazy too — a
 * facet filter over the whole roster would otherwise cost 262 fetches to answer one question. */
export const MetaFacetsDtoSchema = z.object({
  season: z.string().min(1),
  rule: z.string().min(1),
  format: FormatIdSchema,
  /** facet -> canonical English value -> slugs. Keys: items/moves/abilities/natures/partners/
   * koTargets/koedBy. Values are English canonicals (the cross-skill join key); the UI localizes
   * them through the dex at render time. */
  facets: z.record(z.string(), z.record(z.string(), z.array(z.string()))),
});
export type MetaFacetsDto = z.infer<typeof MetaFacetsDtoSchema>;

/** Rank-trend store projection: one series per tracked pokemon, `ranks[i]` aligned with
 * `periods[i]` (null = outside the tracked window that period). */
export const TrendDtoSchema = z.object({
  season: z.string().min(1),
  rule: z.string().min(1),
  format: FormatIdSchema,
  periods: z.array(z.string().min(1)),
  series: z.array(z.object({
    slug: z.string().min(1),
    ...namedFields,
    ranks: z.array(z.number().int().positive().nullable()),
  })),
}).refine(
  (t) => t.series.every((s) => s.ranks.length === t.periods.length),
  { message: "every series.ranks must align 1:1 with periods" },
);
export type TrendDto = z.infer<typeof TrendDtoSchema>;

const UsageValuesSchema = z.array(z.number().min(0).max(100).nullable());
const UsageNamedSeriesSchema = z.object({
  /** Canonical English join key; the visible label comes from the current detail row. */
  name: z.string().min(1),
  values: UsageValuesSchema,
});
const UsageSpreadSeriesSchema = z.object({
  spread: SpSpreadSchema,
  values: UsageValuesSchema,
});

/** History for a SPECIES-LEVEL panel row (a teammate, a KO opponent). Two things separate it from
 * the usage series above, and both come from the data rather than from taste:
 *   - the key is the national dex number, because that is the panel's own identity — those rows
 *     carry no form, so a name could not identify them anyway;
 *   - the values are RANKS, not percentages. Those panels publish an ordering and no share, so a
 *     percentage axis would plot a measurement nobody took. null = outside that period's list. */
export const SpeciesRankSeriesSchema = z.object({
  nationalDex: z.number().int().positive(),
  ranks: z.array(z.number().int().positive().nullable()),
});
export type SpeciesRankSeries = z.infer<typeof SpeciesRankSeriesSchema>;

/** Lazy per-Pokemon history for the five usage panels. Every values array aligns 1:1 with
 * periods; null means that entry was outside the upstream Top 10, never zero usage. */
export const UsageTrendDtoSchema = z.object({
  season: z.string().min(1),
  rule: z.string().min(1),
  format: FormatIdSchema,
  slug: z.string().min(1),
  periods: z.array(z.string().min(1)).max(10),
  panels: z.object({
    moves: z.array(UsageNamedSeriesSchema),
    items: z.array(UsageNamedSeriesSchema),
    abilities: z.array(UsageNamedSeriesSchema),
    natures: z.array(UsageNamedSeriesSchema),
    spreads: z.array(UsageSpreadSeriesSchema),
    /** Teammates, as ranks. Defaulted because a projection built before this panel existed is
     * still a valid document — it simply has no teammate history to show. */
    partners: z.array(SpeciesRankSeriesSchema).default([]),
  }),
}).refine((t) => {
  const expected = t.periods.length;
  return [
    ...t.panels.moves, ...t.panels.items, ...t.panels.abilities,
    ...t.panels.natures, ...t.panels.spreads,
  ].every((series) => series.values.length === expected)
    && t.panels.partners.every((series) => series.ranks.length === expected);
}, { message: "every usage series must align 1:1 with periods" });
export type UsageTrendDto = z.infer<typeof UsageTrendDtoSchema>;

/** Lazy per-Pokemon history for the KO axis. Its OWN document, not another panel on the usage
 * trend, for the same reason the KO facts are their own file family: the two axes are captured on
 * different clocks, so one period list cannot honestly describe both. */
export const MetaKoTrendDtoSchema = z.object({
  season: z.string().min(1),
  rule: z.string().min(1),
  format: FormatIdSchema,
  slug: z.string().min(1),
  periods: z.array(z.string().min(1)).max(10),
  panels: z.object({
    koTargets: z.array(SpeciesRankSeriesSchema),
    koedBy: z.array(SpeciesRankSeriesSchema),
    /** The move tiers DO carry a share where they are collected at all, so these stay percentages.
     * A period in which the tier was not collected is null, the same as one in which the move was
     * outside the list — both are "no measurement", which is what the chart must show. */
    koMoves: z.array(UsageNamedSeriesSchema),
    koedByMoves: z.array(UsageNamedSeriesSchema),
  }),
}).refine((t) => {
  const expected = t.periods.length;
  return [...t.panels.koTargets, ...t.panels.koedBy]
    .every((series) => series.ranks.length === expected)
    && [...t.panels.koMoves, ...t.panels.koedByMoves]
      .every((series) => series.values.length === expected);
}, { message: "every KO trend series must align 1:1 with periods" });
export type MetaKoTrendDto = z.infer<typeof MetaKoTrendDtoSchema>;

/* ── Top Meta (竞技环境 / 上位環境) ───────────────────────────────────────────────────────────
 * The real-team library counted on the in-game metagame's own fields (team skill `topmeta`).
 * Every entry is ONE record carrying both sides — the Top Meta count/share and the in-game value —
 * so the in-game page and the Top Meta page read the same deviation from opposite perspectives:
 * `delta` is Top Meta minus in-game, and the in-game page shows its negation.
 *   - `tier` decides presentation, never existence: `count` (under 5 units) shows k/n and carries
 *     no numeric delta, only the two markers; `low` is a share with a low-sample flag.
 *   - in-game `bound` means "below the last listed share" — never a measured zero; a delta derived
 *     from it is a lower bound (`deltaIsFloor`).
 *   - the rule scope spans seasons; it compares with the current official snapshot (`comparedWith.window`).
 */
/** `high` is the rule scope filtered on raw performance facts inside each source kind (a top-8 finish
 * in an event of at least 16, or a ladder top-100 rank / 2000+ rating); `criteria` states the bar and
 * `composition` the team units each kind contributed. A filter, never a weight. */
export const TopMetaScopeIdSchema = z.enum(["season", "rule", "high"]);
export type TopMetaScopeId = z.infer<typeof TopMetaScopeIdSchema>;
export const TopMetaTierSchema = z.enum(["count", "low", "reliable"]);
export type TopMetaTier = z.infer<typeof TopMetaTierSchema>;

export const TopIngameValueSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("pct"), value: z.number().min(0).max(100) }),
  z.object({ kind: z.literal("bound"), below: z.number().min(0).max(100) }),
  z.object({ kind: z.literal("absent") }),
]);
export type TopIngameValue = z.infer<typeof TopIngameValueSchema>;

export const TopIngameRankSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("rank"), value: z.number().int().positive() }),
  z.object({ kind: z.literal("unlisted") }),
]);
export type TopIngameRank = z.infer<typeof TopIngameRankSchema>;

const topHeaderFields = {
  format: FormatIdSchema,
  rule: z.string().min(1),
  scope: TopMetaScopeIdSchema,
  season: z.string().nullable(),
  partitions: z.array(z.string()),
  /** Deduplicated units: one player's distinct composition / distinct build. */
  sample: z.object({ teams: z.number().int().nonnegative(), sets: z.number().int().nonnegative() }),
  /** `current_snapshot`: the rule scope compared with the current official snapshot — the two sides
   * cover different time ranges, which every view must say. */
  comparedWith: z.object({
    season: z.string(),
    window: z.enum(["same_season", "current_snapshot"]),
    updatedAt: z.string().optional(),
  }).nullable(),
  builtAt: z.string().optional(),
  criteria: z.object({
    tournamentTop8: z.object({ placingAtMost: z.number().int().positive(), fieldSizeAtLeast: z.number().int().positive() }),
    ladderTop: z.object({ rankAtMost: z.number().int().positive(), orRatingAtLeast: z.number().positive() }),
  }).optional(),
  composition: z.object({ tournamentTop8: z.number().int().nonnegative(), ladderTop: z.number().int().nonnegative() }).optional(),
} as const;

export const TopRankRowDtoSchema = z.object({
  rank: z.number().int().positive(),
  slug: z.string().min(1),
  key: z.string().regex(/^pokemon:[a-z0-9-]+$/),
  types: z.array(TypeNameSchema).max(2),
  ...namedFields,
  teams: z.number().int().positive(),
  share: z.number().min(0).max(100),
  tier: TopMetaTierSchema,
  /** null = not in the in-game ranking, or the scope carries no comparison. */
  ingameRank: z.number().int().positive().nullable(),
  /** In-game rank minus Top Meta rank (positive = ranks higher in Top Meta). Null in the count tier. */
  rankDelta: z.number().int().nullable(),
  withinNoise: z.boolean().nullable(),
});
export type TopRankRowDto = z.infer<typeof TopRankRowDtoSchema>;

export const TopMetaRankingDtoSchema = z.object({
  ...topHeaderFields,
  rows: z.array(TopRankRowDtoSchema),
});
export type TopMetaRankingDto = z.infer<typeof TopMetaRankingDtoSchema>;

const topValueFields = {
  count: z.number().int().nonnegative(),
  share: z.number().min(0).max(100).nullable(),
  ingame: TopIngameValueSchema.nullable(),
  delta: z.number().nullable(),
  deltaIsFloor: z.literal(true).optional(),
  withinNoise: z.boolean().nullable(),
  marker: z.enum(["outside_ingame_top", "unseen_in_top"]).nullable(),
} as const;

export const TopNamedEntryDtoSchema = z.object({ ...namedFields, ...topValueFields });
export type TopNamedEntryDto = z.infer<typeof TopNamedEntryDtoSchema>;
export const TopItemEntryDtoSchema = z.object({
  ...namedFields, ...topValueFields, key: z.string().regex(/^item:[a-z0-9-]+$/),
});
export type TopItemEntryDto = z.infer<typeof TopItemEntryDtoSchema>;
export const TopMoveEntryDtoSchema = z.object({
  ...namedFields, ...topValueFields,
  type: TypeNameSchema.nullable(), category: MoveCategorySchema.nullable(),
  power: z.number().int().nullable(),
});
export type TopMoveEntryDto = z.infer<typeof TopMoveEntryDtoSchema>;
export const TopSpreadEntryDtoSchema = z.object({ spread: SpSpreadSchema, ...topValueFields });
export type TopSpreadEntryDto = z.infer<typeof TopSpreadEntryDtoSchema>;
/** Partners publish only an order in-game, so their comparison is rank against rank. A row counted in
 * fewer than three distinct teams is cropped at the web boundary (it would name one team's line-up). */
export const TopPartnerEntryDtoSchema = z.object({
  ...namedFields,
  slug: z.string().min(1),
  key: z.string().regex(/^pokemon:[a-z0-9-]+$/),
  count: z.number().int().nonnegative(),
  share: z.number().min(0).max(100).nullable(),
  rank: z.number().int().positive().nullable(),
  ingame: TopIngameRankSchema.nullable(),
  rankDelta: z.number().int().nullable(),
});
export type TopPartnerEntryDto = z.infer<typeof TopPartnerEntryDtoSchema>;

function topPanel<T extends z.ZodTypeAny>(entry: T) {
  return z.object({
    n: z.number().int().nonnegative(),
    tier: TopMetaTierSchema,
    entries: z.array(entry),
  });
}

export const TopMetaScopeDtoSchema = z.object({
  ...topHeaderFields,
  rank: z.number().int().positive().nullable(),
  tier: TopMetaTierSchema.nullable(),
  share: z.number().min(0).max(100).nullable(),
  rankDelta: z.number().int().nullable(),
  withinNoise: z.boolean().nullable(),
  ingameRank: z.number().int().positive().nullable(),
  teams: z.number().int().nonnegative(),
  sets: z.number().int().nonnegative(),
  lanes: z.record(z.string(), z.number().int().nonnegative()),
  panels: z.object({
    moves: topPanel(TopMoveEntryDtoSchema),
    items: topPanel(TopItemEntryDtoSchema),
    abilities: topPanel(TopNamedEntryDtoSchema),
    natures: topPanel(TopNamedEntryDtoSchema),
    spreads: topPanel(TopSpreadEntryDtoSchema),
    partners: topPanel(TopPartnerEntryDtoSchema),
  }),
});
export type TopMetaScopeDto = z.infer<typeof TopMetaScopeDtoSchema>;

export const TopMetaDetailDtoSchema = z.object({
  slug: z.string().min(1),
  key: z.string().regex(/^pokemon:[a-z0-9-]+$/),
  ...namedFields,
  format: FormatIdSchema,
  rule: z.string().min(1),
  season: z.string().nullable(),
  scopes: z.object({
    season: TopMetaScopeDtoSchema.nullable(),
    rule: TopMetaScopeDtoSchema.nullable(),
    high: TopMetaScopeDtoSchema.nullable().optional(),
  }),
});
export type TopMetaDetailDto = z.infer<typeof TopMetaDetailDtoSchema>;
