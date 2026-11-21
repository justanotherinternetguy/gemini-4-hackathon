"""Choose the two boards to feature on the demo site.

    .venv/bin/python -m pcb.site.pick [--runs dev_step_agent:dev_step_random hard_step_agent:hard_step_random]

Ranking, best first:
  tier 3  the agents reached a clean pass and the random control (finished) did not
  tier 2  the agents reached a clean pass; the control also did, or has not finished
  tier 1  no clean pass: ranked by the reduction in open connections against the start
          and against the random control, both as a fraction of the starting count
Ties: hard-tier test boards first, then more open connections at the start.
Writes runs/site/pick.json (read by build.py).
"""
from __future__ import annotations

import argparse
import json

from pcb.site.data import DEFAULT_PAIRS, ROOT, load_pairs, short


def rank(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if not r.get("finished"):
            continue
        a, c = r["agent"], r["random"]
        s = max(a["start_open"] or 0, 1)
        vs_start = (a["start_open"] - a["best_open"]) / s
        vs_rand = ((c["best_open"] - a["best_open"]) / s) if c else 0.0
        if a["best_clean"] and c and not c["best_clean"]:
            tier, why = 3, "agents reached a clean pass; random control did not"
        elif a["best_clean"]:
            tier = 2
            why = "agents reached a clean pass; " + ("random control also did" if c else "random control not finished")
        else:
            tier = 1
            why = (f"no clean pass; open {a['start_open']} -> {a['best_open']}"
                   + (f", random control ended at {c['best_open']}" if c else ", random control not finished"))
        if a["start_open"] == 0:
            tier, why = 0, "already clean after the classic router"
        out.append({"board": r["board"], "name": short(r["board"]), "agent_run": r["agent_run"],
                    "random_run": r["random_run"], "hard_test": r["hard_test"], "tier": tier, "why": why,
                    "start_open": a["start_open"], "agent_open": a["best_open"], "agent_clean": a["best_clean"],
                    "best_agent": a["best_agent"], "random_open": c["best_open"] if c else None,
                    "random_clean": c["best_clean"] if c else None, "vs_start": round(vs_start, 3),
                    "vs_random": round(vs_rand, 3), "score": round(vs_start + vs_rand, 3),
                    "agent_steps": a["steps_total"]})
    out.sort(key=lambda x: (-x["tier"], -x["score"], not x["hard_test"], -x["start_open"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", nargs="+", default=DEFAULT_PAIRS, help="agent_run:random_run pairs")
    ap.add_argument("-n", type=int, default=2)
    a = ap.parse_args()
    rows = load_pairs(a.runs)
    ranked = rank(rows)
    pending = [f"{r['agent_run']}/{r['board']}" for r in rows if not r.get("finished")]
    print(f"{'#':>2} {'board':44} {'run':16} tier  start -> agents (best)        random   score  why")
    for i, x in enumerate(ranked):
        ro = "-" if x["random_open"] is None else f"{x['random_open']}{' clean' if x['random_clean'] else ''}"
        ag = f"{x['agent_open']}{' CLEAN' if x['agent_clean'] else ''} ({x['best_agent']})"
        mark = "*" if i < a.n else " "
        print(f"{mark}{i + 1:>1} {x['board'][:44]:44} {x['agent_run']:16} {x['tier']:>3}   {x['start_open']:>3} -> {ag:22} "
              f"{ro:8} {x['score']:5.2f}  {x['why']}")
    if pending:
        print("not finished yet (no history.json):", ", ".join(pending))
    out = ROOT / "runs" / "site" / "pick.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"featured": ranked[: a.n], "ranking": ranked, "pending": pending,
                               "pairs": a.runs}, indent=1))
    print("featured:", ", ".join(x["board"] for x in ranked[: a.n]), "->", out)


if __name__ == "__main__":
    main()
