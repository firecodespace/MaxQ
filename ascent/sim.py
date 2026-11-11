"""2D Point-Mass Ascent Trajectory Simulator.

Flies a rocket archetype to a low Earth parking orbit and exports trajectory.json
conforming to contracts/trajectory.schema.json.

Physics & modeling per HANDOFF.md §7:
- 2D point mass in launch plane (radial distance r and downrange angle theta)
- Thrust with Isp, stage separation and dry mass drop
- Inverse-square gravity g(r) = mu / r^2
- Drag F_d = 0.5 * rho * v_air^2 * Cd * A with exponential atmosphere
- Earth-rotation eastward velocity boost v_rot = omega_E * R_E * cos(lat)
- Parameterized pitch program psi(t)
- Events: max_q, meco, stage_sep, insertion
- Geodesic mapping to (lat_deg, lon_deg) via spherical_forward
- 1 Hz uniform discrete sampling for all output arrays
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ascent.atmosphere import density
from ascent.geo import (
    G0,
    MU_EARTH,
    OMEGA_EARTH,
    R_EARTH,
    earth_rotation_boost,
    spherical_forward,
)
from ascent.rockets import Rocket, get_rocket
from core.frames import ecef_to_geodetic

# Default tuned pitch program parameters [t_start_s, p_meco_deg, p_final_deg, alpha1, alpha2]
DEFAULT_PITCH_PARAMS: dict[str, list[float]] = {
    "medlift_kerolox_a": [11.865, 18.262, -1.0, 0.81, 0.747],
    "crewlift_kerolox_b": [11.215, 19.623, -0.962, 0.782, 0.822],
    "heavylift_hydrolox_c": [13.224, 12.145, -1.126, 0.742, 0.864],
}


@dataclass
class SimResult:
    """Simulation output telemetry and orbital metrics."""

    trajectory: dict[str, Any]
    perigee_km: float
    apogee_km: float
    eccentricity: float
    insertion_speed_mps: float
    insertion_alt_m: float
    max_q_pa: float
    t_max_q_s: float
    gravity_loss_mps: float
    drag_loss_mps: float
    total_loss_mps: float
    t_insertion_s: float
    s1_prop_used_kg: float = 0.0
    s2_prop_used_kg: float = 0.0
    s2_prop_remaining_kg: float = 0.0
    max_accel_g: float = 0.0
    t_meco_s: float = 0.0

    def export(self, path: Path | str, indent: int = 2) -> None:
        """Save trajectory JSON."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.trajectory, f, indent=indent)


def build_pitch_evaluator(
    pitch_params: Sequence[float],
    t_meco: float,
    t_ign2: float,
    t_max: float,
) -> Callable[[float], float]:
    """Builds a continuous pitch angle function psi(t) in degrees above local horizontal."""
    p_start = float(pitch_params[0]) if len(pitch_params) > 0 else 10.0
    p_meco = float(pitch_params[1]) if len(pitch_params) > 1 else 20.0
    p_final = float(pitch_params[2]) if len(pitch_params) > 2 else 0.0
    a1 = float(pitch_params[3]) if len(pitch_params) > 3 else 0.8
    a2 = float(pitch_params[4]) if len(pitch_params) > 4 else 0.8

    def pitch_deg(t: float) -> float:
        if t < p_start:
            return 90.0
        if t <= t_meco:
            tau = (t - p_start) / max(1.0, t_meco - p_start)
            tau = min(1.0, max(0.0, tau))
            return 90.0 - (90.0 - p_meco) * (tau**a1)
        if t < t_ign2:
            return p_meco
        tau = (t - t_ign2) / max(1.0, t_max - t_ign2)
        tau = min(1.0, max(0.0, tau))
        return p_meco - (p_meco - p_final) * (tau**a2)

    return pitch_deg


