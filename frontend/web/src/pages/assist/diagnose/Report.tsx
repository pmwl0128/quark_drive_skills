/** Diagnostic facts and their evidence views; input, jobs and persistence stay in DiagnoseTab. */
import type { DiagnoseFactsDto } from "@pokemon-champions/protocol";
import { createContext, memo, useContext, useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { EntityHover } from "../../../components/EntityHover.tsx";
import { TypeBadge } from "../../../components/TypeBadge.tsx";
import { useDexByName } from "../../../hooks.ts";
import { displayName, useLang } from "../../../i18n.ts";
import { optionalAssistKey, useAssistT } from "../messages.ts";
import { useNavigate } from "react-router-dom";
import { useDamageText } from "../../../lib/damageText.tsx";
import { useAbilitiesByName, useItemsByName, useOppSets } from "../../../hooks.ts";
import { readTeamMembers, stashCalcTeams, stashDamageFill, stashMatchupFill, teamToCalcMembers } from "../../../lib/team.ts";
import { MemberHover, slugify, useMemberProse, useMemberSets } from "../../../components/team/MonChip.tsx";
import type { TeamMemberish } from "../../../lib/team.ts";
import { useProseRenderer } from "../../../lib/prose.tsx";
import { TeamCard } from "../../../components/team/TeamCard.tsx";
import { HintButton } from "../../../components/HintButton.tsx";
import { setMember } from "../../../components/BuildPicker.tsx";
import { KeepMonButton } from "../../../components/KeepMonButton.tsx";

/** The diagnosed team's members by species, for the chips below that name one of them. */
const MemberSets = createContext<(species: string) => TeamMemberish | undefined>(() => undefined);
export const Report = memo(ReportView);

/** Localized species chips (English canonical in, dex display name out). A chip that names one of
 * the diagnosed team's members shows that member's build on hover; an `opponent` chip keeps the dex
 * hover even when the opponent shares a member's species. */
function SpeciesChips({ names, opponent = false }: { names: string[]; opponent?: boolean }) {
  const dex = useDexByName();
  const { lang } = useLang();
  const memberOf = useContext(MemberSets);
  if (names.length === 0) return <span className="muted">—</span>;
  return (
    <span className="gate-detail-chips">
      {names.map((n, i) => {
        const entry = dex.get(n);
        const chip = (
          <span className="gate-chip" title={n}>
            {entry ? displayName(entry, lang) : n}
          </span>
        );
        const member = opponent ? undefined : memberOf(n);
        return member
          ? <MemberHover key={`${n}-${i}`} mon={member}>{chip}</MemberHover>
          : <EntityHover key={`${n}-${i}`} kind="pokemon" name={n}>{chip}</EntityHover>;
      })}
    </span>
  );
}

/** `some`: at least one member was recognized by species ONLY — no moves and no spread —
 * so its conclusions ride on neutral assumptions (drives the "partial report" notice, whose
 * text is specifically about species-only members). `all`: NO member has a moveset, so the
 * offense/roles panels (which read moves — a spread alone tells them nothing) would be a wall
 * of phantom gaps; drop them. The two conditions differ: a member with a spread but no moves
 * is not "species-only", yet it still can't be analyzed for offense/roles. */
function speciesOnlyCount(team: unknown): { some: boolean; all: boolean } {
  const mons = (team as { pokemon?: unknown })?.pokemon;
  if (!Array.isArray(mons) || mons.length === 0) return { some: false, all: false };
  const isObj = (m: unknown): m is { moves?: unknown[]; spread?: unknown } =>
    m !== null && typeof m === "object";
  const noMoves = (m: unknown): boolean => isObj(m) && !(m.moves?.length);
  const speciesOnly = (m: unknown): boolean => noMoves(m) && (isObj(m) && m.spread == null);
  return { some: mons.some(speciesOnly), all: mons.every(noMoves) };
}

function fillTemplate(template: string, params: Record<string, string | number | boolean | null>): string {
  return template.replace(/\{([^}]+)\}/g, (_, key: string) => {
    const value = params[key];
    return value === null ? "—" : String(value ?? `{${key}}`);
  });
}

type CheckRow = NonNullable<DiagnoseFactsDto["checks"]>["byOpponent"][number];

/** Clickable per-opponent detail under the checks table: each member's own facts vs that
 * opponent (grade, our KO line, the worst incoming line, the speed read) — the matchup
 * grid's cell detail, restricted to this row. */
