/**
 * Capabilities handshake (frontend/design.md §3): the bridge / online API assembles this once at
 * startup; the SPA fetches it before rendering. Three version fields serve distinct roles —
 * `webProtocolVersion` is THIS contract's version, `uiBuildId` fingerprints the SPA build,
 * `skillFingerprints` identify the four installed skills' data/code. Capabilities only drive
 * front-end visibility; the server whitelists every endpoint independently (§7.5).
 */
import { z } from "zod";
import { FormatIdSchema } from "./common.ts";

// Bumped 1 → 2: ItemPanelEntryDto.key went from absent to REQUIRED (a breaking DTO change). Without
// a bump, a projection built by an older mapper (no item keys) would share a deploymentId with a
// newer SPA and pass the startup handshake, then throw at MetaDetailDtoSchema.parse. The version gate
// now rejects such a projection up front (external audit 2026-07-14). Bump on any breaking DTO/mapper
// change, not just skill-data changes.
// Bumped 2 → 3: OppCacheDto.sets went REQUIRED → optional so lean projections can omit it. A pre-3
// SPA (sets required) can no longer parse every v3 projection; the version gate makes that breaking
// contract change explicit. The current public projection includes de-attributed processed sets.
// Bumped 3 → 4: the default KO page now requires the lean `oppko_<format>.json` projection/API view
// and lazy-loads the old full cache for detail. A v4 SPA paired with a v3 projection would 404.
// Bumped 4 → 5: meta detail popovers now require the lazy per-Pokemon usage-trend endpoint/projection.
// Bumped 6 → 7: calculator defaults and matchup build previews require the lightweight
// `oppsets_<format>.json` / `view=sets` resource instead of loading the full matchup matrix.
// Bumped 7 → 8: environment.sourceFingerprints is REQUIRED — the per-format data identity (meta
// panels + real-team library) that derived views were built from, independent of code changes.
// Bumped 8 → 9: reference-grid result cards require per-species detail shards / view=details.
export const WEB_PROTOCOL_VERSION = "9";

export const SKILL_IDS = ["dex", "meta", "calc", "team"] as const;
export type SkillId = (typeof SKILL_IDS)[number];

/** Capability ids the current SPA knows how to surface. The wire format allows unknown ids
 * (forward compatibility): a newer server may advertise features an older SPA simply ignores. */
export const KNOWN_CAPABILITIES = [
  "dex.query", "meta.rank", "meta.detail", "meta.trend",
  "calc.damage", "calc.speedline",
  "team.validate", "team.uep", "team.matchup", "team.tune", "team.code",
  "llm.qa", "llm.builder",
] as const;
export type KnownCapability = (typeof KNOWN_CAPABILITIES)[number];

export const SourceFingerprintsSchema = z.object({
  meta: z.string().min(1),
  teamLibrary: z.string().min(1),
});
export type SourceFingerprints = z.infer<typeof SourceFingerprintsSchema>;

export const CapabilitiesSchema = z.object({
  webProtocolVersion: z.string().min(1),
  deploymentId: z.string().min(1),
  uiBuildId: z.string().min(1),
  skillFingerprints: z.record(z.enum(SKILL_IDS), z.string().min(1)),
  environment: z.object({
    season: z.string().min(1),
    rule: z.string().min(1),
    asOf: z.string().optional(),
    /** Content hashes of the data derived views (sets, matchup grids) were built from. They change
     * only when that data changes, so a saved result can be checked against them across releases. */
    sourceFingerprints: z.record(FormatIdSchema, SourceFingerprintsSchema),
  }),
  capabilities: z.array(z.string()),
});
export type Capabilities = z.infer<typeof CapabilitiesSchema>;

export type CompatibilityResult =
  | { ok: true; capabilities: Capabilities }
  | { ok: false; reasons: string[] };

/** Validate a handshake payload and gate on the protocol version. The SPA calls this before
 * trusting anything else the server says; a mismatch renders the incompatibility screen
 * instead of half-working pages. */
export function checkCompatibility(raw: unknown): CompatibilityResult {
  const parsed = CapabilitiesSchema.safeParse(raw);
  if (!parsed.success) {
    return {
      ok: false,
      reasons: parsed.error.issues.map((i) => `${i.path.join(".") || "$"}: ${i.message}`),
    };
  }
  if (parsed.data.webProtocolVersion !== WEB_PROTOCOL_VERSION) {
    return {
      ok: false,
      reasons: [
        `web protocol version ${parsed.data.webProtocolVersion} is not supported ` +
        `(this build speaks ${WEB_PROTOCOL_VERSION})`,
      ],
    };
  }
  return { ok: true, capabilities: parsed.data };
}

export const hasCapability = (c: Capabilities, id: KnownCapability): boolean =>
  c.capabilities.includes(id);
