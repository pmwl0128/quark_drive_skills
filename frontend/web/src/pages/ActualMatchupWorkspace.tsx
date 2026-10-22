import type { ActualMatchupResponseDto, FormatId, TeamDoc, TeamMemberDoc } from "@pokemon-champions/protocol";
import { ActualMatchupResponseDtoSchema } from "@pokemon-champions/protocol";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { IconFileImport, IconHistory } from "@tabler/icons-react";
import { CalcRail, type CalcRailApi, type CalcRailTarget } from "../components/CalcRail.tsx";
import { RailHandle, useSideRail } from "../components/SideRail.tsx";
import { SegmentedControl } from "../components/SegmentedControl.tsx";
import { useAsync, useDexIndex } from "../hooks.ts";
import { parseTeamInput } from "../lib/parseTeamInput.ts";
import { useLang } from "../i18n.ts";
import { useMatchupT } from "./matchup/messages.ts";
import { readTeamMembers, takeMatchupFill, type TeamMemberish } from "../lib/team.ts";
import { useRuntime } from "../runtime/context.tsx";
import { useTransferT } from "../lib/library/transferMessages.ts";
import { useLibraryDrop, useLibraryReceiver, useLibrarySource } from "../lib/library/workspace.tsx";
import { toTeamDoc } from "../lib/teamDoc.ts";
import type { DexIndexEntry } from "../runtime/adapter.ts";
import type { BuildOption } from "../lib/buildState.ts";
import { roveFocus, useFocusOnOpen, usePopover } from "../lib/popover.ts";
import { MonBuildEditor } from "../components/library/MonBuildEditor.tsx";
import { TeamSprites } from "../components/TeamDots.tsx";
import { clearRecords, deleteRecord, finishRecord, freshness, isCutOff, readResult, readSelected, relativeTime, saveSelected, snapshotOf, startRecord, useAssistRecords, type AssistRecord } from "../lib/runRecords.ts";
import { preloadable } from "../lib/preloadable.ts";

const ActualResult = lazy(() => import("./matchup/ActualResult.tsx").then(module => ({ default: module.ActualResult })));

// The library store loads with the list, not with the page.
const TeamImportList = preloadable(() => import("../components/library/TeamImportList.tsx"));

type InputMode = "manual" | "text";
type Spread = Record<"hp" | "atk" | "def" | "spa" | "spd" | "spe", number>;
interface DraftMember extends TeamMemberish { id: string; spread: Spread; moves: string[] }

/** Session storage keeps the editable draft. Complete batteries live in runRecords; writing their
 * tables synchronously on every draft edit would duplicate IndexedDB and block the page. */
const STORE_KEY = "pc-actual-matchup-v1";

interface Stash {
  mode?: InputMode;
  members?: DraftMember[];
  text?: string;
  topK?: number;
  /** Read-only compatibility with a session saved before runRecords. */
  response?: unknown;
}

function readStash(): Stash {
  try {
    return JSON.parse(sessionStorage.getItem(STORE_KEY) ?? "{}") as Stash;
  } catch {
    return {};
  }
}

function writeStash(s: Omit<Stash, "response">): void {
  try {
    sessionStorage.setItem(STORE_KEY, JSON.stringify(s));
  } catch { /* storage blocked — continuity just won't survive navigation */ }
}

/** Module scope keeps a paid battery alive across navigation. The original continuation finishes
 * its run record even after unmount; a returning workspace attaches to the same promise. */
interface ActiveBatteryTask {
  promise: Promise<ActualMatchupResponseDto>;
}
let activeBatteryTask: ActiveBatteryTask | null = null;

const EMPTY_SPREAD: Spread = { hp: 0, atk: 0, def: 0, spa: 0, spd: 0, spe: 0 };
const TOP_K_PRESETS = [8, 20, 30, 50, 60] as const;
/** One team's worth of builds per battery (the bridge's MATCHUP_SOURCE_MAX). */
const MAX_BUILDS = 6;

