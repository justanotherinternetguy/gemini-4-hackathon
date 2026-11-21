"""Red-team auditor: a Gemini Managed Agent (Antigravity, gemini-4-carbon) that tries to make our
judge LIE - i.e. find designs, inputs or spec strings where the evaluator reports PASS for a design
that is not really good, or where an optimizer could game the score or the budget.

It gets a read-only copy of the evaluator source in its hosted sandbox (/workspace/src) and three
client-side tools that run the REAL code on this machine:
  evaluate(circuit, params, guard)       - our 15-PVT ngspice evaluator (budgeted)
  render_deck(circuit, params, corner, temp) - the exact SPICE deck a parameter set produces
  check_spec(spec, metrics)              - the AMS-SizingBench spec parser/checker (no sim)
It must back every finding with tool evidence. Findings + proposed pytest cases are saved for
human triage; nothing is auto-applied.

python -m sizing.redteam                     # default budget: 40 evaluate calls, 350k tokens
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path

from bench import amsbench as ab
from sizing import managed
from sizing import opamp2stg as oa

MODEL = "gemini-4-carbon"
OUT_ROOT = oa.ROOT / "runs" / "redteam"
SOURCES = ["sizing/opamp2stg.py", "sizing/baseline.py", "sizing/swarm.py", "bench/amsbench.py"]


def _num(x):
    if x is None:
        return None
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return str(x)
    return x


class Tools:
    def __init__(self, max_evals: int):
        self.max_evals, self.evals = max_evals, 0

    def _use(self, circuit: str) -> None:
        if circuit not in oa.CIRCUITS:
            raise ValueError(f"circuit must be one of {list(oa.CIRCUITS)}")
        oa.use(circuit)

    def evaluate(self, a: dict) -> dict:
        if self.evals >= self.max_evals:
            return {"error": f"evaluate budget exhausted ({self.evals}/{self.max_evals})"}
        self._use(a.get("circuit", "diff"))
        self.evals += 1
        t0 = time.perf_counter()
        er = oa.evaluate(a.get("params") or {}, guard=bool(a.get("guard", True)))
        pts = []
        for r in er.points:
            roles = {}
            for d in r.devices.values():
                if d["role"]:
                    roles[d["role"]] = round(min(roles.get(d["role"], 9.0), d["margin"]) * 1e3, 1)
            pts.append({"pt": f"{r.corner}/{r.temp:g}", "ok": r.ok, "error": r.error,
                        "dc_gain_db": _num(r.dc_gain_db), "ugf_hz": _num(r.ugf_hz),
                        "pm_deg": _num(r.pm_deg), "gain_margin_db": _num(r.gain_margin_db),
                        "i_core_a": _num(r.i_core_a), "i_total_a": _num(r.i_total_a),
                        "min_sat_margin_mv": roles, "bias_triode": len(r.bias_triode)})
        return {"evaluate_calls_used": self.evals, "evaluate_calls_max": self.max_evals,
                "params_used": {k: _num(v) for k, v in er.params.items()},
                "passed_target": er.passed, "passed_nominal_spec": er.passed_nominal,
                "target": {k: [op, b] for k, (op, b) in er.spec.items()},
                "worst": {k: ([_num(v[0]), v[1], v[2]] if v else None) for k, v in er.worst.items()},
                "n_failures": len(er.failures), "failures": er.failures[:12],
                "points": pts, "seconds": round(time.perf_counter() - t0, 2)}

    def render_deck(self, a: dict) -> dict:
        self._use(a.get("circuit", "diff"))
        deck = oa.render(a.get("params") or {}, a.get("corner", "tt"), a.get("temp", 27))
        return {"deck": deck[-14000:], "truncated_head": len(deck) > 14000}

    def check_spec(self, a: dict) -> dict:
        spec, metrics = a.get("spec", ""), a.get("metrics") or {}
        try:
            cons = ab.parse_specs(spec)
        except Exception as e:
            return {"parse_error": str(e)}
        detail = [{"term": str(c), "metric_value": metrics.get(c.metric), "pass": c.check(metrics)}
                  for c in cons]
        return {"parsed_terms": [str(c) for c in cons], "detail": detail,
                "pass": all(d["pass"] for d in detail)}


def tool_specs(knobs_note: str) -> list:
    params_schema = {"type": "object", "description": "knob -> value in SI units (meters, A, F, ohm). "
                     + knobs_note, "additionalProperties": {"type": "number"}}
    circuit = {"type": "string", "enum": list(oa.CIRCUITS)}
    return [
        {"type": "code_execution"},
        {"type": "function", "name": "evaluate",
         "description": "Run the REAL evaluator (sizing.opamp2stg.evaluate) on this machine: ngspice at "
                        "15 PVT points. Returns pass/fail vs the guard-banded target and the bare spec, "
                        "worst cases, failures and a per-point summary incl. total supply current. Budgeted.",
         "parameters": {"type": "object", "properties": {
             "circuit": circuit, "params": params_schema,
             "guard": {"type": "boolean", "description": "use guard-banded target (default true)"}},
             "required": ["circuit", "params"]}},
        {"type": "function", "name": "render_deck",
         "description": "Return the exact SPICE deck the evaluator would simulate for these params at one PVT point.",
         "parameters": {"type": "object", "properties": {
             "circuit": circuit, "params": params_schema,
             "corner": {"type": "string", "enum": list(oa.CORNERS)}, "temp": {"type": "number"}},
             "required": ["circuit", "params"]}},
        {"type": "function", "name": "check_spec",
         "description": "Run the AMS-SizingBench spec parser + checker (bench.amsbench.parse_specs / "
                        "Constraint.check) on a spec string and a metrics dict. No simulation.",
         "parameters": {"type": "object", "properties": {
             "spec": {"type": "string"}, "metrics": {"type": "object", "additionalProperties": {"type": "number"}}},
             "required": ["spec", "metrics"]}},
    ]


SYSTEM = ("You are a red-team auditor for an automated analog-circuit optimizer. Your job is to make "
          "its judge lie: find concrete inputs where the evaluator reports PASS (or a better score) for a "
          "design that a careful analog engineer would reject, or where an optimizer could game the "
          "score, the constraints or the simulation budget. You are rewarded for real, reproduced "
          "findings and penalized for speculation: every finding must cite the tool call that shows it.")


def task_text(max_evals: int) -> str:
    knobs = {name: {k: [v[0], v[1], v[2]] for k, v in c["space"].items()} for name, c in oa.CIRCUITS.items()}
    return f"""TARGET: an LLM-agent analog sizing system. Agents propose transistor sizes; the judge
