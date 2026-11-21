"""Scoring run: PCBWorld's own scorer (eval.metrics.evaluate_one, the benchmark's post-hoc
stage-2 entry point) on the board each method SUBMITTED, for a fixed list of boards.

Every number comes from re-opening the saved .kicad_pcb in a fresh engine process with the board's
own design rules. No method scores itself.

.venv/bin/python -m pcb.repair.score_run --boards <ids...> --runs freerouting=<folder> control=runs/pcb/hard_control_v2 ...
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pcb.repair.swarm import OUT, PCBW, ROOT, Engine, board_files

FOUR = ["0400_laptimer58_Chickadee", "0473_data-manager_data-manager", "0626_TOBS_HybridChargeController",
        "0599_RGBMatrixPanelCPLD-PhotonBackpack_RGBMatrixPanel_CPLD"]


def find_board(folder: Path, board: str) -> Path | None:
    for pat in (f"submitted/{board}.kicad_pcb", f"{board}.kicad_pcb", f"{board}_freerouting.kicad_pcb",
                f"{board}*.kicad_pcb"):
        hits = sorted(folder.glob(pat))
        if hits:
            return hits[0]
    return None


def score(path: Path, board: str) -> dict:
    e = Engine()
    try:
        return e.call("score", path=str(path), project=str(board_files(board)["pro"]), timeout=300)
    except Exception as ex:
        return {"error": str(ex)[:160]}
    finally:
        e.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boards", nargs="+", default=FOUR)
    ap.add_argument("--runs", nargs="+", required=True, help="label=folder (relative to repo root or to PCBWorld)")
    ap.add_argument("--out", type=Path, default=OUT / "scoreboard")
    a = ap.parse_args()
    methods = []
    for spec in a.runs:
        label, folder = spec.split("=", 1)
        p = Path(folder)
        p = p if p.is_absolute() else (ROOT / p if (ROOT / p).exists() else PCBW / p)
        methods.append((label, p))
    jobs = [(label, b, find_board(folder, b)) for label, folder in methods for b in a.boards]
    t0 = time.time()
    with ThreadPoolExecutor(6) as ex:
        scores = list(ex.map(lambda j: score(j[2], j[1]) if j[2] else {"missing": True}, jobs))
    table: dict = {}
    for (label, b, path), s in zip(jobs, scores):
        table.setdefault(label, {})[b] = {**s, "file": str(path) if path else None}
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "scoreboard.json").write_text(json.dumps({"boards": a.boards, "methods": [m for m, _ in methods],
                                                       "scores": table, "scorer": "eval.metrics.evaluate_one",
                                                       "seconds": round(time.time() - t0, 1)}, indent=1))
    lines = ["| Board | " + " | ".join(m for m, _ in methods) + " |", "|---|" + "---|" * len(methods)]
    for b in a.boards:
        cells = []
        for m, _ in methods:
            s = table[m][b]
            if s.get("missing"):
                cells.append("no board")
            elif s.get("error"):
                cells.append("error")
            else:
                cells.append(("CLEAN" if s.get("clean_pass") else "not clean")
                             + f", {100 * (s.get('routability') or 0):.1f}% routed, {s.get('drv_errors_only_count')} errors")
        lines.append(f"| {b[:34]} | " + " | ".join(cells) + " |")
    lines.append("| **clean boards** | " + " | ".join(
        str(sum(bool(table[m][b].get("clean_pass")) for b in a.boards)) + f" of {len(a.boards)}" for m, _ in methods) + " |")
    lines.append("| **mean routability** | " + " | ".join(
        f"{100 * sum((table[m][b].get('routability') or 0) for b in a.boards) / len(a.boards):.1f}%" for m, _ in methods) + " |")
    md = "\n".join(lines)
    (a.out / "scoreboard.md").write_text(md + "\n")
    print(md)


if __name__ == "__main__":
    main()
