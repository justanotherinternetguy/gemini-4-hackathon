"""Board specialist: one long-horizon Gemini Managed Agent per stuck board.

The swarm (pcb/repair/swarm.py) plans in short single-shot calls. The specialist is the other
shape: ONE agent stays with ONE board for many turns. It sees the board picture, the history of
every earlier attempt, and three coarse client-side tools that run the real PCBWorld engine here:

  try_repair(plans)   run 1-2 complete repair attempts from the starting board (rip-up set, route
                      order, router mode, via search / via-site order); returns what is still
                      open, which nets failed to re-route, vias added and a picture of the result
  inspect(which, net) status of the starting board or of the best attempt: open connections,
                      candidate blockers, numbered via sites for a net
  submit(tag, reason) name the attempt to submit

The agent never draws copper: KiCad's push-and-shove router does, and PCBWorld's own scorer judges.
Budgets are enforced on OUR side (plans, wall clock, inspect calls); the first clean pass stops it.
One agent per board; no two agents ever touch the same board.

  .venv/bin/python -m pcb.repair.specialist --boards 0400_laptimer58_Chickadee \
      0473_data-manager_data-manager 0626_TOBS_HybridChargeController \
      --model gemini-4-hydrogen --max-plans 8 --wall 720 --run-id hard_specialist
  .venv/bin/python -m pcb.repair.specialist --boards 0400_laptimer58_Chickadee --dry-run   # no API call
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import statistics
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pcb.repair.swarm import OUT, PCBW, Engine, board_files, execute, rank_key

START_DIR = PCBW / "var" / "results" / "baselines" / "PCBench_hard_test" / "freerouting" / "seed0" / "routed"
MAX_INSPECTS = 6
PRINT_LOCK = threading.Lock()


def _b64(p: Path) -> str:
    return base64.b64encode(Path(p).read_bytes()).decode()


def _final_per_net(route_log: list) -> dict:
    last = {}
    for r in route_log or []:
        last[r.get("net")] = r
    return last


TOOLS = [
    {"type": "code_execution"},
    {"type": "function", "name": "try_repair",
     "description": "Run 1 or 2 complete repair attempts (in parallel, each from the STARTING board, each counted "
                    "against your plan budget). Per plan: delete the copper of the `ripup` nets, re-route the nets "
                    "in `order` (every ripped-up net and every open net should be in it), give anything still open "
                    "a second chance in the other router mode, then (if `vias`) search via sites for layer changes "
                    "on whatever is still open - a via is kept only if it adds no rule error. Returns per attempt: "
                    "tag, clean_pass, open connections left, rule errors, nets that failed to re-route, vias added, "
                    "and a picture of the best resulting board.",
     "parameters": {"type": "object", "properties": {
         "plans": {"type": "array", "description": "1 or 2 DIFFERENT plans", "items": {"type": "object", "properties": {
             "name": {"type": "string"},
             "ripup": {"type": "array", "items": {"type": "integer"}, "description": "net codes whose copper is deleted first"},
             "order": {"type": "array", "items": {"type": "integer"}, "description": "net codes to route, in this order"},
             "mode": {"type": "integer", "description": "1 = shove neighbouring copper aside, 2 = walk around it"},
             "vias": {"type": "boolean", "description": "search via sites (layer changes) for anything still open; default true"},
             "via_order": {"type": "array", "description": "optional: preferred via-site indices (from inspect) per net",
                           "items": {"type": "object", "properties": {
                               "net": {"type": "integer"}, "sites": {"type": "array", "items": {"type": "integer"}}},
                               "required": ["net", "sites"]}},
             "why": {"type": "string", "description": "one sentence: what you expect this plan to fix"}},
             "required": ["name", "ripup", "order", "mode", "why"]}}},
         "required": ["plans"]}},
    {"type": "function", "name": "inspect",
     "description": f"Free look (max {MAX_INSPECTS} calls) at the STARTING board or at your BEST attempt so far: open "
                    "connections with endpoint coordinates (mm), candidate blocker nets per open connection, rule "
                    "errors; with `net`, the numbered candidate via sites for that net's open connections.",
     "parameters": {"type": "object", "properties": {
         "which": {"type": "string", "enum": ["start", "best"]},
         "net": {"type": "integer", "description": "optional net code: list its candidate via sites"}},
         "required": ["which"]}},
    {"type": "function", "name": "submit",
     "description": "Name the attempt to submit for this board ('start' = the untouched starting board). Ends the run.",
     "parameters": {"type": "object", "properties": {
         "tag": {"type": "string"}, "reason": {"type": "string"}}, "required": ["tag", "reason"]}},
]

SYSTEM = """You are the board specialist: a PCB layout engineer who stays with ONE auto-routed board until it is
clean or your budget runs out. Clean pass = every connection routed AND zero error-level design-rule
violations on the saved board. You cannot draw copper. You decide WHICH nets to rip up, in WHAT order nets are
re-routed, the router mode (1 = shove, 2 = walk around), and whether / where to change layers (via search and
via-site order). KiCad's push-and-shove router draws every track; the PCBWorld scorer judges every attempt.
Rules of thumb: rip up as little as possible (every ripped-up net is a net that may fail to come back); a net
that blocks an open connection must be re-routed AFTER the net it was blocking; big power/ground nets rarely
re-route completely once ripped up. Learn from every attempt: do not repeat a plan that already failed, change
one idea at a time and say why. Tool round trips are slow, so think before each call and use both plan slots.
When an attempt returns clean_pass = true, STOP and call submit with its tag. When the budget is exhausted,
call submit with the best tag (or 'start' if nothing beat the starting board), then give a short final message:
what you tried, what you learned about this board, what is still blocking it."""


class Specialist:
    def __init__(self, board_id: str, start: Path, run_dir: Path, a):
        self.board, self.start, self.a = board_id, Path(start), a
        self.out_dir = run_dir / board_id
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.files = board_files(board_id)
        self.short = board_id[:4]
        self.t0 = time.time()
        self.history: list[dict] = []        # start + every attempt (swarm record shape)
        self.plans_used = 0
        self.inspects = 0
        self.submitted = None
        self.done = False
        self.after_done_calls = 0
        self.images_ok = not a.no_images
        self.image_error = None
        self.api_s: list[float] = []
        self.tool_s: list[float] = []
        self.usage: list = []
        self.turns = 0
        self.seen: set[str] = set()
        self.names: dict[int, str] = {}
        self.final_message = ""
        self.note = ""

    # ------------------------------------------------------------------ plumbing
    def log(self, msg: str) -> None:
        with PRINT_LOCK:
            print(f"{time.strftime('%H:%M:%S')} [{self.short}] {msg}", flush=True)

    def elapsed(self) -> float:
        return time.time() - self.t0

    def trace(self, **rec) -> None:
        rec = {"t": round(self.elapsed(), 1), "turn": self.turns, **rec}
        with open(self.out_dir / "specialist_trace.jsonl", "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")

    def best(self, include_start: bool = True) -> dict | None:
        ok = [h for h in self.history if h.get("ok") and (include_start or h["tag"] != "start")]
        if not ok:
            return None
        pick = (self.submitted or {}).get("tag")
        return sorted(ok, key=lambda h: (rank_key(h), h["tag"] != pick, h["tag"] != "start"))[0]

    def budget(self) -> dict:
        return {"plans_used": self.plans_used, "plans_left": max(0, self.a.max_plans - self.plans_used),
                "wall_left_s": int(max(0, self.a.wall - self.elapsed())),
                "inspect_left": MAX_INSPECTS - self.inspects}

    # ------------------------------------------------------------------ board state
    def setup(self) -> None:
        eng = Engine()
        try:
            st = eng.call("open", board=str(self.start), project=str(self.files["pro"]))
            if "open_nets" not in st or "violations" not in st:
                st = eng.call("status", detail=True)
            nets = eng.call("nets")
            self.img = self.out_dir / "start.png"
            try:
                eng.call("render", path=str(self.img))
            except Exception as e:
                self.log(f"render failed: {str(e)[:100]}")
            block = eng.call("blockers") if not st.get("clean") else {"open_connections": []}
            s0 = self.out_dir / f"{self.board}__start.kicad_pcb"
            eng.call("save", path=str(s0))
            score = eng.call("score", path=str(s0), project=str(self.files["pro"]))
        finally:
            eng.close()
        self.st, self.nets, self.block = st, nets, block
        self.names = {int(k): v for k, v in nets["names"].items()}
        self.codes = {v: k for k, v in self.names.items()}
        (self.out_dir / "blockers.json").write_text(json.dumps(block, indent=1))
        self.history.append({"board": self.board, "tag": "start", "plan": {"name": "start (classic router output)"},
                             "end_status": {k: st[k] for k in ("unrouted", "drc_errors", "clean")},
                             "score": score, "saved": str(s0), "ok": True})
        self.log(f"start: open={st['unrouted']} err={st['drc_errors']} clean_pass={score.get('clean_pass')}")

    def earlier_attempts(self) -> str:
        lines, seen = [], set()
        for hp in sorted(OUT.glob(f"*/{self.board}/history.json")):
            if hp.parent.parent.name == self.a.run_id:
                continue
            try:
                hist = json.loads(hp.read_text())
            except Exception:
                continue
            for h in hist:
                p = h.get("plan") or {}
                if h.get("tag") == "start" or not h.get("ok", True):
                    continue
                via_used = any(r.get("via") for r in h.get("route_log") or [])
                sig = json.dumps([p.get("ripup"), p.get("order"), p.get("mode"), via_used])
                if sig in seen:
                    continue
                seen.add(sig)
                per = "; ".join(f"{r.get('name')}({n}) " + ("OK" if not r.get("open_left") else f"{r['open_left']} open")
                                for n, r in _final_per_net(h.get("route_log")).items())
                es, s = h.get("end_status") or {}, h.get("score") or {}
                lines.append(f"- [{hp.parent.parent.name}/{h.get('tag')}] {p.get('name')}: ripup={p.get('ripup')} "
                             f"order={p.get('order')} mode={p.get('mode')} via_search={'yes' if via_used else 'no'} "
                             f"-> open={es.get('unrouted')} errors={es.get('drc_errors')} "
                             f"clean={s.get('clean_pass')} | {per}"[:520])
        return "\n".join(lines[-36:]) or "(none)"

    def earlier_diagnoses(self) -> str:
        out = []
        for lp in sorted(OUT.glob(f"*/{self.board}/llm_calls.jsonl")):
            try:
                for line in lp.read_text().splitlines():
                    d = json.loads(line)
                    if d.get("diagnosis"):
                        out.append(f"- ({d.get('model')}) {d['diagnosis'][:500]}")
            except Exception:
                pass
        return "\n".join(out[-3:]) or "(none)"

    def brief(self) -> list[dict]:
        st, a = self.st, self.a
        opens = "\n".join(
            f"  net {oc['net']} {oc['name']!r}: ({oc['from'][0]}, {oc['from'][1]}) -> ({oc['to'][0]}, {oc['to'][1]}) "
            f"{oc['length_mm']} mm; copper crossing the straight path: "
            + (", ".join(f"{c['name']}({c['net']}) x{c['crossings']} on layer(s) {c['layers']}" for c in oc["candidates"]) or "none")
            for oc in self.block.get("open_connections", [])) or "  (none)"
        viol = json.dumps((st.get("violations") or [])[:12])[:2200]
        names = json.dumps({k: v for k, v in list(self.names.items())[:140]})[:3200]
        tri = ""
        tp = OUT / "hard_agents" / self.board / "triage.json"
        if tp.exists():
            tri = ("\nBLOCKER SCREEN from an earlier yes/no model (p = probability that removing the net is needed):\n"
                   + tp.read_text().replace("\n", "").replace(" ", "")[:1500])
        text = f"""BOARD {self.board}: {self.nets.get('layers')} copper layers. A classic autorouter (Freerouting) left it with
{st['unrouted']} open connection(s) and {st['drc_errors']} rule error(s) (an open connection counts as a rule error).
The attached image is the board now: copper by layer (red = layer 1, blue = layer 2), pads (squares), open
connections (orange dashed, labelled with the net name), rule errors (red X). Axes in mm.

