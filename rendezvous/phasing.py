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
