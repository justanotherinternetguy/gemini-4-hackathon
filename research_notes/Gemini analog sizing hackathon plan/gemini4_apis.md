# Gemini 4 developer platform (as of 2026-11-21): Python reference for a one-day hackathon

Scope: the exact strings, call patterns and limits needed to build an LLM-guided SKY130 analog sizing agent (datasheet PDF and schematic image in, gm/Id and L targets out, ngspice as the judge). Every fact below links to its source. Primary sources: AI Studio docs (ai.google.dev now 301-redirects to aistudio.google.com/docs/...), the Gemini API changelog, the Google blog and a Google codelab. Philipp Schmid's blog is a Google DeepMind staff roundup. Vercel and Pickaxe pages are third-party.

Quick summary of the user's unverified notes:

| User note | Status |
|---|---|
| Argon GA 13 Oct 2026, "efficient frontier" | Date CONFIRMED. Google calls it "the efficient Gemini 4 frontier reasoning model". The word "default" is NOT confirmed: Hydrogen is the Antigravity default, and Argon has no free tier. |
| Carbon GA 10 Nov, strongest, deep thinking | CONFIRMED (strongest reasoning model). Its thinking levels stop at low/medium/high, with no xhigh. |
| Hydrogen GA 12 Nov, fast and cheap workhorse | Date CONFIRMED. It is the only Gemini 4 model on the free tier. It is NOT the cheapest on paid tiers: $1.50/$7.50, against Argon and Carbon at $2/$10 introductory pricing. |
| Neon GA 18 Nov, 1M context, four thinking levels | CONFIRMED (low, medium, high, xhigh). "Between Hydrogen and Carbon" is NOT confirmed. It is the most expensive Gemini 4 model right now ($3/$15). |
| Decisions API launched 11 Nov, "Gemini Decision 1" | CONFIRMED. Model ID `gemini-decision-1`. |
| Up to 1,000 questions, yes/no / MC (≤255) / rubric | CONFIRMED. The yes/no type string is `"noul"`. |
| Text/image/audio/video/PDF, calibrated, input billing only | CONFIRMED. $0.10 per 1M input tokens, $0 output. |
| Managed Agents launched May with Antigravity; later model selection, hooks, budgets | Model selection, hooks and the `max_total_tokens` budget are CONFIRMED. The May launch is only inferred from the old agent ID `antigravity-preview-05-2026`. |

## 1. Gemini 4 model IDs, thinking levels, context, rate limits, prices, and official model guidance

### Takeaway
The four API IDs are `gemini-4-argon`, `gemini-4-carbon`, `gemini-4-hydrogen` and `gemini-4-neon`. All take 1M tokens of context and text, image, audio, video and PDF input. Thinking is set with `thinking_level`. Only Hydrogen is free in AI Studio. Google's suggested pairing is "Carbon plans and orchestrates, Hydrogen executes", which fits proposal fan-out on Hydrogen and planning or critique on Carbon. Any Gemini 4 model can read the PDF and the image.

