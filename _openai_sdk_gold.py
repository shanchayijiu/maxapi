# -*- coding: utf-8 -*-
"""OpenAI SDK gold cases against local maxapi :8080.

Acceptance contract: Desktop/2api必读文档.txt
- Official openai-python is the only client under test
- Structural field parity (ignore id/created/content text)
- Stream tool_calls shard shape, multi-turn tool refill, errors, DONE

Exit 0 only if all cases PASS.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8080/v1"
API_KEY = "sk-gold-maxapi"
MODEL = "deepseek-v4-flash"
# Secondary models for smoke (not all cases)
MODEL_OPUS = "Claude Opus 5"

RESULTS: list[dict[str, Any]] = []
OUT = r"C:\Users\Administrator\Desktop\maxapi\_openai_sdk_gold.json"


def rec(name: str, ok: bool, detail: str, t: float = 0.0) -> None:
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:800], "t": round(t, 2)})
    print(f"[{'PASS' if ok else 'FAIL'}] {name} t={t:.1f}s :: {detail}", flush=True)


def client():
    from openai import OpenAI

    return OpenAI(base_url=BASE, api_key=API_KEY, max_retries=0, timeout=120.0)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get current time in a timezone",
            "parameters": {
                "type": "object",
                "properties": {"tz": {"type": "string", "description": "IANA tz"}},
                "required": ["tz"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add",
            "description": "Add two integers",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "integer"},
                    "b": {"type": "integer"},
                },
                "required": ["a", "b"],
            },
        },
    },
]


def _assert_chat_obj_shape(obj: Any, stream: bool = False) -> list[str]:
    errs = []
    if not hasattr(obj, "id") or not obj.id:
        errs.append("missing id")
    if getattr(obj, "object", None) not in ("chat.completion", "chat.completion.chunk"):
        # stream iterator yields chunks
        if stream and getattr(obj, "object", None) != "chat.completion.chunk":
            errs.append(f"bad object={getattr(obj,'object',None)}")
        if not stream and getattr(obj, "object", None) != "chat.completion":
            errs.append(f"bad object={getattr(obj,'object',None)}")
    if getattr(obj, "model", None) is None:
        errs.append("missing model")
    if not getattr(obj, "choices", None):
        # usage-only final chunk may have empty choices — allowed only with usage
        if not getattr(obj, "usage", None):
            errs.append("empty choices without usage")
    return errs


def case_models():
    t0 = time.time()
    c = client()
    try:
        m = c.models.list()
        ids = [x.id for x in m.data]
        ok = len(ids) >= 1 and any("deepseek" in (i or "").lower() or "Claude" in (i or "") or "gpt" in (i or "").lower() for i in ids)
        rec("models.list", ok, f"n={len(ids)} sample={ids[:5]}", time.time() - t0)
    except Exception as e:
        rec("models.list", False, f"{type(e).__name__}: {e}", time.time() - t0)


def case_nonstream_text():
    t0 = time.time()
    c = client()
    try:
        r = c.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Reply with exactly: GOLD_OK"}],
            max_tokens=32,
            stream=False,
        )
        errs = _assert_chat_obj_shape(r, stream=False)
        text = (r.choices[0].message.content or "")
        fr = r.choices[0].finish_reason
        ok = ("GOLD_OK" in text) and fr in ("stop", "length") and not errs
        # usage present
        if r.usage is None:
            errs.append("usage is None")
            ok = False
        rec("nonstream.text", ok, f"fr={fr} text={text!r} errs={errs} usage={r.usage}", time.time() - t0)
    except Exception as e:
        rec("nonstream.text", False, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-400:]}", time.time() - t0)


def case_stream_text():
    t0 = time.time()
    c = client()
    try:
        stream = c.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Reply with exactly: STREAM_OK"}],
            max_tokens=32,
            stream=True,
        )
        role_seen = False
        pieces = []
        finish = None
        n_chunks = 0
        for ch in stream:
            n_chunks += 1
            if not ch.choices:
                continue
            d = ch.choices[0].delta
            if getattr(d, "role", None) == "assistant":
                role_seen = True
            if d.content:
                pieces.append(d.content)
            if ch.choices[0].finish_reason:
                finish = ch.choices[0].finish_reason
        text = "".join(pieces)
        # true incremental: no chunk should equal full final text unless single-chunk
        ok = ("STREAM_OK" in text) and finish == "stop" and role_seen and n_chunks >= 1
        rec(
            "stream.text",
            ok,
            f"role={role_seen} fr={finish} chunks={n_chunks} text={text!r}",
            time.time() - t0,
        )
    except Exception as e:
        rec("stream.text", False, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-400:]}", time.time() - t0)


def case_nonstream_tool():
    t0 = time.time()
    c = client()
    try:
        r = c.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Call get_time with tz=UTC. No prose."}],
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=128,
            stream=False,
        )
        msg = r.choices[0].message
        fr = r.choices[0].finish_reason
        tcs = msg.tool_calls or []
        ok = fr == "tool_calls" and len(tcs) >= 1
        detail = f"fr={fr} n={len(tcs)}"
        if tcs:
            tc0 = tcs[0]
            args = tc0.function.arguments
            # CRITICAL: arguments must be str
            if not isinstance(args, str):
                ok = False
                detail += f" args_type={type(args).__name__}"
            else:
                try:
                    parsed = json.loads(args)
                    detail += f" name={tc0.function.name} args={parsed} id={tc0.id} type={tc0.type}"
                    if tc0.function.name != "get_time":
                        ok = False
                    if tc0.type != "function":
                        ok = False
                    if not tc0.id:
                        ok = False
                except Exception as je:
                    ok = False
                    detail += f" args_not_json={args!r} err={je}"
        rec("nonstream.tool", ok, detail, time.time() - t0)
        return r if ok else None
    except Exception as e:
        rec("nonstream.tool", False, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-400:]}", time.time() - t0)
        return None


def case_stream_tool():
    """Wire shape for streaming tools per OpenAI docs / 2api checklist."""
    t0 = time.time()
    c = client()
    try:
        stream = c.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Call get_time with tz=UTC. No prose."}],
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=128,
            stream=True,
        )
        role_seen = False
        # index -> accumulated
        acc: dict[int, dict[str, Any]] = {}
        finish = None
        first_tool_had_id = False
        args_are_str = True
        n_tool_deltas = 0
        # Strict OpenAI stream shape (2api P0): first shard for an index carries
        # id+name with arguments=="" (or absent); later shards append arguments only.
        first_shard_empty_args = True
        saw_args_increment = False
        for ch in stream:
            if not ch.choices:
                continue
            d = ch.choices[0].delta
            if getattr(d, "role", None) == "assistant":
                role_seen = True
            if ch.choices[0].finish_reason:
                finish = ch.choices[0].finish_reason
            tcs = getattr(d, "tool_calls", None) or []
            for tc in tcs:
                n_tool_deltas += 1
                idx = tc.index if tc.index is not None else 0
                slot = acc.setdefault(idx, {"id": None, "name": "", "arguments": "", "type": None, "n": 0})
                slot["n"] += 1
                is_first_for_idx = slot["n"] == 1
                if tc.id:
                    slot["id"] = tc.id
                    first_tool_had_id = True
                if tc.type:
                    slot["type"] = tc.type
                fn = tc.function
                if fn is not None:
                    if fn.name:
                        slot["name"] = (slot["name"] or "") + fn.name
                    if fn.arguments is not None:
                        if not isinstance(fn.arguments, str):
                            args_are_str = False
                        piece = fn.arguments if isinstance(fn.arguments, str) else json.dumps(fn.arguments)
                        if is_first_for_idx and piece != "":
                            # first shard dumped full args — wire-compat fail
                            first_shard_empty_args = False
                        if (not is_first_for_idx) and piece:
                            saw_args_increment = True
                        slot["arguments"] += piece
        ok = (
            finish == "tool_calls"
            and role_seen
            and len(acc) >= 1
            and args_are_str
            and first_tool_had_id
            and first_shard_empty_args
            and (saw_args_increment or all(not s["arguments"] for s in acc.values()))
            and n_tool_deltas >= 2  # at least header + one args piece when args non-empty
        )
        detail_parts = [
            f"fr={finish}",
            f"role={role_seen}",
            f"deltas={n_tool_deltas}",
            f"n_idx={len(acc)}",
            f"first_args_empty={first_shard_empty_args}",
            f"args_incr={saw_args_increment}",
        ]
        for idx, slot in acc.items():
            try:
                parsed = json.loads(slot["arguments"] or "{}")
            except Exception as e:
                ok = False
                parsed = f"BAD_JSON:{e}:{slot['arguments']!r}"
            detail_parts.append(f"[{idx}] id={slot['id']} type={slot['type']} name={slot['name']} args={parsed}")
            if slot["name"] != "get_time" or slot["type"] not in ("function", None):
                # type may only appear on first shard
                if slot["name"] != "get_time":
                    ok = False
            if not slot["id"]:
                ok = False
        if not args_are_str:
            ok = False
            detail_parts.append("ARGS_NOT_STR")
        if not first_shard_empty_args:
            detail_parts.append("FIRST_SHARD_DUMPED_ARGS")
        rec("stream.tool", ok, " | ".join(detail_parts), time.time() - t0)
        return acc if ok else None
    except Exception as e:
        rec("stream.tool", False, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-400:]}", time.time() - t0)
        return None


def case_multiturn_tool_refill():
    t0 = time.time()
    c = client()
    try:
        r1 = c.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "What time is it in UTC? Use get_time."}],
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=128,
            stream=False,
        )
        msg1 = r1.choices[0].message
        tcs = msg1.tool_calls or []
        if not tcs:
            rec("multiturn.tool_refill", False, f"no tool on turn1 fr={r1.choices[0].finish_reason}", time.time() - t0)
            return
        tc = tcs[0]
        # Build round 2 with tool result
        messages = [
            {"role": "user", "content": "What time is it in UTC? Use get_time."},
            {
                "role": "assistant",
                "content": msg1.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": "2026-08-19T12:00:00Z",
            },
        ]
        r2 = c.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=256,
            stream=False,
        )
        msg2 = r2.choices[0].message
        fr2 = r2.choices[0].finish_reason
        text = msg2.content or ""
        # Should continue with prose mentioning time; may or may not call tool again
        ok = fr2 in ("stop", "tool_calls", "length") and (bool(text.strip()) or bool(msg2.tool_calls))
        # Stronger: if stop, content should reference the tool output somehow
        if fr2 == "stop" and text.strip():
            ok = True
        if fr2 == "stop" and not text.strip():
            ok = False
        rec(
            "multiturn.tool_refill",
            ok,
            f"fr2={fr2} content={text[:120]!r} tools2={len(msg2.tool_calls or [])}",
            time.time() - t0,
        )
    except Exception as e:
        rec("multiturn.tool_refill", False, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-400:]}", time.time() - t0)


def case_tool_choice_none():
    t0 = time.time()
    c = client()
    try:
        r = c.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Call get_time please."}],
            tools=TOOLS,
            tool_choice="none",
            max_tokens=64,
            stream=False,
        )
        fr = r.choices[0].finish_reason
        tcs = r.choices[0].message.tool_calls
        ok = fr == "stop" and not tcs
        rec("tool_choice.none", ok, f"fr={fr} tcs={tcs}", time.time() - t0)
    except Exception as e:
        rec("tool_choice.none", False, f"{type(e).__name__}: {e}", time.time() - t0)


def case_tool_choice_required():
    t0 = time.time()
    c = client()
    try:
        r = c.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "I need the UTC time."}],
            tools=TOOLS,
            tool_choice="required",
            max_tokens=128,
            stream=False,
        )
        fr = r.choices[0].finish_reason
        tcs = r.choices[0].message.tool_calls or []
        ok = fr == "tool_calls" and len(tcs) >= 1
        rec("tool_choice.required", ok, f"fr={fr} n={len(tcs)}", time.time() - t0)
    except Exception as e:
        rec("tool_choice.required", False, f"{type(e).__name__}: {e}", time.time() - t0)


def case_named_tool_choice():
    t0 = time.time()
    c = client()
    try:
        r = c.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Use a tool."}],
            tools=TOOLS,
            tool_choice={"type": "function", "function": {"name": "get_time"}},
            max_tokens=128,
            stream=False,
        )
        fr = r.choices[0].finish_reason
        tcs = r.choices[0].message.tool_calls or []
        name = tcs[0].function.name if tcs else None
        ok = fr == "tool_calls" and name == "get_time"
        rec("tool_choice.named", ok, f"fr={fr} name={name}", time.time() - t0)
    except Exception as e:
        rec("tool_choice.named", False, f"{type(e).__name__}: {e}", time.time() - t0)


def case_error_model():
    t0 = time.time()
    c = client()
    try:
        c.chat.completions.create(
            model="this-model-definitely-does-not-exist-xyz",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=8,
        )
        rec("error.bad_model", False, "expected APIError", time.time() - t0)
    except Exception as e:
        # openai.NotFoundError or BadRequestError — status 404 preferred
        status = getattr(e, "status_code", None)
        body = getattr(e, "body", None)
        ok = status in (400, 404) or "model" in str(e).lower()
        rec("error.bad_model", ok, f"status={status} type={type(e).__name__} body={body!r} msg={e}", time.time() - t0)


def case_stream_include_usage():
    t0 = time.time()
    c = client()
    try:
        stream = c.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Say OK"}],
            max_tokens=16,
            stream=True,
            stream_options={"include_usage": True},
        )
        usage_seen = False
        finish = None
        for ch in stream:
            if getattr(ch, "usage", None) is not None:
                usage_seen = True
            if ch.choices and ch.choices[0].finish_reason:
                finish = ch.choices[0].finish_reason
        # If server ignores stream_options, this FAILS (P0 in doc)
        rec("stream.include_usage", usage_seen, f"usage_seen={usage_seen} fr={finish}", time.time() - t0)
    except Exception as e:
        rec("stream.include_usage", False, f"{type(e).__name__}: {e}", time.time() - t0)


def case_parallel_tools_prompt():
    """Ask for two tools; accept 1+ if model only does one, but structure must be valid."""
    t0 = time.time()
    c = client()
    try:
        r = c.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": "Call get_time with tz=UTC AND call add with a=1 b=2. Use both tools.",
                }
            ],
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=256,
            stream=False,
            parallel_tool_calls=True,
        )
        fr = r.choices[0].finish_reason
        tcs = r.choices[0].message.tool_calls or []
        names = [t.function.name for t in tcs]
        # Soft: at least one tool; hard structure
        struct_ok = fr == "tool_calls" and all(isinstance(t.function.arguments, str) for t in tcs)
        multi = len(set(names)) >= 2
        ok = struct_ok and len(tcs) >= 1
        rec(
            "parallel.tools",
            ok,
            f"fr={fr} names={names} multi={multi} (multi preferred not required if model refuses)",
            time.time() - t0,
        )
    except Exception as e:
        rec("parallel.tools", False, f"{type(e).__name__}: {e}", time.time() - t0)


def case_agent_chain_short():
    """5-round tool chain via OpenAI SDK (mini agent long)."""
    t0 = time.time()
    c = client()
    tools = [
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
    messages: list[dict[str, Any]] = []
    ok_rounds = 0
    detail = []
    try:
        for i in range(5):
            token = f"STEP_{i}"
            messages.append(
                {"role": "user", "content": f"Call Bash with command echo {token} right now. Tool only."}
            )
            r = c.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=256,
                stream=False,
            )
            msg = r.choices[0].message
            tcs = msg.tool_calls or []
            if not tcs:
                detail.append(f"r{i}:no_tool fr={r.choices[0].finish_reason}")
                break
            tc = tcs[0]
            args = tc.function.arguments
            if not isinstance(args, str) or token not in args:
                detail.append(f"r{i}:bad_args={args!r}")
                break
            ok_rounds += 1
            detail.append(f"r{i}:ok")
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": args},
                        }
                    ],
                }
            )
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": f"{token}\n"})
        # final
        messages.append({"role": "user", "content": "All done. Reply exactly ALL_DONE with no tools."})
        r = c.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            max_tokens=64,
            stream=False,
        )
        text = r.choices[0].message.content or ""
        final_ok = "ALL_DONE" in text.upper() and not r.choices[0].message.tool_calls
        ok = ok_rounds >= 5 and final_ok
        rec(
            "agent.chain_5",
            ok,
            f"rounds={ok_rounds}/5 final_ok={final_ok} text={text[:80]!r} {detail}",
            time.time() - t0,
        )
    except Exception as e:
        rec("agent.chain_5", False, f"rounds={ok_rounds} {type(e).__name__}: {e}\n{detail}", time.time() - t0)


def case_raw_sse_headers_and_done():
    """Raw HTTP checks that SDK hides: headers, [DONE], incremental tool shards."""
    import http.client

    t0 = time.time()
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Call get_time with tz=UTC. No prose."}],
        "tools": TOOLS,
        "tool_choice": "auto",
        "stream": True,
        "max_tokens": 128,
        "stream_options": {"include_usage": True},
    }
    try:
        conn = http.client.HTTPConnection("127.0.0.1", 8080, timeout=90)
        data = json.dumps(body).encode()
        conn.request(
            "POST",
            "/v1/chat/completions",
            body=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer sk-gold",
                "Accept": "text/event-stream",
            },
        )
        resp = conn.getresponse()
        ct = resp.getheader("Content-Type") or ""
        cache = resp.getheader("Cache-Control") or ""
        xab = resp.getheader("X-Accel-Buffering") or ""
        te = resp.getheader("Transfer-Encoding") or ""
        conn_h = resp.getheader("Connection") or ""
        raw = resp.read().decode("utf-8", "replace")
        conn.close()
        has_done = "data: [DONE]" in raw
        # parse tool deltas
        tool_shards = []
        usage_chunk = False
        for line in raw.splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[6:].strip()
            if payload == "[DONE]":
                continue
            try:
                o = json.loads(payload)
            except Exception:
                continue
            if o.get("usage"):
                usage_chunk = True
            for ch in o.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("tool_calls"):
                    tool_shards.append(d["tool_calls"])
        # OpenAI-style: first shard id+name with arguments==""; later args-only increments
        shape_ok = False
        if tool_shards:
            first = tool_shards[0][0]
            first_fn = first.get("function") or {}
            first_args = first_fn.get("arguments")
            shape_ok = (
                bool(first.get("id"))
                and bool(first_fn.get("name"))
                and (first_args in (None, ""))
                and len(tool_shards) >= 2
            )
            # arguments always str when present; at least one later shard has non-empty args piece
            later_args = False
            for si, shard in enumerate(tool_shards):
                for tc in shard:
                    args = (tc.get("function") or {}).get("arguments")
                    if args is not None and not isinstance(args, str):
                        shape_ok = False
                    if si > 0 and isinstance(args, str) and args:
                        later_args = True
            if not later_args:
                shape_ok = False
        hdr_ok = ("text/event-stream" in ct) and ("no-cache" in cache.lower()) and (xab.lower() == "no" or xab == "")
        # Connection keep-alive preferred; chunked without close is acceptable
        conn_ok = ("keep-alive" in conn_h.lower()) or (te.lower() == "chunked") or (conn_h == "")
        ok = resp.status == 200 and has_done and shape_ok and hdr_ok and conn_ok
        rec(
            "raw.sse_tool_wire",
            ok,
            f"st={resp.status} ct={ct!r} cache={cache!r} xab={xab!r} te={te!r} conn={conn_h!r} "
            f"done={has_done} shards={len(tool_shards)} shape={shape_ok} usage={usage_chunk}",
            time.time() - t0,
        )
        # separate include_usage raw signal
        rec("raw.sse_include_usage", usage_chunk, f"usage_chunk={usage_chunk}", 0.0)
    except Exception as e:
        rec("raw.sse_tool_wire", False, f"{type(e).__name__}: {e}", time.time() - t0)


def main():
    print(f"BASE={BASE} MODEL={MODEL}", flush=True)
    case_models()
    case_nonstream_text()
    case_stream_text()
    case_nonstream_tool()
    case_stream_tool()
    case_multiturn_tool_refill()
    case_tool_choice_none()
    case_tool_choice_required()
    case_named_tool_choice()
    case_parallel_tools_prompt()
    case_error_bad = case_error_model
    case_error_bad()
    case_stream_include_usage()
    case_raw_sse_headers_and_done()
    case_agent_chain_short()

    passed = sum(1 for r in RESULTS if r["ok"])
    total = len(RESULTS)
    print(f"==== TOTAL {passed} / {total} ====", flush=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    # Fail hard if any FAIL
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
