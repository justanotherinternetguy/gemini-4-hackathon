"""Describe it and we build it, for ANALOG circuits.

text and/or image -> a new circuit written from scratch (SKY130, ngspice) -> verified by
simulation (with repair rounds) -> sized by the Gemini swarm (bench.ams_swarm.AmsSwarm) and by a
random-search baseline at the same simulation budget -> explained part by part.

  python -m create.analog --text "a differential amplifier with a current-mirror load, 30 dB, 10 MHz, under 100 uW"
  python -m create.analog --text "size this" --image sketch.png --budget 60 --out runs/create/my_ota
  python -m create.analog --text "a two-stage op-amp" --library        # pick the closest circuit we already have

Outputs in the out dir: request.json, circuit.yaml, netlist_final.sp, verify_log.jsonl,
sizing_summary.json, explain.json, explain.md, events.jsonl (replayable by a UI).

Honesty rules: a circuit that cannot be made to simulate is reported as a failure (exit code 2);
"spec met" is only ever the simulator's verdict at the typical corner, 27 C, schematic level.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from bench import amsbench as ab
from bench.ams_swarm import AmsSwarm, violations
from sizing import managed

UNDERSTAND_MODELS = ["gemini-4-hydrogen", "gemini-4-argon"]
WRITER_CHAIN = {"carbon": ["gemini-4-carbon", "gemini-4-hydrogen"],
                "hydrogen": ["gemini-4-hydrogen", "gemini-4-argon"],
                "argon": ["gemini-4-argon", "gemini-4-hydrogen"]}
EXPLAIN_MODEL = "gemini-4-hydrogen"
DECISION_MODEL = "gemini-decision-1"
MAX_REPAIRS = 4
N_RANDOM_CHECK = 12
METRICS = ["dc_gain_db", "ugbw", "power_dc", "phase_margin", "fom"]
METRIC_POST = {
    "dc_gain_db": {"scale": 1, "decimals": 2, "unit": "dB"},
    "ugbw": {"scale": 1e-6, "decimals": 2, "unit": "MHz"},
    "power_dc": {"scale": 1e6, "decimals": 2, "unit": "uW"},
    "phase_margin": {"scale": 1, "decimals": 1, "unit": "deg"},
    "fom": {"expr": "dc_gain_db * ugbw / power_dc", "scale": 1, "decimals": 3, "unit": ""},
}
PM_DEFAULT = 45.0
# Appended by us (not by the model) before `quit`: open-loop phase margin from the same AC sweep, as
# 180 deg minus the phase shift accumulated between 1 Hz and the unity-gain frequency.
PM_BLOCK = """if dc_gain_db > 0
  let ph_deg = 57.29577951 * cph(v(VOUT))
  let ph0 = ph_deg[0]
  meas ac ph_at_ugbw FIND ph_deg WHEN gain_db=0 CROSS=1
  let phase_margin = 180 - abs(ph_at_ugbw - ph0)
  print phase_margin
end
"""
KINDS = {
    "common_source": "single-transistor common-source amplifier (resistor or current-source load)",
    "diff_pair_mirror_load": "differential pair with a current-mirror load (five-transistor OTA)",
    "two_stage_miller": "two-stage Miller-compensated op-amp (diff pair + mirror, then a common-source stage)",
    "cascode": "cascode amplifier (single-ended, a cascode device stacked on the input device)",
    "telescopic": "telescopic cascode OTA",
    "folded_cascode": "folded cascode OTA",
    "source_follower": "source follower / buffer",
    "current_mirror": "current mirror",
    "other": "some other analog amplifier",
}
# (min DC gain dB, min unity-gain bandwidth MHz, max power uW, load pF) used when the user gave no number
DEFAULTS = {
    "common_source": (20, 10, 200, 1.0), "diff_pair_mirror_load": (30, 10, 200, 1.0),
    "two_stage_miller": (50, 10, 500, 1.0), "cascode": (35, 10, 200, 1.0),
    "telescopic": (45, 10, 300, 1.0), "folded_cascode": (45, 10, 500, 1.0),
    "source_follower": (-3, 10, 200, 1.0), "current_mirror": (0, 10, 200, 1.0), "other": (25, 10, 300, 1.0),
}
UNSUPPORTED_NOTE = ("This kind of circuit is not an amplifier with gain above 0 dB, so the gain / unity-gain "
                    "bandwidth / power metric set used here does not describe it well; results are unreliable.")

DEVICE_RULES = """DEVICE AND SIMULATOR RULES (SkyWater SKY130, ngspice 47, VDD = 1.8 V):
- Only these transistor models exist: sky130_fd_pr__nfet_01v8, sky130_fd_pr__pfet_01v8,
  sky130_fd_pr__nfet_01v8_lvt, sky130_fd_pr__pfet_01v8_lvt. No other models, no .model cards.
- Transistors are SUBCIRCUIT INSTANCES, so the instance name starts with x:
    xm1 drain gate source bulk sky130_fd_pr__nfet_01v8 w={W_in} l={L_in}
  W and L are plain numbers in MICRONS (w=2.0 l=0.5), never with a 'u' suffix. An integer multiplier m=4 is allowed.
- Limits: W >= 0.42; nfet L >= 0.15; pfet_01v8 L >= 0.15; pfet_01v8_lvt L >= 0.35. Keep W <= 100 and L <= 8.
- NMOS bulk goes to ground (VSS), PMOS bulk to VDD (or to its own source).
- Ideal resistors, capacitors, inductors and independent voltage/current sources are allowed.
- SPICE suffixes: M or m means MILLI (use MEG for mega); G means giga; write small values as 1e-12.
- Curly braces are ONLY for placeholders that are a param, a variable, or one of the built-ins
  {pdk_lib_path} {ota_subckt} {inst_pins} {subckt_name}. Never use braces for anything else, no .param lines.
"""

CONTROL_BLOCK = """.control
op
let vdd_current = abs(i(VDD))
let power_dc = vdd_current * {vdd}
print power_dc
ac dec 50 1 10g
let gain_db = vdb(VOUT)
let dc_gain_db = gain_db[0]
print dc_gain_db
if dc_gain_db > 0
  meas ac ugbw WHEN gain_db=0 CROSS=1
