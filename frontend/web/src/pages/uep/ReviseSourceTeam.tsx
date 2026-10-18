/** Write a recommended team back to the library team the session started from (`meta.teamRef`):
 * the team takes the new builds and its previous version is kept in a free slot, so nothing the
 * reader had is lost and the change can be undone. Shown only while that team exists in the same
 * format. */
import { IconArrowBackUp } from "@tabler/icons-react";
import { useState } from "react";
import { Modal } from "../../components/Modal.tsx";
import { useToast } from "../../components/Toast.tsx";
import { useTeamLabel } from "../../components/library/shared.tsx";
import { useDexByName } from "../../hooks.ts";
import { useLang } from "../../i18n.ts";
import { useLibrary } from "../../lib/library/hooks.ts";
import { boxById, teamDocOf } from "../../lib/library/repo.ts";
import { toTeamDoc } from "../../lib/teamDoc.ts";
import { useRuntime } from "../../runtime/context.tsx";
import { useUepT } from "./messages.ts";

export function ReviseSourceTeam({ teamId, team, provisional }: { teamId: string; team: unknown; provisional: boolean }) {
  const t = useUepT();
  const toast = useToast();
  const { lang } = useLang();
  const { capabilities } = useRuntime();
  const library = useLibrary();
  const dex = useDexByName();
  const label = useTeamLabel(dex, lang);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const doc = toTeamDoc(team);
  const state = library && library !== "unavailable" ? library : null;
  const source = state?.teams.find((record) => record.id === teamId);
  if (!state || !source || !doc || doc.format !== source.format) return null;
  const name = label(source.name, teamDocOf(source, boxById(state))).text;
  const old = t("uep.versionName").replace("{name}", name);

  const revise = async () => {
    setBusy(true);
    try {
      const repo = await import("../../lib/library/repo.ts");
      const { season, rule, asOf } = capabilities.environment;
      const result = await repo.reviseTeam(teamId, doc, {
        origin: "session",
        snapshot: { season: season ?? null, rule: rule ?? null, asOf: asOf ?? null, deploymentId: capabilities.deploymentId ?? null },
      }, old);
      setOpen(false);
      if (result.status === "revised") {
        toast({ text: t("uep.revised").replace("{name}", name).replace("{old}", old),
                action: { label: t("uep.undo"), run: () => {
                  void repo.undoReviseTeam(result).then(undone => {
                    if (!undone) toast({text:t("uep.undoConflict")});
                  }, () => toast({text:t("uep.actionError")}));
                } } });
      } else {
        const reason = { unchanged: "uep.reviseUnchanged", full: "uep.reviseFull", invalid: "uep.reviseInvalid",
          format: "uep.reviseFormat", missing: "uep.reviseMissing" } as const;
        toast({ text: result.status === "no-room" ? t("uep.reviseNoRoom").replace("{n}", String(result.needed - result.free))
          : t(reason[result.status]) });
      }
    } catch (e) {
      console.error("Team revision failed:", e);
      toast({ text: t("uep.actionError") });
    } finally {
      setBusy(false);
    }
  };

  return <>
    <button type="button" className="second-btn" onClick={() => setOpen(true)}>
      <IconArrowBackUp size={15} aria-hidden />{t("uep.revise")}</button>
    {open ? <Modal title={t("uep.reviseTitle").replace("{name}", name)} width={480} onClose={() => setOpen(false)}
      footer={<>
        <button type="button" className="second-btn" data-autofocus onClick={() => setOpen(false)}>{t("uep.cancel")}</button>
        <button type="button" className="primary-btn" disabled={busy} onClick={() => void revise()}>{t("uep.revise")}</button>
      </>}>
      <p className="session-delete-body">{t("uep.reviseBody").replace("{name}", name).replace("{old}", old)}</p>
      {provisional ? <p className="session-delete-body revise-draft-note">{t("uep.reviseDraft")}</p> : null}
    </Modal> : null}
  </>;
}