def simulate_ascent(
    rocket: Rocket,
    site_lat_deg: float = 28.5,
    site_lon_deg: float = -80.6,
    azimuth_deg: float = 44.9,
    target_incl_deg: float = 51.6,
    pitch_params: Sequence[float] | None = None,
    trajectory_id: str = "traj_medlift_a_w0",
    window_id: str = "w0",
    t0_unix: int = 1794286000,
    seed: int = 42,
    dt_substep: float = 0.05,
    target_parking_alt_m: float = 200000.0,
    is_dummy: bool = False,
    throttle_s1: float = 1.0,
    throttle_s2: float = 1.0,
    max_g_limit: float | None = None,
    coast_s2_s: float = 0.0,
    s2_burn1_frac: float = 1.0,
) -> SimResult:
    """Execute 2D point-mass rocket ascent simulation.

    Parameters
    ----------
    rocket : Rocket
        Rocket archetype with stages and payload.
    site_lat_deg : float
        Launch site latitude (degrees).
    site_lon_deg : float
        Launch site longitude (degrees).
    azimuth_deg : float
        Launch azimuth (degrees clockwise from North).
    target_incl_deg : float
        Target orbital inclination (degrees).
    pitch_params : Sequence[float] | None
        Parameters controlling pitch program [t_start, p_meco, p_final, a1, a2].
    trajectory_id : str
        Trajectory identifier for contract.
    window_id : str
        Associated launch window identifier.
    t0_unix : int
        Launch timestamp (Unix seconds).
    seed : int
        Deterministic random seed.
    dt_substep : float
        Numerical integration sub-step (s).
    target_parking_alt_m : float
        Nominal parking orbit altitude in meters (default 200 km).
    is_dummy : bool
        If True, marks "_note": "dummy" in trajectory output.
    throttle_s1 : float
        Stage-1 throttle fraction in [0.25, 1.0].
    throttle_s2 : float
        Stage-2 throttle fraction in [0.25, 1.0].
    max_g_limit : float | None
        Optional axial acceleration throttle cap in g (throttles engines continuously).
    coast_s2_s : float
        Optional coast duration (s) between Stage-2 burn 1 and burn 2.
    s2_burn1_frac : float
        Fraction of Stage-2 propellant expended before coast_s2_s when coast_s2_s > 0.

    Returns
    -------
    SimResult
        Telemetry arrays, events, orbit metrics, and losses.
    """
    if len(rocket.stages) < 2:
        raise ValueError(f"Rocket {rocket.id} must have at least 2 stages for ascent sim")

    s1, s2 = rocket.stages[0], rocket.stages[1]

    thr1_cmd = min(1.0, max(0.25, float(throttle_s1)))
    thr2_cmd = min(1.0, max(0.25, float(throttle_s2)))
    coast2_dur = max(0.0, float(coast_s2_s))
    burn1_frac = min(1.0, max(0.05, float(s2_burn1_frac)))

    # Nominal staging schedule for pitch curve scaling
    t_sep_coast = 5.0
    t_meco_nom = s1.burn_time_s / thr1_cmd
    t_ign2_nom = t_meco_nom + t_sep_coast
    t_burn2_nom = s2.burn_time_s / thr2_cmd
    t_max_sim = (s1.burn_time_s / 0.25) + t_sep_coast + coast2_dur + (s2.burn_time_s / 0.25) + 20.0

    if pitch_params is None:
        pitch_params = DEFAULT_PITCH_PARAMS.get(
            rocket.id, [11.865, 18.262, -1.0, 0.81, 0.747]
        )

    pitch_evaluator = build_pitch_evaluator(
        pitch_params, t_meco_nom, t_ign2_nom, t_ign2_nom + t_burn2_nom
    )

    # Earth rotation initial velocity
    v_boost = earth_rotation_boost(site_lat_deg)

    # Initial state
    r = R_EARTH
    theta = 0.0
    vr = 0.0
    vd = v_boost
    m = rocket.liftoff_mass_kg

    prop1_rem = float(s1.prop_kg)
    prop2_rem = float(s2.prop_kg)
    use_s2_coast = coast2_dur > 0.0 and burn1_frac < 0.999
    s2_burn1_budget = float(s2.prop_kg) * burn1_frac if use_s2_coast else float(s2.prop_kg)
    s2_prop_used = 0.0

    t = 0.0
    max_q = 0.0
    t_max_q = 0.0
    max_accel_g = 0.0
    dv_drag = 0.0
    dv_grav = 0.0
    ideal_dv = 0.0

    # 1 Hz discrete logs
    t_s_list: list[int] = []
    lat_deg_list: list[float] = []
    lon_deg_list: list[float] = []
    alt_m_list: list[float] = []
    speed_mps_list: list[float] = []
    q_pa_list: list[float] = []
    mass_kg_list: list[float] = []

    next_record_t = 0.0
    t_meco = t_meco_nom
    t_sep = t_ign2_nom
    t_ign2 = t_ign2_nom
    t_insertion = 0.0
    insertion_reached = False
    v_ins_speed = 0.0
    h_ins_alt = 0.0
    perigee_km = 0.0
    apogee_km = 0.0
    ecc_final = 0.0
    stage1_separated = False

    stage_state = "s1"  # s1 -> sep_coast -> s2_burn1 -> s2_coast -> s2_burn2 -> done
    s2_coast_start_t = 0.0
    s2_coast_elapsed = 0.0
    depleted_at_t: float | None = None

    while t <= t_max_sim:
        if depleted_at_t is not None and t >= depleted_at_t + 5.0:
            break

        h = r - R_EARTH
        speed = math.sqrt(vr**2 + vd**2)
        v_circ = math.sqrt(MU_EARTH / r)

        # Record at 1 Hz
        if t >= next_record_t - 1e-6:
            downrange_m = R_EARTH * theta
            lat_iner, lon_iner = spherical_forward(site_lat_deg, site_lon_deg, azimuth_deg, downrange_m)

            # Earth-fixed longitude: subtract Earth rotation accumulated since launch
            lon_ecef = lon_iner - math.degrees(OMEGA_EARTH * t)
            lon_ecef = (lon_ecef + 180.0) % 360.0 - 180.0

            # Convert spherical geocentric coordinates (lat_iner, lon_ecef, r) to WGS-84 geodetic
            phi_rad = math.radians(lat_iner)
            lam_rad = math.radians(lon_ecef)
            x_ecef = r * math.cos(phi_rad) * math.cos(lam_rad)
            y_ecef = r * math.cos(phi_rad) * math.sin(lam_rad)
            z_ecef = r * math.sin(phi_rad)
            lat_wgs, lon_wgs, alt_wgs = ecef_to_geodetic(np.array([x_ecef, y_ecef, z_ecef]))

            rho_rec = density(h)
            v_air_d_rec = vd - OMEGA_EARTH * r * math.cos(math.radians(site_lat_deg))
            v_air_rec = math.sqrt(vr**2 + v_air_d_rec**2)
            q_rec = 0.5 * rho_rec * v_air_rec**2

            sec = int(round(next_record_t))
            t_s_list.append(sec)
            lat_deg_list.append(round(float(lat_wgs), 4))
            lon_deg_list.append(round(float(lon_wgs), 4))
            alt_m_list.append(round(float(alt_wgs), 1))
            speed_mps_list.append(round(speed, 1))
            q_pa_list.append(round(q_rec, 1))
            mass_kg_list.append(round(m, 1))

            next_record_t += 1.0

        # Max dynamic pressure tracking
        rho_current = density(h)
        v_air_d_cur = vd - OMEGA_EARTH * r * math.cos(math.radians(site_lat_deg))
        v_air_cur = math.sqrt(vr**2 + v_air_d_cur**2)
        q_current = 0.5 * rho_current * v_air_cur**2
        if q_current > max_q:
            max_q = q_current
            t_max_q = t

        # Check insertion cutoff: stage 2 active, altitude >= 160 km, semi-major axis >= target
        eps_current = 0.5 * speed**2 - MU_EARTH / r
        a_semi = -MU_EARTH / (2.0 * eps_current) if eps_current < 0 else 1e9
        if (
            stage_state in ("s2_burn1", "s2_burn2")
            and h >= 160000.0
            and (a_semi >= (R_EARTH + target_parking_alt_m) or speed >= v_circ)
            and not insertion_reached
        ):
            insertion_reached = True
            t_insertion = t
            v_ins_speed = speed
            h_ins_alt = h
            eps_final = eps_current
            a_final = a_semi
            h_ang_final = r * vd
            ecc_sq = 1.0 + 2.0 * eps_final * (h_ang_final**2) / (MU_EARTH**2)
            ecc_final = math.sqrt(max(0.0, ecc_sq))
            r_peri = a_final * (1.0 - ecc_final)
            r_apo = a_final * (1.0 + ecc_final)
            perigee_km = (r_peri - R_EARTH) / 1000.0
            apogee_km = (r_apo - R_EARTH) / 1000.0

        # After insertion, coast 5 seconds in parking orbit to allow central-difference
        # velocity reconstruction in downstream collision screening
        if insertion_reached and t >= t_insertion + 5.0:
            break

        # Stage state transitions & parameters
        is_default_mode = (
            thr1_cmd == 1.0
            and thr2_cmd == 1.0
            and max_g_limit is None
            and not use_s2_coast
        )
        if is_default_mode:
            if insertion_reached or prop2_rem <= 1e-6 and t >= t_ign2_nom:
                if not insertion_reached and prop2_rem <= 1e-6:
                    stage_state = "depleted"
                    if depleted_at_t is None:
                        depleted_at_t = t
                thrust = 0.0
                cd = 0.0
                area = 0.0
                mdot = 0.0
            elif t <= t_meco_nom:
                stage_state = "s1"
                thrust = s1.thrust_n
                cd = s1.cd
                area = s1.area_m2
                mdot = s1.mass_flow_rate_kgps
            elif t < t_ign2_nom:
                stage_state = "sep_coast"
                thrust = 0.0
                cd = s1.cd
                area = s1.area_m2
                mdot = 0.0
            else:
                stage_state = "s2_burn1"
                thrust = s2.thrust_n
                cd = s2.cd
                area = s2.area_m2
                mdot = s2.mass_flow_rate_kgps
            if t >= t_meco_nom and not stage1_separated:
                m -= s1.dry_kg
                stage1_separated = True
        else:
            if stage_state == "s1" and prop1_rem <= 1e-6:
                stage_state = "sep_coast"
                t_meco = t
                t_sep = t_meco + t_sep_coast
                t_ign2 = t_sep
                if not stage1_separated:
                    m -= s1.dry_kg
                    stage1_separated = True

            if stage_state == "sep_coast" and t >= t_ign2:
                stage_state = "s2_burn1"

            if (
                stage_state == "s2_burn1"
                and use_s2_coast
                and s2_prop_used >= s2_burn1_budget - 1e-6
            ):
                stage_state = "s2_coast"
                s2_coast_start_t = t

            if stage_state == "s2_coast" and t >= s2_coast_start_t + coast2_dur:
                s2_coast_elapsed += t - s2_coast_start_t
                stage_state = "s2_burn2"

            if (
                stage_state in ("s2_burn1", "s2_burn2")
                and prop2_rem <= 1e-6
                and not insertion_reached
            ):
                stage_state = "depleted"
                if depleted_at_t is None:
                    depleted_at_t = t

            if insertion_reached or stage_state in ("s2_coast", "depleted"):
                thrust = 0.0
                cd = 0.0
                area = 0.0
                mdot = 0.0
            elif stage_state == "s1":
                eff_thr = thr1_cmd
                if max_g_limit is not None and max_g_limit > 0.0:
                    g_cap_thr = (max_g_limit * m * G0) / max(1.0, s1.thrust_n)
                    eff_thr = max(0.15, min(eff_thr, g_cap_thr))
                mdot_full = eff_thr * s1.mass_flow_rate_kgps
                max_mdot = prop1_rem / dt_substep
                if mdot_full > max_mdot:
                    scale = max(0.0, max_mdot / max(1e-9, mdot_full))
                    thrust = eff_thr * s1.thrust_n * scale
                    mdot = max_mdot
                else:
                    thrust = eff_thr * s1.thrust_n
                    mdot = mdot_full
                cd = s1.cd
                area = s1.area_m2
            elif stage_state == "sep_coast":
                thrust = 0.0
                cd = s1.cd
                area = s1.area_m2
                mdot = 0.0
            else:
                eff_thr = thr2_cmd
                if max_g_limit is not None and max_g_limit > 0.0:
                    g_cap_thr = (max_g_limit * m * G0) / max(1.0, s2.thrust_n)
                    eff_thr = max(0.15, min(eff_thr, g_cap_thr))
                mdot_full = eff_thr * s2.mass_flow_rate_kgps
                rem_budget = prop2_rem
                if stage_state == "s2_burn1" and use_s2_coast:
                    rem_budget = min(rem_budget, max(0.0, s2_burn1_budget - s2_prop_used))
                max_mdot = rem_budget / dt_substep
                if mdot_full > max_mdot:
                    scale = max(0.0, max_mdot / max(1e-9, mdot_full))
                    thrust = eff_thr * s2.thrust_n * scale
                    mdot = max_mdot
                else:
                    thrust = eff_thr * s2.thrust_n
                    mdot = mdot_full
                cd = s2.cd
                area = s2.area_m2

        pitch_time_offset = s2_coast_elapsed if stage_state == "s2_burn2" else 0.0
        if thrust > 0.0:
            accel_inst_g = thrust / (max(1.0, m) * G0)
            if accel_inst_g > max_accel_g:
                max_accel_g = accel_inst_g

        # Dynamics derivatives function
        def derivatives(
            t_c: float,
            r_c: float,
            th_c: float,
            vr_c: float,
            vd_c: float,
            m_c: float,
        ) -> tuple[float, float, float, float, float, float]:
            h_c = r_c - R_EARTH
            rho_c = density(h_c)
            v_air_d_c = vd_c - OMEGA_EARTH * r_c * math.cos(math.radians(site_lat_deg))
            v_air_c = math.sqrt(vr_c**2 + v_air_d_c**2)
            f_drag_c = 0.5 * rho_c * v_air_c**2 * cd * area

            drag_r = f_drag_c * (vr_c / v_air_c) if v_air_c > 0 else 0.0
            drag_d = f_drag_c * (v_air_d_c / v_air_c) if v_air_c > 0 else 0.0

            psi_c = math.radians(pitch_evaluator(t_c - pitch_time_offset))
            ft_r = thrust * math.sin(psi_c)
            ft_d = thrust * math.cos(psi_c)

            g_loc = MU_EARTH / (r_c * r_c)
            dr_dt = vr_c
            dth_dt = vd_c / r_c
            dvr_dt = (vd_c**2 / r_c) - g_loc + (ft_r - drag_r) / m_c
            dvd_dt = -(vr_c * vd_c / r_c) + (ft_d - drag_d) / m_c
            dm_dt = -mdot
            return dr_dt, dth_dt, dvr_dt, dvd_dt, dm_dt, f_drag_c

        # RK4 Integration
        k1_r, k1_th, k1_vr, k1_vd, k1_m, f_d1 = derivatives(t, r, theta, vr, vd, m)
        k2_r, k2_th, k2_vr, k2_vd, k2_m, _ = derivatives(
            t + 0.5 * dt_substep,
            r + 0.5 * dt_substep * k1_r,
            theta + 0.5 * dt_substep * k1_th,
            vr + 0.5 * dt_substep * k1_vr,
            vd + 0.5 * dt_substep * k1_vd,
            m + 0.5 * dt_substep * k1_m,
        )
        k3_r, k3_th, k3_vr, k3_vd, k3_m, _ = derivatives(
            t + 0.5 * dt_substep,
            r + 0.5 * dt_substep * k2_r,
            theta + 0.5 * dt_substep * k2_th,
            vr + 0.5 * dt_substep * k2_vr,
            vd + 0.5 * dt_substep * k2_vd,
            m + 0.5 * dt_substep * k2_m,
        )
        k4_r, k4_th, k4_vr, k4_vd, k4_m, _ = derivatives(
            t + dt_substep,
            r + dt_substep * k3_r,
            theta + dt_substep * k3_th,
            vr + dt_substep * k3_vr,
            vd + dt_substep * k3_vd,
            m + dt_substep * k3_m,
        )

        # Loss integration
        dv_drag += (f_d1 / m) * dt_substep
        psi_current = math.radians(pitch_evaluator(t - pitch_time_offset))
        dv_grav += (MU_EARTH / (r * r)) * math.sin(psi_current) * dt_substep
        ideal_dv += (thrust / m) * dt_substep

        r += (dt_substep / 6.0) * (k1_r + 2 * k2_r + 2 * k3_r + k4_r)
        theta += (dt_substep / 6.0) * (k1_th + 2 * k2_th + 2 * k3_th + k4_th)
        vr += (dt_substep / 6.0) * (k1_vr + 2 * k2_vr + 2 * k3_vr + k4_vr)
        vd += (dt_substep / 6.0) * (k1_vd + 2 * k2_vd + 2 * k3_vd + k4_vd)
        m += (dt_substep / 6.0) * (k1_m + 2 * k2_m + 2 * k3_m + k4_m)

        dm_step = mdot * dt_substep
        if stage_state == "s1":
            prop1_rem = max(0.0, prop1_rem - dm_step)
        elif stage_state in ("s2_burn1", "s2_burn2") and not insertion_reached:
            used_dm = min(prop2_rem, dm_step)
            prop2_rem = max(0.0, prop2_rem - used_dm)
            s2_prop_used += used_dm

        t += dt_substep

    if not insertion_reached:
        t_insertion = t
        v_ins_speed = math.sqrt(vr**2 + vd**2)
        h_ins_alt = r - R_EARTH
        eps_final = 0.5 * v_ins_speed**2 - MU_EARTH / r
        a_final = -MU_EARTH / (2.0 * eps_final) if eps_final < 0 else 1e9
        h_ang_final = r * vd
        ecc_sq = 1.0 + 2.0 * eps_final * (h_ang_final**2) / (MU_EARTH**2)
        ecc_final = math.sqrt(max(0.0, ecc_sq))
        r_peri = a_final * (1.0 - ecc_final)
        r_apo = a_final * (1.0 + ecc_final)
        perigee_km = (r_peri - R_EARTH) / 1000.0
        apogee_km = (r_apo - R_EARTH) / 1000.0

    # Ensure events timestamps are integers within the discrete 1 Hz series
    sec_max_q = min(max(0, int(round(t_max_q))), t_s_list[-1])
    sec_meco = min(max(0, int(round(t_meco))), t_s_list[-1])
    sec_sep = min(max(sec_meco, int(round(t_sep))), t_s_list[-1])
    sec_ins = min(max(sec_sep, int(round(t_insertion))), t_s_list[-1])

    events = [
        {"t_s": sec_max_q, "name": "max_q"},
        {"t_s": sec_meco, "name": "meco"},
        {"t_s": sec_sep, "name": "stage_sep"},
        {"t_s": sec_ins, "name": "insertion"},
    ]

    # Trajectory contract dictionary
    traj_dict: dict[str, Any] = {
        "schema_version": 1,
        "id": trajectory_id,
        "rocket_id": rocket.id,
        "window_id": window_id,
        "t0_unix": t0_unix,
        "seed": seed,
        "t_s": t_s_list,
        "lat_deg": lat_deg_list,
        "lon_deg": lon_deg_list,
        "alt_m": alt_m_list,
        "speed_mps": speed_mps_list,
        "q_pa": q_pa_list,
        "mass_kg": mass_kg_list,
        "events": events,
        "final_orbit": {
            "perigee_km": round(perigee_km, 1),
            "apogee_km": round(apogee_km, 1),
            "incl_deg": round(target_incl_deg, 2),
            "ecc": round(ecc_final, 4),
        },
        "params": {
            "pitch_program": [float(p) for p in pitch_params],
        },
    }

    if is_dummy:
        traj_dict["_note"] = "dummy"

    return SimResult(
        trajectory=traj_dict,
        perigee_km=perigee_km,
        apogee_km=apogee_km,
        eccentricity=ecc_final,
        insertion_speed_mps=v_ins_speed,
        insertion_alt_m=h_ins_alt,
        max_q_pa=max_q,
        t_max_q_s=t_max_q,
        gravity_loss_mps=dv_grav,
        drag_loss_mps=dv_drag,
        total_loss_mps=dv_grav + dv_drag,
        t_insertion_s=t_insertion,
        s1_prop_used_kg=max(0.0, float(s1.prop_kg) - prop1_rem),
        s2_prop_used_kg=s2_prop_used,
        s2_prop_remaining_kg=prop2_rem,
        max_accel_g=max_accel_g,
        t_meco_s=t_meco,
    )


