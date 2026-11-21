"""Render a recorded repair run as an MP4: each agent's own board, zoomed on the open connections.

Reads a live folder in the solver/viewer contract (pcb/CONTRACT.md): <seq>__<agent>.kicad_pcb
versions plus events.jsonl. Every agent in the stepper works its own copy of the board, so each
gets its own panel; a version credited to an agent updates only that agent's panel. Open
connections (dashed yellow) are recomputed from each saved board (pcb/connectivity.py); new
copper glows for a moment after it appears. Optionally a control run (random policy, same board,
same step budget) is drawn as a grey panel and a grey line in the chart.

    .venv/bin/python -m pcb.make_video --run runs/pcb/dev_step_agent/live/0658_sweet-sixteen_sweet-sixteen \
        --control runs/pcb/dev_step_random/live/0658_sweet-sixteen_sweet-sixteen --out runs/video/0658.mp4

Timing: a version's time is its file modification time, aligned to the router's first note in
events.jsonl (or manifest.json when the viewer recorded the run). Run time is compressed by --speed.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import LineCollection, PatchCollection  # noqa: E402
from matplotlib.patches import Circle, Rectangle  # noqa: E402
from matplotlib.transforms import Affine2D  # noqa: E402

from pcb.board_parse import copper, scene  # noqa: E402
from pcb.connectivity import open_connections  # noqa: E402

BG, PANEL, INK, INK2, LINE = "#0c110f", "#141b18", "#e6ebe7", "#8e9a93", "#26302b"
FRONT, BACK, VIA, AIR, GLOW = "#d9534a", "#4f8fe6", "#d8dcd9", "#ffe14d", "#fff6c2"
AGENT_COLORS = {"careful": "#6fd3b4", "bold": "#ff9a62", "layer-first": "#b9a2f5", "power-aware": "#f08bb4",
                "random": "#9aa29e",
                "carbon": "#e6ebe7", "router": "#8e9a93"}
FPS = 30
VERSION_RE = re.compile(r"^(\d+)__(.+)\.kicad_pcb$")
NOW_OPEN = re.compile(r"now (\d+) open")


def load_run(folder: Path) -> dict:
    folder = Path(folder)
    events = []
    if (folder / "events.jsonl").is_file():
        for line in (folder / "events.jsonl").read_text().splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    files = sorted(p for p in folder.glob("*.kicad_pcb") if VERSION_RE.match(p.name))
    man = folder / "manifest.json"
    if man.is_file():
        times = {v["file"]: v["t"] for v in json.loads(man.read_text())["versions"]}
    else:
        router_t = next((e["t"] for e in events if e.get("agent") == "router"), 0.0)
        m0 = files[0].stat().st_mtime
        times = {p.name: router_t + (p.stat().st_mtime - m0) for p in files}
    sc = scene(files[0])
    versions = []
    for p in files:
        seq, agent = VERSION_RE.match(p.name).groups()
        cu = copper(p)
        versions.append({"t": times[p.name], "agent": agent, "file": p.name, "copper": cu,
                         "open": open_connections(sc, cu)})
    return {"scene": sc, "versions": versions, "events": events,
            "end_t": max([v["t"] for v in versions] + [e["t"] for e in events]) + 1.0}


def zoom_box(run: dict, margin: float = 9.0, aspect: float = 1.0) -> tuple:
    pts = [pt for o in run["versions"][0]["open"] for pt in (o["a"], o["b"])]
    bx = run["scene"]["bbox"]
    if not pts:
        return tuple(bx)
    x0, x1 = min(p[0] for p in pts) - margin, max(p[0] for p in pts) + margin
    y0, y1 = min(p[1] for p in pts) - margin, max(p[1] for p in pts) + margin
    w, h = x1 - x0, y1 - y0                   # pad to the panel's aspect ratio
    if w / h < aspect:
        cx = (x0 + x1) / 2
        x0, x1 = cx - h * aspect / 2, cx + h * aspect / 2
    else:
        cy = (y0 + y1) / 2
        y0, y1 = cy - w / aspect / 2, cy + w / aspect / 2
    return (max(bx[0], x0), max(bx[1], y0), min(bx[2], x1), min(bx[3], y1))


def clip(segs, box):
    x0, y0, x1, y1 = box
    return [s for s in segs if not (max(s["a"][0], s["b"][0]) < x0 or min(s["a"][0], s["b"][0]) > x1
                                    or max(s["a"][1], s["b"][1]) < y0 or min(s["a"][1], s["b"][1]) > y1)]


class Panel:
    """One agent's board: static pads, live copper, glow on new copper, airwires, caption."""

    def __init__(self, fig, rect, run, agent, box, label, color):
        self.run, self.agent, self.box, self.color = run, agent, box, color
        self.narrow = rect[2] < 0.2            # five panels across: smaller labels, shorter captions
        self.wrap = max(24, int(rect[2] * 1920 / 7.7))
        self.ax = fig.add_axes(rect)
        ax = self.ax
        ax.set_facecolor(PANEL)
        ax.set_xlim(box[0], box[2])
        ax.set_ylim(box[3], box[1])           # KiCad y points down
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_color(color)
            s.set_linewidth(1.6)
        pads = []
        for p in run["scene"]["pads"]:
            if not (box[0] - 3 < p["x"] < box[2] + 3 and box[1] - 3 < p["y"] < box[3] + 3):
                continue
            if p["shape"] == "round" and abs(p["w"] - p["h"]) < 1e-6:
                pads.append(Circle((p["x"], p["y"]), p["w"] / 2))
            else:
                r = Rectangle((p["x"] - p["w"] / 2, p["y"] - p["h"] / 2), p["w"], p["h"])
                r.set_transform(Affine2D().rotate_deg_around(p["x"], p["y"], -p["rot"]))
                pads.append(r)
        pc = PatchCollection(pads, facecolor="#5d6762", edgecolor="none", zorder=1)
        pc.set_transform(Affine2D().rotate_deg(0) + ax.transData)
        # rotated rectangles carry their own transforms; PatchCollection ignores them, so add them one by one
        for patch in pads:
            patch.set_facecolor("#59625e")
            patch.set_edgecolor("none")
            patch.set_zorder(1)
            if isinstance(patch, Rectangle):
                patch.set_transform(patch.get_transform() + ax.transData)
            ax.add_patch(patch)
        self.front = LineCollection([], colors=FRONT, alpha=0.9, zorder=3, capstyle="round")
        self.back = LineCollection([], colors=BACK, alpha=0.85, zorder=2, capstyle="round")
        self.glow = LineCollection([], colors=GLOW, alpha=0.0, zorder=4, capstyle="round")
        self.air = LineCollection([], colors=AIR, linestyles=(0, (2.2, 1.6)), linewidths=2.4, zorder=6)
        for c in (self.back, self.front, self.glow, self.air):
            ax.add_collection(c)
        self.vias = ax.scatter([], [], s=14, c=VIA, zorder=5, linewidths=0)
        self.title = ax.text(0.0, 1.035, label, transform=ax.transAxes, color=color,
                             fontsize=13.5 if self.narrow else 17,
                             fontweight="bold", va="bottom")
        self.badge = ax.text(1.0, 1.035, "", transform=ax.transAxes, color=INK,
                             fontsize=13.5 if self.narrow else 16,
                             va="bottom", ha="right", fontweight="bold")
        self.action = ax.text(0.02, 0.97, "", transform=ax.transAxes, color=INK, fontsize=12.5, va="top",
                              family="monospace", bbox=dict(boxstyle="round,pad=0.35", fc=BG, ec=color, lw=1.2,
                                                            alpha=0.92), zorder=10)
        self.thought = ax.text(0.0, -0.025, "", transform=ax.transAxes, color=INK2, fontsize=12, va="top",
                               linespacing=1.35, wrap=False)
        self.cur = None
        self.prev_keys: set = set()
        self.glow_segs: list = []
        self.glow_t = -99.0
        self.scale = (rect[2] * 1920) / (box[2] - box[0])   # pixels per mm, for line widths

    def lw(self, w_mm):
        return max(0.8, w_mm * self.scale * 0.75)

    def set_version(self, v, t):
        segs = clip(v["copper"]["segments"], self.box)
        keys = {s["k"] for s in v["copper"]["segments"]}
        if self.cur is not None:
            new = [s for s in segs if s["k"] not in self.prev_keys]
            self.glow_segs, self.glow_t = new, t
            self.glow.set_segments([(s["a"], s["b"]) for s in new])
            self.glow.set_linewidths([self.lw(s["w"]) * 2.6 for s in new])
        self.prev_keys, self.cur = keys, v
        for coll, side in ((self.front, "front"), (self.back, "back")):
            ss = [s for s in segs if s["side"] == side]
            coll.set_segments([(s["a"], s["b"]) for s in ss])
            coll.set_linewidths([self.lw(s["w"]) for s in ss])
        vv = [x for x in v["copper"]["vias"] if self.box[0] <= x["x"] <= self.box[2] and self.box[1] <= x["y"] <= self.box[3]]
        self.vias.set_offsets([(x["x"], x["y"]) for x in vv] or [(math.nan, math.nan)])
        self.vias.set_sizes([(x["size"] * self.scale * 0.75) ** 2 for x in vv] or [1])
        self.air.set_segments([(o["a"], o["b"]) for o in v["open"]])
        n = len(v["open"])
        self.badge.set_text(("all routed" if self.narrow else "all connections routed") if n == 0 else f"{n} open")
        self.badge.set_color("#6fd39a" if n == 0 else AIR)

    def tick(self, t, frame):
        age = t - self.glow_t
        self.glow.set_alpha(max(0.0, 0.95 * (1 - age / 4.0)) if self.glow_segs else 0.0)
        self.air.set_alpha(0.55 + 0.45 * (0.5 + 0.5 * math.sin(frame / 4.0)))

    def caption(self, ev):
        if not ev:
            return
        txt = ev.get("text", "")
        act, _, why = txt.partition(" - ")
        self.action.set_text(act.strip()[: min(46, self.wrap - 6)])
        self.thought.set_text("\n".join(textwrap.wrap(why.strip().replace("**", ""), self.wrap)[:5 if self.narrow else 4]))


