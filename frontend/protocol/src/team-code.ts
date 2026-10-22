import { z } from "zod";
import { FormatIdSchema } from "./common.ts";
import { TeamDocSchema } from "./team.ts";

export const TeamCodeRequestDtoSchema = z.object({
  code: z.string().min(10).max(64),
  format: FormatIdSchema,
}).strict();
export const TeamCodeResultDtoSchema = z.object({
  schema: z.literal("team-code.v1"),
  code: z.string().regex(/^[A-Z0-9]{10}$/),
  team: TeamDocSchema.extend({ pokemon: TeamDocSchema.shape.pokemon.length(6) }),
  cache: z.object({ hit: z.boolean(), fetched_at: z.string() }),
});
export type TeamCodeResultDto = z.infer<typeof TeamCodeResultDtoSchema>;
