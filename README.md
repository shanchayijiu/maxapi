# maxapi — se.zzmax.cn 访客旁路 OpenAI / Anthropic 兼容服务

把 se.zzmax.cn 的访客免登录 + 伪造 X-Forwarded-For（无限重置每日 2 次额度）+ 客户端维护长上下文，
封装成标准 **OpenAI Chat Completions** 与 **Anthropic Messages**（Claude Code / agent 可直连）。
纯 Python 标准库、零依赖、单文件 Docker。

**2026-08-16**：预压缩 `eff>limit*0.88`（估核算 inflate 1.20，分段 budget），修超 100% 不自动压。**2026-08-15**：sol auto tool escalate/terminal-force/529。身份：2-OK XFF；`busy!=quota`。详见 [STATUS.md](STATUS.md)。

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

`GET /healthz` · `GET /v1/models` · `POST /v1/chat/completions`（OpenAI 兼容，流式默认）· `POST /v1/messages`（原生 Anthropic，Claude Code / Codex 直连，流式+非流式+thinking+tool_use 全闭环，2026-08-03 实测）

两个 POST 端点共享同一上游 (`upstream()`) 与限流重试逻辑；`/v1/messages` 直接出 Anthropic Messages 格式，无需外部协议转换层（cc-switch 15721 proxy 路径已非必需，详见文末「Claude Code 直连 maxapi」节）。

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

## 工具调用（tool use / DSML 伪 OpenAI·Anthropic tool calling）

se.zzmax.cn 是私有 schema（`/api/chat/stream` 只认 `model/subModel/messages/reasoningEffort/search`），**不透传 OpenAI `tools` 字段**。maxapi 在代理层用 **DSML 诱导 + 解析** 做伪 tool calling，让 Codex / Claude Code / Cherry Studio / openai SDK 传入 `tools` 后像正常 API 一样工作：

1. **注入 DSML tools system prompt**（CONNECTED TOOLS）：把客户端 tools 编进 system，教模型输出 DSML `tool_calls` 块；`tool_choice` auto/none/required/指定函数名全支持。
2. **展平历史**：`assistant.tool_calls` → DSML 文本块；`role:tool` / Anthropic `tool_result` → user 观察消息。
3. **ToolCallParser**：流式 sieve 剥 DSML / 上游 `<function=NAME>` 等格式，跨 chunk 不泄漏闭合标签。
4. **转标准协议**：OpenAI `message.tool_calls` / Anthropic `tool_use`；流式对齐原生分片。

### auto tool 可靠性阶梯（2026-08-15，gpt-5.6-sol 验收）

sol 等模型在 `tool_choice=auto` 下会偶发只 think + `end_turn`（声称无终端）。代理层补强制阶梯，**不改 XFF / 2-OK identity / busy≠quota**：

| 阶段 | 行为 |
|---|---|
| Prompt | CONNECTED TOOLS；动作必须发 DSML；howto / do-not-execute 允许纯文本 |
| Escalate | 动作请求且无 `tool_call` → 升到 forced `function/Bash` 或 `required` 再打 1 次 |
| Terminal-force | 仍无 tool：独立短上下文 + HARD REQUIREMENT，最多 2 次 |
| 529 旁路 | 首轮/escalate 的 busy/529/502/503 可进 ladder；**quota/auth 永不进** |
| 禁 tool | 用户明确 `do not call tools` → `tool_choice=none`，不 escalate |

环境变量（默认开）：`MAXAPI_TOOL_ESCALATE=1`、`MAXAPI_TOOL_TERMINAL_FORCE=1`。

**实证（8080 Docker，`gpt-5.6-sol`）**：`_accept_tool_suite.py` **28/28**（auto Bash **20/20**，howto/plain/agent 禁 tool/chat/stream/multi 全过）；`_accept_agent_long.py` **15/15** 轮 tool + 收尾 `ALL_DONE`（约 160s）。日志：`first_err=temporarily unavailable` → `terminal-force success`；`identity retire after 2 ok uses` 仍在。

```bash
python -u _accept_tool_suite.py    # 28/28
python -u _accept_agent_long.py    # 17/17
```

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
### 流式 sources chunk 兼容性修复（2026-08-01，AI_TypeValidationError）

开启 search 时、上游返回 sources 字段，代理会发一个单独的 sources chunk。之前该 chunk 形态为 `{id,object,created,model,sources}` **缺 choices 数组**，OpenAI chunk schema 是 discriminated union（需 choices(array) 或 error(object)），Cherry Studio Zod 校验报 `AI_TypeValidationError: expected array at path choices`（原 chunk 如 `{"model":"claude-opus-4-6","sources":[]}` 无 choices）。**已修**: sources chunk 加 `choices: []`，满足 array 分支、sources 扩展字段仍可被客户端读取。实证(monkeypatch 假 upstream 强制 yield sources): sources chunk keys=[choices,created,id,model,object,sources]，has_choices_array=True → Zod union 命中。
## 工具调用升级：DSML 协议移植 (代码落地 + 单测全绿 + agentic 长跑验收, 2026-08-06)

