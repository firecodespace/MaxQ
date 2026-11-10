"""The browser ascent simulator must agree with the Python one.

``web/js/sim3d/ascent.js`` is a port of ``ascent/sim.py`` so the site can re-fly a launch without a
server. A port that silently disagrees with the model it claims to reproduce would make every number
on the 3D page untrustworthy, so the agreement is checked here rather than assumed: the same rocket
and the same launch conditions go to both simulators and every 1 Hz sample is compared.

Tolerances are at the contract's own precision (lat/lon 4 dp, the rest 1 dp) plus a little room for
the order in which IEEE-754 doubles are summed. Events must match exactly, since they are integers.

Skipped (not failed) when Node is not installed, like the other JavaScript tests.

Owner: Yash (lane: web/, tests/).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ascent.rockets import list_archetypes
from ascent.sim import DEFAULT_PITCH_PARAMS, simulate_ascent

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tests" / "ascent" / "js_ascent_runner.mjs"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# One case per archetype, with launch conditions that are not the defaults so the port cannot pass
# by accident on hard-coded values.
CASES = [
    ("medlift_kerolox_a", 28.5619, -80.5772, 44.87, 51.6),
    ("crewlift_kerolox_b", 28.5619, -80.5772, 44.87, 51.6),
    ("heavylift_hydrolox_c", 34.7420, -120.5724, 158.30, 97.4),
]


def _run_js(rocket_dict, opts):
    proc = subprocess.run(
        [NODE, str(RUNNER), json.dumps({"rocket": rocket_dict, "opts": opts})],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    return json.loads(proc.stdout)


def _max_abs_diff(a, b):
    return max(abs(x - y) for x, y in zip(a, b, strict=True))


@pytest.mark.parametrize(("rocket_id", "lat", "lon", "az", "incl"), CASES)
def test_js_ascent_matches_python(rocket_id, lat, lon, az, incl):
    rocket = next(r for r in list_archetypes() if r.id == rocket_id)
    pitch = DEFAULT_PITCH_PARAMS[rocket_id]

    py = simulate_ascent(
        rocket=rocket,
        site_lat_deg=lat,
        site_lon_deg=lon,
        azimuth_deg=az,
        target_incl_deg=incl,
        pitch_params=pitch,
        trajectory_id=f"traj_{rocket_id}_parity",
        window_id="w0",
        t0_unix=1794267690,
    ).trajectory

    js = _run_js(
        rocket.to_dict(),
        {
            "siteLatDeg": lat,
            "siteLonDeg": lon,
            "azimuthDeg": az,
            "targetInclDeg": incl,
            "pitchParams": pitch,
            "trajectoryId": f"traj_{rocket_id}_parity",
            "windowId": "w0",
            "t0Unix": 1794267690,
        },
    )
    jt = js["trajectory"]

    # Same number of samples at the same times: a drift of even one step would shift everything.
    assert jt["t_s"] == py["t_s"], "sample times differ between the two simulators"
    assert len(jt["t_s"]) > 100, "suspiciously short trajectory"

    # Position and state, at the precision the contract stores.
    assert _max_abs_diff(jt["lat_deg"], py["lat_deg"]) <= 2e-4
    assert _max_abs_diff(jt["lon_deg"], py["lon_deg"]) <= 2e-4
    assert _max_abs_diff(jt["alt_m"], py["alt_m"]) <= 0.5
    assert _max_abs_diff(jt["speed_mps"], py["speed_mps"]) <= 0.2
    assert _max_abs_diff(jt["q_pa"], py["q_pa"]) <= 0.2
    assert _max_abs_diff(jt["mass_kg"], py["mass_kg"]) <= 0.2

    # Events are integer seconds, so they have to be identical.
    assert jt["events"] == py["events"]

    # The orbit the ascent actually reaches.
    assert jt["final_orbit"]["perigee_km"] == pytest.approx(py["final_orbit"]["perigee_km"], abs=0.3)
    assert jt["final_orbit"]["apogee_km"] == pytest.approx(py["final_orbit"]["apogee_km"], abs=0.3)
    assert jt["final_orbit"]["ecc"] == pytest.approx(py["final_orbit"]["ecc"], abs=5e-4)
    assert jt["rocket_id"] == py["rocket_id"]

    # The attitude series the flight camera uses is not in the contract, so check it is at least
    # present, the right length, and inside the range the pitch program can produce.
    extra = js["extra"]
    for key in ("pitch_deg", "stage", "thrust_n"):
        assert len(extra[key]) == len(jt["t_s"]), f"{key} is not parallel to t_s"
    assert max(extra["pitch_deg"]) <= 90.0 + 1e-6
    assert min(extra["pitch_deg"]) >= -90.0
    assert set(extra["stage"]) <= {0, 1, 2}


def test_js_output_satisfies_the_trajectory_contract():
    """Whatever the browser produces must be a valid trajectory.json, not merely plot-shaped."""
    from contracts.validate import validate

    rocket = next(r for r in list_archetypes() if r.id == "medlift_kerolox_a")
    js = _run_js(
        rocket.to_dict(),
        {
            "siteLatDeg": 28.5619,
            "siteLonDeg": -80.5772,
            "azimuthDeg": 44.87,
            "targetInclDeg": 51.6,
            "pitchParams": DEFAULT_PITCH_PARAMS["medlift_kerolox_a"],
            "trajectoryId": "traj_browser_contract_check",
        },
    )
    validate("trajectory", js["trajectory"])  # raises ContractError naming the field
