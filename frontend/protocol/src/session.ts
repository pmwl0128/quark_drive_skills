/**
 * Local UEP session DTOs (frontend/design.md §4.2/§6): the bridge's session/artifact store as the
 * SPA consumes it. Artifacts themselves stay OPAQUE here — the store round-trips the exact
 * bytes the agent submitted (audit receipts must be recoverable verbatim), so the panel
 * renders payloads best-effort and never re-validates skill-owned shapes. The only artifact
 * the WEB side authors is the checkpoint decision, so that one gets a real schema.
 */
import { z } from "zod";

/** Artifact kinds the UEP panel knows how to label/order (design §6 gate table). The wire
 * accepts ANY kind — an agent may introduce new kinds an older panel simply renders
 * generically. `decision` is the UI-authored answer to a `checkpoint` pause. */
export const UEP_GATE_KINDS = [
  "intake", "context", "audit", "frame", "slate", "checkpoint", "decision", "draft", "answer-audit",
] as const;
export type UepGateKind = (typeof UEP_GATE_KINDS)[number];

/** One append-only ledger row (session_artifacts). */
export const LedgerEntryDtoSchema = z.object({
  sequence: z.number().int().nonnegative(),
  kind: z.string().min(1),
  artifactHash: z.string().min(1),
  createdAt: z.string().min(1),
});
export type LedgerEntryDto = z.infer<typeof LedgerEntryDtoSchema>;

/** What the daemon sees live and never stores: an operator it is running for the assistant
 * (`pcui run --session`), kept a while after a failure so the reader sees it. */
export const SessionActivityDtoSchema = z.object({
  op: z.string().min(1),
  state: z.enum(["running", "failed"]),
  startedAt: z.string().min(1),
});
export type SessionActivityDto = z.infer<typeof SessionActivityDtoSchema>;

/** An assistant blocked in `pcui session wait` for these panel artifact kinds; it counts as
 * present until `until`, which each poll pushes forward. */
export const SessionWaitingDtoSchema = z.object({
  kinds: z.array(z.string().min(1)),
  until: z.string().min(1),
});
export type SessionWaitingDto = z.infer<typeof SessionWaitingDtoSchema>;

/** Session row: current state = heads (kind -> artifact hash) + revision, updated together
 * under the store's single-row optimistic lock. */
export const SessionDtoSchema = z.object({
  id: z.string().min(1),
  createdAt: z.string().min(1),
  revision: z.number().int().nonnegative(),
  heads: z.record(z.string(), z.string()),
  meta: z.record(z.string(), z.unknown()),
  /** Evidence-derived summary; optional for compatibility with older local bridges. */
  state: z.enum(["idle", "progress", "awaiting", "questions", "needs_changes", "review", "done"]).optional(),
  updatedAt: z.string().min(1).optional(),
  /** Kind of the newest ledger entry: who moved last (the panel's `decision`, or the agent). */
  lastKind: z.string().min(1).optional(),
  /** The reader's own bookkeeping, outside the ledger. */
  title: z.string().min(1).optional(),
  archived: z.boolean().optional(),
  activity: SessionActivityDtoSchema.optional(),
  waiting: SessionWaitingDtoSchema.optional(),
  /** Sequence of the newest panel answer an assistant has collected through `pcui session wait`:
   * the panel then says it was handed over instead of asking the reader to hand it over. */
  pickedUp: z.number().int().nonnegative().optional(),
});
export type SessionDto = z.infer<typeof SessionDtoSchema>;

/** Bounded Console home cards plus exact counts; full attached teams are sent for at most four. */
export const SessionOverviewDtoSchema = z.object({
  sessions: z.array(SessionDtoSchema).max(4),
  totalCount: z.number().int().nonnegative(),
  attentionCount: z.number().int().nonnegative(),
  relatedCount: z.number().int().nonnegative(),
});
export type SessionOverviewDto = z.infer<typeof SessionOverviewDtoSchema>;

