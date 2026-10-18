/** One line of text to hand elsewhere (an instruction, a terminal command) with its copy button. */
import { IconCheck, IconCopy } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { useToast } from "../Toast.tsx";
import { useSessionCopyT } from "./messages.ts";
import "./console.css";

export function CopyLine({ text, label, code = false }: { text: string; label?: string; code?: boolean }) {
  const t = useSessionCopyT();
  const toast = useToast();
  const [copied, setCopied] = useState(false);
  useEffect(() => setCopied(false), [text]);
  return <div className={`copy-line${code ? " code" : ""}`}>
    {code ? <code>{text}</code> : <p>{text}</p>}
    <button type="button" className="second-btn" onClick={async () => {
      try { await navigator.clipboard.writeText(text); setCopied(true); }
      catch { toast({ text: t("copyFailed") }); }
    }}>{copied ? <IconCheck size={16} aria-hidden /> : <IconCopy size={16} aria-hidden />}
      {copied ? t("copied") : label ?? t("copy")}</button>
  </div>;
}
