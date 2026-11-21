# Architecture: the routing solver, the watched folder and the viewer

This describes how the circuit-board routing part of the repository fits together. The design is
built around one small agreement, `pcb/CONTRACT.md` (v1): **the solver saves whole boards into a
folder; the viewer watches that folder, checks every board itself and decides the verdict.**
A finished folder is also the replay buffer.

## The picture

```
                     starts it with one shell command (Route button)
        +-----------------------------------------------------------------+
        |                                                                 |
        v                                                                 |
+----------------------+                                    +----------------------------+
| SOLVER               |   writes          reads            | VIEWER  pcb/view3d.py      |
| pcb/repair/swarm.py  | -------> WATCHED FOLDER ---------> | (page: pcb/view3d.html)    |
| (our venv)           |   runs/pcb/<run_id>/live/<board>/  |                            |
|  planner + workers   |     0000__router.kicad_pcb         | - draws copper per version |
|  (Gemini 4), or      |     0001__<agent>.kicad_pcb ...    | - runs KiCad's rule check  |
|  scripted (--no-llm) |     events.jsonl   (optional)      |   on every version         |
+----------+-----------+     result.json    (optional)      | - verdict = check of the   |
           | JSON lines                                     |   last version             |
           v                                                | - copies the finished run  |
+----------------------+                                    |   to runs/recordings/      |
| ENGINE TOOL          |                                    +-------------+--------------+
| pcb/repair/          |                                                  |
|   engine_tool.py     |      REPLAY: pcb/replay_run.py copies a          v
| (PCBWorld conda env, |      recording back into a fresh folder   runs/recordings/
|  pinned KiCad engine)|      at its original pace; the viewer     <board>__<run_id>/
+----------------------+      cannot tell it from a live solver     + manifest.json
```

Data flows one way: solver to folder to viewer. The viewer never writes into the watched folder
and the solver never reads anything from the viewer.

## Who owns what

| Module | Runs in | Job |
|---|---|---|
| `pcb/repair/engine_tool.py` | PCBWorld's conda env (`~/miniforge3/envs/pcbworld`), cwd `third_party/pcbworld` | One process per board attempt. Reads JSON requests on stdin (`open`, `status`, `nets`, `ripup`, `route`, `save`, `score`, `render`, ...). KiCad's own router draws every track here. `score` calls PCBWorld's scorer. |
| `pcb/repair/swarm.py` | our venv (`.venv`) | The orchestrator and the agents. Takes a board the classic router (Freerouting) left unfinished, plans repairs (which nets to rip up, in what order, in which router mode), runs each plan in an engine process, keeps the best. Its `Live` class publishes to the watched folder. |
| `pcb/repair/specialist.py` | our venv | One long-running Gemini Managed Agent per stuck board, with three coarse tools that call the same engine. Writes its own run folder; it does **not** publish to a watched folder yet. |
| `pcb/repair/fr_reroute.py` | PCBWorld's conda env | Hands a partly ripped-up board back to Freerouting and merges the result. |
| `pcb/view3d.py` + `view3d.html` | our venv, standard library only | The viewer: web server, folder watcher, rule check (`kicad-cli pcb drc`), recordings. |
| `pcb/board_parse.py` | imported by the viewer | Reads parts, pads and copper out of a `.kicad_pcb` file. |
| `pcb/replay_run.py` | any Python | Plays a recording into a folder, for the viewer. |
| `pcb/make_test_frames.py` | any Python | Test fixture: made-up agents. The viewer uses it when no solver command is given and labels the page as test data. |
| `pcb/create/` | `describe` in our venv, `build` in the system Python | A separate flow: words or a sketch to a small new board. Does not use the contract. |
| `demo/` | our venv | The earlier analog-sizing stage ("routr"). Separate server; not part of this contract. |
| `third_party/pcbworld/` | - | The benchmark: prepared boards, the engine build (`build_rl/`), the scorer. |

## File formats

