"""Acceptance tests for WP-07 Rocket Design Search (NSGA-II).

Validates multi-objective launch vehicle sizing and Pareto frontier discovery:
- Objectives: Maximize payload capacity, minimize liftoff wet mass.
- Constraints: Delta-V requirement and stage acceleration limits.
- Simulation verification: Finalists achieve circular parking orbit (perigee >= 160 km, e < 0.015).
- Contract compliance: All exported rocket definitions validate against contracts/rocket.schema.json.
- Plot generation and determinism.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from ascent.design import (
    PROFILE_SPECS,
    DesignFinalist,
    RocketDesignProblem,
    convert_vector_to_rocket,
    search_rocket_designs,
    tune_pitch_program,
)
from contracts.validate import validate


def test_problem_initialization():
    """Verify problem formulation dimensions, objectives, and constraints."""
    for prof in ["cargo_iss", "crew_iss", "geo_comm"]:
        prob = RocketDesignProblem(prof)
        assert prob.n_var == 7
        assert prob.n_obj == 2
        assert prob.n_ieq_constr == 3
        assert len(prob.xl) == 7
        assert len(prob.xu) == 7
        assert np.all(prob.xl < prob.xu)


def test_problem_evaluation_physics():
    """Verify objective and constraint computation conforms to rocket equation and physics."""
    prob = RocketDesignProblem("cargo_iss")
    specs = PROFILE_SPECS["cargo_iss"]

    # Midpoint test vector
    x_mid = 0.5 * (prob.xl + prob.xu)
    out: dict[str, Any] = {}
    prob._evaluate(x_mid, out)

    f1, f2 = out["F"]
    g1, g2, g3 = out["G"]

    # f1 is -payload, f2 is liftoff mass
    payload = x_mid[0]
    assert f1 == -payload
    assert f2 > payload

    # delta-v requirement constraint
    assert isinstance(g1, float)
    assert isinstance(g2, float)
    assert isinstance(g3, float)


def test_convert_vector_to_rocket():
    """Verify conversion of decision vector into contract-valid Rocket archetype."""
    prob = RocketDesignProblem("cargo_iss")
    x = prob.xl.copy()
    x[0] = 8000.0  # 8 tonnes payload

    rocket = convert_vector_to_rocket(x, "cargo_iss", "test_search_rocket", "Test Search Rocket")
    assert rocket.id == "test_search_rocket"
    assert rocket.payload_kg == 8000.0
    assert len(rocket.stages) == 2
    assert rocket.stages[0].dry_kg > 0
    assert rocket.stages[0].prop_kg > 0
    assert rocket.stages[1].dry_kg > 0
    assert rocket.stages[1].prop_kg > 0

    # Contract schema validation
    rocket_dict = rocket.to_dict()
    assert validate("rocket", rocket_dict) == rocket_dict


def test_tune_pitch_program_inserts_into_orbit():
    """Verify automated pitch adaptation inserts a candidate vehicle into parking orbit."""
    prob = RocketDesignProblem("cargo_iss")
    x = 0.5 * (prob.xl + prob.xu)
    rocket = convert_vector_to_rocket(x, "cargo_iss", "test_tune_rocket", "Test Tune Rocket")

    pitch_params, sim_res = tune_pitch_program(
        rocket=rocket,
        profile="cargo_iss",
        site_lat_deg=28.5,
        azimuth_deg=44.9,
        target_incl_deg=51.6,
    )

    assert len(pitch_params) == 5
    assert sim_res.perigee_km >= 160.0
    assert sim_res.eccentricity < 0.015
    assert sim_res.insertion_speed_mps >= 7700.0
    assert 40.0 <= sim_res.t_max_q_s <= 120.0


def test_search_rocket_designs_execution_and_verification(tmp_path: Path):
    """Run a fast deterministic NSGA-II search and verify Pareto frontier and finalists."""
    out_json = tmp_path / "test_pareto.json"
    out_plot = tmp_path / "test_pareto.png"

    result = search_rocket_designs(
        profile="cargo_iss",
        pop_size=16,
        n_gen=10,
        seed=42,
        out_results_json=out_json,
        out_plot_png=out_plot,
        export_finalists=False,
    )

    assert result.profile == "cargo_iss"
    assert result.seed == 42
    assert result.n_pareto_designs >= 3
    assert len(result.pareto_payloads_kg) == result.n_pareto_designs
    assert len(result.pareto_masses_kg) == result.n_pareto_designs

    # Verify payloads are monotonic
    assert list(result.pareto_payloads_kg) == sorted(result.pareto_payloads_kg)

    # Verify finalists
    assert len(result.finalists) == 3
    labels = [f.label for f in result.finalists]
    assert labels == ["Lightweight", "Balanced", "Heavyweight"]

    for f in result.finalists:
        assert isinstance(f, DesignFinalist)
        assert f.verified is True
        assert f.sim_perigee_km >= 160.0
        assert f.sim_eccentricity < 0.015
        assert f.sim_speed_mps >= 7700.0
        assert f.payload_fraction_pct > 2.0

    # Verify JSON summary export
    assert out_json.exists()
    summary = json.loads(out_json.read_text(encoding="utf-8"))
    assert summary["profile"] == "cargo_iss"
    assert len(summary["finalists"]) == 3

    # Verify Plot export
    assert out_plot.exists()
    assert out_plot.stat().st_size > 1000


def test_search_determinism():
    """Verify search is deterministic given the same seed."""
    res1 = search_rocket_designs("crew_iss", pop_size=12, n_gen=6, seed=99, export_finalists=False)
    res2 = search_rocket_designs("crew_iss", pop_size=12, n_gen=6, seed=99, export_finalists=False)

    assert res1.pareto_payloads_kg == res2.pareto_payloads_kg
    assert res1.pareto_masses_kg == res2.pareto_masses_kg
    assert len(res1.finalists) == len(res2.finalists)


def test_exported_results_exist_and_validate():
    """Verify all real sprint results exist and validate against contracts."""
    profiles = ["cargo_iss", "crew_iss", "geo_comm"]
    labels = ["lightweight", "balanced", "heavyweight"]

    for prof in profiles:
        # Pareto summary JSON
        summary_p = Path("data/results/pareto") / f"pareto_designs_{prof}.json"
        assert summary_p.exists(), f"Missing Pareto summary {summary_p}"

        # Pareto plot PNG
        plot_p = Path("data/results") / f"pareto_front_{prof}.png"
        assert plot_p.exists(), f"Missing Pareto plot {plot_p}"
        assert plot_p.stat().st_size > 5000

        # Exported contract-valid rockets
        for lbl in labels:
            r_path = Path("data/results") / f"rocket_search_{prof}_{lbl}.json"
            assert r_path.exists(), f"Missing exported rocket {r_path}"
            data = json.loads(r_path.read_text(encoding="utf-8"))
            validate("rocket", data)
