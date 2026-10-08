"""Responses API → chat-completions dialect.

/v1/responses originally forwarded only model, input, temperature, top_p and
max_output_tokens. Everything else a Responses client sends was dropped without
a word — no log, no error, just an answer that ignored the question:

    tools: [{"type": "web_search"}]   server-side search asked for, never ran
    instructions: "..."               the system prompt, gone
    stream: true                      client waited for SSE, got a JSON blob

That last one matters most: a client asking for search and silently receiving an
unsearched answer has no way to notice. It reads as a confident hallucination.

The mapping also settles which search engine a request gets, which is why it
lives here rather than in the route. A Responses client is asking for the hosted
capability, so it gets grounding first with DuckDuckGo behind it. Everything else
on the chat paths defaults to DuckDuckGo and spends no Gemini quota at all.
"""

from typing import Any, Dict, List, Tuple

# Responses names hosted tools by type; the chat paths speak function tools.
_HOSTED_SEARCH_TYPES = {"web_search", "web_search_preview", "web_search_2025_08_26"}
_HOSTED_FETCH_TYPES = {"web_fetch", "url_context"}


def _text_of(item: Any) -> str:
    """Flatten a Responses input item or a content part to plain text.

    Handles lists, because Responses content is a list of parts
    ([{"type": "input_text", "text": "..."}]) far more often than a bare string,
    and falling through to str() there turns the whole structure into text.
    """
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    if isinstance(item, list):
        return "".join(_text_of(part) for part in item)
    if isinstance(item, dict):
        if isinstance(item.get("text"), str):
            return item["text"]
        if isinstance(item.get("output_text"), str):
            return item["output_text"]
        if "content" in item:
            return _text_of(item["content"])
    return str(item)


def split_tools(tools: Any) -> Tuple[List[Dict[str, Any]], List[str], bool, bool]:
    """Split a Responses tools array into what the chat path can and cannot use.

    Returns (function_tools, hosted_types, wants_search, wants_fetch). Hosted
    tools have no chat-path equivalent as a function — the router implements
    them — so they come back as names rather than as schemas.
    """
    functions: List[Dict[str, Any]] = []
    hosted: List[str] = []
    wants_search = False
    wants_fetch = False

    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        kind = tool.get("type")
        if kind in _HOSTED_SEARCH_TYPES:
            wants_search = True
            hosted.append("web_search")
            continue
        if kind in _HOSTED_FETCH_TYPES:
            wants_fetch = True
            hosted.append("web_fetch")
            continue
        if kind in (None, "function"):
            fn = tool.get("function") or tool
            name = fn.get("name")
            if name:
                functions.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": fn.get("description", "") or "",
                        "parameters": fn.get("parameters") or fn.get("input_schema")
                        or {"type": "object", "properties": {}},
                    },
                })
    return functions, hosted, wants_search, wants_fetch


def responses_to_chat_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a Responses request into the chat-completions dialect.

    Search handling: a hosted web_search tool becomes the chat path's
    web_search flag with search_engine auto, which is grounding first and
    DuckDuckGo behind it. A Responses client asked for the hosted capability,
    so it gets the stronger engine. Nothing else on the chat paths sets this
    flag, and with it unset they stay on DuckDuckGo at no Gemini quota.
    """
    chat: Dict[str, Any] = {}

    model = body.get("model")
    if model:
        chat["model"] = model

    messages: List[Dict[str, Any]] = []

    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})
    elif isinstance(instructions, list):
        joined = "".join(_text_of(part) for part in instructions).strip()
        if joined:
            messages.append({"role": "system", "content": joined})

    raw_input = body.get("input")

    # A string input is one user turn, not a sequence of characters. Iterating it
    # produced a message per letter.
    if isinstance(raw_input, str):
        if raw_input:
            messages.append({"role": "user", "content": raw_input})
    elif isinstance(raw_input, list):
        for item in raw_input:
            if isinstance(item, dict) and item.get("role"):
                text = _text_of(item.get("content"))
                if text:
                    messages.append({"role": item.get("role"), "content": text})
            else:
                text = _text_of(item)
                if text:
                    messages.append({"role": "user", "content": text})
    elif raw_input is not None:
        text = _text_of(raw_input)
        if text:
            messages.append({"role": "user", "content": text})

    chat["messages"] = messages

    if "temperature" in body and body["temperature"] is not None:
        chat["temperature"] = body["temperature"]
    if "top_p" in body and body["top_p"] is not None:
        chat["top_p"] = body["top_p"]
    max_out = body.get("max_output_tokens") or body.get("max_tokens")
    if max_out:
        chat["max_tokens"] = max_out

    # Read the client's engine choice before the hosted tools are handled below,
    # so it is already on chat when the default is applied.
    explicit_engine = (body.get("search_engine") or "").strip().lower()
    if explicit_engine:
        chat["search_engine"] = explicit_engine

    functions, hosted, wants_search, wants_fetch = split_tools(body.get("tools"))
    if functions:
        chat["tools"] = functions
    if body.get("tool_choice") is not None:
        chat["tool_choice"] = body["tool_choice"]

    if wants_search:
        chat["web_search"] = True
        # Marks this as the Responses dialect asking for hosted search, which is
        # the only place the router injects its WebSearch tool. Without the
        # marker the injection gate reads "not asked" and Responses silently
        # loses search.
        chat["_hosted_search"] = True
        # Only set the engine when the client did not name one. An explicit
        # search_engine in the request outranks the dialect default, and this
        # function is what builds the body, so clobbering it here would make
        # resolve_search_engine's first precedence rule unreachable.
        if not chat.get("search_engine"):
            chat["search_engine"] = "auto"
    if wants_fetch:
        chat["web_fetch"] = True

    # reasoning effort, spelled the Responses way
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        chat["reasoning_effort"] = reasoning["effort"]

    if body.get("stream"):
        chat["stream"] = True

    return chat


def hosted_tool_names(body: Dict[str, Any]) -> List[str]:
    """Which hosted tools the client asked for. For logging and for the reply."""
    _, hosted, _, _ = split_tools(body.get("tools"))
    return hosted