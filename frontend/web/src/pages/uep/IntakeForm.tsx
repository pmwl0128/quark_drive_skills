/** The assistant's open intake batch, answered in the panel (design §6). Each question shows in the
 * reader's language with its numbered options; `choice_or_text` and `text` questions also take a
 * typed answer. The answers go back as one panel-authored `intake-answer` artifact, which the
 * assistant applies exactly like an answer given in conversation. The agent-facing option notes
 * stay out of the form. */
import { IntakeAnswerDtoSchema, type IntakeAnswerDto, type SessionWithLedgerDto } from "@pokemon-champions/protocol";
import { useMemo, useState } from "react";
import { useLang, type Lang } from "../../i18n.ts";
import { HttpError, type SessionApi } from "../../runtime/adapter.ts";
import { useUepT } from "./messages.ts";

interface Option { id: string; label: Partial<Record<Lang, string>> }
interface Question {
  id: string;
  text: Partial<Record<Lang, string>>;
  options: Option[];
  mode: "choice" | "choice_or_text" | "text";
  skippable: boolean;
}

const asObj = (v: unknown): Record<string, unknown> | null =>
  v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const texts = (v: unknown): Partial<Record<Lang, string>> => {
  const o = asObj(v);
  return o ? Object.fromEntries(Object.entries(o).filter(([, s]) => typeof s === "string")) : {};
};

/** The batch's questions, read leniently: an artifact this panel cannot read renders nothing. */
export function intakeQuestions(intake: Record<string, unknown> | null): Question[] {
  if (!intake || intake.done !== false || !Array.isArray(intake.batch)) return [];
  return intake.batch.flatMap((raw) => {
    const q = asObj(raw);
    if (!q || typeof q.id !== "string") return [];
    const options = (Array.isArray(q.options) ? q.options : []).flatMap((o) => {
      const opt = asObj(o);
      return opt && typeof opt.id === "string" ? [{ id: opt.id, label: texts(opt.label) }] : [];
    });
    const mode = q.answer_mode === "text" || q.answer_mode === "choice_or_text" ? q.answer_mode : "choice";
    return [{ id: q.id, text: texts(q.text), options, mode, skippable: q.skippable !== false }];
  });
}

export function IntakeForm({ sessions, session, intake, onCommitted }: {
  sessions: SessionApi; session: SessionWithLedgerDto; intake: Record<string, unknown> | null; onCommitted: () => void;
}) {
  const t = useUepT();
  const { lang } = useLang();
  const questions = useMemo(() => intakeQuestions(intake), [intake]);
  const [answers, setAnswers] = useState<Record<string, { option?: string; text?: string }>>({});
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  if (!questions.length) return null;
  const pick = (text: Partial<Record<Lang, string>>) => text[lang] ?? text.en ?? Object.values(text)[0] ?? "";
  const answered = (q: Question) => !!answers[q.id]?.option || !!answers[q.id]?.text?.trim();
  const missing = questions.filter((q) => !q.skippable && !answered(q));
  const remaining = typeof intake?.remaining_after === "number" ? intake.remaining_after : 0;

  const submit = async () => {
    setSubmitting(true);
    setError("");
    try {
      const dto: IntakeAnswerDto = IntakeAnswerDtoSchema.parse({
        gate: "intake",
        ...(typeof intake?.group === "string" ? { group: intake.group } : {}),
        answers: questions.map((q) => {
          const a = answers[q.id] ?? {};
          const text = a.text?.trim();
          return a.option || text ? { id: q.id, ...(a.option ? { option: a.option } : {}), ...(text ? { text } : {}) }
            : { id: q.id, skipped: true };
        }),
        ...(note.trim() ? { note: note.trim() } : {}),
        answeredAt: new Date().toISOString(),
      });
      await sessions.putArtifact(session.id, "intake-answer", JSON.stringify(dto), session.revision);
      onCommitted();
    } catch (e) {
      console.error("Intake answer submission failed:", e);
      const conflict = e instanceof HttpError && e.status === 409;
      setError(conflict ? t("uep.conflict") : t("uep.submitError"));
      if (conflict) onCommitted();
    } finally {
      setSubmitting(false);
    }
  };

  return <section className="panel uep-intake" aria-labelledby="uep-intake-heading">
    <h2 id="uep-intake-heading">{t("uep.intake.title")}</h2>
    <p className="muted">{t("uep.intake.hint")}{remaining > 0 ? ` ${t("uep.intake.remaining").replace("{n}", String(remaining))}` : ""}</p>
    {questions.map((q, index) => {
      const current = answers[q.id] ?? {};
      const set = (patch: { option?: string; text?: string }) => setAnswers((all) => ({ ...all, [q.id]: { ...all[q.id], ...patch } }));
      return <fieldset key={q.id} className="uep-intake-question">
        <legend><span className="num">{index + 1}</span>{pick(q.text)}
          {!q.skippable ? <small>{t("uep.intake.required")}</small> : null}</legend>
        {q.options.length > 0 && q.mode !== "text" ? <div className="uep-intake-options">{q.options.map((o, n) =>
          <label key={o.id} className={current.option === o.id ? "on" : ""}>
            <input type="radio" name={`intake-${q.id}`} checked={current.option === o.id}
              onChange={() => set({ option: o.id })} />
            <span className="num">{n + 1}</span>{pick(o.label)}
          </label>)}</div> : null}
        {q.mode !== "choice" ? <textarea rows={2} maxLength={2000} value={current.text ?? ""} placeholder={t("uep.intake.textPh")}
          onChange={(event) => set({ text: event.target.value })} /> : null}
        {current.option && q.skippable ? <button type="button" className="linkish" onClick={() => set({ option: undefined })}>
          {t("uep.intake.clear")}</button> : null}
      </fieldset>;
    })}
    <textarea className="uep-intake-note" rows={2} maxLength={2000} value={note} placeholder={t("uep.intake.notePh")}
      onChange={(event) => setNote(event.target.value)} />
    <div className="decision-submit-row">
      <button type="button" className="primary-btn" onClick={() => void submit()} disabled={submitting || missing.length > 0}>
        {t("uep.intake.submit")}</button>
      {missing.length > 0 ? <span className="muted decision-hint">{t("uep.intake.missing").replace("{n}", String(missing.length))}</span> : null}
    </div>
    {error ? <div className="notice">{error}</div> : null}
  </section>;
}
