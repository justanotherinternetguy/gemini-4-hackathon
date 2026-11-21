"""Render the featured boards' videos (agents vs random control) with pcb/make_video.py.

    .venv/bin/python -m pcb.site.videos [--seconds 24] [--boards <board> ...]

Reads runs/site/pick.json (written by pcb.site.pick); writes runs/site/video/<board>.mp4 and a
poster PNG. The speed is chosen per board so every video lasts about --seconds.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pcb.site.data import ROOT, RUNS, _jsonl, short

OUT = ROOT / "runs" / "site" / "video"


def span(live: Path) -> float:
    ev = _jsonl(live / "events.jsonl")
    ts = [e["t"] for e in ev if "t" in e]
    t0 = next((e["t"] for e in ev if e.get("agent") == "router"), min(ts, default=0.0))
    return max(ts, default=t0) - t0


def render(x: dict, seconds: float) -> str:
    run = RUNS / x["agent_run"] / "live" / x["board"]
    ctl = RUNS / x["random_run"] / "live" / x["board"]
    has_ctl = ctl.is_dir() and any(ctl.glob("0000__*.kicad_pcb"))
    length = max(span(run), span(ctl) if has_ctl else 0.0, 10.0)
    speed = max(1.0, round(length / seconds, 1))
    out = OUT / f"{x['board']}.mp4"
    cmd = [sys.executable, "-m", "pcb.make_video", "--run", str(run), "--out", str(out), "--speed", str(speed),
           "--poster", "--title", f"{short(x['board'])}: Gemini 4 step agents vs a random policy"]
    if has_ctl:
        cmd += ["--control", str(ctl)]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return f"{x['board']}: speed x{speed} -> {out}\n{(r.stdout + r.stderr).strip()[-600:]}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=24.0, help="target length of each video")
    ap.add_argument("--boards", nargs="*", help="only these boards (default: the featured ones)")
    a = ap.parse_args()
    pick = json.loads((ROOT / "runs" / "site" / "pick.json").read_text())
    todo = [x for x in pick["ranking"] if x["board"] in a.boards] if a.boards else pick["featured"]
    OUT.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(len(todo) or 1) as ex:
        for msg in ex.map(lambda x: render(x, a.seconds), todo):
            print(msg, flush=True)


if __name__ == "__main__":
    main()
