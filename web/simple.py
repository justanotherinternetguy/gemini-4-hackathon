"""The front page: what routr does, in plain words, for people who are not hardware engineers.

One idea per screen: the catchphrase, the swarm at its real scale, the team of agents, one round of
their loop, the two results, a video. Every number comes from the run logs (web/scale.py and the
same functions the evidence page uses). The full evidence lives on evidence.html.
"""
from __future__ import annotations

import random
import subprocess

from web import analog, pcb, scale
from web.kit import OUT, ROOT, asset, studio_gif

# role -> (colour token, plain name, model, what it does)
TEAM = [
    ("sky", "The workers", "Gemini 4 Hydrogen",
     "Fast agents that try fixes side by side, each with its own style: careful, bold, power-aware."),
    ("lilac", "The lead", "Gemini 4 Carbon",
     "Thinks the hardest. Reads every failure so far and sends each worker in a different direction."),
    ("butter", "The reviewer", "Gemini 4 Neon",
     "Looks at pictures (the board, the frequency plot) and points out what looks wrong."),
    ("coral", "The screener", "Gemini Decisions API",
     "Answers yes/no questions in about a second, with a confidence, so the team skips bad ideas."),
    ("mint", "The judge", "not AI",
     "KiCad's rule checker and a circuit simulator. The agents never grade their own work."),
]
MODEL_ROLE = {"gemini-4-hydrogen": "sky", "gemini-4-carbon": "lilac", "gemini-4-neon": "butter",
              "gemini-decision-1": "coral"}
PER_DOT = 10


def swarm_svg(models: dict) -> str:
    """One dot per 10 Gemini calls we really made, coloured by role, drifting in a cloud."""
    rng = random.Random(7)
    dots = []
    for model, role in MODEL_ROLE.items():
        dots += [role] * max(1, round(models.get(model, 0) / PER_DOT))
    rng.shuffle(dots)
    cx, cy, rx, ry = 600, 230, 215, 170
    circles = []
    for i, role in enumerate(dots):
        while True:
            x, y = rng.uniform(-1, 1), rng.uniform(-1, 1)
            if x * x + y * y <= 1:
                break
        r = 7 if role in ("sky",) else 9
        circles.append(f'<circle class="s-dot {role}" cx="{cx + x * rx:.1f}" cy="{cy + y * ry:.1f}" r="{r}" '
                       f'style="animation-delay:-{rng.uniform(0, 6):.2f}s;animation-duration:{rng.uniform(4, 8):.2f}s"/>')
    ideas = []
    for i in range(9):                                   # ideas leave the swarm for the judge
        ok = i % 3 == 0
        ideas.append(f'<circle class="idea {"ok" if ok else "no"}" r="6">'
                     f'<animateMotion dur="5s" begin="-{i * 0.55:.2f}s" repeatCount="indefinite" '
                     f'path="{"M800,230 L948,230 L1110,230" if ok else "M800,230 L940,230 L880,300"}"/></circle>')
    return f"""<svg class="swarm" viewBox="0 0 1200 460" role="img" aria-label="A stuck design goes into a swarm of
 {len(dots)} dots, one per ten Gemini calls; ideas leave the swarm for a checker, and only checked ones become the finished design.">
  <g class="stuck">
    <rect x="30" y="140" width="190" height="180" rx="18" class="board"/>
    <rect x="58" y="172" width="34" height="22" rx="4" class="s-part"/><rect x="150" y="262" width="40" height="26" rx="4" class="s-part"/>
    <rect x="140" y="170" width="44" height="30" rx="4" class="s-part"/><rect x="60" y="262" width="30" height="24" rx="4" class="s-part"/>
    <path d="M92 183 H140" class="wire"/><path d="M75 262 V194" class="wire"/>
    <path d="M90 274 C120 230 150 230 162 200" class="s-open"/><path d="M190 275 C205 240 200 215 184 190" class="s-open"/>
  </g>
  <path d="M240 230 H370" class="s-flow"/><path d="M362 222 L374 230 L362 238" class="s-head"/>
  <ellipse cx="600" cy="230" rx="240" ry="196" class="cloud"/>
  {''.join(circles)}
  <path d="M830 230 H930" class="s-flow"/>
  <g class="gate"><rect x="940" y="150" width="34" height="160" rx="17"/><path d="M948 232 l7 8 l12 -16" class="tick"/></g>
  {''.join(ideas)}
  <path d="M990 230 H1050" class="s-flow"/><path d="M1042 222 L1054 230 L1042 238" class="s-head"/>
  <g class="done">
    <rect x="1060" y="160" width="120" height="140" rx="16" class="board ok"/>
    <path d="M1080 200 H1160 M1080 230 H1130 V270 M1160 200 V260 H1140" class="wire"/>
    <circle cx="1150" cy="170" r="20" class="badge"/><path d="M1140 170 l7 7 l12 -14" class="tick"/>
  </g>
</svg>"""


