/** One way to start a Console session, on the home page and the sessions page alike: the request,
 * a task kind, the format, and optionally the current team as a build snapshot (design §6). The
 * draft survives navigation within the browser session until a session is created. */
import type { FormatId, TeamDoc } from "@pokemon-champions/protocol";
import { IconArrowRight } from "@tabler/icons-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useT } from "../../i18n.ts";
import { useRuntime } from "../../runtime/context.tsx";
import { FormatTabs } from "../FormatTabs.tsx";
import { useSessionCopyT } from "./messages.ts";
import "./console.css";

export const TASKS = ["build", "rework", "diagnose", "tune"] as const;
export type Task = typeof TASKS[number];
const PLACEHOLDER = { build: "buildPh", rework: "reworkPh", diagnose: "diagnosePh", tune: "tunePh" } as const;
const DRAFT_KEY = "pc-local-request-v1";

function readDraft(): string {
  try { return sessionStorage.getItem(DRAFT_KEY) ?? ""; } catch { return ""; }
}

export function NewTaskForm({ team, teamName, teamId, startTask = "build", focusVersion = 0 }: {
  team: TeamDoc | null; teamName: string;
  /** The library team the snapshot comes from, so results and related sessions can find it again. */
  teamId?: string; startTask?: Task; focusVersion?: number;
}) {
  const t = useSessionCopyT();
  const st = useT();
  const { adapter, capabilities } = useRuntime();
  const navigate = useNavigate();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [intent, setIntent] = useState(readDraft);
  const [task, setTask] = useState<Task>(startTask);
  const [format, setFormat] = useState<FormatId>(team?.format ?? "double");
  const [attach, setAttach] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(false);
  const attached = attach && team ? team : null;
  useEffect(() => {
    if (focusVersion) { setTask(startTask); textarea.current?.focus(); }
  }, [focusVersion, startTask]);
  const update = (value: string) => {
    setIntent(value);
    try { sessionStorage.setItem(DRAFT_KEY, value); } catch { /* the draft stays in memory */ }
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!intent.trim() || !adapter.sessions || creating) return;
    setCreating(true); setError(false);
    try {
      const session = await adapter.sessions.create({
        intent: intent.trim(), requestKind: task, format: attached?.format ?? format,
        ...(attached ? { team: attached, teamName, ...(teamId ? { teamRef: { id: teamId } } : {}) } : {}),
        environment: { ...capabilities.environment },
      });
      update("");
      navigate(`/sessions/${session.id}`);
    } catch { setError(true); setCreating(false); }
  };
  return <form className="new-task" onSubmit={submit}>
    <div className="new-task-kinds" role="group" aria-label={t("newTask")}>{TASKS.map((kind) =>
      <button key={kind} type="button" aria-pressed={task === kind} onClick={() => setTask(kind)}>{t(`task.${kind}`)}</button>)}</div>
    <label className="sr-only" htmlFor="new-task-intent">{t("request")}</label>
    <textarea id="new-task-intent" ref={textarea} value={intent} onChange={(event) => update(event.target.value)}
      rows={4} maxLength={8000} placeholder={t(PLACEHOLDER[task])} />
    <div className="new-task-options">
      {team ? <label className="new-task-attach"><input type="checkbox" checked={attach} onChange={(event) => setAttach(event.target.checked)} />
        <span>{t("attach")}<small>{teamName}</small></span></label> : null}
      {attached ? <span className="new-task-format">{st(`format.${attached.format}`)} · {t("attached")}</span>
        : <FormatTabs format={format} onChange={setFormat} />}
    </div>
    <div className="new-task-submit"><p>{t("requestHint")}</p>
      <button type="submit" className="primary-btn" disabled={creating || !intent.trim()}>
        {creating ? t("creating") : t("create")}<IconArrowRight size={17} aria-hidden /></button></div>
    {error ? <p className="new-task-error" role="alert">{t("createError")}</p> : null}
  </form>;
}
