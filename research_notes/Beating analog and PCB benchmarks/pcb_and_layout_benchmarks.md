# PCB-routing and analog-layout agent benchmarks: prior numbers and how a swarm could beat them (as of 2026-11-21)

Read this first: the handoff's "numbers to beat" are out of date. A paper from 5 Nov 2026
(PCBRouteAgent, arXiv 2611.08175) already reports 0.949 clean pass on D3-A and 1.000 on D3-B on
PCBWorld-Bench with Claude Opus 5.5. The PCBWorld paper itself has an ablation row at 0.94 on D3-A.
"Beat 0.86" is still a true statement about PCBWorld's headline table, but it is not the state of
the art.

Legend: "verified" = read on the primary source today. "local test" = run on this machine today.
"unverified" = from a search snippet or the teammate's handoff only.

## 1. PCBWorld: numbers, protocol, action space, failure modes, Gemini

### Takeaway
The handoff's numbers match Table 3 of the paper, and they are best-of-5 (@5) values; the
single-run (@1) values are much lower (Freerouting 0.75, PPO 0.68, GPT-5.4 0.42 on D3-A). No Gemini
result is published on PCBWorld-Bench in either the PCBWorld paper or PCBRouteAgent.

### Cited Findings

Paper identity
- PCBWorld: A Benchmark Environment for Engine-Grounded PCB Design Automation; Song et al. (LG AI Research); v1 7 Jul 2026, v4 11 Sep 2026; accepted to a KDD 2026 workshop (non-archival). — [arXiv 2607.05915](https://arxiv.org/abs/2607.05915)
- The abstract says 679 real boards, not 677. The paper's split is D3-A 100 (99 evaluated), D3-B 287, D3-C 292. Zero-shot evaluation uses 99 D3-A boards and 10 each from D3-B and D3-C. The excluded D3-A board is `0096_karabas-nano_wifi_revA`. — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)
- D3-C is not evaluated in the PCBWorld paper: the ten-board subset "is fixed for reference and is not evaluated in this paper." — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)

Table 3, @5 protocol (CP = clean pass, Rout. = routability, Time in seconds per rollout) — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915); D3-A column also in the [repo README](https://github.com/LGAI-Research/PCBWorld)

| Method | D2 CP | D3-A CP | D3-A Rout. | D3-A Time | D3-B CP | D3-B Rout. | D3-B Time |
|---|---|---|---|---|---|---|---|
| Reference (human) | - | 1.00 | 1.00 | - | 1.00 | 1.00 | - |
| Freerouting 2.1.0 | 1.00 | 0.80 | 0.91 | 7.09 | 0.78 | 1.00 | 9.94 |
| OrthoRoute | 0.01 | 0.02 | 0.53 | 2.20 | 0.00 | 0.44 | 9.30 |
| KiCadRoutingTools | 1.00 | 0.74 | 0.94 | 0.65 | 0.20 | 0.86 | 3.27 |
| GPT-5.4 agent | 0.96 | 0.65 | 0.91 | 231.15 | 0.00 | 0.62 | 865.83 |
| GPT-5.4-mini | 0.58 | 0.28 | 0.72 | 56.85 | 0.00 | 0.61 | 422.06 |
| GPT-5.4-nano | 0.55 | 0.30 | 0.73 | 100.76 | 0.00 | 0.59 | 510.25 |
| Qwen3.5-397B | 0.33 | 0.34 | 0.75 | 81.82 | 0.00 | 0.51 | 222.29 |
| PPO | 1.00 | 0.86 | 0.95 | 1.83 | 0.45 | 0.85 | 10.77 |
| GRPO | 1.00 | 0.85 | 0.98 | 3.54 | 0.10 | 0.69 | 11.20 |
| PPO (terminal) | 0.92 | 0.82 | 0.95 | 2.58 | 0.38 | 0.81 | 12.60 |
| PPO (w/o finish), ablation | 1.00 | **0.94** | 0.99 | 3.05 | 0.42 | 0.87 | 14.44 |

Table 22, @1 single rollout on D3-A (CP / error-level DRV per board / wirelength mm / vias) — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)

