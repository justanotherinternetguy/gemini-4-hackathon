"""Let Gemini 4 design the viewer: generate several complete alternative pages, then compare them.

Each variant is one self-contained HTML file written by a Gemini 4 model from the same data
contract and a different art direction. Variants talk to the running studio server
(``view3d.py``) through a small proxy, so every one of them shows the real board, the real copper
and the real rule-check counts. Pick one, iterate on it with a plain-English note, repeat.

    # 1. the studio must be running (it supplies the data):
    #      python view3d.py --boards-dir ... --port 8766
    # 2. generate four designs, one per built-in art direction:
    python gemini_variants.py generate
    # 3. look at them:
    python gemini_variants.py serve            # then open http://127.0.0.1:8770
    # iterate on one you like:
    python gemini_variants.py generate --base variants/<file>.html --brief "bigger numbers, darker canvas" --n 2
    # your own direction for a fresh batch:
    python gemini_variants.py generate --brief "looks like an oscilloscope" --n 3

Needs GEMINI_API_KEY (read from the environment or the repo's .env) and the google-genai package.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "variants"

DIRECTIONS = {
    "workbench": "Dark professional EDA workbench. Near-black canvas with a faint grid, copper that glows, "
                 "docked panels with hairline dividers, monospace numerals, one restrained accent colour. "
                 "Should feel like KiCad or Altium rebuilt by a modern product team.",
    "studio": "Bright product studio. Soft daylight, matte green board with crisp contact shadows, like a "
              "hardware company's product page. Clean docked panels, generous type, calm and confident.",
    "blueprint": "Technical blueprint. Deep blue canvas, parts drawn as precise outlined solids, copper as "
                 "bright solid lines, thin dimension lines and callouts, drafting-style labels.",
    "stage": "Presentation mode for a projector. Low cinematic camera that slowly orbits, dramatic key "
             "light, very large numbers readable from the back of a room, almost no chrome.",
}

SPEC = """You are an exceptional front-end engineer and 3D designer. Write ONE complete, self-contained
HTML file: a web app in which an engineer imports a circuit board and watches a swarm of AI agents
route its copper in 3D.

Return only the HTML document, starting with <!doctype html>. No explanation, no markdown fence.

TECHNICAL RULES
- Use three.js r160 as an ES module. Include exactly this import map and nothing from a CDN:
  <script type="importmap">{ "imports": { "three": "/vendor/three.module.min.js" } }</script>
  then: import * as THREE from 'three'; import { OrbitControls } from '/vendor/OrbitControls.js';
- No other libraries, no external fonts, images or network requests besides the endpoints below.
- All data comes from the same origin over these endpoints. Poll GET /state about 3 times a second.
- Board coordinates are millimetres, x right, y DOWN. Map board (x, y) to scene (x - cx, 0, y - cy)
  with y-up for height, where (cx, cy) is the centre of scene.bbox. Rotations are degrees,
  counter-clockwise seen from above: use mesh.rotation.y = rot * Math.PI / 180.
- The board is 1.6 mm thick. "front" copper and parts sit on top, "back" ones underneath. Many
  boards have most copper on the back, so the board must be see-through enough to show it.
- Must run at 60 fps on a laptop with boards of about 300 track segments and 150 pads.

ENDPOINTS (live samples from the running server are below)
- GET  /featured        sample boards for the import screen (may be empty)
- GET  /boards          all benchmark boards: [{name, router_failed}]
- POST /load            JSON {"board": name}: load a benchmark board
- POST /upload          raw .kicad_pcb text as the body, header X-Name: file name
- GET  /scene           the loaded board: bbox, components, pads, nets, summary
- GET  /state           epoch (changes when a new board is loaded), board, frames, routing, result...
- GET  /frame/<id>      one version of the board: segments, vias, open_pairs, viol_pts
- POST /route           JSON {} : start the solver.   POST /stop : stop it.
- GET  /download        the latest routed board file

WHAT THE APP MUST DO, IN ORDER
1. Import. A drop target for a .kicad_pcb file (POST /upload) and a way to pick a sample board
   (POST /load). Boards with router_failed true are ones the classic autorouter could not finish:
   mark them.
2. Assemble. When /state.epoch changes and a board is loaded, fetch /scene and build the board
   piece by piece: the bare board appears, then each component lands one at a time with its pads,
   biggest first, briefly labelled with its ref and value. Show what the file contains (size,
   layers, parts, pads, signals).
