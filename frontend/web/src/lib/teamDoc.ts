/** Everything the Web UI hands around as a team becomes a `TeamDoc` here (protocol `team.ts`, the
 * team skill's `team-json`). Builder results, diagnose inputs, UEP artifacts and library imports all
 * arrive as loosely typed JSON; this is the one lenient reader that turns them into the checked
 * shape, so a stored team never depends on which page produced it.
 *
 * Lenient on the way in, strict on the way out: unknown fields are dropped, blank strings become
 * null, empty move slots disappear, and whatever survives must pass `TeamDocSchema`. A document
 * that still fails is rejected whole rather than half-stored. */
import {
  STAT_KEYS, TeamDocSchema, TeamMemberDocSchema, type FormatId, type TeamDoc, type TeamMemberDoc,
} from "@pokemon-champions/protocol";
import type { DexIndexEntry } from "../runtime/adapter.ts";
import type { ItemRef } from "../runtime/projection.ts";
import { megaFor } from "./lookup.ts";

type Named = { name: string; nameZh?: string; nameJa?: string };
export interface TeamVocabulary {
  dex: DexIndexEntry[];
  items: ItemRef[];
  abilities?: Named[];
  natures?: Named[];
  moves?: Named[];
}

const nameKey = (name: string) => name.normalize("NFKC").toLowerCase().replace(/[\s'’‘.\-]/g, "");
const nameIndexes = new WeakMap<readonly Named[], Map<string, Named>>();

/** Exact dex aliases, including punctuation variants used by Showdown. No guessed names. The first
 * row (in pool order) carrying a matching name wins. Each pool is indexed once: a full library reads
 * every stored member through here, which scanning the dex and move lists per name made take seconds. */
export function matchName<T extends Named>(pool: readonly T[], value: string): T | undefined {
  let index = nameIndexes.get(pool);
  if (!index) {
    index = new Map();
    for (const row of pool) {
      for (const name of [row.name, row.nameZh, row.nameJa]) {
        if (!name) continue;
        const key = nameKey(name);
        if (!index.has(key)) index.set(key, row);
      }
    }
    nameIndexes.set(pool, index);
  }
  return index.get(nameKey(value)) as T | undefined;
}

import { TEAM_SIZE as MAX_MEMBERS } from "./battle.ts";

function text(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

/** SP spread from either key (`spread` in team-json, `sps` in the calculator); null when absent. */
function spreadOf(raw: Record<string, unknown>): unknown {
  const source = raw.spread === undefined ? raw.sps : raw.spread;
  if (source == null) return null;
  if (typeof source !== "object" || Array.isArray(source)) return source;
  return Object.fromEntries(STAT_KEYS.filter((key) => key in source)
    .map((key) => [key, (source as Record<string, unknown>)[key]]));
}

function memberOf(value: unknown, vocabulary?: TeamVocabulary): TeamMemberDoc | null {
  if (value === null || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const species = text(raw.species);
  if (!species) return null;
  const moves = Array.isArray(raw.moves)
    ? raw.moves.flatMap((move) => text(move) ?? []) : [];
  const member = {
    species,
    item: text(raw.item),
    ability: text(raw.ability),
    moves,
    nature: text(raw.nature),
    spread: spreadOf(raw),
    ...(raw.tera !== undefined ? { tera: raw.tera } : {}),
    ...(raw.completeness != null ? { completeness: raw.completeness } : {}),
  };
  if (vocabulary) {
    const entry = matchName(vocabulary.dex, species);
    if (!entry) return null;
    member.species = entry.name;
    for (const [field, pool] of [["item", vocabulary.items], ["ability", vocabulary.abilities],
      ["nature", vocabulary.natures]] as const) {
      const value = member[field];
      if (value && pool) {
        const found = matchName(pool, value);
        if (!found) return null;
        member[field] = found.name;
      }
    }
    if (vocabulary.moves) {
      for (let i = 0; i < member.moves.length; i++) {
        const found = matchName(vocabulary.moves, member.moves[i]!);
        if (!found) return null;
        member.moves[i] = found.name;
      }
    }
    // Library builds describe the battle form: Mega species + its stone + Mega ability.
    // This also repairs old calculator exports containing base species + Mega ability.
    const mega = entry.isMega ? entry : megaFor(entry.slug, member.item ?? "", vocabulary.dex, vocabulary.items);
    if (mega) {
      member.species = mega.name;
      member.item = vocabulary.items.find((item) => item.requiredBy?.includes(mega.name))?.name ?? member.item;
      member.ability = mega.abilities[0]?.name ?? null;
    }
  }
  const parsed = TeamMemberDocSchema.safeParse(member);
  return parsed.success ? parsed.data : null;
}

/** Anything team-json shaped → a checked TeamDoc, or null when nothing usable is in it. A document
 * without its own format takes `fallbackFormat` (a page usually knows which format it is in). */
export function toTeamDoc(input: unknown, fallbackFormat?: FormatId, vocabulary?: TeamVocabulary): TeamDoc | null {
  if (input === null || typeof input !== "object") return null;
  const raw = input as Record<string, unknown>;
  const format = raw.format === "single" || raw.format === "double" ? raw.format : fallbackFormat;
  if (!format) return null;
  if (!Array.isArray(raw.pokemon) || raw.pokemon.length > MAX_MEMBERS) return null;
  const pokemon = raw.pokemon.map((member) => memberOf(member, vocabulary));
  if (pokemon.some((member) => !member)) return null;
  const provenance = raw.provenance !== null && typeof raw.provenance === "object"
    && !Array.isArray(raw.provenance) ? raw.provenance as Record<string, unknown> : null;
  const parsed = TeamDocSchema.safeParse({
    schema_version: 1, format, season: text(raw.season), rule: text(raw.rule), pokemon, provenance,
  });
  return parsed.success ? parsed.data : null;
}

/** A single member in the same lenient way (a Box entry, one calculator slot). */
export function toTeamMember(input: unknown, vocabulary?: TeamVocabulary): TeamMemberDoc | null {
  return memberOf(input, vocabulary);
}

/** Identity of a build: the same species, item, ability, nature, moves (any order) and SP. The
 * library reuses a box Pokémon with the same build instead of storing a second one; nicknames and
 * notes belong to the box entry, not to the build. */
export function memberBuildKey(member: TeamMemberDoc): string {
  const lower = (value: string | null) => value?.toLowerCase() ?? "";
  return JSON.stringify([
    member.species.toLowerCase(), lower(member.item), lower(member.ability), lower(member.nature),
    member.moves.map((move) => move.toLowerCase()).sort(),
    member.spread === null ? null : STAT_KEYS.map((key) => member.spread?.[key] ?? null),
    member.tera ?? null, member.completeness ?? null,
  ]);
}
