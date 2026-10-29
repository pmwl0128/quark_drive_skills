#!/usr/bin/env python
"""Deterministic team-level legality validation (M1.2).

Champions rules enforced here (facts pulled from the sibling dex skill):
  - Roster legality : every species must exist in the Champions dex (catches phantom /
                      pre-evolution / removed Pokemon — the dex already is the roster).
  - Move legality   : every move must be in that species' cached learnset.
  - Move count      : at most `moves_per_pokemon` (4) moves per member, and they must be
                      distinct (dex-independent, definitive — like the SP caps).
  - Ability legality: ability must be one the species can have (when dex lists abilities).
  - Mega item match : if a member is given as a Mega FORM, its item must be that form's stone.
  - Species Clause  : each base species at most once.
  - Item Clause     : each held item at most once across the team.
  - SP caps         : each stat <= 32 SP, total <= 66 SP.
  - Team size       : 3..6 members.

NOT a team-registration rule (so NOT enforced here): "one Mega per battle". Champions is
6-bring-3 (singles) / bring-4 (doubles), so carrying several Mega stones in the registered team
is legal and common — only one may Mega Evolve once selected. That belongs to selection analysis
(M2/M3), not legality.

The model decides strength; this module only decides legality and returns concrete
repairs. Unknown facts (e.g. dex unavailable) are reported as warnings, not silent passes.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from team_io import Team, STATS, BuildContext  # noqa: E402
from dexlink import lookup_pokemon, lookup_items, DexUnavailable  # noqa: E402
from rules import get_ruleset  # noqa: E402
from mega import form_base_species, legal_member_abilities  # noqa: E402
import team_i18n as i18n  # noqa: E402


@dataclass
class ValidationResult:
    """Three-state legality (audit 2026-06-21): valid / invalid / unknown.

    A clean pass is only reported when every legality check actually ran. If a check could not run
    (e.g. the dex was unavailable), its result is unknown — NOT silently valid — so a team with
    unverified legality never comes back `valid=true, confidence=high`. `confidence` follows from
    what was actually checked, it is not a fixed constant.
    """
    # Messages are stored DEFERRED as (catalog_key, kwargs), NOT pre-rendered strings, so the same
    # result renders localized for the md report and canonical-English for `--format json` (the
    # JSON-never-localized contract). `err`/`warn`/`skip` take a catalog key + its fmt kwargs.
    _errors: list[tuple[str, dict]] = field(default_factory=list)
    _warnings: list[tuple[str, dict]] = field(default_factory=list)   # real warnings (≠ skipped)
    _skipped: list[tuple[str, dict]] = field(default_factory=list)    # checks that could not run

    def err(self, _key: str, **kw: Any) -> None:
        self._errors.append((_key, kw))

    def warn(self, _key: str, **kw: Any) -> None:
        self._warnings.append((_key, kw))

    def skip(self, _key: str, **kw: Any) -> None:
        self._skipped.append((_key, kw))

    @property
    def status(self) -> str:
        # A concrete violation is definitive even if other checks were skipped; absent any
        # violation, an incomplete check set means we cannot certify legality.
        if self._errors:
            return "invalid"
        if self._skipped:
            return "unknown"
        return "valid"

    @property
    def valid(self) -> bool:
        """Back-compat boolean: True only for a fully-checked clean pass (unknown is not valid)."""
        return self.status == "valid"

    @property
    def confidence(self) -> str:
        # invalid: we found a real violation -> high. valid: every check ran clean -> high.
        # unknown: legality couldn't be fully established -> low.
        return "low" if self.status == "unknown" else "high"

    # --- localizable views (Msg = localized value for md, .en() for JSON via i18n.jsonify) ---
    def errors_msg(self) -> list[i18n.Msg]:
        return [i18n.Msg(k, **kw) for k, kw in self._errors]

    def skipped_msg(self) -> list[i18n.Msg]:
        return [i18n.Msg(k, **kw) for k, kw in self._skipped]

    def warnings_msg(self) -> list[i18n.Msg]:
        # JSON/back-compat shape: warnings includes each skipped check as a "Skipped: …" entry.
        return ([i18n.Msg(k, **kw) for k, kw in self._warnings]
                + [i18n.Msg(k, _prefix="Skipped: ", **kw) for k, kw in self._skipped])

    # --- canonical English views (what JSON / cross-module consumers read) ---
    @property
    def errors(self) -> list[str]:
        return [m.en() for m in self.errors_msg()]

    @property
    def skipped(self) -> list[str]:
        return [m.en() for m in self.skipped_msg()]

    @property
    def warnings(self) -> list[str]:
        return [m.en() for m in self.warnings_msg()]

    @staticmethod
    def _message_detail(severity: str, key: str, params: dict[str, Any]) -> dict[str, Any]:
        """Language-invariant message coordinates for presentation boundaries.

        Canonical JSON keeps its English strings for existing skill consumers.  The extra
        code + params representation lets the web bridge localize without reverse-parsing
        those strings.  Params are facts/join keys; stringify exceptional objects so this
        extension can never make an otherwise valid result non-serializable.
        """
        safe = {k: (v if v is None or isinstance(v, (str, int, float, bool)) else str(v))
                for k, v in params.items() if k != "_prefix"}
        return {"severity": severity, "code": key, "params": safe}

    def message_details(self) -> list[dict[str, Any]]:
        return ([self._message_detail("error", k, kw) for k, kw in self._errors]
                + [self._message_detail("warning", k, kw) for k, kw in self._warnings]
                + [self._message_detail("skipped", k, kw) for k, kw in self._skipped])

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "valid": self.valid, "confidence": self.confidence,
                "errors": self.errors, "warnings": self.warnings, "skipped": self.skipped,
                "messages": self.message_details()}


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _incomplete_reason(m: Any) -> str | None:
    """Return why a tagged member cannot be fully certified, or None when present fields suffice.

    `completeness` is a source-trust tag, not an automatic legality veto. A fully specified
    extracted/inferred set can still be checked for hard legality; only absent critical fields keep
    the verdict in the unknown state. `observed_species_only` remains incomplete by definition.
    """
    if m.completeness == "observed_species_only":
        return "species only"
    if m.completeness not in ("extracted_set", "inferred_set"):
        return None
    missing = []
    if not m.moves:
        missing.append("moves")
    if not m.ability:
        missing.append("ability")
    return ", ".join(missing) if missing else None


def validate(team: Team, context: BuildContext | None = None) -> ValidationResult:
    r = ValidationResult()
    rs = get_ruleset(getattr(context, "season", None) if context else None,
                     getattr(context, "rule", None) if context else None)

    # team size (dex-independent, definitive)
    n = len(team.pokemon)
    if n < rs.team_min or n > rs.team_max:
        r.err('val_team_size', n=n, min=rs.team_min, max=rs.team_max)

    # dex facts (one batch call). If the dex is down, roster/move/ability/Mega/base-species checks
    # cannot run -> record them as skipped (status becomes 'unknown', never a silent 'valid').
    species_names = [m.species for m in team.pokemon if m.species]
    dex_ok = True
    try:
        facts = lookup_pokemon(species_names)
    except DexUnavailable as e:
        r.skip('val_skip_roster', e=e)
        facts = {}
        dex_ok = False

    # A base+own-stone registration may name either stage's ability. The dex's host
    # mapping decides which Mega is reachable; Species Clause grouping is insufficient.
    item_facts: dict[str, dict] = {}
    form_info: dict[str, dict] = {}
    held_stones = sorted({m.item for m in team.pokemon if m.item})
    if dex_ok and held_stones:
        try:
            item_facts = lookup_items(held_stones)
            forms = sorted({f for it in item_facts.values() for f in (it.get("required_by") or [])})
            form_info = {f: ff for f, ff in (lookup_pokemon(forms) if forms else {}).items()
                         if ff.get("found")}
        except DexUnavailable:
            pass

    base_seen: dict[str, list[str]] = {}
    item_seen: dict[str, list[str]] = {}

    for m in team.pokemon:
        sp = m.species or "(blank)"
        fact = facts.get(m.species)

        # roster legality
        if fact is not None:
            if not fact.get("found"):
                r.err('val_not_in_dex', sp=sp)
            else:
                # move legality vs cached learnset
                learn = {_norm(x) for x in fact.get("moves", [])}
                if learn:
                    for mv in m.moves:
                        if _norm(mv) not in learn:
                            r.err('val_move_illegal', sp=sp, mv=mv)
                elif m.moves:
                    r.skip('val_skip_learnset_empty', sp=sp)
                # Shared with correction suggestions: exact host + held stone, not base group.
                base = form_base_species(sp, fact) or sp
                own_item = item_facts.get(m.item) or {}
                canonical_item = own_item.get("name") or m.item
                allowed = {_norm(a) for a in legal_member_abilities(
                    fact.get("name") or sp, canonical_item, fact,
                    {canonical_item: own_item}, form_info)}
                if m.ability and allowed and _norm(m.ability) not in allowed:
                    r.err('val_ability_illegal', sp=sp, ability=m.ability)
                # mega item match
                req = fact.get("required_item")
                if fact.get("is_mega") and req and _norm(m.item) != _norm(req):
                    r.err('val_mega_item', sp=sp, req=req, item=m.item)
                base_seen.setdefault(_norm(base), []).append(sp)
        elif dex_ok:
            # dex is up but this name wasn't resolvable (e.g. blank species); group by raw name.
            base_seen.setdefault(_norm(sp), []).append(sp)

        # item clause bookkeeping (dex-independent)
        if m.item:
            item_seen.setdefault(_norm(m.item), []).append(sp)

        # move count + uniqueness (dex-independent, definitive — a learnset-legal set can still be
        # illegal by carrying >4 moves or the same move twice; the learnset loop above never caught
        # that, so e.g. 6 legal moves or 4x Protect passed as valid/high. audit 2026-06-28).
        if m.moves:
            if len(m.moves) > rs.moves_per_pokemon:
                r.err('val_move_count', sp=sp, n=len(m.moves), cap=rs.moves_per_pokemon)
            seen_moves: set[str] = set()
            dups: list[str] = []
            for mv in m.moves:
                k = _norm(mv)
                if k and k in seen_moves:
                    dups.append(mv)
                else:
                    seen_moves.add(k)
            if dups:
                r.err('val_dup_moves', sp=sp, dups=', '.join(dups))

        if not m.spread:
            r.warn('val_sp_missing', sp=sp)
        # SP caps (dex-independent, definitive)
        if m.spread:
            # Shape first: an unknown key ("speed"), a non-int value, or a negative would either
            # silently DROP SP from the cap math below (32 Spe vanishing while the verdict says
            # valid) or crash int() — the legality authority must reject the same shapes
            # rules.legal_spread rejects (external audit 2026-07-02).
            bad = sorted(k for k, v in m.spread.items()
                         if k not in STATS or isinstance(v, bool) or not isinstance(v, int) or v < 0)
            if bad:
                r.err('val_sp_shape', sp=sp, bad=', '.join(bad), keys='/'.join(STATS))
            else:
                over = [s.upper() for s in STATS if int(m.spread.get(s, 0)) > rs.sp_per_stat_cap]
                total = sum(int(m.spread.get(s, 0)) for s in STATS)
                if over:
                    r.err('val_sp_over', sp=sp, cap=rs.sp_per_stat_cap, over=', '.join(over))
                if total > rs.sp_total_cap:
                    r.err('val_sp_total', sp=sp, total=total, cap=rs.sp_total_cap)

    # item pool legality (dex is the authority for the Champions item pool)
    held = sorted({m.item for m in team.pokemon if m.item})
    if held:
        try:
            for it, f in lookup_items(held).items():
                if not f.get("found"):
                    r.err('val_item_pool', it=it)
        except DexUnavailable as e:
            r.skip('val_skip_itempool', e=e)

    # owned-only check (from build-context; owned list is AI-supplied — resolved from the user's input).
    # owned_only with an EMPTY owned list cannot be certified — every member is unverifiable —
    # so it is skipped (status 'unknown'), never silently passed as valid (audit 2026-06-21).
    if context and context.owned_only:
        if not context.owned:
            r.skip('val_skip_owned_empty')
        else:
            try:
                owned_base = {
                    form_base_species(f.get("name"), f)
                    for f in lookup_pokemon(context.owned).values() if f.get("found")
                }
                for m in team.pokemon:
                    ff = facts.get(m.species)
                    base = form_base_species(m.species, ff) if ff and ff.get("found") else m.species
                    if base not in owned_base:
                        r.err('val_not_owned', species=m.species)
            except DexUnavailable as e:
                r.skip('val_skip_owned', e=e)

    # species clause (base resolution needs the dex; skipped above if it was down)
    if rs.species_clause and dex_ok:
        for base, owners in base_seen.items():
            if len(owners) > 1:
                r.err('val_species_clause', owners=', '.join(owners))

    # item clause (dex-independent)
    if rs.item_clause:
        for item, owners in item_seen.items():
            if len(owners) > 1:
                r.err('val_item_clause', owners=', '.join(owners))

    # missing vs illegal: on a tagged low-trust/incomplete set, ABSENT critical fields are unknown,
    # not legal. The tag itself is not a hard veto: a fully specified extracted/inferred set can be
    # legality-checked, while a species-only or field-missing entry still drops to unknown.
    # Untagged sets are treated as user-authored = trusted, so they don't trip this.
    incomplete = []
    for m in team.pokemon:
        reason = _incomplete_reason(m)
        if reason:
            incomplete.append(f"{m.species or '(blank)'} ({reason})")
    if incomplete:
        r.skip('val_incomplete', incomplete=", ".join(incomplete))

    return r


def format_report(r: ValidationResult) -> str:
    head = {"valid": "VALID", "invalid": "INVALID", "unknown": "UNKNOWN (incomplete)"}[r.status]
    lines = [f"# {i18n.t('val_title')}: {head}  ({i18n.t('confidence')}: {r.confidence})"]
    # md uses the localized Msg values directly; real warnings come from `_warnings` (skipped checks
    # are shown only under "not checked", never duplicated here — no more English "Skipped:" prefix filter).
    errs = r.errors_msg()
    if errs:
        lines.append(f"\n## {i18n.t('val_errors')}")
        lines += [f"- {e}" for e in errs]
    skips = r.skipped_msg()
    if skips:
        lines.append(f"\n## {i18n.t('val_not_checked')}")
        lines += [f"- {s}" for s in skips]
    other_warnings = [i18n.Msg(k, **kw) for k, kw in r._warnings]
    if other_warnings:
        lines.append(f"\n## {i18n.t('val_warnings')}")
        lines += [f"- {w}" for w in other_warnings]
    if r.status == "valid":
        lines.append(f"\n{i18n.t('val_no_issues')}")
    return "\n".join(lines)
