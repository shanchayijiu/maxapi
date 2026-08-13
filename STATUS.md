# maxapi STATUS

> 2026-08-11 更新: commit `5e80617` — 修复长对话+tools场景用户指令丢失问题（trailing system reminder），303条消息+19个tools记忆测试全满分。

## 一句话现状

`maxapi_server.py` 最新提交 `5e80617`。Opus5诊断+trailing reminder修复，scale test 10-150轮全4/4通过。

## 已稳部分

- `/v1/models` 返回 200，模型对象含 `context_length`、`max_output_tokens`、`supports_tool_use`。
- `claude-opus-4-8` alias 映射回 `Claude Opus 4.8`，本地断言通过。
- 工具参数 schema coercion 已验证：字符串 `"3"` 转整数 `3`，字符串 `"true"` 转布尔 `True`，schema default `mode="fast"` 会补齐。
- OpenAI Responses 非流式文本路径通过：`/v1/responses` 返回 `object=response`、`status=completed`、文本 `hello world`。
- OpenAI Responses 非流式 function_call 路径通过：函数名 `do_work`，参数 coercion 后 `count=3`、`flag=True`。
- OpenAI Responses 流式文本路径通过：SSE 含 `response.output_text.delta` 和 `response.completed`。
- OpenAI Responses 流式 function_call 路径通过：SSE 含 `function_call` item、`response.function_call_arguments.done`，参数字符串内已是 coerced JSON。
- forced function `tool_choice` prompt 已验证：保留强制调用指令、完整 DSML schema 模板、且用 `m._DSML` 拼出的精确 `<invoke name="do_work">` 标签存在。
- **上下文压缩机制上线**：preflight拒绝已移除，改为advisory日志+可选compaction(1.25x阈值)+上游错误重试。
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
- 改为LOG.warning记录超限，est超过limit 1.25倍时触发compaction
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
- 当时未跟踪文件: `_local_compat_check.py`、`_orig.py`。

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
- `_canary_scale.py`: 记忆scale test（10-150轮全满分）。
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

### 上游 GPT 模型可用性

| 模型 | 状态 |
|---|---|
| `gpt-5.6-sol` | ✅ 8/8 纯文本 + tool call |
| `gpt-5.6-terra` | ❌ 0/8 上游已死 |
| `GPT-5.5` | ❌ 0/8 上游已死 |

`gpt-5.6-terra` 和 `GPT-5.5` 跟 4.8 一样的问题：上游「暂无可用的服务提供商」。
要不要也下架？还是观察一段时间？

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

## 下一步建议

1. NAS 部署本次 SSE 分帧修复，观察 `error decoding response body` 是否消失
2. 若仍复现，下一步查 CF 隧道侧：`cloudflared` 的 `--no-chunked-encoding` 若开启需关掉
3. 确认 se.zzmax 真实 context limit（从日志看至少 180k）
4. `_STALL_TIMEOUT=15s` 对慢上游可能偏短，视 NAS 实测再定
5. 定期复查上游模型可用性：4.8 这次是静默死掉的，建议加个探活脚本

## 进度报告四要素

- 已完成: Opus5三轮review修复（17项问题）+ GPT5.6-sol审计修复（5项：流式重试守卫、错误后停止、Responses估算+max_output_tokens、输入校验）。
- 实证: `python _local_compat_check.py` 最终 51/51 PASS。
- 未完成: 无阻塞性待修项。
- 下一步: 可收口；后续按真实客户端反馈按需修复。
