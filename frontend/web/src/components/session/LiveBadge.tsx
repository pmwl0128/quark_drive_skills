/** Whether this page is still receiving the bridge's updates. */
import { useLiveStatus } from "./live.ts";
import { useSessionCopyT } from "./messages.ts";
import "./console.css";

export function LiveBadge() {
  const t = useSessionCopyT();
  const status = useLiveStatus();
  if (!status) return null;
  return <span className={`live-badge live-${status}`} role="status"><i aria-hidden />{t(`live.${status}`)}</span>;
}
