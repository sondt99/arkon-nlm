"""
Provider-agnostic types for multi-turn LLM agent loops with tool calling.

Used by wiki_agent.py to drive agent loops without importing any provider SDK.
Providers convert between these neutral types and their native API formats.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    id: str        # provider-assigned id, echoed back in tool result
    name: str
    arguments: dict


@dataclass
class AssistantTurn:
    text: Optional[str]                        # narration text (may be None)
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "end_turn"            # "tool_use" | "end_turn" | "max_tokens"
    # Provider-specific raw content for replay (e.g. Gemini Content with thought_signature).
    # Stored in the neutral message as "_raw_content" and used by the originating provider
    # to avoid reconstructing content that may lose internal metadata.
    raw_provider_content: Any = field(default=None)
    # {"input_tokens": int, "output_tokens": int} from the provider's real response, when available.
    usage: Optional[dict] = field(default=None)


# ---------------------------------------------------------------------------
# Neutral message builders — used by the agent loop
# ---------------------------------------------------------------------------

def assistant_message_from_turn(turn: AssistantTurn) -> dict:
    """Build neutral assistant message dict to append to message history."""
    msg: dict = {
        "role": "assistant",
        "content": turn.text,
        "tool_calls": turn.tool_calls,
    }
    if turn.raw_provider_content is not None:
        msg["_raw_content"] = turn.raw_provider_content
    return msg


def tool_results_message(results: list[tuple[str, str, Any]]) -> dict:
    """
    Build neutral tool-results message from (call_id, call_name, result) tuples.
    Result is JSON-serialized if not already a string.
    """
    return {
        "role": "user",
        "tool_results": [
            {
                "id": cid,
                "name": cname,
                "content": (
                    json.dumps(r, ensure_ascii=False, default=str)
                    if not isinstance(r, str) else r
                ),
            }
            for cid, cname, r in results
        ],
    }


# ---------------------------------------------------------------------------
# Neutral → provider-specific message converters (used inside providers)
# ---------------------------------------------------------------------------

def _user_text(msg: dict) -> str:
    """Flatten a user message for a provider with no cache-breakpoint concept.

    "cache_prefix" is an optimisation hint, never content the model may go without — a
    converter that ignored the key would silently drop the source document.
    """
    return (msg.get("cache_prefix") or "") + (msg.get("content") or "")


def neutral_to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Convert neutral messages to Anthropic API format.

    A user message may carry "cache_prefix": the invariant head of its content, which this
    converter emits as a separate leading text block with a cache breakpoint on it. In an
    agent loop the opening turn is re-sent on every step, so without a breakpoint the whole
    source document is re-read at full price once per step and once per page.
    """
    result = []
    for msg in messages:
        role = msg["role"]
        if role == "user":
            if "tool_results" in msg:
                result.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": r["id"],
                            "content": r["content"],
                        }
                        for r in msg["tool_results"]
                    ],
                })
            elif msg.get("cache_prefix"):
                result.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": msg["cache_prefix"],
                            "cache_control": {"type": "ephemeral"},
                        },
                        {"type": "text", "text": msg.get("content") or ""},
                    ],
                })
            else:
                result.append({"role": "user", "content": msg.get("content") or ""})
        elif role == "assistant":
            blocks: list[dict] = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for tc in msg.get("tool_calls", []):
                blocks.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.arguments,
                })
            result.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
    return result


def neutral_to_openai_messages(messages: list[dict]) -> list[dict]:
    """Convert neutral messages to OpenAI API format."""
    result = []
    for msg in messages:
        role = msg["role"]
        if role == "user":
            if "tool_results" in msg:
                for r in msg["tool_results"]:
                    result.append({
                        "role": "tool",
                        "tool_call_id": r["id"],
                        "content": r["content"],
                    })
            else:
                result.append({"role": "user", "content": _user_text(msg)})
        elif role == "assistant":
            m: dict = {"role": "assistant", "content": msg.get("content")}
            if msg.get("tool_calls"):
                m["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg["tool_calls"]
                ]
            result.append(m)
    return result


def neutral_to_gemini_contents(messages: list[dict]):
    """Convert neutral messages to Gemini Content objects.

    For assistant messages that carry '_raw_content' (a Gemini Content object
    stored during a previous turn), replay it directly to preserve thought_signature
    metadata that thinking models embed in function_call parts.
    """
    from google.genai import types as gtypes

    result = []
    for msg in messages:
        role = msg["role"]
        if role == "user":
            if "tool_results" in msg:
                parts = [
                    gtypes.Part(
                        function_response=gtypes.FunctionResponse(
                            name=r["name"],
                            response={"result": r["content"]},
                        )
                    )
                    for r in msg["tool_results"]
                ]
                result.append(gtypes.Content(role="user", parts=parts))
            else:
                result.append(gtypes.Content(
                    role="user",
                    parts=[gtypes.Part(text=_user_text(msg))]
                ))
        elif role == "assistant":
            # Use the raw Gemini Content if available — preserves thought_signature
            # that gemini-2.5 thinking models embed in function_call parts.
            if "_raw_content" in msg:
                result.append(msg["_raw_content"])
            else:
                parts = []
                if msg.get("content"):
                    parts.append(gtypes.Part(text=msg["content"]))
                for tc in msg.get("tool_calls", []):
                    parts.append(gtypes.Part(
                        function_call=gtypes.FunctionCall(
                            name=tc.name,
                            args=tc.arguments,
                        )
                    ))
                if not parts:
                    parts = [gtypes.Part(text="")]
                result.append(gtypes.Content(role="model", parts=parts))
    return result


