# maxapi STATUS
> 2026-08-20 R7: **2api v4 本地可闭环收口**——`verdict=insufficient-evidence`（fail=0，pass=108，unknown=18 全为 mustE3/无 harness，validate 合法，e3=0）。最长匹配 leak-b 修复 + mutant a/b/c/d/g killed=5；INV-03 ledger / leak-g harness 先红后绿；八漏点 **a–h 全 pass**；live reasoning_content + include_usage shape 复证。deployedArtifact `binarySha256=81d61570…` host==container；image `maxapi-server@sha256:cc20318c…`；commit 进程=ca52270 仍需生产 canary 才能 verdict=pass。

> 2026-08-20 R6: **2api v4 续跑清 blocking**——`verdict=insufficient-evidence`（fail=0，unknown≈94 含 mustE3，validate 合法）。`_finalize_chat_stream` 统一六类终止（INV-13/REQ-SAN-14 先红后绿）。八漏点 a/b/c/d/e/f/h **pass**，g unknown。live include_usage：mid `usage:null` + trail `choices:[]` + `[DONE]`。回归：**compat 96 / gold 20 / tool 28 / agent_long 17**。deployedArtifact `binarySha256=eb0a1401…` host==container；image `maxapi-server@sha256:32775a3c…`。产物已更新。

> 2026-08-20 R5: **2api v4 诚实门禁封板**——当时 `verdict=fail`（INV-13/REQ-SAN-14）；DEP 指纹与 leak-e section tag；compat 96；上游 529 致 live/gold 缺口。

> 2026-08-19 R4: **2api v3 P0 落地**——EOF hold-back 不泄漏半截 tag；include_usage 中间 chunk `usage:null`；`stream_options`/`logprobs`→400；全响应 `x-request-id`；`GET /v1/models/{id}`；tool_call_id 序列校验；`call_` tool id；stream error 仍发 `[DONE]`。local_compat **96/96**；gold **20/20**；Docker md5=`06cd87eddeb939e538882d4e73afcd5c`。blocking 余 REQ-SAN-12 / INV-11（P1）。

> 2026-08-19 R3: **incomplete tool 静默 tools=0 修复**——flush 对截断 wrapper  salvage 已闭合 invoke；tools_enabled 且无 tool_call 时 incomplete 不再因 reasoning 已 yield 而假 stop，改为 retry/error 供 escalate。compat **74/74**；gold **20/20**；Docker md5=`1d40bc7cb923e6cc5455e9da075aff45`。

