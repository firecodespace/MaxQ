"""Closed-form launch geometry and a simple impulsive phasing estimate (HANDOFF section 7 formulas).

Azimuths are degrees clockwise from north. ``inertial_azimuth_deg`` is the heading of the target plane at
the site in the inertial frame (sin(beta) = cos(i)/cos(phi)); this is the value stored in ``windows.json``
(``azimuth_deg``) because it is a pure property of the plane. ``rotating_azimuth_deg`` is the heading the
vehicle must fly relative to the rotating ground, which accounts for the Earth-rotation velocity.

Rendezvous numbers are *estimates*: an impulsive phasing-orbit model, excluding proximity operations and
any operational constraint. Owner: Yash (lane: mission/).
"""

from __future__ import annotations

import math

from core.constants import MU_M3_S2, OMEGA_E_RAD_S, R_E_M


def circular_speed_mps(alt_m: float) -> float:
    return math.sqrt(MU_M3_S2 / (R_E_M + alt_m))


def period_s(semi_major_axis_m: float) -> float:
    return 2.0 * math.pi * math.sqrt(semi_major_axis_m**3 / MU_M3_S2)


def semi_major_axis_from_mean_motion(rev_per_day: float) -> float:
    n = rev_per_day * 2.0 * math.pi / 86400.0
    return (MU_M3_S2 / n**2) ** (1.0 / 3.0)


def rotation_speed_mps(lat_deg: float) -> float:
    """Eastward speed of the ground at the site, omega_E * R_E * cos(phi)."""
    return OMEGA_E_RAD_S * R_E_M * math.cos(math.radians(lat_deg))


def inertial_azimuth_deg(lat_deg: float, incl_deg: float, ascending: bool = True) -> float:
    """Inertial launch azimuth for a direct launch into inclination ``incl_deg``.

    sin(beta) = cos(i)/cos(phi). Raises ValueError if the inclination is below the site latitude (unreachable
    without a dogleg). Descending (southbound) passes return 180 - beta.
    """
    if incl_deg < abs(lat_deg) - 1e-9:
        raise ValueError(
            f"inclination {incl_deg:.3f} deg is below the site latitude {abs(lat_deg):.3f} deg: not reachable directly"
        )
    ratio = math.cos(math.radians(incl_deg)) / math.cos(math.radians(lat_deg))
    beta = math.degrees(math.asin(max(-1.0, min(1.0, ratio))))
    return beta if ascending else 180.0 - beta


def rotating_azimuth_deg(lat_deg: float, incl_deg: float, v_orbit_mps: float, ascending: bool = True) -> float:
    """Azimuth relative to the rotating ground: atan2(v sin(beta) - w R cos(phi), v cos(beta))."""
    beta = math.radians(inertial_azimuth_deg(lat_deg, incl_deg, ascending))
    v_east = v_orbit_mps * math.sin(beta) - rotation_speed_mps(lat_deg)
    v_north = v_orbit_mps * math.cos(beta)
    return math.degrees(math.atan2(v_east, v_north)) % 360.0


def plane_change_dv_mps(v_mps: float, delta_incl_deg: float) -> float:
    """Pure plane change: dv = 2 v sin(di/2)."""
    return 2.0 * v_mps * math.sin(math.radians(delta_incl_deg) / 2.0)


def hohmann_dv_mps(r1_m: float, r2_m: float) -> tuple[float, float]:
    """(dv at r1, dv at r2) for a Hohmann transfer between circular orbits."""
    a_t = 0.5 * (r1_m + r2_m)
    v1, v2 = math.sqrt(MU_M3_S2 / r1_m), math.sqrt(MU_M3_S2 / r2_m)
    vp = math.sqrt(MU_M3_S2 * (2.0 / r1_m - 1.0 / a_t))
    va = math.sqrt(MU_M3_S2 * (2.0 / r2_m - 1.0 / a_t))
    return abs(vp - v1), abs(v2 - va)


def rendezvous_estimates(
    phase_deg: float,
    park_alt_m: float,
    target_a_m: float,
    fast_max_revs: int = 2,
    slow_min_revs: int = 16,
    approach_h: float = 0.25,
) -> tuple[float | None, float]:
    """(fast_hours | None, slow_hours) to rendezvous, given the in-plane lead of the target.

    ``phase_deg`` in [0, 360): how far the target is ahead of the chaser along the orbit at insertion.

    A phasing orbit with perigee at the parking radius and N revolutions has period
    ``T_p = T_t (1 - phase / (360 N))``; it needs ``T_p >= T_parking`` (apogee not below perigee), which sets the
    smallest feasible ``N_min``. More revolutions mean a phasing orbit closer to the target's (smaller burns).

    * fast: ``N_min`` revolutions; available only if ``N_min <= fast_max_revs`` (~3 h for 2 revs), else None.
    * slow: ``max(N_min, slow_min_revs)`` revolutions: the low-propellant approach (about a day for 16 revs).
    ``approach_h`` is a flat allowance for final approach; proximity operations are not modelled.
    """
    t_c = period_s(R_E_M + park_alt_m)
    t_t = period_s(target_a_m)
    if t_c >= t_t:
        raise ValueError("parking orbit must be lower (faster) than the target orbit")
    n_min = max(1, math.ceil(phase_deg / (360.0 * (1.0 - t_c / t_t)) - 1e-12))

    def hours(n: int) -> float:
        return n * t_t * (1.0 - phase_deg / (360.0 * n)) / 3600.0 + approach_h

    fast = round(hours(n_min), 2) if n_min <= fast_max_revs else None
    return fast, round(hours(max(n_min, slow_min_revs)), 2)
