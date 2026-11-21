# Demo site and videos

One projector-ready page plus the videos for the hackathon demo. Everything on the page is read
from the run folders under `runs/pcb/`; nothing calls a model.

| File | What it does |
|---|---|
| `pick.py` | Ranks the finished boards of the step-agent runs and picks the two to feature. Writes `runs/site/pick.json`. |
| `videos.py` | Renders one video per featured board (agents vs random control) with `pcb/make_video.py`. |
| `build.py` | Writes `runs/site/index.html` (the page) and `runs/site/cards.html` (stills for the summary video). |
| `shots.sh` | Headless-Chromium screenshots of every section into `runs/site/shots/`. |
| `summary.py` | Builds `runs/site/video/summary.mp4` (under 60 s) from the screenshots and the board videos. |
| `data.py` | Shared loader for `history.json`, `llm_calls.jsonl` and the run folders. |

## Rebuild everything

Run from the repository root. One command does all of it and renders only the videos that are
missing or older than their run:

```bash
pcb/site/rebuild.sh            # or: pcb/site/rebuild.sh dev_step_agent:dev_step_random hard_step_agent:hard_step_random
```

The same thing step by step. Steps 1 and 3 take seconds; step 2 takes about five minutes.

```bash
# 1. choose the two boards (prints the ranking and the reason for each)
.venv/bin/python -m pcb.site.pick --runs dev_step_agent:dev_step_random hard_step_agent:hard_step_random

# 2. render their videos into runs/site/video/  (--fps 30 is smoother and three times slower)
.venv/bin/python -m pcb.site.videos --seconds 20 --fps 12

# 3. build the page
.venv/bin/python -m pcb.site.build

# 4. screenshots of every section (check them) and the 60-second summary video
pcb/site/shots.sh
.venv/bin/python -m pcb.site.summary

# open the page (F = fullscreen, arrow keys or 1-7 = sections)
chromium --autoplay-policy=no-user-gesture-required runs/site/index.html
```

When a new run lands, repeat steps 1, 3 and 4. Repeat step 2 only if the featured boards changed
(`pick.py` prints them) or to render one board: `--boards <board id>`.

How the choice is made: boards the agents brought to a clean pass that the random control did not
come first; then boards both brought to a clean pass; then the largest drop in open connections
against the start and against the random control. Ties go to hard-tier test boards.

## Three-minute live demo

Open `runs/site/index.html` in Chromium, press `F`. Section numbers match the bar at the bottom.

| Time | Key | Screen | Say |
|---|---|---|---|
| 0:00 | `1` | The problem | "A circuit board with one unconnected wire is scrap. This is PCBWorld's hard tier: ten real boards. A board passes only if every connection is routed and the rule checker finds nothing." |
| 0:12 | | (bars fill) | "Freerouting, the classic autorouter, passes 3. The published agent on Claude Opus passes 4. We pass the number in green. Grey is the router alone, blue is our scripted repair with no model, green is what the Gemini step agents added." |
| 0:30 | `→` | How the crew works | "The router does 97% of the work and gives up on the last few connections. Gemini 4 Carbon looks at the board picture and writes a strategy note." |
| 0:42 | | (pills light up) | "Then four Gemini 4 Hydrogen agents, each with a temperament, each on its own copy of the board. One action per step: route, route with a via, rip up, roll back. KiCad's router acts, KiCad's rule checker judges, and the agent sees fresh facts before the next step. PCBWorld's scorer picks the best board." |
| 1:00 | | (bottom row) | "The control is a random policy with the same actions and step budget. Gemini Decision screens blockers in the whole-plan arm. A Managed Agent is the long-horizon specialist for boards nothing else fixes." |
| 1:10 | `→` | Featured board 1 | "Here is a real run." Let the video play. "Each panel is one agent's board, zoomed on the open connection in yellow. Grey is the random control." |
| 1:25 | | (point at the feed) | Read one decision aloud from "Gemini's thinking", for example the rip-up: the agent names the net that walls off the open connection, removes it, routes the starved net, then puts the first one back with vias. "That is the actual text from the log." |
| 1:45 | | (point at the chart) | "Open connections after each step. It goes up when an agent rips a net up, then down to zero." Read the two result chips at the top: agents and random. |
| 1:55 | `→` | Featured board 2 | "Second board." Let the video play 10 seconds. Read the chips: where the agents ended, where random ended. Point at the pinned Carbon note and one decision that follows it. |
| 2:20 | `→` | Did the agents matter? | "Same actions, same budget, random choices. Top row: boards brought to a clean pass and connections closed, agents against random." Read one table row. |
| 2:35 | `→` | Scoring table | "The four hard boards nothing had fixed, every method, one scorer: PCBWorld's own evaluate_one." Point at the green column, then at the Managed Agent specialist column next to it. |
| 2:45 | `→` | Honest limits | "What we did not prove." Read the first and third bullets: what still fails, and where the random control did as well. "Single run, no error bars. Most of our score is the scripted repair. The step agents are what moved the last board." |
| 3:00 | | | Stop. For questions: `1`-`7` jump to any section. |

Numbers to have ready (build of 21 Nov, single run per arm; the page shows the current ones):

- Hard tier, clean boards of 10: Freerouting 3, PCBRouteAgent 4, ours 6 (router 3, scripted repair +2, step agents +1).
- Chickadee (0400), hard test board: all four agents reach a clean pass, the fastest in 4 steps and 39 s;
  the random control also reaches it, in 9 steps and 82 s.
- bt-tnc (0492), development board: agents 4 open to 1, random control 4 open to 3. Not clean.
- All 7 boards: agents closed 39 of 79 open connections, the random control 15. Clean boards: 1 each.

If a video does not start, click it once (browser autoplay rules), or start Chromium with the flag above.

## Notes

- The page is a fixed 1920x1080 stage scaled to the window, so it looks the same on any projector.
- `index.html` holds the before and after pictures inline; the videos are separate files in
  `runs/site/video/` and must stay next to the page.
- `pcb/make_video.py` was changed here. Fixes: it cut the fourth agent, `power-aware` had no colour,
  the zoom box lost its aspect ratio at the board edge (panels shrank and labels overlapped the lead
  note), captions ran into the next panel. The panel height now follows the shape of the open
  connections and the chart takes the space that is left. New options: `--fps` (render fewer frames), `--seconds`
  (target length), `--trim` (stop soon after the last board change, so the video has no dead end).
