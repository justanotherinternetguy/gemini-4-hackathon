"""Projector-friendly STORY page for one swarm run: full-screen scenes, navigable like slides.

  python -m viz.story --run runs/swarm/<id> [--baselines guard|default] [--out runs/viz/story_<id>.html]
  python -m viz.story --hero [--baselines guard]     # best finished guard_s* run -> runs/viz/story_hero.html

One self-contained HTML file (CSS, JS, SVG inline; works offline from file://). Charts are native SVG
drawn by the embedded JS from an embedded JSON blob; the schematic and the Bode plot are matplotlib SVG.
Keys: ←/→ (or PgUp/PgDn/space) step through scenes, Home/End jump; click empty space = next scene.
URL hash #s<k> opens scene k; ?cand=<n> preselects candidate n in the playback (for screenshots).

Runs without per-point data (logged before the guard band existed) are re-simulated once per
candidate with sizing.opamp2stg.evaluate (~0.6 s each) and cached in <run>/story_points.json.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sizing import opamp2stg as oa

HERE = Path(__file__).resolve().parent
E = html.escape
SWARM_ROOT = oa.ROOT / "runs" / "swarm"
BASE_ROOT = oa.ROOT / "runs" / "opamp_baselines"
VIZ_ROOT = oa.ROOT / "runs" / "viz"

# $ per 1M tokens (input, output); thought tokens are billed as output
PRICES = {"gemini-4-hydrogen": (1.50, 7.50), "gemini-4-carbon": (2.00, 10.00)}
METRICS = ("pm_deg", "ugf_hz", "dc_gain_db", "i_core_a")
CATEGORY = {"pm_deg": "stability", "ugf_hz": "speed", "dc_gain_db": "accuracy", "i_core_a": "power",
            "sat": "headroom", "sim": "sim error"}
CORNER_NAME = {"tt": "typical", "ff": "fast", "ss": "slow", "sf": "slow-N / fast-P", "fs": "fast-N / slow-P"}
ROLE_TEXT = {  # plain description, technical name
    "in": ("Input pair", "senses the difference between the two inputs"),
    "load": ("Mirror load", "turns that difference into a voltage"),
    "tail": ("Tail source", "sets how much current stage 1 burns"),
    "drv": ("Output driver", "second gain stage; drives the load"),
    "sink": ("Output sink", "sets how much current stage 2 burns"),
}
CACHE_VERSION = 1


# --------------------------------------------------------------------------- io helpers

def _jsonl(path: Path) -> list[dict]:
    out = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict):
                out.append(d)
    return out


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


# --------------------------------------------------------------------------- per-point data

def _point_from_result(r: oa.PointResult) -> dict:
    roles = {}
    for d in r.devices.values():
        if d["role"]:
            roles[d["role"]] = min(roles.get(d["role"], 9.0), d["margin"])
    return {"corner": r.corner, "temp": r.temp, "pm_deg": r.pm_deg, "ugf_hz": r.ugf_hz,
            "dc_gain_db": r.dc_gain_db, "i_core_a": r.i_core_a, "sat_margin_v": roles,
            "fails": [] if r.ok else ["sim"]}


def _guard_sig() -> str:
    return json.dumps([CACHE_VERSION, oa.GUARD, oa.SAT_GUARD_V, oa.SAT_MARGIN, oa.DEFAULT_SPEC], sort_keys=True)


def ensure_points(run_dir: Path, recs: list[dict], jobs: int = 3) -> None:
    """Fill rec['points'] for records logged without them (re-simulate once, cache next to the run)."""
    missing = [r for r in recs if not r.get("points")]
    if not missing:
        return
    cache_path = run_dir / "story_points.json"
    cache = _json(cache_path)
    if cache.get("sig") != _guard_sig():
        cache = {"sig": _guard_sig(), "points": {}}
    key = lambda r: json.dumps({k: float(v) for k, v in sorted(r["params"].items())})
    todo = [r for r in missing if key(r) not in cache["points"]]
    if todo:
        print(f"  re-simulating {len(todo)} candidates without per-point data (cached in {cache_path}) …",
              flush=True)
        t0 = time.perf_counter()

        def one(r):
            er = oa.evaluate(r["params"], guard=True)
            return key(r), [_point_from_result(p) for p in er.points]
        with ThreadPoolExecutor(jobs) as ex:
            for k, pts in ex.map(one, todo):
                cache["points"][k] = pts
        cache_path.write_text(json.dumps(cache))
        print(f"  done in {time.perf_counter() - t0:.0f} s", flush=True)
    for r in missing:
        r["points"] = cache["points"][key(r)]


def classify(pt: dict) -> dict:
    """One PVT point vs the datasheet spec and the guard-banded target.
    -> {s: 0 pass with margin | 1 passes spec, inside guard band | 2 fails spec, why: [...], slack_mv}"""
    nominal, target = oa.DEFAULT_SPEC, oa.guarded(oa.DEFAULT_SPEC)
    fail, guard = [], []
    for m in METRICS:
        v = pt.get(m)
        op, b = nominal[m]
        if v is None:
            fail.append(CATEGORY[m])
        elif not oa._OPS[op](v, b):
            fail.append(CATEGORY[m])
        elif not oa._OPS[op](v, target[m][1]):
            guard.append(CATEGORY[m])
    roles = pt.get("sat_margin_v") or {}
    slack = min((v - oa.sat_required(k, False) for k, v in roles.items()), default=None)
    if slack is None or slack < 0:
        fail.append("headroom")
    elif slack < oa.SAT_GUARD_V:
        guard.append("headroom")
    if "sim" in (pt.get("fails") or []):
        fail.append("sim error")
    return {"s": 2 if fail else 1 if guard else 0, "why": fail or guard,
            "slack_mv": None if slack is None else slack * 1e3}


def candidate(rec: dict) -> dict:
    pts = rec["points"]
    order = {(c, t): i for i, (c, t) in enumerate(oa.PVT15)}
    pts = sorted(pts, key=lambda p: order.get((p["corner"], int(p["temp"])), 99))
    cl = [classify(p) for p in pts]

    def agg(m, fn):
        vals = [p.get(m) for p in pts if p.get(m) is not None]
        return fn(vals) if vals else None
    slacks = [c["slack_mv"] for c in cl if c["slack_mv"] is not None]
    worst = {"pm_deg": agg("pm_deg", min), "ugf_mhz": (agg("ugf_hz", min) or 0) / 1e6 or None,
             "dc_gain_db": agg("dc_gain_db", min), "i_core_ua": (agg("i_core_a", max) or 0) * 1e6 or None,
             "slack_mv": min(slacks) if slacks else None}
    return {"n": int(rec.get("n") or 0), "iter": rec.get("iter"), "worker": rec.get("worker") or "?",
            "rationale": plain_units(str(rec.get("rationale") or "")), "params": rec.get("params") or {},
            "pts": [[c["s"], c["why"]] for c in cl], "worst": worst,
            "pass_target": all(c["s"] == 0 for c in cl) and len(cl) == 15,
            "pass_spec": all(c["s"] <= 1 for c in cl) and len(cl) == 15,
            "n_spec": sum(c["s"] <= 1 for c in cl), "n_target": sum(c["s"] == 0 for c in cl)}


# --------------------------------------------------------------------------- run summary helpers

def _short(text: str, words: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    w = first.split()
    return " ".join(w[:words]).rstrip(",;:") + ("…" if len(w) > words else "")


def plain_units(text: str) -> str:
    """'W_tail_um to 1.05 um, Ibias_uA 17 uA' -> 'W_tail to 1.05 µm, Ibias 17 µA' (LLM rationales use display keys)."""
    text = re.sub(r"\b([WL]_[a-z]+|Ibias|Cc|Rz)_(um|uA|pF|kohm)\b", r"\1", text or "")
    text = re.sub(r"(\d)\s*uA\b", r"\1 µA", text)
    text = re.sub(r"(\d)\s*um\b", r"\1 µm", text)
    text = re.sub(r"(\d)\s*kohm\b", r"\1 kΩ", text)
    return text


def _model_short(model: str) -> str:
    return {"gemini-4-carbon": "Carbon", "gemini-4-hydrogen": "Hydrogen"}.get(model, model or "?")


def llm_cost(calls: list[dict]) -> dict:
    by = {}
    for c in calls:
        if c.get("in") is None:
            continue
        m = c.get("model", "?")
        d = by.setdefault(m, {"calls": 0, "in": 0, "out": 0, "thought": 0})
        d["calls"] += 1
        for k in ("in", "out", "thought"):
            d[k] += c.get(k) or 0
    usd = 0.0
    for m, d in by.items():
        pin, pout = PRICES.get(m, (2.0, 10.0))
        d["usd"] = (d["in"] * pin + (d["out"] + d["thought"]) * pout) / 1e6
        usd += d["usd"]
    tokens = sum(d["in"] + d["out"] + d["thought"] for d in by.values())
    return {"by_model": by, "usd": usd, "tokens": tokens, "calls": sum(d["calls"] for d in by.values())}


def run_result(run_dir: Path) -> dict:
    """Compact outcome of one swarm run (for the comparison scene)."""
    s = _json(run_dir / "summary.json")
    hist = _jsonl(run_dir / "history.jsonl")
    if not s:
        return {"name": run_dir.name, "done": False, "progress": len(hist)}
    return {"name": run_dir.name, "done": True, "success": bool(s.get("success")),
            "first": s.get("candidates_to_first_pass"), "best_ua": s.get("best_i_core_uA") if s.get("success") else None,
            "recheck": s.get("recheck_full_lib"), "cost": llm_cost(_jsonl(run_dir / "llm_calls.jsonl"))}


def family_of(run_dir: Path, recs: list[dict]) -> str:
    s = _json(run_dir / "summary.json")
    if "guard_band" in s or any("passed_nominal" in r for r in recs):
        return "guard"
    return "default"


def comparison(tag: str) -> list[dict]:
    """Per method: seeds finished/expected, k successes, median candidates-to-first-pass, best current."""
    swarm_glob = "guard_s*" if tag == "guard" else "seed*"
    swarm = [run_result(d) for d in sorted(SWARM_ROOT.glob(swarm_glob)) if d.is_dir()]
    rows = [("swarm", "Gemini swarm", "Carbon architect + 4 Hydrogen workers", swarm)]
    for key, name, sub in (("gp", "Bayesian optimization", "Gaussian process (Optuna)"),
                           ("tpe", "TPE", "tree-structured Parzen (Optuna)"),
                           ("random", "Random search", "uniform in log-space")):
        res = []
        for f in sorted(BASE_ROOT.glob(f"{tag}_{key}_s*_c40.json")):
            s = _json(f).get("summary") or {}
            res.append({"name": f.stem, "done": True, "success": bool(s.get("success")),
                        "first": s.get("candidates_to_first_pass"),
                        "best_ua": s.get("best_i_core_uA"), "recheck": s.get("recheck_full_lib")})
        rows.append((key, name, sub, res))
    out = []
    for key, name, sub, res in rows:
        done = [r for r in res if r.get("done")]
        ok = [r for r in done if r.get("success")]
        firsts = [r["first"] for r in ok if r.get("first")]
        bests = [r["best_ua"] for r in ok if r.get("best_ua")]
        out.append({"key": key, "name": name, "sub": sub, "expected": max(3, len(res)), "done": len(done),
                    "running": [r.get("progress", 0) for r in res if not r.get("done")],
                    "k": len(ok), "median_first": statistics.median(firsts) if firsts else None,
                    "best_ua": min(bests) if bests else None,
                    "cost_usd": [r["cost"]["usd"] for r in done if r.get("cost")]})
    return out


# --------------------------------------------------------------------------- figures (matplotlib)

def figures(published: dict, final: dict) -> dict:
    from viz.bode import plot_bode
    from viz.common import inline_svg
    from viz.schematic import draw_schematic
    import io
    import matplotlib.pyplot as plt

    def svg_of(fig, uid):
        buf = io.StringIO()
        fig.savefig(buf, format="svg", bbox_inches="tight", pad_inches=0.05)
        plt.close(fig)
        return inline_svg(buf.getvalue(), uid)

    with ThreadPoolExecutor(2) as ex:
        er_a, er_b = ex.map(lambda p: oa.evaluate(p, curves=True, guard=True), (final["params"], published["params"]))
    bode = svg_of(plot_bode(er_a, er_b, oa.DEFAULT_SPEC, after_label="Gemini swarm", before_label="published",
                            story=True), "bode")
    sch = svg_of(draw_schematic(final["params"], compact=True), "sch")
    return {"bode": bode, "schematic": sch, "er_final": er_a}


# --------------------------------------------------------------------------- page

def _fmt_size(params: dict, role: str) -> str:
    from viz.common import role_size
    w, l, m = role_size(params, role)
    return (f"{m}× " if m > 1 else "") + f"{w * 1e6:.3g}/{l * 1e6:.3g}"


def _eng(v: float, unit: str) -> str:
    for s, k in (("M", 1e6), ("k", 1e3), ("", 1), ("m", 1e-3), ("µ", 1e-6), ("n", 1e-9), ("p", 1e-12), ("f", 1e-15)):
        if abs(v) >= k * 0.999:
            return f"{v / k:.3g} {s}{unit}"
    return f"{v:.3g} {unit}"


def build(run_dir: Path, out: Path, tag: str | None = None) -> Path:
    t0 = time.perf_counter()
    run_dir = Path(run_dir)
    recs = _jsonl(run_dir / "history.jsonl")
    if not recs:
        raise SystemExit(f"no candidates in {run_dir}/history.jsonl")
    summary = _json(run_dir / "summary.json")
    arch = _jsonl(run_dir / "architect.jsonl")
    calls = _jsonl(run_dir / "llm_calls.jsonl")
    family = family_of(run_dir, recs)
    tag = tag or family
    ensure_points(run_dir, recs)
    cands = [candidate(r) for r in recs]
    cands.sort(key=lambda c: c["n"])
    pub = next((c for c in cands if c["worker"] == "start"), cands[0])
    tgt = [c for c in cands if c["pass_target"]]
    spc = [c for c in cands if c["pass_spec"]]
    yard = "target" if tgt else "spec"
    pool = tgt or spc
    if pool:
        final = min(pool, key=lambda c: (c["worst"]["i_core_ua"] or 1e9, c["n"]))
    else:
        final = max(cands, key=lambda c: (c["n_spec"], c["n_target"]))
    first = next((c for c in cands if (c["pass_target"] if yard == "target" else c["pass_spec"])), None)

    # ---- agents: architect plans + worker focus + representative rationale
    arch_calls = [c for c in calls if str(c.get("tag", "")).startswith("architect") and c.get("in") is not None]
    plans = []
    for i, a in enumerate(arch):
        model = a.get("model") or (arch_calls[i].get("model") if i < len(arch_calls) else "gemini-4-carbon")
        lat = a.get("latency_s") or (arch_calls[i].get("latency_s") if i < len(arch_calls) else None)
        dirs = [d for d in a.get("directions", []) if isinstance(d, dict)]
        plans.append({"iter": a.get("iter", i), "model": model, "latency_s": lat,
                      "diagnosis": plain_units(_short(a.get("diagnosis", ""), 30)),
                      "focus": [str(d.get("focus", "")) for d in dirs],
                      "knobs": [[k.split("_")[0] + "_" + k.split("_")[1] if k.count("_") >= 2 else k
                                 for k in d.get("knobs", [])] for d in dirs]})
    workers = sorted({c["worker"] for c in cands if c["worker"] != "start"})
    wcards = []
    for w in workers:
        mine = [c for c in cands if c["worker"] == w]
        good = [c for c in mine if (c["pass_target"] if yard == "target" else c["pass_spec"])]
        rep = min(good, key=lambda c: c["worst"]["i_core_ua"] or 1e9) if good else mine[-1]
        idx = int(w[1:]) if w[1:].isdigit() else 0
        focus = plans[0]["focus"][idx] if plans and idx < len(plans[0]["focus"]) else w
        wcards.append({"id": w, "idx": idx, "focus": focus, "n": len(mine), "n_pass": len(good),
                       "rep_n": rep["n"], "rep": _short(rep["rationale"], 24),
                       "best_ua": rep["worst"]["i_core_ua"] if good else None})

    cost = llm_cost(calls)
    comp = comparison(tag)
    print(f"  computing figures …", flush=True)
    figs = figures(pub, final)
    recheck = summary.get("recheck_full_lib")
    recheck_guard = "guard_band" in summary

    sizes = {r: {"before": _fmt_size(pub["params"], r), "after": _fmt_size(final["params"], r)}
             for r in ROLE_TEXT}
    pp, fp = oa.clamp(pub["params"]), oa.clamp(final["params"])
    extra = [("Cc", "Cc + Rz", "keep the feedback loop stable", _eng(pp["Cc"], "F") + " · " + _eng(pp["Rz"], "Ω"),
              _eng(fp["Cc"], "F") + " · " + _eng(fp["Rz"], "Ω")),
             ("Ibias", "Bias current", "reference for both stages", _eng(pp["Ibias"], "A"), _eng(fp["Ibias"], "A"))]

    nominal, target = oa.DEFAULT_SPEC, oa.guarded(oa.DEFAULT_SPEC)
    data = {
        "run": run_dir.name, "family": family, "yard": yard,
        "cands": [{k: c[k] for k in ("n", "iter", "worker", "rationale", "pts", "worst", "pass_target",
                                      "pass_spec", "n_spec", "n_target")} for c in cands],
        "pub": cands.index(pub), "final": cands.index(final),
        "first": cands.index(first) if first else None,
        "plans": plans, "workers": wcards,
        "spec": {"pm": [nominal["pm_deg"][1], target["pm_deg"][1]],
                 "ugf": [nominal["ugf_hz"][1] / 1e6, target["ugf_hz"][1] / 1e6],
                 "gain": [nominal["dc_gain_db"][1], target["dc_gain_db"][1]],
                 "cur": [nominal["i_core_a"][1] * 1e6, target["i_core_a"][1] * 1e6],
                 "slack": [0.0, oa.SAT_GUARD_V * 1e3]},
        "corners": [[c, CORNER_NAME[c]] for c in oa.CORNERS], "temps": list(oa.TEMPS),
    }
    ctx = dict(data=data, summary=summary, cost=cost, comp=comp, figs=figs, recheck=recheck,
               recheck_guard=recheck_guard, sizes=sizes, extra=extra, pub=pub, final=final, first=first,
               plans=plans, wcards=wcards, yard=yard, n=len(cands), tag=tag)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(ctx))
    print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB) [{time.perf_counter() - t0:.1f} s]")
    return out


# --------------------------------------------------------------------------- HTML

GLOSS = {
    "pm": ("Phase margin", "How far the feedback loop is from ringing or oscillating. More degrees = calmer."),
    "ugf": ("Unity-gain frequency (UGF)", "How fast the amplifier responds: the frequency where its gain drops to 1×."),
    "gain": ("DC gain", "How strongly it amplifies slow signals. More dB = more accurate output."),
    "pvt": ("PVT", "Process, voltage, temperature: the conditions a real chip can end up in."),
    "corner": ("Process corner", "A possible fab outcome: transistors come out typical, fast or slow (N and P types can differ)."),
    "sat": ("Saturation", "The region where a transistor works as a good amplifier. Too little voltage headroom "
                          "and it drops out: gain collapses."),
    "gm": ("gm (transconductance)", "How much a transistor's current changes per volt at its input: its 'strength'."),
    "guard": ("Guard band", "Extra safety margin on top of the datasheet spec, to cover model error, layout "
                            "parasitics and chip-to-chip spread."),
    "spice": ("SPICE", "The industry-standard circuit simulator; here ngspice with the open SkyWater 130 nm models."),
    "icore": ("Core current", "Supply current of the amplifier's two stages: what the battery pays for."),
}


def g(key: str, text: str | None = None) -> str:
    title, body = GLOSS[key]
    return (f'<span class="g" tabindex="0">{E(text or title)}<span class="tip"><b>{E(title)}</b>'
            f'{E(body)}</span></span>')


def _num(v, f="{:.0f}"):
    return "—" if v is None else f.format(v)


def render(ctx: dict) -> str:
    d, s, figs = ctx["data"], ctx["summary"], ctx["figs"]
    pub, fin, first = ctx["pub"], ctx["final"], ctx["first"]
    n = ctx["n"]
    pub_fail = sum(1 for p in pub["pts"] if p[0] == 2)
    fin_ok = fin["n_target"] if ctx["yard"] == "target" else fin["n_spec"]
    ratio = (pub["worst"]["i_core_ua"] or 0) / (fin["worst"]["i_core_ua"] or 1)
    pub_why = []
    for p in pub["pts"]:
        for w in p[1]:
            if p[0] == 2 and w not in pub_why:
                pub_why.append(w)
    with_margin = ctx["yard"] == "target"
    wall = s.get("wall_s")
    decks = s.get("decks") or n * 15
    cost = ctx["cost"]

    # ---------------- scene 1: hook
    chips_bad = "".join(f'<span class="chip bad">{E(w)}</span>' for w in pub_why)
    s1 = f"""
