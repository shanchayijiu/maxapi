# maxapi 延迟与 escalate

> 卡点 B：回复慢。先分桶再改；retry/escalate 是最后手段。  
> 产品目标仍是 Agent 可感 RTT，不是压测刷分。

## 1. 慢从哪来（结构）

maxapi 不是「一次 HTTP = 一次上游」。在 **tools + auto** 路径上可能：

```text
上游 #1 完整生成（可能很长 thinking）
  → 无 tool_call
  → prefetch/escalate 上游 #2（forced 文案）
  → 仍无 → terminal-force 上游 #3（短上下文）
```

每一跳都是真实 `se.zzmax.cn` SSE 费用。  
另有：并发槽排队、`reasoningEffort`、quota retire 换身份、compact、客户端/代理缓冲。

## 2. 分桶

| 症状 | 优先真因 | 勿先做 |
|------|----------|--------|
| 简单闲聊也 2～3× 单次上游 | auto+tools 误 escalate；mid-flight 门控失效 | 加大 timeout |
| 首 token 很晚、其后正常 | 上游本身 TTFB；effort 高；队列 `UPSTREAM_QUEUE_WAIT` | 改 parser |
| 有 tools 动作慢但最终有 tool | escalate 命中（用 RTT 换成功率） | 关 escalate 却不补 prompt |
| 偶发极慢 + 日志 busy | 并发过高打 upstream 529；身份/quota 抖动 | 盲升 concurrency |
| 非 tools 纯文本仍慢 | effort 默认、search、上游负载 | 怪 tool 阶梯 |
| 改完代码一样慢 | 容器不是新二进制 | 继续改源码 |

## 3. 关键旋钮（代码/环境）

| 旋钮 | 默认（代码） | 作用 |
|------|----------------|------|
| `MAXAPI_TOOL_ESCALATE` | **关（False）** | 空 tool 多上游阶梯总闸；CC first-hit 默认关 |
| `MAXAPI_TOOL_TERMINAL_FORCE` | 开（且依赖 escalate） | 最后短上下文强召；escalate 关则不进 |
| `_prefetch_until_tool_or_end(max_wait=…)` | **5.0s** | 自**首个非 reasoning** 事件起的等待封顶 |
| `MAXAPI_UPSTREAM_CONCURRENCY` | env 默认 **8** | 同时上游数；过高→busy |
| `MAXAPI_UPSTREAM_QUEUE_WAIT` | 6.0s | 抢不到槽时等待 |
| `MAXAPI_DEFAULT_EFFORT` | `medium` | 未显式传 `reasoning_effort` 时 |
| 请求 `reasoning_effort` / `reasoningEffort` | 覆盖默认 | `off\|low\|medium\|high\|max` |
| **客户端 `off` 映射** | → 上游 **`low`** | se.zzmax **不接受**真 `off`（live：发 `off`→连 busy/529）；`strip_reasoning` 无显式 effort 时同样走 off→low |
| `search` | 客户端 | 联网显著变慢 |
| sol mid-flight completion | 逻辑门控 | 已完成则禁止再 escalate |

历史 STATUS 曾写 concurrency=3、max_wait=8：以**当前源码与容器 env**为准，文档冲突时改文档。

### 2026-08-21 顺序矩阵（deepseek-v4-flash，单飞，权威）

| 场景 | HTTP/s | 结果 |
|------|--------|------|
| bare `effort=low` | ~1.2s | pong |
| bare medium | ~2.6s | pong |
| bare off（→low） | ~3.9s | pong（仍有短 think） |
| tools none + off | ~3.8s | 短答，无 tool |
| tools auto + off | ~2.6s | 1×do_work |
| tools auto + medium | ~6.7s | 1×do_work |
| chatty + tools auto + off | ~2.1s | 无 tool（不误 escalate） |
| responses auto off/med | ~5.9s / ~4.2s | 1×function_call |

证据：`_runtime/v4_evidence/goal_gb_latency_20260821.json`。并发探针会制造 busy，**不要**并行压矩阵当延迟结论。

## 4. escalate 决策摘要

`_should_escalate_auto_tools`：

- 需要：tools 启用、auto 类 choice、首轮无 tcs、非 howto/禁止调用、未 completion 门控。  
- 允许：retryable busy/529；**incomplete tool block** / mid-output interrupt（2026-08-21 起，避免 chain 中途 529 直接杀回合）。  
- 禁止：quota/auth；`MAXAPI_TOOL_ESCALATE=0`。  

prefetch：reasoning **不**启动 max_wait 时钟；先出 content/tool 才计时。  
仅 `saw_tool` 才把 escalate 缓冲当成功（防「无 tool 缓冲当成功」）。

## 4.5 incomplete-only retry（2026-08-21e）

与「空 tool escalate」分离：

- 首轮 upstream 以 **incomplete tool block** 结束 → 无论 escalate 开关，做 **1 次** forced（required）重试。
- 首轮只是「有 prose、无 tool」→ **不重试**（默认）。需要旧行为时设 `MAXAPI_TOOL_ESCALATE=1`。
- 流式 `_need_buf` / prefetch-until-tool 仅在 escalate 开启时阻塞 TTFB。

证据：`_runtime/v4_evidence/goal_cc_firsthit_20260821e.json`（action_stream ~6.7s vs 旧 escalate 路径 ~143s）。

## 5. 怎么量（探针纪律）

- 只打 `127.0.0.1:8080`，Authorization 用独立标识；**不抢**用户 Codex/57321。  
- 对比矩阵建议：  
  - 纯文本 short（无 tools）  
  - 同 prompt + tools auto（动作型）  
  - 同 prompt + `tool_choice=none`  
  - `reasoning_effort=off` vs 默认  
- 日志关键字：`tool-escalate`、`[busy]`、`identity retire`、rid、model、耗时。  
- 成功标准（产品）：动作型 auto **有** tool；纯聊天 **不**无故多轮上游；热路径体感接近单次上游。

## 6. 改策略时的验收

见 [dev-contract.md](./dev-contract.md) §3.3：

- 动作型 auto 有 tool；纯聊天不误强制  
- 成功路径总 RTT 可接受  
- mid-flight 已完成门控仍在  
- 无 BrokenPipe；无无意义 escalate×3 风暴  

## 7. 明确不拿来「优化延迟」的手段

- 删测试 / 放宽「必须有 tool」断言  
- 关 parser hold-back  
- 把 busy 当 quota 洗身份  
- 整文件重写并发框架  
- 为慢而引入 Admin 队列 UI  

## 8. 卡点 B 续做入口（文档登记）

未关闭。优先取证：一次慢请求的 rid 时间线（TTFB、是否 escalate、effort、是否 tools）。  
有时间线再改：prompt 首轮命中率 > 调 max_wait > 调默认 effort > 调 concurrency。  
无 live 时间线禁止大补丁。
