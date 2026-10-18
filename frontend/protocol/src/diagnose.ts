/**
 * Team diagnose DTOs (design §7.5 deterministic surface): one free-form team text
 * (Showdown export or team-json) -> parse -> validate + diagnose, zero LLM. The report is
 * a DISCLOSURE-SAFE cut of the team skill's diagnose output: per-aspect facts the page
 * renders (weakness rows, hard gaps, speed order, role coverage) — not the full evidence
 * tree. The report is deterministic and rate-limited per identity; its optional reading
 * uses the shared model budget and the diagnosis allowance.
 */
import { z } from "zod";
import { OppCheckDtoSchema, OppOffenseDtoSchema } from "./matchup.ts";
import { DailyQuotaDtoSchema } from "./llm.ts";

export const DIAGNOSE_TEXT_MAX_CHARS = 8000;

export const DiagnoseRequestDtoSchema = z.object({
  format: z.enum(["single", "double"]),
  /** Showdown export text OR a team-json document — the server parses either. */
  text: z.string().min(1).max(DIAGNOSE_TEXT_MAX_CHARS),
  /** Opt-in LLM reading of the report. It uses the diagnosis allowance, not Q&A. */
  explain: z.boolean().optional(),
  /** High-effort reading. Requires explain=true and consumes two diagnosis units total. */
  thinking: z.boolean().optional(),
  lang: z.enum(["zh", "en", "ja"]).optional(),
});
export type DiagnoseRequestDto = z.infer<typeof DiagnoseRequestDtoSchema>;

const speciesList = z.array(z.string());

