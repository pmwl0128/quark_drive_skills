/** Shared bits of the UEP panel chunk: kind badges + payload pretty-printing. Artifact
 * payloads are agent-authored skill JSON the panel must NOT re-validate (design §4.2 keeps
 * them verbatim) — rendering is best-effort, the raw text is always the fallback. */
import { UEP_GATE_KINDS } from "@pokemon-champions/protocol";
import { useKindLabel } from "../../components/session/SessionActivity.tsx";

const isGateKind = (kind: string) => (UEP_GATE_KINDS as readonly string[]).includes(kind);

export function KindBadge({ kind }: { kind: string }) {
  const label = useKindLabel();
  return <span className={`uep-kind uep-kind-${isGateKind(kind) ? kind : "other"}`}>{label(kind)}</span>;
}

/** Pretty-print a payload for DISPLAY only (the stored artifact stays verbatim). */
export function prettyPayload(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export function shortHash(hash: string): string {
  return hash.slice(0, 10);
}

export function localTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}
