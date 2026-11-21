# Swarm coordination strategies for LLM-driven black-box optimization (analog sizing swarm)

Research date: 2026-11-21. Time-boxed (about 20 tool calls). Source depth is marked on each finding:
**[full]** = I fetched the paper HTML and the numbers come from its tables/text;
**[abstract]** = only the arXiv abstract page was read;
**[snippet]** = only a search-result summary was seen, the page itself was not opened (treat as unverified).
Statements about the team's code come from reading `/home/thinkies/NewGit/gemini-4-hackathon/sizing/swarm.py` only; `sizing/opamp2stg.py` and `sizing/baseline.py` were NOT read, so anything that depends on them is flagged.

## Objective: ranked list of coordination techniques, evidence, and a recipe for this swarm

### Takeaway
The best-evidenced change is to stop letting LLM workers pick every point: let the architect set a search box and let a numeric optimizer fill part of each batch (ADO-LLM, AutoSizer, LEDRO, HOLLM all measure gains from this). For the specific failure (one saturation constraint never satisfied), the highest-fit changes are a measured one-knob sensitivity table for that constraint and showing workers the candidate that is best on that constraint, which the current top-3-by-summed-violation ranking can hide. Debate, voting and extra roles have no measured support for this kind of task.

### Cited Findings
Ranked by (measured evidence) x (fit to the observed failure) x (fits in one hour). Evidence strength is my grading.

