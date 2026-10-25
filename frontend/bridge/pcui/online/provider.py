"""LLM provider seam for the QA orchestration (frontend/design.md §7.2). One blocking method
so the whole answer pipeline runs inside a single `asyncio.to_thread` — the endpoint stays
async while provider + skill-tool calls stay simple synchronous code."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Mapping, Protocol

from ..paths import SKILLS_ROOT


class LlmUnavailable(RuntimeError):
    """The provider cannot serve at all (missing key, network failure, upstream 5xx/429).
    Maps to HTTP 503 — the caller refunds the user's quota count. `tokens` carries any spend
    from EARLIER rounds of the same request so the budget is still settled truthfully when the
    provider dies mid-pipeline (the orchestrator attaches it before re-raising)."""

    tokens: int = 0


@dataclass
class ChatResult:
    message: dict          # assistant message verbatim: content and/or tool_calls
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Provider cache split for benchmark/accounting visibility — 0 when the upstream
    # doesn't report it.
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0


class LlmProvider(Protocol):
    def chat(self, messages: list[dict], tools: list[dict], *, max_tokens: int,
             tool_choice: str, timeout: float) -> ChatResult: ...


def _optional_float(values: Mapping[str, str], key: str) -> float | None:
    raw = values.get(key, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number or empty") from exc


def _optional_int(values: Mapping[str, str], key: str) -> int | None:
    raw = values.get(key, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer or empty") from exc


def _extra_body(values: Mapping[str, str], reserved: set[str]) -> dict:
    raw = values.get("PCUI_LLM_EXTRA_BODY_JSON", "{}").strip() or "{}"
    try:
        extra = json.loads(raw)
    except ValueError as exc:
        raise ValueError("PCUI_LLM_EXTRA_BODY_JSON must be a JSON object") from exc
    if not isinstance(extra, dict):
        raise ValueError("PCUI_LLM_EXTRA_BODY_JSON must be a JSON object")
    overlap = reserved.intersection(extra)
    if overlap:
        raise ValueError("PCUI_LLM_EXTRA_BODY_JSON cannot override: "
                         + ", ".join(sorted(overlap)))
    return extra


def _thinking_controls(values: Mapping[str, str]) -> tuple[str | None, str | None]:
    thinking = values.get("PCUI_LLM_THINKING", "enabled").strip().lower()
    if thinking not in {"enabled", "disabled", ""}:
        raise ValueError("PCUI_LLM_THINKING must be enabled, disabled, or empty")
    effort = values.get("PCUI_LLM_REASONING_EFFORT", "high").strip().lower()
    if effort not in {"high", "max", ""}:
        raise ValueError("PCUI_LLM_REASONING_EFFORT must be high, max, or empty")
    return thinking or None, effort or None


def _post_json(url: str, payload: dict, headers: dict[str, str], timeout: float) -> dict:
    import httpx
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise LlmUnavailable(f"llm request failed: {type(exc).__name__}") from exc
    except ImportError as exc:
        # httpx can fail while constructing a proxy transport when an optional extra is absent.
        raise LlmUnavailable(f"llm transport unavailable: {exc}") from exc
    except OSError as exc:
        raise LlmUnavailable(
            f"llm transport failed: {type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        # Never include the body: upstream errors can echo request fragments or credentials.
        raise LlmUnavailable(f"llm upstream returned {response.status_code}")
    try:
        document = response.json()
    except ValueError as exc:
        raise LlmUnavailable("llm upstream returned an unparseable response") from exc
    if not isinstance(document, dict):
        raise LlmUnavailable("llm upstream returned an unparseable response")
    return document


# OpenCode Go: the subscription tier's OpenAI-compatible endpoint (DeepSeek models are served only as
# chat completions there). Its edge refuses default client user agents, and the plan asks for one stable
# x-opencode-session per conversation.
OPENCODE_BASE_URL = "https://opencode.ai/inference/go/openai/v1"
OPENCODE_MODEL = "deepseek-v4.1-flash"
USER_AGENT = "QuarkDrive/1.0"


def _conversation_id(messages: list[dict]) -> str:
    """Stable across one tool loop: the system prompt and the first user turn never change in it."""
    opening = json.dumps(messages[:2], ensure_ascii=False, sort_keys=True, default=str)
    return "qd-" + hashlib.sha256(opening.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class OpenAIChatConfig:
    """One environment-backed seam for OpenAI-compatible chat-completions providers.

    Known generation controls get typed fields. ``extra_body`` is the escape hatch for a
    future provider-specific option without another code change, but cannot replace request
    structure or orchestration-owned limits. ``provider="opencode"`` is the OpenCode Go
    subscription (PCUI_OC_*); ``openai-compatible`` is any other endpoint (PCUI_LLM_*).
    """

    api_key: str
    provider: str = "openai-compatible"
    base_url: str = "https://api.openai.com/v1"
    api_path: str = "/chat/completions"
    model: str = ""
    api_style: str = "openai-chat-completions"
    temperature: float | None = 0.3
    top_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    thinking: str | None = "enabled"
    reasoning_effort: str | None = "high"
    extra_body: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, values: Mapping[str, str], *,
                 provider: str = "openai-compatible") -> "OpenAIChatConfig":
        if provider not in {"openai-compatible", "opencode"}:
            raise ValueError(f"unsupported OpenAI-compatible provider: {provider}")
        api_style = "openai-chat-completions"
        if provider == "openai-compatible":
            api_style = values.get("PCUI_LLM_API_STYLE", api_style).strip()
            if api_style != "openai-chat-completions":
                raise ValueError("PCUI_LLM_API_STYLE currently supports only openai-chat-completions")
        thinking, effort = _thinking_controls(values)
        reserved = {"model", "messages", "stream", "max_tokens", "tools", "tool_choice",
                    "temperature", "top_p", "presence_penalty", "frequency_penalty", "seed",
                    "thinking", "reasoning_effort"}
        extra = _extra_body(values, reserved)
        if provider == "opencode":
            base_url = values.get("PCUI_OC_BASE_URL", OPENCODE_BASE_URL).strip()
            model = values.get("PCUI_OC_MODEL", OPENCODE_MODEL).strip()
            api_key = values.get("PCUI_OC_API_KEY", "").strip()
            api_path = "/chat/completions"
        else:
            base_url = values.get("PCUI_LLM_BASE_URL", "https://api.openai.com/v1").strip()
            model = values.get("PCUI_LLM_MODEL", "").strip()
            api_key = values.get("PCUI_LLM_API_KEY", "").strip()
            api_path = values.get("PCUI_LLM_API_PATH", "/chat/completions").strip()
        if not base_url or not model or not api_path.startswith("/"):
            raise ValueError(
                f"{provider} base URL/model must be non-empty and API path must start with /")
        return cls(
            api_key=api_key, provider=provider, base_url=base_url,
            api_path=api_path, model=model,
            api_style=api_style,
            temperature=_optional_float(values, "PCUI_LLM_TEMPERATURE")
            if "PCUI_LLM_TEMPERATURE" in values else 0.3,
            top_p=_optional_float(values, "PCUI_LLM_TOP_P"),
            presence_penalty=_optional_float(values, "PCUI_LLM_PRESENCE_PENALTY"),
            frequency_penalty=_optional_float(values, "PCUI_LLM_FREQUENCY_PENALTY"),
            seed=_optional_int(values, "PCUI_LLM_SEED"),
            thinking=thinking,
            reasoning_effort=effort,
            extra_body=extra,
        )

    def safe_summary(self) -> str:
        thinking = self.thinking or "provider-default"
        effort = self.reasoning_effort if self.thinking == "enabled" else "n/a"
        return (f"{self.provider}/{self.api_style}, model={self.model}, thinking={thinking}, "
                f"reasoning_effort={effort}")


class OpenAICompatibleProvider:
    """Blocking OpenAI chat-completions client used behind the provider protocol seam."""

    def __init__(self, config: OpenAIChatConfig):
        self.config = config
        self.model = config.model

    @property
    def thinking_enabled(self) -> bool:
        return self.config.thinking == "enabled"

    def safe_summary(self) -> str:
        return self.config.safe_summary()

    def with_thinking(self, enabled: bool) -> "OpenAICompatibleProvider":
        """Return an isolated surface-specific client without mutating shared config."""
        return OpenAICompatibleProvider(replace(
            self.config, thinking="enabled" if enabled else "disabled"))

    def chat(self, messages: list[dict], tools: list[dict], *, max_tokens: int,
             tool_choice: str, timeout: float) -> ChatResult:
        cfg = self.config
        payload: dict = {"model": cfg.model, "messages": messages, "stream": False,
                         "max_tokens": max_tokens, **cfg.extra_body}
        for key, value in (("temperature", cfg.temperature), ("top_p", cfg.top_p),
                           ("presence_penalty", cfg.presence_penalty),
                           ("frequency_penalty", cfg.frequency_penalty), ("seed", cfg.seed)):
            if value is not None and cfg.thinking != "enabled":
                payload[key] = value
        if cfg.thinking is not None:
            payload["thinking"] = {"type": cfg.thinking}
        if cfg.thinking == "enabled" and cfg.reasoning_effort is not None:
            payload["reasoning_effort"] = cfg.reasoning_effort
        # Keep the tool definitions in the payload even on the forced-final (tool_choice=none)
        # call: they are part of the cached request prefix, so dropping them would break upstream
        # prefix caching on the most common terminal call. tool_choice=none still forbids calls.
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        headers = {"Authorization": f"Bearer {cfg.api_key}", "User-Agent": USER_AGENT}
        if cfg.provider == "opencode":
            headers["x-opencode-session"] = _conversation_id(messages)
        doc = _post_json(f"{cfg.base_url.rstrip('/')}{cfg.api_path}", payload, headers, timeout)
        try:
            message = doc["choices"][0]["message"]
        except (LookupError, TypeError) as exc:
            raise LlmUnavailable("llm upstream returned an unparseable response") from exc
        usage = doc.get("usage") or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        # DeepSeek names the cache split; OpenAI-style upstreams (OpenCode) report only the hits.
        details = usage.get("prompt_tokens_details") or {}
        hit = int(usage.get("prompt_cache_hit_tokens") or details.get("cached_tokens") or 0)
        miss = int(usage.get("prompt_cache_miss_tokens") or (prompt - hit if details else 0))
        return ChatResult(message, prompt, int(usage.get("completion_tokens") or 0), hit, miss)


@dataclass(frozen=True)
class AnthropicMessagesConfig:
    """DeepSeek's official Anthropic Messages endpoint (the fallback behind OpenCode Go)."""

    api_key: str
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com/anthropic"
    api_path: str = "/v1/messages"
    model: str = "deepseek-flash"
    api_style: str = "anthropic-messages"
    temperature: float | None = 0.3
    top_p: float | None = None
    thinking: str | None = "enabled"
    reasoning_effort: str | None = "high"
    extra_body: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, values: Mapping[str, str], *,
                 provider: str = "deepseek") -> "AnthropicMessagesConfig":
        if provider != "deepseek":
            raise ValueError(f"unsupported Anthropic Messages provider: {provider}")
        # A named provider has a fixed wire protocol; the generic PCUI_LLM_API_STYLE flag does not apply.
        api_style = "anthropic-messages"
        thinking, effort = _thinking_controls(values)
        reserved = {"model", "messages", "system", "stream", "max_tokens", "tools",
                    "tool_choice", "temperature", "top_p", "thinking", "output_config"}
        extra = _extra_body(values, reserved)
        base_url = values.get(
            "PCUI_DEEPSEEK_ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic").strip()
        api_path = values.get("PCUI_DEEPSEEK_ANTHROPIC_API_PATH", "/v1/messages").strip()
        model = values.get("PCUI_DEEPSEEK_MODEL", values.get("DEEPSEEK_MODEL", "deepseek-flash")).strip()
        api_key = values.get(
            "PCUI_DEEPSEEK_API_KEY",
            values.get("PCUI_LLM_API_KEY", values.get("DEEPSEEK_API_KEY", ""))).strip()
        if not base_url or not model or not api_path.startswith("/"):
            raise ValueError(
                f"{provider} base URL/model must be non-empty and API path must start with /")
        return cls(
            api_key=api_key, provider=provider, base_url=base_url, api_path=api_path,
            model=model, api_style=api_style,
            temperature=_optional_float(values, "PCUI_LLM_TEMPERATURE")
            if "PCUI_LLM_TEMPERATURE" in values else 0.3,
            top_p=_optional_float(values, "PCUI_LLM_TOP_P"),
            thinking=thinking, reasoning_effort=effort, extra_body=extra,
        )

    def safe_summary(self) -> str:
        thinking = self.thinking or "provider-default"
        effort = self.reasoning_effort if self.thinking == "enabled" else "provider-default"
        return (f"{self.provider}/{self.api_style}, model={self.model}, thinking={thinking}, "
                f"reasoning_effort={effort}")