> 2026-08-19 R2: **2api 全文 P0 补齐**——`n!=1`→400；`response_format` json_object/json_schema 软注入；`context_length_exceeded` 标准 code；客户端断连中止上游 drain；`/v1/embeddings|completions`→501；过滤 DSML 占位名 `TOOL_NAME_HERE`。金标 `_openai_sdk_gold.py` **20/20**；compat **70** / tool **28** / agent_long **17**。Docker md5 已同步。
> 2026-08-19: **OpenAI wire-compat P0**（chat stream）：未知 model → 404 `model_not_found`；SSE `Connection: keep-alive` + `X-Accel-Buffering: no`；流式 tool_calls 分片（首片 id/name/type/args=""，后续 args 字符串增量）；`stream_options.include_usage` 末块 usage；官方 openai-python 金标。
> 2026-08-16 更新: **sol mid-flight completion 门控**（打断任务完成后的 Write-Output 空转 + 双发慢）；此前 catalog `code_mode_only`→tools=0 已修；mid-flight escalate（d1eb247）仍有效。accept 28/28；thrash 探针 3/3。
> 2026-08-17 更新: **terminal-force 单次 + concurrency 3**（commit `9df41b5`）：terminal-force 2次循环压为单次 max_retry=2，upstream concurrency 5→3，减少 busy 风暴。Docker 已 rebuild（`30bac93`）。accept 28/28 + 17/17 全 PASS。
> 2026-08-17 晚: **根因修复 — thinking 通道 DSML 泄漏**。对照 git：`ToolCallParser`/`_consume_capture`/`_normalize_dsml` 自 `1b434fa` 起字节级未变；泄漏不在 strip 回归，而在 `upstream()`` 内正文当 `reasoning` 直接 yield，**绕过** ToolCallParser。模型常把 `|DSML|tool_calls` 写进 thinking → 客户端可见标签 + tool_call 丢失 → escalate 双发。修复：始终 peel think；reasoning 也过 tparser；tool_call 抽出，干净 prose 才当下 thinking。Docker 已 rebuild。accept 28/28 + 17/17。
> 2026-08-18 00:15: **local_compat 70/70 FAIL 0**（commit `c85b50e`）：`_find_partial()` 补 bare `<function=` opener 识别（se.zzmax upstream 格式），防止 flush 时 pending 残留 emit。Docker rebuild + push 完成。
> 2026-08-18 13:26: **live 复核三项全绿**（Docker `maxapi-server:latest` 已运行 13h，healthy）：local\_compat **70/70**、tool\_suite **28/28**、agent\_long **17/17**。无新问题。

## 一句话现状

`maxapi_server.py`：OpenAI Chat Completions **wire-compat P0** + **2api v4 诚实门禁**（DEP 指纹 / finalize 六类 / ToolCallParser 最长匹配）+ sol auto tool 阶梯 + mid-flight 门控 + thinking 双通道 tparser。验收：gold 20 + compat 96 + tool 28 + agent_long 17；v4 report pass=108 unknown=18 fail=0（insufficient-evidence）。8080 Docker `maxapi-server:latest` 已同步。

## 0.5 目标锚点

**北极星**：maxapi（OpenAI Chat Completions 兼容层）在《2api 兼容层验收标准 v4》门禁下可交付诚实四态 verdict 的完整一致性报告；每条 INV/REQ 均有 status∈{pass,fail,unknown}+证据等级与可打开证据路径，且报告顶层 deployedArtifact 与正在监听 8080 的进程指纹一致；L0 内容平面与终止路径优先取证/整改后，wire 结构与长验收主路径不因本任务被踩烂。

**成功判据**：
- [x] sc-1 《2api v4》全部 INV/REQ 每条均有 status∈{pass,fail,unknown} + evidence 等级(E0-E3)与路径；无证据不得 pass（18 INV + 108 REQ；R7: pass=108 unknown=18 fail=0）
- [x] sc-2 存在 `_runtime/v4_consistency_report.json` 且符合文档附录 E；顶层 deployedArtifact 含镜像 digest/二进制 sha256/commit/sanitizerConfigVersion，且指纹取自监听 8080 的进程（非构建产物）
- [x] sc-3 `_runtime/v4_evidence/` 下每条 pass 有可打开证据文件，证据内产物指纹与 deployedArtifact 一致（INV-18 pass + inv18_evidence_index.json）
- [x] sc-4 报告含 metaRuleViolations、testChanges、mutantResults；unknown=0 且无 E3 时 verdict=invalid（不得造假全绿）；verdict 按 M4 四态诚实给出 → 本窗 **insufficient-evidence**（mustE3/无 harness），validate valid，blocking=[]
- [x] sc-5 八个历史高频漏点(a-h)各自有代码位置与对应测试/证据条目：**a–h 全 pass**（finalize/leak-g/live/probe + L0）见 eight_leaks_ah.json
- [x] sc-6 优先级组 INV-01..03 与 INV-13..15 已优先取证；fail 项附文件:行 + 最小复现命令 + 期望vs实际（R7 fail=0；INV-03 ledger E1 pass；INV-13 finalize pass）
- [x] sc-7 不超过 20 行的整改优先级清单已产出（`_runtime/v4_remediation_priority.txt`）
- [x] sc-8 若有代码修复：leak-e section tag + leak-b 最长匹配 + finalize 六类；8080 Docker sha 与源码一致（81d61570…）；未放宽 gold/compat；compat 96/96
- [x] sc-9 GOALS.md G1-G8 与 STATUS §1 旁路核心未被破坏；主路径回归未因本任务踩烂
- [x] sc-10 官方 openai-python 金标全绿：`python -u _openai_sdk_gold.py` → **20/20**
- [x] sc-11 流式 tool_calls 分片 shape：gold `stream.tool` + `raw.sse_tool_wire` shape=True；DONE/X-Accel/event-stream 已锁
- [x] sc-12 stream_options.include_usage：live + gold + compat 中间 usage:null、末块 choices=[]、未知 model 404
- [x] sc-13 n!=1→400；response_format json_object 可解析；embeddings→501（gold）
- [x] sc-14 context_length_exceeded 标准 code（gold unit）；客户端断连路径走 `_finalize_chat_stream(client_cancel)` + `_iter.close`
- [x] sc-15 drop unknown/placeholder tool（既有 R2/R4 能力 + gold/compat 覆盖）
- [x] sc-16 长验收全绿：compat **96** / tool_suite **28** / agent_long **17** / gold **20**

**明确不做**：
- 不碰访客旁路核心（XFF/额度轮换/busy≠quota/隐身 header）——只读不改
- 不整文件重写 `maxapi_server.py`；禁止顺手重构/统一抽象
- 不改测试/放宽断言/重写 golden 让门禁变绿（确需改必须记 testChanges 且保留等价覆盖）
- 探针只打 `127.0.0.1:8080`，不抢 57321 / 不与用户 Codex 抢流量
- 不把模型遵从/上游策略问题（tool_choice=required 被拒、偶发 re-tool）单独挡 ready（wire P0 全绿前提下可标 partial/COMPAT）
- 不做 60m soak（本次任务未要求）；不把 tools 全量 prompt 绑进长 soak
- 不提交 git（除非后续任务明确要求）；不删 STATUS 其它段落 / 不删减 successCriteria 意图

**目标变更记录**：
- 2026-08-19：新建 §0.5。来源=调用方 auto 任务「2api v4 完整门禁与整改」+ wire-compat 强制项(a-h)一次锁全表；与既有 R4「v3 P0」叙述兼容，升级为 v4 证据等级/产物绑定/四态 verdict 为唯一验收口径。

## §1 已稳部分（保护区 — 换会话修局部时禁止整块重写）

- **访客旁路核心**：每请求伪造 XFF/X-Real-IP；2 次 OK 后主动 retire identity；quota 换新 identity 短睡，busy/stall 同 identity 退避；`busy≠quota`；客户端错误不泄漏上游额度中文文案；上游并发信号量默认 **3**（`MAXAPI_UPSTREAM_CONCURRENCY`）。
- **工具协议**：DSML prompt + ToolCallParser；OpenAI `/v1/chat/completions` + Anthropic `/v1/messages` + Responses。**content 与 reasoning 双通道都过 tparser**（防 think 内 DSML 泄漏 / 漏提 tool）。
- **incomplete tool（2026-08-19 R3）**：流结束截断 DSML/function 块时优先 salvage 已闭合 invoke；tools 回合无 tool_call 不得因 reasoning 已 yield 而 `finish_reason=stop`（改为 retry/error → escalate）。
- **auto tool 阶梯（2026-08-15）**：CONNECTED TOOLS 声明 → 动作请求无 tool 时 escalate 到 forced function/Bash → 仍失败则 terminal-force（独立短上下文，单次 max_retry=2，不再循环重试）→ 首轮/escalate 的 **retryable 529/busy** 也进 ladder（quota/auth 不进）→ howto / do-not-call-tools 门控 + `tool_choice=none`。2026-08-17: 上游并发从 5 降到 3，减少 busy 风暴。
- **sol mid-flight completion 门控（2026-08-16）**：tool 历史存在时若 recent tool_result / 首轮 prose 已声明完成（「无需进一步操作」等），**不再** auto→required/terminal-force，允许 end_turn；未完成 + 短 nudge 仍 escalate。修 thrash 空转与双发延迟。
- stream：header 前 prefetch；仅 `saw_tool` 才接受 escalate 缓冲（修「无 tool 缓冲当成功」）。

## 已稳部分（历史能力清单）

- `/v1/models` 返回 200，模型对象含 `context_length`、`max_output_tokens`、`supports_tool_use`。
- `claude-opus-4-8` alias 映射回 `Claude Opus 4.8`，本地断言通过。
- 工具参数 schema coercion 已验证：字符串 `"3"` 转整数 `3`，字符串 `"true"` 转布尔 `True`，schema default `mode="fast"` 会补齐。
- OpenAI Responses 非流式文本路径通过：`/v1/responses` 返回 `object=response`、`status=completed`、文本 `hello world`。
- OpenAI Responses 非流式 function_call 路径通过：函数名 `do_work`，参数 coercion 后 `count=3`、`flag=True`。
- OpenAI Responses 流式文本路径通过：SSE 含 `response.output_text.delta` 和 `response.completed`。
- OpenAI Responses 流式 function_call 路径通过：SSE 含 `function_call` item、`response.function_call_arguments.done`，参数字符串内已是 coerced JSON。
- forced function `tool_choice` prompt 已验证：保留强制调用指令、完整 DSML schema 模板、且用 `m._DSML` 拼出的精确 `<invoke name="do_work">` 标签存在。
- **上下文压缩机制上线**：preflight 拒绝已移除；**2026-08-16** 起预压缩触发 `eff>limit*0.88`（估核算 inflate 默认 1.20 + 分段 budget），上游 too-long 仍重试 compact。
- **结构化日志上线**：`LOG.info/warning/error` 替代 `sys.stderr.write`，含rid、model、est/actual tokens、耗时。
- **post-compact validator上线**：校验tool_use/tool_result配对，失败回退到原始请求透传。
- 真实本地服务端到端验证通过：`python maxapi_server.py --host 127.0.0.1 --port 18080 --rpm 0 --no-companion` 后，`/healthz`、`/v1/models`、`/v1/responses`、`/v1/chat/completions`、`/v1/messages` 均返回 200。
- 真实 upstream 文本路径通过：Responses 非流式/流式、Chat 非流式/流式、Anthropic Messages 非流式均对 `Reply with exactly: pong` 返回 `pong`。
- 真实 forced tool_call 路径通过：Responses 和 Chat 在 `tool_choice` 强制 `do_work` 时均返回 function call，参数为 `{"count": 3, "flag": true}`，未出现 502。

## 2026-08-10 改动详情

### Opus 5三轮review修复（commit `288ae48`）

#### P0: _parse_too_long三态
- 返回`_TooLong(actual, allowed)`对象，actual/allowed可为None
- 4个正则：exceeds、gt、max-context正序、max-context反序
- head+tail截断(各4096字符，>8192才截)，防ReDoS
- `int()`包try/except，invariant(actual>=allowed)用if return+LOG.warning
- 关键词fallback仅在无regex匹配时触发

#### P1: _err_body统一错误构造器
- `flavor='anthropic'` → `{"type":"error","error":{"type":...,"message":...}}`
- `flavor='openai'` → `{"error":{"message":...,"type":...,"code":...}}`
- /v1/messages用anthropic，/v1/responses和/v1/chat/completions用openai
- 流式SSE error frame保持手写Anthropic格式

#### _compact_budget单调递减
- `allowed*0.9 - out_reserve` → `min(budget, est*0.8)` → `max(8000, ...)`
- MODEL_META查context不带默认值，unknown model走blind path
- 负数budget时LOG.warning+fallback到floor
- 所有6个调用点用walrus operator去重复调用

### 1. 移除preflight拒绝
- 三个端点(`/v1/messages`, `/v1/responses`, `/v1/chat/completions`)不再在preflight阶段拒绝长输入
- 改为LOG.warning记录超限；**旧** est>limit×1.25 才压 → **现** eff>limit×0.88（见 2026-08-16 节）
- 上游返回"too long"错误时自动解析actual/allowed并重试（非流式路径）

### 2. 结构化日志
- `Handler.log_message` → `LOG.debug`，`log_error` → `LOG.warning`
- 每个请求记录rid(8位hex)、model、est tokens、limit、stream
- 响应记录status、input/output tokens、耗时
- 环境变量 `MAXAPI_LOG_LEVEL` 控制日志级别

### 3. 上下文自动压缩（4阶段）
- Stage 1: 截断超长tool_result（>`_TOOL_RESULT_CAP`字符时保留首尾）
- Stage 2: 按segment分组（保证tool_use/tool_result配对），丢弃中间segment
- Stage 3: 截断剩余text block（>2000字符时保留首1000+尾800）
- Stage 4: 仍超限则透传给上游决定
- `_validate_compacted_messages()` post-compact校验，失败回退到原始请求

### 4. 改进的token估算
- `_estimate_request_tokens()` 包含system prompt + tools定义 + 所有content block类型
- `_context_limit(display_id, max_tokens)` 支持动态输出预留

### 环境变量
| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MAXAPI_COMPACT` | `1` | `0`=纯透传，不做压缩 |
| `MAXAPI_COMPACT_TRIGGER` | `0.88` | 预压缩触发：eff > limit × 该值 |
| `MAXAPI_EST_COMPACT_INFLATE` | `1.20` | 无 EWMA 样本时对 est 上浮，抵消低估 |
| `MAXAPI_LOG_LEVEL` | `INFO` | 日志级别 |
| `MAXAPI_KEEP_TAIL_SEGMENTS` | `6` | 压缩时保留的尾部segment数 |
| `MAXAPI_TOOL_RESULT_CAP` | `4000` | tool_result截断阈值(字符) |