end
quit
.endc
.end"""

FALLBACK_EXAMPLE = """params: {L: 0.15, vdd: 1.8, vcm: 0.9, cload: 1e-12, ibias: 2e-05}
variables: W_tail [..], W_diff [..], W_load [..]
subckt_template:
.subckt FIVE_TRANSISTOR_OTA VSS VDD VOUT VINN VINP ID
xm5 ID ID VSS VSS sky130_fd_pr__nfet_01v8 w={W_tail} l={L}
xm4 SOURCE ID VSS VSS sky130_fd_pr__nfet_01v8 w={W_tail} l={L}
xm3 VOUT VINN SOURCE VSS sky130_fd_pr__nfet_01v8 w={W_diff} l={L}
xm0 NET8 VINP SOURCE VSS sky130_fd_pr__nfet_01v8 w={W_diff} l={L}
xm2 VOUT NET8 VDD VDD sky130_fd_pr__pfet_01v8 w={W_load} l={L}
xm1 NET8 NET8 VDD VDD sky130_fd_pr__pfet_01v8 w={W_load} l={L}
.ends FIVE_TRANSISTOR_OTA
testbench_template:
* OTA testbench
.lib {pdk_lib_path} tt
{ota_subckt}
VDD VDD 0 DC {vdd}
VSS VSS 0 DC 0
IBIAS VDD ID DC {ibias}
VINP VINP 0 DC {vcm} AC 0.5
VINN VINN 0 DC {vcm} AC -0.5
XOTA {inst_pins} {subckt_name}
CLOAD VOUT 0 {cload}
""" + CONTROL_BLOCK


# --------------------------------------------------------------------------- small helpers

class Events:
    """events.jsonl: one JSON object per line with `t` (seconds since start) and `type`."""

    def __init__(self, path: Path):
        self.path, self.t0, self.lock = path, time.perf_counter(), threading.Lock()
        path.write_text("")

    def emit(self, type_: str, **kw) -> None:
        rec = {"t": round(time.perf_counter() - self.t0, 2), "type": type_, **kw}
        with self.lock:
            with open(self.path, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        brief = {k: v for k, v in kw.items() if k in ("round", "ok", "n", "feasible", "model", "kind", "status", "source")}
        print(f"[{rec['t']:7.1f}s] {type_} {json.dumps(brief, default=str)}", flush=True)


class Llm:
    def __init__(self):
        self.client = managed.client()
        self.calls: list[dict] = []

    def gen(self, models: list[str], blocks: list[dict], schema: dict, thinking: str = "low",
            timeout: float = 150, what: str = "") -> tuple[dict, str]:
        """Try each model in order (the fallback on timeout / error). Returns (parsed JSON, model used)."""
        last = None
        for model in models:
            t0 = time.perf_counter()
            try:
                r = self.client.interactions.create(
                    model=model, input=blocks, generation_config={"thinking_level": thinking}, timeout=timeout,
                    response_format={"type": "text", "mime_type": "application/json", "schema": schema})
                out = json.loads(r.output_text)
                self.calls.append({"what": what, "model": model, "ok": True, "s": round(time.perf_counter() - t0, 1)})
                return out, model
            except Exception as e:
                last = e
                self.calls.append({"what": what, "model": model, "ok": False, "s": round(time.perf_counter() - t0, 1),
                                   "error": f"{type(e).__name__}: {str(e)[:200]}"})
                print(f"  ! {model} failed for {what} after {time.perf_counter() - t0:.0f}s: {type(e).__name__}: "
                      f"{str(e)[:120]}", flush=True)
        raise RuntimeError(f"all models failed for {what}: {last}")


def _img_block(path: Path) -> dict:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return {"type": "image", "mime_type": mime, "data": base64.b64encode(path.read_bytes()).decode()}


def _fmt_metrics(m: dict | None) -> str:
    if not m:
        return "n/a"
    units = {"dc_gain_db": "dB", "ugbw": "MHz", "power_dc": "uW", "phase_margin": "deg", "fom": ""}
    return ", ".join(f"{k}={v:.4g}{(' ' + units.get(k, '')).rstrip()}" if v is not None else f"{k}=n/a"
                     for k, v in m.items())


class _Lit(str):
    pass


yaml.add_representer(_Lit, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", str(s), style="|"))


def dump_yaml(cfg: dict) -> str:
    c = dict(cfg)
    for k in ("ota_subckt_template", "testbench_template"):
        c[k] = _Lit("\n".join(l.rstrip() for l in str(c[k]).splitlines()) + "\n")
    return yaml.dump(c, sort_keys=False, allow_unicode=True, width=120)


# --------------------------------------------------------------------------- 1. understand

UNDERSTAND_SCHEMA = {"type": "object", "properties": {
    "circuit_kind": {"type": "string", "enum": list(KINDS)},
    "restatement": {"type": "string", "description": "2-3 plain-English sentences: what circuit, what it is for, what the user wants from it"},
    "seen_in_image": {"type": "string", "description": "what the attached image shows (devices and how they connect); empty if no image"},
    "gain_db_min": {"type": "number", "nullable": True, "description": "minimum DC gain in dB, only if the user stated it"},
    "ugbw_mhz_min": {"type": "number", "nullable": True, "description": "minimum unity-gain bandwidth in MHz, only if stated"},
    "power_uw_max": {"type": "number", "nullable": True, "description": "maximum power in microwatts, only if stated"},
    "cload_pf": {"type": "number", "nullable": True, "description": "load capacitance in pF, only if stated"},
    "phase_margin_deg_min": {"type": "number", "nullable": True, "description": "minimum phase margin in degrees, only if stated"},
    "other_requirements": {"type": "array", "items": {"type": "string"}}},
    "required": ["circuit_kind", "restatement", "seen_in_image", "gain_db_min", "ugbw_mhz_min", "power_uw_max",
                 "cload_pf", "phase_margin_deg_min", "other_requirements"]}


def understand(llm: Llm, text: str, image: Path | None) -> dict:
    kinds = "\n".join(f"  {k}: {v}" for k, v in KINDS.items())
    prompt = f"""Someone is asking for an analog integrated circuit (CMOS, 1.8 V supply). They gave text and maybe an image
(a hand sketch or a textbook schematic). Work out what they want.
Circuit kinds:
{kinds}
Extract the numbers they STATED, converting units (0.1 mW = 100 uW; 2e7 Hz = 20 MHz; "bandwidth" for an
amplifier means unity-gain bandwidth; a gain of 100 V/V = 40 dB; a current limit I at 1.8 V is a power limit 1.8*I).
Use null for anything they did not state. Do not invent numbers.
If there is an image, the image decides the circuit kind; describe the devices and connections you see.

