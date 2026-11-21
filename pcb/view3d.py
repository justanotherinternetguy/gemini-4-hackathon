"""3D routing studio: import a board, watch it assemble, press Route, watch the swarm lay copper.

Flow in the page:
  1. Import: drop a ``.kicad_pcb`` file on the page, or pick one of the benchmark boards.
  2. The board is built piece by piece from the file (outline, then each component and its pads),
     then the parts turn to glass and every connection still to make is shown as a red line.
  3. Route: the configured producer command is started. It saves new versions of the board into
     the run folder; each version's new copper is animated, led by the agent that produced it.
  4. Verdict: KiCad's design-rule checker decides. Fully connected with zero violations is a
     clean pass; otherwise the page lists exactly which connections are left for the engineer.

    export KICAD_CLI=/path/to/kicad-cli
    python pcb/view3d.py --boards-dir $PCBWORLD_DATA_ROOT/pcbench/exacad_sorted \
        --route-cmd 'python my_swarm.py --board {unrouted} --project {project} --out {out}'

Producer protocol (what --route-cmd has to do):
  * Save each new version of the board as a ``.kicad_pcb`` file in ``{out}``. Versions are read in
    modification-time order and are the same board with more (or less) copper.
  * To credit an agent, end the file name with ``__<agent>``:  ``0007__w3.kicad_pcb``.
  * Exit when finished. The verdict is shown once the command has exited.
Placeholders: {unrouted} starting board, {project} its .kicad_pro ('' if none), {out} run folder,
{routed} a finished version of the same board when one is known (benchmark boards have the
human original).

Without --route-cmd the built-in DUMMY producer runs: made-up agents replay a finished routing.
The page marks that clearly as test data. It only works for boards that have a finished version.

The numbers here are a live check. Reported benchmark numbers come from PCBWorld's own pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import board_parse as bp

HERE = Path(__file__).resolve().parent
MIME = {".js": "text/javascript", ".html": "text/html; charset=utf-8", ".css": "text/css"}
COPPER_ITEM_RE = re.compile(r"\n\t\((?:segment|via|arc)\b.*?\n\t\)", re.S)
DUMMY_CMD = (f"{shlex.quote(sys.executable)} {shlex.quote(str(HERE / 'make_test_frames.py'))} "
             "--routed {routed} --out {out} --agents 5 --burst 5 --delay 2.2 --no-start")


def kicad_cli() -> str | None:
    return os.environ.get("KICAD_CLI") or shutil.which("kicad-cli")


def rule_check(cli: str, board: Path, tmp: Path, project: Path | None) -> dict:
    """Open connections and error-level violations, with positions, from kicad-cli's DRC."""
    work = Path(tempfile.mkdtemp(prefix="drc_", dir=tmp))
    copy = work / "board.kicad_pcb"
    shutil.copyfile(board, copy)
    if project and project.is_file():
        shutil.copyfile(project, copy.with_suffix(".kicad_pro"))
    report = work / "drc.json"
    try:
        subprocess.run([cli, "pcb", "drc", "--format", "json", "--severity-error", "--units", "mm",
                        "-o", str(report), str(copy)], capture_output=True, text=True, timeout=300)
        data = json.loads(report.read_text())
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    pairs = []
    for u in data.get("unconnected_items", []):
        items = u.get("items", [])
        if len(items) >= 2:
            a, b = items[0], items[1]
            pairs.append([a["pos"]["x"], a["pos"]["y"], b["pos"]["x"], b["pos"]["y"],
                          a.get("description", ""), b.get("description", "")])
    viol = []
    for v in data.get("violations", []):
        items = v.get("items", [])
        if items:
            viol.append([items[0]["pos"]["x"], items[0]["pos"]["y"], v.get("description", v.get("type", "")),
                         items[0].get("description", "")])
    return {"open": len(data.get("unconnected_items", [])), "violations": len(data.get("violations", [])),
            "open_pairs": pairs, "viol_pts": viol}


