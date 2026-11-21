# routr

Teams of Gemini 4 agents on two hardware jobs that stall chip and board work: finishing the last
connections on a circuit board, and sizing an analog circuit until it passes at every corner. An
independent checker scores every result against a published or standard baseline.

**Site with all results and agent logs: https://routr.internetguy.dev**

## Results

| Task | routr | Baseline |
|---|---|---|
| PCBWorld hard tier: boards with every connection routed and zero rule errors | **7 / 10** | Freerouting 3 / 10, PCBRouteAgent (published) 4 / 10 |
| SKY130 two-stage op-amp, all 15 PVT points, 40 candidates per run | **7 / 7 runs pass** | Optuna TPE, GP-BO and random search: 1 / 18 |
| AMS-SizingBench, five circuits we did not write, 3 seeds | **14 / 15** | GP-BO 13, TPE 13, random 12 |
| Device roles read from a schematic photo | **46 / 49** | netlist only 43 / 49 |
| Independent sign-off agent on the winning op-amp | **15 / 15 points match** | |

What did not work, on the site and below: two held-out analog circuits (folded cascode,
telescopic) pass 0 of 12 runs, and TPE also passes 0 of 6. One run per PCB arm, so no error bars.
Of the 7 clean boards, 5 need no model (router + scripted repair); Gemini step agents added
Chickadee (0400) and the Gemini council added HybridChargeController (0626).

## How it works

**PCB routing** (`pcb/`). Freerouting routes about 97% of each board. Then:

- *Step agents* (`pcb/repair/stepper.py`): a gemini-4-carbon lead reads the board
  picture and writes a strategy note; four gemini-4-hydrogen agents with different temperaments
  each work their own copy of the board, one action per step (route, route with a via, rip up,
  roll back). KiCad's router draws every track; its rule checker judges every step.
- *Council* (`pcb/repair/council.py`): six gemini-4-hydrogen proposers, a gemini-decision-1
  screen, a gemini-4-neon reviewer that looks at the board picture, and a gemini-4-carbon chief
  that picks, edits or merges proposals into three branches. Each branch runs on its own copy;
  the best board carries to the next round.
- *Managed Agent specialist* (`pcb/repair/specialist.py`): one long-running agent per stuck board.
- Controls: a random policy with the same actions and budget, in both harnesses.
- Scoring: PCBWorld's `evaluate_one` on the submitted board. The solver never grades itself.

**Analog sizing** (`sizing/`). The Decisions API reads device roles from a schematic photo; a
spoken or typed spec becomes targets; a gemini-4-carbon architect directs four gemini-4-hydrogen
workers; ngspice judges every candidate at 15 PVT points. A Managed Agent re-simulates the winner
from scratch (sign-off), and another tries to break the judge (red team). Baselines are Optuna TPE,
GP-BO and random search on the same budget (`sizing/baseline.py`, `bench/`).

## Run it

```bash
web/serve.sh        # site on :8080, analog stage on :8000, PCB studio on :8766 (embedded in the site)
.venv/bin/python -m web.build   # rebuild the site from runs/ after new results
web/deploy.sh       # publish the built site to the gh-pages branch (routr.internetguy.dev)
```

| Folder | What |
|---|---|
| `web/` | The site generator. Every number and every line of agent text comes from `runs/`. |
| `pcb/` | Board repair: solver, council, specialist, 3D studio (`view3d.py`), [contract](pcb/CONTRACT.md). See [pcb/README.md](pcb/README.md). |
| `sizing/`, `bench/` | Op-amp swarm, baselines, intake, sign-off, red team; AMS-SizingBench runs. |
| `demo/` | The analog live stage (photo in, spoken spec in, crew sizes the circuit live). |
| `layout/`, `create/`, `viz/` | Layout planning, circuit from text or a sketch, figures. |
| `docs/` | [Architecture](docs/ARCHITECTURE.md) of the solver, the watched folder and the viewer. |

PCBWorld lives in `third_party/pcbworld` (not committed); see `pcb/README.md` for setup.
