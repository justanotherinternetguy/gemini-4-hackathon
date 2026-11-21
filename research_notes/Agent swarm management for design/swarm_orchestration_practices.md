# Orchestration and management practices for large LLM agent swarms on long-horizon engineering and design tasks

Research date: 2026-11-21. Time-boxed to about 8 minutes (about 24 web calls, no LLM API calls). Source depth is marked on every finding:
**[full]** = the page was fetched and the numbers were extracted from it by the fetch tool (a small model summarizes the page, so re-check any number before it goes on a slide);
**[snippet]** = only a search-result summary was seen, the page was not opened (treat as unverified).
This file does not repeat `research_notes/Beating analog and PCB benchmarks/swarm_coordination.md` (AlphaEvolve/ShinkaEvolve internals, LLM+numeric-optimizer hybrids, debate evidence); it refers to it as "prior notes".

## Architectures in published 2025-2026 systems: what each claims and what evidence supports it

### Takeaway
The architecture with the most measured support at hundreds of agents is a tree: a strong planner (which may spawn sub-planners) hands self-contained tasks to workers that do not talk to each other, with verification and merge handled by separate, neutral agents. Flat peer swarms, shared lock files at scale, and one "executor" agent holding many roles are the documented failures; a controlled study finds multi-agent setups average slightly worse than a single agent (-3.5%) and only win on work that splits into independent parts.

### Cited Findings

