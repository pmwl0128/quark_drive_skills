/** Reads and writes of the user library. Pages use these, never storage directly.
 *
 * Every write that places something — a Pokémon in a box cell, a team in a team slot — reads the
 * library and writes inside one transaction, so two writes can never claim the same cell. Records
 * are re-checked against their schema on the way in. Removals hand back what an "undo" needs to put
 * things exactly where they were. */
import type { FormatId, TeamDoc, TeamMemberDoc } from "@pokemon-champions/protocol";
import { memberBuildKey, toTeamDoc, toTeamMember, type TeamVocabulary } from "../teamDoc.ts";
import { loadLibraryVocabulary } from "./vocabulary.ts";
import { libraryTransaction, type LibraryTables } from "./storage.ts";
import { userStorage } from "../userStorage.ts";
import {
  BOX_SLOTS, BoxRecordSchema, defaultLayout, LIBRARY_SCHEMA_VERSION, LIBRARY_START, LibraryLayoutSchema,
  preferredBox, TEAM_SLOT_STEP, TeamRecordSchema,
  type BoxRecord, type LibraryLayout, type LibraryOrigin, type LibrarySnapshot, type TeamRecord,
} from "./records.ts";
import { TEAM_SIZE } from "../battle.ts";

interface SaveMeta {
  origin: LibraryOrigin;
  snapshot?: LibrarySnapshot | null;
  name?: string;
  nickname?: string;
  notes?: string;
  tags?: string[];
}

export interface BoxPosition {
  box: number;
  slot: number;
}

/** The whole library as one consistent read. */
export interface LibraryState {
  /** Session facts used at every library boundary; never persisted. */
  vocabulary?: TeamVocabulary;
  layout: LibraryLayout;
  /** By format, then slot. */
  teams: TeamRecord[];
  /** By box, then cell. */
  box: BoxRecord[];
  /** Stored rows that no longer read as a record (a newer app wrote them, or they were damaged), or
   * that claim a place another record holds. They stay in the store untouched and go out with
   * "export all", never silently dropped. */
  broken: { teams: unknown[]; box: unknown[] };
}

function newId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

let persistenceAsked = false;

/** Ask the browser to keep the library through storage pressure. Asked once, after the first real
 * write: asking on page load prompts for something the reader has not used yet. */
async function askPersistence(): Promise<void> {
  if (persistenceAsked) return;
  persistenceAsked = true;
  try {
    if (navigator.storage?.persisted && !(await navigator.storage.persisted())) {
      await navigator.storage.persist?.();
    }
  } catch {
    // Not available in this browser or context — the library still works, only best-effort.
  }
}

function readTeamRecord(row: unknown): TeamRecord | null {
  const parsed = TeamRecordSchema.safeParse(row);
  return parsed.success ? parsed.data : null;
}

function readBoxRecord(row: unknown): BoxRecord | null {
  const parsed = BoxRecordSchema.safeParse(row);
  return parsed.success ? parsed.data : null;
}

/** Records that read, first-come per place (oldest first); the rest are broken. */
function placed<T extends { createdAt: number }>(rows: unknown[], read: (row: unknown) => T | null,
  place: (record: T) => string): { records: T[]; broken: unknown[] } {
  const records: T[] = [];
  const broken: unknown[] = [];
  const taken = new Set<string>();
  const readable = rows.map((row) => ({ row, record: read(row) }));
  readable.sort((a, b) => (a.record?.createdAt ?? 0) - (b.record?.createdAt ?? 0));
  for (const { row, record } of readable) {
    if (!record || taken.has(place(record))) { broken.push(row); continue; }
    taken.add(place(record));
    records.push(record);
  }
  return { records, broken };
}

