"""Gemini swarm on AMS-SizingBench (the external benchmark from the AutoSizer paper, ICML 2026).

Same crew idea as sizing/swarm.py, run in the benchmark's OWN setting so numbers are comparable
with published ones: the benchmark's netlists, its discrete value grids, its spec strings, the
typical corner, and a budget counted in ngspice simulations (default 100; the paper's baselines
get 300). Success is checked with our bug-fixed parser (bench/amsbench.py), which is stricter
than the original: signed numbers are parsed and a missing metric fails.

  workers  : gemini-4-hydrogen x N, each proposes K grid points per round
  planner  : gemini-4-hydrogen quick plan each round start (async) + gemini-4-carbon deep plan (async)
  judge    : ngspice via bench.amsbench.simulate (1 simulation per candidate)

python -m bench.ams_swarm telescopic_ota azc smc iac fdgb --seeds 0 1 2 --budget 100
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bench import amsbench as ab
from sizing import managed

WORKER = "gemini-4-hydrogen"
DEEP = "gemini-4-carbon"
OUT = ab.ROOT / "runs" / "ams_swarm"
DIRECTIONS = [
    "fix the single spec term that is furthest from its bound, moving the knobs most responsible for it",
    "trade the metric with the most slack for the one that is failing",
    "bold: move 3 or more knobs at once toward a design you expect to meet every term",
    "careful: change one or two knobs by one grid step around the best design",
    "explore a region of the grid nobody has tried yet (different L or a very different current split)",
    "maximise the figure of merit among designs that already meet the spec; if none do, get one to pass",
    "reason from the netlist about which devices set gain, bandwidth and power, then size them deliberately",
    "contrarian: test the opposite of the team's current trend on the most-moved knob",
]


def violations(cons, metrics) -> list[float]:
    out = []
    for c in cons:
        v = metrics.get(c.metric)
        if v is None:
            out.append(10.0)
            continue
        s = max(abs(c.bound), 1e-12)
        out.append((c.bound - v) / s if c.op in (">", ">=") else (v - c.bound) / s)
    return out


class AmsSwarm:
    def __init__(self, circuit: str, seed: int, budget: int, workers: int, per_worker: int, deep: bool):
        self.name, self.seed, self.budget = circuit, seed, budget
        self.cfg = ab.load(circuit)
        self.cons = ab.parse_specs(self.cfg["user_specs_metric"])
        self.vars = ab.variables(self.cfg)
        self.grids = {v: ab.grid(self.cfg, v) for v in self.vars}
        self.nw, self.k, self.use_deep = workers, per_worker, deep
        self.client = managed.client()
        self.hist: list[dict] = []
        self.seen: set = set()
        self.plan = ""
        self.calls, self.tokens = 0, 0
        self.lock = threading.Lock()
        self._deep_pool = ThreadPoolExecutor(1)
        self._deep = None

    # ---- judge
    def _eval(self, point: dict, who: str, why: str) -> dict | None:
        key = tuple(point[v] for v in self.vars)
        with self.lock:
            if key in self.seen or len(self.hist) + getattr(self, "_inflight", 0) >= self.budget:
                return None
            self.seen.add(key)
            self._inflight = getattr(self, "_inflight", 0) + 1
        r = ab.simulate(self.cfg, point, "tt", 27)
        viol = violations(self.cons, r.metrics)
        rec = {"point": point, "metrics": r.metrics, "ok": r.ok, "viol": viol,
               "feasible": all(x <= 0 for x in viol), "who": who, "why": why}
        with self.lock:
            self._inflight -= 1
            rec["n"] = len(self.hist) + 1
            self.hist.append(rec)
        return rec

    def _rank(self):
        def key(h):
            if h["feasible"]:
                return (0, -(h["metrics"].get("fom") or 0))
            return (1, sum(max(0.0, x) for x in h["viol"]))
        return sorted(self.hist, key=key)

    # ---- prompts
    def _digest(self, rows) -> str:
        out = []
        for h in rows:
            unmet = [str(c) for c, x in zip(self.cons, h["viol"]) if x > 0]
            mets = ", ".join(f"{k}={v:.4g}" if v is not None else f"{k}=n/a" for k, v in h["metrics"].items())
            out.append(f"#{h['n']} {json.dumps(h['point'])} -> {'PASS' if h['feasible'] else 'fail'} | {mets}"
                       + (f" | unmet: {'; '.join(unmet)}" if unmet else ""))
        return "\n".join(out) or "(none)"

    def _census(self) -> str:
        n = len(self.hist)
        lines = []
        for i, c in enumerate(self.cons):
            bad = sum(1 for h in self.hist if h["viol"][i] > 0)
            best = min((h["viol"][i] for h in self.hist), default=0)
            lines.append(f"  {c}: violated by {bad}/{n}; closest candidate is "
                         + ("inside the bound" if best <= 0 else f"{best*100:.1f}% short"))
        return "\n".join(lines)

    def _base(self) -> str:
        cfg = self.cfg
        grids = "\n".join(f"  {v}: {self.grids[v]}" for v in self.vars)
        fixed = json.dumps(cfg.get("params") or {})
        scales = {k: cfg[k] for k in ("width_scales", "length_scales") if cfg.get(k)}
        return f"""CIRCUIT: {self.name} (SkyWater SKY130, ngspice, typical corner, 27 C). Goal: {cfg.get('user_specs', '')}
