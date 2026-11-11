"""GEO and GPS missions through MissionEnv (WP2-15): the orbit-raising path.

Before this existed, `MissionEnv` flew every profile down the rendezvous path, so a GEO
delivery was scored on a phase angle to a satellite the payload was never going to meet. These
tests assert the two paths stay separate and that a transfer mission's record says what it
actually did.

In a separate file from `test_mission_env.py` deliberately: that file is Shiva's and he has
unpushed work in it.

Owner: Yash (lane: env/, tests/) -- taken over while Shiva is offline, on Yash's instruction.
"""

from __future__ import annotations

import copy

import pytest

from contracts.validate import validate
from env.mission_env import MissionEnv
from mission.transfer import is_transfer_profile, target_for_profile

TRANSFER_PROFILES = [("geo_comm", 44457), ("meo_gps", 28474)]


def _request(profile: str, norad: int, **over) -> dict:
    req = {
        "schema_version": 1,
        "id": f"msn_{profile}_test",
        "profile": profile,
        "payload": {"mass_kg": 2500.0, "crew": 0},
        "rocket_id": "falcon9_block5",
        "pad_id": "ccsfs_slc40",
        "target": {"norad_id": norad},
        "window_id": "w0",
        "constraints_profile": "cargo_default",
        "objective": {
            "w_fuel": 1.0, "w_time": 0.5, "fuel_ref_kg": 488370.0,
            "prop_orbit_ref_kg": 92670.0, "time_ref_s": 86400.0,
        },
        "budget": {"max_attempts": 10, "max_wall_s": 600.0},
        "seed": 42,
    }
    req.update(over)
    validate("mission_request", req)
    return req


@pytest.mark.parametrize("profile,norad", TRANSFER_PROFILES)
def test_a_transfer_mission_produces_a_contract_valid_attempt(profile, norad):
    attempt, trajectory = MissionEnv(_request(profile, norad)).evaluate_plan({})
    validate("attempt", attempt)
    validate("mission_trajectory", trajectory)


@pytest.mark.parametrize("profile,norad", TRANSFER_PROFILES)
def test_a_transfer_mission_records_transfer_phases_not_rendezvous_ones(profile, norad):
    """The record has to say what happened. `phasing` and `approach` on a GEO delivery would
    describe physics that never ran."""
    attempt, _ = MissionEnv(_request(profile, norad)).evaluate_plan({})
    names = [p["name"] for p in attempt["phases"]]
    assert names == ["ascent", "parking", "transfer", "circularise"]
    assert "phasing" not in names and "approach" not in names and "dock" not in names


@pytest.mark.parametrize("profile,norad", TRANSFER_PROFILES)
def test_a_transfer_mission_has_no_hold_points(profile, norad):
    """There is nothing to hold off from. Two waypoints nobody flew would be invented data."""
    attempt, _ = MissionEnv(_request(profile, norad)).evaluate_plan({})
    assert attempt["plan"]["waypoints"] == []


@pytest.mark.parametrize("profile,norad", TRANSFER_PROFILES)
def test_transfer_burns_are_recorded_in_the_inertial_frame(profile, norad):
    """LVLH is a frame relative to a target being approached, and there is not one."""
    attempt, _ = MissionEnv(_request(profile, norad)).evaluate_plan({})
    assert [b["frame"] for b in attempt["plan"]["burns"]] == ["ECI", "ECI"]


@pytest.mark.parametrize("profile,norad", TRANSFER_PROFILES)
def test_the_approach_constraints_are_reported_as_not_evaluated(profile, norad):
    """C09 and C10 are about an approach corridor and a docking contact. On a GEO delivery
    they do not apply, and `evaluated: false` is the honest report -- a different statement
    from passing them, which is what the old note-prefix scheme could not distinguish."""
    attempt, _ = MissionEnv(_request(profile, norad)).evaluate_plan({})
    for cid in ("C09", "C10"):
        cr = next(c for c in attempt["constraint_report"] if c["id"] == cid)
        assert cr["evaluated"] is False, f"{cid} claims to have been evaluated"
        assert cr["pass"] is False, f"{cid} was reported as passing without being checked"


