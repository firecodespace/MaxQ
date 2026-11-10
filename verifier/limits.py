"""Constraint limits by constraints_profile.

These are the ONLY numbers that determine pass/fail.  Any change here bumps
verifier_version automatically (the hash covers this module's source).

Sources: HANDOFF_PHASE2 §5.1 (illustrative defaults).  Where a tighter or
looser real value exists it would be cited here; until then every limit carries
the label "illustrative".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Defaults


@dataclass(frozen=True)
class ConstraintsProfile:
    """All limit values for one constraints profile."""

    # C01 — Orbit insertion
    min_perigee_km: float = 160.0          # illustrative
    max_eccentricity: float = 0.05         # illustrative (small)

    # C02 — Max dynamic pressure (kPa)
    max_q_kpa: float | None = None         # per-vehicle from rocket; None = use rocket field

    # C03 — Max acceleration (g)
    max_accel_g: float = 6.0              # cargo default; crew = 4.0  (illustrative)

    # C04 — Launch window plane error (degrees)
    max_plane_error_deg: float = 3.0       # illustrative

    # C05 — Weather (not evaluated unless weather data is provided)
    # Thresholds here are illustrative; real data needed to evaluate.
    max_wind_kts: float = 30.0
    max_visibility_km: float = 1.0         # minimum visibility (must be >= this)
    # C05 is never evaluated unless a weather dict is supplied.

    # C06 — Conjunction risk: status must not be "no-go"
    # (checked against risk data if provided; skipped otherwise)

    # C07 — Propellant reserve fractions
    min_ascent_reserve_frac: float = 0.02  # ≥ 2 %
    min_orbital_reserve_frac: float = 0.05 # ≥ 5 %

    # C08 — Mission time budget (seconds)
    max_mission_time_s: float = 3 * 24 * 3600  # 3 days illustrative; override per profile

    # C09, C10 — Approach / final approach
    # Not evaluated without approach telemetry; listed for completeness.
    approach_cone_half_angle_deg: float = 15.0  # illustrative
    max_closing_speed_mps: float = 0.1           # illustrative
    max_lateral_offset_m: float = 0.1            # illustrative

    # Extra fields for profile-specific overrides
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Named profiles

_CREW_DEFAULT = ConstraintsProfile(
    max_accel_g=4.0,            # crew: 4 g  (illustrative)
    max_plane_error_deg=1.0,    # crew: tighter window (illustrative)
    max_mission_time_s=3 * 24 * 3600,
)

_CARGO_DEFAULT = ConstraintsProfile(
    max_accel_g=6.0,
    max_plane_error_deg=3.0,
    max_mission_time_s=4 * 24 * 3600,
)

_PROFILES: dict[str, ConstraintsProfile] = {
    "crew_default": _CREW_DEFAULT,
    "cargo_default": _CARGO_DEFAULT,
    # Aliases
    "crew_iss": _CREW_DEFAULT,
    "cargo_iss": _CARGO_DEFAULT,
}


def get_profile(name: str) -> ConstraintsProfile:
    """Return the named constraints profile, raising KeyError if unknown."""
    if name not in _PROFILES:
        raise KeyError(
            f"Unknown constraints_profile {name!r}. "
            f"Known: {sorted(_PROFILES)}"
        )
    return _PROFILES[name]