(sizing/opamp2stg.py: evaluate) simulates the op-amp with ngspice at 15 PVT points (corners tt ff ss sf fs
x temps -40 27 125 C) and declares PASS only if every spec holds at every point and every signal-path
transistor is in saturation with margin. The optimizers (sizing/swarm.py, sizing/baseline.py) trust that
verdict and its numbers (worst-case PM, UGF, DC gain, core current, saturation margins). bench/amsbench.py
is a second judge (spec-string parser) used for another benchmark.

SOURCE (read-only copy): /workspace/src/ - read it with code_execution / file tools before attacking.

CIRCUITS and knobs [default, min, max] in SI units:
{json.dumps(knobs, indent=1)}

TOOLS: evaluate (REAL judge, {max_evals} calls max, ~1-2 s each), render_deck (free), check_spec (free).
Start from the published defaults or from good designs and perturb.

ATTACK SURFACE - try at least these, and anything else you see in the code:
1. False PASS from measurement artifacts: multiple 0 dB crossings, phase wrapping, loop gain that peaks
   above the 10 Hz DC-gain probe, UGF/PM taken at the wrong crossing, gain margin ignored, conditional
   stability, metrics parsed from the wrong ngspice output line.
2. Metric definitions an optimizer can game: e.g. "core current" excludes the bias network - can total
   supply current grow while i_core shrinks? Is anything important not checked at all (gain margin,
   bias-network devices in triode, output swing, slew)?
3. Parameter handling: out-of-range, zero, negative, NaN, inf, huge values, wrong keys, extreme ratios;
   do they get clamped, crash, produce a bogus netlist, or silently pass?
4. Saturation check gaps: which devices are not checked; can margins be satisfied while the circuit is
   broken?
5. Spec-string parser (check_spec): signs, scientific notation, spacing, OR/and casing, unknown metrics,
   NaN metric values, equality operators.
