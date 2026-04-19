#!/usr/bin/env python3
"""
random_drive.py — drive AriadneEnv with random actions.

This is the "manual proof" that reset(), step(), observation, reward, and
termination all work before we plug in PPO. If anything here is
broken, you want to find it now, not while an agent is training.

Usage:
    python random_drive.py \\
        --cape-url http://192.168.182.134:8000 \\
        --corpus-dir .\\corpus \\
        --baseline-dir .\\baseline_reports \\
        --episodes 2
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np

from ariadne_env import AriadneEnv, CapeClient, ACTIONS


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cape-url", default="http://localhost:8000")
    ap.add_argument("--api-token", default=os.environ.get("CAPE_API_TOKEN", ""))
    ap.add_argument("--corpus-dir", required=True, type=Path)
    ap.add_argument("--baseline-dir", required=True, type=Path)
    ap.add_argument("--episodes", type=int, default=1)
    ap.add_argument("--max-steps", type=int, default=4,
                    help="Short episodes for manual validation")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    client = CapeClient(args.cape_url, args.api_token)
    env = AriadneEnv(
        corpus_dir=args.corpus_dir,
        baseline_dir=args.baseline_dir,
        cape_client=client,
        max_steps=args.max_steps,
        patience=3,
        seed=args.seed,
    )

    rng = np.random.default_rng(args.seed)

    print(f"[+] environment ready")
    print(f"    corpus: {[b.name for b in env.binaries]}")
    print(f"    action space: {env.action_space}")
    print(f"    observation shape: {env.observation_space.shape}")
    print(f"    actions: {[a.name for a in ACTIONS]}")

    for ep in range(args.episodes):
        print(f"\n[+] episode {ep+1}/{args.episodes}")
        obs, info = env.reset(seed=args.seed + ep)
        print(f"    reset -> binary={info['binary']}")
        print(f"    baseline sizes: {info['baseline_sizes']}")
        print(f"    obs[:8] = {obs[:8].tolist()}")

        total_reward = 0.0
        for step in range(args.max_steps):
            action = int(rng.integers(env.action_space.n))
            obs, reward, terminated, truncated, info = env.step(action)
            total_reward += reward
            novelty_str = ", ".join(f"{k}={v}" for k, v in info["novelty"].items() if v)
            novelty_str = novelty_str or "none"
            flag = " [retry]" if info.get("already_tried") else ""
            print(f"    step {step+1}: action={info['action']:18s}{flag}  "
                  f"reward={reward:+.2f}  novel: {novelty_str}")
            if terminated or truncated:
                reason = "terminated (patience exhausted)" if terminated else "truncated (max_steps)"
                print(f"    episode end: {reason}")
                break
        print(f"    episode total reward: {total_reward:+.2f}")
        print(f"    cumulative novelty:   {info['cumulative_novelty']}")

    print("\n[OK] environment drove end-to-end without errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())