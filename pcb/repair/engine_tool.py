"""Engine-side repair tool for the PCB routing swarm. Runs INSIDE PCBWorld's conda env
(its pinned KiCad engine), as a JSON-lines server on stdin/stdout, so the orchestrator
(our own venv, newer google-genai) can drive one board attempt per process.

    cd third_party/pcbworld
    PYTHONPATH=build_rl/pcbnew/python/rl:. ~/miniforge3/envs/pcbworld/bin/python \
        /path/to/pcb/repair/engine_tool.py

Requests are one JSON object per line: {"op": "...", ...}; replies are one JSON line.
Ops: open, status, nets, ripup, route, checkpoint, restore, save, score, render, close.

The tool never reads the human-routed original: callers pass a board the swarm or a
baseline router produced (or the unrouted board).
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import traceback


def _attrs(obj) -> dict:
    """Best-effort plain dict of a pybind/wire record."""
    out = {}
    for k in dir(obj):
        if k.startswith("_"):
            continue
        try:
            v = getattr(obj, k)
        except Exception:
            continue
        if callable(v):
            continue
        if isinstance(v, (int, float, str, bool)) or v is None:
            out[k] = v
        elif isinstance(v, (list, tuple)) and all(isinstance(x, (int, float, str)) for x in v):
            out[k] = list(v)
    return out


class Session:
    def __init__(self):
        self.eng = None
        self.board = None
        self.pro = None
        self.names = {}

    # ---- lifecycle
    def open(self, board: str, project: str | None = None) -> dict:
        from pcb_world.engine.kicad_engine import KiCadEngine
        if self.eng is not None:
            self.eng.close()
        self.board, self.pro = board, project
        self.eng = KiCadEngine.for_board_file(board, project_path=project)
        self.eng.build_connectivity()
        self.names = {int(k): v for k, v in self.eng.get_net_names().items()}
        return self.status()

    def close(self) -> dict:
        if self.eng is not None:
            self.eng.close()
            self.eng = None
        return {"closed": True}

    # ---- inspection
    def status(self, detail: bool = True) -> dict:
        e = self.eng
        if e.is_routing():
            e.cancel_route()
        e.build_connectivity()
        snap = e.get_reward_snapshot(run_drc=True)
        groups = e.get_pad_groups()
        routable = set(e.get_routable_nets())
        open_nets = sorted(n for n in routable if groups.get(n, 1) > 1)
        out = {
            "unrouted": int(snap.unrouted_count),
            "drc_errors": int(snap.drc_error_count),
            "drc_warnings": int(snap.drc_warning_count),
            "wirelength_mm": round(float(snap.total_wirelength), 2),
            "vias": int(snap.via_count),
            "tracks": int(snap.track_count),
            "n_routable_nets": len(routable),
            "open_nets": [{"net": n, "name": self.names.get(n, ""), "pad_groups": groups.get(n)}
                          for n in open_nets],
            "errors_per_net": dict(snap.drc_errors_per_net or {}),
            "clean": int(snap.drc_error_count) == 0 and not open_nets,
        }
        if detail:
            viol = []
            for v in e.get_drc_violations() or []:
                a = _attrs(v)
                viol.append(a)
            out["violations"] = viol[:60]
            rats = []
            for r in e.get_ratsnest() or []:
                rats.append(_attrs(r))
            out["ratsnest"] = rats[:200]
        return out

    def nets(self) -> dict:
        e = self.eng
        pads = {}
        for p in e.get_pads():
            a = _attrs(p)
            nc = a.get("net_code", 0)
            if nc and nc > 0:
                pads.setdefault(nc, []).append(
                    {k: a.get(k) for k in ("x_mm", "y_mm", "layer", "pad_type", "width_mm", "height_mm")})
        bbox = e.get_board_bbox()
        return {"names": self.names, "pads": pads, "layers": int(e.get_copper_layer_count()),
                "bbox": _attrs(bbox) if not isinstance(bbox, (list, tuple)) else list(bbox)}

    # ---- mutation
    def ripup(self, nets: list[int]) -> dict:
        e = self.eng
        if e.is_routing():
            e.cancel_route()
        n = e.delete_routing_of_nets([int(x) for x in nets])
        e.build_connectivity()
        return {"removed_items": int(n)}

    def _edges(self, net: int) -> list[dict]:
        return [a for a in (_attrs(r) for r in (self.eng.get_ratsnest() or []))
                if a.get("net_code") == net]

    def route(self, net: int, mode: int = 2, max_attempts: int = 5, layers: list[int] | None = None,
              max_edges: int = 40) -> dict:
        """Route every open connection of `net` with KiCad's router `finish`
        (mode 0 = mark obstacles, 1 = shove, 2 = walk around). Tries each open
        ratsnest endpoint, on the preferred layers first."""
        e = self.eng
        net = int(net)
        nlayers = int(e.get_copper_layer_count())
        layer_order = [l for l in (layers or []) if 1 <= l <= nlayers] or list(range(1, nlayers + 1))
        e.set_routing_mode(int(mode))
        made, tried, log = 0, 0, []
        e.build_connectivity()
        for _ in range(max_edges):
            edges = self._edges(net)
            if not edges:
                break
            progressed = False
            for ed in edges:
                # endpoint fields differ between builds; collect every (x, y, layer) we can find
                ends = []
                for sx, sy, sl in (("x1_mm", "y1_mm", "layer1"), ("x2_mm", "y2_mm", "layer2"),
                                   ("start_x_mm", "start_y_mm", "start_layer"),
                                   ("end_x_mm", "end_y_mm", "end_layer"),
                                   ("x1", "y1", "layer1"), ("x2", "y2", "layer2")):
                    if sx in ed and sy in ed:
                        ends.append((float(ed[sx]), float(ed[sy]), ed.get(sl)))
                for (x, y, lay) in ends:
                    cand_layers = ([int(lay)] if isinstance(lay, int) and 1 <= lay <= nlayers else []) + \
                                  [l for l in layer_order if l != lay]
                    for L in cand_layers:
                        tried += 1
                        if not e.start_route(x, y, L):
                            continue
                        ok = False
                        try:
                            ok = bool(e.finish(int(max_attempts)))
                        except Exception as ex:   # router refused
                            log.append(f"finish error: {ex}")
                        if e.is_routing():
                            e.cancel_route()
                        e.build_connectivity()
                        if ok and len(self._edges(net)) < len(edges):
                            made += 1
                            progressed = True
                            break
                    if progressed:
                        break
                if progressed:
                    break
            if not progressed:
                break
        left = len(self._edges(net))
        return {"net": net, "name": self.names.get(net, ""), "connections_made": made,
                "open_left": left, "attempts": tried, "log": log[:5]}

    def blockers(self, margin_mm: float = 0.6, max_per_net: int = 6) -> dict:
        """For every open connection: which OTHER nets have copper crossing the straight
        corridor between its two endpoints? Candidates only - the Decisions API and the
        planners judge which ones really block."""
        e = self.eng
        e.build_connectivity()
        tracks = []
        for t in e.get_tracks() or []:
            a = _attrs(t)
            if None in (a.get("x1_mm"), a.get("y1_mm"), a.get("x2_mm"), a.get("y2_mm")):
                continue
            tracks.append((a["x1_mm"], a["y1_mm"], a["x2_mm"], a["y2_mm"], a.get("net_code"), a.get("layer")))

        def ccw(ax, ay, bx, by, cx, cy):
            return (cy - ay) * (bx - ax) > (by - ay) * (cx - ax)

        def cross(p1, p2, p3, p4):
            return (ccw(*p1, *p3, *p4) != ccw(*p2, *p3, *p4)) and (ccw(*p1, *p2, *p3) != ccw(*p1, *p2, *p4))

        out = []
        for r in e.get_ratsnest() or []:
            a = _attrs(r)
            n = a.get("net_code")
            p1, p2 = (a["x1_mm"], a["y1_mm"]), (a["x2_mm"], a["y2_mm"])
            cnt = {}
            for (x1, y1, x2, y2, nc, lay) in tracks:
                if nc == n or nc is None:
                    continue
                if cross(p1, p2, (x1, y1), (x2, y2)):
                    d = cnt.setdefault(nc, {"crossings": 0, "layers": set()})
                    d["crossings"] += 1
                    d["layers"].add(lay)
            cands = sorted(cnt.items(), key=lambda kv: -kv[1]["crossings"])[:max_per_net]
            out.append({"net": n, "name": self.names.get(n, ""), "from": [round(p1[0], 2), round(p1[1], 2)],
                        "to": [round(p2[0], 2), round(p2[1], 2)],
                        "length_mm": round(math.hypot(p2[0] - p1[0], p2[1] - p1[1]), 2),
                        "candidates": [{"net": nc, "name": self.names.get(nc, ""), "crossings": v["crossings"],
                                        "layers": sorted(x for x in v["layers"] if x is not None)}
                                       for nc, v in cands]})
        return {"open_connections": out}

    # ---- via-capable routing (layer changes)
    def _board_health(self) -> tuple[int, int]:
        """(open connections, rule errors that are NOT missing connections)."""
        self.eng.build_connectivity()
        snap = self.eng.get_reward_snapshot(run_drc=True)
        return int(snap.unrouted_count), int(snap.drc_error_count) - int(snap.unrouted_count)

    def via_sites(self, net: int, max_sites: int = 36) -> dict:
        """Numbered candidate via sites for every open connection of `net`: points along and
        beside the straight path, and rings around both ends. A planner (or the Decisions
        API, looking at the board picture with these numbers drawn on it) chooses among them."""
        net = int(net)
        self.eng.build_connectivity()
        out = []
        for ed in self._edges(net):
            x1, y1, x2, y2 = ed["x1_mm"], ed["y1_mm"], ed["x2_mm"], ed["y2_mm"]
            L = math.hypot(x2 - x1, y2 - y1) or 1e-6
            ux, uy = (x2 - x1) / L, (y2 - y1) / L
            px, py = -uy, ux
            pts = []
            for f in (0.5, 0.3, 0.7, 0.15, 0.85):
                for off in (0.0, 0.8, -0.8, 1.6, -1.6, 2.6, -2.6, 4.0, -4.0):
                    pts.append((x1 + ux * L * f + px * off, y1 + uy * L * f + py * off, "path"))
            for (cx, cy, tag) in ((x1, y1, "end1"), (x2, y2, "end2")):
                for r in (1.0, 1.8, 2.8):
                    for k in range(8):
                        a = k * math.pi / 4
                        pts.append((cx + r * math.cos(a), cy + r * math.sin(a), tag))
            seen, sites = set(), []
            for (x, y, tag) in pts:
                key = (round(x, 1), round(y, 1))
                if key in seen:
                    continue
                seen.add(key)
                sites.append({"x": round(x, 3), "y": round(y, 3), "where": tag})
            out.append({"from": [x1, y1, ed.get("layer1")], "to": [x2, y2, ed.get("layer2")],
                        "sites": sites[:max_sites]})
        return {"net": net, "name": self.names.get(net, ""), "connections": out}

    def _via_attempt(self, start, vias, mode: int) -> bool:
        """start=(x, y, human_layer); route start -> via(s) -> finish. Caller checkpoints."""
        e = self.eng
        if e.is_routing():
            e.cancel_route()
        x0, y0, L = start
        if not e.start_route(float(x0), float(y0), int(L)):
            return False
        try:
            r = e.route_item_radius_mm(for_via=True)
            for (vx, vy) in vias:
                if e.pad_block_reason(vx, vy, item_radius_mm=r, for_via=True):
                    return False
                e.set_routing_mode(int(mode))
                before = e.get_via_count()
                e.set_via_placement(True)
                ok = e.fix_route(float(vx), float(vy), force_finish=True, arrive_tol_mm=r, require_via=True)
                placed = e.get_via_count() > before
                if e.is_routing():
                    e.set_via_placement(False)
                if not (ok and placed):
                    return False
                if not e.is_routing():          # session ended at the via: continue from it on the other layer
                    nl = int(e.get_copper_layer_count())
                    cont = False
                    for L2 in [l for l in range(1, nl + 1) if l != L]:
                        if e.start_route(float(vx), float(vy), L2):
                            L, cont = L2, True
                            break
                    if not cont:
                        return False
            e.set_routing_mode(int(mode))
            return bool(e.finish(5))
        finally:
            if e.is_routing():
                e.cancel_route()

    def route_via(self, net: int, mode: int = 2, order: list[int] | None = None, max_trials: int = 120,
                  two_via: bool = True) -> dict:
        """Complete open connections of `net` with layer changes. For each open connection, try
        candidate via sites (optionally in the caller's preferred order of site indices): one via,
        then a two-via bridge (down near one end, back up near the other). A trial is kept only if
        it closes a connection without adding a rule error; otherwise the board is rolled back."""
        e = self.eng
        net = int(net)
        nl = int(e.get_copper_layer_count())
        made, trials, kept = 0, 0, []
        for _ in range(8):
            conns = self.via_sites(net)["connections"]
            if not conns:
                break
            progressed = False
            open0, err0 = self._board_health()
            for conn in conns:
                sites = conn["sites"]
                idx = [i for i in (order or []) if 0 <= i < len(sites)] + \
                      [i for i in range(len(sites)) if i not in (order or [])]
                ends = [conn["from"], conn["to"]]

                def human(board_layer):
                    try:
                        return int(e.layer_map.board_to_human(int(board_layer))) if board_layer is not None and int(board_layer) >= 0 else None
                    except Exception:
                        return None

                l1, l2 = human(ends[0][2]), human(ends[1][2])
                singles, bridges = [], []
                for i in idx:                         # single via from either end, on its pad layer (or any)
                    for end, le in ((ends[0], l1), (ends[1], l2)):
                        for L in ([le] if le else range(1, nl + 1)):
                            singles.append(((end[0], end[1], L), [(sites[i]["x"], sites[i]["y"])]))
                if two_via:
                    def near(pt, k):
                        return sorted(idx, key=lambda i: math.hypot(sites[i]["x"] - pt[0], sites[i]["y"] - pt[1]))[:k]
                    if order:      # ranked by the caller (vision): split the top-ranked sites by the nearer end
                        top = idx[:16]
                        d = lambda i, e_: math.hypot(sites[i]["x"] - e_[0], sites[i]["y"] - e_[1])
                        a = [i for i in top if d(i, ends[0]) <= d(i, ends[1])][:7] or near(ends[0], 4)
                        b = [i for i in top if d(i, ends[0]) > d(i, ends[1])][:7] or near(ends[1], 4)
                    else:
                        a, b = near(ends[0], 7), near(ends[1], 7)
                    pairs = sorted(((i, j) for i in a for j in b if i != j),
                                   key=lambda ij: math.hypot(sites[ij[0]]["x"] - ends[0][0], sites[ij[0]]["y"] - ends[0][1])
                                   + math.hypot(sites[ij[1]]["x"] - ends[1][0], sites[ij[1]]["y"] - ends[1][1]))
                    for i, j in pairs:
                        for L in ([l1] if l1 else range(1, nl + 1)):
                            bridges.append(((ends[0][0], ends[0][1], L),
                                            [(sites[i]["x"], sites[i]["y"]), (sites[j]["x"], sites[j]["y"])]))
                # both pads surface-mount on the same layer: a single via cannot land, so bridges go first
                plans = (bridges + singles) if (l1 and l2 and l1 == l2) else (singles[:40] + bridges + singles[40:])
                for (start, vias) in plans:
                    if trials >= max_trials:
                        break
                    trials += 1
                    h = e.checkpoint()
                    ok = False
                    try:
                        ok = self._via_attempt(start, vias, mode)
                    except Exception:
                        ok = False
                    # The full rule check is the slow part of a trial, and most trials fail. Count open
                    # connections first (connectivity only); run the rule check only for a trial that
                    # actually closed one. A trial is kept on exactly the same test as before.
                    open1, err1 = open0, err0 + 1
                    if ok:
                        e.build_connectivity()
                        if int(e.get_reward_snapshot(run_drc=False).unrouted_count) < open0:
                            open1, err1 = self._board_health()
                    if ok and open1 < open0 and err1 <= err0:
                        e.release_checkpoint(h)
                        made += 1
                        kept.append({"start": [round(v, 2) for v in start[:2]] + [start[2]],
                                     "vias": [[round(v[0], 2), round(v[1], 2)] for v in vias]})
                        progressed = True
                        break
                    e.restore(h)
                    e.release_checkpoint(h)
                    e.build_connectivity()
                if progressed or trials >= max_trials:
                    break
            if not progressed:
                break
        left = len(self._edges(net))
        return {"net": net, "name": self.names.get(net, ""), "connections_made": made, "open_left": left,
                "trials": trials, "kept": kept}

    def checkpoint(self) -> dict:
        return {"handle": int(self.eng.checkpoint())}

    def restore(self, handle: int) -> dict:
        ok = bool(self.eng.restore(int(handle)))
        self.eng.build_connectivity()
        return {"restored": ok}

    def save(self, path: str) -> dict:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if self.eng.is_routing():
            self.eng.cancel_route()
        self.eng.save(path)
        return {"saved": path, "project": os.path.splitext(path)[0] + ".kicad_pro"}

    def score(self, path: str, project: str | None = None) -> dict:
        """PCBWorld's own post-hoc scorer on a SAVED board (fresh engine, like stage 2)."""
        from eval.metrics import evaluate_one
        self.close()            # one live router per process: release ours before the scorer builds its own
        r = evaluate_one(path, project or (os.path.splitext(path)[0] + ".kicad_pro"))
        keep = ("clean_pass", "success", "routability", "drv_errors_only_count", "total_drv_count",
                "wirelength_mm", "total_wirelength", "wirelength", "via_count", "vias", "potential_gain",
                "final_potential", "track_angle_count", "unrouted_count", "drv_errors_only_by_type")
        out = {k: r[k] for k in keep if k in r}
        if "drv_errors_only" in r:
            out["drv_errors_only_count"] = len(r["drv_errors_only"])
        return out

    def render_sites(self, net: int, path: str, margin_mm: float = 5.0, dpi: int = 130) -> dict:
        """Zoomed board picture around the first open connection of `net`, with its two ends
        marked A and B and every candidate via site drawn as a numbered yellow dot - the
        'set of marks' a vision model chooses from."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        vs = self.via_sites(int(net))
        if not vs["connections"]:
            return {"image": None, "sites": []}
        conn = vs["connections"][0]
        sites = conn["sites"]
        xs = [conn["from"][0], conn["to"][0]] + [s_["x"] for s_ in sites]
        ys = [conn["from"][1], conn["to"][1]] + [s_["y"] for s_ in sites]
        fig, ax = plt.subplots(figsize=(10, 9))
        self._draw(ax, mark=False)
        ax.plot([conn["from"][0], conn["to"][0]], [conn["from"][1], conn["to"][1]], "--", color="#e67e22", lw=2)
        for lab, pt in (("A", conn["from"]), ("B", conn["to"])):
            ax.plot(pt[0], pt[1], "*", ms=22, color="#27ae60", mec="black", mew=1.2, zorder=6)
            ax.annotate(lab, (pt[0], pt[1]), fontsize=15, fontweight="bold", color="black", zorder=7,
                        xytext=(6, 6), textcoords="offset points")
        for i, s_ in enumerate(sites):
            ax.plot(s_["x"], s_["y"], "o", ms=13, mfc="#f9e04c", mec="black", mew=0.9, alpha=0.9, zorder=5)
            ax.annotate(str(i), (s_["x"], s_["y"]), fontsize=7.5, ha="center", va="center", zorder=6)
        ax.set_xlim(min(xs) - margin_mm, max(xs) + margin_mm)
        ax.set_ylim(max(ys) + margin_mm, min(ys) - margin_mm)
        ax.set_aspect("equal")
        ax.set_title(f"net {self.names.get(int(net), net)}: open connection A-B; yellow numbers = candidate via sites; "
                     "red = top copper, blue = bottom copper, squares = pads, white circles = existing vias", fontsize=8)
        fig.tight_layout()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fig.savefig(path, dpi=dpi)
        plt.close(fig)
        return {"image": path, "sites": sites, "from": conn["from"], "to": conn["to"], "net": int(net),
                "name": self.names.get(int(net), "")}

    def _draw(self, ax, mark: bool = True) -> None:
        e = self.eng
        colors = ["#c0392b", "#2471a3", "#1e8449", "#8e44ad", "#b7950b", "#5d6d7e"]
        nl = int(e.get_copper_layer_count())
        for t in e.get_tracks() or []:
            a = _attrs(t)
            xs = [a.get("x1_mm"), a.get("x2_mm")]
            ys = [a.get("y1_mm"), a.get("y2_mm")]
            if None in xs or None in ys:
                continue
            lay = a.get("layer", 0) or 0
            try:
                hl = int(e.layer_map.board_to_human(int(lay)))
            except Exception:
                hl = 1
            ax.plot(xs, ys, color=colors[(hl - 1) % len(colors)], lw=2.2 if nl <= 2 else 1.6, alpha=0.8,
                    solid_capstyle="round")
        for p in e.get_pads() or []:
            a = _attrs(p)
            if a.get("x_mm") is not None:
                ax.plot(a["x_mm"], a["y_mm"], "s", ms=5, color="#333", alpha=0.75)
        for v in e.get_vias() or []:
            a = _attrs(v)
            if a.get("x_mm") is not None:
                ax.plot(a["x_mm"], a["y_mm"], "o", ms=5, mfc="white", mec="black", mew=1.0)
        ax.grid(alpha=0.2)

    def render(self, path: str, dpi: int = 110, mark: bool = True) -> dict:
        """Board picture for the vision model: copper per layer, pads, open connections
        (dashed), and rule-check errors (red X)."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        e = self.eng
        colors = ["#c0392b", "#2471a3", "#1e8449", "#8e44ad", "#b7950b", "#5d6d7e"]
        fig, ax = plt.subplots(figsize=(10, 8))
        for t in e.get_tracks() or []:
            a = _attrs(t)
            xs = [a.get("x1_mm", a.get("start_x_mm")), a.get("x2_mm", a.get("end_x_mm"))]
            ys = [a.get("y1_mm", a.get("start_y_mm")), a.get("y2_mm", a.get("end_y_mm"))]
            if None in xs or None in ys:
                continue
            lay = a.get("layer", 1) or 1
            ax.plot(xs, ys, color=colors[(int(lay) - 1) % len(colors)], lw=1.4, alpha=0.85,
                    solid_capstyle="round")
        for p in e.get_pads() or []:
            a = _attrs(p)
            if a.get("x_mm") is None:
                continue
            ax.plot(a["x_mm"], a["y_mm"], "s", ms=3.2, color="#333", alpha=0.7)
        for v in e.get_vias() or []:
            a = _attrs(v)
            if a.get("x_mm") is not None:
                ax.plot(a["x_mm"], a["y_mm"], "o", ms=3.5, mfc="white", mec="black", mew=0.8)
        if mark:
            for r in e.get_ratsnest() or []:
                a = _attrs(r)
                xs = [a.get("x1_mm", a.get("start_x_mm")), a.get("x2_mm", a.get("end_x_mm"))]
                ys = [a.get("y1_mm", a.get("start_y_mm")), a.get("y2_mm", a.get("end_y_mm"))]
                if None not in xs and None not in ys:
                    ax.plot(xs, ys, "--", color="#e67e22", lw=1.2)
                    ax.annotate(self.names.get(a.get("net_code"), str(a.get("net_code"))),
                                ((xs[0] + xs[1]) / 2, (ys[0] + ys[1]) / 2), fontsize=6, color="#a04000")
            for v in e.get_drc_violations() or []:
                a = _attrs(v)
                x, y = a.get("x_mm", a.get("pos_x_mm")), a.get("y_mm", a.get("pos_y_mm"))
                if x is not None and y is not None:
                    ax.plot(x, y, "x", color="red", ms=9, mew=2.2)
        ax.set_aspect("equal")
        ax.invert_yaxis()
        ax.set_xlabel("x (mm)")
        ax.set_ylabel("y (mm)")
        ax.grid(alpha=0.2)
        ax.set_title("red/blue = copper layers 1/2, squares = pads, orange dashed = open connection, red X = rule error",
                     fontsize=8)
        fig.tight_layout()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fig.savefig(path, dpi=dpi)
        plt.close(fig)
        return {"image": path}


def main() -> None:
    s = Session()
    out = sys.stdout
    sys.stdout = sys.stderr          # engine chatter must not corrupt the JSON channel
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        t0 = time.time()
        try:
            req = json.loads(line)
            op = req.pop("op")
            res = getattr(s, op)(**req)
            reply = {"ok": True, "result": res, "s": round(time.time() - t0, 3)}
        except Exception as ex:
            reply = {"ok": False, "error": f"{type(ex).__name__}: {ex}",
                     "trace": traceback.format_exc()[-1200:], "s": round(time.time() - t0, 3)}
        out.write(json.dumps(reply, default=str) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
