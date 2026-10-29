/** Reverse inference of an opponent's build from damage actually seen in a battle (配置反推).
 *
 * The opponent's build is a hidden state: a nature, an item and an ability out of what the
 * environment actually plays (usage >= 1%), and HP / Atk / Def / SpA / SpD SP anywhere in 0..32 with
 * the 66-point budget. Every observation is a filter over that state:
 *
 * - our move into them shows their HP as a percentage (floored; alive is never below 1%), so it
 *   constrains HP x Def (or SpD);
 * - their move into us shows our HP exactly, so it constrains their Atk (or SpA) with item and ability.
 *
 * Damage never depends on the defender's HP SP, so a move's rolls are computed once per defensive
 * stat value and the HP axis is pure arithmetic — the trick the `tune` operator uses for its HP lane.
 * A state survives when at least one of the sixteen rolls reproduces every observation; its weight is
 * the product of the fraction of rolls that do, times the environment's share of its nature, item
 * and ability. The views read the surviving set; weights shade it and give relative nature shares,
 * not a confirmation of the opponent's true nature.
 *
 * Pure: the engine is reached through the `RollsAt` callbacks, and `undefined` from one means "not
 * computed yet", which makes the whole answer pending rather than wrong. */
import { actualStat } from "../../../lib/stats.ts";

export type Mult = 0.9 | 1 | 1.1;
export type BulkKey = "def" | "spd";
export type OffKey = "atk" | "spa";
export type InferKey = BulkKey | OffKey;
export type SpKey = "hp" | InferKey;

import { SP_MAX } from "../../../lib/battle.ts";
import { SP_BUDGET } from "../../../lib/battle.ts";
const W = SP_MAX + 1;
const SP_RANGE = Array.from({ length: W }, (_, sp) => sp);
const BULK_KEYS: BulkKey[] = ["def", "spd"];
const OFF_KEYS: OffKey[] = ["atk", "spa"];
export const SP_KEYS: SpKey[] = ["hp", "atk", "def", "spa", "spd"];

// -- what the game shows --------------------------------------------------------------------

/** The opponent's HP as the game prints it: floored percent, but a Pokémon still standing never
 * reads below 1%. */
export function shownPercent(current: number, max: number): number {
  if (current <= 0) return 0;
  return Math.max(1, Math.floor((100 * current) / max));
}

const ceilDiv = (a: number, b: number) => Math.floor((a + b - 1) / b);

/** The integer HP values that print as `p`%, or null when no value of this maximum can. 100% is
 * full HP only; 0% is fainted. */
export function hpShownAs(p: number, max: number): [number, number] | null {
  if (!Number.isInteger(p) || p < 0 || p > 100 || max <= 0) return null;
  if (p === 0) return [0, 0];
  if (p === 100) return [max, max];
  const lo = p <= 1 ? 1 : ceilDiv(p * max, 100);
  const hi = Math.min(max - 1, ceilDiv((p + 1) * max, 100) - 1);
  return lo <= hi ? [lo, hi] : null;
}

/** Our move into them: does a hit of `damage` take a Pokémon of `max` HP from `before`% to `after`%? */
export function foeRollFits(damage: number, max: number, before: number, after: number, stopAtOne = false): boolean {
  const range = hpShownAs(before, max);
  if (!range) return false;
  for (let current = range[0]; current <= range[1]; current++) {
    if (shownPercent(stopAtOne && current === max ? Math.max(1, current - damage) : current - damage, max) === after) return true;
  }
  return false;
}

/** Their move into us: our HP is exact, so the hit is either exactly the difference or, on a faint,
 * at least what was left. */
export function mineRollFits(damage: number, before: number, after: number, stopAtOne = false): boolean {
  return stopAtOne ? Math.max(1, before - damage) === after
    : after > 0 ? before - damage === after : damage >= before;
}

function share(rolls: readonly number[], fits: (damage: number) => boolean): number {
  if (!rolls.length) return 0;
  let hit = 0;
  for (const roll of rolls) if (fits(roll)) hit++;
  return hit / rolls.length;
}

// -- the hidden state ----------------------------------------------------------------------

export interface SpaceNature {
  name: string;
  prior: number;
  mult: Record<InferKey, Mult>;
}

export interface SpaceOption {
  name: string;
  prior: number;
}

/** What is unknown about one opponent, already cut to the environment. */
export interface FoeSpace {
  base: Record<SpKey, number>;
  natures: SpaceNature[];
  items: SpaceOption[];
  abilities: SpaceOption[];
}

/** Rolls for our move into them, per their ability, nature multiplier on the pressed stat, and SP in
 * it. `undefined` = not computed yet; `null` = the engine refused (no information). */
export type BulkRolls = (ability: string, mult: Mult, sp: number, item: string) => readonly number[] | null | undefined;
/** Rolls for their move into us, per their item, ability, multiplier and SP in the attacking stat. */
export type OffRolls = (item: string, ability: string, mult: Mult, sp: number)
  => readonly number[] | null | undefined;

