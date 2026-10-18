import "fake-indexeddb/auto";
import type { TeamMemberDoc } from "@pokemon-champions/protocol";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// These tests exercise placement independently of deployment data. Canonicalization has its own
// dex-backed contract cases in teamDoc.test.ts.
vi.mock("./vocabulary.ts", () => ({ loadLibraryVocabulary: async () => undefined }));
import { toTeamDoc } from "../teamDoc.ts";
import { LibraryDb, setLibraryDbForTests } from "./db.ts";
import { applyImport, exportAll, planImport } from "./exchange.ts";
import { BOX_SLOTS, LIBRARY_START, LIBRARY_TIERS } from "./records.ts";
import {
  addManyToBox, addToBox, addToTeam, boxById, deleteBox, closeTeamSlot, duplicateTeam, loadLibrary, moveBoxRecord,
  moveBoxGroup, moveTeam, addBox,
  openTeamSlots, removeBoxRecord, restoreBoxRecord, reviseTeam, saveTeam, setTeamMembers, startTeam,
  teamDocOf, undoReviseTeam, undoSaveTeam, updateTeam, type SaveTeamResult,
} from "./repo.ts";

const mon = (species: string, item: string | null = null): TeamMemberDoc => ({
  species, item, ability: null, nature: null, moves: ["Protect"], spread: null,
});
const team = (format: "single" | "double", ...species: string[]) => toTeamDoc({
  format, pokemon: species.map((name) => mon(name)),
})!;
const saved = (result: SaveTeamResult) => {
  if (result.status !== "saved") throw new Error(result.status);
  return result;
};

let db: LibraryDb;
let serial = 0;

beforeEach(() => {
  db = new LibraryDb(`library-test-${++serial}`);
  setLibraryDbForTests(db);
});

afterEach(async () => {
  setLibraryDbForTests(null);
  await db.delete();
});

