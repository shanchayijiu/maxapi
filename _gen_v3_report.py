# -*- coding: utf-8 -*-
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

report = {
    "specVersion": "2api-acceptance-v3",
    "buildId": "2026-08-19.maxapi.db6b73f",
    "upstreamProfile": "se.zzmax.cn-guest-ui-protocol",
    "sanitizerConfigVersion": "markers-toolcallparser+reasoningfilter-2026-08-19",
    "reviewedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "implementation": {
        "root": r"C:\Users\Administrator\Desktop\maxapi",
        "server": "maxapi_server.py",
        "commit": "db6b73fea417baeac804404db41691e4a3b72e79",
        "dockerMd5": "1d40bc7cb923e6cc5455e9da075aff45",
        "existingTests": {
            "local_compat": "74/74",
            "openai_sdk_gold": "20/20",
            "tool_suite": "28/28 (historical)",
            "agent_long": "17/17 (historical)",
        },
    },
    "invariants": [
        {
            "id": "INV-01",
            "status": "fail",
            "evidence": (
                "EOF hold-back incomplete: ToolCallParser.flush emits unfinished tag prefixes as content. "
                "Repro: feed('hi <tool_cal')+flush() => content contains '<tool_cal'. "
                "Locus: ToolCallParser.flush pending-tail; _find_partial. "
                "</s> is stripped, but known tool/think prefixes leak at EOF. No T-07 blacklist gate."
            ),
        },
        {
            "id": "INV-02",
            "status": "unknown",
            "evidence": "No unknown-marker detector (REQ-SAN-12/T-09). Need heuristic scan on raw upstream + fixture dump + CI alert.",
        },
        {
            "id": "INV-03",
            "status": "unknown",
            "evidence": "No codepoint conservation / rule-id accounting / reconstruct-original mode. Need sanitizer trace + fixture rebuild (T-04).",
        },
        {
            "id": "INV-04",
            "status": "pass",
            "evidence": (
                "Live forced tool: finish_reason=tool_calls and message.content is None; "
                "gold nonstream.tool/stream.tool 20/20; chat stream drops trailing prose after tool_call_count>0."
            ),
        },
        {
            "id": "INV-05",
            "status": "unknown",
            "evidence": "No record-replay fixture dual-path stream vs nonstream diff. Gold is live upstream, not fixture replay.",
        },
        {
            "id": "INV-06",
            "status": "unknown",
            "evidence": "No golden 4-tuple + exhaustive split fuzz. Compat only has a few incomplete/salvage cases (not T-04).",
        },
        {
            "id": "INV-07",
            "status": "unknown",
            "evidence": (
                "No timer flush in sanitizer (good for SAN-05), but _make_tool_id uses random; "
                "no N-repeat/concurrency pure-function suite (ids ignorable)."
            ),
        },
        {
            "id": "INV-08",
            "status": "unknown",
            "evidence": (
                "Code guard exists: upstream() if content_yielded and attempt>1 -> error return (~3529). "
                "No T-08 fault-injection test proving no duplicated bytes -> unknown per spec rule."
            ),
        },
        {
            "id": "INV-09",
            "status": "unknown",
            "evidence": "No per-request canary nonce / cross-request isolation probes (T-12). XFF rotate != INV-09.",
        },
        {
            "id": "INV-10",
            "status": "unknown",
            "evidence": "No OpenAI response JSON Schema gate (T-01). SDK gold covers fields partially only.",
        },
        {
            "id": "INV-11",
            "status": "fail",
            "evidence": (
                "Only stop/tool_calls stably mapped. No forced cases for length, content_filter, legacy function_call, "
                "or upstream-abort must-not-be-stop. Client stop param silently ignored."
            ),
        },
        {
            "id": "INV-12",
            "status": "unknown",
            "evidence": "True upstream SSE forwarding (not fake slice), but no TTFB/inter-chunk/outputSpanRatio thresholds (§3.5).",
        },
    ],
    "requirements": [
        {"id": "REQ-SAN-01", "level": "MUST", "status": "unknown", "tests": ["T-04", "T-13"], "note": "No pure-function/config-version assertions"},
        {"id": "REQ-SAN-02", "level": "MUST", "status": "unknown", "tests": ["T-05"], "note": "Parsers operate on str; incremental UTF-8 decoder not unit-tested"},
        {
            "id": "REQ-SAN-03",
            "level": "MUST",
            "status": "pass",
            "tests": ["manual-probe"],
            "note": "Longer FULLS/PREFIXES first; earliest across positions; cross-chunk hold then parse OK. Missing formal ambiguity fixtures",
        },
        {"id": "REQ-SAN-04", "level": "MUST", "status": "unknown", "tests": ["T-04"], "note": "No explicit 64-codepoint max / holdback=max-1 config assert"},
        {
            "id": "REQ-SAN-05",
            "level": "MUST",
            "status": "pass",
            "tests": ["code-audit"],
            "note": "No timer-based parser flush; SSE keepalive comments do not flush sanitizer",
        },
        {
            "id": "REQ-SAN-06",
            "level": "MUST",
            "status": "fail",
            "tests": ["manual-probe"],
            "note": "EOF pending emits half tags as content. Repro ToolCallParser.feed('hi <tool_cal')+flush(). Locus flush pending-tail",
        },
        {
            "id": "REQ-SAN-07",
            "level": "MUST",
            "status": "pass",
            "tests": ["code-audit"],
            "note": "Fullwidth DeepSeek tokens and ASCII variants registered separately; explicit replace not NFKC",
        },
        {"id": "REQ-SAN-08", "level": "MUST", "status": "unknown", "tests": ["T-04", "T-07"], "note": "Fence-aware seg exists; whitespace-normalization rules not fixture-tested"},
        {
            "id": "REQ-SAN-09",
            "level": "MUST",
            "status": "pass",
            "tests": ["compat+R3"],
            "note": "Incomplete tool: drop tags, no half tool_call; tools_enabled error/retry (R3). Strategy not yet in public report field",
        },
        {"id": "REQ-SAN-10", "level": "MUST", "status": "pass", "tests": ["code-audit"], "note": "_MAX_CAPTURE_CHARS=2MiB -> incomplete_tool"},
        {"id": "REQ-SAN-11", "level": "MUST", "status": "unknown", "tests": ["T-04", "T-07"], "note": "tools_enabled gates tool emit; control-token blacklist incomplete (INV-01)"},
        {"id": "REQ-SAN-12", "level": "MUST", "status": "fail", "tests": ["T-09"], "note": "No unknown-marker detector"},
        {"id": "REQ-SAN-13", "level": "SHOULD", "status": "unknown", "tests": ["T-07"], "note": "Citation/watermark stripping not verified"},
        {
            "id": "REQ-STR-01",
            "level": "MUST",
            "status": "pass",
            "tests": ["gold raw.sse_tool_wire"],
            "note": "event-stream charset=utf-8; no-cache; keep-alive; X-Accel-Buffering: no",
        },
        {"id": "REQ-STR-02", "level": "MUST", "status": "unknown", "tests": ["T-02"], "note": "No response compression; no proxy matrix"},
        {"id": "REQ-STR-03", "level": "MUST", "status": "pass", "tests": ["gold+probe"], "note": "data JSON + [DONE]; nothing after DONE"},
        {"id": "REQ-STR-04", "level": "MUST", "status": "pass", "tests": ["gold"], "note": "chunk has id/object/created/model/choices"},
        {"id": "REQ-STR-05", "level": "MUST", "status": "pass", "tests": ["gold stream.text"], "note": "first role=assistant; last finish_reason"},
        {"id": "REQ-STR-06", "level": "MUST", "status": "pass", "tests": ["gold stream"], "note": "incremental deltas; SDK assembles"},
        {
            "id": "REQ-STR-07",
            "level": "MUST",
            "status": "fail",
            "tests": ["gold error.n_gt_1"],
            "note": "n>1 returns 400; no per-choice sanitizer state machine",
        },
        {
            "id": "REQ-STR-08",
            "level": "MUST",
            "status": "fail",
            "tests": ["live-probe"],
            "note": "usage-only choices=[] before DONE OK; middle chunks omit usage key instead of usage:null",
        },
        {
            "id": "REQ-STR-09",
            "level": "MUST",
            "status": "fail",
            "tests": ["live-probe"],
            "note": "stream=false + stream_options still 200, not 400",
        },
        {"id": "REQ-STR-10", "level": "MUST", "status": "unknown", "tests": ["T-08"], "note": "client_gone aborts drain; no <=1s cancel metric assert"},
        {"id": "REQ-STR-11", "level": "MUST", "status": "pass", "tests": ["live-probe"], "note": "empty_no_fr=0 on sample stream"},
        {
            "id": "REQ-STR-12",
            "level": "SHOULD",
            "status": "waived",
            "tests": ["T-11"],
            "note": "SSE comment keepalive on by default; no eco-matrix waiver doc beyond this report",
        },
        {"id": "REQ-STR-13", "level": "MUST", "status": "unknown", "tests": ["T-08"], "note": "No bounded write-buffer / slow-client backpressure test"},
        {"id": "REQ-STR-14", "level": "MUST", "status": "unknown", "tests": ["T-03"], "note": "True upstream stream; missing threshold gate"},
        {"id": "REQ-TOOL-01", "level": "MUST", "status": "pass", "tests": ["gold"], "note": "auto/none/required/named/parallel structure pass"},
        {"id": "REQ-TOOL-02", "level": "MUST", "status": "pass", "tests": ["gold stream.tool/raw"], "note": "arguments JSON string increments"},
        {"id": "REQ-TOOL-03", "level": "MUST", "status": "pass", "tests": ["live+gold"], "note": "tool_calls => content null"},
        {"id": "REQ-TOOL-04", "level": "MUST", "status": "pass", "tests": ["gold raw.sse_tool_wire"], "note": "first shard id/type/name/args=\"\" + later incr"},
        {
            "id": "REQ-TOOL-05",
            "level": "MUST",
            "status": "fail",
            "tests": ["code-audit"],
            "note": "id format toolu_01... (Anthropic-like), not call_+16 alnum; stable in-stream but format mismatch",
        },
        {"id": "REQ-TOOL-06", "level": "MUST", "status": "pass", "tests": ["gold parallel/stream"], "note": "index increments"},
        {"id": "REQ-TOOL-07", "level": "MUST", "status": "pass", "tests": ["code-audit+gold"], "note": "drop trailing content after tools"},
        {
            "id": "REQ-TOOL-08",
            "level": "MUST",
            "status": "unknown",
            "tests": ["T-10"],
            "note": "escalate/terminal-force exist; required-still-empty final behavior not fixture-locked against forge",
        },
        {"id": "REQ-TOOL-09", "level": "MUST", "status": "unknown", "tests": ["T-10"], "note": "named tool_choice gold often passes; wrong-name error/remap not unit-tested"},
        {"id": "REQ-TOOL-10", "level": "MUST", "status": "unknown", "tests": ["T-10"], "note": "function name 1..64 400 branch not tested"},
        {
            "id": "REQ-TOOL-11",
            "level": "MUST",
            "status": "fail",
            "tests": ["live-probe"],
            "note": "unknown tool_call_id does not 400 (probe 200 continues)",
        },
        {
            "id": "REQ-TOOL-12",
            "level": "MUST",
            "status": "fail",
            "tests": ["live-probe"],
            "note": "tool_calls coverage integrity not enforced with 400",
        },
        {
            "id": "REQ-TOOL-13",
            "level": "MUST",
            "status": "unknown",
            "tests": ["gold json_object"],
            "note": "json_object soft inject exists; strict schema validate/retry not proven",
        },
        {"id": "REQ-TOOL-14", "level": "SHOULD", "status": "unknown", "tests": ["T-11"], "note": "legacy functions/function_call not tested"},
        {
            "id": "REQ-RSN-01",
            "level": "MUST",
            "status": "pass",
            "tests": ["unit RF"],
            "note": "ReasoningFilter continuous: think->reasoning, outer->content",
        },
        {
            "id": "REQ-RSN-02",
            "level": "MUST",
            "status": "unknown",
            "tests": ["T-01"],
            "note": "include_reasoning gate exists; default exposure policy not doc-tested",
        },
        {
            "id": "REQ-RSN-03",
            "level": "MUST",
            "status": "fail",
            "tests": ["live-probe"],
            "note": "client stop silently ignored; channel scope N/A until stop works",
        },
        {"id": "REQ-RSN-04", "level": "MUST", "status": "pass", "tests": ["gold stream"], "note": "role once; dual-channel tparser"},
        {
            "id": "REQ-RSN-05",
            "level": "SHOULD",
            "status": "fail",
            "tests": ["live-probe"],
            "note": "usage lacks completion_tokens_details.reasoning_tokens",
        },
        {"id": "REQ-STATE-01", "level": "MUST", "status": "unknown", "tests": ["T-12"], "note": "designed stateless; no echo/self-history probe"},
        {"id": "REQ-STATE-02", "level": "MUST", "status": "fail", "tests": ["T-12"], "note": "no canary nonce"},
        {
            "id": "REQ-STATE-03",
            "level": "MUST",
            "status": "unknown",
            "tests": ["T-12"],
            "note": "session reuse may be N/A if none; not documented -> unknown",
        },
        {"id": "REQ-STATE-04", "level": "MUST", "status": "unknown", "tests": ["T-08", "T-12"], "note": "identity/cookie pool exists; lease/fuse tests missing"},
        {
            "id": "REQ-STATE-05",
            "level": "MUST",
            "status": "unknown",
            "tests": ["T-09", "T-12"],
            "note": "upstream persona/autonomous tools not canaried; belongs in knownDeviations or tests",
        },
        {"id": "REQ-STATE-06", "level": "MUST", "status": "pass", "tests": ["gold unit.context"], "note": "context_length_exceeded code/param"},
        {"id": "REQ-STATE-07", "level": "MUST", "status": "unknown", "tests": ["T-10"], "note": "boundary message matrix not built"},
        {"id": "REQ-STATE-08", "level": "MUST", "status": "unknown", "tests": ["T-10"], "note": "multimodal reject not tested"},
        {
            "id": "REQ-API-01",
            "level": "MUST",
            "status": "fail",
            "tests": ["live-probe"],
            "note": "has /v1/models and chat; GET /v1/models/{id} returns full list not single model object",
        },
        {
            "id": "REQ-API-02",
            "level": "MUST",
            "status": "unknown",
            "tests": ["T-01", "T-11"],
            "note": "list fields mostly present; per-id tiny smoke not gated for full table",
        },
        {
            "id": "REQ-API-03",
            "level": "MUST",
            "status": "pass",
            "tests": ["live-probe"],
            "note": "response model echoes request alias deepseek-v4-flash",
        },
        {
            "id": "REQ-API-04",
            "level": "MUST",
            "status": "unknown",
            "tests": ["T-14"],
            "note": "MODEL_META in code; not versioned into consistency report historically",
        },
        {
            "id": "REQ-API-05",
            "level": "MUST",
            "status": "unknown",
            "tests": ["T-10"],
            "note": "_SILENT_PARAMS exists but not fully listed in docs/report",
        },
        {
            "id": "REQ-API-06",
            "level": "MUST",
            "status": "fail",
            "tests": ["live-probe"],
            "note": "logprobs=true still 200, not 400",
        },
        {
            "id": "REQ-API-07",
            "level": "MUST",
            "status": "unknown",
            "tests": ["T-10"],
            "note": "max_tokens vs max_completion_tokens priority not unit/doc tested",
        },
        {"id": "REQ-API-08", "level": "MUST", "status": "unknown", "tests": ["T-01"], "note": "system_fingerprint behavior not asserted"},
        {"id": "REQ-API-09", "level": "MUST", "status": "pass", "tests": ["compat/gold"], "note": "application/json + chat.completion"},
        {
            "id": "REQ-API-10",
            "level": "MUST",
            "status": "unknown",
            "tests": ["T-02", "T-10"],
            "note": "CORS present; Bearer not enforced; unknown field counters absent",
        },
        {"id": "REQ-ERR-01", "level": "MUST", "status": "pass", "tests": ["gold errors"], "note": "_err_body message/type/param/code"},
        {"id": "REQ-ERR-02", "level": "MUST", "status": "pass", "tests": ["gold error.bad_model"], "note": "404 model_not_found"},
        {
            "id": "REQ-ERR-03",
            "level": "MUST",
            "status": "unknown",
            "tests": ["T-10"],
            "note": "429 has Retry-After; remaining/limit/reset headers not seen",
        },
        {
            "id": "REQ-ERR-04",
            "level": "MUST",
            "status": "fail",
            "tests": ["live-probe"],
            "note": "chat response lacks x-request-id/request-id header (only log rid)",
        },
        {
            "id": "REQ-ERR-05",
            "level": "MUST",
            "status": "unknown",
            "tests": ["T-08"],
            "note": "stream error frame code exists; error-then-DONE not automated",
        },
        {"id": "REQ-ERR-06", "level": "MUST", "status": "unknown", "tests": ["T-08"], "note": "same as INV-08: code yes, injection test no"},
        {
            "id": "REQ-ERR-07",
            "level": "MUST",
            "status": "unknown",
            "tests": ["T-08", "T-10"],
            "note": "classify_error leans 529/400; 504/502 mapping not fully unit-tested",
        },
        {
            "id": "REQ-ERR-08",
            "level": "MUST",
            "status": "unknown",
            "tests": ["T-08"],
            "note": "R3 incomplete->error improves path; need chaos fixture locking no silent stop",
        },
        {"id": "REQ-ERR-09", "level": "MUST", "status": "unknown", "tests": ["T-10"], "note": "_MAX_BODY_BYTES exists; full limit matrix untested"},
        {
            "id": "REQ-SEC-01",
            "level": "MUST",
            "status": "unknown",
            "tests": ["T-10"],
            "note": "local proxy often accepts any Bearer; constant-time compare unproven",
        },
        {
            "id": "REQ-SEC-02",
            "level": "MUST",
            "status": "unknown",
            "tests": ["T-10"],
            "note": "avoids leaking quota Chinese copy; full redaction policy untested",
        },
        {"id": "REQ-SEC-03", "level": "MUST", "status": "unknown", "tests": ["T-10"], "note": "remote fetch/multimodal not core path"},
        {"id": "REQ-SEC-04", "level": "MUST", "status": "unknown", "tests": ["T-12"], "note": "guest identity pool is not multi-tenant model"},
        {"id": "REQ-OBS-01", "level": "MUST", "status": "pass", "tests": ["gold"], "note": "usage not always 0; estimate semantics"},
        {"id": "REQ-OBS-02", "level": "MUST", "status": "fail", "tests": ["T-12"], "note": "missing TTFB/sanitizer-hit/disconnect metric set"},
        {"id": "REQ-OBS-03", "level": "MUST", "status": "fail", "tests": ["T-04"], "note": "no rule-id counters"},
        {"id": "REQ-OBS-04", "level": "MUST", "status": "pass", "tests": ["this-report"], "note": "this appendix E JSON"},
        {"id": "REQ-OBS-05", "level": "MUST", "status": "fail", "tests": ["T-09"], "note": "no page-human alert / raw-stream retention policy"},
        {"id": "REQ-OBS-06", "level": "SHOULD", "status": "unknown", "tests": ["T-12"], "note": "log rid present; end-to-end trace unproven"},
    ],
    "fourCriticalChecks": {
        "holdback_eof_flush": {
            "status": "fail",
            "detail": (
                "pending half-tag at EOF emitted as content; capture-path has salvage/discard, "
                "but _find_partial hold tail takes pending flush leak path"
            ),
            "locus": "maxapi_server.py:ToolCallParser.flush pending-tail; _find_partial",
        },
        "same_position_longest_match": {
            "status": "pass",
            "detail": (
                "tool FULLS/PREFIXES longer-first; cross-chunk hold parses tool_calls. "
                "Missing formal think/tool overlap fixtures, core tool longest-match holds"
            ),
            "locus": "_TOOL_TAG_FULLS order; ReasoningFilter._OPENS",
        },
        "include_usage_tail_two_details": {
            "status": "fail",
            "detail": "choices:[] usage chunk present before DONE; middle chunks omit usage key (not usage:null)",
            "locus": "chat stream sse emitters; include_usage block",
        },
        "no_retry_after_first_byte": {
            "status": "unknown",
            "detail": "upstream hard-guard content_yielded blocks retry; missing fault-injection automation",
            "locus": "upstream ~3529",
        },
    },
    "thresholds": {
        "ttfbP50Ms": None,
        "ttfbP95Ms": None,
        "interChunkP95Ms": None,
        "outputSpanRatio": None,
        "usageDriftPct": None,
        "mutationKillRate": None,
        "cancelPropagationP95Ms": None,
        "note": "no §3.5 sampling data",
    },
    "fixtures": {
        "total": 0,
        "dialects": 0,
        "unclosedVariants": 0,
        "goldenReviewed": 0,
        "note": "no v3 golden fixture library; only ad-hoc compat/gold live cases",
    },
    "knownDeviations": [
        {
            "id": "DEV-01",
            "requirement": "REQ-STATE-05",
            "description": "Upstream is guest UI protocol (se.zzmax); persona/quota semantics possible; not canaried off",
        },
        {
            "id": "DEV-02",
            "requirement": "REQ-TOOL-05",
            "description": "tool call id uses toolu_01 prefix instead of call_",
        },
        {
            "id": "DEV-03",
            "requirement": "REQ-STR-07",
            "description": "n>1 explicitly 400 unsupported (single-stream upstream)",
        },
    ],
    "verdict": "fail",
    "blockingIds": [
        "INV-01",
        "REQ-SAN-06",
        "REQ-STR-08",
        "REQ-STR-09",
        "REQ-API-06",
        "REQ-ERR-04",
        "REQ-TOOL-11",
        "REQ-TOOL-12",
        "REQ-SAN-12",
        "INV-11",
    ],
    "remediationPriority": [
        "P0 INV-01/REQ-SAN-06: EOF flush hold-back — unfinished prefixes must not enter content; CONTENT tail only after non-prefix residual",
        "P0 REQ-STR-08: emit usage:null on every intermediate chat.completion.chunk",
        "P0 REQ-STR-09: stream_options without stream=true -> 400 param=stream_options",
        "P0 REQ-ERR-04: add x-request-id (or request-id) on all responses, correlate log rid",
        "P0 REQ-API-06: logprobs/top_logprobs -> 400 (changes response shape)",
        "P0 REQ-TOOL-11/12: validate tool_call_id coverage / unknown id -> 400",
        "P0 INV-08/REQ-ERR-06: add T-08 inject after first byte, assert no retry/duplicate",
        "P1 REQ-SAN-12/INV-02: unknown marker detector + fixture dump",
        "P1 INV-03/06: golden fixtures + codepoint conservation + chunk fuzz",
        "P1 INV-11: finish_reason matrix length/content_filter/upstream-abort",
        "P1 REQ-API-01: GET /v1/models/{id} single object",
        "P1 REQ-TOOL-05: id format call_+16 or document DEV + client-tested exception",
        "P1 client stop sequences in same hold-back engine (REQ-RSN-03/SAN stop)",
        "P2 INV-09/STATE-02 canary nonce isolation",
        "P2 INV-10 JSON Schema gate T-01",
        "P2 OBS metrics + canary alert-to-human",
        "P2 INV-12 timing thresholds",
        "P2 n>1 either implement or keep DEV-03 explicit in report forever",
        "P2 reasoning_tokens details if available",
        "P2 mutation tests T-13 kill-rate >=85%",
    ],
}

ic = Counter(x["status"] for x in report["invariants"])
rc = Counter(x["status"] for x in report["requirements"])
report["summaryCounts"] = {
    "invariants": dict(ic),
    "requirements": dict(rc),
    "blocking": len(report["blockingIds"]),
}

path = Path(__file__).resolve().parent / "2api_v3_consistency_report.json"
path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("WROTE", path)
print("INV", dict(ic))
print("REQ", dict(rc))
print("verdict", report["verdict"])
