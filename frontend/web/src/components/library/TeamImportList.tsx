/** The reader's saved teams of one format as a pick list: a page that works on one whole team (the
 * actual-sets matchup) takes one straight from the library without opening the dock. Loaded only when
 * the list opens, so the page itself never pays for the library store. */
import type { FormatId, TeamDoc } from "@pokemon-champions/protocol";
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useDexByName } from "../../hooks.ts";
import { useLang } from "../../i18n.ts";
import { useLibrary } from "../../lib/library/hooks.ts";
import { boxById, teamDocOf } from "../../lib/library/repo.ts";
import { fill, useLibraryT } from "./messages.ts";
import { SpriteRow, useTeamLabel } from "./shared.tsx";
import "./styles.team-import.css";
import { TeamCodeImport } from "./TeamCodeImport.tsx";

export default function TeamImportList(props: Parameters<typeof SavedTeams>[0]) {
  return <div className="team-import-list"><TeamCodeImport format={props.format} replaceCurrent={props.replaceCurrent}
    onPick={doc => props.onPick(doc, "")} /><SavedTeams {...props} /></div>;
}

function SavedTeams({ format, onPick, autoFocus = false, initialLimit = Infinity }: {
  format: FormatId;
  onPick: (doc: TeamDoc, name: string) => void;
  autoFocus?: boolean;
  initialLimit?: number;
  replaceCurrent?: boolean;
}) {
  const t = useLibraryT();
  const { lang } = useLang();
  const dex = useDexByName();
  const teamLabel = useTeamLabel(dex, lang);
  const state = useLibrary();
  const list = useRef<HTMLDivElement>(null);
  const listId = useId();
  const [expanded, setExpanded] = useState(false);
  const ready = state !== undefined && state !== "unavailable";
  useEffect(() => setExpanded(false), [format, initialLimit]);
  useEffect(() => {
    if (autoFocus) list.current?.querySelector<HTMLElement>("button:not(:disabled), a")?.focus();
  }, [autoFocus, ready, format]);
  if (state === undefined) return <p className="team-import-note muted">{t("lib.loading")}</p>;
  if (state === "unavailable") return <p className="team-import-note muted">{t("lib.unavailable")}</p>;
  const byId = boxById(state);
  const teams = state.teams
    .filter((team) => team.format === format)
    .sort((a, b) => a.slot - b.slot)
    .flatMap((team) => {
      const doc = teamDocOf(team, byId);
      return doc ? [{ team, doc }] : [];
    });
  if (!teams.length) {
    return (
      <p className="team-import-note muted">
        {fill(t("lib.import.none"), { format: t(`format.${format}`) })}
        {" "}<Link to="/teams">{t("dock.openPage")} →</Link>
      </p>
    );
  }
  return (
    <div ref={list} className="team-import-choices">
      <div id={listId}>
      {teams.slice(0, expanded ? undefined : initialLimit).map(({ team, doc }) => {
        const label = teamLabel(team.name, doc);
        return (
          <button key={team.id} type="button" role="menuitem" className="team-import-row" onClick={() => onPick(doc, label.text)}>
            <span className="team-import-no num">{team.slot + 1}</span>
            <span className="team-import-main">
              <span className={`team-import-name${label.auto ? " auto" : ""}`}>{label.text}</span>
              <SpriteRow species={doc.pokemon.map((mon) => mon.species)} dex={dex} lang={lang}
                         className="team-import-sprites" />
            </span>
            <span className="team-import-count num">{doc.pokemon.length}/6</span>
          </button>
        );
      })}
      </div>
      {teams.length > initialLimit && <button type="button" className="team-import-more" role="menuitem"
        aria-expanded={expanded} aria-controls={listId}
        onClick={() => setExpanded(!expanded)}>
        {expanded ? t("lib.import.less") : fill(t("lib.import.more"), { n: teams.length - initialLimit })}
      </button>}
    </div>
  );
}
