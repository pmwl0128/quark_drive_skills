import { useLibraryT } from "./messages.ts";
import "./styles.team-import.css";

/** An explicit replacement decision before a whole-team import starts. */
export function ReplaceTeamNotice({ onConfirm, onCancel }: {
  onConfirm: () => void; onCancel: () => void;
}) {
  const t = useLibraryT();
  return <div className="team-replace-notice" role="group" aria-label={t("lib.import.replaceTitle")}>
    <p>{t("lib.import.replaceBody")}</p>
    <div className="team-replace-actions">
      <button type="button" className="second-btn" onClick={onCancel}>{t("lib.cancel")}</button>
      <button type="button" className="primary-btn" onClick={onConfirm}>{t("lib.import.replaceConfirm")}</button>
    </div>
  </div>;
}
