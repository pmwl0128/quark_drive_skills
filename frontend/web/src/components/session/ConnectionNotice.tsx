/** Site-wide notice for a tab that lost the local bridge. The shell keeps the session feed open on
 * every page, so a restart is noticed wherever the reader is, not only on a session page. */
import { IconPlugConnectedX } from "@tabler/icons-react";
import { CopyLine } from "./CopyLine.tsx";
import { useConsoleStatus, useLiveStatus, useSessionChanges } from "./live.ts";
import { useSessionCopyT } from "./messages.ts";
import "./console.css";

export function ConnectionNotice() {
  const t = useSessionCopyT();
  const status = useLiveStatus();
  const setup = useConsoleStatus();
  useSessionChanges(() => {});
  if (status !== "signedOut" && status !== "offline") return null;
  const signedOut = status === "signedOut";
  return <div className={`connection-notice ${status}`} role="alert">
    <IconPlugConnectedX size={20} aria-hidden />
    <div>
      <strong>{t(signedOut ? "notice.signedOutTitle" : "notice.offlineTitle")}</strong>
      <p>{t(signedOut ? "notice.signedOutBody" : "notice.offlineBody")}</p>
      {signedOut ? <CopyLine code text={`${setup?.pcuiCommand ?? "pcui"} open`} /> : null}
    </div>
  </div>;
}
