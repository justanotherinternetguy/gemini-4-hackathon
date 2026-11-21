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