| Method | CP | DRV | WL | Via |
|---|---|---|---|---|
| Freerouting | 0.75 | 1.08 | 156.6 | 0.86 |
| KiCadRoutingTools | 0.74 | 2.38 | 142.8 | 3.69 |
| GPT-5.4 | 0.42 | 2.71 | 148.1 | 2.36 |
| GPT-5.4-mini | 0.13 | 7.16 | 81.9 | 0.21 |
| PPO | 0.68 | 1.91 | 164.9 | 1.09 |
| PPO (w/o finish) | 0.81 | 0.95 | 262.7 | 3.87 |

- D3-B @1: Freerouting CP 0.64, DRV 11.94, WL 533.5, vias 3.88; KiCadRoutingTools CP 0.20; GPT-5.4 CP 0.00, DRV 24.88. — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)

Protocol
- @5: each method routes each board five times and the rollout with the largest potential gain is kept (not the one with the best clean pass). Time is the mean over all five rollouts. @1 is a single rollout with no selection. Deterministic baselines run once. — [docs/METRICS.md](https://raw.githubusercontent.com/LGAI-Research/PCBWorld/main/docs/METRICS.md)
- Freerouting and the RL agents report the mean over 4 seeds. — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)
- Clean pass = fraction of boards fully connected with zero error-level DRC violations; it is the headline metric. DRV counts error-level violations only. Other metrics: potential gain, routability, wirelength (mm), vias, time, parse-fail. — [docs/METRICS.md](https://raw.githubusercontent.com/LGAI-Research/PCBWorld/main/docs/METRICS.md)
- "Fully connected" (`success`) = every net's pads form one connectivity group. Routability = (initial groups - current groups) / (initial groups - 1), over clusters holding at least one pad. — [eval/README.md](https://raw.githubusercontent.com/LGAI-Research/PCBWorld/main/eval/README.md)
- Freerouting setup in the paper: `freerouting-2.1.0.jar`, OpenJDK 21.0.6, `-mp 10`, `-Xmx` 4 GiB, other options at defaults, input pre-converted to DSN. — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)

LLM agent
- Six actions: `net_select`, `net_end`, `start_route`, `make_line`, `make_via`, `finish` (auto-completes a route to the nearest unconnected pad). Observation is a text S-expression of the board, coordinates rounded to 0.001 mm; history is the last two think/action turns plus rejected-action notes; step budget T = 200; text only, no images. — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)
- The agent cannot delete or edit committed copper: track-removal APIs exist but are excluded from the action space, so it struggles with congestion deadlocks once formed. This is a stated limitation. — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)
- Failure modes reported: net ordering (starting with a harder net and rarely recovering, case `0018_hy_adapter`); repeated rerouting after congestion instead of anticipating it (case `0100_smt-zvs-driver`); DRC-code-level errors such as via-on-pad, wrong-layer endpoints, cross-net contact, stranded segments, detours over the board edge. — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)
- Interaction mode matters: on D3-A GPT-5.4 leaves 34.58 DRV per board when generating without the engine, 16.28 in plan-and-execute, 1.31 in interactive mode. All LLM agents score 0.00 CP on D3-B. — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)
- Models evaluated: GPT-5.4, GPT-5.4-mini, GPT-5.4-nano, Qwen3.5-397B. No Gemini model. The repo supports a Gemini provider but publishes no Gemini number. — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915); [repo README](https://github.com/LGAI-Research/PCBWorld)

Follow-up result on the same benchmark (the real number to beat)
- PCBRouteAgent (Zhang et al., 5 Nov 2026, v2 9 Nov): an LLM global-routing planner that reads board images plus structured data, and a detailed-routing executor on KiCad's native router with bounded local recovery. 247 boards across D2, D3-A (99), D3-B (10), D3-C (10). — [arXiv 2611.08175](https://arxiv.org/abs/2611.08175)
- Clean pass, scored with PCBWorld's official evaluator, one scored board per instance — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2):

| Method | D2 | D3-A | D3-B | D3-C |
|---|---|---|---|---|
| Reference | - | 1.000 | 1.000 | 0.900 |
| Freerouting (@1) | 1.000 | 0.750 | 0.640 | 0.300 |
| PCBWorld PPO (@1) | 0.980 | 0.680 | 0.210 | 0.000 |
| PCBWorld PPO w/o finish (@1) | 1.000 | 0.810 | 0.220 | - |
| PCBWorld GPT-5.4 (@1) | 0.880 | 0.420 | 0.000 | - |
| PCBWorld agent on Claude Haiku 5.5 | 0.633 | 0.343 | 0.000 | 0.000 |
| PCBRouteAgent (Haiku 5.5) | 1.000 | 0.929 | 0.800 | 0.100 |
| PCBRouteAgent (Opus 5.5) | 1.000 | **0.949** | **1.000** | **0.400** |
| PCBRouteAgent without the LLM planner | 0.961 | 0.535 | 0.000 | 0.000 |

- PCBRouteAgent budget: 12 replans per board, at most four per connection, five batch-replanning rounds, up to five angle-repair passes; the D3-B result includes retries and angle repair. Recovery includes at most one rip-up per obstructing net. Haiku cost on D3-A: 27 s and $0.0048 per board. Code: github.com/chip-ai-agent/PCBRouteAgent. No image ablation is reported. No Gemini model. — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2)

