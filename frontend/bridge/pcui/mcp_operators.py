"""Typed deterministic tools over the daemon's existing operator and query boundaries."""
from copy import deepcopy
from urllib.parse import urlencode


STRING = {"type": "string", "minLength": 1}
OBJECT = {"type": "object"}
FORMAT = {"type": "string", "enum": ["single", "double"]}
TEAM = {"type": "object", "required": ["pokemon"], "properties": {
    "format": FORMAT, "pokemon": {"type": "array", "minItems": 1, "maxItems": 6,
        "items": {"type": "object", "required": ["species"], "properties": {"species": STRING}}}}}
TOP_K = {"type": "integer", "minimum": 1, "maximum": 60}
VIEW = {"type": "string", "enum": ["full", "summary"]}
STRINGS = {"type": "array", "items": STRING}
FOCUS = {"type": "object", "additionalProperties": False, "properties": {
    "opponents": STRINGS, "members": {"type": "array", "items": {"type": ["integer", "string"]}}}}
FIELD = {"type": "object", "additionalProperties": False, "properties": {
    "weather": {"type": "string", "enum": ["rain", "sun", "sand", "snow", "Rain", "Sun", "Sand", "Snow"]},
    "terrain": {"type": "string", "enum": ["electric", "grassy", "psychic", "misty", "Electric", "Grassy", "Psychic", "Misty"]},
    "trick_room": {"type": "boolean"}}}
CHANGES = {"type": "array", "minItems": 1, "items": {"type": "object", "additionalProperties": False,
    "required": ["slot", "set"], "properties": {"slot": {"type": "integer", "minimum": 0, "maximum": 5},
    "set": {"type": "object", "additionalProperties": False, "properties": {
        "species": STRING, "ability": STRING, "nature": STRING, "item": {"type": ["string", "null"]},
        "moves": STRINGS, "spread": OBJECT}}}}}
REVIEW = {"file": TEAM, "context": OBJECT, "top_k": TOP_K, "focus": FOCUS,
          "opponents": TEAM, "conditions": FIELD, "view": VIEW}


def validate(value, schema, path="arguments"):
    kinds = schema.get("type")
    kinds = [kinds] if isinstance(kinds, str) else kinds
    checks = {"object": lambda: isinstance(value, dict), "array": lambda: isinstance(value, list),
              "string": lambda: isinstance(value, str), "integer": lambda: type(value) is int,
              "boolean": lambda: type(value) is bool, "null": lambda: value is None}
    if kinds and not any(checks[k]() for k in kinds):
        raise ValueError(f"{path} has the wrong JSON type")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is outside the allowed values")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        if set(schema.get("required", [])) - set(value):
            raise ValueError(f"{path} is missing required fields")
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise ValueError(f"{path} has unsupported fields")
        for key, child in value.items():
            if key in props:
                validate(child, props[key], path + "." + key)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf")):
            raise ValueError(f"{path} has an invalid length")
        for child in value:
            validate(child, schema.get("items", {}), path + "[]")
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        raise ValueError(f"{path} is empty")
    if type(value) is int and not schema.get("minimum", float("-inf")) <= value <= schema.get("maximum", float("inf")):
        raise ValueError(f"{path} is out of range")


def install(tools, error_type):
    def schema(properties, required):
        return {"type": "object", "additionalProperties": False, "properties": properties, "required": required}

    def checked(shape, action):
        def call(args):
            try:
                validate(args, shape)
            except ValueError as exc:
                raise error_type(str(exc)) from exc
            return action(args)
        return call

    definitions = {
        "team_review": ("review", REVIEW, ["file"], "Validate and review one complete team using one shared matchup battery."),
        "team_try": ("try", {**REVIEW, "changes": CHANGES}, ["file", "changes"], "Apply explicit edits to a copy and compare qualified before/after facts."),
        "review_claims": ("review-claims", {"file": OBJECT}, ["file"], "Recompute exact team-floor, opponent-variant or member-cell claims from a full review."),
        "slate_init": ("slate-init", {"file": OBJECT, "context": OBJECT, "audit_receipt": OBJECT,
            "frame_output": OBJECT, "intake_output": OBJECT}, ["file", "context", "audit_receipt"], "Transport explicit candidates and receipts into a slate; never choose a team."),
        "team_localize": ("localize", {"file": TEAM}, ["file"], "Resolve official trilingual names for an explicit team."),
        "team_counters": ("counters", {"species": STRING, "game_format": FORMAT, "top_n": TOP_K, "view": VIEW},
            ["species", "game_format"], "Retrieve qualified observed-build answers to one exact form."),
        "team_ko_scan": ("ko-scan", {"species": STRING, "game_format": FORMAT, "top_n": TOP_K, "view": VIEW,
            "direction": {"type": "string", "enum": ["both", "attacking", "defending"]}}, ["species", "game_format"], "Read qualified retained-build KO facts."),
        "team_speed_ladder": ("speed-ladder", {"species": STRING, "game_format": FORMAT, "set": OBJECT,
            "conditions": OBJECT, "limit": {"type": "integer", "minimum": 1}, "opponent_ranges": {"type": "boolean"}},
            ["species", "game_format"], "Compare stated Speed with observed environment configurations."),
        "team_scenario": ("scenario", {"file": OBJECT}, ["file"], "Calculate one explicit scenario with qualified source configurations."),
        "team_threshold": ("threshold", {"file": OBJECT}, ["file"], "Solve a stated SP threshold with nature/item lanes and optional secondary maximization."),
        "team_fill": ("fill", {"file": TEAM, "context": OBJECT, "slot": {"type": "integer", "minimum": 0, "maximum": 5}},
            ["file", "context"], "Retrieve replacement facts; slot returns complete trial configurations and structural differences."),
        "team_tune_cell": ("tune", {"file": OBJECT}, ["file"], "Tune a verified member cell; return threshold lanes and explicit trial edits."),
    }
    for name, (op, props, required, description) in definitions.items():
        shape = schema({**deepcopy(props), "session": STRING, "output_path": STRING}, required)
        def action(args, op=op, name=name):
            extra = {k: v for k, v in args.items() if k not in {"session", "output_path"}}
            if name == "team_tune_cell":
                extra["from_cell"] = True
            return tools.run_operator({"op": op, "args": extra,
                **{k: args[k] for k in ("session", "output_path") if k in args}})
        tools.table[name] = (description, shape, checked(shape, action))

    prior = schema({"pokemon": STRING, "format": FORMAT}, ["pokemon", "format"])
    tools.table["inference_prior"] = ("Read the current meta prior for a portable inference case.", prior,
        checked(prior, lambda args: tools.client.request("GET", "/api/meta/inference-prior?" + urlencode(args))))
    operations = {"init": ({"case": OBJECT}, ["case"]), "add": ({"case": OBJECT, "observation": OBJECT}, ["case", "observation"]),
        "status": ({"case": OBJECT, "predict": {"type": "boolean"}, "mine": {"type": "integer", "minimum": 0, "maximum": 5}}, ["case"]),
        "remove": ({"case": OBJECT, "id": STRING}, ["case", "id"]), "example": ({"case": OBJECT}, ["case"]),
        "import_web": ({"case": OBJECT, "prior": OBJECT, "foe_id": STRING}, ["case", "prior"]), "schema": ({}, [])}
    for name, (props, required) in operations.items():
        shape = schema(props, required)
        tools.table["inference_" + name] = ("Portable inference " + name + "; returns JSON, never edits a team or source case.", shape,
            checked(shape, lambda args, name=name: tools.client.request("POST", "/api/calc/inference",
                {"operation": name.replace("_", "-"), **args}, timeout=630)))