def _anthropic_request_messages(messages: list[dict]) -> tuple[str | None, list[dict]]:
    system: list[str] = []
    converted: list[dict] = []

    def append(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})

    for message in messages:
        role = message.get("role")
        if role == "system":
            content = str(message.get("content") or "").strip()
            if content:
                system.append(content)
            continue
        if role == "tool":
            append("user", [{"type": "tool_result",
                             "tool_use_id": str(message.get("tool_call_id") or ""),
                             "content": str(message.get("content") or "")}])
            continue
        if role not in {"user", "assistant"}:
            raise ValueError(f"unsupported chat role for Anthropic Messages: {role!r}")
        blocks: list[dict] = []
        content = message.get("content")
        if isinstance(content, str) and content:
            blocks.append({"type": "text", "text": content})
        elif isinstance(content, list):
            blocks.extend(block for block in content if isinstance(block, dict))
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                arguments = function.get("arguments") or "{}"
                try:
                    parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
                except ValueError:
                    parsed = {}
                if not isinstance(parsed, dict):
                    parsed = {}
                blocks.append({"type": "tool_use", "id": str(call.get("id") or ""),
                               "name": str(function.get("name") or ""), "input": parsed})
        append(str(role), blocks)
    return "\n\n".join(system) or None, converted


def _anthropic_tools(tools: list[dict]) -> list[dict]:
    converted = []
    for tool in tools:
        function = tool.get("function") or {}
        row = {"name": str(function.get("name") or ""),
               "input_schema": function.get("parameters") or {"type": "object"}}
        if function.get("description"):
            row["description"] = str(function["description"])
        converted.append(row)
    return converted


