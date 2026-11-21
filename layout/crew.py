"""Layout-planning crew: Gemini agents propose layout plans, a deterministic judge decides.

Layout planning with estimated parasitics - not routed, no DRC/LVS.

Roles
  * Lead      - gemini-4-hydrogen (thinking: medium), ONE short call at the start: gives each
                worker a distinct direction. (Carbon is deliberately kept out of the critical path.)
  * Workers   - gemini-4-hydrogen (thinking: low), N in parallel, 2 plans each per round.
  * Reviewer  - gemini-4-neon looks at the floorplan IMAGE of the current best after every round;
                its note goes into the next round's worker prompts.
  * Judge     - layout.model.judge: geometry + constraints + systematic offset + ngspice at 15 PVT
                points with finger counts and wiring capacitance. The LLMs never decide pass/fail.
Baselines at the same candidate budget: --baseline random | tpe (same discrete plan space).
The naive plan (nf=1, AABB, no dummies) is judged first as the "before" and is not counted.

  python -u -m layout.crew --run runs/swarm/guard_s0 --candidates 16 --run-id crew_demo
  python -u -m layout.crew --run runs/swarm/guard_s0 --candidates 16 --baseline random --run-id rand_demo
  python -u -m layout.crew --design my_design.json        # {"params": {...}} or a bare params dict (SI)
"""
from __future__ import annotations

import argparse
import base64
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from sizing import opamp2stg as oa

from layout import DISCLAIMER
from layout import model as lm
from layout import render as lr

WORKER_MODEL = "gemini-4-hydrogen"
LEAD_MODEL = "gemini-4-hydrogen"
CRITIC_MODEL = "gemini-4-neon"
RUN_ROOT = oa.ROOT / "runs" / "layout"
DECKS_PER_CANDIDATE = len(oa.PVT15)

GENERIC_DIRECTIONS = [
    "Compact and safe: choose finger counts so every finger is 4-8 um wide, ABBA for both pairs, dummies on, three rows.",
    "Minimum area: push the bounding box toward a square, balance the row widths so no row leaves empty space; try dropping dummies and moving small groups (tail, sink) next to a wider one.",
    "Matching first: zero systematic offset in BOTH x and y (ABBA with even nf, or AB/BA with even nf); then shrink area.",
    "Protect the simulation margin: unity-gain frequency is the tightest target, so keep finger counts moderate and wires short (put groups that share a net above each other); explore row orders.",
]


# --------------------------------------------------------------------------- design loading

def load_design(run: str | None, design: str | None) -> tuple[dict, str]:
    path = Path(run) / "summary.json" if run else Path(design)
    d = json.loads(path.read_text())
    circuit = d.get("circuit", "diff")
    if circuit != "diff":
        raise SystemExit(f"layout planning supports only circuit 'diff'; {path} is for {circuit!r}")
    params = d.get("best_params") or d.get("params") or d
    unknown = [k for k in params if k not in lm.oa.CIRCUITS["diff"]["space"]]
    if unknown or not params:
        raise SystemExit(f"{path}: not a sizing of circuit 'diff' (unexpected knobs {unknown}); "
                         f"layout planning supports only 'diff'")
    return oa.clamp({k: float(v) for k, v in params.items()}), str(path)


# --------------------------------------------------------------------------- LLM plumbing

