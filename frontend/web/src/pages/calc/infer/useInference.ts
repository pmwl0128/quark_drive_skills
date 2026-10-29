/** Everything the inference tab computes for the opponent on screen: its environment-cut hidden
 * state, the rolls every hypothesis needs (fetched through a request-keyed store, observations
 * first), the inference over its recorded hits, the narrowing history, predictions for both sides'
 * moves in the current pairing, and how the real builds and common spreads fare. */
import type { DamageRequestDto, MetaDetailDto } from "@pokemon-champions/protocol";
import { useEffect, useMemo, useState } from "react";
import { useAsync } from "../../../hooks.ts";
import { HttpError, type RuntimeAdapter } from "../../../runtime/adapter.ts";
import type { MoveRef } from "../../../runtime/projection.ts";
import { natureMult, type FieldState, type MonState } from "../duel/state.ts";
import { useBuildOptions } from "../../../components/build/inputs.tsx";
import { withMega, type BuildOption } from "../../../lib/buildState.ts";
import { useDamageStore } from "../tune/useDamageStore.ts";
import {
  bulkUse, buildSpace, frameOf, frameIssueOf, hitRequests, liveFrame, offenseUse,
  type HitFrame, type KnownFacts, type Observation, type RequestKit, type SpaceInfo,
} from "./context.ts";
import type { Candidate, Inference, InferKey, Mult, Prediction } from "./model.ts";
import type { InferenceJob } from "./evaluate.ts";

const STORE_CAP = 60000;

export interface MoveReading {
  move: string;
  /** Why it cannot be read, if it cannot. */
  reason: "status" | "unreadable" | "multiHit" | "immune" | null;
  key: InferKey | null;
  /** Our stat this hit reads: the attacking one (our move) or the defence it presses (theirs). */
  ours: InferKey | null;
  posterior: Prediction | "pending" | null;
  prior: Prediction | "pending" | null;
  isSpread: boolean;
}

export interface SetReading {
  option: BuildOption;
  candidate: Candidate;
  /** Share of rolls reproduced, per enabled observation in order; null while pending. */
  shares: number[] | null;
  fits: boolean;
}

interface SpreadReading {
  spread: Record<string, number>;
  percentage: number | null;
  /** Per space nature: still fits. */
  natures: boolean[];
}

export interface InferenceView {
  info: SpaceInfo | null;
  loading: boolean;
  inference: Inference | null;
  prior: Inference | null;
  /** Per observation id: the share of the prior's builds left after it (in order); null if skipped. */
  remaining: Map<string, number | null>;
  skipped: Set<string>;
  into: MoveReading[];
  from: MoveReading[];
  sets: SetReading[];
  spreads: SpreadReading[];
  /** Records no build at all could produce, with the nearest readings that were possible. */
  impossible: Map<string, number[]>;
  /** Whether the build currently on their card explains every observation; null with none. */
  currentFits: boolean | null;
  error: boolean;
}

const EMPTY_KNOWN: KnownFacts = {};