async function readState(db: LibraryTables, vocabulary?: TeamVocabulary): Promise<LibraryState> {
  const [teamRows, boxRows, layoutRow] = await Promise.all([
    db.teams.toArray(), db.box.toArray(), db.meta.get("layout"),
  ]);
  const stored = LibraryLayoutSchema.safeParse(layoutRow);
  const layout = stored.success ? stored.data : defaultLayout();
  const teams = placed(teamRows, readTeamRecord, (record) => `${record.format}:${record.slot}`);
  const box = placed(boxRows, (row) => {
    const record = readBoxRecord(row);
    const member = record && toTeamMember(record.member, vocabulary);
    return record && member ? { ...record, member } : null;
  }, (record) => `${record.box}:${record.slot}`);
  // The records decide the least that is open: a lost or damaged layout row never hides them.
  for (const record of box.records) {
    while (layout.boxes.length <= record.box) layout.boxes.push({ name: "" });
  }
  // Just enough to show the last team: slots are opened five at a time but closed one at a time, so
  // rounding up to a step would reopen a slot the reader closed.
  for (const record of teams.records) {
    layout.teamSlots[record.format] = Math.max(layout.teamSlots[record.format], record.slot + 1);
  }
  teams.records.sort((a, b) => a.format.localeCompare(b.format) * 100 + a.slot - b.slot);
  box.records.sort((a, b) => a.box * BOX_SLOTS + a.slot - (b.box * BOX_SLOTS + b.slot));
  return {
    vocabulary, layout, teams: teams.records, box: box.records,
    broken: { teams: teams.broken, box: box.broken },
  };
}

export async function loadLibrary(): Promise<LibraryState> {
  const vocabulary = await loadLibraryVocabulary();
  return libraryTransaction(false, db => readState(db, vocabulary));
}

/** One read-and-write transaction over the whole library (also used by exchange.ts). */
export async function rw<T>(work: (db: LibraryTables, state: LibraryState) => Promise<T>): Promise<T> {
  const vocabulary = await loadLibraryVocabulary();
  const result = await libraryTransaction(true, async db => work(db, await readState(db, vocabulary)));
  if (!userStorage()) void askPersistence();
  return result;
}

// ---- reading helpers (pure) ------------------------------------------------------------

export function boxById(state: Pick<LibraryState, "box">): Map<string, BoxRecord> {
  return new Map(state.box.map((record) => [record.id, record]));
}

/** A team as the skills see it: its Pokémon's builds in party order. Null when it has none left. */
export function teamDocOf(team: TeamRecord, box: Map<string, BoxRecord>): TeamDoc | null {
  if (new Set(team.members).size !== team.members.length || team.members.some((id) => !box.has(id))) return null;
  const pokemon = team.members.flatMap((id) => box.get(id)?.member ?? []);
  return pokemon.length ? toTeamDoc({ format: team.format, pokemon }) : null;
}

/** Free cells in reading order, starting at `from` and wrapping round every open box. */
export function freeCells(state: Pick<LibraryState, "box" | "layout">, from: BoxPosition = { box: 0, slot: 0 })
  : BoxPosition[] {
  const taken = new Set(state.box.map((record) => record.box * BOX_SLOTS + record.slot));
  const total = state.layout.boxes.length * BOX_SLOTS;
  const start = Math.min(from.box * BOX_SLOTS + from.slot, total - 1);
  const cells: BoxPosition[] = [];
  for (let step = 0; step < total; step += 1) {
    const index = (start + step) % total;
    if (!taken.has(index)) cells.push({ box: Math.floor(index / BOX_SLOTS), slot: index % BOX_SLOTS });
  }
  return cells;
}

export function freeTeamSlots(state: Pick<LibraryState, "teams" | "layout">, format: FormatId): number[] {
  const taken = new Set(state.teams.filter((team) => team.format === format).map((team) => team.slot));
  return Array.from({ length: state.layout.teamSlots[format] }, (_, slot) => slot)
    .filter((slot) => !taken.has(slot));
}

export function isFree(state: Pick<LibraryState, "box" | "layout">, at: BoxPosition): boolean {
  return at.box < state.layout.boxes.length && at.slot < BOX_SLOTS
    && !state.box.some((record) => record.box === at.box && record.slot === at.slot);
}

/** A team's identity for "already saved": its format and its members' builds, in any order. Builds,
 * not box ids — the box may hold two Pokémon with the same build, and a team is the same team
 * whichever of them it uses. */