export type Evidence =
  | { kind: "bulk"; key: BulkKey; before: number; after: number; rolls: BulkRolls;
      stopAtOne?: (item: string, ability: string) => boolean }
  | { kind: "offense"; key: OffKey; before: number; after: number; rolls: OffRolls; stopAtOne?: (item: string, ability: string) => boolean };

class Pending extends Error {}
const PENDING = new Pending();

function need<T>(value: T | undefined): T {
  if (value === undefined) throw PENDING;
  return value;
}

/** HP and stat values for every SP position, per nature multiplier. */
function statTables(base: FoeSpace["base"]) {
  const hp = SP_RANGE.map((sp) => actualStat(base.hp, "hp", sp, 1));
  const stat = (key: InferKey, mult: Mult) => SP_RANGE.map((sp) => actualStat(base[key], key, sp, mult));
  const table = {} as Record<InferKey, Record<string, number[]>>;
  for (const key of [...BULK_KEYS, ...OFF_KEYS]) {
    table[key] = { "0.9": stat(key, 0.9), "1": stat(key, 1), "1.1": stat(key, 1.1) };
  }
  return { hp, stat: (key: InferKey, mult: Mult) => table[key][String(mult)]! };
}

/** Share of rolls reproducing one bulk observation, over (HP SP, stat SP): index hp * 33 + sp. */
function bulkShares(evidence: Extract<Evidence, { kind: "bulk" }>, item: string, ability: string, mult: Mult,
                    hp: number[]): Float64Array {
  const out = new Float64Array(W * W);
  for (const sp of SP_RANGE) {
    const rolls = need(evidence.rolls(ability, mult, sp, item));
    if (!rolls) continue;
    for (let h = 0; h < W; h++) {
      out[h * W + sp] = share(rolls, (damage) => foeRollFits(damage, hp[h]!, evidence.before, evidence.after,
        evidence.stopAtOne?.(item, ability)));
    }
  }
  return out;
}

function offShares(evidence: Extract<Evidence, { kind: "offense" }>, item: string, ability: string,
                   mult: Mult): Float64Array {
  const out = new Float64Array(W);
  for (const sp of SP_RANGE) {
    const rolls = need(evidence.rolls(item, ability, mult, sp));
    out[sp] = rolls ? share(rolls, (damage) => mineRollFits(damage, evidence.before, evidence.after, evidence.stopAtOne?.(item, ability))) : 0;
  }
  return out;
}

export interface StatView {
  /** Some observation constrains this stat; otherwise its range is only the budget's doing. */
  observed: boolean;
  /** Surviving SP range; lo > hi when nothing survives. */
  lo: number;
  hi: number;
  /** Weight per SP position, normalised to the largest. */
  mass: number[];
}

/** One axis of HP x Def (or SpD): the product the damage actually scales with. */
export interface Ruler {
  observed: boolean;
  /** Axis ends: 0 SP with a lowering nature, 32 / 32 with a raising one. */
  axis: [number, number];
  /** Surviving products; null when nothing survives. */
  band: [number, number] | null;
  /** Weight per bin, normalised to the largest. */
  bins: number[];
  /** Bulk-equivalent SP of the band's ends (neutral nature). */
  equivalent: [number, number] | null;
}

export interface Lane {
  /** Items giving this lane's answer (they differ in nothing this evidence can see). */
  items: string[];
  rows: Array<{ mult: Mult; flags: boolean[] }>;
  feasible: boolean;
}

export interface Inference {
  /** Surviving joint states, the budget applied. */
  count: number;
  natures: boolean[];
  /** Per nature: normalized joint-state weight (environment priors times hit likelihoods).
   * Null for an empty/zero-weight set or a count-only job; not the raw share of state counts. */
  natureShares: number[] | null;
  /** A maximum-weight complete joint state; speed is a feasible example within the leftover budget.
   * Null only for no surviving state or a count-only job. */
  mostLikely: Candidate | null;
  items: boolean[];
  abilities: boolean[];
  stats: Record<SpKey | "spe", StatView>;
  /** Remaining Speed SP cap per nature × item × ability; -1 for an excluded joint option.
   * Derived from the compatible damage axes and the same 66-SP budget, not an independent box. */
  speedCaps: number[];
  rulers: Record<BulkKey, Ruler>;
  lanes: Record<OffKey, Lane[]>;
  /** Surviving (HP SP, stat SP) per nature x item x ability: index [(n * I + i) * A + a][hp * 33 + sp]. */
  bulkProj: Record<BulkKey, Uint8Array[]>;
  /** Surviving stat SP per nature x item x ability: index [(n * I + i) * A + a][sp]. */
  offProj: Record<OffKey, Uint8Array[]>;
  /** Optional unnormalised joint-state marginals for observation planning. Kept inside the local
   * model call, not requested by the browser's ordinary inference worker. */
  observationWeights?: {
    bulk: Record<BulkKey, Float64Array[]>;
    offense: Record<OffKey, Float64Array[]>;
  };
}

const RULER_BINS = 48;

