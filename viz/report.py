"""Self-contained HTML report for one design (optionally vs a reference) or one swarm run.

  python -m viz.report --design designs/hand_K.json --compare default --out runs/viz/hand_K.html
  python -m viz.report --run runs/swarm/<run_id> [--compare default] [--out runs/viz/<run_id>.html]

Every figure is inlined as SVG (no external assets) and also written as PNG + SVG to <out stem>_files/.
"""
from __future__ import annotations

import argparse
import html
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sizing import opamp2stg as oa
from viz.common import load_design, pt_label, save_fig, spec_of

# --------------------------------------------------------------------------- formatting

METRICS = [  # key, label, scale, format, unit
    ("pm_deg", "Phase margin", 1.0, "{:.1f}", "°"),
    ("ugf_hz", "Unity-gain frequency", 1e-6, "{:.1f}", " MHz"),
    ("dc_gain_db", "DC loop gain", 1.0, "{:.1f}", " dB"),
    ("i_core_a", "Core current", 1e6, "{:.1f}", " µA"),
    ("gain_margin_db", "Gain margin", 1.0, "{:.1f}", " dB"),
    ("min_sat_margin_v", "Min. saturation margin", 1e3, "{:.0f}", " mV"),
]
KNOBS = [  # knob, label, scale, unit
    ("W_in", "W input pair", 1e6, "µm"), ("L_in", "L input pair", 1e6, "µm"),
    ("W_load", "W mirror load", 1e6, "µm"), ("L_load", "L mirror load", 1e6, "µm"),
    ("W_tail", "W tail", 1e6, "µm"), ("W_drv", "W stage-2 driver", 1e6, "µm"),
    ("L_drv", "L stage-2 driver", 1e6, "µm"), ("W_sink", "W stage-2 sink", 1e6, "µm"),
    ("Ibias", "Ibias", 1e6, "µA"), ("Cc", "Cc", 1e12, "pF"), ("Rz", "Rz", 1e-3, "kΩ"),
]
OPS = {">=": "≥", "<=": "≤", ">": ">", "<": "<"}
E = html.escape


def _fmt(key, v):
    for k, _, sc, f, u in METRICS:
        if k == key:
            return f.format(v * sc) + u
    return f"{v:.4g}"


def _spec_ok(spec, key, v):
    if key == "min_sat_margin_v":
        return None
    if key not in spec or v is None:
        return None
    op, b = spec[key]
    return {">=": v >= b, "<=": v <= b, ">": v > b, "<": v < b}[op]


def _spec_txt(spec, key):
    if key == "min_sat_margin_v":
        return "per role (see schematic)"
    if key not in spec:
        return "—"
    op, b = spec[key]
    unit, sc = next((m[4], m[2]) for m in METRICS if m[0] == key)
    return f"{OPS[op]} {b * sc:g}{unit}"


def _cell(er: oa.EvalResult, spec, key):
    w = er.worst.get(key)
    if not w or w[0] is None:
        return '<td class="na">—</td>'
    ok = _spec_ok(spec, key, w[0])
    mark = "" if ok is None else ('<span class="ok">✓</span> ' if ok else '<span class="bad">✗</span> ')
    return f'<td>{mark}<b>{_fmt(key, w[0])}</b> <span class="at">{pt_label(w[1], w[2])}</span></td>'


def badge(passed: bool) -> str:
    return ('<span class="badge pass">✓ PASS</span>' if passed
            else '<span class="badge fail">✗ FAIL</span>')


# --------------------------------------------------------------------------- sections

def worst_table(spec, items: list[tuple[str, oa.EvalResult]]) -> str:
    head = "".join(f"<th>{E(n)} {badge(er.passed)}</th>" for n, er in items)
    rows = []
    for key, label, *_ in METRICS:
        rows.append(f"<tr><th class='m'>{label}</th><td class='spec'>{_spec_txt(spec, key)}</td>"
                    + "".join(_cell(er, spec, key) for _, er in items) + "</tr>")
    nviol = "".join(f"<td>{len(er.failures)}</td>" for _, er in items)
    rows.append(f"<tr><th class='m'>Violations (all PVT)</th><td class='spec'>0</td>{nviol}</tr>")
    return (f"<table class='worst'><thead><tr><th>worst case over {len(items[0][1].points)} PVT points</th>"
            f"<th>spec</th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>")


