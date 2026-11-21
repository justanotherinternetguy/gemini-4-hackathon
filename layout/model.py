"""Deterministic geometry model + judge for a LAYOUT PLAN of the sized two-stage op-amp ("diff").

Scope (say it plainly): layout PLANNING with ESTIMATED parasitics - not routed, no DRC/LVS.

A layout plan is
    {"nf":      {"in": 8, "load": 4, "tail": 1, "drv": 4, "sink": 1},     fingers per device, per role
     "pattern": {"in": "ABBA", "load": "ABBA"},                           matched-pair placement
     "dummies": true,                                                     dummy finger at each row end of matched groups
     "rows":    [["load", "drv"], ["in"], ["tail", "sink"]]}             top-to-bottom rows of groups

What is real and what is approximated
  * REAL (simulated): splitting a role into nf fingers rewrites its SPICE device lines to
    W = W_role/nf per finger, m = m0*nf, and the source/drain junction geometry functions
    cds_as/ad/ps/pd(W_role/nf, k0*nf). Total width is unchanged; junction capacitance and the
    BSIM narrow-width behaviour change. ngspice then re-judges the design at all 15 PVT points
    against the same guard-banded target as the sizing stage.
  * APPROXIMATED (constants below): finger/row geometry, a linear Vth gradient for systematic
    mismatch, and wiring capacitance = half-perimeter wirelength x a per-um constant, added as
    grounded capacitors on four key nets.

Cost used for ranking (lower is better):
    feasible plans (simulation passes the guard-banded target AND no constraint violation) first;
    among feasible:  cost = area_um2 / A_naive + offset_uV / 100
    infeasible:      cost = 10 + number_of_violations + the same expression
A_naive is the bounding-box area of the NAIVE plan (nf=1 everywhere, AABB, no dummies).
"""
from __future__ import annotations

import json
import re

from sizing import opamp2stg as oa

# --------------------------------------------------------------------------- constants (um)
# Documented APPROXIMATIONS of SKY130 layout rules - not a DRC deck.
SD_CONTACTED = 0.29      # contacted source/drain diffusion between two gates -> finger pitch = L + 0.29
END_DIFF = 0.25          # diffusion beyond the last gate at each end of a group
CELL_OVERHEAD = 1.0      # device height = finger width + 1.0 (poly endcaps, contacts, guard spacing)
ROW_SPACE = 1.0          # vertical spacing between rows (and between the two sub-rows of AB/BA)
GROUP_SPACE = 1.0        # horizontal spacing between groups in one row
WELL_GAP = 2.0           # gap between the N-well (PMOS rows) and the NMOS rows
WELL_MARGIN = 0.35       # N-well overhang drawn around PMOS rows (drawing only)
WF_MIN, WF_MAX = 0.42, 10.0          # allowed finger width
ASPECT_MIN, ASPECT_MAX = 0.4, 2.5    # allowed bounding-box width/height
VTH_GRADIENT_UV_PER_UM = 10.0        # assumed linear threshold gradient, worst of x / y direction
WIRE_CAP_F_PER_UM = 0.15e-15         # assumed wiring capacitance per um of half-perimeter wirelength
LN_FIXED_UM = 0.3                    # tail / sink L (frozen bias-reference L, netlist param Ln_)

ROLES = ["in", "load", "tail", "drv", "sink"]
POLARITY = {"in": "n", "load": "p", "tail": "n", "drv": "p", "sink": "n"}
M0 = {"in": 1, "load": 1, "tail": 2, "drv": 3, "sink": 3}          # netlist multiplier per device
# role -> [(device key, netlist instance)]
DEVICES = {
    "in": [("A", "XNM0"), ("B", "XNM1")],
    "load": [("A", "XPM0"), ("B", "XPM1")],
    "tail": [("cas", "XNM2"), ("bot", "XNM3")],
    "drv": [("d", "XPM2")],
    "sink": [("cas", "XNM5"), ("bot", "XNM4")],
}
MATCHED = ("in", "load")
PATTERNS = ("AABB", "ABAB", "ABBA", "AB/BA")
# key nets -> devices whose fingers the net must reach
NETS = {
    "s1out": [("load", "A"), ("in", "B"), ("drv", "d")],        # stage-1 output (Diffamp1 vout)
    "net9": [("load", "A"), ("load", "B"), ("in", "A")],        # mirror node
    "net10": [("in", "A"), ("in", "B"), ("tail", "cas")],       # common-source node
    "vout": [("drv", "d"), ("sink", "cas")],                    # op-amp output
}
NAIVE_PLAN = {"nf": {r: 1 for r in ROLES}, "pattern": {"in": "AABB", "load": "AABB"},
              "dummies": False, "rows": [["load", "drv"], ["in", "tail", "sink"]]}
