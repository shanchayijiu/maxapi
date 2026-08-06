#!/usr/bin/env python
# maxapi:8080 原生 /v1/messages 链路自检 (中性项目)
# 用法: python sanity_check.py
# 判据: 链路 OK 必须 7 项全 PASS; 任何 FAIL 先区分是 maxapi bug 还是上游不可用。
# 跑前确保容器在跑: docker ps --filter name=maxapi
import json, http.client, sys, time

BASE_HOST, BASE_PORT = "127.0.0.1", 8080
MODEL = "Claude Sonnet 5"
H = {"Content-Type": "application/json", "x-api-key": "sk-test", "anthropic-version": "2023-06-01"}

results = []
def ok(k, cond, note=""):
    results.append((k, bool(cond), note))
    print(("[%s] %s :: %s" % ("PASS" if cond else "FAIL", k, note))[:140])

def post(path, body, stream=False, timeout=90):
    c = http.client.HTTPConnection(BASE_HOST, BASE_PORT, timeout=timeout)
    c.request("POST", path, json.dumps(body), H)
    return c.getresponse()

def collect_sse(resp):
    buf, events = b"", []
    while True:
        ch = resp.read1(4096)
        if not ch: break
        buf += ch
        while b"\n\n" in buf:
            ev, buf = buf.split(b"\n\n", 1)
            s = ev.decode("utf-8", "ignore")
            et = s.split("event: ",1)[1].split("\n",1)[0] if "event: " in s else "?"
            ds = ""
            if "data: " in s:
                try: ds = json.loads(s.split("data: ",1)[1].strip())
                except: pass
            events.append((et, ds))
    return events

# 1. healthz
try:
    r = post("/healthz", {}) if False else http.client.HTTPConnection(BASE_HOST,BASE_PORT,timeout=10)
    import urllib.request
    h = urllib.request.urlopen("http://%s:%d/healthz"%(BASE_HOST,BASE_PORT), timeout=10).read()
    ok("healthz", json.loads(h).get("status")=="ok", json.loads(h))
except Exception as e:
    ok("healthz", False, str(e)[:120])

# 2. /v1/models
try:
    h = urllib.request.urlopen("http://%s:%d/v1/models"%(BASE_HOST,BASE_PORT), timeout=10).read()
    m = json.loads(h); ok("models_list", len(m.get("data",[]))>0, "n=%d"%len(m.get("data",[])))
except Exception as e:
    ok("models_list", False, str(e)[:120])

# 3. 非流式 text
try:
    r = post("/v1/messages", {"model":MODEL,"max_tokens":128,"messages":[{"role":"user","content":"只回 ok 两字符"}]})
    d = json.loads(r.read())
    t = "".join(b.get("text","") for b in d.get("content",[]) if b.get("type")=="text")
    ok("nonstream_text", d.get("stop_reason")=="end_turn" and len(t)>0, "stop=%s t=%r"%(d.get("stop_reason"),t[:30]))
except Exception as e:
    ok("nonstream_text", False, str(e)[:120])

# 4. 流式 text 完整收尾
try:
    r = post("/v1/messages", {"model":MODEL,"max_tokens":128,"stream":True,"messages":[{"role":"user","content":"数1到3逗号分隔"}]})
    evs = collect_sse(r)
    types = [e for e,_ in evs]
    has_tail = "message_delta" in types and "message_stop" in types
    ok("stream_text_tail", has_tail and "content_block_delta" in types, "n=%d tail=%s"%(len(types),has_tail))
except Exception as e:
    ok("stream_text_tail", False, str(e)[:120])

# 5. 流式 tool_use 完整收尾 (关键: classifier 需要 opus, 上游不可用时会 FAIL)
try:
    r = post("/v1/messages", {"model":MODEL,"max_tokens":256,"stream":True,
        "tools":[{"name":"calc","description":"算","input_schema":{"type":"object","properties":{"x":{"type":"number"}},"required":["x"]}}],
        "tool_choice":{"type":"any"},"messages":[{"role":"user","content":"必须用calc工具算42"}]})
    evs = collect_sse(r)
    types=[e for e,_ in evs]
    has_tu = any((e=="content_block_start" and d.get("content_block",{}).get("type")=="tool_use") for e,d in evs)
    has_tail = "message_stop" in types
    ok("stream_tooluse_tail", has_tu and has_tail, "tooluse=%s tail=%s n=%d"%(has_tu,has_tail,len(types)))
except Exception as e:
    ok("stream_tooluse_tail", False, str(e)[:120])

# 6. 工具往返两轮 (stop_reason=tool_use -> end_turn)
try:
    r1 = post("/v1/messages", {"model":MODEL,"max_tokens":256,
        "tools":[{"name":"get_weather","description":"天气","input_schema":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}],
        "messages":[{"role":"user","content":"北京天气?必须调 get_weather"}]})
    d1 = json.loads(r1.read())
    tu = next((b for b in d1.get("content",[]) if b.get("type")=="tool_use"), None)
    if tu:
        r2 = post("/v1/messages", {"model":MODEL,"max_tokens":256,"tools":[{"name":"get_weather","description":"天气","input_schema":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}],
            "messages":[{"role":"user","content":"北京天气?必须调 get_weather"},{"role":"assistant","content":d1["content"]},
                        {"role":"user","content":[{"type":"tool_result","tool_use_id":tu["id"],"content":"晴 -3C"}]}]})
        d2 = json.loads(r2.read())
        ok("tool_roundtrip", d1.get("stop_reason")=="tool_use" and d2.get("stop_reason")=="end_turn",
           "r1=%s r2=%s input=%s"%(d1.get("stop_reason"),d2.get("stop_reason"),tu.get("input")))
    else:
        ok("tool_roundtrip", False, "no tool_use, stop=%s blocks=%s"%(d1.get("stop_reason"),[b.get("type") for b in d1.get("content",[])]))
except Exception as e:
    ok("tool_roundtrip", False, str(e)[:120])

# 7. 长上下文 needle
try:
    needle="PURPLE-DRAGON-7841"
    filler="中性填充文本撑开上下文。"*150
    r = post("/v1/messages", {"model":MODEL,"max_tokens":128,"messages":[{"role":"user","content":filler+"\n\n"+needle+"\n\n上面插入的代号是什么?只回代号"}]})
    d = json.loads(r.read())
    t = "".join(b.get("text","") for b in d.get("content",[]) if b.get("type")=="text")
    ok("long_context", needle in t, "out=%r"%t[:60])
except Exception as e:
    ok("long_context", False, str(e)[:120])

print("\n=== %d/%d PASS ==="%(sum(1 for _,c,_ in results if c), len(results)))
# 判据: 7/7 = 链路完全OK. 若 stream_tooluse_tail 或 tool_roundtrip 偶发 FAIL 而其它全 PASS,
# 多半是上游 opus 间不可用 (classifier 需要 opus), 不是 maxapi bug. 重试确认: 跑 2-3 次
# 看是否稳定复现. stream_text_tail / nonstream_text 稳定 PASS 即说明 SSE 结构本身正确.
sys.exit(0 if all(c for _,c,_ in results) else 1)