THE REQUEST: {text or '(no text; see the image)'}"""
    blocks = [{"type": "text", "text": prompt}]
    if image:
        blocks.append(_img_block(image))
    u, model = llm.gen(UNDERSTAND_MODELS, blocks, UNDERSTAND_SCHEMA, "low", 90, "understand")
    kind = u["circuit_kind"] if u.get("circuit_kind") in KINDS else "other"
    dg, du, dp, dc = DEFAULTS[kind]
    defaults_used = []

    def pick(key, dflt, label, unit):
        v = u.get(key)
        if v is None or not isinstance(v, (int, float)) or (key != "gain_db_min" and v <= 0):
            defaults_used.append(f"{label} = {dflt:g} {unit} (default, not stated)")
            return float(dflt)
        return float(v)

    g = pick("gain_db_min", dg, "minimum DC gain", "dB")
    b = pick("ugbw_mhz_min", du, "minimum unity-gain bandwidth", "MHz")
    p = pick("power_uw_max", dp, "maximum power", "uW")
    cl = pick("cload_pf", dc, "load capacitance", "pF")
    pm = pick("phase_margin_deg_min", PM_DEFAULT, "minimum phase margin", "deg")
    notes = [UNSUPPORTED_NOTE] if kind in ("source_follower", "current_mirror") else []
    return {"text": text, "image": str(image) if image else None, "circuit_kind": kind,
            "circuit_kind_description": KINDS[kind], "restatement": u.get("restatement", ""),
            "seen_in_image": u.get("seen_in_image", ""), "other_requirements": u.get("other_requirements") or [],
            "targets": {"dc_gain_db_min": g, "ugbw_mhz_min": b, "power_uw_max": p, "cload_pf": cl,
                        "phase_margin_deg_min": pm},
            "spec": f"dc_gain_db > {g:g} AND ugbw > {b:g} AND power_dc < {p:g} AND phase_margin > {pm:g}",
            "spec_units": {"dc_gain_db": "dB", "ugbw": "MHz", "power_dc": "uW", "phase_margin": "deg"},
            "defaults_used": defaults_used, "notes": notes, "model": model}


# --------------------------------------------------------------------------- 2. write the circuit

CIRCUIT_SCHEMA = {"type": "object", "properties": {
    "subckt_name": {"type": "string"},
    "description": {"type": "string", "description": "3-5 plain-English sentences on how the circuit works"},
    "subckt_pins": {"type": "array", "items": {"type": "string"}},
    "params": {"type": "array", "description": "fixed parameters (must include vdd)", "items": {
        "type": "object", "properties": {"name": {"type": "string"}, "value": {"type": "number"},
                                         "meaning": {"type": "string"}}, "required": ["name", "value", "meaning"]}},
    "variables": {"type": "array", "description": "the design variables the sizing agents will tune", "items": {
        "type": "object", "properties": {"name": {"type": "string"},
                                         "values": {"type": "array", "items": {"type": "number"}},
                                         "meaning": {"type": "string"}}, "required": ["name", "values", "meaning"]}},
    "subckt_template": {"type": "string", "description": "the .subckt ... .ends block with {placeholders}"},
    "testbench_template": {"type": "string", "description": "complete testbench from the title line to .end"},
    "roles": {"type": "array", "description": "EVERY device in the subcircuit and testbench bias network", "items": {
        "type": "object", "properties": {
            "device": {"type": "string", "description": "instance name, e.g. xm1"},
            "role": {"type": "string", "description": "one plain-English sentence: this device's job"},
            "variable": {"type": "string", "description": "the variable or param that sizes it (comma-separated if several)"}},
        "required": ["device", "role", "variable"]}},
    "design_notes": {"type": "string", "description": "the hand calculation behind the mid-grid values (currents, gm, gain estimate)"}},
    "required": ["subckt_name", "description", "subckt_pins", "params", "variables", "subckt_template",
                 "testbench_template", "roles", "design_notes"]}


def _reference_example() -> str:
    try:
        ref = ab.load("five_trans_ota")
        return (f"params: {json.dumps(ref['params'])}\nvariable: {list(ref['variable'])}  (grid {ref['W_values']})\n"
                f"subckt_pins: {ref['subckt_pins']}\nota_subckt_template:\n{ref['ota_subckt_template']}\n"
                f"testbench_template:\n" + "\n".join(l.strip() for l in ref["testbench_template"].splitlines()))
    except Exception:
        return FALLBACK_EXAMPLE


def writer_prompt(req: dict) -> str:
    t = req["targets"]
    return f"""You are a senior analog IC designer. Write a NEW circuit from scratch for this request and a testbench that
measures it. A SPICE simulator (ngspice) will run exactly what you write, so it must be syntactically right and it
must bias correctly. Then a team of agents will size it on the value grids you provide.

THE REQUEST: {req['text'] or '(see image)'}
UNDERSTOOD AS: {req['circuit_kind_description']}. {req['restatement']}
{('THE IMAGE SHOWS: ' + req['seen_in_image']) if req.get('seen_in_image') else ''}
SPEC THE SIZED CIRCUIT MUST MEET (typical corner): {req['spec']}   (dc_gain_db in dB, ugbw in MHz, power_dc in uW;
phase_margin in degrees is measured by lines we append to your control block, do not add them yourself)
Load capacitance: {t['cload_pf']:g} pF.

{DEVICE_RULES}
OUTPUT FORMAT (a JSON object; the two templates are Python str.format templates):
- subckt_template: ".subckt NAME pins ... .ends NAME", device sizes written as {{placeholders}}.
- testbench_template: complete and self-contained. It MUST contain, in this order:
    a title comment line, the line  .lib {{pdk_lib_path}} tt  (exactly once, exactly like that), the line  {{ota_subckt}}
    (the subcircuit is pasted there), the supply  VDD VDD 0 DC {{vdd}}  (the source must be named VDD, it measures
    power), bias generation (ideal current source into a diode-connected device, or a voltage source), the input
    common mode, the instance line  XDUT {{inst_pins}} {{subckt_name}}, the load  CLOAD VOUT 0 {{cload}}, and then
    this control block COPIED VERBATIM (the output node must be called VOUT at the top level):
{CONTROL_BLOCK}
- Drive: differential circuits get  VINP VINP 0 DC {{vcm}} AC 0.5  and  VINN VINN 0 DC {{vcm}} AC -0.5;
  single-ended circuits get AC 1 on the input source. dc_gain_db is the gain at 1 Hz.
- params: fixed numbers (vdd = 1.8, cload = {t['cload_pf'] * 1e-12:g}, vcm, ...). variables: 3 to 8 design variables, each
  with an ascending grid of 5 to 9 values (transistor W and L in microns, bias current in amps, compensation
  capacitor in farads, resistors in ohms). Matched devices share a variable. Every placeholder must be a param or
  a variable. The MID-GRID value of every variable must be a working, sensible design, and the grids must be wide
  enough to contain designs that meet the spec above (check power = 1.8 V x total current against the power limit:
  include bias currents small enough).
- roles: every device (transistors, bias sources, R, C) with a one-sentence plain-English job and its sizing variable.

BIASING ADVICE (this is where designs usually fail):
- Every transistor in the signal path must be in saturation at the mid-grid point: check VGS - VTH and the
  headroom of every stack against 1.8 V (SKY130 VTH is roughly 0.5-0.7 V for nfet, 0.6-0.9 V for pfet at short L;
  mobility ratio is about 3, so a PMOS needs about 3x the W of an NMOS for the same current density).
- Generate bias voltages with diode-connected mirror devices fed by an ideal current source, not hard-coded
  gate voltages, so the bias tracks the sizes.