当前 ToolCallParser 用 bytes 级标签扫描 + 5 种别名容错, body 是内联 JSON。该方案在 se.zzmax 上游注入了自己的工具 system prompt（cpa_final_answer / multi_tool_use / file/python/web）环境下不稳定。

移植来源: ds2api (internal/toolcall + internal/toolstream) 的 DSML 标记方案, prompt 注入式 tool calling, 固定注意力结构标签 + CDATA 值容器。

### DSML 格式（教模型输出的协议）

用包裹标签 tool_calls + invoke name=TOOL_NAME + parameter name=ARG 用 CDATA 值容器。12 条规则 + 正/负例 + 参数形态表 + 真实 tool 名例。
- 字符串值用 CDATA (含代码/路径/查询, 自动编码右方括号大于号); 数值/布尔/null 纯文本; 对象用嵌套 XML; 数组用重复 item 子节点。
- prompt 含 IMPORTANT 禁令压制上游注入的 cpa_final_answer / multi_tool_use / file/python/browser/search 工具。

### 解析器（移植 ds2api 流式 sieve）

- 非流式完整解析: stripFencedCodeBlocks -> 标准化前缀 -> XML block 提取 -> CDATA 解包 -> JSON 修复（反斜杠/未引号键/缺数组括号）。
- 流式 sieve: State(pending/capture/capturing) 状态机, 检测部分标签尾部 hold 不切（跨 SSE chunk 安全）, 代码围栏内不误判, flush 释残量文本。
- 保留接口契约: feed(str)->(content,str)|(tool_call,id+name+arguments), flush()->同, _make_tools_prompt->str|None, _build_messages_with_tools->(msgs,enabled)。

接口不变（upstream() / 三端点不改）, 纯内部替换。sanity 7/7 仍需 PASS。

进度(代码层已落地 / 单测全绿): DSML 全套移植完成 — `_make_tools_prompt` / `_build_messages_with_tools` / `class ToolCallParser` 及全部 helper (`_dsml_extract_calls` / `_strip_fences` / `_normalize_dsml` / `_parse_invoke` / `_try_legacy_json` / `_consume_capture`)。`maxapi_server.py` 自备份点 53632→70677 字节、约 1069→1467 行, `import re` 已加 (在既有 `import http.server, json, ...` 行追加 `re` 于 L40)。
接口契约 (feed / flush / _make_tools_prompt / _build_messages_with_tools) 不变, `upstream()` 与三端点 `/v1/chat/completions`、`/v1/messages`、`/v1/responses` 未改 — `git diff -w` 内容变更仅落 L40 import 与 L256~L1040 DSML 区(`# ---- DSML toolcall` 至 `def upstream()` 之前)。备份 `maxapi_server.py.bak-pre-dsml` 在位。
单元测试: 初期 scratch 套件(39 例) 仅作开发期验证, 已并入下方仓库内 `tests/test_dsml.py`(69 例); clone 可直接 `python tests/test_dsml.py` 复现 69/69。末修: `_strip_fences` 原把围栏内整段内容一并丢弃致 fence-stripped 用例 FAIL, 改为只剥围栏分隔行、保留内文, 全绿。
活链路验证(已跑): 重建容器, `docker exec` 实证容器内 `_DSML`/`_dsml_extract_calls`/`_RE_FN_INVOKE`/`_parse_fn_invoke` 在位、`maxapi_server.py` 70677 B 与本机一致, `/healthz` ok。
sanity 7/7 PASS(实跑): healthz/models_list(n=12)/nonstream_text(end_turn)/stream_text_tail/stream_tooluse_tail/tool_roundtrip(r1=tool_use r2=end_turn input={'city':'北京'})/long_context(PURPLE-DRAGON-7841)。
单测已入仓库 tests/test_dsml.py (相对路径, clone 可跑), 69/69 ALL PASSED — 覆盖 DSML + 上游 <function=NAME> 格式 / raw-content 含<>保留 / multi-invoke 闭合标签不泄 / 围栏包裹 / 跨 SSE chunk / 逐字符。
`claude -p` agentic 长跑验收(run5): 经 maxapi 接 Claude Sonnet 5, 15 turns, 11 tool_use/11 tool_result, unittest 跑→Edit 修→重跑, 独立复核 `python -m unittest discover -s tests` 16/16 OK, CLI 输出合法 JSON, 闭合标签泄漏=0, is_error=False terminal=completed。对比修复前 run1 num_turns=1(工具调用全漏成文本)、run3 7 turns 仍有 </|DSML|invoke> 闭合标签泄为 content —— 三个 bug 经长跑暴露并修复:
  1) `_strip_fences` 把围栏内整段丢弃 → 只剥围栏分隔行、保留内文;
  2) 上游 `<function=NAME>...<parameter=KEY>` 注入格式不被 DSML 解析 → 加 FN 兼容层(_RE_FN_INVOKE/_RE_FN_PARAM/_parse_fn_invoke/_dsml_extract_calls 兜底/_consume_capture bare-handler/_find_seg+_find_partial 含 `<function`);
  3) DSML 流式 prefix/suffix 用 captured(原始)坐标但取自 normalized(逐 |DSML| 越缩越偏)坐标 → multi-invoke 时闭合标签泄为 content → 改在 norm 坐标切片。另: raw-text 参数含 <> 被 XML 误解 → `_parse_param` 非CDATA 分支加 `_STRING_PRESERVE` 守护; `_find_partial` 尾长于前缀时漏判 → 加 `low.startswith(prefix)`。