function CheckRowDetail({ row, team, format }: {
  row: CheckRow; team: unknown; format: "single" | "double";
}) {
  const t = useAssistT();
  const damageText = useDamageText();
  const navigate = useNavigate();
  // Every retained observed variant is an independent opponent. Its exact set is keyed by
  // variantId in the shipped build catalog; never silently substitute the species representative.
  const catalog = useOppSets(format);
  const setFor = (variantId: string) => catalog.status === "ready" ? catalog.data.sets[variantId] : undefined;
  const members = readTeamMembers(team);
  const toCalc = (cell: NonNullable<CheckRow["cells"]>[number]) => {
    const member = members.find((m) => m.species === cell.member);
    const oppSet = setFor(cell.variantId);
    const runForm = oppSet?.runForm || oppSet?.species || row.opponent;
    stashDamageFill({
      format, attackerSlug: slugify(runForm),
      attacker: {
        slug: slugify(runForm),
        ...(oppSet?.ability ? { ability: oppSet.ability } : {}),
        ...(oppSet?.item ? { item: oppSet.item } : {}),
        ...(oppSet?.nature ? { nature: oppSet.nature } : {}),
        ...(oppSet?.sps ? { sps: oppSet.sps as Record<string, number> } : {}),
      },
      defender: {
        slug: slugify(cell.member),
        ...(member?.ability ? { ability: member.ability } : {}),
        ...(member?.item ? { item: member.item } : {}),
        ...(member?.nature ? { nature: member.nature } : {}),
        ...(member?.spread ? { sps: member.spread } : {}),
      },
      ...(cell.incoming?.move ? { move: cell.incoming.move } : {}),
    });
    navigate("/calc?tab=damage");
  };
  if (!row.cells?.length) return null;
  return (
    <div className="matchup-detail diag-check-detail">
      {row.cells.map((cell) => {
        const oppSet = setFor(cell.variantId);
        return (
          <div key={`${cell.variantId}:${cell.member}`} className="diag-cell">
          <div className="md-head">
            <SpeciesChips names={[cell.member]} />
            {cell.check?.grade && (
              <span className={`grade-pill grade-${cell.check.grade}`}>
                {cell.check.grade}
              </span>
            )}
            {cell.check?.c0Kind && (
              <span className="muted"> {t(cell.check.c0Kind === "wall_no_ko"
                ? "matchup.c0Kind.wall_no_ko" : "matchup.c0Kind.loss")}</span>
            )}
            {cell.check?.resolvability === "contested" && (
              <span className="muted"> · {t("matchup.contested")}</span>
            )}
          </div>
          {oppSet && (
            <div className="md-line md-caveat">
              {damageText.name([oppSet.item, oppSet.ability, oppSet.nature]
                .filter(Boolean).join(" · "))}
              <KeepMonButton member={() => setMember(oppSet)} origin="diagnose" />
              {cell.coverage != null && (
                <span className="muted num"> · {(cell.coverage * 100).toFixed(1)}%</span>
              )}
            </div>
          )}
          {cell.offense && (
            <div className="md-line md-caveat">
              {t("matchup.ourKo")}: {damageText.name(cell.offense.move)}{" "}
              <span className="num">{cell.offense.minPercent}–{cell.offense.maxPercent}%</span>
              {" · "}{damageText.ko(offenseKoChanceDiag(cell.offense))}
            </div>
          )}
          {cell.incoming && (
            <div className="md-line md-caveat">
              {t("matchup.incoming")}: {damageText.name(cell.incoming.move)}{" "}
              <span className="num">{cell.incoming.minPercent}–{cell.incoming.maxPercent}%</span>
              {" · "}{damageText.ko(offenseKoChanceDiag(cell.incoming))}
            </div>
          )}
          <div className="md-line md-caveat num">
            {t("matchup.speed")}: {cell.speed.member ?? "—"} {t("matchup.vs")}{" "}
            {cell.speed.opponent ?? "—"}
            {cell.speed.faster ? (() => {
              const k = optionalAssistKey(`matchup.faster.${cell.speed.faster}`);
              return k ? ` · ${t(k)}` : "";
            })() : ""}
            <button type="button" className="second-btn md-verify" onClick={() => toCalc(cell)}>
              {t("builder.verifyCalc")}
            </button>
          </div>
          </div>
        );
      })}
    </div>
  );
}

/** OppOffenseDto -> the ko-chance shape damageText.ko renders (mirrors MatchupPage). */
function offenseKoChanceDiag(off: NonNullable<CheckRow["cells"]>[number]["offense"]) {
  if (!off) return null;
  return off.koChance ?? {
    text: off.ko,
    n: off.koGuaranteed ?? off.koPossible ?? undefined,
    guaranteed: off.koGuaranteed != null,
  };
}

/** Mega facts (all dex/projection data, client-side): who holds a matching stone, the
 * Mega form's stat/type/ability deltas, and the registered count vs the meta modal. */
/** The team's registered Mega Evolutions: who holds a matching stone, and the form it becomes. */
function useMegaHolders(team: unknown) {
  const dex = useDexByName();
  const items = useItemsByName();
  const members = readTeamMembers(team);
  // A registered Mega arrives in EITHER shape and both count toward the slot budget:
  //   base species + its stone  ('Delphox' + Delphoxite)
  //   the Mega form named directly ('Mega Blastoise' + Blastoisinite)
  // Matching only `mega.baseSpecies === base.name` silently dropped the second: for a member already
  // named 'Mega Blastoise' that comparison is Blastoise === Mega Blastoise, never true — so a team
  // registering two stones reported only one.
  const holders = members.flatMap((m) => {
    const entry = dex.get(m.species);
    if (!entry) return [];
    if (entry.isMega) {
      const base = entry.baseSpecies ? dex.get(entry.baseSpecies) : undefined;
      return [{ member: m.species, base: base ?? entry, mega: entry }];
    }
    if (!m.item) return [];
    const target = items.get(m.item)?.requiredBy?.[0];
    const mega = target ? dex.get(target) : undefined;
    if (!mega || !mega.isMega) return [];
    // `baseSpecies` is the Species Clause GROUPING KEY, not a host pointer. It names the holder
    // only while the key is itself a roster form (Raichu holds Raichunite; Raichu-Alola merely
    // shares the key and holds nothing). Champions has one species whose key is no roster form at
    // all — Mega Floette keys under "Floette" while the roster only has Floette-Eternal — and
    // there the group's own form is the holder. Same rule as the dex (champdex.mega_forms_for).
    const holder = dex.get(mega.baseSpecies ?? "") ? mega.baseSpecies === entry.name
                                                   : mega.baseSpecies === entry.baseSpecies;
    if (!holder) return [];
    return [{ member: m.species, base: entry, mega }];
  });
  return holders;
}

