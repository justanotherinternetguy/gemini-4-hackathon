# Handoff: circuit board routing swarm

Written 21 Nov, about 14:00. Submission is at 17:00.

## Part 1. For Hendry, in plain English

We changed problems. The project is now **circuit board routing**, not analog sizing. Your analog
code on `main` is untouched, and the new work is in `pcb/` on the branch `shaurya/demo`.

**The problem.** A circuit board has parts with metal pads, and a list of which pads must be
connected. Routing means drawing copper lines between those pads so that everything connects and
no rule is broken (lines too close, lines crossing, and so on). It is a spatial puzzle.

**The benchmark.** PCBWorld is an open benchmark from LG AI Research with 677 real open-source
boards. It scores a routed board automatically using the same rule checker engineers use. The
headline number is the share of boards that come back fully connected with zero rule violations.

**Why it is a good target.** On small real boards the best classic router passes 80% and a GPT-5.4
agent passes 65%. On the next tier up, the GPT-5.4 agent passes 0%. Nobody has published a Gemini
number.

**The split of work.**
- You: build the managed-agent swarm that routes boards and beats those numbers.
- Shaurya: the baselines, the scoring runs, the live viewer, the pitch and the video.

**The only thing you owe us is board files.** Whenever your swarm has a new best version of a
board, save it as a `.kicad_pcb` file in an agreed folder. Our viewer redraws it live and our
scoring run grades it. You do not need to write a log format or touch any scoring code.

**Do this first.** Check whether the routing engine can be built inside Google's managed sandbox.
Everything about the Managed Agents prize depends on that answer. Details are in Part 2, step 1.

**One trap already found.** Gemini 4 uses up a small output limit before it writes its answer.
PCBWorld's built-in agent defaults to 256 tokens and routes nothing. Use 4,096 or more everywhere.

## Part 2. Brief for Hendry's coding agent

### Your task

Build a swarm of Gemini managed agents that routes PCBWorld's real boards and scores a higher
clean-pass rate than the baselines in the table below, under PCBWorld's own scoring.

Done means: for a list of boards, a folder of routed `.kicad_pcb` files produced by the swarm,
plus the attempt count and wall-clock time per board. Shaurya's side scores them.

### Numbers to beat

Clean pass = fully connected and zero error-level rule violations. Published numbers are from the
PCBWorld paper (arXiv 2607.05915, Table 3), best of 5 attempts per board.

| Method | D3-A: 99 small real boards | D3-B: 10 medium real boards |
|---|---|---|
| Human original routing | 1.00 | 1.00 |
| Freerouting (classic router) | 0.80 | 0.78 |
| Reinforcement-learning agent (PPO) | 0.86 | 0.45 |
| GPT-5.4 agent | 0.65 | 0.00 |
| GPT-5.4-mini agent | 0.28 | 0.00 |
| Freerouting, re-run here today | 0.84 (83 of 99, one run) | not run yet |
| Single Gemini 4 Hydrogen agent, run here today | in progress | not run yet |

The two rows that matter most: **Freerouting** is the strongest existing method, and the **single
Gemini 4 agent** is the fair comparison that shows the swarm, not just the model, made the
difference. Shaurya is producing both.

### Hard rules (from the hackathon's participant guide)

- Use at least two Gemini 4 models, each with a clear job. Model ids: `gemini-4-carbon` (strongest),
  `gemini-4-neon`, `gemini-4-argon`, `gemini-4-hydrogen` (fast; the default managed agent runs on it).
- The project must go beyond text. Board images should be an input the system actually uses.
- No demos on scripted inputs, and no evaluation made only of examples we wrote. Use the benchmark's
  own board lists.
- The repository must be public by submission. It is private right now.
- Only work built today may be shown.
- Judging: live demo 35%, proof it works 25%, creativity 20%, use of Gemini 4 and the Decisions API 20%.
- There is a separate $5,000 prize for best use of Managed Agents. We are going for it.

### Rules we set for ourselves

- **The swarm never scores itself.** It may run the rule checker as feedback while working. The
  reported numbers come from PCBWorld's separate evaluation step on the saved files.
- **Count every attempt.** The published protocol is best of 5 attempts per board. If the swarm
  uses more, say how many.
- **Keep development boards apart from reported boards.** In `configs/datasets/d3.json` the easy
  tier's train and test lists are the same 99 boards, so develop on the medium and hard train pools
  (286 and 292 boards) and report on the test lists.
- **Report failures.** Boards the swarm could not finish go in the table.

### Step 1, before anything else: can the engine run in the managed sandbox?

