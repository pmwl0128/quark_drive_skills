/** The local bridge's session event stream, shared by every reader in a tab (design §6).
 *
 * One EventSource per tab, open while anyone listens. The bridge greets each stream with `hello`
 * and pings every 15 s, so a stream that goes quiet for longer is treated as dead even when the
 * browser has not noticed. Every failure is classified with one probe: a 401 means the bridge
 * restarted and forgot this tab's cookie (`signedOut`), no answer means it is down (`offline`).
 * Both keep retrying, and a stream that opens again after having been open emits `resync`, since
 * events may have been missed in between: readers re-read rather than trust their copy.
 *
 * An EventSource that receives a 401 closes for good and fires one bare `error`, which is how an
 * open page used to fall silent after a bridge restart. */
import {
  ArtifactAppendEventDtoSchema, SessionEventDtoSchema,
} from "@pokemon-champions/protocol";
import type { LiveStatus, SessionChange } from "./adapter.ts";

const QUIET_MS = 40_000;
const PROBE_MS = 5_000;
const RETRY_MS = [1_000, 2_000, 5_000, 10_000, 15_000];

function parse(data: string): unknown {
  try { return JSON.parse(data); } catch { return null; }
}

export class SessionFeed {
  private source: EventSource | null = null;
  private readonly listeners = new Set<(change: SessionChange) => void>();
  private readonly statusListeners = new Set<() => void>();
  private status: LiveStatus = "connecting";
  private attempt = 0;
  private wasOpen = false;
  private retryTimer = 0;
  private quietTimer = 0;
  private generation = 0;
  private probeController: AbortController | null = null;

  constructor(private readonly url: string, private readonly probe: (signal: AbortSignal) => Promise<number>) {}

  subscribe = (onChange: (change: SessionChange) => void): (() => void) => {
    this.listeners.add(onChange);
    if (!this.source && !this.retryTimer) this.connect();
    return () => {
      this.listeners.delete(onChange);
      if (!this.listeners.size) this.stop();
    };
  };

  liveStatus = (): LiveStatus => this.status;

  onLiveStatus = (listener: () => void): (() => void) => {
    this.statusListeners.add(listener);
    return () => { this.statusListeners.delete(listener); };
  };

  private setStatus(next: LiveStatus) {
    if (next === this.status) return;
    this.status = next;
    for (const listener of [...this.statusListeners]) listener();
  }

  private emit(change: SessionChange) {
    for (const listener of [...this.listeners]) listener(change);
  }

  private connect() {
    if (this.source || !this.listeners.size) return;
    this.generation += 1;
    this.probeController?.abort();
    this.probeController = null;
    window.clearTimeout(this.retryTimer);
    this.retryTimer = 0;
    document.removeEventListener("visibilitychange", this.onVisible);
    const source = new EventSource(this.url);
    this.source = source;
    source.addEventListener("hello", () => {
      if (this.source !== source) return;
      this.attempt = 0;
      this.setStatus("live");
      this.armQuiet();
      if (this.wasOpen) this.emit({ type: "resync" });
      this.wasOpen = true;
    });
    source.addEventListener("ping", () => { if (this.source === source) this.armQuiet(); });
    for (const type of ["session", "activity"] as const) {
      source.addEventListener(type, (event) => {
        if (this.source !== source) return;
        this.armQuiet();
        const parsed = SessionEventDtoSchema.safeParse(parse((event as MessageEvent).data));
        if (!parsed.success) return;
        this.emit(type === "session"
          ? { type, sessionId: parsed.data.sessionId, ...(parsed.data.change ? { change: parsed.data.change } : {}) }
          : { type, sessionId: parsed.data.sessionId });
      });
    }
    // Unnamed messages are artifact appends.
    source.onmessage = (event) => {
      if (this.source !== source) return;
      this.armQuiet();
      const parsed = ArtifactAppendEventDtoSchema.safeParse(parse(event.data));
      if (parsed.success) this.emit({ type: "artifact", event: parsed.data });
    };
    source.onerror = () => { if (this.source === source) void this.recover(); };
  }

  private armQuiet() {
    window.clearTimeout(this.quietTimer);
    this.quietTimer = window.setTimeout(() => void this.recover(), QUIET_MS);
  }

  private close() {
    window.clearTimeout(this.quietTimer);
    this.source?.close();
    this.source = null;
  }

  private stop() {
    this.generation += 1;
    this.probeController?.abort();
    this.probeController = null;
    this.close();
    window.clearTimeout(this.retryTimer);
    this.retryTimer = 0;
    document.removeEventListener("visibilitychange", this.onVisible);
  }

  private async recover() {
    const generation = ++this.generation;
    this.close();
    window.clearTimeout(this.retryTimer);
    this.retryTimer = 0;
    this.setStatus("connecting");
    document.addEventListener("visibilitychange", this.onVisible);
    const controller = new AbortController();
    this.probeController?.abort();
    this.probeController = controller;
    let timer = 0;
    const timeout = new Promise<number>(resolve => {
      timer = window.setTimeout(() => { controller.abort(); resolve(0); }, PROBE_MS);
    });
    let status: number;
    try { status = await Promise.race([this.probe(controller.signal), timeout]); } catch { status = 0; }
    finally { window.clearTimeout(timer); }
    if (!this.listeners.size || generation !== this.generation || this.source) return;
    this.probeController = null;
    this.setStatus(status === 401 ? "signedOut" : status ? "connecting" : "offline");
    const delay = RETRY_MS[Math.min(this.attempt, RETRY_MS.length - 1)]!;
    this.attempt += 1;
    window.clearTimeout(this.retryTimer);
    this.retryTimer = window.setTimeout(() => this.connect(), delay);
    // A tab brought back to the front tries at once instead of waiting out the back-off.
  }

  private onVisible = () => {
    if (document.visibilityState !== "visible" || this.source || !this.listeners.size) return;
    document.removeEventListener("visibilitychange", this.onVisible);
    window.clearTimeout(this.retryTimer);
    this.connect();
  };
}
