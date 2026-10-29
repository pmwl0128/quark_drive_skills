/** Shared presentation of the tool facts a question actually read. */
import type { FormatId, QaToolTraceEntry } from "@pokemon-champions/protocol";
import type { ComponentType } from "react";
import { IconBolt, IconBook2, IconCards, IconChartBar, IconDatabase, IconFilter, IconSwords } from "@tabler/icons-react";
import { useLang } from "../../../i18n.ts";
import { optionalAssistKey, useAssistT } from "../messages.ts";
export interface QaInput { question: string }

/** Each lookup's kind of data, as an icon. */
export function toolIcon(tool: string): ComponentType<{ "aria-hidden"?: boolean | "true" }> {
  if (tool === "dex.find" || tool === "dex.find_move") return IconFilter;
  if (tool.startsWith("dex.")) return IconBook2;
  if (tool.startsWith("meta.")) return IconChartBar;
  if (tool === "team.repset") return IconCards;
  if (tool === "calc.damage" || tool === "calc.threshold") return IconSwords;
  if (tool === "calc.speed" || tool === "calc.speed_threshold" || tool === "speed_ladder") return IconBolt;
  return IconDatabase;
}

/** A lookup in the reader's language: its localized action with the lookup's own facts filled in. */
export function useTraceLabel() {
  const t = useAssistT();
  const { lang } = useLang();
  return (source: QaToolTraceEntry): string => {
    if (!source.params) return lang === "en" ? source.label : t("qa.trace.generic");
    const key = source.tool.startsWith("dex.")
      ? `qa.trace.dex.${source.tool.slice(4)}` : `qa.trace.${source.tool}`;
    const msgKey = optionalAssistKey(key);
    const translated = msgKey ? t(msgKey) : source.label;
    const params = { ...source.params };
    if (params.format === "single" || params.format === "double") {
      params.format = t(`format.${params.format}`);
    }
    return translated.replace(/\{([^}]+)\}/g, (_, name: string) => String(params[name] ?? `{${name}}`));
  };
}

/** The formats an answer's lookups read: its data identity (a dex-only answer reads none). */
export function traceFormats(trace: QaToolTraceEntry[]): FormatId[] {
  const formats = new Set<FormatId>();
  for (const entry of trace) {
    const format = entry.params?.format;
    if (format === "single" || format === "double") formats.add(format);
  }
  return [...formats];
}
