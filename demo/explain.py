"""Interpretability layer for the Tapeout Crew stage: part explorer, decision trace, ask-the-crew,
change requests and the PCB board panel.  Everything except ask()/change() is computed from recorded
run files only (no LLM).  ask()/change() make ONE gemini-4-argon call each, grounded on those files."""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
SWARM = RUNS / "swarm"
PCB = Path(__import__("os").environ.get("TAPEOUT_PCB_DIR") or RUNS / "pcb")
EXPLAIN_MODEL = "gemini-4-argon"
SPEC_LIMITS = {"pm_deg_min": (45, 80), "ugf_mhz_min": (1, 60), "dc_gain_db_min": (40, 85), "i_core_ua_max": (10, 400)}
DEFAULT_SPEC = {"pm_deg": [">=", 60.0], "ugf_hz": [">=", 1e7], "dc_gain_db": [">=", 60.0], "i_core_a": ["<=", 1e-4]}
GUARD = {"pm_deg": ("add", 5.0), "ugf_hz": ("mul", 1.1), "dc_gain_db": ("add", 3.0), "i_core_a": ("mul", 0.9)}
CHECK = {"pm_deg": ("stability", "phase margin", 1.0, "°"), "ugf_hz": ("speed", "unity-gain frequency", 1e-6, " MHz"),
         "dc_gain_db": ("accuracy", "DC gain", 1.0, " dB"), "i_core_a": ("power", "core current", 1e6, " µA"),
         "gain_margin_db": ("stability", "gain margin", 1.0, " dB")}
SCHEMATIC = {"diff": "Opamp_2stg_Diff_ClassA.png", "fc": "Opamp_2stg_FC_ClassA.png", "tele": "Opamp_2stg_Telescopic_ClassA.png"}
CIRCUIT_NAME = {"diff": "two-stage Miller op-amp", "fc": "folded-cascode two-stage op-amp",
                "tele": "telescopic-cascode two-stage op-amp"}

# part -> (name, one plain sentence, what its knobs do, words that identify it in agent text)
PARTS = {
    "in": ("Input pair", "Senses the tiny difference between the two inputs and turns it into a current.",
           "Wider or longer devices give more gain and need less voltage, but add capacitance that slows the amplifier.",
           [r"input pair", r"\bW_in", r"\bL_in", r"Vgs_in", r"gm_in", r"diff(erential)? pair"]),
    "tail": ("Tail source", "Sets how much current the first stage burns.",
             "Its width (times the bias current) sets stage-1 current: more current is faster but costs power.",
             [r"\btail", r"W_tail"]),
    "load": ("Mirror load", "Turns the input pair's current difference into the first stage's output voltage.",
             "Longer channels give more gain; the width sets how much voltage headroom it needs.",
             [r"\bload\b(?! C)", r"W_load", r"L_load", r"mirror load"]),
    "drv": ("Stage-2 driver", "The second gain stage: it amplifies again and drives the output.",
            "Width and length trade gain against speed, and its gate voltage sets the first stage's headroom.",
            [r"\bdrv", r"driver", r"W_drv", r"L_drv", r"[Ss]tage[- ]2", r"second stage"]),
    "sink": ("Stage-2 sink", "The current source the stage-2 driver pulls against.",
             "Its width (times the bias current) sets stage-2 current, usually most of the power.",
             [r"(?<!n)sink", r"W_sink"]),
    "psrc": ("P current source", "Feeds current into the fold nodes from the supply and mirrors the signal to one output.",
             "Its size sets its gate voltage and headroom; longer channels give more gain.",
             [r"psrc", r"PMOS current source", r"fold"]),
    "pcas": ("P cascode", "Shields the gain node so the first stage's gain is much higher.",
             "Its width sets how much voltage headroom the cascode needs.", [r"pcas", r"PMOS cascode"]),
    "ncas": ("N cascode", "Stacks on the input pair to boost the first stage's gain.",
             "Its width sets how much voltage headroom the cascode needs.", [r"ncas", r"NMOS cascode", r"Vbias5"]),
    "nsink": ("N current sink", "Pulls the folded branch current to ground.",
              "Its width (times the bias current) sets the folded branch current, which counts as power.",
              [r"nsink", r"W_nsink"]),
    "comp": ("Compensation (Cc, Rz)", "A capacitor and resistor between the two stages that keep the amplifier from ringing or oscillating.",
             "A bigger capacitor is more stable but slower; the resistor cancels an unwanted zero.",
             [r"\bCc\b", r"\bCc_", r"\bRz\b", r"\bRz_", r"Miller", r"compensation", r"RHP zero"]),
    "bias": ("Bias current", "The master reference current that the tail and sink sources copy.",
             "Raising it raises every mirrored current at once; the frozen bias network was designed around 20 µA.",
             [r"Ibias", r"bias network", r"bias current"]),
}
GLOBAL_KNOBS = {"Cc": "comp", "Rz": "comp", "Ibias": "bias"}


# --------------------------------------------------------------------------- file helpers
def read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def read_jsonl(p: Path) -> list[dict]:
    out = []
    try:
        for line in p.read_text().splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return out


def unit(k: str) -> tuple[str, float]:
    if k.startswith(("W_", "L_")):
        return "um", 1e6
    return {"Ibias": ("uA", 1e6), "Cc": ("pF", 1e12), "Rz": ("kohm", 1e-3)}.get(k, ("", 1.0))


def disp(k: str, v) -> float | None:
    return None if v is None else round(float(v) * unit(k)[1], 4)


def pretty_unit(k: str) -> str:
    return {"um": "µm", "uA": "µA", "pF": "pF", "kohm": "kΩ"}.get(unit(k)[0], "")


