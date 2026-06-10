from typing import Any, Dict

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

from src.core.config_n_logg import config
from src.core.limits import account_limiter
from src.core.accounts import account_manager


def _bearer_token(authorization: str | None) -> str:
    raw = str(authorization or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    if raw.startswith("sk-"):
        return raw
    return raw


def _resolve_auth(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key and x_api_key.strip():
        val = x_api_key.strip()
        return f"Bearer {val}" if not val.lower().startswith("bearer ") else val
    return authorization if authorization and authorization.strip() else None


def _check_auth(authorization: str | None) -> Dict[str, Any]:
    token = _bearer_token(authorization)
    if not token:
        raise HTTPException(
            status_code=401,
            detail={"error": {"message": "Missing API Key", "type": "authentication_error"}},
        )

    account = account_manager.find_by_key(token)
    if not account and token.lower().startswith("sk-ant-"):
        account = account_manager.find_by_key(token[7:])

    if account:
        return account

    if not config.AUTH_TOKEN and (token.startswith("sk-ant-") or token.startswith("sk-")):
        active_accounts = account_manager.list_accounts(include_disabled=False)
        if active_accounts:
            return active_accounts[0]
        return {
            "account_id": "auto-detected",
            "name": "claude-auto-session",
            "auth_key": token,
            "enabled": True,
            "tier": "free",
            "rpm": 100,
            "tpm": 1000000,
            "rpd": 10000,
        }

    raise HTTPException(
        status_code=401,
        detail={"error": {"message": "Unauthorized API Key", "type": "authentication_error"}},
    )


def _count_openai_images(messages: list) -> int:
    image_count = 0
    for msg in messages or []:
        content = msg.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "image_url":
                    image_count += 1
    return image_count


async def _apply_account_limit(account: Dict[str, Any], body: Dict[str, Any], is_opencode: bool = False) -> None:
    if account.get("name") in {"anonymous", "legacy-env-token"}:
        return
    model = body.get("model") or ""
    from src.core.router import router
    
    # Detect if the request is a Claude Code sub-agent request (which gets overridden to gemini-flash-lite)
    is_sub_agent = False
    if not is_opencode:
        system_instruction = body.get("system", "")
        if isinstance(system_instruction, list):
            system_prompt = "\n".join([str(item.get("text", "")) for item in system_instruction if isinstance(item, dict)])
        else:
            system_prompt = str(system_instruction or "")

        if system_prompt:
            system_prompt_lower = system_prompt.lower()
            if "you are an interactive agent" in system_prompt_lower:
                pass
            else:
                sub_agent_keywords = [
                    "general-purpose agent",
                    "general-purpose assistant",
                    "explore agent",
                    "file search specialist",
                    "exploration task",
                    "read-only exploration",
                    "plan agent",
                    "software architect",
                    "implementation plans",
                    "claude-code-guide",
                    "statusline-setup",
                    "specialized agent",
                    "subagent",
                    "sub-agent",
                    "security monitor",
                    "you are the claude-code-guide",
                    "you are the explore",
                    "you are the plan",
                    "you are the general-purpose",
                    "you are the statusline-setup",
                    "file_search_specialist", "file-search-specialist",
                    "code search specialist", "code_search_specialist", "code-search-specialist",
                    "search specialist", "search_specialist", "search-specialist",
                    "research specialist", "research_specialist", "research-specialist",
                    "code review", "code_review", "codereview",
                    "debug assistant", "debug_assistant", "debug-assistant",
                    "planning", "planner",
                    "task agent", "task_agent", "task-agent",
                ]
                is_claude_code = (
                    "you are claude code" in system_prompt_lower 
                    or "cc_version=" in system_prompt_lower 
                    or "claude-code" in system_prompt_lower
                )
                if any(kw in system_prompt_lower for kw in sub_agent_keywords):
                    is_sub_agent = True
                else:
                    import re
                    if re.search(r"you are (a|an|the)[\s\w\-]*sub.?agent", system_prompt_lower):
                        is_sub_agent = True
                    elif "[sub-agent]" in system_prompt_lower:
                        is_sub_agent = True
                    elif is_claude_code and len(body.get("tools", [])) in (19, 20):
                        is_sub_agent = True

        if not is_sub_agent:
            messages = body.get("messages", [])
            for msg in messages:
                if msg.get("role") != "user":
                    continue
                content = msg.get("content")
                if isinstance(content, str) and content.strip().startswith("[SUB-AGENT]"):
                    is_sub_agent = True
                    break
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            if block.get("text", "").strip().startswith("[SUB-AGENT]"):
                                is_sub_agent = True
                                break
                    if is_sub_agent:
                        break

    if is_sub_agent:
        target_model = None
        if account:
            target_model = account.get("subagent_model") or account.get("agent_model") or account.get("sub_agent_model")
        if not target_model:
            import os
            target_model = os.getenv("OPENCODE_SUB_AGENT_MODEL") or os.getenv("SUB_AGENT_MODEL")
        if not target_model:
            target_model = "gemini-flash-lite"
        model_alias = router.resolve_model_alias(target_model)
        pool_type = "lite" if (model_alias and ("lite" in model_alias.lower() or "flash-lite" in model_alias.lower())) else "flash"
    else:
        model_alias = router.resolve_model_alias(model)
        pool_type = "lite" if (model_alias and ("lite" in model_alias.lower() or "flash-lite" in model_alias.lower())) else "flash"

    # Detect if the request goes to a custom endpoint
    from src.core.providers import _custom_endpoint_manager
    is_custom = False
    ep = _custom_endpoint_manager.get_endpoint_for_account(account)
    if ep and ep.get("enabled", True):
        model_to_use = body.get("model", model_alias)
        enabled_models = ep.get("enabled_models", [])
        if model_to_use in enabled_models:
            is_custom = True
    if not is_custom:
        pool_models = router.get_pool_custom_models(model_alias)
        if pool_models:
            is_custom = True
    if is_custom:
        pool_type = "custom"

    from src.core.limits.account_limiter import get_effective_limits_by_pool
    eff_rpm, eff_tpm, eff_rpd = await get_effective_limits_by_pool(account, pool_type)
    
    effective = dict(account)
    effective["rpm"] = eff_rpm
    effective["tpm"] = eff_tpm
    effective["rpd"] = eff_rpd
    
    max_tokens = int(body.get("max_tokens") or body.get("max_completion_tokens") or 4096)
    estimated = account_limiter.estimate_messages_tokens(body.get("messages") or [], max_tokens)
    image_count = _count_openai_images(body.get("messages") or [])
    estimated += image_count * 258
    
    allowed, reason = await account_limiter.acquire(effective, estimated, pool_type)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "message": f"Account rate limit exceeded: {reason}",
                    "type": "rate_limit_error",
                }
            },
        )


def _auth_key_prefix(account: Dict[str, Any]) -> str:
    ak = account.get("auth_key") or ""
    return ak[-8:] if len(ak) >= 8 else ak

