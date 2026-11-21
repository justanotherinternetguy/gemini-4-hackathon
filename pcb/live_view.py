"""Live board viewer for the routing swarm.

Watches a folder for KiCad board files. Each time a new or changed ``*.kicad_pcb`` lands, it
  1. renders the board to SVG with kicad-cli,
  2. runs KiCad's own design-rule check on it (connections still open + error-level violations),
  3. measures copper length and via count from the file,
and serves a page that always shows the newest board next to those numbers.

The producer (the swarm, a baseline router, a replay script) only has to save board files into
the folder. Files are shown in modification-time order, so "save the current best board" is the
whole protocol.

    export KICAD_CLI=/path/to/kicad-cli          # any KiCad 9 kicad-cli
    python pcb/live_view.py --watch runs/live --project <board>.kicad_pro \
        --title "Gemini 4 swarm" --port 8765

``--project`` is the board's own .kicad_pro file. It carries the design rules, and without it
the rule count is checked against KiCad's defaults and comes out wrong.

The numbers here are a live check for the demo. The reported benchmark numbers come from
PCBWorld's own evaluation pipeline, which re-opens the saved boards separately.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SEG_RE = re.compile(
    r"\(segment\s*\(start\s+([-\d.]+)\s+([-\d.]+)\)\s*\(end\s+([-\d.]+)\s+([-\d.]+)\)", re.S)
VIA_RE = re.compile(r"\(via\b")
LAYERS = "F.Cu,B.Cu,In1.Cu,In2.Cu,Edge.Cuts,F.SilkS"


def kicad_cli() -> str:
    cli = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli")
    if not cli:
        raise SystemExit("kicad-cli not found: set KICAD_CLI or put kicad-cli on PATH")
    return cli


def copper_stats(board: Path) -> tuple[float, int, int]:
    """(total track length in mm, track segments, vias) read straight from the board file."""
    text = board.read_text(errors="replace")
    length = 0.0
    n = 0
    for x0, y0, x1, y1 in SEG_RE.findall(text):
        length += math.hypot(float(x1) - float(x0), float(y1) - float(y0))
        n += 1
    return length, n, len(VIA_RE.findall(text))


def render_svg(cli: str, board: Path, out: Path) -> bool:
    r = subprocess.run(
        [cli, "pcb", "export", "svg", "--layers", LAYERS, "--mode-single", "--page-size-mode", "2",
         "--exclude-drawing-sheet", "-o", str(out), str(board)],
        capture_output=True, text=True, timeout=120)
    return r.returncode == 0 and out.exists()


def rule_check(cli: str, board: Path, tmp: Path, project: Path | None = None) -> tuple[int | None, int | None]:
    """(open connections, error-level violations) from kicad-cli's DRC; None when it cannot run.

    KiCad reads a board's design rules from the ``.kicad_pro`` file next to it. A board saved
    alone is checked against KiCad's defaults, which reports violations the real rules do not.
    Pass ``project`` (the board's own .kicad_pro) and the check runs on a copy that has it.
    """
    report = tmp / (board.stem + ".drc.json")
    if project is not None and not board.with_suffix(".kicad_pro").exists():
        work = tmp / "drc_work"
        work.mkdir(exist_ok=True)
        copy = work / board.name
        shutil.copyfile(board, copy)
        shutil.copyfile(project, copy.with_suffix(".kicad_pro"))
        board = copy
    try:
        subprocess.run(
            [cli, "pcb", "drc", "--format", "json", "--severity-error", "--units", "mm",
             "-o", str(report), str(board)],
            capture_output=True, text=True, timeout=300)
        data = json.loads(report.read_text())
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None, None
    return len(data.get("unconnected_items", [])), len(data.get("violations", []))


class Watcher(threading.Thread):
    def __init__(self, watch: Path, cache: Path, interval: float, project: Path | None = None):
        super().__init__(daemon=True)
        self.watch, self.cache, self.interval, self.project = watch, cache, interval, project
        self.cli = kicad_cli()
        self.frames: list[dict] = []
        self.seen: dict[str, float] = {}
        self.lock = threading.Lock()
        self.t0: float | None = None

    def scan_once(self) -> None:
        files = sorted(self.watch.glob("*.kicad_pcb"), key=lambda p: (p.stat().st_mtime, p.name))
        for f in files:
            mtime = f.stat().st_mtime
            if self.seen.get(f.name) == mtime:
                continue
            # a file still being written: wait until its size is stable
            size = f.stat().st_size
            time.sleep(0.15)
            if not f.exists() or f.stat().st_size != size:
                continue
            self.seen[f.name] = mtime
            self.add_frame(f, mtime)

    def add_frame(self, board: Path, mtime: float) -> None:
        idx = len(self.frames)
        svg = self.cache / f"frame_{idx:05d}.svg"
        ok = render_svg(self.cli, board, svg)
        open_conns, violations = rule_check(self.cli, board, self.cache, self.project)
        length, segments, vias = copper_stats(board)
        if self.t0 is None:
            self.t0 = mtime
        frame = {
            "id": idx, "name": board.name, "t": round(mtime - self.t0, 2), "svg": ok,
            "open": open_conns, "violations": violations,
            "length_mm": round(length, 1), "segments": segments, "vias": vias,
        }
        with self.lock:
            self.frames.append(frame)
        print(f"[frame {idx}] {board.name}: open={open_conns} violations={violations} "
              f"copper={length:.0f}mm vias={vias}", flush=True)

    def run(self) -> None:
        while True:
            try:
                self.scan_once()
            except Exception as e:  # keep the demo alive whatever one file does
                print(f"[watcher] {type(e).__name__}: {e}", flush=True)
            time.sleep(self.interval)

    def state(self) -> dict:
        with self.lock:
            return {"frames": list(self.frames)}


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg:#070b16; --panel:#0e1526; --line:#1d2740; --ink:#e8edf7; --dim:#8b97b3;
          --good:#3ddc97; --bad:#ff5d6c; --warn:#ffc857; --front:#e0453f; --back:#4c8fe0; }
  * { box-sizing:border-box; }
  html,body { height:100%; margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif; }
  .wrap { display:grid; grid-template-columns:minmax(0,1fr) 380px; gap:18px; height:100vh; padding:18px; }
  .board { background:var(--panel); border:1px solid var(--line); border-radius:14px; position:relative;
    display:flex; align-items:center; justify-content:center; overflow:hidden; min-height:0; }
  .board img { max-width:96%; max-height:96%; transition:opacity .18s ease; }
  .tag { position:absolute; left:16px; top:14px; color:var(--dim); font-size:13px; letter-spacing:.04em; }
  .legend { position:absolute; left:16px; bottom:14px; color:var(--dim); font-size:13px; display:flex; gap:16px; }
  .dot { display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; vertical-align:-1px; }
  .side { display:flex; flex-direction:column; gap:14px; min-height:0; }
  h1 { font-size:20px; margin:2px 0 0; font-weight:650; }
  .sub { color:var(--dim); font-size:13px; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:14px 16px; }
  .label { color:var(--dim); font-size:12px; text-transform:uppercase; letter-spacing:.08em; }
  .big { font-size:56px; font-weight:700; line-height:1.05; font-variant-numeric:tabular-nums; }
  .big small { font-size:18px; color:var(--dim); font-weight:500; margin-left:6px; }
  .row { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  .mid { font-size:30px; font-weight:650; font-variant-numeric:tabular-nums; }
  .bar { height:8px; background:#17203a; border-radius:99px; overflow:hidden; margin-top:10px; }
  .bar > i { display:block; height:100%; width:0; background:var(--good); transition:width .3s ease; }
  .status { font-weight:700; font-size:18px; padding:10px 14px; border-radius:10px; text-align:center;
    border:1px solid var(--line); color:var(--dim); }
  .status.pass { color:#04130c; background:var(--good); border-color:var(--good); }
  .status.fail { color:var(--bad); border-color:var(--bad); }
  svg.chart { width:100%; height:90px; display:block; }
  .ctl { display:flex; gap:10px; align-items:center; }
  button { background:#1a2543; color:var(--ink); border:1px solid var(--line); border-radius:9px;
    padding:8px 14px; font:inherit; cursor:pointer; }
  button:hover { background:#22305a; }
  input[type=range] { flex:1; accent-color:var(--good); }
  .good { color:var(--good); } .bad { color:var(--bad); }
  @media (max-width:900px) { .wrap { grid-template-columns:1fr; height:auto; } .board { height:62vh; } }
</style></head>
<body><div class="wrap">
  <div class="board">
    <div class="tag" id="tag">waiting for the first board…</div>
    <img id="img" alt="current board" style="opacity:0">
    <div class="legend"><span><i class="dot" style="background:var(--front)"></i>front copper</span>
      <span><i class="dot" style="background:var(--back)"></i>back copper</span></div>
  </div>
  <div class="side">
    <div><h1>__TITLE__</h1><div class="sub">Checked live by KiCad's design-rule checker</div></div>
    <div class="status" id="status">no board yet</div>
    <div class="card">
      <div class="label">Connections still open</div>
      <div class="big" id="open">–<small id="total"></small></div>
      <div class="bar"><i id="bar"></i></div>
    </div>
    <div class="row">
      <div class="card"><div class="label">Rule violations</div><div class="mid" id="viol">–</div></div>
      <div class="card"><div class="label">Layer changes</div><div class="mid" id="vias">–</div></div>
    </div>
    <div class="row">
      <div class="card"><div class="label">Copper length</div><div class="mid" id="len">–</div></div>
      <div class="card"><div class="label">Board versions</div><div class="mid" id="count">0</div></div>
    </div>
    <div class="card"><div class="label">Open connections over time</div>
      <svg class="chart" id="chart" viewBox="0 0 340 90" preserveAspectRatio="none"></svg></div>
    <div class="ctl"><button id="play">Replay</button><input type="range" id="scrub" min="0" max="0" value="0">
      <button id="live">Live</button></div>
  </div>
</div>
<script>
let frames = [], shown = -1, follow = true, timer = null;
const $ = id => document.getElementById(id);
function show(i) {
  if (i < 0 || i >= frames.length) return;
  shown = i; const f = frames[i], total = frames.length ? frames[0].open : null;
  const pre = new Image();
  pre.onload = () => { $('img').src = pre.src; $('img').style.opacity = 1; };
  pre.src = '/frame/' + f.id + '.svg?n=' + f.name;
  $('tag').textContent = f.name + '  ·  version ' + (i + 1) + ' of ' + frames.length;
  $('open').firstChild.nodeValue = f.open ?? '–';
  $('open').className = 'big ' + (f.open === 0 ? 'good' : '');
  $('total').textContent = total != null ? 'of ' + total : '';
  $('bar').style.width = (total ? Math.max(0, 100 * (1 - f.open / total)) : 0) + '%';
  $('viol').textContent = f.violations ?? '–';
  $('viol').className = 'mid ' + (f.violations === 0 ? 'good' : 'bad');
  $('vias').textContent = f.vias; $('len').textContent = Math.round(f.length_mm) + ' mm';
  $('count').textContent = frames.length; $('scrub').value = i;
  const st = $('status');
  if (f.open === 0 && f.violations === 0) { st.textContent = 'CLEAN PASS: fully connected, zero violations'; st.className = 'status pass'; }
  else if (f.open === 0) { st.textContent = 'connected, ' + f.violations + ' violations to fix'; st.className = 'status fail'; }
  else { st.textContent = 'routing… ' + f.open + ' connections to go'; st.className = 'status'; }
  chart();
}
function chart() {
  const c = $('chart'), W = 340, H = 90, n = frames.length; if (!n) { c.innerHTML = ''; return; }
  const max = Math.max(1, ...frames.map(f => f.open ?? 0));
  const x = i => n === 1 ? 0 : 6 + (W - 12) * i / (n - 1), y = v => 8 + (H - 16) * (1 - (v ?? 0) / max);
  let d = ''; frames.forEach((f, i) => { d += (i ? ' L' : 'M') + x(i).toFixed(1) + ' ' + y(f.open).toFixed(1); });
  const cur = frames[Math.max(0, shown)];
  c.innerHTML = '<path d="' + d + '" fill="none" stroke="#3ddc97" stroke-width="2.2" vector-effect="non-scaling-stroke"/>' +
    '<circle cx="' + x(Math.max(0, shown)) + '" cy="' + y(cur.open) + '" r="4" fill="#e8edf7"/>';
}
async function poll() {
  try {
    const s = await (await fetch('/state')).json();
    const grew = s.frames.length !== frames.length; frames = s.frames;
    $('scrub').max = Math.max(0, frames.length - 1);
    if (frames.length && (shown < 0 || (follow && grew))) show(frames.length - 1);
    else if (grew) { $('count').textContent = frames.length; chart(); }
  } catch (e) {}
}
$('scrub').oninput = e => { follow = false; stop(); show(+e.target.value); };
$('live').onclick = () => { follow = true; stop(); show(frames.length - 1); };
function stop() { if (timer) { clearInterval(timer); timer = null; $('play').textContent = 'Replay'; } }
$('play').onclick = () => {
  if (timer) return stop();
  follow = false; let i = 0; show(0); $('play').textContent = 'Pause';
  const step = Math.max(120, Math.min(900, 12000 / Math.max(1, frames.length)));
  timer = setInterval(() => { i++; if (i >= frames.length) return stop(); show(i); }, step);
};
setInterval(poll, 500); poll();
</script></body></html>
"""


def make_handler(watcher: Watcher, title: str):
    page = PAGE.replace("__TITLE__", title).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def send(self, code: int, ctype: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                return self.send(200, "text/html; charset=utf-8", page)
            if path == "/state":
                return self.send(200, "application/json", json.dumps(watcher.state()).encode())
            m = re.fullmatch(r"/frame/(\d+)\.svg", path)
            if m:
                f = watcher.cache / f"frame_{int(m.group(1)):05d}.svg"
                if f.exists():
                    return self.send(200, "image/svg+xml", f.read_bytes())
            self.send(404, "text/plain", b"not found")

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch", required=True, help="folder the producer saves .kicad_pcb files into")
    ap.add_argument("--title", default="Routing swarm")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--interval", type=float, default=0.4, help="seconds between folder scans")
    ap.add_argument("--project", help="the board's .kicad_pro (its design rules); needed when the "
                                      "producer saves bare .kicad_pcb files without one beside them")
    args = ap.parse_args()

    watch = Path(args.watch)
    watch.mkdir(parents=True, exist_ok=True)
    cache = Path(tempfile.mkdtemp(prefix="pcb_live_"))
    project = Path(args.project) if args.project else None
    if project is not None and not project.is_file():
        raise SystemExit(f"--project {project} is not a file")
    watcher = Watcher(watch, cache, args.interval, project)
    watcher.start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(watcher, args.title))
    print(f"watching {watch.resolve()}  ->  http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