export const DiagnoseReportDtoSchema = z.object({
  /** The parsed team (team-json) — rendered with the same lenient readers as everywhere. */
  team: z.unknown(),
  legality: z.object({
    status: z.string().min(1),                    // valid | invalid | unknown
    confidence: z.string().nullish(),
    errors: z.array(z.string()),
    warnings: z.array(z.string()),
    /** Language-invariant validation coordinates. English strings above remain for
     * backward compatibility; the UI renders these codes at the locale boundary. */
    issues: z.array(z.object({
      severity: z.enum(["error", "warning", "skipped"]),
      code: z.string().min(1),
      params: z.record(z.string(), z.union([
        z.string(), z.number(), z.boolean(), z.null(),
      ])),
    })).default([]),
  }),
  defense: z.object({
    /** One row per attack type: which members are weak / resist / immune to it. */
    byAttackType: z.array(z.object({
      type: z.string(),
      weak: speciesList,
      resist: speciesList,
      immune: speciesList,
      neutralCount: z.number().int().nonnegative(),
    })),
  }),
  offense: z.object({
    /** Defense types the team cannot hit effectively at all. */
    hardGaps: z.array(z.string()),
    stabTypes: z.array(z.string()),
    otherTypes: z.array(z.string()),
    /** False when a counted member's moveset isn't authoritative: a listed hard gap may be a
     * phantom (that member's offense is unknown, not absent). Defaults true for old servers. */
    gapsConfirmed: z.boolean().default(true),
    /** Members skipped because their moveset isn't authoritative — the offense facts don't
     * count them, so the UI names them as the reason a gap is unconfirmed. */
    incompleteMembers: z.array(z.object({
      species: z.string(), completeness: z.string(),
    })).default([]),
  }),
  speed: z.object({
    order: z.array(z.object({ species: z.string(), speed: z.number().nullish() })),
    orderUnderTrickRoom: z.array(z.object({
      species: z.string(), speed: z.number().nullish() })),
    members: z.array(z.object({
      species: z.string(),
      baseSpeed: z.number().nullish(),
      speed: z.number().nullish(),
      speSp: z.number().nullish(),
      nature: z.string().nullish(),
      /** True when the spread was unknown and a neutral assumption was used. */
      assumedNeutral: z.boolean().nullish(),
    })),
  }),
  roles: z.object({
    coverage: z.array(z.object({
      key: z.string(),
      label: z.string(),
      present: z.boolean(),
      /** Public attention level, translated from the skill's legacy wire values:
       * `priority` = inspect a missing signal closely; `situational` = relevant only when the
       * team's plan calls for it. Neither value means that every team must carry the role.
       * NOT_CHECKED roles are omitted entirely for the format. */
      expectation: z.enum(["priority", "situational"]).optional(),
      expectationReason: z.string().optional(),
      bearers: z.array(z.object({ species: z.string(), via: z.string().nullish() })),
    })),
    /** Per-member role signals (the compression view: what each member is FOR). */
    members: z.array(z.object({
      species: z.string(),
      signals: z.array(z.string()),
    })),
    /** False when a member's moveset isn't authoritative: a "not detected" role may just be an
     * unknown move signal, not a real absence. Defaults true for old servers. */
    coverageConfirmed: z.boolean().default(true),
    /** Members whose move signals weren't counted (non-authoritative moveset). */
    incompleteMembers: z.array(z.object({
      species: z.string(), completeness: z.string(),
    })).default([]),
    /** How the attention levels above were calibrated. `reviewed` = measured on this regulation.
     * `carried_over` = the predecessor's study applies, for the length of the cross-rule handover
     * window and no longer. `unreviewed` = no level is promoted at all. Absent on old servers. */
    attentionCalibration: z.object({
      rule: z.string(),
      status: z.enum(["reviewed", "carried_over", "unreviewed"]),
      measuredOn: z.string().optional(),
      expiresAt: z.string().optional(),
    }).optional(),
  }),
  /** Per-opponent check grades vs the top-K meta (diagnose --with-check): the report's
   * most actionable table. Absent when no opponent cache exists for the format. */
  checks: z.object({
    topK: z.number().int().positive(),
    confidence: z.string(),
    byOpponent: z.array(z.object({
      opponent: z.string(),
      usageRank: z.number().int().nullish(),
      /** Weakest team answer across every calculated observed variant. */
      observedFloor: z.string().nullish(),
      /** Exact observed variants which attain the floor. */
      witnessVariantIds: z.array(z.string()),
      /** Team members providing the strongest answer on the witness variants. */
      floorBy: z.array(z.string()),
      /** Team answer to the representative (highest-coverage) variant. */
      representativeGrade: z.string().nullish(),
      /** Share of the real-team sample represented by the retained variants. */
      representedCoverage: z.number().min(0).max(1).nullish(),
      /** False means at least one retained variant lacks a calculated cell. */
      calculationComplete: z.boolean(),
      /** Per-member facts behind the roll-up (matchup-grid shaped), for the row's
       * clickable detail. Absent on pre-cells servers. */
      cells: z.array(z.object({
        member: z.string(),
        variantId: z.string(),
        coverage: z.number().min(0).max(1).nullish(),
        offense: OppOffenseDtoSchema.nullish(),
        incoming: OppOffenseDtoSchema.nullish(),
        speed: z.object({
          member: z.number().nullish(),
          opponent: z.number().nullish(),
          faster: z.string().nullish(),
        }),
        check: OppCheckDtoSchema.nullish(),
      })).default([]),
    })),
  }).optional(),
  /** Today's diagnose allowance AFTER this call (deterministic surface count). */
  quota: DailyQuotaDtoSchema,
  /** Opt-in LLM reading (grounded on this report's facts; English canonical entities). */
  explanation: z.string().optional(),
  /** Why the reading is absent when it was requested: rate_limited / budget_exhausted /
   * llm_failed / llm_unavailable. The deterministic report above is unaffected. */
  explanationError: z.string().optional(),
});
export type DiagnoseReportDto = z.infer<typeof DiagnoseReportDtoSchema>;

/** The report's facts without the online call's allowance: what a local session's diagnosis
 * (GET /api/sessions/{id}/diagnose) renders through the same report view. */
export const DiagnoseFactsDtoSchema = DiagnoseReportDtoSchema.omit({ quota: true });
export type DiagnoseFactsDto = z.infer<typeof DiagnoseFactsDtoSchema>;
