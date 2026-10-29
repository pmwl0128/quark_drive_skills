/**
 * llm.qa DTOs (frontend/design.md §7.2): the online fact-QA endpoint — ONE question, ONE
 * answer, no conversation history. The answer's prose follows the same entity contract as
 * agent prose (English canonical names; the SPA localizes via the dex authority), so the QA
 * page reuses the prose renderer. `toolTrace` is the server's disclosure of which
 * deterministic lookups grounded the answer; `quota` is today's usage AFTER this call so
 * the UI can show the remaining allowance without a second endpoint.
 */
import { z } from "zod";

/** Hard input cap (server enforces its own copy — this is the client-side mirror). */
export const QA_QUESTION_MAX_CHARS = 400;

export const DailyQuotaDtoSchema = z.object({
  used: z.number().int().nonnegative(),
  /** Zero means the locally hosted online rehearsal is unmetered. */
  limit: z.number().int().nonnegative(),
});
export type DailyQuotaDto = z.infer<typeof DailyQuotaDtoSchema>;

export const OnlineQuotaDtoSchema = z.object({
  qa: DailyQuotaDtoSchema,
  diagnose: DailyQuotaDtoSchema,
  builder: DailyQuotaDtoSchema,
  matchup: DailyQuotaDtoSchema,
  /** Present on deployments that expose the authoritative online tune operator. */
  tune: DailyQuotaDtoSchema.optional(),
  /** Who is asking: an anonymous visitor, or a browser the server lets past visitor limits (owner
   * dev key, tester key, smoke, a local unmetered rehearsal). Absent from older servers, which
   * means visitor. It gates allowances that live in the browser, such as library capacity. */
  access: z.enum(["visitor", "authorized"]).optional(),
});
export type OnlineQuotaDto = z.infer<typeof OnlineQuotaDtoSchema>;

/** Codes the online API puts in an error body's `detail.error.code`. Pages branch on these, never on
 * message text (design §2.1). The wire keeps an open string: a newer server's code renders the
 * generic copy on an older SPA. */
export const ONLINE_ERROR_CODES = [
  "bad_input", "unresolved_names", "unparseable", "rate_limited", "busy",
  "budget_exhausted", "tester_budget_exhausted", "llm_unavailable", "invalid_access_key",
  "not_found",
] as const;
export type OnlineErrorCode = (typeof ONLINE_ERROR_CODES)[number];

export const OnlineErrorDetailSchema = z.object({
  error: z.object({ code: z.string().min(1), message: z.string() }),
});
export type OnlineErrorDetail = z.infer<typeof OnlineErrorDetailSchema>;

export const QaRequestDtoSchema = z.object({
  question: z.string().min(1).max(QA_QUESTION_MAX_CHARS),
  /** Answer language hint (the UI's current lang); the server defaults to zh. */
  lang: z.enum(["zh", "en", "ja"]).optional(),
});
export type QaRequestDto = z.infer<typeof QaRequestDtoSchema>;

/** One deterministic lookup the server ran while answering. `label` is a short
 * human-readable summary naming entities by English canonical (prose contract). */
export const QaToolTraceEntrySchema = z.object({
  tool: z.string().min(1),
  label: z.string().min(1),
  /** Language-invariant presentation coordinates. `label` remains for older clients and
   * diagnostics; current clients localize the tool action and interpolate these facts. */
  params: z.record(z.string(), z.union([
    z.string(), z.number(), z.boolean(), z.null(),
  ])).optional(),
});
export type QaToolTraceEntry = z.infer<typeof QaToolTraceEntrySchema>;

/** An intermediate `report` event on a streamed question: the lookups run so far, and the model
 * round under way. It discloses nothing the final `toolTrace` does not. */
export const QaProgressDtoSchema = z.object({
  toolTrace: z.array(QaToolTraceEntrySchema),
  round: z.number().int().nonnegative(),
});
export type QaProgressDto = z.infer<typeof QaProgressDtoSchema>;

export const QaAnswerDtoSchema = z.object({
  answer: z.string().min(1),
  toolTrace: z.array(QaToolTraceEntrySchema),
  /** True only when at least one deterministic lookup grounded the answer (toolTrace is
   * non-empty). A `false` reply is a REFUSAL / out-of-scope decline, never a Champions fact —
   * the site promises "answers backed by deterministic queries", so an ungrounded reply must
   * not be presented as an authoritative fact (design §7.2). */
  grounded: z.boolean(),
  quota: DailyQuotaDtoSchema,
});
export type QaAnswerDto = z.infer<typeof QaAnswerDtoSchema>;

