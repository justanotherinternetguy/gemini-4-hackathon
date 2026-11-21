"""Build the presenter briefing PDF from template.html + the latest run files.

    python -m docs.build_report            # or: python docs/report/build.py
    python docs/report/build.py --html-only   # skip the PDF (for quick previews)
    python docs/report/build.py --pages        # also render page PNGs to docs/report/pages/

Every number in the results sections is read from runs/ at build time; anything missing is
shown as "pending". Held-out runs (runs/swarm/heldout_*, runs/opamp_baselines/heldout_*) and
the sign-off comparison (runs/signoff/*/comparison.json) fill in automatically when present.
Needs only the standard library + a Chromium binary (and pdftoppm for --pages). No API calls.
"""
from __future__ import annotations

import argparse
import base64
import glob
import html
import json
import math
import re
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RUNS = ROOT / "runs"
OUT_PDF = HERE / "gemini_analog_sizing_briefing.pdf"
OUT_HTML = HERE / "briefing.html"
E = html.escape

BUDGET = 40                       # candidates per run (x 15 PVT points = 600 decks)
SAMPLERS = ("tpe", "gp", "random")
SEEDS = (0, 1, 2)
# $ per 1M tokens (introductory pricing, research_notes/.../gemini4_apis.md). Thought tokens
# are billed as output tokens (assumption, stated in the report).
PRICE = {"gemini-4-carbon": (2.00, 10.00), "gemini-4-hydrogen": (1.50, 7.50)}
COLORS = {"swarm": "#2a78d6", "tpe": "#eb6834", "gp": "#1baf7a", "random": "#eda100"}
NAMES = {"swarm": "Gemini swarm", "tpe": "Optuna TPE", "gp": "Optuna GP-BO", "random": "Random search"}


# --------------------------------------------------------------------------- helpers

def jload(p: Path):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return None


def jsonl(p: Path) -> list:
    out = []
    try:
        for line in Path(p).read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except FileNotFoundError:
        pass
    return out


def fnum(x, fmt="{:.1f}", none="–"):
    return none if x is None else fmt.format(x)


def median_ftp(vals: list, budget: int = BUDGET) -> str:
    """Median candidates-to-first-pass with failures counted as > budget."""
    if not vals:
        return "pending"
    xs = sorted(v if v is not None else math.inf for v in vals)
    m = statistics.median(xs)
    return f"&gt;{budget}" if math.isinf(m) else f"{m:g}"


def ftp_list(vals: list) -> str:
    return ", ".join("–" if v is None else str(v) for v in vals) if vals else "pending"


def img_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def first_existing(*paths) -> Path | None:
    for p in paths:
        p = Path(p)
        if p.exists():
            return p
    return None


def svg_inline(name: str) -> str:
    s = (HERE / "svg" / f"{name}.svg").read_text()
    return s[s.index("<svg"):]


def run_cost(run_dir: Path) -> dict:
    per = {}
    for r in jsonl(run_dir / "llm_calls.jsonl"):
        m = r.get("model", "?")
        d = per.setdefault(m, {"calls": 0, "errors": 0, "in": 0, "out": 0, "thought": 0, "latency": []})
        if "error" in r:
            d["errors"] += 1
            continue
        d["calls"] += 1
        d["in"] += r.get("in") or 0
        d["out"] += r.get("out") or 0
        d["thought"] += r.get("thought") or 0
        d["latency"].append(r.get("latency_s") or 0)
    usd = 0.0
    for m, d in per.items():
        pi, po = PRICE.get(m, (0, 0))
        usd += d["in"] / 1e6 * pi + (d["out"] + d["thought"]) / 1e6 * po
    return {"per_model": per, "usd": usd}


# --------------------------------------------------------------------------- data

def load_swarm() -> dict:
    runs = {}
    for p in sorted((RUNS / "swarm").glob("*/summary.json")):
        rid = p.parent.name
        if rid.startswith("_") or rid.startswith("heldout"):
            continue
        s = jload(p)
        if not s or "success" not in s:
            continue
        s["guard"] = "guard_band" in s
        s["cost"] = run_cost(p.parent)
        hist = jsonl(p.parent / "history.jsonl")
        s["hist"] = hist
        override = jload(HERE / "data" / f"{rid}_recheck.json")
        s["recheck_override"] = override
        runs[rid] = s
    return runs


def load_opamp_baselines() -> dict:
    out = {}
    for tag in ("default", "guard"):
        for smp in SAMPLERS:
            for seed in SEEDS:
                p = RUNS / "opamp_baselines" / f"{tag}_{smp}_s{seed}_c{BUDGET}.json"
                d = jload(p)
                out[(tag, smp, seed)] = d["summary"] if d and "summary" in d else None
    return out


