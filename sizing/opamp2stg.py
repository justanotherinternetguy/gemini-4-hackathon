"""Per-role parameterization + 15-point PVT evaluator for the AMPICBench two-stage op-amp
(diff-pair first stage, class-A PMOS second stage, Miller Cc + Rz, Tian loop-gain bench).

The AMPICBench netlist (CC BY-NC 4.0) is read in place from third_party/ and rewritten at
import time: each signal-path transistor's shared `Wn/Ln_/Wp/Lp` becomes a per-role knob.
The bias subcircuits (Bias_gen, Cascode_bias) stay frozen at their published sizes.

Knobs (SI units, same convention as the source netlist):
  W_in, L_in       input pair            (nfet_01v8_lvt,  XI7.XNM0/XNM1)
  W_load, L_load   mirror load           (pfet_01v8_lvt,  XI7.XPM0/XPM1)
  W_tail           tail + its cascode    (nfet_01v8_lvt, m=2, XI7.XNM3/XNM2)  L fixed = Ln_
  W_drv, L_drv     stage-2 driver        (pfet_01v8_lvt, m=3, XI6.XPM2)
  W_sink           stage-2 sink + casc.  (nfet_01v8_lvt, m=3, XI6.XNM4/XNM5) L fixed = Ln_
  Ibias, Cc, Rz
Tail and sink L stay equal to the bias reference's L so the cascode mirrors keep their ratio;
their W sets the current (I_tail = Ibias * 2*W_tail/Wn, I_sink = Ibias * 3*W_sink/Wn).

CLI:
  python -m sizing.opamp2stg                 # default (published) sizing, 15 PVT points
  python -m sizing.opamp2stg --deck out.cir  # also write the tt/27 deck for inspection
"""
from __future__ import annotations

import argparse
import json
import os
import math
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "third_party" / "AMPICBench" / "Opamps" / "sim_Opamp_2stg_Diff_ClassA_STB"
       / "sim_Opamp_2stg_Diff_ClassA_STB.cir")
SLIM_LIB = ROOT / "sim" / "models" / "sky130_slim.lib.spice"
FULL_LIB = Path.home() / ".ciel" / "sky130A" / "libs.tech" / "combined" / "sky130.lib.spice"
SPICEINIT = ROOT / "sim" / ".spiceinit"

CORNERS = ("tt", "ff", "ss", "sf", "fs")
TEMPS = (-40, 27, 125)
PVT15 = tuple((c, t) for c in CORNERS for t in TEMPS)

# --------------------------------------------------------------------------- roles

# (subckt name, instance) -> role.  This is the map the Decisions API will later produce
# from the schematic image; here it is the hand-written answer key.
ROLE_MAP: dict[tuple[str, str], str] = {
    ("Diffamp1", "XNM0"): "in", ("Diffamp1", "XNM1"): "in",
    ("Diffamp1", "XPM0"): "load", ("Diffamp1", "XPM1"): "load",
    ("Diffamp1", "XNM2"): "tail", ("Diffamp1", "XNM3"): "tail",
    ("ClassA_stg_PMOS", "XPM2"): "drv",
    ("ClassA_stg_PMOS", "XNM4"): "sink", ("ClassA_stg_PMOS", "XNM5"): "sink",
}
# role -> (has its own L knob?)
ROLE_HAS_L = {"in": True, "load": True, "tail": False, "drv": True, "sink": False}
# op-point device path prefix -> role (paths as ngspice prints them, lower case)
DEVICE_ROLE = {
    "m.xi1.xi7.xnm0": "in", "m.xi1.xi7.xnm1": "in",
    "m.xi1.xi7.xpm0": "load", "m.xi1.xi7.xpm1": "load",
    "m.xi1.xi7.xnm2": "tail", "m.xi1.xi7.xnm3": "tail",
    "m.xi1.xi6.xpm2": "drv",
    "m.xi1.xi6.xnm4": "sink", "m.xi1.xi6.xnm5": "sink",
}
# Required saturation margin |vds|-|vdsat| per role (V). The tail/sink bottom devices sit in
# a wide-swing cascode whose headroom is set by the frozen bias network (23-37 mV at tt in the
# published design), so they only have to be in saturation.
SAT_MARGIN = {"in": 0.05, "load": 0.05, "drv": 0.05, "tail": 0.0, "sink": 0.0}

