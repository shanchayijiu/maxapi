# -*- coding: utf-8 -*-
"""Live CC-like latency/leak/tool probe against 8080 after v5 patch."""
import json, re, sys, time, urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:8080"
KEY = "sk-maxapi"
MODEL = "gpt-5.6-sol"

LEAK_RE = re.compile(
    r"(?i)(?:\|DSML\||</?\|?DSML\||tool_calls\s*\||```json\s*action|<invoke\b|<parameter\b|<function=)",
)

def post(path, body, stream=False, timeout=180):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        },
        method="POST",
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if not stream:
            raw = resp.read()
            dt = time.monotonic() - t0
            return dt, json.loads(raw.decode())
        # SSE
        content, reasoning, tools = [], [], []
        ttfb = None
        buf = b""
        while True:
            chunk = resp.read(256)
            if not chunk:
                break
            if ttfb is None:
                ttfb = time.monotonic() - t0
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    continue
                try:
                    ev = json.loads(payload)
                except Exception:
                    continue
                for ch in (ev.get("choices") or []):
                    d = ch.get("delta") or {}
                    if d.get("content"):
                        content.append(d["content"])
                    if d.get("reasoning_content"):
                        reasoning.append(d["reasoning_content"])
                    for tc in d.get("tool_calls") or []:
                        tools.append(tc)
        dt = time.monotonic() - t0
        return dt, {
            "ttfb": ttfb,
            "content": "".join(content),
            "reasoning": "".join(reasoning),
            "tool_deltas": tools,
        }

def merge_tools(deltas):
    by = {}
    for d in deltas:
        idx = d.get("index", 0)
        slot = by.setdefault(idx, {"id": None, "name": None, "arguments": ""})
        if d.get("id"):
            slot["id"] = d["id"]
        fn = d.get("function") or {}
        if fn.get("name"):
            slot["name"] = fn["name"]
        if fn.get("arguments"):
            slot["arguments"] += fn["arguments"]
    out = []
    for idx in sorted(by):
        s = by[idx]
        args = s["arguments"]
        try:
            args = json.loads(args) if args else {}
        except Exception:
            pass
        out.append({"name": s["name"], "arguments": args, "id": s["id"]})
    return out

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "Bash",
            "description": "Run a shell command",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    }
]

cases = []

# 1) action stream — must get tool first-shot, no long wait
body = {
    "model": MODEL,
    "stream": True,
    "messages": [{"role": "user", "content": "Run the shell command: echo MAXAPI_V5_OK"}],
    "tools": TOOLS,
    "tool_choice": "auto",
    "reasoning_effort": "low",
}
try:
    dt, r = post("/v1/chat/completions", body, stream=True)
    tcs = merge_tools(r["tool_deltas"])
    leaks = LEAK_RE.findall(r["content"] + "\n" + r["reasoning"])
    cases.append({
        "name": "action_stream",
        "ok": bool(tcs) and dt < 60 and not leaks,
        "dt": round(dt, 2),
        "ttfb": None if r["ttfb"] is None else round(r["ttfb"], 2),
        "tools": tcs,
        "leaks": leaks,
        "content_preview": r["content"][:120],
    })
except Exception as e:
    cases.append({"name": "action_stream", "ok": False, "err": str(e)})

# 2) action nonstream
body2 = dict(body)
body2["stream"] = False
try:
    dt, r = post("/v1/chat/completions", body2, stream=False)
    msg = (r.get("choices") or [{}])[0].get("message") or {}
    tcs = msg.get("tool_calls") or []
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    leaks = LEAK_RE.findall(str(content) + "\n" + str(reasoning))
    names = []
    for tc in tcs:
        fn = tc.get("function") or {}
        names.append(fn.get("name"))
    cases.append({
        "name": "action_nonstream",
        "ok": bool(tcs) and dt < 60 and not leaks,
        "dt": round(dt, 2),
        "tool_names": names,
        "leaks": leaks,
        "content_preview": str(content)[:120],
    })
except Exception as e:
    cases.append({"name": "action_nonstream", "ok": False, "err": str(e)})

# 3) chatty no false tool
body3 = {
    "model": MODEL,
    "stream": False,
    "messages": [{"role": "user", "content": "用一句话解释什么是 TCP，不要执行任何命令。"}],
    "tools": TOOLS,
    "tool_choice": "auto",
    "reasoning_effort": "low",
}
try:
    dt, r = post("/v1/chat/completions", body3, stream=False)
    msg = (r.get("choices") or [{}])[0].get("message") or {}
    tcs = msg.get("tool_calls") or []
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    leaks = LEAK_RE.findall(str(content) + "\n" + str(reasoning))
    cases.append({
        "name": "chatty_no_tool",
        "ok": (not tcs) and bool(content) and dt < 90 and not leaks,
        "dt": round(dt, 2),
        "tools": len(tcs),
        "leaks": leaks,
        "content_preview": str(content)[:160],
    })
except Exception as e:
    cases.append({"name": "chatty_no_tool", "ok": False, "err": str(e)})

# 4) required force
body4 = {
    "model": MODEL,
    "stream": False,
    "messages": [{"role": "user", "content": "请帮忙"}],
    "tools": TOOLS,
    "tool_choice": "required",
    "reasoning_effort": "low",
}
try:
    dt, r = post("/v1/chat/completions", body4, stream=False)
    msg = (r.get("choices") or [{}])[0].get("message") or {}
    tcs = msg.get("tool_calls") or []
    content = msg.get("content") or ""
    leaks = LEAK_RE.findall(str(content))
    cases.append({
        "name": "required_force",
        "ok": bool(tcs) and dt < 60 and not leaks,
        "dt": round(dt, 2),
        "tools": [(tc.get("function") or {}).get("name") for tc in tcs],
        "leaks": leaks,
    })
except Exception as e:
    cases.append({"name": "required_force", "ok": False, "err": str(e)})

out = {
    "sha": None,
    "model": MODEL,
    "cases": cases,
    "pass": all(c.get("ok") for c in cases),
}
try:
    with urllib.request.urlopen(BASE + "/healthz", timeout=5) as resp:
        h = json.loads(resp.read().decode())
        out["sha"] = h.get("binarySha256")
        out["sanitizer"] = h.get("sanitizerConfigVersion")
except Exception:
    pass

path = "_runtime/v4_evidence/goal_cc_v5_20260821.json"
with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))
print("WROTE", path, "PASS" if out["pass"] else "FAIL")
sys.exit(0 if out["pass"] else 1)
