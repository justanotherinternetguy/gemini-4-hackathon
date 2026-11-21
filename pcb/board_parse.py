"""Read a KiCad board file (.kicad_pcb) into plain data for the 3D viewer.

    scene(board)  -> the parts that never change during routing: outline, components, pads, nets
    copper(board) -> the parts that do: track segments and vias

Positions are in millimetres in KiCad's frame (x right, y down). Component and pad rotations are
degrees, counter-clockwise as seen from the top of the board.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()"]+')


def parse_sexpr(text: str) -> list:
    """Minimal S-expression reader: nested lists of strings."""
    stack: list[list] = [[]]
    for tok in TOKEN_RE.findall(text):
        if tok == "(":
            new: list = []
            stack[-1].append(new)
            stack.append(new)
        elif tok == ")":
            if len(stack) > 1:
                stack.pop()
        elif tok.startswith('"'):
            stack[-1].append(tok[1:-1].replace('\\"', '"'))
        else:
            stack[-1].append(tok)
    return stack[0][0] if stack[0] else []


def child(node: list, name: str) -> list | None:
    for c in node:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def children(node: list, name: str):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def fnum(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def xy(node: list | None) -> tuple[float, float]:
    if not node or len(node) < 3:
        return 0.0, 0.0
    return fnum(node[1]), fnum(node[2])


def local_to_board(fx: float, fy: float, rot_deg: float, px: float, py: float) -> tuple[float, float]:
    """A point in a footprint's own frame -> board frame (y down, rotation counter-clockwise)."""
    t = math.radians(rot_deg)
    c, s = math.cos(t), math.sin(t)
    return fx + px * c + py * s, fy - px * s + py * c


def _outline_points(root: list) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for kind in ("gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly"):
        for g in children(root, kind):
            layer = child(g, "layer")
            if not layer or layer[1] != "Edge.Cuts":
                continue
            if kind == "gr_circle":
                cx, cy = xy(child(g, "center"))
                ex, ey = xy(child(g, "end"))
                r = math.hypot(ex - cx, ey - cy)
                pts += [(cx - r, cy - r), (cx + r, cy + r)]
            elif kind == "gr_poly":
                p = child(g, "pts")
                pts += [xy(q) for q in children(p or [], "xy")]
            else:
                for key in ("start", "mid", "end"):
                    n = child(g, key)
                    if n:
                        pts.append(xy(n))
    return pts


def _kind(ref: str, through_hole: bool, n_pads: int, w: float, h: float) -> tuple[str, float]:
    """(shape family, body height in mm) guessed from the reference letter and the pads."""
    r = re.sub(r"[^A-Za-z]", "", ref).upper()
    small, big = min(w, h), max(w, h)
    if r in ("X", "Y", "XTAL") and n_pads <= 4:
        return "crystal", 3.2
    if r in ("U", "IC", "Q", "VR", "REG") or (n_pads >= 6 and r not in ("J", "P", "CON", "X", "K", "SW")):
        return "chip", (2.4 if through_hole else 1.4)
    if r in ("J", "P", "CON", "CN", "JP", "X", "USB", "H"):
        return "connector", (2.6 if through_hole else 2.2)
    if r == "C":
        if through_hole and big < small * 1.6:
            return "can", max(4.0, small * 1.1)
        return "cap", (2.4 if through_hole else 0.9)
    if r == "R":
        return "resistor", (1.8 if through_hole else 0.55)
    if r in ("D", "LED"):
        return "led", (3.0 if through_hole else 0.9)
    if r in ("Y", "XTAL"):
        return "crystal", 3.2
    if r in ("SW", "S", "BTN"):
        return "switch", 3.6
    if r == "L":
        return "inductor", 2.2
    return "part", (2.2 if through_hole else 1.1)