/* ---- builder wizard (llm.builder, design §7.3) ------------------------------------------
 * ONE static form -> a queued job running the full UEP gate chain server-side -> a small set of
 * compared candidates (never ranked) + correctness confirmation (validate badge + answer-audit
 * pass) + disclosed profile defaults. No mid-run questions, no history: the client holds only a
 * job id and read-only progress (§7.3 — the server's state is the single truth). */

/** Tactic vocabulary (mirrors the team skill's tactic tokens). */
export const BUILDER_TACTICS = [
  "stall", "trickroom", "weather", "tailwind", "screens", "pivot", "setup", "hazards",
] as const;
export type BuilderTactic = (typeof BUILDER_TACTICS)[number];

export const BuilderRequestDtoSchema = z.object({
  format: z.enum(["single", "double"]),
  /** Explicit form controls (§7.3 onboarding mapping): posture maps play_posture; anchor
   * maps anchor_pokemon_or_mega (absent = the user actively chose "no anchor"); owned maps
   * starting_point (non-empty = fixed pool); avoid maps availability_and_avoid; wants —
   * PREFERRED tactics (build-context `wants`) — maps anchor_theme_or_gameplan when given.
   * Everything else is a profile constant the result page discloses. */
  posture: z.enum(["offense", "balance", "defense"]),
  /** Rationale language (entities stay English canonical); server defaults to zh. */
  lang: z.enum(["zh", "en", "ja"]).optional(),
  anchor: z.string().min(1).max(100).optional(),
  owned: z.array(z.string().min(1).max(100)).max(30).optional(),
  avoid: z.array(z.string().min(1).max(100)).max(10).optional(),
  wants: z.array(z.enum(BUILDER_TACTICS)).optional(),
  /** Free-text goal in the visitor's words ("beat X", "around Y", tournament play). The server reads
   * the Pokemon it names and high-level wording as an evaluation focus; it never becomes a hard
   * constraint. */
  goal: z.string().max(300).optional(),
  /** Teams (species) earlier runs of the same brief already showed, newest first. The server draws
   * their frames less and asks for teams that differ from them; only exact dex species names count. */
  seen: z.array(z.array(z.object({
    species: z.string().min(1).max(60),
    /** A base species holding its own Mega stone is that Mega; otherwise the two are different members. */
    item: z.string().max(60).nullish(),
  })).max(6)).max(6).optional(),
  /** Optional thinking mode for the final explanation. It consumes two builder quota units. */
  thinking: z.boolean().optional(),
});
export type BuilderRequestDto = z.infer<typeof BuilderRequestDtoSchema>;

/** The server's gate timeline in execution order ("context" covers context-audit + the
 * intake done:true verification; "audit" covers checkpoint + draft + answer-audit). */
export const BUILDER_GATES = ["context", "frame", "ground", "generate", "evaluate", "audit"] as const;

/** `gate` stays an open string: a newer server may add gates an older SPA just renders
 * generically (same forward-compat stance as capabilities). `detail` is a small
 * DISCLOSURE-TRIMMED fact summary of the gate's product (species lists render as localized
 * chips; scalars as text) — never a full frame/slate artifact: those carry real-team joint
 * sets the public site must not leak (§7.1), and the client never sends any of it back. */
export const BuilderGateDtoSchema = z.object({
  gate: z.string().min(1),
  status: z.enum(["pending", "running", "done", "failed"]),
  detail: z.record(z.string(),
                   z.union([z.string(), z.number(), z.array(z.string())])).optional(),
  /** The gate's processed artifact (pruned, provenance-stripped — revised §7.1): the
   * skeletons, the grounding the model read, the assembled team json, the slate verdict.
   * Rendered as a collapsible JSON block. */
  raw: z.unknown().optional(),
});
export type BuilderGateDto = z.infer<typeof BuilderGateDtoSchema>;

/** POST /api/builder acceptance: the job is queued; quota is today's usage AFTER counting
 * this build (a failed run refunds server-side). */
export const BuilderStartDtoSchema = z.object({
  jobId: z.string().min(1),
  queuePosition: z.number().int().nonnegative(),
  quota: DailyQuotaDtoSchema,
  /** `preview`: the day's model budget is spent, so the job runs only the deterministic gates and
   * ends with a `preview` instead of a team (roadmap §1.8). Absent from older servers = build. */
  mode: z.enum(["build", "preview"]).optional(),
});
export type BuilderStartDto = z.infer<typeof BuilderStartDtoSchema>;

