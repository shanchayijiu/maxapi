# Opus5 dual-buffer plan + review

model=Claude Opus 5

## Plan (dt=77.87s stop=end_turn)

# maxapi 身份双缓冲（A/B Standby Warm）最小可行实施方案

---

## 1. 问题定义

### 当前单槽时间线延迟分解

```
请求 N:  成功 got_done → mark_ok → ok_count=2 → 达到 MAX
请求 N+1: acquire → 发现空槽 → rand_ip() → 新 IP
          ├─ 新 IP 无 cookie → CookieJar 空 → companion cold start
          ├─ companion 请求 /api/auth/session → 上游 TTFB (~500-2000ms)
          ├─ 若 companion 拿到 cookie → 后续可用
          └─ 若 companion 也 quota/busy → 额外 retry 延迟
请求 N+1 的体感延迟 = 新 IP 获取 + companion 冷启动 + 上游 TTFB
```

### 可感延迟的三段

| 段 | 来源 | 双缓冲能否消除 |
|---|---|---|
| **退役冷启动** | 新 IP 空 cookie → companion 冷启动 | ✅ **完全消除**：standby 已预热 |
| **companion TTFB** | 即使 cookie 就绪，首次请求仍需 round-trip | ⚠️ **部分消除**：standby 已有 cookie，省掉 auth round-trip，但首次 chat 请求本身 TTFB 仍在 |
| **上游 quota/busy** | 真实限流，与身份无关 | ❌ **不能消除** |

**结论：双缓冲主要消除「退役后首次请求的 cookie cold start 延迟」，约 1-3 秒。**

---

## 2. 推荐设计

### 2.1 数据结构

```python
# 每槽字段
@dataclass
class IdentitySlot:
    ip: str = ""
    ok_count: int = 0
    max_ok: int = 0          # 从 MAXAPI_IDENTITY_MAX_OK 读入
    cookie_gen: int = 0      # 与 CookieJar.generation 绑定
    warmed: bool = False     # 是否至少成功过 1 次（cookie 有效）
    warming: bool = False    # 后台 warm 中
    created_at: float = 0.0  # time.time()

# 全局 buffer（单例）
@dataclass
class DualBuffer:
    active: IdentitySlot = field(default_factory=IdentitySlot)
    standby: IdentitySlot = field(default_factory=IdentitySlot)
    lock: threading.Lock = field(default_factory=threading.Lock)
```

### 2.2 状态机

```
                    ┌─────────────────────────────────┐
                    │                                 │
                    ▼                                 │
    ┌─────────┐  mark_ok(x2)  ┌──────────┐  swap    ┌──────────┐
    │ ACTIVE  │ ─────────────►│ RETIRING │────────►│ STANDBY  │
    │ warm    │               │          │          │ promoted │
    │ ok<MAX  │               └──────────┘          │ ok=0     │
    └─────────┘                      │              └──────────┘
         ▲                           │                    │
         │                           │ retire             │ mark_ok(x2)
         │                           ▼                    ▼
         │                      ┌──────────┐        ┌──────────┐
         │                      │  IDLE    │        │  NEW     │
         │                      │  清理    │◄───────│ STANDBY  │
         │                      │  IP 丢弃 │  warm  │ warming  │
         │                      └──────────┘  完成  └──────────┘
         │                           ▲
         └───────────────────────────┘
```

### 2.3 核心流程

#### `identity_acquire(force_new=False)`

```python
def identity_acquire(force_new=False):
    with _dual_buffer.lock:
        if force_new or _dual_buffer.active.ip == "":
            # 强制新身份或首次初始化
            if _dual_buffer.standby.warmed and not _dual_buffer.standby.warming:
                # ✅ 有热 standby → 直接提升
                _swap_active()
                log("[SWAP] standby promoted to active, ip=%s", _dual_buffer.active.ip)
                return _dual_buffer.active.ip
            else:
                # 冷路径：当前行为
                new_ip = rand_ip()
                _init_slot(_dual_buffer.active, new_ip)
                return new_ip

        # 正常路径：active 有余量
        if _dual_buffer.active.ok_count < _dual_buffer.active.max_ok:
            return _dual_buffer.active.ip

        # active 已满 → 尝试 swap
        if _dual_buffer.standby.warmed and not _dual_buffer.standby.warming:
            _swap_active()
            log("[SWAP] active exhausted, standby promoted, ip=%s", _dual_buffer.active.ip)
            _ensure_standby_warming()  # 启动新 standby warm
            return _dual_buffer.active.ip
        else:
            # standby 未就绪 → 退回冷路径（最差情况）
            new_ip = rand_ip()
            _init_slot(_dual_buffer.active, new_ip)
            log("[WARN] no warm standby, cold acquire ip=%s", new_ip)
            _ensure_standby_warming()
            return new_ip
```

