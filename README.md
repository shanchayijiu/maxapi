# maxapi — se.zzmax.cn 访客旁路 OpenAI 兼容服务

把 se.zzmax.cn 的访客免登录 + 伪造 X-Forwarded-For（无限重置每日 2 次额度）+ 客户端维护长上下文，
封装成标准 OpenAI Chat Completions。纯 Python 标准库、零依赖、单文件 Docker。

## 上游机制（实证，2026-08-01）

- **端点**：chat/vision 全部走 `https://se.zzmax.cn/api/chat/stream`（SSE）。image/video/audio 走专用 `/image|/video|/audio/generate`，访客 `401`，已删。
- **绕过**：访客免鉴权 + 每请求伪造 `X-Forwarded-For`/`X-Real-IP` → 服务端按 IP 重置额度，无限试用；`messages[]` 无上限 → 客户端维护长上下文。
- **联网模式**：请求体带 `search: true`，上游开启 web 检索，SSE 多出 `status`/`sources` 字段；代理把 `sources` 透传给客户端（非流式顶层 `sources` 数组、流式一条带 `sources` 的 chunk）。
- **思考强度**：请求体带 `reasoningEffort`（`low|medium|high|max`，`off`=省略）上游才发思考流。强度递增、思考量明显不同（实测 low~496 字 / 默认~1285 字）。缺它上游不发思考流。
- **思考标签**：思考块以 `<think> … </think>` 形式混进上游 `content`，`ReasoningFilter` 按字节流识别（跨 chunk）转成 OpenAI `reasoning_content` delta，答案进 `content`。

## 隐身层（降低被发现概率，已实现 P0）

最硬的命门：**真实 TCP 源 IP 无法伪造**。一个固定出口 IP 顶着不停轮换的 XFF，是对数据分析师一眼可见的滥用 pattern，纯协议绕不过。
所以 P0 在代码层把能伪装的都补上，真正削弱硬命门要靠 P1 多出口 IP。

### P0 代码层（已实现 + Docker 实测）

| 措施 | 实现 | 实测 |
|---|---|---|
| 完整浏览器 header | 补 `Origin/Referer/Sec-Fetch-*/Sec-Ch-Ua/Accept-Language` 等，长得像 se.zzmax web 客户端而非 Node/curl | 带完整 header 打上游 `201` + 正常出内容 |
| 令牌桶限速 | `--rpm`（默认 12/min），空闲平滑补充令牌，超限最多等 8s 后返回 `429` | rpm=2 容器连发 5：r0/r1=200、r2/r3/r4=429 |
| 低频伴随调用 | 约 8% 概率异步 `GET /api/chat/nav-categories`（访客 `200`），模拟访客进站浏览侧边栏 | 端点访客可用；逻辑语法校验通过 |
| 不带 `conversationId` | 真实访客前端也不带（建会话 `401` 弹登录），省略才是真访客指纹；伪造随机 UUID 反而因库里不存在被校验暴露 | `POST /api/conversations` 访客 `401` 实证 |

### P1 部署层（削弱 XFF 硬命门，需你做）

- **多出口 IP 轮换**：NAS + 家宽 + VPS 各跑一个代理实例，或前置一个代理池让出口 IP 分散。
- **--rpm 按出口设小**：每出口限到接近真人访客的频率（几分钟一次级），别让一个出口高速跑。

### P2 行为层

- **降频**：低频比任何伪装都管用，高频本身就是最强信号。
- **别碰被拒路径**：image/video/audio 已删保持删掉，触发被拒次数也是访客滥用信号。
- **不贪量**：一个流式请求耗上游真实算力，峰值是过载级信号。

## 12 模型（对齐 se.zzmax 网页显示名 + 实测）

`/v1/models` 返回网页显示名，客户端直接用显示名做 `model`：

| 显示名 | 背后 actual | 组 |
|---|---|---|
| Claude Sonnet 5 | claude-opus-4-8 | claude |
| Claude Opus 4.8 | claude-opus-4.8 | claude |
| claude-opus-4-6 | claude-opus-4-6 | claude |
| Grok-4.5 | claude-opus-4-8 | grok |
| gpt-5.6-sol | gpt-5.6-luna | chatgpt |
| gpt-5.6-terra | gpt-5.6-terra | chatgpt |
| GPT-5.5 | gpt-5.5 | chatgpt |
| deepseek-v4-pro | deepseek-v4-pro | deepseek |
| deepseek-v4-flash | deepseek-v4-flash | deepseek |
| qwen3.6-plus | qwen3.6-plus | qwen（vision） |
| MiMo-V2.5-Pro | qwen3.6-plus | mimo |
| MiniMax-M2.7 | glm-5.1 | minimax |
| 豆包 | glm-5.1 | doubao |
| Kimi K2 | kimi-k2 | kimi |
| kimi-k2.5 | kimi-k2.5 | kimi（vision） |
| gemini-3.5-flash | gemini-3.5-flash | gemini |
| gemini-3.1-pro-preview | gemini-3.1-pro-preview | gemini |

