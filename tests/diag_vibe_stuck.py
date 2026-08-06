"""Diagnostic: send a /v1/chat/completions request that strongly invites a tool
call, with a mid-task history, against several models. Log what upstream emitted
(content vs tool_calls) and the finish_reason.

Usage: python tests/diag_vibe_stuck.py
Reads the SSE stream and reports: #content chunks, #tool_call chunks, tool_call
names/args, raw tail (to see raw DSML/cpa leakage), finish_reason.
"""
import sys, os, json, http.client

BASE_HOST, BASE_PORT = "127.0.0.1", 8080
H = {"Content-Type": "application/json", "Authorization": "Bearer sk-test"}

tools = [{"type": "function", "function": {
    "name": "read_file", "description": "Read a file's contents.",
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string"}},
                   "required": ["path"]}}}]

# Mid-task: assistant already said it will read a file, then a tool result came
# back, now user asks to continue. Model MUST call read_file again to continue.
msgs = [
    {"role": "system", "content": "You are a coding agent. Use tools to do tasks."},
    {"role": "user", "content": "Please read the file src/app.py and tell me its first line."},
    {"role": "assistant", "content": "I'll read src/app.py now.",
     "tool_calls": [{"id": "call_1", "type": "function",
                     "function": {"name": "read_file", "arguments": '{"path":"src/app.py"}'}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": "import os"},
    {"role": "user", "content": "Now also read src/utils.py the same way and show its first line."},
]


def run(model):
    body = json.dumps({"model": model, "stream": True, "max_tokens": 1024,
                       "messages": msgs, "tools": tools, "tool_choice": "auto"})
    c = http.client.HTTPConnection(BASE_HOST, BASE_PORT, timeout=120)
    c.request("POST", "/v1/chat/completions", body, H)
    r = c.getresponse()
    print("\n===== model=%s status=%d =====" % (model, r.status))
    buf = b""
    n_content = 0
    n_tc = 0
    tc_seen = []
    finish = None
    raw_tail = ""
    all_content = []
    while True:
        ch = r.read1(4096)
        if not ch:
            break
        buf += ch
        while b"\n\n" in buf:
            ev, buf = buf.split(b"\n\n", 1)
            s = ev.decode("utf-8", "ignore")
            for line in s.split("\n"):
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]" or not payload:
                    continue
                try:
                    o = json.loads(payload)
                except Exception:
                    continue
                choices = o.get("choices") or []
                if not choices:
                    # error chunk or sources
                    if isinstance(o.get("error"), dict):
                        print("  ERROR chunk:", o["error"])
                    continue
                ch0 = choices[0]
                delta = ch0.get("delta", {}) or {}
                fr = ch0.get("finish_reason")
                if fr:
                    finish = fr
                if delta.get("content"):
                    n_content += 1
                    all_content.append(delta["content"])
                tcs = delta.get("tool_calls")
                if tcs:
                    n_tc += 1
                    for t in tcs:
                        fn = (t.get("function") or {})
                        if fn.get("name"):
                            tc_seen.append({"id": t.get("id"), "name": fn["name"]})
                        if fn.get("arguments"):
                            tc_seen and tc_seen[-1].setdefault("args", "")
                            if tc_seen:
                                tc_seen[-1]["args"] = tc_seen[-1].get("args", "") + fn["arguments"]
    raw_tail = "".join(all_content)
    print("finish_reason = %s" % finish)
    print("content_chunks = %d, tool_call_chunks = %d" % (n_content, n_tc))
    print("tool_calls parsed = %s" % json.dumps(tc_seen, ensure_ascii=False))
    print("raw_content tail (last 300 chars):")
    print("  " + raw_tail[-300:].replace("\n", "\\n"))
    return finish, tc_seen, raw_tail


results = {}
for model in ["Claude Sonnet 5", "GPT-5.5", "Claude Opus 4.8"]:
    try:
        f, tc, raw = run(model)
        results[model] = {"finish": f, "n_tc": len(tc), "had_dsml": "<|DSML|" in raw or "tool_calls>" in raw,
                          "had_cpa": "cpa_final_answer" in raw or "multi_tool_use" in raw,
                          "had_tool_name": "<tool_name>" in raw}
    except Exception as e:
        print("model=%s EXC %r" % (model, e))
        results[model] = {"exc": repr(e)}

print("\n\n===== SUMMARY =====")
for m, r in results.items():
    print("%-18s %s" % (m, json.dumps(r, ensure_ascii=False)))
