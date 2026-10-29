"""Exact-form speed facts over the complete rule pool; NCP owns all numerical calculation."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json

import contracts
import query_identity
import team_i18n as i18n
from rules import get_ruleset
from stat_keys import canonical_spread

MIN_SAMPLE = 5
RELIABLE, USUAL, USUAL_TIE = .90, .60, .50
SET_FIELDS = {"item", "ability", "nature", "spread", "sps", "boosts", "status"}
CONDITIONS = {"weather", "terrain", "tailwind", "trickroom"}


class InputError(ValueError):
    def __init__(self, path, message, *, details=None, code="bad_input"):
        super().__init__(message)
        self.path, self.details, self.code = path, details, code

    def result(self):
        error = {"code": self.code, "message": str(self)}
        if self.details:
            error["details"] = {"schema": "speed_ladder_input_v1", **self.details}
        return {"ok": False, "query": self.path, "error": error}


def _spread(raw, path):
    if not isinstance(raw, dict):
        raise InputError(path, "SP allocation must be an object")
    try:
        result = canonical_spread(raw)
    except ValueError as exc:
        raise InputError(path, str(exc)) from exc
    rules = get_ruleset()
    if any(k not in {"hp", "atk", "def", "spa", "spd", "spe"} or type(v) is not int
           or not 0 <= v <= rules.sp_per_stat_cap for k, v in result.items()):
        raise InputError(path, "SP uses canonical stat keys and integers within the rule cap")
    if sum(result.values()) > rules.sp_total_cap:
        raise InputError(path, "SP total exceeds the rule cap")
    return result


def prepare(species, config, conditions, resolve_fn):
    """Normalize authored names on a copy, reject unsupported coordinates before reading evidence."""
    if not isinstance(species, str) or not species.strip():
        raise InputError("pokemon", "a species name is required")
    if config is not None and not isinstance(config, dict):
        raise InputError("set", "set must be an object")
    if conditions is not None and not isinstance(conditions, dict):
        raise InputError("conditions", "conditions must be an object")
    config, conditions = deepcopy(config), deepcopy(conditions or {})
    if set(config or {}) - SET_FIELDS:
        raise InputError("set", "unsupported set fields", details={"fields": sorted(set(config) - SET_FIELDS)})
    if set(conditions) - CONDITIONS:
        raise InputError("conditions", "unsupported condition fields", details={"fields": sorted(set(conditions) - CONDITIONS)})
    flags = []
    def resolve(raw, kind, path):
        if not isinstance(raw, str) or not raw.strip():
            raise InputError(path, "expected a nonempty name")
        rec = (resolve_fn([raw], kind, fuzzy=True) or {}).get(raw) or {}
        if not rec.get("ok") or not rec.get("canonical"):
            raise InputError(path, "name must resolve unambiguously", details={"resolution": rec})
        if rec.get("match_type") == "fuzzy":
            flags.append({"path": path, "kind": kind, "from": raw, "to": rec["canonical"],
                          "match_type": "fuzzy", "score": rec.get("score"), "distance": rec.get("distance")})
        return rec["canonical"]
    species = resolve(species, "pokemon", "pokemon")
    if config is not None:
        for key in ("nature", "ability", "item"):
            if key in config and not (key == "item" and config[key] in (None, "")):
                config[key] = resolve(config[key], key, "set." + key)
        spreads = {k: _spread(config[k], "set." + k) for k in ("spread", "sps") if k in config}
        if len(spreads) == 2 and any(spreads["spread"].get(k, 0) != spreads["sps"].get(k, 0)
                                     for k in set(spreads["spread"]) | set(spreads["sps"])):
            raise InputError("set", "spread and sps disagree")
        if spreads:
            config["spread"] = next(iter(spreads.values()))
            config.pop("sps", None)
        if "boosts" in config:
            boosts = config["boosts"]
            if not isinstance(boosts, dict) or set(boosts) - {"spe"} or any(
                    type(v) is not int or not -6 <= v <= 6 for v in boosts.values()):
                raise InputError("set.boosts", "speed ladder supports only spe stages, integers -6..6")
        if "status" in config and (not isinstance(config["status"], str) or config["status"] not in contracts.MEMBER_STATUSES):
            raise InputError("set.status", "unsupported status")
    for key, choices in (("weather", ["Sun", "Rain", "Sand", "Snow", "Harsh Sun", "Heavy Rain", "Strong Winds"]),
                         ("terrain", ["Electric", "Grassy", "Psychic", "Misty"])):
        if key in conditions:
            aliases = {s.lower(): s for s in choices}
            value = conditions[key]
            if not isinstance(value, str) or value.lower() not in aliases:
                raise InputError("conditions." + key, "unsupported " + key)
            conditions[key] = aliases[value.lower()]
    if "tailwind" in conditions and conditions["tailwind"] not in ("none", "mine", "opponents", "both"):
        raise InputError("conditions.tailwind", "tailwind is none|mine|opponents|both")
    if "trickroom" in conditions and type(conditions["trickroom"]) is not bool:
        raise InputError("conditions.trickroom", "trickroom must be a boolean")
    return species, config, conditions, flags


def relation(dist, speed, trickroom=False):
    """Opponent order against the asked form's observed sample; ties are never first."""
    total = sum(n for _, n in dist)
    if not total:
        return {"basis": "unavailable", "reason": "empty_observed_sample"}
    first = sum(n for s, n in dist if (speed < s if trickroom else speed > s))
    tie = sum(n for s, n in dist if speed == s)
    last = total - first - tie
    if tie >= USUAL_TIE * total:
        label = "usually_ties"
    elif first >= RELIABLE * total:
        label = "reliably_first"
    elif first >= USUAL * total:
        label = "usually_first"
    elif last >= RELIABLE * total:
        label = "reliably_last"
    elif last >= USUAL * total:
        label = "usually_last"
    else:
        label = "depends_on_build"
    return {"basis": "observed_sample", "label": label, "sample": total,
            "counts": {"opponent_first": first, "ties": tie, "asked_first": last},
            "percent": {"opponent_first": round(100 * first / total, 1),
                        "ties": round(100 * tie / total, 1), "asked_first": round(100 * last / total, 1)}}


