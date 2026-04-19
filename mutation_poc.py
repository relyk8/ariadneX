#!/usr/bin/env python3
"""
mutation_poc.py — ariadneX gym, proof-of-concept.

Submits the SAME binary twice — once with default options and once with a
user-specified option string — then diffs the IoC sets and reports novelty.

If the mutated run produces novel APIs/files/keys/mutexes that the baseline
didn't, the end-to-end mutation loop is validated and you are ready to wrap
everything in AriadneEnv.

Usage:
    python mutation_poc.py \\
        --cape-url http://192.168.182.134:8000 \\
        --binary .\\corpus\\putty.exe \\
        --mutation "full-logs=1" \\
        --show-items
"""

import argparse
import json
import os
import sys
from pathlib import Path

from baseline_submit import submit_file, poll_task, fetch_report
from novelty import extract_iocs, novelty_score, novel_items, IOC_CATEGORIES


def detonate(cape_url, token, binary, options, timeout, max_wait):
    task_id = submit_file(cape_url, token, binary, timeout)
    print(f"    task_id={task_id}  options={options!r}")
    # We duplicate the submit_file call with options here because
    # baseline_submit.submit_file hardcodes options="". Patch that in step 5.
    # For now, resubmit with options included.
    return task_id


def submit_with_options(cape_url, token, binary_path, options, timeout):
    """Clone of baseline_submit.submit_file but with options passed through."""
    import requests
    url = cape_url.rstrip("/") + "/apiv2/tasks/create/file/"
    headers = {"Authorization": f"Token {token}"} if token else {}
    with binary_path.open("rb") as f:
        files = {"file": (binary_path.name, f)}
        data = {
            "timeout": str(timeout),
            "enforce_timeout": "1",
            "options": options,
        }
        r = requests.post(url, headers=headers, files=files, data=data, timeout=60)
    r.raise_for_status()
    payload = r.json()
    task_id = (
        payload.get("data", {}).get("task_ids", [None])[0]
        or payload.get("task_id")
        or payload.get("data", {}).get("task_id")
    )
    if task_id is None:
        raise RuntimeError(f"No task_id in submission response: {payload}")
    return int(task_id)


def run_one(cape_url, token, binary, options, timeout, max_wait, label):
    print(f"[+] {label}: options={options!r}")
    task_id = submit_with_options(cape_url, token, binary, options, timeout)
    print(f"    task_id={task_id}")
    poll_task(cape_url, token, task_id, max_wait_sec=max_wait)
    report = fetch_report(cape_url, token, task_id)
    iocs = extract_iocs(report)
    for cat in IOC_CATEGORIES:
        print(f"    {label:8s} {cat:15s}  {len(iocs[cat])} items")
    return iocs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cape-url", default="http://localhost:8000")
    ap.add_argument("--api-token", default=os.environ.get("CAPE_API_TOKEN", ""))
    ap.add_argument("--binary", required=True, type=Path)
    ap.add_argument("--mutation", required=True,
                    help="Options string to test, e.g. 'full-logs=1' or 'yarascan=1'")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--max-wait", type=int, default=900)
    ap.add_argument("--show-items", action="store_true",
                    help="Print the actual novel items per category")
    args = ap.parse_args()

    base_iocs = run_one(args.cape_url, args.api_token, args.binary,
                        options="", timeout=args.timeout,
                        max_wait=args.max_wait, label="baseline")
    mut_iocs = run_one(args.cape_url, args.api_token, args.binary,
                       options=args.mutation, timeout=args.timeout,
                       max_wait=args.max_wait, label="mutated")

    forward = novelty_score(base_iocs, mut_iocs)
    reverse = novelty_score(mut_iocs, base_iocs)

    print()
    print(f"[=] novelty (mutation={args.mutation!r})")
    print(f"    {'category':15s}  {'new':>5s}  {'missing':>7s}")
    total_new = 0
    for cat in IOC_CATEGORIES:
        print(f"    {cat:15s}  {forward[cat]:>5d}  {reverse[cat]:>7d}")
        total_new += forward[cat]

    if args.show_items:
        print()
        print("[=] novel items in mutated run (first 10 per category):")
        novel = novel_items(base_iocs, mut_iocs)
        for cat in IOC_CATEGORIES:
            items = sorted(novel[cat])[:10]
            if items:
                print(f"    {cat}:")
                for it in items:
                    print(f"      + {it}")

    print()
    if total_new > 0:
        print(f"[OK] mutation surfaced {total_new} novel IoCs — framework validated.")
        return 0
    print("[WARN] zero novelty. Either the option had no effect, or it doesn't")
    print("       produce observable changes for this binary. Try another mutation")
    print("       or another binary before concluding the pipeline is broken.")
    return 2


if __name__ == "__main__":
    sys.exit(main())