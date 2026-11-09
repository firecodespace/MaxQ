"""WP-01 acceptance: cached fetch, offline sample, parsing, contract-valid parquet. Owner: Yash.

Network is never touched: urllib is replaced by a fake opener that counts calls.
"""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

import catalog.fetch as F
from catalog.fetch import FetchError, build_catalog, fetch_group, parse_omm, run, sources_from_sample
from contracts.validate import validate_catalog_parquet

ISS_OMM = {
    "OBJECT_NAME": "ISS (ZARYA)", "OBJECT_ID": "1998-067A", "EPOCH": "2026-11-09T11:10:18.771456",
    "MEAN_MOTION": 15.49275323, "ECCENTRICITY": 0.00072858, "INCLINATION": 51.6321, "RA_OF_ASC_NODE": 301.1765,
    "ARG_OF_PERICENTER": 6.3021, "MEAN_ANOMALY": 353.8058, "EPHEMERIS_TYPE": 0, "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": 25544, "ELEMENT_SET_NO": 999, "REV_AT_EPOCH": 59427, "BSTAR": 0.00016254709,
    "MEAN_MOTION_DOT": 8.605e-05, "MEAN_MOTION_DDOT": 0,
}  # fmt: skip
SIX_DIGIT_OMM = {**ISS_OMM, "OBJECT_NAME": "SOYUZ-MS 29", "OBJECT_ID": "2026-162A", "NORAD_CAT_ID": 100057}
ISS_JSON = json.dumps([ISS_OMM])
T0 = datetime(2026, 11, 10, 0, 0, 0, tzinfo=UTC)