# knob -> (default from the published design, lo, hi, log-scale?)
# All devices are LVT. SKY130 minimums: nfet_01v8_lvt L >= 0.15u, pfet_01v8_lvt L >= 0.35u, W >= 0.42u.
SPACE: dict[str, tuple[float, float, float, bool]] = {
    "W_in":   (5e-6,   0.5e-6, 100e-6, True),
    "L_in":   (300e-9, 0.15e-6, 4e-6,  True),
    "W_load": (20e-6,  0.5e-6, 100e-6, True),
    "L_load": (700e-9, 0.35e-6, 4e-6,  True),
    "W_tail": (5e-6,   0.5e-6, 50e-6,  True),
    "W_drv":  (20e-6,  0.5e-6, 100e-6, True),
    "L_drv":  (700e-9, 0.35e-6, 4e-6,  True),
    "W_sink": (5e-6,   0.5e-6, 50e-6,  True),
    "Ibias":  (20e-6,  2e-6,   100e-6, True),
    "Cc":     (250e-15, 50e-15, 5e-12, True),
    "Rz":     (3e3,    10.0,   50e3,   True),
}
DEFAULTS = {k: v[0] for k, v in SPACE.items()}
SUPPLY = "VV3"                                   # supply source name in the testbench
CORE_DEVICES = ("m.xi1.xi7.xnm3.", "m.xi1.xi6.xnm4.")   # bottom devices whose id sum = core current
CIRCUIT = "diff"

# --------------------------------------------------------------------------- circuit registry
# Held-out circuits: AMPICBench's other two-stage op-amps (same bias network and class-A second
# stage, different first stage). Same conventions: per-role W (and L where the device is not a
# mirror of the frozen bias network), Ibias, Cc, Rz. Defaults = published sizing.
_OPAMPS = ROOT / "third_party" / "AMPICBench" / "Opamps"
_W = lambda d: (d, 0.5e-6, 100e-6, True)
_WM = lambda d: (d, 0.5e-6, 50e-6, True)      # mirror widths (set current)
_LN = lambda d: (d, 0.15e-6, 4e-6, True)
_LP = lambda d: (d, 0.35e-6, 4e-6, True)
_COMMON_TAIL = {"Ibias": (20e-6, 2e-6, 100e-6, True), "Rz": None, "Cc": None}