Handoff and local file
- The team's re-run is 83 of 99 clean (0.8384), 87 fully connected, Freerouting 2.1.0, 1 seed, scored by the runner's own `evaluate_one`, not the 3-stage pipeline. — local file `/home/thinkies/NewGit/gemini-4-hackathon/pcb/bench/results/freerouting_d3a.json`
- That file lists 16 non-clean boards, matching the handoff: 12 incomplete (routability 0.733 to 0.938, each with 1 to 4 error violations) and 4 fully connected with violations (0021: 1, 0026: 4, 0066: 3, 0088: 2). — local file, same path

### Inferences
- The team's 0.84 is a single run and is above the published single-run 0.75 and even the published @5 0.80. The repo is now v1.2.2 and its Freerouting runner has DSN rule checks and an optional 90-degree snap patch, so the baseline may have moved since the paper. Quote it as "our re-run", never as the published number.
- On D3-A, 99 boards means: 86 clean boards beats PPO's 0.86; 94 ties PCBRouteAgent's 0.949 and edges the 0.94 ablation; 95 is needed to beat PCBRouteAgent. From 83 that is +3, +11, +12 boards.
- D3-B is already at 1.000 (PCBRouteAgent); it can only be tied. D3-C (0.40 best, human reference 0.90) is the open tier, and it is 10 boards.

### Gaps
- Table 21 (@5 DRV, wirelength, vias) was not captured. Use the @1 values above or read the PDF at `third_party/pcbworld/PCBWorld.pdf`.
- 677 vs 679 boards and 286 vs 287 medium boards: the handoff and the paper differ by small counts. Probably boards that fail to load; not confirmed.
- How "seed" changes Freerouting's output is not documented; Freerouting's docs list no seed flag.

## 2. Related benchmarks: PCB Gym, OmniRouting, HWE-Bench, AnalogLayoutWorld, CASCoDe, ALIGN/MAGICAL

### Takeaway
Every 2026 benchmark tells the same story: models produce an artifact easily and fail the verifier. None of them is a fair target for a claim today except PCBWorld-Bench. The team's analog layout-planning stage produces no GDS, so it cannot be scored on any of the analog benchmarks.

