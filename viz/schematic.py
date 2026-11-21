"""Role-colored transistor schematic of the two-stage op-amp, drawn with AMPICBench's Sheet/AMS symbols.

Each signal-path transistor is drawn in its role color on a tinted box, annotated with its size
(m x W/L in um) and its worst saturation margin over the evaluated PVT points; devices that miss
their required margin at any point get a red outline and a red margin line.

The drawing helpers (tools/analog_sheet.py, tools/ams_symbols.py, CC BY-NC 4.0) are imported in place
from third_party/AMPICBench; nothing is copied.
"""
from __future__ import annotations

import sys

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from sizing import opamp2stg as oa
from viz.common import (_prune_fonts, BAD, DEVICES, INK, INK2, MUTED, ROLE_COLOR, ROLE_LABEL, ROLE_SHORT, pt_label,
                        size_str, worst_margin)

_TOOLS = oa.ROOT / "third_party" / "AMPICBench" / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
from analog_sheet import Sheet  # noqa: E402
from ams_symbols import AMS, PT_PER_UNIT  # noqa: E402

FONT = ["Liberation Sans", "DejaVu Sans"]
STYLES = {
    "device": dict(color=INK, fontsize=12, fontweight="bold"),
    "size": dict(color=INK, fontsize=12),
    "was": dict(color=MUTED, fontsize=11, fontstyle="italic"),
    "ok": dict(color=INK2, fontsize=11),
    "bad": dict(color=BAD, fontsize=11, fontweight="bold"),
    "net": dict(color=INK2, fontsize=11),
    "vdd": dict(color=INK, fontsize=11, fontweight="bold", fontstyle="italic", family=["Liberation Serif"]),
    "title": dict(color=INK, fontsize=15, fontweight="bold"),
    "legend": dict(color=INK, fontsize=12),
    "note": dict(color=INK2, fontsize=11),
}
ROW = 14      # label line pitch (pt)


class RoleSheet(Sheet):
    """Sheet with role-colored MOSFETs, highlight boxes and our own text styles."""

    def __init__(self):
        super().__init__()
        self.boxes = []    # (x0, y0, x1, y1, fill, edge, lw) in sheet points

    def role_fet(self, kind, x, y, gate, role, lines, bad=False, side=None):
        """kind 'n': (x, y) is the drain; 'p': the source.  lines: [(text, style), ...] beside the device."""
        cls, anchor = (AMS.NMOS2A, "drain") if kind == "n" else (AMS.PMOS2A, "source")
        e = cls(stroke=2.0)
        col = ROLE_COLOR[role]
        for seg in e.segments:
            seg.color = col
            if getattr(seg, "fill", None):
                seg.fill = col
        if gate == "right":
            e = e.reverse()
        r = self.d.add(e.anchor(anchor).at(self.U((x, y))))
        t = {k: self.P(r.absanchors[k]) for k in ("drain", "gate", "source")}
        xs = [t["gate"][0], x]
        x0, x1 = min(xs) - 4, max(xs) + 5
        y0 = min(t["drain"][1], t["source"][1]) - 3
        y1 = max(t["drain"][1], t["source"][1]) + 3
        self.boxes.append((x0, y0, x1, y1, col, BAD if bad else col, 2.6 if bad else 0.0, 0.16))
        if bad:   # red badge on the box corner away from the labels
            self.boxes.append(("badge", x0 if gate == "left" else x1, y0))
        cy = (t["drain"][1] + t["source"][1]) / 2
        side = side or ("right" if gate == "left" else "left")
        dx, ha = (9, "left") if side == "right" else (-9, "right")
        top = cy - ROW * (len(lines) - 1) / 2
        for i, (s, kind_) in enumerate(lines):
            self.text((x + dx, top + ROW * i), s, kind_, ha=ha)
        return t

    def render(self, margin=4):
        """Draw to a matplotlib figure (1 sheet point = 1 output point) and return it."""
        # texts are unclipped and picked up by savefig(bbox_inches="tight"); only the boxes need to be
        # inside the drawing extent
        xs, ys = [], []
        for b in self.boxes:
            if b[0] != "badge":
                xs += [b[0], b[2]]
                ys += [b[1], b[3]]
        from schemdraw import elements as elm
        lo = self.U((min(xs) - margin, min(ys) - margin))
        hi = self.U((max(xs) + margin, max(ys) + margin))
        for p in (lo, hi):
            self.d.add(elm.Line().at(p).to((p[0] + 0.001, p[1])).color("white").linewidth(0.01))
        f = self.d.draw(show=False)
        x1, x2 = f.ax.get_xlim()
        y1, y2 = f.ax.get_ylim()
        f.fig.set_size_inches(abs(x2 - x1) * PT_PER_UNIT / 72, abs(y2 - y1) * PT_PER_UNIT / 72)
        f.ax.set_position([0, 0, 1, 1])
        ax = f.ax
        for b in self.boxes:
            if b[0] == "badge":
                _, bx, by = b
                ux, uy = self.U((bx, by))
                ax.text(ux, uy, "!", ha="center", va="center", color="white", fontsize=10,
                        fontweight="bold", fontfamily=FONT, zorder=7,
                        bbox=dict(boxstyle="circle,pad=0.18", fc=BAD, ec="white", lw=1.0))
                continue
            x0, y0, x1_, y1_, fill, edge, lw, alpha = b
            (ux0, uy1), (ux1, uy0) = self.U((x0, y0)), self.U((x1_, y1_))
            ax.add_patch(FancyBboxPatch((ux0, uy0), ux1 - ux0, uy1 - uy0,
                                        boxstyle=f"round,pad=0,rounding_size={4 / PT_PER_UNIT}",
                                        fc=fill, alpha=alpha, lw=0, zorder=0.5, clip_on=False))
            if lw:
                ax.add_patch(FancyBboxPatch((ux0, uy0), ux1 - ux0, uy1 - uy0,
                                            boxstyle=f"round,pad=0,rounding_size={4 / PT_PER_UNIT}",
                                            fc="none", ec=edge, lw=lw, zorder=6, clip_on=False))
        for (x, y), s, kind, ha, va in self.texts:
            st = dict(STYLES[kind])
            fam = st.pop("family", FONT)
            ux, uy = self.U((x, y))
            ax.text(ux, uy, s, ha=ha, va=va, fontfamily=fam, zorder=8, **st)
        return f.fig

    def legend_swatch(self, x, y, color, edge=None, label=""):
        self.boxes.append((x, y - 7, x + 18, y + 7, color, edge or color, 2.6 if edge else 0.0, 1.0))
        self.text((x + 24, y), label, "legend")
        return x + 24 + 0.5 * STYLES["legend"]["fontsize"] * len(label) + 22


