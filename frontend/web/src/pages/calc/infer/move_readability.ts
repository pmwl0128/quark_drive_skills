/** Move eligibility shared by the web inference UI and the local inference model. */
import { pressedStat } from "../tune/model.ts";
import type { InferKey } from "./model.ts";

const NOT_READABLE = new Set([
  "Super Fang", "Ruination", "Nature's Madness", "Final Gambit", "Endeavor", "Counter",
  "Mirror Coat", "Metal Burst", "Comeuppance", "Bide",
  "Brine", "Wring Out", "Crush Grip", "Hard Press", "Eruption", "Water Spout", "Dragon Energy",
  "Flail", "Reversal", "Gyro Ball", "Electro Ball", "Rollout", "Ice Ball", "Fury Cutter", "Echoed Voice",
]);
const NOT_INTO_FOE = new Set(["Foul Play"]);
const NOT_FROM_FOE = new Set(["Body Press"]);

type MoveUse = { key: InferKey } | { reason: "status" | "unreadable" };

export interface FrameSupportInput {
  kind: "bulk" | "offense";
  foeAbilities: string[];
  foeBaseHP: number;
  foeCurrentHP?: number | null;
}

/** The factorised model cannot use a hit whose damage changes with an additional hidden axis.
 * Reject the complete observation rather than silently dropping the affected ability candidate. */
export function frameIssue(input: FrameSupportInput): string | null {
  if (input.kind === "offense" && input.foeCurrentHP != null
      && input.foeAbilities.some(a => ["Blaze", "Torrent", "Overgrow", "Swarm"].includes(a))) {
    const low = input.foeBaseHP === 1 ? 1 : input.foeBaseHP + 75;
    const high = input.foeBaseHP === 1 ? 1 : low + 32;
    if (input.foeCurrentHP > Math.floor(low / 3) && input.foeCurrentHP <= Math.floor(high / 3)) {
      return "hp_trigger_hidden_hp";
    }
  }
  return null;
}

export function bulkUse(move: string, category: string | undefined): MoveUse {
  if (!move || !category || category === "Status") return { reason: "status" };
  if (NOT_READABLE.has(move) || NOT_INTO_FOE.has(move)) return { reason: "unreadable" };
  const key = pressedStat(move, category);
  return key ? { key } : { reason: "status" };
}

export function offenseUse(move: string, category: string | undefined): MoveUse {
  if (!move || !category || category === "Status") return { reason: "status" };
  if (NOT_READABLE.has(move) || NOT_FROM_FOE.has(move)) return { reason: "unreadable" };
  return { key: category === "Physical" ? "atk" : "spa" };
}