#### `identity_mark_ok(ip)`

```python
def identity_mark_ok(ip):
    with _dual_buffer.lock:
        if ip == _dual_buffer.active.ip:
            _dual_buffer.active.ok_count += 1
            _dual_buffer.active.warmed = True
            log("[OK] active ip=%s ok=%d/%d", ip, _dual_buffer.active.ok_count, _dual_buffer.active.max_ok)

            # 第一次成功 → 触发 standby warm
            if _dual_buffer.active.ok_count == 1:
                _ensure_standby_warming()

            # 达到 MAX → 如果 standby 已 warm，立即 swap（下次 acquire 用）
            if (_dual_buffer.active.ok_count >= _dual_buffer.active.max_ok
                    and _dual_buffer.standby.warmed
                    and not _dual_buffer.standby.warming):
                _swap_active()
                _ensure_standby_warming()
                log("[SWAP] proactive swap after max ok, new active=%s", _dual_buffer.active.ip)
        else:
            # ip 不匹配 active（可能是旧身份的迟到 ok）
            log("[OK] stale ok ignored, ip=%s active=%s", ip, _dual_buffer.active.ip)
```

#### `_swap_active()` (内部)

```python
def _swap_active():
    """调用方已持锁"""
    old = _dual_buffer.active
    new = _dual_buffer.standby

    # 退役旧 active
    old.ip = ""
    old.ok_count = 0
    old.warmed = False

    # 提升 standby → active
    _dual_buffer.active = new
    # standby 清空，准备接收新的 warm 结果
    _dual_buffer.standby = IdentitySlot(max_ok=old.max_ok if old.max_ok else _max_ok_default)
```

#### `_ensure_standby_warming()` (内部，可后台)

```python
def _ensure_standby_warming():
    """调用方已持锁，检查后可释放锁再起线程"""
    if _dual_buffer.standby.warming or _dual_buffer.standby.warmed:
        return
    _dual_buffer.standby.warming = True
    slot_ref = _dual_buffer.standby  # 引用传递

    def _warm():
        try:
            new_ip = rand_ip()
            slot_ref.ip = new_ip
            # 用新 IP 做一次轻量 warm（如 companion refresh 或 /models）
            _companion_warm_for_ip(new_ip)  # 复用现有 companion 逻辑
            with _dual_buffer.lock:
                slot_ref.warmed = True
                slot_ref.warming = False
                log("[WARM] standby ready, ip=%s", new_ip)
        except Exception as e:
            with _dual_buffer.lock:
                slot_ref.warming = False
            log("[WARM] standby warm failed: %s", e)

    threading.Thread(target=_warm, daemon=True).start()
```

### 2.4 与现有子系统的关系

| 子系统 | 动作 |
|---|---|
| `identity_acquire()` | **重写**：增加 buffer 检查和 swap 逻辑 |
| `identity_mark_ok()` | **重写**：增加 buffer 状态推进 |
| `identity_retire()` | **保留签名**，内部改为只处理 quota 退役（不处理正常退役，正常退役由 swap 完成） |
| `CookieJar` | **不改结构**；per-identity 通过 `cookie_gen` 隔离：swap 时新 active 继承 standby 的 cookie_gen |
| `sticky_*` 子系统 | **不动**：当前主路径未用，保持原样 |
| `rand_ip()` | **不动** |
| `COOKIE_JAR.get_header()` | **不改**：cookie 通过 generation 与 identity 绑定，不串 |

### 2.5 Cookie 隔离

```
当前：CookieJar 是全局单 dict，generation 单计数器
方案：不改 CookieJar 结构，改为 acquire 时绑定 generation

identity_acquire() 返回的 xff，后续 get_header() 用与该 xff 绑定的 generation
  → generation_map[ip] = gen
  → clear(bump=True) 时 bump 并写入 generation_map[ip]
  → 如果 generation 不匹配则丢弃（现有逻辑已支持）
```

**最小改动**：在 `DualBuffer` 每槽增加 `cookie_gen: int`，swap 时 `cookie_gen` 跟随 slot 转移，不改 CookieJar 内部。

