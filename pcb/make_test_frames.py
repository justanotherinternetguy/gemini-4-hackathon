"""Make a fake "routing in progress" sequence from one finished board, to test the viewers.

Takes a routed board and writes versions into the output folder: version 0 has no copper, and
each later version adds the copper of one more net (connection group), ending with the full board.
With --agents N the versions are credited to made-up workers w1..wN in turn.

This is a TEST FIXTURE. The order and the worker names are invented, so its output must never be
shown as a real method's run.

    python pcb/make_test_frames.py --routed board.kicad_pcb --out runs/test --agents 4 --delay 0.6
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

ITEM_RE = re.compile(r"\n\t\((?:segment|via|arc)\b.*?\n\t\)", re.S)
NET_RE = re.compile(r"\(net\s+(\d+)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--routed", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--agents", type=int, default=0, help="credit versions to this many made-up workers")
    ap.add_argument("--per-step", type=int, default=1, help="nets added per version")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between bursts (0 = write all at once)")
    ap.add_argument("--burst", type=int, default=1, help="versions written back to back before each pause, "
                                                         "so several workers are seen working at once")
    args = ap.parse_args()

    text = Path(args.routed).read_text()
    items = list(ITEM_RE.finditer(text))
    if not items:
        raise SystemExit("no tracks or vias found in the routed board")
    net_of = [int(m.group(1)) if (m := NET_RE.search(it.group(0))) else 0 for it in items]
    order: list[int] = []
    for n in net_of:
        if n not in order:
            order.append(n)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    def write(name: str, keep_nets: set[int]) -> None:
        parts, last = [], 0
        for it, n in zip(items, net_of):
            parts.append(text[last:it.start()])
            if n in keep_nets:
                parts.append(it.group(0))
            last = it.end()
        parts.append(text[last:])
        (out / name).write_text("".join(parts))

    write("v000.kicad_pcb", set())
    print("v000: no copper", flush=True)
    kept: set[int] = set()
    step = 0
    for i in range(0, len(order), args.per_step):
        if args.delay and step % max(1, args.burst) == 0:
            time.sleep(args.delay)
        elif args.delay:
            time.sleep(0.05)                      # keep modification times in order within a burst
        kept |= set(order[i:i + args.per_step])
        step += 1
        who = f"__w{(step - 1) % args.agents + 1}" if args.agents else ""
        write(f"v{step:03d}{who}.kicad_pcb", kept)
        print(f"v{step:03d}{who}: {len(kept)}/{len(order)} nets", flush=True)


if __name__ == "__main__":
    main()
