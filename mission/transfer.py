"""Analytic orbit-raising and plane change for GEO and MEO deliveries (WP2-15).

A GEO or GPS mission is not a rendezvous. There is nothing to catch up with and nothing to
dock to: the vehicle reaches a parking orbit, raises apogee to the target radius, and at
apogee performs one burn that both circularises and removes the inclination it was left with
by launching from a site at non-zero latitude. `MissionEnv` flew every profile as a phasing
rendezvous before this module existed, which for a GEO delivery produced numbers about
physics that was not happening.

Everything here is closed-form two-body. No integration, no simulation, no fitting.

The combined apogee burn
------------------------
The plane change is done AT APOGEE, combined vectorially with the circularisation burn, which
is the standard result and a large saving rather than a detail:

    dv_combined = sqrt(v_apogee^2 + v_circ^2 - 2 * v_apogee * v_circ * cos(di))

Doing the two separately costs `|v_circ - v_apogee| + 2 * v_circ * sin(di/2)`. Measured, from
the Cape (28.5619 deg) to GEO: 1,837.9 m/s combined against 2,994.2 m/s separately, so the
combined burn saves **1,156 m/s** -- about a quarter of the whole mission's post-insertion
budget, not a refinement. `test_the_combined_burn_beats_doing_the_two_separately` asserts the
inequality across the whole range rather than at one point. Apogee is the right place for the
same reason: the velocity being rotated is smallest there.

Measured against the textbook figures for a Cape-to-GEO transfer (perigee kick ~2,440 m/s,
apogee kick ~1,800 m/s), this module gives 2,454.6 and 1,837.9 m/s. The derived GEO altitude
is 35,786.0 km against the published 35,786 km, and GPS comes out at 20,183.6 km against
~20,180 km -- both computed from the required period and MU_M3_S2 rather than quoted.

What this does NOT model, stated rather than implied
----------------------------------------------------
* **Launch window timing for GEO.** `data/results/windows_geo_comm_*.json` still carries one
  nominal due-east opportunity per day at local hour 0, and its `_note` says so. Picking real
  GTO windows needs the perigee-argument and node alignment that puts apogee over the target
  longitude, which is separate work. This module takes the plane error the window data gives
  it and computes the burn honestly; it does not pretend the timing is real.
* **Finite burn losses.** These are impulsive burns. A real apogee kick spends a few tens of
  m/s more.
* **Station keeping, drift orbits, and super-synchronous transfers**, all of which real
  operators use to trade propellant against time.

So the delta-v figures here are a clean lower-bound-flavoured estimate of a real transfer,
and they are labelled `confidence: approximate` wherever they are recorded.

Owner: Yash (lane: mission/).
"""

from __future__ import annotations

import math
from typing import Any

from core.constants import MU_M3_S2, R_E_M

#: Geostationary radius: the circular orbit whose period is one sidereal day (86,164.0905 s).
#: Derived rather than quoted, so it cannot drift from MU_M3_S2.
SIDEREAL_DAY_S = 86164.0905
GEO_RADIUS_M = (MU_M3_S2 * (SIDEREAL_DAY_S / (2.0 * math.pi)) ** 2) ** (1.0 / 3.0)
GEO_ALT_KM = (GEO_RADIUS_M - R_E_M) / 1000.0

#: GPS: a 12-hour sidereal orbit, ~20,180 km altitude, 55 deg inclination.
GPS_PERIOD_S = SIDEREAL_DAY_S / 2.0
GPS_RADIUS_M = (MU_M3_S2 * (GPS_PERIOD_S / (2.0 * math.pi)) ** 2) ** (1.0 / 3.0)
GPS_ALT_KM = (GPS_RADIUS_M - R_E_M) / 1000.0
GPS_INCL_DEG = 55.0


def circular_speed_mps(radius_m: float) -> float:
    return math.sqrt(MU_M3_S2 / radius_m)


def orbital_period_s(radius_m: float) -> float:
    return 2.0 * math.pi * math.sqrt(radius_m**3 / MU_M3_S2)


def combined_plane_change_dv_mps(
    v_from_mps: float, v_to_mps: float, delta_incl_deg: float
) -> float:
    """One burn that changes speed and rotates the velocity vector, by the law of cosines.

    Reduces to |v_to - v_from| at zero inclination change and to the pure rotation
    2*v*sin(di/2) when the two speeds are equal, so the two familiar special cases fall out
    rather than being special-cased.
    """
    di = math.radians(delta_incl_deg)
    return math.sqrt(
        max(0.0, v_from_mps**2 + v_to_mps**2 - 2.0 * v_from_mps * v_to_mps * math.cos(di))
    )


