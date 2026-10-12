"""Request and response schemas for self-service endpoint routes."""

from typing import List, Optional

from pydantic import BaseModel, Field


class CreateEndpointRequest(BaseModel):
    name: str = Field(..., description="Endpoint name (will be sanitized)")
    base_url: str = Field(..., description="API base URL")
    auth_key: str = Field(..., description="API authentication key")
    api_format: str = Field(default="openai", description="API format (openai, anthropic, etc.)")
    enabled: bool = Field(default=True, description="Initial enabled state")


class UpdateEndpointRequest(BaseModel):
    base_url: Optional[str] = Field(None, description="New base URL")
    auth_key: Optional[str] = Field(None, description="New auth key")
    enabled: Optional[bool] = Field(None, description="New enabled state")
    api_format: Optional[str] = Field(None, description="New API format")


class ToggleModelRequest(BaseModel):
    model_id: str = Field(..., description="Model ID to toggle or add")
    enabled: bool = Field(default=True, description="Enable or disable this model")


class EndpointResponse(BaseModel):
    name: str
    base_url: str
    enabled: bool
    api_format: str
    account_id: str
    models: List[str]
    enabled_models: Optional[List[str]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


__all__ = [
    "CreateEndpointRequest", "UpdateEndpointRequest", "ToggleModelRequest",
    "EndpointResponse",
]

