#!/usr/bin/env bash
# Replay a recorded run in the 3D viewer (pcb/CONTRACT.md section 6). The page is titled as a replay.
#
#   pcb/run_replay.sh <recording folder> [port] [speed]
#
# The recording is a folder of board versions: one the viewer saved (runs/recordings/<board>__<run_id>)
# or the solver's own folder (runs/pcb/<run_id>/live/<board>). The board is opened for you; press
# Route in the page to play. The verdict is a fresh KiCad rule check of the replayed files.
#
# Environment: BOARD (board id, if it cannot be read from the folder), BOARDS_DIR, GAP (seconds
# between versions when the folder has no manifest, default 3), KICAD_CLI.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PCBW="$ROOT/third_party/pcbworld"
[ $# -ge 1 ] || { sed -n 2,11p "${BASH_SOURCE[0]}" >&2; exit 2; }
REC="$(cd "$1" && pwd)"
PORT="${2:-8767}"
SPEED="${3:-2}"
BOARDS_DIR="${BOARDS_DIR:-$PCBW/var/datasets/pcbench/exacad_sorted}"

export KICAD_CLI="${KICAD_CLI:-$PCBW/build_rl/kicad/kicad-cli}"
export KICAD_RUN_FROM_BUILD_DIR=1
[ -x "$KICAD_CLI" ] || { echo "kicad-cli not found at $KICAD_CLI (build PCBWorld or set KICAD_CLI)" >&2; exit 1; }
PY="$ROOT/.venv/bin/python"
ls "$REC"/*.kicad_pcb > /dev/null 2>&1 || { echo "no .kicad_pcb versions in $REC" >&2; exit 1; }

BOARD="${BOARD:-}"
if [ -z "$BOARD" ] && [ -f "$REC/manifest.json" ]; then
  BOARD="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("board",""))' "$REC/manifest.json")"
fi
[ -n "$BOARD" ] || BOARD="$(basename "$REC")"; BOARD="${BOARD%%__*}"
[ -d "$BOARDS_DIR/$BOARD" ] || { echo "board '$BOARD' is not in $BOARDS_DIR (set BOARD=<board id>)" >&2; exit 1; }
echo "replaying $REC as board $BOARD at ${SPEED}x"

cd "$ROOT/pcb"
exec "$PY" view3d.py --port "$PORT" --record-dir '' --boards-dir "$BOARDS_DIR" --load "$BOARD" \
  --title 'Board router (replay of a recorded run)' \
  --route-cmd "$PY replay_run.py --from '$REC' --out {out} --speed $SPEED --gap ${GAP:-3}"
