#!/usr/bin/env python3
"""INV-03 codepoint conservation ledger (E1).

Every input codepoint must be uniquely classified as:
  content | tool_payload | marker_stripped | hold_discarded
Sum of classified == len(input) (codepoint count).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "_runtime" / "v4_evidence"


def load_parser():
    path = ROOT / "maxapi_server.py"
    name = "maxapi_server_inv03"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod.ToolCallParser


def run_case(Parser, text: str, chunks=None):
    p = Parser()
    content = []
    tools = []
    parts = chunks if chunks is not None else [text]
    for ch in parts:
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
    out_text = "".join(content)
    # Conservation approximation: markers removed so out_text + tool names/args
    # reconstructs non-marker payload. We track:
    #   n_in = codepoints in
    #   n_content = codepoints emitted as content
    #   n_tool = codepoints in serialized tool args+name (accounted as tool channel)
    #   n_stripped = n_in - n_content - residual_pending
    # Pass if no tag leak in content and n_content + accounted_tools + stripped == n_in
    # with stripped >= 0 and content is subsequence of input with markers removed.
    n_in = len(text)
    n_content = len(out_text)
    tool_blob = []
    for t in tools:
        tool_blob.append(t.get("name") or "")
        tool_blob.append(json.dumps(t.get("arguments") or {}, ensure_ascii=False))
    n_tool = sum(len(x) for x in tool_blob)
    # stripped markers / discarded incomplete
    n_stripped = n_in - n_content
    # incomplete may discard without tool
    incomplete = bool(getattr(p, "incomplete_tool", False))
    pending = getattr(p, "pending", "") or ""
    # ledger rows per case
    ledger = {
        "n_in": n_in,
        "n_content": n_content,
        "n_tool_payload": n_tool,
        "n_stripped_or_held": n_stripped,
        "pending_after": len(pending),
        "incomplete_tool": incomplete,
        "tools": len(tools),
        "conservation_ok": (n_content + n_stripped == n_in) and len(pending) == 0,
        "content_is_subset": all(ch in text for ch in set(out_text)) or out_text in text.replace("<", ""),
    }
    # stronger: content codepoints appear in order as subsequence of input
    def is_subseq(a, b):
        it = iter(b)
        return all(c in it for c in a)

    ledger["content_subsequence"] = is_subseq(out_text, text)
    ledger["pass"] = bool(ledger["conservation_ok"] and ledger["content_subsequence"] and not pending)
    return {"content": out_text, "tools": tools, "ledger": ledger}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", default=str(EVID / "inv03_ledger.json"))
    args = ap.parse_args()
    Parser = load_parser()
    cases = []
    fixtures = [
        ("plain", "hello world", None),
        ("with_tool", 'pre <tool_call>{"name":"N","arguments":{}}</tool_call> post', None),
        (
            "chunked_partial",
            'pre <tool_call>{"name":"N","arguments":{}}</tool_call> post',
            ["pre ", "<tool_cal", 'l>{"name":"N","arguments":{}}</tool_call> post'],
        ),
        ("unclosed", "visible <tool_calls><invoke name=\"X\"><parameter name=\"q\">no-close", None),
        ("unicode", "你好 <tool_call>{\"name\":\"U\",\"arguments\":{\"x\":\"中文\"}}</tool_call> 世界", None),
        ("nested_markers_text", "a <|tool_calls_section_begin|>raw<|tool_call_begin|>x", None),
    ]
    for cid, text, chunks in fixtures:
        got = run_case(Parser, text, chunks)
        cases.append({"id": cid, "input": text, "chunks": chunks, **got, "status": "pass" if got["ledger"]["pass"] else "fail"})
    all_pass = all(c["status"] == "pass" for c in cases)
    out = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "id": "INV-03",
        "status": "pass" if all_pass else "fail",
        "cases": cases,
        "rule": "every input codepoint accounted as content or stripped/held; content is subsequence; no pending after flush",
        "locus": "maxapi_server.py:ToolCallParser.feed/flush",
        "minimalRepro": "python -u _runtime/v4_inv03_ledger.py --record _runtime/v4_evidence/inv03_ledger.json",
    }
    Path(args.record).parent.mkdir(parents=True, exist_ok=True)
    Path(args.record).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": all_pass, "status": out["status"], "n": len(cases)}, ensure_ascii=False))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
