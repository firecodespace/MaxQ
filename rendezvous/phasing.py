"""Hohmann transfer orbital mechanics calculations."""

from __future__ import annotations

import math

from ascent.geo import MU_EARTH, R_EARTH


def hohmann_transfer(
    r1_alt_km: float,
    r2_alt_km: float,
) -> dict[str, float]:
    """Calculate two-impulse Hohmann transfer delta-v and duration.

    Parameters
    ----------
    r1_alt_km : float
        Initial orbit altitude above Earth surface (km).
    r2_alt_km : float
        Target orbit altitude above Earth surface (km).

    Returns
    -------
    dict
        dv1_mps, dv2_mps, total_dv_mps, transfer_time_s.
    """
    r1 = (R_EARTH + r1_alt_km * 1000.0)
    r2 = (R_EARTH + r2_alt_km * 1000.0)

    # Circular velocities
    v1 = math.sqrt(MU_EARTH / r1)
    v2 = math.sqrt(MU_EARTH / r2)

    # Semi-major axis of transfer orbit
    a_tx = (r1 + r2) / 2.0

    # Velocities at periapsis and apoapsis of transfer orbit
    v_tx1 = math.sqrt(MU_EARTH * (2.0 / r1 - 1.0 / a_tx))
    v_tx2 = math.sqrt(MU_EARTH * (2.0 / r2 - 1.0 / a_tx))

    dv1 = abs(v_tx1 - v1)
    dv2 = abs(v2 - v_tx2)
    total_dv = dv1 + dv2

    # Transfer duration: half period of transfer ellipse
    t_tx = math.pi * math.sqrt(a_tx**3 / MU_EARTH)

    return {
        "dv1_mps": round(dv1, 3),
        "dv2_mps": round(dv2, 3),
        "total_dv_mps": round(total_dv, 3),
        "transfer_time_s": round(t_tx, 1),
    }


def solve_phasing_orbit(
    r_park_alt_km: float,
    r_target_alt_km: float,
    phase_angle_deg: float,
    n_revs: int = 2,
) -> dict[str, float]:
    """Solve co-planar orbital phasing + Hohmann climb from parking orbit to target.

    Combines:
    1) Optional phasing-ellipse/wait adjustment in `n_revs` revolutions from `r_park_alt_km`
       to match the Hohmann departure lead angle for closing `phase_angle_deg`, and
    2) A Hohmann transfer from `r_park_alt_km` to `r_target_alt_km`.

    Fewer revolutions force a larger period change (more propellant, less time);
    more revolutions coast closer to the natural synodic rate (less propellant, more time).
    """
    n_rev = max(1, int(n_revs))
    r1 = R_EARTH + r_park_alt_km * 1000.0
    r2 = R_EARTH + r_target_alt_km * 1000.0

    hoh = hohmann_transfer(r_park_alt_km, r_target_alt_km)
    t_hoh = hoh["transfer_time_s"]

    omega1 = math.sqrt(MU_EARTH / (r1**3))
    omega2 = math.sqrt(MU_EARTH / (r2**3))
    t_park = 2.0 * math.pi / omega1

    # Target moves omega2 * t_hoh during Hohmann half-ellipse; chaser sweeps pi rad
    phi_hoh_needed = (math.pi - omega2 * t_hoh) % (2.0 * math.pi)
    phi_initial = math.radians(phase_angle_deg) % (2.0 * math.pi)

    # Angular phase chaser needs to gain/lose relative to target before Hohmann burn 1
    dphi = (phi_initial - phi_hoh_needed) % (2.0 * math.pi)

    # Required phasing orbit period T_phase so target catches alignment after n_rev orbits:
    # Target travels omega2 * (n_rev * T_phase), chaser completes n_rev * 2*pi rad
    # Catch-up dphi_per_rev = 2*pi - omega2 * T_phase = dphi / n_rev  =>  T_phase = (2*pi - dphi/n_rev)/omega2
    dphi_per_rev = dphi / n_rev
    t_phase = max(t_park * 0.85, (2.0 * math.pi - dphi_per_rev) / omega2)

    # Semi-major axis corresponding to T_phase from Kepler's 3rd law
    a_phase = (MU_EARTH * (t_phase / (2.0 * math.pi)) ** 2) ** (1.0 / 3.0)

    # Bound other apsis safely above dense atmosphere (>= 155 km)
    r_other = 2.0 * a_phase - r1
    r_min_safe = R_EARTH + 155000.0
    if r_other < r_min_safe:
        # Must wait extra target wrap(s) so periapsis stays above atmosphere
        k_wrap = 1
        while r_other < r_min_safe and k_wrap <= 10:
            t_phase = (2.0 * math.pi + (2.0 * math.pi * k_wrap - dphi) / n_rev) / omega2
            a_phase = (MU_EARTH * (t_phase / (2.0 * math.pi)) ** 2) ** (1.0 / 3.0)
            r_other = 2.0 * a_phase - r1
            k_wrap += 1
        r_other = max(r_min_safe, r_other)
        a_phase = 0.5 * (r1 + r_other)
        t_phase = 2.0 * math.pi * math.sqrt(a_phase**3 / MU_EARTH)

    v_circ1 = math.sqrt(MU_EARTH / r1)
    v_phase_at_r1 = math.sqrt(max(0.0, MU_EARTH * (2.0 / r1 - 1.0 / a_phase)))
    # Enter phasing ellipse and circularize back at departure point (two phasing burns)
    dv_phase = 2.0 * abs(v_phase_at_r1 - v_circ1)

    phasing_coast_s = n_rev * t_phase
    total_time_s = phasing_coast_s + t_hoh
    total_dv_mps = dv_phase + hoh["total_dv_mps"]

    return {
        "n_revs": n_rev,
        "phasing_dv_mps": round(dv_phase, 3),
        "hohmann_dv_mps": hoh["total_dv_mps"],
        "total_dv_mps": round(total_dv_mps, 3),
        "phasing_time_s": round(phasing_coast_s, 1),
        "hohmann_time_s": round(t_hoh, 1),
        "total_time_s": round(total_time_s, 1),
        "phasing_alt_other_km": round((r_other - R_EARTH) / 1000.0, 2),
    }
