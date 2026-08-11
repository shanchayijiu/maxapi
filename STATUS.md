# maxapi STATUS

> 2026-08-10 更新: commit `288ae48` — Opus 5三轮review：_parse_too_long三态+head+tail截断+_compact_budget单调递减+output reserve+_err_body统一错误构造器。51项兼容测试全部通过。

## 一句话现状

`maxapi_server.py` 最新提交 `288ae48`。Opus 5三轮review修复完成，51项兼容测试全部通过。

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

- `maxapi_server.py`: 本会话未修改；与最新提交 `7032fc1` 保持一致。
- `_local_compat_check.py`: 未跟踪文件，本会话扩展了检查项并修正 DSML prompt 断言。
- `_orig.py`: 未跟踪文件，疑似本地源码备份，未修改。
- `STATUS.md`: 本文件，本会话新建，用于续做断点。
- `.gitignore`: 未修改。

## 下一步建议

1. 如目标是只复核 Codex 修复: 当前可收口，证据是 `_local_compat_check.py` 24/24 PASS。
2. 如目标是提交辅助检查: 需要决定是否把 `_local_compat_check.py` 和 `STATUS.md` 纳入 git；`_orig.py` 更像本地备份，不建议提交。
3. 如真实客户端仍遇到 tool call 参数错误: 下一步重点验证 `additionalProperties: False` 时服务端应剔除额外字段、返回错误，还是保留当前“只记录 stderr”的行为。
4. 真实本地服务访问真实 upstream 已验证通过；若后续仍遇到线上 502，下一步应对比线上部署环境、网络、端口代理、环境变量和请求体差异，而不是优先怀疑当前 `maxapi_server.py` 的已测桥接逻辑。

## 进度报告四要素

- 已完成: 复核 Codex 最新提交、扩展本地兼容检查、修正测试断言、创建 STATUS.md。
- 实证: `python /c/Users/Administrator/Desktop/maxapi/_local_compat_check.py` 最终 `TOTAL 24 FAIL 0`。
- 未完成: 未决定是否提交 `_local_compat_check.py` / `STATUS.md`；未处理 `_orig.py`。
- 下一步: 若继续，先 `git -C /c/Users/Administrator/Desktop/maxapi status --short`，再按目标选择提交检查脚本/STATUS 或清理本地备份 `_orig.py`。
