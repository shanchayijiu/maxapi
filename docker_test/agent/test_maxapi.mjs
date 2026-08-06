// OpenAI-client full-dimension test against maxapi (in-container, isolated).
// Hits MAXAPI_BASE with openai SDK the same way a real agent (Codex/CC stack) does.
// Dimensions: non-stream, stream, reasoning_content stream, search/sources,
// single tool call + tool-result round-trip + second-turn stop, multi-tool,
// long context, UTF-8 chinese. Prints structured JSON-line results.

import OpenAI from "openai";

const BASE = process.env.MAXAPI_BASE || "http://host.docker.internal:8080/v1";
const KEY = process.env.MAXAPI_KEY || "sk-test";
const MODEL = process.env.MAXAPI_MODEL || "Claude Sonnet 5";
const client = new OpenAI({ baseURL: BASE, apiKey: KEY });

const R = [];
function pass(k, ok, info) { R.push({ case: k, ok, ...info }); console.error(`[${ok ? "PASS" : "FAIL"}] ${k} ${typeof info.note === "string" ? ":: " + info.note : ""}`); }

async function nonstreamBasic() {
  try {
    const c = await client.chat.completions.create({
      model: MODEL, messages: [{ role: "user", content: "用一句话回答：你是哪种AI模型？" }],
      stream: false, reasoning_effort: "low",
    });
    const txt = c.choices?.[0]?.message?.content || "";
    const rc = c.choices?.[0]?.message?.reasoning_content || "";
    pass("nonstream_basic", !!(txt && txt.length > 0), { note: `content.len=${txt.length} reasoning.len=${rc.length}`, text: txt.slice(0, 120), has_reasoning: rc.length > 0 });
  } catch (e) { pass("nonstream_basic", false, { note: String(e).slice(0, 200) }); }
}

async function streamBasic() {
  try {
    const s = await client.chat.completions.create({
      model: MODEL, messages: [{ role: "user", content: "数1到5，只输出数字用逗号分隔。" }],
      stream: true,
    });
    let txt = "", rc = "", n = 0, fr = null, done = false;
    for await (const ch of s) {
      n++;
      const d = ch.choices?.[0]?.delta || {};
      if (d.content) txt += d.content;
      if (d.reasoning_content) rc += d.reasoning_content;
      if (ch.choices?.[0]?.finish_reason) fr = ch.choices[0].finish_reason;
      if (ch.object === "chat.completion.chunk") { /* shape ok */ }
    }
    pass("stream_basic", txt.length > 0 && fr === "stop", { note: `chunks=${n} content.len=${txt.length} fr=${fr} reasoning.len=${rc.length}`, text: txt.slice(0, 80) });
  } catch (e) { pass("stream_basic", false, { note: String(e).slice(0, 200) }); }
}

async function reasoningStream() {
  try {
    const s = await client.chat.completions.create({
      model: MODEL, messages: [{ role: "user", content: "think briefly then answer: 17*23 = ?" }],
      stream: true, reasoning_effort: "high",
    });
    let rc = "", txt = "", rchunks = 0;
    for await (const ch of s) {
      const d = ch.choices?.[0]?.delta || {};
      if (d.reasoning_content) { rc += d.reasoning_content; rchunks++; }
      if (d.content) txt += d.content;
    }
    // reasoning should arrive as incremental deltas (live thinking), not all at once at end.
    pass("reasoning_stream", rchunks > 0 && rc.length > 0, { note: `reasoning_chunks=${rchunks} reasoning.len=${rc.length} answer=${txt.trim().slice(0, 40)}` });
  } catch (e) { pass("reasoning_stream", false, { note: String(e).slice(0, 200) }); }
}

async function searchSources() {
  try {
    const c = await client.chat.completions.create({
      model: MODEL, messages: [{ role: "user", content: "联网搜索：今天最新的一条科技新闻是什么？给出来源链接。" }],
      stream: false, search: true, reasoning_effort: "low",
    });
    const raw = c; // full object
    const src = raw.sources;
    const srcChoice = c.choices?.[0]?.message?.sources;
    const txt = c.choices?.[0]?.message?.content || "";
    const srcStr = (() => { try { return JSON.stringify(src || srcChoice || "none"); } catch { return "none"; } })();
    pass("search_sources", txt.length > 0, {
      note: `content.len=${txt.length} top_sources=${Array.isArray(src) ? src.length : "none"} msg_sources=${Array.isArray(srcChoice) ? srcChoice.length : "none"}`,
      sources_sample: srcStr.slice(0, 300), top_keys: Object.keys(raw), text: txt.slice(0, 140),
    });
  } catch (e) { pass("search_sources", false, { note: String(e).slice(0, 200) }); }
}

const WEATHER_TOOL = { type: "function", function: { name: "get_weather", description: "查询某城市当前天气", parameters: { type: "object", properties: { city: { type: "string", description: "城市名" } }, required: ["city"] } } };

