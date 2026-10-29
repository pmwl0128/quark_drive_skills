/**
 * Tune (SP cliff) Web DTOs — the `tune` operator's benchmark-ordered frontier cards, consumed by the web bulk-tune
 * tool's "precise" mode. Unlike the other domains this DTO is validated in the operator's NATIVE
 * snake_case shape (the card schema is deep, varied — survival vs speed cliffs — and evolving; it is
 * passed through and rendered rather than re-modeled into camelCase). `.passthrough()` keeps the
 * richer fields (evidence/nature/probability/field lanes/environment) the UI doesn't yet surface.
 */
import { z } from "zod";

/** Path-free request shared by the local and online precise-tune endpoints. The nested team and
 * benchmark contracts remain owned by the team skill; this Web boundary only enforces the public
 * cardinality/resource envelope before invoking it. */
export const TuneRequestDtoSchema = z.object({
  team: z.record(z.string(), z.unknown()),
  benchmarks: z.array(z.record(z.string(), z.unknown())).min(1).max(12),
});
export type TuneRequestDto = z.infer<typeof TuneRequestDtoSchema>;

const LaneSchema = z.object({
  stat: z.string().optional(),
  result: z.string(),
  delta_sp: z.number().optional(),
  need_total: z.number().optional(),
  note: z.string().optional(),
}).passthrough();

const ReallocationSchema = z.object({
  required_sp: z.number().nonnegative(),
  available_sp: z.number().nonnegative(),
  unused_sp: z.number().nonnegative(),
  candidate_donors: z.array(z.object({
    stat: z.string(),
    current_sp: z.number().positive(),
    opportunity_cost: z.string(),
    protected_sp: z.number().nonnegative().optional(),
    available_without_breaking: z.number().nonnegative().optional(),
    protected_by_benchmarks: z.array(z.number().nonnegative()).optional(),
  })),
  available_without_breaking: z.number().nonnegative().optional(),
  preserves_existing_benchmarks: z.boolean().optional(),
  protected_shortfall_sp: z.number().nonnegative().optional(),
  note: z.string(),
});

const AllocationStatSchema = z.object({
  current: z.number().nonnegative(),
  need_total: z.number().nonnegative(),
  delta_sp: z.number().nonnegative(),
});

const AllocationSchema = z.record(z.string(), AllocationStatSchema);

const ProbabilityLaneSchema = LaneSchema.extend({
  probability: z.string(),
  primary: z.boolean(),
  scope: z.string().optional(),
  ko_basis: z.string().optional(),
});

export const TuneCardDtoSchema = z.object({
  aspect: z.string(),                          // "defense" | "speed"
  kind: z.string(),                            // "survive" | "outspeed" | "ohko" | "2hko"
  member: z.string(),
  vs: z.union([z.string(), z.number()]),       // species, or a raw Speed number (outspeed)
  move: z.string().optional(),
  category: z.string().optional(),
  stat: z.string().optional(),
  probability: z.string().optional(),
  result: z.string(),                          // already | cliff | infeasible | unreachable | n/a | skipped
  delta_sp: z.number().optional(),
  need_total: z.number().optional(),
  slack_sp: z.number().optional(),
  reallocation: ReallocationSchema.optional(),
  allocation: AllocationSchema.optional(),
  selected_lane: z.string().optional(),
  combined_lane: LaneSchema.extend({ allocation: AllocationSchema.optional() }).nullable().optional(),
  allocation_lanes: z.array(LaneSchema).optional(),
  probability_lanes: z.array(ProbabilityLaneSchema).optional(),
  speed_basis: z.string().optional(),
  target_speed: z.number().optional(),
  ceiling_lane: LaneSchema.extend({
    basis: z.string().optional(),
    target_speed: z.number().optional(),
    label: z.string().optional(),
  }).nullable().optional(),
  confidence: z.string().optional(),
  note: z.string(),                            // the full human-readable cliff summary (always present)
  survive_tiers: z.array(z.object({
    hits: z.number(),
    label: z.string(),
    result: z.string(),
    delta_sp: z.number(),
    need_total: z.number().optional(),
    slack_sp: z.number().optional(),
  })).optional(),
  hp_lane: LaneSchema.nullable().optional(),
  intimidate: z.object({
    stages: z.number(),
    result: z.string(),
    delta_sp: z.number().optional(),
    need_total: z.number().optional(),
  }).nullable().optional(),
  attacker: z.object({
    source: z.string().optional(),
    ability: z.string().nullable().optional(),
    item: z.string().nullable().optional(),
    nature: z.string().nullable().optional(),
  }).passthrough().optional(),
  assumptions: z.array(z.string()).optional(),
  nature_alternatives: z.array(LaneSchema.extend({
    nature: z.string(), plus_stat: z.string().nullable().optional(), penalty_stat: z.string().nullable().optional(),
    saves_sp: z.number().optional(), unlock: z.boolean().optional(),
  })).optional(),
}).passthrough();
export type TuneCardDto = z.infer<typeof TuneCardDtoSchema>;

export const TuneResultDtoSchema = z.object({
  kind: z.literal("tune").optional(),
  format: z.string().optional(),
  cards: z.array(TuneCardDtoSchema),
  notes: z.array(z.string()).optional(),
}).passthrough();
export type TuneResultDto = z.infer<typeof TuneResultDtoSchema>;
