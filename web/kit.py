"""Small HTML and SVG helpers shared by the site sections. Colours are CSS variables from style.css,
so every chart follows the page's cream or ink theme."""
from __future__ import annotations

import html
import json
import math
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
OUT = RUNS / "web"
E = html.escape


def jload(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return default


def jsonl(p: Path) -> list:
    out = []
    try:
        for line in Path(p).read_text().splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass
    return out


def asset(src: Path, name: str) -> str:
    """Copy a file into runs/web/img/ and return its relative URL ('' if the source is missing)."""
    src = Path(src)
    if not src.is_file():
        return ""
    dst = OUT / "img" / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime or dst.stat().st_size != src.stat().st_size:
        shutil.copy2(src, dst)
    return f"img/{name}"


def chip(text: str, cls: str = "") -> str:
    return f'<span class="chip {cls}">{text}</span>'


def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=raw)
    t = math.floor(lo / step) * step
    out = []
    while t <= hi + 1e-9:
        out.append(round(t, 10))
        t += step
    return out


def fmt(v: float) -> str:
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1000:
        return f"{v / 1000:g}k"
    if a >= 10:
        return f"{v:.0f}"
    return f"{v:g}"


def line_chart(series: list[dict], *, w: int = 640, h: int = 280, xlabel: str = "", ylabel: str = "",
               ymin: float | None = None, ymax: float | None = None, step: bool = False,
               hlines: list[tuple[float, str]] | None = None, xint: bool = True) -> str:
    """series: {name, color (css), pts [(x, y)], dashed, tips [str per point] (optional), dots}."""
    xs = [x for s in series for x, _ in s["pts"]] or [0, 1]
    ys = [y for s in series for _, y in s["pts"] if y is not None] or [0, 1]
    for v, _ in hlines or []:
        ys.append(v)
    x0, x1 = min(xs), max(xs) if max(xs) > min(xs) else min(xs) + 1
    y0 = min(ys) if ymin is None else ymin
    y1 = max(ys) if ymax is None else ymax
    if y1 <= y0:
        y1 = y0 + 1
    yt = nice_ticks(y0, y1, 4)
    if all(float(y).is_integer() for y in ys):          # counts: whole-number ticks only
        yt = [t for t in yt if float(t).is_integer()] or yt
    y0, y1 = min(y0, yt[0]), max(y1, yt[-1])
    L, R, T, B = 48, 16, 12, 40 if xlabel else 28
    pw, ph = w - L - R, h - T - B
    X = lambda x: L + pw * (x - x0) / (x1 - x0)
    Y = lambda y: T + ph * (1 - (y - y0) / (y1 - y0))
    p = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img">']
    for t in yt:
        p.append(f'<line class="grid" x1="{L}" x2="{w - R}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
                 f'<text x="{L - 8}" y="{Y(t) + 4:.1f}" text-anchor="end">{fmt(t)}</text>')
    xt = nice_ticks(x0, x1, 6)
    for t in xt:
        if x0 <= t <= x1 and (not xint or float(t).is_integer()):
            p.append(f'<text x="{X(t):.1f}" y="{T + ph + 18}" text-anchor="middle">{fmt(t)}</text>')
    if xlabel:
        p.append(f'<text x="{L + pw / 2:.0f}" y="{h - 4}" text-anchor="middle">{E(xlabel)}</text>')
    if ylabel:
        p.append(f'<text x="{L}" y="{T - 0}" dy="-2" opacity="0">{E(ylabel)}</text>')
    for v, lab in hlines or []:
        p.append(f'<line class="zero" x1="{L}" x2="{w - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>'
                 f'<text x="{w - R}" y="{Y(v) - 6:.1f}" text-anchor="end" style="fill:var(--mint-text)">{E(lab)}</text>')
    for s in sorted(series, key=lambda s: not s.get("dashed")):
        pts = [(x, y) for x, y in s["pts"] if y is not None]
        if not pts:
            continue
        coords = []
        for i, (x, y) in enumerate(pts):
            if step and i:
                coords.append(f"{X(x):.1f},{Y(pts[i - 1][1]):.1f}")
            coords.append(f"{X(x):.1f},{Y(y):.1f}")
        cls = "ln ctl" if s.get("dashed") else "ln"
        if not s.get("noline"):
            p.append(f'<polyline class="{cls}" points="{" ".join(coords)}" style="stroke:{s["color"]}"/>')
        tips = s.get("tips")
        if s.get("dots") or tips:
            for i, (x, y) in enumerate(pts):
                tip = f' data-tip="{E(tips[i])}"' if tips and i < len(tips) and tips[i] else ""
                r = 4 if s.get("dots") else 0
                if r:
                    p.append(f'<circle class="pt" cx="{X(x):.1f}" cy="{Y(y):.1f}" r="{r}" fill="{s["color"]}"{tip}/>')
                if tip:
                    p.append(f'<circle class="hit" cx="{X(x):.1f}" cy="{Y(y):.1f}" r="10"{tip}/>')
        else:
            x, y = pts[-1]
            p.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="5" fill="{s["color"]}"/>')
    p.append("</svg>")
    return "".join(p)


