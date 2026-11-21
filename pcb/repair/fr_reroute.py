"""Continue a PARTIALLY routed board with the classic router (Freerouting).

The agents' strongest lever: they choose a neighbourhood of nets to rip up; this tool hands the
board - everything else still routed - back to Freerouting, which re-routes the open connections
with vias and multi-net negotiation, then the result is merged back into a KiCad board.

Run with PCBWorld's conda env python and PYTHONPATH=build_rl/pcbnew (the pcbnew module), cwd =
third_party/pcbworld:
    python fr_reroute.py --board partial.kicad_pcb --pro rules.kicad_pro --unrouted bare.kicad_pcb \
        --out routed.kicad_pcb [--passes 6] [--timeout 240]
Prints one JSON line: {"ok": bool, "seconds": .., "mode": "all-wires"|"new-wires", "error": ..}
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path.cwd()
sys.path.insert(0, str(REPO / "engine" / "pcbnew_prep"))
sys.path.insert(0, str(REPO))
JAR = REPO / "external" / "freerouting" / "freerouting-2.1.0.jar"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True)
    ap.add_argument("--pro", required=True)
    ap.add_argument("--unrouted", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--passes", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=240)
    a = ap.parse_args()
    t0 = time.time()
    res = {"ok": False}
    try:
        import pcbnew
        from dsn_classes import fix_dsn_classes, parse_pro_classes, verify_dsn_constraints
        from methods.baselines.rule_based._converters import convert_ses

        work = Path(tempfile.mkdtemp(prefix="fr_"))
        # the board must sit next to a project file with the same stem so pcbnew loads its rules
        pcb = work / "board.kicad_pcb"
        pcb.write_text(Path(a.board).read_text(encoding="utf-8"), encoding="utf-8")
        (work / "board.kicad_pro").write_text(Path(a.pro).read_text(encoding="utf-8"), encoding="utf-8")
        board = pcbnew.LoadBoard(str(pcb))
        raw = work / "raw.dsn"
        if not pcbnew.ExportSpecctraDSN(board, str(raw)) or not raw.exists():
            raise RuntimeError("ExportSpecctraDSN failed")
        pro = parse_pro_classes(json.loads(Path(a.pro).read_text(encoding="utf-8")), source=a.pro)
        dsn_text, _ = fix_dsn_classes(raw.read_text(encoding="utf-8"), pro)
        ok_rules, issues = verify_dsn_constraints(dsn_text, pro)
        if not ok_rules:
            raise RuntimeError(f"DSN rules do not match the project ({len(issues)} issues)")
        dsn = work / "board.dsn"
        dsn.write_text(dsn_text, encoding="utf-8")
        ses = work / "board.ses"
        tmp = work / "tmp"
        tmp.mkdir()
        cmd = ["java", "-Djava.awt.headless=true", f"-Djava.io.tmpdir={tmp}", "-Xmx4g", "-jar", str(JAR),
               "-de", str(dsn), "-do", str(ses), "-mp", str(a.passes)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout)
            res["fr_exit"] = p.returncode
        except subprocess.TimeoutExpired:
            res["timeout"] = True
        if not ses.exists() or ses.stat().st_size == 0:
            raise RuntimeError("freerouting produced no session file" + (" (timeout)" if res.get("timeout") else ""))
        # Does the session carry every wire, or only the new ones? Try the bare board first.
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        stats = convert_ses(ses, Path(a.unrouted), out)
        res["mode"] = "onto-bare"
        res["stats"] = {k: (v if isinstance(v, (int, float, str, bool)) else str(v)[:120]) for k, v in dict(stats).items()} \
            if isinstance(stats, dict) else str(stats)[:200]
        (out.with_suffix(".kicad_pro")).write_text(Path(a.pro).read_text(encoding="utf-8"), encoding="utf-8")
        res["ok"] = True
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"[:400]
    res["seconds"] = round(time.time() - t0, 1)
    print("FR_RESULT " + json.dumps(res))


if __name__ == "__main__":
    main()
