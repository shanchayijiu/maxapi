# maxapi 架构（逻辑模块）

> 实现载体目前是单文件 `maxapi_server.py`（~6k 行）。本文按**逻辑边界**描述，便于改 wire 时不踩旁路。  
> **不包含** Admin / 账号池产品 / 多租户设计（明确不做）。

## 1. 位置

```text
Agent (Claude Code / Codex / OpenAI SDK)
        │  可选：NewAPI 等网关（多 key / 配额）
        ▼
maxapi :8080
  GET  /healthz
  GET  /v1/models[/{id}]
  POST /v1/chat/completions
  POST /v1/messages
  POST /v1/responses
        │
        ▼
se.zzmax.cn  POST /api/chat/stream  (访客 SSE，无原生 tools 字段)
```

## 2. 主链路

```text
HTTP Handler
  → 参数校验 / 未知 model 404
  → 消息展平（Anthropic/Responses → 内部 messages）
  → tools → DSML system prompt（_make_tools_prompt / _build_messages_with_tools）
  → compact_request（可选，上下文超阈）
  → upstream()：XFF identity + CookieJar + 并发槽 + SSE 读
  → ReasoningFilter（<think>）
  → ToolCallParser ×2（think 通道 + answer 通道）
  → （auto）escalate / terminal-force 阶梯
  → 各协议 renderer（chat SSE / messages SSE / responses SSE / 非流式 JSON）
```

## 3. 逻辑模块 ↔ 代码（符号级）

| 模块 | 主要符号 | 职责 | 旁路任务默认 |
|------|----------|------|----------------|
| 模型目录 | `RAW_MODELS`, `MODEL_ALIASES`, `resolve_model` | 显示名 / actual / 别名 | 只读 |
| 访客身份 | `rand_ip`, `identity_*`, `CookieJar`, `sticky_ip` | XFF 轮换、cookie | **保护区** |
| 限流 | `RateLimiter`, `--rpm` | 访客频率伪装 | 慎改 |
| 上游传输 | `upstream`, `_pool_get/_pool_put`, 信号量 | SSE、重试、busy/quota | 协议可碰传输慎碰 |
| 推理剥离 | `ReasoningFilter` | think 标签 | 可测后改 |
| 工具诱导 | `_make_tools_prompt`, `_build_messages_with_tools` | DSML 注入与历史展平 | 改必跑 tool 回归 |
| 工具筛 | `ToolCallParser`, `_find_partial`, `_consume_capture` | 流式防泄漏 | 改必跑 v4 L0 |
| 压缩 | `compact_request`, EWMA | 长上下文 | 可测后改 |
| 升级阶梯 | `_should_escalate_*`, `_prefetch_until_tool_or_end`, terminal-force | auto 无 tool 时二次上游 | **慢路径核心** |
| HTTP 面 | `Handler`, `_handle_*` | 三端点 | wire 改这里 |
| 终止 | `_finalize_chat_stream` 等 | 六类终止统一 | 改必跑 finalize |

## 4. 三端点

| 端点 | 客户端 | 内部 |
|------|--------|------|
| `/v1/chat/completions` | OpenAI SDK / 多数 UI | 主 wire 验收面（gold/compat） |
| `/v1/messages` | Claude Code | Anthropic 块 / tool_use |
| `/v1/responses` | Codex `wire_api=responses` | function_call 事件 |

三者共享 `upstream()` 与 tool 管线；差异在请求展平与响应 framing。修一侧必须抽测另外两侧。

## 5. 与「真 API」的差距（架构层）

官方 API：tools 为一等协议字段。  
maxapi：上游只有网页 chat 文本 → **prompt 诱导 + 流式 sieve 合成** tool_calls。

因此架构上永远存在：

- parser 漏/过剥 → L0 内容问题  
- 模型不输出 DSML → escalate 用额外 RTT 换成功率 → 慢  

「API 化」在本项目 = 把上述差距在 **Agent 可感行为**上压到可接受，**不是**上管理后台。

## 6. 目标骨架（仅 wire 清晰，可选演进）

若未来拆文件（需用户点名，非本阶段任务），建议边界：

```text
httpapi/       # 三端点校验与渲染
promptcompat/  # DSML、历史展平、compact
runtime/       # upstream、pool、identity、retry 策略
toolstream/    # parser 唯一实现
obs/           # request-id、TTFB、escalate 计数
```

**禁止**借拆分引入 Admin/账号池产品模块。

## 7. 保护区（摘要）

详见 `STATUS.md` §1：

- XFF / retire / busy≠quota / 隐身 header  
- 双通道 tparser  
- incomplete salvage  
- sol mid-flight completion 门控  
- 连接池 close 后再 put 的禁令  

## 8. 相关文档

- 上游细节 → [upstream-se-zzmax.md](./upstream-se-zzmax.md)  
- tool → [toolcall-semantics.md](./toolcall-semantics.md)  
- 慢 → [latency-and-escalate.md](./latency-and-escalate.md)  
- Agent 契约 → [agent-wire.md](./agent-wire.md)  
