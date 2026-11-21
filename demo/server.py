"""routr live stage.  `python -m demo.server [--port 8000]`  (stdlib only, works offline for replays)."""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import statistics
import subprocess
import sys
import threading
import time
from email.parser import BytesParser
from email.policy import default as email_default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
RUNS = ROOT / "runs"
SWARM = RUNS / "swarm"
LIVE = RUNS / "live"
LAYOUT = RUNS / "layout"
PY = str(ROOT / ".venv" / "bin" / "python") if (ROOT / ".venv" / "bin" / "python").exists() else sys.executable
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
SAFE_PNG = re.compile(r"^[A-Za-z0-9_.-]{1,120}\.(png|jpg|jpeg)$")
DEFAULT_SPEC = {"pm_deg": [">=", 60.0], "ugf_hz": [">=", 1e7], "dc_gain_db": [">=", 60.0], "i_core_a": ["<=", 1e-4]}
GUARD = {"pm_deg": ("add", 5.0), "ugf_hz": ("mul", 1.1), "dc_gain_db": ("add", 3.0), "i_core_a": ("mul", 0.9)}
PROCS: dict[str, subprocess.Popen] = {}      # run_id / signoff name -> process
LOCK = threading.Lock()


def guarded(spec: dict) -> dict:
    out = {}
    for m, (op, b) in spec.items():
        kind, amt = GUARD.get(m, ("add", 0.0))
        out[m] = [op, (b + amt if op.startswith(">") else b - amt) if kind == "add" else b * amt]
    return out


def read_jsonl(p: Path) -> list[dict]:
    out = []
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return out


