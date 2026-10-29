/** Pure inference job, executed in a dedicated worker. Requests and replies cross the boundary as
 * data; no callbacks or live roster objects survive a job generation. */
import type { DamageRequestDto, MetaDetailDto } from "@pokemon-champions/protocol";
import type { MoveRef } from "../../../runtime/projection.ts";
import { maxHPOf, natureMult, type FieldState, type MonState } from "../duel/state.ts";
import type { BuildOption } from "../../../lib/buildState.ts";
import { pressedStat, requestKey, type Rolls } from "../tune/model.ts";
import { bulkRequest, offenseRequest, liveFrame, survivesAtOne, frameIssueOf, type HitFrame,
  type Observation, type RequestKit, type SpaceInfo } from "./context.ts";
import { candidateShares, evaluateEvidence,
  predictIntoFoe, predictIntoMine, spreadSurvives, type BulkKey, type BulkRolls,
  type Candidate, type Evidence, type InferKey, type OffKey, type OffRolls } from "./model.ts";
import type { InferenceView, MoveReading } from "./useInference.ts";

export interface InferenceJob {
  kit: RequestKit;
  info: SpaceInfo;
  mine: MonState;
  foe: MonState;
  field: FieldState;
  moves: Map<string, MoveRef>;
  recorded: Array<{ observation: Observation; frame: HitFrame; key: InferKey }>;
  skipped: Set<string>;
  liveMoves: { into: Array<{ move: string; use: { key: InferKey } | { reason: "status" | "unreadable" } }>;
    from: Array<{ move: string; use: { key: InferKey } | { reason: "status" | "unreadable" } }> };
  realSets: BuildOption[];
  candidates: Candidate[];
  current: Candidate;
  detail: MetaDetailDto | null;
  rolls: Map<string, Rolls | null>;
}

