"""Step-by-step repair agents for PCB routing (PCBWorld), with rollback.

Whole-plan repair stalls on near-miss boards: ripping a blocker frees one connection, then the
blocker cannot be re-routed, and the plan has no way to react. Here an agent acts ONE STEP AT A
TIME on a live board and sees fresh facts after every step:

    observe (open connections, what blocks each, what the last action changed)
      -> choose one action: ripup | route | route_via | restore_best | stop
      -> KiCad's router executes it; the rule checker reports the new state
      -> the best state so far is checkpointed and can be restored

Several agents with different temperaments work the same board in separate engine processes
(nobody shares a board); the judge keeps the best. A random policy with the same actions and the
same number of steps is the control.

  agents  gemini-4-hydrogen (one decision per step, ~8 s)
  lead    gemini-4-carbon   (optional strategy note from the board picture, off the critical path)
  judge   PCBWorld engine + its own scorer on the saved board

.venv/bin/python -m pcb.repair.stepper --boards <ids> --start <folder> --agents 3 --steps 22 --run-id NAME
.venv/bin/python -m pcb.repair.stepper --boards <ids> --start <folder> --policy random --agents 3 --steps 22
"""
from __future__ import annotations

import argparse
import base64
import json
import random
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pcb.repair.swarm import (DATA, OUT, PCBW, PLANNER, WORKER, Engine, Live, _client, board_files,
                              rank_key)

TEMPERAMENTS = [
    ("careful", "Prefer the smallest change: finish open connections, try a via bridge, and only then rip up "
                "ONE small signal net that blocks. Restore the best state as soon as things get worse."),
    ("bold", "Clear space decisively: rip up the nets that wall off an open connection (even two at once), "
             "route the starved connection first, then re-route what you removed, using via bridges freely."),
    ("layer-first", "Assume the problem is a single-layer wall: try route_via on every open connection before "
                    "ripping anything; if a via bridge fails, rip the net that crosses the path the most and retry."),
    ("power-aware", "Never rip up ground or supply nets with many pads if a small signal net can move instead. "
                    "Route power connections first after any rip-up, signals last."),
]

ACTION_SCHEMA = {"type": "object", "properties": {
    "thought": {"type": "string", "description": "one or two sentences: what is stuck and why this action"},
    "action": {"type": "string", "enum": ["ripup", "route", "route_via", "restore_best", "stop"]},
    "nets": {"type": "array", "items": {"type": "integer"}, "description": "net codes the action applies to"},
    "mode": {"type": "integer", "description": "router mode for route/route_via: 1 = shove, 2 = walk around"}},
    "required": ["thought", "action", "nets", "mode"]}


def observe(eng: Engine, nets: dict) -> dict:
    st = eng.call("status", detail=False)
    block = eng.call("blockers") if st["open_nets"] else {"open_connections": []}
    sizes = {int(k): len(v) for k, v in nets["pads"].items()}
    conns = []
    for oc in block["open_connections"][:14]:
        conns.append({"net": oc["net"], "name": oc["name"], "pads_in_net": sizes.get(oc["net"]),
                      "from": oc["from"], "to": oc["to"], "length_mm": oc["length_mm"],
                      "crossed_by": [{"net": c["net"], "name": c["name"], "crossings": c["crossings"],
                                      "layers": c["layers"], "pads_in_net": sizes.get(c["net"])}
                                     for c in oc["candidates"]]})
    return {"open": st["unrouted"], "errors_not_open": st["drc_errors"] - st["unrouted"], "vias": st["vias"],
            "clean": st["clean"], "open_nets": [(n["net"], n["name"]) for n in st["open_nets"]],
            "connections": conns}


def health(obs: dict) -> tuple:
    return (obs["open"] + max(0, obs["errors_not_open"]), obs["open"])


