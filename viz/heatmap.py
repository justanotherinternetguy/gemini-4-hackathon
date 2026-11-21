"""Saturation heatmap: signal-path devices x PVT points, cell = (|Vds|-|Vdsat|) - required margin, in mV."""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import Patch, Rectangle

from sizing import opamp2stg as oa
from viz.common import BAD, DEVICES, INK, INK2, ROLE_COLOR, ROLE_SHORT, apply_style, device_at

CMAP = LinearSegmentedColormap.from_list("sat", ["#b3261e", "#e88a84", "#f0efec", "#86b6ef", "#1c5cab"])
CLIP_MV = 150.0


def margin_matrix(er: oa.EvalResult):
    """-> (excess mV [dev x point], raw margin mV, column labels). NaN where the device is missing."""
    pts = list(er.points)
    ex = np.full((len(DEVICES), len(pts)), np.nan)
    raw = np.full_like(ex, np.nan)
    for j, r in enumerate(pts):
        for i, (pre, _, role, _) in enumerate(DEVICES):
            d = device_at(r, pre)
            if d is not None:
                raw[i, j] = d["margin"] * 1e3
                ex[i, j] = (d["margin"] - oa.SAT_MARGIN[role]) * 1e3
    return ex, raw, [(r.corner, r.temp) for r in pts]


def _draw(ax, er: oa.EvalResult, label: str, norm):
    ex, raw, cols = margin_matrix(er)
    nr, nc = ex.shape
    ax.imshow(np.clip(ex, -CLIP_MV, CLIP_MV), cmap=CMAP, norm=norm, aspect="auto",
              interpolation="nearest", extent=(-0.5, nc - 0.5, nr - 0.5, -0.5))
    for i in range(nr):
        for j in range(nc):
            v = ex[i, j]
            if np.isnan(v):
                ax.text(j, i, "n/a", ha="center", va="center", fontsize=8.5, color=INK2)
                continue
            strong = abs(v) > 0.55 * CLIP_MV
            tri = raw[i, j] < 0
            ax.text(j, i, f"{v:.0f}", ha="center", va="center", fontsize=9.5, zorder=5,
                    color="white" if strong else INK, fontweight="bold" if v < 0 else "normal",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none") if tri else None)
            if tri:                  # triode: light hatch + dark frame
                ax.add_patch(Rectangle((j - 0.5, i - 0.5), 1, 1, fill=False, hatch="///", ec="none",
                                       hatchcolor="#6b6a66", lw=0, zorder=3))
                ax.add_patch(Rectangle((j - 0.47, i - 0.47), 0.94, 0.94, fill=False, ec=INK, lw=1.8, zorder=4))
            elif v < 0:              # saturated but below the required margin: red frame
                ax.add_patch(Rectangle((j - 0.47, i - 0.47), 0.94, 0.94, fill=False, ec=BAD, lw=1.8, zorder=3))
    # corner groups
    for k in range(1, nc):
        if cols[k][0] != cols[k - 1][0]:
            ax.axvline(k - 0.5, color="white", lw=4)
    corners = []
    for j, (c, _) in enumerate(cols):
        if not corners or corners[-1][0] != c:
            corners.append([c, j, j])
        corners[-1][2] = j
    for c, a, b in corners:
        ax.text((a + b) / 2, -0.75, c, ha="center", va="bottom", fontsize=11, fontweight="bold", color=INK)
    # role stage separators
    roles = [d[2] for d in DEVICES]
    for i in range(1, nr):
        if roles[i] != roles[i - 1]:
            ax.axhline(i - 0.5, color="white", lw=2.5)
    ax.set_xticks(range(nc))
    ax.set_xticklabels([f"{t:g}°" for _, t in cols], fontsize=9.5)
    ax.set_yticks(range(nr))
    ax.set_yticklabels([f"{n}  {ROLE_SHORT[role]}" + (f" {note}" if note else "") for _, n, role, note in DEVICES],
                       fontsize=10.5)
    ax.tick_params(axis="y", pad=20, length=0)
    ax.tick_params(axis="x", length=0)
    for i, (_, _, role, _) in enumerate(DEVICES):   # role chips left of the labels
        ax.add_patch(Rectangle((-0.5 - 0.42, i - 0.32), 0.3, 0.64, color=ROLE_COLOR[role],
                               clip_on=False, transform=ax.transData))
    for i, (_, _, role, _) in enumerate(DEVICES):   # required margin per row
        ax.text(nc - 0.5 + 0.15, i, f"≥{oa.SAT_MARGIN[role] * 1e3:.0f}", ha="left", va="center",
                fontsize=9.5, color=INK2)
    ax.text(nc - 0.5 + 0.15, -0.75, "need\n(mV)", ha="left", va="bottom", fontsize=9, color=INK2)
    ax.grid(False)
    for sp in ax.spines.values():
        sp.set_visible(False)
    n_bad = int(np.nansum(ex < 0))
    n_tri = int(np.nansum(raw < 0))
    ax.set_title(f"{label}: {n_bad} of {ex.size} device·corner cells below requirement"
                 + (f" ({n_tri} in triode)" if n_tri else ""), pad=26)


def plot_heatmaps(items: list[tuple[str, oa.EvalResult]]):
    """items: [(label, EvalResult), ...] drawn top to bottom with one shared color scale."""
    apply_style()
    n = len(items)
    fig, axes = plt.subplots(n, 1, figsize=(11, 4.3 * n + 0.6), squeeze=False)
    norm = Normalize(-CLIP_MV, CLIP_MV)
    for ax, (label, er) in zip(axes[:, 0], items):
        _draw(ax, er, label, norm)
    fig.subplots_adjust(left=0.2, right=0.9, hspace=0.45, bottom=0.17 / n + 0.02)
    cax = fig.add_axes([0.2, 0.06 / n, 0.4, 0.022])
    sm = plt.cm.ScalarMappable(norm=norm, cmap=CMAP)
    cb = fig.colorbar(sm, cax=cax, orientation="horizontal", extend="both")
    cb.set_label("saturation margin above requirement (mV): |Vds| − |Vdsat| − need", fontsize=10)
    cb.outline.set_visible(False)
    fig.legend(handles=[Patch(fc="white", ec=INK, hatch="///", hatchcolor="#6b6a66", lw=1.6, label="triode (|Vds| < |Vdsat|)"),
                        Patch(fc="white", ec=BAD, lw=1.8, label="saturated, below need")],
               loc="lower left", bbox_to_anchor=(0.63, 0.06 / n - 0.02), ncol=1, fontsize=10)
    return fig
