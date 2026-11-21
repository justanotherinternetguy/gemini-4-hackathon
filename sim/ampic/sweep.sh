#!/usr/bin/env bash
# Run the AMPICBench 2-stage op-amp at every corner x temperature in parallel.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
run() {
  c=$1; t=$2; d="$here/runs/${c}_${t}"; mkdir -p "$d"; cp "$here/.spiceinit" "$d/"
  sed -e "s#^.lib \"../models/sky130_slim.lib.spice\" tt#.lib \"$here/../models/sky130_slim.lib.spice\" $c#" \
      -e "s#^.temp 27#.temp $t#" "$here/opamp2stg_diff.cir" > "$d/deck.cir"
  (cd "$d" && ngspice -b deck.cir > run.log 2>&1)
  awk -v c="$c" -v t="$t" '/^ugf/{u=$3}/^pm_raw/{p=$3}/^dcgain_db/{g=$3}END{printf "%-3s %4s  gain=%6.2f dB  ugf=%6.1f MHz  pm=%6.2f deg\n",c,t,g,u/1e6,180+p}' "$d/stb_meas.txt"
}
export -f run; export here
parallel -j 15 run ::: tt ff ss sf fs ::: -40 27 125 2>/dev/null || for c in tt ff ss sf fs; do for t in -40 27 125; do run $c $t & done; done; wait
