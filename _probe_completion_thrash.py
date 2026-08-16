# -*- coding: utf-8 -*-
"""Live thrash regression: after done tool_result, sol must end_turn without escalate."""
import json, time, urllib.request, sys
sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8080"

def call(path, body, timeout=180):
    data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer sk-test"},
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()), time.time() - t0

tools = [{
    "type": "function",
    "function": {
        "name": "exec_command",
        "description": "Run shell",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    },
}]

DONE = "配置修复已完成并通过最终核验，无需进一步操作。"

# Simulate Codex mid-agent after success: tool_result done, no new user work
msgs = [
    {"role": "user", "content": "fix settings.local.json: remove invalid Write(**) rules"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "exec_command", "arguments": json.dumps({"command": "verify"})},
        }],
    },
    {"role": "tool", "tool_call_id": "call_1", "content": DONE},
]

body = {
    "model": "gpt-5.6-sol",
    "messages": msgs,
    "tools": tools,
    "tool_choice": "auto",
    "stream": False,
    "max_tokens": 256,
}

print("=== thrash_done_should_stop ===", flush=True)
try:
    d, t = call("/v1/chat/completions", body)
    msg = d["choices"][0]["message"]
    tcs = msg.get("tool_calls") or []
    fr = d["choices"][0].get("finish_reason")
    content = msg.get("content") or ""
    # Success: no tool_calls (end_turn/stop). Escalate thrash would force a tool.
    ok = (not tcs) and fr in ("stop", "end_turn", None)
    print(f"[{'PASS' if ok else 'FAIL'}] thrash_done t={t:.1f}s finish={fr} n_tools={len(tcs)} content={content[:120]!r}", flush=True)
    if tcs:
        print("  forced tools:", json.dumps(tcs, ensure_ascii=False)[:300], flush=True)
except Exception as e:
    print(f"[FAIL] thrash_done err={e}", flush=True)
    ok = False

# Control: mid-flight incomplete must still tool
msgs2 = [
    {"role": "user", "content": "fix the file"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "exec_command", "arguments": json.dumps({"command": "cat x"})},
        }],
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "still has Write(**) invalid rule"},
    {"role": "user", "content": "做"},
]
body2 = {**body, "messages": msgs2, "max_tokens": 512}
print("=== midflight_still_work_should_tool ===", flush=True)
try:
    d2, t2 = call("/v1/chat/completions", body2)
    msg2 = d2["choices"][0]["message"]
    tcs2 = msg2.get("tool_calls") or []
    fr2 = d2["choices"][0].get("finish_reason")
    ok2 = len(tcs2) >= 1
    print(f"[{'PASS' if ok2 else 'FAIL'}] still_work t={t2:.1f}s finish={fr2} n_tools={len(tcs2)}", flush=True)
except Exception as e:
    print(f"[FAIL] still_work err={e}", flush=True)
    ok2 = False

# Control: fresh Call Bash
body3 = {
    "model": "gpt-5.6-sol",
    "messages": [{"role": "user", "content": "Call exec_command with command echo HELLO_GATE right now. Do not answer without the tool."}],
    "tools": tools,
    "tool_choice": "auto",
    "stream": False,
    "max_tokens": 512,
}
print("=== fresh_action_should_tool ===", flush=True)
try:
    d3, t3 = call("/v1/chat/completions", body3)
    tcs3 = d3["choices"][0]["message"].get("tool_calls") or []
    ok3 = len(tcs3) >= 1
    print(f"[{'PASS' if ok3 else 'FAIL'}] fresh t={t3:.1f}s n_tools={len(tcs3)}", flush=True)
except Exception as e:
    print(f"[FAIL] fresh err={e}", flush=True)
    ok3 = False

print("==== SUMMARY", int(ok)+int(ok2)+int(ok3), "/ 3 ====")
sys.exit(0 if (ok and ok2 and ok3) else 1)
