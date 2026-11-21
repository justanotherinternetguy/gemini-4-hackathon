# Gemini agents in the analog-sizing pipeline (as of 2026-11-21)

Scope: where Gemini agents, especially **Managed Agents** (the Antigravity agent `antigravity-preview-09-2026` through the Interactions API), fit the datasheet → spec card → role labels → Carbon planner → 15-PVT ngspice evaluator → held-out eval pipeline. This note builds on `../Gemini analog sizing hackathon plan/gemini4_apis.md` and does not repeat it. No Gemini API calls were made. Facts come from docs pulled as raw markdown from aistudio.google.com (`/docs/<page>.md`), from reading the installed `google-genai==3.6.1` source in `.venv`, from the `google-gemini/gemini-api-cli` repo, from adk.dev, and from discuss.ai.google.dev threads. Items marked **[UNVERIFIED]** were not confirmed in a primary source.

## 0. TL;DR

- **New finding: a Managed Agent can call our own Python `evaluate()`.** Declare `{"type": "function", "name": "evaluate", ...}` in `tools`. The interaction stops with `status == "requires_action"`. We run ngspice locally and resume with `{"type": "function_result", "call_id": ..., "result": ...}` plus `previous_interaction_id` and the same `environment`. The agent's sandbox then never needs ngspice. This is the fallback if hosted sandboxes cannot run ngspice, and it keeps the simulation budget enforced in *our* code.
- **Self-hosted "local mode" is real and shipped** (docs plus the CLI repo). Create the environment with `client.environments.create(mode="self_hosted")`, which returns `id` and a one-time `environment_key`. Then run `gemini-api environments connect --environment-key ... --root-dir ... --workdir ...` on the laptop. The agent's `execute_bash` and file tools then run on our machine, where ngspice 47 and SKY130 are already installed. Google still runs the model and the agent loop, so this is still a "Managed Agent".
- **Hosted sandbox facts:** Ubuntu, Python 3.12, 4 vCPU / 16 GB (Schmid). pip and npm are documented; **apt/sudo are not documented**. No ngspice wheel exists on PyPI. conda-forge has `ngspice` 41 (linux-64), so `micromamba` is a plausible no-root route **[UNVERIFIED in sandbox]**. You can upload a tarball into a live environment (`client.environments.files.upload(..., extract=True)`) and fork a prepared environment (`client.environments.create(from_environment=env_id)`).
- **Top 3 to build today:** (1) **independent sign-off verifier** Managed Agent, (2) **Managed-Agent designer arm** that calls our `evaluate()` as a custom function, (3) **red-team / reward-hacking auditor** Managed Agent. Skeletons are in §5. If the hosted sandbox and the verifier come up early, the alternate #3 is the **testbench builder from datasheet page images**.

## 1. Managed Agents: exact shapes (verified against docs and SDK 3.6.1 source)

### 1.1 Create / response
```python
from google import genai
client = genai.Client(http_options={"api_version": "v1beta", "timeout": 600_000})  # timeout in ms (HttpOptions.timeout, SDK source)

it = client.interactions.create(
    agent="antigravity-preview-09-2026",          # only base agent; 05-2026 shut down Oct 5
    input="..." | [{"type": "text", "text": "..."}, {"type": "image", "mime_type": "image/png", "data": b64}],
    environment="remote" | "<env_id>" | {"type": "remote", "sources": [...], "network": ..., "env": {...}},
    agent_config={"type": "antigravity", "model": "gemini-4-hydrogen", "max_total_tokens": 150_000},
    system_instruction="...",                      # field exists on CreateAgentInteraction (SDK source)
    tools=[...],                                   # omit = defaults (code_execution, google_search, url_context, fs tools)
    previous_interaction_id=None, stream=False, background=False, store=True,
)
it.id, it.environment_id, it.status, it.output_text, it.steps, it.usage.total_tokens
```
- **CreateAgentInteraction fields** (from SDK source): `agent_config, background, continuation_token, environment, input, labels, previous_interaction_id, response_format, response_mime_type, response_modalities, safety_settings, service_tier, store, stream, system_instruction, tools, webhook_config`.
- **Statuses** (SDK enum): `in_progress, requires_action, completed, failed, cancelled, incomplete, budget_exceeded, queued, idle, requires_continuation`. Docs use `incomplete` for budget hits. Handle `budget_exceeded` too.
- **Models for `agent_config.model`:** `gemini-4-hydrogen` (default since Nov 12), `gemini-4-carbon`, `gemini-4-argon`, `gemini-4-neon`, `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.5-flash-lite`. A saved agent (`agents.create`) locks its model.
- **Not supported on the agent:** structured output (`response_format`), `temperature/top_p/top_k/stop_sequences/max_output_tokens` (all return 400), `file_search`, `computer_use`, `google_maps`. **Input is text + inline-base64 image only, with no PDF/audio/video.** The environment's file tools read only text and image files. Binary files are fine for bash.
- **Ways to get JSON out:** tell the agent to write `/workspace/out/signoff.json` and download it, or parse a fenced block from `output_text`. In self-hosted mode the file is already on our disk.

### 1.2 Multi-turn, continuation, budget
- Same sandbox and same conversation: `previous_interaction_id=it.id, environment=it.environment_id`. Fresh conversation in the same workspace: omit `previous_interaction_id`. Same conversation in a new sandbox: keep `previous_interaction_id` and set `environment="remote"`.
- Context compacts automatically at about 135k tokens.
- `max_total_tokens` counts input, output and thinking, excluding cached tokens. It is best-effort and checked between steps. When hit, the run ends with status `incomplete`. To continue: `input="continue", previous_interaction_id=..., environment=..., agent_config={... new budget}`.
- The docs' cost estimates per agent interaction: research $0.30–1.00 (100–500k in / 10–40k out). Data processing runs $0.70–3.25 (0.3–3M in), with 50–70% of input cached and a heavy-run ceiling of about $5. **Sandbox compute is not billed during preview.**

