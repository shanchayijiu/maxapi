#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maxapi - se.zzmax.cn guest-bypass OpenAI-compatible HTTP service.

Guest no-auth + per-attempt forged X-Forwarded-For (2/day free tier reset) + client-side
long context -> standard OpenAI Chat Completions. 17 chat/vision models routed
through the upstream /api/chat/stream SSE endpoint. Image/video/audio generation
models dropped: they use dedicated endpoints that return 401 for guests.

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

Model naming: the external ids returned by /v1/models and accepted in "model"
are the se.zzmax WEB display names ("Claude Sonnet 5", "gpt-5.6-sol",
"Grok-4.5", doubao, ...) so clients match what users see on the site.
resolve_model() also accepts raw actualModelIds and group-qualified aliases
(e.g. "claude/claude-opus-5", "grok-4.5", "doubao-glm-5.1") for backward
compatibility with older calls.

reasoningEffort (off/low/medium/high/max) is forwarded to the upstream so the
live thinking block streams back; ReasoningFilter peels it into OpenAI
reasoning_content deltas so clients render the live thinking and no idle timeout
occurs during long thinking. SSE heartbeat keeps the connection alive.

search / web_search: when the client sets search=true (or web_search=true) the
request is forwarded with upstream field "search": true, enabling the built-in
web-retrieval / online mode for guests. Upstream may then return extra "sources"
and "status" fields; sources are forwarded as a top-level "sources" array on the
final non-stream message and as a final SSE chunk on streams so clients that
render citations can use them.
"""
import http.server, json, ssl, http.client, socket, random, string, argparse, sys, time, threading, re, logging, os, copy, hashlib, uuid as _uuid

BASE = "se.zzmax.cn"
OPEN_TAG = bytes([0x3c]) + b"think" + bytes([0x3e])
CLOSE_TAG = bytes([0x3c, 0x2f]) + b"think" + bytes([0x3e])

# ── Logging ──────────────────────────────────────────────────────────────────
LOG = logging.getLogger("maxapi")
_COMPACT_ENABLED = os.getenv("MAXAPI_COMPACT", "1") != "0"
_KEEP_TAIL_SEGMENTS = int(os.getenv("MAXAPI_KEEP_TAIL_SEGMENTS", "6"))
_TOOL_RESULT_CAP = int(os.getenv("MAXAPI_TOOL_RESULT_CAP", "4000"))  # token-based threshold per tool_result
_MAX_BODY_BYTES = 10 * 1024 * 1024  # 10 MB max request body
# Preflight compact: trigger BEFORE hard limit so estimator error + upstream hidden
# tokens never ride the 100%~125% "warn only" band (user-confirmed agent stall zone).
# Default 0.88 leaves ~12% headroom; target tiers leave ~18%~28% after compact.
try:
    _COMPACT_TRIGGER = float(os.getenv("MAXAPI_COMPACT_TRIGGER", "0.88"))
except (TypeError, ValueError):
    _COMPACT_TRIGGER = 0.88
_COMPACT_TRIGGER = max(0.50, min(0.99, _COMPACT_TRIGGER))
try:
    _EST_COMPACT_INFLATE = float(os.getenv("MAXAPI_EST_COMPACT_INFLATE", "1.20"))
except (TypeError, ValueError):
    _EST_COMPACT_INFLATE = 1.20
_EST_COMPACT_INFLATE = max(1.0, min(2.0, _EST_COMPACT_INFLATE))


def _est_for_preflight(est_tokens, model=None):
    """Inflate token estimate for compact decisions.

    Live evidence: raw estimator can under-count ~20-30% vs upstream input_tokens
    (esp. CJK-heavy agent histories). EWMA ratio is actual/estimated when sampled;
    with no samples, apply _EST_COMPACT_INFLATE. Never use a scale < 1.0 here —
    under-triggering compact is worse than compacting slightly early.
    """
    est = int(est_tokens or 0)
    if est <= 0:
        return 0
    scale = _EST_COMPACT_INFLATE
    if model:
        with _EWMA_LOCK:
            s = _ewma_store.get(model)
            if s and s.get("n", 0) > 0:
                # actual/estimated; only honor values that indicate under-count
                scale = max(float(s.get("ratio") or 1.0), 1.0)
    return int(est * scale)


def _preflight_should_compact(est_tokens, limit_tokens, model=None):
    """True when estimated input should be compacted before first upstream call."""
    if not _COMPACT_ENABLED or limit_tokens <= 0:
        return False
    eff = _est_for_preflight(est_tokens, model)
    return eff > int(limit_tokens * _COMPACT_TRIGGER)


def _preflight_compact_budget(est_tokens, limit_tokens, model=None):
    """Target budget for compact_request (uses RAW estimator units).

    Tier by effective (inflated) ratio vs limit, then convert target back to raw
    estimator units so compact actually shrinks when under-count hid over-limit.
    Always force at least ~15% raw shrink when we decided to compact.
    """
    lim = max(1, int(limit_tokens))
    raw = max(0, int(est_tokens or 0))
    eff = _est_for_preflight(raw, model)
    ratio = float(eff) / float(lim)
    if ratio > 1.25:
        frac = 0.72
    elif ratio > 1.05:
        frac = 0.78
    else:
        frac = 0.82
    scale = float(eff) / float(raw) if raw > 0 else _EST_COMPACT_INFLATE
    if scale < 1.0:
        scale = 1.0
    # Want inflated(raw_after) ~= limit*frac => raw_after ~= limit*frac/scale
    budget = int(lim * frac / scale)
    if raw > 0:
        budget = min(budget, int(raw * 0.85))
    budget = min(budget, lim - 1)
    if lim > 10000:
        budget = max(8000, budget)
    return max(1, budget)

def _setup_logging():
    lvl = os.getenv("MAXAPI_LOG_LEVEL", "INFO").upper()
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S"))
    LOG.handlers[:] = [h]
    LOG.setLevel(getattr(logging, lvl, logging.INFO))
    LOG.propagate = False

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

# Canonical model list in se.zzmax web display-name order.
# Each tuple: (display_id, group, actualModelId, tier)
# display_id is what /v1/models returns and what clients send in "model".
RAW_MODELS = [
    ("Claude Sonnet 5",        "claude",   "claude-sonnet-5",       "premium"),
    ("Claude Opus 5",          "claude",   "claude-opus-5",         "premium"),
    ("claude-opus-4-6",        "claude",   "claude-opus-4-6",       "normal"),
    ("gpt-5.6-sol",            "chatgpt",  "gpt-5.6-sol",           "normal"),
    ("gpt-5.6-luna",           "chatgpt",  "gpt-5.6-luna",          "normal"),
    ("deepseek-v4-pro",        "deepseek", "deepseek-v4-pro",       "premium"),
    ("deepseek-v4-flash",      "deepseek", "deepseek-v4-flash",     "normal"),
    ("qwen3.6-plus",           "qwen",     "qwen3.6-plus",         "premium"),
    ("MiMo-V2.5-Pro",          "mimo",     "qwen3.6-plus",          "premium"),
    ("gemini-3.5-flash",       "gemini",   "gemini-3.5-flash",      "normal"),
    ("gemini-3.1-pro-preview", "gemini",   "gemini-3.1-pro-preview","normal"),
]

# Ordered list of external display ids (for /v1/models).
MODEL_DISPLAY_IDS = [m[0] for m in RAW_MODELS]

# display_id -> (group, actual)
MODEL_BY_DISPLAY = {m[0]: (m[1], m[2]) for m in RAW_MODELS}

# Backward-compatible aliases accepted in "model": raw actualModelIds and
# group-qualified forms. Ambiguous actuals (shared by over 1 group) point to
# their canonical display so behavior stays well-defined.
MODEL_ALIASES = {
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude/claude-sonnet-5": "Claude Sonnet 5",
    "claude/claude-opus-4-8": "Claude Opus 5",
    "qwen/qwen3.6-plus": "qwen3.6-plus",
    "mimo/qwen3.6-plus": "MiMo-V2.5-Pro",
    "mimo-qwen3.6-plus": "MiMo-V2.5-Pro",
    "chatgpt/gpt-5.6-sol": "gpt-5.6-sol",
    "chatgpt/gpt-5.6-luna": "gpt-5.6-luna",
    "chatgpt/gpt-5.6-terra": "gpt-5.6-sol",
    "chatgpt/gpt-5.5": "gpt-5.6-sol",
    # Opus 4.8 retired: upstream had no provider for claude-opus-4.8 (0/8 OK on
    # 2026-08-12 while opus-5 / sonnet-5 / opus-4-6 were all 8/8). Clients that
    # still ask for it — Claude Code sends "claude-opus-4-8" natively — are
    # routed to Opus 5 rather than 404'd. The old display name is aliased too,
    # otherwise resolve_model() falls through to DEFAULT_MODEL and a request for
    # an Opus-class model silently lands on deepseek-v4-flash.
    "claude/claude-opus-4.8": "Claude Opus 5",
    "Claude Opus 4.8": "Claude Opus 5",
    "claude/claude-opus-4-6": "claude-opus-4-6",
    "deepseek/deepseek-v4-pro": "deepseek-v4-pro",
    "deepseek/deepseek-v4-flash": "deepseek-v4-flash",
    "gemini/gemini-3.5-flash": "gemini-3.5-flash",
    "gemini/gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
    # plain (unambiguous) actuals
    # GPT terra / 5.5 retired (upstream had no provider). Keep aliases on sol.
    "gpt-5.6-terra": "gpt-5.6-sol",
    "gpt-5.5": "gpt-5.6-sol",
    "GPT-5.5": "gpt-5.6-sol",
    "claude-opus-4.8": "Claude Opus 5",
    "claude-opus-4-6": "claude-opus-4-6",
    "claude-opus-5": "Claude Opus 5",
    "deepseek-v4-pro": "deepseek-v4-pro",
    "deepseek-v4-flash": "deepseek-v4-flash",
    "gemini-3.5-flash": "gemini-3.5-flash",
    "gemini-3.1-pro-preview": "gemini-3.1-pro-preview",
    # ambiguous plain actuals -> canonical display (preferred group)
    "claude-opus-4-8": "Claude Opus 5",
    "qwen3.6-plus": "qwen3.6-plus",
}

DEFAULT_MODEL = "deepseek-v4-flash"
COMPANION_PROB = 0.08
RATE = None

# Per-model capability metadata returned by /v1/models. context_length is the
# advertised input window; max_output_tokens the advertised completion cap;
# supports_tool_use marks DSML-routed tool endpoints. Values are advisory so
# clients (Claude Code / Codex) display context length instead of blank.
# group -> (context_length, max_output_tokens, supports_tool_use)
_GROUP_META = {
    "claude":   (200000, 8192,  True),
    "chatgpt":  (400000, 16384, True),
    "deepseek": (128000, 8192,  True),
    "qwen":     (131072, 8192,  True),
    "mimo":     (131072, 8192,  True),
    "gemini":   (1000000, 8192, True),
}
MODEL_META = {m[0]: _GROUP_META.get(m[1], (200000, 8192, True)) for m in RAW_MODELS}

# display_id -> Anthropic standard model ID (used in /v1/messages responses so
# Claude Code can look up context_length from its internal model registry).
_ANTHROPIC_MODEL_IDS = {
    "Claude Sonnet 5":        "claude-sonnet-4-20250514",
    "Claude Opus 5":          "claude-opus-5",
    "claude-opus-4-6":        "claude-opus-4-20250514",
    "gpt-5.6-sol":            "claude-sonnet-4-20250514",
    "gpt-5.6-luna":           "claude-sonnet-4-20250514",
    "deepseek-v4-pro":        "claude-sonnet-4-20250514",
    "deepseek-v4-flash":      "claude-sonnet-4-20250514",
    "qwen3.6-plus":           "claude-sonnet-4-20250514",
    "MiMo-V2.5-Pro":          "claude-sonnet-4-20250514",
    "gemini-3.5-flash":       "claude-sonnet-4-20250514",
    "gemini-3.1-pro-preview": "claude-sonnet-4-20250514",
}

def _anthropic_model_id(display_id):
    """Map display_id to a standard Anthropic model ID for CC compatibility."""
    return _ANTHROPIC_MODEL_IDS.get(display_id, "claude-sonnet-4-20250514")

# Per-display max input tokens. Dynamic: reserves space for requested output + safety margin.
SAFETY_MARGIN = 512  # blocks/tools/metadata the estimator can't see

def _context_limit(display_id, max_tokens=None):
    """Input-token budget for preflight/compaction.

    Clients (esp. Claude Code) often send huge max_tokens (e.g. 64000) that the
    upstream model cannot actually emit. Reserving the raw value collapses the
    input window (200k-64k-512=135k) and triggers catastrophic compaction.
    Cap the reserve at the model's advertised max_output_tokens.
    """
    ctx, model_out, _tu = MODEL_META.get(display_id, (200000, 8192, True))
    try:
        req_out = int(max_tokens) if max_tokens else min(4096, model_out)
    except (TypeError, ValueError):
        req_out = min(4096, model_out)
    reserve = max(0, min(req_out, model_out))
    return max(ctx - reserve - SAFETY_MARGIN, 8192)


def _clamp_max_tokens(display_id, max_tokens):
    """Clamp client max_tokens to the model's advertised output cap."""
    _ctx, model_out, _tu = MODEL_META.get(display_id, (200000, 8192, True))
    try:
        mt = int(max_tokens) if max_tokens not in (None, "") else model_out
    except (TypeError, ValueError):
        mt = model_out
    if mt <= 0:
        mt = model_out
    return max(1, min(mt, model_out))

def resolve_model(name):
    """Map a client-supplied model id to (group, actualModelId, display_id)."""
    if not name:
        name = DEFAULT_MODEL
    if name in MODEL_BY_DISPLAY:
        g, a = MODEL_BY_DISPLAY[name]
        return g, a, name
    alt = MODEL_ALIASES.get(name)
    if alt and alt in MODEL_BY_DISPLAY:
        g, a = MODEL_BY_DISPLAY[alt]
        return g, a, alt
    g, a = MODEL_BY_DISPLAY[DEFAULT_MODEL]
    return g, a, DEFAULT_MODEL

def rand_ip():
    return "%d.%d.%d.%d" % (random.randint(1,250), random.randint(0,251), random.randint(0,251), random.randint(1,250))


# Free tier ~2 successful calls / network-identity / day.
# After 2 OK uses, retire so the next call never waits for a quota error.
_IDENTITY_MAX_OK = int(os.getenv("MAXAPI_IDENTITY_MAX_OK", "2"))
_identity_lock = threading.Lock()
_identity_ip = None
_identity_ok = 0


def identity_acquire(force_new=False):
    global _identity_ip, _identity_ok
    with _identity_lock:
        if force_new or _identity_ip is None or _identity_ok >= _IDENTITY_MAX_OK:
            _identity_ip = rand_ip()
            _identity_ok = 0
        return _identity_ip


def identity_mark_ok(ip):
    global _identity_ip, _identity_ok
    if not ip:
        return
    with _identity_lock:
        if ip != _identity_ip:
            return
        _identity_ok += 1
        if _identity_ok >= _IDENTITY_MAX_OK:
            LOG.info("identity retire after %d ok uses", _identity_ok)
            _identity_ip = None
            _identity_ok = 0


def identity_retire(ip, reason=""):
    global _identity_ip, _identity_ok
    with _identity_lock:
        if ip is None or ip == _identity_ip:
            LOG.info("identity retire (%s)", reason or "force")
            _identity_ip = None
            _identity_ok = 0


class RateLimiter:
    """Token-bucket: max 'rpm' chat requests per minute, smoothed across time."""
    def __init__(self, rpm=60):
        self.capacity = max(1, rpm)
        self.tokens = float(rpm)
        self.rate = max(0.01, rpm) / 60.0
        self.last = time.monotonic()
        self.lock = threading.Lock()
    def acquire(self, timeout=0):
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
            self.last = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
        if timeout <= 0:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.1)
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
        return False


class CookieJar:
    """Global cookie jar: capture Set-Cookie from companion/upstream responses.

    generation bumps when sticky identity rotates; stale responses from an old
    generation must not write cookies into the new identity.
    """
    def __init__(self):
        self.jar = {}
        self.lock = threading.Lock()
        self.last_update = 0.0  # monotonic
        self.generation = 0

    def update_from_response(self, resp, generation=None):
        try:
            changed = False
            with self.lock:
                if generation is not None and generation != self.generation:
                    return False  # stale identity
                for hdr in resp.getheaders():
                    if hdr[0].lower() == "set-cookie":
                        part = hdr[1].split(";")[0].strip()
                        if "=" in part:
                            k, v = part.split("=", 1)
                            k, v = k.strip(), v.strip()
                            if k and self.jar.get(k) != v:
                                self.jar[k] = v
                                changed = True
                if changed:
                    self.last_update = time.monotonic()
            return changed
        except Exception:
            return False

    def get_header(self):
        with self.lock:
            if not self.jar:
                return None
            return "; ".join(k + "=" + v for k, v in self.jar.items())

    def snapshot(self):
        """Atomic (generation, cookie_header) under jar lock."""
        with self.lock:
            if not self.jar:
                return self.generation, None
            return self.generation, "; ".join(k + "=" + v for k, v in self.jar.items())

    def size(self):
        with self.lock:
            return len(self.jar)

    def snapshot_generation(self):
        with self.lock:
            return self.generation

    def clear(self, bump=True):
        with self.lock:
            self.jar.clear()
            self.last_update = 0.0
            if bump:
                self.generation += 1
            return self.generation


COOKIE_JAR = CookieJar()

# Sticky egress identity: real browsers keep one public IP per session. Rotating
# XFF every request looks like a botnet and breaks session affinity upstream.
# IP + cookie generation share _session_lock so snapshots are atomic.
_STICKY_IP_TTL = float(os.getenv("MAXAPI_STICKY_IP_TTL", "600"))  # seconds
_sticky_ip = None
_sticky_ip_until = 0.0
_session_lock = threading.Lock()
_warm_lock = threading.Lock()
_warm_inflight = False


def sticky_ip():
    """Return current sticky XFF (may rotate on TTL). Prefer session_snapshot()."""
    snap = session_snapshot(kick_warm=False)
    return snap[0]


def session_snapshot(kick_warm=True):
    """Atomic (ip, generation, cookie_header) for one upstream attempt.

    Holds _session_lock across IP selection and CookieJar.snapshot() so rotate
    cannot interleave. CookieJar.snapshot() itself is one jar-lock critical section.
    """
    global _sticky_ip, _sticky_ip_until
    now = time.monotonic()
    with _session_lock:
        if not _sticky_ip or now >= _sticky_ip_until:
            _sticky_ip = rand_ip()
            _sticky_ip_until = now + max(60.0, _STICKY_IP_TTL)
        ip = _sticky_ip
        gen, cookie = COOKIE_JAR.snapshot()
    if kick_warm and not cookie:
        _kick_warm_singleflight()
    return ip, gen, cookie


def rotate_sticky_ip(reason=""):
    """Atomically rotate XFF and drop cookies (quota / guest / ban)."""
    global _sticky_ip, _sticky_ip_until
    with _session_lock:
        prev = _sticky_ip
        _sticky_ip = rand_ip()
        _sticky_ip_until = time.monotonic() + max(60.0, _STICKY_IP_TTL)
        gen = COOKIE_JAR.clear(bump=True)
        new_ip = _sticky_ip
    LOG.info("sticky identity rotated (%s): %s -> %s gen=%d", reason or "manual", prev, new_ip, gen)
    _kick_warm_singleflight()
    return new_ip


def _kick_warm_singleflight():
    """At most one background companion warm at a time."""
    global _warm_inflight
    with _warm_lock:
        if _warm_inflight:
            return
        _warm_inflight = True

    def _run():
        global _warm_inflight
        try:
            background_companion_refresh()
        finally:
            with _warm_lock:
                _warm_inflight = False

    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        with _warm_lock:
            _warm_inflight = False


_COMPANION_PATHS = (
    "/",  # landing page often sets session cookies
    "/api/chat/nav-categories",
    "/favicon.ico",
)
_companion_lock = threading.Lock()
_companion_last_try = 0.0
_COMPANION_MIN_INTERVAL = 15.0  # avoid stampedes when jar empty under concurrency


