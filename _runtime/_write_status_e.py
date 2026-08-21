# -*- coding: utf-8 -*-
from pathlib import Path
p = Path("STATUS.md")
t = p.read_text(encoding="utf-8")

# prepend new top quote after title
old_head = """# maxapi STATUS

> 2026-08-21d: **Goal G-A/B/C/S = 100%（Done 定义）**。正式 `docker build`+run；sha=`791a15fa…`。G-A：双通道 dedupe + sol `<|DSML|…|>` 方言 normalize；多模型 6/6 auto 有 tool、leaks=[]、无双发。G-B：off→low、incomplete escalate、顺序矩阵数秒级。G-C/S 终局 sha 四套全绿：gold **20/20** · tool **28/28** · compat **96/96** · agent_long **17/17**。v4 `verdict=insufficient-evidence` fail=0（**非本 Goal** E3）。证据 `goal_ga_*` / `goal_gb_*` / `v4_consistency_report.json`。
>
> 2026-08-21c: G-B/G-C 推进——off→low；incomplete escalate；四套回归绿。
>
> 2026-08-21: G-A 双通道 dedupe；文档 L0–L5。史 → `docs/archive/STATUS-history-2026-08.md`。
"""

new_head = """# maxapi STATUS

> 2026-08-21e: **CC first-hit 工具路径**。空 tool escalate **默认关**（`MAXAPI_TOOL_ESCALATE=0`）；流式不再 prefetch-wait 空等；compact 首轮 prompt（ds2/c2a 风）+ ` ```json action` / JSON 数组 sieve + residue strip；**incomplete tool** 单独 1 次 forced retry（与空 tool 阶梯分离）。sha=`18769b4a…` sanitizer=`…v5-20260821-cc-inc`。live CC 探针 4/4：action_stream **~6.7s/ttfb0.7**（此前 ~143s）、leaks=[]；四套 gold20+compat96+tool28+agent17。证据 `goal_cc_v5_20260821.json` / `goal_cc_firsthit_20260821e.json`。
>
> 2026-08-21d: **Goal G-A/B/C/S = 100%（Done 定义）**。正式 `docker build`+run；sha=`791a15fa…`。G-A：双通道 dedupe + sol `<|DSML|…|>` 方言 normalize；多模型 6/6 auto 有 tool、leaks=[]、无双发。G-B：off→low、incomplete escalate、顺序矩阵数秒级。G-C/S 终局 sha 四套全绿：gold **20/20** · tool **28/28** · compat **96/96** · agent_long **17/17**。v4 `verdict=insufficient-evidence` fail=0（**非本 Goal** E3）。证据 `goal_ga_*` / `goal_gb_*` / `v4_consistency_report.json`。
>
> 2026-08-21c: G-B/G-C 推进——off→low；incomplete escalate；四套回归绿。
>
> 2026-08-21: G-A 双通道 dedupe；文档 L0–L5。史 → `docs/archive/STATUS-history-2026-08.md`。
"""
if old_head not in t:
    raise SystemExit("head mismatch")
t = t.replace(old_head, new_head, 1)

old0 = """## 0. 一句话现状

单文件 `maxapi_server.py`：se.zzmax.cn **访客旁路** → OpenAI Chat / Anthropic Messages / OpenAI Responses。工具 = DSML + 双通道 `ToolCallParser` + auto escalate。**当前 Goal（Agent drop-in 三卡点）已达 Done 定义**。v4 mustE3 canary 仍 unknown（另任务，不挡 Agent 使用）。

**产品口径**：API 化 = Agent drop-in；**不做**多用户/Admin/账号池产品（→ NewAPI）。

### 真跑（改协议后最小集）

```bash
curl -sS http://127.0.0.1:8080/healthz
# binarySha256 须与主机 maxapi_server.py 一致

python -u _openai_sdk_gold.py          # 20/20
python _local_compat_check.py          # FAIL 0
python _accept_tool_suite.py           # FAIL 0
python _accept_agent_long.py           # FAIL 0

python -u _runtime/v4_probe_deployed.py --out _runtime/v4_evidence/deployed_artifact.json
python -u _runtime/v4_audit.py build-report --out _runtime/v4_consistency_report.json
python -u _runtime/v4_audit.py validate --report _runtime/v4_consistency_report.json
```

证据：`_runtime/v4_consistency_report.json`、`_runtime/v4_evidence/goal_ga_*.json`、`goal_gb_latency_20260821.json`、`goal_done_100_20260821d.json`（终局摘要 + `logs_20260821d/`）。
"""

