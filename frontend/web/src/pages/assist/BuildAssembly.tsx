/** A build as it happens (frontend/design.md §7.3): the same story the home page's flow map tells —
 * the reader's brief goes in, six gates work on it in turn, real teams, usage and damage calcs feed
 * the gates that read them, a team comes out — told here with the job's own facts.
 *
 * On the left, the six steps: each says what it reads and, once done, what it found (the defaults it
 * filled, the frameworks it took from real teams, the Pokémon whose data it read, the six it picked,
 * the rule check, the review). On the right, the bench the team is assembled on: the brief, the pool
 * of candidates the frame and the data put forward, and six slots that fill when the team is picked
 * and are checked off as it passes validation and review. Nothing is staged: a step lights when the
 * server says it runs, and every number comes from the job. */
import type { BuilderGateDto, BuilderJobDto } from "@pokemon-champions/protocol";
import {
  IconCheck, IconClipboardCheck, IconDatabase, IconShieldCheck, IconSitemap, IconSparkles, IconSwords, IconX,
} from "@tabler/icons-react";
import type { ComponentType, CSSProperties, ReactNode } from "react";
import { GameImage } from "../../components/GameImage.tsx";
import { slugify } from "../../components/team/MonChip.tsx";
import { useDexByName } from "../../hooks.ts";
import { displayName, useLang, useT, type MsgKey } from "../../i18n.ts";
import { useAssistT } from "./messages.ts";
import { Elapsed } from "./Workspace.tsx";

type Icon = ComponentType<{ "aria-hidden"?: boolean | "true" }>;
const STEPS: Array<{ id: string; Icon: Icon; reads?: { key: MsgKey; tone: string } }> = [
  { id: "context", Icon: IconClipboardCheck },
  { id: "frame", Icon: IconSitemap, reads: { key: "home.qa.src.real", tone: "a" } },
  { id: "ground", Icon: IconDatabase, reads: { key: "home.qa.src.ranking", tone: "b" } },
  { id: "generate", Icon: IconSparkles },
  { id: "evaluate", Icon: IconSwords, reads: { key: "home.qa.src.damage", tone: "c" } },
  { id: "audit", Icon: IconShieldCheck },
];
const TEAM_SIZE = 6;
const POOL_MAX = 18;

const list = (value: unknown): string[] => Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
const count = (value: unknown): number => typeof value === "number" ? value : Array.isArray(value) ? value.length : 0;

/** A Pokémon on the bench: its face and its name in the reader's language. */
export function BenchMon({ name, style }: { name: string; style?: CSSProperties }) {
  const dex = useDexByName();
  const { lang } = useLang();
  const entry = dex.get(name);
  const label = entry ? displayName(entry, lang) : name;
  return (
    <span className="asm-mon" title={label} style={style}>
      <GameImage assetKey={`pokemon:${entry?.slug ?? slugify(name)}`} role="dense" alt="" className="asm-mon-img" />
      <span className="asm-mon-name">{label}</span>
    </span>
  );
}

/** What a finished step found, from its gate detail. */
function useFact() {
  const t = useAssistT();
  return (gate: BuilderGateDto): string | null => {
    const d = gate.detail ?? {};
    switch (gate.gate) {
      case "context": {
        const parts = [t("asm.fact.context").replace("{n}", String(count(d.safeDefaults)))];
        if (count(d.conflicts)) parts.push(t("asm.fact.conflicts").replace("{n}", String(count(d.conflicts))));
        return parts.join(" · ");
      }
      case "frame": return t("asm.fact.frame").replace("{n}", String(count(d.skeletons)));
      case "ground": {
        const parts = [t("asm.fact.ground").replace("{n}", String(count(d.detailSpecies)))];
        if (count(d.megaOptions)) parts.push(t("asm.fact.megas").replace("{n}", String(count(d.megaOptions))));
        return parts.join(" · ");
      }
      case "generate": {
        const parts = [t("asm.fact.generate").replace("{n}", String(count(d.team)))];
        if (count(d.candidates) > 1) parts.push(t("asm.fact.candidates").replace("{n}", String(count(d.candidates))));
        if (count(d.fixes)) parts.push(t("asm.fact.fixes").replace("{n}", String(count(d.fixes))));
        return parts.join(" · ");
      }
      case "evaluate": {
        const status = typeof d.legality === "string" ? d.legality : "";
        const parts = [status === "valid" ? t("asm.fact.legal") : t("asm.fact.illegal").replace("{status}", status || "—")];
        if (count(d.megaCount)) parts.push(t("asm.fact.megaCount").replace("{n}", String(count(d.megaCount))));
        return parts.join(" · ");
      }
      case "audit": return t("asm.fact.audit");
      default: return null;
    }
  };
}