/** The best HP x stat product reachable with `total` SP between the two, neutral nature. */
function bestProduct(hp: number[], stat: number[], total: number): number {
  let best = 0;
  for (let h = Math.max(0, total - SP_MAX); h <= Math.min(SP_MAX, total); h++) {
    best = Math.max(best, hp[h]! * stat[total - h]!);
  }
  return best;
}

/** Bulk-equivalent SP: the fewest neutral-nature SP in HP + the stat that reach this product. Below
 * 0 SP (a lowering nature) is reported as 0; above 64 as 64. */
function bulkEquivalent(product: number, hp: number[], stat: number[]): number {
  for (let total = 0; total <= 2 * SP_MAX; total++) {
    if (bestProduct(hp, stat, total) >= product) return total;
  }
  return 2 * SP_MAX;
}

/** Tick positions (products) for the equivalent-SP axis. */
export function rulerTicks(space: FoeSpace, key: BulkKey, totals: number[]): number[] {
  const { hp, stat } = statTables(space.base);
  return totals.map((total) => bestProduct(hp, stat(key, 1), total));
}

/** HP x Def (or SpD) of one concrete build, on the ruler's scale. */
export function bulkProduct(space: FoeSpace, key: BulkKey, sps: Partial<Record<SpKey | "spe", number>>,
                            mult: Mult): number {
  return actualStat(space.base.hp, "hp", sps.hp ?? 0, 1) * actualStat(space.base[key], key, sps[key] ?? 0, mult);
}

function rulerAxis(space: FoeSpace, key: BulkKey): [number, number] {
  const { hp, stat } = statTables(space.base);
  return [hp[0]! * stat(key, 0.9)[0]!, hp[SP_MAX]! * stat(key, 1.1)[SP_MAX]!];
}

interface Prepared {
  hp: number[];
  stat: (key: InferKey, mult: Mult) => number[];
  /** Product of bulk shares per key, ability, multiplier; null = unobserved (all ones). */
  bulkL: Record<BulkKey, Map<string, Float64Array | null>>;
  offL: Record<OffKey, Map<string, Float64Array | null>>;
  observed: Record<SpKey, boolean>;
}

function prepare(space: FoeSpace, evidence: Evidence[]): Prepared {
  const { hp, stat } = statTables(space.base);
  const observed: Record<SpKey, boolean> = { hp: false, atk: false, def: false, spa: false, spd: false };
  const multsOf = (key: InferKey) => [...new Set(space.natures.map((n) => n.mult[key]))];
  const bulkL = { def: new Map(), spd: new Map() } as Prepared["bulkL"];
  for (const key of BULK_KEYS) {
    const list = evidence.filter((e): e is Extract<Evidence, { kind: "bulk" }> =>
      e.kind === "bulk" && e.key === key);
    if (list.length) { observed[key] = true; observed.hp = true; }
    for (const { name: item } of space.items) {
    for (const { name: ability } of space.abilities) {
      for (const mult of multsOf(key)) {
        let product: Float64Array | null = null;
        for (const e of list) {
          const shares = bulkShares(e, item, ability, mult, hp);
          if (!product) product = shares;
          else for (let at = 0; at < product.length; at++) product[at]! *= shares[at]!;
        }
        bulkL[key].set(`${item}|${ability}|${mult}`, product);
      }
    }
    }
  }
  const offL = { atk: new Map(), spa: new Map() } as Prepared["offL"];
  for (const key of OFF_KEYS) {
    const list = evidence.filter((e): e is Extract<Evidence, { kind: "offense" }> =>
      e.kind === "offense" && e.key === key);
    if (list.length) observed[key] = true;
    for (const { name: item } of space.items) {
      for (const { name: ability } of space.abilities) {
        for (const mult of multsOf(key)) {
          let product: Float64Array | null = null;
          for (const e of list) {
            const shares = offShares(e, item, ability, mult);
            if (!product) product = shares;
            else for (let at = 0; at < W; at++) product[at]! *= shares[at]!;
          }
          offL[key].set(`${item}|${ability}|${mult}`, product);
        }
      }
    }
  }
  return { hp, stat, bulkL, offL, observed };
}

const ONES2 = new Float64Array(W * W).fill(1);
const ONES1 = new Float64Array(W).fill(1);

/** Run the inference, or null while any roll it needs is still being computed. `countOnly` skips
 * every view and returns just the surviving-state count (for the narrowing history). */
