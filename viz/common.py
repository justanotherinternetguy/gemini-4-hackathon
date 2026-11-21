"""Shared bits for the circuit visualizer: palette, device table, design loading, figure saving."""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from sizing import opamp2stg as oa  # noqa: E402

# --------------------------------------------------------------------------- palette
# Categorical slots in fixed order (validated reference palette); red is kept for "violation".
ROLE_COLOR = {
    "in": "#2a78d6",    # blue
    "load": "#eb6834",  # orange
    "tail": "#1baf7a",  # aqua
    "drv": "#6250d6",   # violet
    "sink": "#e87ba4",  # magenta
}
ROLE_LABEL = {
    "in": "input pair",
    "load": "mirror load",
    "tail": "tail current source",
    "drv": "stage-2 driver",
    "sink": "stage-2 current sink",
}
ROLE_SHORT = {"in": "input", "load": "load", "tail": "tail", "drv": "driver", "sink": "sink"}
CORNER_COLOR = {"tt": "#2a78d6", "ff": "#eb6834", "ss": "#1baf7a", "sf": "#6250d6", "fs": "#e87ba4"}
TEMP_STYLE = {-40: (0, (1.2, 1.6)), 27: "-", 125: (0, (5, 2.2))}
WORKER_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
GOOD, BAD, WARN = "#0ca30c", "#d03b3b", "#fab219"
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df"
SURFACE = "#ffffff"

# --------------------------------------------------------------------------- devices
# display order: signal path, stage 1 then stage 2.  path prefix -> (name, role, note)
DEVICES = [
    ("m.xi1.xi7.xnm0", "NM0", "in", "vm side"),
    ("m.xi1.xi7.xnm1", "NM1", "in", "vp side"),
    ("m.xi1.xi7.xpm1", "PM1", "load", "diode"),
    ("m.xi1.xi7.xpm0", "PM0", "load", "output"),
    ("m.xi1.xi7.xnm2", "NM2", "tail", "cascode"),
    ("m.xi1.xi7.xnm3", "NM3", "tail", "bottom"),
    ("m.xi1.xi6.xpm2", "PM2", "drv", ""),
    ("m.xi1.xi6.xnm5", "NM5", "sink", "cascode"),
    ("m.xi1.xi6.xnm4", "NM4", "sink", "bottom"),
]
LN_FIXED = 300e-9   # tail / sink L follows the bias reference (Ln_)
MULT = {"in": 1, "load": 1, "tail": 2, "drv": 3, "sink": 3}


def role_size(params: dict, role: str) -> tuple[float, float, int]:
    """(W, L, m) in metres for a role."""
    p = oa.clamp(params)
    w = p[f"W_{role}"]
    l = p[f"L_{role}"] if oa.ROLE_HAS_L[role] else LN_FIXED
    return w, l, MULT[role]


def size_str(params: dict, role: str) -> str:
    w, l, m = role_size(params, role)
    s = f"{w * 1e6:.3g}/{l * 1e6:.3g}"
    return f"{m}× {s}" if m != 1 else s


def device_at(point: oa.PointResult, prefix: str) -> dict | None:
    for path, d in point.devices.items():
        if path.startswith(prefix + "."):
            return d
    return None


def worst_margin(er: oa.EvalResult, prefix: str, role: str):
    """-> (margin_minus_requirement V, margin V, corner, temp) at the worst point, or None."""
    best = None
    for r in er.points:
        d = device_at(r, prefix)
        if d is None:
            continue
        x = d["margin"] - oa.SAT_MARGIN[role]
        if best is None or x < best[0]:
            best = (x, d["margin"], r.corner, r.temp)
    return best


# --------------------------------------------------------------------------- designs

def load_design(spec: str) -> tuple[str, dict, dict]:
    """'default' or a JSON path ({"params": {...}} or a flat knob dict) -> (name, params, meta)."""
    if spec in ("default", "DEFAULTS", "published"):
        return "default", dict(oa.DEFAULTS), {"note": "published AMPICBench sizing"}
    path = Path(spec)
    data = json.loads(path.read_text())
    params = data.get("params", data) if isinstance(data, dict) else {}
    params = {k: v for k, v in params.items() if k in oa.SPACE}
    meta = {k: v for k, v in data.items() if k != "params"} if "params" in data else {}
    return str(data.get("name") or path.stem), params, meta


def spec_of(meta: dict | None) -> dict:
    s = (meta or {}).get("spec")
    if isinstance(s, dict) and s:
        return {k: (v[0], float(v[1])) for k, v in s.items()}
    return dict(oa.DEFAULT_SPEC)


def pt_label(corner, temp) -> str:
    return f"{corner}/{int(temp) if float(temp).is_integer() else temp}"


# --------------------------------------------------------------------------- style + saving

_FONTS_PRUNED = False


def _prune_fonts() -> None:
    """findfont scores every installed font (10k+ on some machines, ~0.2 s per lookup); keep only the
    families we use plus matplotlib's own math fonts."""
    global _FONTS_PRUNED
    if _FONTS_PRUNED:
        return
    from matplotlib import font_manager as fm
    keep = ("Liberation Sans", "Liberation Serif", "DejaVu Sans", "DejaVu Sans Mono", "DejaVu Serif")
    small = [f for f in fm.fontManager.ttflist if f.name in keep or f.name.startswith(("STIX", "cm"))]
    if any(f.name == "Liberation Sans" for f in small) or any(f.name == "DejaVu Sans" for f in small):
        fm.fontManager.ttflist = small
        fm.fontManager._findfont_cached.cache_clear()
    _FONTS_PRUNED = True


def apply_style() -> None:
    _prune_fonts()
    plt.rcParams.update({
        "font.family": ["Liberation Sans", "DejaVu Sans"],
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "axes.labelsize": 11,
        "axes.labelcolor": INK2,
        "axes.edgecolor": "#b9b8b3",
        "axes.linewidth": 0.8,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.7,
        "xtick.color": INK2,
        "ytick.color": INK2,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "legend.fontsize": 10,
        "legend.frameon": False,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "svg.fonttype": "path",
        "svg.hashsalt": "viz",
    })


def save_fig(fig, base: Path, dpi: int = 150) -> str:
    """Write base.png + base.svg; return the SVG markup ready to inline in HTML."""
    base.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(base.with_suffix(".png"), dpi=dpi, bbox_inches="tight", pad_inches=0.12)
    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)
    svg = buf.getvalue()
    base.with_suffix(".svg").write_text(svg)
    return inline_svg(svg, base.stem)


def inline_svg(svg: str, uid: str) -> str:
    """Strip the XML prologue, make the SVG scale to its container, and namespace its ids."""
    svg = svg[svg.index("<svg"):]
    head_end = svg.index(">")
    head = svg[:head_end]
    head = re.sub(r'\s(width|height)="[^"]*"', "", head, count=2)
    svg = head + ' preserveAspectRatio="xMidYMid meet" role="img"' + svg[head_end:]
    # ids from different figures must not collide once inlined in one page
    ids = set(re.findall(r'\sid="([^"]+)"', svg))
    if ids:
        pat = re.compile(r'(id="|#)(' + "|".join(re.escape(i) for i in sorted(ids, key=len, reverse=True)) + r')(?=["\)])')
        svg = pat.sub(lambda m: f"{m.group(1)}{uid}-{m.group(2)}", svg)
    return svg
