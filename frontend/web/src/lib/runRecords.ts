/** Run records: questions, builds, diagnoses and matchup batteries — every
 * request that spends an allowance (frontend/design.md §2.3, §7.2–7.5). The server keeps no
 * online history; online browsers keep a separate IndexedDB and local workspaces use records.json. These are
 * derived results, not the reader's teams and boxes, so the two never mix and the library's export
 * does not carry them.
 *
 * A record keeps its input, its result and the data it was computed against. Nothing here refreshes
 * a result or passes an old one off as current: `freshness` compares the record's data identity with
 * the runtime's, and the page offers a re-run. A result this build can no longer read keeps its input
 * (the page parses `result` with the DTO schema and says so when it fails).
 *
 * Pages read through an in-memory copy, so the lists render even where IndexedDB is unavailable
 * (records then last for the page's life); writes go to both, and other tabs are told over a
 * BroadcastChannel which records changed, and read just those. Dexie itself loads on first use, and
 * each kind is read when a page first asks for it. A run in progress beats every few seconds; a running record whose
 * beat stops was cut off (its tab closed or reloaded mid-request) and reads as interrupted. A build
 * is the exception: its job lives on the server, so a later visit picks it up again by job id. */
import type { Table } from "dexie";
import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import type { Capabilities, FormatId, SourceFingerprints } from "@pokemon-champions/protocol";
import type { Lang } from "../i18n.ts";
import { cancelUserWrite, persistUserWrite, rememberStorageFailure, userStorage } from "./userStorage.ts";

export type AssistKind = "qa" | "build" | "diagnose" | "matchup";
type AssistStatus = "running" | "done" | "failed";

/** The data a result was computed against: the rule, and per format the meta panel and real-team
 * library hashes (`environment.sourceFingerprints`, which change only when that data changes). */
interface AssistSnapshot {
  season: string;
  rule: string;
  asOf: string | null;
  deploymentId: string;
  data: Partial<Record<FormatId, SourceFingerprints>>;
}

export interface AssistRecord<I = unknown> {
  id: string;
  kind: AssistKind;
  v: 1;
  createdAt: number;
  updatedAt: number;
  status: AssistStatus;
  /** Last sign of life from the tab running it (ms). */
  beatAt?: number;
  lang: Lang;
  snapshot: AssistSnapshot;
  input: I;
  /** The endpoint's payload, as received; read back through its DTO schema. */
  result?: unknown;
  /** A typed error code for a failed run. */
  error?: string;
  /** A build's server job, while it runs. */
  jobId?: string;
}

/** Newest records kept per kind; older finished ones go first. */
/** A matchup battery's result is the largest (a full table per member), so fewer are kept. */
const CAPS: Record<AssistKind, number> = { qa: 80, build: 30, diagnose: 30, matchup: 12 };
const BEAT_MS = 4000;
const CUT_OFF_MS = 15000;

/** The database, opened on first use. Dexie is imported then, not with the page: a page renders its
 * records list empty, and fills it once the store answers. */
type RunTable = Pick<Table<AssistRecord, string>, "put" | "bulkGet" | "bulkDelete" | "where">;
let opening: Promise<RunTable | null> | null = null;
function database(): Promise<RunTable | null> {
  const local = userStorage();
  if (local) {
    // Same repository behavior; no IndexedDB is opened in the local runtime.
    return Promise.resolve({
      where: () => ({ equals: (kind: AssistKind) => ({ toArray: async () => (await local.records()).filter(row => readable(row) && row.kind === kind) }) }),
      put: async (record: AssistRecord) => { await local.putRecord(record as unknown as Record<string, unknown>); return record.id; },
      bulkGet: async (ids: string[]) => { const rows = await local.records(); return ids.map(id => rows.find(row => readable(row) && row.id === id)); },
      bulkDelete: async (ids: string[]) => local.deleteRecords(ids),
    } as unknown as RunTable);
  }
  opening ??= import("dexie").then(({ default: Dexie }) => {
    const db = new Dexie("pokemon-champions-runs");
    db.version(1).stores({ records: "id, kind" });
    return db.table<AssistRecord, string>("records");
  }).catch(() => null);
  return opening;
}

const cache = new Map<string, AssistRecord>();
const listeners = new Map<AssistKind, Set<() => void>>();
const versions: Record<AssistKind, number> = { qa: 0, build: 0, diagnose: 0, matchup: 0 };
/** Kinds whose stored records have been read (each kind loads on its own, when a page asks). */
const loading = new Map<AssistKind, Promise<void>>();
const loaded = new Set<AssistKind>();
const channel = typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel("pcui-run-records");

