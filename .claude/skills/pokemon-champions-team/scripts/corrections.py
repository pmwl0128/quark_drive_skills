"""Read-only configuration repair choices. The host chooses and revalidates changes."""
from __future__ import annotations

from copy import deepcopy

from canonhash import content_hash
from dexlink import lookup_items, lookup_pokemon
from mega import legal_member_abilities, mega_form_from_maps
from rules import get_ruleset
from validate_team import validate

# A disclosed convenience shortlist, not an item pool authority or a suitability ranking.
# Every entry is checked against the live sibling dex before it is offered.
ITEM_SHORTLIST = ("Choice Scarf", "Focus Sash", "Leftovers", "Life Orb", "Lum Berry",
                  "Rocky Helmet", "Sitrus Berry")


def suggest(raw: dict, team, context=None) -> dict:
    """`team` is a strictly alias-normalized copy; change preconditions use raw input values.

    No fuzzy normalization, form conversion, first-ability choice or automatic repair occurs.
    Options are per field; they are not a jointly certified team or a strategic recommendation.
    """
    rs = get_ruleset(getattr(context, "season", None), getattr(context, "rule", None))
    validation = validate(team, context).to_dict()
    members = team.pokemon
    rows_key = "pokemon" if "pokemon" in raw else "decklist"
    rows = raw[rows_key]
    facts = lookup_pokemon(sorted({m.species for m in members}))
    names = set(ITEM_SHORTLIST) | {m.item for m in members if m.item}
    names.update(f["required_item"] for f in facts.values() if f.get("required_item"))
    items = lookup_items(sorted(names))
    forms = sorted({f for it in items.values() for f in it.get("required_by", [])})
    form_facts = lookup_pokemon(forms) if forms else {}
    avoided = set(getattr(context, "avoid_items", []) or [])
    issues, gaps = [], []

    def change(i, field, value):
        return {"path": f"/{rows_key}/{i}/{field}", "member_index": i, "field": field,
                "from": deepcopy(rows[i].get(field)), "present": field in rows[i], "to": value}

    def issue(code, indices, **extra):
        issues.append({"code": code, "members": indices, **extra})

    def fact(i):
        return facts.get(members[i].species) or {}

    def own_form(i):
        m = members[i]
        return mega_form_from_maps(m.species, m.item, fact(i), items, form_facts)

    def required(i):
        f = fact(i)
        # Preserve an explicitly named Mega AND a base+own-stone registration.
        # Losing its stone is a semantic redesign, outside this repair helper.
        if f.get("is_mega"):
            return f.get("required_item")
        return members[i].item if own_form(i) else None

    def item_options(i):
        m, f = members[i], fact(i)
        if not f.get("found"):
            return [], "species_facts_unavailable"
        req = required(i)
        if f.get("is_mega") and not req:
            return [], "required_item_unavailable"
        if req == m.item and req:
            return [], "preserve_registered_mega"
        values = [req] if req else [*ITEM_SHORTLIST, None]
        choices = []
        for value in values:
            if value == m.item:
                continue
            if value and not (items.get(value) or {}).get("found"):
                continue
            blockers = ([j for j, other in enumerate(members) if j != i and other.item == value]
                        if value and rs.item_clause else [])
            choices.append({"changes": [change(i, "item", value)],
                            "available": not blockers and value not in avoided,
                            "blocked_by_members": blockers, "blocked_by_avoid": value in avoided})
        return choices, None if choices else "no_verified_item_options"

    for i, m in enumerate(members):
        f = fact(i)
        if not f.get("found"):
            gaps.append({"member_index": i, "code": "species_facts_unavailable"})
            continue
        form = own_form(i)
        if form and not f.get("is_mega") and not (form_facts.get(form) or {}).get("found"):
            gaps.append({"member_index": i, "code": "mega_ability_facts_unavailable"})
        else:
            abilities = legal_member_abilities(m.species, m.item, f, items, form_facts)
            if not abilities:
                gaps.append({"member_index": i, "code": "ability_facts_unavailable"})
            elif m.ability and m.ability not in abilities:
                issue("ability_illegal", [i], severity="error", allowed_abilities=abilities,
                      options=[{"changes": [change(i, "ability", a)], "available": True} for a in abilities])
        req = f.get("required_item") if f.get("is_mega") else None
        codes = []
        if req and m.item != req:
            codes.append(("mega_item", "error"))
        if m.item and not (items.get(m.item) or {}).get("found"):
            codes.append(("item_pool", "error"))
        elif m.item and (items[m.item].get("required_by") or []) and not form:
            # Holding a foreign stone is legal; it simply cannot activate on this host.
            codes.append(("ineffective_mega_stone", "warning"))
        for code, severity in codes:
            options, blocked = item_options(i)
            issue(code, [i], severity=severity, required_item=req, options=options, blocked_reason=blocked)

    holders = {}
    for i, m in enumerate(members):
        if m.item:
            holders.setdefault(m.item, []).append(i)
    if rs.item_clause:
        for item, indices in holders.items():
            if len(indices) < 2:
                continue
            sides = []
            for i in indices:
                options, blocked = item_options(i)
                sides.append({"member_index": i, "species": members[i].species,
                              "preserved_mega": own_form(i), "options": options, "blocked_reason": blocked})
            issue("item_clause", indices, severity="error", item=item,
                  minimum_changes=len(indices) - 1, holders=sides,
                  note="Choose which holder keeps the item; choices on different holders can collide. Revalidate the combined team.")

    normalization = []
    for i, m in enumerate(members):
        for field in ("species", "item", "ability", "nature", "moves"):
            key = "name" if field == "species" and "species" not in rows[i] else field
            before, after = rows[i].get(key), getattr(m, field)
            if before is not None and before != after:
                normalization.append(change(i, key, after))
    return {"schema": "team-corrections.v1", "kind": "suggest-fixes",
            "input_fingerprint": content_hash(raw, 24), "applied": False,
            "validation": validation, "name_normalization": normalization,
            "suggestions": issues, "gaps": gaps,
            "item_shortlist": [it for it in ITEM_SHORTLIST if (items.get(it) or {}).get("found")],
            "next_step": "Choose and disclose edits on a copy, then rerun validate and the affected UEP gates.",
            "scope": {"item_options": "dex-verified convenience shortlist plus no held item; alphabetic, not exhaustive or ranked",
                      "mega": "preserve named Mega and base+own-stone registrations; foreign stones are advisory, not illegal",
                      "constraints": "occupied and context-avoided items are marked unavailable; other intent and battle effects need host review",
                      "validation": "unchanged input with strict alias normalization; suggestions do not certify the resulting team"}}
