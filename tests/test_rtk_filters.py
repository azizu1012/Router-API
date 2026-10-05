import sys
import os
import json

# Ensure root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.logical_HQ_translator.rtk import (
    filter_git_diff,
    filter_git_status,
    filter_grep,
    filter_find,
    filter_smart_truncate,
    compress_messages
)

def test_git_diff_filter():
    diff_input = """diff --git a/src/main.py b/src/main.py
index 123456..789012 100644
--- a/src/main.py
+++ b/src/main.py
@@ -1,5 +1,6 @@
 import os
+import sys
-import time
+import time_custom
"""
    result = filter_git_diff(diff_input)
    assert "src/main.py" in result
    assert "+2 -1" in result
    assert "import sys" in result
    print("test_git_diff_filter passed!")

def test_git_status_filter():
    status_input = """## main
?? tests/test_rtk.py
 M src/main.py
A  src/rtk.py
"""
    result = filter_git_status(status_input)
    assert "* main" in result
    assert "+ Staged: 1 files" in result
    assert "~ Modified: 1 files" in result
    assert "? Untracked: 1 files" in result
    print("test_git_status_filter passed!")

def test_grep_filter():
    grep_input = """src/main.py:10:import sys
src/main.py:25:sys.exit(0)
src/rtk.py:45:def compress_text(text):
"""
    result = filter_grep(grep_input)
    assert "3 matches in 2F:" in result
    assert "[file] src/main.py (2):" in result
    assert "[file] src/rtk.py (1):" in result
    print("test_grep_filter passed!")

def test_find_filter():
    find_input = """src/main.py
src/rtk.py
tests/test_rtk.py
"""
    result = filter_find(find_input)
    assert "3 files in 2 dirs:" in result
    assert "src/  (2)" in result
    assert "tests/  (1)" in result
    print("test_find_filter passed!")

def test_smart_truncate_filter():
    # Construct a string with more than 300 lines to trigger smart truncate
    lines = [f"Line {i}" for i in range(300)]
    input_str = "\n".join(lines)
    result = filter_smart_truncate(input_str)
    assert "Line 0" in result
    assert "Line 119" in result
    assert "... +120 lines truncated" in result
    assert "Line 299" in result
    assert "Line 240" in result
    print("test_smart_truncate_filter passed!")

def test_compress_messages_integration():
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "src/main.py:10:import sys\nsrc/main.py:25:sys.exit(0)\nsrc/rtk.py:45:def compress_text(text):\n"
                    }
                ]
            }
        ]
    }
    
    # Needs to be large enough to trigger MIN_COMPRESS_SIZE (500 bytes)
    long_grep_input = "src/main.py:10:import sys\n" * 30
    body["messages"][0]["content"][0]["content"] = long_grep_input
    
    stats = compress_messages(body, enabled=True)
    assert stats is not None
    assert len(stats["hits"]) > 0
    assert stats["hits"][0]["filter"] == "grep"
    print("test_compress_messages_integration passed!")

if __name__ == "__main__":
    test_git_diff_filter()
    test_git_status_filter()
    test_grep_filter()
    test_find_filter()
    test_smart_truncate_filter()
    test_compress_messages_integration()
    print("\nALL RTK FILTER TESTS PASSED SUCCESSFULY!")
