/** Readout distributions conditional on the existing model's surviving joint-state weights.
 * This is observation information, not move strength, accuracy, safety or a recommendation. */
import { actualStat } from "../../../lib/stats.ts";
import { SP_MAX } from "../../../lib/battle.ts";
import { shownPercent, type Evidence, type FoeSpace, type Inference } from "./model.ts";

export type ObservationInformation = {
  state: "ready";
  basis: "surviving_joint_state_weights";
  readout: "foe_hp_percent" | "our_hp";
  before: number;
  readings: Array<{ after: number; probability: number }>;
  readingEntropyBits: number;
  expectedNoiseBits: number;
  informationGainBits: number;
} | { state: "pending" | "unavailable"; reason: string };

const entropy = (values: Iterable<number>, total: number): number => {
  let bits = 0;
  for (const value of values) if (value > 0) {
    const p = value / total;
    bits -= p * Math.log2(p);
  }
  return bits;
};

/** Marginalise all compatible hidden axes, rather than counting each surviving projection once.
 * A roll has its engine multiplicity; censor at KO or one-HP survival before computing entropy.
 * Full HP is required for a bulk probability: a partial percent identifies several exact current
 * HPs, and the existing model only specifies feasibility over them, not a probability law. */
export function observationInformation(space: FoeSpace, inference: Inference | null,
                                       hit: Evidence): ObservationInformation {
  if (!inference) return { state: "pending", reason: "inference_pending" };
  if (!inference.count) return { state: "unavailable", reason: "no_surviving_states" };
  const weights = inference.observationWeights;
  if (!weights) return { state: "unavailable", reason: "joint_weights_not_requested" };
  if (hit.kind === "bulk" && hit.before !== 100) {
    return { state: "unavailable", reason: "partial_hp_probability_unspecified" };
  }
  if (!Number.isInteger(hit.before) || hit.before <= 0) {
    return { state: "unavailable", reason: "invalid_before" };
  }
  const W = SP_MAX + 1, I = space.items.length, A = space.abilities.length;
  const mass = new Map<number, number>();
  let total = 0, noise = 0;
  for (const [n, nature] of space.natures.entries()) {
    for (const [i, item] of space.items.entries()) for (const [a, ability] of space.abilities.entries()) {
      const index = (n * I + i) * A + a;
      const row = hit.kind === "bulk" ? weights.bulk[hit.key][index] : weights.offense[hit.key][index];
      if (!row) return { state: "unavailable", reason: "joint_weights_incomplete" };
      const stop = hit.stopAtOne?.(item.name, ability.name) ?? false;
      for (let sp = 0; sp < W; sp++) {
        let rolls: readonly number[] | null | undefined;
        for (let h = 0; h < (hit.kind === "bulk" ? W : 1); h++) {
          const weight = row[hit.kind === "bulk" ? h * W + sp : sp]!;
          if (!(weight > 0)) continue;
          if (rolls === undefined) rolls = hit.kind === "bulk"
            ? hit.rolls(ability.name, nature.mult[hit.key], sp, item.name)
            : hit.rolls(item.name, ability.name, nature.mult[hit.key], sp);
          if (rolls === undefined) return { state: "pending", reason: "rolls_pending" };
          if (!rolls?.length || rolls.some(d => !Number.isFinite(d) || d < 0)) {
            return { state: "unavailable", reason: "rolls_incomplete" };
          }
          const hp = hit.kind === "bulk" ? actualStat(space.base.hp, "hp", h, 1) : hit.before;
          const counts = new Map<number, number>();
          for (const damage of rolls) {
            const remaining = Math.max(stop ? 1 : 0, hp - damage);
            const after = hit.kind === "bulk" ? shownPercent(remaining, hp) : remaining;
            counts.set(after, (counts.get(after) ?? 0) + 1);
          }
          total += weight;
          noise += weight * entropy(counts.values(), rolls.length);
          for (const [after, count] of counts) {
            mass.set(after, (mass.get(after) ?? 0) + weight * count / rolls.length);
          }
        }
      }
    }
  }
  if (!(total > 0) || !Number.isFinite(total)) return { state: "unavailable", reason: "zero_model_weight" };
  const readingEntropyBits = entropy(mass.values(), total), expectedNoiseBits = noise / total;
  return { state: "ready", basis: "surviving_joint_state_weights",
    readout: hit.kind === "bulk" ? "foe_hp_percent" : "our_hp", before: hit.before,
    readings: [...mass].sort(([a], [b]) => a - b).map(([after, value]) => ({ after, probability: value / total })),
    readingEntropyBits, expectedNoiseBits,
    informationGainBits: Math.max(0, readingEntropyBits - expectedNoiseBits) };
}
