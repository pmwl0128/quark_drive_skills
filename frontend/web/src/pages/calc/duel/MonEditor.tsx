/** One combatant's editable build: species + forme, ability, nature, item, status and the SP /
 * boost / final-stat table with current HP. The four moves are edited in the result grid above it,
 * where their damage is read. The portrait, types and Mega state live with the build here.
 *
 * The stat table shows base, investment and RESULT side by side because that is the number the calc
 * actually used — a spread that reads "32 Spe" tells you nothing about whether it clears a
 * benchmark, and re-deriving it in your head is exactly the arithmetic this page exists to remove. */
import type { FormatId, NatureDto } from "@pokemon-champions/protocol";
import { STAT_KEYS } from "@pokemon-champions/protocol";
import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { BuildSetSummary, type BuildCardOption } from "../../../components/BuildPicker.tsx";
import { EntityHover } from "../../../components/EntityHover.tsx";
import { GameImage } from "../../../components/GameImage.tsx";
import { PLACEHOLDERS } from "../../../assets/icons.ts";
import { KeepMonButton } from "../../../components/KeepMonButton.tsx";
import { displayName, useLang } from "../../../i18n.ts";
import { useCalcT } from "../messages.ts";
import type { DexIndexEntry } from "../../../runtime/adapter.ts";
import type { ItemRef } from "../../../runtime/projection.ts";
import { BuildPickerButton, ItemCombo } from "../../../components/build/inputs.tsx";
import { megaFor } from "../../../lib/lookup.ts";
import { clampNum, buildConfigSig, spSum, spSumClass, type BuildOption } from "../../../lib/buildState.ts";
import { useMetaUsage } from "../../../lib/metaUsage.ts";
import {
  boostedStat, curHPOf, effectiveEntry, finalStat, makeMon,
  maxHPOf, monToMember, type MonState,
} from "./state.ts";
import { ABILITY_NEEDS_TRIGGER } from "../../../lib/calc-engine/semantics.ts";

import { MonNameRow, MonPortrait } from "../../../components/build/MonIdentity.tsx";
import { StageSelect } from "../../../components/build/StageSelect.tsx";
import { SpControl } from "../../../components/build/SpControl.tsx";
import { NatureCombo } from "../../../components/build/NatureCombo.tsx";
import { ItemFieldLabel, StatusFieldLabel } from "../../../components/build/FieldLabels.tsx";
import { AdaptiveSelect } from "../../../components/AdaptiveSelect.tsx";
import { StatusSelect } from "../../../components/build/StatusSelect.tsx";
import { SpreadPresets } from "./SpreadPresets.tsx";
import "./styles.stats.css";

