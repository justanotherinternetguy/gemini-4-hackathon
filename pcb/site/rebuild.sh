#!/usr/bin/env bash
# Rebuild the whole demo site: pick boards, render missing videos, build the page, screenshots, summary video.
#   pcb/site/rebuild.sh [agent_run:random_run ...]
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
if [ "$#" -gt 0 ]; then $PY -m pcb.site.pick --runs "$@"; else $PY -m pcb.site.pick; fi
$PY -m pcb.site.videos --missing --seconds "${SECONDS_PER_VIDEO:-20}" --fps "${FPS:-12}"
$PY -m pcb.site.build
$PY -m pcb.site.summary
echo "open runs/site/index.html"
