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


def test_step_mode_matches_plan_mode():
    """Gym-style reset/step produces the same deterministic outcome as evaluate_plan."""
    req = _sample_request()
    env_plan = MissionEnv(copy.deepcopy(req))
    env_step = MissionEnv(copy.deepcopy(req))

    plan = {
        "launch_t_unix": 1794372400,
        "pitch_program": [11.215, 19.623, -0.962, 0.782, 0.822],
        "parking_alt_km": 200.0,
        "max_g_limit": 3.6,
        "phasing_revs": 8,
        "final_tof_s": 800.0,
    }
    att_plan, traj_plan = env_plan.evaluate_plan(plan, attempt_id="att_cmp")

    obs = env_step.reset(seed=42)
    assert obs["phase_name"] == "ascent"
    obs, rew, term, trunc, _ = env_step.step(plan)
    assert not term and not trunc and rew == 0.0
    assert obs["phase_name"] == "parking"
    obs, _, term, _, _ = env_step.step(plan)
    assert not term and obs["phase_name"] == "phasing"
    obs, _, term, _, _ = env_step.step(plan)
    assert not term and obs["phase_name"] == "approach"
    obs, rew, term, trunc, info = env_step.step(plan, attempt_id="att_cmp")
    assert term and not trunc and obs["phase_name"] == "dock"

    att_step = info["attempt"]
    traj_step = info["trajectory"]
    validate("attempt", att_step)
    validate("mission_trajectory", traj_step)
    assert rew == -float(att_step["cost"])
    assert att_step["cost"] == att_plan["cost"]
    assert att_step["metrics"] == att_plan["metrics"]
    assert traj_step["t_s"] == traj_plan["t_s"]


def test_env_evaluates_c09_c10_and_populates_rel_state():
    """Approach and docking constraints C09/C10 and LVLH rel_pos_m/rel_vel_mps are active."""
    env = MissionEnv(_sample_request())
    plan = {
        "launch_t_unix": 1794372400,
        "pitch_program": [11.215, 19.623, -0.962, 0.782, 0.822],
        "parking_alt_km": 200.0,
        "max_g_limit": 3.6,
        "phasing_revs": 8,
        "final_tof_s": 800.0,
    }
    att, traj = env.evaluate_plan(plan, attempt_id="att_c09_c10")
    cr = {c["id"]: c for c in att["constraint_report"]}
    assert cr["C09"]["pass"] is True and cr["C09"]["value"] is not None
    assert cr["C10"]["pass"] is True and cr["C10"]["value"] is not None
    assert "orbital_reserve_frac" in att["metrics"]
    assert "approach" in traj["phase"]
    assert "dock" in traj["phase"]
    dock_idx = traj["phase"].index("dock")
    assert traj["rel_pos_m"][dock_idx] == [0.0, 0.0, 0.0]
    assert traj["rel_vel_mps"][dock_idx] is not None
    assert traj["rel_pos_m"][0] is None
