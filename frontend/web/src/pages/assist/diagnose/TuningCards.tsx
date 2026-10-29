import type { DiagnosisChangeCardDto, DiagnosisTuningDto, TuneCardDto } from "@pokemon-champions/protocol";
import { useDamageText } from "../../../lib/damageText.tsx";
import { useProseRenderer } from "../../../lib/prose.tsx";
import { optionalAssistKey, useAssistT } from "../messages.ts";

type Target = DiagnosisTuningDto["targets"][number];

/** A threshold is kept separate from both native KO prose and tested team gains. */
export function Threshold({ card }: { card?: Pick<TuneCardDto, "result" | "stat" | "need_total" | "delta_sp" | "allocation"> }) {
  const t = useAssistT();
  if (!card) return <span>{t("diag.changeCard.unknown")}</span>;
  const stat = (key: string) => { const k = optionalAssistKey(`stat.${key}`); return k ? t(k) : key; };
  const status = optionalAssistKey(`diag.tune.result.${card.result}`);
  const hasThreshold = ["already", "cliff", "infeasible"].includes(card.result);
  return <span className="diag-threshold">
    <b>{status ? t(status) : t("diag.changeCard.unknown")}</b>
    {hasThreshold && card.stat && card.need_total != null && <span>{stat(card.stat)} {card.need_total} SP</span>}
    {hasThreshold && card.allocation && <span>{Object.entries(card.allocation).map(([key, lane]) =>
      `${stat(key)} ${lane.need_total} SP`).join(" · ")}</span>}
    {hasThreshold && card.delta_sp != null && card.delta_sp > 0 && <span>+{card.delta_sp} SP</span>}
  </span>;
}

function TargetDetails({ target }: { target: Target }) {
  const t = useAssistT();
  const prose = useProseRenderer();
  const damage = useDamageText();
  const build = target.opponentBuild;
  const stat = (key: string) => { const k = optionalAssistKey(`stat.${key}`); return k ? t(k) : key; };
  return <>
    <p>{prose([build.species, build.item, build.ability, build.nature].filter(Boolean).join(" / "))}</p>
    <p className="muted num">{Object.entries(build.spread).map(([key, n]) => `${stat(key)} ${n}`).join(" · ")}</p>
    <p className="muted">{prose(build.moves.join(" / "))}</p>
    <p className="muted">{t("diag.tune.conditions")}: {Object.entries(target.conditions).map(([key, value]) => {
      const label = optionalAssistKey(`diag.tune.condition.${key}`);
      const localized = typeof value === "boolean" ? t(value ? "diag.tune.on" : "diag.tune.off") : prose(String(value));
      return <span className="diag-condition" key={key}>{label ? t(label) : key}: {localized}</span>;
    })}</p>
    {target.probability && <p className="muted">{t("diag.tune.probability")}: {
      (() => { const key = optionalAssistKey(`diag.tune.prob.${target.probability}`); return key ? t(key) : target.probability; })()
    }{target.hits ? ` · ${t("diag.tune.hits").replace("{n}", String(target.hits))}` : ""}</p>}
    {target.scope === "raw_damage_threshold" && <p className="notice">{t("diag.tune.damageScope")}</p>}
    {target.sourceDamageCaveats.length > 0 && <div className="notice">
      <b>{t("diag.tune.sourceCaveats")}</b>
      {target.sourceDamageCaveats.map((c, i) => <p key={i}>{damage.caveat(c)}</p>)}
    </div>}
  </>;
}

export function TuningCards({ tunings }: { tunings: DiagnosisTuningDto[] }) {
  const t = useAssistT();
  const prose = useProseRenderer();
  const stat = (key: string) => { const k = optionalAssistKey(`stat.${key}`); return k ? t(k) : key; };
  return tunings.map((tuning) => <section className="panel builder-result diag-section diag-tuning wide" key={tuning.tuningId}>
    <h2>{t("diag.tune.title")}</h2>
    <p className="muted diag-note">{t("diag.tune.scope")}</p>
    {tuning.targets.map((target, i) => {
      const card = tuning.result.cards[i];
      if (!card) return <p key={i}>{t("diag.changeCard.unknown")}</p>;
      return <article className="diag-tuning-target" key={i}>
      <h3>{prose(card.member)} · {t(`diag.tune.kind.${target.kind}`)}
        {card.move ? <> · {prose(card.move)}</> : null}</h3>
      <TargetDetails target={target} />
      <Threshold card={card} />
      {card.reallocation && <>
        <p>{t("diag.tune.reallocate").replace("{n}", String(card.reallocation.required_sp))}</p>
        <p className="muted">{t("diag.tune.donors")}: {card.reallocation.candidate_donors.map((d) =>
          `${stat(d.stat)} ${d.current_sp} SP`).join(" · ")}</p>
        <p className="muted">{t("diag.tune.donorCost")}</p>
      </>}
      {!!card.nature_alternatives?.length && <details>
        <summary>{t("diag.tune.natures")}</summary>
        <p className="muted">{t("diag.tune.natureCost")}</p>
        {card.nature_alternatives.map((lane, n) => <p key={n}>
          {prose(lane.nature)}{lane.penalty_stat ? ` · −10% ${stat(lane.penalty_stat)}` : ""}
          <Threshold card={lane} />
        </p>)}
      </details>}
    </article>; })}
  </section>);
}

export function TuningCheck({ check }: { check: NonNullable<DiagnosisChangeCardDto["tuningCheck"]> }) {
  const t = useAssistT();
  const prose = useProseRenderer();
  return <div className="diag-tuning-check">
    <h3>{t("diag.tune.recheck")}</h3>
    <p className={check.allThresholdsMet ? "muted" : "notice"}>{t(check.allThresholdsMet ? "diag.tune.met" : "diag.tune.unmet")}</p>
    {check.targets.map((target, i) => <div key={i} className="diag-tuning-target">
      <b>{prose(target.opponentBuild.species)} · {t(`diag.tune.kind.${target.kind}`)}</b>
      <div className="diag-threshold-compare">
        <div><small>{t("diag.changeCard.before")}</small><Threshold card={check.before.cards[i]} /></div>
        <span aria-hidden="true">→</span>
        <div><small>{t("diag.changeCard.after")}</small><Threshold card={check.after.cards[i]} /></div>
      </div>
      <details><summary>{t("diag.tune.targetDetails")}</summary><TargetDetails target={target} /></details>
    </div>)}
  </div>;
}
