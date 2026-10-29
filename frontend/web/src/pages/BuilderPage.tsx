/** Builder wizard (llm.builder, design §7.3): ONE static form -> a queued server-side job
 * running the full UEP gate chain -> ONE team + correctness confirmation (validate badge +
 * answer-audit pass) + disclosed profile defaults. No candidate comparison and no mid-run
 * questions: the page shows the job's gates as the server reports them, on the home page's flow
 * map. The server holds transient job state; the browser keeps each run as a record
 * (lib/runRecords.ts) — its brief, the gate timeline and the result — and follows a
 * still-running job again by id after a reload. Online runtime only: route/nav are
 * capability-gated and this chunk is lazy — a deployment without the builder backend never
 * downloads it. */
import { BUILDER_TACTICS, BuilderJobDtoSchema, type BuilderJobDto, type BuilderRequestDto } from "@pokemon-champions/protocol";
import { IconAdjustmentsHorizontal, IconChevronUp, IconRefresh } from "@tabler/icons-react";
import { lazy, useEffect, useMemo, useState } from "react";
import type { FormatId } from "@pokemon-champions/protocol";
import { PokemonCombo } from "../components/build/PokemonCombo.tsx";
import { FormatTabs } from "../components/FormatTabs.tsx";
import { PageHeader } from "../components/PageHeader.tsx";
import { SegmentedControl, segmentedPanelId, segmentedTabId } from "../components/SegmentedControl.tsx";
import { ThinkingToggle } from "../components/ThinkingToggle.tsx";
import { useDexByName, useDexIndex } from "../hooks.ts";
import { displayName, useLang } from "../i18n.ts";
import { useAssistT } from "./assist/messages.ts";
import { readTeamMembers } from "../lib/team.ts";
import { HttpError, type BuilderApi } from "../runtime/adapter.ts";
import { useRuntime } from "../runtime/context.tsx";
import { useTransferT } from "../lib/library/transferMessages.ts";
import { BudgetNotice } from "./assist/BudgetNotice.tsx";
import { BuilderPreview } from "./assist/BuilderPreview.tsx";
import { useLibraryDrop, useLibraryReceiver, useLibrarySource } from "../lib/library/workspace.tsx";
import { toTeamDoc } from "../lib/teamDoc.ts";
import type { BuilderFill } from "../lib/builderHandoff.ts";
import { BuildAssembly } from "./assist/BuildAssembly.tsx";
import { failRecord, finishRecord, readResult, readSelected, saveSelected, snapshotOf, startRecord, updateRecord, useAssistRecords, type AssistRecord } from "../lib/runRecords.ts";
import { RecordStrip, ResultBoundary } from "./assist/Workspace.tsx";
import { OnlineNote } from "./assist/OnlineNote.tsx";
import { TeamSprites } from "../components/TeamDots.tsx";
import type { BuildInput, Posture } from "./assist/build/model.ts";
const BuildResult = lazy(() => import("./assist/build/Result.tsx").then(module => ({ default: module.BuildResult })));

type StartError =
  | { kind: "limit" | "busy" | "unavailable" | "budget" | "generic" }
  | { kind: "unresolved"; names: string };

/** POST /api/builder failures, read from the typed error code (never the message text). A spent
 * daily budget normally arrives as a preview job instead; the code covers older servers. */
function startError(e: unknown): StartError {
  if (!(e instanceof HttpError)) return { kind: "generic" };
  if (e.code === "budget_exhausted") return { kind: "budget" };
  if (e.status === 429) return { kind: e.code === "rate_limited" ? "limit" : "busy" };
  if (e.status === 503) return { kind: "unavailable" };
  if (e.code === "unresolved_names") return { kind: "unresolved", names: e.reason ?? "" };
  return { kind: "generic" };
}

/** Terminal job codes -> copy keys (unknown/future codes fall back to generic). */
const FAIL_COPY = {
  no_frame: "builder.error.no_frame",
  generation_failed: "builder.error.generation",
  audit_failed: "builder.error.failed",
  llm_unavailable: "builder.error.unavailable",
  expired: "builder.error.expired",
} as const;

/** The server takes at most this many owned species (and says so if sent more). */
const OWNED_MAX = 30;
/** The goal text the server reads (protocol BuilderRequestDto.goal). */
const GOAL_MAX = 300;