export function infer(space: FoeSpace, evidence: Evidence[], countOnly = false,
                      preferred?: Candidate, withObservationWeights = false): Inference | null {
  let prep: Prepared;
  try {
    prep = prepare(space, evidence);
  } catch (error) {
    if (error === PENDING) return null;
    throw error;
  }
  const { hp, stat, bulkL, offL, observed } = prep;
  const N = space.natures.length;
  const I = space.items.length;
  const A = space.abilities.length;
  const natureOk = new Array<boolean>(N).fill(false);
  const natureMass = new Float64Array(N);
  const itemOk = new Array<boolean>(I).fill(false);
  const abilityOk = new Array<boolean>(A).fill(false);
  const mass: Record<SpKey | "spe", Float64Array> = {
    hp: new Float64Array(W), atk: new Float64Array(W), def: new Float64Array(W),
    spa: new Float64Array(W), spd: new Float64Array(W), spe: new Float64Array(W),
  };
  const axes = { def: rulerAxis(space, "def"), spd: rulerAxis(space, "spd") };
  const bins = { def: new Float64Array(RULER_BINS), spd: new Float64Array(RULER_BINS) };
  const band = { def: [Infinity, -Infinity], spd: [Infinity, -Infinity] };
  const bulkProj = { def: [] as Uint8Array[], spd: [] as Uint8Array[] };
  const offProj = { atk: [] as Uint8Array[], spa: [] as Uint8Array[] };
  const observationWeights: Inference["observationWeights"] = withObservationWeights && !countOnly
    ? { bulk: { def: [], spd: [] }, offense: { atk: [], spa: [] } } : undefined;
  const speedCaps = new Array<number>(N * I * A).fill(-1);
  // The ruler bins, hoisted out of the per-state loop below (it runs W³ times per nature and
  // ability): the same arithmetic as reading the axis each time, so the same bins.
  const [loDef, hiDef] = axes.def;
  const [loSpd, hiSpd] = axes.spd;
  const spanDef = hiDef - loDef;
  const spanSpd = hiSpd - loSpd;
  const binDef = (product: number) =>
    Math.max(0, Math.min(RULER_BINS - 1, Math.floor(((product - loDef) / spanDef) * RULER_BINS)));
  const binSpd = (product: number) =>
    Math.max(0, Math.min(RULER_BINS - 1, Math.floor(((product - loSpd) / spanSpd) * RULER_BINS)));
  let minDef = Infinity, maxDef = -Infinity, minSpd = Infinity, maxSpd = -Infinity;
  let count = 0;
  let mostLikely: Candidate | null = null;
  let bestWeight = -1, bestDistance = Infinity;
  // Only an environment template breaks equal-weight ties. The shared calculator build does not
  // participate in inference, even as a preference. The concrete example uses a feasible speed
  // allocation from the environment template, bounded by the remaining budget.
  const distance = (key: SpKey, sp: number) => Math.abs(sp - (preferred?.sps[key] ?? 0));

  const prefix = (values: Float64Array) => {
    const out = new Float64Array(SP_BUDGET + 1);
    let sum = 0;
    for (let t = 0; t <= SP_BUDGET; t++) { sum += values[t] ?? 0; out[t] = sum; }
    return out;
  };
  for (let n = 0; n < N; n++) {
    const nature = space.natures[n]!;
    const statDef = stat("def", nature.mult.def), statSpd = stat("spd", nature.mult.spd);
    for (let i = 0; i < I; i++) {
      const item = space.items[i]!;
      for (let a = 0; a < A; a++) {
        const ability = space.abilities[a]!;
        const index = (n * I + i) * A + a;
        const lp = bulkL.def.get(`${item.name}|${ability.name}|${nature.mult.def}`) ?? ONES2;
        const ls = bulkL.spd.get(`${item.name}|${ability.name}|${nature.mult.spd}`) ?? ONES2;
        const la = offL.atk.get(`${item.name}|${ability.name}|${nature.mult.atk}`) ?? ONES1;
        const ly = offL.spa.get(`${item.name}|${ability.name}|${nature.mult.spa}`) ?? ONES1;
        const prior = nature.prior * item.prior * ability.prior;
        const projDef = new Uint8Array(W * W), projSpd = new Uint8Array(W * W);
        const projAtk = new Uint8Array(W), projSpa = new Uint8Array(W);
        bulkProj.def[index] = projDef; bulkProj.spd[index] = projSpd;
        offProj.atk[index] = projAtk; offProj.spa[index] = projSpa;
        const weightedDef = observationWeights ? new Float64Array(W * W) : null;
        const weightedSpd = observationWeights ? new Float64Array(W * W) : null;
        const weightedAtk = observationWeights ? new Float64Array(W) : null;
        const weightedSpa = observationWeights ? new Float64Array(W) : null;
        if (observationWeights) {
          observationWeights.bulk.def[index] = weightedDef!;
          observationWeights.bulk.spd[index] = weightedSpd!;
          observationWeights.offense.atk[index] = weightedAtk!;
          observationWeights.offense.spa[index] = weightedSpa!;
        }
        const offCount = new Float64Array(SP_BUDGET + 1), offMass = new Float64Array(SP_BUDGET + 1);
        type OffChoice = { weight: number; distance: number; atk: number; spa: number };
        const offBest = new Array<OffChoice | null>(SP_BUDGET + 1).fill(null);
        for (let x = 0; x < W; x++) for (let y = 0; y < W; y++) {
          const weight = la[x]! * ly[y]!;
          if (weight) { offCount[x + y]!++; offMass[x + y]! += weight; }
          if (weight && !countOnly) {
            const cost = distance("atk", x) + distance("spa", y), previous = offBest[x + y];
            if (!previous || weight > previous.weight || weight === previous.weight && cost < previous.distance) {
              offBest[x + y] = { weight, distance: cost, atk: x, spa: y };
            }
          }
        }
        if (!countOnly) for (let room = 1; room <= SP_BUDGET; room++) {
          const lower = offBest[room - 1], current = offBest[room];
          if (lower && (!current || lower.weight > current.weight
            || lower.weight === current.weight && lower.distance < current.distance)) offBest[room] = lower;
        }
        const offCounts = prefix(offCount), offWeights = prefix(offMass);
        const bulkCount = new Float64Array(SP_BUDGET + 1), bulkMass = new Float64Array(SP_BUDGET + 1);
        for (let h = 0; h < W; h++) for (let d = 0; d < W; d++) {
          const wd = lp[h * W + d]!;
          if (!wd) continue;
          for (let b = 0; b < W && h + d + b <= SP_BUDGET; b++) {
            const ws = ls[h * W + b]!;
            if (!ws) continue;
            const total = h + d + b, room = SP_BUDGET - total;
            bulkCount[total]!++; bulkMass[total]! += wd * ws;
            if (!offCounts[room]) continue;
            count += offCounts[room]!;
            natureOk[n] = itemOk[i] = abilityOk[a] = true;
            if (countOnly) continue;
            // Marginalize ALL compatible attack allocations, including their likelihood and
            // item prior. Counting a surviving projection once gives a different distribution.
            const weight = prior * wd * ws * offWeights[room]!;
            if (weightedDef && weightedSpd) {
              weightedDef[h * W + d]! += weight;
              weightedSpd[h * W + b]! += weight;
            }
            natureMass[n]! += weight;
            const choice = offBest[room]!;
            const stateWeight = prior * wd * ws * choice.weight;
            const cost = distance("hp", h) + distance("def", d) + distance("spd", b) + choice.distance;
            if (stateWeight > bestWeight || stateWeight === bestWeight && cost < bestDistance) {
              bestWeight = stateWeight; bestDistance = cost;
              mostLikely = { nature: nature.name, item: item.name, ability: ability.name,
                sps: { hp: h, def: d, spd: b, atk: choice.atk, spa: choice.spa,
                  spe: Math.min(SP_MAX, preferred?.sps.spe ?? 0, room - choice.atk - choice.spa) } };
            }
            projDef[h * W + d] = 1; projSpd[h * W + b] = 1;
            mass.hp[h]! += weight; mass.def[d]! += weight; mass.spd[b]! += weight;
            const pd = hp[h]! * statDef[d]!, ps = hp[h]! * statSpd[b]!;
            bins.def[binDef(pd)]! += weight; bins.spd[binSpd(ps)]! += weight;
            minDef = Math.min(minDef, pd); maxDef = Math.max(maxDef, pd);
            minSpd = Math.min(minSpd, ps); maxSpd = Math.max(maxSpd, ps);
          }
        }
        if (countOnly) continue;
        const bulkCounts = prefix(bulkCount), bulkWeights = prefix(bulkMass);
        // Project the remaining budget onto Speed without expanding all six axes. For each
        // speed investment, sum compatible bulk totals against the attack prefix. Damage-state
        // counts and nature marginals keep their existing five-axis measure.
        for (let spe = 0; spe < W; spe++) {
          const room = SP_BUDGET - spe;
          let possible = false, weight = 0;
          for (let total = 0; total <= room; total++) {
            if (bulkCount[total] && offCounts[room - total]) possible = true;
            weight += bulkMass[total]! * offWeights[room - total]!;
          }
          if (possible) speedCaps[index] = spe;
          mass.spe[spe]! += prior * weight;
        }
        for (let x = 0; x < W; x++) for (let y = 0; y < W; y++) {
          const room = SP_BUDGET - x - y;
          if (!la[x] || !ly[y] || !bulkCounts[room]) continue;
          const weight = prior * la[x]! * ly[y]! * bulkWeights[room]!;
          if (weightedAtk && weightedSpa) { weightedAtk[x]! += weight; weightedSpa[y]! += weight; }
          projAtk[x] = 1; projSpa[y] = 1;
          mass.atk[x]! += weight; mass.spa[y]! += weight;
        }
      }
    }
  }
  band.def = [minDef, maxDef];
  band.spd = [minSpd, maxSpd];

  const statView = (key: SpKey | "spe"): StatView => {
    const row = Array.from(mass[key]);
    const top = Math.max(...row);
    const lo = row.findIndex((value) => value > 0);
    let hi = -1;
    for (let at = W - 1; at >= 0; at--) if (row[at]! > 0) { hi = at; break; }
    return { observed: key === "spe" ? evidence.length > 0 : observed[key],
      lo: lo < 0 ? 1 : lo, hi, mass: row.map((value) => top > 0 ? value / top : 0) };
  };
  const ruler = (key: BulkKey): Ruler => {
    const top = Math.max(...bins[key]);
    const found = band[key][0]! <= band[key][1]!;
    const equivalentOf = (product: number) => bulkEquivalent(product, hp, stat(key, 1));
    return {
      observed: observed[key],
      axis: axes[key],
      band: found ? [band[key][0]!, band[key][1]!] : null,
      bins: Array.from(bins[key], (value) => top > 0 ? value / top : 0),
      equivalent: found ? [equivalentOf(band[key][0]!), equivalentOf(band[key][1]!)] : null,
    };
  };
  const lanes = (key: OffKey): Lane[] => {
    if (!observed[key]) return [];
    const mults = [...new Set(space.natures.map((nature) => nature.mult[key]))].sort((x, y) => y - x);
    const groups = new Map<string, Lane>();
    space.items.forEach((item, i) => {
      const rows = mults.map((mult) => {
        const flags = new Array<boolean>(W).fill(false);
        space.natures.forEach((nature, n) => {
          if (nature.mult[key] !== mult) return;
          for (let a = 0; a < A; a++) {
            const proj = offProj[key][(n * I + i) * A + a]!;
            for (let sp = 0; sp < W; sp++) if (proj[sp]) flags[sp] = true;
          }
        });
        return { mult, flags };
      });
      const signature = JSON.stringify(rows);
      const lane = groups.get(signature);
      if (lane) lane.items.push(item.name);
      else groups.set(signature, { items: [item.name], rows, feasible: rows.some((row) => row.flags.some(Boolean)) });
    });
    return [...groups.values()].sort((x, y) => Number(y.feasible) - Number(x.feasible));
  };

  const totalNatureMass = natureMass.reduce((sum, value) => sum + value, 0);
  return {
    count,
    natures: natureOk,
    natureShares: totalNatureMass > 0 ? Array.from(natureMass, (value) => value / totalNatureMass) : null,
    mostLikely,
    items: itemOk,
    abilities: abilityOk,
    stats: { hp: statView("hp"), atk: statView("atk"), def: statView("def"),
             spa: statView("spa"), spd: statView("spd"), spe: statView("spe") },
    speedCaps,
    rulers: { def: ruler("def"), spd: ruler("spd") },
    lanes: { atk: lanes("atk"), spa: lanes("spa") },
    bulkProj,
    offProj,
    ...(observationWeights ? { observationWeights } : {}),
  };
}

