"""Analytic lower bounds for the benchmark (WP2-12, HANDOFF section 5.3).

A floor that cannot be beaten, computed from orbital mechanics and the mission's own
constraints -- not from any simulation. Its job is to sit underneath every result and say
"nothing can do better than this", so a best-known cost can be read against something that
does not depend on how hard we searched.

Every assumption here is the one most favourable to the vehicle, which is what makes the
result a bound rather than an estimate:

* **Ascent delta-v** is the ideal lossless figure: circular velocity at the parking altitude
  minus the launch site's eastward rotation speed. No gravity loss, no drag loss, no steering
  loss. A real ascent spends 1.5-2 km/s more than this.
* **Propellant** comes from the rocket equation applied to the PAYLOAD ALONE at the vehicle's
  best stage Isp -- as though the structure were massless and the whole mission flew on the
  most efficient engine. Nothing can beat it.
* **Phasing and approach delta-v** are bounded below by zero. A phase angle that happens to
  be already closed costs nothing, and `solve_phasing_orbit`'s cost does tend to zero as the
  rev count rises toward the natural synodic drift, so zero is the correct floor even though
  no suite mission attains it.
* **Ascent time** is bounded by the acceleration limit the mission flies under: reaching the
  ascent delta-v at the constraint's maximum acceleration takes at least dv / (g_max * g0).
  This is where a crew mission's 4 g limit shows up as a real floor on time.
* **Transfer time** is the Hohmann half-period from the parking to the target altitude, which
  is fixed by the two radii and nothing else.

The bound is therefore LOOSE -- in particular the propellant floor ignores stage dry mass, so
it is far below anything flyable. It is honest about being a floor, not a target. A result
below it would mean a bug in the simulator or the verifier, which is the other thing it is
for: `test_no_measured_cost_is_below_the_analytic_floor` uses it as an assertion.

Owner: Yash (lane: benchmarks/). Read-only to any agent.
"""

from __future__ import annotations

import json
import math
from functools import cache
from pathlib import Path
from typing import Any

from core.constants import G0_M_S2, MU_M3_S2, OMEGA_E_RAD_S, R_E_M
from rendezvous.phasing import hohmann_transfer
from verifier.limits import get_profile

ROOT = Path(__file__).resolve().parents[1]

#: The parking altitude the ascent simulator targets, km. Matches
#: `simulate_ascent(target_parking_alt_m=200_000.0)`.
PARKING_ALT_KM = 200.0

#: ISS altitude used for the transfer bound, km. The suite's target.
TARGET_ALT_KM = 410.0


@cache
def _pad(pad_id: str) -> dict[str, Any]:
    return json.loads((ROOT / "data" / "pads" / f"{pad_id}.json").read_text(encoding="utf-8"))


@cache
def _vehicle(rocket_id: str) -> dict[str, Any]:
    return json.loads(
        (ROOT / "data" / "rockets" / f"{rocket_id}.json").read_text(encoding="utf-8")
    )


def ascent_dv_floor_mps(pad_lat_deg: float, parking_alt_km: float = PARKING_ALT_KM) -> float:
    """Ideal lossless delta-v to a circular parking orbit, launching due east.

    Circular velocity minus the free eastward velocity the launch site already has from
    Earth's rotation. A higher-latitude pad gets less of that help, which is why
    `mars_lp0a` at 37.8 N carries a bigger floor than the Cape pads at 28.5 N.
    """
    r_park = R_E_M + parking_alt_km * 1000.0
    v_circ = math.sqrt(MU_M3_S2 / r_park)
    v_site_east = OMEGA_E_RAD_S * R_E_M * math.cos(math.radians(pad_lat_deg))
    return max(0.0, v_circ - v_site_east)


def transfer_dv_floor_mps(
    parking_alt_km: float = PARKING_ALT_KM, target_alt_km: float = TARGET_ALT_KM
) -> float:
    """Two-impulse Hohmann delta-v from the parking orbit to the target altitude.

    A co-planar Hohmann is the cheapest two-impulse transfer between two circular orbits, so
    this is a true floor for the climb. Phasing is bounded separately, by zero.
    """
    return float(hohmann_transfer(parking_alt_km, target_alt_km)["total_dv_mps"])


def transfer_time_floor_s(
    parking_alt_km: float = PARKING_ALT_KM, target_alt_km: float = TARGET_ALT_KM
) -> float:
    """Hohmann half-period: fixed by the two radii, unbeatable for a two-impulse transfer."""
    return float(hohmann_transfer(parking_alt_km, target_alt_km)["transfer_time_s"])


def propellant_floor_kg(rocket_id: str, payload_kg: float, dv_mps: float) -> float:
    """Rocket equation on the payload alone, at the vehicle's best Isp.

    Deliberately absurd in the vehicle's favour: no stage structure, no fairing, one perfect
    engine for the whole mission. Anything real needs more.
    """
    isp_best = max(float(s["isp_s"]) for s in _vehicle(rocket_id)["stages"])
    return float(payload_kg) * (math.exp(dv_mps / (isp_best * G0_M_S2)) - 1.0)


def for_mission(mission_request: dict[str, Any]) -> dict[str, Any]:
    """The full bound for one suite mission: delta-v, propellant, time and cost floors."""
    pad = _pad(mission_request["pad_id"])
    rocket_id = mission_request["rocket_id"]
    payload_kg = float(mission_request["payload"]["mass_kg"])
    limits = get_profile(mission_request["constraints_profile"])
    obj = mission_request["objective"]

    dv_ascent = ascent_dv_floor_mps(float(pad["lat_deg"]))
    dv_transfer = transfer_dv_floor_mps()
    dv_total = dv_ascent + dv_transfer  # phasing and approach floor at zero

    prop_ascent = propellant_floor_kg(rocket_id, payload_kg, dv_ascent)
    prop_on_orbit = propellant_floor_kg(rocket_id, payload_kg, dv_transfer)

    # Ascent cannot be faster than reaching its delta-v at the acceleration limit.
    t_ascent = dv_ascent / (float(limits.max_accel_g) * G0_M_S2)
    t_total = t_ascent + transfer_time_floor_s()  # phasing and approach floor at zero

    cost = (
        float(obj.get("w_fuel", 1.0)) * (prop_ascent / float(obj["fuel_ref_kg"]))
        + float(obj.get("w_prop_orbit", 1.0))
        * (prop_on_orbit / float(obj.get("prop_orbit_ref_kg") or obj["fuel_ref_kg"]))
        + float(obj.get("w_time", 0.5)) * (t_total / float(obj["time_ref_s"]))
    )

    return {
        "dv_mps": round(dv_total, 3),
        "prop_kg": round(prop_ascent + prop_on_orbit, 3),
        "cost": round(cost, 6),
        "_breakdown": {
            "dv_ascent_mps": round(dv_ascent, 3),
            "dv_transfer_mps": round(dv_transfer, 3),
            "dv_phasing_mps": 0.0,
            "prop_ascent_kg": round(prop_ascent, 3),
            "prop_on_orbit_kg": round(prop_on_orbit, 3),
            "t_ascent_s": round(t_ascent, 1),
            "t_transfer_s": round(transfer_time_floor_s(), 1),
            "t_phasing_s": 0.0,
            "max_accel_g": float(limits.max_accel_g),
            "pad_lat_deg": float(pad["lat_deg"]),
            "isp_best_s": max(float(s["isp_s"]) for s in _vehicle(rocket_id)["stages"]),
            "_note": (
                "A floor, not a target. Lossless ascent, massless structure, best single Isp, "
                "zero phasing cost. No flyable mission comes near it; nothing may come in "
                "under it."
            ),
        },
    }
