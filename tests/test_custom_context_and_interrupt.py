"""Context windows from upstream + Claude Code interrupt repair.

Covers three fixes that share one symptom (the client guesses wrong):

1. Custom endpoints persist the context window their upstream advertises
   (OpenRouter ``context_length``, Gemini ``inputTokenLimit``, nested
   ``capabilities.contextWindow``) and expose it per model on /v1/models,
   instead of the hardcoded 200k fallback.
2. Native pools keep the safe ``context_length`` ceiling but also publish
   the physical window (``physical_context``) so clients that read it can
   set the right compaction threshold.
3. A user interrupt landing between ``assistant(tool_calls)`` and its late
   ``tool`` result no longer 400s strict upstreams — the result is
   re-attached next to its call, and a dangling forced ``tool_choice``
   left behind when tools are gone is dropped by the sanitizer.
"""

import sys

import pytest

sys.path.insert(0, ".")

from src.core.providers.custom_endpoint.manager import _upstream_context_window


class TestUpstreamContextWindowParsing:
    def test_openrouter_snake_case(self):
        assert _upstream_context_window({"id": "m", "context_length": 1_000_000}) == 1_000_000

    def test_gemini_camel_case(self):
        assert _upstream_context_window({"name": "m", "inputTokenLimit": 1_048_576}) == 1_048_576

    def test_nested_capabilities_camel_case(self):
        entry = {"id": "m", "capabilities": {"contextWindow": 372_000}}
        assert _upstream_context_window(entry) == 372_000

    def test_first_positive_wins(self):
        entry = {"context_length": 0, "inputTokenLimit": 200_000}
        assert _upstream_context_window(entry) == 200_000

    def test_garbage_means_unknown(self):
        assert _upstream_context_window({"id": "m"}) == 0
        assert _upstream_context_window({"context_length": "huge"}) == 0
        assert _upstream_context_window({"context_length": -5}) == 0
        assert _upstream_context_window("not-a-dict") == 0  # type: ignore[arg-type]


class TestInterruptedToolResultReorder:
    def test_late_result_moves_next_to_its_call(self):
        from src.core.providers.custom_endpoint.client import (
            _drop_orphaned_tool_results,
            _reorder_interrupted_tool_results,
        )

        history = [
            {"role": "assistant", "tool_calls": [
                {"id": "A", "function": {"name": "Read", "arguments": "{}"}}]},
            {"role": "user", "content": "chen ngang"},
            {"role": "tool", "tool_call_id": "A", "content": "r"},
        ]
        fixed = _reorder_interrupted_tool_results(history)
        assert fixed[1] == {"role": "tool", "tool_call_id": "A", "content": "r"}
        assert fixed[2] == {"role": "user", "content": "chen ngang"}
        # The pair must now survive the orphan guard instead of being dropped.
        kept = _drop_orphaned_tool_results(fixed)
        assert any(m.get("role") == "tool" for m in kept)
        assert any(m.get("tool_calls") for m in kept if m.get("role") == "assistant")

    def test_result_without_a_call_is_left_for_the_orphan_guard(self):
        from src.core.providers.custom_endpoint.client import (
            _reorder_interrupted_tool_results,
        )

        history = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "ORPHAN", "content": "stray"},
        ]
        assert _reorder_interrupted_tool_results(history) == history

    def test_plain_chat_is_untouched(self):
        from src.core.providers.custom_endpoint.client import (
            _reorder_interrupted_tool_results,
        )

        history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        assert _reorder_interrupted_tool_results(history) == history


