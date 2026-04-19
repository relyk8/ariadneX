#!/usr/bin/env python3
"""
sanity_check.py — ariadneX gym, step 2 validation.

Runs the SAME binary twice with DEFAULT options and compares the two IoC sets
via novelty_score. For a reproducible sandbox this should be near zero across
all categories. If it isn't, either our extraction is wrong or CAPE is
nondeterministic in ways we need to filter out before using novelty as reward.

Why this matters: the whole RL loop assumes "baseline" is a stable reference.
If unmodified reruns drift by 200 novel APIs, then a mutation that surfaces
10 novel APIs is pure noise and the agent will learn nothing useful.

Usage:
    python sanity_check.py \\
        --cape-url http://192.168.182.134:8000 \\
        --binary .\\corpus\\putty.exe \\
        --baseline baseline_reports\\putty.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

from baseline_submit import submit_file, poll_task, fetch_report
from novelty import extract_iocs, novelty_score, novel_items, IOC_CATEGORIES


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cape-url", default="http://localhost:8000")
    ap.add_argument("--api-token", default=os.environ.get("CAPE_API_TOKEN", ""))
    ap.add_argument("--binary", required=True, type=Path,
                    help="Binary to re-detonate (must already have a baseline)")
    ap.add_argument("--baseline", required=True, type=Path,
                    help="Path to the existing baseline JSON report")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--max-wait", type=int, default=900)
    ap.add_argument("--save-rerun", type=Path, default=None,
                    help="Optional: save the new report here for inspection")
    ap.add_argument("--show-items", action="store_true",
                    help="Print the actual novel items in each category")
    args = ap.parse_args()

    print(f"[+] loading baseline: {args.baseline}")
    baseline_report = json.loads(args.baseline.read_text())
    baseline_iocs = extract_iocs(baseline_report)
    for cat in IOC_CATEGORIES:
        print(f"    baseline {cat:15s}  {len(baseline_iocs[cat])} items")

    print(f"[+] re-detonating {args.binary.name} with default options")
    task_id = submit_file(args.cape_url, args.api_token, args.binary, args.timeout)
    print(f"    task_id={task_id}")
    poll_task(args.cape_url, args.api_token, task_id, max_wait_sec=args.max_wait)
    rerun_report = fetch_report(args.cape_url, args.api_token, task_id)

    if args.save_rerun:
        args.save_rerun.write_text(json.dumps(rerun_report, indent=2))
        print(f"    saved rerun to {args.save_rerun}")

    rerun_iocs = extract_iocs(rerun_report)
    for cat in IOC_CATEGORIES:
        print(f"    rerun    {cat:15s}  {len(rerun_iocs[cat])} items")

    # Novelty in BOTH directions — a good sanity check is that both are small.
    forward = novelty_score(baseline_iocs, rerun_iocs)   # new in rerun
    reverse = novelty_score(rerun_iocs, baseline_iocs)   # missing from rerun

    print()
    print(f"[=] novelty (unmodified rerun vs baseline)")
    print(f"    {'category':15s}  {'new':>5s}  {'missing':>7s}")
    for cat in IOC_CATEGORIES:
        print(f"    {cat:15s}  {forward[cat]:>5d}  {reverse[cat]:>7d}")

    if args.show_items:
        print()
        print("[=] novel items in rerun (first 10 per category):")
        novel = novel_items(baseline_iocs, rerun_iocs)
        for cat in IOC_CATEGORIES:
            items = sorted(novel[cat])[:10]
            if items:
                print(f"    {cat}:")
                for it in items:
                    print(f"      + {it}")

    # Verdict: APIs should be essentially identical. Files/keys/mutexes often
    # have a handful of TEMP-path or session-ID differences and that's OK.
    api_delta = forward["apis"] + reverse["apis"]
    if api_delta <= 5:
        print(f"\n[OK] API set reproducible ({api_delta} total delta)")
        return 0
    print(f"\n[WARN] API set drifts by {api_delta} across unmodified reruns.")
    print("       If this is consistent, your baseline needs to be an *average*")
    print("       across multiple runs, not a single reference run.")
    return 2


if __name__ == "__main__":
    sys.exit(main())