<div class="kicker">Gemini 4 agent swarm · analog chip design</div>
<h1 class="hook">An amplifier must work on <em>every</em> chip the factory makes.</h1>
<p class="lede">Fast or slow silicon, −40 °C to 125 °C: {g('pvt', '15 combinations')}. All must pass.</p>
<div class="ba">
  <div class="bacard before">
    <div class="who">Published design</div>
    <div class="huge bad">{15 - pub_fail}<span class="of">/15</span></div>
    <div class="what">corners pass</div>
    <div class="chips"><span class="chip lbl">fails</span>{chips_bad}</div>
    <div class="cur"><b>{_num(pub['worst']['i_core_ua'])} µA</b> {g('icore', 'current')}</div>
  </div>
  <div class="arrow">→</div>
  <div class="bacard after">
    <div class="who">After the Gemini swarm</div>
    <div class="huge good">{fin_ok}<span class="of">/15</span></div>
    <div class="what">corners pass{' <span class="chip guard">with ' + g('guard', 'safety margin') + '</span>' if with_margin else ' <span class="chip">datasheet spec</span>'}</div>
    <div class="chips"><span class="chip lbl">passes</span><span class="chip good">stability</span><span class="chip good">speed</span><span class="chip good">accuracy</span><span class="chip good">power</span><span class="chip good">headroom</span></div>
    <div class="cur"><b>{_num(fin['worst']['i_core_ua'], '{:.1f}')} µA</b> · <b class="good">{ratio:.1f}× less current</b></div>
  </div>