def render(args) -> None:
    run = load_run(args.run)
    control = load_run(args.control) if args.control else None
    agents = []
    for v in run["versions"]:
        if v["agent"] != "router" and v["agent"] not in agents:
            agents.append(v["agent"])
    for e in run["events"]:
        a = e.get("agent")
        if a and a not in ("router", "carbon", "lead") and a not in agents and e["type"] in ("start", "note"):
            agents.append(a)
    agents = agents[:4]
    cols = agents + (["random"] if control else [])
    n = len(cols)
    fig = plt.figure(figsize=(19.2, 10.8), dpi=100, facecolor=BG)
    gap, left, top_h = 0.02, 0.035, 0.53
    pw = (1 - 2 * left - gap * (n - 1)) / n
    ph = pw * 1920 / 1080 * 1.0
    if ph > top_h:
        ph = top_h
        pw = ph * 1080 / 1920
    aspect = (pw * 1920) / (ph * 1080)
    box = zoom_box(run, aspect=aspect)
    y0 = 0.86 - ph
    panels = {}
    for i, a in enumerate(cols):
        src = control if a == "random" else run
        label = ("random (control)" if n >= 5 else "random policy (control)") if a == "random" else f"Hydrogen · {a}"
        panels[a] = Panel(fig, [left + i * (pw + gap), y0, pw, ph], src, a, box,
                          label, AGENT_COLORS.get(a, INK))
        panels[a].set_version(src["versions"][0], -99)
        panels[a].cur_src = src

    board = Path(args.run).name
    fig.text(left, 0.955, args.title or "Gemini 4 agents repair what the classic router left open",
             color=INK, fontsize=26, fontweight="bold", va="center")
    fig.text(left, 0.915, f"PCBWorld board {board}  ·  zoomed on the open connections  ·  "
             "red = top copper, blue = bottom copper, dashed yellow = still open", color=INK2, fontsize=14,
             va="center")
    lead = fig.text(left, 0.885 - 0.0, "", color=AGENT_COLORS["carbon"], fontsize=13, va="center", style="italic")
    clock = fig.text(1 - left, 0.955, "", color=INK2, fontsize=16, ha="right", va="center", family="monospace")

    # chart: open connections over run time, per agent (+ control best)
    cax = fig.add_axes([left + 0.02, 0.07, 1 - 2 * left - 0.04, 0.17])
    cax.set_facecolor(BG)
    for s in ("top", "right"):
        cax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        cax.spines[s].set_color(LINE)
    cax.tick_params(colors=INK2, labelsize=11)
    end_t = max(run["end_t"], control["end_t"] if control else 0)
    t_start = run["versions"][0]["t"]
    start_open = len(run["versions"][0]["open"])
    cax.set_xlim(0, end_t - t_start)
    cax.set_ylim(-0.3, start_open + 0.6)
    cax.set_yticks(range(0, start_open + 1))
    cax.set_ylabel("open", color=INK2, fontsize=12)
    cax.set_xlabel("seconds of run time", color=INK2, fontsize=12)
    cax.grid(axis="y", color=LINE, lw=0.6)
    lines = {}
    for a in cols:
        lines[a], = cax.step([], [], where="post", color=AGENT_COLORS.get(a, INK), lw=2.6 if a != "random" else 2.0,
                             ls="-" if a != "random" else (0, (4, 2)), label=a if a != "random" else "random (control)")
    cursor = cax.axvline(0, color=INK2, lw=1, alpha=0.6)
    leg = cax.legend(loc="upper right", frameon=False, fontsize=11, ncol=len(cols))
    for txt in leg.get_texts():
        txt.set_color(INK2)

    def series(src, agent, upto):
        xs, ys = [0.0], [start_open]
        for v in src["versions"][1:]:
            if v["agent"] == agent and v["t"] <= upto:
                xs.append(v["t"] - t_start)
                ys.append(len(v["open"]))
        xs.append(max(0.0, upto - t_start))
        ys.append(ys[-1])
        return xs, ys

    intro, outro = 1.5, 3.0
    FPS = args.fps                            # frames rendered per video second (the MP4 is always 30 fps)
    run_len = (end_t - t_start) / args.speed
    total = intro + run_len + outro
    nframes = int(total * FPS)
    tmp = Path(tempfile.mkdtemp(prefix="pcbvid_"))
    applied = {a: 0 for a in cols}
    seen_events = {id(e) for e in []}
    shown_lead = False
    for f in range(nframes):
        vt = f / FPS
        t = t_start + max(0.0, min(vt - intro, run_len)) * args.speed
        for a in cols:
            src = control if a == "random" else run
            want = "random" if a == "random" else a
            vs = src["versions"]
            while applied[a] + 1 < len(vs) and vs[applied[a] + 1]["t"] <= t:
                applied[a] += 1
                v = vs[applied[a]]
                if a == "random" or v["agent"] == want:
                    if a == "random":   # control: show its best-so-far board
                        best = min(vs[: applied[a] + 1], key=lambda x: len(x["open"]))
                        if best is not panels[a].cur:
                            panels[a].set_version(best, t)
                    else:
                        panels[a].set_version(v, t)
            evs = [e for e in src["events"] if e["type"] == "note" and e.get("agent") == want and e["t"] <= t]
            if a != "random":
                panels[a].caption(evs[-1] if evs else None)
            else:
                ra = [e for e in src["events"] if e["type"] == "start" and e["t"] <= t]
                if ra:
                    panels[a].action.set_text(f"{ra[-1].get('agent', '')}: {ra[-1].get('net', '')}"[: min(46, panels[a].wrap - 6)])
            panels[a].tick(t, f)
            if a == "random":
                xs, ys = [0.0], [start_open]
                best_n = start_open
                for v in src["versions"][1:]:
                    if v["t"] <= t and len(v["open"]) < best_n:
                        best_n = len(v["open"])
                        xs.append(v["t"] - t_start)
                        ys.append(best_n)
                xs.append(max(0.0, t - t_start))
                ys.append(ys[-1])
            else:
                xs, ys = series(src, a, t)
            lines[a].set_data(xs, ys)
        if not shown_lead:
            le = [e for e in run["events"] if e.get("agent") in ("carbon", "lead") and e["t"] <= t]
            if le:
                note = re.sub(r"[*`]", "", le[0].get("text", "")).replace("\n", " ")
                lead.set_text("Carbon lead, from the board picture: " + textwrap.shorten(note, 150, placeholder=" …"))
                shown_lead = True
        cursor.set_xdata([t - t_start] * 2)
        clock.set_text(f"run time {t - t_start:5.1f} s   ×{args.speed:g}")
        fig.savefig(tmp / f"f{f:05d}.png", facecolor=BG)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(tmp / "f%05d.png"),
                    "-vf", "fps=30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", str(out)], check=True)
    if args.poster:
        shutil.copy(tmp / f"f{nframes - 1:05d}.png", out.with_suffix(".png"))
    shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps({"out": str(out), "seconds": round(total, 1), "frames": nframes,
                      "final_open": {a: len(panels[a].cur["open"]) for a in cols}}))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="live folder of the agent run")
    ap.add_argument("--control", help="live folder of a control run on the same board (random policy)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--speed", type=float, default=6.0, help="run seconds per video second")
    ap.add_argument("--title")
    ap.add_argument("--fps", type=int, default=30, help="frames rendered per video second (lower = faster render)")
    ap.add_argument("--poster", action="store_true", help="also save the last frame as a PNG")
    render(ap.parse_args())


if __name__ == "__main__":
    main()
