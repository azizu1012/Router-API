import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional
from google.adk import Agent, Event
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types, Client

from src.core.config_n_logg.logger import logger_api
from src.core.providers.search_manager import execute_hybrid_search


class MockGenerateContentResponse:
    """Mock wrapper around ADK response/chunk dicts to look like GenerateContentResponse."""
    def __init__(self, response_dict: dict):
        self._dict = response_dict
        
        # 1. Map candidates
        self.candidates = []
        cands = response_dict.get("candidates") or []
        for cand in cands:
            class MockCandidate:
                def __init__(self, c_dict):
                    self.content = None
                    content_dict = c_dict.get("content")
                    if content_dict:
                        class MockContent:
                            def __init__(self, co_dict):
                                self.role = co_dict.get("role")
                                self.parts = []
                                parts_list = co_dict.get("parts") or []
                                for p in parts_list:
                                    class MockPart:
                                        def __init__(self, p_dict):
                                            self.text = p_dict.get("text")
                                            self.thought = p_dict.get("thought")
                                            self.function_call = p_dict.get("function_call")
                                    self.parts.append(MockPart(p))
                        self.content = MockContent(content_dict)
            self.candidates.append(MockCandidate(cand))

        # 2. Map usage_metadata
        self.usage_metadata = None
        usage = response_dict.get("usageMetadata") or response_dict.get("usage_metadata")
        if usage:
            class UsageMetadata:
                def __init__(self, u_dict):
                    self.prompt_token_count = u_dict.get("promptTokenCount") or u_dict.get("prompt_token_count") or 0
                    self.candidates_token_count = u_dict.get("candidatesTokenCount") or u_dict.get("candidates_token_count") or 0
                    self.total_token_count = u_dict.get("totalTokenCount") or u_dict.get("total_token_count") or 0
            self.usage_metadata = UsageMetadata(usage)

    def model_dump(self, by_alias=True, exclude_none=True):
        return self._dict

    def model_dump_json(self, by_alias=True, exclude_none=True):
        return json.dumps(self._dict)


def get_web_search_tool(auth_key_prefix: str, account: Optional[Dict[str, Any]]):
    """Generates the custom web search tool for ADK."""
    
    async def web_search(query: str) -> str:
        """Search the internet for real-time information.
        
        Args:
            query: The search query.
            
        Returns:
            The search results.
        """
        logger_api.info(f"[ADK Tool] Running web search query: {query}")
        try:
            search_results, hybrid_citations = await execute_hybrid_search(
                [query], auth_key_prefix=auth_key_prefix, account=account
            )
            return search_results or "No search results found."
        except Exception as e:
            logger_api.error(f"[ADK Tool] Search failed: {e}")
            return f"Search failed: {str(e)}"
            
    return web_search


async def run_adk_agent_stream(
    model_id: str,
    api_key: str,
    system_instruction: Optional[str],
    contents: List[Any],
    auth_key_prefix: str,
    account: Optional[Dict[str, Any]],
) -> AsyncIterator[Dict[str, Any]]:
    """Runs the ADK agent session and yields dict response chunks for streaming."""
    
    # 1. Define model with client injection
    model_obj = Gemini(model=model_id)
    client = Client(api_key=api_key)
    model_obj.api_client = client
    model_obj._live_api_client = Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            api_version=model_obj._live_api_version
        )
    )
    
    # 2. Define custom search tool and Agent
    search_tool = get_web_search_tool(auth_key_prefix, account)
    agent = Agent(
        name="grounding_agent",
        model=model_obj,
        instruction=system_instruction or "You are a helpful assistant.",
        tools=[search_tool]
    )
    
    # 3. Setup Session and Runner
    session_service = InMemorySessionService()
    runner = Runner(agent=agent, app_name="router_api", session_service=session_service)
    session = await session_service.create_session(app_name="router_api", user_id="user")
    
    # 4. Populate conversation history
    if len(contents) > 1:
        for c in contents[:-1]:
            role = getattr(c, "role", "user")
            author = "assistant" if role == "model" else "user"
            parts = []
            for p in getattr(c, "parts", []):
                if getattr(p, "text", None):
                    parts.append(types.Part.from_text(text=p.text))
            if parts:
                event = Event(
                    author=author,
                    content=types.Content(role=role, parts=parts),
                    type="message"
                )
                await session_service.append_event(session, event)
                
    # 5. Extract latest user message
    last_msg = contents[-1]
    last_role = getattr(last_msg, "role", "user")
    last_parts = []
    for p in getattr(last_msg, "parts", []):
        if getattr(p, "text", None):
            last_parts.append(types.Part.from_text(text=p.text))
            
    new_message = types.Content(role=last_role, parts=last_parts)
    
    # 6. Execute and yield response chunks
    async for event in runner.run_async(
        user_id="user",
        session_id=session.id,
        new_message=new_message
    ):
        if event.content and event.content.parts:
            parts_list = []
            for p in event.content.parts:
                # Filter out function calls from user-facing stream
                if p.text:
                    parts_list.append({"text": p.text})
                elif getattr(p, "thought", None):
                    parts_list.append({"text": p.text, "thought": True})
            
            if parts_list:
                chunk = {
                    "candidates": [
                        {
                            "index": 0,
                            "content": {
                                "role": "model",
                                "parts": parts_list
                            }
                        }
                    ]
                }
                if event.usage_metadata:
                    chunk["usageMetadata"] = event.usage_metadata.model_dump(
                        by_alias=True, exclude_none=True
                    )
                yield chunk


async def run_adk_agent(
    model_id: str,
    api_key: str,
    system_instruction: Optional[str],
    contents: List[Any],
    auth_key_prefix: str,
    account: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Runs the ADK agent session and returns the final response dict (non-streaming)."""
    final_text = ""
    usage_metadata = None
    
    async for chunk in run_adk_agent_stream(
        model_id=model_id,
        api_key=api_key,
        system_instruction=system_instruction,
        contents=contents,
        auth_key_prefix=auth_key_prefix,
        account=account,
    ):
        candidates = chunk.get("candidates") or []
        if candidates:
            parts = candidates[0].get("content", {}).get("parts") or []
            for p in parts:
                if p.get("text"):
                    final_text += p["text"]
        if chunk.get("usageMetadata"):
            usage_metadata = chunk["usageMetadata"]
            
    # Format the final non-streaming response dict
    response_dict = {
        "candidates": [
            {
                "index": 0,
                "content": {
                    "role": "model",
                    "parts": [{"text": final_text}]
                },
                "finishReason": "STOP"
            }
        ]
    }
    if usage_metadata:
        response_dict["usageMetadata"] = usage_metadata
        
    return response_dict
