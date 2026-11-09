"""WP-02 acceptance: sites, closed-form launch geometry, plane-crossing windows. Owner: Yash.

Known-value tests from HANDOFF section 7 plus an independent closed-form cross-check of the crossing times.
Uses the committed real-data offline catalog sample (no network).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pytest

from catalog.fetch import build_catalog, sources_from_sample
from catalog.propagate import sat_epoch_unix, satrec_from_tle
from contracts.validate import ContractError, validate
from core.constants import OMEGA_E_RAD_S, R_E_M
from core.frames import gmst_rad
from mission.phasing import (
    circular_speed_mps,
    hohmann_dv_mps,
    inertial_azimuth_deg,
    plane_change_dv_mps,
    rendezvous_estimates,
    rotating_azimuth_deg,
    rotation_speed_mps,
    semi_major_axis_from_mean_motion,
)
from mission.profiles import PROFILES
from mission.sites import SITES, get_site, min_inclination_deg
from mission.windows import MAX_TLE_AGE_DAYS, compute_windows, find_plane_crossings, pick_target

T_START = datetime(2026, 11, 10, 0, 0, 0, tzinfo=UTC).timestamp()


@pytest.fixture(scope="module")
def df():
    return build_catalog(sources_from_sample()[0])[0]


@pytest.fixture(scope="module")
def iss(df):
    row = pick_target(df, PROFILES["crew_iss"])
    return satrec_from_tle(row["tle1"], row["tle2"]), row


@pytest.fixture(scope="module")
def cape_week(df):
    return compute_windows("crew_iss", "cape_canaveral", df, T_START, 7, now_unix=T_START)


# ------------------------------------------------------------------ sites and closed-form geometry


def test_sites_include_cape_and_kourou():
    assert SITES["cape_canaveral"].lat_deg == pytest.approx(28.5, abs=0.1)
    assert SITES["kourou"].lat_deg == pytest.approx(5.2, abs=0.1)


@pytest.mark.parametrize("key", sorted(SITES))
def test_min_inclination(key):
    site = get_site(key)
    assert min_inclination_deg(site) == abs(site.lat_deg)
    # a direct launch at exactly the site latitude flies due east; below it is unreachable
    assert inertial_azimuth_deg(site.lat_deg, abs(site.lat_deg)) == pytest.approx(90.0, abs=1e-6)
    with pytest.raises(ValueError, match="below the site latitude"):
        inertial_azimuth_deg(site.lat_deg, abs(site.lat_deg) - 0.5)


def test_azimuth_cape_iss():
    az = inertial_azimuth_deg(28.5, 51.64)
    assert 44.0 <= az <= 46.0 and az == pytest.approx(44.9, abs=0.1)
    assert inertial_azimuth_deg(28.5, 51.64, ascending=False) == pytest.approx(180.0 - az)


def test_azimuth_kourou_iss_is_further_north():
    assert 38.0 < inertial_azimuth_deg(5.236, 51.64) < 39.5


def test_rotation_boost_cape():
    assert 405.0 <= rotation_speed_mps(28.5) <= 412.0
    assert rotation_speed_mps(5.236) == pytest.approx(OMEGA_E_RAD_S * R_E_M * math.cos(math.radians(5.236)))
    assert rotation_speed_mps(90.0) == pytest.approx(0.0, abs=1e-6)


def test_circular_speed_200km():
    assert circular_speed_mps(200_000.0) == pytest.approx(7780.0, abs=20.0)


def test_rotating_azimuth_is_south_of_inertial_for_eastward_launch():
    v = circular_speed_mps(200_000.0)
    rot = rotating_azimuth_deg(28.5, 51.64, v)
    assert (
        41.0 < rot < 44.0 < inertial_azimuth_deg(28.5, 51.64)
    )  # Earth rotation helps: heading shifts north of east... smaller az


def test_hohmann_leo_to_geo_and_plane_change_known_values():
    dv1, dv2 = hohmann_dv_mps(R_E_M + 200_000.0, 42_164_000.0)
    assert 3850 < dv1 + dv2 < 4000  # textbook ~3.9 km/s (185-200 km LEO to GEO)
    # 28.5 deg plane change at GEO speed: ~1.5 km/s (why GEO launches from low latitudes)
    assert plane_change_dv_mps(3074.7, 28.5) == pytest.approx(1514.0, abs=15.0)


# ------------------------------------------------------------------ phasing estimates


def test_rendezvous_estimates_shape():
    a_iss = semi_major_axis_from_mean_motion(15.49)
    fast, slow = rendezvous_estimates(31.2, 200_000.0, a_iss)  # the HANDOFF example's phase angle
    assert fast == pytest.approx(3.2, abs=0.2)  # ~3 h fast rendezvous
    assert slow == pytest.approx(25.0, abs=0.7)  # HANDOFF example: 3.2 h fast / 25 h slow
    assert slow > fast
    fast_none, slow_far = rendezvous_estimates(200.0, 200_000.0, a_iss)
    assert fast_none is None  # no <=2-rev phasing solution for a 200 deg lead
    _, slow_max = rendezvous_estimates(359.9, 200_000.0, a_iss)
    assert 25 < slow_max < 36  # a bit over a day of passive drift
    # more revolutions allowed -> a fast solution exists
    assert rendezvous_estimates(200.0, 200_000.0, a_iss, fast_max_revs=12)[0] is not None
    with pytest.raises(ValueError):
        rendezvous_estimates(10.0, 500_000.0, a_iss)  # parking orbit above the target


# ------------------------------------------------------------------ windows


def test_windows_per_day(cape_week):
    """The ISS plane crosses a mid-latitude site about twice a day (one ascending, one descending)."""
    ws = cape_week["windows"]
    assert 13 <= len(ws) <= 15  # 7 days
    passes = [w["pass"] for w in ws]
    assert all(a != b for a, b in zip(passes, passes[1:], strict=False))  # strictly alternating
    gaps = np.diff([w["t_launch_unix"] for w in ws])
    assert 3 * 3600 < gaps.min() and gaps.max() < 21 * 3600  # roughly 12 h apart, uneven (not 24 h)


def test_ascending_window_shifts_earlier_each_day(cape_week):
    asc = [w["t_launch_unix"] for w in cape_week["windows"] if w["pass"] == "ascending"]
    shifts = np.diff(asc) - 86400
    assert np.all((shifts > -30 * 60) & (shifts < -15 * 60))  # ISS plane regresses ~5 deg/day: ~22 min earlier/day


def test_azimuths_match_closed_form(cape_week, iss):
    sat, row = iss
    incl = row["incl_deg"]
    lat_gc = math.degrees(math.atan((1 - 0.00669437999014) * math.tan(math.radians(28.5619))))  # geocentric
    expect = math.degrees(math.asin(math.cos(math.radians(incl)) / math.cos(math.radians(lat_gc))))
    for w in cape_week["windows"]:
        want = expect if w["pass"] == "ascending" else 180.0 - expect
        assert w["azimuth_deg"] == pytest.approx(want, abs=0.4)
        assert w["plane_error_deg"] < 0.01  # solver residual: the site really is in the plane


def test_crossing_times_match_independent_j2_closed_form(iss):
    """Cross-check the numerical solver against a closed-form model: mean RAAN + J2 regression + spherical geometry."""
    sat, row = iss
    site = get_site("cape_canaveral")
    incl = math.radians(row["incl_deg"])
    a = semi_major_axis_from_mean_motion(row["mean_motion_rev_day"])
    n = row["mean_motion_rev_day"] * 2 * math.pi / 86400.0
    j2, p = 1.08262668e-3, a * (1 - row["ecc"] ** 2)
    raan_rate = -1.5 * j2 * (R_E_M / p) ** 2 * n * math.cos(incl)  # rad/s
    t_epoch, raan0 = sat_epoch_unix(sat), math.radians(row["raan_deg"])
    lat = math.radians(site.lat_deg)
    lon = math.radians(site.lon_deg)
    for ascending in (True, False):
        u = math.asin(math.sin(lat) / math.sin(incl))
        u = u if ascending else math.pi - u
        d_ra = math.atan2(math.cos(incl) * math.sin(u), math.cos(u))

        def g(t, d_ra=d_ra):
            raan = raan0 + raan_rate * (t - t_epoch)
            return (lon + gmst_rad(t) - raan - d_ra + math.pi) % (2 * math.pi) - math.pi

        # scan a day, take the sign change of g (skipping the 2pi wrap)
        ts = np.arange(T_START, T_START + 86400, 120.0)
        gs = np.array([g(t) for t in ts])
        i = [k for k in range(len(ts) - 1) if gs[k] * gs[k + 1] < 0 and abs(gs[k] - gs[k + 1]) < 1.0][0]
        from scipy.optimize import brentq

        t_closed = brentq(g, ts[i], ts[i + 1])
        found = [c for c in find_plane_crossings(sat, site, T_START, T_START + 86400) if c.ascending == ascending]
        assert len(found) == 1
        assert abs(found[0].t_unix - t_closed) < 90.0  # osculating-vs-mean plane + geocentric/geodetic latitude


def test_descending_not_flyable_by_default(cape_week):
    for w in cape_week["windows"]:
        assert w["flyable"] == (w["pass"] == "ascending")


def test_descending_flyable_when_site_allows(df):
    site = get_site("cape_canaveral").with_descending(True)
    obj = compute_windows("crew_iss", site, df, T_START, 2, now_unix=T_START)
    assert all(w["flyable"] for w in obj["windows"])


def test_windows_json_validates_and_is_labelled(cape_week):
    validate("windows", cape_week)
    assert cape_week["target"]["norad_id"] == 25544 and cape_week["profile"] == "crew_iss"
    assert "Not an operational launch plan" in cape_week["_note"]
    for w in cape_week["windows"]:
        assert w["t_launch_utc"] == datetime.fromtimestamp(w["t_launch_unix"], tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert 0.0 <= w["phase_angle_deg"] < 360.0
        assert w["rendezvous_hours_slow"] > 0
        assert w["rendezvous_hours_fast"] is None or 0 < w["rendezvous_hours_fast"] < 6


def test_some_days_offer_fast_rendezvous_and_some_do_not(df):
    obj = compute_windows("crew_iss", "cape_canaveral", df, T_START, 14, now_unix=T_START)
    fast = [w["rendezvous_hours_fast"] for w in obj["windows"] if w["pass"] == "ascending"]
    assert any(f is not None for f in fast) and any(f is None for f in fast)


def test_deterministic(df):
    a = compute_windows("cargo_iss", "kourou", df, T_START, 3, now_unix=T_START)
    b = compute_windows("cargo_iss", "kourou", df, T_START, 3, now_unix=T_START)
    assert a == b


def test_kourou_azimuth_and_windows(df):
    obj = compute_windows("crew_iss", "kourou", df, T_START, 3, now_unix=T_START)
    asc = [w for w in obj["windows"] if w["pass"] == "ascending"]
    assert asc and all(38.0 < w["azimuth_deg"] < 39.5 for w in asc)


def test_meo_gps_uses_gps_plane(df):
    obj = compute_windows("meo_gps", "cape_canaveral", df, T_START, 3, now_unix=T_START)
    row = df[df.norad_id == obj["target"]["norad_id"]].iloc[0]
    assert row["group"] == "gps-ops" and abs(row["incl_deg"] - 55) < 2
    assert len(obj["windows"]) >= 5
    assert all(w["rendezvous_hours_fast"] is None and w["phase_angle_deg"] is None for w in obj["windows"])
    asc = [w for w in obj["windows"] if w["pass"] == "ascending"]
    assert all(40.0 < w["azimuth_deg"] < 50.0 for w in asc)  # cos55/cos28.5 -> ~ 40.8 deg


def test_geo_comm_is_labelled_placeholder(df):
    obj = compute_windows("geo_comm", "cape_canaveral", df, T_START, 3, now_unix=T_START)
    assert "PLACEHOLDER" in obj["_note"]
    assert all(w["azimuth_deg"] == pytest.approx(90.0, abs=0.01) for w in obj["windows"])
    assert 3 <= len(obj["windows"]) <= 4


def test_unreachable_inclination_fails_loudly(df):
    # Baikonur (45.9N) cannot reach a ~28 deg plane directly: use a GEO-ish object with tiny inclination
    row = df[(df.group == "geo")].iloc[0]
    sat = satrec_from_tle(row["tle1"], row["tle2"])
    with pytest.raises(ValueError, match="never passes through"):
        find_plane_crossings(sat, get_site("baikonur"), T_START, T_START + 86400)


def test_stale_tle_is_refused(df):
    with pytest.raises(ValueError, match="refresh the catalog"):
        compute_windows("crew_iss", "cape_canaveral", df, T_START + (MAX_TLE_AGE_DAYS + 5) * 86400, 1)


def test_bad_windows_would_be_caught_by_contract(cape_week):
    bad = {**cape_week, "windows": [{**cape_week["windows"][0], "t_launch_unix": 1}]}
    with pytest.raises(ContractError, match="t_launch_unix"):
        validate("windows", bad)
