"""Reproduce the 502 upstream_error / TypeError in _build_messages_with_tools
when an assistant message carries tool_calls AND content is an OpenAI list-of-parts.

Run: python tests/test_repro_repro_502_list_content.py
Before fix: TypeError: can only concatenate list (not "str") to list at L713
After fix: returns msgs without crashing.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import maxapi_server as M

tools = [{"type": "function", "function": {
    "name": "get_weather", "description": "get weather",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}]

# Claude Code /v1/chat/completions multi-turn history: assistant content is a
# list of parts (thinking + text) AND tool_calls at top level, then tool results.
messages = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "weather in 北京?"},
    {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "I should call get_weather."},
        {"type": "text", "text": "Let me check."},
    ], "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "get_weather", "arguments": '{"city":"北京"}'}},
    ]},
    {"role": "tool", "tool_call_id": "call_1", "content": "sunny 24C"},
    {"role": "user", "content": "thanks"},
]

try:
    out, enabled = M._build_messages_with_tools(tools, "auto", messages)
    print("PASS: built %d messages, tools_enabled=%s" % (len(out), enabled))
    # the rendered assistant message must be a string
    asst = [m for m in out if m.get("role") == "assistant"]
    if asst:
        c = asst[-1].get("content")
        assert isinstance(c, str), "assistant content must be str, got %s" % type(c).__name__
        print("  last assistant content type=%s len=%d" % (type(c).__name__, len(c)))
        assert "tool_calls" in c.lower() or "<|DSML|tool_calls" in c, "should contain DSML block"
        print("  contains DSML tool_calls block: OK")
    sys.exit(0)
except TypeError as e:
    print("FAIL (reproduced bug): %s" % e)
    sys.exit(1)
