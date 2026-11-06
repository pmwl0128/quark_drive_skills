/** The comparison column of the metagame detail card, shared by both views.
 *
 * Top Meta ships ONE record per entry with both sides on it (`delta` = Top Meta minus in-game). The
 * in-game card looks a row up by its own key and shows the negation; the Top Meta card shows it as
 * is. Either way the column reads like the change column: this view relative to its baseline.
 *
 * Sample size decides the form: below five units the column carries no number at all — the Top Meta
 * page marks an entry the in-game top ten does not list, the in-game page shows the raw count. */
import type { SpSpread, TopMetaScopeDto } from "@pokemon-champions/protocol";
import { spreadKey } from "../../lib/spread.ts";
import { fromView, type MetaSource } from "../../lib/metaView.ts";
import { useMetaT } from "./messages.ts";

export type ValuePanel = "moves" | "items" | "abilities" | "natures" | "spreads";
type Panels = TopMetaScopeDto["panels"];
type ValueRecord = Panels[ValuePanel]["entries"][number];
export type PartnerRecord = Panels["partners"]["entries"][number];

export interface CompareLens {
  /** The view on screen. */
  source: MetaSource;
  scope: TopMetaScopeDto;
  record(panel: ValuePanel, key: string): ValueRecord | undefined;
  /** A teammate record by its Top Meta name, or (in-game view) by its in-game teammate rank. */
  partner(name: string, ingameRank?: number): PartnerRecord | undefined;
}

export function entryKey(entry: { name?: string; spread?: SpSpread }): string {
  return entry.spread ? spreadKey(entry.spread) : entry.name ?? "";
}

export function makeLens(source: MetaSource, scope: TopMetaScopeDto): CompareLens {
  const maps = new Map<ValuePanel, Map<string, ValueRecord>>();
  for (const panel of ["moves", "items", "abilities", "natures", "spreads"] as const) {
    maps.set(panel, new Map((scope.panels[panel].entries as ValueRecord[]).map((e) => [entryKey(e), e])));
  }
  const partners = scope.panels.partners.entries;
  const byName = new Map(partners.map((e) => [e.name, e]));
  const byIngameRank = new Map(partners.filter((e) => e.ingame?.kind === "rank")
    .map((e) => [(e.ingame as { value: number }).value, e]));
  return {
    source, scope,
    record: (panel, key) => maps.get(panel)?.get(key),
    partner: (name, ingameRank) => (ingameRank != null ? byIngameRank.get(ingameRank) : undefined)
      ?? byName.get(name),
  };
}

function pct(value: number) {
  return `${value.toFixed(1)}%`;
}

function topValueText(t: ReturnType<typeof useMetaT>, lens: CompareLens, panel: ValuePanel, rec: ValueRecord) {
  const { n, tier } = lens.scope.panels[panel];
  return t(tier === "count" ? "cmp.topCount" : "cmp.topValue")
    .replace("{v}", rec.share != null ? pct(rec.share) : "—")
    .replace("{k}", String(rec.count)).replace("{n}", String(n));
}

/** What the OTHER side says about one record, as plain text (tooltip and hover line). */
function useOtherText() {
  const t = useMetaT();
  return (lens: CompareLens, panel: ValuePanel, rec: ValueRecord): string => {
    if (lens.source === "ingame") {
      return topValueText(t, lens, panel, rec);
    }
    const ingame = rec.ingame;
    if (!ingame || ingame.kind === "absent") return t("cmp.ingameAbsent");
    if (ingame.kind === "bound") return t("cmp.ingameBelow").replace("{v}", pct(ingame.below));
    return t("cmp.ingameValue").replace("{v}", pct(ingame.value));
  };
}

export function CompareCell({ lens, panel, entry }: {
  lens: CompareLens;
  panel: ValuePanel;
  entry: { name?: string; spread?: SpSpread };
}) {
  const t = useMetaT();
  const otherText = useOtherText();
  const rec = lens.record(panel, entryKey(entry));
  if (!rec) return <span className="mc-dl" />;
  const { tier, n } = lens.scope.panels[panel];
  const other = otherText(lens, panel, rec);
  if (tier === "count") {
    if (lens.source === "top") {
      return rec.marker === "outside_ingame_top"
        ? <span className="mc-dl cmp mark" title={other}>{t("cmp.outside")}</span>
        : <span className="mc-dl" title={other} />;
    }
    const unseen = rec.marker === "unseen_in_top";
    return (
      <span className={`mc-dl cmp count${unseen ? " mark" : ""}`}
            title={unseen ? `${other} · ${t("cmp.unseen")}` : `${other} · ${t("cmp.fewSamples")}`}>
        {rec.count}/{n}
      </span>
    );
  }
  const value = fromView(rec.delta, lens.source);
  if (value == null) return <span className="mc-dl" title={other} />;
  const rounded = Math.round(value * 10) / 10;
  if (rounded === 0) return <span className="mc-dl" title={other} />;
  const minor = rec.withinNoise === true || Math.abs(rounded) < 0.5;
  const sign = rounded > 0 ? "+" : "−";
  const floor = rec.deltaIsFloor ? "≥" : "";
  const title = `${other}${minor && rec.withinNoise ? ` · ${t("cmp.withinNoise")}` : ""}`;
  return (
    <span className={`mc-dl cmp ${rounded > 0 ? "up" : "down"}${minor ? " minor" : ""}`} title={title}
          data-degree={Math.abs(rounded) < 2 ? "small" : Math.abs(rounded) < 10 ? "medium" : "large"}>
      <span className="ar" aria-hidden="true">{rounded > 0 ? "▲" : "▼"}</span>
      <span className="mc-sr">{sign}</span>{floor && <small>{floor}</small>}{Math.abs(rounded).toFixed(1)}
    </span>
  );
}

/** The hover line of a Top Meta row: both sides, so a screenshot reader can check the column. */
export function CompareAddon({ lens, panel, entry }: {
  lens: CompareLens;
  panel: ValuePanel;
  entry: { name?: string; spread?: SpSpread };
}) {
  const t = useMetaT();
  const otherText = useOtherText();
  const rec = lens.record(panel, entryKey(entry));
  if (!rec) return null;
  return (
    <span className="mc-cmp-pop">
      <b>{topValueText(t, lens, panel, rec)}</b>
      {lens.scope.comparedWith && <span className="muted">{otherText({ ...lens, source: "top" }, panel, rec)}</span>}
    </span>
  );
}
