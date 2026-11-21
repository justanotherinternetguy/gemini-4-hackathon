"""The PCB council (pcb/repair/council.py): six proposers, a Decisions screen, a reviewer, a chief and
a judge, run round by round on the hard boards nothing else fixed. Read from runs/pcb/ at build time."""
from __future__ import annotations

from pcb.site.data import short
from web.kit import E, RUNS, asset, chip, jload, jsonl, key, line_chart, pane, switch, table

PCB = RUNS / "pcb"
AGENT_RUN, RANDOM_RUN = "hard_council_v2", "hard_council_random"
BOARDS = ["0626_TOBS_HybridChargeController", "0599_RGBMatrixPanelCPLD-PhotonBackpack_RGBMatrixPanel_CPLD",
          "0473_data-manager_data-manager"]
PROPOSER_COLOR = {"careful": "var(--s1)", "bold": "var(--s2)", "layer-first": "var(--s3)", "power-aware": "var(--s4)",
                  "minimalist": "var(--s5)", "unblocker": "var(--text)"}
ROLES = [("Six proposers", "gemini-4-hydrogen",
          "Careful, bold, layer-first, power-aware, minimalist and unblocker each propose one short action sequence.", "ai"),
         ("Screen", "gemini-decision-1", "Scores how likely each proposal is to work. Cheap and fast.", "ai"),
         ("Reviewer", "gemini-4-neon", "Looks at the board picture and the proposals; flags duplicates and risky orders.", "ai"),
         ("Chief", "gemini-4-carbon", "Picks, edits or merges proposals into three branches, and may add its own.", "ai"),
         ("Judge", "KiCad + evaluate_one", "Runs each branch on its own copy of the board. The best board carries to the next round.", "judge")]


def load(run: str, board: str) -> dict | None:
    d = PCB / run / board
    log = jsonl(d / "council.jsonl")
    if not log:
        return None
    rounds, cur = [], {"proposals": []}
    for x in log:
        role = x.get("role")
        if role == "proposer":
            cur["proposals"].append(x)
        elif role in ("screen", "reviewer", "chief"):
            cur[role] = x
        elif role == "judge":
            cur["judge"] = x
            rounds.append(cur)
            cur = {"proposals": []}
    finished = (d / "history.json").is_file()
    open_trace = [r["judge"].get("open_now") for r in rounds]
    return {"dir": d, "rounds": rounds, "finished": finished, "open": open_trace,
            "final": open_trace[-1] if open_trace else None,
            "start_png": d / "start.png",
            "end_png": max(d.glob("state_r*.png"), key=lambda p: int(p.stem[7:]), default=None)}


def start_open(board: str) -> int | None:
    for run in ("hard_step_agent", "hard_control"):
        h = jload(PCB / run / board / "history.json")
        if isinstance(h, list) and h:
            return ((h[0].get("end_status") or {}).get("unrouted"))
    return None


def clean_boards() -> set[str]:
    """Boards the council brought to 0 open, confirmed by PCBWorld's scorer where a scoreboard exists."""
    out = set()
    for b in BOARDS:
        c = load(AGENT_RUN, b)
        if c and c["final"] == 0:
            sb = jload(PCB / f"scoreboard_{b[:4]}" / "scoreboard.json") or {}
            v = ((sb.get("scores") or {}).get("council") or {}).get(b)
            if v is None or v.get("clean_pass"):
                out.add(b)
    return out


def results_table() -> str:
    rows = []
    for b in BOARDS:
        c, r = load(AGENT_RUN, b), load(RANDOM_RUN, b)
        if not c:
            continue
        s0 = start_open(b)
        trace = " → ".join(str(x) for x in c["open"])
        end = ('<span class="ok-t">0 · clean</span>' if c["final"] == 0 else f'{c["final"]} open')
        note = "" if c["finished"] else f'<span class="dim"> · unfinished, {len(c["rounds"])} rounds</span>'
        rend = ("–" if not r else '<span class="ok-t">0 · clean</span>' if r["final"] == 0 else f'{r["final"]} open')
        rnote = "" if not r else f'<span class="dim"> · {"" if r["finished"] else "unfinished, "}{len(r["rounds"])} rounds</span>'
        rows.append([f"<b>{E(short(b))}</b>", f'<td class="n">{s0 if s0 is not None else "–"}</td>',
                     f'<td class="n hl">{trace}</td>', f'<td class="n hl">{end}{note}</td>', f'<td class="n">{rend}{rnote}</td>'])
    return table(["Board", "Open at start", "Council: open after each round", "Council: end", "Random proposals: end"], rows)


