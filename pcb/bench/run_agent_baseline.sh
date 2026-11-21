#!/usr/bin/env bash
# Single-agent baseline: PCBWorld's own built-in LLM agent, pointed at a Gemini 4 model, on one
# D3 tier, split into parallel chunks (the stock script runs boards one at a time).
#
#   export PCBWORLD_REPO=/path/to/pcbworld      # a set-up PCBWorld checkout, conda env active
#   export GEMINI_API_KEY=...
#   bash pcb/bench/run_agent_baseline.sh gemini-4-hydrogen easy  hydrogen_d3a [chunks] [rollouts]
#   bash pcb/bench/run_agent_baseline.sh gemini-4-carbon   medium carbon_d3b
#
# tier: easy = D3-A (99 boards), medium = D3-B (10), hard = D3-C (10).
# Output: $PCBWORLD_REPO/var/results/llm/<tag>/chunk<i>/ and one log per chunk beside this script.
#
# --max-new-tokens 4096 is deliberate: the stock default of 256 is used up by Gemini 4 before it
# writes an answer, and the agent then routes nothing.
set -euo pipefail
MODEL="$1"; TIER="$2"; TAG="$3"; CHUNKS="${4:-6}"; ROLLOUTS="${5:-1}"
: "${PCBWORLD_REPO:?set PCBWORLD_REPO to a PCBWorld checkout}"
: "${GEMINI_API_KEY:?set GEMINI_API_KEY}"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$HERE/.work/$TAG"; mkdir -p "$WORK"

python3 - "$PCBWORLD_REPO" "$TIER" "$CHUNKS" "$WORK" <<'PY'
import json, sys
repo, tier, n, work = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
d = json.load(open(f"{repo}/configs/datasets/d3.json"))
boards = d[tier]["test"]
n = max(1, min(n, len(boards)))
splits = {}
for i in range(n):
    c = json.loads(json.dumps(d))
    c[tier] = {"train": boards[i::n], "test": boards[i::n]}
    p = f"{work}/chunk{i}.json"
    json.dump(c, open(p, "w"), indent=1)
    splits[f"c{i}"] = {"boards_json": p, "difficulty": tier, "boards_split": "test"}
json.dump(splits, open(f"{work}/splits.json", "w"), indent=1)
print(f"{len(boards)} boards in {n} chunks")
PY

cd "$PCBWORLD_REPO"
for f in "$WORK"/chunk*.json; do
  i="$(basename "$f" .json)"; i="${i#chunk}"
  bash experiments/kdd/table1_llm/run.sh --model "$MODEL" --split "c$i" \
    --out "var/results/llm/$TAG/chunk$i" \
    --config-models "$HERE/models.json" --config-splits "$WORK/splits.json" \
    --rollouts "$ROLLOUTS" --max-new-tokens 4096 > "$WORK/chunk$i.log" 2>&1 &
done
wait
echo "done: $PCBWORLD_REPO/var/results/llm/$TAG"