6. Selection/budget logic in swarm.py / baseline.py: can an infeasible design outrank a feasible one,
   can the budget be exceeded, do duplicates cost budget, does a failed sim score well?

OUTPUT: your final message must contain exactly one ```json block:
{{"findings": [{{"id": "RT1", "title": "...", "category": "false_pass|metric_gaming|param_handling|
  saturation_gap|spec_parser|selection_budget|other", "severity": "high|medium|low",
  "confirmed": true/false (true only if a tool call in this session demonstrated it),
  "evidence": "which tool call(s) and what they returned", "repro": {{...exact args...}},
  "observed": "...", "expected": "...", "fix": "concrete code-level fix",
  "pytest": "a self-contained pytest function (imports from sizing/bench) that fails today and passes after the fix"}}],
 "summary": "3 sentences: how trustworthy is this judge, and the top fix"}}
Rank findings by severity. Do not pad the list; 3 solid confirmed findings beat 10 guesses."""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-evals", type=int, default=40)
    ap.add_argument("--tokens", type=int, default=350_000)
    ap.add_argument("--wall", type=float, default=2700)
    ap.add_argument("--id")
    a = ap.parse_args()
    run_dir = OUT_ROOT / (a.id or f"rt_{datetime.now():%H%M%S}")
    run_dir.mkdir(parents=True, exist_ok=True)
    tools = Tools(a.max_evals)
    handlers = {"evaluate": tools.evaluate, "render_deck": tools.render_deck, "check_spec": tools.check_spec}
    task = task_text(a.max_evals)
    (run_dir / "task.txt").write_text(task)
    sources = [{"type": "inline", "target": f"/workspace/src/{p}", "content": (oa.ROOT / p).read_text()}
               for p in SOURCES]
    c = managed.client()
    env = {"type": "remote", "sources": sources}
    print(f"red-team agent ({MODEL}) starting; logs in {run_dir}", flush=True)
    t0 = time.time()
    try:
        it, turns, usage = managed.run(c, run_dir, [{"type": "text", "text": task}], SYSTEM,
                                       tool_specs("Unknown keys are rejected."), handlers, MODEL,
                                       a.tokens, a.wall, environment=env)
    except Exception as e:
        if "source" not in str(e).lower() and "400" not in str(e):
            raise
        print(f"inline sources rejected ({str(e)[:160]}); embedding sources in the prompt instead", flush=True)
        embedded = task.replace("SOURCE (read-only copy): /workspace/src/ - read it with code_execution / file tools before attacking.",
                                "SOURCE (read-only copy) is embedded below.") + "\n\n" + "\n\n".join(
            f"### {p}\n```python\n{(oa.ROOT / p).read_text()}\n```" for p in SOURCES)
        it, turns, usage = managed.run(c, run_dir, [{"type": "text", "text": embedded}], SYSTEM,
                                       tool_specs("Unknown keys are rejected."), handlers, MODEL,
                                       a.tokens, a.wall, environment="remote")
    text = it.output_text or ""
    (run_dir / "agent_final.md").write_text(text)
    m = re.search(r"```json\s*(\{.*\})\s*```", text, re.S)
    report = json.loads(m.group(1)) if m else {}
    (run_dir / "findings.json").write_text(json.dumps(report, indent=1))
    tests = [f.get("pytest", "") for f in report.get("findings", []) if f.get("pytest")]
    if tests:
        (run_dir / "proposed_tests.py").write_text(
            "# Proposed by the red-team agent - REVIEW BEFORE PROMOTING TO tests/\n\n" + "\n\n\n".join(tests) + "\n")
    summary = {"status": it.status, "turns": turns, "evaluate_calls": tools.evals,
               "tokens_last": usage[-1] if usage else None, "wall_s": round(time.time() - t0, 1),
               "n_findings": len(report.get("findings", [])),
               "confirmed": sum(bool(f.get("confirmed")) for f in report.get("findings", [])),
               "summary": report.get("summary")}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    for f in report.get("findings", []):
        print(f"- [{f.get('severity')}/{'confirmed' if f.get('confirmed') else 'unconfirmed'}] "
              f"{f.get('id')}: {f.get('title')}")


if __name__ == "__main__":
    main()
