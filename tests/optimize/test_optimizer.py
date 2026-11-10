"""WP2-05 Baseline Optimizer acceptance tests."""

from pathlib import Path

from contracts.validate import validate, validate_session_file
from optimize.cma_optimizer import optimize_mission

ROOT = Path(__file__).resolve().parents[2]


def test_optimizer_runs_and_emits_valid_artifacts(tmp_path):
    """CMA-ES runs 3 attempts and emits valid attempt and session logs."""
    req = {
        "schema_version": 1,
        "id": "msn_test_opt",
        "profile": "crew_iss",
        "payload": {"mass_kg": 9500.0, "crew": 4},
        "rocket_id": "falcon9_block5",
        "pad_id": "ksc_lc39a",
        "target": {"norad_id": 25544},
        "window_id": "w0",
        "constraints_profile": "crew_default",
        "objective": {
            "w_fuel": 1.0,
            "w_time": 0.5,
            "fuel_ref_kg": 500000.0,
            "time_ref_s": 86400.0,
        },
        "budget": {"max_attempts": 3, "max_wall_s": 60.0},
        "seed": 42,
    }

    session_log = tmp_path / "session.jsonl"
    best_att = optimize_mission(
        mission_request=req,
        max_attempts=3,
        seed=42,
        session_log_path=session_log,
        save_attempts=False,
    )

    assert best_att is not None
    validate("attempt", best_att)
    assert session_log.exists()
    validate_session_file(session_log)
