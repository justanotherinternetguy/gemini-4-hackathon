"""3D swarm viewer: watch a folder of board files and show the copper being laid, in 3D.

The board is drawn from the real file (outline, components, pads). Every time a new version of
the board lands in the folder, the copper that is new in that version grows along its path, led
by a marker for the agent that produced it. Open connections hang over the board as red lines
and disappear as they are completed. All counts come from KiCad's design-rule checker.

    export KICAD_CLI=/path/to/kicad-cli
    python pcb/view3d.py --watch runs/live --project <board>.kicad_pro --title "Gemini 4 swarm"

File protocol for the producer:
  * Save each new version of the board as a ``.kicad_pcb`` file in the folder. Versions are read
    in modification-time order and are expected to be the same board with more (or less) copper.
  * To credit an agent, end the file name with ``__<agent>``:  ``0007__w3.kicad_pcb`` means
    "worker w3 produced the change in version 7". No suffix = shown as one unnamed router.
  * The first file is the starting board and supplies the components and the open-connection web.

The numbers here are a live check. Reported benchmark numbers come from PCBWorld's own pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import board_parse as bp

HERE = Path(__file__).resolve().parent
MIME = {".js": "text/javascript", ".html": "text/html; charset=utf-8", ".css": "text/css"}


def kicad_cli() -> str | None:
    return os.environ.get("KICAD_CLI") or shutil.which("kicad-cli")


def rule_check(cli: str, board: Path, tmp: Path, project: Path | None) -> dict:
    """Open connections and error-level violations, with positions, from kicad-cli's DRC."""
    work = Path(tempfile.mkdtemp(prefix="drc_", dir=tmp))
    copy = work / "board.kicad_pcb"
    shutil.copyfile(board, copy)
    pro = project if project else board.with_suffix(".kicad_pro")
    if pro.is_file():
        shutil.copyfile(pro, copy.with_suffix(".kicad_pro"))
    report = work / "drc.json"
    try:
        subprocess.run([cli, "pcb", "drc", "--format", "json", "--severity-error", "--units", "mm",
                        "-o", str(report), str(copy)], capture_output=True, text=True, timeout=300)
        data = json.loads(report.read_text())
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}
    finally:
        pass
    pairs = []
    for u in data.get("unconnected_items", []):
        items = u.get("items", [])
        if len(items) >= 2:
            a, b = items[0]["pos"], items[1]["pos"]
            pairs.append([a["x"], a["y"], b["x"], b["y"]])
    viol = []
    for v in data.get("violations", []):
        items = v.get("items", [])
        if items:
            viol.append([items[0]["pos"]["x"], items[0]["pos"]["y"], v.get("type", "")])
    shutil.rmtree(work, ignore_errors=True)
    return {"open": len(data.get("unconnected_items", [])), "violations": len(data.get("violations", [])),
            "open_pairs": pairs, "viol_pts": viol}


