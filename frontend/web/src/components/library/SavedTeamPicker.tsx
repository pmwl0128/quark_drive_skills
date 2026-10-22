/** Loaded on demand: importing a saved team shares the dock's canonical receiver path. */
import type { FormatId } from "@pokemon-champions/protocol";
import { useLibraryWorkspace } from "../../lib/library/workspace.tsx";
import { roveFocus } from "../../lib/popover.ts";
import { useLibraryT } from "./messages.ts";
import TeamImportList from "./TeamImportList.tsx";

export default function SavedTeamPicker({ format, receiverId, title, onClose, replaceCurrent = false }: {
  format: FormatId;
  receiverId: string;
  title: string;
  onClose: () => void;
  replaceCurrent?: boolean;
}) {
  const t = useLibraryT();
  const { receivers, deliver } = useLibraryWorkspace("receivers", "deliver");
  return <>
    <div className="saved-team-heading">
      <b>{title}</b>
      <button type="button" className="saved-team-close" onClick={onClose} autoFocus aria-label={t("common.close")}>×</button>
    </div>
    <div className="saved-team-tools">
      <span className="muted">{t("library.importReplace")}</span>
    </div>
    <div className="saved-team-list" role="menu" aria-label={t("library.importSaved")}
      onKeyDown={event => { if (!(event.target instanceof HTMLInputElement)) roveFocus(event, "[role=menuitem]"); }}>
      <TeamImportList format={format} autoFocus initialLimit={5} replaceCurrent={replaceCurrent} onPick={(doc, name) => {
        const receiver = receivers.find(candidate => candidate.id === receiverId && candidate.kind === "team");
        if (receiver && deliver(receiver, { kind: "team", doc, name }).ok) onClose();
      }} />
    </div>
  </>;
}
