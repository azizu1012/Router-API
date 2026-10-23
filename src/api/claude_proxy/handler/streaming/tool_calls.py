"""Helpers for accumulating provider tool-call fragments."""

import json
import uuid
from typing import Any, Dict, Optional


def buffer_tool_calls(
    delta: Any,
    tool_buffers: Dict[int, Dict[str, Any]],
    index_map: Optional[Dict[int, int]] = None,
) -> None:
    """Accumulate streamed tool calls keyed by provider index.

    Providers send the name and id in one fragment, then argument fragments
    without either field. The wire shapes vary between dicts and SDK objects,
    so normalization belongs at this boundary.
    """
    tool_calls_val = getattr(delta, "tool_calls", None)
    if tool_calls_val is None and hasattr(delta, "get"):
        tool_calls_val = delta.get("tool_calls")
    if not tool_calls_val:
        return

    for tc in tool_calls_val:
        if isinstance(tc, dict):
            raw_idx = tc.get("index", 0)
            if raw_idx is None:
                raw_idx = 0
            fn = tc.get("function", {})
            fn_name = (
                fn.get("name", "")
                if isinstance(fn, dict)
                else getattr(fn, "name", "")
            )
            fn_args = (
                fn.get("arguments")
                if isinstance(fn, dict)
                else getattr(fn, "arguments", None)
            )
            generated_id = (
                f"toolu_{fn_name}_{uuid.uuid4().hex[:12]}"
                if fn_name
                else f"toolu_{uuid.uuid4().hex}"
            )
            tc_id = tc.get("id")
            id_explicit = tc_id is not None
            tc_id = tc_id or generated_id
        else:
            raw_idx = getattr(tc, "index", 0)
            if raw_idx is None:
                raw_idx = 0
            fn = getattr(tc, "function", {})
            fn_name = getattr(fn, "name", "")
            fn_args = getattr(fn, "arguments", None)
            generated_id = (
                f"toolu_{fn_name}_{uuid.uuid4().hex[:12]}"
                if fn_name
                else f"toolu_{uuid.uuid4().hex}"
            )
            tc_id = getattr(tc, "id", None)
            id_explicit = tc_id is not None
            tc_id = tc_id or generated_id

        # A provider may reuse an index for a second call, so an explicit new
        # id on an occupied slot means a new call even when the name matches:
        # without this, two same-name calls concatenate into one unparseable
        # args blob. The id must be explicit on the wire — argument-only
        # continuation fragments carry no id, and a generated placeholder must
        # never split a call in two.
        if index_map is not None:
            if raw_idx in index_map:
                buf_idx = index_map[raw_idx]
                existing_name = tool_buffers.get(buf_idx, {}).get("name", "")
                existing_id = tool_buffers.get(buf_idx, {}).get("id", "")
                if fn_name and existing_name and fn_name != existing_name:
                    buf_idx = max(tool_buffers.keys(), default=-1) + 1
                    index_map[raw_idx] = buf_idx
                elif (id_explicit and existing_id and tc_id != existing_id
                        and fn_args and not existing_id.startswith("toolu_")):
                    buf_idx = max(tool_buffers.keys(), default=-1) + 1
                    index_map[raw_idx] = buf_idx
            else:
                buf_idx = raw_idx if raw_idx not in tool_buffers else (max(tool_buffers.keys(), default=-1) + 1)
                index_map[raw_idx] = buf_idx
        else:
            buf_idx = raw_idx
            if buf_idx in tool_buffers:
                same_name = (fn_name and tool_buffers[buf_idx].get("name")
                             and tool_buffers[buf_idx]["name"] != fn_name)
                stored_id = tool_buffers[buf_idx].get("id", "")
                new_id = (id_explicit and stored_id
                          and stored_id != tc_id and fn_args
                          and not stored_id.startswith("toolu_"))
                if same_name or new_id:
                    buf_idx = max(tool_buffers.keys(), default=-1) + 1

        if buf_idx not in tool_buffers:
            generated_id = (
                f"toolu_{fn_name}_{uuid.uuid4().hex[:12]}"
                if fn_name
                else f"toolu_{uuid.uuid4().hex}"
            )
            tool_buffers[buf_idx] = {
                "id": tc_id,
                "name": fn_name,
                "args": "",
            }
        elif fn_name:
            tool_buffers[buf_idx]["name"] = fn_name

        if fn_args:
            if isinstance(fn_args, dict):
                args_str = json.dumps(fn_args)
            elif isinstance(fn_args, str):
                args_str = fn_args
            else:
                args_str = str(fn_args)
            # Providers disagree about how arguments arrive across chunks, and
            # every disagreement used to end in the same place: the arguments
            # string was concatenated past valid JSON, and the client was handed
            # a tool_use it could not parse.
            #
            #   * fragments      '{"a":' + '"b":1}'   -> concatenate
            #   * placeholder    '{}' then the body    -> replace the '{}'
            #   * repeat         the same fragment sent again with the final
            #                   chunk -> ignore it
            #
            # The repeat is not hypothetical: a provider that re-sends the whole
            # tool_call alongside finish_reason turned one call into two.
            current = tool_buffers[buf_idx]["args"]
            if current == args_str:
                continue
            if current == "{}":
                tool_buffers[buf_idx]["args"] = args_str
            else:
                tool_buffers[buf_idx]["args"] = current + args_str

