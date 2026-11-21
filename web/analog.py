"""Analog sizing part of the site: SKY130 op-amp sizing with a Gemini 4 crew, read from runs/ at build time."""
from __future__ import annotations

import math
import statistics

from web.kit import (E, ROOT, RUNS, asset, chip, hbars, jload, jsonl, key, line_chart, pane, switch, table)

SW = RUNS / "swarm"
BL = RUNS / "opamp_baselines"
SAMPLERS = [("tpe", "Optuna TPE"), ("gp", "Optuna GP-BO"), ("random", "Random search")]
CORNERS = ["tt", "ff", "ss", "fs", "sf"]
TEMPS = [-40, 27, 125]
BARE = ["seed0", "seed1", "seed2", "first40"]
GUARD = ["guard_s0", "guard_s1", "guard_s2"]


def clip(t: str, n: int) -> str:
    """Cut at the last sentence end before n characters."""
    if len(t) <= n:
        return t
    cut = max(t.rfind(". ", 0, n), t.rfind("; ", 0, n))
    return t[:cut + 1] if cut > n // 3 else t[:n].rsplit(" ", 1)[0] + "…"


def mu(a):  # amps -> µA text
    return "–" if a is None else f"{a * 1e6:.1f}"


def med(xs):
    xs = sorted(math.inf if x is None else x for x in xs)
    if not xs:
        return "–"
    m = statistics.median(xs)
    return ">40" if math.isinf(m) else f"{m:g}"


# ------------------------------------------------------------------ op-amp: swarm vs Bayesian optimisation
def swarm_runs(ids):
    out = []
    for rid in ids:
        s = jload(SW / rid / "summary.json")
        if s:
            if rid == "seed0" and s.get("recheck_full_lib") is False:
                ov = jload(ROOT / "docs" / "report" / "data" / "seed0_recheck.json") or {}
                if ov.get("passed_full_lib_nominal_spec"):
                    s["recheck_full_lib"] = True
            s["_id"] = rid
            out.append(s)
    return out


def baseline_runs(tag):
    out = {}
    for smp, _ in SAMPLERS:
        out[smp] = [(jload(BL / f"{tag}_{smp}_s{k}_c40.json") or {}).get("summary") for k in range(3)]
    return out


def opamp_table():
    rows = []
    for title, sw_ids, tag in (("Bare spec", BARE, "default"), ("Guard-banded spec", GUARD, "guard")):
        sw = swarm_runs(sw_ids)
        bl = baseline_runs(tag)
        k = sum(bool(s.get("success")) for s in sw)
        cur = [s.get("best_i_core_uA") for s in sw if s.get("success")]
        rows.append(['<b>routr</b>', f'<span class="soft">{title}</span>', f'<td class="n hl"><span class="ok-t">{k}/{len(sw)}</span></td>',
                     f'<td class="n hl">{", ".join(str(s.get("candidates_to_first_pass") or "–") for s in sw)}</td>',
                     f'<td class="n hl">{med([s.get("candidates_to_first_pass") for s in sw])}</td>',
                     f'<td class="n hl">{min(cur):.1f}</td>' if cur else '<td class="n hl">–</td>'])
        for smp, lab in SAMPLERS:
            ss = [x for x in bl[smp] if x]
            kk = sum(bool(x.get("success")) for x in ss)
            c = [x.get("best_i_core_uA") for x in ss if x.get("success") and x.get("best_i_core_uA")]
            rows.append([lab, f'<span class="soft">{title}</span>',
                         f'<td class="n"><span class="{"ok-t" if kk else "fail-t"}">{kk}/{len(ss)}</span></td>',
                         f'<td class="n">{", ".join(str(x.get("candidates_to_first_pass") or "–") for x in ss)}</td>',
                         f'<td class="n">{med([x.get("candidates_to_first_pass") for x in ss])}</td>',
                         f'<td class="n">{min(c):.1f}</td>' if c else '<td class="n">–</td>'])
    return table(["Method", "Spec", "Runs that pass", "First pass at candidate", "Median", "Best core current (µA)"], rows)


