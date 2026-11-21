"""PCB routing repair swarm for PCBWorld (target: D3-C, the hard tier).

Hybrid method, disclosed as such: a classic router (Freerouting 2.1.0) routes the board;
a crew of Gemini 4 agents then repairs what it left broken - open connections and rule
violations - by choosing which nets to rip up, in what order to re-route them, on which
layers and in which router mode. KiCad's own push-and-shove router draws every track and
KiCad's rule checker judges every attempt; the agents never touch geometry directly and
never see the human-routed original.

  triage   gemini-decision-1   from the board picture + geometry: which routed nets block an
                               open connection? (yes/no with probability per candidate)
  planner  gemini-4-carbon     reads the picture, the rule report and the attempt history;
                               writes one repair plan per worker (different strategies)
  workers  gemini-4-hydrogen   each refines its plan into concrete rip-up / order / mode /
                               layer choices; an engine process executes it
  judge    PCBWorld engine     saves the board, scores it with PCBWorld's own evaluate_one

Protocol: one submitted board per instance (the swarm's best), every internal attempt counted.

Run (our venv; engine processes use PCBWorld's conda env):
  .venv/bin/python -m pcb.repair.swarm --boards hard --start var/results/baselines/PCBench_hard_test/freerouting/seed0/routed
  .venv/bin/python -m pcb.repair.swarm --boards 0400_laptimer58_Chickadee --no-llm
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PCBW = ROOT / "third_party" / "pcbworld"
CONDA_ENV = Path.home() / "miniforge3" / "envs" / "pcbworld"
DATA = PCBW / "var" / "datasets"
SORTED = DATA / "pcbench" / "exacad_sorted"
OUT = ROOT / "runs" / "pcb"
STEM = "processed_v9_guide_v3"

PLANNER = "gemini-4-carbon"
WORKER = "gemini-4-hydrogen"
DECIDER = "gemini-decision-1"


def engine_env() -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{CONDA_ENV / 'bin'}:{env['PATH']}"
    env["PYTHONPATH"] = "build_rl/pcbnew/python/rl:."
    env["PCBWORLD_DATA_ROOT"] = str(DATA)
    env["CONDA_PREFIX"] = str(CONDA_ENV)
    env.pop("PYTHONHOME", None)
    return env


class Engine:
    """One PCBWorld engine process (JSON lines)."""

    def __init__(self):
        self.p = subprocess.Popen([str(CONDA_ENV / "bin" / "python"), str(ROOT / "pcb" / "repair" / "engine_tool.py")],
                                  cwd=PCBW, env=engine_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def call(self, op: str, timeout: float = 240, **kw):
        import select
        self.p.stdin.write(json.dumps({"op": op, **kw}) + "\n")
        self.p.stdin.flush()
        ready, _, _ = select.select([self.p.stdout], [], [], timeout)
        if not ready:                       # a hung router call must not hang the board
            self.p.kill()
            raise RuntimeError(f"engine timeout ({timeout:.0f} s) during {op}")
        line = self.p.stdout.readline()
        if not line:
            raise RuntimeError(f"engine died during {op}")
        r = json.loads(line)
        if not r["ok"]:
            raise RuntimeError(f"{op}: {r['error']}")
        return r["result"]

    def close(self):
        try:
            self.p.stdin.close()
            self.p.terminate()
        except Exception:
            pass


class Live:
    """Publishes a run in the solver/viewer contract (pcb/CONTRACT.md): whole-board versions named
    <seq>__<agent>.kicad_pcb in the watched folder, plus events.jsonl and result.json."""

    def __init__(self, folder: Path):
        self.dir = folder
        self.dir.mkdir(parents=True, exist_ok=True)
        self.t0 = time.time()
        self.seq = 0
        self.lock = threading.Lock()

    def event(self, type: str, **kw) -> None:
        with self.lock, open(self.dir / "events.jsonl", "a") as f:
            f.write(json.dumps({"t": round(time.time() - self.t0, 2), "type": type, **kw}, default=str) + "\n")

    def publish(self, board_file: str | Path, agent: str) -> str:
        """Atomic copy of a complete board as the next version, credited to `agent`."""
        with self.lock:
            name = f"{self.seq:04d}__{agent}.kicad_pcb"
            self.seq += 1
        tmp = self.dir / (name + ".tmp")
        shutil.copy(board_file, tmp)
        os.replace(tmp, self.dir / name)
        return name

    def result(self, claimed_clean: bool, attempts: int, left: list) -> None:
        (self.dir / "result.json").write_text(json.dumps(
            {"claimed_clean": bool(claimed_clean), "attempts": attempts, "left": left}, indent=1))


def agent_name(rec: dict) -> str:
    """Who gets credit for an attempt in the viewer: the model that wrote the plan, else the script."""
    by = str((rec.get("plan") or {}).get("by") or "script")
    by = by.replace("gemini-4-", "").replace("gemini-", "").split(" ")[0]
    return "".join(ch for ch in by if ch.isalnum() or ch == "-") or "script"


def board_files(board_id: str) -> dict:
    d = SORTED / board_id
    return {"unrouted": d / f"{STEM}_unrouted.kicad_pcb", "pro": d / f"{STEM}.kicad_pro"}


# --------------------------------------------------------------------------- plans

def scripted_plans(st: dict) -> list[dict]:
    """Model-free repair plans: the control arm, and the seeds the agents improve on."""
    open_nets = [n["net"] for n in st["open_nets"]]
    err_nets = sorted({int(k) for k in (st.get("errors_per_net") or {}) if str(k).lstrip("-").isdigit()})
    plans = [
        {"name": "finish-open-walkaround", "ripup": [], "order": open_nets, "mode": 2, "vias": False},
        {"name": "finish-open-shove", "ripup": [], "order": open_nets, "mode": 1, "vias": False},
        {"name": "finish-open-then-vias", "ripup": [], "order": open_nets, "mode": 2, "vias": True},
        {"name": "finish-open-then-vias-shove", "ripup": [], "order": open_nets, "mode": 1, "vias": True},
    ]
    if err_nets:
        plans.append({"name": "rip-error-nets", "ripup": err_nets, "order": err_nets + open_nets, "mode": 2})
        plans.append({"name": "rip-error-nets-shove", "ripup": err_nets, "order": open_nets + err_nets, "mode": 1})
    if open_nets:
        plans.append({"name": "rip-open-nets", "ripup": open_nets, "order": open_nets, "mode": 2})
    return plans


def random_plans(st: dict, block: dict, n: int, seed: int) -> list[dict]:
    """Control arm: random rip-up sets drawn from the same candidate blockers the agents see."""
    import random
    rng = random.Random(seed)
    open_nets = [x["net"] for x in st["open_nets"]]
    cands = sorted({c["net"] for oc in block.get("open_connections", []) for c in oc["candidates"]})
    plans = []
    for i in range(n):
        k = rng.randint(1, max(1, min(4, len(cands)))) if cands else 0
        rip = rng.sample(cands, k) if cands else []
        order = open_nets + rip
        rng.shuffle(order)
        plans.append({"name": f"random-{i}", "ripup": rip + open_nets if rng.random() < 0.5 else rip,
                      "order": order, "mode": rng.choice([1, 2]), "by": "random"})
    return plans


CARBON_SLOTS = threading.Semaphore(2)      # Carbon timed out today whenever many calls overlapped


def decisions_blockers(board_id: str, block: dict, image: Path, log_dir: Path) -> dict:
    """Decisions API screen: for each (open connection, candidate net) - is that net's copper
    really in the way? One call, one yes/no question per pair, calibrated probabilities."""
    pairs = [(oc, c) for oc in block.get("open_connections", []) for c in oc["candidates"]][:60]
    if not pairs:
        return {}
    c = _client()
    blocks = [{"type": "text", "text": "PCB after autorouting. Image: red = copper layer 1, blue = layer 2, "
               "squares = pads, orange dashed = connections still open (labelled by net name), red X = rule errors. "
               "Coordinates are millimetres as on the axes."}]
    if image.exists():
        blocks.append({"type": "image", "mime_type": "image/png", "resolution": "high",
                       "data": base64.b64encode(image.read_bytes()).decode()})
    qs = {}
    for i, (oc, cand) in enumerate(pairs):
        qs[f"q{i}"] = {"type": "noul", "instructions":
                       f"The connection of net '{oc['name']}' from ({oc['from'][0]}, {oc['from'][1]}) to "
                       f"({oc['to'][0]}, {oc['to'][1]}) is still open. Net '{cand['name']}' has copper crossing the "
                       f"straight path between those two points {cand['crossings']} time(s) on layer(s) "
                       f"{cand['layers']}. Would removing net '{cand['name']}' copper be needed to complete this "
                       f"connection on a two-layer board (i.e. it cannot simply be passed on the other layer or "
                       f"walked around)?",
                       "criteria": {"true_description": "Yes: this net's copper walls off the connection.",
                                    "false_description": "No: there is room to go around or use the other layer."}}
    t0 = time.time()
    res = c.decisions.create(model=DECIDER, input=blocks, questions=qs)
    out = {}
    for i, (oc, cand) in enumerate(pairs):
        a = res.answers[f"q{i}"]
        out.setdefault(str(oc["net"]), []).append({"blocker": cand["net"], "name": cand["name"],
                                                   "p_blocks": round(float(a.noul), 3), "crossings": cand["crossings"]})
    for k in out:
        out[k].sort(key=lambda d: -d["p_blocks"])
    with open(log_dir / "llm_calls.jsonl", "a") as f:
        f.write(json.dumps({"board": board_id, "model": DECIDER, "latency_s": round(time.time() - t0, 2),
                            "questions": len(qs), "blockers": out}) + "\n")
    return out


def vision_via_order(board_id: str, start: Path, st: dict, out_dir: Path, max_nets: int = 6) -> dict:
    """VISION STEP. For each open connection, draw the candidate via sites as numbered dots on a
    zoomed picture of the board and ask the Decisions API which one is the best place to change
    layers. Its probabilities over the numbered sites become the ORDER in which the router tries
    them. Returns {net: [site indices best-first]} and logs the pictures and probabilities."""
    f = board_files(board_id)
    eng = Engine()
    scout = {}
    try:
        eng.call("open", board=str(start), project=str(f["pro"]))
        pics = []
        for item in st["open_nets"][:max_nets]:
            r = eng.call("render_sites", net=item["net"], path=str(out_dir / f"sites_net{item['net']}.png"))
            if r.get("image") and r.get("sites"):
                pics.append(r)
    finally:
        eng.close()
    if not pics:
        return {}
    c = _client()
    order = {}
    for r in pics:
        opts = {f"s{i}": f"marker {i} at ({s_['x']:.1f}, {s_['y']:.1f}) mm" for i, s_ in enumerate(r["sites"])}
        t0 = time.time()
        try:
            res = c.decisions.create(model=DECIDER, input=[
                {"type": "text", "text":
                 "Close-up of a two-layer printed circuit board. Red lines are copper tracks on the TOP layer, blue "
                 "lines are copper tracks on the BOTTOM layer, dark squares are pads, white circles are existing "
                 "vias. The dashed orange line is a connection that still has to be made between the green stars "
                 "A and B; it is blocked on its own layer. Yellow numbered dots are candidate places to drill a "
                 "via (a plated hole that moves the route to the other layer)."},
                {"type": "image", "mime_type": "image/png", "resolution": "high",
                 "data": base64.b64encode(Path(r["image"]).read_bytes()).decode()}],
                questions={"site": {"type": "choice", "instructions":
                           "Which numbered marker is the best place for a via on this connection? A good site has "
                           "NO track of either colour and no pad or via under or touching it, and has clear space "
                           "on the other layer to continue toward A or B.", "criteria": opts}})
            a = res.answers["site"]
            probs = dict(a.probabilities) if isinstance(a.probabilities, dict) else {}
            ranked = sorted(range(len(r["sites"])), key=lambda i: -float(probs.get(f"s{i}", 0.0)))
            order[str(r["net"])] = ranked
            scout[str(r["net"])] = {"name": r["name"], "image": Path(r["image"]).name, "choice": a.choice,
                                    "top": [(i, round(float(probs.get(f"s{i}", 0.0)), 3)) for i in ranked[:8]],
                                    "latency_s": round(time.time() - t0, 2)}
        except Exception as e:
            scout[str(r["net"])] = {"error": str(e)[:200]}
    (out_dir / "via_scout.json").write_text(json.dumps(scout, indent=1))
    with open(out_dir / "llm_calls.jsonl", "a") as fh:
        fh.write(json.dumps({"board": board_id, "model": DECIDER, "step": "vision via-site ranking",
                             "nets": len(pics), "scout": scout}) + "\n")
    return order


def execute(board_id: str, start: Path, plan: dict, out_dir: Path, tag: str) -> dict:
    """Run one repair attempt in its own engine process; returns the judge's score."""
    f = board_files(board_id)
    eng = Engine()
    t0 = time.time()
    rec = {"board": board_id, "tag": tag, "plan": plan}
    try:
        st0 = eng.call("open", board=str(start), project=str(f["pro"]))
        ripup = [int(n) for n in plan.get("ripup", [])]
        if ripup:
            eng.call("ripup", nets=ripup)
        order = [int(n) for n in plan.get("order", [])]
        modes = plan.get("modes") or {}
        layers = plan.get("layers") or {}
        log = []
        done = set()
        for n in order:
            if n in done:
                continue
            done.add(n)
            r = eng.call("route", net=n, mode=int(modes.get(str(n), plan.get("mode", 2))),
                         layers=layers.get(str(n)))
            log.append(r)
        # second chance for anything still open, with the other router mode
        st = eng.call("status", detail=False)
        for item in st["open_nets"]:
            r = eng.call("route", net=item["net"], mode=1 if plan.get("mode", 2) == 2 else 2)
            log.append(r)
        # layer changes: for anything STILL open, search via sites (kept only if the rule check stays clean)
        if plan.get("vias", True):
            st = eng.call("status", detail=False)
            for item in st["open_nets"][:8]:
                r = eng.call("route_via", net=item["net"], mode=int(plan.get("mode", 2)),
                             order=(plan.get("via_order") or {}).get(str(item["net"])),
                             max_trials=int(plan.get("via_trials", 120)), timeout=500)
                r["via"] = True
                log.append(r)
        out = out_dir / f"{board_id}__{tag}.kicad_pcb"
        eng.call("save", path=str(out))
        st1 = eng.call("status", detail=False)
        score = eng.call("score", path=str(out), project=str(f["pro"]))
        rec.update(start_status={k: st0[k] for k in ("unrouted", "drc_errors", "clean")},
                   end_status={k: st1[k] for k in ("unrouted", "drc_errors", "clean")},
                   score=score, saved=str(out), route_log=log, ok=True)
    except Exception as e:
        rec.update(ok=False, error=str(e))
    finally:
        eng.close()
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