### 2.6 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MAXAPI_IDENTITY_MAX_OK` | `2` | 每身份最大成功次数（现有） |
| `MAXAPI_DUAL_BUFFER` | `1` | 0=退回单槽，1=启用双缓冲 |
| `MAXAPI_STANDBY_WARM_TIMEOUT` | `30` | standby warm 超时秒数 |
| `MAXAPI_STANDBY_WARM_RETRY` | `2` | warm 失败重试次数 |

### 2.7 并发锁粒度

- **锁对象**：`DualBuffer.lock`（单个 `threading.Lock`）
- **临界区**：仅 `_swap_active()` 和 `ok_count` 增减
- **锁持有时间**：~微秒级（纯内存操作）
- **warm 线程**：获取锁仅在 warm 完成时写入 `warmed=True`，warm 过程本身不持锁

---

## 3. 明确不做

| 不做的扩展 | 原因 |
|---|---|
| N 槽轮转（>2） | 风控特征翻倍，维护复杂度超收益 |
| 跨请求 cookie 预热（在请求空闲期主动 warm） | 增加额外 upstream 请求，可能触发异常检测 |
| 改 CookieJar 为 per-identity dict | 保护区约束：CookieJar 是全局单例，整块重写风险高 |
| sticky 子系统集成 | 当前主路径未用，改了也不生效 |
| 多用户账号池 | 明确不做（产品决策） |
| IP 段筛选/geo 路由 | 超出旁路范畴 |
| 移除 busy≠quota 区分 | 保护区约束 |

---

## 4. 最小 Diff 落点

### 改动清单

```
maxapi_server.py:
  1. 新增 IdentitySlot / DualBuffer dataclass（约 20 行）
  2. 新增 _dual_buffer 全局实例（约 3 行）
  3. 新增 _swap_active()（约 15 行）
  4. 新增 _ensure_standby_warming()（约 25 行）
  5. 重写 identity_acquire()：在现有逻辑前加 buffer 检查分支（约 30 行，保留冷路径 fallback）
  6. 重写 identity_mark_ok()：增加 buffer 推进（约 15 行）
  7. 修改 identity_retire()：仅处理 quota 退役，不处理正常退役（约 10 行微调）
  8. 新增环境变量解析（约 5 行）
  新增代码总量：约 120 行
```

### 建议补的日志字段

```python
log("[DUAL] acquire: active=%s ok=%d/%d standby=%s warmed=%s",
    slot.ip, slot.ok_count, slot.max_ok, standby.ip, standby.warmed)
log("[SWAP] promoted standby=%s → active, old_active=%s ok=%d",
    new.ip, old.ip, old.ok_count)
log("[WARM] start ip=%s", slot.ip)
log("[WARM] done ip=%s latency_ms=%d", slot.ip, elapsed)
log("[WARM] failed ip=%s reason=%s", slot.ip, e)
log("[RETIRE] quota ip=%s (dual_buffer=%s)", ip, _dual_buffer_enabled)
```

---

## 5. 验收

### 5.1 日志期望序列

正常双缓冲流程（MAX_OK=2）：

```
[DUAL] acquire: active=1.2.3.4 ok=0/2 standby= warmed=False
[OK] active ip=1.2.3.4 ok=1/2
[WARM] start ip=5.6.7.8                          ← 第一次 OK 触发 standby warm
[DUAL] acquire: active=1.2.3.4 ok=1/2 standby=5.6.7.8 warmed=False
[OK] active ip=1.2.3.4 ok=2/2
[SWAP] start ip=5.6.7.8                          ← standby 已就绪，proactive swap
[WARM] done ip=5.6.7.8 latency_ms=1200
[DUAL] acquire: active=5.6.7.8 ok=0/2 standby= warmed=False   ← 第三次请求无冷启动！
[WARM] start ip=9.10.11.12                      ← 新 standby 开始 warm
```

### 5.2 最小 live 探针

```bash
# 1. 启用双缓冲
export MAXAPI_DUAL_BUFFER=1
export MAXAPI_IDENTITY_MAX_OK=2

# 2. 连续打 4 次请求（应看到 swap + 无冷启动）
for i in $(seq 1 4); do
  curl -s http://127.0.0.1:8080/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"gpt-4","messages":[{"role":"user","content":"ping"}],"max_tokens":1}' \
    | jq -r '.choices[0].message.content // .error.message'
  echo "--- request $i done ---"
done

# 3. 检查日志
docker logs <container> 2>&1 | grep -E "\[SWAP\]|\[WARM\]|\[DUAL\]" | tail -20
```

**期望**：第 3 次请求的日志中应出现 `[DUAL] acquire: active=<新IP> ok=0/2`，且无 `[WARN] no warm standby`。

### 5.3 回归范围

