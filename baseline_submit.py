#!/usr/bin/env python3
"""
baseline_submit.py — ariadneX gym, step 1.

Submits each benign binary in --corpus-dir to CAPE with DEFAULT options
(no mutations), polls until the analysis reports, and saves the full JSON
report to --reports-dir. Also writes a manifest.json mapping binary -> task_id
so step 2 (novelty function) can iterate over them without re-guessing names.

Usage:
    export CAPE_API_TOKEN=...        # if your CAPE requires auth
    python baseline_submit.py \
        --cape-url http://localhost:8000 \
        --corpus-dir ./corpus \
        --reports-dir ./baseline_reports

Assumes CAPE REST API v2 (the default since CAPEv2). If your endpoints differ,
adjust CAPE_SUBMIT / CAPE_STATUS / CAPE_REPORT at the top.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests


# Adjust these if your CAPE install exposes different paths.
CAPE_SUBMIT = "/apiv2/tasks/create/file/"
CAPE_STATUS = "/apiv2/tasks/status/{task_id}/"
CAPE_REPORT = "/apiv2/tasks/get/report/{task_id}/json/"

TERMINAL_OK = {"reported"}  # 'completed' means analysis done but report not yet written
TERMINAL_FAIL = {"failed_analysis", "failed_processing"}


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Token {token}"} if token else {}


def submit_file(cape_url: str, token: str, file_path: Path, timeout_sec: int) -> int:
    url = cape_url.rstrip("/") + CAPE_SUBMIT
    with file_path.open("rb") as f:
        files = {"file": (file_path.name, f)}
        # options="" is the critical "no mutations" marker for the baseline.
        data = {
            "timeout": str(timeout_sec),
            "enforce_timeout": "1",
            "options": "",
        }
        r = requests.post(
            url, headers=_auth_headers(token),
            files=files, data=data, timeout=60,
        )
    r.raise_for_status()
    payload = r.json()
    # CAPE's response shape has drifted across versions; handle the common ones.
    task_id = (
        payload.get("data", {}).get("task_ids", [None])[0]
        or payload.get("task_id")
        or payload.get("data", {}).get("task_id")
    )
    if task_id is None:
        raise RuntimeError(f"No task_id in submission response: {payload}")
    return int(task_id)


def poll_task(cape_url: str, token: str, task_id: int,
              poll_sec: int = 10, max_wait_sec: int = 900) -> str:
    url = cape_url.rstrip("/") + CAPE_STATUS.format(task_id=task_id)
    deadline = time.time() + max_wait_sec
    last_status = None
    while time.time() < deadline:
        r = requests.get(url, headers=_auth_headers(token), timeout=30)
        r.raise_for_status()
        body = r.json()
        status = body.get("data") or body.get("status")
        if status != last_status:
            print(f"    status={status}")
            last_status = status
        if status in TERMINAL_OK:
            return status
        if status in TERMINAL_FAIL:
            raise RuntimeError(f"Task {task_id} failed with status={status}")
        time.sleep(poll_sec)
    raise TimeoutError(f"Task {task_id} did not reach terminal state in {max_wait_sec}s")


def fetch_report(cape_url: str, token: str, task_id: int) -> dict:
    url = cape_url.rstrip("/") + CAPE_REPORT.format(task_id=task_id)
    r = requests.get(url, headers=_auth_headers(token), timeout=120)
    r.raise_for_status()
    return r.json()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cape-url", default="http://localhost:8000",
                    help="Base URL of the CAPE web/API host")
    ap.add_argument("--api-token",
                    default=os.environ.get("CAPE_API_TOKEN", ""),
                    help="CAPE API token (or set CAPE_API_TOKEN env var)")
    ap.add_argument("--corpus-dir", required=True, type=Path,
                    help="Directory containing benign .exe files")
    ap.add_argument("--reports-dir", default=Path("./baseline_reports"), type=Path,
                    help="Where to write JSON reports and manifest")
    ap.add_argument("--timeout", type=int, default=120,
                    help="Per-sample CAPE analysis timeout (seconds)")
    ap.add_argument("--max-wait", type=int, default=900,
                    help="Max wall-clock seconds to wait per sample")
    args = ap.parse_args()

    corpus = sorted(args.corpus_dir.glob("*.exe"))
    if not corpus:
        print(f"No .exe files found in {args.corpus_dir}", file=sys.stderr)
        return 1

    args.reports_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {}

    for binary in corpus:
        print(f"[+] {binary.name}")
        entry: dict = {"path": str(binary)}
        try:
            task_id = submit_file(args.cape_url, args.api_token, binary, args.timeout)
            entry["task_id"] = task_id
            print(f"    submitted, task_id={task_id}")
            poll_task(args.cape_url, args.api_token, task_id,
                      max_wait_sec=args.max_wait)
            report = fetch_report(args.cape_url, args.api_token, task_id)
            # Sanity check: if the report came back suspiciously small, give CAPE
            # a few more seconds and refetch once. Catches the edge case where
            # 'reported' flips true before the JSON is fully flushed to disk.
            if len(json.dumps(report)) < 2048:
                print(f"    report looks tiny ({len(json.dumps(report))} bytes), retrying in 15s...")
                time.sleep(15)
                report = fetch_report(args.cape_url, args.api_token, task_id)
            out_path = args.reports_dir / f"{binary.stem}.json"
            out_path.write_text(json.dumps(report, indent=2))
            entry["report"] = str(out_path)
            size_kb = out_path.stat().st_size // 1024
            print(f"    saved {out_path}  ({size_kb} KB)")
            if size_kb < 10:
                print(f"    WARNING: report under 10 KB, may still be incomplete",
                      file=sys.stderr)
        except Exception as e:
            entry["error"] = str(e)
            print(f"    FAILED: {e}", file=sys.stderr)
        manifest[binary.name] = entry

    (args.reports_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    ok = sum(1 for v in manifest.values() if "report" in v)
    print(f"[=] done: {ok}/{len(manifest)} reports written to {args.reports_dir}")
    return 0 if ok == len(manifest) else 2


if __name__ == "__main__":
    sys.exit(main())