export function useInference({ adapter, kit, moves, foe, mine, mineTeam, observations, known, field, active }: {
  active: boolean;
  adapter: RuntimeAdapter;
  kit: RequestKit;
  moves: Map<string, MoveRef>;
  foe: MonState;
  mine: MonState;
  mineTeam: MonState[];
  observations: Observation[];
  known: KnownFacts | undefined;
  field: FieldState;
}): InferenceView {
  const { dex, items } = kit;
  const store = useDamageStore(adapter, STORE_CAP, "inference");
  const loadOptions = useBuildOptions();
  const format = field.format;
  const facts = known ?? EMPTY_KNOWN;

  // Usage data lives on the base species (a Mega folds into it).
  const { entry, mega } = withMega(foe, dex, items);
  const baseSlug = entry?.isMega && entry.baseSpecies
    ? dex.find((candidate) => candidate.name === entry.baseSpecies)?.slug ?? foe.slug : entry?.slug ?? "";
  const detail = useAsync<MetaDetailDto | null>(() => baseSlug
    ? adapter.detail(format, baseSlug).catch((error) => {
      if (error instanceof HttpError && error.status === 404) return null;
      throw error;
    })
    : Promise.resolve(null), [baseSlug, format]);
  const options = useAsync<BuildOption[]>(() => baseSlug ? loadOptions(baseSlug, format) : Promise.resolve([]),
    [baseSlug, format]);

  const detailData = detail.status === "ready" ? detail.data : null;
  const info = useMemo(() => detail.status === "ready"
    ? buildSpace(foe, detailData, facts, kit) : null,
  [detail.status, detailData, foe.slug, foe.item, facts.item, facts.ability, kit]);
  const space = info?.space ?? null;

  // Real builds of THIS form: a Mega's own sets, or the sets that stay in base form.
  const realSets = useMemo(() => {
    if (options.status !== "ready") return [];
    return options.data.filter((option) => option.source === "aggregate"
      && (mega ? option.set.runForm === entry?.name : !option.set.runForm));
  }, [options, mega, entry?.name]);
  const candidates = useMemo<Candidate[]>(() => realSets.map((option) => ({
    nature: option.modal.nature, item: option.modal.item, ability: option.modal.ability,
    sps: { ...option.modal.sps },
  })), [realSets]);

  // The build on their card right now (auto-fill or adopted), judged like any real build.
  const current = useMemo<Candidate>(() => ({
    nature: foe.nature, item: foe.item, ability: foe.ability, sps: { ...foe.sps },
  }), [foe.nature, foe.item, foe.ability, foe.sps]);

  // What the requests must cover: the space plus every real build's own nature, item and ability,
  // and the card's.
  const plan = useMemo(() => {
    if (!space) return null;
    const mults = (key: InferKey, natures: string[]) =>
      [...new Set(natures.map((name) => natureMult(kit.natures, name, key)))] as Mult[];
    const natureNames = space.natures.map((nature) => nature.name);
    const judged = [...candidates, current];
    const withSets = [...natureNames, ...judged.map((candidate) => candidate.nature).filter(Boolean)];
    const spaceMults = { atk: mults("atk", natureNames), def: mults("def", natureNames),
                         spa: mults("spa", natureNames), spd: mults("spd", natureNames) };
    const fullMults = { atk: mults("atk", withSets), def: mults("def", withSets),
                        spa: mults("spa", withSets), spd: mults("spd", withSets) };
    const legal = new Set(entry?.abilities.map((ability) => ability.name));
    return {
      space: { items: space.items.map((item) => item.name), abilities: space.abilities.map((a) => a.name),
               mults: spaceMults },
      full: {
        items: [...new Set([...space.items.map((item) => item.name), ...judged.map((c) => c.item)])],
        abilities: [...new Set([...space.abilities.map((a) => a.name),
          ...judged.map((c) => c.ability).filter((name) => legal.has(name))])],
        mults: fullMults,
      },
    };
  }, [space, candidates, current, kit.natures, entry]);

  // The recorded hits that can be read now.
  const recorded = useMemo(() => {
    const out: Array<{ observation: Observation; frame: HitFrame; key: InferKey }> = [];
    const skipped = new Set<string>();
    for (const observation of observations) {
      if (!observation.enabled) continue;
      const ours = observation.mineSnapshot ?? mineTeam.find((candidate) => candidate.uid === observation.mineId);
      const category = moves.get(observation.move)?.category;
      const use = observation.kind === "bulk" ? bulkUse(observation.move, category)
        : offenseUse(observation.move, category);
      if (!ours || !observation.mineSnapshot || !observation.foeSnapshot
        || withMega(observation.foeSnapshot, dex, items).entry?.name !== entry?.name || !("key" in use)) { skipped.add(observation.id); continue; }
      const frame = frameOf(observation, ours, foe);
      if (frameIssueOf(frame, observation.kind, plan?.space.abilities ?? [], kit)) { skipped.add(observation.id); continue; }
      out.push({ observation, frame, key: use.key });
    }
    return { out, skipped };
  }, [observations, mineTeam, foe, moves, dex, items, entry, plan, kit]);

  const liveMoves = useMemo(() => {
    const use = (move: string, kind: "bulk" | "offense") =>
      frameIssueOf(liveFrame(mine, foe, move, field), kind, plan?.space.abilities ?? [], kit)
        ? { reason: "unreadable" as const }
        : kind === "bulk" ? bulkUse(move, moves.get(move)?.category) : offenseUse(move, moves.get(move)?.category);
    const into = mine.moves.map((move) => ({ move, use: use(move, "bulk") }));
    const from = foe.moves.map((move) => ({ move, use: use(move, "offense") }));
    return { into, from };
  }, [mine, foe, field, moves, plan, kit]);

  // -- fetch ------------------------------------------------------------------------------
  const requests = useMemo(() => {
    if (!active || !plan) return { first: [] as DamageRequestDto[], then: [] as DamageRequestDto[] };
    const first = recorded.out.flatMap(({ observation, frame, key }) =>
      hitRequests(frame, { kind: observation.kind, key }, plan.full, kit));
    const then: DamageRequestDto[] = [];
    for (const { move, use } of liveMoves.into) {
      if ("key" in use) then.push(...hitRequests(liveFrame(mine, foe, move, field), { kind: "bulk", key: use.key },
        plan.space, kit));
    }
    for (const { move, use } of liveMoves.from) {
      if ("key" in use) then.push(...hitRequests(liveFrame(mine, foe, move, field), { kind: "offense", key: use.key },
        plan.space, kit));
    }
    return { first, then };
  }, [active, plan, recorded, liveMoves, mine, foe, field, kit]);

  useEffect(() => {
    // Active observations must not lose cached rolls when a long session exceeds the idle LRU cap.
    store.retain([...requests.first, ...requests.then]);
    if (!active) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void (async () => {
        await store.fetchMissing(requests.first, controller.signal);
        if (!controller.signal.aborted) await store.fetchMissing(requests.then, controller.signal);
      })();
    }, 60);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [active, requests, store.fetchMissing, store.retain]);

  const job = useMemo<InferenceJob | null>(() => active && info ? ({
    kit, info, mine, foe, field, moves, recorded: recorded.out, skipped: recorded.skipped,
    liveMoves, realSets, candidates, current, detail: detailData,
    rolls: store.snapshot([...requests.first, ...requests.then]),
  }) : null, [active, info, kit, mine, foe, field, moves, recorded, liveMoves, realSets, candidates,
    current, detailData, store.snapshot, store.version, requests]);
  const [result, setResult] = useState<{ job: InferenceJob; view: InferenceView } | null>(null);
  useEffect(() => {
    if (!job) return;
    const worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (event: MessageEvent<{ value?: InferenceView; error?: string }>) => {
      if (event.data.value) setResult({ job, view: event.data.value });
      else setResult({ job, view: { ...emptyView(info), loading: false, error: true } });
    };
    worker.onerror = () => setResult({ job, view: { ...emptyView(info), loading: false, error: true } });
    worker.postMessage(job);
    // Termination cancels obsolete CPU work as well as preventing stale results from being shown.
    return () => worker.terminate();
  }, [job, info]);
  const view = result?.job === job ? result.view : emptyView(info);
  return { ...view, error: view.error || store.error || detail.status === "error" };
}

function emptyView(info: SpaceInfo | null): InferenceView {
  return { info, loading: true, inference: null, prior: null, remaining: new Map(), skipped: new Set(),
    into: [], from: [], sets: [], spreads: [], impossible: new Map(), currentFits: null, error: false };
}