The sandbox is Ubuntu with Python 3.12 and Node 22, with outbound network on by default. PCBWorld's
setup needs conda and compiles a modified KiCad (about 960 build steps; 7 minutes on an M4 Pro).

Try, in this order, and stop at the first that works:

1. **Build it in the sandbox once and save that as the base environment.** Every later agent run
   forks the base environment, so the build cost is paid once.
2. **Build it on your Linux machine and ship the built tree** into the base environment as a source.
3. **Keep the engine on your machine and expose routing and rule-check as a small HTTP service.**
   Agents in the sandbox call it as a tool. This still counts as a managed-agent swarm.

Report which one worked within 20 minutes. If none does, say so immediately.

### Setting up PCBWorld on Linux

Linux x86_64 is the primary platform, so none of the Mac workarounds apply.

```bash
git clone --recursive https://github.com/LGAI-Research/PCBWorld.git pcbworld && cd pcbworld
bash tools/setup/setup_all.sh          # needs conda with the libmamba solver; Miniforge works
conda activate pcbworld
export PYTHONPATH=build_rl/pcbnew/python/rl:. PCBWORLD_DATA_ROOT=$PWD/var/datasets
bash tools/quickstart/prepare_pcbench.sh --workers 10   # builds kicad-cli, prepares all 677 boards
```

Where things land:

- Boards: `var/datasets/pcbench/exacad_sorted/<NNNN>_<name>/`
  - `processed_v9_guide_v3_unrouted.kicad_pcb` is the starting board (no copper).
  - `processed_v9_guide_v3.kicad_pcb` is the human original.
  - `processed_v9_guide_v3.kicad_pro` holds the board's design rules. A board checked without it is
    checked against KiCad's defaults and gets wrong violation counts.
- Board lists: `configs/datasets/d3.json`. `easy.test` is D3-A (99), `medium.test` is D3-B (10),
  `hard.test` is D3-C (10).
- Read these: `docs/ENVIRONMENT.md` (state, actions, reward), `docs/METRICS.md`, `docs/QUICKSTART.md`
  sections 1 to 3 (result file layout and the scoring commands), `eval/README.md`.

Commands verified today:

```bash
# Classic router on D3-A. About 6 s per board, 8 in parallel.
python methods/baselines/rule_based/run_rule_based_routers.py --baseline freerouting \
  --dataset pcbench --difficulty easy --workers 8 --output-root var/results/baselines
# routed boards land in var/results/baselines/PCBench_easy_test/freerouting/seed0/routed/

# PCBWorld's built-in single agent on Gemini 4 (this repo's helper; see its header)
export PCBWORLD_REPO=$PWD
bash /path/to/gemini-4-hackathon/pcb/bench/run_agent_baseline.sh gemini-4-hydrogen easy hydrogen_d3a
```

The built-in agent lives in `methods/llm_agent/`. Its prompt and action format are a working
reference for driving the engine from a model: select a net, start a route, draw lines, place vias,
end the net.

### Gemini facts verified today

- **Key:** the event key works for `gemini-4-hydrogen` and `gemini-4-carbon`. Both answered a
  trivial call in about 3 seconds.
- **Output limit:** set 4,096 or more. At 256 the model returns about 3 tokens.
- **Managed Agents** (docs: ai.google.dev/gemini-api/docs/agents, `/managed-agents-quickstart`,
  `/custom-agents`). Public preview, API version `v1beta`.
  - One call provisions a sandbox and runs the agent:
    `client.interactions.create(agent="antigravity-preview-09-2026", input=..., environment="remote")`.
  - Continue with `previous_interaction_id` (conversation) and `environment=<environment id>` (files).
  - Save a reusable agent with `client.agents.create(id=..., base_agent="antigravity-preview-09-2026",
    agent_config={"type": "antigravity", "model": ...}, system_instruction=..., base_environment=...)`.
    Each run of a saved agent forks its base environment.
  - Files come out through the Files API: `GET .../v1beta/files/environment-<id>:download?alt=media`.
  - One interaction typically uses 100k to 3M tokens. Context is compacted automatically near 135k.
- **Decisions API** (docs: ai.google.dev/gemini-api/docs/decisions-api). Model `gemini-decision-1`,
  `client.decisions.create(model=..., input=..., questions={...})`. It answers yes/no, choice and
  rating questions over text, images, video, audio and PDFs with calibrated probabilities, and
  writes no text. It needs `google-genai` 3.2 or later.
- **Version clash:** PCBWorld's conda environment pins `google-genai` 2.9.0. Run the swarm's own
  Gemini calls from a separate virtual environment so the Decisions API and Managed Agents work.