### 1.3 Custom functions (our own tool) — the key mechanism
```python
EVAL_TOOL = {"type": "function", "name": "evaluate",
  "description": "Simulate one op-amp sizing at all 15 SKY130 PVT points (5 corners x 3 temps) with ngspice. "
                 "Returns per-spec worst corner, failures, triode devices. Costs 15 sims of a fixed budget.",
  "parameters": {"type": "object", "properties": {
      "W_in": {"type": "number"}, "L_in": {"type": "number"}, "...": {"type": "number"}},
      "required": ["W_in", "L_in"]}}
it = client.interactions.create(agent=AG, input=task, environment="remote",
                                tools=[{"type": "code_execution"}, EVAL_TOOL])   # listing tools REPLACES defaults
while it.status == "requires_action":
    done = {s.call_id for s in it.steps if s.type == "function_result"}
    calls = [s for s in it.steps if s.type == "function_call" and s.id not in done and s.name == "evaluate"]
    # filesystem tools also appear as function_call steps but the environment runs them itself; filter by name
    results = [{"type": "function_result", "name": c.name, "call_id": c.id,
                "result": run_local(c.arguments)} for c in calls]        # may also be [{"type":"text",...},{"type":"image",...}]
    it = client.interactions.create(agent=AG, previous_interaction_id=it.id,
                                    environment=it.environment_id, input=results)
```
- This works only in stateful mode (`previous_interaction_id`). Manual history replay is unsupported for agents.
- `function_result.result` can be a JSON object or a list of content blocks: `text`, `image` (png/jpeg/webp), `document` (PDF/CSV/Office), `audio`, `video`. That means **`evaluate()` can return the 15-corner Bode-fan PNG to the agent**, which puts multimodality inside the loop. There is an `is_error` field for failures.
- **[UNVERIFIED]** Whether `tools` must be re-sent on agent continuations. The model docs say "Send the same `tools` array on every request". The Antigravity function-calling sample omits them on turn 2. If the agent stops seeing `evaluate` after turn 2, re-send `tools` (and `agent_config`).
- **ADK note:** `google.adk.agents.ManagedAgent` (ADK Python ≥2.4.0, preview) does **not** support client-executed Python tools (it raises `NotImplementedError`) and is streaming-only. For `evaluate()` as a tool, use the raw Interactions API, not ADK.

### 1.4 Remote MCP
`{"type": "mcp_server", "name": "spice", "url": "https://.../mcp", "headers": {...}, "allowed_tools": [...]}`. The name must match `^[a-z0-9_-]+$`, and uppercase gives a generic 400. Only Streamable HTTP is supported, not SSE. MCP servers also support `defer_loading` (tool retrieval, Nov 19). Exposing the laptop's ngspice over MCP would need a public tunnel, so a custom function is simpler today.

### 1.5 Streaming
`stream=True` yields events: `interaction.created`, `interaction.status_update`, `step.start` (with `step.type` ∈ `thought`, `model_output`, `function_call`, `code_execution_call/result`, ...), `step.delta` (`delta.type` ∈ `text`, `thought_summary`, `arguments_delta`, `image`, ...), `step.stop` (carries `usage`), `interaction.completed`, `error`. The SDK event-type constants confirm these names. Use streaming for the live demo trace. **Plain synchronous HTTP requests "typically close after 60 seconds"** (background-execution doc), so an agent run needs `stream=True` or `background=True` with polling `client.interactions.get(id=...)`. Cancel with `client.interactions.cancel(id=...)`.

### 1.6 Environments: files in/out, fork, lifecycle
- **Sources at creation:** `repository` (500 MB), `gcs` (2 GB), `inline` (1 MB/file, 2 MB total). The target must not be `/`.
- **Upload into a live environment** (SDK 3.6.1 has it):
  ```python
  with open("spicekit.tar.gz", "rb") as f:   # sky130 combined models are 3.3 MB on disk: too big for inline, fine here
      client.environments.files.upload(environment=env_id, path="workspace/spice/", file=f, extract=True)
  ```
  REST uses `PUT https://generativelanguage.googleapis.com/upload/v1beta/environments/{id}/files/{path}?extract=true&overwrite=true` (the `/upload/` prefix is required). Resumable upload is available for large files.
- **Download:** `client.environments.files.download(environment=env_id, path="workspace/out")` returns tar bytes for a directory and raw bytes for a file. `client.environments.files.list(environment=env_id, path="", recursive=True)`. The legacy REST route `/v1beta/files/environment-{id}:download?alt=media` still appears in the docs.
- **Create / fork:** `client.environments.create(sources=[...], network=..., from_environment="<env_id>", mode="on_demand"|"self_hosted", system_instruction=..., custom_config=...)` (SDK source). `from_environment` forks a prepared environment, so you can install ngspice once and fork N copies for parallel agents. With a saved agent, each invocation forks its `base_environment`.
- **Lifecycle:** provisioning takes "up to ~5 s". An environment goes Idle (snapshotted) after 15 min of inactivity and stays resumable for 7 days, then is deleted. An expired ID returns 404.
- **Network:** unrestricted egress by default. You can set an allowlist (`{"domain":..., "credential":..., "transform":{headers}}`) or `"disabled"`. Secrets belong in credentials, not in literal `env`.