new0 = """## 0. 一句话现状

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
"""
if old0 not in t:
    raise SystemExit("sec0 mismatch")
t = t.replace(old0, new0, 1)

# means table
t = t.replace(
    "| Agent drop-in | 访客 XFF + DSML + sieve + escalate | schema compact、tool-fixer、escalate 预算、TTFB 日志 |",
    "| Agent drop-in | 访客 XFF + compact DSML/json-action + sieve；escalate **默认关** | tool-fixer、refusal 预算、长 tool continue（c2a） |",
)

# change log
t = t.replace(
    "- **2026-08-21d**：Goal Done 勾选 100%（G-A/B/C/S）；E3 仍非本 Goal。",
    "- **2026-08-21e**：CC first-hit——escalate 默认关 + compact prompt + json-action sieve + incomplete-only retry；四套回归保持。\n"
    "- **2026-08-21d**：Goal Done 勾选 100%（G-A/B/C/S）；E3 仍非本 Goal。",
)

# section 1.2 tools
old12 = """- auto 阶梯：escalate → terminal-force；sol mid-flight completion 门控
- prefetch：仅 `saw_tool` 才接受 escalate 缓冲；`max_wait` 自首个非 reasoning 起算（5s）
- 客户端 `reasoning_effort=off` → 上游 **`low`**（真 off 会 529）
"""
new12 = """- **空 tool escalate 默认关**（`MAXAPI_TOOL_ESCALATE` default False）；流式 `_need_buf` 同步门控 → 不再空等多上游
- incomplete tool block：**独立** 1 次 forced retry（不依赖空 tool 阶梯）
- compact 首轮 prompt（签名 + 首选 DSML + 接受 json-action/数组）；双通道 sieve + residue strip
- 客户端 `reasoning_effort=off` → 上游 **`low`**（真 off 会 529）
"""
if old12 not in t:
    raise SystemExit("1.2 mismatch")
t = t.replace(old12, new12, 1)

# 1.4 table - update rows
old14 = """| 套件 | 结果 | 记录日 |
|------|------|--------|
| `_openai_sdk_gold.py` | **20/20**（rebuild 后 sha `791a15fa`） | 2026-08-21d |
| `_local_compat_check.py` | **96/96 FAIL 0**（终局 sha） | 2026-08-21d |
| `_accept_tool_suite.py` | **28/28**（`/tmp/tool_100b.log`） | 2026-08-21d |
| `_accept_agent_long.py` | **17/17**（终局 sha，`/tmp/agent_final2.log`） | 2026-08-21d |
| v4 validate | **valid**，verdict=`insufficient-evidence`，fail=0，unknown=18（mustE3） | 2026-08-21d |
| 多模型 tool 抽测 | 6/6 auto≥1 tool，leaks=[]，dup=0 | 2026-08-21d |
"""
new14 = """| 套件 | 结果 | 记录日 |
|------|------|--------|
| `_runtime/_probe_cc_v5.py` | **4/4** action_stream ~6.7s ttfb0.7 leaks=[] | 2026-08-21e |
| `_openai_sdk_gold.py` | **20/20**（sha `18769b4a`） | 2026-08-21e |
| `_local_compat_check.py` | **96/96 FAIL 0** | 2026-08-21e |
| `_accept_tool_suite.py` | **28/28** | 2026-08-21e |
| `_accept_agent_long.py` | **17/17** | 2026-08-21e |
| v4 validate | **valid**，verdict=`insufficient-evidence`，fail=0，unknown=18（mustE3） | 2026-08-21d |
| 多模型 tool 抽测 | 6/6 auto≥1 tool，leaks=[]，dup=0 | 2026-08-21d |
"""
if old14 not in t:
    raise SystemExit("1.4 mismatch")
t = t.replace(old14, new14, 1)