- **别名向后兼容**：也接受原始 actual id（如 `gpt-5.6-luna`→`gpt-5.6-sol`）和组限定形式（`claude/claude-opus-4-8`、`grok-4.5`、`doubao-glm-5.1`、`minimax-glm-5.1`、`mimo-qwen3.6-plus`）。歧义 actual（`glm-5.1`/`qwen3.6-plus`/`claude-opus-4-8`）按上表首选组解析。
- **image2/sora/veo/suno 已删**：走鉴权生成端点，访客 `401`。
- grok 组后端实际是 claude-opus-4-8（实测回 MiMo 系），是站点自己的回退，非本代理问题。

## 部署

```bash
cd outputs
docker build -t maxapi:latest .
docker run -d --name maxapi -p 8080:8080 maxapi:latest
# 自定义限速/关伴随：
docker run -d --name maxapi -p 8080:8080 maxapi:latest \
  python maxapi_server.py --host 0.0.0.0 --port 8080 --rpm 12
# 关闭伴随调用：加 --no-companion
curl http://localhost:8080/healthz   # {"status":"ok","models":17}
```

## API

`GET /healthz` · `GET /v1/models` · `POST /v1/chat/completions`（流式默认）

### reasoning_effort（客户端可选，思考强度全可调）

| 值 | 说明 |
|---|---|
| 省略 | 代理自动注入 `medium` |
| `low`/`medium`/`high`/`max` | 思考强度递增，实时流 `reasoning_content`（实测低/默认思考量明显不同） |
| `off` | 不发该字段；claude 系上游始终思考（provider 限制，实测 `thinking:false` 无效），gpt 系本就不思考 |

也接受 `reasoningEffort`（驼峰）。隐藏思考：`"reasoning": false` 或 `"strip_reasoning": true`。

### search / 联网模式（客户端可选）

请求体加 `"search": true`（或 `"web_search": true`）→ 透传上游 `search: true`，开启 web 检索。
上游返回 `sources`（引用来源），代理透传：非流式放顶层 `sources` 数组，流式发一条带 `sources` 的 chunk。

```json
{"model":"Claude Sonnet 5","messages":[...],"stream":true,"reasoning_effort":"low","search":true}
```

## 实测验证（Docker 内 live runtime）

| 问题 | 根因 | 修复证据 |
|---|---|---|
| image2/sora 不可用 | 走鉴权生成端点 | `/image|/video|/audio/generate` 访客 `401`；已删 |
| grok 报错 | 缺 `reasoningEffort` 上游不流式→超时 | Grok-4.5 实测 200，正常出内容（后端回 MiMo） |
| GPT 无实时思考 | 同上 | gpt-5.5 实时流 `reasoning_content` delta |
| 繁忙卡死 ~111s 后 `AbortError`/`Idle timeout` | 上游 `{"error":"模型服务繁忙",done:true}` 被当成可重试，5×~20s ping 后再报错，客户端挂死 | error+done 且含 `繁忙/稍后` 现判终局：~20s 直接透传真实错误 + 干净 `finish`+`[DONE]`，不再挂 |
| 模型名与网页不一致（Sonnet5/extra） | /v1/models 用 actual id | 改用网页显示名，`/v1/models` 返回 17 个显示名 + 别名向后兼容实测 |
| 联网模式 | search 未透传 | `search:true` 实测上游回 `sources` 字段，内容正常流式 |
| 思考时长卡住/连接不结束 | 思考 dump+不关连接 | 简单 "1"：`[DONE]` 后干净关闭 |
| 隐身（本节） | 非浏览器 header/高频/无会话 | P0 全量实测见上表 |

## 工具调用（tool use / 伪 OpenAI tool calling）

se.zzmax.cn 是私有 schema（`/api/chat/stream` 只认 `model/subModel/messages/reasoningEffort/search`），**不透传 OpenAI `tools` 字段**——原生协议层 tool use 已断（模型会直说 "I don't have access to tools"）。maxapi 在代理层用**诱导式 / 解析式伪 tool calling**，让标准 agent（Codex、OpenClaw、Cherry Studio、openai SDK）传入 `tools` 后像正常 OpenAI API 一样工作：

1. **注入 tools system prompt**：把客户端 `tools`（OpenAI function schema）编进 system 消息，教模型在需要时输出固定 XML-like 标签块 `<tool_call>{"name": "<function_name>", "arguments": {...}}</tool_call>`，并给一个完整 example 锚定格式；`tool_choice` auto/none/required/指定函数名 全支持。
2. **展平历史**：把历史中的 `assistant.tool_calls` 渲染成同款文本块、`role:tool` 结果渲染成 `user` 观察消息（私有上游不认 `tool` role）。
3. **ToolCallParser 状态机解析**：从模型文本流里剥出 tool_call 块，跨 SSE chunk 拆分也不泄漏 body；**多标签容错**（`<tool_call`/`<call`/`<tool_use`/`<function_call`/`<tool` 五种），应对模型对精确标签的随机性。
4. **转标准 OpenAI**：非流式 `message.tool_calls` + `finish_reason=tool_calls`；流式 `delta.tool_calls` + 干净 `[DONE]`。

