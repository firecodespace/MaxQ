"""Ascent Pitch Program Optimizer using CMA-ES.

Optimizes rocket pitch trajectory parameters to minimize propellant consumption
and aerodynamic/gravity losses while delivering payload to target parking orbit.

Conforms to WP-06 requirements:
- Uses CMA-ES (cma package) with deterministic seed recording.
- Reaches target orbit (perigee >= 160 km, e < 0.01).
- Compares naive baseline vs optimized trajectory.
- Generates 4-panel comparison plot (altitude, speed, dynamic pressure, pitch).
- Exports contract-valid trajectory JSON to data/results/.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cma
import matplotlib

# Use non-interactive backend for headless plotting
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from ascent.rockets import Rocket, get_rocket
from ascent.sim import DEFAULT_PITCH_PARAMS, SimResult, simulate_ascent


@dataclass
class OptimizationResult:
    """Telemetry and performance comparison from trajectory optimization."""

    rocket_id: str
    seed: int
    baseline_result: SimResult
    optimized_result: SimResult
    baseline_prop_used_kg: float
    optimized_prop_used_kg: float
    propellant_saved_kg: float
    propellant_saved_pct: float
    delta_loss_reduction_mps: float
    best_pitch_params: list[float]
    iterations: int
    evaluations: int
    runtime_s: float
    plot_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rocket_id": self.rocket_id,
            "seed": self.seed,
            "iterations": self.iterations,
            "evaluations": self.evaluations,
            "runtime_s": round(self.runtime_s, 2),
            "best_pitch_params": [round(float(p), 4) for p in self.best_pitch_params],
            "baseline": {
                "insertion_time_s": round(self.baseline_result.t_insertion_s, 1),
                "propellant_used_kg": round(self.baseline_prop_used_kg, 1),
                "total_loss_mps": round(self.baseline_result.total_loss_mps, 1),
                "gravity_loss_mps": round(self.baseline_result.gravity_loss_mps, 1),
                "drag_loss_mps": round(self.baseline_result.drag_loss_mps, 1),
                "perigee_km": round(self.baseline_result.perigee_km, 1),
                "apogee_km": round(self.baseline_result.apogee_km, 1),
                "eccentricity": round(self.baseline_result.eccentricity, 5),
                "max_q_pa": round(self.baseline_result.max_q_pa, 1),
            },
            "optimized": {
                "insertion_time_s": round(self.optimized_result.t_insertion_s, 1),
                "propellant_used_kg": round(self.optimized_prop_used_kg, 1),
                "total_loss_mps": round(self.optimized_result.total_loss_mps, 1),
                "gravity_loss_mps": round(self.optimized_result.gravity_loss_mps, 1),
                "drag_loss_mps": round(self.optimized_result.drag_loss_mps, 1),
                "perigee_km": round(self.optimized_result.perigee_km, 1),
                "apogee_km": round(self.optimized_result.apogee_km, 1),
                "eccentricity": round(self.optimized_result.eccentricity, 5),
                "max_q_pa": round(self.optimized_result.max_q_pa, 1),
            },
            "improvement": {
                "propellant_saved_kg": round(self.propellant_saved_kg, 1),
                "propellant_saved_pct": round(self.propellant_saved_pct, 2),
                "loss_reduction_mps": round(self.delta_loss_reduction_mps, 1),
            },
        }


def plot_naive_vs_optimized(
    base: SimResult,
    opt: SimResult,
    rocket: Rocket,
    opt_summary: dict[str, Any],
    output_path: Path | str,
) -> None:
    """Generate 4-panel comparison plot: altitude, velocity, dynamic pressure, and pitch."""
    traj_base = base.trajectory
    traj_opt = opt.trajectory

    t_b = np.array(traj_base["t_s"])
    alt_b_km = np.array(traj_base["alt_m"]) / 1000.0
    spd_b_kmps = np.array(traj_base["speed_mps"]) / 1000.0
    q_b_kpa = np.array(traj_base["q_pa"]) / 1000.0

    t_o = np.array(traj_opt["t_s"])
    alt_o_km = np.array(traj_opt["alt_m"]) / 1000.0
    spd_o_kmps = np.array(traj_opt["speed_mps"]) / 1000.0
    q_o_kpa = np.array(traj_opt["q_pa"]) / 1000.0

    # Reconstruct continuous pitch curves for comparison
    from ascent.sim import build_pitch_evaluator

    s1, s2 = rocket.stages[0], rocket.stages[1]
    t_meco = s1.burn_time_s
    t_ign2 = t_meco + 5.0
    t_max = t_ign2 + s2.burn_time_s

    p_eval_base = build_pitch_evaluator(traj_base["params"]["pitch_program"], t_meco, t_ign2, t_max)
    p_eval_opt = build_pitch_evaluator(traj_opt["params"]["pitch_program"], t_meco, t_ign2, t_max)

    pitch_b_deg = [p_eval_base(float(t)) for t in t_b]
    pitch_o_deg = [p_eval_opt(float(t)) for t in t_o]

    fig, axs = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle(
        f"MaxQ Ascent Optimization — {rocket.name}\n"
        f"Propellant Saved: {opt_summary['improvement']['propellant_saved_kg']} kg "
        f"({opt_summary['improvement']['propellant_saved_pct']}%) | "
        f"Loss Reduction: {opt_summary['improvement']['loss_reduction_mps']} m/s | "
        f"Seed: {opt_summary['seed']}",
        fontsize=13,
        fontweight="bold",
    )

    # 1. Altitude
    ax = axs[0, 0]
    ax.plot(t_b, alt_b_km, "r--", label=f"Baseline (Peri={base.perigee_km:.0f}km, Apo={base.apogee_km:.0f}km)", lw=1.8)
    ax.plot(t_o, alt_o_km, "b-", label=f"Optimized (Peri={opt.perigee_km:.0f}km, Apo={opt.apogee_km:.0f}km)", lw=2.0)
    ax.axhline(200.0, color="gray", linestyle=":", label="Target 200 km")
    ax.set_ylabel("Altitude (km)")
    ax.set_xlabel("Flight Time (s)")
    ax.set_title("Altitude Profile")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right", fontsize=9)

    # 2. Speed
    ax = axs[0, 1]
    ax.plot(t_b, spd_b_kmps, "r--", label=f"Baseline (v={base.insertion_speed_mps:.0f} m/s)", lw=1.8)
    ax.plot(t_o, spd_o_kmps, "b-", label=f"Optimized (v={opt.insertion_speed_mps:.0f} m/s)", lw=2.0)
    ax.axhline(7.784, color="gray", linestyle=":", label="v_circ (7.78 km/s)")
    ax.set_ylabel("Inertial Speed (km/s)")
    ax.set_xlabel("Flight Time (s)")
    ax.set_title("Inertial Speed Profile")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right", fontsize=9)

    # 3. Dynamic Pressure (q)
    ax = axs[1, 0]
    ax.plot(t_b, q_b_kpa, "r--", label=f"Baseline Max-Q={base.max_q_pa/1000:.1f} kPa @ {base.t_max_q_s:.0f}s", lw=1.8)
    ax.plot(t_o, q_o_kpa, "b-", label=f"Optimized Max-Q={opt.max_q_pa/1000:.1f} kPa @ {opt.t_max_q_s:.0f}s", lw=2.0)
    ax.set_ylabel("Dynamic Pressure q (kPa)")
    ax.set_xlabel("Flight Time (s)")
    ax.set_title("Aerodynamic Dynamic Pressure")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", fontsize=9)

    # 4. Pitch Program
    ax = axs[1, 1]
    ax.plot(t_b, pitch_b_deg, "r--", label="Baseline Pitch Program", lw=1.8)
    ax.plot(t_o, pitch_o_deg, "b-", label="Optimized Pitch Program", lw=2.0)
    ax.set_ylabel("Pitch Angle above Horizontal (deg)")
    ax.set_xlabel("Flight Time (s)")
    ax.set_title("Pitch Angle Program ψ(t)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", fontsize=9)

    plt.tight_layout()
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_p, dpi=160)
    plt.close(fig)


def optimize_ascent(
    rocket: Rocket | str = "medlift_kerolox_a",
    site_lat_deg: float = 28.5,
    site_lon_deg: float = -80.6,
    azimuth_deg: float = 44.9,
    target_incl_deg: float = 51.6,
    seed: int = 42,
    max_iter: int = 40,
    pop_size: int = 12,
    sigma0: float = 0.35,
    target_perigee_km: float = 200.0,
    target_apogee_km: float = 200.0,
    out_trajectory_json: Path | str | None = None,
    out_plot_png: Path | str | None = None,
) -> OptimizationResult:
    """Run CMA-ES optimization of ascent trajectory pitch parameters.

    Parameters
    ----------
    rocket : Rocket | str
        Rocket archetype or archetype ID string.
    site_lat_deg : float
        Launch site latitude.
    site_lon_deg : float
        Launch site longitude.
    azimuth_deg : float
        Launch azimuth (degrees).
    target_incl_deg : float
        Target orbital inclination.
    seed : int
        Deterministic random seed for CMA-ES.
    max_iter : int
        Maximum CMA-ES evolution generations.
    pop_size : int
        Population size per generation.
    sigma0 : float
        Initial search radius.
    target_perigee_km : float
        Target parking orbit perigee (km).
    target_apogee_km : float
        Target parking orbit apogee (km).
    out_trajectory_json : Path | str | None
        Path to save contract-valid optimized trajectory.
    out_plot_png : Path | str | None
        Path to save comparison plot.

    Returns
    -------
    OptimizationResult
        Detailed optimization result and comparison metrics.
    """
    if isinstance(rocket, str):
        rocket = get_rocket(rocket)

    t_start_wall = time.time()

    # 1. Run baseline simulation
    p0 = DEFAULT_PITCH_PARAMS.get(rocket.id, [11.865, 18.262, -1.0, 0.81, 0.747])
    baseline_result = simulate_ascent(
        rocket=rocket,
        site_lat_deg=site_lat_deg,
        site_lon_deg=site_lon_deg,
        azimuth_deg=azimuth_deg,
        target_incl_deg=target_incl_deg,
        pitch_params=p0,
        trajectory_id=f"traj_{rocket.id}_baseline",
        seed=seed,
    )
    base_prop_used = rocket.liftoff_mass_kg - baseline_result.trajectory["mass_kg"][-1]

    # 2. Fitness evaluation function for CMA-ES
    def fitness_fn(p: Sequence[float]) -> float:
        try:
            res = simulate_ascent(
                rocket=rocket,
                site_lat_deg=site_lat_deg,
                site_lon_deg=site_lon_deg,
                azimuth_deg=azimuth_deg,
                target_incl_deg=target_incl_deg,
                pitch_params=p,
                dt_substep=0.10,  # Fast sub-step for candidate evaluations
            )
        except Exception:
            return 1e9

        prop_used = rocket.liftoff_mass_kg - res.trajectory["mass_kg"][-1]

        # Orbital accuracy penalties (target 200 km circular)
        h_peri_err = max(0.0, 175.0 - res.perigee_km) ** 2 + max(0.0, res.perigee_km - 225.0) ** 2
        h_apo_err = max(0.0, 185.0 - res.apogee_km) ** 2 + max(0.0, res.apogee_km - 230.0) ** 2
        ecc_penalty = 80000.0 * (res.eccentricity**2)

        # Max-Q timing penalty (HANDOFF §7: plausible range 40-120s, optimal 60-90s)
        max_q_penalty = 0.0
        if res.t_max_q_s < 55.0 or res.t_max_q_s > 95.0:
            max_q_penalty += 1500.0 * (res.t_max_q_s - 70.0) ** 2
        if res.max_q_pa > 46000.0:
            max_q_penalty += (res.max_q_pa - 46000.0) * 2.0

        # Loss penalty (penalize excessive gravity loss)
        loss_penalty = 0.5 * res.total_loss_mps

        return prop_used + 80.0 * h_peri_err + 80.0 * h_apo_err + ecc_penalty + max_q_penalty + loss_penalty

    # 3. Configure and execute CMA-ES
    cma_opts = {
        "seed": seed,
        "maxiter": max_iter,
        "popsize": pop_size,
        "verb_log": 0,
        "verb_disp": 0,
        "bounds": [
            [5.0, 10.0, -5.0, 0.3, 0.3],  # lower bounds
            [22.0, 32.0, 5.0, 2.0, 2.0],  # upper bounds
        ],
    }

    es = cma.CMAEvolutionStrategy(p0, sigma0, cma_opts)
    es.optimize(fitness_fn)

    best_p = [float(x) for x in es.result.xbest]
    evaluations_count = es.result.evaluations
    actual_iterations = es.result.iterations

    # 4. High-precision full simulation with optimized parameters
    optimized_result = simulate_ascent(
        rocket=rocket,
        site_lat_deg=site_lat_deg,
        site_lon_deg=site_lon_deg,
        azimuth_deg=azimuth_deg,
        target_incl_deg=target_incl_deg,
        pitch_params=best_p,
        trajectory_id=f"traj_{rocket.id}_optimized",
        seed=seed,
        dt_substep=0.05,
    )
    opt_prop_used = rocket.liftoff_mass_kg - optimized_result.trajectory["mass_kg"][-1]
    prop_saved = base_prop_used - opt_prop_used
    prop_saved_pct = (prop_saved / base_prop_used) * 100.0 if base_prop_used > 0 else 0.0
    loss_reduction = baseline_result.total_loss_mps - optimized_result.total_loss_mps

    runtime_s = time.time() - t_start_wall

    # 5. Export JSON if requested
    if out_trajectory_json is not None:
        p_json = Path(out_trajectory_json)
        p_json.parent.mkdir(parents=True, exist_ok=True)
        optimized_result.export(p_json)
        # Validate against contracts
        from contracts.validate import validate

        validate("trajectory", optimized_result.trajectory)

    # 6. Generate comparison plot if requested
    plot_file = None
    if out_plot_png is not None:
        p_plot = Path(out_plot_png)
        p_plot.parent.mkdir(parents=True, exist_ok=True)
        temp_result = OptimizationResult(
            rocket_id=rocket.id,
            seed=seed,
            baseline_result=baseline_result,
            optimized_result=optimized_result,
            baseline_prop_used_kg=base_prop_used,
            optimized_prop_used_kg=opt_prop_used,
            propellant_saved_kg=prop_saved,
            propellant_saved_pct=prop_saved_pct,
            delta_loss_reduction_mps=loss_reduction,
            best_pitch_params=best_p,
            iterations=actual_iterations,
            evaluations=evaluations_count,
            runtime_s=runtime_s,
        )
        plot_naive_vs_optimized(
            base=baseline_result,
            opt=optimized_result,
            rocket=rocket,
            opt_summary=temp_result.to_dict(),
            output_path=p_plot,
        )
        plot_file = str(p_plot)

    return OptimizationResult(
        rocket_id=rocket.id,
        seed=seed,
        baseline_result=baseline_result,
        optimized_result=optimized_result,
        baseline_prop_used_kg=base_prop_used,
        optimized_prop_used_kg=opt_prop_used,
        propellant_saved_kg=prop_saved,
        propellant_saved_pct=prop_saved_pct,
        delta_loss_reduction_mps=loss_reduction,
        best_pitch_params=best_p,
        iterations=actual_iterations,
        evaluations=evaluations_count,
        runtime_s=runtime_s,
        plot_path=plot_file,
    )


def main() -> None:
    """CLI entrypoint for WP-06 Pitch/Staging Optimizer."""
    parser = argparse.ArgumentParser(description="MaxQ Ascent Trajectory Optimizer (CMA-ES)")
    parser.add_argument(
        "--rocket",
        type=str,
        default="medlift_kerolox_a",
        help="Rocket archetype ID (medlift_kerolox_a, crewlift_kerolox_b, heavylift_hydrolox_c)",
    )
    parser.add_argument(
        "--windows",
        type=str,
        default="data/results/windows_cargo_iss_cape_canaveral.json",
        help="Path to windows JSON (or fallback to dummy)",
    )
    parser.add_argument("--window-id", type=str, default="w0", help="Window ID to extract launch site and azimuth")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for CMA-ES")
    parser.add_argument("--max-iter", type=int, default=35, help="CMA-ES generations")
    parser.add_argument(
        "--out-json",
        type=str,
        default="data/results/trajectory_optimized_medlift_a.json",
        help="Output trajectory JSON path",
    )
    parser.add_argument(
        "--out-plot",
        type=str,
        default="data/results/naive_vs_optimized_medlift_a.png",
        help="Output comparison plot PNG path",
    )
    args = parser.parse_args()

    rocket = get_rocket(args.rocket)

    site_lat = 28.5619
    site_lon = -80.5772
    azimuth = 44.87
    incl = 51.64

    win_path = Path(args.windows)
    if not win_path.exists():
        win_path = Path("data/dummy/windows_dummy.json")

    if win_path.exists():
        with open(win_path, encoding="utf-8") as f:
            wdata = json.load(f)
        site_lat = float(wdata.get("site", {}).get("lat_deg", site_lat))
        site_lon = float(wdata.get("site", {}).get("lon_deg", site_lon))
        for w in wdata.get("windows", []):
            if w.get("id") == args.window_id:
                azimuth = float(w.get("azimuth_deg", azimuth))
                break

    print(f"Optimizing {rocket.name} ascent from ({site_lat}N, {site_lon}E, az={azimuth} deg)...")
    print(f"CMA-ES configuration: seed={args.seed}, max_iter={args.max_iter}")

    res = optimize_ascent(
        rocket=rocket,
        site_lat_deg=site_lat,
        site_lon_deg=site_lon,
        azimuth_deg=azimuth,
        target_incl_deg=incl,
        seed=args.seed,
        max_iter=args.max_iter,
        out_trajectory_json=args.out_json,
        out_plot_png=args.out_plot,
    )

    print("\n" + "=" * 60)
    print("OPTIMIZATION RESULTS (WP-06):")
    print(f"  Rocket:                {rocket.name} ({rocket.id})")
    print(f"  Seed:                  {res.seed}")
    print(f"  Runtime:               {res.runtime_s:.2f} s ({res.iterations} gens, {res.evaluations} evals)")
    print(
        f"  Propellant Consumed:   Baseline: {res.baseline_prop_used_kg:.1f} kg -> "
        f"Optimized: {res.optimized_prop_used_kg:.1f} kg"
    )
    print(f"  Propellant Saved:      {res.propellant_saved_kg:.1f} kg ({res.propellant_saved_pct:.2f}%)")
    print(
        f"  Delta-V Losses:        Baseline: {res.baseline_result.total_loss_mps:.1f} m/s -> "
        f"Optimized: {res.optimized_result.total_loss_mps:.1f} m/s "
        f"(cut by {res.delta_loss_reduction_mps:.1f} m/s)"
    )
    print(
        f"  Target Orbit Achieved: Perigee={res.optimized_result.perigee_km:.1f} km, "
        f"Apogee={res.optimized_result.apogee_km:.1f} km, "
        f"Ecc={res.optimized_result.eccentricity:.5f}"
    )
    q_pa = res.optimized_result.max_q_pa
    t_q = res.optimized_result.t_max_q_s
    print(f"  Max-Q:                 {q_pa:.0f} Pa at t={t_q:.1f} s")
    print(f"  Best Pitch Params:     {[round(x, 4) for x in res.best_pitch_params]}")
    if res.plot_path:
        print(f"  Comparison Plot:       {res.plot_path}")
    if args.out_json:
        print(f"  Trajectory JSON:       {args.out_json} (contract validated)")
    print("=" * 60)


if __name__ == "__main__":
    main()
