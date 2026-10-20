/** What the bridge sees happening right now: an operator it is running for the assistant, a run
 * that produced nothing, or an assistant waiting on the reader. These are live observations from
 * the daemon, so unlike the ledger they may say "running" or "waiting". */
import type { SessionDto } from "@pokemon-champions/protocol";
import { useEffect, useState } from "react";
import { useSessionCopyT } from "./messages.ts";
import "./console.css";

export function waitingNow(session: SessionDto, now = Date.now()): boolean {
  return !!session.waiting && Date.parse(session.waiting.until) > now;
}

/** Expiry need not produce an SSE event. Every view using this state must wake at the deadline. */
export function useWaitingNow(session: SessionDto): boolean {
  const [, refresh] = useState(0);
  const until = session.waiting?.until;
  useEffect(() => {
    if (!until) return;
    const remaining = Date.parse(until) - Date.now();
    if (!Number.isFinite(remaining) || remaining <= 0) return;
    const timer = window.setTimeout(() => refresh(n => n + 1), remaining + 1);
    return () => window.clearTimeout(timer);
  }, [until]);
  return waitingNow(session);
}

export function SessionLive({ session }: { session: SessionDto }) {
  const t = useSessionCopyT();
  const [now, setNow] = useState(Date.now);
  const running = session.activity?.state === "running";
  const waiting = useWaitingNow(session);
  // Elapsed operator time ticks only while running; the shared waiting hook owns expiry.
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  const { activity } = session;
  if (activity) {
    const seconds = Math.max(0, Math.round((now - Date.parse(activity.startedAt)) / 1000));
    return <span className={`session-live ${activity.state}`} role="status"><i aria-hidden />
      {t(running ? "live.running" : "live.failed").replace("{op}", activity.op).replace("{s}", String(seconds))}</span>;
  }
  if (waiting) return <span className="session-live waiting" role="status"><i aria-hidden />{t("live.waiting")}</span>;
  return null;
}
