"""Build the demo site: runs/site/index.html (one self-contained page) and runs/site/cards.html.

    .venv/bin/python -m pcb.site.build

Reads runs/site/pick.json (pcb.site.pick), the step-agent runs under runs/pcb/, the older arms
(hard_control, hard_agents) and runs/pcb/scoreboard/scoreboard.json. Every number and every line of
agent text on the page comes from those files. Videos are referenced as video/<board>.mp4.
The page is a 1920x1080 stage scaled to the window; arrow keys move between sections; #3 opens
section 3. cards.html holds the still cards used by pcb.site.summary for the summary video.
"""
from __future__ import annotations

import base64
import html
import json
import re
from pathlib import Path

from pcb.site.data import ROOT, RUNS, _json, load_pairs, short

SITE = ROOT / "runs" / "site"
TIER_BOARDS = 10
PUBLISHED = {"freerouting": 3, "pcbrouteagent": 4, "humans": 9}
COLORS = {"careful": "#6fd3b4", "bold": "#ff9a62", "layer-first": "#b9a2f5", "power-aware": "#f08bb4",
          "random": "#9aa29e", "lead": "#ffffff"}
TEMPER = {"careful": "smallest change first", "bold": "clears space decisively",
          "layer-first": "tries a via bridge first", "power-aware": "protects power nets"}
E = html.escape


def color(agent: str) -> str:
    return COLORS.get(agent, COLORS["random"] if str(agent).startswith("random") else "#e6ebe7")


def img_uri(p: Path) -> str:
    """PNG as a data URI, with the white margin cropped (the page shows it inverted, on black)."""
    if not p.is_file():
        return ""
    data = p.read_bytes()
    try:
        import io

        from PIL import Image, ImageChops
        im = Image.open(p).convert("RGB")
        box = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).getbbox()
        if box:
            im = im.crop((max(0, box[0] - 8), max(0, box[1] - 8), min(im.width, box[2] + 8), min(im.height, box[3] + 8)))
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        data = buf.getvalue()
    except Exception:
        pass
    return "data:image/png;base64," + base64.b64encode(data).decode()


