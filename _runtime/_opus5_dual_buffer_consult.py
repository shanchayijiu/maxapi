# -*- coding: utf-8 -*-
"""Call maxapi Claude Opus 5 for dual-buffer identity plan + adversarial review."""
import json, sys, time, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8080"
KEY = "sk-maxapi"
MODEL = "Claude Opus 5"

CONTEXT = r'''
# maxapi 身份轮转现状（源码实证）

项目：单文件 maxapi_server.py，se.zzmax.cn 访客旁路 → OpenAI/Anthropic Agent drop-in。
保护区：XFF 伪造、CookieJar、busy≠quota 禁止整块重写；只允许最小接缝。

## 当前主路径（upstream 内）

```python
_IDENTITY_MAX_OK = int(os.getenv("MAXAPI_IDENTITY_MAX_OK", "2"))  # ~2 OK / identity / day
_identity_ip = None
_identity_ok = 0

def identity_acquire(force_new=False):
    # 单槽：force_new 或空或 ok>=MAX → rand_ip()，ok=0
    ...

def identity_mark_ok(ip):
    # 成功 SSE got_done 后 +1；达 MAX 则清空槽（retire after N ok uses）

def identity_retire(ip, reason=""):
    # quota 路径：清空槽
```

upstream 循环：
1. xff = identity_acquire(False)
2. headers X-Forwarded-For / X-Real-IP = xff；Cookie = COOKIE_JAR.get_header()（全局单 jar）
3. quota → identity_retire + COOKIE_JAR.clear(bump) + warm companion + acquire(force_new=True)
4. busy/529/stall → 同身份 sleep，不换身份
5. 成功 got_done → identity_mark_ok(xff)

另有 sticky 子系统（主路径 chat 当前未用）：
- MAXAPI_STICKY_IP_TTL 默认 600s
- session_snapshot() / rotate_sticky_ip() 与 CookieJar.generation 原子绑定
- companion warm: background_companion_refresh on empty cookie / rotate

## CookieJar
- 全局单例 jar dict
- generation bump on clear(bump=True) / sticky rotate
- update_from_response 若 generation 不匹配则丢弃（防旧身份写新 jar）

## 用户体感问题
- 工具 escalate 已默认关，CC 动作 ~6-7s；仍觉比网页慢
- 网页：同会话 sticky cookie，不每 2 次换身份
- maxapi：2 OK 后退役 → 第 3 请求新 IP，可能空 cookie → companion 冷启动；或撞 quota/busy

## 用户提出的双缓冲思路
「一直开两个，一个用到第二次时第三次直接转第二个」：
- 槽 A active，槽 B standby（已 warm cookie）
- A 第 2 次 OK → atomic swap B→active，后台再造 B'
- 目标：退役后下一次无冷启动，体感丝滑

## 约束
- 最小 diff，不整块重写旁路
- busy≠quota 必须保持
- 真实 TCP 源 IP 无法伪造；只伪 XFF
- 双身份并行可能增加风控特征
- 部署 Docker 8080；改完要 healthz sha 对齐 + 回归冒烟
- 产品不做多用户账号池（NewAPI）；这里只是访客 identity 手段
'''

