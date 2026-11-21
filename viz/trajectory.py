"""Design-iteration view for an agent-swarm run (runs/swarm/<run_id>/history.jsonl).

One panel per worst-corner metric vs candidate index, colored by worker, spec line + failing side
shaded, filled markers = candidate passed every spec at every PVT point.  Tolerates missing fields.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, NullFormatter

from sizing import opamp2stg as oa
from viz.common import BAD, GOOD, INK, INK2, MUTED, WORKER_COLORS, apply_style

# metric key, panel label, scale, log axis, spec fallback (op, bound) in display units
PANELS = [
    ("pm_deg", "worst PM (deg)", 1.0, False),
    ("dc_gain_db", "worst DC gain (dB)", 1.0, False),
    ("ugf_hz", "worst UGF (MHz)", 1e-6, True),
    ("i_core_a", "max core current (µA)", 1e6, False),
    ("min_sat_margin_v", "min sat. margin (mV)", 1e3, False),
]


def load_history(run_dir: Path) -> list[dict]:
    path = run_dir / "history.jsonl" if run_dir.is_dir() else run_dir
    recs = []
    for i, line in enumerate(path.read_text().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict):
            r.setdefault("_idx", len(recs) + 1)
            recs.append(r)
    return recs


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def metric(rec: dict, key: str) -> float | None:
    """worst[key] as a float; accepts a number, [value, corner, temp], or {"value": ...}."""
    w = rec.get("worst") or {}
    v = w.get(key) if isinstance(w, dict) else None
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    elif isinstance(v, dict):
        v = v.get("value")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def where(rec: dict, key: str) -> str:
    wa = rec.get("worst_at") or {}
    if isinstance(wa, dict) and wa.get(key):
        return str(wa[key])
    w = (rec.get("worst") or {}).get(key)
    if isinstance(w, (list, tuple)) and len(w) >= 3:
        return f"{w[1]}/{w[2]}"
    return ""


def cand_no(rec: dict) -> int:
    n = rec.get("n")
    return int(n) if isinstance(n, (int, float)) else int(rec["_idx"])


def worker_of(rec: dict) -> str:
    return str(rec.get("worker") or "(unknown)")


def worker_colors(recs: list[dict]) -> dict:
    cols, k = {}, 0
    for r in recs:
        w = worker_of(r)
        if w not in cols:
            if w in ("start", "seed", "baseline", "default"):
                cols[w] = MUTED
            else:
                cols[w] = WORKER_COLORS[k % len(WORKER_COLORS)]
                k += 1
    return cols


def best_record(recs: list[dict]) -> dict | None:
    """Passing candidate with the lowest core current, else the one with the fewest failures."""
    if not recs:
        return None
    passed = [r for r in recs if r.get("passed")]
    if passed:
        return min(passed, key=lambda r: metric(r, "i_core_a") or float("inf"))

    def nfail(r):
        n = r.get("n_failures")
        return n if isinstance(n, (int, float)) else len(r.get("failures") or []) or float("inf")
    return min(recs, key=nfail)


def plot_trajectory(recs: list[dict], spec: dict | None = None, title: str = "swarm run",
                    best: dict | None = None):
    apply_style()
    spec = spec or oa.DEFAULT_SPEC
    cols = worker_colors(recs)
    fig, axes = plt.subplots(len(PANELS), 1, figsize=(11, 10.5), sharex=True,
                             gridspec_kw=dict(hspace=0.12))
    fig.subplots_adjust(top=0.9)
    xs = [cand_no(r) for r in recs]
    xmax = max(xs) if xs else 1
    first_pass = next((cand_no(r) for r in recs if r.get("passed")), None)
    # iteration boundaries
    bounds = [(cand_no(r), r.get("iter")) for k, r in enumerate(recs)
              if k > 0 and r.get("iter") is not None and r.get("iter") != recs[k - 1].get("iter")]
    for ax, (key, label, scale, logy) in zip(axes, PANELS):
        for x, _ in bounds:
            ax.axvline(x - 0.5, color="#d8d7d2", lw=0.8, zorder=0)
        sp = spec.get(key)
        if key == "min_sat_margin_v":
            sp = (">=", 0.0)
        vals = [(cand_no(r), metric(r, key), r) for r in recs]
        vals = [(x, v * scale, r) for x, v, r in vals if v is not None and (not logy or v > 0)]
        if logy:
            ax.set_yscale("log")
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v:g}"))
            ax.yaxis.set_minor_formatter(NullFormatter())
        for x, v, r in vals:
            c = cols[worker_of(r)]
            ok = bool(r.get("passed"))
            ax.plot([x], [v], marker="o", ms=7.5 if ok else 6.5, mfc=c if ok else "white", mec=c,
                    mew=1.8, ls="", zorder=4)
        if best is not None:
            b = metric(best, key)
            if b is not None and (not logy or b > 0):
                ax.plot([cand_no(best)], [b * scale], marker="*", ms=17, mfc=GOOD if best.get("passed") else BAD,
                        mec="white", mew=1.0, ls="", zorder=5)
        if sp:
            op, bound = sp
            bound *= scale
            ax.axhline(bound, color=BAD, lw=1.1, ls=(0, (4, 3)), zorder=2)
            lo, hi = ax.get_ylim()
            if vals:
                vv = [v for _, v, _ in vals] + [bound]
                pad = (max(vv) - min(vv)) * 0.12 or abs(bound) * 0.1 or 1
                lo, hi = (min(vv) / 1.6, max(vv) * 1.6) if logy else (min(vv) - pad, max(vv) + pad)
            ax.set_ylim(lo, hi)
            if op in (">=", ">"):
                ax.axhspan(lo, bound, color=BAD, alpha=0.06, lw=0, zorder=0)
            else:
                ax.axhspan(bound, hi, color=BAD, alpha=0.06, lw=0, zorder=0)
            ax.text(1.005, bound, f"{'≥' if op.startswith('>') else '≤'} {bound:g}", transform=ax.get_yaxis_transform(),
                    va="center", ha="left", fontsize=9.5, color=BAD)
        if not vals:
            ax.text(0.5, 0.5, "no data", transform=ax.transAxes, ha="center", va="center", color=MUTED)
        ax.set_ylabel(label, rotation=0, ha="right", va="center", fontsize=10.5, color=INK2)
        if first_pass is not None:
            ax.axvline(first_pass, color=GOOD, lw=1.2, alpha=0.8, zorder=1)
    axes[0].set_xlim(0.3, xmax + 0.7)
    axes[-1].set_xlabel("candidate # (evaluation order)")
    if first_pass is not None:
        axes[0].text(first_pass, 1.02, f" first pass: #{first_pass}", transform=axes[0].get_xaxis_transform(),
                     color=GOOD, fontsize=10, fontweight="bold", ha="left", va="bottom")
    for x, it in bounds:
        axes[-1].text(x - 0.4, 0.02, f"it{it}", transform=axes[-1].get_xaxis_transform(), fontsize=8.5,
                      color=MUTED, ha="left", va="bottom")
    handles = [Line2D([], [], marker="o", ls="", mfc=c, mec=c, ms=8, label=w) for w, c in cols.items()]
    handles += [Line2D([], [], marker="o", ls="", mfc="white", mec=INK2, mew=1.6, ms=7, label="failed spec"),
                Line2D([], [], marker="o", ls="", mfc=INK2, mec=INK2, ms=7, label="passed all 15 PVT")]
    if best is not None:
        handles.append(Line2D([], [], marker="*", ls="", mfc=GOOD if best.get("passed") else BAD, mec="white",
                              ms=14, label=f"best #{cand_no(best)}"))
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.125, 0.99), ncol=min(len(handles), 6),
               fontsize=10, title="worker", title_fontsize=10, alignment="left", columnspacing=1.2)
    n_pass = sum(1 for r in recs if r.get("passed"))
    fig.suptitle(f"{title}: {len(recs)} candidates, {n_pass} passed", x=0.125, y=1.005, ha="left",
                 fontsize=13, fontweight="bold", color=INK)
    return fig
