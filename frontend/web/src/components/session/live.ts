/** React side of the local session feed (runtime/sessionFeed.ts). Every hook is inert where the
 * runtime has no session surface, so callers never branch on runtime. */
import type { ConsoleStatusDto } from "@pokemon-champions/protocol";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { LiveStatus, SessionChange } from "../../runtime/adapter.ts";
import { useRuntime } from "../../runtime/context.tsx";

const noSubscription = () => () => {};
const noStatus = () => null;

export function useLiveStatus(): LiveStatus | null {
  const sessions = useRuntime().adapter.sessions;
  return useSyncExternalStore(sessions ? sessions.onLiveStatus : noSubscription,
    sessions ? sessions.liveStatus : noStatus);
}

/** Runs the latest `onChange` for every feed change while mounted. */
export function useSessionChanges(onChange: (change: SessionChange) => void): void {
  const sessions = useRuntime().adapter.sessions;
  const latest = useRef(onChange);
  latest.current = onChange;
  useEffect(() => sessions?.subscribe((change) => latest.current(change)), [sessions]);
}

/** A counter that moves whenever a reader should look again; `filter` narrows which changes count. */
export function useSessionVersion(filter?: (change: SessionChange) => boolean): number {
  const [version, setVersion] = useState(0);
  useSessionChanges((change) => { if (!filter || filter(change)) setVersion((value) => value + 1); });
  return version;
}

// The last answer is kept for the tab: once the bridge forgets this tab, the signed-out notice can
// no longer ask it which pcui command to show.
let lastConsole: ConsoleStatusDto | null = null;
let pending: Promise<ConsoleStatusDto> | null = null;

/** Assistant link and projection status; read again on reconnect and when the tab comes back to
 * the front (the reader may have just run `install-agent` in a terminal). */
export function useConsoleStatus(): ConsoleStatusDto | null {
  const sessions = useRuntime().adapter.sessions;
  const [value, setValue] = useState(lastConsole);
  const load = useCallback(() => {
    if (!sessions) return;
    pending ??= sessions.consoleStatus().finally(() => { pending = null; });
    pending.then((status) => { lastConsole = status; setValue(status); }, () => {});
  }, [sessions]);
  useEffect(() => {
    load();
    const onVisible = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [load]);
  useSessionChanges((change) => { if (change.type === "resync") load(); });
  return value;
}

export function companionReady(status: ConsoleStatusDto | null): boolean | null {
  return status ? status.companion.some((row) => row.status === "installed") : null;
}