CIRCUITS = {
    "diff": dict(
        src=_OPAMPS / "sim_Opamp_2stg_Diff_ClassA_STB" / "sim_Opamp_2stg_Diff_ClassA_STB.cir",
        role_map=dict(ROLE_MAP), role_has_l=dict(ROLE_HAS_L), device_role=dict(DEVICE_ROLE),
        sat_margin=dict(SAT_MARGIN), space=dict(SPACE), supply="VV3",
        core=("m.xi1.xi7.xnm3.", "m.xi1.xi6.xnm4."), cload=500e-15),
    "fc": dict(
        src=_OPAMPS / "sim_Opamp_2stg_FC_ClassA_STB" / "sim_Opamp_2stg_FC_ClassA_STB.cir",
        role_map={("FC_Amp", "XNM9"): "in", ("FC_Amp", "XNM10"): "in",
                  ("FC_Amp", "XNM0"): "tail", ("FC_Amp", "XNM1"): "tail",
                  ("FC_Amp", "XNM4"): "tail", ("FC_Amp", "XNM5"): "tail",
                  ("FC_Amp", "XPM0"): "psrc", ("FC_Amp", "XPM1"): "psrc",
                  ("FC_Amp", "XPM2"): "psrc", ("FC_Amp", "XPM3"): "psrc",
                  ("FC_Amp", "XPM4"): "pcas", ("FC_Amp", "XPM5"): "pcas",
                  ("FC_Amp", "XNM2"): "nsink", ("FC_Amp", "XNM6"): "nsink",
                  ("FC_Amp", "XNM3"): "nsink", ("FC_Amp", "XNM8"): "nsink",
                  ("ClassA_stg_PMOS", "XPM2"): "drv",
                  ("ClassA_stg_PMOS", "XNM4"): "sink", ("ClassA_stg_PMOS", "XNM5"): "sink"},
        role_has_l={"in": True, "tail": False, "psrc": True, "pcas": False, "nsink": False,
                    "drv": True, "sink": False},
        sat_margin={"in": 0.05, "psrc": 0.05, "pcas": 0.05, "drv": 0.05,
                    "tail": 0.0, "nsink": 0.0, "sink": 0.0},
        space={"W_in": _W(5e-6), "L_in": _LN(300e-9), "W_tail": _WM(5e-6),
               "W_psrc": _W(20e-6), "L_psrc": _LP(700e-9), "W_pcas": _W(20e-6),
               "W_nsink": _WM(5e-6), "W_drv": _W(20e-6), "L_drv": _LP(700e-9), "W_sink": _WM(5e-6),
               "Ibias": (20e-6, 2e-6, 100e-6, True), "Cc": (400e-15, 50e-15, 5e-12, True),
               "Rz": (3e3, 10.0, 50e3, True)},
        supply="VV4",
        core=("m.xi1.xi7.xnm4.", "m.xi1.xi7.xnm5.", "m.xi1.xi7.xnm6.", "m.xi1.xi7.xnm8.",
              "m.xi1.xi6.xnm4."), cload=1e-12),
    "tele": dict(
        src=_OPAMPS / "sim_Opamp_2stg_Telescopic_ClassA_STB" / "sim_Opamp_2stg_Telescopic_ClassA_STB.cir",
        role_map={("Telescopic_Amp", "XNM0"): "in", ("Telescopic_Amp", "XNM1"): "in",
                  ("Telescopic_Amp", "XNM2"): "ncas", ("Telescopic_Amp", "XNM3"): "ncas",
                  ("Telescopic_Amp", "XPM2"): "load", ("Telescopic_Amp", "XPM3"): "load",
                  ("Telescopic_Amp", "XPM0"): "pcas", ("Telescopic_Amp", "XPM1"): "pcas",
                  ("Telescopic_Amp", "XNM4"): "tail", ("Telescopic_Amp", "XNM5"): "tail",
                  ("Telescopic_Amp", "XNM6"): "tail", ("Telescopic_Amp", "XNM7"): "tail",
                  ("ClassA_stg_PMOS", "XPM2"): "drv",
                  ("ClassA_stg_PMOS", "XNM4"): "sink", ("ClassA_stg_PMOS", "XNM5"): "sink"},
        role_has_l={"in": True, "ncas": False, "load": True, "pcas": False, "tail": False,
                    "drv": True, "sink": False},
        sat_margin={"in": 0.05, "ncas": 0.05, "load": 0.05, "pcas": 0.05, "drv": 0.05,
                    "tail": 0.0, "sink": 0.0},
        space={"W_in": _W(5e-6), "L_in": _LN(300e-9), "W_ncas": _W(5e-6),
               "W_load": _W(20e-6), "L_load": _LP(700e-9), "W_pcas": _W(20e-6), "W_tail": _WM(5e-6),
               "W_drv": _W(20e-6), "L_drv": _LP(700e-9), "W_sink": _WM(5e-6),
               "Ibias": (20e-6, 2e-6, 100e-6, True), "Cc": (0.6e-12, 50e-15, 5e-12, True),
               "Rz": (1.2e3, 10.0, 50e3, True)},
        supply="VV4",
        core=("m.xi1.xi7.xnm6.", "m.xi1.xi7.xnm7.", "m.xi1.xi6.xnm4."), cload=1e-12),
}
_SUBCKT_INST = {"Diffamp1": "xi7", "FC_Amp": "xi7", "Telescopic_Amp": "xi7", "ClassA_stg_PMOS": "xi6"}
for _c in CIRCUITS.values():
    if "device_role" not in _c:
        _c["device_role"] = {f"m.xi1.{_SUBCKT_INST[sub]}.{inst.lower()}": role
                             for (sub, inst), role in _c["role_map"].items()}


