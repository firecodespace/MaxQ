"""Mission profiles (HANDOFF section 3). Parameters here are assumptions, not data.

mode:
  "iss"     real plane-crossing windows against the live ISS TLE, with phasing/rendezvous estimates
  "plane"   real plane-crossing windows against a catalog reference satellite's plane (no rendezvous)
  "nominal" PLACEHOLDER: one nominal due-east opportunity per day (GEO transfer timing is not modelled)

Owner: Yash (lane: mission/).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    id: str
    mode: str
    description: str
    target_norad: int | None = None  # None -> pick a reference object from the catalog (see windows.pick_reference)
    reference_group: str | None = None  # catalog group used when target_norad is None
    reference_incl_deg: float | None = None
    # Ascent assumptions used for phasing (Shiva's simulator can replace them later):
    ascent_s: float = 520.0  # liftoff -> insertion (matches the dummy trajectory's insertion event)
    ascent_arc_deg: float = 17.0  # in-plane arc travelled between liftoff and insertion (~1,900 km downrange)
    park_alt_m: float = 200_000.0
    fast_max_revs: int = 2  # a "fast" (~3 h) rendezvous allows at most this many phasing revolutions
    slow_min_revs: int = 16  # the low-propellant approach spreads phasing over at least this many revolutions
    approach_h: float = 0.25
    nominal_local_hour: float = 0.0  # mode "nominal": local clock hour of the daily opportunity


PROFILES: dict[str, Profile] = {
    p.id: p
    for p in (
        Profile(
            "crew_iss",
            "iss",
            "Crew to ISS: instantaneous window, strictest weather (not modelled), fast rendezvous preferred",
            target_norad=25544,
            fast_max_revs=2,
        ),
        Profile(
            "cargo_iss",
            "iss",
            "Cargo to ISS: same orbit, looser weather (not modelled), slower rendezvous allowed",
            target_norad=25544,
            fast_max_revs=2,
        ),
        Profile(
            "meo_gps",
            "plane",
            "GPS-like MEO (~55 deg): windows against the plane of a catalog gps-ops satellite",
            reference_group="gps-ops",
            reference_incl_deg=55.0,
        ),
        Profile(
            "geo_comm",
            "nominal",
            "GEO communications: nominal daily due-east opportunity (placeholder; no transfer-timing model)",
            reference_group="geo",
            reference_incl_deg=0.0,
        ),
    )
}


def get_profile(pid: str) -> Profile:
    try:
        return PROFILES[pid]
    except KeyError:
        raise KeyError(f"unknown profile {pid!r}; known: {sorted(PROFILES)}") from None