| 套件 | 必须绿 |
|---|---|
| healthz 端点 | ✅ |
| 基础 chat completions | ✅ |
| quota 触发 → retire → 新身份 | ✅ |
| busy/529 → 同身份 retry | ✅ |
| `MAXAPI_DUAL_BUFFER=0` 回退单槽 | ✅ |
| CookieJar generation 隔离 | ✅ |
| Docker build + sha 对齐 | ✅ |

---

## 6. 风险与回滚

### 6.1 风险缓解

| 风险 | 缓解 |
|---|---|
| **风控：双 IP 同时活跃被检测** | standby warm 用轻量请求（/models 或 /auth/session），不发 chat；且 warm 完成后旧 IP 立即退役，重叠窗口 <1s |
| **串 cookie：swap 后 cookie 混用** | 每槽独立 `cookie_gen`，swap 时 generation 跟随转移，不跨槽写入 |
| **standby warm 卡死** | `MAXAPI_STANDBY_WARM_TIMEOUT` 超时后标记 `warming=False`，下次 acquire 走冷路径 fallback |
| **并发 mark_ok 竞态** | 单锁保护，临界区 <1μs |
| **Docker healthz sha 不对齐** | 改完后本地 `sha256sum maxapi_server.py` 与 Dockerfile COPY 目标对齐 |

### 6.2 一键回滚开关

```bash
# 环境变量控制
MAXAPI_DUAL_BUFFER=0   # 立即退回单槽逻辑，零代码改动
```

代码层面：`identity_acquire()` 入口处：

```python
if not _dual_buffer_enabled:
    # 原有单槽逻辑，一字不改
    ...
```

回滚步骤：
1. 设 `MAXAPI_DUAL_BUFFER=0`
2. 重启容器
3. 确认日志无 `[DUAL]`/`[SWAP]`/`[WARM]` 出现
4. 冒烟 2 次请求确认正常

---

**施工优先级**：先实现 `_swap_active` + `_ensure_standby_warming` + `identity_acquire` buffer 分支（核心 3 个函数），跑通冒烟后再补日志字段和边界 case。

## Review (dt=50.55s stop=end_turn)

好的，我将作为严谨的审查者，对这份“双缓冲”方案进行逐项挑刺。

---

### **1. 致命问题（不修不能合）**

1.  **`_swap_active()` 存在竞态，可能串 cookie 或丢失身份**
    *   **问题**：`_swap_active()` 操作两个 slot 对象（`old` 和 `new`），虽然调用方声称已持锁，但 `_swap_active` 内部操作 **不原子**。`new = _dual_buffer.standby` 后，`new` 对象被 `active` 引用，而 `standby` 槽被清空。此时，若有另一个线程（如在 `identity_mark_ok` 中）正在处理对 `new`（即旧的 standby）的 `mark_ok`，可能导致状态混乱。
    *   **具体风险**：线程A在 `identity_acquire` 中持锁并开始 `_swap_active`，将 standby（IP=B, ok=1）提升为 active。线程B此时完成了对 IP=B 的请求，在 `identity_mark_ok` 中也持锁，但发现 `ip == active.ip`（此时 active 已是 B），于是 `active.ok_count += 1`（从0变为1）。这看似无害，但破坏了“standby提升时ok_count应为0”的不变量，可能导致提前触发下一次 swap。
    *   **修正**：`_swap_active` 必须 **原子地** 操作整个 `DualBuffer` 结构。应在锁内完成对 `active` 和 `standby` 两个指针/引用的交换和状态重置，而非先取出旧对象再修改。最安全的做法是**重建 `standby` slot 对象**，而不是复用旧的。

2.  **双身份并行活跃，触发风控的概率从“偶尔”变为“必然”**
    *   **问题**：方案将风控风险缓解描述为“重叠窗口 <1s”。但这建立在 `_companion_warm_for_ip`（可能是网络请求）瞬间完成的错误假设上。实际上，`_warm` 线程中执行 `_companion_warm_for_ip(new_ip)` 本身就是一个 **耗时的上游请求**，在此期间，旧的 active IP 仍用于服务正式请求。这必然产生一个 **可长达数秒的双IP并行窗口**，远比当前“退役-新建”串行流程更容易被风控模型（如“同一客户端短时从两个IP发起行为”）捕获。
    *   **修正**：必须接受风控风险或放弃此设计。若坚持双缓冲，应将 warm 过程的 **网络 I/O 彻底移出请求路径**，并允许其有较长的异步时间。但即便如此，只要新旧 IP 共同活动，风险就高于现状。