export function evaluateInference(job: InferenceJob): InferenceView {
  const { kit, info, mine, foe, field, moves, recorded, liveMoves, realSets, candidates, current, detail } = job;
  const space = info.space;
  const read = (request: DamageRequestDto | null) => request ? job.rolls.get(requestKey(request)) : null;
  const damage = (request: DamageRequestDto | null) => {
    const result = read(request);
    return result === undefined ? undefined : result?.damage ?? null;
  };
  const bulkRolls = (frame: HitFrame, key: BulkKey): BulkRolls => {
    const cache = new Map<string, readonly number[] | null>();
    return (ability, mult, sp, item) => {
      const id = `${item}|${ability}|${mult}|${sp}`;
      if (cache.has(id)) return cache.get(id);
      const value = damage(bulkRequest(frame, key, ability, mult, sp, kit, item));
      if (value !== undefined) cache.set(id, value);
      return value;
    };
  };
  const offRolls = (frame: HitFrame, key: OffKey): OffRolls => {
    const cache = new Map<string, readonly number[] | null>();
    return (item, ability, mult, sp) => {
      const id = `${item}|${ability}|${mult}|${sp}`;
      if (cache.has(id)) return cache.get(id);
      const value = damage(offenseRequest(frame, key, item, ability, mult, sp, kit));
      if (value !== undefined) cache.set(id, value);
      return value;
    };
  };
  const skipped = new Set(job.skipped);
  const records: Array<{ id: string; evidence: Evidence }> = [];
  for (const { observation: o, frame, key } of recorded) {
    if (frameIssueOf(frame, o.kind, space.abilities.map(a => a.name), kit)) { skipped.add(o.id); continue; }
    const probe = o.kind === "bulk"
      ? bulkRequest(frame, key as BulkKey, space.abilities[0]?.name ?? "", space.natures[0]?.mult[key] ?? 1, 0, kit, space.items[0]?.name ?? "")
      : offenseRequest(frame, key as OffKey, space.items[0]?.name ?? "", space.abilities[0]?.name ?? "", space.natures[0]?.mult[key] ?? 1, 0, kit);
    if (read(probe)?.multiHit) { skipped.add(o.id); continue; }
    const e: Evidence = o.kind === "bulk"
      ? { kind: "bulk", key: key as BulkKey, before: o.before, after: o.after,
          rolls: bulkRolls(frame, key as BulkKey),
          stopAtOne: (item, ability) => survivesAtOne(frame.mine, frame.foe, frame.move, item, ability, kit) }
      : { kind: "offense", key: key as OffKey, before: o.before, after: o.after,
          rolls: offRolls(frame, key as OffKey),
          stopAtOne: (item, ability) => o.before === maxHPOf(frame.mine, kit.dex, kit.items)
            && survivesAtOne({ ...frame.foe, item, ability }, frame.mine, frame.move,
              frame.mine.item, frame.mine.ability, kit) };
    records.push({ id: o.id, evidence: e });
  }
  const templateIndex = realSets.reduce((best, option, index) =>
    (option.coverage ?? 0) > (realSets[best]?.coverage ?? -1) ? index : best, -1);
  const template = candidates[templateIndex];
  const screened = evaluateEvidence(space, records, template);
  const { prior, inference, evidence, impossible, remaining, pending } = screened;
  for (const id of screened.skipped) skipped.add(id);
  const ourHP = maxHPOf(mine, kit.dex, kit.items);
  const into: MoveReading[] = liveMoves.into.map(({ move, use }) => {
    if (!("key" in use)) return { move, reason: use.reason, key: null, ours: null, posterior: null, prior: null, isSpread: false };
    const key = use.key as BulkKey, frame = liveFrame(mine, foe, move, field);
    const probe = read(bulkRequest(frame, key, space.abilities[0]?.name ?? "", space.natures[0]?.mult[key] ?? 1, 0, kit, space.items[0]?.name ?? ""));
    const rolls = bulkRolls(frame, key);
    const survival = (item: string, ability: string) => survivesAtOne(mine, foe, move, item, ability, kit);
    const before = prior ? predictIntoFoe(space, prior, key, rolls, survival) : "pending";
    return { move, key, ours: moves.get(move)?.category === "Special" ? "spa" : "atk", isSpread: !!probe?.isSpread,
      reason: probe?.multiHit ? "multiHit" : before && before !== "pending" && before.hi <= 0 ? "immune" : null,
      posterior: inference ? predictIntoFoe(space, inference, key, rolls, survival) : "pending", prior: before };
  });
  const from: MoveReading[] = liveMoves.from.map(({ move, use }) => {
    if (!("key" in use)) return { move, reason: use.reason, key: null, ours: null, posterior: null, prior: null, isSpread: false };
    const key = use.key as OffKey, frame = liveFrame(mine, foe, move, field);
    const probe = read(offenseRequest(frame, key, space.items[0]?.name ?? "", space.abilities[0]?.name ?? "", space.natures[0]?.mult[key] ?? 1, 0, kit));
    const rolls = offRolls(frame, key);
    const survival = (item: string, ability: string) => survivesAtOne({ ...foe, item, ability }, mine, move,
      mine.item, mine.ability, kit);
    const before = prior ? predictIntoMine(space, prior, key, rolls, ourHP, survival) : "pending";
    return { move, key, ours: pressedStat(move, moves.get(move)?.category), isSpread: !!probe?.isSpread,
      reason: probe?.multiHit ? "multiHit" : before && before !== "pending" && before.hi <= 0 ? "immune" : null,
      posterior: inference ? predictIntoMine(space, inference, key, rolls, ourHP, survival) : "pending", prior: before };
  });
  const multOf = (nature: string, key: InferKey) => natureMult(kit.natures, nature, key);
  const sets = realSets.map((option, index) => {
    const candidate = candidates[index]!;
    const shares = pending ? null : candidateShares(space, evidence, candidate, multOf);
    const fits = !!shares && shares.every((value) => value > 0);
    return { option, candidate, shares, fits };
  });
  const spreads = inference && detail ? detail.panels.spreads.map((row) => ({
    spread: { ...row.spread }, percentage: row.percentage,
    natures: space.natures.map((_, n) => spreadSurvives(space, inference, n, row.spread)),
  })) : [];
  const assumed = !!foe.nature || Object.values(foe.sps).some((value) => (value ?? 0) > 0);
  const shares = evidence.length && assumed && !pending ? candidateShares(space, evidence, current, multOf) : null;
  return { info, inference, prior, remaining, skipped, into, from, sets, spreads, impossible,
    currentFits: shares ? shares.every((value) => value > 0) : null, loading: pending, error: false };
}
