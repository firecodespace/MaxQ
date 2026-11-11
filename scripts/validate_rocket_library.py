"""Measure each library vehicle's simulated payload-to-LEO against the published figure (WP2-01).

    python scripts/validate_rocket_library.py [--only falcon9_block5] [--quick]

For every `kind: real` vehicle in data/rockets this finds the heaviest payload the ascent simulator
can put into the reference orbit, compares it with `payload_to_leo_kg_published`, and writes the
result into the file's `sim_validation` block. The numbers are reported as measured: a vehicle that
misses its tolerance is recorded as `pass: false`, not tuned until it passes.

Method
------
* Reference orbit: the simulator's own cut-off (semi-major axis reaches 200 km circular) with
  perigee >= 160 km (constraint C01), launched due east from the vehicle's first supported pad.
* A payload counts as deliverable if SOME pitch program reaches that orbit. The pitch program is
  searched with CMA-ES (seeded, so the result is reproducible) over the same five parameters and
  bounds as ascent/optimize.py.
* The largest deliverable payload is found by bisection between 0 and 2.2x the published figure.

Why the match cannot be close: the simulator is a 2D point mass with one thrust and one Isp per
stage, an exponential atmosphere and no fairing jettison. (Throttling and a stage-2 coast DO exist
now, but this measurement flies the default unthrottled single-burn path, so they do not help it.)

Tolerance
---------
`DEFAULT_TOLERANCE_PCT` is a judgement about that model, not a measurement: with the omissions
above, a payload figure within about a third of the published one is as close as a 2D point mass
can be expected to come. A vehicle may state its own `sim_tolerance_pct`, and if it does, its
`notes` must say why -- `test_a_custom_tolerance_is_justified` enforces that, so a tolerance can
never be quietly widened around a vehicle that misses.

A tolerance is NOT a quality claim. Three vehicles passing at +/-35 % does not make the library
accurate enough to benchmark against; it means the model's error is recorded. What protects a
benchmark is `sim_version` on every block (a sim edit invalidates the number and the suite says so)
plus the measured error travelling with any result that uses the vehicle -- not a tighter number
here. Tightening this constant would drop vehicles, not improve the model.

Owner: Yash (lane: scripts/, data/rockets).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import cma

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ascent.geo import G0  # noqa: E402
from ascent.rockets import Rocket, Stage  # noqa: E402
from ascent.sim import simulate_ascent  # noqa: E402
from contracts.validate import validate  # noqa: E402

ROCKETS = REPO_ROOT / "data" / "rockets"
PADS = REPO_ROOT / "data" / "pads"
SIM_FILES = ("ascent/sim.py", "ascent/atmosphere.py", "ascent/geo.py", "ascent/rockets.py")
# The stated bar, the same for every vehicle. It is derived from what this model leaves out, not
# fitted to the results: no fairing mass (Falcon 9's is around 1.9 t against a 22.8 t payload, about
# 8 %), no propellant reserve or margin (another 2-5 %), and a reference orbit -- 200 km circular,
# due east from the pad -- that is the easiest LEO available and takes the full Earth-rotation boost,
# where the published figures quote their own and generally harder orbits (10-20 %). Those add to
# roughly a third, in the direction of the simulator OVER-delivering, which is what is measured.
#
# Read it as a bar that catches a broken model, a vehicle that cannot reach orbit, or a stale
# record. It is not a claim that the simulator is accurate to 35 %: the honest number is the
# per-vehicle error_pct, and anything quoting a payload should show it.
#: Fidelity tolerance for a vehicle that does not state its own, in percent of the published
#: payload figure. See the "Tolerance" section of the module docstring for what justifies it.
DEFAULT_TOLERANCE_PCT = 35.0
MIN_PERIGEE_KM = 160.0
SEED = 7
# Pitch program [t_start_s, p_meco_deg, p_final_deg, alpha1, alpha2], in degrees above the local
# horizontal. The bounds are deliberately WIDER than ascent/optimize.py's, which were fitted around
# the three fictional archetypes. Real vehicles here carry far more delta-v than those archetypes
# (12.6-15.1 km/s ideal against about 11.3), and with the archetype bounds every one of them flew
# too steep: they cut off at 300-1,000 km altitude with the perigee still underground, having spent
# their margin climbing instead of accelerating. Letting the program pitch over to the horizon, and
# a little below it, is what a high-energy vehicle actually needs.
LOWER = [4.0, 0.0, -25.0, 0.3, 0.3]
UPPER = [30.0, 45.0, 25.0, 1.8, 1.8]
X0 = [10.0, 12.0, -2.0, 0.9, 0.9]


def sim_version() -> str:
    """A hash of the simulator sources: results are only comparable for the same value."""
    h = hashlib.sha256()
    for rel in SIM_FILES:
        h.update((REPO_ROOT / rel).read_bytes().replace(b"\r\n", b"\n"))
    return "ascent-" + h.hexdigest()[:12]


def to_rocket(vehicle: dict, payload_kg: float) -> Rocket:
    keys = ("dry_kg", "prop_kg", "thrust_n", "isp_s", "cd", "area_m2")
    stages = [Stage(**{k: float(s[k]) for k in keys}) for s in vehicle["stages"]]
    return Rocket(
        id=vehicle["id"],
        name=vehicle["name"],
        profile=vehicle.get("profile", "cargo_iss"),
        payload_kg=float(payload_kg),
        stages=stages,
    )


def fly(rocket: Rocket, lat: float, lon: float, pitch, dt: float):
    return simulate_ascent(
        rocket=rocket,
        site_lat_deg=lat,
        site_lon_deg=lon,
        azimuth_deg=90.0,
        target_incl_deg=lat,
        pitch_params=list(pitch),
        dt_substep=dt,
    )


def reaches_orbit(res) -> bool:
    return res.perigee_km >= MIN_PERIGEE_KM and res.eccentricity < 0.05


def shortfall(res) -> float:
    """How far a flight is from the reference orbit; 0 when it is there.

    The search needs a value that falls steadily as the flight improves, so the terms are scaled to
    be comparable rather than letting one swamp the rest. A raised perigee is the thing that
    actually matters, so it leads; eccentricity is a tie-breaker between flights that all fall short;
    and a wildly lofted apogee is penalised gently, because climbing to 1,000 km is the failure mode
    these vehicles fall into and nothing else in the score discourages it.
    """
    if reaches_orbit(res):
        return 0.0
    peri = res.perigee_km if math.isfinite(res.perigee_km) else -1e4
    apo = res.apogee_km if math.isfinite(res.apogee_km) else 1e5
    gap = MIN_PERIGEE_KM - min(peri, MIN_PERIGEE_KM)  # km of perigee still missing
    ecc = 300.0 * max(0.0, res.eccentricity - 0.05)  # only once the orbit is nearly closed
    loft = 0.15 * max(0.0, apo - 400.0)  # discourage trading speed for height
    return gap + ecc + loft


def best_pitch(rocket: Rocket, lat: float, lon: float, iters: int, pop: int, dt: float, x0=None):
    """Search the pitch program for one that reaches orbit. Returns (reached, pitch, result)."""

    def fitness(p):
        try:
            return shortfall(fly(rocket, lat, lon, p, dt))
        except Exception:
            return 1e6

    def run(start, sigma, n):
        es = cma.CMAEvolutionStrategy(
            list(start),
            sigma,
            {
                "seed": SEED,
                "maxiter": n,
                "popsize": pop,
                "verb_log": 0,
                "verb_disp": 0,
                "verbose": -9,
                "bounds": [LOWER, UPPER],
                "CMA_stds": [5.0, 10.0, 8.0, 0.3, 0.3],
            },
        )
        top = (float("inf"), None)
        while not es.stop():
            xs = es.ask()
            fs = [fitness(x) for x in xs]
            es.tell(xs, fs)
            i = min(range(len(fs)), key=fs.__getitem__)
            if fs[i] < top[0]:
                top = (fs[i], [float(z) for z in xs[i]])
            if top[0] == 0.0:
                break
        return top

    best = run(x0 or X0, 0.3, iters)
    # A local polish from the best point. The wide first pass finds the right basin but stops a few
    # kilometres of perigee short of the threshold, which is the difference between "this vehicle
    # cannot reach orbit" and "it can": worth a second, narrower search before concluding anything.
    if best[1] is not None and best[0] > 0.0:
        polished = run(best[1], 0.06, max(8, iters // 2))
        if polished[0] < best[0]:
            best = polished
    if best[1] is None:
        return False, None, None
    # Confirm at the simulator's normal step, not the coarse search step.
    res = fly(rocket, lat, lon, best[1], 0.05)
    return reaches_orbit(res), best[1], res


def search_step_s(vehicle: dict) -> float:
    """Integration step for CANDIDATE flights, scaled to how long the vehicle burns.

    A fixed step is the wrong thing here. Atlas V 401's Centaur has a thrust-to-weight of 0.42 and
    burns for 928 s, so at the step that suits Falcon 9 a single failed candidate costs about 31,000
    derivative evaluations, and a full search costs over half an hour. The step is therefore set
    from the vehicle's own total burn time, keeping the work per candidate roughly constant. Every
    flight that matters is re-flown at the simulator's normal 0.05 s step before it is believed, so
    this only affects how fast the search explores, never what is reported.
    """
    burn = sum(s["prop_kg"] / (s["thrust_n"] / (s["isp_s"] * G0)) for s in vehicle["stages"])
    return min(0.5, max(0.15, burn / 3000.0))


def max_payload(vehicle: dict, lat: float, lon: float, quick: bool) -> dict:
    """The heaviest payload that reaches the reference orbit, found by scanning, not bisecting.

    Bisection would be the obvious method and it is wrong here, because feasibility is NOT monotonic
    in payload. A lighter payload leaves the vehicle with more excess delta-v, and with this pitch
    program family the excess goes into climbing: Atlas V 401 fails at 980 kg, where it lofts itself
    to 780 km with the perigee underground, and succeeds at 4,900 kg. A bisection that starts low
    concludes the vehicle cannot reach orbit at all, which is simply false. So the payload axis is
    scanned on a grid, every success is recorded, and the largest is reported.
    """
    published = vehicle["payload_to_leo_kg_published"]
    dt = search_step_s(vehicle)
    iters, pop = (14, 10) if quick else (55, 14)
    if quick:
        dt *= 1.6
    grid = [0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 1.05, 1.2, 1.4, 1.6, 1.85, 2.1]
    if quick:
        grid = grid[::3]
    successes: list[tuple[float, list, object]] = []
    warm = None
    sims = 0
    for f in grid:
        mass = max(1.0, f * published)
        ok, pitch, res = best_pitch(to_rocket(vehicle, mass), lat, lon, iters, pop, dt, warm)
        sims += iters * pop * 2
        if ok:
            successes.append((mass, pitch, res))
            warm = pitch
    if not successes:
        return {"best": None, "evaluations_upper_bound": sims, "resolution_kg": None, "tried": len(grid)}

    best = max(successes, key=lambda t: t[0])
    # Refine upward from the best success towards the next grid point that failed.
    step = 0.25 * published
    hi = best[0] + step
    for _ in range(0 if quick else 4):
        ok, pitch, res = best_pitch(to_rocket(vehicle, hi), lat, lon, iters, pop, dt, best[1])
        sims += iters * pop * 2
        if ok:
            best = (hi, pitch, res)
            hi += step
        else:
            step *= 0.5
            hi = best[0] + step
    return {"best": best, "evaluations_upper_bound": sims, "resolution_kg": step, "tried": len(grid)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help="validate a single vehicle id")
    ap.add_argument("--quick", action="store_true", help="smaller search (for a smoke run; do not commit its numbers)")
    args = ap.parse_args(argv)

    version = sim_version()
    pads = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(PADS.glob("*.json"))}
    rc = 0
    for path in sorted(ROCKETS.glob("*.json")):
        v = json.loads(path.read_text(encoding="utf-8"))
        if v["kind"] != "real" or (args.only and v["id"] != args.only):
            continue
        pad = next((p for p in pads.values() if v["id"] in p["supported_rocket_ids"]), None)
        if pad is None or v["payload_to_leo_kg_published"] is None:
            print(f"{v['id']}: skipped (no pad or no published payload figure)")
            continue
        t0 = time.time()
        out = max_payload(v, pad["lat_deg"], pad["lon_deg"], args.quick)
        published = v["payload_to_leo_kg_published"]
        tolerance = float(v.get("sim_tolerance_pct") or DEFAULT_TOLERANCE_PCT)
        if out["best"] is None:
            block = {
                "payload_to_leo_kg_sim": None,
                "error_pct": None,
                "tolerance_pct": tolerance,
                "pass": False,
                "sim_version": version,
                "note": f"No pitch program reached a {MIN_PERIGEE_KM:.0f} km perigee at any of {out['tried']} "
                f"payloads scanned from {0.15 * published:.0f} to {2.1 * published:.0f} kg, due east "
                f"from {pad['id']}.",
            }
            print(f"{v['id']}: DID NOT REACH ORBIT at any tested payload  ({time.time() - t0:.0f} s)")
            rc = 1
        else:
            mass, pitch, res = out["best"]
            err = 100.0 * (mass - published) / published
            ok = abs(err) <= tolerance
            block = {
                "payload_to_leo_kg_sim": round(mass, 0),
                "error_pct": round(err, 1),
                "tolerance_pct": tolerance,
                "pass": ok,
                "sim_version": version,
                "note": f"Heaviest payload reaching perigee >= {MIN_PERIGEE_KM:.0f} km, due east from {pad['id']} "
                f"(resolved to +/- {out['resolution_kg']:.0f} kg; pitch program "
                f"{[round(x, 3) for x in pitch]} found by CMA-ES seed {SEED}). Orbit reached: "
                f"{res.perigee_km:.0f} x {res.apogee_km:.0f} km, max q {res.max_q_pa / 1000:.1f} kPa. "
                f"Published figure: {published:.0f} kg.",
            }
            print(
                f"{v['id']}: sim {mass:,.0f} kg vs published {published:,.0f} kg  ({err:+.1f} %)  "
                f"{'PASS' if ok else 'MISS'} at +/-{tolerance:.0f} %  ({time.time() - t0:.0f} s)"
            )
            if not ok:
                rc = 1
        if not args.quick:
            v["sim_validation"] = block
            validate("vehicle", v)
            path.write_text(json.dumps(v, indent=2) + "\n", encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
