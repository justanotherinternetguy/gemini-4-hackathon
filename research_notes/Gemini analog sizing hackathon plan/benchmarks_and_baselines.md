# Held-out evaluation sets, baselines and prior LLM-agent results for analog circuit sizing (as of 2026-11-21)

Scope note: research done 2026-11-21. GitHub facts (licenses, file lists, code lines) were checked directly with `gh api`. Paper numbers come from arXiv HTML/PDF fetches unless a line says otherwise. The third-party AutoSizer audit (taha-mazher) is an unreviewed work-in-progress repo with 0 stars. I checked one of its claims myself (the spec-regex bug, see Q3). Its other numbers are not independently verified.

## Q1. SGAnalog: availability, format, which sizing tasks run on SKY130 + ngspice, scoring

### Takeaway
SGAnalog is public under Apache-2.0 at github.com/Li-Yueting/SGAnalog. All tasks are ngspice 45.2 + sky130A inside a pinned IIC-OSIC-TOOLS 2026.02 Docker container. It is a weak primary held-out set for this project. Its 17 sizing tasks are tiny (2-14 devices), only 3 are op-amps, there are no corners and no explicit specs: the score is closeness to the human design's own metric. The human sizes are public, and 7 tasks only check that the circuit runs. It is still useful in two ways: as a small "real human designs we never saw" side check, and because the paper already publishes a single-LLM-call baseline for Gemini (91.2/100).