</div>
<div class="foot">{n} designs tried · {decks} {g('spice', 'SPICE')} simulations{f' · {wall / 60:.0f} minutes' if wall else ''}{f' · ≈ ${cost["usd"]:.2f} of LLM calls' if cost['calls'] else ''}</div>
"""
    # ---------------- scene 2: corner grids
    s2 = f"""
<h2>15 ways a chip can come out</h2>
<p class="lede">Rows: how the silicon turned out ({g('corner', 'process corner')}). Columns: temperature.</p>
<div class="grids">
  <div class="gridwrap"><div class="gtitle">Published design</div><div class="cgrid" data-cand="{d['pub']}"></div></div>
  <div class="gridwrap"><div class="gtitle">Gemini swarm · design #{fin['n']}</div><div class="cgrid" data-cand="{d['final']}"></div></div>
</div>
<div class="legend">
  <span><i class="sw ok"></i>passes with margin</span>
  <span><i class="sw gb"></i>passes, but inside the {g('guard', 'guard band')}</span>
  <span><i class="sw ko"></i>fails — word says why</span>
</div>
<div class="fn">Corner labels as in the SkyWater 130 PDK; in this PDK build sf/fs behave swapped relative to their names.</div>
"""
    # ---------------- scene 3: gauges
    s3 = f"""
