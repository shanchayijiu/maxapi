# maxapi 目标宪章（每次改动必看）

> **硬规矩：动 `maxapi_server.py` / Docker / 协议路径之前，先通读本文。**  
> 改完对照文末「完成判定」打勾；对不上 = 没做完，不许声称完成、不许顺手加戏。  
> 本文是用户要求的单一真相源；与 STATUS §1 保护区叠加，冲突时以**更严**的为准。

最后整理：2026-08-20（叠加 2api v4 诚实门禁；wire 回归口径不变）。

---

## 0. 一句话目标

**让 Claude Code（及 Codex 经 8080）用本地 maxapi 时，体感接近直连官方 API：工具、多轮、thinking、流式都稳，没有协议形缺陷，也不比直连无故慢一截。**

不是「功能清单越长越好」，是 **稳、对、快、可回归**。

---

## 1. 产品目标（必须始终对齐）

| # | 目标 | 可观察标准 |
|---|------|------------|
| G1 | **三端点协议完整** | `/v1/messages`（Claude Code）、`/v1/chat/completions`、`/v1/responses`（Codex `wire_api=responses`）流式+非流式均可用 |
| G2 | **工具调用真闭环** | auto/required/forced 能产出结构化 `tool_use` / `tool_calls` / `function_call`；客户端能执行再回传；**禁止** DSML/`<tool_call>` 原文泄漏到用户可见 content/reasoning |
| G3 | **流式不截断** | 禁止「说一句就断」：已 yield 部分内容后，不得再甩 fatal error / 半截 SSE / 无 `[DONE]`；stall/incomplete 在已有输出时干净收尾 |
| G4 | **速度可接受** | 简单工具回合不应无故 2～3 倍上游 RTT；禁止 escalate/terminal-force 风暴、thinking 误触发 max_wait、死连接池 BrokenPipe 重试 |
| G5 | **Thinking 正确** | 用上游真实参数（`reasoningEffort` / 客户端 thinking 开关），**禁止**靠提示词假造思考强度；thinking 通道里的工具标签必须过 parser，不得当正文泄漏 |
| G6 | **多模型可用** | Claude / gpt-sol / deepseek-v4-flash 等 catalog 内模型在 8080 可调用；DeepSeek 原生 tool token 必须能解析 |
| G7 | **Codex 路径可用** | Codex →（可选 57321）→ **8080** 工具+长文正常；修 maxapi 时默认 **不抢用户的 57321**，独立打 8080 探针 |
| G8 | **访客旁路核心不被改坏** | 伪造 XFF/额度轮换/busy≠quota/隐身 header 等是生存能力，review/功能活默认只读（见 STATUS §1 + `review_principle_bypass`） |
| G9 | **2api v4 报告可复验** | `_runtime/v4_consistency_report.json` 四态 verdict 诚实；`deployedArtifact` 与 8080 监听进程指纹一致；无证据不得 pass；mustE3 无 E3 时不得 verdict=pass |

---

## 2. 用户红线（明确禁止）

1. **禁止删测试、放宽断言、减功能、减轮次** 来「让 CI 变绿」。
2. **禁止碰已稳访客旁路核心**（除非用户点名且最小接缝）。
3. **禁止顺手重构**、整文件重写、统一抽象、「顺便现代化」。
4. **禁止为修 A 踩烂 B**：chat stream / responses / messages / escalate / pool 任一侧改动，必须回归另外两侧。
5. **禁止推测式大改**：「越改越垃圾」已发生过——没有 live 复现与根因，不准堆 escalate/prefetch/error 策略。
6. **禁止 ship/agent 被 system notification 自我触发死循环**；纯通知不是新任务。
7. **禁止与用户正在用的 Codex/57321 抢流量**做探针；只用 8080 + 独立 Authorization 标识。
8. **禁止凭印象还原**代码；有 git/bak 用原件，没有就停。
9. **禁止把 incomplete/stall 在已有输出后打成硬错误**（会表现为一句话中断）。
10. **禁止连接池 close 后再 put**（死 socket → BrokenPipe → Codex 全挂）。

---

## 3. 每次改动前门禁（按序执行）

```
[ ] 1. 读 GOALS.md（本文）+ STATUS.md §1 保护区
[ ] 2. 用一句话写清：这次要修的用户可感症状是什么？（不是「优化 XX 模块」）
[ ] 3. 指出最小触碰面：哪个函数/分支；明确「不改」哪些共享层
[ ] 4. 先有复现：8080 live 或单测能红；禁止无复现纯猜测大补丁
[ ] 5. 方案选「最小 diff + 根因」；若要加 retry/timeout/escalate，先证明不是掩盖 bug
```

---

## 4. 每次改动后验收（按改动类型勾选）

