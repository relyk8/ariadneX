#!/usr/bin/env python3
"""
build_stable_baseline.py — multi-run baseline with noise filtering.

Detonates a single binary N times with default options and computes:
  * persistent set — items appearing in >= persistence_threshold of N runs
  * volatile set  — items appearing in some but not all runs (ambient noise)

Writes both to <reports-dir>/<binary_stem>.stable.json for use as a
noise-filtered baseline reference.

Usage:
    python build_stable_baseline.py \\
        --cape-url http://192.168.182.134:8000 \\
        --binary .\\corpus\\notepad++.exe \\
        --reports-dir .\\baseline_reports \\
        --runs 5
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

from baseline_submit import submit_file, poll_task, fetch_report
from novelty import extract_iocs, IOC_CATEGORIES


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cape-url", default="http://localhost:8000")
    ap.add_argument("--api-token", default=os.environ.get("CAPE_API_TOKEN", ""))
    ap.add_argument("--binary", required=True, type=Path)
    ap.add_argument("--reports-dir", required=True, type=Path)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--persistence-threshold", type=float, default=0.8,
                    help="Fraction of runs an item must appear in to be 'persistent'")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--max-wait", type=int, default=900)
    args = ap.parse_args()

    args.reports_dir.mkdir(parents=True, exist_ok=True)
    min_appearances = max(1, int(args.runs * args.persistence_threshold))

    print(f"[+] {args.binary.name}: {args.runs} runs, persistence threshold "
          f"{args.persistence_threshold:.0%} ({min_appearances}/{args.runs} appearances)")

    # Per-category Counter mapping item -> number of runs it appeared in
    counters: dict[str, Counter] = {c: Counter() for c in IOC_CATEGORIES}

    for i in range(args.runs):
        print(f"[+] run {i+1}/{args.runs}")
        task_id = submit_file(args.cape_url, args.api_token, args.binary, args.timeout)
        print(f"    task_id={task_id}")
        poll_task(args.cape_url, args.api_token, task_id, max_wait_sec=args.max_wait)
        report = fetch_report(args.cape_url, args.api_token, task_id)
        iocs = extract_iocs(report)
        for cat in IOC_CATEGORIES:
            counters[cat].update(iocs[cat])
            print(f"    {cat:15s}  {len(iocs[cat])} items")

    # Split into persistent (>= min_appearances) and volatile (1 <= k < min)
    persistent: dict[str, list[str]] = {}
    volatile: dict[str, list[str]] = {}
    print()
    print("[=] persistence analysis:")
    print(f"    {'category':15s}  {'persistent':>10s}  {'volatile':>8s}")
    for cat in IOC_CATEGORIES:
        p, v = [], []
        for item, count in counters[cat].items():
            (p if count >= min_appearances else v).append(item)
        persistent[cat] = sorted(p)
        volatile[cat] = sorted(v)
        print(f"    {cat:15s}  {len(p):>10d}  {len(v):>8d}")

    out = {
        "binary": args.binary.name,
        "runs": args.runs,
        "persistence_threshold": args.persistence_threshold,
        "min_appearances": min_appearances,
        "persistent": persistent,
        "volatile": volatile,
    }
    out_path = args.reports_dir / f"{args.binary.stem}.stable.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"\n[OK] wrote {out_path}")
    print(f"     persistent: the reference baseline (use this for novelty)")
    print(f"     volatile: ambient noise (items in mutated run matching these")
    print(f"     should not count as novelty)")
    return 0


if __name__ == "__main__":
    sys.exit(main())