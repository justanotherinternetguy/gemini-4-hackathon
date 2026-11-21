"""Gemini fixes what KiCad's rule checker found, on the spot.

The studio (pcb/view3d.py) starts this automatically when the newest version of a board still has
rule violations (shorts, clearance errors) or open connections. It works on that exact board file:

  screen  gemini-decision-1   for every pair of nets that break a rule together: which of the two
                              should be ripped up and routed again? (choice, calibrated probability)
  agents  gemini-4-hydrogen   N agents in parallel, each on its OWN copy of the board, each with a
                              temperament. One action per step: reroute (rip up + route again),
                              ripup, route, route_via, restore_best, stop. They see the grouped
                              violations, the open connections, the screen's hints, the board
                              picture and their own history.
  judge   KiCad               the engine's rule check after every step; the studio re-checks every
                              published version with kicad-cli.

The agents never draw copper: KiCad's push-and-shove router does. Every strict improvement is
published into --out as fix<round>_<seq>__<agent>.kicad_pcb (pcb/CONTRACT.md), with events.jsonl
and result.json, so the studio animates it like any other version. The first clean board stops
every agent.

    .venv/bin/python -m pcb.repair.fix_violations --board /abs/board.kicad_pcb \
        --project /abs/board.kicad_pro --out /abs/watch/folder --name BOARD
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pcb.repair.swarm import DECIDER, WORKER, Engine, Live, _client

TEMPERAMENTS = [
    ("careful", "Move the SMALLER net of each conflicting pair first (fewer pads, shorter copper). Re-route it "
                "with walk-around (mode 2). If that leaves it open, restore_best and try the other net."),
    ("decisive", "Follow the Decisions API hint for each pair. Re-route the net it names; use shove (mode 1) "
                 "when walk-around leaves a connection open, and a via (route_via) when one layer is full."),
    ("power-aware", "Supply and ground nets are long and hard to re-route: prefer moving the signal net. Only "
                    "move a supply net when the signal net cannot be re-routed cleanly."),
    ("layer-first", "Assume the clash is because both nets want the same layer. Re-route one net, and if it "
                    "stays open use route_via to finish it on the other layer."),
]

ACTION_SCHEMA = {"type": "object", "properties": {
    "thought": {"type": "string", "description": "one or two sentences: what is wrong and why this action"},
    "action": {"type": "string", "enum": ["reroute", "ripup", "route", "route_via", "restore_best", "stop"]},
    "nets": {"type": "array", "items": {"type": "integer"}, "description": "net codes the action applies to"},
    "mode": {"type": "integer", "description": "router mode for reroute/route/route_via: 1 = shove, 2 = walk around"}},
    "required": ["thought", "action", "nets", "mode"]}


class Publisher(Live):
    """Live, with file names that cannot collide with the versions the router already saved."""

    def __init__(self, folder: Path, round_no: int, start: tuple):
        super().__init__(folder)
        self.round = round_no
        self.best = tuple(start)                 # best (errors, open) across ALL agents

    def offer(self, board_file, agent: str, h: tuple) -> bool:
        """Publish only a board better than every version so far, so the newest is always the best."""
        with self.lock:
            if tuple(h) >= self.best:
                return False
            self.best = tuple(h)
        self.publish(board_file, agent)
        return True

    def publish(self, board_file, agent: str) -> str:
        with self.lock:
            name = f"fix{self.round}_{self.seq:03d}__{agent}.kicad_pcb"
            self.seq += 1
        tmp = self.dir / (name + ".tmp")
        shutil.copy(board_file, tmp)
        os.replace(tmp, self.dir / name)
        return name


def observe(eng: Engine, names: dict, sizes: dict) -> dict:
    """The board as the agents see it: violations grouped by rule and net pair, plus open connections."""
    st = eng.call("status", detail=True)
    code = {v: int(k) for k, v in names.items()}
    groups: dict = {}
    for v in st.get("violations") or []:
        rule = str(v.get("error_type") or "rule error")
        if "nconnected" in rule:                  # open connections are listed separately below
            continue
        ns = tuple(sorted(set(v.get("net_names") or [])))
        g = groups.setdefault((rule, ns), {
            "rule": rule, "count": 0,
            "nets": [{"net": code.get(n), "name": n, "pads_in_net": sizes.get(code.get(n))} for n in ns],
            "near_mm": [round(float(v.get("x_mm") or 0), 1), round(float(v.get("y_mm") or 0), 1)]})
        g["count"] += 1
    conns = []
    if st["open_nets"]:
        for oc in eng.call("blockers")["open_connections"][:10]:
            conns.append({"net": oc["net"], "name": oc["name"], "pads_in_net": sizes.get(oc["net"]),
                          "from": oc["from"], "to": oc["to"],
                          "crossed_by": [{"net": c["net"], "name": c["name"], "pads_in_net": sizes.get(c["net"])}
                                         for c in oc["candidates"]]})
    return {"errors": int(st["drc_errors"]), "open": int(st["unrouted"]), "clean": bool(st["clean"]),
            "violations": list(groups.values()), "open_connections": conns}


def health(obs: dict) -> tuple:
    return (obs["errors"], obs["open"])          # the engine's error count includes open connections


def screen_pairs(c, obs: dict, image: Path | None, log: Path) -> list[dict]:
    """Decisions API: for each conflicting net pair, which net should move? One call for all pairs."""
    pairs = [g for g in obs["violations"] if len(g["nets"]) == 2 and all(n["net"] for n in g["nets"])][:12]
    if not pairs:
        return []
    blocks = [{"type": "text", "text": "Routed two-layer printed circuit board. Image: red = top copper, blue = "
               "bottom copper, squares = pads, red X = design-rule errors. Coordinates are millimetres."}]
    if image is not None and image.exists():
        blocks.append({"type": "image", "mime_type": "image/png", "resolution": "high",
                       "data": base64.b64encode(image.read_bytes()).decode()})
    qs = {}
    for i, g in enumerate(pairs):
        a, b = g["nets"]
        qs[f"q{i}"] = {"type": "choice", "instructions":
                       f"Nets '{a['name']}' and '{b['name']}' break the rule '{g['rule']}' together "
                       f"{g['count']} time(s), near ({g['near_mm'][0]}, {g['near_mm'][1]}) mm. To fix it, ONE of "
                       "them will have all its copper removed and be routed again by KiCad's router, which keeps "
                       "legal clearance. Which net should be moved so the board ends with no errors and nothing "
                       "left unconnected?",
                       "criteria": {"a": f"move '{a['name']}' ({a['pads_in_net']} pads)",
                                    "b": f"move '{b['name']}' ({b['pads_in_net']} pads)"}}
    t0 = time.time()
    res = c.decisions.create(model=DECIDER, input=blocks, questions=qs)
    hints = []
    for i, g in enumerate(pairs):
        ans = res.answers[f"q{i}"]
        probs = dict(ans.probabilities) if isinstance(getattr(ans, "probabilities", None), dict) else {}
        pa = float(probs.get("a", 0.5))
        move, keep = (g["nets"][0], g["nets"][1]) if pa >= 0.5 else (g["nets"][1], g["nets"][0])
        hints.append({"move": move["net"], "move_name": move["name"], "keep_name": keep["name"],
                      "p": round(max(pa, 1 - pa), 3), "rule": g["rule"], "count": g["count"]})
    with open(log, "a") as f:
        f.write(json.dumps({"step": "screen", "model": DECIDER, "latency_s": round(time.time() - t0, 2),
                            "hints": hints}) + "\n")
    return hints


def ask_agent(c, temperament, obs, hints, steps, image, steps_left, log) -> dict:
    name, style = temperament
    hist = "\n".join(f"  {i + 1}. {s['action']} nets={s['nets']} mode={s.get('mode')} -> errors {s['before']}"
                     f"->{s['after']}, open {s['open_after']}" + (f" | {s['detail']}" if s.get("detail") else "")
                     for i, s in enumerate(steps[-10:]))
    hint_txt = "\n".join(f"  {h['rule']} x{h['count']}: move '{h['move_name']}' (net {h['move']}), keep "
                         f"'{h['keep_name']}'  p={h['p']}" for h in hints) or "  (none)"
    prompt = f"""You are fixing a routed printed circuit board, one action at a time. KiCad's design-rule checker