NF_CHOICES = {"in": [1, 2, 3, 4, 5, 6, 8, 10, 12, 16], "load": [1, 2, 3, 4, 5, 6, 8, 10, 12, 16],
              "drv": [1, 2, 3, 4, 5, 6, 8, 10, 12, 16], "tail": [1, 2, 3, 4], "sink": [1, 2, 3, 4]}


def require_diff() -> None:
    if oa.CIRCUIT != "diff":
        raise SystemExit(f"layout planning supports only circuit 'diff' (got {oa.CIRCUIT!r})")


def role_wl_um(params: dict) -> dict:
    """role -> (total W per device in um, L in um)."""
    p = oa.clamp(params)
    return {"in": (p["W_in"] * 1e6, p["L_in"] * 1e6), "load": (p["W_load"] * 1e6, p["L_load"] * 1e6),
            "tail": (p["W_tail"] * 1e6, LN_FIXED_UM), "drv": (p["W_drv"] * 1e6, p["L_drv"] * 1e6),
            "sink": (p["W_sink"] * 1e6, LN_FIXED_UM)}


# --------------------------------------------------------------------------- plan handling

def normalize_plan(plan: dict) -> dict:
    """Coerce an (LLM-written) plan into a valid one: integer nf >= 1, known patterns, every group
    in exactly one row, no mixed PMOS/NMOS rows, PMOS rows on top."""
    nf_in = plan.get("nf") or {}
    nf = {}
    for r in ROLES:
        try:
            nf[r] = max(1, min(64, int(round(float(nf_in.get(r, plan.get(f"nf_{r}", 1)))))))
        except (TypeError, ValueError):
            nf[r] = 1
    pat_in = plan.get("pattern") or {}
    pattern = {}
    for r in MATCHED:
        v = str(pat_in.get(r, plan.get(f"pattern_{r}", "AABB"))).upper().replace(" ", "")
        pattern[r] = v if v in PATTERNS else "AABB"
    seen, prow, nrow = set(), [], []
    for row in plan.get("rows") or []:
        if isinstance(row, str):
            row = [row]
        pr = [g for g in row if g in ROLES and POLARITY[g] == "p" and g not in seen and not seen.add(g)]
        nr = [g for g in row if g in ROLES and POLARITY[g] == "n" and g not in seen and not seen.add(g)]
        if pr:
            prow.append(pr)
        if nr:
            nrow.append(nr)
    for g in ROLES:                       # groups the plan forgot get their own row
        if g not in seen:
            (prow if POLARITY[g] == "p" else nrow).append([g])
    return {"nf": nf, "pattern": pattern, "dummies": bool(plan.get("dummies", False)), "rows": prow + nrow}


def plan_key(plan: dict) -> str:
    return json.dumps(normalize_plan(plan), sort_keys=True)


def plan_short(plan: dict) -> str:
    p = normalize_plan(plan)
    nf = " ".join(f"{r}x{p['nf'][r]}" for r in ROLES)
    rows = " | ".join("+".join(r) for r in p["rows"])
    return f"nf[{nf}] in:{p['pattern']['in']} load:{p['pattern']['load']} dum:{'y' if p['dummies'] else 'n'} rows[{rows}]"


