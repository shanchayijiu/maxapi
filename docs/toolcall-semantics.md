# maxapi 工具调用语义（DSML + sieve）

> 上游无原生 tools。maxapi = **prompt 诱导 + 流式 ToolCallParser 合成** OpenAI/Anthropic/Responses tool 事件。  
> 改 parser/prompt 后必跑：compat / tool_suite / v4 L0 / leak harness。见 [dev-contract.md](./dev-contract.md)。

## 1. 为什么不是「真 tool API」

| 真 API | maxapi |
|--------|--------|
| `tools` / `tool_choice` 为一等 JSON 字段 | 字段只存在于客户端 wire；上行变成文本 |
| 模型输出结构化 `tool_calls` | 模型被要求输出 DSML/类 XML；parser 抽出 |
| 协议保证不泄漏内部标记 | 必须 sieve，否则 `|DSML|` / `<function_calls>` 进 content |

## 2. 诱导（prompt）

- 入口：`_make_tools_prompt(tools, tool_choice)` → 可能 `None`（无 tools / none）。  
- **2026-08-21e compact first-hit**（对齐 ds2/c2a）：短签名列表 + 首选 DSML 示例 + 接受 ` ```json action` / JSON 数组 / 原生 tag；forced 前置 `You MUST call…`；附录仍带一行 `Available tools (JSON-schema)`（compat）。  
- 历史：`_build_messages_with_tools` 把 prior `assistant.tool_calls` 与 `tool` 结果展成 DSML / observation 文本。  
- 标记常量：`_DSML` = `|` + `DSML` + `|`（防源码字面量误匹配）。  
- forced：`tool_choice=required` / 具名 function → 前置硬指令。  
- 过滤：占位名如 `TOOL_NAME_HERE`、不在 catalog 的 name → drop。

## 3. 流式筛（ToolCallParser）

符号：`ToolCallParser`（自 ds2api toolstream 思路移植）。

职责：

1. 识别完整/分片 tool 块（DSML、`<function_calls>`、`<function=`、antml 变体）。  
2. **hold-back** 半截 opener，防 `<l` / 半截 tag 泄漏（`_find_partial`）。  
3. 闭合 invoke → yield `kind=tool_call`；正文只出干净 prose。  
4. EOF **flush**：截断 wrapper 时 salvage 已闭合 invoke（incomplete tool R3）。  
5. 归一化：singular/function_calls、`|DSML|`/`|DSTML|` strip。

### 双通道（硬不变量）

`upstream()` 内：

- **answer** 通道 → `tparser_ans`  
- **reasoning / `<think>`** 通道 → `tparser_think`  

模型常把 tool DSML 写进 thinking。若 reasoning 不过 parser → 标签进 `reasoning_content` 且 tool_call 丢失 → 客户端无工具 + 可能 escalate 双发。  
**禁止**「只筛 content、thinking 原样转发」。

### 跨通道去重（G-A，2026-08-21）

同一 DSML 块常同时出现在 think + answer。双 parser 会各抽出一次 → 非流式/Responses 曾出现 **两个相同** `do_work`（流式路径往往只看到一帧，更易漏检）。

- `upstream()`：`_emit_tool_call` 按 `(name, canonical_args)` 本 attempt 去重  
- `_validate_and_coerce_tool_calls`：列表级再去重；placeholder 含 `tool_name` / `param_name`  
- 证据：`_runtime/v4_evidence/goal_ga_tool_dedupe_20260821.json`

### sol `|>` 方言（G-A，2026-08-21c）

`gpt-5.6-sol` 常输出 `<|DSML|tool_calls|>…<|/DSML|tool_calls|>`（`|>` 收尾 / `<|/` 关标签）。旧 `_normalize_dsml` 只剥 `|DSML|` 前缀 → 变成非法 `<tool_calls|>`，wrapper 漏进 content 同时 invoke 仍可 salvage。

- `_normalize_dsml`：完整 open/close rewrite（含 `<|/DSML|tag|>`）  
- `_TOOL_TAG_FULLS`：加入 `|>` 形态  
- 多模型证据：`_runtime/v4_evidence/goal_ga_multimodel_20260821.json`（6/6 auto 有 tool、leaks=[]、无双发）

## 4. 客户端 wire 形态（合成后）

| 协议 | 形态 |
|------|------|
| chat completions | `delta.tool_calls[]` 分片：首片 id/name/type/arguments=""，后续 arguments 增量；`finish_reason=tool_calls` |
| messages | `tool_use` content blocks |
| responses | `function_call` item + arguments delta/done |

参数：schema coercion（`"3"`→int、`"true"`→bool、default 补齐）在抽出后、下发前。

## 5. auto 无 tool 时（与慢路径交界）

`tool_choice` 默认 auto 且声明了 tools：

1. **首轮**靠 compact prompt 出 tool（CC first-hit 主路径）。  
2. 空 tool escalate：`_should_escalate_auto_tools`（`MAXAPI_TOOL_ESCALATE`，**默认关**）。  
3. 若开启：`_prefetch_until_tool_or_end`（`max_wait` 默认 **5.0s**）→ terminal-force。  
4. **incomplete tool block**：与空 tool 分离，始终可 1 次 forced retry（`_retry_incomplete_tool_*`）。  

门控：howto / do-not-call-tools、`tool_choice=none`、**sol mid-flight completion**。  
quota/auth **不进** ladder。

细节与测 TTFB → [latency-and-escalate.md](./latency-and-escalate.md)。

## 6. 泄漏不变量（L0）

必须保持（v4 八漏点 a–h 有证据）：

- 可见 content / reasoning **无**原始 `|DSML|`、未闭合 function 壳、section 方言残片  
- 半截 tag 跨 chunk 不漏  
- abort/cancel 路径不把 hold buffer 当答案 dump  
- finalize 六类终止干净（含已 partial 后不再 fatal）  
- include_usage 中间 `usage:null`、末块 shape 合法  

证据目录：`_runtime/v4_evidence/`；复跑见 README / STATUS。

## 7. 已知残留（卡点 A — 文档层登记）

用户可感「tool 暴露未完全解决」通常来自：

1. 模型不按 DSML → 依赖 escalate（成功率↑延迟↑）  
2. 极端方言 / 截断仍 salvage 失败 → 偶发无 tool 或残片  
3. 某端点 framing 与另外侧不一致（改一侧漏回归）  
4. 主机改了 parser 但 **8080 容器未 rebuild**（假「没修好」）  

续做：先 live 复现（chat stream + responses stream + 日志有无 escalate），再最小改 parser/prompt；禁止无复现堆规则。

## 8. 相关符号速查

`_make_tools_prompt` · `_build_messages_with_tools` · `ToolCallParser` · `_find_partial` · `_consume_capture` · `_normalize_dsml` · `ReasoningFilter` · `_should_escalate_auto_tools` · `_prefetch_until_tool_or_end` · `_run_terminal_force_*` · `_finalize_chat_stream`
