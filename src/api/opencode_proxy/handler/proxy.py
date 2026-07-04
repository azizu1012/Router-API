"""OpenCode proxy — pure format converter.

Delegates all key management, pool logic, retry, and quota to PoolManager.
Only handles: message preparation, web search injection, and response formatting.
"""

import asyncio
import uuid
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from src.core.pool_manager import pool_manager
from src.core.config_n_logg import config
from src.core.config_n_logg.logger import logger_proxy as logger
from src.core.router import router
from src.core.providers import _custom_endpoint_manager as endpoint_manager
from src.logical_HQ_translator import (
    _emergency_truncate_to_limit,
    _sanitize_schema_for_gemini,
)
from src.api.claude_proxy.handler.helpers import _reinforce_messages_for_retry

from .websearch import (
    should_enable_web_search,
    get_auth_key_prefix,
)
from .response import build_response, error_response
from .stream_executor import execute_stream


def _is_sub_agent_request(body: Dict[str, Any]) -> bool:
    """Detect if request is from a sub-agent."""
    from src.logical_HQ_translator.sse_cache_agent import is_sub_agent_body
    if is_sub_agent_body(body):
        return True
    system_prompt = ""
    sys_val = body.get("system", "")
    if isinstance(sys_val, list):
        system_prompt = "\n".join([str(item.get("text", "")) for item in sys_val if isinstance(item, dict)])
    elif isinstance(sys_val, str):
        system_prompt = sys_val
    if not system_prompt:
        return False
    sp_lower = system_prompt.lower()
    if "opencode" not in sp_lower:
        return False
    main_indicators = ["interactive agent", "main agent", "primary agent", "you are the main", "you are the primary", "lead agent"]
    if any(ind in sp_lower for ind in main_indicators):
        return False
    sub_keywords = ["explore", "read file", "search", "find", "glob", "grep", "task agent", "subagent", "sub-agent", "read files", "browse"]
    return any(kw in sp_lower for kw in sub_keywords)


