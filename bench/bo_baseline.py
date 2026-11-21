"""Optuna baselines (GP-BO by default) on AMS-SizingBench circuits, at a fixed simulation budget.

Each design variable is an integer index into its benchmark grid. The objective is to
maximize the circuit's FoM; every spec term becomes an Optuna constraint (<= 0 means
feasible), normalized by |bound|. A failed simulation violates every constraint.

python -m bench.bo_baseline                                   # 5 circuits x 3 seeds, GP, 100 sims
python -m bench.bo_baseline smc --sampler tpe --budget 200 --seeds 0 1 2
"""
from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from bench import amsbench as ab

HELD_OUT = ["telescopic_ota", "azc", "smc", "iac", "fdgb"]
OUT_DIR = ab.ROOT / "runs" / "baselines"
FAIL_VIOLATION = 10.0   # constraint value for a failed sim / missing metric
FAIL_OBJECTIVE = -1.0   # FoM assigned to a failed sim


def _violations(cons: list[ab.Constraint], metrics: dict) -> list[float]:
    out = []
    for c in cons:
        v = metrics.get(c.metric)
        if v is None:
            out.append(FAIL_VIOLATION)
            continue
        scale = max(abs(c.bound), 1e-12)
        out.append((c.bound - v) / scale if c.op in (">", ">=") else (v - c.bound) / scale)
    return out


def _make_sampler(kind: str, seed: int):
    import optuna
    # constraints are reported per trial with trial.set_constraint (Optuna >= 5)
    if kind == "gp":
        return optuna.samplers.GPSampler(seed=seed, n_startup_trials=10)
    if kind == "tpe":
        return optuna.samplers.TPESampler(seed=seed, n_startup_trials=10)
    if kind == "random":
        return optuna.samplers.RandomSampler(seed=seed)
    raise ValueError(kind)


def run_one(circuit: str, sampler: str = "gp", seed: int = 0, budget: int = 100,
            corner: str = "tt", temp: float = 27) -> dict:
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    import optuna
    import torch
    torch.set_num_threads(1)
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    cfg = ab.load(circuit)
    cons = ab.parse_specs(cfg["user_specs_metric"])
    names = ab.variables(cfg)
    grids = {v: ab.grid(cfg, v) for v in names}
    trials: list[dict] = []
    cache: dict[tuple, dict] = {}

    def objective(trial):
        point = {v: grids[v][trial.suggest_int(v, 0, len(grids[v]) - 1)] for v in names}
        key = tuple(point[v] for v in names)
        if key not in cache:
            r = ab.simulate(cfg, point, corner, temp)
            cache[key] = r.metrics if r.ok else {k: r.metrics.get(k) for k in r.metrics}
        metrics = cache[key]
        viol = _violations(cons, metrics)
        feasible = all(x <= 0 for x in viol)
        fom = metrics.get("fom")
        for c, x in zip(cons, viol):
            trial.set_constraint(str(c), x)
        trials.append({"n": len(trials) + 1, "point": point, "metrics": metrics,
                       "feasible": feasible, "new_sim": len(cache)})
        return fom if fom is not None else FAIL_OBJECTIVE

    study = optuna.create_study(
        direction="maximize",
        sampler=_make_sampler(sampler, seed))
    t0 = time.perf_counter()
    study.optimize(objective, n_trials=budget)
    wall = time.perf_counter() - t0

    feas = [t for t in trials if t["feasible"]]
    best = max(feas, key=lambda t: t["metrics"].get("fom") or -1) if feas else None
    # re-check the best design independently (fresh sim, full PDK library)
    recheck = None
    if best:
        r = ab.simulate(cfg, best["point"], corner, temp, lib="full")
        recheck = ab.check(cfg, r.metrics)[0]
    summary = {
        "circuit": circuit, "sampler": sampler, "seed": seed, "budget": budget,
        "corner": corner, "temp": temp, "spec": cfg["user_specs_metric"],
        "success": bool(feas), "recheck_pass": recheck,
        "sims_to_first_pass": feas[0]["n"] if feas else None,
        "unique_sims": len(cache), "n_feasible": len(feas),
        "best_fom": best["metrics"].get("fom") if best else None,
        "best_point": best["point"] if best else None,
        "best_metrics": best["metrics"] if best else None,
        "wall_s": round(wall, 1),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{circuit}_{sampler}_s{seed}_b{budget}_{corner}.json"
    out.write_text(json.dumps({"summary": summary, "trials": trials}, indent=1))
    return summary


def _job(args):
    return run_one(*args)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("circuits", nargs="*")
    ap.add_argument("--sampler", nargs="+", default=["gp"], choices=["gp", "tpe", "random"])
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    ap.add_argument("--budget", type=int, default=100)
    ap.add_argument("--corner", default="tt")
    ap.add_argument("--temp", type=float, default=27)
    ap.add_argument("-j", type=int, default=15)
    a = ap.parse_args()
    jobs = [(c, s, seed, a.budget, a.corner, a.temp)
            for c in (a.circuits or HELD_OUT) for s in a.sampler for seed in a.seeds]
    with ProcessPoolExecutor(a.j) as ex:
        rows = list(ex.map(_job, jobs))

    print(f"\nbudget={a.budget} sims/run, corner={a.corner}, temp={a.temp}")
    print(f"{'circuit':16s} {'sampler':7s} {'success':>8s} {'recheck':>8s} {'sims->1st pass':>15s} "
          f"{'best FoM (median)':>18s} {'wall s':>7s}")
    by = {}
    for r in rows:
        by.setdefault((r["circuit"], r["sampler"]), []).append(r)
    for (c, s), rs in by.items():
        k = sum(r["success"] for r in rs)
        rc = sum(bool(r["recheck_pass"]) for r in rs)
        firsts = sorted(r["sims_to_first_pass"] for r in rs if r["sims_to_first_pass"])
        foms = sorted(r["best_fom"] for r in rs if r["best_fom"] is not None)
        med = lambda xs: xs[len(xs) // 2] if xs else None
        print(f"{c:16s} {s:7s} {k:>5d}/{len(rs):<2d} {rc:>5d}/{len(rs):<2d} "
              f"{str(med(firsts) or '-'):>15s} {(f'{med(foms):.3g}' if foms else '-'):>18s} "
              f"{max(r['wall_s'] for r in rs):>7.0f}")


if __name__ == "__main__":
    main()
