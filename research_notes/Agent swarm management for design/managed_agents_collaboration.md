# Managed agents combined with a locally orchestrated swarm (as of 2026-11-21)

Scope: how hosted long-horizon agents (primary target: Gemini Managed Agents, the Antigravity agent `antigravity-preview-09-2026` on the Interactions API) should be combined with a local agent swarm that drives an external engine (ngspice evaluator, KiCad/PCBWorld router). No LLM API calls were made. Sources are (1) Google docs fetched today, (2) Anthropic, OpenAI and AWS engineering docs fetched today, (3) local run logs and code in this repo. Labels used: **[LOCAL]** = measured in this repo's run logs or code; **[UNVERIFIED]** = not confirmed in a primary source today; **[INFERRED]** = my reasoning from cited facts.

Local source paths (all under `/home/thinkies/NewGit/gemini-4-hackathon/`):
- `research_notes/Gemini agents in the sizing pipeline/agents_integration.md` (call shapes, verified earlier today against docs and `google-genai==3.6.1` source)
- `sizing/managed.py` (driver), `sizing/signoff.py`, `sizing/redteam.py`
- `runs/signoff/guard_s0/{interactions.jsonl,comparison.json}`, `runs/redteam/rt1/{interactions.jsonl,summary.json,findings.json,tool_calls.jsonl,events.jsonl}`
- `pcb/repair/engine_tool.py`, `pcb/repair/swarm.py`

## Key Question 1: Gemini Managed Agents today — parallelism, shared state, continuation, streaming, budgets, latency and tokens, reported pitfalls

### Takeaway
Parallel managed agents are possible but undocumented as a pattern: the docs give one sandbox per run, per-invocation forking of a saved agent's base environment, and no concurrency or rate numbers, and they state that subagent delegation is not supported. So the client process must be the orchestrator. Budgets are per interaction and exclude cached tokens, which means a tool-calling agent that makes 20 client round trips gets 20 fresh budgets; the local "1.5M tokens against a 350k budget" run is mostly explained by this, not by a broken limit.

### Cited Findings