export function BuildAssembly({ job, startedAt, finishedAt, brief, preview = false, live }: {
  job: BuilderJobDto | null;
  startedAt: number;
  /** When the run ended (a finished record's last update): the bench then shows the time it took. */
  finishedAt?: number;
  /** The brief's choices, as the reader would name them. */
  brief: string[];
  /** A preview job runs only the deterministic three steps and assembles no team. */
  preview?: boolean;
  live: boolean;
}) {
  const t = useAssistT();
  const tm = useT();
  const fact = useFact();
  const steps = preview ? STEPS.slice(0, 3) : STEPS;
  const gates = new Map((job?.gates ?? []).map((g) => [g.gate, g]));
  const statusOf = (id: string) => gates.get(id)?.status ?? "pending";
  const doneCount = steps.filter((s) => statusOf(s.id) === "done").length;
  const runningAt = steps.findIndex((s) => statusOf(s.id) === "running" || statusOf(s.id) === "failed");
  const current = runningAt >= 0 ? runningAt + 1 : Math.min(doneCount + (live ? 1 : 0), steps.length);
  const detail = (id: string) => gates.get(id)?.detail ?? {};
  const pool = [...new Set([...list(detail("frame").coreSpecies), ...list(detail("ground").detailSpecies)])].slice(0, POOL_MAX);
  const team = list(detail("generate").team).slice(0, TEAM_SIZE);
  const legal = statusOf("evaluate") === "done" && detail("evaluate").legality === "valid";
  const reviewed = statusOf("audit") === "done";
  const queued = live && (!job || job.status === "queued");
  const progress = (doneCount + (runningAt >= 0 && statusOf(steps[runningAt]!.id) === "running" ? 0.5 : 0)) / steps.length;

  let head: ReactNode;
  if (!live && finishedAt) {
    head = <span className="aw-elapsed num">{t("asm.took").replace("{s}", String(Math.max(1, Math.round((finishedAt - startedAt) / 1000))))}</span>;
  } else {
    head = <Elapsed startedAt={startedAt} />;
  }

  return (
    <div className={`asm${live ? " live" : ""}`} style={{ "--progress": progress } as CSSProperties}>
      <div className="asm-bar" aria-hidden="true"><i /></div>
      <div className="asm-meta">
        <span className="num">
          {queued && (job?.queuePosition ?? 0) > 0
            ? t("builder.queue").replace("{n}", String(job!.queuePosition))
            : queued ? t("builder.run.queued")
              : t("asm.step").replace("{n}", String(Math.max(1, current))).replace("{total}", String(steps.length))}
        </span>
        {head}
      </div>
      <div className="asm-body">
        <ol className="asm-steps">
          {steps.map((step, i) => {
            const status = statusOf(step.id);
            const gate = gates.get(step.id);
            const label = t(`gate.${step.id}` as Parameters<typeof t>[0]);
            const line = status === "done" && gate ? fact(gate)
              : status === "failed" ? t("asm.stopped")
                : tm(`home.flow.${step.id}.d` as MsgKey);
            return (
              <li key={step.id} className={`asm-step ${status}`} style={{ "--n": i } as CSSProperties}>
                <span className="asm-node" aria-hidden="true">
                  {status === "done" ? <IconCheck /> : status === "failed" ? <IconX /> : <step.Icon />}
                </span>
                <div className="asm-step-text">
                  <div className="asm-step-name">
                    <b className="num">{String(i + 1).padStart(2, "0")}</b>
                    <span>{label}</span>
                    {step.reads && (
                      <span className={`asm-reads tone-${step.reads.tone}`}>
                        <i aria-hidden="true" />{tm(step.reads.key)}
                      </span>
                    )}
                  </div>
                  <p className="asm-step-line">{line}</p>
                  {!live && gate?.raw != null && (
                    <details className="gate-raw asm-raw">
                      <summary>{t("builder.rawData")}</summary>
                      <pre>{JSON.stringify(gate.raw, null, 1)}</pre>
                    </details>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
        <div className="asm-bench">
          <div className="asm-block">
            <span className="asm-label">{tm("home.flow.in")}</span>
            <span className="asm-brief">{brief.map((b, i) => <span key={i}>{b}</span>)}</span>
          </div>
          <div className="asm-block">
            <span className="asm-label">{t("asm.pool")}{pool.length > 0 && <span className="num"> · {pool.length}</span>}</span>
            {pool.length > 0 ? (
              <span className="asm-pool">
                {pool.map((name, i) => (
                  <BenchMon key={name} name={name} style={{ "--n": i } as CSSProperties} />
                ))}
              </span>
            ) : (
              <span className={`asm-pool-wait${statusOf("frame") === "running" || statusOf("ground") === "running" ? " on" : ""}`}
                aria-hidden="true">{Array.from({ length: 8 }, (_, i) => <i key={i} style={{ "--n": i } as CSSProperties} />)}</span>
            )}
          </div>
          {!preview && (
            <div className={`asm-block asm-team${legal ? " legal" : ""}${reviewed ? " reviewed" : ""}`}>
              <span className="asm-label">
                {tm("home.flow.out")}
                {legal && <span className="asm-seal"><IconCheck aria-hidden="true" />{t("builder.badge.valid")}</span>}
                {reviewed && <span className="asm-seal"><IconShieldCheck aria-hidden="true" />{t("asm.checked")}</span>}
              </span>
              <ol className="asm-slots">
                {Array.from({ length: TEAM_SIZE }, (_, i) => {
                  const name = team[i];
                  return (
                    <li key={i} className={name ? "filled" : statusOf("generate") === "running" ? "wait" : ""}
                      style={{ "--n": i } as CSSProperties}>
                      {name ? <BenchMon name={name} /> : <span className="asm-slot-num num">{i + 1}</span>}
                    </li>
                  );
                })}
              </ol>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
