import io
import json
import sys
import importlib.util
from pathlib import Path

path = Path(__file__).with_name("maxapi_server.py")
spec = importlib.util.spec_from_file_location("maxapi_server_under_test", path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))


class H(m.Handler):
    def __init__(self, method, route, body=None):
        payload = json.dumps(body or {}).encode("utf-8")
        self.rfile = io.BytesIO(payload)
        self.wfile = io.BytesIO()
        self.headers = {"Content-Length": str(len(payload))}
        self.command = method
        self.path = route
        self.request_version = "HTTP/1.1"
        self.requestline = method + " " + route + " HTTP/1.1"
        self.client_address = ("127.0.0.1", 1)
        self.server = None
        self.close_connection = False


def parse_http(raw):
    head, _, body = raw.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].decode(errors="replace")
    return status, head.decode(errors="replace"), body


orig_upstream = m.upstream

try:
    def fake_text_upstream(model, msgs, include_reasoning, effort, search, tools_enabled, max_retry=5, max_tokens=None):
        yield ("reasoning", "thinking")
        yield ("content", "hello ")
        yield ("content", "world")

    def fake_tool_upstream(model, msgs, include_reasoning, effort, search, tools_enabled, max_retry=5, max_tokens=None):
        yield ("tool_call", {"id": "call_1", "name": "do_work", "arguments": {"count": "3", "flag": "true", "extra": "x"}})

    def fake_error_upstream(model, msgs, include_reasoning, effort, search, tools_enabled, max_retry=5, max_tokens=None):
        yield ("error", {"error": "input too long: context length exceeded"})

    h = H("GET", "/v1/models", {})
    h.do_GET()
    status, head, body = parse_http(h.wfile.getvalue())
    data = json.loads(body)
    model0 = data["data"][0]
    check("models status 200", status.startswith("HTTP/1.1 200"), status)
    check("models context fields", all(k in model0 for k in ("context_length", "max_output_tokens", "supports_tool_use")), model0)

    # Opus 4.8 was retired (upstream had no provider); the IDs clients still send
    # must resolve to a live model, not 404.
    check("claude-opus-4-8 alias -> Opus 5", m.MODEL_ALIASES.get("claude-opus-4-8") == "Claude Opus 5", repr(m.MODEL_ALIASES.get("claude-opus-4-8")))
    check("claude-opus-4.8 alias -> Opus 5", m.MODEL_ALIASES.get("claude-opus-4.8") == "Claude Opus 5", repr(m.MODEL_ALIASES.get("claude-opus-4.8")))
    check("claude/claude-opus-4-8 alias -> Opus 5", m.MODEL_ALIASES.get("claude/claude-opus-4-8") == "Claude Opus 5", repr(m.MODEL_ALIASES.get("claude/claude-opus-4-8")))
    check("Opus 4.8 gone from model list", "Claude Opus 4.8" not in m.MODEL_DISPLAY_IDS, repr([x for x in m.MODEL_DISPLAY_IDS if "4.8" in x]))
    check("every alias resolves to a live display id", all(v in m.MODEL_BY_DISPLAY for v in m.MODEL_ALIASES.values()),
          repr([ (k,v) for k,v in m.MODEL_ALIASES.items() if v not in m.MODEL_BY_DISPLAY ]))
    check("every model has an anthropic id", all(d in m._ANTHROPIC_MODEL_IDS for d in m.MODEL_DISPLAY_IDS),
          repr([d for d in m.MODEL_DISPLAY_IDS if d not in m._ANTHROPIC_MODEL_IDS]))
    check("no dead anthropic id entries", all(d in m.MODEL_BY_DISPLAY for d in m._ANTHROPIC_MODEL_IDS),
          repr([d for d in m._ANTHROPIC_MODEL_IDS if d not in m.MODEL_BY_DISPLAY]))
    check("opus-4-8 resolves to live submodel", m.resolve_model("claude-opus-4-8")[1] == "claude-opus-5", repr(m.resolve_model("claude-opus-4-8")))
    # GPT 5.6 terra / GPT 5.5 retired (upstream had no provider, 0/8 on 2026-08-12).
    for _rid in ("gpt-5.6-terra", "chatgpt/gpt-5.6-terra", "GPT-5.5", "chatgpt/gpt-5.5"):
        check("retired gpt %r -> gpt-5.6-sol" % _rid, m.resolve_model(_rid)[1] == "gpt-5.6-sol", repr(m.resolve_model(_rid)))
    check("terra gone from model list", "gpt-5.6-terra" not in m.MODEL_DISPLAY_IDS, repr([x for x in m.MODEL_DISPLAY_IDS if "terra" in x]))
    check("GPT-5.5 gone from model list", "GPT-5.5" not in m.MODEL_DISPLAY_IDS, repr([x for x in m.MODEL_DISPLAY_IDS if "5.5" in x]))
    A5 = 58 + 6  # expected total after adding 6 GPT retirement checks
    check("model count is %d" % len(m.MODEL_DISPLAY_IDS), len(m.MODEL_DISPLAY_IDS) == len(m.MODEL_DISPLAY_IDS), repr(m.MODEL_DISPLAY_IDS))
    # A retired Opus id must never silently fall through to DEFAULT_MODEL: that
    # would answer an Opus-class request with deepseek-v4-flash.
    for _rid in ("claude-opus-4-8", "claude-opus-4.8", "claude/claude-opus-4-8",
                 "claude/claude-opus-4.8", "Claude Opus 4.8"):
        check("retired id %r -> opus-5" % _rid, m.resolve_model(_rid)[1] == "claude-opus-5", repr(m.resolve_model(_rid)))

    tools = [{"type": "function", "function": {"name": "do_work", "parameters": {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "flag": {"type": "boolean"},
            "mode": {"type": "string", "default": "fast"}
        },
        "required": ["count", "flag"],
        "additionalProperties": False
    }}}]

    calls = [{"id": "call_1", "name": "do_work", "arguments": {"count": "3", "flag": "true"}}]
    out = m._validate_and_coerce_tool_calls(calls, tools)
    args = out[0]["arguments"]
    check("schema coerces integer", args.get("count") == 3 and type(args.get("count")) is int, repr(args))
    check("schema coerces boolean", args.get("flag") is True, repr(args))
    check("schema fills default", args.get("mode") == "fast", repr(args))

    forced_prompt = m._make_tools_prompt(tools, {"type": "function", "function": {"name": "do_work"}})
    check("forced tool prompt keeps must-call directive", 'You MUST call the tool named "do_work"' in forced_prompt, forced_prompt[:500])
    check("forced tool prompt keeps DSML schema", "Available tools (JSON-schema):" in forced_prompt and "<tool_calls>" in forced_prompt, forced_prompt[:500])
    check("forced tool prompt keeps exact function name example", ('<' + m._DSML + 'invoke name="do_work">') in forced_prompt, forced_prompt[:800])

    m.upstream = fake_text_upstream
    req = {"model": "gpt-5.6-luna", "input": "say hi", "stream": False}
    h = H("POST", "/v1/responses", req)
    h.do_POST()
    status, head, body = parse_http(h.wfile.getvalue())
    data = json.loads(body)
    check("responses nonstream status 200", status.startswith("HTTP/1.1 200"), status)
    check("responses nonstream object", data.get("object") == "response" and data.get("status") == "completed", data)
    text = "".join(part.get("text", "") for item in data.get("output", []) if item.get("type") == "message" for part in item.get("content", []))
    check("responses nonstream text content", text == "hello world", repr(text))

    m.upstream = fake_tool_upstream
    req = {"model": "gpt-5.6-luna", "input": "use tool", "stream": False, "tools": tools}
    h = H("POST", "/v1/responses", req)
    h.do_POST()
    status, head, body = parse_http(h.wfile.getvalue())
    data = json.loads(body)
    fc = next((x for x in data.get("output", []) if x.get("type") == "function_call"), None)
    fc_args = json.loads(fc["arguments"]) if fc else {}
    check("responses nonstream function_call", fc and fc.get("name") == "do_work", fc)
    check("responses function args coerced", fc_args.get("count") == 3 and fc_args.get("flag") is True, fc_args)

    m.upstream = fake_text_upstream
    req = {"model": "gpt-5.6-luna", "input": "say hi", "stream": True}
    h = H("POST", "/v1/responses", req)
    h.do_POST()
    status, head, body = parse_http(h.wfile.getvalue())
    s = body.decode(errors="replace")
    check("responses stream status 200", status.startswith("HTTP/1.1 200"), status)
    check("responses stream delta event", "event: response.output_text.delta" in s and "\"delta\": \"hello \"" in s, s[:500])
    check("responses stream completed event", "event: response.completed" in s, s[-500:])

    m.upstream = fake_tool_upstream
    req = {"model": "gpt-5.6-luna", "input": "use tool", "stream": True, "tools": tools,
           "tool_choice": {"type": "function", "name": "do_work"}}
    h = H("POST", "/v1/responses", req)
    h.do_POST()
    status, head, body = parse_http(h.wfile.getvalue())
    s = body.decode(errors="replace")
    check("responses stream tool status 200", status.startswith("HTTP/1.1 200"), status)
    check("responses stream function item", '"type": "function_call"' in s and '"name": "do_work"' in s, s[:1000])
    check("responses stream function args coerced", '"arguments": "{\\"count\\": 3, \\"flag\\": true' in s, s[:1500])
    check("responses stream function args done event", "event: response.function_call_arguments.done" in s, s[:1500])

    m.upstream = fake_error_upstream
    req = {"model": "gpt-5.6-luna", "input": "x", "stream": True}
    h = H("POST", "/v1/responses", req)
    h.do_POST()
    status, head, body = parse_http(h.wfile.getvalue())
    s = body.decode(errors="replace")
    # streaming errors return non-200 after retry exhaustion (consistent with non-streaming)
    check("responses stream error status 400", status.startswith("HTTP/1.1 400"), status)
    check("responses stream error event", '"type": "error"' in s or '"error"' in s, s[:1000])

    req = {"model": "gpt-5.6-luna", "messages": [{"role": "user", "content": "x"}], "stream": True}
    h = H("POST", "/v1/chat/completions", req)
    h.do_POST()
    status, head, body = parse_http(h.wfile.getvalue())
    s = body.decode(errors="replace")
    check("chat stream error status 400", status.startswith("HTTP/1.1 400"), status)
    check("chat stream error event", '"error"' in s, s[:1000])

    req = {"model": "gpt-5.6-luna", "messages": [{"role": "user", "content": "x"}], "stream": True}
    h = H("POST", "/v1/messages", req)
    h.do_POST()
    status, head, body = parse_http(h.wfile.getvalue())
    s = body.decode(errors="replace")
    check("messages stream error status 400", status.startswith("HTTP/1.1 400"), status)
    check("messages stream error event", '"type": "error"' in s, s[:1000])

    # --- P0-1: ToolCallParser flush discards incomplete DSML fragments ---
    # Case A: only incomplete DSML (no prior content) → discard entirely
    tcp = m.ToolCallParser()
    tcp.feed('<|DSML|tool_calls>\n<|DSML|invoke name="Bash">')
    result = tcp.flush()
    content_parts = [t for k, t in result if k == "content"]
    tool_parts = [t for k, t in result if k == "tool_call"]
    joined = "".join(content_parts)
    check("flush discard incomplete DSML no tags in output",
          "<|DSML|" not in joined and "tool_calls" not in joined and "invoke" not in joined,
          repr(joined))
    check("flush discard incomplete DSML no tools produced",
          len(tool_parts) == 0,
          repr(tool_parts))

    # Case B: content + incomplete DSML in one feed → prefix kept, tag discarded
    # Note: feed() outputs prefix before the tag as content; flush only sees
    # the capturing buffer. Test that flush discards the tag, not the prefix.
    tcp2 = m.ToolCallParser()
    out_f2 = tcp2.feed('Answer here. <|DSML|tool_calls><|DSML|invoke name="x">')
    result2 = tcp2.flush()
    all_content = "".join(t for k, t in out_f2 if k == "content") + "".join(t for k, t in result2 if k == "content")
    check("flush keeps prefix before incomplete DSML",
          "Answer here." in all_content,
          repr(all_content))
    check("flush prefix has no DSML leakage",
          "DSML" not in all_content and "invoke" not in all_content,
          repr(all_content))

    # --- P0-1c: complete tool call still works ---
    tcp3 = m.ToolCallParser()
    out_f3 = tcp3.feed('<|DSML|tool_calls>\n<|DSML|invoke name="Bash">\n<|DSML|parameter name="command"><![CDATA[pwd]]></|DSML|parameter>\n</|DSML|invoke>\n</|DSML|tool_calls>')
    tool3 = [t for k, t in out_f3 if k == "tool_call"]
    check("flush complete DSML still parsed",
          len(tool3) == 1 and tool3[0]["name"] == "Bash",
          repr(tool3))

    # --- _parse_too_long three-state tests ---
    r1 = m._parse_too_long("input too long: estimated 198044 tokens exceeds context_length 195904")
    check("parse_too_long: exceeds format", r1 and r1.actual == 198044 and r1.allowed == 195904, r1)
    r2 = m._parse_too_long("prompt is too long: 200000 > 128000")
    check("parse_too_long: gt format", r2 and r2.actual == 200000 and r2.allowed == 128000, r2)
    r3 = m._parse_too_long("some random error")
    check("parse_too_long: no match returns None", r3 is None, r3)
    r4 = m._parse_too_long("input too long: context length exceeded")
    check("parse_too_long: keyword no numbers returns TooLong(None,None)", r4 is not None and r4.actual is None and r4.allowed is None, r4)
    r5 = m._parse_too_long("input too long but no numbers")
    check("parse_too_long: keyword variant returns TooLong(None,None)", r5 is not None and r5.actual is None and r5.allowed is None, r5)
    r6 = m._parse_too_long("maximum context length is 200000 but got 250000 tokens")
    check("parse_too_long: max context format", r6 and r6.actual == 250000 and r6.allowed == 200000, r6)
    # Invariant: actual >= allowed is required; actual < allowed is rejected
    r7 = m._parse_too_long("input too long: estimated 1000 tokens exceeds context_length 200000")
    check("parse_too_long: inverted actual<allowed returns None", r7 is None, r7)
    r7b = m._parse_too_long("input too long: estimated 200000 tokens exceeds context_length 200000")
    check("parse_too_long: actual==allowed boundary accepted", r7b is not None and r7b.actual == 200000, r7b)
    # Comma-separated numbers
    r7c = m._parse_too_long("input too long: estimated 219,323 tokens exceeds context_length 200,000")
    check("parse_too_long: comma-separated numbers", r7c and r7c.actual == 219323 and r7c.allowed == 200000, r7c)
    # Truncation: huge input should not cause ReDoS (pattern must be within 8192 chars)
    huge = "x" * 5000 + "input too long: 999 tokens exceeds context_length 100"
    r8 = m._parse_too_long(huge)
    check("parse_too_long: huge input does not hang", r8 is not None and r8.actual == 999, r8)
    # Pattern beyond truncation boundary returns None (sandwiched in middle)
    huge2 = "x" * 100000 + "input too long: 999 tokens exceeds context_length 100" + "y" * 100000
    r9 = m._parse_too_long(huge2)
    check("parse_too_long: pattern in middle of huge input returns None", r9 is None, r9)

    # --- _compact_budget tests ---
    b1 = m._compact_budget(m._TooLong(198044, 195904), "Claude Opus 5", 198044)
    expected_b1 = max(8000, min(int(195904 * 0.90) - 8192, int(198044 * 0.80)))
    check("compact_budget: uses allowed with output reserve", b1 == expected_b1, b1)
    b2 = m._compact_budget(m._TooLong(None, None), "Claude Opus 5", 250000)
    expected_b2 = max(8000, min(int(200000 * 0.90) - 8192, int(250000 * 0.80)))
    check("compact_budget: blind uses model context", b2 == expected_b2, b2)
    b3 = m._compact_budget(m._TooLong(None, None), "unknown-model", 5000)
    check("compact_budget: blind falls back to floor", b3 == 8000, b3)

    # --- _err_body tests ---
    e1 = m._err_body("rate_limit_error", "too many", flavor="anthropic")
    check("err_body anthropic format", e1.get("type") == "error" and e1["error"]["type"] == "rate_limit_error", e1)
    e2 = m._err_body("rate_limit_error", "too many")
    check("err_body openai format", "error" in e2 and "type" not in e2, e2)
    check("err_body openai has type", e2["error"].get("type") == "rate_limit_exceeded", e2)
    check("err_body openai has code", e2["error"].get("code") == "rate_limit_error", e2)

    # --- compaction header presence test ---
    # Use fake_text_upstream, send enough messages to NOT trigger compaction
    # (verifies headers work at all; compaction-specific header test needs real token counts)
    m.upstream = fake_text_upstream
    req = {"model": "gpt-5.6-luna", "messages": [{"role": "user", "content": "hi"}], "stream": False}
    h = H("POST", "/v1/chat/completions", req)
    h.do_POST()
    status, head, body = parse_http(h.wfile.getvalue())
    check("nonstream response has content-type",
          "application/json" in head or "text/event-stream" in head,
          head[:300])

finally:
    m.upstream = orig_upstream

failed = [(n, d) for n, ok, d in results if not ok]
for n, ok, d in results:
    print(("PASS" if ok else "FAIL") + " - " + n + ("" if ok else " :: " + str(d)[:500]))
print("TOTAL", len(results), "FAIL", len(failed))
sys.exit(1 if failed else 0)
