import type { FormatId, TeamCodeResultDto } from "@pokemon-champions/protocol";
import type { RuntimeAdapter } from "../runtime/adapter.ts";

export function teamCodeFromText(text: string): string | null {
  const code = text.replace(/\s+/g, "").toUpperCase();
  return /^[A-Z0-9]{10}$/.test(code) ? code : null;
}

const inFlight = new WeakMap<RuntimeAdapter, Map<string, Promise<TeamCodeResultDto>>>();
/** Explicit submit only. Coalesce concurrent imports without doing a background refresh. */
export function readTeamCode(adapter: RuntimeAdapter, code: string, format: FormatId) {
  let requests = inFlight.get(adapter);
  if (!requests) { requests = new Map(); inFlight.set(adapter, requests); }
  const key = `${format}:${code}`;
  const existing = requests.get(key);
  if (existing) return existing;
  const pending = adapter.teamCode(code, format).finally(() => requests.delete(key));
  requests.set(key, pending);
  return pending;
}