/** Every "after" reading one observation could have shown, over the whole space: our HP left, or
 * their displayed percent. Null while pending. Used to explain readings outside this model. */
export function possibleAfters(space: FoeSpace, evidence: Evidence): number[] | null {
  const { hp } = statTables(space.base);
  const out = new Set<number>();
  const mults = [...new Set(space.natures.map((nature) => nature.mult[evidence.key]))];
  try {
    for (const { name: ability } of space.abilities) {
      for (const mult of mults) {
        for (const sp of SP_RANGE) {
          if (evidence.kind === "offense") {
            for (const { name: item } of space.items) {
              for (const damage of need(evidence.rolls(item, ability, mult, sp)) ?? []) {
                out.add(Math.max(evidence.stopAtOne?.(item, ability) ? 1 : 0, evidence.before - damage));
              }
            }
          } else {
            for (const { name: item } of space.items) {
            const rolls = need(evidence.rolls(ability, mult, sp, item)) ?? [];
            const stop = evidence.stopAtOne?.(item, ability);
            for (let h = 0; h < W; h++) {
              const range = hpShownAs(evidence.before, hp[h]!);
              if (!range) continue;
              for (const damage of rolls) {
                for (let current = range[0]; current <= range[1]; current++) {
                  out.add(shownPercent(stop && current === hp[h] ? Math.max(1, current - damage) : current - damage, hp[h]!));
                }
              }
            }
            }
          }
        }
      }
    }
  } catch (error) {
    if (error === PENDING) return null;
    throw error;
  }
  return [...out].sort((x, y) => x - y);
}