## 2026-08-10 第二轮改动（commit `2b97bff`）

### B2: 压缩信号改response header
- 移除 `_append_system_note` 注入system prompt（会打穿prompt cache）
- compact返回metadata dict → handler注入 `X-Maxapi-Compacted`、`X-Maxapi-Dropped-Segments`、`X-Maxapi-Token-Estimate` 等响应头

### EWMA自校准
- 按model维护 `ratio = EWMA(actual/estimated)`，alpha=0.15
- 每次非流式响应后用 `usage.input_tokens` 更新
- `_estimate_request_tokens(model=)` 自动应用校准比
- ratio clamp [0.5, 2.0] 防异常值

### Q2: segment分组改用显式依赖图
- `_segments()` 同时处理Anthropic和OpenAI两种格式的tool配对
- 以turn unit为粒度丢弃，保证配对完整性

### B4: 格式感知压缩
- Stage 1同时截断Anthropic content blocks和OpenAI `role:tool` 消息

### 流式重试
- 三端点流式路径：prefetch first event → 确认非error → 写SSE header
- too long错误时自动compact → retry
- 错误直接返回HTTP状态码（client能正确处理）

### 测试结果
```
Test1 (non-stream): est=6 limit=199438 → 200 input=4 output=3 ✓
Test2 (stream): est=4 limit=199438 → 200 stream ✓
EWMA: 第二个请求est已从6降到4（校准生效）✓
```

