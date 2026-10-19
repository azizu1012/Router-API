"""Request and response schemas for self-service alias routes."""

from typing import Optional, Union, List, Dict, Any

from pydantic import BaseModel, Field


class CreateAliasRequest(BaseModel):
    alias_name: str = Field(..., description="Name client will call")
    target_model: str = Field(..., description="Actual model to route to")
    target_endpoint: Optional[str] = Field(None, description="Custom endpoint name")
    account_key_id: Optional[str] = Field(None, description="Key-specific alias")
    label: str = Field(default="", description="User description")
    enabled: bool = Field(default=True, description="Initial enabled state")
    targets: Optional[Union[str, List[Any], Dict[str, Any]]] = Field(default="", description="Combo Model Mix targets")
    strategy: Optional[str] = Field(default="waterfall", description="Combo dispatch strategy (waterfall | load_balance)")
    context_length: Optional[int] = Field(default=0, description="Context length override (0 = auto-compute min)")


class UpdateAliasRequest(BaseModel):
    enabled: Optional[bool] = Field(None, description="New enabled state")
    label: Optional[str] = Field(None, description="New label")
    target_model: Optional[str] = Field(None, description="New target model")
    target_endpoint: Optional[str] = Field(None, description="New target endpoint")
    targets: Optional[Union[str, List[Any], Dict[str, Any]]] = Field(None, description="Combo Model Mix targets")
    strategy: Optional[str] = Field(None, description="Combo dispatch strategy")
    context_length: Optional[int] = Field(None, description="Context length override")


class AliasResponse(BaseModel):
    alias_id: str
    account_id: str
    account_key_id: Optional[str]
    alias_name: str
    target_model: str
    target_endpoint: Optional[str]
    enabled: bool
    label: str
    created_at: int
    updated_at: int
    targets: Optional[str] = ""
    strategy: Optional[str] = "waterfall"
    context_length: Optional[int] = 0


class AliasTemplateResponse(BaseModel):
    name: str
    alias_name: str
    target_model: str
    target_endpoint: Optional[str] = None
    description: str
    category: Optional[str] = "General"


__all__ = [
    "CreateAliasRequest", "UpdateAliasRequest", "AliasResponse",
    "AliasTemplateResponse",
]