def companion_touch(force_all=False):
    """Warm browser-like session: hit landing + nav, capture Set-Cookie.

    force_all=True walks all paths (startup / empty-jar ensure). Otherwise a
    light touch (landing + maybe favicon) for background refresh.
    """
    paths = list(_COMPANION_PATHS) if force_all else ["/", "/api/chat/nav-categories"]
    if not force_all and random.random() < 0.3:
        paths.append("/favicon.ico")
    got = 0
    for path in paths:
        conn = None
        try:
            gen_at_start = COOKIE_JAR.snapshot_generation()
            conn = http.client.HTTPSConnection(BASE, timeout=8, context=ssl.create_default_context())
            h = dict(BROWSER_GET_HEADERS)
            if path != "/":
                h["Referer"] = "https://%s/" % BASE
            else:
                h["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                h["Sec-Fetch-Dest"] = "document"
                h["Sec-Fetch-Mode"] = "navigate"
            conn.request("GET", path, headers=h)
            resp = conn.getresponse()
            # Serialize cookie writes with rotate/snapshot; drop if identity rotated mid-warm.
            with _session_lock:
                if COOKIE_JAR.update_from_response(resp, generation=gen_at_start):
                    got += 1
            try:
                resp.read(65536)
            except Exception:
                pass
        except Exception as e:
            LOG.debug("companion %s failed: %r", path, e)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        if not force_all:
            time.sleep(random.uniform(0.05, 0.2))
    if got:
        LOG.info("companion warm cookies=%d header=%s", COOKIE_JAR.size(),
                 (COOKIE_JAR.get_header() or "")[:80])
    return COOKIE_JAR.size()


def ensure_cookies(force=False):
    """Return Cookie header without blocking the request hot path.

    Prefer session_snapshot() on the request path. force=True is for bg/startup.
    """
    h = COOKIE_JAR.get_header()
    if h:
        return h
    if force:
        global _companion_last_try
        with _companion_lock:
            h = COOKIE_JAR.get_header()
            if h:
                return h
            now = time.monotonic()
            if now - _companion_last_try < _COMPANION_MIN_INTERVAL:
                return COOKIE_JAR.get_header()
            _companion_last_try = now
            try:
                companion_touch(force_all=True)
            except Exception as e:
                LOG.warning("ensure_cookies warm failed: %r", e)
        return COOKIE_JAR.get_header()
    _kick_warm_singleflight()
    return None


def background_companion_refresh():
    """Async non-blocking refresh (startup / entry / empty-jar kick)."""
    try:
        if COOKIE_JAR.size() == 0:
            ensure_cookies(force=True)
        else:
            companion_touch(force_all=False)
    except Exception:
        pass



class ReasoningFilter:
    """Peel the upstream <think>...</think> block from content; forward as
    reasoning_content deltas. Operates on strings (not bytes) to avoid splitting
    multi-byte UTF-8 characters at chunk boundaries."""
    _OPENS = ["<think>", "<thinking>"]
    _CLOSES = ["</think>", "</thinking>"]
    _MAX_OPEN = 10  # len("<thinking>")
    _MAX_CLOSE = 12  # len("</thinking>")
    def __init__(self, include_reasoning=False):
        self.include = include_reasoning
        self.buf = ""
        self.mode = None
        self.answer_trimmed = False
    def _match_open(self):
        low = self.buf.lower()
        for tag in self._OPENS:
            if low[:len(tag)] == tag:
                return len(tag)
        return 0
    def _partial_open(self):
        low = self.buf.lower()
        for tag in self._OPENS:
            if len(low) < len(tag) and tag.startswith(low):
                return True
        return False
    def _find_close(self):
        low = self.buf.lower()
        for tag in self._CLOSES:
            idx = low.find(tag)
            if idx >= 0:
                return idx, len(tag)
        return -1, 0
    def feed(self, text):
        self.buf += text
        out = []
        while True:
            if self.mode is None:
                # Strip leading whitespace so a <thinking> tag after a leading
                # newline is not missed. If the buffer is pure whitespace, hold.
                if self.buf and self.buf[0] in "\r\n\t ":
                    self.buf = self.buf.lstrip("\r\n\t ")
                    if not self.buf:
                        return out
                n = self._match_open()
                if n:
                    self.buf = self.buf[n:]
                    self.mode = "think"
                    continue
                if self._partial_open():
                    return out
                self.mode = "answer"
                continue
            if self.mode == "think":
                idx, clen = self._find_close()
                if idx == -1:
                    keep = self._MAX_CLOSE
                    if len(self.buf) > keep:
                        piece = self.buf[:-keep]
                        self.buf = self.buf[-keep:]
                        if self.include:
                            out.append(("reasoning", piece))
                    return out
                piece = self.buf[:idx]
                self.buf = self.buf[idx + clen:]
                if self.include and piece:
                    out.append(("reasoning", piece))
                self.mode = "answer"
                continue
            if self.mode == "answer":
                if not self.answer_trimmed:
                    stripped = self.buf.lstrip("\r\n\t ")
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
                self.buf = ""
                out.append(("content", piece))
                return out
    def flush(self):
        out = []
        if self.buf:
            if self.mode == "think" and self.include:
                out.append(("reasoning", self.buf))
            elif self.mode == "answer":
                if not self.answer_trimmed:
                    self.buf = self.buf.lstrip("\r\n\t ")
                if self.buf:
                    out.append(("content", self.buf))
            self.buf = ""
        return out


def _make_tool_id():
    chars = string.ascii_letters + string.digits
    return "toolu_01" + "".join(random.choices(chars, k=22))

def _make_msg_id():
    chars = string.ascii_letters + string.digits
    return "msg_01" + "".join(random.choices(chars, k=24))


# ---- DSML toolcall (ported from ds2api, replaces bytes-tag parser) ----

_DSML = chr(0x7c) + "DSML" + chr(0x7c)
# Match both |DSML| and |DSTML| variants (upstream occasionally emits DSTML with an extra T).
_RE_DSML_STRIP = re.compile(r'(</?)\|?dst?ml[\s|]*', re.IGNORECASE)
_RE_CDATA = re.compile(r'^<!\[CDATA\[(.*?)\]\]>$', re.DOTALL | re.IGNORECASE)
_RE_INVOKE = re.compile(r'<invoke\b[^>]*\bname\s*=\s*"([^"]*)"[^>]*>(.*?)</invoke>', re.DOTALL | re.IGNORECASE)
_RE_INVOKE_SQ = re.compile(r"<invoke\b[^>]*\bname\s*=\s*'([^']*)'[^>]*>(.*?)</invoke>", re.DOTALL | re.IGNORECASE)
_RE_INVOKE_ANY = re.compile(r'<invoke\b[^>]*>(.*?)</invoke>', re.DOTALL | re.IGNORECASE)
_RE_PARAM = re.compile(r'<parameter\b[^>]*\bname\s*=\s*"([^"]*)"[^>]*>(.*?)</parameter>', re.DOTALL | re.IGNORECASE)
_RE_PARAM_SQ = re.compile(r"<parameter\b[^>]*\bname\s*=\s*'([^']*)'[^>]*>(.*?)</parameter>", re.DOTALL | re.IGNORECASE)
_RE_PARAM_ATTR = re.compile(r'<parameter\b[^>]*\bname\s*=\s*["\x27]([^"\x27]*)["\x27]\s+[^>]*?(?:string_value|string_value|value|val|string)\s*=\s*["\x27]([^"\x27]*)["\x27]', re.IGNORECASE)
_RE_ITEM = re.compile(r'<item\b[^>]*>(.*?)</item>', re.DOTALL | re.IGNORECASE)
_RE_CHILD = re.compile(r'<([a-zA-Z_][a-zA-Z0-9_\-]*)\b[^>]*>(.*?)</\1>', re.DOTALL | re.IGNORECASE)
_RE_LEGACY = re.compile(r'<(?:tool_call|call|tool_use|function_call|tool)\b[^>]*>(.*?)</(?:tool_call|call|tool_use|function_call|tool)>', re.DOTALL | re.IGNORECASE)

# se.zzmax 上游注入格式: <function=NAME>...<parameter=KEY>VAL</parameter>...</function>
_RE_FN_INVOKE = re.compile(r'<function\s*=\s*"?([A-Za-z_][\w:-]*)"?\b[^>]*>(.*?)</function\s*>', re.DOTALL | re.IGNORECASE)
_RE_FN_PARAM = re.compile(r'<parameter\s*=\s*"?([A-Za-z_][\w:-]*)"?\b[^>]*>(.*?)</parameter\s*>', re.DOTALL | re.IGNORECASE)

_STRING_PRESERVE = frozenset([
    "content", "file_content", "text", "prompt", "query",
    "command", "cmd", "script", "code",
    "old_string", "new_string", "pattern", "path", "file_path",
])
_TOOL_TAG_PREFIXES = [
    "<tool_calls", "<invoke", "<parameter",
    # DSML variant (|DSML| prefix)
    "<|dsml|tool_calls", "<|dsml|invoke", "<|dsml|parameter",
    # DSTML variant (upstream occasionally emits an extra T)
    "<|dstml|tool_calls", "<|dstml|invoke", "<|dstml|parameter",
    "<|tool_calls", "<|invoke", "<|parameter",
    "<|tool_calls", "<|invoke", "<|parameter",
    "<dstml|tool_calls", "<dstml|invoke", "<dstml|parameter",
    "<function",  # se.zzmax <function=NAME> AND Claude-native <function_calls>
]

# Full opening tags (with > or space) for segment detection.
# These only match when the tag has a body separator (> or whitespace), not mid-build.
_TOOL_TAG_FULLS = [
    "<function_calls>", "<function_calls ", "<function_calls\t", "<function_calls\n", "<function_calls\r",
    "<tool_calls>", "<tool_calls ", "<tool_calls\t", "<tool_calls\n", "<tool_calls\r",
    "<invoke>", "<invoke ", "<invoke\t", "<invoke\n", "<invoke\r",
    "<parameter>", "<parameter ", "<parameter\t", "<parameter\n", "<parameter\r",
    # DSML variant (|DSML| prefix)
    "<|dsml|tool_calls>", "<|dsml|tool_calls ", "<|dsml|tool_calls\t", "<|dsml|tool_calls\n", "<|dsml|tool_calls\r",
    "<|dsml|invoke>", "<|dsml|invoke ", "<|dsml|invoke\t", "<|dsml|invoke\n", "<|dsml|invoke\r",
    "<|dsml|parameter>", "<|dsml|parameter ", "<|dsml|parameter\t", "<|dsml|parameter\n", "<|dsml|parameter\r",
    # DSTML variant (upstream occasionally emits an extra T)
    "<|dstml|tool_calls>", "<|dstml|tool_calls ", "<|dstml|tool_calls\t", "<|dstml|tool_calls\n", "<|dstml|tool_calls\r",
    "<|dstml|invoke>", "<|dstml|invoke ", "<|dstml|invoke\t", "<|dstml|invoke\n", "<|dstml|invoke\r",
    "<|dstml|parameter>", "<|dstml|parameter ", "<|dstml|parameter\t", "<|dstml|parameter\n", "<|dstml|parameter\r",
    # bare |tool_calls| variants without dsml prefix
    "<|tool_calls>", "<|tool_calls ", "<|tool_calls\t", "<|tool_calls\n", "<|tool_calls\r",
    "<|invoke>", "<|invoke ", "<|invoke\t", "<|invoke\n", "<|invoke\r",
    "<|parameter>", "<|parameter ", "<|parameter\t", "<|parameter\n", "<|parameter\r",
    # dsml| without leading pipe
    "<|tool_calls>", "<|tool_calls ", "<|tool_calls\t", "<|tool_calls\n", "<|tool_calls\r",
    "<|invoke>", "<|invoke ", "<|invoke\t", "<|invoke\n", "<|invoke\r",
    "<|parameter>", "<|parameter ", "<|parameter\t", "<|parameter\n", "<|parameter\r",
    # dstml| without leading pipe
    "<dstml|tool_calls>", "<dstml|tool_calls ", "<dstml|tool_calls\t", "<dstml|tool_calls\n", "<dstml|tool_calls\r",
    "<dstml|invoke>", "<dstml|invoke ", "<dstml|invoke\t", "<dstml|invoke\n", "<dstml|invoke\r",
    "<dstml|parameter>", "<dstml|parameter ", "<dstml|parameter\t", "<dstml|parameter\n", "<dstml|parameter\r",
]


# Upstream (claude-opus-5 via se.zzmax) sometimes emits a literal HTML <br>
# on its own line as a paragraph separator. Terminal clients (Claude Code)
# don't render HTML, so it leaks as visible text. Strip only the standalone
# form -- a <br> alone on a line -- and leave inline / fenced ones alone so
# real HTML in code blocks survives.
_RE_BR_LINE = re.compile(r'(?:\r\n|\r|\n)[ \t]*<br\s*/?>[ \t]*(?=\r\n|\r|\n|\Z)', re.IGNORECASE)
_RE_BR_LEAD = re.compile(r'\A[ \t]*<br\s*/?>[ \t]*(?:\r\n|\r|\n)', re.IGNORECASE)
# Upstream models sometimes leak the EOS token </s> as literal text.
_RE_EOS_TOKEN = re.compile(r'</s>', re.IGNORECASE)


def _strip_stray_br(text):
    """Remove standalone <br> lines and stray EOS </s> tokens emitted as
    literal text by upstream. Leaves inline <br> and fenced code untouched."""
    if not text:
        return text
    # Strip </s> EOS token regardless of context (never legitimate in API output)
    if "</s" in text.lower():
        text = _RE_EOS_TOKEN.sub("", text)
    if "<br" not in text.lower():
        return text
    # Protect fenced regions: only rewrite spans that are outside a fence.
    parts = []
    pos = 0
    for m in re.finditer(r'(?:^|\n)[ \t]{0,3}(`{3,}|~{3,})[^\n]*\n.*?(?:\n[ \t]{0,3}\1[ \t]*(?=\n|\Z)|\Z)',
                         text, re.DOTALL):
        outside = text[pos:m.start()]
        parts.append(_RE_BR_LINE.sub("", _RE_BR_LEAD.sub("", outside)))
        parts.append(m.group(0))          # fenced block: verbatim
        pos = m.end()
    tail = text[pos:]
    parts.append(_RE_BR_LINE.sub("", _RE_BR_LEAD.sub("", tail)))
    return "".join(parts)


def _dsml_wrap_cdata(text):
    if not text:
        return ""
    if "]]>" in text:
        text = text.replace("]]>", "]]]]><![CDATA[>")
    return "<![CDATA[" + text + "]]>"


def _dsml_extract_cdata(text):
    text = text.strip()
    m = _RE_CDATA.match(text)
    if m:
        return m.group(1)
    low = text.lower()
    if low.startswith("<![cdata["):
        if text.rstrip().endswith("]]>"):
            return text[9:-3]
        return text[9:]
    return None


def _try_json(raw):
    s = raw.strip()
    if not s or s[0] not in '{["-0123456789tfn':
        return None, False
    try:
        return json.loads(s), True
    except Exception:
        return None, False


def _repair_backslash(s):
    if "\\" not in s:
        return s
    out = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt in '"\\/bfnrt':
                out.append("\\" + nxt)
                i += 2
                continue
            elif nxt == "u" and i + 5 < n:
                ok = all(c in "0123456789abcdefABCDEF" for c in s[i + 2:i + 6])
                if ok:
                    out.append(s[i:i + 6])
                    i += 6
                    continue
            out.append("\\\\")
            i += 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _repair_loose_json(s):
    s = s.strip()
    if not s:
        return s
    return re.sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', s)


def _html_unescape(s):
    _a = '&'
    s = s.replace(_a + 'lt;', '<')
    s = s.replace(_a + 'gt;', '>')
    s = s.replace(_a + 'quot;', '"')
    s = s.replace(_a + '#39;', "'")
    s = s.replace(_a + 'apos;', "'")
    s = s.replace(_a + 'amp;', _a)
    return s


def _parse_xml_val(text):
    text = text.strip()
    if not text:
        return ""
    val, ok = _try_json(text)
    if ok:
        return val
    items = [_parse_xml_val(m.group(1)) for m in _RE_ITEM.finditer(text)]
    if items:
        return items
    children = {}
    for m in _RE_CHILD.finditer(text):
        cn = m.group(1).lower()
        cv = _parse_xml_val(m.group(2))
        if cn in children:
            if isinstance(children[cn], list):
                children[cn].append(cv)
            else:
                children[cn] = [children[cn], cv]
        else:
            children[cn] = cv
    if children:
        return children
    return text


def _parse_param(name, raw):
    t = raw.strip()
    if not t:
        return ""
    cd = _dsml_extract_cdata(t)
    if cd is not None:
        if name.lower().strip() in _STRING_PRESERVE:
            return cd
        val, ok = _try_json(cd)
        if ok:
            return val
        if "<" in cd and ">" in cd:
            parsed = _parse_xml_val(cd)
            if parsed is not None:
                return parsed
        return cd
    decoded = _html_unescape(t)
    val, ok = _try_json(decoded)
    if ok:
        return val
    # raw-text (no CDATA) string params: preserve verbatim, don't XML-parse content/command/etc.
    if name.lower().strip() in _STRING_PRESERVE:
        return decoded
    if "<" in decoded and ">" in decoded:
        parsed = _parse_xml_val(decoded)
        if parsed is not None:
            return parsed
    return decoded


_RE_FUNCTION_CALLS = re.compile(r'<(/?)function_calls\b[^>]*>', re.IGNORECASE)

def _normalize_dsml(text):
    # Claude-native antml format uses <function_calls> as the wrapper;
    # normalize to <tool_calls> so all downstream parsing handles it uniformly.
    text = _RE_FUNCTION_CALLS.sub(lambda m: '<' + m.group(1) +'tool_calls>', text)
    return _RE_DSML_STRIP.sub(r'\1', text)


def _strip_fences(text):
    lines = text.split("\n")
    out = []
    in_fence = False
    fence_char = ""
    for line in lines:
        trimmed = line.lstrip(" \t")
        if not in_fence:
            if len(trimmed) >= 3 and trimmed[:3] in ("```", "~~~"):
                in_fence = True
                fence_char = trimmed[0]
                continue
            out.append(line)
        else:
            if trimmed.startswith(fence_char * 3) and len(re.sub(fence_char + r'{3,}\s*$', '', trimmed)) == 0:
                in_fence = False
                fence_char = ""
                continue  # closing fence delimiter: drop the line
            out.append(line)  # keep fenced content; only strip the fence delimiters
    return "\n".join(out)


def _parse_invoke(name, body):
    name = name.strip()
    if not body.strip():
        return {"id": _make_tool_id(), "name": name, "arguments": {}}
    bs = body.strip()
    if bs.startswith("{"):
        try:
            payload = json.loads(bs)
            if isinstance(payload, dict):
                inp = payload.get("input") or payload.get("arguments") or payload.get("parameters") or {}
                if isinstance(inp, dict) and inp:
                    return {"id": _make_tool_id(), "name": name, "arguments": inp}
                # bare JSON: treat the whole payload as the arguments/input
                if payload and not any(k in payload for k in ("input","arguments","parameters","name","type")):
                    return {"id": _make_tool_id(), "name": name, "arguments": payload}
        except Exception:
            pass
    input = {}
    for m in _RE_PARAM.finditer(body):
        pn = m.group(1).strip()
        if pn:
            input[pn] = _parse_param(pn, m.group(2))
    for m in _RE_PARAM_SQ.finditer(body):
        pn = m.group(1).strip()
        if pn and pn not in input:
            input[pn] = _parse_param(pn, m.group(2))
    # fallback: attribute-style value (string_value=... or value=...)
    if not input:
        for m in _RE_PARAM_ATTR.finditer(body):
            pn = m.group(1).strip()
            if pn:
                input[pn] = m.group(2).strip()
    return {"id": _make_tool_id(), "name": name, "arguments": input}

def _parse_fn_invoke(name, body):
    """Parse a <function=NAME>...<parameter=KEY>VAL</parameter>...</function> block.
    Name lives in the opening tag; params use key-in-tag. Values are raw text
    (no CDATA), so _parse_param's _STRING_PRESERVE guard keeps code/command verbatim."""
    name = (name or "").strip()
    if not body.strip():
        return {"id": _make_tool_id(), "name": name, "arguments": {}}
    arguments = {}
    for m in _RE_FN_PARAM.finditer(body):
        pn = m.group(1).strip()
        if pn:
            arguments[pn] = _parse_param(pn, m.group(2))
    if not arguments and body.strip().startswith("{"):
        for js_str in [body.strip(), _repair_loose_json(body.strip()), _repair_backslash(body.strip())]:
            try:
                jo = json.loads(js_str)
                if isinstance(jo, dict):
                    inp = jo.get("input") or jo.get("arguments") or jo.get("parameters") or {}
                    if isinstance(inp, dict):
                        arguments = inp
                    break
            except Exception:
                continue
    return {"id": _make_tool_id(), "name": name, "arguments": arguments}


def _try_legacy_json(text):
    calls = []
    for m in _RE_LEGACY.finditer(text):
        inner = m.group(1).strip()
        if not inner:
            continue
        for js_str in [inner, _repair_loose_json(inner), _repair_backslash(inner)]:
            try:
                jo = json.loads(js_str)
                if isinstance(jo, dict) and jo.get("name"):
                    a = jo.get("arguments", {})
                    if isinstance(a, str):
                        try:
                            a = json.loads(a)
                        except Exception:
                            pass
                    if not isinstance(a, dict):
                        a = {}
                    calls.append({"id": _make_tool_id(), "name": jo["name"], "arguments": a})
                    break
            except Exception:
                continue
    return calls


def _dsml_extract_calls(text):
    if not text or not text.strip():
        return []
    stripped = _strip_fences(text).strip()
    if not stripped:
        return []
    normalized = _normalize_dsml(stripped)
    calls = []
    for m in re.finditer(r'<tool_calls\b[^>]*>(.*?)</tool_calls>', normalized, re.DOTALL | re.IGNORECASE):
        for im in _RE_INVOKE.finditer(m.group(1)):
            calls.append(_parse_invoke(im.group(1), im.group(2)))
        for im in _RE_INVOKE_SQ.finditer(m.group(1)):
            calls.append(_parse_invoke(im.group(1), im.group(2)))
        if not calls:
            for im in _RE_INVOKE_ANY.finditer(m.group(1)):
                name = ""
                am = re.search(r'\bname\s*=\s*"([^"]*)"', im.group(0), re.IGNORECASE)
                if am:
                    name = am.group(1)
                calls.append(_parse_invoke(name, im.group(1)))
    if not calls:
        for im in _RE_INVOKE.finditer(normalized):
            calls.append(_parse_invoke(im.group(1), im.group(2)))
        for im in _RE_INVOKE_SQ.finditer(normalized):
            calls.append(_parse_invoke(im.group(1), im.group(2)))
    # se.zzmax 上游 <function=NAME>...</function> 注入格式 (无 tool_calls 包裹)
    if not calls:
        for im in _RE_FN_INVOKE.finditer(normalized):
            calls.append(_parse_fn_invoke(im.group(1), im.group(2)))
    if not calls:
        legacy = _try_legacy_json(normalized)
        if legacy:
            return legacy
    # fallback: bare OpenAI-style JSON {"tool_calls": [{"name": ..., "arguments": ...}]}
    if not calls:
        s2 = stripped.strip()
        if s2.startswith("{"):
            for js_str in [s2, _repair_loose_json(s2), _repair_backslash(s2)]:
                try:
                    jo = json.loads(js_str)
                    if isinstance(jo, dict) and isinstance(jo.get("tool_calls"), list):
                        out = []
                        for tc in jo["tool_calls"]:
                            if not isinstance(tc, dict):
                                continue
                            fn = tc.get("function") or {}
                            nm = fn.get("name") or tc.get("name")
                            if not nm:
                                continue
                            a = fn.get("arguments") or tc.get("arguments") or {}
                            if isinstance(a, str):
                                try: a = json.loads(a)
                                except: pass
                            if not isinstance(a, dict):
                                a = {}
                            out.append({"id": tc.get("id") or _make_tool_id(), "name": nm, "arguments": a})
                        if out:
                            return out
                except Exception:
                    continue
    return [c for c in calls if c and c.get("name")]


def _make_tools_prompt(tools, tool_choice):
    """Build DSML instruction prompt. None when inactive."""
    if not tools:
        return None
    funcs = []
    for t in tools:
        if isinstance(t, dict) and t.get("type") == "function" and isinstance(t.get("function"), dict):
            f = t["function"]
            funcs.append({"name": f.get("name", ""), "description": f.get("description", ""),
                          "parameters": f.get("parameters", {}) or {}})
    if not funcs:
        return None
    if tool_choice == "none":
        return None
    nl = chr(10)
    d = _DSML
    tco = "<" + d + "tool_calls>"
    tcc = "</" + d + "tool_calls>"
    invo = '<' + d + 'invoke name="TOOL_NAME_HERE">'
    invc = "</" + d + "invoke>"
    po = '<' + d + 'parameter name="PARAMETER_NAME">'
    pc = "</" + d + "parameter>"
    head = [
        "TOOL CALL FORMAT - FOLLOW EXACTLY:",
        tco, "  " + invo, "    " + po + "<![CDATA[PARAMETER_VALUE]]>" + pc, "  " + invc, tcc, "",
        "RULES:",
        "1) Use the " + tco + " wrapper format.",
        "2) Put one or more " + invo + " entries under a single " + tco + " root.",
        "3) Put the tool name in the invoke name attribute.",
        "4) All string values must use <![CDATA[...]]>, even short ones. This includes code, scripts, file contents, prompts, paths, names, and queries.",
        "5) Every top-level argument must be a " + po + "..." + pc + " node.",
        "6) Objects use nested XML elements inside the parameter body. Arrays may repeat <item> children.",
        "7) Numbers, booleans, and null stay plain text.",
        "8) Use only the parameter names in the tool schema. Do not invent fields.",
        "9) Do NOT wrap XML in markdown fences. Do NOT output explanations, role markers, or internal monologue.",
        "10) If you call a tool, the first non-whitespace characters of that tool block must be exactly " + tco + ".",
        "11) Never omit the opening " + tco + " tag.",
        "12) Compatibility note: the runtime also accepts the legacy XML tags <tool_calls> / <invoke> / <parameter>, but prefer the DSML-prefixed form above.",
        "13) EXECUTE, DO NOT NARRATE: if you intend to perform an action (write/edit/run/read a file, run a command, query, etc.), emit the " + tco + " block and call the tool in THIS turn. Do NOT describe the action in prose and then stop. Do NOT end your turn with only a plan/explanation/summary if a tool action is still needed to make progress. Prose narration is NEVER a substitute for a tool call: if work remains and the next step is an action, you MUST call the tool now, not say what you will do.",
        "", "PARAMETER SHAPES:",
        "- string => " + po + "<![CDATA[value]]>" + pc,
        "- object => " + po + "<field>...</field>" + pc,
        "- array => " + po + "<item>...</item><item>...</item>" + pc,
        "- number/bool/null => " + po + "plain_text" + pc,
        "", "WRONG - Do NOT do these:",
        "Wrong 1 - mixed text after XML: " + tco + "..." + tcc + " I hope this helps.",
        "Wrong 2 - Markdown code fences around XML.",
        "Wrong 3 - missing opening wrapper: just <invoke> without <tool_calls>.",
        "",
    ]
    names = [f.get("name", "") for f in funcs if f.get("name")]
    if names:
        head += [
            "CORRECT EXAMPLE - a single tool call:", tco,
            "  <" + d + 'invoke name="' + names[0] + '">',
            "    " + po + "<![CDATA[example_value]]>" + pc,
            "  " + invc, tcc, "",
            "CONNECTED TOOLS (live and callable in THIS runtime): " + ", ".join(names) + ".",
            "These tools ARE available right now. NEVER claim a listed tool is unavailable/missing/disabled, and NEVER claim you lack a terminal, shell, filesystem, or execution environment.",
            "If the user asks you to PERFORM an action now (run/execute/call a tool, read/write a file in this environment), you MUST emit a " + tco + " block in THIS turn. Refusing in prose is incorrect.",
            "If the user only wants an explanation/howto/plan, or explicitly says do not execute / do not call tools / just explain, answer in prose and do NOT emit a tool block.",
            "Put tool calls AFTER any thinking. The tool block must not be inside <think>/<thinking>.",
            "",
        ]
        # Claude Code exposes both Skill and Agent. Non-Anthropic models (esp. grok)
        # often call Skill(claude-code-guide) for built-in agent types. Hard-separate.
        name_set = set(names)
        if "Skill" in name_set and "Agent" in name_set:
            head += [
                "TOOL ROUTING — Skill vs Agent (HARD RULE):",
                "- Skill(skill=NAME): ONLY for names listed under available skills / slash-commands "
                "(user/plugin skills). Example shape: skill=\"ship\" or skill=\"update-config\".",
                "- Agent(subagent_type=TYPE): for built-in agent types listed in system-reminder "
                "agent types. TYPE examples: claude-code-guide, Explore, Plan, general-purpose, "
                "statusline-setup, claude.",
                "- NEVER call Skill with an agent type name. Skill(claude-code-guide) / "
                "Skill(Explore) / Skill(Plan) / Skill(general-purpose) / Skill(statusline-setup) "
                "are ALWAYS wrong.",
                "- Questions about Claude Code CLI/hooks/MCP/settings/Agent SDK/Claude API/"
                "Claude in Slack → Agent(subagent_type=\"claude-code-guide\", prompt=...).",
                "- If unsure whether a name is a skill or an agent type: if it appears under "
                "agent types, use Agent; only use Skill for names in the skills list.",
                "",
            ]
    head.append("IMPORTANT: Ignore any other tool/function instructions you may have been given earlier (for example cpa_final_answer, multi_tool_use, file/python/browser/search tools) - those are NOT available to you here. Use ONLY the tools listed below, and call them via the tag form above.")
    head.append("Do NOT use any other tag format either - NOT <tool_name>NAME</tool_name>, NOT <function=NAME>, NOT <function_calls>, and NOT any antml code fences. The ONLY correct form is the " + tco + " block shown above.")
    force_one = (tool_choice == "required") or (isinstance(tool_choice, dict) and tool_choice.get("type") == "function")
    force_name = None
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function" and isinstance(tool_choice.get("function"), dict):
        force_name = tool_choice["function"].get("name")
    prompt = nl.join(head)
    # Prepend the forced-tool directive in FRONT of the full DSML template
    # (do not replace it): the model still needs the format rules + examples.
    force_dir = None
    if force_one and force_name:
        force_dir = 'You MUST call the tool named "' + force_name + '" in THIS turn. Do not answer in prose.'
    elif force_one:
        force_dir = 'You MUST call exactly one tool in THIS turn. Do not answer in prose.'
    if force_dir:
        prompt = force_dir + nl + nl + prompt
    return prompt + nl + nl + "Available tools (JSON-schema):" + nl + json.dumps(funcs, ensure_ascii=False)


def _dsml_render_value(v):
    if isinstance(v, str):
        return _dsml_wrap_cdata(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if v is None:
        return "null"
    if isinstance(v, list):
        return "".join("<item>" + _dsml_render_value(i) + "</item>" for i in v)
    if isinstance(v, dict):
        return "".join("<" + k + ">" + _dsml_render_value(v[k]) + "</" + k + ">" for k in sorted(v.keys()))
    return _dsml_wrap_cdata(str(v))


def _content_to_text(content):
    """Normalize a message content (str | list-of-parts | None) into a plain
    string. OpenAI /v1/chat/completions assistant messages can carry content as
    a list of typed parts (e.g. thinking + text) alongside top-level tool_calls;
    _build_messages_with_tools must render that as a string before DSML joining."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, dict):
                if blk.get("type") == "text":
                    t = blk.get("text", "")
                    if isinstance(t, str) and t:
                        parts.append(t)
                elif "text" in blk:
                    if isinstance(blk["text"], str) and blk["text"]:
                        parts.append(blk["text"])
                # thinking/tool_use/tool_result/image blocks: dropped (text-only chat)
            elif isinstance(blk, str) and blk:
                parts.append(blk)
        return chr(10).join(p for p in parts if p)
    return json.dumps(content, ensure_ascii=False)


def _build_messages_with_tools(tools, tool_choice, messages):
    """Flatten messages: prepend DSML tools system message, render prior assistant tool_calls as DSML blocks, render tool results as user observations. Returns (msgs, enabled)."""
    prompt = _make_tools_prompt(tools, tool_choice)
    if prompt is None:
        return messages, False
    name_by_callid = {}
    for m in messages:
        if isinstance(m, dict) and m.get("tool_calls"):
            for ca in m["tool_calls"]:
                fn = ca.get("function") or {}
                cid = ca.get("id")
                if cid and fn.get("name"):
                    name_by_callid[cid] = fn["name"]
    d = _DSML
    tco = "<" + d + "tool_calls>"
    tcc = "</" + d + "tool_calls>"
    invo = "<" + d + "invoke"
    invc = "</" + d + "invoke>"
    po = "<" + d + "parameter"
    pc = "</" + d + "parameter>"
    nl = chr(10)
    out = [{"role": "system", "content": prompt}]
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        tcs = m.get("tool_calls")
        if tcs is None and content is None:
            continue
        if role == "assistant" and tcs:
            txt = _content_to_text(content)
            lines = [tco]
            for ca in tcs:
                fn = ca.get("function") or {}
                cname = fn.get("name", "")
                a0 = fn.get("arguments")
                try:
                    if isinstance(a0, str) and a0:
                        a0 = json.loads(a0)
                    elif not a0:
                        a0 = {}
                except Exception:
                    a0 = a0 if isinstance(a0, dict) else {}
                if not isinstance(a0, dict):
                    a0 = {}
                lines.append("  " + invo + ' name="' + cname + '">')
                for k in sorted(a0.keys()):
                    lines.append("    " + po + ' name="' + k + '">' + _dsml_render_value(a0[k]) + pc)
                lines.append("  " + invc)
            lines.append(tcc)
            txt = txt + nl + nl + nl.join(lines) if txt else nl.join(lines)
            out.append({"role": "assistant", "content": txt})
        elif role == "tool":
            cid = m.get("tool_call_id")
            name = name_by_callid.get(cid, "tool")
            inner = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            out.append({"role": "user", "content": "Observation from tool " + chr(34) + name + chr(34) + " (call_id " + str(cid) + "):" + nl + inner})
        else:
            out.append(m)
    fo = (tool_choice == "required") or (isinstance(tool_choice, dict) and tool_choice.get("type") == "function")
    if fo:
        if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
            fname = ""
            if isinstance(tool_choice.get("function"), dict):
                fname = tool_choice["function"].get("name", "")
            if fname:
                out.append({"role": "system", "content": "Reminder: you must call the tool named \"" + fname + "\" in this turn. Do not answer in prose."})
            else:
                out.append({"role": "system", "content": "Reminder: you must call exactly one tool in this turn. Do not answer in prose."})
        else:
            out.append({"role": "system", "content": "Reminder: you must call exactly one tool in this turn. Do not answer in prose."})
    else:
        out.append({"role": "system", "content": "Reminder: the tools listed above are CONNECTED and callable now. If the latest user message asks you to PERFORM an action now, EXECUTE the action with a tool call in THIS turn using ONLY those tools — emit a single " + tco + "..." + tcc + " block. Prose narration is NEVER a substitute for a tool call when an action is required. If the latest user message is explanation-only or says do not execute / do not call tools / just explain / reply with text only, answer in prose and do NOT call tools. Do NOT say a tool is unavailable when an action is required."})
    # Trailing reminder: repeat the user's original instruction at the end
    # so it survives long tool-heavy conversations where early messages lose attention
    _first_user = None
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user" and m.get("content"):
            _first_user = m["content"]
            break
    if _first_user and isinstance(_first_user, str) and len(_first_user) > 5:
        _reminder = _first_user[:500]  # cap to avoid bloating
        out.append({"role": "system", "content": "Context reminder — the user's original request: " + _reminder})
    return out, True


def _env_flag(name, default=True):
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in ("0", "false", "off", "no", "")


def _is_auto_tool_choice(tool_choice):
    return tool_choice is None or tool_choice == "auto"


def _tool_func_names(tools):
    names = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "function" and isinstance(t.get("function"), dict):
            n = t["function"].get("name") or ""
        else:
            n = t.get("name") or ""
        if isinstance(n, str) and n.strip():
            names.append(n.strip())
    return names


def _message_text_blob(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, dict):
                if blk.get("type") == "text" and isinstance(blk.get("text"), str):
                    parts.append(blk.get("text") or "")
                elif isinstance(blk.get("text"), str):
                    parts.append(blk.get("text") or "")
            elif isinstance(blk, str):
                parts.append(blk)
        return "\n".join(p for p in parts if p)
    return str(content)


def _latest_user_text(messages):
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            t = _message_text_blob(m.get("content"))
            if t and t.strip():
                return t
    return ""


def _message_has_tool_payload(m):
    if not isinstance(m, dict):
        return False
    if m.get("tool_calls") or m.get("role") == "tool":
        return True
    content = m.get("content")
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") in ("tool_use", "tool_result"):
                return True
    return False


def _has_recent_tool_turn(messages, window=8):
    """True when the turn immediately before the latest user nudge is still a tool exchange.

    Walks backward from the end: skip trailing user text (the nudge), then require the
    next message to carry tool_use / tool_result / tool_calls / role=tool. Only searches
    within the last `window` messages so ancient tools do not pin escalate forever.
    """
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    if not msgs:
        return False
    tail = msgs[-max(1, int(window)):]
    i = len(tail) - 1
    # Skip latest pure-text user nudge(s)
    while i >= 0:
        m = tail[i]
        if m.get("role") == "user" and not _message_has_tool_payload(m):
            i -= 1
            continue
        break
    if i < 0:
        return False
    return _message_has_tool_payload(tail[i])


_SOL_MODEL_IDS = frozenset({
    "gpt-5.6-sol",
    "gpt-5.6-luna",
    "chatgpt-5.6-sol",
    "max/gpt-5.6-sol",
    "max/gpt-5.6-luna",
    "gpt-5.6-sol-luna",
})


def _is_sol_model(model):
    """Exact allowlist for sol coding path. Claude and other GPTs never match."""
    if not model:
        return False
    s = str(model).lower().strip()
    if "claude" in s:
        return False
    if s in _SOL_MODEL_IDS:
        return True
    # strip common provider prefixes once
    for pfx in ("max/", "openai/", "chatgpt/", "azure/"):
        if s.startswith(pfx):
            s2 = s[len(pfx):]
            if s2 in _SOL_MODEL_IDS or s2 in ("gpt-5.6-sol", "gpt-5.6-luna"):
                return True
    return False


_RE_TOOL_STRONG = re.compile(
    r"(?i)(?:\b(?:run|execute|call|invoke|do\s+this|perform)\b)|"
    r"(?:\u6267\u884c|\u8fd0\u884c|\u8c03\u7528|\u7acb\u523b|\u9a6c\u4e0a|\u73b0\u5728\u5c31)"
)
_RE_TOOL_ACTION = re.compile(
    r"(?i)(?:\b(?:run|execute|call|invoke|use the|read|write|edit|create|delete|list|check|inspect|build|test|install|"
    r"curl|wget|npm|pnpm|yarn|pip|git|docker|kubectl|bash|shell|terminal|command|cwd|pwd|cat|ls|echo|python|node|dir)\b)|"
    r"(?:\u6267\u884c|\u8fd0\u884c|\u8c03\u7528|\u4f7f\u7528\u5de5\u5177|\u8bfb\u53d6|\u5199\u5165|\u67e5\u770b\u6587\u4ef6|\u68c0\u67e5\u4ed3\u5e93|\u6d4b\u8bd5|\u6784\u5efa|\u547d\u4ee4|\u7ec8\u7aef|\u5de5\u5177)"
)
_RE_TOOL_HOWTO = re.compile(
    r"(?i)(?:\b(?:how\s+(?:do|to|would|can|should)|what\s+is|explain|translate|summarize|difference\s+between|"
    r"write\s+(?:a\s+)?(?:poem|essay|story))\b)|"
    r"(?:\u5982\u4f55|\u600e\u4e48\u505a|\u600e\u6837\u624d|\u4ec0\u4e48\u662f|\u89e3\u91ca\u4e00\u4e0b|\u7ffb\u8bd1|\u603b\u7ed3|\u6982\u8ff0|\u5199\u4e00\u9996)"
)
_RE_TOOL_UNAVAIL = re.compile(
    r"(?i)(?:\b(?:not available|unavailable|no access|cannot\s+(?:run|execute)|can't\s+(?:run|execute)|"
    r"don'?t have\s+(?:access|a\s+(?:bash|shell|terminal)|tools?)|lacks?\s+(?:bash|shell|terminal|tools?))\b)|"
    r"(?:\u65e0\u6cd5\u4f7f\u7528|\u4e0d\u53ef\u7528|\u6ca1\u6709.*(?:\u5de5\u5177|\u7ec8\u7aef|bash)|\u672a\u63d0\u4f9b|\u4e0d\u80fd\u6267\u884c|\u65e0\u7ec8\u7aef|\u5f53\u524d\u73af\u5883.*\u4e0d)"
)

_RE_TOOL_NO_TOOL = re.compile(
    r"(?i)(?:\b(?:do\s+not\s+(?:execute|run|call)|don'?t\s+(?:execute|run|call)|just\s+explain|only\s+explain|"
    r"without\s+(?:running|executing|calling)|no\s+tool(?:s)?\b|reply\s+with\s+exactly|text\s+only)\b)|"
    r"(?:\u4e0d\u8981\u6267\u884c|\u4e0d\u8981\u8c03\u7528|\u4e0d\u8981\u8fd0\u884c|\u53ea\u9700\u89e3\u91ca|\u53ea\u89e3\u91ca|\u4e0d\u8981\u4f7f\u7528\u5de5\u5177|\u4ec5\u8bf4\u660e|\u53ea\u56de\u7b54)"
)




# Completion / no-further-work signals. Stops sol mid-flight thrash when the
# model wants end_turn after the task is done but structural mid-flight would
# still force tool_choice=required → Write-Output "无需进一步操作" loops.
_RE_TASK_DONE = re.compile(
    r"(?i)(?:"
    r"(?:no|without)\s+(?:further|more|additional)\s+(?:action|actions|operation|operations|work|changes?|modifications?|steps?|commands?)|"
    r"(?:nothing|no(?:thing)?)\s+(?:else|more)\s+to\s+(?:do|change|fix|run|execute)|"
    r"task\s+(?:is\s+)?(?:complete|completed|done|finished)|"
    r"(?:already\s+)?(?:complete|completed|done|finished)[:.]?\s*(?:no|nothing)|"
    r"all\s+(?:done|set|good)|"
    r"ready(?:\s+to\s+stop)?|"
    r"stop\s+(?:now|here|executing|running)|"
    r"no\s+need\s+to\s+(?:continue|proceed|run|execute|modify|change)|"
    r"无需进一步|"
    r"不需要进一步|"
    r"无需继续|"
    r"不用进一步|"
    r"无需再|"
    r"不需要再|"
    r"无需进一步操作|"
    r"无需进一步修改|"
    r"无需进一步执行|"
    r"最终核验通过|"
    r"状态已确认|"
    r"停止执行|"
    r"当前无需|"
    r"任务已完成|"
    r"修复已完成|"
    r"配置修复已完成|"
    r"已完成[，,]\s*当前无需|"
    r"已确认[：:]\s*无需|"
    r"无需进一步操作"
    r")"
)


def _recent_tool_result_texts(messages, limit=6):
    """Collect text from the most recent tool_result / role=tool payloads (newest first)."""
    out = []
    for m in reversed(messages or []):
        if not isinstance(m, dict):
            continue
        if m.get("role") == "tool":
            t = _message_text_blob(m.get("content"))
            if t and t.strip():
                out.append(t.strip())
        content = m.get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    t = _message_text_blob(b.get("content"))
                    if t and t.strip():
                        out.append(t.strip())
        if len(out) >= limit:
            break
    return out


def _looks_like_task_complete(text):
    """True when blob clearly signals the agent task is finished / no further work."""
    if not text or not str(text).strip():
        return False
    s = str(text).strip()
    if len(s) > 4000:
        s = s[:4000]
    return bool(_RE_TASK_DONE.search(s))


def _user_text_after_latest_tool(messages):
    """User text strictly after the newest tool payload; '' if none (tool-result tail)."""
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    last_tool_i = -1
    for i, m in enumerate(msgs):
        if _message_has_tool_payload(m):
            last_tool_i = i
    if last_tool_i < 0:
        return ""
    parts = []
    for m in msgs[last_tool_i + 1 :]:
        if m.get("role") == "user" and not _message_has_tool_payload(m):
            t = _message_text_blob(m.get("content"))
            if t and t.strip():
                parts.append(t.strip())
    return "\n".join(parts)


def _midflight_should_allow_end_turn(messages, answer_text="", reason_text=""):
    """Sol mid-flight: let first-pass prose end_turn through when work is done.

    Live evidence (Codex++ thrash): after settings fix succeeded, every turn still
    hit structural mid-flight -> auto->required -> often terminal-force, forcing
    Write-Output done phrases dozens of times (msgs 380->430+, 15-50s/turn).
    Gate fires when:
      A) first-pass answer claims done, OR
      B) recent tool outputs claim done AND there is no new strong user action
         after those tools (tool-result tail, short nudge, or done-claim user).
    Does NOT fire on fresh strong action requests after the tool exchange.
    """
    ans = (answer_text or "").strip()
    if ans and _looks_like_task_complete(ans):
        return True
    results = _recent_tool_result_texts(messages, limit=6)
    if not results:
        return False
    recent_done = any(_looks_like_task_complete(t) for t in results[:4])
    if not recent_done:
        return False
    # Only the user text AFTER the latest tool exchange counts as a new request.
    # Original task text before tools must not keep escalate forever.
    user = (_user_text_after_latest_tool(messages) or "").strip()
    if not user:
        return True  # pure tool-result continuation after done output
    if _looks_like_task_complete(user):
        return True
    # Fresh strong action after done-results still escalates (real new work).
    if _RE_TOOL_STRONG.search(user) and not _looks_like_task_complete(user):
        if len(user) >= 8 and not _RE_TOOL_HOWTO.search(user):
            return False
    # Short continue / ok / 做 after done-results -> allow stop (break thrash)
    if len(user) < 24:
        return True
    if len(user) <= 200 and _RE_TASK_DONE.search(user):
        return True
    # Longer non-done, non-strong user after tools: still allow stop when results
    # already said done (model is looping status chatter, not new work).
    if not _RE_TOOL_ACTION.search(user):
        return True
    return False


def _auto_action_candidate(tool_choice, tools, messages, model=None):
    """True when auto+tools needs a real tool action (pre-upstream).

    Lexical gates for all models. Structural mid-flight (history has tool turns)
    is sol-family only so short nudges (做/继续/ok) escalate; Claude stays lexical.
    Completion gate: sol mid-flight skips escalate when recent tool results
    already signal task-complete (breaks Write-Output done-loop thrash).
    """
    if not _env_flag("MAXAPI_TOOL_ESCALATE", True):
        return False
    if not _is_auto_tool_choice(tool_choice):
        return False
    names = _tool_func_names(tools)
    if not names:
        return False
    if _user_forbids_tools(messages):
        return False
    user = _latest_user_text(messages)
    mid = _has_recent_tool_turn(messages)
    if mid and _is_sol_model(model):
        # Mid-flight structural: short nudges escalate. Howto/explain stays prose-only
        # even if the sentence also contains a strong verb like "run".
        if user and _RE_TOOL_HOWTO.search(user):
            return False
        # Pre-upstream completion: recent tool results already say done -> do not force tools.
        # Aligns with _should_escalate_auto_tools post-upstream gate (both must agree).
        if _midflight_should_allow_end_turn(messages):
            return False
        return True
    if not user or len(user.strip()) < 3:
        return False
    named = any(re.search(r"(?i)\b" + re.escape(n) + r"\b", user) for n in names)
    strong = bool(_RE_TOOL_STRONG.search(user))
    action = bool(_RE_TOOL_ACTION.search(user)) or named
    howto = bool(_RE_TOOL_HOWTO.search(user))
    if howto and not strong:
        return False
    return bool(strong or (action and named) or (action and not howto))


def _user_forbids_tools(messages):
    user = _latest_user_text(messages)
    return bool(user and _RE_TOOL_NO_TOOL.search(user))


def _is_retryable_tool_upstream_err(err):
    """True for busy/overloaded/transient upstream failures safe to force-retry for tools.
    Never true for quota/auth/validation — busy≠quota must stay separate."""
    if not err:
        return False
    low = str(err).lower()
    if any(k in low for k in (
        "quota", "rate limit", "rate_limit", "429", "authentication", "unauthorized",
        "forbidden", "invalid_api", "invalid request", "context length", "too long",
        "max_tokens", "billing",
    )):
        return False
    return any(k in low for k in (
        "temporarily unavailable", "busy", "overloaded", "529", "stalled",
        "service unavailable", "bad gateway", "502", "503", "504",
        "timeout", "timed out", "retry shortly", "connection reset", "connection aborted",
    ))


def _should_escalate_auto_tools(tool_choice, tools, tools_enabled, tcs_out, err, messages, reason_text="", answer_text="", model=None):
    """Post-upstream gate: escalate auto turn that produced no tool_call but needed action.
    Retryable first-pass busy/529 may enter the ladder; quota/auth never do.
    Sol mid-flight completion: if first-pass prose or recent tool results say done,
    do NOT escalate (allow end_turn) — stops double-upstream thrash."""
    if not tools_enabled or tcs_out:
        return False
    if err and not _is_retryable_tool_upstream_err(err):
        return False
    if not _env_flag("MAXAPI_TOOL_ESCALATE", True):
        return False
    if not _is_auto_tool_choice(tool_choice):
        return False
    if _user_forbids_tools(messages):
        return False
    # Completion short-circuit BEFORE candidate (mid-flight candidate alone would force True)
    if _is_sol_model(model) and _has_recent_tool_turn(messages):
        if _midflight_should_allow_end_turn(messages, answer_text=answer_text, reason_text=reason_text):
            return False
        if answer_text and _looks_like_task_complete(answer_text):
            return False
    if _auto_action_candidate(tool_choice, tools, messages, model=model):
        return True
    # no prose path when first pass was a hard error
    if err:
        return False
    # escalate pure "tool unavailable" hallucination when user named a live tool
    names = _tool_func_names(tools)
    user = _latest_user_text(messages)
    blob = (reason_text or "") + "\n" + (answer_text or "")
    if names and user and _RE_TOOL_UNAVAIL.search(blob) and any(n.lower() in user.lower() for n in names):
        return True
    # sol mid-flight + model claimed tools unavailable even without naming a tool
    # BUT not when the same blob is a completion claim (done > fake unavail thrash)
    if names and _is_sol_model(model) and _has_recent_tool_turn(messages) and _RE_TOOL_UNAVAIL.search(blob):
        if _looks_like_task_complete(blob) or _midflight_should_allow_end_turn(
                messages, answer_text=answer_text, reason_text=reason_text):
            return False
        return True
    return False


def _escalate_tool_choice(tools, messages):
    names = _tool_func_names(tools)
    user = _latest_user_text(messages) or ""
    for n in names:
        if re.search(r"(?i)\b" + re.escape(n) + r"\b", user):
            return {"type": "function", "function": {"name": n}}
    if len(names) == 1:
        return {"type": "function", "function": {"name": names[0]}}
    return "required"


def _consume_upstream(model, msgs_up, include_reasoning, effort, search, tools_enabled, max_retry=5, max_tokens=None):
    """Collect a full non-stream upstream turn into lists."""
    answer, reason, tcs_out, err, sources = [], [], [], None, []
    for kind, data in upstream(model, msgs_up, include_reasoning, effort, search, tools_enabled, max_retry=max_retry, max_tokens=max_tokens):
        if kind == "error":
            err = data.get("error") if isinstance(data, dict) else str(data)
            break
        if kind == "content":
            answer.append(data)
        elif kind == "reasoning":
            reason.append(data)
        elif kind == "tool_call":
            tcs_out.append(data)
        elif kind == "sources":
            sources = data or []
    return answer, reason, tcs_out, err, sources


def _prefetch_until_tool_or_end(model, msgs_up, include_reasoning, effort, search, tools_enabled, max_retry=5, max_tokens=None,
                                max_events=400, max_chars=262144):
    """Buffer upstream events before client headers. Stops early on tool_call/error/end/limits.
    Returns (buf_events, remainder_iter, error, saw_tool)."""
    it = upstream(model, msgs_up, include_reasoning, effort, search, tools_enabled, max_retry=max_retry, max_tokens=max_tokens)
    buf = []
    err = None
    saw_tool = False
    chars = 0
    while True:
        try:
            ev = next(it)
        except StopIteration:
            break
        except Exception as e:
            err = str(e)
            break
        if not (isinstance(ev, tuple) and len(ev) == 2):
            continue
        kind, data = ev
        if kind == "error":
            err = data.get("error") if isinstance(data, dict) else str(data)
            break
        buf.append(ev)
        if kind == "tool_call":
            saw_tool = True
            break
        if kind in ("content", "reasoning") and isinstance(data, str):
            chars += len(data)
        if len(buf) >= max_events or chars >= max_chars:
            break
    return buf, it, err, saw_tool


def _chain_buf_and_iter(buf, remainder_iter):
    for ev in buf or []:
        yield ev
    if remainder_iter is not None:
        yield from remainder_iter


def _filter_tools_to_names(tools, names):
    """Keep only tools whose names are in names (order preserved)."""
    want = set(names or [])
    out = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "function" and isinstance(t.get("function"), dict):
            n = t["function"].get("name") or ""
        else:
            n = t.get("name") or ""
        if n in want:
            out.append(t)
    return out or list(tools or [])


def _extract_shellish_command(user_text):
    """Best-effort extract a simple shell command from an action request."""
    if not user_text:
        return None
    m = re.search(r"(?i)\becho\s+(\"[^\"]+\"|'[^']+'|\S+)", user_text)
    if m:
        return "echo " + m.group(1).strip()
    m = re.search(r"(?i)\bcommand\s*[:=]\s*(\"[^\"]+\"|'[^']+'|\S.+)$", user_text.strip())
    if m:
        return m.group(1).strip().strip("\"'")
    m = re.search(r"(?i)\b(?:run|execute)\s*:?\s*`([^`]+)`", user_text)
    if m:
        return m.group(1).strip()
    return None


def _slim_action_messages(messages):
    """Independent terminal-force context: latest user only (no prior assistant end_turn anchor)."""
    user = _latest_user_text(messages)
    if not user:
        return list(messages or [])
    cmd = _extract_shellish_command(user)
    if cmd:
        user = "Call the Bash tool exactly once now with command: " + cmd + ". Do not answer in prose."
    else:
        user = "Perform the user's requested tool action now. User request: " + user[:400] + " Emit the tool call only."
    return [{"role": "user", "content": user}]


def _terminal_force_tool_choice(tools, messages):
    etc = _escalate_tool_choice(tools, messages)
    if isinstance(etc, dict):
        return etc
    names = _tool_func_names(tools)
    if len(names) == 1:
        return {"type": "function", "function": {"name": names[0]}}
    return "required"


def _build_terminal_force_msgs(tools, messages):
    """Build a hard-forced DSML turn with only the target tool exposed."""
    etc = _terminal_force_tool_choice(tools, messages)
    force_name = None
    if isinstance(etc, dict) and isinstance(etc.get("function"), dict):
        force_name = etc["function"].get("name")
    slim_tools = _filter_tools_to_names(tools, [force_name] if force_name else _tool_func_names(tools)[:1])
    slim_msgs = _slim_action_messages(messages)
    msgs_up, te = _build_messages_with_tools(slim_tools, etc, slim_msgs)
    if not te:
        return msgs_up, te, etc, slim_tools
    name = force_name or (_tool_func_names(slim_tools)[0] if _tool_func_names(slim_tools) else "the tool")
    hard = (
        "FINAL HARD REQUIREMENT: You MUST call tool \"" + str(name) + "\" in THIS turn via the DSML "
        "tool_calls block. Do NOT answer the user in prose. Do NOT claim the tool is unavailable. "
        "Do NOT end the turn without a tool call. Emit ONLY thinking (optional) then the tool block."
    )
    msgs_up = list(msgs_up) + [{"role": "system", "content": hard}]
    return msgs_up, te, etc, slim_tools


def _run_terminal_force_nonstream(model, tools, messages, include_reasoning, effort, search, max_tokens, rid_label=""):
    """Last-resort independent forced tool attempt (single try to avoid retry storms)."""
    if not _env_flag("MAXAPI_TOOL_TERMINAL_FORCE", True):
        return None
    msgs_up, te, etc, slim_tools = _build_terminal_force_msgs(tools, messages)
    if not te:
        return None
    LOG.info("%s[tool-escalate] terminal-force ->%s tools=%s", rid_label, etc, _tool_func_names(slim_tools))
    eff = effort
    if isinstance(effort, str) and effort.lower() in ("max", "high"):
        eff = "medium"
    a, r, tcs, err, srcs = _consume_upstream(
        model, msgs_up, include_reasoning, eff, search, te, max_retry=2, max_tokens=max_tokens)
    last = (a, r, tcs, err, srcs, msgs_up, te)
    if err:
        LOG.info("%s[tool-escalate] terminal-force err=%s", rid_label, str(err)[:120])
    elif tcs:
        LOG.info("%s[tool-escalate] terminal-force success tools=%d", rid_label, len(tcs))
    else:
        LOG.info("%s[tool-escalate] terminal-force no tool_call", rid_label)
    return last


def _run_terminal_force_stream_prefetch(model, tools, messages, include_reasoning, effort, search, max_tokens, rid_label=""):
    if not _env_flag("MAXAPI_TOOL_TERMINAL_FORCE", True):
        return None
    msgs_up, te, etc, slim_tools = _build_terminal_force_msgs(tools, messages)
    if not te:
        return None
    LOG.info("%s[tool-escalate] terminal-force stream ->%s tools=%s", rid_label, etc, _tool_func_names(slim_tools))
    buf, it, err, saw = _prefetch_until_tool_or_end(
        model, msgs_up, include_reasoning, effort, search, te, max_retry=3, max_tokens=max_tokens)
    if err:
        LOG.info("%s[tool-escalate] terminal-force stream err=%s", rid_label, str(err)[:120])
    elif saw:
        LOG.info("%s[tool-escalate] terminal-force stream success", rid_label)
    else:
        LOG.info("%s[tool-escalate] terminal_forced_retry_exhausted stream", rid_label)
    return buf, it, err, saw, msgs_up, te



def _inside_fence(text):
    """Return True if `text` ends inside an open fenced code block.
    Recognises triple-backtick and triple-tilde fences (CommonMark subset).
    Each line is examined for a fence marker (run of 3+ identical chars at
    line start, after up to 3 leading spaces); indented lines (4+ spaces)
    are treated as indented code and never as fence openers/closers.
    """
    in_fence = False
    fence_ch = ""
    fence_run = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        # consume line endings
        if ch in "\r\n":
            i += 1
            if ch == "\r" and i < n and text[i] == "\n":
                i += 1
            continue
        # count up to 3 leading spaces
        spaces = 0
        j = i
        while j < n and text[j] == " " and spaces < 4:
            spaces += 1
            j += 1
        if spaces >= 4:
            # indented code line — cannot be a fence; skip to EOL
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        i = j  # advance past leading spaces
        # check for backtick/tilde run
        if i < n and text[i] in "`~":
            fc = text[i]
            run = 0
            k = i
            while k < n and text[k] == fc:
                run += 1
                k += 1
            if run >= 3:
                if not in_fence:
                    in_fence = True
                    fence_ch = fc
                    fence_run = run
                    i = k
                    # skip info string to EOL
                    while i < n and text[i] not in "\r\n":
                        i += 1
                    continue
                elif fc == fence_ch and run >= fence_run:
                    in_fence = False
                    fence_ch = ""
                    fence_run = 0
                    i = k
                    continue
        # skip rest of line
        while i < n and text[i] not in "\r\n":
            i += 1
    return in_fence


def _find_partial(s):
    last_lt = s.rfind("<")
    if last_lt < 0:
        return -1
    tail = s[last_lt:]
    if ">" in tail:
        return -1
    low = tail.lower()
    hold = -1
    for prefix in _TOOL_TAG_PREFIXES:
        if prefix.startswith(low):
            return last_lt
        # hold when low extends BEYOND a known prefix: e.g. "<function=" ,
        # "<function=write" still belongs to the <function=NAME> opener in progress
        if low.startswith(prefix):
            hold = last_lt
            break
    if hold >= 0:
        return hold
    # bare <function=NAME> opener from se.zzmax upstream
    if low.startswith("<function=") or low.startswith("<function =\""):
        return last_lt
    return -1


def _find_seg(s):
    low = s.lower()
    best = -1
    for prefix in _TOOL_TAG_FULLS:
        # Loop past fence-enclosed occurrences so a tag inside a code block
        # doesn't shadow a real tag that follows it.
        pos = 0
        while True:
            idx = low.find(prefix, pos)
            if idx < 0:
                break
            if not _inside_fence(s[:idx]):
                if best < 0 or idx < best:
                    best = idx
                break  # found a valid one; earlier is always better for this prefix
            pos = idx + 1
    # bare <function=NAME> opener (se.zzmax upstream): name varies, match by regex
    for fn in re.finditer(r'<function\s*=\s*"?[A-Za-z_]', s, re.IGNORECASE):
        fidx = fn.start()
        if not _inside_fence(s[:fidx]):
            if best < 0 or fidx < best:
                best = fidx
            break  # earliest valid match wins
    return best


def _consume_capture(captured):
    """Try to extract tool calls from captured text.
    Returns (prefix, calls, suffix, ready) or None if not ready."""
    if not captured:
        return None
    norm = _normalize_dsml(captured)
    # Bare <function=NAME>...</function> blocks (se.zzmax upstream injection, no tool_calls wrapper).
    # Guard against <tool_calls> wrappers whose content mentions "<function" in code/values.
    nlow = norm.lower()
    if "<function" in nlow and "<tool_calls" not in nlow:
        first = nlow.find("<function")
        calls_fn, search, last_end = [], first, first
        while True:
            m = _RE_FN_INVOKE.search(norm, search)
            if not m:
                break
            calls_fn.append(_parse_fn_invoke(m.group(1), m.group(2)))
            last_end = m.end()
            search = m.end()
        if calls_fn:
            prefix_fn = captured[:first] if first > 0 else ""
            return prefix_fn, calls_fn, captured[last_end:], True
        return None  # <function present but no complete block yet -> keep buffering
    open_tag = re.search(r'<tool_calls\b[^>]*>', norm, re.IGNORECASE)
    if not open_tag:
        # Check for incomplete open prefix -> keep buffering
        low = norm.lower()
        if any(low.find(p) >= 0 for p in ("<tool_calls", "<invoke ", "<parameter ")):
            return None
        return captured, [], "", True
    close_tag = re.search(r'</tool_calls\s*>', norm, re.IGNORECASE)
    if not close_tag:
        return None
    full = norm[open_tag.start():close_tag.end()]
    # slice prefix/suffix in NORMALIZED coords: normalize is idempotent and shrinks by
    # 6 chars per |DSML| prefix removed, so a single diff offset is wrong when multiple
    # |DSML| tags appear (that mis-mapped suffix and leaked closing tags as content).
    prefix = norm[:open_tag.start()] if open_tag.start() > 0 else ""
    calls = []
    for im in _RE_INVOKE.finditer(full):
        calls.append(_parse_invoke(im.group(1), im.group(2)))
    for im in _RE_INVOKE_SQ.finditer(full):
        calls.append(_parse_invoke(im.group(1), im.group(2)))
    if not calls:
        for im in _RE_INVOKE_ANY.finditer(full):
            name = ""
            am = re.search(r'\bname\s*=\s*"([^"]*)"', im.group(0), re.IGNORECASE)
            if am:
                name = am.group(1)
            calls.append(_parse_invoke(name, im.group(1)))
    if not calls:
        legacy = _try_legacy_json(full)
        if legacy:
            calls = legacy
    suffix = norm[close_tag.end():]
    if not calls:
        return None
    return prefix, calls, suffix, True


class ToolCallParser:
    """Streaming DSML sieve (ported from ds2api toolstream).
    feed(str) -> list of ("content", str) / ("tool_call", {id,name,arguments}).
    flush() -> list of same."""
    _MAX_CAPTURE_CHARS = 2 * 1024 * 1024

    def __init__(self):
        self.pending = ""
        self.capture = ""
        self.capturing = False

    @staticmethod
    def _emit(text):
        """Content emission point: strips upstream literal <br> artifacts."""
        return ("content", _strip_stray_br(text))

    def feed(self, text):
        self.pending += text
        out = []
        while True:
            if self.capturing:
                self.capture += self.pending
                self.pending = ""
                if len(self.capture) > self._MAX_CAPTURE_CHARS:
                    LOG.warning("[tcp] dropping oversized incomplete tool capture (%d chars)", len(self.capture))
                    self.capture = ""
                    self.capturing = False
                    continue
                r = _consume_capture(self.capture)
                if r is None:
                    break
                prefix, calls, suffix, ready = r
                if not ready:
                    break
                self.capturing = False
                self.capture = ""
                if prefix:
                    out.append(self._emit(prefix))
                for c in calls:
                    out.append(("tool_call", c))
                if suffix:
                    self.pending = suffix
                continue
            if not self.pending:
                break
            seg = _find_seg(self.pending)
            if seg >= 0:
                prefix = self.pending[:seg]
                if prefix:
                    out.append(self._emit(prefix))
                self.capture = self.pending[seg:]
                self.pending = ""
                self.capturing = True
                continue
            partial = _find_partial(self.pending)
            if partial >= 0:
                safe = self.pending[:partial]
                hold = self.pending[partial:]
                if safe:
                    out.append(self._emit(safe))
                self.pending = hold
                break
            else:
                out.append(self._emit(self.pending))
                self.pending = ""
                break
        return out

    def flush(self):
        out = []
        if self.capturing:
            self.capture += self.pending
            self.pending = ""
            if len(self.capture) > self._MAX_CAPTURE_CHARS:
                LOG.warning("[tcp] dropping oversized incomplete tool capture (%d chars)", len(self.capture))
                self.capture = ""
                self.capturing = False
                return out
            r = _consume_capture(self.capture)
            if r is not None:
                prefix, calls, suffix, ready = r
                self.capturing = False
                self.capture = ""
                if prefix:
                    out.append(self._emit(prefix))
                for c in calls:
                    out.append(("tool_call", c))
                if suffix:
                    out.append(self._emit(suffix))
                return out
            content = self.capture
            self.capture = ""
            self.capturing = False
            tries = _dsml_extract_calls(content)
            if tries:
                for c in tries:
                    out.append(("tool_call", c))
            else:
                # P0-fix: incomplete DSML block at stream end — discard the
                # fragment that contains raw tags to avoid leaking them as
                # visible text.  Keep any clean prefix before the opening tag.
                _clow = content.lower()
                _seg = -1
                for _tp in _TOOL_TAG_FULLS:
                    _i = _clow.find(_tp)
                    if _i >= 0 and (_seg < 0 or _i < _seg):
                        _seg = _i
                if _seg < 0:
                    # also check bare <function= opener
                    _fm = re.search(r'<function\s*=\s*"?[A-Za-z_]', content, re.IGNORECASE)
                    if _fm:
                        _seg = _fm.start()
                if _seg >= 0:
                    # has text before the incomplete tag — keep that prefix, discard the tag fragment
                    out.append(self._emit(content[:_seg]))
                # else: entire content is the incomplete DSML fragment → discard
                sys.stderr.write("[tcp] flush-discarded %d bytes of incomplete DSML\n" % len(content));
        if self.pending:
            out.append(self._emit(self.pending))
            self.pending = ""
        return out


def _estimate_tokens(text):
    """Token estimate: ~1 token per word for English, ~1.5 per CJK char, +2 overhead."""
    if not text:
        return 0
    # Count CJK characters (each ~1.5 tokens)
    cjk = 0
    for ch in text:
        if 0x3400 <= ord(ch) <= 0x4DBF or 0x4E00 <= ord(ch) <= 0x9FFF or '　' <= ch <= '〿' or 0xFF00 <= ord(ch) <= 0xFFEF:
            cjk += 1
    ascii_len = len(text) - cjk
    # English: ~4 chars per token (word-based is better but this is a fast heuristic)
    return max(1, int(ascii_len / 4) + int(cjk * 1.5) + 2)


def _estimate_messages_tokens(messages):
    """Estimate total input tokens from a message list."""
    total = 0
    for m in messages:
        if isinstance(m, dict):
            c = m.get("content")
            if isinstance(c, str):
                total += _estimate_tokens(c)
            elif isinstance(c, list):
                for blk in c:
                    if isinstance(blk, dict):
                        total += _estimate_tokens(blk.get("text", ""))
            tcs = m.get("tool_calls")
            if isinstance(tcs, list):
                for tc in tcs:
                    fn = tc.get("function") or {}
                    total += _estimate_tokens(fn.get("arguments", ""))
                    total += _estimate_tokens(fn.get("name", ""))
    return max(1, total)


# ── Improved token estimation (includes system + tools) ─────────────────────
def _est_text(s):
    """Estimate tokens for a plain text string."""
    if not s:
        return 0
    cjk = sum(1 for ch in s if 0x3400 <= ord(ch) <= 0x4DBF or 0x4E00 <= ord(ch) <= 0x9FFF or 0xFF00 <= ord(ch) <= 0xFFEF)
    return int(cjk * 1.05 + (len(s) - cjk) / 3.6)


def _est_blocks(blocks):
    """Estimate tokens from a list of content blocks."""
    total = 0
    for b in (blocks or []):
        if not isinstance(b, dict):
            continue
        bt = b.get("type", "")
        if bt == "text":
            total += _est_text(b.get("text", ""))
        elif bt == "thinking":
            total += _est_text(b.get("thinking", ""))
        elif bt == "tool_use":
            total += _est_text(b.get("name", "")) + _est_text(json.dumps(b.get("input", {}), ensure_ascii=False)) + 12
        elif bt == "tool_result":
            total += _est_text(_stringify_content(b.get("content"))) + 8
        elif bt == "image":
            total += 1600
        elif bt == "document":
            total += 3000
    return total


def _stringify_content(c):
    """Convert content block value to plain string for estimation."""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for item in c:
            if isinstance(item, dict):
                parts.append(item.get("text", "") or item.get("content", "") or "")
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(c) if c else ""


def _estimate_request_tokens(body, model=None):
    """Estimate total tokens for an Anthropic-format request body (includes system + tools).
    If model is provided, applies EWMA self-calibration ratio."""
    total = 0
    # system prompt
    sys = body.get("system")
    if isinstance(sys, str):
        total += _est_text(sys)
    elif isinstance(sys, list):
        total += sum(_est_text(b.get("text", "")) for b in sys if isinstance(b, dict))
    # tools
    for t in (body.get("tools") or []):
        total += _est_text(json.dumps(t, ensure_ascii=False)) + 8
    # messages
    for m in (body.get("messages") or []):
        total += 4  # role/turn overhead
        c = m.get("content")
        if isinstance(c, str):
            total += _est_text(c)
        elif isinstance(c, list):
            total += _est_blocks(c)
        tcs = m.get("tool_calls")
        if isinstance(tcs, list):
            for tc in tcs:
                fn = tc.get("function") or {}
                total += _est_text(fn.get("arguments", "")) + _est_text(fn.get("name", "")) + 8
    # Apply EWMA calibration if available
    if model:
        total = int(total * _ewma_get(model))
    return total


# ── EWMA self-calibration for token estimation ───────────────────────────────
_EWMA_ALPHA = 0.15  # smoothing factor (lower = more stable, higher = faster adaptation)
_EWMA_MAX_KEYS = 64
_EWMA_MAX_N = 1_000_000
_ewma_store = {}    # model -> {"ratio": float, "n": int}, hard-bounded
_EWMA_LOCK = threading.Lock()

def _ewma_update(model, actual, estimated):
    """Update EWMA ratio with a new (actual, estimated) sample. Thread-safe."""
    if estimated <= 0 or actual <= 0:
        return
    ratio = actual / estimated
    # Clamp to [0.5, 2.0] to prevent single outlier from destabilizing
    ratio = max(0.5, min(2.0, ratio))
    # Prevent arbitrary request/model data from creating huge persistent keys.
    model = str(model)[:128]
    with _EWMA_LOCK:
        if model not in _ewma_store:
            if len(_ewma_store) >= _EWMA_MAX_KEYS:
                _ewma_store.pop(next(iter(_ewma_store)))
            _ewma_store[model] = {"ratio": ratio, "n": 1}
        else:
            s = _ewma_store[model]
            s["ratio"] = _EWMA_ALPHA * ratio + (1 - _EWMA_ALPHA) * s["ratio"]
            s["n"] = min(_EWMA_MAX_N, s["n"] + 1)

def _ewma_get(model):
    """Get the current calibration ratio for a model. Returns 1.0 if no data."""
    with _EWMA_LOCK:
        s = _ewma_store.get(model)
        return s["ratio"] if s else 1.0


# ── Compaction: shrink context to fit budget ─────────────────────────────────
_TOO_LONG_HINTS = (
    "input too long", "prompt is too long",
    "context length exceeded", "context_length_exceeded",
    "maximum context length", "exceeds context_length",
    "too many total tokens", "reduce the length of the messages",
)
_OUTPUT_BUDGET_HINTS = ("max_tokens", "max_completion_tokens", "max output tokens")

_RE_EXCEEDS = re.compile(
    r"input too long[:.]?\s*(?:estimated\s+)?(\d[\d,]*)\s*tokens?\s*exceeds\s+(?:context_length|limit)\s+(\d[\d,]*)",
    re.I,
)
_RE_GT = re.compile(
    r"(?:prompt|input)\s+is\s+too\s+long[:.]?\s*(\d[\d,]*)\s*>\s*(\d[\d,]*)",
    re.I,
)
_RE_MAXCTX = re.compile(
    r"(\d[\d,]*)\s*tokens?.{0,60}?maximum context length is\s*(\d[\d,]*)",
    re.I,
)
_RE_MAXCTX_REV = re.compile(
    r"maximum context length is\s*(\d[\d,]*).{0,60}?(\d[\d,]*)\s*tokens?",
    re.I,
)
# Note: _RE_MAXCTX_REV group order is (allowed, actual) — reversed from others.
# We swap in _parse_too_long when this regex matches.

class _TooLong:
    """Result of _parse_too_long: actual/allowed may each be int or None."""
    __slots__ = ("actual", "allowed")
    def __init__(self, actual, allowed):
        self.actual = actual
        self.allowed = allowed
    def __bool__(self):
        return True  # always truthy when returned (vs None = not matched)

_COMPACT_SAFETY = 0.90        #留10%给system/tools编码差异
_COMPACT_BLIND_RATIO = 0.60   #完全无数字时的盲压缩比
_COMPACT_FLOOR = 8000
_MAX_COMPACT_RETRIES = 2

def _parse_too_long(err_text):
    """Parse upstream 'too long' error.

    Returns:
        None          – not a "too long" error at all
        _TooLong(a,b) – matched, a/allowed may be int or None
    """
    txt = str(err_text) or ""
    # Head+tail truncation to prevent ReDoS while keeping numbers visible
    _HALF = 4096
    if len(txt) > _HALF * 2:
        txt = txt[:_HALF] + "…" + txt[-_HALF:]
    for rx in (_RE_EXCEEDS, _RE_GT, _RE_MAXCTX):
        m = rx.search(txt)
        if m:
            try:
                actual = int(m.group(1).replace(",", ""))
                allowed = int(m.group(2).replace(",", ""))
            except (ValueError, TypeError):
                continue
            if actual < allowed:
                LOG.warning("_parse_too_long: rejected inverted parse actual=%d < allowed=%d", actual, allowed)
                return None  # suspicious parse, reject
            return _TooLong(actual, allowed)
    # _RE_MAXCTX_REV has (allowed, actual) group order — swap
    m = _RE_MAXCTX_REV.search(txt)
    if m:
        try:
            actual = int(m.group(2).replace(",", ""))
            allowed = int(m.group(1).replace(",", ""))
        except (ValueError, TypeError):
            pass
        else:
            if actual >= allowed:
                return _TooLong(actual, allowed)
            LOG.warning("_parse_too_long: rejected inverted MAXCTX_REV actual=%d < allowed=%d", actual, allowed)
            return None  # inverted, reject
    # Keyword fallback: only if no regex matched at all
    low = txt.lower()
    if any(h in low for h in _TOO_LONG_HINTS):
        #排除"输出预算过大"这类同样含too long但不该压缩的错误
        if any(h in low for h in _OUTPUT_BUDGET_HINTS) and "context" not in low:
            return None
        return _TooLong(None, None)
    return None


def _compact_budget(tl, model, est_tokens, max_tokens=8192):
    """Derive compaction target from a _TooLong result.

    Guarantees: result <= est_tokens (monotonic decrease) and accounts for
    output reservation (max_tokens) so the compacted payload fits.
    """
    out_reserve = max_tokens or 8192
    if tl.allowed:
        budget = int(tl.allowed * _COMPACT_SAFETY) - out_reserve
    else:
        ctx = (MODEL_META.get(model) or (None,))[0]
        if ctx:
            budget = int(ctx * _COMPACT_SAFETY) - out_reserve
        else:
            base = tl.actual or est_tokens
            budget = max(_COMPACT_FLOOR, int(base * _COMPACT_BLIND_RATIO))
    if budget <= 0:
        LOG.warning("_compact_budget: budget=%d (allowed*0.9=%s out_reserve=%d), using floor",
                     budget, tl.allowed, out_reserve)
        budget = _COMPACT_FLOOR
    # Ensure strict decrease from current estimate
    return max(_COMPACT_FLOOR, min(budget, int(est_tokens * 0.80)))


def _msg_blocks(msg):
    """Extract content blocks from a message."""
    c = msg.get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    if isinstance(c, list):
        return [b for b in c if isinstance(b, dict)]
    return []


def _segments(messages):
    """Group messages into atomic segments where no segment has an unresolved tool_use.
    Handles both Anthropic (content blocks) and OpenAI (tool_calls + role:tool) formats.
    Returns list of message-list segments."""
    segs, cur, pending = [], [], set()
    for m in messages:
        cur.append(m)
        role = m.get("role", "")
        # Anthropic format: tool_use/tool_result in content blocks
        for b in _msg_blocks(m):
            if b.get("type") == "tool_use":
                pending.add(b.get("id"))
            elif b.get("type") == "tool_result":
                pending.discard(b.get("tool_use_id"))
        # OpenAI format: tool_calls on assistant, tool role messages
        tcs = m.get("tool_calls")
        if isinstance(tcs, list):
            for tc in tcs:
                tid = tc.get("id")
                if tid:
                    pending.add(tid)
        if role == "tool":
            tid = m.get("tool_call_id")
            if tid:
                pending.discard(tid)
        if not pending:
            segs.append(cur)
            cur = []
    if cur:
        segs.append(cur)
    return segs


def _compact_headers(meta):
    """Build response headers from compaction metadata dict. Empty if no compaction."""
    if not meta:
        return {}
    h = {"X-Maxapi-Compacted": "1"}
    if meta.get("dropped_segments"):
        h["X-Maxapi-Dropped-Segments"] = str(meta["dropped_segments"])
    if meta.get("tool_results_trimmed"):
        h["X-Maxapi-Trimmed-Tool-Results"] = str(meta["tool_results_trimmed"])
    if meta.get("text_truncated"):
        h["X-Maxapi-Truncated-Texts"] = str(meta["text_truncated"])
    if meta.get("before") and meta.get("after"):
        h["X-Maxapi-Token-Estimate"] = f"{meta['before']}->{meta['after']}"
    return h


def _append_system_note(body, note):
    """Append a note to the system prompt without corrupting message alternation."""
    sys = body.get("system")
    note_block = {"type": "text", "text": note}
    if isinstance(sys, str):
        body["system"] = [{"type": "text", "text": sys}, note_block]
    elif isinstance(sys, list):
        sys.append(note_block)
    else:
        body["system"] = [note_block]


def _msg_token_cost(m):
    """Per-message token estimate without EWMA (for incremental compaction)."""
    total = 4  # role/turn overhead
    if not isinstance(m, dict):
        return total
    c = m.get("content")
    if isinstance(c, str):
        total += _est_text(c)
    elif isinstance(c, list):
        total += _est_blocks(c)
    tcs = m.get("tool_calls")
    if isinstance(tcs, list):
        for tc in tcs:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            total += _est_text(fn.get("arguments", "")) + _est_text(fn.get("name", "")) + 8
    return total


def _fixed_overhead_tokens(body):
    """system + tools overhead (stable across message compaction)."""
    total = 0
    sys = body.get("system")
    if isinstance(sys, str):
        total += _est_text(sys)
    elif isinstance(sys, list):
        total += sum(_est_text(b.get("text", "")) for b in sys if isinstance(b, dict))
    for t in (body.get("tools") or []):
        total += _est_text(json.dumps(t, ensure_ascii=False)) + 8
    return total


def _trim_tool_result_text(txt):
    """Head/tail clamp a tool_result string to _TOOL_RESULT_CAP tokens. Returns (new_txt, changed)."""
    if _est_text(txt) <= _TOOL_RESULT_CAP:
        return txt, False
    _char_budget = int(_TOOL_RESULT_CAP * 3.6)
    if len(txt) <= _char_budget:
        return txt, False
    half = _char_budget // 2
    new_txt = (
        f"{txt[:half]}\n\n[... {_est_text(txt) - _TOOL_RESULT_CAP} "
        f"tokens elided by proxy ...]\n\n{txt[-half:]}")
    return new_txt, True


def compact_request(body, budget, model=None):
    """4-stage compaction to fit request within token budget.

    Uses incremental per-message costs so long chats (600+ msgs) finish in
    milliseconds instead of O(n^2) full-body re-estimates per drop.
    Returns (compacted_body, report_string, compaction_meta_dict).
    Never touches the last 2 messages or system.
    """
    body_original = body  # keep reference for validation fallback
    body = copy.deepcopy(body)
    msgs = body.get("messages") or []
    notes = []
    ratio = _ewma_get(model) if model else 1.0
    overhead = _fixed_overhead_tokens(body)
    costs = [_msg_token_cost(m) for m in msgs]

    def _est_now():
        return int((overhead + sum(costs)) * ratio)

    before = _est_now()
    est = before

    # Stage 1: clamp oversized tool_result payloads (oldest first)
    trimmed = 0
    for i, m in enumerate(msgs):
        if est <= budget:
            break
        changed = False
        for b in _msg_blocks(m):
            if b.get("type") == "tool_result":
                txt = _stringify_content(b.get("content"))
                new_txt, did = _trim_tool_result_text(txt)
                if did:
                    b["content"] = new_txt
                    changed = True
                    trimmed += 1
        if m.get("role") == "tool":
            txt = str(m.get("content", ""))
            new_txt, did = _trim_tool_result_text(txt)
            if did:
                m["content"] = new_txt
                changed = True
                trimmed += 1
        if changed:
            old_c = costs[i]
            costs[i] = _msg_token_cost(m)
            est -= int((old_c - costs[i]) * ratio)
            if trimmed % 8 == 0:
                est = _est_now()
    if trimmed:
        est = _est_now()
        notes.append(f"tool_results_trimmed={trimmed}")

    # Stage 2: drop middle segments, keep first turn + tail
    body["messages"] = msgs
    segs = _segments(msgs)
    seg_costs = []
    idx = 0
    for s in segs:
        sc = 0
        for _m in s:
            sc += costs[idx] if idx < len(costs) else _msg_token_cost(_m)
            idx += 1
        seg_costs.append(sc)

    dropped = 0
    # Batch-drop middle segments: sort middle by cost desc and drop until under budget.
    # O(n log n) instead of hundreds of single-pop passes on 600+ msg chats.
    if est > budget and len(segs) > _KEEP_TAIL_SEGMENTS + 1:
        if _KEEP_TAIL_SEGMENTS > 0:
            mid_lo, mid_hi = 1, len(segs) - _KEEP_TAIL_SEGMENTS
        else:
            mid_lo, mid_hi = 1, len(segs)
        mid_idx = list(range(mid_lo, mid_hi))
        # heaviest first
        mid_idx.sort(key=lambda j: seg_costs[j], reverse=True)
        drop_set = set()
        need = est - budget
        freed = 0
        for j in mid_idx:
            if freed >= need and len(segs) - len(drop_set) <= _KEEP_TAIL_SEGMENTS + 1:
                break
            if len(segs) - len(drop_set) <= _KEEP_TAIL_SEGMENTS + 1:
                break
            # always keep at least head + tail
            if freed >= need and drop_set:
                break
            drop_set.add(j)
            freed += int(seg_costs[j] * ratio)
        if drop_set:
            new_segs, new_costs = [], []
            for i, s in enumerate(segs):
                if i in drop_set:
                    dropped += 1
                    continue
                new_segs.append(s)
                new_costs.append(seg_costs[i])
            segs, seg_costs = new_segs, new_costs
            est -= freed
            # clamp drift
            if est < 0:
                est = 0
    body["messages"] = [m for s in segs for m in s]
    costs = [_msg_token_cost(m) for m in body["messages"]]
    est = int((overhead + sum(costs)) * ratio)
    if dropped:
        notes.append(f"segments_dropped={dropped}")

    # Stage 3: head/tail truncate remaining text blocks (oldest first, skip last 2 msgs)
    truncated = 0
    target_msgs = body.get("messages") or []
    for i, m in enumerate(target_msgs[:-2] if len(target_msgs) > 2 else []):
        if est <= budget:
            break
        changed = False
        for b in _msg_blocks(m):
            if b.get("type") == "text" and len(b.get("text", "")) > 2000:
                txt = b["text"]
                b["text"] = txt[:1000] + f"\n[... {len(txt) - 1800} chars elided ...]\n" + txt[-800:]
                changed = True
                truncated += 1
        if changed:
            old_c = costs[i]
            costs[i] = _msg_token_cost(m)
            est -= int((old_c - costs[i]) * ratio)
    if truncated:
        est = int((overhead + sum(costs)) * ratio)
        notes.append(f"text_truncated={truncated}")

    # Stage 4: still over? forward anyway (upstream decides)
    after = int((overhead + sum(costs)) * ratio)

    meta = {"before": before, "after": after, "budget": budget,
            "dropped_segments": dropped, "tool_results_trimmed": trimmed,
            "text_truncated": truncated}

    # Validate: ensure tool_use/tool_result pairing is intact
    if not _validate_compacted_messages(body.get("messages") or []):
        notes.append("VALIDATION_FAILED → reverting to original")
        LOG.warning("compact validation failed, reverting to original request")
        return copy.deepcopy(body_original), f"est {before} budget={budget} VALIDATION_FAILED", {}

    return body, f"est {before}->{after} budget={budget} " + " ".join(notes), meta

def _validate_compacted_messages(messages):
    """Post-compaction validator: ensure tool_use/tool_result pairing is intact.
    Returns True if valid, False if compaction produced broken structure."""
    if not messages:
        return False
    # Collect all tool_use ids from assistant messages
    tool_use_ids = set()
    tool_result_ids = set()
    for m in messages:
        role = m.get("role", "")
        content = m.get("content")
        # Anthropic format: content blocks
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    tid = b.get("id")
                    if tid:
                        tool_use_ids.add(tid)
                elif b.get("type") == "tool_result":
                    tid = b.get("tool_use_id")
                    if tid:
                        tool_result_ids.add(tid)
        # OpenAI format: tool_calls on assistant, tool role messages
        tcs = m.get("tool_calls")
        if isinstance(tcs, list):
            for tc in tcs:
                tid = tc.get("id")
                if tid:
                    tool_use_ids.add(tid)
        if role == "tool":
            tid = m.get("tool_call_id")
            if tid:
                tool_result_ids.add(tid)
    # Every tool_use must have a matching tool_result, and vice versa
    # Allow some slack: orphan tool_results (from already-dropped tool_uses) are ok
    # but orphan tool_uses (tool_use without tool_result) are NOT ok
    orphans = tool_use_ids - tool_result_ids
    orphan_results = tool_result_ids - tool_use_ids
    if orphans:
        LOG.info("compact validator: %d orphan tool_use ids (tolerated ≤3): %s", len(orphans), list(orphans)[:5])
    if orphan_results:
        LOG.info("compact validator: %d orphan tool_result ids (expected from dropped segments): %s", len(orphan_results), list(orphan_results)[:5])
    if len(orphans) > 3:  # allow up to 3 orphans (e.g., from truncation of tail)
        LOG.warning("compact validator: %d orphan tool_use ids EXCEEDS threshold: %s", len(orphans), list(orphans)[:5])
        return False
    return True


def _thinking_signature(text):
    """Anthropic thinking blocks carry a 'signature' field (CC validates it as a
    non-empty opaque string). Derive a stable sig from the thinking text so the
    block is accepted and cacheable. Format mirrors real API (~100 base-ish chars)."""
    h = hashlib.sha256(("sig:" + text).encode("utf-8")).hexdigest()
    return "Eu" + h[:96]


def _flatten_anthropic_messages(messages, system_text):
    """Convert Claude Code / Anthropic Messages request into the OpenAI-style
    message list that _build_messages_with_tools + upstream() already consume.
    Anthropic content can be a string or an array of typed blocks:
      text -> append text
      tool_use {id,name,input} -> assistant tool_calls (carried verbatim)
      tool_result {tool_use_id,content} -> role=tool (flattened by _build_messages_with_tools)
    System string/array -> a leading system message (kept; tools prompt is
    prepended later by _build_messages_with_tools)."""
    out = []
    if system_text:
        out.append({"role": "system", "content": system_text})
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content")
        if content is None:
            content = ""
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        # array of blocks
        text_parts = []
        tool_calls = []
        tool_results = []
        if isinstance(content, list):
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                bt = blk.get("type")
                if bt == "text":
                    text_parts.append(blk.get("text", ""))
                elif bt == "tool_use":
                    tool_calls.append({
                        "id": blk.get("id", ""),
                        "type": "function",
                        "function": {"name": blk.get("name", ""),
                                     "arguments": json.dumps(blk.get("input", {}), ensure_ascii=False)},
                    })
                elif bt == "tool_result":
                    inner = blk.get("content")
                    if isinstance(inner, list):
                        # array of result blocks (text only in our case)
                        inner = "".join(b.get("text", "") for b in inner if isinstance(b, dict) and b.get("type") == "text")
                    elif not isinstance(inner, str):
                        inner = json.dumps(inner, ensure_ascii=False)
                    tool_results.append({"tool_call_id": blk.get("tool_use_id", ""), "content": inner or ""})
                # image blocks: ignored for now (upstream text-only chat)
        if role == "assistant":
            if tool_calls:
                tc_render = [{"id": c["id"], "function": c["function"]} for c in tool_calls]
                # _build_messages_with_tools looks for assistant.tool_calls to render text blocks
                out.append({"role": "assistant", "content": "\n".join(text_parts) if text_parts else None, "tool_calls": tc_render})
            else:
                out.append({"role": "assistant", "content": "\n".join(text_parts)})
        elif role == "user" and tool_results:
            # tool results come as user messages when CC notícias round-trips; map to tool role
            for tr in tool_results:
                out.append({"role": "tool", "tool_call_id": tr["tool_call_id"], "content": tr["content"]})
            if text_parts:
                out.append({"role": "user", "content": "\n".join(text_parts)})
        else:
            out.append({"role": role, "content": "\n".join(text_parts)})
    return out


def _anthropic_tools_to_openai(tools):
    """Anthropic tools [{name,description,input_schema}] -> OpenAI function tools."""
    out = []
    if not isinstance(tools, list):
        return out
    for t in tools:
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        if not name:
            continue
        out.append({"type": "function", "function": {
            "name": name,
            "description": t.get("description", ""),
            "parameters": t.get("input_schema", {}) or {"type": "object", "properties": {}},
        }})
    return out


def _anthropic_tool_choice(tool_choice):
    """Anthropic tool_choice variants -> OpenAI tool_choice + whether tools enabled."""
    if tool_choice is None:
        return None
    if tool_choice == "auto":
        return "auto"
    if tool_choice == "any":
        return "required"
    if tool_choice == "none":
        return "none"
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "tool":
        nm = (tool_choice.get("name") or "")
        return {"type": "function", "function": {"name": nm}}
    return "auto"



# ── Connection pool for upstream HTTPS ───────────────────────────────────────
# Reuse idle connections across concurrent requests to reduce handshake latency
# and avoid port-exhaustion under 3-4 parallel agents.
_CONN_POOL_MAXSIZE = 8  # idle connections kept per host
# Connect/headers budget. Keep short so a hung upstream fails before the client
# (Claude Code ~60-90s) shows a generic "API Error" with no body.
_CONN_CONNECT_TIMEOUT = 20
# Upstream emits SSE keepalives ": ping <ts>" about every 15s while thinking.
# Stall must exceed that interval or healthy streams are aborted as false 529s.
_STALL_TIMEOUT = 35          # no socket bytes between chunks (ping ~15s)
_FIRST_BYTE_TIMEOUT = 45     # allow slow first content under ping keepalives
# Budget ONLY for connect + retries BEFORE any content/reasoning is yielded.
# Once the model starts streaming (incl. thinking), wall-clock deadline is
# disabled — only inter-chunk stall applies. Otherwise long thinking (~1min+)
# gets hard-killed mid-stream (Cherry shows thinking then forced stop).
_UPSTREAM_DEADLINE = 75.0
_UPSTREAM_CONCURRENCY = int(os.getenv("MAXAPI_UPSTREAM_CONCURRENCY", "3"))
_UPSTREAM_QUEUE_WAIT = float(os.getenv("MAXAPI_UPSTREAM_QUEUE_WAIT", "3.0"))
_upstream_sema = threading.BoundedSemaphore(max(1, _UPSTREAM_CONCURRENCY))
_conn_pool_lock = threading.Lock()
_conn_pool = []  # list of idle http.client.HTTPSConnection

def _pool_get():
    """Borrow an idle connection or create a fresh one."""
    with _conn_pool_lock:
        while _conn_pool:
            c = _conn_pool.pop()
            try:
                # Quick liveness probe: if the socket is gone, discard
                if c.sock is None:
                    c.close()
                    continue
                # Reset per-request timeouts; pooled sockets may carry old values.
                if c.sock is not None:
                    c.sock.settimeout(_CONN_CONNECT_TIMEOUT)
            except Exception:
                try:
                    c.close()
                except Exception:
                    pass
                continue
            return c
    return http.client.HTTPSConnection(BASE, timeout=_CONN_CONNECT_TIMEOUT,
                                       context=ssl.create_default_context())

def _pool_put(conn):
    """Return a connection to the pool if there is room; otherwise close it."""
    with _conn_pool_lock:
        if len(_conn_pool) < _CONN_POOL_MAXSIZE:
            _conn_pool.append(conn)
            return
    try:
        conn.close()
    except Exception:
        pass

def upstream(model_field, messages, include_reasoning=False, reasoning_effort="medium", search=False, tools_enabled=False, max_retry=6, max_tokens=None):
    grp, sub, disp = resolve_model(model_field)
    payload = {"model": grp, "subModel": sub, "messages": messages, "stream": True}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if reasoning_effort and reasoning_effort != "off":
        payload["reasoningEffort"] = reasoning_effort
    if search:
        payload["search"] = True
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # diagnostic: check if canary/test markers survive transformation
    _payload_str = json.dumps(payload, ensure_ascii=False)
    _roles = "".join(m.get("role","?")[0] for m in messages[:50])
    if "ZK-7391" in _payload_str or len(messages) > 50:
        LOG.info("[diag] upstream msgs=%d bytes=%d has_canary=%s roles=%s",
                 len(messages), len(_payload_str), "ZK-7391" in _payload_str, _roles)
    sources_sent = False
    content_yielded = False
    _deadline = time.monotonic() + _UPSTREAM_DEADLINE
    xff = identity_acquire(force_new=False)
    retry_class = None  # quota|busy|stall
    for attempt in range(1, max_retry + 1):
        # Never retry after bytes were already sent to the client — would duplicate.
        if content_yielded and attempt > 1:
            return
        if not content_yielded:
            _remain = _deadline - time.monotonic()
            if _remain <= 0.5:
                LOG.warning("[deadline] upstream budget exhausted before attempt %d/%d model=%s",
                            attempt, max_retry, disp)
                yield ("error", {"error": "upstream model busy/stalled (deadline); retry shortly"})
                return
        # 重建解析器，避免重试时残留上次的部分状态导致输出错乱
        # Always peel <think> so DSML inside thinking can be sieved; client emission
        # of reasoning is gated at yield sites via include_reasoning.
        filt = ReasoningFilter(include_reasoning=True)
        tparser = ToolCallParser()  # always active: strips tool tags even when tools_enabled=False
        if retry_class == "quota":
            identity_retire(xff, "quota")
            xff = identity_acquire(force_new=True)
        retry_class = None
        h = dict(BROWSER_STREAM_HEADERS)
        h["X-Forwarded-For"] = xff
        h["X-Real-IP"] = xff
        _cookie = COOKIE_JAR.get_header()
        if _cookie:
            h["Cookie"] = _cookie
        else:
            _kick_warm_singleflight()
        _id_gen = COOKIE_JAR.snapshot_generation()
        _slot_wait = min(_UPSTREAM_QUEUE_WAIT, max(0.05, _deadline - time.monotonic() - 0.5))
        _slot_held = False
        if not _upstream_sema.acquire(timeout=max(0.05, _slot_wait)):
            yield ("error", {"error": "upstream temporarily unavailable; retry shortly"})
            return
        _slot_held = True
        conn = _pool_get()
        volatile = False
        got_done = False  # initialize before try so finally can reference it safely
        try:
            # Bound connect+headers by remaining deadline (never exceed budget).
            _remaining = _deadline - time.monotonic()
            if _remaining <= 0.5:
                if not content_yielded:
                    yield ("error", {"error": "upstream model busy/stalled (deadline); retry shortly"})
                return
            _to = max(0.2, min(float(_CONN_CONNECT_TIMEOUT), _remaining))
            try:
                if conn.sock is not None:
                    conn.sock.settimeout(_to)
                else:
                    conn.timeout = _to
            except Exception:
                conn.timeout = _to
            conn.request("POST", "/api/chat/stream", body, h)
            resp = conn.getresponse()
            status = resp.status
            try:
                COOKIE_JAR.update_from_response(resp, generation=_id_gen)
            except Exception:
                pass
            # After headers, allow long first-byte wait (thinking + pings).
            try:
                if conn.sock is not None:
                    conn.sock.settimeout(_FIRST_BYTE_TIMEOUT)
                if resp.fp is not None:
                    resp.fp.settimeout(_FIRST_BYTE_TIMEOUT)
            except Exception:
                pass
            if status == 429 or status in (502, 503, 504):
                volatile = True
                err_body = resp.read(2048).decode("utf-8", "ignore")[:300]
                if any(k in err_body for k in ("额度", "2次", "登录", "游客", "套餐", "频繁")):
                    retry_class = "quota"
                    LOG.info("[quota] http=%s attempt=%d/%d", status, attempt, max_retry)
                else:
                    retry_class = "busy"
                    LOG.info("[busy] http=%s attempt=%d/%d", status, attempt, max_retry)
                if attempt >= max_retry:
                    yield ("error", {"error": "upstream temporarily unavailable after %d retries; retry shortly" % attempt})
                    return
                if locals().get("_slot_held"):
                    try:
                        _upstream_sema.release()
                    except Exception:
                        pass
                    _slot_held = False
                _remain = _deadline - time.monotonic()
                if _remain <= 1.0:
                    yield ("error", {"error": "upstream temporarily unavailable; retry shortly"})
                    return
                if retry_class == "quota":
                    _sleep = min(0.25, max(0.05, 0.08 * attempt)) + random.uniform(0, 0.08)
                else:
                    _base = (0.5, 1.0, 2.0, 4.0, 4.0, 4.0)
                    _sleep = min(_base[min(attempt, len(_base)) - 1] + random.uniform(0.0, 0.3), max(0.2, _remain - 0.5))
                LOG.info("[retry %d/%d class=%s] sleep=%.2fs", attempt, max_retry, retry_class, _sleep)
                time.sleep(_sleep)
                continue
            elif status not in (200, 201):
                err = resp.read(2048).decode("utf-8", "ignore")[:300]
                yield ("error", {"status": status, "error": err})
                return
            buf = b""
            got_done = False
            last_data_time = time.monotonic()
            saw_data_event = False  # True after first data: event (not SSE ping comments)
            while True:
                # After any content/reasoning has been yielded, never hard-kill on
                # wall-clock deadline — long thinking is legitimate. Stall-only.
                if not content_yielded:
                    _remain = _deadline - time.monotonic()
                    if _remain <= 0.2:
                        LOG.warning("[deadline %d/%d] abort pre-content model=%s", attempt, max_retry, disp)
                        volatile = True
                        break
                else:
                    _remain = 1e9  # no wall budget once streaming
                # Before first real data event, allow longer wait (thinking + pings).
                _limit = float(_STALL_TIMEOUT if saw_data_event else _FIRST_BYTE_TIMEOUT)
                _chunk_to = max(0.5, min(_limit, _remain if not content_yielded else _limit))
                try:
                    if conn.sock is not None:
                        conn.sock.settimeout(_chunk_to)
                    if getattr(resp, "fp", None) is not None:
                        try:
                            resp.fp.settimeout(_chunk_to)
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    chunk = resp.read1(8192)
                except (socket.timeout, TimeoutError):
                    elapsed = time.monotonic() - last_data_time
                    LOG.warning("[stall %d/%d] no data for %.0fs (to=%.1f saw_data=%s), abort",
                                attempt, max_retry, elapsed, _chunk_to, saw_data_event)
                    volatile = True
                    retry_class = "stall"
                    break
                except OSError as _re:
                    _errno = getattr(_re, "errno", None)
                    _etxt = str(_re).lower()
                    if _errno in (getattr(__import__("errno"), "ETIMEDOUT", 110), 10060) or "timed out" in _etxt or "timeout" in _etxt:
                        elapsed = time.monotonic() - last_data_time
                        LOG.warning("[stall %d/%d] no data for %.0fs (to=%.1f saw_data=%s), abort",
                                    attempt, max_retry, elapsed, _chunk_to, saw_data_event)
                        volatile = True
                        retry_class = "stall"
                        break
                    raise
                if not chunk:
                    break
                # Any socket bytes (including ": ping") count as liveness.
                last_data_time = time.monotonic()
                buf += chunk
                if len(buf) > 4 * 1024 * 1024:
                    LOG.warning("[stream] oversized SSE buffer (%d bytes), aborting exchange", len(buf))
                    buf = b""
                    volatile = True
                    break
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    # SSE comment keepalive from upstream: ": ping <ts>"
                    if not line or line.startswith(b":"):
                        continue
                    if not line.startswith(b"data:"):
                        continue
                    saw_data_event = True
                    payload_s = line[5:].strip()
                    if not payload_s:
                        continue
                    try:
                        obj = json.loads(payload_s)
                    except Exception:
                        continue
                    if obj.get("error"):
                        err = obj["error"]
                        etxt = str(err)
                        if any(k in etxt for k in ("额度", "2次", "登录", "游客", "套餐", "频繁")):
                            LOG.info("[quota] attempt=%d/%d", attempt, max_retry)
                            volatile = True
                            retry_class = "quota"
                            break
                        if any(k in etxt for k in ("繁忙", "服务提供商", "provider", "暂无可用", "稍后")):
                            LOG.info("[busy] attempt=%d/%d", attempt, max_retry)
                            volatile = True
                            retry_class = "busy"
                            break
                        yield ("error", {"error": "upstream temporarily unavailable; retry shortly"})
                        return
                    ct = obj.get("content")
                    if ct is not None and ct != "":
                        for kind, piece in filt.feed(ct):
                            # Reasoning MUST also go through ToolCallParser: models often
                            # emit DSML/tool_calls inside <think> (despite prompt). Without
                            # this sieve, raw |DSML|/tool_calls leak as thinking text and
                            # tool_call is missed → false escalate + double upstream RTT.
                            if kind == "reasoning":
                                if tparser is not None:
                                    for tk, tp in tparser.feed(piece):
                                        if tk == "tool_call":
                                            content_yielded = True
                                            yield (tk, tp)
                                        elif tk == "content" and include_reasoning and tp:
                                            content_yielded = True
                                            yield ("reasoning", tp)
                                elif include_reasoning and piece:
                                    content_yielded = True
                                    yield ("reasoning", piece)
                            elif kind == "content":
                                if tparser is not None:
                                    for tk, tp in tparser.feed(piece):
                                        content_yielded = True
                                        yield (tk, tp)
                                else:
                                    content_yielded = True
                                    yield ("content", piece)
                    if obj.get("sources") is not None and not sources_sent:
                        sources_sent = True
                        yield ("sources", obj.get("sources"))
                    if obj.get("done"):
                        got_done = True
            # stream ended — always flush remaining buffers
            for kind, piece in filt.flush():
                if kind == "reasoning":
                    if tparser is not None:
                        for tk, tp in tparser.feed(piece):
                            if tk == "tool_call":
                                content_yielded = True
                                yield (tk, tp)
                            elif tk == "content" and include_reasoning and tp:
                                content_yielded = True
                                yield ("reasoning", tp)
                    elif include_reasoning and piece:
                        content_yielded = True
                        yield ("reasoning", piece)
                elif kind == "content":
                    if tparser is not None:
                        for tk, tp in tparser.feed(piece):
                            content_yielded = True
                            yield (tk, tp)
                    else:
                        content_yielded = True
                        yield ("content", piece)
            if tparser is not None:
                for tk, tp in tparser.flush():
                    content_yielded = True
                    # flush may surface stripped prose that originated inside <think>;
                    # only promote leftover text as content (tool_call always forwarded).
                    if tk == "tool_call":
                        yield (tk, tp)
                    elif tk == "content" and tp:
                        yield (tk, tp)
            if got_done and not volatile:
                identity_mark_ok(xff)
                return
            if not got_done and not volatile and not content_yielded:
                # stream cut without done — likely connection error, retry only if no content sent
                if attempt < max_retry:
                    _remain = _deadline - time.monotonic()
                    if _remain <= 1.0:
                        yield ("error", {"error": "upstream stream cut (deadline); retry shortly"})
                        return
                    _sleep = min(4.0, max(0.2, min(1.5 ** attempt, _remain - 0.5))) + random.uniform(0, 0.3)
                    LOG.warning("[nodone %d/%d] stream cut, retry in %.1fs", attempt, max_retry, _sleep)
                    time.sleep(_sleep)
                    continue
            if volatile and not got_done and not content_yielded and attempt < max_retry:
                if locals().get("_slot_held"):
                    try:
                        _upstream_sema.release()
                    except Exception:
                        pass
                    _slot_held = False
                _remain = _deadline - time.monotonic()
                if _remain <= 1.0:
                    yield ("error", {"error": "upstream temporarily unavailable; retry shortly"})
                    return
                rc = retry_class or "busy"
                if rc == "quota":
                    _sleep = min(0.25, max(0.05, 0.08 * attempt)) + random.uniform(0, 0.08)
                elif rc == "stall":
                    _base = (0.3, 0.6, 1.2, 2.0, 3.0, 3.0)
                    _sleep = min(_base[min(attempt, len(_base)) - 1] + random.uniform(0, 0.3), max(0.2, _remain - 0.5))
                else:
                    _base = (0.5, 1.0, 2.0, 4.0, 4.0, 4.0)
                    _sleep = min(_base[min(attempt, len(_base)) - 1] + random.uniform(0, 0.3), max(0.2, _remain - 0.5))
                LOG.info("[retry %d/%d class=%s] sleep=%.2fs", attempt, max_retry, rc, _sleep)
                time.sleep(_sleep)
                continue
            if volatile and (attempt >= max_retry or (not content_yielded and time.monotonic() >= _deadline)):
                if not content_yielded:
                    # Generic client message — never leak 额度/登录 Chinese copy
                    yield ("error", {"error": "upstream temporarily unavailable after %d retries; retry shortly" % attempt})
            return
        except Exception as e:
            if attempt < max_retry:
                _remain = _deadline - time.monotonic()
                if _remain <= 1.0:
                    yield ("error", {"error": "upstream failed (deadline): %r" % e})
                    return
                LOG.warning("[connerr %d/%d %r] retry", attempt, max_retry, e)
                time.sleep(min(4.0, max(0.2, min(1.0 * attempt, _remain - 0.5))))
                continue
            yield ("error", {"error": "upstream failed: %r" % e})
            return
        finally:
            # http.client connections are not reliably reusable after SSE streams;
            # always close to avoid hung sockets under concurrency.
            try:
                conn.close()
            except Exception:
                pass
            if locals().get("_slot_held"):
                try:
                    _upstream_sema.release()
                except Exception:
                    pass
                _slot_held = False
            # Recycle connection only if stream ended cleanly (got_done).
            # After a stall/volatile abort, the socket may be in an inconsistent
            # state (half-read chunk buffer), so close instead of recycling.
            if got_done and not volatile:
                try:
                    _pool_put(conn)
                except Exception:
                    pass


def classify_error(err, default=529):
    """Classify an upstream error string into (http_code, error_type) for both
    OpenAI and Anthropic error envelopes."""
    etxt = str(err)
    low = etxt.lower()
    if any(k in low for k in ("too long", "context length", "token limit",
                              "maximum context", "input too long", "exceeds the max")):
        return 400, "invalid_request_error"
    if any(k in low for k in ("rate limit", "rate_limit", "too many request",
                              "quota", "429")):
        return 429, "rate_limit_error"
    if any(k in low for k in ("overloaded", "busy", "529", "service unavailable",
                              "503", "service provider", "provider", "no available")):
        return 529, "overloaded_error"
    if any(k in low for k in ("bad request", "invalid_request", "400", "bad json")):
        return 400, "invalid_request_error"
    if any(k in low for k in ("not found", "404", "model not")):
        return 404, "not_found_error"
    if any(k in low for k in ("unauthorized", "auth", "401", "forbidden", "403")):
        return default, "api_error"
    return default, "api_error"


# ── Error envelope builder ──────────────────────────────────────────────────
# /v1/messages uses Anthropic format; /v1/responses and /v1/chat/completions
# use OpenAI format (so openai-python / Cursor parse error.type / error.code).
_OAI_ERROR_TYPE = {
    "rate_limit_error": "rate_limit_exceeded",
    "invalid_request_error": "invalid_request_error",
    "overloaded_error": "server_error",
    "api_error": "server_error",
    "not_found_error": "invalid_request_error",
}

def _err_body(etype, message, flavor="openai"):
    """Build error response body in the correct envelope for the endpoint.

    flavor="anthropic" → {"type":"error","error":{"type":...,"message":...}}
    flavor="openai"    → {"error":{"message":...,"type":...,"code":...}}
    """
    if flavor == "anthropic":
        return {"type": "error", "error": {"type": etype, "message": message}}
    return {"error": {
        "message": message,
        "type": _OAI_ERROR_TYPE.get(etype, "invalid_request_error"),
        "code": etype,
    }}


def _tool_schemas_map(tools, anthropic=False):
    """Build name -> parameters-schema dict from an OpenAI or Anthropic tool list."""
    schemas = {}
    if not isinstance(tools, list):
        return schemas
    for t in tools:
        if not isinstance(t, dict):
            continue
        if anthropic:
            name = t.get("name")
            schema = t.get("input_schema") or {}
        else:
            fn = t.get("function") or {}
            name = fn.get("name")
            schema = fn.get("parameters") or {}
        if name:
            schemas[name] = schema if isinstance(schema, dict) else {}
    return schemas


def _coerce_value(val, typ):
    """Coerce a parsed value toward the expected JSON-schema type."""
    if val is None or typ is None:
        return val
    if typ == "string":
        return val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
    if typ == "boolean":
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            s = val.strip().lower()
            if s in ("true", "yes", "1"):
                return True
            if s in ("false", "no", "0", ""):
                return False
        return bool(val)
    if typ == "integer":
        if isinstance(val, bool):
            return int(val)
        if isinstance(val, int):
            return val
        if isinstance(val, float):
            return int(val)
        if isinstance(val, str):
            try:
                return int(val.strip())
            except ValueError:
                try:
                    return int(float(val.strip()))
                except ValueError:
                    return val
        return val
    if typ == "number":
        if isinstance(val, bool):
            return float(val)
        if isinstance(val, (int, float)):
            return val
        if isinstance(val, str):
            try:
                return float(val.strip())
            except ValueError:
                return val
        return val
    if typ == "array":
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            s = val.strip()
            if s.startswith("["):
                v, ok = _try_json(s)
                if ok and isinstance(v, list):
                    return v
            if "," in s:
                return [x.strip() for x in s.split(",") if x.strip()]
            return [s] if s else []
        return val if isinstance(val, list) else [val]
    if typ == "object":
        if isinstance(val, dict):
            return val
        if isinstance(val, str):
            s = val.strip()
            if s.startswith("{"):
                v, ok = _try_json(s)
                if ok and isinstance(v, dict):
                    return v
            return val
        return val
    return val


def _coerce_tool_params(args, schema):
    """Coerce args dict values toward the schema's property types, including
    array item types and nested object properties."""
    if not isinstance(args, dict) or not isinstance(schema, dict):
        return args
    props = schema.get("properties") or {}
    for key, val in list(args.items()):
        ps = props.get(key)
        if not isinstance(ps, dict):
            continue
        typ = ps.get("type")
        if isinstance(typ, list):
            typ = next((t for t in typ if t != "null"), None)
        if typ:
            args[key] = _coerce_value(val, typ)
        # coerce array items recursively
        if typ == "array" and isinstance(args[key], list):
            items_schema = ps.get("items") or {}
            item_type = items_schema.get("type")
            if item_type:
                args[key] = [_coerce_value(v, item_type) for v in args[key]]
        # coerce nested object properties
        if typ == "object" and isinstance(args[key], dict):
            args[key] = _coerce_tool_params(args[key], ps)
    return args


def _validate_schema(val, schema, path="", errors=None):
    """Lightweight JSON Schema subset validator (no external deps).
    Supports: type, required, properties, items, enum, additionalProperties,
    nested objects and arrays. Returns list of error strings."""
    if errors is None:
        errors = []
    if not isinstance(schema, dict):
        return errors
    typ = schema.get("type")
    if isinstance(typ, list):
        typ = next((t for t in typ if t != "null"), None)
    if typ:
        if val is None and (typ == "null" or "null" in schema.get("type", [])):
            return errors
        if typ == "string" and not isinstance(val, str):
            errors.append("expected string at %s, got %s" % (path or "(root)", type(val).__name__))
        elif typ == "integer" and not isinstance(val, (int,)) or (isinstance(val, bool)):
            if typ == "integer" and (isinstance(val, bool) or not isinstance(val, int)):
                errors.append("expected integer at %s, got %s" % (path or "(root)", type(val).__name__))
        elif typ == "number" and not isinstance(val, (int, float)) or (isinstance(val, bool)):
            if typ == "number" and (isinstance(val, bool) or not isinstance(val, (int, float))):
                errors.append("expected number at %s, got %s" % (path or "(root)", type(val).__name__))
        elif typ == "boolean" and not isinstance(val, bool):
            errors.append("expected boolean at %s, got %s" % (path or "(root)", type(val).__name__))
        elif typ == "array" and not isinstance(val, list):
            errors.append("expected array at %s, got %s" % (path or "(root)", type(val).__name__))
        elif typ == "object" and not isinstance(val, dict):
            errors.append("expected object at %s, got %s" % (path or "(root)", type(val).__name__))
    enum = schema.get("enum")
    if enum is not None and isinstance(enum, list) and val not in enum:
        errors.append("value %r at %s not in enum %r" % (val, path or "(root)", enum))
    if typ == "object" and isinstance(val, dict):
        props = schema.get("properties") or {}
        req = schema.get("required") or []
        for r in req:
            if r not in val:
                if r in props and "default" in props.get(r, {}):
                    val[r] = props[r]["default"]
                else:
                    errors.append("missing required field: %s.%s" % (path, r) if path else r)
        ap = schema.get("additionalProperties")
        if ap is False:
            for k in val:
                if k not in props:
                    errors.append("unexpected property %s%s.%s" % (path, "." if path else "", k))
        for k, sub in val.items():
            if k in props:
                _validate_schema(sub, props[k], "%s.%s" % (path, k) if path else k, errors)
    if typ == "array" and isinstance(val, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(val):
                _validate_schema(item, items, "%s[%d]" % (path, i), errors)
    return errors


def _validate_and_coerce_tool_calls(tcs_out, tools, anthropic=False):
    """Coerce then validate tool call arguments against the tool JSON schema.
    Prevents 'true'/'123'/container strings from reaching the client as
    wrong-typed values.  Logs validation errors to stderr."""
    schemas = _tool_schemas_map(tools, anthropic=anthropic)
    for tc in tcs_out:
        name = tc.get("name") or tc.get("function", {}).get("name")
        schema = schemas.get(name)
        if not schema:
            continue
        args = tc.get("arguments") or {}
        # step 1: coerce types (incl. array items + nested objects)
        args = _coerce_tool_params(args, schema)
        # step 1b: fill defaults for optional fields that have a default
        props = schema.get("properties") or {}
        for k, ps in props.items():
            if k not in args and isinstance(ps, dict) and "default" in ps:
                args[k] = ps["default"]
        # step 2: validate (required/enum/items/nested)
        errors = _validate_schema(args, schema)
        if errors:
            sys.stderr.write("[tool-validate] name=%s error=%s\n" % (name, "; ".join(errors)));
        # step 3: ensure arguments is a dict (Anthropic input must be object)
        if not isinstance(args, dict):
            args = {} if anthropic else {"_raw": args}
        tc["arguments"] = args
    return tcs_out


def _upstream_iter(first, remainder_iter):
    """Yield caching the first event, then continue from the same upstream generator."""
    if first is not None:
        yield first
    if remainder_iter is not None:
        yield from remainder_iter


def _prefetch_first_upstream(model, msgs_up, include_reasoning, effort, search, tools_enabled, max_retry=5, max_tokens=None):
    """Prefetch the first event from upstream. Returns (first_event, iter, error).
    If the first event is an error, error is set and iter is None.
    Otherwise first_event is the cached first event and iter is a chained generator
    that yields the first event from cache then continues the SAME upstream generator."""
    it = upstream(model, msgs_up, include_reasoning, effort, search, tools_enabled, max_retry=max_retry, max_tokens=max_tokens)
    try:
        first = next(it, None)
    except Exception as e:
        return None, None, str(e)
    if first is None:
        return None, None, None  # upstream returned nothing
    kind, data = first
    if kind == "error":
        return None, None, data.get("error") if isinstance(data, dict) else str(data)
    return first, it, None  # it still has remaining events; first is cached

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, fmt, *args):
        LOG.debug("http %s %s", self.address_string(), fmt % args)
    def log_error(self, fmt, *args):
        LOG.warning("http %s %s", self.address_string(), fmt % args)
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "*")
    def _send(self, code, obj, ctype="application/json", extra=None):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        if extra:
            for k, v in extra.items():
                self.send_header(k, str(v))
        self._cors()
        self.end_headers()
        self.wfile.write(b)

    # ── SSE framing ────────────────────────────────────────────────────────
    # An SSE body sent with "Connection: close" and no Content-Length is
    # delimited ONLY by the TCP FIN. Any intermediary (Cloudflare Tunnel,
    # nginx, a NewAPI relay) that drops the connection mid-stream produces a
    # body the client cannot tell apart from a complete one, and Rust/hyper
    # based clients surface that as "error decoding response body".
    # Chunked encoding makes completion explicit: the terminating 0-length
    # chunk is the only legitimate end, so a truncated relay becomes a
    # detectable error instead of a silently short message.
    # Chunked is HTTP/1.1-only; a 1.0 client would read the hex size lines as
    # body text, so fall back to close-delimited framing for those.
    def _sse_begin(self, code=200, extra_headers=None):
        self._sse_chunked = self.request_version >= "HTTP/1.1"
        self.send_response(code)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")  # stop nginx/CF from buffering
        if self._sse_chunked:
            self.send_header("Transfer-Encoding", "chunked")
        else:
            self.send_header("Connection", "close")
            self.close_connection = True
        self._cors()
        for k, v in (extra_headers or {}).items():
            self.send_header(k, str(v))
        self.end_headers()
        self._sse_done = False

    def _sse_chunk(self, b):
        if not b:
            return
        if getattr(self, "_sse_chunked", False):
            # One write, not three: a partial chunk (size line written, payload
            # not) both corrupts the framing and hides the disconnect, because
            # the small size-line write can land in the socket buffer and defer
            # the error past the point where a heartbeat thread would notice.
            self.wfile.write(b"%X\r\n" % len(b) + b + b"\r\n")
        else:
            self.wfile.write(b)
        self.wfile.flush()

    def _sse_end(self):
        if getattr(self, "_sse_done", False):
            return
        self._sse_done = True
        if not getattr(self, "_sse_chunked", False):
            return
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception:
            pass
    def _handle_responses(self):
        """OpenAI Responses API bridge for coding clients.

        Convert Responses input/tools into the same internal chat+DSML path used
        by /v1/chat/completions, then render a Responses-compatible response.
        This keeps Codex/Claude-Code-style clients from failing on 501."""
        if RATE and not RATE.acquire(timeout=0):
            return self._send(429, _err_body("rate_limit_error", "rate limit: too many requests, try again shortly"), extra={"Retry-After": "5"})
        if COMPANION_PROB and random.random() < COMPANION_PROB:
            threading.Thread(target=background_companion_refresh, daemon=True).start()
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except (ValueError, TypeError):
            length = 0
        if length > _MAX_BODY_BYTES:
            return self._send(413, _err_body("invalid_request_error", "request body too large (max %d MB)" % (_MAX_BODY_BYTES // 1024 // 1024)))
        try:
            raw = self.rfile.read(length) if length else b"{}"
            req = json.loads(raw.decode("utf-8", "ignore"))
        except Exception as e:
            return self._send(400, _err_body("invalid_request_error", "bad json: %s" % e))
        if not isinstance(req, dict):
            return self._send(400, _err_body("invalid_request_error", "request body must be a JSON object"))

        model = req.get("model") or DEFAULT_MODEL
        grp, sub, disp = resolve_model(model)
        stream = bool(req.get("stream"))
        resp_id = "resp_%d" % int(time.time() * 1000)
        created = int(time.time())

        def _resp_part_text(parts):
            if isinstance(parts, str):
                return parts
            if isinstance(parts, list):
                out = []
                for p in parts:
                    if isinstance(p, str):
                        out.append(p)
                    elif isinstance(p, dict):
                        t = p.get("text") or p.get("input_text") or p.get("output_text")
                        if isinstance(t, str):
                            out.append(t)
                return "\n".join(x for x in out if x)
            if parts is None:
                return ""
            return json.dumps(parts, ensure_ascii=False)

        def _resp_input_to_messages(inp, instructions):
            msgs = []
            if instructions:
                msgs.append({"role": "system", "content": _resp_part_text(instructions)})
            if isinstance(inp, str):
                msgs.append({"role": "user", "content": inp})
                return msgs
            if not isinstance(inp, list):
                msgs.append({"role": "user", "content": _resp_part_text(inp)})
                return msgs
            for item in inp:
                if not isinstance(item, dict):
                    msgs.append({"role": "user", "content": _resp_part_text(item)})
                    continue
                typ = item.get("type")
                role = item.get("role")
                if typ == "message" or role in ("system", "user", "assistant", "developer"):
                    r = role or "user"
                    if r == "developer":
                        r = "system"
                    content = _resp_part_text(item.get("content"))
                    if content or r != "assistant":
                        msgs.append({"role": r, "content": content})
                elif typ == "function_call":
                    name = item.get("name") or "tool"
                    args = item.get("arguments") or "{}"
                    cid = item.get("call_id") or item.get("id") or _make_tool_id()
                    msgs.append({"role": "assistant", "content": None, "tool_calls": [{
                        "id": cid, "type": "function",
                        "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)}
                    }]})
                elif typ == "function_call_output":
                    msgs.append({"role": "tool", "tool_call_id": item.get("call_id") or item.get("id") or "", "content": _resp_part_text(item.get("output"))})
                else:
                    # unknown item type: log and skip (don't crash)
                    sys.stderr.write("[responses] skipping unknown input item type=%r\n" % typ);
                    txt = _resp_part_text(item.get("content") if "content" in item else item)
                    if txt:
                        msgs.append({"role": "user", "content": txt})
            return msgs

        def _resp_tools_to_openai(tools):
            out = []
            if not isinstance(tools, list):
                return out
            for t in tools:
                if not isinstance(t, dict):
                    continue
                if t.get("type") == "function":
                    if isinstance(t.get("function"), dict):
                        f = t["function"]
                        name = f.get("name")
                        params = f.get("parameters", {}) or {}
                        desc = f.get("description", "")
                    else:
                        name = t.get("name")
                        params = t.get("parameters", {}) or {}
                        desc = t.get("description", "")
                    if name:
                        out.append({"type": "function", "function": {"name": name, "description": desc, "parameters": params}})
            return out

        msgs = _resp_input_to_messages(req.get("input"), req.get("instructions"))
        tools = _resp_tools_to_openai(req.get("tools") or [])
        tool_choice = req.get("tool_choice")
        if isinstance(tool_choice, dict) and tool_choice.get("type") == "function" and not tool_choice.get("function"):
            tool_choice = {"type": "function", "function": {"name": tool_choice.get("name", "")}}
        if tool_choice is None and tools:
            tool_choice = "auto"
        msgs_up, tools_enabled = _build_messages_with_tools(tools, tool_choice, msgs)
        include_reasoning = not (req.get("reasoning") is False or req.get("strip_reasoning"))
        effort = req.get("reasoning_effort") or req.get("reasoningEffort") or "max"
        if isinstance(req.get("reasoning"), dict) and req["reasoning"].get("effort"):
            effort = req["reasoning"].get("effort")
        if str(effort).lower() not in ("off", "low", "medium", "high", "max"):
            effort = "max"
        search = bool(req.get("search") or req.get("web_search") or req.get("websearch"))
        _rid = _uuid.uuid4().hex[:8]
        max_tokens = req.get("max_tokens") if "max_tokens" in req else (req.get("max_output_tokens") if "max_output_tokens" in req else 8192)
        max_tokens = max_tokens or 8192  # only fallback for None/0
        max_tokens = _clamp_max_tokens(disp, max_tokens)
        _pf = _context_limit(disp, max_tokens)
        # Inject synthetic "messages" key BEFORE estimation so token count is accurate
        _is_responses_fmt = "messages" not in req and ("input" in req or "instructions" in req)
        if _is_responses_fmt:
            req["messages"] = msgs
        _inp_toks = _estimate_request_tokens(req, model=disp)
        _compact_meta = {}
        if _inp_toks > _pf:
            LOG.warning("rid=%s [responses] over budget est=%d limit=%d model=%s", _rid, _inp_toks, _pf, disp)
        if _preflight_should_compact(_inp_toks, _pf, model=disp):
            _eff = _est_for_preflight(_inp_toks, disp)
            _cb = _preflight_compact_budget(_inp_toks, _pf, disp)
            LOG.warning("rid=%s [responses] compacting (est=%d eff=%d limit=%d ratio=%.2f trigger=%.2f budget=%d)",
                        _rid, _inp_toks, _eff, _pf, _eff / max(1, _pf), _COMPACT_TRIGGER, _cb)
            req, _cprep, _compact_meta = compact_request(req, _cb, model=disp)
            LOG.info("rid=%s compacted %s", _rid, _cprep)
            # Use compacted messages directly
            msgs = req.get("messages") or []
            msgs_up, tools_enabled = _build_messages_with_tools(tools, tool_choice, msgs)
        if _inp_toks <= _pf:
            LOG.info("rid=%s [responses] stream=%s model=%s ninput=%s ntools=%s tool_choice=%s est=%d limit=%d",
                     _rid, stream, model, len(req.get("input") or []) if isinstance(req.get("input"), list) else 1, len(tools), tool_choice, _inp_toks, _pf)

        if not stream:
            answer, reason, tcs_out, err = [], [], [], None
            for kind, data in upstream(model, msgs_up, include_reasoning, str(effort).lower(), search, tools_enabled, max_retry=5, max_tokens=max_tokens):
                if kind == "error":
                    err = data.get("error") if isinstance(data, dict) else str(data)
                    break
                if kind == "content":
                    answer.append(data)
                elif kind == "reasoning":
                    reason.append(data)
                elif kind == "tool_call":
                    tcs_out.append(data)
            # Retry with compaction on "too long" upstream error
            if err and _COMPACT_ENABLED and (tl := _parse_too_long(str(err))):
                target = _compact_budget(tl, disp, _inp_toks, max_tokens)
                LOG.warning("rid=%s [responses] upstream rejected — retrying compact target=%d", _rid, target)
                req, _cprep, _compact_meta = compact_request(req, target, model=disp)
                LOG.info("rid=%s retry compacted %s", _rid, _cprep)
                msgs = req.get("messages") or []
                msgs_up, tools_enabled = _build_messages_with_tools(tools, tool_choice, msgs)
                answer, reason, tcs_out, err = [], [], [], None
                for kind, data in upstream(model, msgs_up, include_reasoning, str(effort).lower(), search, tools_enabled, max_retry=3, max_tokens=max_tokens):
                    if kind == "error":
                        err = data.get("error") if isinstance(data, dict) else str(data)
                        break
                    if kind == "content":
                        answer.append(data)
                    elif kind == "reasoning":
                        reason.append(data)
                    elif kind == "tool_call":
                        tcs_out.append(data)
            if err:
                _code, _type = classify_error(err)
                return self._send(_code, _err_body(_type, err, flavor="anthropic"))
            if tools_enabled and tcs_out:
                tcs_out = _validate_and_coerce_tool_calls(tcs_out, tools)
            output = []
            content_text = "".join(answer)
            if content_text:
                output.append({"id": "msg_%d" % int(time.time() * 1000), "type": "message", "status": "completed", "role": "assistant",
                               "content": [{"type": "output_text", "text": content_text, "annotations": []}]})
            for c in tcs_out:
                output.append({"id": c["id"], "type": "function_call", "status": "completed", "call_id": c["id"], "name": c["name"],
                               "arguments": json.dumps(c["arguments"], ensure_ascii=False)})
            p_toks = _estimate_messages_tokens(msgs_up)
            o_toks = _estimate_tokens(content_text + "".join(reason))
            _ewma_update(disp, p_toks, _inp_toks)
            return self._send(200, {
                "id": resp_id, "object": "response", "created_at": created, "status": "completed", "model": disp,
                "output": output, "parallel_tool_calls": True, "error": None,
                "usage": {"input_tokens": p_toks, "output_tokens": o_toks, "total_tokens": p_toks + o_toks},
            }, extra=_compact_headers(_compact_meta) or None)

        # Open SSE immediately, then stream upstream directly so the client
        # Prefetch first event BEFORE writing SSE header to client.
        _first, _iter, _err = _prefetch_first_upstream(model, msgs_up, include_reasoning, str(effort).lower(), search, tools_enabled, max_tokens=max_tokens)
        # Retry with compaction on "too long" upstream error
        if _err and _COMPACT_ENABLED and (tl := _parse_too_long(str(_err))):
            target = _compact_budget(tl, disp, _inp_toks, max_tokens)
            LOG.warning("rid=%s [responses] stream upstream rejected — retrying compact target=%d", _rid, target)
            req2, _cprep2, _compact_meta = compact_request(req, target, model=disp)
            LOG.info("rid=%s retry compacted %s", _rid, _cprep2)
            msgs2 = req2.get("messages") or []
            msgs_up2, tools_enabled2 = _build_messages_with_tools(tools, tool_choice, msgs2)
            _first, _iter, _err = _prefetch_first_upstream(model, msgs_up2, include_reasoning, str(effort).lower(), search, tools_enabled2, max_retry=3, max_tokens=max_tokens)
        if _err:
            _code, _type = classify_error(_err)
            _extra = {"Retry-After": "5"} if _code in (429, 529) else None
            return self._send(_code, _err_body(_type, str(_err), flavor="anthropic"), extra=_extra)
        # Now safe to write SSE header
        self._sse_begin(200, _compact_headers(_compact_meta))
        lock = threading.Lock()
        _hb_stop = {"v": False}
        _hb_started = threading.Event()
        def emit_event(ev, data):
            if isinstance(data, dict) and data.get("type") is None:
                data = {"type": ev, **data}
            with lock:
                self._sse_chunk(("event: %s\ndata: %s\n\n" % (ev, json.dumps(data, ensure_ascii=False))).encode("utf-8"))
        def _hb():
            # Keepalive comment frames: without traffic, Cloudflare Tunnel and
            # most reverse proxies drop an idle connection well before a slow
            # upstream produces its first token, which the client then reports
            # as a network failure and retries with a long backoff.
            _hb_started.wait()
            while not _hb_stop["v"]:
                try:
                    with lock:
                        self._sse_chunk(b": keepalive\n\n")
                except Exception:
                    return
                time.sleep(1.0)
        _hbt = threading.Thread(target=_hb, daemon=True)
        _hbt.start()
        try:
            emit_event("response.created", {"response": {"id": resp_id, "object": "response", "created_at": created, "status": "in_progress", "model": disp, "output": []}})
            _hb_started.set()  # keepalive only after the first real event
            msg_item = None
            text_index = None
            out_index = 0
            tool_index = 0
            for kind, data in _upstream_iter(_first, _iter):
                if kind == "content":
                    if msg_item is None:
                        msg_item = {"id": "msg_%d" % int(time.time() * 1000), "type": "message", "status": "in_progress", "role": "assistant", "content": []}
                        emit_event("response.output_item.added", {"output_index": out_index, "item": msg_item})
                        text_index = 0
                        emit_event("response.content_part.added", {"item_id": msg_item["id"], "output_index": out_index, "content_index": text_index, "part": {"type": "output_text", "text": "", "annotations": []}})
                    emit_event("response.output_text.delta", {"item_id": msg_item["id"], "output_index": out_index, "content_index": text_index, "delta": data})
                elif kind == "tool_call":
                    if not tools_enabled:
                        continue
                    data = _validate_and_coerce_tool_calls([data], tools)[0]
                    if msg_item is not None:
                        emit_event("response.output_text.done", {"item_id": msg_item["id"], "output_index": out_index, "content_index": text_index, "text": ""})
                        emit_event("response.content_part.done", {"item_id": msg_item["id"], "output_index": out_index, "content_index": text_index, "part": {"type": "output_text", "text": "", "annotations": []}})
                        msg_item["status"] = "completed"
                        emit_event("response.output_item.done", {"output_index": out_index, "item": msg_item})
                        out_index += 1
                        msg_item = None
                    item = {"id": data["id"], "type": "function_call", "status": "completed", "call_id": data["id"], "name": data["name"],
                            "arguments": json.dumps(data["arguments"], ensure_ascii=False)}
                    emit_event("response.output_item.added", {"output_index": out_index + tool_index, "item": item})
                    emit_event("response.function_call_arguments.done", {"item_id": data["id"], "output_index": out_index + tool_index, "arguments": item["arguments"]})
                    emit_event("response.output_item.done", {"output_index": out_index + tool_index, "item": item})
                    tool_index += 1
                elif kind == "error":
                    emit_event("response.failed", {"response": {"id": resp_id, "object": "response", "created_at": created, "status": "failed", "model": disp, "error": {"type": "api_error", "message": data.get("error") if isinstance(data, dict) else str(data)}}})
                    return
            if msg_item is not None:
                emit_event("response.output_text.done", {"item_id": msg_item["id"], "output_index": out_index, "content_index": text_index, "text": ""})
                emit_event("response.content_part.done", {"item_id": msg_item["id"], "output_index": out_index, "content_index": text_index, "part": {"type": "output_text", "text": "", "annotations": []}})
                msg_item["status"] = "completed"
                emit_event("response.output_item.done", {"output_index": out_index, "item": msg_item})
            emit_event("response.completed", {"response": {"id": resp_id, "object": "response", "created_at": created, "status": "completed", "model": disp, "output": []}})
        except Exception as e:
            try:
                emit_event("response.failed", {"response": {"id": resp_id, "object": "response", "created_at": created, "status": "failed", "model": disp, "error": {"type": "api_error", "message": "server: %s" % e}}})
            except Exception:
                pass
        finally:
            _hb_stop["v"] = True
            _hb_started.set()
            _hbt.join(1.0)
            with lock:
                self._sse_end()
    def _handle_messages(self):
        """Native Anthropic /v1/messages endpoint: Claude Code / Codex connect
        directly, no external converter needed. Anthropic request -> private
        upstream -> Anthropic Messages streaming / non-streaming response."""
        if RATE and not RATE.acquire(timeout=0):
            return self._send(429, _err_body("rate_limit_error", "rate limit: too many requests, try again shortly", flavor="anthropic"), extra={"Retry-After": "5"})
        if COMPANION_PROB and random.random() < COMPANION_PROB:
            threading.Thread(target=background_companion_refresh, daemon=True).start()
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except (ValueError, TypeError):
            length = 0
        if length > _MAX_BODY_BYTES:
            return self._send(413, _err_body("invalid_request_error", "request body too large (max %d MB)" % (_MAX_BODY_BYTES // 1024 // 1024)))
        try:
            raw = self.rfile.read(length) if length else b"{}"
            req = json.loads(raw.decode("utf-8", "ignore"))
        except Exception as e:
            return self._send(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "bad json: %s" % e}})
        model = req.get("model") or DEFAULT_MODEL
        grp, sub, disp = resolve_model(model)
        msg_id = _make_msg_id()
        _t0 = time.monotonic()
        _msgs_count = len(req.get("messages") or [])
        _tools_count = len(req.get("tools") or [])
        _stream = bool(req.get("stream"))
        # messages & system
        anth_messages = req.get("messages") or []
        sysc = req.get("system")
        if isinstance(sysc, list):
            sysc = " ".join(b.get("text", "") for b in sysc if isinstance(b, dict) and b.get("type") == "text")
        elif sysc is None:
            sysc = ""
        # tools
        anth_tools = req.get("tools") or []
        tool_choice = _anthropic_tool_choice(req.get("tool_choice"))
        openai_tools = _anthropic_tools_to_openai(anth_tools)
        if tool_choice is None and openai_tools:
            tool_choice = "auto"
        openai_msgs = _flatten_anthropic_messages(anth_messages, sysc)
        # Latest user explicitly forbids tools on an auto turn -> disable tools for this request.
        if openai_tools and _is_auto_tool_choice(tool_choice) and _user_forbids_tools(openai_msgs):
            tool_choice = "none"
        msgs_up, tools_enabled = _build_messages_with_tools(openai_tools, tool_choice, openai_msgs)
        max_tokens = _clamp_max_tokens(disp, req.get("max_tokens") or 4096)
        _rid = _uuid.uuid4().hex[:8]
        _pf = _context_limit(disp, max_tokens)
        _inp_toks = _estimate_request_tokens(req, model=disp)
        _compact_meta = {}
        if _inp_toks > _pf:
            LOG.warning("rid=%s over budget est=%d limit=%d model=%s", _rid, _inp_toks, _pf, disp)
        if _preflight_should_compact(_inp_toks, _pf, model=disp):
            _eff = _est_for_preflight(_inp_toks, disp)
            _cb = _preflight_compact_budget(_inp_toks, _pf, disp)
            LOG.warning("rid=%s compacting (est=%d eff=%d limit=%d ratio=%.2f trigger=%.2f budget=%d)",
                        _rid, _inp_toks, _eff, _pf, _eff / max(1, _pf), _COMPACT_TRIGGER, _cb)
            req, _cprep, _compact_meta = compact_request(req, _cb, model=disp)
            LOG.info("rid=%s compacted %s", _rid, _cprep)
            # rebuild from compacted request
            anth_messages = req.get("messages") or []
            sysc = req.get("system")
            if isinstance(sysc, list):
                sysc = " ".join(b.get("text", "") for b in sysc if isinstance(b, dict) and b.get("type") == "text")
            elif sysc is None:
                sysc = ""
            openai_msgs = _flatten_anthropic_messages(anth_messages, sysc)
            msgs_up, tools_enabled = _build_messages_with_tools(openai_tools, tool_choice, openai_msgs)
        else:
            LOG.info("rid=%s -> %s %s msgs=%d tools=%d est=%d limit=%d max_tokens=%s stream=%s",
                     _rid, self.path, disp, _msgs_count, _tools_count, _inp_toks, _pf, max_tokens, _stream)
        stream = bool(req.get("stream"))
        # thinking: anthropic 'thinking' param; we pass medium by default unless
        # client sent a budget — keep reasoning on for claude (upstream always thinks).
        thinking_cfg = req.get("thinking")
        include_reasoning = True
        effort = "max"
        if isinstance(thinking_cfg, dict):
            if thinking_cfg.get("type") == "disabled":
                # Upstream always thinks regardless of disabled — keep reasoning
                # on so the client receives it instead of burning tokens for nothing.
                pass
        # CC sometimes sends reasoning_effort via extension; honor it too.
        eff_in = req.get("reasoning_effort") or req.get("reasoningEffort")
        if eff_in and str(eff_in).lower() in ("off", "low", "medium", "high", "max"):
            effort = str(eff_in).lower()
        search = bool(req.get("search") or req.get("web_search") or req.get("websearch"))
        max_tokens = _clamp_max_tokens(disp, req.get("max_tokens") or 4096)
        if not stream:
            answer, reason, tcs_out, err, _sources = _consume_upstream(
                model, msgs_up, include_reasoning, effort, search, tools_enabled, max_retry=5, max_tokens=max_tokens)
            # Retry with compaction on "too long" upstream error
            if err and _COMPACT_ENABLED and (tl := _parse_too_long(str(err))):
                target = _compact_budget(tl, disp, _inp_toks, max_tokens)
                LOG.warning("rid=%s upstream rejected — retrying with compaction target=%d", _rid, target)
                req, _cprep, _compact_meta = compact_request(req, target, model=disp)
                LOG.info("rid=%s retry compacted %s", _rid, _cprep)
                anth_messages = req.get("messages") or []
                sysc = req.get("system")
                if isinstance(sysc, list):
                    sysc = " ".join(b.get("text", "") for b in sysc if isinstance(b, dict) and b.get("type") == "text")
                elif sysc is None:
                    sysc = ""
                openai_msgs = _flatten_anthropic_messages(anth_messages, sysc)
                msgs_up, tools_enabled = _build_messages_with_tools(openai_tools, tool_choice, openai_msgs)
                answer, reason, tcs_out, err, _sources = _consume_upstream(
                    model, msgs_up, include_reasoning, effort, search, tools_enabled, max_retry=3, max_tokens=max_tokens)
            # auto tools: forced escalate (+ terminal-force) when action needed but no tool_call;
            # also enter ladder on first-pass retryable busy/529 (not quota).
            if tools_enabled and (not tcs_out) and _should_escalate_auto_tools(
                    tool_choice, openai_tools, tools_enabled, tcs_out, err, openai_msgs,
                    reason_text="".join(reason), answer_text="".join(answer), model=disp):
                _etc = _escalate_tool_choice(openai_tools, openai_msgs)
                LOG.info("rid=%s [tool-escalate] messages nonstream auto->%s first_err=%s",
                         _rid, _etc, (str(err)[:80] if err else None))
                msgs_up2, te2 = _build_messages_with_tools(openai_tools, _etc, openai_msgs)
                a2, r2, t2, e2, _s2 = _consume_upstream(
                    model, msgs_up2, include_reasoning, effort, search, te2, max_retry=3, max_tokens=max_tokens)
                if not e2 and t2:
                    answer, reason, tcs_out, err = a2, r2, t2, None
                    msgs_up, tools_enabled = msgs_up2, te2
                    LOG.info("rid=%s [tool-escalate] success tools=%d", _rid, len(t2))
                else:
                    if e2:
                        LOG.info("rid=%s [tool-escalate] failed err=%s", _rid, str(e2)[:120])
                    else:
                        LOG.info("rid=%s [tool-escalate] still no tool_call", _rid)
                    # Fall through to terminal-force on no-tool OR retryable escalate err.
                    if (not e2) or _is_retryable_tool_upstream_err(e2):
                        _tf = _run_terminal_force_nonstream(
                            model, openai_tools, openai_msgs, include_reasoning, effort, search, max_tokens,
                            rid_label="rid=%s " % _rid)
                        if _tf and (not _tf[3]) and _tf[2]:
                            answer, reason, tcs_out, err = _tf[0], _tf[1], _tf[2], None
                            msgs_up, tools_enabled = _tf[5], _tf[6]
            if err:
                _code, _type = classify_error(err, default=529)
                _extra = {"Retry-After": "5"} if _code in (429, 529) else None
                return self._send(_code, {"type": "error", "error": {"type": _type, "message": str(err)}}, extra=_extra)
            if tcs_out:
                tcs_out = _validate_and_coerce_tool_calls(tcs_out, anth_tools, anthropic=True)
            content = []
            if include_reasoning and "".join(reason):
                content.append({"type": "thinking", "thinking": "".join(reason), "signature": _thinking_signature("".join(reason))})
            if tcs_out:
                for c in tcs_out:
                    content.append({"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]})
                if "".join(answer):
                    content.append({"type": "text", "text": "".join(answer)})
                stop_reason = "tool_use"
            else:
                content.append({"type": "text", "text": "".join(answer)})
                stop_reason = "end_turn"
            # remove empty text blocks
            content = [b for b in content if not (b.get("type") == "text" and not b.get("text"))]
            if not content:
                content.append({"type": "text", "text": ""})
            input_toks = _estimate_messages_tokens(msgs_up)
            output_toks = _estimate_tokens("".join(answer))
            out = {
                "id": msg_id, "type": "message", "role": "assistant", "model": _anthropic_model_id(disp),
                "content": content, "stop_reason": stop_reason, "stop_sequence": None,
                "usage": {"input_tokens": input_toks, "output_tokens": output_toks, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            }
            _extra = {"anthropic-version": "2023-06-01", "request-id": msg_id}
            _extra.update(_compact_headers(_compact_meta))
            self._send(200, out, extra=_extra)
            LOG.info("rid=%s <- 200 model=%s input=%d output=%d time=%dms",
                     _rid, disp, input_toks, output_toks, int((time.monotonic()-_t0)*1000))
            _ewma_update(disp, input_toks, _inp_toks)
            return
        # Prefetch BEFORE writing SSE header so we can compact / escalate without client bytes.
        _stream_buf = None
        _need_buf = tools_enabled and _auto_action_candidate(tool_choice, openai_tools, openai_msgs, model=disp)
        if _need_buf:
            _stream_buf, _iter, _err, _saw_tool = _prefetch_until_tool_or_end(
                model, msgs_up, include_reasoning, effort, search, tools_enabled, max_tokens=max_tokens)
            _first = _stream_buf[0] if _stream_buf else None
            if (not _saw_tool) and _should_escalate_auto_tools(
                    tool_choice, openai_tools, tools_enabled, [], _err, openai_msgs,
                    reason_text="".join(d for k, d in (_stream_buf or []) if k == "reasoning" and isinstance(d, str)),
                    answer_text="".join(d for k, d in (_stream_buf or []) if k == "content" and isinstance(d, str)), model=disp):
                _etc = _escalate_tool_choice(openai_tools, openai_msgs)
                LOG.info("rid=%s [tool-escalate] messages stream auto->%s first_err=%s",
                         _rid, _etc, (str(_err)[:80] if _err else None))
                msgs_up2, te2 = _build_messages_with_tools(openai_tools, _etc, openai_msgs)
                _buf2, _it2, _err2, _saw2 = _prefetch_until_tool_or_end(
                    model, msgs_up2, include_reasoning, effort, search, te2, max_retry=3, max_tokens=max_tokens)
                if not _err2 and _saw2:
                    _stream_buf, _iter, _err = _buf2, _it2, None
                    msgs_up, tools_enabled = msgs_up2, te2
                    LOG.info("rid=%s [tool-escalate] stream reroll saw_tool=%s", _rid, _saw2)
                else:
                    if _err2:
                        LOG.info("rid=%s [tool-escalate] stream reroll err=%s", _rid, str(_err2)[:120])
                    else:
                        LOG.info("rid=%s [tool-escalate] stream still no tool_call", _rid)
                    if (not _err2) or _is_retryable_tool_upstream_err(_err2):
                        _tf = _run_terminal_force_stream_prefetch(
                            model, openai_tools, openai_msgs, include_reasoning, effort, search, max_tokens,
                            rid_label="rid=%s " % _rid)
                        if _tf and (not _tf[2]) and _tf[3]:
                            _stream_buf, _iter, _err = _tf[0], _tf[1], None
                            msgs_up, tools_enabled = _tf[4], _tf[5]
            _first = None  # consume via _stream_buf chain
        else:
            _first, _iter, _err = _prefetch_first_upstream(model, msgs_up, include_reasoning, effort, search, tools_enabled, max_tokens=max_tokens)
        # Retry with compaction on "too long" upstream error
        if _err and _COMPACT_ENABLED and (tl := _parse_too_long(str(_err))):
            target = _compact_budget(tl, disp, _inp_toks, max_tokens)
            LOG.warning("rid=%s stream upstream rejected — retrying compact target=%d", _rid, target)
            req2, _cprep2, _compact_meta = compact_request(req, target, model=disp)
            LOG.info("rid=%s stream retry compacted %s", _rid, _cprep2)
            anth_messages2 = req2.get("messages") or []
            sysc2 = req2.get("system")
            if isinstance(sysc2, list):
                sysc2 = " ".join(b.get("text", "") for b in sysc2 if isinstance(b, dict) and b.get("type") == "text")
            elif sysc2 is None:
                sysc2 = ""
            openai_msgs2 = _flatten_anthropic_messages(anth_messages2, sysc2)
            msgs_up2, tools_enabled2 = _build_messages_with_tools(openai_tools, tool_choice, openai_msgs2)
            if _need_buf:
                _stream_buf, _iter, _err, _saw_tool = _prefetch_until_tool_or_end(
                    model, msgs_up2, include_reasoning, effort, search, tools_enabled2, max_retry=3, max_tokens=max_tokens)
                _first = None
                msgs_up, tools_enabled = msgs_up2, tools_enabled2
            else:
                _first, _iter, _err = _prefetch_first_upstream(model, msgs_up2, include_reasoning, effort, search, tools_enabled2, max_retry=3, max_tokens=max_tokens)
        if _err:
            _code, _type = classify_error(_err, 529)
            _extra = {"Retry-After": "5"} if _code in (429, 529) else None
            return self._send(_code, _err_body(_type, str(_err), flavor="anthropic"), extra=_extra)
        if _stream_buf is not None:
            _iter = _chain_buf_and_iter(_stream_buf, _iter)
            _first = None
        # Now safe to write SSE header — upstream is streaming
        _hdrs = {"anthropic-version": "2023-06-01", "request-id": msg_id}
        _hdrs.update(_compact_headers(_compact_meta))
        self._sse_begin(200, _hdrs)
        lock = threading.Lock()
        stop = {"v": False}
        started_evt = threading.Event()  # barrier: heartbeat must not fire before message_start
        def emit(b):
            with lock:
                self._sse_chunk(b)
        def sse(ev, data):
            # Anthropic SDK routes on the TOP-LEVEL "type" field of each parsed
            # event payload (MessageStream checks event.type === 'message_start'
            # etc.); the raw SSE "event:" line is not enough. Inject type==ev so
            # the SDK sees a well-formed event object.
            if isinstance(data, dict) and data.get("type") is None:
                data = {"type": ev, **data}
            emit(("event: %s\ndata: %s\n\n" % (ev, json.dumps(data, ensure_ascii=False))).encode("utf-8"))
        def heartbeat():
            started_evt.wait()
            while not stop["v"]:
                try:
                    emit(b"event: ping\ndata: {\"type\":\"ping\"}\n\n")
                except Exception:
                    return
                time.sleep(1.0)
        hb = threading.Thread(target=heartbeat, daemon=True)
        hb.start()
        # block index management for streaming content blocks (thinking>text>tool_use interleaved)
        blocks = {}  # type -> index (current open block of that type)
        next_idx = [0]
        stop_reason = {"r": "end_turn"}
        def open_block(btype, extra=None):
            idx = next_idx[0]
            next_idx[0] += 1
            blk = {"type": btype, "index": idx}
            blocks[btype] = blk
            # content_block (the block payload itself) must NOT carry "index";
            # index lives only on the outer SSE event. Anthropic spec content_block
            # has only {type, ...type-specific fields}; some clients (Claude Code
            # desktop) mis-handle an extra "index" inside the block.
            start = {"type": btype}
            if extra:
                start.update(extra)
            sse("content_block_start", {"index": idx, "content_block": start})
            return idx
        thinking_acc = {"s": ""}  # accumulate thinking text for one signature at close; intentional: not reset between blocks because upstream never interleaves thinking→text→thinking
        def close_block(btype):
            idx = blocks.get(btype, {}).get("index")
            if idx is not None:
                if btype == "thinking":
                    # emit a single full signature as the block closes
                    sig = _thinking_signature(thinking_acc["s"])
                    sse("content_block_delta", {"index": idx, "delta": {"type": "signature_delta", "signature": sig}})
                sse("content_block_stop", {"index": idx})
        emitted_any = {"v": False}
        output_acc = {"n": 0}
        input_toks = _estimate_messages_tokens(msgs_up)
        try:
            sse("message_start", {"message": {"id": msg_id, "type": "message", "role": "assistant", "model": _anthropic_model_id(disp), "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": input_toks, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}})
            started_evt.set()  # allow heartbeat now that message_start is the first event
            tool_count = 0
            stream_failed = False
            for kind, data in _upstream_iter(_first, _iter):
                if kind == "reasoning":
                    if "thinking" not in blocks:
                        open_block("thinking")
                    sse("content_block_delta", {"index": blocks["thinking"]["index"], "delta": {"type": "thinking_delta", "thinking": data}})
                    thinking_acc["s"] += data
                    emitted_any["v"] = True
                elif kind == "content":
                    if "thinking" in blocks:
                        close_block("thinking")
                        del blocks["thinking"]
                    if "tool_use" in blocks:
                        close_block("tool_use")
                        del blocks["tool_use"]
                    if "text" not in blocks:
                        open_block("text", {"text": ""})
                    sse("content_block_delta", {"index": blocks["text"]["index"], "delta": {"type": "text_delta", "text": data}})
                    output_acc["n"] += len(data)
                    emitted_any["v"] = True
                elif kind == "tool_call":
                    if not tools_enabled:
                        continue
                    if "text" in blocks:
                        close_block("text")
                        del blocks["text"]
                    if "tool_use" in blocks:
                        close_block("tool_use")
                        del blocks["tool_use"]
                    data = _validate_and_coerce_tool_calls([data], anth_tools, anthropic=True)[0]
                    idx = open_block("tool_use", {"id": data["id"], "name": data["name"], "input": {}})
                    argstr = json.dumps(data["arguments"], ensure_ascii=False)
                    for off in range(0, len(argstr), 20):
                        sse("content_block_delta", {"index": idx, "delta": {"type": "input_json_delta", "partial_json": argstr[off:off + 20]}})
                    tool_count += 1
                    emitted_any["v"] = True
                elif kind == "sources":
                    # upstream sources (empty array in实践中); drop not standard
                    continue
                elif kind == "error":
                    sse("error", {"type": "error", "error": {"type": "api_error", "message": (data.get("error") if isinstance(data, dict) else str(data))}})
                    stream_failed = True
            # close any open blocks
            for bt in list(blocks.keys()):
                close_block(bt)
            if tool_count > 0:
                stop_reason["r"] = "tool_use"
            if not emitted_any["v"] and not stream_failed:
                # ensure at least one block so client sees a (empty) message
                if "text" not in blocks:
                    open_block("text", {"text": ""})
                    close_block("text")
            if not stream_failed:
                sse("message_delta", {"delta": {"stop_reason": stop_reason["r"], "stop_sequence": None}, "usage": {"output_tokens": max(1, output_acc["n"] // 4 + 2)}})
                sse("message_stop", {})
            LOG.info("rid=%s <- 200 stream model=%s time=%dms", _rid, disp, int((time.monotonic()-_t0)*1000))
        except Exception as e:
            try:
                sse("error", {"type": "error", "error": {"type": "api_error", "message": "server: %s" % e}})
                LOG.error("rid=%s <- 500 model=%s error=%s time=%dms", _rid, disp, e, int((time.monotonic()-_t0)*1000))
            except Exception:
                pass
        finally:
            stop["v"] = True
            hb.join(1.0)
            with lock:
                self._sse_end()
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()
    def do_GET(self):
        _t0 = time.monotonic()
        if self.path.startswith("/healthz"):
            self._send(200, {"status": "ok", "service": "maxapi", "models": len(MODEL_DISPLAY_IDS)})
            sys.stderr.write(f"[RES] {self.path} 200 models={len(MODEL_DISPLAY_IDS)} time={int((time.monotonic()-_t0)*1000)}ms\n"); sys.stderr.flush()
            return
        if self.path.startswith("/v1/models"):
            data = [{"id": m, "object": "model", "owned_by": "se.zzmax.cn-guest", "created": 1700000000, "permission": [], "root": m, "parent": None,
                      "context_length": MODEL_META.get(m, (200000, 8192, True))[0],
                      "max_output_tokens": MODEL_META.get(m, (200000, 8192, True))[1],
                      "supports_tool_use": MODEL_META.get(m, (200000, 8192, True))[2]}
                    for m in MODEL_DISPLAY_IDS]
            self._send(200, {"object": "list", "data": data})
            sys.stderr.write(f"[RES] {self.path} 200 models={len(data)} time={int((time.monotonic()-_t0)*1000)}ms\n"); sys.stderr.flush()
            return
        self._send(404, _err_body("not_found_error", "not found"))
    def do_POST(self):
        _t0 = time.monotonic()
        if self.path.startswith("/v1/messages"):
            return self._handle_messages()
        if self.path.startswith("/v1/responses"):
            return self._handle_responses()
        if not self.path.startswith("/v1/chat/completions"):
            self._send(404, _err_body("not_found_error", "not found"))
            return
        if RATE and not RATE.acquire(timeout=0):
            return self._send(429, _err_body("rate_limit_error", "rate limit: too many requests, try again shortly"), extra={"Retry-After": "5"})
        if COMPANION_PROB and random.random() < COMPANION_PROB:
            threading.Thread(target=background_companion_refresh, daemon=True).start()
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except (ValueError, TypeError):
            length = 0
        if length > _MAX_BODY_BYTES:
            return self._send(413, _err_body("invalid_request_error", "request body too large (max %d MB)" % (_MAX_BODY_BYTES // 1024 // 1024)))
        try:
            raw = self.rfile.read(length) if length else b"{}"
            req = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return self._send(400, _err_body("invalid_request_error", "bad json: %s" % e))
        # DIAG: dump compact shape of incoming chat/completions request (for Codex++ conversion debugging)
        try:
            _msgs = req.get("messages") or []
            _tools = req.get("tools") or []
            _roles = [m.get("role") for m in _msgs] if isinstance(_msgs, list) else "?"
            _tc = any(isinstance(m, dict) and m.get("tool_calls") for m in _msgs) if isinstance(_msgs, list) else False
            _toolroles = any(m.get("role") == "tool" for m in _msgs) if isinstance(_msgs, list) else False
            LOG.debug("[chatreq] stream=%s model=%s nmsgs=%d ntools=%d has_tc=%s tool_choice=%s",
                req.get("stream"), req.get("model"), len(_msgs) if isinstance(_msgs,list) else -1, len(_tools) if isinstance(_tools,list) else -1, _tc, req.get("tool_choice"))
        except Exception as _e:
            LOG.debug("[chatreq diag err] %s", _e)
        model = req.get("model") or DEFAULT_MODEL
        grp, sub, disp = resolve_model(model)
        messages = req.get("messages") or []
        stream = bool(req.get("stream"))
        _msgs_count = len(messages) if isinstance(messages, list) else 0
        _tools_count = len(req.get("tools") or [])
        include_reasoning = not (req.get("reasoning") is False or req.get("strip_reasoning"))
        effort = req.get("reasoning_effort") or req.get("reasoningEffort") or "max"
        if str(effort).lower() not in ("off", "low", "medium", "high", "max"):
            effort = "max"
        search = bool(req.get("search") or req.get("web_search") or req.get("websearch"))
        turn_id = "chatcmpl-%d" % int(time.time() * 1000)
        created = int(time.time())
        tools = req.get("tools") or []
        tool_choice = req.get("tool_choice")
        if tool_choice is None and tools:
            tool_choice = "auto"
        if tools and _is_auto_tool_choice(tool_choice) and _user_forbids_tools(messages):
            tool_choice = "none"
        msgs_up, tools_enabled = _build_messages_with_tools(tools, tool_choice, messages)
        _rid = _uuid.uuid4().hex[:8]
        max_tokens = _clamp_max_tokens(disp, req.get("max_tokens") or 8192)
        _pf = _context_limit(disp, max_tokens)
        _inp_toks = _estimate_request_tokens(req, model=disp)
        _compact_meta = {}
        if _inp_toks > _pf:
            LOG.warning("rid=%s [chat] over budget est=%d limit=%d model=%s", _rid, _inp_toks, _pf, disp)
        if _preflight_should_compact(_inp_toks, _pf, model=disp):
            _eff = _est_for_preflight(_inp_toks, disp)
            _cb = _preflight_compact_budget(_inp_toks, _pf, disp)
            LOG.warning("rid=%s [chat] compacting (est=%d eff=%d limit=%d ratio=%.2f trigger=%.2f budget=%d)",
                        _rid, _inp_toks, _eff, _pf, _eff / max(1, _pf), _COMPACT_TRIGGER, _cb)
            req, _cprep, _compact_meta = compact_request(req, _cb, model=disp)
            LOG.info("rid=%s compacted %s", _rid, _cprep)
            messages = req.get("messages") or []
            msgs_up, tools_enabled = _build_messages_with_tools(tools, tool_choice, messages)
        LOG.info("rid=%s [chat] -> %s model=%s msgs=%d tools=%d est=%d limit=%d stream=%s",
                 _rid, self.path, disp, _msgs_count, _tools_count, _inp_toks, _pf, stream)
        if not stream:
            answer, reason, tcs_out, err, sources = _consume_upstream(
                model, msgs_up, include_reasoning, str(effort).lower(), search, tools_enabled, max_retry=5, max_tokens=max_tokens)
            # Retry with compaction on "too long" upstream error
            if err and _COMPACT_ENABLED and (tl := _parse_too_long(str(err))):
                target = _compact_budget(tl, disp, _inp_toks, max_tokens)
                LOG.warning("rid=%s [chat] upstream rejected — retrying compact target=%d", _rid, target)
                req, _cprep, _compact_meta = compact_request(req, target, model=disp)
                LOG.info("rid=%s retry compacted %s", _rid, _cprep)
                messages = req.get("messages") or []
                msgs_up, tools_enabled = _build_messages_with_tools(tools, tool_choice, messages)
                answer, reason, tcs_out, err, sources = _consume_upstream(
                    model, msgs_up, include_reasoning, str(effort).lower(), search, tools_enabled, max_retry=3, max_tokens=max_tokens)
            if tools_enabled and (not tcs_out) and _should_escalate_auto_tools(
                    tool_choice, tools, tools_enabled, tcs_out, err, messages,
                    reason_text="".join(reason), answer_text="".join(answer), model=disp):
                _etc = _escalate_tool_choice(tools, messages)
                LOG.info("rid=%s [tool-escalate] chat nonstream auto->%s first_err=%s",
                         _rid, _etc, (str(err)[:80] if err else None))
                msgs_up2, te2 = _build_messages_with_tools(tools, _etc, messages)
                a2, r2, t2, e2, s2 = _consume_upstream(
                    model, msgs_up2, include_reasoning, str(effort).lower(), search, te2, max_retry=3, max_tokens=max_tokens)
                if not e2 and t2:
                    answer, reason, tcs_out, err, sources = a2, r2, t2, None, s2
                    msgs_up, tools_enabled = msgs_up2, te2
                    LOG.info("rid=%s [tool-escalate] chat success tools=%d", _rid, len(t2))
                else:
                    if e2:
                        LOG.info("rid=%s [tool-escalate] chat failed err=%s", _rid, str(e2)[:120])
                    else:
                        LOG.info("rid=%s [tool-escalate] chat still no tool_call", _rid)
                    if (not e2) or _is_retryable_tool_upstream_err(e2):
                        _tf = _run_terminal_force_nonstream(
                            model, tools, messages, include_reasoning, str(effort).lower(), search, max_tokens,
                            rid_label="rid=%s " % _rid)
                        if _tf and (not _tf[3]) and _tf[2]:
                            answer, reason, tcs_out, err, sources = _tf[0], _tf[1], _tf[2], None, _tf[4]
                            msgs_up, tools_enabled = _tf[5], _tf[6]
            if err:
                _code, _type = classify_error(err)
                return self._send(_code, _err_body(_type, err))
            if tools_enabled and tcs_out:
                tcs_out = _validate_and_coerce_tool_calls(tcs_out, tools)
            content = "".join(answer)
            if tools_enabled and tcs_out:
                msg = {"role": "assistant", "content": content if content else None,
                       "tool_calls": [{"id": c["id"], "type": "function",
                                       "function": {"name": c["name"],
                                                    "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                                      for c in tcs_out]}
            else:
                msg = {"role": "assistant", "content": content}
            if include_reasoning:
                msg["reasoning_content"] = "".join(reason)
            p_toks = _estimate_messages_tokens(msgs_up)
            c_toks = _estimate_tokens("".join(answer) + "".join(reason))
            out = {
                "id": turn_id, "object": "chat.completion", "created": created, "model": disp,
                "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if (tools_enabled and tcs_out) else "stop"}],
                "usage": {"prompt_tokens": p_toks, "completion_tokens": c_toks, "total_tokens": p_toks + c_toks}}
            if sources:
                out["sources"] = sources
            self._send(200, out, extra=_compact_headers(_compact_meta) or None)
            LOG.info("rid=%s <- 200 [chat] model=%s input=%d output=%d time=%dms",
                     _rid, disp, p_toks, c_toks, int((time.monotonic()-_t0)*1000))
            _ewma_update(disp, p_toks, _inp_toks)
            return
        # Prefetch BEFORE SSE headers; escalate auto action turns if no tool_call.
        _stream_buf = None
        _need_buf = tools_enabled and _auto_action_candidate(tool_choice, tools, messages, model=disp)
        _eff = str(effort).lower()
        if _need_buf:
            _stream_buf, _iter, _err, _saw_tool = _prefetch_until_tool_or_end(
                model, msgs_up, include_reasoning, _eff, search, tools_enabled, max_tokens=max_tokens)
            if (not _saw_tool) and _should_escalate_auto_tools(
                    tool_choice, tools, tools_enabled, [], _err, messages,
                    reason_text="".join(d for k, d in (_stream_buf or []) if k == "reasoning" and isinstance(d, str)),
                    answer_text="".join(d for k, d in (_stream_buf or []) if k == "content" and isinstance(d, str)), model=disp):
                _etc = _escalate_tool_choice(tools, messages)
                LOG.info("rid=%s [tool-escalate] chat stream auto->%s first_err=%s",
                         _rid, _etc, (str(_err)[:80] if _err else None))
                msgs_up2, te2 = _build_messages_with_tools(tools, _etc, messages)
                _buf2, _it2, _err2, _saw2 = _prefetch_until_tool_or_end(
                    model, msgs_up2, include_reasoning, _eff, search, te2, max_retry=3, max_tokens=max_tokens)
                if not _err2 and _saw2:
                    _stream_buf, _iter, _err = _buf2, _it2, None
                    msgs_up, tools_enabled = msgs_up2, te2
                    LOG.info("rid=%s [tool-escalate] chat stream reroll saw_tool=%s", _rid, _saw2)
                else:
                    if _err2:
                        LOG.info("rid=%s [tool-escalate] chat stream reroll err=%s", _rid, str(_err2)[:120])
                    else:
                        LOG.info("rid=%s [tool-escalate] chat stream still no tool_call", _rid)
                    if (not _err2) or _is_retryable_tool_upstream_err(_err2):
                        _tf = _run_terminal_force_stream_prefetch(
                            model, tools, messages, include_reasoning, _eff, search, max_tokens,
                            rid_label="rid=%s " % _rid)
                        if _tf and (not _tf[2]) and _tf[3]:
                            _stream_buf, _iter, _err = _tf[0], _tf[1], None
                            msgs_up, tools_enabled = _tf[4], _tf[5]
            _first = None
        else:
            _first, _iter, _err = _prefetch_first_upstream(model, msgs_up, include_reasoning, _eff, search, tools_enabled, max_tokens=max_tokens)
        # Retry with compaction on "too long" upstream error
        if _err and _COMPACT_ENABLED and (tl := _parse_too_long(str(_err))):
            target = _compact_budget(tl, disp, _inp_toks, max_tokens)
            LOG.warning("rid=%s [chat] stream upstream rejected — retrying compact target=%d", _rid, target)
            req2, _cprep2, _compact_meta = compact_request(req, target, model=disp)
            LOG.info("rid=%s retry compacted %s", _rid, _cprep2)
            messages2 = req2.get("messages") or []
            msgs_up2, tools_enabled2 = _build_messages_with_tools(tools, tool_choice, messages2)
            if _need_buf:
                _stream_buf, _iter, _err, _saw_tool = _prefetch_until_tool_or_end(
                    model, msgs_up2, include_reasoning, _eff, search, tools_enabled2, max_retry=3, max_tokens=max_tokens)
                _first = None
                msgs_up, tools_enabled, messages = msgs_up2, tools_enabled2, messages2
            else:
                _first, _iter, _err = _prefetch_first_upstream(model, msgs_up2, include_reasoning, _eff, search, tools_enabled2, max_retry=3, max_tokens=max_tokens)
        if _err:
            _code, _type = classify_error(_err)
            _extra = {"Retry-After": "5"} if _code in (429, 529) else None
            return self._send(_code, _err_body(_type, str(_err)), extra=_extra)
        if _stream_buf is not None:
            _iter = _chain_buf_and_iter(_stream_buf, _iter)
            _first = None
        # Now safe to write SSE header
        self._sse_begin(200, _compact_headers(_compact_meta))
        lock = threading.Lock()
        stop = {"v": False}
        started_evt = threading.Event()
        def emit(b):
            with lock:
                self._sse_chunk(b)
        def sse(o):
            emit(("data: " + json.dumps(o, ensure_ascii=False) + "\n\n").encode("utf-8"))
        def heartbeat():
            started_evt.wait()
            while not stop["v"]:
                try:
                    emit(b": keepalive\n\n")
                except Exception:
                    return
                time.sleep(1.0)
        hb = threading.Thread(target=heartbeat, daemon=True)
        hb.start()
        try:
            sse({"id": turn_id, "object": "chat.completion.chunk", "created": created, "model": disp,
                 "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})
            started_evt.set()
            tool_call_count = 0
            stream_failed = False
            for kind, data in _upstream_iter(_first, _iter):
                if kind == "content":
                    sse({"id": turn_id, "object": "chat.completion.chunk", "created": created, "model": disp,
                         "choices": [{"index": 0, "delta": {"content": data}, "finish_reason": None}]})
                elif kind == "reasoning":
                    sse({"id": turn_id, "object": "chat.completion.chunk", "created": created, "model": disp,
                         "choices": [{"index": 0, "delta": {"reasoning_content": data}, "finish_reason": None}]})
                elif kind == "sources":
                    sse({"id": turn_id, "object": "chat.completion.chunk", "created": created, "model": disp,
                         "choices": [], "sources": data})
            if not stream_failed:
                sse({"id": turn_id, "object": "chat.completion.chunk", "created": created, "model": disp,
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if (tools_enabled and tool_call_count > 0) else "stop"}]})
            emit(b"data: [DONE]\n\n")
            LOG.info("rid=%s <- 200 [chat] stream model=%s time=%dms", _rid, disp, int((time.monotonic()-_t0)*1000))
        except Exception as e:
            try:
                sse({"error": {"message": "server: %s" % e, "type": "api_error", "code": None}})
                sys.stderr.write(f"[ERR] {self.path} 500 model={disp} error={e} time={int((time.monotonic()-_t0)*1000)}ms\n"); sys.stderr.flush()
            except Exception:
                pass
        finally:
            stop["v"] = True
            hb.join(1.0)
            with lock:
                self._sse_end()


def main():
    global RATE
    _setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--rpm", type=int, default=60, help="max chat requests per minute (token bucket, 0=unlimited)")
    ap.add_argument("--no-companion", action="store_true", help="disable companion nav-categories calls")
    args = ap.parse_args()
    if args.no_companion:
        global COMPANION_PROB
        COMPANION_PROB = 0.0
    RATE = RateLimiter(args.rpm) if args.rpm > 0 else None
    # Warm guest session before first client request (non-fatal).
    if COMPANION_PROB:
        try:
            n = companion_touch(force_all=True)
            LOG.info("startup companion cookies=%d", n)
        except Exception as e:
            LOG.warning("startup companion failed: %r", e)
        threading.Thread(target=background_companion_refresh, daemon=True).start()
    srv = http.server.ThreadingHTTPServer((args.host, args.port), Handler)
    print("maxapi listening on %s:%d  models=%d  rpm=%s  companion=%s" % (
        args.host, args.port, len(MODEL_DISPLAY_IDS), args.rpm, not args.no_companion))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("")
        print("bye")


if __name__ == "__main__":
    main()