describe("teams and the box", () => {
  it("refuses a stale revision undo and preserves subsequent edits and slot placement", async () => {
    const first = saved(await saveTeam(team("double", "Garchomp"), {origin:"manual", name:"original"}));
    const revised = await reviseTeam(first.record.id, team("double", "Incineroar"), {origin:"session"}, "before");
    if (revised.status !== "revised") throw new Error(revised.status);
    await updateTeam(first.record.id, {name:"edited", notes:"keep this"});
    expect(await undoReviseTeam(revised)).toBe(false);
    expect((await loadLibrary()).teams.find(t => t.id === first.record.id))
      .toMatchObject({name:"edited", notes:"keep this"});
    await moveTeam(first.record.id, 2);
    await saveTeam(team("double", "Whimsicott"), {origin:"manual"}, {slot:0});
    expect(await undoReviseTeam(revised)).toBe(false);
    const state = await loadLibrary();
    expect(state.broken.teams).toEqual([]);
    expect(state.teams.find(t => t.id === revised.previous.id)).toBeDefined();
    expect(state.teams.filter(t => t.format === "double" && t.slot === 0)).toHaveLength(1);
  });
  it("puts a team's Pokémon in the box, its format's box first, and reuses the same builds", async () => {
    const first = saved(await saveTeam(team("double", "Incineroar", "Rillaboom"), { origin: "manual", name: " Lanes " }));
    expect(first.record).toMatchObject({ name: "Lanes", format: "double", slot: 0 });
    expect(first.added.map((record) => [record.box, record.slot])).toEqual([[1, 0], [1, 1]]);

    const second = saved(await saveTeam(team("double", "Incineroar", "Amoonguss"), { origin: "manual" }));
    expect(second.record.slot).toBe(1);
    expect(second.record.members[0]).toBe(first.record.members[0]);
    expect(second.added).toHaveLength(1);

    const state = await loadLibrary();
    expect(state.box).toHaveLength(3);
    expect(teamDocOf(second.record, boxById(state))?.pokemon.map((member) => member.species))
      .toEqual(["Incineroar", "Amoonguss"]);
  });

  it("reports the same team instead of saving it twice, and saves a copy on request", async () => {
    const first = saved(await saveTeam(team("single", "Garchomp", "Salamence"), { origin: "manual" }));
    const again = await saveTeam(team("single", "Salamence", "Garchomp"), { origin: "import" });
    expect(again).toEqual({ status: "duplicate", existing: first.record });
    const copy = saved(await saveTeam(team("single", "Salamence", "Garchomp"), { origin: "import" }, { allowDuplicate: true }));
    expect(copy.added).toHaveLength(0);
  });

  it("knows a team by its builds, whichever of two same-build box Pokémon it uses", async () => {
    await addManyToBox([mon("Garchomp"), mon("Garchomp")], { origin: "manual" });
    const first = saved(await saveTeam(team("single", "Garchomp"), { origin: "manual" }));
    // The team's Garchomp now comes after its twin in box order, so a fresh save would pick the twin.
    await moveBoxRecord(first.record.members[0]!, { box: 1, slot: 0 });
    expect(await saveTeam(team("single", "Garchomp"), { origin: "manual" }))
      .toEqual({ status: "duplicate", existing: first.record });
  });

  it("fills a format's slots, then replaces only the slot it is given; the replaced team's Pokémon stay", async () => {
    for (let index = 0; index < LIBRARY_START.teamSlots; index += 1) {
      saved(await saveTeam(team("single", `Mon${index}`), { origin: "manual" }));
    }
    expect(await saveTeam(team("single", "Garchomp"), { origin: "manual" })).toEqual({ status: "full" });
    const replacing = saved(await saveTeam(team("single", "Garchomp"), { origin: "manual" }, { slot: 2 }));
    expect(replacing.replaced?.slot).toBe(2);
    expect((await loadLibrary()).box.map((record) => record.member.species)).toContain("Mon2");

    await undoSaveTeam(replacing);
    const state = await loadLibrary();
    expect(state.teams.find((record) => record.slot === 2)?.id).toBe(replacing.replaced?.id);
    expect(state.box.map((record) => record.member.species)).not.toContain("Garchomp");
  });

  it("refuses a team the box has no room for", async () => {
    await addManyToBox(Array.from({ length: BOX_SLOTS * 2 - 1 }, (_, index) => mon(`Filler${index}`)), { origin: "manual" });
    expect(await saveTeam(team("single", "Garchomp", "Salamence"), { origin: "manual" }))
      .toEqual({ status: "no-room", needed: 2, free: 1 });
    expect((await loadLibrary()).teams).toHaveLength(0);
  });

  it("takes a deleted Pokémon out of its teams, keeps an emptied team, and undoes both exactly", async () => {
    const lanes = saved(await saveTeam(team("double", "Incineroar", "Rillaboom"), { origin: "manual" }));
    const solo = saved(await saveTeam(team("double", "Incineroar"), { origin: "manual" }));
    const incineroar = lanes.record.members[0]!;

    const removed = await removeBoxRecord(incineroar);
    expect(removed?.memberships).toEqual([
      { teamId: lanes.record.id, index: 0 }, { teamId: solo.record.id, index: 0 },
    ]);
    let state = await loadLibrary();
    expect(state.teams.map((record) => record.members.length)).toEqual([1, 0]);
    expect(teamDocOf(state.teams[1]!, boxById(state))).toBeNull();

    expect(await restoreBoxRecord(removed!)).toBe(true);
    state = await loadLibrary();
    expect(state.teams.map((record) => record.members)).toEqual([lanes.record.members, solo.record.members]);
    expect(state.box.find((record) => record.id === incineroar)).toMatchObject({ box: 1, slot: 0 });
  });

  it("swaps on a move into a taken place, for Pokémon and for teams", async () => {
    const a = (await addToBox(mon("Garchomp"), { origin: "manual" }))!;
    const b = (await addToBox(mon("Sneasler"), { origin: "manual" }, { box: 1, slot: 7 }))!;
    await moveBoxRecord(a.id, { box: 1, slot: 7 });
    let state = await loadLibrary();
    expect(state.box.map((record) => [record.member.species, record.box, record.slot]))
      .toEqual([["Sneasler", 0, 0], ["Garchomp", 1, 7]]);
    expect(b.id).toBe(state.box[0]!.id);

    const first = saved(await saveTeam(team("single", "Garchomp"), { origin: "manual" }));
    const second = saved(await saveTeam(team("single", "Sneasler"), { origin: "manual" }));
    await moveTeam(second.record.id, 0);
    state = await loadLibrary();
    expect(state.teams.map((record) => [record.id, record.slot]))
      .toEqual([[second.record.id, 0], [first.record.id, 1]]);
  });

  it("builds a team out of box Pokémon: start, add, reorder, remove", async () => {
    const [a, b, c] = (await addManyToBox([mon("Garchomp"), mon("Sneasler"), mon("Kingambit")], { origin: "manual" })).added;
    const started = await startTeam("double", 2, a!.id, { origin: "manual" });
    expect(started).toMatchObject({ format: "double", slot: 2, members: [a!.id] });
    expect(await startTeam("double", 2, b!.id, { origin: "manual" })).toBeNull();

    expect(await addToTeam(started!.id, b!.id)).toMatchObject({ ok: true });
    expect(await addToTeam(started!.id, b!.id)).toEqual({ ok: false, reason: "present" });
    await setTeamMembers(started!.id, [b!.id, a!.id, b!.id, "gone", c!.id]);
    const state = await loadLibrary();
    expect(state.teams[0]!.members).toEqual([b!.id, a!.id, c!.id]);

    const full = saved(await saveTeam(team("single", "A", "B", "C", "D", "E", "F"), { origin: "manual" }));
    expect(await addToTeam(full.record.id, c!.id)).toEqual({ ok: false, reason: "full" });
  });

  it("copies a team onto the same Pokémon", async () => {
    const original = saved(await saveTeam(team("single", "Garchomp"), { origin: "manual", name: "A" }));
    const copy = await duplicateTeam(original.record.id, (name) => `${name} (copy)`);
    expect(copy).toMatchObject({ name: "A (copy)", slot: 1, members: original.record.members });
  });

  it("opens boxes and team slots only up to the limit it is given", async () => {
    expect(await addBox(LIBRARY_START.boxes)).toBeNull();
    expect((await addBox(3))?.boxes).toHaveLength(3);
    expect(await addBox(3)).toBeNull();
    expect((await openTeamSlots("double", 20))?.teamSlots).toEqual({ single: 5, double: 10 });
    expect((await addManyToBox(Array.from({ length: BOX_SLOTS * 3 + 2 }, () => mon("Magikarp")), { origin: "manual" })).left).toBe(2);
  });

  it("moves a group together, onto its own cells if need be, and refuses a target someone else holds", async () => {
    const [a, b, c] = (await addManyToBox([mon("Abra"), mon("Bagon"), mon("Cleffa")], { origin: "manual" }, { box: 0, slot: 0 })).added;
    expect(await moveBoxGroup([{ id: a!.id, to: { box: 0, slot: 1 } }, { id: b!.id, to: { box: 0, slot: 2 } }])).toBe(false);
    expect(await moveBoxGroup([{ id: a!.id, to: { box: 0, slot: 1 } }, { id: b!.id, to: { box: 0, slot: 3 } }])).toBe(true);
    const state = await loadLibrary();
    expect(state.box.map((record) => [record.id, record.slot])).toEqual([[a!.id, 1], [c!.id, 2], [b!.id, 3]]);
  });

  it("closes only boxes and team slots opened beyond the start, taking their contents and closing the gap", async () => {
    await addBox(4);
    await addBox(4);
    const doomed = await addToBox(mon("Magikarp"), { origin: "manual" }, { box: 2, slot: 0 });
    const later = await addToBox(mon("Gyarados"), { origin: "manual" }, { box: 3, slot: 4 });
    const kept = saved(await saveTeam(team("single", "Garchomp"), { origin: "manual" }));
    await addToTeam(kept.record.id, doomed!.id);
    expect(await deleteBox(1)).toBeNull();
    expect((await deleteBox(2))?.boxes).toHaveLength(3);
    let state = await loadLibrary();
    expect(state.box.find((record) => record.id === doomed!.id)).toBeUndefined();
    expect(state.box.find((record) => record.id === later!.id)).toMatchObject({ box: 2, slot: 4 });
    expect(state.teams[0]!.members).toEqual(kept.record.members);

    await openTeamSlots("single", 10);
    const moved = saved(await saveTeam(team("single", "Salamence"), { origin: "manual" }, { slot: 7 }));
    const gone = saved(await saveTeam(team("single", "Dragonite"), { origin: "manual" }, { slot: 6 }));
    expect(await closeTeamSlot("single", 4)).toBeNull();
    expect((await closeTeamSlot("single", 6))?.teamSlots.single).toBe(9);
    state = await loadLibrary();
    expect(state.layout.teamSlots.single).toBe(9);
    expect(state.teams.find((record) => record.id === gone.record.id)).toBeUndefined();
    expect(state.teams.find((record) => record.id === moved.record.id)?.slot).toBe(6);
    expect(state.box.some((record) => record.member.species === "Dragonite")).toBe(true);
  });

  it("keeps rows it cannot read instead of dropping them", async () => {
    saved(await saveTeam(team("single", "Garchomp"), { origin: "manual" }));
    await db.teams.put({ id: "future", kind: "team", schemaVersion: 99 } as never);
    const state = await loadLibrary();
    expect(state.teams).toHaveLength(1);
    expect(state.broken.teams).toHaveLength(1);
    expect((await exportAll()).teams).toHaveLength(2);
  });
});

