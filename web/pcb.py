"""PCB routing part of the site: PCBWorld hard tier, read from runs/pcb/ at build time."""
from __future__ import annotations

import re

from pcb.site.data import load_pairs, short
from web import council
from web.kit import (E, RUNS, asset, chip, hbars, jload, key, line_chart, pane, switch, table)

PCB = RUNS / "pcb"
HARD = ["0400_laptimer58_Chickadee", "0423_induction-hob_temperature-sender", "0448_uedaino_uedaino",
        "0473_data-manager_data-manager", "0496_kitspace_f-91w",
        "0520_RGBMatrixPanelCPLD-PhotonBackpack_RGBMatrixPanel_CPLD_negative", "0546_ozinverter_ozinverterkicad",
        "0571_Brushless_ESC_Brushless_ESC", "0599_RGBMatrixPanelCPLD-PhotonBackpack_RGBMatrixPanel_CPLD",
        "0626_TOBS_HybridChargeController"]
PUBLISHED = {"Freerouting": 3, "PCBRouteAgent": 4}
AGENT_COLOR = {"careful": "var(--s1)", "bold": "var(--s2)", "layer-first": "var(--s3)", "power-aware": "var(--s4)",
               "router": "var(--s0)", "lead": "var(--text)"}
TEMPER = {"careful": "smallest change first", "bold": "clears space decisively",
          "layer-first": "tries a via bridge first", "power-aware": "protects power nets"}


def acolor(a: str) -> str:
    return AGENT_COLOR.get(a, "var(--s0)")


