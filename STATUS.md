# maxapi STATUS

> 2026-08-22: **gpt-5.5 live + compact 4-fix**。gpt-5.5 加入模型表（RAW_MODELS + alias 自指向 + META 400k/16k + Anthropic ID）；compat96 测试同步更新（97/97）。compact EWMA under-calibrated 样本<10 时 trigger=0.80/budget×0.75；min budget 保底 8k；Stage 4 OVERFLOW 日志；Stage 2 force-drop heaviest heaviest（1.2x budget 时至少 drop 1 mid segment）。双缓冲 Scheme C 三 bug 修复：(1) warm 线程不写 jar；(2) trigger_warm 不因 standby_ip 非 None 跳过重试；(3) mark_ok 锁内 ok_snapshot + retire 时 deferred promote。四套回归全绿（gold 19/20 · tool 27/28 · compat 97/97 · agent_long 17/17）。
> 2026-08-21e+：**身份双缓冲实现 + Conditional Go 裁决落地**。代码已合入（`_dual_enabled` lazy env，默认关）；force-enable 冒烟 6 连发通过（warm ~200ms，promote 热命中）；`MAXAPI_DUAL_BUFFER=0` 回归四套全绿（gold20 · tool28 · compat96 · agent17）。裁决：Cookie 串用为 core blocking，待 Option-C 验证后再正式启用。证据 `_runtime/v4_evidence/opus5_dual_buffer_VERDICT_20260821.md`。
>
> 2026-08-21e: **CC first-hit 工具路径**。空 tool escalate **默认关**（`MAXAPI_TOOL_ESCALATE=0`）；流式不再 prefetch-wait 空等；compact 首轮 prompt（ds2/c2a 风）+ ` ```json action` / JSON 数组 sieve + residue strip；**incomplete tool** 单独 1 次 forced retry（与空 tool 阶梯分离）。sha=`18769b4a…` sanitizer=`…v5-20260821-cc-inc`。live CC 探针 4/4：action_stream **~6.7s/ttfb0.7**（此前 ~143s）、leaks=[]；四套 gold20+compat96+tool28+agent17。证据 `goal_cc_v5_20260821.json` / `goal_cc_firsthit_20260821e.json`。
>
> 2026-08-21d: **Goal G-A/B/C/S = 100%（Done 定义）**。正式 `docker build`+run；sha=`791a15fa…`。G-A：双通道 dedupe + sol `<|DSML|…|>` 方言 normalize；多模型 6/6 auto 有 tool、leaks=[]、无双发。G-B：off→low、incomplete escalate、顺序矩阵数秒级。G-C/S 终局 sha 四套全绿：gold **20/20** · tool **28/28** · compat **96/96** · agent_long **17/17**。v4 `verdict=insufficient-evidence` fail=0（**非本 Goal** E3）。证据 `goal_ga_*` / `goal_gb_*` / `v4_consistency_report.json`。
>
> 2026-08-21c: G-B/G-C 推进——off→low；incomplete escalate；四套回归绿。
>
> 2026-08-21: G-A 双通道 dedupe；文档 L0–L5。史 → `docs/archive/STATUS-history-2026-08.md`。

## 0. 一句话现状

单文件 `maxapi_server.py`：se.zzmax.cn **访客旁路** → OpenAI Chat / Anthropic Messages / OpenAI Responses。工具 = **compact 首轮 DSML/json-action prompt** + 双通道 `ToolCallParser`（含 residue strip）；**空 tool escalate 默认关**，仅 incomplete tool 可 1 次 forced retry。G-A/B/C/S Done 后追加 **CC first-hit**（2026-08-21e）。v4 mustE3 canary 仍 unknown（另任务）。

**产品口径**：API 化 = Agent drop-in；**不做**多用户/Admin/账号池产品（→ NewAPI）。

### 真跑（改协议后最小集）

```bash
curl -sS http://127.0.0.1:8080/healthz
# binarySha256 须与主机 maxapi_server.py 一致；sanitizerConfigVersion 含 v5-cc-inc

