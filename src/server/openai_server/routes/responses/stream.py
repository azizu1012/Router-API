"""OpenAI Responses stream framing."""

import json
import time
import uuid

from .format import (
    _output_message, _output_text_part, _responses_envelope,
    _responses_event, _responses_usage, _web_search_call_item,
)
async def _responses_sse_stream(chunks, model: str = "",
                                search_trace: list | None = None,
                                input_tokens_hint: int = 0):
    """Re-frame chat-completions SSE into Responses API events.

    The chat proxy speaks the chat dialect: `data: {"choices":[{"delta":...}]}`.
    A Responses client parses typed events — `response.created`,
    `response.output_text.delta`, `response.completed` — and ignores anything
    else, so forwarding chat chunks verbatim produced a stream that opened and
    closed with no text in it. The model never failed; the client just had no
    event to print.

    The closing frames used to carry empty text and empty arrays, which the
    official SDK accepts for `output_item.done` but which discard the answer:
    `response.completed.output` is what a Responses client reads as the result,
    so an empty array there means a successful call that returns nothing.

    Tool calls are the same failure one level down. The adapter only ever read
    `delta.content`, so a turn that called a function produced a stream with no
    text at all: `output_text.done` with `text: ""` and `response.completed`
    with `output: []`. Every function call the model made was dropped, and the
    client got a successful response describing nothing it had asked for.
    """
    rid = f"resp_{uuid.uuid4().hex}"
    msg_id = f"msg_{uuid.uuid4().hex}"
    searches = list(search_trace or [])
    created = int(time.time())
    seq = 0
    parts: list[str] = []
    usage = None
    msg_open = False
    msg_index = 0
    # Output indices are handed out as items open, not as they close, so this
    # cannot be derived from `len(output)` — that stays 0 until the end.
    next_index = 0
    tools: dict[int, dict] = {}
    output: list = []

    def envelope(status, u=None):
        return _responses_envelope(rid, model, status, output, created, u)

    yield _responses_event("response.created", seq,
                           response=envelope("in_progress"))
    seq += 1
    yield _responses_event("response.in_progress", seq,
                           response=envelope("in_progress"))

    # A search the router ran for the model is a hosted tool call, not a
    # function call: OpenAI reports these as `web_search_call` items that come
    # before the message, with no client-side tool loop. Emitting a
    # `function_call` here would ask the client to run a tool it does not have.
    for record in searches:
        idx = next_index
        next_index += 1
        call_id = record.get("id") or f"ws_{uuid.uuid4().hex}"
        item = _web_search_call_item(call_id, record)
        yield _responses_event("response.output_item.added", seq,
                              output_index=idx, item=item)
        seq += 1
        yield _responses_event("response.web_search_call.in_progress", seq,
                              item_id=call_id, output_index=idx)
        seq += 1
        yield _responses_event("response.web_search_call.searching", seq,
                              item_id=call_id, output_index=idx)
        seq += 1
        yield _responses_event("response.web_search_call.completed", seq,
                              item_id=call_id, output_index=idx)
        seq += 1
        yield _responses_event("response.output_item.done", seq,
                              output_index=idx, item=item)
        seq += 1
        output.append(item)
    seq += 1

    async for raw in chunks:
        # The chat proxy yields bytes; a literal "[DONE]" ends the stream.
        if isinstance(raw, (bytes, bytearray)):
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
        else:
            text = str(raw)
        if not text.startswith("data:"):
            continue
        data = text[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("usage"):
            usage = _responses_usage(payload["usage"])

        for choice in payload.get("choices") or []:
            delta = choice.get("delta") or {}

            content = delta.get("content")
            if isinstance(content, str) and content:
                # The message item is opened on the first text fragment rather
                # than up front: a turn that only calls a function must not emit
                # an empty message item.
                if not msg_open:
                    msg_open = True
                    msg_index = next_index
                    next_index += 1
                    yield _responses_event(
                        "response.output_item.added", seq,
                        output_index=msg_index,
                        item={"id": msg_id, "type": "message",
                              "status": "in_progress", "content": [],
                              "role": "assistant"},
                    )
                    seq += 1
                    yield _responses_event(
                        "response.content_part.added", seq,
                        item_id=msg_id, output_index=msg_index, content_index=0,
                        part=_output_text_part(""),
                    )
                    seq += 1
                parts.append(content)
                yield _responses_event(
                    "response.output_text.delta", seq,
                    item_id=msg_id, output_index=msg_index, content_index=0,
                    delta=content, logprobs=[],
                )
                seq += 1

            for tc in delta.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                name = fn.get("name") or tc.get("name") or ""
                if name in ("WebSearch", "web_search", "WebFetch",
                            "web_fetch"):
                    # The router runs these itself and reports them as hosted
                    # tool calls. A function_call for them would ask the client
                    # to execute a tool the client was never given.
                    continue
                idx = int(tc.get("index", 0) or 0)
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                args = args if isinstance(args, str) else (
                    json.dumps(args) if args else "")
                entry = tools.get(idx)
                if entry is None:
                    fc_id = tc.get("id") or f"fc_{uuid.uuid4().hex}"
                    name = fn.get("name") or ""
                    entry = {"id": f"fc_{uuid.uuid4().hex}",
                             "call_id": fc_id, "name": name,
                             "arguments": "", "index": next_index}
                    next_index += 1
                    tools[idx] = entry
                    yield _responses_event(
                        "response.output_item.added", seq,
                        output_index=entry["index"],
                        item={"id": entry["id"], "type": "function_call",
                              "status": "in_progress",
                              "call_id": fc_id, "name": name, "arguments": ""},
                    )
                    seq += 1
                elif fn.get("name") and not entry["name"]:
                    entry["name"] = fn["name"]
                if not args:
                    continue
                entry["arguments"] += args
                yield _responses_event(
                    "response.function_call_arguments.delta", seq,
                    item_id=entry["id"], output_index=entry["index"],
                    delta=args,
                )
                seq += 1

    text = "".join(parts)
    cites = [c for r in searches for c in (r.get("citations") or [])
             if isinstance(c, dict)]
    if msg_open:
        yield _responses_event(
            "response.output_text.done", seq,
            item_id=msg_id, output_index=msg_index, content_index=0,
            text=text, logprobs=[],
        )
        seq += 1
        yield _responses_event(
            "response.content_part.done", seq,
            item_id=msg_id, output_index=msg_index, content_index=0,
            part=_output_text_part(text, cites),
        )
        seq += 1
        item = _output_message(msg_id, "completed", text, cites)
        yield _responses_event(
            "response.output_item.done", seq,
            output_index=msg_index, item=item,
        )
        seq += 1
        output.append(item)

    for idx in sorted(tools):
        entry = tools[idx]
        yield _responses_event(
            "response.function_call_arguments.done", seq,
            item_id=entry["id"], output_index=entry["index"],
            arguments=entry["arguments"], name=entry["name"],
        )
        seq += 1
        item = {"id": entry["id"], "type": "function_call",
                "status": "completed", "call_id": entry["call_id"],
                "name": entry["name"], "arguments": entry["arguments"]}
        yield _responses_event(
            "response.output_item.done", seq,
            output_index=entry["index"], item=item,
        )
        seq += 1
        output.append(item)

    # The chat layer only sends its trailing usage chunk when the client asks,
    # and sends nothing at all when the upstream stream is cut short.
    # `response.completed` is expected to carry usage either way, so fall back to
    # the same estimate the Anthropic path already reports: an estimate is closer
    # to the truth than null, which leaves a client unable to bill the call.
    final_usage = usage or _responses_usage({
        "prompt_tokens": input_tokens_hint,
        "completion_tokens": max(1, sum(len(p) for p in parts) // 4),
    })
    yield _responses_event(
        "response.completed", seq,
        response=envelope("completed", final_usage),
    )
    yield b"data: [DONE]\n\n"





