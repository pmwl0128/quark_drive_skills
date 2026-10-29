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
import { TuneResultDtoSchema } from "./tune.ts";
import { KoCaveatDtoSchema } from "./calc.ts";

export const DIAGNOSE_TEXT_MAX_CHARS = 8000;
export const DIAGNOSE_GOAL_MAX_CHARS = 500;
export const DIAGNOSE_FOLLOWUP_MAX_CHARS = 500;
export const DIAGNOSIS_CHANGE_FIELDS = ["moves", "item", "ability", "nature", "spread", "species"] as const;
export const DEFAULT_DIAGNOSIS_CHANGES = ["moves", "item", "ability", "nature", "spread"] as const;
export const DiagnosisChangeFieldSchema = z.enum(DIAGNOSIS_CHANGE_FIELDS);
export const DiagnosisContinuationDtoSchema = z.object({
  id: z.string().regex(/^[a-f0-9]{48}$/), revision: z.number().int().nonnegative(),
  remainingTurns: z.number().int().nonnegative(), expiresAt: z.string().datetime({ offset: true }),
});

export const DiagnoseRequestDtoSchema = z.object({
  format: z.enum(["single", "double"]),
  /** Showdown export text OR a team-json document — the server parses either. */
  text: z.string().min(1).max(DIAGNOSE_TEXT_MAX_CHARS),
  goal: z.string().max(DIAGNOSE_GOAL_MAX_CHARS).optional(),
  /** Zero-based member slots whose exact configuration must be preserved. */
  lockedSlots: z.array(z.number().int().min(0).max(5)).max(6)
    .refine((slots) => new Set(slots).size === slots.length).optional(),
  allowedChanges: z.array(DiagnosisChangeFieldSchema).max(6)
    .refine((fields) => new Set(fields).size === fields.length).optional(),
  /** Opt-in LLM reading of the report. It uses the diagnosis allowance, not Q&A. */
  explain: z.boolean().optional(),
  /** High-effort reading. Requires explain=true and consumes two diagnosis units total. */
  thinking: z.boolean().optional(),
  lang: z.enum(["zh", "en", "ja"]).optional(),
  /** Follow up on the exact original input/constraints. No client report is accepted. */
  followup: z.object({
    id: z.string().regex(/^[a-f0-9]{48}$/), revision: z.number().int().nonnegative(),
    question: z.string().trim().min(1).max(DIAGNOSE_FOLLOWUP_MAX_CHARS),
    /** Explicit opt-in for a new copy test; original locks and field permissions still apply. */
    testChange: z.boolean().optional(),
  }).strict().optional(),
});
export type DiagnoseRequestDto = z.infer<typeof DiagnoseRequestDtoSchema>;

const speciesList = z.array(z.string());

const evidenceCoordinates = {
  analysisId: z.string().regex(/^[a-f0-9]{64}$/),
  opponent: z.string().min(1),
};
export const TeamFloorRefSchema = z.object({
  scope: z.literal("team_floor"), ...evidenceCoordinates,
}).strict();
export const OpponentVariantRefSchema = z.object({
  scope: z.literal("opponent_variant"), ...evidenceCoordinates, variantId: z.string().min(1),
}).strict();
export const MemberCellRefSchema = z.object({
  scope: z.literal("member_cell"), ...evidenceCoordinates, variantId: z.string().min(1),
  memberSlot: z.number().int().min(0).max(5),
}).strict();
export const DiagnosisEvidenceRefSchema = z.discriminatedUnion("scope", [
  TeamFloorRefSchema, OpponentVariantRefSchema, MemberCellRefSchema,
]);
export type DiagnosisEvidenceRef = z.infer<typeof DiagnosisEvidenceRefSchema>;

export const DiagnosisAnalysisDtoSchema = z.object({
  id: z.string().regex(/^[a-f0-9]{64}$/),
  status: z.enum(["ready", "needs_input"]),
  format: z.enum(["single", "double"]),
  pickSize: z.union([z.literal(3), z.literal(4)]),
  snapshot: z.object({ id: z.string().min(1), season: z.string().min(1), rule: z.string().min(1) }),
  goal: z.string().max(DIAGNOSE_GOAL_MAX_CHARS),
  lockedSlots: z.array(z.number().int().min(0).max(5)).max(6),
  allowedChanges: z.array(DiagnosisChangeFieldSchema).max(6).default([...DEFAULT_DIAGNOSIS_CHANGES]),
  members: z.array(z.object({
    slot: z.number().int().min(0).max(5), species: z.string().min(1),
    completeness: z.string().nullish(),
    missingFields: z.array(z.enum(["trusted_set", "ability", "nature", "moves", "spread"])),
  })).max(6),
});

