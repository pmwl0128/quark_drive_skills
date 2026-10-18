/** The library's current team as the Console pages use it: its record, its assembled TeamDoc and
 * its display name, plus every team newest first for the pickers. */
import { useMemo } from "react";
import { useDexByName } from "../../hooks.ts";
import { useLang } from "../../i18n.ts";
import { useLibrary } from "../../lib/library/hooks.ts";
import { boxById, teamDocOf } from "../../lib/library/repo.ts";
import { useLibraryWorkspace } from "../../lib/library/workspace.tsx";
import { useTeamLabel } from "../library/shared.tsx";

export function useActiveTeam() {
  const { lang } = useLang();
  const library = useLibrary();
  const dex = useDexByName();
  const label = useTeamLabel(dex, lang);
  const { active, setActive } = useLibraryWorkspace("active", "setActive");
  const state = library && library !== "unavailable" ? library : null;
  const box = useMemo(() => state ? boxById(state) : new Map(), [state]);
  const teams = useMemo(() => state ? [...state.teams].sort((a, b) => b.updatedAt - a.updatedAt) : [], [state]);
  const record = teams.find((team) => team.id === active?.id) ?? null;
  const doc = record ? teamDocOf(record, box) : null;
  const name = record ? label(record.name, doc).text : "";
  return { library, dex, label, box, teams, record, doc, name, setActive };
}
