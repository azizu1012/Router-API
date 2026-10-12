"""Model catalog, alias resolution, and pool membership logic."""

from typing import Any, Dict, List, Optional, Set

from src.core.config.api_config import (
    AVAILABLE_MODELS, MODEL_POOLS, active_pool_members, is_sunset_25,
)
from src.core.config_n_logg import config
from src.core.router.pool import ModelPool


class ModelResolutionMixin:
    def list_models(self) -> List[Dict[str, Any]]:
        models = []
        for alias, cfg in AVAILABLE_MODELS.items():
            if cfg.get("hidden"):
                continue
            if not cfg.get("enabled", True):
                # Admin switched it off — offering it here would invite a client
                # to pick a model the router will refuse to route.
                continue
            if is_sunset_25() and alias in ("gemini-flash-25", "gemini-flash-25-lite"):
                continue
            m = {
                "id": alias,
                "object": "model",
                "created": 0,
                "owned_by": "router_api",
                "root": cfg["model_id"],
                "display": cfg["display"],
            }
            cl = cfg.get("context_length")
            if cl:
                m["context_length"] = cl
            models.append(m)

        # Include custom endpoint models
        try:
            from src.core.providers import _custom_endpoint_manager
            for ep in _custom_endpoint_manager.list_endpoints():
                if not ep.get("enabled", True):
                    continue
                for mid in (ep.get("enabled_models") or ep.get("models") or []):
                    if not any(m["id"] == mid for m in models):
                        models.append({
                            "id": mid,
                            "object": "model",
                            "created": 0,
                            "owned_by": ep.get("name", "custom"),
                            "root": mid,
                            "display": f"{mid} ({ep.get('name', 'custom')})",
                        })
        except Exception as e:
            # Dropping this silently makes every custom endpoint model disappear
            # from /v1/models, and the client's next call for one fails with an
            # unknown-model error that points nowhere near the real cause.
            self._log_model_error("[Router] Failed to list custom endpoint models: %s", e)

        return models

    def resolve_model_alias(
        self,
        model: Optional[str],
        account: Optional[Dict[str, Any]] = None,
        account_id: Optional[str] = None,
        account_key_id: Optional[str] = None,
    ) -> str:
        """
        Resolves a raw model string (either alias or concrete model ID) into a standardized alias.
        Supports per-account/per-key dynamic aliases as well as built-in model mappings.

        Args:
            model: The raw model string provided in the request.
            account: Optional user account dictionary.
            account_id: Optional account ID.
            account_key_id: Optional account key ID.

        Returns:
            The resolved standardized model alias. Defaults to `self.current_model` if no match is found.
        """
        raw = (model or "").strip()
        if not raw:
            return self.current_model

        # 1. Resolve user-defined per-account or per-key alias if account info is provided
        acc_id = account_id or (account.get("account_id") if account else None)
        key_id = account_key_id or (account.get("token_key_id") or account.get("account_key_id") if account else None)
        if acc_id:
            try:
                from src.backend.models import aliases as model_aliases
                alias_row = model_aliases.resolve_alias_db(raw, str(acc_id), key_id)
                if alias_row and alias_row.get("target_model"):
                    raw = alias_row["target_model"].strip()
            except Exception:
                pass

        raw_lower = raw.lower()
        if raw_lower in AVAILABLE_MODELS:
            return raw_lower
        for alias, cfg in AVAILABLE_MODELS.items():
            if raw_lower == cfg.get("model_id"):
                return alias
        from src.core.providers import _custom_endpoint_manager
        for ep in _custom_endpoint_manager.list_endpoints():
            if raw in ep.get("models", []) or raw_lower in [m.lower() for m in ep.get("models", [])]:
                return raw
        
        if "haiku" in raw_lower:
            return "gemini-flash-lite"
        if "sonnet" in raw_lower or "opus" in raw_lower:
            return "gemini-flash"
            
        return self.current_model

    def get_model_id(self, alias: str) -> str:
        cfg = AVAILABLE_MODELS.get(alias)
        if cfg:
            return str(cfg.get("model_id", alias))
        from src.core.providers import _custom_endpoint_manager
        for ep in _custom_endpoint_manager.list_endpoints():
            if alias in ep.get("models", []):
                return alias
        return alias

    def resolve_pool(self, alias: str) -> Optional[ModelPool]:
        """
        Resolves and returns a `ModelPool` instance for a given model alias.
        Model pools are used to manage a group of interchangeable model members
        (e.g., different versions of Gemini Flash) for resilience and load balancing.

        Args:
            alias: The alias of the model for which to resolve the pool.

        Returns:
            A `ModelPool` instance if a pool is configured for the alias, otherwise None.
        """
        pool_cfg = MODEL_POOLS.get(alias)
        if pool_cfg:
            members = active_pool_members(alias)

            # Add custom endpoints with pool_assignments for this alias as real pool members
            custom_endpoint_members: Set[str] = set()
            pool_models = self.get_pool_custom_models(alias)
            for pm in pool_models:
                ep_name = pm["endpoint"].get("name", "")
                if ep_name and ep_name not in members:
                    members.append(ep_name)
                    custom_endpoint_members.add(ep_name)

            if not members:
                return None
            pool_cfg = {
                **pool_cfg,
                "members": members,
                "swap_failures": config.POOL_SWAP_FAILURES,
                "max_retry_seconds": config.POOL_RETRY_SECONDS,
            }
            pool = ModelPool.get_or_create(alias, pool_cfg, custom_endpoint_members)
            pool.sync_custom_members(custom_endpoint_members)
            return pool
        return None

    def get_pool_custom_models(self, pool_name: str) -> List[Dict[str, Any]]:
        """
        Retrieves a list of custom endpoint models assigned to a specific pool.
        This allows dynamic integration of custom LLM providers into the routing logic.

        Args:
            pool_name: The name of the pool to check for custom model assignments.

        Returns:
            A list of dictionaries, each containing endpoint details and the assigned model ID.
        """
        from src.core.providers import _custom_endpoint_manager
        results = []
        for ep in _custom_endpoint_manager.list_endpoints():
            if not ep.get("enabled", True):
                continue
            pool_assignments = ep.get("pool_assignments", {})
            if pool_name in pool_assignments:
                model_id = pool_assignments[pool_name]
                enabled_models = ep.get("enabled_models", [])
                # If enabled_models is empty, treat pool_assignment model as implicitly enabled
                if not enabled_models or model_id in enabled_models:
                    results.append({"endpoint": ep, "model_id": model_id})
        return results