def agent_policy(c, temperament, board_id, obs, steps, note, image: Path | None, log: Path, steps_left: int):
    name, style = temperament
    hist = "\n".join(f"  {i + 1}. {s['action']} nets={s['nets']} mode={s.get('mode')} -> open {s['before']}"
                     f"->{s['after']}, extra rule errors {s['err_after']}"
                     + (f" | {s['detail']}" if s.get("detail") else "") for i, s in enumerate(steps[-10:]))
    prompt = f"""You are repairing an auto-routed printed circuit board, one action at a time. A classic autorouter
finished most of it; a few connections are still open. KiCad's interactive router executes your actions and
KiCad's rule checker reports the result. You never draw copper yourself.

GOAL: zero open connections and zero rule errors. A net you rip up becomes open and MUST be routed again.

ACTIONS (one per step):
  route      nets=[n]  complete the open connections of net n on its current layer (mode 1 shove / 2 walk around)
  route_via  nets=[n]  complete net n by changing layers: tries candidate via sites, including a two-via bridge;
                       kept only if it closes a connection without a new rule error
  ripup      nets=[..] delete the copper of these nets (use on nets that wall off an open connection)
  restore_best         go back to the best board seen so far (fewest open connections and errors)
  stop                 nothing more can be gained

YOUR STYLE ({name}): {style}
{('LEAD ENGINEER NOTE: ' + note) if note else ''}

CURRENT BOARD: {obs['open']} open connection(s), {obs['errors_not_open']} other rule error(s), {obs['vias']} vias.
OPEN CONNECTIONS and the nets whose copper crosses the straight path between their two ends
(pads_in_net tells you how big a net is; big nets are usually power or ground):
{json.dumps(obs['connections'], indent=0)[:5000]}

YOUR LAST STEPS (open connections before -> after):
{hist or '  (none yet)'}

Steps left: {steps_left}. Do not repeat an action that just failed in the same state. Reply with one action."""
    blocks = [{"type": "text", "text": prompt}]
    if image is not None and image.exists():
        blocks.append({"type": "image", "mime_type": "image/png",
                       "data": base64.b64encode(image.read_bytes()).decode()})
    t0 = time.time()
    r = c.interactions.create(model=WORKER, input=blocks, generation_config={"thinking_level": "low"},
                              response_format={"type": "text", "mime_type": "application/json",
                                               "schema": ACTION_SCHEMA}, timeout=60)
    out = json.loads(r.output_text)
    with open(log, "a") as f:
        f.write(json.dumps({"board": board_id, "agent": name, "model": WORKER, "latency_s": round(time.time() - t0, 1),
                            "tokens": getattr(r.usage, "total_tokens", None), "decision": out}) + "\n")
    return out


def random_policy(rng: random.Random, obs: dict) -> dict:
    open_nets = [n for n, _ in obs["open_nets"]]
    cands = sorted({c["net"] for oc in obs["connections"] for c in oc["crossed_by"]})
    kinds = ["route", "route", "route_via", "route_via", "restore_best"] + (["ripup", "ripup"] if cands else [])
    k = rng.choice(kinds)
    if k == "ripup":
        return {"thought": "random", "action": "ripup", "nets": rng.sample(cands, rng.randint(1, min(2, len(cands)))),
                "mode": 2}
    if k == "restore_best" or not open_nets:
        return {"thought": "random", "action": "restore_best", "nets": [], "mode": 2}
    return {"thought": "random", "action": k, "nets": [rng.choice(open_nets)], "mode": rng.choice([1, 2])}


def lead_note(board_id: str, obs: dict, image: Path, log: Path) -> str:
    """One Carbon call per board: a strategy note from the board picture. Never blocks the agents."""
    c = _client()
    r = c.interactions.create(
        model=PLANNER, generation_config={"thinking_level": "medium"}, timeout=110,
        input=[{"type": "text", "text":
                "Auto-routed PCB (image: red = top copper, blue = bottom copper, squares = pads, orange dashed = "
                "connections still open, red X = rule errors). Open connections and the nets crossing their "
                f"paths: {json.dumps(obs['connections'])[:3500]}\nIn at most 90 words, tell the repair agents "
                "which nets to move out of the way first, which to leave alone, and where a layer change (via "
                "bridge) is the right tool. Be specific with net names."},
               {"type": "image", "mime_type": "image/png", "data": base64.b64encode(image.read_bytes()).decode()}])
    note = (r.output_text or "").strip()
    with open(log, "a") as f:
        f.write(json.dumps({"board": board_id, "agent": "lead", "model": PLANNER, "note": note}) + "\n")
    return note