/** The possible readings nearest to `value`: the closest below and the closest above. */
export function nearestAfters(values: number[], value: number): number[] {
  const below = values.filter((candidate) => candidate < value).pop();
  const above = values.find((candidate) => candidate > value);
  return [below, above].filter((candidate): candidate is number => candidate !== undefined);
}

export interface EvidenceRecord { id: string; evidence: Evidence }

/** Apply the same per-hit validity rule before intersecting observations. An individually impossible
 * reading is excluded; two individually possible readings may still form a real conflict. */
export function evaluateEvidence(space: FoeSpace, records: EvidenceRecord[], preferred?: Candidate,
                                 withObservationWeights = false) {
  const impossible = new Map<string, number[]>(), skipped = new Set<string>();
  const evidence: Evidence[] = [], accepted: string[] = [];
  let pending = false;
  for (const { id, evidence: hit } of records) {
    const single = infer(space, [hit], true);
    if (!single) pending = true;
    if (single?.count === 0) {
      const values = possibleAfters(space, hit);
      if (values?.length) impossible.set(id, nearestAfters(values, hit.after));
      else skipped.add(id);
    } else { evidence.push(hit); accepted.push(id); }
  }
  const prior = infer(space, [], false, preferred, withObservationWeights && !evidence.length);
  const inference = pending ? null : evidence.length
    ? infer(space, evidence, false, preferred, withObservationWeights) : prior;
  const remaining = new Map<string, number | null>();
  if (prior?.count && inference) accepted.forEach((id, index) => {
    const count = index === accepted.length - 1 ? inference.count
      : infer(space, evidence.slice(0, index + 1), true)?.count;
    remaining.set(id, count === undefined ? null : count / prior.count);
  });
  return { prior, inference, evidence, accepted, impossible, skipped, remaining, pending };
}

