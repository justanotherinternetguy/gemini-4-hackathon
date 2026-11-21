"""The one-minute summary video, in the site's design: runs/site/video/summary.mp4 (1920x1080, H.264).

    .venv/bin/python -m web.video

Each still is a 1920x1080 card built from the same pieces as the site (so the numbers match it),
screenshotted with headless Chromium; the board clip is the step-agent video from pcb.site.videos.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from pcb.site.data import load_pairs
from web import analog, council, pcb
from web.kit import OUT, ROOT

VIDEO = ROOT / "runs" / "site" / "video"
BOARD_CLIP = VIDEO / "0400_laptimer58_Chickadee.mp4"
FONTS = ("https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Figtree:wght@400;600"
         "&family=Fredoka:wght@500;600&display=swap")
CARD_CSS = """
html,body{width:1920px;height:1080px;overflow:hidden}
body{display:flex;align-items:center}
.card-stage{width:1920px;padding:0 150px;zoom:1.32}
.card-stage .wrap{max-width:none;padding:0}
.word{font:600 64px/1 var(--display);display:flex;align-items:center;gap:22px;margin-bottom:56px}
.word i{width:64px;height:64px;border-radius:50%;background:var(--main);display:grid;place-items:center}
.word i:after{content:"";width:26px;height:26px;border-radius:50%;background:var(--mint)}
.big{font:600 96px/1.02 var(--display)}
.big em{font-style:normal;background:linear-gradient(transparent 62%,var(--butter) 62%,var(--butter) 92%,transparent 92%);padding:0 6px}
.sub{font-size:26px;color:var(--text-soft);margin-top:28px;max-width:1150px;line-height:1.45}
.sub b{color:var(--text)}
.tag{font:600 16px/1 var(--body);letter-spacing:.08em;text-transform:uppercase;color:var(--text-soft);margin-bottom:16px;display:flex;gap:10px;align-items:center}
.tag .dot{width:10px;height:10px;border-radius:50%;background:var(--mint)}
h2{margin-bottom:28px}
.rank .sq{animation:none!important}
"""


def card(body: str, zoom: float | None = None) -> str:
    z = f'style="zoom:{zoom}"' if zoom else ""
    return (f'<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{FONTS}">'
            f'<link rel="stylesheet" href="style.css"><style>{CARD_CSS}</style></head>'
            f'<body><div class="card-stage" {z}><div class="wrap">{body}</div></div></body></html>')


def pm_chart(rid: str) -> str:
    """The worst-case phase-margin chart (with its legend) from the site's run view."""
    html = analog.candidate_view(rid)
    i = html.index('<svg class="chart"')
    j = html.index('</div>', html.index('class="keyrow"', i))
    return html[i:j + 6]