def separate_burns_dv_mps(
    v_from_mps: float, v_to_mps: float, delta_incl_deg: float
) -> float:
    """What it costs to circularise and then rotate, as two burns. Always >= the combined one.

    Here to be compared against, not to be used: it is the quantity the combined burn beats.
    """
    di = math.radians(delta_incl_deg)
    return abs(v_to_mps - v_from_mps) + 2.0 * v_to_mps * math.sin(di / 2.0)


def plan_transfer(
    parking_alt_km: float,
    target_alt_km: float,
    plane_error_deg: float,
) -> dict[str, Any]:
    """Two-impulse orbit raising with the plane change folded into the apogee burn.

    Returns the two burns, the total, the coast time, and the transfer ellipse, so a caller
    can report where the propellant went rather than only how much.
    """
    if target_alt_km <= parking_alt_km:
        raise ValueError(
            f"target altitude {target_alt_km} km is not above the parking orbit "
            f"{parking_alt_km} km; this module raises orbits, it does not lower them"
        )
    r1 = R_E_M + parking_alt_km * 1000.0
    r2 = R_E_M + target_alt_km * 1000.0
    a_transfer = 0.5 * (r1 + r2)

    v_park = circular_speed_mps(r1)
    v_target = circular_speed_mps(r2)
    # Vis-viva on the transfer ellipse at each apsis.
    v_perigee = math.sqrt(MU_M3_S2 * (2.0 / r1 - 1.0 / a_transfer))
    v_apogee = math.sqrt(MU_M3_S2 * (2.0 / r2 - 1.0 / a_transfer))

    dv1 = v_perigee - v_park  # perigee kick into the transfer ellipse, in plane
    dv2 = combined_plane_change_dv_mps(v_apogee, v_target, plane_error_deg)
    coast_s = 0.5 * orbital_period_s(a_transfer)

    return {
        "dv1_mps": round(dv1, 3),
        "dv2_mps": round(dv2, 3),
        "total_dv_mps": round(dv1 + dv2, 3),
        "coast_s": round(coast_s, 1),
        "total_time_s": round(coast_s, 1),
        "plane_change_deg": round(float(plane_error_deg), 4),
        "transfer_sma_km": round(a_transfer / 1000.0, 1),
        "v_park_mps": round(v_park, 2),
        "v_perigee_mps": round(v_perigee, 2),
        "v_apogee_mps": round(v_apogee, 2),
        "v_target_mps": round(v_target, 2),
        "dv2_if_separate_mps": round(
            separate_burns_dv_mps(v_apogee, v_target, plane_error_deg), 3
        ),
        "confidence": "approximate",
        "_note": (
            "Impulsive two-body burns, plane change combined into the apogee burn. No finite-"
            "burn losses, no drift orbit, no super-synchronous option, and for GEO the launch "
            "window timing is still the placeholder in the window data. See mission/transfer.py."
        ),
    }


def target_for_profile(profile: str) -> dict[str, Any]:
    """The orbit each non-rendezvous profile delivers to, derived from its period.

    Altitudes are computed from the required period and MU_M3_S2, not quoted from a table, so
    they cannot disagree with the constants the rest of the project uses.
    """
    if profile == "geo_comm":
        return {
            "alt_km": round(GEO_ALT_KM, 3),
            "incl_deg": 0.0,
            "period_s": SIDEREAL_DAY_S,
            "_note": "Geostationary: one sidereal day, equatorial.",
        }
    if profile == "meo_gps":
        return {
            "alt_km": round(GPS_ALT_KM, 3),
            "incl_deg": GPS_INCL_DEG,
            "period_s": GPS_PERIOD_S,
            "_note": (
                "GPS MEO: half a sidereal day. The 55 deg inclination is the constellation's, "
                "so launching from the Cape at 28.56 deg means raising inclination, not "
                "removing it."
            ),
        }
    raise ValueError(
        f"{profile!r} is not a transfer profile. crew_iss and cargo_iss are rendezvous "
        f"missions and go through rendezvous/phasing.py instead."
    )


def is_transfer_profile(profile: str) -> bool:
    """True for profiles delivered by orbit raising rather than by rendezvous."""
    return profile in ("geo_comm", "meo_gps")
