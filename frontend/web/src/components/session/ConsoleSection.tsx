/** A Console section's heading in the online home's language (pages/home/SectionHead.tsx), at panel
 * scale: a small mark drawn from the section's own content, the title, a hairline to the edge, and
 * an optional link. The marks are decoration. */
import type { CSSProperties, ReactNode } from "react";
import { typeColor } from "../../assets/icons.ts";

export type ConsoleMarkKind = "core" | "lab" | "glance" | "team" | "flow";

const LAB = ["ko-ohko", "ko-2hko", "ko-3hko", "ko-slow"];
const GLANCE = ["Dragon", "Water", "Steel"];

export function ConsoleMark({ kind }: { kind: ConsoleMarkKind }) {
  return <span className={`console-mark ${kind}`} aria-hidden="true">
    {kind === "lab" && LAB.map((cls) => <i key={cls} className={cls} />)}
    {kind === "glance" && GLANCE.map((type) => <i key={type} style={{ "--tc": typeColor(type) } as CSSProperties} />)}
    {kind === "team" && [0, 1, 2].map((i) => <i key={i} />)}
    {kind === "flow" && [0, 1, 2].map((i) => <i key={i} />)}
    {kind === "core" && <i />}
  </span>;
}

export function ConsoleSectionHead({ id, title, mark, children }: {
  id: string; title: string; mark: ConsoleMarkKind; children?: ReactNode;
}) {
  return <header className="console-section-head">
    <ConsoleMark kind={mark} />
    <h2 id={id}>{title}</h2>
    <span className="console-section-rule" aria-hidden="true" />
    {children}
  </header>;
}