def work_board(board_id: str, start: Path, agent_idx: int, a, live: Live, note_box: dict) -> dict:
    """One agent (or one random walker) on its own copy of the board."""
    out_dir = OUT / a.run_id / board_id
    out_dir.mkdir(parents=True, exist_ok=True)
    f = board_files(board_id)
    name = TEMPERAMENTS[agent_idx % len(TEMPERAMENTS)][0] if a.policy == "agent" else f"random{agent_idx}"
    eng = Engine()
    rng = random.Random(a.seed * 100 + agent_idx)
    c = _client() if a.policy == "agent" else None
    steps, rec = [], {"board": board_id, "agent": name, "policy": a.policy}
    t0 = time.time()
    try:
        eng.call("open", board=str(start), project=str(f["pro"]))
        nets = eng.call("nets")
        obs = observe(eng, nets)
        best, best_h = health(obs), eng.call("checkpoint")["handle"]
        rec["start"] = {"open": obs["open"], "errors_not_open": obs["errors_not_open"]}
        img = out_dir / "start.png"
        for step in range(a.steps):
            if obs["clean"]:
                break
            try:
                if a.policy == "agent":
                    act = agent_policy(c, TEMPERAMENTS[agent_idx % len(TEMPERAMENTS)], board_id, obs, steps,
                                       note_box.get("note", ""), img if step == 0 else None,
                                       out_dir / "llm_calls.jsonl", a.steps - step)
                else:
                    act = random_policy(rng, obs)
            except Exception as e:
                steps.append({"action": "error", "nets": [], "before": obs["open"], "after": obs["open"],
                              "err_after": obs["errors_not_open"], "detail": str(e)[:80]})
                continue
            kind, ns, mode = act.get("action"), [int(n) for n in act.get("nets") or []], int(act.get("mode") or 2)
            mode = 1 if mode == 1 else 2
            before, detail = obs["open"], ""
            valid = {int(k) for k in nets["names"]}
            ns = [n for n in ns if n in valid]
            try:
                if kind == "stop":
                    steps.append({"action": "stop", "nets": [], "before": before, "after": before,
                                  "err_after": obs["errors_not_open"], "thought": act.get("thought", "")})
                    break
                if kind == "restore_best":
                    eng.call("restore", handle=best_h)
                elif kind == "ripup" and ns:
                    eng.call("ripup", nets=ns[:3])
                elif kind == "route" and ns:
                    r = eng.call("route", net=ns[0], mode=mode)
                    detail = f"made {r['connections_made']}, left {r['open_left']}"
                elif kind == "route_via" and ns:
                    r = eng.call("route_via", net=ns[0], mode=mode, max_trials=90, timeout=400)
                    detail = f"made {r['connections_made']}, left {r['open_left']} in {r['trials']} trials"
                else:
                    detail = "no valid net given"
            except Exception as e:
                detail = f"tool error: {str(e)[:70]}"
            obs = observe(eng, nets)
            steps.append({"action": kind, "nets": ns[:3], "mode": mode, "before": before, "after": obs["open"],
                          "err_after": obs["errors_not_open"], "detail": detail,
                          "thought": str(act.get("thought", ""))[:240]})
            live.event("start", agent=name, net=f"{kind} {ns[:3]}")
            if health(obs) < best:
                best = health(obs)
                best_h = eng.call("checkpoint")["handle"]
                tmp = out_dir / f"{board_id}__{name}_s{step}.kicad_pcb"
                eng.call("save", path=str(tmp))
                live.publish(tmp, name)
                live.event("note", agent=name, text=f"{kind} {ns[:3]}: now {obs['open']} open - "
                                                    f"{str(act.get('thought', ''))[:120]}")
        eng.call("restore", handle=best_h)
        final = out_dir / f"{board_id}__{name}_final.kicad_pcb"
        eng.call("save", path=str(final))
        end = observe(eng, nets)
        score = eng.call("score", path=str(final), project=str(f["pro"]))
        rec.update(ok=True, saved=str(final), score=score, steps=steps, n_steps=len(steps),
                   end_status={"unrouted": end["open"], "drc_errors": end["open"] + end["errors_not_open"],
                               "clean": end["clean"]},
                   plan={"name": f"step agent {name}", "by": WORKER if a.policy == "agent" else "random"},
                   tag=name)
    except Exception as e:
        rec.update(ok=False, error=str(e)[:200], steps=steps)
    finally:
        eng.close()
    rec["seconds"] = round(time.time() - t0, 1)
    live.event("attempt", agent=name, plan=f"{len(steps)} steps", open=(rec.get("end_status") or {}).get("unrouted"),
               errors=(rec.get("end_status") or {}).get("drc_errors"),
               clean=bool((rec.get("score") or {}).get("clean_pass")), seconds=rec["seconds"], best=False)
    s = rec.get("score") or {}
    print(f"  [{board_id[:28]}] {name:12s} {rec.get('n_steps', 0):2d} steps -> open={(rec.get('end_status') or {}).get('unrouted')} "
          f"clean={s.get('clean_pass')} {rec['seconds']}s {rec.get('error', '')}", flush=True)
    return rec