def _sequence(role: str, nf: int, pattern: str) -> list[list[str]]:
    """Finger order of a group as sub-rows of device keys (top sub-row first)."""
    if role not in MATCHED:
        seq = []
        for key, _ in DEVICES[role]:
            seq += [key] * (M0[role] * nf)
        return [seq]
    if pattern == "AABB":
        return [["A"] * nf + ["B"] * nf]
    if pattern == "ABAB":
        return [["A", "B"] * nf]
    if pattern == "ABBA":                 # common centroid; exact only for even nf
        return [["A", "B", "B", "A"] * (nf // 2) + (["A", "B"] if nf % 2 else [])]
    hi, lo = (nf + 1) // 2, nf // 2       # AB/BA: two cross-coupled sub-rows
    return [["A"] * hi + ["B"] * lo, ["B"] * hi + ["A"] * lo]


# --------------------------------------------------------------------------- geometry

def layout(params: dict, plan: dict) -> dict:
    """Place every finger. Returns fingers (rectangles), groups, rows, bounding box, centroids,
    wirelengths and constraint violations. Coordinates in um, origin bottom-left, y up."""
    plan = normalize_plan(plan)
    wl = role_wl_um(params)
    violations: list[str] = []
    groups = {}
    for role in ROLES:
        w_tot, L = wl[role]
        nf = plan["nf"][role]
        wf = w_tot / nf
        if wf < WF_MIN - 1e-9:
            violations.append(f"{role}: finger width {wf:.2f} um < {WF_MIN} um (nf={nf})")
        if wf > WF_MAX + 1e-9:
            violations.append(f"{role}: finger width {wf:.2f} um > {WF_MAX} um (nf={nf})")
        subrows = _sequence(role, nf, plan["pattern"].get(role, "AABB"))
        if role in MATCHED and plan["dummies"]:
            subrows = [["dummy"] + s + ["dummy"] for s in subrows]
        pitch = L + SD_CONTACTED
        cell_h = wf + CELL_OVERHEAD
        groups[role] = dict(role=role, wf=wf, L=L, pitch=pitch, subrows=subrows, cell_h=cell_h,
                            w=max(len(s) for s in subrows) * pitch + 2 * END_DIFF,
                            h=len(subrows) * cell_h + (len(subrows) - 1) * ROW_SPACE)
    rows = []
    for names in plan["rows"]:
        rows.append(dict(groups=names, pol=POLARITY[names[0]],
                         w=sum(groups[g]["w"] for g in names) + GROUP_SPACE * (len(names) - 1),
                         h=max(groups[g]["h"] for g in names)))
    width = max(r["w"] for r in rows)
    y = 0.0
    for i in range(len(rows) - 1, -1, -1):            # place bottom row first
        r = rows[i]
        r["y"] = y
        r["x"] = (width - r["w"]) / 2.0                # rows are centred on the block axis
        y += r["h"]
        if i > 0:
            y += WELL_GAP if rows[i - 1]["pol"] != r["pol"] else ROW_SPACE
    height = y
    fingers = []
    for r in rows:
        x = r["x"]
        for g in r["groups"]:
            G = groups[g]
            G["x"], G["y"] = x, r["y"] + (r["h"] - G["h"]) / 2.0
            for si, seq in enumerate(G["subrows"]):    # sub-row 0 is the top one
                sy = G["y"] + (len(G["subrows"]) - 1 - si) * (G["cell_h"] + ROW_SPACE)
                sx = G["x"] + (G["w"] - len(seq) * G["pitch"]) / 2.0
                for fi, dev in enumerate(seq):
                    fingers.append(dict(role=g, dev=dev, dummy=dev == "dummy",
                                        x=sx + fi * G["pitch"] + SD_CONTACTED / 2.0,
                                        y=sy + CELL_OVERHEAD / 2.0, w=G["L"], h=G["wf"]))
            x += G["w"] + GROUP_SPACE
    cent, pts = {}, {}
    for f in fingers:
        if not f["dummy"]:
            pts.setdefault((f["role"], f["dev"]), []).append((f["x"] + f["w"] / 2, f["y"] + f["h"] / 2))
    for k, v in pts.items():
        cent[k] = (sum(p[0] for p in v) / len(v), sum(p[1] for p in v) / len(v))
    wires = {}
    for net, devs in NETS.items():                     # half-perimeter of all finger centres on the net
        allp = [p for d in devs for p in pts[d]]
        xs, ys = [p[0] for p in allp], [p[1] for p in allp]
        wires[net] = (max(xs) - min(xs)) + (max(ys) - min(ys))
    aspect = width / height
    if not (ASPECT_MIN <= aspect <= ASPECT_MAX):
        violations.append(f"aspect ratio (width/height) {aspect:.2f} outside [{ASPECT_MIN}, {ASPECT_MAX}]")
    prow = [r for r in rows if r["pol"] == "p"]
    nwell = None
    if prow:
        x0 = min(r["x"] for r in prow) - WELL_MARGIN
        x1 = max(r["x"] + r["w"] for r in prow) + WELL_MARGIN
        y0 = min(r["y"] for r in prow) - WELL_MARGIN
        y1 = max(r["y"] + r["h"] for r in prow) + WELL_MARGIN
        nwell = (x0, y0, x1 - x0, y1 - y0)
    sep = {r: (abs(cent[(r, "A")][0] - cent[(r, "B")][0]), abs(cent[(r, "A")][1] - cent[(r, "B")][1]))
           for r in MATCHED}
    return dict(plan=plan, fingers=fingers, groups=groups, rows=rows, width=width, height=height,
                area=width * height, aspect=aspect, centroids=cent, pair_sep=sep, wires=wires,
                cw={n: l * WIRE_CAP_F_PER_UM for n, l in wires.items()}, nwell=nwell,
                violations=violations, geom_hard=any("finger width" in v and " < " in v for v in violations),
                finger_w={r: groups[r]["wf"] for r in ROLES})


def offset_uv(geom: dict, gm_ratio: float) -> tuple[float, dict]:
    """Input-referred systematic offset (uV) for a linear Vth gradient in the worse of x / y:
    |dVth_in| + (gm_load/gm_in) * |dVth_load|, dVth = gradient * centroid distance of A and B."""
    g = VTH_GRADIENT_UV_PER_UM
    per = {}
    for i, axis in enumerate("xy"):
        per[axis] = g * geom["pair_sep"]["in"][i] + gm_ratio * g * geom["pair_sep"]["load"][i]
    worst = max(per, key=per.get)
    return per[worst], {"x_uV": round(per["x"], 2), "y_uV": round(per["y"], 2), "worst_axis": worst,
                        "gm_load_over_gm_in": round(gm_ratio, 3),
                        "in_sep_um": [round(v, 3) for v in geom["pair_sep"]["in"]],
                        "load_sep_um": [round(v, 3) for v in geom["pair_sep"]["load"]]}


# --------------------------------------------------------------------------- SPICE deck hook

def make_deck_hook(plan: dict, cw: dict):
    """deck text -> deck text: apply finger counts to the role devices and add wiring capacitors."""
    plan = normalize_plan(plan)
    nf = plan["nf"]

    def hook(deck: str) -> str:
        out, sub, n_dev, n_cap = [], None, 0, 0
        for line in deck.splitlines():
            s = line.strip()
            low = s.lower()
            if low.startswith(".subckt"):
                sub = s.split()[1]
            elif low.startswith(".ends"):
                if sub == "Diffamp1":
                    out.append(f"Cw_s1out vout 0 {cw['s1out']:.5g}")
                    out.append(f"Cw_net9 net9 0 {cw['net9']:.5g}")
                    out.append(f"Cw_net10 net10 0 {cw['net10']:.5g}")
                    n_cap += 3
                sub = None
            elif sub and s[:1].upper() == "X":
                role = oa.ROLE_MAP.get((sub, s.split()[0]))
                if role:
                    n_dev += 1
                    n = nf[role]
                    if n > 1:
                        new = line.replace(f"W={{W_{role}*1e6}}", f"W={{W_{role}/{n}*1e6}}")
                        new = re.sub(r"\bm=(\{_gpar1\}|\d+)", lambda m: "m=%d" % (
                            (3 if m.group(1).startswith("{") else int(m.group(1))) * n), new)
                        new, k = re.subn(rf"(cds_\w+)\(W_{role},(\d+)\)", lambda m: "%s(W_%s/%d,%d)" % (
                            m.group(1), role, n, int(m.group(2)) * n), new)
                        if k != 4 or f"W_{role}/{n}*1e6" not in new:
                            raise RuntimeError(f"finger rewrite failed for {sub}.{s.split()[0]}: {line}")
                        line = new
            elif sub is None and s.startswith("CC0 "):
                out.append(line)
                out.append(f"Cw_vout vout 0 {cw['vout']:.5g}")
                n_cap += 1
                continue
            out.append(line)
        if n_dev != len(oa.ROLE_MAP) or n_cap != 4:
            raise RuntimeError(f"deck hook touched {n_dev} devices / {n_cap} wire caps (expected 9 / 4)")
        return "\n".join(out) + "\n"

    return hook


# --------------------------------------------------------------------------- judge

def naive_area(params: dict) -> float:
    return layout(params, NAIVE_PLAN)["area"]


def gm_ratio_of(er) -> float | None:
    """gm_load / gm_in at tt/27 from the simulator's operating point."""
    for r in er.points:
        if r.corner == "tt" and int(r.temp) == 27:
            gm = {}
            for path, d in r.devices.items():
                if path.startswith("m.xi1.xi7.xnm0.") and "gm" in d:
                    gm["in"] = abs(d["gm"])
                if path.startswith("m.xi1.xi7.xpm0.") and "gm" in d:
                    gm["load"] = abs(d["gm"])
            if len(gm) == 2 and gm["in"] > 0:
                return gm["load"] / gm["in"]
    return None


def judge(params: dict, plan: dict, jobs: int = 8, budget=None, gm_ratio_fallback: float = 1.0,
          simulate: bool = True) -> dict:
    """Judge one layout plan: geometry + constraints + systematic offset + 15-PVT re-simulation
    with finger counts and wiring capacitance (guard-banded target, same as the sizing stage)."""
    require_diff()
    geom = layout(params, plan)
    plan = geom["plan"]
    a_naive = naive_area(params)
    res = dict(plan=plan, area_um2=round(geom["area"], 2), width_um=round(geom["width"], 2),
               height_um=round(geom["height"], 2), aspect=round(geom["aspect"], 3),
               naive_area_um2=round(a_naive, 2),
               finger_w_um={r: round(v, 3) for r, v in geom["finger_w"].items()},
               wirelength_um=round(sum(geom["wires"].values()), 2),
               wires_um={n: round(v, 2) for n, v in geom["wires"].items()},
               cw_fF={n: round(v * 1e15, 3) for n, v in geom["cw"].items()},
               violations=list(geom["violations"]), simulated=False, passed=False, passed_nominal=False,
               pm_deg=None, ugf_hz=None, dc_gain_db=None, i_core_a=None, sim_failures=[], sim_s=0.0)
    ratio = None
    if simulate and not geom["geom_hard"]:       # a finger below the device minimum cannot be simulated
        er = oa.evaluate(params, deck_hook=make_deck_hook(plan, geom["cw"]), jobs=jobs, budget=budget)
        w = er.worst
        val = lambda k: (w[k][0] if w.get(k) else None)
        res.update(simulated=True, passed=bool(er.passed), passed_nominal=bool(er.passed_nominal),
                   pm_deg=val("pm_deg"), ugf_hz=val("ugf_hz"), dc_gain_db=val("dc_gain_db"),
                   i_core_a=val("i_core_a"), sim_failures=er.failures[:4], sim_s=round(er.seconds, 1))
        ratio = gm_ratio_of(er)
    elif geom["geom_hard"]:
        res["sim_failures"] = [f"not simulated: finger narrower than the {WF_MIN} um device minimum"]
    off, detail = offset_uv(geom, ratio if ratio is not None else gm_ratio_fallback)
    detail["gm_from_sim"] = ratio is not None
    res.update(offset_uV=round(off, 2), offset_detail=detail, gm_ratio=ratio)
    base = geom["area"] / a_naive + off / 100.0
    res["feasible"] = bool(res["passed"] and not res["violations"])
    n_bad = len(res["violations"]) + (0 if res["passed"] else 1)
    res["cost"] = round(base if res["feasible"] else 10.0 + n_bad + base, 4)
    return res


RULES_TEXT = f"""GEOMETRY MODEL (documented approximations of SKY130 rules; all in um):
  finger pitch = L + {SD_CONTACTED} (contacted source/drain); end diffusion {END_DIFF} at each end of a group
  device (row) height = finger width + {CELL_OVERHEAD}; row spacing {ROW_SPACE}; group spacing in a row {GROUP_SPACE}
  gap between the N-well (PMOS rows, on top) and the NMOS rows = {WELL_GAP}; rows are centred
  dummies=true adds one dummy finger at each end of every sub-row of the matched groups (in, load)
  finger width = W_role / nf.  Number of fingers in a group: in/load 2*nf (two matched devices A and B),
  tail 4*nf (cascode + bottom, m=2 each), drv 3*nf (m=3), sink 6*nf (cascode + bottom, m=3 each)
  patterns for matched pairs: AABB side by side | ABAB interdigitated | ABBA common centroid
  (exact for even nf) | AB/BA two cross-coupled sub-rows (group is two cells tall, half as wide)
CONSTRAINTS: finger width within [{WF_MIN}, {WF_MAX}] um for EVERY role; bounding-box aspect ratio
  width/height within [{ASPECT_MIN}, {ASPECT_MAX}]; PMOS groups (load, drv) go in the top rows, NMOS (in, tail, sink) below.
JUDGE: (1) systematic offset for a linear Vth gradient of {VTH_GRADIENT_UV_PER_UM:g} uV/um in the worse of x / y:
  |dVth_in| + (gm_load/gm_in)*|dVth_load|, dVth = gradient * distance between the A and B centroids;
  (2) wiring capacitance = half-perimeter wirelength of 4 key nets * {WIRE_CAP_F_PER_UM*1e15:g} fF/um, added to the netlist;
  (3) ngspice re-simulation at 15 PVT points with the finger counts applied (junction capacitance and
  narrow-width effects change; tail and sink are current mirrors of a W=5um single-finger reference,
  so fingering them shifts the bias current) - must still meet the guard-banded target.
COST (lower is better): feasible first; cost = area_um2 / A_naive + offset_uV / 100."""


# --------------------------------------------------------------------------- CLI (judge only, no LLM)

def _main() -> None:
    """python -m layout.model --run runs/swarm/guard_s0 [--plan '<json>'] [--out DIR]
    Judges the naive plan and one plan (default: a hand-written sensible one) and draws both."""
    import argparse
    from pathlib import Path
    from layout import render as lr
    ap = argparse.ArgumentParser(description=_main.__doc__)
    ap.add_argument("--run", required=True, help="sizing run directory (summary.json with best_params)")
    ap.add_argument("--plan", type=json.loads, default={
        "nf": {"in": 8, "load": 4, "tail": 1, "drv": 4, "sink": 1}, "pattern": {"in": "ABBA", "load": "ABBA"},
        "dummies": True, "rows": [["load", "drv"], ["in"], ["tail", "sink"]]})
    ap.add_argument("--out", default=str(oa.ROOT / "runs" / "layout" / "judge_check"))
    a = ap.parse_args()
    require_diff()
    d = json.loads((Path(a.run) / "summary.json").read_text())
    if d.get("circuit", "diff") != "diff":
        raise SystemExit(f"layout planning supports only circuit 'diff' (run is {d.get('circuit')!r})")
    params = d["best_params"]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    base = oa.evaluate(params, jobs=8)
    print("sizing only (no layout effects):", base.summary().splitlines()[0])
    naive, plan = judge(params, NAIVE_PLAN), judge(params, a.plan)
    for name, r in (("naive", naive), ("plan", plan)):
        print(f"{name:6s} {plan_short(r['plan'])}\n       area={r['area_um2']:.0f} um2 ({r['width_um']:.1f} x {r['height_um']:.1f}, "
              f"aspect {r['aspect']:.2f}) offset={r['offset_uV']:.1f} uV wire={r['wirelength_um']:.0f} um "
              f"sim={'PASS' if r['passed'] else 'FAIL'} PM={r['pm_deg']} UGF={r['ugf_hz']} gain={r['dc_gain_db']} "
              f"cost={r['cost']} violations={r['violations']}")
    lr.save_floorplan(params, NAIVE_PLAN, naive, out / "naive.png", "naive plan", svg=True)
    lr.save_floorplan(params, a.plan, plan, out / "plan.png", "plan", svg=True)
    lr.save_before_after(params, NAIVE_PLAN, naive, a.plan, plan, out / "before_after.png")
    (out / "judge.json").write_text(json.dumps({"naive": naive, "plan": plan}, indent=1, default=str))
    print(f"images + judge.json in {out}\n(layout planning with estimated parasitics - not routed, no DRC/LVS)")


if __name__ == "__main__":
    _main()
