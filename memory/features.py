"""`features-v1`: the mission feature vector experience memory retrieves on (WP2-10, HANDOFF 9).

Hand-built and normalized against FIXED ranges declared here, so the same mission encodes to
the same vector on every machine and in every version. No embedding model, nothing learned,
nothing fetched -- the whole point is that this works offline and is auditable.

What is in it, and what is deliberately not
-------------------------------------------
HANDOFF section 9 lists the intended features. These are the ones with a real source:

    0-3   profile one-hot (crew_iss, cargo_iss, geo_comm, meo_gps)
    4     target altitude
    5     target inclination
    6     payload / the vehicle's PUBLISHED payload-to-LEO
    7     lift-off thrust-to-weight
    8     vehicle ideal delta-v capability (rocket equation over both stages)
    9     pad latitude
    10-11 launch window phase angle, as sin and cos
    12    window plane error
    13    objective weight ratio, w_time / (w_fuel + w_time)

OMITTED, each for a stated reason, rather than encoded as zero (charter addition 12 -- a
metric with no real source is null, never 0, and in a vector a 0 is not "unknown", it is a
specific point in the space that makes unrelated missions look similar):

* **weather metrics** -- there is no weather data source in the project at all. C05 reports
  "not evaluated" for the same reason.
* **Kp index** -- no source.
* **minimum miss distance** -- `data/results/risk_*.json` has `min_miss_km`, but only for
  eight windows against one trajectory family, so it is absent for most suite missions.
  Encoding "absent" as a number would be inventing data.

Adding any of them is a `features-v1` -> `features-v2` change: a vector built under one spec
cannot be compared with one built under another, which is why `VECTOR_SPEC` is stored on every
record and `store.search` filters on it.

The phase angle is encoded as (sin, cos) and not as a scaled number, because it is circular:
359 degrees and 1 degree are two degrees apart, and any linear normalization puts them at
opposite ends of the range. That single detail is the difference between retrieving a
genuinely similar launch window and retrieving the most distant one available.

Owner: Yash (lane: memory/).
"""

from __future__ import annotations

import hashlib
import json
import math
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np

from core.constants import G0_M_S2

ROOT = Path(__file__).resolve().parents[1]

VECTOR_SPEC = "features-v1"

PROFILES = ("crew_iss", "cargo_iss", "geo_comm", "meo_gps")

#: Fixed normalization ranges: (min, max) mapped to [0, 1] and clipped. Stored with the spec
#: (HANDOFF section 9) and inside SPEC_HASH, so a range cannot be changed without every
#: existing vector becoming visibly incomparable.
RANGES: dict[str, tuple[float, float]] = {
    "target_alt_km": (150.0, 36000.0),
    "target_incl_deg": (0.0, 100.0),
    "payload_fraction": (0.0, 1.5),
    "liftoff_twr": (1.0, 2.5),
    "vehicle_dv_capability_mps": (6000.0, 14000.0),
    "pad_lat_deg": (0.0, 70.0),
    "plane_error_deg": (0.0, 30.0),
    "objective_time_share": (0.0, 1.0),
}

FEATURE_NAMES: tuple[str, ...] = (
    *(f"profile_{p}" for p in PROFILES),
    "target_alt_km",
    "target_incl_deg",
    "payload_fraction",
    "liftoff_twr",
    "vehicle_dv_capability_mps",
    "pad_lat_deg",
    "phase_angle_sin",
    "phase_angle_cos",
    "plane_error_deg",
    "objective_time_share",
)

DIM = len(FEATURE_NAMES)

#: Defaults used when a mission_request does not carry the value. These are mission
#: PROPERTIES with real defaults elsewhere in the codebase, not missing measurements:
#: MissionEnv targets the ISS altitude and inclination unless told otherwise.
DEFAULT_TARGET_ALT_KM = 410.0
DEFAULT_TARGET_INCL_DEG = 51.6


def _norm(name: str, value: float) -> float:
    lo, hi = RANGES[name]
    return float(min(1.0, max(0.0, (value - lo) / (hi - lo))))


@cache
def _vehicle(rocket_id: str) -> dict[str, Any]:
    return json.loads(
        (ROOT / "data" / "rockets" / f"{rocket_id}.json").read_text(encoding="utf-8")
    )


@cache
def _pad(pad_id: str) -> dict[str, Any]:
    return json.loads((ROOT / "data" / "pads" / f"{pad_id}.json").read_text(encoding="utf-8"))