def read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def session_dir() -> Path:
    d = LIVE / time.strftime("s_%Y%m%d_%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_cli(args: list[str], out: Path, timeout: float = 90) -> tuple[dict | None, str]:
    """Run one of our CLIs; return (parsed output json | None, error text)."""
    try:
        r = subprocess.run([PY, "-m", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout:.0f}s"
    except OSError as e:
        return None, str(e)
    data = read_json(out)
    if data is not None:
        return data, ""
    tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
    return None, f"exit {r.returncode}: " + " | ".join(tail)


# ----------------------------------------------------------------------------- fallbacks (clearly labelled in the UI)
def answer_key(circuit: str) -> dict:
    """Role answer key from the circuit registry; used only when the Decisions-API CLI is unavailable."""
    try:
        sys.path.insert(0, str(ROOT))
        from sizing import opamp2stg as oa
        c = oa.CIRCUITS.get(circuit) or oa.CIRCUITS["fc"]
        devs = [{"subckt": s, "inst": i, "label": i.lstrip("X"), "role": r, "confidence": None, "truth": r,
                 "correct": None} for (s, i), r in c["role_map"].items()]
        return {"circuit": circuit, "devices": devs, "knobs": list(c["space"]), "fallback": True,
                "model": "answer key (Decisions API not called)"}
    except Exception as e:  # noqa: BLE001
        return {"circuit": circuit, "devices": [], "knobs": [], "fallback": True, "model": f"unavailable ({e})"}


def parse_spec_offline(text: str) -> dict:
    """Tiny regex spec parser; used only when the Gemini spec-reader CLI is unavailable."""
    spec = {k: list(v) for k, v in DEFAULT_SPEC.items()}
    notes = []
    t = text.lower()
    num = r"(\d+(?:\.\d+)?)"
    m = re.search(num + r"\s*(?:degrees?|deg|°)", t) or re.search(r"(?:phase margin|stability)[^\d]{0,30}" + num, t)
    if m:
        spec["pm_deg"][1] = float(m.group(1)); notes.append(f"phase margin {m.group(1)} deg")
    m = re.search(num + r"\s*(mega ?hertz|mhz|megs?|khz|kilo ?hertz)", t)
    if m:
        spec["ugf_hz"][1] = float(m.group(1)) * (1e3 if m.group(2).startswith("k") else 1e6); notes.append(f"speed {m.group(0)}")
    m = re.search(num + r"\s*(?:db|decibels?)", t)
    if m:
        spec["dc_gain_db"][1] = float(m.group(1)); notes.append(f"gain {m.group(1)} dB")
    m = re.search(num + r"\s*(?:ua|µa|μa|micro ?amps?|microamperes?)", t)
    if m:
        spec["i_core_a"][1] = float(m.group(1)) * 1e-6; notes.append(f"current {m.group(1)} uA")
    return {"transcript": text, "spec": spec, "notes": notes or ["no numbers found; using default spec"],
            "model": "offline regex parser (Gemini not called)", "fallback": True}


# ----------------------------------------------------------------------------- run events
def synth_events(run_dir: Path) -> list[dict]:
    """Rebuild an event stream for old runs that only have history.jsonl + architect.jsonl."""
    hist = read_jsonl(run_dir / "history.jsonl")
    if not hist:
        return []
    arch = read_jsonl(run_dir / "architect.jsonl")
    summ = read_json(run_dir / "summary.json") or {}
    wall = float(summ.get("wall_s") or 240.0)
    spec = DEFAULT_SPEC
    target = guarded(spec) if summ.get("guard_band") else spec
    workers = sorted({h["worker"] for h in hist if str(h.get("worker", "")).startswith("w")})
    ev = [{"t": 8.0, "type": "start", "circuit": summ.get("circuit") or "diff", "spec": spec, "target": target,
           "knobs": list(hist[0].get("params", {})), "workers": max(len(workers), 1), "per_worker": 2,
           "max_candidates": summ.get("candidates") or len(hist), "synth": True,
           "models": {"architect": "gemini-4-carbon", "worker": "gemini-4-hydrogen", "critic": "gemini-4-neon",
                      **(summ.get("models") or {})}}]
    iters = sorted({h.get("iter", 0) for h in hist})
    plan_by_iter = {}
    for a in arch:
        plan_by_iter.setdefault(a.get("iter", 0), a)
    first_lat = float((arch[0].get("latency_s") if arch else 0) or 0)
    t_rounds0 = 8.0 + first_lat
    rounds = [i for i in iters if i > 0] or [0]
    span = max(wall - t_rounds0, 10.0) / max(len(rounds), 1)

    def cand(h, t):
        return {"t": round(t, 2), "type": "candidate", "n": h["n"], "iter": h.get("iter", 0),
                "worker": h.get("worker", "?"), "passed": bool(h.get("passed")),
                "passed_nominal": bool(h.get("passed_nominal", h.get("passed"))), "display": h.get("display", {}),
                "worst": h.get("worst", {}),
                "points": [{"corner": p["corner"], "temp": p["temp"], "pass": p.get("pass", False),
                            "fails": p.get("fails", [])} for p in h.get("points", [])],
                "rationale": h.get("rationale", ""), "n_failures": h.get("n_failures", 0)}

    def plan(a, t):
        lat = float(a.get("latency_s") or 0)
        return [{"t": round(t, 2), "type": "plan_requested", "model": a.get("model", "gemini-4-carbon"),
                 "iter": a.get("iter", 0), "n_evaluated": a.get("n_evaluated_at_request", 0)},
                {"t": round(t + lat, 2), "type": "plan_ready", "model": a.get("model"), "latency_s": lat,
                 "iter": a.get("iter", 0)},
                {"t": round(t + lat, 2), "type": "plan_applied", "diagnosis": a.get("diagnosis", ""),
                 "binding": a.get("binding_constraint"), "likely_infeasible": a.get("likely_infeasible"),
                 "directions": [{"focus": d.get("focus", ""), "knobs": d.get("knobs", [])}
                                for d in a.get("directions", [])]}]

    for h in hist:
        if h.get("iter", 0) == 0:
            ev.append(cand(h, 8.0))
    if 0 in plan_by_iter:
        ev += plan(plan_by_iter[0], 8.0)
    for k, it in enumerate(rounds):
        if it == 0:
            continue
        t0 = t_rounds0 + k * span
        if it in plan_by_iter and it != 0:
            a = plan_by_iter[it]
            ev += plan(a, max(t0 - float(a.get("latency_s") or 0), 8.0))
        ev.append({"t": round(t0, 2), "type": "round_start", "iter": it, "n_evaluated": 0})
        hs = [h for h in hist if h.get("iter", 0) == it]
        for j, h in enumerate(hs):
            ev.append(cand(h, t0 + span * (0.35 + 0.6 * (j + 1) / len(hs))))
    if summ:
        ev.append({"t": round(wall, 2), "type": "done",
                   **{k: summ.get(k) for k in ("success", "candidates_to_first_pass", "best_n", "best_i_core_uA",
                                               "recheck_full_lib", "llm_calls", "wall_s")}})
    ev.sort(key=lambda e: e["t"])
    return ev


def list_layout_runs() -> list[dict]:
    out = []
    if LAYOUT.exists():
        for d in sorted(LAYOUT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if d.is_dir() and not d.name.startswith("_") and (d / "events.jsonl").exists():
                s = read_json(d / "summary.json") or {}
                out.append({"id": d.name, "events": True, "done": bool(s), "success": s.get("passed")})
    return out


def list_runs() -> list[dict]:
    out = []
    if SWARM.exists():
        for d in sorted(SWARM.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not d.is_dir() or d.name.startswith("_"):
                continue
            has_ev = (d / "events.jsonl").exists()
            if not has_ev and not (d / "history.jsonl").exists():
                continue
            s = read_json(d / "summary.json") or {}
            out.append({"id": d.name, "events": has_ev, "done": bool(s), "success": s.get("success"),
                        "circuit": s.get("circuit") or ("diff" if s else None),
                        "first_pass": s.get("candidates_to_first_pass")})
    return out


# ----------------------------------------------------------------------------- proof
def _series(run_id: str) -> str:
    return re.sub(r"(_s\d+|seed\d+)$", "", run_id).rstrip("_") or "default"


SERIES = {  # series -> (group key, circuit label, method label)
    "default": ("diff_plain", None), "": ("diff_plain", None), "guard": ("diff_guard", None),
    "heldout_fc": ("fc", None), "heldout_tele": ("tele", None),
    "v2_fc": ("fc", "Agent crew v2 (re-plan + plot reviewer)"),
    "v2_tele": ("tele", "Agent crew v2 (re-plan + plot reviewer)"),
}
GROUPS = [("diff_guard", "Dev circuit: two-stage Miller op-amp (guard-banded targets)"),
          ("diff_plain", "Dev circuit: two-stage Miller op-amp (plain spec, no guard band)"),
          ("fc", "Held-out: folded-cascode op-amp (never tuned on)"),
          ("tele", "Held-out: telescopic op-amp (never tuned on)")]
METHOD = {"gp": "Bayesian optimisation (GP)", "tpe": "TPE (Optuna-style)", "random": "Random search",
          "single": "Single LLM call", "swarm": "Agent crew (Gemini 4)"}


def proof() -> dict:
    rows: dict[tuple, dict] = {}

    def add(group, method, order, run, success, first, best, budget, running=False):
        r = rows.setdefault((group, method), {"group": group, "method": method, "order": order, "runs": [],
                                              "passed": 0, "n": 0, "running": 0, "firsts": [], "bests": [],
                                              "budgets": set()})
        r["runs"].append(run)
        if running:
            r["running"] += 1
            return
        r["n"] += 1
        r["passed"] += bool(success)
        if success and first:
            r["firsts"].append(first)
        if success and best:
            r["bests"].append(best)
        if budget:
            r["budgets"].add(budget)

    tokens, calls, walls = [], [], []
    if SWARM.exists():
        for d in sorted(SWARM.iterdir()):
            if not d.is_dir() or d.name.startswith(("_", "live", "first")):
                continue
            ser = _series(d.name)
            if ser not in SERIES:
                continue
            group, label = SERIES[ser]
            s = read_json(d / "summary.json")
            if s is None:
                if (d / "history.jsonl").exists():
                    add(group, label or METHOD["swarm"], 0 if label else 1, d.name, None, None, None, None, True)
                continue
            add(group, label or METHOD["swarm"], 0 if label else 1, d.name, s.get("success"),
                s.get("candidates_to_first_pass"), s.get("best_i_core_uA"), s.get("candidates"))
            tk = s.get("tokens") or {}
            if tk:
                tokens.append(sum(v for v in tk.values() if isinstance(v, (int, float))))
            if s.get("llm_calls"):
                calls.append(s["llm_calls"])
            if s.get("wall_s"):
                walls.append(s["wall_s"])
    bdir = RUNS / "opamp_baselines"
    if bdir.exists():
        for f in sorted(bdir.glob("*.json")):
            m = re.match(r"^(.*)_(gp|tpe|random|single)_s(\d+)_c(\d+)$", f.stem)
            d = read_json(f)
            if not m or not d:
                continue
            ser, meth = m.group(1), m.group(2)
            if ser not in SERIES:
                continue
            s = d.get("summary", {})
            add(SERIES[ser][0], METHOD.get(meth, meth), {"gp": 2, "tpe": 3, "random": 4, "single": 5}.get(meth, 9),
                f.stem, s.get("success"), s.get("candidates_to_first_pass"), s.get("best_i_core_uA"),
                s.get("candidates"))
    odir = RUNS / "oneshot"
    if odir.exists():
        for f in sorted(odir.glob("*.json")):
            d = read_json(f)
            if not isinstance(d, dict) or "passed_target" not in d:
                continue
            circ = d.get("circuit") or "diff"
            label = "Single model call" + (f" ({d['model']})" if d.get("model") else "")
            if circ == "diff":
                add("diff_guard", label, 5, f.stem, d.get("passed_target"), 1, d.get("best_i_core_uA"), 1)
                add("diff_plain", label, 5, f.stem, d.get("passed_nominal_spec"), 1, d.get("best_i_core_uA"), 1)
            elif circ in ("fc", "tele"):
                add(circ, label, 5, f.stem, d.get("passed_target"), 1, d.get("best_i_core_uA"), 1)
    intake = None
    ie = read_json(RUNS / "intake_eval.json")
    if isinstance(ie, dict) and ie.get("rows"):
        def tot(flag):
            rs = [r for r in ie["rows"] if bool(r.get("with_image")) == flag]
            return {"n_correct": sum(r.get("n_correct", 0) for r in rs), "n": sum(r.get("n", 0) for r in rs),
                    "per_circuit": {r.get("circuit"): [r.get("n_correct"), r.get("n")] for r in rs}}
        intake = {"with_image": tot(True), "netlist_only": tot(False)}
    groups = []
    for key, title in GROUPS:
        rs = sorted([r for (g, _), r in rows.items() if g == key], key=lambda r: (r["order"], r["method"]))
        if not rs:
            continue
        groups.append({"key": key, "title": title, "rows": [{
            "method": r["method"], "is_crew": r["order"] <= 1, "passed": r["passed"], "n": r["n"],
            "running": r["running"],
            "median_first_pass": statistics.median(r["firsts"]) if r["firsts"] else None,
            "best_current_uA": round(min(r["bests"]), 1) if r["bests"] else None,
            "budget": sorted(r["budgets"]), "runs": r["runs"]} for r in rs]})
    cost = None
    if calls:
        cost = {"runs": len(calls), "median_llm_calls": statistics.median(calls),
                "median_tokens": statistics.median(tokens) if tokens else None,
                "median_wall_s": statistics.median(walls) if walls else None}
    signoffs = {}
    sd = RUNS / "signoff"
    if sd.exists():
        for d in sorted(sd.iterdir()):
            c = read_json(d / "comparison.json")
            if c:
                c.pop("rows", None)
                signoffs[d.name] = c
    red = {}
    rd = RUNS / "redteam"
    if rd.exists():
        for d in sorted(rd.iterdir()):
            f, s = read_json(d / "findings.json"), read_json(d / "summary.json")
            if f or s:
                red[d.name] = {"findings": (f or {}).get("findings", []) if isinstance(f, dict) else f,
                               "summary": s, "overall": (f or {}).get("overall") if isinstance(f, dict) else None}
            elif d.is_dir():
                red[d.name] = {"running": True, "tool_calls": len(read_jsonl(d / "tool_calls.jsonl"))}
    return {"groups": groups, "cost": cost, "signoff": signoffs, "redteam": red, "intake": intake}


# ----------------------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):  # quiet
        if "/api/" in (a[0] if a else ""):
            sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % a))

    # -- helpers
    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, p: Path):
        try:
            b = p.read_bytes()
        except OSError:
            return self.send_json({"error": "not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(p.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def upload(self) -> tuple[bytes, str, dict]:
        """Return (file bytes, filename/ctype hint, other form fields) from raw or multipart body."""
        raw = self.body()
        if len(raw) < int(self.headers.get("Content-Length") or 0):
            return b"", "", {}          # interrupted upload: never hand a truncated file to a CLI
        ctype = self.headers.get("Content-Type", "")
        if ctype.startswith("multipart/"):
            msg = BytesParser(policy=email_default).parsebytes(
                b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + raw)
            data, hint, fields = b"", "", {}
            for part in msg.iter_parts():
                payload = part.get_payload(decode=True) or b""
                if part.get_filename():
                    data, hint = payload, part.get_filename() + " " + part.get_content_type()
                else:
                    fields[part.get_param("name", header="content-disposition") or ""] = payload.decode(errors="replace").strip()
            return data, hint, fields
        return raw, ctype, {}

    def sse_open(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

    def sse(self, obj, event: str | None = None):
        s = (f"event: {event}\n" if event else "") + "data: " + json.dumps(obj) + "\n\n"
        self.wfile.write(s.encode())
        self.wfile.flush()

    # -- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self.send_file(STATIC / "index.html")
            if u.path.startswith("/static/"):
                name = u.path[len("/static/"):]
                if not re.match(r"^[A-Za-z0-9_.-]+$", name):
                    return self.send_json({"error": "bad name"}, 400)
                return self.send_file(STATIC / name)
            if u.path == "/api/runs":
                return self.send_json({"runs": list_layout_runs() if q.get("kind") == "layout" else list_runs()})
            if u.path == "/api/proof":
                return self.send_json(proof())
            if u.path == "/api/runfile":
                run, name = q.get("run", ""), q.get("name", "")
                if not SAFE_ID.match(run) or not SAFE_PNG.match(name) or ".." in run or ".." in name:
                    return self.send_json({"error": "bad name"}, 400)
                return self.send_file((LAYOUT if q.get("kind") == "layout" else SWARM) / run / name)
            if u.path == "/api/livefile":
                sess, name = q.get("session", ""), q.get("name", "")
                if not SAFE_ID.match(sess) or not SAFE_PNG.match(name) or ".." in sess or ".." in name:
                    return self.send_json({"error": "bad name"}, 400)
                return self.send_file(LIVE / sess / name)
            if u.path == "/api/events":
                return self.events(q)
            if u.path == "/api/signoff_events":
                return self.signoff_events(q)
            if u.path == "/api/signoff_result":
                name = q.get("name", "")
                if not SAFE_ID.match(name) or ".." in name:
                    return self.send_json({"error": "bad name"}, 400)
                return self.send_json({"comparison": read_json(RUNS / "signoff" / name / "comparison.json")})
            if u.path == "/api/explain":
                from demo import explain
                run = q.get("run", "")
                if run and (not SAFE_ID.match(run) or ".." in run):
                    return self.send_json({"error": "bad run id"}, 400)
                upto = int(q["upto"]) if q.get("upto", "").isdigit() and int(q["upto"]) > 0 else None
                if q.get("n", "").isdigit():
                    return self.send_json(explain.candidate(run, int(q["n"])))
                if q.get("part"):
                    if q["part"] not in explain.PARTS:
                        return self.send_json({"error": "unknown part"}, 400)
                    circ = q.get("circuit") if q.get("circuit") in ("diff", "fc", "tele") else None
                    return self.send_json(explain.part(run, q["part"], upto, circ))
                return self.send_json(explain.overview(run, upto) if run else {"error": "no run"})
            if u.path == "/api/answer_key":
                return self.send_json(answer_key(q.get("circuit") if q.get("circuit") in ("diff", "fc", "tele") else "diff"))
            if u.path == "/api/schematic":
                from demo import explain
                name = explain.SCHEMATIC.get(q.get("circuit", ""))
                if not name:
                    return self.send_json({"error": "unknown circuit"}, 400)
                return self.send_file(ROOT / "third_party" / "AMPICBench" / "schematics" / name)
            if u.path == "/api/pcb":
                from demo import explain
                return self.send_json(explain.pcb_runs())
            if u.path in ("/api/pcb_board", "/api/pcbfile"):
                from demo import explain
                run, board, name = q.get("run", ""), q.get("board", ""), q.get("name", "")
                if not SAFE_ID.match(run) or not SAFE_ID.match(board) or ".." in run or ".." in board:
                    return self.send_json({"error": "bad name"}, 400)
                if u.path == "/api/pcb_board":
                    return self.send_json(explain.pcb_board(run, board))
                if not SAFE_PNG.match(name) or ".." in name:
                    return self.send_json({"error": "bad name"}, 400)
                return self.send_file(explain.PCB / run / board / name)
            return self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            try:
                self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)
            except OSError:
                pass

    def events(self, q):
        run = q.get("run", "")
        if not SAFE_ID.match(run) or ".." in run:
            return self.send_json({"error": "bad run id"}, 400)
        layout = q.get("kind") == "layout"
        d = (LAYOUT if layout else SWARM) / run
        replay = q.get("replay") in ("1", "true")
        speed = max(float(q.get("speed") or 1), 0.1)
        max_gap = float(q.get("max_gap") or 6.0)
        f = d / "events.jsonl"
        self.sse_open()
        sent_done = False
        pos = 0
        if replay:
            evs = read_jsonl(f) if f.exists() else ([] if layout else synth_events(d))
            if not evs:
                self.sse({"error": f"run {run} has no events or history"}, "fail")
                return
            if f.exists():
                pos = f.stat().st_size
            prev = evs[0].get("t", 0)
            for e in evs:
                gap = min(max(e.get("t", prev) - prev, 0) / speed, max_gap)
                prev = e.get("t", prev)
                if gap > 0.01:
                    time.sleep(gap)
                self.sse(e)
                sent_done |= e.get("type") == "done"
            if sent_done or not f.exists():
                self.sse({"reason": "replay finished"}, "end")
                return
        # live tail (also continues an in-progress run after its replayed part)
        proc = PROCS.get(("layout:" if layout else "") + run)
        logname = f"layout_{run}.log" if layout else f"{run}.log"
        buf, idle, last_ping = b"", 0.0, time.time()
        while True:
            chunk = b""
            if f.exists():
                with f.open("rb") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    pos += len(chunk)
            if chunk:
                idle = 0.0
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self.sse(e)
                    sent_done |= e.get("type") == "done"
                if sent_done:
                    self.sse({"reason": "done"}, "end")
                    return
            else:
                time.sleep(0.25)
                idle += 0.25
                if proc is not None and proc.poll() is not None and idle > 2.0:
                    tail = ""
                    try:
                        tail = "\n".join((LIVE / logname).read_text().splitlines()[-6:])
                    except OSError:
                        pass
                    self.sse({"reason": f"process exited ({proc.returncode})", "exit": proc.returncode, "log": tail}, "end")
                    return
                if proc is None and idle > 900:
                    self.sse({"reason": "no new events for 15 min"}, "end")
                    return
                if time.time() - last_ping > 5:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = time.time()

    def signoff_events(self, q):
        name = q.get("name", "")
        if not SAFE_ID.match(name) or ".." in name:
            return self.send_json({"error": "bad name"}, 400)
        sdir = RUNS / "signoff" / name
        log = LIVE / f"signoff_{name}.log"
        proc = PROCS.get("signoff:" + name)
        self.sse_open()
        if proc is None and not log.exists():
            # finished earlier (outside this server): rebuild a short trace from the agent's own SPICE calls
            calls = read_jsonl(sdir / "spice_calls.jsonl")
            comp = read_json(sdir / "comparison.json")
            if not calls and not comp:
                self.sse({"error": f"no sign-off named {name}"}, "fail")
                return
            self.sse({"line": f"sign-off agent session '{name}' (recorded)"})
            for c in calls:
                time.sleep(0.5)
                self.sse({"line": "  agent -> run_spice()"})
                time.sleep(0.4)
                self.sse({"line": f"   [run_spice #{c.get('call')} '{c.get('label', '')}'] {len(c.get('points', []))} point(s), "
                                  f"{c.get('seconds', '?')}s", "deck_chars": len(c.get("deck", ""))})
            time.sleep(0.4)
            self.sse({"comparison": comp, "running": comp is None}, "end")
            return
        pos, buf, last_ping = 0, b"", time.time()
        while True:
            chunk = b""
            if log.exists():
                with log.open("rb") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    pos += len(chunk)
            if chunk:
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    s = line.decode(errors="replace").rstrip()
                    if s:
                        self.sse({"line": s})
            else:
                if proc is None or proc.poll() is not None:
                    time.sleep(0.5)
                    self.sse({"comparison": read_json(sdir / "comparison.json"),
                              "exit": proc.returncode if proc else None}, "end")
                    return
                time.sleep(0.3)
                if time.time() - last_ping > 5:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = time.time()

    # -- POST
    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/photo":
                data, hint, fields = self.upload()
                if not data:
                    return self.send_json({"error": "empty upload"}, 400)
                sd = session_dir()
                ext = "png" if ("png" in hint.lower() or data[:4] == b"\x89PNG") else "jpg"
                img = sd / f"photo.{ext}"
                img.write_bytes(data)
                circuit = fields.get("circuit") or q.get("circuit") or "auto"
                if circuit not in ("auto", "diff", "fc", "tele"):
                    circuit = "auto"
                out = sd / "roles.json"
                res, err = run_cli(["sizing.intake", "roles", "--image", str(img), "--out", str(out),
                                    "--circuit", circuit], out, timeout=120)
                if res is None:
                    res = answer_key(circuit if circuit != "auto" else "fc")
                    res["error"] = err
                res["session"], res["photo"] = sd.name, img.name
                return self.send_json(res)
            if u.path == "/api/voice":
                data, hint, _ = self.upload()
                if not data:
                    return self.send_json({"error": "empty upload"}, 400)
                sd = session_dir()
                ext = next((e for e in ("webm", "ogg", "wav", "mp3", "m4a", "mp4") if e in hint.lower()), "webm")
                aud = sd / f"voice.{ext}"
                aud.write_bytes(data)
                out = sd / "spec.json"
                res, err = run_cli(["sizing.intake", "spec", "--audio", str(aud), "--out", str(out)], out, timeout=120)
                if res is None:
                    return self.send_json({"error": f"spec reader unavailable ({err}); type the spec instead",
                                           "session": sd.name}, 200)
                res["session"] = sd.name
                res["target"] = guarded(res.get("spec") or DEFAULT_SPEC)
                return self.send_json(res)
            if u.path == "/api/spec_text":
                try:
                    text = (json.loads(self.body() or b"{}").get("text") or "").strip()
                except json.JSONDecodeError:
                    text = ""
                if not text:
                    return self.send_json({"error": "no text"}, 400)
                sd = session_dir()
                out = sd / "spec.json"
                res, err = run_cli(["sizing.intake", "spec", "--text", text, "--out", str(out)], out, timeout=90)
                if res is None:
                    res = parse_spec_offline(text)
                    res["error"] = err
                res["session"] = sd.name
                res["target"] = guarded(res.get("spec") or DEFAULT_SPEC)
                return self.send_json(res)
            if u.path in ("/api/ask", "/api/change"):
                import base64
                from demo import explain
                body = json.loads(self.body() or b"{}")
                run = str(body.get("run") or "")
                if run and (not SAFE_ID.match(run) or ".." in run):
                    return self.send_json({"error": "bad run id"}, 400)
                audio = base64.b64decode(body["audio"]) if body.get("audio") else None
                mime = str(body.get("mime") or "audio/webm")
                text = str(body.get("question") or body.get("text") or "").strip()[:2000]
                if not audio and not text:
                    return self.send_json({"error": "ask a question (type it or hold to talk)"}, 400)
                if u.path == "/api/ask":
                    if not run or not (SWARM / run / "history.jsonl").exists():
                        return self.send_json({"error": "no crew run to ask about yet: start or replay a run first"}, 400)
                    return self.send_json(explain.ask(run, text, audio, mime))
                spec = body.get("spec") if isinstance(body.get("spec"), dict) else None
                return self.send_json(explain.change(run if run and (SWARM / run / "history.jsonl").exists() else "", text, audio, mime, spec))
            if u.path == "/api/run":
                body = json.loads(self.body() or b"{}")
                circuit = body.get("circuit") if body.get("circuit") in ("diff", "fc", "tele") else "diff"
                run_id = time.strftime("live_%H%M%S")
                LIVE.mkdir(parents=True, exist_ok=True)
                cmd = [PY, "-u", "-m", "sizing.swarm", "--circuit", circuit,
                       "--workers", str(int(body.get("workers") or 8)), "--per-worker", str(int(body.get("per_worker") or 2)),
                       "--candidates", str(int(body.get("candidates") or 96)), "--fast-start", "--critic",
                       "--run-id", run_id]
                rs, ss = str(body.get("roles_session") or ""), str(body.get("spec_session") or "")
                rf = LIVE / rs / "roles.json" if SAFE_ID.match(rs) and ".." not in rs else None
                if rf is not None and rf.exists():          # photo-derived role map defines the knobs
                    cmd += ["--roles", str(rf)]
                sf = LIVE / ss / "spec.json" if SAFE_ID.match(ss) and ".." not in ss else None
                spec = body.get("spec")
                if sf is not None and sf.exists():          # intake spec JSON, as-is
                    cmd += ["--spec", str(sf)]
                elif isinstance(spec, dict) and spec:
                    sp = LIVE / f"{run_id}_spec.json"
                    sp.write_text(json.dumps(spec))
                    cmd += ["--spec", str(sp)]
                sfrom, started_from = str(body.get("start_from_run") or ""), None
                if sfrom and SAFE_ID.match(sfrom) and ".." not in sfrom:      # change request: warm-start from that run's best design
                    sj = SWARM / sfrom / "summary.json"
                    try:
                        ok = "--start-from" in (ROOT / "sizing" / "swarm.py").read_text()
                    except OSError:
                        ok = False
                    if ok and (read_json(sj) or {}).get("best_params") and rf is None:
                        cmd += ["--start-from", str(sj)]
                        started_from = sfrom
                logf = (LIVE / f"{run_id}.log").open("wb")
                with LOCK:
                    PROCS[run_id] = subprocess.Popen(cmd, cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT)
                return self.send_json({"run_id": run_id, "cmd": " ".join(cmd[1:]), "started_from": started_from})
            if u.path == "/api/layout":
                body = json.loads(self.body() or b"{}")
                run = body.get("run", "")
                if not SAFE_ID.match(run) or ".." in run or not (SWARM / run / "summary.json").exists():
                    return self.send_json({"error": f"sizing run {run or '(none)'} has no finished design yet (summary.json missing)"}, 400)
                if not (ROOT / "layout" / "crew.py").exists():
                    return self.send_json({"error": "layout crew is not installed yet (layout/crew.py missing); replay a recorded layout run instead"}, 400)
                run_id = time.strftime("live_%H%M%S")
                LIVE.mkdir(parents=True, exist_ok=True)
                cmd = [PY, "-u", "-m", "layout.crew", "--run", str(SWARM / run),
                       "--candidates", str(int(body.get("candidates") or 16)), "--workers", str(int(body.get("workers") or 4)),
                       "--run-id", run_id]
                logf = (LIVE / f"layout_{run_id}.log").open("wb")
                with LOCK:
                    PROCS["layout:" + run_id] = subprocess.Popen(cmd, cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT)
                return self.send_json({"run_id": run_id, "cmd": " ".join(cmd[1:])})
            if u.path == "/api/stop":
                body = json.loads(self.body() or b"{}")
                p = PROCS.get(body.get("run", ""))
                if p and p.poll() is None:
                    p.terminate()
                return self.send_json({"ok": True})
            if u.path == "/api/signoff":
                body = json.loads(self.body() or b"{}")
                run = body.get("run", "")
                if not SAFE_ID.match(run) or ".." in run or not (SWARM / run / "summary.json").exists():
                    return self.send_json({"error": f"run {run or '(none)'} has no summary.json yet (finish a run first)"}, 400)
                name = f"live_{run}_{time.strftime('%H%M%S')}"
                LIVE.mkdir(parents=True, exist_ok=True)
                logf = (LIVE / f"signoff_{name}.log").open("wb")
                cmd = [PY, "-u", "-m", "sizing.signoff", "--run", str(SWARM / run), "--id", name]
                with LOCK:
                    PROCS["signoff:" + name] = subprocess.Popen(cmd, cwd=ROOT, stdout=logf, stderr=subprocess.STDOUT)
                return self.send_json({"name": name})
            return self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            try:
                self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)
            except OSError:
                pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), H)
    srv.daemon_threads = True
    print(f"routr stage: http://{a.host}:{a.port}/   (replay: http://{a.host}:{a.port}/?replay=guard_s0&speed=8)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