def key(series: list[tuple[str, str, bool]]) -> str:
    """[(label, color, dashed)] -> legend row."""
    return '<div class="keyrow">' + "".join(
        f'<span><i class="{"d" if d else ""}" style="background:{c}"></i>{lab}</span>' for lab, c, d in series) + "</div>"


def hbars(rows: list[tuple[str, float, str, str]], *, vmax: float | None = None, unit: str = "",
          w: int = 640, label_w: int = 170) -> str:
    """rows: (label, value, color, text-to-show). Horizontal bars, one per row."""
    vmax = vmax or max([r[1] for r in rows] + [1])
    rh, gap = 30, 10
    h = len(rows) * (rh + gap)
    pw = w - label_w - 70
    p = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img">']
    for i, (lab, v, c, txt) in enumerate(rows):
        y = i * (rh + gap)
        bw = max(2, pw * (v / vmax if vmax else 0))
        p.append(f'<text class="lbl" x="0" y="{y + rh / 2 + 5}">{E(lab)}</text>'
                 f'<rect x="{label_w}" y="{y}" width="{pw}" height="{rh}" rx="8" style="fill:var(--sunk)"/>'
                 f'<rect x="{label_w}" y="{y}" width="{bw:.1f}" height="{rh}" rx="8" style="fill:{c}"/>'
                 f'<text x="{label_w + bw + 8:.1f}" y="{y + rh / 2 + 5}" style="fill:var(--text)">{E(txt)}</text>')
    p.append("</svg>")
    return "".join(p)


def table(head: list[str], rows: list[list[str]], cls: str = "") -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    trs = "".join("<tr>" + "".join(c if c.startswith("<td") else f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'


def switch(group: str, items: list[tuple[str, str]], dot_colors: list[str] | None = None) -> str:
    """Pill switch; items (id, label). Panes carry data-group/data-id."""
    b = []
    for i, (pid, lab) in enumerate(items):
        dot = f'<i style="background:{dot_colors[i]}"></i>' if dot_colors and dot_colors[i] else ""
        b.append(f'<button class="{"on" if i == 0 else ""}" data-group="{group}" data-id="{pid}">{dot}{lab}</button>')
    return f'<div class="switch" role="tablist">{"".join(b)}</div>'


def pane(group: str, pid: str, body: str, first: bool) -> str:
    return f'<div class="pane{" on" if first else ""}" data-group="{group}" data-id="{pid}">{body}</div>'


def studio_gif() -> str:
    """The studio routing a real board, start to finish (web/shots/studio_route.gif), for the top of both pages."""
    src = asset(ROOT / "web" / "shots" / "studio_route.gif", "shots/studio_route.gif")
    if not src:
        return ""
    return (f'<figure class="shot herogif"><img src="{src}" alt="The routr studio routing a circuit board: the classic '
            f'router lays copper, Gemini screens what blocks the last connection, repair plans run, and KiCad marks it ready to manufacture">'
            f'<figcaption>A real run in the studio, 1.5\u00d7 speed. The classic router leaves one open connection '
            f'and one rule error. Gemini\u2019s Decisions API reads a picture of the board and names what is in the way, '
            f'six repair plans run in parallel, and KiCad\u2019s rule checker passes the board.</figcaption></figure>')