def knob_table(params: dict, ref: dict | None, name: str, ref_name: str) -> str:
    p = oa.clamp(params)
    r = oa.clamp(ref) if ref is not None else None
    rows = []
    for k, label, sc, unit in KNOBS:
        a = f"{p[k] * sc:.3g}"
        if r is None:
            rows.append(f"<tr><th class='m'>{label}</th><td>{a} {unit}</td></tr>")
            continue
        b = f"{r[k] * sc:.3g}"
        ratio = p[k] / r[k] if r[k] else float("nan")
        changed = abs(ratio - 1) > 1e-6
        arrow = ("▲" if ratio > 1 else "▼") if changed else ""
        rows.append(f"<tr class='{'chg' if changed else 'same'}'><th class='m'>{label}</th><td>{b} {unit}</td>"
                    f"<td><b>{a} {unit}</b></td><td class='ratio'>{arrow} {'×%.2g' % ratio if changed else '='}</td></tr>")
    head = ("<tr><th>knob</th><th>value</th></tr>" if r is None else
            f"<tr><th>knob</th><th>{E(ref_name)}</th><th>{E(name)}</th><th>change</th></tr>")
    return f"<table class='knobs'><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table>"


def failures_block(er: oa.EvalResult, name: str) -> str:
    if not er.failures:
        return ""
    items = "".join(f"<li>{E(f)}</li>" for f in er.failures[:40])
    more = f"<li>… {len(er.failures) - 40} more</li>" if len(er.failures) > 40 else ""
    return (f"<details><summary>{E(name)}: {len(er.failures)} spec violations across PVT</summary>"
            f"<ul class='fails'>{items}{more}</ul></details>")


def figure(svg: str, title: str, caption: str, fid: str) -> str:
    return (f"<section class='fig' id='{fid}'><h2>{E(title)}</h2><p class='cap'>{caption}</p>"
            f"<div class='svg'>{svg}</div></section>")


CSS = """
:root { color-scheme: light; --ink:#0b0b0b; --ink2:#52514e; --muted:#8a8984; --line:#e4e3df;
  --bg:#f6f6f4; --card:#ffffff; --good:#0a7d0a; --bad:#c0302f; --accent:#2a78d6; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font: 17px/1.5 "Inter", "Liberation Sans", "Helvetica Neue", Arial, sans-serif; }
main { max-width: 1280px; margin: 0 auto; padding: 28px 16px 64px; }
header.top { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:22px 26px; }
h1 { font-size: 30px; margin: 0 0 4px; letter-spacing:-0.01em; }
h1 .sub { font-weight: 400; color: var(--ink2); font-size: 20px; }
h2 { font-size: 22px; margin: 0 0 4px; }
.meta { color: var(--ink2); margin: 4px 0 14px; }
.badge { display:inline-block; padding: 2px 10px; border-radius: 999px; font-weight:700; font-size: 15px;
  vertical-align: middle; margin-left: 6px; }
.badge.pass { background:#e3f4e3; color:var(--good); border:1px solid #9fd69f; }
.badge.fail { background:#fbe7e7; color:var(--bad); border:1px solid #efaaaa; }
.chips { display:flex; flex-wrap:wrap; gap:8px; margin: 10px 0 18px; }
.chip { border:1px solid var(--line); border-radius:999px; padding:3px 12px; font-size:15px; background:#fafaf8; }
.grid { display:grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 22px; align-items:start; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
table { border-collapse: collapse; width:100%; font-size: 15.5px; font-variant-numeric: tabular-nums; }
th, td { text-align:left; padding: 6px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
thead th { font-size: 14px; color: var(--ink2); font-weight: 600; border-bottom: 2px solid #cfcec9; }
th.m { font-weight: 600; white-space: nowrap; }
td.spec { color: var(--ink2); white-space: nowrap; }
.at { color: var(--muted); font-size: 13.5px; }
.ok { color: var(--good); font-weight:700; } .bad { color: var(--bad); font-weight:700; }
tr.same td, tr.same th { color: var(--muted); }
td.ratio { white-space: nowrap; }
.na { color: var(--muted); }
section.fig, section.card { background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding: 20px 24px; margin-top: 22px; }
.cap { color: var(--ink2); margin: 0 0 12px; max-width: 72em; }
.svg svg { width: 100%; height: auto; display:block; }
details { margin-top: 12px; } summary { cursor: pointer; color: var(--ink2); }
ul.fails { columns: 2; font-size: 14px; color: var(--ink2); }
@media (max-width: 900px) { ul.fails { columns: 1; } }
.timeline { list-style: none; padding: 0; margin: 0; }
.timeline li { border-left: 3px solid var(--accent); padding: 4px 0 8px 14px; margin-bottom: 6px; }
.timeline .it { font-weight: 700; color: var(--ink2); margin-right: 8px; }
.cands { font-size: 14px; } .cands td { padding: 4px 8px; }
.scroll { max-height: 520px; overflow: auto; border:1px solid var(--line); border-radius: 8px; }
footer { color: var(--muted); font-size: 13.5px; margin-top: 26px; }
"""


