import type { SessionDto } from "@pokemon-champions/protocol";
import { defineMessages } from "../../i18n.ts";
import "./session-state.css";

export const useSessionT = defineMessages({
  idle: { zh: "待交给助手", en: "Ready to hand off", ja: "アシスタントへの引き継ぎ待ち" },
  progress: { zh: "已收到进展", en: "Progress received", ja: "進捗を受信" },
  awaiting: { zh: "待你决策", en: "Your decision needed", ja: "あなたの判断待ち" },
  questions: { zh: "待你回答", en: "Your answers needed", ja: "あなたの回答待ち" },
  needs_changes: { zh: "需要修改", en: "Changes needed", ja: "修正が必要" },
  review: { zh: "草稿待复核", en: "Draft awaiting review", ja: "下書きの確認待ち" },
  done: { zh: "复核通过", en: "Review passed", ja: "確認済み" },
});

export function sessionState(session: SessionDto) {
  return session.state ?? (session.revision === 0 ? "idle" : "progress");
}

export function SessionState({ session }: { session: SessionDto }) {
  const t = useSessionT();
  const state = sessionState(session);
  return <span className={`session-state state-${state}`}><i aria-hidden />{t(state)}</span>;
}
