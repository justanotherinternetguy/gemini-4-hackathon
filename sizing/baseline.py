"""Optuna baselines (TPE / GP / random) on the per-role two-stage op-amp, judged at all 15 PVT points.

Objective: minimize worst-corner core current. Constraints (<= 0 feasible): every spec term at
its worst PVT point, normalized by |bound|, plus the per-role saturation-margin requirement.
Budget is counted in ngspice decks (15 per candidate).

python -m sizing.baseline --sampler tpe --candidates 40 --seeds 0 1 2
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

from sizing import opamp2stg as oa

OUT_DIR = oa.ROOT / "runs" / "opamp_baselines"
FAIL = 10.0


def constraint_values(er: oa.EvalResult) -> dict[str, float]:
    vals = {}
    for metric, (op, bound) in er.spec.items():
        w = er.worst.get(metric)
        if w is None or w[0] is None:
            vals[metric] = FAIL
            continue
        s = max(abs(bound), 1e-30)
        vals[metric] = (bound - w[0]) / s if op in (">=", ">") else (w[0] - bound) / s
    # saturation: worst (margin - required) over signal-path devices and points, in units of 50 mV
    worst = None
    for r in er.points:
        if not r.ok:
            worst = -FAIL * 0.05
            break
        for d in r.devices.values():
            if d["role"]:
                slack = d["margin"] - oa.sat_required(d["role"], er.guard)
                worst = slack if worst is None else min(worst, slack)
    vals["saturation"] = -(worst if worst is not None else -FAIL * 0.05) / 0.05
    return vals


def run(sampler: str = "tpe", seed: int = 0, candidates: int = 40, spec: dict | None = None,
        tag: str = "default") -> dict:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    budget = oa.Budget(candidates * len(oa.PVT15))
    history = []

    def objective(trial):
        params = {}
        for k, (_, lo, hi, log) in oa.SPACE.items():
            params[k] = trial.suggest_float(k, lo, hi, log=log)
        er = oa.evaluate(params, spec=spec, budget=budget)
        cons = constraint_values(er)
        for k, v in cons.items():
            trial.set_constraint(k, v)
        icore = er.worst.get("i_core_a")
        history.append({"n": len(history) + 1, "params": er.params, "passed": er.passed,
                        "constraints": cons,
                        "worst": {k: (v[0] if v else None) for k, v in er.worst.items()},
                        "failures": er.failures[:6]})
        return icore[0] if icore and icore[0] is not None else 1.0

    samplers = {"tpe": lambda: optuna.samplers.TPESampler(seed=seed, n_startup_trials=8),
                "gp": lambda: optuna.samplers.GPSampler(seed=seed, n_startup_trials=8),
                "random": lambda: optuna.samplers.RandomSampler(seed=seed)}
    study = optuna.create_study(direction="minimize", sampler=samplers[sampler]())
    # start from the published design (same starting point the agent gets)
    study.enqueue_trial(dict(oa.DEFAULTS))
    t0 = time.perf_counter()
    study.optimize(objective, n_trials=candidates)
    passed = [h for h in history if h["passed"]]
    best = min(passed, key=lambda h: h["worst"]["i_core_a"]) if passed else None
    recheck = None
    if best:   # independent re-check on the full PDK library
        recheck = oa.evaluate(best["params"], spec=spec, lib="full").passed
    summary = {"sampler": sampler, "seed": seed, "candidates": candidates,
               "decks": budget.used, "success": bool(passed), "recheck_full_lib": recheck,
               "candidates_to_first_pass": passed[0]["n"] if passed else None,
               "best_i_core_uA": best["worst"]["i_core_a"] * 1e6 if best else None,
               "best_params": best["params"] if best else None,
               "wall_s": round(time.perf_counter() - t0, 1)}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{tag}_{sampler}_s{seed}_c{candidates}.json").write_text(
        json.dumps({"summary": summary, "spec": spec or oa.DEFAULT_SPEC, "history": history}, indent=1))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sampler", nargs="+", default=["tpe"], choices=["tpe", "gp", "random"])
    ap.add_argument("--seeds", type=int, nargs="+", default=[0])
    ap.add_argument("--candidates", type=int, default=40)
    a = ap.parse_args()
    for s in a.sampler:
        for seed in a.seeds:
            r = run(s, seed, a.candidates)
            print(json.dumps({k: v for k, v in r.items() if k != "best_params"}))
            if r["best_params"]:
                print("   best:", {k: f"{v:.3g}" for k, v in r["best_params"].items()})


if __name__ == "__main__":
    main()