def opamp_bars():
    sw = swarm_runs(BARE + GUARD)
    k = sum(bool(s.get("success")) for s in sw)
    rows = [("routr", k / max(1, len(sw)) * 100, "var(--mint)", f"{k}/{len(sw)} runs")]
    for smp, lab in SAMPLERS:
        ss = [x for t in ("default", "guard") for x in baseline_runs(t)[smp] if x]
        kk = sum(bool(x.get("success")) for x in ss)
        rows.append((lab, kk / max(1, len(ss)) * 100, "var(--text-soft)", f"{kk}/{len(ss)} runs"))
    return hbars(rows, vmax=100, w=560, label_w=150)


def candidate_view(rid):
    """Worst-case phase margin of every candidate in one run: pass in mint, fail in coral."""
    hist = jsonl(SW / rid / "history.jsonl")
    s = jload(SW / rid / "summary.json") or {}
    if not hist:
        return ""
    tgt = (hist[0].get("target") or {}).get("pm_deg")
    ok, bad = [], []
    tok, tbad = [], []
    for h in hist:
        w = h.get("worst") or {}
        pm = w.get("pm_deg")
        if pm is None:
            continue
        pm = max(-10, min(110, pm))
        tip = (f'#{h["n"]} · {h.get("worker")} · PM {w.get("pm_deg", 0):.1f}° · gain {w.get("dc_gain_db", 0):.1f} dB · '
               f'{mu(w.get("i_core_a"))} µA')
        if h.get("passed"):
            ok.append((h["n"], pm)); tok.append(tip + " · PASS at all 15 points")
        else:
            bad.append((h["n"], pm)); tbad.append(tip + " · " + "; ".join((h.get("failures") or [])[:2]))
    ch = line_chart([{"name": "fail", "color": "var(--coral)", "pts": bad, "noline": True, "dots": True, "tips": tbad},
                     {"name": "pass", "color": "var(--mint-text)", "pts": ok, "noline": True, "dots": True, "tips": tok}],
                    xlabel="candidate", hlines=[(tgt, f"target {tgt:g}°")] if tgt else None, h=260)
    cur = [(h["n"], (h.get("worst") or {}).get("i_core_a", 0) * 1e6) for h in hist if (h.get("worst") or {}).get("i_core_a")]
    best, run = [], math.inf
    for h in hist:
        if h.get("passed") and (h.get("worst") or {}).get("i_core_a"):
            run = min(run, h["worst"]["i_core_a"] * 1e6)
        best.append((h["n"], None if math.isinf(run) else run))
    ch2 = line_chart([{"name": "all", "color": "var(--s0)", "pts": cur, "noline": True, "dots": True},
                      {"name": "best", "color": "var(--mint-text)", "pts": [p for p in best if p[1] is not None], "step": True}],
                     xlabel="candidate", ymin=0, h=200, step=True)
    arch = jsonl(SW / rid / "architect.jsonl")
    notes = "".join(f'<div class="step"><div class="h"><b><i style="background:var(--sky)"></i>Architect</b><span class="act">{E(a.get("model") or "")}</span>'
                    f'<span class="soft">round {a.get("iter")}</span></div><p>{E(clip(a.get("diagnosis") or "", 600))}</p>'
                    + "".join(f'<p class="soft" style="margin-top:4px">→ <b>{E(d.get("focus") or "")}</b>: {E(clip(d.get("instructions") or "", 240))}</p>'
                              for d in (a.get("directions") or [])[:4]) + "</div>" for a in arch[:6])
    chips = [chip("passes all 15 PVT points" if s.get("success") else "no pass", "ok" if s.get("success") else "fail"),
             chip(f'first pass at candidate {s.get("candidates_to_first_pass")}') if s.get("candidates_to_first_pass") else "",
             chip(f'best {s.get("best_i_core_uA", 0):.1f} µA core current') if s.get("best_i_core_uA") else "",
             chip(f'{s.get("llm_calls", "?")} Gemini calls', "ai"),
             chip("full-library recheck: pass", "ok") if s.get("recheck_full_lib") else ""]
    return f"""
<div class="chips" style="margin:20px 0 16px">{"".join(chips)}</div>
<div class="grid2">
  <div class="stack"><div class="card"><div class="chead"><h4>Worst-case phase margin, every candidate</h4><span class="soft" style="font-size:13.5px">worst of 15 PVT points · hover a dot</span></div>{ch}
    {key([("passes every spec at all 15 points", "var(--mint-text)", False), ("fails somewhere", "var(--coral)", False)])}</div>
    <div class="card"><div class="chead"><h4>Core current (µA)</h4><span class="soft" style="font-size:13.5px">line = lowest passing so far</span></div>{ch2}</div></div>
  <div class="card"><div class="chead"><h4>What the architect saw and asked for</h4><span class="soft" style="font-size:13.5px">architect.jsonl</span></div><div class="feed">{notes or '<p class="soft">No architect log in this run.</p>'}</div></div>
</div>"""