### 测试结果
```
# 基本请求 - 正常
rid=b40ca388 est=9 limit=199388 → 200 input=7 output=9 ✓

# 超长请求(est/limit=1.18x < 1.25x阈值) - 不压缩，直接透传
rid=59d672a9 est=236115 limit=199388 → 200 input=212502 ✓

# 超长请求(est/limit=1.30x > 1.25x阈值) - 触发压缩（单message无可压缩内容）
rid=86ded96a est=259726 limit=199388 → compacted → 200 input=233752 ✓
```

## 卡点和实证

### 卡点: forced tool prompt 测试断言误判

- 现象: 扩展 `_local_compat_check.py` 后首次跑出 `TOTAL 24 FAIL 1`，失败项为 `forced tool prompt keeps exact function name example`。
- 铁证: 失败输出里的 prompt 明确包含 `single <invoke name="do_work"> inside it`，但测试断言查的是无前缀 `'<invoke name="do_work">'`。
- 逐因验证:
  - 服务端 prompt 输出含强制工具名和 DSML 前缀，说明服务端不是缺标签。
  - `forced tool prompt keeps must-call directive` PASS，说明 forced directive 没丢。
  - `forced tool prompt keeps DSML schema` PASS，说明完整 DSML 模板没被替掉。
  - 仅 exact function name example FAIL，且失败详情显示实际输出是 `<invoke name="do_work">`，决定因是测试断言没包含 DSML 前缀。
- 处理: 修改断言为 `('<' + m._DSML + 'invoke name="do_work">') in forced_prompt`，避开在测试源码里硬写 `|DSML|` 字面量。
- 结果: 重新运行 `_local_compat_check.py`，`TOTAL 24 FAIL 0`。

### 卡点: 工具参数含额外字段 `.extra`

- 现象: 检查期间 stderr 出现 `[tool-validate] name=do_work error=unexpected property .extra`。
- 铁证: fake upstream 故意返回 `{"count": "3", "flag": "true", "extra": "x"}`，schema 设置 `additionalProperties: False`。
- 实证结论: 当前实现会记录 validation error，但不会丢弃或阻断 tool_call；检查重点是类型 coercion 后传给客户端，相关断言 PASS。此行为是否要改成剔除额外字段或直接报错，还未作为目标验证。
- 待验点: 如果真实客户端严格拒绝额外参数，下一步应加实测和策略决策；当前不能标成已解决。

## 真跑命令

```bash
git -C /c/Users/Administrator/Desktop/maxapi status --short && git -C /c/Users/Administrator/Desktop/maxapi log --oneline -5
```

结果要点:
- `main` 上最新提交 `7032fc1`。
- 当时未跟踪文件: `_local_compat_check.py`。

```bash
python /c/Users/Administrator/Desktop/maxapi/_local_compat_check.py
```

初始结果:
- 修改断言前扩展检查为 `TOTAL 24 FAIL 1`。
- 失败项仅为 forced prompt 的 DSML 前缀断言误判。

```bash
python /c/Users/Administrator/Desktop/maxapi/_local_compat_check.py
```

最终结果:
- `TOTAL 24 FAIL 0`。
- 覆盖 models metadata、alias、schema coercion、Responses 非流式/流式文本、Responses 非流式/流式 function_call、forced tool prompt、三类 stream prefetch error。

## 当前文件状态

- `maxapi_server.py`: 最新提交 `5e80617`，含trailing reminder修复+upstream诊断日志。
- `_local_compat_check.py`: 51项测试全部通过。
- `_canary_scale.py`: 已清理（记忆scale test，10-150轮全满分，结果已验证）。
- `STATUS.md`: 本文件。
- `.gitignore`: 未修改。

## 2026-08-12 SSE 分帧修复（未提交）

### 根因: `Connection: close` + 无 Content-Length 的 SSE 无法判定完整性

三个流式端点原来都这么发头：

```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: close        ← 正文边界只能靠 TCP FIN
```

HTTP/1.1 下这种 body **只由连接关闭来界定**。经 CF 隧道 + NewAPI 中转时，
任何一跳中途断流，客户端收到的字节流和"正常收完"在协议层完全无法区分。
Rust/hyper 系客户端（Claude Code）对此报 `error decoding response body`。

改为 chunked 后，结尾的 0-length chunk 是唯一合法终止符，
中转截断变成**可检测的错误**而不是静默截短。