function teamBuildKey(format: FormatId, members: TeamMemberDoc[]): string {
  return JSON.stringify([format, members.map(memberBuildKey).sort()]);
}

/** The stored team of `format` made of exactly these builds, if any. */
export function findSameTeam(state: Pick<LibraryState, "teams" | "box">, format: FormatId,
  members: TeamMemberDoc[]): TeamRecord | null {
  const key = teamBuildKey(format, members);
  const box = boxById(state);
  return state.teams.find((team) => team.format === format
    && team.members.every((id) => box.has(id))
    && teamBuildKey(format, team.members.map((id) => box.get(id)!.member)) === key) ?? null;
}

interface TeamSavePlan {
  /** Per member of the team: the box Pokémon with the same build it will use, or null for a new one. */
  reuse: (BoxRecord | null)[];
  /** Box cells the new Pokémon need, and how many are free. */
  needed: number;
  free: number;
  /** A team of the same format already made of the same builds. */
  duplicate: TeamRecord | null;
}

/** What saving `doc` would do: which of its Pokémon are already in the box, and what is new. */
export function planTeamSave(state: LibraryState, doc: TeamDoc): TeamSavePlan {
  doc = toTeamDoc(doc, doc.format, state.vocabulary) ?? doc;
  const used = new Set<string>();
  const reuse = doc.pokemon.map((member) => {
    const key = memberBuildKey(member);
    const hit = state.box.find((record) => !used.has(record.id) && memberBuildKey(record.member) === key);
    if (hit) used.add(hit.id);
    return hit ?? null;
  });
  const needed = reuse.filter((hit) => !hit).length;
  const duplicate = findSameTeam(state, doc.format, doc.pokemon);
  return { reuse, needed, free: freeCells(state).length, duplicate };
}

// ---- building records --------------------------------------------------------------------

export function buildBoxRecord(member: TeamMemberDoc, meta: SaveMeta, at: BoxPosition, now = Date.now(),
  vocabulary?: TeamVocabulary): BoxRecord {
  return BoxRecordSchema.parse({
    id: newId(), kind: "box", schemaVersion: LIBRARY_SCHEMA_VERSION, createdAt: now, updatedAt: now,
    nickname: meta.nickname?.trim() ?? "", notes: meta.notes ?? "", tags: meta.tags ?? [],
    origin: meta.origin, snapshot: meta.snapshot ?? null, member: toTeamMember(member, vocabulary), ...at,
  });
}

export function buildTeamRecord(format: FormatId, members: string[], meta: SaveMeta, slot: number,
  now = Date.now()): TeamRecord {
  return TeamRecordSchema.parse({
    id: newId(), kind: "team", schemaVersion: LIBRARY_SCHEMA_VERSION, createdAt: now, updatedAt: now,
    name: meta.name?.trim() ?? "", notes: meta.notes ?? "", tags: meta.tags ?? [],
    origin: meta.origin, snapshot: meta.snapshot ?? null, format, slot, members,
  });
}

/** Box Pokémon for a team's members: the same builds already in the box are reused, the rest are
 * added to free cells, the team's format's own box first. Null when the box has no room for them. */
async function placeMembers(db: LibraryTables, state: LibraryState, doc: TeamDoc, meta: SaveMeta)
  : Promise<{ ids: string[]; added: BoxRecord[] } | null> {
  const plan = planTeamSave(state, doc);
  const cells = freeCells(state, { box: preferredBox(doc.format), slot: 0 });
  if (cells.length < plan.needed) return null;
  const added: BoxRecord[] = [];
  const ids = doc.pokemon.map((member, index) => {
    const hit = plan.reuse[index];
    if (hit) return hit.id;
    const record = buildBoxRecord(member, { origin: meta.origin, snapshot: meta.snapshot }, cells[added.length]!, Date.now(), state.vocabulary);
    added.push(record);
    return record.id;
  });
  if (added.length) await db.box.bulkAdd(added);
  state.box.push(...added);
  return { ids, added };
}

// ---- teams -------------------------------------------------------------------------------