def tt_vs_all(runs: dict) -> dict:
    """Among guard-band swarm candidates: how many pass at tt/27 alone vs all 15 points."""
    n = tt = allp = 0
    for s in runs.values():
        if not s["guard"]:
            continue
        for h in s["hist"]:
            pts = h.get("points")
            if not pts:
                continue
            n += 1
            t = [p for p in pts if p["corner"] == "tt" and int(p["temp"]) == 27]
            tt += bool(t and t[0]["pass"])
            allp += all(p["pass"] for p in pts)
    return {"n": n, "tt": tt, "all": allp}


# --------------------------------------------------------------------------- tables

def group_row(label: str, color: str, entries: list, guard: bool, note: str = "") -> str:
    """entries: list of dicts with success, ftp, icore, recheck (None = pending)."""
    done = [e for e in entries if e is not None]
    n_exp = len(entries)
    k = sum(1 for e in done if e["success"])
    ftps = [e["ftp"] for e in done]
    icores = [e["icore"] for e in done if e["success"] and e["icore"] is not None]
    rech = [e["recheck"] for e in done if e["success"]]
    pending = n_exp - len(done)
    succ = f"{k}/{len(done)}" if done else "–"
    if pending:
        succ += f" <span class='muted'>(+{pending} pending)</span>"
    best = f"{min(icores):.1f}" if icores else "–"
    med = f"{statistics.median(icores):.1f}" if icores else "–"
    rc = f"{sum(1 for r in rech if r)}/{len(rech)}" if rech else "–"
    cls = "pass" if k else ("fail" if done else "pend")
    sw = f"<span style='display:inline-block;width:8px;height:8px;border-radius:2px;background:{color};margin-right:5px'></span>"
    return (f"<tr><td>{sw}{label}{note}</td><td class='n {cls}'>{succ}</td><td class='n'>{ftp_list(ftps) if done else 'pending'}</td>"
            f"<td class='n'>{median_ftp(ftps) if done else 'pending'}</td><td class='n'>{best}</td><td class='n'>{med}</td><td class='n'>{rc}</td></tr>")


def swarm_entry(s: dict) -> dict:
    rc = s.get("recheck_full_lib")
    ov = s.get("recheck_override")
    if rc is False and ov and ov.get("passed_full_lib_nominal_spec"):
        rc = True
    return {"success": s["success"], "ftp": s.get("candidates_to_first_pass"),
            "icore": s.get("best_i_core_uA"), "recheck": rc}


def base_entry(b: dict | None) -> dict | None:
    if b is None:
        return None
    return {"success": b.get("success"), "ftp": b.get("candidates_to_first_pass"),
            "icore": b.get("best_i_core_uA"), "recheck": b.get("recheck_full_lib")}


def results_table(runs: dict, base: dict) -> str:
    head = ("<table><thead><tr><th>Method</th><th class='n'>Runs passing<br>all 15 points</th>"
            "<th class='n'>Candidates to<br>first pass, per run</th><th class='n'>Median</th>"
            "<th class='n'>Best core<br>current (µA)</th><th class='n'>Median best<br>current (µA)</th>"
            "<th class='n'>Full-library<br>re-check</th></tr></thead><tbody>")
    rows = []
    for tag, guard, title in (("default", False, "Bare spec (PM ≥ 60°, UGF ≥ 10 MHz, gain ≥ 60 dB, core current ≤ 100 µA)"),
                              ("guard", True, "Guard-banded target (PM ≥ 65°, UGF ≥ 11 MHz, gain ≥ 63 dB, ≤ 90 µA, +10 mV saturation)")):
        rows.append(f"<tr class='group'><td colspan='7'>{title}</td></tr>")
        sw = [swarm_entry(s) for s in runs.values() if s["guard"] == guard]
        note = " <span class='muted'>(incl. first40)</span>" if not guard and "first40" in runs else ""
        rows.append(group_row(NAMES["swarm"], COLORS["swarm"], sw, guard, note))
        for smp in SAMPLERS:
            ents = [base_entry(base[(tag, smp, sd)]) for sd in SEEDS]
            rows.append(group_row(NAMES[smp], COLORS[smp], ents, guard))
    return head + "".join(rows) + "</tbody></table>"


