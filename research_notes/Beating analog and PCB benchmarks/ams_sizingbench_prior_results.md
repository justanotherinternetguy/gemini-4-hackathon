# Published results to beat on AMS-SizingBench and other analog sizing benchmarks (as of 2026-11-21)

Scope note. Research done 2026-11-21 in about 10 minutes. Sources read directly: arXiv HTML of 2602.02849v1 (AutoSizer), 2610.39395 (OPERA), 2610.37319 (AnalogScout), 2411.12930 (LEDRO), 2509.25510 (EEsizer), 2406.18770 (ADO-LLM), 2508.13172 (White-Box); the audit repo's README, `results/summary.txt` and `results/live_*.csv`, `results/bench_runs.csv`; the local AutoSizer clone at `/home/thinkies/NewGit/gemini-4-hackathon/third_party/AutoSizer` (commit `1b9b8f6`, the same commit the audit used). Lines marked "computed by me" are counts I derived from the audit's CSVs; they are not printed in the audit README. The audit is an unreviewed, 0-star, work-in-progress repo (last push 2026-10-28). This file extends `research_notes/Gemini analog sizing hackathon plan/benchmarks_and_baselines.md` and does not repeat what that file already verified.

## Q1. AutoSizer / AMS-SizingBench: exact numbers, tiers, budget, trials, LLM, success definition

### Takeaway
The AutoSizer paper publishes only tier-level averages (Easy / Medium / Hard), never per-circuit numbers, so there is no published per-circuit AutoSizer result to beat. The headline is 100% success in all three tiers at a 300-sample budget, 3 trials, Gemini-2.5-Flash, TT corner only, with success judged by the paper's own (buggy) spec check. The public release differs from the paper in budget (100 designs, not 300) and in spec strings (the released telescopic spec is much looser than the one printed in the paper).

### Cited Findings

