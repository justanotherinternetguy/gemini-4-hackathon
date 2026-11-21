"""Minimal, independent runner for AMS-SizingBench circuits (from the AutoSizer repo).

Reads the benchmark YAML files in place (third_party/AutoSizer/AMS-SizingBench, not
vendored: the repo has no license), fills the SPICE templates with a design point,
runs ngspice in batch mode and checks the spec string.

Only the "generic template" circuits are supported (the ones with no `circuit_type`;
AutoSizer routes the others through custom Python simulators).

Differences from AutoSizer, on purpose:
  * the spec parser handles negative numbers and FAILS a constraint whose metric is
    missing (AutoSizer's parser silently drops e.g. `gain > -5`);
  * the process corner and temperature are parameters, not hard-coded tt / 27 C;
  * every simulation runs in its own temp directory, so runs can go in parallel.

CLI:
  python -m bench.amsbench                 # all generic circuits, mid-grid point, tt
  python -m bench.amsbench smc azc --corner ss --temp 125
"""
from __future__ import annotations

import argparse
import math
import operator
import re
import string
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = ROOT / "third_party" / "AutoSizer" / "AMS-SizingBench"
SLIM_LIB = ROOT / "sim" / "models" / "sky130_slim.lib.spice"
FULL_LIB = Path.home() / ".ciel" / "sky130A" / "libs.tech" / "combined" / "sky130.lib.spice"
SPICEINIT = ROOT / "sim" / ".spiceinit"

CORNERS = ("tt", "ff", "ss", "sf", "fs")
TEMPS = (-40, 27, 125)

_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
_OPS = {">": operator.gt, "<": operator.lt, ">=": operator.ge, "<=": operator.le}


# --------------------------------------------------------------------------- config

def list_circuits(generic_only: bool = True) -> list[str]:
    names = []
    for f in sorted(BENCH_DIR.glob("*.yaml")):
        cfg = yaml.safe_load(f.read_text())
        if generic_only and cfg.get("circuit_type"):
            continue
        names.append(f.stem)
    return names


def load(name: str) -> dict:
    return yaml.safe_load((BENCH_DIR / f"{name}.yaml").read_text())


def grid(cfg: dict, var: str) -> list[float]:
    """Allowed values for a design variable: `<var>_values` if present, else `W_values`."""
    return list(cfg.get(f"{var}_values") or cfg["W_values"])


def variables(cfg: dict) -> list[str]:
    return list((cfg.get("variable") or {}).keys())


