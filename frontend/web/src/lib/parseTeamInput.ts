import type { FormatId } from "@pokemon-champions/protocol";
import type { RuntimeAdapter } from "../runtime/adapter.ts";
import { loadLibraryVocabulary } from "./library/vocabulary.ts";
import { matchLocal, membersFromPaste } from "./pasteMembers.ts";
import { parsePokepaste } from "./pokepaste.ts";
import { toTeamDoc } from "./teamDoc.ts";
import { teamCodeFromText } from "./teamCode.ts";

/** The text currently displayed by a team input, parsed without truncating or dropping members. */
export async function parseTeamInput(text: string, format: FormatId, adapter: RuntimeAdapter) {
  if (!text.trim()) return { doc: null, error: null };
  // Typing only recognizes the shape; the explicit submit resolves it on the backend.
  const vocabulary = await loadLibraryVocabulary();
  if (!matchLocal(vocabulary.dex, text) && teamCodeFromText(text)) return { doc: null, error: null };
  if (text.trim().startsWith("{")) {
    let raw: unknown;
    try { raw = JSON.parse(text); } catch { return { doc: null, error: "invalid" as const }; }
    if (raw && typeof raw === "object" && "pokemon" in raw && Array.isArray(raw.pokemon) && raw.pokemon.length > 6) {
      return { doc: null, error: "too-many" as const };
    }
    const doc = toTeamDoc(raw, format, vocabulary);
    return { doc, error: doc ? null : "invalid" as const };
  }
  if (parsePokepaste(text).mons.length > 6) return { doc: null, error: "too-many" as const };
  const parsed = await membersFromPaste(text, { dex: vocabulary.dex, items: vocabulary.items,
    natures: vocabulary.natures as import("@pokemon-champions/protocol").NatureDto[], adapter });
  const doc = parsed.unresolved.length ? null : toTeamDoc({ format, pokemon: parsed.members.map((entry) => entry.member) }, format, vocabulary);
  return { doc, error: doc ? null : "invalid" as const };
}