def run_table(runs: dict) -> str:
    head = ("<table class='tight'><thead><tr><th>Run</th><th>Guard</th><th class='n'>First<br>pass</th>"
            "<th class='n'>Passing<br>candidates</th><th class='n'>Best core<br>current (µA)</th><th>Full-lib<br>re-check</th>"
            "<th class='n'>LLM<br>calls</th><th class='n'>Tokens in / out+thought</th><th class='n'>Cost<br>(USD)</th>"
            "<th class='n'>Carbon plan<br>latency (s)</th><th class='n'>Wall<br>(s)</th></tr></thead><tbody>")
    rows = []
    order = sorted(runs, key=lambda r: (runs[r]["guard"], r != "first40", r))
    for rid in order:
        s = runs[rid]
        hist = s["hist"]
        npass = sum(1 for h in hist if h.get("passed"))
        rc = s.get("recheck_full_lib")
        ov = s.get("recheck_override")
        if rc is True:
            rct = "<td class='pass'>pass</td>"
        elif rc is False and ov and ov.get("passed_full_lib_nominal_spec"):
            rct = "<td class='pass'>pass*</td>"
        elif rc is False:
            rct = "<td class='fail'>fail</td>"
        else:
            rct = "<td class='pend'>–</td>"
        c = s["cost"]
        toks_in = sum(d["in"] for d in c["per_model"].values())
        toks_out = sum(d["out"] + d["thought"] for d in c["per_model"].values())
        arch = s.get("architect_calls") or []
        if arch:
            a0 = arch[0]
            lat = f"{a0['latency_s']:.0f}" + (" <span class='muted'>(fallback)</span>" if a0.get("model") != "gemini-4-carbon" else "")
        else:
            carb = c["per_model"].get("gemini-4-carbon")
            lat = f"{carb['latency'][0]:.0f}" if carb and carb["latency"] else "–"
        rows.append(
            f"<tr><td class='mono'>{E(rid)}</td><td>{'yes' if s['guard'] else 'no'}</td>"
            f"<td class='n'>{fnum(s.get('candidates_to_first_pass'), '{}')}</td><td class='n'>{npass}/{len(hist)}</td>"
            f"<td class='n'>{fnum(s.get('best_i_core_uA'))}</td>{rct}<td class='n'>{s.get('llm_calls', '–')}</td>"
            f"<td class='n'>{toks_in/1e3:.0f}k / {toks_out/1e3:.0f}k</td><td class='n'>{c['usd']:.2f}</td>"
            f"<td class='n'>{lat}</td><td class='n'>{fnum(s.get('wall_s'), '{:.0f}')}</td></tr>")
    return head + "".join(rows) + "</tbody></table>"


def ftp_chart(runs: dict, base: dict) -> str:
    """Dot strip: candidates to first all-corner pass, one dot per run; misses in a 'no pass' lane."""
    rows = []
    for guard, tag, gl in ((False, "default", "bare spec"), (True, "guard", "guard band")):
        rows.append(("swarm", gl, [swarm_entry(s) for s in runs.values() if s["guard"] == guard]))
        for smp in SAMPLERS:
            ents = [base_entry(base[(tag, smp, sd)]) for sd in SEEDS]
            rows.append((smp, gl, ents))
    W, LM, RM, top, rh = 700, 175, 92, 26, 21
    pw = W - LM - RM
    def X(v): return LM + (v - 1) / (BUDGET - 1) * pw
    H = top + rh * len(rows) + 36
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Noto Sans, DejaVu Sans, sans-serif">']
    for v in (1, 10, 20, 30, 40):
        s.append(f'<line x1="{X(v):.1f}" y1="{top-6}" x2="{X(v):.1f}" y2="{top + rh*len(rows)}" stroke="#e4e2da" stroke-width="0.8"/>')
        s.append(f'<text x="{X(v):.1f}" y="{top + rh*len(rows) + 13}" font-size="9.5" fill="#4a4945" text-anchor="middle">{v}</text>')
    nx = W - RM + 46
    s.append(f'<rect x="{W-RM+14}" y="{top-6}" width="64" height="{rh*len(rows)+6}" fill="#fbe9e7"/>')
    s.append(f'<text x="{nx}" y="{top-10}" font-size="9" fill="#b3261e" text-anchor="middle" font-weight="700">no pass in 40</text>')
    s.append(f'<text x="{LM + pw/2:.0f}" y="{top + rh*len(rows) + 29}" font-size="10" fill="#1b1b1a" text-anchor="middle" font-weight="700">candidates evaluated before the first design that passes at all 15 PVT points (40 = 600 simulations)</text>')
    for i, (meth, gl, ents) in enumerate(rows):
        y = top + rh * i + rh / 2
        if i == 4:
            s.append(f'<line x1="8" y1="{top + rh*4}" x2="{W-8}" y2="{top + rh*4}" stroke="#9a988f" stroke-width="0.9"/>')
        s.append(f'<text x="{LM-10}" y="{y+3.5:.1f}" font-size="10" fill="#1b1b1a" text-anchor="end">{NAMES[meth]} · <tspan fill="#77766f">{gl}</tspan></text>')
        s.append(f'<line x1="{LM}" y1="{y:.1f}" x2="{W-RM}" y2="{y:.1f}" stroke="#d9d7cf" stroke-width="0.8"/>')
        misses, pend = 0, 0
        for e in ents:
            if e is None:
                pend += 1
            elif e["success"] and e["ftp"]:
                s.append(f'<circle cx="{X(e["ftp"]):.1f}" cy="{y:.1f}" r="5" fill="{COLORS[meth]}" stroke="#fff" stroke-width="1.6"/>')
            else:
                misses += 1
        for j in range(misses):
            s.append(f'<circle cx="{nx - 16 + j*12:.1f}" cy="{y:.1f}" r="4.6" fill="#fff" stroke="{COLORS[meth]}" stroke-width="1.8"/>')
        if pend:
            s.append(f'<text x="{nx - 20 + misses*12:.1f}" y="{y+3.5:.1f}" font-size="8.5" fill="#77766f">+{pend} pending</text>')
    s.append("</svg>")
    return "".join(s)


