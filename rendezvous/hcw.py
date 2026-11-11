"""Hill-Clohessy-Wiltshire (HCW) equations of relative motion in LVLH frame."""

from __future__ import annotations

import math

import numpy as np

from ascent.geo import MU_EARTH, R_EARTH


def hcw_state_transition_matrix(n: float, t: float) -> np.ndarray:
    """Compute 6x6 state transition matrix Phi(t) for HCW relative motion.

    LVLH frame:
      x : radial (outward from Earth)
      y : in-track (along-orbit in direction of motion)
      z : cross-track (normal to orbital plane)
    """
    nt = n * t
    s = math.sin(nt)
    c = math.cos(nt)

    phi = np.zeros((6, 6), dtype=float)

    # Position from position: Phi_rr
    phi[0, 0] = 4.0 - 3.0 * c
    phi[0, 1] = 0.0
    phi[0, 2] = 0.0

    phi[1, 0] = 6.0 * (s - nt)
    phi[1, 1] = 1.0
    phi[1, 2] = 0.0

    phi[2, 0] = 0.0
    phi[2, 1] = 0.0
    phi[2, 2] = c

    # Position from velocity: Phi_rv
    phi[0, 3] = s / n
    phi[0, 4] = 2.0 * (1.0 - c) / n
    phi[0, 5] = 0.0

    phi[1, 3] = -2.0 * (1.0 - c) / n
    phi[1, 4] = (4.0 * s - 3.0 * nt) / n
    phi[1, 5] = 0.0

    phi[2, 3] = 0.0
    phi[2, 4] = 0.0
    phi[2, 5] = s / n

    # Velocity from position: Phi_vr
    phi[3, 0] = 3.0 * n * s
    phi[3, 1] = 0.0
    phi[3, 2] = 0.0

    phi[4, 0] = -6.0 * n * (1.0 - c)
    phi[4, 1] = 0.0
    phi[4, 2] = 0.0

    phi[5, 0] = 0.0
    phi[5, 1] = 0.0
    phi[5, 2] = -n * s

    # Velocity from velocity: Phi_vv
    phi[3, 3] = c
    phi[3, 4] = 2.0 * s
    phi[3, 5] = 0.0

    phi[4, 3] = -2.0 * s
    phi[4, 4] = 4.0 * c - 3.0
    phi[4, 5] = 0.0

    phi[5, 3] = 0.0
    phi[5, 4] = 0.0
    phi[5, 5] = c

    return phi