found the problems below. KiCad's interactive router executes your actions and the rule checker reports the
result after each one. You never draw copper yourself.

GOAL: zero rule violations and zero open connections. A net you rip up loses all its copper and MUST be routed
again. To clear a short or clearance error between two nets, re-route ONE of them: the router keeps legal
clearance when it routes.

ACTIONS (one per step):
  reroute    nets=[..]  rip up these nets, then route each again in the order given (mode 1 shove / 2 walk around)
  ripup      nets=[..]  only delete the copper of these nets
  route      nets=[n]   complete the open connections of net n
  route_via  nets=[n]   complete net n by changing layers through a via (or a two-via bridge)
  restore_best          go back to the best board seen so far
  stop                  nothing more can be gained

YOUR STYLE ({name}): {style}

DECISIONS API HINTS (which net of each conflicting pair to move, with probability):
{hint_txt}

CURRENT BOARD: {obs['errors']} rule error(s) in total, of which {obs['open']} are open connections.
RULE VIOLATIONS grouped by rule and net pair (pads_in_net: big nets are usually power or ground):
{json.dumps(obs['violations'], indent=0)[:4000]}
OPEN CONNECTIONS and nets crossing their straight path:
{json.dumps(obs['open_connections'], indent=0)[:3000]}

YOUR LAST STEPS:
{hist or '  (none yet)'}