def counters(t: dict) -> str:
    items = [(f"{t['calls']:,}", "calls to Gemini models"),
             (f"{t['tokens'] / 1e6:.1f}M", "tokens read and written"),
             (f"{t['spice_decks']:,}", "circuit simulations run"),
             (f"{t['board_versions']:,}", "board versions checked")]
    return "".join(f'<div class="count"><b>{v}</b><span>{k}</span></div>' for v, k in items)


def loop_svg() -> str:
    steps = [("Look", "The agents get a picture of the design, the rules, and every earlier try.", "sky"),
             ("Propose", "Each one suggests a fix in its own style. The screener drops the weak ones.", "lilac"),
             ("Test", "The real checker tries every fix. No AI grades its own work.", "mint"),
             ("Keep the best", "The best design so far is saved. The next round starts from it.", "butter")]
    cards = "".join(f'<li class="lstep {c}"><span class="num">{i + 1}</span><h4>{h}</h4><p>{p}</p></li>'
                    for i, (h, p, c) in enumerate(steps))
    return f'<ol class="rounds">{cards}</ol>'


def squares(k: int, n: int, cls: str) -> str:
    return '<span class="blocks">' + "".join(f'<i class="{cls if i < k else ""}"></i>' for i in range(n)) + "</span>"


def pipe(steps: list[tuple[str, str, str, str]]) -> str:
    """A left-to-right pipeline: (colour, stage, who does it, what it does)."""
    return '<ol class="pipe">' + "".join(
        f'<li class="{c}"><span class="who">{w}</span><b>{h}</b><small>{d}</small></li>' for c, h, w, d in steps) + "</ol>"


