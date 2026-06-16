from typing import Any, Dict, List, Optional

def get_system_status_summary(model_alias: str, reason: str = "pool_exhausted") -> str:
    if reason in ("rate_limit", "rate_limit_rpd", "rate_limit_rpm_tpm"):
        msg = (
            "⚠️ **[Tạm thời vượt giới hạn tốc độ / Rate Limit Reached]** ⚠️\n\n"
            "Hệ thống đang bị giới hạn tốc độ (RPM/TPM) tạm thời. Đây **không phải lỗi context** của bạn.\n\n"
            "**Hướng xử lý nhanh:**\n"
            "1. **Đợi 15-30 giây** rồi thử lại — rate limit sẽ tự reset theo phút.\n"
            "2. Nếu vẫn lỗi, chạy `/compact` để giảm token nhằm giảm áp lực rate limit."
        )
    elif reason in ("unavailable", "server_error"):
        msg = (
            "⚠️ **[Dịch vụ tạm thời không khả dụng / Service Unavailable]** ⚠️\n\n"
            "API Gemini đang tạm thời không phản hồi (503/500). Đây là lỗi server tạm thời.\n\n"
            "**Hướng xử lý:**\n"
            "1. **Đợi 30-60 giây** rồi thử lại.\n"
            "2. Hệ thống sẽ tự động thử lại với key/model khác ở request tiếp theo."
        )
    elif reason in ("billing_error", "invalid_key"):
        msg = (
            "⚠️ **[Lỗi xác thực API / Auth Error]** ⚠️\n\n"
            "Một số API key đang gặp vấn đề billing hoặc không hợp lệ. Hệ thống đã tự động loại key đó.\n\n"
            "**Hướng xử lý:**\n"
            "1. Thử lại ngay — hệ thống sẽ dùng key khác.\n"
            "2. Nếu vẫn lỗi liên tục, kiểm tra lại danh sách API key trong dashboard."
        )
    else:
        # Generic pool exhausted — could be rate limit cascade
        msg = (
            "⚠️ **[Hệ thống quá tải tạm thời / System Overloaded]** ⚠️\n\n"
            "Tất cả các model/key trong pool đã được dùng hết hoặc đang bị rate limit. "
            "Đây **không phải lỗi context** của bạn.\n\n"
            "**Hướng xử lý nhanh:**\n"
            "1. **Đợi 15-30 giây** để hệ thống tự động reset rate limit rồi thử lại.\n"
            "2. Nếu context thực sự lớn (>100k tokens), chạy `/compact` để giảm tải."
        )
    return msg


def _classify_error_reason(error_text: str, api_key: Optional[str] = None, model_id: Optional[str] = None) -> str:
    if "403" in error_text and "permission_denied" in error_text:
        return "permission_denied"
    if "quota" in error_text and ("day" in error_text or "daily" in error_text):
        return "rate_limit_rpd"
    if "429" in error_text or "rate_limit" in error_text or "quota" in error_text:
        if api_key and model_id:
            from src.core.limits.gemini_rate_limiter import get_key_rpd_status
            _, _, is_exhausted = get_key_rpd_status(api_key, model_id)
            if is_exhausted:
                return "rate_limit_rpd"
        return "rate_limit"
    if ("401" in error_text and ("unauthorized" in error_text or "invalid" in error_text or "api_key" in error_text or "api key" in error_text)) or "api key not valid" in error_text or "api_key_invalid" in error_text:
        return "invalid_key"
    if "503" in error_text or "unavailable" in error_text or "overloaded" in error_text:
        return "unavailable"
    if "500" in error_text or "internal" in error_text:
        return "server_error"
    if "504" in error_text or "deadline" in error_text or "timeout" in error_text:
        return "timeout"
    return "unknown_error"

def _reinforce_messages_for_retry(messages: List[Dict[str, Any]], attempt: int) -> List[Dict[str, Any]]:
    new_messages = [dict(m) for m in messages]
    
    instruction = ""
    if attempt >= 2:
        instruction = (
            "\n\n[SYSTEM REINFORCEMENT: URGENT / BẮT BUỘC ÉP CONCISE]\n"
            "The system is under extremely high load and rate limited. You MUST make your response extremely concise, direct, and fast.\n"
            "- DO NOT write verbose explanations, code boilerplates, or conversational pleasantries.\n"
            "- For small scope (1-3 files, isolated changes): handle directly — read files yourself, no sub-agents needed.\n"
            "- For large scope (many files with cross-cutting dependencies): use Task tool to spawn parallel sub-agents for exploration, then synthesize results.\n"
            "- If you are searching or writing, return ONLY the direct output. Minimize token consumption!\n"
            "- Trả lời cực kỳ ngắn gọn, trực diện, không giải thích dông dài."
        )
    if attempt >= 5:
        instruction = (
            "\n\n[SYSTEM REINFORCEMENT LEVEL 2: EMERGENCY / KHẨN CẤP TỐI GIẢN]\n"
            "CRITICAL: The system is close to timeout limit. You MUST return a minimalist, bare-minimum response.\n"
            "- Limit your output to the absolute necessary answers. Use bullet points or code snippets directly.\n"
            "- Handle small scope directly (read files yourself). For large cross-cutting changes, use Task tool sparingly — max 1-2 sub-agents.\n"
            "- Do not output more than 100-200 tokens. Be as brief as a terminal command.\n"
            "- Bắt buộc trả về kết quả tối giản nhất có thể. Không dông dài."
        )

    if not instruction:
        return new_messages

    for msg in new_messages:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if "[SYSTEM REINFORCEMENT" not in content:
                msg["content"] = content + instruction
            return new_messages
            
    new_messages.insert(0, {"role": "system", "content": instruction.strip()})
    return new_messages
