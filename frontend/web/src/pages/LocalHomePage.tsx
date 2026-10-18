/** The Console's front page (design §2.5): the reader's own team, what waits on them, and where
 * each AI session stands, with the deterministic tools a step away. It speaks the online home's
 * visual language (stage surface, section marks) at working scale, with no showcase art. */
import {
  ActualMatchupResponseDtoSchema, type FormatId, type SessionDto, type SessionOverviewDto,
} from "@pokemon-champions/protocol";
import {
  IconAlertTriangle, IconArrowRight, IconCalculator, IconChartBar, IconCheck, IconChevronRight, IconFolder,
  IconHistory, IconPlus, IconShield, IconTargetArrow, IconTimeline, IconTrophy, IconUsers,
} from "@tabler/icons-react";
import { useMemo, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { AdaptiveSelect } from "../components/AdaptiveSelect.tsx";
import { GameImage } from "../components/GameImage.tsx";
import { TeamSprites } from "../components/TeamDots.tsx";
import { ConsoleSectionHead } from "../components/session/ConsoleSection.tsx";
import { CopyLine } from "../components/session/CopyLine.tsx";
import { LiveBadge } from "../components/session/LiveBadge.tsx";
import { NewTaskForm, type Task } from "../components/session/NewTaskForm.tsx";
import { SessionActivity } from "../components/session/SessionActivity.tsx";
import { SessionLive } from "../components/session/SessionLive.tsx";
import { sessionTitle } from "../components/session/title.ts";
import { SessionState } from "../components/session/SessionState.tsx";
import { useConsoleStatus, useSessionVersion } from "../components/session/live.ts";
import { useSessionCopyT } from "../components/session/messages.ts";
import { useActiveTeam } from "../components/session/useActiveTeam.ts";
import { formatDay, speciesLabel, speciesSlug, useRuleChange } from "../components/library/shared.tsx";
import { useAsync } from "../hooks.ts";
import { useLang, useT } from "../i18n.ts";
import { teamDocOf } from "../lib/library/repo.ts";
import {
  freshness, readResult, relativeTime, saveSelected, useAssistRecords, type AssistRecord,
} from "../lib/runRecords.ts";
import { readTeamMembers, stashCalcTeams, stashMatchupFill, stashTuneFill, teamToCalcMembers } from "../lib/team.ts";
import { useRuntime } from "../runtime/context.tsx";
import { useLocalT } from "./local/messages.ts";
import "../styles.local.css";

const ATTENTION = new Set(["awaiting", "questions", "needs_changes"]);
const CONTINUE_ROWS = 4;

function useSessionList(teamId?: string) {
  const { adapter } = useRuntime();
  const sessions = adapter.sessions;
  const [retry, setRetry] = useState(0);
  const version = useSessionVersion();
  const list = useAsync(() => sessions?.overview(teamId) ?? Promise.resolve({sessions:[],totalCount:0,attentionCount:0,relatedCount:0}),
    [sessions, teamId, version, retry]);
  // Keep the last good list on screen while a change is being re-read.
  const [last, setLast] = useState<{teamId?: string; data: SessionOverviewDto} | null>(null);
  if (list.status === "ready" && list.data !== last?.data) setLast({teamId, data:list.data});
  return { list, data: list.status === "ready" ? list.data : last?.teamId === teamId ? last?.data : null,
    retry: () => setRetry((value) => value + 1) };
}


function Attention({ sessions, count }: { sessions: SessionDto[]; count: number }) {
  const t = useLocalT();
  if (!sessions.length) return null;
  return <section className="console-attention" aria-labelledby="console-attention-heading">
    <h2 id="console-attention-heading"><IconAlertTriangle size={18} aria-hidden />{t("attention")}<span className="num">{count}</span></h2>
    <div>{sessions.slice(0, 3).map((session) => <Link key={session.id} to={`/sessions/${session.id}`} className="console-attention-item">
      <SessionState session={session} />
      <strong>{sessionTitle(session)}</strong>
      <span className="console-attention-go">{t("attentionOpen")}<IconArrowRight size={16} aria-hidden /></span>
    </Link>)}</div>
  </section>;
}

function Setup({ hasTeam, hasSession, onStart }: { hasTeam: boolean; hasSession: boolean; onStart: () => void }) {
  const t = useLocalT();
  const status = useConsoleStatus();
  const companion = status?.companion ?? [];
  const agentReady = companion.some((row) => row.status === "installed");
  const outdated = !agentReady && companion.some((row) => row.status === "outdated");
  const steps = [
    { id: "team", done: hasTeam, title: t("setupTeam"), body: t("setupTeamBody"),
      action: <Link className="second-btn" to="/teams"><IconPlus size={16} aria-hidden />{t("import")}</Link> },
    { id: "agent", done: agentReady, title: t("setupAgent"), body: t(outdated ? "setupAgentOutdated" : "setupAgentBody"),
      action: status ? <CopyLine code text={`${status.pcuiCommand} install-agent`} /> : null },
    { id: "task", done: hasSession, title: t("setupTask"), body: t("setupTaskBody"),
      action: <button type="button" className="second-btn" onClick={onStart}>{t("setupStart")}</button> },
  ];
  const stale = status?.projectionStale
    ? <p className="console-stale" role="status"><IconAlertTriangle size={17} aria-hidden />{t("projectionStale")}</p> : null;
  // Unknown until the bridge answers: never flash a checklist the reader has already finished.
  if (!status || steps.every((step) => step.done)) return stale;
  return <>{stale}<section className="console-setup" aria-labelledby="console-setup-heading">
    <ConsoleSectionHead id="console-setup-heading" title={t("setup")} mark="flow" />
    <ol>{steps.map((step, index) => <li key={step.id} className={step.done ? "done" : ""}>
      <span className="console-step num" aria-hidden>{step.done ? <IconCheck size={15} /> : index + 1}</span>
      <div><strong>{step.title}</strong>{step.done ? <small>{t("setupDone")}</small> : <><p>{step.body}</p>{step.action}</>}</div>
    </li>)}</ol>
  </section></>;
}

function ContinueWorking({ state }: { state: ReturnType<typeof useSessionList> }) {
  const t = useLocalT();
  const { list } = state;
  const data = state.data?.sessions;
  return <section className="local-panel" aria-labelledby="local-continue-heading">
    <ConsoleSectionHead id="local-continue-heading" title={t("recent")} mark="core">
      <Link className="console-head-link" to="/sessions">{t("allSessions")}<IconChevronRight aria-hidden /></Link>
    </ConsoleSectionHead>
    {!data && list.status === "loading" ? <p className="local-muted" role="status">{t("state.loading")}</p>
      : !data ? <div className="local-empty"><p>{t("sessionError")}</p>
        <button type="button" className="second-btn" onClick={state.retry}>{t("retry")}</button></div>
      : data.length ? <div className="local-session-list">{data.slice(0, CONTINUE_ROWS).map((session) => (
        <Link className="local-session" key={session.id} to={`/sessions/${session.id}`}>
          <span className="local-session-top"><SessionState session={session} /><SessionActivity session={session} /></span>
          <strong title={sessionTitle(session)}>{sessionTitle(session)}</strong>
          <SessionLive session={session} />
          <IconArrowRight className="local-session-arrow" aria-hidden />
        </Link>
      ))}</div> : <div className="local-empty"><IconHistory size={26} aria-hidden />
        <h3>{t("emptySessions")}</h3><p>{t("emptySessionsBody")}</p></div>}
  </section>;
}

function Tools() {
  const t = useLocalT();
  const { can } = useRuntime();
  const tools = [
    { id: "damage", body: "damageBody", to: "/calc", icon: IconCalculator, show: can("calc.damage") },
    { id: "speed", body: "speedBody", to: "/calc?tab=speed", icon: IconTimeline, show: can("calc.speedline") },
    { id: "durability", body: "durabilityBody", to: "/calc?tab=tune", icon: IconShield, show: can("team.tune") },
    { id: "inference", body: "inferenceBody", to: "/calc?tab=infer", icon: IconTargetArrow, show: can("calc.damage") },
    { id: "official", body: "officialBody", to: "/ranking", icon: IconChartBar, show: true },
    { id: "competitive", body: "competitiveBody", to: "/ranking?src=top", icon: IconTrophy, show: true },
  ] as const;
  return <section className="local-panel" aria-labelledby="local-tools-heading">
    <ConsoleSectionHead id="local-tools-heading" title={t("tools")} mark="lab" />
    <div className="local-tools">{tools.filter((tool) => tool.show).map((tool) => <Link key={tool.id} className="local-tool" to={tool.to}>
      <span className="local-tool-icon"><tool.icon size={20} aria-hidden /></span>
      <span><strong>{t(tool.id)}</strong><small>{t(tool.body)}</small></span>
    </Link>)}</div>
  </section>;
}

interface BatteryInput { format: FormatId; topK: number; members?: Array<{ species?: string }> }

function RecentResults() {
  const t = useLocalT();
  const ct = useSessionCopyT();
  const { lang } = useLang();
  const st = useT();
  const { capabilities, can } = useRuntime();
  const navigate = useNavigate();
  const { records, ready } = useAssistRecords<BatteryInput>("matchup");
  if (!can("team.matchup")) return null;
  const open = (record: AssistRecord<BatteryInput>) => { saveSelected("matchup", record.id); navigate("/matchup?mode=actual"); };
  return <section className="local-panel" aria-labelledby="local-results-heading">
    <ConsoleSectionHead id="local-results-heading" title={t("results")} mark="glance" />
    {!ready ? <p className="local-muted" role="status">{t("state.loading")}</p>
      : records.length ? <div className="local-rows">{records.slice(0, 3).map((record) => {
        const result = readResult(record, ActualMatchupResponseDtoSchema);
        const species = result ? readTeamMembers(result.team).map((member) => member.species)
          : (record.input.members ?? []).flatMap((member) => member.species ? [member.species] : []);
        const stale = freshness(record, capabilities, [record.input.format]).dataChanged;
        return <button type="button" key={record.id} className="local-row" disabled={!result} onClick={() => open(record)}>
          <TeamSprites species={species} />
          <span className="local-row-meta"><span>{st(`format.${record.input.format}`)} · {t("resultsTop").replace("{k}", String(record.input.topK))}</span>
            <small className="num">{relativeTime(record.createdAt, lang, ct("activity.justNow"))}</small></span>
          {record.status === "running" ? <span className="local-tag">{t("resultsRunning")}</span>
            : stale ? <span className="local-tag warn">{t("resultsStale")}</span> : null}
        </button>;
      })}</div> : <p className="local-muted">{t("resultsEmpty")}</p>}
  </section>;
}

function RecentTeams({ team }: { team: ReturnType<typeof useActiveTeam> }) {
  const t = useLocalT();
  const st = useT();
  const { lang } = useLang();
  return <section className="local-panel" aria-labelledby="local-recent-teams-heading">
    <ConsoleSectionHead id="local-recent-teams-heading" title={t("recentlyEdited")} mark="team">
      <Link className="console-head-link" to="/teams">{t("manage")}<IconChevronRight aria-hidden /></Link>
    </ConsoleSectionHead>
    {team.teams.length ? <div className="local-rows">{team.teams.slice(0, 3).map((row) => {
      const name = team.label(row.name, teamDocOf(row, team.box)).text;
      const current = row.id === team.record?.id;
      return <button type="button" className={`local-row${current ? " current" : ""}`} key={row.id}
        aria-label={`${t("useTeam")} · ${name}`} aria-pressed={current}
        onClick={() => team.setActive({ id: row.id, format: row.format })}>
        <span className="local-row-meta"><span>{name}</span>
          <small>{st(`format.${row.format}`)} · {formatDay(row.updatedAt, lang)}</small></span>
        {current ? <span className="local-tag brand">{t("current")}</span> : <IconArrowRight size={16} aria-hidden />}
      </button>;
    })}</div> : <p className="local-muted">{t("emptyRecent")}</p>}
  </section>;
}

/** Where everything on this page is kept, and how to back it up. */
function Workspace() {
  const t = useLocalT();
  const { adapter } = useRuntime();
  const info = useAsync(() => adapter.storage?.info() ?? Promise.resolve(null), [adapter.storage]);
  if (info.status !== "ready" || !info.data) return null;
  return <section className="local-workspace" aria-labelledby="local-workspace-heading">
    <span className="local-workspace-icon" aria-hidden><IconFolder size={18} /></span>
    <div>
      <h2 id="local-workspace-heading">{t("workspaceTitle")}</h2>
      <code title={info.data.path}>{info.data.path}</code>
      <p>{t("workspaceBody")}</p>
    </div>
    <Link className="second-btn" to="/teams">{t("workspaceManage")}</Link>
  </section>;
}

export function LocalHomePage() {
  const t = useLocalT();
  const st = useT();
  const { lang } = useLang();
  const { can } = useRuntime();
  const team = useActiveTeam();
  const ruleChange = useRuleChange();
  const sessions = useSessionList(team.record?.id);
  const navigate = useNavigate();
  const location = useLocation();
  const [task, setTask] = useState<Task>("build");
  const [focusVersion, setFocusVersion] = useState(0);
  const { library, teams, record, doc, name: teamName, dex } = team;
  const changed = record ? ruleChange(record.snapshot) : null;
  const attention = useMemo(() => (sessions.data?.sessions ?? []).filter((session) => ATTENTION.has(session.state ?? "")), [sessions.data]);
  const related = sessions.data?.relatedCount ?? 0;
  const openTask = (kind: Task) => {
    setTask(kind); setFocusVersion((value) => value + 1);
    document.getElementById("local-request")?.scrollIntoView({ block: "center",
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  };
  const sendCalc = () => {
    if (doc) stashCalcTeams({ format: doc.format, attackers: teamToCalcMembers(doc), defenders: [] });
    navigate("/calc?tab=damage");
  };
  const legacy = new URLSearchParams(location.search);
  if (["view", "format", "trend"].some((key) => legacy.has(key))) {
    return <Navigate to={`/ranking${location.search}`} replace />;
  }
  return <div className="local-home stage-tokens">
    <header className="console-hero">
      <h1><span className="console-capsule"><i aria-hidden />{t("workspace")}</span><span className="console-brand">{t("title")}</span></h1>
      <LiveBadge />
    </header>
    <Attention sessions={attention} count={sessions.data?.attentionCount ?? 0} />
    {/* Unknown counts as done, so a step never flashes as open while its data loads. */}
    <Setup hasTeam={library === undefined || teams.length > 0} hasSession={!sessions.data || sessions.data.totalCount > 0}
      onStart={() => openTask("build")} />
    <div className="local-grid">
      <section className="local-panel local-request" id="local-request" aria-labelledby="local-request-heading">
        <NewTaskHead />
        <NewTaskForm team={doc} teamName={teamName} teamId={record?.id} startTask={task} focusVersion={focusVersion} />
      </section>
      <ContinueWorking state={sessions} />
      <section className="local-panel local-team" aria-labelledby="local-team-heading">
        <ConsoleSectionHead id="local-team-heading" title={t("currentTeam")} mark="team">
          {record && related ? <Link className="console-head-link" to={`/sessions?team=${encodeURIComponent(record.id)}`}>
            {t("related").replace("{n}", String(related))}<IconChevronRight aria-hidden /></Link> : null}
          <Link className="console-head-link" to="/teams">{t("manage")}<IconChevronRight aria-hidden /></Link>
        </ConsoleSectionHead>
        {teams.length ? <AdaptiveSelect className="local-team-select" aria-label={t("chooseTeam")} value={record?.id ?? ""}
          onValueChange={(id) => { const found = teams.find((row) => row.id === id); if (found) team.setActive({ id, format: found.format }); }}>
          <option value="" disabled>{t("chooseTeam")}</option>
          {teams.map((row) => <option key={row.id} value={row.id}>{team.label(row.name, teamDocOf(row, team.box)).text} · {st(`format.${row.format}`)}</option>)}
        </AdaptiveSelect> : null}
        {library === undefined ? <p className="local-muted" role="status">{t("state.loading")}</p>
          : doc ? <>
            <div className="local-roster">{Array.from({ length: 6 }, (_, index) => {
              const mon = doc.pokemon[index];
              return <div key={index} className={`local-member${mon ? "" : " empty"}`}>
                {mon ? <><GameImage assetKey={`pokemon:${speciesSlug(mon.species, dex)}`} role="dense" alt="" />
                  <span title={speciesLabel(mon.species, dex, lang)}>{speciesLabel(mon.species, dex, lang)}</span></>
                  : <span className="local-slot num">{String(index + 1).padStart(2, "0")}</span>}
              </div>;
            })}</div>
            {changed ? <p className="local-rule-note">{t("ruleChanged")}</p> : null}
            <div className="local-team-actions">
              <button type="button" className="primary-btn" onClick={() => openTask("diagnose")}>{t("aiDiagnose")}<IconArrowRight size={16} aria-hidden /></button>
              <button type="button" className="second-btn" onClick={() => openTask("rework")}>{t("rework")}</button>
              {can("team.matchup") ? <button type="button" className="second-btn" onClick={() => {
                stashMatchupFill({ source: "other", label: teamName, format: doc.format, team: doc }); navigate("/matchup?mode=actual");
              }}>{t("matchup")}</button> : null}
              {can("team.tune") ? <button type="button" className="second-btn" onClick={() => {
                stashTuneFill({ team: doc, label: teamName }); navigate("/calc?tab=tune");
              }}>{t("tune")}</button> : null}
            </div>
          </> : <div className="local-empty local-team-empty"><span className="local-empty-mark"><IconUsers size={28} aria-hidden /></span>
            <h3>{teams.length ? t("chooseTeam") : t("emptyTitle")}</h3>
            <p>{library === "unavailable" ? t("unavailable") : teams.length ? t("selectHint") : t("emptyBody")}</p>
            <div><Link className="primary-btn" to="/teams"><IconPlus size={17} aria-hidden />{t("import")}</Link>
              <button type="button" className="second-btn" onClick={sendCalc}>{t("damage")}</button></div>
          </div>}
      </section>
      <Tools />
    </div>
    <div className="local-grid local-grid-secondary">
      <RecentResults />
      <RecentTeams team={team} />
    </div>
    <Workspace />
  </div>;
}

function NewTaskHead() {
  const t = useSessionCopyT();
  return <ConsoleSectionHead id="local-request-heading" title={t("newTask")} mark="core" />;
}
