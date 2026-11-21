"""3D routing studio: import a board, watch it assemble, press Route, watch the swarm lay copper.

Flow in the page:
  1. Import: drop a ``.kicad_pcb`` file on the page, or pick one of the benchmark boards.
  2. The board is built piece by piece from the file (outline, then each component and its pads),
     then the parts turn to glass and every connection still to make is shown as a red line.
  3. Route: the solver command is started. It saves new versions of the board into a folder;
     each version's new copper is animated, led by the agent that produced it.
  4. Verdict: KiCad's design-rule checker decides. Fully connected with zero violations is a
     clean pass; otherwise the page lists exactly which connections are left for the engineer.

The solver side of this is specified in pcb/CONTRACT.md. In short: save board versions into the
watched folder, optionally append lines to events.jsonl there, and exit when finished.

    export KICAD_CLI=/path/to/kicad-cli
    # live, with a solver
    python pcb/view3d.py --boards-dir $PCBWORLD_DATA_ROOT/pcbench/exacad_sorted \
        --route-cmd 'python -m pcb.repair.swarm --boards {board} --start START --run-id {run_id}' \
        --watch-dir 'runs/pcb/{run_id}/live/{board}' --cwd ..
    # replay of a recorded run (label it as one)
    python pcb/view3d.py --boards-dir ... --title 'Board router (replay of a recorded run)' \
        --route-cmd 'python replay_run.py --from RECORDING --out {out}'

Placeholders in --route-cmd and --watch-dir: {board} board name, {unrouted} starting board file,
{project} its .kicad_pro ('' if none), {out} a fresh folder owned by the viewer, {run_id} a fresh
id per run, {routed} a finished version of the same board when one is known.

Without --route-cmd the built-in DUMMY solver runs: made-up agents replay a finished routing.
The page marks that clearly as test data.

Every finished run is copied to --record-dir (default runs/recordings) with a manifest of when
each version arrived, so it can be replayed later with replay_run.py.

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
             "--routed {routed} --out {out} --agents 5 --burst 5 --delay 2.2 --no-start --events")


def kicad_cli() -> str | None:
    return os.environ.get("KICAD_CLI") or shutil.which("kicad-cli")


def agent_of(stem: str) -> str:
    """Who gets credit for a version, from its file name (see CONTRACT.md)."""
    m = re.search(r"__([A-Za-z0-9_.\-]+)$", stem)          # 0007__w3
    if m:
        return m.group(1)
    m = re.fullmatch(r"\d+_([A-Za-z][A-Za-z0-9_.\-]*)", stem)   # 1795290000_r1_2
    return m.group(1) if m else ""


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
    """One loaded board, the folder being watched, the versions seen so far and the solver process."""

    def __init__(self, boards_dir: Path | None, route_cmd: str | None, watch_tpl: str, cwd: Path,
                 record_dir: Path | None, interval: float):
        self.boards_dir, self.interval = boards_dir, interval
        self.route_cmd = route_cmd or DUMMY_CMD
        self.dummy = route_cmd is None
        self.watch_tpl, self.cwd, self.record_dir = watch_tpl, cwd, record_dir
        self.cli = kicad_cli()
        self.tmp = Path(tempfile.mkdtemp(prefix="pcb3d_"))
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.lock = threading.RLock()
        self.epoch = 0                    # bumps when a board is loaded
        self.attempt = 0                  # bumps on every Route
        self.board: dict | None = None
        self.scene: dict | None = None
        self.frames: list[dict] = []
        self.heavy: list[dict] = []
        self.events: list[dict] = []
        self.result: dict | None = None
        self.seen: dict[str, float] = {}
        self.events_pos = 0
        self.watch: Path | None = None
        self.run_id = ""
        self.proc: subprocess.Popen | None = None
        self.route_started: float | None = None
        self.route_seconds: float | None = None
        self.solver_exit: int | None = None
        self.recorded: str | None = None
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

    # ----- page extras: sample cards, thumbnails, download, stop -----
    def featured(self) -> list[dict]:
        """A short, picture-card list for the import screen: boards the classic router failed first."""
        if getattr(self, "_featured", None) is not None:
            return self._featured
        boards = self.list_boards()
        pick = [b for b in boards if b["router_failed"]][:5] + [b for b in boards[20:99:9] if not b["router_failed"]][:4]
        out = []
        for b in pick:
            try:
                sm = bp.scene(self.boards_dir / b["name"] / "processed_v9_guide_v3_unrouted.kicad_pcb")["summary"]
            except Exception:
                continue
            out.append({**b, "parts": sm["components"], "signals": sm["nets"],
                        "size": f'{sm["width_mm"]:g} × {sm["height_mm"]:g} mm'})
        self._featured = out
        return out

    def thumb(self, name: str) -> bytes | None:
        """Flat picture of a benchmark board with no copper, for its card."""
        if not self.cli or not self.boards_dir or "/" in name or ".." in name:
            return None
        src = self.boards_dir / name / "processed_v9_guide_v3_unrouted.kicad_pcb"
        if not src.is_file():
            return None
        out = self.tmp / f"thumb_{name}.svg"
        if not out.is_file():
            subprocess.run([self.cli, "pcb", "export", "svg", "--layers", "F.Cu,B.Cu,Edge.Cuts,F.SilkS",
                            "--mode-single", "--page-size-mode", "2", "--exclude-drawing-sheet",
                            "-o", str(out), str(src)], capture_output=True, text=True, timeout=120)
        return out.read_bytes() if out.is_file() else None

    def latest_board(self) -> tuple[str, bytes] | None:
        """The newest version of the loaded board, for the Download button."""
        with self.lock:
            b, last = self.board, getattr(self, "last_path", None)
        if not b or not last or not Path(last).is_file():
            return None
        return re.sub(r"[^A-Za-z0-9_.-]", "_", b["name"]) + "_routed.kicad_pcb", Path(last).read_bytes()

    def stop_route(self) -> None:
        """Stop the solver but keep what it saved; the verdict then reports where it got to."""
        with self.lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()

    def load_benchmark(self, name: str) -> None:
        d = (self.boards_dir / name) if self.boards_dir else None
        if not d or "/" in name or not (d / "processed_v9_guide_v3_unrouted.kicad_pcb").is_file():
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
        with self.lock:
            self.epoch += 1
            self.attempt = 0
            self.board = {"name": name, "unrouted": unrouted, "routed": routed, "project": project}
            self.scene = scene
            self.frames, self.heavy, self.events, self.result = [], [], [], None
            self.seen, self.events_pos, self.watch = {}, 0, None
            self.route_started = self.route_seconds = self.solver_exit = self.recorded = None
            # version 0 is always the bare board, straight from the imported file
            self._add_frame("start", "", unrouted, 0.0, self.epoch, self.attempt)
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
            self.attempt += 1
            self.run_id = time.strftime("demo_%H%M%S") + f"_{self.attempt}"
            out = Path(tempfile.mkdtemp(prefix="run_", dir=self.tmp))
            fill = {"board": b["name"], "run_id": self.run_id, "out": str(out), "unrouted": str(b["unrouted"]),
                    "routed": str(b["routed"] or ""), "project": str(b["project"] or "")}
            watch = Path(self.watch_tpl.format(**fill))
            self.watch = watch if watch.is_absolute() else self.cwd / watch
            self.watch.mkdir(parents=True, exist_ok=True)
            cmd = self.route_cmd.format(**{k: shlex.quote(v) for k, v in fill.items()})
            if self.dummy and leave > 0:
                cmd += f" --leave {int(leave)}"       # test only: preview an unfinished result
            del self.frames[1:], self.heavy[1:]
            self.events, self.result, self.seen, self.events_pos = [], None, {}, 0
            self.route_started, self.route_seconds, self.solver_exit, self.recorded = time.time(), None, None, None
            self.proc = subprocess.Popen(cmd, shell=True, cwd=str(self.cwd), stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
        print(f"[route] {cmd}\n[watch] {self.watch}", flush=True)
        return ""

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None

    # ----- watching -----
    def loop(self) -> None:
        while True:
            try:
                with self.lock:
                    b, epoch, attempt, watch = self.board, self.epoch, self.attempt, self.watch
                    finished = bool(self.proc and self.proc.poll() is not None and self.route_seconds is None
                                    and self.route_started)
                if b and watch:
                    self.scan(b, watch, epoch, attempt)
                if finished:
                    time.sleep(0.4)
                    self.scan(b, watch, epoch, attempt)         # pick up anything saved at the very end
                    with self.lock:
                        self.solver_exit = self.proc.returncode
                        self.route_seconds = round(time.time() - self.route_started, 1)
                    self.record(b, watch)
            except Exception as e:
                print(f"[watcher] {type(e).__name__}: {e}", flush=True)
            time.sleep(self.interval)

    def scan(self, b: dict, watch: Path, epoch: int, attempt: int) -> None:
        files = sorted(watch.glob("*.kicad_pcb"), key=lambda p: (p.stat().st_mtime, p.name))
        for f in files:
            st = f.stat()
            if self.seen.get(f.name) == st.st_mtime:
                continue
            time.sleep(0.1)                         # let a writer finish
            if not f.exists() or f.stat().st_size != st.st_size:
                continue
            with self.lock:
                if (self.epoch, self.attempt) != (epoch, attempt):
                    return
                self.seen[f.name] = st.st_mtime
                self._add_frame(f.name, agent_of(f.stem), f, round(time.time() - (self.route_started or time.time()), 2),
                                epoch, attempt)
        ev = watch / "events.jsonl"
        if ev.is_file() and ev.stat().st_size > self.events_pos:
            with open(ev, "rb") as fh:
                fh.seek(self.events_pos)
                chunk = fh.read()
            end = chunk.rfind(b"\n") + 1               # only whole lines
            new = []
            for line in chunk[:end].decode(errors="replace").splitlines():
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        new.append(obj)
                except json.JSONDecodeError:
                    pass
            with self.lock:
                if (self.epoch, self.attempt) == (epoch, attempt):
                    self.events_pos += end
                    self.events += new
        res = watch / "result.json"
        if res.is_file() and self.result is None:
            try:
                r = json.loads(res.read_text())
                with self.lock:
                    self.result = r if isinstance(r, dict) else None
            except json.JSONDecodeError:
                pass

    def _add_frame(self, name: str, agent: str, path: Path, t: float, epoch: int, attempt: int) -> None:
        cu = bp.copper(path)
        self.last_path = path
        idx = len(self.frames)
        self.frames.append({"id": idx, "name": name, "agent": agent, "t": t, "open": None, "violations": None,
                            "length_mm": cu["length_mm"], "segments": len(cu["segments"]),
                            "vias": len(cu["vias"]), "checked": False})
        self.heavy.append({"id": idx, "segments": cu["segments"], "vias": cu["vias"],
                           "open_pairs": None, "viol_pts": None})
        if self.cli:
            snap = self.tmp / f"e{epoch}_a{attempt}_v{idx:05d}.kicad_pcb"
            shutil.copyfile(path, snap)
            self.pool.submit(self.check, epoch, attempt, idx, snap, self.board["project"])

    def check(self, epoch: int, attempt: int, idx: int, snap: Path, project: Path | None) -> None:
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
            # version 0 survives a re-route, so it only has to match the board, not the attempt
            if self.epoch != epoch or idx >= len(self.frames) or (idx > 0 and self.attempt != attempt):
                return
            self.frames[idx].update(open=r["open"], violations=r["violations"], checked=True)
            self.heavy[idx].update(open_pairs=r["open_pairs"], viol_pts=r["viol_pts"])

    def record(self, b: dict, watch: Path) -> None:
        """Copy the finished run somewhere durable, with arrival times, so it can be replayed."""
        if not self.record_dir:
            return
        dst = self.record_dir / f"{b['name']}__{self.run_id}"
        dst.mkdir(parents=True, exist_ok=True)
        with self.lock:
            versions = [{"file": f["name"], "t": f["t"], "agent": f["agent"]} for f in self.frames[1:]]
            meta = {"board": b["name"], "run_id": self.run_id, "seconds": self.route_seconds,
                    "solver_exit": self.solver_exit, "test_data": self.dummy, "versions": versions}
        for v in versions:
            if (watch / v["file"]).is_file():
                shutil.copyfile(watch / v["file"], dst / v["file"])
        for extra in ("events.jsonl", "result.json"):
            if (watch / extra).is_file():
                shutil.copyfile(watch / extra, dst / extra)
        (dst / "manifest.json").write_text(json.dumps(meta, indent=1))
        with self.lock:
            self.recorded = str(dst)
        print(f"[record] {dst}", flush=True)

    def state(self) -> dict:
        with self.lock:
            routing = bool(self.proc and self.proc.poll() is None)
            can = bool(self.board) and ("{routed}" not in self.route_cmd or bool(self.board["routed"]))
            return {"epoch": self.epoch, "attempt": self.attempt, "board": self.board["name"] if self.board else None,
                    "frames": list(self.frames), "events": list(self.events), "result": self.result,
                    "checker": bool(self.cli), "dummy": self.dummy, "routing": routing,
                    "route_started": self.route_started is not None, "route_seconds": self.route_seconds,
                    "solver_exit": self.solver_exit, "recorded": self.recorded, "can_route": can,
                    "has_project": bool(self.board and self.board["project"])}


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
            if path == "/featured":
                return self.js(s.featured())
            if path == "/download":
                got = s.latest_board()
                if got:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", f'attachment; filename="{got[0]}"')
                    self.send_header("Content-Length", str(len(got[1])))
                    self.end_headers()
                    self.wfile.write(got[1])
                    return
            m = re.fullmatch(r"/thumb/([^/]+)\.svg", path)
            if m:
                from urllib.parse import unquote
                data = s.thumb(unquote(m.group(1)))
                if data:
                    return self.send(200, "image/svg+xml", data)
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
                if path == "/explain":
                    # Gemini's read on one unmade connection; see gemini_explain.py
                    import gemini_explain
                    out = gemini_explain.explain(json.loads(body or b"{}"))
                    out["retry_available"] = False      # there is no POST /retry: do not offer a fix button
                    return self.js(out)
                if path == "/stop":
                    s.stop_route()
                    return self.js({"ok": True})
                if path == "/route":
                    leave = int(json.loads(body).get("leave", 0)) if body else 0
                    err = s.route(leave)
                    return self.js({"ok": not err, "error": err}, 200 if not err else 409)
            except Exception as e:
                return self.js({"ok": False, "error": f"{type(e).__name__}: {e}"}, 400)
            self.send(404, "text/plain", b"not found")

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boards-dir", help="folder of benchmark boards (PCBWorld's pcbench/exacad_sorted)")
    ap.add_argument("--route-cmd", help="solver command; see the placeholders above. Omit for the dummy run")
    ap.add_argument("--watch-dir", default="{out}", help="folder the solver saves versions into (placeholders "
                                                         "allowed); relative paths are relative to --cwd")
    ap.add_argument("--cwd", default=str(HERE), help="working directory for the solver command")
    ap.add_argument("--record-dir", default=str(HERE.parent / "runs" / "recordings"),
                    help="where finished runs are copied for replay ('' to turn off)")
    ap.add_argument("--load", help="benchmark board name to load at start")
    ap.add_argument("--title", default="Board routing studio")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--interval", type=float, default=0.3)
    args = ap.parse_args()

    s = Studio(Path(args.boards_dir) if args.boards_dir else None, args.route_cmd, args.watch_dir,
               Path(args.cwd).resolve(), Path(args.record_dir) if args.record_dir else None, args.interval)
    if not s.cli:
        print("kicad-cli not found (set KICAD_CLI): showing copper only, no rule check", flush=True)
    if s.dummy:
        print("no --route-cmd: Route runs the DUMMY solver (made-up agents, test data)", flush=True)
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
