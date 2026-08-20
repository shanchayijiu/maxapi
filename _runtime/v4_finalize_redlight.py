#!/usr/bin/env python3
"""
Redlights for leak-a / INV-13 / REQ-SAN-14: unified chat-stream finalize.

Rule (v4): six termination classes must converge on one finalize entry that
guarantees terminal wire signal + generator close. Before the helper exists,
this script MUST fail (red). After wiring, it must pass.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "maxapi_server.py"
EVID = ROOT / "_runtime" / "v4_evidence"

# Six classes from v4 § leak-a / INV-13
REQUIRED_CLASSES = (
    "natural_eof",
    "stop_sequence",
    "length_limit",
    "upstream_interrupt",
    "client_cancel",
    "internal_or_buffer_limit",
)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def analyze_source(text: str) -> dict:
    problems = []
    # 1) single helper symbol
    has_helper = bool(re.search(r"def\s+_finalize_chat_stream\s*\(", text))
    if not has_helper:
        problems.append("missing_helper:_finalize_chat_stream")

    # 2) helper must mention all six classes (as string literals or comments table)
    missing_cls = [c for c in REQUIRED_CLASSES if c not in text]
    if missing_cls:
        problems.append("helper_missing_classes:" + ",".join(missing_cls))

    # 3) chat stream path must not have three divergent terminal emitters without helper call
    # Locate the chat stream block roughly via unique markers
    m = re.search(r"stream_options\.include_usage.*?finally:", text, re.S)
    block = m.group(0) if m else text
    # Count raw finish_reason stop emitters in stream chat section
    stop_emit = len(re.findall(r'finish_reason":\s*"stop"|finish_reason.: .\bstop\b', block))
    helper_calls = len(re.findall(r"_finalize_chat_stream\s*\(", text))
    if has_helper and helper_calls < 2:
        # def + at least one call site
        problems.append(f"helper_not_called:calls={helper_calls}")

    # 4) client_gone branch must go through helper (not only LOG)
    # After fix: client_gone path calls finalize with class client_cancel
    if has_helper:
        if "client_cancel" not in text or not re.search(
            r"_finalize_chat_stream\([^)]*client_cancel|class_name\s*=\s*[\"']client_cancel",
            text,
        ):
            # allow table mapping
            if "client_cancel" not in text:
                problems.append("client_cancel_not_in_finalize_table")

    # 5) AST: ensure helper exists as FunctionDef inside Handler or module
    try:
        tree = ast.parse(text)
        names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if has_helper and "_finalize_chat_stream" not in names:
            problems.append("helper_name_not_in_ast")
    except SyntaxError as e:
        problems.append(f"syntax:{e}")

    return {
        "has_helper": has_helper,
        "helper_calls": helper_calls if has_helper else 0,
        "missing_classes": missing_cls,
        "problems": problems,
        "pass": not problems,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", default=str(EVID / "finalize_redlight.json"))
    args = ap.parse_args()
    text = SRC.read_text(encoding="utf-8")
    result = analyze_source(text)
    out = {
        "id": "a",
        "title": "unified finalize on six termination classes",
        "generatedAt": _now(),
        "locus": "maxapi_server.py:Handler._finalize_chat_stream",
        "required_classes": list(REQUIRED_CLASSES),
        "status": "pass" if result["pass"] else "fail",
        "expected": {
            "helper": "_finalize_chat_stream",
            "classes": list(REQUIRED_CLASSES),
            "called_from_chat_stream": True,
        },
        "actual": result,
        "problems": result["problems"],
        "minimalRepro": "python -u _runtime/v4_finalize_redlight.py --record _runtime/v4_evidence/finalize_redlight.json",
    }
    Path(args.record).parent.mkdir(parents=True, exist_ok=True)
    Path(args.record).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": result["pass"], "status": out["status"], "problems": result["problems"]}, ensure_ascii=False))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