function newMember(seed?: TeamMemberish, index = 0): DraftMember {
  return {
    id: `${Date.now()}-${index}-${Math.random().toString(36).slice(2, 7)}`,
    species: seed?.species ?? "",
    item: seed?.item ?? "",
    ability: seed?.ability ?? "",
    nature: seed?.nature ?? "",
    moves: [...(seed?.moves ?? []), "", "", "", ""].slice(0, 4),
    spread: { ...EMPTY_SPREAD, ...(seed?.spread ?? {}) },
  };
}

function membersFromTeam(team: unknown): DraftMember[] {
  return readTeamMembers(team).slice(0, MAX_BUILDS).map((member, index) => newMember(member, index));
}

function teamFromMembers(format: FormatId, members: DraftMember[]) {
  return {
    schema_version: 1, format, season: null, rule: null,
    pokemon: members.filter((member) => member.species.trim()).map((member) => ({
      species: member.species.trim(), item: member.item?.trim() || null,
      ability: member.ability?.trim() || null, nature: member.nature?.trim() || null,
      moves: member.moves.map((move) => move.trim()).filter(Boolean).slice(0, 4),
      spread: member.spread, tera: null, completeness: "extracted_set",
    })),
    provenance: null,
  };
}

/** 导入队伍: one of the reader's kept teams of this format replaces the builds. A page that works on
 * one whole team takes it straight from here; the dock stays the way for a single Pokémon. */
function TeamImport({ format, onPick, replaceCurrent }: { format: FormatId; onPick: (doc: TeamDoc) => void; replaceCurrent: boolean }) {
  const t = useMatchupT();
  const pop = usePopover();
  const list = useRef<HTMLDivElement>(null);
  useFocusOnOpen(pop.open, list, "[role=menuitem]");
  const preloadList = () => void TeamImportList.preload().catch(() => {});
  return (
    <div className="team-import" ref={pop.wrap} onBlur={pop.onBlur}>
      <button ref={pop.trigger} type="button" className={`ghost-btn team-import-btn${pop.open ? " open" : ""}`}
        aria-haspopup="menu" aria-expanded={pop.open} title={t("actual.importTeamHint")} onClick={pop.toggle}
        onPointerEnter={preloadList} onFocus={preloadList}>
        <IconFileImport size={16} aria-hidden />{t("actual.importTeam")}
      </button>
      {pop.open && (
        <div ref={list} className="duel-pop team-import-pop" role="menu" aria-label={t("actual.importTeam")}
          onKeyDown={(event) => { if (!(event.target instanceof HTMLInputElement)) roveFocus(event, "[role=menuitem]"); }}>
          <TeamImportList format={format} autoFocus replaceCurrent={replaceCurrent} onPick={(doc) => { pop.setOpen(false); onPick(doc); }} />
        </div>
      )}
    </div>
  );
}

/** A draft member as the build editor's document, and back. */
const docOf = (member: DraftMember): TeamMemberDoc => ({
  species: member.species, item: member.item || null, ability: member.ability || null,
  nature: member.nature || null, moves: member.moves.filter(Boolean), spread: member.spread,
});
const fromDoc = (doc: TeamMemberDoc, id: string): DraftMember => ({
  id, species: doc.species, item: doc.item ?? "", ability: doc.ability ?? "", nature: doc.nature ?? "",
  moves: [...doc.moves, "", "", "", ""].slice(0, 4), spread: { ...EMPTY_SPREAD, ...(doc.spread ?? {}) },
});

/** One of our builds, edited with the same editor as a box Pokémon (the calculator's inputs), its
 * environment builds taken from this page's format. */
function MemberEditor({ member, index, count, format, onChange, onRemove }: {
  member: DraftMember; index: number; count: number; format: FormatId;
  onChange: (next: DraftMember) => void; onRemove: () => void;
}) {
  const t = useMatchupT();
  return (
    <article className="actual-member-card">
      <header className="actual-member-head">
        <span className="actual-member-index num">{String(index + 1).padStart(2, "0")}</span>
        <button type="button" className="mini-x" onClick={onRemove} disabled={count === 1}
          aria-label={t("actual.remove")} title={t("actual.remove")}>✕</button>
      </header>
      <div className="actual-member-body">
        <MonBuildEditor value={docOf(member)} onChange={(doc) => onChange(fromDoc(doc, member.id))}
          formats={[format]} />
      </div>
    </article>
  );
}

