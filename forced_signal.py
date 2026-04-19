#!/usr/bin/env python3
"""
forced_signal.py — confirm the env can produce nonzero reward.

The random-drive test validated env mechanics but hit zero reward on every
step because 7z and putty are behaviorally quiet. This script forces the
known-good combo (notepad++, full_logs) to verify the reward path fires.

Usage:
    python forced_signal.py \\
        --cape-url http://192.168.182.134:8000 \\
        --corpus-dir .\\corpus \\
        --baseline-dir .\\baseline_reports
"""

import argparse
import os
import sys
from pathlib import Path

from ariadne_env import AriadneEnv, CapeClient, ACTIONS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cape-url", default="http://localhost:8000")
    ap.add_argument("--api-token", default=os.environ.get("CAPE_API_TOKEN", ""))
    ap.add_argument("--corpus-dir", required=True, type=Path)
    ap.add_argument("--baseline-dir", required=True, type=Path)
    args = ap.parse_args()

    client = CapeClient(args.cape_url, args.api_token)
    env = AriadneEnv(
        corpus_dir=args.corpus_dir,
        baseline_dir=args.baseline_dir,
        cape_client=client,
        max_steps=2,
        patience=5,   # high, so patience doesn't terminate us early
    )

    # Find notepad++ in the corpus and force reset() to pick it
    npp_idx = next(i for i, b in enumerate(env.binaries)
                   if b.name == "notepad++.exe")
    obs, info = env.reset(options={"binary_index": npp_idx})
    print(f"[+] forced reset -> {info['binary']}")
    print(f"    baseline sizes: {info['baseline_sizes']}")

    # Find the full_logs action index
    fl_idx = next(i for i, a in enumerate(ACTIONS) if a.name == "full_logs")
    print(f"[+] stepping action={ACTIONS[fl_idx].name}  options={ACTIONS[fl_idx].options!r}")
    obs, reward, terminated, truncated, info = env.step(fl_idx)

    print(f"    reward = {reward:+.2f}")
    print(f"    novelty: {info['novelty']}")
    print()
    if reward > 0:
        print(f"[OK] env produced reward > 0 on known-good combo. Ready for step 6.")
        return 0
    print(f"[WARN] reward is {reward}. Expected > 0 on notepad++ + full_logs.")
    print(f"       Check whether CAPE is still returning the same log-suppression")
    print(f"       behavior it did during step 4.")
    return 2


if __name__ == "__main__":
    sys.exit(main())