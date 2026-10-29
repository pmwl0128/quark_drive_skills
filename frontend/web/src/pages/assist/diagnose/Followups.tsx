import type { DiagnoseReportDto, DiagnosisChangeCardDto } from "@pokemon-champions/protocol";
import { DIAGNOSE_FOLLOWUP_MAX_CHARS } from "@pokemon-champions/protocol";
import { useEffect, useState } from "react";
import { ThinkingToggle } from "../../../components/ThinkingToggle.tsx";
import { useProseRenderer } from "../../../lib/prose.tsx";
import { HttpError } from "../../../runtime/adapter.ts";
import { useAssistT } from "../messages.ts";
import { ChangeCards } from "./ChangeCards.tsx";

function readDraft(recordId: string) {
  try {
    const value = JSON.parse(sessionStorage.getItem(`pc-diagnose-followup-${recordId}`) ?? "{}");
    return { question: typeof value.question === "string" ? value.question.slice(0, DIAGNOSE_FOLLOWUP_MAX_CHARS) : "",
      thinking: value.thinking === true, testChange: value.testChange === true };
  } catch { return { question: "", thinking: false, testChange: false }; }
}

export function clearFollowupDraft(recordId: string) {
  try { sessionStorage.setItem(`pc-diagnose-followup-${recordId}`,
    JSON.stringify({ ...readDraft(recordId), question: "", testChange: false })); }
  catch { /* storage may be unavailable */ }
}

export function Followups({ recordId, report, busy, canAsk, quota, thinkingUnavailable, onAsk, onAdopt }: {
  recordId: string;
  report: DiagnoseReportDto; busy: boolean; canAsk: boolean;
  quota: { used: number; limit: number } | null; thinkingUnavailable: boolean;
  onAsk: (question: string, thinking: boolean, testChange: boolean) => Promise<void>;
  onAdopt?: (card: DiagnosisChangeCardDto) => void;
}) {
  const t = useAssistT();
  const prose = useProseRenderer();
  const [draft] = useState(() => readDraft(recordId));
  const [question, setQuestion] = useState(draft.question);
  const [thinking, setThinking] = useState(draft.thinking && !thinkingUnavailable);
  const [testChange, setTestChange] = useState(draft.testChange);
  const [error, setError] = useState<"unavailable" | "conflict" | "busy" | "limit" | "generic" | null>(null);
  const [now, setNow] = useState(Date.now);
  const context = report.continuation;
  const expires = context ? Date.parse(context.expiresAt) : 0;
  useEffect(() => {
    try { sessionStorage.setItem(`pc-diagnose-followup-${recordId}`, JSON.stringify({ question, thinking, testChange })); }
    catch { /* drafts remain in memory if session storage is unavailable */ }
  }, [recordId, question, thinking, testChange]);
  useEffect(() => {
    const saved = readDraft(recordId);
    setQuestion(saved.question);
    setTestChange(saved.testChange);
  }, [recordId, context?.revision]);
  useEffect(() => {
    if (!context || expires <= Date.now()) return;
    const timer = window.setTimeout(() => setNow(Date.now()), Math.min(expires-Date.now()+20, 2_147_483_647));
    return () => window.clearTimeout(timer);
  }, [expires, context?.id]);
  const unavailable = !context || expires <= now || error === "unavailable";
  const exhausted = !!context && context.remainingTurns === 0;
  const limited = !!quota && quota.limit > 0 && quota.limit-quota.used < (thinking ? 2 : 1);
  const ask = async () => {
    if (!question.trim() || busy || unavailable || exhausted || limited) return;
    setError(null);
    try {
      await onAsk(question.trim(), thinking, testChange);
      setQuestion("");
      setTestChange(false);
    } catch (reason) {
      const code = reason instanceof HttpError ? reason.code : undefined;
      setError(code === "context_busy" ? "busy" : code === "context_conflict" ? "conflict"
        : ["context_unavailable", "snapshot_changed", "followup_limit"].includes(code ?? "") ? "unavailable"
        : reason instanceof HttpError && reason.status === 429 ? "limit" : "generic");
    }
  };
  const format = report.analysis?.format ?? "single";
  return <section className="diag-followups" aria-label={t("diag.followup.title")}>
    {(report.followups ?? []).map((turn, i) => <article className="panel diag-followup-turn" key={i}>
      <h2>{t("diag.followup.turn").replace("{n}", String(i+1))}</h2>
      <p className="diag-followup-question">{turn.question}</p>
      {turn.explanation && <div className="diag-followup-answer">{turn.explanation.split(/\n\n+/).map((text, n) => <p key={n}>{prose(text)}</p>)}</div>}
      {turn.explanationError && <p className="notice">{t("diag.followup.answerFailed")}</p>}
      {turn.assistance && <div className="diag-grid"><ChangeCards assistance={turn.assistance} format={format} onAdopt={onAdopt} /></div>}
    </article>)}
    {canAsk && <div className="panel form-panel diag-followup-form">
      <h2>{t("diag.followup.title")}</h2>
      <p className="muted">{t("diag.followup.note")}</p>
      {unavailable || exhausted ? <>
        <p className="notice">{t(exhausted ? "diag.followup.exhausted" : "diag.followup.unavailable")}</p>
        {question.trim() && <p className="diag-followup-question">{question}</p>}
      </> : <>
        <div className="builder-field">
          <label htmlFor="diag-followup-question">{t("diag.followup.question")}</label>
          <textarea id="diag-followup-question" value={question} maxLength={DIAGNOSE_FOLLOWUP_MAX_CHARS}
            rows={3} disabled={busy} placeholder={t("diag.followup.placeholder")}
            onChange={(event) => setQuestion(event.target.value)} />
        </div>
        <div className="diag-followup-actions">
          {report.analysis?.allowedChanges.length !== 0 && <label className="diag-explain-opt">
            <input type="checkbox" checked={testChange} disabled={busy} onChange={(event) => setTestChange(event.target.checked)} />
            {t("diag.followup.testChange")}
          </label>}
          <ThinkingToggle checked={thinking} onChange={setThinking} disabled={busy || thinkingUnavailable}
            label={t("thinking.label")} tip={t("diag.thinkingTip")} />
          <span className="muted">{t("diag.followup.remaining").replace("{n}", String(context?.remainingTurns ?? 0))}</span>
          <button type="button" className="primary-btn" disabled={busy || limited || !question.trim()} onClick={() => void ask()}>
            {t(busy ? "diag.followup.running" : "diag.followup.ask")}
          </button>
        </div>
        {busy && <p role="status">{t("diag.followup.runningNote")}</p>}
        {limited && <p className="notice">{t("diag.followup.limit")}</p>}
      </>}
      {error && error !== "unavailable" && <p role="alert" className="notice">{t(`diag.followup.${error}`)}</p>}
    </div>}
  </section>;
}
