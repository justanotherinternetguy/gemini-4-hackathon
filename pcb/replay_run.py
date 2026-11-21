"""Replay a recorded run into a folder, at its original pace, so the viewer can animate it again.

A recording is a folder with board versions and (when the viewer recorded it) a manifest.json of
when each one arrived. The viewer writes one for every finished run under runs/recordings/. A
solver's own output folder works too: without a manifest, versions are replayed in file-name
order, spaced by --gap seconds.

Use it as the viewer's solver command, and say on screen that it is a replay:

    python pcb/view3d.py --boards-dir ... --title 'Board router (replay of a recorded run)' \\
        --route-cmd 'python replay_run.py --from runs/recordings/<board>__<run_id> --out {out}'
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", required=True, help="recording folder")
    ap.add_argument("--out", required=True, help="folder the viewer is watching")
    ap.add_argument("--speed", type=float, default=1.0, help="2 = twice as fast")
    ap.add_argument("--gap", type=float, default=1.5, help="seconds between versions when there is no manifest")
    ap.add_argument("--max-wait", type=float, default=6.0, help="never pause longer than this between versions")
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    man = src / "manifest.json"
    if man.is_file():
        versions = [(v["t"], v["file"]) for v in json.loads(man.read_text())["versions"]]
    else:
        files = sorted(p.name for p in src.glob("*.kicad_pcb"))
        versions = [((i + 1) * args.gap, name) for i, name in enumerate(files)]
    events = []
    if (src / "events.jsonl").is_file():
        for line in (src / "events.jsonl").read_text().splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    events.sort(key=lambda e: e.get("t", 0))

    clock = 0.0
    for t, name in versions:
        wait = min(args.max_wait, max(0.05, (t - clock) / max(args.speed, 0.01)))
        time.sleep(wait)
        clock = t
        with open(out / "events.jsonl", "a") as fh:      # events up to this moment, in step
            while events and events[0].get("t", 0) <= t:
                fh.write(json.dumps(events.pop(0)) + "\n")
        if (src / name).is_file():
            tmp = out / (name + ".tmp")
            shutil.copyfile(src / name, tmp)
            tmp.rename(out / name)                       # appear all at once, never half-written
            print(f"{t:7.1f}s  {name}", flush=True)
    with open(out / "events.jsonl", "a") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    if (src / "result.json").is_file():
        shutil.copyfile(src / "result.json", out / "result.json")


if __name__ == "__main__":
    main()