**Parallelism and forking**
- A saved agent takes `id` and `base_agent` (only `antigravity-preview-09-2026`), plus optional `agent_config`, `system_instruction`, `description`, `tools`; `base_environment` accepts `"remote"`, an existing environment ID, or a config object with `sources` and `network`. — [Building managed agents](https://aistudio.google.com/docs/custom-agents.md)
- "Each invocation forks the base environment, so every run starts clean." The recommended flow is to iterate with the base agent until the environment is right, then create the agent from that environment ID. — [Building managed agents](https://aistudio.google.com/docs/custom-agents.md); same statement in [Schmid developer guide](https://www.philschmid.de/gemini-managed-agents-developer-guide)
- Limits: "You can have up to 1000 managed agents." "Subagent delegation is not yet supported." "Agent versioning and rollback are not yet available." "For named agents, the model cannot be overridden at interaction time." — [Building managed agents](https://aistudio.google.com/docs/custom-agents.md)
- Per-invocation overrides allowed on a saved agent: `system_instruction`, `tools`, network rules (new rules fully replace the old ones); base sources are preserved. — [Building managed agents](https://aistudio.google.com/docs/custom-agents.md)
- The custom-agents page gives no concurrency limit, no rate limit, no multi-agent guidance and nothing on sharing state between agents. The Antigravity agent page says only that "Free tier projects include a free rate limit and usage quota." — [Building managed agents](https://aistudio.google.com/docs/custom-agents.md); [Antigravity agent](https://aistudio.google.com/docs/antigravity-agent.md.txt)
- `client.environments.create(from_environment="<env_id>")` exists in the installed SDK 3.6.1 source (`create_environment(mode, from_environment, custom_config, system_instruction)`). **[LOCAL, SDK source; not found in the docs text fetched today]** — `research_notes/Gemini agents in the sizing pipeline/agents_integration.md` §1.6
- Self-hosted workers: `gemini-api environments connect --environment-key ... --root-dir ... --workdir ... --command-timeout 10m --max-concurrent 4`; default `--max-concurrent` is 4 interactions sharing one `--root-dir`; default command timeout 2 min. **[LOCAL note citing the CLI repo; not re-fetched today]** — `agents_integration.md` §1.8, §1.9; [gemini-api-cli self-hosted docs](https://github.com/google-gemini/gemini-api-cli/tree/main/docs/self-hosted)
- Self-hosted creation is `POST /v1beta/environments` with `{"mode": "self_hosted"}`, returning an `id` and a one-time `environment_key`; the worker connects over outbound HTTPS with no inbound ports; hosted-only options (`sources`, `network`, `env`, `/files` snapshots) are rejected with `400 INVALID_ARGUMENT`; local mode runs `execute_bash` directly on the host unless `--disable-bash` is passed. — [Environments](https://aistudio.google.com/docs/agent-environment.md)

**Environments, lifecycle, shared state**
- Environments are decoupled from conversation context: `environment=<id>` reuses the sandbox, `previous_interaction_id` keeps the conversation, and either can be changed independently ("Omit `previous_interaction_id` to start a fresh conversation in the same workspace."). — [Schmid developer guide](https://www.philschmid.de/gemini-managed-agents-developer-guide); [Environments](https://aistudio.google.com/docs/agent-environment.md)
- Lifecycle: idle (snapshotted, stopped) after 15 minutes of inactivity; retained 7 days; expired or invalid ID returns `404`. Provisioning takes up to about 5 s. Hosted sandbox: 4 CPU cores, 16 GB RAM. — [Environments](https://aistudio.google.com/docs/agent-environment.md); [Schmid developer guide](https://www.philschmid.de/gemini-managed-agents-developer-guide)
- Sources: git repo 500 MB, Cloud Storage 2 GB, inline 1 MB per file and 2 MB total; target cannot be `/`. The agent can read only text and image files. — [Environments](https://aistudio.google.com/docs/agent-environment.md)
- File API for moving state in and out: list (`GET /files`, `recursive=true`), download (file bytes or a POSIX tar for a directory), upload via `PUT` on the `/upload/v1beta/...` prefix with `extract=true` for tar archives; `overwrite` defaults to `false` and an existing target returns `409 Conflict`. — [Environments](https://aistudio.google.com/docs/agent-environment.md)
- "Storage in sandbox environments is subject to fair usage limits" (no number). A developer reported a 429 with body "Project environment storage quota exceeded" on the first request of a day. — [Environments](https://aistudio.google.com/docs/agent-environment.md); forum report surfaced by search on [discuss.ai.google.dev](https://discuss.ai.google.dev/t/incorrect-rate-limit-display-for-free-tier/199839) **[UNVERIFIED: seen only in a search summary]**
- Network: unrestricted egress by default; `disabled` or an allowlist are available. — [Environments](https://aistudio.google.com/docs/agent-environment.md)
- "Google does not bill for hosted environment compute during the preview period." — [Environments](https://aistudio.google.com/docs/agent-environment.md)
- Remote MCP servers are a tool type (`{"type":"mcp_server","name":...,"url":...}`; Streamable HTTP only; lowercase names). **[LOCAL note]** — `agents_integration.md` §1.4

**Client-executed function tools and continuation**
- Pending client calls are function calls with no matching function result; filesystem tools also appear as function calls but "are executed automatically by the environment". The docs' second-turn examples omit `tools`, and the page does not say whether tools must be re-sent or whether several calls can be pending at once (examples handle only the first). — [Antigravity agent](https://aistudio.google.com/docs/antigravity-agent.md.txt)
- **[LOCAL]** The working driver re-sends `agent_config`, `tools` and `system_instruction` on every continuation, with a fallback that drops `tools` and `agent_config` on a 400; it filters `function_call` steps by handler name and by `call_id` not already answered. — `sizing/managed.py` lines 82-110
- **[LOCAL]** Several client calls do arrive per turn: the red-team run made 49 tool calls (37 `evaluate`, 8 `check_spec`, 4 `render_deck`) in 20 turns, about 2.5 per turn. — `runs/redteam/rt1/tool_calls.jsonl`, `interactions.jsonl`
- ADK's `ManagedAgent` wrapper does not support client-executed Python tools; use the raw Interactions API. **[LOCAL note]** — `agents_integration.md` §1.3; [ADK managed agents](https://adk.dev/agents/managed-agents/)

**Streaming and background**
- `stream=True` "returns incremental text, reasoning tokens, and tool call updates". — [Schmid developer guide](https://www.philschmid.de/gemini-managed-agents-developer-guide)
- `background=True` returns immediately; poll until `completed` or `failed`; needs `store=True` (default); cancel with the `cancel` method. — [Antigravity agent](https://aistudio.google.com/docs/antigravity-agent.md.txt)
- **[LOCAL]** One 20-turn streamed run produced 1,167 events: 719 `step.delta`, 114 `step.start`, 194 `step.stop`, 20 `interaction.created`, 20 `interaction.status_update`. `step.stop` carries a `usage` object with `total_input_tokens`, `total_cached_tokens`, `total_output_tokens`, `total_thought_tokens`, `total_tool_use_tokens`, `total_tokens`. — `runs/redteam/rt1/events.jsonl`

**Budget**
- `max_total_tokens` limits "the total number of tokens (input + output + thinking) an interaction can consume." "Cached tokens do not count toward this limit." "The limit is best-effort: actual usage may slightly exceed it depending on when the agent checks the budget between steps." On the limit the interaction returns `status: "incomplete"`. — [Antigravity agent](https://aistudio.google.com/docs/antigravity-agent.md.txt)
- A continuation after `incomplete` gets its own new budget. — [Antigravity agent (ai.google.dev)](https://ai.google.dev/gemini-api/docs/antigravity-agent) **[UNVERIFIED in full text: seen in a search summary and in the local note §1.2; the part of the page I fetched did not show it]**
- Third-party test: on 2026-11-07 a run with `max_total_tokens: 1000` ended `incomplete` at 4,976 total tokens; `budget_exceeded` did not appear (deprecated in v1beta, absent from v1). The issue itself says this single run does not establish enforcement because there was no unbudgeted comparison. — [genai-rs issue #540](https://github.com/evansenter/genai-rs/issues/540)
- Context compaction is automatic at about 135k tokens. — [Antigravity agent](https://aistudio.google.com/docs/antigravity-agent.md.txt)

**Measured latency and token use [LOCAL]**
- Sign-off verifier (Carbon, hosted sandbox, one client tool `run_spice`): 3 turns, 2 tool calls covering 16 decks, 628.1 s wall; per-turn `total_tokens` 48,274 / 57,708 / 244,341 (sum 350,323; budget flag default 400,000). Calibration reproduced the published numbers (60.593 dB, 125.43 MHz, 31.53 deg) and 15 of 15 points matched the local judge (max |dPM| 0.015 deg, max relative dUGF 0.04%). — `runs/signoff/guard_s0/comparison.json`, `interactions.jsonl`; `sizing/signoff.py`
- Red-team auditor (Carbon, hosted sandbox with 4 inline source files, three client tools): 20 turns, 2,286.6 s wall, status `completed`, 6 findings all marked confirmed (4 high, 2 medium), 37 of 40 allowed `evaluate` calls used. — `runs/redteam/rt1/summary.json`, `findings.json`
- Red-team per-turn `total_tokens` are **not cumulative**: turn 14 = 213,688, turn 15 = 1,143,690, turn 16 = 234,258, turn 19 = 272,245, turn 20 = 1,499,582. The sum over 20 turns is 5,582,147. The figure "about 1.5M" is the last turn only. — `runs/redteam/rt1/interactions.jsonl`
- The last usage record in the event stream reports `total_cached_tokens` = 1,323,054. — `runs/redteam/rt1/events.jsonl`
- Time between client round trips in the red-team run: median 58.9 s, min 13.4 s, max 779.9 s (the last turn, which wrote the report). — `runs/redteam/rt1/interactions.jsonl`
- A trivial self-hosted run completed in about 24 s (Schmid, Nov 16 demo). **[LOCAL note]** — `agents_integration.md` §1.8; [Schmid roundup](https://www.philschmid.de/google-deepmind-roundup-nov-2026)
- Docs' cost estimates per interaction: research $0.30-1.00, data processing $0.70-3.25, heavy-run ceiling about $5; Hydrogen $1.50/$7.50 and Carbon $2/$10 per 1M tokens through Nov 30. **[LOCAL note; the cost table was not in the page text I fetched today]** — `agents_integration.md` §1.2, §1.9

**Reported pitfalls (Aug-Nov 2026)**
- Background interactions returned 404 on GET right after a 200 create (around Sep 18; Google called it temporary). — [forum thread 184054](https://discuss.ai.google.dev/t/antigravity-preview-09-2026-background-interaction-returns-404-immediately-after-successful-creation/184054)
- Stuck `in_progress` background runs, `:cancel` returning 404, Environment API rejecting an AI Studio key with 403, billing UI lag; the poster asked for a hard monetary budget in addition to `max_total_tokens`. — [forum thread 185863](https://discuss.ai.google.dev/t/managed-antigravity-agent-setup-is-excessively-complex-auth-inconsistencies-stuck-interactions-unusable-cancellation-and-unclear-billing/185863)
- From Nov 12, 2026 (JST) Gemini 4 Argon and Carbon through the managed agent returned HTTP 429 on the free tier with quota shown as zero. — [forum thread 199769](https://discuss.ai.google.dev/t/question-about-free-tier-model-availability-for-gemini-4-argon-and-carbon-in-the-antigravity-managed-agent-api/199769) **[seen in search summary only]**
- A developer asked how to set thinking level / reasoning effort for the managed agent in the Interactions API (thread exists; answer not read). — [forum thread 190348](https://discuss.ai.google.dev/t/how-to-configure-thinking-level-reasoning-effort-for-antigravity-managed-agent-in-interactions-api/190348)
- Unsupported on the agent: structured output, sampling parameters, `file_search`, `computer_use`, PDF/audio/video input. **[LOCAL note]** — `agents_integration.md` §1.1

### Inferences
- **The budget "overrun" is largely an accounting effect, not a contradiction of the docs. [INFERRED]** Three facts combine: (1) the budget is per interaction; (2) each client tool round trip is a new interaction and `managed.py` re-sends `agent_config` with the full budget every time, so a 20-turn run is allowed up to 20 x 350k non-cached tokens; (3) cached tokens are excluded, and the last usage record shows 1,323,054 cached tokens. If that record belongs to turn 20 (1,499,582 total), the non-cached part is about 176,500, under 350k. What does contradict expectations is the docs' word "slightly" in the third-party 1,000 vs 4,976 test, and the absence of any run-level cap.
- **A run-level token cap must be built on the client. [INFERRED]** Keep a running sum of `usage.total_tokens - usage.total_cached_tokens` across turns, and pass `max_total_tokens = remaining` (not the original figure) on each continuation; stop answering tool calls when the remainder is exhausted. Also cap the number of turns and wall time (the driver already has `wall_s`).
- **Client tools are slow per call.** At a median of about 59 s per round trip on Carbon, a managed agent is the wrong driver for a tight loop of hundreds of engine actions. Batch work into each call (the sign-off run did 16 decks in 2 calls).
- **Parallel agents should each get their own environment** (one `"remote"` per run, or one saved agent whose base environment is forked per invocation). Nothing found today documents two interactions running at once in one hosted environment, so treat a shared hosted environment as a sequential handoff space only.
- **State sharing options, ranked by reliability for today's API [INFERRED]:** (1) client-side function tools as the single shared interface (scoreboard, engine, blackboard live on the client); (2) client copies files between environments with the file API; (3) a self-hosted worker with per-agent subdirectories of one `--root-dir`; (4) a remote MCP server (needs a public HTTPS endpoint).
- Prefer `stream=True` over `background=True`: every background failure reported on the forum involved polling or cancel, and both local runs used streaming without incident.

### Gaps
- No documented concurrency, RPM or environment-count limit for managed agents; no number for the storage quota.
- `from_environment` and `--max-concurrent` were not re-confirmed in docs text today (SDK source and CLI repo only, via the local note).
- Whether custom `function` tools reach the client when the environment is self-hosted, and whether self-hosted workers honor hooks, remain untested.
- Whether per-turn `usage.total_tokens` values are additive for billing, and the cached-token price, were not established; the true dollar cost of the 5.58M-token red-team run is unknown.
- Which turn the 1,323,054 cached-token record belongs to was not checked line by line.
- No October-November 2026 write-up of many Gemini managed agents running in parallel was found.

## Key Question 2: Collaboration patterns for managed agents plus a swarm — evidence and trade-offs

### Takeaway
Two patterns have direct local evidence (independent verifier, red-team auditor of the judge) and strong published support from Anthropic's evaluator and verification guidance. Planner-with-cheap-workers has published evidence on other stacks but Gemini's "no subagent delegation" rule forces the fan-out through a client tool. Per-region ownership with a shared scoreboard and a tool-improving agent are supported by published guidance but have no Gemini example.

### Cited Findings
- **(a) Verifier / sign-off.** Local: a Carbon managed agent wrote its own testbench, calibrated to published numbers, and agreed with the local judge on 15 of 15 PVT points in 628 s. — `runs/signoff/guard_s0/comparison.json`
- **(a)** Anthropic found that agents "reliably skew positive when grading their own work" and that "tuning a standalone evaluator to be skeptical turns out to be far more tractable" than making a generator self-critical; the evaluator was still lenient at first and needed several rounds of prompt tuning against its logs. — [Anthropic, harness design for long-running apps](https://www.anthropic.com/engineering/harness-design-long-running-apps)
- **(a)** The evaluator is worth its cost "when the task sits beyond what the current model does reliably solo"; with a stronger model Anthropic moved it from per-sprint grading to a single final pass. — [same](https://www.anthropic.com/engineering/harness-design-long-running-apps)
- **(b) Long-horizon specialist calling the swarm's tools.** Mechanism is documented (function tools pause the interaction; client returns results). — [Antigravity agent](https://aistudio.google.com/docs/antigravity-agent.md.txt). Local: both runs used exactly this, with client-side budgets in the handler (`evaluate budget exhausted`, `simulation budget exhausted`). — `sizing/redteam.py` lines 47-52; `sizing/signoff.py` line 122
- **(b)** The PCB engine already exposes the right verbs as a JSON-lines server: `open, status, nets, ripup, route, checkpoint, restore, save, score, render, close`, one process per board attempt. — `pcb/repair/engine_tool.py`
- **(c) Red-team auditor of the judge.** Local: 6 confirmed findings in one run, including a design with negative gain margin passing the guarded judge, crashed NaN runs outranking valid ones in `Swarm.score()`, and a deduplicator that quantized Cc to whole picofarads; proposed tests are saved for human triage and nothing is auto-applied. — `runs/redteam/rt1/summary.json`, `findings.json`; `sizing/redteam.py`
- **(d) Planner / orchestrator with cheap workers.** Anthropic's research system: an Opus 4 lead with Sonnet 4 subagents "outperformed single-agent Claude Opus 4 by 90.2%" on an internal research eval; "multi-agent systems use about 15x more tokens than chats"; "token usage by itself explains 80% of the variance". — [Anthropic, multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- **(d)** Fit: multi-agent systems "excel at valuable tasks that involve heavy parallelization"; "most coding tasks involve fewer truly parallelizable tasks than research"; tasks with shared context or many dependencies between agents fit poorly. — [same](https://www.anthropic.com/engineering/multi-agent-research-system)
- **(d)** Delegation brief: "Each subagent needs an objective, an output format, guidance on the tools and sources to use" plus clear task boundaries. A synchronous lead is a bottleneck: "the entire system can be blocked while waiting for a single subagent". — [same](https://www.anthropic.com/engineering/multi-agent-research-system)
- **(d)** Gemini constraint: "Subagent delegation is not yet supported." — [Building managed agents](https://aistudio.google.com/docs/custom-agents.md)
- **(d)** Cost evidence for heavy harnesses: a planner/generator/evaluator harness cost $200 over 6 hours against $9 over 20 minutes for a solo run (over 20x), with a large quality gap in favor of the harness; a later run cost $124.70 over 3 h 50 min with the planner costing $0.46. — [Anthropic, harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps)
- **(d)** Local swarm already has this shape with plain model calls: Carbon planner writes one repair plan per worker, Hydrogen workers refine it, engine processes execute, PCBWorld scores; defaults 3 Hydrogen plans plus 2 Carbon plans per round, 4 engine processes per board, 3 boards in parallel. — `pcb/repair/swarm.py`
- **(e) Several agents each owning a sub-problem.** Mechanism: one environment per run, forks of a base environment per invocation. — [Building managed agents](https://aistudio.google.com/docs/custom-agents.md). Published guidance on separation: avoid "running live tasks on the same files without using Git worktrees". — [OpenAI Codex best practices](https://learn.chatgpt.com/guides/best-practices.md). Subagents should write results to external storage and "pass lightweight references back to the coordinator" to avoid a "game of telephone". — [Anthropic, multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- **(f) Agent that improves the swarm's tooling.** Anthropic recommends feeding evaluation transcripts to an agent so it can analyze failures and refactor tool descriptions and implementations, checked against held-out evaluations that track accuracy, tool calls, runtime, tokens and errors. — [Anthropic, writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents). OpenAI recommends scheduled tasks that review recent chats and improve prompts or instructions, but only for workflows that are already reliable manually. — [OpenAI Codex best practices](https://learn.chatgpt.com/guides/best-practices.md)

### Inferences
Trade-off table (my synthesis; costs for (a) and (c) are the local measurements, the rest are estimates):

| Pattern | Evidence | Wall / tokens | Main risk | Use when |
|---|---|---|---|---|
| (a) Verifier / sign-off | Local run; Anthropic evaluator results | 628 s; 350k summed tokens | Verifier shares the swarm's blind spots if it sees the swarm's code or numbers | Final candidates only (one per board or design), never inside the inner loop |
| (b) Specialist calling swarm tools | Local mechanism proven; no published Gemini example | About 1 min per client round trip | Slow loop; per-turn budget resets; tool results bloat context | Hard residual cases the swarm failed on, with coarse tools |
| (c) Red-team of the judge | Local run, 6 confirmed findings | 2,287 s; 5.58M summed tokens (mostly cached) | Token cost; findings need human triage | Once per judge version, before trusting any leaderboard number |
| (d) Planner spawning cheap workers through a client tool | Anthropic research system (90.2%, 15x tokens); no Gemini example | One round trip per batch | Synchronous bottleneck; planner cannot steer workers mid-batch | Breadth-first search over independent candidates |
| (e) One agent per board / region / net cluster | Mechanism documented; no published example | N parallel runs; unknown concurrency limit | Conflicts on shared copper; quota 429s | Sub-problems that are truly independent (separate boards first; regions only with locks) |
| (f) Tool-improving agent | Anthropic guidance; local red-team produced `proposed_tests.py` | Offline | Agent edits the judge or overfits tools to the eval | Between runs, with a human merge step and a held-out regression set |

- The local red-team result is itself evidence for ordering: run (c) before (a). A verifier that agrees with a judge that passes an unstable op-amp proves little. **[INFERRED]**
- For (d) on Gemini, the fan-out tool should take a list of worker briefs and return a compact table plus file references; the managed agent then plans the next batch. One call per batch keeps the round-trip count low.
- For (e), start with one agent per board. Region or net-cluster ownership inside one board needs a client-side lock table, because the engine holds one board state per process (`engine_tool.py` runs one board attempt per process).

### Gaps
- No published example (Google or third party) of Gemini managed agents used as verifier, planner, or per-region owners in a swarm was found; all Gemini evidence here is local.
- No measurements comparing a managed-agent planner against the existing Carbon planner call on the PCB task.
- No evidence on how many hosted agent runs can execute in parallel before 429s.

## Key Question 3: Lessons from comparable systems (Anthropic, OpenAI, AWS)

### Takeaway
The three vendors converge on the same rules: keep durable state outside the agent's context and sandbox, expose all actions through one narrow tool interface, keep credentials out of the sandbox, isolate parallel work, verify with a separate actor against a written definition of done, and remove harness parts as models improve.

### Cited Findings
- **Anthropic, managed agents architecture.** Split the system into a session (an "append-only log of everything that happened", stored outside the context window), a stateless harness loop, and a replaceable sandbox ("The container became cattle"). — [Anthropic, managed agents](https://www.anthropic.com/engineering/managed-agents)
- One tool interface for everything: `execute(name, input) -> string`; "The harness doesn't know whether the sandbox is a container, a phone, or a Pokemon emulator." — [same](https://www.anthropic.com/engineering/managed-agents)
- Credentials stay out of reach of generated code: tokens are used at sandbox setup or held in a vault behind a proxy. — [same](https://www.anthropic.com/engineering/managed-agents)
- Recovery by log replay: a new harness calls `wake(sessionId)`, reads the log and resumes from the last event; a dead sandbox is returned to the model as a tool-call error so it can retry on a fresh one. — [same](https://www.anthropic.com/engineering/managed-agents)
- Provisioning sandboxes lazily cut p50 time-to-first-token by about 60% and p95 by over 90%. — [same](https://www.anthropic.com/engineering/managed-agents)
- Harness assumptions go stale: context resets built for one model "had become dead weight" on the next. — [same](https://www.anthropic.com/engineering/managed-agents)
- **Anthropic, three-agent harness.** Planner expands a short prompt into a spec; generator builds; evaluator tests the running product against criteria with hard thresholds. Agents hand off through files. Before each sprint, generator and evaluator agreed a contract of "done" criteria (one sprint had 27). "Every component in a harness encodes an assumption about what the model can't do on its own." — [Anthropic, harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps)
- **Anthropic, multi-agent research.** Resume from the point of error instead of restarting; combine deterministic safeguards (retries, regular checkpoints) with the model's ability to adapt to tool failure. — [Anthropic, multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- Claude Managed Agents is described as hosted long-running sessions with persistent progress; multi-agent coordination is in research preview. — [Claude Managed Agents](https://claude.com/platform/claude-managed-agents) **[from search summary]**
- **OpenAI Codex.** Write a "Done when" line; have the agent write or update tests, "run the relevant checks, confirm the result, and review the work before you accept it". Keep `AGENTS.md` short and accurate; add rules after repeated mistakes. Offload "bounded work from the main thread" to subagents. "Start with the default permissions" and loosen only for trusted workflows. Do not schedule a recurring task "before it's reliable manually". Add MCP tools one or two at a time. Keep one chat per coherent unit of work. — [OpenAI Codex best practices](https://learn.chatgpt.com/guides/best-practices.md)
- Codex cloud tasks run in isolated containers in parallel, produce a diff or PR, and cannot see uncommitted local state. — third-party guide surfaced by search, [blakecrosley.com Codex guide](https://blakecrosley.com/en/guides/codex) **[UNVERIFIED: secondary source, search summary only]**
- **AWS Bedrock AgentCore Runtime.** Each session runs in a dedicated microVM that is terminated and sanitized afterwards; default idle timeout 15 minutes; maximum lifetime up to 8 hours (range 60 to 28,800 s); the client backend must manage the mapping of users to sessions and limits such as sessions per user; agents reach tools and other agents through MCP and A2A; a runtime update does not change sessions already running. — [AgentCore runtime sessions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-sessions.html); [AgentCore lifecycle settings](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-lifecycle-settings.html); [AgentCore runtime overview](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html) **[from search summary of AWS docs; pages not fetched in full]**

### Inferences
- The Gemini design maps onto Anthropic's split: interaction chain = session, Google's loop = harness, environment = sandbox. What Gemini lacks is a durable log on the client side, so the local driver's `interactions.jsonl`, `tool_calls.jsonl` and `events.jsonl` are the session log. Keep writing them; they are what makes a crashed run resumable (`previous_interaction_id` + `environment_id` of the last logged turn).
- The 15-minute idle figure appears in both Gemini environments and AgentCore sessions. A client tool that blocks longer than that (a long routing job) risks the sandbox going idle; return early with a job handle and let the agent poll, or keep tool calls short. **[INFERRED; not tested]**
- All three vendors' guidance implies that human oversight belongs at merge points: accepting a verifier's sign-off, promoting red-team tests, changing the judge, and raising permissions.

### Gaps
- No primary OpenAI page on "hosted agents" multi-agent design or on Codex cloud parallelism limits was fetched; the cloud-task facts are from a secondary source.
- AWS pages were read only as search summaries; no AgentCore multi-agent best-practices page was found.
- Anthropic's Claude Managed Agents docs on its multi-agent preview were not read.

## Key Question 4: Tool design for agents acting on an external engine (simulator, router, CAD)

### Takeaway
Give the agent a few coarse, workflow-level tools that return compact structured data with names it can reason about, an image only on request, explicit budget counters in every reply, and checkpoint handles; enforce every limit and every secrecy rule in the client handler, not in the prompt.

### Cited Findings
- Build "a few thoughtful tools targeting specific high-impact workflows" in place of one tool per API call; return "high signal information" and avoid "low-level technical identifiers"; offer a `response_format` so the agent can choose "concise" or "detailed" (concise used about a third of the tokens); apply "pagination, range selection, filtering, and/or truncation with sensible default parameter values" (Claude Code caps tool responses at 25,000 tokens); make error messages steer the agent toward valid input; namespace tools; name parameters unambiguously; evaluate on held-out multi-step tasks before and after changes. — [Anthropic, writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- Gemini function results may be a JSON object or content blocks including images, and carry an `is_error` flag. **[LOCAL note; multimodal function results shipped Nov 5 per the changelog]** — `agents_integration.md` §1.3; [changelog](https://aistudio.google.com/docs/changelog.md)
- Hooks (hosted only): `pre_tool_execution` can deny a built-in tool call and the model sees the reason; `post_tool_execution` is for side effects; matchers cover `code_execution|view_file|write_to_file|replace_file_content|list_dir|delete_file`. **[LOCAL note]** — `agents_integration.md` §1.7; [Hooks](https://aistudio.google.com/docs/agent-hooks.md)
- **[LOCAL] Client-side budget enforcement works.** The red-team handler refuses after `max_evals` and reports `evaluate_calls_used` / `evaluate_calls_max` in every reply; the agent stopped at 37 of 40. The sign-off handler counts decks and returns `budget_used` / `budget_max`. — `sizing/redteam.py`; `sizing/signoff.py` lines 122-135
- **[LOCAL] Input filtering at the tool boundary.** `run_spice` rejects decks containing `.lib`, `.inc`, `.include`, `.temp` or `shell`, and inserts the library and temperature itself, so the agent cannot swap models or run shell commands through the simulator. — `sizing/signoff.py` (`_FORBIDDEN`, tool description)
- **[LOCAL] Truncation.** `render_deck` returns only the last 14,000 characters with a `truncated_head` flag; `evaluate` returns at most 12 failures. — `sizing/redteam.py`
- **[LOCAL] Checkpoint and rollback exist in the PCB engine.** `checkpoint()` returns an integer handle; `restore(handle)` reverts and rebuilds connectivity; `ripup(nets)` returns `removed_items`; `route(net, mode, max_attempts, layers, max_edges)` returns `connections_made`; `save(path)` then `score(path)` runs PCBWorld's own `evaluate_one` on the saved board; `render(path, dpi, mark)` writes a picture with open connections dashed and rule errors marked. Errors come back as `{"ok": false, "error": ..., "trace": ...}` with elapsed seconds. — `pcb/repair/engine_tool.py`
- **[LOCAL] Held-out answers.** The engine tool "never reads the human-routed original"; the swarm's agents "never touch geometry directly and never see the human-routed original". The red-team agent received a read-only copy of four source files only. — `pcb/repair/engine_tool.py` docstring; `pcb/repair/swarm.py` docstring; `sizing/redteam.py` (`SOURCES`)
- State handoff through files with lightweight references, not through long messages. — [Anthropic, multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system); [Anthropic, harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps)

### Inferences
Design rules for a `pcb_*` tool set exposed to a managed agent (my synthesis from the findings above):
- **Granularity.** Because a client round trip costs about a minute, do not expose `route` one net at a time as the main verb. Expose `apply_plan(steps=[{ripup:[...]},{route:{net,mode,layers}},...], checkpoint_first=true)` that runs a whole repair plan in the engine and returns one result table, and keep single-step `ripup` / `route` for fine repair. Keep `status` cheap and summary-first (`detail=false` default).
- **Structured data first, image on request.** Default reply: open nets with names and pad groups, rule-error counts by type, delta against the last checkpoint. Add `render` as a separate tool (or `include_image=true`) so the agent pays image tokens only when it needs spatial reasoning. Return the PNG as an image content block, not a file path, because the hosted agent cannot read the client's disk.
- **Checkpoints and rollback.** Return the checkpoint handle in every mutating reply; have `apply_plan` auto-restore when the score gets worse unless `keep=true`. Handles are per engine process, so name them per session.
- **Idempotency.** Key each mutating call by the interaction's `call_id` and cache the reply in the handler, so a retried continuation (after a stream drop or the 400 fallback in `managed.py`) does not rip up or route twice. `status`, `score` and `render` are naturally safe to repeat. **[not implemented locally today]**
- **Budgets on the client.** Count engine attempts, wall seconds and score calls in the handler; return `attempts_left` in every reply; refuse with a clear `is_error` message when exhausted. Add the run-level token meter from Key Question 1.
- **Held-out answers.** The agent can only see what a tool returns or what is uploaded. So: never upload the reference board or the scorer's ground truth; make `open` accept only board IDs from an allowlist that maps to swarm or baseline outputs; make `save` and `score` take no free path (the handler chooses paths under the run directory); strip any field of `score` derived from the human-routed original before returning it (the current `score` returns `_keys`, a list of up to 80 scorer field names, which should be dropped for an agent audience). With hosted sandboxes, also set `network` to an allowlist or `disabled` so the agent cannot fetch the public dataset. With a self-hosted bare-host worker this protection is absent, because `execute_bash` runs as the local user and `--root-dir` confines only file tools (`agents_integration.md` §1.8).
- **Errors.** Return engine errors as short, steering text ("net 37 has no open connections; call status to list open nets") and omit Python tracebacks.

### Gaps
- No published tool-design guidance specific to EDA engines (routers, SPICE) for LLM agents was found in this pass; rules above transfer from general agent-tool guidance plus local experience.
- Whether returning an image block from a client function result works on `antigravity-preview-09-2026` was not tested locally (docs and changelog only).
- No measurement of how tool-result size affects the per-turn token totals in the local runs.

## Key Question 5: Job allocation and concrete collaboration patterns a team can implement in under an hour

### Takeaway
Use a managed agent only where the job needs a long, self-directed write-run-debug loop or an independent actor; use plain model calls for single decisions inside the swarm; keep all engine work, budgets, scoring and the scoreboard on the local machine. Four patterns below reuse `sizing/managed.py` and `pcb/repair/engine_tool.py` nearly unchanged.

### Cited Findings
- The managed agent is an autonomous loop billed by model tokens and tools; plain model calls with function tools are a separate, cheaper surface with a deterministic client loop. — [Antigravity agent](https://aistudio.google.com/docs/antigravity-agent.md.txt); `agents_integration.md` §2 table
- Local evidence of cost per managed job: 628 s for a sign-off, 2,287 s for an audit, about 59 s median per client round trip. — `runs/signoff/guard_s0/comparison.json`; `runs/redteam/rt1/interactions.jsonl`
- The swarm's existing roles are plain calls: `gemini-decision-1` triage, `gemini-4-carbon` planner, `gemini-4-hydrogen` workers, engine processes as executors, PCBWorld as judge. — `pcb/repair/swarm.py`
- Multi-agent structures pay off for heavily parallel work and cost about 15x chat tokens. — [Anthropic, multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- A separate evaluator is more tractable to tune than a self-critical generator. — [Anthropic, harness design](https://www.anthropic.com/engineering/harness-design-long-running-apps)

### Inferences

**Which job goes where**

| Job | Best surface | Reason |
|---|---|---|
| Yes/no triage, per-candidate scoring, one repair plan from a picture | Plain model call | One decision, seconds, deterministic budget |
| Many independent candidate plans | Plain parallel calls from a thread pool | Managed-agent overhead (about 1 min per round trip, sandbox, loop) buys nothing |
| Routing, DRC, simulation, scoring, scoreboard, budgets, locks | Local worker / engine process | Deterministic, must not be editable by any agent |
| Independent sign-off with its own method | Managed agent | Needs write-run-debug loop and a separate actor |
| Audit of the judge and scorer | Managed agent | Open-ended search with feedback over source plus live tool |
| Residual hard cases the swarm failed on | Managed agent with coarse engine tools | Long horizon, benefits from its own analysis code in the sandbox |
| Tool and prompt improvement between runs | Managed agent offline, human merge | Reads transcripts, proposes patches and tests |

**Pattern 1 — PCB sign-off verifier (about 30 min; copy of `sizing/signoff.py`).** One managed agent per final board. Tools: `status`, `score`, `render` only (read-only wrappers over one engine process opened on the swarm's saved board). Task: independently count open connections and rule errors from `status` and the picture, compare with the swarm's claimed score, reply with a fenced JSON verdict. Client compares verdict to `score` and logs agreement. Limits: 6 tool calls, 300 s wall, run-level token meter. Failure handling: on timeout or `incomplete`, mark "unverified" and keep the swarm's result; never block submission on the agent.

**Pattern 2 — Residual-case specialist (about 45 min).** After the swarm finishes a board with open nets left, start one managed agent with tools `status`, `apply_plan` (batch of rip-up and route steps with automatic checkpoint and restore-on-worse), `render`, `score`. Handler owns one engine process, an attempt counter (for example 8 plans), and a `call_id` reply cache. The best board is chosen from the handler's own log by score, never from the agent's claim (same rule as `best = next(e for e in h.log if e.passed)` in the local note §5). Failure handling: any exception becomes an `is_error` result; engine crash restarts the process from the last saved board and tells the agent the checkpoint handles are void.

**Pattern 3 — Judge red-team for the PCB scorer (about 30 min; copy of `sizing/redteam.py`).** Inline the swarm's scoring glue and `engine_tool.py` as read-only sources (within the 1 MB per file, 2 MB total inline limit); tools `score_board(board_id)` and `status(board_id)` over an allowlist of saved boards plus a few deliberately broken ones. Ask for findings with reproduction steps and pytest cases; save to `proposed_tests.py` for human triage. Limits: 20 score calls, 20 turns, 1,500 s wall, and `max_total_tokens` set to the remaining run budget on each turn. Run this before publishing any PCB number.

**Pattern 4 — One agent per board with a shared scoreboard (about 45 min).** A thread pool of 2 to 3 calls to `managed.run`, each with `environment="remote"`, its own run directory, its own engine process and handler, and one extra client tool `scoreboard()` that returns the current best score per board and per method from a lock-protected dict on the client (read-only for agents; only the handler's `score` path writes it). Start with 2 in parallel and raise slowly, since no concurrency limit is documented and free-tier 429s were reported in November. Failure handling: on 429 back off and requeue the board; on 404 for an environment or interaction start a fresh run from the last saved board; log `interaction.id`, `environment_id` and usage every turn (already done by `managed.py`).

**Optional Pattern 5 — Planner through a fan-out tool (about 60 min).** A managed agent gets one tool `run_workers(briefs=[{board, strategy, nets, modes}], max=5)`; the handler runs the existing Hydrogen worker plus engine path from `pcb/repair/swarm.py` for each brief in parallel and returns a table (score, opens, errors, attempt ID). This replaces the Carbon planner call with an agent that can analyze results in its sandbox between batches. Only worth doing if planner quality is the bottleneck, because each batch adds about a minute of agent latency on top of engine time.

**Driver changes needed for all patterns (about 15 min, in `sizing/managed.py`).**
- Track cumulative non-cached tokens across turns and pass the remainder as `max_total_tokens` each turn; add a `max_turns` argument.
- Treat `incomplete`, `failed`, `cancelled`, `budget_exceeded` as terminal with a distinct return code; today only `requires_action` is distinguished.
- Cache handler replies by `call_id`.
- Catch stream exceptions, then `interactions.get(id=...)` once before retrying, so a dropped stream does not re-run a turn.

**Human oversight points.** Promote red-team tests by hand; treat a verifier disagreement as a stop-and-look event; never let an agent's tool write to the judge, the scoreboard or the held-out data; keep a recorded run for demos because preview outages were reported on the forum.

### Gaps
- Time estimates are my judgment from the size of the existing local code, not measurements.
- Patterns 1 to 5 for PCB have not been run; only the op-amp sign-off and red-team analogues have.
- Safe parallel width for Pattern 4 is unknown until probed against the live quota.