@pytest.mark.parametrize("profile,norad", TRANSFER_PROFILES)
def test_a_transfer_mission_is_deterministic(profile, norad):
    req = _request(profile, norad)
    a, _ = MissionEnv(copy.deepcopy(req)).evaluate_plan({})
    b, _ = MissionEnv(copy.deepcopy(req)).evaluate_plan({})
    assert a["metrics"] == b["metrics"]
    assert a["cost"] == b["cost"]


def test_a_geo_delivery_spends_about_the_textbook_gto_budget():
    """Perigee kick ~2,440 m/s, apogee kick ~1,800 m/s from the Cape."""
    attempt, _ = MissionEnv(_request("geo_comm", 44457)).evaluate_plan({})
    phases = {p["name"]: p for p in attempt["phases"]}
    assert phases["transfer"]["dv_mps"] == pytest.approx(2440.0, abs=40.0)
    assert phases["circularise"]["dv_mps"] == pytest.approx(1800.0, abs=60.0)


def test_a_geo_coast_is_about_five_hours_plus_the_parking_orbit():
    attempt, _ = MissionEnv(_request("geo_comm", 44457)).evaluate_plan({})
    phases = {p["name"]: p for p in attempt["phases"]}
    coast_h = (phases["transfer"]["t1_s"] - phases["transfer"]["t0_s"]) / 3600.0
    assert coast_h == pytest.approx(5.26, abs=0.15)


def test_gps_costs_more_in_plane_change_than_geo_does():
    """GEO removes the Cape's 28.56 deg; GPS has to RAISE inclination to 55 deg, a 26.4 deg
    change at a lower altitude where the velocity being rotated is larger. A module that
    assumed plane error was always the site latitude would get this backwards."""
    geo, _ = MissionEnv(_request("geo_comm", 44457)).evaluate_plan({})
    gps, _ = MissionEnv(_request("meo_gps", 28474)).evaluate_plan({})
    geo_circ = next(p for p in geo["phases"] if p["name"] == "circularise")["dv_mps"]
    gps_circ = next(p for p in gps["phases"] if p["name"] == "circularise")["dv_mps"]
    assert gps_circ > geo_circ


def test_an_iss_mission_still_takes_the_rendezvous_path():
    """The branch must not have changed the path everything else uses."""
    from benchmarks import suite

    assert is_transfer_profile("cargo_iss") is False
    attempt, _ = MissionEnv(suite.mission_by_id("msn_bench_cargo_f9_w3_light")).evaluate_plan({})
    names = [p["name"] for p in attempt["phases"]]
    assert names == ["ascent", "parking", "phasing", "approach"]
    assert attempt["plan"]["waypoints"], "the rendezvous path lost its hold points"
    assert [b["frame"] for b in attempt["plan"]["burns"]] == ["LVLH", "LVLH"]


def test_the_on_orbit_propellant_split_still_adds_up_on_a_transfer():
    """The split landed before this path existed; it has to hold on both."""
    attempt, _ = MissionEnv(_request("geo_comm", 44457)).evaluate_plan({})
    m = attempt["metrics"]
    assert m["prop_ascent_kg"] + m["prop_on_orbit_kg"] == pytest.approx(
        m["prop_used_kg"], abs=1.0
    )


@pytest.mark.parametrize("profile", ["geo_comm", "meo_gps"])
def test_the_target_orbit_comes_from_the_profile_not_the_request(profile):
    """The mission_request's target is a NORAD id, as the contract says. The orbit itself is
    derived from the profile's required period, so a GEO mission cannot be pointed at the
    wrong altitude by a typo in a request."""
    target = target_for_profile(profile)
    assert target["alt_km"] > 20000.0
    assert "period_s" in target