def main() -> None:
    """CLI Entrypoint for WP-05 Ascent Simulator."""
    parser = argparse.ArgumentParser(description="MaxQ Ascent Simulator")
    parser.add_argument(
        "--rocket",
        type=str,
        default="medlift_kerolox_a",
        help="Rocket archetype ID (medlift_kerolox_a, crewlift_kerolox_b, heavylift_hydrolox_c)",
    )
    parser.add_argument(
        "--windows",
        type=str,
        default="data/dummy/windows_dummy.json",
        help="Path to windows.json",
    )
    parser.add_argument(
        "--window-id",
        type=str,
        default="w0",
        help="Launch window ID to simulate",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="data/dummy/trajectory_dummy.json",
        help="Output trajectory JSON path",
    )
    args = parser.parse_args()

    rocket = get_rocket(args.rocket)

    # Load launch site and azimuth from windows JSON
    site_lat = 28.5
    site_lon = -80.6
    azimuth = 44.9
    incl = 51.6
    t0_unix = 1794286000

    win_path = Path(args.windows)
    if win_path.exists():
        with open(win_path, encoding="utf-8") as f:
            win_data = json.load(f)
        site_lat = float(win_data.get("site", {}).get("lat_deg", site_lat))
        site_lon = float(win_data.get("site", {}).get("lon_deg", site_lon))
        for w in win_data.get("windows", []):
            if w.get("id") == args.window_id:
                azimuth = float(w.get("azimuth_deg", azimuth))
                t0_unix = int(w.get("t_launch_unix", t0_unix))
                break

    print(f"Simulating {rocket.name} from {site_lat}N, {site_lon}E at azimuth {azimuth} deg...")
    result = simulate_ascent(
        rocket=rocket,
        site_lat_deg=site_lat,
        site_lon_deg=site_lon,
        azimuth_deg=azimuth,
        target_incl_deg=incl,
        trajectory_id=f"traj_{rocket.id}_{args.window_id}",
        window_id=args.window_id,
        t0_unix=t0_unix,
        is_dummy="dummy" in args.out,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.export(out_path)

    # Validate against contract
    from contracts.validate import validate

    validate("trajectory", result.trajectory)

    alt_km = result.insertion_alt_m / 1000.0
    print(f"Trajectory saved to {out_path} and validated against contract!")
    print(
        f"Insertion: t={result.t_insertion_s:.1f}s, speed={result.insertion_speed_mps:.1f} m/s, "
        f"alt={alt_km:.1f} km"
    )
    print(
        f"Orbit: perigee={result.perigee_km:.1f} km, apogee={result.apogee_km:.1f} km, "
        f"ecc={result.eccentricity:.5f}"
    )
    print(f"Max-Q: {result.max_q_pa:.0f} Pa at t={result.t_max_q_s:.1f} s")
    print(
        f"Losses: Gravity={result.gravity_loss_mps:.1f} m/s, Drag={result.drag_loss_mps:.1f} m/s, "
        f"Total={result.total_loss_mps:.1f} m/s"
    )


if __name__ == "__main__":
    main()
