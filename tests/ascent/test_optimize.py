"""Tests for ascent trajectory optimization with CMA-ES (WP-06)."""

from pathlib import Path

import pytest

from ascent.optimize import optimize_ascent
from ascent.rockets import get_rocket
from contracts.validate import validate


@pytest.fixture(scope="module")
def opt_run(tmp_path_factory):
    tmp_dir = tmp_path_factory.mktemp("opt_test")
    out_json = tmp_dir / "trajectory_optimized_test.json"
    out_plot = tmp_dir / "naive_vs_optimized_test.png"

    rocket = get_rocket("medlift_kerolox_a")
    res = optimize_ascent(
        rocket=rocket,
        seed=42,
        max_iter=15,
        pop_size=8,
        out_trajectory_json=out_json,
        out_plot_png=out_plot,
    )
    return res, out_json, out_plot


def test_optimizer_improves_baseline(opt_run):
    """HANDOFF.md §6 WP-06: reaches the target orbit, beats the baseline."""
    res, _, _ = opt_run
    # Reaches valid parking orbit
    assert res.optimized_result.perigee_km >= 160.0, f"Perigee {res.optimized_result.perigee_km:.1f} km < 160 km"
    assert res.optimized_result.eccentricity < 0.01, f"Eccentricity {res.optimized_result.eccentricity:.5f} >= 0.01"
    # Propellant consumed is reduced
    assert res.propellant_saved_kg >= 0.0
    assert res.optimized_prop_used_kg <= res.baseline_prop_used_kg


def test_seed_recorded_in_output(opt_run):
    """HANDOFF.md §6 WP-06: seed recorded in outputs."""
    res, _, _ = opt_run
    assert res.seed == 42
    assert res.optimized_result.trajectory["seed"] == 42


def test_optimized_trajectory_contract_validates(opt_run):
    """Optimized trajectory must strictly satisfy contracts/trajectory.schema.json."""
    res, out_json, _ = opt_run
    validate("trajectory", res.optimized_result.trajectory)
    assert Path(out_json).exists()


def test_comparison_plot_created(opt_run):
    """HANDOFF.md §6 WP-06: improvement reported as measured with a naive-vs-optimized plot."""
    _, _, out_plot = opt_run
    assert Path(out_plot).exists()
    assert Path(out_plot).stat().st_size > 50000  # valid image file


def test_deterministic_reproducibility():
    """Identical seeds must produce identical best parameters."""
    rocket = get_rocket("medlift_kerolox_a")
    res1 = optimize_ascent(rocket, seed=123, max_iter=5, pop_size=6)
    res2 = optimize_ascent(rocket, seed=123, max_iter=5, pop_size=6)
    assert res1.best_pitch_params == pytest.approx(res2.best_pitch_params, abs=1e-6)
    assert res1.propellant_saved_kg == pytest.approx(res2.propellant_saved_kg, abs=1e-3)
