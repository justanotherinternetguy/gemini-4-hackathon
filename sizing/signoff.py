"""Independent sign-off verifier: a Gemini Managed Agent (Antigravity agent, gemini-4-carbon)
that re-verifies a sized op-amp WITHOUT trusting our evaluator.

The agent gets: the circuit netlist (subcircuits + bias network, no testbench), the published
reference sizing for calibration, the spec + guard band, and the schematic image. It must
write its OWN testbench, calibrate it against the circuit authors' published numbers, then
simulate the design at all 15 PVT points with the FULL SkyWater library and return a verdict.

It runs in Google's hosted sandbox; simulations go through one custom function tool,
`run_spice(deck, points)`, which we execute locally with ngspice 47 + the full SKY130 library.
Afterwards we compare the agent's numbers with our own evaluator, point by point.

python -m sizing.signoff --run runs/swarm/guard_s0          # verify a swarm run's best design
python -m sizing.signoff --design designs/hand_K.json
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from sizing import opamp2stg as oa

AGENT = "antigravity-preview-09-2026"
MODEL = "gemini-4-carbon"
OUT_ROOT = oa.ROOT / "runs" / "signoff"
SCHEMATIC_PNG = (oa.ROOT / "third_party" / "AMPICBench" / "schematics" / "Opamp_2stg_Diff_ClassA.png")
PUBLISHED_TT = {"dc_gain_db": 60.6, "ugf_hz": 126.0e6, "pm_deg": 31.5}   # AMPICBench RESULTS.md

# --------------------------------------------------------------------------- netlists

def dut_netlist(params: dict) -> str:
    """Circuit only: params, helper funcs, subcircuits and the bias-network instances.
    No supply, stimulus, load, op-amp instance, probe or .control block."""
    deck = oa.render(params, "tt", 27, lib="full")
    keep = []
    for line in deck.splitlines():
        s = line.strip().lower()
        if s.startswith("* --- stb probe"):
            break
        if s.startswith(".lib") or s.startswith(".temp") or "design change" in s:
            continue
        keep.append(line)
    keep[0] = "* DUT: SKY130 two-stage op-amp (AMPICBench Opamp_2stg_Diff_ClassA) + bias network"
    return "\n".join(keep).strip() + "\n"


# --------------------------------------------------------------------------- the tool

_FORBIDDEN = re.compile(r"(?im)^\s*(\.lib|\.inc|\.include|\.temp|shell\b)")
_NUMLINE = re.compile(r"^\s*([\w@.\[\]():#-]+)\s*=\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")

RUN_SPICE_TOOL = {
    "type": "function", "name": "run_spice",
    "description": (
        "Run an ngspice-47 batch simulation of YOUR deck at one or more PVT points with the FULL "
        "SkyWater SKY130 model library. The tool inserts `.lib <sky130 full library> <corner>` and "
        "`.temp <T>` right after your title line, so your deck must NOT contain .lib, .include or "
        ".temp lines (or shell commands). Put analyses in a .control block (op/ac/tran/meas/let/print, "
        "end with quit). Returned per point: every stdout line of the form `name = number` (from print/"
        "meas), plus error/warning lines. Each point costs one simulation from a limited budget."),
    "parameters": {"type": "object", "properties": {
        "deck": {"type": "string", "description": "complete SPICE deck text, first line is the title"},
        "points": {"type": "array", "description": "PVT points to run this deck at",
                   "items": {"type": "object", "properties": {
                       "corner": {"type": "string", "enum": list(oa.CORNERS)},
                       "temp": {"type": "number"}}, "required": ["corner", "temp"]}},
        "label": {"type": "string", "description": "short name for this run, e.g. 'calibration'"}},
        "required": ["deck", "points"]},
}


class SpiceTool:
    def __init__(self, run_dir: Path, max_decks: int = 150):
        self.run_dir, self.max_decks, self.used = run_dir, max_decks, 0
        self.lock = threading.Lock()
        self.calls = 0

    def _one(self, deck: str, corner: str, temp: float) -> dict:
        lines = deck.splitlines()
        full = "\n".join([lines[0], f'.lib "{oa.FULL_LIB}" {corner}', f".temp {temp}"] + lines[1:])
        with tempfile.TemporaryDirectory(prefix="signoff_") as td:
            (Path(td) / ".spiceinit").write_text(oa.SPICEINIT.read_text())
            (Path(td) / "deck.cir").write_text(full)
            try:
                p = subprocess.run(["ngspice", "-b", "deck.cir"], cwd=td, capture_output=True,
                                   text=True, timeout=600)
                out, rc = p.stdout + "\n" + p.stderr, p.returncode
            except subprocess.TimeoutExpired:
                return {"corner": corner, "temp": temp, "error": "timeout after 600 s"}
        vals, errs = [], []
        for l in out.splitlines():
            if _NUMLINE.match(l):
                vals.append(l.strip())
            elif re.search(r"error|warning|failed|not found|no such", l, re.I):
                errs.append(l.strip())
        return {"corner": corner, "temp": temp, "returncode": rc,
                "values": vals[:120], "errors": errs[:8]}

    def __call__(self, args: dict) -> dict:
        deck = args.get("deck", "")
        points = args.get("points") or [{"corner": "tt", "temp": 27}]
        if not deck.strip():
            return {"error": "empty deck"}
        if m := _FORBIDDEN.search(deck):
            return {"error": f"deck rejected: line starting with '{m.group(1)}' is not allowed "
                             "(the tool inserts .lib/.temp itself)"}
        bad = [p for p in points if p.get("corner") not in oa.CORNERS]
        if bad:
            return {"error": f"unknown corner in {bad}; use {list(oa.CORNERS)}"}
        with self.lock:
            if self.used + len(points) > self.max_decks:
                return {"error": f"simulation budget exhausted ({self.used}/{self.max_decks})"}
            self.used += len(points)
            self.calls += 1
            n = self.calls
        t0 = time.perf_counter()
        with ThreadPoolExecutor(min(15, len(points))) as ex:
            res = list(ex.map(lambda p: self._one(deck, p["corner"], float(p["temp"])), points))
        rec = {"call": n, "label": args.get("label"), "points": points, "deck": deck,
               "results": res, "seconds": round(time.perf_counter() - t0, 1)}
        with open(self.run_dir / "spice_calls.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"   [run_spice #{n} '{args.get('label', '')}'] {len(points)} point(s), "
              f"{rec['seconds']}s, budget {self.used}/{self.max_decks}")
        return {"results": res, "budget_used": self.used, "budget_max": self.max_decks}


# --------------------------------------------------------------------------- agent loop

def _stream(client, run_dir: Path, **kw):
    """Create (streamed) and return the finished interaction, printing a live trace."""
    stream = client.interactions.create(stream=True, **kw)
    last = None
    for ev in stream:
        et = getattr(ev, "event_type", None) or getattr(ev, "type", None)
        if getattr(ev, "interaction", None) is not None:
            last = ev.interaction
        if et == "step.start":
            st = getattr(ev, "step", None)
            kind = getattr(st, "type", None)
            if kind == "function_call":
                print(f"  agent -> {getattr(st, 'name', '?')}()")
            elif kind in ("code_execution_call",):
                print("  agent -> code_execution")
        with open(run_dir / "events.jsonl", "a") as f:
            try:
                f.write(json.dumps(ev.model_dump(exclude_none=True), default=str)[:4000] + "\n")
            except Exception:
                pass
    if last is None:
        raise RuntimeError("stream ended without an interaction")
    return client.interactions.get(id=last.id)


def run_agent(client, run_dir: Path, input_blocks: list, system: str, tool: SpiceTool,
              token_budget: int, wall_s: float) -> tuple:
    cfg = {"type": "antigravity", "model": MODEL, "max_total_tokens": token_budget}
    tools = [{"type": "code_execution"}, RUN_SPICE_TOOL]
    t0 = time.time()
    it = _stream(client, run_dir, agent=AGENT, input=input_blocks, environment="remote",
                 agent_config=cfg, tools=tools, system_instruction=system)
    turns, usage = 1, []
    while True:
        usage.append(getattr(getattr(it, "usage", None), "total_tokens", None))
        with open(run_dir / "interactions.jsonl", "a") as f:
            f.write(json.dumps({"turn": turns, "id": it.id, "environment_id": it.environment_id,
                                "status": it.status, "total_tokens": usage[-1]}) + "\n")
        if it.status != "requires_action":
            break
        if time.time() - t0 > wall_s:
            try:
                client.interactions.cancel(id=it.id)
            except Exception:
                pass
            raise TimeoutError(f"agent exceeded {wall_s}s wall clock")
        done = {getattr(s, "call_id", None) for s in it.steps or [] if s.type == "function_result"}
        calls = [s for s in it.steps or [] if s.type == "function_call" and s.name == "run_spice"
                 and s.id not in done]
        results = [{"type": "function_result", "name": c.name, "call_id": c.id,
                    "result": tool(dict(c.arguments or {}))} for c in calls]
        turns += 1
        kw = dict(agent=AGENT, previous_interaction_id=it.id, environment=it.environment_id,
                  input=results, agent_config=cfg, tools=tools, system_instruction=system)
        try:
            it = _stream(client, run_dir, **kw)
        except Exception as e:   # some API versions reject re-sent tools on continuation
            if "tools" in str(e).lower() or "400" in str(e):
                kw.pop("tools"); kw.pop("agent_config")
                it = _stream(client, run_dir, **kw)
            else:
                raise
    return it, turns, usage


SYSTEM = (
    "You are an independent analog sign-off engineer. You did not design this circuit and you do "
    "not trust any numbers from its designers or their tools. You verify it yourself with SPICE: "
    "write your own testbench, prove it is correct by calibrating it against published reference "
    "numbers, then judge the design. Be rigorous and concise. Never invent simulation results: every "
    "number you report must come from a run_spice result.")


def task_text(dut: str, ref: str, spec: dict) -> str:
    target = oa.guarded(spec)
    names = {"pm_deg": "phase margin [deg]", "ugf_hz": "loop unity-gain frequency [Hz]",
             "dc_gain_db": "DC loop gain [dB]", "i_core_a": "core current = tail + stage-2 sink [A]"}
    spec_lines = "\n".join(f"  {names[m]}: spec {op} {b:g}; guard-banded design target {op} {target[m][1]:.4g}"
                           for m, (op, b) in spec.items())
    roles = "\n".join(f"  {path} -> {role} (required |Vds|-|Vdsat| >= {oa.SAT_MARGIN[role]*1e3:.0f} mV spec, "
                      f"{oa.sat_required(role)*1e3:.0f} mV target)"
                      for path, role in oa.DEVICE_ROLE.items())
    return f"""TASK: sign off (or reject) the op-amp design below.