OPEN NETS: {json.dumps(st.get('open_nets'))}
OPEN CONNECTIONS and candidate blockers:
{opens}
RULE ERRORS PER NET: {json.dumps(st.get('errors_per_net'))}
RULE VIOLATIONS (first 12): {viol}
NET CODES -> NAMES: {names}
{tri}

EARLIER ATTEMPTS on this board by a swarm of single-shot planners (every one started from this same board; none
reached a clean pass). "via_search=no" means layer changes were never tried in that attempt:
{self.earlier_attempts()}

EARLIER DIAGNOSES:
{self.earlier_diagnoses()}

YOUR BUDGET: {a.max_plans} repair plans (try_repair takes 1 or 2 per call, run in parallel), {int(a.wall)} s wall
clock, {MAX_INSPECTS} inspect calls. Every try_repair attempt starts from the starting board (attempts do not
stack). Via search (`vias`: true, optional `via_order` from inspect(net)) is the main lever the earlier attempts
did not have. Start now: decide what the earlier attempts tell you, then call try_repair."""
        if a.note:
            text += "\n\nNOTE FROM THE OPERATOR: " + a.note
        blocks = [{"type": "text", "text": text}]
        if self.images_ok and getattr(self, "img", None) and self.img.exists():
            blocks.append({"type": "image", "mime_type": "image/png", "data": _b64(self.img)})
        return blocks

    # ------------------------------------------------------------------ tools
    def _code(self, x) -> int | None:
        try:
            n = int(x)
        except (TypeError, ValueError):
            n = self.codes.get(str(x))
        return n if n in self.names else None

    def _norm(self, p: dict, i: int) -> dict:
        rip = [n for n in (self._code(x) for x in (p.get("ripup") or [])) if n is not None]
        order = [n for n in (self._code(x) for x in (p.get("order") or [])) if n is not None]
        order += [n for n in rip if n not in order]          # a ripped-up net must be routed again
        try:
            mode = 1 if int(p.get("mode", 2)) == 1 else 2
        except (TypeError, ValueError):
            mode = 2
        plan = {"name": str(p.get("name") or f"plan-{i}")[:60], "ripup": list(dict.fromkeys(rip)),
                "order": list(dict.fromkeys(order)), "mode": mode, "vias": bool(p.get("vias", True)),
                "via_trials": self.a.via_trials, "by": f"specialist:{self.a.model}", "why": str(p.get("why", ""))[:400]}
        vo = p.get("via_order")
        if isinstance(vo, list):
            vo = {str(x.get("net")): x.get("sites") for x in vo if isinstance(x, dict)}
        if isinstance(vo, dict) and vo:
            plan["via_order"] = {str(k): [int(s) for s in (v or [])][:36] for k, v in vo.items()}
        return plan

    def _run_one(self, plan: dict, tag: str) -> dict:
        box, t = {}, time.time()

        def work():
            try:
                box["rec"] = execute(self.board, self.start, plan, self.out_dir, tag)
            except Exception as e:
                box["rec"] = {"board": self.board, "tag": tag, "plan": plan, "ok": False,
                              "error": f"{type(e).__name__}: {e}"}

        th = threading.Thread(target=work, daemon=True)
        th.start()
        th.join(self.a.attempt_timeout)
        if th.is_alive():
            return {"board": self.board, "tag": tag, "plan": plan, "ok": False, "seconds": round(time.time() - t, 1),
                    "error": f"attempt timed out after {self.a.attempt_timeout:.0f} s (router too slow on this plan)"}
        return box["rec"]

    def _digest(self, rec: dict) -> dict:
        d = {"tag": rec["tag"], "plan": rec["plan"].get("name"), "seconds": rec.get("seconds")}
        if not rec.get("ok"):
            d.update(ok=False, error=str(rec.get("error"))[:300])
            return d
        s, es = rec.get("score") or {}, rec.get("end_status") or {}
        last = _final_per_net(rec.get("route_log"))
        v0 = (self.history[0].get("score") or {}).get("via_count")
        d.update(clean_pass=bool(s.get("clean_pass")), open_connections_left=es.get("unrouted"),
                 rule_errors=es.get("drc_errors"),
                 nets_still_open=[{"net": n, "name": r.get("name"), "connections_open": r["open_left"]}
                                  for n, r in last.items() if r.get("open_left")],
                 nets_completed=[f"{r.get('name')}({n})" for n, r in last.items() if not r.get("open_left")],
                 vias_added=(s.get("via_count") - v0) if None not in (s.get("via_count"), v0) else None,
                 via_search=[{"net": r.get("net"), "name": r.get("name"), "connections_made": r.get("connections_made"),
                              "open_left": r.get("open_left"), "trials": r.get("trials"), "kept": r.get("kept")}
                             for r in rec.get("route_log") or [] if r.get("via")],
                 routability=round(s.get("routability") or 0, 4))
        return d

    def _render(self, board_path: str, png: Path) -> Path | None:
        eng = Engine()
        try:
            eng.call("open", board=str(board_path), project=str(self.files["pro"]))
            eng.call("render", path=str(png))
            return png if png.exists() else None
        except Exception as e:
            self.log(f"render failed: {str(e)[:100]}")
            return None
        finally:
            eng.close()

    def try_repair(self, args: dict) -> dict:
        if self.done:
            self.after_done_calls += 1
            return {"error": "the run is over (already submitted or clean pass found); give your final message"}
        raw = args.get("plans") or ([args["plan"]] if isinstance(args.get("plan"), dict) else
                                    ([args] if ("order" in args or "ripup" in args) else []))
        if isinstance(raw, dict):
            raw = [raw]
        if not raw:
            return {"error": "pass plans: [{name, ripup, order, mode, vias, why}]"}
        left = self.a.max_plans - self.plans_used
        if left <= 0:
            return {"error": "budget exhausted: no plans left. Call submit(tag, reason) now.", **self._best_line()}
        if self.elapsed() > self.a.wall:
            return {"error": "budget exhausted: wall clock. Call submit(tag, reason) now.", **self._best_line()}
        plans, skipped = [], []
        for i, p in enumerate(raw[: self.a.batch]):
            plan = self._norm(p if isinstance(p, dict) else {}, self.plans_used + i)
            sig = json.dumps([plan["ripup"], plan["order"], plan["mode"], plan["vias"], plan.get("via_order")])
            if not plan["order"]:
                skipped.append({"plan": plan["name"], "skipped": "no valid net codes in order/ripup (not counted)"})
            elif sig in self.seen:
                skipped.append({"plan": plan["name"], "skipped": "identical to a plan you already ran (not counted)"})
            else:
                self.seen.add(sig)
                plans.append(plan)
        plans = plans[:left]
        if len(raw) > self.a.batch:
            skipped.append({"skipped": f"only {self.a.batch} plans per call; the rest were ignored (not counted)"})
        if not plans:
            return {"error": "no runnable plan in this call", "details": skipped, **self.budget()}
        tags = [f"spec_{self.plans_used + i}" for i in range(len(plans))]
        self.plans_used += len(plans)
        for tag, p in zip(tags, plans):
            self.log(f"try {tag} {p['name'][:32]!r} ripup={p['ripup']} order={p['order']} mode={p['mode']} "
                     f"vias={p['vias']}{' via_order' if p.get('via_order') else ''}")
        with ThreadPoolExecutor(len(plans)) as ex:
            recs = list(ex.map(lambda tp: self._run_one(tp[1], tp[0]), zip(tags, plans)))
        digests = []
        for r in recs:
            self.history.append(r)
            d = self._digest(r)
            digests.append(d)
            self.log(f"  -> {r['tag']} open={d.get('open_connections_left')} err={d.get('rule_errors')} "
                     f"clean={d.get('clean_pass')} vias+={d.get('vias_added')} {r.get('seconds')}s "
                     f"{str(r.get('error') or '')[:90]}")
        (self.out_dir / "history.json").write_text(json.dumps(self.history, indent=1, default=str))
        out = {"attempts": digests, **self._best_line(), **self.budget()}
        if skipped:
            out["skipped"] = skipped
        clean = [r for r in recs if (r.get("score") or {}).get("clean_pass")]
        if clean:
            self.done = True
            self.submitted = self.submitted or {"tag": clean[0]["tag"], "reason": "first clean pass (auto)", "auto": True}
            out["STOP"] = (f"CLEAN PASS with {clean[0]['tag']}. The run is over: call submit(tag='{clean[0]['tag']}') "
                           "and give your final message.")
        elif self.plans_used >= self.a.max_plans or self.elapsed() > self.a.wall:
            out["STOP"] = "Budget exhausted. Call submit(tag, reason) now ('start' if nothing beat the starting board)."
        okr = sorted([r for r in recs if r.get("ok")], key=rank_key)
        if self.images_ok and okr:
            png = self._render(okr[0]["saved"], self.out_dir / f"{okr[0]['tag']}.png")
            if png:
                out["_image"] = str(png)
                out["picture"] = f"attached: board after {okr[0]['tag']}"
        return out

    def _best_line(self) -> dict:
        b = self.best()
        if not b:
            return {}
        return {"best_so_far": {"tag": b["tag"], "open": b["end_status"].get("unrouted"),
                                "rule_errors": b["end_status"].get("drc_errors"),
                                "clean_pass": bool((b.get("score") or {}).get("clean_pass"))}}

    def inspect(self, args: dict) -> dict:
        if self.inspects >= MAX_INSPECTS:
            return {"error": f"inspect limit reached ({MAX_INSPECTS}); decide with what you have"}
        self.inspects += 1
        which = "best" if str(args.get("which", "start")).lower() == "best" else "start"
        rec = (self.best(include_start=False) or self.history[0]) if which == "best" else self.history[0]
        eng = Engine()
        try:
            st = eng.call("open", board=str(rec["saved"]), project=str(self.files["pro"]))
            if "open_nets" not in st:
                st = eng.call("status", detail=True)
            out = {"which": which, "tag": rec["tag"], "open_connections": st.get("unrouted"),
                   "rule_errors": st.get("drc_errors"), "open_nets": st.get("open_nets"),
                   "errors_per_net": st.get("errors_per_net"),
                   "violations": json.loads(json.dumps((st.get("violations") or [])[:8], default=str))}
            if st.get("unrouted"):
                out["blockers"] = eng.call("blockers").get("open_connections")
            net = self._code(args.get("net")) if args.get("net") is not None else None
            if net is not None:
                vs = eng.call("via_sites", net=net)
                out["via_sites"] = {"net": net, "name": vs.get("name"), "note": "site index: [x_mm, y_mm, where]",
                                    "connections": [{"from": c["from"], "to": c["to"],
                                                     "sites": {i: [s["x"], s["y"], s["where"]]
                                                               for i, s in enumerate(c["sites"])}}
                                                    for c in vs.get("connections", [])]}
                if not vs.get("connections"):
                    out["via_sites"]["note"] = "this net has no open connection on this board"
            if which == "best" and self.images_ok and rec["tag"] != "start":
                png = self.out_dir / f"{rec['tag']}.png"
                if not png.exists():
                    try:
                        eng.call("render", path=str(png))
                    except Exception:
                        pass
                if png.exists():
                    out["_image"] = str(png)
            out.update(self.budget())
            return out
        finally:
            eng.close()

    def submit(self, args: dict) -> dict:
        tag = str(args.get("tag", "")).strip()
        tags = [h["tag"] for h in self.history if h.get("ok")]
        if tag not in tags:
            return {"error": f"unknown tag {tag!r}; choose one of {tags}"}
        self.submitted = {"tag": tag, "reason": str(args.get("reason", ""))[:600]}
        self.done = True
        self.log(f"submit {tag}: {self.submitted['reason'][:140]}")
        return {"accepted": tag, "note": "The run is over. Reply with a short final message: what you tried, what "
                                         "you learned about this board, what is still blocking it."}

    # ------------------------------------------------------------------ agent loop
    def _handle(self, name: str, args: dict) -> tuple[dict, str | None]:
        fn = {"try_repair": self.try_repair, "inspect": self.inspect, "submit": self.submit}.get(name)
        t = time.time()
        try:
            res = fn(args) if fn else {"error": f"unknown tool {name}"}
        except Exception as e:           # a failed tool call goes back to the agent, never crashes the run
            res = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
        img = res.pop("_image", None)
        secs = round(time.time() - t, 1)
        self.tool_s.append(secs)
        self.trace(tool=name, args=args, result=res, seconds=secs, image_attached=bool(img and self.images_ok))
        if name != "try_repair":
            self.log(f"{name}({json.dumps(args)[:90]}) {secs}s {str(res.get('error') or '')[:100]}")
        return res, img

    def drive(self, c) -> None:
        from sizing import managed
        d = self.out_dir
        cfg = {"type": "antigravity", "model": self.a.model, "max_total_tokens": self.a.tokens}
        kw0 = dict(agent=managed.AGENT, agent_config=cfg, tools=TOOLS, system_instruction=SYSTEM)
        blocks = self.brief()
        (d / "brief.txt").write_text(blocks[0]["text"])
        self.t0 = time.time()
        try:
            it = managed._stream(c, d, input=blocks, environment="remote", **kw0)
        except Exception as e:
            if len(blocks) < 2:
                raise
            self.log(f"input with image rejected ({str(e)[:160]}); retrying text-only")
            self.image_error = f"input image: {str(e)[:300]}"
            it = managed._stream(c, d, input=blocks[:1], environment="remote", **kw0)
        self.api_s.append(round(time.time() - self.t0, 1))
        self.turns = 1
        while True:
            self.usage.append(getattr(getattr(it, "usage", None), "total_tokens", None))
            with open(d / "interactions.jsonl", "a") as f:
                f.write(json.dumps({"turn": self.turns, "id": it.id, "environment_id": it.environment_id,
                                    "status": it.status, "total_tokens": self.usage[-1],
                                    "t": round(self.elapsed(), 1)}) + "\n")
            self.final = it
            if it.status != "requires_action":
                break
            if self.elapsed() > self.a.wall + self.a.grace or self.after_done_calls > 2:
                self.note = "stopped by us (hard wall clock)" if self.after_done_calls <= 2 else "stopped by us (kept calling tools after the end)"
                try:
                    c.interactions.cancel(id=it.id)
                except Exception:
                    pass
                break
            steps = it.steps or []
            done = {getattr(s, "call_id", None) for s in steps if s.type == "function_result"}
            calls = [s for s in steps if s.type == "function_call" and s.id not in done]
            with_img, text_only = [], []
            for call in calls:
                res, img = self._handle(call.name, dict(call.arguments or {}))
                base = {"type": "function_result", "name": call.name, "call_id": call.id}
                text_only.append({**base, "result": res})
                if img and self.images_ok:
                    with_img.append({**base, "result": [{"type": "text", "text": json.dumps(res, default=str)},
                                                        {"type": "image", "mime_type": "image/png", "data": _b64(img)}]})
                else:
                    with_img.append({**base, "result": res})
            self.turns += 1
            kw = dict(kw0, previous_interaction_id=it.id, environment=it.environment_id)
            t_api = time.time()
            it = self._resume(c, managed, kw, with_img, text_only)
            self.api_s.append(round(time.time() - t_api, 1))
            self.trace(event="agent_turn", api_seconds=self.api_s[-1], status=it.status)
        self.final_message = (getattr(self.final, "output_text", None) or "").strip()

    def _resume(self, c, managed, kw: dict, with_img: list, text_only: list):
        if any(isinstance(r["result"], list) for r in with_img):
            try:
                return managed._stream(c, self.out_dir, input=with_img, **kw)
            except Exception as e:
                self.images_ok = False
                self.image_error = f"image tool result: {str(e)[:300]}"
                self.log(f"image tool result rejected ({str(e)[:160]}); falling back to text-only results")
                self.trace(event="image_result_rejected", error=str(e)[:500])
        try:
            return managed._stream(c, self.out_dir, input=text_only, **kw)
        except Exception as e:   # some API versions reject re-sent tools/config on continuation
            if "400" in str(e) or "tools" in str(e).lower():
                kw2 = {k: v for k, v in kw.items() if k not in ("tools", "agent_config")}
                return managed._stream(c, self.out_dir, input=text_only, **kw2)
            raise

    # ------------------------------------------------------------------ results
    def finish(self, run_dir: Path, error: str | None = None) -> dict:
        start = self.history[0]
        best = self.best() or start
        if rank_key(best) > rank_key(start):          # never submit something worse than the starting board
            best = start
        final = run_dir / "submitted" / f"{self.board}.kicad_pcb"
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(best["saved"], final)
        pro = Path(best["saved"]).with_suffix(".kicad_pro")
        shutil.copy(pro if pro.exists() else self.files["pro"], final.with_suffix(".kicad_pro"))
        try:
            self._render(str(final), self.out_dir / "final.png")
        except Exception:
            pass
        (self.out_dir / "history.json").write_text(json.dumps(self.history, indent=1, default=str))
        toks = [u for u in self.usage if u]
        summary = {"board": self.board, "model": self.a.model, "plans_tried": self.plans_used,
                   "clean_pass": bool((best.get("score") or {}).get("clean_pass")),
                   "start": {"unrouted": start["end_status"]["unrouted"], "drc_errors": start["end_status"]["drc_errors"],
                             "clean_pass": bool(start["score"].get("clean_pass"))},
                   "best": {"tag": best["tag"], "plan": best["plan"], "score": best.get("score"),
                            "end_status": best.get("end_status")},
                   "beat_start": rank_key(best) < rank_key(start),
                   "agent_submitted": self.submitted, "wall_s": round(self.elapsed(), 1), "turns": self.turns,
                   "tool_round_trip_median_s": round(statistics.median(self.api_s), 1) if self.api_s else None,
                   "agent_api_seconds": self.api_s, "tool_seconds": self.tool_s,
                   "inspect_calls": self.inspects, "tokens_per_interaction": self.usage, "tokens_total": sum(toks),
                   "image_results": self.images_ok, "image_error": self.image_error,
                   "agent_final_message": self.final_message, "note": self.note, "error": error,
                   "submitted": str(final)}
        (self.out_dir / "specialist_summary.json").write_text(json.dumps(summary, indent=1, default=str))
        self.log(f"done: plans={self.plans_used} best={best['tag']} open={best['end_status'].get('unrouted')} "
                 f"clean={summary['clean_pass']} turns={self.turns} tokens={summary['tokens_total']} "
                 f"{summary['wall_s']}s {error or ''}")
        return summary


def run_board(board_id: str, a, run_dir: Path, client) -> dict:
    cands = sorted(Path(a.start).glob(f"{board_id}*.kicad_pcb"))
    if not cands:
        return {"board": board_id, "error": f"no starting board in {a.start}"}
    sp = Specialist(board_id, cands[0], run_dir, a)
    try:
        sp.setup()
    except Exception as e:
        sp.log(f"setup failed: {e}")
        return {"board": board_id, "error": f"setup: {e}"}
    err = None
    try:
        if a.dry_run:
            sp.t0 = time.time()
            (sp.out_dir / "brief.txt").write_text(sp.brief()[0]["text"])
            open_nets = [n["net"] for n in sp.st["open_nets"]]
            for name, args in (("try_repair", {"plans": [{"name": "dry: finish open nets, then vias", "ripup": [],
                                                          "order": open_nets, "mode": 2, "vias": True, "why": "dry test"}]}),
                               ("inspect", {"which": "best", "net": open_nets[0] if open_nets else None}),
                               ("submit", {"tag": "spec_0", "reason": "dry test"})):
                sp.turns += 1
                res, img = sp._handle(name, args)
                print(json.dumps(res, indent=1, default=str)[:3000], "\nimage:", img, flush=True)
        else:
            sp.drive(client)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:400]}"
        sp.log(f"agent run failed: {err}")
    return sp.finish(run_dir, err)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boards", nargs="+", required=True)
    ap.add_argument("--model", default="gemini-4-hydrogen")
    ap.add_argument("--max-plans", type=int, default=8)
    ap.add_argument("--wall", type=float, default=720, help="seconds per board; no new attempt starts after this")
    ap.add_argument("--grace", type=float, default=300, help="extra seconds before we cancel the agent outright")
    ap.add_argument("--run-id", default=time.strftime("specialist_%H%M%S"))
    ap.add_argument("--parallel", type=int, default=3, help="boards (agents) in parallel")
    ap.add_argument("--batch", type=int, default=2, help="plans per try_repair call (run in parallel)")
    ap.add_argument("--attempt-timeout", type=float, default=330)
    ap.add_argument("--via-trials", type=int, default=60)
    ap.add_argument("--tokens", type=int, default=400_000, help="max_total_tokens per interaction")
    ap.add_argument("--start", type=Path, default=START_DIR)
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--note", default="", help="extra operator note appended to the brief (e.g. a stacked second round)")
    ap.add_argument("--dry-run", action="store_true", help="exercise the tool handlers once, no API call")
    a = ap.parse_args()
    if not a.start.is_absolute():      # engine processes run in PCBWorld's folder: always hand them absolute paths
        a.start = a.start.resolve() if a.start.exists() else PCBW / a.start
    run_dir = OUT / a.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    client = None
    if not a.dry_run:
        from sizing import managed
        client = managed.client()
    t0 = time.time()

    def safe(b):
        try:
            return run_board(b, a, run_dir, client)
        except Exception as e:           # one board failing must not stop the others
            print(f"[{b[:4]}] FAILED: {type(e).__name__}: {e}", flush=True)
            return {"board": b, "error": f"{type(e).__name__}: {e}"}

    with ThreadPoolExecutor(max(1, a.parallel)) as ex:
        outs = list(ex.map(safe, a.boards))
    results = []
    for s in outs:
        if "best" not in s:
            results.append({"board": s["board"], "start": {"unrouted": None, "drc_errors": None, "clean_pass": False},
                            "attempts": 0, "best_tag": None, "best_plan": None, "final": {}, "error": s.get("error")})
            continue
        results.append({"board": s["board"], "start": s["start"], "attempts": s["plans_tried"],
                        "best_tag": s["best"]["tag"], "best_plan": s["best"]["plan"], "final": s["best"]["score"],
                        "submitted": s["submitted"], "turns": s["turns"], "tokens": s["tokens_total"],
                        "tool_round_trip_median_s": s["tool_round_trip_median_s"],
                        "agent_final_message": s["agent_final_message"]})
    n = len(results)
    start_clean = sum(bool(r["start"]["clean_pass"]) for r in results)
    final_clean = sum(bool((r.get("final") or {}).get("clean_pass")) for r in results)
    summary = {"run_id": a.run_id, "boards": n, "start_clean": start_clean, "final_clean": final_clean,
               "start_rate": start_clean / max(n, 1), "final_rate": final_clean / max(n, 1), "llm": not a.dry_run,
               "method": f"managed-agent board specialist ({a.model})",
               "total_attempts": sum(r["attempts"] for r in results),
               "total_tokens": sum(r.get("tokens") or 0 for r in results),
               "wall_s": round(time.time() - t0, 1), "results": results}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    for r in results:
        f = r.get("final") or {}
        print(f"{r['board'][:44]:44s} start: open={r['start']['unrouted']} err={r['start']['drc_errors']} -> "
              f"clean={f.get('clean_pass')} routability={f.get('routability')} ({r['best_tag']}, "
              f"{r['attempts']} plans) {r.get('error') or ''}", flush=True)
    print(f"\nclean pass: {start_clean}/{n} -> {final_clean}/{n}   ({summary['wall_s']} s, "
          f"{summary['total_tokens']} tokens)  -> {run_dir}", flush=True)
    # engine processes we started that are still busy (timed-out attempts): stop them, nobody else's
    try:
        kids = subprocess.run(["pgrep", "-P", str(os.getpid())], capture_output=True, text=True).stdout.split()
        for k in kids:
            os.kill(int(k), 15)
    except Exception:
        pass
    os._exit(0)


if __name__ == "__main__":
    main()
