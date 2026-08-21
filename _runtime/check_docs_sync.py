#!/usr/bin/env python3
"""Lightweight docs sync checks for maxapi (no network required except optional healthz)."""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ok = True


def fail(msg: str) -> None:
    global ok
    ok = False
    print("FAIL:", msg)


def main() -> int:
    required = [
        ROOT / "STATUS.md",
        ROOT / "README.md",
        ROOT / "GOALS.md",
        ROOT / "docs" / "README.md",
        ROOT / "docs" / "dev-contract.md",
        ROOT / "docs" / "architecture.md",
        ROOT / "docs" / "upstream-se-zzmax.md",
        ROOT / "docs" / "toolcall-semantics.md",
        ROOT / "docs" / "latency-and-escalate.md",
        ROOT / "docs" / "agent-wire.md",
    ]
    for p in required:
        if not p.is_file():
            fail(f"missing {p.relative_to(ROOT)}")

    wrong = ROOT / "project_maxapi.md"
    if wrong.is_file():
        fail("root project_maxapi.md must not exist (use docs/archive/)")

    goals = (ROOT / "GOALS.md").read_text(encoding="utf-8", errors="replace")
    if "成功判据" in goals and goals.count("- [") > 3:
        fail("GOALS.md looks like a full goals table again (should be pointer)")
    if "STATUS.md" not in goals:
        fail("GOALS.md should point to STATUS.md")

    status = (ROOT / "STATUS.md").read_text(encoding="utf-8", errors="replace")
    nlines = status.count("\n") + 1
    if nlines > 320:
        fail(f"STATUS.md too long for resume leaf: {nlines} lines (want <=320)")
    for needle in ("0.5", "NewAPI", "卡点", "保护区"):
        if needle not in status:
            fail(f"STATUS.md missing {needle!r}")

    docs_readme = (ROOT / "docs" / "README.md").read_text(encoding="utf-8", errors="replace")
    if "NewAPI" not in docs_readme and "多用户" not in docs_readme:
        fail("docs/README.md should state NewAPI / 多用户口径")

    for script in (
        "_openai_sdk_gold.py",
        "_local_compat_check.py",
        "_accept_tool_suite.py",
        "_accept_agent_long.py",
    ):
        if not (ROOT / script).is_file():
            fail(f"missing acceptance script {script}")

    base = os.environ.get("MAXAPI_BASE", "http://127.0.0.1:8080")
    try:
        with urllib.request.urlopen(base + "/healthz", timeout=3) as r:
            hz = json.loads(r.read().decode("utf-8"))
        print("healthz:", {k: hz.get(k) for k in ("status", "commit", "models", "binarySha256")})
        host = ROOT / "maxapi_server.py"
        if host.is_file() and hz.get("binarySha256"):
            import hashlib

            h = hashlib.sha256(host.read_bytes()).hexdigest()
            if h != hz["binarySha256"]:
                print(
                    "WARN: host maxapi_server.py sha256 != healthz.binarySha256 "
                    "(container stale or local undeployed diff)"
                )
                print("      host=", h)
                print("      live=", hz["binarySha256"])
    except Exception as e:
        print("WARN: healthz unreachable:", e)

    if ok:
        print("OK: docs sync checks passed")
        return 0
    print("DONE with failures")
    return 1


if __name__ == "__main__":
    sys.exit(main())