const MatchupThreatRouteDtoSchema = z.object({
  member: z.string().min(1),
  move: z.string().min(1),
  evidenceId: z.string().min(1),
  usageRank: z.number().int().positive().nullable(),
  opponentVariant: z.string().nullish(),
  opponentIsModal: z.boolean().nullish(),
});

const MatchupThreatOpponentDtoSchema = z.object({
  opponent: z.string().min(1),
  /** Breadth grade only: G3 affects 3+ registered members, G2 affects 2, G1 affects 1. */
  grade: z.enum(["G3", "G2", "G1"]),
  affectedMembers: z.array(z.string().min(1)).min(1),
  affectedMemberCount: z.number().int().positive(),
  usageRank: z.number().int().positive().nullable(),
  routeCount: z.number().int().positive(),
  variantCount: z.number().int().positive(),
  routes: z.array(MatchupThreatRouteDtoSchema).min(1),
});

export const MatchupThreatAssessmentDtoSchema = z.object({
  method: z.literal("guaranteed-ohko-impact-v1"),
  ordering: z.array(z.string()).min(1),
  scope: z.object({
    topK: z.number().int().positive(),
    teamSize: z.number().int().positive(),
    variantBasis: z.literal("retained_observed_variants"),
  }),
  /** The selected row is reconstructible from opponents + ordering; no composite score exists. */
  worst: z.object({
    opponent: z.string().min(1),
    grade: z.enum(["G3", "G2", "G1"]),
    basis: z.object({
      affectedMemberCount: z.number().int().positive(),
      teamSize: z.number().int().positive(),
      usageRank: z.number().int().positive().nullable(),
      routeCount: z.number().int().positive(),
    }),
  }).nullable(),
  /** Complete guaranteed-OHKO fact list, grouped by opponent and ordered by the declared rule. */
  opponents: z.array(MatchupThreatOpponentDtoSchema),
});
export type MatchupThreatAssessmentDto = z.infer<typeof MatchupThreatAssessmentDtoSchema>;

const BuilderThreatRowSchema = z.object({
  opponent: z.string(),
  member: z.string().nullish(),
  move: z.string().nullish(),
});

/** One delivered candidate, with the same facts the single-team fields carry. */
export const BuilderCandidateDtoSchema = z.object({
  label: z.string().min(1),
  team: z.unknown(),
  legality: z.object({ status: z.string().min(1), confidence: z.string().nullish() }),
  worstMatchup: BuilderThreatRowSchema.nullish(),
  threats: z.array(BuilderThreatRowSchema).optional(),
  matchupThreats: MatchupThreatAssessmentDtoSchema.optional(),
  megaCount: z.number().int().nonnegative().optional(),
  rationale: z.string().optional(),
  repaired: z.boolean(),
  /** Set on the candidate assembled around an observed answer to a goal Pokemon every earlier
   * candidate left as a hole (the earlier candidates stay as they were). */
  goalFor: z.string().optional(),
});
export type BuilderCandidateDto = z.infer<typeof BuilderCandidateDtoSchema>;

const ObservedFloorSchema = z.enum(["C0", "C1", "C2"]).nullable();

