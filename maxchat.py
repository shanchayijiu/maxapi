#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
maxchat - 无账号无限次长上下文 CLI 客户端  (se.zzmax.cn)
==========================================================
原理 (均经服务端实测):
  1) /api/chat/stream 访客免鉴权 -> 不需要任何账号/Key
  2) 访客 2 次/天 按"源IP"计; 服务端信任 X-Forwarded-For -> 每请求换随机IP即可无限重置
  3) messages 数组服务端无长度上限 + 真把多轮喂给 LLM -> 客户端自维护历史=长上下文
  4) creditsPerUse=0 的模型访客零成本可调

用法:
  python maxchat.py                       # 交互式 REPL (多轮对话, 自动记住上下文)
  python maxchat.py --once "1+1=?"        # 单次问答
  python maxchat.py -m claude -s claude-opus-4-6 --once "写首诗"
  python maxchat.py -m deepseek -s deepseek-v4-pro --system "你是简洁助手" 
  python maxchat.py --list                # 列出可用模型
  python maxchat.py --max-chars 80000     # 旧上下文超过该字符数自动丢弃最早轮次

依赖: 仅 Python3 标准库 (无 pip 包).
"""
import sys, json, ssl, http.client, random, argparse, textwrap, time

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = "se.zzmax.cn"
DEFAULT_MODEL, DEFAULT_SUB = "deepseek", "deepseek-v4-flash"   # normal/credits0, 实测最稳

# 推荐模型表 (model, subModel, tier, credits) -- 免费首选
RECOMMENDED = [
    ("deepseek", "deepseek-v4-flash", "normal", 0),
    ("deepseek", "deepseek-v4-pro",  "premium", 0),
    ("claude",   "claude-opus-4-6",   "normal", 0),
    ("chatgpt",  "gpt-5.6-luna",      "normal", 0),
    ("chatgpt",  "gpt-5.6-terra",     "normal", 0),
    ("gemini",   "gemini-3.5-flash",  "normal", 0),
    ("gemini",   "gemini-3.1-pro-preview","normal",0),
    ("chatgpt",  "gpt-5.5",           "premium",1),
    ("claude",   "claude-opus-4-8",   "premium",1),
]
FREE = [(m,s) for (m,s,t,c) in RECOMMENDED if c==0]

def rand_ip():
    return f"{random.randint(1,250)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,250)}"

class MaxChat:
    def __init__(self, model=DEFAULT_MODEL, sub=DEFAULT_SUB, system=None, max_chars=60000):
        self.model, self.sub = model, sub
        self.system = system
        self.max_chars = max_chars
        self.history = []   # [{role,content}, ...]  (不含 system)

    def _build_msgs(self, user):
        msgs = []
        if self.system:
            msgs.append({"role": "system", "content": self.system})
        msgs += self.history
        msgs.append({"role": "user", "content": user})
        return msgs

    def _rotate(self):
        """软截断: 历史累积字符超过 max_chars 则丢弃最早一对 user/assistant."""
        if self.max_chars <= 0:
            return
        while self.history and sum(len(m["content"]) for m in self.history) > self.max_chars:
            # 丢最早一轮 (先丢 user, 再丢紧随的 assistant)
            if self.history: self.history.pop(0)
            if self.history and self.history[0]["role"] == "assistant":
                self.history.pop(0)

    def stream(self, user_prompt, max_retry=4):
        """发送一轮, 逐字 yield content; 完成后把回复并入 history. 生成器."""
        self._rotate()
        body = json.dumps({
            "model": self.model, "subModel": self.sub,
            "messages": self._build_msgs(user_prompt), "stream": True,
        }, ensure_ascii=False).encode("utf-8")

        for attempt in range(1, max_retry + 1):
            xff = rand_ip()
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "text/event-stream",
                "User-Agent": "Mozilla/5.0",
                "X-Forwarded-For": xff,
                "X-Real-IP": xff,
            }
            conn = http.client.HTTPSConnection(BASE, timeout=120, context=ssl.create_default_context())
            try:
                conn.request("POST", "/api/chat/stream", body, headers)
                resp = conn.getresponse()
                status = resp.status
                full = []
                saw_done = False
                volatile = False   # 是否为可重试的瞬态(额度/繁忙)
                if status == 429:
                    volatile = True
                while True:
                    chunk = resp.read1(4096)
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", "ignore")
                    for line in text.split("\n"):
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if not payload:
                            continue
                        try:
                            obj = json.loads(payload)
                        except Exception:
                            continue
                        if obj.get("error"):
                            err = obj["error"]
                            sys.stderr.write(f"\n[模型返回错误] {err}\n")
                            if ("额度" in err or "2次" in err or "登录" in err or "繁忙" in err or "稍后" in err):
                                volatile = True
                            full.append("")  # 标记本轮回复失败
                            saw_done = True
                            break
                        if obj.get("content"):
                            c = obj["content"]
                            full.append(c)
                            yield c
                        if obj.get("done"):
                            saw_done = True
                            break
                    if saw_done:
                        break
                reply = "".join(full)
                if volatile and attempt < max_retry:
                    wait = 1.5 * attempt
                    sys.stderr.write(f"[瞬态错误, 换 IP 重试 {attempt+1}/{max_retry}…稍候 {wait:.1f}s]\n")
                    time.sleep(wait)
                    continue
                # 成功: 并入历史
                self.history.append({"role": "user", "content": user_prompt})
                if reply.strip():
                    self.history.append({"role": "assistant", "content": reply})
                return
            except Exception as e:
                if attempt < max_retry:
                    sys.stderr.write(f"[网络异常 {e!r}, 重试 {attempt+1}/{max_retry}]\n")
                    time.sleep(1.5 * attempt)
                    continue
                raise
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

def cmd_list(args):
    print("推荐模型 (model / subModel / tier / creditsPerUse):")
    print("-" * 56)
    for m, s, t, c in RECOMMENDED:
        tag = "  <- 免费(0积分)" if c == 0 else ""
        print(f"  {m:10} / {s:24}  {t:7}  {c}{tag}")
    print("\n免费档(creditsPerUse=0), 访客零成本可调:")
    for m, s in FREE:
        print(f"  -m {m} -s {s}")
    print("\n完整列表可 /api/chat/models 拉取; premium 档访客同样可调(每日额度同样按IP重置).")

def cmd_once(args, client):
    print(">>> " + args.once)
    for piece in client.stream(args.once):
        print(piece, end="", flush=True)
    print()
    if args.history:
        print("\n--- history 简报 ---")
        for i, hh in enumerate(client.history):
            print(f"[{hh['role']}] {hh['content'][:80]}{'…' if len(hh['content'])>80 else ''}")

def cmd_repl(args, client):
    print(f"maxchat  | model={client.model}/{client.sub} | system={'有' if client.system else '无'} "
          f"| max_chars={client.max_chars}")
    print("输入消息开始对话. /help 查命令, /model <m> <s> 切换模型, /sys <prompt> 设系统提示, /reset 清历史, /quit 退出.\n")
    while True:
        try:
            prompt = input("你 › ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见.")
            break
        if not prompt:
            continue
        if prompt.startswith("/"):
            parts = prompt.split()
            cmd = parts[0]
            if cmd in ("/quit", "/exit", "/q"):
                print("再见.")
                break
            elif cmd == "/help":
                print("/model <model> <sub>   切换模型  例: /model deepseek deepseek-v4-flash")
                print("/sys <prompt>           设置系统提示  例: /sys 用一句话回答")
                print("/sysoff                 清除系统提示")
                print("/reset                  清空对话历史")
                print("/hist                   查看当前历史条数与总字符")
                print("/list                   列推荐模型")
                print("/quit                   退出")
            elif cmd == "/model":
                if len(parts) >= 3:
                    client.model, client.sub = parts[1], parts[2]
                    print(f"已切换 -> {client.model}/{client.sub}")
                else:
                    print(f"当前模型 {client.model}/{client.sub}")
            elif cmd in ("/sys", "/system"):
                sp = prompt.split(None, 1)
                client.system = sp[1] if len(sp) > 1 else None
                print("系统提示已设置." if client.system else "系统提示已清除.")
            elif cmd == "/sysoff":
                client.system = None
                print("系统提示已清除.")
            elif cmd in ("/reset", "/clear"):
                client.history = []
                print("历史已清空.")
            elif cmd in ("/hist", "/history"):
                total = sum(len(h["content"]) for h in client.history)
                print(f"历史条数={len(client.history)} 总字符={total} 轮次约={len(client.history)//2}")
            elif cmd == "/list":
                cmd_list(None)
            else:
                print(f"未知命令 {cmd}. /help 查看可用命令.")
            continue
        # 普通对话
        print("AI › ", end="", flush=True)
        for piece in client.stream(prompt):
            print(piece, end="", flush=True)
        print("\n")

def main():
    ap = argparse.ArgumentParser(
        prog="maxchat",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="se.zzmax.cn 无账号无限次长上下文 CLI 客户端",
        epilog=textwrap.dedent("""\
          示例:
            python maxchat.py                           交互式多轮对话
            python maxchat.py --once "你好"
            python maxchat.py -m claude -s claude-opus-4-6 --once "写首诗"
            python maxchat.py --system "你是简洁助手" --max-chars 100000
            python maxchat.py --list
        """))
    ap.add_argument("-m", "--model", default=DEFAULT_MODEL, help="模型组 (默认 deepseek)")
    ap.add_argument("-s", "--sub", default=DEFAULT_SUB, help="子模型 (默认 deepseek-v4-flash)")
    ap.add_argument("--system", default=None, help="系统提示词")
    ap.add_argument("--max-chars", type=int, default=60000, help="历史软截断字符阈值, 0=不截断 (默认60000)")
    ap.add_argument("--once", metavar="PROMPT", default=None, help="单次问答模式, 输出后退出")
    ap.add_argument("--history", action="store_true", help="--once 模式下附带打印历史简报")
    ap.add_argument("--list", action="store_true", help="列出推荐模型后退出")
    args = ap.parse_args()

    if args.list:
        cmd_list(args)
        return

    client = MaxChat(model=args.model, sub=args.sub, system=args.system, max_chars=args.max_chars)
    if args.once:
        cmd_once(args, client)
    else:
        cmd_repl(args, client)

if __name__ == "__main__":
    main()