export type SaveTeamResult =
  | { status: "saved"; record: TeamRecord; replaced: TeamRecord | null; added: BoxRecord[] }
  | { status: "duplicate"; existing: TeamRecord }
  /** Every team slot of the format is taken, and no slot was named to replace. */
  | { status: "full" }
  /** The box has fewer free cells than the team's new Pokémon need. */
  | { status: "no-room"; needed: number; free: number };

/** Save a team into a team slot: the named one (replacing its team, whose Pokémon stay in the box)
 * or the first free one. Its Pokémon go into the box. The same Pokémon already saved as a team is
 * reported instead, unless the reader chose to keep a second copy. */
export function saveTeam(doc: TeamDoc, meta: SaveMeta,
  options: { slot?: number; allowDuplicate?: boolean } = {}): Promise<SaveTeamResult> {
  return rw(async (db, state) => {
    const normalized = toTeamDoc(doc, doc.format, state.vocabulary);
    if (!normalized) throw new TypeError("Invalid team document");
    doc = normalized;
    const slot = options.slot ?? freeTeamSlots(state, doc.format)[0];
    if (slot === undefined) return { status: "full" } as const;
    if (slot >= state.layout.teamSlots[doc.format]) throw new RangeError(`team slot ${slot} is not open`);
    const plan = planTeamSave(state, doc);
    if (plan.duplicate && !options.allowDuplicate) return { status: "duplicate", existing: plan.duplicate } as const;
    const placedMembers = await placeMembers(db, state, doc, meta);
    if (!placedMembers) return { status: "no-room", needed: plan.needed, free: plan.free } as const;
    const replaced = state.teams.find((team) => team.format === doc.format && team.slot === slot) ?? null;
    if (replaced) await db.teams.delete(replaced.id);
    const record = buildTeamRecord(doc.format, placedMembers.ids, meta, slot);
    await db.teams.add(record);
    return { status: "saved", record, replaced, added: placedMembers.added } as const;
  });
}

/** Take back a save: the team goes, the Pokémon it added go unless another team took them up since,
 * and a team it replaced comes back. */
export function undoSaveTeam(result: Extract<SaveTeamResult, { status: "saved" }>): Promise<void> {
  return rw(async (db, state) => {
    await db.teams.delete(result.record.id);
    const stillUsed = new Set(state.teams.filter((team) => team.id !== result.record.id)
      .flatMap((team) => team.members));
    await db.box.bulkDelete(result.added.map((record) => record.id).filter((id) => !stillUsed.has(id)));
    if (result.replaced) await db.teams.put(TeamRecordSchema.parse(result.replaced));
  });
}

export type ReviseTeamResult =
  | { status: "revised"; record: TeamRecord; original: TeamRecord; previous: TeamRecord; added: BoxRecord[] }
  /** The team is gone, the new builds are for another format, or the library cannot hold them
   * (a Pokémon or entry the dex does not know). */
  | { status: "missing" } | { status: "format" } | { status: "invalid" }
  /** The new builds are exactly the team's own: nothing to revise. */
  | { status: "unchanged" }
  /** No free slot of the format for the version being replaced. */
  | { status: "full" }
  | { status: "no-room"; needed: number; free: number };

/** Give a team new builds and keep what it was: the current members are first copied into the
 * format's first free slot under `previousName`, then the team itself takes the new builds (same
 * slot, name and notes). All or nothing: without a free slot or box room, nothing changes. */