**Board versions** (required). `<seq>__<agent>.kicad_pcb`, for example `0003__carbon.kicad_pcb`.
Each file is the complete board; only tracks and vias may differ from the starting board. The name
after the last `__` is who gets credit on screen. Our solver writes `0000__router.kicad_pcb` first
(the classic router's board), then one new version each time an attempt becomes the new best, named
after whoever wrote the plan: `script`, `random`, `hydrogen`, `carbon`. Files are written to
`name.tmp` and renamed, so the viewer never sees half a file. Older runs used
`<unixtime>_<tag>.kicad_pcb`; the viewer still accepts that and credits `<tag>`.

**`events.jsonl`** (optional). One JSON object per line; every line has `t` (seconds since the
solver started) and `type`:

| `type` | Fields | Written by our solver |
|---|---|---|
| `note` | `agent`, `text` | start summary; the decision model's blocker screen |
| `round` | `n` | each planning round |
| `start` | `agent`, `net` | each plan as it is handed to an engine (`net` holds the plan name) |
| `attempt` | `agent`, `plan`, `open`, `errors`, `clean`, `seconds`, `best` | every finished attempt, including the ones that did not win |
| `done`, `fail` | `agent`, `net`, `file` / `reason` | not written (our solver works in whole-board attempts) |

**`result.json`** (optional, written once at the end):
`{"claimed_clean": true, "attempts": 6, "left": [{"net": "/RESET"}]}`. The page shows `attempts`
and uses `left` reasons in the "needs you" list. `claimed_clean` is the solver's claim and is never
shown as the verdict.

**`manifest.json`** (written by the viewer into each recording): board, run id, seconds, solver
exit code, `test_data`, and for each version its file name, arrival time and agent.

**Solver-private files**, outside the contract, under `runs/pcb/<run_id>/`:

- `<board>/history.json`: one record per attempt (plan, start and end status, PCBWorld score, path
  of the saved board, per-net route log, seconds).
- `<board>/blockers.json`, `start.png`, `final.png`, and every attempt's board.
- `submitted/<board>.kicad_pcb`: the one board the solver submits for scoring.
- `summary.json`: per run. Boards, clean before and after, total attempts, wall time, per-board results.

## Who is allowed to judge

- **On screen: the viewer.** It runs KiCad's design-rule check on every version, with the board's
  own `.kicad_pro` rules. Open connections and violations shown on the page come from that check.
  The verdict is the check of the last version: zero open and zero error-level violations is a pass.
- **In reported numbers: PCBWorld's scorer** (`eval.metrics.evaluate_one`), run on the files in
  `submitted/`. Clean pass means fully connected with zero error-level violations.
- **Never the solver.** It may call the scorer as feedback to rank its own attempts, and it writes
  `claimed_clean`, but nothing it says is displayed as a result. Every attempt is counted.

The two checks count differently, so the solver's `errors` in an `attempt` event may not equal the
viewer's violation count for the same board. On the test board the solver reported "1 rule error"
at the start where KiCad's check shows 1 open connection and 0 violations. The page shows KiCad's.

## How to run

```bash
# 1. Solver alone (no viewer). --no-llm is the scripted control arm and calls no model.
.venv/bin/python -m pcb.repair.swarm --boards 0471_BlueBerry-Zero_blueberry --run-id try1 --no-llm \
  --start var/results/baselines_dev/PCBench_corpus/freerouting/seed0/routed

# 2. Viewer wired to the solver. Open http://127.0.0.1:8766, pick a board, press Route.
pcb/run_studio.sh 8766                                        # agents on (needs the Gemini key)
SOLVER_ARGS='--no-llm --max-attempts 6' pcb/run_studio.sh 8766   # scripted only, no model calls

# 3. Replay a finished run. The page title says it is a replay. Press Route to play.
pcb/run_replay.sh runs/recordings/<board>__<run_id> 8767
pcb/run_replay.sh runs/pcb/<run_id>/live/<board> 8767         # the solver's own folder works too
```

What `run_studio.sh` expands to (CONTRACT section 7):

```bash
export KICAD_CLI=$PWD/third_party/pcbworld/build_rl/kicad/kicad-cli KICAD_RUN_FROM_BUILD_DIR=1
.venv/bin/python pcb/view3d.py --cwd . --port 8766 --boards-dir <boards> \
  --watch-dir 'runs/pcb/{run_id}/live/{board}' \
  --route-cmd '.venv/bin/python -m pcb.repair.swarm --boards {board} --run-id {run_id} --start <start folder>'
```

Use PCBWorld's built `kicad-cli`, not the stock KiCad 10 one on `PATH`: the check should match the
engine that routed the board. `--start` is a folder of classic-router boards named `<board id>*.kicad_pcb`;
a relative path is taken inside `third_party/pcbworld`. With no arguments `run_studio.sh` links the
development and hard-test Freerouting outputs into `runs/studio/start` and offers only the boards
that have one (20 of the 34 prepared boards).
Each Route makes a new run id `demo_<HHMMSS>_<n>`; the solver's files land in `runs/pcb/<run id>/`
and the recording in `runs/recordings/<board>__<run id>/`.

## What is not covered

- **Boards with no classic-router board.** The solver repairs; it does not route from nothing. For
  a board missing from `--start` it prints a message and saves nothing.
- **Uploaded boards.** Our solver only knows PCBWorld board ids, so Route does nothing for a dropped file.
- **Per-connection progress.** The page shows one version per new best attempt, not copper growing net by net.
- **The specialist** (`pcb/repair/specialist.py`) does not publish to a watched folder.
- **Solver output.** The viewer discards the solver's stdout and stderr; a crash shows only the exit code.
- **One board and one run at a time** per viewer process.
- `pcb/live_view.py` is the earlier flat viewer; `pcb/create/` and `demo/` are separate flows.

Checked on 21 Nov: live `--no-llm` runs through the viewer (`router` version first, then `script`,
events shown, verdict from the viewer's check, recording saved), and replays of a recording, of the
solver's own `live/` folder and of older-naming folders.