function splitNames(raw: string, cap: number): string[] {
  return raw.split(/[,，、;；\n]/).map((s) => s.trim()).filter(Boolean).slice(0, cap);
}

/** The form as the reader left it (sessionStorage): a draft for this browser session, not a record. */
const DRAFT_KEY = "pc-builder-draft";

function readDraft(): Partial<BuildInput> {
  try {
    return JSON.parse(sessionStorage.getItem(DRAFT_KEY) ?? "{}") as Partial<BuildInput>;
  } catch {
    return {};
  }
}

function writeDraft(draft: BuildInput): void {
  try {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch { /* storage blocked — the draft just won't survive navigation */ }
}

/** A record's job snapshot, read back through the DTO schema (null when none, or unreadable). */
function jobOf(record: AssistRecord<BuildInput>): BuilderJobDto | null {
  return readResult(record, BuilderJobDtoSchema);
}

const SEEN_MAX = 6;   // mirrors builder.SEEN_TEAMS

/** The brief as a comparable key: what is built from it (thinking only changes the explanation). */
function briefKey(input: BuildInput): string {
  return JSON.stringify([input.format, input.posture, input.anchor.trim(), input.owned.trim(),
    input.avoid.trim(), [...input.wants].sort(), (input.goal ?? "").trim()]);
}

type SeenMember = { species: string; item?: string | null };

/** The teams earlier runs of the same brief already showed, newest first: a rerun asks for others. */
function seenTeams(records: AssistRecord<BuildInput>[], input: BuildInput): SeenMember[][] {
  const key = briefKey(input);
  const teams: SeenMember[][] = [];
  for (const record of records) {
    if (record.status !== "done" || briefKey(record.input) !== key) continue;
    const result = jobOf(record)?.result;
    for (const team of result?.candidates?.map((c) => c.team) ?? (result?.team ? [result.team] : [])) {
      const members = membersOf(team);
      if (members.length && teams.length < SEEN_MAX) teams.push(members);
    }
  }
  return teams;
}

/** Species and item per member: a base species holding its own Mega stone battles as that Mega. */
function membersOf(team: unknown): SeenMember[] {
  const members = (team as { pokemon?: unknown } | null)?.pokemon;
  if (!Array.isArray(members)) return [];
  return members.flatMap((m) => {
    const { species, item } = (m ?? {}) as { species?: unknown; item?: unknown };
    return typeof species === "string" ? [{ species, item: typeof item === "string" ? item : null }] : [];
  });
}

/** Jobs being followed, by record. Module scope: a job keeps filling its record while the tab shows
 * another page; a later mount (or a reload) follows any record still running again by its job id.
 * The server holds a job only while it runs, so an unknown id means it lapsed. */
const following = new Map<string, () => void>();

function follow(record: AssistRecord<BuildInput>, api: BuilderApi): void {
  const jobId = record.jobId;
  if (!jobId || following.has(record.id)) return;
  let stopped = false;
  let poll: number | undefined;
  let unsubscribe = () => {};
  const stop = () => {
    stopped = true;
    unsubscribe();
    if (poll !== undefined) window.clearInterval(poll);
    following.delete(record.id);
  };
  const apply = (job: BuilderJobDto) => {
    if (stopped) return;
    if (job.status === "done") { finishRecord(record.id, job); stop(); }
    else if (job.status === "failed") { failRecord(record.id, job.errorCode ?? "generic", job); stop(); }
    else updateRecord(record.id, { result: job });
  };
  const lapsed = (e: unknown) => {
    if (e instanceof HttpError && e.status === 404 && !stopped) { failRecord(record.id, "expired"); stop(); }
  };
  following.set(record.id, stop);
  // Read the snapshot first: a lapsed id is rejected by the cheap GET instead of an SSE timeout.
  void api.job(jobId).then(apply, lapsed);
  // SSE carries live progress; any transport failure falls back to polling the same snapshot.
  unsubscribe = api.subscribe(jobId, apply, () => {
    if (stopped || poll !== undefined) return;
    poll = window.setInterval(() => { api.job(jobId).then(apply, lapsed); }, 2000);
  });
}

/** One line under the brief bar: the choices the run was given. */
function briefFacts(input: BuildInput, t: ReturnType<typeof useAssistT>, anchorName: (name: string) => string): string[] {
  return [
    t(`format.${input.format}`),
    t(`builder.posture.${input.posture}`),
    input.anchor.trim() ? anchorName(input.anchor.trim()) : t("builder.anyCore"),
    ...input.wants.map((w) => t(`tactic.${w}` as Parameters<typeof t>[0])),
    ...(input.goal?.trim() ? [input.goal.trim().length > 24 ? `${input.goal.trim().slice(0, 24)}…` : input.goal.trim()] : []),
  ];
}

function useBriefFacts(input: BuildInput): string[] {
  const t = useAssistT();
  const { lang } = useLang();
  const dexByName = useDexByName();
  return briefFacts(input, t, (raw) => {
    const entry = dexByName.get(raw);
    return entry ? displayName(entry, lang) : raw;
  });
}

/** A build at work — or, folded under its result, how a finished one went: the assembly view. */
function BuildRun({ record, live }: { record: AssistRecord<BuildInput>; live: boolean }) {
  const t = useAssistT();
  const brief = useBriefFacts(record.input);
  const preview = record.input.mode === "preview";
  const assembly = (
    <BuildAssembly job={jobOf(record)} startedAt={record.createdAt} brief={brief} preview={preview}
      finishedAt={live ? undefined : record.updatedAt} live={live} />
  );
  if (!live) {
    return (
      <details className="ar-fold build-fold">
        <summary><span className="ar-run-core still" aria-hidden="true" />{t("asm.process")}</summary>
        {assembly}
      </details>
    );
  }
  return (
    <section className="ar-run build-run" aria-live="polite">
      <header className="ar-run-head">
        <span className="ar-run-core" aria-hidden="true" />
        <h2>{t(preview ? "preview.watching" : "builder.run.title")}</h2>
      </header>
      {assembly}
    </section>
  );
}

/** The brief of the record on screen, folded to one line while its result is read. */
function BriefBar({ input, onEdit, onRerun, busy }: {
  input: BuildInput; onEdit: () => void; onRerun: () => void; busy: boolean;
}) {
  const t = useAssistT();
  const { lang } = useLang();
  const dexByName = useDexByName();
  const name = (raw: string) => {
    const entry = dexByName.get(raw);
    return entry ? displayName(entry, lang) : raw;
  };
  const owned = splitNames(input.owned, OWNED_MAX).length;
  return (
    <div className="aw-brief">
      <span className="aw-brief-facts">
        {briefFacts(input, t, name).map((fact, i) => <span key={i} className="aw-brief-fact">{fact}</span>)}
        {owned > 0 && <span className="aw-brief-fact num">{t("builder.owned")} · {owned}</span>}
      </span>
      <span className="aw-brief-actions">
        <button type="button" className="ar-tool" onClick={onEdit}>
          <IconAdjustmentsHorizontal aria-hidden="true" />{t("builder.brief.edit")}
        </button>
        <button type="button" className="ar-tool" disabled={busy} onClick={onRerun}>
          <IconRefresh aria-hidden="true" />{t("builder.rerun")}
        </button>
      </span>
    </div>
  );
}

export function BuilderPage({ embedded = false, active = true, fill = null, onConsumeFill }: {
  embedded?: boolean;
  /** False while another assist tab is on screen: the library then sees none of this page. */
  active?: boolean;
  /** A brief handed over from the home page: format, playstyle and core only. */
  fill?: BuilderFill | null;
  onConsumeFill?: () => void;
} = {}) {
  const { adapter, capabilities } = useRuntime();
  const t = useAssistT();
  const { lang } = useLang();
  const dex = useDexIndex();
  const dexByName = useDexByName();

  const draft = useMemo(readDraft, []);
  const [format, setFormat] = useState<FormatId>(draft.format ?? "single");
  const [posture, setPosture] = useState<Posture>(draft.posture ?? "balance");
  const [anchor, setAnchor] = useState(draft.anchor ?? "");
  const [owned, setOwned] = useState(draft.owned ?? "");
  const [avoid, setAvoid] = useState(draft.avoid ?? "");
  const [wants, setWants] = useState<Set<string>>(new Set(draft.wants ?? []));
  const [goal, setGoal] = useState(draft.goal ?? "");
  const [thinking, setThinking] = useState(draft.thinking ?? false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<StartError | null>(null);
  const [quota, setQuota] = useState<{ used: number; limit: number } | null>(null);
  useEffect(() => {
    writeDraft({ format, posture, anchor, owned, avoid, wants: [...wants], goal, thinking });
  }, [format, posture, anchor, owned, avoid, wants, goal, thinking]);

  const { records, ready } = useAssistRecords<BuildInput>("build");
  const [selected, setSelected] = useState<string | null>(readSelected("build"));
  const current = records.find((r) => r.id === selected) ?? records[0] ?? null;
  const select = (id: string | null) => { setSelected(id); saveSelected("build", id); };
  // The compared candidate on screen; a different record opens on its first candidate again.
  const [shownCandidate, setShownCandidate] = useState("A");
  useEffect(() => { setShownCandidate("A"); }, [current?.id]);
  // The form opens on its own when there is nothing to read; a record on screen folds it to its brief.
  const [composing, setComposing] = useState<boolean | null>(null);
  const open = composing ?? (ready && !current);

  // A brief from the home page fills its fields (format, playstyle, core, tactics) and leaves the rest
  // of the form alone, opening the form over whatever record is on screen.
  useEffect(() => {
    if (fill === null) return;
    setFormat(fill.format);
    setPosture(fill.posture);
    setAnchor(fill.anchor);
    setWants(new Set(fill.wants));
    setComposing(true);
    onConsumeFill?.();
  }, [fill, onConsumeFill]);

  // Follow every build still running — this visit's, or one a reload left behind.
  useEffect(() => {
    if (!adapter.builder) return;
    for (const record of records) if (record.status === "running") follow(record, adapter.builder);
  }, [records, adapter]);

  useEffect(() => {
    let live = true;
    adapter.quota?.().then((all) => { if (live) setQuota(all.builder); }, () => {});
    return () => { live = false; };
  }, [adapter]);
  const thinkingUnavailable = quota === null || (quota.limit > 0 && quota.limit - quota.used < 2);
  useEffect(() => {
    if (thinkingUnavailable) setThinking(false);
  }, [thinkingUnavailable]);

  // The library workspace (design §2.4): the boxes become the owned list, one boxed Pokémon joins
  // it, and a finished result can be kept. Names go in the reader's language; the server resolves
  // any of the three.
  const bt = useTransferT();
  const ownedNames = () => splitNames(owned, OWNED_MAX);
  const localSpecies = (species: string) => {
    const entry = dexByName.get(species);
    return entry ? displayName(entry, lang) : species;
  };
  useLibraryReceiver(active ? { id: "builder-box", kind: "box", label: bt("transfer.builder.box"),
    receive: ({ species }) => {
      const names = species.map(localSpecies);
      setOwned(names.slice(0, OWNED_MAX).join("、"));
      setComposing(true);
      return names.length > OWNED_MAX
        ? { ok: true, message: bt("transfer.builder.capped").replace(/\{n\}/g, String(OWNED_MAX)) }
        : { ok: true };
    } } : null);
  useLibraryReceiver(active ? { id: "builder-mon", kind: "pokemon", label: bt("transfer.builder.mon"),
    receive: ({ member }) => {
      const current = ownedNames();
      const name = localSpecies(member.species);
      if (!current.includes(name) && current.length < OWNED_MAX) setOwned([...current, name].join("、"));
      setComposing(true);
      return { ok: true };
    } } : null);
  const currentJob = current ? jobOf(current) : null;
  const builtTeam = current?.status === "done"
    ? currentJob?.result?.candidates?.find((c) => c.label === shownCandidate)?.team ?? currentJob?.result?.team ?? null
    : null;
  useLibrarySource(active && builtTeam && current ? { id: "builder-keep", origin: "builder", kind: "team",
    label: bt("transfer.builder.save"), read: () => toTeamDoc(builtTeam, current.input.format) } : null);
  const ownedDrop = useLibraryDrop(["builder-box", "builder-mon"]);

  const loadBrief = (input: BuildInput) => {
    setFormat(input.format);
    setPosture(input.posture);
    setAnchor(input.anchor);
    setOwned(input.owned);
    setAvoid(input.avoid);
    setWants(new Set(input.wants));
    setGoal(input.goal ?? "");
    setThinking(input.thinking && !thinkingUnavailable);
  };

  const submit = async (brief?: BuildInput) => {
    if (!adapter.builder || submitting) return;
    const input: BuildInput = brief ?? { format, posture, anchor, owned, avoid, wants: [...wants], goal, thinking };
    const seen = seenTeams(records, input);
    setError(null);
    setSubmitting(true);
    const req: BuilderRequestDto = {
      format: input.format,
      posture: input.posture,
      lang,
      ...(input.anchor.trim() ? { anchor: input.anchor.trim().slice(0, 100) } : {}),
      ...(input.owned.trim() ? { owned: splitNames(input.owned, OWNED_MAX) } : {}),
      ...(input.avoid.trim() ? { avoid: splitNames(input.avoid, 10) } : {}),
      ...(input.wants.length ? { wants: input.wants as BuilderRequestDto["wants"] } : {}),
      ...(input.goal?.trim() ? { goal: input.goal.trim().slice(0, GOAL_MAX) } : {}),
      ...(seen.length ? { seen } : {}),
      ...(input.thinking && !thinkingUnavailable ? { thinking: true } : {}),
    };
    try {
      const start = await adapter.builder.start(req);
      setQuota(start.quota);
      const record = startRecord<BuildInput>("build",
        { ...input, thinking: !!req.thinking, ...(start.mode === "preview" ? { mode: "preview" as const } : {}) },
        lang, snapshotOf(capabilities), { jobId: start.jobId });
      select(record.id);
      setComposing(false);
      follow(record, adapter.builder);
    } catch (e) {
      setError(startError(e));
    } finally {
      setSubmitting(false);
    }
  };

  const rerun = (record: AssistRecord<BuildInput>) => {
    loadBrief(record.input);
    void submit({ ...record.input, mode: undefined });
  };

  const view = (() => {
    if (!current || open) return null;
    const job = currentJob;
    if (current.status === "running") return <BuildRun record={current} live />;
    if (current.status === "failed") {
      return (
        <>
          <p className="notice qa-error">
            {t(FAIL_COPY[current.error as keyof typeof FAIL_COPY] ?? "builder.error.generic")}
          </p>
          {/* The run's steps stay reviewable after a failure: the facts gathered so far are part of
              the answer, and the step it stopped at is marked. */}
          {job && <BuildRun record={current} live={false} />}
        </>
      );
    }
    if (job?.result) {
      return (
        <>
          <ResultBoundary><BuildResult record={current} job={job} onRerun={() => rerun(current)}
            candidate={shownCandidate} onCandidate={setShownCandidate} /></ResultBoundary>
          <BuildRun record={current} live={false} />
        </>
      );
    }
    if (job?.preview) {
      return (
        <>
          <BudgetNotice />
          <BuilderPreview preview={job.preview} />
          <BuildRun record={current} live={false} />
        </>
      );
    }
    return <article className="ar-card muted-card"><p className="ar-card-note">{t("rec.unreadable")}</p></article>;
  })();

  return (
    <div>
      {!embedded && <PageHeader title={t("builder.title")} description={<OnlineNote />} />}
      <div role={embedded ? "tabpanel" : undefined}
        id={embedded ? segmentedPanelId("assistant-mode", "wizard") : undefined}
        aria-labelledby={embedded ? segmentedTabId("assistant-mode", "wizard") : undefined}>
      <div className="aw">
      <p className="notice page-disclosure">{t("builder.disclosure")}</p>

      <RecordStrip kind="build" title={t("rec.buildTitle")} records={records}
        selected={open ? null : current?.id ?? null}
        onSelect={(id) => { select(id); setComposing(false); }}
        onNew={() => { setComposing(true); setError(null); }}
        card={(record) => {
          const job = jobOf(record);
          const team = job?.result?.team;
          const members = team ? readTeamMembers(team).map((m) => m.species) : [];
          const anchorName = record.input.anchor.trim()
            ? localSpecies(record.input.anchor.trim()) : t("builder.anyCore");
          return {
            formats: [record.input.format],
            body: (
              <>
                <TeamSprites species={members} />
                <span className="aw-card-title">{anchorName}</span>
                <span className="aw-card-sub">
                  {t(`format.${record.input.format}`)} · {t(`builder.posture.${record.input.posture}`)}
                </span>
              </>
            ),
          };
        }} />

      {!open && current && (
        <BriefBar input={current.input} busy={submitting}
          onEdit={() => { loadBrief(current.input); setComposing(true); }}
          onRerun={() => rerun(current)} />
      )}

      {open && (
        <div className="aw-composer form-panel builder-form">
          <div className="builder-field">
            <label>{t("format.single")} / {t("format.double")}</label>
            <FormatTabs format={format} onChange={setFormat} />
          </div>
          <div className="builder-field">
            <label>{t("builder.posture")}</label>
            <SegmentedControl kind="radio" value={posture} onChange={setPosture}
              ariaLabel={t("builder.posture")} className="seg"
              items={(["offense", "balance", "defense"] as const).map((id) => ({
                id,
                label: t(`builder.posture.${id}`),
              }))} />
          </div>
          <div className="builder-field">
            <label htmlFor="builder-anchor">{t("builder.anchor")}</label>
            <PokemonCombo id="builder-anchor" value={anchor}
              maxLength={100} onValueChange={setAnchor}
              placeholder={t("builder.anchorHint")}
              dex={dex.status === "ready" ? dex.data : []} />
          </div>
          <div className="builder-field" {...ownedDrop}>
            <label htmlFor="builder-owned">{t("builder.owned")}</label>
            <textarea id="builder-owned" rows={2} value={owned}
                      onChange={(e) => setOwned(e.target.value)}
                      placeholder={t("builder.ownedHint")} />
          </div>
          <div className="builder-field">
            <label htmlFor="builder-avoid">{t("builder.avoid")}</label>
            <textarea id="builder-avoid" rows={1} value={avoid}
                      onChange={(e) => setAvoid(e.target.value)} />
          </div>
          <div className="builder-field">
            <label htmlFor="builder-goal">{t("builder.goal")}</label>
            <textarea id="builder-goal" rows={2} value={goal} maxLength={GOAL_MAX}
                      onChange={(e) => setGoal(e.target.value)}
                      placeholder={t("builder.goalHint")} />
          </div>
          <div className="builder-field">
            <label>{t("builder.tactics")}</label>
            <div className="builder-tactics">
              {BUILDER_TACTICS.map((tac) => (
                <label key={tac} className={wants.has(tac) ? "on" : ""}>
                  <input type="checkbox" checked={wants.has(tac)}
                         onChange={(e) => {
                           const next = new Set(wants);
                           if (e.target.checked) next.add(tac);
                           else next.delete(tac);
                           setWants(next);
                         }} />
                  {t(`tactic.${tac}`)}
                </label>
              ))}
            </div>
          </div>
          {/* The submit cluster — thinking mode over the quota and the start button — is built and
              placed exactly as the diagnose tab's, so switching tabs moves none of it. */}
          <div className="assist-submit">
            <ThinkingToggle checked={thinking} onChange={setThinking}
              disabled={submitting || thinkingUnavailable} label={t("thinking.label")}
              tip={`${t("builder.thinkingTip")}${thinkingUnavailable && quota !== null
                ? ` ${t("thinking.insufficient")}` : ""}`} />
            <div className="builder-actions">
              {current && (
                <button type="button" className="ar-tool" onClick={() => setComposing(false)}>
                  <IconChevronUp aria-hidden="true" />{t("builder.brief.collapse")}
                </button>
              )}
              <span className={`aw-quota num${quota ? "" : " pending"}`}>
                {t("builder.quota")} {!quota ? "—" : quota.limit === 0
                  ? t("quota.unlimited") : `${quota.used}/${quota.limit}`}
              </span>
              <button type="button" className="primary-btn"
                      disabled={submitting || !!(quota && quota.limit > 0 && quota.used >= quota.limit)}
                      onClick={() => void submit()}>
                {submitting ? t("builder.starting") : t("builder.start")}
              </button>
            </div>
          </div>
        </div>
      )}

      {error?.kind === "budget" && <BudgetNotice />}
      {error && error.kind !== "budget" && (
        <p className="notice qa-error">
          {error.kind === "unresolved"
            ? `${t("builder.error.unresolved")}${error.names}`
            : t(`builder.error.${error.kind}`)}
        </p>
      )}
      {view}
      </div>
      </div>
    </div>
  );
}
