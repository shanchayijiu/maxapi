#!/usr/bin/env python3
"""Build v4 catalog (INV-01..18 + all REQ-*) from the v4 acceptance markdown."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_CANDIDATES = [
    Path(r"C:/Users/Administrator/Desktop/2api 兼容层验收标准 v4.md"),
    ROOT.parent / "2api 兼容层验收标准 v4.md",
]
MUST_E3 = {"INV-02", "INV-09", "INV-12", "REQ-STATE-05", "REQ-OBS-05"}

# Fallback statements if regex misses (from doc §3)
INV_FALLBACK = {
    "INV-01": ("已知标记零泄漏", "E1+E2", "L0"),
    "INV-02": ("未知标记零告警", "E3", "L0"),
    "INV-03": ("码点守恒", "E1", "L0"),
    "INV-04": ("工具互斥与合法", "E1", "L1"),
    "INV-05": ("双模式等价", "E1", "L1"),
    "INV-06": ("分块不变性", "E1", "L1"),
    "INV-07": ("纯函数", "E1", "L1"),
    "INV-08": ("单调追加", "E1+E2", "L1"),
    "INV-09": ("无状态与隔离", "E3", "L2"),
    "INV-10": ("报文契约", "E1", "L1"),
    "INV-11": ("终止忠实", "E1+E2", "L1"),
    "INV-12": ("真流式", "E3", "L2"),
    "INV-13": ("终止路径统一", "E1+E2", "L0"),
    "INV-14": ("无损降级", "E1", "L0"),
    "INV-15": ("通道不回灌", "E1", "L0"),
    "INV-16": ("部署一致", "E2", "L2"),
    "INV-17": ("终止可感知", "E1+E2", "L1"),
    "INV-18": ("证据完整", "E1", "L2"),
}


def _min_ev(s: str) -> str:
    s = (s or "").strip()
    s = s.replace("**", "")
    if "E3" in s and "E1" not in s and "E2" not in s:
        return "E3"
    if "E2" in s and "E1" in s:
        return "E2"  # need at least E2 when both listed for chain items; store primary highest required
    if "E3" in s:
        return "E3"
    if "E2" in s:
        return "E2"
    if "E1" in s:
        return "E1"
    return "E1"


def parse_doc(text: str):
    inv = {}
    # **INV-01 title** · stmt · method · evidence · timing
    for m in re.finditer(
        r"\*\*(INV-\d+)([^*]*)\*\*\s*·\s*([^·\n]+)·\s*([^·\n]+)·\s*([^·\n]+)·\s*([^\n]+)",
        text,
    ):
        iid = m.group(1)
        title = m.group(2).strip()
        stmt = m.group(3).strip()
        method = m.group(4).strip()
        ev = m.group(5).strip()
        inv[iid] = {
            "id": iid,
            "title": title,
            "statement": stmt,
            "method": method,
            "minEvidence": _min_ev(ev),
            "mustE3": iid in MUST_E3 or "E3" in ev.replace(" ", "") and ev.strip().startswith("**E3"),
            "level": "MUST",
            "plane": INV_FALLBACK.get(iid, ("", "", "L1"))[2],
        }
        if iid in MUST_E3:
            inv[iid]["mustE3"] = True
            inv[iid]["minEvidence"] = "E3"

    # ensure all 18
    for iid, (title, ev, plane) in INV_FALLBACK.items():
        if iid not in inv:
            inv[iid] = {
                "id": iid,
                "title": title,
                "statement": title,
                "method": "",
                "minEvidence": _min_ev(ev),
                "mustE3": iid in MUST_E3,
                "level": "MUST",
                "plane": plane,
            }
        else:
            inv[iid]["mustE3"] = iid in MUST_E3 or inv[iid].get("mustE3")
            if iid in MUST_E3:
                inv[iid]["minEvidence"] = "E3"

    req = {}
    # **REQ-XXX-NN** MUST/SHOULD · stmt · E? · T-?
    for m in re.finditer(
        r"\*\*(REQ-[A-Z]+-\d+)\*\*\s*(MUST NOT|MUST|SHOULD|MAY)?\s*·\s*([^·\n]+)(?:·\s*([^·\n]+))?(?:·\s*([^\n]+))?",
        text,
    ):
        rid = m.group(1)
        level = (m.group(2) or "MUST").strip()
        stmt = m.group(3).strip()
        ev = (m.group(4) or "E1").strip()
        tests = (m.group(5) or "").strip()
        req[rid] = {
            "id": rid,
            "level": level,
            "statement": stmt,
            "minEvidence": _min_ev(ev),
            "mustE3": rid in MUST_E3 or _min_ev(ev) == "E3",
            "tests": [t for t in re.findall(r"T-\d+", tests)],
        }
        if rid in MUST_E3:
            req[rid]["mustE3"] = True
            req[rid]["minEvidence"] = "E3"

    # id list fallback
    id_list = ROOT / "_runtime" / "_v4_id_list.txt"
    if id_list.exists():
        for line in id_list.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if re.fullmatch(r"REQ-[A-Z]+-\d+", line) and line not in req:
                req[line] = {
                    "id": line,
                    "level": "MUST",
                    "statement": "(from id list; statement not parsed)",
                    "minEvidence": "E1",
                    "mustE3": line in MUST_E3,
                    "tests": [],
                }
            if re.fullmatch(r"INV-\d+", line) and line not in inv:
                title, ev, plane = INV_FALLBACK.get(line, (line, "E1", "L1"))
                inv[line] = {
                    "id": line,
                    "title": title,
                    "statement": title,
                    "method": "",
                    "minEvidence": _min_ev(ev),
                    "mustE3": line in MUST_E3,
                    "level": "MUST",
                    "plane": plane,
                }

    # stable order
    inv_list = [inv[k] for k in sorted(inv.keys(), key=lambda x: int(x.split("-")[1]))]
    req_list = sorted(req.values(), key=lambda r: r["id"])
    return inv_list, req_list


def main():
    doc = None
    for p in DOC_CANDIDATES:
        if p.exists():
            doc = p.read_text(encoding="utf-8")
            doc_path = str(p)
            break
    if not doc:
        print("v4 doc not found", file=sys.stderr)
        sys.exit(1)
    inv, req = parse_doc(doc)
    cat = {
        "specVersion": "2api-acceptance-v4",
        "sourceDoc": doc_path,
        "mustE3": sorted(MUST_E3),
        "invariants": inv,
        "requirements": req,
        "inv_count": len(inv),
        "req_count": len(req),
    }
    out = ROOT / "_runtime" / "v4_catalog.json"
    out.write_text(json.dumps(cat, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "inv": len(inv), "req": len(req), "out": str(out)}, ensure_ascii=False))
    if len(inv) != 18 or len(req) < 100:
        sys.exit(3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
