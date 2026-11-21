"""Build the routr website into runs/web/ (index.html, style.css, app.js, img/).

    .venv/bin/python -m web.build          # then: web/serve.sh  ->  http://127.0.0.1:8080

Every number and every line of agent text comes from the run folders under runs/ at build time;
nothing here calls a model. Rebuild after new runs land.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from web import analog, pcb
from web.kit import OUT

HERE = Path(__file__).resolve().parent
FONTS = ("https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Figtree:wght@400;600"
         "&family=Fredoka:wght@500;600&display=swap")
MOON = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>')


def hero(p: dict, a: dict) -> str:
    return f"""
<header class="hero" id="top">
  <div class="wrap">
    <div class="eyebrow"><span class="dot" style="background:var(--sky)"></span>Gemini 4 hackathon · 5 Sigma track</div>
    <h1>Agents that finish the <em>hardware</em> design.</h1>
    <p class="lede">routr puts teams of Gemini 4 agents on the two jobs that stall chip and board work: routing the last connections on a circuit board, and sizing an analog circuit until it passes at every corner. Every result here is scored by an independent checker against a published or standard baseline.</p>
    <div class="cta"><a class="btn main" href="#pcb">PCB routing</a><a class="btn paper" href="#analog">Analog sizing</a>
      <a class="btn lilac" href="#pcb-studio">Try it live</a></div>
    <div class="kpis">
      <div class="kpi ok"><b>{p["ours"]}<small>/10</small></b><span>hard PCBWorld boards routed clean. Best published: 4/10</span></div>
      <div class="kpi"><b>4<small>/10</small></b><span>PCBRouteAgent, the published LLM routing agent</span></div>
      <div class="kpi ok"><b>{a["sw_k"]}<small>/{a["sw_n"]}</small></b><span>op-amp sizing runs that pass all 15 PVT points</span></div>
      <div class="kpi"><b>{a["bl_k"]}<small>/{a["bl_n"]}</small></b><span>Bayesian optimisation and random search, same budget</span></div>
    </div>
  </div>
</header>"""


def page() -> str:
    p, a = pcb.hero_numbers(), analog.numbers()
    built = datetime.now().strftime("%-d %b %Y, %H:%M")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>routr</title>
<meta name="description" content="Gemini 4 agents that route circuit boards and size analog circuits, with every result checked.">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}"><link rel="stylesheet" href="style.css">
</head><body>
<nav class="top"><div class="wrap">
  <a class="brand" href="#top"><i></i><span>routr</span></a>
  <div class="tabs"><a href="#pcb">PCB routing</a><a href="#analog">Analog sizing</a><a href="#pcb-studio">Live</a></div>
  <button class="themebtn" aria-label="Switch light or dark theme">{MOON}</button>
</div></nav>
{hero(p, a)}
{pcb.section()}
{analog.section()}
<footer><div class="wrap"><span>routr · Gemini 4 hackathon, New York</span><span>Built {built} from the run files in <code>runs/</code>. Rebuild: <code>.venv/bin/python -m web.build</code></span></div></footer>
<script src="app.js"></script>
</body></html>"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    html = page()
    (OUT / "index.html").write_text(html)
    for f in ("style.css", "app.js"):
        shutil.copy2(HERE / f, OUT / f)
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"wrote {OUT / 'index.html'} ({len(html) // 1024} kB page, {size // 1024} kB with images)")


if __name__ == "__main__":
    main()
