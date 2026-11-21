# Ship a corner-verified sizing loop first

Build this first: a closed loop that takes a **vendor datasheet PDF plus a schematic image** and turns them into a spec card and a device-role map. **Gemini 4 Carbon** then proposes sizes for the AMPICBench two-stage diff-pair/class-A op-amp, and every candidate is judged by **ngspice at all 15 SKY130 PVT points (5 corners x 3 temperatures)**. Only designs that pass every spec at every corner, with all signal-path devices in saturation, count as passes. This loop is the MVP. The team's own tests today show the ground is ready. ngspice 47 and a trimmed SKY130 library (about 0.2 s per run instead of about 9 s) are installed. The AMPICBench testbench reproduces its published tt numbers (60.59 dB, 125.4 MHz, 31.5 deg against 60.61/126.0/31.5). A full 15-point sweep runs in **about 1.5 s on 16 cores**. The circuit misses a 60 deg phase-margin target at every corner (27-41 deg), so the optimizer has a real job from the first minute. The "5 Sigma" proof then comes almost for free. AMPICBench ships two sibling op-amps (folded-cascode and telescopic first stages) with the same knob names, published numbers, schematic PNGs and per-corner saturation data. With two TI datasheets the team never opens during development, they make a held-out set. Run the agent on it against a constrained Optuna TPE baseline and a single-shot Gemini baseline, all at the same simulation budget. Treat the Managed Agent independent verifier, the hand-drawn-photo input, slew-rate specs and voice as ordered stretch goals. Treat every hackathon rule as unverified: no public page for this event exists.

## The MVP is a 15-corner judge wrapped around one planner