def scene(board: Path) -> dict:
    root = parse_sexpr(Path(board).read_text(errors="replace"))
    nets = {int(fnum(n[1])): (n[2] if len(n) > 2 else "") for n in children(root, "net")}

    comps, pads = [], []
    for fp in children(root, "footprint"):
        fx, fy = xy(child(fp, "at"))
        at = child(fp, "at")
        rot = fnum(at[3]) if at and len(at) > 3 else 0.0
        side = "back" if (child(fp, "layer") or ["", "F.Cu"])[1].startswith("B.") else "front"
        ref = value = ""
        for prop in children(fp, "property"):
            if len(prop) > 2 and prop[1] == "Reference":
                ref = prop[2]
            if len(prop) > 2 and prop[1] == "Value":
                value = prop[2]
        for t in children(fp, "fp_text"):
            if len(t) > 2 and t[1] == "reference":
                ref = ref or t[2]
            if len(t) > 2 and t[1] == "value":
                value = value or t[2]

        local_pts: list[tuple[float, float]] = []
        through = False
        first_pad = len(pads)
        fp_pads = children(fp, "pad")
        for p in fp_pads:
            px, py = xy(child(p, "at"))
            pat = child(p, "at")
            prot = fnum(pat[3]) if pat and len(pat) > 3 else rot
            sx, sy = xy(child(p, "size"))
            kind = p[2] if len(p) > 2 else "smd"
            shape = p[3] if len(p) > 3 else "rect"
            drill = child(p, "drill")
            net = child(p, "net")
            ax, ay = local_to_board(fx, fy, rot, px, py)
            th = kind in ("thru_hole", "np_thru_hole")
            through = through or kind == "thru_hole"
            half = max(sx, sy) / 2
            local_pts += [(px - half, py - half), (px + half, py + half)]
            pads.append({
                "x": round(ax, 4), "y": round(ay, 4), "w": sx, "h": sy, "rot": prot,
                "shape": "round" if shape in ("circle", "oval") else "rect", "th": th,
                "drill": fnum(drill[-1]) if drill and len(drill) > 1 else 0.0,
                "side": side, "net": int(fnum(net[1])) if net else 0, "ref": ref,
                "name": p[1] if len(p) > 1 else "",
            })

        # body outline: courtyard if drawn, else fabrication layer, else the pads themselves
        body: list[tuple[float, float]] = []
        for want in ("CrtYd", "Fab"):
            for kind in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
                for g in children(fp, kind):
                    layer = child(g, "layer")
                    if not layer or want not in layer[1]:
                        continue
                    if kind == "fp_poly":
                        body += [xy(q) for q in children(child(g, "pts") or [], "xy")]
                    elif kind == "fp_circle":
                        cx, cy = xy(child(g, "center"))
                        ex, ey = xy(child(g, "end"))
                        r = math.hypot(ex - cx, ey - cy)
                        body += [(cx - r, cy - r), (cx + r, cy + r)]
                    else:
                        body += [xy(child(g, k)) for k in ("start", "end") if child(g, k)]
            if body:
                break
        shrink = 0.25 if body else -0.25     # courtyards sit outside the body; bare pads sit inside
        pts = body or local_pts
        if not pts:
            continue
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        w, h = max(0.6, (x1 - x0) - 2 * shrink), max(0.6, (y1 - y0) - 2 * shrink)
        cx, cy = local_to_board(fx, fy, rot, (x0 + x1) / 2, (y0 + y1) / 2)
        family, height = _kind(ref, through, len(fp_pads), w, h)
        if family == "chip" and through and not body:
            # a through-hole chip's body sits between its two pin rows, not over them
            if w < h:
                w = max(1.0, w - 2.6)
            else:
                h = max(1.0, h - 2.6)
        for p in pads[first_pad:]:
            p["ci"] = len(comps)                  # which component this pad belongs to
        comps.append({"ref": ref, "value": value, "x": round(cx, 4), "y": round(cy, 4), "w": round(w, 3), "h": round(h, 3),
                      "rot": rot, "side": side, "kind": family, "height": height, "pads": len(fp_pads)})

    pts = _outline_points(root) or [(p["x"], p["y"]) for p in pads]
    if not pts:
        pts = [(0.0, 0.0), (10.0, 10.0)]
    margin = 0.0 if _outline_points(root) else 2.0
    bbox = [min(p[0] for p in pts) - margin, min(p[1] for p in pts) - margin,
            max(p[0] for p in pts) + margin, max(p[1] for p in pts) + margin]
    used = {}
    for p in pads:
        if p["net"]:
            used[p["net"]] = used.get(p["net"], 0) + 1
    copper_layers = sum(1 for layer in (child(root, "layers") or []) if isinstance(layer, list)
                        and len(layer) > 1 and str(layer[1]).endswith(".Cu"))
    return {"bbox": [round(v, 4) for v in bbox], "components": comps, "pads": pads,
            "nets": {str(k): v for k, v in nets.items()},
            "summary": {"width_mm": round(bbox[2] - bbox[0], 1), "height_mm": round(bbox[3] - bbox[1], 1),
                        "components": len(comps), "pads": len(pads),
                        "nets": sum(1 for n in used.values() if n >= 2), "copper_layers": copper_layers or 2}}


SEG_RE = re.compile(
    r"\(segment\s*\(start\s+([-\d.]+)\s+([-\d.]+)\)\s*\(end\s+([-\d.]+)\s+([-\d.]+)\)\s*"
    r"\(width\s+([-\d.]+)\)\s*\(layer\s+\"([^\"]+)\"\)\s*\(net\s+(\d+)", re.S)
VIA_RE = re.compile(
    r"\(via\b[^()]*\(at\s+([-\d.]+)\s+([-\d.]+)\)\s*\(size\s+([-\d.]+)\)\s*\(drill\s+([-\d.]+)\)"
    r".*?\(net\s+(\d+)", re.S)


def copper(board: Path) -> dict:
    """Track segments and vias, each with a stable key so two versions of a board can be diffed."""
    text = Path(board).read_text(errors="replace")
    segs = []
    for x0, y0, x1, y1, w, layer, net in SEG_RE.findall(text):
        a, b = (float(x0), float(y0)), (float(x1), float(y1))
        side = "back" if layer.startswith("B.") else ("front" if layer.startswith("F.") else "inner")
        lo, hi = sorted([a, b])
        segs.append({"k": f"{lo[0]:.3f},{lo[1]:.3f},{hi[0]:.3f},{hi[1]:.3f},{layer}",
                     "a": a, "b": b, "w": float(w), "side": side, "net": int(net)})
    vias = [{"k": f"v{float(x):.3f},{float(y):.3f}", "x": float(x), "y": float(y),
             "size": float(size), "drill": float(drill), "net": int(net)}
            for x, y, size, drill, net in VIA_RE.findall(text)]
    length = sum(math.hypot(s["b"][0] - s["a"][0], s["b"][1] - s["a"][1]) for s in segs)
    return {"segments": segs, "vias": vias, "length_mm": round(length, 1)}


if __name__ == "__main__":
    import json
    import sys
    s = scene(Path(sys.argv[1]))
    c = copper(Path(sys.argv[1]))
    print(json.dumps({"bbox": s["bbox"], "components": len(s["components"]), "pads": len(s["pads"]),
                      "segments": len(c["segments"]), "vias": len(c["vias"]), "length_mm": c["length_mm"],
                      "kinds": sorted({x["kind"] for x in s["components"]})}))