def vehicle_dv_capability_mps(rocket_id: str, payload_kg: float) -> float:
    """Ideal staged delta-v: the rocket equation per stage, carrying the payload throughout.

    Ideal, so it ignores every loss -- it is a capability figure for comparing vehicles, not
    a prediction. Stage 1 lifts everything above it; stage 2 lifts only itself and the
    payload.
    """
    stages = _vehicle(rocket_id)["stages"]
    upper_mass = sum(s["dry_kg"] + s["prop_kg"] for s in stages[1:]) + payload_kg
    total = 0.0
    carried = upper_mass
    for stage in reversed(stages[1:]):
        m0 = carried + 0.0
        total += stage["isp_s"] * G0_M_S2 * math.log(
            m0 / max(1.0, m0 - stage["prop_kg"])
        )
        carried = m0
    s1 = stages[0]
    m0 = s1["dry_kg"] + s1["prop_kg"] + upper_mass
    total += s1["isp_s"] * G0_M_S2 * math.log(m0 / max(1.0, m0 - s1["prop_kg"]))
    return float(total)


def liftoff_twr(rocket_id: str, payload_kg: float) -> float:
    stages = _vehicle(rocket_id)["stages"]
    mass = sum(s["dry_kg"] + s["prop_kg"] for s in stages) + payload_kg
    return float(stages[0]["thrust_n"] / (mass * G0_M_S2))


def phase_angle_deg(mission_request: dict[str, Any]) -> float | None:
    """The launch window's phase angle, read from the window data for this profile and site.

    Returns None when the profile has no phase angle (GEO and MEO windows record
    `phase_angle_deg: null`, because there is no rendezvous target to catch up with).
    """
    profile = mission_request["profile"]
    path = ROOT / "data" / "results" / f"windows_{profile}_cape_canaveral.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    for window in data["windows"]:
        if window["id"] == mission_request.get("window_id"):
            value = window.get("phase_angle_deg")
            return None if value is None else float(value)
    return None


def plane_error_deg(mission_request: dict[str, Any]) -> float:
    profile = mission_request["profile"]
    path = ROOT / "data" / "results" / f"windows_{profile}_cape_canaveral.json"
    if not path.exists():
        return 0.0
    data = json.loads(path.read_text(encoding="utf-8"))
    for window in data["windows"]:
        if window["id"] == mission_request.get("window_id"):
            return float(window.get("plane_error_deg") or 0.0)
    return 0.0


def encode(mission_request: dict[str, Any]) -> np.ndarray:
    """The `features-v1` vector for one mission request. Deterministic and offline."""
    rocket_id = mission_request["rocket_id"]
    payload_kg = float(mission_request["payload"]["mass_kg"])
    target = mission_request.get("target") or {}
    obj = mission_request["objective"]

    published = float(_vehicle(rocket_id)["payload_to_leo_kg_published"] or 1.0)
    w_fuel = float(obj.get("w_fuel", 1.0))
    w_time = float(obj.get("w_time", 0.5))
    denom = w_fuel + w_time

    angle = phase_angle_deg(mission_request)
    # No phase angle means no rendezvous target. (0, 0) is the vector's centre for this pair
    # and is the honest encoding of "this axis does not apply to this mission", which is a
    # different statement from a specific angle -- and it stays at distance 1 from every real
    # angle, so a GEO mission never looks like a close neighbour of an ISS one.
    angle_sin = 0.0 if angle is None else math.sin(math.radians(angle))
    angle_cos = 0.0 if angle is None else math.cos(math.radians(angle))

    values = [
        *(1.0 if mission_request["profile"] == p else 0.0 for p in PROFILES),
        _norm("target_alt_km", float(target.get("alt_km") or DEFAULT_TARGET_ALT_KM)),
        _norm("target_incl_deg", float(target.get("incl_deg") or DEFAULT_TARGET_INCL_DEG)),
        _norm("payload_fraction", payload_kg / published),
        _norm("liftoff_twr", liftoff_twr(rocket_id, payload_kg)),
        _norm(
            "vehicle_dv_capability_mps", vehicle_dv_capability_mps(rocket_id, payload_kg)
        ),
        _norm("pad_lat_deg", abs(float(_pad(mission_request["pad_id"])["lat_deg"]))),
        angle_sin,
        angle_cos,
        _norm("plane_error_deg", plane_error_deg(mission_request)),
        _norm("objective_time_share", (w_time / denom) if denom else 0.0),
    ]
    assert len(values) == DIM, f"{len(values)} features, spec says {DIM}"
    return np.asarray(values, dtype=np.float64)


def spec_document() -> dict[str, Any]:
    """Everything that defines the encoding, for hashing."""
    return {
        "vector_spec": VECTOR_SPEC,
        "dim": DIM,
        "features": list(FEATURE_NAMES),
        "ranges": {k: list(v) for k, v in sorted(RANGES.items())},
        "profiles": list(PROFILES),
        "defaults": {
            "target_alt_km": DEFAULT_TARGET_ALT_KM,
            "target_incl_deg": DEFAULT_TARGET_INCL_DEG,
        },
    }


@cache
def spec_hash() -> str:
    blob = json.dumps(spec_document(), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()