def page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{E(title)}</title><style>{CSS}</style></head><body><main>{body}"
            f"<footer>Generated by viz.report · ngspice + SKY130 · two-stage op-amp from AMPICBench "
            f"(CC BY-NC 4.0) · {time.strftime('%Y-%m-%d %H:%M')}</footer></main></body></html>")


# --------------------------------------------------------------------------- build

def evaluate_pair(params, ref, spec, lib):
    jobs = [params] + ([ref] if ref is not None else [])
    with ThreadPoolExecutor(len(jobs)) as ex:
        res = list(ex.map(lambda p: oa.evaluate(p, spec=spec, lib=lib, curves=True), jobs))
    return res[0], (res[1] if ref is not None else None)


def design_sections(name, params, meta, ref_name, ref, spec, lib, figdir: Path, er=None, er_ref=None):
    """-> (header html, figures html, er, er_ref)"""
    from viz.bode import plot_bode
    from viz.heatmap import plot_heatmaps
    from viz.schematic import draw_schematic

    if er is None:
        er, er_ref = evaluate_pair(params, ref, spec, lib)
    svg_bode = save_fig(plot_bode(er, er_ref, spec, after_label=name, before_label=ref_name), figdir / "bode")
    svg_sch = save_fig(draw_schematic(params, er, ref, name, ref_name), figdir / "schematic")
    hm_items = [(name, er)] + ([(ref_name, er_ref)] if er_ref is not None else [])
    svg_heat = save_fig(plot_heatmaps(hm_items), figdir / "saturation")

    items = [(name, er)] + ([(ref_name, er_ref)] if er_ref is not None else [])
    top = (f"<div class='grid'><div>{worst_table(spec, items)}"
           f"{''.join(failures_block(e, n) for n, e in items)}</div>"
           f"<div>{knob_table(params, ref, name, ref_name)}</div></div>")
    figs = figure(
        svg_sch, "Schematic: who does what",
        "Each signal-path transistor is colored by its role and labelled with its size (multiplier × W/L in µm)"
        + (f", the size it had in <b>{E(ref_name)}</b>," if ref is not None else "")
        + " and its worst saturation margin over all PVT points. A red outline means the device misses its "
          "required margin somewhere.", "schematic")
    figs += figure(
        svg_bode, "Loop gain at every corner",
        "Tian loop gain at 5 process corners × 3 temperatures. Dots mark the unity-gain crossings; the phase "
        "there must stay above −120° (PM ≥ 60°) and the crossing must sit right of the UGF spec line."
        + (f" Gray: <b>{E(ref_name)}</b>, colored: <b>{E(name)}</b>." if er_ref is not None else ""), "bode")
    figs += figure(
        svg_heat, "Saturation headroom per device and corner",
        "Each cell is |Vds| − |Vdsat| minus the margin that device needs (mV). Blue = headroom, gray = at the "
        "limit, red = short; hatched cells are in triode.", "saturation")
    return top, figs, er, er_ref