- 新增 `Handler._sse_begin/_sse_chunk/_sse_end` 统一分帧
- 三端点（`/v1/messages`、`/v1/chat/completions`、`/v1/responses`）全部切过去
- 三端点 `finally` 里补 `_sse_end()`，异常/return 路径也保证写终止 chunk
- 加 `X-Accel-Buffering: no` + `no-transform`，防 nginx/CF 缓冲整条流

### `/v1/responses` 完全没有 keepalive

另两个端点有 1s 心跳，Responses 端点一个都没有。上游首 token 慢时连接空转，
被中间层掐断 → 客户端按网络故障处理并长 backoff（即 `will retry in 4m 29s`）。
已补 `: keepalive` 心跳线程，`response.created` 之后才启动。

### 缓存命中率 0：不是统计 bug，是真的没有缓存

`cache_read_input_tokens` / `cache_creation_input_tokens` 在 2644、2743 行**硬编码 0**，
全文件无任何 `cache_control` 处理。上游 `/api/chat/stream` 不返回 usage，
proxy 层无法凭空造出命中数据。要有真实命中必须上游支持。

### 其他

- 1893 行 `yield (tk, tp)` 重复两次 → tool_call flush 时末个 tool 被下发两遍，已删

### Review 阶段发现的两个缺陷（都已修）

**A. 心跳线程泄漏（本次改动引入的回归）**

`_sse_chunk` 最初写成三次 `wfile.write`（长度头 / 正文 / CRLF）。
客户端中途断开时，头几个字节的长度头写入落进 socket 缓冲区**不报错**，
异常被推迟，心跳线程于是一直空转不退。

- `git stash` 对照实测：原版 0.3s 退出，改动后 >14s 仍在转
- 合并为单次 `write(size + payload + CRLF)` 后 → 0.0s 退出
- 副作用：也消除了「长度头已写、正文未写」的半个 chunk 破坏分帧的可能

这个 bug 只在慢上游 + 客户端中断时暴露，正是 NAS 上的常态路径。

**B. HTTP/1.0 客户端收到乱码**

chunked 是 HTTP/1.1 才有的。原先无条件发 `Transfer-Encoding: chunked`，
HTTP/1.0 客户端不认，会把 `163\r\n` 这类十六进制长度行当正文读进去。
已按 `request_version` 判断，1.0 客户端回退到 close-delimited。

### 实证

- `python _local_compat_check.py` → **TOTAL 51 FAIL 0**
- 三端点 HTTP/1.1 线格式：`chunked=True` / 终止 0-chunk 存在 / `X-Accel-Buffering: no` 存在
- HTTP/1.0 回退：`chunked=False` / 正文无十六进制长度行泄漏
- 连接复用：同一条 TCP 连发两个流式请求，第二个正常返回并带终止 chunk
- 客户端中断：三端点心跳线程均 0.0s 退出，无线程泄漏

### 教训

上一轮我在只跑通「正常路径」后就宣称验证完成，而 51/51 那套测试并不覆盖
本次改动的分帧逻辑。缺的三件事：客户端中断路径、HTTP/1.0、以及
`git stash` 与原版对照（判断「是我引入的还是本来就有」只需一条命令）。

## 2026-08-12 下架 Claude Opus 4.8（未提交）

### 上游 provider 已死，实测 0/8

同一道题各打 8 次，只换 subModel：

| subModel | 成功率 |
|---|---|
| `claude-opus-4.8` | **0/8**（全部「当前模型暂无可用的服务提供商」）|
| `claude-opus-5` | 8/8 |
| `claude-sonnet-5` | 8/8 |
| `claude-opus-4-6` | 8/8 |

不是临时抽风，是这个 subModel 上游没有 provider。留着的代价是每次请求走满
5 次重试才报错。

### 处理方式：下架模型，但保留所有入口 ID → Opus 5

`claude-opus-4-8` 是 Claude Code 原生发送的 model id，直接删会变 404，
所以全部 alias 重定向而非删除：

- `RAW_MODELS` 移除 `Claude Opus 4.8` 条目（13 → 12 个模型）
- `_ANTHROPIC_MODEL_IDS` 移除对应死条目
- 4 个 alias（`claude-opus-4-8` / `claude-opus-4.8` / `claude/…` 两种）改指 `Claude Opus 5`
- **补 `"Claude Opus 4.8"` 显示名 alias** — 漏了这条会 fall through 到
  `DEFAULT_MODEL`，即请求 Opus 级模型静默拿到 `deepseek-v4-flash`。
  这是移除模型时最容易漏的一环，已加断言锁住。

### 测试

`_local_compat_check.py` 51 → **63 项**，新增：

- 4 个 alias 各自指向 Opus 5
- `Claude Opus 4.8` 已不在模型清单
- **所有 alias 的 value 都必须是活模型**（防以后再删模型留下悬空 alias）
- 所有模型都有 anthropic id / 无死 anthropic 条目
- 5 个退役 ID 逐个断言 `resolve_model()` 落到 `claude-opus-5`（防静默降级到 default）

### 实证

- `python _local_compat_check.py` → **TOTAL 63 FAIL 0**
- 5 个退役 ID 全部路由到 `claude-opus-5`
- 全项目 grep 无残留 4.8 引用

## 2026-08-12 GPT 5.6 sol 路径 Review

### 结论：代理链路完全正常，之前误判

实测确认：**GPT 5.6 sol 通过代理的 tool calling 是通的。**
之前说"GPT 不能正常用"是误判，根因是测试脚本往 upstream payload 里
加了 `tools` 参数（代理不会这么做），导致上游告诉 GPT"没有可用工具"。

### 逐段验证

