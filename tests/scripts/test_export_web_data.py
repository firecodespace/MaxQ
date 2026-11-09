"""WP-03: web data export. Owner: Yash."""

from __future__ import annotations

import json
import shutil

import pytest

import scripts.export_web_data as E
from contracts.validate import validate_file


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    out = tmp_path_factory.mktemp("web_data")
    assert E.main(["--out", str(out)]) == 0
    return out


def test_manifest_lists_valid_files_and_flags_dummies(exported):
    m = json.loads((exported / "manifest.json").read_text())
    names = {f["name"]: f for f in m["files"]}
    assert "windows_dummy.json" in names and names["windows_dummy.json"]["dummy"] is True
    assert "trajectory_dummy.json" in names and names["trajectory_dummy.json"]["dummy"] is True  # '_note': 'dummy'
    real = [f for f in m["files"] if f["kind"] == "windows" and not f["dummy"]]
    assert real, "real windows from WP-02 should be exported and not flagged dummy"
    for f in m["files"]:
        validate_file(exported / f["name"])  # copies are contract-valid


def test_catalog_web_shape_and_iss_present(exported):
    c = json.loads((exported / "catalog_web.json").read_text())
    assert c["shown_objects"] == len(c["objects"]) <= c["total_objects"]
    assert c["groups"][:3] == ["stations", "gps-ops", "geo"]
    row = next(o for o in c["objects"] if o[0] == 25544)
    assert row[3].startswith("1 25544") and row[4].startswith("2 25544") and c["groups"][row[2]] == "stations"
    assert all(len(o) == 5 and 0 <= o[2] < len(c["groups"]) for o in c["objects"])
    assert len({o[0] for o in c["objects"]}) == len(c["objects"])  # no duplicates


def test_export_is_deterministic(exported, tmp_path):
    E.main(["--out", str(tmp_path)])
    assert (tmp_path / "catalog_web.json").read_bytes() == (exported / "catalog_web.json").read_bytes()


def test_invalid_file_fails_loudly(tmp_path, monkeypatch, capsys):
    bad_dir = tmp_path / "dummy"
    bad_dir.mkdir()
    shutil.copy(E.REPO_ROOT / "data" / "dummy" / "windows_dummy.json", bad_dir / "windows_dummy.json")
    obj = json.loads((bad_dir / "windows_dummy.json").read_text())
    obj["windows"][0]["azimuth_deg"] = 999
    (bad_dir / "windows_dummy.json").write_text(json.dumps(obj))
    monkeypatch.setattr(E, "DATA_DIRS", (bad_dir,))
    assert E.main(["--out", str(tmp_path / "out")]) == 1
    assert "azimuth_deg" in capsys.readouterr().err