### Cited Findings
- PCB Gym (NeurIPS 2026): joint placement and routing from designs written in Zener (schematic-as-code), open-source boards with 2 to 12 copper layers, deterministic gates plus a rubric-based agentic grader. Frontier LLMs produce DRC-valid, fully routed boards on 32.1% of attempts and often route power rails like ordinary signals. — [NeurIPS 2026 listing](https://neurips.cc/virtual/2026/161602)
- OmniRouting (5 Aug 2026): 1,681 industrial PCB designs with schematics; four tasks (geometric routing, design-rule-aware routing, electrical functionality, tool-augmented agentic routing). The abstract reports weak path planning, poor design-rule adherence and inconsistent preservation of electrical function; it gives no headline number. — [arXiv 2608.04434](https://arxiv.org/abs/2608.04434)
- HWE-Bench (18 Mar 2026): 300 board-level schematic design tasks, 2,914 datasheets; best model passes 8.15% (ERC then simulation). It is schematic design, not routing. — [arXiv 2603.18102](https://arxiv.org/abs/2603.18102)
- AnalogLayoutWorld (12 Nov 2026, NovaSilicon Team): 40 expert-reviewed tasks, layout from fixed transistor-level netlists; 13 model-harness configurations; 91.1% of attempts produce GDS, 23.0% satisfy the acceptance criteria; an in-house agent (Novara) has the top aggregate score. — [arXiv 2611.16991](https://arxiv.org/abs/2611.16991)
- CASCoDe (9 Nov 2026; Wei, Liu, David Z. Pan): closed-loop agent for analog sizing and layout co-design; 20 SKY130 amplifiers (one-, two-, three-stage); the only flow with a DRC- and LVS-clean routed layout on every circuit; area 59 to 85% smaller (mean 71%) than a conventional place-and-route flow with identical PCells; mean density 0.74 vs 0.39 for APPlace, MAGICAL, ALIGN and ICPI; a baseline-beating layout after about 11 minutes of agent time. — [arXiv 2611.11710](https://arxiv.org/abs/2611.11710)

### Inferences
- The honest analog statement is: "our layout planner outputs finger counts, matched-pair patterns and a floorplan; it stops before GDS, so it has no number on AnalogLayoutWorld or against CASCoDe." The published gap (91.1% GDS vs 23.0% accepted) is a reason to say verification, not generation, is the hard part.
- PCB Gym's 32.1% is on joint placement and routing with a different grader. It is not comparable with PCBWorld clean pass.

### Gaps
- Unverified (search snippet of the PDF only): AnalogLayoutWorld uses SKY130, gdsfactory, KLayout, Magic, ngspice and a three-hour budget per attempt; CASCoDe's baselines were DRC-clean on 15 (APPlace), 0 (MAGICAL) and 1 (ALIGN) of 20 circuits. Open the PDFs before quoting these.
- Unverified (handoff only): that PCB Gym names a planner/worker split as the next step and found no solid link between image viewing and score. The NeurIPS listing shows only the abstract.
- PCB Gym's model list and per-model scores, and OmniRouting's per-model numbers, were not found. Whether either includes Gemini is unknown.
- Seen in search, not read: ICLayout-Bench (arXiv 2611.12982), CADET (arXiv 2611.16913), PCB-Bench (ICLR 2026).

## 3. Repair-from-a-classic-router: evidence and what an agent adds

### Takeaway
I found no published result where an LLM or RL agent post-processes Freerouting output. The nearest evidence (PCBRouteAgent's ablation, and PCBWorld's 1-to-5 rollout gain) says planning plus retries are worth a lot, which makes the idea plausible but unproven.

### Cited Findings
- Best-of-N alone is worth about 5 points for Freerouting on D3-A: 0.75 at @1, 0.80 at @5. For PPO it is 18 points (0.68 to 0.86) and for GPT-5.4 23 points (0.42 to 0.65). — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)
- An LLM planner on top of a fixed geometric executor moved D3-A clean pass from 0.535 to 0.949 and D3-B from 0.000 to 1.000 (PCBRouteAgent "w/o LLM" ablation). The planner decides connection topology, routing order, corridors and layer transitions; failures return as structured feedback and accepted routes are kept. — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2)
- PCBWorld's own agents cannot remove copper, and the paper names this as the reason they cannot escape congestion deadlocks. — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)
- Freerouting knobs that exist: `--router.autorouter.max_passes` (`-mp`, deprecated alias), `--router.via_costs`, `--router.layers.routable`, `--router.layers.preferred_direction_horizontal`, `--router.autorouter.ignore_net_classes`, `--router.neck_width_um`, `--router.optimizer.enabled` (default true), `--router.optimizer.improvement_threshold` (default 2.5), `--router.optimizer.item_selection_strategy` (sequential or prioritized), thread counts. `-us` and `-hr` are removed and do nothing. There is no documented seed flag and no net-ordering flag. A `-drc [json file]` flag writes a DRC report in KiCad's JSON schema. — [Freerouting command-line docs](https://raw.githubusercontent.com/freerouting/freerouting/master/docs/command_line_arguments.md)
- Freerouting can load a DSN plus an existing SES (`-de` accepts files joined with `+`), which is the hook for "keep these routes, redo the rest". — [Freerouting command-line docs](https://raw.githubusercontent.com/freerouting/freerouting/master/docs/command_line_arguments.md)
- PCBWorld's runner calls `java -Xmx4g -jar freerouting-2.1.0.jar -de <dsn> -do <ses> -mp <n>` and can inject a 90-degree snap angle into the DSN. — local file `/home/thinkies/NewGit/gemini-4-hackathon/third_party/pcbworld/methods/baselines/rule_based/run_rule_based_routers.py` (lines 49, 74, 174-177)

### Inferences
- Background, not sourced today: rip-up-and-reroute removes the nets blocking a failed connection, routes the failed one, then re-routes the removed nets, usually with rising costs on contested areas. Freerouting already does this inside its passes, so "run more passes" has diminishing returns.
- What an agent can add beyond re-running with other settings:
  1. Choose which nets to pre-route or protect (the hard net first), then let Freerouting finish. PCBWorld names net order as the main LLM failure cause, and Freerouting has no ordering flag, so this must be done by pre-routing or by ignoring net classes in stages.
  2. Edit the DSN per attempt: layer enables, preferred directions, via cost, snap angle, keep-outs around a violation.
  3. Targeted rip-up: delete the two or three tracks named in the DRC report, re-run with the rest fixed.
  4. Diagnose the four "fully connected, 1 to 4 violations" boards (0021, 0026, 0066, 0088): these may be conversion or rule-mismatch artefacts (track width or clearance from a net class the DSN did not carry) fixable by a width or clearance edit with no rerouting.
  5. Pick the winner among candidates with a vision or Decisions-API screen.
- Likely outcome on D3-A in 90 minutes, my estimate: best-of-N over seeds and three or four settings recovers 2 to 5 boards (85 to 88 of 99); agent repair of the violation-only boards may add 1 to 3. That beats 0.86. Reaching 95 of 99 to beat PCBRouteAgent needs 12 of the 16 failures fixed and is unlikely.
- A plain "best-of-N Freerouting, no agent" row is a required control. If it gets most of the gain, the swarm claim is weak; say so.

### Gaps
- No published Freerouting-plus-agent hybrid found; absence after a short search, not proof.
- Not measured: how much Freerouting's result varies run to run on these boards. One loop of 5 runs on the 16 failures answers this in about 10 minutes and should be the first experiment.

## 4. Practicalities with stock KiCad 10.0.8

### Takeaway
Stock KiCad 10 is good enough for a fast feedback loop, but not for the reported score, and a board saved by KiCad 10 probably cannot be opened by PCBWorld's KiCad 9.0.8 scorer. This machine is also not ready to run Freerouting: Java is 17, the jar needs 21, and no jar or prepared boards are present.

### Cited Findings
- PCBWorld's engine is pinned to KiCad 9.0.8 (kicad-python 0.6.0). Scoring calls the engine (`get_reward_snapshot(run_drc=True)`), not `kicad-cli`, and a matching `.kicad_pro` is mandatory: without one the evaluator raises an error. — [repo README](https://github.com/LGAI-Research/PCBWorld); [eval/README.md](https://raw.githubusercontent.com/LGAI-Research/PCBWorld/main/eval/README.md); local file `third_party/pcbworld/eval/metrics.py` line 319
- The D3 compatibility pass edits only the `.kicad_pro`: it ignores non-routing checks (solder_mask_bridge, drill_out_of_range, malformed_courtyard, courtyards_overlap) and relaxes KiCad 9 floors for annular width, copper-edge clearance and hole clearance. Clearance, shorts, track width, via diameter and net-class rules are kept. — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)
- The scorer reports two counts: errors only (the published one) and errors plus three promoted warnings (`via_dangling`, `track_dangling`, `net_conflict`). A board can be a clean pass with dangling copper. — [eval/README.md](https://raw.githubusercontent.com/LGAI-Research/PCBWorld/main/eval/README.md)
- Scoring an arbitrary routed board: `evaluate_one(routed_pcb, pro_path)` or `python -m eval.pipeline --skip-rollout --output-dir <dir> --stages eval,aggregate`; the pipeline requires file names of the form `<board_id>_<cell>_s<SS>_r<RR>.kicad_pcb` with the `.kicad_pro` alongside. — [eval/README.md](https://raw.githubusercontent.com/LGAI-Research/PCBWorld/main/eval/README.md)
- Local test: `kicad-cli` is 10.0.8. `kicad-cli pcb drc --format json --severity-error -o out.json board.kicad_pcb` runs headless and returns JSON with `violations`, `unconnected_items`, `schematic_parity`, `included_severities`. Flags available: `--all-track-errors`, `--refill-zones`, `--save-board`, `--exit-code-violations`. — local test, `/usr/bin/kicad-cli`
- Local test: `pcbnew` 10.0.8 exposes `ExportSpecctraDSN(board, path)` and `ImportSpecctraSES(board, path)`. DSN export of a PCBWorld fixture board worked headless (returned True). `BOARD.Add`, `BOARD.Remove`, `GetTracks`, `PCB_TRACK`, `PCB_VIA`, `BuildConnectivity` are present, so deleting and adding tracks and vias from Python is possible. — local test, system `python3 -c "import pcbnew"`
- Local test: a KiCad 9 board (file version 20241229) saved by `pcbnew.SaveBoard` in KiCad 10 is rewritten as version 20260206, generator_version "10.0". — local test on `third_party/pcbworld/tests/fixtures/*.kicad_pcb`
- Local test: this machine has OpenJDK 17.0.21. PCBWorld's runner states that the Freerouting 2.1.0 jar is built for Java 21. No Freerouting jar, no `build_rl/`, and no `var/datasets` exist under `third_party/pcbworld`. — local file `third_party/pcbworld/methods/baselines/rule_based/run_rule_based_routers.py` lines 88-89
- PCBWorld ships its own SES-to-board converter, `methods/baselines/rule_based/_converters/ses_to_kicadpcb.py`, and the dataset ships pre-converted `.dsn` files next to each board. — local clone, same directory
- Headless Freerouting: `--gui.enabled=false`; inputs `-de`, output `-do` (`.ses`, `.dsn` or `.scr` by extension). — [Freerouting command-line docs](https://raw.githubusercontent.com/freerouting/freerouting/master/docs/command_line_arguments.md)

### Inferences
- KiCad refuses files written by a newer version, so a `.kicad_pcb` saved through KiCad 10's `pcbnew` will very likely fail to load in the 9.0.8 scorer. Not tested here. Safe routes: (a) never call `SaveBoard` from KiCad 10 on a deliverable; edit the version-9 file as text (append `(segment ...)` and `(via ...)` nodes, delete by uuid), or (b) do all writes with PCBWorld's own converter and engine on the teammate's machine, and use KiCad 10 only on scratch copies for DRC feedback.
- Stock `kicad-cli pcb drc` with the processed `.kicad_pro` copied next to the board under the same stem should match PCBWorld's error-level count closely, because the compatibility changes live in the `.kicad_pro`. Two risks: KiCad 10 has DRC checks that the version-9 project file does not list, and they take their default severity; and KiCad 10 upgrades the project file on load. Calibrate first: run both scorers on the 99 Freerouting outputs and report the agreement rate. That takes minutes and turns "different DRC build" into a number.
- Use `unconnected_items == 0` plus zero error-level `violations` as the local proxy for clean pass. PCBWorld's connectivity test is pad-group based, so dangling copper does not hurt, but a track that only touches a pad edge may be judged differently.
- Use the dataset's own `.dsn` files, not a fresh KiCad 10 export, so the Freerouting input is identical to the baseline's.
- Time cost to make this machine usable: install JDK 21 and fetch the jar (`tools/setup/fetch_baselines.sh`), and get the 16 failed boards plus their `.kicad_pro` and `.dsn` from the teammate. Building the engine here (about 960 build steps) is not worth it in the time left.

### Gaps
- `ImportSpecctraSES` was not tested headless on KiCad 10; older releases had trouble with it outside the GUI. Test on one board before relying on it, or use PCBWorld's converter.
- Whether the 9.0.8 engine truly rejects a version-20260206 file was not tested (no engine build here).
- Agreement between KiCad 10 DRC and the PCBWorld scorer on real D3-A boards is unmeasured.

## 5. A defensible "we beat it" claim, and what must be disclosed

### Takeaway
The claim that survives scrutiny is narrow: "a Gemini swarm that repairs Freerouting output raises D3-A clean pass from 83 of 99 (our Freerouting run) to N of 99, above every method in the PCBWorld paper's headline table, and it is the first Gemini result on this benchmark." Claiming state of the art needs 95 of 99, because PCBRouteAgent already reports 0.949. Verdict: beating 0.86 is plausible in 90 minutes if Freerouting and the scorer are running on the teammate's machine now; beating 0.949 is unlikely; tying D3-B at 1.00 is possible but only 10 boards.

### Cited Findings
- Published D3-A clean pass to compare against: Freerouting 0.80 (@5) / 0.75 (@1); PPO 0.86 (@5) / 0.68 (@1); PPO w/o finish 0.94 (@5) / 0.81 (@1); GPT-5.4 0.65 (@5) / 0.42 (@1). — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)
- PCBRouteAgent with Claude Opus 5.5: 0.949 D3-A, 1.000 D3-B, 0.400 D3-C; with Haiku 5.5: 0.929, 0.800, 0.100; one scored board per instance, with up to 12 replans. — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2)
- No Gemini model appears in either paper. — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915); [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2)
- The published @5 rule selects by potential gain, and time is averaged over all rollouts. — [docs/METRICS.md](https://raw.githubusercontent.com/LGAI-Research/PCBWorld/main/docs/METRICS.md)
- In `configs/datasets/d3.json` the easy tier's train and test lists are the same 99 boards. — local file `/home/thinkies/NewGit/gemini-4-hackathon/pcb/HANDOFF.md` (not re-checked in the config)

### Inferences
Claims in order of strength, with what each needs:
1. "First Gemini result on PCBWorld-Bench." True as of the two papers read; needs only a scored run.
2. "Repair swarm adds +k boards over Freerouting on the same machine, same day, same scorer." The cleanest claim. Needs the best-of-N-without-agent control.
3. "Above every method in PCBWorld's Table 3 headline rows (best 0.86)." Needs 86 of 99. Must mention the 0.94 ablation row and PCBRouteAgent's 0.949 in the same breath.
4. "State of the art on D3-A." Needs 95 of 99. Do not plan the pitch around it.

Must disclose:
- The method is a hybrid: Freerouting does most of the routing; Gemini repairs 16 boards. It is not an LLM routing from scratch, so the comparison with the GPT-5.4 agent (0.65) is not like for like.
- Developed on the reported boards: the 16 repaired boards are in the test list, and D3-A has no separate train split. Stronger: develop on Freerouting failures from the medium or hard pools, then run the 16 once.
- Attempts per board and the selection rule. Published @5 picks by potential gain from five rollouts; the swarm picks by DRC feedback from more than five candidates. State N and the wall-clock and token cost per board (published: Freerouting 7 s, PPO 1.8 s, GPT-5.4 231 s, PCBRouteAgent-Haiku 27 s).
- Scorer: final numbers must come from PCBWorld's own evaluator (KiCad 9.0.8 engine). If any number comes from stock KiCad 10, label it as a proxy and give the measured agreement rate.
- Baseline provenance: 0.84 is a one-seed re-run with repo v1.2.2; the published values are 0.80 (@5, mean of 4 seeds) and 0.75 (@1).
- Variance: one run, no seeds, 99 boards. One board is 1.0 point; a 3-board gain is within the spread between the team's run (0.84) and the published one (0.75 to 0.80).
- The excluded board (`0096_karabas-nano_wifi_revA`) and every board the swarm failed.
- Prior work: cite PCBRouteAgent as the higher published number.

Plan for the 90 minutes, in order:
1. On the teammate's machine (engine and Freerouting already work): rerun Freerouting 5 times on the 16 failures with 2 or 3 settings (`-mp` 10 / 30 / 100, snap-angle patch on and off, via cost). Score each. This is the control row.
2. For still-failing boards, give Gemini the DRC JSON plus a rendered image, and let it choose: delete named tracks and re-route, change a width, or pre-route one net. Keep the file in KiCad 9 format.
3. Score the final folder once with `eval.pipeline --skip-rollout --stages eval,aggregate`.
4. Report three rows: Freerouting single run, Freerouting best-of-N, best-of-N plus Gemini repair.

### Gaps
- Whether hackathon judges will treat "Freerouting plus repair" as beating an LLM-agent baseline is a framing question, not a research one.
- No evidence yet that Gemini 4 can fix any of the 16 boards. The single-Gemini baseline in the handoff was still running.
- For analog layout there is no claim available today; the planner has no verifier-scored output.