# role -> (has its own L knob?, required saturation margin). Mirror-of-bias roles keep L fixed.
ROLE_RULES = {"in": (True, 0.05), "load": (True, 0.05), "psrc": (True, 0.05), "drv": (True, 0.05),
              "pcas": (False, 0.05), "ncas": (False, 0.05),
              "tail": (False, 0.0), "sink": (False, 0.0), "nsink": (False, 0.0)}
ROLE_ORDER = ["in", "ncas", "tail", "load", "psrc", "pcas", "nsink", "drv", "sink"]


def register_from_roles(base: str, role_map: dict, name: str = "live") -> str:
    """Build a tunable circuit from a (subckt, instance) -> role map, e.g. one predicted from a
    schematic photo. The role map IS the search space: devices sharing a role share a knob."""
    c = CIRCUITS[base]
    role_map = {k: v for k, v in role_map.items() if v in ROLE_RULES}
    roles = [r for r in ROLE_ORDER if r in set(role_map.values())]
    space = {}
    for r in roles:
        pol = [inst for (_, inst), rr in role_map.items() if rr == r][0][1].upper()   # XNM.. / XPM..
        wd, ld = (5e-6, 300e-9) if pol == "N" else (20e-6, 700e-9)
        space[f"W_{r}"] = _WM(wd) if r in ("tail", "sink", "nsink") else _W(wd)
        if ROLE_RULES[r][0]:
            space[f"L_{r}"] = _LN(ld) if pol == "N" else _LP(ld)
    for k in ("Ibias", "Cc", "Rz"):
        space[k] = c["space"][k]
    CIRCUITS[name] = dict(c, role_map=role_map, role_has_l={r: ROLE_RULES[r][0] for r in roles},
                          sat_margin={r: ROLE_RULES[r][1] for r in roles}, space=space, base=base,
                          device_role={f"m.xi1.{_SUBCKT_INST[sub]}.{inst.lower()}": role
                                       for (sub, inst), role in role_map.items()})
    return name


def use(name: str) -> None:
    """Switch the module to another registered circuit (rebinds module globals)."""
    global SRC, ROLE_MAP, ROLE_HAS_L, DEVICE_ROLE, SAT_MARGIN, SPACE, DEFAULTS, SUPPLY
    global CORE_DEVICES, CIRCUIT, TEMPLATE
    c = CIRCUITS[name]
    SRC, ROLE_MAP, ROLE_HAS_L = c["src"], c["role_map"], c["role_has_l"]
    DEVICE_ROLE, SAT_MARGIN, SPACE = c["device_role"], c["sat_margin"], c["space"]
    DEFAULTS = {k: v[0] for k, v in SPACE.items()}
    SUPPLY, CORE_DEVICES, CIRCUIT = c["supply"], c["core"], name
    TEMPLATE = _build_template()
if os.environ.get("OPAMP_CIRCUIT", "diff") != "diff":
    use(os.environ["OPAMP_CIRCUIT"])
    os.environ["OPAMP_CIRCUIT"] = name


def clamp(params: dict) -> dict:
    out = dict(DEFAULTS)
    for k, v in params.items():
        if k not in SPACE:
            raise KeyError(f"unknown knob {k!r}; valid: {list(SPACE)}")
        _, lo, hi, _ = SPACE[k]
        out[k] = min(max(float(v), lo), hi)
    return out


# --------------------------------------------------------------------------- template