function changed(kind: AssistKind) {
  versions[kind] += 1;
  for (const listener of listeners.get(kind) ?? []) listener();
}

function readable(row: unknown): row is AssistRecord {
  const r = row as Partial<AssistRecord> | null;
  return !!r && typeof r.id === "string" && r.v === 1 && (r.kind === "qa" || r.kind === "build" || r.kind === "diagnose" || r.kind === "matchup")
    && typeof r.createdAt === "number" && typeof r.status === "string" && !!r.snapshot && "input" in r;
}

function ensureLoaded(kind: AssistKind): Promise<void> {
  let pending = loading.get(kind);
  if (!pending) {
    const read = async () => {
        const table = await database();
        const rows = table ? await table.where("kind").equals(kind).toArray() : [];
        // A record written here before the store answered is newer than its stored row.
        for (const row of rows) if (readable(row) && !cache.has(row.id)) cache.set(row.id, row);
      loaded.add(kind);
      changed(kind);
    };
    pending = read().catch(error => {
      if (userStorage()) rememberStorageFailure(`read:${kind}`, read, error);
      else { loaded.add(kind); changed(kind); }
    });
    loading.set(kind, pending);
  }
  return pending;
}

/** Another tab changed these records: read just them again (a kind this tab never loaded waits for
 * its own first read). */
channel?.addEventListener("message", (event: MessageEvent<{ ids?: string[]; beats?: Array<{ id: string; at: number }> }>) => {
  const beats = event.data?.beats;
  if (Array.isArray(beats)) {
    const touched = new Set<AssistKind>();
    for (const beat of beats) {
      const record = cache.get(beat.id);
      if (record?.status !== "running" || beating.has(beat.id) || !Number.isFinite(beat.at)
        || beat.at <= (record.beatAt ?? record.updatedAt)) continue;
      cache.set(beat.id, { ...record, beatAt: beat.at });
      touched.add(record.kind);
    }
    for (const kind of touched) changed(kind);
    return;
  }
  const ids = event.data?.ids;
  if (!Array.isArray(ids) || !ids.length) return;
  void (async () => {
    const table = await database();
    if (!table) return;
    const touched = new Set<AssistKind>();
    try {
      const rows = await table.bulkGet(ids);
      for (const [index, id] of ids.entries()) {
        const row = rows[index];
        if (readable(row)) {
          if (!loaded.has(row.kind)) continue;
          cache.set(id, row);
          touched.add(row.kind);
        } else {
          const previous = cache.get(id);
          if (previous && cache.delete(id)) touched.add(previous.kind);
        }
      }
    } catch { /* unreadable now: leave the in-memory copy */ }
    for (const kind of touched) changed(kind);
  })();
});

const announce = (ids: string[]) => { if (ids.length) channel?.postMessage({ ids }); };

let persistenceAsked = false;
function persist(record: AssistRecord, announceChange = true): void {
  if (userStorage()) {
    persistUserWrite(record.id, async () => {
      await userStorage()!.putRecord(record as unknown as Record<string, unknown>);
      if (announceChange) announce([record.id]);
    });
    return;
  }
  void database().then((table) => table?.put(record)).then(() => {
    if (announceChange) announce([record.id]);
    if (persistenceAsked) return;
    persistenceAsked = true;
    void navigator.storage?.persist?.().catch(() => {});
  }, () => { /* storage blocked or full: the record lives on in memory */ });
}

function forget(ids: string[]): void {
  if (!ids.length) return;
  if (userStorage()) {
    for (const id of ids) cancelUserWrite(id);
    persistUserWrite(`delete:${ids.join(",")}`, async () => { await userStorage()!.deleteRecords(ids); announce(ids); });
    return;
  }
  void database().then((table) => table?.bulkDelete(ids)).then(() => announce(ids), () => {});
}

function write(record: AssistRecord): AssistRecord {
  cache.set(record.id, record);
  changed(record.kind);
  persist(record);
  return record;
}

/** A result read back through its DTO schema, once per stored payload: lists re-render on every
 * keystroke in a form, and a diagnosis report is too large to validate again each time. Keyed by the
 * payload object, which survives the record copies a heartbeat makes. */
