> **A**PI **R**esponse **I**nterposition for **A**utomated **D**iscovery of **N**ovel **Ex**ecution Paths

# The Problem
Modern, sophisticated malware rarely exposes its full capabilities in a single execution and often saves further functionality for when the environment meets a very specific state. The most famous example of this is the classic case where a malware checks if it's in a VM. In reality, such checks can quickly increase in scale and complexity, making it extremely difficult to predict if you wanted to uncover novel execution paths in a malware. This isn't rare either, as a study from last year revealed that out of 1,078 Windows malware samples, 42.39% of them were hiding functionality that was completely invisible in a default sandbox run (Pfuzzer, EuroS&P 2025). Even with samples that appeared non-evasive, 70.64% had hidden execution paths that only triggered upon exact environmental conditions.

The important part of this is that most if not all of these environmental checks happen through API calls. If we control what those API calls return (API hooking), we control the malware's perception of its "environment".  API hooking allows us to simulate different environments without reconfiguring the VM between iterations, drastically reducing per-run overhead.  Through this medium I think we can fuzz the environment itself with an RL agent to ask the question: 

- **Can we use Reinforcement Learning (RL) guided, environmental fuzzing on the API interposition layer to discover execution paths that only trigger in certain circumstances?**
# The Gap
RL-guided fuzzing is a well-explored topic in the software vulnerability discovery field, where fuzzing is of interest. The only work that I have found to explore my specific application, however, is Pfuzzer (EuroS&P 2025). They proved the concept of a coverage-guided fuzzer over environmental configurations via API hooking successfully finds hidden malware behaviors... but it has a few concrete limitations:

- **No learning**. Pfuzzer uses a heuristic-guided scheduler to decide which policies to invest in, but the actual mutation selection — like traditional fuzzing — is random. So it knows based off the heuristic when to backtrack from any given path, but it does not "learn" through any pattern recognition which types of mutations yield the highest result (novel code coverage).
- **Limited API surface**. Pfuzzer can only hook environment-query APIs due to its traditional fuzzing nature. It is not sophisticated enough to hook API requests on file contents, network data, or command-line arguments. Its own evaluation identifies 62 samples (5.75%) of its dataset that are unreachable because of this.
- **Monolithic design**. Pfuzzer is a single system, not a framework. It is rigid in the sense that you cannot swap or try different exploration strategies nor use it as a research platform.

I think that all of these limitations could be solved by making a framework for this interposition layer, with an RL-guided fuzzing agent.
# The Approach
So ariadneX would similarly hook the Windows API calls that malware uses to perceive its environment and control what gets returned.  It then measures the impact of each manipulation via code coverage feedback.  The framework is designed as a gymnasium environment, meaning the exploration logic is modular and swappable.  A simple exploration strategy can be dropped for initial PoC testing, and a full RL agent can replace it later.

Here's what I'm thinking so far.
- Malware type: Windows PE binaries
- Sandbox backend: CAPEv2 (for VM orchestration, behavioral capture, and existing API hooking infrastructure)
- Coverage instrumentation: DynamoRIO for basic block coverage (with CAPE behavioral signals as an interim proxy during development).  Pfuzzer themselves confidently said this is the best choice.
- RL framework: OpenAI Gymnasium interface, with Stable Baselines3 for agent implementations
- Language: Python (orchestration and Gym interface), leveraging CAPE's existing hooking engine for the interposition layer
