"""
Module này chịu trách nhiệm chuyển đổi các định dạng tin nhắn và công cụ từ chuẩn OpenAI
(hoặc tương thích) sang định dạng gốc của Google Gemini. Nó đảm bảo rằng các yêu cầu
đến Router API có thể được dịch chính xác để tương tác với các mô hình Gemini.

**Các chuyển đổi chính bao gồm:**
- `messages` của OpenAI được ánh xạ tới `contents[]` của Gemini, với các vai trò `user` và `model`.
- `system` prompt của OpenAI được ánh xạ tới `systemInstruction` của Gemini.
- `tools` của OpenAI được chuyển đổi thành `functionDeclarations` của Gemini.
- Các tham số cấu hình tạo sinh như `temperature`, `maxOutputTokens`, và `thinkingConfig`
  cũng được xử lý.
- `safetySettings` được đặt mặc định thành `OFF` cho tất cả các danh mục để linh hoạt hơn trong việc kiểm soát nội dung.

Module này cũng bao gồm các hàm tiện ích để làm sạch tên hàm, trích xuất nội dung văn bản,
chuyển đổi nội dung sang các phần của Gemini và đảm bảo định dạng tin nhắn cuối cùng phù hợp.
"""

import json
import re
from typing import Any, Dict, List, Optional

from src.logical_HQ_translator.message_converter import _sanitize_schema_for_gemini

# Cấu hình cài đặt an toàn mặc định cho các yêu cầu Gemini.
# Tất cả các danh mục an toàn được đặt thành "OFF" để cung cấp sự kiểm soát linh hoạt hơn
# cho người dùng cuối hoặc các lớp xử lý an toàn khác ở cấp độ cao hơn.
DEFAULT_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
]


def sanitize_function_name(name: str) -> str:
    """
    Làm sạch tên hàm để tuân thủ các quy tắc đặt tên của Gemini API.
    Tên hàm phải bắt đầu bằng chữ cái hoặc dấu gạch dưới, và chỉ có thể chứa
    các ký tự chữ cái, số, dấu gạch dưới, dấu chấm, dấu hai chấm, dấu gạch ngang.
    Chiều dài tối đa là 64 ký tự.

    Args:
        name (str): Tên hàm gốc.

    Returns:
        str: Tên hàm đã được làm sạch và tuân thủ định dạng của Gemini.
    """
    if not name:
        return "_unknown"
    sanitized = re.sub(r'[^a-zA-Z0-9_.:\-]', '_', name)
    if not re.match(r'^[a-zA-Z_]', sanitized):
        sanitized = '_' + sanitized
    return sanitized[:64]