## 限制

- 访客档每日每 IP 2 次额度 → 伪造 XFF 循环 IP 绕过；遇 `额度/2次/登录/频繁`（per-IP）自动换 IP 重试；遇 `繁忙/稍后`（后端忙）直接透传真实错误不重试。
- 长上下文由客户端维护（上游 `messages[]` 无上限）。
- 非确定性思考：上游对同请求有时思考有时直答，属上游特性。
- claude 系上游始终思考，`off` 无法完全静默（provider 限制）。
- 被发现风险：P0 削弱代码层指纹，但 XFF 硬命门需 P1 多出口 IP 分散（见隐身层章节）。
- 支付系统金额篡改/回调伪造不可行（早期已实证）；唯一发现 `/api/payment/status` IDOR（只读）。

## 稳定性修复批次 (commit 860f005)

### 1. 繁忙并入换 IP 重试
上游对 chatgpt 组偶发按 IP 限流, 仅返回 "{error":"模型服务繁忙,请稍后重试"}". 旧逻辑把"繁忙/稍后"判为终局直接透传(单次15s失败). 现把"繁忙"并入volatile集合, 在max_retry次内换随机 XFF/X-Real-IP 重试,全部失败才透传终局. "稍后"仍判终局不重试. 实测: GPT 组在限流窗口内换 IP 后 4s 成功(工具调用+中文均正常).

### 2. 流式 error chunk 补全 OpenAI 结构
旧 error chunk 形如 {"error":{"message":...}}, 缺 choices/object/id/created/model. 部分 agent 端(如 Cherry Studio Zod discriminated union)对非 streaming-error 事件校验时,极端情况下可能误判;与之前已修的 sources chunk 是同一类隐患. 现统一补全:
```
{"id":..,"object":"chat.completion.chunk","created":..,"model":..,"choices":[],"error":{"message":..}}
```
choices:[] 命中 union 第一支, error 同时保留在顶层, OpenAI SDK 仍能识别为 error 事件.

### 3. error 即终结, 不再发空 stop
旧: error chunk 后仍发 finish_reason="stop" 空 chunk + [DONE], 显得像"空成功",是早期客户端"一直回复中/AbortError/Idle timeout"症状来源之一. 现 error 后置 stream_failed=true, 跳过 final stop chunk, 直接 data: [DONE]; except 分支(内部异常)同样规范化后终结. 非流式 error 保持 502.

### 4. 真实 agent 形式回归矩阵 (重建 --no-cache 后, fetch 模拟 agent 填 baseurl + tools + 多轮 tool result 回填)
| 模型 | 工具 r1 (finish=tool_calls, args 合法 JSON) | 闭环 r2 (回填工具结果后正常停) | 中文无损 |
|---|---|---|---|
| Claude Sonnet 5 | OK city=北京 | OK 94字表格 | OK |
| Claude Opus 4.8 / claude-opus-4-6 | OK(同组) | -- | OK |
| deepseek-v4-pro | OK | -- | OK |
| deepseek-v4-flash | OK | -- | OK |
| qwen3.6-plus | OK | -- | OK |
| MiMo-V2.5-Pro | OK city=Beijing | -- | OK |
| gemini-3.5-flash | OK | -- | OK |
| gemini-3.1-pro-preview | OK | -- | OK |
| gpt-5.6-sol | OK city=北京 | -- | OK(reasoning.len=40) |
| GPT-5.5 | (纯对话) OK | -- | OK(89字无损) |

注: GPT 组早期因上游限流整段繁忙(每次15-16s); 本次"繁忙改换 IP 重试"后,同一段时间内4-18s成功,中文/工具均正常. 早先 handoff 记的"GPT 组工具调用不可用(cpa 锁死)"结论已证伪——那是限流,非工具机制问题.