def rank_key(rec: dict):
    s = rec.get("score") or {}
    return (not s.get("clean_pass", False), not s.get("success", False),
            (rec.get("end_status") or {}).get("drc_errors", 99) + 10 * (rec.get("end_status") or {}).get("unrouted", 99),
            -(s.get("potential_gain") or -1e9))


# --------------------------------------------------------------------------- agents

PLAN_SCHEMA = {"type": "object", "properties": {
    "diagnosis": {"type": "string"},
    "plans": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"},
        "ripup": {"type": "array", "items": {"type": "integer"},
                  "description": "net codes whose existing copper is deleted first (blockers and broken nets)"},
        "order": {"type": "array", "items": {"type": "integer"},
                  "description": "net codes to route, in order; include every ripped-up net and every open net"},
        "mode": {"type": "integer", "description": "default router mode: 1 = shove other copper aside, 2 = walk around it"},
        "why": {"type": "string"}},
        "required": ["name", "ripup", "order", "mode", "why"]}}},
    "required": ["diagnosis", "plans"]}


def _client():
    from sizing import managed
    return managed.client()


def describe(st: dict, nets: dict, max_nets: int = 40) -> str:
    names = {int(k): v for k, v in nets["names"].items()}
    lines = [f"Board: {nets['layers']} copper layers. Now: {st['unrouted']} open connections, "
             f"{st['drc_errors']} rule errors, {st['tracks']} tracks, {st['vias']} vias."]
    lines.append("OPEN NETS (net code, name, pad groups still separate):")
    for n in st["open_nets"][:max_nets]:
        lines.append(f"  {n['net']} {n['name']!r} groups={n['pad_groups']}")
    if st.get("errors_per_net"):
        lines.append("RULE ERRORS PER NET: " + json.dumps(st["errors_per_net"]))
    if st.get("violations"):
        lines.append("RULE VIOLATIONS (first 25): " + json.dumps(st["violations"][:25])[:3500])
    if st.get("ratsnest"):
        lines.append("OPEN CONNECTION ENDPOINTS (mm): " + json.dumps(st["ratsnest"][:40])[:3500])
    lines.append("NET NAMES: " + json.dumps({k: v for k, v in list(names.items())[:120]})[:3000])
    return "\n".join(lines)


