# -*- coding: utf-8 -*-
import json
import time
import urllib.request
import sys

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8080"
OUT = r"C:\Users\Administrator\Desktop\maxapi\_accept_tool.json"
LOG = r"C:\Users\Administrator\Desktop\maxapi\_accept_run.log"


def call(path, body, headers, timeout=180, stream=False, retries=3):
    h = {"Content-Type": "application/json"}
    h.update(headers)
    data = json.dumps(body, ensure_ascii=False).encode()
    t0 = time.time()
    last_err = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(BASE + path, data=data, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if stream:
                    return True, raw.decode("utf-8", "ignore"), time.time() - t0, None
                return True, json.loads(raw.decode("utf-8", "ignore")), time.time() - t0, None
        except Exception as e:
            last_err = str(e)
            # Retry upstream busy / temporary unavailability only.
            if ("529" in last_err or "502" in last_err or "503" in last_err or "timed out" in last_err.lower()) and attempt < retries:
                time.sleep(1.2 * attempt + 0.5)
                continue
            return False, None, time.time() - t0, last_err
    return False, None, time.time() - t0, last_err


tools = [
    {
        "name": "Bash",
        "description": "Run a shell command",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    }
]
oai = [
    {
        "type": "function",
        "function": {
            "name": "Bash",
            "description": "Run shell",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    }
]
Hmsg = {"x-api-key": "sk-test", "anthropic-version": "2023-06-01"}
Hchat = {"Authorization": "Bearer sk-test"}
results = []


def rec(name, ok, detail, t):
    results.append({"name": name, "ok": bool(ok), "detail": str(detail), "t": round(t, 2)})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} t={t:.1f}s :: {detail}", flush=True)


def main():
    ok, d, t, e = call(
        "/v1/chat/completions",
        {
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "Reply exactly: PONG"}],
            "max_tokens": 16,
            "stream": False,
        },
        Hchat,
    )
    content = ""
    if ok:
        content = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    rec("no_tools_pong", ok and content.strip() == "PONG", content if ok else e, t)

    tool_ok = tool_n = tool_err = 0
    for i in range(20):
        ok, d, t, e = call(
            "/v1/messages",
            {
                "model": "gpt-5.6-sol",
                "max_tokens": 512,
                "tools": tools,
                "messages": [
                    {
                        "role": "user",
                        "content": f"Call Bash with command echo HELLO_{i} right now. Do not answer without the tool.",
                    }
                ],
                "stream": False,
            },
            Hmsg,
        )
        if not ok:
            tool_err += 1
            rec(f"auto_tool_{i}", False, e, t)
            time.sleep(0.8)
            continue
        tool_n += 1
        has = any(b.get("type") == "tool_use" for b in d.get("content") or [])
        tool_ok += 1 if has else 0
        tu = next((b for b in d.get("content") or [] if b.get("type") == "tool_use"), None)
        rec(
            f"auto_tool_{i}",
            has,
            f"stop={d.get('stop_reason')} input={(tu or {}).get('input')}",
            t,
        )
        time.sleep(0.4)
    rate_ok = tool_n > 0 and (tool_ok / max(1, tool_n)) >= 0.95 and tool_err <= 2
    rec("auto_tool_rate_20", rate_ok, f"{tool_ok}/{tool_n} err={tool_err}", 0)

    ok, d, t, e = call(
        "/v1/messages",
        {
            "model": "gpt-5.6-sol",
            "max_tokens": 256,
            "tools": tools,
            "messages": [
                {
                    "role": "user",
                    "content": "Explain what a binary tree is in one short sentence. Do not run any command.",
                }
            ],
            "stream": False,
        },
        Hmsg,
    )
    if ok:
        types = [b.get("type") for b in d.get("content") or []]
        rec(
            "plain_no_tool",
            "tool_use" not in types and any(x == "text" for x in types),
            f"types={types} stop={d.get('stop_reason')}",
            t,
        )
    else:
        rec("plain_no_tool", False, e, t)

    ok, d, t, e = call(
        "/v1/messages",
        {
            "model": "gpt-5.6-sol",
            "max_tokens": 256,
            "tools": tools,
            "messages": [
                {
                    "role": "user",
                    "content": "How do I use Bash to list files? Just explain, do not execute.",
                }
            ],
            "stream": False,
        },
        Hmsg,
    )
    if ok:
        types = [b.get("type") for b in d.get("content") or []]
        rec("howto_no_force", "tool_use" not in types, f"types={types} stop={d.get('stop_reason')}", t)
    else:
        rec("howto_no_force", False, e, t)

    ok, d, t, e = call(
        "/v1/chat/completions",
        {
            "model": "gpt-5.6-sol",
            "max_tokens": 512,
            "tools": oai,
            "messages": [
                {"role": "user", "content": "Call Bash with command echo HELLO_CHAT right now."}
            ],
            "stream": False,
        },
        Hchat,
    )
    if ok:
        msg = d["choices"][0]["message"]
        n = len(msg.get("tool_calls") or [])
        rec(
            "chat_auto_tool",
            n >= 1,
            f"finish={d['choices'][0].get('finish_reason')} n={n}",
            t,
        )
    else:
        rec("chat_auto_tool", False, e, t)

    ok, raw, t, e = call(
        "/v1/messages",
        {
            "model": "gpt-5.6-sol",
            "max_tokens": 512,
            "tools": tools,
            "messages": [
                {"role": "user", "content": "Call Bash with command echo HELLO_STREAM right now."}
            ],
            "stream": True,
        },
        Hmsg,
        stream=True,
    )
    if ok:
        rec(
            "stream_auto_tool",
            "tool_use" in raw and "input_json_delta" in raw,
            f"tool_use={'tool_use' in raw} json={'input_json_delta' in raw} bytes={len(raw)}",
            t,
        )
    else:
        rec("stream_auto_tool", False, e, t)

    tools2 = [
        {
            "name": "Bash",
            "description": "Run shell",
            "input_schema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
        {
            "name": "Read",
            "description": "Read a file",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    ]
    ok, d, t, e = call(
        "/v1/messages",
        {
            "model": "gpt-5.6-sol",
            "max_tokens": 512,
            "tools": tools2,
            "messages": [{"role": "user", "content": "Use the Bash tool to run: echo AGENT_OK"}],
            "stream": False,
        },
        Hmsg,
    )
    if ok:
        tus = [b for b in d.get("content") or [] if b.get("type") == "tool_use"]
        rec(
            "multi_tool_bash",
            any(b.get("name") == "Bash" for b in tus),
            f"tools={[b.get('name') for b in tus]} inputs={[b.get('input') for b in tus]}",
            t,
        )
    else:
        rec("multi_tool_bash", False, e, t)

    ok, d, t, e = call(
        "/v1/messages",
        {
            "model": "gpt-5.6-sol",
            "max_tokens": 512,
            "tools": tools,
            "messages": [{"role": "user", "content": "Call Bash with command echo STEP1"}],
            "stream": False,
        },
        Hmsg,
    )
    if ok and any(b.get("type") == "tool_use" for b in d.get("content") or []):
        tu = next(b for b in d.get("content") if b.get("type") == "tool_use")
        ok2, d2, t2, e2 = call(
            "/v1/messages",
            {
                "model": "gpt-5.6-sol",
                "max_tokens": 256,
                "tools": tools,
                "messages": [
                    {"role": "user", "content": "Call Bash with command echo STEP1"},
                    {"role": "assistant", "content": d.get("content")},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tu["id"],
                                "content": "STEP1\n",
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": "Now reply with exactly DONE and do not call tools.",
                    },
                ],
                "stream": False,
            },
            Hmsg,
        )
        if ok2:
            types = [b.get("type") for b in d2.get("content") or []]
            text = "".join(
                b.get("text", "") for b in d2.get("content") or [] if b.get("type") == "text"
            )
            rec(
                "agent_multiturn",
                "DONE" in text.upper() and "tool_use" not in types,
                f"types={types} text={text[:80]}",
                t + t2,
            )
        else:
            rec("agent_multiturn", False, e2, t + t2)
    else:
        rec("agent_multiturn", False, e or "no first tool", t)

    passed = sum(1 for r in results if r["ok"])
    print(f"==== TOTAL {passed} / {len(results)} ====", flush=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
