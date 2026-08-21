"""G-A: dual-channel / list-level tool_call dedupe + placeholder drop."""
import maxapi_server as M

def test_validate_dedupe_and_placeholder():
    tools = [{
        "type": "function",
        "function": {
            "name": "do_work",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer"},
                    "flag": {"type": "boolean"},
                },
                "required": ["count"],
            },
        },
    }]
    tcs = [
        {"id": "a", "name": "do_work", "arguments": {"count": 3, "flag": True}},
        {"id": "b", "name": "do_work", "arguments": {"count": 3, "flag": True}},
        {"id": "c", "name": "tool_name", "arguments": {"x": 1}},
        {"id": "d", "name": "do_work", "arguments": {"count": 4, "flag": False}},
    ]
    out = M._validate_and_coerce_tool_calls(tcs, tools)
    assert len(out) == 2, out
    assert out[0]["arguments"]["count"] == 3
    assert out[1]["arguments"]["count"] == 4
    print("ok validate_dedupe_and_placeholder")

if __name__ == "__main__":
    test_validate_dedupe_and_placeholder()
    print("ALL OK")