- A single-ended open-loop high-gain amplifier (two-stage op-amp, or a common-source / cascode stage with a
  current-source load) will rail its output unless the DC operating point is set by feedback. Use the standard
  trick in the testbench: connect the output to the inverting input with a huge inductor (LFB VOUT VINN 1e9: a short
  at DC, open for AC) and couple the AC source into that node through a huge capacitor (CIN VINN VACN 1e9 with
  VACN VACN 0 DC 0 AC -0.5). For a single common-source stage: gate biased from the output through the inductor,
  AC source coupled through the capacitor. A five-transistor OTA with a mirror load self-biases and does not need this.
- A two-stage op-amp needs a Miller capacitor (a variable) between the first-stage output and VOUT.

FORMAT EXAMPLE (a different, existing circuit; follow its style, do not copy its sizes):
{_reference_example()}
"""


def build_cfg(c: dict, req: dict) -> dict:
    """Model JSON -> an amsbench-style config dict. Raises ValueError with a message the model can act on."""
    params = {str(p["name"]): p["value"] for p in c.get("params") or []}
    params.setdefault("vdd", 1.8)
    variables = c.get("variables") or []
    if not variables:
        raise ValueError("no design variables were given")
    cfg: dict = {"user_specs": req["restatement"] or req["text"], "user_specs_metric": req["spec"],
                 "params": params, "variable": {str(v["name"]): None for v in variables}}
    for v in variables:
        vals = sorted({float(x) for x in v.get("values") or []})
        if len(vals) < 2:
            raise ValueError(f"variable {v['name']} needs a grid of at least 2 values, got {v.get('values')}")
        if v["name"] in params:
            del cfg["params"][v["name"]]
        cfg[f"{v['name']}_values"] = vals
    pins = [str(p) for p in c.get("subckt_pins") or []]
    cfg.update(subckt_name=str(c["subckt_name"]), subckt_pins=pins, testbench_signals={p: p for p in pins},
               metrics=list(METRICS), metric_post=json.loads(json.dumps(METRIC_POST)),
               ota_subckt_template=str(c["subckt_template"]).strip() + "\n",
               testbench_template=str(c["testbench_template"]).strip() + "\n",
               roles=c.get("roles") or [], description=c.get("description", ""),
               variable_meaning={str(v["name"]): v.get("meaning", "") for v in variables},
               design_notes=c.get("design_notes", ""))
    if "{ota_subckt}" not in cfg["testbench_template"]:
        raise ValueError("testbench_template does not contain the {ota_subckt} line")
    return add_pm(cfg)


def add_pm(cfg: dict) -> dict:
    """Append our phase-margin measurement to the testbench (idempotent) and register the metric."""
    tb = cfg["testbench_template"]
    if "phase_margin" not in tb:
        hits = [m.start() for m in re.finditer(r"(?im)^[ \t]*quit[ \t]*$", tb)]
        if not hits:
            raise ValueError("the control block must end with a `quit` line before .endc (copy the block verbatim)")
        cfg["testbench_template"] = tb[:hits[-1]] + PM_BLOCK + tb[hits[-1]:]
    cfg["metrics"] = list(METRICS)
    cfg["metric_post"] = json.loads(json.dumps(METRIC_POST))
    if "phase_margin" not in cfg["user_specs_metric"]:
        cfg["user_specs_metric"] += f" AND phase_margin > {PM_DEFAULT:g}"
    return cfg


# --------------------------------------------------------------------------- 3. verify by simulation

_LOG_PAT = re.compile(r"error|singular|no such|unknown|fatal|fail|not found|too few|too many|could not|"
                      r"timestep|undefined|mismatch|unable|cannot", re.I)


def _log_excerpt(log: str, limit: int = 1800) -> str:
    lines = [l.strip() for l in log.splitlines() if _LOG_PAT.search(l) and "option noinit" not in l]
    seen, out = set(), []
    for l in lines:
        if l not in seen:
            seen.add(l)
            out.append(l)
    return "\n".join(out[:30])[:limit]


def op_dump(cfg: dict, point: dict, limit: int = 60) -> str:
    """Node voltages at the DC operating point (for the 'bias point is wrong' repair message)."""
    try:
        net = ab.render(cfg, point)
        net2, n = re.subn(r"(?im)^(\s*)op\s*$", r"\1op\n\1print all", net, count=1)
        if not n:
            return ""
        with tempfile.TemporaryDirectory(prefix="create_op_") as td:
            (Path(td) / ".spiceinit").write_text(ab.SPICEINIT.read_text())
            (Path(td) / "deck.sp").write_text(net2)
            p = subprocess.run(["ngspice", "-b", "deck.sp"], cwd=td, capture_output=True, text=True, timeout=60)
        rows = []
        for l in p.stdout.splitlines():
            m = re.match(rf"^\s*([\w.#\[\]()-]+)\s*=\s*({ab._NUM})\s*$", l)
            if m and not re.search(r"#|power_dc|dc_gain_db|vdd_current|ugbw|phase_margin|ph_deg|ph0", m.group(1)):
                rows.append(f"{m.group(1)} = {float(m.group(2)):.4g}")
        return "\n".join(rows[:limit])
    except Exception as e:
        return f"(operating point dump failed: {e})"


def verify(cfg: dict, keep: Path, seed: int = 0) -> dict:
    """Mid-grid point must simulate with every metric; at least one of 12 random points must show gain > 0 dB."""
    mid = ab.mid_point(cfg)
    try:
        ab.parse_specs(cfg["user_specs_metric"])
        ab.render(cfg, mid)
    except Exception as e:
        return {"ok": False, "stage": "render", "error": f"the templates could not be rendered: {type(e).__name__}: {e}"}
    r = ab.simulate(cfg, mid, "tt", 27, keep_dir=keep)
    log = (keep / "ngspice.log").read_text() if (keep / "ngspice.log").exists() else ""
    out: dict = {"mid_point": mid, "mid_metrics": r.metrics}
    gain, power = r.metrics.get("dc_gain_db"), r.metrics.get("power_dc")
    if gain is None or power is None:
        return {**out, "ok": False, "stage": "ngspice",
                "error": f"ngspice did not produce the metrics at the mid-grid point ({r.error}).\n"
                         f"ngspice messages:\n{_log_excerpt(log) or log[-1200:]}"}
    rng = random.Random(seed)
    pts = [{v: rng.choice(ab.grid(cfg, v)) for v in ab.variables(cfg)} for _ in range(N_RANDOM_CHECK)]
    with ThreadPoolExecutor(6) as ex:
        rs = list(ex.map(lambda p: ab.simulate(cfg, p, "tt", 27), pts))
    pos = [x for x in rs if x.ok and (x.metrics.get("dc_gain_db") or -1) > 0]
    out.update(random_points=len(rs), random_simulated=sum(x.ok or x.metrics.get("dc_gain_db") is not None for x in rs),
               random_positive_gain=len(pos),
               random_gain_range=[min((x.metrics["dc_gain_db"] for x in rs if x.metrics.get("dc_gain_db") is not None), default=None),
                                  max((x.metrics["dc_gain_db"] for x in rs if x.metrics.get("dc_gain_db") is not None), default=None)])
    if power < 1e-3:
        return {**out, "ok": False, "stage": "bias",
                "error": f"the circuit draws no supply current at the mid-grid point (power_dc = {power:.3g} uW): it is "
                         f"switched off. The bias point is wrong.\nDC node voltages:\n{op_dump(cfg, mid)}"}
    if gain > 0 and r.metrics.get("ugbw") is not None and r.metrics.get("phase_margin") is None:
        return {**out, "ok": False, "stage": "measure",
                "error": "the gain and unity-gain frequency were measured but the phase margin was not: the control "
                         "block must define the vector gain_db = vdb(VOUT) from an `ac` analysis and end with `quit`. "
                         f"ngspice messages:\n{_log_excerpt(log)}"}
    if not r.ok or gain <= 0:
        return {**out, "ok": False, "stage": "bias",
                "error": f"at the mid-grid point the DC gain is {gain:.2f} dB (it must be clearly positive, so no "
                         f"unity-gain frequency exists) with power {power:.4g} uW; {len(pos)} of {len(rs)} random grid "
                         f"points have positive gain. The bias point is wrong: some transistor is out of saturation "
                         f"or the output is stuck at a rail. Fix the bias and centre the grids on a working design.\n"
                         f"DC node voltages at the mid-grid point:\n{op_dump(cfg, mid)}"}
    if not pos:
        return {**out, "ok": False, "stage": "bias",
                "error": f"none of {len(rs)} random grid points gives a positive DC gain in dB. The bias point is wrong "
                         f"over most of the grid.\nDC node voltages at the mid-grid point:\n{op_dump(cfg, mid)}"}
    if gain > 160:
        return {**out, "ok": False, "stage": "sanity",
                "error": f"the DC gain of {gain:.1f} dB is not physical; the testbench is not measuring the amplifier."}
    out["ok"] = True
    return out


def write_and_verify(llm: Llm, req: dict, image: Path | None, out: Path, ev: Events, writer: str,
                     timeout: float) -> tuple[dict | None, list[dict]]:
    chain = list(WRITER_CHAIN[writer])
    base = writer_prompt(req)
    blocks = [{"type": "text", "text": base}]
    if image:
        blocks.append(_img_block(image))
    log_path = out / "verify_log.jsonl"
    log_path.write_text("")
    rounds: list[dict] = []
    prev_json, cfg = None, None
    for rnd in range(MAX_REPAIRS + 1):
        t0 = time.perf_counter()
        rec: dict = {"round": rnd, "kind": "generate" if rnd == 0 else "repair"}
        try:
            c, model = llm.gen(chain, blocks, CIRCUIT_SCHEMA, "medium" if "carbon" in chain[0] else "low",
                               timeout, f"write_circuit_round{rnd}")
        except Exception as e:
            rec.update(ok=False, stage="llm", error=str(e)[:400])
            rounds.append(rec)
            with open(log_path, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
            ev.emit("verify_round", round=rnd, ok=False, error=rec["error"])
            break
        if model != chain[0]:        # the first model timed out: stay on the fallback for the repairs
            chain = [model] + [m for m in chain if m != model and m != chain[0]]
        rec.update(model=model, llm_seconds=round(time.perf_counter() - t0, 1))
        prev_json = c
        try:
            cfg = build_cfg(c, req)
            (out / f"circuit_round{rnd}.yaml").write_text(dump_yaml(cfg))
            ev.emit("circuit_written", round=rnd, model=model, subckt_name=cfg["subckt_name"],
                    variables=ab.variables(cfg), n_devices=len(cfg["roles"]), netlist=cfg["ota_subckt_template"])
            v = verify(cfg, out / "verify" / f"round{rnd}")
        except Exception as e:
            cfg, v = None, {"ok": False, "stage": "format", "error": f"{type(e).__name__}: {e}"}
        rec.update(v)
        rounds.append(rec)
        with open(log_path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        ev.emit("verify_round", round=rnd, ok=bool(v["ok"]), error=v.get("error"), stage=v.get("stage"),
                metrics=v.get("mid_metrics"), random_positive_gain=v.get("random_positive_gain"), model=model)
        if v["ok"]:
            return cfg, rounds
        netlist = ""
        if cfg is not None:
            try:
                netlist = ab.render(cfg, ab.mid_point(cfg))
            except Exception:
                netlist = cfg["ota_subckt_template"] + "\n" + cfg["testbench_template"]
        repair = f"""Your circuit was run through ngspice and it FAILED verification (repair round {rnd + 1} of {MAX_REPAIRS}).