const parsedResults = new WeakMap<object, Map<unknown, unknown>>();
export function readResult<T>(record: AssistRecord,
                              schema: { safeParse(value: unknown): { success: boolean; data?: T } }): T | null {
  const payload = record.result;
  if (payload === null || typeof payload !== "object") return null;
  let bySchema = parsedResults.get(payload);
  if (!bySchema) parsedResults.set(payload, bySchema = new Map());
  if (!bySchema.has(schema)) {
    const parsed = schema.safeParse(payload);
    bySchema.set(schema, parsed.success ? parsed.data as T : null);
  }
  return bySchema.get(schema) as T | null;
}

const newId = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

export function snapshotOf(capabilities: Capabilities): AssistSnapshot {
  const env = capabilities.environment;
  return {
    season: env.season, rule: env.rule, asOf: env.asOf ?? null, deploymentId: capabilities.deploymentId,
    data: { ...env.sourceFingerprints },
  };
}

/** Records this tab is running: they beat, and they are never read as cut off here. */
const beating = new Set<string>();
let beatTimer: number | undefined;
function beat() {
  const now = Date.now();
  const beats: Array<{ id: string; at: number }> = [];
  for (const id of beating) {
    const record = cache.get(id);
    // Silent: only other tabs read a beat (to tell a live run from a cut-off one), so this tab's lists
    // need not re-render for it.
    if (record?.status !== "running") continue;
    const next = { ...record, beatAt: now };
    cache.set(id, next);
    persist(next, false);
    beats.push({ id, at: now });
  }
  // Older open tabs understand ids only; current tabs use the small beat and preserve payload identity.
  if (beats.length) channel?.postMessage({ beats, ids: beats.map(beat => beat.id) });
  if (!beating.size && beatTimer !== undefined) {
    window.clearInterval(beatTimer);
    beatTimer = undefined;
  }
}

export function startRecord<I>(kind: AssistKind, input: I, lang: Lang, snapshot: AssistSnapshot,
                               extra: Partial<AssistRecord<I>> = {}): AssistRecord<I> {
  const now = Date.now();
  const record: AssistRecord<I> = {
    id: newId(), kind, v: 1, createdAt: now, updatedAt: now, status: "running", beatAt: now,
    lang, snapshot, input, ...extra,
  };
  beating.add(record.id);
  beatTimer ??= window.setInterval(beat, BEAT_MS);
  void ensureLoaded(kind);
  return write(record as AssistRecord) as AssistRecord<I>;
}

export function updateRecord(id: string, patch: Partial<AssistRecord>): void {
  const record = cache.get(id);
  if (!record) return;
  write({ ...record, ...patch, updatedAt: Date.now() });
}

/** Continue work on an existing record while preserving its input, result and identity. */
export function resumeRecord(id: string): void {
  if (!cache.has(id)) return;
  beating.add(id);
  beatTimer ??= window.setInterval(beat, BEAT_MS);
  updateRecord(id, { status: "running", beatAt: Date.now() });
}

export function finishRecord(id: string, result: unknown): void {
  beating.delete(id);
  const record = cache.get(id);
  if (!record) return;
  write({ ...record, status: "done", result, error: undefined, jobId: undefined, updatedAt: Date.now() });
  prune(record.kind);
}

export function failRecord(id: string, error: string, result?: unknown): void {
  beating.delete(id);
  const record = cache.get(id);
  if (!record) return;
  write({ ...record, status: "failed", error, ...(result !== undefined ? { result } : {}), jobId: undefined,
    updatedAt: Date.now() });
  prune(record.kind);
}

export function deleteRecord(id: string): void {
  beating.delete(id);
  const record = cache.get(id);
  if (!cache.delete(id)) return;
  changed(record!.kind);
  forget([id]);
}

/** Clears a kind's finished records; anything still running stays. */
export function clearRecords(kind: AssistKind): void {
  const gone = [...cache.values()].filter((r) => r.kind === kind && r.status !== "running").map((r) => r.id);
  for (const id of gone) cache.delete(id);
  changed(kind);
  forget(gone);
}

function prune(kind: AssistKind): void {
  const finished = [...cache.values()].filter((r) => r.kind === kind && r.status !== "running")
    .sort((a, b) => b.createdAt - a.createdAt);
  const gone = finished.slice(CAPS[kind]).map((r) => r.id);
  if (!gone.length) return;
  for (const id of gone) cache.delete(id);
  changed(kind);
  forget(gone);
}

/** A running question or diagnosis whose tab stopped beating: its request is gone. A build is not
 * cut off this way — its job carries on server-side and is picked up by id. */