function Elapsed() {
  const t = useMatchupT();
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return <span className="num">{t("actual.elapsed").replace("{n}", String(seconds))}</span>;
}

/** A battery as it was run: its format, its opponent range and its team (builds or text). */
interface BatteryInput { format: FormatId; topK: number; mode: InputMode; members?: DraftMember[]; text?: string }

function batteryOf(record: AssistRecord<BatteryInput>): ActualMatchupResponseDto | null {
  return readResult(record, ActualMatchupResponseDtoSchema);
}

/** 历史计算: the batteries this browser has run (lib/runRecords.ts) — each spends allowance, so each
 * is kept with the data it was run against. Picking one puts its team back in the editor and its
 * result on screen. Built like 导入队伍 beside it: a button and a menu. */
function BatteryHistory({ records, shown, onPick }: {
  records: AssistRecord<BatteryInput>[]; shown: string | null; onPick: (record: AssistRecord<BatteryInput>) => void;
}) {
  const t = useMatchupT();
  const { lang } = useLang();
  const { capabilities } = useRuntime();
  const pop = usePopover();
  const list = useRef<HTMLDivElement>(null);
  const [confirming, setConfirming] = useState(false);
  useFocusOnOpen(pop.open, list, "[role=menuitem]");
  return (
    <div className="team-import battery-history" ref={pop.wrap} onBlur={pop.onBlur}>
      <button ref={pop.trigger} type="button" className={`ghost-btn team-import-btn${pop.open ? " open" : ""}`}
        aria-haspopup="menu" aria-expanded={pop.open} onClick={pop.toggle}>
        <IconHistory size={16} aria-hidden />{t("actual.history")}
        {records.length > 0 && <span className="battery-count num">{records.length}</span>}
      </button>
      {pop.open && (
        <div ref={list} className="duel-pop team-import-pop battery-pop" role="menu" aria-label={t("actual.history")}
          onKeyDown={(event) => roveFocus(event, "[role=menuitem]")}>
          {records.length === 0 && <p className="battery-empty">{t("actual.historyEmpty")}</p>}
          {records.map((record) => {
            const result = batteryOf(record);
            const species = result ? readTeamMembers(result.team).map((m) => m.species)
              : (record.input.members ?? []).map((m) => m.species).filter(Boolean);
            const cutOff = isCutOff(record);
            const { dataChanged } = freshness(record, capabilities, [record.input.format]);
            const badge = record.status === "running" ? (cutOff ? "actual.historyInterrupted" : "actual.historyRunning")
              : dataChanged ? "actual.historyStale" : null;
            return (
              <div key={record.id} className={`battery-row${record.id === shown ? " on" : ""}`}>
                <button type="button" role="menuitem" className="battery-pick" disabled={!result}
                  onClick={() => { if (result) { pop.setOpen(false); onPick(record); } }}>
                  <TeamSprites species={species} />
                  <span className="battery-meta">
                    <span>{t(`format.${record.input.format}`)} · {t("actual.historyTop").replace("{k}", String(record.input.topK))}</span>
                    <span className="num">{relativeTime(record.createdAt, lang, t("actual.justNow"))}</span>
                    {badge && <span className={`battery-badge${dataChanged ? " stale" : ""}`}>{t(badge)}</span>}
                  </span>
                </button>
                <button type="button" className="battery-del" aria-label={t("actual.historyDelete")}
                  title={t("actual.historyDelete")} onClick={() => deleteRecord(record.id)}>×</button>
              </div>
            );
          })}
          {records.some((r) => r.status !== "running") && (
            <div className="battery-foot">
              {confirming ? (
                <>
                  <button type="button" className="linkish danger" onClick={() => { clearRecords("matchup"); setConfirming(false); }}>
                    {t("actual.historyConfirm")}</button>
                  <button type="button" className="linkish" onClick={() => setConfirming(false)}>{t("actual.historyCancel")}</button>
                </>
              ) : (
                <button type="button" className="linkish" onClick={() => setConfirming(true)}>{t("actual.historyClear")}</button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** The note over a result from history whose data has since changed. */
function BatteryFreshness({ record, onRerun }: { record: AssistRecord<BatteryInput>; onRerun: () => void }) {
  const t = useMatchupT();
  const { capabilities } = useRuntime();
  const { dataChanged, ruleChanged } = freshness(record, capabilities, [record.input.format]);
  if (!dataChanged && !ruleChanged) return null;
  return (
    <div className="notice battery-fresh" role="status">
      {record.snapshot.deploymentId === "legacy-session" ? t("actual.legacyNote") : ruleChanged
        ? t("actual.ruleNote").replace("{season}", record.snapshot.season).replace("{rule}", record.snapshot.rule)
        : t("actual.staleNote").replace("{asOf}", record.snapshot.asOf ?? "—")}
      <button type="button" className="linkish" onClick={onRerun}>{t("actual.rerun")}</button>
    </div>
  );
}

export function ActualMatchupWorkspace({ format, onFormatChange }: {
  format: FormatId; onFormatChange: (format: FormatId) => void;
}) {
  const t = useMatchupT();
  const { adapter, capabilities } = useRuntime();
  const { lang } = useLang();
  const dexIndex = useDexIndex();
  const species = dexIndex.status === "ready" ? dexIndex.data : [];
  const stash = useMemo(readStash, []);
  const { records, ready } = useAssistRecords<BatteryInput>("matchup");
  // The history record whose result is on screen, when it came from one.
  const [shownId, setShownId] = useState<string | null>(readSelected("matchup"));
  const showRecord = useCallback((id: string | null) => { setShownId(id); saveSelected("matchup", id); }, []);
  const shownRecord = records.find((record) => record.id === shownId && record.status === "done") ?? null;
  // An older stash may name the retired "from other tools" mode: those teams arrive through the
  // library dock now.
  const [mode, setMode] = useState<InputMode>(stash.mode === "text" ? "text" : "manual");
  const [members, setMembers] = useState<DraftMember[]>(
    () => (stash.members?.length ? stash.members.slice(0, MAX_BUILDS) : [newMember()]));
  const [text, setText] = useState(stash.text ?? "");
  const [topK, setTopK] = useState(stash.topK ?? 30);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quota, setQuota] = useState<{ used: number; limit: number } | null>(null);
  const [response, setResponse] = useState<ActualMatchupResponseDto | null>(() => {
    // Re-validate through the schema rather than trusting readStash's raw JSON.parse: a response
    // cached by an OLDER build can lack fields this build reads unconditionally, and rendering it
    // raw would white-screen the tab. safeParse fills defaults for a compatible cache and drops
    // an incompatible one (same guard the diagnose tab applies to its report).
    const parsed = ActualMatchupResponseDtoSchema.safeParse(stash.response);
    return parsed.success ? parsed.data : null;
  });
  const resultRef = useRef<HTMLDivElement>(null);
  const rail = useSideRail(380, "calc");
  const restored = useRef(false);
  useEffect(() => {
    if (!ready || restored.current) return;
    restored.current = true;
    if (activeBatteryTask) return;
    const saved = records.find(record => record.id === shownId && record.status === "done");
    const previous = saved && batteryOf(saved);
    if (previous) {
      setResponse(previous);
      if (saved.input.format !== format) onFormatChange(saved.input.format);
      return;
    }
    // A legacy session result without an associated record is retained once in the asynchronous
    // history. Its data identity is unknown, so freshness must never label it current.
    const legacy = ActualMatchupResponseDtoSchema.safeParse(stash.response);
    if (!legacy.success) return;
    const record = startRecord<BatteryInput>("matchup", { format: legacy.data.result.format,
      topK: legacy.data.result.top_k, mode, members, text }, lang,
    { season: "unknown", rule: "unknown", asOf: null, deploymentId: "legacy-session", data: {} });
    finishRecord(record.id, legacy.data);
    showRecord(record.id);
    if (legacy.data.result.format !== format) onFormatChange(legacy.data.result.format);
  }, [ready, records, shownId, format, onFormatChange, stash, mode, members, text, lang, showRecord]);

  const pickFromRail = useCallback((_target: CalcRailTarget, entry: DexIndexEntry,
                                    option: BuildOption | null) => {
    const emptyIndex = members.findIndex((member) => !member.species.trim());
    if (emptyIndex < 0 && members.length >= MAX_BUILDS) {
      return { ok: false as const, reason: "full" as const };
    }
    restored.current = true;
    const picked = newMember({
      species: entry.name,
      item: option?.modal.item ?? "",
      ability: option?.modal.ability ?? "",
      nature: option?.modal.nature ?? "",
      moves: option?.modal.moves ?? [],
      spread: { ...EMPTY_SPREAD, ...(option?.modal.sps ?? {}) },
    }, emptyIndex >= 0 ? emptyIndex : members.length);
    setMembers(emptyIndex >= 0
      ? members.map((member, index) => index === emptyIndex ? picked : member)
      : [...members, picked]);
    setMode("manual");
    setResponse(null);
    showRecord(null);
    setError(null);
    return { ok: true as const };
  }, [members, showRecord]);

  const railApi = useMemo<CalcRailApi>(() => ({
    format,
    targets: [{ id: "primary", label: t("actual.inputTitle") }],
    maxItems: MAX_BUILDS,
    pick: pickFromRail,
  }), [format, pickFromRail, t]);

  const loadTeam = (team: unknown, nextFormat?: FormatId) => {
    const loaded = membersFromTeam(team);
    if (!loaded.length) return;
    restored.current = true;
    setMembers(loaded); setMode("manual"); setResponse(null); showRecord(null); setError(null);
    if (nextFormat) onFormatChange(nextFormat);
  };
  useEffect(() => {
    const fill = takeMatchupFill();
    if (fill) loadTeam(fill.team, fill.format);
  }, []);
  useEffect(() => {
    writeStash({ mode, members, text, topK });
  }, [mode, members, text, topK]);

  const sourceTeam = useMemo(() => teamFromMembers(format, members), [format, members]);
  const parsedText = useAsync(() => mode === "text" ? parseTeamInput(text, format, adapter)
    : Promise.resolve({ doc: null, error: null }), [mode, text, format, adapter]);
  const textError = mode === "text" && parsedText.status === "ready" ? parsedText.data.error : null;

  // The library workspace (design §2.4): a saved team becomes ours, a saved Pokémon joins our
  // builds, and our team on screen can be kept. The input panel is the drop zone.
  const bt = useTransferT();
  useLibraryReceiver({ id: "matchup-team", kind: "team", label: bt("transfer.matchup.team"),
    receive: ({ doc }) => {
      if (!membersFromTeam(doc).length) return { ok: false, message: bt("transfer.unreadable") };
      loadTeam(doc, doc.format);
      return { ok: true };
    } });
  useLibraryReceiver({ id: "matchup-mon", kind: "pokemon", label: bt("transfer.matchup.mon"),
    receive: ({ member }) => {
      const emptyIndex = members.findIndex((row) => !row.species.trim());
      if (emptyIndex < 0 && members.length >= MAX_BUILDS) return { ok: false, message: bt("transfer.matchup.full") };
      restored.current = true;
      const added = newMember(member, emptyIndex >= 0 ? emptyIndex : members.length);
      setMembers(emptyIndex >= 0
        ? members.map((row, index) => index === emptyIndex ? added : row) : [...members, added]);
      setMode("manual"); setResponse(null); showRecord(null); setError(null);
      return { ok: true };
    } });
  useLibrarySource({ id: "matchup-keep", origin: "matchup", kind: "team", label: bt("transfer.matchup.save"),
    read: () => mode === "text" ? (parsedText.status === "ready" ? parsedText.data.doc : null)
      : toTeamDoc(sourceTeam, format) });
  const drop = useLibraryDrop(["matchup-team", "matchup-mon"]);
  const sourceCount = mode === "text" ? null : readTeamMembers(sourceTeam).length;
  const estimatedUnits = sourceCount ? Math.ceil(sourceCount * topK / 30) : null;
  const refreshQuota = () => adapter.quota?.().then(
    (all) => setQuota(all.matchup), () => {});
  useEffect(() => { refreshQuota(); }, [adapter]);
  // Re-attach to a battery still in flight from an earlier mount of this workspace.
  useEffect(() => {
    const task = activeBatteryTask;
    if (!task) return;
    let live = true;
    setRunning(true);
    setError(null);
    task.promise.then((next) => {
      if (!live) return;
      setResponse(next);
      const normalized = membersFromTeam(next.team);
      if (normalized.length) setMembers(normalized);
      refreshQuota();
    }, (reason: unknown) => {
      if (live) setError(String(reason));
    }).finally(() => {
      if (live) setRunning(false);
    });
    return () => { live = false; };
  }, []);

  const hasIllegalSpread = mode === "manual" && members.some((member) =>
    Object.values(member.spread).reduce((sum, value) => sum + value, 0) > 66);

  const run = async () => {
    const hasInput = mode === "text" ? !!text.trim() : readTeamMembers(sourceTeam).length > 0;
    if (!hasInput || hasIllegalSpread || textError || running || activeBatteryTask) return;
    setRunning(true); setError(null); setResponse(null);
    const record = startRecord<BatteryInput>("matchup", { format, topK, mode,
      ...(mode === "text" ? { text: text.trim() } : { members }) }, lang, snapshotOf(capabilities));
    showRecord(record.id);
    const promise = adapter.actualMatchup({ format, topK,
      ...(mode === "text" ? { text: text.trim() } : { team: sourceTeam }) });
    const task: ActiveBatteryTask = { promise };
    activeBatteryTask = task;
    try {
      const next = await promise;
      finishRecord(record.id, next);
      setResponse(next);
      const normalized = membersFromTeam(next.team);
      if (normalized.length) setMembers(normalized);
      refreshQuota();
      requestAnimationFrame(() => resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    } catch (reason) {
      // A refused or failed battery leaves no record; its team is still in the editor.
      deleteRecord(record.id);
      setError(String(reason));
    } finally {
      if (activeBatteryTask === task) activeBatteryTask = null;
      setRunning(false);
    }
  };

  return <>
    <RailHandle state={rail} label={t("calc.rail.title")} />
    <CalcRail state={rail} dex={species} tab="actual" api={railApi} />
    <div className="actual-workspace">
    {/* Both guides sit at the TOP of the tab, exactly where the two reference grids put theirs —
      * they describe how to read the whole tab, not just the results section, and having them
      * appear halfway down once a run finished made the three matchup views look unrelated. This
      * replaces the old "calculate with custom sets" disclosure line, which only restated the tab's
      * own name. */}
    <div className="matrix-guide" aria-live="polite">
      <span className="matrix-guide-mark" aria-hidden>↘</span>{t("actual.cellHint")}
    </div>
    <section className="panel actual-input-panel" {...drop}>
      <div className="actual-input-head">
        <div className="actual-input-title">
          <h3>{t("actual.inputTitle")}</h3>
          {mode === "manual" && <span className="actual-input-count num">{members.filter((member) => member.species).length}/{MAX_BUILDS}</span>}
        </div>
        <span className="actual-input-tools">
          <BatteryHistory records={records} shown={shownRecord?.id ?? null} onPick={(record) => {
            const past = batteryOf(record);
            if (!past) return;
            if (record.input.format !== format) onFormatChange(record.input.format);
            const loaded = membersFromTeam(past.team);
            if (loaded.length) { setMembers(loaded); setMode("manual"); }
            setTopK(record.input.topK);
            setError(null);
            setResponse(past);
            showRecord(record.id);
            requestAnimationFrame(() => resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
          }} />
          <TeamImport format={format} replaceCurrent={mode === "text" ? !!text.trim() : members.some(m => !!m.species)} onPick={(doc) => loadTeam(doc)} />
        </span>
        <SegmentedControl kind="tabs" idBase="actual-input" value={mode} onChange={setMode}
          ariaLabel={t("actual.inputMode")} className="seg" items={[
            { id: "manual", label: t("actual.manual") }, { id: "text", label: t("actual.text") },
          ]} />
      </div>
      {mode === "manual" && <div className="actual-members">
        {members.map((member, index) => <MemberEditor key={member.id} member={member} index={index} count={members.length}
          format={format}
          onChange={(next) => setMembers((rows) => rows.map((row) => row.id === member.id ? next : row))}
          onRemove={() => setMembers((rows) => rows.filter((row) => row.id !== member.id))} />)}
        {members.length < MAX_BUILDS && <button type="button" className="actual-add-member"
          onClick={() => setMembers((rows) => [...rows, newMember(undefined, rows.length)])}>
          <span>＋</span><b>{t("actual.addMember")}</b><small>{t("actual.addMemberHint")}</small></button>}
      </div>}
      {mode === "text" && <div className="actual-text-import"><label htmlFor="actual-text">{t("actual.textLabel")}</label>
        <textarea id="actual-text" rows={12} maxLength={16000} value={text} onChange={(e) => setText(e.target.value)}
          placeholder={t("actual.textPlaceholder")} />
        <p>{t("actual.textSupport")}</p>
        {textError && <p className="notice" role="alert">{t(textError === "too-many" ? "actual.textTooMany" : "actual.textInvalid")}</p>}
        </div>}
    </section>
    <section className="panel actual-runbar">
      <div className="actual-topk"><div><b>{t("actual.topK")}</b><span>{t("actual.topKHint")}</span></div>
        <div className="actual-topk-presets">{TOP_K_PRESETS.map((value) => <button type="button" key={value}
          className={topK === value ? "on" : ""} onClick={() => setTopK(value)}>{value}</button>)}
          <label><span>{t("actual.custom")}</span><input className="num" type="number" min={1} max={60} value={topK}
            onChange={(e) => setTopK(Math.max(1, Math.min(60, Number(e.target.value) || 1)))} /></label></div></div>
      <div className="actual-run-action"><span className="actual-budget muted num">
          {!adapter.quota
            ? t("actual.budget.local")
            : quota?.limit === 0
              ? t("actual.budget.unlimited")
              : estimatedUnits == null
                ? t("actual.budget.text").replace("{remaining}", quota
                    ? String(Math.max(0, quota.limit - quota.used)) : "—")
                : t("actual.budget.estimate")
                    .replace("{cost}", String(estimatedUnits))
                    .replace("{remaining}", quota
                      ? String(Math.max(0, quota.limit - quota.used)) : "—")}
        </span>
        <button type="button" className="primary-btn" disabled={running || hasIllegalSpread || !!textError || (mode === "text" ? !text.trim() || parsedText.status === "loading" : readTeamMembers(sourceTeam).length === 0)}
          onClick={run}>{running ? t("actual.running") : t("actual.run")}</button></div>
    </section>
    {running && <section className="panel actual-progress" aria-live="polite"><span className="actual-progress-orbit" aria-hidden />
      <div><h3>{t("actual.progressTitle")}</h3><p>{t("actual.progressScope").replace("{sources}", String(mode === "text" ? "—" : readTeamMembers(sourceTeam).length)).replace("{targets}", String(topK))}</p></div><Elapsed /></section>}
    {error && <div className="notice actual-error">{t("actual.error")}<button type="button" className="linkish" onClick={run}>{t("actual.retry")}</button></div>}
      <div ref={resultRef}>
        {response && shownRecord && <BatteryFreshness record={shownRecord} onRerun={run} />}
        {response && <Suspense fallback={<div className="spinner">{t("state.loading")}</div>}>
          <ActualResult response={response} format={format} sourceTeam={response.team} />
        </Suspense>}
      </div>
    </div>
  </>;
}
