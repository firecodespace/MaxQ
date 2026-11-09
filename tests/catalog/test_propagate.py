"""WP-01 acceptance: ISS (NORAD 25544) propagates via SGP4 and lands where ISS should be. Owner: Yash.

Uses the committed real-data offline sample, so it works without a network. The ISS inclination and
altitude are read from the TLE, never hard-coded; the checks use wide physical bounds.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from catalog.fetch import SAMPLE_PATH, build_catalog, get_tle, sources_from_sample
from catalog.propagate import PropagationError, geodetic_track, propagate_teme, sat_epoch_unix, satrec_from_tle
from core.constants import MU_M3_S2

ISS = 25544


@pytest.fixture(scope="module")
def iss():
    sources, _ = sources_from_sample()
    df, _ = build_catalog(sources)
    name, l1, l2 = get_tle(df, ISS)
    row = df[df.norad_id == ISS].iloc[0]
    return name, satrec_from_tle(l1, l2), row


def test_iss_is_in_offline_sample():
    s = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    assert "25544U" in s["groups"]["stations"]


def test_iss_propagates_at_epoch(iss):
    name, sat, row = iss
    assert "ISS" in name
    t0 = sat_epoch_unix(sat)
    r, v = propagate_teme(sat, t0)
    radius_km, speed = np.linalg.norm(r[0]), np.linalg.norm(v[0])
    assert 6578 < radius_km < 6878  # ISS-like LEO: 200-500 km altitude
    assert 7.5 < speed < 7.8  # km/s
    # vis-viva for a near-circular orbit: v ~ sqrt(mu/r)
    assert speed == pytest.approx(np.sqrt(MU_M3_S2 / 1e9 / radius_km), rel=0.01)


def test_iss_ground_track_over_one_day(iss):
    _, sat, row = iss
    t0 = sat_epoch_unix(sat)
    t = t0 + np.arange(0, 86400, 30.0)
    lat, lon, alt = geodetic_track(sat, t)
    assert np.max(np.abs(lat)) == pytest.approx(row["incl_deg"], abs=0.3)  # reaches +-inclination, not beyond
    assert np.all((alt > 300_000) & (alt < 500_000))
    assert np.all((lon >= -180) & (lon <= 180))
    # period from mean motion ~ 92-93 min: lat repeats after one orbit
    period_s = 86400.0 / row["mean_motion_rev_day"]
    assert 5400 < period_s < 5700
    lat1, _, _ = geodetic_track(sat, np.array([t0, t0 + period_s]))
    assert lat1[1] == pytest.approx(lat1[0], abs=1.0)


def test_epoch_matches_catalog(iss):
    _, sat, row = iss
    from datetime import UTC, datetime

    cat_epoch = datetime.fromisoformat(row["epoch_utc"].replace("Z", "+00:00")).astimezone(UTC).timestamp()
    assert sat_epoch_unix(sat) == pytest.approx(cat_epoch, abs=0.01)


def test_propagation_error_is_loud(iss):
    _, sat, _ = iss
    with pytest.raises(PropagationError):
        propagate_teme(sat, sat_epoch_unix(sat) + 86400.0 * 365 * 400)  # decayed/diverged long before this