def md_inline(s: str) -> str:
    """Lead notes use **bold** and `code`; render those two, escape the rest."""
    s = E(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s.replace("\n\n", "<br>").replace("\n", " ")


# ---------------------------------------------------------------- data
def tier_counts(rows: list[dict]) -> dict:
    """Clean boards on the hard tier: classic router, + scripted repair, + step agents."""
    ctl = _json(RUNS / "hard_control" / "summary.json", {}) or {}
    res = ctl.get("results", [])
    start_clean = {r["board"] for r in res if (r.get("start") or {}).get("clean_pass")}
    scripted_clean = {r["board"] for r in res if (r.get("final") or {}).get("clean_pass")}
    agents_clean = {r["board"] for r in rows if r.get("finished") and r.get("hard_test") and r["agent"]["best_clean"]}
    random_clean = {r["board"] for r in rows if r.get("finished") and r.get("hard_test") and r.get("random")
                    and r["random"]["best_clean"]}
    return {"start": len(start_clean) or PUBLISHED["freerouting"],
            "scripted_added": sorted(scripted_clean - start_clean),
            "agents_added": sorted(agents_clean - scripted_clean),
            "random_also": sorted(random_clean & (agents_clean - scripted_clean)),
            "whole_plan_added": max(0, ((_json(RUNS / "hard_agents" / "summary.json", {}) or {}).get("final_clean") or 0)
                                    - len(scripted_clean)) if scripted_clean else 0}


# ---------------------------------------------------------------- pieces
def squares(n_parts: list[tuple[int, str]], total: int = TIER_BOARDS, delay0: float = 0.0) -> str:
    out, i = [], 0
    for n, cls in n_parts:
        for _ in range(n):
            out.append(f'<i class="sq {cls}" style="animation-delay:{delay0 + i * 0.12:.2f}s"></i>')
            i += 1
    out += ['<i class="sq empty"></i>'] * (total - i)
    return "".join(out)


def slide_hook(tc: dict) -> str:
    s, sc, ag = tc["start"], len(tc["scripted_added"]), len(tc["agents_added"])
    ours = s + sc + ag
    rows = [
        ("Freerouting", "classic autorouter", [(PUBLISHED["freerouting"], "base")], PUBLISHED["freerouting"], ""),
        ("PCBRouteAgent", "Claude Opus 5.5, published", [(PUBLISHED["pcbrouteagent"], "base")], PUBLISHED["pcbrouteagent"], ""),
        ("Ours", "router + repair + Gemini 4 step agents", [(s, "base"), (sc, "script"), (ag, "agent")], ours, "ours"),
        ("Human engineers", "published", [(PUBLISHED["humans"], "human")], PUBLISHED["humans"], ""),
    ]
    bars = "".join(
        f'<div class="brow {cls}"><div class="bl"><b>{E(n)}</b><span>{E(sub)}</span></div>'
        f'<div class="sqs">{squares(parts, delay0=0.3 + k * 0.5)}</div><div class="bn">{tot}<small>/{TIER_BOARDS}</small></div></div>'
        for k, (n, sub, parts, tot, cls) in enumerate(rows))
    names = ", ".join(short(b) for b in tc["agents_added"]) or "none yet"
    return f"""
<section class="slide" data-title="The problem">
  <div class="kicker">PCBWorld benchmark · hard tier · 10 real boards</div>
  <h1>A circuit board with one unconnected wire is <em>scrap</em>.</h1>
  <p class="lede">Clean pass = every connection routed <b>and</b> zero design-rule errors. Boards that pass, out of 10:</p>
  <div class="bars">{bars}</div>
  <div class="legend">
    <span><i class="sq base on"></i> classic router alone ({s})</span>
    <span><i class="sq script on"></i> + our scripted repair, no model (+{sc})</span>
    <span><i class="sq agent on"></i> + Gemini 4 step agents (+{ag}: {E(names)})</span>
  </div>
</section>"""


def diagram() -> str:
    lanes = [("careful", 120), ("bold", 220), ("layer-first", 320), ("power-aware", 420)]
    cols = [(900, "observe", "fresh facts"), (1040, "decide", "one action"), (1180, "KiCad router", "acts"),
            (1320, "rule checker", "judges"), (1460, "keep best", "or roll back")]
    p = []
    p.append('<svg class="dia" viewBox="0 0 1840 800" xmlns="http://www.w3.org/2000/svg">')
    p.append('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
             '<path d="M0 0 L10 5 L0 10 z" fill="#5c6b63"/></marker></defs>')

    def node(x, y, w, h, title, model, lines, cls=""):
        t = [f'<g class="node {cls}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16"/>',
             f'<text class="nt" x="{x + 20}" y="{y + 42}">{E(title)}</text>']
        yy = y + 74
        if model:
            t.append(f'<text class="nm" x="{x + 20}" y="{yy}">{E(model)}</text>')
            yy += 32
        for ln in lines:
            t.append(f'<text class="nn" x="{x + 20}" y="{yy}">{E(ln)}</text>')
            yy += 27
        t.append("</g>")
        return "".join(t)

    def flow(d, dur, begin, col="#ffe14d", dashed=False):
        cls = "wire dash" if dashed else "wire"
        return (f'<path class="{cls}" d="{d}" marker-end="url(#ah)"/>'
                f'<circle r="7" fill="{col}" class="tok"><animateMotion dur="{dur}s" begin="{begin}s" '
                f'repeatCount="indefinite" path="{d}"/></circle>')

    for x, a, b in cols:
        p.append(f'<text class="ch" x="{x}" y="34" text-anchor="middle">{E(a)}</text>'
                 f'<text class="cs" x="{x}" y="60" text-anchor="middle">{E(b)}</text>')
    p.append(flow("M250 270 L296 270", 1.2, 0))
    for i, (name, y) in enumerate(lanes):
        c = color(name)
        p.append(flow(f"M560 270 C 592 270, 588 {y}, 616 {y}", 1.6, 0.4 + i * 0.3, "#ffffff"))
        p.append(f'<rect class="lane" x="620" y="{y - 40}" width="920" height="80" rx="14" style="stroke:{c}"/>')
        p.append(f'<text class="la" x="640" y="{y - 4}" fill="{c}">{E(name)}</text>'
                 f'<text class="lm" x="640" y="{y + 22}">gemini-4-hydrogen</text>')
        for k, (x, _, _) in enumerate(cols):
            p.append(f'<rect class="pill" x="{x - 55}" y="{y - 20}" width="110" height="40" rx="20" '
                     f'style="--c:{c};animation-delay:{k * 0.8 + i * 0.37:.2f}s"/>')
            if k < len(cols) - 1:
                p.append(f'<path class="mini" d="M{x + 58} {y} L{x + 82} {y}" marker-end="url(#ah)"/>')
        p.append(f'<path class="loop" d="M1460 {y + 22} C 1460 {y + 38}, 900 {y + 38}, 900 {y + 23}" marker-end="url(#ah)"/>')
        p.append(flow(f"M1540 {y} C 1568 {y}, 1562 270, 1586 270", 1.6, 1.0 + i * 0.45, c))
    p.append(node(20, 185, 230, 170, "Classic router", "Freerouting", ["routes ~97%,", "leaves a few open"]))
    p.append(node(300, 172, 260, 196, "Lead", "gemini-4-carbon", ["reads the board", "picture, writes the", "strategy note"], "lead"))
    p.append(node(1590, 172, 230, 196, "Judge", "PCBWorld scorer", ["scores every saved", "board, the best", "board wins"], "judge"))
    p.append('<text class="cap" x="700" y="500">four step agents, each on its own copy of the board, '
             'one action per step, up to 20 steps</text>')
    # side branches
    p.append(node(20, 570, 540, 170, "Control", "random policy", ["same actions, same step budget,", "no model"], "side"))
    p.append(node(620, 570, 520, 170, "Blocker screen", "gemini-decision-1",
                  ["which routed nets wall off an open", "connection (whole-plan arm)"], "side"))
    p.append(node(1200, 570, 620, 170, "Managed Agent specialist", "gemini-4-hydrogen, long-running",
                  ["long-horizon specialist for boards", "nothing else fixes"], "side"))
    p.append(flow("M660 570 L660 468", 2.2, 0.6, "#8fd0ff", dashed=True))
    p.append(flow("M1705 368 L1705 566", 2.2, 1.2, "#8fd0ff", dashed=True))
    p.append('<text class="cs" x="1690" y="548" text-anchor="end">still not clean?</text>')
    p.append("</svg>")
    return "".join(p)


def slide_crew() -> str:
    return f"""
<section class="slide" data-title="How the crew works">
  <div class="kicker">How the crew works</div>
  <h2>The router does the bulk. Gemini agents finish the board, one checked step at a time.</h2>
  {diagram()}
</section>"""


def chart(ag: dict, rnd: dict | None, w: int = 660, h: int = 250) -> str:
    """Open connections vs step: one line per agent, grey dashed lines for the random control."""
    series = [(a["agent"], a["traj"], False) for a in ag["agents"]]
    if rnd:
        series += [(a["agent"], a["traj"], True) for a in rnd["agents"]]
    ymax = max([ag["start_open"]] + [max(t) for _, t, r in series if not r and t]) + 1
    ymax = max(ymax, 3)
    xmax = max([len(t) - 1 for _, t, _ in series] + [1])
    L, R, T, B = 58, 18, 16, 46
    pw, ph = w - L - R, h - T - B
    X = lambda i: L + pw * i / xmax
    Y = lambda v: T + ph * (1 - min(v, ymax + 0.6) / ymax)
    p = [f'<svg class="chart" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">',
         f'<clipPath id="cp{id(ag)}"><rect x="{L}" y="{T - 6}" width="{pw + 8}" height="{ph + 12}"/></clipPath>',
         f'<g clip-path="url(#cp{id(ag)})">']
    for name, traj, is_r in sorted(series, key=lambda s: not s[2]):
        pts = []
        for i, v in enumerate(traj):
            if i:
                pts.append(f"{X(i):.1f},{Y(traj[i - 1]):.1f}")
            pts.append(f"{X(i):.1f},{Y(v):.1f}")
        cls = "ln rnd" if is_r else "ln"
        p.append(f'<polyline class="{cls}" points="{" ".join(pts)}" style="stroke:{color(name)}"/>')
        if not is_r:
            p.append(f'<circle cx="{X(len(traj) - 1):.1f}" cy="{Y(traj[-1]):.1f}" r="6" fill="{color(name)}"/>')
    p.append("</g>")
    p.append(f'<rect class="curtain" x="{L}" y="0" width="{pw + R}" height="{T + ph + 4}"/>')
    step = 1 if ymax <= 8 else 2 if ymax <= 16 else 5
    for v in range(0, ymax + 1, step):
        p.append(f'<line class="grid" x1="{L}" x2="{w - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>'
                 f'<text class="tick" x="{L - 12}" y="{Y(v) + 6:.1f}" text-anchor="end">{v}</text>')
    p.append(f'<line class="zero" x1="{L}" x2="{w - R}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    xs = max(1, xmax // 5)
    for i in range(0, xmax + 1, xs):
        p.append(f'<text class="tick" x="{X(i):.1f}" y="{h - 16}" text-anchor="middle">{i}</text>')
    p.append(f'<text class="tick" x="{w - R}" y="{h - 16}" text-anchor="end" dx="0" dy="0" opacity="0"></text>')
    p.append("</svg>")
    return "".join(p)


def feed_items(ag: dict) -> str:
    items, depth = [], max([len(a["steps"]) for a in ag["agents"]] + [0])
    for i in range(depth):
        for a in ag["agents"]:
            if i >= len(a["steps"]):
                continue
            s = a["steps"][i]
            b, af = s.get("before"), s.get("after")
            cls = "good" if af < b else "warn" if af > b else "flat"
            nets = ", ".join(str(n) for n in (s.get("nets") or []))
            act = s["action"] + (f" net {nets}" if nets else "")
            done = " · CLEAN" if af == 0 and not s.get("err_after") else ""
            items.append(
                f'<div class="it" style="--c:{color(a["agent"])}"><div class="who"><b>{E(a["agent"])}</b>'
                f'<span class="stepn">step {i + 1}</span><span class="act">{E(act)}</span>'
                f'<span class="res {cls}">{b} → {af} open{done}</span></div>'
                f'<div class="th">{E(s.get("thought") or "")}</div></div>')
    return "".join(items)


def result_line(side: dict) -> str:
    if side["best_clean"]:
        return f'{side["start_open"]} open → 0 · clean pass'
    return f'{side["start_open"]} open → {side["best_open"]} open'


def slide_board(row: dict, idx: int) -> str:
    ag, rnd, board = row["agent"], row["random"], row["board"]
    d = Path(ag["dir"])
    tag = "hard-tier test board" if row["hard_test"] else "development board"
    best = next((a for a in ag["agents"] if a["agent"] == ag["best_agent"]), None)
    chips = [f'<span class="chip {"ok" if ag["best_clean"] else "part"}">Gemini agents: {E(result_line(ag))}'
             + (f' in {best["n_steps"]} steps' if best and ag["best_clean"] else "") + "</span>"]
    if rnd:
        rb = next((a for a in rnd["agents"] if a["agent"] == rnd["best_agent"]), None)
        chips.append(f'<span class="chip rnd">random control: {E(result_line(rnd))}'
                     + (f' in {rb["n_steps"]} steps' if rb and rnd["best_clean"] else "") + "</span>")
    else:
        chips.append('<span class="chip rnd">random control: not finished</span>')
    legend = "".join(f'<span style="color:{color(a["agent"])}">● {E(a["agent"])} <b>{a["traj"][-1]}</b></span>'
                     for a in ag["agents"])
    if rnd:
        legend += f'<span style="color:{COLORS["random"]}">┄ random <b>{rnd["best_open"]}</b></span>'
    vid = f"video/{board}.mp4"
    has_vid = (SITE / vid).is_file()
    video = (f'<video src="{vid}" poster="video/{board}.png" controls muted loop playsinline preload="auto"></video>'
             if has_vid else '<div class="novid">video not rendered yet: run pcb.site.videos</div>')
    note = ag.get("lead_note")
    lead = (f'<div class="leadnote"><div class="who"><b>Lead</b><span class="model">{E(ag.get("lead_model") or "gemini-4-carbon")}'
            f'</span><span class="stepn">strategy note from the board picture</span></div><div class="th">{md_inline(note)}</div></div>'
            if note else "")
    return f"""
<section class="slide board" data-title="{E(short(board))}">
  <div class="bhead"><div><div class="kicker">Featured board {idx} · {tag}</div><h2>{E(short(board))}</h2></div>
    <div class="chips">{"".join(chips)}</div></div>
  <div class="bgrid">
    <div class="bleft">
      <div class="vidbox">{video}</div>
      <div class="pics">
        <figure><img src="{img_uri(d / "start.png")}" alt="before"><figcaption>before · classic router · {ag["start_open"]} open</figcaption></figure>
        <figure><img src="{img_uri(d / "final.png")}" alt="after"><figcaption>after · {E(ag["best_agent"])} agent · {ag["best_open"]} open</figcaption></figure>
      </div>
    </div>
    <div class="bright">
      <div class="panel chartp"><div class="ptitle">Open connections after each step <span class="pnote">rip-ups push it up first</span></div>
        {chart(ag, rnd)}<div class="clegend">{legend}</div></div>
      <div class="panel feedp"><div class="ptitle">Gemini's thinking <span class="pnote">{ag["llm_calls"]} calls · {ag["tokens"]:,} tokens · from llm_calls.jsonl</span></div>
        {lead}<div class="feed">{feed_items(ag)}</div></div>
    </div>
  </div>
</section>"""


def fmt_s(x) -> str:
    return "–" if x in (None, 0) else f"{x:.0f} s"


def slide_matter(rows: list[dict]) -> str:
    trs, tot = [], {"n": 0, "a_clean": 0, "r_clean": 0, "open": 0, "a_closed": 0, "r_closed": 0, "r_n": 0}
    for r in rows:
        name = f'<b>{E(short(r["board"]))}</b><span class="tag">{"hard test" if r["agent_run"].startswith("hard") else "dev"}</span>'
        if not r.get("finished"):
            trs.append(f'<tr class="pend"><td>{name}</td><td colspan="9">run not finished when this page was built</td></tr>')
            continue
        a, c = r["agent"], r["random"]
        if a["start_open"] == 0:
            continue
        ba = next((x for x in a["agents"] if x["agent"] == a["best_agent"]), None)
        tot["n"] += 1
        tot["a_clean"] += a["best_clean"]
        tot["open"] += a["start_open"]
        tot["a_closed"] += a["start_open"] - a["best_open"]

        def cell(side, bx):
            end = '<span class="okc">0 · clean</span>' if side["best_clean"] else f'{side["best_open"]} open'
            return (f'<td class="num">{end}</td><td class="num">{bx["n_steps"] if bx else "–"}'
                    f'<small> / {side["steps_total"]}</small></td><td class="num">{fmt_s(bx["seconds"] if bx else None)}</td>')
        row = f'<tr><td>{name}</td><td class="num">{a["start_open"]}</td>' + cell(a, ba)
        row += f'<td class="num">{a["llm_calls"]}</td><td class="num">{a["tokens"] / 1000:.0f}k</td>'
        if c:
            bc = next((x for x in c["agents"] if x["agent"] == c["best_agent"]), None)
            tot["r_n"] += 1
            tot["r_clean"] += c["best_clean"]
            tot["r_closed"] += c["start_open"] - c["best_open"]
            row += cell(c, bc).replace('<td class="num">', '<td class="num rc">')
        else:
            row += '<td class="num rc" colspan="3">control not finished</td>'
        trs.append(row + "</tr>")
    head = (f'<div class="stats"><div><b>{tot["a_clean"]}/{tot["n"]}</b><span>boards brought to a clean pass by the agents</span></div>'
            f'<div class="r"><b>{tot["r_clean"]}/{tot["r_n"]}</b><span>by the random control</span></div>'
            f'<div><b>{tot["a_closed"]}/{tot["open"]}</b><span>open connections closed by the agents</span></div>'
            f'<div class="r"><b>{tot["r_closed"]}</b><span>closed by the random control</span></div></div>')
    return f"""
<section class="slide" data-title="Did the agents matter?">
  <div class="kicker">Proof against a baseline</div>
  <h2>Did the agents matter? Same actions, same step budget, random choices.</h2>
  {head}
  <table class="tbl">
    <thead><tr><th rowspan="2">Board</th><th rowspan="2">open at start</th><th colspan="5" class="ga">Gemini 4 step agents</th><th colspan="3" class="gr">random control</th></tr>
    <tr><th class="ga">end</th><th class="ga">steps <small>best / all</small></th><th class="ga">time</th><th class="ga">LLM calls</th><th class="ga">tokens</th>
        <th class="gr">end</th><th class="gr">steps <small>best / all</small></th><th class="gr">time</th></tr></thead>
    <tbody>{"".join(trs)}</tbody>
  </table>
  <p class="foot">Steps and time are for the agent whose board was kept; "all" counts every agent on that board. Scored by PCBWorld's evaluate_one. One run per arm.</p>
</section>"""


def slide_scoreboard(rows: list[dict]) -> str:
    sb = _json(RUNS / "scoreboard" / "scoreboard.json")
    if not sb:
        return ""
    by = {r["board"]: r for r in rows if r.get("finished")}
    labels = {"freerouting": "Freerouting", "scripted": "scripted repair", "whole-plan-agents": "whole-plan agents"}
    methods = list(sb["methods"])

    def cell(v):
        if not v or v.get("routability") is None:
            return '<td class="num dim">no board</td>'
        if v.get("clean_pass"):
            return '<td class="num"><span class="okc">clean pass</span></td>'
        return f'<td class="num">{v["routability"] * 100:.1f}%<small> · {v.get("drv_errors_only_count", "?")} err</small></td>'

    def side_score(side):
        if not side:
            return None
        b = next((x for x in side["agents"] if x["agent"] == side["best_agent"]), None)
        if b is None:
            return {"clean_pass": False, "routability": side["start_score"].get("routability"),
                    "drv_errors_only_count": side["start_score"].get("drv_errors_only_count")}
        return {"clean_pass": b["clean"], "routability": b["routability"], "drv_errors_only_count": b["drv"]}

    trs, clean = [], {m: 0 for m in methods + ["step", "rand"]}
    for b in sb["boards"]:
        tds = ""
        for m in methods:
            v = (sb["scores"].get(m) or {}).get(b)
            clean[m] += bool(v and v.get("clean_pass"))
            tds += cell(v)
        r = by.get(b)
        sa, sr = side_score(r["agent"]) if r else None, side_score(r["random"]) if r else None
        clean["step"] += bool(sa and sa["clean_pass"])
        clean["rand"] += bool(sr and sr["clean_pass"])
        tds += (cell(sa) if r else '<td class="num dim">running</td>').replace('class="num', 'class="num hl')
        tds += (cell(sr) if sr else '<td class="num dim">running</td>').replace('class="num', 'class="num rc')
        trs.append(f"<tr><td><b>{E(short(b))}</b></td>{tds}</tr>")
    n = len(sb["boards"])
    tot = "".join(f'<td class="num"><b>{clean[m]}</b> of {n}</td>' for m in methods)
    tot += f'<td class="num hl"><b>{clean["step"]}</b> of {n}</td><td class="num rc"><b>{clean["rand"]}</b> of {n}</td>'
    ths = "".join(f"<th>{E(labels.get(m, m))}</th>" for m in methods)
    return f"""
<section class="slide" data-title="Scoring table">
  <div class="kicker">The four hard-tier boards nothing had fixed · scorer: {E(sb.get("scorer", "evaluate_one"))}</div>
  <h2>Same boards, every method, one scorer.</h2>
  <table class="tbl big">
    <thead><tr><th>Board</th>{ths}<th class="ga">Gemini 4 step agents</th><th class="gr">random steps (control)</th></tr></thead>
    <tbody>{"".join(trs)}<tr class="total"><td>clean boards</td>{tot}</tr></tbody>
  </table>
  <p class="foot">Cell = share of connections routed · design-rule errors left. "No board" = that method returned nothing for the board.</p>
</section>"""


def slide_limits(rows: list[dict], tc: dict) -> str:
    fin = [r for r in rows if r.get("finished") and r["agent"]["start_open"]]
    by = {r["board"]: r for r in fin}
    fails = [r for r in fin if not r["agent"]["best_clean"]]
    pend = [r for r in rows if not r.get("finished")]
    li = []
    if fails:
        parts = ", ".join(f'{short(r["board"])} ({r["agent"]["start_open"]} → {r["agent"]["best_open"]} open)' for r in fails)
        li.append(f"<b>Still not clean after the step agents:</b> {E(parts)}. One open connection fails the whole board.")
        worst = max(fails, key=lambda r: r["agent"]["best_open"])
        ba = next((x for x in worst["agent"]["agents"] if x["agent"] == worst["agent"]["best_agent"]), None) \
            or min(worst["agent"]["agents"], key=lambda x: x["open"], default=None)
        if ba and ba["steps"]:
            li.append(f'<b>Why, in an agent\'s own words</b> ({E(short(worst["board"]))}, {E(ba["agent"])}, last step): '
                      f'<q>{E(ba["steps"][-1]["thought"])}</q>')
    if tc["random_also"]:
        li.append("<b>The random control also reached a clean pass on</b> " + E(", ".join(short(b) for b in tc["random_also"]))
                  + ". On that board the step loop and KiCad's router are enough; the agents needed fewer steps ("
                  + "; ".join(f'{by[b]["agent"]["agents"][0]["n_steps"] and min(x["n_steps"] for x in by[b]["agent"]["agents"] if x["clean"])} vs '
                              f'{min(x["n_steps"] for x in by[b]["random"]["agents"] if x["clean"])}' for b in tc["random_also"])
                  + ").")
    li.append("<b>Freerouting does most of the routing</b> (about 97% of each board). The agents only finish the last few connections.")
    li.append(f'<b>{tc["start"] + len(tc["scripted_added"])} of 10 came from the scripted repair</b>, no model. '
              f'Whole-plan agents added {tc["whole_plan_added"]} board(s). Step agents added {len(tc["agents_added"])}'
              + (f' ({E(", ".join(short(b) for b in tc["agents_added"]))})' if tc["agents_added"] else "") + ".")
    li.append("<b>Single run per arm.</b> No repeated seeds, so no error bars. Every saved board is scored with PCBWorld's evaluate_one.")
    if pend:
        li.append("<b>Not finished when this page was built:</b> " + E(", ".join(short(r["board"]) for r in pend)) + ".")
    return f"""
<section class="slide" data-title="Honest limits">
  <div class="kicker">Honest limits</div>
  <h2>What still fails, and what we did not prove.</h2>
  <ul class="limits">{"".join(f"<li>{x}</li>" for x in li)}</ul>
  <p class="foot">Earlier analog-sizing work from this project lives in demo/ and is not part of this page.</p>
</section>"""


# ---------------------------------------------------------------- cards for the summary video
def cards(rows: list[dict], tc: dict, feat: list[dict]) -> list[str]:
    ours = tc["start"] + len(tc["scripted_added"]) + len(tc["agents_added"])
    title = """
<section class="slide card" data-title="title">
  <div class="kicker">PCBWorld hard tier · Gemini 4 hackathon</div>
  <h1>A circuit board with one unconnected wire is <em>scrap</em>.</h1>
  <p class="lede big">A classic router gets 97% of the way. Gemini 4 agents finish the board, one checked step at a time.</p>
</section>"""
    picks = []
    for r in feat:
        a = r["agent"]
        good = [(x, s) for x in a["agents"] for s in x["steps"] if s["after"] < s["before"] or s["action"] == "ripup"]
        best = next((x for x in a["agents"] if x["agent"] == a["best_agent"]), None)
        mine = [(x, s) for x, s in good if x is best] or good
        for x, s in mine[:2]:
            picks.append((r, x, s))
    its = "".join(
        f'<div class="it on" style="--c:{color(x["agent"])}"><div class="who"><b>{E(x["agent"])}</b><span class="stepn">{E(short(r["board"]))}</span>'
        f'<span class="act">{E(s["action"])} net {E(", ".join(map(str, s["nets"] or [])))}</span>'
        f'<span class="res {"good" if s["after"] < s["before"] else "warn"}">{s["before"]} → {s["after"]} open</span></div>'
        f'<div class="th">{E(s["thought"])}</div></div>' for r, x, s in picks[:4])
    decisions = f"""
<section class="slide card" data-title="decisions">
  <div class="kicker">What the agents decided · gemini-4-hydrogen · text straight from the run logs</div>
  <div class="feed static">{its}</div>
</section>"""
    lines = []
    for r in feat:
        a, c = r["agent"], r["random"]
        lines.append(f'<div class="rl"><b>{E(short(r["board"]))}</b><span class="{"okc" if a["best_clean"] else ""}">agents: {E(result_line(a))}</span>'
                     f'<span class="rc">random: {E(result_line(c)) if c else "not finished"}</span></div>')
    result = f"""
<section class="slide card" data-title="result">
  <div class="kicker">Result · scored by PCBWorld's evaluate_one · single run</div>
  <h1><em>{ours}</em> of 10 hard boards pass clean.</h1>
  <p class="lede big">Freerouting 3 · PCBRouteAgent 4 · ours {ours} · human engineers 9</p>
  <div class="rls">{"".join(lines)}</div>
  <p class="foot">{tc["start"] + len(tc["scripted_added"])} of ours come from the scripted repair (no model); step agents added {len(tc["agents_added"])}. gemini-4-carbon lead · gemini-4-hydrogen step agents · KiCad router and rule checker.</p>
</section>"""
    return [title, decisions, result]


# ---------------------------------------------------------------- page
CSS = """
:root{--bg:#0a0e0c;--panel:#131a17;--ink:#f4f7f5;--ink2:#aab6af;--line:#2a3530;--ok:#5fe0a0;--open:#ffe14d;
 --base:#7d8b84;--script:#58a6ff;--agent:#5fe0a0;--human:#e6ebe7}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#000;overflow:hidden}
body{font-family:Inter,"Helvetica Neue",Helvetica,Arial,system-ui,sans-serif;color:var(--ink);font-size:24px;line-height:1.35}
#stage{position:absolute;left:50%;top:50%;width:1920px;height:1080px;transform-origin:center;background:var(--bg);overflow:hidden}
.slide{display:none;position:absolute;inset:0;padding:44px 60px 64px}
.slide.active{display:block}
.kicker{font-size:22px;letter-spacing:.08em;text-transform:uppercase;color:var(--ok);font-weight:700;margin-bottom:10px}
h1{font-size:88px;line-height:1.04;margin:0 0 22px;font-weight:800;letter-spacing:-.02em;max-width:1700px}
h1 em{font-style:normal;color:var(--open)}
h2{font-size:50px;line-height:1.1;margin:0 0 20px;font-weight:800;letter-spacing:-.01em}
.lede{font-size:30px;color:var(--ink2);margin:0 0 30px}.lede b{color:var(--ink)}.lede.big{font-size:40px;max-width:1500px;line-height:1.25}
.foot{position:absolute;left:60px;right:60px;bottom:58px;font-size:20px;color:var(--ink2);margin:0}
small{font-size:.72em;color:var(--ink2);font-weight:500}
code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.9em;color:#cfe6da}
/* hook */
.bars{display:flex;flex-direction:column;gap:22px;margin-top:8px}
.brow{display:grid;grid-template-columns:470px 1fr 170px;align-items:center;gap:24px;padding:12px 22px;border-radius:16px}
.brow.ours{background:var(--panel);outline:2px solid var(--ok)}
.bl b{display:block;font-size:38px;line-height:1.1}.bl span{font-size:21px;color:var(--ink2)}
.sqs{display:flex;gap:12px}
.sq{display:inline-block;width:94px;height:86px;border-radius:12px;background:#1b2420;border:2px solid var(--line)}
.sqs .sq:not(.empty){animation:pop .45s both}
.sq.base{background:var(--base);border-color:var(--base)}.sq.script{background:var(--script);border-color:var(--script)}
.sq.agent{background:var(--agent);border-color:var(--agent);box-shadow:0 0 26px #5fe0a088}.sq.human{background:var(--human);border-color:var(--human)}
@keyframes pop{from{transform:scale(.2);opacity:0}to{transform:scale(1);opacity:1}}
.bn{font-size:70px;font-weight:800;text-align:right;line-height:1}.bn small{font-size:32px}
.brow.ours .bn{color:var(--ok)}
.legend{display:flex;gap:44px;margin-top:26px;font-size:23px;color:var(--ink2);align-items:center}
.legend .sq{width:30px;height:24px;border-radius:6px;vertical-align:-4px;margin-right:8px}
/* diagram */
.dia{width:1800px;height:783px;display:block;margin-top:4px}
.dia .node rect{fill:var(--panel);stroke:#46544d;stroke-width:2}
.dia .node.lead rect{stroke:#fff}.dia .node.judge rect{stroke:var(--ok)}.dia .node.side rect{stroke-dasharray:8 6;stroke:#5c6b63}
.dia text{fill:var(--ink);font-family:inherit}
.dia .nt{font-size:28px;font-weight:800}.dia .nm{font-size:21px;fill:var(--open);font-family:ui-monospace,Menlo,Consolas,monospace}
.dia .nn{font-size:21px;fill:var(--ink2)}
.dia .ch{font-size:20px;font-weight:700}.dia .cs{font-size:20px;fill:var(--ink2)}
.dia .cap{font-size:23px;fill:var(--ink2)}
.dia .lane{fill:#101613;stroke-width:2}
.dia .la{font-size:26px;font-weight:800}.dia .lm{font-size:19px;fill:var(--open);font-family:ui-monospace,Menlo,Consolas,monospace}
.dia .pill{fill:#222c27;animation:lit 4s infinite both}
@keyframes lit{0%{fill:var(--c)}18%{fill:var(--c)}26%{fill:#222c27}100%{fill:#222c27}}
.dia .wire{fill:none;stroke:#5c6b63;stroke-width:2.5}.dia .wire.dash{stroke-dasharray:7 6}
.dia .mini{stroke:#5c6b63;stroke-width:2;fill:none}
.dia .loop{fill:none;stroke:#3f4c45;stroke-width:1.6;stroke-dasharray:4 5}
.dia .tok{filter:drop-shadow(0 0 6px currentColor)}
/* board */
.board{padding-top:30px}
.bhead{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:14px;height:98px}
.bhead h2{margin:0;font-size:54px}.bhead .kicker{margin-bottom:4px}
.chips{display:flex;gap:14px;flex-wrap:wrap;justify-content:flex-end}
.chip{font-size:24px;font-weight:700;padding:10px 18px;border-radius:999px;border:2px solid var(--line);background:var(--panel)}
.chip.ok{border-color:var(--ok);color:var(--ok)}.chip.part{border-color:var(--open);color:var(--open)}.chip.rnd{color:#c3cbc6;border-color:#5c6b63}
.bgrid{display:grid;grid-template-columns:1100px 676px;gap:24px}
.vidbox{width:1100px;height:619px;background:#000;border-radius:12px;overflow:hidden;border:1px solid var(--line)}
.vidbox video{width:100%;height:100%;display:block}
.novid{display:flex;align-items:center;justify-content:center;height:100%;color:var(--ink2)}
.pics{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}
.pics figure{margin:0;height:238px;background:#000;border:1px solid var(--line);border-radius:12px;position:relative;overflow:hidden}
.pics img{width:100%;height:100%;object-fit:contain;display:block;filter:invert(1) hue-rotate(180deg) contrast(1.15)}
.pics figcaption{position:absolute;left:0;top:0;font-size:20px;font-weight:700;background:#000c;padding:5px 12px;border-bottom-right-radius:10px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px}
.ptitle{font-size:24px;font-weight:800;margin-bottom:4px;display:flex;justify-content:space-between;align-items:baseline}
.pnote{font-size:20px;color:var(--ink2);font-weight:500}
.chartp{height:334px}.chart{width:100%;height:auto;display:block}
.chart .ln{fill:none;stroke-width:4;stroke-linejoin:round;stroke-linecap:round}
.chart .ln.rnd{stroke-width:2.5;stroke-dasharray:7 6;opacity:.85}
.chart .grid{stroke:#26302b;stroke-width:1}.chart .zero{stroke:var(--ok);stroke-width:2;stroke-dasharray:3 5}
.chart .tick{fill:var(--ink2);font-size:20px}
.chart .curtain{fill:var(--panel)}
.slide.active .chart .curtain{animation:reveal 4.5s .4s linear both}
@keyframes reveal{from{transform:translateX(0)}to{transform:translateX(700px)}}
.clegend{display:flex;gap:13px;font-size:20px;font-weight:600;white-space:nowrap;line-height:1.2}.clegend b{color:var(--ink)}
.feedp{height:523px;margin-top:14px;display:flex;flex-direction:column}
.leadnote{border-left:5px solid #fff;background:#1c2521;border-radius:8px;padding:8px 12px;margin-bottom:8px;flex:none}
.leadnote .th{font-size:20px;line-height:27px;max-height:81px;overflow-y:auto;color:#dfe6e2}.model{font-family:ui-monospace,Menlo,monospace;color:var(--open);font-size:19px;font-weight:500}
.feed{overflow-y:auto;flex:1;scroll-behavior:smooth;padding-right:6px}
.it{display:none;border-left:5px solid var(--c);padding:6px 12px;margin-bottom:8px;background:#0f1512;border-radius:8px}
.it.on{display:block;animation:slidein .35s both}
@keyframes slidein{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.who{display:flex;gap:12px;align-items:baseline;font-size:21px;flex-wrap:wrap}
.who b{color:var(--c);font-size:23px}.leadnote .who b{color:#fff}
.stepn{color:var(--ink2);font-size:19px}
.act{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:20px;color:var(--ink)}
.res{margin-left:auto;font-weight:800;font-size:21px;white-space:nowrap}
.res.good{color:var(--ok)}.res.warn{color:#ffb454}.res.flat{color:var(--ink2)}
.th{font-size:21px;line-height:1.3;color:#dfe6e2}
/* tables */
.stats{display:flex;gap:22px;margin:6px 0 20px}
.stats div{background:var(--panel);border:2px solid var(--ok);border-radius:14px;padding:12px 22px;flex:1}
.stats div.r{border-color:#5c6b63}
.stats b{font-size:58px;line-height:1;display:block;color:var(--ok)}.stats .r b{color:#c3cbc6}
.stats span{font-size:21px;color:var(--ink2)}
.tbl{width:100%;border-collapse:collapse;font-size:25px}
.tbl th{font-size:20px;text-transform:uppercase;letter-spacing:.05em;color:var(--ink2);text-align:right;padding:8px 14px;border-bottom:2px solid var(--line);font-weight:700}
.tbl th:first-child{text-align:left}.tbl th.ga{color:var(--ok)}.tbl th.gr{color:#c3cbc6}
.tbl th[colspan]{text-align:center}
.tbl td{padding:13px 14px;border-bottom:1px solid var(--line);vertical-align:baseline}
.tbl td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tbl td.rc{color:#c3cbc6;background:#0f1412}.tbl td.hl{background:#13201a}
.tbl td.dim,.tbl tr.pend td{color:#7b8780}
.tbl .tag{font-size:18px;color:var(--ink2);border:1px solid var(--line);border-radius:6px;padding:1px 8px;margin-left:10px;white-space:nowrap}
.okc{color:var(--ok);font-weight:800}
.tbl.big{font-size:30px;margin-top:24px}.tbl.big td{padding:24px 14px}.tbl.big th{font-size:21px}
.tbl tr.total td{border-top:2px solid var(--ink2);border-bottom:none;color:var(--ink2)}.tbl tr.total b{color:var(--ink);font-size:1.25em}
/* limits */
.limits{margin:24px 0 0;padding:0;list-style:none;max-width:1740px}
.limits li{font-size:29px;line-height:1.3;padding:15px 0 15px 34px;border-bottom:1px solid var(--line);position:relative;color:#dfe6e2}
.limits li:before{content:"";position:absolute;left:0;top:28px;width:14px;height:14px;border-radius:50%;background:var(--open)}
.limits b{color:#fff}.limits q{color:var(--ink2);font-style:italic}
/* cards */
.card{padding:150px 110px}.card h1{font-size:104px}.card .kicker{font-size:28px;margin-bottom:26px}
.feed.static{overflow:visible}.feed.static .it{padding:16px 24px;margin-bottom:22px}
.feed.static .th{font-size:34px}.feed.static .who{font-size:30px}.feed.static .who b{font-size:36px}
.feed.static .act,.feed.static .res{font-size:30px}.feed.static .stepn{font-size:26px}
.card:has(.feed.static){padding:70px 90px}
.rls{margin-top:34px;display:flex;flex-direction:column;gap:16px}
.rl{display:grid;grid-template-columns:400px 660px 1fr;font-size:34px;white-space:nowrap;background:var(--panel);border-radius:14px;padding:16px 26px}
.rl .rc{color:#c3cbc6}
.card .foot{font-size:24px;left:110px;right:110px;bottom:70px}
/* nav */
#nav{position:absolute;left:0;right:0;bottom:0;height:44px;display:flex;align-items:center;gap:8px;padding:0 60px;font-size:20px;color:var(--ink2);background:#070a09;border-top:1px solid var(--line)}
#nav button{font:inherit;color:var(--ink2);background:none;border:1px solid var(--line);border-radius:8px;padding:2px 12px;cursor:pointer}
#nav button.cur{color:#06110b;background:var(--ok);border-color:var(--ok);font-weight:700}
#nav .hint{margin-left:auto}
body.cards #nav{display:none}
"""

JS = """
(function(){
  var stage=document.getElementById('stage'),slides=[].slice.call(document.querySelectorAll('.slide')),
      nav=document.getElementById('nav'),cur=-1,timer=null;
  function fit(){var s=Math.min(innerWidth/1920,innerHeight/1080);stage.style.transform='translate(-50%,-50%) scale('+s+')';}
  addEventListener('resize',fit);fit();
  slides.forEach(function(s,i){var b=document.createElement('button');b.textContent=(i+1)+' '+s.dataset.title;
    b.onclick=function(){show(i)};nav.insertBefore(b,nav.lastElementChild);});
  function feed(s){
    clearInterval(timer);var f=s.querySelector('.feed:not(.static)');if(!f)return;
    var its=[].slice.call(f.querySelectorAll('.it')),k=0,paused=false;
    its.forEach(function(x){x.classList.remove('on')});f.scrollTop=0;
    f.onmouseenter=function(){paused=true};f.onmouseleave=function(){paused=false};
    function step(){if(paused||k>=its.length)return;its[k++].classList.add('on');f.scrollTop=f.scrollHeight;}
    step();timer=setInterval(step,1700);
  }
  function show(i){
    i=Math.max(0,Math.min(slides.length-1,i));if(i===cur)return;
    slides.forEach(function(s,k){s.classList.toggle('active',k===i);
      var v=s.querySelector('video');if(v){if(k===i){try{v.currentTime=0;var p=v.play();if(p&&p.catch)p.catch(function(){});}catch(e){}}else v.pause();}});
    [].slice.call(nav.querySelectorAll('button')).forEach(function(b,k){b.classList.toggle('cur',k===i)});
    cur=i;try{history.replaceState(null,'','#'+(i+1));}catch(e){}
    feed(slides[i]);
  }
  addEventListener('keydown',function(e){
    var k=e.key;
    if(k==='ArrowRight'||k==='ArrowDown'||k==='PageDown'||k===' '){show(cur+1);e.preventDefault();}
    else if(k==='ArrowLeft'||k==='ArrowUp'||k==='PageUp'){show(cur-1);e.preventDefault();}
    else if(k==='Home'){show(0);}else if(k==='End'){show(slides.length-1);}
    else if(/^[1-9]$/.test(k)){show(+k-1);}
    else if(k==='f'){if(document.fullscreenElement)document.exitFullscreen();else document.documentElement.requestFullscreen();}
  },true);
  addEventListener('hashchange',function(){var n=parseInt(location.hash.slice(1),10);if(n)show(n-1);});
  show((parseInt(location.hash.slice(1),10)||1)-1);
})();
"""


def page(slides: list[str], title: str, body_cls: str = "") -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{E(title)}</title>'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><style>{CSS}</style></head>'
            f'<body class="{body_cls}"><div id="stage">{"".join(s for s in slides if s)}'
            f'<div id="nav"><span class="hint">← → to move · F fullscreen</span></div></div>'
            f'<script>{JS}</script></body></html>')


def main() -> None:
    pick = _json(SITE / "pick.json", {}) or {}
    rows = load_pairs(pick.get("pairs"))
    by = {(r["agent_run"], r["board"]): r for r in rows if r.get("finished")}
    feat = [by[(x["agent_run"], x["board"])] for x in pick.get("featured", []) if (x["agent_run"], x["board"]) in by]
    tc = tier_counts(rows)
    slides = [slide_hook(tc), slide_crew()] + [slide_board(r, i + 1) for i, r in enumerate(feat)]
    slides += [slide_matter(rows), slide_scoreboard(rows), slide_limits(rows, tc)]
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "index.html").write_text(page(slides, "Gemini 4 agents finish the board"))
    (SITE / "cards.html").write_text(page(cards(rows, tc, feat), "cards", "cards"))
    n = len([s for s in slides if s])
    print(f"wrote {SITE / 'index.html'} ({(SITE / 'index.html').stat().st_size // 1024} kB, {n} sections); "
          f"featured: {', '.join(r['board'] for r in feat)}; ours = "
          f"{tc['start'] + len(tc['scripted_added']) + len(tc['agents_added'])}/10 "
          f"(router {tc['start']}, scripted +{len(tc['scripted_added'])}, step agents +{len(tc['agents_added'])})")


if __name__ == "__main__":
    main()