// -- predictions ----------------------------------------------------------------------------

export interface Prediction {
  /** Damage range across every surviving build: percent of their HP, or our HP points. */
  lo: number;
  hi: number;
  /** Knocks out from full in every surviving build / in at least one. */
  koAll: boolean;
  koAny: boolean;
}

/** Our move into them, over their surviving bulk. Percent of their max HP. */
export function predictIntoFoe(space: FoeSpace, inference: Inference, key: BulkKey,
                               rolls: BulkRolls, stopAtOne?: (item: string, ability: string) => boolean): Prediction | "pending" | null {
  const { hp } = statTables(space.base);
  const A = space.abilities.length;
  let lo = Infinity;
  let hi = -Infinity;
  let koAll = true;
  let koAny = false;
  let seen = false;
  for (let n = 0; n < space.natures.length; n++) {
    const mult = space.natures[n]!.mult[key];
    for (let i = 0; i < space.items.length; i++) {
    for (let a = 0; a < A; a++) {
      const proj = inference.bulkProj[key][(n * space.items.length + i) * A + a];
      if (!proj) continue;
      for (let sp = 0; sp < W; sp++) {
        let row: readonly number[] | null | undefined;
        for (let h = 0; h < W; h++) {
          if (!proj[h * W + sp]) continue;
          if (row === undefined) {
            row = rolls(space.abilities[a]!.name, mult, sp, space.items[i]!.name);
            if (row === undefined) return "pending";
          }
          if (!row || !row.length) continue;
          seen = true;
          const survives = stopAtOne?.(space.items[i]!.name, space.abilities[a]!.name);
          const low = Math.min(Math.min(...row), survives ? hp[h]! - 1 : Infinity);
          const high = Math.min(Math.max(...row), survives ? hp[h]! - 1 : Infinity);
          lo = Math.min(lo, (100 * low) / hp[h]!);
          hi = Math.max(hi, (100 * high) / hp[h]!);
          if (low < hp[h]!) koAll = false;
          if (high >= hp[h]!) koAny = true;
        }
      }
    }
  }
  }
  return seen ? { lo, hi, koAll, koAny } : null;
}

/** Their move into us, over their surviving offense. Our HP points; KO against `ourHP`. */
export function predictIntoMine(space: FoeSpace, inference: Inference, key: OffKey, rolls: OffRolls,
                                ourHP: number, stopAtOne?: (item: string, ability: string) => boolean): Prediction | "pending" | null {
  const I = space.items.length;
  const A = space.abilities.length;
  let lo = Infinity;
  let hi = -Infinity;
  let koAll = true;
  let koAny = false;
  let seen = false;
  for (let n = 0; n < space.natures.length; n++) {
    const mult = space.natures[n]!.mult[key];
    for (let i = 0; i < I; i++) {
      for (let a = 0; a < A; a++) {
        const proj = inference.offProj[key][(n * I + i) * A + a];
        if (!proj) continue;
        for (let sp = 0; sp < W; sp++) {
          if (!proj[sp]) continue;
          const row = rolls(space.items[i]!.name, space.abilities[a]!.name, mult, sp);
          if (row === undefined) return "pending";
          if (!row || !row.length) continue;
          seen = true;
          const survives = stopAtOne?.(space.items[i]!.name, space.abilities[a]!.name);
          const low = Math.min(Math.min(...row), survives ? ourHP - 1 : Infinity);
          const high = Math.min(Math.max(...row), survives ? ourHP - 1 : Infinity);
          lo = Math.min(lo, low);
          hi = Math.max(hi, high);
          if (low < ourHP) koAll = false;
          if (high >= ourHP) koAny = true;
        }
      }
    }
  }
  return seen ? { lo, hi, koAll, koAny } : null;
}

// -- candidates -------------------------------------------------------------------------------

export interface Candidate {
  nature: string;
  item: string;
  ability: string;
  sps: Partial<Record<SpKey | "spe", number>>;
}

/** Per observation, the share of rolls a concrete build reproduces (0 = ruled out); null while
 * pending. `multOf` resolves the build's own nature, which need not be in the space. */