CIRCUIT. The attached image is the schematic of the op-amp topology. The netlists below contain the
subcircuits and the bias network only (no supplies, stimulus, load or measurement):
  - Op-amp subcircuit `Opamp_2stg_Diff_ClassA` with ports (Vbias_3 Vbias_4 vm vout vp):
    vm = inverting input, vp = non-inverting input, Vbias_3/Vbias_4 = cascode bias nodes produced
    by the bias network instances XI0 (Bias_gen) and XI2 (Cascode_bias) already in the netlist.
  - Global supply node `vdd` (you must add the 1.8 V source). Device W/L params are in meters and
    converted inside the netlist; leave them alone.
APPLICATION: unity-gain follower (vout fed back to vm), input common mode 0.9 V, load 500 fF at vout.
Verify the LOOP GAIN of that feedback loop: DC loop gain, unity-gain frequency, phase margin,
gain margin. Choose and justify your own measurement method.
Also measure, per PVT point, the core current (drain current of the tail bottom device + stage-2
sink bottom device) and the saturation margin |Vds|-|Vdsat| of every signal-path device. If you
instantiate the op-amp as XI1, the devices are:
{roles}
SKY130/ngspice notes: transistors are subcircuit instances, so operating-point values are read as
@m.<path>.msky130_fd_pr__<model>[param] in lower case, e.g.
print @m.xi1.xi7.xnm0.msky130_fd_pr__nfet_01v8_lvt[vdsat]  (all op-amp devices are LVT:
nfet_01v8_lvt / pfet_01v8_lvt). Drain current param is [id].
In this PDK build the corner labels sf/fs behave swapped vs their names; just report by label.

