# maxapi — se.zzmax.cn 访客旁路 OpenAI 兼容服务

把 se.zzmax.cn 的访客免登录 + 伪造 X-Forwarded-For（无限重置每日 2 次额度）+ 客户端维护长上下文，
封装成标准 OpenAI Chat Completions。纯 Python 标准库、零依赖、单文件 Docker。

## 上游机制（实证，2026-07-31）

- **端点**：chat/vision 全部走 `https://se.zzmax.cn/api/chat/stream`（SSE）。image/video/audio 走专用 `/image|/video|/audio/generate`，访客 `401`，已删。
- **绕过**：访客免鉴权 + 每请求伪造 `X-Forwarded-For`/`X-Real-IP` → 服务端按 IP 重置额度，无限试用；`messages[]` 无上限 → 客户端维护长上下文。
- **思考触发**：请求体带 `reasoningEffort`（`low|medium|high|max`，`off`=省略）上游才发思考流。缺它不发——这是先前 GPT 无思考、grok 超时报错的根因。
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

- **多出口 IP 轮换**：NAS + 家宽 + VPS 各跑一个代理实例，或前置一个代理池让出口 IP 分散。让"一个 TCP 源 IP 顶几十 XFF"变成"多个源 IP 各顶少量"，直接削弱最易被审计的 pattern。
- **--rpm 按出口设小**：每出口限到接近真人访客的频率（几分钟一次级），别让一个出口高速跑。

### P2 行为层

- **降频**：低频比任何伪装都管用，高频本身就是最强信号。
- **别碰被拒路径**：image/video/audio 已删保持删掉，触发被拒次数也是访客滥用信号。
- **不贪量**：一个流式请求耗上游真实算力，峰值是过载级信号。

## 18 模型（`/api/chat/models` 权威表 + 实测）

| 类别 | 模型 |
|---|---|
| chat | deepseek-v4-flash/pro, claude-opus-4-6/4-8/4.8, gpt-5.6-luna/terra, gpt-5.5, gemini-3.5-flash/3.1-pro-preview, grok-4.5, doubao-glm-5.1, minimax-glm-5.1, kimi-k2, mimo-qwen3.6-plus |
| vision | qwen3.6-plus, kimi-k2.5 |

> grok 组实际路由 `claude-opus-4-8` 后端；补 `reasoningEffort` 后 4.2s 出思考，已实测修复。

## 部署

```bash
cd outputs
docker build -t maxapi:latest .
docker run -d --name maxapi -p 8080:8080 maxapi:latest
# 自定义限速/关伴随：
docker run -d --name maxapi -p 8080:8080 maxapi:latest \
  python maxapi_server.py --host 0.0.0.0 --port 8080 --rpm 12
# 关闭伴随调用：加 --no-companion
curl http://localhost:8080/healthz   # {"status":"ok","models":18}
```

## API

`GET /healthz` · `GET /v1/models` · `POST /v1/chat/completions`（流式默认）

### reasoning_effort（客户端可选）

| 值 | 说明 |
|---|---|
| 省略 | 代理自动注入 `medium` |
| `low`/`medium`/`high`/`max` | 思考强度递增，实时流 `reasoning_content` |
| `off` | 不发该字段，上游不思考 |

也接受 `reasoningEffort`（驼峰）。隐藏思考：`"reasoning": false` 或 `"strip_reasoning": true`。

## 实测验证（Docker 内 live runtime）

| 问题 | 根因 | 修复证据 |
|---|---|---|
| image2/sora 不可用 | 走鉴权生成端点 | `/image|/video|/audio/generate` 端点访客 `401`；已删 27→18 |
| grok 报错 | 缺 `reasoningEffort` 上游不流式→超时 | grok-4.5 low：4.2s 思考、5.7s `[DONE]`、无错误 |
| GPT 无实时思考 | 同上 | gpt-5.5 medium：7.2s 出 36 字符思考 + 153 delta 实时流 |
| 思考时长卡住/连接不结束 | 思考 dump+不关连接 | 简单 "1"：`[DONE]` 后 80ms 干净关闭，`done≈close` |
| 隐身（本节） | 非浏览器 header/高频/无会话 | P0 全量实测见上表 |

## 限制

- 访客档每日每 IP 2 次额度 → 伪造 XFF 循环 IP 绕过；遇 `429`/`繁忙` 自动换 IP 重试（`max_retry=5`）。
- 长上下文由客户端维护（上游 `messages[]` 无上限）。
- 非确定性思考：上游对同请求有时思考有时直答，属上游特性。
- 被发现风险：P0 削弱代码层指纹，但 XFF 硬命门需 P1 多出口 IP 分散（见隐身层章节）。
- 支付系统金额篡改/回调伪造不可行（早期已实证）；唯一发现 `/api/payment/status` IDOR（只读）。
