# pcb/

Circuit-board routing: a solver that repairs what the classic router left unfinished, and a 3D
viewer that shows the work and checks it. How they talk is in [`CONTRACT.md`](CONTRACT.md); how the
code is laid out is in [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

Run everything from the repository root. PCBWorld must be built in `third_party/pcbworld`.

## The three commands

**1. Run the solver** (no viewer). `--no-llm` is the scripted control arm and calls no model.

```bash
.venv/bin/python -m pcb.repair.swarm --boards 0471_BlueBerry-Zero_blueberry --run-id try1 --no-llm \
  --start var/results/baselines_dev/PCBench_corpus/freerouting/seed0/routed
```

Output lands in `runs/pcb/try1/`: `live/<board>/` (the folder a viewer can watch or replay),
`submitted/<board>.kicad_pcb`, `summary.json`. For the hard test boards use
`--boards hard --start var/results/baselines/PCBench_hard_test/freerouting/seed0/routed`.

**2. Run the viewer with the solver.** Open the printed address, pick a board, press Route.

```bash
pcb/run_studio.sh 8766                                           # agents on (needs the Gemini key)
SOLVER_ARGS='--no-llm --max-attempts 6' pcb/run_studio.sh 8766   # scripted only, no model calls
```

Arguments: `[port] [boards dir] [start folder]`. `LOAD=<board id>` opens a board at start. Every
finished run is saved to `runs/recordings/<board>__<run id>/`.

**3. Replay a recorded run.** The board opens by itself; press Route to play it.

```bash
pcb/run_replay.sh runs/recordings/0471_BlueBerry-Zero_blueberry__demo_152030_1 8767
pcb/run_replay.sh runs/pcb/try1/live/0471_BlueBerry-Zero_blueberry 8767      # a solver folder works too
```

Arguments: `<recording folder> [port] [speed]`. The page title says it is a replay.

## Good to know

- The verdict on the page comes from the viewer's own KiCad rule check, never from the solver.
- The launchers use PCBWorld's built `kicad-cli`. Set `KICAD_CLI` to use another one.
- The solver repairs a classic-router board. A board with no file in the start folder cannot be
  routed; `run_studio.sh` hides those boards by default.
- With no solver command (`python pcb/view3d.py --boards-dir ...`) the viewer runs made-up agents
  and marks the page as test data.