def llm_plans(board_id: str, st: dict, nets: dict, image: Path, history: list[dict], n_plans: int,
              model: str, thinking: str, log_dir: Path, triage: dict | None = None) -> list[dict]:
    c = _client()
    hist = "\n".join(
        f"- plan {h['plan'].get('name')}: ripup={h['plan'].get('ripup')} order={h['plan'].get('order')} "
        f"mode={h['plan'].get('mode')} -> open={h.get('end_status', {}).get('unrouted')} "
        f"errors={h.get('end_status', {}).get('drc_errors')} clean={h.get('score', {}).get('clean_pass')}"
        f" still-open-after={[r['net'] for r in h.get('route_log', []) if r.get('open_left')][:10]}"
        for h in history[-12:]) or "(none yet)"
    tri = ("BLOCKER SCREEN (a yes/no model looked at the picture; p = probability that removing that net is "
           "needed to complete the open connection):\n" + json.dumps(triage)[:2500]) if triage else ""
    prompt = f"""You are a PCB layout engineer repairing an auto-routed board. A classic autorouter left it with
open connections and/or design-rule errors. You cannot draw copper yourself. You choose:
which nets to RIP UP (delete their copper), the ORDER in which nets are re-routed, and the router MODE
(1 = shove neighbouring copper aside, 2 = walk around it). KiCad's interactive router then completes each
net automatically, and KiCad's rule checker scores the result. A net that blocks an open connection must be
ripped up and re-routed AFTER the net it was blocking; rip up as little as possible.

The attached image shows the board: copper by layer (red = layer 1, blue = layer 2), pads (squares),
open connections (orange dashed, labelled with the net name), rule errors (red X). Coordinates in mm.

{describe(st, nets)}

{tri}

ATTEMPTS SO FAR ON THIS BOARD:
{hist}

Propose exactly {n_plans} DIFFERENT repair plans (different rip-up sets, orders or modes; do not repeat a
plan that already failed). Use net CODES (integers)."""
    blocks = [{"type": "text", "text": prompt}]
    if image and image.exists():
        blocks.append({"type": "image", "mime_type": "image/png",
                       "data": base64.b64encode(image.read_bytes()).decode()})
    t0 = time.time()
    gate = CARBON_SLOTS if model == PLANNER else None
    if gate:
        gate.acquire()
    try:
        r = c.interactions.create(model=model, input=blocks, generation_config={"thinking_level": thinking},
                                  response_format={"type": "text", "mime_type": "application/json",
                                                   "schema": PLAN_SCHEMA},
                                  timeout=120 if model == PLANNER else 75)
    finally:
        if gate:
            gate.release()
    out = json.loads(r.output_text)
    with open(log_dir / "llm_calls.jsonl", "a") as f:
        f.write(json.dumps({"board": board_id, "model": model, "latency_s": round(time.time() - t0, 1),
                            "tokens": getattr(r.usage, "total_tokens", None), "diagnosis": out.get("diagnosis"),
                            "plans": out.get("plans")}) + "\n")
    valid = {int(k) for k in nets["names"]}
    plans = []
    out["_model"] = model
    for p in out.get("plans", [])[:n_plans]:
        p["ripup"] = [int(n) for n in p.get("ripup", []) if int(n) in valid]
        p["order"] = [int(n) for n in p.get("order", []) if int(n) in valid]
        p["mode"] = 1 if int(p.get("mode", 2)) == 1 else 2
        p["by"] = model
        plans.append(p)
    return plans