| # | Technique | Evidence strength | Key number | Source |
|---|---|---|---|---|
| 1 | Hybrid batch: LLM proposes some points, GP/TPE proposes the rest from the same history | Strong, same domain, budget 105 | Only the hybrid met all specs on 2 circuits; GP-BO and LLM-only each missed 1-2 specs | [ADO-LLM](https://arxiv.org/html/2406.18770v1) [full] |
| 2 | LLM outer loop refines the search space (narrow, widen, freeze/unfreeze knobs), expanding on infeasibility or stagnation | Strong, same PDK (SKY130), budget 300 | Hard circuits: 100% success vs LEDRO 80%, BO 50%, TuRBO 25%; removing the reflection loop: 100% -> 50% | [AutoSizer](https://arxiv.org/html/2602.02849v1) [full] |
| 3 | Sensitivity information per knob given to the planner | Medium (LLM-inferred sensitivities, not finite differences) | Removing the circuit-understanding agent: success 100% -> 73.4% | [AutoSizer](https://arxiv.org/html/2602.02849v1) [full] |
| 4 | Local rather than global LLM sampling, several regions per round | Medium (synthetic, HPO, NAS; no numeric tables in text) | 1 region per round "hindered exploration"; 3-7 regions better; 1 proposal per region degraded | [HOLLM](https://arxiv.org/html/2505.21372v1) [full] |
| 5 | Diverse parent sampling from an archive instead of always the best | Medium (program evolution, qualitative ablation) | "Weighted sampling consistently outperforms both random search and hill climbing" | [ShinkaEvolve](https://arxiv.org/html/2509.19349v1) [full, no ablation numbers in text] |
| 6 | Novelty rejection of near-duplicate proposals before evaluation | Medium (same caveat) | "substantial performance gains over no rejection sampling" | [ShinkaEvolve](https://arxiv.org/html/2509.19349v1) |
| 7 | Curated in-context examples (top-k rather than random or none) | Medium, same domain | LLM-only on the amplifier: 3 missed specs with no examples, 2 with random 5, 1 with top 5 | [ADO-LLM](https://arxiv.org/html/2406.18770v1) [full] |
| 8 | Fast + strong model ensemble, bandit model selection | Weak for this use | ShinkaEvolve text is inconsistent on the size of the bandit gain (see below) | [ShinkaEvolve](https://arxiv.org/html/2509.19349v1) |
| 9 | Multi-agent debate / critique rounds | Negative | Debate "often fail[s] to outperform" CoT and self-consistency at higher compute | [Zhang et al. 2502.08788](https://arxiv.org/abs/2502.08788) [snippet] |

### Inferences
Recipes, in the order I would implement them. Line references are to `sizing/swarm.py`. All of these are my proposals, not something a source prescribes for this code.

1. **Per-constraint elites in the prompts (about 15 minutes).** `best(3)` (line 439) ranks failing designs by `score()` = sum of positive constraint violations (line 254-259). A candidate with the best margin on the never-satisfied saturation role can rank outside the top 3 if it is worse on PM or gain, so no worker ever sees its parameters. The failure census (line 383) reports the best margin reached per role but not which candidate reached it. Change: in `failure_census` also record the `Entry` that achieved the best worst-case margin for each `never` role, and append it to both prompts as "BEST ON THE BINDING CONSTRAINT: #n {params} -> eval_text". This is the MAP-Elites idea (keep the best per niche, here per constraint) reduced to one extra parent. Check first, by reading `history.jsonl` of a held-out run, whether the best-on-binding candidate was in fact outside the top 3; if it was always inside, this change does nothing.

2. **Sensitivity probe as a tool (about 30-40 minutes).** When the census reports a `never` role for two consecutive rounds, run a one-knob-at-a-time sweep around the best-on-binding candidate (each knob x1.3 and /1.3, clamped) and give the architect a table: knob, change in binding margin (mV), change in PM, UGF, gain, current. Cost: 2 x (number of knobs) evaluations. The budget object is in decks (`oa.Budget(candidates * len(oa.PVT15))`, line 368), so if `oa.evaluate` can run a subset of corners (NOT verified, `opamp2stg.py` unread), probe only at the single corner where the binding margin is worst: 2n decks is roughly 1.5-2 candidates' worth for 12-14 knobs, instead of 24-28 full candidates. If single-corner evaluation is not available, sweep only the knobs the architect names in `binding_constraint`/`knobs` (4-5 knobs, 8-10 candidates). Report the probe cost in decks so the comparison with Optuna stays honest; the module docstring says budgets are counted in candidates identically to the baselines, and probes break that equivalence unless decks are the reported unit.

3. **Architect emits a box; a numeric sampler fills 2 of every 8 slots (about 40-60 minutes).** Add to `architect_schema` an optional per-knob `[lo, hi]` and a `freeze` list. Each round, replay the whole history into an Optuna study (`study.add_trial`), restrict distributions to the architect's box, and `ask()` 2 candidates; tag them `worker="tpe"`. ADO-LLM used 1 LLM point to 4 GP points per batch of 5; AutoSizer used 100 optimizer samples per LLM reflection. With only 40-100 candidates I would start nearer 1 numeric to 3 LLM and move toward more numeric points if the `tpe` worker produces the round's best candidate more often than the LLM workers (a two-arm bandit over proposers, as ShinkaEvolve does over models). The team's own baseline result (GP-BO 1/3, TPE 0/3 on the development circuit) says a global numeric optimizer alone is weak at this budget, which is consistent with ADO-LLM's and AutoSizer's baselines; the box is what should make it useful.

4. **Feasibility-first score while a constraint is unsatisfied (about 10 minutes).** While the census reports a `never` role, rank by (violation of that role, then total violation) instead of total violation. This makes the top-3, the stall detector (line 675) and the critic's choice of design all track progress on the binding constraint. I found no paper that isolates this, so it rests on reasoning only.

5. **Different parents per worker, plus a near-duplicate filter (about 15 minutes).** All workers currently receive the same top 3 and the instruction "Start from the best design" (line 621). Give worker i the i-th ranked elite as its primary parent (with the per-constraint elite from item 1 always included), and reject a proposal whose every knob is within about 3% (log scale) of an evaluated design; the present filter only removes exact duplicates (rounding to 1e-12, line 660). Ask the worker for 3 candidates and keep the 2 farthest from history, which costs no extra LLM latency.

6. **Point the vision reviewer at the binding constraint, or switch it off on these circuits (about 15 minutes).** The reviewer prompt (line 529-536) covers phase margin, crossover and peaking only. On the held-out circuits the blocker is a DC saturation margin, which a Bode plot does not show. Either render a per-role, per-corner saturation-margin heatmap for it, or skip the call when the census names a `sat:` constraint.

Do not spend the hour on: debate or voting between workers, more roles, or bandit model selection.

### Gaps
- None of the cited papers reports a run at 40-100 full-PVT candidates with a success-rate metric; the closest are ADO-LLM (105 evaluations, apparently single runs, no seed count extracted) and AutoSizer (300 samples, 3 trials).
- I did not verify whether `oa.evaluate` accepts a corner subset, how `constraint_values` normalizes the saturation terms against PM/UGF/gain, or how many knobs the held-out circuits have. Recipes 1, 2 and 4 depend on these.
- Whether the held-out saturation constraint is reachable at all within the knob ranges is unknown; the sensitivity table is the cheapest way to find out (if no knob moves the margin in the right direction at the worst corner, the frozen bias network is the likely cause and no coordination change will help).

## What AlphaEvolve / FunSearch / OpenEvolve-style systems do, and what transfers to numeric search with 40-100 evaluations

### Takeaway
These systems coordinate through a program database (islands plus a MAP-Elites-style archive), a prompt sampler that mixes a parent with "inspiration" programs, and a fast-plus-strong model ensemble. The parts that transfer at 40-100 evaluations are archive-based parent diversity, novelty rejection and a periodic shared summary; islands with migration and thousand-sample evolution do not.

### Cited Findings
- DeepMind describes the ensemble as Gemini Flash to maximize "the breadth of ideas explored" and Gemini Pro to provide "critical depth with insightful suggestions" — [DeepMind blog, May 2025](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) [snippet]
- The white paper is Novikov et al., "AlphaEvolve: A coding agent for scientific and algorithmic discovery", arXiv 2506.13131, 16 June 2025; the abstract describes "an autonomous pipeline of LLMs" with an evolutionary approach and evaluator feedback, and does not mention ablations — [arXiv 2506.13131](https://arxiv.org/abs/2506.13131) [abstract]
- Secondary summaries say the program database is inspired by MAP-Elites and island models, that the prompt sampler selects parents and inspirations, and that the ablation (tensor decomposition and kissing number tasks) found each of evolution, rich prompt context, meta-prompts, full-file evolution and strong LLMs contributes, with "small base LLM only" degrading results. I did not open the paper PDF, and one write-up notes the exact combination of MAP-Elites and islands is not published. Secondary sources also disagree on whether the models were Gemini 2.0 or 2.5 — [gonzoml write-up](https://gonzoml.substack.com/p/alphaevolve), [arxiviq write-up](https://arxiviq.substack.com/p/alphaevolve) [snippet, unverified]
- ShinkaEvolve (Sakana AI, ICLR 2026 poster) reports a state-of-the-art circle packing result in 150 generations. Parent weight = sigmoid(performance relative to the median) x 1/(1 + number of offspring); islands: 2-4; archive size 20-50; novelty rejection at embedding cosine similarity above 0.95; UCB1 bandit over LLMs; a "meta-scratchpad" summary every 5-10 generations with up to 5 recommendations — [ShinkaEvolve](https://arxiv.org/html/2509.19349v1) [full]
- ShinkaEvolve ablation takeaways (figure only, no numbers in text): weighted parent sampling beats random search and hill climbing; embedding-based rejection gives "substantial" gains; the LLM novelty judge adds only marginal gains over embedding filtering — [ShinkaEvolve](https://arxiv.org/html/2509.19349v1)
- The same paper is inconsistent on the bandit ensemble: the ablation text says it "slightly improves" over a fixed uniform ensemble, the takeaways say it "significantly outperforms" single-LLM and fixed-ensemble approaches — [ShinkaEvolve](https://arxiv.org/html/2509.19349v1) [conflict noted via search summary plus fetch]
- ShinkaEvolve's sample-efficiency figures are the authors' own; no independent replication was found — [Sakana blog](https://sakana.ai/shinka-evolve/) [snippet]

### Inferences
- The offspring-count penalty (1/(1+N)) is the cheapest transferable piece: a parent that has already been mutated many times without improvement gets shown less. In this swarm that means rotating which elite each worker starts from instead of all starting from #1.
- ShinkaEvolve's smallest configuration (2 islands, archive 20, 150 samples) is still above the team's budget; with 5-10 rounds there is no time for island divergence and migration to pay off. The team's per-worker directions already act as weak islands.
- These systems evolve code, where an LLM is the only practical mutation operator. For a 10-15 dimensional numeric vector a GP/TPE is a competent mutation operator too, which is why the hybrid literature (next section) is the more relevant evidence.

### Gaps
- No ablation numbers from the AlphaEvolve white paper itself; OpenEvolve and FunSearch were not searched separately.
- No source tests islands or MAP-Elites on numeric parameter vectors at under 100 evaluations.

## LLM + numeric-optimizer hybrids: does LLM-set region plus GP/TPE point selection beat pure LLM or pure BO, and at what budgets?

### Takeaway
Yes in every paper read, at budgets from 105 to 1,200 evaluations: the hybrid beat both pure BO and pure LLM proposals, and the measured failure mode of LLM-only search is exactly the team's (it meets most specs and leaves one violated). No paper tests a budget as small as 40-80 full-PVT candidates.

### Cited Findings
- ADO-LLM (GPT-3.5 Turbo, HSPICE): 5 LLM zero-shot initial points, then each iteration 1 LLM candidate (prompted with the top 5 points) plus 4 GP-BO candidates from qEI, 20 iterations, total 105 simulations — [ADO-LLM](https://arxiv.org/html/2406.18770v1) [full]
- ADO-LLM two-stage amplifier: hybrid at 105 evaluations met all 5 specs (FoM 3.52); GP-BO at 105 missed gain (27 dB vs 60); GP-BO at 405 missed power; the LLM-only agent at 105 missed power (FoM 0.20). Hysteresis comparator: hybrid missed 0; GP-BO at 105 missed 2; GP-BO at 405 missed 1; LLM-only missed 1 — [ADO-LLM](https://arxiv.org/html/2406.18770v1)
- ADO-LLM: LLM zero-shot initialization alone improved GP-BO (amplifier FoM 1.89 -> 2.20; comparator missed specs 2 -> 1) — [ADO-LLM](https://arxiv.org/html/2406.18770v1)
- The ADO-LLM comparator table repeats the amplifier's gain value (60.83), which may be an error in the paper — [ADO-LLM](https://arxiv.org/html/2406.18770v1)
- AutoSizer (ICML 2026 poster; 24 SKY130 circuits in AMS-SizingBench): inner loop is a numeric optimizer (LHS, GA, BO or simulated annealing), outer LLM loop refines the search space; total budget 300 samples, up to 3 outer loops of 100 samples — [AutoSizer](https://arxiv.org/html/2602.02849v1) [full]
- AutoSizer success rate, 3 trials, easy/medium/hard: AutoSizer 100/100/100%; LEDRO 100/80/80%; EE-Sizer (pure LLM agent) 100/80/56.4%; GA 93.3/73.3/50%; BO 80/73.3/50%; ADO-LLM 80/80/41%; TuRBO 39.8/39.8/25% — [AutoSizer](https://arxiv.org/html/2602.02849v1)
- AutoSizer hard circuits, evaluations to solution: AutoSizer 128.9 +/- 23.0; LEDRO 140.8; BO 151.3; EE-Sizer 170.8 — [AutoSizer](https://arxiv.org/html/2602.02849v1)
- AutoSizer ablation (5 circuits, 3 trials), success rate: full 100%; without circuit understanding 73.4%; without optimizer-selection ("OE") 50%; without self-reflection loop 50%; without understanding, search-space definition and reflection 20% — [AutoSizer](https://arxiv.org/html/2602.02849v1)
- AutoSizer has internal inconsistencies: the text claims a 100% -> 20% drop for removing reflection while the table shows 50%; Algorithm 1 stops at the first feasible design while the text describes continued refinement; there is no ablation that removes only the outer loop — [AutoSizer](https://arxiv.org/html/2602.02849v1)
- LEDRO (Llama3-70B + TuRBO, Cadence Spectre, 22 op-amps, 7-16 nm FinFET): 200 calibration samples, then 10 rounds in which the LLM outputs a refined region (not points) and TuRBO draws 100 samples in it; 1,200 simulations total — [LEDRO](https://arxiv.org/html/2411.12930v2) [full]
- LEDRO vs BO at 2,000 simulations: FoM +8 to +20% on low-complexity and +41 to +55% on high-complexity op-amps; steps to reach BO-2000's best FoM: 390-460 vs 680-1,040 (low complexity), 417-750 vs 875-1,217 (high complexity) — [LEDRO](https://arxiv.org/html/2411.12930v2)
- HOLLM (Gemini-1.5-Flash; later ICLR 2026 per search summary): KD-tree partitions scored by a bandit, LLM proposes 5 points inside each of 5 selected regions, 4 evaluated per iteration, 5 initial points + 100 iterations. It "consistently outperforms the global LLM baseline" on six synthetic functions and "always outperforms" it on NAS-Bench-201, where the global LLM "stagnates early"; no numeric tables in the text — [HOLLM](https://arxiv.org/html/2505.21372v1) [full]
- HOLLM ablation: replacing the LLM sampler with uniform or local-GP sampling inside the regions had the largest negative effect, i.e. the LLM adds value as the in-region sampler too — [HOLLM](https://arxiv.org/html/2505.21372v1)
- LLAMBO (ICLR 2024, GPT-3.5) reports better zero-shot warm-starting, surrogate quality and candidate sampling than standard BO, with a much larger compute footprint; a November 2025 reproduction with Llama 3.1 70B broadly confirms the claims and finds contextual warm-starting improves early regret and reduces run-to-run variance — [LLAMBO](https://arxiv.org/abs/2402.03921), [reproduction 2511.18891](https://arxiv.org/abs/2511.18891) [snippet]
- AnalogScout (submitted to ISCAS 2027; 3 SKY130 OTAs, all candidates verified across PVT): reuse of verified elite designs plus analytic gm/Id sizing of sub-blocks before global refinement; 43.3% lower power than CMA-ES at an equal 16k-simulation budget, and CMA-ES's final power reached after 7.0% of that budget — [AnalogScout 2610.37319](https://arxiv.org/abs/2610.37319) [abstract]
- BORA (IJCAI 2025) and LLINBO (2025) are further LLM+GP hybrids; no numbers were retrieved — [BORA](https://arxiv.org/abs/2501.16224), [LLINBO](https://arxiv.org/abs/2505.14756) [snippet]

### Inferences
- The two directions of hybridization disagree on who picks points: ADO-LLM and LEDRO let the numeric optimizer pick most points; HOLLM finds the LLM is the better in-region sampler. Both agree that the region must be local and that a pure global LLM proposer stalls. For this swarm, keeping LLM workers as the main proposers inside an architect-set box, plus a minority of numeric points, is consistent with both.
- AutoSizer's "compact initial space, expand only on infeasibility or stagnation" is the opposite of the team's situation (full ranges from round 1) and directly addresses a never-satisfied constraint: the architect should be allowed to say "unfreeze / widen knob X" or, if X is not a knob, set `likely_infeasible`.
- TuRBO alone performing worst in AutoSizer (25-40% success) at a 300 budget suggests a trust-region optimizer without a good starting region is a poor choice at 40-100 candidates; do not replace workers with it.

### Gaps
- No result at 40-100 evaluations with 15-corner evaluation. Budgets found: 105 (ADO-LLM), 300 (AutoSizer), 405 (HOLLM), 1,200 (LEDRO), 16k simulations (AnalogScout).
- "EVOLVE-style" hybrids were not identified; I do not know which paper the brief means.
- AutoSizer's remaining 25k characters (appendix) and its PVT handling were not read.

## Evidence on multi-agent patterns: which have measured gains and which are theatre

### Takeaway
Measured gains exist for reflection that changes the search space, for supplying domain understanding/sensitivities, for curated shared examples, and for diversity mechanisms. Debate and critique between agents have negative evidence at equal compute; role specialization is claimed but not quantified in what I could read.

### Cited Findings
- Reflection loop with authority to change the search space: removing it halves success (100% -> 50%) — [AutoSizer](https://arxiv.org/html/2602.02849v1) [full]
- Role specialization (a separate circuit-understanding agent that infers variables and sensitivities): removing it drops success 100% -> 73.4% and FoM by 19.2% — [AutoSizer](https://arxiv.org/html/2602.02849v1)
- AgenticSizing (planner plus role-specialized sizing agents, 8 circuits up to 60 variables) reports 60% success on an LDO in 83 iterations on average where classical optimizers found nothing feasible, and says topology understanding, knowledge infusion and specialization give "complementary benefits"; no ablation numbers on the abstract page — [AgenticSizing 2609.25873](https://arxiv.org/abs/2609.25873) [abstract]
- Shared memory of best designs: showing the top 5 rather than a random 5 or none reduced missed specs for an LLM-only sizer (1 vs 2 vs 3 on the amplifier; 1 vs 3 vs 2 on the comparator, where random examples were worse than none) — [ADO-LLM](https://arxiv.org/html/2406.18770v1) [full]
- Shared notebook: ShinkaEvolve appends a periodic meta-summary with recommendations to mutation prompts; I found no isolated ablation of it — [ShinkaEvolve](https://arxiv.org/html/2509.19349v1)
- Diversity enforcement: novelty rejection and novelty-weighted parent sampling both help in ShinkaEvolve (qualitative); in HOLLM, sampling 1 region per round hindered exploration and 1 proposal per region degraded performance — [ShinkaEvolve](https://arxiv.org/html/2509.19349v1), [HOLLM](https://arxiv.org/html/2505.21372v1)
- Exploration-exploitation scheduling: HOLLM anneals its exploration weight from 1.0 to 0.01 on a cosine schedule; effect of the maximum was task-dependent — [HOLLM](https://arxiv.org/html/2505.21372v1)
- Best-of-N selection before evaluation: HOLLM generates 25 candidates per iteration and evaluates the top 4 by LLM-predicted value; LLAMBO likewise uses the LLM as a surrogate to rank candidates — [HOLLM](https://arxiv.org/html/2505.21372v1), [LLAMBO](https://arxiv.org/abs/2402.03921)
- Debate: a study of 5 debate methods on 9 benchmarks with 4 models finds they often fail to beat chain-of-thought and self-consistency while using more compute; an earlier paper finds debate underperforms self-consistency at an equal number of responses. Both are on question-answering tasks, not optimization — [Zhang et al. 2502.08788](https://arxiv.org/abs/2502.08788), [Huang et al. 2310.01798](https://arxiv.org/abs/2310.01798) [snippet]
- Model heterogeneity: the same debate paper argues heterogeneous models are what help; AlphaEvolve's secondary-reported ablation says small-model-only degrades results — [Zhang et al.](https://arxiv.org/abs/2502.08788), [arxiviq write-up](https://arxiviq.substack.com/p/alphaevolve) [snippet, unverified]

### Inferences
- When the simulator costs 1 s and judges deterministically, tournament selection or voting among LLMs before evaluation only makes sense if candidates are the scarce resource. They are here (40-100), so over-generating (ask each worker for 3-4, evaluate the 2 most novel or the 2 a cheap surrogate ranks highest) is defensible; debate about which is better is not, because the simulator answers in 1 s.
- The vision reviewer is a role without an ablation in the team's results. On circuits where a saturation constraint binds it comments on a different problem. Run one seed with and without `--critic` before claiming it helps.
- Self-consistency voting has no natural analogue for proposing continuous vectors; I found nothing measuring it for optimization.

### Gaps
- No measured ablation of debate, critique or voting inside an optimization loop was found; the negative evidence is from QA benchmarks.
- No measured ablation of one-knob sweep tools exposed to LLM agents.
- AI co-scientist (tournament/Elo ranking of hypotheses) was not searched.

## Constraint handling when a single binding constraint is never satisfied

### Takeaway
The literature read here supports two tactics: let the planner expand or unfreeze the search space when infeasibility persists, and make the system report the limiting constraint and which parameter to relax. Feasibility-first phases and finite-difference sensitivity are standard numeric practice but I found no LLM-swarm paper that isolates them.

### Cited Findings
- AutoSizer starts from a compact space and expands only on infeasibility or stagnation; its outer loop can widen or narrow ranges, re-prioritize variables, and fix or unfix variables — [AutoSizer](https://arxiv.org/html/2602.02849v1) [full]
- AnalogScout, when no legal geometry exists for a block's gm/Id intent, "reports the limiting constraint and the parameter to relax", with the simulator as final arbiter — [AnalogScout 2610.37319](https://arxiv.org/abs/2610.37319) [snippet of PDF via search; abstract page read]
- AutoSizer's circuit-understanding agent infers design variables and their performance sensitivities from the netlist and revises priorities from feedback — [AutoSizer](https://arxiv.org/html/2602.02849v1)
- LEDRO's case study: pure TuRBO over-optimized one metric (UGBW) and could not improve gain in 1,200 iterations on a 7 nm folded-cascode amplifier, the same pattern of one metric left behind — [LEDRO](https://arxiv.org/html/2411.12930v2)
- ADO-LLM: every non-hybrid method ended with exactly one or two specs violated — [ADO-LLM](https://arxiv.org/html/2406.18770v1)

### Inferences
- The current score sums violations across constraints, so a move that fixes the binding saturation margin by 30 mV but costs 5 degrees of PM can look worse and drop out of the top 3. A lexicographic score on the binding constraint (recipe 4) or a per-constraint elite (recipe 1) removes that.
- Relax-and-tighten: temporarily present workers with a relaxed target for the non-binding specs (for example the datasheet spec without guard band, which the code already tracks as `passed_nominal`) until some candidate satisfies the binding saturation constraint, then tighten. No source measured this; it is a suggestion.
- A constraint-specific specialist is cheap to try: when the census names a `never` role, overwrite one worker's direction with "maximize the worst-corner margin of role R; ignore current; other specs may degrade by up to X". The architect prompt already asks for diverse directions but does not guarantee one worker is dedicated to the binding constraint.
- The circuit card says the bias network is frozen and the telescopic replica bias "no longer tracks" if the input pair's current density changes. If the binding saturation constraint is on a device biased by a frozen node, the sensitivity table will show near-zero or wrong-sign slopes for every knob, which is evidence for `likely_infeasible` rather than for more search.

### Gaps
- No published measurement of feasibility-first phases, constraint relaxation schedules or finite-difference probes inside an LLM agent loop was found.
- I did not read the held-out run logs, so which role fails and what margins were reached is unknown to me.

## Budget split between LLM proposals (about 8 s) and cheap numeric refinement (about 1 s per 15-corner evaluation)

### Takeaway
Published hybrids spend 80-99% of evaluations on numeric-optimizer points and a small share on LLM points or LLM region choices, but none optimizes the split or reports wall-clock trade-offs at a 1 s simulation cost. In the team's setup the scarce resource is the candidate count, not seconds, so numeric refinement only "pays" if it is charged in decks or replaces LLM slots.

### Cited Findings
- ADO-LLM: 1 LLM point to 4 GP points per iteration (80% numeric) at 105 evaluations — [ADO-LLM](https://arxiv.org/html/2406.18770v1) [full]
- AutoSizer: up to 3 LLM reflections per 300 optimizer samples — [AutoSizer](https://arxiv.org/html/2602.02849v1) [full]
- LEDRO: 10 LLM region choices per 1,200 optimizer samples; despite needing fewer steps, wall-clock to reach BO-2000's optimum was 0.55x (low complexity) and 0.68x (high complexity) of the baseline's — [LEDRO](https://arxiv.org/html/2411.12930v2) [full]
- AutoSizer hard circuits wall-clock: AutoSizer 1,153 s vs BO 1,875 s and the pure-LLM EE-Sizer 1,873 s, i.e. LLM overhead was repaid by fewer evaluations at their simulation cost — [AutoSizer](https://arxiv.org/html/2602.02849v1)
- LLAMBO's authors state it trades a much larger compute footprint for sample efficiency — [LLAMBO](https://arxiv.org/abs/2402.03921) [snippet]
- AnalogScout's claim is that analytic sub-block sizing and elite reuse save most of the simulation budget that surrogate and global optimizers spend learning the response — [AnalogScout 2610.37319](https://arxiv.org/abs/2610.37319) [abstract]

### Inferences
- From the code: a round is one parallel worker call (about 8 s) producing up to 16 candidates, evaluated 2 at a time (line 669), so about 8 s of simulation. LLM and simulation time are already balanced; numeric points add evaluation time but no LLM latency. While workers are thinking, the simulator is idle: that 8 s window can run about 8-16 full evaluations, or about 100+ single-corner decks, for free in wall-clock terms.
- If the reported budget stays in candidates, every numeric point displaces an LLM point, and the ADO-LLM ratio (4:1 numeric) is probably too aggressive for 40-100 candidates given the team's own baselines; start at 2 of 8 slots.
- If the team reports budget in decks (which is what `oa.Budget` enforces), single-corner evaluation changes the economics: screen numeric and LLM proposals at the 1-3 corners that have historically been worst for the binding constraint, and promote only survivors to the full 15 corners. This is a multi-fidelity scheme; I found no cited measurement of it for LLM sizing agents in this session, and it requires corner-subset support in `oa.evaluate` (unverified).
- "Each LLM candidate seeds a tiny local search" in its simplest form: after each round, take the round's best-on-binding candidate and the previous one, and evaluate one extrapolated point along the difference (a line-search step). One extra evaluation per round, no LLM call.

### Gaps
- No paper found that studies the LLM-latency versus simulation-cost trade-off or the optimal fraction of numeric points as a function of budget.
- No published result on seeding local search or gradient estimates from each LLM proposal.

## What Google's Gemini 4 materials recommend for swarm coordination and model routing

### Takeaway
I could not find or verify any official Gemini 4 builder guide, Antigravity "orchestrator mode" documentation, or "Decisions API"; treat every claim about them as unverified. What is documented is the Interactions API's `thinking_level` control, which the swarm already uses.

### Cited Findings
- Searches for the Gemini 4 builder guide, Antigravity orchestrator mode and a Decisions API returned no official page describing them; a search for the model ids `gemini-4-hydrogen`, `gemini-4-carbon`, `gemini-4-neon` returned no documentation — no source (negative result from two web searches on 2026-11-21)
- A Google for Developers page is reported to say Gemini 4 Argon is available to AI Pro/Ultra subscribers and on the Gemini API paid tier, and to link an "Antigravity Architecture" walkthrough; I did not open it — [Google for Developers builder page](https://developers.google.com/profile/badges/builder/videos) [snippet, unverified]
- The Interactions API documents `thinking_level` with values minimal, low, medium, high, with defaults and supported levels varying by model; reasoning is surfaced as `thought` steps — [Interactions API reference](https://ai.google.dev/api/interactions), [Gemini thinking guide](https://ai.google.dev/gemini-api/docs/thinking) [snippet]
- Google Cloud's startup guidance on agents is reported to recommend starting with a single, highly specialized agent when prototyping, and describes ADK as a code-first framework for custom multi-agent systems — [Google Cloud startup guide](https://cloud.google.com/blog/topics/startups/startup-guide-ai-agents-production-ready-ai-how-to) [snippet, unverified which of two Cloud posts carries the statement]
- DeepMind's own description of AlphaEvolve is the nearest Google statement on routing: the fast model for breadth, the strong model for depth — [DeepMind blog](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) [snippet]

### Inferences
- The swarm's current routing (strong slow model for diagnosis, fast model at low thinking for proposals) matches the AlphaEvolve breadth/depth split. A routing variant that needs no new API: run one worker at a higher `thinking_level` on the binding-constraint direction, since its latency (unknown; measure it) is hidden behind the parallel round as long as it stays under the slowest worker plus evaluation time.
- The architect is asynchronous already; its 40-150 s latency means a plan lands 3-10 rounds late at 8 s per round, so half the budget can be spent on a stale plan in a 40-candidate run. A fast "mini-replan" by the worker model each round (the fallback path already exists at line 486-489) is a routing change supported by the code, not by any Google document I found.

### Gaps
- No verified Google guidance specific to Gemini 4 swarms, Antigravity orchestration or a Decisions API. If the team has these documents locally or behind a login, they should be read directly; nothing here should be attributed to them.
- Supported `thinking_level` values for the Gemini 4 model ids were not confirmed.
