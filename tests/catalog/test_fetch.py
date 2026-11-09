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
from catalog.fetch import FetchError, build_catalog, fetch_group, parse_3le, run, sources_from_sample
from contracts.validate import validate_catalog_parquet

ISS_3LE = (
    "ISS (ZARYA)\n"
    "1 25544U 98067A   26313.20747964  .00008201  00000+0  15534-3 0  9995\n"
    "2 25544  51.6321 302.4538 0007297   5.2479 354.8585 15.49270212594231\n"
)
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
    fake = opener([ISS_3LE])
    r1 = fetch_group("stations", tmp_path, now=T0)
    assert not r1.from_cache and "25544U" in r1.text
    assert fake.calls == ["https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=3le"]
    assert (tmp_path / "stations.3le").read_text() == ISS_3LE
    meta = json.loads((tmp_path / "stations.3le.meta.json").read_text())
    assert meta["fetched_utc"] == "2026-11-10T00:00:00Z" and len(meta["sha256"]) == 64

    r2 = fetch_group("stations", tmp_path, now=T0 + timedelta(minutes=119))  # < 2 h: no network
    assert r2.from_cache and len(fake.calls) == 1

    fake.script.append(ISS_3LE)
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
    fake = opener([ISS_3LE, http_error(403, "GP data has not updated since your last successful download")])
    fetch_group("stations", tmp_path, now=T0)
    r = fetch_group("stations", tmp_path, now=T0 + timedelta(hours=3))
    assert r.from_cache and "25544U" in r.text and len(fake.calls) == 2


def test_network_error_needs_allow_stale(tmp_path, opener):
    opener([ISS_3LE, urllib.error.URLError("no route"), urllib.error.URLError("no route")])
    fetch_group("stations", tmp_path, now=T0)
    with pytest.raises(FetchError, match="network error"):
        fetch_group("stations", tmp_path, now=T0 + timedelta(hours=3))
    r = fetch_group("stations", tmp_path, now=T0 + timedelta(hours=3, minutes=1), allow_stale=True)
    assert r.stale and r.from_cache


def test_non_tle_body_rejected(tmp_path, opener):
    opener(["<html>maintenance</html>"])
    with pytest.raises(FetchError, match="not 3LE data"):
        fetch_group("stations", tmp_path, now=T0)


def test_redirects_are_not_followed():
    assert F._NoRedirect().redirect_request(None, None, 301, "", {}, "http://x") is None


# ---------------------------------------------------------------- parsing and building


def test_parse_3le_iss():
    rows, rejected = parse_3le(ISS_3LE)
    assert rejected == 0 and len(rows) == 1
    r = rows[0]
    assert r["norad_id"] == 25544 and r["name"] == "ISS (ZARYA)"
    assert r["incl_deg"] == pytest.approx(51.6321) and r["ecc"] == pytest.approx(0.0007297)
    assert r["raan_deg"] == pytest.approx(302.4538) and r["mean_motion_rev_day"] == pytest.approx(15.49270212)
    assert r["epoch_utc"].startswith("2026-11-09T04:58:46") and r["epoch_utc"].endswith("Z")
    assert 380 < r["alt_km_approx"] < 450


def test_parse_rejects_bad_checksum():
    bad = ISS_3LE.replace("9995", "9996")
    rows, rejected = parse_3le(bad)
    assert rows == [] and rejected == 1


def test_build_catalog_priority_dedupe_and_starlink_split():
    iss_as_active = ISS_3LE
    star = ISS_3LE.replace("ISS (ZARYA)", "STARLINK-1234").replace("25544", "44444")
    # fix checksums for the edited starlink lines
    lines = star.splitlines()
    for k in (1, 2):
        body = lines[k][:68]
        lines[k] = body + str(sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in body) % 10)
    star = "\n".join(lines) + "\n"
    df, stats = build_catalog([("stations", "stations", ISS_3LE), ("active", "active", iss_as_active + star)])
    assert stats["duplicates_dropped"] == 1
    assert dict(zip(df.norad_id, df.group, strict=True)) == {25544: "stations", 44444: "starlink"}


def test_build_catalog_refuses_mostly_corrupt_input():
    with pytest.raises(FetchError, match="rejected"):
        build_catalog([("active", "active", ISS_3LE + ISS_3LE.replace("9995", "9990") * 10)])


# ---------------------------------------------------------------- offline sample and parquet contract


def test_offline_mode_builds_valid_parquet_from_committed_sample(tmp_path):
    out = tmp_path / "catalog.parquet"
    res = run(offline=True, out=out, raw_dir=tmp_path / "empty_raw")  # no cache, no network -> sample
    assert res["mode"] == "offline/committed-sample"
    validate_catalog_parquet(out)  # contract + schema_version metadata
    df = pd.read_parquet(out)
    assert 25544 in set(df.norad_id) and len(df) > 50
    assert set(df.group) >= {"stations", "gps-ops", "geo", "debris"}


def test_offline_sample_is_real_data_with_a_fetch_timestamp():
    sources, fetched = sources_from_sample()
    assert fetched.endswith("Z") and sum(len(parse_3le(t)[0]) for _, _, t in sources) >= 50


def test_load_catalog_missing_file_message(tmp_path):
    with pytest.raises(FileNotFoundError, match="catalog.fetch"):
        F.load_catalog(tmp_path / "nope.parquet")
