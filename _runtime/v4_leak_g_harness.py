#!/usr/bin/env python3
"""leak-g / REQ-ERR-08: upstream abort must not become finish_reason=stop.

Tests Handler._finalize_chat_stream class upstream_interrupt emits [DONE]
without a finish_reason=stop chunk (error line is separate).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "_runtime" / "v4_evidence"


def load_mod():
    path = ROOT / "maxapi_server.py"
    name = "maxapi_server_leakg"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeHandler:
    def __init__(self, mod):
        # bind unbound methods
        self._FINALIZE_CHAT_CLASSES = mod.Handler._FINALIZE_CHAT_CLASSES
        self._finalize_chat_stream = types.MethodType(mod.Handler._finalize_chat_stream, self)
        self.emitted = []

    def _sse(self, o):
        self.emitted.append(("sse", o))

    def _emit(self, b):
        self.emitted.append(("raw", b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else str(b)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", default=str(EVID / "leak_g_abort.json"))
    args = ap.parse_args()
    mod = load_mod()
    h = _FakeHandler(mod)
    # simulate: error already sent by caller; finalize upstream_interrupt
    result = h._finalize_chat_stream(
        "upstream_interrupt",
        sse=h._sse,
        emit=h._emit,
        turn_id="chatcmpl-test",
        created=1,
        disp="test-model",
        close_iter=None,
    )
    problems = []
    # must emit DONE
    raws = [x[1] for x in h.emitted if x[0] == "raw"]
    if not any("[DONE]" in r for r in raws):
        problems.append("missing_[DONE]")
    # must NOT emit finish_reason stop
    for kind, payload in h.emitted:
        if kind == "sse" and isinstance(payload, dict):
            for ch in payload.get("choices") or []:
                if ch.get("finish_reason") == "stop":
                    problems.append("finish_reason_stop_on_upstream_interrupt")
                if ch.get("finish_reason") == "tool_calls":
                    problems.append("finish_reason_tool_calls_on_upstream_interrupt")
    # natural_eof control: SHOULD emit stop
    h2 = _FakeHandler(mod)
    h2._finalize_chat_stream(
        "natural_eof",
        sse=h2._sse,
        emit=h2._emit,
        turn_id="c2",
        created=1,
        disp="m",
        finish_reason="stop",
    )
    stops = []
    for kind, payload in h2.emitted:
        if kind == "sse" and isinstance(payload, dict):
            for ch in payload.get("choices") or []:
                if ch.get("finish_reason") == "stop":
                    stops.append(True)
    if not stops:
        problems.append("control_natural_eof_missing_stop")

    # client_cancel: no wire
    h3 = _FakeHandler(mod)
    r3 = h3._finalize_chat_stream("client_cancel", client_gone=True, close_iter=None)
    if h3.emitted:
        problems.append("client_cancel_emitted_wire")
    if r3.get("class") != "client_cancel":
        problems.append("client_cancel_class")

    out = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "id": "g",
        "status": "pass" if not problems else "fail",
        "problems": problems,
        "upstream_interrupt_result": result,
        "upstream_interrupt_emitted": h.emitted,
        "expected": "upstream_interrupt → [DONE] without finish_reason=stop; natural_eof → stop; client_cancel → no wire",
        "locus": "maxapi_server.py:Handler._finalize_chat_stream",
        "minimalRepro": "python -u _runtime/v4_leak_g_harness.py --record _runtime/v4_evidence/leak_g_abort.json",
    }
    Path(args.record).parent.mkdir(parents=True, exist_ok=True)
    Path(args.record).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": not problems, "status": out["status"], "problems": problems}, ensure_ascii=False))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