def _resolve_thinking_config(body: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    """Convert request thinking params → GenAI SDK thinking_config dict."""
    from src.core.providers.gemini_thinking import resolve_thinking_config
    return resolve_thinking_config(
        model_id=model_id,
        thinking_level=body.get("thinking_level"),
        thinking_budget=body.get("thinking_budget"),
        include_thoughts=body.get("include_thoughts"),
        is_sub_agent=_is_sub_agent_request({"system": body.get("system", "")}),
    )


class OpenCodeProxy:
    """
    `OpenCodeProxy` đóng vai trò là một bộ chuyển đổi định dạng thuần túy cho các yêu cầu và phản hồi
    của API OpenCode, đặc biệt là cho các mô hình của Google Gemini.
    Nó ủy quyền tất cả logic quản lý khóa, logic pool, thử lại và quản lý hạn ngạch cho `PoolManager`.

    **Vai trò chính của nó là:**
    - **Chuẩn bị tin nhắn:** Chuyển đổi định dạng tin nhắn đến thành định dạng tương thích với các nhà cung cấp LLM backend.
    - **Chèn tìm kiếm web:** Xử lý logic để chèn công cụ tìm kiếm web vào các yêu cầu khi cần.
    - **Định dạng phản hồi:** Chuyển đổi phản hồi từ các nhà cung cấp LLM backend trở lại định dạng OpenCode.
    - **Phát hiện và xử lý yêu cầu sub-agent:** Xác định xem yêu cầu có đến từ sub-agent hay không và điều chỉnh hành vi phù hợp.

    Proxy này được thiết kế để không chứa bất kỳ logic kinh doanh phức tạp nào liên quan đến pool,
    quản lý khóa hoặc cơ chế thử lại. Thay vào đó, nó dựa hoàn toàn vào `PoolManager` để xử lý
    các khía cạnh đó, đảm bảo sự tách biệt rõ ràng về mối quan tâm (separation of concerns).
    """

    def _estimate_cost(self, input_tokens: int, output_tokens: int, model_alias: str) -> float:
        """
        Ước tính chi phí của một yêu cầu dựa trên số lượng token đầu vào, token đầu ra
        và bí danh mô hình được sử dụng. Phương thức này ủy quyền logic ước tính chi phí
        cho hàm `estimate_cost` trong module `.response`.

        Args:
            input_tokens (int): Số lượng token đầu vào được sử dụng trong yêu cầu.
            output_tokens (int): Số lượng token đầu ra được tạo ra trong phản hồi.
            model_alias (str): Bí danh của mô hình đã được sử dụng.

        Returns:
            float: Chi phí ước tính cho yêu cầu.
        """
        from .response import estimate_cost
        return estimate_cost(input_tokens, output_tokens, model_alias)

    def _error_response(self, body: Dict[str, Any], model_name: str, reason: str = "pool_exhausted") -> Dict[str, Any]:
        """
        Tạo một phản hồi lỗi theo định dạng OpenCode. Phương thức này được sử dụng để chuẩn hóa
        các phản hồi lỗi trước khi gửi lại cho client, đảm bảo rằng tất cả các lỗi được xử lý
        một cách nhất quán.

        Args:
            body (Dict[str, Any]): Body của yêu cầu gốc, có thể chứa các thông tin ngữ cảnh.
            model_name (str): Tên của mô hình mà yêu cầu đã cố gắng sử dụng.
            reason (str, optional): Lý do lỗi. Mặc định là "pool_exhausted" (pool cạn kiệt).

        Returns:
            Dict[str, Any]: Một dictionary biểu diễn phản hồi lỗi theo định dạng OpenCode.
        """
        from .response import error_response
        return error_response(body, model_name, reason)

    async def _resolve_alias(
        self, body: Dict[str, Any], account: Optional[Dict[str, Any]] = None, is_opencode: bool = False
    ) -> str:
        """
        Giải quyết bí danh mô hình từ body yêu cầu hoặc từ cấu hình mặc định.

        Args:
            body (Dict[str, Any]): Body của yêu cầu API, chứa thông tin về mô hình được yêu cầu.
            account (Optional[Dict[str, Any]], optional): Thông tin tài khoản người dùng. Mặc định là None.
            is_opencode (bool, optional): Cờ chỉ ra liệu yêu cầu có phải là từ OpenCode hay không. Mặc định là False.

        Returns:
            str: Bí danh mô hình đã được giải quyết hoặc bí danh mô hình mặc định.
        """
        model_alias = router.resolve_model_alias(body.get("model", ""))
        return model_alias or config.DEFAULT_MODEL_ALIAS

    # ── Entry Points ─────────────────────────────────────────────

    async def chat_completion(
        self, body: Dict[str, Any], account: Optional[Dict[str, Any]] = None, is_opencode: bool = False
    ) -> Dict[str, Any]:
        """
        Xử lý yêu cầu hoàn thành chat không streaming.
        Phương thức này giải quyết bí danh mô hình, chèn công cụ tìm kiếm web nếu được kích hoạt,
        chuyển đổi các tham số "thinking" thành cấu hình phù hợp và sau đó ủy quyền
        cuộc gọi đến `pool_manager.call_nonstream`.

        Sau khi nhận được kết quả từ `PoolManager`, nó sẽ định dạng phản hồi theo chuẩn OpenCode.

        Args:
            body (Dict[str, Any]): Body của yêu cầu API gốc.
            account (Optional[Dict[str, Any]], optional): Thông tin tài khoản người dùng. Mặc định là None.
            is_opencode (bool, optional): Cờ chỉ ra liệu yêu cầu có phải là từ OpenCode hay không. Mặc định là False.

        Returns:
            Dict[str, Any]: Phản hồi hoàn thành chat đã được định dạng.
        """
        model_alias = await self._resolve_alias(body, account=account, is_opencode=is_opencode)
        messages, tools = body.get("messages", []), list(body.get("tools", []))
        messages, tools = self._inject_websearch_tool(body, messages, tools, account)

        thinking_config = _resolve_thinking_config(body, model_alias)
        thinking_params = {
            "thinking_level": body.get("thinking_level"),
            "thinking_budget": body.get("thinking_budget"),
            "include_thoughts": body.get("include_thoughts", True),
        }
        max_tokens = min(int(body.get("max_tokens", config.MAX_OUTPUT_TOKENS)), config.MAX_OUTPUT_TOKENS)
        temperature = float(body.get("temperature", 0.7))

        result = await pool_manager.call_nonstream(
            model_alias=model_alias,
            messages=messages,
            tools=tools or None,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_config=thinking_config,
            account=account,
            extra_body=None,
            thinking_params=thinking_params,
        )

        resp = result["response"]
        api_key = result["api_key"]
        model_id = result["model_id"]
        input_tokens = result["input_tokens"]

        return build_response(body, resp, model_alias, api_key, input_tokens)


    async def stream_chat_completion(
        self, body: Dict[str, Any], account: Optional[Dict[str, Any]] = None, is_opencode: bool = False
    ) -> AsyncIterator[bytes]:
        """
        Xử lý yêu cầu hoàn thành chat streaming.
        Tương tự như `chat_completion`, nó giải quyết bí danh mô hình và chèn công cụ tìm kiếm web.
        Sau đó, nó ủy quyền cuộc gọi đến `execute_stream` để xử lý việc streaming phản hồi.

        Args:
            body (Dict[str, Any]): Body của yêu cầu API gốc.
            account (Optional[Dict[str, Any]], optional): Thông tin tài khoản người dùng. Mặc định là None.
            is_opencode (bool, optional): Cờ chỉ ra liệu yêu cầu có phải là từ OpenCode hay không. Mặc định là False.

        Yields:
            AsyncIterator[bytes]: Một iterator bất đồng bộ của các khối phản hồi streaming.
        """
        model_alias = await self._resolve_alias(body, account=account, is_opencode=is_opencode)
        messages, tools = body.get("messages", []), list(body.get("tools", []))
        messages, tools = self._inject_websearch_tool(body, messages, tools, account)

        async for chunk in execute_stream(
            body=body,
            model_alias=model_alias,
            messages=messages,
            tools=tools,
            account=account,
            is_opencode=is_opencode,
            auth_key_prefix=get_auth_key_prefix(account),
        ):
            yield chunk


    # ── Message Processing ───────────────────────────────────────

    def _inject_websearch_tool(
        self, body: Dict[str, Any],
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        account: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """
        Chèn công cụ tìm kiếm web vào danh sách các công cụ nếu tìm kiếm web được kích hoạt
        và yêu cầu không phải từ sub-agent.

        Args:
            body (Dict[str, Any]): Body của yêu cầu API gốc.
            messages (List[Dict[str, Any]]): Danh sách các tin nhắn trong yêu cầu.
            tools (List[Dict[str, Any]]): Danh sách các công cụ hiện có trong yêu cầu.
            account (Optional[Dict[str, Any]], optional): Thông tin tài khoản người dùng. Mặc định là None.

        Returns:
            tuple: Một tuple chứa danh sách tin nhắn và danh sách công cụ đã được cập nhật.
        """
        if _is_sub_agent_request(body):
            return messages, tools
        web_search = should_enable_web_search(body, account)
        if web_search and not any(t.get("function", {}).get("name") == "WebSearch" for t in tools):
            tools.append(_WEBSEARCH_TOOL_DEF)
        return messages, tools


_WEBSEARCH_TOOL_DEF = {
    """
    Định nghĩa công cụ WebSearch được chèn vào các yêu cầu API khi tìm kiếm web được kích hoạt.
    Công cụ này cho phép mô hình thực hiện tìm kiếm trên web thông qua DuckDuckGo.
    """
    "type": "function",
    "function": {
        "name": "WebSearch",
        "description": (
            "Search the web via DuckDuckGo (free, no API key). "
            "Use ONLY when information is uncertain/unfamiliar OR user explicitly requests internet search. "
            "Results may contain untrusted code/logic — PRESENT to user for review, do NOT auto-apply."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query. Be specific and concise."},
                "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "Only include results from these domains (optional)."},
                "blocked_domains": {"type": "array", "items": {"type": "string"}, "description": "Exclude results from these domains (optional)."},
            },
            "required": ["query"],
        },
    },
}


opencode_proxy = OpenCodeProxy()