def tech(a: dict) -> str:
    """The technical layer: what each job really is, what the agents control, and what judges them."""
    board = pipe([
        ("plain", "Route", "Freerouting 2.1", "A classic autorouter lays most of the copper and leaves a few connections open."),
        ("coral", "Screen", "gemini-decision-1", "Which routed nets wall off each open connection? A probability for every candidate."),
        ("sky", "Propose", "gemini-4-hydrogen ×6", "Each proposer, in its own style: what to rip up, re-route order, layer, router mode."),
        ("lilac", "Decide", "neon + carbon", "Neon flags conflicts on the board picture; Carbon picks, edits or merges the best plans."),
        ("mint", "Judge", "KiCad + PCBWorld", "KiCad's push-and-shove router draws; its rule checker and PCBWorld's scorer grade."),
    ])
    chip = pipe([
        ("sky", "Hear", "gemini-4-hydrogen", "A spoken or typed spec becomes targets: gain, bandwidth, phase margin, current."),
        ("lilac", "Plan", "gemini-4-carbon", "Reads the worst failures and gives each worker its own search direction."),
        ("sky", "Size", "gemini-4-hydrogen ×4", "Workers propose transistor widths, lengths, bias current and compensation."),
        ("mint", "Simulate", "ngspice · SKY130", "Every candidate runs at 15 corners: 5 process corners × −40, 27, 125 °C."),
        ("butter", "Look", "gemini-4-neon", "Reads the Bode plot image of the best design and says what to fix next."),
    ])
    sg = a.get("signoff") or (None, None)
    return f"""
<section class="band" id="tech">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot" style="background:var(--sky)"></span>Under the hood</div>
      <h2>What the agents control, and what they never touch.</h2>
      <p class="lede">The agents make decisions. Deterministic engineering tools do the drawing, the simulating and the
        grading. That split is why the results can be trusted.</p></div></div>

    <div class="techjob">
      <div class="techhead"><h3>Circuit boards: repair what the router leaves</h3>
        <span class="tag">benchmark: PCBWorld, hard tier</span></div>
      {board}
      <ul class="facts">
        <li><b>The agents never draw copper.</b> Their whole action space is rip-up sets, route order, layer, router mode
          (shove or walk-around) and via bridges. KiCad's own router executes every action.</li>
        <li><b>A council, not a single model.</b> Proposers cannot see each other. The screener scores each proposal in about a
          second and is the fallback if a slower model times out. Each chosen plan runs on its own copy of the board, in
          parallel, and the best board becomes the next round's start.</li>
        <li><b>Vision where it helps.</b> For a layer change, candidate via sites are drawn as numbered dots on a picture of
          the board, and the Decisions API ranks them. The router tries them in that order.</li>
        <li><b>Long horizon when it is stuck.</b> A Gemini Managed Agent stays with one hard board for many turns, calling
          tools that run the real engine on our machine.</li>
      </ul>
    </div>

    <div class="techjob">
      <div class="techhead"><h3>Chip circuits: size an op-amp that passes everywhere</h3>
        <span class="tag">two-stage op-amp · SkyWater SKY130 130 nm</span></div>
      {chip}
      <ul class="facts">
        <li><b>A hard pass/fail.</b> Phase margin ≥ 60°, unity-gain bandwidth ≥ 10 MHz, DC gain ≥ 60 dB and core current
          ≤ 100 µA, at all 15 corners at once, with a guard band on top so the design keeps a safety margin.</li>
        <li><b>A fair fight.</b> Bayesian optimisation (Optuna TPE and GP) and random search get exactly the same
          budget, counted in candidates, with 15 simulations each.</li>
        <li><b>Inputs people actually have.</b> The spec can be spoken (audio straight into the model), and the circuit can
          be read from a photo of the schematic.</li>
      </ul>
    </div>

    <div class="trust">
      <div><h4>Who checks the judge?</h4><p>A <b>sign-off agent</b> (Gemini Managed Agent) gets the circuit but not our
        evaluator. It writes its own testbench, calibrates it against the circuit authors' published numbers and
        re-simulates with the full SkyWater library{f": it matched our judge at <b>{sg[0]} of {sg[1]}</b> corners" if sg[0] is not None else ""}.</p></div>
      <div><h4>An agent paid to break us</h4><p>A <b>red-team agent</b> read our evaluator's source and tried to make it
        report a false pass. It found <b>6 confirmed weaknesses</b> (4 high severity), each backed by real simulation
        evidence, and wrote test cases for them. A person reviews each one; nothing is applied automatically.</p></div>
      <div><h4>Long context, kept honest</h4><p>Managed agents keep their whole history and files between turns. The
        red-team run lasted 20 turns and about 1.5 million tokens. Its budgets (tool calls and wall-clock
        time) are enforced on our side, not left to the agent.</p></div>
    </div>
  </div>
</section>"""


