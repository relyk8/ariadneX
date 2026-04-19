#!/usr/bin/env python3
"""
ppo_training.py — plug Stable-Baselines3 PPO into AriadneEnv.

Purpose: prove the training loop runs end-to-end. Agent picks action, env
detonates, reward comes back, policy updates. We are NOT trying to converge
here — that would take days at 2-3 min per step. Success = SB3 prints
training metrics without errors.

Config notes per the stack recommendation from the research doc:
  * gamma=0.0 makes PPO behave like a contextual bandit (no future discounting).
  * n_steps must be small because collecting 2048 samples at 2 min each
    is almost 3 days. We use n_steps=8 so the first policy update comes
    after ~16-24 minutes of wall clock.

Usage:
    python ppo_training.py \\
        --cape-url http://192.168.182.134:8000 \\
        --corpus-dir .\\corpus \\
        --baseline-dir .\\baseline_reports \\
        --total-timesteps 16
"""

import argparse
import os
import sys
from pathlib import Path

from stable_baselines3 import PPO
from stable_baselines3.common.monitor import Monitor

from ariadne_env import AriadneEnv, CapeClient


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cape-url", default="http://localhost:8000")
    ap.add_argument("--api-token", default=os.environ.get("CAPE_API_TOKEN", ""))
    ap.add_argument("--corpus-dir", required=True, type=Path)
    ap.add_argument("--baseline-dir", required=True, type=Path)
    ap.add_argument("--total-timesteps", type=int, default=16,
                    help="Very small; we are proving the loop, not converging")
    ap.add_argument("--n-steps", type=int, default=8,
                    help="Steps per policy update")
    ap.add_argument("--max-episode-steps", type=int, default=4)
    ap.add_argument("--save-model", type=Path, default=Path("ariadne_ppo.zip"))
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    client = CapeClient(args.cape_url, args.api_token)
    env = AriadneEnv(
        corpus_dir=args.corpus_dir,
        baseline_dir=args.baseline_dir,
        cape_client=client,
        max_steps=args.max_episode_steps,
        patience=3,
        seed=args.seed,
    )
    env = Monitor(env)  # logs episode rewards for SB3's progress output

    print(f"[+] env ready. Estimated wall clock: "
          f"~{args.total_timesteps * 2.5:.0f}-{args.total_timesteps * 3.5:.0f} min "
          f"({args.total_timesteps} steps at 2.5-3.5 min/step).")

    model = PPO(
        "MlpPolicy",
        env,
        learning_rate=3e-4,
        n_steps=args.n_steps,
        batch_size=args.n_steps,          # match n_steps for bandit-style
        n_epochs=4,
        gamma=0.0,                         # contextual bandit (no discounting)
        gae_lambda=1.0,
        clip_range=0.2,
        verbose=1,
        seed=args.seed,
    )

    print(f"[+] starting PPO.learn(total_timesteps={args.total_timesteps})")
    model.learn(total_timesteps=args.total_timesteps, progress_bar=False)

    args.save_model.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(args.save_model))
    print(f"\n[OK] training loop ran to completion. Model saved to {args.save_model}")
    print("    This proves: action -> detonate -> reward -> policy update works.")
    print("    It does NOT prove the agent learned anything useful. That requires")
    print("    much more training (1000+ steps) and is beyond step 6's scope.")
    return 0


if __name__ == "__main__":
    sys.exit(main())