def cards() -> list[tuple[str, str, float]]:
    rows = load_pairs()
    t = pcb.tier(rows)
    n = analog.numbers()
    c0 = council.load(council.AGENT_RUN, council.BOARDS[0])
    r0 = council.load(council.RANDOM_RUN, council.BOARDS[0])
    flow = "".join(f'<div class="node {c}"><h4>{a}</h4><span class="m">{m}</span><p>{d}</p></div>' for a, m, d, c in council.ROLES)
    out = [
        ("title", card(f"""<div class="word"><i></i>routr</div>
<div class="big">Agents that finish the <em>hardware</em> design.</div>
<p class="sub">Crews of Gemini 4 agents for the two jobs that stall chip and board work: <b>routing the last connections on a circuit board</b>, and <b>sizing an analog circuit until it passes at every corner</b>. An independent checker scores every result.</p>"""), 5.0),
        ("pcb", card(f"""<div class="tag"><span class="dot"></span>PCB routing · PCBWorld hard tier · scored by evaluate_one</div>
<h2>A board with one unconnected wire is scrap.</h2>{pcb.ranking(t).replace('data-inview', '')}"""), 8.0),
        ("council", card(f"""<div class="tag"><span class="dot" style="background:var(--lilac)"></span>The council · the board nothing else could finish</div>
<h2>Five Gemini roles closed the last connection.</h2><div class="flow">{flow}</div>
<div class="chips" style="margin-top:28px;gap:12px">
<span class="chip ok" style="font-size:20px;padding:10px 18px">Council: 1 open → 0 in {len(c0['rounds'])} rounds · clean pass</span>
<span class="chip" style="font-size:20px;padding:10px 18px">Random proposals, same harness: still {r0['final'] if r0 else '?'} open after {len(r0['rounds']) if r0 else '?'} rounds</span></div>
<p class="sub" style="font-size:20px">The winning branch was the chief's own merge: both branches built from single proposals ended at 3 open.</p>""", zoom=1.12), 9.0),
        ("analog", card(f"""<div class="tag"><span class="dot"></span>Analog sizing · SKY130 op-amp · ngspice at 15 PVT points</div>
<h2>An op-amp that passes at every corner.</h2>
<div class="kpis" style="margin-top:0">
  <div class="kpi ok"><b>{n["sw_k"]}<small>/{n["sw_n"]}</small></b><span>routr runs that pass, 40 candidates each</span></div>
  <div class="kpi"><b>{n["bl_k"]}<small>/{n["bl_n"]}</small></b><span>Optuna TPE, GP-BO and random search, same budget</span></div>
  <div class="kpi ok"><b>{n["intake"][0]}<small>/{n["intake"][1]}</small></b><span>transistor roles read from a schematic photo</span></div>
  <div class="kpi ok"><b>{n["signoff"][0]}<small>/{n["signoff"][1]}</small></b><span>PVT points an independent sign-off agent matched</span></div>
</div>"""), 8.0),
        ("runs", card(f"""<div class="tag"><span class="dot" style="background:var(--sky)"></span>One real run · guard_s0 · every dot is one sizing at all 15 PVT points</div>
<h2>The crew finds the passing region; the simulator decides.</h2>
<div class="grid32" style="align-items:center"><div class="card">{pm_chart("guard_s0")}</div>
<p class="sub" style="margin:0">A gemini-4-carbon architect reads the worst failures and directs four gemini-4-hydrogen workers. <b>ngspice judges every candidate</b>; no model ever decides pass or fail.</p></div>"""), 7.0),
        ("limits", card(f"""<div class="tag"><span class="dot" style="background:var(--coral)"></span>What did not work</div>
<h2>We show the failures too.</h2>
<ul class="limits" style="font-size:22px">
<li class="fail"><b>Two held-out op-amps: 0 of 12 runs pass.</b> Bayesian optimisation also fails them; the PMOS cascode will not stay in saturation.</li>
<li><b>{len(t["start"]) + len(t["scripted"])} of our {t["ours"]} clean boards need no model.</b> The router and our scripted repair do most of the work; Gemini finished the hardest {len(t["agents"]) + len(t["council"])}.</li>
<li><b>Random steps also cleaned Chickadee.</b> The council's win on HybridChargeController is the one random proposals did not match.</li>
</ul>"""), 8.0),
        ("end", card("""<div class="word"><i></i>routr</div>
<div class="big" style="font-size:72px">routr.internetguy.dev</div>
<p class="sub">gemini-4-carbon leads and chiefs · gemini-4-hydrogen proposers and workers · gemini-4-neon reviews the picture · gemini-decision-1 screens and labels · Managed Agents for sign-off, red team and stuck boards.</p>
<p class="sub" style="font-size:20px">github.com/justanotherinternetguy/gemini-4-hackathon</p>"""), 5.0),
    ]
    return out


def shoot(html: str, png: Path) -> None:
    page = OUT / f"_card_{png.stem}.html"
    page.write_text(html)
    try:
        subprocess.run(["chromium", "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                        f"--user-data-dir={tempfile.mkdtemp()}", "--window-size=1920,1080",
                        "--virtual-time-budget=5000", f"--screenshot={png}", page.as_uri()],
                       check=True, capture_output=True)
    finally:
        page.unlink(missing_ok=True)


def dur(p: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                                capture_output=True, text=True).stdout.strip())


def main() -> None:
    from web import build
    build.main()                       # make sure runs/web/style.css and img/ are current
    tmp = Path(tempfile.mkdtemp())
    stills = cards()
    for name, html, _ in stills:
        shoot(html, tmp / f"{name}.png")
    order = [stills[0], stills[1], ("clip", None, 9.0)] + stills[2:]
    cmd, filt = ["ffmpeg", "-y", "-loglevel", "error"], []
    for i, (name, _, d) in enumerate(order):
        if name == "clip":
            if not BOARD_CLIP.is_file():
                continue
            cmd += ["-i", str(BOARD_CLIP)]
            k = d / dur(BOARD_CLIP)
            filt.append(f"[{len(filt)}:v]setpts={k:.5f}*PTS,fps=30,scale=1920:1080,setsar=1,trim=duration={d},"
                        f"fade=t=in:st=0:d=0.3,fade=t=out:st={d - 0.3:.2f}:d=0.3,format=yuv420p[v{len(filt)}]")
        else:
            cmd += ["-loop", "1", "-framerate", "30", "-t", f"{d}", "-i", str(tmp / f"{name}.png")]
            filt.append(f"[{len(filt)}:v]scale=1920:1080,setsar=1,fade=t=in:st=0:d=0.35,"
                        f"fade=t=out:st={d - 0.35:.2f}:d=0.35,format=yuv420p[v{len(filt)}]")
    filt.append("".join(f"[v{i}]" for i in range(len(filt))) + f"concat=n={len(filt)}:v=1:a=0[out]")
    out = VIDEO / "summary.mp4"
    VIDEO.mkdir(parents=True, exist_ok=True)
    cmd += ["-filter_complex", ";".join(filt), "-map", "[out]", "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-crf", "20", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    print(f"wrote {out}: {dur(out):.1f} s; stills in {tmp}")


if __name__ == "__main__":
    main()
