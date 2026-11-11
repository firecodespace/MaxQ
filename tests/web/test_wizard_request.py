"""The request the wizard builds must satisfy the contract the API validates it against (WP2-07).

tests/web/wizard.test.mjs checks the shape in JavaScript, which can only mirror the schema. This
runs the browser's own `buildMissionRequest` in Node and hands the result to the real validator, so
the two sides cannot drift: if the schema gains a required field, this fails rather than the user
discovering it as a 422 at the end of six wizard steps.

Skipped (not failed) when Node is not installed, like the other JavaScript tests.

Owner: Yash (lane: web/, tests/).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from contracts.validate import ContractError, validate

ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

BUILD = r"""
import { readFileSync } from "node:fs";
import { buildMissionRequest, padsForVehicle, usableWindows } from "../../web/js/wizard-logic.js";

const load = (p) => JSON.parse(readFileSync(p, "utf8"));
const rockets = load("web/public/data/rockets.json");
const pads = load("web/public/data/pads.json");
const windows = load("web/public/data/windows_%(profile)s_cape_canaveral.json");

const vehicle = rockets.find((v) => v.id === "%(rocket)s");
const out = buildMissionRequest({
  profile: "%(profile)s",
  payload_kg: %(payload)s,
  crew: %(crew)s,
  vehicle,
  pad: padsForVehicle(pads, vehicle.id)[0],
  window: usableWindows(windows)[0],
  target_norad: windows.target.norad_id,
  max_attempts: 24,
  nowMs: Date.parse("2026-11-11T04:46:40Z"),
});
process.stdout.write(JSON.stringify(out));
"""


def _build(profile: str, rocket: str, payload: float, crew: int) -> dict:
    script = ROOT / "tests" / "web" / "_build_request.mjs"
    script.write_text(
        BUILD % {"profile": profile, "rocket": rocket, "payload": payload, "crew": crew},
        encoding="utf-8",
    )
    try:
        proc = subprocess.run(
            [NODE, str(script)], capture_output=True, text=True, timeout=60, cwd=ROOT
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        return json.loads(proc.stdout)
    finally:
        script.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("profile", "rocket", "payload", "crew"),
    [
        ("cargo_iss", "falcon9_block5", 12000, 0),
        ("crew_iss", "falcon9_block5", 12000, 4),
    ],
)
def test_wizard_request_validates_against_the_contract(profile, rocket, payload, crew):
    req = _build(profile, rocket, payload, crew)
    validate("mission_request", req)  # raises ContractError naming the field
    assert req["profile"] == profile
    assert req["rocket_id"] == rocket
    assert req["payload"]["crew"] == crew
    # The cost normalisers must be real scales, or the fuel and time terms cannot be compared.
    assert req["objective"]["fuel_ref_kg"] > 1000
    assert req["objective"]["time_ref_s"] == 86400


def test_the_wizard_cannot_build_a_crewless_crew_mission():
    """The wizard blocks this in the UI; the contract blocks it here too."""
    req = _build("crew_iss", "falcon9_block5", 12000, 0)
    with pytest.raises(ContractError, match="crew"):
        validate("mission_request", req)


def test_the_api_accepts_a_wizard_request_end_to_end(tmp_path):
    """Hand the browser's request to the real endpoint, not just to the validator."""
    from fastapi.testclient import TestClient

    from server import create_app

    req = _build("cargo_iss", "falcon9_block5", 12000, 0)
    app = create_app(root=tmp_path)
    with TestClient(app) as client:
        r = client.post("/api/missions", json=req)
        assert r.status_code == 201, r.text
        assert r.json()["id"] == req["id"]
    app.state.store.shutdown()
