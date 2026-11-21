"""Spec JSON -> placed board -> Specctra DSN -> Freerouting -> routed board -> KiCad DRC -> pictures.

Run with the SYSTEM python (it has KiCad's `pcbnew` module; our venv does not):

    /usr/bin/python3 -m pcb.create.build --spec board.json --out DIR

No language model is used here. Scope, stated plainly: a small-board generator for
through-hole/simple parts with automatic (column) placement. The netlist in the spec is taken as
given (when it comes from `describe` it was proposed by a language model and is NOT verified by
simulation). KiCad's design rule check is the only judge; a board is only called good when DRC
reports 0 errors and nothing is left unconnected.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from pcb.create import catalog

ROOT = Path(__file__).resolve().parents[2]
FP_ROOT = Path(os.environ.get("KICAD_FOOTPRINT_DIR", "/usr/share/kicad/footprints"))
FREEROUTING_JAR = Path(os.environ.get(
    "FREEROUTING_JAR", ROOT / "third_party/pcbworld/external/freerouting/freerouting-2.1.0.jar"))
JAVA = os.environ.get("PCB_CREATE_JAVA", "/home/thinkies/miniforge3/envs/pcbworld/bin/java")

ORIGIN_MM = (100.0, 80.0)   # where the board's top-left corner sits on the KiCad sheet
GAP_MM = 2.0                # gap between neighbouring parts in a column
COL_GAP_MM = 3.5            # gap between columns
MARGIN_MM = 3.0             # parts-to-board-edge margin
MAX_COL_H_MM = 42.0
TRACK_MM = 0.4


class Events:
    def __init__(self, path: Path):
        self.path = path
        keep = []
        if path.exists():  # keep what `describe` wrote, drop events of an earlier build
            for line in path.read_text().splitlines():
                try:
                    if json.loads(line).get("src") == "describe":
                        keep.append(line)
                except Exception:
                    pass
        path.write_text("".join(k + "\n" for k in keep))
        self.has_describe = bool(keep)

    def emit(self, type_: str, **kw):
        with open(self.path, "a") as f:
            f.write(json.dumps({"t": round(time.time(), 3), "type": type_, "src": "build", **kw}) + "\n")
        print(f"[{type_}] " + json.dumps(kw)[:300], flush=True)


def mm(v):
    import pcbnew
    return pcbnew.FromMM(float(v))


def fp_bbox_mm(fp):
    """Bounding box of a footprint (pads + drawings, no text) in mm: (x0, y0, x1, y1)."""
    import pcbnew
    bb = None
    for args in ((False,), (False, False), ()):
        try:
            bb = fp.GetBoundingBox(*args)
            break
        except TypeError:
            continue
    return tuple(pcbnew.ToMM(v) for v in (bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom()))


def move_to(fp, x0, y0):
    """Move footprint so its bbox top-left is at (x0, y0) mm, snapped to a 0.635 mm grid."""
    import pcbnew
    bx0, by0, _, _ = fp_bbox_mm(fp)
    p = fp.GetPosition()
    g = 0.635
    nx = round((pcbnew.ToMM(p.x) + x0 - bx0) / g + 0.499) * g
    ny = round((pcbnew.ToMM(p.y) + y0 - by0) / g + 0.499) * g
    fp.SetPosition(pcbnew.VECTOR2I(mm(nx), mm(ny)))


def place(spec, board):
    """Deterministic column placement. Returns (footprints by ref, board rect mm, notes)."""
    import pcbnew
    fps, role = {}, {}
    for c in spec["components"]:
        e = catalog.lookup(c["type"])
        fp = pcbnew.FootprintLoad(str(FP_ROOT / (e["lib"] + ".pretty")), e["footprint"])
        if fp is None:
            raise RuntimeError(f"footprint {e['lib']}:{e['footprint']} not found under {FP_ROOT}")
        fp.SetReference(c["ref"])
        fp.SetValue(str(c.get("value") or c["type"]))
        fp.SetPosition(pcbnew.VECTOR2I(0, 0))
        board.Add(fp)
        fps[c["ref"]] = fp
        role[c["ref"]] = e["role"]

    net_of = {}
    for n in spec["nets"]:
        for p in n["pins"]:
            net_of[p] = n["name"]
    net_size = {n["name"]: len(n["pins"]) for n in spec["nets"]}
    ics = [r for r in fps if role[r] == "ic"]
    conns = [r for r in fps if role[r] == "connector"]
    passives = [r for r in fps if role[r] == "passive"]

    def pad_local(ref, pin):
        fp = fps[ref]
        x0, y0, x1, y1 = fp_bbox_mm(fp)
        for pad in fp.Pads():
            if pad.GetNumber() == pin:
                pos = pad.GetPosition()
                return pcbnew.ToMM(pos.x) - (x0 + x1) / 2, pcbnew.ToMM(pos.y) - y0
        return 0.0, 0.0

    # Passives: (1) "direct" = sits beside the IC pin it shares its smallest net with;
    # (2) "follower" = shares a small net with a direct passive (e.g. the LED after its resistor),
    # sits one column further out, level with it; (3) otherwise beside any IC pin; (4) else loose.
    nets_of = {r: sorted({net_of[k] for k in net_of if k.startswith(r + ".")}, key=lambda n: net_size[n])
               for r in passives}
    members = {}
    for k, n in net_of.items():
        members.setdefault(n, []).append(k)

    def ic_pin(net):
        for k in members[net]:
            if k.split(".")[0] in ics:
                return k
        return None

    def my_pin(r, net):
        return next(k.split(".")[1] for k in members[net] if k.startswith(r + "."))

    left = {u: [] for u in ics}
    right = {u: [] for u in ics}
    fleft = {u: [] for u in ics}
    fright = {u: [] for u in ics}
    where = {}   # passive -> (ic, side_left, dy)
    loose = []

    def flip(r):
        fp = fps[r]
        fp.SetOrientationDegrees((fp.GetOrientationDegrees() + 180) % 360)
        x0, y0, x1, y1 = fp_bbox_mm(fp)  # ...and put the reference label back above the part
        tp = fp.Reference().GetPosition()
        fp.Reference().SetPosition(pcbnew.VECTOR2I(mm(x0 + x1) - tp.x, mm(y0 + y1) - tp.y))

    def turn(r, pin, side_left):
        mdx, _ = pad_local(r, pin)
        if (side_left and mdx < -0.1) or (not side_left and mdx > 0.1):
            flip(r)  # turn the connected pin towards the IC

    def make_direct(r, net):
        oref, opin = ic_pin(net).split(".")
        dx, dy = pad_local(oref, opin)
        where[r] = (oref, dx < 0, dy)
        turn(r, my_pin(r, net), dx < 0)

    todo = []
    for r in passives:
        small = [n for n in nets_of[r] if net_size[n] == net_size[nets_of[r][0]] and ic_pin(n)]
        if small:
            make_direct(r, small[0])
        else:
            todo.append(r)
    for u in ics:  # balance a lopsided IC: move overflow to the emptier side
        for want_left in (True, False):
            a = [r for r in where if where[r][0] == u and where[r][1] == want_left]
            b = [r for r in where if where[r][0] == u and where[r][1] != want_left]
            while len(a) - len(b) > 2:
                r = a.pop()
                b.append(r)
                where[r] = (u, not want_left, where[r][2])
                flip(r)
    follower = {}
    while todo:
        progress = False
        for r in list(todo):
            for n in nets_of[r]:
                lead = [k.split(".")[0] for k in members[n] if k.split(".")[0] in where
                        and k.split(".")[0] not in follower]
                if net_size[n] <= 3 and lead:
                    u, side_left, dy = where[lead[0]]
                    follower[r] = lead[0]
                    where[r] = (u, side_left, dy)
                    turn(r, my_pin(r, n), side_left)
                    todo.remove(r)
                    progress = True
                    break
        if progress:
            continue
        cands = sorted((net_size[n], i, r, n) for i, r in enumerate(todo) for n in nets_of[r] if ic_pin(n))
        if not cands:
            loose += todo
            break
        _, _, r, n = cands[0]
        make_direct(r, n)
        todo.remove(r)
    for r, (u, side_left, dy) in where.items():
        tgt = (fleft if side_left else fright) if r in follower else (left if side_left else right)
        tgt[u].append((dy, r))

    columns = []  # list of lists of refs

    def add_cols(refs):
        col, h = [], 0.0
        for r in refs:
            x0, y0, x1, y1 = fp_bbox_mm(fps[r])
            if col and h + (y1 - y0) > MAX_COL_H_MM:
                columns.append(col)
                col, h = [], 0.0
            col.append(r)
            h += (y1 - y0) + GAP_MM
        if col:
            columns.append(col)

    add_cols(conns[0::2])                      # connectors on the left edge
    for u in ics:
        add_cols([r for _, r in sorted(fleft[u])])
        add_cols([r for _, r in sorted(left[u])])
        add_cols([u])
        add_cols([r for _, r in sorted(right[u])])
        add_cols([r for _, r in sorted(fright[u])])
    add_cols(loose)
    add_cols(conns[1::2])                      # and on the right edge

    def size(r):
        x0, y0, x1, y1 = fp_bbox_mm(fps[r])
        return x1 - x0, y1 - y0

    col_w = [max(size(r)[0] for r in col) for col in columns]
    col_h = [sum(size(r)[1] for r in col) + GAP_MM * (len(col) - 1) for col in columns]
    need_w = sum(col_w) + COL_GAP_MM * (len(columns) - 1) + 2 * MARGIN_MM
    need_h = max(col_h) + 2 * MARGIN_MM
    want = spec.get("board") or {}
    W = max(need_w, float(want.get("width_mm") or 0))
    H = max(need_h, float(want.get("height_mm") or 0))
    W, H = (round(v / 1.27 + 0.499) * 1.27 for v in (W, H))
    notes = []
    if want and (need_w > want.get("width_mm", 0) or need_h > want.get("height_mm", 0)):
        notes.append(f"requested board {want.get('width_mm')} x {want.get('height_mm')} mm was too small for "
                     f"the automatic placement; grown to {W:.1f} x {H:.1f} mm")
    ox, oy = ORIGIN_MM
    spare = (W - need_w) / max(1, len(columns) - 1) if len(columns) > 1 else 0.0
    x = ox + MARGIN_MM + (0.0 if len(columns) > 1 else (W - need_w) / 2)
    for col, cw, ch in zip(columns, col_w, col_h):
        y = oy + (H - ch) / 2
        for r in col:
            w, h = size(r)
            move_to(fps[r], x + (cw - w) / 2, y)
            y += h + GAP_MM
        x += cw + COL_GAP_MM + spare

    rect = pcbnew.PCB_SHAPE(board)
    rect.SetShape(pcbnew.SHAPE_T_RECT)
    rect.SetStart(pcbnew.VECTOR2I(mm(ox), mm(oy)))
    rect.SetEnd(pcbnew.VECTOR2I(mm(ox + W), mm(oy + H)))
    rect.SetLayer(pcbnew.Edge_Cuts)
    rect.SetWidth(mm(0.1))
    board.Add(rect)
    return fps, (W, H), notes, columns


def assign_nets(spec, board, fps):
    import pcbnew
    n_conn = 0
    for n in spec["nets"]:
        net = pcbnew.NETINFO_ITEM(board, n["name"])
        board.Add(net)
        pads = 0
        for p in sorted(set(n["pins"])):
            ref, pin = p.split(".")
            for pad in fps[ref].Pads():
                if pad.GetNumber() == pin:
                    pad.SetNet(net)
                    pads += 1
        n_conn += max(0, pads - 1)
    return n_conn


def run(cmd, timeout, cwd=None, log=None):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        out, rc = (p.stdout or "") + (p.stderr or ""), p.returncode
    except subprocess.TimeoutExpired as e:
        out, rc = f"TIMEOUT after {timeout}s\n{e.stdout or ''}", -9
    if log:
        Path(log).write_text(out)
    return rc, out, time.time() - t0


def pictures(pcb: Path, stem: Path):
    """SVG (copper + silk + outline) and a PNG of it. Returns list of files made."""
    made = []
    svg = stem.with_suffix(".svg")
    rc, out, _ = run(["kicad-cli", "pcb", "export", "svg", "--layers", "B.Cu,F.Cu,F.SilkS,Edge.Cuts",
                      "--mode-single", "--page-size-mode", "2", "--exclude-drawing-sheet",
                      "--output", str(svg), str(pcb)], 120)
    if rc == 0 and svg.exists():
        made.append(svg.name)
        png = stem.with_suffix(".png")
        if shutil.which("rsvg-convert"):
            rc, _, _ = run(["rsvg-convert", "-w", "1400", "-b", "#10151c", "-o", str(png), str(svg)], 60)
            if rc == 0 and png.exists():
                made.append(png.name)
    return made


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--passes", type=int, default=10, help="Freerouting max passes")
    ap.add_argument("--route-timeout", type=int, default=420)
    ap.add_argument("--render3d", action="store_true", help="also make a kicad-cli 3D render (slower)")
    a = ap.parse_args(argv)

    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    ev = Events(out / "events.jsonl")
    t_start = time.time()
    spec = json.loads(Path(a.spec).read_text())
    if Path(a.spec).resolve() != (out / "board.json").resolve():
        (out / "board.json").write_text(json.dumps(spec, indent=2))
    result = {"scope": catalog.SCOPE_NOTE, "ok": False, "routed": False, "files": {"spec": "board.json"},
              "notes": []}

    def finish(reason=None):
        if reason:
            result["failed_at"] = reason
        result["seconds"] = round(time.time() - t_start, 1)
        (out / "result.json").write_text(json.dumps(result, indent=2))
        write_explain(out, spec, result)
        ev.emit("done", ok=result["ok"], routed=result["routed"],
                unrouted_connections=result.get("unrouted_connections"),
                drc_errors=result.get("drc_errors"), failed_at=reason)
        return 0 if result["ok"] else 1

    errs = catalog.validate(spec)
    if errs:
        result["validation_errors"] = errs
        ev.emit("spec_valid", valid=False, errors=errs)
        return finish("spec validation")
    if not ev.has_describe:
        ev.emit("spec_valid", valid=True, components=len(spec["components"]), nets=len(spec["nets"]))
    result["components"] = [
        {**{k: c.get(k) for k in ("ref", "type", "value", "purpose")},
         "footprint": "{lib}:{footprint}".format(**catalog.lookup(c["type"]))} for c in spec["components"]]
    result["nets"] = {n["name"]: sorted(set(n["pins"])) for n in spec["nets"]}

    import pcbnew
    result["kicad_version"] = pcbnew.Version()
    pcb_path = out / "board.kicad_pcb"
    for stale in ("board.kicad_pcb", "board.kicad_pro", "board.kicad_prl", "board.dsn", "board.ses",
                  "board_routed.kicad_pcb", "board_routed.kicad_pro", "board_routed.kicad_prl", "drc.json"):
        (out / stale).unlink(missing_ok=True)

    # ---- place
    board = pcbnew.NewBoard(str(pcb_path))
    try:
        nc = board.GetDesignSettings().m_NetSettings.GetDefaultNetclass()
        nc.SetTrackWidth(mm(TRACK_MM))
    except Exception as e:  # keep KiCad defaults
        result["notes"].append(f"could not set track width ({e}); KiCad default used")
    fps, (W, H), notes, columns = place(spec, board)
    result["notes"] += notes
    total_conn = assign_nets(spec, board, fps)
    board.BuildConnectivity()
    pcbnew.SaveBoard(str(pcb_path), board)
    if not (out / "board.kicad_pro").exists():
        (out / "board.kicad_pro").write_text(json.dumps({"meta": {"filename": "board.kicad_pro", "version": 1}}))
    result["board_mm"] = [round(W, 2), round(H, 2)]
    result["connections_total"] = total_conn
    result["placement"] = {r: [round(pcbnew.ToMM(fp.GetPosition().x) - ORIGIN_MM[0], 2),
                               round(pcbnew.ToMM(fp.GetPosition().y) - ORIGIN_MM[1], 2)]
                           for r, fp in fps.items()}
    result["files"]["placed_board"] = pcb_path.name
    pics = pictures(pcb_path, out / "placed")
    result["files"]["placed_pictures"] = pics
    ev.emit("placed", board_mm=result["board_mm"], components=len(fps), connections=total_conn,
            columns=columns, placement=result["placement"], pictures=pics)

    # ---- route (Specctra DSN -> Freerouting -> SES)
    dsn, ses = out / "board.dsn", out / "board.ses"
    if not pcbnew.ExportSpecctraDSN(board, str(dsn)) or not dsn.exists():
        return finish("Specctra DSN export")
    rc, log, secs = run([JAVA, "-Djava.awt.headless=true", "-jar", str(FREEROUTING_JAR), "-de", str(dsn),
                         "-do", str(ses), "-mp", str(a.passes), "-mt", "1"], a.route_timeout, cwd=str(out),
                        log=out / "freerouting.log")
    result["files"]["freerouting_log"] = "freerouting.log"
    result["route_seconds"] = round(secs, 1)
    if not ses.exists() or ses.stat().st_size == 0:
        result["notes"].append(f"Freerouting produced no session file (exit {rc})")
        ev.emit("routed", ok=False, seconds=round(secs, 1), exit=rc)
        return finish("Freerouting")
    if not pcbnew.ImportSpecctraSES(board, str(ses)):
        ev.emit("routed", ok=False, seconds=round(secs, 1), reason="session import failed")
        return finish("Specctra session import")
    board.BuildConnectivity()
    routed_path = out / "board_routed.kicad_pcb"
    pcbnew.SaveBoard(str(routed_path), board)
    if (out / "board.kicad_pro").exists():
        shutil.copy(out / "board.kicad_pro", out / "board_routed.kicad_pro")
    tracks = list(board.GetTracks())
    vias = sum(1 for t in tracks if t.GetClass() == "PCB_VIA")
    segs = [t for t in tracks if t.GetClass() != "PCB_VIA"]
    wl = sum(pcbnew.ToMM(t.GetLength()) for t in segs)
    try:
        unconn_py = int(board.GetConnectivity().GetUnconnectedCount(True))
    except Exception:
        unconn_py = None
    result.update(track_segments=len(segs), vias=vias, wirelength_mm=round(wl, 1),
                  unrouted_connections_pcbnew=unconn_py)
    result["files"].update(dsn=dsn.name, ses=ses.name, routed_board=routed_path.name)
    pics = pictures(routed_path, out / "board")
    result["files"]["pictures"] = pics
    if a.render3d:
        rc3, _, _ = run(["kicad-cli", "pcb", "render", "--side", "top", "--width", "1400", "--height", "900",
                         "--output", str(out / "render_top.png"), str(routed_path)], 300)
        if rc3 == 0 and (out / "render_top.png").exists():
            result["files"]["render3d"] = "render_top.png"
    ev.emit("routed", ok=True, seconds=round(secs, 1), track_segments=len(segs), vias=vias,
            wirelength_mm=round(wl, 1), unrouted_connections_pcbnew=unconn_py, pictures=pics)

    # ---- judge: KiCad DRC
    drc_path = out / "drc.json"
    rc, log, _ = run(["kicad-cli", "pcb", "drc", "--format", "json", "--severity-all", "--output", str(drc_path),
                      str(routed_path)], 180, log=out / "drc.log")
    if not drc_path.exists():
        result["notes"].append("kicad-cli pcb drc produced no report: " + log[-300:])
        return finish("KiCad DRC")
    drc = json.loads(drc_path.read_text())
    viol = drc.get("violations", [])
    unconn = drc.get("unconnected_items", [])
    by = {"error": {}, "warning": {}}
    for v in viol:
        d = by.setdefault(v.get("severity", "error"), {})
        d[v.get("type", "?")] = d.get(v.get("type", "?"), 0) + 1
    n_err = sum(by["error"].values())
    result.update(drc_errors=n_err, drc_errors_by_type=by["error"],
                  drc_warnings=sum(by["warning"].values()), drc_warnings_by_type=by["warning"],
                  unrouted_connections=len(unconn),
                  unrouted_items=[" <-> ".join(i.get("description", "?") for i in u.get("items", []))
                                  for u in unconn][:20])
    result["files"]["drc"] = drc_path.name
    result["routed"] = len(unconn) == 0 and len(segs) > 0
    result["ok"] = result["routed"] and n_err == 0
    ev.emit("drc", errors=n_err, errors_by_type=by["error"], warnings=result["drc_warnings"],
            unrouted_connections=len(unconn), ok=result["ok"])
    return finish()


def write_explain(out: Path, spec: dict, r: dict):
    L = ["# Board build report", "",
         f"> {catalog.SCOPE_NOTE}", ""]
    if r.get("validation_errors"):
        L += ["## Verdict: NOT BUILT - the board description failed validation", ""]
        L += [f"- {e}" for e in r["validation_errors"]]
        (out / "explain.md").write_text("\n".join(L) + "\n")
        return
    if r.get("ok"):
        L += [f"## Verdict: layout passes KiCad's rule check",
              f"All {r.get('connections_total')} connections are routed and KiCad DRC reports 0 errors "
              f"({r.get('drc_warnings', 0)} warnings). This says the copper matches the netlist below and obeys "
              "the spacing rules. It does NOT say the circuit works: the netlist itself was not simulated or "
              "reviewed."]
    elif r.get("failed_at"):
        L += [f"## Verdict: NOT a finished board - failed at: {r['failed_at']}"]
    else:
        L += ["## Verdict: NOT a good board",
              f"{r.get('unrouted_connections')} connection(s) left unrouted, {r.get('drc_errors')} DRC error(s)."]
    L += ["", f"Purpose: {spec.get('title') or ''} {('- ' + spec['description']) if spec.get('description') else ''}".rstrip(),
          ""]
    if r.get("board_mm"):
        L += [f"Board: {r['board_mm'][0]} x {r['board_mm'][1]} mm, 2 copper layers, through-hole parts, "
              f"{TRACK_MM} mm tracks.", ""]
    L += ["## Parts and what each is for", "", "| Ref | Type | Value | Footprint | Purpose |", "|---|---|---|---|---|"]
    for c in r.get("components", []):
        L.append(f"| {c['ref']} | {c['type']} | {c.get('value') or ''} | `{c['footprint']}` | {c.get('purpose') or ''} |")
    L += ["", "## Nets (which pins are wired together)", ""]
    for name, pins in r.get("nets", {}).items():
        L.append(f"- **{name}**: {', '.join(pins)}")
    if spec.get("assumptions"):
        L += ["", "## Assumptions the model said it made", ""] + [f"- {x}" for x in spec["assumptions"]]
    if spec.get("source"):
        L += ["", f"Source request: {spec['source'].get('text') or '(sketch only)'}"
              + (f" (+ sketch image {spec['source']['image']})" if spec['source'].get('image') else "")
              + f" - interpreted by {spec['source'].get('model')}."]
    L += ["", "## What the checks found", ""]
    if "track_segments" in r:
        L.append(f"- Freerouting ({r.get('route_seconds')} s): {r['track_segments']} track segments, {r['vias']} vias, "
                 f"{r['wirelength_mm']} mm of copper track.")
    if "drc_errors" in r:
        L.append(f"- Unrouted connections (KiCad DRC): {r['unrouted_connections']} of {r.get('connections_total')}.")
        for u in r.get("unrouted_items", []):
            L.append(f"  - {u}")
        L.append(f"- KiCad DRC errors: {r['drc_errors']}" + (f" {r['drc_errors_by_type']}" if r["drc_errors"] else ""))
        L.append(f"- KiCad DRC warnings (not counted against the board): {r['drc_warnings']}"
                 + (f" {r['drc_warnings_by_type']}" if r["drc_warnings"] else ""))
    for n in r.get("notes", []):
        L.append(f"- Note: {n}")
    L += ["", "## Limits", "",
          "- Parts come from a small fixed catalogue (" + ", ".join(sorted(catalog._FIXED)) + ", pin_header_1xN); "
          "the IC is a generic DIP footprint, so the pin numbers in the netlist are only as right as the model's "
          "memory of the datasheet.",
          "- Placement is a simple deterministic column layout, not optimised.",
          "- No schematic, no simulation, no current/thermal/EMC checks. Have someone check the netlist before "
          "ordering the board.", "",
          "## Files", ""]
    for k, v in r.get("files", {}).items():
        L.append(f"- {k}: {v if isinstance(v, str) else ', '.join(v)}")
    (out / "explain.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
