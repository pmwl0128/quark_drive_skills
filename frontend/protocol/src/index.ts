/**
 * @pokemon-champions/protocol — the Web protocol layer (frontend/design.md §3).
 *
 * Capabilities handshake + Web DTO Zod schemas shared by the online site and the local
 * bridge UI. Components consume these DTOs only; raw skill CLI JSON never crosses the
 * adapter boundary unvalidated.
 */
export * from "./common.ts";
export * from "./capabilities.ts";
export * from "./dex.ts";
export * from "./meta.ts";
export * from "./calc.ts";
export * from "./matchup.ts";
export * from "./tune.ts";
export * from "./session.ts";
export * from "./llm.ts";
export * from "./diagnose.ts";
export * from "./assets.ts";
export * from "./team.ts";
export * from "./team-code.ts";
export * from "./storage.ts";