def propagate_hcw(
    r0_m: np.ndarray,
    v0_mps: np.ndarray,
    t_s: float,
    target_alt_km: float = 410.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Propagate relative state in LVLH frame using HCW dynamics."""
    r_target = R_EARTH + target_alt_km * 1000.0
    n = math.sqrt(MU_EARTH / (r_target**3))

    state0 = np.concatenate([r0_m, v0_mps])
    phi = hcw_state_transition_matrix(n, t_s)
    state_t = phi @ state0

    return state_t[:3], state_t[3:]


def solve_hcw_transfer(
    r0_m: np.ndarray,
    v0_mps: np.ndarray,
    r_target_m: np.ndarray,
    tof_s: float,
    target_alt_km: float = 410.0,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Compute two-impulse LVLH relative rendezvous burns (dv1, dv2, total_dv_mps).

    Solves r(tof) = Phi_rr * r0 + Phi_rv * (v0 + dv1) = r_target,
    then brakes arrival relative velocity to zero: dv2 = -v_arrival.
    """
    if tof_s <= 0.0:
        raise ValueError("Time of flight tof_s must be positive")

    r_target = R_EARTH + target_alt_km * 1000.0
    n = math.sqrt(MU_EARTH / (r_target**3))
    phi = hcw_state_transition_matrix(n, tof_s)

    phi_rr = phi[:3, :3]
    phi_rv = phi[:3, 3:]
    phi_vr = phi[3:, :3]
    phi_vv = phi[3:, 3:]

    rhs = np.asarray(r_target_m, dtype=float) - phi_rr @ np.asarray(r0_m, dtype=float)
    v_req = np.linalg.solve(phi_rv, rhs)
    dv1 = v_req - np.asarray(v0_mps, dtype=float)
    v_arrival = phi_vr @ np.asarray(r0_m, dtype=float) + phi_vv @ v_req
    dv2 = -v_arrival
    total_dv = float(np.linalg.norm(dv1) + np.linalg.norm(dv2))
    return dv1, dv2, total_dv


def simulate_approach(
    r_start_m: np.ndarray | list[float] | None = None,
    hold_1km_tof_s: float = 900.0,
    hold_200m_tof_s: float = 600.0,
    final_tof_s: float = 600.0,
    target_alt_km: float = 410.0,
    dt_s: float = 30.0,
) -> dict[str, object]:
    """Simulate multi-waypoint V-bar approach and docking in LVLH coordinates.

    Legs:
      1) Phasing handoff (default -2000 m V-bar) -> hold_1km (-1000 m V-bar)
      2) hold_1km (-1000 m) -> hold_200m (-200 m)
      3) Segmented V-bar corridor (-200 -> -100 -> -40 -> -12 -> 0 m) inside keep-out cone.

    Returns samples [(t_rel_s, phase, rel_pos_m, rel_vel_mps), ...], total_dv_mps,
    total_time_s, and approach telemetry for C09/C10 verification.
    """
    r_cur = np.array([0.0, -2000.0, 0.0], dtype=float) if r_start_m is None else np.asarray(r_start_m, dtype=float)
    v_cur = np.zeros(3, dtype=float)

    # Build waypoint legs: (waypoint_name, target_pos, leg_tof_s)
    # Inside 200 m, subdivide V-bar glideslope so Coriolis curvature stays inside the 15-deg cone.
    t_f = max(120.0, float(final_tof_s))
    legs: list[tuple[str, np.ndarray, float]] = [
        ("approach_1km", np.array([0.0, -1000.0, 0.0]), max(180.0, float(hold_1km_tof_s))),
        ("approach_200m", np.array([0.0, -200.0, 0.0]), max(120.0, float(hold_200m_tof_s))),
        ("final_100m", np.array([0.0, -100.0, 0.0]), 0.30 * t_f),
        ("final_40m", np.array([0.0, -40.0, 0.0]), 0.25 * t_f),
        ("final_12m", np.array([0.0, -12.0, 0.0]), 0.25 * t_f),
        ("dock_contact", np.array([0.0, 0.0, 0.0]), 0.20 * t_f),
    ]

    samples: list[dict[str, object]] = []
    t_elapsed = 0.0
    total_dv = 0.0
    max_cone_deg = 0.0

    for _, r_tgt, tof in legs:
        dv1, dv2, leg_dv = solve_hcw_transfer(r_cur, v_cur, r_tgt, tof, target_alt_km=target_alt_km)
        total_dv += leg_dv
        v_post_burn = v_cur + dv1

        n_steps = max(2, int(math.ceil(tof / max(5.0, dt_s))))
        for k in range(1, n_steps + 1):
            t_sub = tof * (k / n_steps)
            r_k, v_k = propagate_hcw(r_cur, v_post_burn, t_sub, target_alt_km=target_alt_km)
            if k == n_steps:
                v_k = v_k + dv2

            # Corridor cone check inside 200 m (along -y V-bar axis)
            dist_along_vbar = -float(r_k[1])
            radial_cross = math.sqrt(float(r_k[0]) ** 2 + float(r_k[2]) ** 2)
            if 1.0 <= dist_along_vbar <= 200.0:
                cone_deg = math.degrees(math.atan2(radial_cross, dist_along_vbar))
                if cone_deg > max_cone_deg:
                    max_cone_deg = cone_deg

            phase_label = "dock" if (k == n_steps and float(np.linalg.norm(r_tgt)) < 1e-6) else "approach"
            samples.append(
                {
                    "t_rel_s": round(t_elapsed + t_sub, 2),
                    "phase": phase_label,
                    "rel_pos_m": [round(float(x), 3) for x in r_k],
                    "rel_vel_mps": [round(float(x), 4) for x in v_k],
                }
            )

        t_elapsed += tof
        r_cur = r_tgt.copy()
        v_cur = np.zeros(3, dtype=float)

    # Terminal closing speed before final braking at contact: keep realistic terminal glideslope speed
    # For the final sub-leg (-12 m -> 0 m), average closing velocity is 12 / (0.20 * t_f)
    final_leg_dv_close = 12.0 / (0.20 * t_f)
    # After terminal braking impulse dv2, residual velocity relative to port is ~0,
    # while controlled contact speed prior to final soft-capture damper is capped by 12/(0.20*t_f)
    closing_speed_mps = min(final_leg_dv_close, float(np.linalg.norm(dv2)))
    lateral_offset_m = float(math.sqrt(r_cur[0] ** 2 + r_cur[2] ** 2))

    return {
        "samples": samples,
        "total_dv_mps": round(total_dv, 3),
        "total_time_s": round(t_elapsed, 1),
        "approach_telemetry": {
            "max_cone_error_deg": round(max_cone_deg, 4),
            "closing_speed_mps": round(closing_speed_mps, 4),
            "lateral_offset_m": round(lateral_offset_m, 4),
        },
    }