describe("revising a team from a session", () => {
  it("gives the team new builds, keeps its old ones as a copy, and can take it back", async () => {
    const source = saved(await saveTeam(team("double", "Incineroar", "Rillaboom"), { origin: "manual", name: "Lanes" }));
    const result = await reviseTeam(source.record.id, team("double", "Incineroar", "Amoonguss"),
      { origin: "session" }, "Lanes (before)");
    if (result.status !== "revised") throw new Error(result.status);
    const state = await loadLibrary();
    const box = boxById(state);
    const species = (id: string) => teamDocOf(state.teams.find((row) => row.id === id)!, box)?.pokemon.map((m) => m.species);
    expect(result.record).toMatchObject({ id: source.record.id, slot: 0, name: "Lanes", origin: "session" });
    expect(species(source.record.id)).toEqual(["Incineroar", "Amoonguss"]);
    expect(result.previous).toMatchObject({ slot: 1, name: "Lanes (before)" });
    expect(species(result.previous.id)).toEqual(["Incineroar", "Rillaboom"]);
    expect(result.added).toHaveLength(1);                 // the shared Incineroar is reused

    expect((await reviseTeam(source.record.id, team("double", "Incineroar", "Amoonguss"), { origin: "session" }, "x")).status)
      .toBe("unchanged");
    expect((await reviseTeam(source.record.id, team("single", "Garchomp"), { origin: "session" }, "x")).status)
      .toBe("format");
    expect((await reviseTeam(source.record.id, { format: "double", pokemon: [{}] } as never, { origin: "session" }, "x")).status)
      .toBe("invalid");

    await undoReviseTeam(result);
    const after = await loadLibrary();
    expect(after.teams.map((row) => row.id)).toEqual([source.record.id]);
    expect(teamDocOf(after.teams[0]!, boxById(after))?.pokemon.map((m) => m.species)).toEqual(["Incineroar", "Rillaboom"]);
    expect(after.box).toHaveLength(2);
  });

  it("changes nothing when there is no free slot for the old version", async () => {
    for (let slot = 0; slot < LIBRARY_START.teamSlots; slot++) {
      saved(await saveTeam(team("single", `Garchomp`, slot % 2 ? "Salamence" : "Dragonite"), { origin: "manual" },
        { slot, allowDuplicate: true }));
    }
    const first = (await loadLibrary()).teams[0]!;
    expect((await reviseTeam(first.id, team("single", "Kingambit"), { origin: "session" }, "old")).status).toBe("full");
    expect((await loadLibrary()).box.map((record) => record.member.species)).not.toContain("Kingambit");
  });
});

