"""WP-02: launch windows = times when the launch site passes through the target's orbital plane.

Method (robust, no closed-form assumptions about precession):
  1. propagate the target with SGP4 (TEME), h = r x v at each time;
  2. site position in TEME from GMST;
  3. the plane passes over the site when  h_hat . r_site_hat = 0  -> scan on a 60 s grid, bracket sign changes,
     refine with brentq;
  4. at each root: inertial launch azimuth from the in-plane direction h x r_site, ascending/descending from its
     northward component, and the in-plane phase angle to the target at insertion (assumed ascent time/arc).

Run:  python -m mission.windows [--profile all|crew_iss|cargo_iss|meo_gps|geo_comm] [--site cape_canaveral]
                                [--days 7] [--start 2026-11-10T00:00:00Z] [--offline]

Owner: Yash (lane: mission/).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy.optimize import brentq

from catalog.fetch import OUT_PATH as CATALOG_PATH
from catalog.fetch import build_catalog, load_catalog, sources_from_sample
from catalog.propagate import propagate_teme, sat_epoch_unix, satrec_from_tle
from contracts.validate import validate
from core.frames import ecef_to_teme, site_teme_m
from mission.phasing import (
    inertial_azimuth_deg,
    rendezvous_estimates,
    semi_major_axis_from_mean_motion,
)
from mission.profiles import PROFILES, Profile, get_profile
from mission.sites import SITES, Site, get_site

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "data" / "results"
SCAN_STEP_S = 60.0
MAX_TLE_AGE_DAYS = 30.0  # SGP4 error grows with age; beyond this we refuse rather than return confident nonsense


def _unit(v):
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def _utc(t_unix: float) -> str:
    return datetime.fromtimestamp(int(round(t_unix)), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Crossing:
    t_unix: float
    ascending: bool
    azimuth_deg: float  # inertial, clockwise from north
    plane_error_deg: float  # |angle between the site and the plane| at t_unix (solver residual)
    u_site_deg: float  # argument of latitude of the site point in the plane
    node_hat: np.ndarray  # in-plane unit vector toward the ascending node
    perp_hat: np.ndarray  # in-plane unit vector at u = 90 deg


def _plane_function(sat, site: Site):
    def f(t: float) -> float:
        r, v = propagate_teme(sat, t)
        h = _unit(np.cross(r[0], v[0]))
        rs = _unit(site_teme_m(site.lat_deg, site.lon_deg, site.alt_m, t))
        return float(np.dot(h, rs))

    return f


def _check_reachable(sat, site: Site) -> None:
    incl = math.degrees(sat.inclo)
    reach = min(incl, 180.0 - incl)
    if abs(site.lat_deg) >= reach:
        raise ValueError(
            f"{site.name} (lat {site.lat_deg:.2f}) never passes through a plane of inclination {incl:.2f} deg: "
            f"direct launch needs |lat| < min(i, 180-i) (a dogleg would be required)"
        )


def _crossing_at(sat, site: Site, t: float) -> Crossing:
    r, v = propagate_teme(sat, t)
    h = _unit(np.cross(r[0], v[0]))
    rs_hat = _unit(site_teme_m(site.lat_deg, site.lon_deg, site.alt_m, t))
    d = _unit(np.cross(h, rs_hat))  # prograde in-plane direction at the site point
    lat, lon = math.radians(site.lat_deg), math.radians(site.lon_deg)
    east = ecef_to_teme(np.array([-math.sin(lon), math.cos(lon), 0.0]), t)
    north = ecef_to_teme(np.array([-math.sin(lat) * math.cos(lon), -math.sin(lat) * math.sin(lon), math.cos(lat)]), t)
    north_c = float(np.dot(d, north))
    azimuth = math.degrees(math.atan2(float(np.dot(d, east)), north_c)) % 360.0
    node = _unit(np.cross([0.0, 0.0, 1.0], h))
    perp = np.cross(h, node)
    u_site = math.degrees(math.atan2(float(np.dot(rs_hat, perp)), float(np.dot(rs_hat, node)))) % 360.0
    return Crossing(
        t_unix=t,
        ascending=north_c > 0.0,
        azimuth_deg=azimuth,
        plane_error_deg=abs(math.degrees(math.asin(float(np.dot(h, rs_hat))))),
        u_site_deg=u_site,
        node_hat=node,
        perp_hat=perp,
    )


def find_plane_crossings(sat, site: Site, t_start: float, t_end: float, step_s: float = SCAN_STEP_S) -> list[Crossing]:
    """All times in [t_start, t_end] when ``site`` lies in the (osculating) orbital plane of ``sat``."""
    _check_reachable(sat, site)
    grid = np.arange(t_start, t_end + step_s, step_s)
    r, v = propagate_teme(sat, grid)
    h = _unit(np.cross(r, v))
    rs = _unit(site_teme_m(site.lat_deg, site.lon_deg, site.alt_m, grid))
    f = np.einsum("ij,ij->i", h, rs)
    f_scalar = _plane_function(sat, site)
    out = []
    for i in np.flatnonzero(f[:-1] * f[1:] < 0.0):
        t = brentq(f_scalar, grid[i], grid[i + 1], xtol=1e-3)
        if t_start <= t <= t_end:
            out.append(_crossing_at(sat, site, t))
    return out


# --------------------------------------------------------------------------- target selection


def pick_target(df, profile: Profile) -> dict:
    """The catalog row that defines the target plane for ``profile``."""
    if profile.target_norad is not None:
        hit = df[df["norad_id"] == profile.target_norad]
        if hit.empty:
            raise KeyError(f"NORAD {profile.target_norad} not in catalog")
        return hit.iloc[0].to_dict()
    cand = df[df["group"] == profile.reference_group].copy()
    cand["_d"] = (cand["incl_deg"] - profile.reference_incl_deg).abs()
    if profile.mode == "plane":  # the plane defines the windows, so it must really be the intended one
        cand = cand[cand["_d"] < 2.0]
    if cand.empty:
        raise KeyError(f"no {profile.reference_group} object near {profile.reference_incl_deg} deg in the catalog")
    # nearest inclination first; ties by NORAD id for determinism ("nominal" mode only uses the row as metadata)
    return cand.sort_values(["_d", "norad_id"]).iloc[0].drop("_d").to_dict()


def catalog_for(offline: bool):
    if offline:
        df, _ = build_catalog(sources_from_sample()[0])
        return df
    return load_catalog(CATALOG_PATH)


# --------------------------------------------------------------------------- windows.json


def _window_record(i: int, t: float, c_az: float, c_err: float, ascending: bool, site: Site, extra: dict) -> dict:
    t_int = int(round(t))
    return {
        "id": f"w{i}",
        "t_launch_unix": t_int,
        "t_launch_utc": _utc(t_int),
        "azimuth_deg": round(c_az, 2),
        "plane_error_deg": round(c_err, 4),
        "pass": "ascending" if ascending else "descending",
        "phase_angle_deg": extra.get("phase_angle_deg"),
        "rendezvous_hours_fast": extra.get("rendezvous_hours_fast"),
        "rendezvous_hours_slow": extra.get("rendezvous_hours_slow"),
        "flyable": bool(ascending or site.allow_descending),
    }


def compute_windows(
    profile_id: str,
    site: Site | str,
    df,
    t_start_unix: float,
    days: float = 7.0,
    now_unix: float | None = None,
) -> dict:
    """Build a contract-valid windows.json dict for ``profile_id`` at ``site`` over ``days`` from ``t_start_unix``."""
    profile = get_profile(profile_id)
    site = get_site(site) if isinstance(site, str) else site
    target = pick_target(df, profile)
    sat = satrec_from_tle(target["tle1"], target["tle2"])
    age_d = (t_start_unix - sat_epoch_unix(sat)) / 86400.0
    if age_d > MAX_TLE_AGE_DAYS:
        raise ValueError(
            f"TLE for NORAD {target['norad_id']} is {age_d:.1f} days older than the search start "
            f"(limit {MAX_TLE_AGE_DAYS:.0f}); refresh the catalog with `python -m catalog.fetch`"
        )
    t_end = t_start_unix + days * 86400.0
    note = (
        f"Computed by mission/windows.py from the TLE of NORAD {target['norad_id']} (epoch {target['epoch_utc']}); "
        f"search {_utc(t_start_unix)} + {days:g} d. Plane-crossing times and inertial azimuth are real geometry "
        f"(SGP4, km-level TLE error). Not an operational launch plan."
    )
    windows = []
    if profile.mode == "nominal":
        note += (
            " PLACEHOLDER profile: one nominal due-east opportunity per day at local hour "
            f"{profile.nominal_local_hour:g}; GEO transfer timing is not modelled; plane_error_deg is the "
            "inclination that must still be removed at GEO (= site latitude)."
        )
        az = inertial_azimuth_deg(site.lat_deg, abs(site.lat_deg))
        day0 = math.floor((t_start_unix + site.lon_deg / 15.0 * 3600.0) / 86400.0)
        k = 0
        for d in range(int(math.ceil(days)) + 2):
            t = (day0 + d) * 86400.0 + profile.nominal_local_hour * 3600.0 - site.lon_deg / 15.0 * 3600.0
            if t_start_unix <= t <= t_end:
                windows.append(_window_record(k, t, az, abs(site.lat_deg), True, site, {}))
                k += 1
    else:
        crossings = find_plane_crossings(sat, site, t_start_unix, t_end)
        target_a = semi_major_axis_from_mean_motion(sat.no_kozai * 1440.0 / (2.0 * math.pi))
        for k, c in enumerate(crossings):
            extra = {}
            if profile.mode == "iss":
                r_t, _ = propagate_teme(sat, c.t_unix + profile.ascent_s)
                r_t = _unit(r_t[0])
                u_target = math.degrees(math.atan2(float(r_t @ c.perp_hat), float(r_t @ c.node_hat)))
                phase = (u_target - (c.u_site_deg + profile.ascent_arc_deg)) % 360.0
                fast, slow = rendezvous_estimates(
                    phase,
                    profile.park_alt_m,
                    target_a,
                    profile.fast_max_revs,
                    profile.slow_min_revs,
                    profile.approach_h,
                )
                extra = {
                    "phase_angle_deg": round(phase, 2),
                    "rendezvous_hours_fast": fast,
                    "rendezvous_hours_slow": slow,
                }
                note_iss = (
                    f" Phasing assumes {profile.ascent_s:g} s ascent, {profile.ascent_arc_deg:g} deg in-plane arc, "
                    f"{profile.park_alt_m / 1000:g} km parking orbit; rendezvous hours are impulsive estimates "
                    f"excluding proximity operations; fast = null means no <= {profile.fast_max_revs}-rev solution."
                )
                if note_iss not in note:
                    note += note_iss
            windows.append(_window_record(k, c.t_unix, c.azimuth_deg, c.plane_error_deg, c.ascending, site, extra))
    obj = {
        "schema_version": 1,
        "_note": note,
        "profile": profile.id,
        "site": {"name": site.name, "lat_deg": site.lat_deg, "lon_deg": site.lon_deg},
        "target": {"norad_id": int(target["norad_id"]), "tle_epoch_utc": target["epoch_utc"]},
        "generated_utc": _utc(now_unix if now_unix is not None else datetime.now(UTC).timestamp()),
        "windows": windows,
    }
    return validate("windows", obj)


# --------------------------------------------------------------------------- CLI


def _parse_start(s: str | None) -> float:
    if s is None:
        return math.floor(datetime.now(UTC).timestamp() / 60.0) * 60.0
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC).timestamp()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compute launch windows -> data/results/windows_<profile>_<site>.json")
    ap.add_argument("--profile", default="all", choices=["all", *PROFILES])
    ap.add_argument("--site", default="cape_canaveral", choices=sorted(SITES))
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--start", default=None, help="ISO UTC start (default: now)")
    ap.add_argument("--offline", action="store_true", help="use the committed offline catalog sample")
    ap.add_argument("--allow-descending", action="store_true", help="mark descending passes flyable for this run")
    ap.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = ap.parse_args(argv)
    try:
        df = catalog_for(args.offline)
        site = (
            get_site(args.site).with_descending(args.allow_descending) if args.allow_descending else get_site(args.site)
        )
        t0 = _parse_start(args.start)
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for pid in PROFILES if args.profile == "all" else [args.profile]:
            obj = compute_windows(pid, site, df, t0, args.days)
            path = args.out_dir / f"windows_{pid}_{site.key}.json"
            path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
            fly = sum(w["flyable"] for w in obj["windows"])
            print(f"{pid:10s} {site.name:15s} {len(obj['windows']):3d} windows ({fly} flyable) -> {path.name}")
        return 0
    except (ValueError, KeyError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