const grade = z.enum(["C0", "C1", "C2"]);
const setFields = z.object({
  species: z.string().nullish(), item: z.string().nullish(), ability: z.string().nullish(),
  nature: z.string().nullish(), moves: z.array(z.string()).nullish(),
  spread: z.record(z.string(), z.number()).nullish(),
}).strict();

export const DiagnosisTuningTargetDtoSchema = z.object({
  ref: MemberCellRefSchema, kind: z.enum(["outspeed", "survive", "ohko", "2hko"]),
  conditions: z.record(z.string(), z.union([z.string(), z.number(), z.boolean()])),
  opponentBuild: z.object({ species: z.string(), ability: z.string(), item: z.string().nullable(),
    nature: z.string(), spread: z.record(z.string(), z.number()), moves: z.array(z.string()) }),
  move: z.string().optional(), probability: z.string().optional(), hits: z.number().int().optional(),
  sourceDamageCaveats: z.array(KoCaveatDtoSchema),
  scope: z.enum(["speed_threshold", "raw_damage_threshold"]),
});
export const DiagnosisTuningDtoSchema = z.object({
  tuningId: z.string().regex(/^t1\.[a-f0-9]{24}\.[a-f0-9]{64}$/),
  analysisId: z.string().regex(/^[a-f0-9]{64}$/), memberSlot: z.number().int().min(0).max(5),
  targets: z.array(DiagnosisTuningTargetDtoSchema).min(1).max(3),
  benchmarks: z.array(z.record(z.string(), z.unknown())).min(1).max(3),
  result: TuneResultDtoSchema,
});
export type DiagnosisTuningDto = z.infer<typeof DiagnosisTuningDtoSchema>;

export const DiagnosisChangeCardDtoSchema = z.object({
  verificationId: z.string().regex(/^v1\.[a-f0-9]{24}\.[a-f0-9]{64}$/),
  analysisId: z.string().regex(/^[a-f0-9]{64}$/),
  afterAnalysisId: z.string().regex(/^[a-f0-9]{64}$/).optional(),
  status: z.enum(["tested", "invalid"]),
  changes: z.array(z.object({
    slot: z.number().int().min(0).max(5), species: z.string(), before: setFields, after: setFields,
  })).min(1).max(2),
  /** Host-owned candidate. It is never applied to the current report or the user's library. */
  candidateTeam: z.unknown(),
  legality: z.object({ status: z.enum(["valid", "invalid", "unknown"]), issues: z.array(z.object({
    severity: z.enum(["error", "warning", "skipped"]), code: z.string(),
    params: z.record(z.string(), z.union([z.string(), z.number(), z.boolean(), z.null()])),
  })) }),
  teamFloorChanges: z.array(z.object({
    opponent: z.string(), before: grade, after: grade,
    beforeRef: TeamFloorRefSchema, afterRef: TeamFloorRefSchema,
    direction: z.enum(["gain", "loss", "unknown"]),
  })),
  memberCellChanges: z.array(z.object({
    opponent: z.string(), memberSlot: z.number().int().min(0).max(5), variantId: z.string(),
    beforeMember: z.string().nullable(), afterMember: z.string().nullable(),
    before: grade.nullable(), after: grade.nullable(),
    beforeRef: MemberCellRefSchema.nullable(), afterRef: MemberCellRefSchema.nullable(),
    direction: z.enum(["gain", "loss", "unknown", "qualification"]),
    beforeContested: z.boolean(), afterContested: z.boolean(),
  })),
  coverage: z.object({
    topK: z.number().int().nonnegative(), comparableOpponents: z.number().int().nonnegative(),
    unchangedTeamFloors: z.number().int().nonnegative(), uncomparedOpponents: z.array(z.string()),
  }),
  /** Team skill `replace` diff per changed slot (shared weaknesses, coverage gaps, speeds, roles): static
   * facts of the swap, not a verdict. Absent when the skill could not diff it. */
  structureChanges: z.array(z.object({
    slot: z.number().int().min(0).max(5), out: z.string(), in: z.string(),
    sharedWeaknessWorsened: z.array(z.object({ type: z.string(), from: z.number().int(), to: z.number().int() })),
    sharedWeaknessEased: z.array(z.object({ type: z.string(), from: z.number().int(), to: z.number().int() })),
    hardGapsAdded: z.array(z.string()), hardGapsRemoved: z.array(z.string()),
    thinAdded: z.array(z.string()), thinRemoved: z.array(z.string()),
    speed: z.object({ out: z.number().int(), in: z.number().int(), delta: z.number().int() }).nullable(),
    rolesLost: z.array(z.string()), rolesLostRequired: z.array(z.string()), rolesGained: z.array(z.string()),
  })).max(2).optional(),
  hasTeamGains: z.boolean(), hasTeamLosses: z.boolean(),
  tuningCheck: z.object({
    tuningId: z.string().regex(/^t1\.[a-f0-9]{24}\.[a-f0-9]{64}$/),
    targets: z.array(DiagnosisTuningTargetDtoSchema).min(1).max(3),
    before: TuneResultDtoSchema, after: TuneResultDtoSchema, allThresholdsMet: z.boolean(),
  }).optional(),
  limits: z.array(z.enum(["static_matchups", "damage_only_changes_not_summarized"])),
});
export type DiagnosisChangeCardDto = z.infer<typeof DiagnosisChangeCardDtoSchema>;

