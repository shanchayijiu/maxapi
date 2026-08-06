// Native Anthropic /v1/messages test against maxapi (in-container, isolated).
// Uses official @anthropic-ai/sdk the same way Claude Code / Codex do.
// Dimensions: non-stream (text+thinking), stream (all event types),
// thinking content block, tool_use round-trip with tool_result + second-turn stop,
// streaming tool_use input_json_delta accumulation, long context.

import Anthropic from "@anthropic-ai/sdk";

const BASE = process.env.MAXAPI_BASE || "http://host.docker.internal:8080";
const KEY = process.env.MAXAPI_KEY || "sk-test";
const MODEL = process.env.MAXAPI_MODEL || "Claude Sonnet 5";
const client = new Anthropic({ baseURL: BASE, apiKey: KEY });

const R = [];
function pass(k, ok, info) { R.push({ case: k, ok, ...info }); console.error(`[${ok ? "PASS" : "FAIL"}] ${k}${info.note ? " :: " + info.note : ""}`); }

async function nonstreamBasic() {
  try {
    const msg = await client.messages.create({
      model: MODEL, max_tokens: 1024,
      messages: [{ role: "user", content: "用一句话说明你是哪种 AI 模型。" }],
    });
    const texts = msg.content.filter(b => b.type === "text").map(b => b.text).join("");
    pass("nonstream_basic", msg.stop_reason === "end_turn" && texts.length > 0, { note: `stop=${msg.stop_reason} text.len=${texts.length}`, text: texts.slice(0, 120), blocks: msg.content.map(b => b.type) });
  } catch (e) { pass("nonstream_basic", false, { note: String(e).slice(0, 300) }); }
}

async function nonstreamThinking() {
  try {
    const msg = await client.messages.create({
      model: MODEL, max_tokens: 2048,
      messages: [{ role: "user", content: "think briefly then answer: 17*23 = ?" }],
      thinking: { type: "enabled", budget_tokens: 1024 },
    });
    const text = msg.content.filter(b => b.type === "text").map(b => b.text).join("");
    const think = msg.content.filter(b => b.type === "thinking").map(b => b.thinking).join("");
    pass("nonstream_thinking", text.length > 0, { note: `blocks=[${msg.content.map(b => b.type)}] think.len=${think.length} text=${text.trim().slice(0, 40)}`, has_thinking: think.length > 0 });
  } catch (e) { pass("nonstream_thinking", false, { note: String(e).slice(0, 300) }); }
}

async function streamBasic() {
  try {
    const events = [];
    let text = "", think = "";
    const s = await client.messages.stream({
      model: MODEL, max_tokens: 1024,
      messages: [{ role: "user", content: "数1到5，逗号分隔。" }],
    });
    const evtTypes = new Set();
    s.on("streamEvent", (e) => {
      evtTypes.add(e.type);
      if (e.type === "content_block_delta") {
        const d = e.delta;
        if (d.type === "text_delta") text += d.text;
        if (d.type === "thinking_delta") think += d.thinking;
      }
    });
    const final = await s.finalMessage();
    const evs = [...evtTypes].sort();
    const need = ["message_start", "content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"];
    const hasAll = need.every(t => evs.includes(t));
    pass("stream_basic", final.stop_reason === "end_turn" && text.length > 0 && hasAll, { note: `stop=${final.stop_reason} text=${text} events=[${evs.join(",")}]` });
  } catch (e) { pass("stream_basic", false, { note: String(e).slice(0, 300) }); }
}

async function streamingThinking() {
  try {
    let thinkChunks = 0, text = "";
    const s = await client.messages.stream({
      model: MODEL, max_tokens: 2048,
      messages: [{ role: "user", content: "think briefly: what is 99+1? Just the answer after thinking." }],
      thinking: { type: "enabled", budget_tokens: 1024 },
    });
    const types = new Set();
    s.on("streamEvent", (e) => {
      types.add(e.type);
      if (e.type === "content_block_delta") {
        if (e.delta.type === "thinking_delta") thinkChunks++;
        if (e.delta.type === "text_delta") text += e.delta.text;
      }
    });
    const final = await s.finalMessage();
    const hasThinkingBlock = final.content.some(b => b.type === "thinking");
    pass("stream_thinking", thinkChunks > 0 && hasThinkingBlock, { note: `thinking_delta_chunks=${thinkChunks} text=${text.trim().slice(0,30)} blocks=[${final.content.map(b=>b.type)}] events=[${[...types].join(",")}]` });
  } catch (e) { pass("stream_thinking", false, { note: String(e).slice(0, 300) }); }
}