def _anthropic_response_message(content: object) -> dict:
    if not isinstance(content, list):
        raise LlmUnavailable("llm upstream returned an unparseable response")
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        elif block.get("type") == "tool_use":
            arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
            tool_calls.append({
                "id": str(block.get("id") or ""), "type": "function",
                "function": {"name": str(block.get("name") or ""),
                             "arguments": json.dumps(arguments, ensure_ascii=False)},
            })
    message: dict = {"role": "assistant", "content": "\n".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


class AnthropicMessagesProvider:
    """Blocking Anthropic Messages adapter exposed through the app's OpenAI-shaped seam."""

    def __init__(self, config: AnthropicMessagesConfig):
        self.config = config
        self.model = config.model

    @property
    def thinking_enabled(self) -> bool:
        return self.config.thinking == "enabled"

    def safe_summary(self) -> str:
        return self.config.safe_summary()

    def with_thinking(self, enabled: bool) -> "AnthropicMessagesProvider":
        return AnthropicMessagesProvider(replace(
            self.config, thinking="enabled" if enabled else "disabled"))

    def with_model(self, model: str) -> "AnthropicMessagesProvider":
        if not model.strip():
            raise ValueError("model must be non-empty")
        return AnthropicMessagesProvider(replace(self.config, model=model.strip()))

    def chat(self, messages: list[dict], tools: list[dict], *, max_tokens: int,
             tool_choice: str, timeout: float) -> ChatResult:
        cfg = self.config
        system, anthropic_messages = _anthropic_request_messages(messages)
        payload: dict = {"model": cfg.model, "messages": anthropic_messages,
                         "stream": False, "max_tokens": max_tokens, **cfg.extra_body}
        if system:
            payload["system"] = system
        if cfg.thinking is not None:
            payload["thinking"] = {"type": cfg.thinking}
        if cfg.thinking != "enabled":
            if cfg.temperature is not None:
                payload["temperature"] = cfg.temperature
            if cfg.top_p is not None:
                payload["top_p"] = cfg.top_p
        # Omitting tools on the forced-final call makes `none` a hard gate instead of relying on
        # the upstream honouring the field. The orchestration still keeps the full tool loop internally.
        if tools and tool_choice != "none":
            payload["tools"] = _anthropic_tools(tools)
            payload["tool_choice"] = {"type": "auto"}
        if cfg.thinking == "enabled" and cfg.reasoning_effort is not None:
            payload["output_config"] = {"effort": cfg.reasoning_effort}
        headers = {"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01",
                   "Content-Type": "application/json", "User-Agent": USER_AGENT}
        doc = _post_json(
            f"{cfg.base_url.rstrip('/')}{cfg.api_path}", payload, headers, timeout)
        try:
            message = _anthropic_response_message(doc["content"])
        except (LookupError, TypeError) as exc:
            raise LlmUnavailable("llm upstream returned an unparseable response") from exc
        usage = doc.get("usage") or {}
        cache_hit = int(usage.get("cache_read_input_tokens") or 0)
        cache_create = int(usage.get("cache_creation_input_tokens") or 0)
        input_tokens = int(usage.get("input_tokens") or 0) + cache_hit + cache_create
        return ChatResult(message, input_tokens, int(usage.get("output_tokens") or 0),
                          cache_hit, int(usage.get("input_tokens") or 0) + cache_create)


class FallbackProvider:
    """Retry an unavailable primary once on an independently configured provider."""

    def __init__(self, primary: LlmProvider, fallback: LlmProvider):
        self.primary = primary
        self.fallback = fallback
        self.model = getattr(primary, "model", "unknown")

    @property
    def thinking_enabled(self) -> bool:
        return bool(getattr(self.primary, "thinking_enabled", False))

    def chat(self, messages: list[dict], tools: list[dict], *, max_tokens: int,
             tool_choice: str, timeout: float) -> ChatResult:
        started = time.monotonic()
        try:
            return self.primary.chat(messages, tools, max_tokens=max_tokens,
                                     tool_choice=tool_choice, timeout=timeout)
        except LlmUnavailable as primary_error:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise primary_error
            try:
                return self.fallback.chat(messages, tools, max_tokens=max_tokens,
                                          tool_choice=tool_choice, timeout=remaining)
            except LlmUnavailable as fallback_error:
                raise LlmUnavailable(
                    "primary and fallback LLM providers unavailable "
                    f"({primary_error}; {fallback_error})") from fallback_error

    def safe_summary(self) -> str:
        primary = getattr(self.primary, "safe_summary", None)
        fallback = getattr(self.fallback, "safe_summary", None)
        primary_text = primary() if primary else type(self.primary).__name__
        fallback_text = fallback() if fallback else type(self.fallback).__name__
        return f"{primary_text}; fallback={fallback_text}"


class DeepSeekProvider(AnthropicMessagesProvider):
    """Backward-compatible constructor using DeepSeek's Anthropic Messages endpoint."""

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com/anthropic",
                 model: str = "deepseek-flash"):
        super().__init__(AnthropicMessagesConfig(
            api_key=api_key, provider="deepseek", base_url=base_url, model=model))