| 环节 | 状态 | 说明 |
|---|---|---|
| 模型解析 | ✅ | `resolve_model("gpt-5.6-sol")` → `("chatgpt","gpt-5.6-luna")` |
| DSML prompt | ✅ | 完整 prompt GPT 认，压缩单行版不认（代理用的是完整版） |
| 上游 payload | ✅ | 不含 `tools` 参数，上游不会回绝 |
| ReasoningFilter | ✅ | GPT 输出 `<think>` 标签，正确剥离为 `reasoning_content` |
| ToolCallParser | ✅ | DSML 解析正确：`name="do_work"`, `count=3` |
| 多轮 round-trip | ✅ | tool result 回传后 GPT 继续正确调用 |
| 文本/工具切换 | ✅ | 被要求纯对话时正确退出 DSML 模式 |
| SSE 转换 | ✅ | `/v1/chat/completions` 和 `/v1/messages` 都正确 |

### 多轮 round-trip 实测

```
Round 1: GPT → DSML(do_work, count=3)     ✅
Round 2: GPT → DSML(do_work, count=5)     ✅（参数正确递增）
Round 3: GPT → 纯文本笑话                  ✅（正确退出 tool call 模式）
```

## 2026-08-12 下架 GPT terra/5.5（commit `c2bcab3`）

### 上游 GPT 模型可用性

| 模型 | 状态 | 处理 |
|---|---|---|
| `gpt-5.6-sol` | ✅ 8/8 | 保留，唯一可用 GPT |
| `gpt-5.6-terra` | ❌ 0/8 | 下架，alias → sol |
| `GPT-5.5` | ❌ 0/8 | 下架，alias → sol |

处理方式同 4.8：RAW_MODELS 移除条目，所有 alias 重定向到 gpt-5.6-sol。
加 `GPT-5.5` 大写显示名 alias 防止静默降级到 DEFAULT_MODEL（又一个 opus 4.8 踩过的坑）。

模型清单：13 → 12（4.8）→ **10**（+terra/5.5）。

### 实证

- `python _local_compat_check.py` → **70/70 FAIL 0**
- 退役模型（gpt-5.6-terra / GPT-5.5 / claude-opus-4-8）全部 200 路由到活模型
- GPT tool call 两条路径端到端通过

### 当前 commit 历史

```
c2bcab3 fix: 下架gpt-5.6-terra和GPT-5.5，只保留gpt-5.6-sol，70/70
3d01139 docs: GPT 5.6 sol 路径 review — 代理链路完全正常
5181a17 fix: SSE分帧修复+下架Opus4.8，63/63
```

### 上游 GPT 不支持原生 function calling

OpenAI 原生 `tools` / `functions` 参数在上游被拦截，GPT 会收到
"no tools available" 而拒绝调用。代理的 DSML prompt 工程绕过了这个限制：
tools 不走 payload 而是注入 messages，上游不拦截 messages 里的文本指令。

这是代理的核心设计优势——也是唯一可行的路径。

### `thinking: disabled` 白烧推理 token（遗留）

`/v1/messages` 2635 行：`disabled` 只关 `include_reasoning`，`effort` 仍是 `max`。
上游照样全速思考，推理内容被丢弃。修法一行，但需要先确认 Claude Code 是否
默认发 `disabled`。已记在上面待决策区。

## 已确认正常（无需动）

`reasoningEffort` 默认 `max` 三端点全部生效，上游也真的认这个字段：

| effort | 耗时 | 正文长度 | 含 `<think>` |
|---|---|---|---|
| `max` | 10.6s | 550 | **是** |
| `off` | 9.6s | 163 | 否 |
| 不传 | 9.9s | 252 | 否 |
| 乱值 | 9.9s | 296 | 否 |

「感觉没推理」的真正原因是 Opus 4.8 上游已死，不是 effort 没传下去。

已知脆弱点：`ReasoningFilter` 只认 `<think>` / `<thinking>` 标签。上游目前
正是这个格式，但若改成结构化字段传推理，过滤器会静默失效、把推理当正文输出。


## 2026-08-16 预压缩安全余量（Sonnet5 规划）

### 根因
- 旧逻辑：`est > limit` 只 warning；**`est > limit * 1.25` 才 compact**，目标 `limit*0.95`。
- 用户实证：上下文 **>100%** 时 agent 异常；**手动压缩一次再继续就正常** → 自动压缩触发过晚。
- 用户否定「100% 就压」：贴顶无安全额度。
- 额外实证：原始估算相对上游 `input_tokens` 可低估 ~20–25%（CJK 长历史 fat 测：est 148k / actual 196k），仅改 1.25→0.88 仍可能不触发。

### 修复（最小 diff，三端点 messages/chat/responses）
| 项 | 值 |
|---|---|
| 触发 | `eff > limit * MAXAPI_COMPACT_TRIGGER`（默认 **0.88**） |
| eff | raw est × `MAXAPI_EST_COMPACT_INFLATE`（默认 **1.20**）；有 EWMA 样本时用 max(ratio,1.0) |
| budget 分段 | eff/limit ≤1.05→limit 的 82%；≤1.25→78%；>1.25→72%；再 /scale 回到 raw 单位，且 **强制 raw≤0.85×** |
| 上游 too-long 重试 | 仍走 `_compact_budget`（未改算法本体） |

环境变量：`MAXAPI_COMPACT_TRIGGER`、`MAXAPI_EST_COMPACT_INFLATE`、`MAXAPI_COMPACT`。

### 实证
- 单元：trigger/inflate/budget 分段断言通过。
- 日志：`compacting (est=148779 eff=178534 limit=199232 ratio=0.90 trigger=0.88 budget=126462)` → `compacted est 148779->126183 ... segments_dropped=7`。
- 短请求 PONG 不误压；fat 请求触发丢段。


## 2026-08-15 sol auto tool 可靠性（本批）

### 问题