def md(s: str) -> str:
    s = E(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", s).replace("\n", " ")


# ------------------------------------------------------------------ data
def tier(rows: list[dict]) -> dict:
    ctl = (jload(PCB / "hard_control" / "summary.json", {}) or {}).get("results", [])
    start = {r["board"] for r in ctl if (r.get("start") or {}).get("clean_pass")}
    scripted = {r["board"] for r in ctl if (r.get("final") or {}).get("clean_pass")} - start
    agents = {r["board"] for r in rows if r.get("finished") and r.get("hard_test") and r["agent"]["best_clean"]}
    agents -= start | scripted
    council_clean = council.clean_boards() - start - scripted - agents
    spec = jload(PCB / "hard_specialist_final" / "MANIFEST.json", {}) or {}
    spec_clean = {b for b, v in (spec.get("boards") or {}).items() if v.get("clean_pass")}
    rnd = {r["board"] for r in rows if r.get("finished") and r.get("hard_test") and r.get("random")
           and r["random"]["best_clean"]}
    status = {}
    for b in HARD:
        status[b] = ("base" if b in start else "script" if b in scripted else "agent" if b in agents
                     else "council" if b in council_clean
                     else "empty")
    return {"start": start, "scripted": scripted, "agents": agents, "spec_clean": spec_clean,
            "random_clean": rnd, "status": status, "council": council_clean, "ours": len(start) + len(scripted) + len(agents) + len(council_clean)}


# ------------------------------------------------------------------ pieces
def ranking(t: dict) -> str:
    def sq(cls_list):
        out = []
        for b, cls in cls_list:
            out.append(f'<i class="sq {cls}" title="{E(short(b)) if b else ""}"></i>')
        return "".join(out + ['<i class="sq empty"></i>'] * (10 - len(out)))

    rows = []
    for name, n in PUBLISHED.items():
        sub = "classic autorouter, published" if name == "Freerouting" else "LLM routing agent, published"
        rows.append(f'<div class="rrow"><div class="who"><b>{name}</b><span>{sub}</span></div>'
                    f'<div class="sqs">{sq([(None, "base")] * n)}</div><div class="n">{n}<small>/10</small></div></div>')
    order = [(b, "base") for b in HARD if b in t["start"]] + [(b, "script") for b in HARD if b in t["scripted"]] + \
            [(b, "agent") for b in HARD if b in t["agents"]] + [(b, "council") for b in HARD if b in t["council"]]
    rows.append(f'<div class="rrow ours"><div class="who"><b>routr</b><span>router + repair + Gemini 4 agents + council</span></div>'
                f'<div class="sqs">{sq(order)}</div><div class="n">{t["ours"]}<small>/10</small></div></div>')
    legend = (f'<div class="legend"><span><i style="background:var(--text-soft)"></i>classic router alone ({len(t["start"])})</span>'
              f'<span><i style="background:var(--sky)"></i>+ our scripted repair, no model (+{len(t["scripted"])})</span>'
              f'<span><i style="background:var(--mint)"></i>+ Gemini 4 step agents (+{len(t["agents"])}'
              f'{": " + E(", ".join(short(b) for b in t["agents"])) if t["agents"] else ""})</span>'
              f'<span><i style="background:var(--lilac)"></i>+ Gemini council (+{len(t["council"])}'
              f'{": " + E(", ".join(short(b) for b in t["council"])) if t["council"] else ""})</span></div>')
    return f'<div class="rank" data-inview>{"".join(rows)}</div>{legend}'


def tier_table(t: dict, rows: list[dict]) -> str:
    by = {r["board"]: r for r in rows if r.get("finished")}
    label = {"base": '<span class="dim">classic router</span>', "script": "scripted repair",
             "agent": '<span class="ok-t">Gemini step agents</span>',
             "council": '<span class="ok-t">Gemini council</span>', "empty": '<span class="warn-t">not clean yet</span>'}
    out = []
    for b in HARD:
        s = t["status"][b]
        extra = []
        if b in t["spec_clean"]:
            extra.append(chip("Managed Agent: clean", "ok"))
        if b in t["random_clean"]:
            extra.append(chip("random control: clean", ""))
        cc = council.load(council.AGENT_RUN, b)
        if cc and cc["final"] and b not in t["council"]:
            extra.append(chip(f'council: {cc["final"]} open', "open"))
        r = by.get(b)
        if r and not r["agent"]["best_clean"]:
            extra.append(chip(f'agents: {r["agent"]["start_open"]} → {r["agent"]["best_open"]} open', "open"))
        out.append([f"<b>{E(short(b))}</b>", f"<td>{'<span class=ok-t>clean</span>' if s != 'empty' else '<span class=warn-t>not clean</span>'}</td>",
                    f"<td>{label[s]}</td>", f'<td><div class="chips" style="justify-content:flex-end">{"".join(extra)}</div></td>'])
    return table(["Board", "Result", "Fixed by", "Other arms"], out)


def flow() -> str:
    nodes = [
        ("Classic router", "Freerouting", "Routes about 97% of each board, then leaves a few connections open.", ""),
        ("Lead", "gemini-4-carbon", "Looks at a picture of the board and writes one strategy note for the crew.", "ai"),
        ("Four step agents", "gemini-4-hydrogen", "Careful, bold, layer-first and power-aware, each on its own copy of the board.", "ai"),
        ("KiCad acts", "router + rule check", "Every step is one action: route, route with a via, rip up, roll back. KiCad does the copper.", ""),
        ("Judge", "PCBWorld evaluate_one", "Scores every saved board. The best board is the one submitted.", "judge"),
    ]
    h = "".join(f'<div class="node {c}"><h4>{E(a)}</h4><span class="m">{E(m)}</span><p>{E(d)}</p></div>' for a, m, d, c in nodes)
    loop = ('<div class="loop"><b>Each step:</b>' + '<span class="arrow">→</span>'.join(
        f'<span class="chip lit" style="animation-delay:{i * .8:.1f}s">{w}</span>'
        for i, w in enumerate(["observe fresh facts", "decide one action", "KiCad routes", "rule check judges",
                               "keep the best or roll back"])) + '<span class="soft">up to 20 steps per agent</span></div>')
    side = ('<div class="grid3" style="margin-top:20px">'
            '<div class="card"><h4>Control arm</h4><p class="soft" style="margin:6px 0 0">A random policy with the same actions and the same step budget. No model.</p></div>'
            '<div class="card"><h4>Blocker screen</h4><span class="m mono" style="color:var(--sky-text);font-size:13px">gemini-decision-1</span>'
            '<p class="soft" style="margin:6px 0 0">Names the routed nets that wall off an open connection (whole-plan arm).</p></div>'
            '<div class="card"><h4>Managed Agent specialist</h4><span class="mono" style="color:var(--sky-text);font-size:13px">gemini-4-hydrogen, long-running</span>'
            '<p class="soft" style="margin:6px 0 0">One agent per stuck board, with coarse tools on the same engine, for boards nothing else fixes.</p></div></div>')
    return f'<div class="flow">{h}</div>{loop}{side}'


def board_chart(ag: dict, rnd: dict | None) -> str:
    series, keys = [], []
    if rnd:
        for a in rnd["agents"]:
            series.append({"name": a["agent"], "color": "var(--s0)", "dashed": True,
                           "pts": list(enumerate(a["traj"]))})
        keys.append(("random control", "var(--s0)", True))
    for a in ag["agents"]:
        tips = [f'{a["agent"]} · start · {a["traj"][0]} open'] + [
            f'{a["agent"]} · step {i + 1} · {s["action"]}{" net " + ", ".join(map(str, s["nets"])) if s.get("nets") else ""}'
            f' · {s["before"]} → {s["after"]} open' for i, s in enumerate(a["steps"])]
        series.append({"name": a["agent"], "color": acolor(a["agent"]), "pts": list(enumerate(a["traj"])),
                       "tips": tips[:len(a["traj"])]})
        keys.insert(len(keys) - (1 if rnd else 0), (f'{a["agent"]} <b class="mono">{a["traj"][-1]}</b>', acolor(a["agent"]), False))
    return line_chart(series, step=True, xlabel="step", ymin=0, hlines=[(0, "clean")]) + key(keys)


def feed(ag: dict) -> str:
    items = []
    if ag.get("lead_note"):
        items.append(f'<div class="step lead" data-agent="lead"><div class="h"><b><i style="background:var(--text)"></i>Lead</b>'
                     f'<span class="act">{E(ag.get("lead_model") or "gemini-4-carbon")}</span>'
                     f'<span class="soft">strategy note from the board picture</span></div><p>{md(ag["lead_note"])}</p></div>')
    depth = max([len(a["steps"]) for a in ag["agents"]] + [0])
    for i in range(depth):
        for a in ag["agents"]:
            if i >= len(a["steps"]):
                continue
            s = a["steps"][i]
            b, af = s.get("before"), s.get("after")
            cls = "good" if (af is not None and b is not None and af < b) else "warn" if (af or 0) > (b or 0) else ""
            nets = ", ".join(str(n) for n in (s.get("nets") or []))
            done = " · clean" if af == 0 and not s.get("err_after") else ""
            items.append(f'<div class="step" data-agent="{E(a["agent"])}"><div class="h"><b><i style="background:{acolor(a["agent"])}"></i>'
                         f'{E(a["agent"])}</b><span class="soft">step {i + 1}</span>'
                         f'<span class="act">{E(s["action"])}{" net " + E(nets) if nets else ""}</span>'
                         f'<span class="res {cls}">{b} → {af} open{done}</span></div><p>{E(s.get("thought") or "")}</p></div>')
    names = ["lead"] * bool(ag.get("lead_note")) + [a["agent"] for a in ag["agents"]]
    bar = "".join(f'<button data-filter="{E(n)}"><i style="background:{acolor(n)}"></i>{E(n)}</button>' for n in names)
    return f'<div class="feedbar">{bar}</div><div class="feed">{"".join(items)}</div>'


def result_line(side: dict) -> str:
    return (f'{side["start_open"]} open → 0 · clean pass' if side["best_clean"]
            else f'{side["start_open"]} open → {side["best_open"]} open')


def board_view(r: dict) -> str:
    ag, rnd, b = r["agent"], r["random"], r["board"]
    from pathlib import Path
    d = Path(ag["dir"])
    best = next((a for a in ag["agents"] if a["agent"] == ag["best_agent"]), None)
    chips = [chip(f'Gemini agents: {E(result_line(ag))}' + (f' in {best["n_steps"]} steps' if best and ag["best_clean"] else ""),
                  "ok" if ag["best_clean"] else "open")]
    if rnd:
        rb = next((a for a in rnd["agents"] if a["agent"] == rnd["best_agent"]), None)
        chips.append(chip(f'random control: {E(result_line(rnd))}' + (f' in {rb["n_steps"]} steps' if rb and rnd["best_clean"] else "")))
    chips.append(chip(f'{ag["llm_calls"]} Gemini calls · {ag["tokens"]:,} tokens', "ai"))
    vid = Path(__file__).resolve().parents[1] / "runs" / "site" / "video" / f"{b}.mp4"
    v = asset(vid, f"video/{b}.mp4")
    vposter = asset(vid.with_suffix(".png"), f"video/{b}.png")
    s_img, f_img = asset(d / "start.png", f"pcb/{b}_start.png"), asset(d / "final.png", f"pcb/{b}_final.png")
    video = (f'<figure class="frame" style="margin-bottom:16px"><video src="{v}" poster="{vposter}" controls muted loop playsinline preload="metadata"></video></figure>'
             if v else "")
    ba = (f'<div class="ba"><figure class="frame"><img loading="lazy" src="{s_img}" alt="board before"><figcaption>before · classic router · {ag["start_open"]} open</figcaption></figure>'
          f'<figure class="frame"><img loading="lazy" src="{f_img}" alt="board after"><figcaption>after · {E(ag["best_agent"])} · {ag["best_open"]} open</figcaption></figure></div>')
    tag = "hard-tier test board" if r.get("hard_test") else "development board (not scored in the tier)"
    return f"""
<div class="bhead"><div><div class="eyebrow">{tag}</div><h3>{E(short(b))}</h3></div><div class="chips">{"".join(chips)}</div></div>
<div class="grid2">
  <div class="stack">{video}{ba}
    <div class="card"><div class="chead"><h4>Open connections after each step</h4><span class="soft" style="font-size:13.5px">a rip-up pushes it up first</span></div>
      {board_chart(ag, rnd)}</div></div>
  <div class="card"><div class="chead"><h4>What Gemini decided</h4><span class="soft" style="font-size:13.5px">straight from llm_calls.jsonl</span></div>{feed(ag)}</div>
</div>"""


def explorer(rows: list[dict]) -> str:
    fin = [r for r in rows if r.get("finished") and r["agent"]["start_open"]]
    fin.sort(key=lambda r: (not r.get("hard_test"), not r["agent"]["best_clean"], r["board"]))
    pend = [r for r in rows if not r.get("finished")]
    if not fin:
        return '<p class="soft">No finished step-agent runs yet.</p>'
    items = [(f"b{i}", E(short(r["board"]))) for i, r in enumerate(fin)]
    dots = ["var(--mint)" if r["agent"]["best_clean"] else "var(--butter)" for r in fin]
    body = "".join(pane("board", f"b{i}", board_view(r), i == 0) for i, r in enumerate(fin))
    note = (f'<p class="soft" style="margin-top:16px;font-size:14px">Still running when this page was built: '
            f'{E(", ".join(short(r["board"]) for r in pend))}. Rebuild the site to pick them up.</p>' if pend else "")
    return switch("board", items, dots) + body + note


def control_table(rows: list[dict]) -> tuple[str, str]:
    trs, tot = [], {"n": 0, "ac": 0, "rc": 0, "open": 0, "acl": 0, "rcl": 0, "rn": 0, "calls": 0, "tok": 0}
    for r in rows:
        if not r.get("finished") or not r["agent"]["start_open"]:
            continue
        a, c = r["agent"], r["random"]
        ba = next((x for x in a["agents"] if x["agent"] == a["best_agent"]), None)
        tot["n"] += 1; tot["ac"] += a["best_clean"]; tot["open"] += a["start_open"]
        tot["acl"] += a["start_open"] - a["best_open"]; tot["calls"] += a["llm_calls"]; tot["tok"] += a["tokens"]
        end = lambda s: '<span class="ok-t">0 · clean</span>' if s["best_clean"] else f'{s["best_open"]} open'
        row = [f'<b>{E(short(r["board"]))}</b> <span class="soft">{"hard test" if r.get("hard_test") else "dev"}</span>',
               f'<td class="n">{a["start_open"]}</td>', f'<td class="n hl">{end(a)}</td>',
               f'<td class="n hl">{ba["n_steps"] if ba else "–"}<span class="dim"> / {a["steps_total"]}</span></td>',
               f'<td class="n hl">{a["llm_calls"]}</td>', f'<td class="n hl">{a["tokens"] / 1000:.0f}k</td>']
        if c:
            bc = next((x for x in c["agents"] if x["agent"] == c["best_agent"]), None)
            tot["rn"] += 1; tot["rc"] += c["best_clean"]; tot["rcl"] += c["start_open"] - c["best_open"]
            row += [f'<td class="n">{end(c)}</td>', f'<td class="n">{bc["n_steps"] if bc else "–"}<span class="dim"> / {c["steps_total"]}</span></td>']
        else:
            row += ['<td class="n dim">running</td>', '<td class="n dim">–</td>']
        trs.append(row)
    kp = (f'<div class="kpis" style="margin:0 0 24px"><div class="kpi ok"><b>{tot["ac"]}<small>/{tot["n"]}</small></b><span>boards brought to a clean pass by the agents</span></div>'
          f'<div class="kpi"><b>{tot["rc"]}<small>/{tot["rn"]}</small></b><span>by the random control</span></div>'
          f'<div class="kpi ok"><b>{tot["acl"]}<small>/{tot["open"]}</small></b><span>open connections closed by the agents</span></div>'
          f'<div class="kpi"><b>{tot["rcl"]}</b><span>closed by the random control</span></div></div>')
    tb = table(["Board", "Open at start", "Agents: end", "steps best / all", "Gemini calls", "tokens", "Random: end", "steps best / all"], trs)
    return kp, tb


def scoreboard() -> str:
    """Every method on the hard boards nothing had fixed, all scored by PCBWorld's evaluate_one."""
    sb = jload(PCB / "scoreboard" / "scoreboard.json")
    if not sb:
        return ""
    sp = jload(PCB / "scoreboard_specialist" / "scoreboard.json") or {}
    labels = {"freerouting": "Freerouting", "scripted": "Scripted", "whole-plan-agents": "Whole-plan agents",
              "random-steps": "Random steps", "gemini-steps": "Gemini step agents",
              "specialist": "Managed Agent"}
    methods = list(sb["methods"])
    if "specialist" not in methods and sp:
        methods.append("specialist")
    ours = {"gemini-steps", "council"}

    def cell(v, m):
        c = " hl" if m in ours else ""
        if not v or v.get("routability") is None:
            return f'<td class="n dim{c}">no board</td>'
        if v.get("clean_pass"):
            return f'<td class="n{c}"><span class="ok-t">clean pass</span></td>'
        return f'<td class="n{c}">{v["routability"] * 100:.1f}%<span class="dim"> · {v.get("drv_errors_only_count", "?")}</span></td>'

    trs, clean = [], {m: 0 for m in methods + ["council"]}
    for b in sb["boards"]:
        row = [f"<b>{E(short(b))}</b>"]
        for m in methods:
            v = (sb["scores"].get(m) or {}).get(b)
            if v is None and m == "specialist":
                if b not in (sp.get("boards") or []):
                    row.append('<td class="n dim">not run</td>'); continue
                v = ((sp.get("scores") or {}).get("specialist") or {}).get(b)
            clean[m] += bool(v and v.get("clean_pass"))
            row.append(cell(v, m))
        cc = council.load(council.AGENT_RUN, b)
        sbc = (((jload(PCB / f"scoreboard_{b[:4]}" / "scoreboard.json") or {}).get("scores") or {}).get("council") or {}).get(b)
        if sbc:
            clean["council"] += bool(sbc.get("clean_pass"))
            row.append(cell(sbc, "council"))
        elif cc:
            row.append(f'<td class="n hl">{cc["final"]} open<span class="dim"> · unscored</span></td>')
        else:
            row.append('<td class="n dim hl">not run</td>')
        trs.append(row)
    n = len(sb["boards"])
    trs.append(["clean boards"] + [f'<td class="n{" hl" if m in ours else ""}">{clean[m]} of {n}</td>' for m in methods + ["council"]])
    return table(["Board"] + [labels.get(m, m) for m in methods] + ["Gemini council"], trs).replace(
        "<tr><td>clean boards", '<tr class="tot"><td>clean boards')

def limits(rows: list[dict], t: dict) -> str:
    fin = [r for r in rows if r.get("finished") and r["agent"]["start_open"]]
    fails = [r for r in fin if not r["agent"]["best_clean"]]
    li = []
    if fails:
        li.append(("fail", "<b>Still not clean after the step agents:</b> " + E(", ".join(
            f'{short(r["board"])} ({r["agent"]["start_open"]} → {r["agent"]["best_open"]} open)' for r in fails))
            + ". One open connection fails the whole board."
            + (" The council later brought " + E(", ".join(short(b) for b in t["council"])) + " to a clean pass." if t["council"] else "")))
        w = max(fails, key=lambda r: r["agent"]["best_open"])
        ba = next((x for x in w["agent"]["agents"] if x["agent"] == w["agent"]["best_agent"]), None) or \
            min(w["agent"]["agents"], key=lambda x: x["open"] or 99, default=None)
        if ba and ba["steps"]:
            li.append(("", f'<b>Why, in an agent\'s own words</b> ({E(short(w["board"]))}, {E(ba["agent"])}, last step): '
                           f'<q>{E(ba["steps"][-1]["thought"] or "")}</q>'))
    both = t["agents"] & t["random_clean"]
    if both:
        li.append(("", "<b>The random control also reached a clean pass on</b> " + E(", ".join(short(b) for b in both))
                   + ". On that board the step loop and KiCad's router are enough on their own; the agents needed fewer steps."))
    li.append(("", "<b>Freerouting does most of the routing</b> (about 97% of each board). The agents finish the last few connections."))
    li.append(("", f'<b>{len(t["start"]) + len(t["scripted"])} of our {t["ours"]} clean boards need no model</b>: the router and the scripted repair. '
                   f'The step agents added {len(t["agents"])} and the council {len(t["council"])}.'))
    li.append(("", "<b>One run per arm.</b> No repeated seeds, so no error bars. Every saved board is scored with PCBWorld's evaluate_one."))
    return '<ul class="limits">' + "".join(f'<li class="{c}">{x}</li>' for c, x in li) + "</ul>"


def studio() -> str:
    return """
<div class="card">
  <div class="chead"><div><h3>routr studio</h3><p class="soft" style="margin:4px 0 0">Pick a board, press Route, and watch the solver's versions arrive while KiCad's rule check judges each one. For any connection the agents could not make, click it: Gemini 4 Neon looks at a close-up and says why it is stuck and the most practical fix, and the Decisions API gives a calibrated chance that moving one track would fix it. Replays play recorded runs at their original pace.</p></div>
    <div class="chips"><a class="btn lilac" href="http://127.0.0.1:8766/" target="_blank" rel="noopener">Open the studio</a></div></div>
  <figure class="frame live" data-live="http://127.0.0.1:8766/" style="background:var(--sunk);min-height:120px">
    <div class="soft livehint" style="padding:28px;text-align:center">The studio is not running. Start it with <code>web/serve.sh</code> (or <code>pcb/run_studio.sh 8766</code>) and reload this page.</div>
  </figure>
</div>"""


def section() -> str:
    rows = load_pairs()
    t = tier(rows)
    kp, tb = control_table(rows)
    return f"""
<section class="band" id="pcb">
  <div class="wrap">
    <span class="part">Part 1 · PCB routing</span>
    <div class="sechead"><div><h2>A circuit board with one unconnected wire is scrap.</h2>
      <p class="lede">PCBWorld's hard tier: 10 real boards. A board passes only when <b>every connection is routed</b> and KiCad's rule checker finds <b>zero errors</b>. Boards that pass, out of 10:</p></div></div>
    {ranking(t)}
    <details class="more"><summary>Every hard-tier board and who fixed it</summary><div style="margin-top:12px">{tier_table(t, rows)}</div></details>
  </div>
</section>
<section class="band alt" id="pcb-how">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot" style="background:var(--sky)"></span>How the crew works</div>
      <h2>The router does the bulk. Gemini agents finish the board, one checked step at a time.</h2></div></div>
    {flow()}
  </div>
</section>
<section class="band" id="pcb-boards">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot"></span>Real runs</div><h2>Watch the agents work a board</h2>
      <p class="lede">Every line below is the agent's own reasoning from the run log. Hover the chart for each step; filter the feed by agent.</p></div></div>
    {explorer(rows)}
  </div>
</section>
<section class="band alt" id="pcb-proof">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot"></span>Proof against a baseline</div>
      <h2>Did the agents matter?</h2><p class="lede">Same actions, same step budget, random choices. Scored by PCBWorld's evaluate_one.</p></div></div>
    {kp}{tb}
    <h3 style="margin:48px 0 8px">The hard boards nothing had fixed, every method, one scorer</h3>
    <p class="soft">Cell = share of connections routed · error-level rule violations left. All scored by PCBWorld's evaluate_one, except where a council run has not been scored yet.</p>
    {scoreboard()}
  </div>
</section>
{council.section()}
<section class="band alt" id="pcb-studio">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot" style="background:var(--lilac)"></span>Live</div><h2>Route a board yourself</h2></div></div>
    {studio()}
    <h3 style="margin:48px 0 16px">What still fails</h3>
    {limits(rows, t)}
  </div>
</section>"""


def hero_numbers() -> dict:
    rows = load_pairs()
    t = tier(rows)
    return {"ours": t["ours"], "agents": len(t["agents"])}