def _build_template() -> str:
    src = SRC.read_text()
    out, sub = [], None
    rewired = set()
    for line in src.splitlines():
        s = line.strip()
        if s.lower().startswith(".subckt"):
            sub = s.split()[1]
        elif s.lower().startswith(".ends"):
            sub = None
        elif s.lower().startswith(".lib"):
            line = '.lib "{LIB}" {CORNER}'
        elif s.lower().startswith(".temp"):
            line = ".temp {TEMP}"
        elif sub and s[:1].upper() == "X":
            inst = s.split()[0]
            role = ROLE_MAP.get((sub, inst))
            if role:
                wname = "Wn" if "nfet" in s else "Wp"
                lname = "Ln_" if "nfet" in s else "Lp"
                line = re.sub(rf"\b{wname}\b", f"W_{role}", line)
                if ROLE_HAS_L[role]:
                    line = re.sub(rf"\b{lname}\b", f"L_{role}", line)
                rewired.add((sub, inst))
        out.append(line)
    missing = set(ROLE_MAP) - rewired
    if missing:
        raise RuntimeError(f"role map instances not found in netlist: {missing}")
    text = "\n".join(out)
    # knob .param line (after the original one so the originals stay defined for the bias nets)
    knob_line = ".param " + " ".join(f"{k}={{{k}}}" for k in SPACE)
    text = re.sub(r"(?m)^(\.param .*)$", lambda m: m.group(1) + "\n" + knob_line, text, count=1)
    # replace the control block with one that prints everything to stdout
    control = r"""
.control
set wr_singlescale
option numdgt=7
op
echo @@OP_BEGIN
show m : id vds vdsat gm gds
echo @@OP_END
print i(@@SUPPLY@@)
ac dec 50 10 10G
let lg_v1 = v(net6)
let lg_i1 = i(Vprb_inj)
alter Vprb_inj acmag=0
alter Iprb_inj acmag=1
ac dec 50 10 10G
let lg_v2 = v(net6)
let lg_i2 = i(Vprb_inj)
let v1 = ac1.lg_v1
let i1 = ac1.lg_i1
let loopgain = 1/(1/(2*(i1*lg_v2 - v1*lg_i2) + v1 + lg_i2) - 1)
let lg_db = db(loopgain)
let lg_ph = 180/pi*cph(loopgain)
meas ac dcgain_db find lg_db at=10
meas ac ugf when lg_db=0
meas ac pm_raw find lg_ph at=ugf
meas ac f180 when lg_ph=-180
meas ac gm_db find lg_db at=f180
echo @@DONE
quit
.endc
.end
"""
    text = re.sub(r"(?s)\.control.*", control.strip().replace("@@SUPPLY@@", SUPPLY) + "\n", text)
    return text


TEMPLATE = _build_template()


def render(params: dict, corner: str = "tt", temp: float = 27, lib: str = "slim") -> str:
    p = clamp(params)
    fill = {k: f"{v:.6g}" for k, v in p.items()}
    fill.update(LIB=str(SLIM_LIB if lib == "slim" else FULL_LIB), CORNER=corner, TEMP=str(temp))
    # only substitute our own {NAME} tokens; leave SPICE {expr} braces alone
    return re.sub(r"\{(\w+)\}", lambda m: fill.get(m.group(1), m.group(0)), TEMPLATE)


# --------------------------------------------------------------------------- budget

class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """Counts every ngspice deck; checked BEFORE each deck runs (not between rounds)."""

    def __init__(self, max_decks: int | None = None):
        self.max_decks, self.used = max_decks, 0
        self._lock = threading.Lock()

    def take(self, n: int = 1) -> None:
        with self._lock:
            if self.max_decks is not None and self.used + n > self.max_decks:
                raise BudgetExceeded(f"budget {self.max_decks} decks exhausted (used {self.used})")
            self.used += n


# --------------------------------------------------------------------------- simulate

_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"


@dataclass
class PointResult:
    corner: str
    temp: float
    ok: bool = False
    dc_gain_db: float | None = None
    ugf_hz: float | None = None
    pm_deg: float | None = None
    gain_margin_db: float | None = None
    i_total_a: float | None = None     # whole supply incl. frozen bias network
    i_core_a: float | None = None      # tail + stage-2 sink (what the knobs control)
    devices: dict = field(default_factory=dict)   # path -> {role,id,vds,vdsat,gm,gds,margin}
    sat_violations: list = field(default_factory=list)  # signal-path devices below margin
    bias_triode: list = field(default_factory=list)     # frozen bias devices with vds<vdsat (info)
    error: str | None = None
    seconds: float = 0.0
    # opt-in (simulate_point/evaluate curves=True): loop-gain Bode data,
    # {"freq_hz": [...], "lg_db": [...], "lg_ph": [...]} (phase in deg, PM = 180 + phase at UGF)
    curves: dict | None = None