export function reviseTeam(teamId: string, doc: TeamDoc, meta: SaveMeta, previousName: string)
  : Promise<ReviseTeamResult> {
  return rw(async (db, state) => {
    const team = state.teams.find((record) => record.id === teamId);
    if (!team) return { status: "missing" } as const;
    const next = toTeamDoc(doc, doc.format, state.vocabulary);
    if (!next) return { status: "invalid" } as const;
    if (next.format !== team.format) return { status: "format" } as const;
    const plan = planTeamSave(state, next);
    if (plan.duplicate?.id === team.id) return { status: "unchanged" } as const;
    const slot = freeTeamSlots(state, team.format)[0];
    if (slot === undefined) return { status: "full" } as const;
    if (plan.needed > plan.free) return { status: "no-room", needed: plan.needed, free: plan.free } as const;
    const previous = buildTeamRecord(team.format, team.members, {
      origin: team.origin, snapshot: team.snapshot, notes: team.notes, tags: team.tags, name: previousName,
    }, slot);
    await db.teams.add(previous);
    const placed = await placeMembers(db, state, next, meta);
    if (!placed) throw new Error("box room changed during the revision");
    const record = TeamRecordSchema.parse({
      ...team, members: placed.ids, origin: meta.origin, snapshot: meta.snapshot ?? team.snapshot, updatedAt: Date.now(),
    });
    await db.teams.put(record);
    return { status: "revised", record, original: team, previous, added: placed.added } as const;
  });
}

/** Take back a revision: the team gets its old builds again, the copy of them goes, and the Pokémon
 * the revision added go unless another team took them up since. */
export function undoReviseTeam(result: Extract<ReviseTeamResult, { status: "revised" }>): Promise<boolean> {
  return rw(async (db, state) => {
    const current = state.teams.find(team => team.id === result.record.id);
    const previous = state.teams.find(team => team.id === result.previous.id);
    const box = boxById(state);
    if (JSON.stringify(current) !== JSON.stringify(result.record)
        || JSON.stringify(previous) !== JSON.stringify(result.previous)
        || result.original.members.some(id => !box.has(id))
        || state.teams.some(team => team.id !== result.record.id && team.format === result.original.format
          && team.slot === result.original.slot)) return false;
    await db.teams.delete(result.previous.id);
    await db.teams.put(TeamRecordSchema.parse(result.original));
    const stillUsed = new Set(state.teams
      .filter((team) => team.id !== result.record.id && team.id !== result.previous.id)
      .flatMap((team) => team.members));
    for (const id of result.original.members) stillUsed.add(id);
    await db.box.bulkDelete(result.added.filter(record => !stillUsed.has(record.id)
      && JSON.stringify(box.get(record.id)) === JSON.stringify(record)).map(record => record.id));
    return true;
  });
}

export function updateTeam(id: string, patch: { name?: string; notes?: string; tags?: string[] })
  : Promise<TeamRecord | null> {
  return rw(async (db, state) => {
    const current = state.teams.find((team) => team.id === id);
    if (!current) return null;
    const next = TeamRecordSchema.parse({
      ...current, ...patch, name: (patch.name ?? current.name).trim(), updatedAt: Date.now(),
    });
    await db.teams.put(next);
    return next;
  });
}

// ---- building a team from the box -------------------------------------------------------

type TeamEditResult =
  | { ok: true; record: TeamRecord }
  /** The team already has six, already has this Pokémon, or one of the two is gone. */
  | { ok: false; reason: "full" | "present" | "missing" };

/** Put the team's Pokémon in this order (a reorder or a removal). Ids that are not in the box, and
 * repeats, are dropped: a team is up to six different box Pokémon. */
export function setTeamMembers(teamId: string, members: string[]): Promise<TeamRecord | null> {
  return rw(async (db, state) => {
    const team = state.teams.find((record) => record.id === teamId);
    if (!team) return null;
    const box = boxById(state);
    const next = TeamRecordSchema.parse({
      ...team, members: [...new Set(members)].filter((id) => box.has(id)).slice(0, TEAM_SIZE),
      updatedAt: Date.now(),
    });
    await db.teams.put(next);
    return next;
  });
}

/** Add a box Pokémon to the end of a team's party. */
export function addToTeam(teamId: string, boxId: string): Promise<TeamEditResult> {
  return rw(async (db, state) => {
    const team = state.teams.find((record) => record.id === teamId);
    if (!team || !state.box.some((record) => record.id === boxId)) return { ok: false, reason: "missing" } as const;
    if (team.members.includes(boxId)) return { ok: false, reason: "present" } as const;
    if (team.members.length >= TEAM_SIZE) return { ok: false, reason: "full" } as const;
    const record = TeamRecordSchema.parse({ ...team, members: [...team.members, boxId], updatedAt: Date.now() });
    await db.teams.put(record);
    return { ok: true, record } as const;
  });
}

