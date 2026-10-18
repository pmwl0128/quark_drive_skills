/** "Last update: Frame · 3 minutes ago": who moved last and when, from the ledger. Evidence only;
 * it never claims the assistant is still working. */
import type { SessionDto } from "@pokemon-champions/protocol";
import { useLang } from "../../i18n.ts";
import { relativeTime } from "../../lib/runRecords.ts";
import { useSessionCopyT } from "./messages.ts";
import "./console.css";

export function useKindLabel(): (kind: string) => string {
  const t = useSessionCopyT();
  return (kind) => {
    const key = useSessionCopyT.optionalKey(`kind.${kind}`);
    return key ? t(key) : kind;
  };
}

export function SessionActivity({ session }: { session: SessionDto }) {
  const t = useSessionCopyT();
  const { lang } = useLang();
  const label = useKindLabel();
  const iso = session.updatedAt ?? session.createdAt;
  const at = Date.parse(iso);
  const time = Number.isNaN(at) ? iso : relativeTime(at, lang, t("activity.justNow"));
  const template = t(session.lastKind ? "activity.last" : "activity.created");
  return <span className="session-activity">{template.split(/(\{(?:kind|time)\})/g).filter(Boolean).map((part, index) =>
    part === "{kind}" ? <b key={index}>{label(session.lastKind ?? "")}</b>
      : part === "{time}" ? <time key={index} dateTime={iso}>{time}</time> : part)}</span>;
}