class Studio:
    """One loaded board, its run folder, the versions seen so far and the producer process."""

    def __init__(self, boards_dir: Path | None, route_cmd: str | None, interval: float):
        self.boards_dir, self.interval = boards_dir, interval
        self.route_cmd = route_cmd or DUMMY_CMD
        self.dummy = route_cmd is None
        self.cli = kicad_cli()
        self.tmp = Path(tempfile.mkdtemp(prefix="pcb3d_"))
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.lock = threading.RLock()
        self.epoch = 0
        self.board: dict | None = None
        self.scene: dict | None = None
        self.frames: list[dict] = []
        self.heavy: list[dict] = []
        self.seen: dict[str, float] = {}
        self.t0: float | None = None
        self.proc: subprocess.Popen | None = None
        self.route_started: float | None = None
        self.route_seconds: float | None = None
        self.failed: set[str] = set()
        res = HERE / "bench" / "results" / "freerouting_d3a.json"
        if res.is_file():
            try:
                self.failed = {b["board"] for b in json.loads(res.read_text())["boards"] if not b["clean_pass"]}
            except (KeyError, json.JSONDecodeError):
                pass
        threading.Thread(target=self.loop, daemon=True).start()

    # ----- boards -----
    def list_boards(self) -> list[dict]:
        if not self.boards_dir or not self.boards_dir.is_dir():
            return []
        out = []
        for d in sorted(self.boards_dir.iterdir()):
            if (d / "processed_v9_guide_v3_unrouted.kicad_pcb").is_file():
                out.append({"name": d.name, "router_failed": d.name in self.failed})
        return out

    def load_benchmark(self, name: str) -> None:
        d = (self.boards_dir / name) if self.boards_dir else None
        if not d or not (d / "processed_v9_guide_v3_unrouted.kicad_pcb").is_file() or "/" in name:
            raise ValueError("unknown board")
        pro = d / "processed_v9_guide_v3.kicad_pro"
        self._load(name, d / "processed_v9_guide_v3_unrouted.kicad_pcb", d / "processed_v9_guide_v3.kicad_pcb",
                   pro if pro.is_file() else None)

    def load_upload(self, name: str, text: str) -> None:
        up = Path(tempfile.mkdtemp(prefix="upload_", dir=self.tmp))
        safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", name) or "board.kicad_pcb"
        routed = up / safe
        routed.write_text(text)
        has_copper = bool(COPPER_ITEM_RE.search(text))
        unrouted = up / ("unrouted_" + safe)
        unrouted.write_text(COPPER_ITEM_RE.sub("", text))
        self._load(Path(safe).stem, unrouted, routed if has_copper else None, None)

    def _load(self, name: str, unrouted: Path, routed: Path | None, project: Path | None) -> None:
        self.stop()
        scene = bp.scene(unrouted)
        run = Path(tempfile.mkdtemp(prefix="run_", dir=self.tmp))
        with self.lock:
            self.epoch += 1
            self.board = {"name": name, "unrouted": unrouted, "routed": routed, "project": project, "run": run}
            self.scene = scene
            self.frames, self.heavy, self.seen, self.t0 = [], [], {}, None
            self.attempt = 0
            self.route_started = self.route_seconds = None
        shutil.copyfile(unrouted, run / "v000.kicad_pcb")
        print(f"[load] {name}: {len(scene['components'])} components, {len(scene['pads'])} pads", flush=True)

    # ----- routing -----
    def route(self, leave: int = 0) -> str:
        with self.lock:
            b = self.board
            if not b:
                return "no board loaded"
            if self.proc and self.proc.poll() is None:
                return "already routing"
            if "{routed}" in self.route_cmd and not b["routed"]:
                return "this board has no finished version for the dummy run to replay"
            cmd = self.route_cmd.format(unrouted=shlex.quote(str(b["unrouted"])),
                                        routed=shlex.quote(str(b["routed"] or "")),
                                        project=shlex.quote(str(b["project"] or "")),
                                        out=shlex.quote(str(b["run"])))
            if self.dummy and leave > 0:
                cmd += f" --leave {int(leave)}"       # test only: preview an unfinished result
            # a second attempt starts again from the bare board
            for f in b["run"].glob("*.kicad_pcb"):
                if f.name != "v000.kicad_pcb":
                    f.unlink()
            keep = self.seen.get("v000.kicad_pcb")
            self.seen = {"v000.kicad_pcb": keep} if keep else {}
            del self.frames[1:], self.heavy[1:]
            self.attempt = getattr(self, "attempt", 0) + 1
            self.route_started, self.route_seconds = time.time(), None
            self.proc = subprocess.Popen(cmd, shell=True, cwd=str(HERE), stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
        print(f"[route] {cmd}", flush=True)
        return ""

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None

    # ----- watching the run folder -----
    def loop(self) -> None:
        while True:
            try:
                with self.lock:
                    b, epoch = self.board, self.epoch
                    if self.proc and self.proc.poll() is not None and self.route_seconds is None and self.route_started:
                        self.route_seconds = round(time.time() - self.route_started, 1)
                if b:
                    self.scan(b, epoch)
            except Exception as e:
                print(f"[watcher] {type(e).__name__}: {e}", flush=True)
            time.sleep(self.interval)

    def scan(self, b: dict, epoch: int) -> None:
        files = sorted(b["run"].glob("*.kicad_pcb"), key=lambda p: (p.stat().st_mtime, p.name))
        for f in files:
            st = f.stat()
            if self.seen.get(f.name) == st.st_mtime:
                continue
            time.sleep(0.1)                         # let a writer finish
            if not f.exists() or f.stat().st_size != st.st_size:
                continue
            cu = bp.copper(f)
            m = re.search(r"__([A-Za-z0-9_.\-]+)$", f.stem)
            with self.lock:
                if self.epoch != epoch:
                    return
                self.seen[f.name] = st.st_mtime
                if self.t0 is None:
                    self.t0 = st.st_mtime
                idx = len(self.frames)
                self.frames.append({"id": idx, "name": f.name, "agent": m.group(1) if m else "",
                                    "t": round(st.st_mtime - self.t0, 2), "open": None, "violations": None,
                                    "length_mm": cu["length_mm"], "segments": len(cu["segments"]),
                                    "vias": len(cu["vias"]), "checked": False})
                self.heavy.append({"id": idx, "segments": cu["segments"], "vias": cu["vias"],
                                   "open_pairs": None, "viol_pts": None})
            if self.cli:
                snap = self.tmp / f"e{epoch}_v{idx:05d}.kicad_pcb"
                shutil.copyfile(f, snap)
                self.pool.submit(self.check, epoch, idx, snap, b["project"])

    def check(self, epoch: int, idx: int, snap: Path, project: Path | None) -> None:
        try:
            r = rule_check(self.cli, snap, self.tmp, project)
        except Exception as e:
            print(f"[check v{idx}] {type(e).__name__}: {e}", flush=True)
            return
        finally:
            snap.unlink(missing_ok=True)
        if not r:
            return
        with self.lock:
            if self.epoch != epoch or idx >= len(self.frames):
                return
            self.frames[idx].update(open=r["open"], violations=r["violations"], checked=True)
            self.heavy[idx].update(open_pairs=r["open_pairs"], viol_pts=r["viol_pts"])

    def state(self) -> dict:
        with self.lock:
            routing = bool(self.proc and self.proc.poll() is None)
            return {"epoch": self.epoch, "board": self.board["name"] if self.board else None,
                    "frames": list(self.frames), "checker": bool(self.cli), "dummy": self.dummy,
                    "routing": routing, "route_started": self.route_started is not None,
                    "route_seconds": self.route_seconds,
                    "attempt": getattr(self, "attempt", 0),
                    "can_route": bool(self.board) and ("{routed}" not in self.route_cmd or bool(self.board["routed"]))}


def make_handler(s: Studio, title: str):
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

        def js(self, obj, code: int = 200) -> None:
            self.send(code, "application/json", json.dumps(obj).encode())

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                html = (HERE / "view3d.html").read_text().replace("__TITLE__", title)
                return self.send(200, MIME[".html"], html.encode())
            if path == "/state":
                return self.js(s.state())
            if path == "/boards":
                return self.js(s.list_boards())
            if path == "/scene":
                return self.js(s.scene or {})
            m = re.fullmatch(r"/frame/(\d+)", path)
            if m:
                with s.lock:
                    i = int(m.group(1))
                    if i < len(s.heavy):
                        return self.js(s.heavy[i])
            m = re.fullmatch(r"/vendor/([\w.\-]+)", path)
            if m and (HERE / "vendor" / m.group(1)).is_file():
                f = HERE / "vendor" / m.group(1)
                return self.send(200, MIME.get(f.suffix, "application/octet-stream"), f.read_bytes())
            self.send(404, "text/plain", b"not found")

        def do_POST(self):
            path = self.path.split("?")[0]
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            try:
                if path == "/load":
                    s.load_benchmark(json.loads(body)["board"])
                    return self.js({"ok": True})
                if path == "/upload":
                    s.load_upload(self.headers.get("X-Name", "board.kicad_pcb"), body.decode(errors="replace"))
                    return self.js({"ok": True})
                if path == "/route":
                    leave = int(json.loads(body or b"{}").get("leave", 0)) if body else 0
                    err = s.route(leave)
                    return self.js({"ok": not err, "error": err}, 200 if not err else 409)
            except Exception as e:
                return self.js({"ok": False, "error": f"{type(e).__name__}: {e}"}, 400)
            self.send(404, "text/plain", b"not found")

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boards-dir", help="folder of benchmark boards (PCBWorld's pcbench/exacad_sorted)")
    ap.add_argument("--route-cmd", help="producer command; see the placeholders above. Omit for the dummy run")
    ap.add_argument("--load", help="benchmark board name to load at start")
    ap.add_argument("--title", default="Board routing studio")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--interval", type=float, default=0.3)
    args = ap.parse_args()

    boards = Path(args.boards_dir) if args.boards_dir else None
    s = Studio(boards, args.route_cmd, args.interval)
    if not s.cli:
        print("kicad-cli not found (set KICAD_CLI): showing copper only, no rule check", flush=True)
    if s.dummy:
        print("no --route-cmd: Route runs the DUMMY producer (made-up agents, test data)", flush=True)
    if args.load:
        s.load_benchmark(args.load)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(s, args.title))
    print(f"http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        s.stop()


if __name__ == "__main__":
    main()
