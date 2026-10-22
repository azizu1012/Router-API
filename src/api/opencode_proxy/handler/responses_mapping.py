"""Responses API to chat-completions dialect.

/v1/responses originally forwarded only model, input, temperature, top_p and
max_output_tokens. Everything else a Responses client sends was dropped without
a word - no log, no error, just an answer that ignored the question:

    tools: [{type: web_search}]   server-side search asked for, never ran
    instructions: ...             the system prompt, gone
    stream: true                  client waited for SSE, got a JSON blob

That last one matters most: a client asking for search and silently receiving an
unsearched answer has no way to notice. It reads as a confident hallucination.

The mapping also marks hosted search requests. The Router executes those through
its own citation search service, so provider-specific search tools never reach a
model endpoint.

OpenCat-driven additions: multimodal input (input_image / input_file) is
preserved as chat content blocks instead of being stringified, and the
Responses JSON mode (text.format / response_format) is forwarded so a client
asking for strict JSON actually gets it."""


from typing import Any, Dict, List, Tuple

from src.core.runtime.effort import normalize_reasoning_effort


_HOSTED_SEARCH_TYPES = {'web_search', 'web_search_preview', 'web_search_2025_08_26'}
_HOSTED_FETCH_TYPES = {'web_fetch', 'url_context'}


def _text_of(item: Any) -> str:
    if item is None:
        return ''
    if isinstance(item, str):
        return item
    if isinstance(item, list):
        return ''.join(_text_of(part) for part in item)
    if isinstance(item, dict):
        if isinstance(item.get('text'), str):
            return item['text']
        if isinstance(item.get('output_text'), str):
            return item['output_text']
        if 'content' in item:
            return _text_of(item['content'])
    return str(item)
def _content_parts(content: Any) -> List[Dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}] if content else []
    if isinstance(content, list):
        out: List[Dict[str, Any]] = []
        for part in content:
            out.extend(_content_parts(part))
        return out
    if isinstance(content, dict):
        kind = content.get('type')
        if kind in ('input_text', 'text', 'output_text'):
            text = content.get('text', '')
            return [{'type': 'text', 'text': text}] if text else []
        if kind == 'input_image':
            image_url = content.get('image_url')
            if isinstance(image_url, str) and image_url:
                return [{'type': 'image_url', 'image_url': {'url': image_url}}]
            if isinstance(image_url, dict) and image_url.get('url'):
                return [{'type': 'image_url', 'image_url': {'url': image_url['url']}}]
            file_id = content.get('file_id') or ''
            if file_id:
                return [{'type': 'text',
                         'text': '[Attached image: file ' + str(file_id)
                                 + ' kept, no URL supplied]'}]
            return []
        if kind == 'input_file':
            name = content.get('filename') or content.get('file_id') or 'file'
            return [{'type': 'text',
                     'text': '[Attached file: ' + str(name) + ' - file extraction '
                             'is not supported by this proxy, content was not sent]'}]
        if isinstance(content.get('text'), str):
            return [{'type': 'text', 'text': content['text']}] if content['text'] else []
        if 'content' in content:
            return _content_parts(content['content'])
        return []
    return [{'type': 'text', 'text': str(content)}]


def _message_content(content: Any) -> Any:
    parts = _content_parts(content)
    non_text = [p for p in parts if p.get('type') != 'text']
    if not non_text:
        return ''.join(p.get('text', '') for p in parts)
    return parts


def _extract_response_format(body: Dict[str, Any]) -> Any:
    fmt = body.get('response_format')
    if fmt is None:
        text_opt = body.get('text')
        if isinstance(text_opt, dict):
            fmt = text_opt.get('format')
    if not isinstance(fmt, dict):
        return None
    kind = str(fmt.get('type', ''))
    if not kind.startswith('json'):
        return None
    if kind == 'json_schema' and 'json_schema' not in fmt and 'schema' in fmt:
        spec: Dict[str, Any] = {'schema': fmt['schema']}
        if fmt.get('name') is not None:
            spec['name'] = fmt['name']
        if fmt.get('strict') is not None:
            spec['strict'] = fmt['strict']
        return {'type': 'json_schema', 'json_schema': spec}
    return fmt