### Cited Findings
**Model IDs and specs**
- The models page lists `gemini-4-carbon`, `gemini-4-neon`, `gemini-4-hydrogen` and `gemini-4-argon`. Each has a 1M-token context, a max output shown as "64K / 1M tokens", and Text, Image, Audio, Video and PDF input — [AI Studio Models](https://aistudio.google.com/docs/models)
- Changelog GA dates:
  - Argon (`gemini-4-argon`): Oct 13, 2026.
  - Carbon (`gemini-4-carbon`): Nov 10, at introductory pricing through Nov 30.
  - Hydrogen (`gemini-4-hydrogen`): Nov 12. It is on the free tier, adds `xhigh`, and became the default model for the Antigravity agent.
  - Neon (`gemini-4-neon`): Nov 18. It has four thinking levels (`low`, `medium`, `high` as default, `xhigh`), a 1M context, and new Office/OpenDocument input.
  - Source: [Gemini API changelog](https://aistudio.google.com/docs/changelog)
- Older IDs that still exist: `gemini-3.8-flash` (GA Sep 2), `gemini-3.6-flash` and `gemini-3.5-flash-lite`. `gemini-2.5-pro` and `gemini-2.5-flash` are deprecated and route to 3.8-flash and 3.5-flash-lite from Dec 1 — [changelog](https://aistudio.google.com/docs/changelog)
- Carbon shares one 1M-token pool across input, thinking and output. It ignores `temperature`, `top_p` and `top_k`. Implicit caching starts at 4,096 tokens with a 95% discount and replaces `cachedContents` — [Philipp Schmid roundup, Nov 16 2026](https://www.philschmid.de/google-deepmind-roundup-nov-2026)

**Thinking levels**
- The parameter is `thinking_level`, passed in `generation_config` on the Interactions API. `minimal` is NOT supported on Gemini 4, and the docs give no way to turn thinking fully off. Thought summaries are controlled with `generation_config={"thinking_summaries": "auto"}` (or `"none"`) — [Thinking docs](https://aistudio.google.com/docs/thinking)

| Model | Default | Levels |
|---|---|---|
| `gemini-4-argon` | medium | low, medium, high |
| `gemini-4-carbon` | medium | low, medium, high |
| `gemini-4-hydrogen` | high | low, medium, high, xhigh |
| `gemini-4-neon` | high | low, medium, high, xhigh |

Source: [Thinking docs](https://aistudio.google.com/docs/thinking)

- Snippet adapted from the docs:
  ```python
  from google import genai
  client = genai.Client()  # reads GEMINI_API_KEY
  r = client.interactions.create(
      model="gemini-4-hydrogen",
      input="...",
      generation_config={"thinking_level": "low"},
  )
  print(r.output_text)
  ```
  — [Thinking docs](https://aistudio.google.com/docs/thinking)

**Prices (USD per 1M tokens, Standard tier)** — [Pricing](https://aistudio.google.com/docs/pricing)

| Model | Free tier? | Input | Output (incl. thinking) | Batch/Flex | Notes |
|---|---|---|---|---|---|
| `gemini-4-hydrogen` | YES (free of charge) | $1.50 | $7.50 | $0.75 / $3.75 | Free-tier data is used to improve products. No Search grounding on free tier. |
| `gemini-4-argon` | No | $2.00 (→ $4.00 from Dec 1) | $10.00 (→ $20.00) | $1.00 / $5.00 | Same introductory schedule as Carbon. |
| `gemini-4-carbon` | No | $2.00 (→ $4.00 from Dec 1) | $10.00 (→ $20.00) | $1.00 / $5.00 | |
| `gemini-4-neon` | No | $3.00 | $15.00 | $1.50 / $7.50 | |
| `gemini-decision-1` | No-cost tier in AI Studio | $0.10 | $0 | — | [Google blog](https://blog.google/innovation-and-ai/technology/developers-tools/gemini-decisions-api/) |

- No >200k-token price tier is listed for the Gemini 4 models — [Pricing](https://aistudio.google.com/docs/pricing)

**Rate limits**
- The rate-limits page gives numbers only for `gemini-decision-1`: Free tier 200 RPM, 50,000 TPM, 10,000 RPD; Tier 1 1,500 RPM, 1,000,000 TPM, 50,000 RPD. For Gemini 4 it lists only Tier 1 Batch enqueued-token limits: Hydrogen 3,000,000; Argon, Carbon and Neon 5,000,000 each — [Rate limits](https://aistudio.google.com/docs/rate-limits)

**Official and semi-official model-selection guidance**
- Models page descriptions:
  - Carbon: the "strongest reasoning model" for complex software engineering, multimodal knowledge work, scientific reasoning and cyber defense.
  - Neon: "frontier-level intelligence with fast end-to-end task completion".
  - Hydrogen: everyday coding and agentic workflows, with an emphasis on speed and efficiency.
  - Argon: efficient frontier reasoning, with faster responses and lower token spend per turn than Carbon.
  - New projects should use "Gemini 4 Hydrogen or 3.5 Flash-Lite" in place of 2.5 Flash-Lite.
  - Source: [AI Studio Models](https://aistudio.google.com/docs/models)
- "Carbon fits hard reasoning. Hydrogen fits everyday and latency-sensitive work … Carbon plans and orchestrates, Hydrogen executes." Hydrogen is recommended for "tool-using agents, and parallel subagents" — [Philipp Schmid roundup](https://www.philschmid.de/google-deepmind-roundup-nov-2026)
- Antigravity's "Orchestrator" option pairs Gemini 4 Carbon as planner with Gemini 4 Hydrogen for implementation — [Antigravity Choose a model (search snippet)](https://antigravity.google/docs/models?hl=sr)
- Vercel (third party) suggests starting with Argon for a general-purpose app or agent — [Vercel: which Gemini 4 model](https://vercel.com/i/which-gemini-4-model-should-you-build-with)

### Inferences
- Suggested job split for the judging criterion "≥2 Gemini 4 models, each with a clear job":
  - **Hydrogen** (`thinking_level="low"` or `"medium"`) does fast parallel parameter proposals. It is free in AI Studio and Google officially recommends it for parallel subagents.
  - **Carbon** (`"high"`) does planning, role labeling and critique of ngspice results. It is cheap through Nov 30.
  - **Neon** or **Carbon** reads the datasheet PDF and schematic image. Every Gemini 4 model is multimodal and nothing in the docs ranks one above another for vision, so Carbon is the natural pick for one-shot "understand the circuit" work.
  - **`gemini-decision-1`** answers yes/no gates (for example "is this transistor the tail current source?" with probabilities) and scores rubrics.
- Carbon and Argon have no free tier, so a billing-enabled key is needed (or hackathon credits, if offered). Hydrogen alone runs on the free tier.
- Because Gemini 4 rejects `minimal`, the fastest proposal setting is `thinking_level="low"`.

### Gaps
- Free-tier and Tier 1 RPM/TPM/RPD for the four Gemini 4 models were not on the rate-limits page. Check the limits in the AI Studio console for your project.
- There is no official "builder guide" page by that name. Guidance comes from the models page, the Schmid roundup and Antigravity docs.
- I did not find "hackathon tier" quotas.
- The meaning of "64K / 1M" max output is unexplained. It is probably 64K per response, with up to 1M through long decode / background execution, per Schmid's "64K hops".
- The docs show every Gemini 4 example on `client.interactions.create`. I did not verify whether `client.models.generate_content` works with Gemini 4. It probably does, since the changelog mentions GenerateContent for other features, but use Interactions to be safe.

## 2. Decisions API (Gemini Decision 1)

### Takeaway
Call `client.decisions.create(model="gemini-decision-1", input=..., questions={...})` with `google-genai >= 3.2.0` and `api_version="v1beta"`. The three question types are `"noul"` (yes/no), `"choice"` (2–255 options) and `"score"` (2–255 ordered rubric levels). Answers come back in `result.answers[name]` with calibrated probabilities. It costs $0.10 per 1M input tokens, takes about 150–250 ms end to end, and accepts up to 1,000 questions and about 256K tokens of shared input.

### Cited Findings
- Launched Nov 11, 2026. Model ID `gemini-decision-1`. Available through the Gemini API and Google AI Studio. Google Cloud support is "coming soon" — [Google blog](https://blog.google/innovation-and-ai/technology/developers-tools/gemini-decisions-api/); [changelog](https://aistudio.google.com/docs/changelog)
- Question types:
  - `noul` (Yes/No) takes optional true/false description criteria.
  - `choice` takes 2 to 255 options and returns the winner plus normalized probabilities.
  - `score` grades against an ordered rubric starting at 0 and returns a continuous weighted score plus per-level probabilities.
  - Every answer has a 0–1 confidence.
  - Source: [Google blog](https://blog.google/innovation-and-ai/technology/developers-tools/gemini-decisions-api/)
- Limits: up to 1,000 questions per request, inputs up to 256K tokens, nearly flat latency from 1 to 100 questions. Latency is 150–250 ms end to end, with 50–100 ms median model compute. It scores options in one forward pass — [Google blog](https://blog.google/innovation-and-ai/technology/developers-tools/gemini-decisions-api/)
- The codelab gives the context window as 262,144 tokens. Image and video `resolution` is one of `low`, `medium`, `high`, `ultra_high`. It needs `google-genai >= 3.2.0` and `v1beta` — [Codelab](https://codelabs.developers.google.com/getting-started-with-gemini-decisions-api)
- Pricing: $0.10 per 1M input tokens and $0 output. There are no charges for thinking, OCR or transcription. Shared input is billed once per request, and each question adds only its own instruction and criteria tokens. A no-cost tier exists in AI Studio — [Google blog](https://blog.google/innovation-and-ai/technology/developers-tools/gemini-decisions-api/)
- Media can be base64, HTTPS URLs or Files API references. A "Gemini Decisions Playground" exists for testing rubrics — [Philipp Schmid roundup](https://www.philschmid.de/google-deepmind-roundup-nov-2026)
- Measured latency in the codelab: Cloud Run warm calls about 155–195 ms; local calls about 600–950 ms including TLS setup; PDF invoice case about 677 ms — [Codelab](https://codelabs.developers.google.com/getting-started-with-gemini-decisions-api)
- Rate limits: Free 200 RPM / 50K TPM / 10K RPD; Tier 1 1,500 RPM / 1M TPM / 50K RPD — [Rate limits](https://aistudio.google.com/docs/rate-limits)
- Minimal Python, assembled from codelab patterns. Field names are as shown in the codelab, and the circuit content is illustrative:
  ```python
  import base64
  from google import genai
  client = genai.Client(http_options={"api_version": "v1beta"})  # GEMINI_API_KEY env

  img = {"type": "image", "data": base64.b64encode(open("schem.png","rb").read()).decode(),
         "mime_type": "image/png", "resolution": "high"}
  # PDF: {"type": "document", "data": <b64>, "mime_type": "application/pdf"}  (no resolution)

  res = client.decisions.create(
      model="gemini-decision-1",
      input=[{"type": "text", "text": "Netlist:\n" + netlist_text}, img],
      questions={
          "M3_role": {"type": "choice",
                      "instructions": "What is the role of transistor M3?",
                      "criteria": {"input_pair": "Differential input pair device.",
                                   "tail_source": "Tail current source.",
                                   "active_load": "Current-mirror active load.",
                                   "output_stage": "Second-stage / output device.",
                                   "bias": "Bias network device."}},
          "is_two_stage": {"type": "noul",
                           "instructions": "Is this a two-stage op-amp?",
                           "criteria": {"true_description": "Has a second gain stage.",
                                        "false_description": "Single gain stage."}},
          "design_quality": {"type": "score",
                             "instructions": "Rate how well the sim results meet the spec.",
                             "criteria": ["Fails most specs", "Fails some specs",
                                          "Meets all specs", "Meets all with margin"]},
      },
  )
  a = res.answers["M3_role"];        print(a.choice, a.probabilities, a.confidence)
  b = res.answers["is_two_stage"];   print(b.noul, b.confidence)   # noul = P(yes), 0.01-0.99
  c = res.answers["design_quality"]; print(c.score, c.probabilities)
  print(res.usage.input_tokens_by_modality)
  ```
  — fields from [Codelab](https://codelabs.developers.google.com/getting-started-with-gemini-decisions-api)
- Response fields:
  - `noul`: `.noul` (yes-probability) and `.confidence`.
  - `choice`: `.choice` (winning key), `.probabilities` (sums to 1) and `.confidence`.
  - `score`: `.score` (probability-weighted), `.legend`, `.probabilities` and `.confidence`.
  - The API applies no threshold. Your app sets the cutoff.
  - Source: [Codelab](https://codelabs.developers.google.com/getting-started-with-gemini-decisions-api)

### Inferences
- The Decisions API generates no text, so it cannot propose numbers such as gm/Id or L. It can choose among discrete candidates. For example, a `choice` over L ∈ {0.15, 0.5, 1, 2 µm} or gm/Id bins, or a `noul` per candidate design, gives calibrated probabilities that could act as a prior or ranking signal for the optimizer. One call with many questions (one `choice` per transistor role) labels the whole schematic at once for about $0.0001.
- A strong demo use is as a fast, cheap gate or critic: score each ngspice result against a rubric, or ask "should the optimizer stop?". That shows "speed" and "calibrated probabilities" to judges.

### Gaps
- I did not find an official SDK reference page listing the Python types. The field names come from the codelab sample.
- Audio and video block shapes were not checked.
- Files API URIs in decisions inputs were not checked beyond Schmid's statement.

## 3. Managed Agents (Antigravity agent): starting from Python, model choice, sandbox, files, timeouts, budgets, cost, parallelism

### Takeaway
Call `client.interactions.create(agent="antigravity-preview-09-2026", input=..., environment="remote" or {config}, agent_config={"type": "antigravity", "model": "gemini-4-carbon", "max_total_tokens": N})`. This provisions an Ubuntu sandbox with Python 3.12, where the agent can `pip install` at runtime. Files go in through `sources` (inline, git or GCS) and come out as a tar download. The docs do not confirm `apt-get install ngspice`. They give no concurrency limits and no sandbox compute price. For many parallel sizing workers, plain parallel API calls with ngspice running locally is the safer core. Use one Managed Agent run as a showcase piece, such as an "independent verifier" or a "testbench builder", to compete for the Managed Agents prize.

### Cited Findings
- Agent ID `antigravity-preview-09-2026` (released Sep 17, 2026). It replaces `antigravity-preview-05-2026`, which shut down Oct 5. Built-in tool names and parameters changed — [changelog](https://aistudio.google.com/docs/changelog)
- Basic call: `genai.Client(http_options={"api_version": "v1beta"})`, then `client.interactions.create(agent=..., input=..., environment="remote")`. The response has `id`, `environment_id` and `output_text`. Multi-turn works through `previous_interaction_id` and passing `environment=<env_id>` again. Streaming uses `stream=True`. Context compacts automatically at about 135k tokens — [Managed agents quickstart](https://aistudio.google.com/docs/managed-agents-quickstart.md.txt)
- Selectable models (`agent_config.model`): `gemini-4-hydrogen` (default), `gemini-4-carbon`, `gemini-4-argon`, `gemini-4-neon`, `gemini-3.8-flash`, `gemini-3.6-flash` and `gemini-3.5-flash-lite`. For a saved agent created with `agents.create`, the model is locked at creation — [Antigravity agent docs](https://aistudio.google.com/docs/antigravity-agent)
- Budget: `agent_config={"type": "antigravity", "max_total_tokens": 50000}` counts input, output and thinking tokens but not cached tokens. The limit is best-effort. When it is hit, `interaction.status == "incomplete"`. Read usage from `interaction.usage.total_tokens` — [Antigravity agent docs](https://aistudio.google.com/docs/antigravity-agent)
- Verbatim model-selection example:
  ```python
  interaction = client.interactions.create(
      agent="antigravity-preview-09-2026",
      input="...",
      environment="remote",
      agent_config={"type": "antigravity", "model": "gemini-4-carbon"},
  )
  print(interaction.output_text)
  ```
  — [Antigravity agent docs](https://aistudio.google.com/docs/antigravity-agent)
- Tools: `code_execution` (bash, Python, Node), `google_search`, `url_context`, filesystem tools (automatic when `environment` is set), `function` (custom functions) and `mcp_server` (remote MCP over streamable HTTP). Synchronous hooks can intercept `code_execution` and filesystem operations — [Antigravity agent docs](https://aistudio.google.com/docs/antigravity-agent)
- Limitations: multimodal input to the agent is `text` and `image` only, with images as inline base64 `data`. PDFs are not listed. Background execution requires `store=True`, which is the default. Trigger execution timeout defaults to 600 s (`execution_timeout_seconds`). No overall max run duration is stated — [Antigravity agent docs](https://aistudio.google.com/docs/antigravity-agent)
- Sandbox:
  - Pre-installed: Ubuntu with Python 3.12 (numpy, pandas, requests, google-genai, beautifulsoup4, pyyaml, ast-grep-cli), Node 22, and curl, git, jq and the gcloud CLI.
  - The agent "can install additional packages at runtime using `pip install` or `npm install`". Packages persist if you reuse the `environment_id`.
  - apt-get is NOT mentioned.
  - Source: [Environments](https://aistudio.google.com/docs/agent-environment)
- Sandbox resources: 4 CPU and 16 GB RAM, per a search snippet of Google docs that I did not fetch directly. The environments page says only "fair usage limits" for storage — [Managed agents quickstart (search snippet)](https://ai.google.dev/gemini-api/docs/managed-agents-quickstart); [Environments](https://aistudio.google.com/docs/agent-environment)
- Environment config schema: `{"type": "remote", "sources": [...], "network": {...}, "env": {...}}`.
  - Sources are `{"type": "repository", "source": git_url, "target": "/workspace/x"}`, `{"type": "gcs", "source": "gs://...", "target": ...}` or `{"type": "inline", "content": "...", "target": "/workspace/file"}`.
  - Size limits: repository 500 MB, GCS 2 GB, inline 1 MB per file and 2 MB total.
  - Network egress is unrestricted by default. You can set an allowlist or `"disabled"`.
  - Hooks are defined in a mounted `.agents/hooks.json`.
  - Source: [Environments](https://aistudio.google.com/docs/agent-environment)
- Lifecycle: Active, then Idle (snapshotted and stopped after 15 min of inactivity), then Offline (kept 7 days and resumable by ID), then Deleted — [Environments](https://aistudio.google.com/docs/agent-environment)
- Files out: HTTP GET `/v1beta/files/environment-{env_id}:download?alt=media` with header `x-goog-api-key` returns a tar of the environment. There is no SDK method yet. There is also `GET /environments/{id}/files?recursive=true` for listings — [Quickstart](https://aistudio.google.com/docs/managed-agents-quickstart.md.txt); [Environments](https://aistudio.google.com/docs/agent-environment)
- Self-hosted environments (new in November): Google runs inference and the agent loop, while bash and file edits run on your own compute. The worker connects over outbound HTTPS. Supported targets are Modal, E2B, Cloudflare, Daytona, Fly.io, Vercel, Cloud Run, or local mode, which runs bash on your host — [Philipp Schmid roundup](https://www.philschmid.de/google-deepmind-roundup-nov-2026)
- Pricing is pay-as-you-go on the underlying model's tokens plus tools. The docs state no sandbox compute price. Free-tier projects get some quota, with no figures given — [Antigravity agent docs](https://aistudio.google.com/docs/antigravity-agent)

### Inferences
- **ngspice in the hosted sandbox is unverified.** Options, from most to least likely to work:
  1. `pip install` a wheel that bundles ngspice. Possible routes are the `PySpice` route (which needs a shared lib) or conda-forge. Neither is verified.
  2. Have the agent try `apt-get install -y ngspice`. It may lack root.
  3. Push a prebuilt static ngspice binary plus SKY130 models through a `gcs` source (≤2 GB). Note that the full SKY130 PDK is large, so ship only the extracted `sky130_fd_pr` model files you need.
  4. Use **self-hosted / local mode**, where the agent's bash runs on your laptop, which already has ngspice and SKY130 installed. This is probably the fastest reliable way to show "Managed Agents + real ngspice" today.
- Test `apt-get` and `pip` in the first 15 minutes with one cheap `gemini-4-hydrogen` run, and set `max_total_tokens`.
- For many parallel workers, the Managed Agent loop adds sandbox provisioning and agent-loop overhead, and the docs give no concurrency limits. Direct `client.interactions.create` calls to Hydrogen from a Python asyncio or ThreadPool fan-out, with ngspice running locally in a process pool, gives more control and more predictable latency. Keep Managed Agents for one or two clearly scoped jobs, such as "build and run the testbench, report the specs", "independent audit of the final sizing" or "write the report".
- The agent accepts only text and image input, not PDF. Pass the datasheet as an inline or GCS source file, or pre-extract the facts with a Gemini 4 model.

### Gaps
- The docs give no concurrency or parallel-environment limits, sandbox compute pricing, root or apt availability, maximum interaction wall-clock time, or exact upload endpoints. The environments page was truncated past 100K characters.
- The hooks JSON schema was not retrieved.
- The May launch date of Managed Agents is inferred only from the old `-05-2026` agent ID.

## 4. Structured output (JSON schema), function calling, and PDF/image input in google-genai

### Takeaway
On the Interactions API, pass `response_format={"type": "text", "mime_type": "application/json", "schema": Model.model_json_schema()}` and validate with Pydantic. `minItems` and `maxItems` are enforced. `minLength`, `maxLength` and similar constraints are silently stripped, and numeric ranges should also be re-validated in code. Structured output can be combined with function calling and built-in tools as a preview feature on Gemini 3/4. PDFs go in as `{"type": "document", "data": b64 | "uri": file.uri, "mime_type": "application/pdf"}`, up to 50 MB and 1,000 pages. Use `client.files.upload` for reuse, since files are kept for 48 h.

### Cited Findings
- Structured output example, condensed from the docs:
  ```python
  from pydantic import BaseModel
  class Proposal(BaseModel):
      W_um: float; L_um: float; gm_id: float
  r = client.interactions.create(
      model="gemini-4-hydrogen",
      input="...",
      response_format={"type": "text", "mime_type": "application/json",
                       "schema": Proposal.model_json_schema()},
  )
  p = Proposal.model_validate_json(r.output_text)
  ```
  — pattern from [Structured output docs](https://aistudio.google.com/docs/structured-output)
- Only a subset of JSON Schema is supported. `minLength`, `maxLength`, `minProperties`, `maxProperties`, `uniqueItems` and `contains` are stripped, not enforced. `minItems` and `maxItems` ARE enforced. Very large or deeply nested schemas can return `400 INVALID_ARGUMENT` — [Structured output docs](https://aistudio.google.com/docs/structured-output)
- Structured outputs combine with Google Search, URL Context, Code Execution, File Search and Function Calling. This is in preview and only on Gemini 4 and Gemini 3 series models — [Structured output docs](https://aistudio.google.com/docs/structured-output)
- Inline PDF: `{"type": "document", "data": base64.b64encode(pdf_bytes).decode("utf-8"), "mime_type": "application/pdf"}`. Files API: `f = client.files.upload(file="file.pdf")`, then `{"type": "document", "uri": f.uri, "mime_type": f.mime_type}`. The limit is 50 MB per file and 1,000 pages per PDF. Uploaded files are kept for 48 hours. Gemini 3/4 PDF cost is about 280, 560 (default) or 1,120 tokens per page at low, medium or high resolution. The block also accepts optional `resolution` and `processing` fields — [Document processing](https://aistudio.google.com/docs/document-processing)
- Image block shape as used in Google's Decisions codelab: `{"type": "image", "uri" | "data": ..., "mime_type": "image/png", "resolution": "low|medium|high|ultra_high"}` — [Codelab](https://codelabs.developers.google.com/getting-started-with-gemini-decisions-api)
- Neon (Nov 18) added Office/OpenDocument input and slicing of documents — [changelog](https://aistudio.google.com/docs/changelog)
- Tool retrieval (`defer_loading: true`) entered Preview on Nov 19 for Gemini 3.6+ on the Interactions API — [changelog](https://aistudio.google.com/docs/changelog)

### Inferences
- Pydantic `Field(ge=..., le=...)` bounds are probably not enforced by the decoder, since the docs list only the stripped string and object constraints and do not mention numeric ones. Clamp gm/Id (for example 5–25 S/A) and L (≥0.15 µm for SKY130) in Python after `model_validate_json`.
- For a 1,000-page-max datasheet, set `resolution: "low"` unless the figures matter. For a schematic image, use `"high"`.
- Upload the datasheet once with `client.files.upload` and reuse its URI across Carbon, Hydrogen and Decisions calls.

### Gaps
- I did not fetch the exact Interactions API function-calling declaration schema (`tools=[{"type": "function", ...}]`). Check https://aistudio.google.com/docs/function-calling.
- I did not confirm whether `client.models.generate_content` with `GenerateContentConfig(response_schema=..., thinking_config=ThinkingConfig(thinking_level=...))` works for Gemini 4. All official Gemini 4 examples use `client.interactions.create`.
- I did not fetch the image-specific docs (file-input-methods, media-resolution) for Interactions. The image block shape is inferred from the Decisions codelab.
- I did not fetch the minimum `google-genai` version for Interactions with Gemini 4. The Decisions API needs ≥ 3.2.0, so `pip install -U "google-genai>=3.2.0"` covers both.

## 5. Official Gemini 4 NYC hackathon pages (rules, rubric, prizes)

### Takeaway
I found no public official page for a "Gemini 4 NYC hackathon" on 2026-11-21. None of the user's stated rules or prizes could be verified: the 20% "≥2 Gemini 4 models + Decisions API" criterion, the $5k Managed Agents prize, "must go beyond a text box", the ban on dashboards as the main feature, and "evaluation not entirely team-written examples". Rely on the organizers' printed or Discord materials.

### Cited Findings
- Searches returned only other NYC events: "Build with Gemma NYC" (Gemma 4, $2,000 pool); the Google Cloud "Agents for Impact" hackathon on Dec 8, 2026; and "The New Agentic Economy Hackathon" (Forever 22 at Betaworks with Google DeepMind support, marked as a past event, with no date, prizes, rules or Gemini 4 / Decisions API mentions on the page) — [Luma: New Agentic Economy Hackathon](https://luma.com/1ofx14ov); [Agents for Impact NYC](https://cloud.google.com/events/agents-for-impact-nyc); [Build with Gemma NYC](https://luma.com/9ujmu9jp)
- The Nov 16 DeepMind roundup covers Gemini 4, the Decisions API and self-hosted sandboxes, but mentions no hackathon — [Philipp Schmid roundup](https://www.philschmid.de/google-deepmind-roundup-nov-2026)

### Inferences
- The event may be the "New Agentic Economy" series, or a private or invite-only page. Treat the user's rubric notes as the working assumption, but confirm them with the organizers.
- If "evaluation must not be made up entirely of examples the team wrote" applies, ngspice simulation on SKY130 against datasheet-derived specs (not invented specs) is a strong answer. Published reference designs or textbook op-amp specs would also count as external benchmarks.

### Gaps
- Official rules, judging rubric, prize terms (including the $5k Managed Agents prize) and any hackathon API credits or quota uplift could not be found online.