def _first_name(rows: object, used: set[str] | None = None) -> str | None:
    for row in rows if isinstance(rows, list) else []:
        name = row.get("name") if isinstance(row, dict) else None
        if isinstance(name, str) and name and (used is None or name not in used):
            return name
    return None


@lru_cache(maxsize=1)
def _mega_stone_hosts() -> dict[str, frozenset[str]]:
    """Return the dex-authoritative Mega-stone -> base-species relation.

    Echo assembly must screen an own stone that falls outside the size-capped ``mega_options``.
    The dex already carries both sides of that relation; spelling heuristics duplicate that
    authority and can drift whenever a non-standard stone name is added.
    """
    db = SKILLS_ROOT / "pokemon-champions-dex" / "data" / "champions_dex.sqlite"
    uri = f"file:{db.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            "select i.canonical, p.canonical, p.base_species "
            "from items i join pokemon p on p.required_item=i.canonical "
            "where i.category='mega_stone' and p.base_species is not null"
        ).fetchall()
    hosts: dict[str, set[str]] = {}
    for item, form, species in rows:
        hosts.setdefault(str(item), set()).update((str(form), str(species)))
    return {item: frozenset(species) for item, species in hosts.items()}


def _is_own_mega_stone(species: str, item: str) -> bool:
    hosts = _mega_stone_hosts().get(item, ())
    return species in hosts or species.removeprefix("Mega ") in hosts


