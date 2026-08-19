#!/usr/bin/env python3
"""Probe the live maxapi process on :8080 and freeze deployedArtifact fields."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_BIN = ROOT / "maxapi_server.py"
CONTAINER = os.environ.get("MAXAPI_CONTAINER", "maxapi")
BASE = os.environ.get("MAXAPI_BASE", "http://127.0.0.1:8080")


def _run(cmd, timeout=20):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return -1, "", str(e)


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def _http_get(url: str, timeout=8):
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, body, headers
    except Exception as e:
        return 0, str(e).encode("utf-8", "replace"), {}


def probe() -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status, body, headers = _http_get(f"{BASE.rstrip('/')}/healthz")
    healthz = {
        "status": status,
        "body": body.decode("utf-8", "replace")[:2000],
        "headers": {
            k: headers.get(k)
            for k in (
                "x-request-id",
                "x-maxapi-commit",
                "x-maxapi-binary-sha256",
                "x-maxapi-sanitizer-config",
                "x-build-id",
                "x-git-commit",
                "x-binary-sha256",
                "content-type",
            )
            if k in headers or True
        },
    }
    # image digest from structured inspect (avoid format templates that differ by engine)
    image_id = ""
    started = ""
    cfg_image = ""
    image_digest = None
    repo_digests = []
    code2, out2, err2 = _run(["docker", "inspect", CONTAINER])
    if code2 == 0 and out2:
        try:
            info = json.loads(out2)[0]
            image_id = info.get("Image") or ""
            started = (info.get("State") or {}).get("StartedAt") or ""
            cfg_image = (info.get("Config") or {}).get("Image") or ""
            code3, out3, err3 = _run(["docker", "image", "inspect", image_id or cfg_image])
            if code3 == 0 and out3:
                im = json.loads(out3)[0]
                repo_digests = im.get("RepoDigests") or []
                # Prefer repo digest; fall back to image Id (sha256:...)
                image_digest = (repo_digests[0] if repo_digests else None) or im.get("Id") or image_id
            else:
                image_digest = image_id or f"image-inspect-failed:{err3}"
        except Exception as e:
            image_digest = f"inspect-parse-error:{e}"
    else:
        image_digest = f"container-missing:{err2}"

    # container binary
    code, out, err = _run(
        [
            "docker",
            "exec",
            CONTAINER,
            "python",
            "-c",
            "import hashlib; b=open('/app/maxapi_server.py','rb').read(); print(hashlib.sha256(b).hexdigest()); print(hashlib.md5(b).hexdigest()); print(len(b))",
        ]
    )
    bin_sha = bin_md5 = None
    if code == 0 and out:
        lines = out.splitlines()
        if len(lines) >= 2:
            bin_sha, bin_md5 = lines[0].strip(), lines[1].strip()
    host_sha = _sha256_bytes(HOST_BIN.read_bytes()) if HOST_BIN.exists() else None
    host_md5 = _md5_bytes(HOST_BIN.read_bytes()) if HOST_BIN.exists() else None

    code, commit, _ = _run(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
    if code != 0:
        commit = "unknown"

    # fingerprint headers present?
    fp_keys = [
        "x-maxapi-commit",
        "x-maxapi-binary-sha256",
        "x-maxapi-sanitizer-config",
        "x-build-id",
        "x-git-commit",
        "x-binary-sha256",
    ]
    hdr_present = any(headers.get(k) for k in fp_keys)

    # try parse healthz body for fingerprint fields
    sanitizer = None
    upstream_profile = "se.zzmax.cn-guest"
    try:
        hz = json.loads(healthz["body"])
        sanitizer = hz.get("sanitizerConfigVersion")
        if hz.get("commit"):
            pass
        if hz.get("upstreamProfile"):
            upstream_profile = hz.get("upstreamProfile")
        if hz.get("binarySha256"):
            pass
    except Exception:
        hz = {}

    host_match = bool(bin_sha and host_sha and bin_sha == host_sha)
    art = {
        "probedFrom": f"{BASE.rstrip('/')}/healthz",
        "probedAt": now,
        "imageDigest": image_digest,
        "imageRepoDigests": repo_digests,
        "binarySha256": bin_sha,
        "binaryMd5": bin_md5,
        "hostBinarySha256": host_sha,
        "hostBinaryMd5": host_md5,
        "hostMatchesContainer": host_match,
        "commit": commit,
        "commitSource": "host-git-not-in-container",
        "sanitizerConfigVersion": sanitizer,
        "upstreamProfile": upstream_profile,
        "processStartedAt": started,
        "processImage": cfg_image,
        "healthz": {"status": status, "body": healthz["body"], "parsed": hz if isinstance(hz, dict) else {}},
        "buildFingerprintHeadersPresent": hdr_present,
        "sampleHeaders": {k: headers.get(k) for k in fp_keys + ["x-request-id", "content-type"]},
        "matchesGatedBuild": host_match and status == 200 and bool(bin_sha),
        "serviceLooksLikeMaxapi": ("maxapi" in healthz["body"].lower()) or (isinstance(hz, dict) and hz.get("service") == "maxapi"),
    }
    return art


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    art = probe()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(art, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out), "binarySha256": art.get("binarySha256"), "hostMatchesContainer": art.get("hostMatchesContainer"), "matchesGatedBuild": art.get("matchesGatedBuild")}, ensure_ascii=False))
    if not art.get("serviceLooksLikeMaxapi"):
        sys.exit(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
