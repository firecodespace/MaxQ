"""Can a mission be feasible at all today? Reproduces the WP2-06 integration findings.

    python scripts/probe_feasibility.py             # the three cases in the report
    python scripts/probe_feasibility.py --profile crew_iss --payload 20000

Read-only: it runs MissionEnv and the verifier and prints what they say. It writes nothing, so it is
safe to run against the committed data at any time.

Why it exists: wiring the API to the optimizer (WP2-06) produced four infeasible attempts in a row,
and "the optimizer is not converging" and "no feasible plan exists" look identical from outside. This
searches the pitch program directly, over WIDER bounds than optimize/cma_optimizer.py uses, and
reports which constraints are still blocking. That distinguishes the two.

Owner: Yash (lane: scripts/, server/). Findings recorded in status/shiva.md and docs/decisions.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cma

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from env import MissionEnv  # noqa: E402

# optimize/cma_optimizer.py clips the pitch program to p_meco [5, 40] and p_final [-5, 5]. Those are
# ascent/optimize.py's archetype bounds; WP2-01 found real vehicles need far more room, so the probe
# searches wide on purpose. If a plan is feasible here but not there, the bounds are the problem.
LOWER = [4.0, 0.0, -25.0, 0.3, 0.3]
UPPER = [30.0, 45.0, 25.0, 1.8, 1.8]
X0 = [10.0, 12.0, -2.0, 0.9, 0.9]


def request(profile: str, payload_kg: float, rocket: str, pad: str) -> dict:
    crew = profile == "crew_iss"
    return {
        "schema_version": 1,
        "id": f"msn_probe_{profile}",
        "profile": profile,
        "payload": {"mass_kg": float(payload_kg), "crew": 4 if crew else 0},
        "rocket_id": rocket,
        "pad_id": pad,
        "target": {"norad_id": 25544},
        "window_id": "w0",
        "constraints_profile": "crew_default" if crew else "cargo_default",
        "objective": {"w_fuel": 1.0, "w_time": 0.5, "fuel_ref_kg": 500000.0, "time_ref_s": 86400.0},
        "budget": {"max_attempts": 1, "max_wall_s": 60.0},
        "seed": 7,
    }


def probe(profile: str, payload_kg: float, rocket: str, pad: str, iters: int) -> dict:
    req = request(profile, payload_kg, rocket, pad)
    env = MissionEnv(req)
    accel_limit = 4.0 if profile == "crew_iss" else 6.0

    def fly(p):
        att, _ = env.evaluate_plan(
            {
                "launch_t_unix": 1794372400,
                "pitch_program": [float(x) for x in p],
                "parking_alt_km": 200.0,
                "burns": [],
                "waypoints": [],
            },
            attempt_id="att_probe",
        )
        return att

    def score(p):
        m = fly(p)["metrics"]
        return (
            max(0.0, 160.0 - m["perigee_km"])
            + 60.0 * max(0.0, m["max_accel_g"] - accel_limit)
            + 3000.0 * max(0.0, m["eccentricity"] - 0.05)
        )

    es = cma.CMAEvolutionStrategy(X0, 0.3, {
        "seed": 7, "maxiter": iters, "popsize": 14, "verbose": -9,
        "bounds": [LOWER, UPPER], "CMA_stds": [5.0, 10.0, 8.0, 0.3, 0.3],
    })
    best = (float("inf"), X0)
    while not es.stop():
        xs = es.ask()
        fs = [score(x) for x in xs]
        es.tell(xs, fs)
        i = min(range(len(fs)), key=fs.__getitem__)
        if fs[i] < best[0]:
            best = (fs[i], [float(v) for v in xs[i]])
        if best[0] == 0.0:
            break

    att = fly(best[1])
    m = att["metrics"]
    blocking = [c["id"] for c in att["constraint_report"] if not c["pass"] and c["value"] is not None]
    print(
        f"{profile:10s} {rocket:16s} payload {payload_kg:>7,.0f} kg  "
        f"perigee {m['perigee_km']:7.1f} km  ecc {m['eccentricity']:.4f}  "
        f"accel {m['max_accel_g']:.2f} g (limit {accel_limit:.1f})  "
        f"feasible={str(att['feasible']):5s} cost={att['cost']:.3f}  blocking={blocking}"
    )
    return {"attempt": att, "pitch": best[1], "blocking": blocking}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", default=None)
    ap.add_argument("--payload", type=float, default=None)
    ap.add_argument("--rocket", default="falcon9_block5")
    ap.add_argument("--pad", default="ccsfs_slc40")
    ap.add_argument("--iters", type=int, default=45)
    args = ap.parse_args(argv)

    if args.profile:
        probe(args.profile, args.payload or 12000.0, args.rocket, args.pad, args.iters)
        return 0

    print("Searching the pitch program over WIDE bounds and reporting what still blocks.\n")
    for payload in (12000.0, 16000.0, 20000.0):
        probe("crew_iss", payload, args.rocket, args.pad, args.iters)
    probe("cargo_iss", 12000.0, args.rocket, args.pad, args.iters)
    print(
        "\nReading: orbit insertion (C01) is reachable once the pitch bounds are wide enough. "
        "The crew acceleration limit (C03) is not reachable at any payload, because the simulator "
        "burns each stage to depletion with no throttling, so peak g is set by the vehicle rather "
        "than by the flight plan."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
