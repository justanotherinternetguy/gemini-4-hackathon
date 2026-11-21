#!/usr/bin/env bash
# Start the 3D viewer wired to our solver (pcb/repair/swarm.py), as pcb/CONTRACT.md section 7 describes.
#
#   pcb/run_studio.sh [port] [boards dir] [start folder]
#
#   port          default 8766
#   boards dir    prepared PCBWorld boards. Default: only the boards that have a classic-router
#                 starting board (a folder of links under runs/studio/boards), so every board in
#                 the picker can actually be routed.
#   start folder  classic-router (Freerouting) outputs the solver repairs. Default: the development
#                 and hard-test outputs together (links under runs/studio/start).
#
# Environment:
#   SOLVER_ARGS   extra solver arguments, e.g. SOLVER_ARGS='--no-llm --max-attempts 6' (no Gemini calls)
#   LOAD          board id to open at start, e.g. LOAD=0471_BlueBerry-Zero_blueberry
#   TITLE         page title (default 'routr studio')
#   KICAD_CLI     rule checker; default is PCBWorld's own build
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PCBW="$ROOT/third_party/pcbworld"
PORT="${1:-8766}"
BOARDS="${2:-}"
START="${3:-}"

export KICAD_CLI="${KICAD_CLI:-$PCBW/build_rl/kicad/kicad-cli}"
export KICAD_RUN_FROM_BUILD_DIR=1
[ -x "$KICAD_CLI" ] || { echo "kicad-cli not found at $KICAD_CLI (build PCBWorld or set KICAD_CLI)" >&2; exit 1; }
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "no venv at $ROOT/.venv" >&2; exit 1; }

ALL_BOARDS="$PCBW/var/datasets/pcbench/exacad_sorted"
if [ -z "$START" ]; then                      # one folder with every classic-router board we have
  START="$ROOT/runs/studio/start"; mkdir -p "$START"
  for d in "$PCBW/var/results/baselines_dev/PCBench_corpus/freerouting/seed0/routed" \
           "$PCBW/var/results/baselines/PCBench_hard_test/freerouting/seed0/routed"; do
    [ -d "$d" ] && for f in "$d"/*.kicad_pcb; do [ -e "$f" ] && ln -sf "$f" "$START/"; done
  done
fi
case "$START" in /*) ;; *) START="$(cd "$START" && pwd)";; esac
if [ -z "$BOARDS" ]; then                     # only boards the solver has a starting board for
  BOARDS="$ROOT/runs/studio/boards"; mkdir -p "$BOARDS"
  for d in "$ALL_BOARDS"/*/; do
    id="$(basename "$d")"
    if compgen -G "$START/${id}*.kicad_pcb" > /dev/null; then ln -sfn "${d%/}" "$BOARDS/$id"; fi
  done
fi
echo "boards: $BOARDS ($(ls "$BOARDS" | wc -l))   start: $START ($(ls "$START" | wc -l) boards)"

cd "$ROOT"
exec "$PY" pcb/view3d.py --cwd "$ROOT" --port "$PORT" --title "${TITLE:-routr studio}" \
  --boards-dir "$BOARDS" ${LOAD:+--load "$LOAD"} \
  --watch-dir 'runs/pcb/{run_id}/live/{board}' \
  --route-cmd "$PY -m pcb.repair.swarm --boards {board} --run-id {run_id} --start '$START' ${SOLVER_ARGS:-}"
