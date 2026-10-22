/** One wing of the duel strip: a side's roster, the conditions standing on that side's field, and
 * the pokepaste import that fills the whole roster at once.
 *
 * The roster is always six slots wide — filled, the next free one, then placeholders — so adding a
 * Pokémon never moves the conditions row, the centre console or the other wing. The side's name is
 * the menu button for the two whole-team actions, which keeps the condition row free for what is
 * actually switched on.
 *
 * Conditions live with the side they belong to rather than in one shared flyout: each chip clears
 * itself, and the wing's own button opens that side's toggles right under the roster it affects. */
import type { Dispatch, ReactNode, SetStateAction } from "react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useCalcT, type CalcMsgKey } from "../messages.ts";
import { useLibraryDrop, useLibraryWorkspace } from "../../../lib/library/workspace.tsx";
import { copyText } from "../../../lib/download.ts";
import { formatPokepasteMon } from "../../../lib/pokepaste.ts";
import type { DexIndexEntry } from "../../../runtime/adapter.ts";
import type { ItemRef } from "../../../runtime/projection.ts";
import { MonAvatar } from "./MonEditor.tsx";
import { roveFocus, useFocusOnOpen, usePopover } from "../../../lib/popover.ts";
import { SIDE_FLAGS, effectiveEntry, type FieldState, type MonState, type SideId } from "./state.ts";
import { useAnchoredPopover } from "../../../components/useAnchoredPopover.ts";
import { ReplaceTeamNotice } from "../../../components/library/ReplaceTeamNotice.tsx";

import { preloadable } from "../../../lib/preloadable.ts";

const SavedTeamPicker = preloadable(() => import("../../../components/library/SavedTeamPicker.tsx"), <SavedTeamLoading />);

function SavedTeamLoading() {
  const t = useCalcT();
  return <p className="muted">{t("state.loading")}</p>;
}

/** Gap between condition chips; mirrors `.team-conds { gap }`. */
const COND_GAP = 5;

/** How many chips of a one-line row fit, measured rather than guessed: label length varies
 * threefold across languages, and a fixed cap either wastes the row or pushes the "+N" chip — the
 * only way to the rest — past the clipped edge.
 *
 * Every chip stays rendered (the overflow ones out of flow and invisible) so each can be measured,
 * and the "+N" chip is measured from a hidden twin. The answer depends only on those widths, never
 * on whether "+N" is currently shown, so it cannot oscillate: when the chips do not all fit, at
 * least one folds and room is kept for "+N". */
function useChipFit(row: { current: HTMLElement | null }, signature: string) {
  const [fit, setFit] = useState(Number.POSITIVE_INFINITY);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const node = row.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setWidth(node.clientWidth));
    observer.observe(node);
    return () => observer.disconnect();
  }, [row]);
  useLayoutEffect(() => {
    const node = row.current;
    if (!node) return;
    const chips = Array.from(node.querySelectorAll<HTMLElement>("[data-chip]"))
      .map((chip) => chip.offsetWidth);
    const face = (kind: string) => node.querySelector<HTMLElement>(`[data-measure="${kind}"]`)
      ?.offsetWidth ?? 0;
    const room = node.clientWidth;
    const all = chips.reduce((sum, w) => sum + w + COND_GAP, 0) - COND_GAP;
    let next = chips.length;
    if (all > room) {
      const count = face("count");
      next = 0;
      let used = 0;
      for (const w of chips) {
        if (used + w + COND_GAP + count > room) break;
        used += w + COND_GAP;
        next += 1;
      }
      next = Math.max(0, Math.min(next, chips.length - 1));
    }
    setFit(next);
  }, [row, signature, width]);
  return fit;
}

export interface ImportOutcome {
  added: number;
  unresolved: string[];
  rescaledEvs: boolean;
}

import { TEAM_SIZE } from "../../../lib/battle.ts";

/** A roster Pokémon as a Showdown block, in English canonicals: the block is read by other tools,
 * so it is a join-key document, not a display (design §2.1). Null for a slot with no species. */
