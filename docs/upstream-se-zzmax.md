# maxapi — 上游与访客旁路（se.zzmax.cn）

> 实证摘要。权威行为以 live 8080 + 当前 `maxapi_server.py` 为准。  
> **不写**账号池产品设计；identity 轮换只是访客旁路手段。

## 1. 上游端点

| 项 | 值 |
|----|-----|
| Host | `se.zzmax.cn` |
| Chat | `POST /api/chat/stream`（SSE） |
| 鉴权 | 访客免登录（无 Bearer 上游账号） |
| 原生 tools | **无** — 只有文本 messages |
| 思考 | 正文内 `<think>…</think>`；请求体 `reasoningEffort` |
| 联网 | 请求体 `search: true` 时 SSE 可带 sources |

image/video/audio 专用生成访客 401，maxapi **不暴露**。

## 2. 访客额度与身份（保护区）

- 上游按 **IP（XFF/X-Real-IP）** 计试用额度。  
- maxapi 每请求（或 sticky 窗口内）伪造 XFF；`CookieJar` 维护访客 cookie。  
- **2 次 OK 后主动 retire** identity，避免等额度错误。  
- **`busy ≠ quota`**：busy/529/stall 同 identity 退避；quota 换新 identity 短睡。  
- 客户端错误体**不**原样泄漏上游额度中文文案。  
- 并发：`MAXAPI_UPSTREAM_CONCURRENCY`（代码默认 env `"8"`；历史文档曾写 3，以代码/容器 env 为准）。  
- 队列等待：`MAXAPI_UPSTREAM_QUEUE_WAIT`（默认 6s）。

### 隐身 header（P0 已实现）

浏览器同源头：`Origin` / `Referer` / `Sec-Fetch-*` / `Sec-Ch-Ua` / `Accept-Language` 等。  
令牌桶 `--rpm`；低频 companion `GET /api/chat/nav-categories`；**不带**伪造 `conversationId`。

### 硬限制（文档诚实）

真实 TCP 源 IP 无法协议伪造。单出口高速轮换 XFF 仍是可分析 pattern。部署层多出口 / 降 rpm 是运维手段，不是 maxapi 产品功能。

## 3. 请求体关键字段（转发/合成）

| 字段 | 含义 |
|------|------|
| `messages` | 展平后的 chat 文本 |
| `model` | `resolve_model` 后的 upstream actual |
| `reasoningEffort` | 上游接受 `low\|medium\|high\|max`。客户端 `off` / `strip_reasoning`：**映射为 `low`**（真传 `off` live 会 busy/529）。默认 `MAXAPI_DEFAULT_EFFORT`=`medium` |
| `search` | 联网 |
| （无）`tools` | 上游不认识；由 DSML 进 system/user 文本 |

## 4. 模型映射

显示名来自 `RAW_MODELS`；上游源目录为 `GET https://se.zzmax.cn/api/chat/models`，当前 live 为 22 个聊天模型（图片/视频/音频专用组不纳入文本 chat wire）：

- chatgpt：`gpt-6-astra`、`gpt-5.6-terra`、`gpt-5.6-sol`
- claude：`claude-sonnet-5`、`claude-opus-4.8`、`claude-opus-5`、`claude-opus-4-7`、`claude-opus-4-8`、`claude-opus-4-5`、`claude-opus-4-6`、`claude-haiku-4-5`
- deepseek：`deepseek-v4-pro`、`deepseek-v4-flash`、`deepseek-v4-flash-vision-exp`
- qwen：`qwen3.6-plus`
- doubao / minimax：`glm-5.1`（外部 display id 分别为 `doubao/glm-5.1`、`minimax/glm-5.1`）
- kimi：`kimi-k2.5`、`kimi-k2`
- mimo：`qwen3.6-plus`（外部 display id 为 `MiMo-V2.5-Pro`）
- gemini：`gemini-3.7-flash`、`gemini-3.6-flash`

已下线或改名的客户端 ID 保留在 `MODEL_ALIASES`：旧 `gpt-5.6-luna` / `gpt-5.5` → `gpt-5.6-sol`；旧 `gemini-3.5-flash` / `gemini-3.1-pro-preview` → `gemini-3.7-flash`。模型上下文元数据按 live `contextWindow` 同步，输出上限是 maxapi 自身安全上限。

改 catalog 后必须：rebuild 容器 + curl `/v1/models` + 更新 README 表 + 跑 `_local_compat_check.py`（或等价 live 检查）。

## 5. 错误与重试语义（传输层）

| 类 | 行为 |
|----|------|
| quota | retire identity，可换 IP 再试 |
| busy / 529 / stall | 同 identity 退避；可进 tool escalate ladder（若开启） |
| auth / validation | 不进 ladder；透传/映射标准错误 |
| too long | compact 后重试（非流式路径） |
| client disconnect | 中止 upstream drain；finalize `client_cancel` |

## 6. 与 Agent wire 的交界

- 上游慢 / busy → 用户可感「回复慢」（见 [latency-and-escalate.md](./latency-and-escalate.md)）。  
- 上游乱输出 DSML 方言 → parser 压力（见 [toolcall-semantics.md](./toolcall-semantics.md)）。  
- 身份话术拒答：本上游主要是网页助手，不如 Notion/Cursor 锁身份狠；仍可能偶发不按 DSML 出 tool。

## 7. 改动红线

除非用户点名：不改 XFF 轮换、retire 阈值、busy≠quota 分支、隐身 header 集合、CookieJar 锁语义。  
协议/tool 工作默认只碰 parser、prompt、renderer、escalate 策略开关。