export const DiagnosisAssistanceDtoSchema = z.object({
  toolTrace: z.array(z.object({
    tool: z.enum(["team_evidence", "opponent_builds", "member_calc", "member_options", "replacement_candidates",
      "selection_options", "tune_member", "test_change", "unknown"]),
    status: z.enum(["done", "error"]), durationMs: z.number().nonnegative(), errorCode: z.string().optional(),
    cacheHit: z.boolean().optional(),
  })).max(6),
  actions: z.array(DiagnosisChangeCardDtoSchema).max(1),
  tunings: z.array(DiagnosisTuningDtoSchema).max(1).default([]),
  notes: z.array(z.object({
    text: z.string(), verificationId: z.string().nullable(), evidenceRefs: z.array(DiagnosisEvidenceRefSchema),
  })).max(3),
  metrics: z.object({
    promptTokens: z.number().nonnegative(), completionTokens: z.number().nonnegative(),
    cacheHitTokens: z.number().nonnegative(), reasoningTokens: z.number().nonnegative().nullable(),
    usageUnknown: z.boolean(), llmCalls: z.number().int().nonnegative(), durationMs: z.number().nonnegative(),
    /** Why a reading failed, for operators; the visitor-facing code stays `explanationError`. */
    failure: z.enum(["provider_unavailable", "output_length", "malformed_output", "evidence_gate", "language",
      "rounds_exhausted", "input_too_large", "deadline", "internal"]).optional(),
    calls: z.array(z.object({
      model: z.string(), thinking: z.boolean(), promptTokens: z.number().nonnegative(),
      completionTokens: z.number().nonnegative(), cacheHitTokens: z.number().nonnegative(),
      reasoningTokens: z.number().nonnegative().nullable(), finishReason: z.string().nullable(),
      usageKnown: z.boolean(), durationMs: z.number().nonnegative(),
    })),
  }).nullable(),
});

export const DiagnosisFollowupTurnDtoSchema = z.object({
  question: z.string(), thinking: z.boolean(), explanation: z.string().optional(),
  explanationError: z.string().optional(), assistance: DiagnosisAssistanceDtoSchema.optional(),
});

export const DiagnoseReportDtoSchema = z.object({
  /** The parsed team (team-json) — rendered with the same lenient readers as everywhere. */
  team: z.unknown(),
  /** Absent only on records produced before request-bound input/evidence checks. */
  analysis: DiagnosisAnalysisDtoSchema.optional(),
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
      ref: TeamFloorRefSchema.optional(),
      variants: z.array(z.object({
        ref: OpponentVariantRefSchema, grade: z.enum(["C0", "C1", "C2"]).nullish(),
      })).optional(),
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
        ref: MemberCellRefSchema.optional(),
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
  assistance: DiagnosisAssistanceDtoSchema.optional(),
  continuation: DiagnosisContinuationDtoSchema.optional(),
  followups: z.array(DiagnosisFollowupTurnDtoSchema).default([]),
  /** Why the reading is absent when it was requested: rate_limited / budget_exhausted /
   * llm_failed / llm_unavailable. The deterministic report above is unaffected. */
  explanationError: z.string().optional(),
});
export type DiagnoseReportDto = z.infer<typeof DiagnoseReportDtoSchema>;

/** The report's facts without the online call's allowance: what a local session's diagnosis
 * (GET /api/sessions/{id}/diagnose) renders through the same report view. */
export const DiagnoseFactsDtoSchema = DiagnoseReportDtoSchema.omit({ quota: true });
export type DiagnoseFactsDto = z.infer<typeof DiagnoseFactsDtoSchema>;
