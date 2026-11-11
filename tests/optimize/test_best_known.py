"""Tests for WP2-05 multi-seed best-known runner."""

import copy

from contracts.validate import validate
from optimize.best_known import run_best_known

REQUEST = {
    "schema_version": 1,
    "id": "msn_best_known_test",
    "profile": "cargo_iss",
    "payload": {"mass_kg": 12000.0, "crew": 0},
    "rocket_id": "falcon9_block5",
    "pad_id": "ksc_lc39a",
    "target": {"norad_id": 25544},
    "window_id": "w0",
    "constraints_profile": "cargo_default",
    "objective": {
        "w_fuel": 1.0,
        "w_time": 0.5,
        "fuel_ref_kg": 500000.0,
        "time_ref_s": 86400.0,
    },
    "budget": {"max_attempts": 30, "max_wall_s": 300.0},
    "seed": 42,
}


def test_best_known_runner_deterministic_and_valid(tmp_path):
    out_file = tmp_path / "best_known.json"
    r1 = run_best_known(
        copy.deepcopy(REQUEST),
        seeds=(11, 22),
        budget_per_seed=15,
        out_path=out_file,
    )
    r2 = run_best_known(
        copy.deepcopy(REQUEST),
        seeds=(11, 22),
        budget_per_seed=15,
        out_path=None,
    )
    assert out_file.exists()
    assert r1["kind"] == "best_known_measured"
    assert r1["total_evaluations"] == 30
    assert r1["seeds"] == [11, 22]
    assert len(r1["seed_results"]) == 2
    assert r1["best_cost"] == r2["best_cost"]
    assert r1["best_seed"] == r2["best_seed"]
    validate("attempt", r1["best_attempt"])