### 1.7 Hooks (hosted only)
- Mount `.agents/hooks.json` plus scripts through `sources`. The events are `pre_tool_execution` (can deny; the model sees the reason and adapts) and `post_tool_execution` (side effects only). The matcher is a regex over `code_execution|view_file|write_to_file|replace_file_content|list_dir|delete_file`. Handlers are `{"type":"command","command":"python3 /.agents/hooks-scripts/x.py","timeout":10}` or `http`. The hook reads `{"tool_call":{"name","args"},"environment_id"}` (plus `error` for post hooks) on stdin and writes `{"decision":"allow"|"deny","reason":...}` to stdout.
- Pipeline uses: deny writes to `/workspace/golden/` (proves the verifier cannot edit the reference netlist or evaluator), and log every `ngspice` command to `/workspace/out/audit.log` (shows "the agent ran ngspice 17 times" in the demo).
- **[UNVERIFIED]** whether self-hosted workers honor hooks. Self-hosted rejects `sources`, and the CLI docs do not mention hooks.

### 1.8 Self-hosted / local mode (Nov 2026)
Setup takes about 5 min once the CLI is installed:
```bash
curl -fsSL https://raw.githubusercontent.com/google-gemini/gemini-api-cli/main/scripts/install.sh | bash   # gemini-api CLI (Go)
export PATH="$HOME/.local/bin:$PATH"
```
```python
env = client.environments.create(mode="self_hosted", system_instruction="ngspice is /usr/bin/ngspice; SKY130 lib at ./pdk/...")
print(env.id, env.environment_key)     # environment_key is returned ONCE
```
```bash
mkdir -p ~/agent_ws && cp -r sim/models ~/agent_ws/
env -u GEMINI_API_KEY gemini-api environments connect --environment-key "$KEY" \
    --root-dir ~/agent_ws --workdir ~/agent_ws --command-timeout 10m --max-concurrent 4
```
Then call `client.interactions.create(agent="antigravity-preview-09-2026", environment=env.id, ...)`.
- **Tools in self-hosted mode:** `execute_bash` (aliases `bash`, `shell`, `run_command`), `view_file`, `list_dir`, `grep_search`, `find_by_name`, `write_to_file`, `replace_file_content`. There is no `code_execution` name here. Don't pass `tools=[{"type":"code_execution"}]` without testing it. Custom `function` tools should still go to the client **[UNVERIFIED but architecture implies it]**.
- **Security model:** the worker polls `POST /v1beta/environments:leaseInteractions` over outbound HTTPS only, with no inbound ports. **Bare-host `execute_bash` runs model-generated `bash -c` as your user.** `--root-dir` confines only the file tools, not bash. bash output is capped at 4 MiB and runs with a scrubbed OS env allowlist, so use absolute paths such as `/usr/bin/ngspice`. **Default `--command-timeout` is 2 min**: a full-library 15-corner sweep (about 9 s × 15 serial) exceeds it, so raise it. A safer option is the repo's Dockerfile (`python:3.12-slim-bookworm`, user 1000), with `apt-get install ngspice` added in the image, the models mounted read-only, and `--cap-drop=ALL --security-opt=no-new-privileges --pids-limit=256`. Split mode (`environments poll` on the host plus `environments serve --lease-token` in throwaway containers) keeps API keys out of the sandbox.
- Hosted-only options (`sources`, `network`, `env`, `/files` snapshots) return **400** on self-hosted environments, so files live directly in `--root-dir`.
- The Schmid Nov 16 local demo shows the lease claimed, then `execute_bash` and `write_to_file`, completing in about 24 s for a trivial task.

### 1.9 Parallelism, limits, pricing
- **Parallel agents:** nothing in the docs forbids it. Each `interactions.create(environment="remote")` gets its own sandbox, and `from_environment` forks clone a prepared one. Self-hosted workers default to `--max-concurrent 4` interactions sharing one `--root-dir`, so give each agent its own subdirectory. **No documented concurrency, RPM or environment-count limit for managed agents [GAP]**. Free tier gets "a free rate limit and usage quota" with no numbers given. The limit is 1,000 saved agents. **No subagent nesting.**
- **Triggers** (cron-scheduled agent runs, `execution_timeout_seconds` default 600, `max_consecutive_failures` 5) are not relevant today.
- **Pricing:** model tokens at list rates plus tool fees. Hydrogen is $1.50/$7.50 and Carbon $2/$10 per 1M tokens through Nov 30. Free-tier projects can run the agent on Hydrogen.

