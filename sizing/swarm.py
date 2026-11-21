"""Gemini agent swarm for the per-role two-stage op-amp.

Roles (one job per model):
  * Architect  - gemini-4-carbon (thinking: medium). Reads the circuit card, spec and the full
                 history; writes a diagnosis and gives every worker a distinct search direction.
                 Called at the start and again whenever the swarm stalls.
  * Workers    - gemini-4-hydrogen (thinking: low), N in parallel. Each proposes K concrete
                 candidate sizings along its direction, given the best designs so far and its
                 own past attempts.
  * Judge      - ngspice at all 15 PVT points (sizing.opamp2stg.evaluate). The LLMs never
                 decide pass/fail.
Budget is counted in candidates (15 ngspice decks each), identical to the Optuna baselines;
the published starting design counts as candidate 1 for both.

python -m sizing.swarm --workers 4 --per-worker 2 --candidates 40
python -m sizing.swarm --dry      # print the prompts, no API calls
"""
from __future__ import annotations

import argparse
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sizing import opamp2stg as oa
from sizing.baseline import constraint_values

ARCHITECT_MODEL = "gemini-4-carbon"
ARCH_THINKING = "medium"
ARCH_TIMEOUT = 120.0          # s; Carbon latency varies (42-63 s typical, one 524 s outlier)
FALLBACK_ARCHITECT = "gemini-4-hydrogen"   # same prompt at thinking "high" if Carbon times out
FALLBACK_TIMEOUT = 90.0
WORKER_MODEL = "gemini-4-hydrogen"
CRITIC_MODEL = "gemini-4-neon"               # vision: reads the Bode plot image of the best design

# Used only in --fast-start (live) mode until the architect's plan lands.
GENERIC_DIRECTIONS = [
    {"focus": "stability first", "knobs": ["Cc", "Rz", "W_sink", "W_drv"],
     "instructions": "Raise Cc and retune Rz until phase margin meets target at the worst corner; keep unity-gain frequency above target."},
    {"focus": "saturation headroom", "knobs": ["W_in", "W_tail", "Ibias"],
     "instructions": "Find the role with the worst saturation margin and fix it: widen devices whose Vgs eats the headroom, keep Ibias near the bias network's design point."},
    {"focus": "gain", "knobs": ["L_in", "L_load", "L_drv"],
     "instructions": "Lengthen gain-setting devices to lift worst-corner DC gain; watch the speed and phase-margin cost."},
    {"focus": "cut current", "knobs": ["W_tail", "W_sink", "Ibias"],
     "instructions": "Reduce branch currents toward the limit while holding bandwidth; trade spare bandwidth for power."},
    {"focus": "bold combined move", "knobs": [],
     "instructions": "Change several knobs at once toward a design you expect to pass everything; accept risk."},
    {"focus": "worst-corner repair", "knobs": [],
     "instructions": "Look only at the worst PVT point of the best design and fix exactly what fails there with minimal changes."},
    {"focus": "careful refinement", "knobs": [],
     "instructions": "Make small moves (10-25%) around the best design, one or two knobs at a time."},
    {"focus": "contrarian", "knobs": [],
     "instructions": "Try the opposite of what the best designs did on one major knob to test whether the team is stuck in a local optimum."},
]


def render_bode(er: "oa.EvalResult", path, title: str = "") -> None:
    """15-corner loop-gain Bode plot as a PNG (what the plot reviewer looks at)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"tt": "#2a78d6", "ff": "#eb6834", "ss": "#1baf7a", "sf": "#6c56d9", "fs": "#e87ba4"}
    styles = {-40: ":", 27: "-", 125: "--"}
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    for r in er.points:
        c = r.curves
        if not c:
            continue
        kw = dict(color=colors.get(r.corner, "k"), ls=styles.get(int(r.temp), "-"), lw=1.3,
                  label=f"{r.corner} {r.temp:g}C")
        a1.semilogx(c["freq_hz"], c["lg_db"], **kw)
        a2.semilogx(c["freq_hz"], c["lg_ph"], **kw)
    a1.axhline(0, color="k", lw=0.8)
    a2.axhline(-180, color="k", lw=0.8)
    a1.set_ylabel("loop gain (dB)"); a2.set_ylabel("phase (deg)"); a2.set_xlabel("frequency (Hz)")
    a1.set_ylim(-60, 100); a2.set_ylim(-280, 10)
    a1.grid(alpha=0.3, which="both"); a2.grid(alpha=0.3, which="both")
    a1.legend(ncol=5, fontsize=6, loc="upper right")
    a1.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
RUN_ROOT = oa.ROOT / "runs" / "swarm"

# knob -> (display unit name, factor from SI to display unit), derived from the knob name
class _Units(dict):
    def __missing__(self, k):
        if k.startswith(("W_", "L_")):
            return ("um", 1e6)
        return {"Ibias": ("uA", 1e6), "Cc": ("pF", 1e12), "Rz": ("kohm", 1e-3)}[k]


UNITS = _Units()


def to_display(p: dict) -> dict:
    return {f"{k}_{UNITS[k][0]}": round(v * UNITS[k][1], 4) for k, v in p.items()}


def from_display(d: dict) -> dict:
    out = {}
    for k in oa.SPACE:
        u, f = UNITS[k]
        key = f"{k}_{u}"
        if key in d and d[key] is not None:
            out[k] = float(d[key]) / f
    return out


CIRCUIT_CARD = """\
CIRCUIT: two-stage Miller op-amp, SkyWater SKY130, VDD = 1.8 V, simulated in ngspice.
Measured in a unity-gain loop-gain (stability) testbench, VCM = 0.9 V, load CL = 500 fF.
Stage 1: NMOS differential pair (role "in", nfet_01v8_lvt) with a PMOS current-mirror load
  (role "load", pfet_01v8_lvt), fed by an NMOS wide-swing cascode tail current source
  (role "tail": two stacked nfet_01v8_lvt devices, multiplier m=2).
