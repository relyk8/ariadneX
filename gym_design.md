# ariadneX — Gym Setup Design Record

> A running log of every decision, why it was made, what was considered instead, and what's left open for the thesis-phase work. Written during the initial six-step gym build on April 19, 2026.

## Project context

ariadneX is a Gymnasium-based framework for RL-guided environmental fuzzing of Windows malware in a CAPEv2 sandbox. The core research question: can an RL agent learn to pick API-hooking mutations that surface hidden execution paths in evasive malware more efficiently than Pfuzzer's random mutation selection (EuroS&P 2025)?

The DefCon deliverable is a working prototype with a contextual-bandit agent over a curated mutation space. The master's thesis extends it with learned parametric per-API mutations and the extended API surface (file contents, network data, command-line arguments) that Pfuzzer cannot reach.

## Stack

Locked in during the prior "OpenAI Gym for research" conversation based on a full research-doc comparison of Gym/Gymnasium, SB3, RLlib, TorchRL, and CleanRL:

| Component | Choice | Pinned version |
|---|---|---|
| Environment API | Gymnasium | 0.29.1 |
| RL library | Stable-Baselines3 | 2.8+ |
| Agent layer | Meta Pearl (for thesis phase) | latest |
| Sandbox | CAPEv2 | 2.5 |
| Instrumentation | capemon (CAPE's default) | shipped with CAPEv2 |
| Coverage (planned) | DynamoRIO basic-block | thesis phase |

Python orchestration, CAPE running on Ubuntu 24.04, one analysis VM.

---

## Step 1 — Baseline benign corpus

### What we did

Wrote `baseline_submit.py`. Submits each `.exe` in a corpus directory to CAPE with default options, polls until the task reaches `reported`, saves the JSON report, writes a `manifest.json`.

### Corpus

Eight portable benign binaries chosen for behavioral diversity:

- **Network-touching:** putty.exe
- **Filesystem-heavy:** 7z.exe, winmerge.exe
- **GUI-idle:** notepad++.exe, sumatrapdf.exe
- **Process-inspecting:** processhacker.exe
- **Crypto-heavy:** keepass.exe
- **Media:** vlc.exe

Report sizes ranged from 174 KB (7z, which exited quickly) to 50 MB (processhacker, which enumerates every process and its call stacks). All were valid after the refetch fix.

### Decisions made

**Why 8 binaries, not more?** Research doc suggested 5–10. Eight is enough to see per-binary variance in mutation effects without making the baseline step take hours on a single analysis VM.

**Why portable builds and not installers?** Installers execute an install routine, which adds registry writes, service creations, and file drops that have nothing to do with the target binary's normal behavior. Portable builds give us the binary's own execution signature.

**Why default options for the baseline?** The baseline is the denominator for every novelty calculation downstream. It must represent "what this binary does under CAPE's standard capture settings," with no perturbations.

### Bug we hit and fixed

CAPE's task lifecycle is `pending → running → completed → reported`, four states, not three. The script originally treated `completed` as terminal success and fetched the report immediately — for three binaries (putty, sumatrapdf, winmerge), the JSON endpoint returned a stub because CAPE's processing phase wasn't finished yet, and those reports saved as 0 KB.

Fixed by: (a) tightening `TERMINAL_OK` to just `{"reported"}`, (b) adding a post-fetch size sanity check that retries once if the returned JSON is under 2 KB, (c) writing a separate `baseline_refetch.py` to rescue the already-run-but-stuck reports without re-detonating.

### Alternatives considered

- **Could have skipped the manifest** — the script could derive filenames from the directory. Rejected: the manifest is the only place that records task IDs, which we need for refetches and future auditing.
- **Could have batched submissions asynchronously** — but there's only one analysis VM, so parallel submissions queue up anyway. Serial is simpler and equally fast with one machine.

---

## Step 2 — IoC extraction and novelty function

### What we did

Wrote `novelty.py` with three functions matching the research doc's spec:

- `extract_iocs(report)` reduces a CAPE JSON report to a dict of six sets (apis, files_written, keys_written, commands, mutexes, services).
- `novelty_score(baseline, mutated)` returns per-category counts of items present in mutated but not baseline.
- `novel_items(baseline, mutated)` returns the actual novel elements (debugging helper).

Also wrote `step2_sanity_check.py` which detonates the same binary twice with default options and verifies novelty is near zero. The reproducibility test.

### Decisions made

**Forward-only novelty, not symmetric difference.** We count what's *new* in the mutated run, not what's missing. A mutation that suppresses behavior is interesting but not the primary signal — we'll track it separately in future work if needed.

**`.get(key) or default` instead of `.get(key, default)`.** CAPE sometimes returns `None` for missing fields rather than omitting them. The `or` pattern handles both correctly.

**`IOC_CATEGORIES` as a single module-level tuple.** One source of truth for category ordering, referenced everywhere a loop over categories happens. Prevents typos from silently dropping a category.

### Sanity-check result on PuTTY

- APIs: 0 new / 0 missing — perfect reproducibility
- Files/keys/commands/services: 0 / 0
- Mutexes: 2 / 2, all `Local\SM0:<PID>:<TID>:WilError_03` or `WilStaging_02`

The mutex drift is Windows Instrumentation Logging telemetry; the session:PID:TID prefix varies each run but the semantic name is stable.

### Mutex noise filter (added after step 4)

Added a regex normalizer that replaces `SM<n>:<PID>:<TID>:` with `SM*:*:*:` before set comparison. This eliminated all false-positive mutex novelty across our tests without affecting real mutex differences.

### Alternatives considered

- **Could have diffed full JSON reports with jq or deepdiff.** Too noisy — timestamps, task IDs, report file paths, and machine metadata differ every run regardless of sample behavior. Reducing to IoC sets first filters all of that out.
- **Could have weighted novelty by API importance.** Deferred to the reward function in step 5 rather than baking it into the extraction layer. Keeps `novelty.py` pure and reusable.

---

## Step 3 — Mutation action-space design

### What we did

Read `/opt/CAPEv2/analyzer/windows/modules/packages/exe.py`, `capemon.c`, and `config.c` to understand the complete hook-option surface. Identified three layers:

1. **Analyzer package options** (exe.py): `arguments`, `appdata`, `runasx86` — control launch behavior, not observation.
2. **capemon flags** (config.c parses them, capemon.c uses them): `no-stealth`, `full-logs`, `yarascan`, `debugger`, `startup-time`, `force-sleepskip`, `force-flush`, `debug`, `exclude-apis`, `include-apis`, `dump-on-api`, etc. The real mutation surface.
3. **Not yet implemented**: Pfuzzer-style per-API mutations (Retarget / Fail / Succeed) and content-level manipulation of `ReadFile`, network recv, `GetCommandLineW`. Would require extending capemon's hook implementations.

### Decision: flat categorical action space for Phase 1

Two viable designs:

- **Option A — flat categorical.** Each action is a complete capemon option string. Maps to `Discrete(N)`. Fits a contextual bandit natively. No new capemon code needed.
- **Option B — parametric per-API.** Action is `(api_id, mutation_type, param)`. Matches Pfuzzer's framing. Requires writing custom capemon hooks first.

**Chose Option A for the DefCon prototype, Option B as thesis-phase extension.**

Reasons:
1. Gets a working end-to-end loop in days, not weeks.
2. The hardest unknowns right now — CAPE reproducibility, measurability of mutation effects — are answered without any custom hook code.
3. The README's "contextual bandit first, full RL later" evolution maps cleanly onto A → B.
4. The 62 Pfuzzer-unreachable samples are the thesis differentiator, not the DefCon differentiator.

### The six curated actions

| Action | Options | Hypothesis |
|---|---|---|
| `noop` | `""` | Control arm. Should always score 0. |
| `full_logs` | `full-logs=1` | Disables default log suppression. Should work on almost anything. |
| `yara_memscan` | `yarascan=1` | Memory signature scanning at hook load. Surfaces payload after unpacking. |
| `expose_vm` | `no-stealth=1` | Disables anti-anti-VM (PEB `NumberOfProcessors` patch). For VM-checking malware. |
| `sleepskip` | `force-sleepskip=1` | Bypasses `Sleep()`-based timing evasion. |
| `full_plus_expose` | `full-logs=1,no-stealth=1` | Interaction effect — bandits learn if combinations help. |

### Alternatives considered

- **10+ actions with more timing variants (`startup-time=5000`, `=30000`).** Trimmed to 6 after seeing null results on benign binaries. Every extra dead action wastes the bandit's exploration budget.
- **Including `include-apis` / `exclude-apis` as actions.** Deferred — these require specifying which APIs, making them parametric. They belong in Option B.
- **Underscore vs hyphen naming.** config.c uses `!strcmp(key, "no-stealth")` for strict string match. Hyphens are canonical; a handful of older options use underscores (`disable_hook_content`, `sysvol_ctimelow`). The parser does not auto-convert between them — options must match exactly.

---

## Step 4 — Manual mutation proof-of-concept

### What we did

Wrote `step4_mutation_poc.py` that submits one binary twice — default options, then a specified mutation — and diffs the IoCs.

### Three test runs

1. **PuTTY + `full-logs=1`:** zero novelty (just the mutex noise). Expected — PuTTY with no args doesn't do anything interesting to surface.
2. **PuTTY + `yarascan=1`:** zero novelty. Expected — YARA scan results go in a different report section and PuTTY's memory has no hit patterns.
3. **Notepad++ + `full-logs=1`:** 25 new APIs, 8 new files, 22 new keys, 6 new commands, 19 new mutexes. After subtracting ambient Windows noise (WMI / Explorer thumbnail cache), the real signal was:
   - Novel APIs: `BCryptImportKey`, `LsaOpenPolicy`, `CreateProcessInternalW`, `NtCreateUserProcess`, `CreateTimerQueueTimer`, etc.
   - Novel commands: spawned `svchost`, `wmiprvse`, `splwow64`
   - Novel files: notepad++ backup session files and temp files

### Decisions made

**Per-category reward weights, not uniform.** Inspecting the notepad++ novelty revealed that files/keys/mutexes are contaminated with ambient Windows background activity (WMI driver registration, Explorer thumbnail cache maintenance) that has nothing to do with the sample. APIs and spawned commands are cleaner behavioral signals. The Gym env in step 5 weights them accordingly.

**Baselines are not perfectly static.** Between step-2's notepad++ baseline (92 APIs) and step 4's re-detonation (113 APIs), the *baseline itself* shifted because of how Windows background state leaks into long-running processes. The env must recompute comparisons against the stored baseline, but we should be skeptical of any single run's "baseline" being canonical — future work should average across multiple baseline runs.

**Benign binaries are weak targets for anti-evasion mutations.** They don't check for VMs, don't sleep-stall, don't probe for debuggers. Most of the capemon evasion-defeat knobs produce zero novelty on them. This is expected, not a bug; they'll prove themselves on real malware samples in the evaluation phase.

### Alternatives considered

- **Testing every action on every binary upfront.** Would take ~96 detonations at ~3 min each → 5 hours. Not worth it; three spot-checks covered the framework-validation question.
- **Hand-picking an evasive sample for the PoC.** Tempting, but the step-4 goal is framework validation, not sample-specific results. Save evasive samples for evaluation.

---

## Step 5 — AriadneEnv

### What we did

Wrote `ariadne_env.py` containing:

- `CapeClient` — thin HTTP client over CAPE's `/apiv2/tasks/*` endpoints with `submit`, `wait_reported`, `fetch_report`, and a one-shot `detonate` helper.
- `AriadneEnv(gym.Env)` — the environment itself.
- `ACTIONS` — the six curated actions as dataclasses.
- `REWARD_WEIGHTS` — per-category reward weighting.

Wrote `step5_random_drive.py` that exercises the env with random actions to prove `reset()`, `step()`, observation, and reward all work before any agent is plugged in.

### Key design decisions

**Per-binary episodes.** `reset()` picks a binary (random, or forced via `options={"binary_index": i}`) and loads its stored baseline as context. All steps in the episode mutate that same binary. Justification: the mutation-vs-binary variance in our step-4 results makes the optimal action binary-conditional; the agent needs to see the same binary across multiple actions to learn that conditioning.

**Episode termination.** Ends at `max_steps=8` OR after `patience=3` consecutive zero-novelty steps. The patience rule prevents the agent from burning through the full episode on a binary where nothing is working (e.g., PuTTY, where most actions are dead).

**Observation layout.** `[baseline_sizes (6), cumulative_novelty (6), step_fraction (1), one_hot_binary (N)]`. Compact, fully numeric, ~20 dimensions for an 8-binary corpus. Big enough to encode context, small enough that a contextual bandit can handle it.

**Action-retry penalty of -0.5.** Prevents the agent from collapsing onto one good action on every step. Still small enough not to dominate real positive rewards.

**Reward weights.** APIs = 1.0, commands = 2.0, files/keys/mutexes = 0.3, services = 1.0. Commands weighted highest because spawning a new process is the rarest and most behaviorally diagnostic signal; the downweighted categories are where ambient Windows noise lives.

**CAPE failures terminate the episode with reward 0.** We never fabricate a reward on error — silent reward corruption is how RL experiments become un-reproducible.

### Alternatives considered

- **One-step episodes (pure bandit treatment).** Simpler, but loses the "cumulative novelty this episode" signal that lets the agent learn to not repeat itself. Per-binary multi-step is only marginally more complex and preserves the signal.
- **Image-based observation (full API call graph as a vector).** Would balloon the observation space to thousands of dimensions. Over-engineered for 8 binaries × 6 actions. Revisit for thesis phase if the action space grows.
- **Using gym.VectorEnv for parallel detonations.** Would require multiple CAPE VMs. We have one. Defer.
- **Continuous action space with a mutation-parameter vector.** Maps to Option B. Defer.

---

## Step 6 — PPO training loop

### What we did

Wrote `step6_ppo_training.py` that instantiates SB3 PPO with contextual-bandit-style hyperparameters and runs `model.learn()` for a small number of timesteps. Purpose: prove the training loop runs without error, not to converge.

### Hyperparameter choices

- `gamma=0.0` — no reward discounting. Makes PPO behave like a contextual bandit per the research-doc recommendation. Each step's reward depends only on the current action, not future actions.
- `gae_lambda=1.0` — consistent with `gamma=0.0` (no TD bootstrapping).
- `n_steps=8` — collect 8 samples before each policy update. Default is 2048, which at 3 min/step is 4+ days per update. Small `n_steps` is mandatory for slow-step environments.
- `batch_size=n_steps` — standard for small on-policy batches.
- `MlpPolicy` — default PyTorch MLP, fine for ~20-dim observations.

### What this proves and does not prove

Proves: the full action → detonate → reward → policy update pipeline works end-to-end.

Does not prove: the agent has learned anything. Meaningful convergence needs 1000+ steps, which at 3 min each is 50+ hours of wall clock. That belongs in a dedicated training run, not a step-6 validation.

### Alternatives considered

- **DQN with replay buffer instead of PPO.** Off-policy and more sample-efficient, which matters a lot at 3 min/step. Strong candidate for thesis-phase training runs. For step-6 validation, PPO is what the research doc recommended and it's the simpler baseline.
- **A pure contextual bandit library (Pearl, Vowpal Wabbit) for the DefCon prototype.** Cleaner fit for the bandit framing. Deferred because SB3 + `gamma=0.0` is sufficient for the prototype and avoids adding another dependency. Pearl will come in when we switch to the Phase 2 parametric action space.
- **Full Phase-2 agent (Pearl) from the start.** Tempting, but it entangles two changes (new action space + new agent layer). Doing them sequentially makes debugging much easier.

---

## Files produced

| File | Purpose | Status |
|---|---|---|
| `baseline_submit.py` | Step 1: submit corpus, save reports | Working, hardened against the `completed` bug |
| `baseline_refetch.py` | Repair stuck 0-KB reports from previous runs | Working, kept as a tool |
| `novelty.py` | Step 2: `extract_iocs`, `novelty_score`, `novel_items`, mutex normalizer | Working |
| `step2_sanity_check.py` | Validates reproducibility on unmodified reruns | Passed on PuTTY (0 API drift) |
| `step4_mutation_poc.py` | Step 4: single-mutation IoC diff | Passed on notepad++ + `full-logs=1` |
| `ariadne_env.py` | Step 5: `AriadneEnv` + `CapeClient` + `ACTIONS` + reward weights | Written, manual-drive validated |
| `step5_random_drive.py` | Step 5: random-action validation harness | Written |
| `step6_ppo_training.py` | Step 6: PPO with `gamma=0.0` | Written |

---

## Open questions and future scope

### Near-term (before DefCon)

1. **Baseline stability.** Our one-shot baselines drifted noticeably between runs. Thesis-phase work should average each binary's baseline across 3–5 detonations and use the intersection of API sets as the "stable" baseline. Items that appear in some baseline runs but not others are ambient noise and should not generate novelty signal.

2. **YARA results and network data in IoCs.** Currently we only read behavior.summary fields. CAPE also populates `target.file.yara`, `network.hosts`, `network.http`, `network.dns` — none of which our `extract_iocs` touches. Add these before evaluation runs against real malware, where network novelty is one of the strongest signals.

3. **Real malware samples.** All our validation has been on benign binaries. Need to run against a small curated evasive-malware subset (5–10 samples) before DefCon to confirm the mutations produce meaningful novelty on actual targets. Candidates: samples that Pfuzzer's paper identifies as their successful discoveries.

4. **Training budget.** 16 timesteps is a loop-works check, not a learning check. Decide what `total_timesteps` makes sense for a demonstrable-learning run — probably 500–1000 steps across a 10-sample evaluation corpus.

### Thesis-phase extensions

1. **Parametric action space (Option B).** Per-API mutations (Retarget / Fail / Succeed) with parameters — matches Pfuzzer's framing and enables the extended API surface the README calls out. Requires capemon C code: add a config table of `api_name → mutation_directive` and modify existing hook implementations to consult it.

2. **The extended API surface (content-level mutations).** File contents returned by `ReadFile` / `MapViewOfFile`. Network data returned by `recv` / `WinHttpReadData`. Command-line args from `GetCommandLineW` / `CommandLineToArgvW`. None of these are in capemon today — they are net new hook implementations. Pfuzzer's 5.75% unreachable samples are precisely the ones that need this surface.

3. **Coverage-guided reward via DynamoRIO.** Currently novelty is based on CAPE behavioral summaries. DynamoRIO basic-block coverage is the Pfuzzer-style signal and a stronger reward because it measures actual code execution, not just "did API X appear in logs." Requires a DynamoRIO client DLL that logs basic blocks and a coverage-tracker module.

4. **Pearl agent layer.** Per the stack recommendation, Pearl unifies contextual bandits and full RL under one API and supports the pluggable-agent design the README wants. Retrofit once the parametric action space is in place — there's no value in doing it against the 6-action discrete space.

5. **Vectorized rollouts via multiple CAPE VMs.** At 3 min per step, single-VM training is bottlenecked. Four parallel VMs gives 4× throughput. Requires a VectorEnv wrapper that handles variable step durations (some detonations finish faster than others — standard `gym.vector.AsyncVectorEnv` does not love this pattern; `SubprocVecEnv` in SB3 is more tolerant).

6. **Reward shaping against Pfuzzer's metrics.** Final evaluation compares ariadneX's discovery rate against Pfuzzer's published numbers on the same dataset. This implies the reward needs to correlate with "new execution paths found," not just "new APIs logged" — once DynamoRIO coverage is wired in, reward should transition to coverage-delta as the primary signal.

7. **Baseline drift handling.** As noted above, per-run baseline drift on long-running processes creates false-positive novelty. Thesis-phase work should include a drift-filtering layer that discounts novelty items appearing in ambient Windows background noise.

### Research questions left open

- **Does learning actually help?** The null hypothesis — "Pfuzzer's random scheduler is already close to optimal because the mutation space is too small for learning to matter" — is the single biggest risk to the thesis. Need to design an evaluation that rigorously compares learned vs random mutation selection on matched sample sets.

- **Transfer across samples.** Does a policy trained on one malware family generalize to another? This is the contextual-bandit story, but has to be demonstrated empirically.

- **Sample complexity ceiling.** How many detonations does the agent need to beat random? At 3 min/detonation, this is a wall-clock question as much as a research one.