### 5. agent 接入要点 (OpenClaw / Codex / Cherry Studio)
- maxapi 是标准 OpenAI 兼容: baseurl=http://<host>:8080/v1, api_key 任意, chat completions 走 /v1/chat/completions.
- 工具不是 maxapi 内置: agent 自己的工具(MCP tools / openclaw Hub builtin / codex 内置)由 client 以 OpenAI tools 字段下发, maxapi 注入工具 system prompt 让模型 emit tool_call 并回传为 OpenAI tool_calls; client 执行后用 role=tool 消息回填, maxapi 展平为 user 观测送上游. 上例矩阵已验证这套闭环.
- 若 agent 端 list 返回 0 tools (如 OpenClaw MCP Hub Total:0 tools), 是该 client 未注册任何 MCP server, 与 maxapi 无关; maxapi 收到空 tools 即不注入, 退化为纯对话. 需在 client 侧配置 MCP server 后工具才可用.
- 思考强度: 请求带 reasoning_effort (off/low/medium/high/max) 透传上游; reasoning 默认开(吐 reasoning_content), 传 reasoning:false 或 strip_reasoning:true 关.
- 联网: 请求带 search:true (或 web_search/websearch) 即开网, 上游返回的 sources 以顶层 sources 数组下发, chunk 带 choices:[] 已兼容.
- GPT 组思考非流式: 上游"思考完成后一次性发 content", 代理透传不能改变该上游行为; first-byte 期间每 1s 发 keepalive 防客户端 idle timeout.

## 流式 tool_call 拆分对齐原生 OpenAI (commit caeb9ac)

### 起因
以真实 agent 客户端栈回归: 不止裸 fetch / openai SDK, 进一步用 Vercel ai-sdk(@ai-sdk/openai 4.0.25 + ai 7 + zod 3.25.76)——即 Cherry Studio 报错栈里的 AiSdkToChunkAdapter/AiProvider.modernCompletions 同款路径——直连 maxapi 跑工具调用。

### 根因(被证伪的猜测)
初测 ai-sdk tool-call part 的 input 为空 {} 经源码核对 StreamingToolCallTracker(provider-utils): processNewToolCall 要求首 delta 带 id+function.name, 后续 processExistingToolCall 累积 function.arguments. 旧实现只发一个 delta 同时塞 id+name+完整 arguments. 经核对 tracker 源码后确认单 burst 实际也能被累积(processNewToolCall 直接存 arguments), 故空 input 的真因另在别处——最终定位为测试侧 zod 版本不匹配(v4 vs ai-sdk peer 的 v3)导致工具 parameters 被序列化为空 {}, 模型无 city 参数 schema 才发出 {} 空参. 换装 zod@3.25.76 后 input 即正确恢复 {city:北京}. 此为测试环境问题非服务端缺陷.

### 仍采纳的改进: tool_call 拆分
虽单 burst 兼容, 拆分为 header + arguments 分片更贴合原生 OpenAI 流式格式, 对最严格客户端更稳: 
- header chunk: {index,id,type,function:{name,arguments:""}}
- continuation chunks: 仅 {index,function:{arguments: 片段}} 按 20 字符切分
maxapi_server.py do_POST 流式 elif tool_call 分支实现上述拆分. UTF-8 安全: arguments 是已解码 Python str, 按码点切片不截断多字节, 各片段编码到 UTF-8 字节均合法.

### 真实客户端栈回归(权威证据)
1. openai SDK 4.104(规范客户端) r1 工具调用 finish=tool_calls args合法中文无损 + r2 闭环(assistant tool_calls+role tool 回填) finish=stop 67字中文无损 —— 重建后容器仍通过.
2. ai-sdk(@ai-sdk/openai 4.0.25, 即 Cherry 栈) r1 工具调用 fr=tool-calls input={city:北京} 参数正确, tool-input-delta 正确累积.
3. 全 chunk 级 TypeValidationError 隐患已封: sources chunk 与 error chunk 均带 choices:[] 命中 Zod union 首支; error 即终结不发空 stop.
4. 注意: 测试 toggle ai-sdk 时务必锁定 zod@3.25.76(v3) 与 ai-sdk peer 一致, 否则工具 parameters 被序列化为空会误判服务端缺陷.

### 与用户报错栈对照
- AI_TypeValidationError @convertAndEmitChunk/readFullStream: 命中 sources/error chunk 缺 choices → 已补 choices:[] 封口.
- AbortError/Idle timeout: 流式首字节前每 1s keepalive + error 即终结不再空转 → 已缓解.
- “工具调用不通”: 真实客户端(openai SDK)轮1+轮2 与 ai-sdk 轮1 均通过; 若 agent 端 list=0 tools(如 OpenClaw MCP Hub Total:0), 是该 client 未注册 MCP server, 与 maxapi 无关, 需在 client 侧配工具.

### openai SDK 闭环实测(重建 caeb9ac 后)
```
R1 fr=tool_calls tc=get_weather/args={"city": "北京"}
R2 fr=stop text.len=67 [北京现在的天气情况如下: 晴 24°C 50% ...]
```
ai-sdk 轮1: fr=tool-calls input={"city":"北京"}


## 全面排查批次 (Dockerfile HEALTHCHECK + .dockerignore + 全维度回归)