Stage 2: PMOS common-source driver (role "drv", pfet_01v8_lvt, m=3) with an NMOS wide-swing
  cascode current-sink load (role "sink": two stacked nfet_01v8_lvt devices, m=3).
Compensation: Miller capacitor Cc in series with nulling resistor Rz, from stage-1 output to vout.
Bias: a FROZEN bias network (not tunable) mirrors Ibias into the tail and sink cascode gates;
  its reference devices are W = 5 um, L = 0.3 um, and it was designed around Ibias = 20 uA.
  Tail and sink L are fixed at 0.3 um to match it, so their W sets the current:
  I_tail ~= Ibias * 2 * W_tail / 5 um ;  I_sink ~= Ibias * 3 * W_sink / 5 um.
  Core current i_core = I_tail + I_sink (the bias network's own current is not counted).
Saturation rule (design target incl. guard band): every signal-path device must have
  |Vds| - |Vdsat| >= {SAT_HI} mV for roles in/load/drv and >= {SAT_LO} mV for tail/sink (bottom
  devices of the wide-swing cascodes), at EVERY PVT point.
PVT: 15 points = process corners {tt, ff, ss, sf, fs} x temperature {-40, 27, 125} C.
Note: in this PDK build the corner LABELS sf/fs behave swapped relative to their names;
  reason from the measured numbers, not from corner names.
"""


_COMMON_TAIL = """Stage 2: PMOS common-source driver (role "drv", pfet_01v8_lvt, m={M2}) with an NMOS wide-swing
  cascode current-sink load (role "sink": two stacked nfet_01v8_lvt devices, m={M2}).
Compensation: Miller capacitor Cc in series with nulling resistor Rz, from stage-1 output to vout.
Bias: a FROZEN bias network (not tunable) mirrors Ibias into all NMOS wide-swing cascode gates
  (Vbias_3 / Vbias_4) and the PMOS cascode gate (Vbias_2); its NMOS reference is W = 5 um,
  L = 0.3 um, and it was designed around Ibias = 20 uA. Devices that mirror it (tail, nsink, sink)
  keep L = 0.3 um, so their W sets their current. PMOS cascodes keep L = 0.7 um.
Saturation rule (design target incl. guard band): every signal-path device must have
  |Vds| - |Vdsat| >= {SAT_HI} mV, except the bottom/cascode devices of the wide-swing NMOS current
  sources (roles tail, nsink, sink) which need >= {SAT_LO} mV, at EVERY PVT point.
PVT: 15 points = process corners {tt, ff, ss, sf, fs} x temperature {-40, 27, 125} C.
Note: in this PDK build the corner LABELS sf/fs behave swapped relative to their names;
  reason from the measured numbers, not from corner names.
All transistors are low-Vt (nfet_01v8_lvt / pfet_01v8_lvt).
"""

CARDS = {"diff": CIRCUIT_CARD}
CARDS["fc"] = """\
CIRCUIT: two-stage op-amp with a FOLDED-CASCODE first stage, SkyWater SKY130, VDD = 1.8 V, ngspice.
Measured in a unity-gain loop-gain (stability) testbench, VCM = 0.9 V, load CL = 1 pF.
Stage 1 (folded cascode, single-ended output): NMOS differential pair (role "in"). Its drains fold
  into the sources of PMOS cascodes (role "pcas", gate at fixed bias Vbias_2). Above each fold node
  are PMOS current sources (role "psrc": four devices, two per side) whose gates are driven from the
  left branch, so they also act as the mirror that makes the output single-ended; their current is
  set by KCL (tail/2 + the NMOS sink current per side), their SIZE sets their Vsg and headroom.
  Below the PMOS cascodes are NMOS wide-swing cascode current sinks (role "nsink": two stacked
  devices per side). Tail: NMOS wide-swing cascode current source with two parallel branches
  (role "tail").
  Approximate currents: I_tail ~= Ibias * 2 * W_tail / 5 um ; each nsink side ~= Ibias * W_nsink / 5 um ;
  I_sink(stage 2) ~= Ibias * 3 * W_sink / 5 um.
  Core current i_core = I_tail + both nsink sides + stage-2 sink (bias network not counted).
""" + _COMMON_TAIL.replace("{M2}", "3")
CARDS["tele"] = """\
CIRCUIT: two-stage op-amp with a TELESCOPIC-CASCODE first stage, SkyWater SKY130, VDD = 1.8 V, ngspice.
Measured in a unity-gain loop-gain (stability) testbench, VCM = 0.9 V, load CL = 1 pF.
Stage 1 (telescopic cascode, single-ended output): NMOS differential pair (role "in") with NMOS
  cascodes stacked on top (role "ncas", gate at Vbias5), loaded by a cascoded PMOS current mirror:
  mirror devices at the top (role "load", gates from the left branch) and PMOS cascodes below them
  (role "pcas", gate at fixed bias Vbias_2). Tail: NMOS wide-swing cascode current source with two
  parallel branches (role "tail").
  Vbias5 comes from a FROZEN replica circuit inside the amplifier: a 1 um-wide copy of the input
  device biased at VCM with ~Ibias/5 of current, i.e. it assumes the input pair runs at the
  published current density (~4 uA per um of W_in at L_in = 0.3 um). If the input pair's current
  density or L changes, Vbias5 no longer tracks, and the input pair / NMOS cascodes can lose headroom.
  Approximate currents: I_tail ~= Ibias * 2 * W_tail / 5 um ; I_sink(stage 2) ~= Ibias * 6 * W_sink / 5 um.
  Core current i_core = I_tail + stage-2 sink (bias network and replica not counted).
""" + _COMMON_TAIL.replace("{M2}", "6")


def _card() -> str:
    hi = min(oa.sat_required(r) for r, v in oa.SAT_MARGIN.items() if v > 0)
    lo = min(oa.sat_required(r) for r in oa.SAT_MARGIN)
    return (CARDS[oa.CIRCUIT].replace("{SAT_HI}", f"{hi*1e3:.0f}").replace("{SAT_LO}", f"{lo*1e3:.0f}"))


CIRCUIT_CARD = CIRCUIT_CARD.replace("{SAT_HI}", f"{oa.sat_required('in')*1e3:.0f}").replace(
    "{SAT_LO}", f"{oa.sat_required('tail')*1e3:.0f}")


def knob_table() -> str:
    rows = []
    for k, (dflt, lo, hi, _) in oa.SPACE.items():
        u, f = UNITS[k]
        rows.append(f"  {k}_{u}: range [{lo * f:g}, {hi * f:g}], published value {dflt * f:g}")
    return "\n".join(rows)


def spec_text(spec: dict) -> str:
    names = {"pm_deg": "phase margin (deg)", "ugf_hz": "unity-gain frequency (Hz)",
             "dc_gain_db": "DC loop gain (dB)", "i_core_a": "core current (A)"}
    target = oa.guarded(spec)
    return "\n".join(f"  {names.get(m, m)} {op} {target[m][1]:.4g}   [at every PVT point; datasheet spec {b:g} "
                     f"+ guard band]" for m, (op, b) in spec.items())


def eval_text(er: oa.EvalResult, max_fail: int = 8) -> str:
    w = er.worst
    def g(m, scale=1.0, fmt="{:.1f}"):
        v = w.get(m)
        return f"{fmt.format(v[0] * scale)} (worst at {v[1]}/{v[2]})" if v and v[0] is not None else "n/a"
    # worst margin per role
    per_role = {}
    for r in er.points:
        for d in r.devices.values():
            if d["role"]:
                cur = per_role.get(d["role"])
                if cur is None or d["margin"] < cur[0]:
                    per_role[d["role"]] = (d["margin"], r.corner, r.temp)
    roles = ", ".join(f"{k} {v[0] * 1e3:.0f} mV@{v[1]}/{v[2]}" for k, v in sorted(per_role.items()))
    fails = er.failures
    return (f"{'PASS' if er.passed else 'FAIL'} | PM {g('pm_deg')} deg | UGF {g('ugf_hz', 1e-6, '{:.2f}')} MHz"
            f" | gain {g('dc_gain_db')} dB | i_core {g('i_core_a', 1e6)} uA"
            f"\n    worst saturation margin per role: {roles}"
            + (f"\n    {len(fails)} violations, e.g.: " + "; ".join(fails[:max_fail]) if fails else ""))


def point_record(r: oa.PointResult, er: oa.EvalResult) -> dict:
    """Compact per-PVT-point record for the visualizer (corner grid / playback)."""
    roles = {}
    for d in r.devices.values():
        if d["role"]:
            roles[d["role"]] = min(roles.get(d["role"], 9.0), d["margin"])
    fails = []
    for m, (op, b) in er.spec.items():
        v = getattr(r, m)
        if v is None or not oa._OPS[op](v, b):
            fails.append(m)
    fails += [f"sat:{k}" for k, v in roles.items() if v < oa.sat_required(k, er.guard)]
    if not r.ok:
        fails.append("sim")
    return {"corner": r.corner, "temp": r.temp, "pm_deg": r.pm_deg, "ugf_hz": r.ugf_hz,
            "dc_gain_db": r.dc_gain_db, "i_core_a": r.i_core_a,
            "sat_margin_v": roles, "fails": fails, "pass": not fails}


def score(er: oa.EvalResult) -> tuple:
    """Lower is better: feasible first (by core current), then by total violation."""
    if er.passed:
        return (0, er.worst["i_core_a"][0])
    v = constraint_values(er)
    return (1, sum(max(0.0, x) for x in v.values()))


# --------------------------------------------------------------------------- LLM plumbing

class LLM:
    def __init__(self, run_dir: Path, dry: bool = False):
        self.dry = dry
        self.run_dir = run_dir
        self.lock = threading.Lock()
        self.calls = 0
        self.tokens = {"in": 0, "out": 0, "thought": 0}
        if not dry:
            from google import genai
            self.client = genai.Client()

    def _log_error(self, tag, model, thinking, t0, err) -> None:
        with self.lock, open(self.run_dir / "llm_calls.jsonl", "a") as f:
            f.write(json.dumps({"tag": tag, "model": model, "thinking": thinking, "error": repr(err)[:300],
                                "latency_s": round(time.perf_counter() - t0, 2)}) + "\n")

    def json_call(self, model: str, prompt: str, schema: dict, thinking: str, tag: str,
                  timeout: float | None = None, attempts: int = 3) -> dict:
        if self.dry:
            print(f"\n===== {tag} ({model}, thinking={thinking}) =====\n{prompt}\n")
            return {}
        last_err = None
        for attempt in range(attempts):
            t0 = time.perf_counter()
            try:
                r = self.client.interactions.create(
                    model=model, input=prompt,
                    generation_config={"thinking_level": thinking},
                    response_format={"type": "text", "mime_type": "application/json", "schema": schema},
                    timeout=timeout,
                )
                data = json.loads(r.output_text)
                u = r.usage
                with self.lock:
                    self.calls += 1
                    self.tokens["in"] += u.total_input_tokens or 0
                    self.tokens["out"] += u.total_output_tokens or 0
                    self.tokens["thought"] += u.total_thought_tokens or 0
                    with open(self.run_dir / "llm_calls.jsonl", "a") as f:
                        f.write(json.dumps({"tag": tag, "model": model, "thinking": thinking,
                                            "latency_s": round(time.perf_counter() - t0, 2),
                                            "in": u.total_input_tokens, "out": u.total_output_tokens,
                                            "thought": u.total_thought_tokens,
                                            "prompt": prompt, "response": data}) + "\n")
                return data
            except Exception as e:  # API or JSON error: retry
                last_err = e
                self._log_error(tag, model, thinking, t0, e)
                if attempt + 1 < attempts:
                    time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"{tag}: LLM call failed after retries: {last_err}")


def candidate_schema(k: int) -> dict:
    props = {f"{name}_{UNITS[name][0]}": {"type": "number"} for name in oa.SPACE}
    props["rationale"] = {"type": "string", "description": "one sentence: what you changed and why"}
    return {"type": "object", "properties": {
        "candidates": {"type": "array", "minItems": k, "maxItems": k,
                       "items": {"type": "object", "properties": props, "required": list(props)}}},
        "required": ["candidates"]}


def architect_schema(n: int) -> dict:
    return {"type": "object", "properties": {
        "diagnosis": {"type": "string", "description": "what limits the best designs, in circuit terms"},
        "binding_constraint": {"type": "string", "description": "the single check that blocks the most candidates, e.g. 'sat:pcas at fs/-40'"},
        "likely_infeasible": {"type": "boolean", "description": "true only if the evidence says the target cannot be met with these knobs"},
        "directions": {"type": "array", "minItems": n, "maxItems": n, "items": {
            "type": "object", "properties": {
                "focus": {"type": "string", "description": "short name of the search direction"},
                "knobs": {"type": "array", "items": {"type": "string"}},
                "instructions": {"type": "string",
                                 "description": "concrete guidance for the worker: which knobs to move, which way, how far, and what trade-off to watch"}},
            "required": ["focus", "knobs", "instructions"]}}},
        "required": ["diagnosis", "binding_constraint", "likely_infeasible", "directions"]}


# --------------------------------------------------------------------------- swarm

@dataclass
class Entry:
    n: int
    iter: int
    worker: str
    params: dict
    er: oa.EvalResult
    rationale: str


class Swarm:
    def __init__(self, workers=4, per_worker=2, candidates=40, spec=None, dry=False, seed=0,
                 stall_rounds=2, run_id=None, replan_every=2, fast_start=False, critic=False,
                 start_params=None):
        self.start_params = start_params
        self.replan_every, self.fast_start, self.use_critic = replan_every, fast_start, critic
        self.critic_note, self._critic_pending = "", None
        self._t0 = time.time()
        self.n_workers, self.k = workers, per_worker
        self.max_candidates = candidates
        self.spec = dict(spec or oa.DEFAULT_SPEC)
        self.stall_rounds = stall_rounds
        self.rng = random.Random(seed)
        self.run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        self.dir = RUN_ROOT / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.llm = LLM(self.dir, dry)
        self.budget = oa.Budget(candidates * len(oa.PVT15))
        self.history: list[Entry] = []
        self.directions: list[dict] = []
        self.diagnosis = ""
        self.arch_log: list[dict] = []
        self._arch_pool = ThreadPoolExecutor(1)
        self._arch_pool2 = ThreadPoolExecutor(1)
        self._fast_pool = ThreadPoolExecutor(1)
        self._pending_fast = None
        self._pending = None

    # ---- bookkeeping
    def emit(self, kind: str, **data) -> None:
        """Typed event stream for the live view (runs/swarm/<id>/events.jsonl)."""
        with open(self.dir / "events.jsonl", "a") as f:
            f.write(json.dumps({"t": round(time.time() - self._t0, 2), "type": kind, **data}, default=str) + "\n")

    def failure_census(self) -> str:
        """Which checks block how many candidates, and the best margin reached per role.
        Added after the held-out runs: the crew kept missing one binding constraint without naming it."""
        n = len(self.history)
        if n < 3:
            return "  (too few candidates yet)"
        cnt, roles = {}, {}
        for e in self.history:
            fams, per = set(), {}
            for r in e.er.points:
                fams.update(point_record(r, e.er)["fails"])
                for d in r.devices.values():
                    if d["role"]:
                        per[d["role"]] = min(per.get(d["role"], 9.0), d["margin"])
            for f in fams:
                cnt[f] = cnt.get(f, 0) + 1
            for k, v in per.items():
                roles[k] = max(roles.get(k, -9.0), v)
        lines = [f"  {k}: violated by {v}/{n} candidates" for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])]
        lines.append("  best worst-case saturation margin any candidate reached, per role (mV): "
                     + ", ".join(f"{k} {v*1e3:.0f} (need {oa.sat_required(k)*1e3:.0f})"
                                 for k, v in sorted(roles.items())))
        never = [k for k, v in roles.items() if v < oa.sat_required(k)]
        if never:
            lines.append("  NEVER satisfied by any candidate so far: " + ", ".join(f"sat:{k}" for k in never)
                         + "  <- binding constraint: attack it first, or say why it cannot be met")
        return "\n".join(lines)

    def _log(self, e: Entry) -> None:
        rec = {"n": e.n, "iter": e.iter, "worker": e.worker, "params": e.params,
               "display": to_display(e.params), "passed": e.er.passed,
               "worst": {k: (v[0] if v else None) for k, v in e.er.worst.items()},
               "worst_at": {k: (f"{v[1]}/{v[2]}" if v else None) for k, v in e.er.worst.items()},
               "failures": e.er.failures[:12], "n_failures": len(e.er.failures),
               "rationale": e.rationale, "decks_used": self.budget.used,
               "passed_nominal": e.er.passed_nominal,
               "points": [point_record(r, e.er) for r in e.er.points],
               "target": {k: v[1] for k, v in e.er.spec.items()},
               "spec_nominal": {k: v[1] for k, v in e.er.spec_nominal.items()}}
        with open(self.dir / "history.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
        self.emit("candidate", n=e.n, iter=e.iter, worker=e.worker, passed=e.er.passed,
                  passed_nominal=e.er.passed_nominal, display=rec["display"], worst=rec["worst"],
                  points=[{"corner": p["corner"], "temp": p["temp"], "pass": p["pass"], "fails": p["fails"]}
                          for p in rec["points"]], rationale=e.rationale, n_failures=rec["n_failures"])

    def _evaluate(self, params: dict, it: int, worker: str, rationale: str) -> Entry | None:
        try:
            er = oa.evaluate(params, spec=self.spec, budget=self.budget)
        except oa.BudgetExceeded:
            return None
        e = Entry(len(self.history) + 1, it, worker, er.params, er, rationale)
        self.history.append(e)
        self._log(e)
        return e

    def best(self, n=1) -> list[Entry]:
        return sorted(self.history, key=lambda e: score(e.er))[:n]

    def remaining(self) -> int:
        return self.max_candidates - len(self.history)

    # ---- prompts
    def _history_digest(self, entries: list[Entry]) -> str:
        lines = []
        for e in entries:
            lines.append(f"#{e.n} [{e.worker}] {json.dumps(to_display(e.params))}\n    -> {eval_text(e.er, 4)}"
                         + (f"\n    rationale: {e.rationale}" if e.rationale else ""))
        return "\n".join(lines)

    def architect(self, it: int) -> None:
        """Blocking plan (used once at the start): bounded by ARCH_TIMEOUT, then falls back."""
        self._apply_plan(self._plan(it))   # keeps current/default directions if both models failed

    def architect_async(self, it: int) -> None:
        """Non-blocking re-plan: workers keep their current directions until it lands."""
        if self._pending is None:
            self._pending = self._arch_pool.submit(self._plan, it)

    def planner_fast_async(self, it: int) -> None:
        """Live mode: a quick Hydrogen plan that lands within a round or two."""
        if self._pending_fast is None:
            self._pending_fast = self._fast_pool.submit(self._plan, it, True)

    def _collect_plan(self) -> None:
        for attr in ("_pending_fast", "_pending"):     # the deep (Carbon) plan wins if both landed
            fut = getattr(self, attr)
            if fut is not None and fut.done():
                try:
                    plan = fut.result()
                    if plan.get("_ok"):
                        self._apply_plan(plan)
                except Exception as e:
                    print(f"[plan] failed: {e}")
                finally:
                    setattr(self, attr, None)

    def _apply_plan(self, plan: dict) -> None:
        self.diagnosis = plan["diagnosis"]
        self.directions = plan["directions"]
        self.emit("plan_applied", diagnosis=self.diagnosis, binding=plan.get("binding_constraint"),
                  likely_infeasible=plan.get("likely_infeasible"),
                  directions=[{"focus": d["focus"], "knobs": d["knobs"]} for d in self.directions])

    def _plan(self, it: int, fast: bool = False) -> dict:
        prompt = self._architect_prompt(it)
        schema = architect_schema(self.n_workers)
        first = FALLBACK_ARCHITECT if fast else ARCHITECT_MODEL
        self.emit("plan_requested", model=first, iter=it, n_evaluated=len(self.history), fast=fast)
        t0 = time.perf_counter()
        model, out = first, None
        try:
            if fast:
                out = self.llm.json_call(FALLBACK_ARCHITECT, prompt, schema, "low",
                                         f"architect_fast_it{it}", timeout=60, attempts=1)
            else:
                out = self.llm.json_call(ARCHITECT_MODEL, prompt, schema, ARCH_THINKING,
                                         f"architect_it{it}", timeout=ARCH_TIMEOUT, attempts=1)
        except Exception:
            if fast:
                out = None
            else:
                model = FALLBACK_ARCHITECT
                try:
                    out = self.llm.json_call(FALLBACK_ARCHITECT, prompt, schema, "medium",
                                             f"architect_fallback_it{it}", timeout=FALLBACK_TIMEOUT, attempts=1)
                except Exception:
                    out = None
        ok = bool(out)
        if not out:   # dry run, or both models failed: keep / default directions
            out = {"diagnosis": self.diagnosis or "(no plan)", "directions": self.directions or [
                {"focus": f"direction {i}", "knobs": list(oa.SPACE),
                 "instructions": "improve the worst violated spec; then reduce core current"}
                for i in range(self.n_workers)]}
            model = "none"
        rec = {"iter": it, "n_evaluated_at_request": len(self.history), "model": model,
               "latency_s": round(time.perf_counter() - t0, 1), **out}
        with open(self.dir / "architect.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
        self.arch_log.append({k: rec[k] for k in ("iter", "model", "latency_s")})
        self.emit("plan_ready", model=model, latency_s=rec["latency_s"], iter=it, fast=fast)
        out = dict(out, _ok=ok)
        print(f"[architect it{it}] {model} in {rec['latency_s']}s: {out['diagnosis'][:160]}")
        return out

    def critic_async(self, it: int) -> None:
        if self._critic_pending is None:
            self._critic_pending = self._arch_pool2.submit(self._critic, it, self.best()[0])

    def _collect_critic(self) -> None:
        if self._critic_pending is not None and self._critic_pending.done():
            try:
                note = self._critic_pending.result()
                if note:
                    self.critic_note = note
            except Exception as e:
                print(f"[critic] failed: {e}")
            finally:
                self._critic_pending = None

    def _critic(self, it: int, entry: "Entry") -> str:
        """Plot reviewer: gemini-4-neon looks at the 15-corner Bode plot IMAGE of the best design."""
        import base64
        png = self.dir / f"bode_it{it}_n{entry.n}.png"
        er = oa.evaluate(entry.params, spec=self.spec, curves=True)   # re-sim of a known candidate
        render_bode(er, png, title=f"candidate #{entry.n}: loop gain at 15 PVT points")
        target = oa.guarded(self.spec)
        prompt = (f"You are the measurement reviewer on an analog design team. The image is the loop-gain "
                  f"Bode plot (magnitude and phase, 15 process/temperature corners overlaid) of the team's "
                  f"current best op-amp sizing. Targets: phase margin >= {target['pm_deg'][1]:.0f} deg, "
                  f"unity-gain frequency >= {target['ugf_hz'][1]/1e6:.0f} MHz, DC gain >= {target['dc_gain_db'][1]:.0f} dB. "
                  f"Look at the curves: where is the unity-gain crossing relative to the phase roll-off, is there "
                  f"gain peaking or a second 0 dB crossing, how wide is the corner spread, is the slope near "
                  f"crossover steeper than -20 dB/dec. In at most 60 words say what you SEE and give one concrete "
                  f"sizing suggestion (the designers can change device W/L, bias current, Cc, Rz).")
        t0 = time.perf_counter()
        r = self.llm.client.interactions.create(
            model=CRITIC_MODEL, generation_config={"thinking_level": "low"}, timeout=90,
            input=[{"type": "text", "text": prompt},
                   {"type": "image", "mime_type": "image/png",
                    "data": base64.b64encode(png.read_bytes()).decode()}])
        note = (r.output_text or "").strip()
        u = r.usage
        with self.llm.lock:
            self.llm.calls += 1
            self.llm.tokens["in"] += u.total_input_tokens or 0
            self.llm.tokens["out"] += u.total_output_tokens or 0
            self.llm.tokens["thought"] += u.total_thought_tokens or 0
            with open(self.dir / "llm_calls.jsonl", "a") as f:
                f.write(json.dumps({"tag": f"critic_it{it}", "model": CRITIC_MODEL, "thinking": "low",
                                    "latency_s": round(time.perf_counter() - t0, 2), "in": u.total_input_tokens,
                                    "out": u.total_output_tokens, "thought": u.total_thought_tokens,
                                    "image": png.name, "response": note}) + "\n")
        self.emit("critic", iter=it, n=entry.n, image=png.name, note=note, model=CRITIC_MODEL,
                  latency_s=round(time.perf_counter() - t0, 1))
        print(f"[critic it{it}] {note}")
        return note

    def _architect_prompt(self, it: int) -> str:
        top = self.best(6)
        prompt = f"""You are the lead analog designer directing {self.n_workers} junior designers who each propose
transistor sizings. A SPICE simulator judges every proposal at all 15 PVT points; you never judge pass/fail yourself.

{_card()}
TUNABLE KNOBS (display units):
{knob_table()}

SPEC (must hold at all 15 PVT points, plus the saturation rule):
{spec_text(self.spec)}
Objective once the spec is met: minimize worst-case core current.

BUDGET: {self.remaining()} candidate evaluations left (of {self.max_candidates}).

BEST DESIGNS SO FAR (ranked: passing designs first by current, then by total spec violation):
{self._history_digest(top)}

FAILURE CENSUS over all {len(self.history)} candidates evaluated so far:
{self.failure_census()}
{('PLOT REVIEWER (looked at the Bode plot image of the best design): ' + self.critic_note) if self.critic_note else ''}
TOTAL EVALUATED: {len(self.history)}. Previous diagnosis: {self.diagnosis or '(none)'}

Task: diagnose, in circuit terms, what is limiting the best designs (which spec or which device's
saturation, at which corners, and why physically). Then assign each of the {self.n_workers} workers a
DIFFERENT, complementary search direction with concrete instructions (which knobs, which direction,
roughly how far, what trade-off to watch). Directions should together cover the most plausible
ways to reach a passing design; at least one should make bold moves and one careful refinements."""
        return prompt

    def worker(self, it: int, idx: int) -> list[tuple[dict, str]]:
        d = self.directions[idx % len(self.directions)]
        name = f"w{idx}"
        mine = [e for e in self.history if e.worker == name][-4:]
        top = self.best(3)
        prompt = f"""You are an analog circuit designer proposing transistor sizings. A SPICE simulator will judge
each proposal at all 15 PVT points.

{_card()}
TUNABLE KNOBS (display units; values outside the range are clipped):
{knob_table()}

SPEC (all 15 PVT points + saturation rule):
{spec_text(self.spec)}
Objective once the spec is met: minimize worst-case core current.

LEAD DESIGNER'S DIAGNOSIS: {self.diagnosis}
YOUR ASSIGNED DIRECTION: {d['focus']} (knobs: {', '.join(d['knobs'])})
INSTRUCTIONS: {d['instructions']}

BEST DESIGNS SO FAR:
{self._history_digest(top)}

FAILURE CENSUS over all {len(self.history)} candidates:
{self.failure_census()}
{('PLOT REVIEWER (looked at the Bode plot image of the best design): ' + self.critic_note) if self.critic_note else ''}

YOUR OWN RECENT ATTEMPTS:
{self._history_digest(mine) if mine else '(none yet)'}

Propose exactly {self.k} new, distinct candidates (all {len(oa.SPACE)} knobs each, in the display units above).
Start from the best design unless your direction says otherwise; do not resubmit a design already tried.
Give each a one-sentence rationale."""
        out = self.llm.json_call(WORKER_MODEL, prompt, candidate_schema(self.k), "low", f"worker{idx}_it{it}")
        cands = []
        for c in (out or {}).get("candidates", [])[: self.k]:
            p = from_display(c)
            if p:
                cands.append((oa.clamp(p), str(c.get("rationale", ""))[:300]))
        return cands

    # ---- main loop
    def run(self) -> dict:
        t0 = time.perf_counter()
        start = self._evaluate(dict(self.start_params or oa.DEFAULTS), 0, "start",
                               "starting design supplied by the user" if self.start_params
                               else "published AMPICBench sizing")
        print(f"[start] {eval_text(start.er, 3)}")
        it, last_best, stall = 0, score(start.er), 0
        self.emit("start", circuit=oa.CIRCUIT, spec={k: list(v) for k, v in self.spec.items()},
                  target={k: list(v) for k, v in oa.guarded(self.spec).items()},
                  knobs=list(oa.SPACE), workers=self.n_workers, per_worker=self.k,
                  max_candidates=self.max_candidates,
                  models={"architect": ARCHITECT_MODEL, "worker": WORKER_MODEL, "critic": CRITIC_MODEL})
        if self.fast_start and not self.llm.dry:
            # live mode: workers start at once on generic directions; the architect's plan lands later
            self.diagnosis = "(lead designer still analysing; use your own judgement)"
            self.directions = [GENERIC_DIRECTIONS[i % len(GENERIC_DIRECTIONS)] for i in range(self.n_workers)]
            self.planner_fast_async(0)   # Hydrogen: lands in ~10-20 s
            self.architect_async(0)      # Carbon: deeper, lands when it lands
        else:
            self.architect(it)
        if self.llm.dry:
            self.worker(0, 0)
            return {}
        while self.remaining() > 0:
            it += 1
            self._collect_plan()   # apply a background re-plan if it has landed
            self._collect_critic()
            self.emit("round_start", iter=it, n_evaluated=len(self.history))
            with ThreadPoolExecutor(self.n_workers) as ex:
                proposals = list(ex.map(lambda i: self.worker(it, i), range(self.n_workers)))
            flat = [(f"w{i}", p, r) for i, ps in enumerate(proposals) for p, r in ps]
            seen = {tuple(f'{v:.6g}' for v in e.params.values()) for e in self.history}
            jobs = []
            for w, p, r in flat:
                key = tuple(f'{v:.6g}' for v in p.values())
                if key not in seen:
                    seen.add(key)
                    jobs.append((w, p, r))
            jobs = jobs[: self.remaining()]
            # evaluate candidates 2 at a time (each eval already uses 15 ngspice processes)
            with ThreadPoolExecutor(2) as ex:
                entries = list(ex.map(lambda j: self._evaluate(j[1], it, j[0], j[2]), jobs))
            for e in entries:
                if e:
                    print(f"[it{it} #{e.n} {e.worker}] {eval_text(e.er, 2)}\n    why: {e.rationale}")
            b = score(self.best()[0].er)
            stall = stall + 1 if b >= last_best else 0
            last_best = min(last_best, b)
            print(f"== it{it}: {len(self.history)}/{self.max_candidates} candidates, best score {b}, "
                  f"LLM calls {self.llm.calls}, tokens {self.llm.tokens}")
            if not jobs:
                stall = self.stall_rounds
            if self.remaining() > 0 and (stall >= self.stall_rounds or
                                         (self.replan_every and it % self.replan_every == 0)):
                self.architect_async(it)
                if self.fast_start:
                    self.planner_fast_async(it)
                stall = 0
            if self.use_critic and self.remaining() > 0:
                self.critic_async(it)
        passed = [e for e in self.history if e.er.passed]
        best = self.best()[0]
        recheck, recheck_fail = (oa.recheck_full(best.params, spec=self.spec) if best.er.passed
                                 else (None, []))
        summary = {"run_id": self.run_id, "candidates": len(self.history), "decks": self.budget.used,
                   "success": bool(passed), "candidates_to_first_pass": passed[0].n if passed else None,
                   "best_n": best.n, "best_passed": best.er.passed, "recheck_full_lib": recheck, "recheck_failures": recheck_fail,
                   "guard_band": {"metrics": oa.GUARD, "sat_v": oa.SAT_GUARD_V},
                   "n_passed_target": len(passed),
                   "n_passed_nominal_spec": sum(e.er.passed_nominal for e in self.history),
                   "best_passed_nominal_spec": best.er.passed_nominal,
                   "best_i_core_uA": best.er.worst["i_core_a"][0] * 1e6,
                   "best_params": best.params, "best_display": to_display(best.params),
                   "llm_calls": self.llm.calls, "tokens": self.llm.tokens,
                   "architect_calls": self.arch_log,
                   "wall_s": round(time.perf_counter() - t0, 1),
                   "models": {"architect": ARCHITECT_MODEL, "worker": WORKER_MODEL},
                   "circuit": oa.CIRCUIT, "swarm_version": 2,
                   "options": {"replan_every": self.replan_every, "fast_start": self.fast_start,
                               "critic": self.use_critic, "workers": self.n_workers, "per_worker": self.k},
                   "final_diagnosis": self.diagnosis}
        (self.dir / "summary.json").write_text(json.dumps(summary, indent=1))
        self.emit("done", **{k: summary[k] for k in ("success", "candidates_to_first_pass", "best_n",
                                                     "best_i_core_uA", "recheck_full_lib", "llm_calls", "wall_s")})
        self._arch_pool.shutdown(wait=False, cancel_futures=True)
        self._arch_pool2.shutdown(wait=False, cancel_futures=True)
        self._fast_pool.shutdown(wait=False, cancel_futures=True)
        return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--per-worker", type=int, default=2)
    ap.add_argument("--candidates", type=int, default=40)
    ap.add_argument("--stall-rounds", type=int, default=2)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--run-id")
    ap.add_argument("--arch-timeout", type=float, default=ARCH_TIMEOUT)
    ap.add_argument("--circuit", default="diff", choices=list(oa.CIRCUITS))
    ap.add_argument("--replan-every", type=int, default=2, help="architect re-plans every N rounds (0 = only on stall)")
    ap.add_argument("--fast-start", action="store_true", help="live mode: workers start before the architect's plan lands")
    ap.add_argument("--critic", action="store_true", help="Neon reviews the Bode plot image of the best design each round")
    ap.add_argument("--roles", type=Path, help="roles.json from `sizing.intake roles` (photo -> roles): "
                    "the predicted role map defines the knobs")
    ap.add_argument("--start-from", type=Path, help="design JSON (a dict of knobs, or {params: …} / "
                    "a run's summary.json with best_params) to start from instead of the published sizing")
    ap.add_argument("--spec", type=Path, help='JSON spec, e.g. {"pm_deg": [">=", 60], "ugf_hz": [">=", 1e7], ...}')
    a = ap.parse_args()
    oa.use(a.circuit)
    if a.roles:   # knobs come from the roles predicted from the schematic image
        rj = json.loads(a.roles.read_text())
        base = rj.get("circuit", a.circuit)
        rmap = {(d["subckt"], d["inst"]): d["role"] for d in rj["devices"]
                if d["role"] != "other" and d.get("confidence", 1.0) >= 0.5}
        CARDS["live"] = CARDS[base]
        oa.use(oa.register_from_roles(base, rmap, "live"))
    start_params = None
    if a.start_from:
        dj = json.loads(a.start_from.read_text())
        dj = dj.get("best_params") or dj.get("params") or dj
        start_params = {k: float(v) for k, v in dj.items() if k in oa.SPACE}
    spec = None
    if a.spec:
        sj = json.loads(a.spec.read_text())
        sj = sj.get("spec", sj)
        spec = {k: (v[0], float(v[1])) for k, v in sj.items()}
    globals()["ARCH_TIMEOUT"] = a.arch_timeout
    if not a.dry and not os.environ.get("GEMINI_API_KEY"):
        env = oa.ROOT / ".env"
        for line in env.read_text().splitlines() if env.exists() else []:
            if line.startswith("GEMINI_API_KEY="):
                os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip()
    s = Swarm(a.workers, a.per_worker, a.candidates, spec=spec, dry=a.dry, stall_rounds=a.stall_rounds,
              replan_every=a.replan_every, fast_start=a.fast_start, critic=a.critic,
              start_params=start_params,
              run_id=a.run_id)
    out = s.run()
    if out:
        print(json.dumps({k: v for k, v in out.items() if k not in ("best_params",)}, indent=1))


if __name__ == "__main__":
    main()
