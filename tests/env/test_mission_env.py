"""WP2-02 MissionEnv acceptance tests.

Acceptance test per HANDOFF_PHASE2 §7:
  test_env_deterministic: same request + seed + versions gives an identical attempt.
"""

import copy

from contracts.validate import validate
from env import SIM_VERSION, MissionEnv


def _sample_request() -> dict:
    return {
        "schema_version": 1,
        "id": "msn_crew_iss_test",
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
        "budget": {"max_attempts": 100, "max_wall_s": 600.0},
        "seed": 42,
    }


def test_env_deterministic():
    """Same request + seed + versions gives an identical attempt and trajectory."""
    req1 = _sample_request()
    req2 = copy.deepcopy(req1)

    env1 = MissionEnv(req1)
    env2 = MissionEnv(req2)

    plan = {
        "launch_t_unix": 1794372400,
        "pitch_program": [11.215, 19.623, -0.962, 0.782, 0.822],
        "parking_alt_km": 200.0,
        "burns": [],
        "waypoints": [],
    }

    att1, traj1 = env1.evaluate_plan(plan, attempt_id="att_test_1")
    att2, traj2 = env2.evaluate_plan(plan, attempt_id="att_test_1")

    assert att1["sim_version"] == SIM_VERSION
    assert att1["sim_version"] == att2["sim_version"]
    assert att1["verifier_version"] == att2["verifier_version"]
    assert att1["cost"] == att2["cost"]
    assert att1["feasible"] == att2["feasible"]
    assert att1["metrics"] == att2["metrics"]
    assert traj1["t_s"] == traj2["t_s"]
    assert traj1["alt_m"] == traj2["alt_m"]


def test_attempt_and_trajectory_validate_against_contracts():
    """The emitted attempt and mission_trajectory strictly validate against contracts."""
    req = _sample_request()
    env = MissionEnv(req)

    plan = {
        "launch_t_unix": 1794372400,
        "pitch_program": [11.215, 19.623, -0.962, 0.782, 0.822],
        "parking_alt_km": 200.0,
        "burns": [],
        "waypoints": [],
    }

    att, traj = env.evaluate_plan(plan, attempt_id="att_test_val")

    # Validate against Phase 2 schemas
    validate("attempt", att)
    validate("mission_trajectory", traj)