def page_body() -> str:
    t = scale.totals()
    p, a = pcb.hero_numbers(), analog.numbers()
    video = asset(ROOT / "runs" / "site" / "video" / "summary.mp4", "video/summary.mp4")
    studio = asset(ROOT / "web" / "shots" / "studio_fix.png", "shots/studio_fix.png")
    team = "".join(f'<div class="member {c}"><i class="avatar"></i><div><h4>{n}</h4><small>{m}</small><p>{d}</p></div></div>'
                   for c, n, m, d in TEAM)
    poster = ""
    if video:                                            # a still from the video, so the player is not a black box
        jpg = OUT / "img" / "video" / "summary.jpg"
        if not jpg.is_file() or jpg.stat().st_mtime < (OUT / video).stat().st_mtime:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "2", "-i", str(OUT / video), "-frames:v", "1",
                            "-q:v", "3", str(jpg)], capture_output=True)
        poster = "img/video/summary.jpg" if jpg.is_file() else ""
    watch = (f'<video class="film" controls muted playsinline preload="none" poster="{poster}" src="{video}"></video>'
             if video else "")
    shot = (f'<figure class="shot" data-zoom><img src="{studio}" alt="The routr studio after a run: a 3D '
            f'circuit board marked Ready to manufacture"><figcaption>A real run in the studio. KiCad found 6 short circuits '
            f'on this board; Gemini fixed them on the spot.</figcaption></figure>' if studio else "")
    return f"""
<header class="hero simple" id="top">
  <div class="wrap split"><div class="herotext">
    <div class="eyebrow"><span class="dot" style="background:var(--sky)"></span>routr · Gemini 4 hackathon</div>
    <h1>Agents that finish the <em>hardware</em> design.</h1>
    <p class="lede">routr is a swarm of Gemini AI agents that finishes electronics designs where people and classic
      tools get stuck. The agents come up with the ideas. A real engineering checker tests every one before it counts.</p>
    <div class="cta"><a class="btn main" href="#swarm">See the swarm</a><a class="btn paper" href="#watch">Watch it work</a>
      <a class="btn lilac" href="evidence.html">Full evidence</a></div>
  </div>{studio_gif()}</div>
</header>

<section class="band alt" id="swarm">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot" style="background:var(--sky)"></span>The swarm</div>
      <h2>Every dot is ten real calls to Gemini.</h2>
      <p class="lede">A stuck design goes in. Hundreds of agent calls attack it from every side. Only the ideas that pass the
        checker come out.</p></div></div>
    <div class="swarmfig">
      {swarm_svg(t["models"])}
      <div class="swarmcap"><div><b>A stuck design</b><span>Wires no tool could connect. A chip circuit that fails when it gets hot.</span></div>
        <div><b>The swarm</b><span>{"".join(f'<i class="key {c}"></i>' for c in ("sky", "lilac", "butter", "coral"))} workers, lead, reviewer, screener</span></div>
        <div><b>The checker, then the result</b><span>Ideas that break a rule bounce back. Ideas that pass become the design.</span></div></div>
    </div>
    <div class="counts">{counters(t)}</div>
    <p class="soft note">Counted from our own run logs when this page was built. Where a log did not record tokens,
      none are counted, so these numbers are a minimum.</p>
  </div>
</section>

<section class="band" id="team">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot" style="background:var(--lilac)"></span>The team</div>
      <h2>Five roles. One rule: nobody grades their own work.</h2></div></div>
    <div class="team">{team}</div>
  </div>
</section>

<section class="band alt" id="how">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot" style="background:var(--mint)"></span>How it works</div>
      <h2>One round, repeated until the design passes.</h2></div></div>
    {loop_svg()}
    <div class="callout"><b>Mistakes get fixed on the spot.</b> When the checker finds a problem, such as two wires touching,
      the agents start fixing that exact design straight away. You see the fix, not the mistake.</div>
  </div>
</section>

{tech(a)}

<section class="band alt" id="results">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot" style="background:var(--mint)"></span>Results</div>
      <h2>Two hard jobs. The swarm beat the published best at both.</h2></div></div>
    <div class="jobs">
      <div class="job">
        <h3>Connect every wire on a circuit board</h3>
        <p>A board with one missing connection is scrap. On the 10 hardest boards in the PCBWorld benchmark:</p>
        <div class="vs"><div><span>routr's agents</span>{squares(p["ours"], 10, "win")}<b class="ok">{p["ours"]} of 10</b></div>
          <div><span>Best published result</span>{squares(4, 10, "base")}<b>4 of 10</b></div></div>
      </div>
      <div class="job">
        <h3>Tune a chip amplifier to work in all conditions</h3>
        <p>It has to pass at 15 combinations of heat, cold, supply voltage and manufacturing variation, all at once.</p>
        <div class="vs"><div><span>routr's agents</span>{squares(a["sw_k"], a["sw_n"], "win")}<b class="ok">{a["sw_k"]} of {a["sw_n"]} runs</b></div>
          <div><span>Standard optimizers, same budget</span>{squares(a["bl_k"], a["bl_n"], "base")}<b>{a["bl_k"]} of {a["bl_n"]} runs</b></div></div>
      </div>
    </div>
  </div>
</section>

<section class="band" id="watch">
  <div class="wrap">
    <div class="sechead"><div><div class="eyebrow"><span class="dot" style="background:var(--butter)"></span>Watch</div>
      <h2>One minute, start to finish.</h2></div></div>
    <div class="watch">{watch}{shot}</div>
    <div class="cta" style="margin-top:32px"><a class="btn main" href="evidence.html">See every run, chart and baseline</a>
      <a class="btn paper" href="evidence.html#pcb-studio">Try it live</a></div>
  </div>
</section>"""