def ladder(species, fmt, teams, ranking, *, config=None, conditions=None, resolve_fn,
           dex_fn, item_fn, meta_fn, speed_fn, opponent_ranges=False, prepared=None):
    if fmt not in ("single", "double"):
        raise InputError("game_format", "single or double is required", code="unknown_format")
    if type(opponent_ranges) is not bool:
        raise InputError("opponent_ranges", "opponent_ranges must be a boolean")
    species, config, conditions, flags = prepared or prepare(species, config, conditions, resolve_fn)
    # Pool readers scope format/rule; explicit format disagreement remains excluded for injected readers.
    teams = [t for t in teams if t.get("format") in (None, fmt)]
    origins = {}
    for row in ranking:
        name = row.get("pokemon_en") or row.get("pokemon")
        if name:
            origins.setdefault(name, {"species": name, "usage_rank": row.get("rank")})
    extra = [{"species": n} for n in {*origins, species}]
    if config is not None:
        extra.append({"species": species, "item": config.get("item")})
    projected, identities = query_identity.battle_view(teams, extra, dex_fn=dex_fn, item_fn=item_fn)
    facts = identities.facts
    for name in list(origins):
        for form in (facts.get(name) or {}).get("mega_forms") or []:
            if form.get("name"):
                origins.setdefault(form["name"], dict(origins[name]))
    missing = sorted(({species} | set(origins)) - set(facts))
    if missing:
        facts.update(dex_fn(missing))
    if not (facts.get(species) or {}).get("found"):
        raise InputError("pokemon", "species has no current dex facts", code="not_found")
    # A stated stone requests that form's defaults. Without a stated set, an ordinary query remains ordinary.
    requested = species
    if config is not None:
        species = identities({"species": species, "item": config.get("item")})
        if not species:
            raise InputError("set.item", "cannot prove the requested battle form")
        if species not in facts:
            facts.update(dex_fn([species]))
    names = set(origins) | {species}
    metas = meta_fn(sorted(names), fmt) or {}
    selected = [(t, m) for t in projected for m in t.get("pokemon") or [] if m.get("species") in names]
    # Stored evidence is strict: never repair a typo into a different speed distribution.
    resolutions = {}
    for kind in ("nature", "ability", "item"):
        values = {m[kind] for _, m in selected if isinstance(m.get(kind), str) and m[kind]}
        values |= {m[kind] for m in metas.values() if isinstance(m, dict) and isinstance(m.get(kind), str) and m[kind]}
        resolutions[kind] = resolve_fn(sorted(values), kind, fuzzy=False) if values else {}

    def clean(member):
        out = dict(member)
        for key in ("nature", "ability", "item"):
            raw = out.get(key)
            if key == "item" and not raw:
                out[key] = None
                continue
            rec = (resolutions[key].get(raw) or {}) if isinstance(raw, str) else {}
            if not rec.get("ok") or not rec.get("canonical"):
                return None, "unresolved_" + key
            out[key] = rec["canonical"]
        try:
            raw = out.get("spread", out.get("sps"))
            if raw is None:
                return None, "missing_spe_sp"
            spread = _spread(raw, "observed.spread")
        except InputError:
            return None, "invalid_spread"
        if "spe" not in spread:
            return None, "missing_spe_sp"
        out["spread"] = {"spe": spread["spe"]}
        if out["ability"] not in (facts.get(out["species"]) or {}).get("abilities", []):
            return None, "invalid_form_ability"
        return out, None

    counts = {n: Counter() for n in names}
    matched, excluded, registrations = Counter(), defaultdict(Counter), defaultdict(Counter)
    levels = defaultdict(Counter)
    for team, member in selected:
        name = member["species"]
        matched[name] += 1
        if member.get("completeness") not in ("observed_full_set", "extracted_set"):
            excluded[name]["non_observed_set"] += 1
            continue
        if not member.get("item") and not any(m.get("item") for m in team.get("pokemon") or []):
            excluded[name]["unknown_item"] += 1
            continue
        row, reason = clean(member)
        if reason:
            excluded[name][reason] += 1
            continue
        key = (row["item"], row["ability"], row["nature"], row["spread"]["spe"])
        counts[name][key] += 1
        levels[name][member["completeness"]] += 1
        reg = member.get("_registration") or member
        registrations[(name, key)][(reg.get("species"), reg.get("ability"))] += 1

    def modal(name):
        if counts[name]:
            key, count = min(counts[name].items(), key=lambda pair: (-pair[1], str(pair[0])))
            item, ability, nature, spe = key
            return {"species": name, "item": item, "ability": ability, "nature": nature, "spread": {"spe": spe}}, {
                "kind": "observed_speed_configuration", "count": count, "sample": sum(counts[name].values()),
                "confidence": "low" if sum(counts[name].values()) < MIN_SAMPLE else "medium",
                "registration_modes": [{"species": sp, "ability": ab, "count": n}
                                       for (sp, ab), n in sorted(registrations[(name, key)].items(), key=lambda r: str(r[0]))]}
        raw = metas.get(name)
        if raw:
            if not raw.get("item"):
                return None, {"kind": "unavailable", "reason": "meta_item_unknown"}
            member = {**raw, "species": name}
            projected_meta, _ = query_identity.battle_view([{"pokemon": [member]}], dex_fn=dex_fn, item_fn=item_fn)
            member = projected_meta[0]["pokemon"][0]
            if member.get("species") != name:
                return None, {"kind": "unavailable", "reason": "meta_configuration_is_another_form"}
            row, reason = clean(member)
            if not reason:
                return {k: row[k] for k in ("species", "item", "ability", "nature", "spread")}, {
                    "kind": "meta_marginals", "note": raw.get("note"), "sample": None}
        return None, {"kind": "unavailable", "reason": "no_complete_speed_configuration"}

    mine, basis = modal(species)
    assumptions = []
    if config is not None:
        fallback = mine or {}
        fact = facts.get(species) or {}
        item = config.get("item") or None
        if fact.get("is_mega"):
            required = fact.get("required_item")
            if "item" in config and item != required:
                raise InputError("set.item", "the Mega form requires its own stone")
            item = required
        elif "item" not in config:
            assumptions.append("item omitted from stated set: no held item, including no implicit Choice Scarf")
        ability = config.get("ability") or fallback.get("ability")
        if not ability and len(fact.get("abilities") or []) == 1:
            ability = fact["abilities"][0]
        if ability not in fact.get("abilities", []):
            registration = facts.get(requested) or {}
            if species != requested and ability in registration.get("abilities", []) and len(fact.get("abilities") or []) == 1:
                ability = fact["abilities"][0]
            else:
                raise InputError("set.ability", "specify an ability legal for the requested form")
        mine = {"species": species, "item": item, "ability": ability,
                "nature": config.get("nature") or fallback.get("nature") or "Hardy",
                "spread": {"spe": (config.get("spread") if "spread" in config else fallback.get("spread") or {}).get("spe", 0)},
                "boosts": config.get("boosts") or {}, "status": config.get("status") or "Healthy"}
        inherited = [k for k in ("nature", "ability", "spread") if k not in config]
        assumptions.append("unstated fields use the exact-form reference when available; otherwise neutral nature / 0 Spe SP / unique dex ability")
        basis = {"kind": "stated_set", "reference": basis, "inherited_fields": inherited, "assumptions": assumptions}
    if mine is None:
        raise InputError("pokemon", "no complete speed configuration; supply --set with nature, spe SP and ability", code="not_found", details=basis)

    inputs, indices = [], {}
    def point(member, is_mine):
        field = {k: conditions[k] for k in ("weather", "terrain") if k in conditions}
        field["tailwind"] = conditions.get("tailwind") in (("mine", "both") if is_mine else ("opponents", "both"))
        inp = {"name": member["species"], "item": member.get("item"), "ability": member["ability"],
               "nature": member["nature"], "sps": {"spe": member["spread"]["spe"]},
               "boosts": member.get("boosts") or {}, "status": member.get("status") or "Healthy", "field": field}
        key = json.dumps(inp, sort_keys=True)
        if key not in indices:
            indices[key] = len(inputs)
            inputs.append(inp)
        return indices[key]
    mine_index = point(mine, True)
    candidates, unavailable = [], []
    for name, origin in origins.items():
        if name == species:
            continue
        member, source = modal(name)
        if member is None:
            unavailable.append({"species": name, "pool_source": origin, **source})
        else:
            candidates.append({"species": name, "pool_source": origin, "configuration": member, "basis": source,
                               "point": point(member, False)})
    samples = {}
    for name in ({species} if config is None else set()) | (set(origins) - {species} if opponent_ranges else set()):
        samples[name] = [(point({"species": name, "item": it, "ability": ab, "nature": na,
                                "spread": {"spe": spe}}, name == species), n)
                         for (it, ab, na, spe), n in counts[name].items()]
    results = speed_fn(inputs)
    if not isinstance(results, list) or len(results) != len(inputs):
        raise RuntimeError("speed calculator returned an incomplete batch")
    def value(index):
        row = results[index]
        speed = row.get("finalSpeed") if isinstance(row, dict) and "error" not in row and row.get("ok") is not False else None
        return speed if type(speed) is int and speed > 0 else None
    if value(mine_index) is None:
        raise InputError("pokemon", "calculator could not price the asked configuration", code="engine_unavailable",
                         details={"calculator": results[mine_index]})
    def observed(name):
        dist, failed, failures = Counter(), 0, []
        for idx, n in samples.get(name, []):
            speed = value(idx)
            if speed is None:
                failed += n
                failures.append({"input": inputs[idx], "rows": n, "calculator": results[idx]})
            else:
                dist[speed] += n
        available = name in samples
        sample = sum(dist.values())
        reliable = available and failed == 0 and sample >= MIN_SAMPLE
        return {"matched_rows": matched[name], "eligible_rows": sum(counts[name].values()),
                "completeness_counts": dict(levels[name]),
                "excluded": dict(excluded[name]), "computed_rows": sample, "failed_rows": failed,
                "failures": failures,
                "distribution": sorted([s, n] for s, n in dist.items()) if available else None,
                "range": [min(dist), max(dist)] if dist and not failed else None,
                "share_labels_available": reliable,
                "reason": ("not_requested_or_stated_set" if not available else "calculation_incomplete" if failed
                           else "sample_below_five" if sample < MIN_SAMPLE else None)}
    mine_observed = observed(species)
    rows, groups = [], {"opponent_first": [], "ties": [], "asked_first": []}
    trickroom = conditions.get("trickroom", False)
    for row in candidates:
        index = row.pop("point")
        speed = value(index)
        if speed is None:
            unavailable.append({"species": row["species"], "pool_source": row["pool_source"],
                                "reason": "calculation_failed", "calculator": results[index]})
            continue
        order = "ties" if speed == value(mine_index) else (
            "opponent_first" if (speed < value(mine_index) if trickroom else speed > value(mine_index)) else "asked_first")
        row.update(speed=speed, order=order)
        row["comparison"] = (relation(mine_observed["distribution"], speed, trickroom) if mine_observed["share_labels_available"] else
                             {"basis": "stated_set" if config is not None else "reference_only", "label": order})
        if opponent_ranges:
            row["observed"] = observed(row["species"])
        rows.append(row)
    rows.sort(key=lambda r: (r["speed"] if trickroom else -r["speed"], r["species"]))
    for row in rows:
        groups[row["order"]].append(row["species"])
    return {"kind": "speed-ladder", "format": fmt, "identity_basis": "dex_battle_form",
            "query": {"pokemon": requested, "set": config, "conditions": conditions, "opponent_ranges": opponent_ranges},
            "asked": {"species": species, "configuration": mine, "basis": basis,
                      "speed": value(mine_index), "observed": mine_observed},
            "rows": rows, "groups": groups, "name_resolution": flags,
            "coverage": {"teams_read": len(teams), "team_limit": None, "ranked_species": len(ranking),
                         "data_seasons": sorted({t["season"] for t in teams if t.get("season")}),
                         "computed_opponents": len(rows), "unavailable": unavailable, "identity_gaps": identities.gaps,
                         "calculator_points": len(inputs)},
            "thresholds": {"min_sample": MIN_SAMPLE, "reliable": RELIABLE, "usual": USUAL, "usual_tie": USUAL_TIE},
            "notes": ["Order assumes equal move priority; Trick Room reverses order, never the speed value. Ties are not first.",
                      "Share labels describe only observed rows with known speed configuration; they are not ladder usage or win probabilities.",
                      "All eligible rows are read. Missing names/SP/items and calculator failures remain visible; stored names are never fuzzily repaired.",
                      "Ranked species expand to dex-declared forms with separate samples. pool_source rank belongs to its named parent, not the expanded form.",
                      "Weather/terrain and each side's Tailwind apply to reference and observed calculations. Weather-setting abilities do not imply a field.",
                      "Neutral IV 31 and level 50; activation-dependent abilities/items use the calculator's baseline state."]}


def format_md(out):
    me = out["asked"]
    lines = ["# " + i18n.t("ladder_title"), f"{me['species']}: {me['speed']} ({me['basis']['kind']})", "",
             "| " + " | ".join(i18n.t(k) for k in ("ladder_species", "ladder_speed", "ladder_order", "ladder_basis")) + " |",
             "|---|---:|---|---|"]
    for row in out["rows"]:
        lines.append(f"| {row['species']} | {row['speed']} | {i18n.t('ladder_' + row['order'])} | {row['comparison']['label']} |")
    lines.extend(["", *["> " + note for note in out["notes"]]])
    return "\n".join(lines)
