"""Single-model-call baseline: the simplest thing that could work.

One Gemini call sees exactly what a swarm worker sees at the start (circuit card, knobs, spec,
the published design and its simulated failures) and proposes ONE sizing, which is then judged
at all 15 PVT points. No iteration, no feedback.

python -m sizing.oneshot --circuit diff --model gemini-4-carbon --seeds 3
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor

from sizing import managed
from sizing import opamp2stg as oa
from sizing import swarm as sw

OUT = oa.ROOT / "runs" / "oneshot"


def run(circuit: str, model: str, seed: int, thinking: str) -> dict:
    s = sw.Swarm(workers=1, per_worker=1, candidates=2, run_id=f"_oneshot_{circuit}_{model}_{seed}")
    start = s._evaluate(dict(oa.DEFAULTS), 0, "start", "published sizing")
    prompt = f"""You are an analog circuit designer. Propose ONE transistor sizing. A SPICE simulator will judge it
at all 15 PVT points; you get no second attempt.

{sw._card()}
TUNABLE KNOBS (display units; values outside the range are clipped):
{sw.knob_table()}

SPEC (all 15 PVT points + saturation rule):
{sw.spec_text(s.spec)}

THE PUBLISHED DESIGN AND ITS SIMULATED RESULT:
{s._history_digest([start])}

Propose exactly 1 candidate (all {len(oa.SPACE)} knobs, display units) with a one-sentence rationale."""
    out = s.llm.json_call(model, prompt, sw.candidate_schema(1), thinking, f"oneshot_{seed}", timeout=300, attempts=2)
    c = out["candidates"][0]
    e = s._evaluate(oa.clamp(sw.from_display(c)), 1, "oneshot", str(c.get("rationale", ""))[:300])
    res = {"circuit": circuit, "model": model, "seed": seed, "thinking": thinking,
           "passed_target": e.er.passed, "passed_nominal_spec": e.er.passed_nominal,
           "n_failures": len(e.er.failures), "worst": {k: (v[0] if v else None) for k, v in e.er.worst.items()},
           "display": sw.to_display(e.params), "rationale": e.rationale}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{circuit}_{model}_s{seed}.json").write_text(json.dumps(res, indent=1))
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--circuit", default="diff", choices=list(oa.CIRCUITS))
    ap.add_argument("--model", default="gemini-4-carbon")
    ap.add_argument("--thinking", default="medium")
    ap.add_argument("--seeds", type=int, default=3)
    a = ap.parse_args()
    managed.load_key()
    oa.use(a.circuit)
    with ThreadPoolExecutor(a.seeds) as ex:
        rows = list(ex.map(lambda i: run(a.circuit, a.model, i, a.thinking), range(a.seeds)))
    for r in rows:
        w = r["worst"]
        print(f"{r['circuit']} {r['model']} s{r['seed']}: target={'PASS' if r['passed_target'] else 'fail'} "
              f"spec={'PASS' if r['passed_nominal_spec'] else 'fail'} n_fail={r['n_failures']} "
              f"pm={w['pm_deg'] and round(w['pm_deg'],1)} ugf={w['ugf_hz'] and round(w['ugf_hz']/1e6,1)}M "
              f"gain={w['dc_gain_db'] and round(w['dc_gain_db'],1)} I={w['i_core_a'] and round(w['i_core_a']*1e6,1)}uA")
    print(f"{a.circuit} {a.model}: {sum(r['passed_target'] for r in rows)}/{len(rows)} pass target, "
          f"{sum(r['passed_nominal_spec'] for r in rows)}/{len(rows)} pass bare spec")


if __name__ == "__main__":
    main()
