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

    def call(self, op: str, **kw):
        self.p.stdin.write(json.dumps({"op": op, **kw}) + "\n")
        self.p.stdin.flush()
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


def board_files(board_id: str) -> dict:
    d = SORTED / board_id
    return {"unrouted": d / f"{STEM}_unrouted.kicad_pcb", "pro": d / f"{STEM}.kicad_pro"}


# --------------------------------------------------------------------------- plans

def scripted_plans(st: dict) -> list[dict]:
    """Model-free repair plans: the control arm, and the seeds the agents improve on."""
    open_nets = [n["net"] for n in st["open_nets"]]
    err_nets = sorted({int(k) for k in (st.get("errors_per_net") or {}) if str(k).lstrip("-").isdigit()})
    plans = [
        {"name": "finish-open-walkaround", "ripup": [], "order": open_nets, "mode": 2},
        {"name": "finish-open-shove", "ripup": [], "order": open_nets, "mode": 1},
    ]
    if err_nets:
        plans.append({"name": "rip-error-nets", "ripup": err_nets, "order": err_nets + open_nets, "mode": 2})
        plans.append({"name": "rip-error-nets-shove", "ripup": err_nets, "order": open_nets + err_nets, "mode": 1})
    if open_nets:
        plans.append({"name": "rip-open-nets", "ripup": open_nets, "order": open_nets, "mode": 2})
    return plans


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
              model: str, thinking: str, log_dir: Path) -> list[dict]:
    c = _client()
    hist = "\n".join(
        f"- plan {h['plan'].get('name')}: ripup={h['plan'].get('ripup')} order={h['plan'].get('order')} "
        f"mode={h['plan'].get('mode')} -> open={h.get('end_status', {}).get('unrouted')} "
        f"errors={h.get('end_status', {}).get('drc_errors')} clean={h.get('score', {}).get('clean_pass')}"
        for h in history[-12:]) or "(none yet)"
    prompt = f"""You are a PCB layout engineer repairing an auto-routed board. A classic autorouter left it with
open connections and/or design-rule errors. You cannot draw copper yourself. You choose:
which nets to RIP UP (delete their copper), the ORDER in which nets are re-routed, and the router MODE
(1 = shove neighbouring copper aside, 2 = walk around it). KiCad's interactive router then completes each
net automatically, and KiCad's rule checker scores the result. A net that blocks an open connection must be
ripped up and re-routed AFTER the net it was blocking; rip up as little as possible.

The attached image shows the board: copper by layer (red = layer 1, blue = layer 2), pads (squares),
open connections (orange dashed, labelled with the net name), rule errors (red X). Coordinates in mm.

{describe(st, nets)}

ATTEMPTS SO FAR ON THIS BOARD:
{hist}

Propose exactly {n_plans} DIFFERENT repair plans (different rip-up sets, orders or modes; do not repeat a
plan that already failed). Use net CODES (integers)."""
    blocks = [{"type": "text", "text": prompt}]
    if image and image.exists():
        blocks.append({"type": "image", "mime_type": "image/png",
                       "data": base64.b64encode(image.read_bytes()).decode()})
    t0 = time.time()
    r = c.interactions.create(model=model, input=blocks, generation_config={"thinking_level": thinking},
                              response_format={"type": "text", "mime_type": "application/json", "schema": PLAN_SCHEMA},
                              timeout=150)
    out = json.loads(r.output_text)
    with open(log_dir / "llm_calls.jsonl", "a") as f:
        f.write(json.dumps({"board": board_id, "model": model, "latency_s": round(time.time() - t0, 1),
                            "tokens": getattr(r.usage, "total_tokens", None), "diagnosis": out.get("diagnosis"),
                            "plans": out.get("plans")}) + "\n")
    valid = {int(k) for k in nets["names"]}
    plans = []
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
    live = OUT / a.run_id / "live" / board_id
    live.mkdir(parents=True, exist_ok=True)
    f = board_files(board_id)
    eng = Engine()
    try:
        st = eng.call("open", board=str(start), project=str(f["pro"]))
        nets = eng.call("nets")
        img = out_dir / "start.png"
        eng.call("render", path=str(img))
        s0 = out_dir / f"{board_id}__start.kicad_pcb"
        eng.call("save", path=str(s0))
        base_score = eng.call("score", path=str(s0), project=str(f["pro"]))
    finally:
        eng.close()
    history: list[dict] = [{"board": board_id, "tag": "start", "plan": {"name": "start (classic router output)"},
                            "end_status": {k: st[k] for k in ("unrouted", "drc_errors", "clean")},
                            "score": base_score, "saved": str(s0), "ok": True}]
    result = {"board": board_id, "start": {"unrouted": st["unrouted"], "drc_errors": st["drc_errors"],
                                           "clean_pass": base_score.get("clean_pass")}}
    attempts = 0
    if not base_score.get("clean_pass"):
        rounds = [("scripted", scripted_plans(st))]
        for rnd in range(a.rounds):
            rounds.append((f"llm{rnd}", None))
        for name, plans in rounds:
            if any(h.get("score", {}).get("clean_pass") for h in history):
                break
            if plans is None:
                if a.no_llm:
                    break
                plans = []
                try:
                    with ThreadPoolExecutor(2) as ex:
                        fut_c = ex.submit(llm_plans, board_id, st, nets, img, history, 2, PLANNER, "medium", out_dir)
                        fut_h = ex.submit(llm_plans, board_id, st, nets, img, history, a.workers, WORKER, "medium", out_dir)
                        for fut in (fut_h, fut_c):
                            try:
                                plans += fut.result(timeout=170)
                            except Exception as e:
                                print(f"  [{board_id}] planner failed: {str(e)[:120]}", flush=True)
                except Exception as e:
                    print(f"  [{board_id}] planning error: {e}", flush=True)
            plans = plans[: a.max_attempts - attempts]
            if not plans:
                continue
            with ThreadPoolExecutor(a.parallel) as ex:
                recs = list(ex.map(lambda ip: execute(board_id, start, ip[1], out_dir, f"{name}_{ip[0]}"),
                                   enumerate(plans)))
            for r in recs:
                attempts += 1
                history.append(r)
                s = r.get("score") or {}
                print(f"  [{board_id}] {r['tag']:14s} {r['plan'].get('name', '')[:34]:34s} "
                      f"open={r.get('end_status', {}).get('unrouted')} err={r.get('end_status', {}).get('drc_errors')} "
                      f"clean={s.get('clean_pass')} {r.get('seconds')}s {r.get('error', '')[:80]}", flush=True)
            best = sorted([h for h in history if h.get("ok")], key=rank_key)[0]
            if best.get("saved"):
                shutil.copy(best["saved"], live / f"{int(time.time())}_{best['tag']}.kicad_pcb")
    ok = [h for h in history if h.get("ok")]
    best = sorted(ok, key=rank_key)[0]
    final = OUT / a.run_id / "submitted" / f"{board_id}.kicad_pcb"
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(best["saved"], final)
    pro_src = Path(best["saved"]).with_suffix(".kicad_pro")
    if pro_src.exists():
        shutil.copy(pro_src, final.with_suffix(".kicad_pro"))
    result.update(attempts=attempts, best_tag=best["tag"], best_plan=best["plan"],
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
