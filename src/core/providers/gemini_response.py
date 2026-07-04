"""
Module này chịu trách nhiệm phân tích cú pháp và chuyển đổi phản hồi gốc từ API Gemini
sang định dạng tương thích với OpenAI. Điều này cho phép Router API trình bày một giao diện
phản hồi thống nhất cho các client, bất kể backend LLM được sử dụng.

**Các chuyển đổi chính bao gồm:**
- `content.parts[]` của Gemini được phân tích thành `text`, `reasoning_content` (suy nghĩ) và `functionCall` của OpenAI.
- `usageMetadata` của Gemini được ánh xạ tới `usage` của OpenAI.
- `finishReason` của Gemini được chuyển đổi thành `finish_reason` của OpenAI.

Module này được chia sẻ giữa cả quá trình xử lý streaming và non-streaming,
đảm bảo tính nhất quán trong việc chuyển đổi phản hồi.
Nó bao gồm các hàm để phân tích từng chunk phản hồi streaming và xử lý toàn bộ phản hồi non-streaming,
đồng thời bao gồm các hàm tiện ích để tạo chunk và trích xuất thông tin sử dụng.
"""

import json
import time
from typing import Any, Dict, List, Optional


def parse_gemini_chunk(chunk: Dict[str, Any], state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Phân tích một chunk phản hồi từ Gemini API và chuyển đổi nó thành một danh sách các dictionary
    delta của OpenAI. Hàm này được sử dụng trong quá trình xử lý phản hồi streaming.

    **Các bước xử lý chính:**
    1. Khởi tạo trạng thái ban đầu nếu đây là chunk đầu tiên, bao gồm `message_id`, `model` và `function_index`.
    2. Lặp qua từng `part` trong `content` của Gemini:
       a. Trích xuất văn bản (`text`), nội dung suy nghĩ (`reasoning_content`) và chữ ký suy nghĩ (`thought_signature`).
       b. Xử lý các `functionCall` của Gemini, tạo `tool_call` tương ứng với ID và đối số.
       c. Tạo các chunk delta của OpenAI dựa trên văn bản, suy nghĩ và cuộc gọi công cụ.
    3. Trích xuất thông tin sử dụng (`usageMetadata`) từ phản hồi.
    4. Xử lý `finishReason` của Gemini, chuyển đổi nó thành `finish_reason` của OpenAI
       (đặc biệt là chuyển `stop` thành `tool_calls` nếu có cuộc gọi công cụ).

    Args:
        chunk (Dict[str, Any]): Một dictionary đại diện cho một chunk phản hồi từ Gemini.
        state (Dict[str, Any]): Một dictionary trạng thái để duy trì thông tin qua các chunk liên tiếp.

    Returns:
        List[Dict[str, Any]]: Một danh sách các dictionary delta ở định dạng OpenAI.
    """
    response = chunk.get("response", chunk)
    if not response or not response.get("candidates"):
        return []

    candidate = response["candidates"][0]
    content = candidate.get("content", {})
    results: List[Dict[str, Any]] = []

    if not state.get("message_id"):
        state["message_id"] = response.get("responseId", f"msg_{id(chunk)}")
        state["model"] = response.get("modelVersion", "gemini")
        state["function_index"] = 0
        results.append(_make_chunk(state, {"role": "assistant"}, None))

    for part in content.get("parts", []):
        is_thought = part.get("thought") is True
        text = part.get("text", "")
        ts = part.get("thought_signature") or part.get("thoughtSignature")
        if text:
            delta = {}
            if is_thought:
                delta["reasoning_content"] = text
            else:
                delta["content"] = text
            if ts:
                delta["thought_signature"] = ts
            results.append(_make_chunk(state, delta, None))
        elif ts and not part.get("functionCall"):
            delta = {"thought_signature": ts}
            if is_thought:
                delta["reasoning_content"] = ""
            results.append(_make_chunk(state, delta, None))

        fc = part.get("functionCall")
        if fc:
            tool_call_index = state["function_index"]
            state["function_index"] += 1
            fc_name = fc["name"]
            fc_args = fc.get("args", {})
            tool_call_id = f"{fc_name}-{int(time.time() * 1000)}-{tool_call_index}"
            tool_call = {
                "id": tool_call_id,
                "index": tool_call_index,
                "type": "function",
                "function": {
                    "name": fc_name,
                    "arguments": json.dumps(fc_args),
                },
            }
            delta = {"tool_calls": [tool_call]}
            if ts:
                delta["thought_signature"] = ts
            results.append(_make_chunk(state, delta, None))

    _extract_usage(response, chunk, state)

    finish_reason = candidate.get("finishReason")
    if finish_reason:
        fr_lower = finish_reason.lower()
        if fr_lower == "stop" and state.get("function_index", 0) > 0:
            fr_lower = "tool_calls"
        final = _make_chunk(state, {}, fr_lower)
        if state.get("usage"):
            final["usage"] = state["usage"]
        results.append(final)

    return results


def parse_gemini_nonstream(response: Dict[str, Any], model_alias: str) -> Dict[str, Any]:
    """
    Phân tích một phản hồi không streaming từ Gemini API và chuyển đổi nó thành một dictionary
    phản hồi của OpenAI. Hàm này sử dụng `parse_gemini_chunk` để xử lý nội dung và sau đó
    tổng hợp các phần delta thành một phản hồi hoàn chỉnh.

    **Các bước xử lý chính:**
    1. Sử dụng `parse_gemini_chunk` để phân tích phản hồi Gemini thành một danh sách các chunk delta của OpenAI.
    2. Tập hợp các phần nội dung (`content_parts`), nội dung suy nghĩ (`reasoning_parts`),
       chữ ký suy nghĩ (`thought_sig_parts`) và các cuộc gọi công cụ (`tool_calls`) từ các chunk delta.
    3. Xây dựng đối tượng `message` cuối cùng với vai trò `assistant`, bao gồm nội dung,
       cuộc gọi công cụ và nội dung suy nghĩ.
    4. Xác định `finish_reason` và thông tin `usage` cho phản hồi cuối cùng.
    5. Ước tính `completion_tokens` nếu chúng không được cung cấp trực tiếp trong `usage`.

    Args:
        response (Dict[str, Any]): Một dictionary đại diện cho phản hồi không streaming từ Gemini.
        model_alias (str): Bí danh của mô hình đã được sử dụng.

    Returns:
        Dict[str, Any]: Một dictionary phản hồi hoàn chỉnh ở định dạng OpenAI.
    """
    state: Dict[str, Any] = {}
    chunks = parse_gemini_chunk(response, state)

    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    thought_sig_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

    for chunk in chunks:
        delta = chunk.get("choices", [{}])[0].get("delta", {})
        if delta.get("content"):
            content_parts.append(delta["content"])
        if delta.get("reasoning_content"):
            reasoning_parts.append(delta["reasoning_content"])
        if delta.get("thought_signature"):
            thought_sig_parts.append(delta["thought_signature"])
        if delta.get("tool_calls"):
            tool_calls.extend(delta["tool_calls"])

    message: Dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if thought_sig_parts:
        message["thought_signature"] = "".join(thought_sig_parts)

    finish_reason = "stop"
    for chunk in chunks:
        fr = chunk.get("choices", [{}])[0].get("finish_reason")
        if fr:
            finish_reason = fr
            break

    usage = state.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})

    full_content = "".join(content_parts)
    if usage.get("completion_tokens", 0) == 0 and full_content:
        estimated = max(1, len(full_content) // 4)
        usage = {
            **usage,
            "completion_tokens": estimated,
            "total_tokens": usage.get("prompt_tokens", 0) + estimated,
        }

    return {
        "id": f"chatcmpl-{id(response)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_alias,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage,
    }


def _make_chunk(state: Dict[str, Any], delta: Dict[str, Any], finish_reason: Optional[str]) -> Dict[str, Any]:
    """
    Tạo một chunk phản hồi ở định dạng OpenAI (cho streaming).

    Args:
        state (Dict[str, Any]): Dictionary trạng thái chứa `message_id` và `model`.
        delta (Dict[str, Any]): Dictionary chứa các thay đổi (`content`, `reasoning_content`, `tool_calls`).
        finish_reason (Optional[str]): Lý do kết thúc của chunk (ví dụ: "stop", "tool_calls").

    Returns:
        Dict[str, Any]: Một dictionary đại diện cho một chunk phản hồi của OpenAI.
    """
    return {
        "id": f"chatcmpl-{state.get('message_id', 'unknown')}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": state.get("model", "gemini"),
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def _extract_usage(response: Dict[str, Any], chunk: Dict[str, Any], state: Dict[str, Any]) -> None:
    """
    Trích xuất thông tin sử dụng token từ phản hồi Gemini và cập nhật trạng thái.
    Hàm này cố gắng tính toán `prompt_tokens`, `completion_tokens` và `total_tokens`
    dựa trên `usageMetadata` của Gemini.

    Args:
        response (Dict[str, Any]): Phản hồi Gemini hoàn chỉnh (cho non-streaming).
        chunk (Dict[str, Any]): Một chunk phản hồi Gemini (cho streaming).
        state (Dict[str, Any]): Dictionary trạng thái để lưu trữ thông tin sử dụng đã trích xuất.
    """
    usage_meta = response.get("usageMetadata") or chunk.get("usageMetadata")
    if not usage_meta:
        return
    cached = usage_meta.get("cachedContentTokenCount", 0) or 0
    prompt_tokens = usage_meta.get("promptTokenCount", 0) or 0
    thoughts_tokens = usage_meta.get("thoughtsTokenCount", 0) or 0
    candidates_tokens = usage_meta.get("candidatesTokenCount", 0) or 0
    total_tokens = usage_meta.get("totalTokenCount", 0) or 0

    if total_tokens > 0 and prompt_tokens > 0:
        actual_completion = total_tokens - prompt_tokens
    else:
        actual_completion = candidates_tokens + thoughts_tokens

    if actual_completion > 0:
        completion_tokens = actual_completion
    else:
        completion_tokens = candidates_tokens + thoughts_tokens

    usage: Dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens if total_tokens > 0 else prompt_tokens + completion_tokens,
    }
    if cached > 0:
        usage["prompt_tokens_details"] = {"cached_tokens": cached}
    if thoughts_tokens > 0:
        usage["completion_tokens_details"] = {"reasoning_tokens": thoughts_tokens}
    state["usage"] = usage