### 1.10 Pitfalls reported by developers (forum, Aug–Nov 2026)
- **Background 404:** `antigravity-preview-09-2026` background interactions returned 404 on GET right after a 200 create. Google said it was "temporary ... around September 18" and has cleared. Synchronous and streamed runs worked. ([thread](https://discuss.ai.google.dev/t/antigravity-preview-09-2026-background-interaction-returns-404-immediately-after-successful-creation/184054))
- **Stuck and auth problems (Sep 28):** background interactions stuck `in_progress` with `created == updated`. `:cancel` returned 404 "Method not found". The Environment API rejected an AI Studio key with 403 "unregistered callers" while `/models` worked. Billing UI lagged by hours. About 4 h of setup. ([thread](https://discuss.ai.google.dev/t/managed-antigravity-agent-setup-is-excessively-complex-auth-inconsistencies-stuck-interactions-unusable-cancellation-and-unclear-billing/185863))
- **Sudden 403 `permission_denied`** on new agent interactions with a key that still worked elsewhere (Aug). Google flagged it as a bug and fixed it. ([thread](https://discuss.ai.google.dev/t/antigravity-managed-agent-new-interactions-return-403-after-previously-working/176850))
- **Mitigations for today:**
  - Run a 20k-token probe **now**, covering the environments API (`client.environments.create`) as well as interactions.
  - Prefer `stream=True` over `background=True`.
  - Always set `max_total_tokens`.
  - Put a client-side wall-clock timeout on each agent run and `interactions.cancel` on expiry.
  - Log `it.id`, `environment_id` and `usage` for every run.
  - Keep a pre-recorded run on video.
- Other pitfalls: the 05→09 tool rename (PascalCase args such as `write_to_file(TargetFile, CodeContent, ...)`) matters only if you parse steps or write hooks. Uppercase MCP names give a 400. Sync HTTP times out at about 60 s.

## 2. Other Gemini agent surfaces: usable today from a laptop?

| Surface | Works with Gemini 4 today? | Setup time | Runs our ngspice? | Counts as "Managed Agents"? | Verdict |
|---|---|---|---|---|---|
| **Managed Agent, hosted sandbox** | Yes (Hydrogen default; Carbon/Argon/Neon selectable) | minutes | Only if ngspice gets installed (micromamba/conda-forge 41 **[UNVERIFIED]**) **or** via custom `function` → local | **Yes** | Use for verifier, auditor, report writer |
| **Managed Agent, self-hosted local worker** | Yes (same API, `environment=<self_hosted id>`) | 5–10 min (CLI install + env create) | **Yes, directly** (laptop or Docker) | **Yes** (Google runs the loop) | Most reliable route to "agent + real ngspice" |
| **Managed Agent + custom function `evaluate`** | Yes | minutes | **Yes** (we execute it) | **Yes** | Best fit for designer arm and auditor |
| Interactions API function calling (`model="gemini-4-carbon"`, `tools=[fn]`) | Yes | minutes | Yes (we execute) | No (plain model) | Our own loop. Cheapest, deterministic budget. No AFC helper in `interactions` (manual loop, ~15 lines) |
| Built-in `code_execution` tool on a model call | Yes | 0 | **No**: 30 s max runtime, fixed library list, "You can't install your own libraries" | No | Only for numeric post-processing |
| ADK (`LlmAgent`, `ParallelAgent`, `LoopAgent`, `SequentialAgent`; `Gemini(model=..., use_interactions_api=True)`) | Model string pass-through; no Gemini 4 page in ADK docs **[UNVERIFIED with gemini-4-*]** | 20–40 min (new dependency) | Yes (Python function tools) | Only `ManagedAgent` wrapper (no client tools) | Not worth a new framework today. Our loop already is a LoopAgent |
| ADK `ManagedAgent` (ADK Py ≥2.4.0 preview) | Wraps Antigravity agent | 20 min | No client tools; remote MCP only | Yes | Skip |
| Antigravity IDE Orchestrator (Carbon plans, Hydrogen implements) | Yes (IDE) | n/a | n/a | No (dev tool) | Use to *build* faster, not in the pipeline |
| Gemini Enterprise Agent Platform managed agents / Agent Engine | GCP project, ADC, `global` location; Gemini 4 not seen in its docs | 1 h+ | — | Probably yes, different backend | Skip |
| `gemini-api` CLI (`agent run`, `environments connect`) | Yes | 2 min | via self-hosted | Yes (same API) | Use for probes and the local worker |

## 3. Catalogue of integration points (ranked)

Ranking weighs prize fit (Managed Agents, at least 2 Gemini 4 models with clear jobs), contribution to "Proof It Works", demo visibility, risk, and build time with about 4.5 h left. Cost uses Hydrogen $1.50/$7.50, Carbon $2/$10, and the docs' estimate that 50–70% of input is cached. All cost and latency figures are estimates.

| # | Integration point | Surface / model | What the agent does | In → out | Why an agent, not one call | Cost / latency (est.) | Risk | Demo moment | Build |
|---|---|---|---|---|---|---|---|---|---|
| **1** | **(a+h) Independent sign-off verifier** | Managed Agent, **self-hosted local worker** (or hosted + micromamba ngspice, or hosted + `run_spice` function). Model: **Carbon** (high-stakes audit) or Hydrogen | Reads only the final netlist, spec card and the *original* AMPICBench deck. Writes its **own** loop-gain/AC testbench and .meas from scratch (it may not import our evaluator). Runs 15 PVT points on the **full** sky130 lib, checks saturation from op-point dumps, and writes `signoff.json` plus a one-page `signoff.md`/PDF. We diff it against our evaluator | netlist, spec JSON, schematic PNG (image input) → `signoff.json` {per-corner UGF/PM/gain/Iq, pass/fail, deltas vs ours}, testbench file | Multi-step tool use: write bench, run, debug ngspice errors, parse, re-run. The independence claim needs a separate actor with its own code | 150–400k in / 15–40k out → **$0.40–1.20 per sign-off** (Carbon). 3–8 min wall | Medium: ngspice availability (solved by self-hosted), the agent mis-measuring PM (mitigate by giving it the reference tt numbers 60.59 dB / 125.4 MHz / 31.5° as a calibration check it must reproduce first) | Live stream of the agent writing a testbench, then "Independent sign-off: 15/15 corners PASS; max |ΔPM| vs our evaluator 0.3°". This feeds the plan's "claimed pass survives re-verification" metric | 60–75 min |
| **2** | **(c) Managed-Agent designer arm** (planner as a tool-using agent) | Managed Agent (hosted, no ngspice needed) + custom function `evaluate` (local ngspice). Model: **Hydrogen** (Google: "tool-using agents") vs our Carbon loop | Gets the spec card, role map, bounds and budget. Calls `evaluate(params)` (batch of up to 4) repeatedly, does its own numpy/sensitivity analysis in the sandbox between calls, and stops when all-corner feasible or the budget is gone | spec, bounds, start point → tool calls; each result = worst-corner table + triode list (+ optional Bode-fan PNG) → final params | A loop is the job: an agent decides when to explore, exploit or stop. Comparing *agent-driven* vs *our-loop-driven* Carbon is a real experiment | Each hop re-reads context (cached): ~10 hops × 30–60k in → 0.3–0.6M in, 20–40k out → **$0.40–1.20/run** (Hydrogen). 5–15 s per hop + 1.5 s sim → 2–4 min/run | Medium: the agent overspending budget is prevented because our handler refuses after 600 sims (returns `is_error` "BUDGET_EXHAUSTED"); preview flakiness | 4th row in the held-out table ("Managed Agent, Hydrogen") on a subset (4 tasks × 1–2 seeds); stream shows tool calls | 45–60 min (reuses `evaluate`, `Budget`) |
| **3** | **(i) Red-team / reward-hacking auditor** | Managed Agent, hosted sandbox (repo snapshot of `sizing/`, `bench/` uploaded) + custom function `evaluate` (local). Model: **Carbon** | Reads evaluator and spec-parser source. Writes adversarial inputs (bounds edges, NaN/inf, L below min, Rz→0, Cc→0, specs with minus signs, units, missing .meas, a "pass" from a failed sim) and probes them through `evaluate`. Reports exploits with repro params | source tar + `evaluate` tool → `redteam.json` [{exploit, repro, observed, expected, severity}] + suggested unit tests | Open-ended search with feedback. Mirrors the published AutoSizer audit (signed-spec regex bug, budget overrun) | 100–300k in / 10–30k out → **$0.30–0.80**. 3–6 min | Low: no ngspice in sandbox needed; worst case it finds nothing, which is itself a result | "We let an agent attack our judge: it found N issues; we fixed M; tests added". Strong credibility for the 25% proof criterion | 30–45 min |
| 4 | **(b) Testbench builder from datasheet conditions** | Managed Agent (self-hosted, or the same env as #1). Model: Carbon. **Input: datasheet EC-table page + test-circuit figure rendered to PNG** (agent can't take PDFs, but images are fine) | Writes new ngspice benches (unity-gain slew-rate transient, PSRR, CMRR, input noise) under the datasheet's stated conditions (VS, RL, CL, G). Validates each on the published sizing (sanity: SR ≈ I_tail/Cc) and returns `.cir` plus a parse function | page PNGs + spec card + netlist → `benches/*.cir`, `measure.py`, validation log | Write → run → fix loop; condition-mapping judgement | **$0.50–1.50**. 5–10 min per bench set | Med-high: wrong benches create wrong specs. Gate each bench with a closed-form sanity check before adopting | "Datasheet figure → agent-built SR bench → new SR row in the spec card"; makes the image input load-bearing | 60–90 min |
| 5 | **(f) Netlist parameterization agent** (held-out netlist → per-role knobs) | Managed Agent (self-hosted) + Decisions role labels as input. Model: Carbon | Rewrites FC and telescopic netlists' shared `Wn/Wp` into per-role params from the Decisions labels. Then **self-checks** that default knob values reproduce the published tt numbers (73.9 dB / 71.3 MHz / 32.0°) before handing off | netlist + role map + published numbers → parameterized `.cir` + knob table + regression pass/fail | Edit → simulate → compare loop | $0.30–0.80, 3–6 min | Medium: silent topology edits (gate: the regression must match within 0.5 dB / 1° / 2%) | Strengthens "held-out inputs not hand-prepared by the team" | 45 min |
| 6 | **(e) Failure-diagnosis agent** | Prefer a **single Carbon call** on the op-point dump. An agent is justified only if it runs follow-up sims (DC sweep of the failing device, PM vs Cc sweep) via `evaluate`/`run_spice` | per-corner op dump + failures → diagnosis + suggested knob deltas | — | Agent only if it runs extra experiments | $0.05 (call) / $0.40 (agent), 10 s / 3 min | Low | "ss/125 °C: tail in triode, Vds margin −40 mV → raise W_tail" annotations | 20–40 min |
| 7 | **(k) Spec-extraction auditor** (new) | Managed Agent, hosted: PDF uploaded as a binary (bash can read it; `pip install pdfplumber`) | Cross-checks every Hydrogen-extracted spec record against the PDF text layer and table geometry, flags absent or mis-conditioned values | PDF + spec JSON → `spec_audit.json` | Multi-step parsing with tools | $0.30–0.80, 3–5 min | Low-med (pip availability) | Supports the extraction-accuracy table | 30–45 min. Overlaps the plan's deterministic text-layer check, so do only if spare |
| 8 | (h) Report / "SKY130 clone" datasheet writer (standalone) | Managed Agent, hosted (`pip install matplotlib reportlab`) | Builds a PDF datasheet (TLV9001 vs SKY130 worst-corner table, corner-fan plots) from result JSON | results JSON + plots → PDF | Mostly formatting; agent buys little | $0.30–1.30, 3–6 min | Low | Nice handout; *not* the main feature (dashboard rule) | 30 min. **Fold into #1** as the sign-off report |
| 9 | (d) Parallel swarm of explorers | **Plain parallel Hydrogen calls** (ThreadPool) + local evaluator; Managed Agents only if forked environments from one prepared env | each worker explores a region / corner subset | — | Little: each worker is one proposal call. Managed-agent overhead (sandbox, loop, tokens) ×N for no gain | N× agent cost | Med (unknown concurrency limits) | "8 agents in parallel" looks good but proves little | Skip as Managed Agents. Optional as plain calls |
| 10 | (g) Benchmark-porting agent (AMS-SizingBench / AMPICBench) | Better done in the IDE (Antigravity Orchestrator / your coding agent) | port circuits and YAML specs | — | One-off dev task, not a product feature | — | — | none | Skip in pipeline |
| 11 | (l) Hand-drawn schematic → netlist agent (new) | Managed Agent, image input + ngspice | transcribe photo → SPICE, simulate DC op to check | image → netlist | Write/run/fix loop | $0.5–1.5 | High (SGAnalog best 56% isomorphism) | camera demo | Stretch only |

## 4. Fallbacks if hosted sandboxes cannot run ngspice (in order)

1. **Self-hosted local worker** (§1.8). The laptop already has `/usr/bin/ngspice` 47 and `~/.ciel/sky130A/libs.tech/combined` (3.3 MB). Copy `sim/models/sky130_slim.lib.spice`, the referenced `combined/continuous` files and `.spiceinit` into `--root-dir`, and rewrite absolute `.include` paths to relative ones. Bare host is acceptable for a demo if you use a dedicated empty root-dir, an allowlisted network and no secrets in env (`env -u GEMINI_API_KEY`). Docker is safer (repo Dockerfile + `apt-get install -y ngspice`; Debian bookworm ships ngspice 39 or later **[UNVERIFIED version]**).
2. **Hosted sandbox + custom function `run_spice(deck: str, corner, temp)`**: the agent writes its own decks in the sandbox and passes the deck text to our function. We substitute the model-library path, run ngspice locally with `-b` and a timeout, and return stdout plus the `.meas` and op files as text. This preserves "agent wrote its own testbench" for the verifier.
3. **Hosted sandbox + micromamba**: `curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xvj bin/micromamba && ./bin/micromamba create -y -p /workspace/ng -c conda-forge ngspice` (conda-forge ngspice **41**). Then upload the 3.3 MB models with `files.upload(extract=True)` and fork that environment for reuse. Version 41 vs 47 may shift numbers slightly, so report tolerance. **[UNVERIFIED: network/conda in sandbox, apt/root]**.
4. **Hosted `.deb` extract without root:** download the Ubuntu `ngspice` .deb and run `dpkg -x` into `/workspace`. The X11 libs it links may be missing, so this is the least likely to work.

## 5. Top-3 recommendation with skeletons

Common helper (`agents/common.py`):
```python
import json, time, base64
from google import genai
AG = "antigravity-preview-09-2026"
client = genai.Client(http_options={"api_version": "v1beta", "timeout": 900_000})

def run_agent(input, *, environment, model, budget, tools=None, system=None, on_call=None, wall_s=900):
    """Run an agent turn-by-turn; on_call(name, args) -> result for custom functions. Returns final interaction."""
    kw = dict(agent=AG, environment=environment, system_instruction=system,
              agent_config={"type": "antigravity", "model": model, "max_total_tokens": budget})
    if tools: kw["tools"] = tools
    it = client.interactions.create(input=input, **kw); t0 = time.time()
    while it.status == "requires_action":
        if time.time() - t0 > wall_s:
            client.interactions.cancel(id=it.id); break
        done = {s.call_id for s in it.steps if s.type == "function_result"}
        pend = [s for s in it.steps if s.type == "function_call" and s.id not in done and on_call and s.name in on_call.names]
        res = [{"type": "function_result", "name": s.name, "call_id": s.id, "result": on_call(s.name, s.arguments)} for s in pend]
        it = client.interactions.create(previous_interaction_id=it.id, input=res,
                                        **{**kw, "environment": it.environment_id})   # re-sends tools/agent_config; drop if 400
    print(f"[agent] {it.id} env={it.environment_id} status={it.status} tokens={it.usage.total_tokens}")
    return it
```
(For the live demo, swap in `stream=True` and print `step.start` types and `text`/`thought_summary` deltas. Keep this non-streaming version for batch runs.)

### #1 Independent sign-off verifier (Managed Agent, self-hosted local worker, Carbon)
Why: it is the most defensible Managed-Agents use (independent actor, its own testbench, real ngspice, full PDK). It feeds the plan's "agent-claimed pass survives independent re-verification" metric directly, and it doubles as the report writer (h).
```python
# one-time: env = client.environments.create(mode="self_hosted"); save env.id / env.environment_key
# terminal 1: env -u GEMINI_API_KEY gemini-api environments connect --environment-key $KEY \
#             --root-dir ~/signoff_ws --workdir ~/signoff_ws --command-timeout 10m
import shutil, pathlib
WS = pathlib.Path.home() / "signoff_ws"
def stage(task_id, final_netlist, spec, schematic_png):
    d = WS / task_id; shutil.rmtree(d, ignore_errors=True); (d / "out").mkdir(parents=True)
    (d / "dut.cir").write_text(final_netlist); (d / "spec.json").write_text(json.dumps(spec))
    shutil.copytree("sim/models", d / "pdk", dirs_exist_ok=True)       # + combined/continuous files, paths made relative
    return d
SYS = ("You are an independent analog sign-off engineer. Never read or import files outside this task folder. "
       "ngspice is /usr/bin/ngspice. Write YOUR OWN testbench; do not trust any provided measurements.")
TASK = """Folder {tid}/ holds dut.cir (SKY130 two-stage op-amp subckt), spec.json, pdk/sky130 full lib.
1. Calibrate: with the ORIGINAL sizing in ref.cir, your bench must reproduce tt/27C DC gain 60.6 dB, UGF 125-126 MHz, PM 31.5 deg within 1 dB / 3% / 1 deg. Fix your bench until it does.
2. Simulate dut.cir at corners tt ff ss sf fs x temps -40 27 125 (15 points) with the FULL library: loop gain (DC gain, UGF, PM, GM),
   supply current, and |Vds|-|Vdsat| for every signal-path transistor.
3. Write {tid}/out/signoff.json: {{"points":[{{corner,temp,gain_db,ugf_hz,pm_deg,gm_db,iq_a,min_sat_margin_v,triode:[...]}}],
   "pass_all": bool, "violations":[...], "bench_file": "..."}} and a 1-page {tid}/out/signoff.md. Reply with the pass/fail line only."""
it = run_agent([{"type": "text", "text": TASK.format(tid=tid)},
                {"type": "image", "mime_type": "image/png", "data": base64.b64encode(open(png, "rb").read()).decode()}],
               environment=SELF_HOSTED_ENV_ID, model="gemini-4-carbon", budget=300_000, system=SYS)
agent = json.loads((WS / tid / "out/signoff.json").read_text())
# compare with sizing.opamp2stg.evaluate(params, lib="full"): per-point |Δgain|, |ΔPM|, pass agreement -> "re-verification survives k/n"
```
Hosted variant (if self-hosted auth fails): `environment={"type":"remote","sources":[inline dut.cir/spec.json, hooks]}` plus `files.upload(..., "workspace/pdk/", extract=True)`, plus either the micromamba step in the prompt or a `run_spice` custom function (§4.2). Add the §1.7 hook that denies writes under `/workspace/golden/`.

### #2 Managed-Agent designer arm (hosted Managed Agent, Hydrogen, custom `evaluate`)
Why: it puts a Managed Agent in the core loop with a measurable outcome (a 4th row in the held-out table). Google runs the agent's reasoning and scratch analysis, while ngspice and the hard budget stay on our machine. It also gives a clean "Hydrogen executes vs Carbon plans" comparison under one budget.
```python
from sizing.opamp2stg import evaluate, Budget, SPACE, clamp   # SPACE: knob -> (default, lo, hi, log)
props = {k: {"type": "number", "description": f"SI units, range {lo:g}..{hi:g}, published {d:g}"}
         for k, (d, lo, hi, _) in SPACE.items()}
EVAL = {"type": "function", "name": "evaluate",
        "description": "Simulate up to 4 candidate sizings, each at all 15 PVT points (15 sims each, from a 600-sim budget). "
                       "Returns per-candidate pass flag, worst corner per spec, failures, triode devices, sims_left.",
        "parameters": {"type": "object", "properties": {"candidates": {"type": "array", "maxItems": 4,
                       "items": {"type": "object", "properties": props, "required": list(props)}}},
                       "required": ["candidates"]}}
class Handler:
    names = {"evaluate"}
    def __init__(self, spec, sims=600): self.spec, self.budget, self.log = spec, Budget(sims), []
    def left(self): return self.budget.max_decks - self.budget.used
    def __call__(self, name, args):
        out = []
        for c in args.get("candidates", [])[:4]:
            c = {k: v for k, v in c.items() if k in SPACE}       # clamp() raises KeyError on unknown knobs
            try: er = evaluate(c, self.spec, budget=self.budget)  # evaluate() clamps + reserves 15 decks up front
            except Exception as e:  # BudgetExceeded or sim crash
                return {"error": f"{type(e).__name__}: {e}", "sims_left": self.left()}
            self.log.append(er)
            out.append({"params": er.params, "pass": er.passed, "worst": er.worst, "failures": er.failures[:12]})
        return {"results": out, "sims_left": self.left()}   # or [{"type":"text",...},{"type":"image",...bode png}]
h = Handler(spec_card)
it = run_agent(f"Size this op-amp to meet ALL specs at ALL 15 PVT points. Spec: {json.dumps(spec_card)}. "
               f"Role map: {json.dumps(roles)}. Start: {json.dumps(start)}. Use the evaluate tool; you may use "
               "python in your sandbox to analyse results. Stop when a candidate passes or sims_left < 15. "
               "Reply with the best passing params as JSON.",
               environment="remote", model="gemini-4-hydrogen", budget=400_000,
               tools=[{"type": "code_execution"}, EVAL], on_call=h)
best = next((e for e in h.log if e.passed), None)    # feasible-first from OUR log, never from the agent's claim
```
`EvalResult` fields (`params, passed, worst, failures`) match `sizing/opamp2stg.py`. Make sure `er.worst` tuples serialize to JSON. Run it on 4 held-out tasks × 1–2 seeds after Cut Line B. Report tokens and $ from `it.usage`.

### #3 Red-team / reward-hacking auditor (hosted Managed Agent, Carbon, custom `evaluate`)
Why: it is cheap, independent of ngspice-in-sandbox, and turns the AutoSizer-audit lesson into a live, agent-driven check of our judge. Every confirmed exploit becomes a unit test. That strengthens the 25% "Proof It Works" criterion and is a distinct, easy-to-explain Managed-Agent job.
```python
import io, tarfile
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as t:
    for p in ["sizing/opamp2stg.py", "bench/amsbench.py", "bench/bo_baseline.py"]: t.add(p)
env = client.environments.create(sources=[{"type": "inline", "target": "/workspace/README_TASK.md", "content": "audit"}])
client.environments.files.upload(environment=env.id, path="workspace/src/", file=io.BytesIO(buf.getvalue()), extract=True)
RT = ("You are auditing an analog-sizing evaluator for reward hacking. Source is in /workspace/src. The evaluate tool "
      "runs the REAL evaluator. Find inputs or spec strings where it reports pass but the design is not truly passing, "
      "or where budget/feasibility accounting can be gamed (bounds, NaN/inf, negative numbers, signed specs like '> -5', "
      "missing measurements, sim failures, units). Max 20 evaluate calls. Write /workspace/out/redteam.json as "
      "[{exploit, repro_params_or_spec, observed, expected, severity}] and propose pytest cases.")
it = run_agent(RT, environment=env.id, model="gemini-4-carbon", budget=250_000,
               tools=[{"type": "code_execution"}, EVAL], on_call=Handler(spec_card, sims=300))
open("runs/redteam.tar", "wb").write(client.environments.files.download(environment=env.id, path="workspace/out"))
```

**Alternate #3** (if #1's environment is up before 4:00): the **testbench builder (#4 in §3)**, fed datasheet EC-table and test-circuit PNGs. It makes the image input load-bearing and unlocks the slew-rate spec, but has higher correctness risk.

### Model-role summary for the 20% criterion
- Hydrogen: datasheet extraction, plus the Managed-Agent designer arm.
- Carbon: the main planner loop, the sign-off verifier, and the red-team agent.
- `gemini-decision-1`: role labels and gates.
- Managed Agents appear as three named jobs with distinct models, all with token budgets set.

## 6. Build-order fit (against the plan's cut lines)
- **Now (≤15 min):** probe with `client.environments.create(mode="self_hosted")` and one hosted 20k-token Hydrogen agent run that executes `which ngspice; sudo -n true; pip download nothing; curl -sI https://conda.anaconda.org`. This settles the apt/root/network questions in one shot. Install the `gemini-api` CLI.
- **After Cut Line A (3:00):** #3 red-team (30–45 min, runs in the background while held-out runs go).
- **Cut Line B window (3:30–4:45):** #1 verifier on the final designs, replacing or augmenting the plan's "full-library re-verification" step with an independent agent.
- **4:45+:** #2 designer arm on a held-out subset if quota and time allow. Otherwise show one recorded run.

## 7. Gaps / unverified
- apt, sudo and root in the hosted sandbox; whether conda/micromamba works there; whether ngspice 41 matches 47 numbers on SKY130.
- Concurrency and rate limits for agent interactions and environments; free-tier agent quota numbers.
- Whether `tools` must be re-sent on agent continuation turns; whether custom `function` tools work with self-hosted environments (very likely, not documented); whether self-hosted workers honor `.agents/hooks.json`.
- Whether ADK's Gemini model class accepts `gemini-4-*` strings with Interactions (likely pass-through, untested).
- Hackathon rules and the $5k Managed Agents prize criteria (see the plan; no public page).

## Sources
- Antigravity agent docs (tools, function calling, MCP, model selection, budget, limitations, costs): https://aistudio.google.com/docs/antigravity-agent.md
- Managed agents quickstart: https://aistudio.google.com/docs/managed-agents-quickstart.md
- Environments (sources, upload/download, fork, lifecycle, self-hosted, pricing, limits): https://aistudio.google.com/docs/agent-environment.md
- Hooks: https://aistudio.google.com/docs/agent-hooks.md
- Building managed agents (`agents.create`, fork, limits, no nesting, 1000 agents): https://aistudio.google.com/docs/custom-agents.md
- Function calling (incl. multimodal function responses, resend-tools note): https://aistudio.google.com/docs/function-calling.md
- Streaming event types: https://aistudio.google.com/docs/streaming.md
- Background execution (60 s HTTP note): https://aistudio.google.com/docs/background-execution.md
- Code execution (30 s, no installs): https://aistudio.google.com/docs/code-execution.md
- Release notes (09-2026 tool renames, CLI beta Oct 27, tool retrieval Nov 19, multimodal function results Nov 5): https://aistudio.google.com/docs/changelog.md
- gemini-api CLI self-hosted guide, local mode, containers: https://github.com/google-gemini/gemini-api-cli/tree/main/docs/self-hosted
- google-genai 3.6.1 source (installed): `.venv/lib/python3.12/site-packages/google/genai/_gaos/` (`environments.py` `create_environment(mode, from_environment, custom_config, system_instruction)`, `types/interactions/antigravityagentconfig.py`, `interactionstatusupdate.py`, `createagentinteraction.py`)
- ADK Managed agents (no client tools): https://adk.dev/agents/managed-agents/ ; ADK Gemini / Interactions API: https://adk.dev/agents/models/google-gemini/
- Philipp Schmid, Nov 16 roundup (self-hosted local demo): https://www.philschmid.de/google-deepmind-roundup-nov-2026 ; How Managed Agents work (4 vCPU / 16 GB): https://www.philschmid.de/how-managed-agents-work ; Developer guide: https://www.philschmid.de/gemini-managed-agents-developer-guide
- Forum threads: https://discuss.ai.google.dev/t/antigravity-preview-09-2026-background-interaction-returns-404-immediately-after-successful-creation/184054 , https://discuss.ai.google.dev/t/managed-antigravity-agent-setup-is-excessively-complex-auth-inconsistencies-stuck-interactions-unusable-cancellation-and-unclear-billing/185863 , https://discuss.ai.google.dev/t/antigravity-managed-agent-new-interactions-return-403-after-previously-working/176850
- conda-forge ngspice 41 (linux-64): https://anaconda.org/conda-forge/ngspice ; PyPI has no ngspice wheel (checked `ngspice`, `pyngspice`, `ngspice-bin`, `ngspyce`)
