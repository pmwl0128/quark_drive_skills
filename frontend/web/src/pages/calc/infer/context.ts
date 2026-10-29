/** The bridge between the calc page's roster and the inference model: what one observation stores,
 * how a hit (observed or predicted) becomes engine requests for every hypothesis, and how an
 * opponent's hidden state is cut down to what the environment actually plays. */
import type { DamageRequestDto, MetaDetailDto, NatureDto } from "@pokemon-champions/protocol";
import type { DexIndexEntry } from "../../../runtime/adapter.ts";
import type { ItemRef } from "../../../runtime/projection.ts";
import { withMega } from "../../../lib/buildState.ts";
import {
  damageRequest, maxHPOf, natureMult, type FieldState, type MonState,
} from "../duel/state.ts";
import { SP_MAX } from "../../../lib/battle.ts";
import { survivesHitAtOne } from "./survival.ts";
import { frameIssue } from "./move_readability.ts";
import {
  type BulkKey, type FoeSpace, type InferKey, type Mult, type OffKey, type SpaceNature,
  type SpaceOption,
} from "./model.ts";

/** Environment share (percent) below which a nature, item or ability is not considered. */
const PRUNE_PERCENT = 1;

export type ObservationKind = "bulk" | "offense";

/** One hit as it happened. `bulk`: our move into them, their HP in displayed percent. `offense`:
 * their move into us, our HP exactly. Both builds and the battle frame are frozen at recording. */
export interface Observation {
  id: string;
  kind: ObservationKind;
  foeId: string;
  mineId: string;
  move: string;
  before: number;
  after: number;
  crit: boolean;
  /** A spread move that hit only this one target (doubles): no 0.75 reduction. */
  singleTarget: boolean;
  field: FieldState;
  mineBoosts: MonState["boosts"];
  foeBoosts: MonState["boosts"];
  mineStatus: string;
  foeStatus: string;
  enabled: boolean;
  mineSnapshot?: MonState;
  foeSnapshot?: MonState;
}

/** The two combatants and the conditions of one hit, with our side already carrying its frame. */
export interface HitFrame {
  mine: MonState;
  foe: MonState;
  move: string;
  field: FieldState;
  crit: boolean;
  singleTarget: boolean;
  /** Their displayed HP before our hit (bulk only); below 100 turns full-HP abilities off. */
  foeBefore: number | null;
  /** Our HP before their hit (offense only). */
  mineBefore: number | null;
}

export function frameOf(observation: Observation, mine: MonState, foe: MonState): HitFrame {
  return {
    mine: { ...(observation.mineSnapshot ?? mine), boosts: { ...observation.mineBoosts }, status: observation.mineStatus },
    foe: { ...(observation.foeSnapshot ?? foe), boosts: { ...observation.foeBoosts }, status: observation.foeStatus },
    move: observation.move,
    field: observation.field,
    crit: observation.crit,
    singleTarget: observation.singleTarget,
    foeBefore: observation.kind === "bulk" ? observation.before : null,
    mineBefore: observation.kind === "offense" ? observation.before : null,
  };
}

/** The live pair on screen, for predictions: from full HP on both sides. */
export function liveFrame(mine: MonState, foe: MonState, move: string, field: FieldState): HitFrame {
  return { mine: { ...mine, curHP: null }, foe, move, field, crit: false, singleTarget: false,
           foeBefore: null, mineBefore: null };
}

export function frameIssueOf(frame: HitFrame, kind: ObservationKind, abilities: string[], kit: RequestKit): string | null {
  const foe = withMega(frame.foe, kit.dex, kit.items);
  return frameIssue({ kind, foeAbilities: abilities,
    foeBaseHP: foe.entry?.stats.hp ?? 0, foeCurrentHP: frame.foe.curHP });
}

// -- which moves can be read ---------------------------------------------------------------
export { bulkUse, offenseUse } from "./move_readability.ts";

// -- requests ---------------------------------------------------------------------------------

