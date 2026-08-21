# maxapi

se.zzmax.cn **访客旁路** → 标准 **OpenAI Chat Completions** / **Anthropic Messages** / **OpenAI Responses**。  
给 **Claude Code、Codex、OpenAI SDK** 等 Agent 用：只换 `base_url` + `api_key`。

纯 Python 标准库、单文件 `maxapi_server.py`、Docker 默认 **8080**。

## 是什么 / 不是什么

| 是 | 不是 |
|----|------|
| Agent drop-in 兼容层（工具/流式/thinking/多轮） | 多用户网关、Admin、账号池产品、计费 |
| 单实例上游适配器 | SaaS 控制面 |

多 key / 用户 / 配额：前面接 **[NewAPI](https://github.com/Calcium-Ion/new-api)**（或同类）。拓扑：

```text
Agent  →  (可选 NewAPI)  →  maxapi:8080  →  se.zzmax.cn
```

续做与卡点：[`STATUS.md`](./STATUS.md)。文档地图：[`docs/README.md`](./docs/README.md)。工程红线：[`docs/dev-contract.md`](./docs/dev-contract.md)。

## 快速部署（Docker 8080）

```bash
# 构建时写入 commit，便于 healthz 对照
export MAXAPI_GIT_COMMIT=$(git rev-parse HEAD)
docker build -t maxapi-server:latest .
docker rm -f maxapi-server 2>/dev/null
docker run -d --name maxapi-server -p 8080:8080 \
  -e MAXAPI_GIT_COMMIT="$MAXAPI_GIT_COMMIT" \
  maxapi-server:latest

curl -sS http://127.0.0.1:8080/healthz
# 确认 binarySha256 与当前 maxapi_server.py 一致后再测业务
```

直接跑（开发）：

```bash
python maxapi_server.py --host 127.0.0.1 --port 8080
```

常用环境变量：`MAXAPI_DEFAULT_EFFORT`（默认 medium）、`MAXAPI_UPSTREAM_CONCURRENCY`、`MAXAPI_TOOL_ESCALATE`、`MAXAPI_COMPACT`、`MAXAPI_LOG_LEVEL`。

## 接入 Agent

### 直连

- **OpenAI SDK**：`base_url=http://127.0.0.1:8080/v1`，`api_key` 任意（若未关校验则按你的部署）、`model` 用下方 live id。  
- **Claude Code**：Anthropic 兼容 base 指到主机，路径 `/v1/messages`。  
- **Codex**：`wire_api=responses` 时走 `/v1/responses`。

细则与 known deviations：[`docs/agent-wire.md`](./docs/agent-wire.md)。

### 经 NewAPI

1. Channel 上游地址 → maxapi `http://<host>:8080`  
2. 模型名与 maxapi `/v1/models` 的 **id** 对齐或可映射  
3. 超时放宽（tools auto 可能多上游 RTT）  
4. 限流与用户体系只在 NewAPI 配置  

## API 面

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/healthz` | 状态 + commit + binarySha256 + models 计数 |
| GET | `/v1/models`、`/v1/models/{id}` | 目录 |
| POST | `/v1/chat/completions` | OpenAI Chat（主 gold 面） |
| POST | `/v1/messages` | Anthropic Messages |
| POST | `/v1/responses` | OpenAI Responses |
| POST | `/v1/embeddings`、`/v1/completions` | **501** not_implemented |

契约摘要：[`docs/agent-wire.md`](./docs/agent-wire.md)。架构：[`docs/architecture.md`](./docs/architecture.md)。

### 参数要点

- `n` 仅 `1`（其它 400）  
- 多数采样参数静默忽略  
- `response_format` json_*：**软注入**，非原生严格保证  
- `reasoning_effort` / `reasoningEffort`：`off|low|medium|high|max`  
- tools：客户端标准字段 → 内部 DSML；见 [`docs/toolcall-semantics.md`](./docs/toolcall-semantics.md)  

## 模型

**以 live 为准**：`curl -sS http://127.0.0.1:8080/v1/models`。

当前 catalog（`RAW_MODELS`，11）：

| id（显示名） | 组 | 备注 |
|--------------|----|------|
| Claude Sonnet 5 | claude | |
| Claude Opus 5 | claude | 旧 4.8 / `claude-opus-4-8` 别名到此 |
| claude-opus-4-6 | claude | |
| gpt-5.6-sol | chatgpt | terra/5.5 别名到 sol |
| gpt-5.6-luna | chatgpt | |
| deepseek-v4-pro / flash | deepseek | 默认 model 常为 flash |
| qwen3.6-plus | qwen | |
| MiMo-V2.5-Pro | mimo | actual 可与 qwen 共享映射 |
| gemini-3.5-flash | gemini | |
| gemini-3.1-pro-preview | gemini | |

## 工具边界（必读）

上游 **没有** 原生 `tools` 字段。maxapi 用 prompt 诱导 + 流式 `ToolCallParser` **合成** `tool_calls` / `tool_use` / `function_call`。  
因此：可能 escalate 二次上游（更慢换成功率）；极端输出仍可能 residual 泄漏——见 STATUS 卡点 A 与 tool 文档。

## 上游与限制

- 访客 XFF 额度轮换；真实 TCP IP 无法伪造（多出口是运维事）  
- 无 image/video/audio 生成（访客 401）  
- busy≠quota；客户端不暴露上游额度中文原句  

更多：[`docs/upstream-se-zzmax.md`](./docs/upstream-se-zzmax.md)。慢路径：[`docs/latency-and-escalate.md`](./docs/latency-and-escalate.md)。

## 验收

```bash
python -u _openai_sdk_gold.py      # 20/20
python _local_compat_check.py      # FAIL 0
python _accept_tool_suite.py       # FAIL 0
python _accept_agent_long.py       # FAIL 0
```

2api v4（改 sanitizer/parser/终止后）：

```bash
python -u _runtime/v4_probe_deployed.py --out _runtime/v4_evidence/deployed_artifact.json
python -u _runtime/v4_l0_redlights.py --record _runtime/v4_evidence/l0_redlight_run.json
python -u _runtime/v4_finalize_redlight.py --record _runtime/v4_evidence/finalize_redlight.json
python -u _runtime/v4_inv03_ledger.py --record _runtime/v4_evidence/inv03_ledger.json
python -u _runtime/v4_leak_g_harness.py --record _runtime/v4_evidence/leak_g_abort.json
python -u _runtime/v4_mutants.py --out _runtime/v4_evidence/mutant_results.json
python -u _runtime/v4_audit.py build-report --out _runtime/v4_consistency_report.json
python -u _runtime/v4_audit.py validate --report _runtime/v4_consistency_report.json
```

当前 lineage 常见：`verdict=insufficient-evidence`（fail=0，unknown=mustE3）——**合法**，不是假绿。

## 探针纪律

- 只打 `127.0.0.1:8080`  
- 不要抢用户本机 Codex/其它端口上的生产流量  
- 改代码后先确认 healthz 指纹再下结论  

## 文档

| 文件 | 内容 |
|------|------|
| [STATUS.md](./STATUS.md) | 现状 / 目标锚 / 保护区 / 卡点 |
| [docs/README.md](./docs/README.md) | 地图 |
| [docs/dev-contract.md](./docs/dev-contract.md) | 红线与验收 |
| [docs/architecture.md](./docs/architecture.md) | 主链路 |
| [docs/agent-wire.md](./docs/agent-wire.md) | Agent 契约 |
| [docs/toolcall-semantics.md](./docs/toolcall-semantics.md) | DSML / parser |
| [docs/latency-and-escalate.md](./docs/latency-and-escalate.md) | 慢与 escalate |
| [docs/upstream-se-zzmax.md](./docs/upstream-se-zzmax.md) | 上游实证 |
| [docs/reference-2api.md](./docs/reference-2api.md) | cursor2api / ds2api 借鉴 |
| [GOALS.md](./GOALS.md) | 指针（全文在 STATUS §0.5） |