<h2>Five requirements, worst case over all 15 corners</h2>
<div class="gauges" data-before="{d['pub']}" data-after="{d['final']}"></div>
<div class="legend">
  <span><i class="mk pubm"></i>published</span>
  <span><i class="mk finm"></i>Gemini swarm</span>
  <span><i class="sw kz"></i>fails spec</span>
  <span><i class="sw gz"></i>{g('guard', 'guard band')} (extra safety margin)</span>
  <span><b class="s1t">!</b>&nbsp;passes spec, but inside the guard band</span>
</div>
"""
    # ---------------- scene 4: circuit
    cards = ""
    from viz.common import ROLE_COLOR
    for r, (name, desc) in ROLE_TEXT.items():
        sz = ctx["sizes"][r]
        same = sz["before"] == sz["after"]
        cards += (f'<div class="role"><i class="rsw" style="background:{ROLE_COLOR[r]}"></i>'
                  f'<div class="rtop"><span class="rname">{E(name)}</span>'
                  f'<span class="rsize">{"" if same else f"<s>{E(sz["before"])}</s> → "}<b>{E(sz["after"])}</b></span></div>'
                  f'<div class="rdesc">{E(desc)}</div></div>')
    for key, name, desc, b, a in ctx["extra"]:
        cards += (f'<div class="role"><i class="rsw" style="background:#9a988f"></i>'
                  f'<div class="rtop"><span class="rname">{E(name)}</span>'
                  f'<span class="rsize">{"" if a == b else f"<s>{E(b)}</s> → "}<b>{E(a)}</b></span></div>'
                  f'<div class="rdesc">{E(desc)}</div></div>')
    s4 = f"""