class FakeResp:
    def __init__(self, body, status=200):
        self._b, self.status = body.encode(), status

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Scripted responses: each item is a body str, or an HTTPError / URLError to raise."""

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def open(self, req, timeout=None):
        self.calls.append(req.full_url)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResp(item)


@pytest.fixture
def opener(monkeypatch):
    def install(script):
        fake = FakeOpener(script)
        monkeypatch.setattr(F.urllib.request, "build_opener", lambda *a, **k: fake)
        return fake

    return install


def http_error(code, body=""):
    return urllib.error.HTTPError("https://celestrak.org/x", code, "err", {}, io.BytesIO(body.encode()))


# ---------------------------------------------------------------- caching


def test_fetch_caches_raw_and_respects_two_hour_policy(tmp_path, opener):
    fake = opener([ISS_JSON])
    r1 = fetch_group("stations", tmp_path, now=T0)
    assert not r1.from_cache and "25544" in r1.text
    assert fake.calls == ["https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=json"]
    assert (tmp_path / "stations.json").read_text() == ISS_JSON
    meta = json.loads((tmp_path / "stations.json.meta.json").read_text())
    assert meta["fetched_utc"] == "2026-11-10T00:00:00Z" and len(meta["sha256"]) == 64

    r2 = fetch_group("stations", tmp_path, now=T0 + timedelta(minutes=119))  # < 2 h: no network
    assert r2.from_cache and len(fake.calls) == 1

    fake.script.append(ISS_JSON)
    r3 = fetch_group("stations", tmp_path, now=T0 + timedelta(hours=2, minutes=1))  # >= 2 h: refresh
    assert not r3.from_cache and len(fake.calls) == 2


def test_http_error_is_loud_and_blocks_retries_for_two_hours(tmp_path, opener):
    fake = opener([http_error(404, "not found")])
    with pytest.raises(FetchError, match="HTTP 404"):
        fetch_group("nope", tmp_path, now=T0)
    with pytest.raises(FetchError, match="not retrying"):  # second attempt never reaches the network
        fetch_group("nope", tmp_path, now=T0 + timedelta(minutes=30))
    assert len(fake.calls) == 1


def test_403_not_updated_uses_cache(tmp_path, opener):
    fake = opener([ISS_JSON, http_error(403, "GP data has not updated since your last successful download")])
    fetch_group("stations", tmp_path, now=T0)
    r = fetch_group("stations", tmp_path, now=T0 + timedelta(hours=3))
    assert r.from_cache and "25544" in r.text and len(fake.calls) == 2


def test_network_error_needs_allow_stale(tmp_path, opener):
    opener([ISS_JSON, urllib.error.URLError("no route"), urllib.error.URLError("no route")])
    fetch_group("stations", tmp_path, now=T0)
    with pytest.raises(FetchError, match="network error"):
        fetch_group("stations", tmp_path, now=T0 + timedelta(hours=3))
    r = fetch_group("stations", tmp_path, now=T0 + timedelta(hours=3, minutes=1), allow_stale=True)
    assert r.stale and r.from_cache


@pytest.mark.parametrize("body", ["<html>maintenance</html>", "[]", '{"error": "x"}', '[{"foo": 1}]'])
def test_non_omm_body_rejected(tmp_path, opener, body):
    opener([body])
    with pytest.raises(FetchError, match="not OMM JSON"):
        fetch_group("stations", tmp_path, now=T0)


def test_redirects_are_not_followed():
    assert F._NoRedirect().redirect_request(None, None, 301, "", {}, "http://x") is None


# ---------------------------------------------------------------- parsing and building


def test_parse_omm_iss_and_six_digit():
    rows, rejected = parse_omm(json.dumps([ISS_OMM, SIX_DIGIT_OMM]))
    assert rejected == 0 and len(rows) == 2
    iss, six = rows
    assert iss["norad_id"] == 25544 and iss["name"] == "ISS (ZARYA)" and iss["tle1"].startswith("1 25544U 98067A")
    assert iss["incl_deg"] == pytest.approx(51.6321) and iss["ecc"] == pytest.approx(0.00072858)
    assert iss["epoch_utc"] == "2026-11-09T11:10:18.771456Z"
    assert 380 < iss["alt_km_approx"] < 450
    assert six["norad_id"] == 100057 and six["tle1"] is None and six["argp_deg"] == pytest.approx(6.3021)


def test_parse_rejects_bad_records_and_counts_them():
    bad = {**ISS_OMM, "ECCENTRICITY": 2.0}
    rows, rejected = parse_omm(json.dumps([ISS_OMM, bad, {"NORAD_CAT_ID": 5}]))
    assert len(rows) == 1 and rejected == 2
    with pytest.raises(FetchError, match="does not parse"):
        parse_omm("not json")


def test_build_catalog_priority_dedupe_and_starlink_split():
    star = {**ISS_OMM, "OBJECT_NAME": "STARLINK-1234", "NORAD_CAT_ID": 44444}
    six_star = {**ISS_OMM, "OBJECT_NAME": "STARLINK-38128", "NORAD_CAT_ID": 100001}
    df, stats = build_catalog(
        [
            ("stations", "stations", ISS_JSON),
            ("active", "active", json.dumps([ISS_OMM, star, six_star])),
        ]
    )
    assert stats["duplicates_dropped"] == 1 and stats["six_digit_objects"] == 1
    assert dict(zip(df.norad_id, df.group, strict=True)) == {25544: "stations", 44444: "starlink", 100001: "starlink"}


def test_build_catalog_refuses_mostly_corrupt_input():
    bad = [{**ISS_OMM, "ECCENTRICITY": 2.0, "NORAD_CAT_ID": 1000 + i} for i in range(12)]
    with pytest.raises(FetchError, match="rejected"):
        build_catalog([("active", "active", json.dumps([ISS_OMM, *bad]))])


# ---------------------------------------------------------------- offline sample and parquet contract


def test_offline_mode_builds_valid_complete_parquet_from_committed_sample(tmp_path):
    out = tmp_path / "catalog.parquet"
    res = run(offline=True, out=out, raw_dir=tmp_path / "empty_raw")  # no cache, no network -> sample
    assert res["mode"] == "offline/committed-sample"
    validate_catalog_parquet(out)  # contract + schema_version metadata
    df = pd.read_parquet(out)
    assert 25544 in set(df.norad_id) and len(df) > 50
    assert set(df.group) >= {"stations", "gps-ops", "geo", "debris"}
    assert (df.norad_id >= 100000).sum() >= 4  # OMM-only objects are in the catalog, not silently dropped
    assert df.loc[df.norad_id >= 100000, "tle1"].isna().all()


def test_offline_sample_is_real_data_with_a_fetch_timestamp():
    sources, fetched = sources_from_sample()
    assert fetched.endswith("Z") and sum(len(parse_omm(t)[0]) for _, _, t in sources) >= 60


def test_load_catalog_missing_file_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="catalog.fetch"):
        F.load_catalog(tmp_path / "nope.parquet")