**Paper Table 2 (tier averages, 3 trials, 300-sample budget).** FoM is the normalised FoM; Evals is "evaluations to best solution", not total simulations used; Time is seconds per trial; SR is "the percentage of trials that meet user specifications". — [arXiv HTML 2602.02849v1, Table 2](https://arxiv.org/html/2602.02849v1)

| Method | Easy FoM | Easy Evals | Easy Time | Easy SR | Med FoM | Med Evals | Med Time | Med SR | Hard FoM | Hard Evals | Hard Time | Hard SR |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Genetic | 30.2±1.6 | 110.5±42.9 | 515.6 | 93.3% | 30.7±1.8 | 193.7±47.9 | 1638.6 | 73.3% | 45.3±3.5 | 165.6±49.2 | 1689.2 | 50.0% |
| BO (GP, Matérn 2.5, UCB β=2) | 24.1±4.0 | 84.0±21.4 | 415.0 | 80.0% | 31.2±3.6 | 153.6±59.8 | 1492.5 | 73.3% | 46.2±2.3 | 151.3±51.2 | 1875.1 | 50.0% |
| TuRBO | 15.8±3.9 | 20.6±6.9 | 141.0 | 39.8% | 27.4±4.3 | 75.7±25.1 | 1250.2 | 39.8% | 36.7±12.7 | 107.2±19.1 | 2105.2 | 25.0% |
| ADO-LLM (re-impl.) | 25.2±3.5 | 87.2±11.5 | 527.2 | 80.0% | 29.2±2.7 | 148.5±23.5 | 1328.6 | 80.0% | 47.3±9.1 | 162.3±42.3 | 1278.5 | 41.0% |
| LEDRO (re-impl.) | 31.3±8.7 | 65.6±13.4 | 385.6 | 100% | 31.6±5.6 | 142.5±32.5 | 1150.7 | 80.0% | 50.5±8.5 | 140.8±23.2 | 1350.8 | 80.0% |
| EE-Sizer (re-impl.) | 30.3±9.8 | 95.3±21.8 | 756.2 | 100% | 30.5±5.4 | 165.2±37.4 | 1485.3 | 80.0% | 49.6±11.5 | 170.8±20.5 | 1872.6 | 56.4% |
| **AutoSizer** | 33.4±9.2 | 57.8±12.2 | 362.6 | 100% | 33.2±2.8 | 130.9±38.2 | 1049.8 | 100% | 60.2±3.4 | 128.9±23.0 | 1152.6 | 100% |

- **No random-search baseline is in the paper.** The baselines are GA, BO, TuRBO, ADO-LLM, LEDRO and EE-Sizer only. AmpAgent, LLM-USO and LLM-ACD were excluded as not reproducible. — [arXiv HTML 2602.02849v1, Sec. 4.1](https://arxiv.org/html/2602.02849v1)
- **No per-circuit results table exists in arXiv v1.** The HTML has Tables 1-5 only: related-work comparison, tier results, ablation, circuit summary, specification checklist. — [arXiv HTML 2602.02849v1](https://arxiv.org/html/2602.02849v1)
- **Ablation (Table 3; 5 circuits = 1 easy, 2 medium, 2 hard; 3 trials each):** — [arXiv HTML 2602.02849v1, Table 3](https://arxiv.org/html/2602.02849v1)

  | Variant | FoM | Evals | Time (s) | SR |
  |---|---|---|---|---|
  | Full | 35.47 | 127.5 | 925.8 | 100% |
  | w/o Circuit Understanding | 28.65 | 137.5 | 1206.4 | 73.4% |
  | w/o CU and initial search space | 33.43 | 145.2 | 1053.8 | 86.8% |
  | w/o Optimization Engine (fixed BO) | 27.00 | 175.4 | 1881.4 | 50.0% |
  | w/o Self-Reflection Loop | 25.32 | 150.9 | 956.8 | 50.0% |
  | w/o CU, SSD and SRL | 21.51 | 136.5 | 857.6 | 20.0% |

**Tier membership (paper Table 4, with transistor counts).** — [arXiv HTML 2602.02849v1, Table 4](https://arxiv.org/html/2602.02849v1)

| Tier | Circuits (#transistors) |
|---|---|
| Easy (5) | inverter (2), buffer (4), nand_gate (4), resistive_load_amp (1), diode_load_amp (2) |
| Medium (6) | ring_oscillator 3-stage (6), five_trans_ota (6), vco (10), telescopic_ota (10), current_mirror_ota (10), folded_cascode_ota (10) |
| Hard (13) | ldo (11), bandgap (12), folded_cascode_ota low-pass / high-pass / band-pass filters (10 each), switched_capacitor (10), xor_gate (14), AZC (25), fdgb (26), nmcnr (24), smc (24), dfcfc (26), iac (35) |

So of the team's ten runnable circuits: **Medium** = five_trans_ota, telescopic_ota, current_mirror_ota, folded_cascode_ota; **Hard** = azc, smc, iac, fdgb, dfcfc, nmcnr. None is in the Easy tier.

**Protocol.**
- LLM: Gemini-2.5-Flash, temperature 0.4, top-p 0.85, top-k 20, max 8192 output tokens. LEDRO, ADO-LLM and EE-Sizer were re-implemented by the AutoSizer authors on the same Gemini-2.5-Flash configuration, "and a unified 300-sample budget as AutoSizer". — [arXiv HTML 2602.02849v1, Sec. 4.1](https://arxiv.org/html/2602.02849v1)
- Trials: Table 2 "reports average three trails". — [arXiv HTML 2602.02849v1, Sec. 4.2](https://arxiv.org/html/2602.02849v1)
- Simulator and PDK: Ngspice with the SKY130 PDK; each task is "a simulation-driven constrained optimization problem maximizing a figure-of-merit". — [arXiv HTML 2602.02849v1, Sec. 4.1](https://arxiv.org/html/2602.02849v1)
- Baseline settings: GA population 20, crossover 0.8, mutation 0.1, tournament selection, elitism; BO = GP with Matérn ν=2.5, UCB β=2.0, 10 random initial samples. — [arXiv HTML 2602.02849v1, Sec. 4.1](https://arxiv.org/html/2602.02849v1)
- Cost: the paper gives no token count and no dollar cost. The only cost numbers found are from the audit: one Gemini 3.5 Flash trial used 3 API calls, 11,757 input tokens, 25 designs and 149 s. — [audit summary.txt](https://github.com/taha-mazher/analog-sizing-agent-audit/blob/main/results/summary.txt)
- **Release defaults differ from the paper.** In the release, `main.py` sets `n_trials = 3`, `max_total_designs = 100`, `num_variables_to_optimize = 3`, `max_regeneration_cycles = 3`, `plateau_patience = 2` (lines 191-195 of the local clone); the README states the same table. The audit notes the release "uses 100 designs and contains no TuRBO". — [AutoSizer README](https://github.com/yuxi120407/AutoSizer), [audit README](https://github.com/taha-mazher/analog-sizing-agent-audit)
- **Corners:** every testbench template I checked (telescopic_ota, smc, azc) loads `.lib {pdk_lib_path} tt` only. — [telescopic_ota.yaml](https://github.com/yuxi120407/AutoSizer/blob/main/AMS-SizingBench/telescopic_ota.yaml)

**Success definition.**
- The spec is the `user_specs_metric` string: constraints joined by `AND`, each `metric op number`. "A design is feasible when every constraint is satisfied." — [AutoSizer README, FoM Definition](https://github.com/yuxi120407/AutoSizer)
- FoM = product of (y_i / y_i_spec) over maximise metrics divided by product of (y_j / y_j_spec) over minimise metrics, auto-generated from the same string. Example: `fom > 0.100 AND dc_gain_db > 55 AND ugbw > 10 AND power_dc < 50` gives `FoM = (dc_gain_db/55) × (ugbw/10) / (power_dc/50)`. — [AutoSizer README](https://github.com/yuxi120407/AutoSizer)
- Code path: `check_user_specs_met(best_design_dict, user_specs_metric, verbose=True, fom_only_check=False)` at line 2406 of `llm_guided_ota_optimization_test.py`, applied to the **best-FoM design** of the run; the parser regex is `(\w+)\s*([><=]+)\s*([\d.]+)` at `utils/feedback_extraction.py` line 1185. The function's default is `fom_only_check=True`, which checks only the `fom` term. — [feedback_extraction.py](https://github.com/yuxi120407/AutoSizer/blob/main/utils/feedback_extraction.py), [llm_guided_ota_optimization_test.py](https://github.com/yuxi120407/AutoSizer/blob/main/llm_guided_ota_optimization_test.py)

**Released spec strings for the ten local circuits** (read from the local clone, commit `1b9b8f6`). — [AMS-SizingBench YAMLs](https://github.com/yuxi120407/AutoSizer/tree/main/AMS-SizingBench)

| Circuit | Tier | `user_specs_metric` in the release |
|---|---|---|
| five_trans_ota | Med | `fom > 0.5 AND dc_gain_db > 15 AND ugbw > 10 AND power_dc < 65` |
| current_mirror_ota | Med | `fom > 0.35 AND dc_gain_db > 15 AND ugbw > 10 AND power_dc < 65` |
| telescopic_ota | Med | `fom > 0.08 AND dc_gain_db > 18 AND ugbw > 3.0 AND power_dc < 65` |
| folded_cascode_ota | Med | `fom > 3.0 AND dc_gain_db > 1.5 AND ugbw > 10 AND power_dc < 1.0` |
| azc | Hard | `fom > 1.0 AND dc_gain_db > 45 AND ugbw > 60 AND power_dc < 120` |
| smc | Hard | `dc_gain_db > 40 AND ugbw > 10 AND power_dc < 500` |
| iac | Hard | `dc_gain_db > 30 AND ugbw > 50 AND power_dc < 1000` |
| fdgb | Hard | `dc_gain_db > 20 AND ugbw > 1 AND power_dc < 200` |
| dfcfc | Hard | `dc_gain_db > 20 AND ugbw > 10 AND power_dc < 400` |
| nmcnr | Hard | `dc_gain_db > 18 AND ugbw > 10 AND power_dc < 600` |

- **Paper spec differs from release spec.** The paper's Appendix A.2 and the README print the telescopic spec as `fom > 0.100 AND dc_gain_db > 55 AND ugbw > 10 AND power_dc < 50`; the released YAML has `fom > 0.08 AND dc_gain_db > 18 AND ugbw > 3.0 AND power_dc < 65`. — [arXiv HTML 2602.02849v1, App. A.2](https://arxiv.org/html/2602.02849v1); contradicted by [telescopic_ota.yaml](https://github.com/yuxi120407/AutoSizer/blob/main/AMS-SizingBench/telescopic_ota.yaml)
- **Design space of the hard amplifiers is not "fixed L".** The simple OTAs fix L = 0.15 µm and use the 9-value `W_values` grid, but smc, iac, fdgb, dfcfc, nmcnr and azc expose a shared `L` variable with a discrete grid (for example smc `L_values: [1.0 … 4.0]`, iac `[0.5 … 4.0]`) plus per-device width grids such as `W_BIAS_P_values: [3.0, 5.0, 7.0, 10.0]`. — [smc.yaml](https://github.com/yuxi120407/AutoSizer/blob/main/AMS-SizingBench/smc.yaml)

### Inferences
- The paper's 100% / 100% / 100% is the only AutoSizer number to quote, and it cannot be reproduced exactly today: the paper's budget (300) and at least one paper spec string differ from the release, Gemini 2.5 Flash is reported as no longer served to new keys, and the success check is buggy (Q2).
- The reported SR percentages do not all correspond to whole trial counts. With 13 hard circuits × 3 trials = 39 trials, 56.4% = 22/39 and 41.0% = 16/39 fit, but 80.0%, 50.0% and 25.0% are not k/39. With 6 medium circuits × 3 = 18 trials, 73.3% and 80.0% are not k/18 (they are 11/15 and 12/15). TuRBO shows 39.8% in both Easy and Medium. So per-trial counts cannot be recovered from the paper, and SR may be averaged over a subset of circuits. This is my arithmetic, not a statement in any source.
- "Evals" in Table 2 is evaluations to the best solution. AutoSizer's 128.9 on Hard is above the release budget of 100, which is consistent with the paper having run at 300. A hackathon claim of "fewer simulations" must compare the same quantity (total simulations used, or simulations to first feasible), and say which.
- folded_cascode_ota's released spec (`dc_gain_db > 1.5`, `power_dc < 1.0`) looks like a unit or scaling quirk of that testbench. Do not describe it as "gain > 1.5 dB" in a slide without checking what the testbench reports.
- Five of the six hard amplifiers have no `fom >` term, so on those circuits success is three plain constraints (gain, UGBW, power) at TT.

### Gaps
- Per-circuit AutoSizer and baseline numbers: not published in arXiv v1. I did not check whether a later arXiv version or the ICML camera-ready supplement adds a per-circuit appendix.
- Dollar or token cost of the paper's Gemini-2.5-Flash runs: not reported anywhere I found.
- Which 5 circuits form the ablation set: not named in the text I read.
- Whether the paper's runs used the looser released specs or the tighter printed ones: unknown.

## Q2. The independent audit: corrected rates, per-circuit numbers, bugs

### Takeaway
On honest scoring, plain Optuna TPE is a strong baseline: on the nine runnable non-filter "hard-set" circuits it meets every spec in 40 of 45 runs at 100 simulations and 42 of 45 at 300. On the six advanced amplifiers the team can run, TPE gets 25/30 at 100 simulations and 27/30 at 300; only iac (2/5 at 100) and to a lesser degree smc and fdgb (4/5) leave room for a method to show a gain. Four of the team's ten circuits (five_trans_ota, current_mirror_ota, telescopic_ota, and dfcfc) are passed by random search essentially every time and cannot support a "beats" claim.

### Cited Findings

**Aggregate (12 circuits, 5 seeds, live; share of runs whose best-FoM design meets every spec / passes AutoSizer's own check).** — [audit README](https://github.com/taha-mazher/analog-sizing-agent-audit), [summary.txt](https://github.com/taha-mazher/analog-sizing-agent-audit/blob/main/results/summary.txt)

| Budget | TPE true / AutoSizer check | GP-BO | Random |
|---|---|---|---|
| 50 | 65 / 88% | 58 / 83% | 38 / 63% |
| 100 | 67 / 87% | 60 / 85% | 48 / 73% |
| 300 | 70 / 95% | 68 / 93% | 58 / 83% |

"Any feasible design seen" (looser: the run found a feasible design at some point): 68 / 67 / 47% at 50; 72 / 68 / 60% at 100; 73 / 73 / 72% at 300 for TPE / GP / random. — [summary.txt](https://github.com/taha-mazher/analog-sizing-agent-audit/blob/main/results/summary.txt)

**Per circuit at 300 simulations, % of 5 seeds, true / AutoSizer check (printed in summary.txt).** — [summary.txt](https://github.com/taha-mazher/analog-sizing-agent-audit/blob/main/results/summary.txt)

| Circuit | GP-BO | Random | TPE |
|---|---|---|---|
| 3_stage_ring_osc | 100 / 100 | 80 / 80 | 100 / 100 |
| azc | 100 / 100 | 40 / 40 | 100 / 100 |
| dfcfc | 100 / 100 | 100 / 100 | 80 / 80 |
| fdgb | 100 / 100 | 100 / 100 | 100 / 100 |
| folded_cascode_ota | 100 / 100 | 100 / 100 | 100 / 100 |
| folded_cascode_ota_bandp | 0 / 100 | 0 / 100 | 0 / 100 |
| folded_cascode_ota_hp | 0 / 100 | 0 / 100 | 0 / 100 |
| folded_cascode_ota_lp | 0 / 100 | 0 / 100 | 0 / 100 |
| iac | 40 / 40 | 40 / 40 | 80 / 80 |
| ldo_regulator | 100 / 100 | 100 / 100 | 100 / 100 |
| nmcnr | 100 / 100 | 60 / 60 | 100 / 100 |
| smc | 80 / 80 | 80 / 80 | 80 / 80 |

**Per circuit at 50 / 100 / 300 simulations: seeds (of 5) whose best-FoM design truly meets every spec. Computed by me from `results/live_<circuit>.csv` (column `ok_true`); in brackets, seeds that saw any feasible design (`any_feasible`) where that differs.** — [audit results folder](https://github.com/taha-mazher/analog-sizing-agent-audit/tree/main/results)

| Circuit | TPE 50 | TPE 100 | TPE 300 | GP 50 | GP 100 | GP 300 | Rand 50 | Rand 100 | Rand 300 |
|---|---|---|---|---|---|---|---|---|---|
| azc | 5 | 5 | 5 | 1 [5] | 1 [5] | 5 | 1 [5] | 2 [5] | 2 [5] |
| dfcfc | 5 | 5 | 4 [5] | 5 | 5 | 5 | 5 | 5 | 5 |
| fdgb | 4 [5] | 4 [5] | 5 | 5 | 5 | 5 | 5 | 5 | 5 |
| iac | 1 | 2 [3] | 4 | 1 | 2 | 2 [4] | 0 | 1 | 2 [5] |
| nmcnr | 5 | 5 | 5 | 4 | 4 | 5 | 0 | 1 | 3 |
| smc | 4 [5] | 4 [5] | 4 [5] | 4 [5] | 4 [5] | 4 [5] | 2 | 4 [5] | 4 [5] |
| folded_cascode_ota | 5 | 5 | 5 | 5 | 5 | 5 | 2 | 3 [5] | 5 |
| ldo_regulator | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 | 5 |
| 3_stage_ring_osc | 5 | 5 | 5 | 5 | 5 | 5 | 3 [4] | 3 [4] | 4 [5] |
| three filters | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Pooled, computed by me from the same CSVs:

| Set | Budget | TPE | GP-BO | Random |
|---|---|---|---|---|
| 6 advanced amps (azc, dfcfc, fdgb, iac, nmcnr, smc), 30 runs | 50 | 24/30 (80%) | 20/30 (67%) | 13/30 (43%) |
| | 100 | 25/30 (83%) | 21/30 (70%) | 18/30 (60%) |
| | 300 | 27/30 (90%) | 26/30 (87%) | 21/30 (70%) |
| 9 non-filter circuits, 45 runs | 50 | 39/45 (87%) | 35/45 (78%) | 23/45 (51%) |
| | 100 | 40/45 (89%) | 36/45 (80%) | 29/45 (64%) |
| | 300 | 42/45 (93%) | 41/45 (91%) | 35/45 (78%) |

**Enumerated small circuits, replayed from ground truth, 20 seeds, 100 simulations, all-spec success %.** — [summary.txt](https://github.com/taha-mazher/analog-sizing-agent-audit/blob/main/results/summary.txt)

| Circuit | GP | Random | Random (no replacement) | TPE |
|---|---|---|---|---|
| five_trans_ota | 100 | 100 | 100 | 100 |
| current_mirror_ota | 100 | 100 | 100 | 100 |
| telescopic_ota | 100 | 100 | 100 | 100 |
| diode_load_amp | 80 | 50 | 30 | 90 |
| buffer | 95 | 80 | 65 | 100 |
| xor_gate | 100 | 65 | 55 | 100 |
| resistive_load_amp | 55 | 65 | 100 | 95 |
| inverter, nand_gate | 100 | 100 | 100 | 100 |

- telescopic_ota at smaller budgets (computed by me from `bench_runs.csv`, 20 seeds): at 10 simulations TPE 13/20, GP 13/20, random 11/20; at 25 simulations TPE 20/20, GP 20/20, random 17/20; at 50 and 100 all methods 20/20 (random without replacement 18/20 at 50). five_trans_ota and current_mirror_ota are 20/20 for every method from 10 simulations up. — [bench_runs.csv](https://github.com/taha-mazher/analog-sizing-agent-audit/blob/main/results/bench_runs.csv)
- **Ground-truth feasible share of the design space:** five_trans_ota 100%, current_mirror_ota 100%, dfcfc 45.6%, telescopic_ota 8.3%, azc 7.6%, fdgb 7.1%, ldo_regulator 6.9%, nand_gate 5.7%, folded_cascode_ota 2.4%, 3_stage_ring_osc 2.1%, buffer 2.0%, smc 1.4%, xor_gate 1.1%, iac 0.9%, diode_load_amp 0.7%, nmcnr 0.2%; none found for the three filters and the VCO. Small spaces were fully enumerated (81 to 6,561 designs); large ones use 2,000 random designs (200 for the VCO). — [audit README](https://github.com/taha-mazher/analog-sizing-agent-audit)

**The AutoSizer agent itself, rerun by the audit (released budget 100 designs, 3 variables, 3 regeneration cycles, patience 2).** — [summary.txt](https://github.com/taha-mazher/analog-sizing-agent-audit/blob/main/results/summary.txt)

| LLM | Trials completed | Claims success | Truly meets spec |
|---|---|---|---|
| Gemini 3.5 Flash | 1 of 1 (low-pass filter; 25 designs, 3 API calls, 149 s) | 1 | 0 |
| Qwen3-8B as released | 0 of 2 (6 attempts, all crashed) | 0 | 0 |
| Qwen3-8B, prompt template fixed | 17 of 18 | 17 | 8 |

Per trial for the three non-filter circuits (Qwen3-8B, fixed prompt): iac 42 / 36 / 89 designs, all truly pass; nmcnr 124 / crashed / 159, the two completed pass; smc 98 / 171 / 165, all pass. Filters: 9 of 9 claim success after 12 to 24 designs, 0 truly pass. — [summary.txt](https://github.com/taha-mazher/analog-sizing-agent-audit/blob/main/results/summary.txt)

**Optimisers at each agent trial's own design count (5 seeds, replayed from cache), % meeting every spec.** — [summary.txt](https://github.com/taha-mazher/analog-sizing-agent-audit/blob/main/results/summary.txt)

| Circuit, agent budget | GP | Random | TPE | Agent |
|---|---|---|---|---|
| iac, 42 | 0 | 0 | 20 | pass |
| iac, 36 | 0 | 0 | 20 | pass |
| iac, 89 | 20 | 20 | 40 | pass |
| nmcnr, 124 | 80 | 20 | 100 | pass |
| nmcnr, 159 | 80 | 40 | 100 | pass |
| smc, 98 | 80 | 80 | 80 | pass |
| smc, 171 | 80 | 80 | 80 | pass |
| smc, 165 | 80 | 80 | 80 | pass |
| All 8 budgets | 52 | 40 | 65 | 8 of 8 |

**Bugs and benchmark defects found by the audit.** — [audit README](https://github.com/taha-mazher/analog-sizing-agent-audit)
1. Spec parser drops negative thresholds. The regex `(\w+)\s*([><=]+)\s*([\d.]+)` cannot match a minus sign, so `lpf_passband_gain_db > -5`, `hpf_passband_gain_db > -15` and `bpf_peak_gain_db > -10` are skipped silently. AutoSizer's check passes 36.4 / 38.8 / 53.6% of 2,000 random filter designs whose true pass rate is 0%. All 45 filter optimiser runs pass the check and none meets the real spec. (The regex is confirmed at line 1185 of the local clone.)
2. FoM maximisation picks infeasible designs: in 13 of 180 runs the optimiser had seen a fully feasible design but reported a higher-FoM infeasible one.
3. Budget overshoot: in 4 of 8 completed non-filter agent trials the agent used 124 to 171 designs against a budget of 100, "since the budget is only checked between optimisation rounds". (Consistent with the loop condition `while (len(optimizer.all_searched_designs) < total_designs and not specs_met and not fom_converged)` at line 2278 of `llm_guided_ota_optimization_test.py` in the local clone.)
4. Two circuits are passed by every design (five_trans_ota, current_mirror_ota), and two have fewer designs than the 100-simulation budget (inverter 81, resistive_load_amp 96).
5. The circuit-analysis prompt shows the LLM an invalid JSON template (`json.dumps(...)[1:-1]` strips the braces); Qwen3-8B crashed in all 6 attempts until the two braces were restored.
6. Only 22 of 24 circuits run: `bandgap_reference` needs a pnp model file the release lacks, and `switched_capacitor` fails in ngspice.

**Audit setup.** AutoSizer's own runner, ngspice 41, SKY130 models bundled with AnalogGym-Opt, about 1 s per simulation; Optuna 5 for TPE, scikit-optimize for GP-BO. "True" scoring = every spec in `user_specs_metric`, using AutoSizer's auto-generated normalised FoM for the `fom` term. — [audit README](https://github.com/taha-mazher/analog-sizing-agent-audit)

**Audit's own caveats.** Three agent trials per circuit "is a pattern, not a rate"; Gemini 2.5 Flash is no longer served to new API keys, so cloud runs use Gemini 3.5 Flash with a 20-requests-per-day free tier; random sampling cannot prove the filters infeasible (best of 2,000: passband gain -7.4 / -21.8 / -22.1 dB against -5 / -15 / -10 dB). — [audit README](https://github.com/taha-mazher/analog-sizing-agent-audit)

### Inferences
- **Where a new method can visibly win, among the ten local circuits:**
  - **iac** is the only one with large headroom: TPE 1/5 at 50, 2/5 at 100, 4/5 at 300; GP 2/5 at 100 and 300. The audit's AutoSizer-with-Qwen runs passed 3/3 in 36 to 89 designs.
  - **azc** and **nmcnr** separate methods at 50-100 simulations against GP and random (azc GP 1/5 at 100; nmcnr random 1/5 at 100), but TPE is already 5/5 at 50.
  - **smc** and **fdgb**: TPE 4/5; one seed of headroom, within noise at 3-5 seeds.
  - **folded_cascode_ota**: TPE and GP 5/5 at 50; only random is beatable.
  - **telescopic_ota**: only informative at 10 simulations or fewer (TPE 13/20).
  - **five_trans_ota, current_mirror_ota, dfcfc**: no headroom; every method including random passes. Report them, but do not count them as evidence.
- On azc and iac the "best-FoM design is infeasible although a feasible one was seen" failure is most frequent (azc GP at 100: 1 true vs 5 any-feasible). Selecting the final design as "feasible first, then FoM" would by itself lift a GP baseline from 1/5 to 5/5 on azc. A fair comparison should apply the same feasible-first selection to the baseline, or report both.
- The audit numbers are with ngspice 41 and AnalogGym-Opt's SKY130 models. A different ngspice version or model file can move a borderline design across a threshold, so the team should rerun TPE and random on its own machine and treat the audit numbers as a sanity check on that rerun, not as the comparison itself.
- The audit's AutoSizer agent numbers use Qwen3-8B with a patched prompt, not the paper's Gemini 2.5 Flash. They are not "AutoSizer's published result".

### Gaps
- No audit data for telescopic_ota, five_trans_ota or current_mirror_ota at 300 simulations (the replay stops at 100), and no live TPE/GP runs for the Easy-tier circuits beyond the replay.
- No audited AutoSizer-agent trials on azc, fdgb, dfcfc, folded_cascode_ota or the simple OTAs.
- The audit has not been peer reviewed; only the regex and the budget-loop condition were checked against the local clone. The per-circuit counts were recomputed from its CSVs, not re-simulated.

## Q3. Other 2025-2026 LLM sizing agents: what is comparable to a laptop run on ngspice + SKY130

### Takeaway
Only three other works report numbers on SKY130 with ngspice: OPERA (six op-amps, 15 PVT corners, with a TPE/random comparison and a public netlist benchmark), AnalogScout (three OTAs, nine corners, 16k-simulation budget, no code yet) and White-Box Reasoning (one two-stage op-amp, single run). LEDRO, ADO-LLM, EEsizer and AgenticSizing were published on Spectre/HSPICE or non-SKY130 models, so their original numbers are not comparable; the only SKY130 numbers for LEDRO, EE-Sizer and ADO-LLM are the AutoSizer authors' re-implementations in Table 2.

### Cited Findings

| Work | PDK / simulator | Circuits | Budget, seeds | Corners | Headline result | Comparable on a laptop? |
|---|---|---|---|---|---|---|
| AutoSizer (2602.02849) | SKY130 / ngspice | 24 | 300 samples, 3 trials | TT | 100% SR all tiers | Yes, same circuits; see Q1-Q2 caveats |
| OPERA (2610.39395) | SKY130 / ngspice, Magic, Netgen, KLayout, gLayout | 5T, FC, telescopic OTAs; Miller, FC 2-stage, telescopic 2-stage | 500-run cap; 3 seeds (5 for 5T) for baselines; OPERA counted on stored sweeps | 15 (tt/ff/ss/fs/sf × 0/27/70 °C) | Table IV below | Partly: netlists public, different circuits and specs from AMS-SizingBench |
| AnalogScout (2610.37319) | SKY130 (also GF180) / ngspice | Miller 2-stage, folded cascode, 3-stage NMC OTAs | 16k simulations (1 sim = 1 testbench at 1 corner); 5 confirmation seeds | 9 | Table below | No: code not released, 16k budget |
| White-Box (2508.13172) | SKY130 / ngspice 42 | one two-stage op-amp | 5 LLM iterations at TT, a 6th for all corners; single run | TT, then PVT with relaxed per-corner targets | Meets specs at TT in 5 iterations | Weakly: one circuit, one run, no baseline optimiser |
| LEDRO (2411.12930) | PTM-MG FinFET 7/10/14/16 nm / Cadence Spectre | 22 op-amp topologies | up to 1200 evaluations (200 calibration + 100 × 10 iterations); baseline TuRBO 1200 steps | not checked | +13% FoM, 2.15× faster (low complexity); +48% FoM, 1.7× faster (high complexity) vs best baseline | No (original); only via AutoSizer's re-implementation |
| EEsizer (2509.25510) | 180 / 130 / 90 nm nodes (model source not checked) / ngspice | 6 basic circuits; one 20-transistor op-amp, 10 metrics | max 20 iterations; three test groups | not in loop | OpenAI o3 met target at 90 nm in all three groups | No (original); only via AutoSizer's re-implementation |
| ADO-LLM (2406.18770) | commercial 90 nm / HSPICE | two-stage differential amplifier, hysteresis comparator | not extracted | not checked | not extracted | No |
| AgenticSizing (2609.25873) | PDK not stated / Cadence Spectre | BGR, 2-stage FC, LDO, LDO+FC+BGR | 5 runs, wall-clock budget | none | 4/5, 4/5, 4/5, 3/5 (see earlier notes) | No |
| LLANA (2406.05250) | not checked | analog layout constraint generation, not transistor sizing | not checked | not checked | "comparable to SOTA BO" per abstract | No, different task |
| AnalogXpert (2412.19824) | not checked | topology synthesis, not sizing | — | — | — | No, different task |

Sources: [AutoSizer](https://arxiv.org/html/2602.02849v1), [OPERA](https://arxiv.org/html/2610.39395), [AnalogScout](https://arxiv.org/html/2610.37319), [White-Box](https://arxiv.org/html/2508.13172), [LEDRO](https://arxiv.org/html/2411.12930), [EEsizer](https://arxiv.org/html/2509.25510), [ADO-LLM](https://arxiv.org/html/2406.18770), [AgenticSizing](https://arxiv.org/html/2609.25873v1), [LLANA](https://arxiv.org/abs/2406.05250v3), [AnalogXpert](https://arxiv.org/abs/2412.19824)

**OPERA details.**
- Agent: Claude in a Linux sandbox; Opus 5.5 for the teaching phase, Sonnet 5.5 for the iterative loops; designer-written skill files; gm/ID lookup tables at five process corners. — [OPERA](https://arxiv.org/html/2610.39395)
- Targets: f_un ≥ 50 MHz, PM ≥ 60°, SR ≥ 25 V/µs into 1 pF, minimum DC gain 30 dB (5T), 50 dB (FC and telescopic OTAs), 60 dB (two-stage); VDD 1.8 V. All six extracted layouts meet every target at all 15 corners (f_un ≥ 53.7 MHz, PM ≥ 62.1°, SR ≥ 27.9 V/µs). — [OPERA](https://arxiv.org/html/2610.39395)
- Benchmark netlists: https://github.com/AMPICLab/AMPICBench (stated in the abstract; I did not open the repo). — [OPERA](https://arxiv.org/html/2610.39395)
- **Table IV, runs to first passing design: passing seeds (median ngspice runs).** Cost unit is ngspice runs; a candidate stops at its first failing corner; a pass means all 15 corners. Protocol A = spec plus OPERA's IDD; Protocol B adds worst saturation margin ≥ -50 mV (5T: ≥ 0). — [OPERA, Table IV](https://arxiv.org/html/2610.39395)

  | Circuit | OPERA runs A / B | Random A | TPE A | Random B | TPE B |
  |---|---|---|---|---|---|
  | 5T OTA | 54 / 54 | 5/5 (58) | 5/5 (48) | 5/5 (76) | 5/5 (48) |
  | FC OTA | 30 / 59 | 3/3 (250) | 3/3 (129) | 1/3 (368) | 1/3 (32) |
  | Telescopic OTA | 30 / 59 | 3/3 (35) | 3/3 (45) | 2/3 (258) | 2/3 (41) |
  | Miller | 30 / 53 | 3/3 (40) | 3/3 (204) | 3/3 (40) | 2/3 (181) |
  | FC 2-stage | 30 / 55 | 2/3 (281) | 3/3 (141) | 0/3 | 0/3 |
  | Telescopic 2-stage | 30 / 55 | 3/3 (217) | 3/3 (166) | 1/3 (352) | 0/3 |

- OPERA's own statement about AutoSizer: it "draws its optimizer from a pool that includes Bayesian optimization and simulates only the typical corner". Under Protocol A, TPE passes every circuit but "with margins down to -169 mV" of saturation. — [OPERA, Sec. V-C](https://arxiv.org/html/2610.39395)
- OPERA's remaining limit: at fs, 70 °C "the weakest cascode is 23 to 63 mV into triode in five designs, yet all targets are met". — [OPERA](https://arxiv.org/html/2610.39395)

**AnalogScout details (Table I, best full-PVT-feasible TT power in µW at an equal 16k-simulation budget, median; n = 5 searches, n = 3 for BO, LAS-style, ACP-style).** — [AnalogScout, Table I](https://arxiv.org/html/2610.37319)

| Method | Miller 2-stage | Folded cascode | 3-stage NMC |
|---|---|---|---|
| Start (hand design) | 451 | 414 | 2,809 |
| CMA-ES @16k | 124 | 163 | 1,561 |
| BO @16k | 142 | 172 | 1,955 |
| LAS-style (re-impl.) | 99.3 | 115 | – |
| ACP-style (re-impl.) | 145 | 168 | – |
| CMA-ES @32k | 107 | – | 1,549 |
| **AnalogScout @16k** | **61.4** | **98.1** | **944** |

- One simulation = one SPICE run of one testbench at one corner in ngspice. Nine corners: tt; ss, sf, fs at -10% VDD and ff at +10% VDD, each at -40 / 125 °C. Sign-off also requires ≥ 50 mV saturation margin at all corners and ≥ 95% mismatch Monte Carlo yield (N = 500). Method fixed on development seeds 1-3, reported on confirmation seeds 4-8. — [AnalogScout](https://arxiv.org/html/2610.37319)
- Code: "the code and open-PDK data will be released" (future tense; not available). — [AnalogScout](https://arxiv.org/html/2610.37319)

**White-Box Reasoning.** Ngspice + LLM API loop with gm/Id data; Gemini converges at TT in 5 iterations; the all-corner step uses "slightly relaxed, more physically realistic design targets for different process corners". Benchmarked against manual design, not against an optimiser. — [White-Box](https://arxiv.org/html/2508.13172)

**EEsizer success rule.** Success is declared only if all specification flags are true and no transistor is in sub-threshold (Vgs − Vth < 0). — [EEsizer](https://arxiv.org/html/2509.25510)

**LEDRO original setup.** LLaMa3-70B (temperature 0.8) plus TuRBO, ten iterations, Cadence Spectre, PTM-MG 7/10/14/16 nm; code at https://github.com/dimplekochar/LEDRO. — [LEDRO](https://arxiv.org/html/2411.12930)

### Inferences
- The two directly comparable public test sets on a laptop are AMS-SizingBench (TT only, the team already has it) and OPERA's AMPICBench (15 corners, ngspice, SKY130). OPERA's Table IV is the only published SKY130/ngspice table with a clean, bug-aware protocol (corner-counted runs, saturation margin, k/n seeds). If the team's pipeline does corners and saturation checks, OPERA Protocol B is the closest published bar, but it needs the AMPICBench netlists and testbenches, which I did not inspect.
- AnalogScout is the strongest corner-aware result but is not reproducible in a day: no code, 16k simulations per search, and its objective is minimum power, not success rate.
- LEDRO / EE-Sizer / ADO-LLM numbers in AutoSizer Table 2 are re-implementations by a competing paper's authors. Saying "we beat LEDRO" on the basis of that table means "we beat AutoSizer's re-implementation of LEDRO", and should be worded that way.

### Gaps
- AMPICBench repo contents, license and whether the 15-corner testbenches run out of the box: not checked.
- OPERA token cost and wall-clock: not extracted.
- ADO-LLM budget and results, LLANA and AnalogXpert details: not extracted (different tasks or closed simulators; low value for this comparison).
- EEsizer's transistor model source for 180/130/90 nm (PTM or other): not checked.
- Which LLM AnalogScout uses: not found in the text I read.
- EasySize, TopoSizing, SLIC, SABLE: not read.

## Q4. What a one-day project can and cannot claim

### Takeaway
A defensible claim is narrow and names its reference: "on N AMS-SizingBench circuits with the released spec strings, at ≤ 100 simulations and k seeds, our agent meets every spec (signed-number parser, feasible-first selection, hard budget) in X of Y runs, against Optuna TPE's X' of Y' rerun on the same machine; the audit reports TPE at 25/30 for the six advanced amplifiers at 100 simulations." It is not defensible to claim "we beat AutoSizer" from numbers alone, because the paper reports 100% by a flawed check at a different budget, on possibly different specs, with a model that is no longer served.

### Cited Findings
- AutoSizer's published success rate is 100% in every tier, so no method can exceed it on the paper's own metric. — [arXiv HTML 2602.02849v1, Table 2](https://arxiv.org/html/2602.02849v1)
- The paper's check overstates success (93-95% reported vs 68-70% true across 180 optimiser runs), entirely because of the three filter circuits. — [audit README](https://github.com/taha-mazher/analog-sizing-agent-audit)
- The audit calls 3 trials per circuit "a pattern, not a rate". — [audit README](https://github.com/taha-mazher/analog-sizing-agent-audit)
- Precedents for corner-aware counting: OPERA counts ngspice runs with early stop at the first failing corner and reports passing seeds with median runs; AnalogScout counts one testbench at one corner as one simulation. — [OPERA](https://arxiv.org/html/2610.39395), [AnalogScout](https://arxiv.org/html/2610.37319)
- OPERA shows that spec-only success hides saturation violations: TPE passes all six circuits on specs but with saturation margins down to -169 mV, and passes only 5 of 15 runs on the five larger circuits once a -50 mV margin is required. — [OPERA, Sec. V-C](https://arxiv.org/html/2610.39395)

### Inferences

**Claims that would hold up (each needs the stated evidence).**
1. "Higher verified success than plain TPE / GP-BO / random at an equal or smaller budget on the discriminating circuits." Evidence: same YAML spec strings, same variable grids, TPE and random rerun locally with the same simulator build, same budget count, ≥ 3 seeds (5 is better; the audit used 5), results as k/n per circuit. The circuits where this can show a real gap are iac (TPE 2/5 at 100), then azc and nmcnr against GP and random, then smc and fdgb (one seed of headroom).
2. "Fewer simulations to first fully feasible design." Evidence: median simulations to first feasible, failures counted as > budget, same counting for the baseline. Reference points: the audit's Qwen-AutoSizer reached feasibility on iac in 36 / 42 / 89 designs; TPE at those budgets passed 20 / 20 / 40% of seeds.
3. "A stricter success check than the benchmark's own." Evidence: unit tests showing signed thresholds are enforced, the final design is chosen feasible-first, the budget is enforced before each simulation, and an independent re-simulation of the reported design. This is a methodological claim and holds regardless of the success numbers.
4. "Corner-verified results, which AMS-SizingBench does not have." Evidence: the same designs re-simulated at SS/FF and V/T. Present it as an extension with no published reference on these circuits; OPERA's Table IV is the nearest published corner-aware table, on different circuits.
5. "Matches the published 100% on the Medium-tier OTAs and the Hard-tier amplifiers we ran, under a stricter check and a third of the paper's budget." This is accurate if the team gets k/k on its subset at 100 simulations, and it is worded as "matches", not "surpasses".

**Claims to avoid.**
- "We beat AutoSizer / surpass the ICML 2026 result." The paper's SR is 100%; its budget (300), specs (at least telescopic differs) and model (Gemini 2.5 Flash) cannot be matched today; no per-circuit numbers exist.
- "State of the art on AMS-SizingBench." The team runs 10 of 24 circuits; 3 of those 10 are trivial (five_trans_ota, current_mirror_ota 100% feasible; dfcfc 45.6% feasible) and telescopic_ota is solved by random search within 50 simulations.
- "We beat LEDRO / EE-Sizer / ADO-LLM." Only AutoSizer's tier-level re-implementation numbers exist on SKY130; the originals ran on Spectre/HSPICE with other PDKs.
- "Higher FoM than AutoSizer's 33.2 (Medium) / 60.2 (Hard)." Those are averages over tier circuits with different FoM scales, the tier includes circuits the team does not run, and the best-FoM design is sometimes infeasible (13 of 180 runs in the audit). Compare best *feasible* FoM against a locally rerun baseline instead.
- Any percentage from fewer than 3 seeds, or a success rate that pools trivial circuits with hard ones without showing the per-circuit split.
- "Fewer simulations than AutoSizer's 128.9" without noting that 128.9 is evaluations-to-best at a 300 budget averaged over 13 hard circuits, not total simulations on the team's subset.
- Corner or post-layout superiority over AutoSizer, since AutoSizer reports neither; say "we add corners", not "we beat them on corners".

**Minimum fair protocol for today.**
- Circuits: all ten, reported per circuit, with iac, azc, nmcnr, smc, fdgb and folded_cascode_ota marked as the discriminating set and five_trans_ota, current_mirror_ota, dfcfc, telescopic_ota marked as sanity checks.
- Specs: the released `user_specs_metric` strings, unedited (table in Q1). State that these are the release strings at commit `1b9b8f6`, not the strings printed in the paper.
- Search space: the YAML's variables and discrete grids. If the agent searches a continuous or wider space (for example its own gm/Id-and-L parameterisation), say so and give the baseline the same space.
- Budget: ≤ 100 simulations per run (the release default), hard-capped, every simulation counted including failed and corner runs. Also report results at 50 if time allows, since the baselines separate more there.
- Seeds: ≥ 3, ideally 5; report k/n and the per-seed simulation counts.
- Success: every term in the spec string met at TT by the design the method reports, re-simulated once independently. Report TT-only success separately from any all-corner success.
- Baselines: Optuna TPE and random at the same budget on the same machine; quote the audit's numbers next to them as an external check.
- Model: name the exact Gemini model and report tokens and wall-clock per run; the paper gives no cost figure, so cost is reported, not compared.

### Gaps
- No published number exists for any method on these ten circuits with corners; corner results cannot be compared to prior work.
- Whether the ICML camera-ready or an updated repo changes the spec strings or adds per-circuit results was not checked; if it does, the comparison target changes.
