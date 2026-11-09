"""Multi-objective rocket design search using NSGA-II (pymoo).

Discovers Pareto fronts of launch vehicle architectures for specific mission profiles.
Conforms to WP-07 requirements:
- Multi-objective: Maximize payload to target orbit, minimize liftoff wet mass.
- Discovers non-dominated Pareto front architectures using NSGA-II.
- Verifies finalist designs using the ascent simulation engine.
- Exports contract-valid rocket JSON definitions conforming to contracts/rocket.schema.json.
- Generates Pareto front visualization plots in data/results/.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.core.problem import ElementwiseProblem
from pymoo.optimize import minimize

from ascent.geo import G0
from ascent.rockets import Rocket, Stage
from ascent.sim import simulate_ascent


# Profile design parameters and physical constraints
PROFILE_SPECS: dict[str, dict[str, Any]] = {
    "cargo_iss": {
        "payload_bounds": (4000.0, 16000.0),
        "prop1_bounds": (180000.0, 550000.0),
        "prop2_bounds": (35000.0, 140000.0),
        "tw1_bounds": (1.25, 1.60),
        "tw2_bounds": (0.70, 1.15),
        "eps1_bounds": (0.052, 0.075),
        "eps2_bounds": (0.065, 0.095),
        "isp1_s": 292.0,  # Kerolox booster
        "isp2_s": 348.0,  # Kerolox upper stage
        "cd": 0.30,
        "diameter_m": 3.7,
        "dv_req_mps": 9200.0,
        "max_a_g": 5.0,
        "target_incl_deg": 51.6,
    },
    "crew_iss": {
        "payload_bounds": (7500.0, 13000.0),  # Crew capsule range
        "prop1_bounds": (200000.0, 500000.0),
        "prop2_bounds": (40000.0, 130000.0),
        "tw1_bounds": (1.28, 1.55),
        "tw2_bounds": (0.75, 1.10),
        "eps1_bounds": (0.055, 0.075),
        "eps2_bounds": (0.070, 0.095),
        "isp1_s": 295.0,
        "isp2_s": 348.0,
        "cd": 0.32,
        "diameter_m": 3.9,
        "dv_req_mps": 9200.0,
        "max_a_g": 4.2,  # Strict limit for crew safety
        "target_incl_deg": 51.6,
    },
    "geo_comm": {
        "payload_bounds": (10000.0, 28000.0),  # High payload to LEO for high-energy GTO transfer
        "prop1_bounds": (350000.0, 950000.0),
        "prop2_bounds": (60000.0, 220000.0),
        "tw1_bounds": (1.30, 1.65),
        "tw2_bounds": (0.65, 1.05),
        "eps1_bounds": (0.050, 0.070),
        "eps2_bounds": (0.075, 0.110),
        "isp1_s": 305.0,
        "isp2_s": 445.0,  # High-Isp Hydrolox upper stage
        "cd": 0.35,
        "diameter_m": 5.2,
        "dv_req_mps": 9800.0,
        "max_a_g": 5.0,
        "target_incl_deg": 28.5,
    },
}


class RocketDesignProblem(ElementwiseProblem):
    """Multi-objective launch vehicle sizing and staging optimization problem."""

    def __init__(self, profile: str = "cargo_iss") -> None:
        self.profile = profile
        self.specs = PROFILE_SPECS[profile]

        # Decision variables: [m_payload, m_prop1, m_prop2, tw1, tw2, eps1, eps2]
        xl = np.array([
            self.specs["payload_bounds"][0],
            self.specs["prop1_bounds"][0],
            self.specs["prop2_bounds"][0],
            self.specs["tw1_bounds"][0],
            self.specs["tw2_bounds"][0],
            self.specs["eps1_bounds"][0],
            self.specs["eps2_bounds"][0],
        ], dtype=np.float64)

        xu = np.array([
            self.specs["payload_bounds"][1],
            self.specs["prop1_bounds"][1],
            self.specs["prop2_bounds"][1],
            self.specs["tw1_bounds"][1],
            self.specs["tw2_bounds"][1],
            self.specs["eps1_bounds"][1],
            self.specs["eps2_bounds"][1],
        ], dtype=np.float64)

        super().__init__(n_var=7, n_obj=2, n_ieq_constr=3, xl=xl, xu=xu)

    def _evaluate(self, x: np.ndarray, out: dict[str, Any], *args: Any, **kwargs: Any) -> None:
        payload, prop1, prop2, tw1, tw2, eps1, eps2 = x
        dry1 = prop1 * eps1
        dry2 = prop2 * eps2

        m_liftoff = payload + (dry1 + prop1) + (dry2 + prop2)
        m_s2_ign = payload + (dry2 + prop2)

        isp1 = self.specs["isp1_s"]
        isp2 = self.specs["isp2_s"]

        # Stage 1 burnout
        m1_burnout = m_liftoff - prop1
        dv1 = isp1 * G0 * math.log(m_liftoff / m1_burnout)
        thrust1 = tw1 * m_liftoff * G0

        # Stage 2 burnout
        m2_burnout = m_s2_ign - prop2
        dv2 = isp2 * G0 * math.log(m_s2_ign / m2_burnout)
        thrust2 = tw2 * m_s2_ign * G0

        dv_total = dv1 + dv2

        # Maximum stage accelerations
        a1_max_g = thrust1 / (m1_burnout * G0)
        a2_max_g = thrust2 / (m2_burnout * G0)

        # Objectives:
        # f1: Maximize payload -> Minimize -payload
        # f2: Minimize liftoff wet mass
        f1 = -payload
        f2 = m_liftoff

        # Constraints (g <= 0):
        # 1. Total ideal delta-v >= required delta-v
        g1 = self.specs["dv_req_mps"] - dv_total
        # 2. Stage 1 max acceleration <= limit
        g2 = a1_max_g - self.specs["max_a_g"]
        # 3. Stage 2 max acceleration <= limit
        g3 = a2_max_g - self.specs["max_a_g"]

        out["F"] = [f1, f2]
        out["G"] = [g1, g2, g3]


@dataclass
class DesignFinalist:
    """A verified design selected from the Pareto frontier."""

    design_id: str
    name: str
    profile: str
    label: str  # e.g. "Lightweight", "Balanced", "Heavyweight"
    rocket: Rocket
    payload_kg: float
    liftoff_mass_kg: float
    payload_fraction_pct: float
    sim_perigee_km: float
    sim_apogee_km: float
    sim_eccentricity: float
    sim_speed_mps: float
    sim_losses_mps: float
    verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "design_id": self.design_id,
            "name": self.name,
            "profile": self.profile,
            "label": self.label,
            "payload_kg": round(self.payload_kg, 1),
            "liftoff_mass_kg": round(self.liftoff_mass_kg, 1),
            "payload_fraction_pct": round(self.payload_fraction_pct, 2),
            "simulation": {
                "perigee_km": round(self.sim_perigee_km, 1),
                "apogee_km": round(self.sim_apogee_km, 1),
                "eccentricity": round(self.sim_eccentricity, 5),
                "speed_mps": round(self.sim_speed_mps, 1),
                "losses_mps": round(self.sim_losses_mps, 1),
                "verified": self.verified,
            },
            "rocket": self.rocket.to_dict(),
        }


@dataclass
class SearchResult:
    """Summary of Pareto front search results."""

    profile: str
    seed: int
    n_pareto_designs: int
    pareto_payloads_kg: list[float]
    pareto_masses_kg: list[float]
    finalists: list[DesignFinalist]
    plot_path: str | None = None
    runtime_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "seed": self.seed,
            "runtime_s": round(self.runtime_s, 2),
            "n_pareto_designs": self.n_pareto_designs,
            "pareto_front": [
                {"payload_kg": round(p, 1), "liftoff_mass_kg": round(m, 1), "payload_fraction_pct": round(p / m * 100, 2)}
                for p, m in zip(self.pareto_payloads_kg, self.pareto_masses_kg)
            ],
            "finalists": [f.to_dict() for f in self.finalists],
        }


def convert_vector_to_rocket(
    x: np.ndarray,
    profile: str,
    design_id: str,
    name: str,
) -> Rocket:
    """Convert optimization decision vector into a Rocket archetype."""
    specs = PROFILE_SPECS[profile]
    payload, prop1, prop2, tw1, tw2, eps1, eps2 = x
    dry1 = prop1 * eps1
    dry2 = prop2 * eps2

    m_liftoff = payload + (dry1 + prop1) + (dry2 + prop2)
    m_s2_ign = payload + (dry2 + prop2)

    thrust1 = tw1 * m_liftoff * G0
    thrust2 = tw2 * m_s2_ign * G0

    radius = specs["diameter_m"] / 2.0
    area = math.pi * (radius**2)

    stage1 = Stage(
        dry_kg=round(float(dry1), 1),
        prop_kg=round(float(prop1), 1),
        thrust_n=round(float(thrust1), 0),
        isp_s=float(specs["isp1_s"]),
        cd=float(specs["cd"]),
        area_m2=round(float(area), 2),
    )

    stage2 = Stage(
        dry_kg=round(float(dry2), 1),
        prop_kg=round(float(prop2), 1),
        thrust_n=round(float(thrust2), 0),
        isp_s=float(specs["isp2_s"]),
        cd=float(specs["cd"]),
        area_m2=round(float(area), 2),
    )

    return Rocket(
        schema_version=1,
        id=design_id,
        name=name,
        profile=profile,
        payload_kg=round(float(payload), 1),
        stages=[stage1, stage2],
    )


def plot_pareto_front(
    payloads: list[float],
    masses: list[float],
    finalists: list[DesignFinalist],
    profile: str,
    output_path: Path | str,
) -> None:
    """Generate multi-panel Pareto front analysis plot."""
    pay_arr = np.array(payloads)
    mass_arr = np.array(masses)
    frac_arr = (pay_arr / mass_arr) * 100.0

    fig, axs = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(
        f"MaxQ Rocket Design Search (NSGA-II) — Profile: {profile.upper()}\n"
        f"Non-Dominated Pareto Frontier ({len(payloads)} designs)",
        fontsize=13,
        fontweight="bold",
    )

    # Panel 1: Liftoff Mass vs Payload
    ax = axs[0]
    ax.plot(pay_arr, mass_arr / 1000.0, "o-", color="#2b5c8f", markersize=6, lw=1.8, label="Pareto Front", alpha=0.85)

    colors = ["#2ca02c", "#ff7f0e", "#d62728"]
    for f, c in zip(finalists, colors):
        ax.scatter(
            [f.payload_kg],
            [f.liftoff_mass_kg / 1000.0],
            color=c,
            s=120,
            zorder=5,
            edgecolor="black",
            label=f"{f.label}: {f.payload_kg:.0f} kg ({f.liftoff_mass_kg/1000:.0f}t GLOW)",
        )

    ax.set_xlabel("Payload to Orbit (kg)", fontsize=11)
    ax.set_ylabel("Liftoff Mass / GLOW (tonnes)", fontsize=11)
    ax.set_title("Vehicle Mass vs. Payload Capacity", fontsize=12)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", fontsize=9)

    # Panel 2: Payload Fraction vs Payload
    ax = axs[1]
    ax.plot(pay_arr, frac_arr, "s-", color="#17becf", markersize=5, lw=1.8, label="Payload Fraction (%)")
    for f, c in zip(finalists, colors):
        ax.scatter([f.payload_kg], [f.payload_fraction_pct], color=c, s=120, zorder=5, edgecolor="black")

    ax.set_xlabel("Payload to Orbit (kg)", fontsize=11)
    ax.set_ylabel("Payload Mass Fraction (%)", fontsize=11)
    ax.set_title("Payload Mass Fraction Frontier", fontsize=12)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right", fontsize=9)

    plt.tight_layout()
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_p, dpi=160)
    plt.close(fig)


def search_rocket_designs(
    profile: str = "cargo_iss",
    pop_size: int = 35,
    n_gen: int = 35,
    seed: int = 42,
    out_results_json: Path | str | None = None,
    out_plot_png: Path | str | None = None,
    export_finalists: bool = True,
) -> SearchResult:
    """Execute multi-objective NSGA-II search for rocket architectures.

    Parameters
    ----------
    profile : str
        Mission profile: "cargo_iss", "crew_iss", or "geo_comm".
    pop_size : int
        Population size per generation.
    n_gen : int
        Number of evolution generations.
    seed : int
        Deterministic random seed.
    out_results_json : Path | str | None
        Path to save summary JSON.
    out_plot_png : Path | str | None
        Path to save Pareto plot.
    export_finalists : bool
        If True, exports individual contract-valid rocket JSON files for finalists.

    Returns
    -------
    SearchResult
        Pareto front designs and verified finalists.
    """
    if profile not in PROFILE_SPECS:
        raise ValueError(f"Unknown profile: {profile}. Valid: {list(PROFILE_SPECS.keys())}")

    t0 = time.time()
    prob = RocketDesignProblem(profile=profile)
    alg = NSGA2(pop_size=pop_size)

    res = minimize(prob, alg, ("n_gen", n_gen), seed=seed, verbose=False)
    runtime_s = time.time() - t0

    # Extract non-dominated solutions
    f_vals = res.F
    x_vals = res.X

    if f_vals is None or len(f_vals) == 0:
        raise RuntimeError("NSGA-II found no feasible non-dominated designs.")

    # Sort strictly by increasing payload (decreasing f1)
    payloads = [-float(f[0]) for f in f_vals]
    masses = [float(f[1]) for f in f_vals]
    sorted_order = np.argsort(payloads)

    sorted_payloads = [payloads[i] for i in sorted_order]
    sorted_masses = [masses[i] for i in sorted_order]
    sorted_x = [x_vals[i] for i in sorted_order]

    # Select 3 distinct finalists: Lightweight, Balanced, Heavyweight
    n_pts = len(sorted_x)
    indices = [
        0,  # Min mass / lightweight
        n_pts // 2,  # Balanced / median
        n_pts - 1,  # Max payload / heavyweight
    ]
    labels = ["Lightweight", "Balanced", "Heavyweight"]

    finalists: list[DesignFinalist] = []
    for rank, (idx, label) in enumerate(zip(indices, labels)):
        x_final = sorted_x[idx]
        slug = f"search_{profile}_{label.lower()}"
        name = f"{label} Design ({profile.upper()} Archetype)"

        rocket = convert_vector_to_rocket(x_final, profile, slug, name)

        # Verify finalist in the 2D point mass ascent simulator
        sim_res = simulate_ascent(
            rocket=rocket,
            site_lat_deg=28.5,
            azimuth_deg=44.9 if "iss" in profile else 90.0,
            target_incl_deg=PROFILE_SPECS[profile]["target_incl_deg"],
            trajectory_id=f"traj_{slug}_verify",
        )

        verified = (
            sim_res.perigee_km >= 160.0
            and sim_res.eccentricity < 0.015
            and sim_res.insertion_speed_mps >= 7700.0
        )

        finalist = DesignFinalist(
            design_id=slug,
            name=name,
            profile=profile,
            label=label,
            rocket=rocket,
            payload_kg=rocket.payload_kg,
            liftoff_mass_kg=rocket.liftoff_mass_kg,
            payload_fraction_pct=(rocket.payload_kg / rocket.liftoff_mass_kg) * 100.0,
            sim_perigee_km=sim_res.perigee_km,
            sim_apogee_km=sim_res.apogee_km,
            sim_eccentricity=sim_res.eccentricity,
            sim_speed_mps=sim_res.insertion_speed_mps,
            sim_losses_mps=sim_res.total_loss_mps,
            verified=verified,
        )
        finalists.append(finalist)

        # Export contract-valid rocket JSON
        if export_finalists:
            r_path = Path("data/results") / f"rocket_{slug}.json"
            r_path.parent.mkdir(parents=True, exist_ok=True)
            rocket.to_json(r_path)
            # Validate against contracts
            from contracts.validate import validate

            validate("rocket", rocket.to_dict())

    # Generate plot if requested
    plot_file = None
    if out_plot_png is not None:
        plot_p = Path(out_plot_png)
        plot_pareto_front(sorted_payloads, sorted_masses, finalists, profile, plot_p)
        plot_file = str(plot_p)

    search_result = SearchResult(
        profile=profile,
        seed=seed,
        n_pareto_designs=len(sorted_payloads),
        pareto_payloads_kg=sorted_payloads,
        pareto_masses_kg=sorted_masses,
        finalists=finalists,
        plot_path=plot_file,
        runtime_s=runtime_s,
    )

    if out_results_json is not None:
        j_path = Path(out_results_json)
        j_path.parent.mkdir(parents=True, exist_ok=True)
        with open(j_path, "w", encoding="utf-8") as f:
            json.dump(search_result.to_dict(), f, indent=2)

    return search_result


def main() -> None:
    """CLI Entrypoint for WP-07 Rocket Design Search."""
    parser = argparse.ArgumentParser(description="MaxQ Rocket Design Search (NSGA-II)")
    parser.add_argument(
        "--profile",
        type=str,
        default="cargo_iss",
        choices=["cargo_iss", "crew_iss", "geo_comm"],
        help="Mission profile for design search",
    )
    parser.add_argument("--pop-size", type=int, default=35, help="Population size")
    parser.add_argument("--n-gen", type=int, default=35, help="Generations")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--out-json",
        type=str,
        default=None,
        help="Output summary JSON path (default: data/results/pareto_designs_<profile>.json)",
    )
    parser.add_argument(
        "--out-plot",
        type=str,
        default=None,
        help="Output plot PNG path (default: data/results/pareto_front_<profile>.png)",
    )
    args = parser.parse_args()

    out_json = args.out_json or f"data/results/pareto_designs_{args.profile}.json"
    out_plot = args.out_plot or f"data/results/pareto_front_{args.profile}.png"

    print(f"Executing NSGA-II rocket design search for profile '{args.profile}'...")
    res = search_rocket_designs(
        profile=args.profile,
        pop_size=args.pop_size,
        n_gen=args.n_gen,
        seed=args.seed,
        out_results_json=out_json,
        out_plot_png=out_plot,
        export_finalists=True,
    )

    print("\n" + "=" * 65)
    print(f"PARETO DESIGN SEARCH RESULTS ({args.profile.upper()}):")
    print(f"  Non-dominated Designs: {res.n_pareto_designs}")
    print(f"  Search Runtime:        {res.runtime_s:.2f} s")
    print(f"  Summary JSON:          {out_json}")
    print(f"  Pareto Plot:           {out_plot}")
    print("\nVERIFIED FINALIST ARCHITECTURES:")
    for f in res.finalists:
        ver_status = "VERIFIED (Reaches Orbit)" if f.verified else "UNVERIFIED"
        print(
            f"  * [{f.label}] {f.name}\n"
            f"      Payload: {f.payload_kg:.0f} kg | GLOW: {f.liftoff_mass_kg:.0f} kg ({f.payload_fraction_pct:.2f}% payload fraction)\n"
            f"      Sim Orbit: Perigee={f.sim_perigee_km:.1f} km, Apogee={f.sim_apogee_km:.1f} km, Ecc={f.sim_eccentricity:.5f}\n"
            f"      Insertion Speed: {f.sim_speed_mps:.1f} m/s | Status: {ver_status}"
        )
    print("=" * 65)


if __name__ == "__main__":
    main()

