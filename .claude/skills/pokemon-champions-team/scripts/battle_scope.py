"""Explicit static field conditions shared by review, matchup and selection."""
from copy import deepcopy


def conditions(raw):
    if raw is None:
        return {}
    if not isinstance(raw, dict) or set(raw) - {"weather", "terrain", "trick_room"}:
        raise ValueError("conditions accepts weather, terrain and trick_room")
    out = {}
    for key, choices in (("weather", {"rain", "sun", "sand", "snow"}),
                         ("terrain", {"electric", "grassy", "psychic", "misty"})):
        value = raw.get(key)
        if value is not None:
            if not isinstance(value, str) or value.lower() not in choices:
                raise ValueError(f"unsupported {key}")
            out[key] = value.lower().capitalize()
    if "trick_room" in raw:
        if type(raw["trick_room"]) is not bool:
            raise ValueError("trick_room must be boolean")
        out["trick_room"] = raw["trick_room"]
    return out


def annotate(check, field):
    if check and field:
        check["field_conditions"] = deepcopy(field)
        check.setdefault("caveats", []).append(
            "Stated static field; no turns remaining, field replacement, doubles partner or full battle sequence is simulated.")
    return check