"""Split a Responses tools array. Hosted tools become names, function tools pass through."""
def split_tools(tools: Any) -> Tuple[List[Dict[str, Any]], List[str], bool, bool]:
    functions: List[Dict[str, Any]] = []
    hosted: List[str] = []
    wants_search = False
    wants_fetch = False

    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        kind = tool.get('type')
        if kind in _HOSTED_SEARCH_TYPES:
            wants_search = True
            hosted.append('web_search')
            continue
        if kind in _HOSTED_FETCH_TYPES:
            wants_fetch = True
            hosted.append('web_fetch')
            continue
        if kind in (None, 'function'):
            fn = tool.get('function') or tool
            name = fn.get('name')
            if name:
                functions.append({
                    'type': 'function',
                    'function': {
                        'name': name,
                        'description': fn.get('description', '') or '',
                        'parameters': fn.get('parameters') or fn.get('input_schema')
                        or {'type': 'object', 'properties': {}},
                    },
                })
    return functions, hosted, wants_search, wants_fetch


def responses_to_chat_body(body: Dict[str, Any]) -> Dict[str, Any]:
    chat: Dict[str, Any] = {}

    model = body.get('model')
    if model:
        chat['model'] = model

    messages: List[Dict[str, Any]] = []

    instructions = body.get('instructions')
    if isinstance(instructions, str) and instructions.strip():
        messages.append({'role': 'system', 'content': instructions})
    elif isinstance(instructions, list):
        joined = ''.join(_text_of(part) for part in instructions).strip()
        if joined:
            messages.append({'role': 'system', 'content': joined})

    raw_input = body.get('input')

    if isinstance(raw_input, str):
        if raw_input:
            messages.append({'role': 'user', 'content': raw_input})
    elif isinstance(raw_input, list):
        for item in raw_input:
            if isinstance(item, dict) and item.get('role'):
                content = _message_content(item.get('content'))
                if content:
                    messages.append({'role': item.get('role'), 'content': content})
            else:
                content = _message_content(item)
                if content:
                    messages.append({'role': 'user', 'content': content})
    elif raw_input is not None:
        content = _message_content(raw_input)
        if content:
            messages.append({'role': 'user', 'content': content})

    chat['messages'] = messages

    if 'temperature' in body and body['temperature'] is not None:
        chat['temperature'] = body['temperature']
    if 'top_p' in body and body['top_p'] is not None:
        chat['top_p'] = body['top_p']
    max_out = body.get('max_output_tokens') or body.get('max_tokens')
    if max_out:
        chat['max_tokens'] = max_out

    fmt = _extract_response_format(body)
    if fmt is not None:
        chat['response_format'] = fmt

    explicit_engine = (body.get('search_engine') or '').strip().lower()
    if explicit_engine:
        chat['search_engine'] = explicit_engine

    functions, hosted, wants_search, wants_fetch = split_tools(body.get('tools'))
    if functions:
        chat['tools'] = functions
    if body.get('tool_choice') is not None:
        chat['tool_choice'] = body['tool_choice']

    if wants_search:
        chat['web_search'] = True
        chat['_hosted_search'] = True
        if not chat.get('search_engine'):
            chat['search_engine'] = 'duckduckgo'
    if wants_fetch:
        chat['web_fetch'] = True

    reasoning = body.get('reasoning')
    if isinstance(reasoning, dict) and reasoning.get('effort') is not None:
        effort = normalize_reasoning_effort(reasoning.get('effort'))
        if effort is not None:
            chat['reasoning_effort'] = effort

    if body.get('stream'):
        chat['stream'] = True

    return chat


def hosted_tool_names(body: Dict[str, Any]) -> List[str]:
    _, hosted, _, _ = split_tools(body.get('tools'))
    return hosted



