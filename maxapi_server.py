#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maxapi - se.zzmax.cn guest-bypass OpenAI-compatible HTTP service.

Guest no-auth + forged X-Forwarded-For (unlimited quota reset) + client-side
long context -> standard OpenAI Chat Completions. 14 chat/vision models routed
through the upstream /api/chat/stream SSE endpoint. Image/video/audio generation
models dropped: they use dedicated /image|/video|/audio/generate endpoints that
return 401 for guests.

Stealth layer (reduce detection risk):
- Full browser headers (Origin/Referer/Sec-Fetch-*/Sec-Ch-Ua/Accept-Language) so
  the upstream request looks like the real se.zzmax web client, not Node/curl.
- Token-bucket rate limit (--rpm) keeps call frequency near human-visitor levels
  instead of machine bursts (high freq is the loudest abuse signal).
- Low-probability companion calls to /api/chat/nav-categories (~8%, async,
  non-blocking) mimic a visitor landing on the site and loading the sidebar.
- No conversationId is sent: verified that the real guest frontend also sends
  none (createConversation 401 -> login wall), so omitting it IS the real guest
  fingerprint; forging a random UUID would be rejected as a non-existent session.

reasoningEffort (low/medium/high/max, omitted for off) is sent by default
(medium) so the upstream streams the <think> ... </think> thinking block via content;
ReasoningFilter peels it into OpenAI reasoning_content deltas so clients render
the live thinking stream and no idle timeout occurs during long thinking. SSE
heartbeat keeps the connection alive; Connection: close ends cleanly.
"""
import http.server, json, ssl, http.client, random, argparse, sys, time, threading

BASE = "se.zzmax.cn"
OPEN_TAG = bytes([0x3c]) + b"think" + bytes([0x3e])
CLOSE_TAG = bytes([0x3c, 0x2f]) + b"think" + bytes([0x3e])

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
SEC_CH_UA = '"' + 'Google Chrome' + '";v="131", "Chromium";v="131", "Not_A Brand";v="24"'
BROWSER_STREAM_HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "Accept": "text/event-stream",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Origin": "https://se.zzmax.cn",
    "Referer": "https://se.zzmax.cn/",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "User-Agent": UA,
    "Sec-Ch-Ua": SEC_CH_UA,
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"' + "Windows" + '"',
}
BROWSER_GET_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Origin": "https://se.zzmax.cn",
    "Referer": "https://se.zzmax.cn/",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "User-Agent": UA,
    "Sec-Ch-Ua": SEC_CH_UA,
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"' + "Windows" + '"',
}

MODEL_TABLE = {
    "deepseek-v4-flash": ("deepseek", "deepseek-v4-flash"),
    "deepseek-v4-pro":   ("deepseek", "deepseek-v4-pro"),
    "claude-opus-4-6":   ("claude",   "claude-opus-4-6"),
    "claude-opus-4-8":   ("claude",   "claude-opus-4-8"),
    "claude-opus-4.8":   ("claude",   "claude-opus-4.8"),
    "gpt-5.6-luna":      ("chatgpt",  "gpt-5.6-luna"),
    "gpt-5.6-terra":     ("chatgpt",  "gpt-5.6-terra"),
    "gpt-5.5":           ("chatgpt",  "gpt-5.5"),
    "gemini-3.5-flash":  ("gemini",   "gemini-3.5-flash"),
    "gemini-3.1-pro-preview": ("gemini", "gemini-3.1-pro-preview"),
    "grok-4.5":          ("grok",    "claude-opus-4-8"),
    "grok-claude-opus-4-8": ("grok", "claude-opus-4-8"),
    "doubao-glm-5.1":    ("doubao",  "glm-5.1"),
    "minimax-glm-5.1":   ("minimax", "glm-5.1"),
    "kimi-k2":           ("kimi",    "kimi-k2"),
    "mimo-qwen3.6-plus": ("mimo",    "qwen3.6-plus"),
    "qwen3.6-plus":      ("qwen",    "qwen3.6-plus"),
    "kimi-k2.5":         ("kimi",   "kimi-k2.5"),
}
DEFAULT_MODEL = "deepseek-v4-flash"
COMPANION_PROB = 0.08
RATE = None

def rand_ip():
    return "%d.%d.%d.%d" % (random.randint(1,250), random.randint(0,251), random.randint(0,251), random.randint(1,250))


class RateLimiter:
    """Token-bucket: max 'rpm' chat requests per minute, smoothed across time."""
    def __init__(self, rpm=12):
        self.capacity = max(1, rpm)
        self.tokens = float(rpm)
        self.rate = max(0.01, rpm) / 60.0
        self.last = time.monotonic()
        self.lock = threading.Lock()
    def acquire(self, timeout=120):
        while timeout > 0:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
                need = 1.0 - self.tokens
                wait = need / self.rate
            step = min(wait, timeout, 2.0)
            time.sleep(step)
            timeout -= step
        return False


def companion_touch():
    """Async GET /api/chat/nav-categories (guest 200) to mimic a visitor landing on the site."""
    try:
        conn = http.client.HTTPSConnection(BASE, timeout=8, context=ssl.create_default_context())
        conn.request("GET", "/api/chat/nav-categories", headers=BROWSER_GET_HEADERS)
        conn.getresponse().read(1024)
        conn.close()
    except Exception:
        pass


class ReasoningFilter:
    """Peel the <think> ... </think> thinking block from upstream content; forward as
    reasoning_content deltas. seek: only wait for more bytes if the buffer is a
    prefix of OPEN_TAG; otherwise answer at once (fixes short answers dropped)."""
    def __init__(self, include_reasoning=False):
        self.include = include_reasoning
        self.buf = b""
        self.mode = None
        self.answer_trimmed = False
    def feed(self, text):
        self.buf += text.encode("utf-8")
        out = []
        while True:
            if self.mode is None:
                if self.buf[:len(OPEN_TAG)] == OPEN_TAG:
                    self.buf = self.buf[len(OPEN_TAG):]
                    self.mode = "think"
                    continue
                if len(self.buf) < len(OPEN_TAG) and OPEN_TAG.startswith(self.buf):
                    return out
                self.mode = "answer"
                continue
            if self.mode == "think":
                idx = self.buf.find(CLOSE_TAG)
                if idx == -1:
                    keep = len(CLOSE_TAG)
                    if len(self.buf) > keep:
                        piece = self.buf[:-keep]
                        self.buf = self.buf[-keep:]
                        if self.include:
                            out.append(("reasoning", piece.decode("utf-8", "ignore")))
                    return out
                piece = self.buf[:idx]
                self.buf = self.buf[idx + len(CLOSE_TAG):]
                if self.include and piece:
                    out.append(("reasoning", piece.decode("utf-8", "ignore")))
                self.mode = "answer"
                continue
            if self.mode == "answer":
                if not self.answer_trimmed:
                    stripped = self.buf.lstrip(b"\r\n\t ")
                    if len(stripped) == len(self.buf) and stripped:
                        self.answer_trimmed = True
                        self.buf = stripped
                        continue
                    if len(stripped) == len(self.buf):
                        return out
                    self.buf = stripped
                    self.answer_trimmed = True
                    continue
                if not self.buf:
                    return out
                piece = self.buf
                self.buf = b""
                out.append(("content", piece.decode("utf-8", "ignore")))
                return out


def upstream(model_field, messages, include_reasoning=False, reasoning_effort="medium", max_retry=5):
    if model_field not in MODEL_TABLE:
        model_field = DEFAULT_MODEL
    grp, sub = MODEL_TABLE[model_field]
    payload = {"model": grp, "subModel": sub, "messages": messages, "stream": True}
    if reasoning_effort and reasoning_effort != "off":
        payload["reasoningEffort"] = reasoning_effort
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    filt = ReasoningFilter(include_reasoning=include_reasoning)
    for attempt in range(1, max_retry + 1):
        xff = rand_ip()
        h = dict(BROWSER_STREAM_HEADERS)
        h["X-Forwarded-For"] = xff
        h["X-Real-IP"] = xff
        conn = http.client.HTTPSConnection(BASE, timeout=180, context=ssl.create_default_context())
        volatile = False
        try:
            conn.request("POST", "/api/chat/stream", body, h)
            resp = conn.getresponse()
            status = resp.status
            if status == 429:
                volatile = True
            elif status not in (200, 201):
                err = resp.read(2048).decode("utf-8", "ignore")[:300]
                yield ("error", {"status": status, "error": err})
                return
            buf = b""
            while True:
                chunk = resp.read1(8192)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    payload_s = line[5:].strip()
                    if not payload_s:
                        continue
                    try:
                        obj = json.loads(payload_s)
                    except Exception:
                        continue
                    if obj.get("error"):
                        err = obj["error"]
                        if any(k in str(err) for k in ("额度", "2次", "登录", "繁忙", "稍后", "频繁", "游客", "套餐")):
                            volatile = True
                            break
                        yield ("error", {"error": err})
                        return
                    ct = obj.get("content")
                    if ct is not None and ct != "":
                        for kind, piece in filt.feed(ct):
                            if kind == "reasoning" and include_reasoning:
                                yield ("reasoning", piece)
                            elif kind == "content":
                                yield ("content", piece)
                    if obj.get("done"):
                        return
            if volatile and attempt < max_retry:
                sys.stderr.write("[volatile %d/%d] retry new IP\n" % (attempt, max_retry))
                time.sleep(1.0 * attempt)
                continue
            if volatile:
                yield ("error", {"error": "upstream model repeatedly busy after %d retries; retry shortly" % max_retry})
            return
        except Exception as e:
            if attempt < max_retry:
                sys.stderr.write("[connerr %d/%d %r] retry\n" % (attempt, max_retry, e))
                time.sleep(1.0 * attempt)
                continue
            yield ("error", {"error": "upstream failed: %r" % e})
            return
        finally:
            try:
                conn.close()
            except Exception:
                pass


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a):
        pass
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "*")
    def _send(self, code, obj, ctype="application/json"):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self._cors()
        self.end_headers()
        self.wfile.write(b)
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()
    def do_GET(self):
        if self.path.startswith("/healthz"):
            return self._send(200, {"status": "ok", "service": "maxapi", "models": len(MODEL_TABLE)})
        if self.path.startswith("/v1/models"):
            data = [{"id": m, "object": "model", "owned_by": "se.zzmax.cn-guest"} for m in MODEL_TABLE]
            return self._send(200, {"object": "list", "data": data})
        return self._send(404, {"error": {"message": "not found"}})
    def do_POST(self):
        if not self.path.startswith("/v1/chat/completions"):
            return self._send(404, {"error": {"message": "not found"}})
        if RATE and not RATE.acquire(timeout=8):
            return self._send(429, {"error": {"message": "rate limit: too many requests, try again shortly"}})
        if COMPANION_PROB and random.random() < COMPANION_PROB:
            threading.Thread(target=companion_touch, daemon=True).start()
        length = int(self.headers.get("Content-Length", "0") or 0)
        try:
            raw = self.rfile.read(length) if length else b"{}"
            req = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return self._send(400, {"error": {"message": "bad json: %s" % e}})
        model = req.get("model") or DEFAULT_MODEL
        messages = req.get("messages") or []
        stream = bool(req.get("stream"))
        include_reasoning = not (req.get("reasoning") is False or req.get("strip_reasoning"))
        effort = req.get("reasoning_effort") or req.get("reasoningEffort") or "medium"
        if str(effort).lower() not in ("off", "low", "medium", "high", "max"):
            effort = "medium"
        turn_id = "chatcmpl-%d" % int(time.time() * 1000)
        created = int(time.time())
        if not stream:
            answer, reason, err = [], [], None
            for kind, data in upstream(model, messages, include_reasoning, str(effort).lower(), max_retry=5):
                if kind == "error":
                    err = data.get("error")
                    break
                if kind == "content":
                    answer.append(data)
                elif kind == "reasoning":
                    reason.append(data)
            if err:
                return self._send(502, {"error": {"message": err}})
            msg = {"role": "assistant", "content": "".join(answer)}
            if include_reasoning:
                msg["reasoning_content"] = "".join(reason)
            return self._send(200, {
                "id": turn_id, "object": "chat.completion", "created": created, "model": model,
                "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": -1, "completion_tokens": -1, "total_tokens": -1}})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.close_connection = True
        self._cors()
        self.end_headers()
        lock = threading.Lock()
        stop = {"v": False}
        def emit(b):
            with lock:
                self.wfile.write(b)
                self.wfile.flush()
        def sse(o):
            emit(("data: " + json.dumps(o, ensure_ascii=False) + "\n\n").encode("utf-8"))
        def heartbeat():
            while not stop["v"]:
                try:
                    emit(b": keepalive\n\n")
                except Exception:
                    return
                for _ in range(10):
                    if stop["v"]:
                        return
                    time.sleep(0.5)
        hb = threading.Thread(target=heartbeat, daemon=True)
        hb.start()
        try:
            sse({"id": turn_id, "object": "chat.completion.chunk", "created": created, "model": model,
                 "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})
            for kind, data in upstream(model, messages, include_reasoning, str(effort).lower(), max_retry=5):
                if kind == "content":
                    sse({"id": turn_id, "object": "chat.completion.chunk", "created": created, "model": model,
                         "choices": [{"index": 0, "delta": {"content": data}, "finish_reason": None}]})
                elif kind == "reasoning":
                    sse({"id": turn_id, "object": "chat.completion.chunk", "created": created, "model": model,
                         "choices": [{"index": 0, "delta": {"reasoning_content": data}, "finish_reason": None}]})
                elif kind == "error":
                    sse({"error": {"message": data.get("error")}})
            sse({"id": turn_id, "object": "chat.completion.chunk", "created": created, "model": model,
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            emit(b"data: [DONE]\n\n")
        except Exception as e:
            try:
                sse({"error": {"message": "server: %s" % e}})
            except Exception:
                pass
        finally:
            stop["v"] = True
            hb.join(1.0)


def main():
    global RATE
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--rpm", type=int, default=12, help="max chat requests per minute (token bucket, 0=unlimited)")
    ap.add_argument("--no-companion", action="store_true", help="disable companion nav-categories calls")
    args = ap.parse_args()
    if args.no_companion:
        global COMPANION_PROB
        COMPANION_PROB = 0.0
    RATE = RateLimiter(args.rpm) if args.rpm > 0 else None
    srv = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    print("maxapi listening on %s:%d  models=%d  rpm=%s  companion=%s" % (
        args.host, args.port, len(MODEL_TABLE), args.rpm, not args.no_companion))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