SPEC (must hold at EVERY one of the 15 PVT points = corners tt ff ss sf fs x temps -40 27 125 C):
{spec_lines}

STEPS
1. CALIBRATE: run your bench on the REFERENCE netlist (published sizing) at tt, 27 C. The circuit's
   authors publish: DC loop gain {PUBLISHED_TT['dc_gain_db']} dB, unity-gain frequency
   {PUBLISHED_TT['ugf_hz']/1e6:.0f} MHz, phase margin {PUBLISHED_TT['pm_deg']} deg. Your bench must
   reproduce these within 1 dB / 3 % / 1.5 deg before you trust it. Fix your bench until it does.
2. VERIFY: run the same bench on the DESIGN netlist at all 15 PVT points (one run_spice call can
   take all 15 points).
3. REPORT: your final message must contain exactly one ```json block:
{{"calibration": {{"dc_gain_db":..,"ugf_hz":..,"pm_deg":..,"matches_published": true/false}},
  "method": "how you measured loop gain and why it is valid",
  "points": [{{"corner":"tt","temp":27,"dc_gain_db":..,"ugf_hz":..,"pm_deg":..,"gain_margin_db":..,
              "i_core_a":..,"min_sat_margin_mv":{{"in":..,"load":..,"tail":..,"drv":..,"sink":..}}}}, ... 15 entries],
  "pass_spec": true/false, "pass_target": true/false,
  "violations": ["metric at corner/temp: value vs limit", ...],
  "verdict": "SIGN-OFF or REJECT: one short paragraph for a design review"}}

REFERENCE NETLIST (published sizing):
```spice
{ref}```

DESIGN NETLIST (to verify):
```spice
{dut}```
"""


# --------------------------------------------------------------------------- compare

def compare(agent: dict, ours: oa.EvalResult) -> dict:
    by = {(p["corner"], int(round(float(p["temp"])))): p for p in agent.get("points", [])}
    rows, dg, dpm, rugf = [], [], [], []
    for r in ours.points:
        a = by.get((r.corner, int(r.temp)))
        row = {"corner": r.corner, "temp": r.temp, "ours": {"dc_gain_db": r.dc_gain_db, "ugf_hz": r.ugf_hz,
               "pm_deg": r.pm_deg, "i_core_a": r.i_core_a}, "agent": a}
        if a:
            try:
                dg.append(abs(float(a["dc_gain_db"]) - r.dc_gain_db))
                dpm.append(abs(float(a["pm_deg"]) - r.pm_deg))
                rugf.append(abs(float(a["ugf_hz"]) / r.ugf_hz - 1))
            except (TypeError, ValueError, KeyError):
                pass
        rows.append(row)
    return {"points_matched": sum(1 for r in rows if r["agent"]), "points_total": len(rows),
            "max_abs_dgain_db": max(dg) if dg else None, "max_abs_dpm_deg": max(dpm) if dpm else None,
            "max_rel_dugf": max(rugf) if rugf else None,
            "ours_pass_spec": ours.passed_nominal, "ours_pass_target": ours.passed,
            "agent_pass_spec": agent.get("pass_spec"), "agent_pass_target": agent.get("pass_target"),
            "verdict_agrees": (agent.get("pass_spec") == ours.passed_nominal
                               and agent.get("pass_target") == ours.passed),
            "rows": rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--run", type=Path, help="swarm run dir (uses summary.json best_params)")
    src.add_argument("--design", type=Path, help="json with a 'params' dict")
    ap.add_argument("--tokens", type=int, default=400_000, help="agent max_total_tokens")
    ap.add_argument("--max-decks", type=int, default=150)
    ap.add_argument("--wall", type=float, default=1800, help="wall-clock limit (s)")
    ap.add_argument("--id")
    a = ap.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        for line in (oa.ROOT / ".env").read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip()
    params = (json.loads((a.run / "summary.json").read_text())["best_params"] if a.run
              else json.loads(a.design.read_text())["params"])
    name = a.id or f"{(a.run or a.design).stem}_{datetime.now():%H%M%S}"
    run_dir = OUT_ROOT / name
    run_dir.mkdir(parents=True, exist_ok=True)
    spec = dict(oa.DEFAULT_SPEC)
    dut, ref = dut_netlist(params), dut_netlist(oa.DEFAULTS)
    (run_dir / "dut.cir").write_text(dut)
    (run_dir / "ref.cir").write_text(ref)
    task = task_text(dut, ref, spec)
    (run_dir / "task.txt").write_text(task)

    # our own verdict first (full library), so the comparison is fixed before the agent runs
    print("our evaluator (full library)...")
    ours = oa.evaluate(params, spec=spec, lib="full", timeout=900, jobs=8)
    print("  ", ours.summary().splitlines()[0])

    from google import genai
    client = genai.Client(http_options={"api_version": "v1beta"})
    img = base64.b64encode(SCHEMATIC_PNG.read_bytes()).decode()
    blocks = [{"type": "text", "text": task}, {"type": "image", "mime_type": "image/png", "data": img}]
    tool = SpiceTool(run_dir, a.max_decks)
    print(f"sign-off agent ({AGENT}, {MODEL}) starting; logs in {run_dir}")
    t0 = time.time()
    it, turns, usage = run_agent(client, run_dir, blocks, SYSTEM, tool, a.tokens, a.wall)
    text = it.output_text or ""
    (run_dir / "agent_final.md").write_text(text)
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    agent = json.loads(m.group(1)) if m else {}
    (run_dir / "signoff.json").write_text(json.dumps(agent, indent=1))
    cmp = compare(agent, ours) if agent else {"error": "agent returned no json block"}
    summary = {"status": it.status, "turns": turns, "spice_calls": tool.calls, "decks": tool.used,
               "tokens_last": usage[-1] if usage else None, "wall_s": round(time.time() - t0, 1),
               "agent_verdict": agent.get("verdict"), "calibration": agent.get("calibration"),
               **{k: v for k, v in cmp.items() if k != "rows"}}
    (run_dir / "comparison.json").write_text(json.dumps({**summary, "rows": cmp.get("rows")}, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