<h2>The circuit: 11 knobs on a two-stage amplifier</h2>
<p class="lede">The agents size every transistor (width/length, µm) and the compensation parts. Published → Gemini:</p>
<div class="circ"><div class="sch">{figs['schematic']}</div><div class="roles">{cards}</div></div>
"""
    # ---------------- scene 5: agents
    plan0 = ctx["plans"][0] if ctx["plans"] else {"model": "gemini-4-carbon", "latency_s": None,
                                                  "diagnosis": "(no architect log)", "focus": []}
    replans = len(ctx["plans"]) - 1
    wc = ""
    for w in ctx["wcards"]:
        wc += (f'<div class="wcard"><div class="wtop"><span class="badge h">Hydrogen {w["idx"] + 1}</span>'
               f'<span class="wstat">{w["n_pass"]}/{w["n"]} pass</span></div>'
               f'<div class="wfocus">{E(w["focus"])}</div>'
               f'<div class="wrat">“{E(w["rep"])}”<span class="wref"> — design #{w["rep_n"]}</span></div></div>')
    lat = plan0.get("latency_s")
    s5 = f"""
<h2>How the agents worked</h2>
<div class="team">
  <div class="arch">
    <div class="wtop"><span class="badge c">{E(_model_short(plan0['model']))} · architect</span>
      <span class="lat">{E(plan0['model'])}{f' · thought for {lat:.0f} s' if lat else ''}{f' · re-planned {replans}×' if replans > 0 else ''}</span></div>
    <div class="diag">“{E(plan0['diagnosis'])}”</div>
    <div class="adesc">Reads the failures, then gives each worker a different direction.</div>
  </div>
  <div class="flow">↓ one search direction each ↓</div>
  <div class="workers">{wc}</div>
  <div class="flow">↓ {len(ctx['wcards']) * 2} designs per round ↓</div>
  <div class="judge"><b>{g('spice', 'SPICE')} judge</b> simulates every design at all 15 corners. The LLMs never grade themselves.</div>
  <div class="flow">↺ results go back to everyone{'; Carbon re-plans when progress stalls' if True else ''}</div>