PLAN_PROMPT = CONTEXT + r'''

# 任务：规划

你是资深协议/旁路工程师。请为 maxapi 设计 **identity 双缓冲（A/B standby warm）** 的**最小可行实现方案**。

要求输出（中文，结构化，具体到符号/字段，不要空话）：

## 1. 问题定义
- 当前单槽在时间线上哪里产生可感延迟（区分：退役冷启动 / busy / 上游 TTFB / companion）
- 双缓冲能消除哪一段、不能消除哪一段

## 2. 推荐设计（默认采纳路径）
- 数据结构（每槽字段）
- 状态机：acquire / mark_ok / retire(quota) / swap / warm_standby
- 与现有 identity_*、CookieJar、sticky_* 的关系：复用谁、废弃谁、禁止双写
- cookie 如何 per-identity（或 generation 隔离）且不串
- warm 触发时机（第 1 次 OK 后？acquire 时发现无 standby？）
- 并发：多请求同时 mark_ok / swap 的锁粒度
- 环境变量旋钮与默认值

## 3. 明确不做
- 哪些「看起来更完美」但会踩保护区或风控的扩展

## 4. 最小 diff 落点
- 预计改哪些函数、各改什么（点名 maxapi_server.py 符号）
- 建议补的日志字段（便于验证 swap 是否命中热 B）

## 5. 验收
- 日志期望序列（2 OK 后下一次应看到什么）
- 最小 live 探针步骤（只打 127.0.0.1:8080）
- 回归范围（哪些套件必须绿）

## 6. 风险与回滚
- 风控/串 cookie/卡死 standby 的缓解
- 一键关回单槽的开关

不要写完整大段补丁代码；给到「可直接开干的施工说明」级别。
'''

REVIEW_PROMPT_TMPL = CONTEXT + r'''

# 任务：Adversarial Review

下面是另一位工程师写的双缓冲方案。请你作为 reviewer **挑刺**，默认假设方案有坑。

## 待审方案
```
{plan}
```

## 审查输出（中文）
1. **致命问题**（不修不能合）：竞态、串 cookie、busy/quota 破坏、双写 identity+sticky、请求路径同步 warm 堵 TTFB 等
2. **重要问题**（应修）：锁、generation、失败路径、日志不可观测
3. **可接受/过设计**：标出可砍的部分
4. **修正后的最小方案**（若原案需改，给修订要点，仍保持最小 diff）
5. **Go / Conditional Go / No-Go** 与一句话理由
6. **若 Go：施工顺序**（3–6 步）

态度：严厉、具体、可执行；不要和稀泥。
'''


def chat(messages, effort="high", max_tokens=8192, timeout=300):
    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "stream": False,
        "messages": messages,
        "reasoning_effort": effort,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/v1/messages",
        data=data,
        headers={
            "x-api-key": KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    dt = time.monotonic() - t0
    r = json.loads(raw)
    texts = []
    thinking = []
    for b in r.get("content") or []:
        if b.get("type") == "text":
            texts.append(b.get("text") or "")
        if b.get("type") == "thinking":
            thinking.append(b.get("thinking") or "")
    return {
        "dt": round(dt, 2),
        "stop": r.get("stop_reason"),
        "usage": r.get("usage"),
        "text": "\n".join(texts).strip(),
        "thinking_len": sum(len(x) for x in thinking),
    }


def main():
    out = {"model": MODEL, "plan": None, "review": None}
    print("=== PLAN via", MODEL, "===")
    plan = chat([{"role": "user", "content": PLAN_PROMPT}], effort="high", max_tokens=6000)
    out["plan"] = plan
    print("plan_dt", plan["dt"], "stop", plan["stop"], "chars", len(plan["text"]))
    print(plan["text"][:4000])
    print("...\n")

    print("=== REVIEW via", MODEL, "===")
    rev_prompt = REVIEW_PROMPT_TMPL.format(plan=plan["text"][:12000])
    review = chat([{"role": "user", "content": rev_prompt}], effort="high", max_tokens=6000)
    out["review"] = review
    print("review_dt", review["dt"], "stop", review["stop"], "chars", len(review["text"]))
    print(review["text"][:4000])

    path = "_runtime/v4_evidence/opus5_dual_buffer_plan_review_20260821.json"
    # store full texts
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    # also markdown for reading
    md = path.replace(".json", ".md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(f"# Opus5 dual-buffer plan + review\n\nmodel={MODEL}\n\n")
        f.write(f"## Plan (dt={plan['dt']}s stop={plan['stop']})\n\n{plan['text']}\n\n")
        f.write(f"## Review (dt={review['dt']}s stop={review['stop']})\n\n{review['text']}\n")
    print("WROTE", path, md)


if __name__ == "__main__":
    main()
