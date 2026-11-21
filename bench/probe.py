"""Random-search probe: is each benchmark spec reachable on its grid at a given corner?

python -m bench.probe [circuits...] -n 64 --corner tt
"""
from __future__ import annotations

import argparse
import random
from concurrent.futures import ProcessPoolExecutor

from bench import amsbench as ab

AMP_CIRCUITS = ["telescopic_ota", "folded_cascode_ota", "five_trans_ota", "current_mirror_ota",
                "azc", "dfcfc", "fdgb", "iac", "nmcnr", "smc"]


def _one(args):
    name, point, corner, temp = args
    cfg = ab.load(name)
    r = ab.simulate(cfg, point, corner, temp)
    passed, _ = ab.check(cfg, r.metrics)
    return name, point, r.ok, passed, r.metrics


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("circuits", nargs="*")
    ap.add_argument("-n", type=int, default=64)
    ap.add_argument("--corner", default="tt")
    ap.add_argument("--temp", type=float, default=27)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("-j", type=int, default=16)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    jobs = []
    for name in a.circuits or AMP_CIRCUITS:
        cfg = ab.load(name)
        for _ in range(a.n):
            jobs.append((name, {v: rng.choice(ab.grid(cfg, v)) for v in ab.variables(cfg)},
                         a.corner, a.temp))
    stats: dict[str, list] = {}
    with ProcessPoolExecutor(a.j) as ex:
        for name, point, ok, passed, m in ex.map(_one, jobs):
            stats.setdefault(name, []).append((ok, passed, m))
    for name, rows in stats.items():
        n_ok = sum(ok for ok, _, _ in rows)
        n_pass = sum(p for _, p, _ in rows)
        cfg = ab.load(name)
        space = 1
        for v in ab.variables(cfg):
            space *= len(ab.grid(cfg, v))
        print(f"{name:20s} grid={space:>9,d}  simulated_ok={n_ok:3d}/{len(rows)}  "
              f"spec_pass={n_pass:3d}/{len(rows)}   spec: {cfg['user_specs_metric']}")


if __name__ == "__main__":
    main()
