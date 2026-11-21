#!/usr/bin/env bash
# Screenshot every section of the site (and the summary-video cards) at 1920x1080.
#   pcb/site/shots.sh            -> runs/site/shots/s1.png ... and card1.png ...
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT=runs/site/shots; mkdir -p "$OUT"
N=$(grep -o '<section class="slide' runs/site/index.html | wc -l)
shot() {  # file-url out.png
  chromium --headless --no-sandbox --disable-gpu --hide-scrollbars --autoplay-policy=no-user-gesture-required \
    --user-data-dir="$(mktemp -d)" --window-size=1920,1080 --virtual-time-budget="${BUDGET:-6000}" \
    --screenshot="$2" "$1" >/dev/null 2>&1
}
for i in $(seq 1 "$N"); do shot "file://$PWD/runs/site/index.html#$i" "$OUT/s$i.png" & done
for i in 1 2 3; do shot "file://$PWD/runs/site/cards.html#$i" "$OUT/card$i.png" & done
wait
ls -1 "$OUT"