### 4.1 必做（任何协议/流式/工具相关改动）

```
[ ] 8080 Docker 已是当前代码（rebuild + MAXAPI_GIT_COMMIT=HEAD；禁止「只改了主机文件」）
[ ] curl /healthz：binarySha256 与主机 maxapi_server.py 一致；X-Maxapi-* 头存在
[ ] 纯文本流式：短答 + 较长列表（如 1..20）不截断、有 finish/[DONE]
[ ] 工具：responses stream + chat stream + chat nonstream 至少各 1 次 auto tool
[ ] 日志：无 BrokenPipe 风暴；无无意义连续 tool-escalate ×3
[ ] 若动 parser/终止路径：跑 v4 L0/finalize/inv03/leak-g/mutants + build-report/validate
[ ] git commit；用户要求时 push；STATUS.md 顶部追加一条日期摘要
```

### 4.2 改了 ToolCallParser / DSML / thinking 通道

```
[ ] 本地 parser 单测：DSML invoke、singular JSON、DeepSeek fullwidth token、chunked feed
[ ] thinking 内嵌 tool 能抽出 tool_call，且 reasoning 无原始标签泄漏
[ ] `_local_compat_check.py`（若环境可跑）或等价断言
```

### 4.3 改了 escalate / prefetch / terminal-force

```
[ ] 动作型 auto 有 tool；纯聊天 auto 不误强制
[ ] prefetch 等待思考时不误判超时；成功路径总 RTT 可接受
[ ] mid-flight「已完成」门控仍在（禁止完成后空转 Write）
```

### 4.4 改了连接池 / 重试 / quota

```
[ ] 连续 5 次请求无 BrokenPipe
[ ] quota 换 identity 后仍能出字；客户端不看到上游中文额度原文
```

### 4.5 全量回归（大改或用户点名 /ship）

```
[ ] python _local_compat_check.py     # 目标 FAIL 0
[ ] python _accept_tool_suite.py      # 目标 FAIL 0
[ ] python _accept_agent_long.py      # 目标 FAIL 0
```

---

## 5. 已知易碎点（改前先查，避免重复踩坑）

| 症状 | 优先查 |
|------|--------|
| Codex/聊天「完全不能用」 | 连接池 close+put、BrokenPipe、57321 模型白名单 ≠ maxapi |
| 有 tools 但客户端收不到 | **chat stream 是否转发 `kind==tool_call`**；think 通道是否过 tparser |
| 工具标签泄漏到画面 | dual parser、flush-discarded、singular/`function_calls` 归一化 |
| 一句话就断 |  partial yield 后还 `yield error`；stall 当 fatal |
| 特别慢 | escalate 阶梯多轮、prefetch max_wait 含 thinking、effort=max、concurrency 过低、quota 连刷 |
| deepseek 有 tool 非流式行、流式不行 | DeepSeek token body（NAME+fenced JSON）解析 + chat stream emit |
| 只修了主机 py，行为没变 | 容器未 cp/restart |

权威历史与保护区细节：`STATUS.md`。  
旁路/伪造代码评审纪律：记忆 `review_principle_bypass`。

---

## 6. 工作方式（用户反复强调）

1. **根因优先**：先证明再改；timeout/retry/escalate 是最后手段，不是第一反应。
2. **最小 diff**：能改函数内几行就不要改架构；默认现有实现是刻意的。
3. **稳大于炫**：用户明确反感「越改越垃圾」——稳定性 hotfix 优先于新策略。
4. **独立探针**：用户说在测 Codex 时，只打 `127.0.0.1:8080`，Authorization 带探针标识，不碰 57321。
5. **文档同步**：大阶段更新 STATUS 顶部 + 本文件（若目标有变）；commit 信息写清用户可感症状。
6. **完成才说完成**：实现 + 正确层级验证 + 低风险收尾；主路径回归挂 = 未完成。

---

## 7. 完成判定（对用户回复前）

同时满足才可说「好了」：

1. 用户提出的**可感症状**在 8080 live 上消失（有命令输出证据）。  
2. 未扩大范围破坏 STATUS §1 已稳能力（至少抽测纯文本 + 一条工具路径）。  
3. 代码已进容器（若用户用 Docker 8080）。  
4. 有 commit（及用户要求的 push/文档）。  
5. 若尚有残留，**明确写出**残留与是否阻塞，不把半成品说成完成。

---

## 8. 非目标（默认不做）

- 替用户改 Codex 安装/57321 白名单（可诊断，不动除非点名）。
- 扩大攻击面、对非任务目标扫盘、泄露无关凭据。
- 为「更像官方」而重写整站协议栈。
- 无用户要求的 performance 微优化、日志美化、目录整理。