def convert_messages_to_contents(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Chuyển đổi danh sách tin nhắn từ định dạng OpenAI sang định dạng `contents` gốc của Gemini.
    Hàm này xử lý các vai trò `system`, `user`, `assistant` và `tool`,
    chuyển đổi chúng thành các `parts` tương ứng trong cấu trúc `contents` của Gemini.

    **Các chuyển đổi bao gồm:**
    - Tin nhắn `system` được chuyển thành `systemInstruction` riêng biệt.
    - Tin nhắn `user` và `assistant` được ánh xạ tới các `role` tương ứng với các `parts` chứa văn bản, suy nghĩ hoặc cuộc gọi hàm.
    - Tin nhắn `tool` được chuyển thành `functionResponse` với tên hàm và phản hồi.
    - Xử lý các `tool_calls` và `reasoning_content` (suy nghĩ) từ phản hồi của assistant.
    - Làm sạch tên hàm bằng `sanitize_function_name`.

    Args:
        messages (List[Dict[str, Any]]): Danh sách các tin nhắn ở định dạng OpenAI.

    Returns:
        Dict[str, Any]: Một dictionary chứa `contents` đã được chuyển đổi và `systemInstruction` (nếu có).
    """
    contents: List[Dict[str, Any]] = []
    system_instruction = None

    tc_id_to_name: Dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if tc.get("type") == "function" and tc.get("id") and tc.get("function", {}).get("name"):
                    tc_id_to_name[tc["id"]] = tc["function"]["name"]

    tool_responses: Dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "tool" and msg.get("tool_call_id"):
            tool_responses[msg["tool_call_id"]] = msg.get("content", "")

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "system":
            text = content if isinstance(content, str) else _extract_text_content(content)
            if text:
                system_instruction = {"role": "user", "parts": [{"text": text}]}

        elif role == "user":
            parts = _convert_content_to_parts(content)
            if parts:
                contents.append({"role": "user", "parts": parts})

        elif role == "assistant":
            parts = []

            reasoning = msg.get("reasoning_content")
            tsig = msg.get("thought_signature")
            if reasoning:
                p_dict: Dict[str, Any] = {"thought": True, "text": reasoning}
                if tsig:
                    import base64
                    try:
                        p_dict["thought_signature"] = base64.b64decode(tsig)
                    except Exception:
                        p_dict["thought_signature"] = tsig if isinstance(tsig, bytes) else tsig.encode("utf-8")
                parts.append(p_dict)

            if content:
                text = content if isinstance(content, str) else _extract_text_content(content)
                if text:
                    parts.append({"text": text})

            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    if tc.get("type") != "function":
                        continue
                    try:
                        args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                    except (json.JSONDecodeError, ValueError):
                        args = {}
                    p_dict: Dict[str, Any] = {
                        "functionCall": {
                            "name": sanitize_function_name(tc["function"]["name"]),
                            "args": args,
                        }
                    }
                    if tsig:
                        import base64
                        try:
                            p_dict["thought_signature"] = base64.b64decode(tsig)
                        except Exception:
                            p_dict["thought_signature"] = tsig if isinstance(tsig, bytes) else tsig.encode("utf-8")
                    parts.append(p_dict)

            if parts:
                contents.append({"role": "model", "parts": parts})

        elif role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            name = (
                tc_id_to_name.get(tool_call_id)
                or _lookup_global_tool_name(tool_call_id)
                or (tool_call_id.split("-")[0] if "-" in tool_call_id else tool_call_id)
            )
            resp = content
            parsed_resp = _try_parse_json(resp)
            if parsed_resp is None:
                parsed_resp = {"result": resp}
            elif not isinstance(parsed_resp, dict):
                parsed_resp = {"result": parsed_resp}

            _ensure_user_part(contents).append({
                "functionResponse": {
                    "id": tool_call_id,
                    "name": sanitize_function_name(name),
                    "response": {"result": parsed_resp},
                }
            })

    result: Dict[str, Any] = {"contents": contents}
    if system_instruction:
        result["systemInstruction"] = system_instruction
    return result


def build_gemini_body(
    model_id: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: Optional[float] = None,
    max_output_tokens: Optional[int] = None,
    thinking_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build full Gemini request body from OpenAI-format inputs."""
    converted = convert_messages_to_contents(messages)

    body = {
        "model": model_id,
        **converted,
        "generationConfig": {},
        "safetySettings": DEFAULT_SAFETY_SETTINGS,
    }

    if temperature is not None:
        body["generationConfig"]["temperature"] = temperature
    if max_output_tokens is not None:
        body["generationConfig"]["maxOutputTokens"] = max_output_tokens
    if thinking_config:
        body["generationConfig"]["thinkingConfig"] = thinking_config

    if tools:
        function_declarations = []
        for tool in tools:
            fn = tool.get("function", {})
            if fn.get("name") and fn.get("parameters"):
                cleaned = _sanitize_schema_for_gemini(fn["parameters"])
                function_declarations.append({
                    "name": sanitize_function_name(fn["name"]),
                    "description": fn.get("description", ""),
                    "parameters": cleaned,
                })
            elif tool.get("name") and tool.get("input_schema"):
                cleaned = _sanitize_schema_for_gemini(tool["input_schema"])
                function_declarations.append({
                    "name": sanitize_function_name(tool["name"]),
                    "description": tool.get("description", ""),
                    "parameters": cleaned,
                })
        if function_declarations:
            body["tools"] = [{"functionDeclarations": function_declarations}]

    return body


def _lookup_global_tool_name(tool_call_id: str) -> str:
    """Look up tool name from global cache (cross-request fallback)."""
    try:
        from src.logical_HQ_translator.message_converter import _GLOBAL_TOOL_NAME_CACHE
        return _GLOBAL_TOOL_NAME_CACHE.get(tool_call_id)
    except Exception:
        return ""


def _extract_text_content(content: Any) -> str:
    """
    Trích xuất nội dung văn bản thuần túy từ một đối tượng `content` có thể là chuỗi hoặc danh sách các phần.
    Hàm này xử lý các định dạng nội dung khác nhau để đảm bảo chỉ có văn bản được trả về.

    Args:
        content (Any): Nội dung cần trích xuất, có thể là `str` hoặc `List[Dict[str, Any]]`.

    Returns:
        str: Nội dung văn bản thuần túy đã được trích xuất.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    texts.append(item.get("text", ""))
            elif isinstance(item, str):
                texts.append(item)
        return "\n".join(texts)
    return str(content) if content else ""


def _convert_content_to_parts(content: Any) -> List[Dict[str, Any]]:
    """
    Chuyển đổi nội dung từ định dạng OpenAI sang danh sách các `parts` của Gemini.
    Hàm này hỗ trợ chuyển đổi các loại nội dung văn bản và hình ảnh (dưới dạng `image_url` hoặc `inlineData`).

    Args:
        content (Any): Nội dung cần chuyển đổi, có thể là `str` hoặc `List[Dict[str, Any]]`.

    Returns:
        List[Dict[str, Any]]: Danh sách các phần nội dung đã được chuyển đổi theo định dạng Gemini.
    """
    parts = []
    if isinstance(content, str):
        parts.append({"text": content})
    elif isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append({"text": item.get("text", "")})
            elif item.get("type") == "image_url":
                url = item.get("image_url", {}).get("url", "")
                if url.startswith("data:"):
                    try:
                        mime, b64 = url[5:].split(";base64,", 1)
                        parts.append({"inlineData": {"mimeType": mime, "data": b64}})
                    except (ValueError, IndexError):
                        pass
    return parts


def _try_parse_json(s: Any) -> Any:
    """
    Cố gắng phân tích một chuỗi thành đối tượng JSON. Nếu đầu vào không phải là chuỗi
    hoặc không thể phân tích cú pháp JSON, nó sẽ trả về đầu vào gốc hoặc None.

    Args:
        s (Any): Chuỗi hoặc đối tượng cần phân tích cú pháp.

    Returns:
        Any: Đối tượng JSON đã phân tích cú pháp, đầu vào gốc nếu không phải chuỗi,
             hoặc None nếu phân tích cú pháp thất bại.
    """
    if not isinstance(s, str):
        return s
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None


def _ensure_user_part(contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Đảm bảo rằng phần nội dung cuối cùng trong danh sách `contents` có vai trò `user`.
    Nếu phần cuối cùng đã là `user`, nó sẽ trả về các `parts` của phần đó.
    Nếu không, nó sẽ tạo một phần `user` mới và thêm vào danh sách `contents`.
    Điều này thường cần thiết khi thêm `functionResponse` vào cuối cuộc hội thoại,
    vì `functionResponse` phải nằm trong một phần `user`.

    Args:
        contents (List[Dict[str, Any]]): Danh sách các phần nội dung của Gemini.

    Returns:
        List[Dict[str, Any]]: Danh sách các `parts` của phần `user` cuối cùng trong `contents`.
    """
    if contents and contents[-1].get("role") == "user":
        return contents[-1]["parts"]
    new_parts: List[Dict[str, Any]] = []
    contents.append({"role": "user", "parts": new_parts})
    return new_parts
