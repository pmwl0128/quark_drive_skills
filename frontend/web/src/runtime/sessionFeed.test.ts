import { afterEach, expect, it, vi } from "vitest";
import type { SessionChange } from "./adapter.ts";
import { SessionFeed } from "./sessionFeed.ts";

class FakeSource {
  static all: FakeSource[] = [];
  readonly listeners = new Map<string, Array<(event: { data: string }) => void>>();
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(readonly url: string) { FakeSource.all.push(this); }
  addEventListener(name: string, listener: (event: { data: string }) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), listener]);
  }
  close() { this.closed = true; }
  fire(name: string, data = "{}") {
    if (name === "message") this.onmessage?.({ data });
    else for (const listener of this.listeners.get(name) ?? []) listener({ data });
  }
}

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); FakeSource.all = []; });

function feedWith(probe: () => number): SessionFeed {
  vi.useFakeTimers();
  vi.stubGlobal("window", globalThis);
  vi.stubGlobal("document", Object.assign(new EventTarget(), { visibilityState: "hidden" }));
  vi.stubGlobal("EventSource", FakeSource);
  return new SessionFeed("/api/events", async () => probe());
}

const append = { sessionId: "s1", kind: "frame", artifactHash: "h", revision: 1, sequence: 0 };

it("recovers from a bridge restart and tells readers to look again", async () => {
  let status = 200;
  const feed = feedWith(() => status);
  const changes: SessionChange[] = [];
  const stop = feed.subscribe((change) => changes.push(change));
  const first = FakeSource.all[0]!;
  first.fire("hello");
  first.fire("message", JSON.stringify(append));
  expect(feed.liveStatus()).toBe("live");
  expect(changes).toEqual([{ type: "artifact", event: append }]);

  // The restarted bridge refuses the old cookie; the browser closes the stream for good.
  status = 401;
  first.onerror?.();
  await vi.advanceTimersByTimeAsync(0);
  expect(first.closed).toBe(true);
  expect(feed.liveStatus()).toBe("signedOut");

  // After `pcui open` signs the browser in again, the retry opens and readers re-read.
  status = 200;
  await vi.advanceTimersByTimeAsync(1_000);
  const second = FakeSource.all[1]!;
  second.fire("hello");
  expect(feed.liveStatus()).toBe("live");
  expect(changes.at(-1)).toEqual({ type: "resync" });

  stop();
  expect(second.closed).toBe(true);
});

it("treats a stream that stops pinging as dead", async () => {
  const feed = feedWith(() => 0);
  feed.subscribe(() => {});
  FakeSource.all[0]!.fire("hello");
  await vi.advanceTimersByTimeAsync(30_000);
  FakeSource.all[0]!.fire("ping");
  await vi.advanceTimersByTimeAsync(30_000);
  expect(FakeSource.all[0]!.closed).toBe(false);
  await vi.advanceTimersByTimeAsync(15_000);
  expect(FakeSource.all[0]!.closed).toBe(true);
  expect(feed.liveStatus()).toBe("offline");
});

it("keeps one stream when visibility changes during a recovery probe", async () => {
  let finish: ((status: number) => void) | undefined;
  let calls = 0;
  feedWith(() => 200); // install the fake browser and clock
  const feed = new SessionFeed("/events", () => ++calls === 1 ? Promise.resolve(200)
    : new Promise(resolve => { finish = resolve; }));
  const stop = feed.subscribe(() => {});
  FakeSource.all[0]!.fire("hello");
  FakeSource.all[0]!.onerror?.();
  await vi.advanceTimersByTimeAsync(1000);
  FakeSource.all.at(-1)!.onerror?.();
  Object.defineProperty(document, "visibilityState", {value:"visible", configurable:true});
  document.dispatchEvent(new Event("visibilitychange"));
  finish?.(200);
  await vi.advanceTimersByTimeAsync(2000);
  expect(FakeSource.all.filter(source => !source.closed)).toHaveLength(1);
  stop();
  expect(FakeSource.all.every(source => source.closed)).toBe(true);
});

it("times out a hung health probe and retries instead of staying live", async () => {
  feedWith(() => 0);
  const feed = new SessionFeed("/events", () => new Promise(() => {}));
  const stop = feed.subscribe(() => {});
  FakeSource.all[0]!.fire("hello");
  await vi.advanceTimersByTimeAsync(47000);
  expect(feed.liveStatus()).not.toBe("live");
  expect(FakeSource.all.length).toBeGreaterThan(1);
  stop();
});
