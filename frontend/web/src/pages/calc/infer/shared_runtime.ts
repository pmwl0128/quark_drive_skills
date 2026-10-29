/** JSON boundary for the same inference model used by the web worker and the local calculator. */
import { bulkUse, offenseUse, frameIssue, type FrameSupportInput } from "./move_readability.ts";
import { survivesHitAtOne } from "./survival.ts";
import { projectSpeeds, type SpeedReadPlan } from "./speed_projection.ts";
import { observationInformation } from "./observation_information.ts";
import { evaluateEvidence, foeRollFits, hpShownAs, infer, mineRollFits, shownPercent,
  predictIntoFoe, predictIntoMine, possibleAfters,
  type BulkRolls, type Evidence, type FoeSpace, type OffRolls } from "./model.ts";

type RollTable = Record<string, readonly number[] | null>;
interface RecordedHit {
  id?: string;
  kind: "bulk" | "offense";
  key: "def" | "spd" | "atk" | "spa";
  before: number;
  after: number;
  rolls: RollTable;
  stopAtOne?: Record<string, boolean>;
}

export type ModelRequest =
  | { op: "frame-support"; frame: FrameSupportInput }
  | { op: "survival"; attackerAbility: string; attackerAbilityOn?: boolean;
      defenderItem: string; defenderAbility: string; defenderAbilityOn?: boolean; move: string }
  | { op: "classify"; kind: "bulk" | "offense"; move: string; category?: string }
  | { op: "hp"; current: number; max: number; before: number; after: number; damage: number;
      kind: "bulk" | "offense"; stopAtOne?: boolean }
  | { op: "infer"; space: FoeSpace; hits: RecordedHit[]; countOnly?: boolean }
  | { op: "evaluate"; space: FoeSpace; hits: RecordedHit[];
      predictions?: Array<{ id: string; hit: RecordedHit; ourHP: number }> }
  | { op: "speed"; plan: SpeedReadPlan; speeds: Array<number | null> };

/** Keys are JSON arrays to avoid collisions between item and ability names. An absent key means the
 * engine has not computed that request; an explicit null means the engine refused it. */
function evidenceOf(hit: RecordedHit): Evidence {
  const stopAtOne = (item: string, ability: string) =>
    !!hit.stopAtOne?.[JSON.stringify([item, ability])];
  if (hit.kind === "bulk") {
    const rolls: BulkRolls = (ability, mult, sp, item) =>
      hit.rolls[JSON.stringify([ability, mult, sp, item])];
    return { kind: "bulk", key: hit.key as "def" | "spd", before: hit.before, after: hit.after,
      rolls, stopAtOne };
  }
  const rolls: OffRolls = (item, ability, mult, sp) =>
    hit.rolls[JSON.stringify([item, ability, mult, sp])];
  return { kind: "offense", key: hit.key as "atk" | "spa", before: hit.before, after: hit.after,
    rolls, stopAtOne };
}

export function runModel(request: ModelRequest): unknown {
  if (request.op === "frame-support") return { reason: frameIssue(request.frame) };
  if (request.op === "survival") return { stopAtOne: survivesHitAtOne(request.attackerAbility,
    request.attackerAbilityOn, request.defenderItem, request.defenderAbility, request.defenderAbilityOn, request.move) };
  if (request.op === "speed") return projectSpeeds(request.plan, request.speeds);
  if (request.op === "classify") return request.kind === "bulk"
    ? bulkUse(request.move, request.category) : offenseUse(request.move, request.category);
  if (request.op === "hp") return {
    shown: shownPercent(request.current, request.max),
    range: hpShownAs(request.after, request.max),
    fits: request.kind === "bulk"
      ? foeRollFits(request.damage, request.max, request.before, request.after, request.stopAtOne)
      : mineRollFits(request.damage, request.before, request.after, request.stopAtOne),
  };
  if (request.op === "evaluate") {
    const result = evaluateEvidence(request.space, request.hits.map((hit, index) =>
      ({ id: hit.id ?? String(index), evidence: evidenceOf(hit) })), undefined, !!request.predictions?.length);
    const predictions = (request.predictions ?? []).map(({ id, hit, ourHP }) => {
      const e = evidenceOf(hit);
      const prediction = !result.inference ? null : e.kind === "bulk"
        ? predictIntoFoe(request.space, result.inference, e.key, e.rolls, e.stopAtOne)
        : predictIntoMine(request.space, result.inference, e.key, e.rolls, ourHP, e.stopAtOne);
      return { id, prediction, possibleAfters: possibleAfters(request.space, e),
        observation: observationInformation(request.space, result.inference, e) };
    });
    return { state: result.pending ? "pending" : !result.inference?.count ? "conflict" : "ready",
      count: result.inference?.count ?? null, priorCount: result.prior?.count ?? null,
      accepted: result.accepted, skipped: [...result.skipped],
      impossible: Object.fromEntries(result.impossible), remaining: Object.fromEntries(result.remaining),
      natures: result.inference?.natures ?? null,
      natureShares: result.inference?.natureShares ?? null,
      items: result.inference?.items ?? null, abilities: result.inference?.abilities ?? null,
      stats: result.inference?.stats ?? null, speedCaps: result.inference?.speedCaps ?? null,
      rulers: result.inference?.rulers ?? null, lanes: result.inference?.lanes ?? null,
      mostLikely: result.inference?.mostLikely ?? null,
      ...(request.predictions ? { predictions } : {}) };
  }
  const result = infer(request.space, request.hits.map(evidenceOf), !!request.countOnly);
  if (result === null) return { state: "pending" };
  return { state: result.count ? "ready" : "conflict", count: result.count,
    natures: result.natures, natureShares: result.natureShares,
    items: result.items, abilities: result.abilities, stats: result.stats,
    speedCaps: result.speedCaps, rulers: result.rulers, lanes: result.lanes,
    mostLikely: result.mostLikely };
}
