/** What to send the assistant next. The panel never wakes an agent (design §1), so each state that
 * needs one names the exact instruction: start, continue after the reader's decision or answers,
 * fix a failed review, or pick up after the assistant ended its turn. Nothing while the reader must
 * act, once review passed, or while the bridge sees the assistant running or waiting; and when a
 * waiting assistant has already collected the reader's answer, it says so instead. */
import type { SessionDto } from "@pokemon-champions/protocol";
import { IconArrowForwardUp, IconChecks, IconRobot, IconTool } from "@tabler/icons-react";
import { CopyLine } from "./CopyLine.tsx";
import { companionReady, useConsoleStatus } from "./live.ts";
import { useSessionCopyT } from "./messages.ts";
import { waitingNow } from "./SessionLive.tsx";
import { sessionState } from "./SessionState.tsx";
import "./session-handoff.css";

type Handoff = "start" | "decided" | "answered" | "collected" | "fix" | "resume";
const PANEL_KINDS = new Set(["decision", "intake-answer"]);

export function handoffFor(session: SessionDto): Handoff | null {
  const state = sessionState(session);
  if (state === "done" || state === "awaiting" || state === "questions") return null;
  if (state === "idle") return "start";
  if (state === "needs_changes") return "fix";
  if (session.lastKind && PANEL_KINDS.has(session.lastKind)) {
    // The newest ledger entry has sequence revision - 1: was that the answer a wait handed over?
    if (session.pickedUp !== undefined && session.pickedUp >= session.revision - 1) return "collected";
    return session.lastKind === "intake-answer" ? "answered" : "decided";
  }
  if (session.activity?.state === "running" || waitingNow(session)) return null;
  return "resume";
}

export function SessionHandoff({ session }: { session: SessionDto }) {
  const t = useSessionCopyT();
  const status = useConsoleStatus();
  const kind = handoffFor(session);
  if (!kind) return null;
  const Icon = kind === "start" ? IconRobot : kind === "fix" ? IconTool : kind === "collected" ? IconChecks : IconArrowForwardUp;
  const needsSetup = kind === "start" && companionReady(status) === false;
  return <section className={`session-handoff handoff-${kind}`} aria-labelledby="session-handoff-title">
    <span className="session-handoff-icon" aria-hidden><Icon size={20} /></span>
    <div className="session-handoff-body">
      <h2 id="session-handoff-title">{t(`handoff.${kind}.title`)}</h2>
      <p>{t(`handoff.${kind}.body`)}</p>
      {kind !== "collected"
        ? <CopyLine text={t(`handoff.${kind}.text`).replace("{id}", session.id)} label={t("handoff.copy")} /> : null}
      {needsSetup && status ? <div className="session-handoff-setup"><p>{t("handoff.setup")}</p>
        <CopyLine code text={`${status.pcuiCommand} install-agent`} /></div> : null}
    </div>
  </section>;
}