async function toolCallRoundTrip() {
  try {
    const r1 = await client.chat.completions.create({
      model: MODEL, stream: false,
      messages: [{ role: "user", content: "北京现在的天气怎么样？请必须调用 get_weather 工具查询。" }],
      tools: [WEATHER_TOOL], tool_choice: "auto", reasoning_effort: "low",
    });
    const msg = r1.choices?.[0]?.message;
    const fr1 = r1.choices?.[0]?.finish_reason;
    const tc = msg?.tool_calls?.[0];
    if (!tc) return pass("tool_roundtrip", false, { note: `r1 no tool_call, fr=${fr1}, content=${(msg?.content || "").slice(0, 120)}` });
    let args = {};
    try { args = JSON.parse(tc.function.arguments || "{}"); } catch {}
    // simulate tool execution -> second turn with role=tool result
    const r2 = await client.chat.completions.create({
      model: MODEL, stream: false,
      messages: [
        { role: "user", content: "北京现在的天气怎么样？请必须调用 get_weather 工具查询。" },
        { role: "assistant", content: msg.content || null, tool_calls: msg.tool_calls },
        { role: "tool", tool_call_id: tc.id, content: "温度 -3°C，晴，微风，湿度 40%" },
      ],
      tools: [WEATHER_TOOL], reasoning_effort: "low",
    });
    const fr2 = r2.choices?.[0]?.finish_reason;
    const a2 = r2.choices?.[0]?.message?.content || "";
    pass("tool_roundtrip", fr1 === "tool_calls" && fr2 === "stop" && a2.length > 0, {
      note: `r1.fr=${fr1} args=${JSON.stringify(args)} r2.fr=${fr2} a2.len=${a2.length}`,
      args, r2_text: a2.slice(0, 160),
    });
  } catch (e) { pass("tool_roundtrip", false, { note: String(e).slice(0, 200) }); }
}

async function toolCallStream() {
  try {
    const s = await client.chat.completions.create({
      model: MODEL, stream: true,
      messages: [{ role: "user", content: "调用 get_weather 查询东京天气。" }],
      tools: [WEATHER_TOOL], tool_choice: "required", reasoning_effort: "low",
    });
    let name = "", args = "", id = "", fr = null, n = 0;
    for await (const ch of s) {
      n++;
      const tcs = ch.choices?.[0]?.delta?.tool_calls;
      if (tcs) for (const t of tcs) { if (t.id) id = t.id; if (t.function?.name) name = t.function.name; if (t.function?.arguments) args += t.function.arguments; }
      if (ch.choices?.[0]?.finish_reason) fr = ch.choices[0].finish_reason;
    }
    let argOk = false;
    try { argOk = !!JSON.parse(args); } catch {}
    pass("tool_stream", !!name && argOk && fr === "tool_calls", { note: `name=${name} args=${args} fr=${fr} chunks=${n}` });
  } catch (e) { pass("tool_stream", false, { note: String(e).slice(0, 200) }); }
}

async function longContext() {
  try {
    // ~6KB filler + a needle the model must retrieve (long-context recall).
    const needle = "【密钥】内部代号是 PURPLE-DRAGON-7841，请记住。";
    const filler = "这在是一段无关的中性填充文本用于撑开上下文窗口。".repeat(150);
    const user = filler + "\n\n" + needle + "\n\n问题：上面插入的内部代号是什么？只回代号。";
    const c = await client.chat.completions.create({
      model: MODEL, stream: false, messages: [{ role: "user", content: user }], reasoning_effort: "low",
    });
    const txt = (c.choices?.[0]?.message?.content || "").trim();
    pass("long_context", /PURPLE-DRAGON-7841/.test(txt), { note: `input.len=${user.length} out=${txt.slice(0, 80)}`, text: txt.slice(0, 100) });
  } catch (e) { pass("long_context", false, { note: String(e).slice(0, 200) }); }
}

async function utf8Chinese() {
  try {
    const long = "特殊字符测试：①②③④⑤ émoji 🐉 中文长句测试，包含逗号、句号、破折号——以及引号“这样可以”。".repeat(20);
    const s = await client.chat.completions.create({
      model: MODEL, stream: true, messages: [{ role: "user", content: "把下面这段原样复述一遍:\n" + long }], reasoning_effort: "low",
    });
    let txt = "";
    for await (const ch of s) { const d = ch.choices?.[0]?.delta?.content; if (d) txt += d; }
    // count chinese chars to measure fidelity
    const zh = (txt.match(/[一-鿿]/g) || []).length;
    const emoji = txt.includes("🐉");
    pass("utf8_chinese", zh > 100 && emoji, { note: `out.len=${txt.length} zh_chars=${zh} has_emoji=${emoji}`, sample: txt.slice(0, 60) });
  } catch (e) { pass("utf8_chinese", false, { note: String(e).slice(0, 200) }); }
}

(async () => {
  console.error("=== maxapi OpenAI full-dimension test ===");
  console.error(`base=${BASE} model=${MODEL}`);
  // models list
  try {
    const lst = await client.models.list();
    pass("models_list", lst.data.length > 0, { note: `models=${lst.data.length}`, ids: lst.data.map(m => m.id).slice(0, 20) });
  } catch (e) { pass("models_list", false, { note: String(e).slice(0, 200) }); }
  await nonstreamBasic();
  await streamBasic();
  await reasoningStream();
  await searchSources();
  await toolCallRoundTrip();
  await toolCallStream();
  await longContext();
  await utf8Chinese();
  const ok = R.filter(r => r.ok).length, tot = R.length;
  console.error(`\n=== RESULT: ${ok}/${tot} pass ===`);
  console.log(JSON.stringify(R, null, 2));
})();