/** Start a team in an empty slot with one box Pokémon. Null when the slot is taken or not open, or
 * the Pokémon is gone. */
export function startTeam(format: FormatId, slot: number, boxId: string, meta: SaveMeta): Promise<TeamRecord | null> {
  return rw(async (db, state) => {
    if (!freeTeamSlots(state, format).includes(slot)) return null;
    if (!state.box.some((record) => record.id === boxId)) return null;
    const record = buildTeamRecord(format, [boxId], meta, slot);
    await db.teams.add(record);
    return record;
  });
}

/** Move a team to another slot of its format; a team already there takes this one's place. */
export function moveTeam(id: string, slot: number): Promise<void> {
  return rw(async (db, state) => {
    const team = state.teams.find((record) => record.id === id);
    if (!team || slot === team.slot) return;
    if (slot < 0 || slot >= state.layout.teamSlots[team.format]) throw new RangeError(`team slot ${slot} is not open`);
    const other = state.teams.find((record) => record.format === team.format && record.slot === slot);
    if (other) await db.teams.put({ ...other, slot: team.slot });
    await db.teams.put({ ...team, slot });
  });
}

/** A second team of the same Pokémon in the first free slot, as the games' "copy team" does. */
export function duplicateTeam(id: string, copyName: (name: string) => string): Promise<TeamRecord | "full" | null> {
  return rw(async (db, state) => {
    const team = state.teams.find((record) => record.id === id);
    if (!team) return null;
    const slot = freeTeamSlots(state, team.format)[0];
    if (slot === undefined) return "full";
    const copy = buildTeamRecord(team.format, team.members, {
      origin: team.origin, snapshot: team.snapshot, notes: team.notes, tags: team.tags,
      name: team.name ? copyName(team.name) : "",
    }, slot);
    await db.teams.add(copy);
    return copy;
  });
}

/** Remove a team (its Pokémon stay in the box) and hand it back for an undo. */
export function removeTeam(id: string): Promise<TeamRecord | null> {
  return rw(async (db, state) => {
    const team = state.teams.find((record) => record.id === id) ?? null;
    await db.teams.delete(id);
    return team;
  });
}

/** Put a removed team back: in its own slot if still free, else the first free one. Pokémon deleted
 * in the meantime are left out. False when no slot is free. */
export function restoreTeam(record: TeamRecord): Promise<boolean> {
  return rw(async (db, state) => {
    const free = freeTeamSlots(state, record.format);
    const slot = free.includes(record.slot) ? record.slot : free[0];
    if (slot === undefined) return false;
    const box = boxById(state);
    await db.teams.put(TeamRecordSchema.parse({
      ...record, slot, members: record.members.filter((member) => box.has(member)),
    }));
    return true;
  });
}

// ---- box ---------------------------------------------------------------------------------

/** Add one Pokémon: at `at` when that cell is free, else the first free cell after it. Null when
 * every open box is full. */
export async function addToBox(member: TeamMemberDoc, meta: SaveMeta, at?: BoxPosition): Promise<BoxRecord | null> {
  const { added } = await addManyToBox([member], meta, at);
  return added[0] ?? null;
}

/** Add Pokémon in order from `at` onwards; those that find no free cell are counted, not added. */
export function addManyToBox(members: TeamMemberDoc[], meta: SaveMeta, at?: BoxPosition)
  : Promise<{ added: BoxRecord[]; left: number }> {
  return rw(async (db, state) => {
    const cells = freeCells(state, at);
    const added = members.slice(0, cells.length)
      .map((member, index) => buildBoxRecord(member, meta, cells[index]!, Date.now(), state.vocabulary));
    if (added.length) {
      await db.box.bulkAdd(added);
    }
    return { added, left: members.length - added.length };
  });
}