def ams_table() -> tuple[str, dict]:
    g = {}
    for p in sorted((RUNS / "baselines").glob("*_b100_tt.json")):
        d = jload(p)
        if not d or "summary" not in d:
            continue
        s = d["summary"]
        g.setdefault(s["circuit"], {}).setdefault(s["sampler"], []).append(s)
    if not g:
        return "<p class='muted'>No AMS-SizingBench baseline files found.</p>", {"k": 0, "n": 0}
    head = ("<table class='tight'><thead><tr><th>Circuit</th><th>Spec (benchmark's own YAML)</th>"
            + "".join(f"<th class='n'>{NAMES[s]}<br>passing / first pass</th>" for s in SAMPLERS) + "</tr></thead><tbody>")
    rows, K, N = [], 0, 0
    for c in sorted(g):
        spec = next(iter(g[c].values()))[0]["spec"]
        cells = []
        for smp in SAMPLERS:
            L = sorted(g[c].get(smp, []), key=lambda s: s["seed"])
            k = sum(1 for s in L if s["success"])
            K += k
            N += len(L)
            cells.append(f"<td class='n'>{k}/{len(L)} · {ftp_list([s.get('sims_to_first_pass') for s in L])}</td>")
        rows.append(f"<tr><td class='mono'>{E(c)}</td><td class='small'>{E(spec)}</td>{''.join(cells)}</tr>")
    return head + "".join(rows) + "</tbody></table>", {"k": K, "n": N}