def _parse_show(block: str) -> dict:
    devs: dict = {}
    cur: list[str] | None = None
    for line in block.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "device":
            cur = parts[1:]
        elif cur and parts[0] in ("id", "vds", "vdsat", "gm", "gds") and len(parts) == len(cur) + 1:
            for d, v in zip(cur, parts[1:]):
                devs.setdefault(d, {})[parts[0]] = float(v)
    return devs


def _role_of(path: str) -> str | None:
    for prefix, role in DEVICE_ROLE.items():
        if path.startswith(prefix + "."):
            return role
    return None


CURVES_FILE = "lg.txt"


def _read_curves(path: Path) -> dict | None:
    f, db, ph = [], [], []
    try:
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 3:
                f.append(float(parts[0])); db.append(float(parts[1])); ph.append(float(parts[2]))
    except (OSError, ValueError):
        return None
    return {"freq_hz": f, "lg_db": db, "lg_ph": ph} if f else None


def simulate_point(params: dict, corner: str, temp: float, lib: str = "slim",
                   timeout: float = 60, budget: Budget | None = None,
                   curves: bool = False, deck_hook=None) -> PointResult:
    if budget:
        budget.take()
    r = PointResult(corner, temp)
    t0 = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix=f"oa_{corner}_{temp}_") as td:
            d = Path(td)
            (d / ".spiceinit").write_text(SPICEINIT.read_text())
            deck = render(params, corner, temp, lib)
            if deck_hook:      # e.g. layout stage: finger counts, wiring parasitics
                deck = deck_hook(deck)
            if curves:   # dump the loop-gain vectors (frequency, dB, deg) after the measurements
                deck = deck.replace("echo @@DONE", f"wrdata {CURVES_FILE} lg_db lg_ph\necho @@DONE", 1)
            (d / "deck.cir").write_text(deck)
            p = subprocess.run(["ngspice", "-b", "deck.cir"], cwd=d, capture_output=True,
                               text=True, timeout=timeout)
            if curves:
                r.curves = _read_curves(d / CURVES_FILE)
        out = p.stdout
        g = lambda name: (float(m.group(1)) if (m := re.search(
            rf"^\s*{name}\s*=\s*({_NUM})", out, re.M | re.I)) else None)
        r.dc_gain_db, r.ugf_hz = g("dcgain_db"), g("ugf")
        pm_raw, gm_db = g("pm_raw"), g("gm_db")
        r.pm_deg = 180.0 + pm_raw if pm_raw is not None else None
        r.gain_margin_db = -gm_db if gm_db is not None else None
        itot = g(rf"i\({SUPPLY.lower()}\)")
        r.i_total_a = abs(itot) if itot is not None else None
        if "@@OP_BEGIN" in out:
            block = out.split("@@OP_BEGIN", 1)[1].split("@@OP_END", 1)[0]
            for path, vals in _parse_show(block).items():
                if not {"id", "vds", "vdsat"} <= vals.keys():
                    continue
                margin = abs(vals["vds"]) - abs(vals["vdsat"])
                role = _role_of(path)
                r.devices[path] = {**vals, "role": role, "margin": margin}
                if role and margin < SAT_MARGIN[role]:
                    r.sat_violations.append(path)
                if role is None and margin < 0:
                    r.bias_triode.append(path)
            core = [abs(v["id"]) for k, v in r.devices.items() if k.startswith(CORE_DEVICES)]
            if len(core) == len(CORE_DEVICES):
                r.i_core_a = sum(core)
        r.ok = (p.returncode == 0 and None not in (r.dc_gain_db, r.ugf_hz, r.pm_deg)
                and len([d for d in r.devices.values() if d["role"]]) == len(DEVICE_ROLE))
        if not r.ok:
            errs = [l for l in (p.stdout + p.stderr).splitlines() if "rror" in l][:5]
            r.error = f"rc={p.returncode} " + " | ".join(errs)
    except subprocess.TimeoutExpired:
        r.error = "timeout"
    r.seconds = time.perf_counter() - t0
    return r


