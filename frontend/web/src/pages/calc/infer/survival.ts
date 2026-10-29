/** Censor a full-HP hit at one HP, consistently in the web and local inference adapters. */
export function survivesHitAtOne(attackerAbility: string, attackerAbilityOn: boolean | null | undefined,
  defenderItem: string, defenderAbility: string, defenderAbilityOn: boolean | null | undefined, move: string): boolean {
  // Klutz suppresses held-item effects even though the uncensored engine rolls still look lethal.
  if (defenderItem === "Focus Sash" && defenderAbility !== "Klutz") return true;
  const bypass = (attackerAbilityOn !== false && ["Mold Breaker", "Teravolt", "Turboblaze"].includes(attackerAbility))
    || ["Sunsteel Strike", "Moongeist Beam", "Photon Geyser"].includes(move);
  return defenderAbility === "Sturdy" && defenderAbilityOn !== false && !bypass;
}