class LLM:
    def __init__(self, run_dir: Path):
        from sizing.managed import client
        self.client = client()
        self.run_dir, self.lock, self.calls = run_dir, threading.Lock(), 0
        self.tokens = {"in": 0, "out": 0, "thought": 0}

    def _log(self, rec: dict, usage=None) -> None:
        with self.lock:
            if usage is not None:
                self.calls += 1
                self.tokens["in"] += usage.total_input_tokens or 0
                self.tokens["out"] += usage.total_output_tokens or 0
                self.tokens["thought"] += usage.total_thought_tokens or 0
                rec.update({"in": usage.total_input_tokens, "out": usage.total_output_tokens,
                            "thought": usage.total_thought_tokens})
            with open(self.run_dir / "llm_calls.jsonl", "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")

    def json_call(self, model: str, prompt: str, schema: dict, thinking: str, tag: str,
                  timeout: float = 60, attempts: int = 2) -> dict:
        last = None
        for attempt in range(attempts):
            t0 = time.perf_counter()
            try:
                r = self.client.interactions.create(
                    model=model, input=prompt, generation_config={"thinking_level": thinking},
                    response_format={"type": "text", "mime_type": "application/json", "schema": schema},
                    timeout=timeout)
                data = json.loads(r.output_text)
                self._log({"tag": tag, "model": model, "thinking": thinking,
                           "latency_s": round(time.perf_counter() - t0, 2), "prompt": prompt,
                           "response": data}, r.usage)
                return data
            except Exception as e:       # API or JSON error: retry once
                last = e
                self._log({"tag": tag, "model": model, "error": repr(e)[:300],
                           "latency_s": round(time.perf_counter() - t0, 2)})
                time.sleep(1.0 + attempt)
        raise RuntimeError(f"{tag}: LLM call failed: {last}")

    def image_call(self, model: str, prompt: str, png: Path, tag: str, timeout: float = 90) -> tuple[str, float]:
        t0 = time.perf_counter()
        r = self.client.interactions.create(
            model=model, generation_config={"thinking_level": "low"}, timeout=timeout,
            input=[{"type": "text", "text": prompt},
                   {"type": "image", "mime_type": "image/png",
                    "data": base64.b64encode(png.read_bytes()).decode()}])
        note = (r.output_text or "").strip()
        dt = time.perf_counter() - t0
        self._log({"tag": tag, "model": model, "thinking": "low", "latency_s": round(dt, 2),
                   "image": png.name, "prompt": prompt, "response": note}, r.usage)
        return note, dt


def plan_schema(k: int) -> dict:
    props = {f"nf_{r}": {"type": "integer", "minimum": 1, "maximum": 32,
                         "description": f"fingers per device for role {r}"} for r in lm.ROLES}
    props["pattern_in"] = {"type": "string", "enum": list(lm.PATTERNS)}
    props["pattern_load"] = {"type": "string", "enum": list(lm.PATTERNS)}
    props["dummies"] = {"type": "boolean"}
    props["rows"] = {"type": "array", "description": "rows top to bottom; each row is a list of group names; "
                     "every group exactly once; load/drv (PMOS) rows first",
                     "items": {"type": "array", "items": {"type": "string", "enum": lm.ROLES}}}
    props["rationale"] = {"type": "string", "description": "one sentence: what you changed and why"}
    return {"type": "object", "properties": {
        "plans": {"type": "array", "minItems": k, "maxItems": k,
                  "items": {"type": "object", "properties": props, "required": list(props)}}},
        "required": ["plans"]}


LEAD_SCHEMA = {"type": "object", "properties": {
    "diagnosis": {"type": "string", "description": "two sentences: what dominates area and offset here"},
    "directions": {"type": "array", "items": {"type": "string",
                   "description": "one concrete, distinct search direction for one worker (max 40 words)"}}},
    "required": ["diagnosis", "directions"]}


# --------------------------------------------------------------------------- plan space (baselines)

def _row_options() -> tuple[list, list]:
    import itertools
    p_opts = [[["load", "drv"]], [["drv", "load"]], [["load"], ["drv"]], [["drv"], ["load"]]]
    n_opts = []
    for perm in itertools.permutations(["in", "tail", "sink"]):
        a, b, c = perm
        n_opts += [[[a, b, c]], [[a], [b, c]], [[a, b], [c]], [[a], [b], [c]]]
    return p_opts, n_opts


P_ROWS, N_ROWS = _row_options()


