"""SGP4 propagation helpers (TEME output) used by windows, screening and the exporter.

Owner: Yash (lane: catalog/).
"""

from __future__ import annotations

import numpy as np
from sgp4.api import WGS72, Satrec, jday

from catalog.omm import satrec_from_row
from core.frames import teme_km_to_geodetic


class PropagationError(RuntimeError):
    pass


def satrec_from_tle(tle1: str, tle2: str) -> Satrec:
    return Satrec.twoline2rv(tle1, tle2, WGS72)


def propagate_teme(sat: Satrec, t_unix) -> tuple[np.ndarray, np.ndarray]:
    """Propagate to UTC unix seconds ``t_unix`` (scalar or 1-D). Returns r (km), v (km/s), TEME, shape (N,3).

    Raises PropagationError naming the SGP4 error code instead of returning NaNs.
    """
    t = np.atleast_1d(np.asarray(t_unix, dtype=float))
    jd0 = 2440587.5 + np.floor(t / 86400.0)
    fr = (t - np.floor(t / 86400.0) * 86400.0) / 86400.0
    err, r, v = sat.sgp4_array(jd0, fr)
    if np.any(err != 0):
        bad = int(np.flatnonzero(err)[0])
        raise PropagationError(f"SGP4 error code {int(err[bad])} at t_unix={t[bad]} (norad {sat.satnum})")
    return r, v


def sat_epoch_unix(sat: Satrec) -> float:
    """TLE epoch as UTC unix seconds."""
    return (sat.jdsatepoch - 2440587.5) * 86400.0 + sat.jdsatepochF * 86400.0


def geodetic_track(sat: Satrec, t_unix) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(lat_deg, lon_deg, alt_m) ground track of ``sat`` at ``t_unix``."""
    t = np.atleast_1d(np.asarray(t_unix, dtype=float))
    r, _ = propagate_teme(sat, t)
    return teme_km_to_geodetic(r, t)


__all__ = [
    "PropagationError",
    "geodetic_track",
    "jday",
    "propagate_teme",
    "sat_epoch_unix",
    "satrec_from_row",
    "satrec_from_tle",
]