def knob_part(k: str) -> str:
    return GLOBAL_KNOBS.get(k) or k.split("_", 1)[-1]


def circuits() -> dict:
    """Circuit registry from sizing.opamp2stg (read only)."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from sizing import opamp2stg as oa
    return oa.CIRCUITS


def guarded(spec: dict) -> dict:
    out = {}
    for m, (op, b) in spec.items():
        kind, amt = GUARD.get(m, ("add", 0.0))
        out[m] = [op, (b + amt if op.startswith(">") else b - amt) if kind == "add" else b * amt]
    return out


# --------------------------------------------------------------------------- run loading
def n_pass(c: dict) -> int:
    return sum(1 for p in c.get("points", []) if p.get("pass"))


def better(a: dict, b: dict | None) -> bool:
    if b is None:
        return True
    if bool(a.get("passed")) != bool(b.get("passed")):
        return bool(a.get("passed"))
    if a.get("passed"):
        return (a.get("worst", {}).get("i_core_a") or 1) < (b.get("worst", {}).get("i_core_a") or 1)
    pa, pb = n_pass(a), n_pass(b)
    return pa > pb if pa != pb else (a.get("n_failures", 99) < b.get("n_failures", 99))


def load_run(run: str, upto: int | None = None) -> dict:
    d = SWARM / run
    hist = read_jsonl(d / "history.jsonl")
    if upto:
        hist = [h for h in hist if h.get("n", 0) <= upto] or hist[:1]
    arch = read_jsonl(d / "architect.jsonl")
    events = read_jsonl(d / "events.jsonl")
    summ = read_json(d / "summary.json") or {}
    start = next((e for e in events if e.get("type") == "start"), {})
    knobs = list(hist[0].get("params", {})) if hist else list(start.get("knobs") or [])
    circuit = summ.get("circuit") or start.get("circuit")
    if circuit not in ("diff", "fc", "tele"):
        circuit = "fc" if "W_psrc" in knobs else "tele" if "W_ncas" in knobs else "diff"
    try:
        circ = circuits().get(circuit) or {}
    except Exception:  # noqa: BLE001  (someone is editing sizing/: degrade to recorded data only)
        circ = {}
    target = {k: v for k, v in (hist[0].get("target") or {}).items()} if hist else {}
    spec = {k: v for k, v in (hist[0].get("spec_nominal") or {}).items()} if hist else {}
    if not spec:
        spec = {k: v[1] for k, v in (start.get("spec") or DEFAULT_SPEC).items()}
    if not target:
        target = {k: v[1] for k, v in (start.get("target") or guarded({k: [DEFAULT_SPEC[k][0], v] for k, v in spec.items() if k in DEFAULT_SPEC})).items()}
    is_guarded = any(abs(target.get(k, 0) - spec.get(k, 0)) > 1e-12 for k in spec)
    sat_guard = float((summ.get("guard_band") or {}).get("sat_v", 0.01 if is_guarded else 0.0)) if is_guarded else 0.0
    # best-so-far lineage
    best, lineage = None, []
    for h in hist:
        if better(h, best):
            best = h
            lineage.append(h["n"])
    space = circ.get("space") or {}
    published = {k: (space[k][0] if k in space and space[k] else None) for k in knobs}
    if hist and hist[0].get("worker") == "start":
        published = {k: hist[0]["params"].get(k, published.get(k)) for k in knobs}
    roles = {}
    for (sub, inst), r in (circ.get("role_map") or {}).items():
        roles.setdefault(r, []).append({"label": inst.lstrip("X"), "subckt": sub})
    return {"id": run, "dir": d, "hist": hist, "arch": arch, "events": events, "summary": summ, "circuit": circuit,
            "circ": circ, "knobs": knobs, "target": target, "spec": spec, "guarded": is_guarded, "sat_guard": sat_guard,
            "best": best, "lineage": lineage, "published": published, "roles": roles, "space": space}


def parent_of(R: dict, c: dict) -> dict | None:
    """The design the worker says it started from, else the best design known when its round began."""
    m = re.search(r"(?:[Ss]tart(?:ing|ed|s)?|[Bb]uild(?:ing|s)?|[Bb]ased|[Rr]efin\w+|[Tt]ak\w+) (?:from|on|off) (?:design |candidate |best )?#(\d+)", c.get("rationale", ""))
    if m:
        hit = next((h for h in R["hist"] if h["n"] == int(m.group(1)) and h["n"] < c["n"]), None)
        if hit:
            return dict(hit, _stated=True)
    best = None
    for h in R["hist"]:
        if h.get("iter", 0) < c.get("iter", 0) and h["n"] < c["n"] and better(h, best):
            best = h
    return best


def knob_diff(R: dict, a: dict, b: dict) -> list[dict]:
    """Knobs that differ going from design a to design b."""
    out = []
    for k in R["knobs"]:
        va, vb = a.get(k), b.get(k)
        if va is None or vb is None or abs(va - vb) <= 1e-9 * max(abs(va), abs(vb), 1e-30):
            continue
        out.append({"knob": k, "part": knob_part(k), "from": disp(k, va), "to": disp(k, vb), "unit": pretty_unit(k),
                    "ratio": round(vb / va, 3) if va else None})
    return out


def plan_for(R: dict, it: int) -> dict | None:
    """The architect plan in force when round `it` started (None = generic directions)."""
    ev, arch = R["events"], R["arch"]
    if any(e.get("type") == "plan_applied" for e in ev) and any(e.get("type") == "round_start" for e in ev):
        cur, applied = None, 0
        for e in ev:
            if e.get("type") == "plan_applied":
                focus0 = (e.get("directions") or [{}])[0].get("focus")
                cur = next((a for a in arch if (a.get("directions") or [{}])[0].get("focus") == focus0), None) or \
                    (arch[applied] if applied < len(arch) else None)
                applied += 1
            if e.get("type") == "round_start" and e.get("iter") == it:
                return cur
        return cur
    prior = [a for a in arch if a.get("iter", 0) < it or (a.get("iter", 0) == 0 and it >= 1)]
    return prior[-1] if prior else None


def worker_idx(w: str) -> int:
    return int(w[1:]) if re.match(r"^w\d+$", str(w)) else -1


def who(c: dict) -> str:
    i = worker_idx(c.get("worker", ""))
    return f"Worker {i + 1}" if i >= 0 else "the published design (starting point)"


def role_sat(c: dict, role: str) -> tuple[float, str] | None:
    best = None
    for p in c.get("points", []):
        v = (p.get("sat_margin_v") or {}).get(role)
        if v is not None and (best is None or v < best[0]):
            best = (v, f"{p['corner']}/{p['temp']}")
    return best


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.;!?])\s+(?=[A-Z(])", text or "") if len(s.strip()) > 12]


# --------------------------------------------------------------------------- 1. part explorer
def overview(run: str, upto: int | None = None) -> dict:
    R = load_run(run, upto)
    parts = []
    for p in [r for r in PARTS if r in R["roles"]] + ["comp", "bias"]:
        ks = [k for k in R["knobs"] if knob_part(k) == p]
        if p in R["roles"] or ks:
            parts.append({"part": p, "name": PARTS[p][0], "what": PARTS[p][1], "knobs": ks,
                          "devices": [d["label"] for d in R["roles"].get(p, [])]})
    return {"run": run, "circuit": R["circuit"], "circuit_name": CIRCUIT_NAME.get(R["circuit"], R["circuit"]),
            "parts": parts, "n_candidates": len(R["hist"]), "best_n": R["best"]["n"] if R["best"] else None,
            "has_run": bool(R["hist"]),
            "plans": [{"iter": a.get("iter"), "after_n": a.get("n_evaluated_at_request"), "model": a.get("model"),
                       "latency_s": a.get("latency_s"), "diagnosis": a.get("diagnosis", ""), "binding": a.get("binding_constraint"),
                       "directions": a.get("directions", [])} for a in R["arch"]
                      if not upto or upto <= 1 or a.get("n_evaluated_at_request", 0) <= upto]}


def static_part(part: str, circuit: str) -> dict:
    """Facts that need no run (step 1, before any crew run)."""
    try:
        circ = circuits().get(circuit) or {}
    except Exception:  # noqa: BLE001
        circ = {}
    name, what, how, _ = PARTS.get(part, (part, "", "", []))
    space = circ.get("space") or {}
    ks = [k for k in space if knob_part(k) == part]
    return {"part": part, "name": name, "what": what, "how": how, "circuit": circuit,
            "circuit_name": CIRCUIT_NAME.get(circuit, circuit),
            "devices": [{"label": inst.lstrip("X"), "subckt": sub} for (sub, inst), r in (circ.get("role_map") or {}).items() if r == part],
            "knobs": [{"knob": k, "unit": pretty_unit(k), "published": disp(k, space[k][0]) if space[k] else None, "now": None,
                       "lo": disp(k, space[k][1]) if space[k] else None, "hi": disp(k, space[k][2]) if space[k] else None} for k in ks],
            "sat": None, "changes": [], "architect": [], "as_of": None,
            "sat_required_mv": round(1e3 * (circ.get("sat_margin") or {}).get(part, 0), 1) if part in (circ.get("sat_margin") or {}) else None}


def part(run: str, name: str, upto: int | None = None, circuit: str | None = None) -> dict:
    R = load_run(run, upto) if run else {"hist": []}
    if not R["hist"]:
        out = static_part(name, circuit or (R.get("circuit") if run else None) or "diff")
        out["note"] = "No crew run loaded yet: showing the published design only."
        return out
    out = static_part(name, R["circuit"])
    best, pub = R["best"], R["published"]
    out["knobs"] = []
    for k in R["knobs"]:
        if knob_part(k) != name:
            continue
        sp = R["space"].get(k)
        now, p0 = best["params"].get(k), pub.get(k)
        out["knobs"].append({"knob": k, "unit": pretty_unit(k), "published": disp(k, p0), "now": disp(k, now),
                             "ratio": round(now / p0, 2) if now and p0 else None,
                             "lo": disp(k, sp[1]) if sp else None, "hi": disp(k, sp[2]) if sp else None})
    req = (R["circ"].get("sat_margin") or {}).get(name)
    s_now, s_pub = role_sat(best, name), role_sat(R["hist"][0], name)
    if s_now:
        need = (req or 0.0) + R["sat_guard"]
        out["sat"] = {"worst_mv": round(s_now[0] * 1e3, 1), "at": s_now[1], "n": best["n"],
                      "required_mv": round(need * 1e3, 1), "ok": s_now[0] >= need - 1e-9,
                      "published_mv": round(s_pub[0] * 1e3, 1) if s_pub else None, "published_at": s_pub[1] if s_pub else None,
                      "source": f"design #{best['n']}, 15 ngspice operating points (history.jsonl)"}
    # why it changed: improving candidates (best-so-far lineage) that moved one of this part's knobs
    changes = []
    for h in R["hist"]:
        par = parent_of(R, h)
        if par is None:
            continue
        d = [x for x in knob_diff(R, par["params"], h["params"]) if x["part"] == name]
        if not d:
            continue
        changes.append({"n": h["n"], "who": who(h), "worker": h.get("worker"), "iter": h.get("iter"), "diff": d,
                        "vs": par["n"], "rationale": h.get("rationale", ""), "passed": bool(h.get("passed")),
                        "pass_count": n_pass(h), "became_best": h["n"] in R["lineage"]})
    key = [c for c in changes if c["became_best"]]
    rest = [c for c in changes if not c["became_best"]]
    out["changes"] = key[-5:] or rest[-3:]
    out["n_changes"] = len(changes)
    out["n_improving"] = len(key)
    pats = [re.compile(p) for p in PARTS.get(name, ("", "", "", []))[3]]
    arch = []
    for a in R["arch"]:
        if upto and a.get("n_evaluated_at_request", 0) > upto:
            continue
        srcs = [("diagnosis", a.get("diagnosis", ""))] + [(f"direction “{d.get('focus', '')}”", d.get("instructions", ""))
                                                           for d in a.get("directions", [])]
        for where, text in srcs:
            for s in sentences(text):
                if any(p.search(s) for p in pats):
                    arch.append({"iter": a.get("iter", 0), "where": where, "text": s,
                                 "after_n": a.get("n_evaluated_at_request")})
    diag = [x for x in arch if x["where"] == "diagnosis"]
    out["architect"] = (diag[:4] + [x for x in arch if x["where"] != "diagnosis"][:max(0, 6 - len(diag[:4]))])
    out["as_of"] = {"n_candidates": len(R["hist"]), "best_n": best["n"], "best_passed": bool(best.get("passed")), "run": run}
    return out


# --------------------------------------------------------------------------- 2. decision trace
def fail_summary(c: dict, target: dict) -> list[dict]:
    by: dict[str, list] = {}
    for p in c.get("points", []):
        for f in p.get("fails", []):
            by.setdefault(f, []).append(p)
    out = []
    for f, pts in sorted(by.items(), key=lambda kv: -len(kv[1])):
        where = [f"{p['corner']}/{p['temp']}" for p in pts]
        if f.startswith("sat"):
            role = f.split(":", 1)[-1]
            v = min(((p.get("sat_margin_v") or {}).get(role, 9), f"{p['corner']}/{p['temp']}") for p in pts)
            out.append({"check": f, "part": role, "label": f"{PARTS.get(role, (role,))[0]} runs out of voltage headroom (saturation)",
                        "count": len(pts), "where": where, "worst": f"{v[0] * 1e3:.0f} mV margin at {v[1]}"})
        else:
            word, jargon, sc, u = CHECK.get(f, (f, f, 1.0, ""))
            lo = f != "i_core_a"
            vals = [(p.get(f), f"{p['corner']}/{p['temp']}") for p in pts if p.get(f) is not None]
            w = (min(vals) if lo else max(vals)) if vals else (None, "")
            need = target.get(f)
            out.append({"check": f, "label": f"{word} ({jargon})", "count": len(pts), "where": where,
                        "worst": (f"{w[0] * sc:.1f}{u} at {w[1]}" if w[0] is not None else "") +
                                 (f"; needs {'≥' if lo else '≤'} {need * sc:.1f}{u}" if need is not None else "")})
    return out


def candidate(run: str, n: int) -> dict:
    R = load_run(run)
    c = next((h for h in R["hist"] if h.get("n") == n), None)
    if c is None:
        return {"error": f"run {run} has no candidate #{n}"}
    it, wi = c.get("iter", 0), worker_idx(c.get("worker", ""))
    plan = plan_for(R, it) if wi >= 0 else None
    direction = None
    if plan and plan.get("directions"):
        d = plan["directions"][wi % len(plan["directions"])]
        direction = {"focus": d.get("focus", ""), "knobs": d.get("knobs", []), "instructions": d.get("instructions", ""),
                     "plan_iter": plan.get("iter", 0), "plan_after_n": plan.get("n_evaluated_at_request"),
                     "model": plan.get("model")}
    par = parent_of(R, c)
    nxt = next((a for a in R["arch"] if a.get("n_evaluated_at_request", 0) >= n), None)
    call = next((l for l in read_jsonl(R["dir"] / "llm_calls.jsonl") if l.get("tag") == f"worker{wi}_it{it}"), None) if wi >= 0 else None
    prev_best = None
    for h in R["hist"]:
        if h["n"] < n and better(h, prev_best):
            prev_best = h
    out = {
        "run": run, "n": n, "iter": it, "worker": c.get("worker"), "who": who(c), "passed": bool(c.get("passed")),
        "pass_count": n_pass(c), "rationale": c.get("rationale", ""), "direction": direction,
        "direction_note": None if direction or wi < 0 else "No architect plan had landed yet: this worker was on a generic starting direction.",
        "points": c.get("points", []), "worst": c.get("worst", {}), "worst_at": c.get("worst_at", {}),
        "fail_summary": fail_summary(c, R["target"]), "failures": c.get("failures", [])[:30],
        "target": R["target"], "spec": R["spec"], "display": c.get("display", {}),
        "parent": {"n": par["n"], "pass_count": n_pass(par), "why": "the design this worker says it started from" if par.get("_stated") else f"the best design when round {it} began"} if par else None,
        "diff": knob_diff(R, par["params"], c["params"]) if par else [],
        "n_knobs": len(R["knobs"]),
        "diff_published": knob_diff(R, R["published"], c["params"]),
        "became_best": n in R["lineage"], "is_best": bool(R["best"] and R["best"]["n"] == n),
        "beat": prev_best["n"] if prev_best and n in R["lineage"] else None,
        "next_plan": ({"iter": nxt.get("iter"), "after_n": nxt.get("n_evaluated_at_request"), "diagnosis": nxt.get("diagnosis", ""),
                       "binding": nxt.get("binding_constraint"), "model": nxt.get("model"),
                       "directions": [d.get("focus", "") for d in nxt.get("directions", [])]} if nxt else None),
        "next_plan_note": None if nxt else (f"The architect wrote no plan after design #{n} "
                                            f"({len(R['arch'])} plan(s) in this run, the last after design "
                                            f"#{max([a.get('n_evaluated_at_request', 0) for a in R['arch']] or [0])})."),
        "sources": {
            "simulation": f"runs/swarm/{run}/history.jsonl, record n={n}: {len(c.get('points', []))} ngspice runs "
                          f"(5 process corners × 3 temperatures)",
            "agent": (f"runs/swarm/{run}/llm_calls.jsonl, call “{call.get('tag')}”: {call.get('model')}, "
                      f"{float(call.get('latency_s') or 0):.1f} s, {call.get('in', '?')} tokens in / {call.get('out', '?')} out"
                      if call else ("published sizing from the benchmark netlist (no agent involved)" if wi < 0 else
                                    f"runs/swarm/{run}/history.jsonl (worker message not logged separately)")),
            "agent_reply": str(call.get("response", ""))[:1500] if call else None},
    }
    return out


# --------------------------------------------------------------------------- 3. ask the crew
def cand_line(c: dict, full: bool = True) -> str:
    w, wa = c.get("worst", {}), c.get("worst_at", {})
    fails: dict[str, list] = {}
    for p in c.get("points", []):
        for f in p.get("fails", []):
            fails.setdefault(f, []).append(f"{p['corner']}/{p['temp']}")
    ftxt = "; ".join(f"{f} fails at {len(v)} corner(s) [{', '.join(v[:4])}{'…' if len(v) > 4 else ''}]" for f, v in fails.items())
    s = (f"#{c['n']} [{who(c)}, round {c.get('iter', 0)}] {'PASS' if c.get('passed') else 'FAIL'} {n_pass(c)}/15 | "
         f"worst PM {w.get('pm_deg', float('nan')):.1f} deg @{wa.get('pm_deg')} | worst UGF {(w.get('ugf_hz') or 0) / 1e6:.1f} MHz @{wa.get('ugf_hz')} | "
         f"worst gain {w.get('dc_gain_db', float('nan')):.1f} dB @{wa.get('dc_gain_db')} | worst current {(w.get('i_core_a') or 0) * 1e6:.1f} uA @{wa.get('i_core_a')} | "
         f"min saturation margin {(w.get('min_sat_margin_v') or 0) * 1e3:.0f} mV @{wa.get('min_sat_margin_v')}")
    if ftxt:
        s += f"\n    failed checks: {ftxt}"
    if full:
        s += f"\n    knobs: {json.dumps(c.get('display', {}))}\n    proposer's reason: {c.get('rationale', '')[:400]}"
    return s


def context(run: str) -> list[dict]:
    """Grounded context for the explainer, as titled sections (also shown to the user as 'what I looked at')."""
    R = load_run(run)
    sec: list[dict] = []
    add = lambda t, x: x and sec.append({"title": t, "text": x.strip()})
    if not R["hist"]:
        add("Run", f"Run {run} has no recorded designs yet.")
        return sec
    try:
        from sizing import swarm as sw
        card = sw.CARDS.get(R["circuit"], "")
        card = card.replace("{SAT_HI}", "60" if R["guarded"] else "50").replace("{SAT_LO}", "10" if R["guarded"] else "0")
    except Exception:  # noqa: BLE001
        card = f"{CIRCUIT_NAME.get(R['circuit'])}; parts: " + "; ".join(f"{p}: {PARTS[p][1]}" for p in R["roles"])
    add("Circuit card", card)
    sc = {"pm_deg": (1, "deg"), "ugf_hz": (1e-6, "MHz"), "dc_gain_db": (1, "dB"), "i_core_a": (1e6, "uA")}
    add("Spec and guard-banded target",
        "\n".join(f"{CHECK[k][1]} ({CHECK[k][0]}): datasheet spec {'<=' if k == 'i_core_a' else '>='} {R['spec'].get(k, 0) * sc[k][0]:.4g} {sc[k][1]}; "
                  f"crew's guard-banded target {'<=' if k == 'i_core_a' else '>='} {R['target'].get(k, 0) * sc[k][0]:.4g} {sc[k][1]}"
                  for k in sc if k in R["target"]) +
        "\nA design passes only if every check holds at all 15 process/temperature corners (tt, ff, ss, sf, fs × -40, 27, 125 C)."
        + (f"\nGuard band on saturation margin: +{R['sat_guard'] * 1e3:.0f} mV." if R["guarded"] else ""))
    s = R["summary"]
    if s:
        add("Run summary", json.dumps({k: s.get(k) for k in ("run_id", "candidates", "decks", "success", "candidates_to_first_pass",
                                                                "best_n", "recheck_full_lib", "n_passed_target", "n_passed_nominal_spec",
                                                                "best_i_core_uA", "llm_calls", "tokens", "wall_s", "models")}))
    b = R["best"]
    roles = "; ".join(f"{r}: {v[0] * 1e3:.0f} mV at {v[1]}" for r in R["roles"] if (v := role_sat(b, r)))
    pts = "\n".join(f"  {p['corner']}/{p['temp']}: PM {p.get('pm_deg', 0):.1f} deg, UGF {(p.get('ugf_hz') or 0) / 1e6:.1f} MHz, gain {p.get('dc_gain_db', 0):.1f} dB, "
                    f"current {(p.get('i_core_a') or 0) * 1e6:.1f} uA, {'pass' if p.get('pass') else 'FAIL ' + ','.join(p.get('fails', []))}" for p in b.get("points", []))
    pub = "; ".join(f"{x['knob']} {x['from']} -> {x['to']} {x['unit']}" for x in knob_diff(R, R["published"], b["params"]))
    add(f"Best design (#{b['n']})", cand_line(b) + f"\n    worst saturation margin per part: {roles}\n  all 15 corners:\n{pts}\n  changes vs the published sizing: {pub}")
    add("Published starting design (#1)", cand_line(R["hist"][0]))
    add("Best-so-far lineage", "Designs that became the new best, in order: " + ", ".join(f"#{n}" for n in R["lineage"]))
    counts: dict[str, int] = {}
    corner_fail: dict[str, int] = {}
    for h in R["hist"]:
        seen = set()
        for p in h.get("points", []):
            if not p.get("pass"):
                corner_fail[f"{p['corner']}/{p['temp']}"] = corner_fail.get(f"{p['corner']}/{p['temp']}", 0) + 1
            seen |= set(p.get("fails", []))
        for f in seen:
            counts[f] = counts.get(f, 0) + 1
    add("Failure digest", f"{len(R['hist'])} designs simulated, {sum(1 for h in R['hist'] if h.get('passed'))} passed all 15 corners.\n"
        "Designs failing each check (at one or more corners): " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) +
        "\nDesigns failing at each corner: " + ", ".join(f"{k}: {v}" for k, v in sorted(corner_fail.items(), key=lambda kv: -kv[1])))
    add("Every design tried", "\n".join(cand_line(h, full=len(R["hist"]) <= 60 or h["n"] in R["lineage"]) for h in R["hist"]))
    for a in R["arch"]:
        add(f"Architect plan (written after design #{a.get('n_evaluated_at_request')}, round {a.get('iter')})",
            f"Diagnosis: {a.get('diagnosis', '')}\nBinding constraint: {a.get('binding_constraint')}\n" +
            "\n".join(f"Direction for Worker {i + 1}: {d.get('focus')}: {d.get('instructions', '')}" for i, d in enumerate(a.get("directions", []))))
    crit = [e for e in R["events"] if e.get("type") == "critic"]
    add("Plot-reviewer notes", "\n".join(f"On design #{e.get('n')}: {e.get('note', '')}" for e in crit[-4:]))
    sd = RUNS / "signoff" / run
    comp = read_json(sd / "comparison.json")
    if comp:
        comp.pop("rows", None)
        add("Independent sign-off agent", json.dumps(comp))
    lay = None
    for d in sorted((RUNS / "layout").glob("*/summary.json")) if (RUNS / "layout").exists() else []:
        ev = read_jsonl(d.parent / "events.jsonl")
        st = next((e for e in ev if e.get("type") == "start"), {})
        if f"swarm/{run}/" in str(st.get("design_from", "")) and "crew" in d.parent.name:
            lay = next((e for e in ev if e.get("type") == "done"), None)
    if lay:
        add("Layout planning result", json.dumps({k: v for k, v in lay.items() if k not in ("t", "type")}) +
            "\n(estimated parasitics; not routed; no DRC/LVS)")
    for d in sorted((RUNS / "redteam").iterdir()) if (RUNS / "redteam").exists() else []:
        f, sm = read_json(d / "findings.json"), read_json(d / "summary.json")
        if f or sm:
            fs = (f or {}).get("findings", []) if isinstance(f, dict) else (f or [])
            add(f"Red team ({d.name}): attacks on the Judge itself, not on this design",
                (sm or {}).get("summary", "") + "\n" + "\n".join(f"- [{x.get('severity')}] {x.get('id')}: {x.get('title')}" for x in fs))
    return sec


ASK_SCHEMA = {"type": "object", "properties": {
    "question": {"type": "string", "description": "the visitor's question, verbatim (transcribe it if it was spoken)"},
    "answer": {"type": "string", "description": "2-5 short plain-English sentences"},
    "candidates_cited": {"type": "array", "items": {"type": "integer"}},
    "corners_cited": {"type": "array", "items": {"type": "string"}},
    "answered_from_data": {"type": "boolean", "description": "false if the recorded data does not contain the answer"}},
    "required": ["question", "answer", "candidates_cited", "corners_cited", "answered_from_data"]}
ASK_PROMPT = """You are the Explainer of "Tapeout Crew": a crew of AI agents sized an analog op-amp while the ngspice
simulator judged every design at 15 process/temperature corners. A visitor at a live demo asks a question.
Answer in plain English for a non-expert, in 2-4 short sentences (at most 90 words), using ONLY the recorded data below.
Rules:
- Cite what you relied on inline: design numbers as #27, corners as fs/125.
- Never invent a number, a cause or an event. Every number must appear in the data.
- If the data does not contain the answer, begin with exactly "The simulations don't show that." then say in
  one sentence what the data does show that is closest, and set answered_from_data to false.