### Cited Findings
- **Paper:** arXiv 2610.03934, "SGAnalog: An End-to-End Circuit Benchmark from Open-Source Silicon Tapeouts", accepted at a NeurIPS 2026 workshop. — [arXiv abs](https://arxiv.org/abs/2610.03934), [HTML](https://arxiv.org/html/2610.03934)
- **Code and data:** https://github.com/Li-Yueting/SGAnalog. Apache-2.0, last push 2026-10-01, 3 stars. The Tiny Tapeout index is CC0. All 144 redistributed repos are Apache-2.0. — [GitHub](https://github.com/Li-Yueting/SGAnalog)
- **Dataset scale (README):** 1,448 schematics from 129 repos, of which 125 contribute circuits. 933 circuits, 425 top-level designs, 273 distinct after netlist-graph deduplication. 515 testbenches; 190 of the 412 core-set benches converge after include-path repair. PDK skew: 217 of 237 candidate entries are sky130. — [README](https://github.com/Li-Yueting/SGAnalog)
- **Format:** each circuit in `circuits/` is a SPICE netlist (`.spice`, sometimes `.cir`) plus SVG and PNG, all generated from the same Xschem `.sch` file. Demo-set renders also have a text-blinded `*.blind.svg`. Every netlist carries the BIG-bench canary string. — [README](https://github.com/Li-Yueting/SGAnalog)
- **Simulator and PDK:** "Every task is scored by simulation in a pinned container (ngspice 45.2, sky130A)". The only host requirement is Docker (IIC-OSIC-TOOLS 2026.02). GF180 and IHP SG13G2 are roadmap items, not delivered. — [README](https://github.com/Li-Yueting/SGAnalog), [paper HTML](https://arxiv.org/html/2610.03934)
- **Sizing task definition:** input is the topology with W/L stripped, the author testbench and the targets. Output is the device sizes. Harness: `scripts/sizing_eval.py`; task list: `eval/sizing/tasks.jsonl` (raw: https://raw.githubusercontent.com/Li-Yueting/SGAnalog/main/eval/sizing/tasks.jsonl). Each task has `task_id, repo, dut, title, klass, tb_cir, n_devices, reference`. `reference` holds the human W/L/nf per device, for example sz004 = 5 devices with W=10/L=0.15 and one W=4/L=1. — [tasks.jsonl](https://raw.githubusercontent.com/Li-Yueting/SGAnalog/main/eval/sizing/tasks.jsonl)
- **Testbench paths:** `tb_cir` points to `out/sim_ui/netlists/...`, a pipeline output that is not committed. Running the tasks needs at least `make env` and the simui/netlist stages in Docker. — [tasks.jsonl](https://raw.githubusercontent.com/Li-Yueting/SGAnalog/main/eval/sizing/tasks.jsonl), [README](https://github.com/Li-Yueting/SGAnalog)
- **The 17 sizing tasks** (from `data/sizing_eval_gemini-3.1-pro-preview.csv`; all sky130/ngspice):

  | Task | Class | Repo | Devices |
  |---|---|---|---|
  | sz000 | dac | Jay13ch | 8 |
  | sz001 | passive | tt07-mixer | 2 |
  | sz002 | opamp | tt07-mixer | 7 |
  | sz003 | power | rajkamal | 9 |
  | sz004 | opamp | rajkamal | 5 |
  | sz005 | opamp | Sud-ana TT08 | 7 |
  | sz006 | dac | vga-matrix-dac | 8 |
  | sz007 | dac | analog-vga-dacs | 8 |
  | sz008 | power | dual-oscillator | 2 |
  | sz009 | passive | dual-oscillator | 4 |
  | sz010 | passive | fprn | 2 |
  | sz011 | oscillator | twin-tee-opamp-osc | 7 |
  | sz012 | dac | r2r-dac | 10 |
  | sz013 | power | sonos-flash | 14 |
  | sz014 | power | ramp-adc | 2 |
  | sz015 | passive | ramp-adc | 6 |
  | sz016 | adc | ramp-adc | 7 |

  — [CSV](https://github.com/Li-Yueting/SGAnalog/blob/main/data/sizing_eval_gemini-3.1-pro-preview.csv)
- **Scoring:**
  - The score is 0 if the proposal gives no sizes or the sized circuit dies.
  - Otherwise it is min(r, 1/r) against the human design's metric (output swing, or AC gain converted to the same ratio scale), averaged over the 17 tasks. Survival-only tasks score 1 when the circuit converges.
  - There is no pass threshold and no PVT corners.
  - Runs must finish within 600 s.

  — [README](https://github.com/Li-Yueting/SGAnalog), [paper HTML](https://arxiv.org/html/2610.03934)
- **Published single-call sizing results** (score/100; "no answer" is out of 17):

  | Model | Score | No answer | Decks converge |
  |---|---|---|---|
  | gemini-3.1-pro-preview | 91.2 | 0 | 17/17 |
  | claude-sonnet-5 | 85.4 | 0 | 16/17 |
  | gpt-5 | 79.0 | 0 | 14/17 |
  | claude-haiku-4-5 | 78.3 | 1 | 16/17 |
  | gpt-5.6-sol | 78.2 | 0 | 14/17 |
  | claude-opus-5 | 74.3 | 4 | 17/17 |
  | claude-fable-5 | 31.8 | 11 | 17/17 |

  Every request declares no tools, so runs are closed-book. — [README](https://github.com/Li-Yueting/SGAnalog)
- **Score decomposition:**
  - On the 10 metric-bearing tasks, opus scores 86.4 and gemini 85.1. The other 7 tasks are survival-only.
  - Zeroing proposals that the regurgitation diagnostic flags as full copies of the reference moves gemini from 91.2 to 73.6. No other model moves by more than 12 points.

  — [README](https://github.com/Li-Yueting/SGAnalog)
- **Documented scoring pitfalls:**
  1. A proposal with no sizes used to leave the deck at the human sizes, which scored as perfect. Opus was first reported at 97.9 this way.
  2. Convergence alone is weak: two proposals converged with output swing below 1% of the reference.
  3. gpt-5.6-sol produced sizings that kill the circuit, for example an 80-finger device below the PDK's minimum per-finger width.
  4. The harness once dropped SPICE unit suffixes (`W=10u`).

  — [README](https://github.com/Li-Yueting/SGAnalog), [paper HTML](https://arxiv.org/html/2610.03934)

### Inferences
- For a gm/Id + L agent with corners, the usable SGAnalog tasks are mainly sz002, sz004 and sz005 (op-amps, 5-7 devices). These have no explicit specs, so "success" would have to be "matches or beats the human reference metric", which is not spec-driven sizing.
- Setting up Docker plus the pipeline (`make env`, then the netlist/simui stages) is a real time risk in a 6.5 h day. Rate it as optional.
- If used, Gemini 3.1 Pro's 91.2 (single call) is the published number to quote. Note that the paper itself shows a large part of that number is regurgitation of public human sizes.

### Gaps
- I did not verify how long the IIC-OSIC-TOOLS image takes to build or download, or its size (likely many GB, unverified).
- I did not check whether any per-task targets beyond the human reference metric exist in `scripts/sizing_eval.py`.

## Q2. Other public sizing benchmarks with ready ngspice/open-PDK circuits

### Takeaway
The best ready-to-run held-out set today is **AMS-SizingBench**, shipped inside the public AutoSizer repo. It has 24 SKY130 circuits with explicit spec strings in YAML, runs on ngspice in about 1 s per simulation on a laptop, and already has published LLM-agent and BO numbers, plus an independent audit that gives ground-truth feasibility rates. AnalogGym (BSD-3) is the second option: 16 amplifiers and 6 LDOs on ngspice/SKY130 with selectable corners. AMSbench and Masala-CHAI are question-answering and netlist datasets, not sizing testbenches.

### Cited Findings
- **AMS-SizingBench (AutoSizer, ICML 2026, arXiv 2602.02849)**
  - 24 circuits on SKY130, Ngspice.
  - Easy (5): inverter, buffer, NAND, resistive-load amp, diode-load amp.
  - Medium (6): ring oscillator, 5T-OTA, VCO, telescopic, current-mirror, folded-cascode OTA.
  - Hard (13): LDO, bandgap, 3 filters, switched-capacitor, XOR, AZC, FDGB, NMCNR, SMC, DFCFC, IAC.
  - Spec format example: `fom > 0.100 AND dc_gain_db > 55 AND ugbw > 10 AND power_dc < 50`.
  - Testbench uses only the `tt` corner.

  — [arXiv HTML](https://arxiv.org/html/2602.02849v1), [ICML poster](https://icml.cc/virtual/2026/poster/66562)
- **AMS-SizingBench code:** public at https://github.com/yuxi120407/AutoSizer, last push 2026-05-26, 24 stars, **no license file** (GitHub reports null). The `AMS-SizingBench/*.yaml` files (24 circuits) and `circuit_sim/*.py` are in the repo. — [GitHub](https://github.com/yuxi120407/AutoSizer)
- **AMS-SizingBench design space** (checked in `telescopic_ota.yaml` and `folded_cascode_ota_lp.yaml`):
  - L is fixed at 0.15 µm.
  - Widths come from a 9-value grid `W_values: [0.84 … 2.52]` with integer multipliers.
  - Bias values and currents are fixed params.
  - Example spec: telescopic OTA `fom > 0.08 AND dc_gain_db > 18 AND ugbw > 3.0 AND power_dc < 65`.
  - Example spec: LPF `fom > 0.3 AND lpf_stopband_atten_db > 20 AND lpf_cutoff_hz > 0.5 AND lpf_passband_gain_db > -5`.

  — [telescopic_ota.yaml](https://github.com/yuxi120407/AutoSizer/blob/main/AMS-SizingBench/telescopic_ota.yaml), [folded_cascode_ota_lp.yaml](https://github.com/yuxi120407/AutoSizer/blob/main/AMS-SizingBench/folded_cascode_ota_lp.yaml)
- **SKY130 models:** they come from AnalogGym-Opt (https://github.com/Computing-Intelligent-Decision-Team/AnalogGym-Opt, MIT, companion to an IEEE TCAD paper, doi 10.1109/TCAD.2026.3733612). The audit copies AnalogGym-Opt's bundled `sky130_pdk` and uses ngspice 41, at about 1 s per simulation. — [AnalogGym-Opt](https://github.com/Computing-Intelligent-Decision-Team/AnalogGym-Opt), [audit README](https://github.com/taha-mazher/analog-sizing-agent-audit)
- **AMS-SizingBench runnability (audit):** 22 of 24 circuits run. `bandgap_reference` needs a pnp model file the release does not include, and `switched_capacitor` fails in ngspice. — [audit](https://github.com/taha-mazher/analog-sizing-agent-audit)
- **AnalogGym**
  - Paper: arXiv 2409.08534, ICCAD '24. Repo: https://github.com/CODA-Team/AnalogGym (BSD-3-Clause, 97 stars, last push 2025-10-29). Docs: https://coda-team.github.io/AnalogGym.
  - 30 topologies in total. Only the amplifiers (16) and LDOs (6) have ngspice/SKY130 support; the rest are Spectre/HSPICE.
  - The PDK is included in the testbench, and the corner can be changed there. Design variables live in separate config files.
  - Recommends ngspice ≥ 42 and warns against conda's ngspice-41.

  — [arXiv](https://arxiv.org/abs/2409.08534v1), [docs](https://coda-team.github.io/AnalogGym), [GitHub](https://github.com/CODA-Team/AnalogGym)
- **AnalogCoder / AnalogCoder-Pro**
  - AnalogCoder: https://github.com/laiyao1/AnalogCoder (MIT, 214 stars).
  - AnalogCoder-Pro: https://github.com/laiyao1/AnalogCoderPro (TCAD 2026; **no license file**, 70 stars, pushed 2026-10-30). It is a multimodal LLM framework for topology generation plus sizing.

  — [GitHub API check]; [AnalogCoder-Pro listing](https://relatedrepos.com/gh/laiyao1/AnalogCoderPro), [alphaXiv 2508.02518](https://www.alphaxiv.org/abs/2508.02518.md)
- **AMSbench:** about 8,000 test questions on MLLM perception, analysis and design for AMS circuits; evaluates 8 models including GPT-4o and Gemini-2.5 Pro. It is question-answering, not a sizing simulator. Paper arXiv 2505.24138; data https://huggingface.co/datasets/wwhhyy/AMSBench; code https://github.com/Why0912/AMSBench (no license file). — [AMSBench README](https://github.com/Why0912/AMSBench)
- **Masala-CHAI:** https://github.com/jitendra-bhandari/Masala-CHAI (MIT, 132 stars, pushed 2026-10-26). — [GitHub](https://github.com/jitendra-bhandari/Masala-CHAI)
- **Other 2026 benchmarks** that appeared in search but that I did not read: LOADBench (arXiv 2610.21591), Long-Horizon Analog Design Bench (arXiv 2609.33356), AnalogPortBench (arXiv 2610.33088, lists "corner-robust design" and LLM-guided numerical sizing as open gaps). — [LOADBench](https://arxiv.org/pdf/2610.21591), [Long-Horizon](https://arxiv.org/pdf/2609.33356), [AnalogPortBench](https://arxiv.org/pdf/2610.33088)

### Inferences
- **Primary held-out set: AMS-SizingBench.** It is runnable on a laptop, the specs were written by someone else, and published numbers exist for AutoSizer (Gemini-2.5-Flash), GA, BO, TuRBO, ADO-LLM, LEDRO and EE-Sizer, plus the audit's TPE/GP-BO/random numbers at equal budgets.
- **Circuits worth using.** Pick circuits with a non-trivial but non-zero feasible fraction: telescopic_ota (8.3%), folded_cascode_ota (2.4%), ldo_regulator (6.9%), azc (7.6%), fdgb (7.1%), smc (1.4%), iac (0.9%), nmcnr (0.2%), diode_load_amp (0.7%).
- **Circuits to avoid:**
  - five_trans_ota and current_mirror_ota: 100% of designs pass.
  - inverter and resistive_load_amp: the design space is smaller than the budget.
  - the three filters: broken spec check, and likely infeasible.
  - bandgap and switched_cap: do not run.
- **Design-space mismatch.** AMS-SizingBench fixes L and uses a discrete W grid, so the user's "choose gm/Id and L" agent does not map 1:1. Option (a): run in the benchmark's native space, with the LLM choosing ranges/priorities over the given variables, for comparability. Option (b): expose L, which departs from published numbers. Say clearly which one was done.
- **Corners.** AMS-SizingBench is TT-only. To test corners, swap the `.lib` section to ss/ff plus V/T in the runner (AnalogGym documents corner swapping in its testbench). Report the corner-extended results as the team's own extension, not as comparable to published numbers.
- AnalogCoder-Pro and AMS-SizingBench have no license files. Using them for evaluation is fine; redistributing their files in the team's repo is legally unclear, so link to them instead of copying.

### Gaps
- Exact AnalogGym amplifier list and per-circuit specs not extracted.
- Masala-CHAI content and size not verified this session (my memory: a schematic-to-SPICE netlist dataset; unverified).
- AnalogCoder-Pro benchmark size and simulator not verified.

## Q3. Published LLM-agent sizing results 2025-2026, LLM vs BO, and documented reward-hacking/spec-gaming

### Takeaway
Published LLM-agent papers report big gains over BO and GA. The only independent rerun found (the AutoSizer audit) shows that plain TPE and GP-BO at 300 simulations meet every spec in about 70% and 68% of runs on the hard circuits. It also shows that AutoSizer's own success check silently drops negative-threshold specs, which inflates success from about 69% (true) to 94% and lets the agent "declare success" on designs that fail.

The concrete spec-gaming failure modes on record:
- dropped or unparsed constraints;
- FoM maximisation choosing infeasible designs;
- "no answer" scored as the human reference;
- convergence without function;
- invalid geometry;
- TT-pass / slow-corner fail;
- regurgitation of public reference sizes;
- budget overshoot.

### Cited Findings
- **AutoSizer (arXiv 2602.02849, ICML 2026; Yu, Torbunov, Mandal, Ren)**
  - Method: inner sizing loop (multiple optimisers) plus an outer LLM loop that reviews history, changes variable priorities and widens/narrows ranges. Uses Gemini-2.5-Flash (temperature 0.4).
  - Budget: all baselines get 300 samples; AutoSizer up to 3 outer iterations × 100 samples. Results are averaged over 3 trials.
  - AutoSizer results by split:

    | Split | FoM | Evals | Time (s) | SR |
    |---|---|---|---|---|
    | Easy | 33.4 ± 9.2 | 57.8 | 362.6 | 100% |
    | Medium | 33.2 ± 2.8 | 130.9 | 1049.8 | 100% |
    | Hard | 60.2 ± 3.4 | 128.9 | 1152.6 | 100% |

  - Best Hard-split baselines: LEDRO FoM 50.5, SR 80%; EE-Sizer 49.6, SR 56.4%; GA 45.3, SR 50%.
  - The paper reports no token or $ cost.
  - Ablation SR is internally inconsistent: the text says 100% → 20% without SRL, but Table 3 lists 50%.

  — [arXiv HTML](https://arxiv.org/html/2602.02849v1)
- **Independent AutoSizer audit** (https://github.com/taha-mazher/analog-sizing-agent-audit; created 2026-10-27, WIP, no license, unreviewed). It reruns the public release at commit 1b9b8f6.
  - **Negative-threshold bug.** The success check regex `(\w+)\s*([><=]+)\s*([\d.]+)` cannot match a minus sign, so specs like `lpf_passband_gain_db > -5` are skipped. **I verified this:** that exact pattern is at lines 1185-1186 of `utils/feedback_extraction.py` in yuxi120407/AutoSizer.
  - Effect: across 180 optimiser runs, AutoSizer's check reports 93-95% success where the true rate is 68-70%.
  - The agent declares success on all filter trials, and none of those designs meets spec.
  - TPE/GP-BO/random at 300 sims on 12 hard circuits (5 seeds) meet every spec in **70% / 68% / 58%** of runs. At 100 sims: 67 / 60 / 48%. At 50 sims: 65 / 58 / 38%.
  - Ground-truth feasible fraction per circuit: 5T-OTA and CM-OTA 100%; telescopic 8.3%; FC-OTA 2.4%; LDO 6.9%; nmcnr 0.2%; filters and VCO none found.
  - FoM maximisation picked an infeasible design over an already-found feasible one in 13 of 180 runs.
  - The agent overshot its 100-design budget (124-171 designs) in 4 of 8 trials, because the budget is checked only between rounds.
  - At matched per-trial budgets on iac/nmcnr/smc: TPE 65%, GP-BO 52%, random 40%, vs the agent (Qwen3-8B, fixed prompt) 8/8.
  - The paper's model (Gemini 2.5 Flash) is no longer served to new API keys. The single Gemini 3.5 Flash trial claimed success on the LPF but did not truly meet spec.
  - The public code uses 100 designs and has no TuRBO, unlike the paper.

  — [audit README](https://github.com/taha-mazher/analog-sizing-agent-audit)
- **AgenticSizing** (arXiv 2609.25873; Hao, Verma, Guo, Sestito, O'Boyle, Bouganis, Prodromakis; code https://github.com/aprilaihub/agentic-analog-sizing, Apache-2.0)
  - Setup: Cadence Spectre (not open), PDK not stated; GPT-5.2 via LangGraph; 5 runs per method under a wall-clock budget.
  - Success and simulations per method:

    | Circuit | DE | BO | MA-RL | AgenticSizing |
    |---|---|---|---|---|
    | BGR | 5/5, avg 936 sims | 3/5, avg 208 | 0/5 | 4/5, avg 22.75 |
    | Two-stage FC | 0/5 | 0/5 | 0/5 | 4/5, 11-29 sims |
    | LDO | 1/5, 1131 | 4/5, ~524 | 1/5 | 4/5, avg 79 |
    | LDO+FC+BGR (55 transistors, 60 variables) | 0/5 | 0/5 | 0/5 | 3/5, ~83-90 sims |

  - About 3.7M tokens per full-LDO run.
  - "Limit-cycle-like" oscillation near spec boundaries.
  - No PVT corners.
  - Internal inconsistencies in iteration counts (83 vs 89.7 vs 90).

  — [arXiv HTML](https://arxiv.org/html/2609.25873v1)
- **AnalogScout** (arXiv 2610.37319; Long, Zhu; submitted to ISCAS 2027; no code found)
  - Method: LLM agent with elite-design reuse, analytic gm/Id local sizing and full-corner verification; SKY130; 3 OTAs.
  - At an equal 16k-simulation budget it cuts power by 43.3% vs CMA-ES and reaches CMA-ES's final power in 7.0% of the budget.
  - Lower power than BO on all 3 OTAs, and than two re-implemented LLM flows on 2 OTAs.

  — [arXiv abs](https://arxiv.org/abs/2610.37319)
- **White-Box Reasoning** (arXiv 2508.13172; Chen, Li, He; Aug 2025). Closest to the user's design: LLM plus gm/Id lookup tables, SKY130, ngspice 42, gemini-2.0-flash.
  - Circuit: two-stage op-amp. Specs: Gain > 60 dB, GBW > 20 MHz, PM > 60°, SR > 20 V/µs, Idc < 200 µA, CL = 2 pF.
  - Met all specs at TT in 5 iterations (final 62.40 dB / 25.30 MHz / 65.80° / 28.18 V/µs / 116.4 µA). A 6th "all-corner" iteration extended it to PVT.
  - Ablation without gm/Id data: "significantly slower and more prone to deviation".
  - Compared to a senior engineer's design, not to BO.

  — [arXiv PDF](https://arxiv.org/pdf/2508.13172)
- **TT-pass / slow-corner fail:** per a search summary of the same paper, a design met spec at TT but failed at a slow-process corner and was fixed with a small adjustment based on corner margins. I did not read this passage directly. — [arXiv PDF](https://arxiv.org/pdf/2508.13172)
- **SGAnalog-documented failure modes:** see Q1. They are: no-answer scored as perfect, convergence with less than 1% swing, invalid geometry below the minimum finger width, unit-suffix parsing, and regurgitation (gemini 91.2 → 73.6 when copies are zeroed). — [SGAnalog README](https://github.com/Li-Yueting/SGAnalog)
- **Other related LLM sizing works in search results** (numbers not extracted): LEDRO (arXiv 2411.12930, LLM-chosen regions plus TuRBO), EasySize (arXiv 2508.05113), TopoSizing (arXiv 2509.14169), SLIC (arXiv 2611.13452, RL-fine-tuned small LMs), SABLE (arXiv 2607.03701, NDA-safe industrial loop), ADO-LLM (arXiv 2406.18770, BO plus LLM in-context). — [LEDRO](https://arxiv.org/abs/2411.12930), [EasySize](https://arxiv.org/pdf/2508.05113), [TopoSizing](https://arxiv.org/pdf/2509.14169), [SLIC](https://arxiv.org/pdf/2611.13452), [SABLE](https://arxiv.org/pdf/2607.03701)

### Inferences
- The fair reference number for the BO baseline is the audit's: GP-BO about 60% at 100 sims and 68% at 300 sims on hard AMS-SizingBench circuits; TPE slightly better. If the team's BO baseline does much worse than this on the same circuits, the baseline is probably mis-tuned.
- **Guards to build in on day one** (each maps to a failure seen in a published paper):
  1. Parse specs with signed numbers, and unit-test that `> -5` is enforced.
  2. Report "verified success" (every spec, every corner, re-simulated independently), never the agent's self-reported success.
  3. Select the final design from feasible designs first, FoM second.
  4. Count every simulation, at every corner, against a hard budget checked before each sim.
  5. Check the operating region: all signal-path devices in saturation (vds > vdsat + margin) at every corner.
  6. Reject geometry below PDK minimums.
  7. Score a missing answer as failure.
  8. Add a functional check (output swing or gain) on top of "ngspice converged".
- LLM-agent "sims to spec" numbers in papers (AgenticSizing 11-90, White-Box 5-6) are not comparable to BO budgets unless each LLM-proposed simulation and each corner counts as one simulation. Count the same way for every method.

### Gaps
- ADO-LLM, LLANA and AnalogXpert numbers not extracted this session (time limit).
- RobustAnalog (corners as multi-task RL; the user's notes say 14-30× fewer sims) and AutoCkt (DATE 2020, arXiv 2001.01808) numbers not re-verified this session. Cite the original papers directly if quoted.
- I found no paper that specifically documents an LLM pushing devices out of saturation to game specs. This remains a plausible failure mode, but it is not documented in the sources I found.
- The AutoSizer audit is unreviewed. Only the regex claim was independently checked.

## Q4. A fair, simple protocol for a one-day project

### Takeaway
Freeze the code first. Then run 5-6 AMS-SizingBench circuits whose specs the team never wrote or tuned on, with 3 seeds each, a 100-simulation budget (every corner sim counts), and 3 methods: agent, GP-BO/TPE at the same budget, and a single LLM call. Report verified all-corner success, sims to first feasible design, tokens/$, and wall-clock. Show tuned-on and held-out circuits side by side.

### Cited Findings
- **Budget and seed precedents:**
  - AutoSizer: 300 samples for baselines, 3 trials.
  - The AutoSizer release: 100 designs.
  - The audit: 50/100/300-sim curves; 5 seeds live, 20 seeds replayed.
  - AgenticSizing: 5 runs per method with wall-clock budgets of 2-6 h.

  — [AutoSizer](https://arxiv.org/html/2602.02849v1), [audit](https://github.com/taha-mazher/analog-sizing-agent-audit), [AgenticSizing](https://arxiv.org/html/2609.25873v1)
- **Simulation cost:** about 1 s per simulation with the AutoSizer runner on SKY130 (VCO about 40 s). The audit's full live benchmark took about 12 h on 4 cores. — [audit](https://github.com/taha-mazher/analog-sizing-agent-audit)
- **Metric precedents:**
  - AutoSizer: FoM, evals to best, time, SR%.
  - AgenticSizing: success k/5, sims for successful runs, tokens.
  - SGAnalog: Wilson 95% intervals and paired McNemar tests on 66 tasks; it notes that ±11 pp intervals are typical at that size.

  — [AutoSizer](https://arxiv.org/html/2602.02849v1), [AgenticSizing](https://arxiv.org/html/2609.25873v1), [SGAnalog](https://github.com/Li-Yueting/SGAnalog)

### Inferences (recommended protocol)
1. **Split.**
   - **Tuned-on:** the 1-2 circuits the team built prompts on (for example their own two-stage op-amp).
   - **Held-out:** AMS-SizingBench circuits never opened during development, for example telescopic_ota, folded_cascode_ota, ldo_regulator, azc, smc and diode_load_amp.
   - Use the YAML specs unchanged; they were written by the AutoSizer authors, which meets the "not written by the team" rule.
   - Optional third tier: 2-3 SGAnalog op-amps (sz002/sz004/sz005) scored against the human reference.
2. **Freeze.** Tag the git commit and record the prompt hashes before the first held-out run. Do not edit prompts after seeing held-out results; if you must, report both versions.
3. **Budget.** 100 simulations per run, with each (design × corner) counted as one. Corners: TT plus SS/FF at fixed V/T (3 corners), or TT-only if time is short, and say so.
4. **Compute estimate.** 6 circuits × 3 methods × 3 seeds × 100 sims ≈ 5,400 sims, about 1.5 h on 1 core at 1 s/sim, under 20 min on 8 cores. A 300-sim budget is feasible only with parallelism.
5. **Methods.**
   - (a) Full agent.
   - (b) GP-BO or Optuna TPE over the same variables and ranges, same budget, no LLM.
   - (c) Single LLM call: Gemini proposes sizes once, then one verification sim per corner.
   - Optional (d): the agent with the LLM range-setting disabled, to show the LLM's contribution.
6. **Seeds.** 3 per method per circuit, 5 if time allows. Report k/n, not percentages alone.
7. **Main table** (one row per method, held-out block):

   | Column | Content |
   |---|---|
   | Verified success | All specs × all corners, k/(circuits × seeds) |
   | TT-only success | Shows the corner gap |
   | Median sims to first all-corner-feasible design | Failures count as > budget |
   | Best feasible FoM | Or power at spec |
   | LLM calls / tokens / $ per run | |
   | Wall-clock per run | |

8. **Tuned vs held-out gap.** Use the same columns for the tuned-on block and show the difference in verified success and median sims. A per-circuit dot plot (success vs sims) with tuned/held-out colours makes the gap visible.
9. **Honesty checks to show judges.** Fraction of "agent claimed success" runs that pass independent re-verification, the saturation-check pass rate, and comparison numbers taken from the audit (GP-BO 60% at 100 sims on hard circuits, measured TT-only).

### Gaps
- I found no published per-circuit BO numbers for the specific subset suggested above with corners added. Corner results will be the team's own, with no external reference.

## Q5. Does a schematic image or datasheet PDF help LLM circuit understanding or sizing?

### Takeaway
The evidence is mixed and indirect. NEMESIS reports that the schematic image resolves port roles and mirrored branches that the netlist alone leaves unclear. SGAnalog finds that sizing is text-only and decoupled from vision ability (models that read schematics badly still size well), and that models lean on net labels as anchors. I found no study measuring datasheet-to-spec extraction for sizing. An ablation is cheap to run on role labeling, and more expensive on downstream sizing.

### Cited Findings
- **NEMESIS** (arXiv 2607.05657; Ghosh, Harjani, Sapatnekar, Jul 2026): multimodal OTA framework. It takes netlist plus schematic, identifies primitives, then synthesises performance equations. Five OTA topologies in a commercial 65 nm PDK; reported error under 7% and about 4622× speedup over SPICE. The paper says the schematic image helps resolve port roles, mirrored branches and ambiguous substructures that the netlist alone leaves unclear. (From a search summary; the full text was not read.) — [arXiv](https://arxiv.org/abs/2607.05657)
- **SGAnalog transcription:**
  - Best exact-isomorphism rate is 56.1% (claude-fable-5). Gemini-3.1-pro is at 39.4%.
  - Blinding net labels and instance names lowers exact isomorphism for every model above the floor, by 3-18 pp.
  - "Every Claude tier that answers sizes competently while reading ability spans 56.1% to 1.5%; sizing is text-only."

  — [SGAnalog README](https://github.com/Li-Yueting/SGAnalog)
- **Auto-SPICE** (arXiv 2411.14299): GPT-4o detects components in schematic images but struggles to connect them correctly. — [arXiv](https://arxiv.org/abs/2411.14299v1)
- **AMSbench:** current MLLMs (GPT-4o, Gemini-2.5 Pro and others) show significant limitations in complex multimodal reasoning and circuit design tasks. — [AMSBench README](https://github.com/Why0912/AMSBench)
- **AgenticSizing** avoids free-form roles: the decomposer assigns each device one role from a predefined ontology to prevent unsupported labels. — [AgenticSizing](https://arxiv.org/html/2609.25873v1)

### Inferences
- **Ablation design.** Three arms: (1) netlist only, (2) netlist + schematic PNG, (3) netlist + PNG + datasheet/spec PDF. Same held-out circuits and seeds.
- **Cheap metric:** role-labeling accuracy (input pair / mirror / tail / cascode / load / output stage) against a hand-made answer key on 6-10 held-out circuits. SGAnalog's PNG + netlist pairs give exact image-netlist correspondence.
- **Expensive metric:** downstream verified success and sims to spec.
- **Expectation from the literature.** Images help most when net and instance names are uninformative. Test this with a blinded-label arm, for example by renaming nets to n1..nk, following SGAnalog's blinded condition.
- **Datasheet arm.** Measure spec-extraction accuracy (exact numeric match per spec field) separately from sizing, since no prior work quantifies it.

### Gaps
- No study found that measures datasheet-PDF-to-spec extraction accuracy for analog sizing, or that shows an image improving sizing outcomes (as opposed to transcription or modelling).
- NEMESIS's ablation numbers (with vs without the image) were not read; only the claim was found.