class TestDanglingToolChoiceSanitizer:
    def test_openai_forced_choice_without_tools_is_dropped(self):
        from src.core.runtime.effort import sanitize_custom_endpoint_payload

        payload = {
            "tool_choice": {"type": "function", "function": {"name": "Read"}},
            "messages": [],
        }
        sanitize_custom_endpoint_payload(payload, api_format="openai")
        assert "tool_choice" not in payload

    def test_openai_plain_auto_without_tools_survives(self):
        from src.core.runtime.effort import sanitize_custom_endpoint_payload

        payload = {"tool_choice": "auto", "messages": []}
        sanitize_custom_endpoint_payload(payload, api_format="openai")
        assert payload["tool_choice"] == "auto"

    def test_anthropic_forced_choice_without_tools_is_dropped(self):
        from src.core.runtime.effort import sanitize_custom_endpoint_payload

        payload = {
            "tool_choice": {"type": "tool", "name": "Read"},
            "tools": [],
            "messages": [],
        }
        sanitize_custom_endpoint_payload(payload, api_format="anthropic")
        assert "tool_choice" not in payload


class TestEndpointContextPersistence:
    def test_per_model_context_survives_refresh_shape(self, temp_db):
        from src.backend.endpoints.manager import (
            add_endpoint_db,
            get_endpoint_db,
            update_endpoint_db,
        )
        from src.logical_HQ_translator.model.alias import get_model_safe_context_length

        add_endpoint_db("ctx-ep", "http://ctx.local", "k")
        update_endpoint_db(
            "ctx-ep",
            models=["muse-1.3", "tiny"],
            enabled_models=["muse-1.3", "tiny"],
            model_contexts={"muse-1.3": 1_000_000},
            context_length=500_000,
        )
        ep = get_endpoint_db("ctx-ep")
        assert ep is not None
        assert ep["model_contexts"] == {"muse-1.3": 1_000_000}
        assert ep["context_length"] == 500_000

        # Per-model value wins; other models fall back to the endpoint default.
        assert get_model_safe_context_length("muse-1.3", "ctx-ep") == 1_000_000
        assert get_model_safe_context_length("tiny", "ctx-ep") == 500_000
        assert get_model_safe_context_length("MUSE-1.3", "ctx-ep") == 1_000_000

    def test_context_keys_normalize_at_save_time(self, temp_db):
        from src.backend.endpoints.manager import (
            add_endpoint_db,
            get_endpoint_db,
            update_endpoint_db,
        )

        add_endpoint_db("ctx-ep3", "http://ctx3.local", "k")
        update_endpoint_db("ctx-ep3", model_contexts={" Muse-1.3 ": 1_000_000, "": 5})
        ep = get_endpoint_db("ctx-ep3")
        assert ep is not None
        assert ep["model_contexts"] == {"muse-1.3": 1_000_000}

    def test_router_lists_custom_context(self, temp_db):
        from src.backend.endpoints.manager import add_endpoint_db, update_endpoint_db
        from src.core.providers import _custom_endpoint_manager
        from src.core.router import router

        add_endpoint_db("ctx-ep2", "http://ctx2.local", "k")
        update_endpoint_db(
            "ctx-ep2",
            models=["muse-1.3"],
            enabled_models=["muse-1.3"],
            model_contexts={"muse-1.3": 1_000_000},
            context_length=1_000_000,
        )
        _custom_endpoint_manager._invalidate_cache()
        found = [m for m in router.list_models() if m["id"] == "muse-1.3"]
        assert found, "custom model must appear in list_models"
        assert found[0].get("context_length") == 1_000_000