const WEATHER = { name: "get_weather", description: "查询某城市当前天气", input_schema: { type: "object", properties: { city: { type: "string", description: "城市名" } }, required: ["city"] } };

async function toolRoundTrip() {
  try {
    const r1 = await client.messages.create({
      model: MODEL, max_tokens: 1024,
      messages: [{ role: "user", content: "北京天气怎样？必须调用 get_weather。" }],
      tools: [WEATHER], tool_choice: { type: "auto" },
    });
    const tu1 = r1.content.find(b => b.type === "tool_use");
    if (!tu1) return pass("tool_roundtrip", false, { note: `r1 no tool_use, stop=${r1.stop_reason} blocks=[${r1.content.map(b=>b.type)}]` });
    // second turn with tool_result
    const r2 = await client.messages.create({
      model: MODEL, max_tokens: 1024,
      messages: [
        { role: "user", content: "北京天气怎样？必须调用 get_weather。" },
        { role: "assistant", content: r1.content },
        { role: "user", content: [{ type: "tool_result", tool_use_id: tu1.id, content: "温度 -3°C，晴，微风，湿度 40%" }] },
      ],
      tools: [WEATHER],
    });
    const r2text = r2.content.filter(b => b.type === "text").map(b => b.text).join("");
    pass("tool_roundtrip", r1.stop_reason === "tool_use" && r2.stop_reason === "end_turn" && tu1.input?.city, {
      note: `r1.stop=${r1.stop_reason} tool_input=${JSON.stringify(tu1.input)} r2.stop=${r2.stop_reason} r2text.len=${r2text.length}`,
      r2text: r2text.slice(0, 160),
    });
  } catch (e) { pass("tool_roundtrip", false, { note: String(e).slice(0, 300) }); }
}

async function streamingToolUse() {
  try {
    let name = "", inputStr = "", id = "";
    const s = await client.messages.stream({
      model: MODEL, max_tokens: 1024,
      messages: [{ role: "user", content: "调用 get_weather 查东京天气。" }],
      tools: [WEATHER], tool_choice: { type: "any" },
    });
    const types = new Set();
    s.on("streamEvent", (e) => {
      types.add(e.type);
      if (e.type === "content_block_start" && e.content_block?.type === "tool_use") {
        id = e.content_block.id; name = e.content_block.name;
      }
      if (e.type === "content_block_delta" && e.delta?.type === "input_json_delta") {
        inputStr += e.delta.partial_json;
      }
    });
    const final = await s.finalMessage();
    let inputOk = {}; try { inputOk = JSON.parse(inputStr || "{}"); } catch {}
    const hasInputJsonDelta = types.has("content_block_delta");
    pass("stream_tooluse", final.stop_reason === "tool_use" && name === "get_weather" && inputOk.city, {
      note: `name=${name} id=${id} input=${inputStr} stop=${final.stop_reason} events=[${[...types].join(",")}]`,
    });
  } catch (e) { pass("stream_tooluse", false, { note: String(e).slice(0, 300) }); }
}

async function longContextAnth() {
  try {
    const needle = "【内部代号】PURPLE-DRAGON-7841";
    const filler = "这是一段中性中文填充文本用于撑开上下文。".repeat(150);
    const c = await client.messages.create({
      model: MODEL, max_tokens: 256,
      messages: [{ role: "user", content: filler + "\n\n" + needle + "\n\n上面插入的内部代号是什么？只回代号。" }],
    });
    const text = c.content.filter(b => b.type === "text").map(b => b.text).join("");
    pass("long_context", /PURPLE-DRAGON-7841/.test(text), { note: `out=${text.trim().slice(0, 80)}` });
  } catch (e) { pass("long_context", false, { note: String(e).slice(0, 300) }); }
}

(async () => {
  console.error(`=== maxapi Anthropic /v1/messages test === base=${BASE} model=${MODEL}`);
  await nonstreamBasic();
  await nonstreamThinking();
  await streamBasic();
  await streamingThinking();
  await toolRoundTrip();
  await streamingToolUse();
  await longContextAnth();
  const ok = R.filter(r => r.ok).length, tot = R.length;
  console.error(`\n=== RESULT: ${ok}/${tot} pass ===`);
  console.log(JSON.stringify(R, null, 2));
})();
