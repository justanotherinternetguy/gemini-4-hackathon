"""How much work the agents did, totalled from the run logs under runs/ at build time.

Only counts what a log records; where a log has no token count the call still counts as a call
but adds no tokens, so every total here is a lower bound ("at least")."""
from __future__ import annotations

import json
from pathlib import Path

RUNS = Path(__file__).resolve().parents[1] / "runs"
SKIP = ("_fake", "dummy", "test_frames")
CALL_LOGS = ("llm_calls.jsonl", "architect.jsonl", "council.jsonl", "ask_log.jsonl")


def _lines(p: Path):
    try:
        for line in p.read_text().splitlines():
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    pass
    except OSError:
        return


def _tokens(d: dict) -> int:
    for k in ("tokens", "total_tokens", "_tokens"):
        v = d.get(k)
        if isinstance(v, (int, float)):
            return int(v)
    if isinstance(d.get("in"), (int, float)):
        return int(d["in"]) + int(d.get("out") or 0)
    for v in d.values():                          # e.g. council proposals keep _tokens one level down
        if isinstance(v, dict) and isinstance(v.get("_tokens"), (int, float)):
            return int(v["_tokens"])
    return 0


def totals() -> dict:
    calls = tokens = 0
    models: dict[str, int] = {}
    for name in CALL_LOGS:
        for p in RUNS.rglob(name):
            if any(s in str(p) for s in SKIP):
                continue
            for d in _lines(p):
                calls += 1
                tokens += _tokens(d)
                m = d.get("model")
                if isinstance(m, str):
                    models[m] = models.get(m, 0) + 1
    # managed agents: one row per turn; total_tokens is cumulative, so take the last row of each log
    for p in RUNS.rglob("interactions.jsonl"):
        rows = list(_lines(p))
        calls += len(rows)
        if rows:
            tokens += int(rows[-1].get("total_tokens") or 0)
    decks = 0
    for p in RUNS.rglob("summary.json"):
        if any(s in str(p) for s in SKIP) or "swarm" not in str(p):
            continue
        try:
            decks += int(json.loads(p.read_text()).get("decks") or 0)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    boards = {p.stem for p in (RUNS / "pcb").rglob("*.kicad_pcb")}
    board_ids = {b.split("__")[0] for b in boards if "__" in b}
    return {"calls": calls, "tokens": tokens, "models": models, "spice_decks": decks,
            "board_versions": len(boards), "boards": len(board_ids)}


if __name__ == "__main__":
    print(json.dumps(totals(), indent=1))
