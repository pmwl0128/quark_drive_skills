/** The reader's bookkeeping for one session: a display title, archiving, deleting. None of it touches
 * the ledger, so it never disturbs an assistant's or the panel's revision. */
import type { SessionDto } from "@pokemon-champions/protocol";
import { IconArchive, IconArchiveOff, IconPencil, IconTrash } from "@tabler/icons-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Modal } from "../../components/Modal.tsx";
import { useToast } from "../../components/Toast.tsx";
import type { SessionApi } from "../../runtime/adapter.ts";
import { useUepT } from "./messages.ts";

export function SessionActions({ sessions, session, onChanged }: {
  sessions: SessionApi; session: SessionDto; onChanged: () => void;
}) {
  const t = useUepT();
  const toast = useToast();
  const navigate = useNavigate();
  const [dialog, setDialog] = useState<"rename" | "delete" | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (action: () => Promise<unknown>, after?: () => void) => {
    setBusy(true);
    try { await action(); setDialog(null); after ? after() : onChanged(); }
    catch (e) { console.error("Session action failed:", e); toast({ text: t("uep.actionError") }); }
    finally { setBusy(false); }
  };
  return <div className="session-actions" role="group" aria-label={t("uep.actions")}>
    <button type="button" className="ghost-btn" onClick={() => { setTitle(session.title ?? ""); setDialog("rename"); }}>
      <IconPencil size={15} aria-hidden />{t("uep.rename")}</button>
    <button type="button" className="ghost-btn" disabled={busy}
      onClick={() => void run(() => sessions.archive(session.id, !session.archived))}>
      {session.archived ? <IconArchiveOff size={15} aria-hidden /> : <IconArchive size={15} aria-hidden />}
      {t(session.archived ? "uep.unarchive" : "uep.archive")}</button>
    <button type="button" className="ghost-btn session-delete" onClick={() => setDialog("delete")}>
      <IconTrash size={15} aria-hidden />{t("uep.delete")}</button>
    {dialog === "rename" ? <Modal title={t("uep.rename")} width={460} onClose={() => setDialog(null)}
      footer={<>
        <button type="button" className="second-btn" onClick={() => setDialog(null)}>{t("uep.cancel")}</button>
        <button type="submit" form="session-rename" className="primary-btn" disabled={busy}>{t("uep.save")}</button>
      </>}>
      <form id="session-rename" onSubmit={(event) => { event.preventDefault(); void run(() => sessions.rename(session.id, title.trim() || null)); }}>
        <input className="session-rename-input" value={title} maxLength={200} placeholder={t("uep.renamePh")}
          aria-label={t("uep.rename")} onChange={(event) => setTitle(event.target.value)} />
      </form>
    </Modal> : null}
    {dialog === "delete" ? <Modal title={t("uep.deleteTitle")} width={440} onClose={() => setDialog(null)}
      footer={<>
        <button type="button" className="second-btn" data-autofocus onClick={() => setDialog(null)}>{t("uep.cancel")}</button>
        <button type="button" className="primary-btn console-danger" disabled={busy}
          onClick={() => void run(() => sessions.remove(session.id), () => { toast({ text: t("uep.deleted") }); navigate("/sessions"); })}>
          {t("uep.delete")}</button>
      </>}>
      <p className="session-delete-body">{t("uep.deleteBody")}</p>
    </Modal> : null}
  </div>;
}
