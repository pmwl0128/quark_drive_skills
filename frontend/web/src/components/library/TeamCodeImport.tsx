import type { FormatId, TeamDoc } from "@pokemon-champions/protocol";
import { useEffect, useRef, useState } from "react";
import { useRuntime } from "../../runtime/context.tsx";
import { readTeamCode, teamCodeFromText } from "../../lib/teamCode.ts";
import { useLibraryT } from "./messages.ts";
import { ReplaceTeamNotice } from "./ReplaceTeamNotice.tsx";

export function TeamCodeImport({ format, onPick, replaceCurrent = false }: {
  format: FormatId; onPick: (team: TeamDoc) => void; replaceCurrent?: boolean;
}) {
  const t = useLibraryT();
  const { adapter, can } = useRuntime();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const generation = useRef(0);
  useEffect(() => {
    setBusy(false); setFailed(false); setConfirming(false);
    return () => { generation.current += 1; };
  }, [adapter, format]);
  if (!can("team.code")) return null;
  const code = teamCodeFromText(text);
  const run = async () => {
    if (!code || busy) return;
    const request = ++generation.current;
    setConfirming(false);
    setBusy(true); setFailed(false);
    try {
      const result = await readTeamCode(adapter, code, format);
      if (request === generation.current) onPick(result.team);
    }
    catch { if (request === generation.current) setFailed(true); }
    finally { if (request === generation.current) setBusy(false); }
  };
  const submit = () => {
    if (!code || busy) return;
    if (replaceCurrent) setConfirming(true); else void run();
  };
  return <div className="team-code-import">
    <label><span>{t("lib.code.label")}</span>
      <input value={text} maxLength={24} disabled={busy || confirming} autoComplete="off" spellCheck={false}
        placeholder={t("lib.code.placeholder")} onChange={event => { setText(event.target.value); setFailed(false); }}
        onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); submit(); } }} />
    </label>
    {!confirming && <button type="button" className="second-btn" disabled={busy || !code} onClick={submit}>
      {t(busy ? "lib.code.loading" : "lib.code.import")}
    </button>}
    {confirming && <ReplaceTeamNotice onCancel={() => setConfirming(false)} onConfirm={() => void run()} />}
    {failed && <p role="alert" className="notice">{t("lib.code.failed")}</p>}
  </div>;
}