class Watcher(threading.Thread):
    def __init__(self, watch: Path, project: Path | None, interval: float):
        super().__init__(daemon=True)
        self.watch, self.project, self.interval = watch, project, interval
        self.cli = kicad_cli()
        self.tmp = Path(tempfile.mkdtemp(prefix="pcb3d_"))
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.lock = threading.Lock()
        self.frames: list[dict] = []      # light metadata, sent on every poll
        self.heavy: list[dict] = []       # copper + positions, fetched once per frame
        self.scene: dict | None = None
        self.seen: dict[str, float] = {}
        self.rev = 0
        self.t0: float | None = None

    def scan_once(self) -> None:
        files = sorted(self.watch.glob("*.kicad_pcb"), key=lambda p: (p.stat().st_mtime, p.name))
        for f in files:
            st = f.stat()
            if self.seen.get(f.name) == st.st_mtime:
                continue
            time.sleep(0.12)                       # let a writer finish
            if not f.exists() or f.stat().st_size != st.st_size:
                continue
            self.seen[f.name] = st.st_mtime
            self.add(f, st.st_mtime)

    def add(self, board: Path, mtime: float) -> None:
        cu = bp.copper(board)
        if self.scene is None:
            self.scene = bp.scene(board)
        if self.t0 is None:
            self.t0 = mtime
        m = re.search(r"__([A-Za-z0-9_.\-]+)$", board.stem)
        with self.lock:
            idx = len(self.frames)
            self.frames.append({"id": idx, "name": board.name, "agent": m.group(1) if m else "",
                                "t": round(mtime - self.t0, 2), "open": None, "violations": None,
                                "length_mm": cu["length_mm"], "segments": len(cu["segments"]),
                                "vias": len(cu["vias"]), "checked": False})
            self.heavy.append({"id": idx, "segments": cu["segments"], "vias": cu["vias"],
                               "open_pairs": None, "viol_pts": None})
            self.rev += 1
        print(f"[v{idx}] {board.name}: {len(cu['segments'])} tracks, {len(cu['vias'])} vias", flush=True)
        if self.cli:
            snapshot = self.tmp / f"v{idx:05d}.kicad_pcb"
            shutil.copyfile(board, snapshot)
            pro = self.project or board.with_suffix(".kicad_pro")
            self.pool.submit(self.check, idx, snapshot, pro if pro.is_file() else None)

    def check(self, idx: int, snapshot: Path, project: Path | None) -> None:
        try:
            r = rule_check(self.cli, snapshot, self.tmp, project)
        except Exception as e:
            print(f"[check v{idx}] {type(e).__name__}: {e}", flush=True)
            return
        if not r:
            return
        with self.lock:
            self.frames[idx].update(open=r["open"], violations=r["violations"], checked=True)
            self.heavy[idx].update(open_pairs=r["open_pairs"], viol_pts=r["viol_pts"])
            self.rev += 1
        print(f"[v{idx}] open={r['open']} violations={r['violations']}", flush=True)

    def run(self) -> None:
        while True:
            try:
                self.scan_once()
            except Exception as e:
                print(f"[watcher] {type(e).__name__}: {e}", flush=True)
            time.sleep(self.interval)


def make_handler(w: Watcher, title: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code: int, ctype: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def js(self, obj) -> None:
            self.send(200, "application/json", json.dumps(obj).encode())

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                html = (HERE / "view3d.html").read_text().replace("__TITLE__", title)
                return self.send(200, MIME[".html"], html.encode())
            if path == "/state":
                with w.lock:
                    return self.js({"rev": w.rev, "frames": list(w.frames), "has_scene": w.scene is not None,
                                    "checker": bool(w.cli)})
            if path == "/scene":
                return self.js(w.scene or {})
            m = re.fullmatch(r"/frame/(\d+)", path)
            if m and int(m.group(1)) < len(w.heavy):
                with w.lock:
                    return self.js(w.heavy[int(m.group(1))])
            m = re.fullmatch(r"/vendor/([\w.\-]+)", path)
            if m and (HERE / "vendor" / m.group(1)).is_file():
                f = HERE / "vendor" / m.group(1)
                return self.send(200, MIME.get(f.suffix, "application/octet-stream"), f.read_bytes())
            self.send(404, "text/plain", b"not found")

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch", required=True, help="folder the producer saves .kicad_pcb versions into")
    ap.add_argument("--project", help="the board's .kicad_pro (its design rules)")
    ap.add_argument("--title", default="Routing swarm")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--interval", type=float, default=0.3)
    args = ap.parse_args()

    watch = Path(args.watch)
    watch.mkdir(parents=True, exist_ok=True)
    project = Path(args.project) if args.project else None
    if project is not None and not project.is_file():
        raise SystemExit(f"--project {project} is not a file")
    w = Watcher(watch, project, args.interval)
    if not w.cli:
        print("kicad-cli not found (set KICAD_CLI): showing copper only, no rule check", flush=True)
    w.start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(w, args.title))
    print(f"watching {watch.resolve()}  ->  http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
