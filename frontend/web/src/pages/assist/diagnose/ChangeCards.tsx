import type { DiagnoseFactsDto, DiagnosisChangeCardDto } from "@pokemon-champions/protocol";
import { useState } from "react";
import { useOppSets } from "../../../hooks.ts";
import { useProseRenderer } from "../../../lib/prose.tsx";
import { optionalAssistKey, useAssistT } from "../messages.ts";
import { TuningCards, TuningCheck } from "./TuningCards.tsx";

type Assistance = NonNullable<DiagnoseFactsDto["assistance"]>;

export function ChangeCards({ assistance, format, onAdopt }: {
  assistance: Assistance; format: "single" | "double"; onAdopt?: (card: DiagnosisChangeCardDto) => void;
}) {
  const t = useAssistT();
  return <>
    <TuningCards tunings={assistance.tunings ?? []} />
    {assistance.actions.map((card) => <ChangeCard key={card.verificationId} card={card} format={format} onAdopt={onAdopt} />)}
    {assistance.toolTrace.length > 0 && <details className="panel builder-result diag-section diag-tool-trace wide">
      <summary>{t("diag.tools.title").replace("{n}", String(assistance.toolTrace.length))}</summary>
      <ol>{assistance.toolTrace.map((entry, i) => <li key={i}>
        <span>{t(`diag.tool.${entry.tool}`)}</span>
        <span>{t(entry.cacheHit ? "diag.tool.reused" : entry.status === "done" ? "diag.tool.done" : "diag.tool.error")}</span>
        <span className="num muted">{(entry.durationMs / 1000).toFixed(1)} s</span>
        {entry.errorCode && <span className="muted">{t(optionalAssistKey(`diag.tool.error.${entry.errorCode}`) ?? "diag.tool.error.generic")}</span>}
      </li>)}</ol>
    </details>}
  </>;
}

function ChangeCard({ card, format, onAdopt }: {
  card: DiagnosisChangeCardDto; format: "single" | "double"; onAdopt?: (card: DiagnosisChangeCardDto) => void;
}) {
  const t = useAssistT();
  const prose = useProseRenderer();
  const catalog = useOppSets(format);
  const [allCells, setAllCells] = useState(false);
  const tested = card.status === "tested";
  const status = !tested ? "invalid" : card.hasTeamLosses ? "losses" : card.hasTeamGains ? "gains" : "unchanged";
  const value = (v: unknown) => {
    if (v === null || v === undefined || v === "") return "—";
    if (Array.isArray(v)) return prose(v.join(" / "));
    if (typeof v === "object") return Object.entries(v).map(([key, n]) => {
      const label = optionalAssistKey(`stat.${key}`);
      return `${label ? t(label) : key} ${String(n)}`;
    }).join(" · ");
    return prose(String(v));
  };
  const cells = allCells ? card.memberCellChanges : card.memberCellChanges.slice(0, 8);
  return <section className={`panel builder-result diag-section diag-change-card wide ${card.hasTeamLosses ? "has-losses" : ""}`}>
    <header className="diag-change-head">
      <h2>{t("diag.changeCard.title")}</h2>
      <span className={`diag-change-status ${status}`}>{t(`diag.changeCard.${status}`)}</span>
    </header>
    <p className="muted diag-note">{t("diag.changeCard.copy")}</p>
    <div className="diag-change-diff">
      {card.changes.map((change) => <div key={change.slot}>
        <h3>{prose(change.species)}</h3>
        {Object.keys(change.before).map((key) => {
          const field = key as keyof typeof change.before;
          const label = optionalAssistKey(`diag.change.${field}`);
          return <div className="diag-change-field" key={field}>
            <b>{label ? t(label) : field}</b>
            <span>{value(change.before[field])}</span><span aria-hidden="true">→</span><span>{value(change.after[field])}</span>
          </div>;
        })}
      </div>)}
    </div>
    {!tested ? <>
      <p className="notice">{t("diag.changeCard.invalidNote")}</p>
      {card.legality.issues.length > 0 && <ul>{card.legality.issues.map((issue, i) => {
        const key = optionalAssistKey(`validation.${issue.code}`);
        const text = key ? t(key).replace(/\{([^}]+)\}/g, (_, name: string) => String(issue.params[name] ?? "—"))
          : t("diag.warningGeneric");
        return <li key={i}>{prose(text)}</li>;
      })}</ul>}
    </> : <>
      {card.tuningCheck && <TuningCheck check={card.tuningCheck} />}
      <h3>{t("diag.changeCard.teamFloors")}</h3>
      {card.teamFloorChanges.length ? <div className="diag-change-table-wrap"><table className="data-table diag-change-table">
        <thead><tr><th>{t("diag.checks.opponent")}</th><th>{t("diag.changeCard.before")}</th><th>{t("diag.changeCard.after")}</th><th>{t("diag.changeCard.result")}</th></tr></thead>
        <tbody>{card.teamFloorChanges.map((row) => <tr key={row.opponent}>
          <td>{prose(row.opponent)}</td><td>{row.before}</td><td>{row.after}</td><td className={`change-${row.direction}`}>{t(`diag.changeCard.${row.direction}`)}</td>
        </tr>)}</tbody>
      </table></div> : <p>{t("diag.changeCard.noTeamChange").replace("{n}", String(card.coverage.comparableOpponents))}</p>}
      {card.coverage.uncomparedOpponents.length > 0 && <p className="notice">{t("diag.changeCard.partial")}{" "}{prose(card.coverage.uncomparedOpponents.join(" / "))}</p>}
      <h3>{t("diag.changeCard.memberCells")}</h3>
      <p className="muted diag-note">{t("diag.changeCard.levelNote")}</p>
      {cells.length ? <div className="diag-change-table-wrap"><table className="data-table diag-change-table">
        <thead><tr><th>{t("diag.changeCard.member")}</th><th>{t("diag.checks.opponent")}</th><th>{t("diag.changeCard.before")}</th><th>{t("diag.changeCard.after")}</th><th>{t("diag.changeCard.result")}</th></tr></thead>
        <tbody>{cells.map((row, i) => {
          const build = catalog.status === "ready" ? catalog.data.sets[row.variantId] : undefined;
          return <tr key={`${row.memberSlot}-${row.variantId}-${i}`}>
            <td>{prose(row.afterMember ?? row.beforeMember ?? "—")}</td>
            <td>{prose(row.opponent)}{build && <small className="muted">{prose([build.item, build.ability].filter(Boolean).join(" / "))}</small>}</td>
            <td>{row.before ?? "?"}{row.beforeContested ? " *" : ""}</td><td>{row.after ?? "?"}{row.afterContested ? " *" : ""}</td>
            <td>{t(`diag.changeCard.${row.direction}`)}</td>
          </tr>;
        })}</tbody>
      </table></div> : <p className="muted">{t("diag.changeCard.noCellChange")}</p>}
      {card.memberCellChanges.length > 8 && <button type="button" className="second-btn" onClick={() => setAllCells((v) => !v)}>
        {t(allCells ? "diag.changeCard.less" : "diag.changeCard.more").replace("{n}", String(card.memberCellChanges.length))}
      </button>}
      <p className="muted diag-note">{t("diag.changeCard.scope").replace("{k}", String(card.coverage.topK))}</p>
      {onAdopt && <button type="button" className="second-btn" onClick={() => onAdopt(card)}>{t("diag.changeCard.draft")}</button>}
    </>}
  </section>;
}