export interface RequestKit {
  dex: DexIndexEntry[];
  items: ItemRef[];
  natures: NatureDto[];
}

/** One nature per (stat, multiplier): the engine only needs the multiplier on the stat in play. */
function natureFor(natures: NatureDto[], key: InferKey, mult: Mult): string {
  const pick = mult === 1
    ? natures.find((nature) => !nature.upStat || nature.upStat === nature.downStat)
    : natures.find((nature) => mult === 1.1
      ? nature.upStat === key && nature.downStat !== key
      : nature.downStat === key && nature.upStat !== key);
  return pick?.name ?? "";
}

/** The item a Mega holds — its own stone, whatever the roster slot says — or null when the item is a
 * hypothesis. */
export function fixedItem(foe: MonState, kit: RequestKit): string | null {
  const { entry, mega } = withMega(foe, kit.dex, kit.items);
  if (!mega || !entry) return null;
  return kit.items.find((item) => item.requiredBy?.includes(entry.name))?.name ?? foe.item;
}

/** Display hint only. All items still reach the engine, including move-specific interactions. */
export function isPowerItem(item: string, kit: RequestKit): boolean {
  return ["Life Orb", "Expert Belt", "Muscle Band", "Wise Glasses", "Light Ball"].includes(item)
    || kit.items.find((ref) => ref.name === item)?.category === "type_boost";
}

/** Our move into them, with their ability / nature multiplier / SP in `key` set and everything else
 * about their build blank (their HP SP does not change the damage). */
export function bulkRequest(frame: HitFrame, key: BulkKey, ability: string, mult: Mult, sp: number,
                            kit: RequestKit, item = ""): DamageRequestDto | null {
  const foe: MonState = {
    ...frame.foe,
    item: fixedItem(frame.foe, kit) ?? item,
    ability, nature: natureFor(kit.natures, key, mult), sps: { [key]: sp },
    curHP: null,
  };
  if (frame.foeBefore !== null && frame.foeBefore < 100) {
    foe.curHP = Math.max(1, maxHPOf(foe, kit.dex, kit.items) - 1);
  }
  return damageRequest(frame.mine, foe, frame.move, { ...frame.field, switchInDrops: false }, "a",
    kit.dex, kit.items, { crit: frame.crit, singleTarget: frame.singleTarget });
}

export function offenseRequest(frame: HitFrame, key: OffKey, item: string, ability: string, mult: Mult,
                               sp: number, kit: RequestKit): DamageRequestDto | null {
  const foe: MonState = {
    ...frame.foe,
    item: fixedItem(frame.foe, kit) ?? item,
    ability, nature: natureFor(kit.natures, key, mult), sps: { [key]: sp },
    curHP: frame.foe.curHP,
  };
  const mine: MonState = frame.mineBefore === null ? frame.mine
    : { ...frame.mine, curHP: frame.mineBefore };
  return damageRequest(foe, mine, frame.move, { ...frame.field, switchInDrops: false }, "b",
    kit.dex, kit.items, { crit: frame.crit, singleTarget: frame.singleTarget });
}

/** Every request a hit needs across the hypotheses. */
export function hitRequests(frame: HitFrame, use: { kind: ObservationKind; key: InferKey },
                            plan: { items: string[]; abilities: string[]; mults: Record<InferKey, Mult[]> },
                            kit: RequestKit): DamageRequestDto[] {
  const out: DamageRequestDto[] = [];
  const reps = [...new Set(plan.items.map((item) => fixedItem(frame.foe, kit)
    ?? item))];
  for (const ability of plan.abilities) {
    for (const mult of plan.mults[use.key]) {
      for (let sp = 0; sp <= SP_MAX; sp++) {
        if (use.kind === "bulk") {
          for (const item of reps) {
            const request = bulkRequest(frame, use.key as BulkKey, ability, mult, sp, kit, item);
            if (request) out.push(request);
          }
        } else {
          for (const item of reps) {
            const request = offenseRequest(frame, use.key as OffKey, item, ability, mult, sp, kit);
            if (request) out.push(request);
          }
        }
      }
    }
  }
  return out;
}