describe("library import/export", () => {
  const limits = LIBRARY_TIERS.visitor;

  it("restores separate saved teams sharing the same builds without collapsing their notes", async () => {
    saved(await saveTeam(team("single", "Garchomp"), { origin: "manual", name: "One", notes: "First" }));
    saved(await saveTeam(team("single", "Garchomp"), { origin: "manual", name: "Two", notes: "Second" },
      { slot: 1, allowDuplicate: true }));
    const text = JSON.stringify(await exportAll());
    await db.teams.clear();
    await db.box.clear();
    const plan = await planImport(text);
    if (typeof plan === "string") throw new Error(plan);
    expect(await applyImport(plan, { keepDuplicates: false, limits })).toMatchObject({ teams: 2 });
    expect((await loadLibrary()).teams.map(({ name, notes }) => [name, notes]))
      .toEqual([["One", "First"], ["Two", "Second"]]);
  });

  it("keeps separate identical individuals and their metadata when restoring into a partially populated library", async () => {
    const source = saved(await saveTeam(team("single", "Garchomp", "Garchomp"), { origin: "manual" }));
    const file = await exportAll();
    const first = source.added[0]!;
    await db.teams.clear();
    await db.box.delete(source.added[1]!.id);
    const plan = await planImport(JSON.stringify(file));
    if (typeof plan === "string") throw new Error(plan);
    expect(plan.box.filter((record) => record.existing)).toHaveLength(1);
    expect(await applyImport(plan, { keepDuplicates: false, limits })).toMatchObject({ teams: 1, box: 1 });
    const state = await loadLibrary();
    expect(new Set(state.teams[0]!.members).size).toBe(2);
    expect(state.box.find((record) => record.id !== first.id)?.updatedAt).toBe(source.added[1]!.updatedAt);
  });

  it("restores empty teams with their notes and skips bad members without rolling back valid rows", async () => {
    const result = saved(await saveTeam(team("single", "Garchomp"), { origin: "manual", name: "Empty", notes: "Keep me" }));
    await removeBoxRecord(result.record.members[0]!);
    const file = await exportAll();
    file.box.push({ member: mon("Salamence") }, { member: mon("x".repeat(101)) });
    await db.teams.clear();
    const plan = await planImport(JSON.stringify(file));
    if (typeof plan === "string") throw new Error(plan);
    expect(plan.invalid).toBe(1);
    expect(await applyImport(plan, { keepDuplicates: false, limits })).toMatchObject({ teams: 1, box: 1 });
    const state = await loadLibrary();
    expect(state.teams[0]).toMatchObject({ name: "Empty", notes: "Keep me", members: [] });
  });

  it("never writes a partial team or its newly allocated members when capacity is insufficient", async () => {
    await addManyToBox(Array.from({ length: 59 }, (_, n) => mon(`Filler${n}`)), { origin: "manual" });
    const plan = await planImport(JSON.stringify({ format: "pokemon-champions-library", version: 1,
      exportedAt: new Date().toISOString(), teams: [{ team: team("single", "Garchomp", "Salamence") }], box: [] }));
    if (typeof plan === "string") throw new Error(plan);
    expect(await applyImport(plan, { keepDuplicates: false, limits })).toMatchObject({ teams: 0, box: 0 });
    expect((await loadLibrary()).box).toHaveLength(59);
  });

  it("carries teams, their Pokémon and their places to another library, and flags what is already stored", async () => {
    saved(await saveTeam(team("single", "Garchomp", "Salamence"), { origin: "manual", name: "Dragons", tags: ["ladder"] }));
    saved(await saveTeam(team("double", "Incineroar"), { origin: "builder" }, { slot: 3 }));
    await addToBox(mon("Sneasler"), { origin: "manual", nickname: "Sly" }, { box: 0, slot: 20 });
    const text = JSON.stringify(await exportAll());

    const again = await planImport(text);
    if (typeof again === "string") throw new Error(again);
    expect(again.teams.every((entry) => entry.duplicate)).toBe(true);
    expect(again.box.every((entry) => entry.existing)).toBe(true);
    expect(await applyImport(again, { keepDuplicates: false, limits })).toEqual({ teams: 0, box: 0, skipped: 6, noRoom: 0 });

    const fresh = new LibraryDb(`library-test-${++serial}`);
    setLibraryDbForTests(fresh);
    const plan = await planImport(text);
    if (typeof plan === "string") throw new Error(plan);
    expect(await applyImport(plan, { keepDuplicates: false, limits })).toEqual({ teams: 2, box: 4, skipped: 0, noRoom: 0 });
    const state = await loadLibrary();
    expect(state.teams.map((record) => [record.format, record.slot, record.name])).toEqual([["double", 3, ""], ["single", 0, "Dragons"]]);
    expect(teamDocOf(state.teams[1]!, boxById(state))?.pokemon.map((member) => member.species)).toEqual(["Garchomp", "Salamence"]);
    expect(state.box.find((record) => record.nickname === "Sly")).toMatchObject({ box: 0, slot: 20 });
    await fresh.delete();
  });

  it("builds box Pokémon for a team that only carries team-json, and counts what finds no room", async () => {
    await addManyToBox(Array.from({ length: BOX_SLOTS * 2 - 1 }, (_, index) => mon(`Filler${index}`)), { origin: "manual" });
    const text = JSON.stringify({
      format: "pokemon-champions-library", version: 1, exportedAt: "2026-09-27T00:00:00Z",
      teams: [{ team: { format: "single", pokemon: [{ species: "Garchomp" }, { species: "Salamence" }] } }],
      box: [{ member: { species: "Sneasler" } }],
    });
    const plan = await planImport(text);
    if (typeof plan === "string") throw new Error(plan);
    expect(plan.room).toEqual({ box: 1, single: 5, double: 5 });
    expect(await applyImport(plan, { keepDuplicates: false, limits })).toEqual({ teams: 0, box: 1, skipped: 0, noRoom: 3 });
  });

  it("counts entries it cannot read and imports the rest", async () => {
    const text = JSON.stringify({
      format: "pokemon-champions-library", version: 1, exportedAt: "2026-09-26T00:00:00Z",
      teams: [{ team: { format: "single", pokemon: [{ species: "Garchomp" }] } }, { team: { pokemon: [] } }, 7],
      box: [{ member: { item: "Leftovers" } }],
    });
    const plan = await planImport(text);
    if (typeof plan === "string") throw new Error(plan);
    expect(plan.teams).toHaveLength(1);
    expect(plan.invalid).toBe(3);
  });

  it("says why a file is not a library", async () => {
    expect(await planImport("{")).toBe("not-json");
    expect(await planImport(JSON.stringify({ format: "something-else" }))).toBe("not-a-library-file");
  });
});
