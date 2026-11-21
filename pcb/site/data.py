"""Load step-agent runs (runs/pcb/<run_id>/) into plain dicts for pick.py and build.py.

A board counts as finished when its history.json exists (the stepper writes it last).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs" / "pcb"
DEFAULT_PAIRS = ["dev_step_agent:dev_step_random", "hard_step_agent:hard_step_random"]
HARD_TEST = {"hard_step_agent"}


def _json(p: Path, default=None):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def _jsonl(p: Path) -> list:
    out = []
    if p.is_file():
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def short(board: str) -> str:
    """0400_laptimer58_Chickadee -> 'Chickadee (0400)'."""
    num, _, rest = board.partition("_")
    parts = rest.split("_") if rest else [board]
    name = parts[-1]
    if len(parts) > 1 and len(name) < 6:      # 'tnc' -> 'bt-tnc', 'CPLD' -> 'RGBMatrixPanel CPLD'
        name = parts[-2] if parts[-2].lower().endswith(name.lower()) else f"{parts[-2]} {name}"
    return f"{name} ({num})"


def load_side(run_id: str, board: str) -> dict | None:
    """One run's view of one board, or None if that board has not finished in that run."""
    d = RUNS / run_id / board
    hist = _json(d / "history.json")
    if not hist:
        return None
    start = hist[0]
    s_open = (start.get("end_status") or {}).get("unrouted")
    agents = []
    for r in hist[1:]:
        es, sc = r.get("end_status") or {}, r.get("score") or {}
        steps = r.get("steps") or []
        agents.append({
            "agent": r.get("agent") or r.get("tag"), "policy": r.get("policy"),
            "open": es.get("unrouted"), "errors": es.get("drc_errors"),
            "clean": bool(sc.get("clean_pass")), "routability": sc.get("routability"),
            "drv": sc.get("drv_errors_only_count"), "score_full": sc, "n_steps": r.get("n_steps", len(steps)),
            "seconds": r.get("seconds"), "ok": r.get("ok", True),
            "steps": [{k: s.get(k) for k in ("action", "nets", "mode", "before", "after", "err_after", "detail",
                                             "thought")} for s in steps],
            "traj": [s_open] + [s.get("after") if s.get("after") is not None else s.get("before") or s_open
                                for s in steps],
        })
    ok = [a for a in agents if a["ok"] and a["open"] is not None]
    best = min(ok, key=lambda a: (not a["clean"], a["open"], a["errors"] or 0, a["n_steps"] or 0), default=None)
    start_sc = start.get("score") or {}
    if best is None or (start_sc.get("clean_pass") and not best["clean"]):
        best_open, best_clean, best_name = s_open, bool(start_sc.get("clean_pass")), "router"
    else:
        best_open, best_clean, best_name = best["open"], best["clean"], best["agent"]
        if best_open > s_open and not best_clean:
            best_open, best_name = s_open, "router"
    final = (best or {}).get("score_full") or start_sc
    # the run's own summary says which board was kept and how PCBWorld scored it; prefer that
    res = next((x for x in (_json(RUNS / run_id / "summary.json", {}) or {}).get("results", [])
                if x.get("board") == board), None)
    if res and res.get("final"):
        final = res["final"]
        best_name = res.get("best_agent") or best_name
        best_clean = bool(final.get("clean_pass"))
        rec = next((a for a in agents if a["agent"] == best_name), None)
        best_open = rec["open"] if rec and rec["open"] is not None else s_open
    # fewest steps (and seconds) any agent needed to end at the kept result
    same = [a for a in ok if a["open"] == best_open and a["clean"] == best_clean] if best_name != "router" else []
    best_steps = min([a["n_steps"] for a in same if a["n_steps"]], default=None)
    best_seconds = min([a["seconds"] for a in same if a["seconds"]], default=None)
    calls = _jsonl(d / "llm_calls.jsonl")
    dec = [c for c in calls if c.get("decision")]
    lead = next((c for c in calls if c.get("agent") == "lead" and c.get("note")), None)
    return {
        "run": run_id, "board": board, "dir": str(d), "live": str(RUNS / run_id / "live" / board),
        "start_open": s_open, "start_errors": (start.get("end_status") or {}).get("drc_errors"),
        "start_score": start_sc, "agents": agents, "best_agent": best_name, "best_open": best_open,
        "best_clean": best_clean, "final": final, "best_steps": best_steps, "best_seconds": best_seconds,
        "steps_total": sum(a["n_steps"] or 0 for a in agents),
        "seconds": max([a["seconds"] or 0 for a in agents], default=0),
        "llm_calls": len(dec) + (1 if lead else 0), "tokens": sum(c.get("tokens") or 0 for c in calls),
        "llm_seconds": round(sum(c.get("latency_s") or 0 for c in dec), 1),
        "lead_note": (lead or {}).get("note"), "lead_model": (lead or {}).get("model"),
        "step_model": next((c.get("model") for c in dec), None),
        "has_start_png": (d / "start.png").is_file(), "has_final_png": (d / "final.png").is_file(),
    }


def load_pairs(pairs: list[str] | None = None) -> list[dict]:
    """Every finished board of every agent run, with its random control (or None)."""
    rows = []
    for pair in pairs or DEFAULT_PAIRS:
        a_run, _, r_run = pair.partition(":")
        if not (RUNS / a_run).is_dir():
            continue
        for d in sorted((RUNS / a_run).iterdir()):
            if d.name in ("live", "submitted") or not d.is_dir():
                continue
            ag = load_side(a_run, d.name)
            if ag is None:
                rows.append({"board": d.name, "agent_run": a_run, "random_run": r_run, "finished": False})
                continue
            rows.append({"board": d.name, "agent_run": a_run, "random_run": r_run, "finished": True,
                         "hard_test": a_run in HARD_TEST, "agent": ag,
                         "random": load_side(r_run, d.name) if r_run else None})
    return rows