Your own notes in `research_notes/` cover the Gemini APIs in more depth. Where they disagree with
this page on hackathon rules, this page is right: it comes from the official participant guide.

### What the baseline results suggest (suggestions, not requirements)

- **Do not route from scratch with a model.** The published GPT-5.4 agent takes 231 seconds on a
  small board and never finishes a medium one. Freerouting takes about 6 seconds.
- **Start from Freerouting's output and repair it.** In today's run Freerouting left 16 of the 99
  small boards not clean. Twelve were incomplete (73 to 94% of connections made) and four were
  fully connected with 1 to 4 rule violations. Finishing the last few connections or fixing a
  handful of violations is a much smaller job than routing a board, and it is the whole gap
  between 0.84 and 1.00. The 16 boards, with per-board numbers, are in
  `pcb/bench/results/freerouting_d3a.json`.
- **Be honest about those 16.** They are in the reported set. If the swarm is developed directly on
  them, the write-up has to say so. Developing on Freerouting's failures in the medium and hard
  train pools, then running on these 16 untouched, is the stronger claim.
- **Give agents different strategies and keep the best board.** For example: a different order of
  connections, a different layer preference, rip up and re-route around one violation. Best-of-N is
  how the benchmark already scores.
- **Split planner and worker.** A Carbon planner reads the board and the rule-check report and
  assigns repairs; Hydrogen workers execute them in their own sandboxes. The PCB Gym paper names
  this split as the natural next step.
- **Feed the planner a picture of the board.** Published benchmarks say models fail here on spatial
  reasoning. Run with and without the picture and report the difference honestly: the PCB Gym paper
  found no solid link between looking at images and scoring better.
- **Use the Decisions API as a cheap screen,** for example "is this violation fixable by moving one
  line?", to decide which candidates deserve a full worker run.

### What to hand back

- A folder of routed boards per run: `<run>/<board_id>.kicad_pcb` for the final board. For the live
  demo, also save each new best version as it happens into a folder we agree on. Files are shown in
  the order they are saved.
- **Credit the agent in the file name.** End each live version's name with `__<agent>`, for example
  `0007__w3.kicad_pcb` when worker w3 produced that change. The 3D viewer draws the copper that is
  new in that version with w3's marker laying it, and several agents saving close together are
  shown working at the same time. Save the starting board (no new copper) first, with no suffix.
  Only credit an agent for work it really did: the viewer shows whatever the file names say.
- Per board: attempts used, wall-clock seconds, and whether the swarm believes it is clean.
- The exact command that reproduces the run.

### On this branch

- `pcb/view3d.py` (with `view3d.html`, `board_parse.py`, `vendor/`): the demo. Import a board,
  watch it assemble from the file, press Route, watch each agent lay its copper, then get a
  verdict from KiCad's checker: a clean pass, or the exact connections left for the engineer.
  Tested today with made-up workers.
  `cd pcb && python view3d.py --boards-dir $PCBWORLD_DATA_ROOT/pcbench/exacad_sorted --route-cmd '<your command>'`
  **`--route-cmd` is how your swarm plugs in.** The page's Route button runs it with these filled
  in: `{unrouted}` the starting board, `{project}` its `.kicad_pro`, `{out}` the folder to save
  versions into. Save versions as described above, and exit when finished. Without `--route-cmd`
  the Route button runs a dummy with fake agents, and the page labels it as test data.
- `pcb/live_view.py`: the flat 2D fallback viewer with the same counts. Tested today.
  `python pcb/live_view.py --watch <folder> --project <board>.kicad_pro --port 8765`
- `pcb/make_test_frames.py`: makes a fake step-by-step sequence from a finished board with made-up
  worker names, only for testing the viewers. Never present its output as a real run.
- `pcb/bench/run_agent_baseline.sh` and `pcb/bench/models.json`: the single-agent baseline.

### Time

| By | What |
|---|---|
| 14:20 | Step 1 answered: where the engine runs |
| 15:15 | Swarm repairs Freerouting's failed boards on a development set |
| 16:00 | Freeze. Final run on the reported board lists starts |
| 16:30 | Routed boards handed over for scoring; demo and video recorded |
| 17:00 | Submission |

### Not verified

- Whether the engine builds in the managed sandbox.
- PCBWorld's final scoring step (`eval/pipeline.py --stages eval,aggregate`) has not been run here
  yet. The 0.84 above is from the router runner's own evaluation of each routed board, one run.
  The published 0.80 is a mean over four runs.
- Any Gemini 4 result on the medium or hard tiers.