export function isCutOff(record: AssistRecord): boolean {
  return record.status === "running" && record.kind !== "build" && !beating.has(record.id)
    && Date.now() - (record.beatAt ?? record.updatedAt) > CUT_OFF_MS;
}

function subscribe(kind: AssistKind, listener: () => void) {
  let group = listeners.get(kind);
  if (!group) listeners.set(kind, group = new Set());
  group.add(listener);
  return () => { group.delete(listener); };
}

/** A kind's records, newest first; `ready` once the stored ones have been read. */
export function useAssistRecords<I = unknown>(kind: AssistKind): { records: AssistRecord<I>[]; ready: boolean } {
  const listen = useCallback((listener: () => void) => subscribe(kind, listener), [kind]);
  const seen = useSyncExternalStore(listen, () => versions[kind]);
  useEffect(() => { void ensureLoaded(kind); }, [kind]);
  const value = useMemo(() => ({
    records: [...cache.values()].filter((r) => r.kind === kind).sort((a, b) => b.createdAt - a.createdAt) as AssistRecord<I>[],
    ready: loaded.has(kind),
  }), [kind, seen]);
  // A run another page instance owns reads as interrupted once its beat lapses: look again then.
  useEffect(() => {
    const due = value.records
      .filter((r) => r.status === "running" && r.kind !== "build" && !beating.has(r.id))
      .map((r) => (r.beatAt ?? r.updatedAt) + CUT_OFF_MS - Date.now())
      .filter((ms) => ms > 0);
    if (!due.length) return;
    const timer = window.setTimeout(() => changed(kind), Math.min(...due) + 100);
    return () => window.clearTimeout(timer);
  }, [value, kind]);
  return value;
}

/** Whether a record still describes the runtime's data. `formats` are the formats its result read
 * (a dex-only answer reads none); `rule` flags a result from another season or rule. */
export function freshness(record: AssistRecord, capabilities: Capabilities, formats: FormatId[]):
  { dataChanged: boolean; ruleChanged: boolean } {
  const env = capabilities.environment;
  const ruleChanged = record.snapshot.rule !== env.rule || record.snapshot.season !== env.season;
  const dataChanged = formats.some((format) => {
    const then = record.snapshot.data[format];
    const now = env.sourceFingerprints[format];
    return !then || !now || then.meta !== now.meta || then.teamLibrary !== now.teamLibrary;
  });
  return { dataChanged, ruleChanged };
}

/** Per-tab selection: which record each surface shows, surviving navigation within the browser tab. */
export function readSelected(kind: AssistKind): string | null {
  try { return sessionStorage.getItem(`pc-run-selected-${kind}`); } catch { return null; }
}
export function saveSelected(kind: AssistKind, id: string | null): void {
  try {
    if (id) sessionStorage.setItem(`pc-run-selected-${kind}`, id);
    else sessionStorage.removeItem(`pc-run-selected-${kind}`);
  } catch { /* storage blocked: the selection just won't survive navigation */ }
}

const RELATIVE: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ["minute", 60_000], ["hour", 3_600_000], ["day", 86_400_000],
];
export const LOCALES: Record<Lang, string> = { zh: "zh-CN", en: "en", ja: "ja" };
const relativeFormats = new Map<Lang, Intl.RelativeTimeFormat>();
const dateFormats = new Map<Lang, Intl.DateTimeFormat>();
const clockFormats = new Map<Lang, Intl.DateTimeFormat>();

export function clockTime(ms: number, lang: Lang): string {
  let format = clockFormats.get(lang);
  if (!format) clockFormats.set(lang, format = new Intl.DateTimeFormat(LOCALES[lang], { hour: "2-digit", minute: "2-digit" }));
  return format.format(ms);
}

/** "3 minutes ago" within a week, the date after that. */
export function relativeTime(ms: number, lang: Lang, justNow: string): string {
  const ago = Date.now() - ms;
  if (ago < 45_000) return justNow;
  if (ago < 7 * 86_400_000) {
    const [unit, size] = [...RELATIVE].reverse().find(([, step]) => ago >= step) ?? RELATIVE[0]!;
    let format = relativeFormats.get(lang);
    if (!format) relativeFormats.set(lang, format = new Intl.RelativeTimeFormat(LOCALES[lang], { numeric: "auto" }));
    return format.format(-Math.round(ago / size), unit);
  }
  let format = dateFormats.get(lang);
  if (!format) dateFormats.set(lang, format = new Intl.DateTimeFormat(LOCALES[lang], { month: "short", day: "numeric" }));
  return format.format(ms);
}