3. Ready. Parts that would hide copper become see-through. Every connection still to make is drawn
   as a red line arcing over the board between the two pad positions in frame 0's open_pairs.
   Show the count and one clear primary button: Route.
4. Routing. frames[i] are successive versions of the same board. For each new frame fetch
   /frame/<i> and diff its segments and vias against what is already drawn, using the "k" key.
   New copper GROWS along its path, led by a marker for the agent named in frames[i].agent, in
   that agent's colour, then settles to copper colour (front: warm copper, back: blue). Several
   agents must be visibly working at the same time. Copper that disappeared flashes red and fades.
   Keep the red open-connection lines in sync with the newest checked frame's open_pairs. Show
   connections left, rule violations, copper length, and one row per agent with what it is doing.
5. Verdict. When /state.routing is false after a run and the last frame is checked: if open == 0
   and violations == 0 show a clear pass with a Download button (/download); otherwise show
   exactly which connections are left, from open_pairs descriptions such as
   "PTH pad 8 [/MOSI] of P5", and offer Try again.

HONESTY RULES (these matter)
- Every number shown must come from /state or /frame. Never invent, estimate or animate a count.
- frames[i].open and .violations are null until checked is true: show a neutral placeholder.
- If /state.dummy is true, show a clearly visible "Test data: made-up agents" label at all times.
- Do not show a failure reason unless the data gives one.