def _eng(v, unit):
    for s, k in (("M", 1e6), ("k", 1e3), ("", 1), ("m", 1e-3), ("µ", 1e-6), ("n", 1e-9), ("p", 1e-12), ("f", 1e-15)):
        if abs(v) >= k * 0.999:
            return f"{v / k:.3g} {s}{unit}"
    return f"{v:.3g} {unit}"


def draw_schematic(params: dict, er: oa.EvalResult | None = None, ref_params: dict | None = None,
                   name: str = "design", ref_name: str = "default"):
    """params: knob dict; er: its evaluation (for margins); ref_params: show 'was ...' where sizes differ."""
    p = oa.clamp(params)
    ref = oa.clamp(ref_params) if ref_params is not None else None
    _prune_fonts()
    s = RoleSheet()
    info = {dev: (role, note) for _, dev, role, note in DEVICES}
    prefix = {dev: pre for pre, dev, _, _ in DEVICES}

    def lines(dev):
        role, note = info[dev]
        out = [(f"{dev} · {ROLE_SHORT[role]}" + (f" {note}" if note and role in ("tail", "sink") else ""),
                "device"), (size_str(p, role), "size")]
        if ref is not None and size_str(ref, role) != size_str(p, role):
            out.append((f"was {size_str(ref, role)}", "was"))
        bad = False
        if er is not None:
            wm = worst_margin(er, prefix[dev], role)
            if wm:
                x, m, c, t = wm
                bad = x < 0
                need = oa.SAT_MARGIN[role] * 1e3
                txt = f"margin {m * 1e3:.0f} mV · {pt_label(c, t)}"
                if bad:
                    txt = ("✗ triode " if m < 0 else f"✗ < {need:.0f} mV: ") + f"{m * 1e3:.0f} mV · {pt_label(c, t)}"
                out.append((txt, "bad" if bad else "ok"))
        return out, bad

    def fet(kind, x, y, gate, dev, side=None):
        ln, bad = lines(dev)
        return s.role_fet(kind, x, y, gate, info[dev][0], ln, bad, side)

    def gate_net(g, side, label):
        dx = -12 if side == "left" else 12
        s.wire(g, (g[0] + dx, g[1]))
        s.text((g[0] + dx + (-3 if side == "left" else 3), g[1]), label, "net",
               ha="right" if side == "left" else "left")

    # ---------------- stage 1: NMOS pair, PMOS mirror, cascoded tail
    XL, XR = 0, 270
    XT = (XL + XR) / 2
    YP, YN, YT = 18, 117, 189
    GAP = 63              # cascode stack pitch (drain-to-drain), leaves room for labels
    s.vdd((XL, YP))
    s.vdd((XR, YP))
    pd = fet("p", XL, YP, "right", "PM1", side="left")
    po = fet("p", XR, YP, "left", "PM0", side="right")
    s.wire(pd["gate"], po["gate"])
    xd = XL + 45
    s.dot((xd, pd["gate"][1]))
    s.wire((xd, pd["gate"][1]), (xd, 78), (XL, 78))
    s.dot((XL, 78))
    s.text((XL - 6, 86), "net9", "net", ha="right")
    s.wire(pd["drain"], (XL, YN))
    s.wire(po["drain"], (XR, YN))
    nd = fet("n", XL, YN, "left", "NM0", side="right")
    no = fet("n", XR, YN, "right", "NM1", side="left")
    gate_net(nd["gate"], "left", "vm (−)")
    gate_net(no["gate"], "right", "vp (+)")
    s.wire(nd["source"], (XL, YT - 18), (XR, YT - 18), no["source"])
    s.wire((XT, YT - 18), (XT, YT))
    s.dot((XT, YT - 18))
    s.text((XT - 52, YT - 12), "net10", "net", ha="right", va="top")
    t1 = fet("n", XT, YT, "left", "NM2")
    gate_net(t1["gate"], "left", "Vbias<3>")
    s.wire(t1["source"], (XT, YT + GAP))
    t2 = fet("n", XT, YT + GAP, "left", "NM3")
    gate_net(t2["gate"], "left", "Vbias<4>")
    s.gnd(t2["source"])

    # ---------------- stage 2: PMOS driver, cascoded NMOS sink, Rz + Cc, CL
    y1 = 90                       # net1 (stage-1 output) / vout row
    xs = XR + 275
    xb = xs - 140
    s.dot((XR, y1))
    s.text((XR + 60, y1 + 6), "net1", "net", va="top")
    s.vdd((xs, YP))
    pm = fet("p", xs, YP, "left", "PM2", side="right")
    s.wire((XR, y1), (xb, y1))
    s.wire((xb, y1), (xb, pm["gate"][1]), pm["gate"])
    s.dot((xb, y1))
    s.wire((xb, y1), (xb + 9, y1))
    s.res((xb + 9, y1), (xb + 45, y1))
    s.text((xb + 27, y1 - 30), "Rz", "device", ha="center")
    s.text((xb + 27, y1 - 16), _eng(p["Rz"], "Ω"), "size", ha="center")
    s.wire((xb + 45, y1), (xb + 72, y1))
    s.cap((xb + 72, y1), (xb + 108, y1))
    s.text((xb + 90, y1 - 30), "Cc", "device", ha="center")
    s.text((xb + 90, y1 - 16), _eng(p["Cc"], "F"), "size", ha="center")
    s.wire((xb + 108, y1), (xs, y1))
    s.dot((xs, y1))
    s.wire(pm["drain"], (xs, y1))
    y5 = y1 + 62
    s.wire((xs, y1), (xs, y5))
    n5 = fet("n", xs, y5, "left", "NM5")
    gate_net(n5["gate"], "left", "Vbias<3>")
    s.wire(n5["source"], (xs, y5 + GAP))
    n4 = fet("n", xs, y5 + GAP, "left", "NM4")
    gate_net(n4["gate"], "left", "Vbias<4>")
    s.gnd(n4["source"])
    xo = xs + 170
    s.wire((xs, y1), (xo + 30, y1))
    s.dot((xo, y1))
    s.text((xo + 34, y1), "vout", "net")
    s.cap((xo, y1), (xo, y1 + 36))
    s.text((xo + 16, y1 + 12), "CL", "device")
    s.text((xo + 16, y1 + 27), "500 fF", "size")
    s.gnd((xo, y1 + 36))
    # passive labels from Sheet._passive_label use kinds 'device'/'size' -> fine with our STYLES

    # ---------------- title, legend, footnote
    x0 = XL - 150
    s.text((x0, -84), f"{name}: signal-path devices colored by role", "title", va="bottom")
    lx = x0
    for role in ("in", "load", "tail", "drv", "sink"):
        lx = s.legend_swatch(lx, -52, ROLE_COLOR[role], label=ROLE_LABEL[role])
    if er is not None:
        s.legend_swatch(lx, -52, "#ffffff", edge=BAD, label="misses saturation margin")
    foot = [f"Sizes: m × W/L in µm (sky130).  Ibias = {_eng(p['Ibias'], 'A')}; bias network frozen at published sizes."]
    if er is not None:
        foot.append(f"margin = |Vds| − |Vdsat|, worst of {len(er.points)} PVT points; required ≥ 50 mV for "
                    "input pair / load / driver, ≥ 0 (saturated) for tail and sink.")
    if ref is not None:
        foot.append(f"'was' = size in {ref_name}.")
    for i, t in enumerate(foot):
        s.text((x0, YT + GAP + 84 + 16 * i), t, "note")
    return s.render()


if __name__ == "__main__":   # quick look: python -m viz.schematic out.png
    out = sys.argv[1] if len(sys.argv) > 1 else "schematic.png"
    fig = draw_schematic(oa.DEFAULTS, oa.evaluate(oa.DEFAULTS), name="default")
    fig.savefig(out, dpi=150)
    plt.close(fig)