export function MonEditor({
  mon, setMon, dex, natures, items, format, onSetPick, keep = true,
}: {
  mon: MonState;
  setMon: Dispatch<SetStateAction<MonState>>;
  dex: DexIndexEntry[];
  natures: NatureDto[];
  items: ItemRef[];
  format: FormatId;
  onSetPick: (set: BuildOption, index: number) => void;
  /** Offer the single-mon "+" (keep in a box). A card whose build is a known quantity it merely
   * reads from (the set-inference tool's own side) leaves it out. */
  keep?: boolean;
}) {
  const { lang } = useLang();
  const t = useCalcT();
  const literal = dex.find((e) => e.slug === mon.slug);
  const mega = literal?.isMega ? literal : megaFor(mon.slug, mon.item, dex, items);
  const entry = effectiveEntry(mon, dex, items);
  const usage = useMetaUsage(mon.slug, [format]);
  // The stone is what activates a Mega, so a base species holding one IS that Mega for every number
  // on the page. The forme control and the item lock therefore key off the EFFECTIVE form, not the
  // literal pick — otherwise the card computes Mega Garchomp Z while its own fields still read
  // "Garchomp / no forme / editable item", which is three claims the result contradicts.
  const requiredStone = mega
    ? items.find((i) => i.requiredBy?.includes(mega.name)) : undefined;

  // A Mega form picked BY NAME requires its stone — pin the item and lock the input.
  useEffect(() => {
    if (requiredStone && mon.item !== requiredStone.name) {
      setMon((s) => ({ ...s, item: requiredStone.name }));
    }
  }, [requiredStone?.name, mon.slug]);

  // The ability selector must describe the form the engine will calculate, so a stone swap or an
  // explicit Mega pick replaces an incompatible base ability immediately rather than relying on an
  // invisible request-time correction.
  useEffect(() => {
    if (mega && !mega.abilities.some((a) => a.name === mon.ability)) {
      setMon((s) => ({ ...s, ability: mega.abilities[0]?.name ?? "" }));
    }
  }, [mega?.slug, mon.item]);

  /** Which way this mon's nature bends a stat — "" when it does not touch it. */
  const nature = natures.find((n) => n.name === mon.nature);
  const natureTone = (key: string) =>
    (nature?.upStat === key && nature.downStat !== key ? "up"
      : nature?.downStat === key && nature.upStat !== key ? "down" : "");

  const maxHP = maxHPOf(mon, dex, items);
  const curHP = curHPOf(mon, maxHP);
  const spTotal = spSum(mon.sps);
  const needsTrigger = !!mon.ability && ABILITY_NEEDS_TRIGGER.has(mon.ability);
  // The cards the button last loaded, mirrored here only so the build below can be recognised as
  // one of them; the popover itself owns its own loading.
  const [setOptions, setSetOptions] = useState<BuildOption[]>([]);
  const currentBuildSig = buildConfigSig(mon);
  const matchedSet = setOptions.find((option) => buildConfigSig(option.modal) === currentBuildSig);
  const matchedSetIndex = matchedSet ? setOptions.indexOf(matchedSet) : -1;
  const currentSetKey = matchedSet?.key;

  // A manually entered build can still be an exact environment card. Once the picker has loaded
  // enough facts to prove that identity, retain it for the compact hover cards; any later field edit
  // changes the signature and the display automatically falls back to "custom".
  useEffect(() => {
    if (!matchedSet) return;
    const buildRef = {
      key: matchedSet.key,
      source: matchedSet.source,
      coverage: matchedSet.coverage,
      isModal: matchedSet.isModal,
      labelIndex: Math.max(0, matchedSetIndex),
      signature: currentBuildSig,
    };
    setMon((current) => buildConfigSig(current) === currentBuildSig
      && (current.buildRef?.key !== buildRef.key
        || current.buildRef.source !== buildRef.source
        || current.buildRef.coverage !== buildRef.coverage
        || current.buildRef.isModal !== buildRef.isModal
        || current.buildRef.labelIndex !== buildRef.labelIndex
        || current.buildRef.signature !== buildRef.signature)
      ? { ...current, buildRef } : current);
  }, [currentBuildSig, matchedSet?.key, matchedSetIndex, setMon]);

  const name = entry ? displayName(entry, lang) : "";
  const member = () => monToMember(mon, dex);
  const editorActions = (
    <>
      <BuildPickerButton slug={mon.slug} format={format} currentKey={currentSetKey}
        onOptions={setSetOptions} onPick={onSetPick} />
      {entry && keep && <KeepMonButton member={member} name={name} origin="calc" />}
    </>
  );

  return (
    <div className="mon-editor">
      <div className="mon-id">
        <MonPortrait entry={entry} name={name} member={member} />
        <MonNameRow slug={mon.slug} item={mon.item} dex={dex} items={items}
          actions={editorActions}
          // An unresolved edit claims even an initially empty slot before the demo arrives.
          onSpecies={(slug) => setMon((s) => (slug && s.slug === slug ? s : makeMon(slug)))}
          // A Mega picked here is stored as the Mega species; its stone is pinned by the effect
          // above, so the two controls can never disagree again.
          onForme={(slug, dropStone) => setMon((s) => ({ ...s, slug, ...(dropStone ? { item: "" } : {}) }))} />
      </div>

      <div className="mon-build-row">
        <label>{t("calc.ability")}
          <AdaptiveSelect value={mon.ability}
            onValueChange={(value) => setMon((s) => ({ ...s, ability: value, abilityOn: null }))}>
            <option value="">—</option>
            {(mega ?? literal)?.abilities.map((a) => (
              <option key={a.name} value={a.name}>{displayName(a, lang)}</option>
            ))}
          </AdaptiveSelect>
        </label>
        <label>{t("calc.nature")}
          <NatureCombo value={mon.nature} natures={natures} usage={usage}
            onChange={(nature) => setMon((s) => ({ ...s, nature }))} />
        </label>
        <label><ItemFieldLabel value={mon.item} items={items} />
          <ItemCombo value={mon.item} items={items} disabled={!!requiredStone} usage={usage}
            onChange={(item) => setMon((s) => s.item === item ? s : { ...s, item })} />
        </label>
        <label><StatusFieldLabel value={mon.status} />
          <StatusSelect value={mon.status} onChange={(status) => setMon((s) => ({ ...s, status }))} />
        </label>
      </div>

      {needsTrigger && (
        <label className="inline-check" title={t("calc.abilityOnHint")}>
          <input type="checkbox" checked={mon.abilityOn === true}
            onChange={(e) => setMon((s) => ({ ...s, abilityOn: e.target.checked ? true : null }))} />
          <span>{t("calc.abilityOn")}</span>
        </label>
      )}

      <div className="stat-table stat-investment-table">
        <div className="stat-table-head">
          <span>{t("calc.stat")}</span>
          <span>{t("calc.base")}</span>
          <div className="stat-head-investment">
            <span className="stat-head-sp">SP
              <span className={`sp-sum num ${spSumClass(spTotal)}`}>{spTotal}/66</span>
            </span>
            <SpreadPresets base={entry?.stats} build={mon} natures={natures}
              onPick={(preset) => setMon((current) => ({ ...current, ...preset }))} />
          </div>
          <span>{t("calc.boosts")}</span>
          <span>{t("calc.final")}</span>
          <span>{t("calc.current")}</span>
        </div>
        {STAT_KEYS.map((key) => (
          <div className="stat-table-row" key={key}>
            <span className="stat-name">{key.toUpperCase()}</span>
            <span className="num muted">{entry?.stats[key] ?? "—"}</span>
            <SpControl stat={key} value={mon.sps[key] ?? 0}
              onChange={(value) => {
                setMon((s) => (s.sps[key] ?? 0) === value ? s
                  : { ...s, sps: { ...s.sps, [key]: value } });
              }} />
            {key === "hp"
              ? <span className="stat-hp-percent">
                  <input type="number" min={1} max={100} step={1} inputMode="numeric" className="num"
                    value={maxHP > 0 ? Math.round(curHP / maxHP * 100) : 0} disabled={!maxHP}
                    aria-label={`${t("calc.curHP")} %`} onFocus={(event) => event.currentTarget.select()}
                    onChange={(event) => {
                      const pct = clampNum(event.target.value, 1, 100);
                      setMon((s) => ({ ...s, curHP: pct === 100 ? null : Math.max(1, Math.round(maxHP * pct / 100)) }));
                    }} />
                  <span aria-hidden>%</span>
                </span>
              : <StageSelect value={mon.boosts[key] ?? 0} label={`${key.toUpperCase()} ${t("calc.boosts")}`}
                  onChange={(stage) => setMon((s) => (s.boosts[key] ?? 0) === stage ? s
                    : { ...s, boosts: { ...s.boosts, [key]: stage } })} />}
            {/* The stat value is what the BUILD computes to, and it says why it is that number:
                which way the nature bent it, and whether the investment is already at the 32-point
                ceiling. HP's is the maximum — a plain fact about the build, like every other row. */}
            <span className={`num stat-final ${key === "hp" ? "" : natureTone(key)}`
              + ((mon.sps[key] ?? 0) >= 32 ? " maxed" : "")}>
              {(key === "hp" ? maxHP : finalStat(mon, entry, natures, key)) || "—"}
            </span>
            {/* The current value is what the mon is playing at RIGHT NOW: for HP a quantity that
                only the reader knows (so it is the one editable cell), for everything else the
                build's value put through its boost stage. Coloured by the STAGE, not the nature —
                this column answers "what did the battle do to it", which is a different question
                from the one the column beside it answers. */}
            {key === "hp"
              ? <input type="number" className="num stat-cur hp-cur"
                  min={1} max={Math.max(1, maxHP)} value={curHP} disabled={!maxHP}
                  aria-label={t("calc.curHP")} title={`${t("calc.curHP")} / ${maxHP}`}
                  onChange={(e) => setMon((s) => ({
                    ...s, curHP: clampNum(e.target.value, 1, Math.max(1, maxHP)),
                  }))} />
              : (() => {
                  const stage = mon.boosts[key as "atk"] ?? 0;
                  const value = finalStat(mon, entry, natures, key);
                  return (
                    <span className={`num stat-cur${stage > 0 ? " up" : stage < 0 ? " down" : ""}`}>
                      {value ? boostedStat(value, stage) : "—"}
                    </span>
                  );
                })()}
          </div>
        ))}
      </div>

    </div>
  );
}

