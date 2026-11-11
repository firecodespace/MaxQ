"""Verifier core: constraints C01–C10 and cost function.

GROUND TRUTH.  This module is read-only to agents, optimizers and env at
runtime (charter addition 1).  Any change bumps verifier_version.

Design rules
------------
* Deterministic: same input → same output, always.
* Honest: constraints whose required inputs are absent are reported with
  `evaluated: false` (and pass=False, value=None, plus a note saying so).
  They are NEVER counted as passed.
* The feasibility flag is exactly the conjunction of the pass flags of the
  constraints with `evaluated: true`. An absent input is not a violation, so it
  does not block feasibility; it is also never presented as a pass.
* Cost: infeasible attempts always cost more than any feasible attempt.

Usage
-----
    from verifier.core import verify, VERIFIER_VERSION

    report = verify(attempt, constraints_profile="crew_default")
    # report is the full dict ready to be written into attempt.json
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any

from verifier.limits import ConstraintsProfile, get_profile

# ---------------------------------------------------------------------------
# Version hash — covers this file + limits.py so any edit bumps the version.


def _hash_sources() -> str:
    """SHA-256 of the two verifier source files, hex-truncated to 12 chars."""
    h = hashlib.sha256()
    for name in ("verifier.core", "verifier.limits"):
        spec = importlib.util.find_spec(name)
        if spec and spec.origin:
            h.update(Path(spec.origin).read_bytes())
    return "ver-" + h.hexdigest()[:12]


VERIFIER_VERSION: str = _hash_sources()

# ---------------------------------------------------------------------------
# Constraint record helpers

_NOT_EVALUATED_NOTE = "not evaluated - required input not available"


def _cr(
    cid: str,
    name: str,
    value: float | None,
    limit: float | None,
    op: str,
    passed: bool,
    margin: float | None,
    note: str | None = None,
    evaluated: bool = True,
) -> dict[str, Any]:
    """Build one constraint_report entry.

    `evaluated` says whether this constraint was actually judged. It is a field rather
    than something a reader infers from `value is None` plus a note prefix, because
    "we checked and it failed" and "we could not check" are different facts and every
    consumer -- the UI, the contract validator, the comparison guard -- needs to tell
    them apart without parsing prose.
    """
    rec: dict[str, Any] = {
        "id": cid,
        "name": name,
        "value": value,
        "limit": limit,
        "op": op,
        "pass": passed,
        "margin": margin,
        "evaluated": evaluated,
    }
    if note:
        rec["note"] = note
    return rec


def _not_evaluated(cid: str, name: str) -> dict[str, Any]:
    """A constraint whose required input is absent: not judged, and never reported as passed."""
    return _cr(
        cid, name, None, None, ">=", False, None, _NOT_EVALUATED_NOTE, evaluated=False
    )


# ---------------------------------------------------------------------------
# Individual constraint evaluators


def _c01_orbit_insertion(attempt: dict, lim: ConstraintsProfile) -> dict[str, Any]:
    """C01 — Perigee ≥ limit and eccentricity ≤ limit."""
    # Look for perigee in the ascent phase's final_orbit if present, else
    # fall back to the top-level phases list.
    perigee_km: float | None = None
    ecc: float | None = None

    # Try the mission_trajectory reference (not always populated at verify time)
    # Primary source: attempt["plan"]["parking_alt_km"] gives the target, but
    # the actual achieved value comes from the trajectory.  For now we look in
    # the attempt's phases for a phase named "ascent" that carries perigee_km.
    for phase in attempt.get("phases", []):
        if phase.get("name") == "ascent":
            perigee_km = phase.get("perigee_km")
            ecc = phase.get("eccentricity")
            break

    # If the env didn't embed it in phases, try the top-level metrics.
    if perigee_km is None:
        perigee_km = attempt.get("metrics", {}).get("perigee_km")
    if ecc is None:
        ecc = attempt.get("metrics", {}).get("eccentricity")

    if perigee_km is None or ecc is None:
        return _not_evaluated("C01", "orbit_insertion")

    # Both sub-conditions must pass; report the binding one (perigee first).
    perigee_pass = perigee_km >= lim.min_perigee_km
    ecc_pass = ecc <= lim.max_eccentricity
    passed = perigee_pass and ecc_pass

    # Report perigee as the primary value; eccentricity in the note.
    margin = perigee_km - lim.min_perigee_km if perigee_pass else perigee_km - lim.min_perigee_km
    note = (
        f"perigee {perigee_km:.1f} km (limit {lim.min_perigee_km} km); "
        f"ecc {ecc:.4f} (limit {lim.max_eccentricity:.3f})"
    )
    return _cr("C01", "orbit_insertion", perigee_km, lim.min_perigee_km, ">=", passed, round(margin, 3), note)


def _c02_max_q(attempt: dict, lim: ConstraintsProfile) -> dict[str, Any]:
    """C02 — Max dynamic pressure ≤ vehicle limit."""
    max_q_pa: float | None = attempt.get("metrics", {}).get("max_q_pa")
    if max_q_pa is None:
        # Try per-phase telemetry embedded in attempt.
        return _not_evaluated("C02", "max_dynamic_pressure")

    # Vehicle limit: prefer rocket's own max_q_limit_kpa if provided in attempt config.
    vehicle_limit_kpa: float | None = (
        attempt.get("config", {}).get("max_q_limit_kpa") or lim.max_q_kpa
    )
    if vehicle_limit_kpa is None:
        # No limit specified — not evaluated.
        return _not_evaluated("C02", "max_dynamic_pressure")

    limit_pa = vehicle_limit_kpa * 1000.0
    max_q_kpa = max_q_pa / 1000.0
    passed = max_q_pa <= limit_pa
    margin = round((limit_pa - max_q_pa) / 1000.0, 3)  # margin in kPa
    return _cr("C02", "max_dynamic_pressure", round(max_q_kpa, 3), vehicle_limit_kpa, "<=", passed, margin)


def _c03_max_accel(attempt: dict, lim: ConstraintsProfile) -> dict[str, Any]:
    """C03 — Max acceleration ≤ profile limit."""
    max_accel_g: float | None = attempt.get("metrics", {}).get("max_accel_g")
    if max_accel_g is None:
        return _not_evaluated("C03", "max_acceleration")

    passed = max_accel_g <= lim.max_accel_g
    margin = round(lim.max_accel_g - max_accel_g, 4)
    return _cr("C03", "max_acceleration", round(max_accel_g, 4), lim.max_accel_g, "<=", passed, margin)


def _c04_launch_window(attempt: dict, lim: ConstraintsProfile) -> dict[str, Any]:
    """C04 — Launch within a real window; plane error ≤ limit."""
    plane_error_deg: float | None = attempt.get("metrics", {}).get("plane_error_deg")
    if plane_error_deg is None:
        # Also accept it at top level config.
        plane_error_deg = attempt.get("config", {}).get("plane_error_deg")
    if plane_error_deg is None:
        return _not_evaluated("C04", "launch_window")

    passed = plane_error_deg <= lim.max_plane_error_deg
    margin = round(lim.max_plane_error_deg - plane_error_deg, 4)
    return _cr("C04", "launch_window", round(plane_error_deg, 4), lim.max_plane_error_deg, "<=", passed, margin)


def _c05_weather(attempt: dict, lim: ConstraintsProfile) -> dict[str, Any]:
    """C05 — Weather go/no-go. NOT evaluated unless weather data is in attempt.config."""
    wx = attempt.get("config", {}).get("weather")
    if wx is None:
        return _not_evaluated("C05", "weather_go_nogo")

    wind_kts: float | None = wx.get("wind_kts")
    visibility_km: float | None = wx.get("visibility_km")
    wx_pass = True
    details = []

    if wind_kts is not None:
        w_pass = wind_kts <= lim.max_wind_kts
        wx_pass = wx_pass and w_pass
        details.append(f"wind {wind_kts:.1f} kts (limit {lim.max_wind_kts})")

    if visibility_km is not None:
        v_pass = visibility_km >= lim.max_visibility_km
        wx_pass = wx_pass and v_pass
        details.append(f"visibility {visibility_km:.1f} km (min {lim.max_visibility_km})")

    if not details:
        return _not_evaluated("C05", "weather_go_nogo")

    # Report wind_kts as the primary value.
    value = wind_kts if wind_kts is not None else visibility_km
    return _cr("C05", "weather_go_nogo", value, lim.max_wind_kts, "<=", wx_pass, None,
               "; ".join(details))


def _c06_conjunction(attempt: dict, _lim: ConstraintsProfile) -> dict[str, Any]:
    """C06 — Conjunction risk: no window may have status 'no-go'."""
    risk_status: str | None = attempt.get("config", {}).get("risk_status")
    if risk_status is None:
        return _not_evaluated("C06", "conjunction_risk")

    passed = risk_status != "no-go"
    # Encode as numeric: 0=go, 1=review, 2=no-go for margin sign
    status_rank = {"go": 0, "review": 1, "no-go": 2}.get(risk_status, 1)
    margin = round(1.0 - status_rank / 2.0, 3)  # positive if go, negative if no-go
    return _cr("C06", "conjunction_risk", float(status_rank), 2.0, "<=", passed, margin,
               f"risk_status={risk_status!r}")


def _c07_propellant_reserve(attempt: dict, lim: ConstraintsProfile) -> dict[str, Any]:
    """C07 — Propellant reserve ≥ 2 % ascent, ≥ 5 % orbital."""
    metrics = attempt.get("metrics", {})
    prop_used_kg: float | None = metrics.get("prop_used_kg")
    prop_total_kg: float | None = attempt.get("config", {}).get("prop_total_kg")
    ascent_reserve_frac: float | None = metrics.get("ascent_reserve_frac")
    orbital_reserve_frac: float | None = metrics.get("orbital_reserve_frac")

    # Try computing reserve from prop_used / prop_total if fracs not given.
    if ascent_reserve_frac is None and prop_used_kg is not None and prop_total_kg:
        ascent_reserve_frac = max(0.0, 1.0 - prop_used_kg / prop_total_kg)

    if ascent_reserve_frac is None:
        return _not_evaluated("C07", "propellant_reserve")

    asc_pass = ascent_reserve_frac >= lim.min_ascent_reserve_frac
    orb_pass = (orbital_reserve_frac >= lim.min_orbital_reserve_frac
                if orbital_reserve_frac is not None else True)  # only fail if data present
    passed = asc_pass and orb_pass

    margin = round(ascent_reserve_frac - lim.min_ascent_reserve_frac, 4)
    note = (f"ascent_reserve {ascent_reserve_frac:.3%} (min {lim.min_ascent_reserve_frac:.0%})"
            + (f"; orbital_reserve {orbital_reserve_frac:.3%} (min {lim.min_orbital_reserve_frac:.0%})"
               if orbital_reserve_frac is not None else ""))
    return _cr("C07", "propellant_reserve",
               round(ascent_reserve_frac, 4), lim.min_ascent_reserve_frac,
               ">=", passed, margin, note)


def _c08_mission_time(attempt: dict, lim: ConstraintsProfile) -> dict[str, Any]:
    """C08 — Total mission time ≤ profile limit."""
    time_to_dock_s: float | None = attempt.get("metrics", {}).get("time_to_dock_s")
    if time_to_dock_s is None:
        return _not_evaluated("C08", "mission_time_budget")

    passed = time_to_dock_s <= lim.max_mission_time_s
    margin = round(lim.max_mission_time_s - time_to_dock_s, 1)
    return _cr("C08", "mission_time_budget",
               round(time_to_dock_s, 1), lim.max_mission_time_s,
               "<=", passed, margin)


def _c09_approach_corridor(attempt: dict, lim: ConstraintsProfile) -> dict[str, Any]:
    """C09 — Approach corridor / keep-out. NOT evaluated without approach telemetry."""
    approach_data = attempt.get("config", {}).get("approach_telemetry")
    if approach_data is None:
        return _not_evaluated("C09", "approach_corridor")

    max_cone_error_deg: float | None = approach_data.get("max_cone_error_deg")
    if max_cone_error_deg is None:
        return _not_evaluated("C09", "approach_corridor")

    passed = max_cone_error_deg <= lim.approach_cone_half_angle_deg
    margin = round(lim.approach_cone_half_angle_deg - max_cone_error_deg, 4)
    return _cr("C09", "approach_corridor",
               round(max_cone_error_deg, 4), lim.approach_cone_half_angle_deg,
               "<=", passed, margin)


def _c10_final_approach(attempt: dict, lim: ConstraintsProfile) -> dict[str, Any]:
    """C10 — Final approach: closing speed ≤ 0.1 m/s AND lateral offset ≤ 0.1 m."""
    approach_data = attempt.get("config", {}).get("approach_telemetry")
    if approach_data is None:
        return _not_evaluated("C10", "final_approach")

    closing_speed_mps: float | None = approach_data.get("closing_speed_mps")
    lateral_offset_m: float | None = approach_data.get("lateral_offset_m")

    if closing_speed_mps is None or lateral_offset_m is None:
        return _not_evaluated("C10", "final_approach")

    sp_pass = closing_speed_mps <= lim.max_closing_speed_mps
    lat_pass = lateral_offset_m <= lim.max_lateral_offset_m
    passed = sp_pass and lat_pass
    margin = round(min(lim.max_closing_speed_mps - closing_speed_mps,
                       lim.max_lateral_offset_m - lateral_offset_m), 4)
    note = (f"closing_speed {closing_speed_mps:.3f} m/s (limit {lim.max_closing_speed_mps}); "
            f"lateral_offset {lateral_offset_m:.3f} m (limit {lim.max_lateral_offset_m})")
    return _cr("C10", "final_approach",
               round(closing_speed_mps, 4), lim.max_closing_speed_mps,
               "<=", passed, margin, note)


# Ordered list of evaluators — order is stable for the version hash.
_EVALUATORS = [
    _c01_orbit_insertion,
    _c02_max_q,
    _c03_max_accel,
    _c04_launch_window,
    _c05_weather,
    _c06_conjunction,
    _c07_propellant_reserve,
    _c08_mission_time,
    _c09_approach_corridor,
    _c10_final_approach,
]

# ---------------------------------------------------------------------------
# Cost function (HANDOFF_PHASE2 §5.2)

INFEASIBLE_BASE = 1000.0  # penalty per violated constraint
INFEASIBLE_SCALE = 1000.0


def _compute_cost(
    attempt: dict,
    constraint_report: list[dict],
    feasible: bool,
) -> float:
    """Compute cost per §5.2.  Infeasible always beats feasible.

    cost = Σ_violated (1000 + 1000 · normalized_violation)      # infeasible
         + w_fuel · (prop_used_kg / fuel_ref_kg)
         + w_time · (time_to_dock_s / time_ref_s)               # feasible only
    """
    obj = attempt.get("objective", {})
    w_fuel: float = float(obj.get("w_fuel", 1.0))
    w_time: float = float(obj.get("w_time", 0.5))
    fuel_ref_kg: float = float(obj.get("fuel_ref_kg", 1.0)) or 1.0
    time_ref_s: float = float(obj.get("time_ref_s", 1.0)) or 1.0

    metrics = attempt.get("metrics", {})
    prop_used_kg: float = float(metrics.get("prop_used_kg") or 0.0)
    time_to_dock_s: float | None = metrics.get("time_to_dock_s")

    # Penalty for each violated constraint.
    penalty = 0.0
    for cr in constraint_report:
        if not cr["pass"]:
            # Normalize the violation magnitude where possible.
            value = cr.get("value")
            limit = cr.get("limit")
            if value is not None and limit is not None and limit != 0:
                normalized = abs(float(value) - float(limit)) / abs(float(limit))
            else:
                normalized = 0.0
            penalty += INFEASIBLE_BASE + INFEASIBLE_SCALE * normalized

    if not feasible:
        return round(penalty, 6)

    # Feasible: fuel + time cost (penalty is 0 when feasible).
    cost = w_fuel * (prop_used_kg / fuel_ref_kg)
    if time_to_dock_s is not None:
        cost += w_time * (time_to_dock_s / time_ref_s)
    return round(cost, 6)


# ---------------------------------------------------------------------------
# Public API


def verify(
    attempt: dict[str, Any],
    constraints_profile: str = "crew_default",
) -> dict[str, Any]:
    """Evaluate all constraints and compute cost for *attempt*.

    Parameters
    ----------
    attempt:
        A dict conforming to the attempt contract (or a partial dict during
        construction — unknown keys are ignored).
    constraints_profile:
        Named profile from verifier.limits.

    Returns
    -------
    dict with keys:
        feasible          bool
        cost              float
        constraint_report list of constraint dicts
        verifier_version  str (VERIFIER_VERSION)
    """
    lim = get_profile(constraints_profile)
    report: list[dict[str, Any]] = []

    for evaluator in _EVALUATORS:
        cr = evaluator(attempt, lim)
        report.append(cr)

    # Feasibility: ALL *evaluated* constraints must pass.
    # An unevaluated constraint does not block feasibility — its input is absent, which
    # is not the same as the constraint being violated. It is still never reported as
    # passed. An evaluated constraint that fails DOES block feasibility.
    feasible = all(cr["pass"] for cr in report if cr["evaluated"])

    cost = _compute_cost(attempt, report, feasible)

    return {
        "feasible": feasible,
        "cost": cost,
        "constraint_report": report,
        "verifier_version": VERIFIER_VERSION,
    }
