/** A diagnosis session's result: the same report the online diagnose page renders, built by the
 * bridge from the session's own `diagnose` artifact and the team it read. Loaded only when a
 * session has one, together with the report's stylesheet. */
import type { SessionApi } from "../../runtime/adapter.ts";
import { useAsync } from "../../hooks.ts";
import { Report } from "../assist/diagnose/Report.tsx";
import { useUepT } from "./messages.ts";
import "../../styles.assist.css";

export default function SessionDiagnosis({ sessions, sessionId, artifactHash }: {
  sessions: SessionApi; sessionId: string; artifactHash: string;
}) {
  const t = useUepT();
  const report = useAsync(() => sessions.diagnosis(sessionId), [sessionId, artifactHash]);
  return <section className="panel uep-diagnosis" aria-labelledby="uep-diagnosis-heading">
    <h2 id="uep-diagnosis-heading">{t("uep.diagnosis.title")}</h2>
    {report.status === "loading" ? <div className="spinner">{t("state.loading")}</div>
      : report.status === "error" ? <div className="notice">{t("uep.diagnosis.error")}</div>
      : <Report report={report.data} />}
  </section>;
}
