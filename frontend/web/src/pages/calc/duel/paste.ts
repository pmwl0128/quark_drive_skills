/** Pokepaste → roster Pokémon, shared by every calc tool's import. Name resolution is the shared
 * `membersFromPaste` (the library imports through the same path); this only turns its canonical
 * members into calculator slots. */
import type { FormatId, NatureDto } from "@pokemon-champions/protocol";
import { membersFromPaste } from "../../../lib/pasteMembers.ts";
import type { DexIndexEntry, RuntimeAdapter } from "../../../runtime/adapter.ts";
import type { ItemRef } from "../../../runtime/projection.ts";
import { type ImportOutcome } from "./TeamBar.tsx";
import { TEAM_SIZE } from "../../../lib/battle.ts";
import { makeMon, withMoves, type MonState } from "./state.ts";

export async function monsFromPaste(text: string, context: {
  dex: DexIndexEntry[];
  items: ItemRef[];
  natures: NatureDto[];
  adapter: RuntimeAdapter;
  format?: FormatId;
  /** Pin the imported builds against the environment auto-fill. */
  pin?: boolean;
}): Promise<{ mons: MonState[]; outcome: ImportOutcome }> {
  const { pin = false } = context;
  const { members, unresolved, rescaledEvs } = await membersFromPaste(text, context, TEAM_SIZE, context.format ?? "single");
  const mons = members.map(({ entry, member }) => {
    const next = makeMon(entry.slug);
    next.sps = { ...(member.spread ?? {}) };
    if (pin) next.pinned = true;
    if (member.item) next.item = member.item;
    if (member.ability) next.ability = member.ability;
    if (member.nature) next.nature = member.nature;
    if (member.moves.length) next.moves = withMoves(next, member.moves).moves;
    return next;
  });
  return { mons, outcome: { added: mons.length, unresolved, rescaledEvs } };
}