class TestRefreshMergesStaleContexts:
    """Refresh must not silently drop contexts the upstream stops advertising.

    Real-world trigger (found by operator on live traffic): an upstream
    hiccup returns a partial model list, or renames a model. A blind
    overwrite would wipe the stored window for the missing model and the
    client would silently fall back to 200k. Merge keeps the old value
    until the upstream advertises a new one.
    """

    def test_missing_model_keeps_old_context(self, temp_db, monkeypatch):
        import asyncio

        from src.backend.endpoints.manager import (
            add_endpoint_db,
            get_endpoint_db,
            update_endpoint_db,
        )
        from src.core.providers.custom_endpoint.manager import (
            _custom_endpoint_manager,
        )

        add_endpoint_db("merge-ep", "http://merge.local", "k")
        update_endpoint_db(
            "merge-ep",
            models=["muse-1.3", "tiny"],
            enabled_models=["muse-1.3", "tiny"],
            model_contexts={"muse-1.3": 1_000_000, "tiny": 8_000},
            context_length=1_000_000,
        )

        async def fake_fetch(base, key):
            return {
                "base": "http://merge.local",
                "data": {"data": [{"id": "tiny", "context_length": 8_000}]},
            }

        monkeypatch.setattr(
            _custom_endpoint_manager, "_try_fetch_models", fake_fetch
        )
        monkeypatch.setattr(
            _custom_endpoint_manager, "_probe_chat_endpoint",
            lambda *a, **k: asyncio.Future(),
        )
        # _probe_chat_endpoint is awaited; make it return True.
        async def fake_probe(*a, **k):
            return True

        monkeypatch.setattr(
            _custom_endpoint_manager, "_probe_chat_endpoint", fake_probe
        )

        asyncio.run(_custom_endpoint_manager.fetch_models("merge-ep"))

        ep = get_endpoint_db("merge-ep")
        assert ep is not None
        # muse-1.3 vanished from upstream but its window must survive.
        assert ep["model_contexts"].get("muse-1.3") == 1_000_000
        assert ep["model_contexts"].get("tiny") == 8_000
        # Default still reflects the surviving max, not zero.
        assert ep["context_length"] == 1_000_000

    def test_empty_upstream_keeps_previous_default(self, temp_db, monkeypatch):
        import asyncio

        from src.backend.endpoints.manager import (
            add_endpoint_db,
            get_endpoint_db,
            update_endpoint_db,
        )
        from src.core.providers.custom_endpoint.manager import (
            _custom_endpoint_manager,
        )

        add_endpoint_db("empty-ep", "http://empty.local", "k")
        update_endpoint_db(
            "empty-ep",
            models=["muse-1.3"],
            enabled_models=["muse-1.3"],
            model_contexts={"muse-1.3": 1_000_000},
            context_length=1_000_000,
        )

        async def fake_fetch(base, key):
            return {"base": "http://empty.local", "data": {"data": []}}

        async def fake_probe(*a, **k):
            return True

        monkeypatch.setattr(
            _custom_endpoint_manager, "_try_fetch_models", fake_fetch
        )
        monkeypatch.setattr(
            _custom_endpoint_manager, "_probe_chat_endpoint", fake_probe
        )

        asyncio.run(_custom_endpoint_manager.fetch_models("empty-ep"))

        ep = get_endpoint_db("empty-ep")
        assert ep is not None
        assert ep["model_contexts"].get("muse-1.3") == 1_000_000
        assert ep["context_length"] == 1_000_000


class TestNativeWinsOverCustomShadow:
    """A custom endpoint re-advertising a native id must not shadow it.

    Real-world trigger (found by operator on live traffic): an endpoint
    lists a model with the same id as a native alias (e.g. "flash").
    Appending it would shadow the native entry's context with an empty
    one on /v1/models.
    """

    def test_native_entry_not_shadowed(self, temp_db):
        from src.backend.endpoints.manager import (
            add_endpoint_db,
            update_endpoint_db,
        )
        from src.core.providers import _custom_endpoint_manager
        from src.core.router import router

        native_ids = {m["id"] for m in router.list_models()}
        assert native_ids, "need at least one native model for this test"
        victim = sorted(native_ids)[0]
        native_ctx = next(
            m.get("context_length") for m in router.list_models()
            if m["id"] == victim
        )

        add_endpoint_db("shadow-ep", "http://shadow.local", "k")
        update_endpoint_db(
            "shadow-ep",
            models=[victim],
            enabled_models=[victim],
        )
        _custom_endpoint_manager._invalidate_cache()

        matches = [m for m in router.list_models() if m["id"] == victim]
        assert len(matches) == 1, (
            f"custom endpoint must not duplicate native id {victim!r}"
        )
        assert matches[0].get("owned_by") == "router_api"
        if native_ctx:
            assert matches[0].get("context_length") == native_ctx