# --------------------------------------------------------------------------- evaluate

# spec: metric -> (op, bound). Metrics are checked at EVERY PVT point.
DEFAULT_SPEC = {
    "pm_deg": (">=", 60.0),
    "ugf_hz": (">=", 10e6),
    "dc_gain_db": (">=", 60.0),
    "i_core_a": ("<=", 100e-6),
}
_OPS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b, "<": lambda a, b: a < b}

# Guard band: optimizers must hit these tighter DESIGN TARGETS so the result keeps margin
# against the datasheet spec (model error, layout parasitics, Monte Carlo spread).
#   additive ("add") for dB / degrees, multiplicative ("mul") for Hz / A.
GUARD = {"pm_deg": ("add", 5.0), "ugf_hz": ("mul", 1.10),
         "dc_gain_db": ("add", 3.0), "i_core_a": ("mul", 0.90)}
SAT_GUARD_V = 0.010   # extra saturation margin on every role


def guarded(spec: dict, guard: dict | None = None) -> dict:
    """Spec -> design target. `>=` bounds move up, `<=` bounds move down."""
    guard = GUARD if guard is None else guard
    out = {}
    for m, (op, b) in spec.items():
        kind, amt = guard.get(m, ("add", 0.0))
        if kind == "add":
            nb = b + amt if op in (">=", ">") else b - amt
        else:
            nb = b * amt
        out[m] = (op, nb)
    return out


def sat_required(role: str, use_guard: bool = True) -> float:
    return SAT_MARGIN[role] + (SAT_GUARD_V if use_guard else 0.0)


def _check(res: list, spec: dict, use_guard_sat: bool) -> tuple[list, dict]:
    failures, worst_all = [], {}
    for metric, (op, bound) in spec.items():
        worst = None
        for r in res:
            v = getattr(r, metric)
            if v is None:
                failures.append(f"{metric} missing at {r.corner}/{r.temp}")
                continue
            if not _OPS[op](v, bound):
                failures.append(f"{metric}={v:.4g} {op} {bound:.4g} fails at {r.corner}/{r.temp}")
            better_is_low = op in ("<=", "<")
            if worst is None or (v > worst[0] if better_is_low else v < worst[0]):
                worst = (v, r.corner, r.temp)
        worst_all[metric] = worst
    for r in res:
        if not r.ok:
            failures.append(f"sim failed at {r.corner}/{r.temp}: {r.error}")
        for path, d in r.devices.items():
            if d["role"] and d["margin"] < sat_required(d["role"], use_guard_sat):
                failures.append(f"{d['role']} {path.split('.msky')[0]} margin {d['margin']*1e3:.0f} mV"
                                f" < {sat_required(d['role'], use_guard_sat)*1e3:.0f} mV at {r.corner}/{r.temp}")
    return failures, worst_all


@dataclass
class EvalResult:
    params: dict
    points: list[PointResult]
    spec: dict                            # the DESIGN TARGET checked (spec + guard band if on)
    passed: bool = False                  # every target at every point + saturation + all sims ok
    worst: dict = field(default_factory=dict)        # metric -> (value, corner, temp)
    failures: list = field(default_factory=list)     # human-readable reasons (vs target)
    seconds: float = 0.0
    spec_nominal: dict = field(default_factory=dict)  # the datasheet spec without guard band
    passed_nominal: bool = False          # passes the bare spec (no guard band)
    guard: bool = True

    def summary(self) -> str:
        w = self.worst
        fmt = {"pm_deg": "{:.1f} deg", "ugf_hz": "{:.3g} Hz", "dc_gain_db": "{:.1f} dB",
               "i_core_a": "{:.3g} A", "gain_margin_db": "{:.1f} dB"}
        parts = [f"{k}={fmt.get(k, '{:.3g}').format(v[0])}@{v[1]}/{v[2]}"
                 for k, v in w.items() if v and v[0] is not None]
        return (("PASS" if self.passed else "FAIL") + "  worst: " + ", ".join(parts)
                + (f"\n  failures: {'; '.join(self.failures[:8])}" if self.failures else ""))

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def recheck_full(params: dict, spec: dict | None = None, guard: bool = True) -> tuple[bool, list]:
    """Independent re-verification on the FULL PDK library (slow: ~9 s per deck), robust to a
    loaded machine: long per-deck timeout, modest parallelism, one retry for failed sims."""
    er = evaluate(params, spec=spec, lib="full", guard=guard, timeout=900, jobs=8)
    if any(not r.ok for r in er.points):
        er = evaluate(params, spec=spec, lib="full", guard=guard, timeout=1800, jobs=4)
    return er.passed, er.failures[:6]