export function updateBoxRecord(id: string,
  patch: { nickname?: string; notes?: string; tags?: string[]; member?: TeamMemberDoc }): Promise<BoxRecord | null> {
  return rw(async (db, state) => {
    const current = state.box.find((record) => record.id === id);
    if (!current) return null;
    const next = BoxRecordSchema.parse({
      ...current, ...patch, member: toTeamMember(patch.member ?? current.member, state.vocabulary),
      nickname: (patch.nickname ?? current.nickname).trim(), updatedAt: Date.now(),
    });
    await db.box.put(next);
    return next;
  });
}

/** Move a Pokémon to another cell, in any open box; a Pokémon already there swaps places. */
export function moveBoxRecord(id: string, to: BoxPosition): Promise<void> {
  return rw(async (db, state) => {
    const entry = state.box.find((record) => record.id === id);
    if (!entry || (entry.box === to.box && entry.slot === to.slot)) return;
    if (to.box < 0 || to.box >= state.layout.boxes.length || to.slot < 0 || to.slot >= BOX_SLOTS) {
      throw new RangeError(`box cell ${to.box}:${to.slot} is not open`);
    }
    const other = state.box.find((record) => record.box === to.box && record.slot === to.slot);
    if (other) await db.box.put({ ...other, box: entry.box, slot: entry.slot });
    await db.box.put({ ...entry, ...to });
  });
}

/** Move several Pokémon at once. Every target must be an open cell that is empty or held by one of
 * the Pokémon being moved; otherwise nothing moves and false comes back. */
export function moveBoxGroup(moves: Array<{ id: string; to: BoxPosition }>): Promise<boolean> {
  return rw(async (db, state) => {
    const moving = new Set(moves.map((move) => move.id));
    const targets = new Set<string>();
    for (const { id, to } of moves) {
      if (!state.box.some((record) => record.id === id)) return false;
      if (to.box < 0 || to.box >= state.layout.boxes.length || to.slot < 0 || to.slot >= BOX_SLOTS) return false;
      const key = `${to.box}:${to.slot}`;
      if (targets.has(key)) return false;
      targets.add(key);
      const holder = state.box.find((record) => record.box === to.box && record.slot === to.slot);
      if (holder && !moving.has(holder.id)) return false;
    }
    const byId = boxById(state);
    await db.box.bulkPut(moves.map(({ id, to }) => ({ ...byId.get(id)!, ...to })));
    return true;
  });
}

interface RemovedBoxRecord {
  record: BoxRecord;
  /** The teams it was taken out of, and where in each party it stood. */
  memberships: Array<{ teamId: string; index: number }>;
}

/** Delete a Pokémon. Teams using it lose it (a team may end up empty and stays until deleted). */
export function removeBoxRecord(id: string): Promise<RemovedBoxRecord | null> {
  return rw(async (db, state) => {
    const record = state.box.find((entry) => entry.id === id);
    if (!record) return null;
    const memberships: RemovedBoxRecord["memberships"] = [];
    const now = Date.now();
    for (const team of state.teams) {
      const index = team.members.indexOf(id);
      if (index < 0) continue;
      memberships.push({ teamId: team.id, index });
      await db.teams.put({ ...team, members: team.members.filter((member) => member !== id), updatedAt: now });
    }
    await db.box.delete(id);
    return { record, memberships };
  });
}

/** Put a deleted Pokémon back — its own cell if free, else the first free one — and back into the
 * teams it left, at the same place in each party. False when the box is full. */
export function restoreBoxRecord(removed: RemovedBoxRecord): Promise<boolean> {
  return rw(async (db, state) => {
    const at = isFree(state, removed.record) ? removed.record : freeCells(state, removed.record)[0];
    if (!at) return false;
    await db.box.put(BoxRecordSchema.parse({ ...removed.record, box: at.box, slot: at.slot }));
    for (const { teamId, index } of removed.memberships) {
      const team = state.teams.find((record) => record.id === teamId);
      if (!team || team.members.length >= TEAM_SIZE || team.members.includes(removed.record.id)) continue;
      const members = [...team.members];
      members.splice(Math.min(index, members.length), 0, removed.record.id);
      await db.teams.put({ ...team, members });
    }
    return true;
  });
}

