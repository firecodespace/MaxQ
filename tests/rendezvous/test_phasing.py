"""WP2-03 Phasing and Rendezvous physics acceptance tests.

Acceptance tests per HANDOFF_PHASE2 §7:
  test_hohmann_200_to_410km: total ≈ 121 m/s (accept 119–124)
  test_hcw_vs_kepler: within 1% for separations <= 1 km over a fraction of an orbit.
"""

import math

import numpy as np
from scipy.integrate import solve_ivp

from ascent.geo import MU_EARTH, R_EARTH
from rendezvous.hcw import propagate_hcw
from rendezvous.phasing import hohmann_transfer


def test_hohmann_200_to_410km():
    """Hohmann transfer from 200 km to 410 km orbit requires 119-124 m/s delta-v."""
    res = hohmann_transfer(200.0, 410.0)
    total_dv = res["total_dv_mps"]

    # HANDOFF_PHASE2 §7: "total ≈ 121 m/s (accept 119–124)"
    assert 119.0 <= total_dv <= 124.0, f"Expected 119-124 m/s, got {total_dv} m/s"
    assert res["transfer_time_s"] > 2500.0


def test_hcw_vs_kepler():
    """Compare HCW relative motion against numerical Keplerian propagation."""
    target_alt_km = 410.0
    r_target = R_EARTH + target_alt_km * 1000.0
    v_target = math.sqrt(MU_EARTH / r_target)
    n = math.sqrt(MU_EARTH / (r_target**3))

    # Chaser initial relative state in target LVLH frame:
    # 50 m radial, 500 m along-track
    r0_lvlh = np.array([50.0, 500.0, 0.0])
    v0_lvlh = np.array([0.0, -2.0 * n * 50.0, 0.0])

    t_eval = 300.0  # fraction of orbit (~5.5% of 92 min orbit)

    # 1. Closed-form HCW propagation
    r_hcw, _ = propagate_hcw(r0_lvlh, v0_lvlh, t_eval, target_alt_km)

    # 2. Numerical Keplerian two-body propagation in ECI frame
    # At t=0, target is at [r_target, 0, 0], velocity [0, v_target, 0]
    # LVLH rotation vector:
    omega = np.array([0.0, 0.0, n])
    r_chaser_0 = np.array([r_target + r0_lvlh[0], r0_lvlh[1], r0_lvlh[2]])
    # Chaser inertial velocity: v_inertial = v_rel + omega x r_chaser
    v_chaser_0 = v0_lvlh + np.cross(omega, r_chaser_0)

    def two_body(t, y):
        r = y[:3]
        v = y[3:]
        a = -MU_EARTH * r / (np.linalg.norm(r)**3)
        return np.concatenate([v, a])

    sol = solve_ivp(
        two_body,
        [0, t_eval],
        np.concatenate([r_chaser_0, v_chaser_0]),
        rtol=1e-10,
        atol=1e-10,
    )
    r_chs_eci = sol.y[:3, -1]

    # Target analytical position and velocity at t_eval (circular orbit):
    theta = n * t_eval
    r_tgt_eci = np.array([r_target * math.cos(theta), r_target * math.sin(theta), 0.0])
    v_tgt_eci = np.array([-v_target * math.sin(theta), v_target * math.cos(theta), 0.0])

    # LVLH basis vectors at t_eval:
    i_r = r_tgt_eci / np.linalg.norm(r_tgt_eci)
    i_w = np.cross(r_tgt_eci, v_tgt_eci)
    i_w = i_w / np.linalg.norm(i_w)
    i_v = np.cross(i_w, i_r)

    rel_eci = r_chs_eci - r_tgt_eci
    r_kepler_lvlh = np.array([
        np.dot(rel_eci, i_r),
        np.dot(rel_eci, i_v),
        np.dot(rel_eci, i_w),
    ])

    rel_error = np.linalg.norm(r_hcw - r_kepler_lvlh) / np.linalg.norm(r_kepler_lvlh)
    assert rel_error < 0.01, f"Expected HCW vs Kepler error < 1%, got {rel_error:.4%}"
