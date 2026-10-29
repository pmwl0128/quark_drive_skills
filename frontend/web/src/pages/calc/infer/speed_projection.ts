/** Turn engine speed readings into the ranges shown for each surviving nature. */
export interface SpeedProbe { nature: string; lo: number; hi: number; spHi: number }
export interface SpeedReadPlan { mine: number | null; probes: SpeedProbe[] }
export interface SpeedRange { nature: string; lo: number; hi: number; spHi: number }
export interface SpeedProjection { mine: number | null; ranges: SpeedRange[]; incomplete: boolean }

export function projectSpeeds(plan: SpeedReadPlan, speeds: Array<number | null>): SpeedProjection {
  const speed = (index: number | null) => index === null ? null : speeds[index] ?? null;
  const ranges = new Map<string, SpeedRange>();
  let incomplete = plan.mine !== null && speed(plan.mine) === null;
  for (const probe of plan.probes) {
    const lo = speed(probe.lo), hi = speed(probe.hi);
    if (lo === null || hi === null) { incomplete = true; continue; }
    const previous = ranges.get(probe.nature);
    ranges.set(probe.nature, { nature: probe.nature, lo: Math.min(lo, previous?.lo ?? lo),
      hi: Math.max(hi, previous?.hi ?? hi), spHi: Math.max(probe.spHi, previous?.spHi ?? 0) });
  }
  return { mine: speed(plan.mine), ranges: [...ranges.values()], incomplete };
}