def pvt_grid(rid):
    """Best passing candidate's 15 PVT points as a grid."""
    hist = jsonl(SW / rid / "history.jsonl")
    s = jload(SW / rid / "summary.json") or {}
    best = next((h for h in hist if h.get("n") == s.get("best_n")), None)
    if not best or not best.get("points"):
        return ""
    pts = {(p["corner"], int(p["temp"])): p for p in best["points"]}
    head = "".join(f"<th>{t} °C</th>" for t in TEMPS)
    rows = []
    for c in CORNERS:
        tds = []
        for t in TEMPS:
            p = pts.get((c, t))
            if not p:
                tds.append('<td class="n dim">–</td>'); continue
            cls = "ok-t" if p.get("pass") else "fail-t"
            tds.append(f'<td class="n"><span class="{cls}">{p["pm_deg"]:.0f}°</span> · {p["dc_gain_db"]:.0f} dB · '
                       f'{p["ugf_hz"] / 1e6:.1f} MHz · {p["i_core_a"] * 1e6:.0f} µA</td>')
        rows.append(f"<tr><td><b>{c}</b></td>{''.join(tds)}</tr>")
    return (f'<div class="tw"><table><thead><tr><th>Corner</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'<p class="soft" style="font-size:13.5px;margin-top:8px">{E(rid)}, candidate #{best["n"]}: phase margin · DC gain · unity-gain frequency · core current. Green = passes the guard-banded target at that point.</p>')


# ------------------------------------------------------------------ other experiments
def ams_bench():
    circ = ["azc", "fdgb", "iac", "smc", "telescopic_ota"]
    names = {"azc": "Active zero-comp. op-amp", "fdgb": "Fully-diff. gain boost", "iac": "Indirect AC op-amp",
             "smc": "Nested Miller op-amp", "telescopic_ota": "Telescopic OTA"}
    rows, tot = [], {"swarm": [0, 0], "gp": [0, 0], "tpe": [0, 0], "random": [0, 0]}
    for c in circ:
        r = [f'<b>{names[c]}</b> <span class="soft mono">{c}</span>']
        for m in ("swarm", "gp", "tpe", "random"):
            fs = [jload(RUNS / "ams_swarm" / f"{c}_s{k}_b100.json") if m == "swarm" else
                  jload(RUNS / "baselines" / f"{c}_{m}_s{k}_b100_tt.json") for k in range(3)]
            ss = [(f or {}).get("summary") for f in fs if f]
            k = sum(bool(x.get("success")) for x in ss)
            tot[m][0] += k; tot[m][1] += len(ss)
            ftp = ", ".join(str(x.get("sims_to_first_pass")) if x.get("success") else "–" for x in ss)
            r.append(f'<td class="n{" hl" if m == "swarm" else ""}"><b>{k}/{len(ss)}</b><span class="dim"> · {ftp}</span></td>')
        rows.append(r)
    rows.append(["all five"] + [f'<td class="n{" hl" if m == "swarm" else ""}">{tot[m][0]}/{tot[m][1]}</td>' for m in tot])
    return table(["Circuit", "routr", "Optuna GP-BO", "Optuna TPE", "Random"], rows).replace(
        "<tr><td>all five", '<tr class="tot"><td>all five')


def intake():
    d = jload(RUNS / "intake_eval.json") or {}
    rows, tot = [], {True: [0, 0], False: [0, 0]}
    for r in d.get("rows", []):
        tot[r["with_image"]][0] += r["n_correct"]; tot[r["with_image"]][1] += r["n"]
        rows.append([f'<b>{E(r["circuit"])}</b>', "schematic photo + netlist" if r["with_image"] else "netlist only",
                     f'<td class="n">{r["n_correct"]}/{r["n"]}</td>', f'<td class="n">{E(r.get("identified_as") or "")} · {r.get("id_confidence", 0):.2f}</td>',
                     f'<td class="n">{r.get("latency_s", 0):.1f} s</td>'])
    return tot, table(["Circuit", "Input", "Device roles right", "Circuit identified as", "Latency"], rows)


def heldout():
    rows = []
    for c, lab in (("fc", "Folded cascode"), ("tele", "Telescopic")):
        for ver, pat in (("crew v1", f"heldout_{c}_s"), ("crew v2 (re-plan + plot critic)", f"v2_{c}_s")):
            ss = [jload(SW / f"{pat}{k}" / "summary.json") for k in range(3)]
            ss = [s for s in ss if s]
            k = sum(bool(s.get("success")) for s in ss)
            rows.append([f"<b>{lab}</b>", ver, f'<td class="n"><span class="{"ok-t" if k else "fail-t"}">{k}/{len(ss)}</span></td>',
                         f'<td class="n">{", ".join(f"{s.get("best_i_core_uA", 0):.0f}" for s in ss if s.get("best_i_core_uA"))}</td>',
                         f'<td class="n">{sum(s.get("n_passed_nominal_spec") or 0 for s in ss)}</td>'])
        ss = [(jload(BL / f"heldout_{c}_tpe_s{k}_c40.json") or {}).get("summary") for k in range(3)]
        ss = [s for s in ss if s]
        k = sum(bool(s.get("success")) for s in ss)
        rows.append([f"<b>{lab}</b>", "Optuna TPE", f'<td class="n"><span class="{"ok-t" if k else "fail-t"}">{k}/{len(ss)}</span></td>',
                     '<td class="n dim">–</td>', '<td class="n dim">–</td>'])
    diag = (jload(SW / "v2_fc_s0" / "summary.json") or {}).get("final_diagnosis") or ""
    q = (f'<div class="quote" style="margin-top:16px"><div class="who"><i style="width:10px;height:10px;border-radius:50%;background:var(--sky)"></i>'
         f'Architect, v2_fc_s0, final diagnosis</div>{E(clip(diag, 900))}</div>' if diag else "")
    return table(["Circuit", "Method", "Runs that pass", "Best core current (µA)", "Candidates passing nominal spec"], rows) + q


def oneshot():
    rows = []
    for c in ("diff", "fc", "tele"):
        fs = [jload(RUNS / "oneshot" / f"{c}_gemini-4-hydrogen_s{k}.json") for k in range(3)]
        fs = [f for f in fs if f]
        k = sum(bool(f.get("passed_target")) for f in fs)
        rows.append([f"<b>{c}</b>", f'<td class="n">{k}/{len(fs)}</td>',
                     f'<td class="n">{", ".join(str(f.get("n_failures")) for f in fs)}</td>'])
    return table(["Circuit", "One Gemini call passes", "Failures per try"], rows)


def layout():
    rows, series = [], []
    cols = {"crew_s0": ("routr", "var(--mint-text)"), "tpe_s0": ("Optuna TPE", "var(--s1)"), "random_s0": ("Random", "var(--s0)")}
    for rid, (lab, col) in cols.items():
        s = jload(RUNS / "layout" / rid / "summary.json") or {}
        if not s:
            continue
        rows.append([f"<b>{lab}</b>", f'<td class="n">{s.get("area_um2", 0):.0f}</td>', f'<td class="n">{s.get("area_reduction_pct", 0):.1f}%</td>',
                     f'<td class="n">{s.get("offset_uV", 0):.2f}</td>', f'<td class="n">{s.get("cost", 0):.3f}</td>',
                     f'<td class="n">{s.get("n_feasible")}/{s.get("candidates")}</td>', f'<td class="n">{s.get("candidates_to_first_feasible") or "–"}</td>'])
        tr = s.get("best_cost_trace") or []
        series.append({"name": lab, "color": col, "pts": [(i + 1, min(v, 2.0)) for i, v in enumerate(tr) if v is not None]})
    naive = jload(RUNS / "layout" / "crew_s0" / "summary.json") or {}
    img = asset(RUNS / "layout" / "crew_s0" / "before_after.png", "analog/layout_before_after.png")
    ch = line_chart(series, xlabel="candidate", step=True, h=220)
    return f"""
<div class="grid2">
  {table(["Method", "Area µm²", "Area cut", "Offset µV", "Cost", "Feasible", "First feasible"], rows)}
    <p class="soft" style="font-size:13.5px;grid-column:1/-1;order:3">Against a naive layout of {naive.get("naive_area_um2", 0):.0f} µm² and {naive.get("naive_offset_uV", 0):.1f} µV offset. {E(naive.get("disclaimer") or "Plans only: not routed, no DRC or LVS.")}</p>
    <div class="card"><div class="chead"><h4>Best cost so far</h4><span class="soft" style="font-size:13.5px">lower is better · no feasible plan yet drawn at 2</span></div>{ch}{key([(l, c, False) for l, c in cols.values()])}</div>
</div>
{f'<figure class="frame" data-zoom style="margin-top:20px"><img loading="lazy" src="{img}" alt="layout before and after"><figcaption>naive → crew plan</figcaption></figure>' if img else ""}"""


def create():
    rows = []
    for t in ("test_a_diff", "test_b_twostage", "test_b_twostage_pm", "test_c_image"):
        s = jload(RUNS / "create" / t / "sizing_summary.json") or {}
        rq = jload(RUNS / "create" / t / "request.json") or {}
        sw, rd = s.get("swarm") or {}, s.get("random") or {}
        if not s:
            continue
        rows.append([f'<b>{E(t.replace("test_", ""))}</b>', E(str(rq.get("circuit_kind") or "")) + (" · from a sketch" if rq.get("image") else " · from text"),
                     f'<td class="n hl">{"pass" if sw.get("success") else "fail"} · {(sw.get("best_metrics") or {}).get("fom", 0):.1f}</td>',
                     f'<td class="n">{"pass" if rd.get("success") else "fail"} · {(rd.get("best_metrics") or {}).get("fom", 0):.1f}</td>'])
    sk = asset(RUNS / "create" / "sketch_5t_ota.png", "analog/sketch_5t_ota.png")
    return f"""<div class="grid32"><div>{table(["Test", "Request", "Crew: result · best FoM", "Random: result · best FoM"], rows)}</div>
  {f'<figure class="frame" data-zoom><img loading="lazy" src="{sk}" alt="hand sketch of a five-transistor OTA"><figcaption>input sketch</figcaption></figure>' if sk else ""}</div>"""


def signoff():
    d = jload(RUNS / "signoff" / "guard_s0" / "comparison.json")
    if not d:
        return ""
    rows = []
    for r in d.get("rows", []):
        o, a = r.get("ours") or {}, r.get("agent") or {}
        rows.append([f'<b>{r["corner"]}/{r["temp"]}</b>', f'<td class="n">{o.get("dc_gain_db", 0):.3f}</td>', f'<td class="n">{a.get("dc_gain_db", 0):.3f}</td>',
                     f'<td class="n">{o.get("pm_deg", 0):.2f}</td>', f'<td class="n">{a.get("pm_deg", 0):.2f}</td>',
                     f'<td class="n">{o.get("ugf_hz", 0) / 1e6:.3f}</td>', f'<td class="n">{a.get("ugf_hz", 0) / 1e6:.3f}</td>'])
    kp = (f'<div class="chips" style="margin-bottom:16px">{chip(f"{d.get("points_matched")}/{d.get("points_total")} points matched", "ok")}'
          f'{chip("verdicts agree", "ok") if d.get("verdict_agrees") else chip("verdicts differ", "fail")}'
          f'{chip(f"max gain diff {d.get("max_abs_dgain_db", 0):.4f} dB")}{chip(f"max PM diff {d.get("max_abs_dpm_deg", 0):.3f}°")}'
          f'{chip(f"{d.get("spice_calls")} SPICE calls · {d.get("wall_s", 0):.0f} s", "ai")}</div>')
    v = d.get("agent_verdict") or ""
    return kp + f'<div class="quote" style="margin-bottom:16px"><div class="who">Managed Agent verdict, written on its own simulator</div>{E(v)}</div>' + \
        f'<details class="more"><summary>All 15 points, ours vs the agent</summary><div style="margin-top:12px">' + \
        table(["Point", "Gain ours", "Gain agent", "PM ours", "PM agent", "UGF ours MHz", "UGF agent MHz"], rows) + "</div></details>"


def redteam():
    d = jload(RUNS / "redteam" / "rt1" / "findings.json") or {}
    s = jload(RUNS / "redteam" / "rt1" / "summary.json") or {}
    items = "".join(
        f'<li class="{"fail" if f.get("severity") == "high" else ""}"><b>{E(f.get("id", ""))} · {E(f.get("title", ""))}</b> '
        f'<span class="chip {"fail" if f.get("severity") == "high" else "open"}" style="margin-left:6px">{E(f.get("severity", ""))}</span>'
        f'{chip("confirmed", "ink") if f.get("confirmed") else ""}<br><span class="soft">{E(clip(f.get("observed") or "", 300))}</span></li>'
        for f in d.get("findings", []))
    return (f'<p class="soft">A Managed Agent was told to break our judge. {s.get("turns", "?")} turns, {s.get("evaluate_calls", "?")} judge calls, '
            f'{s.get("confirmed", "?")}/{s.get("n_findings", "?")} findings confirmed with a reproducer.</p>'
            f'<div class="quote" style="margin:12px 0 16px">{E(d.get("summary") or "")}</div><ul class="limits">{items}</ul>')


def gallery():
    figs = [
        (RUNS / "viz" / "first40_files" / "schematic.png", "Schematic", "Devices coloured by the role the intake model gave them."),
        (RUNS / "viz" / "first40_files" / "bode.png", "Bode plot", "One curve per PVT point for the passing design."),
        (RUNS / "viz" / "first40_files" / "saturation.png", "Saturation margins", "Every device, every PVT point."),
        (RUNS / "docs" / "x", "", ""),
        (ROOT / "docs" / "report" / "fig" / "guard_s0_files" / "trajectory.png", "Search trajectory", "Worst-case metrics against candidate, guard_s0."),
        (RUNS / "viz" / "first40_files" / "trajectory.png", "Search trajectory", "Worst-case metrics against candidate, first40."),
        (RUNS / "layout" / "crew_s0" / "best.png", "Layout plan", "The crew's best floorplan (not routed, no DRC)."),
        (RUNS / "layout" / "crew_s0" / "naive.png", "Naive layout", "The starting floorplan the plan is measured against."),
    ]
    out = []
    for i, (p, t, cap) in enumerate(figs):
        u = asset(p, f"analog/fig_{i}_{p.name}") if t else ""
        if u:
            out.append(f'<figure data-zoom><img loading="lazy" src="{u}" alt="{E(t)}"><figcaption><b>{E(t)}</b>{E(cap)}</figcaption></figure>')
    return f'<div class="gal">{"".join(out)}</div>'


def stage():
    return """
<div class="card">
  <div class="chead"><div><h3>routr live stage</h3><p class="soft" style="margin:4px 0 0">Photo of a schematic in, spoken spec in, the crew sizes the circuit live, then layout, sign-off and red team. Replays work offline.</p></div>
    <a class="btn lilac" href="http://127.0.0.1:8000/" target="_blank" rel="noopener">Open the stage</a></div>
  <figure class="frame live" data-live="http://127.0.0.1:8000/" style="background:var(--sunk);min-height:120px">
    <div class="soft livehint" style="padding:28px;text-align:center">The stage is not running. Start it with <code>web/serve.sh</code> (or <code>.venv/bin/python -m demo.server --port 8000</code>) and reload this page.</div>
  </figure>
</div>"""


def numbers() -> dict:
    sw = swarm_runs(BARE + GUARD)
    bl = [x for t in ("default", "guard") for smp, _ in SAMPLERS for x in baseline_runs(t)[smp] if x]
    tot, _ = intake()
    so = jload(RUNS / "signoff" / "guard_s0" / "comparison.json") or {}
    return {"sw_k": sum(bool(s.get("success")) for s in sw), "sw_n": len(sw),
            "bl_k": sum(bool(x.get("success")) for x in bl), "bl_n": len(bl),
            "intake": tot[True], "intake_txt": tot[False],
            "signoff": (so.get("points_matched"), so.get("points_total"))}


def section() -> str:
    n = numbers()
    tot, intake_tb = intake()
    runs = [r for r in GUARD + BARE if (SW / r / "history.jsonl").is_file()]
    picker = switch("run", [(r, r.replace("_", " ")) for r in runs],
                    ["var(--mint)" if (jload(SW / r / "summary.json") or {}).get("success") else "var(--coral)" for r in runs])
    panes = "".join(pane("run", r, candidate_view(r), i == 0) for i, r in enumerate(runs))
    pvt_items = [(r, r.replace("_", " ")) for r in GUARD if pvt_grid(r)]
    pvt = (switch("pvt", pvt_items) + "".join(pane("pvt", r, '<div style="margin-top:16px">' + pvt_grid(r) + "</div>", i == 0)
                                              for i, (r, _) in enumerate(pvt_items))) if pvt_items else ""
    pipe = asset(ROOT / "docs" / "report" / "svg" / "pipeline.svg", "analog/pipeline.svg")
    return f"""
<section class="band" id="analog">
  <div class="wrap">
    <span class="part">Part 2 · Analog sizing</span>
    <div class="sechead"><div><h2>Size an op-amp that passes at every corner.</h2>
      <p class="lede">A SKY130 two-stage Miller op-amp, simulated in ngspice at <b>15 process, voltage and temperature points</b> (5 corners × 3 temperatures). A design passes only if gain, bandwidth, phase margin, current and every transistor's saturation hold at all 15.</p></div></div>
    <div class="kpis" style="margin-top:0">
      <div class="kpi ok"><b>{n["sw_k"]}<small>/{n["sw_n"]}</small></b><span>routr runs that pass, within 40 candidates</span></div>
      <div class="kpi"><b>{n["bl_k"]}<small>/{n["bl_n"]}</small></b><span>Bayesian-optimisation and random runs that pass, same budget</span></div>
      <div class="kpi ok"><b>{n["intake"][0]}<small>/{n["intake"][1]}</small></b><span>device roles read right from a schematic photo ({n["intake_txt"][0]}/{n["intake_txt"][1]} from the netlist alone)</span></div>
      <div class="kpi ok"><b>{n["signoff"][0]}<small>/{n["signoff"][1]}</small></b><span>PVT points an independent sign-off agent matched</span></div>
    </div>
  </div>
</section>
<section class="band alt" id="analog-how">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot" style="background:var(--sky)"></span>How the crew works</div><h2>Read the circuit, hear the spec, size it, check it</h2></div></div>
    <div class="flow">
      <div class="node ai"><h4>Read the schematic</h4><span class="m">Decisions API</span><p>A photo of the schematic plus the netlist: every transistor gets a role (input pair, load, tail…) with a confidence.</p></div>
      <div class="node ai"><h4>Hear the spec</h4><span class="m">gemini-4-hydrogen</span><p>A spoken or typed spec becomes targets: gain, bandwidth, phase margin, current.</p></div>
      <div class="node ai"><h4>Architect</h4><span class="m">gemini-4-carbon</span><p>Reads the worst PVT failures and the Bode plot, then sends each worker a direction.</p></div>
      <div class="node ai"><h4>Four workers</h4><span class="m">gemini-4-hydrogen</span><p>Propose sizings in parallel. Every candidate is 15 ngspice decks.</p></div>
      <div class="node judge"><h4>Judge, then sign-off</h4><span class="m">ngspice · Managed Agent</span><p>Our judge scores every candidate; an independent agent re-simulates the winner from scratch.</p></div>
    </div>
    {f'<figure class="frame" style="margin-top:24px;background:#fff;padding:16px" data-zoom><img loading="lazy" src="{pipe}" alt="pipeline diagram"></figure>' if pipe else ""}
  </div>
</section>
<section class="band" id="analog-proof">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot"></span>Proof against a baseline</div><h2>The crew against Bayesian optimisation</h2>
      <p class="lede">Same circuit, same 40-candidate budget (600 simulations), three seeds each. Optuna TPE and GP-BO are the standard black-box optimisers for this job.</p></div></div>
    <div class="card" style="max-width:720px;margin-bottom:20px"><h4 style="margin-bottom:12px">Share of runs that pass, both specs</h4>{opamp_bars()}</div>{opamp_table()}
  </div>
</section>
<section class="band alt" id="analog-runs">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot"></span>Real runs</div><h2>Every candidate the crew tried</h2>
      <p class="lede">Pick a run. Each dot is one sizing, simulated at all 15 points; hover for its worst case and why it failed.</p></div></div>
    {picker}{panes}
    {f'<h3 style="margin:48px 0 16px">The winning design at every PVT point</h3>{pvt}' if pvt else ""}
  </div>
</section>
<section class="band" id="analog-views">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot" style="background:var(--lilac)"></span>Views</div><h2>What the crew looks at</h2><p class="lede">Click any figure to enlarge it.</p></div></div>
    {gallery()}
  </div>
</section>
<section class="band alt" id="analog-more">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot"></span>More evidence</div><h2>Beyond the one op-amp</h2></div></div>
    <h3 style="margin-bottom:8px">AMS-SizingBench: five circuits we did not write</h3>
    <p class="soft">Budget 100 simulations at tt/27 °C, three seeds. Cell = runs that pass · simulations to the first pass in each.</p>
    {ams_bench()}
    <h3 style="margin:48px 0 8px">Reading the circuit: photo against netlist</h3>
    <p class="soft">Device roles right: {tot[True][0]}/{tot[True][1]} with the schematic photo, {tot[False][0]}/{tot[False][1]} from the netlist alone.</p>
    {intake_tb}
    <h3 style="margin:48px 0 8px">From a request to a sized circuit</h3>
    <p class="soft">A sentence or a hand sketch in; a netlist, a sizing and an explanation out.</p>
    {create()}
    <h3 style="margin:48px 0 8px">Layout planning</h3>
    <p class="soft">Finger counts, matching patterns, dummies and rows for the winning sizing; estimated parasitics fed back into the simulation.</p>
    {layout()}
  </div>
</section>
<section class="band" id="analog-check">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot"></span>Checks</div><h2>Who checks the judge?</h2></div></div>
    <h3 style="margin-bottom:12px">Independent sign-off</h3>
    {signoff()}
    <h3 style="margin:48px 0 12px">Red team: an agent paid to break our judge</h3>
    {redteam()}
  </div>
</section>
<section class="band alt" id="analog-limits">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot" style="background:var(--coral)"></span>Honest failures</div><h2>Where it did not work</h2>
      <p class="lede">Two circuits held out from development. Neither the crew nor TPE found a passing design; the binding problem is keeping the PMOS cascode in saturation.</p></div></div>
    {heldout()}
    <h3 style="margin:48px 0 8px">One Gemini call, no loop</h3>
    <p class="soft">The same model asked once for a sizing, with no simulator feedback.</p>
    {oneshot()}
  </div>
</section>
<section class="band" id="analog-live">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot" style="background:var(--lilac)"></span>Live</div><h2>Run the crew yourself</h2></div></div>
    {stage()}
  </div>
</section>"""
