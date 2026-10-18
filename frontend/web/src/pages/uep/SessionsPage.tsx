/** UEP session list + creation (design §6). Local runtime only — this file lives in the
 * lazily-imported uep chunk behind the team.uep capability; the online runtime never
 * downloads it. Sessions start from the same form as the Console home, and the list follows the
 * bridge's feed: a new session, an assistant's artifact or a reconnect re-reads it. */
import { UEP_GATE_KINDS, type SessionDto } from "@pokemon-champions/protocol";
import { IconArrowRight } from "@tabler/icons-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PageHeader } from "../../components/PageHeader.tsx";
import { ConsoleSectionHead } from "../../components/session/ConsoleSection.tsx";
import { LiveBadge } from "../../components/session/LiveBadge.tsx";
import { NewTaskForm } from "../../components/session/NewTaskForm.tsx";
import { SessionActivity } from "../../components/session/SessionActivity.tsx";
import { SessionLive } from "../../components/session/SessionLive.tsx";
import { sessionTeamId, sessionTitle } from "../../components/session/title.ts";
import { SessionState, sessionState } from "../../components/session/SessionState.tsx";
import { useSessionVersion } from "../../components/session/live.ts";
import { useSessionCopyT } from "../../components/session/messages.ts";
import { useActiveTeam } from "../../components/session/useActiveTeam.ts";
import { useAsync } from "../../hooks.ts";
import { useRuntime } from "../../runtime/context.tsx";
import { useUepT } from "./messages.ts";
import "../../styles.local.css";

const FILTERS = ["all", "attention", "active", "done", "archived"] as const;
type Filter = typeof FILTERS[number];
const NEEDS_READER = new Set(["awaiting", "questions", "needs_changes"]);

function matches(filter: Filter, session: SessionDto): boolean {
  if (filter === "archived") return !!session.archived;
  if (session.archived) return false;
  const state = sessionState(session);
  if (filter === "attention") return NEEDS_READER.has(state);
  if (filter === "done") return state === "done";
  if (filter === "active") return state !== "done";
  return true;
}


/** How far the gate sequence got: one tick per gate kind the session has produced. */
export function GateTicks({ session }: { session: SessionDto }) {
  return <span className="gate-ticks" aria-hidden>{UEP_GATE_KINDS.map((kind) =>
    <i key={kind} className={session.heads[kind] ? "on" : ""} />)}</span>;
}

export function SessionChips({ session }: { session: SessionDto }) {
  const ct = useSessionCopyT();
  const t = useUepT();
  const { format, requestKind, teamName } = session.meta;
  const task = typeof requestKind === "string" ? useSessionCopyT.optionalKey(`task.${requestKind}`) : null;
  return <span className="session-chips">
    {format === "single" || format === "double" ? <span className="session-chip">{t(`format.${format}`)}</span> : null}
    {task ? <span className="session-chip">{ct(task)}</span> : null}
    {typeof teamName === "string" && teamName ? <span className="session-chip team" title={teamName}>{teamName}</span> : null}
  </span>;
}

export function SessionsPage() {
  const { adapter } = useRuntime();
  const t = useUepT();
  const ct = useSessionCopyT();
  const sessions = adapter.sessions;
  const team = useActiveTeam();
  const [filter, setFilter] = useState<Filter>("all");
  const [params, setParams] = useSearchParams();
  const teamFilter = params.get("team");
  const version = useSessionVersion();
  const list = useAsync(() => (sessions ? sessions.list({ archived: "include" }) : Promise.resolve([])), [sessions, version]);
  const [last, setLast] = useState<SessionDto[] | null>(null);
  if (list.status === "ready" && list.data !== last) setLast(list.data);
  const all = list.status === "ready" ? list.data : last;
  const data = useMemo(() => all && teamFilter ? all.filter((session) => sessionTeamId(session) === teamFilter) : all,
    [all, teamFilter]);
  const counts = useMemo(() => Object.fromEntries(FILTERS.map((key) =>
    [key, (data ?? []).filter((session) => matches(key, session)).length])) as Record<Filter, number>, [data]);
  const shown = (data ?? []).filter((session) => matches(filter, session));
  const teamName = teamFilter ? team.teams.find((row) => row.id === teamFilter) : undefined;

  if (!sessions) return <div className="notice">{t("uep.unavailable")}</div>;

  return (
    <div className="console-sessions stage-tokens">
      <PageHeader title={t("uep.title")} description={t("uep.howto")}><LiveBadge /></PageHeader>
      <div className="sessions-layout">
        <section className="local-panel" aria-labelledby="sessions-new-heading">
          <ConsoleSectionHead id="sessions-new-heading" title={ct("newTask")} mark="core" />
          <NewTaskForm team={team.doc} teamName={team.name} teamId={team.record?.id} />
        </section>
        <section className="local-panel sessions-index" aria-labelledby="sessions-list-heading">
          <ConsoleSectionHead id="sessions-list-heading" title={t("uep.recent")} mark="flow" />
          {teamFilter ? <p className="session-team-filter">
            {t("uep.team").replace("{name}", teamName ? team.label(teamName.name, null).text : teamFilter)}
            <button type="button" className="linkish" onClick={() => { params.delete("team"); setParams(params, { replace: true }); }}>
              {t("uep.teamClear")}</button></p> : null}
          <div className="session-filters" role="group" aria-label={t("uep.filter")}>{FILTERS.map((key) =>
            <button key={key} type="button" aria-pressed={filter === key} onClick={() => setFilter(key)}>
              {t(`uep.filter.${key}`)}<span className="num">{counts[key]}</span></button>)}</div>
          {!data && list.status === "loading" && <div className="spinner">{t("state.loading")}</div>}
          {!data && list.status === "error" && <div className="notice">{t("state.errorDetail")}</div>}
          {data && (shown.length === 0
            ? <div className="sessions-empty muted">{t(filter === "all" ? "uep.empty" : "uep.emptyFilter")}</div>
            : <div className="session-list">{shown.map((session) => (
              <Link key={session.id} to={`/sessions/${session.id}`} className={`session-row state-${sessionState(session)}`}>
                <span className="session-row-top"><SessionState session={session} /><SessionActivity session={session} /></span>
                <strong className="session-intent">{sessionTitle(session)}</strong>
                <SessionLive session={session} />
                <span className="session-row-foot"><SessionChips session={session} /><GateTicks session={session} /></span>
                <IconArrowRight className="session-row-arrow" size={16} aria-hidden />
              </Link>
            ))}</div>)}
        </section>
      </div>
    </div>
  );
}
