# maxapi 工程契约（dev-contract）

> 从原 `GOALS.md` 迁入（2026-08-21）。目标锚在 `STATUS.md` §0.5；本文只保留**红线 / 门禁 / 完成判定**。  
> 动 `maxapi_server.py` / Docker / 协议路径前通读本文 + STATUS §1。

## 0. 产品边界（再强调）

- **目的**：Agent drop-in（CC / Codex / OpenAI SDK）wire 可用、内容干净、别无故慢。  
- **不做**：多用户、Admin、账号池产品、计费、商用配额 UI → 用 **NewAPI**（或同类）前置。  
- **不做**：整文件重写、顺手重构、放宽测试注水、无复现堆 escalate。

## 1. 用户红线

1. 禁止删测试、放宽断言、减轮次来「让 CI 变绿」。  
2. 禁止碰已稳访客旁路核心（除非用户点名且最小接缝）。见 STATUS §1 / §1.6。  
3. 禁止顺手重构、整文件重写、统一抽象、「顺便现代化」。  
4. 禁止为修 A 踩烂 B：chat / messages / responses / escalate / pool 任一侧改动须回归另外侧。  
5. 禁止无 live 复现与根因的推测式大补丁。  
6. 禁止与用户正在用的 Codex/57321 抢流量做探针；只打 `127.0.0.1:8080` + 独立 Authorization 标识。  
7. 禁止凭印象还原代码；有 git/bak 用原件。  
8. 禁止 partial yield 后仍甩 fatal；stall 在已有输出时干净收尾。  
9. 禁止连接池 `close` 后再 `put`（死 socket → BrokenPipe）。  
10. 禁止把「未做 Admin/多用户」当成 maxapi 缺陷去「补产品」。

## 2. 改动前门禁

```
[ ] 1. 读 STATUS.md §0.5 + §1 + 本文
[ ] 2. 一句话写清用户可感症状（不是「优化 XX 模块」）
[ ] 3. 最小触碰面；明确「不改」哪些共享层
[ ] 4. 先有复现：8080 live 或单测能红
[ ] 5. 方案 = 最小 diff + 根因；retry/timeout/escalate 是最后手段
```

## 3. 改动后验收

### 3.1 任何协议/流式/工具相关

```
[ ] 8080 Docker 已是当前代码（rebuild + MAXAPI_GIT_COMMIT=HEAD）
[ ] curl /healthz：binarySha256 与主机 maxapi_server.py 一致
[ ] 纯文本流式：短答 + 较长列表不截断、有 finish/[DONE]
[ ] 工具：responses stream + chat stream + chat nonstream 至少各 1 次 auto tool
[ ] 日志：无 BrokenPipe 风暴；无无意义连续 tool-escalate ×3
[ ] 若动 parser/终止路径：跑 v4 L0/finalize/inv03/leak-g/mutants + build-report/validate
[ ] 刷 STATUS §0/§2；若动 tool/慢/wire 同步 docs 对应篇
[ ] git commit（用户要求时）；禁止把密钥/accounts 提交
```

### 3.2 改了 ToolCallParser / DSML / thinking

```
[ ] parser 单测或 compat 相关项
[ ] thinking 内嵌 tool 能抽出；reasoning 无原始标签泄漏
```

### 3.3 改了 escalate / prefetch / terminal-force

```
[ ] 动作型 auto 有 tool；纯聊天 auto 不误强制
[ ] 成功路径总 RTT 可接受；mid-flight 已完成门控仍在
```

### 3.4 全量回归（大改或 /ship）

```
[ ] python _local_compat_check.py      # FAIL 0
[ ] python _accept_tool_suite.py       # FAIL 0
[ ] python _accept_agent_long.py       # FAIL 0
[ ] python -u _openai_sdk_gold.py      # 20/20（环境允许时）
```

## 4. 易碎点（改前先查）

| 症状 | 优先查 |
|------|--------|
| 完全不能用 | 连接池 close+put、BrokenPipe、57321 白名单 ≠ maxapi |
| 有 tools 客户端收不到 | chat stream 是否转发 `kind==tool_call`；think 是否过 tparser |
| 标签泄漏到画面 | dual parser、flush-discarded、singular/function_calls 归一化 |
| 一句话就断 | partial 后还 yield error；stall 当 fatal |
| 特别慢 | escalate 多轮、prefetch max_wait、effort=max、concurrency、quota 连刷 |
| deepseek 非流式有 tool 流式无 | token body 解析 + chat stream emit |
| 只改了主机 py 行为没变 | 容器未 rebuild/cp |

## 5. 完成判定（对用户说「好了」前）

1. 可感症状在 8080 live 消失（有命令输出）。  
2. STATUS §1 主路径回归仍通。  
3. 代码已进容器（若用 Docker 8080）。  
4. 有 commit（及要求的文档）。  
5. 残留必须写明，不把半成品说成完成。

## 6. 文档维护

1. 开工读 STATUS §0/§0.5/§1/§2（应 <15 分钟读完）。  
2. **禁止**再往 STATUS 追加长篇「2026-xx-xx 改动详情」；日更仅顶部 **1 行** blockquote。  
3. 目标变更只改 STATUS §0.5 并留痕。  
4. archive 默认不读。  
5. 文档与 live 冲突 → 以 healthz + 代码为准，先改文档。  
6. 有人提用户系统/Admin → 回答：不做，用 NewAPI。

## 7. 非目标（默认不做）

- 替用户改 Codex 安装/57321 白名单（可诊断）。  
- 多用户/Admin/账号池产品/计费。  
- 扩大攻击面、无关扫盘。  
- 为「更像官网后台」重写整站。  
- 无要求的微优化、目录整理、日志美化。