# section 2 residual
old2 = """### 非本 Goal 仍开

1. **v4 mustE3 canary/soak** → 挡 `verdict=pass`，不挡 Agent 日常（remediation #1）
2. 上游 busy 在**高并发**压测时仍拉长单次 RTT（运维/concurrency，非协议双 RTT 无故 escalate）
3. 极端未收录方言 / 模型偶发不遵从 DSML（escalate 兜底）
"""
new2 = """### 非本 Goal 仍开

1. **v4 mustE3 canary/soak** → 挡 `verdict=pass`，不挡 Agent 日常（remediation #1）
2. 上游 busy / 单次 RTT 抖动（运维/concurrency；**已不再**用空 tool 多上游 escalate 放大）
3. 模型偶发不遵从 tool 格式 → 默认**不再**自动二跳；可用 env `MAXAPI_TOOL_ESCALATE=1` 临时开回；长期靠 prompt/parser（c2a fixer 未移植）
4. c2a 级 tool-fixer / 长 tool 续写 / refusal 预算 — 未做，非阻塞主路径
"""
if old2 not in t:
    raise SystemExit("sec2 mismatch")
t = t.replace(old2, new2, 1)

old3 = """## 3. 下一步（Goal 已完成后）

1. 用户要求时 `git commit`（勿提交密钥）。
2. 可选另任务：E3 canary → v4 `verdict=pass`。
3. 可选：默认 effort / concurrency 运维调参。
"""
new3 = """## 3. 下一步（Goal 已完成后）

1. 用户要求时 `git commit`（勿提交密钥）。
2. 在真实 Claude Code 会话再体感一轮（本机 8080 已部署 v5-cc-inc）。
3. 可选另任务：E3 canary → v4 `verdict=pass`；c2a tool-fixer。
"""
if old3 not in t:
    raise SystemExit("sec3 mismatch")
t = t.replace(old3, new3, 1)

old4 = """| 项 | 内容 |
|----|------|
| 已完成 | 文档 L0–L5；G-A dedupe+sol 方言；G-B latency；G-C 回归；正式 docker build；多模型抽测；v4 复验；终局 sha 四套全绿 gold20+tool28+compat96+agent17 |
| 位置 | **当前 Goal 100%（Done 定义）** |
| 还差 | 仅 Goal **外**：E3 canary、commit（听你的） |
| 下一步 | 等指示 commit / 开 E3 任务 / 结束 |
"""
new4 = """| 项 | 内容 |
|----|------|
| 已完成 | 文档 L0–L5；G-A/B/C/S Done；**2026-08-21e** CC first-hit（escalate 默认关 + compact prompt + json-action + incomplete-only retry）；四套全绿；CC 探针 4/4 ~6–7s |
| 位置 | Goal Done + CC first-hit 落地（sha `18769b4a`） |
| 还差 | Goal **外**：E3 canary；可选 c2a fixer；commit（听你的） |
| 下一步 | 真 CC 体感 / commit / E3 |
"""
if old4 not in t:
    raise SystemExit("sec4 mismatch")
t = t.replace(old4, new4, 1)

old5 = """## 5. 环境与指纹（2026-08-21d）

| 项 | 值 |
|----|-----|
| 探针 | `http://127.0.0.1:8080/healthz` |
| healthz.binarySha256 | `791a15fa9ba9107b3b874bec862ba86fecf23a16bfa52f08552de3d3b04f4dd9` |
| healthz.commit | `ee64eef76d9c3831b0dad564636c6a4ea5178a7a` |
| healthz.models | 11 |
| 部署 | `docker build -t maxapi-server:latest .` + `docker run --name maxapi -p 8080:8080` |
| 主机 sha | `791a15fa…`（**==** 容器） |
| 工作树 | `M maxapi_server.py` + docs + `_runtime/v4_evidence/*`，**未 commit** |
"""
new5 = """## 5. 环境与指纹（2026-08-21e）

| 项 | 值 |
|----|-----|
| 探针 | `http://127.0.0.1:8080/healthz` |
| healthz.binarySha256 | `18769b4ac6b555ab0a55085b53319c0e75d40bc3e3c6031b008fda739258697d` |
| healthz.sanitizerConfigVersion | `markers-toolcallparser-v5-20260821-cc-inc` |
| healthz.models | 11 |
| 部署 | `docker cp maxapi_server.py maxapi:/app/` + `docker restart maxapi`（或正式 rebuild） |
| 主机 sha | `18769b4a…`（**==** 容器） |
| 工作树 | `M maxapi_server.py` + docs + `_runtime/v4_evidence/*`，**未 commit** |
"""
if old5 not in t:
    raise SystemExit("sec5 mismatch")
t = t.replace(old5, new5, 1)

p.write_text(t, encoding="utf-8", newline="\n")
print("STATUS updated", len(t.splitlines()), "lines")