Claude Code / agent 经 maxapi 打 `gpt-5.6-sol` 时，auto `tool_choice` 偶发只 think + `end_turn`（声称无终端/不调工具）；`tool_choice=required` 明显更稳。套件曾卡在 **18/20** auto：escalate 遇上游 **529 busy** 后直接停，**不走 terminal-force**。

### 修复（最小 diff，不动旁路核心）

| 项 | 行为 |
|---|---|
| `_make_tools_prompt` | CONNECTED TOOLS；动作必须 DSML；howto/禁执行允许纯文本 |
| `_auto_action_candidate` / `_RE_TOOL_NO_TOOL` | 强动作才 escalate；`do not execute` / `just explain` / `do not call tools` 不 escalate |
| `_user_forbids_tools` | auto + 明确禁 tool → `tool_choice=none` |
| escalate | auto 无 tool_call → forced `function/Bash` 或 `required` 再打 1 次 |
| terminal-force | escalate 仍无 tool：独立 slim 上下文 + HARD REQUIREMENT，**单次**（内部 max_retry=2）；可抽 `echo X` 成硬指令 |
| `_is_retryable_tool_upstream_err` | busy/529/502/503/timeout → 可进 ladder；**quota/429/auth/too-long 永不进** |
| 四路径 | messages/chat × nonstream/stream：escalate 失败若 retryable → fallthrough terminal-force |
| stream | 仅 `_saw2` 才接受 escalate 结果 |

环境变量（默认开）：`MAXAPI_TOOL_ESCALATE=1`、`MAXAPI_TOOL_TERMINAL_FORCE=1`。

### 实证（8080 Docker live，sol 规划/review APPROVE）

```
_accept_tool_suite.py     TOTAL 28/28
  auto Bash               20/20 err=0
  plain_no_tool           PASS
  howto_no_force          PASS
  chat_auto_tool          PASS
  stream_auto_tool        PASS
  multi_tool_bash         PASS
  agent_multiturn         PASS (DONE, no tool)

_accept_agent_long.py     TOTAL 17/17 wall≈160s
  round_0..14 Bash        15/15 tool_use (STEP_i)
  final_no_tool           ALL_DONE
```

日志锚点：`first_err=upstream temporarily unavailable` → `terminal-force try=1 success`（如 rid `af3747b6`、`666f9c78`）；`identity retire after 2 ok uses` 仍在。

真跑命令：

```bash
# 容器
docker build -t maxapi-server:latest . && docker rm -f maxapi
docker run -d --name maxapi -p 8080:8080 --restart unless-stopped maxapi-server:latest
curl -s http://127.0.0.1:8080/healthz

# 验收
python -u _accept_tool_suite.py
python -u _accept_agent_long.py
```

### 诚实边界

- 套件与长链均为 **8080 直连** sol；完整「Claude Code → 15721 cc-switch → max/sol → 8080」真人墙钟 ~15min 未在本批重跑。
- 上游 529 仍会拉长单次延迟（escalate/TF 多 1–2 次上游调用）。
- 极少数 forced 两轮仍无 DSML 的尾部风险理论上仍在；当前 20/20 + 15/15 未复现。





## 2026-08-16 Codex++ sol tools=0（catalog，非 maxapi 吞 tools）

### 现象
- Codex++ 直连 `http://127.0.0.1:8080`，Claude Opus 同线程可 `exec_command`/`apply_patch`。
- 切到 `gpt-5.6-sol` 后用户说「做」→ 模型独白「当前没有 shell/文件工具」。
- 用户误判为 maxapi GPT 路径坏了；实为 **请求未带 tools**。

### 铁证
| 来源 | 内容 |
|---|---|
| maxapi 日志 | 失败轮 `rid=70402004 [chat] gpt-5.6-sol msgs=36 **tools=0**`；Claude 同路径曾 `tools=20` |
| 会话 rollout | `01a007fb-...`：Opus 多轮 function_call；sol 轮仅 message，无 tool |
| `/v1/models` | `gpt-5.6-sol supports_tool_use=true`（代理已声明支持） |
| 复现 | chat 故意不传 tools + 历史有 tool_calls → 必独白；responses/`tools` 在则 function_call 正常 |
| accept | `_accept_tool_suite.py` **28/28**（客户端发送 tools 时） |

### 根因（Codex model catalog）
文件：`~/.codex/model-catalogs/relay-ms0exe08.json`（`config.toml` → `model_catalog_json`）

| 字段 | Claude Opus/Sonnet | gpt-5.6-sol / terra（修前） |
|---|---|---|
| `tool_mode` | 无 | **`code_mode_only`** |
| `use_responses_lite` | 无 | `true` |
| `multi_agent_version` | 无 | `v2` |
| `shell_type` | `shell_command` | 同 |
| `apply_patch_tool_type` | `freeform` | 同 |

sol 审查 APPROVE：`code_mode_only` 主嫌疑（lite 可能连带）；**maxapi 无需为 tools=0 再改功能**。

### 修复（本机 Codex catalog，不在 maxapi 仓库内）
对 sol/terra：
- 删除 `tool_mode`
- `use_responses_lite: false`
- 删除 `multi_agent_version`
- 保留 `shell_type=shell_command`、`apply_patch_tool_type=freeform`
- 备份：`relay-ms0exe08.json.bak-toolmode-20260816-133014`
- **必须重启 Codex++ + 新开线程** 加载 catalog

### 修复后实证
| rid | 日志 |
|---|---|
| `fc51d8b2` | 重启前仍 `tools=0` |
| `11c61bc7` | **`tools=20`** stream 200 |
| `b188c569` | `tools=20` |
| `1b00def4` | `tools=20` + **`tool-escalate` chat stream → reroll saw_tool=True** |

