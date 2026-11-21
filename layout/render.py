"""Floorplan pictures of a layout plan (PNG + SVG): what the audience and the Neon reviewer see.

Layout planning with estimated parasitics - not routed, no DRC/LVS.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle

from layout import DISCLAIMER
from layout import model as lm

COLORS = {"in": "#2a78d6", "load": "#eb6834", "tail": "#1baf7a", "drv": "#6c56d9", "sink": "#e87ba4"}
ROLE_NAMES = {"in": "input pair", "load": "mirror load", "tail": "tail (cascode)", "drv": "stage-2 driver",
              "sink": "stage-2 sink"}
INK, MUTED = "#1b1f24", "#5d6672"


def _title(res: dict | None, geom: dict, label: str) -> str:
    t = f"{label}:  {geom['area']:.0f} um²"
    if res:
        t += f"  |  offset {res['offset_uV']:.1f} uV  |  "
        if not res.get("simulated"):
            t += "NOT SIMULATED"
        else:
            t += "15-PVT PASS" if res["passed"] else "15-PVT FAIL"
        if res.get("violations"):
            t += "  |  RULE VIOLATION"
    return t


def draw(ax, params: dict, plan: dict, res: dict | None = None, label: str = "plan",
         extent: tuple | None = None, short_title: bool = False) -> dict:
    """Draw one floorplan into `ax` (call after the axes position is final)."""
    geom = lm.layout(params, plan)
    W, H = geom["width"], geom["height"]
    ext_w, ext_h = extent or (W, H)
    pad = 0.07 * max(ext_w, ext_h) + 1.5
    xr, yr = ext_w + 2 * pad, ext_h + 1.9 * pad
    ax.set_xlim(-pad, ext_w + pad)
    ax.set_ylim(-pad * 1.3, ext_h + pad * 0.6)
    ax.set_aspect("equal")
    box, (fw, fh) = ax.get_position(), ax.figure.get_size_inches()
    pts_per_um = 72.0 * min(box.width * fw / xr, box.height * fh / yr)
    if geom["nwell"]:
        x, y, w, h = geom["nwell"]
        ax.add_patch(Rectangle((x, y), w, h, fill=True, fc="#fff6e0", ec="#b8860b", lw=1.6, ls="--", zorder=1))
        ax.text(x + w, y + h, "N-well ", color="#8a6508", fontsize=9, va="bottom", ha="right", zorder=5)
    for g in geom["groups"].values():       # light active-area backdrop per group
        ax.add_patch(Rectangle((g["x"], g["y"]), g["w"], g["h"], fc=COLORS[g["role"]], alpha=0.10,
                               ec=COLORS[g["role"]], lw=0.8, zorder=2))
    for f in geom["fingers"]:
        if f["dummy"]:
            ax.add_patch(Rectangle((f["x"], f["y"]), f["w"], f["h"], fc="#d9dde2", ec="#7b838d", lw=0.6,
                                   hatch="////", zorder=3))
            continue
        ax.add_patch(Rectangle((f["x"], f["y"]), f["w"], f["h"], fc=COLORS[f["role"]], ec="white", lw=0.5,
                               zorder=3))
        if f["dev"] in ("A", "B"):
            fs = min(13.0, f["w"] * pts_per_um * 1.05, f["h"] * pts_per_um * 0.8)
            if fs >= 3.5:
                ax.text(f["x"] + f["w"] / 2, f["y"] + f["h"] / 2, f["dev"], color="white", fontsize=fs,
                        fontweight="bold", ha="center", va="center", zorder=4)
    ax.add_patch(Rectangle((0, 0), W, H, fill=False, ec=INK, lw=1.4, zorder=6))
    ax.annotate("", xy=(0, -pad * 0.4), xytext=(W, -pad * 0.4),
                arrowprops=dict(arrowstyle="<->", color=INK, lw=1.2))
    ax.text(W / 2, -pad * 0.5, f"{W:.1f} um", ha="center", va="top", fontsize=12, color=INK)
    ax.annotate("", xy=(-pad * 0.35, 0), xytext=(-pad * 0.35, H),
                arrowprops=dict(arrowstyle="<->", color=INK, lw=1.2))
    ax.text(-pad * 0.5, H / 2, f"{H:.1f} um", ha="right", va="center", rotation=90, fontsize=12, color=INK)
    if short_title:
        ax.set_title(label, fontsize=14, fontweight="bold", color=INK, pad=8)
    else:
        ax.set_title(_title(res, geom, label), fontsize=13, fontweight="bold", color=INK, pad=16)
        sub = lm.plan_short(geom["plan"]) + f"   aspect {geom['aspect']:.2f}"
        ax.text(0.5, 1.005, sub, transform=ax.transAxes, ha="center", va="bottom", fontsize=8, color=MUTED)
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    return geom


def _legend(fig, ncol: int = 6) -> None:
    handles = [Patch(fc=COLORS[r], ec="none", label=ROLE_NAMES[r]) for r in lm.ROLES]
    handles.append(Patch(fc="#d9dde2", ec="#7b838d", hatch="////", label="dummy finger"))
    fig.legend(handles=handles, loc="lower center", ncol=ncol, fontsize=10, frameon=False,
               bbox_to_anchor=(0.5, 0.035))
    fig.text(0.5, 0.008, DISCLAIMER, ha="center", va="bottom", fontsize=9.5, color=MUTED, style="italic")


def _save(fig, path: Path, svg: bool) -> None:
    path = Path(path)
    fig.savefig(path, dpi=130, facecolor="white")
    if svg:
        fig.savefig(path.with_suffix(".svg"), facecolor="white")
    plt.close(fig)


def save_floorplan(params: dict, plan: dict, res: dict | None, path, label: str = "plan",
                   svg: bool = False) -> None:
    fig = plt.figure(figsize=(8.5, 7.0))
    ax = fig.add_axes([0.03, 0.15, 0.94, 0.74])
    draw(ax, params, plan, res, label)
    _legend(fig, ncol=3)
    _save(fig, path, svg)


def _stat_rows(res: dict | None, geom: dict) -> list[str]:
    rows = [f"{geom['area']:.0f} um²", f"{geom['width']:.1f} x {geom['height']:.1f} um",
            f"{geom['aspect']:.2f}"]
    if res:
        rows += [f"{res['offset_uV']:.1f} uV",
                 "-" if res.get("pm_deg") is None else f"{res['pm_deg']:.1f} deg",
                 "-" if res.get("ugf_hz") is None else f"{res['ugf_hz'] / 1e6:.2f} MHz",
                 ("PASS" if res["passed"] else "FAIL") if res.get("simulated") else "not simulated",
                 "none" if not res["violations"] else f"{len(res['violations'])} violated"]
    else:
        rows += ["-"] * 5
    return rows


def save_before_after(params: dict, naive_plan: dict, naive_res: dict | None, plan: dict,
                      res: dict | None, path, svg: bool = True) -> None:
    """Naive vs planned, drawn at the SAME scale, plus the judge's numbers."""
    g0, g1 = lm.layout(params, naive_plan), lm.layout(params, plan)
    H = max(g0["height"], g1["height"])
    pad = lambda w: 0.07 * max(w, H) + 1.5
    xr0, xr1 = g0["width"] + 2 * pad(g0["width"]), g1["width"] + 2 * pad(g1["width"])
    yr = H + 1.9 * max(pad(g0["width"]), pad(g1["width"]))
    fig_w, fig_h = 15.0, 8.0
    ax_h = 0.70                                     # axes height as a fraction of the figure
    scale = min(ax_h * fig_h / yr, 0.60 * fig_w / (xr0 + xr1))     # inches per um, shared
    w0, w1 = xr0 * scale / fig_w, xr1 * scale / fig_w
    h = yr * scale / fig_h
    y0 = 0.13 + (ax_h - h) / 2
    ax0 = fig = None
    fig = plt.figure(figsize=(fig_w, fig_h))
    ax0 = fig.add_axes([0.02, y0, w0, h])
    ax1 = fig.add_axes([0.02 + w0 + 0.01, y0, w1, h])
    draw(ax0, params, naive_plan, naive_res, "BEFORE (naive)", extent=(g0["width"], H), short_title=True)
    draw(ax1, params, plan, res, "AFTER (planned)", extent=(g1["width"], H), short_title=True)
    for a in (ax0, ax1):                            # identical y range -> identical um scale
        a.set_ylim(-1.3 * max(pad(g0["width"]), pad(g1["width"])), H + 0.6 * max(pad(g0["width"]), pad(g1["width"])))
    red = 100.0 * (1.0 - g1["area"] / g0["area"])
    fig.suptitle(f"Layout plan: bounding-box area {g0['area']:.0f} → {g1['area']:.0f} um² ({red:.0f}% smaller)"
                 + (f", systematic offset {naive_res['offset_uV']:.1f} → {res['offset_uV']:.1f} uV"
                    if naive_res and res else ""), fontsize=16, fontweight="bold", color=INK, y=0.975)
    tx = 0.02 + w0 + 0.01 + w1 + 0.03               # numbers panel on the right
    ta = fig.add_axes([tx, 0.14, max(0.2, 0.98 - tx), 0.72]); ta.axis("off")
    names = ["area", "width x height", "aspect (w/h)", "systematic offset", "worst phase margin",
             "worst unity-gain freq", "15-PVT + parasitics", "planning constraints"]
    c0, c1 = _stat_rows(naive_res, g0), _stat_rows(res, g1)
    ta.text(0.42, 0.97, "before", fontsize=13, fontweight="bold", color=MUTED, ha="left", va="top")
    ta.text(0.72, 0.97, "after", fontsize=13, fontweight="bold", color=INK, ha="left", va="top")
    for i, (n, a, b) in enumerate(zip(names, c0, c1)):
        y = 0.88 - i * 0.085
        ta.text(0.0, y, n, fontsize=12, color=MUTED, ha="left", va="top")
        ta.text(0.42, y, a, fontsize=12.5, color=MUTED, ha="left", va="top")
        ta.text(0.72, y, b, fontsize=12.5, color=INK, fontweight="bold", ha="left", va="top")
    p = lm.normalize_plan(plan)
    lines = ["plan:  fingers " + ", ".join(f"{r} x{p['nf'][r]}" for r in lm.ROLES),
             f"input pair {p['pattern']['in']}, mirror load {p['pattern']['load']}, "
             f"dummies {'on' if p['dummies'] else 'off'}",
             "rows (top to bottom): " + " | ".join(" + ".join(r) for r in p["rows"]),
             f"assumed: Vth gradient {lm.VTH_GRADIENT_UV_PER_UM:g} uV/um, wire cap {lm.WIRE_CAP_F_PER_UM*1e15:g} fF/um",
             "both drawings use the same scale"]
    for i, l in enumerate(lines):
        ta.text(0.0, 0.17 - i * 0.055, l, fontsize=9.5, color=MUTED, ha="left", va="top")
    _legend(fig)
    _save(fig, path, svg)
