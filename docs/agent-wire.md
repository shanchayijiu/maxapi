# maxapi Agent Wire 契约

> 「API 化」= Claude Code / Codex / OpenAI SDK **drop-in**：只换 `base_url` + `api_key`。  
> **不是**多用户平台；多 key/配额/用户体系 → 前置 **NewAPI**（或同类）。

## 1. 拓扑

```text
Claude Code  ──\                     ┌─ /v1/messages
Codex        ───▶  (可选 NewAPI) ──▶ maxapi:8080 ─▶ se.zzmax.cn
OpenAI SDK   ──/                     ├─ /v1/chat/completions
                                     └─ /v1/responses
```

| 客户端 | 常用端点 | 备注 |
|--------|----------|------|
| OpenAI Python/JS SDK | `/v1/chat/completions` | gold 主验收面 |
| Claude Code | `/v1/messages` | Anthropic 块协议 |
| Codex | `/v1/responses`（`wire_api=responses`） | function_call 事件 |
| 任意 OpenAI 兼容 UI | chat + `/v1/models` | 模型 id 用显示名 |

## 2. 认证与模型

- maxapi 可对客户端检查 `Authorization`（部署时配置）；**不**把该 key 转成上游账号。  
- `model`：用 `GET /v1/models` 返回的 **id（显示名）**；别名见 `MODEL_ALIASES`。  
- 未知 model → **404** `model_not_found`（chat）。  
- 经 NewAPI 时：NewAPI 的模型名需映射到 maxapi 认识的 id；maxapi 再映射到 upstream actual。

## 3. 端点行为摘要

### 3.1 共同

- 流式 / 非流式  
- tools → DSML + parser（见 [toolcall-semantics.md](./toolcall-semantics.md)）  
- thinking：`reasoning_content` / Anthropic thinking 块（由 `<think>` 剥离）  
- 错误体：OpenAI `{error:{message,type,param,code}}` 或 Anthropic `type=error`  
- 长上下文：默认 compact；`MAXAPI_COMPACT=0` 时可 400 `context_length_exceeded`  
- 客户端断连：中止上游 + finalize  

### 3.2 Chat Completions

- `n!=1` → 400  
- `temperature/top_p/stop/seed/...` 多数**静默忽略**（上游无对等）  
- `response_format` json_object/json_schema：**软注入** system，非原生保证  
- 流式 tool_calls **分片** shape 与官方 SDK 对齐（gold）  
- `stream_options.include_usage`：中间 chunk `usage:null`，末块 `choices:[]`+usage + `[DONE]`  
- SSE：`Content-Type: text/event-stream`，`Cache-Control: no-cache`，`Connection: keep-alive`，`X-Accel-Buffering: no`  
- 全响应 `x-request-id`  
- `/v1/embeddings`、`/v1/completions` → **501** `not_implemented`  

### 3.3 Messages（Anthropic）

- 请求展平为内部 messages + DSML  
- 流式/非流式 `tool_use` / `tool_result` 回填多轮  
- 错误 flavor=anthropic  

### 3.4 Responses

- `input` / tools 转到同一 chat+DSML 路径  
- 流式：`response.output_text.delta`、`function_call`、`response.function_call_arguments.done`、`response.completed`  
- 非流式：`object=response`，`status=completed`  

## 4. 与官方 API 的 known deviations

| 项 | 官方 | maxapi |
|----|------|--------|
| tools | 一等协议 | 文本诱导 + 合成（可能 escalate） |
| 采样参数 | 生效 | 多忽略 |
| json_schema 严格 | 引擎约束 | 软提示，不保证 |
| 多模态生成 | 常有 | 访客上游无 image/video/audio gen |
| 身份/安全策略 | 厂商策略 | 上游网页模型策略；非 maxapi 多租户策略 |
| 配额错误 | 账号维度 | 访客 identity；映射为标准错误，不露上游中文额度文案 |
| 延迟 | 单次推理 | tools auto 可能多 RTT |

Agent 可感目标：工具闭环、无标签泄漏、流式不截断、终止干净、不无故多倍 RTT。  
**不**把「缺 Admin / 缺账号池 UI」算 deviation。

## 5. 接 NewAPI（前端网关）

用户自建，maxapi 文档只列注意点：

1. NewAPI channel 的 base_url 指向 `http://<host>:8080`（或内网）。  
2. 上游模型名与 maxapi `/v1/models` id **一致或可映射**。  
3. 超时调大：escalate 路径可能 >60s。  
4. 流式支持打开；勿对 chat 强制非流式聚合除非 UI 需要。  
5. 限流/多用户/计费只在 NewAPI 配；不要改 maxapi 做租户。  
6. 探针与生产 key 分离；勿用用户 Codex 正在用的同 key 打满并发。

## 6. 本地 Agent 直连示例

```bash
# 健康与模型
curl -sS http://127.0.0.1:8080/healthz
curl -sS http://127.0.0.1:8080/v1/models

# Chat 非流式烟测
curl -sS http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer local-dev" \
  -H "Content-Type: application/json" \
  -d '{"model":"Claude Sonnet 5","messages":[{"role":"user","content":"Reply with exactly: pong"}]}'
```

Claude Code / Codex：把 provider base_url 指到 8080，模型填 live 列表中的 id。  
具体客户端配置以各工具文档为准；maxapi 不维护各 IDE 版本的逐步截图。

## 7. 验收（wire）

```bash
python -u _openai_sdk_gold.py       # 期望 20/20
python _local_compat_check.py       # FAIL 0
python _accept_tool_suite.py        # FAIL 0
python _accept_agent_long.py        # FAIL 0
```

v4 一致性：`_runtime/v4_consistency_report.json`（当前 lineage 常为 `insufficient-evidence`：fail=0，unknown=mustE3，合法非假绿）。

## 8. 卡点 C（Agent wire 缺口）登记原则

§2 卡点 C 只写：**相对官方 API，Agent 仍不像的地方**（泄漏、某端点边角、回填、慢导致的工具超时等）。  
禁止写成「未做多用户/Admin」。
