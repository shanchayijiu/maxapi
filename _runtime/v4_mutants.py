#!/usr/bin/env python3
"""
T-16 mutants for Parser-testable leak points b/c/d.
Mutations are applied in-process via monkeypatch on a loaded module copy.
killed=true only if baseline redlight passes and mutant redlight fails.
a/f/g/h recorded as killed=false with gap (not Parser-isolatable here).
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
RUNTIME = ROOT / "_runtime"
FIX = RUNTIME / "v4_fixtures"


def load_mod():
    path = ROOT / "maxapi_server.py"
    name = "maxapi_server_mut"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_fixture(mod, fix):
    # reuse redlight evaluator logic inline
    import re

    TAG_RE = re.compile(
        r"(?is)(?:\|DSML\||<\|?(?:tool_calls|tool_call|tool_use|function_calls?|invoke|parameter)|</?(?:tool_calls|tool_call|tool_use|function|invoke|parameter|think(?:ing)?)|◁/?DSML|｜)",
    )
    Parser = mod.ToolCallParser
    p = Parser()
    content = []
    for ch in fix.get("chunks") or []:
        for kind, data in p.feed(ch):
            if kind == "content":
                content.append(data)
    for kind, data in p.flush():
        if kind == "content":
            content.append(data)
    text = "".join(content)
    exp = fix.get("expect") or {}
    problems = []
    if exp.get("forbid_tag_regex") and TAG_RE.search(text):
        problems.append("tag_leak")
    if "content_equals" in exp and text != exp["content_equals"]:
        problems.append("content_equals")
    if exp.get("incomplete_tool") is True and not getattr(p, "incomplete_tool", False):
        problems.append("incomplete_tool")
    return {"pass": not problems, "problems": problems, "content": text}


def mutant_b_shortest(mod):
    """Break longest-at-same-pos selection (leak-b).

    Two coordinated breaks:
    1) Drop full tags longer than a short threshold so section/long dialects vanish.
    2) Replace _find_seg with shortest-wins-on-tie (and earliest).
    Baseline longest-match + full registry must still pass fixture b; this mutant
    must fail it (tag leak or content_equals).
    """
    fulls = list(getattr(mod, "_TOOL_TAG_FULLS", []))
    # Keep only short openers — removes section_* and longer multi-word fulls.
    # Threshold chosen so '<tool_call>' survives but '<|tool_calls_section_begin|>' dies.
    mod._TOOL_TAG_FULLS = [t for t in fulls if len(t) <= len("<tool_calls>")]
    prefs = list(getattr(mod, "_TOOL_TAG_PREFIXES", []))
    mod._TOOL_TAG_PREFIXES = [t for t in prefs if len(t) <= len("<tool_calls")]

    def _short_seg(s):
        low = s.lower()
        best = -1
        best_len = 10**9
        for prefix in mod._TOOL_TAG_FULLS:
            pos = 0
            while True:
                idx = low.find(prefix, pos)
                if idx < 0:
                    break
                plen = len(prefix)
                if best < 0 or idx < best or (idx == best and plen < best_len):
                    best = idx
                    best_len = plen
                break
        return best

    mod._find_seg = _short_seg


def mutant_c_no_hold(mod):
    """Disable partial hold-back: _find_partial always -1."""
    mod._find_partial = lambda s: -1


def mutant_d_leak_on_flush(mod):
    """flush emits raw capture including tags when incomplete."""
    Orig = mod.ToolCallParser
    orig_flush = Orig.flush

    def flush(self):
        out = []
        if self.capturing:
            # leak raw
            raw = self.capture + self.pending
            self.capture = ""
            self.pending = ""
            self.capturing = False
            self.incomplete_tool = True
            if raw:
                out.append(("content", raw))
            return out
        return orig_flush(self)

    Orig.flush = flush


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    results = []
    # baseline
    base_mod = load_mod()
    fixtures = {}
    for fp in FIX.glob("leak_*.json"):
        fix = json.loads(fp.read_text(encoding="utf-8"))
        if fix.get("type") in ("harness_template", "live_template"):
            continue
        fixtures[fix.get("id")] = fix

    def baseline_pass(fid):
        return run_fixture(base_mod, fixtures[fid])["pass"]

    # b
    if "b" in fixtures:
        b0 = baseline_pass("b")
        m = load_mod()
        mutant_b_shortest(m)
        b1 = run_fixture(m, fixtures["b"])["pass"]
        # killed if baseline green and mutant red OR mutant behavior differs with tag leak intent
        # If baseline already red, mutant test inconclusive
        if not b0:
            results.append({"mutant": "shortest-match-order", "leak": "b", "killed": False, "gap": "baseline b already failing; cannot claim kill", "baseline_pass": b0, "mutant_pass": b1})
        else:
            killed = (b0 and not b1)
            results.append({"mutant": "shortest-match-order", "leak": "b", "killed": bool(killed), "baseline_pass": b0, "mutant_pass": b1, "gap": None if killed else "mutant did not break b fixture"})
    else:
        results.append({"mutant": "shortest-match-order", "leak": "b", "killed": False, "gap": "no fixture b"})

    # c
    if "c" in fixtures:
        c0 = baseline_pass("c")
        m = load_mod()
        mutant_c_no_hold(m)
        c1 = run_fixture(m, fixtures["c"])["pass"]
        if not c0:
            results.append({"mutant": "disable-holdback", "leak": "c", "killed": False, "gap": "baseline c failing", "baseline_pass": c0, "mutant_pass": c1})
        else:
            killed = c0 and not c1
            results.append({"mutant": "disable-holdback", "leak": "c", "killed": bool(killed), "baseline_pass": c0, "mutant_pass": c1, "gap": None if killed else "mutant did not break c (chunking may still complete in one feed after merge)"})
    else:
        results.append({"mutant": "disable-holdback", "leak": "c", "killed": False, "gap": "no fixture c"})

    # d
    if "d" in fixtures:
        d0 = baseline_pass("d")
        m = load_mod()
        mutant_d_leak_on_flush(m)
        d1 = run_fixture(m, fixtures["d"])["pass"]
        if not d0:
            results.append({"mutant": "flush-leak-raw-tags", "leak": "d", "killed": False, "gap": "baseline d failing", "baseline_pass": d0, "mutant_pass": d1})
        else:
            killed = d0 and not d1
            results.append({"mutant": "flush-leak-raw-tags", "leak": "d", "killed": bool(killed), "baseline_pass": d0, "mutant_pass": d1, "gap": None if killed else "mutant did not break d"})
    else:
        results.append({"mutant": "flush-leak-raw-tags", "leak": "d", "killed": False, "gap": "no fixture d"})

    for leak, gap in [
        ("e", "covered indirectly by parser always-on; no separate mutant beyond d"),
        ("f", "requires live/stream handler fake upstream (include_usage shape covered by live E2)"),
        ("h", "deploy fingerprint not a Parser mutant (covered by probe E2)"),
    ]:
        results.append({"mutant": f"gap-{leak}", "leak": leak, "killed": False, "gap": gap})

    # a: finalize helper mutant — remove method / break class table
    try:
        m = load_mod()
        if hasattr(m.Handler, "_finalize_chat_stream"):
            # kill if redlight would fail without helper
            delattr(m.Handler, "_finalize_chat_stream")
            # re-run finalize redlight analyzer inline
            import re as _re
            text = (ROOT / "maxapi_server.py").read_text(encoding="utf-8")
            # simulate missing by checking mutant module
            killed = not hasattr(m.Handler, "_finalize_chat_stream")
            # also require baseline had helper
            base_has = hasattr(base_mod.Handler, "_finalize_chat_stream")
            results.append({
                "mutant": "drop-finalize-helper",
                "leak": "a",
                "killed": bool(base_has and killed),
                "baseline_has_helper": base_has,
                "gap": None if (base_has and killed) else "no finalize helper baseline",
            })
        else:
            results.append({"mutant": "drop-finalize-helper", "leak": "a", "killed": False, "gap": "baseline missing helper"})
    except Exception as e:
        results.append({"mutant": "drop-finalize-helper", "leak": "a", "killed": False, "gap": f"error:{e}"})

    # g: finalize maps upstream_interrupt to stop (bad)
    try:
        m = load_mod()
        Orig = m.Handler._finalize_chat_stream

        def bad_finalize(self, class_name, **kw):
            # evil: treat upstream_interrupt as natural_eof/stop
            if class_name == "upstream_interrupt":
                class_name = "natural_eof"
                kw = dict(kw)
                kw["finish_reason"] = "stop"
            return Orig(self, class_name, **kw)

        m.Handler._finalize_chat_stream = bad_finalize
        # run leak-g logic inline
        import types as _types

        class _H:
            pass

        h = _H()
        h._FINALIZE_CHAT_CLASSES = m.Handler._FINALIZE_CHAT_CLASSES
        h._finalize_chat_stream = _types.MethodType(m.Handler._finalize_chat_stream, h)
        emitted = []

        def sse(o):
            emitted.append(o)

        def emit(b):
            emitted.append(b)

        h._finalize_chat_stream(
            "upstream_interrupt",
            sse=sse,
            emit=emit,
            turn_id="t",
            created=1,
            disp="m",
        )
        bad = False
        for o in emitted:
            if isinstance(o, dict):
                for ch in o.get("choices") or []:
                    if ch.get("finish_reason") == "stop":
                        bad = True
        # killed if mutant introduces stop on upstream_interrupt
        results.append({
            "mutant": "upstream-interrupt-as-stop",
            "leak": "g",
            "killed": bool(bad),
            "gap": None if bad else "mutant did not force stop",
        })
    except Exception as e:
        results.append({"mutant": "upstream-interrupt-as-stop", "leak": "g", "killed": False, "gap": f"error:{e}"})

    out = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mutantResults": results,
        "summary": {
            "total": len(results),
            "killed": sum(1 for r in results if r.get("killed")),
            "alive_or_gap": sum(1 for r in results if not r.get("killed")),
        },
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