</div>
"""
    s6 = f"""
<h2>Watch the search</h2>
<div class="play">
  <div class="pleft">
    <svg class="prog" viewBox="0 0 1060 640"></svg>
    <div class="ctrl"><button class="pbtn" type="button" aria-label="play">▶</button>
      <input class="slider" type="range" min="0" max="{n - 1}" value="0" aria-label="candidate">
      <span class="ccount"></span></div>
  </div>
  <div class="pright">
    <div class="mtitle">Corners</div><div class="cgrid mini" data-live="1"></div>
    <div class="mtitle">Worst case</div><div class="gauges mini" data-live="1"></div>
  </div>
</div>
<div class="caption"></div>
"""
    # ---------------- scene 7: baselines
    rows = ""
    for m in ctx["comp"]:
        hero = m["key"] == "swarm"
        if m["done"] == 0:
            prog = (f' ({", ".join(f"{p}/40" for p in m["running"])} designs)' if m["running"] else "")
            rows += (f'<div class="mrow{" hero" if hero else ""}"><div class="mname">{E(m["name"])}<small>{E(m["sub"])}</small></div>'
                     f'<div class="mval run" style="grid-column: span 3">running…{E(prog)}</div></div>')
            continue
        dots = "".join(f'<i class="dot {"y" if i < m["k"] else "n"}"></i>' for i in range(m["done"]))
        dots += "".join('<i class="dot p"></i>' for _ in range(m["expected"] - m["done"]))
        pending = m["expected"] - m["done"]
        rows += (f'<div class="mrow{" hero" if hero else ""}"><div class="mname">{E(m["name"])}<small>{E(m["sub"])}</small></div>'
                 f'<div class="mval"><span class="dots">{dots}</span><b>{m["k"]}/{m["done"]}</b>'
                 f'{f"<small>{pending} pending</small>" if pending else ""}</div>'
                 f'<div class="mval">{"<b>never</b>" if m["median_first"] is None else f"<b>{m["median_first"]:g}</b><small>designs</small>"}</div>'
                 f'<div class="mval">{"—" if m["best_ua"] is None else f"<b>{m["best_ua"]:.1f}</b><small>µA</small>"}</div></div>')
    sw = next(m for m in ctx["comp"] if m["key"] == "swarm")
    per_run = statistics.mean(sw["cost_usd"]) if sw["cost_usd"] else cost["usd"]
    hy = cost["by_model"].get("gemini-4-hydrogen", {})
    ca = cost["by_model"].get("gemini-4-carbon", {})
    spec_word = "guard-banded target" if ctx["tag"] == "guard" else "datasheet spec"
    s7 = f"""
