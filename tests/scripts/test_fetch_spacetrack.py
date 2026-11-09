"""Space-Track fetch script: rules, credential handling and validation, with a mocked network. Owner: Yash.

No real request is ever made here and no real credentials are used.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse

import pytest

from scripts.fetch_spacetrack_history import (
    FetchError,
    build_query,
    credentials_from_env,
    main,
    request_history,
    validate_records,
)

SECRET = "correct-horse-battery-staple-NOT-REAL"
ENV = {"SPACETRACK_USER": "someone@example.test", "SPACETRACK_PASS": SECRET}
REC = {
    "OBJECT_NAME": "ISS (ZARYA)", "OBJECT_ID": "1998-067A", "EPOCH": "2026-11-09T11:10:18.771456",
    "MEAN_MOTION": "15.49275323", "ECCENTRICITY": "0.00072858", "INCLINATION": "51.6321", "RA_OF_ASC_NODE": "301.1765",
    "ARG_OF_PERICENTER": "6.3021", "MEAN_ANOMALY": "353.8058", "EPHEMERIS_TYPE": "0", "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": "25544", "ELEMENT_SET_NO": "999", "REV_AT_EPOCH": "59427", "BSTAR": "0.00016254709",
    "MEAN_MOTION_DOT": "8.605e-05", "MEAN_MOTION_DDOT": "0", "EXTRA_FIELD": "ignored",
}  # fmt: skip


def records(n=5):
    return [{**REC, "EPOCH": f"2026-11-0{i + 1}T11:10:18.771456"} for i in range(n)]


class FakeResp:
    def __init__(self, body):
        self._b = body.encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, body=None, error=None):
        self.body, self.error, self.calls = body, error, []

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        if self.error:
            raise self.error
        return FakeResp(self.body)


def test_query_string_matches_spacetrack_rest_form():
    assert build_query(25544, 21) == (
        "https://www.space-track.org/basicspacedata/query/class/gp_history/NORAD_CAT_ID/25544/"
        "EPOCH/%3Enow-21/orderby/EPOCH%20asc/format/json"
    )
    with pytest.raises(FetchError, match="bounded"):
        build_query(25544, 400)
    with pytest.raises(FetchError, match="NORAD"):
        build_query(0, 5)


def test_credentials_come_only_from_the_environment_and_are_required():
    assert credentials_from_env(ENV) == ("someone@example.test", SECRET)
    with pytest.raises(FetchError, match="SPACETRACK_USER and SPACETRACK_PASS") as ei:
        credentials_from_env({})
    assert "never printed" in str(ei.value)


def test_exactly_one_request_login_and_query_together():
    op = FakeOpener(json.dumps(records()))
    out = request_history(25544, 21, ("someone@example.test", SECRET), opener=op)
    assert len(op.calls) == 1 and len(out) == 5
    req = op.calls[0]
    assert req.full_url == "https://www.space-track.org/ajaxauth/login" and req.get_method() == "POST"
    form = urllib.parse.parse_qs(req.data.decode())
    assert form["identity"] == ["someone@example.test"] and form["password"] == [SECRET]
    assert form["query"][0] == build_query(25544, 21)  # one decoding pass leaves the documented %3E / %20 intact
    assert all("EXTRA_FIELD" not in r for r in out)  # only the OMM fields are kept


def test_failed_login_and_bad_payloads_are_errors():
    with pytest.raises(FetchError, match="login failed"):
        request_history(25544, 21, ("u", "p"), opener=FakeOpener('{"Login": "Failed"}'))
    with pytest.raises(FetchError, match="at least 3"):
        validate_records(records(2), 25544)
    with pytest.raises(FetchError, match="lacks fields"):
        validate_records([{k: v for k, v in r.items() if k != "BSTAR"} for r in records()], 25544)
    with pytest.raises(FetchError, match="expected 25544"):
        validate_records([{**r, "NORAD_CAT_ID": "99999"} for r in records()], 25544)
    with pytest.raises(FetchError, match="not JSON"):
        request_history(25544, 21, ("u", "p"), opener=FakeOpener("<html>"))


def test_http_and_network_errors_do_not_leak_or_loop():
    err = urllib.error.HTTPError("https://www.space-track.org/x", 500, "rate", {}, io.BytesIO(b""))
    with pytest.raises(FetchError, match="HTTP 500.*do not retry") as ei:
        request_history(25544, 21, ("u", SECRET), opener=FakeOpener(error=err))
    assert SECRET not in str(ei.value)
    with pytest.raises(FetchError, match="network error"):
        request_history(25544, 21, ("u", SECRET), opener=FakeOpener(error=urllib.error.URLError("down")))


def test_refuses_to_download_twice_even_without_credentials(tmp_path, capsys):
    out = tmp_path / "h.json"
    out.write_text("{}")
    op = FakeOpener(json.dumps(records()))
    assert main(["--out", str(out)], env={}, opener=op) == 2
    assert "already exists" in capsys.readouterr().err and op.calls == []  # no network, no credential prompt


def test_missing_credentials_stop_before_any_network(tmp_path, capsys):
    op = FakeOpener(json.dumps(records()))
    assert main(["--out", str(tmp_path / "h.json")], env={}, opener=op) == 2
    assert "SPACETRACK_USER" in capsys.readouterr().err and op.calls == []


def test_success_writes_a_cited_real_marked_file_and_never_the_password(tmp_path, capsys):
    out = tmp_path / "h.json"
    op = FakeOpener(json.dumps(records(6)))
    assert main(["--out", str(out), "--days", "14"], env=ENV, opener=op) == 0
    text, cap = out.read_text(encoding="utf-8"), capsys.readouterr()
    doc = json.loads(text)
    assert (
        doc["data_source"] == "REAL"
        and doc["_note"].startswith("REAL Space-Track gp_history")
        and len(doc["snapshots"]) == 6
    )
    assert "Space-Track.org" in doc["citation"] and doc["norad_id"] == 25544
    assert (
        SECRET not in text and SECRET not in cap.out and SECRET not in cap.err
    )  # credentials never persisted or echoed
    assert "someone@example.test" not in text
    assert main(["--out", str(out)], env=ENV, opener=op) == 2 and len(op.calls) == 1  # second run refuses