# --------------------------------------------------------------------------- per-board loop

def repair_board(board_id: str, start: Path, a) -> dict:
    out_dir = OUT / a.run_id / board_id
    out_dir.mkdir(parents=True, exist_ok=True)
    live = Live(OUT / a.run_id / "live" / board_id)
    live.event("phase", name="load", text="Opening the classic router's board in KiCad's engine and measuring "
                                          "what it left unfinished")
    f = board_files(board_id)
    eng = Engine()
    try:
        st = eng.call("open", board=str(start), project=str(f["pro"]))
        nets = eng.call("nets")
        img = out_dir / "start.png"
        eng.call("render", path=str(img))
        block = eng.call("blockers") if not st["clean"] else {"open_connections": []}
        s0 = out_dir / f"{board_id}__start.kicad_pcb"
        eng.call("save", path=str(s0))
        base_score = eng.call("score", path=str(s0), project=str(f["pro"]))
    finally:
        eng.close()
    live.publish(s0, "router")                 # contract: the classic router's board comes first
    live.event("note", agent="router", text=f"classic router left {st['unrouted']} open connection(s) and "
                                            f"{st['drc_errors']} rule error(s)")
    history: list[dict] = [{"board": board_id, "tag": "start", "plan": {"name": "start (classic router output)"},
                            "end_status": {k: st[k] for k in ("unrouted", "drc_errors", "clean")},
                            "score": base_score, "saved": str(s0), "ok": True}]
    result = {"board": board_id, "start": {"unrouted": st["unrouted"], "drc_errors": st["drc_errors"],
                                           "clean_pass": base_score.get("clean_pass")}}
    attempts = 0
    triage = {}
    names = nets.get("names", {})

    def net_names(codes, cap=8) -> list:
        return [names.get(str(n), str(n)) for n in (codes or [])][:cap]

    def think(agent: str, text: str = "") -> None:      # a model is working (text) or has finished ("")
        live.event("think", agent=agent, text=text)
    (out_dir / "blockers.json").write_text(json.dumps(block, indent=1))
    if not base_score.get("clean_pass"):
        if not a.no_llm:
            live.event("phase", name="screen", text="Gemini's Decisions API looks at a picture of the board and "
                                                    "rates which tracks are in the way of each open connection")
            think("decision-1", "reading the board picture: which tracks block each open connection?")
            try:
                triage = decisions_blockers(board_id, block, img, out_dir)
                (out_dir / "triage.json").write_text(json.dumps(triage, indent=1))
                top = sorted(((b_["p_blocks"], b_["name"]) for bl in triage.values() for b_ in bl), reverse=True)[:3]
                if top:
                    live.event("note", agent="decision-1", text="blocker screen: " + ", ".join(
                        f"{n} ({p:.0%})" for p, n in top))
            except Exception as e:
                print(f"  [{board_id}] decisions screen failed: {str(e)[:140]}", flush=True)
            think("decision-1")
        via_order = {}
        if not a.no_llm and not a.no_vision:
            think("decision-1", "ranking where a stuck connection could change layers (via sites), from the picture")
            try:
                via_order = vision_via_order(board_id, start, st, out_dir)
            except Exception as e:
                print(f"  [{board_id}] vision via-site step failed: {str(e)[:140]}", flush=True)
            think("decision-1")
        rounds = [("scripted", [] if a.skip_scripted else scripted_plans(st))]
        open_now = [x["net"] for x in st["open_nets"]]
        if via_order:       # same repair, but the router tries via sites in the order the vision model ranked them
            rounds[0][1].append({"name": "vias-vision-ranked", "ripup": [], "order": open_now, "mode": 2,
                                 "vias": True, "via_order": via_order, "by": DECIDER + " (vision)"})
        if a.no_llm:        # control for the vision step: a random site order
            import random as _r
            rng = _r.Random(a.seed)
            rounds[0][1].append({"name": "vias-random-order", "ripup": [], "order": open_now, "mode": 2, "vias": True,
                                 "via_order": {str(n): rng.sample(range(36), 36) for n in open_now}, "by": "random"})
        if triage:      # rip the nets the screen is most sure about
            sure = sorted({b["blocker"] for bl in triage.values() for b in bl if b["p_blocks"] >= 0.6})
            open_nets = [x["net"] for x in st["open_nets"]]
            if sure:
                rounds[0][1].append({"name": "rip-screened-blockers", "ripup": sure, "order": open_nets + sure,
                                     "mode": 2, "by": DECIDER})
        for rnd in range(a.rounds):
            rounds.append((f"llm{rnd}", None))
        if a.no_llm:        # equal-attempt control: scripted plans topped up with random rip-up plans
            rounds.append(("random", random_plans(st, block, max(0, a.max_attempts - len(rounds[0][1])), a.seed)))
        carbon_pool = ThreadPoolExecutor(1)
        carbon_fut = None
        for name, plans in rounds:
            if any(h.get("score", {}).get("clean_pass") for h in history):
                break
            if plans is None:
                if a.no_llm:
                    continue
                plans = []
                if carbon_fut is not None and carbon_fut.done():      # a Carbon plan that landed late joins now
                    try:
                        plans += carbon_fut.result()
                    except Exception as e:
                        print(f"  [{board_id}] carbon plan failed: {str(e)[:100]}", flush=True)
                    carbon_fut = None
                live.event("phase", name="plan", text="Gemini agents read the board picture and every attempt so far, "
                                                      "then write new repair plans")
                if carbon_fut is None:
                    think("carbon", "writing 2 careful repair plans (slower; joins the next round when ready)")
                    carbon_fut = carbon_pool.submit(llm_plans, board_id, st, nets, img, list(history), 2, PLANNER,
                                                    "medium", out_dir, triage)
                think("hydrogen", f"writing {a.workers} repair plans from the picture and the {len(history)} "
                                  f"attempt(s) so far")
                try:
                    plans += llm_plans(board_id, st, nets, img, history, a.workers, WORKER, "medium", out_dir, triage)
                except Exception as e:
                    print(f"  [{board_id}] hydrogen plans failed: {str(e)[:120]}", flush=True)
                think("hydrogen")
                if carbon_fut.done():
                    try:
                        plans += carbon_fut.result()
                    except Exception as e:
                        print(f"  [{board_id}] carbon plan failed: {str(e)[:100]}", flush=True)
                    carbon_fut = None
                for p_ in plans:             # agent plans inherit the vision-ranked via sites
                    p_.setdefault("via_order", via_order)
                seen = {json.dumps([h["plan"].get("ripup"), h["plan"].get("order"), h["plan"].get("mode")])
                        for h in history}
                plans = [p for p in plans
                         if json.dumps([p.get("ripup"), p.get("order"), p.get("mode")]) not in seen]
            plans = plans[: a.max_attempts - attempts]
            if not plans:
                continue
            live.event("round", n=len([h for h in history if h.get("tag", "").endswith("_0")]) + 1, name=name,
                       plans=len(plans))
            for p_ in plans:
                ag = agent_name({"plan": p_})
                if ag == "carbon":
                    think("carbon")
                live.event("start", agent=ag, net=p_.get("name", ""), nets=net_names(p_.get("order")),
                           ripup=net_names(p_.get("ripup")), mode=p_.get("mode"), vias=bool(p_.get("vias")),
                           why=str(p_.get("why", ""))[:200])
            with ThreadPoolExecutor(a.parallel) as ex:
                recs = list(ex.map(lambda ip: execute(board_id, start, ip[1], out_dir, f"{name}_{ip[0]}"),
                                   enumerate(plans)))
            for r in recs:
                attempts += 1
                prev_best = sorted([h for h in history if h.get("ok")], key=rank_key)[0]
                history.append(r)
                is_best = bool(r.get("ok")) and rank_key(r) < rank_key(prev_best)
                if is_best and r.get("saved"):
                    live.publish(r["saved"], agent_name(r))
                live.event("attempt", agent=agent_name(r), plan=r["plan"].get("name", ""),
                           open=r.get("end_status", {}).get("unrouted"),
                           errors=r.get("end_status", {}).get("drc_errors"),
                           clean=bool((r.get("score") or {}).get("clean_pass")), seconds=r.get("seconds"),
                           best=is_best, why=str(r["plan"].get("why", ""))[:200])
                s = r.get("score") or {}
                print(f"  [{board_id}] {r['tag']:14s} {r['plan'].get('name', '')[:34]:34s} "
                      f"open={r.get('end_status', {}).get('unrouted')} err={r.get('end_status', {}).get('drc_errors')} "
                      f"clean={s.get('clean_pass')} {r.get('seconds')}s {r.get('error', '')[:80]}", flush=True)
        if not a.no_llm:
            think("carbon")
    ok = [h for h in history if h.get("ok")]
    best = sorted(ok, key=rank_key)[0]
    final = OUT / a.run_id / "submitted" / f"{board_id}.kicad_pcb"
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(best["saved"], final)
    pro_src = Path(best["saved"]).with_suffix(".kicad_pro")
    if pro_src.exists():
        shutil.copy(pro_src, final.with_suffix(".kicad_pro"))
    try:   # picture of the submitted board for the stage
        e2 = Engine()
        e2.call("open", board=str(final), project=str(f["pro"]))
        e2.call("render", path=str(out_dir / "final.png"))
        e2.close()
    except Exception as e:
        print(f"  [{board_id}] final render failed: {str(e)[:100]}", flush=True)
    left = []
    try:
        for r_ in (best.get("route_log") or []):
            if r_.get("open_left"):
                left.append({"net": r_.get("name") or str(r_.get("net"))})
    except Exception:
        pass
    live.result(bool((best.get("score") or {}).get("clean_pass")), attempts, left)
    result.update(attempts=attempts, best_tag=best["tag"], best_plan=best["plan"], best_agent=agent_name(best),
                  final=best.get("score"), submitted=str(final))
    (out_dir / "history.json").write_text(json.dumps(history, indent=1, default=str))
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boards", nargs="+", default=["hard"], help="'hard' | 'medium' | 'dev' | board ids")
    ap.add_argument("--start", type=Path, required=True,
                    help="folder of starting boards (classic router output), files <board_id>*.kicad_pcb")
    ap.add_argument("--run-id", default=time.strftime("pcb_%H%M%S"))
    ap.add_argument("--no-llm", action="store_true", help="scripted repair only (control arm)")
    ap.add_argument("--rounds", type=int, default=2, help="LLM planning rounds per board")
    ap.add_argument("--workers", type=int, default=3, help="Hydrogen plans per round (plus 2 Carbon plans)")
    ap.add_argument("--max-attempts", type=int, default=16)
    ap.add_argument("--parallel", type=int, default=4, help="engine processes per board")
    ap.add_argument("--boards-parallel", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--skip-scripted", action="store_true", help="testing: go straight to the agents")
    ap.add_argument("--no-vision", action="store_true", help="ablation: no vision ranking of via sites")
    a = ap.parse_args()
    ids_file = DATA / "subset_work" / "ids.json"
    ids = json.loads(ids_file.read_text()) if ids_file.exists() else {}
    boards = []
    for b in a.boards:
        boards += {"hard": ids.get("hard_test", []), "medium": ids.get("medium_test", []),
                   "dev": ids.get("hard_dev", [])}.get(b, [b])
    start_dir = a.start if a.start.is_absolute() else PCBW / a.start
    jobs = []
    for b in boards:
        cands = sorted(start_dir.glob(f"{b}*.kicad_pcb"))
        if not cands:
            print(f"no starting board for {b} in {start_dir}")
            continue
        jobs.append((b, cands[0]))
    (OUT / a.run_id).mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with ThreadPoolExecutor(a.boards_parallel) as ex:
        results = list(ex.map(lambda j: repair_board(j[0], j[1], a), jobs))
    n = len(results)
    start_clean = sum(bool(r["start"]["clean_pass"]) for r in results)
    final_clean = sum(bool((r.get("final") or {}).get("clean_pass")) for r in results)
    summary = {"run_id": a.run_id, "boards": n, "start_clean": start_clean, "final_clean": final_clean,
               "start_rate": start_clean / max(n, 1), "final_rate": final_clean / max(n, 1),
               "llm": not a.no_llm, "total_attempts": sum(r["attempts"] for r in results),
               "wall_s": round(time.time() - t0, 1), "results": results}
    (OUT / a.run_id / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    for r in results:
        f = r.get("final") or {}
        print(f"{r['board'][:50]:50s} start: open={r['start']['unrouted']} err={r['start']['drc_errors']} "
              f"clean={r['start']['clean_pass']} -> final clean={f.get('clean_pass')} "
              f"({r['best_tag']}, {r['attempts']} attempts)")
    print(f"\nclean pass: {start_clean}/{n} -> {final_clean}/{n}   ({summary['wall_s']} s)")


if __name__ == "__main__":
    main()
