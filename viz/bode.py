"""Corner-fan Bode plot: loop gain magnitude + phase at all PVT points, optional before/after overlay."""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FixedLocator, FuncFormatter

from sizing import opamp2stg as oa
from viz.common import (BAD, CORNER_COLOR, GOOD, INK, INK2, MUTED, TEMP_STYLE, WARN, apply_style,
                        pt_label)

BEFORE_GRAY = "#b4b3ad"
F_MIN, F_MAX = 1e3, 1e10


def _hz(f, _pos=None) -> str:
    for unit, k in (("GHz", 1e9), ("MHz", 1e6), ("kHz", 1e3)):
        if f >= k:
            return f"{f / k:g} {unit}"
    return f"{f:g} Hz"


def _curves(r: oa.PointResult):
    c = r.curves
    if not c:
        return None
    return np.asarray(c["freq_hz"]), np.asarray(c["lg_db"]), np.asarray(c["lg_ph"])


def _worst(er: oa.EvalResult, metric: str):
    w = er.worst.get(metric)
    return w if w and w[0] is not None else None


def plot_bode(after: oa.EvalResult, before: oa.EvalResult | None = None, spec: dict | None = None,
              after_label: str = "design", before_label: str = "default", story: bool = False):
    """after/before must have been evaluated with curves=True.
    spec: the datasheet spec (red); the guard-banded design target (oa.guarded(spec)) is drawn in amber.
    story=True: larger type for the projector story page, no title, compact legend."""
    apply_style()
    k = 1.0
    if story:
        k = 1.45
        plt.rcParams.update({"font.size": 11 * k, "axes.labelsize": 11 * k, "xtick.labelsize": 10 * k,
                             "ytick.labelsize": 10 * k, "legend.fontsize": 10 * k})
    spec = spec or getattr(after, "spec_nominal", None) or after.spec or oa.DEFAULT_SPEC
    target = oa.guarded(spec)
    fig, (am, ap) = plt.subplots(2, 1, figsize=(13, 8.0) if story else (11, 7.4), sharex=True,
                                 gridspec_kw=dict(height_ratios=[1.15, 1], hspace=0.08))
    pm_spec = spec.get("pm_deg", (">=", None))[1]
    ugf_spec = spec.get("ugf_hz", (">=", None))[1]
    gain_spec = spec.get("dc_gain_db", (">=", None))[1]
    pm_tgt = target.get("pm_deg", (">=", None))[1]
    ugf_tgt = target.get("ugf_hz", (">=", None))[1]

    # ---- spec regions (faint red = fails the spec)
    if ugf_spec:
        am.axvspan(F_MIN, ugf_spec, color=BAD, alpha=0.06, lw=0, zorder=0)
        for ax in (am, ap):
            ax.axvline(ugf_spec, color=BAD, lw=1.0, ls=(0, (4, 3)), alpha=0.7, zorder=1)
        am.text(ugf_spec * 0.88, -55, f"UGF < {ugf_spec / 1e6:g} MHz\nfails spec", ha="right",
                va="bottom", fontsize=9.5 * k, color=BAD)
        if ugf_tgt and ugf_tgt != ugf_spec:
            for ax in (am, ap):
                ax.axvspan(ugf_spec, ugf_tgt, color=WARN, alpha=0.35, lw=0, zorder=0)
    if gain_spec is not None:
        am.axhline(gain_spec, color=INK2, lw=0.9, ls=(0, (4, 3)), zorder=1)
        am.text(F_MAX / 1.3, gain_spec + 2.5, f"DC loop gain ≥ {gain_spec:g} dB", fontsize=9.5 * k,
                color=INK2, va="bottom", ha="right")
    am.axhline(0, color=INK, lw=0.9, zorder=1)
    if pm_spec is not None:
        lim = -(180 - pm_spec)
        ap.axhspan(-300, lim, color=BAD, alpha=0.06, lw=0, zorder=0)
        ap.axhline(lim, color=BAD, lw=1.0, ls=(0, (4, 3)), alpha=0.7, zorder=1)
        ap.text(F_MIN * 1.3, lim - 4, f"phase at UGF must stay above {lim:g}°  (PM ≥ {pm_spec:g}°)",
                fontsize=9.5 * k, color=BAD, va="top")
        if pm_tgt is not None and pm_tgt != pm_spec:
            ap.axhspan(lim, -(180 - pm_tgt), color=WARN, alpha=0.35, lw=0, zorder=0)
            ap.text(F_MIN * 1.3, -(180 - pm_tgt) + 3, f"guard band: target PM ≥ {pm_tgt:g}°",
                    fontsize=9.5 * k, color="#8a5a00", va="bottom")
    ap.axhline(-180, color=INK, lw=0.9, zorder=1)

    # ---- curves
    def draw(er: oa.EvalResult, bold: bool):
        for r in er.points:
            c = _curves(r)
            if c is None:
                continue
            f, db, ph = c
            col = CORNER_COLOR.get(r.corner, INK2) if bold else BEFORE_GRAY
            ls = TEMP_STYLE.get(int(r.temp), "-")
            kw = dict(color=col, ls=ls, lw=1.7 if bold else 1.1, alpha=0.95 if bold else 0.9,
                      zorder=3 if bold else 2)
            am.plot(f, db, **kw)
            ap.plot(f, ph, **kw)
            if r.ugf_hz and r.pm_deg is not None:
                mk = dict(marker="o", ms=6.5, mfc=col, mec="white", mew=1.2, zorder=5) if bold else \
                    dict(marker="o", ms=4.5, mfc=BEFORE_GRAY, mec="white", mew=0.8, zorder=4)
                am.plot([r.ugf_hz], [0], **mk)
                ap.plot([r.ugf_hz], [r.pm_deg - 180], **mk)

    if before is not None:
        draw(before, bold=False)
    draw(after, bold=True)

    am.set_xscale("log")
    am.set_xlim(F_MIN, F_MAX)
    ap.xaxis.set_major_locator(FixedLocator([10.0 ** e for e in range(3, 11)]))
    ap.xaxis.set_major_formatter(FuncFormatter(_hz))
    am.set_ylim(-60, 80)
    ap.set_ylim(-280, 15)
    ap.set_yticks(np.arange(-270, 1, 45))
    am.set_ylabel("loop gain  |T|  (dB)")
    ap.set_ylabel("phase ∠T  (deg)")
    ap.set_xlabel("frequency")
    am.tick_params(labelbottom=False)

    # ---- worst-case PM / UGF callouts
    lines = []
    for er, lab, col in ((after, after_label, INK), (before, before_label, MUTED)):
        if er is None:
            continue
        pm, ugf = _worst(er, "pm_deg"), _worst(er, "ugf_hz")
        if pm:
            ok = pm_spec is None or pm[0] >= pm_spec
            lines.append((f"{lab}: worst PM {pm[0]:.1f}° @ {pt_label(pm[1], pm[2])}"
                          + (f", min UGF {ugf[0] / 1e6:.1f} MHz @ {pt_label(ugf[1], ugf[2])}" if ugf else ""),
                          col, ok))
    y = 0.96
    for text, col, ok in lines:
        ap.text(0.985, y, ("✓ " if ok else "✗ ") + text, transform=ap.transAxes, ha="right", va="top",
                fontsize=10.5 * k, color=col, fontweight="bold" if col == INK else "normal",
                bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=GOOD if ok else BAD, lw=1.0, alpha=0.95))
        y -= 0.115 * k

    # ---- legend (corners = color, temperature = line style)
    handles = [Line2D([], [], color=CORNER_COLOR[c], lw=2.2, label=c) for c in oa.CORNERS]
    handles += [Line2D([], [], color=INK2, lw=1.6, ls=TEMP_STYLE[t], label=f"{t} °C") for t in oa.TEMPS]
    handles += [Line2D([], [], marker="o", ls="", mfc=INK2, mec="white", ms=7, label="unity-gain crossing")]
    if before is not None:
        handles += [Line2D([], [], color=BEFORE_GRAY, lw=1.3, label=f"{before_label} (before)")]
    handles += [Patch(fc=BAD, alpha=0.12, label="fails spec")]
    if (pm_tgt is not None and pm_tgt != pm_spec) or (ugf_tgt and ugf_tgt != ugf_spec):
        handles += [Patch(fc=WARN, alpha=0.45, label="guard band")]
    if story:   # compact legend below the plot: what is colored vs gray, and the zones
        sh = [Line2D([], [], color=CORNER_COLOR["tt"], lw=2.4, label=f"{after_label} (15 corners, one color per process corner)")]
        if before is not None:
            sh.append(Line2D([], [], color=BEFORE_GRAY, lw=2.0, label=f"{before_label}"))
        sh += [Line2D([], [], marker="o", ls="", mfc=INK2, mec="white", ms=9, label="gain = 1× crossing"),
               Patch(fc=BAD, alpha=0.15, label="fails spec")]
        if (pm_tgt is not None and pm_tgt != pm_spec) or (ugf_tgt and ugf_tgt != ugf_spec):
            sh.append(Patch(fc=WARN, alpha=0.45, label="guard band"))
        ap.legend(handles=sh, loc="upper left", bbox_to_anchor=(0.0, -0.2), ncol=3, handlelength=2.0,
                  columnspacing=1.6, frameon=False)
    else:
        am.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1.0), handlelength=2.6,
                  title="corner / temp", title_fontsize=10 * k, alignment="left")
    if not story:
        title = f"Loop gain at 15 PVT points — {after_label}"
        if before is not None:
            title += f" (bold) vs {before_label} (gray)"
        am.set_title(title)
    return fig
