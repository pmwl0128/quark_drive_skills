"""Dex-backed battle-form identity for retrieval; no Species Clause or ownership folding."""
from __future__ import annotations

from typing import Callable

import dexlink
from mega import mega_form_from_maps


class Forms:
    def __init__(self, members: list[dict], *, dex_fn: Callable | None = None, item_fn: Callable | None = None):
        names = sorted({m.get("species") for m in members if m.get("species")})
        items = sorted({m.get("item") for m in members if m.get("item")})
        self.facts = (dex_fn or dexlink.lookup_pokemon)(names) if names else {}
        self.items = (item_fn or dexlink.lookup_items)(items) if items else {}
        self.dex_fn = dex_fn or dexlink.lookup_pokemon
        for mapping in (self.facts, self.items):
            for value in list(mapping.values()):
                if value.get("name"):
                    mapping.setdefault(value["name"], value)
        self.gaps: list[dict] = []

    def __call__(self, member: dict) -> str | None:
        species, item = member.get("species"), member.get("item")
        if not species:
            return None
        fact = self.facts.get(species) or {}
        known = bool(fact) and fact.get("found") is not False
        if not known and item:
            item_fact = self.items.get(item) or {}
            if not item_fact.get("found") or item_fact.get("required_by"):
                gap = {"species": species, "item": item, "reason": "cannot prove this held item's battle form without dex facts"}
                if gap not in self.gaps:
                    self.gaps.append(gap)
                return None
        species = fact.get("name") or species
        item = (self.items.get(item) or {}).get("name") or item
        return mega_form_from_maps(species, item, fact, self.items, self.facts) or species

    def project(self, teams: list[dict]) -> list[dict]:
        """A disposable battle-form view; retain registration facts beside derived values."""
        targets = {form for team in teams for m in team.get("pokemon") or []
                   if (form := self(m)) is not None}
        missing = sorted(targets - self.facts.keys())
        if missing:
            self.facts.update(self.dex_fn(missing))
        projected = []
        for team in teams:
            rows = []
            for member in team.get("pokemon") or []:
                out = dict(member)
                form = self(member)
                if form is None:
                    out["species"] = None
                    out["_identity_unknown"] = True
                    rows.append(out)
                    continue
                fact = self.facts.get(form) or {}
                ability = member.get("ability")
                abilities = list(fact.get("abilities") or [])
                derived_ability = False
                if fact.get("is_mega") and ability and abilities and ability not in abilities:
                    if len(abilities) == 1:
                        ability, derived_ability = abilities[0], True
                    else:
                        gap = {"species": member.get("species"), "item": member.get("item"),
                               "reason": "Mega ability cannot be uniquely derived"}
                        if gap not in self.gaps:
                            self.gaps.append(gap)
                        ability = None
                if form != member.get("species") or derived_ability:
                    out.setdefault("_registration", {"species": member.get("species"), "ability": member.get("ability")})
                out["species"], out["ability"] = form, ability
                rows.append(out)
            projected.append({**team, "pokemon": rows})
        return projected


def registration_note(record: dict) -> str:
    """Preserve observed-versus-derived attribution in the human-readable reports too."""
    if record.get("configuration_basis") != "battle_form":
        return ""
    modes = "; ".join(f"{r.get('species')} / {r.get('ability')}: {r['count']}" for r in record.get("registration_modes") or [])
    return ("Battle-form species/ability are derived from dex; observed registrations "
            f"(n={record.get('registration_sample')}): {modes}.")


def battle_view(teams: list[dict], extra: list[dict] | None = None, *, dex_fn=None, item_fn=None):
    identities = Forms(members(teams, extra), dex_fn=dex_fn, item_fn=item_fn)
    return identities.project(teams), identities


def members(teams: list[dict], extra: list[dict] | None = None) -> list[dict]:
    return [m for team in teams for m in team.get("pokemon") or []] + list(extra or [])


def corrections(resolved: dict) -> list[dict]:
    return [{"kind": kind, "from": raw, "to": rec.get("canonical"), "match_type": "fuzzy",
             "score": rec.get("score"), "distance": rec.get("distance")}
            for (kind, raw), rec in resolved.items() if rec.get("ok") and rec.get("match_type") == "fuzzy"]