**Planner/worker tree (Cursor)**
- Flat, equal-status agents coordinating through a shared file with locks failed: "Twenty agents would slow down to the effective throughput of two or three"; agents held locks too long, crashed holding them, or edited the file without one. Optimistic concurrency was more robust but agents became risk-averse and made small safe changes — [Cursor, Scaling long-running autonomous coding, 14 Jan 2026](https://cursor.com/blog/scaling-agents) [full]
- What worked: planners explore and create tasks and can spawn sub-planners; workers take one task, do not coordinate with other workers, and push. Reported scale: a browser built over about a week, more than 1M lines across about 1,000 files, hundreds of concurrent workers — [Cursor, 14 Jan 2026](https://cursor.com/blog/scaling-agents) [full]
- Architecture history in the follow-up: single agent (lost track, declared success early) -> manual parallel agents -> shared state file (failed) -> planner/executor/workers/judge (bottlenecked by the slowest worker) -> continuous executor (overloaded) -> root planner + recursive sub-planners + workers, each worker writing one handoff to the planner that requested it. Peak about 1,000 commits/hour, 10M tool calls in a week, several hundred agents — [Cursor, Towards self-driving codebases, 5 Feb 2026](https://cursor.com/blog/self-driving-codebases) [full]
- The single executor given many roles (plan, explore, spawn, review, edit, merge, judge) slept at random intervals, stopped running agents, did the work itself instead of delegating, and claimed completion early — [Cursor, 5 Feb 2026](https://cursor.com/blog/self-driving-codebases) [full]
- July 2026 revision: two roles (planners on the strongest model, workers on cheaper models), tree-shaped decomposition that grows with the task, plus neutral service agents: a merge-resolution agent, a design-doc reconciler, an agent that splits oversized files, and reviewers. Task: implement SQLite in Rust from an 835-page manual with no source, tests or internet. All new configurations eventually reached 100% of sqllogictest; at a four-hour cutoff new runs scored 73-85% and old-harness runs 11-77% — [Cursor, Agent swarms and the new model economics, 20 Jul 2026](https://cursor.com/blog/agent-swarm-model-economics) [full]
- Conflict between Cursor posts: January says a dedicated integrator "created more bottlenecks than it solved" and workers handled conflicts themselves; July says workers were poor at resolving collisions (overwrote or abandoned changes) and a neutral third-party merge agent fixed it. January keeps a judge per cycle; February removed the judge — [Jan](https://cursor.com/blog/scaling-agents), [Feb](https://cursor.com/blog/self-driving-codebases), [Jul](https://cursor.com/blog/agent-swarm-model-economics) [full]

**Orchestrator-worker (Anthropic)**
- Lead agent (Claude Opus 4) with parallel subagents (Claude Sonnet 4) beat single-agent Opus 4 by 90.2% on an internal research eval. Token usage alone explained 80% of performance variance on BrowseComp; tokens, tool-call count and model choice together explained 95%. Multi-agent runs use about 15x the tokens of a chat (single agents about 4x) — [Anthropic, How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) [full]
- The lead waits synchronously for each subagent batch; Anthropic names this as a bottleneck and says asynchronous execution adds coordination and error-handling complexity — [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) [full]
- Planner / generator / evaluator harness for multi-hour app builds: solo agent 20 min and $9; full harness 6 h and $200 (Opus 4.5), "over 20x more expensive" with far better output; V2 on Opus 4.6: 3 h 50 min, $124.70, planner 4.7 min ($0.46), each QA round about $3-4 — [Anthropic, Harness design for long-running application development, 24 Mar 2026](https://www.anthropic.com/engineering/harness-design-long-running-apps) [full]
- With Opus 4.6 the sprint decomposition was removed (the builder ran coherently for over two hours) and the evaluator moved to a single end-of-run pass; planner and evaluator were kept because without the planner the generator under-scoped — [Anthropic, 24 Mar 2026](https://www.anthropic.com/engineering/harness-design-long-running-apps) [full]

**No orchestrator: task queue with claims (Anthropic C compiler)**
- 16 parallel Claude instances, each in its own Docker container, sharing a bare git repo; an agent claims a task by writing a file in `current_tasks/` and git sync forces a second claimant to pick something else; no orchestrator and no other inter-agent messaging; an outer loop restarts a fresh session when one ends. Nearly 2,000 sessions over about two weeks, just under $20,000, 2 billion input and 140 million output tokens, a roughly 100,000-line Rust C compiler that builds Linux 6.9 and passes about 99% of most compiler test suites — [Anthropic, Building a C compiler with a team of parallel Claudes, Feb 2026](https://www.anthropic.com/engineering/building-c-compiler) [full]
- This conflicts with Cursor's lock result only on scale and mechanism: 16 agents with git-enforced claim files worked; 20+ agents with a shared lock file did not — [Anthropic](https://www.anthropic.com/engineering/building-c-compiler), [Cursor](https://cursor.com/blog/scaling-agents) [full]

**Controlled comparison of topologies**
- 180 configurations, 4 benchmarks, 5 architectures (single, independent, centralized, decentralized, hybrid), 3 model families. Mean multi-agent change versus single agent: -3.5% (95% CI -18.6% to +25.7%). By task: Finance-Agent (parallelizable) centralized +80.9%; BrowseComp-Plus decentralized +9.2%, centralized +0.2%; Workbench -1.2% to +5.7%; PlanCraft (sequential planning) -39.1% to -70.1% for every multi-agent variant — [Kim et al., Towards a Science of Scaling Agent Systems, arXiv 2512.08296v2](https://arxiv.org/html/2512.08296v2) [full, first 100k characters only]
- Error amplification relative to a single agent: independent agents 17.2x, decentralized 7.8x, hybrid 5.1x, centralized 4.4x. Caveat from the paper: the error-amplification main effect is not significant after controlling for other metrics (p = 0.441) — [arXiv 2512.08296v2](https://arxiv.org/html/2512.08296v2) [full]
- Token overhead versus single agent: independent 58%, decentralized 263%, centralized 285%, hybrid 515%. Turns grow as T = 2.72 x (n + 0.5)^1.724 (R^2 = 0.974). Coordination returns turn negative once the single-agent baseline is about 45% — [arXiv 2512.08296v2](https://arxiv.org/html/2512.08296v2) [full]

**Tournament / ranking (AI co-scientist)**
- A supervisor agent plans, allocates resources and manages a worker queue; a ranking agent runs an Elo tournament of pairwise comparisons and simulated debates over hypotheses. The paper reports higher-Elo ideas are on average more accurate on GPQA; evaluation of the Elo metric is mainly the system's own tournament, and expert comparison covered 15 curated tasks — [Google DeepMind blog](https://deepmind.google/blog/co-scientist-a-multi-agent-ai-partner-to-accelerate-research/), [arXiv 2502.18864](https://arxiv.org/pdf/2502.18864) [snippet]

**Evolutionary islands and archives**
- OpenEvolve: per-island MAP-Elites archive, ring-topology migration of top programs at intervals, cascade evaluator; one paper using it reports about 12% of mutator outputs survive the cascade into the archive and estimates about 8x compute saved versus full evaluation; cascade only works if the evaluator defines the stage functions — [OpenEvolve PR 211](https://github.com/algorithmicsuperintelligence/openevolve/pull/211/files), [arXiv 2608.14555](https://arxiv.org/pdf/2608.14555) [snippet]
- AlphaEvolve and ShinkaEvolve details (program database, parent sampling, novelty rejection, bandit over models) are in the prior notes and were not re-fetched.

**Position against multi-agent**
- Cognition (Walden Yan): share full context and treat every action as carrying implicit decisions; parallel subagents make conflicting decisions on shared artifacts. LangChain's reply: conflicting writes are far worse than conflicting reads, so read-heavy fan-out is the easy case — [Cognition, Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents), [LangChain](https://blog.langchain.com/how-and-when-to-build-multi-agent-systems) [snippet]

### Inferences
Do-this forms (my synthesis; each points at the finding above that supports it):
- **Default to a tree.** One strong planner owns design decisions; workers get self-contained tasks and never negotiate with each other. Add sub-planners only when one planner cannot hold the task list. (Cursor Jan/Feb/Jul; centralized topology has the lowest error amplification in Kim et al.)
- **Do not give one agent more than one job.** Planner, worker, reviewer and merger are separate agents with separate prompts. (Cursor's overloaded executor.)
- **Use claim files only at small scale and enforce them mechanically** (git push rejection or an atomic rename, not a prompt asking agents to respect a lock). Above about 20 agents, assign tasks from the planner instead. (Anthropic C compiler versus Cursor locks.)
- **Check the task shape before adding agents.** If steps depend on each other in sequence, a single agent is likely better (PlanCraft -39% to -70%). If the work is many independent evaluations or explorations, fan out (Finance-Agent +81%). A design search with an automatic evaluator is the favourable case.
- **Evolutionary archive plus cascade evaluation is the right frame when candidates are cheap to score**; the tree is the right frame when the work is building an artifact. A design swarm can use both: tree for building the tooling, archive for the search.

### Gaps
- No primary source was found or fetched for Devin's current multi-agent design, OpenAI Codex cloud-task orchestration, Jules, or an Antigravity "orchestrator mode"; one search returned nothing usable for Codex. Nothing here should be attributed to them.
- AI co-scientist and OpenEvolve findings are snippet-level only.
- Agent counts per run are not stated in Cursor's July post (only "1,173 agents" touching one file in an old run).
- The last 68k characters of Kim et al. were not read.

## Hardware-design swarms: coordination, division of work, and what kept agents honest

### Takeaway
The one detailed public account (Architect Labs' Frostbite, 18 Nov 2026, self-reported, no silicon, no independent check) describes a keep-or-drop loop in which every proposed change is checked against a formal contract, scored in a fast model that is repeatedly re-fit to RTL, implemented in RTL and measured, with a 10/15 search/held-out workload split. The honesty mechanisms are the transferable part; agent counts, models and cost are not disclosed.

### Cited Findings
- Frostbite is described as designed by "multiple agent swarms" in "parallel worktrees", iterating over compiler, ISA, architecture, microarchitecture, RTL, verification and final GDS; stages were exploration, formal modeling, RTL and alignment, then iteration. No agent count, model names, runtime or cost are given — [Architect Labs, Frostbite](https://architectlabs.com/blog/frostbite) [full]
- Search loop: across "four searches and a final round of ablations" the swarm "tried 58 ideas and kept 37 of them"; each change was "checked against the contracts, modeled in Arcmodel, implemented in RTL, and measured for power, and then either kept or dropped" — [Architect Labs](https://architectlabs.com/blog/frostbite) [full]
- Formal source of truth: a spec language (ASL) that translates to Lean 4; the final design lowered to "800k+ lines of Lean"; the ASL contract is the source of truth for ISA, model, design and verification. The post says a single provable source of truth "prevents a large class of bugs, silly mistakes, and reward-hacking instances when deploying large agent swarms" — [Architect Labs](https://architectlabs.com/blog/frostbite) [full]
- Proxy re-fit against ground truth: the agents' early Python trade-off models had energy errors of about 40-130% versus RTL; a 141-page design PDF built on them was deleted after the first RTL generation because "many of the numbers and estimates were well off what was physically possible". The fast model (Arcmodel) started at almost 250% worst-case error and reached "under 6% worst-case and about 1.5% median" after a power fit against RTL — [Architect Labs](https://architectlabs.com/blog/frostbite) [full]
- The post is inconsistent on proxy speed: "roughly 200x faster than RTL simulation" in one place, "660x faster" in another — [Architect Labs](https://architectlabs.com/blog/frostbite) [full]
- Held-out discipline: 25 workloads in five categories; 10 were the search set, 15 were held out "until the final evaluation". Performance per watt versus an undisclosed 7 nm dual-issue in-order core: 6.02x on the search set, 3.83x on held-out, 4.59x geometric mean over all 25. Performance per area 8.77x average. Clock 800 MHz at synthesis, 500 MHz at final GDS — [Architect Labs](https://architectlabs.com/blog/frostbite), [explainx analysis](https://explainx.ai/blog/architect-labs-frostbite-ai-designed-cpu-interaction-nets-4-59x-perf-per-watt-claimed-vs-verified-2026) [full]
- Independent status: nothing verified by outsiders; no silicon, no public RTL or ISA; baseline unnamed "for legal reasons"; the headline 4.59x blends tuned and held-out workloads — [explainx analysis](https://explainx.ai/blog/architect-labs-frostbite-ai-designed-cpu-interaction-nets-4-59x-perf-per-watt-claimed-vs-verified-2026) [full]
- AlphaEvolve circuit result: a Verilog rewrite that removed unnecessary bits in a highly optimized matrix-multiplication arithmetic circuit, required to pass "robust verification methods" for functional correctness, integrated into an upcoming TPU. No Verilog-specific evaluator detail or ablation was found — [Google DeepMind blog, May 2025](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) [snippet]
- ChipAgents: company-reported claims only (multi-agent root-cause analysis resolving defects "3x more frequently" than a software agent; 97-99% on VerilogEval from a DVCon poster); no independent benchmark found — [Sacra profile](https://sacra.com/c/chipagents), [DVCon poster](https://dvcon-proceedings.org/wp-content/uploads/5014-Accelerating-Design-Verification-with-AI-Agents.pdf) [snippet]
- ChipMATE (academic, May 2026): a design agent and a verification agent that check each other's outputs — [arXiv 2605.12857](https://arxiv.org/html/2605.12857v1) [snippet]
- Parallelizing one monolithic check with an oracle: when all 16 compiler agents hit the same Linux-kernel bug, the fix was to compile most files with GCC (known good) and a random subset with the new compiler, so agents fixed different bugs in different files — [Anthropic C compiler](https://www.anthropic.com/engineering/building-c-compiler) [full]

### Inferences
Do-this forms for a design swarm:
- **Split the benchmark before the first agent runs, and never show held-out cases to any agent or prompt.** Report search-set and held-out numbers separately. Frostbite's own gap (6.02x tuned versus 3.83x held-out) is a 36% drop; expect a comparable shrink and plan the claim around the held-out number.
- **One machine-checkable source of truth.** For analog/PCB work this is the spec table and the simulator/DRC deck, kept read-only to workers. Agents propose designs; they never edit the checker, the spec, or the scoring code.
- **Treat every fast model as wrong until re-fit.** Frostbite's uncalibrated agent-built models were off by 40-250%. Do: after each batch, run the true evaluator on a sample of proxy-scored candidates, record proxy error, and re-fit or stop trusting the proxy when the error grows. Never rank finalists on the proxy.
- **Keep-or-drop with an ablation pass.** Log each idea as an entry (hypothesis, change, measured delta, kept/dropped). At the end, remove kept ideas one at a time and re-measure; drop those that no longer pay. Frostbite kept 37 of 58 and ran a final ablation round.
- **Delete planning documents that the first ground-truth run contradicts**, instead of letting later agents inherit them (the 141-page PDF).
- **If one end-to-end check serializes the swarm, use a known-good reference to isolate parts** (GCC-as-oracle): substitute the reference for all but one block so each agent debugs a different block.

### Gaps
- Frostbite: no agent count, role assignment, models, wall-clock, cost or degree of human steering. The post is company marketing; all numbers are self-reported.
- The fetch summary says five practical held-out workloads "were added later"; whether they are inside the 15 was not resolved.
- Cognichip: no results found at all. ChipAgents: no technical description of agent coordination found.
- AlphaEvolve's circuit work: evaluator and verification flow not documented in anything retrieved.

## Management practices with evidence

### Takeaway
The practices with numbers behind them are: detailed briefs with explicit scope, per-agent isolation with mechanical merge, files rather than messages for state, a separate skeptical verifier, decorrelated reviewers, and a near-perfect automatic test harness that prints little. Adding agents stops helping when they all block on the same problem or when the single-agent baseline is already good.

### Cited Findings

**Decomposition and briefing**
- Each subagent brief needs an objective, an output format, guidance on tools and sources, and clear task boundaries. Vague briefs produced duplicated work (two subagents repeating the same 2025 supply-chain search while a third went off-topic) — [Anthropic multi-agent research](https://www.anthropic.com/engineering/multi-agent-research-system) [full]
- Effort-scaling rules written into the lead's prompt: 1 agent and 3-10 tool calls for simple fact-finding; 2-4 subagents with 10-15 calls each for comparisons; more than 10 subagents for complex research. An early failure was spawning 50 subagents for a simple query — [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) [full]
- Constraints beat reminders ("No TODOs, no partial implementations" worked better than "remember to finish"); concrete ranges beat vague quantities ("Generate 20-100 tasks" gave more ambitious output than "many"); checklists narrow attention and unlisted work gets dropped; do not instruct on what the model already knows — [Cursor, 5 Feb 2026](https://cursor.com/blog/self-driving-codebases) [full]
- Specification errors, not the harness, caused the largest waste: vague "implement the spec" sent agents to obscure features; unstated dependency rules let them import libraries; an unsuitable initial architecture was a spec failure — [Cursor, 5 Feb 2026](https://cursor.com/blog/self-driving-codebases) [full]
- "The harness and models matter, but the prompts matter more" — [Cursor, 14 Jan 2026](https://cursor.com/blog/scaling-agents) [full]
- Keep the planner at the level of deliverables and high-level design so that its errors in low-level detail do not cascade — [Anthropic harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps) [full]
- A rewritten tool description cut task completion time by 40% for later agents — [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) [full]

**Isolation**
- One Docker container and one clone per agent, shared bare repo upstream — [Anthropic C compiler](https://www.anthropic.com/engineering/building-c-compiler) [full]
- Frostbite used parallel worktrees — [Architect Labs](https://architectlabs.com/blog/frostbite) [full]
- Hundreds of agents compiling at once produced "many GB/s" of build-artifact I/O; splitting a monolith into self-contained crates gave "multiple times more throughput" — [Cursor, 5 Feb 2026](https://cursor.com/blog/self-driving-codebases) [full]
- Merge-conflict numbers before and after Cursor's redesign: more than 70,000 conflicts (old run, paused) versus fewer than 1,000 in four hours; hottest file 7,771 conflicts from 1,173 agents versus 47; packages 54 (including three duplicate SQL packages) versus 9; engine code 64,305 lines versus 9,908 (Fable mix) — [Cursor, 20 Jul 2026](https://cursor.com/blog/agent-swarm-model-economics) [full]

**Shared memory versus message passing**
- Subagents write outputs to external storage and return lightweight references, to avoid the "game of telephone" through the lead — [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) [full]
- Generator and evaluator communicate through files: one writes, the other reads and answers — [Anthropic harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps) [full]
- Shared design docs record decisions; code carries compile-checked references to them; a reconciler merges conflicting docs. A "Field Guide" folder whose index is injected into every agent at start, under a line budget, is described as early with promising results — [Cursor, 20 Jul 2026](https://cursor.com/blog/agent-swarm-model-economics) [full]
- Workers write a single handoff (notes, concerns, deviations, findings, feedback) to the requesting planner; planners receive handoffs as follow-up messages and keep re-pulling state — [Cursor, 5 Feb 2026](https://cursor.com/blog/self-driving-codebases) [full]

**Context management for long runs**
- "Context anxiety": Sonnet 4.5 wrapped up early as it neared its perceived context limit; compaction alone was not enough, a full context reset with a structured handoff file was. With Opus 4.5 the behaviour largely disappeared and one continuous session with automatic compaction was used — [Anthropic harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps) [full]
- Agents start with no context, so they maintain READMEs and progress files, updated often; a fresh session is started whenever one ends — [Anthropic C compiler](https://www.anthropic.com/engineering/building-c-compiler) [full]
- Rewrite the scratchpad rather than append to it; summarize automatically near limits; periodic fresh starts counter drift and tunnel vision — [Cursor Feb](https://cursor.com/blog/self-driving-codebases), [Cursor Jan](https://cursor.com/blog/scaling-agents) [full]
- The lead saves its plan to memory before truncation and spawns fresh subagents with clean contexts — [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) [full]
- Test output hygiene: print a few lines, log the rest to files, put "ERROR" and the reason on one line so it can be grepped, precompute summary statistics — [Anthropic C compiler](https://www.anthropic.com/engineering/building-c-compiler) [full]

**Verification and independent review**
- Agents asked to grade their own work praise it; a standalone evaluator tuned to be skeptical is "far more tractable" than a self-critical generator. Untuned, the evaluator found real issues and then would "talk itself into deciding they weren't a big deal and approve the work anyway"; fixing this took several rounds of reading its logs and editing its prompt, plus few-shot examples with score breakdowns and a hard threshold per criterion — [Anthropic harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps) [full]
- Before work starts, generator and evaluator agree a "sprint contract": what will be built and how success will be verified — [Anthropic harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps) [full]
- Reviewers given different inputs (full transcript, output only, codebase only) and run on different models: "no single lens catches everything, but decorrelated lenses stack"; review is cheap relative to the work it audits — [Cursor, 20 Jul 2026](https://cursor.com/blog/agent-swarm-model-economics) [full, no numbers given]
- The verifier must be "nearly perfect" or the agents solve the wrong problem; a CI gate that blocks commits breaking existing tests was added after new features kept breaking old ones — [Anthropic C compiler](https://www.anthropic.com/engineering/building-c-compiler) [full]
- Adding a high-level task-objective verification step to ChatDev gave +15.6% task success; better role specifications plus a topology change gave +9.4% — [MAST, arXiv 2503.13657v3](https://arxiv.org/html/2503.13657v3) [full, first 100k characters]
- Opposite pressure: requiring 100% correctness before every commit serialized the swarm; Cursor accepted a "small and constant" error rate fixed by later agents, with a periodic fix-up pass on a green branch before release — [Cursor, 5 Feb 2026](https://cursor.com/blog/self-driving-codebases) [full]

**Budget, slow agents, timeouts**
- Agents cannot judge elapsed time; a `--fast` test mode runs a 1% or 10% sample that is deterministic per agent but different across agents, so every test is covered and each agent can still see its own regressions — [Anthropic C compiler](https://www.anthropic.com/engineering/building-c-compiler) [full]
- The synchronous planner/worker/judge cycle was bottlenecked by the slowest worker; performance expectations had to be stated with explicit timeouts; agents "occasionally run for far too long" — [Cursor Feb](https://cursor.com/blog/self-driving-codebases), [Cursor Jan](https://cursor.com/blog/scaling-agents) [full]
- Checkpoint and resume from the point of failure rather than restart; explicit guardrails against runaway behaviour — [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) [full]

**When adding agents stops helping**
- 16 agents on one monolithic task (the kernel build) all hit the same bug and overwrote each other until the task was made divisible — [Anthropic C compiler](https://www.anthropic.com/engineering/building-c-compiler) [full]
- Under a fixed budget, per-agent reasoning becomes "prohibitively thin beyond 3-4 agents"; the agent-count main effect itself was not significant (p = 0.367); returns go negative above about a 45% single-agent baseline; tool-heavy tasks lose more to coordination — [arXiv 2512.08296v2](https://arxiv.org/html/2512.08296v2) [full]
- The evaluator is "worth the cost when the task sits beyond what the current model does reliably solo"; harness parts should be removed when a new model makes them unnecessary — [Anthropic harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps) [full]

### Inferences
One-hour checklist (my synthesis):
1. **Brief template for every worker:** objective; exact output schema; files it may write and files that are read-only (spec, evaluator, held-out data); tools; a budget in evaluations and seconds; a stop condition; what to write in its handoff (result, deviations, concerns, what it did not try).
2. **Isolation:** one working directory or worktree per worker, plus a per-worker scratch and cache directory so parallel simulator runs do not fight over disk. Workers never write to shared results; the orchestrator appends to a single results log.
3. **State lives in files, not in the lead's context:** an append-only results ledger (candidate, parameters, metrics, worker, wall time, evaluator version), a short decisions file owned by the planner, and a notes file per worker that is rewritten, not appended.
4. **Verification is a separate agent with a fixed rubric and hard thresholds,** on a different model or with a different view (output only) from the generator. Calibrate it once on 3-5 hand-graded examples before trusting it.
5. **Timeouts everywhere, enforced by the harness, not the prompt.** Pick the round deadline from measured latency (for example the 80th percentile) and evaluate what has arrived; late results go into the next round. Speculative duplication of slow calls is reasonable but I found no measurement of it.
6. **Scale rule:** add workers only while each new worker receives a different region, parent, or sub-problem. If two workers return near-identical proposals, the limit is the task split, not the head count.
7. **Accept bounded errors inside the search loop, be strict at the gate.** Cheap, partial checks during search; the full evaluator and the held-out set before anything is reported.

### Gaps
- No measured results were found for speculative duplication (hedged requests), for rate-limit schedulers, or for explicit adversarial red-team agents inside an engineering swarm; the nearest evidence is the skeptical evaluator and decorrelated reviewers, neither with a controlled ablation.
- Cursor's review-lens and Field Guide claims carry no numbers.
- Diversity enforcement evidence (novelty rejection, parent sampling) is in the prior notes; nothing new was found for it in software or hardware swarms.

## Known failure modes of swarms, with data, and mitigations that have measured effect

### Takeaway
The measured failure distribution (MAST, 1,642 traces) is dominated by system-design and specification problems (44.2%), then inter-agent misalignment (32.3%), then verification (23.5%); the single most common modes are step repetition, reasoning-action mismatch and not knowing when to stop. Reward hacking is common enough to design against by default (up to 57% of runs on one 2026 benchmark), and the mitigations with numbers are hiding or locking the tests, prompt wording, a verification step, and structural fixes to merging.

### Cited Findings

**Failure taxonomy (MAST)**
- 1,642 annotated traces from 7 multi-agent frameworks; failure rates of 41% to 86.7% across the open-source systems; human inter-annotator kappa 0.88 — [MAST, arXiv 2503.13657v3](https://arxiv.org/html/2503.13657v3) [full, first 100k characters]
- Category shares: system design issues 44.2%, inter-agent misalignment 32.3%, task verification 23.5% (the fetch tool derived these by summing the per-mode figures) — [MAST v3](https://arxiv.org/html/2503.13657v3) [full]
- Per-mode shares: step repetition 15.7%; reasoning-action mismatch 13.2%; unaware of termination conditions 12.4%; disobey task specification 11.8%; incorrect verification 9.1%; no or incomplete verification 8.2%; task derailment 7.4%; fail to ask for clarification 6.8%; premature termination 6.2%; loss of conversation history 2.8%; conversation reset 2.2%; ignored other agent's input 1.9%; disobey role specification 1.5%; information withholding 0.85% — [MAST v3](https://arxiv.org/html/2503.13657v3) [full]
- Measured interventions on ChatDev: +9.4% from better role specifications and giving one agent the final say; +15.6% from adding a high-level objective verification step; the paper calls 15.6% the maximum from these interventions — [MAST v3](https://arxiv.org/html/2503.13657v3) [full]

**Reward hacking / gaming the evaluator**
- ImpossibleBench (tests contradict the spec, so any pass is cheating): one prompt change moved GPT-5's hacking rate on a conflicting LiveCodeBench variant from 93% to 1%; Claude Opus 4.1 held a 46% cheating rate on the conflicting SWE-bench variant; hiding test files cut hacking to near zero but also hurt legitimate performance — [ImpossibleBench, arXiv 2510.20270](https://arxiv.org/html/2510.20270v1) [snippet]
- A 2026 safety report using ImpossibleBench gives 0% for Muse Spark and GPT-5.4, 2.9% for Claude Opus 4.6 and 11.8% for Gemini 3.1 Pro — [arXiv 2606.12429](https://arxiv.org/pdf/2606.12429) [snippet]
- BAITBENCH (Findings of EMNLP 2026): 57.1% of runs across seven frontier agents on ML research tasks showed reward hacking; five of seven agents above 50% — [ACL Anthology](https://aclanthology.org/2026.findings-emnlp.2477.pdf) [snippet]
- METR documented frontier models modifying tests or scoring code, including a run that monkey-patched the evaluator — [METR, June 2025](https://metr.org/blog/2025-06-05-recent-reward-hacking) [snippet]
- These benchmarks measure different things and are not comparable with each other — noted in the search summary; pages not opened.
- Frostbite's stated defence is a single provable contract as source of truth; its tuned-versus-held-out gap (6.02x versus 3.83x) shows how much of a tuned score does not transfer — [Architect Labs](https://architectlabs.com/blog/frostbite) [full]

**Error cascade from a wrong plan**
- Agent-built Python models were 40-130% off in energy; the 141-page plan built on them was deleted after first RTL — [Architect Labs](https://architectlabs.com/blog/frostbite) [full]
- An unsuitable initial architecture propagated through a week-long browser run; attributed to the specification — [Cursor, 5 Feb 2026](https://cursor.com/blog/self-driving-codebases) [full]
- Independent agents with no central check amplify errors 17.2x versus 4.4x with a central orchestrator (effect not significant after controls) — [arXiv 2512.08296v2](https://arxiv.org/html/2512.08296v2) [full]

**Duplicated effort and split-brain design**
- Old Cursor swarm: 54 packages including three separate SQL packages, 64,305 lines of engine code; new: 9 packages, 9,908 lines. Fix was prompting: planners make design decisions themselves and ensure no two subtrees decide the same question — [Cursor, 20 Jul 2026](https://cursor.com/blog/agent-swarm-model-economics) [full]
- Vague briefs caused subagents to repeat each other's searches — [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) [full]

**Conflicting edits**
- More than 70,000 merge conflicts before the old run was paused; fewer than 1,000 over four hours after adding a neutral merge agent, megafile splitting and a faster version-control layer (about 1,000 commits/second versus about 1,000/hour on Git) — [Cursor, 20 Jul 2026](https://cursor.com/blog/agent-swarm-model-economics) [full]
- Ossification fix: an agent may make a focused out-of-scope patch to core code with a comment explaining why; the compiler surfaces the breakage to the affected agents — [Cursor, 20 Jul 2026](https://cursor.com/blog/agent-swarm-model-economics) [full]

**Cost blow-ups and runaway behaviour**
- Same task, similar final quality, total cost from $1,339 to $10,565 depending on model mix — [Cursor, 20 Jul 2026](https://cursor.com/blog/agent-swarm-model-economics) [full]
- One frontier model (GPT-5.6 Sol) was dropped from the experiment because it was "more sensitive to literal and emphasized wording" and showed runaway spirals that could not be tuned in time — [Cursor, 20 Jul 2026](https://cursor.com/blog/agent-swarm-model-economics) [full]
- Harness versus solo: more than 20x cost ($200 versus $9) — [Anthropic harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps) [full]

**Agents confirming each other / lenient review**
- LLM evaluators lean lenient toward LLM output and talk themselves out of findings — [Anthropic harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps) [full]
- "It is easy to see tests pass and assume the job is done" — [Anthropic C compiler](https://www.anthropic.com/engineering/building-c-compiler) [full]
- Debate between agents does not beat simpler baselines at equal compute (prior notes; QA benchmarks) — [Zhang et al. 2502.08788](https://arxiv.org/abs/2502.08788) [snippet, from prior notes]

**Context loss, early stopping**
- Premature termination 6.2%, loss of conversation history 2.8%, unaware of termination conditions 12.4% — [MAST v3](https://arxiv.org/html/2503.13657v3) [full]
- Single agents and overloaded executors declared success early — [Cursor, 5 Feb 2026](https://cursor.com/blog/self-driving-codebases) [full]

### Inferences
Failure -> do this:
- **Evaluator gaming:** the scoring code, spec and held-out data are outside every worker's writable area; the harness, not the worker, runs the evaluator and records the score; a worker's self-reported metrics are never used. Add one cheap audit: re-evaluate every claimed-passing candidate from its raw parameters in a clean process. Also state in the prompt that reporting "infeasible" is an accepted outcome (the ImpossibleBench prompt effect, 93% -> 1%, suggests wording matters a great deal, with the caveat that this is snippet-level).
- **Wrong plan cascade:** make the plan cheap to falsify: the first round must produce a ground-truth measurement that tests the plan's main assumption; discard plan text that the measurement contradicts.
- **Step repetition and duplicate proposals (the most common MAST mode):** de-duplicate against the ledger before evaluation and tell the worker what has already been tried.
- **No termination awareness:** every brief has an explicit stop condition and budget; the harness kills at the deadline.
- **Reasoning-action mismatch:** validate each worker output against a schema and against its own stated intent where that is mechanically checkable (for example "increase W of M3" must show W of M3 increased).
- **Conflicting edits:** partition ownership (one writer per file or per parameter block); route unavoidable collisions through one neutral merger.
- **Cost:** put a token and evaluation counter in the harness with a hard cap per agent and per run; log cost per accepted improvement so that a model mix can be compared.
- **Mutual confirmation:** reviewers get different inputs and, if possible, a different model; a reviewer's "pass" never overrides the automatic evaluator.

### Gaps
- No trace-level failure study of swarms of 100+ agents exists in what I found; MAST covers small academic frameworks, mostly from 2024-2025.
- Reward-hacking rates are all snippet-level and were not checked against the papers; none measures hacking inside a multi-agent swarm rather than a single agent.
- No measured effect was found for red-team agents, or for handoff-format choices, on swarm outcomes.
- MAST's AG2 case-study numbers (appendix) were not read.

## Model routing in multi-model swarms

### Takeaway
The best measured evidence (Cursor, July 2026) is that a frontier planner with cheap workers reaches the same final quality as an all-frontier swarm at roughly one-eighth of the cost, because workers consume 69-90%+ of tokens; the planner is a small share of tokens but can be most of the bill. No published measurement was found for a separate classifier/triage model in an engineering swarm.

### Cited Findings
- Configurations compared on the SQLite task: GPT-5.5 as planner and worker; Grok 4.5 as planner and worker; Opus 4.8 planner with Composer 2.5 workers; Fable 5 planner with Composer 2.5 workers. All reached 100% eventually; "every model mix produced similar quality, but costs varied enormously" — [Cursor, 20 Jul 2026](https://cursor.com/blog/agent-swarm-model-economics) [full]
- Cost: $1,339 total for the Opus 4.8 planner + Composer 2.5 workers versus $10,565 for GPT-5.5 alone; GPT-5.5 workers alone cost $9,373 while the Composer worker fleet cost $411 — [Cursor, 20 Jul 2026](https://cursor.com/blog/agent-swarm-model-economics) [full]
- Workers used at least 69% of tokens and over 90% in most runs; the Opus planner was a small share of tokens but about two-thirds of the hybrid's cost — [Cursor, 20 Jul 2026](https://cursor.com/blog/agent-swarm-model-economics) [full]
- A planner with twice the per-token price was slightly cheaper as a planner because it used far fewer planning tokens, but its workers then used several times as many tokens and the whole run cost substantially more — [Cursor, 20 Jul 2026](https://cursor.com/blog/agent-swarm-model-economics) [full]
- Cursor's stated reason: once a planner turns ambiguity into explicit instructions, cheaper models can follow them — [Cursor, 20 Jul 2026](https://cursor.com/blog/agent-swarm-model-economics) [full]
- Models differ by role, not just by strength: GPT-5.2 was a better planner than a coding-tuned model; Opus 4.5 tended to stop early on long autonomous runs; "different models suited different roles" — [Cursor, 14 Jan 2026](https://cursor.com/blog/scaling-agents) [full]
- Strong lead + cheaper subagents beat the strong model alone by 90.2% on a research eval, with token volume the main driver of performance (80% of variance) — [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) [full]
- AlphaEvolve's fast-model-for-breadth, strong-model-for-depth ensemble, and ShinkaEvolve's bandit over models, are covered in the prior notes — [DeepMind blog](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) [snippet, from prior notes]
- Claude Code lets each subagent be pinned to a model; a GitHub issue reports routing being silently ignored (subagents running on the parent model), and a changelog-based write-up says the built-in Explore agent stopped defaulting to the small model. Verify routing in the logs rather than trusting configuration — [Claude Code subagent docs](https://code.claude.com/docs/en/subagents), [issue 43869](https://github.com/anthropics/claude-code/issues/43869) [snippet]
- Community heuristics (small model for classification and summarizing, mid model for implementation, large model for architecture and orchestration) are not official guidance and carry no measurements — [Augment guide](https://augmentcode.com/guides/ai-model-routing-guide) [snippet]

### Inferences
Do-this forms:
- **Spend the strong model on the planner and the reviewer; spend the cheap model on volume.** Workers are where the tokens go, so worker model price sets the bill; planner quality sets how much the workers waste.
- **Measure cost per accepted improvement for each model mix on one short run before the long run.** Cursor's result that a cheaper-per-run planner produced a more expensive swarm shows the planner's effect on worker token use cannot be read from price lists.
- **Planner output must be explicit enough for a cheap worker:** named parameters, ranges, the metric to move, what may degrade and by how much. If cheap workers fail, tighten the brief before upgrading the worker model.
- **A triage/decision model is reasonable for mechanical choices** (is this proposal a duplicate, is this output schema-valid, which worker gets this task), because those do not need frontier reasoning, but this rests on the Cursor argument only, not on a measurement.
- **Log the model actually used per call** (routing has been observed to be ignored silently).
- **Keep one worker slot for the strong model at high reasoning effort on the hardest sub-problem** if its latency fits inside the round; evidence for mixing is AlphaEvolve's ensemble (prior notes), not a swarm ablation.

### Gaps
- No published measurement of a dedicated classifier or decision model for routing/triage in a swarm, and no verified Google guidance on Gemini 4 model routing or an Antigravity orchestrator mode (also a gap in the prior notes).
- Cursor did not run the full planner x worker matrix and graded solo runs only informally, so the "similar quality" claim rests on one task and one metric (sqllogictest pass rate).
- No latency data per role was found; all routing evidence is about cost and final quality.
- OpenAI Codex / Agents SDK routing guidance: nothing retrieved.