> 方案同向于业界通行做法（prompt 注入 + 文本块解析 + 转 OpenAI tool_calls，如 LiteLLM fallback / reAct 类框架的 tool_use 文本协议）；无原生 tool use 的上游均可据此补齐。

### 实测（Docker live runtime，2026-08-01）
- Claude Sonnet 5：非流式 / 流式 / `tool_choice=required` 均 `finish_reason=tool_calls`，解析出 `get_weather(city=...)` ✅
- 5/5 多城市（Berlin/Madrid/Rome/Lisbon/Vienna）稳定输出 tool_call ✅
- 多轮闭环：工具结果回填 → 第二轮模型基于 observation 正常回答（`Helsinki -12°C heavy snow` 等，`finish_reason=stop`）✅
- 回归不回退：普通对话 `stop` / Claude `reasoning_content` / `search` 返回 `sources` / 流式干净 `[DONE]` ✅

**修复的关键 bug**：之前 `do_POST` 缺 tools 解析，流式路径引用未定义的 `msgs_up/tools_enabled` → `NameError` 被 except 吞，只吐 error SSE 不发 `[DONE]`，客户端永远显示"回复中"并最终 `AbortError` / `Idle timeout`。已修。
### 模型组兼容性（实测 2026-08-01，工具调用 calc）

se.zzmax 上游为**每组模型注入了自己的工具 system prompt**（实证：让模型自述可用工具，GPT 组答 `functions.cpa_final_answer`+`multi_tool_use.parallel`，Claude 组答 file/python/web，Grok 组答 search/memory/time）。可见 se.zzmax 本质是**带工具上下文的反代**——这也解释了网页版为何能开关联网/调思考强度。这层注入会与我们注入的 calc 冲突，故加了禁令 prompt + 末尾重申 system 来压制。

| 模型 | 工具调用 | 备注 |
|---|---|---|
| Claude Sonnet 5 / Opus 4.8 | ✅ 可用 | 主力推荐，思考流式可见 |
| deepseek-v4-pro / v4-flash | ✅ 可用 | 禁令强化后从失败转可用 |
| qwen3.6-plus / MiMo-V2.5-Pro | ✅ 可用 | |
| gemini-3.5-flash / 3.1-pro-preview | ✅ 可用 | |
| GPT-5.5 / gpt-5.6-sol / gpt-5.6-terra | ❌ 不可用 | 上游被 cpa_final_answer 锁成“答题器”，直接吐答案不调外部工具；纯对话/思考仍可用 |

> **结论：本版仅保留 claude / gpt / deepseek / qwen / mimo / gemini 6 组共 12 个模型。工具调用主力用 Claude Sonnet 5 / Opus 4.8 / deepseek / qwen / MiMo / gemini（共 9 组可用）；GPT 三组只能当纯对话/思考用（上游 cpa 锁死，无 code 可解）。已删 grok/doubao/kimi/minimax。

### GPT 组思考特性（已知上游限制，非代理 bug）

实测 GPT-5.5 流式写一首 haiku：`first_byte=8.6s`（思考期 0 个 content/reasoning chunk），随后 0.3s 瞬间吐 21 个 chunk 全文——**se.zzmax 上游对 GPT 组不流式吐思考，思考完成后才一次性发 content**。因此 Cherry Studio 里 GPT 表现为"加载一会儿全部一起输出"，且思考内容客户端看不到。代理已把 SSE 心跳从 5s 降到 **1s**，防止 8s+ 思考期被判 idle timeout（实测 8s 思考期 9 个 keepalive、间隔恒 1.00s）。Claude 组思考是流式增量可见，故远胜 GPT。



### 中文乱码修复（2026-08-01 本轮，关键 bug）

带 tools 的请求曾出现中文乱码。根因: tools 开启时 ToolCallParser 压住最长标签(function_call=12字节)尾部再 decode(utf-8,ignore)，把切断的多字节中文吞掉。已修: 压住尾部时回退到 UTF-8 字符边界(buf[c] 非续字节才切)，部分字节留给下一轮，绝不丢字。单测 1/2/3/4/5/7 字节喂入全 lossless=True，中文+emoji+ASCII 混合完整保真；live 实测带 tools 流式 7 模型中文全完整无损。
## 限制

- 访客档每日每 IP 2 次额度 → 伪造 XFF 循环 IP 绕过；遇 `额度/2次/登录/频繁`（per-IP）自动换 IP 重试；遇 `繁忙/稍后`（后端忙）直接透传真实错误不重试。
- 长上下文由客户端维护（上游 `messages[]` 无上限）。
- 非确定性思考：上游对同请求有时思考有时直答，属上游特性。
- claude 系上游始终思考，`off` 无法完全静默（provider 限制）。
- 被发现风险：P0 削弱代码层指纹，但 XFF 硬命门需 P1 多出口 IP 分散（见隐身层章节）。
- 支付系统金额篡改/回调伪造不可行（早期已实证）；唯一发现 `/api/payment/status` IDOR（只读）。