The scope that fits 6.5 hours is **one topology family, one testbench and one hard verifier**. The family is the two-stage op-amp from AMPICBench. Its `_STB` testbenches measure loop gain by Tian double injection and report UGF, phase margin, DC gain and gain margin. Each circuit ships a `corners.json` for tt/ff/ss/fs/sf at -40/27/125 C, which lists the worst saturation margin and the devices in triode ([AMPICBench](https://github.com/AMPICLab/AMPICBench)). The team's local copy already runs, so the first engineering task is not "get SPICE working" but "make the netlist sizeable." Today every device shares one `Wn/Ln` and one `Wp/Lp`, which gives about 8 knobs (Wn, Ln, Wp, Lp, Ibias, Cc, Rz, a multiplier). Split the **signal-path devices into per-role parameters**: input pair, mirror load, tail, second-stage driver and second-stage sink each get their own W and L, plus Ibias, Cc, Rz and the stage-2 multiplier. That makes about 14 knobs. Freeze the `Bias_gen` and `Cascode_bias` subcircuits at their published sizes so the bias voltages stay valid. Matching comes for free, because both devices of the input pair or mirror read the same parameter.

The evaluator is a single Python function, `evaluate(params, spec) -> per-corner metrics`. It writes 15 decks against `sim/models/sky130_slim.lib.spice`, runs `ngspice -b` in a `ProcessPoolExecutor` with `num_threads=1`, and parses `stb_meas.txt` and `op_devices.txt`. It returns UGF, PM, DC loop gain, gain margin, core current and the per-device saturation margin `|vds| - |vdsat|` for each corner. The `@m.x...msky130_fd_pr__nfet_01v8[vds|vdsat|id|gm|gds]` readouts were confirmed today. Subprocess batch mode is the right driver. PySpice's last release (1.5, 2021) supports only "Ngspice up to version 34" ([PyPI PySpice](https://pypi.org/project/PySpice/)), and the same `ngspice -b` plus `@m.` readout pattern is what CircuitGenome's verification module uses ([CircuitGenome spice_sim](https://circuitgenome.readthedocs.io/en/latest/api/sizer/shared/spice_sim.html)). One local finding changes how results should be reported. In this PDK build the corner labelled **`sf` gives more nfet current than `tt` and `fs` gives less**, in both the slim and full libraries. Always sweep all five corners, and do not let any prompt or slide reason from corner names.

The verifier, not the LLM, decides success. The published failures here are specific, and each one maps to a guard. An independent audit of AutoSizer found that its success regex `(\w+)\s*([><=]+)\s*([\d.]+)` cannot match a minus sign. It silently skipped specs such as `> -5`, which inflated reported success from about 69% to 93-95% ([AutoSizer audit](https://github.com/taha-mazher/analog-sizing-agent-audit)). The same audit found FoM maximisation picking an infeasible design over a feasible one in 13 of 180 runs, and the agent overshooting its design budget in 4 of 8 trials because the budget was checked only between rounds ([audit](https://github.com/taha-mazher/analog-sizing-agent-audit)). SGAnalog reports "no answer" once scoring as the human reference, convergence with under 1% of the reference swing, and geometry below the PDK's minimum finger width ([SGAnalog](https://github.com/Li-Yueting/SGAnalog)). The MVP therefore has these rules:

- Specs use signed numeric parsing, with a unit test.
- Success means all specs x all 15 corners, recomputed in a fresh process.
- The final design is chosen feasible-first.
- The simulation budget is checked before every deck.
- A saturation margin of at least 50 mV applies to every signal-path device.
- Geometry is clamped to SKY130 minimums (L >= 0.15 um).
- A missing answer counts as a failure.

### One job per model, chosen for cost and the 20% criterion

The official framing is "Carbon plans and orchestrates, Hydrogen executes" ([Philipp Schmid roundup](https://www.philschmid.de/google-deepmind-roundup-nov-2026)), and Antigravity's Orchestrator mode pairs the two the same way ([Antigravity models](https://antigravity.google/docs/models?hl=sr)). That maps cleanly onto three jobs that judges can tell apart:

| Role | Model and setting | Input | Output | Calls per sizing run |
|---|---|---|---|---|
| Perception: datasheet reader | `gemini-4-hydrogen`, `thinking_level="low"` | Electrical-characteristics pages of the PDF | Spec records (param, min/typ/max, unit, conditions, page, row_text) as strict JSON | 1 per datasheet, cached |
| Designer: sizing planner | `gemini-4-carbon`, `thinking_level="medium"` | Spec card, role map, bounds, compact history, worst-corner metrics, triode list | 4 candidate parameter vectors plus a one-line rationale each, as JSON | about 8 |
| Classifier and gate | `gemini-decision-1` | Schematic PNG + netlist; later, the iteration history | `choice` role per device with probabilities; `noul` "likely infeasible, stop?" | 1 for roles, 1 per iteration for the gate |

Hydrogen is the only Gemini 4 model on the free tier. Carbon is $2/$10 per million input/output tokens at introductory pricing through Nov 30, and Decisions costs $0.10 per million input tokens with $0 output ([Pricing](https://aistudio.google.com/docs/pricing); [Decisions blog](https://blog.google/innovation-and-ai/technology/developers-tools/gemini-decisions-api/)). Gemini 4 rejects `minimal` thinking, so `low` is the cheapest setting ([Thinking docs](https://aistudio.google.com/docs/thinking)). Carbon ignores `temperature` ([Schmid](https://www.philschmid.de/google-deepmind-roundup-nov-2026)), so repeated runs differ only through the model's own nondeterminism and the random initial batch. The Decisions API returns no text, so it cannot propose W or L. It can, however, label every transistor in one call with calibrated probabilities, at about 150-250 ms latency ([Decisions blog](https://blog.google/innovation-and-ai/technology/developers-tools/gemini-decisions-api/); [Codelab](https://codelabs.developers.google.com/getting-started-with-gemini-decisions-api)). Use structured output (`response_format` with a Pydantic JSON schema) for both generative roles. Re-clamp numbers in Python, because `minLength`-style constraints are silently stripped and numeric bounds are not documented as enforced ([Structured output](https://aistudio.google.com/docs/structured-output)).

At this scale the API cost is small. A planner call carries about 4k input tokens; thinking output is unknown, so assume about 5k. That comes to roughly **$0.06 per Carbon call**. A full evaluation of about 100 Carbon calls costs single-digit dollars. This is an estimate, so log `usage` on every call. Carbon has no free tier, so **confirm a billing-enabled key or credits in the first 15 minutes**. Without one, the "two Gemini 4 models" criterion collapses to Hydrogen alone.

## Multimodal inputs must change the numbers, not decorate the demo

Multimodality is central only if removing it breaks the pipeline, so both inputs should feed directly into the optimization.

**The datasheet PDF sets the targets.** The best targets are TI's 1.8 V-capable CMOS parts in the two-stage range. TLV9001 is 1 MHz, 60 uA typ / 77 uA max, 78 deg PM. TLV6001 is 1 MHz, 75 uA, 65 deg. TLV9041 is 350 kHz, 10 uA, 65 deg at 10 pF. All three PDFs were verified to download from TI ([TLV9001](https://www.ti.com/lit/ds/symlink/tlv9001.pdf), [TLV6001](https://www.ti.com/lit/ds/symlink/tlv6001.pdf), [TLV9041](https://www.ti.com/lit/ds/symlink/tlv9041.pdf)). Datasheets make extraction hard in specific ways. TLV9001 lists open-loop gain under four condition rows (100, 115 and 130 dB) that `pdftotext` separates from their values. TLV9041's table-wide conditions ("G = 10 V/V, RF = 180 kOhm ... RL = 100 kOhm") sit in a header sentence, not in the rows ([TLV9001](https://www.ti.com/lit/ds/symlink/tlv9001.pdf); [TLV9041](https://www.ti.com/lit/ds/symlink/tlv9041.pdf)). Have Hydrogen read only the pre-split electrical-characteristics pages, at about 560 tokens per page at default resolution ([Document processing](https://aistudio.google.com/docs/document-processing)). Then cross-check every returned number against the page's text layer and flag any value that is absent as a hallucination.

A **deterministic translation table**, shown on screen, turns the extracted records into SKY130 targets:

- **Kept:** GBW (as loop-gain UGF), phase margin, and the load capacitance named in the PM condition.
- **Scaled:** quiescent current, as core current (tail plus second-stage sink `id`, excluding the shared bias generator), and open-loop gain, to a reachable floor of about 70 dB.
- **Dropped:** rail-to-rail input, offset, noise and EMI.

Each change prints a stated reason. The translation logic is code, not prompt, and that is what lets the held-out evaluation run with specs the team did not write.

**The schematic image sets the knobs.** Gemini Decision 1 receives the PNG plus the netlist and answers one `choice` question per transistor (input pair / mirror load / tail / stage-2 driver / stage-2 sink / bias). A short script then rewrites each instance's `W={Wn*1e6}` into `W={W_<role>*1e6}`. That role map *is* the per-role parameterization, so a wrong label produces a wrong design space, which is easy to measure. The literature supports giving the image this narrow job rather than asking it to read the whole netlist. NEMESIS reports that schematics resolve port roles and mirrored branches the netlist leaves unclear ([arXiv 2607.05657](https://arxiv.org/abs/2607.05657)). Full transcription remains unreliable: the best model reaches only 56.1% exact isomorphism on SGAnalog ([SGAnalog](https://github.com/Li-Yueting/SGAnalog)), and GPT-4o "struggles to connect" components it detects ([Auto-SPICE](https://arxiv.org/abs/2411.14299v1)). SGAnalog's finding that "sizing is text-only" ([SGAnalog](https://github.com/Li-Yueting/SGAnalog)) argues against pretending the image improves the sizing itself. Claim role accuracy, and measure it. AMPICBench's published PNG and SVG schematics ([AMPICBench](https://github.com/AMPICLab/AMPICBench)) are the development inputs. A team member's hand-drawn photo is a stretch input that shows "camera" use.

## Held-out proof: two unseen op-amps, two unseen datasheets, three methods

The held-out set is mostly already on disk. AMPICBench's `Opamp_2stg_FC_ClassA_STB` and `Opamp_2stg_Telescopic_ClassA_STB` use the same parameter names (`Wn, Ln_, Wp, Lp, Ibias, Cc, Rz`, a stage multiplier) as the development circuit, so the evaluator and the parameterization script carry over unchanged. Their published tt numbers are 73.9 dB / 71.3 MHz / 32.0 deg (folded cascode) and 81.9 dB / 68.7 MHz / 49.3 deg (telescopic) ([AMPICBench](https://github.com/AMPICLab/AMPICBench)). A local read of the shipped `corners.json` files shows the problem is real at every corner. PM spans **28-41 deg (folded cascode) and 45-59 deg (telescopic)**, and devices sit in triode at 7 and 8 of the 15 PVT points respectively. These circuits fail a 60 deg PM target and a saturation check before any agent touches them. Cross the two circuits with two datasheets that are not opened during development (TLV6001 and TLV9041; TLV9001 is the development part). That gives **four held-out (circuit, datasheet) tasks** whose topology, starting sizes, schematic images and spec numbers all came from third parties.

Three methods run at an identical budget of **40 candidates x 15 PVT points = 600 simulations per run**, with every deck counted.

1. **The agent**: Carbon proposes 4 candidates per iteration for about 10 iterations. The first batch is 4 random draws, so the planner does not start from nothing.
2. **Optuna TPE** over the same bounds, with normalized spec slacks returned through `constraints_func`. A trial is feasible when all values are <= 0; return large positive slacks on simulator failure instead of raising ([Optuna GPSampler docs](https://optuna.readthedocs.io/en/v4.5.0/reference/samplers/generated/optuna.samplers.GPSampler.html)).
3. **A single-shot baseline**: one Carbon call proposes sizes from the same inputs, followed by one 15-point verification.

The published AMPICBench sizing is a fourth, zero-cost reference row. If time allows, run TPE again at 3x budget to show the agent is not merely beating a starved baseline. The audit's reference point is plain TPE meeting every spec in 67% of runs at 100 simulations and 70% at 300 on hard AMS-SizingBench circuits (TT only) ([audit](https://github.com/taha-mazher/analog-sizing-agent-audit)). If TPE does far worse here, the slack normalization is probably wrong.

**Freeze before the first held-out run.** Tag the commit, hash the prompts, and report both versions if anything changes afterwards. Report these numbers:

- verified 15-corner success as k/n (not percentages alone);
- TT-only success, to show the corner gap;
- median simulations to the first all-corner-feasible design, with failures counted as greater than the budget;
- the best feasible core current;
- Carbon calls, tokens and dollars;
- wall-clock time;
- how often "agent claimed pass" survives independent re-verification.

The re-verification should use the **full `sky130.lib.spice`**, not the slim library. At about 9 s x 15 points per final design, this costs a few minutes in total, and it shows the speed trick did not change the answer. With 2 circuits x 2 datasheets x 3 methods x 3 seeds = 36 runs, simulation alone is about 36 x 40 x 1.5 s, or 36 minutes. Agent runs add Carbon latency, so start held-out runs in the background by hour 4.

Two smaller held-out measurements test the multimodal claims directly:

- **Extraction accuracy.** Compare Hydrogen's output on the held-out datasheets with the vendor's own numbers in the PDF, exact match per field, using a ground truth built by hand from the text layer.
- **Role-labeling accuracy.** Compare Decisions labels on the folded-cascode and telescopic PNGs with an answer key written before the run, and report the mean probability on wrong labels to show whether the calibration holds.

Neither is a published benchmark result, and both are honest. AMS-SizingBench is stronger external evidence, with 24 SKY130 circuits, explicit YAML specs and published BO numbers ([AutoSizer arXiv](https://arxiv.org/html/2602.02849v1); [repo](https://github.com/yuxi120407/AutoSizer)). It is a stretch tier because it needs its own model bundle, fixes L, and the repository has no license.

## Hour-by-hour build order with three cut lines

T+0 is when building starts. Two people can run the "sim" and "LLM" columns in parallel. A solo builder should follow the rows in order.

| Time | Sim and evaluation track | LLM and multimodal track |
|---|---|---|
| 0:00-0:15 | Per-role `.param` split of the diff/class-A netlist; bias subcircuits frozen | Key check: one Carbon call, one `client.decisions.create` call (v1beta), log `usage`. Kick off one cheap Managed Agent probe (`max_total_tokens` of about 20k) asking it to try `apt-get install ngspice` |
| 0:15-1:00 | `evaluate()` with ProcessPool over 15 PVT points, parse stb plus device op-points plus core current; regression test against 60.59 dB / 125.4 MHz / 31.5 deg | Spec record schema; split TLV9001 electrical-characteristics pages; Hydrogen extraction plus text-layer cross-check |
| 1:00-1:45 | Verifier with all guards; budget counter; Optuna TPE baseline running on the TLV9001 spec (also a feasibility probe) | Translation table; Decisions role-label call on the AMPICBench PNG; role-to-parameter rewrite script |
| 1:45-3:00 | Feasibility decision: if TPE finds nothing feasible for the TLV9001 card, switch the hero card to TLV6001 or relax CL and say why | Carbon planner prompt plus JSON schema; loop of 4 candidates per iteration; Decisions stop gate |
| **3:00 CUT LINE A (MVP)** | **Loop closes on the dev circuit: PDF + PNG in, a design verified at all 15 corners out, with a before/after corner table. Demoable even if everything below is cut.** | |
| 3:00-3:30 | Single-shot baseline; parameterize FC and telescopic with the same script | Freeze: git tag, prompt hashes |
| 3:30-4:45 | Held-out matrix running in background (36 runs; drop to 2 seeds if behind) | Extraction and role-label accuracy on held-out PDFs and PNGs; full-library re-verification of final designs |
| **4:45 CUT LINE B (Proof It Works)** | **Held-out table complete: agent vs TPE vs single-shot, verified k/n, sims to feasible, $ per run** | |
| 4:45-5:45 | Corner-fan Bode plot (15 curves, before vs after, pass band shaded); generated "TLV9001 vs SKY130 worst-corner" spec sheet | Highlighted datasheet rows (pdfplumber coordinates from `row_text`); role-colored schematic |
| 5:45-6:30 | **CUT LINE C:** record a full fallback run on video; rehearse | README with rules caveats and license note |

The stretch goals are listed in priority order, and each one starts only after its cut line is green.

1. **Managed Agent independent verifier.** This targets the $5k prize. Give the Antigravity agent (`antigravity-preview-09-2026`, model `gemini-4-hydrogen`, `max_total_tokens` set) the final netlist and the spec card. Have it write its own testbench, re-simulate, and return a sign-off report. Hosted sandboxes provide Python 3.12 and `pip install`, but apt is not mentioned ([Environments](https://aistudio.google.com/docs/agent-environment)). The agent accepts only text and image input, not PDF ([Antigravity agent docs](https://aistudio.google.com/docs/antigravity-agent)). If the 0:00 probe shows no ngspice, use the newer self-hosted or local mode, where the agent's bash runs on the laptop that already has ngspice ([Schmid](https://www.philschmid.de/google-deepmind-roundup-nov-2026)). The SKY130 model files that the slim library includes from `~/.ciel/.../combined/continuous` must ship too: inline sources cap at 2 MB total, so use a repository or GCS source.
2. **Hand-drawn photo** of the op-amp as the role-labeling input, scored against the same answer key.
3. **Unity-gain slew-rate transient bench**, which unlocks datasheet SR specs and an honest "infeasible" demo moment. TLV9051's 15 V/us into 130 pF needs about 2 mA against a 330 uA IQ ([TLV9051](https://www.ti.com/lit/ds/symlink/tlv9051.pdf)).
4. **Decisions `score` pre-screen** of Carbon's candidates before simulation, reported as an ablation.
5. **AMS-SizingBench held-out tier.**
6. **Voice narration.**

Skip GF180 migration and gm/Id lookup tables today. Both cost an hour or more and do not move any criterion.

The demo should follow one linear story, not a dashboard:

1. A TI PDF page appears with the extracted rows highlighted.
2. The schematic follows, colored by Decisions role labels with their probabilities.
3. Four or five Carbon iterations scroll by, each showing worst corner, triode devices and rationale.
4. The corner fan moves from 27-41 deg to at least 60 deg, re-verified on the full PDK.
5. The held-out table follows, including the runs where TPE won or everything failed.

## What could still break the plan

The largest risk is the rules themselves. No public page exists for a Gemini 4 NYC hackathon on this date. The rubric weights, the "two Gemini 4 models plus Decisions" criterion, the dashboard ban, the held-out-input rule and the $5k Managed Agents prize all come from the brief, not from a verifiable source. Searches turned up only other NYC events, such as the Dec 8 Agents for Impact hackathon ([Agents for Impact NYC](https://cloud.google.com/events/agents-for-impact-nyc)) and a past Betaworks agentic-economy event ([Luma](https://luma.com/1ofx14ov)). Confirm with organizers whether `gemini-decision-1` counts toward "two Gemini 4 models" (this plan assumes it does not, hence Hydrogen plus Carbon), and whether AMPICBench circuits count as "inputs the team did not prepare."

Several facts remain unverified:

- **Decisions response fields** (`.choice`, `.probabilities`, `.noul`) come from a codelab, not an SDK reference ([Codelab](https://codelabs.developers.google.com/getting-started-with-gemini-decisions-api)).
- **Gemini 4 rate limits** are unpublished except for Decisions ([Rate limits](https://aistudio.google.com/docs/rate-limits)).
- **Spec feasibility** is unknown. Whether the AMPICBench topologies can reach a datasheet-derived target (for example 1 MHz into a large CL within a scaled current budget) is untested, which is why TPE doubles as a feasibility probe at 1:45.

AMPICBench is CC BY-NC 4.0, which is fine for a hackathon demo but must be stated in any repository release ([AMPICBench](https://github.com/AMPICLab/AMPICBench)).

## Conclusion

The fair claim is that an LLM planner reaches verified all-corner feasibility in fewer simulations than TPE on circuits and specs it never saw, and that it explains each move. A claim that LLMs beat Bayesian optimization would rest on weaker evidence: the only independent rerun found shows plain TPE beating a published LLM agent once the agent's success check is fixed ([audit](https://github.com/taha-mazher/analog-sizing-agent-audit)). A lost race is still a credible result if it is reported honestly. The setup is unusual in that one PVT sweep costs about 1.5 s, so the budget limit is Gemini calls, not simulation. Every design decision above follows from that: few, rich Carbon calls; cheap calibrated Decisions labels; and brute-force verification on every candidate. The team should spend its scarce hours on the verifier and the held-out table, not on making the agent clever.
