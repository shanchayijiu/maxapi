# Opus5 双缓冲：规划 + Review 综合裁决（2026-08-21）

> 原料：maxapi `Claude Opus 5` 两轮  
> `_runtime/v4_evidence/opus5_dual_buffer_plan_review_20260821.md`  
> 本文件 = 主会话综合（含对 Review 误判的纠正）

## 1. 延迟账（Plan 与现状一致）

| 段 | 双缓冲能否消 |
|----|----------------|
| 2 OK 退役后下一请求 **cookie 冷启动 / companion** | ✅ 主要收益，估 **1–3s** |
| 上游 **busy/529 退避** | ❌ |
| 模型 **TTFB / thinking** | ❌ |
| 真 **quota** 换号 | ⚠️ 可加快切到已 warm 的 B，但 B 也可能马上 quota |

**结论**：双缓冲不是「追上网页」的全部答案，只砍「第 3 请求顿一下」里的冷身份段。网页慢感若仍在，多半是 busy + 模型本身。

## 2. Plan 摘要（Opus5 规划）

- 结构：`active` + `standby` 两槽；`MAX_OK` 仍默认 2  
- 第 1 次 OK → 后台 warm standby  
- 第 2 次 OK 或 acquire 时 active 已满 → 有热 standby 则 **swap**  
- `MAXAPI_DUAL_BUFFER=0` 回退单槽  
- sticky_* 不动；声称不改 CookieJar 结构  

## 3. Review 致命点（Opus5 挑刺）— 主会话裁定

| # | Review 主张 | 主会话裁定 |
|---|-------------|------------|
| 1 | swap / mark_ok 竞态破坏 ok 不变量 | **成立**。swap 必须锁内原子替换 slot；提升后的 active **ok 必须归零**；迟到 mark_ok 对非「本请求租约 ip」一律 ignore |
| 2 | warm 期间双 IP 并行，风控窗口秒级 | **成立**。方案写「重叠 <1s」过乐观。要做就**接受**更高并行特征，或改为「仅预生成 IP、**不**预打 companion」（收益变小） |
| 3 | 全局 CookieJar 无法 per-ip get_header → 串 cookie | **成立且是核心阻塞**。现 `get_header()` 无 ip 参数；双槽若并行 warm 写同一 jar → 必串。仅 slot 上挂 `cookie_gen` **不够** |
| 4 | busy 可能误 retire/swap | **半错**。源码 **busy 不调用** `identity_retire`，只有 quota 调。但双缓冲实现时仍须保证 busy 路径 **零 swap** |
| 5 | 请求路径持锁调 warm 影响 TTFB | **部分成立**。应只 set flag / 启 daemon，**禁止**在持锁期做网络；线程 create 开销可忽略，但设计要干净 |

## 4. 裁决

**Conditional Go（先改 cookie 模型，再谈 swap）**，不是直接按原 Plan 开干。

### 若做：修正后的最小方案（比 Opus5 Plan 更窄）

1. **默认关**：`MAXAPI_DUAL_BUFFER=0`；显式 `=1` 才启用（Plan 默认 1 太激进）。  
2. **Cookie 二选一（必须先定）**  
   - **A（更真双缓冲）**：`CookieJar` 改为 `dict[ip] -> cookies` 或 `dict[gen] -> cookies`，`get_header(ip|gen)`；warm/swap 带 ip。**碰 jar，但是最小接缝扩展 API，不整文件重写。**  
   - **B（假双缓冲 / 低风险）**：standby **只预生成 IP + 空 jar 策略不变**；swap 时仍 `clear` 再 warm——**几乎消不掉冷启动**，别做。  
   - **C（推荐折中）**：不并行双 cookie。时间线改为：  
     - active 用到 ok==1 时，**后台** `rand_ip` 放进 standby.ip，**不**写全局 jar；  
     - active 第 2 次 OK 返回后（响应已结束），**异步**：`clear` jar → 把 sticky/active 指针切到 standby.ip → companion warm；  
     - **下一请求**若 warm 已完成则热，未完成则与今天相同冷路径。  
     这样 **任意时刻 jar 仍单身份**，无串 cookie；收益 = 把 companion 从「第 3 请求临界路径」挪到「第 2 请求结束后的空窗」。  
3. **API 表面尽量不动**：`identity_acquire/mark_ok/retire` 签名保留；内部按 flag 分支。  
4. **quota**：retire active + 若 standby 热则 swap，否则 force_new；**busy：禁止 retire/swap**。  
5. **观测**：`[DUAL] swap hit|miss`、`warm_ms`、`cold_fallback`；探针 4 连发看第 3 枪是否 miss。  
6. **回归**：healthz sha、CC 探针、tool 冒烟、busy 同身份（日志无 swap）、`DUAL_BUFFER=0` 行为与现网一致。

### 明确不按 Plan 原文做的部分

- 不在请求路径持锁保证 warm  
- 不假设重叠窗口 <1s  
- 不「零改 CookieJar」却声称 per-identity cookie  
- 不默认打开双缓冲  

## 5. 和「网页一样快」的预期管理

即使 C 方案完美：  
- 省的是 **~1–3s 退役冷启动**（有时有、有时没有）  
- **省不了** busy sleep、模型 TTFB、长 thinking  

若用户体感慢是「每条都慢」而非「每隔一条顿一下」，优先查：effort、上游 busy 率、并发，而不是双缓冲。

## 6. 建议下一步（需用户点名）

1. **先观测**（不改旁路）：连续请求日志里 `identity retire after 2 ok` 后下一条 RTT / 是否 companion / quota——确认痛点是不是冷启动。  
2. 若确认 → 按上面 **方案 C** 最小 diff 落地（默认关，可 env 开）。  
3. 若痛点是 busy → 降并发/rpm 或上游侧，不做双缓冲。

证据路径：`opus5_dual_buffer_plan_review_20260821.json` / `.md` / 本综合稿。