<h2>Did it beat the classic optimizers?</h2>
<p class="lede">Same budget for everyone: 40 designs = 600 SPICE runs per attempt. Goal: pass the {spec_word} at all 15 corners.</p>
<div class="mtable">
  <div class="mrow head"><div></div><div>attempts that found a passing design</div><div>median designs until the first pass</div><div>lowest current found (lower is better)</div></div>
  {rows}
</div>
<div class="cost">This run: <b>{cost['calls']}</b> LLM calls ({ca.get('calls', 0)} Carbon, {hy.get('calls', 0)} Hydrogen) ·
  <b>{cost['tokens'] / 1e3:.0f}k</b> tokens · <b>≈ ${cost['usd']:.2f}</b>{f' (mean ${per_run:.2f} per swarm attempt)' if len(sw['cost_usd']) > 1 else ''}</div>
<div class="fn">Prices: Hydrogen $1.50 / $7.50 and Carbon $2 / $10 per million input / output tokens; thinking tokens billed as output.</div>
"""
    # ---------------- scene 8: proof
    rc = ctx["recheck"]
    rc_html = ("<span class='bigcheck good'>✓</span> passes" if rc else
               "<span class='bigcheck bad'>✗</span> does not pass" if rc is False else
               "<span class='bigcheck muted'>…</span> not run yet")
    s8 = f"""
