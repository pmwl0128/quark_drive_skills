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

export function SessionLive({ session }: { session: SessionDto }) {
  const t = useSessionCopyT();
  const [now, setNow] = useState(Date.now);
  const running = session.activity?.state === "running";
  const waiting = waitingNow(session, now);
  // Tick only while something is live: the elapsed time, and a wait that lapses with no event.
  useEffect(() => {
    if (!running && !waiting) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running, waiting]);
  const { activity } = session;
  if (activity) {
    const seconds = Math.max(0, Math.round((now - Date.parse(activity.startedAt)) / 1000));
    return <span className={`session-live ${activity.state}`} role="status"><i aria-hidden />
      {t(running ? "live.running" : "live.failed").replace("{op}", activity.op).replace("{s}", String(seconds))}</span>;
  }
  if (waiting) return <span className="session-live waiting" role="status"><i aria-hidden />{t("live.waiting")}</span>;
  return null;
}