3.  **Cookie 隔离方案与现有 CookieJar 机制冲突，存在串 cookie 风险**
    *   **问题**：方案提出“generation_map[ip] = gen”的思路。但 `COOKIE_JAR.get_header()` 是一个 **全局单例**，它如何知道当前请求应该返回哪个 `ip` 对应的 cookie？当前逻辑是：`identity_acquire` 返回 `xff`（即 IP），后续代码使用 `COOKIE_JAR.get_header()`。`get_header()` 内部很可能基于一个全局的 `_current_ip` 或 `_current_gen` 来返回。双缓冲引入两个并行的 IP，**谁在什么时候设置 `_current_ip`**？方案未说明。如果设置不当，线程A用IP1的cookie去请求，但`get_header()`返回的是IP2的cookie，即发生串cookie。
    *   **修正**：必须明确 `COOKIE_JAR.get_header()` 如何与双缓冲的 slot 绑定。最小改动可能是在 `identity_acquire` 返回 `xff` 的同时，返回该 `xff` 对应的 `cookie_gen`，然后由调用方显式传入 `COOKIE_JAR.get_header(generation=gen)`。但这需要修改 `get_header` 签名，侵入 CookieJar，**违反了“不改 CookieJar 内部”** 的约束。否则，必须在 `identity_acquire` 内部，**在锁内**，原子性地完成 `xff` 和 CookieJar 当前代际的绑定，这几乎不可能，因为 CookieJar 是全局的。

4.  **busy/529 路径被破坏，可能导致无意义的身份轮转**
    *   **问题**：当前逻辑是“busy/529 → 同身份 sleep，不换身份”。双缓冲方案引入了 `identity_retire()` 的改变，但未清晰定义 **busy 场景下是否还触发 `identity_retire`**。如果一个请求因 busy 失败，按当前代码，会调用 `identity_retire`。在双缓冲下，这个 `retire` 是否会错误地退役当前 active 身份？方案中 `identity_retire` 被标注为“仅处理 quota 退役”，但代码入口是同一个函数。如何区分？如果通过参数或全局状态区分，增加了复杂性；如果无法区分，则 busy 会导致意外退役和 swap，引入不必要的延迟和风控风险。
    *   **修正**：必须在 `identity_retire` 的调用源头或函数内部，**严格区分** quota退役和 busy退役。busy退役不应触发任何 buffer swap 或状态推进。

5.  **`_ensure_standby_warming` 在请求路径中被同步调用，可能阻塞 TTFB**
    *   **问题**：在 `identity_acquire` 和 `identity_mark_ok` 中，多处调用了 `_ensure_standby_warming()`。虽然其内部检查后会启动一个 daemon 线程，**但 `_ensure_standby_warming` 本身是在持锁状态下被调用的**。如果锁竞争激烈，或线程创建本身有开销，这会在关键路径上引入微小但确定的额外延迟。更重要的是，它**掩盖了设计意图**：warm 应是完全异步的，不应在请求逻辑中被“确保”。
    *   **修正**：warm 的触发逻辑应移出关键路径。例如，可在 `identity_mark_ok` 中，当 `ok_count == 1` 时，**不调用** `_ensure_standby_warming`，而是通过一个无锁的标志位或消息队列，通知后台任务检查是否需要启动 warm。

### **2. 重要问题（应修）**

1.  **锁粒度与复杂度不匹配**：使用了全局 `DualBuffer.lock`，但 warm 线程需要频繁获取锁来更新状态。如果 warm 操作（`_companion_warm_for_ip`）耗时长，虽然 warm 过程不持锁，但成功/失败时的 **状态更新需要获取锁**，这可能导致锁在 `identity_acquire`/`mark_ok` 中被短暂阻塞。对于微秒级锁，这不是性能问题，是设计复杂度问题，增加了死锁或遗漏更新的可能性。
2.  **`cookie_gen` 与 CookieJar 的耦合未解决**：如致命问题第3点，这是核心矛盾。若不修改 `COOKIE_JAR.get_header()`，则每个 slot 的 `cookie_gen` 仅是个记录，**无法强制影响 `get_header()` 的返回**，隔离形同虚设。
3.  **失败路径状态清理不彻底**：`_warm` 线程中，如果 `_companion_warm_for_ip` 失败，仅将 `warming=False`，但 `ip` 字段可能已被设置为一个无效 IP，且 `warmed=False`。下次 `identity_acquire` 检查 `standby.warmed` 为 false，会走冷路径，但此时 `standby.ip` 可能存有一个无效值。虽然下次 `_