### 两层分工（勿混）
1. **Catalog**：决定 Codex 是否在 HTTP 里带上 tools（0 vs 20）。
2. **maxapi mid-flight escalate（d1eb247）**：tools 已在时，短 nudge / 偷懒 narrate 再逼进 ladder。
3. 只改 maxapi 不改 catalog → 永远 tools=0 独白；只改 catalog 无 escalate → 多数能跑，sol 偶发空转仍可能。

### 排障口诀
看 `docker logs maxapi`：
- `tools=0` → catalog / 客户端 / 未重启；别先改代理。
- `tools≥1` 仍无 tool_call → 查 escalate / 529 busy / forbid-howto 门控。


## 2026-08-16 sol mid-flight structural escalate

### 根因
- auto 短 nudge（「做」「继续」「ok」）不匹配 `_RE_TOOL_ACTION`，且 `len(user)<3` 直接 False → 不进 escalate。
- 真实 Codex/CC 会话里模型常 narrate「没有可用工具」后 end_turn；有 tools 时需结构门控逼进 ladder。
- 日志里大量 `tools=0` 来自**客户端未带 tools**（maxapi 不丢 tools，已透传核验）；无 schema 时代理无法凭空造工具。

### 修复（最小 diff，sol-only）
| 项 | 行为 |
|---|---|
| `_is_sol_model` | 精确 allowlist：`gpt-5.6-sol` / `gpt-5.6-luna` / `max/` 前缀；Claude/其它 GPT 永不匹配 |
| `_has_recent_tool_turn` | 跳过尾部纯文本 user nudge，要求**紧邻前一条**带 tool payload；window=8 |
| `_auto_action_candidate(..., model=)` | sol + pending mid-flight → True；howto/forbid 仍 False；非 sol 保持词法门控 |
| `_should_escalate_auto_tools(..., model=)` | 透传 model；sol mid + UNAVAIL 幻觉可 escalate |
| 不动 | XFF、2-OK、busy≠quota、terminal-force 算法主干、词表同义词（concurrency 默认已改为 3，见 2026-08-17） |

### 实证
- `_accept_tool_suite.py` **28/28**（最终 allowlist+pending 修订上）
- live：settings「做」→ `Edit` 去掉 `Write(**)` 等；nudge 做/继续/ok → tool_use；Claude howto → n_tool=0
- sol plan APPROVE_PLAN；review 多轮 REVISE 后收紧 allowlist/pending；终局 28/28 后要求 APPROVE_SHIP

### 客户端注意
- agent 路径必须带 tools schema；`tools=0` 时模型只会说话，maxapi 不会发明工具。
- NAS：`git pull` + **docker rebuild**，勿只 pull。


## 2026-08-16 sol mid-flight completion gate（反 thrash / 反双发慢）

### 根因（日志铁证）
- mid-flight 结构 escalate 修好「能跑命令」后，**任务已完成后仍每轮** `auto→required`（常再 terminal-force）。
- Codex++ thrash 窗口：`msgs=380→430+`，几乎每轮 `tool-escalate`，单轮 15–50s（双上游）；模型被迫 `Write-Output "无需进一步操作"` / 反复核验 settings / `pwd` 几十轮。
- 不是 tools=0；是 **maxapi 在「该 end_turn」时仍逼 tool**。

### 修复（最小 diff，sol-only）
| 项 | 行为 |
|---|---|
| `_RE_TASK_DONE` | 中英完成信号：无需进一步操作 / task complete / no further action / 最终核验通过 … |
| `_recent_tool_result_texts` | 最近 tool_result / role=tool 文本 |
| `_user_text_after_latest_tool` | **仅**最新 tool 之后的 user 文本算新请求（避免原任务句钉死 escalate） |
| `_midflight_should_allow_end_turn` | 首轮 prose 已 done **或** 近 tool 输出 done 且无新强动作 → 允许 end_turn |
| `_auto_action_candidate` | sol mid-flight：howto/完成门控 False；否则 True |
| `_should_escalate_auto_tools` | 完成短路在 candidate 之前；UNAVAIL 幻觉仍 escalate，但 done 优先 |
| 不动 | Claude 词法-only；XFF/2-OK/busy≠quota/concurrency5；terminal-force 算法 |

### 实证
- 单元：completion gate 全绿（done+nudge 不 escalate；未完成+做仍 escalate；Claude 不结构；forbid/howto；新强动作仍 tool）
- live `_probe_completion_thrash.py` **3/3**：`thrash_done` **5.6s finish=stop n_tools=0**（无 escalate 日志）；`still_work` tool_calls；`fresh` tool
- `_accept_tool_suite.py` **28/28**
- docker rebuild `maxapi-server:latest` + 容器重建 8080

### 排障补充
- 任务完成后仍疯狂 shell 空转 → 先看是否旧镜像（无 completion gate）；再看日志是否每轮 `tool-escalate` 而无 done 短路。
- 真未完成却过早 stop → 查 tool_result 是否误含「已完成」文案；门控只读 tool 后 user + 近结果。


## 下一步建议

1. NAS/生产镜像同步本批 completion gate + 观察 Codex thrash 是否消失
2. 可选：经 Codex++ 真客户端复跑「已完成任务」会话，确认不再 Write-Output 空转
3. 定期复查上游 529 比例（仍会拉长单次，与 thrash 双发是两件事）
4. `_STALL_TIMEOUT` / 并发 5 按出口负载再调（改前先证明必须动保护区）

## 进度报告四要素

- **已完成**: tool escalate + compact 0.88 + sol mid-flight d1eb247 + catalog tools=20 + **completion gate 反 thrash**；accept 28/28；thrash 探针 3/3。
- **当前位置**: Codex++ → 8080 → sol 能跑命令且任务完成后应能 end_turn；文档本批固化。
- **离 goal 还差**: NAS 镜像同步；用户侧 Codex 真会话确认 thrash 消失。
- **下一步**: commit+push；换机检查 catalog；观察 live thrash 日志是否归零。

