"""Proposer + orchestrator swarm for PCB routing repair (PCBWorld).

A two-tier crew working ONE shared board state per round:

  PROPOSERS      many cheap agents (gemini-4-hydrogen, low thinking), each with a temperament.
                 Each proposes ONE next action for the current board, with its reason and what it
                 expects to happen. They do not see each other's proposals.
  SCREEN         gemini-decision-1 answers one yes/no question per proposal ("will this reduce the
                 open connections without breaking a routed net?") with a calibrated probability.
                 Sub-second; it is also the fallback ranking if the orchestrators are slow.
  ORCHESTRATORS  two stronger agents that DISCUSS the proposals before anything is executed:
                   reviewer (gemini-4-neon, looks at the board picture): which proposals conflict,
                     which are redundant, which risk tearing up a net that cannot be re-routed;
                   chief (gemini-4-carbon): reads the proposals, the screen's probabilities and the
                     reviewer's critique, then SELECTS up to K proposals to try, may MERGE or EDIT
                     them (e.g. "rip 19, then route 5 before re-routing 19"), and writes guidance
                     for the next round.
                 Both are bounded by timeouts; if they do not answer, the screen's top-K is used,
                 so the loop never waits on a slow model.
  EXECUTORS      each selected action sequence runs on its OWN COPY of the current board, in its
                 own engine process, in parallel (KiCad's router draws; the rule checker reports).
  JUDGE          the child with the fewest open connections + rule errors becomes the next shared
                 state; if no child is better, the state is kept (implicit rollback) and the
                 orchestrators are told the round stalled. PCBWorld's scorer grades the final board.

Controls / ablations: --no-orchestrators (screen ranking only), --no-screen, --policy random.
Publishes the run in the solver/viewer contract (pcb/CONTRACT.md).

.venv/bin/python -m pcb.repair.council --boards <ids> --start <folder of classic-router boards> \
    --proposers 6 --keep 3 --rounds 8 --run-id NAME
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
from concurrent.futures import TimeoutError as FutTimeout
from pathlib import Path

from pcb.repair.stepper import TEMPERAMENTS, health, observe
from pcb.repair.swarm import (DECIDER, OUT, PCBW, PLANNER, WORKER, Engine, Live, _client, board_files)

REVIEWER = "gemini-4-neon"
EXTRA_TEMPERAMENTS = [
    ("minimalist", "Change as little copper as possible; never rip up more than one net per step."),
    ("unblocker", "Find the single net that crosses the most open connections and move it out of the way."),
    ("finisher", "Only complete connections (route / route_via); never rip anything up."),
    ("re-router", "If a net was ripped up earlier and is still open, get it back first before anything else."),
]
ALL_TEMPERAMENTS = TEMPERAMENTS + EXTRA_TEMPERAMENTS

STEP = {"type": "object", "properties": {
    "action": {"type": "string", "enum": ["ripup", "route", "route_via"]},
    "nets": {"type": "array", "items": {"type": "integer"}},
    "mode": {"type": "integer", "description": "1 = shove, 2 = walk around"}},
    "required": ["action", "nets", "mode"]}
PROPOSAL_SCHEMA = {"type": "object", "properties": {
    "reason": {"type": "string", "description": "what is stuck and why this helps, one or two sentences"},
    "expect": {"type": "string", "description": "what you expect to be open afterwards"},
    "steps": {"type": "array", "minItems": 1, "maxItems": 4, "items": STEP,
              "description": "a short sequence executed in order on a copy of the board"}},
    "required": ["reason", "expect", "steps"]}
REVIEW_SCHEMA = {"type": "object", "properties": {
    "critique": {"type": "string", "description": "at most 80 words: conflicts, redundancy, risks"},
    "risky": {"type": "array", "items": {"type": "integer"}, "description": "proposal indices likely to make it worse"},
    "promising": {"type": "array", "items": {"type": "integer"}}},
    "required": ["critique", "risky", "promising"]}
DECISION_SCHEMA = {"type": "object", "properties": {
    "discussion": {"type": "string", "description": "at most 80 words: your reply to the reviewer and your choice"},
    "selected": {"type": "array", "description": "what to execute this round, most promising first",
                 "items": {"type": "object", "properties": {
                     "from_proposals": {"type": "array", "items": {"type": "integer"},
                                        "description": "indices of the proposals this is taken or merged from"},
                     "steps": {"type": "array", "minItems": 1, "maxItems": 6, "items": STEP},
                     "why": {"type": "string"}},
                     "required": ["from_proposals", "steps", "why"]}},
    "guidance": {"type": "string", "description": "one sentence of direction for the proposers next round"}},
    "required": ["discussion", "selected", "guidance"]}


# --------------------------------------------------------------------------- model calls

def _json_call(c, model: str, blocks: list, schema: dict, thinking: str, timeout: float) -> dict:
    r = c.interactions.create(model=model, input=blocks, generation_config={"thinking_level": thinking},
                              response_format={"type": "text", "mime_type": "application/json", "schema": schema},
                              timeout=timeout)
    out = json.loads(r.output_text)
    out["_tokens"] = getattr(r.usage, "total_tokens", None)
    return out


def _state_text(obs: dict, history: list[dict]) -> str:
    hist = "\n".join(f"  round {h['round']}: executed {h['executed']} -> open {h['before']}->{h['after']}"
                     f"{' (kept)' if h['improved'] else ' (no child was better; state unchanged)'}"
                     for h in history[-6:]) or "  (first round)"
    return (f"CURRENT BOARD: {obs['open']} open connection(s), {obs['errors_not_open']} other rule error(s).\n"
            "OPEN CONNECTIONS and the nets whose copper crosses the straight path between their ends "
            "(pads_in_net = size of the net; big nets are usually power or ground):\n"
            f"{json.dumps(obs['connections'])[:4500]}\n\nROUNDS SO FAR:\n{hist}")


RULES = ("ACTIONS: route nets=[n] (complete net n's open connections on its layer; mode 1 shove, 2 walk around) | "
         "route_via nets=[n] (complete net n with a layer change: one via or a two-via bridge, kept only if no new "
         "rule error) | ripup nets=[..] (delete those nets' copper; a ripped net becomes open and must be routed "
         "again). GOAL: zero open connections and zero rule errors.")


def propose(c, idx: int, obs: dict, history: list[dict], guidance: str, log: Path) -> dict | None:
    name, style = ALL_TEMPERAMENTS[idx % len(ALL_TEMPERAMENTS)]
    prompt = (f"You are one of several junior engineers repairing an auto-routed printed circuit board. You each "
              f"propose ONE short action sequence; senior engineers choose which to try. KiCad's router executes "
              f"it; you never draw copper.\n{RULES}\n\nYOUR STYLE ({name}): {style}\n"
              f"{('SENIOR GUIDANCE: ' + guidance) if guidance else ''}\n\n{_state_text(obs, history)}\n\n"
              "Propose 1 to 4 steps.")
    t0 = time.time()
    try:
        out = _json_call(c, WORKER, [{"type": "text", "text": prompt}], PROPOSAL_SCHEMA, "low", 45)
    except Exception as e:
        return None
    out.update(proposer=name, idx=idx)
    with open(log, "a") as f:
        f.write(json.dumps({"role": "proposer", "agent": name, "model": WORKER,
                            "latency_s": round(time.time() - t0, 1), "proposal": out}) + "\n")
    return out


def screen(c, proposals: list[dict], obs: dict, image: Path | None, log: Path) -> list[float]:
    """Decisions API: P(this proposal reduces the open connections without breaking a routed net)."""
    blocks = [{"type": "text", "text": _state_text(obs, []) + "\n" + RULES}]
    if image and image.exists():
        blocks.append({"type": "image", "mime_type": "image/png", "resolution": "high",
                       "data": base64.b64encode(image.read_bytes()).decode()})
    qs = {f"p{i}": {"type": "noul",
                    "instructions": f"Proposal: {json.dumps(p['steps'])}. Reason given: {p['reason'][:300]}. Will "
                                    "executing it leave FEWER open connections than now, without leaving a "
                                    "ripped-up net unrouted?",
                    "criteria": {"true_description": "Yes, the board ends with fewer open connections.",
                                 "false_description": "No: it fails, or it trades one open connection for another."}}
          for i, p in enumerate(proposals)}
    t0 = time.time()
    res = c.decisions.create(model=DECIDER, input=blocks, questions=qs)
    probs = [float(res.answers[f"p{i}"].noul) for i in range(len(proposals))]
    with open(log, "a") as f:
        f.write(json.dumps({"role": "screen", "model": DECIDER, "latency_s": round(time.time() - t0, 2),
                            "probabilities": [round(p, 3) for p in probs]}) + "\n")
    return probs


def _proposal_digest(proposals: list[dict], probs: list[float] | None) -> str:
    return "\n".join(f"  [{i}] by {p['proposer']}: steps={json.dumps(p['steps'])} | reason: {p['reason'][:220]}"
                     + (f" | screen P(success)={probs[i]:.2f}" if probs else "")
                     for i, p in enumerate(proposals))


def review(c, proposals, probs, obs, history, image: Path | None, log: Path, timeout: float) -> dict:
    """Reviewer (Neon, vision): critique before anything runs."""
    blocks = [{"type": "text", "text":
               "You are the reviewing engineer on a PCB repair team. Junior engineers proposed the action "
               f"sequences below; nothing has been executed yet.\n{RULES}\n\n{_state_text(obs, history)}\n\n"
               f"PROPOSALS:\n{_proposal_digest(proposals, probs)}\n\nThe attached picture shows the board (red = top "
               "copper, blue = bottom copper, squares = pads, orange dashed = open connections, red X = rule errors). "
               "Critique them: which conflict or duplicate each other, which would rip up a net that probably cannot "
               "be re-routed, which look right given the free space you can see."}]
    if image and image.exists():
        blocks.append({"type": "image", "mime_type": "image/png",
                       "data": base64.b64encode(image.read_bytes()).decode()})
    t0 = time.time()
    out = _json_call(c, REVIEWER, blocks, REVIEW_SCHEMA, "low", timeout)
    with open(log, "a") as f:
        f.write(json.dumps({"role": "reviewer", "model": REVIEWER, "latency_s": round(time.time() - t0, 1),
                            "review": out}) + "\n")
    return out


def decide(c, proposals, probs, critique: dict | None, obs, history, keep: int, log: Path, timeout: float) -> dict:
    """Chief (Carbon): replies to the reviewer, selects / merges / edits, sets guidance."""
    prompt = (f"You are the chief engineer of a PCB repair team. {RULES}\n\n{_state_text(obs, history)}\n\n"
              f"PROPOSALS from the junior engineers:\n{_proposal_digest(proposals, probs)}\n\n"
              f"REVIEWER'S CRITIQUE: {json.dumps(critique) if critique else '(reviewer did not answer in time)'}\n\n"
              f"Select up to {keep} action sequences to execute this round, each on its own copy of the board. You may "
              "take a proposal as is, EDIT it, or MERGE several into one sequence (for example: rip up a blocker, "
              "route the starved connection, then re-route the blocker with a via bridge). Prefer sequences that end "
              "with every ripped-up net routed again. Then give one sentence of guidance for the next round.")
    t0 = time.time()
    out = _json_call(c, PLANNER, [{"type": "text", "text": prompt}], DECISION_SCHEMA, "medium", timeout)
    with open(log, "a") as f:
        f.write(json.dumps({"role": "chief", "model": PLANNER, "latency_s": round(time.time() - t0, 1),
                            "decision": out}) + "\n")
    return out


# --------------------------------------------------------------------------- execution

def run_child(board_id: str, state_file: Path, steps: list[dict], out_file: Path) -> dict:
    """Execute one action sequence on a COPY of the current board, in its own engine process."""
    f = board_files(board_id)
    eng = Engine()
    log = []
    try:
        eng.call("open", board=str(state_file), project=str(f["pro"]))
        nets = eng.call("nets")
        valid = {int(k) for k in nets["names"]}
        for s in steps[:6]:
            ns = [int(n) for n in s.get("nets") or [] if int(n) in valid]
            mode = 1 if int(s.get("mode") or 2) == 1 else 2
            try:
                if s["action"] == "ripup" and ns:
                    eng.call("ripup", nets=ns[:3])
                    log.append(f"ripup {ns[:3]}")
                elif s["action"] == "route" and ns:
                    r = eng.call("route", net=ns[0], mode=mode)
                    log.append(f"route {ns[0]}: made {r['connections_made']}, left {r['open_left']}")
                elif s["action"] == "route_via" and ns:
                    r = eng.call("route_via", net=ns[0], mode=mode, max_trials=90, timeout=400)
                    log.append(f"route_via {ns[0]}: made {r['connections_made']}, left {r['open_left']}")
            except Exception as e:
                log.append(f"{s.get('action')} error: {str(e)[:60]}")
        obs = observe(eng, nets)
        eng.call("save", path=str(out_file))
        return {"ok": True, "obs": obs, "health": health(obs), "saved": str(out_file), "log": log}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "log": log}
    finally:
        eng.close()


def council_board(board_id: str, start: Path, a) -> dict:
    out_dir = OUT / a.run_id / board_id
    out_dir.mkdir(parents=True, exist_ok=True)
    live = Live(OUT / a.run_id / "live" / board_id)
    log = out_dir / "council.jsonl"
    f = board_files(board_id)
    c = None if a.policy == "random" else _client()
    rng = random.Random(a.seed)
    # starting state
    eng = Engine()
    try:
        st = eng.call("open", board=str(start), project=str(f["pro"]))
        nets = eng.call("nets")
        obs = observe(eng, nets)
        state = out_dir / "state_r0.kicad_pcb"
        eng.call("save", path=str(state))
        eng.call("render", path=str(out_dir / "start.png"))
        base = eng.call("score", path=str(state), project=str(f["pro"]))
    finally:
        eng.close()
    live.publish(state, "router")
    live.event("note", agent="router", text=f"classic router left {obs['open']} open connection(s)")
    image = out_dir / "start.png"
    history, guidance, executed_total = [], "", 0
    t0 = time.time()
    for rnd in range(1, a.rounds + 1):
        if obs["clean"] or time.time() - t0 > a.wall:
            break
        live.event("round", n=rnd)
        # 1. proposers (parallel)
        if a.policy == "random":
            open_nets = [n for n, _ in obs["open_nets"]]
            cands = sorted({x["net"] for oc in obs["connections"] for x in oc["crossed_by"]})
            proposals = []
            for i in range(a.proposers):
                steps = []
                if cands and rng.random() < 0.5:
                    steps.append({"action": "ripup", "nets": [rng.choice(cands)], "mode": 2})
                if open_nets:
                    steps.append({"action": rng.choice(["route", "route_via"]), "nets": [rng.choice(open_nets)],
                                  "mode": rng.choice([1, 2])})
                proposals.append({"proposer": f"random{i}", "idx": i, "reason": "random", "expect": "", "steps": steps})
        else:
            with ThreadPoolExecutor(a.proposers) as ex:
                proposals = [p for p in ex.map(lambda i: propose(c, i, obs, history, guidance, log),
                                               range(a.proposers)) if p and p.get("steps")]
        # de-duplicate identical sequences
        seen, uniq = set(), []
        for p in proposals:
            key = json.dumps(p["steps"], sort_keys=True)
            if key not in seen:
                seen.add(key)
                uniq.append(p)
        proposals = uniq
        if not proposals:
            history.append({"round": rnd, "executed": [], "before": obs["open"], "after": obs["open"], "improved": False})
            continue
        for p in proposals:
            live.event("start", agent=p["proposer"], net=json.dumps(p["steps"])[:120])
        # 2. screen (Decisions API) - also the fallback ranking
        probs = None
        if c is not None and not a.no_screen:
            try:
                probs = screen(c, proposals, obs, image, log)
            except Exception as e:
                print(f"  [{board_id[:24]}] r{rnd} screen failed: {str(e)[:80]}", flush=True)
        order = sorted(range(len(proposals)), key=lambda i: -(probs[i] if probs else rng.random()))
        selected = [{"from_proposals": [i], "steps": proposals[i]["steps"], "why": "screen ranking",
                     "credit": proposals[i]["proposer"]} for i in order[: a.keep]]
        # 3. orchestrators discuss (bounded; never block the loop)
        if c is not None and not a.no_orchestrators:
            pool = ThreadPoolExecutor(1)
            try:
                critique = None
                try:
                    critique = pool.submit(review, c, proposals, probs, obs, history, image, log,
                                           a.review_timeout).result(timeout=a.review_timeout + 5)
                    live.event("note", agent="neon", text=str(critique.get("critique", ""))[:300])
                except (FutTimeout, Exception) as e:
                    print(f"  [{board_id[:24]}] r{rnd} reviewer skipped: {str(e)[:60]}", flush=True)
                try:
                    d = pool.submit(decide, c, proposals, probs, critique, obs, history, a.keep, log,
                                    a.chief_timeout).result(timeout=a.chief_timeout + 5)
                    chosen = []
                    for s in d.get("selected", [])[: a.keep]:
                        src = [i for i in s.get("from_proposals", []) if 0 <= i < len(proposals)]
                        credit = "+".join(sorted({proposals[i]["proposer"] for i in src})) or "carbon"
                        chosen.append({**s, "credit": credit + ("" if len(src) == 1 and s["steps"] == proposals[src[0]]["steps"]
                                                                else "~carbon")})
                    if chosen:
                        selected = chosen
                    guidance = str(d.get("guidance", ""))[:300]
                    live.event("note", agent="carbon", text=str(d.get("discussion", ""))[:300])
                except (FutTimeout, Exception) as e:
                    print(f"  [{board_id[:24]}] r{rnd} chief skipped, using screen ranking: {str(e)[:60]}", flush=True)
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
        # 4. execute each selection on its own copy (parallel)
        with ThreadPoolExecutor(len(selected)) as ex:
            kids = list(ex.map(lambda js: run_child(board_id, state, js[1]["steps"],
                                                    out_dir / f"r{rnd}_c{js[0]}.kicad_pcb"), enumerate(selected)))
        executed_total += len(selected)
        # 5. judge: best child becomes the shared state, if it is better
        cur = health(obs)
        best_i, best_h = None, cur
        for i, k in enumerate(kids):
            live.event("attempt", agent=selected[i].get("credit", "?"), plan=json.dumps(selected[i]["steps"])[:160],
                       open=(k.get("obs") or {}).get("open"),
                       errors=((k.get("obs") or {}).get("open", 0) + max(0, (k.get("obs") or {}).get("errors_not_open", 0)))
                       if k.get("ok") else None,
                       clean=bool((k.get("obs") or {}).get("clean")), best=False, why=selected[i].get("why", "")[:200])
            if k.get("ok") and tuple(k["health"]) < tuple(best_h):
                best_i, best_h = i, k["health"]
        before = obs["open"]
        if best_i is not None:
            state = Path(kids[best_i]["saved"])
            obs = kids[best_i]["obs"]
            live.publish(state, selected[best_i].get("credit", "agent").split("~")[0].split("+")[0])
            try:      # fresh picture for the reviewer next round
                e2 = Engine()
                e2.call("open", board=str(state), project=str(f["pro"]))
                image = out_dir / f"state_r{rnd}.png"
                e2.call("render", path=str(image))
                e2.close()
            except Exception:
                pass
        history.append({"round": rnd, "executed": [s["steps"] for s in selected], "before": before,
                        "after": obs["open"], "improved": best_i is not None,
                        "winner": selected[best_i].get("credit") if best_i is not None else None})
        with open(log, "a") as fh:
            fh.write(json.dumps({"role": "judge", "round": rnd, "children": [
                {"credit": selected[i].get("credit"), "ok": k.get("ok"), "health": k.get("health"),
                 "log": k.get("log")} for i, k in enumerate(kids)], "winner": best_i, "open_now": obs["open"]}) + "\n")
        print(f"  [{board_id[:24]}] round {rnd}: {len(proposals)} proposals -> {len(selected)} executed -> "
              f"open {before}->{obs['open']} {'(kept)' if best_i is not None else '(stalled)'}", flush=True)
    # final scoring by PCBWorld's scorer on the saved state
    final = OUT / a.run_id / "submitted" / f"{board_id}.kicad_pcb"
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(state, final)
    pro = Path(state).with_suffix(".kicad_pro")
    if pro.exists():
        shutil.copy(pro, final.with_suffix(".kicad_pro"))
    eng = Engine()
    try:
        score = eng.call("score", path=str(final), project=str(f["pro"]))
    except Exception as e:
        score = {"error": str(e)[:160]}
    finally:
        eng.close()
    live.result(bool(score.get("clean_pass")), executed_total, [])
    (out_dir / "history.json").write_text(json.dumps(history, indent=1, default=str))
    return {"board": board_id, "start": {"unrouted": st["unrouted"], "drc_errors": st["drc_errors"],
                                         "clean_pass": base.get("clean_pass")},
            "final": score, "rounds": len(history), "attempts": executed_total,
            "open_by_round": [st["unrouted"]] + [h["after"] for h in history],
            "winners": [h.get("winner") for h in history], "best_tag": "council", "best_agent": "council",
            "best_plan": {"name": "proposer + orchestrator council"}, "submitted": str(final)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boards", nargs="+", required=True)
    ap.add_argument("--start", type=Path, required=True, help="folder of classic-router boards")
    ap.add_argument("--proposers", type=int, default=6)
    ap.add_argument("--keep", type=int, default=3, help="action sequences executed per round")
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--wall", type=float, default=900, help="per-board wall-clock limit (s)")
    ap.add_argument("--policy", default="agent", choices=["agent", "random"])
    ap.add_argument("--no-orchestrators", action="store_true", help="ablation: screen ranking only")
    ap.add_argument("--no-screen", action="store_true", help="ablation: no Decisions-API screen")
    ap.add_argument("--review-timeout", type=float, default=40)
    ap.add_argument("--chief-timeout", type=float, default=90)
    ap.add_argument("--run-id", default=time.strftime("council_%H%M%S"))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--boards-parallel", type=int, default=2)
    a = ap.parse_args()
    start_dir = a.start if a.start.is_absolute() else PCBW / a.start
    jobs = [(b, hits[0]) for b in a.boards if (hits := sorted(start_dir.glob(f"{b}*.kicad_pcb")))]
    (OUT / a.run_id).mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with ThreadPoolExecutor(a.boards_parallel) as ex:
        results = list(ex.map(lambda j: council_board(j[0], j[1], a), jobs))
    n = len(results)
    summary = {"run_id": a.run_id, "method": f"council ({a.policy})", "boards": n,
               "start_clean": sum(bool(r["start"]["clean_pass"]) for r in results),
               "final_clean": sum(bool((r.get("final") or {}).get("clean_pass")) for r in results),
               "proposers": a.proposers, "keep": a.keep, "rounds": a.rounds,
               "orchestrators": not a.no_orchestrators, "screen": not a.no_screen,
               "total_attempts": sum(r["attempts"] for r in results),
               "wall_s": round(time.time() - t0, 1), "results": results}
    (OUT / a.run_id / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    for r in results:
        fsc = r.get("final") or {}
        print(f"{r['board'][:44]:44s} open by round {r['open_by_round']} -> clean={fsc.get('clean_pass')} "
              f"winners={r['winners']}")
    print(f"\ncouncil: clean {summary['start_clean']}/{n} -> {summary['final_clean']}/{n}  ({summary['wall_s']} s)")


if __name__ == "__main__":
    main()
