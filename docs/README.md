# maxapi 文档地图

> 2026-08-21 建立。产品口径见下；细节以代码 + live `8080/healthz` 为准。

## 产品口径（写死）

| 概念 | 含义 |
|------|------|
| **API 化** | Claude Code / Codex / OpenAI SDK 等 **Agent** 只换 `base_url`+`api_key`，工具/流式/thinking/多轮接近官方 API |
| **多用户 / Admin / 账号池 / 计费** | **不做**。前面接 **NewAPI**（或多 key 网关）自行解决 |
| **maxapi 角色** | 单实例上游适配器：`se.zzmax.cn` 访客旁路 → OpenAI/Anthropic/Responses wire |

默认部署拓扑：

```text
Agent 客户端  →  (可选 NewAPI)  →  maxapi:8080  →  se.zzmax.cn
```

## 先读谁

| 顺序 | 文件 | 职责 |
|------|------|------|
| 1 | [`../STATUS.md`](../STATUS.md) | 续做：现状、§0.5 目标锚、§1 保护区、§2 卡点、真跑命令 |
| 2 | 本文 | 地图与口径 |
| 3 | [`../README.md`](../README.md) | 部署、接入、验收命令 |
| 4 | 下表 L3 | 改对应子系统时再读 |

## 分层

```text
L0  STATUS.md                 进行到哪 / 卡点 / 保护区
L1  STATUS.md §0.5            要去哪（唯一目标锚；不维护第二份 GOALS 全文）
L2  README.md                 用法 / 部署 / Agent·NewAPI 接入
L3  docs/*.md（本目录）        架构、上游、tool、延迟、wire、工程契约
L4  _runtime/v4_*             门禁证据（只链不抄）
L5  docs/archive/             过时史与过程稿（默认不读）
```

## L3 文件

| 文件 | 内容 | 何时改 |
|------|------|--------|
| [architecture.md](./architecture.md) | 主链路、逻辑模块、旁路边界 | 主链路/端点变 |
| [upstream-se-zzmax.md](./upstream-se-zzmax.md) | 上游实证与限制 | 上游行为变 |
| [toolcall-semantics.md](./toolcall-semantics.md) | DSML / parser / 泄漏不变量 | parser·prompt 变 |
| [latency-and-escalate.md](./latency-and-escalate.md) | 慢路径、escalate、旋钮 | 策略变 |
| [agent-wire.md](./agent-wire.md) | Agent 视角契约 + NewAPI 注意 | wire 变 |
| [dev-contract.md](./dev-contract.md) | 红线、改前/后验收、完成判定 | 流程约定变 |
| [reference-2api.md](./reference-2api.md) | cursor2api/ds2api 借鉴：可采纳/禁止 | 对照结论变 |

## 权威源冲突时

1. live `GET /healthz` + 运行中二进制  
2. 当前 `maxapi_server.py`  
3. `_runtime/v4_consistency_report.json`  
4. `STATUS.md`  
5. 本目录与 README  
6. `docs/archive/` 与旧 memory — **最后**，且可能过时  

## 明确不建的文档

- Admin / WebUI / 多租户鉴权矩阵  
- 账号池产品说明（访客 identity 轮换写在 upstream 文即可）  
- 双语镜像文档  

## archive

见 [archive/](./archive/)。含错误 `project_maxapi.WRONG.md`、旧 STATUS 全文、`_sol_*`、audit、SHIP 报告。