# ---------------------------------------------------------------------------
# Provider (Anthropic wire format) → neutral converters
#
# Used by the Claude Code gateway (app/routers/claude_gateway.py) to translate
# an inbound Anthropic Messages API request into the neutral format the rest
# of this module (and every LLMProvider.generate_with_tools) expects.
# ---------------------------------------------------------------------------

def anthropic_tools_to_neutral(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool schemas ({name, description, input_schema}) to
    the OpenAI-style neutral shape generate_with_tools() expects."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for tool in tools
    ]


def anthropic_messages_to_neutral(messages: list[dict]) -> list[dict]:
    """Convert an Anthropic Messages API `messages` array to neutral messages.

    Anthropic message `content` is either a plain string or a list of blocks
    (text / image / tool_use / tool_result). Image blocks are dropped with a
    text placeholder — the neutral format and every provider's
    generate_with_tools() are text/tool-call only today.
    """
    result: list[dict] = []
    for msg in messages:
        role = msg["role"]
        content = msg.get("content")

        if isinstance(content, str) or content is None:
            if role == "assistant":
                result.append({"role": "assistant", "content": content or "", "tool_calls": []})
            else:
                result.append({"role": "user", "content": content or ""})
            continue

        if role == "user":
            tool_results = [
                {"id": b["tool_use_id"], "name": "", "content": _tool_result_text(b.get("content"))}
                for b in content
                if b.get("type") == "tool_result"
            ]
            if tool_results:
                result.append({"role": "user", "tool_results": tool_results})
                continue
            text_parts = [b["text"] for b in content if b.get("type") == "text"]
            if any(b.get("type") == "image" for b in content):
                text_parts.append("[image omitted]")
            result.append({"role": "user", "content": "\n".join(text_parts)})
        elif role == "assistant":
            text_parts = [b["text"] for b in content if b.get("type") == "text"]
            tool_calls = [
                ToolCall(id=b["id"], name=b["name"], arguments=b.get("input") or {})
                for b in content
                if b.get("type") == "tool_use"
            ]
            result.append({
                "role": "assistant",
                "content": "\n".join(text_parts) or None,
                "tool_calls": tool_calls,
            })
    return result


def _tool_result_text(content: Any) -> str:
    """Anthropic tool_result `content` is a string or a list of {type:"text", text} blocks."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def assistant_turn_to_anthropic_content(turn: AssistantTurn) -> list[dict]:
    """Convert a neutral AssistantTurn to Anthropic Messages API content blocks."""
    blocks: list[dict] = []
    if turn.text:
        blocks.append({"type": "text", "text": turn.text})
    for tc in turn.tool_calls:
        blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
    return blocks or [{"type": "text", "text": ""}]


# ---------------------------------------------------------------------------
# Provider-specific schema converters
# ---------------------------------------------------------------------------

def openai_tools_to_anthropic(tools: list[dict]) -> list[dict]:
    """Convert OpenAI-style tool schemas to Anthropic format."""
    result = []
    for tool in tools:
        fn = tool.get("function", {})
        result.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return result


def openai_tools_to_gemini(tools: list[dict]):
    """Convert OpenAI-style tool schemas to Gemini Tool objects."""
    from google.genai import types as gtypes

    declarations = []
    for tool in tools:
        fn = tool.get("function", {})
        params = fn.get("parameters", {})
        declarations.append(gtypes.FunctionDeclaration(
            name=fn["name"],
            description=fn.get("description", ""),
            parameters=_json_schema_to_gemini_schema(params),
        ))
    return [gtypes.Tool(function_declarations=declarations)]


def _json_schema_to_gemini_schema(schema: dict):
    """Recursively convert JSON Schema dict to Gemini Schema object."""
    from google.genai import types as gtypes

    type_map = {
        "string": gtypes.Type.STRING,
        "number": gtypes.Type.NUMBER,
        "integer": gtypes.Type.INTEGER,
        "boolean": gtypes.Type.BOOLEAN,
        "array": gtypes.Type.ARRAY,
        "object": gtypes.Type.OBJECT,
    }
    gemini_type = type_map.get((schema.get("type") or "string").lower(), gtypes.Type.STRING)

    props = None
    if schema.get("properties"):
        props = {k: _json_schema_to_gemini_schema(v) for k, v in schema["properties"].items()}

    items = None
    if schema.get("items"):
        items = _json_schema_to_gemini_schema(schema["items"])

    return gtypes.Schema(
        type=gemini_type,
        description=schema.get("description"),
        properties=props,
        required=schema.get("required"),
        items=items,
        enum=schema.get("enum"),
    )