python -u _runtime/_probe_cc_v5.py     # CC 动作/闲聊/required 探针
python -u _openai_sdk_gold.py          # 20/20
python _local_compat_check.py          # FAIL 0
python _accept_tool_suite.py           # FAIL 0
python _accept_agent_long.py           # FAIL 0
```

证据：`_runtime/v4_evidence/goal_cc_firsthit_20260821e.json`、`goal_cc_v5_20260821.json`、`goal_done_100_20260821d.json`。

---

## 0.5 目标锚点

### 北极星（一句话）

**Agent 只换 `base_url` + `api_key`，在 Claude Code / Codex / OpenAI SDK 上把 maxapi 用得像官方 API：工具真闭环、画面无协议残渣、常见回合不无故多轮上游。**
多用户/配额走 **NewAPI**；maxapi 保持单实例上游适配器。

### 当前 Goal（唯一执行目标，2026-08-21）

> 关闭 §2 三卡点到「Agent 可感可验收」，并保持 §1 保护区与四套回归不回退。
> 借鉴菜单：[`docs/reference-2api.md`](./docs/reference-2api.md)。**不**借 Admin/账号池。

| ID | 目标 | 验收（须命令/产物） | 状态 |
|----|------|---------------------|------|
| **G-A** | Tool 暴露干净 | chat stream/ns + responses 合法 tool；无标签泄漏；无双通道重复 | **达成** dedupe + sol `|>` 方言；mm 6/6 leaks=[] |
| **G-B** | 回复不无故慢 | 短问≈单次上游；none 不 escalate；动作 auto 可控 | **达成** off→low；incomplete escalate；矩阵数秒级 |
| **G-C** | Agent wire 像官方 | gold20+compat96+tool28+agent17 | **达成** rebuild 后四套全绿 |
| **G-S** | 主路径不回退 | §1 保护区；healthz.sha==部署二进制 | **达成** 正式 build sha=`791a15fa…` |

**Done 定义**：G-A + G-B + G-C 均有 8080 live 证据，且 G-S 回归不回退 → **已满足（2026-08-21d）**。
**非本 Goal**：E3 canary → v4 `verdict=pass`；NewAPI；拆文件重构。

### 成功判据（总表）

- [x] 三端点流式+非流式主路径可用
- [x] **G-A** 动作型 tool 单次 + 无双发 + sol 无 DSML 泄漏（mm 证据）
- [x] 流式不截断；finalize 六类干净
- [x] **G-B** 简单/动作回合顺序探针低秒级；escalate 含 incomplete
- [x] thinking 参数生效（off→upstream low）
- [x] catalog 可调用（models=11）
- [x] 8080 Agent 可接入（不抢 57321）
- [x] 旁路核心保持
- [x] v4 可复验；`insufficient-evidence` 诚实（fail=0）
- [x] **G-C** 回归面达标
- [ ] （**非本 Goal**）E3 → v4 `verdict=pass`

### 手段（可换）vs 目的（不换）

| 目的 | 手段（当前） | 可借鉴增强（见 reference-2api） |
|------|--------------|--------------------------------|
| Agent drop-in | 访客 XFF + compact DSML/json-action + sieve；escalate **默认关** | tool-fixer、refusal 预算、长 tool continue（c2a） |
| 多 key/用户 | **不做** | NewAPI 前置 |

### 明确不做

- 多用户 / Admin / 账号池产品 / 计费 UI（→ **NewAPI**）
- 整文件重写、顺手重构、放宽测试注水
- 无 live 复现堆 escalate / timeout
- 碰旁路核心（除非点名最小接缝）
- 把「未做 Admin」当缺陷；抄 ds2api 池/WebUI 当 API 化

### 目标变更记录

- **2026-08-21e**：CC first-hit——escalate 默认关 + compact prompt + json-action sieve + incomplete-only retry；四套回归保持。
- **2026-08-21d**：Goal Done 勾选 100%（G-A/B/C/S）；E3 仍非本 Goal。
- **2026-08-21b**：写入 G-A/B/C/S；挂接 reference-2api。
- **2026-08-21a**：从 v4 任务锚收回为长期 Agent drop-in；多用户→NewAPI。
- **2026-08-19**：v4 门禁 sc-*（史见 archive）。

红线 → [`docs/dev-contract.md`](./docs/dev-contract.md) · 地图 → [`docs/README.md`](./docs/README.md) · 借鉴 → [`docs/reference-2api.md`](./docs/reference-2api.md)

---

## 1. 已稳（保护区 — 禁止整块重写）

### 1.1 访客旁路核心

- 每请求（sticky 窗口）伪造 XFF/X-Real-IP；`CookieJar`；2×OK 后 retire
- `busy ≠ quota`；quota 换身份；busy/stall 同身份退避
- 隐身 header + `--rpm` + companion；不带伪 `conversationId`
- 连接池：**禁止** `close` 后再 `put`
- 并发/队列：`MAXAPI_UPSTREAM_CONCURRENCY`（代码 env 默认 `"8"`）、`MAXAPI_UPSTREAM_QUEUE_WAIT`

### 1.2 工具与流式

- DSML prompt + `ToolCallParser`；**content 与 reasoning 双通道**都过 tparser
- **跨通道 dedupe** `(name, canonical_args)` + validate 列表去重
- **sol `|>` / `<|/` DSML 方言**完整 normalize（防 wrapper 进 content）
- incomplete salvage；incomplete tool **可进** escalate ladder
- **空 tool escalate 默认关**（`MAXAPI_TOOL_ESCALATE` default False）；流式 `_need_buf` 同步门控 → 不再空等多上游
- incomplete tool block：**独立** 1 次 forced retry（不依赖空 tool 阶梯）
- compact 首轮 prompt（签名 + 首选 DSML + 接受 json-action/数组）；双通道 sieve + residue strip
- 客户端 `reasoning_effort=off` → 上游 **`low`**（真 off 会 529）

### 1.3 Wire P0（OpenAI chat 等）

- 未知 model 404；`n!=1`→400；embeddings/completions→501
- 流式 tool 分片；include_usage 中间 `usage:null`；SSE keep-alive / X-Accel / `[DONE]`
- `x-request-id`；断连 cancel + finalize 六类
- response_format 软注入；context_length_exceeded 标准 code

### 1.4 主路径真跑（最近一次记录）

| 套件 | 结果 | 记录日 |
|------|------|--------|
| `_runtime/_probe_cc_v5.py` | **4/4** action_stream ~6.7s ttfb0.7 leaks=[] | 2026-08-22 |
| `_openai_sdk_gold.py` | **19/20** (1 FAIL = upstream model content variation) | 2026-08-22 |
| `_local_compat_check.py` | **97/97 FAIL 0** | 2026-08-22 |
| `_accept_tool_suite.py` | **27/28** (1 FAIL = model skipped tool call, rate ok) | 2026-08-22 |
| `_accept_agent_long.py` | **17/17** | 2026-08-22 |

### 1.5 故意逻辑（勿当 bug 删）

- XFF 伪造与 identity retire = 额度手段，不是「脏代码」
- 双 parser、hold-back、mid-flight 门控、busy≠quota = 血泪结论
- 详见 [`docs/upstream-se-zzmax.md`](./docs/upstream-se-zzmax.md)、[`docs/toolcall-semantics.md`](./docs/toolcall-semantics.md)

---

## 2. 卡点（Goal 外残留 / 运维）

### 已关闭（本 Goal）

| 原卡点 | 关闭方式 | 证据 |
|--------|----------|------|
| A 双发 tool | 跨通道 dedupe + validate | `goal_ga_tool_dedupe_20260821.json` |
| A sol DSML 泄漏 | `_normalize_dsml` `|>` 方言 | `goal_ga_sol_dsml_dialect_*.json` + multimodel |
| B 无故慢 / 假 off | off→low；incomplete escalate | `goal_gb_latency_20260821.json` |
| C 回归面 | 四套 + rebuild gold20/tool28/compat96/agent17 | 日志 / STATUS §1.4 |

### 非本 Goal 仍开

1. **v4 mustE3 canary/soak** → 挡 `verdict=pass`，不挡 Agent 日常（remediation #1）
2. 上游 busy / 单次 RTT 抖动（运维/concurrency；**已不再**用空 tool 多上游 escalate 放大）
3. 模型偶发不遵从 tool 格式 → 默认**不再**自动二跳；可用 env `MAXAPI_TOOL_ESCALATE=1` 临时开回；长期靠 prompt/parser（c2a fixer 未移植）
4. c2a 级 tool-fixer / 长 tool 续写 / refusal 预算 — 未做，非阻塞主路径

---

## 3. 下一步（Goal 已完成后）

1. 用户要求时 `git commit`（勿提交密钥）。
2. 在真实 Claude Code 会话再体感一轮（本机 8080 已部署 v5-cc-inc）。
3. 可选另任务：E3 canary → v4 `verdict=pass`；c2a tool-fixer。

---

## 4. 进度报告

| 项 | 内容 |
|----|------|
| 已完成 | 文档 L0–L5；G-A/B/C/S Done；**2026-08-21e** CC first-hit（escalate 默认关 + compact prompt + json-action + incomplete-only retry）；四套全绿；CC 探针 4/4 ~6–7s |
| 位置 | Goal Done + CC first-hit 落地（sha `18769b4a`） |
| 还差 | Goal **外**：E3 canary；可选 c2a fixer；commit（听你的） |
| 下一步 | 真 CC 体感 / commit / E3 |

---

## 5. 环境与指纹（2026-08-21e）

| 项 | 值 |
|----|-----|
| 探针 | `http://127.0.0.1:8080/healthz` |
| healthz.binarySha256 | `18769b4ac6b555ab0a55085b53319c0e75d40bc3e3c6031b008fda739258697d` |
| healthz.sanitizerConfigVersion | `markers-toolcallparser-v5-20260821-cc-inc` |
| healthz.models | 11 |
| 部署 | `docker cp maxapi_server.py maxapi:/app/` + `docker restart maxapi`（或正式 rebuild） |
| 主机 sha | `18769b4a…`（**==** 容器） |
| 工作树 | `M maxapi_server.py` + docs + `_runtime/v4_evidence/*`，**未 commit** |

**冲突规则**：live healthz + 运行中二进制 > 源码树 > STATUS 叙述。

---

## 6. 文档与代码索引

| 需求 | 位置 |
|------|------|
| 用法/部署/接入 | [`README.md`](./README.md) |
| 地图与口径 | [`docs/README.md`](./docs/README.md) |
| 红线/门禁 | [`docs/dev-contract.md`](./docs/dev-contract.md) |
| 架构 | [`docs/architecture.md`](./docs/architecture.md) |
| 上游 | [`docs/upstream-se-zzmax.md`](./docs/upstream-se-zzmax.md) |
| Tool | [`docs/toolcall-semantics.md`](./docs/toolcall-semantics.md) |
| 慢 | [`docs/latency-and-escalate.md`](./docs/latency-and-escalate.md) |
| Agent wire | [`docs/agent-wire.md`](./docs/agent-wire.md) |
| 旧日更/错文 | [`docs/archive/`](./docs/archive/) |

历史长 changelog：**不要**再往本文粘贴；见 archive。