<h2>Proof: the loop is stable at every corner</h2>
<div class="proof">
  <div class="bode">{figs['bode']}</div>
  <div class="pcards">
    <div class="pcard"><div class="plabel">Independently re-verified on the full SkyWater PDK library</div>
      <div class="pval">{rc_html}</div>
      <div class="psub">{'guard-banded target' if ctx['recheck_guard'] else 'datasheet spec'}, all 15 corners, slow full-model simulation</div></div>
    <div class="pcard"><div class="plabel">Worst-corner {g('pm', 'phase margin')}</div>
      <div class="pval"><span class="muted">{_num(pub['worst']['pm_deg'])}°</span> → <span class="good">{_num(fin['worst']['pm_deg'], '{:.1f}')}°</span></div>
      <div class="psub">published design rings; Gemini's settles cleanly</div></div>
    <div class="pcard"><div class="plabel">How to read it</div><div class="psub">Colored: Gemini design at 15 corners. Gray: published. Dots: where the gain falls to 1×; the phase there decides stability.</div></div>
  </div>
</div>
"""
    scenes = [("Hook", s1), ("Corners", s2), ("Requirements", s3), ("Circuit", s4), ("Agents", s5),
              ("Playback", s6), ("Baselines", s7), ("Proof", s8)]
    body = "".join(f'<section class="scene" id="s{i + 1}" data-title="{E(t)}"><div class="stage">{c}'
                   f'<div class="pageno">{i + 1}/{len(scenes)}</div></div></section>'
                   for i, (t, c) in enumerate(scenes))
    css = (HERE / "story.css").read_text()
    js = (HERE / "story.js").read_text()
    blob = json.dumps(d, separators=(",", ":")).replace("</", "<\\/")
    title = f"Gemini swarm · {d['run']}"
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'><title>{E(title)}</title>"
            f"<style>{css}</style></head><body>{body}"
            f"<nav class='dots' aria-label='scenes'><button class='nav prev' type='button' aria-label='previous scene'>▲</button>"
            f"<button class='nav next' type='button' aria-label='next scene'>▼</button></nav>"
            f"<script id='data' type='application/json'>{blob}</script><script>{js}</script></body></html>")


# --------------------------------------------------------------------------- CLI

def pick_hero(tag: str = "guard") -> Path | None:
    glob = "guard_s*" if tag == "guard" else "seed*"
    best = None
    for dpath in sorted(SWARM_ROOT.glob(glob)):
        r = run_result(dpath)
        if not r.get("done") or not r.get("success"):
            continue
        key = (r.get("recheck") is not True, r.get("best_ua") or 1e9, r.get("first") or 99)
        if best is None or key < best[0]:
            best = (key, dpath)
    return best[1] if best else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g_ = ap.add_mutually_exclusive_group(required=True)
    g_.add_argument("--run", type=Path, help="swarm run directory (runs/swarm/<id>)")
    g_.add_argument("--hero", action="store_true", help="pick the best finished run of the --baselines family")
    ap.add_argument("--baselines", choices=("guard", "default"),
                    help="which comparison family to show (default: the run's own)")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    if a.hero:
        run = pick_hero(a.baselines or "guard")
        if run is None:
            raise SystemExit("no finished successful run to use as hero")
        print(f"hero run: {run.name}")
        build(run, a.out or VIZ_ROOT / "story_hero.html", a.baselines or "guard")
    else:
        build(a.run, a.out or VIZ_ROOT / f"story_{a.run.name}.html", a.baselines)


if __name__ == "__main__":
    main()
