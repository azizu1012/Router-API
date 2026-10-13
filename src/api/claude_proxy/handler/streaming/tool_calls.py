"""Helpers for accumulating provider tool-call fragments."""

import json
import uuid
from typing import Any, Dict


def buffer_tool_calls(delta: Any, tool_buffers: Dict[int, Dict[str, Any]]) -> None:
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
            tc_idx = tc.get("index", 0)
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
            tc_id = tc.get("id", generated_id)
        else:
            tc_idx = getattr(tc, "index", 0)
            fn = getattr(tc, "function", {})
            fn_name = getattr(fn, "name", "")
            fn_args = getattr(fn, "arguments", None)
            generated_id = (
                f"toolu_{fn_name}_{uuid.uuid4().hex[:12]}"
                if fn_name
                else f"toolu_{uuid.uuid4().hex}"
            )
            tc_id = getattr(tc, "id", generated_id)

        if tc_idx not in tool_buffers:
            generated_id = (
                f"toolu_{fn_name}_{uuid.uuid4().hex[:12]}"
                if fn_name
                else f"toolu_{uuid.uuid4().hex}"
            )
            tool_buffers[tc_idx] = {
                "id": tc_id,
                "name": fn_name,
                "args": "",
            }
        elif fn_name:
            tool_buffers[tc_idx]["name"] = fn_name

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
            current = tool_buffers[tc_idx]["args"]
            if current == args_str:
                continue
            if current == "{}":
                tool_buffers[tc_idx]["args"] = args_str
            else:
                tool_buffers[tc_idx]["args"] = current + args_str