THE PROBLEM:
{v.get('error')}

YOUR PREVIOUS ANSWER (JSON):
{json.dumps(prev_json)[:9000]}

THE NETLIST AS SIMULATED (mid-grid point, placeholders filled in):
{netlist[:6000]}

Work out the root cause from the messages and node voltages, fix it, and return the COMPLETE corrected JSON object
(same format, every field). Keep what was right. Re-check every rule in the original instructions."""
        blocks = [{"type": "text", "text": base + "\n\n" + repair}]
        if image:
            blocks.append(_img_block(image))
    return None, rounds


# --------------------------------------------------------------------------- 4. size it

class TracedSwarm(AmsSwarm):
    """AmsSwarm that reports every simulated candidate to events.jsonl."""
    emit = None

    def _eval(self, point, who, why):
        rec = super()._eval(point, who, why)
        if rec is not None and self.emit:
            self.emit("sizing_candidate", source="swarm", n=rec["n"], metrics=rec["metrics"],
                      feasible=rec["feasible"], point=rec["point"], who=who, why=why)
        return rec


def _score(h: dict):
    if h["feasible"]:
        return (0, -(h["metrics"].get("fom") or 0))
    return (1, sum(max(0.0, x) for x in h["viol"]))


def random_search(cfg: dict, budget: int, seed: int, ev: Events) -> dict:
    cons = ab.parse_specs(cfg["user_specs_metric"])
    names = ab.variables(cfg)
    rng = random.Random(1000 + seed)
    space = 1
    for v in names:
        space *= len(ab.grid(cfg, v))
    pts, seen = [], set()
    while len(pts) < min(budget, space):
        p = {v: rng.choice(ab.grid(cfg, v)) for v in names}
        k = tuple(p.values())
        if k not in seen:
            seen.add(k)
            pts.append(p)
    with ThreadPoolExecutor(8) as ex:
        rs = list(ex.map(lambda p: ab.simulate(cfg, p, "tt", 27), pts))
    hist = []
    for i, (p, r) in enumerate(zip(pts, rs), 1):
        viol = violations(cons, r.metrics)
        h = {"n": i, "point": p, "metrics": r.metrics, "ok": r.ok, "viol": viol, "feasible": all(x <= 0 for x in viol)}
        hist.append(h)
        ev.emit("baseline_candidate", source="random", n=i, metrics=r.metrics, feasible=h["feasible"], point=p)
    feas = [h for h in hist if h["feasible"]]
    best = min(hist, key=_score) if hist else None
    return {"sampler": "random_search", "budget": budget, "unique_sims": len(hist), "success": bool(feas),
            "sims_to_first_pass": feas[0]["n"] if feas else None, "n_feasible": len(feas),
            "simulated_ok": sum(h["ok"] for h in hist),
            "best_point": best["point"] if best else None, "best_metrics": best["metrics"] if best else None,
            "best_is_feasible": bool(best and best["feasible"]), "trials": hist}


def size(cfg: dict, budget: int, seed: int, workers: int, per_worker: int, deep: bool, ev: Events) -> dict:
    ev.emit("sizing_started", budget=budget, spec=cfg["user_specs_metric"])
    rnd = random_search(cfg, budget, seed, ev)
    sw = TracedSwarm(cfg, seed, budget, workers, per_worker, deep)
    sw.emit = ev.emit
    s = sw.run()
    ranked = sorted(sw.hist, key=_score)
    best = ranked[0] if ranked else None
    start = sw.hist[0] if sw.hist else None
    swarm = {"sampler": "gemini_swarm", "budget": budget, "unique_sims": len(sw.hist), "success": s["success"],
             "sims_to_first_pass": s["sims_to_first_pass"], "n_feasible": s["n_feasible"],
             "simulated_ok": sum(h["ok"] for h in sw.hist),
             "best_point": best["point"] if best else None, "best_metrics": best["metrics"] if best else None,
             "best_is_feasible": bool(best and best["feasible"]), "best_why": best.get("why") if best else None,
             "best_who": best.get("who") if best else None, "recheck_full_pdk_pass": s["recheck_pass"],
             "llm_calls": s["llm_calls"], "tokens": s["tokens"], "rounds": s["rounds"], "wall_s": s["wall_s"],
             "last_plan": sw.plan, "trials": sw.hist}
    return {"spec": cfg["user_specs_metric"], "budget": budget, "corner": "tt, 27 C, schematic level",
            "start": {"point": start["point"], "metrics": start["metrics"], "feasible": start["feasible"]} if start else None,
            "swarm": swarm, "random": rnd}


# --------------------------------------------------------------------------- 5. explain

CAVEATS = [
    "Verified at the typical process corner (tt), 27 C only: no slow/fast corners, no temperature sweep, no mismatch / Monte Carlo.",
    "Schematic-level simulation with a slimmed SKY130 model library: no layout, no parasitic extraction.",
    "Only DC gain, unity-gain bandwidth, supply power and open-loop phase margin (from one small-signal AC sweep) "
    "were checked: no transient / step response, and noise, offset, swing, slew rate and PSRR were NOT checked.",
    "The circuit was written by a language model and checked by simulation, not reviewed by a designer.",
    "The sizes come from a discrete value grid and a small simulation budget; they are a starting point, not a sign-off.",
]


def explain(llm: Llm, req: dict, cfg: dict, rounds: list[dict], sizing: dict, use_llm: bool = True) -> dict:
    sw, rd, start = sizing["swarm"], sizing["random"], sizing["start"]
    best = sw["best_point"] or {}
    params = cfg.get("params") or {}
    parts = []
    for r in cfg.get("roles") or []:
        names = [n.strip() for n in re.split(r"[,\s]+", str(r.get("variable", ""))) if n.strip()]
        sizes = {n: best.get(n, params.get(n)) for n in names if n in best or n in params}
        parts.append({"device": r.get("device"), "role": r.get("role"), "variable": r.get("variable"), "final_size": sizes})
    changed = []
    for v in ab.variables(cfg):
        a, b = (start or {}).get("point", {}).get(v), best.get(v)
        if a is not None and b is not None and a != b:
            changed.append({"variable": v, "meaning": (cfg.get("variable_meaning") or {}).get(v, ""),
                            "from": a, "to": b, "direction": "up" if b > a else "down"})
    rationales = []
    for h in sorted([h for h in sw["trials"] if h["feasible"]] or sw["trials"], key=_score)[:5]:
        if h.get("why"):
            rationales.append({"n": h["n"], "who": h.get("who"), "why": h["why"], "feasible": h["feasible"],
                               "metrics": h["metrics"]})
    met = bool(sw["success"])
    ex = {"status": "spec_met" if met else "simulates_but_spec_not_met",
          "request": req["text"], "restatement": req["restatement"], "circuit_kind": req["circuit_kind_description"],
          "spec": req["spec"], "defaults_used": req["defaults_used"],
          "circuit_description": cfg.get("description", ""), "design_notes": cfg.get("design_notes", ""),
          "verification": {"rounds": len(rounds), "repair_rounds": len(rounds) - 1,
                           "writer_models": sorted({r.get("model") for r in rounds if r.get("model")}),
                           "problems_fixed": [{"round": r["round"], "stage": r.get("stage"), "error": str(r.get("error"))[:300]}
                                              for r in rounds if not r.get("ok")]},
          "parts": parts, "final_sizes": best, "fixed_parameters": params,
          "before": start, "after": {"point": best, "metrics": sw["best_metrics"], "feasible": sw["best_is_feasible"]},
          "what_the_swarm_changed": changed, "swarm_rationales": rationales, "swarm_last_plan": sw.get("last_plan", ""),
          "spec_met": met, "spec_met_full_pdk_recheck": sw.get("recheck_full_pdk_pass"),
          "comparison": {k: {"success": d["success"], "sims_to_first_pass": d["sims_to_first_pass"],
                             "n_feasible": d["n_feasible"], "best_metrics": d["best_metrics"],
                             "best_is_feasible": d["best_is_feasible"]} for k, d in (("swarm", sw), ("random", rd))},
          "caveats": CAVEATS + req.get("notes", []), "narrative": ""}
    if use_llm:
        try:
            prompt = f"""Explain this circuit-sizing result to a smart person who is not an analog designer, in at most 170 words,
