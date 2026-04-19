"""
ariadne_env.py — ariadneX gym.

Minimal Gymnasium environment wrapping CAPE as the step executor and the
novelty function as the reward signal.

Design:
  * One binary per episode. reset() picks a binary, loads its stored baseline.
  * Discrete action space over 6 curated capemon option strings (see ACTIONS).
  * Reward = weighted sum of novel IoCs per category, where APIs/commands
    carry more weight than files/keys/mutexes.
  * Observation is a small fixed-length float vector: baseline sizes per
    category, episode-cumulative novelty per category, step index, and a
    one-hot over the corpus.
  * Episode ends at max_steps OR after `patience` consecutive zero-novelty
    steps (signals the agent has exhausted useful mutations on this binary).

This file is deliberately agent-agnostic. ppo_training.py plugs in PPO; here we
just drive it with random actions to validate the loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gymnasium as gym
import numpy as np
import requests
from gymnasium import spaces

from novelty import (extract_iocs, novelty_score, noise_filtered_novelty,
                     load_stable_baseline, IOC_CATEGORIES)


# ---------------------------------------------------------------------------
# Action space: six curated capemon option strings. Each is chosen because
# we have a specific hypothesis about when it should surface novelty. See
# DESIGN.md for the per-action reasoning.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Action:
    name: str
    options: str


ACTIONS: tuple[Action, ...] = (
    Action("noop",             ""),
    Action("full_logs",        "full-logs=1"),
    Action("yara_memscan",     "yarascan=1"),
    Action("expose_vm",        "no-stealth=1"),
    Action("sleepskip",        "force-sleepskip=1"),
    Action("full_plus_expose", "full-logs=1,no-stealth=1"),
)


# Per-category reward weights. Commands are the strongest behavioral signal
# (spawning new processes is rarely ambient noise); APIs are next; files/keys/
# mutexes are downweighted because Windows background activity creates them
# regardless of the sample's behavior.
REWARD_WEIGHTS: dict[str, float] = {
    "apis":          1.0,
    "commands":      2.0,
    "files_written": 0.3,
    "keys_written":  0.3,
    "mutexes":       0.3,
    "services":      1.0,
}


# ---------------------------------------------------------------------------
# CAPE client — inline rather than a separate module because this is the only
# consumer and the surface we need is tiny.
# ---------------------------------------------------------------------------
class CapeClient:
    def __init__(self, base_url: str, token: str = "", poll_sec: int = 10,
                 max_wait_sec: int = 900):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.poll_sec = poll_sec
        self.max_wait_sec = max_wait_sec

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Token {self.token}"} if self.token else {}

    def submit(self, binary: Path, options: str, timeout: int = 120) -> int:
        import time as _t
        url = self.base_url + "/apiv2/tasks/create/file/"
        with binary.open("rb") as f:
            files = {"file": (binary.name, f)}
            data = {"timeout": str(timeout), "enforce_timeout": "1", "options": options}
            r = requests.post(url, headers=self._headers, files=files, data=data, timeout=60)
        r.raise_for_status()
        payload = r.json()
        task_id = (payload.get("data", {}).get("task_ids", [None])[0]
                   or payload.get("task_id")
                   or payload.get("data", {}).get("task_id"))
        if task_id is None:
            raise RuntimeError(f"No task_id in submission response: {payload}")
        return int(task_id)

    def wait_reported(self, task_id: int) -> None:
        import time as _t
        url = self.base_url + f"/apiv2/tasks/status/{task_id}/"
        deadline = _t.time() + self.max_wait_sec
        while _t.time() < deadline:
            r = requests.get(url, headers=self._headers, timeout=30)
            r.raise_for_status()
            body = r.json()
            status = body.get("data") or body.get("status")
            if status == "reported":
                return
            if status in {"failed_analysis", "failed_processing"}:
                raise RuntimeError(f"Task {task_id} failed: {status}")
            _t.sleep(self.poll_sec)
        raise TimeoutError(f"Task {task_id} timed out before 'reported'")

    def fetch_report(self, task_id: int) -> dict:
        url = self.base_url + f"/apiv2/tasks/get/report/{task_id}/json/"
        r = requests.get(url, headers=self._headers, timeout=120)
        r.raise_for_status()
        return r.json()

    def detonate(self, binary: Path, options: str, timeout: int = 120) -> dict:
        """End-to-end: submit, wait, fetch. Returns the full CAPE report dict."""
        task_id = self.submit(binary, options, timeout)
        self.wait_reported(task_id)
        return self.fetch_report(task_id)


# ---------------------------------------------------------------------------
# The environment
# ---------------------------------------------------------------------------
class AriadneEnv(gym.Env):
    """Gymnasium environment: pick a capemon option, detonate, reward by novelty.

    Parameters
    ----------
    corpus_dir
        Directory of .exe files. Must match the binaries used to populate
        baseline_reports/.
    baseline_dir
        Directory of baseline CAPE JSON reports produced by baseline_submit.py.
    cape_client
        Live CapeClient pointed at a running CAPE instance.
    max_steps
        Hard cap on steps per episode (default 8).
    patience
        End the episode early if this many consecutive steps return zero
        weighted novelty (default 3).
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        corpus_dir: Path,
        baseline_dir: Path,
        cape_client: CapeClient,
        max_steps: int = 8,
        patience: int = 3,
        seed: int | None = None,
    ):
        super().__init__()
        self.corpus_dir = Path(corpus_dir)
        self.baseline_dir = Path(baseline_dir)
        self.cape = cape_client
        self.max_steps = max_steps
        self.patience = patience

        # Discover corpus + baselines. Order matters: the index is the one-hot
        # position in observations, so we sort for reproducibility.
        self.binaries: list[Path] = sorted(self.corpus_dir.glob("*.exe"))
        if not self.binaries:
            raise RuntimeError(f"No .exe files in {self.corpus_dir}")

        self._baselines: dict[str, dict[str, set[str]]] = {}
        self._volatile: dict[str, dict[str, set[str]]] = {}
        for b in self.binaries:
            stable_path = self.baseline_dir / f"{b.stem}.stable.json"
            single_path = self.baseline_dir / f"{b.stem}.json"
            if stable_path.exists():
                persistent, volatile = load_stable_baseline(stable_path)
                self._baselines[b.name] = persistent
                self._volatile[b.name] = volatile
            elif single_path.exists():
                self._baselines[b.name] = extract_iocs(json.loads(single_path.read_text()))
                self._volatile[b.name] = {c: set() for c in IOC_CATEGORIES}
            else:
                raise RuntimeError(f"No baseline found for {b.name} "
                                   f"(looked for {stable_path} and {single_path})")

        # Spaces. Observation layout:
        #   [baseline_size_per_category (6),
        #    cumulative_novelty_per_category (6),
        #    step_fraction (1),
        #    one_hot_binary (len(corpus))]
        self._n_cat = len(IOC_CATEGORIES)
        obs_dim = self._n_cat * 2 + 1 + len(self.binaries)
        self.observation_space = spaces.Box(low=0.0, high=np.inf,
                                            shape=(obs_dim,), dtype=np.float32)
        self.action_space = spaces.Discrete(len(ACTIONS))

        self._rng = np.random.default_rng(seed)

        # Per-episode state
        self._cur_binary_idx: int = 0
        self._cur_baseline: dict[str, set[str]] = {}
        self._cur_volatile: dict[str, set[str]] = {c: set() for c in IOC_CATEGORIES}
        self._cumulative_novelty: dict[str, int] = {c: 0 for c in IOC_CATEGORIES}
        self._step_idx: int = 0
        self._zero_streak: int = 0
        self._tried_actions: set[int] = set()

    # -------- gymnasium API --------------------------------------------------

    def reset(self, *, seed: int | None = None, options: dict | None = None
              ) -> tuple[np.ndarray, dict]:
        super().reset(seed=seed)
        if seed is not None:
            self._rng = np.random.default_rng(seed)

        forced = (options or {}).get("binary_index")
        self._cur_binary_idx = (int(forced) if forced is not None
                                else int(self._rng.integers(len(self.binaries))))
        binary = self.binaries[self._cur_binary_idx]
        self._cur_baseline = self._baselines[binary.name]
        self._cur_volatile = self._volatile[binary.name]
        self._cumulative_novelty = {c: 0 for c in IOC_CATEGORIES}
        self._step_idx = 0
        self._zero_streak = 0
        self._tried_actions = set()

        info = {"binary": binary.name,
                "baseline_sizes": {c: len(self._cur_baseline[c]) for c in IOC_CATEGORIES}}
        return self._observation(), info

    def step(self, action: int) -> tuple[np.ndarray, float, bool, bool, dict]:
        if not self.action_space.contains(action):
            raise ValueError(f"Invalid action {action}")

        act = ACTIONS[action]
        binary = self.binaries[self._cur_binary_idx]
        already_tried = action in self._tried_actions
        self._tried_actions.add(action)

        # Detonate. Any CAPE failure becomes a terminal episode with reward 0 —
        # we don't want to hand the agent a fabricated reward on error.
        try:
            report = self.cape.detonate(binary, act.options)
        except Exception as e:
            obs = self._observation()
            return obs, 0.0, True, False, {
                "binary": binary.name, "action": act.name, "error": str(e),
            }

        mut_iocs = extract_iocs(report)
        novelty = noise_filtered_novelty(self._cur_baseline, self._cur_volatile, mut_iocs)
        weighted = sum(novelty[c] * REWARD_WEIGHTS[c] for c in IOC_CATEGORIES)

        # Small penalty for wasting a step on an already-tried action. Keeps
        # the agent from collapsing onto one good action and never exploring.
        if already_tried:
            weighted -= 0.5

        for c in IOC_CATEGORIES:
            self._cumulative_novelty[c] += novelty[c]

        self._step_idx += 1
        self._zero_streak = 0 if weighted > 0 else self._zero_streak + 1

        terminated = self._zero_streak >= self.patience
        truncated = self._step_idx >= self.max_steps

        info = {
            "binary": binary.name,
            "action": act.name,
            "options": act.options,
            "novelty": novelty,
            "weighted_reward": weighted,
            "cumulative_novelty": dict(self._cumulative_novelty),
            "already_tried": already_tried,
        }
        return self._observation(), float(weighted), terminated, truncated, info

    # -------- internals ------------------------------------------------------

    def _observation(self) -> np.ndarray:
        base_sizes = [float(len(self._cur_baseline[c])) for c in IOC_CATEGORIES]
        cum = [float(self._cumulative_novelty[c]) for c in IOC_CATEGORIES]
        step_frac = [self._step_idx / max(1, self.max_steps)]
        one_hot = [0.0] * len(self.binaries)
        one_hot[self._cur_binary_idx] = 1.0
        return np.array(base_sizes + cum + step_frac + one_hot, dtype=np.float32)