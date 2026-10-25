"""Where could the AI have passed, and did it? (game/passoracle.py asks the physics.)

    python tools/pass_probe.py slow --circuit Monza --zone 0 --pace 0.55
    python tools/pass_probe.py slow --circuit Monza --seeds 8 --jobs 3
    python tools/pass_probe.py race --circuit Monza --seeds 2 --jobs 2

``slow``: a car crawls out of a corner (``--pace`` of its plan), the ego right
behind it and a few more cars behind the ego -- the scene of a car queueing
behind a slow one. The ego drives as the game has it (the deployed networks);
the scene runs ``--time`` seconds and says whether and when the ego got by, and
the oracle is asked every ``--every`` seconds while the ego is still behind.

``race``: whole twenty-car races; every ``--every`` seconds each car held up
behind another (passoracle.held_up) is probed, at most ``--max-probes`` per race.

Every probe: was a pass possible (some lane worked), did the ego's own driving
pass, and the *missed* ones -- possible, yet the car stayed behind. Output goes
to ``policy/raceai/probe_<name>.json`` and a summary on stdout.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

OUT = Path(__file__).resolve().parents[1] / "policy" / "raceai"
#: Held-up samples (one a second per car) in a two-lap race, about.
HELD_PER_RACE = 800.0


def _row(p, fld) -> dict:
    d = fld.cars[p.car].driver
    o = fld.views[p.other]
    own = float(d.plan.v[fld.frame.node(o.s)]) * d.pace_lap
    out = {k: v for k, v in vars(p).items() if k != "out"}
    out.update(ratio=round(o.v / max(own, 1.0), 3), kappa=round(float(d.plan.kappa[fld.frame.node(p.s)]), 4),
               possible=p.possible, missed=p.missed, best=p.best(),
               out={w: asdict(x) for w, x in p.out.items()})
    return out


def slow_scene(circuit: str, seed: int, zone: int | None, pace: float, gap: float):
    """raceenv's ``slow`` scene with what it would draw fixed: (field, ego, the
    crawling car, its brake zone)."""
    from game import raceenv
    opts = {"pace": pace, "gap": gap}
    if zone is not None:
        opts["zone"] = zone
    sc = raceenv.make_scenario(circuit, "slow", seed, opts=opts)
    return sc.fld, sc.egos[0], 0, sc.zone


def run_slow(args) -> dict:
    circuit, seed, zone, pace, gap, T, every, probe = args
    from game import passoracle as po
    fld, ego, lead, z = slow_scene(circuit, seed, zone, pace, gap)
    out = {"circuit": circuit, "seed": seed, "zone": z, "pace": pace, "gap": gap,
           "s_lead": round(fld.views[lead].s, 1), "probes": [], "trace": []}
    e, o = fld.cars[ego], fld.cars[lead]
    passed_t = None
    nxt = 0.0
    while fld.t < T:
        fld.step(po.DT)
        lead_m = po.lead_of(fld, ego, lead)
        if passed_t is None and lead_m > po.CLEAR:
            passed_t = round(fld.t, 2)
        if fld.ticks % 30 == 0:
            me, ov = fld.views[ego], fld.views[lead]
            out["trace"].append([round(fld.t, 1), round(me.v * 3.6), round(ov.v * 3.6),
                                 round(lead_m, 1), round(me.n - ov.n, 2),
                                 round(e.driver.target, 2)])
        if probe and passed_t is None and fld.t >= nxt:
            nxt = fld.t + every
            p = po.probe(fld, ego, lead)
            out["probes"].append(_row(p, fld))
    out.update(passed_t=passed_t, hits=e.hits, recoveries=e.driver.recoveries,
               lead_v_kmh=round(float(np.mean([r[2] for r in out["trace"]])), 1))
    return out


def run_race(args) -> dict:
    circuit, seed, laps, every, max_probes, t_max, horizon, from_lap = args
    from game import grandprix, passoracle as po, raceenv
    rng = np.random.default_rng(seed + 7)
    track = raceenv.track_of(circuit)
    fld = grandprix.build(track, 6, laps, seed, player=False)
    fld.lights_out()
    fld.views = fld._views()
    probes, held = [], 0
    nxt = 8.0                                 # the start's scramble is another matter
    t0 = time.perf_counter()
    while not fld.finished() and fld.t < t_max:
        fld.step(po.DT)
        if fld.t < nxt:
            continue
        nxt = fld.t + every
        pairs = [(i, j) for i in range(len(fld.cars))
                 if (j := po.held_up(fld, i)) is not None]
        held += len(pairs)
        # Spread over the race: each held-up car is probed with a chance that
        # makes about max_probes in a race of HELD_PER_RACE such samples.
        for i, j in pairs:
            if fld.cars[i].laps < from_lap:
                continue
            if len(probes) < max_probes and rng.random() < max_probes / HELD_PER_RACE:
                row = _row(po.probe(fld, i, j, horizon), fld)
                row["lap"] = fld.cars[i].laps
                probes.append(row)
    return {"circuit": circuit, "seed": seed, "laps": laps, "t": round(fld.t, 1),
            "held_samples": held, "every": every, "probes": probes,
            "overtakes": fld.overtakes, "wall_s": round(time.perf_counter() - t0)}


def summary(probes: list) -> dict:
    n = len(probes)
    if not n:
        return {"n": 0}
    f = lambda k: round(100.0 * sum(1 for p in probes if p[k]) / n, 1)
    stay = round(100.0 * sum(1 for p in probes if p["out"]["stay"]["passed"]) / n, 1)
    poss = [p for p in probes if p["possible"]]
    conv = (round(100.0 * sum(1 for p in poss if p["out"]["stay"]["passed"]) / len(poss), 1)
            if poss else None)
    return {"n": n, "possible_pct": f("possible"), "stay_passed_pct": stay,
            "missed_pct": f("missed"), "conversion_pct": conv}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("slow", "race"))
    ap.add_argument("--circuit", default="Monza")
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--seed0", type=int, default=0)
    ap.add_argument("--zone", type=int, default=0, help="slow: brake zone index (-1: random)")
    ap.add_argument("--pace", type=float, nargs="+", default=[0.55])
    ap.add_argument("--gap", type=float, default=22.0)
    ap.add_argument("--time", type=float, default=25.0)
    ap.add_argument("--every", type=float, default=1.0)
    ap.add_argument("--no-probe", action="store_true")
    ap.add_argument("--laps", type=int, default=2)
    ap.add_argument("--max-probes", type=int, default=60)
    ap.add_argument("--horizon", type=float, default=15.0, help="race: seconds each try runs")
    ap.add_argument("--from-lap", type=int, default=0, help="race: probe only from this lap on")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--name", default="")
    args = ap.parse_args()

    if args.mode == "slow":
        zone = None if args.zone < 0 else args.zone
        tasks = [(args.circuit, args.seed0 + s, zone, p, args.gap, args.time, args.every,
                  not args.no_probe) for p in args.pace for s in range(args.seeds)]
        fn = run_slow
    else:
        tasks = [(args.circuit, args.seed0 + s, args.laps, args.every, args.max_probes,
                  140.0 * (args.laps + 1), args.horizon, args.from_lap) for s in range(args.seeds)]
        fn = run_race
    t0 = time.perf_counter()
    if args.jobs > 1:
        with ProcessPoolExecutor(min(args.jobs, 3)) as ex:
            res = list(ex.map(fn, tasks))
    else:
        res = [fn(t) for t in tasks]
    probes = [p for r in res for p in r["probes"]]
    for r in res:
        if args.mode == "slow":
            print(f"{r['circuit']} seed {r['seed']} pace {r['pace']} lead ~{r['lead_v_kmh']} km/h: "
                  f"passed at {r['passed_t']} s, hits {r['hits']}, rec {r['recoveries']}, "
                  f"probes {summary(r['probes'])}")
        else:
            print(f"{r['circuit']} seed {r['seed']}: held samples {r['held_samples']}, "
                  f"overtakes {r['overtakes']}, wall {r['wall_s']} s, {summary(r['probes'])}")
    s = summary(probes)
    print("ALL", s, f"({time.perf_counter() - t0:.0f} s)")
    name = args.name or f"{args.mode}_{args.circuit}"
    path = OUT / f"probe_{name}.json"
    path.write_text(json.dumps({"args": vars(args), "summary": s, "runs": res}, indent=1))
    print("->", path)


if __name__ == "__main__":
    main()