def signoff_block() -> tuple[str, str]:
    comps = sorted(RUNS.glob("signoff/*/comparison.json"))
    started = sorted(p.parent.name for p in RUNS.glob("signoff/*/task.txt"))
    if not comps:
        msg = (f"Run{'s' if len(started) != 1 else ''} started: {', '.join(started)}." if started else "No run started yet.")
        return ("<div class='callout pending'><div class='callout-title'><span class='tag pending'>pending</span> Sign-off agent result</div>"
                f"<p>{E(msg)} The agent had not returned a verdict when this PDF was built. Rebuild the PDF after "
                "<code>runs/signoff/&lt;id&gt;/comparison.json</code> appears; this box then turns into a table that "
                "compares the agent's numbers with ours, point by point.</p></div>"), "pending"
    out = []
    status_words = []
    for p in comps:
        c = jload(p) or {}
        rid = p.parent.name
        cal = c.get("calibration") or {}
        agree = c.get("verdict_agrees")
        status_words.append(f"{rid}: {'agrees' if agree else 'disagrees' if agree is False else 'no verdict'}")
        rows = [
            ("Agent status", E(str(c.get("status")))),
            ("Calibration on published design (tt, 27 °C)",
             f"{fnum(cal.get('dc_gain_db'))} dB, {fnum((cal.get('ugf_hz') or 0)/1e6 if cal.get('ugf_hz') else None)} MHz, "
             f"{fnum(cal.get('pm_deg'))}° (published 60.6 dB, 126 MHz, 31.5°); matches: {E(str(cal.get('matches_published')))}"),
            ("PVT points reported", f"{c.get('points_matched', '–')}/{c.get('points_total', '–')}"),
            ("Largest disagreement vs our evaluator",
             f"gain {fnum(c.get('max_abs_dgain_db'), '{:.2f}')} dB, PM {fnum(c.get('max_abs_dpm_deg'), '{:.2f}')}°, "
             f"UGF {fnum((c.get('max_rel_dugf') or 0)*100 if c.get('max_rel_dugf') is not None else None, '{:.1f}')} %"),
            ("Verdict: agent vs ours (spec / target)",
             f"{c.get('agent_pass_spec')} / {c.get('agent_pass_target')} vs {c.get('ours_pass_spec')} / {c.get('ours_pass_target')}"
             f" → <b>{'agree' if agree else 'disagree' if agree is False else 'n/a'}</b>"),
            ("SPICE calls / decks / wall time", f"{c.get('spice_calls', '–')} / {c.get('decks', '–')} / {fnum(c.get('wall_s'), '{:.0f}')} s"),
        ]
        out.append(f"<p class='tcap'><b>Sign-off run <span class='mono'>{E(rid)}</span></b></p><table class='def tight'>"
                   + "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in rows) + "</table>")
        if c.get("agent_verdict"):
            out.append(f"<blockquote>{E(str(c['agent_verdict']))}<span class='src'>Agent's verdict paragraph, verbatim.</span></blockquote>")
    return "".join(out), "; ".join(status_words)


def heldout_block() -> tuple[str, str]:
    sw = sorted((RUNS / "swarm").glob("heldout_*/summary.json"))
    bl = sorted((RUNS / "opamp_baselines").glob("heldout_*.json"))
    if not sw and not bl:
        return ("<div class='callout pending'><div class='callout-title'><span class='tag pending'>pending</span> Held-out op-amps</div>"
                "<p>No held-out result files existed when this PDF was built. The build script looks for "
                "<code>runs/swarm/heldout_*/summary.json</code> and <code>runs/opamp_baselines/heldout_*.json</code> "
                "and fills this section with one row per run. Rebuild with <code>python -m docs.build_report</code>.</p></div>"), "pending"
    head = ("<table class='tight'><thead><tr><th>Run file</th><th>Method</th><th class='n'>Pass all 15</th><th class='n'>First pass</th>"
            "<th class='n'>Best core current (µA)</th><th>Full-lib re-check</th></tr></thead><tbody>")
    rows = []
    for p in sw:
        s = jload(p) or {}
        rows.append(f"<tr><td class='mono'>{E(p.parent.name)}</td><td>{NAMES['swarm']}</td>"
                    f"<td class='n {'pass' if s.get('success') else 'fail'}'>{'yes' if s.get('success') else 'no'}</td>"
                    f"<td class='n'>{fnum(s.get('candidates_to_first_pass'), '{}')}</td><td class='n'>{fnum(s.get('best_i_core_uA'))}</td>"
                    f"<td>{s.get('recheck_full_lib', '–')}</td></tr>")
    for p in bl:
        d = jload(p) or {}
        s = d.get("summary", d)
        rows.append(f"<tr><td class='mono'>{E(p.stem)}</td><td>{E(NAMES.get(str(s.get('sampler')), str(s.get('sampler', '?'))))}</td>"
                    f"<td class='n {'pass' if s.get('success') else 'fail'}'>{'yes' if s.get('success') else 'no'}</td>"
                    f"<td class='n'>{fnum(s.get('candidates_to_first_pass'), '{}')}</td><td class='n'>{fnum(s.get('best_i_core_uA'))}</td>"
                    f"<td>{s.get('recheck_full_lib', '–')}</td></tr>")
    k_sw = sum(1 for p in sw if (jload(p) or {}).get("success"))
    return head + "".join(rows) + "</tbody></table>", f"{len(sw)} swarm run(s) ({k_sw} passed), {len(bl)} baseline file(s)"


# --------------------------------------------------------------------------- values

def build_values() -> tuple[dict, list]:
    V, pending = {}, []
    runs = load_swarm()
    base = load_opamp_baselines()

    ng = [s for s in runs.values() if not s["guard"]]
    gd = [s for s in runs.values() if s["guard"]]
    V["n_sw_ng"], V["k_sw_ng"] = str(len(ng)), str(sum(s["success"] for s in ng))
    V["n_sw_g"], V["k_sw_g"] = str(len(gd)), str(sum(s["success"] for s in gd))
    V["ftp_sw_ng"] = ftp_list([s.get("candidates_to_first_pass") for s in ng])
    V["ftp_sw_g"] = ftp_list([s.get("candidates_to_first_pass") for s in gd])
    V["med_ftp_sw_ng"] = median_ftp([s.get("candidates_to_first_pass") for s in ng])
    V["med_ftp_sw_g"] = median_ftp([s.get("candidates_to_first_pass") for s in gd])
    ic_ng = [s["best_i_core_uA"] for s in ng if s["success"]]
    ic_g = [s["best_i_core_uA"] for s in gd if s["success"]]
    V["icore_sw_ng_range"] = f"{min(ic_ng):.1f}–{max(ic_ng):.1f}" if ic_ng else "–"
    V["icore_sw_g_range"] = f"{min(ic_g):.1f}–{max(ic_g):.1f}" if ic_g else "–"
    V["icore_sw_ng_best"] = f"{min(ic_ng):.1f}" if ic_ng else "–"

    # baselines
    def bstats(tag):
        done = {k: v for k, v in base.items() if k[0] == tag and v is not None}
        miss = [f"{k[1]} seed {k[2]}" for k, v in base.items() if k[0] == tag and v is None]
        k = sum(1 for v in done.values() if v.get("success"))
        return done, miss, k
    d_done, d_miss, d_k = bstats("default")
    g_done, g_miss, g_k = bstats("guard")
    V["n_bl_ng"], V["k_bl_ng"] = str(len(d_done)), str(d_k)
    V["n_bl_g"], V["k_bl_g"] = str(len(g_done)), str(g_k)
    if d_miss:
        pending.append("bare-spec baselines: " + ", ".join(d_miss))
    if g_miss:
        pending.append("guard-band baselines: " + ", ".join(g_miss))
    V["bl_pending_note"] = ""
    if d_miss or g_miss:
        V["bl_pending_note"] = (f"<p class='small muted'>Still running when this PDF was built: "
                                f"{E(', '.join(['bare ' + m for m in d_miss] + ['guard ' + m for m in g_miss]))}. "
                                "Rebuild to fill them in.</p>")
    gp_wins = [(k, v) for k, v in d_done.items() if v.get("success")]
    if gp_wins:
        V["bl_win_note"] = "; ".join(
            f"{NAMES[k[1]]} seed {k[2]} passed at candidate {v.get('candidates_to_first_pass')} with {fnum(v.get('best_i_core_uA'))} µA"
            for k, v in gp_wins)
    else:
        V["bl_win_note"] = "none of the finished baseline runs passed"
    walls = {smp: [v["wall_s"] for k, v in base.items() if v and k[1] == smp and v.get("wall_s")] for smp in SAMPLERS}
    V["wall_tpe"] = f"{min(walls['tpe']):.0f}–{max(walls['tpe']):.0f}" if walls["tpe"] else "–"
    V["wall_gp"] = f"{min(walls['gp']):.0f}–{max(walls['gp']):.0f}" if walls["gp"] else "–"
    sw_w = [s["wall_s"] for s in runs.values() if s.get("wall_s") and s["run_id"] != "first40"]
    V["wall_sw"] = f"{min(sw_w):.0f}–{max(sw_w):.0f}" if sw_w else "–"

    V["results_table"] = results_table(runs, base)
    V["run_table"] = run_table(runs)
    V["ftp_chart"] = ftp_chart(runs, base)

    # costs
    usd = [s["cost"]["usd"] for s in runs.values()]
    calls = [s.get("llm_calls", 0) for s in runs.values()]
    V["cost_range"] = f"${min(usd):.2f}–${max(usd):.2f}" if usd else "–"
    V["cost_mean"] = f"${statistics.mean(usd):.2f}" if usd else "–"
    V["calls_range"] = f"{min(calls)}–{max(calls)}" if calls else "–"
    lat_h = [x for s in runs.values() for x in s["cost"]["per_model"].get("gemini-4-hydrogen", {}).get("latency", [])]
    V["lat_h_med"] = f"{statistics.median(lat_h):.0f}" if lat_h else "–"
    carb = [a["latency_s"] for s in runs.values() for a in (s.get("architect_calls") or []) if a.get("model") == "gemini-4-carbon"]
    V["lat_c_range"] = f"{min(carb):.0f}–{max(carb):.0f}" if carb else "–"
    fallback = [s["run_id"] for s in runs.values() for a in (s.get("architect_calls") or []) if a.get("model") != "gemini-4-carbon"]
    V["fallback_runs"] = ", ".join(fallback) if fallback else "none"

    # seed0-style recheck override note
    notes = []
    for rid, s in runs.items():
        ov = s.get("recheck_override")
        if s.get("recheck_full_lib") is False and ov:
            notes.append(f"* <span class='mono'>{E(rid)}</span>: the run logged a failed full-library re-check. That run predates the "
                         f"retry and long-timeout fix in <code>recheck_full</code>. Re-running the same check on the same design for this "
                         f"briefing gave <b>{'pass' if ov.get('passed_full_lib_nominal_spec') else 'fail'}</b> "
                         f"({ov.get('seconds')} s, <code>docs/report/data/{E(rid)}_recheck.json</code>). The logged failure most "
                         f"likely came from simulator timeouts on a loaded machine.")
    V["recheck_notes"] = ("<p class='small'>" + "<br>".join(notes) + "</p>") if notes else ""

    tv = tt_vs_all(runs)
    V["tt_n"], V["tt_pass"], V["all_pass"] = str(tv["n"]), str(tv["tt"]), str(tv["all"])

    # guard_s0-style worked example + architect quote
    ex_run = next((r for r in ("guard_s0", "guard_s1", "guard_s2") if r in runs and runs[r]["success"]), None)
    if ex_run:
        s = runs[ex_run]
        bp = s["best_params"]
        best = next((h for h in s["hist"] if h["n"] == s["best_n"]), None)
        it = bp["Ibias"] * 2 * bp["W_tail"] / 5e-6
        isk = bp["Ibias"] * 3 * bp["W_sink"] / 5e-6
        ugf = best["worst"]["ugf_hz"] if best else None
        gm1 = 2 * math.pi * ugf * bp["Cc"] if ugf else None
        V.update({
            "ex_run": ex_run, "ex_n": str(s["best_n"]),
            "ex_ibias": f"{bp['Ibias']*1e6:.1f}", "ex_wtail": f"{bp['W_tail']*1e6:.2f}", "ex_wsink": f"{bp['W_sink']*1e6:.2f}",
            "ex_itail": f"{it*1e6:.1f}", "ex_isink": f"{isk*1e6:.1f}", "ex_icore_calc": f"{(it+isk)*1e6:.1f}",
            "ex_icore_sim": f"{s['best_i_core_uA']:.1f}", "ex_cc": f"{bp['Cc']*1e12:.2f}", "ex_rz": f"{bp['Rz']/1e3:.1f}",
            "ex_ugf": f"{ugf/1e6:.1f}" if ugf else "–", "ex_ugf_at": E(best["worst_at"]["ugf_hz"]) if best else "–",
            "ex_gm1": f"{gm1*1e6:.0f}" if gm1 else "–", "ex_gmid": f"{gm1/(it/2):.0f}" if gm1 else "–",
            "ex_win": f"{bp['W_in']*1e6:.0f}", "ex_lin": f"{bp['L_in']*1e6:.2f}",
            "ex_pm": f"{best['worst']['pm_deg']:.1f}" if best else "–", "ex_pm_at": E(best["worst_at"]["pm_deg"]) if best else "–",
            "ex_gain": f"{best['worst']['dc_gain_db']:.1f}" if best else "–",
            "ex_rationale": E(best.get("rationale", "")) if best else "",
        })
        arch = jsonl(RUNS / "swarm" / ex_run / "architect.jsonl")
        diag = arch[0].get("diagnosis", "") if arch else ""
        sents = re.split(r"(?<=[.])\s+", diag)
        V["arch_quote"] = E(" ".join(sents[:4]))
        V["arch_dirs"] = "".join(f"<li><b>{E(d.get('focus', ''))}</b></li>" for d in (arch[0].get("directions", []) if arch else []))
    else:
        pending.append("guard-band swarm runs (worked example)")
        for k in ("ex_run", "ex_n", "ex_ibias", "ex_wtail", "ex_wsink", "ex_itail", "ex_isink", "ex_icore_calc", "ex_icore_sim",
                  "ex_cc", "ex_rz", "ex_ugf", "ex_ugf_at", "ex_gm1", "ex_gmid", "ex_win", "ex_lin", "ex_pm", "ex_pm_at",
                  "ex_gain", "ex_rationale", "arch_quote", "arch_dirs"):
            V[k] = "pending"

    # first40 thin-margin example
    f40 = runs.get("first40")
    if f40:
        b = next((h for h in f40["hist"] if h["n"] == f40["best_n"]), None)
        V["f40_pm"] = f"{b['worst']['pm_deg']:.1f}" if b else "–"
        V["f40_ugf"] = f"{b['worst']['ugf_hz']/1e6:.1f}" if b else "–"
        V["f40_icore"] = f"{f40['best_i_core_uA']:.1f}"
        V["f40_carbon"] = f"{(f40['cost']['per_model'].get('gemini-4-carbon') or {'latency': [0]})['latency'][0]:.0f}"
        V["f40_ftp"] = str(f40.get("candidates_to_first_pass"))
    # published start point
    start = next((h for s in runs.values() for h in s["hist"] if h.get("worker") == "start"), None)
    if start:
        w = start["worst"]
        V["def_pm"], V["def_pm_at"] = f"{w['pm_deg']:.1f}", start["worst_at"]["pm_deg"]
        V["def_ugf"] = f"{w['ugf_hz']/1e6:.0f}"
        V["def_gain"] = f"{w['dc_gain_db']:.1f}"
        V["def_icore"] = f"{w['i_core_a']*1e6:.1f}"
        V["def_sat"] = f"{w['min_sat_margin_v']*1e3:.0f}"
        V["def_nfail"] = str(start.get("n_failures"))

    hk = jload(ROOT / "designs" / "hand_K.json") or {}
    hw = hk.get("worst", {})
    V["hand_icore"] = f"{hw['i_core_a'][0]*1e6:.1f}" if hw else "–"
    V["hand_pm"] = f"{hw['pm_deg'][0]:.1f}" if hw else "–"
    V["hand_sat"] = f"{hw['min_sat_margin_v'][0]*1e3:.0f}" if hw else "–"

    V["ams_table"], ak = ams_table()
    V["ams_k"], V["ams_n"] = str(ak["k"]), str(ak["n"])

    V["signoff_block"], so_status = signoff_block()
    if so_status == "pending":
        pending.append("sign-off Managed Agent verdict (runs/signoff/*/comparison.json)")
    V["heldout_block"], ho_status = heldout_block()
    if ho_status == "pending":
        pending.append("held-out op-amps (folded-cascode, telescopic): swarm and baselines")

    # figures
    figs = {
        "fig_bode": first_existing(RUNS / "viz" / "first40_files" / "bode.png"),
        "fig_schem": first_existing(RUNS / "viz" / "first40_files" / "schematic.png"),
        "fig_traj": first_existing(RUNS / "viz" / "guard_s0_files" / "trajectory.png",
                                   HERE / "fig" / "guard_s0_files" / "trajectory.png",
                                   RUNS / "viz" / "first40_files" / "trajectory.png"),
        "fig_sat": first_existing(RUNS / "viz" / "first40_files" / "saturation.png"),
    }
    for k, p in figs.items():
        V[k] = f"<img src='{img_uri(p)}' alt=''>" if p else "<div class='callout pending'><p>figure not found</p></div>"
        V[k + "_src"] = E(str(p.relative_to(ROOT))) if p else "missing"
    V["traj_run"] = "guard_s0" if figs["fig_traj"] and "guard_s0" in str(figs["fig_traj"]) else "first40"

    for name in ("pipeline", "idvds", "bode_sketch", "opamp_blocks"):
        V["svg_" + name] = svg_inline(name)

    V["built"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    V["pending_list"] = ("".join(f"<li>{E(p)}</li>" for p in pending)) if pending else "<li>nothing; all expected files were present</li>"
    return V, pending


# --------------------------------------------------------------------------- render

def find_chromium() -> str:
    for b in ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "chrome"):
        p = shutil.which(b)
        if p:
            return p
    sys.exit("no Chromium/Chrome binary found on PATH")


def render(V: dict) -> str:
    tpl = (HERE / "template.html").read_text()
    css = (HERE / "style.css").read_text()
    missing = set()

    def sub(m):
        k = m.group(1)
        if k not in V:
            missing.add(k)
            return f"[[{k}]]"
        return str(V[k])
    out = re.sub(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", sub, tpl.replace("{{css}}", css))
    if missing:
        print("WARNING: template keys without values:", ", ".join(sorted(missing)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html-only", action="store_true")
    ap.add_argument("--pages", action="store_true", help="render page PNGs with pdftoppm")
    a = ap.parse_args()
    t0 = time.time()
    V, pending = build_values()
    OUT_HTML.write_text(render(V))
    print(f"wrote {OUT_HTML.relative_to(ROOT)}")
    if a.html_only:
        return
    cmd = [find_chromium(), "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
           "--run-all-compositor-stages-before-draw", "--virtual-time-budget=4000",
           f"--print-to-pdf={OUT_PDF}", OUT_HTML.as_uri()]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not OUT_PDF.exists() or r.returncode != 0:
        print(r.stderr[-2000:])
        sys.exit("PDF render failed")
    pages = None
    if shutil.which("pdfinfo"):
        info = subprocess.run(["pdfinfo", str(OUT_PDF)], capture_output=True, text=True).stdout
        m = re.search(r"Pages:\s+(\d+)", info)
        pages = int(m.group(1)) if m else None
    print(f"wrote {OUT_PDF.relative_to(ROOT)} ({pages} pages) in {time.time() - t0:.1f} s")
    print("pending:", "; ".join(pending) if pending else "none")
    if a.pages and shutil.which("pdftoppm"):
        pd = HERE / "pages"
        pd.mkdir(exist_ok=True)
        for old in pd.glob("p-*.png"):
            old.unlink()
        subprocess.run(["pdftoppm", "-png", "-r", "70", str(OUT_PDF), str(pd / "p")], check=True)
        print(f"page images in {pd.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