def mid_point(cfg: dict) -> dict[str, float]:
    return {v: grid(cfg, v)[len(grid(cfg, v)) // 2] for v in variables(cfg)}


# --------------------------------------------------------------------------- specs

@dataclass
class Constraint:
    metric: str
    op: str
    bound: float

    def check(self, metrics: dict) -> bool:
        val = metrics.get(self.metric)
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return False  # missing metric = fail, never "skip"
        return _OPS[self.op](val, self.bound)

    def __str__(self) -> str:
        return f"{self.metric} {self.op} {self.bound:g}"


def parse_specs(spec: str) -> list[Constraint]:
    out = []
    for term in re.split(r"\s+AND\s+", spec.strip(), flags=re.IGNORECASE):
        m = re.fullmatch(rf"\s*(\w+)\s*(>=|<=|>|<)\s*({_NUM})\s*", term)
        if not m:
            raise ValueError(f"cannot parse spec term {term!r} in {spec!r}")
        out.append(Constraint(m.group(1), m.group(2), float(m.group(3))))
    return out


# --------------------------------------------------------------------------- netlist

def _placeholders(tmpl: str) -> set[str]:
    return {f.split("!")[0].split(":")[0].split(".")[0]
            for _, f, _, _ in string.Formatter().parse(tmpl) if f}


def render(cfg: dict, point: dict[str, float], corner: str = "tt", temp: float = 27,
           lib: str = "slim") -> str:
    lib_path = SLIM_LIB if lib == "slim" else FULL_LIB
    fmt: dict = {}
    fmt.update(cfg.get("params") or {})
    fmt.update(point)
    fmt["pdk_lib_path"] = str(lib_path)
    fmt["subckt_name"] = cfg.get("subckt_name")
    fmt.update(cfg.get("testbench_signals") or {})
    for scales in ("width_scales", "length_scales", "nfin_scales"):
        for final, (base, factor) in (cfg.get(scales) or {}).items():
            if fmt.get(final) is None:
                fmt[final] = round(fmt[base] * factor, 2)
    pins = cfg.get("subckt_pins") or []
    sig = cfg.get("testbench_signals") or {}
    fmt["inst_pins"] = " ".join(str(sig.get(p, p)) for p in pins)

    sub_t, tb_t = cfg["ota_subckt_template"], cfg["testbench_template"]
    missing = sorted(k for k in (_placeholders(sub_t) | _placeholders(tb_t)) - {"ota_subckt"}
                     if fmt.get(k) is None)
    if missing:
        raise KeyError(f"missing placeholder values: {missing}")
    net = tb_t.format(ota_subckt=sub_t.format(**fmt), **fmt)
    # corner: the templates hard-code `.lib <path> tt`
    net, n = re.subn(r"(?im)^(\s*\.lib\s+\S+\s+)tt\b", rf"\g<1>{corner}", net)
    if n != 1:
        raise ValueError(f"expected exactly one '.lib ... tt' line, found {n}")
    # temperature: insert before .control (or before .end)
    temp_line = f".temp {temp}\n.option tnom=27\n"
    if re.search(r"(?im)^\s*\.control", net):
        net = re.sub(r"(?im)^(\s*\.control)", temp_line + r"\1", net, count=1)
    else:
        net = re.sub(r"(?im)^(\s*\.end\s*)$", temp_line + r"\1", net, count=1)
    return net


# --------------------------------------------------------------------------- simulate

@dataclass
class SimResult:
    circuit: str
    corner: str
    temp: float
    point: dict
    metrics: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    ok: bool = False           # ngspice ran and every metric was found
    error: str | None = None
    seconds: float = 0.0


def _grab(name: str, text: str) -> float | None:
    m = re.search(rf"measure:\s*{re.escape(name)}\s*=\s*({_NUM})", text, re.IGNORECASE)
    if not m:
        m = re.search(rf"^\s*{re.escape(name)}\s*=\s*({_NUM})", text, re.IGNORECASE | re.MULTILINE)
    return float(m.group(1)) if m else None


def simulate(name_or_cfg, point: dict[str, float] | None = None, corner: str = "tt",
             temp: float = 27, lib: str = "slim", timeout: float = 60,
             keep_dir: Path | None = None) -> SimResult:
    cfg = load(name_or_cfg) if isinstance(name_or_cfg, str) else name_or_cfg
    name = name_or_cfg if isinstance(name_or_cfg, str) else cfg.get("subckt_name", "?")
    point = dict(point or mid_point(cfg))
    res = SimResult(name, corner, temp, point)
    t0 = time.perf_counter()
    try:
        net = render(cfg, point, corner, temp, lib)
        with tempfile.TemporaryDirectory(prefix=f"ams_{name}_") as td:
            d = Path(keep_dir) if keep_dir else Path(td)
            d.mkdir(parents=True, exist_ok=True)
            (d / ".spiceinit").write_text(SPICEINIT.read_text())
            (d / "deck.sp").write_text(net)
            p = subprocess.run(["ngspice", "-b", "deck.sp"], cwd=d, capture_output=True,
                               text=True, timeout=timeout)
            out = p.stdout + "\n" + p.stderr
            if keep_dir:
                (d / "ngspice.log").write_text(out)
        metric_names = list(cfg.get("metrics") or [])
        post = cfg.get("metric_post") or {}
        raw = {m: _grab(m, out) for m in metric_names}
        res.raw = raw
        safe = {"__builtins__": {}, "math": math, "pow": pow}
        vals: dict = {}
        for m in metric_names:
            spec = post.get(m, {})
            try:
                if spec.get("expr"):
                    vals[m] = float(eval(spec["expr"], safe, dict(vals)))
                elif raw[m] is not None:
                    vals[m] = raw[m] * float(spec.get("scale", 1))
                else:
                    vals[m] = None
            except Exception:
                vals[m] = None
        res.metrics = vals
        res.ok = p.returncode == 0 and all(v is not None for v in vals.values())
        if not res.ok:
            res.error = (f"rc={p.returncode}; missing={[k for k, v in vals.items() if v is None]}; "
                         + " | ".join(l for l in out.splitlines() if "rror" in l)[:400])
    except Exception as e:  # render errors, timeouts
        res.error = f"{type(e).__name__}: {e}"
    res.seconds = time.perf_counter() - t0
    return res


def check(cfg: dict, metrics: dict) -> tuple[bool, list[tuple[str, bool]]]:
    cons = parse_specs(cfg["user_specs_metric"])
    detail = [(str(c), c.check(metrics)) for c in cons]
    return all(ok for _, ok in detail), detail


# --------------------------------------------------------------------------- CLI

def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("circuits", nargs="*", help="circuit names (default: all generic ones)")
    ap.add_argument("--corner", default="tt", choices=CORNERS)
    ap.add_argument("--temp", type=float, default=27)
    ap.add_argument("--lib", default="slim", choices=("slim", "full"))
    ap.add_argument("--keep", type=Path, help="keep the deck and log in this directory")
    a = ap.parse_args()
    for name in a.circuits or list_circuits():
        cfg = load(name)
        r = simulate(name, None, a.corner, a.temp, a.lib,
                     keep_dir=(a.keep / name) if a.keep else None)
        passed, detail = check(cfg, r.metrics)
        mets = "  ".join(f"{k}={v:.4g}" if v is not None else f"{k}=None" for k, v in r.metrics.items())
        print(f"{name:22s} {r.seconds:5.2f}s ok={r.ok!s:5} spec={'PASS' if passed else 'fail'}  {mets}")
        if r.error:
            print(f"   error: {r.error}")
        fails = [s for s, ok in detail if not ok]
        if fails and r.ok:
            print(f"   unmet: {', '.join(fails)}")


if __name__ == "__main__":
    _main()