export const BuilderResultDtoSchema = z.object({
  /** team-json. Deliberately unknown-shaped at the protocol boundary: the SPA reads it with
   * the same lenient team readers the UEP panel uses (lib/team.ts) — the inner team shape is
   * the team skill's contract, not this protocol's. */
  team: z.unknown(),
  /** The validate badge (from the slate's legality gate on the recommended candidate). */
  legality: z.object({
    status: z.string().min(1),
    confidence: z.string().nullish(),
  }),
  /** Worst-matchup battery fact with calculator-ready coordinates (deep link, §13):
   * `opponent` can guaranteed-OHKO `member` with `move` within the top-K battery. Null
   * when no guaranteed OHKO threat surfaced. */
  worstMatchup: z.object({
    opponent: z.string(),
    member: z.string().nullish(),
    move: z.string().nullish(),
  }).nullish(),
  /** answer-audit outcome: a `done` job ALWAYS carries a passed audit (a failing audit
   * fails the job after the single repair) — `checked` is the audit's own counters. */
  audit: z.object({
    pass: z.boolean(),
    checked: z.record(z.string(), z.number()).optional(),
  }),
  /** Open checkpoint decisions recorded (never pausing — direct_final is a profile constant). */
  checkpointDecisions: z.number().int().nonnegative(),
  /** Disclosed profile-default keys (i18n ids; unknown keys render as-is). */
  assumptions: z.array(z.string()),
  /** The slate battery's pressure-test scope, disclosed on the result page. */
  slateTopK: z.number().int().positive(),
  /** The assembler's own build rationale (user language, English-canonical entities). */
  rationale: z.string().optional(),
  /** Every guaranteed-OHKO threat the battery surfaced (worstMatchup = threats[0]). */
  threats: z.array(z.object({
    opponent: z.string(),
    member: z.string().nullish(),
    move: z.string().nullish(),
  })).optional(),
  /** Transparent, lossless replacement for the legacy alphabetical worstMatchup/threats fields. */
  matchupThreats: MatchupThreatAssessmentDtoSchema.optional(),
  /** Mega-stone holders registered on the team (the game registers 6, picks few). */
  megaCount: z.number().int().nonnegative().optional(),
  /** True when an LLM repair round was used to reach a delivered candidate. */
  repaired: z.boolean(),
  /** Two or more delivered candidates, in assembly order (A, B ...). Absent when one team was
   * delivered; the single-team fields above always describe candidates[0]. */
  candidates: z.array(BuilderCandidateDtoSchema).min(2).optional(),
  /** Where the candidates' team floors differ within the top-K battery: every opponent that is a
   * hole (observed floor C1/C0) for at least one candidate. `null` = not a hole for that candidate
   * (observed floor C2, or the pair was not calculated). Facts side by side, no winner. */
  comparison: z.object({
    labels: z.array(z.string()),
    teamFloorDifferences: z.array(z.object({
      opponent: z.string().min(1),
      usageRank: z.number().int().positive().nullable(),
      floors: z.record(z.string(), ObservedFloorSchema),
    })),
    holeCounts: z.record(z.string(), z.number().int().nonnegative()),
    topK: z.number().int().positive(),
  }).optional(),
  /** The free-text goal and, per Pokemon it names, each candidate's observed floor against it
   * (`floors` absent when that Pokemon is outside the top-K battery). */
  goal: z.object({
    text: z.string(),
    floors: z.array(z.object({
      pokemon: z.string().min(1),
      inBattery: z.boolean(),
      /** The host's reading of the wording: the visitor wants to beat it, or wants it on the team. */
      intent: z.enum(["beat", "include"]).nullish(),
      /** False when no observed top-K build answers every retained build of it (e.g. a Focus Sash
       * build keeps every team floor at C0); absent when the index could not say. */
      answerable: z.boolean().optional(),
      floors: z.record(z.string(), ObservedFloorSchema).optional(),
    })),
  }).optional(),
  /** Assembled candidates that failed their checks after a repair and were not delivered. */
  droppedCandidates: z.number().int().nonnegative().optional(),
  /** Dev-key-only metric split (§9 benchmark); never present for real visitors. */
  debug: z.record(z.string(), z.number()).optional(),
});
export type BuilderResultDto = z.infer<typeof BuilderResultDtoSchema>;

/** The model-free outcome of a preview job: the frame skeletons and the candidate pool a build would
 * have assembled from. It names no team and passed no slate — never render it as a build result.
 * Species are English canonical; shares, confidence and provenance stay out of the public layer. */
export const BuilderPreviewDtoSchema = z.object({
  skeletons: z.array(z.object({
    /** Structural tier inside the observed group: the user's anchor, the group's backbone, or a
     * partner that recurs without binding the slot. */
    core: z.array(z.object({
      species: z.string().min(1),
      tier: z.enum(["anchor", "core", "partner"]),
    })).min(1),
    /** Slots the skeleton says nothing about. */
    openSlots: z.number().int().nonnegative(),
  })).min(1),
  /** The species a build would have been allowed to pick from (skeleton cores + ranking fill). */
  candidates: z.array(z.string().min(1)),
  megaOptions: z.array(z.string().min(1)),
});
export type BuilderPreviewDto = z.infer<typeof BuilderPreviewDtoSchema>;

export const BuilderJobDtoSchema = z.object({
  id: z.string().min(1),
  status: z.enum(["queued", "running", "done", "failed"]),
  gates: z.array(BuilderGateDtoSchema),
  queuePosition: z.number().int().nonnegative(),
  result: BuilderResultDtoSchema.optional(),
  /** Set instead of `result` when a preview job (BuilderStartDto.mode) finished. */
  preview: BuilderPreviewDtoSchema.optional(),
  /** Terminal failure code (rate_limited never reaches a job — it is a 429 at POST time):
   * no_frame / generation_failed / audit_failed / llm_unavailable / deadline / internal. */
  errorCode: z.string().optional(),
});
export type BuilderJobDto = z.infer<typeof BuilderJobDtoSchema>;