/** GET /api/sessions/{id} — the session plus its full ledger. */
export const SessionWithLedgerDtoSchema = SessionDtoSchema.extend({
  ledger: z.array(LedgerEntryDtoSchema),
});
export type SessionWithLedgerDto = z.infer<typeof SessionWithLedgerDtoSchema>;

/** Append result AND the SSE event body — one shape, emitted by the same store commit. */
export const ArtifactAppendEventDtoSchema = z.object({
  sessionId: z.string().min(1),
  kind: z.string().min(1),
  artifactHash: z.string().min(1),
  revision: z.number().int().positive(),
  sequence: z.number().int().nonnegative(),
});
export type ArtifactAppendEventDto = z.infer<typeof ArtifactAppendEventDtoSchema>;

/** The checkpoint decision the panel writes back (kind="decision") when a checkpoint
 * artifact reports pause:true — the agent reads it on its next turn (design §6). */
export const CheckpointDecisionDtoSchema = z.object({
  gate: z.literal("checkpoint"),
  /** chooseCandidate: pick slate candidate `candidateIndex`; requestChanges: `note` says
   * what to change; proceed: keep converging with the agent's own judgment. */
  action: z.enum(["chooseCandidate", "requestChanges", "proceed"]),
  candidateIndex: z.number().int().nonnegative().optional(),
  note: z.string().optional(),
  decidedAt: z.string().min(1),
}).refine((d) => d.action !== "chooseCandidate" || d.candidateIndex !== undefined, {
  message: "chooseCandidate requires candidateIndex",
});
export type CheckpointDecisionDto = z.infer<typeof CheckpointDecisionDtoSchema>;

/** Named SSE events `session` (created, retitled, archived or deleted) and `activity` (an operator
 * run or an assistant's wait began or ended): readers re-read; neither carries state itself. */
export const SessionEventDtoSchema = z.object({
  sessionId: z.string().min(1),
  change: z.enum(["created", "updated", "deleted"]).optional(),
});
export type SessionEventDto = z.infer<typeof SessionEventDtoSchema>;

export const SessionTitleRequestDtoSchema = z.object({ title: z.string().max(200).nullable() });
export const SessionArchiveRequestDtoSchema = z.object({ archived: z.boolean() });

/** GET /api/sessions/{id}/wait — the reader's next panel artifact, or a timeout to poll again. */
export const SessionWaitResultDtoSchema = z.object({
  status: z.enum(["ready", "timeout"]),
  sequence: z.number().int().nonnegative().optional(),
  kind: z.string().min(1).optional(),
  artifactHash: z.string().min(1).optional(),
  createdAt: z.string().min(1).optional(),
});

/** The reader's answers to an intake batch, written by the panel (kind="intake-answer"). The
 * assistant applies each chosen option's `maps_to` exactly as for an answer given in conversation;
 * a free-text answer is echoed back before it is committed, as the intake notes require. */
export const IntakeAnswerDtoSchema = z.object({
  gate: z.literal("intake"),
  group: z.string().optional(),
  answers: z.array(z.object({
    id: z.string().min(1),
    option: z.string().min(1).optional(),
    text: z.string().max(2000).optional(),
    skipped: z.boolean().optional(),
  })).min(1),
  note: z.string().max(2000).optional(),
  answeredAt: z.string().min(1),
});
export type IntakeAnswerDto = z.infer<typeof IntakeAnswerDtoSchema>;

/** GET /api/console — the local Console's setup card: whether an assistant can work on its
 * sessions (the companion skill, judged against what `install-agent` would write now) and
 * whether the static pages were built from the skill data being served. */
export const CompanionStatusDtoSchema = z.object({
  target: z.enum(["claude", "codex"]),
  status: z.enum(["installed", "outdated", "missing"]),
});
export const ConsoleStatusDtoSchema = z.object({
  /** A pcui invocation that works from any directory on this machine. */
  pcuiCommand: z.string().min(1),
  companion: z.array(CompanionStatusDtoSchema),
  projectionStale: z.boolean(),
});
export type ConsoleStatusDto = z.infer<typeof ConsoleStatusDtoSchema>;