def round_chart(b: str) -> str:
    c, r = load(AGENT_RUN, b), load(RANDOM_RUN, b)
    s0 = start_open(b)
    series = []
    if r:
        series.append({"name": "random", "color": "var(--s0)", "dashed": True,
                       "pts": [(0, s0)] + [(i + 1, v) for i, v in enumerate(r["open"])]})
    if c:
        tips = [f"start · {s0} open"] + [f"round {i + 1} · {v} open" for i, v in enumerate(c["open"])]
        series.append({"name": "council", "color": "var(--mint-text)", "dots": True, "tips": tips,
                       "pts": [(0, s0)] + [(i + 1, v) for i, v in enumerate(c["open"])]})
    return (line_chart(series, step=True, xlabel="round", ymin=0, hlines=[(0, "clean")], h=230)
            + key([("Gemini council", "var(--mint-text)", False), ("random proposals, same harness", "var(--s0)", True)]))


def steps_text(steps: list[dict]) -> str:
    return " → ".join(f'{s.get("action")} {",".join(map(str, s.get("nets") or []))}' + (f' (mode {s["mode"]})' if s.get("mode") is not None else "")
                      for s in steps or [])


def round_view(i: int, rd: dict, last: bool) -> str:
    props = rd["proposals"]
    probs = (rd.get("screen") or {}).get("probabilities") or []
    pitems = []
    for k, p in enumerate(props):
        pr = p.get("proposal") or {}
        col = PROPOSER_COLOR.get(p.get("agent"), "var(--s0)")
        prob = f'<span class="act">screen {probs[k]:.0%}</span>' if k < len(probs) else ""
        pitems.append(f'<div class="step"><div class="h"><b><i style="background:{col}"></i>{E(p.get("agent", ""))}</b>{prob}'
                      f'</div><p class="mono" style="font-size:12.5px;color:var(--text-soft)">{E(steps_text(pr.get("steps")))}</p>'
                      f'<p>{E(pr.get("reason") or "")}</p></div>')
    rv = ((rd.get("reviewer") or {}).get("review") or {}).get("critique") or ""
    ch = (rd.get("chief") or {}).get("decision") or {}
    branches = "".join(f'<p class="soft" style="margin-top:6px">→ <b>branch {n + 1}</b>'
                       f'{" (from proposals " + ", ".join(map(str, s.get("from_proposals") or [])) + ")" if s.get("from_proposals") else " (the chief&#39;s own)"}: '
                       f'<span class="mono">{E(steps_text(s.get("steps")))}</span>. {E(s.get("why") or "")}</p>'
                       for n, s in enumerate(ch.get("selected") or []))
    jd = rd.get("judge") or {}
    kids = []
    for n, k in enumerate(jd.get("children") or []):
        o = (k.get("health") or [None])[0]
        win = n == jd.get("winner")
        cls = "ok" if o == 0 else ("ink" if win else "")
        kids.append(f'<div class="step"><div class="h"><b>branch {n + 1}</b><span class="soft">{E(k.get("credit", ""))}</span>'
                    f'{chip("kept", "ink") if win and o else ""}<span class="res {"good" if o == 0 else ""}">{o} open</span></div>'
                    f'<p class="mono" style="font-size:13px">{E(" · ".join(k.get("log") or []))}</p></div>')
    return f"""
<details class="round"{" open" if last else ""}><summary><b>Round {i + 1}</b><span class="soft">{len(props)} proposals → 3 branches → {jd.get("open_now")} open</span></summary>
<h4 style="margin:14px 0 8px">Proposers <span class="soft" style="font:400 13.5px var(--body)">gemini-4-hydrogen · screen: gemini-decision-1</span></h4>
<div class="grid3">{"".join(pitems)}</div>
<div class="grid2" style="margin-top:16px">
  <div class="quote"><div class="who"><i style="width:10px;height:10px;border-radius:50%;background:var(--sky)"></i>Reviewer · gemini-4-neon</div>{E(rv)}</div>
  <div class="quote"><div class="who"><i style="width:10px;height:10px;border-radius:50%;background:var(--text)"></i>Chief · gemini-4-carbon</div>{E(ch.get("discussion") or "")}{branches}</div>
</div>
<h4 style="margin:16px 0 8px">Judge <span class="soft" style="font:400 13.5px var(--body)">each branch runs on its own copy of the board</span></h4>
<div class="grid3">{"".join(kids)}</div>
</details>"""