function pasteOf(mon: MonState, dex: DexIndexEntry[], items: ItemRef[]): string | null {
  const entry = effectiveEntry(mon, dex, items);
  if (!entry) return null;
  return formatPokepasteMon({
    species: entry.name, item: mon.item, ability: mon.ability, nature: mon.nature,
    sps: mon.sps, moves: mon.moves.filter(Boolean),
  });
}

function Caret() {
  return (
    <svg className="duel-caret" viewBox="0 0 10 10" aria-hidden>
      <path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="currentColor" strokeWidth="1.6"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function PlusGlyph() {
  return (
    <svg className="duel-plus" viewBox="0 0 12 12" aria-hidden>
      <path d="M6 2v8M2 6h8" fill="none" stroke="currentColor" strokeWidth="1.5"
        strokeLinecap="round" />
    </svg>
  );
}

/** The strip's frame: weather art behind the console, terrain colour along the bottom edge. The
 * outer box is the size container the layout tiers query, because the calc rail can take several
 * hundred pixels from the page without the viewport changing at all. */
export function DuelBar({ field, children }: { field: FieldState; children: ReactNode }) {
  const weather = field.weather ? ` weather-${field.weather.toLowerCase()}` : "";
  const terrain = field.terrain ? ` terrain-${field.terrain.toLowerCase()}` : "";
  return (
    <div className="duel-bar">
      <div className={`duel-teams${weather}${terrain}`}>{children}</div>
    </div>
  );
}

export function TeamBar({
  label, teamLabel, team, index, onIndex, onAdd, onRemove, onReset,
  field, setField, side, allowedFlags, flagNote, lockedNote, inlineFlags = false,
  dex, items, onImport, mirrored = false, librarySide,
}: {
  /** Short side name shown on the strip (我方 / 对方). */
  label: string;
  /** Full name for menus and screen readers (我方队伍). */
  teamLabel: string;
  team: MonState[];
  index: number;
  onIndex: (i: number) => void;
  onAdd: () => void;
  onRemove: (i: number) => void;
  onReset: () => void;
  field: FieldState;
  setField: Dispatch<SetStateAction<FieldState>>;
  /** The FIELD side this roster's conditions are stored on. The damage tab keeps the left roster on
   * side a; the durability workspace defends on the left, which is field side b. */
  side: SideId;
  /** Conditions this tool can actually use; omitted means the calculator's full list. An empty set
   * leaves the side without a toggle and shows `lockedNote` instead. */
  allowedFlags?: ReadonlySet<string>;
  flagNote?: string;
  lockedNote?: string;
  /** Show the offered conditions as toggles right on the strip instead of chips plus a popover —
   * for a tool that offers only one or two (the speed line's Tailwind). */
  inlineFlags?: boolean;
  dex: DexIndexEntry[];
  items: ItemRef[];
  onImport: (text: string) => Promise<ImportOutcome>;
  /** Right-hand wing: the name, the roster and the conditions all pack toward the page edge, so the
   * two sides read as facing each other rather than as one list repeated twice. */
  mirrored?: boolean;
  /** The roster side a team or Pokémon dropped from the library lands on (calc/LibraryTransfers.tsx). */
  librarySide?: SideId;
}) {
  const t = useCalcT();
  const drop = useLibraryDrop(librarySide ? [`calc-team-${librarySide}`, `calc-mon-${librarySide}`] : [],
    (event) => {
      const slot = (event.target as Element).closest<HTMLElement>("[data-team-slot]");
      return slot && event.currentTarget.contains(slot) ? Number(slot.dataset.teamSlot) : undefined;
    });
  const { requestSave } = useLibraryWorkspace("requestSave");
  const menu = usePopover();
  // The saved-team picker's code arrives while the menu is open, so choosing it renders at once.
  useEffect(() => { if (menu.open && librarySide) void SavedTeamPicker.preload().catch(() => {}); },
    [menu.open, librarySide]);
  const paste = usePopover(menu.trigger);
  const saved = usePopover(menu.trigger);
  const savedPosition = useAnchoredPopover(saved.open, menu.trigger, saved.wrap,
    { minWidth: 380, matchAnchor: false, align: mirrored ? "end" : "start", maxHeight: 440 });
  const conds = usePopover();
  const menuList = useRef<HTMLDivElement>(null);
  const condList = useRef<HTMLDivElement>(null);
  useFocusOnOpen(menu.open, menuList, "[role=menuitem]");
  useFocusOnOpen(conds.open, condList, ".duel-opt");

  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  // Which menu row just copied: it says so for a moment, then the menu closes by itself.
  const [copied, setCopied] = useState<"mon" | "team" | null>(null);
  const copy = async (which: "mon" | "team") => {
    const blocks = (which === "mon" ? [team[index]] : team)
      .flatMap((mon) => (mon ? pasteOf(mon, dex, items) ?? [] : []));
    if (!blocks.length || !(await copyText(blocks.join("\n\n")))) return;
    setCopied(which);
    window.setTimeout(() => {
      setCopied(null);
      menu.setOpen(false);
      menu.trigger.current?.focus();
    }, 900);
  };
  const activeNamed = !!team[index]?.slug;
  const anyNamed = team.some((mon) => !!mon.slug);
  const [outcome, setOutcome] = useState<ImportOutcome | null>(null);
  const [importFailed, setImportFailed] = useState(false);
  const [confirmImport, setConfirmImport] = useState(false);
  const run = async () => {
    if (busy) return;
    setConfirmImport(false);
    setBusy(true);
    setImportFailed(false);
    try {
      setOutcome(await onImport(text));
    } catch {
      setImportFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const singles = field.format === "single";
  const flags = field.sides[side];
  const setFlag = (key: string, on: boolean) => setField((current) => ({
    ...current,
    sides: { ...current.sides, [side]: { ...current.sides[side], [key]: on } },
  }));
  const offered = SIDE_FLAGS.filter((flag) => !allowedFlags || allowedFlags.has(flag.key));
  const flagName = (flag: (typeof SIDE_FLAGS)[number]) => t(flag.label as CalcMsgKey);
  // Live conditions take the visible places first; a muted one only says "set, but ignored here".
  const active = offered.filter((flag) => flags[flag.key])
    .map((flag) => ({ flag, muted: !!flag.doublesOnly && singles }))
    .sort((a, b) => Number(a.muted) - Number(b.muted));
  const condRow = useRef<HTMLDivElement>(null);
  const fit = useChipFit(condRow,
    `${active.map(({ flag, muted }) => `${flagName(flag)}${muted ? "~" : ""}`).join("|")}:${t("calc.condAdd")}`);
  const hidden = Math.max(0, active.length - fit);
  const condsTitle = t("calc.sideOf").replace("{side}", label);
  const moreLabel = t("calc.moreSideFlags")
    .replace("{n}", String(active.length))
    .replace("{flags}", active.map(({ flag }) => flagName(flag)).join("、"));

  const slots = Array.from({ length: TEAM_SIZE }, (_, i) => {
    const mon = team[i];
    if (mon) {
      return <MonAvatar key={mon.uid || i} mon={mon} dex={dex} items={items} active={i === index} slot={i}
        onClick={() => onIndex(i)} onRemove={() => onRemove(i)} />;
    }
    if (i === team.length) {
      return (
        <button key={i} type="button" className="team-slot-add" onClick={onAdd} data-team-slot={i}
          aria-label={t("calc.addMon")} title={t("calc.addMon")}>
          <PlusGlyph />
        </button>
      );
    }
    return <span key={i} className="team-slot-rest" aria-hidden />;
  });

  return (
    <div className={`team-bar${mirrored ? " mirrored" : ""}`} {...drop}>
      <div className="team-who" ref={menu.wrap} onBlur={menu.onBlur}>
        <button ref={menu.trigger} type="button"
          className={`team-who-btn${menu.open ? " open" : ""}`}
          aria-haspopup="menu" aria-expanded={menu.open}
          aria-label={`${teamLabel} ${team.length}/${TEAM_SIZE}`}
          title={t("calc.teamMenuHint")} onClick={menu.toggle}>
          <b>{label}<Caret /></b>
          <span className="num">{team.length}/{TEAM_SIZE}</span>
        </button>
        {menu.open && (
          <div ref={menuList} className="duel-pop duel-menu" role="menu" aria-label={teamLabel}
            onKeyDown={(event) => roveFocus(event, "[role=menuitem]")}>
            <button type="button" role="menuitem" className="duel-menu-row"
              onClick={() => { menu.setOpen(false); saved.setOpen(false); setOutcome(null); paste.setOpen(true); }}>
              {t("calc.importPaste")}…
            </button>
            {librarySide && <button type="button" role="menuitem" className="duel-menu-row"
              onClick={() => { menu.setOpen(false); paste.setOpen(false); saved.setOpen(true); }}>
              {t("library.importSaved")}…
            </button>}
            <button type="button" role="menuitem" className="duel-menu-row" disabled={!activeNamed}
              onClick={() => void copy("mon")}>
              {copied === "mon" ? t("calc.copied") : t("calc.copyActive")}
            </button>
            <button type="button" role="menuitem" className="duel-menu-row" disabled={!anyNamed}
              onClick={() => void copy("team")}>
              {copied === "team" ? t("calc.copied") : t("calc.exportTeam")}
            </button>
            {librarySide && (
              // The side as a team in the reader's library: the dock opens on its save dialog.
              <button type="button" role="menuitem" className="duel-menu-row" disabled={!anyNamed}
                onClick={() => { menu.setOpen(false); requestSave(`calc-keep-${librarySide}`); }}>
                {t("calc.saveTeam")}…
              </button>
            )}
            <div className="duel-menu-sep" role="separator" />
            <button type="button" role="menuitem" className="duel-menu-row danger"
              onClick={() => { menu.setOpen(false); onReset(); menu.trigger.current?.focus(); }}>
              {t("calc.resetTeamFull")}
            </button>
          </div>
        )}
      </div>
      <div className="team-strip" role="group" aria-label={teamLabel}>{slots}</div>

      {inlineFlags ? (
        <div className="team-conds inline" role="group" aria-label={condsTitle}>
          {offered.map((flag) => {
            const on = !!flags[flag.key];
            const ignored = !!flag.doublesOnly && singles;
            return (
              <button key={flag.key} type="button" className={`duel-toggle${on ? " on" : ""}`}
                data-cat={flag.cat} aria-pressed={on} disabled={ignored && !on}
                title={ignored ? t("calc.doublesOnlyHint") : flag.hint ? t(flag.hint as CalcMsgKey) : undefined}
                onClick={() => setFlag(flag.key, !on)}>
                <i aria-hidden />{flagName(flag)}
              </button>
            );
          })}
        </div>
      ) : (
        <div className="team-conds-zone" ref={conds.wrap} onBlur={conds.onBlur}>
          {/* The button keeps one place, under the side's name at the outer edge, however many
              conditions are on; the switched-on ones line up under the roster they affect. */}
          {offered.length > 0 && (
            <button ref={conds.trigger} type="button"
              className={`duel-cond-add team-cond-btn${conds.open ? " open" : ""}`}
              aria-expanded={conds.open} title={condsTitle} aria-label={condsTitle}
              onClick={conds.toggle}>
              <PlusGlyph />{t("calc.condAdd")}
            </button>
          )}
          <div ref={condRow} className="team-conds" aria-label={condsTitle}>
            {active.map(({ flag, muted }, at) => {
              const name = flagName(flag);
              const title = (muted ? t("calc.condSinglesOff") : t("calc.clearSideFlag"))
                .replace("{flag}", name);
              return (
                <button key={flag.key} type="button" data-chip
                  className={`duel-cond${muted ? " muted" : ""}${at >= fit ? " spill" : ""}`}
                  data-cat={flag.cat} title={title} aria-label={title} aria-hidden={at >= fit || undefined}
                  tabIndex={at >= fit ? -1 : undefined}
                  onClick={() => setFlag(flag.key, false)}>
                  <i aria-hidden /><span className="duel-cond-label">{name}</span>
                  <span className="duel-cond-x" aria-hidden>×</span>
                </button>
              );
            })}
            {hidden > 0 && (
              <button type="button" className={`duel-cond-add more${conds.open ? " open" : ""}`}
                aria-expanded={conds.open} title={moreLabel} aria-label={moreLabel}
                onClick={conds.toggle}>
                <span className="num">+{hidden}</span>
              </button>
            )}
            {offered.length === 0 && lockedNote && (
              <span className="duel-cond-lock" title={lockedNote}>{lockedNote}</span>
            )}
            {offered.length > 0 && (
              // The "+N" chip's widest face, measured by useChipFit; never seen, never focused.
              <span className="duel-cond-add more spill" data-measure="count" aria-hidden>
                <span className="num">+{Math.max(9, active.length)}</span>
              </span>
            )}
          </div>
          {conds.open && (
            <div ref={condList} className="duel-pop team-cond-pop" role="group" aria-label={condsTitle}
              onKeyDown={(event) => roveFocus(event, ".duel-opt")}>
              <div className="duel-pop-title">{condsTitle}{flagNote && <small>{flagNote}</small>}</div>
              <div className="duel-opt-grid">
                {offered.map((flag) => {
                  const on = !!flags[flag.key];
                  const ignored = !!flag.doublesOnly && singles;
                  return (
                    <button key={flag.key} type="button" className={`duel-opt${on ? " on" : ""}`}
                      data-cat={flag.cat} aria-pressed={on}
                      // A doubles-only flag that is already on stays clickable in singles, so it can
                      // still be switched off from here.
                      disabled={ignored && !on}
                      title={ignored ? t("calc.doublesOnlyHint")
                        : flag.hint ? t(flag.hint as CalcMsgKey) : undefined}
                      onClick={() => setFlag(flag.key, !on)}>
                      <i aria-hidden /><span>{flagName(flag)}</span>
                      {flag.doublesOnly && <small>{t("format.double")}</small>}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {saved.open && librarySide && createPortal(
        <div className="duel-pop saved-team-pop" ref={saved.wrap} onBlur={saved.onBlur}
          style={savedPosition} role="dialog" aria-label={`${teamLabel} · ${t("library.importSaved")}`}>
          <SavedTeamPicker format={field.format} receiverId={`calc-team-${librarySide}`}
            replaceCurrent={anyNamed}
            title={`${teamLabel} · ${t("library.importSaved")}`}
            onClose={() => { saved.setOpen(false); menu.trigger.current?.focus(); }} />
        </div>, document.body,
      )}
      {paste.open && (
        <div className="duel-pop paste-pop" ref={paste.wrap} onBlur={paste.onBlur}
          role="dialog" aria-label={`${teamLabel} · ${t("calc.importPaste")}`}>
          <label className="paste-label">{t("calc.pasteHint")}
            <textarea rows={8} value={text} spellCheck={false} autoFocus disabled={busy || confirmImport}
              placeholder={t("calc.pasteHint")}
              onChange={(e) => { setText(e.target.value); setImportFailed(false); }} />
          </label>
          <div className="paste-actions">
            {!confirmImport && <button type="button" className="primary-btn" disabled={busy || !text.trim()}
              onClick={() => { if (anyNamed) setConfirmImport(true); else void run(); }}>
              {busy ? t("state.loading") : t("calc.pasteApply")}
            </button>}
            <button type="button" className="ghost-btn"
              onClick={() => { paste.setOpen(false); setOutcome(null); setConfirmImport(false); menu.trigger.current?.focus(); }}>
              {t("calc.pasteClose")}
            </button>
          </div>
          {confirmImport && <ReplaceTeamNotice onCancel={() => setConfirmImport(false)} onConfirm={() => void run()} />}
          {importFailed && <p className="notice" role="alert">{t("calc.pasteFailed")}</p>}
          {outcome && (
            <div className="paste-outcome">
              <div>{t("calc.pasteResult").replace("{n}", String(outcome.added))}</div>
              {outcome.rescaledEvs && <div className="paste-note">{t("calc.pasteEvNote")}</div>}
              {outcome.unresolved.length > 0 && (
                <div className="paste-note warn">
                  {t("calc.pasteUnresolved").replace("{names}", outcome.unresolved.join("、"))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
