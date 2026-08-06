#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DSML toolcall 移植单测 (移植自 ds2api, 69 例)。

跑法:
    cd <repo>/outputs
    python tests/test_dsml.py

判据: 69/69 ALL PASSED。覆盖非流式提取 / CDATA 路径 / 围栏包裹 /
多调用 / legacy JSON / prompt 构建 / 消息构造 / 历史渲染 / 流式
跨 SSE chunk 合并 / 逐字符喂入 / 内容无损。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import maxapi_server as M

passed = 0
failed = 0
def test(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL: %s" % name)

LT = chr(0x3c)  # <
d = M._DSML  # |DSML|

# DSML sample: fully formed tool call
dsml1 = (LT + d + "tool_calls>" + chr(10) +
        "  " + LT + d + 'invoke name="get_weather">' + chr(10) +
        "    " + LT + d + 'parameter name="city"><![CDATA[Tokyo]]></' + d + "parameter>" + chr(10) +
        "  </" + d + "invoke>" + chr(10) +
        "</" + d + "tool_calls>")

print("dsml1 preview:", repr(dsml1[:80]))

# Test 1: _dsml_extract_calls
calls1 = M._dsml_extract_calls(dsml1)
test("extract: single call", len(calls1) == 1)
test("extract: name", len(calls1) == 1 and calls1[0]["name"] == "get_weather")
test("extract: arg city", len(calls1) == 1 and calls1[0]["arguments"].get("city") == "Tokyo")

# Test 2: no tool calls
calls4 = M._dsml_extract_calls("No tools here.")
test("extract: no calls", calls4 == [])

# Test 3: special chars in CDATA
dsml5 = (LT + d + "tool_calls>\n  " + LT + d + 'invoke name="Read">\n    ' +
        LT + d + 'parameter name="file_path"><![CDATA[C:\\Users\\test\\file.txt]]></' +
        d + "parameter>\n  </" + d + "invoke>\n</" + d + "tool_calls>")
calls5 = M._dsml_extract_calls(dsml5)
test("cdargs: path", len(calls5) == 1 and calls5[0]["arguments"].get("file_path") == "C:\\Users\\test\\file.txt")

# Test 4: fence stripping
dsml7 = "```xml\n" + dsml1 + "\n```"
calls7 = M._dsml_extract_calls(dsml7)
test("fence stripped", len(calls7) == 1 and calls7[0]["name"] == "get_weather")

# Test 5: two tools
dsml2 = (LT + d + "tool_calls>\n" +
        "  " + LT + d + 'invoke name="calc">\n    ' +
        LT + d + 'parameter name="x"><![CDATA[5]]></' + d + "parameter>\n  </" + d + "invoke>\n" +
        "  " + LT + d + 'invoke name="search">\n    ' +
        LT + d + 'parameter name="q"><![CDATA[hello]]></' + d + "parameter>\n  </" + d + "invoke>\n" +
        "</" + d + "tool_calls>")
calls2 = M._dsml_extract_calls(dsml2)
test("extract: two calls", len(calls2) == 2)
test("extract: first name", len(calls2) >= 1 and calls2[0]["name"] == "calc")
test("extract: second name", len(calls2) >= 2 and calls2[1]["name"] == "search")

# Test 6: prompt
tools = [{"type": "function", "function": {"name": "get_weather", "description": "Get weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}]
prompt = M._make_tools_prompt(tools, "auto")
test("prompt not None", prompt is not None)
test("prompt has DSML", "DSML" in prompt)
test("prompt has tool name", "get_weather" in prompt)
test("prompt has rules", "RULES:" in prompt)
test("prompt has CDATA", "CDATA" in prompt)
test("prompt opening tag has LT", (LT + d + "tool_calls>") in prompt)
test("empty tools None", M._make_tools_prompt([], "auto") is None)
test("none choice None", M._make_tools_prompt(tools, "none") is None)
test("required has MUST", "MUST" in M._make_tools_prompt(tools, "required"))

# Test 7: build messages
msgs = [{"role": "user", "content": "Weather?"}]
out, enabled = M._build_messages_with_tools(tools, "auto", msgs)
test("build enabled", enabled == True)
test("build has system", any(m.get("role") == "system" and "DSML" in m.get("content", "") for m in out))
test("build user present", any(m.get("role") == "user" and m.get("content") == "Weather?" for m in out))

# Test 8: history rendering
msgs2 = [{"role": "user", "content": "Weather?"},
         {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "function": {"name": "get_weather", "arguments": '{"city": "Tokyo"}'}}]},
         {"role": "tool", "tool_call_id": "c1", "content": "Sunny 25C"}]
out2, en2 = M._build_messages_with_tools(tools, "auto", msgs2)
test("hist enabled", en2 == True)
asst_msg = [m for m in out2 if m.get("role") == "assistant"]
test("hist asst exists", len(asst_msg) == 1)
test("hist asst DSML", len(asst_msg) == 1 and "DSML" in asst_msg[0].get("content", ""))
tool_obs = [m for m in out2 if m.get("role") == "user" and "Observation" in m.get("content", "")]
test("hist tool obs", len(tool_obs) == 1)

# Test 9: streaming - one chunk
tp = M.ToolCallParser()
results = tp.feed("Hello.\n" + dsml1)
results += tp.flush()
tcs = [r for r in results if r[0] == "tool_call"]
cts = [r for r in results if r[0] == "content"]
test("stream: content before", any("Hello" in r[1] for r in cts))
test("stream: tool extracted", len(tcs) == 1)
test("stream: name", len(tcs) == 1 and tcs[0][1]["name"] == "get_weather")
test("stream: arg", len(tcs) == 1 and tcs[0][1]["arguments"].get("city") == "Tokyo")

# Test 10: streaming - split
tp2 = M.ToolCallParser()
ar = []
mid = len(dsml1) // 2
ar += tp2.feed("text8 " + dsml1[:mid])
ar += tp2.feed(dsml1[mid:])
ar += tp2.flush()
tcs2 = [r for r in ar if r[0] == "tool_call"]
test("split: tool extracted", len(tcs2) == 1)
if tcs2:
    test("split: name", tcs2[0][1]["name"] == "get_weather")
    test("split: arg", tcs2[0][1]["arguments"].get("city") == "Tokyo")

# Test 11: streaming - char by char
tp3 = M.ToolCallParser()
a3 = []
t3 = "prose. " + dsml1 + " trail."
for ch in t3:
    a3 += tp3.feed(ch)
a3 += tp3.flush()
tcs3 = [r for r in a3 if r[0] == "tool_call"]
test("char: tool call", len(tcs3) == 1)
if tcs3:
    test("char: name", tcs3[0][1]["name"] == "get_weather")
    test("char: arg", tcs3[0][1]["arguments"].get("city") == "Tokyo")

# Test 12: no tool - just text
tp4 = M.ToolCallParser()
r4 = tp4.feed("No tools here.")
r4 += tp4.flush()
tcs4 = [r for r in r4 if r[0] == "tool_call"]
cts4 = [r for r in r4 if r[0] == "content"]
test("no tool: 0 calls", len(tcs4) == 0)
test("no tool: content intact", "".join(r[1] for r in cts4) == "No tools here.")

# Test 13: content integrity (no data loss during splitting)
tp5 = M.ToolCallParser()
a5 = []
big_text = "Some random text with emoji test and chinese chars here. Then later: " + dsml1 + " final text with more content here."
for ch in big_text:
    a5 += tp5.feed(ch)
a5 += tp5.flush()
all_content = "".join(r[1] for r in a5 if r[0] == "content")
all_tools = [r for r in a5 if r[0] == "tool_call"]
test("integrity: tool extracted", len(all_tools) == 1)
# Check that content before and after the tool is preserved
test("integrity: content preserved", "Some random text" in all_content or "random text" in all_content)

# ---- Test 14: se.zzmax upstream <function=NAME> injection format ----
# The format the upstream model emits when it ignores our DSML prompt:
# <function=NAME>...<parameter=KEY>RAW VALUE</parameter>...</function> (no tool_calls wrapper, raw text, no CDATA).
fn1 = ("<function=Write>\n"
        "<parameter=file_path>C:\\tmp\\marker.txt</parameter>\n"
        "<parameter=content>print('hi <b> bold </b> and > a')</parameter>\n"
        "</function>")
# non-stream extract
cf1 = M._dsml_extract_calls(fn1)
test("fn: single call", len(cf1) == 1)
test("fn: name", len(cf1) == 1 and cf1[0]["name"] == "Write")
test("fn: file_path", len(cf1) == 1 and cf1[0]["arguments"].get("file_path") == "C:\\tmp\\marker.txt")
test("fn: content raw preserved (no XML parse)", len(cf1) == 1 and cf1[0]["arguments"].get("content") == "print('hi <b> bold </b> and > a')")

# multiple consecutive FN blocks (as seen in the 15-min agentic run)
fn2 = ("<function=Write>\n<parameter=file_path>a.py</parameter>\n<parameter=content>A</parameter>\n</function>\n"
        "<function=Write>\n<parameter=file_path>b.py</parameter>\n<parameter=content>B</parameter>\n</function>\n"
        "<function=Bash>\n<parameter=command>python a.py && python b.py</parameter>\n</function>")
cf2 = M._dsml_extract_calls(fn2)
test("fn: three calls", len(cf2) == 3)
test("fn: first name Write", len(cf2) >= 1 and cf2[0]["name"] == "Write")
test("fn: third name Bash", len(cf2) >= 3 and cf2[2]["name"] == "Bash")
test("fn: bash command preserved", len(cf2) >= 3 and cf2[2]["arguments"].get("command") == "python a.py && python b.py")

# fence-wrapped FN
cf3 = M._dsml_extract_calls("```xml\n" + fn1 + "\n```")
test("fn: fence stripped", len(cf3) == 1 and cf3[0]["name"] == "Write")

# streaming FN - one chunk
tp6 = M.ToolCallParser()
r6 = tp6.feed("prose then " + fn1) + tp6.flush()
tc6 = [r for r in r6 if r[0] == "tool_call"]
test("fn stream: tool extracted", len(tc6) == 1)
test("fn stream: name", len(tc6) == 1 and tc6[0][1]["name"] == "Write")
test("fn stream: content preserved", len(tc6) == 1 and tc6[0][1]["arguments"].get("content") == "print('hi <b> bold </b> and > a')")
test("fn stream: prose before", any("prose then" in r[1] for r in r6 if r[0] == "content"))

# streaming FN - split mid-block (cross SSE chunk)
tp7 = M.ToolCallParser()
mid7 = len(fn1)//2
r7 = tp7.feed("x " + fn1[:mid7]) + tp7.feed(fn1[mid7:]) + tp7.flush()
tc7 = [r for r in r7 if r[0] == "tool_call"]
test("fn split: tool extracted", len(tc7) == 1)
test("fn split: name", len(tc7) == 1 and tc7[0][1]["name"] == "Write")
test("fn split: name Write", len(tc7) == 1 and tc7[0][1]["arguments"].get("file_path") == "C:\\tmp\\marker.txt")

# streaming FN - char by char
tp8 = M.ToolCallParser()
r8 = []
for ch in ("p. " + fn1 + " tail."):
    r8 += tp8.feed(ch)
r8 += tp8.flush()
tc8 = [r for r in r8 if r[0] == "tool_call"]
test("fn char: tool extracted", len(tc8) == 1)
test("fn char: name", len(tc8) == 1 and tc8[0][1]["name"] == "Write")
test("fn char: content preserved", len(tc8) == 1 and tc8[0][1]["arguments"].get("content") == "print('hi <b> bold </b> and > a')")
_c8 = "".join(r[1] for r in r8 if r[0] == "content")
test("fn char: trail content", "tail" in _c8)

# incomplete FN flushed -> emitted as content (not lost), no false tool
tp9 = M.ToolCallParser()
r9 = tp9.feed("<function=Write>\n<parameter=file_path>C:") + tp9.flush()
tc9 = [r for r in r9 if r[0] == "tool_call"]
ct9 = "".join(r[1] for r in r9 if r[0] == "content")
test("fn incomplete: no tool", len(tc9) == 0)
test("fn incomplete: content not lost", "<function=Write>" in ct9)

# FN with no params -> empty arguments
cf0 = M._dsml_extract_calls("<function=Ping>\n</function>")
test("fn empty: empty args", len(cf0) == 1 and cf0[0]["name"] == "Ping" and cf0[0]["arguments"] == {})

# ---- Test 15: streaming multi-invoke DSML block must NOT leak closing tags ----
# Regression for the 15-min run: a single <tool_calls> with 2 invokes leaked
# '</|DSML|invoke></|DSML|tool_calls>' as content because prefix/suffix were sliced in
# captured (raw) coords but indexed via normalized (shrunk) coords.
dsml_multi = (LT+d+"tool_calls>\n  "+LT+d+'invoke name="Write">\n    '+LT+d+'parameter name="content"><![CDATA[A]]></'+d+'parameter>\n  </'+d+'invoke>\n  '+LT+d+'invoke name="Write">\n    '+LT+d+'parameter name="content"><![CDATA[B]]></'+d+'parameter>\n  </'+d+'invoke>\n</'+d+'tool_calls>')
tpm = M.ToolCallParser()
rm = tpm.feed("intro then " + dsml_multi + " outro") + tpm.flush()
tcm = [r for r in rm if r[0] == "tool_call"]
ctm = "".join(r[1] for r in rm if r[0] == "content")
test("multi-stream: two calls", len(tcm) == 2)
test("multi-stream: no invoke-close leak", ("</"+d+"invoke>") not in ctm)
test("multi-stream: no tool_calls-close leak", ("</"+d+"tool_calls>") not in ctm)
test("multi-stream: prefix kept", "intro then" in ctm)
test("multi-stream: suffix kept", "outro" in ctm)

# char-by-char variant
tpm2 = M.ToolCallParser()
rm2 = []
for ch in ("x " + dsml_multi + " y"):
    rm2 += tpm2.feed(ch)
rm2 += tpm2.flush()
tcm2 = [r for r in rm2 if r[0] == "tool_call"]
ctm2 = "".join(r[1] for r in rm2 if r[0] == "content")
test("multi-char: two calls", len(tcm2) == 2)
test("multi-char: no closing-tag leak", ("</"+d+"invoke>") not in ctm2 and ("</"+d+"tool_calls>") not in ctm2)

print()
print("=" * 40)
print("RESULTS: %d passed, %d failed" % (passed, failed))
if failed:
    sys.exit(1)
print("ALL PASSED")
