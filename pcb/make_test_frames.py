"""Make a fake "routing in progress" sequence from one finished board, to test the live viewer.

Takes a routed board and writes N copies into the output folder, each keeping only the first k
tracks and vias, ending with the full board. This is a TEST FIXTURE for the viewer: the order is
file order, not how any router actually worked, so it must not be shown as a method's run.

    python pcb/make_test_frames.py --routed board.kicad_pcb --out runs/live --frames 30 --delay 0.5
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

ITEM_RE = re.compile(r"\n\t\((?:segment|via|arc)\b.*?\n\t\)", re.S)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--routed", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", type=int, default=30)
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between files (0 = write all at once)")
    args = ap.parse_args()

    text = Path(args.routed).read_text()
    items = list(ITEM_RE.finditer(text))
    if not items:
        raise SystemExit("no tracks or vias found in the routed board")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    n = len(items)
    for i in range(args.frames + 1):
        keep = round(n * i / args.frames)
        parts, last = [], 0
        for j, m in enumerate(items):
            parts.append(text[last:m.start()])
            if j < keep:
                parts.append(m.group(0))
            last = m.end()
        parts.append(text[last:])
        (out / f"step_{i:03d}.kicad_pcb").write_text("".join(parts))
        print(f"step_{i:03d}: {keep}/{n} copper items", flush=True)
        if args.delay:
            time.sleep(args.delay)


if __name__ == "__main__":
    main()
