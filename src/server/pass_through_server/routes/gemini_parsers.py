import base64
from typing import List
from fastapi import Request
from src.core.providers.genai_types import types as gt


def resolve_gemini_auth(
    request: Request,
    authorization: str | None = None,
    x_api_key: str | None = None,
    x_goog_api_key: str | None = None,
) -> str | None:
    key_param = request.query_params.get("key")
    if key_param:
        return f"Bearer {key_param}"
    if x_goog_api_key and x_goog_api_key.strip():
        return f"Bearer {x_goog_api_key.strip()}"
    if x_api_key and x_api_key.strip():
        return f"Bearer {x_api_key.strip()}"
    if authorization and authorization.strip():
        return authorization
    return None


def parse_gemini_contents(raw_contents: list) -> List[gt.Content]:
    contents = []
    for c in raw_contents or []:
        if not isinstance(c, dict):
            continue
        role = c.get("role")
        parts = []
        for p in c.get("parts") or []:
            if not isinstance(p, dict):
                continue
            thought = p.get("thought")
            thought_sig = p.get("thoughtSignature") or p.get("thought_signature")
            if isinstance(thought_sig, str):
                try:
                    thought_sig = base64.b64decode(thought_sig)
                except Exception:
                    thought_sig = thought_sig.encode("utf-8")
            part_kwargs = {}
            if thought is not None:
                part_kwargs["thought"] = thought
            if thought_sig is not None:
                part_kwargs["thought_signature"] = thought_sig
            if "text" in p:
                part_kwargs["text"] = p["text"]
                parts.append(gt.Part(**part_kwargs))
            elif "inlineData" in p or "inline_data" in p:
                inline = p.get("inlineData") or p.get("inline_data") or {}
                mime_type = inline.get("mimeType") or inline.get("mime_type")
                data_b64 = inline.get("data")
                if mime_type and data_b64:
                    data = base64.b64decode(data_b64)
                    part_kwargs["inline_data"] = gt.Blob(data=data, mime_type=mime_type)
                    parts.append(gt.Part(**part_kwargs))
            elif "fileData" in p or "file_data" in p:
                file_info = p.get("fileData") or p.get("file_data") or {}
                part_kwargs["file_data"] = gt.FileData(
                    file_uri=file_info.get("fileUri") or file_info.get("file_uri"),
                    mime_type=file_info.get("mimeType") or file_info.get("mime_type")
                )
                parts.append(gt.Part(**part_kwargs))
            elif "functionCall" in p or "function_call" in p:
                fc = p.get("functionCall") or p.get("function_call") or {}
                name = fc.get("name")
                args = fc.get("args") or {}
                if name:
                    part_kwargs["function_call"] = gt.FunctionCall(name=name, args=args)
                    parts.append(gt.Part(**part_kwargs))
            elif "functionResponse" in p or "function_response" in p:
                fr = p.get("functionResponse") or p.get("function_response") or {}
                name = fr.get("name")
                response = fr.get("response") or {}
                if name:
                    part_kwargs["function_response"] = gt.FunctionResponse(name=name, response=response)
                    parts.append(gt.Part(**part_kwargs))
            elif thought is not None or thought_sig is not None:
                parts.append(gt.Part(**part_kwargs))
        contents.append(gt.Content(role=role, parts=parts))
    return contents


def parse_gemini_tools(raw_tools: list) -> List[gt.Tool]:
    tools = []
    for t in raw_tools or []:
        if not isinstance(t, dict):
            continue
        if "googleSearch" in t or "google_search" in t:
            tools.append(gt.Tool(google_search=gt.GoogleSearch()))
        elif "functionDeclarations" in t or "function_declarations" in t:
            decls = t.get("functionDeclarations") or t.get("function_declarations") or []
            func_decls = []
            for d in decls:
                func_decls.append(gt.FunctionDeclaration(**d))
            tools.append(gt.Tool(function_declarations=func_decls))
    return tools
