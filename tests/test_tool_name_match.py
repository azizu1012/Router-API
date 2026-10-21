"""The model's spelling of a tool name is not always the declared spelling.

Gemini rewrites namespaces on the way out. A client that receives a name it
never declared rejects the call outright ("No such tool available"), and the
turn is lost even though the model picked a tool the client does have. These
tests pin the repair to the cases that are *unambiguous*: one candidate or
nothing. A wrong repair would hand the client a tool it never asked for, which
is worse than leaving the failure visible, so ambiguity is a first-class case
here rather than an edge case.
"""

import pytest

from src.core.providers.gemini.format import sanitize_function_name
from src.core.runtime.tool_name_match import (
    _sanitize_like_gemini,
    _strip_transport_prefixes,
    declared_tool_names,
    resolve_declared_tool_name,
)


CODEGRAPH_DECLARED = "mcp__codegraph__codegraph_explore"
CODEGRAPH_EMITTED = "codegraph__codegraph_explore"


class TestDeclaredToolNames:
    def test_reads_the_nested_shape_this_router_builds(self):
        tools = [{"type": "function", "function": {"name": "Read", "parameters": {}}}]
        assert declared_tool_names(tools) == ["Read"]

    def test_reads_the_flattened_shape(self):
        tools = [{"type": "function", "name": "Bash", "parameters": {}}]
        assert declared_tool_names(tools) == ["Bash"]

    def test_accepts_plain_strings(self):
        assert declared_tool_names(["Read", "Bash"]) == ["Read", "Bash"]

    def test_preserves_order_and_drops_duplicates(self):
        tools = [
            {"function": {"name": "Bash"}},
            {"function": {"name": "Read"}},
            {"function": {"name": "Bash"}},
        ]
        assert declared_tool_names(tools) == ["Bash", "Read"]

    def test_empty_inputs_are_empty_lists(self):
        assert declared_tool_names(None) == []
        assert declared_tool_names([]) == []
        assert declared_tool_names({}) == []

    def test_entries_without_a_name_are_skipped(self):
        tools = [{"type": "function", "function": {}}, {"type": "function"}, "Read", 42]
        assert declared_tool_names(tools) == ["Read"]


class TestPrefixTiers:
    @pytest.mark.parametrize("emitted", [
        CODEGRAPH_EMITTED,
        "default_api:" + CODEGRAPH_EMITTED,
        "functions." + CODEGRAPH_EMITTED,
        "function." + CODEGRAPH_EMITTED,
        "tools." + CODEGRAPH_EMITTED,
        "tools__" + CODEGRAPH_EMITTED,
    ])
    def test_a_transport_prefix_does_not_hide_the_declared_name(self, emitted):
        assert resolve_declared_tool_name(emitted, [CODEGRAPH_DECLARED]) == CODEGRAPH_DECLARED

    def test_repeated_prefixes_are_stripped_together(self):
        emitted = "default_api:mcp__" + CODEGRAPH_EMITTED
        assert resolve_declared_tool_name(emitted, [CODEGRAPH_DECLARED]) == CODEGRAPH_DECLARED

    def test_a_lost_namespace_segment_still_resolves(self):
        assert resolve_declared_tool_name(CODEGRAPH_EMITTED, [CODEGRAPH_DECLARED]) == CODEGRAPH_DECLARED

    def test_a_gained_namespace_segment_still_resolves(self):
        assert resolve_declared_tool_name(CODEGRAPH_DECLARED, [CODEGRAPH_EMITTED]) == CODEGRAPH_EMITTED

    def test_the_suffix_match_is_segment_bounded(self):
        # "explore" must not match "reexplore"; the "__" boundary is the point.
        assert resolve_declared_tool_name("explore", ["mcp__codegraph__reexplore"]) == "explore"


class TestSanitizedTier:
    def test_the_clients_spelling_comes_back_not_the_sanitized_one(self):
        assert resolve_declared_tool_name("my_tool", ["my tool"]) == "my tool"

    def test_a_leading_digit_regains_its_declared_spelling(self):
        assert resolve_declared_tool_name("_1password", ["1password"]) == "1password"

    def test_truncation_to_64_characters_still_resolves(self):
        declared = "a" * 70
        assert resolve_declared_tool_name("a" * 64, [declared]) == declared


class TestItNeverGuesses:
    def test_an_unknown_name_passes_through(self):
        assert resolve_declared_tool_name("Bash", ["Read", "Write"]) == "Bash"

    def test_two_declarations_that_reduce_to_the_same_name_are_ambiguous(self):
        # Both strip to "a", so picking either would be a guess.
        assert resolve_declared_tool_name("a", ["mcp__a", "functions.a"]) == "a"

    def test_two_declarations_sharing_a_suffix_are_ambiguous(self):
        assert resolve_declared_tool_name("b", ["a__b", "x__a__b"]) == "b"

    def test_case_is_respected(self):
        assert resolve_declared_tool_name("websearch", ["WebSearch"]) == "websearch"

    def test_an_exact_match_wins_over_every_other_tier(self):
        assert resolve_declared_tool_name("a__b", ["a__b", "mcp__a__b"]) == "a__b"

    @pytest.mark.parametrize("emitted", [None, "", 7, "   "])
    def test_non_names_pass_through_untouched(self, emitted):
        assert resolve_declared_tool_name(emitted, ["Read"]) == emitted

    def test_an_empty_declaration_list_changes_nothing(self):
        assert resolve_declared_tool_name(CODEGRAPH_EMITTED, []) == CODEGRAPH_EMITTED


class TestPrefixStripping:
    def test_a_name_that_is_only_a_prefix_is_left_alone(self):
        assert _strip_transport_prefixes("mcp__") == "mcp__"

    def test_stripping_repeats_until_stable(self):
        assert _strip_transport_prefixes("mcp__tools__Read") == "Read"


class TestTheSanitizerCopyAgreesWithTheOriginal:
    @pytest.mark.parametrize("name", [
        "",
        "Read",
        "my tool",
        "1password",
        "a" * 80,
        "a.b:c-d",
        "café",
        "mcp__codegraph__codegraph_explore",
        "  leading space",
    ])
    def test_the_local_mirror_matches_sanitize_function_name(self, name):
        assert _sanitize_like_gemini(name) == sanitize_function_name(name)
