# -*- coding: utf-8 -*-
"""Long multi-turn agent chain against 8080 gpt-5.6-sol (~15 tool rounds)."""
import json
import time
import urllib.request
import sys

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8080"
OUT = r"C:\Users\Administrator\Desktop\maxapi\_accept_agent_long.json"
LOG = r"C:\Users\Administrator\Desktop\maxapi\_accept_agent_long.log"
ROUNDS = 15
MODEL = "gpt-5.6-sol"

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
H = {"x-api-key": "sk-test", "anthropic-version": "2023-06-01", "Content-Type": "application/json"}


def call(body, timeout=180, retries=3):
    data = json.dumps(body, ensure_ascii=False).encode()
    t0 = time.time()
    last_err = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(BASE + "/v1/messages", data=data, headers=H)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return True, json.loads(r.read().decode("utf-8", "ignore")), time.time() - t0, None
        except Exception as e:
            last_err = str(e)
            if any(k in last_err for k in ("529", "502", "503", "timed out", "timeout")) and attempt < retries:
                time.sleep(1.2 * attempt + 0.5)
                continue
            return False, None, time.time() - t0, last_err
    return False, None, time.time() - t0, last_err


def main():
    results = []
    messages = []
    t_total0 = time.time()

    def rec(name, ok, detail, t):
        results.append({"name": name, "ok": bool(ok), "detail": str(detail), "t": round(t, 2)})
        print(f"[{'PASS' if ok else 'FAIL'}] {name} t={t:.1f}s :: {detail}", flush=True)

    for i in range(ROUNDS):
        token = f"STEP_{i}"
        user_text = f"Call Bash with command echo {token} right now. Do not answer without the tool."
        messages.append({"role": "user", "content": user_text})
        ok, d, t, e = call(
            {
                "model": MODEL,
                "max_tokens": 512,
                "tools": tools,
                "messages": messages,
                "stream": False,
            }
        )
        if not ok:
            rec(f"round_{i}_tool", False, e, t)
            break
        blocks = d.get("content") or []
        tus = [b for b in blocks if b.get("type") == "tool_use"]
        has = any(b.get("name") == "Bash" for b in tus)
        tu = next((b for b in tus if b.get("name") == "Bash"), None)
        cmd = (tu or {}).get("input") or {}
        cmd_ok = has and token in str(cmd.get("command", ""))
        rec(
            f"round_{i}_tool",
            cmd_ok,
            f"stop={d.get('stop_reason')} n_tools={len(tus)} input={(tu or {}).get('input')}",
            t,
        )
        if not has:
            break
        # append assistant turn + synthetic tool_result
        messages.append({"role": "assistant", "content": blocks})
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": f"{token}\n",
                    }
                ],
            }
        )
        time.sleep(0.3)

    # final no-tool turn
    messages.append(
        {
            "role": "user",
            "content": "All steps done. Reply with exactly ALL_DONE and do not call tools.",
        }
    )
    ok, d, t, e = call(
        {
            "model": MODEL,
            "max_tokens": 256,
            "tools": tools,
            "messages": messages,
            "stream": False,
        }
    )
    if ok:
        types = [b.get("type") for b in d.get("content") or []]
        text = "".join(b.get("text", "") for b in d.get("content") or [] if b.get("type") == "text")
        rec(
            "final_no_tool",
            "ALL_DONE" in text.upper() and "tool_use" not in types,
            f"types={types} text={text[:100]} stop={d.get('stop_reason')}",
            t,
        )
    else:
        rec("final_no_tool", False, e, t)

    tool_rounds = [r for r in results if r["name"].startswith("round_")]
    tool_ok = sum(1 for r in tool_rounds if r["ok"])
    rate_ok = tool_ok >= ROUNDS and all(r["ok"] for r in results)
    rec(
        "agent_long_summary",
        rate_ok,
        f"tool_rounds={tool_ok}/{ROUNDS} total_cases={sum(1 for r in results if r['ok'])}/{len(results)} wall={time.time()-t_total0:.1f}s",
        time.time() - t_total0,
    )
    print(f"==== TOTAL {sum(1 for r in results if r['ok'])} / {len(results)} ====", flush=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