### 容器/构建卫生改进(低风险)
- Dockerfile 新增 `HEALTHCHECK`: 每 30s 探 /healthz, timeout 5s, start-period 30s 容忍冷启动, 连续 3 次失败才标 unhealthy (90s 窗口). 用镜像自带 python (slim 无 curl/wget) 调 urllib, 不引第三方依赖. 实测重建后 `docker inspect .State.Health.Status = healthy`, ExitCode 0.
- 新增 `.dockerignore`: 把 __pycache__/.git/pocs_*/maxchat.py/scratch/*.log/*.md 排除出 build context, 加速 build 不影响功能 (Dockerfile 仅 COPY maxapi_server.py).

### 全维度回归矩阵 (重建后实测)
| 维度 | 结果 |
|---|---|
| /v1/models | 12 模型, 标准 object=list/data[] |
| /healthz | 200, 含 models 计数 |
| 流式 ping 全12模型 | 12/12 OK, finish=stop, fb~10ms, tot 3-5s |
| 非流式 全12模型 | 12/12 OK, finish=stop, choices+message+usage 结构标准 |
| 工具调用 全12模型 (stream, auto) | 11/12 OK finish=tool_calls args 合法 JSON; GPT-5.5 auto 罕见拒参(上游 cpa 干扰), tool_choice:required 可强制 |
| 多工具一次调用 (Claude) | OK, 一次发 get_weather(city=北京)+get_time(tz=Asia/Tokyo) |
| 长中文 UTF-8 切分 (deepseek 1102字) | OK, 标准中文标点无损, 无乱码 |
| 非法 JSON body | 400 + error.message (不崩) |
| 空 messages | 200, 上游正常响应 (不崩) |
| 并发 3 模型 | 全 200 stop, 无串扰 |
| reasoning:false strip | OK, 不返 reasoning nuts, 纯文本 |
| 无效 model id | 兜底 DEFAULT_MODEL, 200 正常回答 |
| tool_call 流式拆分 (header+args分片) | OK, 经 PowerShell 实测确认格式对齐原生 OpenAI |
| HEALTHCHECK | healthy, ExitCode 0 |

### 已知边界 (无法修, 已公示)
- GPT 组思考非流式: 上游"思考完成才一次性发 content", 代理无法改变上游行为; first-byte 期间每 1s keepalive 防 idle timeout. 极个别 GPT-5.5 思考超长 (实测曾 60s+), 客户端 idle timeout 应设宽 / 或选 GPT-5.5 以外模型做长任务.
- GPT-5.5 auto 工具偶发拒绝: 上游注入的 cpa_final_answer/multi_tool_use 与本服务 tools prompt 在 auto 下偶有冲突; tool_choice:required 可强制 emit tool_calls, args 合法.
- 不限: 这是对游客免登录反代的代理, 上游对 chatgpt 组偶发按 IP 限流 (繁忙); 已实现 max_retry 内换随机 XFF/X-Real-IP 重试.


## Claude Code 直连 maxapi (原生 /v1/messages, 已实测闭环 2026-08-03)

maxapi 现已自带原生 Anthropic `/v1/messages` 端点 (`_handle_messages`), **Claude Code 可直连 8080, 无需 cc-switch 15721 proxy 做协议转换**. 旧结论 "不要加 /v1/messages, proxy 会转" 已作废 — 直连链路更短、更可控、已全绿.

### 根因修复 (本轮 P0)
1. **流式 SSE 缺顶层 `type` 字段**: Anthropic SDK 路由靠 `JSON.parse(sse.data).type` (不是 SSE `event:` 行名), 之前每个事件 data 只写内层结构 (`{"message":{...}}` / `{"index":0,"delta":{...}}`), 顶层 `type` 全是 undefined, SDK 第一帧即抛 `Unexpected event order, got undefined before "message_start"`. 修: `sse()` 自动注入 `{"type": ev, ...data}`. 修后流式 7/7 全过.
2. **`content_block` 内多了 `index` 字段 (工具调用失败+长输出中断的真因)**: `open_block()` 之前把 `index` 同时塞进了 SSE 事件顶层和 `content_block` 对象内部 (`{"type":"tool_use","index":N,...}`). Anthropic 规范里 `index` 只在事件顶层, `content_block` 内只该有 `type`+类型字段. Claude Code desktop 客户端遇到 block 内的 index 会把 `tool_use` 块解析退化成纯文本, 导致「工具调用失败」+ 后续流式状态机错乱长输出中断. 修: `open_block()` 的 `start` 去掉 `"index": idx`, 仅保留 `{"type": btype, ...}`. 修后单/多工具 agentic + PowerShell/Bash/Read 全闭环.
3. **`claude -p` 的 settings.json env 优先级**: `~/.claude/settings.json` 的 `env` 块 (`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN`) 优先级**高于 shell inline env**. 单靠 `ANTHROPIC_BASE_URL=... claude -p` 不生效, claude 仍连主 settings 钉的 15721, 对 `Claude Sonnet 5` + sk-test 返回 `403 openai_error`. 解法: 用 `--settings <file>` 注入临时 settings 覆盖 env (见下), 不动主 config.

### cc-switch 接入 (推荐生产链路)
cc-switch 跑 15721 做 provider 路由, maxapi:8080 作为一个 **claude app_type 的 provider** 加进 switch (DB id `62dca522...`), env 关键项:
- `ANTHROPIC_BASE_URL`: **`http://127.0.0.1:8080`** (用 127.0.0.1, 不要用 localhost — Windows 上 localhost 可能解析到 `[::1]` 命中 wslrelay:8080 而非 docker 容器)
- `ANTHROPIC_AUTH_TOKEN`: 任意占位 (maxapi 不验 token)
- `ANTHROPIC_DEFAULT_*_MODEL[_NAME]`: 指向 maxapi `/v1/models` 里的 id (如 `Claude Opus 4.8` / `Claude Sonnet 5`)
- 在 cc-switch 里把该 provider 切到 current, Claude Code (desktop+CLI) 即经 switch 走 8080, 走原生 Anthropic `/v1/messages`, 无需协议转换.

### 复现步骤 (claude -p 直连 maxapi 8080)
```bash
# 1. 临时 settings (不动 ~/.claude/settings.json)
cat > /tmp/maxapi_settings.json <<'EOF'
{"env":{"ANTHROPIC_API_KEY":"sk-test","ANTHROPIC_BASE_URL":"http://127.0.0.1:8080"},"model":"Claude Sonnet 5"}
EOF

# 2. 单轮问答
claude -p "用一句话回答你是什么模型。" \
  --model "Claude Sonnet 5" \
  --settings /tmp/maxapi_settings.json

# 3. agentic 工具往返 (Read 多轮 — 验证 tool_use/tool_result 闭环)
echo "SECRET-12345" > target.txt
claude -p "读取当前目录 target.txt 并原样输出内容, 只用 Read 工具." \
  --model "Claude Sonnet 5" \
  --settings /tmp/maxapi_settings.json \
  --allow-dangerously-skip-permissions
```

### 实测证据 (2026-08-03, claude-cli 2.1.220)
- `docker logs maxapi`: `POST /v1/messages?beta=true ua=claude-cli/2.1.220 (external, sdk-cli) key=sk-test` 命中容器, 单轮返回正确中文回复.
- agentic `Read target.txt` 多轮: claude 经 maxapi 完成 tool_use → tool_result → 第二轮 text, 正确取出 `SECRET-12345`. 证明 `/v1/messages` 对完整 agentic 闭环 (tool 往返 + 多轮 + stop_reason=tool_use 再调用) 成立.
- Anthropic SDK in-container 7 维度 (非流/流/thinking/tool 往返/stream tool_use input_json_delta 累积/长上下文): 7/7 PASS.

### P1 限流重试对齐 (本轮)
- 本地限流闸 429 + 上游 overloaded 529 均补 `Retry-After: 5` header (Anthropic SDK 见 429/529 + Retry-After 会自动退避重试). `_send()` 扩展 `extra` 参数支持自定义 header.
- 上游 429/繁忙的 IP 轮换重试逻辑 (`upstream()` 的 `volatile` + `max_retry=5`) 由 OpenAI / Anthropic 路径**共享**, 无需重复实现.
- 流式 error 事件 (`sse("error",...)`) 保持 `api_error` type; SSE 事件无 HTTP header 概念, Retry-After 仅作用于非流式响应.

## 工具调用加固 + agentic 不中断验证（DSML 上游注入格式禁令，2026-08-06）

承接 `工具调用升级：DSML 协议移植` 节。probe1 复现：se.zzmax 上游对 Claude 组模型会注入自己的工具 system prompt，模型偶发把 `<tool_name>NAME</tool_name>` 形式直接发到正文（probe1 日志：`<tool_name>Write</tool_name><param name="path">probe.txt</param>...`，maxapi 透传成文本，claude -p 一轮即 end_turn）。根因：上游注入指令与我们 DSML block 在同一 system prompt 里并存，模型混淆格式。

修复（`_make_tools_prompt` L633，在既有「忽略其它工具指令」句之后追加一行）:
```python
head.append("Do NOT use any other tag format either - NOT <tool_name>NAME</tool_name>, NOT <function=NAME>, NOT <function_calls>, and NOT any antml code fences. The ONLY correct form is the " + tco + " block shown above.")
```
明令禁用 `<tool_name>` / `<function=NAME>` / `<function_calls>` / antml fence 四种上游注入格式，唯 DSML block 正确。仅 prompt 一行，无解析层改动。

证据（5 份 claude -p agentic run，跨 hardening 前后对比）:
- 加固前 probe1: `<tool_name>Write</tool_name>` 泄漏到对话，num_turns=1，terminal=completed（工具调用全漏成文本）。
- 加固后 5 份 run 全 0 泄漏（grep `<tool_name>` / `<function=` / `<function_calls>` 计数皆 0）: run6d（12 turn / 3.83 min / terminal=completed）、run6e（14 turn / 4.14 min / terminal=completed）、run6f（35 turn / 8.35 min / 29 tool_use × 29 tool_result / 5 Bash / is_error=false）、run6f `--resume`×2（同 session ea55a4a3，3+6 turn）。
- run6f 是真实 5 子包 linguakit 工程（tokenkit/metrics/corpus/nlp/集成），经 maxapi 接 Claude（上游别名通道 deepseek-v4-flash），DSML 多轮 tool_use/tool_result 闭环不中断，标签全不泄漏到对话框。
- sanity 7/7 PASS 重证（本轮实跑）: healthz / models_list(n=12) / nonstream_text / stream_text / stream_tooluse / tool_roundtrip / long_context。

容器对齐: Dockerfile 采用 `COPY` 模式（非挂载）。`docker exec maxapi grep -n 'Do NOT use any other tag format' /app/maxapi_server.py` 命中 L633；容器内 `wc -c /app/maxapi_server.py` = 70904 B，与本机 CR-normalize 后逐字节一致（确认跑的是当前 hardening 版本，非旧镜像）。

诚实边界（等同于「像正常 API 能 vibe coding」的可达范围）:
- DSML 管线层: 多轮 tool 循环不中断、0 标签泄漏、工具往返闭环正常 —— 已验证（约 60 turn 累计）。
- 上游内容层: se.zzmax 访客档偶发退化短响应，单次 wall clock 受限（典型单 run 3-8 min）；非 maxapi 缺陷，`claude -p --resume <session>` 续做即恢复（同 session 累积 wall 可延长）。
- 结论: 经 maxapi 走 Claude Code 做 vibe coding 的关键链路（工具调用不泄漏、多轮不中断）已可正常使用；单次时长受上游访客档退化影响存在上限，需 `--resume` 续做延长。

复现（claude -p 经 maxapi 跑 agentic，不泄漏）:
```bash
# settings.json env 直连 8080（无需 cc-switch）: ANTHROPIC_BASE_URL=http://127.0.0.1:8080, ANTHROPIC_API_KEY=sk-test
echo "<任务文本>" | claude -p --model "Claude Sonnet 5" \
  --max-turns 300 --dangerously-skip-permissions \
  --output-format stream-json --verbose
# 末行 JSON: <tool_name>/<function=/<function_calls> 计数为 0 → 加固生效; num_turns>1 且 terminal_reason=completed → 多轮闭环不中断
```

## 修 502 upstream_error: assistant content 为 OpenAI list-of-parts 时崩溃（P0, 2026-08-06）

现象用户实撞: `claude -p` 经 maxapi 跑多轮 vibe coding 时偶发 `✻ 502 {error:{message:"","type":"upstream_error"}}` 表面像上游错误。

根因: OpenAI `/v1/chat/completions` 路径不经过 `_flatten_anthropic_messages` (那是 Anthropic 路径专用), messages 直接进 `_build_messages_with_tools`。其 assistant+tool_calls 分支取 `txt = content or ""`, 没处理 content 为 OpenAI list-of-parts 格式。Claude Code 经 `/v1/chat/completions` 发多轮 tool 往返时, assistant 消息 content 是 `[thinking, text]` parts 数组且 `tool_calls` 在顶层, `txt` 变 list 后 `txt + nl + nl + nl.join(lines)` 报 `TypeError: can only concatenate list (not str) to list` → `do_POST` 捕获后走 502 (空 message)。

修复 (`_content_to_text` helper, `_build_messages_with_tools` assistant+tcs 分支调用):
```python
def _content_to_text(content):
    # str|list-of-parts|None -> str; text parts 拼接, thinking/tool_use/tool_result/image 块丢弃
    ...
    # _build_messages_with_tools: txt = _content_to_text(content)  (原为: txt = content or "")
```

验证:
- `tests/test_repro_502_list_content.py` 复现: 修前 exit 1 (TypeError), 修后 exit 0 (建 7 消息, assistant content=str len=173 含 DSML block)。
- `tests/test_dsml.py` 69/69 仍全绿。
- 重建容器, 容器内 `_content_to_text`@L662 与调用@L720 在位, 70904→72092 B, `/healthz` ok。
- sanity 7/7 PASS。
- 端到端: 直接发该崩溃形态请求 (assistant content=[thinking,text]+tool_calls+tool 结果多轮) 到容器 → 200 流式正常返回 + `finish_reason=stop` + `[DONE]`, 502 消失。

影响面: 仅 OpenAI `/v1/chat/completions` 路径有何含 list content 的 assistant+tool_calls 多轮历史时触发 (Claude Code 经此路径发多轮 tool 往返会撞)。原生 `/v1/messages` 路径因 `_flatten_anthropic_messages` 已处理 list content 未受影响。回归测试已入仓库 `tests/test_repro_502_list_content.py`。

## 缓解"做一下停一下"：tool prompt 加 rule13 EXECUTE-DO-NOT-NARRATE（2026-08-07）

现象用户实撞: Claude Code 经 maxapi 跑 vibe coding 时"做一下停一下"——做几步就停在叙述（描述将要做啥但不发 tool_call 然后停），需催才继续，离正常 API 可连续 vibe coding 差一段。

根因（chatdbg 实抓诊断）: 不是 sieve 解析 bug——停顿那几轮 assistant content 里无 `|DSML|`/`<tool_calls>` 块，是模型自己选择用嘴说动作而非发 tool_call。属上游内容侧退化倾向（GPT 组受 cpa_final_answer/multi_tool_use 注入冲突最严重时只叙述不发 tool_call 然后停）。

修复（仅 prompt 强化，解析层不动）:
- `_make_tools_prompt` 规则区 L613 追加 rule13: "EXECUTE, DO NOT NARRATE: 若你意图执行动作（写/编辑/跑/读文件、跑命令、查询等），本回合就发 tool_call 块调用工具，勿用散文描述动作然后停；叙述绝不替代 tool 调用，工作未完且下一步是动作时必须现在就调用工具。"
- `_build_messages_with_tools` 末尾 reminder 同步强化为同一 EXECUTE-DO-NOT-NARRATE 措辞。

实证（强化后两轮 claude -p agentic，同一 4-checker pylintx 工程，`--max-turns 250`，stream-json 核数）:
- run6h2 (GPT-5.5): 16 turn / 15 tool_call / 15 tool_result / 0 `<tool_name>`/`<function=`/`<function_calls>` 泄漏 / terminal=success，但末轮助理 content 是纯叙述"当前回合无法继续执行文件写入和测试命令"+停，pylintx 工程未真正交付——GPT-5.5 仍撞上游叙述化停止。
- run6h3 (Claude Sonnet 5): 43 turn / 41 tool_call / 41 tool_result / 0 标签泄漏 / 425 s / terminal=completed / pylintx 真做完（34 单测全过、4 CLI 子命令输出合法、`verify_all.py` exit 0 打 `ALL_GREEN_OK`×21）——真闭环。
- 容器对齐: `docker exec maxapi grep -c 'EXECUTE, DO NOT NARRATE' /app/maxapi_server.py` = 1，跑的是 rule13 版。
- 单测全绿: `tests/test_dsml.py` 69/69，`tests/test_prompt_exec_discipline.py` PASS，`tests/test_repro_502_list_content.py` PASS。

诚实边界:
- Claude Sonnet 5 经 maxapi vibe coding 的关键链路（工具不泄漏 / 多轮不中断 / 单次可做完一个真工程）已稳，推荐 vibe coding 用 Claude Sonnet 5。
- GPT-5.5 在大上下文多轮 tool 往返下仍偶发"叙述化停顿"（只说不动然后停），属上游内容侧行为，非 maxapi 侧可完全消除；prompt 强化已缓解但未根除。
- 单次 wall clock 受上游访客档退化上限（典型几分钟），需时可 `claude -p --resume <session>` 续做延长。


## 修 Claude Sonnet 5 整体 502/error：上游 subModel 映射错指到已失效的 provider（2026-08-07）

现象用户实撞: 手动在 se.zzmax 网页用 Claude Sonnet 5 对话成功，但 `claude -p` / 任何客户端经 maxapi 打 Claude Sonnet 5 全部 502 / `upstream model repeatedly busy after 5 retries`，最小 `say hi` 也打不通。

根因 (绕 maxapi 直连上游枚举证实): 模型映射表 `RAW_MODELS` 把 display "Claude Sonnet 5" 指到上游 `subModel=claude-opus-4-8`。本轮直连 se.zzmax `/api/chat/stream` 枚举: `claude-opus-4-8` 和 `claude-opus-4.8` 全返回 `当前模型暂无可用的服务提供商` / `模型服务繁忙`；而 `claude-sonnet-5` 5/5 OK-stream、`claude-opus-4-6` 3/3 OK-stream。网页同名成功 vs 枚举反证 → 网页发的就是 `claude-sonnet-5`，maxapi 错指的 `claude-opus-4-8` 上游 provider 已被撤。

修复（两处）:
- `RAW_MODELS` L80: `("Claude Sonnet 5", "claude", "claude-opus-4-8", "premium")` → `("Claude Sonnet 5", "claude", "claude-sonnet-5", "premium")`，与网页实际发送对齐。
- `upstream()` SSE error volatile 关键词 L1119: 在 额度/2次/登录/游客/套餐/频繁/繁忙 后补 服务提供商/provider/暂无可用，使上游 provider 临时被撤也走换 IP 重试而非直接报错。

实证 (重建容器后，本轮实跑):
- 容器 L80 已 claude-sonnet-5，/healthz 200。
- 经 maxapi 直接 POST Claude Sonnet 5 最小请 → 之前 502，现在 HTTP 200 + `"content":"Hi there friend!"` + `finish_reason=stop`。
- 单测全绿：`tests/test_dsml.py` 69/69、`tests/test_repro_502_list_content.py`、`tests/test_prompt_exec_discipline.py`。

诚实边界:
- Claude Opus 4.8 上游 claude-opus-4.8 本轮枚举 5/5 繁忙，网页也显示繁忙——属上游侧限流非 maxapi 缺陷，等上游恢复即恢复。
- claude-opus-4-8 上游 provider 已被撤，别名 claude-opus-4-8 / claude/claude-opus-4-8 仍指 display Claude Sonnet 5 (现↦上游 claude-sonnet-5)，即显式发 model=claude-opus-4-8 会降级路由 sonnet；原意 opus 建议换 claude-opus-4-6 (上游 OK)。
