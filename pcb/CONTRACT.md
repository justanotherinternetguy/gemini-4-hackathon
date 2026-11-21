# Solver and viewer contract (v1)

How the routing solver (`pcb/repair/swarm.py`, or anything else) and the 3D viewer
(`pcb/view3d.py`) work together. One format covers both the live demo and an after-the-fact replay.

## The whole contract in five lines

1. The viewer starts the solver with a command and names a folder to watch.
2. The solver saves a new `.kicad_pcb` file into that folder each time the board changes.
3. The file name says which agent made the change: `0007__w3.kicad_pcb`.
4. Optionally, the solver appends one line per thing that happened to `events.jsonl`.
5. The solver exits when it is done. The viewer, not the solver, decides whether the board passed.

A finished folder is also the replay buffer. Nothing extra has to be written for replay.

## 1. Starting the solver

The viewer runs one shell command when the user presses Route. Placeholders it fills in:

| Placeholder | Meaning |
|---|---|
| `{board}` | Board name, for benchmark boards the PCBWorld id such as `0400_laptimer58_Chickadee` |
| `{unrouted}` | Path to the starting board with no copper |
| `{project}` | Path to the board's `.kicad_pro` (its design rules), empty if there is none |
| `{run_id}` | A fresh id for this run, such as `demo_151126_1` |
| `{out}` | A fresh empty folder owned by the viewer |

The viewer watches `--watch-dir`, which defaults to `{out}` and may use the same placeholders.
The command runs in `--cwd`. Exit code 0 means "finished", whether or not the board is clean.
Any other exit code means the solver crashed.

## 2. Board versions (required)

Save the board into the watched folder whenever it changes.

- **Whole board every time.** Each file is the complete board, not a patch.
- **Same board.** Only tracks and vias may differ from `{unrouted}`. Do not move parts, edit
  pads, rename nets or change rules. The viewer works out what copper was added or removed by
  comparing each version with the one before it.
- **Order.** Versions are shown in the order they were saved (file modification time, then name).
- **Atomic.** Write to `name.tmp`, then rename. A plain copy also works; the viewer waits for
  the size to stop changing.
- **Pace.** At most about five versions per second. One version per completed connection or per
  improved attempt is the right grain.
- **Credit.** The agent's name is the part of the file name after the last `__`:
  `0007__w3.kicad_pcb` credits `w3`. As a fallback, `<digits>_<tag>.kicad_pcb` credits `<tag>`.
  A file with neither is shown as one unnamed router.
- **Only credit real work.** The viewer animates whatever the names say.

Recommended first version: the board as the classic router left it, named `0000__router.kicad_pcb`.
The audience then sees the classic router's work appear, followed by the agents repairing it.

## 3. `events.jsonl` (optional, recommended)

Append one JSON object per line, in the watched folder. Every line has `t` (seconds since the
solver started) and `type`. Unknown fields are kept and ignored.

| `type` | Extra fields | Meaning |
|---|---|---|
| `note` | `agent`, `text` | Free text, such as the planner's one-line plan |
| `round` | `n` | A planning round started |
| `start` | `agent`, `net` | An agent began work on a connection or plan |
| `attempt` | `agent`, `plan`, `open`, `errors`, `clean`, `seconds`, `best` | A whole-board attempt finished and was judged. `best` is true if it became the new best |
| `done` | `agent`, `net`, `file` | A connection was completed in version `file` |
| `fail` | `agent`, `net`, `reason` | An agent gave up on something, with a short reason |

`attempt` is the natural event for `pcb/repair/swarm.py`, where each worker produces a whole
candidate board. Attempts that did not win have no board version, so events are the only way
the audience sees that those agents worked.

## 4. `result.json` (optional)

Written once, at the end:

```json
{"claimed_clean": true, "attempts": 11, "left": [{"net": "/RESET", "reason": "blocked on both layers"}]}
```

`left` feeds the "needs you" screen. Leave a reason out if the solver does not know one. The
viewer never shows a reason the solver did not give.

## 5. What the viewer does

- Shows the bare board first, taken from the imported file, with every open connection in red.
- For each new version: new copper grows along its path behind the credited agent's marker,
  and removed copper flashes red and fades.
- Runs KiCad's design-rule check on every version. Open connections and violations on screen
  come from that check, never from the solver.
- When the solver exits, shows the verdict from the last version's check.
- Copies the finished run to `runs/recordings/<board>__<run_id>/` with a `manifest.json` of
  when each version arrived.

## 6. Replay

A recording is a folder of board versions, plus `events.jsonl`, `result.json` and
`manifest.json` when they exist. The solver's own output folder is a valid recording. Without a
manifest, versions replay in file-name order at a fixed spacing.

```bash
python pcb/view3d.py --boards-dir <boards> --title 'Board router (replay of a recorded run)' \
  --route-cmd 'python replay_run.py --from <recording folder> --out {out} --speed 2'
```

A replay must be labelled as a replay on screen. The `--title` above does that.

To move a recording between machines, copy the folder. If it goes through git, modification
times are lost, which is why the manifest and `events.jsonl` carry their own times.

## 7. `pcb/repair/swarm.py` as it is on main today

It already works with no changes, because it copies each round's best board to
`runs/pcb/<run_id>/live/<board>/<unixtime>_<tag>.kicad_pcb`:

```bash
export KICAD_CLI=third_party/pcbworld/build_rl/kicad/kicad-cli KICAD_RUN_FROM_BUILD_DIR=1
python pcb/view3d.py --cwd . \
  --boards-dir third_party/pcbworld/var/datasets/pcbench/exacad_sorted \
  --watch-dir 'runs/pcb/{run_id}/live/{board}' \
  --route-cmd '.venv/bin/python -m pcb.repair.swarm --boards {board} --run-id {run_id} --start <folder of classic-router boards>'
```

Three small changes would make the demo much better, in this order:

1. **Put the starting board in `live/` first**, as `0000__router.kicad_pcb`, right after it is
   saved as `__start`.
2. **Copy a new best into `live/` as soon as an attempt beats the current best**, not once per
   round, named `{seq:04d}__{tag}.kicad_pcb`.
3. **Append an `attempt` event for every finished attempt.** Everything needed is already in
   `rec`:

```python
def emit(live, t0, **kw):
    with open(live / "events.jsonl", "a") as f:
        f.write(json.dumps({"t": round(time.time() - t0, 2), **kw}) + "\n")

emit(live, t0, type="attempt", agent=r["tag"], plan=r["plan"].get("name", ""),
     open=r.get("end_status", {}).get("unrouted"), errors=r.get("end_status", {}).get("drc_errors"),
     clean=bool((r.get("score") or {}).get("clean_pass")), seconds=r.get("seconds"), best=r is best)
```

## 8. For the page: `/state`

`GET /state` returns: `board`, `epoch` (changes when a board is loaded), `attempt` (changes on
each Route), `frames` (one per version: `id`, `name`, `agent`, `t`, `open`, `violations`,
`length_mm`, `segments`, `vias`, `checked`), `events` (the lines of `events.jsonl` so far),
`result`, `routing`, `route_started`, `route_seconds`, `solver_exit`, `recorded`, `dummy`,
`checker`, `can_route`. `GET /frame/<id>` returns that version's copper and, once checked, the
positions of open connections and violations.

## Tested

Both paths were run on 21 Nov with the built-in dummy solver (made-up agents): a live run of 40
versions and 40 events, then a replay of its recording at double speed. `pcb/repair/swarm.py`
has not been run through the viewer yet.
