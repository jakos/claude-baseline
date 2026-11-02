"""The request/response drivers of scripts/implement.py: one object per models.toml driver.

Each driver maps a resolved profile to one provider's wire format: the URL and headers
of a request, the body built from the system prompt, the conversation and the tool list,
the reading of a response (text, tool calls, refusal), the usage mapping and the
appending of the assistant message and the tool results. implement.py keeps the retry,
timeout, fallback and tool logic and calls only this module, so the provider mapping is
unit-testable without a subprocess or a server.

Drivers: "openai-loop" (OpenAI-compatible chat completions, POST <base_url>/chat/
completions) and "anthropic-loop" (the native Messages API, POST <base_url>/messages,
with prompt caching). Stdlib only; no network code lives here.

Messages API notes (anthropic-loop), pinned to API version ANTHROPIC_VERSION:
- any temperature, top_p or top_k is a 400 on every model, so the driver never sends
  them (models.py rejects temperature on an anthropic-loop profile);
- the system prompt goes top-level as a list of text blocks; the request carries two
  cache_control breakpoints, on the system block and on the last content block of the
  last message, built on a copy so the stored conversation never holds markers;
- tool results go back as ONE user message whose content is tool_result blocks;
- usage: input_tokens is the uncached remainder only, so the total prompt is
  input_tokens + cache_creation_input_tokens + cache_read_input_tokens.
"""

import json

ANTHROPIC_VERSION = "2026-10-21"
ANTHROPIC_DEFAULT_MAX_TOKENS = 16000


class Malformed(Exception):
    """The provider's response cannot be read; the message says which part."""


