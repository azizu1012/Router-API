"""Suggested model alias templates for the self-service dashboard."""

from typing import Any, Dict, List

from .router import router
from .alias_models import AliasTemplateResponse

@router.get("/alias-templates", response_model=List[AliasTemplateResponse])
async def get_alias_templates():
    """Get suggested alias templates with latest Claude and GPT models.

    Includes latest official models (Claude 3.7 Sonnet, GPT-4.5 Preview, o3-mini, o1)
    as well as next-gen / relay models (Sonnet 5.5, Opus 5.5, Haiku 5.5).
    """
    return [
        # --- CLAUDE (Anthropic) ---
        {
            "name": "Claude 3.7 Sonnet (Hybrid Reasoning)",
            "alias_name": "claude-3-7-sonnet-20250219",
            "target_model": "gemini-flash",
            "target_endpoint": None,
            "description": "Flash = Sonnet 3.7: Model mới nhất của Anthropic, chạy Gemini Flash",
            "category": "Claude",
        },
        {
            "name": "Claude-style Gemini Flash (Sonnet 3.5)",
            "alias_name": "claude-3-5-sonnet-20241022",
            "target_model": "gemini-flash",
            "target_endpoint": None,
            "description": "Flash = Sonnet: Model coding chuẩn của Anthropic, chạy Gemini Flash",
            "category": "Claude",
        },
        {
            "name": "Claude Sonnet 5.5 (Next-Gen Relay)",
            "alias_name": "claude-sonnet-5-5",
            "target_model": "gemini-flash",
            "target_endpoint": None,
            "description": "Sonnet 5.5: Tên model đời cao trên các relay/proxy, chạy Gemini Flash",
            "category": "Claude",
        },
        {
            "name": "Claude Sonnet 5 (Next-Gen)",
            "alias_name": "claude-sonnet-5",
            "target_model": "gemini-flash",
            "target_endpoint": None,
            "description": "Sonnet 5: Chạy Gemini Flash cho client yêu cầu sonnet-5",
            "category": "Claude",
        },
        {
            "name": "Claude-style Gemini Lite (Haiku 3.5)",
            "alias_name": "claude-3-5-haiku-20241022",
            "target_model": "gemini-flash-lite",
            "target_endpoint": None,
            "description": "Lite = Haiku: Tốc độ cao, sub-agent rẻ, chạy Gemini Flash Lite",
            "category": "Claude",
        },
        {
            "name": "Claude Haiku 5.5 (Next-Gen Fast)",
            "alias_name": "claude-haiku-5-5",
            "target_model": "gemini-flash-lite",
            "target_endpoint": None,
            "description": "Haiku 5.5: Model tốc độ cao đời mới, chạy Gemini Flash Lite",
            "category": "Claude",
        },
        {
            "name": "Claude Opus 5.5 (Deep Reasoning)",
            "alias_name": "claude-opus-5-5",
            "target_model": "gemini-pro",
            "target_endpoint": None,
            "description": "Opus 5.5: Dành cho client đòi model Opus đời cao, chạy Gemini Pro",
            "category": "Claude",
        },
        {
            "name": "Claude 3 Opus (Classic Flagship)",
            "alias_name": "claude-3-opus-20240229",
            "target_model": "gemini-pro",
            "target_endpoint": None,
            "description": "Claude 3 Opus chính thức, chạy Gemini Pro",
            "category": "Claude",
        },

        # --- OPENAI (GPT & Reasoning) ---
        {
            "name": "GPT-4.5 Preview (Orion Flagship)",
            "alias_name": "gpt-4.5-preview",
            "target_model": "gemini-pro",
            "target_endpoint": None,
            "description": "GPT-4.5 Orion: Model lớn nhất mới nhất của OpenAI, chạy Gemini Pro",
            "category": "OpenAI",
        },
        {
            "name": "OpenAI o3-mini (Reasoning Coder)",
            "alias_name": "o3-mini",
            "target_model": "gemini-flash",
            "target_endpoint": None,
            "description": "o3-mini: Model suy luận lập trình mới của OpenAI, chạy Gemini Flash",
            "category": "OpenAI",
        },
        {
            "name": "OpenAI o1 (Full Reasoning)",
            "alias_name": "o1",
            "target_model": "gemini-pro",
            "target_endpoint": None,
            "description": "o1: Model suy luận sâu của OpenAI, chạy Gemini Pro",
            "category": "OpenAI",
        },
        {
            "name": "GPT-4o (Omni Flagship)",
            "alias_name": "gpt-4o",
            "target_model": "gemini-flash",
            "target_endpoint": None,
            "description": "gpt-4o: Model chuẩn của OpenAI SDK, chạy Gemini Flash",
            "category": "OpenAI",
        },
        {
            "name": "GPT-4o Mini (Siêu nhẹ)",
            "alias_name": "gpt-4o-mini",
            "target_model": "gemini-flash-lite",
            "target_endpoint": None,
            "description": "gpt-4o-mini: Tác vụ phụ & sub-agent nhanh, chạy Gemini Flash Lite",
            "category": "OpenAI",
        },
        {
            "name": "GPT-5 (Speculative Next-Gen)",
            "alias_name": "gpt-5",
            "target_model": "gemini-pro",
            "target_endpoint": None,
            "description": "Bypass các client/extension đòi hỏi tên model GPT-5",
            "category": "OpenAI",
        },

        # --- CUSTOM ENDPOINTS ---
        {
            "name": "Custom Endpoint: GPT-4o → Claude Sonnet",
            "alias_name": "gpt-4o",
            "target_model": "claude-sonnet-5-5",
            "target_endpoint": "my-endpoint",
            "description": "OpenAI client gọi gpt-4o nhưng chạy Claude trên Custom Endpoint",
            "category": "Custom Endpoint",
        },
        {
            "name": "Custom Endpoint: Claude 3.7 → Custom DeepSeek",
            "alias_name": "claude-3-7-sonnet-20250219",
            "target_model": "deepseek-v4.1-flash",
            "target_endpoint": "my-endpoint",
            "description": "Claude Code gọi Sonnet 3.7 nhưng chạy DeepSeek trên Custom Endpoint",
            "category": "Custom Endpoint",
        },
    ]