def legal_nf(params: dict) -> dict:
    """Baselines search only finger counts whose finger width is legal (static bound, known up front)."""
    wl = lm.role_wl_um(params)
    out = {}
    for r in lm.ROLES:
        ok = [n for n in lm.NF_CHOICES[r] if lm.WF_MIN <= wl[r][0] / n <= lm.WF_MAX]
        out[r] = ok or [min(lm.NF_CHOICES[r], key=lambda n: abs(wl[r][0] / n - 5.0))]
    return out


def random_plan(rng: random.Random, nf_choices: dict) -> dict:
    return {"nf": {r: rng.choice(nf_choices[r]) for r in lm.ROLES},
            "pattern": {r: rng.choice(lm.PATTERNS) for r in lm.MATCHED},
            "dummies": rng.random() < 0.5,
            "rows": [list(r) for r in rng.choice(P_ROWS)] + [list(r) for r in rng.choice(N_ROWS)]}


# --------------------------------------------------------------------------- the run

class LayoutRun:
    def __init__(self, params: dict, design_from: str, run_id: str, candidates: int = 16,
                 workers: int = 4, per_worker: int = 2, critic: bool = True, mode: str = "crew",
                 seed: int = 0, jobs: int = 8):
        lm.require_diff()
        self.params, self.design_from, self.mode = params, design_from, mode
        self.max_c, self.nw, self.k, self.use_critic, self.seed, self.jobs = candidates, workers, per_worker, critic, seed, jobs
        self.dir = RUN_ROOT / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        for f in ("events.jsonl", "history.jsonl", "llm_calls.jsonl"):
            (self.dir / f).unlink(missing_ok=True)
        self.run_id = run_id
        self._t0 = time.time()
        self.budget = oa.Budget((candidates + 1) * DECKS_PER_CANDIDATE)   # +1: the naive "before"
        self.history: list[dict] = []
        self.seen: set[str] = set()
        self.llm = LLM(self.dir) if mode == "crew" else None
        self.critic_note, self.critic_notes = "", []
        self.directions = list(GENERIC_DIRECTIONS)
        self.diagnosis = ""
        self._pool = ThreadPoolExecutor(1)
        self._critic_pending = None
        self._reviewed_n = None
        self.naive = None
        self.gm_ratio = 1.0

    # ---- bookkeeping
    def emit(self, kind: str, **data) -> None:
        with open(self.dir / "events.jsonl", "a") as f:
            f.write(json.dumps({"t": round(time.time() - self._t0, 2), "type": kind, **data}, default=str) + "\n")

    def best(self, n: int = 1) -> list[dict]:
        return sorted(self.history, key=lambda e: e["cost"])[:n]

    def remaining(self) -> int:
        return self.max_c - len(self.history)

    def _judge(self, plan: dict, it: int, worker: str, rationale: str) -> dict | None:
        """Judge one plan (counts against the budget). Duplicates are skipped and not counted."""
        plan = lm.normalize_plan(plan)
        key = lm.plan_key(plan)
        if key in self.seen or self.remaining() <= 0:
            return None
        self.seen.add(key)
        res = lm.judge(self.params, plan, jobs=self.jobs, budget=self.budget, gm_ratio_fallback=self.gm_ratio)
        n = len(self.history) + 1
        prev_best = self.best()[0]["cost"] if self.history else float("inf")
        img = f"cand_{n:03d}.png"
        lr.save_floorplan(self.params, plan, res, self.dir / img, f"plan #{n}")
        e = dict(n=n, iter=it, worker=worker, rationale=rationale, image=img, **res)
        self.history.append(e)
        with open(self.dir / "history.jsonl", "a") as f:
            f.write(json.dumps({"t": round(time.time() - self._t0, 2), **e}, default=str) + "\n")
        self.emit("candidate", n=n, iter=it, worker=worker, plan=plan, passed=res["passed"],
                  feasible=res["feasible"], area_um2=res["area_um2"], width_um=res["width_um"],
                  height_um=res["height_um"], aspect=res["aspect"], offset_uV=res["offset_uV"],
                  cost=res["cost"], pm_deg=res["pm_deg"], ugf_hz=res["ugf_hz"], dc_gain_db=res["dc_gain_db"],
                  wirelength_um=res["wirelength_um"], violations=res["violations"],
                  sim_failures=res["sim_failures"], simulated=res["simulated"], image=img,
                  rationale=rationale, new_best=res["cost"] < prev_best)
        flag = "FEASIBLE" if res["feasible"] else ("sim-pass, rule violation" if res["passed"] else "infeasible")
        star = " *best*" if res["cost"] < prev_best else ""
        ugf = f"{res['ugf_hz']/1e6:.2f}MHz" if res["ugf_hz"] else "-"
        pm = f"{res['pm_deg']:.1f}" if res["pm_deg"] is not None else "-"
        print(f"[{time.time()-self._t0:6.1f}s] #{n:02d} {worker:6s} {flag:24s} cost={res['cost']:.3f} "
              f"area={res['area_um2']:.0f}um2 ({res['width_um']:.1f}x{res['height_um']:.1f}) "
              f"off={res['offset_uV']:.1f}uV PM={pm} UGF={ugf}{star}\n          {lm.plan_short(plan)}", flush=True)
        return e

    # ---- prompts
    def _sized_table(self) -> str:
        wl = lm.role_wl_um(self.params)
        out = ["role   type   W_total(um)  L(um)  pitch(um)  fingers/group  legal nf (finger width 0.42-10 um)"]
        for r in lm.ROLES:
            w, L = wl[r]
            lo = max(1, int(-(-w // lm.WF_MAX)))
            hi = max(1, int(w // lm.WF_MIN))
            mult = {"in": "2*nf", "load": "2*nf", "tail": "4*nf", "drv": "3*nf", "sink": "6*nf"}[r]
            out.append(f"{r:6s} {'PMOS' if lm.POLARITY[r]=='p' else 'NMOS'}   {w:9.2f}  {L:6.2f}  "
                       f"{L+lm.SD_CONTACTED:8.2f}  {mult:13s}  {lo}..{hi}")
        return "\n".join(out)

    def _digest(self, entries: list[dict]) -> str:
        out = []
        for e in entries:
            pm = f"{e['pm_deg']:.1f}" if e["pm_deg"] is not None else "-"
            ugf = f"{e['ugf_hz']/1e6:.2f}" if e["ugf_hz"] else "-"
            out.append(f"#{e['n']} cost={e['cost']:.3f} {'FEASIBLE' if e['feasible'] else 'INFEASIBLE'} "
                       f"area={e['area_um2']:.0f} um2 ({e['width_um']:.1f} x {e['height_um']:.1f}, aspect {e['aspect']:.2f}) "
                       f"offset={e['offset_uV']:.1f} uV (x {e['offset_detail']['x_uV']}, y {e['offset_detail']['y_uV']}) "
                       f"worst PM={pm} deg UGF={ugf} MHz wire={e['wirelength_um']:.0f} um\n"
                       f"   plan: {json.dumps(e['plan'])}"
                       + (f"\n   violations: {'; '.join(e['violations'] + e['sim_failures'][:2])}"
                          if not e["feasible"] else ""))
        return "\n".join(out) if out else "(none yet)"

    def _context(self) -> str:
        tgt = oa.guarded(oa.DEFAULT_SPEC)
        nv = self.naive
        return f"""TASK: plan the layout of a sized SKY130 two-stage Miller op-amp (planning only: finger counts,
matched-pair patterns, dummies, row floorplan - no routing). A deterministic judge scores every plan.

SIZED DEVICES (fixed; you only choose how to draw them):
{self._sized_table()}
Groups: in = input pair (matched A/B), load = PMOS mirror load (matched A/B), tail = tail cascode stack,
drv = stage-2 PMOS driver, sink = stage-2 cascode sink. Nets: stage-1 output joins load, in and drv;
the mirror node joins load and in; the common-source node joins in and tail; vout joins drv and sink.

{lm.RULES_TEXT}
SIMULATION TARGET at all 15 PVT points: PM >= {tgt['pm_deg'][1]:.0f} deg, UGF >= {tgt['ugf_hz'][1]/1e6:.1f} MHz,
DC gain >= {tgt['dc_gain_db'][1]:.0f} dB. The sized design has little UGF margin, so extra capacitance can fail it.

NAIVE PLAN (the "before", nf=1 everywhere, AABB, no dummies): area A_naive = {nv['area_um2']:.0f} um2
({nv['width_um']:.1f} x {nv['height_um']:.1f} um, aspect {nv['aspect']:.2f}), offset {nv['offset_uV']:.1f} uV,
violations: {'; '.join(nv['violations']) or 'none'}."""

    def lead(self) -> None:
        prompt = self._context() + f"""

You are the layout lead. In two sentences say what dominates bounding-box area and offset for THIS
design, then give exactly {self.nw} distinct, concrete search directions (one per worker, max 40 words each),
e.g. which finger widths to aim for, how many rows, which groups share a row, which patterns."""
        try:
            out = self.llm.json_call(LEAD_MODEL, prompt, LEAD_SCHEMA, "medium", "lead", timeout=60, attempts=1)
            dirs = [str(d) for d in out.get("directions", []) if d][: self.nw]
            if dirs:
                self.directions = dirs + GENERIC_DIRECTIONS[len(dirs):]
            self.diagnosis = str(out.get("diagnosis", ""))
            self.emit("plan", model=LEAD_MODEL, diagnosis=self.diagnosis, directions=self.directions[: self.nw])
            print(f"[lead] {self.diagnosis}", flush=True)
        except Exception as e:
            print(f"[lead] failed ({e}); using generic directions", flush=True)

    def worker(self, it: int, idx: int) -> list[tuple[dict, str]]:
        name = f"w{idx}"
        mine = [e for e in self.history if e["worker"] == name][-3:]
        prompt = self._context() + f"""

LEAD'S DIAGNOSIS: {self.diagnosis or '(none)'}
YOUR DIRECTION: {self.directions[idx % len(self.directions)]}

BEST PLANS SO FAR (judge results):
{self._digest(self.best(3))}
{('FLOORPLAN REVIEWER (looked at the image of the current best plan): ' + self.critic_note) if self.critic_note else ''}

YOUR OWN RECENT PLANS:
{self._digest(mine)}

Propose exactly {self.k} new, distinct layout plans as JSON (fields nf_in, nf_load, nf_tail, nf_drv, nf_sink,
pattern_in, pattern_load, dummies, rows, rationale). Do not resubmit a plan already tried. Check each plan
yourself first: finger widths legal, estimate row widths and total height so the aspect ratio is legal."""
        out = self.llm.json_call(WORKER_MODEL, prompt, plan_schema(self.k), "low", f"worker{idx}_it{it}")
        plans = []
        for c in (out or {}).get("plans", [])[: self.k]:
            plans.append((lm.normalize_plan(c), str(c.get("rationale", ""))[:300]))
        return plans

    # ---- reviewer (vision)
    def _critic(self, it: int, entry: dict) -> str:
        png = self.dir / entry["image"]
        prompt = (f"You are the layout reviewer on an analog design team. The image is the FLOORPLAN of the team's "
                  f"current best layout plan for a two-stage op-amp (planning stage: finger rectangles in rows, "
                  f"colored by device group, A/B letters on matched-pair fingers, hatched gray = dummy fingers, "
                  f"dashed outline = N-well with the PMOS rows, black box = bounding box). Judge numbers: area "
                  f"{entry['area_um2']:.0f} um2 ({entry['width_um']:.1f} x {entry['height_um']:.1f} um), systematic "
                  f"offset {entry['offset_uV']:.1f} uV, {'feasible' if entry['feasible'] else 'INFEASIBLE'}. "
                  f"Cost = area / naive area + offset_uV / 100; aspect ratio (width/height) must stay within "
                  f"[{lm.ASPECT_MIN}, {lm.ASPECT_MAX}]. Look at the picture: is each matched pair symmetric about its "
                  f"centre, are the rows balanced in width or is there empty white space inside the bounding box, "
                  f"is the block close to square, do the small groups waste a whole row? In at most 70 words say what "
                  f"you SEE and give one or two concrete changes (finger counts, which groups share a row, row "
                  f"order, pattern, dummies).")
        note, dt = self.llm.image_call(CRITIC_MODEL, prompt, png, f"critic_it{it}")
        self.emit("critic", iter=it, n=entry["n"], image=png.name, note=note, model=CRITIC_MODEL,
                  latency_s=round(dt, 1))
        print(f"[reviewer it{it}, plan #{entry['n']}, {dt:.0f}s] {note}", flush=True)
        return note

    def _collect_critic(self, wait_s: float) -> None:
        if self._critic_pending is None:
            return
        try:
            note = self._critic_pending.result(timeout=wait_s)
            if note:
                self.critic_note = note
                self.critic_notes.append(note)
            self._critic_pending = None
        except TimeoutError:
            print(f"[reviewer] still thinking after {wait_s:.0f}s; workers go on without the note", flush=True)
        except Exception as e:
            print(f"[reviewer] failed: {e}", flush=True)
            self._critic_pending = None

    # ---- search strategies
    def run_crew(self) -> None:
        self.lead()
        it = 0
        while self.remaining() > 0 and it < 8:
            n_w = min(self.nw, -(-self.remaining() // self.k))
            with ThreadPoolExecutor(n_w) as ex:
                futs = [ex.submit(self.worker, it, i) for i in range(n_w)]
                results = []
                for i, f in enumerate(futs):
                    try:
                        results.append((i, f.result()))
                    except Exception as e:
                        print(f"[w{i}] failed: {e}", flush=True)
            for i, plans in results:
                for plan, why in plans:
                    self._judge(plan, it, f"w{i}", why)
            if self.use_critic and self.history and self._critic_pending is None:
                b = self.best()[0]
                if b["n"] != self._reviewed_n:          # one review per distinct best plan
                    self._reviewed_n = b["n"]
                    self._critic_pending = self._pool.submit(self._critic, it, b)
                else:
                    print(f"[reviewer] best plan unchanged (#{b['n']}); previous note stands", flush=True)
            self._collect_critic(wait_s=40 if self.remaining() > 0 else 60)
            it += 1

    def run_random(self) -> None:
        rng = random.Random(self.seed)
        nfc = legal_nf(self.params)
        tries = 0
        while self.remaining() > 0 and tries < 50 * self.max_c:
            tries += 1
            self._judge(random_plan(rng, nfc), 0, "random", "uniform random sample of the plan space (legal finger widths)")

    def run_tpe(self) -> None:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        nfc = legal_nf(self.params)

        def objective(trial):
            plan = {"nf": {r: trial.suggest_categorical(f"nf_{r}", nfc[r]) for r in lm.ROLES},
                    "pattern": {r: trial.suggest_categorical(f"pattern_{r}", list(lm.PATTERNS)) for r in lm.MATCHED},
                    "dummies": trial.suggest_categorical("dummies", [False, True]),
                    "rows": [list(r) for r in P_ROWS[trial.suggest_int("p_rows", 0, len(P_ROWS) - 1)]]
                            + [list(r) for r in N_ROWS[trial.suggest_int("n_rows", 0, len(N_ROWS) - 1)]]}
            key = lm.plan_key(plan)
            e = self._judge(plan, 0, "tpe", "Optuna TPE proposal")
            if e is None:                       # duplicate: reuse the known cost, do not spend budget
                if self.remaining() <= 0:
                    trial.study.stop()
                return next((h["cost"] for h in self.history if lm.plan_key(h["plan"]) == key), 20.0)
            if self.remaining() <= 0:
                trial.study.stop()
            return e["cost"]

        study = optuna.create_study(sampler=optuna.samplers.TPESampler(seed=self.seed, n_startup_trials=6))
        study.optimize(objective, n_trials=self.max_c * 6)

    # ---- main
    def run(self) -> dict:
        models = ({"worker": WORKER_MODEL, "lead": LEAD_MODEL, "reviewer": CRITIC_MODEL if self.use_critic else None}
                  if self.mode == "crew" else {"baseline": self.mode})
        print(f"== layout planning ({self.mode}) run {self.run_id}: {self.max_c} candidates x {DECKS_PER_CANDIDATE} decks ==\n"
              f"   {DISCLAIMER}", flush=True)
        self.naive = lm.judge(self.params, lm.NAIVE_PLAN, jobs=self.jobs, budget=self.budget)
        if self.naive.get("gm_ratio"):
            self.gm_ratio = self.naive["gm_ratio"]
        lr.save_floorplan(self.params, lm.NAIVE_PLAN, self.naive, self.dir / "naive.png", "naive plan", svg=True)
        nv = self.naive
        print(f"[naive] area={nv['area_um2']:.0f}um2 ({nv['width_um']:.1f}x{nv['height_um']:.1f}, aspect {nv['aspect']:.2f}) "
              f"offset={nv['offset_uV']:.1f}uV sim={'PASS' if nv['passed'] else 'FAIL'} "
              f"violations={len(nv['violations'])}", flush=True)
        self.emit("start", run_id=self.run_id, mode=self.mode, design_from=self.design_from,
                  sized_params=self.params, sized_display={r: {"W_um": round(w, 3), "L_um": round(L, 3)}
                                                           for r, (w, L) in lm.role_wl_um(self.params).items()},
                  naive={"area_um2": nv["area_um2"], "width_um": nv["width_um"], "height_um": nv["height_um"],
                         "aspect": nv["aspect"], "offset_uV": nv["offset_uV"], "passed": nv["passed"],
                         "feasible": nv["feasible"], "violations": nv["violations"], "pm_deg": nv["pm_deg"],
                         "ugf_hz": nv["ugf_hz"], "plan": nv["plan"], "image": "naive.png"},
                  max_candidates=self.max_c, models=models, disclaimer=DISCLAIMER,
                  assumptions={"vth_gradient_uV_per_um": lm.VTH_GRADIENT_UV_PER_UM,
                               "wire_cap_fF_per_um": lm.WIRE_CAP_F_PER_UM * 1e15,
                               "cost": "feasible first; area_um2/A_naive + offset_uV/100"})
        try:
            {"crew": self.run_crew, "random": self.run_random, "tpe": self.run_tpe}[self.mode]()
        except oa.BudgetExceeded as e:
            print(f"[budget] {e}", flush=True)
        return self.finish()

    def finish(self) -> dict:
        best = self.best()[0] if self.history else None
        wall = round(time.time() - self._t0, 1)
        summary = dict(run_id=self.run_id, mode=self.mode, disclaimer=DISCLAIMER, design_from=self.design_from,
                       sized_params=self.params, candidates=len(self.history), decks=self.budget.used,
                       naive={k: v for k, v in self.naive.items()}, wall_s=wall,
                       llm_calls=self.llm.calls if self.llm else 0, tokens=self.llm.tokens if self.llm else {},
                       critic_notes=self.critic_notes, lead_diagnosis=self.diagnosis,
                       n_feasible=sum(e["feasible"] for e in self.history),
                       assumptions={"vth_gradient_uV_per_um": lm.VTH_GRADIENT_UV_PER_UM,
                                    "wire_cap_fF_per_um": lm.WIRE_CAP_F_PER_UM * 1e15,
                                    "finger_pitch_um": f"L + {lm.SD_CONTACTED}", "end_diffusion_um": lm.END_DIFF,
                                    "device_height_um": f"finger width + {lm.CELL_OVERHEAD}",
                                    "row_space_um": lm.ROW_SPACE, "well_gap_um": lm.WELL_GAP,
                                    "finger_width_um": [lm.WF_MIN, lm.WF_MAX],
                                    "aspect": [lm.ASPECT_MIN, lm.ASPECT_MAX],
                                    "cost": "feasible first; area_um2/A_naive + offset_uV/100"})
        done = dict(best_n=None, passed=False, feasible=False, area_um2=None, offset_uV=None,
                    naive_area_um2=self.naive["area_um2"], naive_offset_uV=self.naive["offset_uV"],
                    area_reduction_pct=None, candidates=len(self.history),
                    llm_calls=summary["llm_calls"], wall_s=wall)
        if best:
            red = round(100.0 * (1.0 - best["area_um2"] / self.naive["area_um2"]), 1)
            first = next((e["n"] for e in self.history if e["feasible"]), None)
            done.update(best_n=best["n"], passed=best["passed"], feasible=best["feasible"],
                        area_um2=best["area_um2"], offset_uV=best["offset_uV"], area_reduction_pct=red,
                        cost=best["cost"], width_um=best["width_um"], height_um=best["height_um"],
                        pm_deg=best["pm_deg"], ugf_hz=best["ugf_hz"], best_plan=best["plan"],
                        best_image="best.png", before_after="before_after.png",
                        candidates_to_first_feasible=first)
            summary.update(best={k: v for k, v in best.items()}, best_n=best["n"], best_plan=best["plan"],
                           best_passed=best["passed"], best_feasible=best["feasible"],
                           area_um2=best["area_um2"], offset_uV=best["offset_uV"], cost=best["cost"],
                           naive_area_um2=self.naive["area_um2"], naive_offset_uV=self.naive["offset_uV"],
                           area_reduction_pct=red, candidates_to_first_feasible=first,
                           best_cost_trace=[min(e["cost"] for e in self.history[: i + 1])
                                            for i in range(len(self.history))])
            lr.save_floorplan(self.params, best["plan"], best, self.dir / "best.png", f"best plan (#{best['n']})", svg=True)
            lr.save_before_after(self.params, lm.NAIVE_PLAN, self.naive, best["plan"], best,
                                 self.dir / "before_after.png")
        (self.dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
        self.emit("done", **done)
        if best:
            print(f"== done ({self.mode}): best #{best['n']} {'FEASIBLE' if best['feasible'] else 'INFEASIBLE'} "
                  f"area {self.naive['area_um2']:.0f} -> {best['area_um2']:.0f} um2 ({done['area_reduction_pct']}% smaller), "
                  f"offset {self.naive['offset_uV']:.1f} -> {best['offset_uV']:.1f} uV, cost {best['cost']:.3f}, "
                  f"{len(self.history)} candidates, {summary['llm_calls']} LLM calls, {wall}s ==\n"
                  f"   {lm.plan_short(best['plan'])}\n   outputs: {self.dir}\n   {DISCLAIMER}", flush=True)
        return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--run", help="a finished sizing run directory (reads summary.json best_params)")
    src.add_argument("--design", help='JSON file with {"params": {...}} (SI units) or a bare params dict')
    ap.add_argument("--candidates", type=int, default=16)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--per-worker", type=int, default=2)
    ap.add_argument("--run-id")
    ap.add_argument("--baseline", choices=("random", "tpe"))
    ap.add_argument("--no-critic", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=8, help="parallel ngspice decks per candidate")
    a = ap.parse_args()
    if oa.CIRCUIT != "diff":
        raise SystemExit(f"layout planning supports only circuit 'diff' (OPAMP_CIRCUIT={oa.CIRCUIT!r})")
    params, src_path = load_design(a.run, a.design)
    mode = a.baseline or "crew"
    run_id = a.run_id or f"{mode}_{datetime.now().strftime('%m%d_%H%M%S')}"
    LayoutRun(params, src_path, run_id, candidates=a.candidates, workers=a.workers, per_worker=a.per_worker,
              critic=not a.no_critic, mode=mode, seed=a.seed, jobs=a.jobs).run()


if __name__ == "__main__":
    main()