/** Raw engine rolls precede Focus Sash/Sturdy survival. This is a censored damage reading,
 * not an exact one-HP roll. Ability bypass does not bypass a held Focus Sash. */
export function survivesAtOne(attacker: MonState, defender: MonState, move: string,
  item: string, ability: string, kit: RequestKit): boolean {
  const attackAbility = withMega(attacker, kit.dex, kit.items).state.ability;
  return survivesHitAtOne(attackAbility, attacker.abilityOn, item, ability, defender.abilityOn, move);
}

// -- the hidden state ----------------------------------------------------------------------------

export interface KnownFacts {
  item?: string;
  ability?: string;
}

export interface SpaceInfo {
  space: FoeSpace;
  /** The environment cut applied (false: no usage data, every nature / legal ability kept). */
  pruned: boolean;
  /** Item and ability choices for the "confirmed" pickers: the environment's lists, uncut. */
  itemChoices: string[];
  abilityChoices: string[];
}

const share = (percentage: number | null) => (percentage ?? 0) / 100;

/** The opponent's hidden state: natures, items and abilities at >= 1% of the environment (the top-10
 * panels; anything past them is not considered), unless the item or ability is confirmed or fixed by
 * a Mega form. */
export function buildSpace(foe: MonState, detail: MetaDetailDto | null, known: KnownFacts,
                           kit: RequestKit): SpaceInfo | null {
  const { entry, mega } = withMega(foe, kit.dex, kit.items);
  if (!entry) return null;
  const kept = <T extends { percentage: number | null }>(rows: T[]) =>
    rows.filter((row) => (row.percentage ?? 0) >= PRUNE_PERCENT);
  const multOf = (name: string): SpaceNature["mult"] => ({
    atk: natureMult(kit.natures, name, "atk"), def: natureMult(kit.natures, name, "def"),
    spa: natureMult(kit.natures, name, "spa"), spd: natureMult(kit.natures, name, "spd"),
  });

  const natureRows = detail ? kept(detail.panels.natures) : [];
  const natures: SpaceNature[] = natureRows.length
    ? natureRows.map((row) => ({ name: row.name, prior: share(row.percentage), mult: multOf(row.name) }))
    : kit.natures.map((nature) => ({ name: nature.name, prior: 1 / kit.natures.length, mult: multOf(nature.name) }));

  const isStone = (name: string) => kit.items.find((item) => item.name === name)?.category === "mega_stone";
  const itemChoices = (detail?.panels.items ?? []).map((row) => row.name).filter((name) => !isStone(name));
  let items: SpaceOption[];
  if (mega) items = [{ name: fixedItem(foe, kit) ?? foe.item, prior: 1 }];
  else if (known.item) items = [{ name: known.item, prior: 1 }];
  else {
    const rows = detail ? kept(detail.panels.items).filter((row) => !isStone(row.name)) : [];
    items = rows.length ? rows.map((row) => ({ name: row.name, prior: share(row.percentage) }))
      : [{ name: "", prior: 1 }];
  }

  const legal = entry.abilities.map((ability) => ability.name);
  let abilities: SpaceOption[];
  if (mega) abilities = [{ name: legal[0] ?? "", prior: 1 }];
  else if (known.ability) abilities = [{ name: known.ability, prior: 1 }];
  else {
    const rows = detail ? kept(detail.panels.abilities).filter((row) => legal.includes(row.name)) : [];
    abilities = rows.length ? rows.map((row) => ({ name: row.name, prior: share(row.percentage) }))
      : legal.map((name) => ({ name, prior: 1 / Math.max(1, legal.length) }));
  }

  return {
    space: {
      base: { hp: entry.stats.hp, atk: entry.stats.atk, def: entry.stats.def,
              spa: entry.stats.spa, spd: entry.stats.spd },
      natures, items, abilities,
    },
    pruned: natureRows.length > 0,
    itemChoices,
    abilityChoices: legal,
  };
}
