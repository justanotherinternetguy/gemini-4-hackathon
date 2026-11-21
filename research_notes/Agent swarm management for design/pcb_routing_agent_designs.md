# PCB routing agent designs: teardown for a D3-C repair swarm (as of 2026-11-21)

Builds on, and does not repeat, `research_notes/Beating analog and PCB benchmarks/pcb_and_layout_benchmarks.md`
(headline tables, Freerouting flags, @1 vs @5 protocol are there).

Legend: "verified" = read on the primary source or local file today. "unverified" = search snippet only.
Local paths are under `/home/thinkies/NewGit/gemini-4-hackathon/third_party/pcbworld/` (clone, v1.2.2 line).

## 1. PCBRouteAgent (arXiv 2611.08175v2): design, results, D3-C failures

### Takeaway
PCBRouteAgent wins by keeping the LLM out of geometry: scripts build a first plan (MST topology, shortest-net-first order, bounding-box corridors), KiCad's push-and-shove router draws the copper, and the LLM is called only when execution is stuck, to patch corridors, via regions and order. Its stated D3-C weakness is the one a repair swarm can attack: accepted routes are almost never revisited, so an early route (power, in the published case) traps later signals.

### Cited Findings

Board representation (what the planner sees)
- Four images in one mm frame: board layout view, front-copper state, back-copper state, and outstanding connectivity demand; each view has a pixel-to-mm affine transform in a JSON sidecar. — [arXiv HTML 2611.08175v2, Sec. 3.2.1, App. B.1](https://arxiv.org/html/2611.08175v2)
- The JSON sidecar carries pad coordinates and shapes, layers, nets, connectivity clusters (ids like `cluster:24:0`), design rules and a `snapshot_id`. The paper states images give spatial context and text gives exact ids and numbers. No congestion map is described. — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2)
- In code, the sidecar is trimmed to the "work set" of nets: full records for work-set nets and pads, a summary of at most 40 other nets, plus all obstacle geometry (other pads, tracks, vias), footprints with courtyard boxes, keepouts and rules. — [route_agent/prompts/bundle.py](https://github.com/chip-ai-agent/PCBRouteAgent/blob/HEAD/route_agent/prompts/bundle.py)
- The planner prompt tells the model to "use the images to avoid dense pad fields and existing copper" and "Do not draw copper." — [route_agent/prompts/bundle.py](https://github.com/chip-ai-agent/PCBRouteAgent/blob/HEAD/route_agent/prompts/bundle.py)

What a plan is
- Topology: each net's disconnected clusters form a weighted graph (min pad-to-pad distance plus a penalty for incompatible layers); Prim's MST gives K-1 connection tasks. Nets are ordered by estimated tree length, shortest first. — [arXiv HTML 2611.08175v2, Sec. 3.2.2-3.2.3](https://arxiv.org/html/2611.08175v2)
- Corridors: per edge, ordered stages. Same-layer edge = one axis-aligned box around the endpoints expanded by 1.5 mm. Layer-change edge = split at the midpoint into two layer-specific corridors with a via region at the midpoint. Corridors are soft. — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2)
- Plan JSON fields: `connection_trees`, `corridors` (id, layer, bbox in mm), ordered `stages` per edge, `via_regions`, `net_order`, soft `reservations`, `profile_ref`, `based_on.snapshot_id`. A validator enforces: consecutive same-layer stages overlap; a layer change needs a via region covering the overlap; first and last stage contain an anchor of the source and target cluster. — [route_agent/prompts/bundle.py](https://github.com/chip-ai-agent/PCBRouteAgent/blob/HEAD/route_agent/prompts/bundle.py)
- Layer changes belong to the planner only: "the Detailed router never places a via and never leaves the layer of the stage it is in." The prompt names the fenced-pad case (dense SMD row) and prescribes a roughly 1 mm via region next to the pad. — [route_agent/prompts/bundle.py](https://github.com/chip-ai-agent/PCBRouteAgent/blob/HEAD/route_agent/prompts/bundle.py)
- Scripts build the initial guide; the multimodal model revises it only when execution gets stuck. — [project page](https://chip-ai-agent.github.io/research/pcbrouteagent/)
- Replans are patches, not rewrites: `GlobalGuidePatch` with `replace_corridors`, `replace_via_regions`, `replace_edges`, and a `preserve` list of every untouched edge. — [route_agent/prompts/bundle.py](https://github.com/chip-ai-agent/PCBRouteAgent/blob/HEAD/route_agent/prompts/bundle.py)

Executor and recovery
- Executor: KiCad's native push-and-shove (PNS) router; waypoints at the centres of overlapping corridor regions; success = source and target clusters verified connected. — [arXiv HTML 2611.08175v2, Sec. 3.3](https://arxiv.org/html/2611.08175v2)
- Local recovery order: walkaround and shove; alternative waypoints; one grid A* detour on the same layer (executed through PNS); backtrack to earlier stages; alternative source anchors. Limits per connection: two stage backtracks, one A* detour, two alternative anchors, at most one rip-up per obstructing net. — [arXiv HTML 2611.08175v2, App. B.2](https://arxiv.org/html/2611.08175v2)
- Replan limits: 12 replans per board, four per connection, five batch-replanning rounds. Failed tasks roll back to a checkpoint; accepted routes are kept. — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2)
- Code defaults match: `ripup_cap=1`, `replans_per_edge=4`, `batch_rounds=5`, `backtrack=2`, `alt_anchors=2`, `--max-global-rounds 12` ("the measured D3-B configuration"). Rip-up is limited to work-set nets (`rippable_nets=set(snap.work_set)`). — [route_agent/cli/pipeline.py](https://github.com/chip-ai-agent/PCBRouteAgent/blob/HEAD/route_agent/cli/pipeline.py)
- Feedback packet to the planner: failed attempts, last head position, blocking obstacles, affected regions, deviation from the guide. In code the evidence fields are `via_sites` (clearance-checked points next to the stuck head where a via fits on both layers), `blocker_candidates` (foreign pads and tracks fencing the head), `head_is_thru`, `alt_starts`, and a per-edge `history` of shapes that already failed, with the instruction "do not return one of them or a near copy." — [route_agent/prompts/bundle.py](https://github.com/chip-ai-agent/PCBRouteAgent/blob/HEAD/route_agent/prompts/bundle.py)
- End-of-pass batch replan: all failed edges go to the planner in one call, which returns one patch. — [route_agent/prompts/bundle.py](https://github.com/chip-ai-agent/PCBRouteAgent/blob/HEAD/route_agent/prompts/bundle.py)
- Angle repair (Opus runs): up to five guarded passes; a change is kept only if connectivity, DRC and wirelength do not worsen. The code re-routes the two segments around a non-45-degree joint as one PNS move. Measured before repair: 8.8 flagged joints per board on D3-B and 20.9 on D3-C. Cause: PNS keeps corners at 45 degrees inside one move, but the joint between two hops is unconstrained. — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2); [route_agent/exec/angles.py](https://github.com/chip-ai-agent/PCBRouteAgent/blob/HEAD/route_agent/exec/angles.py)
- Opus token settings: 32,000 output tokens at medium effort on D2/D3-A, 32,000 on D3-B, 64,000 at low effort on D3-C. — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2)

Results (clean pass / routability / error DRV per board / wirelength mm / vias), official PCBWorld evaluator, one scored board per instance — [arXiv HTML 2611.08175v2, Sec. 5](https://arxiv.org/html/2611.08175v2)

| D3-C (10 boards) | CP | Rout. | DRV | WL | Via |
|---|---|---|---|---|---|
| Reference (human) | 0.900 | 0.938 | 8.80 | 1391.0 | 44.00 |
| Freerouting | 0.300 | 0.918 | 32.00 | 1304.0 | 21.40 |
| KiCadRoutingTools | 0.000 | 0.778 | 30.50 | 1086.0 | 57.40 |
| PCBWorld PPO | 0.000 | 0.403 | 72.70 | 1075.0 | 5.00 |
| PCBWorld agent (Haiku 5.5) | 0.000 | 0.199 | 94.60 | 330.9 | 4.30 |
| PCBRouteAgent, no LLM | 0.000 | 0.831 | 18.70 | 1124.9 | 5.90 |
| PCBRouteAgent (Haiku 5.5) | 0.100 | 0.915 | 9.80 | 1352.4 | 17.30 |
| PCBRouteAgent (Opus 5.5) | 0.400 | 0.949 | 6.30 | 1410.5 | 21.30 |

- D3-A: Opus 0.949 CP, rout. 0.994, DRV 0.09; Haiku 0.929; no-LLM 0.535. D3-B: Opus 1.000, Haiku 0.800, no-LLM 0.000 (rout. 0.891). — [arXiv HTML 2611.08175v2, Table 5](https://arxiv.org/html/2611.08175v2)
- The LLM ablation has no rule-based replanner control and no "general agent with the same tools" control; the authors say so. The project page says controlled component ablations "remain important next steps." No image-versus-text ablation exists. — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2); [project page](https://chip-ai-agent.github.io/research/pcbrouteagent/)
- D3-B numbers include retries of unscored model-response failures and angle repair, so they are not a strict @1. D3-C rows and the Haiku PCBWorld rows are the authors' local runs. — [project page](https://chip-ai-agent.github.io/research/pcbrouteagent/)

Cost and time (Haiku 5.5, per-board mean) — [arXiv HTML 2611.08175v2, App. C](https://arxiv.org/html/2611.08175v2)

| Split | Time s | LLM calls | Input k tok | Output k tok | Cost $ |
|---|---|---|---|---|---|
| D2 | 0.8 | 0.05 | 1.6 | 0.1 | 0.0002 |
| D3-A | 27.0 | 0.87 | 19.9 | 5.8 | 0.0048 |
| D3-B | 146.0 | 2.90 | 163.0 | 30.0 | 0.0330 |
| D3-C | 516.0 | 5.10 | 666.0 | 62.0 | 0.3930 |

- Against the PCBWorld step agent on the same model: 172 vs 22,529 calls over 247 boards (about 131x fewer), $4.77 vs $20.72, 2.6 h vs 36.4 h cumulative. On D3-C the step agent used 676.6 calls and 4,566 s per board for 0.000 CP. — [arXiv HTML 2611.08175v2, Table 8](https://arxiv.org/html/2611.08175v2)
- Tokens per connected edge on D3-C: 6,744 (PCBRouteAgent) vs 269,324 (PCBWorld agent). — [arXiv HTML 2611.08175v2, Table 7](https://arxiv.org/html/2611.08175v2)

D3-C failure analysis
- With Opus, 4 of 10 pass; "Six of the ten boards remain incomplete." The failures are incomplete connectivity, not rule violations: mean routability 0.949, and "even a small number of remaining connections can prevent a nearly routed board from passing." — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2); [project page](https://chip-ai-agent.github.io/research/pcbrouteagent/)
- Published case (Opus, unnamed D3-C board): 70 of 83 connections. The saved order "routes sensor power before these signals, without reserving pad access or space for layer-changing vias." SCL and SDA stall near crowded controller pads; layer changes on retry get them out, but their approach to the sensor hits the existing power route; "repair removes unfinished attempts but keeps that route." Authors' proposed fix: reserve pad access, prioritise connections with few alternatives, revisit earlier routes. — [arXiv HTML 2611.08175v2, App. D.2](https://arxiv.org/html/2611.08175v2)
- Published case (Haiku): 84 of 86 targets connected; two open connections fail the board. — [project page](https://chip-ai-agent.github.io/research/pcbrouteagent/)
- Stated limitation: "Local recovery cannot revisit accepted routes, so earlier allocations can trap later connections." — [project page](https://chip-ai-agent.github.io/research/pcbrouteagent/)
- The ten D3-C boards: `0400_laptimer58_Chickadee`, `0423_induction-hob_temperature-sender`, `0448_uedaino_uedaino`, `0473_data-manager_data-manager`, `0496_kitspace_f-91w`, `0520_RGBMatrixPanelCPLD-PhotonBackpack_RGBMatrixPanel_CPLD_negative`, `0546_ozinverter_ozinverterkicad`, `0571_Brushless_ESC_Brushless_ESC`, `0599_RGBMatrixPanelCPLD-PhotonBackpack_RGBMatrixPanel_CPLD`, `0626_TOBS_HybridChargeController`. — [configs/board_lists/d3c_test.txt](https://github.com/chip-ai-agent/PCBRouteAgent/blob/HEAD/configs/board_lists/d3c_test.txt)
- The human reference itself scores 0.900 CP on D3-C with mean routability 0.938 and 8.80 DRV, so one reference board fails the scorer. — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2)
- Other costs of the design: wirelength 8.2% above Freerouting on D3-C and 10.9% on D3-B; differential pairs are not modelled (USB D+ and D- routed apart, 34.03 and 36.49 mm vs 30.25 mm each in the reference). — [project page](https://chip-ai-agent.github.io/research/pcbrouteagent/)

### Inferences
- The D3-C problem is "last 5% of connections", not DRC. Freerouting on D3-C has the mirror profile: routability 0.918 but 32 DRV per board. A repair swarm starting from Freerouting output must fix both opens and violations; PCBRouteAgent's own output has few violations (6.3) and mostly opens.
- The Haiku-to-Opus jump on D3-C (0.1 to 0.4) with the same executor says planner quality matters most on the hard tier; on D3-A it is worth only 2 points. Spend the strong model on D3-C planning, not on D3-A.
- The 0.9 human ceiling means at most 9 of 10 unless a method beats the reference on that board. Beating 0.400 needs 5 of 10 clean.
- LLM calls are few (5.1 per D3-C board with Haiku) but large (about 130k input tokens per call): the cost is context, not call count. The trimmed work-set sidecar is the mechanism that keeps it bounded.
- Design choices to copy: scripted first plan; LLM only on failure; patch-with-preserve replans; structured failure evidence with pre-computed legal via sites; failed-shape history to stop repeats; checkpoint and rollback; guarded post-repair that is kept only if nothing worsens.
- Design choices to avoid: rip-up capped at one per obstructing net and never touching accepted routes outside the current work set; no pad-access or via-site reservation before routing power; no congestion summary handed to the planner.

### Gaps
- The paper does not name which six D3-C boards fail under Opus, nor give per-board causes beyond the one case. No per-board table is published in the paper, project page or README. Running `scripts/summarize.py` on a run directory would show it, but no run outputs are in the repo.
- No Gemini result. No image ablation. No Opus cost table (only Haiku).
- `route_agent/loop/budget.py` has `replan_cap = 3` as a dataclass default while the paper and CLI use 12 global rounds; I did not trace which value applies when. Treat the paper's 12 as the measured setting.
- Whether angle repair changed any clean-pass outcome is not stated (see section 2: the local scorer does not gate clean pass on angles).

## 2. PCBWorld (arXiv 2607.05915): what its agents see, how they fail, what the scorer counts

### Takeaway
Clean pass in the local scorer is "fully connected AND zero error-severity DRC violations", nothing else. Track-angle violations and the three promoted warnings are counted in other columns and do not block a clean pass. PCBWorld's own LLM agents see text only, draw one segment per call, and cannot delete copper, which is why they fail on congestion.

### Cited Findings

Scorer
- `clean_pass = bool(success and len(drv_errors_only) == 0)`. — local `eval/metrics.py` line 465 (same file upstream: [eval/metrics.py](https://github.com/LGAI-Research/PCBWorld/blob/main/eval/metrics.py))
- `clean_pass_from` docstring: "clean_pass = success AND zero errors-only DRVs (promoted excluded)." — local `eval/eval_utils.py` line 428 ([eval/eval_utils.py](https://github.com/LGAI-Research/PCBWorld/blob/main/eval/eval_utils.py))
- Promoted warnings are three KiCad warnings counted as violations by the training reward only: code 12 `via_dangling`, code 13 `track_dangling`, code 37 `net_conflict`. "A board can score a clean pass while still carrying promoted warnings." — [docs/METRICS.md](https://raw.githubusercontent.com/LGAI-Research/PCBWorld/main/docs/METRICS.md); [eval/README.md](https://raw.githubusercontent.com/LGAI-Research/PCBWorld/main/eval/README.md)
- Track angle: a Python heuristic, not KiCad DRC. It walks every same-net, same-layer joint of exactly two tracks and flags angles not within 0.5 degrees of 135 or 180 (45 mode) or 90 or 180 (90 mode). The result goes to `track_angle_drv` and is added to `total_drv_count` (promoted count + angle count). It is not part of `drv_errors_only` and so not part of clean pass. — local `eval/metrics.py` lines 88-106, 435-465
- The code comment says KiCad's C++ `track_angle` DRC provider is not compiled into the engine library, so a `.kicad_dru` track-angle rule "silently no-ops". The paper's DRC table lists `TRACK_ANGLE` (code 18) as error-level, but with the provider absent it cannot fire. — local `eval/metrics.py` lines 98-102; local `PCBWorld.pdf` Appendix F table (text line 1164 of the extracted PDF)
- `check_angle` defaults to 45 and otherwise follows the env's `corner_mode`. — local `configs/rl.yaml` line 220; [eval/README.md](https://raw.githubusercontent.com/LGAI-Research/PCBWorld/main/eval/README.md)
- Stage 2 re-opens each saved `.kicad_pcb` and checks it against its own `.kicad_pro` rules; the `.pro` is mandatory or the scorer raises. "Anything that produces a routed `.kicad_pcb` can be dropped into stage 1." — [docs/METRICS.md](https://raw.githubusercontent.com/LGAI-Research/PCBWorld/main/docs/METRICS.md); [eval/README.md](https://raw.githubusercontent.com/LGAI-Research/PCBWorld/main/eval/README.md)
- `success` = every net's pads form one connectivity group; dangling copper holds no pad and cannot enter routability. — [eval/README.md](https://raw.githubusercontent.com/LGAI-Research/PCBWorld/main/eval/README.md)
- The per-violation records carry `error_type`, `x_mm`, `y_mm`, `layer`, `net_names`, `is_error`, `is_promoted`; `extras.per_net` gives per-net unrouted edges. — [eval/README.md](https://raw.githubusercontent.com/LGAI-Research/PCBWorld/main/eval/README.md)

What the agents see and do
- State is a nested dict of exact mm geometry: "No grid, no rendered image." The text wrapper renders it as S-expressions: `board_static` (bbox, layers, nets with pads and per-net constraints, obstacles), `routing_geometry`, `drc_violations` near the head, `router_head`, `closed_nets`. — [docs/ENVIRONMENT.md](https://github.com/LGAI-Research/PCBWorld/blob/main/docs/ENVIRONMENT.md)
- Six actions plus `idle`; `routing_mode` 0 = mark obstacles, 1 = shove, 2 = walk around. The LLM wrapper takes free coordinates by default; candidates mode restricts to pad centres, track ends, ratsnest targets and eight neighbours of the head. — [docs/ENVIRONMENT.md](https://github.com/LGAI-Research/PCBWorld/blob/main/docs/ENVIRONMENT.md)
- Prompt hints (v7 guidelines): detour by via with at least 1 mm offset from any pad, "never place a via on a pad"; if the detour is long, use push-and-shove; a "pad trap" warning about arriving at a pad on the wrong layer. — local `methods/llm_agent/wrappers/prompts/glossary.yaml` lines 204-229
- Engagement mode on D3-A, GPT-5.4: interactive 0.65 CP / 1.31 DRV; plan-and-execute 0.19 / 16.28; engine-free 0.02 / 34.58. — [arXiv HTML 2607.05915, Table 4](https://arxiv.org/html/2607.05915)

Failure modes the paper reports
- Net order: "routing-target ordering is a primary factor governing downstream routing success"; the agent "rarely performs corrective recovery behaviors after an unfavorable decision" (case `0018_hy_adapter`). — local `PCBWorld.pdf`, Appendix N (extracted text lines 2449-2455); [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)
- No route editing: track-removal APIs (`delete_track_*`, `delete_via_*`) exist in the engine but are excluded from the agent's action space; the agent "has limited capability to resolve routing deadlocks after they emerge" and "tends to repeatedly attempt rerouting through alternative paths" (case `0100_smt-zvs-driver_IH10-mc`). — local `PCBWorld.pdf`, Appendix N (lines 2456-2466)
- DRC-level failure map (Table 11): via on pad (annular width, hole clearance); wrong-layer endpoint (dangling track); cross-net contact (shorting items, clearance); stranded segment (dangling track, dangling via); detour over board edge (edge clearance); invalid coordinate (parse fail or mask reject); repeated identical attempt (rejection streak). — local `PCBWorld.pdf`, Table 11 (lines 1280-1291)
- Any parse failure or masked action becomes `idle`. — local `PCBWorld.pdf`, Table 12 (lines 1353-1362)
- All four LLMs score 0.00 CP on D3-B; D3-C is "reserved ... as a challenge split" and not evaluated in the paper. — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)

### Inferences
- A Freerouting board that is fully connected with non-45-degree joints still passes, as long as no error-severity rule fires. Angle repair is optional polish for clean pass. It is not optional if the team quotes `total_drv_count`, and risky: every re-route can add a real error.
- A repair agent should delete dangling stubs it leaves (promoted codes 12, 13) for hygiene, but they do not cost clean pass. Shorts and clearance do.
- The engine exposes `delete_track_*` and `delete_via_*`. A repair swarm can use exactly the capability the paper's agents lacked. This is the largest structural advantage available.
- The scorer output is already a repair worklist: violation type, x, y, layer, nets, plus per-net unrouted edges. No vision is needed to find the problem; vision is at most for judging the neighbourhood.
- Plan-and-execute without engine feedback (0.19) is far worse than interactive (0.65). A planner must not emit coordinates that are committed blind.

### Gaps
- I did not trace whether `total_drv_count` (with angle count) feeds the potential used for @5 selection. If it does, angles affect which rollout is picked, though not clean pass itself. Check `pcb_world/core/reward.py` before relying on this.
- The PCBRouteAgent paper performs angle repair although clean pass does not need it under this scorer; the reason is not stated. Possibly their PCBWorld commit differs. Unverified.
- Appendix N has only two case studies; no frequency breakdown of failure causes.

## 3. Classic rip-up-and-reroute, negotiated congestion, and Freerouting on dense boards

### Takeaway
Sequential routers fail on the last few nets because earlier nets took the scarce resource (pad escape, a channel, a via site), and their fixes are high-level choices: order, which blocker to remove, layer, and where to reserve space. All of these can be made by an LLM while a router draws. Sourcing for this question is thin; most of it is background knowledge and is marked so.

### Cited Findings
- Rip-up and reroute is "the most common technique" for detailed routing: pick a net order, route in sequence, and on failure remove selected routes and retry in a changed order; "This process repeats until all nets are routed or the program (or user) gives up." If routing stays incomplete, "a typical fix is to change the placement to allow more room." — [Wikipedia, Routing (EDA)](https://en.wikipedia.org/wiki/Routing_(electronic_design_automation))
- Push-and-shove: the tool "pushes other nets out of the way, if possible." — [Wikipedia, Routing (EDA)](https://en.wikipedia.org/wiki/Routing_(electronic_design_automation))
- Freerouting's `-mp` sets the maximum number of full rip-up-and-reroute passes; OrthoRoute's `max_iterations` counts PathFinder negotiation rounds. — local `PCBWorld.pdf` Appendix (lines 1813-1814)
- Freerouting has no net-ordering flag and no documented seed flag; it can load a DSN plus an existing SES, and has via-cost, routable-layer and preferred-direction settings. — [Freerouting command-line docs](https://raw.githubusercontent.com/freerouting/freerouting/master/docs/command_line_arguments.md) (details in the prior notes file)
- Freerouting on D3-C: routability 0.918, 32.00 error DRV per board, 21.4 vias against 44.0 in the human reference. On D3-B: routability 0.990 but 11.94 DRV. — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2)
- PathFinder-style negotiation alone does badly here: OrthoRoute scores 0.000 CP, routability 0.063, 139.9 DRV on D3-C. — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2)
- PCBWorld's related work names the hybrid directly: an agent that "selects high-level options for a fixed auto-router", and RL for net ordering in detailed routing. — local `PCBWorld.pdf` (lines 44, 495, 675)
- Unverified (search snippets): a DATE 2023 paper reports Freerouting and DeepPCB both fail on two of ten real double-layer designs; Altium guidance says to route or fan out power nets first; forum advice for the last few unroutes is to route them by hand. — [search result, DATE 2023 PDF](https://past.date-conference.com/proceedings-archive/2023/DATA/603.pdf); [Altium Situs docs](https://www.altium.com/documentation/altium-designer/topological-autorouting-ad?version=18.1)

### Inferences
- Background, not sourced today: PathFinder lets nets share resources illegally at first, then raises the cost of each shared resource every iteration (present-sharing and history terms) until nets with alternatives move away and the net with no alternative keeps the resource. The lesson is the priority rule: the net with the fewest alternatives should win the contested spot. PCBRouteAgent's authors reach the same rule from their D3-C failure ("prioritize connections with few alternatives").
- Freerouting uses about half the vias of the human reference on D3-C (21.4 vs 44.0). Under-use of layer changes is a plausible cause of its opens and of clearance violations in crowded channels. A planner that forces a via region near a fenced pad, as PCBRouteAgent's prompt does, addresses this directly.
- Interventions, and who should decide them:

| Intervention | Decision an LLM can make | Who draws |
|---|---|---|
| Net order / pre-route critical nets | pick the 3 to 5 nets with fewest escape options to route first or protect | router |
| Rip-up of blockers | choose which committed net(s) to delete from the blocker list around a failed edge; deeper than one level | engine delete + router |
| Layer change | choose a via site from pre-checked candidates; choose layer per corridor stage | router |
| Walk-around vs shove | pick routing mode per attempt; shove risks new violations on the shoved net | PNS |
| Fanout / escape | reserve a via site or stub per pad of a dense part before any long route | router |
| Keep-out / reservation | place a temporary keep-out so power does not cross a signal escape | router |
| Rule or width fix | decide whether a violation is a net-class mismatch (edit width) or a real collision (reroute) | script |
| Violation-only repair | for a connected board, delete the 1 to 3 offending segments and reroute only those | router |

- All rows are discrete choices over ids and pre-computed candidates. None requires the model to invent a coordinate. That matches the 0.19 vs 0.65 evidence against blind coordinates.

### Gaps
- No primary PathFinder source or Freerouting issue-tracker thread was read today; the search returned no documented fix for "last connections unrouted". The mechanism above is textbook knowledge, not a cited finding.
- No published study of why Freerouting specifically leaves opens on D3-C boards. The team's own per-board Freerouting DRC reports are the only source; classify each D3-C board as opens-only, violations-only, or both before designing roles.
- Whether Freerouting's D3-C violations are real collisions or DSN-conversion and net-class artefacts is unknown. This decides whether repair is mostly cheap rule edits or real rerouting.

## 4. Other 2025-2026 agent systems: the coordination or perception trick, and image evidence

### Takeaway
The repeated winning pattern is a model that picks regions or options from a constrained, discrete action space while a conventional tool does geometry and a hard verifier judges. The only modality ablation found (VeoPlace) shows images and text are interchangeable to within 0.8%; nothing shows images are necessary.

### Cited Findings
- VeoPlace (arXiv 2603.28733, NeurIPS 2026): a Gemini VLM sees a canvas image with macros coloured by connectivity cluster plus text (grid size, macro sizes, prior placements with scores) and outputs one rectangle per macro. The placer uses the rectangle as a soft constraint: a quadratic anchor in DREAMPlace (weight 0.01), or a logit mask in ChiPFormer. — [arXiv HTML 2603.28733](https://arxiv.org/html/2603.28733)
- VeoPlace loop: each iteration runs 8 unguided rollouts, one VLM call, 8 guided rollouts; 2,000 rollouts = 125 VLM calls. Context is chosen by "Top Stratified" sampling; "Best"-only selection performs worst (local minima). — [arXiv HTML 2603.28733](https://arxiv.org/html/2603.28733)
- VeoPlace results: beats the best learning-based method on 9 of 10 benchmarks; improves DREAMPlace on all eight ICCAD 2015 Superblue designs by up to 4.3%. Models: Gemini 3.6 Flash (62 s per call), Gemini 2.5 Flash for ablations; small open Qwen3-VL models perform about the same. About $7 per 2,000-rollout run. — [arXiv HTML 2603.28733](https://arxiv.org/html/2603.28733)
- VeoPlace modality ablation: removing the image, removing the text, or shuffling images all still beat DREAMPlace on all eight designs and differ from each other by under 0.8%. Random regions and anchoring to best-so-far were both worse than VLM regions. — [arXiv HTML 2603.28733](https://arxiv.org/html/2603.28733)
- About 20% of VeoPlace suggestions have no valid in-region cell and fall back to unconstrained sampling. — [arXiv HTML 2603.28733](https://arxiv.org/html/2603.28733)
- CASCoDe (arXiv 2611.11710): hierarchical discrete action space with four decision types (sizing selection, structural floorplan, local layout refinement, routing-attempt selection) and feedback-driven tree search; each result judged by DRC, LVS and extracted-RC simulation. Only flow clean on all 20 SKY130 amplifiers; area 59 to 85% smaller; first baseline-beating layout after about 11 minutes. "Ablations indicate that both the structured action space and the feedback-guided tree search reduce area and search cost." — [arXiv 2611.11710](https://arxiv.org/abs/2611.11710)
- PCB Gym (NeurIPS 2026): frontier LLMs get DRC-valid, fully routed boards on 32.1% of attempts and "often route power rails like ordinary signals." — [NeurIPS 2026 listing](https://neurips.cc/virtual/2026/161602)
- PCB-Bench (ICLR 2026) reports substantial gaps in models' ability to reason over spatial placements, including in multimodal image-text tasks (abstract-level, unverified detail). — [ICLR 2026 poster](https://iclr.cc/virtual/2026/poster/10009621)
- PCBWorld's own agents are text-only and reach 0.65 CP on D3-A at @5; PCBRouteAgent adds images and a planner and reaches 0.949, but changes many things at once, so the gain cannot be attributed to images. — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915); [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2)

### Inferences
- Common trick across VeoPlace, PCBRouteAgent and CASCoDe: the model outputs soft regions or picks among options; a deterministic tool realises them; the verifier's output is the only reward. Fallback to the unguided tool is always available.
- Image evidence, summarised: no system shows a measured gain from images over structured geometry. VeoPlace says either channel suffices; PCBRouteAgent has no ablation; PCBWorld works text-only on small boards. The safe design gives the planner structured geometry as the source of truth and treats images as an optional second channel, which must be A/B tested before it is claimed as useful.
- VeoPlace's context finding transfers: when showing a planner previous attempts, show a diverse set of good ones, not only the single best.
- PCB Gym's power-rail finding and PCBRouteAgent's "sensor power routed first" failure are the same defect from two sources: power nets need different treatment (wider, fan out, but must not consume signal escapes).
- CASCoDe supports tree search over discrete repair options with checkpoints, rather than one linear repair attempt.

### Gaps
- PCB Gym: could not confirm any planner/worker recommendation or any image-viewing result; only the abstract is public on the listing. The handoff's claim stays unverified.
- OmniRouting (arXiv 2608.04434) and AnalogLayoutWorld (arXiv 2611.16991) leader designs were not read today; no coordination details extracted. LLM-guided FPGA/ASIC routing: not searched for lack of time.
- VeoPlace Table 18 per-variant numbers not captured. CASCoDe full text not read (abstract only).

## 5. Division of labour for a Gemini 4 swarm repairing Freerouting output on D3-C

### Takeaway
Everything in this section is inference from the findings above; no source tests a swarm on this task. The evidence favours a small hierarchy: a strong planner that issues discrete repair decisions per board, deterministic executors on the KiCad engine with checkpoints, and the official scorer as the only judge. Parallelism should come from independent candidate repairs per board (best-of-N), not from many agents editing one board.

### Cited Findings
- Planner quality is the lever on the hard tier: same executor, Haiku 0.100 vs Opus 0.400 on D3-C. — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2)
- The scripted executor without any LLM already reaches routability 0.831 on D3-C, and Freerouting 0.918; the LLM adds the last connections. — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2)
- Step-level LLM control is the expensive failure: 676.6 calls and 4,566 s per D3-C board for 0.000 CP. — [arXiv HTML 2611.08175v2](https://arxiv.org/html/2611.08175v2)
- Best-of-N by the scorer is worth 5 to 23 points on D3-A depending on method variance. — [arXiv HTML 2607.05915](https://arxiv.org/html/2607.05915)
- Unrevisited accepted routes are the named cause of D3-C failures. — [project page](https://chip-ai-agent.github.io/research/pcbrouteagent/)
- Clean pass counts only error-severity violations and full connectivity. — local `eval/metrics.py` line 465

### Inferences

Recommended roles
1. Triage (script, no model). Run the official scorer on each Freerouting board. Emit a worklist per board: open edges per net, each error violation with type, position, layer and nets, and for each item the blocker list and legal via sites (compute as PCBRouteAgent does). Classify the board: clean, violations-only, opens-only, both.
2. Strong slow planner (one call per board per round, Gemini 4 top tier). Input: the worklist, trimmed structured geometry for the work-set nets and their neighbours, a coarse occupancy summary per layer, and the history of failed repairs. Output: an ordered list of discrete repair ops with ids only: `rip(net or segment ids)`, `reroute(edge, layer plan, via_site id, mode)`, `reserve(pad, via_site)`, `protect(net)`, `rerun_freerouting(fixed=..., settings=...)`. Use patch-with-preserve, never a full rewrite.
3. Executors (deterministic code; a fast model only for local choices). Apply one op under a checkpoint through the PCBWorld engine (`delete_track_*`, PNS route with walkaround first, then shove), or re-run Freerouting with protected routes. Roll back if scoped error count rises or connectivity drops. Return a structured feedback packet.
4. Yes/no decision model. Use for gates with a crisp question and structured evidence: "is this violation a net-class width mismatch (edit) or a collision (reroute)?", "accept this candidate given before and after counts?" Prefer a script when the scorer already answers it; never let the model override the scorer.
5. Fast workers in parallel. Fan out K different repair strategies per board on separate board copies (different rip sets, via sites, order). Pick by the scorer: clean pass first, then fewest error DRVs, then routability. This is where swarm width pays, and it has a clean control (K independent Freerouting re-runs).
6. Vision reviewer. Optional and off the critical path. Plausible uses: sanity review of the winning board, and as a second planner input in an A/B arm. No evidence it adds over structured geometry; do not make acceptance depend on it.
7. Hosted long-horizon agents. Fit: one agent per D3-C board owning the whole plan, execute, score loop for minutes (PCBRouteAgent took 516 s per D3-C board on Haiku). Ten boards means ten agents in parallel. Do not use them for per-segment work.

Priorities specific to D3-C
- Deeper rip-up than PCBRouteAgent: allow removing an accepted route that the blocker list names, including power, and re-route it after the trapped signal. This is the single most direct attack on the published failure.
- Fewest-alternatives-first ordering and pad-escape reservation near dense parts before re-routing power.
- Force layer changes where a pad is fenced; Freerouting under-uses vias relative to the reference (21.4 vs 44.0).
- Violations-only boards first: they need the least work per clean pass gained.
- Report against the 0.9 reference ceiling, with the @1 Freerouting 0.300 and PCBRouteAgent 0.400 as comparators, and a no-LLM best-of-N control.

What would be a mistake to build
- An LLM that emits track coordinates or one segment per call (0.19 CP plan-and-execute, 0.00 on D3-B and D3-C for step agents, 131x more calls).
- Many agents editing the same board concurrently. Routing resources are shared; one agent's shove invalidates another's plan. No prior system does this; all serialise commits behind checkpoints.
- A debate or voting layer over repairs. The scorer is exact and cheap; votes add cost and no information.
- Acceptance by a vision model or by the planner's own judgement instead of the official scorer.
- Angle repair as a core stage. It does not affect clean pass under the local scorer and each re-route risks a real error. If used, guard it as PCBRouteAgent does.
- Full replans that discard clean routes. Use patches with an explicit preserve list.
- Uncapped retries on one edge. Carry failed-shape history and stop after a fixed budget; a 95%-routed board that cannot be finished should fall back to the best scored candidate.
- Spending the strong model on D3-A or D2, where the planner's marginal value is small.
- Claiming images help without an image-off arm.

### Gaps
- No measurement exists of LLM repair on Freerouting output for D3-C; expected gain is unknown. First experiment: score Freerouting on the ten boards, classify failures, and run K=5 plain re-runs as the control.
- Gemini 4 model names, latency, context limits and prices were not researched here; role sizing above is by analogy with the Haiku and Opus numbers in PCBRouteAgent.
- Whether the PCBWorld engine build on this machine exposes delete and checkpoint calls to an external script was not tested (build in progress; code not run).
- D3-C boards reach 451 nets and 2,103 pads; whether a trimmed work-set sidecar fits the planner's context on the largest of the ten boards is untested. PCBRouteAgent averaged about 130k input tokens per call on D3-C.
