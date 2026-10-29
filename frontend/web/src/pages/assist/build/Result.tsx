/** Read-only presentation of a completed, audited build. Job execution stays in BuilderPage. */
import { type BuilderJobDto, type BuilderResultDto, type MatchupThreatAssessmentDto, type OppSetDto } from "@pokemon-champions/protocol";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { FormatId } from "@pokemon-champions/protocol";
import { EntityHover } from "../../../components/EntityHover.tsx";
import { useDexByName, useOppSets } from "../../../hooks.ts";
import { displayName, useLang } from "../../../i18n.ts";
import { optionalAssistKey, useAssistT } from "../messages.ts";
import { useProseRenderer } from "../../../lib/prose.tsx";
import { readTeamMembers, stashCalcTeams, stashDamageFill, stashMatchupFill, teamToCalcMembers } from "../../../lib/team.ts";
import { stashDiagnoseFill } from "../../../lib/diagnoseHandoff.ts";
import { type DexIndexEntry } from "../../../runtime/adapter.ts";
import { useRuntime } from "../../../runtime/context.tsx";
import { MemberHover, slugify, useMemberProse, useMemberSets } from "../../../components/team/MonChip.tsx";
import { TeamCard } from "../../../components/team/TeamCard.tsx";
import { HintButton } from "../../../components/HintButton.tsx";
import { SegmentedControl } from "../../../components/SegmentedControl.tsx";
import { toTeamDoc } from "../../../lib/teamDoc.ts";
import { type AssistRecord } from "../../../lib/runRecords.ts";
import { FreshnessNote } from "../Workspace.tsx";
import type { BuildInput } from "./model.ts";

type ThreatRoute = MatchupThreatAssessmentDto["opponents"][number]["routes"][number];
/** Threats listed before the rest fold away (the worst first, as the assessment orders them). */
const THREATS_SHOWN = 3;

function variantFacts(key: string | null | undefined): { item?: string; ability?: string } {
  if (!key?.startsWith("variant:")) return {};
  const fields = Object.fromEntries(key.split("|").slice(1).flatMap((part) => {
    const at = part.indexOf("=");
    return at > 0 ? [[part.slice(0, at), decodeURIComponent(part.slice(at + 1))]] : [];
  }));
  return { ...(fields.item ? { item: fields.item } : {}),
    ...(fields.ability ? { ability: fields.ability } : {}) };
}