def chips(spec: dict, lib: str) -> str:
    out = [f"{label} {_spec_txt(spec, key)}" for key, label, *_ in METRICS if key in spec]
    out += ["sat. margin ≥ 50 mV (in/load/driver), ≥ 0 (tail/sink)", "15 PVT: tt ff ss sf fs × −40/27/125 °C",
            f"models: {'slim' if lib == 'slim' else 'full'} sky130"]
    return "<div class='chips'>" + "".join(f"<span class='chip'>{E(c)}</span>" for c in out) + "</div>"


def build_design_report(design: str, compare: str | None, out: Path, lib: str) -> Path:
    t0 = time.perf_counter()
    name, params, meta = load_design(design)
    ref_name, ref, _ = load_design(compare) if compare else (None, None, None)
    if ref_name == name:
        ref_name = f"{ref_name} (ref)"
    spec = spec_of(meta)
    figdir = out.with_name(out.stem + "_files")
    top, figs, er, _ = design_sections(name, params, meta, ref_name, ref, spec, lib, figdir)
    note = meta.get("note", "") if meta else ""
    head = (f"<header class='top'><h1>{E(name)} {badge(er.passed)} <span class='sub'>· two-stage Miller op-amp, "
            f"SKY130</span></h1><p class='meta'>{E(note.rstrip('. ') + '.' if note else '')}"
            + (f" Compared against <b>{E(ref_name)}</b>." if ref is not None else "")
            + f" 15 PVT simulations in {er.seconds:.1f} s.</p>{chips(spec, lib)}{top}</header>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page(f"{name} op-amp report", head + figs))
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB) + figures in {figdir}/  [{time.perf_counter() - t0:.1f} s]")
    return out


