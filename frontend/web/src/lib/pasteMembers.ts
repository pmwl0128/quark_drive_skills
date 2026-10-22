/** Pokepaste / Showdown text → canonical team members, shared by the calculator's import and the
 * library's.
 *
 * Species go through the dex index the page already holds first, then ONE resolver call for
 * whatever is left (a fuzzy or alias spelling is exactly what `resolve` is for; per-name round trips
 * are not). Items, abilities, natures and moves match the local vocabularies in any of the three
 * languages; anything unrecognised is reported back rather than guessed. */
import type { FormatId, LearnsetDto, NatureDto, TeamMemberDoc } from "@pokemon-champions/protocol";
import { readTeamCode, teamCodeFromText } from "./teamCode.ts";
import { parsePokepaste } from "./pokepaste.ts";
import type { DexIndexEntry, RuntimeAdapter } from "../runtime/adapter.ts";
import { loadLearnset, type ItemRef } from "../runtime/projection.ts";
import { megaFor } from "./lookup.ts";
import { matchName, toTeamMember } from "./teamDoc.ts";

export function matchLocal<T extends { name: string; nameZh?: string; nameJa?: string }>(
  pool: T[], raw: string | undefined): T | undefined {
  if (!raw) return undefined;
  return raw.trim() ? matchName(pool, raw) : undefined;
}

interface PasteVocabulary {
  dex: DexIndexEntry[];
  items: ItemRef[];
  natures: NatureDto[];
  adapter: RuntimeAdapter;
}

interface PastedMember {
  entry: DexIndexEntry;
  member: TeamMemberDoc;
}

export interface PasteMembersResult {
  members: PastedMember[];
  /** Names that matched nothing, in the order they appeared. */
  unresolved: string[];
  /** A mainline EV line was rescaled onto the SP budget; the importer must say so. */
  rescaledEvs: boolean;
}

export async function membersFromPaste(text: string, vocabulary: PasteVocabulary, limit = 6, codeFormat?: FormatId)
  : Promise<PasteMembersResult> {
  const { dex, items, natures, adapter } = vocabulary;
  const code = codeFormat && !matchLocal(dex, text) ? teamCodeFromText(text) : null;
  if (code && codeFormat) {
    const result = await readTeamCode(adapter, code, codeFormat);
    const members = result.team.pokemon.map(member => {
      const entry = dex.find(row => row.name === member.species);
      if (!entry) throw new Error("team-code entity unavailable in this snapshot");
      return { entry, member };
    });
    return { members, unresolved: [], rescaledEvs: false };
  }
  const { mons: pasted, rescaledEvs } = parsePokepaste(text);
  if (!pasted.length) return { members: [], unresolved: [], rescaledEvs };

  const resolved = new Map<string, DexIndexEntry>();
  const misses: string[] = [];
  for (const p of pasted) {
    const local = matchLocal(dex, p.species)
      ?? dex.find((e) => e.slug === p.species.trim().toLowerCase());
    if (local) resolved.set(p.species, local);
    else misses.push(p.species);
  }
  if (misses.length) {
    try {
      const entries = await adapter.resolve(misses, "pokemon");
      entries.forEach((entry, i) => {
        const hit = entry.ok && entry.canonical
          ? dex.find((e) => e.name === entry.canonical) : undefined;
        if (hit) resolved.set(misses[i]!, hit);
      });
    } catch (e) {
      console.error("pokepaste species resolution failed:", e);
    }
  }

  const unresolved: string[] = [];
  const members: PastedMember[] = [];
  for (const p of pasted.slice(0, limit)) {
    const entry = resolved.get(p.species);
    if (!entry) { unresolved.push(p.species); continue; }
    const item = matchLocal(items, p.item);
    if (!item && p.item) unresolved.push(p.item);
    const mega = entry.isMega ? entry : megaFor(entry.slug, item?.name ?? "", dex, items);
    const ability = matchLocal([...entry.abilities, ...(mega?.abilities ?? [])], p.ability);
    if (!ability && p.ability) unresolved.push(p.ability);
    const nature = matchLocal(natures, p.nature);
    if (!nature && p.nature) unresolved.push(p.nature);
    const moves: string[] = [];
    if (p.moves.length) {
      let pool: LearnsetDto["moves"] = [];
      try {
        pool = (await loadLearnset(entry.slug)).moves;
      } catch (e) {
        console.error(`learnset load failed for ${entry.slug}:`, e);
      }
      for (const raw of p.moves) {
        const hit = matchLocal(pool, raw);
        if (hit) moves.push(hit.name);
        else unresolved.push(raw);
      }
    }
    const member = toTeamMember({
        species: entry.name,
        item: item?.name ?? null,
        ability: ability?.name ?? null,
        nature: nature?.name ?? null,
        moves: moves.slice(0, 4),
        // No stat line at all is an unknown spread, not a deliberate all-zero one.
        spread: Object.keys(p.sps).length ? { ...p.sps } : null,
      }, { dex, items });
    if (member) members.push({ entry: mega ?? entry, member });
    else unresolved.push(p.species);
  }
  return { members, unresolved, rescaledEvs };
}