def repair_board(board_id: str, start: Path, a) -> dict:
    out_dir = OUT / a.run_id / board_id
    out_dir.mkdir(parents=True, exist_ok=True)
    live = Live(OUT / a.run_id / "live" / board_id)
    f = board_files(board_id)
    eng = Engine()
    try:
        st = eng.call("open", board=str(start), project=str(f["pro"]))
        nets = eng.call("nets")
        obs0 = observe(eng, nets)
        eng.call("render", path=str(out_dir / "start.png"))
        s0 = out_dir / f"{board_id}__start.kicad_pcb"
        eng.call("save", path=str(s0))
        base = eng.call("score", path=str(s0), project=str(f["pro"]))
    finally:
        eng.close()
    live.publish(s0, "router")
    live.event("note", agent="router", text=f"classic router left {st['unrouted']} open connection(s)")
    note_box: dict = {}
    if a.policy == "agent" and not a.no_lead:
        def _lead():
            try:
                note_box["note"] = lead_note(board_id, obs0, out_dir / "start.png", out_dir / "llm_calls.jsonl")
                live.event("note", agent="carbon", text=note_box["note"][:300])
            except Exception as e:
                print(f"  [{board_id[:28]}] lead note failed: {str(e)[:80]}", flush=True)
        threading.Thread(target=_lead, daemon=True).start()
    start_rec = {"board": board_id, "tag": "start", "plan": {"name": "start (classic router output)"},
                 "end_status": {"unrouted": st["unrouted"], "drc_errors": st["drc_errors"], "clean": st["clean"]},
                 "score": base, "saved": str(s0), "ok": True}
    recs = [start_rec]
    if not base.get("clean_pass"):
        with ThreadPoolExecutor(a.agents) as ex:
            recs += list(ex.map(lambda i: work_board(board_id, start, i, a, live, note_box), range(a.agents)))
    ok = [r for r in recs if r.get("ok")]
    best = sorted(ok, key=rank_key)[0]
    final = OUT / a.run_id / "submitted" / f"{board_id}.kicad_pcb"
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(best["saved"], final)
    pro = Path(best["saved"]).with_suffix(".kicad_pro")
    if pro.exists():
        shutil.copy(pro, final.with_suffix(".kicad_pro"))
    if best is not start_rec:
        live.publish(best["saved"], best.get("agent", "agent"))
    live.result(bool((best.get("score") or {}).get("clean_pass")), sum(r.get("n_steps", 0) for r in recs), [])
    try:
        e2 = Engine()
        e2.call("open", board=str(final), project=str(f["pro"]))
        e2.call("render", path=str(out_dir / "final.png"))
        e2.close()
    except Exception:
        pass
    (out_dir / "history.json").write_text(json.dumps(recs, indent=1, default=str))
    return {"board": board_id, "start": {"unrouted": st["unrouted"], "drc_errors": st["drc_errors"],
                                         "clean_pass": base.get("clean_pass")},
            "final": best.get("score"), "attempts": len(recs) - 1, "best_tag": best.get("tag"),
            "best_plan": best.get("plan"), "best_agent": best.get("agent", "router"),
            "total_steps": sum(r.get("n_steps", 0) for r in recs),
            "per_agent": [{"agent": r.get("agent"), "open": (r.get("end_status") or {}).get("unrouted"),
                           "clean": (r.get("score") or {}).get("clean_pass"), "steps": r.get("n_steps")}
                          for r in recs[1:]],
            "submitted": str(final)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boards", nargs="+", required=True)
    ap.add_argument("--start", type=Path, required=True)
    ap.add_argument("--policy", default="agent", choices=["agent", "random"])
    ap.add_argument("--agents", type=int, default=3)
    ap.add_argument("--steps", type=int, default=22)
    ap.add_argument("--run-id", default=time.strftime("step_%H%M%S"))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-lead", action="store_true")
    ap.add_argument("--boards-parallel", type=int, default=4)
    a = ap.parse_args()
    start_dir = a.start if a.start.is_absolute() else PCBW / a.start
    jobs = []
    for b in a.boards:
        cands = sorted(start_dir.glob(f"{b}*.kicad_pcb"))
        if cands:
            jobs.append((b, cands[0]))
        else:
            print(f"no starting board for {b}")
    (OUT / a.run_id).mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with ThreadPoolExecutor(a.boards_parallel) as ex:
        results = list(ex.map(lambda j: repair_board(j[0], j[1], a), jobs))
    n = len(results)
    sc, fc = (sum(bool(r["start"]["clean_pass"]) for r in results),
              sum(bool((r.get("final") or {}).get("clean_pass")) for r in results))
    summary = {"run_id": a.run_id, "method": f"step {a.policy}", "boards": n, "start_clean": sc, "final_clean": fc,
               "agents_per_board": a.agents, "steps_per_agent": a.steps, "wall_s": round(time.time() - t0, 1),
               "total_attempts": sum(r["attempts"] for r in results), "results": results}
    (OUT / a.run_id / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    for r in results:
        fsc = r.get("final") or {}
        print(f"{r['board'][:44]:44s} start open={r['start']['unrouted']} -> clean={fsc.get('clean_pass')} "
              f"routability={round(fsc.get('routability') or 0, 3)} best={r['best_agent']} | {r['per_agent']}")
    print(f"\n{a.policy}: clean {sc}/{n} -> {fc}/{n}  ({summary['wall_s']} s)")


if __name__ == "__main__":
    main()