def story(b: str) -> str:
    c = load(AGENT_RUN, b)
    if not c:
        return ""
    s_img = asset(c["start_png"], f"pcb/council_{b}_start.png")
    e_img = asset(c["end_png"], f"pcb/council_{b}_end.png") if c["end_png"] else ""
    pics = (f'<div class="ba" style="margin-bottom:20px"><figure class="frame" data-zoom><img loading="lazy" src="{s_img}" alt="board before the council"><figcaption>before · {start_open(b)} open</figcaption></figure>'
            f'<figure class="frame" data-zoom><img loading="lazy" src="{e_img}" alt="board after the council"><figcaption>after round {len(c["rounds"])} · {c["final"]} open</figcaption></figure></div>'
            if s_img and e_img else "")
    rounds = "".join(round_view(i, rd, i == len(c["rounds"]) - 1) for i, rd in enumerate(c["rounds"]))
    return pics + rounds


def caveats() -> str:
    r = load(RANDOM_RUN, BOARDS[0])
    li = [("", "<b>Like-for-like control.</b> The same council harness with random proposals in place of Gemini (no model calls). "
               + (f"On {E(short(BOARDS[0]))} it ended at {r['final']} open after {len(r['rounds'])} rounds" + ("" if r["finished"] else "; that run has no final result file yet") + "."
                  if r else "Not finished when this page was built.")),
          ("", "<b>One run.</b> The council has run once per board; no repeated seeds."),
          ("", "<b>The Decisions screen did not separate the proposals here</b>: on the winning round it scored them between 25% and 40%."),
          ("", "<b>The win came from the chief&#39;s merge.</b> On the winning round both proposer-derived branches ended at 3 open; the branch the chief wrote itself ended at 0.")]
    for b in BOARDS[1:]:
        c = load(AGENT_RUN, b)
        if c and not c["finished"]:
            li.append(("", f"<b>{E(short(b))}: unfinished</b> after {len(c['rounds'])} rounds at {c['final']} open (no final result file); not yet scored by evaluate_one."))
    return '<ul class="limits">' + "".join(f'<li class="{k}">{x}</li>' for k, x in li) + "</ul>"


def section() -> str:
    flow = "".join(f'<div class="node {c}"><h4>{E(a)}</h4><span class="m">{E(m)}</span><p>{E(d)}</p></div>' for a, m, d, c in ROLES)
    items = [(f"c{i}", E(short(b))) for i, b in enumerate(BOARDS) if load(AGENT_RUN, b)]
    dots = ["var(--mint)" if (load(AGENT_RUN, BOARDS[i]) or {}).get("final") == 0 else "var(--butter)" for i in range(len(items))]
    charts = "".join(pane("cchart", f"c{i}", round_chart(BOARDS[i]), i == 0) for i in range(len(items)))
    stories = "".join(pane("cstory", f"c{i}", story(BOARDS[i]), i == 0) for i in range(len(items)))
    win = BOARDS[0]
    return f"""
<section class="band" id="pcb-council">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot" style="background:var(--lilac)"></span>The council</div>
      <h2>The board nothing else could finish</h2>
      <p class="lede">{E(short(win))} defeated the router, the scripted repair, the step agents, the random steps and the Managed Agent: each left the same <b>1 open connection</b>. A council of five Gemini roles closed it in three rounds, and PCBWorld's scorer confirms a <b>clean pass</b>.</p></div></div>
    <div class="flow">{flow}</div>
    <h3 style="margin:48px 0 12px">Council against random proposals, same harness</h3>
    {results_table()}
    <div class="grid32" style="margin-top:20px;align-items:start">
      <div class="card"><div class="chead"><h4>Open connections after each round</h4></div>{switch("cchart", items, dots)}<div style="margin-top:12px">{charts}</div></div>
      <div class="stack"><h4>What to look for</h4>{caveats()}</div>
    </div>
    <h3 style="margin:48px 0 6px">Every round, every voice</h3>
    <p class="soft">Each round: the proposals and their screen scores, the reviewer's critique, the chief's three branches, and what KiCad's judge found. Straight from council.jsonl.</p>
    {switch("cstory", items, dots)}<div style="margin-top:16px">{stories}</div>
  </div>
</section>"""
