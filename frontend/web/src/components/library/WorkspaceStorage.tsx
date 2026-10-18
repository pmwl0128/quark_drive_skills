import { useEffect, useState } from "react";
import { IconFolderOpen } from "@tabler/icons-react";
import { useLang } from "../../i18n.ts";
import { useRuntime } from "../../runtime/context.tsx";
import { HttpError } from "../../runtime/adapter.ts";
import { flushStorageWrites, storageChanged } from "../../lib/userStorage.ts";
import { useWorkspaceT } from "./workspaceMessages.ts";

export function WorkspaceStorage() {
  const { adapter } = useRuntime();
  const api = adapter.storage;
  const t = useWorkspaceT();
  const { lang } = useLang();
  const [current, setCurrent] = useState("");
  const [pendingPath, setPendingPath] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => { void api?.info().then(info => setCurrent(info.path), () => setMessage(t("error"))); }, [api, t]);
  const errorMessage = (error: unknown) => {
    const code = error instanceof HttpError ? error.code : "";
    if (code === "workspace_changed" || code === "library_conflict") return t("conflict");
    if (code === "target_not_empty" || code === "workspace_in_use" || code === "absolute_path_required" || code === "workspace_file_unreadable") return t(code);
    if (code === "folder_picker_unavailable" || code === "folder_picker_busy" || code === "workspace_busy") return t(code);
    return t("error");
  };
  const change = async (path: string, migrate: boolean) => {
    await flushStorageWrites();
    await api!.switch(path, migrate);
    window.location.reload();
  };
  const choose = async () => {
    if (!api || busy) return;
    setBusy(true); setMessage(""); setPendingPath(null);
    try {
      await flushStorageWrites();
      const path = await api.pickFolder(lang);
      if (!path || path === current) return;
      try { await change(path, true); }
      catch (error) {
        if (error instanceof HttpError && error.code === "target_not_empty") setPendingPath(path);
        else throw error;
      }
    } catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(false); }
  };
  const openExisting = async () => {
    if (!pendingPath || busy) return;
    setBusy(true); setMessage("");
    try { await change(pendingPath, false); }
    catch (error) { setMessage(errorMessage(error)); setBusy(false); }
  };
  const migrateBrowser = async () => {
    if (!api || busy) return;
    setBusy(true); setMessage("");
    try {
      await flushStorageWrites();
      const document = await api.library();
      if (document.teams.length || document.box.length || (await api.records()).length) { setMessage(t("legacyNotEmpty")); return; }
      const { default: Dexie } = await import("dexie");
      let count = 0;
      let snapshot: Pick<typeof document, "teams" | "box" | "layout"> = { teams: [], box: [], layout: null };
      let records: unknown[] = [];
      if (await Dexie.exists("pokemon-champions-library")) {
        const { libraryDb } = await import("../../lib/library/db.ts");
        const db = libraryDb();
        snapshot = await db.transaction("r", db.teams, db.box, db.meta, async () => ({
          teams: await db.teams.toArray(), box: await db.box.toArray(), layout: await db.meta.get("layout") ?? null,
        }));
        count += snapshot.teams.length + snapshot.box.length;
      }
      if (await Dexie.exists("pokemon-champions-runs")) {
        const db = new Dexie("pokemon-champions-runs");
        db.version(1).stores({ records: "id, kind" });
        try {
          records = await db.table("records").toArray();
          count += records.length;
        } finally { db.close(); }
      }
      if (count) await api.importBrowser({ ...document, ...snapshot, records });
      storageChanged();
      // Reload clears the result repository's memory after importing old reports.
      if (count) window.location.reload();
      else setMessage(t("empty"));
    } catch (error) { setMessage(errorMessage(error)); }
    finally { setBusy(false); }
  };
  return <section id="workspace-storage" className="lib-workspace">
    <div className="lib-workspace-row">
      <h3 id="workspace-label">{t("title")}</h3>
      <div className="lib-workspace-path">
        <output id="workspace-path" aria-labelledby="workspace-label" title={current}>{current || t("busy")}</output>
        <button type="button" className="lib-icon-btn" aria-label={t("choose")} title={t("choose")} disabled={busy || !current} onClick={() => void choose()}>
          <IconFolderOpen aria-hidden />
        </button>
      </div>
    </div>
    <details><summary>{t("more")}</summary><p>{t("copyHint")}</p><p>{t("files")}</p>
      <button type="button" className="second-btn lib-btn" disabled={busy} onClick={() => void migrateBrowser()}>{t("legacy")}</button>
    </details>
    {pendingPath && <div className="notice" role="status"><p>{t("existing")} <span>{pendingPath}</span></p>
      <div className="lib-store-actions"><button type="button" className="second-btn lib-btn" disabled={busy} onClick={() => void openExisting()}>{t("open")}</button>
        <button type="button" className="second-btn lib-btn" disabled={busy} onClick={() => setPendingPath(null)}>{t("cancel")}</button></div>
    </div>}
    {busy && <p role="status">{t("busy")}</p>}
    {message && <p role="status" className="notice">{message}</p>}
  </section>;
}
