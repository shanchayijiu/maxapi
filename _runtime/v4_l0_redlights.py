#!/usr/bin/env python3
"""L0 redlights against real maxapi_server.ToolCallParser (importlib)."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "_runtime"
FIX = RUNTIME / "v4_fixtures"
EVID = RUNTIME / "v4_evidence"

TAG_RE = re.compile(
    r"(?is)(?:\|DSML\||<\|?(?:tool_calls|tool_call|tool_use|function_calls?|invoke|parameter)|</?(?:tool_calls|tool_call|tool_use|function|invoke|parameter|think(?:ing)?)|◁/?DSML|｜)",
)


def load_parser_cls():
    path = ROOT / "maxapi_server.py"
    spec = importlib.util.spec_from_file_location("maxapi_server_v4", path)
    mod = importlib.util.module_from_spec(spec)
    # Avoid starting server if any side effect — module defines main but doesn't call it.
    sys.modules["maxapi_server_v4"] = mod
    spec.loader.exec_module(mod)
    return mod.ToolCallParser, mod


def run_parser(Parser, chunks):
    p = Parser()
    content = []
    tools = []
    for ch in chunks:
        for kind, data in p.feed(ch):
            if kind == "content":
                content.append(data)
            elif kind == "tool_call":
                tools.append(data)
    for kind, data in p.flush():
        if kind == "content":
            content.append(data)
        elif kind == "tool_call":
            tools.append(data)
    text = "".join(content)
    return {
        "content": text,
        "tools": tools,
        "incomplete_tool": bool(getattr(p, "incomplete_tool", False)),
        "pending": getattr(p, "pending", ""),
        "capturing": bool(getattr(p, "capturing", False)),
    }


def write_fixtures():
    FIX.mkdir(parents=True, exist_ok=True)
    fixtures = {
        "leak_b_longest_match.json": {
            "id": "b",
            "title": "earliest/longest tool tag selection",
            "chunks": [
                "hello ",
                "<function_calls><invoke name=\"x\">",
                "<parameter name=\"a\">1</parameter></invoke></function_calls> tail",
            ],
            "expect": {
                "content_equals": "hello  tail",
                "min_tools": 0,  # may or may not parse this dialect; must not leak tags
                "forbid_tag_regex": True,
            },
            "locus": "maxapi_server.py:2644",
        },
        "leak_c_holdback_codepoint.json": {
            "id": "c",
            "title": "partial tag hold-back then complete across chunks (unicode safe)",
            "chunks": [
                "pre ",
                "<tool_cal",  # partial
                "l>{\"name\":\"N\",\"arguments\":{}}</tool_call> post",
            ],
            "expect": {
                "content_contains": ["pre ", " post"],
                "forbid_tag_regex": True,
                "no_pending_after_flush": True,
            },
            "locus": "maxapi_server.py:2670",
        },
        "leak_d_unclosed_strip.json": {
            "id": "d",
            "title": "unclosed tool block at EOF must not leak markers",
            "chunks": [
                "visible ",
                "<tool_calls><invoke name=\"X\"><parameter name=\"q\">",
                "no-close",
            ],
            "expect": {
                "content_equals": "visible ",
                "forbid_tag_regex": True,
                "incomplete_tool": True,
            },
            "locus": "maxapi_server.py:2726",
        },
        "leak_e_no_tools_strip.json": {
            "id": "e",
            "title": "parser strips tags even when caller has no tools (content plane)",
            "chunks": [
                "A ",
                "<|tool_calls_section_begin|>",
                "raw",
                "<|tool_call_begin|>",
                "x",
            ],
            "expect": {
                "forbid_tag_regex": True,
                "content_startswith": "A ",
            },
            "locus": "maxapi_server.py:3574",
            "note": "simulates upstream text with control tokens when tools_enabled=false; parser still peels",
        },
        "leak_a_close_paths.json": {
            "id": "a",
            "title": "document-only: six termination paths (not Parser-only)",
            "type": "harness_template",
            "status": "unknown",
            "required_paths": [
                "natural_eof",
                "stop_sequence",
                "length_limit",
                "upstream_interrupt",
                "client_cancel",
                "internal_or_buffer_limit",
            ],
            "locus": "maxapi_server.py:3791",
        },
        "leak_f_include_usage.json": {
            "id": "f",
            "title": "live include_usage shape",
            "type": "live_template",
            "status": "unknown",
            "locus": "maxapi_server.py:5527",
            "probe": "POST /v1/chat/completions stream + stream_options.include_usage",
        },
        "leak_g_upstream_abort.json": {
            "id": "g",
            "title": "upstream abort must not become finish_reason=stop",
            "type": "harness_template",
            "status": "unknown",
            "locus": "maxapi_server.py:5620",
        },
        "leak_h_deploy_fingerprint.json": {
            "id": "h",
            "title": "deployed fingerprint fields required",
            "type": "live_template",
            "required_fields": [
                "binarySha256",
                "imageDigest",
                "commit",
                "sanitizerConfigVersion",
                "upstreamProfile",
                "processStartedAt",
            ],
            "locus": "maxapi_server.py:5233",
        },
    }
    for name, body in fixtures.items():
        (FIX / name).write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return list(fixtures)


def eval_case(Parser, fix: dict) -> dict:
    cid = fix.get("id")
    if fix.get("type") in ("harness_template", "live_template"):
        return {
            "id": cid,
            "status": "unknown",
            "expected": "handler/live harness",
            "actual": "not executable at Parser layer",
            "locus": fix.get("locus"),
            "note": fix.get("title"),
        }
    chunks = fix.get("chunks") or []
    got = run_parser(Parser, chunks)
    exp = fix.get("expect") or {}
    problems = []
    content = got["content"]
    if exp.get("forbid_tag_regex") and TAG_RE.search(content):
        problems.append(f"tag_leak content={content!r}")
    if "content_equals" in exp and content != exp["content_equals"]:
        # allow if only whitespace drift? no — exact
        problems.append(f"content_equals expected={exp['content_equals']!r} actual={content!r}")
    if "content_contains" in exp:
        for s in exp["content_contains"]:
            if s not in content:
                problems.append(f"missing {s!r} in {content!r}")
    if "content_startswith" in exp and not content.startswith(exp["content_startswith"]):
        problems.append(f"startswith {exp['content_startswith']!r} actual={content!r}")
    if exp.get("incomplete_tool") is True and not got["incomplete_tool"]:
        problems.append("expected incomplete_tool=True")
    if exp.get("no_pending_after_flush") and (got.get("pending") or got.get("capturing")):
        problems.append(f"pending residual pending={got.get('pending')!r} capturing={got.get('capturing')}")
    if "min_tools" in exp and len(got["tools"]) < exp["min_tools"]:
        problems.append(f"tools {len(got['tools'])} < {exp['min_tools']}")

    status = "fail" if problems else "pass"
    return {
        "id": cid,
        "status": status,
        "expected": exp,
        "actual": {"content": content, "tools": got["tools"], "incomplete_tool": got["incomplete_tool"]},
        "problems": problems,
        "locus": fix.get("locus"),
        "fixture": str(fix.get("_path", "")),
    }


def map_priority(cases: dict) -> dict:
    # INV-01/14/15 from b,d,e ; INV-03 unknown without ledger ; INV-13 fail by design note
    def agg(keys):
        cs = [cases[k] for k in keys if k in cases]
        if not cs:
            return {"status": "unknown", "note": "no cases"}
        if any(c.get("status") == "fail" for c in cs):
            bad = next(c for c in cs if c.get("status") == "fail")
            return {
                "status": "fail",
                "codeLocation": bad.get("locus"),
                "minimalRepro": "python -u _runtime/v4_l0_redlights.py --record _runtime/v4_evidence/l0_redlight_run.json",
                "expected": bad.get("expected"),
                "actual": bad.get("actual"),
            }
        if all(c.get("status") == "pass" for c in cs):
            return {"status": "pass", "evidence": "_runtime/v4_evidence/l0_redlight_run.json"}
        return {"status": "unknown", "note": "mixed/unknown child cases"}

    return {
        "INV-01": agg(["b", "d", "e"]),
        "INV-03": {
            "status": "unknown",
            "note": "no rule-id conservation ledger",
            "nextEvidenceNeeded": "E1",
            "codeLocation": "maxapi_server.py:2482",
        },
        "INV-13": {
            "status": "fail",
            "codeLocation": "maxapi_server.py:3791",
            "minimalRepro": "rg -n finalize maxapi_server.py; inspect upstream/chat termination",
            "expected": "single finalize for 6 termination classes",
            "actual": "no finalize symbol; split EOF/cancel/error paths",
        },
        "INV-14": agg(["d"]),
        "INV-15": agg(["e", "b"]),
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", required=True)
    ap.add_argument("--write-fixtures", action="store_true", default=True)
    args = ap.parse_args()
    names = write_fixtures()
    Parser, mod = load_parser_cls()
    cases = {}
    for fp in sorted(FIX.glob("leak_*.json")):
        fix = json.loads(fp.read_text(encoding="utf-8"))
        fix["_path"] = str(fp.relative_to(ROOT)).replace("\\", "/")
        result = eval_case(Parser, fix)
        cases[result["id"]] = result
    out = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parser": "maxapi_server.ToolCallParser",
        "fixtures": names,
        "cases": cases,
    }
    outp = Path(args.record)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    pri = map_priority(cases)
    (EVID / "inv_priority_e1.json").write_text(json.dumps(pri, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k: v.get("status") for k, v in cases.items()}
    print(json.dumps({"ok": True, "summary": summary, "record": str(outp)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