function ThreatAssessmentPanel({ assessment, team, format, dexByName }: {
  assessment: MatchupThreatAssessmentDto;
  team: unknown;
  format: FormatId;
  dexByName: Map<string, DexIndexEntry>;
}) {
  const t = useAssistT();
  const { lang } = useLang();
  const prose = useProseRenderer();
  const navigate = useNavigate();
  const members = readTeamMembers(team);
  const [setsRequested, setSetsRequested] = useState(false);
  const [pendingCalc, setPendingCalc] = useState<{ opponent: string; route: ThreatRoute } | null>(null);
  const oppSetsState = useOppSets(format, setsRequested);
  const oppSets = oppSetsState.status === "ready" ? oppSetsState.data?.sets : undefined;

  const openCalc = (opponent: string, route: ThreatRoute,
                    sets?: Record<string, OppSetDto>) => {
    const opponentEntry = dexByName.get(opponent);
    const attackerSlug = opponentEntry?.slug ?? slugify(opponent);
    const targetSet = sets?.[route.opponentVariant ?? attackerSlug];
    const member = members.find((candidate) => candidate.species === route.member);
    stashDamageFill({
      format,
      attackerSlug,
      attacker: {
        slug: attackerSlug,
        ...(targetSet?.ability ? { ability: targetSet.ability } : {}),
        ...(targetSet?.item ? { item: targetSet.item } : {}),
        ...(targetSet?.nature ? { nature: targetSet.nature } : {}),
        ...(targetSet?.sps ? { sps: targetSet.sps as Record<string, number> } : {}),
      },
      defender: {
        slug: dexByName.get(route.member)?.slug ?? slugify(route.member),
        ...(member?.ability ? { ability: member.ability } : {}),
        ...(member?.item ? { item: member.item } : {}),
        ...(member?.nature ? { nature: member.nature } : {}),
        ...(member?.spread ? { sps: member.spread } : {}),
      },
      move: route.move,
    });
    navigate("/calc?tab=damage");
  };

  const requestCalc = (opponent: string, route: ThreatRoute) => {
    if (setsRequested && oppSetsState.status === "ready") {
      openCalc(opponent, route, oppSets);
      return;
    }
    setPendingCalc({ opponent, route });
    setSetsRequested(true);
  };

  useEffect(() => {
    if (!pendingCalc || (oppSetsState.status !== "ready" && oppSetsState.status !== "error")) return;
    openCalc(pendingCalc.opponent, pendingCalc.route,
      oppSetsState.status === "ready" ? oppSetsState.data?.sets : undefined);
    setPendingCalc(null);
  }, [oppSetsState.status, pendingCalc]);

  const worst = assessment.worst;
  // Routes land on OUR members, whose generated builds are known; opponents keep the dex hover.
  const memberOf = useMemberSets(team);
  // Like the diagnosis's answers table: the first few threats, the rest one click away.
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? assessment.opponents : assessment.opponents.slice(0, THREATS_SHOWN);
  const grades = assessment.opponents.reduce<Record<string, number>>((acc, threat) => {
    acc[threat.grade] = (acc[threat.grade] ?? 0) + 1;
    return acc;
  }, {});
  return (
    <div className="builder-threat-assessment">
      <p className="muted builder-threat-method">
        {t("builder.threatMethod").replace("{k}", String(assessment.scope.topK))}
      </p>
      {worst && (
        <div className="builder-worst gate-detail-row">
          <span className="gate-detail-label">{t("builder.worst")}</span>
          <span className={`threat-grade threat-${worst.grade}`}>{worst.grade}</span>
          <EntityHover kind="pokemon" name={worst.opponent}>
            <span className="gate-chip">
              {dexByName.get(worst.opponent)
                ? displayName(dexByName.get(worst.opponent)!, lang) : worst.opponent}
            </span>
          </EntityHover>
          <span className="muted num">
            {t("builder.threatBasis")
              .replace("{affected}", String(worst.basis.affectedMemberCount))
              .replace("{team}", String(worst.basis.teamSize))
              .replace("{rank}", worst.basis.usageRank == null ? "?" : String(worst.basis.usageRank))
              .replace("{routes}", String(worst.basis.routeCount))}
          </span>
        </div>
      )}
      <div className="builder-threat-list">
        <span className="gate-detail-label">{t("builder.threatFacts")}</span>
        {assessment.opponents.length > 0 && (
          <div className="diag-check-overview builder-threat-overview">
            {(["G3", "G2", "G1"] as const).filter((g) => grades[g]).map((g) => (
              <span key={g} className={`diag-check-count threat-${g}`}>{g} <span className="num">{grades[g]}</span></span>
            ))}
            <span className="muted diag-check-shown">
              {t("diag.checks.shown").replace("{shown}", String(shown.length))
                .replace("{total}", String(assessment.opponents.length))}
            </span>
            {assessment.opponents.length > THREATS_SHOWN && (
              <button type="button" className="diag-check-toggle" aria-expanded={showAll}
                onClick={() => setShowAll((value) => !value)}>
                {showAll ? t("builder.threats.showTop").replace("{n}", String(THREATS_SHOWN))
                  : t("diag.checks.showAll").replace("{total}", String(assessment.opponents.length))}
                <svg viewBox="0 0 12 12" aria-hidden><path d="M3 4.5 6 7.5 9 4.5" /></svg>
              </button>
            )}
          </div>
        )}
        {assessment.opponents.length === 0 ? (
          <p className="muted">{t("builder.threatNone")}</p>
        ) : shown.map((threat, threatIndex) => (
          <details key={threat.opponent} open={threatIndex === 0} className="builder-threat-group">
            <summary>
              <span className={`threat-grade threat-${threat.grade}`}>{threat.grade}</span>
              <EntityHover kind="pokemon" name={threat.opponent}>
                <span className="gate-chip">
                  {dexByName.get(threat.opponent)
                    ? displayName(dexByName.get(threat.opponent)!, lang) : threat.opponent}
                </span>
              </EntityHover>
              <span className="muted num">
                {t("builder.threatSummary")
                  .replace("{affected}", String(threat.affectedMemberCount))
                  .replace("{team}", String(assessment.scope.teamSize))
                  .replace("{rank}", threat.usageRank == null ? "?" : String(threat.usageRank))
                  .replace("{routes}", String(threat.routeCount))
                  .replace("{variants}", String(threat.variantCount))}
              </span>
            </summary>
            <div className="builder-threat-routes">
              {threat.routes.map((route, routeIndex) => {
                const set = oppSets?.[route.opponentVariant ?? slugify(threat.opponent)];
                const summary = set ?? variantFacts(route.opponentVariant);
                const memberEntry = dexByName.get(route.member);
                const memberSet = memberOf(route.member);
                const memberChip = (
                  <span className="gate-chip">
                    {memberEntry ? displayName(memberEntry, lang) : route.member}
                  </span>
                );
                return (
                  <div className="builder-threat-route"
                       key={`${route.evidenceId}:${route.opponentVariant ?? "base"}:${routeIndex}`}>
                    <span>{prose(route.move)}</span>
                    <span aria-hidden="true">→</span>
                    {memberSet ? <MemberHover mon={memberSet}>{memberChip}</MemberHover>
                      : <EntityHover kind="pokemon" name={route.member}>{memberChip}</EntityHover>}
                    {(summary.item || summary.ability) && (
                      <span className="muted">
                        {prose([summary.item, summary.ability].filter(Boolean).map(String).join(" · "))}
                      </span>
                    )}
                    {route.opponentIsModal === true && (
                      <span className="muted">{t("builder.threatModal")}</span>
                    )}
                    <button type="button" className="linkish threat-calc"
                            title={t("builder.verifyCalc")}
                            aria-label={t("builder.verifyCalc")}
                            aria-busy={pendingCalc?.route.evidenceId === route.evidenceId}
                            disabled={pendingCalc !== null}
                            onClick={() => requestCalc(threat.opponent, route)}>⚔</button>
                  </div>
                );
              })}
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}

type Floor = "C0" | "C1" | "C2" | null;

/** A team-floor grade as the diagnosis report draws it; `null` = not a hole there. */
function FloorBadge({ grade }: { grade: Floor | undefined }) {
  const t = useAssistT();
  if (!grade) return <span className="muted" title={t("builder.compare.noHole")}>—</span>;
  return <span className={`diag-grade diag-grade-${grade}`}>{grade}</span>;
}

/** Where the candidates differ: every top-K opponent that is a hole for at least one of them, each
 * candidate's floor side by side. Facts only — the page names no better candidate. */
function ComparisonCard({ result, local }: { result: BuilderResultDto; local: (name: string) => string }) {
  const t = useAssistT();
  const comparison = result.comparison!;
  const labels = comparison.labels;
  return (
    <article className="ar-card">
      <header className="ar-card-head">
        <span className="ar-card-icon threat" aria-hidden="true" />
        <h2>{t("builder.compare.title")}</h2>
      </header>
      <p className="muted builder-threat-method">
        {t("builder.compare.note").replace("{k}", String(comparison.topK))}
      </p>
      <p className="ar-facts num">
        {labels.map((label) => t("builder.compare.holes").replace("{label}", label)
          .replace("{n}", String(comparison.holeCounts[label] ?? 0))).join(" · ")}
      </p>
      {comparison.teamFloorDifferences.length === 0 ? (
        <p className="muted">{t("builder.compare.same")}</p>
      ) : (
        <table className="builder-compare">
          <thead><tr><th>{t("builder.compare.opponent")}</th>
            {labels.map((label) => <th key={label}>{t("builder.candidate").replace("{label}", label)}</th>)}</tr></thead>
          <tbody>
            {comparison.teamFloorDifferences.map((row) => (
              <tr key={row.opponent}>
                <td>
                  <EntityHover kind="pokemon" name={row.opponent}><span className="gate-chip">{local(row.opponent)}</span></EntityHover>
                  {row.usageRank != null && <span className="muted num"> #{row.usageRank}</span>}
                </td>
                {labels.map((label) => <td key={label}><FloorBadge grade={row.floors[label] as Floor} /></td>)}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </article>
  );
}

/** The free-text goal and, per Pokémon it names, each candidate's team floor against it. */
function GoalCard({ result, local, labels, topK }: {
  result: BuilderResultDto; local: (name: string) => string; labels: string[]; topK: number;
}) {
  const t = useAssistT();
  const prose = useProseRenderer();
  const goal = result.goal!;
  return (
    <article className="ar-card">
      <header className="ar-card-head">
        <span className="ar-card-icon notes" aria-hidden="true" />
        <h2>{t("builder.goal.title")}</h2>
      </header>
      <p className="qa-answer-text">{goal.text}</p>
      {goal.floors.length === 0 ? <p className="muted">{t("builder.goal.noMention")}</p> : (
        <table className="builder-compare">
          <thead><tr><th>{t("builder.goal.named")}</th>
            {labels.map((label) => <th key={label}>{labels.length > 1 ? t("builder.candidate").replace("{label}", label) : t("builder.goal.floor")}</th>)}</tr></thead>
          <tbody>
            {goal.floors.map((row) => (
              <tr key={row.pokemon}>
                <td>
                  <EntityHover kind="pokemon" name={row.pokemon}><span className="gate-chip">{local(row.pokemon)}</span></EntityHover>
                  {row.intent && <span className="muted"> · {t(`builder.intent.${row.intent}`)}</span>}
                </td>
                {row.inBattery && row.floors
                  ? labels.map((label) => <td key={label}><FloorBadge grade={row.floors![label] as Floor} /></td>)
                  : <td colSpan={labels.length} className="muted">{prose(t("builder.goal.outside").replace("{k}", String(topK)))}</td>}
              </tr>
            ))}
            {goal.floors.filter((row) => row.intent === "beat" && row.answerable === false).map((row) => (
              <tr key={`${row.pokemon}:note`}>
                <td colSpan={labels.length + 1} className="muted">
                  {t("builder.goal.unanswerable").replace("{k}", String(topK)).replace("{pokemon}", local(row.pokemon))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted builder-threat-method">{t("builder.goal.note")}</p>
    </article>
  );
}

/** A finished build: the team with its checks, the reasoning, the threats it faces, the run's scope
 * and assumptions — each its own card. With compared candidates a switcher picks the one shown and the
 * comparison follows it. The run itself follows, folded (`BuildRun`). */
export function BuildResult({ record, job, onRerun, candidate = "A", onCandidate }: {
  record: AssistRecord<BuildInput>; job: BuilderJobDto; onRerun: () => void;
  /** The compared candidate on screen (its label); the page keeps it so the library saves that one. */
  candidate?: string;
  onCandidate?: (label: string) => void;
}) {
  const t = useAssistT();
  const { lang } = useLang();
  const { adapter, can } = useRuntime();
  const navigate = useNavigate();
  const prose = useProseRenderer();
  const dexByName = useDexByName();
  const full = job.result!;
  const candidates = full.candidates ?? [];
  const chosen = candidates.find((c) => c.label === candidate) ?? candidates[0];
  // The shown candidate in the single-team shape the cards below already read.
  const result = chosen ? { ...full, ...chosen } : full;
  const resultFormat = toTeamDoc(result.team, record.input.format)?.format ?? record.input.format;
  const memberProse = useMemberProse(result.team);
  // Diagnose is its own entry on the assist page (roadmap §1.2: a deterministic report must not
  // sit behind an "AI" label); the result hands its team over through a navigation.
  const canDiagnose = can("team.validate") && !!adapter.diagnose;
  // Current results load the build catalog only when a visitor asks for calculator hand-off.
  // Legacy results still need it here because their old threat DTO has no self-describing variant id.
  const needsLegacyOppSets = !result.matchupThreats
    && (!!result.worstMatchup || (result.threats?.length ?? 0) > 1);
  const oppSetsState = useOppSets(resultFormat, needsLegacyOppSets);
  const oppSets = oppSetsState.status === "ready" ? oppSetsState.data?.sets : undefined;
  const team = result.team;
  const toCalc = (opponent: string, member?: string | null, move?: string | null) => {
    const found = member ? readTeamMembers(team).find((m) => m.species === member) : undefined;
    const aSlug = dexByName.get(opponent)?.slug ?? slugify(opponent);
    const aSet = oppSets?.[aSlug];
    stashDamageFill({
      format: resultFormat,
      attackerSlug: aSlug,
      attacker: {
        slug: aSlug,
        ...(aSet?.ability ? { ability: aSet.ability } : {}),
        ...(aSet?.item ? { item: aSet.item } : {}),
        ...(aSet?.nature ? { nature: aSet.nature } : {}),
        ...(aSet?.sps ? { sps: aSet.sps as Record<string, number> } : {}),
      },
      ...(member ? { defender: {
        slug: dexByName.get(member)?.slug ?? slugify(member),
        ...(found?.ability ? { ability: found.ability } : {}),
        ...(found?.item ? { item: found.item } : {}),
        ...(found?.nature ? { nature: found.nature } : {}),
        ...(found?.spread ? { sps: found.spread } : {}),
      } } : {}),
      ...(move ? { move } : {}),
    });
    navigate("/calc?tab=damage");
  };
  const local = (name: string) => {
    const entry = dexByName.get(name);
    return entry ? displayName(entry, lang) : name;
  };
  return (
    <>
      <article className="ar-card build-card">
        <header className="ar-card-head">
          <span className="ar-card-icon build" aria-hidden="true" />
          <h2>{t("builder.result.title")}</h2>
          <span className="ar-card-tags">
            <span className="badge-note">{t(`format.${resultFormat}`)}</span>
            <span className="badge-ok">✓ {t("builder.badge.valid")}</span>
            <span className="badge-ok">✓ {t("builder.badge.audit")}</span>
            {result.repaired && <span className="badge-note">{t("builder.badge.repaired")}</span>}
          </span>
        </header>
        <FreshnessNote record={record} formats={[record.input.format]} action={
          <button type="button" className="aw-fresh-act" onClick={onRerun}>{t("builder.rerun")}</button>} />
        {candidates.length > 1 && (
          <div className="builder-candidates">
            <SegmentedControl<string> kind="radio" value={chosen?.label ?? "A"} ariaLabel={t("builder.candidates")}
              className="seg" onChange={(label) => onCandidate?.(label)}
              items={candidates.map((c) => ({ id: c.label, label: c.goalFor
                ? t("builder.candidateGoal").replace("{label}", c.label).replace("{pokemon}", local(c.goalFor))
                : t("builder.candidate").replace("{label}", c.label) }))} />
            <span className="muted">{t("builder.candidatesNote")}</span>
          </div>
        )}
        <TeamCard team={team} keepOrigin="builder" saveSource="builder-keep" actions={
          <>
            {canDiagnose && (
              <button type="button" className="second-btn"
                onClick={() => { stashDiagnoseFill(toTeamDoc(team, resultFormat) ?? team); navigate("/assist?tab=diagnose"); }}>
                {t("builder.sendDiagnose")}
              </button>
            )}
            <HintButton hint={t("team.sendCalcHint")}
              onClick={() => {
                stashCalcTeams({ format: resultFormat, attackers: teamToCalcMembers(team), defenders: [] });
                navigate("/calc?tab=damage");
              }}>{t("team.sendCalc")}</HintButton>
            <HintButton hint={t("actual.sendMatchupHint")} onClick={() => {
              stashMatchupFill({ source: "builder", label: t("actual.source.builder"), format: resultFormat, team });
              navigate("/matchup?mode=actual");
            }}>{t("actual.sendMatchup")}</HintButton>
          </>
        } />
        {result.rationale && (
          <div className="builder-rationale">
            <span className="ar-label">{t("builder.rationale")}</span>
            {result.rationale.split(/\n+/).map((paragraph, index) => (
              <p key={index} className="qa-answer-text">{memberProse(paragraph)}</p>
            ))}
          </div>
        )}
      </article>

      {full.comparison && <ComparisonCard result={full} local={local} />}
      {full.goal && <GoalCard result={full} local={local} topK={full.slateTopK}
        labels={candidates.length > 1 ? candidates.map((c) => c.label) : ["A"]} />}

      {(result.matchupThreats || result.worstMatchup || (result.threats?.length ?? 0) > 1) && (
        <article className="ar-card">
          <header className="ar-card-head">
            <span className="ar-card-icon threat" aria-hidden="true" />
            <h2>{t("builder.result.threats")}</h2>
          </header>
          {result.matchupThreats && (
            <ThreatAssessmentPanel assessment={result.matchupThreats}
              team={team} format={resultFormat} dexByName={dexByName} />
          )}
          {!result.matchupThreats && (result.threats?.length ?? 0) > 1 && (
            <div className="gate-detail-row">
              <span className="gate-detail-label">{t("builder.threats")}</span>
              <span className="gate-detail-chips">
                {result.threats!.map((th) => (
                  <span key={th.opponent} className="threat-chip">
                    <EntityHover kind="pokemon" name={th.opponent}>
                      <span className="gate-chip">{local(th.opponent)}</span>
                    </EntityHover>
                    <button type="button" className="linkish threat-calc" title={t("builder.verifyCalc")}
                      aria-label={t("builder.verifyCalc")} onClick={() => toCalc(th.opponent, th.member, th.move)}>⚔</button>
                  </span>
                ))}
              </span>
            </div>
          )}
          {/* Worst-matchup battery fact with a calculator hand-off (design §13): the
              opponent attacks with its usage set; MY member defends with its real build. */}
          {!result.matchupThreats && result.worstMatchup && (() => {
            const wm = result.worstMatchup;
            const set = wm.member ? readTeamMembers(team).find((m) => m.species === wm.member) : undefined;
            const chip = wm.member ? <span className="gate-chip">→ {local(wm.member)}</span> : null;
            return (
              <div className="builder-worst gate-detail-row">
                <span className="gate-detail-label">{t("builder.worst")}</span>
                <span className="gate-detail-chips">
                  <span className="gate-chip">{local(wm.opponent)}</span>
                  {wm.move && <span className="muted">{prose(wm.move)}</span>}
                  {chip && (set ? <MemberHover mon={set}>{chip}</MemberHover> : chip)}
                </span>
                <button type="button" className="second-btn builder-verify"
                  onClick={() => toCalc(wm.opponent, wm.member, wm.move)}>{t("builder.verifyCalc")}</button>
              </div>
            );
          })()}
        </article>
      )}

      <article className="ar-card ar-card-quiet">
        <header className="ar-card-head">
          <span className="ar-card-icon notes" aria-hidden="true" />
          <h2>{t("builder.result.notes")}</h2>
        </header>
        <p className="ar-facts num">
          {t("builder.scope").replace("{k}", String(result.slateTopK))}
          {result.megaCount != null ? ` · ${t("builder.megaCount").replace("{n}", String(result.megaCount))}` : ""}
          {full.droppedCandidates ? ` · ${t("builder.dropped").replace("{n}", String(full.droppedCandidates))}` : ""}
        </p>
        <ul className="ar-list">
          {result.assumptions.map((k) => {
            const key = optionalAssistKey(`assumption.${k}`);
            return <li key={k}>{key ? t(key) : k}</li>;
          })}
        </ul>
      </article>
    </>
  );
}
