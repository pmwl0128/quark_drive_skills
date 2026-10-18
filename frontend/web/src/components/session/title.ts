import type { SessionDto } from "@pokemon-champions/protocol";

/** A session as the reader names it: their title, else the request they typed, else its id. */
export function sessionTitle(session: SessionDto): string {
  if (session.title) return session.title;
  return typeof session.meta.intent === "string" && session.meta.intent ? session.meta.intent : session.id;
}

/** The library team a session was started from (`meta.teamRef`), for related-session links. */
export function sessionTeamId(session: SessionDto): string | null {
  const ref = session.meta.teamRef;
  return ref && typeof ref === "object" && typeof (ref as { id?: unknown }).id === "string"
    ? (ref as { id: string }).id : null;
}