plain English, no bullet points, no hype. Say what the circuit is, what the sizing agents changed from the starting
design and why (use their own reasons), and state plainly whether the spec was met. Use only the facts below; do
not invent numbers.
FACTS: {json.dumps({k: ex[k] for k in ('restatement', 'spec', 'circuit_description', 'before', 'after', 'what_the_swarm_changed', 'swarm_rationales', 'swarm_last_plan', 'spec_met', 'comparison')}, default=str)[:7000]}
Units: dc_gain_db dB, ugbw MHz, power_dc uW, phase_margin degrees, widths/lengths in microns."""
            n, _ = llm.gen([EXPLAIN_MODEL], [{"type": "text", "text": prompt}],
                           {"type": "object", "properties": {"narrative": {"type": "string"}}, "required": ["narrative"]},
                           "low", 60, "explain")
            ex["narrative"] = n.get("narrative", "")
        except Exception as e:
            ex["narrative_error"] = str(e)[:200]
    return ex


def explain_md(ex: dict) -> str:
    L = [f"# {ex['circuit_kind']}", "", f"**Request:** {ex['request']}", "", f"**Understood as:** {ex['restatement']}", "",
         f"**Spec:** `{ex['spec']}` (dB, MHz, uW, degrees)"]
    if ex["defaults_used"]:
        L.append("\nDefaults filled in (not stated by the user): " + "; ".join(ex["defaults_used"]))
    L += ["", f"## Result: {'SPEC MET' if ex['spec_met'] else 'SPEC NOT MET'} (typical corner, simulation only)", ""]
    if ex.get("narrative"):
        L += [ex["narrative"], ""]
    b, a = ex.get("before") or {}, ex.get("after") or {}
    L += ["| | DC gain (dB) | UGBW (MHz) | power (uW) | phase margin (deg) | meets spec |", "|---|---|---|---|---|---|"]
    for name, d in (("starting design (mid-grid)", b), ("after the swarm", a)):
        m = d.get("metrics") or {}
        g = lambda k: f"{m[k]:.4g}" if m.get(k) is not None else "n/a"
        L.append(f"| {name} | {g('dc_gain_db')} | {g('ugbw')} | {g('power_dc')} | {g('phase_margin')} | {'yes' if d.get('feasible') else 'no'} |")
    c = ex["comparison"]
    L += ["", "## Swarm vs random search (same simulation budget)", "",
          "| | spec met | simulations to first pass | passing designs | best design |", "|---|---|---|---|---|"]
    for k in ("swarm", "random"):
        d = c[k]
        L.append(f"| {k} | {'yes' if d['success'] else 'no'} | {d['sims_to_first_pass'] or '-'} | {d['n_feasible']} | "
                 f"{_fmt_metrics(d['best_metrics'])} |")
    L += ["", "## How the circuit works", "", ex["circuit_description"], "", "## Parts", "",
          "| device | job | sized by | final size |", "|---|---|---|---|"]
    for p in ex["parts"]:
        fs = ", ".join(f"{k}={v:g}" if isinstance(v, (int, float)) else f"{k}={v}" for k, v in (p["final_size"] or {}).items())
        L.append(f"| {p['device']} | {p['role']} | {p['variable']} | {fs or '-'} |")
    L += ["", "## What the swarm changed and why", ""]
    for ch in ex["what_the_swarm_changed"]:
        L.append(f"- `{ch['variable']}` {ch['from']:g} -> {ch['to']:g} ({ch['direction']}) - {ch['meaning']}")
    if not ex["what_the_swarm_changed"]:
        L.append("- nothing: the starting design was kept")
    if ex["swarm_rationales"]:
        L += ["", "The agents' own reasons for the best candidates:"]
        L += [f"- #{r['n']} ({r['who']}, {'pass' if r['feasible'] else 'fail'}): {r['why']}" for r in ex["swarm_rationales"]]
    v = ex["verification"]
    L += ["", "## Verification", "",
          f"The netlist simulated after {v['repair_rounds']} repair round(s) (writer: {', '.join(v['writer_models'])})."]
    L += [f"- round {p['round']} failed ({p['stage']}): {p['error']}" for p in v["problems_fixed"]]
    if ex.get("spec_met_full_pdk_recheck") is not None:
        L.append(f"\nRe-check of the best design with the full SKY130 library: {'pass' if ex['spec_met_full_pdk_recheck'] else 'FAIL'}.")
    L += ["", "## Caveats", ""] + [f"- {c_}" for c_ in ex["caveats"]]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- library mode (safe fallback)

def library_candidates() -> dict[str, str]:
    from sizing import intake
    cands = {f"opamp_{k}": v for k, v in intake.TOPOLOGIES.items()}
    for name in ab.list_circuits(generic_only=False):
        try:
            cfg = ab.load(name)
            cands[f"bench_{name}"] = f"{name.replace('_', ' ')}: {str(cfg.get('user_specs', ''))[:220]}"
        except Exception:
            pass
    return cands


def run_library(llm: Llm, text: str, image: Path | None, out: Path, ev: Events) -> int:
    cands = library_candidates()
    blocks = [{"type": "text", "text": "A user is asking for an analog circuit. Their request: " + (text or "(see the image)")}]
    if image:
        blocks.append(_img_block(image))
    res = llm.client.decisions.create(model=DECISION_MODEL, input=blocks, questions={"circuit": {
        "type": "choice", "instructions": "Which circuit in our library is the closest match to what the user is asking for?",
        "criteria": cands}})
    a = res.answers["circuit"]
    probs = dict(a.probabilities) if isinstance(a.probabilities, dict) else {}
    top = sorted(probs.items(), key=lambda kv: -kv[1])[:4]
    why = ""
    try:
        w, _ = llm.gen([EXPLAIN_MODEL], [{"type": "text", "text":
            f"A user asked for: {text or '(an image)'}\nWe picked this existing circuit from our library: {a.choice} "
            f"({cands[a.choice]}).\nRunner-up options: {json.dumps([(k, cands[k]) for k, _ in top[1:]])}\n"
            "In 2-3 plain sentences say why it is the closest match and what differs from the request."}],
            {"type": "object", "properties": {"why": {"type": "string"}}, "required": ["why"]}, "low", 60, "library_why")
        why = w.get("why", "")
    except Exception as e:
        why = f"(no explanation: {e})"
    kind, name = a.choice.split("_", 1)
    cmd = (f".venv/bin/python -m sizing.swarm --circuit {name}  # two-stage op-amp '{name}' (see sizing/opamp2stg.py CIRCUITS)"
           if kind == "opamp" else f".venv/bin/python -m bench.ams_swarm {name} --seeds 0 --budget 100")
    choice = {"mode": "library", "text": text, "image": str(image) if image else None, "choice": a.choice,
              "source": "sizing.opamp2stg.CIRCUITS" if kind == "opamp" else f"benchmark YAML {name}.yaml",
              "description": cands[a.choice], "confidence": probs.get(a.choice), "top_probabilities": top,
              "why": why, "next_step_hint": cmd, "n_candidates": len(cands)}
    (out / "request.json").write_text(json.dumps(choice, indent=1, default=str))
    (out / "explain.json").write_text(json.dumps(choice, indent=1, default=str))
    (out / "explain.md").write_text(
        f"# Closest circuit in the library: {a.choice}\n\n**Request:** {text}\n\n{cands[a.choice]}\n\n"
        f"**Why:** {why}\n\nConfidence {probs.get(a.choice)}; runners-up: {top[1:]}\n\n"
        f"Nothing was simulated in library mode. To size it (hint, check the module's --help): `{cmd}`\n")
    ev.emit("understood", mode="library", choice=a.choice, confidence=probs.get(a.choice), why=why)
    ev.emit("done", status="library_choice", choice=a.choice)
    print(json.dumps(choice, indent=1, default=str))
    return 0


# --------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", default="")
    ap.add_argument("--image", type=Path)
    ap.add_argument("--budget", type=int, default=60, help="ngspice simulations for the swarm (and for random search)")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--library", action="store_true", help="pick the closest existing circuit instead of writing one")
    ap.add_argument("--writer", default="carbon", choices=list(WRITER_CHAIN), help="model that writes the netlist")
    ap.add_argument("--writer-timeout", type=float, default=150)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--per-worker", type=int, default=3)
    ap.add_argument("--deep", action="store_true", help="also run the slow Carbon deep planner in the swarm")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume", action="store_true", help="reuse request.json + circuit.yaml in --out (no LLM rewrite)")
    ap.add_argument("--no-size", action="store_true", help="stop after verification")
    a = ap.parse_args()
    if not a.text and not a.image:
        ap.error("give --text and/or --image")
    if a.image and not a.image.exists():
        ap.error(f"image not found: {a.image}")
    out = a.out or (ab.ROOT / "runs" / "create" / time.strftime("%m%d_%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    ev = Events(out / "events.jsonl")
    llm = Llm()
    if a.library:
        return run_library(llm, a.text, a.image, out, ev)

    # 1. understand
    if a.resume and (out / "request.json").exists() and (out / "circuit.yaml").exists():
        req = json.loads((out / "request.json").read_text())
        cfg = yaml.safe_load((out / "circuit.yaml").read_text())
        if "phase_margin" not in req["spec"]:       # runs written before the phase-margin check existed
            req["spec"] += f" AND phase_margin > {PM_DEFAULT:g}"
            req["defaults_used"].append(f"minimum phase margin = {PM_DEFAULT:g} deg (default, not stated)")
            (out / "request.json").write_text(json.dumps(req, indent=1))
        cfg["user_specs_metric"] = req["spec"]
        cfg = add_pm(cfg)
        ev.emit("understood", kind=req["circuit_kind"], restatement=req["restatement"], spec=req["spec"],
                defaults_used=req["defaults_used"], model=req.get("model"), resumed=True)
        ev.emit("circuit_written", round=0, model="(resumed from circuit.yaml)", subckt_name=cfg["subckt_name"],
                variables=ab.variables(cfg), n_devices=len(cfg.get("roles") or []), netlist=cfg["ota_subckt_template"],
                resumed=True)
        rounds = [json.loads(l) for l in (out / "verify_log.jsonl").read_text().splitlines() if l.strip()] \
            if (out / "verify_log.jsonl").exists() else []
        v = verify(cfg, out / "verify" / "resume")
        ev.emit("verify_round", round=len(rounds), ok=bool(v["ok"]), error=v.get("error"), metrics=v.get("mid_metrics"))
        if not v["ok"]:
            cfg = None
    else:
        req = understand(llm, a.text, a.image)
        (out / "request.json").write_text(json.dumps(req, indent=1))
        ev.emit("understood", kind=req["circuit_kind"], restatement=req["restatement"], spec=req["spec"],
                defaults_used=req["defaults_used"], model=req["model"])
        print(f"  understood: {req['circuit_kind']} | spec: {req['spec']} | defaults: {req['defaults_used']}", flush=True)
        # 2 + 3. write, verify, repair
        cfg, rounds = write_and_verify(llm, req, a.image, out, ev, a.writer, a.writer_timeout)

    if cfg is None:
        fail = {"status": "failed_to_simulate", "request": req["text"], "restatement": req["restatement"],
                "spec": req["spec"], "rounds": len(rounds),
                "problems": [{"round": r.get("round"), "stage": r.get("stage"), "error": str(r.get("error"))[:600]} for r in rounds],
                "message": f"No circuit that simulates correctly was produced in {len(rounds)} round(s) "
                           f"(1 generation + up to {MAX_REPAIRS} repairs). Nothing was sized. "
                           "Try --library to pick the closest circuit we already have.",
                "llm_calls": llm.calls}
        (out / "explain.json").write_text(json.dumps(fail, indent=1, default=str))
        (out / "explain.md").write_text(
            f"# FAILED: no working circuit\n\n**Request:** {req['text']}\n\n{fail['message']}\n\n"
            + "\n".join(f"- round {p['round']} ({p['stage']}): {p['error']}" for p in fail["problems"]) + "\n")
        (out / "sizing_summary.json").write_text(json.dumps({"status": "not_run", "reason": "circuit failed verification"}))
        ev.emit("done", status="failed_to_simulate", rounds=len(rounds))
        print(f"\nFAILED to produce a circuit that simulates; see {out}/explain.md")
        return 2
    (out / "circuit.yaml").write_text(dump_yaml(cfg))
    if a.no_size:
        (out / "netlist_final.sp").write_text(ab.render(cfg, ab.mid_point(cfg)))
        ev.emit("done", status="verified_not_sized")
        return 0

    # 4. size
    sizing = size(cfg, a.budget, a.seed, a.workers, a.per_worker, a.deep, ev)
    sw, rd = sizing["swarm"], sizing["random"]
    best_point = sw["best_point"] or ab.mid_point(cfg)
    (out / "netlist_final.sp").write_text(ab.render(cfg, best_point))
    (out / "sizing_summary.json").write_text(json.dumps(sizing, indent=1, default=str))

    # 5. explain
    ex = explain(llm, req, cfg, rounds, sizing)
    ex["llm_calls"] = {"create": llm.calls, "swarm": sw["llm_calls"]}
    (out / "explain.json").write_text(json.dumps(ex, indent=1, default=str))
    (out / "explain.md").write_text(explain_md(ex))
    ev.emit("done", status=ex["status"], spec_met=ex["spec_met"], best_point=best_point, best_metrics=sw["best_metrics"],
            swarm_first_pass=sw["sims_to_first_pass"], random_first_pass=rd["sims_to_first_pass"],
            random_success=rd["success"], repair_rounds=len(rounds) - 1)
    print(f"\n{'SPEC MET' if ex['spec_met'] else 'SPEC NOT MET'}: {req['spec']}")
    print(f"  simulated after {len(rounds) - 1} repair round(s); writer {ex['verification']['writer_models']}")
    print(f"  start : {_fmt_metrics((sizing['start'] or {}).get('metrics'))}")
    print(f"  swarm : success={sw['success']} first pass at sim {sw['sims_to_first_pass']} | best {_fmt_metrics(sw['best_metrics'])}")
    print(f"  random: success={rd['success']} first pass at sim {rd['sims_to_first_pass']} | best {_fmt_metrics(rd['best_metrics'])}")
    print(f"  LLM calls: create={len(llm.calls)} swarm={sw['llm_calls']}   out: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
