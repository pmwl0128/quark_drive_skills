import { useMemo, type ReactNode } from "react";
import { AdaptiveSelect } from "../AdaptiveSelect.tsx";
import type { TeamMemberDoc } from "@pokemon-champions/protocol";
import { GameImage } from "../GameImage.tsx";
import { TypeBadge } from "../TypeBadge.tsx";
import { PLACEHOLDERS, typeColor } from "../../assets/icons.ts";
import { useLibraryDragSource } from "../../lib/library/workspace.tsx";
import { displayName, useLang } from "../../i18n.ts";
import { useSharedT } from "../messages.ts";
import type { DexIndexEntry } from "../../runtime/adapter.ts";
import type { ItemRef } from "../../runtime/projection.ts";
import { MonPicker } from "./MonPicker.tsx";
import { megaFor } from "../../lib/lookup.ts";
/** The mon's portrait on a soft wash of its primary type, beside the name row of a build. With
 * `member` it can be dragged into the library dock's boxes (design §2.4). */
export function MonPortrait({ entry, name, member }: {
  entry: DexIndexEntry | undefined;
  name: string;
  /** The build as it stands, read when a drag starts. */
  member?: () => TeamMemberDoc | null;
}) {
  const primary = entry?.types[0];
  const drag = useLibraryDragSource(() => {
    const value = member?.();
    return value ? { kind: "pokemon", member: value, name } : null;
  }, () => name);
  return (
    <span className={`duel-portrait${member && entry ? " draggable" : ""}`} {...(member && entry ? drag : {})}
      style={primary ? { ["--mt" as string]: typeColor(primary) } : undefined}>
      {entry
        ? <GameImage assetKey={entry.key} role="dense" alt={name} className="hp-bar-sprite" />
        : <img className="hp-bar-sprite" src={PLACEHOLDERS.pokemon} alt="" aria-hidden />}
    </span>
  );
}

/** Species and optional Mega forme as one row, shared by calculation and library editors. */
export function MonNameRow({ slug, item, dex, items, actions, identityShown = false,
  onSpecies, onForme }: {
  slug: string;
  item: string;
  dex: DexIndexEntry[];
  items: ItemRef[];
  actions?: ReactNode;
  /** Omit identity badges when the surrounding surface already provides that information. */
  identityShown?: boolean;
  /** A different species: the caller decides what of the old build survives. */
  onSpecies: (slug: string) => void;
  /** A forme switch keeps the build; `dropStone` asks the caller to clear a held Mega stone. */
  onForme: (slug: string, dropStone: boolean) => void;
}) {
  const { lang } = useLang();
  const t = useSharedT();
  const literal = dex.find((e) => e.slug === slug);
  const mega = literal?.isMega ? literal : megaFor(slug, item, dex, items);
  const entry = mega ?? literal;
  // Forme picker: the base species plus every Mega form the dex lists for it. Derived from the
  // browse index that is already loaded — a forme switch must not cost a round trip.
  const baseName = entry?.isMega ? entry.baseSpecies : entry?.name;
  const formes = useMemo(() => {
    if (!baseName) return [];
    const base = dex.find((e) => e.name === baseName && !e.isMega);
    const megas = dex.filter((e) => e.isMega && e.baseSpecies === baseName);
    return megas.length && base ? [base, ...megas] : [];
  }, [dex, baseName]);
  const actionBox = actions ? <span className="mon-editor-actions">{actions}</span> : null;

  return (
    // Species and forme are one identity, so they sit on one row when both exist — a forme select
    // stacked underneath would push the card taller than its partner across the page.
    <div className={`mon-editor-namerow${formes.length ? " split" : ""}`}>
      <div className="mon-editor-namefield">
        <span className="mon-editor-label-line">
          <span>{t("calc.name")}</span>
          {!identityShown && (
            <span className="mon-editor-types">
              {(entry?.types ?? []).map((ty) => <TypeBadge key={ty} type={ty} />)}
            </span>
          )}
          {!formes.length && actionBox}
        </span>
        <MonPicker slug={slug} dex={dex} ariaLabel={t("calc.name")}
          onSlug={onSpecies} />
      </div>
      {formes.length > 0 && (
        <div className="mon-editor-namefield">
          <span className="mon-editor-form-heading">
            {t("calc.forme")}
            {mega && !identityShown && <span className="mega-badge" title={displayName(mega, lang)}>MEGA</span>}
            {actionBox}
          </span>
          <AdaptiveSelect value={entry?.slug ?? ""} aria-label={t("calc.forme")}
            onValueChange={(value) => {
              const next = dex.find((x) => x.slug === value);
              // Dropping back to the base must also drop the stone, or the held item would silently
              // re-Mega it and the picker would disagree with the numbers.
              const dropStone = !!next && !next.isMega
                && items.some((i) => i.name === item && i.requiredBy?.length);
              onForme(value, dropStone);
            }}>
            {formes.map((f) => (
              <option key={f.slug} value={f.slug}>{displayName(f, lang)}</option>
            ))}
          </AdaptiveSelect>
        </div>
      )}
    </div>
  );
}