Steps left: {steps_left}. Do not repeat an action that just failed in the same state. Reply with one action."""
    blocks = [{"type": "text", "text": prompt}]
    if image is not None and image.exists():
        blocks.append({"type": "image", "mime_type": "image/png", "data": base64.b64encode(image.read_bytes()).decode()})
    t0 = time.time()
    r = c.interactions.create(model=WORKER, input=blocks, generation_config={"thinking_level": "low"},
                              response_format={"type": "text", "mime_type": "application/json", "schema": ACTION_SCHEMA},
                              timeout=60)
    out = json.loads(r.output_text)
    with open(log, "a") as f:
        f.write(json.dumps({"agent": name, "model": WORKER, "latency_s": round(time.time() - t0, 1),
                            "tokens": getattr(r.usage, "total_tokens", None), "decision": out}) + "\n")
    return out


def run_agent(i: int, a, hints: list, image: Path, live: Publisher, done: threading.Event, deadline: float) -> dict:
    temperament = TEMPERAMENTS[i % len(TEMPERAMENTS)]
    tag = f"hydrogen-{temperament[0]}"
    work = Path(a.out).parent / f"fix{a.round}_work" / tag
    work.mkdir(parents=True, exist_ok=True)
    log = work / "llm_calls.jsonl"
    eng, c = Engine(), _client()
    rec = {"agent": tag, "steps": []}
    try:
        eng.call("open", board=a.board, project=a.project or None)
        nets = eng.call("nets")
        names = nets["names"]
        sizes = {int(k): len(v) for k, v in nets["pads"].items()}
        valid = {int(k) for k in names}
        obs = observe(eng, names, sizes)
        best, best_h = health(obs), eng.call("checkpoint")["handle"]
        rec["start"] = best
        steps = rec["steps"]
        for step in range(a.steps):
            if obs["clean"] or done.is_set() or time.time() > deadline:
                break
            live.event("think", agent=tag, text=f"step {step + 1}: deciding the next action from KiCad's report "
                                               f"({obs['errors'] - obs['open']} violation(s), {obs['open']} open)")
            try:
                act = ask_agent(c, temperament, obs, hints, steps, image if step == 0 else None, a.steps - step, log)
            except Exception as e:
                steps.append({"action": "error", "nets": [], "before": obs["errors"], "after": obs["errors"],
                              "open_after": obs["open"], "detail": f"model call failed: {str(e)[:80]}"})
                continue
            kind = act.get("action")
            ns = [int(n) for n in act.get("nets") or [] if str(n).lstrip("-").isdigit() and int(n) in valid][:3]
            mode = 1 if int(act.get("mode") or 2) == 1 else 2
            before, detail = obs["errors"], ""
            if kind == "stop":
                steps.append({"action": "stop", "nets": [], "before": before, "after": before, "open_after": obs["open"],
                              "thought": act.get("thought", "")})
                break
            live.event("start", agent=tag, net=f"{kind} {', '.join(names.get(str(n), str(n)) for n in ns)}".strip(),
                       nets=[names.get(str(n), str(n)) for n in ns], why=str(act.get("thought", ""))[:200],
                       step=step + 1, of=a.steps)
            try:
                if kind == "restore_best":
                    eng.call("restore", handle=best_h)
                elif kind in ("reroute", "ripup") and ns:
                    eng.call("ripup", nets=ns)
                    if kind == "reroute":
                        made = [eng.call("route", net=n, mode=mode) for n in ns]
                        detail = "; ".join(f"{m['name']}: made {m['connections_made']}, left {m['open_left']}" for m in made)
                elif kind == "route" and ns:
                    r = eng.call("route", net=ns[0], mode=mode)
                    detail = f"made {r['connections_made']}, left {r['open_left']}"
                elif kind == "route_via" and ns:
                    r = eng.call("route_via", net=ns[0], mode=mode, max_trials=90, timeout=300)
                    detail = f"made {r['connections_made']}, left {r['open_left']}"
                else:
                    detail = "no valid net given"
            except Exception as e:
                detail = f"tool error: {str(e)[:80]}"
            obs = observe(eng, names, sizes)
            steps.append({"action": kind, "nets": ns, "mode": mode, "before": before, "after": obs["errors"],
                          "open_after": obs["open"], "detail": detail, "thought": str(act.get("thought", ""))[:240]})
            if health(obs) < best:
                best, best_h = health(obs), eng.call("checkpoint")["handle"]
                snap = work / f"best_s{step}.kicad_pcb"
                eng.call("save", path=str(snap))
                if live.offer(snap, tag, best):
                        live.event("note", agent=tag, text=f"{obs['errors'] - obs['open']} violation(s), {obs['open']} "
                                                       f"open - {str(act.get('thought', ''))[:140]}")
                if obs["clean"]:
                    done.set()
        eng.call("restore", handle=best_h)
        final = work / "final.kicad_pcb"
        eng.call("save", path=str(final))
        rec.update(ok=True, best=best, saved=str(final), clean=best == (0, 0))
    except Exception as e:
        rec.update(ok=False, error=str(e)[:200])
    finally:
        eng.close()
    b = rec.get("best") or (None, None)
    live.event("attempt", agent=tag, plan=f"{len(rec['steps'])} steps", open=b[1],
               errors=None if b[0] is None else b[0] - b[1], clean=bool(rec.get("clean")), best=bool(rec.get("clean")))
    print(f"  [{tag}] {len(rec['steps'])} steps -> {b} {rec.get('error', '')}", flush=True)
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", required=True, help="absolute path of the board version to fix")
    ap.add_argument("--project", default="", help="its .kicad_pro (design rules); '' for KiCad's defaults")
    ap.add_argument("--out", required=True, help="folder the studio watches")
    ap.add_argument("--name", default="board")
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--agents", type=int, default=3)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--wall", type=float, default=300, help="seconds before the agents stop starting new steps")
    a = ap.parse_args()
    a.board = str(Path(a.board).resolve())
    a.project = str(Path(a.project).resolve()) if a.project else ""
    out = Path(a.out)
    work = out.parent / f"fix{a.round}_work"
    work.mkdir(parents=True, exist_ok=True)

    eng = Engine()
    try:
        eng.call("open", board=a.board, project=a.project or None)
        nets = eng.call("nets")
        sizes = {int(k): len(v) for k, v in nets["pads"].items()}
        obs = observe(eng, nets["names"], sizes)
        image = work / "start.png"
        eng.call("render", path=str(image))
    finally:
        eng.close()
    live = Publisher(out, a.round, health(obs))
    n_viol = obs["errors"] - obs["open"]
    print(f"[fix] {a.name}: {n_viol} violation(s), {obs['open']} open", flush=True)
    if obs["clean"]:
        live.result(True, 0, [])
        return
    live.event("phase", name="fix", text=f"KiCad's rule checker still flags {n_viol} violation(s) and {obs['open']} "
                                         f"open connection(s): Gemini agents fix that exact board")
    live.event("note", agent="gemini", text=f"Gemini is fixing {n_viol} rule violation(s) and {obs['open']} open "
                                            f"connection(s) found by the rule checker")
    hints: list = []
    live.event("think", agent="decision", text="reading the board picture: for each clashing pair, which net should move?")
    try:
        hints = screen_pairs(_client(), obs, image, work / "llm_calls.jsonl")
        for h in hints:
            live.event("note", agent="decision", text=f"{h['rule']}: move {h['move_name']}, keep {h['keep_name']} "
                                                      f"({round(100 * h['p'])}%)")
    except Exception as e:
        print(f"[fix] Decisions API screen failed: {str(e)[:120]}", flush=True)
    live.event("think", agent="decision", text="")

    done, deadline = threading.Event(), time.time() + a.wall
    with ThreadPoolExecutor(a.agents) as ex:
        recs = list(ex.map(lambda i: run_agent(i, a, hints, image, live, done, deadline), range(a.agents)))
    ok = [r for r in recs if r.get("ok") and r.get("best")]
    best = min(ok, key=lambda r: tuple(r["best"])) if ok else None
    left = [] if best and best.get("clean") else [{"net": g["nets"][0]["name"] if g["nets"] else "",
                                                   "reason": g["rule"]} for g in obs["violations"]][:20]
    live.result(bool(best and best.get("clean")), sum(len(r.get("steps", [])) for r in recs), left)
    (work / "summary.json").write_text(json.dumps({"start": [obs["errors"], obs["open"]], "hints": hints,
                                                   "agents": recs}, indent=1, default=str))
    print(f"[fix] best {best and best['best']} clean={bool(best and best.get('clean'))}", flush=True)


if __name__ == "__main__":
    main()
