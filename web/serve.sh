#!/usr/bin/env bash
# Build the site and serve it with both live tools, for a demo on this machine.
#
#   web/serve.sh            site on :8080, analog stage on :8000, PCB studio on :8766
#   NO_LIVE=1 web/serve.sh  site only
#
# The site embeds the two live tools when they answer. Ctrl-C stops everything.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
"$PY" -m web.build
pids=()
trap 'kill "${pids[@]}" 2>/dev/null' EXIT INT TERM
if [ -z "${NO_LIVE:-}" ]; then
  "$PY" -m demo.server --port 8000 > runs/web/stage.log 2>&1 & pids+=($!)
  pcb/run_studio.sh 8766 > runs/web/studio.log 2>&1 & pids+=($!)
  echo "analog stage  http://127.0.0.1:8000   (log runs/web/stage.log)"
  echo "PCB studio    http://127.0.0.1:8766   (log runs/web/studio.log)"
fi
echo "site          http://127.0.0.1:8080"
"$PY" -m http.server 8080 --bind 127.0.0.1 --directory runs/web