def _echo_builder_reply(messages: list[dict]) -> str | None:
    """A grounded deterministic candidate for zero-cost release/browser rehearsals.

    Echo mode is test infrastructure, not a second production builder. It selects only values from
    the real builder prompt so the complete queue -> UEP -> SSE -> result UI can be smoke-tested.
    """
    payload = None
    for message in reversed(messages):
        try:
            doc = json.loads(str(message.get("content") or ""))
        except (TypeError, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("final_team"), list):
            return json.dumps({"rationale": "Echo rehearsal of the validator-confirmed final team."})
        if isinstance(doc, dict) and "usage_details" in doc and "skeletons" in doc:
            payload = doc
            break
    if not isinstance(payload, dict):
        return None

    details = payload.get("usage_details") if isinstance(payload.get("usage_details"), dict) else {}
    mega_options = [row for row in (payload.get("mega_options") or []) if isinstance(row, dict)]
    skeletons = [row for row in (payload.get("skeletons") or []) if isinstance(row, dict)]
    constraints = payload.get("constraints") if isinstance(payload.get("constraints"), dict) else {}
    wanted: list[str] = []

    def want(name: object) -> None:
        if isinstance(name, str) and name and name not in wanted:
            wanted.append(name)

    locked = [name for name in (constraints.get("locked") or [])
              if isinstance(name, str) and name]
    for name in locked:
        want(name)
    # Reserve the two Mega slots before broad skeleton/ranking fill. A future frame with six core
    # candidates must not crowd all Megas out of the deterministic release rehearsal.
    locked_bases = {name.removeprefix("Mega ") for name in locked}
    for option in mega_options:
        if len([name for name in wanted if name.startswith("Mega ")]) >= 2:
            break
        species = option.get("species")
        if isinstance(species, str) and species.removeprefix("Mega ") not in locked_bases:
            want(species)
    for skeleton in skeletons[:1]:
        for candidate in skeleton.get("core_candidates") or []:
            if isinstance(candidate, dict):
                want(candidate.get("species"))
    for name in details:
        want(name)

    mega_by_name = {row.get("species"): row for row in mega_options
                    if isinstance(row.get("species"), str)}
    # A base species holding any registered Mega stone counts toward the team's Mega registration
    # even when its species label is not prefixed with "Mega ". Reserve every stone for its explicit
    # Mega option so the deterministic rehearsal cannot accidentally assemble a third Mega from a
    # base species whose current modal item happens to be its stone.
    mega_items = {row.get("item") for row in mega_options
                  if isinstance(row.get("item"), str) and row.get("item")}
    used_items: set[str] = set()
    used_bases: set[str] = set()
    members: list[dict] = []

    def base_name(species: str) -> str:
        return species.removeprefix("Mega ")

    for species in wanted:
        if len(members) >= 6 or base_name(species) in used_bases:
            continue
        mega = mega_by_name.get(species)
        detail = details.get(species) or details.get(base_name(species))
        detail = detail if isinstance(detail, dict) else {}
        if mega is not None:
            item = mega.get("item")
            ability = mega.get("ability") or _first_name(detail.get("abilities"))
            nature = mega.get("nature") or _first_name(detail.get("natures"))
            moves = mega.get("moves") or [row.get("name") for row in detail.get("moves") or []
                     if isinstance(row, dict) and row.get("name")]
            spread = mega.get("spread")
        else:
            excluded_items = used_items | mega_items | {
                name for row in detail.get("items") or []
                if isinstance(row, dict) and isinstance((name := row.get("name")), str)
                and _is_own_mega_stone(species, name)
            }
            item = _first_name(detail.get("items"), excluded_items)
            ability = _first_name(detail.get("abilities"))
            nature = _first_name(detail.get("natures"))
            moves = [row.get("name") for row in detail.get("moves") or []
                     if isinstance(row, dict) and row.get("name")]
            spreads = detail.get("spreads") or []
            first_spread = spreads[0] if spreads and isinstance(spreads[0], dict) else {}
            spread = first_spread.get("spread")
        if not item or item in used_items or not ability or not nature or not moves:
            continue
        used_items.add(str(item))
        used_bases.add(base_name(species))
        members.append({"species": species, "item": item, "ability": ability,
                        "nature": nature, "moves": moves[:4], "spread": spread or {}})
    if len(members) != 6 or len([m for m in members if m["species"].startswith("Mega ")]) != 2:
        return None
    frame_id = skeletons[0].get("frame_id") if skeletons else None
    return json.dumps({"frame_id": frame_id, "pokemon": members,
                       "rationale": "Echo rehearsal assembled a grounded six-member team."})


class EchoProvider:
    """Dev provider (`online-serve --provider echo`): answers without any LLM so the page
    plumbing (limits, cookie, DTO shape) can be exercised end-to-end with zero cost."""

    def chat(self, messages: list[dict], tools: list[dict], *, max_tokens: int,
             tool_choice: str, timeout: float) -> ChatResult:
        if any("team-assembly engine" in str(message.get("content") or "")
               for message in messages if message.get("role") == "system"):
            reply = _echo_builder_reply(messages)
            if reply is not None:
                return ChatResult({"role": "assistant", "content": reply})
        question = str(messages[-1].get("content", ""))
        return ChatResult({"role": "assistant", "content": f"(echo) {question}"})