def evaluate(params: dict, spec: dict | None = None, points=PVT15, lib: str = "slim",
             budget: Budget | None = None, jobs: int = 15, curves: bool = False,
             guard: bool = True, timeout: float = 60, deck_hook=None) -> EvalResult:
    """Judge a design at every PVT point against the spec. With guard=True (default) the
    pass/fail and `failures` use the guard-banded DESIGN TARGET (see GUARD, SAT_GUARD_V);
    `passed_nominal` always reports the bare spec.
    curves=True also stores loop-gain magnitude/phase vs frequency on each PointResult.curves."""
    nominal = dict(DEFAULT_SPEC if spec is None else spec)
    target = guarded(nominal) if guard else dict(nominal)
    p = clamp(params)
    if budget:
        budget.take(len(points))   # reserve the whole sweep up front
    t0 = time.perf_counter()
    with ThreadPoolExecutor(jobs) as ex:
        res = list(ex.map(lambda ct: simulate_point(p, ct[0], ct[1], lib, timeout=timeout,
                                                     curves=curves, deck_hook=deck_hook), points))
    er = EvalResult(p, res, target, spec_nominal=nominal, guard=guard)
    er.failures, er.worst = _check(res, target, use_guard_sat=guard)
    nominal_failures, _ = _check(res, nominal, use_guard_sat=False)
    er.passed_nominal = not nominal_failures
    margins = [(d["margin"], r.corner, r.temp) for r in res for d in r.devices.values() if d["role"]]
    er.worst["min_sat_margin_v"] = min(margins) if margins else None
    gms = [(r.gain_margin_db, r.corner, r.temp) for r in res if r.gain_margin_db is not None]
    er.worst["gain_margin_db"] = min(gms) if gms else None
    er.passed = not er.failures
    er.seconds = time.perf_counter() - t0
    return er


# --------------------------------------------------------------------------- CLI

def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--params", type=json.loads, default={}, help='JSON knob overrides, e.g. \'{"Cc":1e-12}\'')
    ap.add_argument("--lib", default="slim", choices=("slim", "full"))
    ap.add_argument("--deck", type=Path, help="write the tt/27 deck here")
    ap.add_argument("--table", action="store_true", help="print the per-point table")
    a = ap.parse_args()
    if a.deck:
        a.deck.write_text(render(a.params))
    er = evaluate(a.params, lib=a.lib)
    if a.table:
        print(f"{'pt':10s} {'gain dB':>8s} {'UGF MHz':>8s} {'PM deg':>7s} {'GM dB':>6s} "
              f"{'Icore uA':>9s} {'min margin mV':>14s}  sat-violations")
        for r in er.points:
            mm = min((d["margin"] for d in r.devices.values() if d["role"]), default=float("nan"))
            fmt = lambda v, s=1.0, f="{:.1f}": f.format(v * s) if v is not None else "-"
            print(f"{r.corner + '/' + str(r.temp):10s} {fmt(r.dc_gain_db):>8s} {fmt(r.ugf_hz, 1e-6):>8s} "
                  f"{fmt(r.pm_deg):>7s} {fmt(r.gain_margin_db):>6s} {fmt(r.i_core_a, 1e6):>9s} "
                  f"{mm * 1e3:14.0f}  {[DEVICE_ROLE.get(v.split('.msky')[0]) for v in r.sat_violations]}")
    print(er.summary())
    print(f"({len(er.points)} sims in {er.seconds:.1f}s)")


if __name__ == "__main__":
    _main()
