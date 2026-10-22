/** Showdown / Pokepaste text into the library: a team into a team slot (its Pokémon into the box),
 * or any number of Pokémon into the box.
 *
 * Reading and saving are two steps. The reader sees what was recognised, what was left out, which
 * Pokémon are already boxed, which slot the team goes to and what it would replace before anything
 * is written — a paste that silently dropped a misspelt member would store a five-Pokémon team the
 * reader believes has six. */
import type { FormatId, TeamDoc } from "@pokemon-champions/protocol";
import { useState } from "react";
import { GameImage } from "../GameImage.tsx";
import { Modal } from "../Modal.tsx";
import { SegmentedControl } from "../SegmentedControl.tsx";
import { useToast } from "../Toast.tsx";
import { useDexByName, useDexIndex, useItems, useNatures } from "../../hooks.ts";
import { displayName, useLang } from "../../i18n.ts";
import { useLibrarySnapshot } from "../../lib/library/hooks.ts";
import { TEAM_SIZE } from "../../lib/battle.ts";
import {
  addManyToBox, boxById, freeCells, freeTeamSlots, planTeamSave, removeBoxRecord, saveTeam, teamDocOf,
  undoSaveTeam, type BoxPosition, type LibraryState,
} from "../../lib/library/repo.ts";
import { localName, useNameMaps } from "../../lib/names.ts";
import { membersFromPaste, type PasteMembersResult } from "../../lib/pasteMembers.ts";
import { parsePokepaste } from "../../lib/pokepaste.ts";
import { toTeamDoc, toTeamMember } from "../../lib/teamDoc.ts";
import { useRuntime } from "../../runtime/context.tsx";
import { fill, useLibraryT } from "./messages.ts";
import { useTeamLabel } from "./shared.tsx";
import { TeamSlotPicker } from "./TeamSlotPicker.tsx";

/** A box paste is a list of owned Pokémon, not a team; this only bounds one paste. */
const BOX_PASTE_LIMIT = 60;

export type PasteTarget =
  | { mode: "team"; format: FormatId; slot?: number }
  | { mode: "box"; at?: BoxPosition };

interface Reading extends PasteMembersResult {
  overflow: boolean;
  doc: TeamDoc | null;
}