SPEC (every term must hold): {cfg['user_specs_metric']}
Metric units after post-processing: {json.dumps({k: v.get('unit', '') for k, v in (cfg.get('metric_post') or {}).items()})}
FoM expression: {((cfg.get('metric_post') or {}).get('fom') or {}).get('expr', 'n/a')}
FIXED PARAMETERS: {fixed}
DESIGN VARIABLES and their allowed grid values (you must pick values from these lists):
{grids}
{('VARIABLE -> DEVICE SCALING: ' + json.dumps(scales)) if scales else ''}
NETLIST (placeholders in braces are the variables/parameters above):
{cfg['ota_subckt_template'].strip()}
TESTBENCH (what is measured):
{re.sub(r'(?m)^\s*$\n', '', cfg['testbench_template']).strip()[:2500]}
"""

    def _call(self, model, prompt, schema, thinking, timeout):
        r = self.client.interactions.create(
            model=model, input=prompt, generation_config={"thinking_level": thinking}, timeout=timeout,
            response_format={"type": "text", "mime_type": "application/json", "schema": schema})
        with self.lock:
            self.calls += 1
            self.tokens += (r.usage.total_tokens or 0)
        return json.loads(r.output_text)

    def _plan_call(self, model, thinking, timeout) -> str:
        prompt = f"""You lead a team of {self.nw} designers sizing this circuit; a SPICE simulator judges every candidate.
{self._base()}
BEST CANDIDATES SO FAR:
{self._digest(self._rank()[:6])}
CONSTRAINT CENSUS over {len(self.hist)} simulated candidates:
{self._census()}
Simulations left: {self.budget - len(self.hist)}.
In at most 120 words: say which devices/variables control each failing term (from the netlist), which
direction each should move, and the most promising region of the grid. Be concrete with values."""
        schema = {"type": "object", "properties": {"plan": {"type": "string"}}, "required": ["plan"]}
        try:
            return self._call(model, prompt, schema, thinking, timeout)["plan"]
        except Exception:
            return ""

    def _worker(self, rnd: int, i: int) -> list[tuple[dict, str]]:
        who = f"w{i}"
        mine = [h for h in self.hist if h["who"] == who][-3:]
        props = {v: {"type": "number"} for v in self.vars}
        props["why"] = {"type": "string"}
        schema = {"type": "object", "properties": {"candidates": {
            "type": "array", "minItems": self.k, "maxItems": self.k,
            "items": {"type": "object", "properties": props, "required": list(props)}}},
            "required": ["candidates"]}
        prompt = f"""You are an analog designer on a team sizing this circuit; a SPICE simulator judges every candidate.
{self._base()}
TEAM LEAD'S PLAN: {self.plan or '(not ready yet; use your own judgement)'}
YOUR ROLE THIS ROUND: {DIRECTIONS[(i + rnd) % len(DIRECTIONS)]}
BEST CANDIDATES SO FAR:
{self._digest(self._rank()[:4])}
CONSTRAINT CENSUS over {len(self.hist)} candidates:
{self._census()}
YOUR RECENT ATTEMPTS:
{self._digest(mine)}
Propose exactly {self.k} NEW candidates (not already listed), each a value from the grid for every variable,
with a one-sentence reason."""
        try:
            out = self._call(WORKER, prompt, schema, "low", 60)
        except Exception:
            return []
        res = []
        for c in out.get("candidates", [])[: self.k]:
            try:   # snap to the nearest grid value
                pt = {v: min(self.grids[v], key=lambda g: abs(g - float(c[v]))) for v in self.vars}
            except Exception:
                continue
            res.append((pt, str(c.get("why", ""))[:200]))
        return res

    # ---- loop
    def run(self) -> dict:
        t0 = time.perf_counter()
        self._eval(ab.mid_point(self.cfg), "start", "mid-grid starting point")
        rnd = 0
        if self.use_deep:
            self._deep = self._deep_pool.submit(self._plan_call, DEEP, "medium", 150)
        while len(self.hist) < self.budget and rnd < 40:
            rnd += 1
            if self._deep is not None and self._deep.done():
                p = self._deep.result()
                if p:
                    self.plan = "[deep plan] " + p
                self._deep = self._deep_pool.submit(self._plan_call, DEEP, "medium", 150) if self.use_deep else None
            with ThreadPoolExecutor(self.nw + 1) as ex:
                quick = ex.submit(self._plan_call, WORKER, "low", 45) if rnd > 1 and not self.plan.startswith("[deep") else None
                proposals = list(ex.map(lambda i: self._worker(rnd, i), range(self.nw)))
                if quick is not None:
                    qp = quick.result()
                    if qp:
                        self.plan = qp
            jobs = [(f"w{i}", pt, why) for i, ps in enumerate(proposals) for pt, why in ps]
            before = len(self.hist)
            with ThreadPoolExecutor(12) as ex:
                list(ex.map(lambda j: self._eval(j[1], j[0], j[2]), jobs))
            if len(self.hist) == before:      # all duplicates: nudge with a random unexplored point
                import random
                rng = random.Random(self.seed * 1000 + rnd)
                self._eval({v: rng.choice(self.grids[v]) for v in self.vars}, "random", "duplicate round")
        self._deep_pool.shutdown(wait=False, cancel_futures=True)
        feas = [h for h in self.hist if h["feasible"]]
        best = max(feas, key=lambda h: h["metrics"].get("fom") or -1) if feas else None
        recheck = None
        if best:   # independent re-check with the full PDK library
            r = ab.simulate(self.cfg, best["point"], "tt", 27, lib="full")
            recheck = ab.check(self.cfg, r.metrics)[0]
        summary = {"circuit": self.name, "sampler": "gemini_swarm", "seed": self.seed, "budget": self.budget,
                   "spec": self.cfg["user_specs_metric"], "success": bool(feas), "recheck_pass": recheck,
                   "sims_to_first_pass": feas[0]["n"] if feas else None, "unique_sims": len(self.hist),
                   "n_feasible": len(feas), "best_fom": best["metrics"].get("fom") if best else None,
                   "best_point": best["point"] if best else None,
                   "best_metrics": best["metrics"] if best else None,
                   "llm_calls": self.calls, "tokens": self.tokens, "rounds": rnd,
                   "wall_s": round(time.perf_counter() - t0, 1),
                   "models": {"workers": WORKER, "deep_plan": DEEP if self.use_deep else None}}
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{self.name}_s{self.seed}_b{self.budget}.json").write_text(
            json.dumps({"summary": summary, "trials": self.hist}, indent=1, default=str))
        return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("circuits", nargs="*", default=["telescopic_ota", "azc", "smc", "iac", "fdgb"])
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--budget", type=int, default=100)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--per-worker", type=int, default=2)
    ap.add_argument("--no-deep", action="store_true")
    ap.add_argument("-j", type=int, default=5, help="runs in parallel")
    a = ap.parse_args()
    jobs = [(c, s) for c in a.circuits for s in a.seeds]
    def one(cs):
        s = AmsSwarm(cs[0], cs[1], a.budget, a.workers, a.per_worker, not a.no_deep).run()
        print(json.dumps({k: s[k] for k in ("circuit", "seed", "success", "sims_to_first_pass", "best_fom",
                                            "recheck_pass", "unique_sims", "llm_calls", "wall_s")}), flush=True)
        return s
    with ThreadPoolExecutor(a.j) as ex:
        rows = list(ex.map(one, jobs))
    print(f"\nbudget={a.budget} sims/run")
    for c in a.circuits:
        rs = [r for r in rows if r["circuit"] == c]
        firsts = sorted(r["sims_to_first_pass"] for r in rs if r["sims_to_first_pass"])
        foms = sorted(r["best_fom"] for r in rs if r["best_fom"] is not None)
        print(f"{c:16s} success {sum(r['success'] for r in rs)}/{len(rs)}  sims->1st pass {firsts}  best FoM {[round(f, 3) for f in foms]}")


if __name__ == "__main__":
    main()