// ---- layout ------------------------------------------------------------------------------

export async function writeLayout(db: LibraryTables, layout: LibraryLayout): Promise<LibraryLayout> {
  const checked = LibraryLayoutSchema.parse(layout);
  await db.meta.put({ id: "layout", ...checked });
  return checked;
}

/** Open one more box, up to `limit` boxes. Null when the limit is reached. */
export function addBox(limit: number): Promise<LibraryLayout | null> {
  return rw(async (db, state) => {
    if (state.layout.boxes.length >= limit) return null;
    return writeLayout(db, { ...state.layout, boxes: [...state.layout.boxes, { name: "" }] });
  });
}

/** Open the next team slots of a format, up to `limit`. Null when the limit is reached. */
export function openTeamSlots(format: FormatId, limit: number): Promise<LibraryLayout | null> {
  return rw(async (db, state) => {
    const open = state.layout.teamSlots[format];
    if (open >= limit) return null;
    return writeLayout(db, {
      ...state.layout,
      teamSlots: { ...state.layout.teamSlots, [format]: Math.min(limit, open + TEAM_SLOT_STEP) },
    });
  });
}

/** Close a box opened beyond the starting ones. Its Pokémon go with it — and out of every team that
 * used them — and the boxes after it move up one place. Null for a starting box or no such box. */
export function deleteBox(index: number): Promise<LibraryLayout | null> {
  return rw(async (db, state) => {
    if (index < LIBRARY_START.boxes || index >= state.layout.boxes.length) return null;
    const gone = new Set(state.box.filter((record) => record.box === index).map((record) => record.id));
    const now = Date.now();
    for (const team of state.teams) {
      if (!team.members.some((member) => gone.has(member))) continue;
      await db.teams.put({ ...team, members: team.members.filter((member) => !gone.has(member)), updatedAt: now });
    }
    await db.box.bulkDelete([...gone]);
    const later = state.box.filter((record) => record.box > index).map((record) => ({ ...record, box: record.box - 1 }));
    if (later.length) await db.box.bulkPut(later);
    return writeLayout(db, { ...state.layout, boxes: state.layout.boxes.filter((_, at) => at !== index) });
  });
}

/** Close a team slot opened beyond the starting ones. The team in it goes (its Pokémon stay in the
 * box) and the slots after it move up one place. Null for a starting slot or no such slot. */
export function closeTeamSlot(format: FormatId, slot: number): Promise<LibraryLayout | null> {
  return rw(async (db, state) => {
    const open = state.layout.teamSlots[format];
    if (slot < LIBRARY_START.teamSlots || slot >= open) return null;
    const team = state.teams.find((record) => record.format === format && record.slot === slot);
    if (team) await db.teams.delete(team.id);
    const later = state.teams.filter((record) => record.format === format && record.slot > slot)
      .map((record) => ({ ...record, slot: record.slot - 1 }));
    if (later.length) await db.teams.bulkPut(later);
    return writeLayout(db, { ...state.layout, teamSlots: { ...state.layout.teamSlots, [format]: open - 1 } });
  });
}

export function renameBox(index: number, name: string): Promise<LibraryLayout> {
  return rw(async (db, state) => {
    const boxes = state.layout.boxes.map((box, at) => at === index ? { name: name.trim().slice(0, 24) } : box);
    return writeLayout(db, { ...state.layout, boxes });
  });
}

// ---- storage -----------------------------------------------------------------------------

export interface StorageStatus {
  persisted: boolean | null;
  usage: number | null;
  quota: number | null;
}

export async function storageStatus(): Promise<StorageStatus> {
  try {
    const [persisted, estimate] = await Promise.all([
      navigator.storage?.persisted?.() ?? Promise.resolve(null),
      navigator.storage?.estimate?.() ?? Promise.resolve(null),
    ]);
    return { persisted, usage: estimate?.usage ?? null, quota: estimate?.quota ?? null };
  } catch {
    return { persisted: null, usage: null, quota: null };
  }
}