QUALITY BAR
This is the centrepiece of a live demo. It should look like a shipped, opinionated product, not a
prototype: a real layout with a top bar and docked panels, a deliberate type scale, restrained
colour, smooth easing on every transition, sensible empty and loading states. Parts are simple
solids sized from each component's w, h and height, coloured by its kind.
"""


def load_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        env = HERE.parent / ".env"
        if env.is_file():
            for line in env.read_text().splitlines():
                m = re.match(r"\s*(?:export\s+)?(?:GEMINI|GOOGLE)_API_KEY\s*=\s*['\"]?([^'\"\s]+)", line)
                if m:
                    key = m.group(1)
    if not key:
        raise SystemExit("no GEMINI_API_KEY in the environment or in the repo's .env")
    return key


def get(studio: str, path: str):
    with urllib.request.urlopen(studio + path, timeout=5) as r:
        return json.loads(r.read())


def samples(studio: str) -> str:
    """Trimmed live responses, so the model writes against the API as it is right now."""
    out = []
    try:
        scene = get(studio, "/scene")
        if scene:
            scene = dict(scene, components=scene["components"][:4], pads=scene["pads"][:4],
                         nets=dict(list(scene["nets"].items())[:6]))
            out.append("GET /scene (lists trimmed)\n" + json.dumps(scene, indent=1))
        state = get(studio, "/state")
        frames = state.get("frames", [])
        state = dict(state, frames=frames[:2] + frames[-1:], events=(state.get("events") or [])[:3])
        out.append("GET /state (frames and events trimmed)\n" + json.dumps(state, indent=1))
        if len(frames) > 1:
            fr = get(studio, f"/frame/{min(len(frames) - 1, 3)}")
            fr = dict(fr, segments=fr["segments"][:3], vias=fr["vias"][:2],
                      open_pairs=(fr.get("open_pairs") or [])[:3], viol_pts=(fr.get("viol_pts") or [])[:2])
            out.append("GET /frame/3 (lists trimmed)\n" + json.dumps(fr, indent=1))
        out.append("GET /featured (first 2)\n" + json.dumps(get(studio, "/featured")[:2], indent=1))
    except Exception as e:
        out.append(f"(could not sample the studio at {studio}: {type(e).__name__}: {e}. "
                   "Load a board and press Route there first for the best samples.)")
    return "\n\n".join(out)


def extract_html(text: str) -> str:
    m = re.search(r"<!doctype html.*</html>", text, re.S | re.I)
    return (m.group(0) if m else text).strip() + "\n"


def generate(args) -> None:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=load_key())
    live = samples(args.studio)
    base = Path(args.base).read_text() if args.base else None
    if base:
        jobs = [(Path(args.base).stem.split("__")[-1] + f"-r{i + 1}", args.brief or "Improve it.") for i in range(args.n)]
    elif args.brief:
        jobs = [(re.sub(r"[^a-z0-9]+", "-", args.brief.lower())[:24].strip("-") + f"-{i + 1}", args.brief)
                for i in range(args.n)]
    else:
        jobs = list(DIRECTIONS.items())[:args.n]
    OUT.mkdir(exist_ok=True)
    stamp = time.strftime("%H%M%S")

    def one(job):
        slug, brief = job
        if base:
            prompt = (SPEC + "\n\nLIVE SAMPLES\n" + live + "\n\nBelow is an existing version of the page. "
                      "Keep what works, keep every behaviour and honesty rule above, and revise it as "
                      f"follows:\n{brief}\n\nEXISTING PAGE\n{base}")
        else:
            prompt = SPEC + "\n\nLIVE SAMPLES\n" + live + f"\n\nART DIRECTION FOR THIS VERSION\n{brief}"
        t = time.time()
        try:
            r = client.models.generate_content(
                model=args.model, contents=prompt,
                config=types.GenerateContentConfig(max_output_tokens=60000))
            html = extract_html(r.text or "")
        except Exception as e:
            return f"{slug}: FAILED {type(e).__name__}: {str(e)[:200]}"
        if "<html" not in html.lower() or "three" not in html:
            return f"{slug}: FAILED, the model did not return a usable page ({len(html)} chars)"
        f = OUT / f"{stamp}__{slug}.html"
        f.write_text(html)
        return f"{slug}: {f.name}  ({len(html) // 1000} kB, {time.time() - t:.0f} s, {args.model})"

    print(f"asking {args.model} for {len(jobs)} design(s)...", flush=True)
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for line in pool.map(one, jobs):
            print("  " + line, flush=True)
    print(f"look at them:  python {Path(__file__).name} serve   ->  http://127.0.0.1:8770")


GALLERY = """<!doctype html><meta charset="utf-8"><title>Gemini viewer designs</title>
<style>body{font:15px/1.5 -apple-system,system-ui,sans-serif;margin:32px;color:#1c2420;background:#f4f5f2}
h1{font-size:20px;margin:0 0 4px}p{color:#66736b;margin:0 0 18px}
.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:16px}
.c{background:#fff;border:1px solid #dfe3dc;border-radius:10px;overflow:hidden}
.c iframe{width:100%;height:300px;border:0;display:block;background:#eee}
.c div{display:flex;justify-content:space-between;align-items:center;padding:9px 12px;font-size:13.5px}
a{color:#1f6feb;text-decoration:none}</style>
<h1>Viewer designs written by Gemini 4</h1>
<p>Each one is a live page on the same board data. Load a board in any of them. Newest first.</p>
<div class="g">__CARDS__</div>"""


def serve(args) -> None:
    studio = args.studio

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def out(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def proxy(self, method):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if method == "POST" else None
            req = urllib.request.Request(studio + self.path, data=body, method=method)
            for h in ("Content-Type", "X-Name"):
                if self.headers.get(h):
                    req.add_header(h, self.headers[h])
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return self.out(r.status, r.headers.get("Content-Type", "application/octet-stream"), r.read())
            except urllib.error.HTTPError as e:
                return self.out(e.code, e.headers.get("Content-Type", "text/plain"), e.read())
            except Exception as e:
                return self.out(502, "text/plain", f"studio not reachable at {studio}: {e}".encode())

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                files = sorted(OUT.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True) if OUT.is_dir() else []
                cards = "".join(
                    f'<div class="c"><iframe src="/v/{f.name}" loading="lazy"></iframe>'
                    f'<div><b>{f.stem.split("__")[-1]}</b><a href="/v/{f.name}" target="_blank">open full size</a></div></div>'
                    for f in files) or "<p>No designs yet. Run: python gemini_variants.py generate</p>"
                return self.out(200, "text/html; charset=utf-8", GALLERY.replace("__CARDS__", cards).encode())
            m = re.fullmatch(r"/v/([\w.\-]+\.html)", path)
            if m and (OUT / m.group(1)).is_file():
                return self.out(200, "text/html; charset=utf-8", (OUT / m.group(1)).read_bytes())
            self.proxy("GET")

        def do_POST(self):
            self.proxy("POST")

    print(f"gallery: http://127.0.0.1:{args.port}   (data from the studio at {studio})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), H).serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--studio", default="http://127.0.0.1:8766", help="the running view3d.py server")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="ask Gemini for new designs")
    g.add_argument("--n", type=int, default=4, help="how many designs")
    g.add_argument("--model", default="gemini-4-carbon")
    g.add_argument("--brief", help="your own art direction, or the change to make with --base")
    g.add_argument("--base", help="an existing variant to revise")
    s = sub.add_parser("serve", help="show the designs side by side")
    s.add_argument("--port", type=int, default=8770)
    args = ap.parse_args()
    generate(args) if args.cmd == "generate" else serve(args)


if __name__ == "__main__":
    main()
