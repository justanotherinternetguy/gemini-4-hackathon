#!/usr/bin/env bash
# Fast path: prepare only the boards we need (D3-C hard test, D3-B medium test, and a
# development sample of hard-train boards) with PCBWorld's OWN preparation scripts, then give
# the folders their official D3 ids. The full 677-board chain (tools/quickstart/prepare_pcbench.sh)
# takes much longer; it can be run afterwards to cross-check these boards.
#
#   cd third_party/pcbworld && conda activate pcbworld
#   export PYTHONPATH=build_rl/pcbnew/python/rl:. PCBWORLD_DATA_ROOT=$PWD/var/datasets
#   bash ../../pcb/repair/prepare_subset.sh
set -euo pipefail
: "${PCBWORLD_DATA_ROOT:?export PCBWORLD_DATA_ROOT first}"
SRC="$PCBWORLD_DATA_ROOT/PCBench/PCBs"
SUB="$PCBWORLD_DATA_ROOT/subset_work"
rm -rf "$SUB"; mkdir -p "$SUB/PCBs"
python - "$SRC" "$SUB" <<'PY'
import json, os, sys
src, sub = sys.argv[1], sys.argv[2]
d = json.load(open("configs/datasets/d3.json"))
test = set(d["hard"]["test"])
dev = [i for i in d["hard"]["train"] if i not in test]
dev = dev[:: max(1, len(dev) // 14)][:14]            # an even spread over the hard-train pool
ids = d["hard"]["test"] + d["medium"]["test"] + dev
m = {}
for i in ids:
    name = i.split("_", 1)[1]
    if not os.path.isdir(os.path.join(src, name)):
        raise SystemExit(f"missing source board {name}")
    os.symlink(os.path.abspath(os.path.join(src, name)), os.path.join(sub, "PCBs", name))
    m[name] = i
json.dump({"name_to_id": m, "hard_test": d["hard"]["test"], "medium_test": d["medium"]["test"], "hard_dev": dev},
          open(os.path.join(sub, "ids.json"), "w"), indent=1)
print(f"subset: {len(ids)} boards ({len(d['hard']['test'])} hard test, {len(d['medium']['test'])} medium test, {len(dev)} hard dev)")
PY
export PCBENCH_PCBS_ROOT="$SUB/PCBs"
export PCBENCH_V9_ROOT="$SUB/v9"
export PCBENCH_NEWDRC_OUT="$SUB/newdrc"
export PCBENCH_SORTED_OUT="$SUB/sorted_raw"
PP=tools/datagen/pcbench_prep
W="${WORKERS:-12}"
python "$PP/convert_v9.py" --workers "$W"
python "$PP/drc_fix_v9.py" --workers "$W"
python "$PP/make_guide.py" --base-dir "$PCBENCH_NEWDRC_OUT" --stem processed_v9 --suffix _guide_v3 --workers "$W"
python "$PP/sort_prefix.py"
# rename <subset index>_<name> -> official <NNNN>_<name>
FINAL="$PCBWORLD_DATA_ROOT/pcbench/exacad_sorted"
mkdir -p "$FINAL"
python - "$SUB" "$FINAL" <<'PY'
import json, os, shutil, sys
sub, final = sys.argv[1], sys.argv[2]
m = json.load(open(os.path.join(sub, "ids.json")))["name_to_id"]
n = 0
for d in sorted(os.listdir(os.path.join(sub, "sorted_raw"))):
    p = os.path.join(sub, "sorted_raw", d)
    if not os.path.isdir(p):
        continue
    name = d.split("_", 1)[1]
    if name not in m:
        raise SystemExit(f"unexpected board {d}")
    dst = os.path.join(final, m[name])
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(p, dst)
    n += 1
missing = sorted(set(m.values()) - set(os.listdir(final)))
print(f"prepared {n} boards into {final}; missing after DRC filter: {missing}")
PY
export PCBENCH_SORTED_OUT="$FINAL"
export PCBENCH_DSN_ROOT="$PCBWORLD_DATA_ROOT/pcbench/exacad_sorted_dsn"
rc=0; python "$PP/make_dsn_orp.py" || rc=$?
[ "$rc" = 0 ] || [ "$rc" = 3 ] || exit "$rc"
ls "$FINAL" | wc -l