def build_run_report(run_dir: Path, compare: str | None, out: Path, lib: str) -> Path:
    from viz.trajectory import (best_record, cand_no, load_history, load_jsonl, metric, plot_trajectory,
                                where, worker_of)
    t0 = time.perf_counter()
    run_id = run_dir.name
    recs = load_history(run_dir)
    if not recs:
        raise SystemExit(f"no candidates in {run_dir}/history.jsonl")
    summary = json.loads((run_dir / "summary.json").read_text()) if (run_dir / "summary.json").exists() else {}
    spec = spec_of({"spec": summary.get("spec")} if isinstance(summary.get("spec"), dict) else None)
    best = best_record(recs)
    if summary.get("best_n") is not None:
        best = next((r for r in recs if r.get("n") == summary["best_n"]), best)
    figdir = out.with_name(out.stem + "_files")
    svg_traj = save_fig(plot_trajectory(recs, spec, title=run_id, best=best), figdir / "trajectory")

    n_pass = sum(1 for r in recs if r.get("passed"))
    first = next((cand_no(r) for r in recs if r.get("passed")), None)
    workers = sorted({worker_of(r) for r in recs})
    facts = [f"{len(recs)} candidates", f"{n_pass} passed", f"first pass: #{first}" if first else "no pass yet",
             f"workers: {', '.join(workers)}"]
    if summary.get("decks"):
        facts.append(f"{summary['decks']} ngspice decks")
    if summary.get("llm_calls"):
        facts.append(f"{summary['llm_calls']} LLM calls")

    body = figure(svg_traj, "How the agents searched",
                  "Worst-corner value of each metric for every candidate, in evaluation order, colored by the worker "
                  "that proposed it. Dashed red lines are the spec; filled dots passed all 15 PVT points; the star is "
                  "the design detailed below.", "trajectory")
    arch = load_jsonl(run_dir / "architect.jsonl")
    if arch:
        lis = []
        for a in arch:
            dirs = "; ".join(f"<b>{E(str(d.get('focus', '')))}</b>" for d in a.get("directions", []) if isinstance(d, dict))
            lis.append(f"<li><span class='it'>it{E(str(a.get('iter', '?')))}</span>"
                       f"after {E(str(a.get('n_evaluated', '?')))} candidates: {E(str(a.get('diagnosis', '')))}"
                       + (f"<br><span class='at'>directions: {dirs}</span>" if dirs else "") + "</li>")
        body += (f"<section class='card'><h2>Architect diagnoses</h2><p class='cap'>What the lead agent concluded "
                 f"each time the search stalled, and the directions it handed to the workers.</p>"
                 f"<ul class='timeline'>{''.join(lis)}</ul></section>")
    rows = []
    for r in recs:
        ok = r.get("passed")
        cells = "".join(f"<td>{_fmt(k, v) if (v := metric(r, k)) is not None else '—'}"
                        f"{(' <span class=at>' + E(where(r, k)) + '</span>') if v is not None and where(r, k) else ''}</td>"
                        for k in ("pm_deg", "ugf_hz", "dc_gain_db", "i_core_a", "min_sat_margin_v"))
        rows.append(f"<tr><td>#{cand_no(r)}</td><td>{E(worker_of(r))}</td>"
                    f"<td>{'<span class=ok>✓</span>' if ok else '<span class=bad>✗</span>'}</td>{cells}"
                    f"<td>{E(str(r.get('rationale') or ''))}</td></tr>")
    body += ("<section class='card'><h2>All candidates</h2><details><summary>show the table "
             f"({len(recs)} rows, with each worker's rationale)</summary><div class='scroll'><table class='cands'>"
             "<thead><tr><th>#</th><th>worker</th><th>pass</th><th>PM</th><th>UGF</th><th>gain</th><th>I core</th>"
             f"<th>sat</th><th>rationale</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></details></section>")

    params = summary.get("best_params") or best.get("params") or {}
    params = {k: v for k, v in params.items() if k in oa.SPACE}
    name = f"best #{cand_no(best)}"
    ref_name, ref, _ = load_design(compare) if compare else (None, None, None)
    top, figs, er, _ = design_sections(name, params, {}, ref_name, ref, spec, lib, figdir)
    head = (f"<header class='top'><h1>Swarm run {E(run_id)} <span class='sub'>· best design {badge(er.passed)}"
            f"</span></h1><p class='meta'>{E(' · '.join(facts))}. Best candidate re-simulated here "
            f"({'slim' if lib == 'slim' else 'full'} models): #{cand_no(best)} from <b>{E(worker_of(best))}</b>"
            + (f" — “{E(str(best.get('rationale')))}”" if best.get("rationale") else "")
            + f"</p>{chips(spec, lib)}{top}</header>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page(f"Swarm run {run_id}", head + body + figs))
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB) + figures in {figdir}/  [{time.perf_counter() - t0:.1f} s]")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--design", help="design JSON ({'params': {...}} or flat knobs) or 'default'")
    g.add_argument("--run", type=Path, help="swarm run directory (runs/swarm/<run_id>)")
    ap.add_argument("--compare", help="reference design for before/after ('default' or a JSON path); "
                                      "--run defaults to 'default', pass 'none' to disable")
    ap.add_argument("--out", type=Path, help="output .html (default runs/viz/<name>.html)")
    ap.add_argument("--lib", default="slim", choices=("slim", "full"))
    a = ap.parse_args()
    compare = None if (a.compare or "").lower() == "none" else a.compare
    if a.run:
        compare = "default" if a.compare is None else compare
        out = a.out or oa.ROOT / "runs" / "viz" / f"{a.run.name}.html"
        build_run_report(a.run, compare, out, a.lib)
    else:
        stem = "default" if a.design == "default" else Path(a.design).stem
        out = a.out or oa.ROOT / "runs" / "viz" / f"{stem}.html"
        build_design_report(a.design, compare, out, a.lib)


if __name__ == "__main__":
    main()