function MegaFacts({ holders }: { holders: ReturnType<typeof useMegaHolders> }) {
  const t = useAssistT();
  const { lang } = useLang();
  if (holders.length === 0) return null;
  const KEYS = ["hp", "atk", "def", "spa", "spd", "spe"] as const;
  const SHORT: Record<string, string> = { hp: "HP", atk: "A", def: "B", spa: "C", spd: "D", spe: "S" };
  return (
    <div className="panel builder-result diag-section">
      <h2>{t("diag.mega")}</h2>
      <p className="muted diag-note">{t("diag.mega.count").replace("{n}", String(holders.length))}</p>
      {holders.map(({ member, base, mega }) => (
        <div key={member} className="gate-detail-row diag-mega-row">
          <SpeciesChips names={[member]} />
          <span className="muted">→</span>
          <SpeciesChips names={[mega.name]} />
          <span className="num diag-mega-stats">
            {KEYS.map((k) => {
              const d = mega.stats[k] - base.stats[k];
              return d ? (
                <span key={k} className={d > 0 ? "prio-plus" : "prio-minus"}>
                  {SHORT[k]}{d > 0 ? `+${d}` : d}{" "}
                </span>
              ) : null;
            })}
          </span>
          {mega.abilities[0] && base.abilities.every((a) => a.name !== mega.abilities[0]!.name) && (
            <span className="muted">
              {t("diag.mega.ability")}: {displayName(mega.abilities[0], lang)}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

/** Archetype-counter surface: for each mainstream meta archetype, the members that can
 * answer it and HOW (priority / rewrites weather / clears terrain / anti-setup / breaks walls ...).
 * Move- and item-based mechanisms are computed client-side (complete — moves don't change on
 * Mega); own weather and terrain setters come from the skill coverage (Mega-aware). Format-gated
 * like roles: singles hides Trick Room / Tailwind / Intimidate; doubles hides Wall-break.
 * Terrain is listed in BOTH formats, mirroring the terrain-control coverage row: a terrain
 * archetype is answered either by overwriting the field or by removing it. */
function ModePressure({ report }: { report: DiagnoseFactsDto }) {
  const t = useAssistT();
  const { lang } = useLang();
  const abilities = useAbilitiesByName();
  const members = readTeamMembers(report.team);
  const dbl = (report.team as { format?: string })?.format === "double";
  const abName = (name: string): string => {
    const e = abilities.get(name);
    return e ? displayName(e, lang) : name;
  };
  type Member = (typeof members)[number];
  type Asset = { species: string; how: string };
  const hasMv = (m: Member, s: Set<string> | string): boolean =>
    (m.moves ?? []).some((mv) => {
      const k = mv.toLowerCase();
      return typeof s === "string" ? k === s : s.has(k);
    });
  const byMove = (s: Set<string> | string, how: string): Asset[] =>
    members.filter((m) => hasMv(m, s)).map((m) => ({ species: m.species, how }));
  // own weather / terrain sources: Mega-aware (Mega Froslass = Snow Warning), so read the skill
  // coverage rather than the declared base ability. A bearer's `via` is a comma-joined source list
  // ("move:Grassy Terrain, ability:Grassy Surge").
  const abilityVia = (via?: string | null): string | null =>
    via?.match(/ability:([^,]+)/)?.[1]?.trim() ?? null;
  const movesVia = (via?: string | null): string[] =>
    (via ?? "").split(", ").filter((part) => part.startsWith("move:"))
      .map((part) => part.slice("move:".length).trim().toLowerCase());
  const coverageBearers = (key: string) =>
    report.roles.coverage.find((c) => c.key === key)?.bearers ?? [];
  const weatherBearers = coverageBearers("weather_rewrite");
  const terrainBearers = coverageBearers("terrain_control");
  const merge = (...lists: Asset[][]): Asset[] => {
    const map = new Map<string, Set<string>>();
    for (const list of lists) {
      for (const a of list) {
        if (!map.has(a.species)) map.set(a.species, new Set());
        map.get(a.species)!.add(a.how);
      }
    }
    return [...map].map(([species, hows]) => ({ species, how: [...hows].join(" / ") }));
  };
  // Grassy Glide is priority 0 in the dex and +1 only on Grassy Terrain, so it answers a speed
  // archetype only while this team can put that terrain up — the same gate diagnose.py applies to
  // the priority_attack role.
  const grassyTerrainOnTeam = terrainBearers.some((b) =>
    /ability:\s*(Grassy Surge|Seed Sower)|move:\s*Grassy Terrain/.test(b.via ?? ""));
  const PRIORITY = new Set(["accelerock", "aqua jet", "bullet punch", "extreme speed",
    "first impression", "ice shard", "jet punch", "mach punch", "quick attack",
    "shadow sneak", "sucker punch", "vacuum wave", "water shuriken",
    ...(grassyTerrainOnTeam ? ["grassy glide"] : [])]);
  const ANTI_SETUP = new Set(["whirlwind", "roar", "dragon tail", "circle throw", "haze",
    "clear smog", "topsy-turvy"]);
  const ANTI_SETUP_ABIL = new Set(["Unaware", "Imposter"]);
  const DISRUPT = new Set(["taunt", "encore", "disable", "quash", "imprison", "torment"]);
  // Defog removes screens as well as terrain in this game (dex: 解除反射壁…场地等状态);
  // Court Change does not break them, it takes them, so it carries its own label.
  const SCREEN_BREAK = new Set(["brick break", "psychic fangs", "raging bull", "defog"]);
  // The dex's complete "removes any terrain" set — used to tell a remover apart from a setter
  // inside the terrain-control coverage row, not to re-derive who is in it.
  const TERRAIN_CLEAR = new Set(["steel roller", "ice spinner", "defog"]);
  const OFF_SETUP = new Set(["swords dance", "dragon dance", "nasty plot", "calm mind",
    "bulk up", "quiver dance", "shell smash", "work up", "coil", "hone claws", "tail glow",
    "growth", "shift gear", "victory dance", "clangorous soul", "no retreat", "belly drum",
    "geomancy"]);
  const TRICK = new Set(["trick", "switcheroo"]);
  const CHOICE = new Set(["Choice Band", "Choice Specs", "Choice Scarf"]);
  const PUNISH = new Set(["Defiant", "Competitive", "Clear Body", "White Smoke",
    "Hyper Cutter", "Mirror Armor", "Guard Dog", "Rattled", "Inner Focus", "Own Tempo",
    "Oblivious", "Scrappy"]);
  const antiSetup = (): Asset[] => merge(
    byMove(ANTI_SETUP, t("diag.how.antiSetup")),
    members.filter((m) => m.ability && ANTI_SETUP_ABIL.has(m.ability))
      .map((m) => ({ species: m.species, how: t("diag.how.antiSetup") })));
  const choiceTrick = (): Asset[] =>
    members.filter((m) => m.item && CHOICE.has(m.item) && hasMv(m, TRICK))
      .map((m) => ({ species: m.species, how: t("diag.how.choiceTrick") }));
  const slowFirst = [...report.speed.order].reverse();
  const allRows: Record<string, { assets: Asset[]; extra?: ReactNode }> = {
    trickroom: {
      assets: merge(byMove(PRIORITY, t("diag.how.priority")), byMove(DISRUPT, t("diag.how.disrupt")),
        byMove("trick room", t("diag.how.ownTR"))),
      extra: slowFirst[0]
        ? <>{t("diag.mode.slowest")} <SpeciesChips
            names={slowFirst.slice(0, 2).map((o) => o.species)} /></>
        : undefined,
    },
    tailwind: {
      assets: merge(byMove(PRIORITY, t("diag.how.priority")), byMove("tailwind", t("diag.how.ownTailwind")),
        byMove("trick room", t("diag.how.ownTR")), byMove("taunt", t("diag.how.taunt"))),
    },
    weather: {
      assets: weatherBearers.map((b) => ({
        species: b.species,
        how: abilityVia(b.via) ? abName(abilityVia(b.via)!) : t("diag.how.weather"),
      })),
    },
    terrain: {
      // The coverage row already unions setting moves, removal moves and surge abilities, so it is
      // the whole answer. A terrain ability brings its own on-entry terrain — naming the ability
      // says everything the member's terrain move would repeat.
      assets: terrainBearers.map((b) => {
        const ability = abilityVia(b.via);
        if (ability) return { species: b.species, how: abName(ability) };
        const moves = movesVia(b.via);
        const how = [...new Set(moves.map((mv) =>
          TERRAIN_CLEAR.has(mv) ? t("diag.how.clearTerrain") : t("diag.how.terrain")))];
        return { species: b.species, how: how.join(" / ") || t("diag.how.terrain") };
      }),
    },
    intimidate: {
      assets: members.filter((m) => m.ability && PUNISH.has(m.ability))
        .map((m) => ({ species: m.species, how: abName(m.ability!) })),
    },
    screens: {
      assets: merge(byMove(SCREEN_BREAK, t("diag.how.breakScreen")),
        byMove("court change", t("diag.how.courtChange")), byMove(DISRUPT, t("diag.how.disrupt"))),
    },
    setup: {
      assets: merge(antiSetup(), byMove("encore", t("diag.how.encore")), byMove("taunt", t("diag.how.taunt"))),
    },
    break: {
      assets: merge(byMove(OFF_SETUP, t("diag.how.offSetup")), byMove("toxic", t("diag.how.toxic")),
        byMove("encore", t("diag.how.encore")), byMove("taunt", t("diag.how.taunt")), choiceTrick(),
        byMove("psychic noise", t("diag.how.psychicNoise"))),
    },
  };
  const order = dbl
    ? ["trickroom", "tailwind", "weather", "terrain", "intimidate", "screens", "setup"]
    : ["weather", "terrain", "screens", "setup", "break"];
  return (
    <div className="panel builder-result diag-section wide">
      <h2>{t("diag.modes")}</h2>
      <p className="muted diag-note">{t("diag.modes.hint")}</p>
      <table className="data-table diag-table">
        <tbody>
          {order.map((key) => {
            const r = allRows[key]!;   // order only lists keys present in allRows
            const descKey = optionalAssistKey(`diag.modeDesc.${key}`);
            return (
              <tr key={key}>
                <td><span className={descKey ? "diag-label-info" : undefined}
                          title={descKey ? t(descKey) : undefined}>
                  {t(`diag.mode.${key}` as Parameters<typeof t>[0])}</span></td>
                <td>
                  {r.assets.length
                    ? <span className="diag-mode-assets">
                        {r.assets.map((a) => (
                          <span key={a.species} className="diag-mode-asset">
                            <SpeciesChips names={[a.species]} />
                            <span className="diag-mode-how">{a.how}</span>
                          </span>
                        ))}
                      </span>
                    : <span className="muted">{t("diag.mode.none")}</span>}
                  {r.extra && <div className="muted diag-fragile">{r.extra}</div>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ReportView({ report }: { report: DiagnoseFactsDto }) {
  const t = useAssistT();
  const navigate = useNavigate();
  const prose = useProseRenderer();
  const memberProse = useMemberProse(report.team);
  const memberOf = useMemberSets(report.team);
  const teamFormat = (report.team as { format?: string })?.format === "double" ? "double" : "single";
  const [selOpp, setSelOpp] = useState<string | null>(null);
  const [showAllChecks, setShowAllChecks] = useState(false);
  const valid = report.legality.status === "valid";
  const unknown = report.legality.status === "unknown";
  const weakRows = report.defense.byAttackType.filter((e) => e.weak.length > 0);
  const calmTypes = report.defense.byAttackType.length - weakRows.length;
  const issues = report.legality.issues ?? [];
  const errorIssues = issues.filter((i) => i.severity === "error");
  const noticeIssues = issues.filter((i) => i.severity !== "error");
  const bare = speciesOnlyCount(report.team);
  const megaHolders = useMegaHolders(report.team);
  const allCheckRows = report.checks?.byOpponent ?? [];
  const visibleCheckRows = showAllChecks ? allCheckRows : allCheckRows.slice(0, 10);
  const checkGradeCounts = allCheckRows.reduce<Record<"C2" | "C1" | "C0", number>>(
    (counts, row) => {
      const grade = row.observedFloor;
      if (grade === "C2" || grade === "C1" || grade === "C0") counts[grade] += 1;
      return counts;
    }, { C2: 0, C1: 0, C0: 0 });
  useEffect(() => {
    setSelOpp(null);
    setShowAllChecks(false);
  }, [report]);
  const issueText = (issue: (typeof issues)[number]) => {
    const key = optionalAssistKey(`validation.${issue.code}`);
    return key ? fillTemplate(t(key), issue.params) : t("diag.warningGeneric");
  };
  const signalText = (signal: string) => {
    // item/ability signals carry a functional MEANING (the skill's signal maps) — show it,
    // or the row reads like a random equipment listing (user-reported confusion).
    if (signal.startsWith("item:") || signal.startsWith("ability:")) {
      const isItem = signal.startsWith("item:");
      const value = signal.slice(isItem ? 5 : 8);
      const slug = value.toLowerCase().replace(/[^a-z0-9]+/g, "-");
      const meaning = optionalAssistKey(`diag.sig.${slug}`);
      const label = prose(value);
      return meaning
        ? <>{label}<span className="diag-sig-meaning">（{t(meaning)}）</span></>
        : prose(t(isItem ? "diag.signal.item" : "diag.signal.ability").replace("{value}", value));
    }
    const key = optionalAssistKey(`diag.cov.${signal}`);
    return key ? t(key) : signal;
  };
  const bearerHow = (raw: string | null | undefined) => {
    if (!raw) return null;
    return raw.split(", ").map((part, i) => {
      let label;
      if (part === "move") {
        label = t("diag.via.move");
      } else if (part.startsWith("move:")) {
        label = prose(t("diag.signal.move").replace("{value}", part.slice(5)));
      } else if (part.startsWith("item:")) {
        label = prose(t("diag.signal.item").replace("{value}", part.slice(5)));
      } else if (part.startsWith("ability:")) {
        label = prose(t("diag.signal.ability").replace("{value}", part.slice(8)));
      } else {
        label = prose(part);
      }
      return <span key={`${part}-${i}`}>{i > 0 ? " · " : ""}{label}</span>;
    });
  };
  return (
    <MemberSets.Provider value={memberOf}>
      <div className="panel result-panel builder-result">
        {bare.some && (
          <p className="notice diag-partial">{t("diag.partial")}</p>
        )}
        {errorIssues.length > 0 ? (
          <ul className="diag-errors">
            {errorIssues.map((issue, i) => <li key={i}>{prose(issueText(issue))}</li>)}
          </ul>
        ) : report.legality.errors.length > 0 && (
          <p className="notice diag-errors">{t("diag.legality.invalid")}</p>
        )}
        {(noticeIssues.length > 0 || (issues.length === 0 && report.legality.warnings.length > 0)) && (
          <ul className="diag-warnings">
            {noticeIssues.length > 0
              ? noticeIssues.map((issue, i) => <li key={i}>{prose(issueText(issue))}</li>)
              : <li>{t("diag.warningGeneric")}</li>}
          </ul>
        )}
        <TeamCard team={report.team} keepOrigin="diagnose" badges={
          <div className="builder-badges">
            <span className={valid ? "badge-ok" : unknown ? "badge-note" : "badge-err"}>
              {valid ? "✓ " : unknown ? "? " : "✗ "}
              {t(`diag.legality.${valid ? "valid" : unknown ? "unknown" : "invalid"}`)}
            </span>
          </div>
        } actions={<>
          <HintButton hint={t("team.sendCalcHint")}
            onClick={() => {
              stashCalcTeams({ format: teamFormat,
                attackers: teamToCalcMembers(report.team), defenders: [] });
              navigate("/calc?tab=damage");
            }}>{t("team.sendCalc")}</HintButton>
          <HintButton hint={t("actual.sendMatchupHint")} onClick={() => {
            stashMatchupFill({ source: "diagnose", label: t("actual.source.diagnose"),
              format: teamFormat, team: report.team });
            navigate("/matchup?mode=actual");
          }}>{t("actual.sendMatchup")}</HintButton>
        </>} />
      </div>
      <Summary report={report} />
      <div className="diag-grid">

      {/* Opt-in LLM reading — a decorated aside; the deterministic report stands alone. */}
      {report.explanation && (
        <div className="panel builder-result diag-section diag-explain wide">
          <h2>{t("diag.explanation")}</h2>
          {report.explanation.split(/\n+/).map((para, i) => (
            <p key={i} className="qa-answer-text">{memberProse(para)}</p>
          ))}
        </div>
      )}
      {report.explanationError === "budget_exhausted" && (
        <p className="notice" role="status">{t("budget.explainPaused")}</p>
      )}
      {report.explanationError && report.explanationError !== "budget_exhausted" && (
        <p className="notice qa-error">
          {t(report.explanationError === "rate_limited"
            ? "diag.explainError.limit" : "diag.explainError.generic")}
        </p>
      )}

      {report.checks && (
        <div className="panel builder-result diag-section wide">
          <h2>{t("diag.checks").replace("{k}", String(report.checks.topK))}</h2>
          <div className="diag-check-overview" aria-label={t("diag.checks.summary")}>
            {(["C2", "C1", "C0"] as const).map((grade) => (
              <span key={grade} className={`diag-check-count diag-grade-${grade}`}>
                {grade} <span className="num">{checkGradeCounts[grade]}</span>
              </span>
            ))}
            <span className="muted diag-check-shown">
              {t("diag.checks.shown")
                .replace("{shown}", String(visibleCheckRows.length))
                .replace("{total}", String(allCheckRows.length))}
            </span>
            {allCheckRows.length > 10 && (
              <button type="button" className="diag-check-toggle" aria-expanded={showAllChecks}
                onClick={() => {
                  if (showAllChecks && selOpp
                      && !allCheckRows.slice(0, 10).some((row) => row.opponent === selOpp)) {
                    setSelOpp(null);
                  }
                  setShowAllChecks((value) => !value);
                }}>
                {t(showAllChecks ? "diag.checks.showTop" : "diag.checks.showAll")
                  .replace("{total}", String(allCheckRows.length))}
                <svg viewBox="0 0 12 12" aria-hidden><path d="M3 4.5 6 7.5 9 4.5" /></svg>
              </button>
            )}
          </div>
          <table className="data-table diag-table diag-checks-table">
            <colgroup>
              <col className="diag-check-opponent" />
              <col className="diag-check-grade-col" />
              <col className="diag-check-members" />
            </colgroup>
            <thead>
              <tr>
                <th>{t("diag.checks.opponent")}</th>
                <th>{t("diag.checks.grade")}</th>
                <th>{t("diag.checks.by")}</th>
              </tr>
            </thead>
            <tbody>
              {visibleCheckRows.map((row) => {
                const grade = row.observedFloor || "?";
                const clickable = (row.cells?.length ?? 0) > 0;
                const on = selOpp === row.opponent;
                return (
                  <tr key={row.opponent}
                      className={`${clickable ? "diag-row-click" : ""}${on ? " on" : ""}`}
                      onClick={clickable
                        ? () => setSelOpp(on ? null : row.opponent) : undefined}>
                    <td><SpeciesChips names={[row.opponent]} opponent /></td>
                    <td>
                      <span className={`diag-grade diag-grade-${grade}`}>{grade}</span>
                      {!row.calculationComplete && (
                        <span className="muted"> · {t("diag.checks.incomplete")}</span>
                      )}
                    </td>
                    <td>
                      <SpeciesChips names={row.floorBy} />
                      {row.representedCoverage != null && (
                        <span className="muted num"> · {(row.representedCoverage * 100).toFixed(1)}%</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {selOpp && (() => {
            const row = report.checks!.byOpponent.find((r) => r.opponent === selOpp);
            const fmt = (report.team as { format?: string })?.format === "double"
              ? "double" as const : "single" as const;
            return row ? <CheckRowDetail row={row} team={report.team} format={fmt} /> : null;
          })()}
        </div>
      )}

      <div className="panel builder-result diag-section wide">
        <h2>{t("diag.defense")}</h2>
        <table className="data-table diag-table diag-defense">
          <tbody>
            {[...weakRows].sort((a, b) => b.weak.length - a.weak.length).map((row) => (
              <tr key={row.type} className={row.weak.length >= 3 ? "diag-weak-3" : row.weak.length === 2 ? "diag-weak-2" : undefined}>
                <td><TypeBadge type={row.type} /></td>
                <td><span className="diag-weak-count num">×{row.weak.length}</span></td>
                <td><SpeciesChips names={row.weak} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {calmTypes > 0 && (
          <p className="muted diag-note">
            {t("diag.noWeak").replace("{n}", String(calmTypes))}
          </p>
        )}
      </div>

      {/* Offense and roles read moves; on a species-only parse they would render as a
          wall of dashes — drop them and let the partial notice carry the message. */}
      {/* Offense and its Mega registrations on the left, the speed order on the right: two columns
          whose last cards stretch so both end level. Without either left card the speed takes the row. */}
      {(!bare.all || megaHolders.length > 0) ? (
        <div className="diag-pair wide">
          <div className="diag-col">
      {!bare.all && <div className="panel builder-result diag-section">
              <h2>{t("diag.offense")}</h2>
              {report.offense.incompleteMembers.length > 0 && (
                <p className="notice diag-unconfirmed diag-incomplete">
                  {t("diag.gapsUnconfirmed")}{" "}
                  <SpeciesChips names={report.offense.incompleteMembers.map((m) => m.species)} />
                </p>
              )}
              <div className="gate-detail-row">
                <span className="gate-detail-label">{t("diag.hardGaps")}</span>
                {report.offense.hardGaps.length > 0
                  ? <span className="gate-detail-chips">
                      {report.offense.hardGaps.map((tp) => <TypeBadge key={tp} type={tp} />)}
                    </span>
                  : <span className="muted">—</span>}
              </div>
              <div className="gate-detail-row">
                <span className="gate-detail-label">{t("diag.stab")}</span>
                <span className="gate-detail-chips">
                  {report.offense.stabTypes.map((tp) => <TypeBadge key={tp} type={tp} />)}
                </span>
              </div>
              {report.offense.otherTypes.length > 0 && (
                <div className="gate-detail-row">
                  <span className="gate-detail-label">{t("diag.other")}</span>
                  <span className="gate-detail-chips">
                    {report.offense.otherTypes.map((tp) => <TypeBadge key={tp} type={tp} />)}
                  </span>
                </div>
              )}
            </div>}
            <MegaFacts holders={megaHolders} />
          </div>
          <div className="diag-col">
            <div className="panel builder-result diag-section">
              <h2>{t("diag.speed")}</h2>
              <ol className="diag-speed">
                {(() => {
                  const top = Math.max(1, ...report.speed.order.map((o) => o.speed ?? 0));
                  return report.speed.order.map((o, i) => (
                    <li key={o.species} style={{ "--w": `${((o.speed ?? 0) / top) * 100}%`, "--n": i } as CSSProperties}>
                      <SpeciesChips names={[o.species]} />
                      <span className="diag-speed-bar" aria-hidden="true"><i /></span>
                      <span className="num diag-speed-value">{o.speed ?? "?"}</span>
                    </li>
                  ));
                })()}
              </ol>
              {report.speed.members.some((m) => m.assumedNeutral) && (
                <p className="muted diag-note">{t("diag.assumedNeutral")}</p>
              )}
            </div>
          </div>
        </div>
      ) : (
      <div className="panel builder-result diag-section wide">
        <h2>{t("diag.speed")}</h2>
        <ol className="diag-speed">
          {(() => {
            const top = Math.max(1, ...report.speed.order.map((o) => o.speed ?? 0));
            return report.speed.order.map((o, i) => (
              <li key={o.species} style={{ "--w": `${((o.speed ?? 0) / top) * 100}%`, "--n": i } as CSSProperties}>
                <SpeciesChips names={[o.species]} />
                <span className="diag-speed-bar" aria-hidden="true"><i /></span>
                <span className="num diag-speed-value">{o.speed ?? "?"}</span>
              </li>
            ));
          })()}
        </ol>
        {report.speed.members.some((m) => m.assumedNeutral) && (
          <p className="muted diag-note">{t("diag.assumedNeutral")}</p>
        )}
      </div>
      )}

      {/* Archetype-counter surface sits directly before role coverage — the two role/
          coverage views read together. */}
      <ModePressure report={report} />

      {!bare.all && <div className="panel builder-result diag-section wide">
        <h2>{t("diag.roles")}</h2>
        {report.roles.incompleteMembers.length > 0 && (
          <p className="notice diag-unconfirmed diag-incomplete">
            {t("diag.rolesUncounted")}{" "}
            <SpeciesChips names={report.roles.incompleteMembers.map((m) => m.species)} />
          </p>
        )}
        {report.roles.attentionCalibration?.status === "carried_over" && (
          <p className="notice diag-unconfirmed">
            {t("diag.rolesCarriedOver")}{" "}
            {report.roles.attentionCalibration.measuredOn} &rarr;{" "}
            {report.roles.attentionCalibration.rule},{" "}
            {report.roles.attentionCalibration.expiresAt}
          </p>
        )}
        <table className="data-table diag-table diag-coverage-table">
          <tbody>
            {report.roles.coverage.map((c) => {
              const key = optionalAssistKey(`diag.cov.${c.key}`);
              const descKey = optionalAssistKey(`diag.covDesc.${c.key}`);
              // Public attention levels deliberately avoid the skill's legacy
              // required|optional wording. Both kinds of absence stay visible; color shows how
              // strongly the user should weigh the missing signal.
              const tier = c.expectation === "situational" ? "situational" : "priority";
              return (
                <tr key={c.key} className={c.present ? "" : `diag-missing-${tier}`}>
                  <td>{c.present ? "✓" : "✗"}</td>
                  <td>
                    <span className={`diag-tier diag-tier-${tier}`}>{t(`diag.tier.${tier}`)}</span>{" "}
                    <span className={descKey ? "diag-label-info" : undefined}
                          title={descKey ? t(descKey) : undefined}>{key ? t(key) : c.label}</span>
                  </td>
                  <td>
                    {c.bearers.length > 0
                      ? <span className="diag-mode-assets">
                          {c.bearers.map((b) => (
                            <span key={`${c.key}-${b.species}`} className="diag-mode-asset">
                              <SpeciesChips names={[b.species]} />
                              {b.via && <span className="diag-mode-how">{bearerHow(b.via)}</span>}
                            </span>
                          ))}
                        </span>
                      : <span className="muted">—</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="diag-signals">
          <p className="muted diag-note">{t("diag.compressionHint")}</p>
          {report.roles.members.map((m) => (
            <div key={m.species} className="gate-detail-row">
              <SpeciesChips names={[m.species]} />
              <span className="muted">{m.signals.length
                ? m.signals.map((signal, i) => <span key={`${signal}-${i}`}>{i > 0 ? " · " : ""}{signalText(signal)}</span>)
                : "—"}</span>
            </div>
          ))}
        </div>
      </div>}
      </div>
    </MemberSets.Provider>
  );
}

/** The report's headline numbers, each a tile: legality, answers to the metagame, shared weaknesses,
 * coverage gaps, speed range and role coverage. Every number is the report's own; the tiles only
 * count what the sections below list. */
function Summary({ report }: { report: DiagnoseFactsDto }) {
  const t = useAssistT();
  const issues = report.legality.issues ?? [];
  const valid = report.legality.status === "valid";
  const unknown = report.legality.status === "unknown";
  const rows = report.checks?.byOpponent ?? [];
  const grades = rows.reduce<Record<"C2" | "C1" | "C0", number>>((acc, row) => {
    if (row.observedFloor === "C2" || row.observedFloor === "C1" || row.observedFloor === "C0") acc[row.observedFloor] += 1;
    return acc;
  }, { C2: 0, C1: 0, C0: 0 });
  const shared = report.defense.byAttackType.filter((row) => row.weak.length >= 2)
    .sort((a, b) => b.weak.length - a.weak.length);
  const speeds = report.speed.order.map((o) => o.speed).filter((s): s is number => typeof s === "number");
  const present = report.roles.coverage.filter((c) => c.present).length;
  return (
    <section className="diag-summary" aria-label={t("diag.sum.legality")}>
      <div className={`diag-tile ${valid ? "good" : unknown ? "" : "bad"}`}>
        <span className="diag-tile-label">{t("diag.sum.legality")}</span>
        <b className="diag-tile-value">{valid ? t("diag.sum.ok") : t(`diag.legality.${unknown ? "unknown" : "invalid"}`)}</b>
        <span className="diag-tile-sub num">{issues.length ? t("diag.sum.issues").replace("{n}", String(issues.length)) : "—"}</span>
      </div>
      {rows.length > 0 && (
        <div className="diag-tile">
          <span className="diag-tile-label">{t("diag.sum.checks")}</span>
          <b className="diag-tile-value num">{grades.C2 + grades.C1}<small>{t("diag.sum.of").replace("{n}", String(rows.length))}</small></b>
          <span className="diag-grades" aria-hidden="true">
            {(["C2", "C1", "C0"] as const).map((g) => (
              <i key={g} className={`diag-grade-${g}`} style={{ flexGrow: grades[g] }} />
            ))}
          </span>
          <span className="diag-tile-sub num">C2 {grades.C2} · C1 {grades.C1} · C0 {grades.C0}</span>
        </div>
      )}
      <div className={`diag-tile${shared.length >= 3 ? " bad" : shared.length ? " warn" : " good"}`}>
        <span className="diag-tile-label">{t("diag.sum.weak")}</span>
        <b className="diag-tile-value num">{shared.length ? t("diag.sum.types").replace("{n}", String(shared.length)) : t("diag.sum.weakNone")}</b>
        <span className="diag-tile-types">{shared.slice(0, 4).map((row) => <TypeBadge key={row.type} type={row.type} iconOnly />)}</span>
      </div>
      <div className={`diag-tile${report.offense.hardGaps.length ? " warn" : " good"}`}>
        <span className="diag-tile-label">{t("diag.sum.gaps")}</span>
        <b className="diag-tile-value num">{report.offense.hardGaps.length
          ? t("diag.sum.types").replace("{n}", String(report.offense.hardGaps.length)) : t("diag.sum.weakNone")}</b>
        <span className="diag-tile-types">{report.offense.hardGaps.slice(0, 4).map((tp) => <TypeBadge key={tp} type={tp} iconOnly />)}</span>
      </div>
      {speeds.length > 0 && (
        <div className="diag-tile">
          <span className="diag-tile-label">{t("diag.sum.speed")}</span>
          <b className="diag-tile-value num">{Math.min(...speeds)}–{Math.max(...speeds)}</b>
        </div>
      )}
      <div className="diag-tile">
        <span className="diag-tile-label">{t("diag.sum.roles")}</span>
        <b className="diag-tile-value num">{present}<small>{t("diag.sum.of").replace("{n}", String(report.roles.coverage.length))}</small></b>
        <span className="diag-meter" aria-hidden="true"><i style={{ width: `${report.roles.coverage.length ? present / report.roles.coverage.length * 100 : 0}%` }} /></span>
      </div>
    </section>
  );
}