- Explain jargon the first time (phase margin = how far from oscillating; unity-gain frequency = speed).
"""


def to_wav(data: bytes, mime: str) -> tuple[bytes, str]:
    if any(x in mime for x in ("wav", "mp3", "flac")):
        return data, mime
    try:
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.bin", Path(td) / "clip.wav"
            src.write_bytes(data)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "16000", str(dst)],
                           check=True, timeout=30)
            return dst.read_bytes(), "audio/wav"
    except Exception:  # noqa: BLE001
        return data, mime or "audio/webm"


def _call(prompt: str, schema: dict, audio: bytes | None, mime: str, timeout: int = 60) -> tuple[dict, float]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from sizing import managed
    c = managed.client()
    blocks = [{"type": "text", "text": prompt}]
    if audio:
        wav, m = to_wav(audio, mime)
        blocks.append({"type": "audio", "mime_type": m, "data": base64.b64encode(wav).decode()})
    t0 = time.perf_counter()
    r = c.interactions.create(model=EXPLAIN_MODEL, input=blocks, generation_config={"thinking_level": "low"},
                              response_format={"type": "text", "mime_type": "application/json", "schema": schema},
                              timeout=timeout)
    return json.loads(r.output_text), round(time.perf_counter() - t0, 2)


def _log(kind: str, rec: dict) -> None:
    try:
        (RUNS / "live").mkdir(parents=True, exist_ok=True)
        with (RUNS / "live" / "ask_log.jsonl").open("a") as fh:
            fh.write(json.dumps({"t": time.strftime("%H:%M:%S"), "kind": kind, **rec}) + "\n")
    except OSError:
        pass


def ask(run: str, question: str = "", audio: bytes | None = None, mime: str = "") -> dict:
    sec = context(run)
    ctx = "\n\n".join(f"=== {s['title']} ===\n{s['text']}" for s in sec)
    q = "The visitor's question is in the attached audio: transcribe it, then answer it." if audio else f"The visitor asks: {question}"
    looked = [{"title": s["title"], "chars": len(s["text"]), "text": s["text"][:6000]} for s in sec]
    try:
        d, lat = _call(f"{ASK_PROMPT}\n{q}\n\nRECORDED DATA for run {run}:\n\n{ctx}", ASK_SCHEMA, audio, mime)
    except Exception as e:  # noqa: BLE001
        return {"error": f"explainer unavailable ({type(e).__name__}: {str(e)[:200]})", "looked_at": looked, "run": run}
    hist_n = {h["n"] for h in read_jsonl(SWARM / run / "history.jsonl")}
    out = {"run": run, "question": d.get("question") or question, "answer": d.get("answer", ""),
           "candidates_cited": [n for n in d.get("candidates_cited", []) if n in hist_n],
           "corners_cited": d.get("corners_cited", []), "answered_from_data": bool(d.get("answered_from_data")),
           "model": EXPLAIN_MODEL, "latency_s": lat, "source": "audio" if audio else "text", "looked_at": looked,
           "context_chars": len(ctx)}
    _log("ask", {k: out[k] for k in ("run", "question", "answer", "candidates_cited", "corners_cited", "answered_from_data", "latency_s")})
    return out


# --------------------------------------------------------------------------- 4. request a change
CHANGE_SCHEMA = {"type": "object", "properties": {
    "request": {"type": "string", "description": "what the person asked for, verbatim (transcribe if spoken)"},
    "pm_deg_min": {"type": "number", "description": "new minimum phase margin, degrees"},
    "ugf_mhz_min": {"type": "number", "description": "new minimum unity-gain frequency, MHz"},
    "dc_gain_db_min": {"type": "number", "description": "new minimum DC gain, dB"},
    "i_core_ua_max": {"type": "number", "description": "new maximum core current, microamps"},
    "reason": {"type": "string", "description": "one or two plain sentences: which numbers you moved, why, and what trade-off the recorded data suggests"},
    "understood": {"type": "boolean", "description": "false if the request is not about these four requirements"}},
    "required": ["request", "pm_deg_min", "ugf_mhz_min", "dc_gain_db_min", "i_core_ua_max", "reason", "understood"]}
CHANGE_PROMPT = """You turn a spoken or typed change request for an op-amp into a new spec. There are exactly four
requirements: minimum phase margin (deg; "stability"), minimum unity-gain frequency (MHz; "speed", "faster",
"bandwidth"), minimum DC gain (dB; "accuracy", "precision"), maximum core current (uA; "power", "battery").
Return ALL FOUR numbers of the new spec: change only what the request implies, keep the others exactly as they are.
If the person gives a number, use it. If they are qualitative ("faster", "I can spend more power"), choose a
meaningful but realistic step (typically 1.5x-2x for speed or current, +5 for degrees or dB), guided by what the
current best design already achieves. Supported ranges: phase margin 45-80, speed 1-60 MHz, gain 40-85 dB,
current 10-400 uA. If the request is not about these four things, set understood=false and keep the spec unchanged.
"""


def spec_to_flat(spec: dict) -> dict:
    g = lambda k: (spec.get(k) or DEFAULT_SPEC[k])[1]
    return {"pm_deg_min": g("pm_deg"), "ugf_mhz_min": g("ugf_hz") / 1e6, "dc_gain_db_min": g("dc_gain_db"), "i_core_ua_max": g("i_core_a") * 1e6}


def flat_to_spec(f: dict) -> dict:
    return {"pm_deg": [">=", float(f["pm_deg_min"])], "ugf_hz": [">=", float(f["ugf_mhz_min"]) * 1e6],
            "dc_gain_db": [">=", float(f["dc_gain_db_min"])], "i_core_a": ["<=", float(f["i_core_ua_max"]) * 1e-6]}


def start_from_supported() -> bool:
    try:
        return "--start-from" in (ROOT / "sizing" / "swarm.py").read_text()
    except OSError:
        return False


def change(run: str, text: str = "", audio: bytes | None = None, mime: str = "", spec: dict | None = None) -> dict:
    R = load_run(run) if run else {"hist": []}
    if not spec and R["hist"]:
        spec = {k: [DEFAULT_SPEC[k][0], v] for k, v in R["spec"].items() if k in DEFAULT_SPEC}
    spec = {k: list(v) for k, v in (spec or DEFAULT_SPEC).items()}
    cur = spec_to_flat(spec)
    best = ""
    if R["hist"] and R["best"]:
        b = R["best"]
        best = (f"\nThe current best design (#{b['n']}, {'passes' if b.get('passed') else 'does NOT pass'} all 15 corners) achieves, worst case: "
                + cand_line(b, full=False))
    req = "The request is in the attached audio: transcribe it." if audio else f"The request: {text}"
    try:
        d, lat = _call(f"{CHANGE_PROMPT}\nCurrent spec: {json.dumps(cur)}{best}\n\n{req}", CHANGE_SCHEMA, audio, mime)
    except Exception as e:  # noqa: BLE001
        return {"error": f"spec editor unavailable ({type(e).__name__}: {str(e)[:200]})", "before": spec}
    new, notes = {}, []
    for k, (lo, hi) in SPEC_LIMITS.items():
        v = d.get(k)
        v = cur[k] if v is None else float(v)
        cl = min(max(v, lo), hi)
        if cl != v:
            notes.append(f"{k}={v:g} is outside the supported range [{lo}, {hi}]; clamped to {cl:g}")
        new[k] = cl
    if not d.get("understood", True):
        new = dict(cur)
    after = flat_to_spec(new)
    changed = [k for k in after if abs(after[k][1] - spec[k][1]) > 1e-9 * max(abs(spec[k][1]), 1e-30)]
    out = {"run": run, "request": d.get("request") or text, "before": spec, "after": after, "after_target": guarded(after),
           "before_target": guarded(spec), "changed": changed, "reason": d.get("reason", ""), "notes": notes,
           "understood": bool(d.get("understood", True)), "model": EXPLAIN_MODEL, "latency_s": lat,
           "start_from": bool(R["hist"] and R.get("summary", {}).get("best_params") and start_from_supported()),
           "start_from_n": R["best"]["n"] if R["hist"] and R["best"] else None, "circuit": R.get("circuit")}
    _log("change", {k: out[k] for k in ("run", "request", "after", "changed", "reason", "latency_s")})
    return out


# --------------------------------------------------------------------------- 6. PCB boards
def pcb_plan_words(plan: dict | None) -> str:
    if not isinstance(plan, dict) or not plan:
        return ""
    bits = []
    rip, order, mode = plan.get("ripup") or [], plan.get("order") or [], plan.get("mode")
    if rip:
        bits.append("ripped up " + ("nets " if len(rip) > 1 else "net ") + ", ".join(map(str, rip[:8])) + ("…" if len(rip) > 8 else ""))
    if order:
        bits.append("re-routed in this order: " + " → ".join(map(str, order[:8])) + ("…" if len(order) > 8 else ""))
    if mode:
        m = str(mode).lower()
        bits.append({"shove": "router allowed to shove existing tracks aside", "walkaround": "router had to walk around existing tracks",
                     "highlight": "router stops at collisions"}.get(m, f"router mode: {mode}"))
    s = "; ".join(bits)
    return (s[:1].upper() + s[1:] + ".") if s else ""


def pcb_runs() -> dict:
    out = []
    if PCB.exists():
        for d in sorted([p for p in PCB.iterdir() if p.is_dir() and not p.name.startswith("_")], key=lambda p: p.stat().st_mtime, reverse=True):
            s = read_json(d / "summary.json")
            boards = []
            src = (s or {}).get("boards") if isinstance(s, dict) else None
            if isinstance(src, dict):
                src = [dict(v, board_id=k) if isinstance(v, dict) else {"board_id": k} for k, v in src.items()]
            for b in src or []:
                bid = str(b.get("board_id") or b.get("id") or b.get("board") or b.get("name") or "")
                bd = d / bid
                boards.append({**b, "board_id": bid, "has_start": (bd / "start.png").exists(), "has_final": (bd / "final.png").exists(),
                               "plan_words": pcb_plan_words(b.get("best_plan"))})
            if s is None:   # run in progress: list board folders that exist so far
                for bd in sorted(p for p in d.iterdir() if p.is_dir()):
                    h = read_json(bd / "history.json") or []
                    boards.append({"board_id": bd.name, "running": True, "attempts": len(h), "has_start": (bd / "start.png").exists(),
                                   "has_final": (bd / "final.png").exists()})
            out.append({"id": d.name, "done": s is not None, "summary": {k: v for k, v in (s or {}).items() if k != "boards"} if isinstance(s, dict) else {},
                        "boards": boards})
    return {"runs": out}


def pcb_board(run: str, board: str) -> dict:
    bd = PCB / run / board
    hist = read_json(bd / "history.json") or []
    if isinstance(hist, dict):
        hist = hist.get("attempts") or hist.get("history") or []
    for h in hist:
        if isinstance(h, dict):
            h["plan_words"] = pcb_plan_words(h.get("plan"))
    calls = []
    for l in read_jsonl(bd / "llm_calls.jsonl"):
        r = l.get("response")
        if isinstance(r, str):
            try:
                r = json.loads(r)
            except json.JSONDecodeError:
                pass
        calls.append({"tag": l.get("tag"), "model": l.get("model"), "latency_s": l.get("latency_s"),
                      "diagnosis": (r.get("diagnosis") if isinstance(r, dict) else None) or l.get("diagnosis"),
                      "plans": (r.get("plans") if isinstance(r, dict) else None) or l.get("plans"),
                      "raw": None if isinstance(r, dict) else str(r)[:1200]})
    return {"run": run, "board": board, "history": hist, "llm": calls,
            "has_start": (bd / "start.png").exists(), "has_final": (bd / "final.png").exists()}


if __name__ == "__main__":   # quick offline check:  python -m demo.explain guard_s0 [part|n]
    a = sys.argv[1:]
    run_ = a[0] if a else "guard_s0"
    if len(a) > 1 and a[1].isdigit():
        print(json.dumps(candidate(run_, int(a[1])), indent=1)[:6000])
    elif len(a) > 1 and a[1] == "context":
        for s_ in context(run_):
            print(f"=== {s_['title']} ({len(s_['text'])} chars)\n{s_['text'][:700]}\n")
    elif len(a) > 1:
        print(json.dumps(part(run_, a[1]), indent=1)[:6000])
    else:
        print(json.dumps(overview(run_), indent=1))