export function PasteDialog({ state, target, onSaved, onClose }: {
  state: LibraryState;
  target: PasteTarget;
  /** The saved team's id and format, or the first boxed Pokémon's id. */
  onSaved?: (id: string, format: FormatId) => void;
  onClose: () => void;
}) {
  const t = useLibraryT();
  const { lang } = useLang();
  const { adapter } = useRuntime();
  const toast = useToast();
  const names = useNameMaps();
  const snapshot = useLibrarySnapshot();
  const dex = useDexIndex();
  const items = useItems();
  const natures = useNatures();
  const dexByName = useDexByName();
  const teamLabel = useTeamLabel(dexByName, lang);
  const teamMode = target.mode === "team";
  const [text, setText] = useState("");
  const [format, setFormat] = useState<FormatId>(teamMode ? target.format : "single");
  const [slot, setSlot] = useState<number | null>(teamMode ? target.slot ?? null : null);
  const [name, setName] = useState("");
  const [reading, setReading] = useState<Reading | null>(null);
  const [busy, setBusy] = useState(false);
  const vocabReady = dex.status === "ready" && items.status === "ready" && natures.status === "ready";

  const read = async () => {
    if (busy || !vocabReady || !text.trim()) return;
    setBusy(true);
    try {
      const limit = teamMode ? TEAM_SIZE : BOX_PASTE_LIMIT;
      const result = await membersFromPaste(text, {
        dex: dex.data, items: items.data, natures: natures.data, adapter,
      }, limit, format);
      result.members = result.members.map((row) => ({ ...row,
        member: toTeamMember(row.member, state.vocabulary) ?? row.member }));
      const overflow = teamMode && parsePokepaste(text).mons.length > limit;
      const doc = teamMode && result.members.length
        ? toTeamDoc({ format, pokemon: result.members.map(({ member }) => member) }, format, state.vocabulary) : null;
      setReading({ ...result, overflow, doc });
    } catch {
      toast({ text: t("lib.code.failed") });
    } finally {
      setBusy(false);
    }
  };

  const plan = reading?.doc ? planTeamSave(state, reading.doc) : null;
  const targetSlot = slot ?? freeTeamSlots(state, format)[0] ?? 0;
  const occupant = state.teams.find((team) => team.format === format && team.slot === targetSlot) ?? null;
  const box = boxById(state);
  const free = freeCells(state).length;
  const found = reading?.members.length ?? 0;
  const noRoom = plan !== null && plan.needed > plan.free;

  const saveAsTeam = async () => {
    if (!reading?.doc) return;
    setBusy(true);
    try {
      const result = await saveTeam(reading.doc, { origin: "import", snapshot, name },
        { slot: targetSlot, allowDuplicate: true });
      if (result.status !== "saved") {
        toast({ text: result.status === "no-room"
          ? fill(t("lib.paste.noRoom"), { n: result.needed, free: result.free }) : t("lib.toast.failed") });
        setBusy(false);
        return;
      }
      toast({
        text: fill(t(result.replaced ? "lib.toast.replaced" : "lib.toast.saved"), { n: targetSlot + 1 }),
        action: { label: t("lib.toast.undo"), run: () => void undoSaveTeam(result) },
      });
      onSaved?.(result.record.id, format);
      onClose();
    } catch {
      toast({ text: t("lib.toast.failed") });
      setBusy(false);
    }
  };

  const saveToBox = async () => {
    if (!reading?.members.length || target.mode !== "box") return;
    setBusy(true);
    try {
      const { added, left } = await addManyToBox(reading.members.map(({ member }) => member),
        { origin: "import", snapshot }, target.at);
      toast({
        text: left ? fill(t("lib.box.addedSome"), { n: added.length, left })
          : fill(t("lib.box.addedMany"), { n: added.length }),
        action: added.length ? {
          label: t("lib.toast.undo"),
          run: () => { for (const record of added) void removeBoxRecord(record.id); },
        } : undefined,
      });
      if (added[0]) onSaved?.(added[0].id, format);
      onClose();
    } catch {
      toast({ text: t("lib.toast.failed") });
      setBusy(false);
    }
  };

  const footer = !reading ? (
    <>
      <button type="button" className="second-btn" onClick={onClose}>{t("lib.cancel")}</button>
      <button type="button" className="primary-btn" disabled={busy || !vocabReady || !text.trim()}
              onClick={() => void read()}>
        {busy ? t("lib.paste.reading") : t("lib.paste.read")}
      </button>
    </>
  ) : (
    <>
      <button type="button" className="second-btn" onClick={() => setReading(null)}>
        {t("lib.paste.edit")}
      </button>
      {found > 0 && !teamMode && (
        <button type="button" className="primary-btn" disabled={busy || free === 0} onClick={() => void saveToBox()}>
          {fill(t("lib.paste.addBox"), { n: Math.min(found, free) })}
        </button>
      )}
      {found > 0 && teamMode && (
        <button type="button" className={occupant ? "primary-btn lib-danger" : "primary-btn"}
                disabled={busy || noRoom} onClick={() => void saveAsTeam()}>
          {fill(t(occupant ? "lib.paste.replace" : "lib.paste.saveTo"), { n: targetSlot + 1 })}
        </button>
      )}
    </>
  );

  return (
    <Modal title={t(teamMode ? "lib.paste" : "lib.box.fromText")} onClose={onClose} width={640}
           className="lib-dialog" footer={footer}>
      {!reading ? (
        <div className="lib-form">
          <p className="muted lib-dialog-hint">
            {t("lib.paste.hint")}{!teamMode && <> {t("lib.box.fromTextHint")}</>}
          </p>
          {teamMode && (
            <div className="lib-form-row">
              <label>
                <span>{t("lib.formatPick")}</span>
                <SegmentedControl kind="radio" value={format} className="seg" ariaLabel={t("lib.formatPick")}
                  onChange={(next) => { setFormat(next); setSlot(null); }}
                  items={(["single", "double"] as const).map((id) => ({ id, label: t(`format.${id}`), disabled: busy }))} />
              </label>
              <label className="grow">
                <span>{t("lib.field.name")}</span>
                <input value={name} maxLength={80} placeholder={t("lib.field.namePlaceholder")}
                       onChange={(event) => setName(event.target.value)} />
              </label>
            </div>
          )}
          <label>
            <span>{t("lib.paste.text")}</span>
            <textarea className="lib-paste-text" rows={12} value={text} autoFocus spellCheck={false} disabled={busy}
                      onChange={(event) => setText(event.target.value)}
                      placeholder={t("lib.paste.hint")} />
          </label>
        </div>
      ) : (
        <div className="lib-reading">
          {found === 0 ? (
            <p className="notice">{t("lib.paste.none")}</p>
          ) : (
            <ul className="lib-preview">
              {reading.members.map(({ entry, member }, index) => (
                <li key={`${member.species}-${index}`}>
                  <GameImage assetKey={`pokemon:${entry.slug}`} role="dense" alt="" />
                  <span className="lib-preview-name">
                    <span>{displayName(entry, lang)}</span>
                    {plan?.reuse[index] && <span className="lib-reuse">{t("lib.paste.reuse")}</span>}
                  </span>
                  <span className="lib-preview-sub muted">
                    {member.item ? localName(names.item, member.item, lang) : "—"}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {reading.unresolved.length > 0 && (
            <p className="notice lib-warn">
              {fill(t("lib.paste.unresolved"), { names: reading.unresolved.join(", ") })}
            </p>
          )}
          {reading.overflow && <p className="notice lib-warn">{t("lib.paste.limit")}</p>}
          {reading.rescaledEvs && <p className="notice">{t("lib.paste.rescaled")}</p>}
          {!teamMode && found > free && (
            <p className="notice lib-warn">{fill(t("lib.paste.boxRoom"), { free })}</p>
          )}
          {teamMode && plan && (
            <>
              <TeamSlotPicker state={state} format={format} value={targetSlot} onChange={setSlot}
                              label={t("lib.paste.target")} />
              {occupant && (
                <p className="notice lib-warn">
                  {fill(t("lib.paste.slotTaken"), {
                    n: targetSlot + 1, name: teamLabel(occupant.name, teamDocOf(occupant, box)).text,
                  })}
                </p>
              )}
              {plan.duplicate && (
                <p className="notice">
                  {fill(t("lib.paste.duplicate"), { format: t(`format.${format}`), n: plan.duplicate.slot + 1 })}
                </p>
              )}
              <p className={`notice${noRoom ? " lib-warn" : ""}`}>
                {fill(t(noRoom ? "lib.paste.noRoom" : "lib.paste.cells"), { n: plan.needed, free: plan.free })}
              </p>
            </>
          )}
        </div>
      )}
    </Modal>
  );
}
