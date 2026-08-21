# 参考项目借鉴对照（cursor2api / ds2api）

> 2026-08-21 落盘。对照树：`Desktop/cursor2api`、`C:/Users/Administrator/ds2api`。  
> **只借 wire / tool / 观测 / 文档分层**；**不借** Admin、账号池产品、WebUI、多租户。

## 1. 三项目角色

| 项目 | 上游 | 形态 | 对 maxapi 的意义 |
|------|------|------|------------------|
| **maxapi** | se.zzmax 访客 SSE | 单文件 Python | 本仓库；Agent drop-in；多用户→NewAPI |
| **cursor2api** | Cursor 文档页 chat | TS 模块化 | Agent wire 与 tool 诱导/拒答/截断续写经验 |
| **ds2api** | DeepSeek 网页 | Go 模块 + Admin/池 | promptcompat / toolstream / 文档分层；**池与 Admin 不抄产品** |

## 2. 已在 maxapi 对齐的（不必再「引进」当新项目）

| 能力 | maxapi 现状 | 参考来源 |
|------|-------------|----------|
| DSML + 流式 sieve | `ToolCallParser`、`_make_tools_prompt` | ds2api `toolstream` / `toolcall` 思路（已移植） |
| 双通道 think+answer 过 parser | `upstream()` 内 tparser×2 | 自研血泪；ds2api 亦强调 sieve 唯一路径 |
| hold-back / EOF salvage | `_find_partial`、incomplete flush | ds2api toolstream holdback + maxapi R3 |
| OpenAI wire P0 | 404 model、tool 分片、include_usage、n=1、501 embeddings | 与 cursor/ds2api/2api v4 同族 |
| 三端点 | chat / messages / responses | 三者皆有 |
| 上下文 compact | `compact_request` + EWMA | 类 cursor 压缩/ds2 长上下文，实现不同 |
| 文档 L0–L5 | STATUS / docs/* / _runtime / archive | **借 ds2api 文档分层，不借 Admin 专章** |

## 3. 值得借鉴且映射三卡点（采用清单）

### 卡点 A — Tool 暴露 / 泄漏

| 借鉴 | 来源 | 怎么用在 maxapi | 不做 |
|------|------|-----------------|------|
| schema **compact** 模式（names_only / 紧凑签名） | cursor `converter` `schemaMode` | 工具多时减 prompt 噪声，提高 DSML 遵从 | 不换上游 |
| tool 参数 **fixer**（别名、类型、fuzzy） | cursor `tool-fixer.ts` | 抽出后、下发前增强 coercion（已有基础 coercion） | 不放开非法 name |
| 五层/容错 JSON 与代码块内 JSON | cursor | 加固 arguments 解析边角 | 不放宽 catalog |
| 拒答/空 tool 时 **有限** synthesizer 或加强 forced 文案 | cursor refusal + required 路径；2api-bridge | 仅 `tool_choice=required` 或动作型 auto 失败后；预算次数 | 不假造客户端未声明的 tool |
| promptcompat 历史展平单测矩阵 | ds2api `promptcompat/*_test` | 给 `_build_messages_with_tools` 加方言 fixture | 不整模块 Go 重写 |
| unknown marker / 方言表可扩展 | ds2api `unknown_marker` | 新泄漏样例进 fixture + parser 最小补丁 | 不无样例堆正则 |

### 卡点 B — 慢

| 借鉴 | 来源 | 怎么用在 maxapi | 不做 |
|------|------|-----------------|------|
| **先量 TTFB / 分桶** 再改 | 2api-bridge + cursor metrics | rid 时间线：是否 escalate、effort、tools | 盲加 timeout |
| 默认关重活（web search / 高 thinking） | notion2api/2api-bridge 经验 | 默认 effort 可评估降为 `low`/`off`（需 live 对比）；search 仅客户端显式 | 不关用户显式 high |
| refusal/escalate **预算**（次数+已发正文则停） | cursor `refusal-budget` | 与 escalate ladder 对齐：已 partial 且完成门控 → 禁止再 force | 不无限 ladder |
| 观测：escalate 计数、TTFB | cursor `metrics.ts`；ds2 OBS | 日志字段结构化（rid, escalate_n, ttfb_ms） | 不上 Admin 图表产品 |
| 截断续写 vs 二次上游 | cursor truncation continue | 仅确认「真截断」再续；与 tool escalate 分路径 | 不把续写当 tool 补丁 |

### 卡点 C — Agent wire（像官方 API）

| 借鉴 | 来源 | 怎么用在 maxapi | 不做 |
|------|------|-----------------|------|
| 逻辑分层清晰（即使单文件） | ds2api `httpapi` / `promptcompat` / `toolstream` / `assistantturn` | 文档符号表已建；改动按边界，禁止顺手拆 Admin | **不**为像 ds2 而拆成 Go 式大仓 |
| assistantturn 统一终止语义 | ds2api `assistantturn` | 已有 finalize 六类；保持唯一收尾 | 多套 finalize |
| SDK golden 为 wire 真源 | 两者 + maxapi gold 20 | 保持 `_openai_sdk_gold.py` 门禁 | 手写 curl 代替 SDK |
| 身份/system bypass 分路径 | cursor converter + 2api-bridge | se.zzmax 身份锁较弱；仅当 live 拒答再加 **顺应式** reframe（禁止对抗「你不是网页助手」） | 不把越狱当卖点 |
| NewAPI 前置 | 用户裁定 | 文档写死；配额/多 key 不进 maxapi | 账号池/WebUI |

## 4. 明确不借鉴（产品/范围）

| 项 | 来源 | 原因 |
|----|------|------|
| Admin WebUI / 账号管理页 | ds2api `httpapi/admin`、webui | 用户：多用户→NewAPI |
| account pool / mute / 切号产品 | ds2api `internal/account` | 访客 XFF 已是额度手段；不做托管号池产品 |
| 公网多 token 租户体系 | cursor auth_tokens 产品化 | 单实例适配器足够；鉴权保持简单 |
| 全链路日志 Web UI | cursor log-viewer | 非目标；要诊断用日志/rid |
| 双语镜像文档大全 | ds2api *.en.md | 维护成本；中文 + 代码为准 |
| 整文件重写成 TS/Go 模块仓 | 两者外观 | 红线：最小 diff，单文件可演进但不借机重写 |

## 5. 采纳优先级（协议阶段，非本文档任务）

```text
P0  live 复现 A/B 时间线（8080，指纹对齐）
P1  A：泄漏样例 → parser/fixture 最小补丁；required 空 tool 路径
P1  B：escalate 预算 + 默认 effort/search 分桶对比；日志 escalate_n/ttfb
P2  A：schema compact 可选开关（工具多时）
P2  C：gold/compat/agent_long 全绿 + 文档 known deviations 同步
P3  可选 obs 字段；E3 canary（v4 verdict=pass）
禁止  Admin/池/拆仓重写
```

## 6. 与 STATUS 的关系

- 本文件 = **手段菜单**（改协议时查阅）。  
- 目标与勾选只在 [`../STATUS.md`](../STATUS.md) **§0.5**。  
- 工程红线：[`dev-contract.md`](./dev-contract.md)。
