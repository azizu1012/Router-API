"""OpenAI Responses protocol route helpers."""

from .format import (
    _error_type, _extract_finish_reason, _extract_response_text,
    _extract_response_tool_calls, _output_message, _output_text_part,
    _responses_envelope, _responses_event, _responses_usage,
    _spoof_model_name_in_response, _url_citations, _web_search_action,
    _web_search_call_item,
)
from .stream import _responses_sse_stream