class Reply:
    """One parsed assistant response.

    text: the joined text content (a str) or None; calls: a list of (id, name,
    arguments) tuples — arguments is a JSON string for openai-loop, an object for
    anthropic-loop; refused: True when the model refused the request; message: the
    assistant message to append to the conversation.
    """

    def __init__(self, text, calls, refused, message):
        self.text = text
        self.calls = calls
        self.refused = refused
        self.message = message


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class OpenAILoop:
    """OpenAI-compatible chat completions: POST <base_url>/chat/completions."""

    def url(self, profile: dict) -> str:
        return profile["base_url"].rstrip("/") + "/chat/completions"

    def headers(self, key: str | None) -> dict:
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def start(self, system: str, user: str) -> list:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def body(self, profile: dict, system: str, conversation: list, tools: list) -> dict:
        body = {
            "model": profile["model"],
            "messages": conversation,
            "tools": tools,
            "tool_choice": "auto",
        }
        if profile["temperature"] is not None:
            body["temperature"] = profile["temperature"]
        if profile["max_output_tokens"] is not None:
            body[profile["max_tokens_param"]] = profile["max_output_tokens"]
        body.update(profile["extra_body"] or {})
        return body

    def read(self, payload) -> Reply:
        try:
            message = payload["choices"][0]["message"]
            raw_calls = message.get("tool_calls") or []
            content = message.get("content")
        except (KeyError, IndexError, TypeError, AttributeError):
            raise Malformed("malformed response from the model") from None
        calls = []
        tool_calls = []
        for call in raw_calls:
            try:
                call_id = str(call["id"])
                function = call["function"]
                tool_name = function["name"]
                raw = function.get("arguments") or "{}"
            except (KeyError, TypeError):
                raise Malformed("malformed tool call from the model") from None
            calls.append((call_id, tool_name, raw))
            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": raw if isinstance(raw, str) else json.dumps(raw),
                    },
                }
            )
        return Reply(
            text=content,
            calls=calls,
            refused=False,
            message={"role": "assistant", "content": content, "tool_calls": tool_calls},
        )

    def usage(self, payload) -> dict:
        usage = payload.get("usage") if isinstance(payload, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        prompt = _int(usage.get("prompt_tokens"))
        details = usage.get("prompt_tokens_details")
        cached = _int(details.get("cached_tokens")) if isinstance(details, dict) else 0
        return {
            "input": prompt,
            "cached": min(cached, prompt),
            "cache_write": 0,
            "output": _int(usage.get("completion_tokens")),
        }

    def append(self, conversation: list, reply: Reply, results: list) -> None:
        conversation.append(reply.message)
        for call_id, text in results:
            conversation.append(
                {"role": "tool", "tool_call_id": call_id, "content": text}
            )


class AnthropicLoop:
    """The native Messages API: POST <base_url>/messages, with prompt caching."""

    def url(self, profile: dict) -> str:
        return profile["base_url"].rstrip("/") + "/messages"

    def headers(self, key: str | None) -> dict:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        if key:
            headers["x-api-key"] = key
        return headers

    def start(self, system: str, user: str) -> list:
        # the first user message's content is a one-element list of a text block, so
        # body() can put the cache breakpoint on its last block
        return [{"role": "user", "content": [{"type": "text", "text": user}]}]

    def body(self, profile: dict, system: str, conversation: list, tools: list) -> dict:
        max_tokens = profile["max_output_tokens"]
        if max_tokens is None:
            max_tokens = ANTHROPIC_DEFAULT_MAX_TOKENS
        body = {
            "model": profile["model"],
            "max_tokens": max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "tools": [
                {
                    "name": tool["function"]["name"],
                    "description": tool["function"]["description"],
                    "input_schema": tool["function"]["parameters"],
                }
                for tool in tools
            ],
            "tool_choice": {"type": "auto"},
            "messages": _with_breakpoint(conversation),
        }
        body.update(profile["extra_body"] or {})
        return body

    def read(self, payload) -> Reply:
        if not isinstance(payload, dict):
            raise Malformed("malformed response from the model")
        content = payload.get("content")
        if not isinstance(content, list) or not all(
            isinstance(block, dict) for block in content
        ):
            raise Malformed("malformed response from the model")
        texts = []
        calls = []
        for block in content:
            kind = block.get("type")
            if kind == "text":
                text = block.get("text")
                if isinstance(text, str):
                    texts.append(text)
            elif kind == "tool_use":
                try:
                    call_id = str(block["id"])
                    tool_name = block["name"]
                except (KeyError, TypeError):
                    raise Malformed("malformed tool call from the model") from None
                calls.append((call_id, tool_name, block.get("input") or {}))
        return Reply(
            text="\n".join(texts) if texts else None,
            calls=calls,
            refused=payload.get("stop_reason") == "refusal",
            message={"role": "assistant", "content": content},
        )

    def usage(self, payload) -> dict:
        usage = payload.get("usage") if isinstance(payload, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        cache_write = _int(usage.get("cache_creation_input_tokens"))
        cached = _int(usage.get("cache_read_input_tokens"))
        uncached = _int(usage.get("input_tokens"))
        return {
            "input": uncached + cache_write + cached,
            "cached": cached,
            "cache_write": cache_write,
            "output": _int(usage.get("output_tokens")),
        }

    def append(self, conversation: list, reply: Reply, results: list) -> None:
        # the assistant content (text, thinking and tool_use blocks) goes back
        # unchanged; all results of one turn go in ONE user message of tool_result
        # blocks, in call order
        conversation.append(reply.message)
        conversation.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": call_id, "content": text}
                    for call_id, text in results
                ],
            }
        )


def _with_breakpoint(conversation: list) -> list:
    """A copy of the conversation with a cache_control marker on the last content
    block of the last message; the stored conversation is never modified."""
    messages = [dict(message) for message in conversation]
    if not messages:
        return messages
    last = dict(messages[-1])
    content = last.get("content")
    marker = {"type": "ephemeral"}
    if isinstance(content, list) and content:
        blocks = [
            dict(block) if isinstance(block, dict) else block for block in content
        ]
        if isinstance(blocks[-1], dict):
            blocks[-1] = {**blocks[-1], "cache_control": marker}
        else:
            blocks[-1] = {
                "type": "text",
                "text": str(blocks[-1]),
                "cache_control": marker,
            }
        last["content"] = blocks
    elif isinstance(content, str):
        last["content"] = [{"type": "text", "text": content, "cache_control": marker}]
    messages[-1] = last
    return messages


_DRIVERS = {
    "openai-loop": OpenAILoop(),
    "anthropic-loop": AnthropicLoop(),
}


def get(name: str):
    """The driver object for a models.toml driver name."""
    return _DRIVERS[name]
