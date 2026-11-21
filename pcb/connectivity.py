"""Which connections are still open on a saved board, from geometry alone (no KiCad needed).

Used by the video renderer to draw open connections as airwires. It is an approximation of
KiCad's own connectivity: pads, track segments and vias of the same net are joined when an end
point lies inside a pad, on a via, or on another segment of the same layer. Copper zones are not
handled (the PCBWorld boards we render have none). The engine's rule checker stays the judge.

    open_connections(scene, copper) -> [{"net", "a": (x, y), "b": (x, y)}]   one per missing link
"""
from __future__ import annotations

import math


class _DSU:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def _in_pad(px, py, pad, tol):
    dx, dy = px - pad["x"], py - pad["y"]
    hw, hh = pad["w"] / 2 + tol, pad["h"] / 2 + tol
    if pad["shape"] == "round" and abs(pad["w"] - pad["h"]) < 1e-6:
        return math.hypot(dx, dy) <= hw
    for sign in (1, -1):                       # rotation sense differs between KiCad versions; accept either
        r = math.radians(sign * pad["rot"])
        lx, ly = dx * math.cos(r) + dy * math.sin(r), -dx * math.sin(r) + dy * math.cos(r)
        if abs(lx) <= hw and abs(ly) <= hh:
            return True
    return False


def _on_segment(px, py, s, tol):
    (ax, ay), (bx, by) = s["a"], s["b"]
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / L2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy)) <= tol


def _pad_layers(pad):
    return {"front", "back", "inner"} if pad["th"] else {pad["side"]}


def open_connections(scene: dict, copper: dict) -> list[dict]:
    by_net: dict[int, dict] = {}
    for i, p in enumerate(scene["pads"]):
        if p["net"]:
            by_net.setdefault(p["net"], {"pads": [], "segs": [], "vias": []})["pads"].append(i)
    for i, s in enumerate(copper["segments"]):
        if s["net"] in by_net:
            by_net[s["net"]]["segs"].append(i)
    for i, v in enumerate(copper["vias"]):
        if v["net"] in by_net:
            by_net[v["net"]]["vias"].append(i)

    pads, segs, vias = scene["pads"], copper["segments"], copper["vias"]
    out = []
    for net, items in by_net.items():
        if len(items["pads"]) < 2:
            continue
        d = _DSU()
        for i in items["pads"]:
            d.find(("p", i))
        # segment ends against pads, vias and other segments on the same layer
        for i in items["segs"]:
            s = segs[i]
            tol = s["w"] / 2
            for end in (s["a"], s["b"]):
                for j in items["pads"]:
                    if s["side"] in _pad_layers(pads[j]) and _in_pad(end[0], end[1], pads[j], tol):
                        d.union(("s", i), ("p", j))
                for j in items["vias"]:
                    v = vias[j]
                    if math.hypot(end[0] - v["x"], end[1] - v["y"]) <= v["size"] / 2 + tol:
                        d.union(("s", i), ("v", j))
                for j in items["segs"]:
                    if j != i and segs[j]["side"] == s["side"] and _on_segment(end[0], end[1], segs[j], tol + segs[j]["w"] / 2):
                        d.union(("s", i), ("s", j))
        for j in items["vias"]:                 # a via on a through-hole pad, or inside an SMD pad
            v = vias[j]
            for k in items["pads"]:
                if _in_pad(v["x"], v["y"], pads[k], v["size"] / 2):
                    d.union(("v", j), ("p", k))
        groups: dict = {}
        for i in items["pads"]:
            groups.setdefault(d.find(("p", i)), []).append(i)
        comps = list(groups.values())
        # one airwire per missing link: greedily join the closest pair of groups (Prim on groups)
        joined = [comps[0]]
        rest = comps[1:]
        while rest:
            best = None
            for gi, g in enumerate(rest):
                for a in (x for c in joined for x in c):
                    for b in g:
                        dd = math.hypot(pads[a]["x"] - pads[b]["x"], pads[a]["y"] - pads[b]["y"])
                        if best is None or dd < best[0]:
                            best = (dd, gi, a, b)
            _, gi, a, b = best
            out.append({"net": net, "a": (pads[a]["x"], pads[a]["y"]), "b": (pads[b]["x"], pads[b]["y"])})
            joined.append(rest.pop(gi))
    return out