/** Compact-card identity for a live calculator mon. Environment provenance is shown only while the
 * current item/ability/nature/SP/moves still match the applied card exactly. */
export function buildCardOptionForMon(
  mon: MonState, entry: DexIndexEntry, dex: DexIndexEntry[],
): BuildCardOption {
  const literal = dex.find((candidate) => candidate.slug === mon.slug);
  const signature = buildConfigSig(mon);
  const source = mon.buildRef?.signature === signature ? mon.buildRef : null;
  return {
    key: source?.key ?? `custom:${entry.slug}`,
    source: source?.source ?? "custom",
    coverage: source?.coverage ?? null,
    isModal: source?.isModal ?? false,
    labelIndex: source?.labelIndex,
    set: {
      species: literal?.isMega ? literal.baseSpecies ?? entry.name : literal?.name ?? entry.name,
      runForm: entry.name,
      ability: mon.ability || null,
      item: mon.item || null,
      nature: mon.nature || null,
      moves: [...mon.moves.filter(Boolean)],
      sps: { ...mon.sps },
    },
  };
}

/** Small read-only echo of a mon, used by the team strips. */
export function MonAvatar({ mon, dex, items, active, onClick, onRemove, title, slot }: {
  mon: MonState;
  dex: DexIndexEntry[];
  items: ItemRef[];
  active: boolean;
  onClick: () => void;
  onRemove?: () => void;
  title?: string;
  /** Roster position for a library drop, independent of which member is currently selected. */
  slot?: number;
}) {
  const { lang } = useLang();
  const t = useCalcT();
  const entry = effectiveEntry(mon, dex, items);
  const name = entry ? displayName(entry, lang) : t("calc.emptySlot");
  const hasBuild = !!entry && !!(mon.ability || mon.item || mon.nature
    || Object.values(mon.sps).some((value) => (value ?? 0) > 0) || mon.moves.some(Boolean));
  const preview = hasBuild && entry ? buildCardOptionForMon(mon, entry, dex) : null;
  const image = entry
    ? <GameImage assetKey={entry.key} role="dense" alt={name} className="mini" />
    : <img className="mini team-chip-empty" src={PLACEHOLDERS.pokemon} alt="" aria-hidden />;
  return (
    <span className={`team-chip${active ? " on" : ""}`} data-team-slot={slot}>
      <button type="button" className="team-chip-btn" onClick={onClick}
        aria-pressed={active} title={title ?? name}>
        {preview
          ? <EntityHover kind="spread" name="" link={false} passive
              previewAddon={<BuildSetSummary option={preview} index={0} />}>{image}</EntityHover>
          : image}
      </button>
      {onRemove && (
        <button type="button" className="team-chip-x" onClick={onRemove}
          aria-label={`${t("a11y.remove")} ${name}`}>✕</button>
      )}
    </span>
  );
}