export function candidateShares(space: FoeSpace, evidence: Evidence[], candidate: Candidate,
                                multOf: (nature: string, key: InferKey) => Mult): number[] | null {
  const hpMax = actualStat(space.base.hp, "hp", candidate.sps.hp ?? 0, 1);
  const out: number[] = [];
  for (const e of evidence) {
    const sp = candidate.sps[e.key] ?? 0;
    const mult = multOf(candidate.nature, e.key);
    const rolls = e.kind === "bulk"
      ? e.rolls(candidate.ability, mult, sp, candidate.item)
      : e.rolls(candidate.item, candidate.ability, mult, sp);
    if (rolls === undefined) return null;
    out.push(!rolls ? 0 : e.kind === "bulk"
      ? share(rolls, (damage) => foeRollFits(damage, hpMax, e.before, e.after, e.stopAtOne?.(candidate.item, candidate.ability)))
      : share(rolls, (damage) => mineRollFits(damage, e.before, e.after, e.stopAtOne?.(candidate.item, candidate.ability))));
  }
  return out;
}

/** Whether a spread with nature `n` (an index into the space) survives for some item and ability.
 * Reads the per-stat projections, which factor exactly given the nature and ability. */
export function spreadSurvives(space: FoeSpace, inference: Inference, n: number,
                               sps: Partial<Record<SpKey | "spe", number>>): boolean {
  const I = space.items.length;
  const A = space.abilities.length;
  const at = (key: SpKey) => Math.max(0, Math.min(SP_MAX, sps[key] ?? 0));
  if ([...SP_KEYS, "spe" as const].some((key) => !Number.isInteger(sps[key] ?? 0)
    || (sps[key] ?? 0) < 0 || (sps[key] ?? 0) > SP_MAX)
    || Object.values(sps).reduce((sum, value) => sum + (value ?? 0), 0) > SP_BUDGET) return false;
  const hp = at("hp");
  for (let a = 0; a < A; a++) for (let i = 0; i < I; i++) {
    const index = (n * I + i) * A + a;
    if (inference.bulkProj.def[index]?.[hp * W + at("def")]
      && inference.bulkProj.spd[index]?.[hp * W + at("spd")]
      && inference.offProj.atk[index]?.[at("atk")]
      && inference.offProj.spa[index]?.[at("spa")]) return true;
  }
  return false;
}

export interface Nearest {
  nature: string;
  item: string;
  ability: string;
  sps: Record<SpKey | "spe", number>;
  /** SP moved, summed over stats. */
  cost: number;
}

/** Minimum L1 change over all six stats under the SP budget. Dynamic programming keeps
 * the same nature/item/ability when possible, then tries other natures. */
export function nearestSurviving(space: FoeSpace, inference: Inference, candidate: Candidate): Nearest | null {
  const I = space.items.length, A = space.abilities.length;
  const want = (key: SpKey | "spe") => candidate.sps[key] ?? 0;
  let best: Nearest | null = null;
  const tryNature = (n: number, i: number, a: number) => {
    const index = (n * I + i) * A + a;
    const keys = ["def", "spd", "atk", "spa", "spe"] as const;
    for (let h = 0; h < W; h++) {
      type Path = { cost: number; sps: Record<SpKey | "spe", number> };
      let paths = new Map<number, Path>([[h, { cost: Math.abs(h - want("hp")),
        sps: { hp: h, def: 0, spd: 0, atk: 0, spa: 0, spe: 0 } }]]);
      for (const key of keys) {
        const next = new Map<number, Path>();
        const proj = key === "spe" ? null : key === "def" || key === "spd"
          ? inference.bulkProj[key][index] : inference.offProj[key][index];
        const offset = key === "def" || key === "spd" ? h * W : 0;
        for (const [total, path] of paths) for (let sp = 0; sp < W && total + sp <= SP_BUDGET; sp++) {
          if (proj && !proj[offset + sp]) continue;
          const cost = path.cost + Math.abs(sp - want(key));
          if (best && cost >= best.cost) continue;
          if (!next.has(total + sp) || cost < next.get(total + sp)!.cost) {
            next.set(total + sp, { cost, sps: { ...path.sps, [key]: sp } });
          }
        }
        paths = next;
      }
      for (const path of paths.values()) if (!best || path.cost < best.cost) {
        best = { nature: space.natures[n]!.name, item: space.items[i]!.name,
          ability: space.abilities[a]!.name, ...path };
      }
    }
  };
  const n0 = space.natures.findIndex((nature) => nature.name === candidate.nature);
  const i0 = space.items.findIndex((item) => item.name === candidate.item);
  const a0 = space.abilities.findIndex((ability) => ability.name === candidate.ability);
  if (n0 >= 0 && i0 >= 0 && a0 >= 0) tryNature(n0, i0, a0);
  if (best) return best;
  for (let n = 0; n < space.natures.length; n++) {
    for (let i = 0; i < I; i++) {
      if (i0 >= 0 && i !== i0) continue;
      for (let a = 0; a < A; a++) {
        if (a0 >= 0 && a !== a0) continue;
        tryNature(n, i, a);
      }
    }
  }
  return best;
}
